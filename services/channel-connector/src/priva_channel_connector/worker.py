"""AppWorker — one armed account: wires its transport's inbound callback to the
router → dialer → outbound-send pipeline, for the lifetime of the WS connection.

Inbound pipeline (transport already ack'd the frame <3s, so this runs detached):
  access gate → decide (slash command?) → detach-ack OR wake+dial → capture session → reply
"""

from __future__ import annotations

import asyncio

from priva_common.logging import get_app_logger

from .cards import render_card
from .sse import StreamState

logger = get_app_logger(__name__)

_NEW_ACK = "🆕 已开始新对话 / New conversation started."

# Streaming-card tick: the worker patches the whole card on this cadence while a run is in
# flight — this both streams fresh content (the state is folded live by the dialer) and
# animates the "Thinking" dots (1→2→3). ~1.6 patches/s stays well under Feishu's 5 QPS
# per-message cap.
_TICK_INTERVAL = 0.6

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

            # #0 streaming card: post an initial "running" card up front to get a
            # message_id we can patch as the stream folds in. Best-effort — if the card
            # can't be posted (message_id is None) we fall back to the plain-text reply.
            state = StreamState()
            message_id = await self._send_card(msg.chat_id, render_card(state, final=False, dots=1))

            # A ticker patches the card every _TICK_INTERVAL while the run is in flight: it
            # both streams the content (dial folds into the SAME `state`) and animates the
            # Thinking dots. Cancelled the moment the run returns.
            ticker = asyncio.create_task(self._animate(message_id, state)) if message_id else None
            try:
                final_state = await self._dialer.run(
                    self.account_id,
                    self._username,
                    prompt=decision.prompt,
                    session_id=resume,
                    model=(getattr(self._cfg, "model", None) or None),
                    state=state,
                )
            except Exception:
                # dial maps transport errors into `state`, so this only fires on an
                # unexpected crash — fold the error in so the final card renders it.
                logger.exception("feishu run crashed account={}", self.account_id)
                state.is_error = True
                state.error_text = state.error_text or "run_failed"
                final_state = state
            finally:
                if ticker:
                    ticker.cancel()
                    try:
                        await ticker
                    except asyncio.CancelledError:
                        pass

            outcome = final_state.outcome()
            if outcome.session_id:
                await asyncio.to_thread(
                    self._router.commit_session, self.account_id, outcome.session_id, msg.chat_id
                )

            if message_id:
                # Final patch lands the terminal card, rendered from the SAME state dial
                # folded — so card == outcome on every path.
                await self._patch_card(message_id, render_card(final_state, final=True))
            else:
                reply = (outcome.text or "").strip()
                if not reply:
                    reply = f"⚠️ {outcome.error_text}" if outcome.error_text else "(no output)"
                await self._transport.send_text(msg.chat_id, reply)
            ok = not outcome.is_error
            logger.info("feishu run done: account={} session={} card={} err={}",
                        self.account_id, (outcome.session_id or "-")[:12],
                        "yes" if message_id else "no", outcome.error_text or "-")
        finally:
            await self._settle_reaction(msg.message_id, typing_rid, ok)

    # --- streaming card ---------------------------------------------------
    async def _send_card(self, chat_id: str, card: dict) -> str | None:
        try:
            return await self._transport.send_card(chat_id, card)
        except Exception:
            logger.exception("feishu card send failed account={}", self.account_id)
            return None

    async def _patch_card(self, message_id: str, card: dict) -> None:
        try:
            await self._transport.patch_card(message_id, card)
        except Exception:
            logger.exception("feishu card patch failed account={}", self.account_id)

    async def _animate(self, message_id: str, state: StreamState) -> None:
        """Patch the running card on a fixed cadence: streams the live-folded `state` and
        cycles the Thinking dots 1→2→3. Runs until cancelled at run end. Patch failures are
        swallowed by _patch_card, so a hiccup never kills the ticker."""
        dots = 1
        while True:
            await asyncio.sleep(_TICK_INTERVAL)
            await self._patch_card(message_id, render_card(state, final=False, dots=dots))
            dots = dots % 3 + 1

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
