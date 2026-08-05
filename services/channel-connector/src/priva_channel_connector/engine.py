"""ReconcileEngine — converge the live WS set to the dataplane's *effective* set.

data-spine has no watch/stream (all-unary), so the baseline is poll-list-diff:
every ``poll_seconds`` re-list ``feishu_configs.list_effective()`` and diff each row's
``desired_digest`` against what's armed. A row that vanishes (admin_disabled=1 /
user_enabled=0 / creds cleared) or whose account is no longer ``active`` (admin
disable / purge) is torn down (kill-switch = hard-stop). A digest
change (creds/domain/gate change) is torn down then re-armed — ordered, because the
same app_id must never hold two WS at once (single-cast). A best-effort push
(``reconcile_now``) collapses the ≤poll latency for the common single-account edit.

Diff key is ``desired_digest`` (hash of desired cols only), NOT ``updated_at`` — the
connector writes status cols back to the same row, and diffing on updated_at would
thrash into an infinite reconnect loop. This is the one hard correction vs. the
scheduler's list-diff.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone

from priva_common.logging import get_app_logger

from . import config
from .router import SessionRouter
from .scheduler_callback import SchedulerCallbackPayload
from .worker import AppWorker

logger = get_app_logger(__name__)

_CALLBACK_RATE_WINDOW_SECONDS = 60.0
_CALLBACK_RATE_LIMIT = 120
_CALLBACK_DELIVERY_CACHE_MAX = 10_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SchedulerCallbackUnavailable(RuntimeError):
    """The account has no effective Feishu bot configuration."""


class SchedulerCallbackOwnerUnbound(RuntimeError):
    """The bot is configured, but no owner open_id is bound to it."""


class SchedulerCallbackWorkerUnavailable(RuntimeError):
    """The effective account currently has no armed connector worker."""


class SchedulerCallbackDeliveryFailed(RuntimeError):
    """Feishu rejected or failed the proactive card send."""


class SchedulerCallbackRejected(RuntimeError):
    """The claimed callback does not match an eligible terminal run."""


class SchedulerCallbackRateLimited(RuntimeError):
    """The account exceeded the bounded proactive-callback request rate."""


class ReconcileEngine:
    def __init__(self, client, transport_factory, dialer, *, poll_seconds: float | None = None):
        self._client = client
        self._transport_factory = transport_factory
        self._dialer = dialer
        self._router = SessionRouter(client)
        self._poll = poll_seconds if poll_seconds is not None else config.poll_seconds()
        self._workers: dict[str, AppWorker] = {}
        self._digests: dict[str, str | None] = {}
        self._lock = asyncio.Lock()   # serialize reconcile_once vs reconcile_now
        # Reconcile and callback delivery for one account share this lock.  It
        # fences owner/config changes across the REST send without blocking
        # callback delivery for unrelated accounts.
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._callback_attempts: dict[str, deque[float]] = {}
        self._callback_deliveries: dict[tuple[str, str], str] = {}
        self._task: asyncio.Task | None = None

    @property
    def armed_count(self) -> int:
        return len(self._workers)

    # --- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="connector-reconcile")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Graceful SIGTERM: close every WS (single-replica maxSurge=0 relies on this).
        async with self._lock:
            for aid in list(self._workers):
                async with self._account_lock(aid):
                    await self._teardown(aid)

    async def _loop(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("reconcile loop error")
            await asyncio.sleep(self._poll)

    # --- reconciliation ---------------------------------------------------
    def _active_account_ids(self, account_ids) -> set[str]:
        """The subset still allowed to hold a socket. ``list_effective`` answers the
        credentials/kill-switch question only — it carries no account status and is
        shared with the admin nudge path — so the lifecycle gate lives here: a
        disabled/purged account drops out of the desired set and takes the normal
        teardown path. Blocking dataplane calls."""
        active = set()
        for aid in account_ids:
            account = self._client.accounts.get(aid)
            if account is not None and account.status == "active":
                active.add(aid)
        return active

    async def reconcile_once(self) -> None:
        desired = await asyncio.to_thread(self._client.feishu_configs.list_effective)
        by_id = {c.account_id: c for c in desired}
        active = await asyncio.to_thread(self._active_account_ids, list(by_id))
        by_id = {aid: c for aid, c in by_id.items() if aid in active}
        async with self._lock:
            # Vanished from the effective set → disabled/creds-cleared → hard-stop.
            for aid in list(self._workers):
                if aid not in by_id:
                    async with self._account_lock(aid):
                        await self._teardown(aid)
            # New or digest-changed → arm / re-arm.
            for aid, cfg in by_id.items():
                async with self._account_lock(aid):
                    if aid not in self._workers:
                        await self._arm(cfg)
                    elif cfg.desired_digest != self._digests.get(aid):
                        await self._teardown(aid, mark_disabled=False)  # re-arm: don't flap the UI to disabled
                        await self._arm(cfg)

    async def reconcile_now(self, account_id: str) -> None:
        """Targeted push (control-panel → connector). Fetch just this account and
        converge it. Idempotent with the poll loop."""
        cfg = await asyncio.to_thread(self._client.feishu_configs.get, account_id)
        active = await asyncio.to_thread(self._active_account_ids, [account_id])
        async with self._lock:
            async with self._account_lock(account_id):
                effective = (cfg is not None and getattr(cfg, "effective_enabled", False)
                             and account_id in active)
                if not effective:
                    if account_id in self._workers:
                        await self._teardown(account_id)
                    return
                if account_id not in self._workers:
                    await self._arm(cfg)
                elif cfg.desired_digest != self._digests.get(account_id):
                    await self._teardown(account_id, mark_disabled=False)
                    await self._arm(cfg)

    async def push_scheduler_callback(
        self,
        account_id: str,
        payload: SchedulerCallbackPayload,
        card: dict,
    ) -> str:
        """Send one scheduled outcome to this account's bound Feishu owner.

        Addressing is deliberately resolved here from the data-plane record.  The
        caller supplies only ``account_id`` + content and therefore cannot target a
        different Feishu identity.  Before any send, the claimed IDs/status are
        matched against the terminal scheduler ledger and the current job must still
        opt in to Feishu.  One account lock fences owner/config reconciliation across
        the send and also makes the per-run delivery cache atomic.
        """
        async with self._account_lock(account_id):
            cache_key = (account_id, payload.run_id)
            delivered = self._callback_deliveries.get(cache_key)
            if delivered:
                return delivered
            self._consume_callback_rate(account_id)

            await self._verify_scheduler_callback(account_id, payload)

            # Read the owner while holding the same account lock used by
            # reconcile/teardown. A desired-digest fence rejects a config row
            # that has changed but whose worker has not been re-armed yet.
            try:
                cfg = await asyncio.to_thread(self._client.feishu_configs.get, account_id)
            except Exception as exc:
                logger.exception("scheduler callback config read failed account={}", account_id)
                raise SchedulerCallbackWorkerUnavailable("feishu config unavailable") from exc
            if cfg is None or not getattr(cfg, "effective_enabled", False):
                raise SchedulerCallbackUnavailable("feishu bot is not enabled")
            owner_open_id = (getattr(cfg, "owner_open_id", "") or "").strip()
            if not owner_open_id:
                raise SchedulerCallbackOwnerUnbound("feishu owner is not bound")

            worker = self._workers.get(account_id)
            if worker is None:
                raise SchedulerCallbackWorkerUnavailable("feishu connector is not ready")
            desired_digest = getattr(cfg, "desired_digest", None)
            if not desired_digest or desired_digest != self._digests.get(account_id):
                raise SchedulerCallbackWorkerUnavailable("feishu configuration is changing")
            try:
                message_id = await worker.send_card_to_user(owner_open_id, card)
            except Exception as exc:
                logger.exception("scheduler callback send crashed account={}", account_id)
                raise SchedulerCallbackDeliveryFailed("feishu card send failed") from exc
            if not message_id:
                raise SchedulerCallbackDeliveryFailed("feishu card send failed")

            self._callback_deliveries[cache_key] = message_id
            while len(self._callback_deliveries) > _CALLBACK_DELIVERY_CACHE_MAX:
                self._callback_deliveries.pop(next(iter(self._callback_deliveries)))

        logger.info(
            "scheduler callback delivered account={} message_id={}",
            account_id, message_id,
        )
        return message_id

    def _account_lock(self, account_id: str) -> asyncio.Lock:
        lock = self._account_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            self._account_locks[account_id] = lock
        return lock

    def _consume_callback_rate(self, account_id: str) -> None:
        now = time.monotonic()
        attempts = self._callback_attempts.setdefault(account_id, deque())
        cutoff = now - _CALLBACK_RATE_WINDOW_SECONDS
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= _CALLBACK_RATE_LIMIT:
            raise SchedulerCallbackRateLimited("scheduler callback rate limit exceeded")
        attempts.append(now)

    async def _verify_scheduler_callback(
        self, account_id: str, payload: SchedulerCallbackPayload,
    ) -> None:
        try:
            run, job = await asyncio.gather(
                asyncio.to_thread(
                    self._client.scheduler.get_run, account_id, payload.run_id,
                ),
                asyncio.to_thread(self._client.scheduler.get_job, payload.job_id),
            )
        except Exception as exc:
            logger.exception("scheduler callback verification failed account={}", account_id)
            raise SchedulerCallbackWorkerUnavailable(
                "scheduler callback verification unavailable"
            ) from exc

        if run is None:
            raise SchedulerCallbackRejected("scheduled run not found")
        if run.run_id != payload.run_id:
            raise SchedulerCallbackRejected("scheduled run id mismatch")
        if run.job_id != payload.job_id:
            raise SchedulerCallbackRejected("scheduled run job mismatch")
        if run.job_name != payload.job_name:
            raise SchedulerCallbackRejected("scheduled run name mismatch")
        if run.status not in {"success", "error", "cancelled"}:
            raise SchedulerCallbackRejected("scheduled run is not terminal")
        if run.status != payload.status:
            raise SchedulerCallbackRejected("scheduled run status mismatch")

        job_config = getattr(job, "job_config", None) if job is not None else None
        callback = getattr(job_config, "callback", None)
        callback_type = (
            callback.get("type") if isinstance(callback, dict)
            else getattr(callback, "type", None)
        )
        if job is None or getattr(job, "id", None) != payload.job_id:
            raise SchedulerCallbackRejected("scheduled job not found")
        if getattr(job_config, "job_type", None) != payload.job_type:
            raise SchedulerCallbackRejected("scheduled job type mismatch")
        if callback_type != "feishu":
            raise SchedulerCallbackRejected("feishu callback is not enabled")

    # --- arm / teardown (call under self._lock) ---------------------------
    async def _arm(self, cfg) -> None:
        aid = cfg.account_id
        # Fetch the decrypted secret only at arm time (surgical exposure). "" means
        # unset OR undecryptable (key rotation/corruption) → park with an error the
        # UI can surface, never a silent dead socket (spec §12-4).
        secret = await asyncio.to_thread(self._client.feishu_configs.get_secret, aid)
        if secret is None or not secret.app_secret:
            logger.warning("feishu not armed (secret unset/undecryptable): account={}", aid)
            await self._set_status(aid, "error", None, "secret_undecryptable")
            return
        account = await asyncio.to_thread(self._client.accounts.get, aid)
        worker = AppWorker(
            self._client, self._dialer, self._router, cfg, secret, account,
            self._transport_factory, self._status_cb(aid),
        )
        await self._set_status(aid, "connecting", None, None)
        try:
            await worker.start()
        except Exception as exc:
            logger.exception("feishu arm failed account={}", aid)
            await self._set_status(aid, "error", None, str(exc)[:200])
            return
        self._workers[aid] = worker
        self._digests[aid] = cfg.desired_digest
        logger.info("feishu armed: account={} app_id={} domain={}",
                    aid, secret.app_id, secret.domain)

    async def _teardown(self, aid: str, *, mark_disabled: bool = True) -> None:
        worker = self._workers.pop(aid, None)
        self._digests.pop(aid, None)
        if worker is not None:
            await worker.stop()
            logger.info("feishu torn down: account={} (mark_disabled={})", aid, mark_disabled)
        if mark_disabled:
            # Positive-ACK fencing (§12-6): write disabled + fresh status_updated_at so
            # the UI knows the socket is really down, not just "connector offline".
            await self._set_status(aid, "disabled", None, None)

    # --- status write-back ------------------------------------------------
    def _status_cb(self, account_id: str):
        async def cb(status: str, code: int | None, message: str | None) -> None:
            await self._set_status(account_id, status, code, message)
        return cb

    async def _set_status(self, account_id: str, status: str, code: int | None, message: str | None) -> None:
        try:
            await asyncio.to_thread(
                self._client.feishu_configs.set_status,
                account_id,
                conn_status=status,
                last_error_code=code,
                last_error_message=message,
                last_connected_at=(_now_iso() if status == "connected" else None),
            )
        except Exception:
            logger.exception("set_status failed account={} status={}", account_id, status)
