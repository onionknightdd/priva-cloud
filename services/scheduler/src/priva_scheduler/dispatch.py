"""Dispatch — deliver a claimed fire to the account's pod (D1/D13/D16).

``Dispatcher`` is the seam the firing engine talks to; ``WakeDialDispatcher``
is the v1 implementation (wake the pod via the CR, POST the admission frame to
the stable per-account Service). A Redis-inbox dispatcher can replace it later
without touching the engine (parked upgrade, design §15).

Connection semantics (US-3): the POST waits only for *admission* (~ms). The
scheduler is stateless about the run after a 202 — the pod owns the outcome.

Retry rules:
- connection-level failures (wake timeout / refused / request timeout / 5xx):
  up to ``wake_retry_attempts``, backoff base→max + jitter, then
  ``DispatchError('wake_failed')``. Safe because admission is idempotent by
  run_id (a 202 we never saw → the retry gets 202 duplicate).
- 409: immediate ``job_overlap`` (the runner's D9 backstop; never retried).
- 429: re-POST within ``admission_retry_window_seconds`` (D16), then
  ``concurrency_cap``.
"""

from __future__ import annotations

import asyncio
import random
from typing import Literal, Protocol

import httpx

from priva_common.config import get_settings
from priva_common.logging import get_app_logger
from priva_common.models.scheduler import ScheduledRunRequest
from priva_common.runner_token import mint

from . import wake

logger = get_app_logger(__name__)

DispatchResult = Literal["accepted", "job_overlap", "concurrency_cap"]

# 429 re-admission cadence inside the D16 window.
_ADMISSION_RETRY_DELAYS = (5.0, 10.0, 20.0, 30.0)


class DispatchError(Exception):
    """Dispatch could not deliver the fire; ``reason`` is the D11 token."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Dispatcher(Protocol):
    async def dispatch(
        self, account_id: str, username: str, frame: ScheduledRunRequest
    ) -> DispatchResult: ...


class WakeDialDispatcher:
    """v1: CR-patch wake → poll Ready → POST to ``ar-{account}`` (design §4)."""

    def __init__(self, *, waker=None, transport: httpx.AsyncBaseTransport | None = None):
        # Both seams exist for tests: a fake waker and an httpx MockTransport.
        self._waker = waker or wake.wake_and_wait
        self._transport = transport

    def _url(self, account_id: str) -> str:
        s = get_settings()
        ns = s.kubernetes.namespace_tenants
        port = s.kubernetes.runner_service_port
        return f"http://ar-{account_id}.{ns}.svc:{port}/api/sandbox/agent/scheduled-run"

    async def _post(
        self, cx: httpx.AsyncClient, account_id: str, username: str,
        frame: ScheduledRunRequest,
    ) -> httpx.Response:
        return await cx.post(
            self._url(account_id),
            json=frame.model_dump(mode="json"),
            # Short-TTL token minted per attempt (the same trust seam the
            # control-panel edge uses; the runner pins it to its account).
            headers={"X-Priva-Runner-Token": mint(account_id, username)},
        )

    async def dispatch(
        self, account_id: str, username: str, frame: ScheduledRunRequest
    ) -> DispatchResult:
        s = get_settings().scheduler
        async with httpx.AsyncClient(
            trust_env=False, transport=self._transport,
            timeout=httpx.Timeout(10.0, connect=5.0),
        ) as cx:
            for attempt in range(1, s.wake_retry_attempts + 1):
                if attempt > 1:
                    delay = min(
                        s.wake_retry_base_seconds * (2 ** (attempt - 2)),
                        s.wake_retry_max_seconds,
                    ) + random.uniform(0, 1)
                    logger.info(
                        "dispatch retry {}/{} run={} in {:.1f}s",
                        attempt, s.wake_retry_attempts, frame.run_id, delay,
                    )
                    await asyncio.sleep(delay)

                if not await self._waker(account_id):
                    continue  # wake didn't come up in time — next attempt re-wakes

                try:
                    resp = await self._post(cx, account_id, username, frame)
                except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as exc:
                    logger.warning(
                        "dispatch dial failed run={} attempt {}: {}",
                        frame.run_id, attempt, exc,
                    )
                    continue

                verdict = await self._handle_response(
                    resp, cx, account_id, username, frame,
                    admission_window=float(s.admission_retry_window_seconds),
                )
                if verdict is not None:
                    return verdict
                # 5xx / unexpected status — connection-level retry.

        raise DispatchError("wake_failed")

    async def _handle_response(
        self,
        resp: httpx.Response,
        cx: httpx.AsyncClient,
        account_id: str,
        username: str,
        frame: ScheduledRunRequest,
        *,
        admission_window: float,
    ) -> DispatchResult | None:
        if resp.status_code == 202:
            return "accepted"
        if resp.status_code == 409:
            return "job_overlap"
        if resp.status_code == 429:
            # D16: the pod is at its concurrency cap — re-try ADMISSION (not
            # the wake) for up to the window, then record the skip.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + admission_window
            for delay in _ADMISSION_RETRY_DELAYS + (30.0,) * 16:
                if loop.time() + delay > deadline:
                    return "concurrency_cap"
                await asyncio.sleep(delay)
                try:
                    retry_resp = await self._post(cx, account_id, username, frame)
                except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError):
                    return None  # pod went away mid-window — re-wake path
                if retry_resp.status_code == 202:
                    return "accepted"
                if retry_resp.status_code == 409:
                    return "job_overlap"
                if retry_resp.status_code != 429:
                    return None
            return "concurrency_cap"
        logger.warning(
            "dispatch unexpected status {} run={}: {}",
            resp.status_code, frame.run_id, resp.text[:200],
        )
        return None
