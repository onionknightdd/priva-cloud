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

# Feishu message-reaction lifecycle stamped on the *inbound* DM (emoji_type keys are a
# fixed, case-sensitive Feishu enum — see im-v1/message-reaction docs). Typing rides the
# whole turn, then swaps to CheckMark on success / CrossMark on any error or abnormal end.
# Purely cosmetic: a reaction API hiccup must never break the actual reply.
_EMOJI_TYPING = "Typing"
_EMOJI_DONE = "CheckMark"
_EMOJI_ERROR = "CrossMark"


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

        # Accepted → stamp the inbound DM "Typing" for the whole turn. Resolved in the
        # finally so it always settles (CheckMark on success, CrossMark on any error or
        # exception — the abnormal-interruption case).
        typing_rid = await self._react(msg.message_id, _EMOJI_TYPING)
        ok = False
        try:
            # Router touches the (blocking, sync) dataplane client — run off the loop.
            decision = await asyncio.to_thread(self._router.decide, msg)

            if decision.kind == "detach":
                await asyncio.to_thread(self._router.detach, self.account_id, msg.chat_id)
                await self._transport.send_text(msg.chat_id, _NEW_ACK)
                logger.info("feishu /new detach: account={} chat={}", self.account_id, msg.chat_id)
                ok = True
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
            ok = not outcome.is_error
            logger.info("feishu run done: account={} session={} reply={}chars err={}",
                        self.account_id, (outcome.session_id or "-")[:12], len(reply),
                        outcome.error_text or "-")
        finally:
            await self._settle_reaction(msg.message_id, typing_rid, ok)

    # --- reaction lifecycle -----------------------------------------------
    async def _react(self, message_id: str, emoji_type: str) -> str | None:
        """Add one reaction, returning its id (None on failure / no message id).
        Best-effort: the reaction is cosmetic and must never break the reply."""
        if not message_id:
            return None
        try:
            return await self._transport.add_reaction(message_id, emoji_type)
        except Exception:
            logger.exception("feishu reaction add failed account={} emoji={}",
                             self.account_id, emoji_type)
            return None

    async def _settle_reaction(self, message_id: str, typing_rid: str | None, ok: bool) -> None:
        """Swap the transient Typing reaction for the terminal one (CheckMark / CrossMark)."""
        if not message_id:
            return
        if typing_rid:
            try:
                await self._transport.remove_reaction(message_id, typing_rid)
            except Exception:
                logger.exception("feishu reaction remove failed account={}", self.account_id)
        await self._react(message_id, _EMOJI_DONE if ok else _EMOJI_ERROR)
