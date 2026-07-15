"""AppWorker — one armed account: wires its transport's inbound callback to the
router → dialer → outbound-send pipeline, for the lifetime of the WS connection.

Inbound pipeline (transport already ack'd the frame <3s, so this runs detached):
  access gate → decide (slash command?) → detach-ack OR wake+dial → capture session → reply
"""

from __future__ import annotations

import asyncio

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

_NEW_ACK = "🆕 已开始新对话 / New conversation started."


class AppWorker:
    def __init__(self, client, dialer, router, cfg, secret, account, transport_factory, on_status=None):
        self.account_id: str = cfg.account_id
        self._client = client
        self._dialer = dialer
        self._router = router
        self._cfg = cfg
        self._username = getattr(account, "username", None) if account is not None else None
        self._transport = transport_factory(
            cfg.account_id,
            secret.app_id or "",
            secret.app_secret,
            secret.domain or "feishu",
            self._on_message,
            on_status,
        )

    async def start(self) -> None:
        await self._transport.start()

    async def stop(self) -> None:
        try:
            await self._transport.stop()
        except Exception:
            logger.exception("transport stop failed account={}", self.account_id)

    # --- inbound ----------------------------------------------------------
    async def _on_message(self, msg) -> None:
        # The transport fires this per DM; never let one bad message kill the socket.
        try:
            await self._handle(msg)
        except Exception:
            logger.exception("inbound handling failed account={}", self.account_id)

    async def _handle(self, msg) -> None:
        if not self._router.access_allowed(self._cfg, msg):
            logger.info("feishu rejected (access gate): account={} from={}",
                        self.account_id, msg.sender_open_id[:14])
            reject = getattr(self._cfg, "reject_message", "") or ""
            if reject:
                await self._transport.send_text(msg.chat_id, reject)
            return

        # Router touches the (blocking, sync) dataplane client — run off the loop.
        decision = await asyncio.to_thread(self._router.decide, msg)

        if decision.kind == "detach":
            await asyncio.to_thread(self._router.detach, self.account_id, msg.chat_id)
            await self._transport.send_text(msg.chat_id, _NEW_ACK)
            logger.info("feishu /new detach: account={} chat={}", self.account_id, msg.chat_id)
            return

        resume = decision.resume_session_id
        logger.info("feishu run start: account={} session={} ({})",
                    self.account_id, (resume or "fresh")[:12], "resume" if resume else "new")
        outcome = await self._dialer.run(
            self.account_id,
            self._username,
            prompt=decision.prompt,
            session_id=resume,
            model=(getattr(self._cfg, "model", None) or None),
        )
        if outcome.session_id:
            await asyncio.to_thread(
                self._router.commit_session, self.account_id, outcome.session_id, msg.chat_id
            )

        reply = (outcome.text or "").strip()
        if not reply:
            reply = f"⚠️ {outcome.error_text}" if outcome.error_text else "(no output)"
        await self._transport.send_text(msg.chat_id, reply)
        logger.info("feishu run done: account={} session={} reply={}chars err={}",
                    self.account_id, (outcome.session_id or "-")[:12], len(reply),
                    outcome.error_text or "-")
