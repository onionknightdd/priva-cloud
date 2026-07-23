"""AppWorker — one armed account: wires its transport's inbound callback to the
router → dialer → outbound-send pipeline, for the lifetime of the WS connection.

Inbound pipeline (transport already ack'd the frame <3s, so this runs detached):
  access gate → decide (slash command?) → detach-ack OR wake+dial → capture session → reply
"""

from __future__ import annotations

import asyncio
import base64

from priva_common.logging import get_app_logger

from .cards import render_card
from .router import match_link_code
from .sse import StreamState

logger = get_app_logger(__name__)

_NEW_ACK = "🆕 已开始新对话 / New conversation started."
_LINK_OK = "✅ 绑定成功！你已成为此机器人的所有者。"
_LINK_FAIL = "❌ 绑定码无效或已过期，请在控制台重新生成。"

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

# Inbound image caps mirror the runner's own validator (routers/agent.py): >5 images or
# >3MB decoded would 400/413 the whole dial, so enforce here and note what was skipped.
_MAX_IMAGES = 5
_MAX_IMAGE_BYTES = 3 * 1024 * 1024
# Image-only DM: AgentRunRequest.message requires non-empty text (same fallback role as
# the web SPA's 'Describe the uploaded image(s).', user-ruled Chinese for the DM channel).
_IMAGE_FALLBACK_PROMPT = "请描述图片内容。"


class AppWorker:
    def __init__(self, client, dialer, router, cfg, secret, account, transport_factory, on_status=None):
        self.account_id: str = cfg.account_id
        self._client = client
        self._dialer = dialer
        self._router = router
        self._cfg = cfg
        self._inflight: set[asyncio.Task] = set()
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
        # Kill-switch semantics (see engine): cancel in-flight turns FIRST — their
        # finally blocks still get to settle reactions/cards while the transport is
        # alive — then drop the socket. Un-drained turns are logged, never silent.
        tasks = [t for t in self._inflight if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            _, unsettled = await asyncio.wait(tasks, timeout=5)
            if unsettled:
                logger.warning("feishu teardown left {} in-flight turn(s) unsettled account={}",
                               len(unsettled), self.account_id)
        try:
            await self._transport.stop()
        except Exception:
            logger.exception("transport stop failed account={}", self.account_id)

    # --- inbound ----------------------------------------------------------
    async def _on_message(self, msg) -> None:
        # The transport fires this per DM; never let one bad message kill the socket.
        task = asyncio.current_task()
        if task is not None:
            self._inflight.add(task)
        try:
            await self._handle(msg)
        except asyncio.CancelledError:
            logger.info("inbound turn cancelled (teardown): account={}", self.account_id)
        except Exception:
            logger.exception("inbound handling failed account={}", self.account_id)
        finally:
            if task is not None:
                self._inflight.discard(task)

    async def _handle(self, msg) -> None:
        if (getattr(msg, "chat_type", "") or "") == "group":
            # 群聊链路（feat_feishu_DM.md §5.3）：effective_group_enabled（用户 opt-in
            # AND NOT admin 全局闸，随 digest re-arm 进 cfg 快照）+ 仅 @ 消息触发。
            # 拉群即授权 — 单聊 access gate / link-code 均不适用（绑定只在私聊完成）。
            if not getattr(self._cfg, "effective_group_enabled", False):
                logger.info("feishu group skipped (group chat disabled): account={} chat={}",
                            self.account_id, msg.chat_id)
                return
            if not getattr(msg, "mentioned", False):
                logger.info("feishu group skipped (no @mention): account={} chat={}",
                            self.account_id, msg.chat_id)
                return
        else:
            # Link-code binding runs BEFORE the access gate: re-binding from a new Feishu
            # identity must not be blocked by the previous owner's gate — code possession
            # (minted behind the platform login) is the authorization. Never enters the agent.
            code = match_link_code(msg.text)
            if code is not None:
                await self._handle_link(msg, code)
                return
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
        issued: list = []   # prompts this turn registered; expired at run end (leak guard)
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

            # Feishu image / 图文 DM: pull the bytes (REST, message-scoped) and ride them
            # into the run as AgentRunRequest.images. Anything skipped is noted in the
            # prompt so neither the model nor the user silently loses content.
            images, image_note = await self._fetch_images(msg)
            prompt = decision.prompt or ""
            if images and not prompt.strip():
                prompt = _IMAGE_FALLBACK_PROMPT
            if image_note:
                prompt = f"{prompt}\n{image_note}".strip()

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
                    prompt=prompt,
                    session_id=resume,
                    model=(getattr(self._cfg, "model", None) or None),
                    images=images,
                    state=state,
                    on_permission=self._permission_handler(msg, state, message_id, issued),
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
            self._expire_prompts(issued)
            await self._settle_reaction(msg.message_id, typing_rid, ok)

    # --- owner link-code --------------------------------------------------
    async def _handle_link(self, msg, code: str) -> None:
        """Bind the DM sender as this bot's owner. data-spine does the hashed,
        constant-time, single-use validation and clears the code atomically; the
        digest change re-arms the worker so the fresh owner lands in cfg."""
        if not getattr(msg, "sender_union_id", ""):
            # Old/odd payload without union_id — can't key the binding; treat as invalid.
            await self._transport.send_text(msg.chat_id, _LINK_FAIL)
            return
        try:
            ok = await asyncio.to_thread(
                self._client.feishu_configs.bind_owner_with_code,
                self.account_id, code, msg.sender_union_id, msg.sender_open_id,
            )
        except Exception:
            logger.exception("feishu link bind crashed account={}", self.account_id)
            ok = False
        logger.info("feishu link bind: account={} ok={} union={}",
                    self.account_id, bool(ok), msg.sender_union_id[:14])
        await self._transport.send_text(msg.chat_id, _LINK_OK if ok else _LINK_FAIL)

    # --- inbound images ---------------------------------------------------
    async def _fetch_images(self, msg) -> tuple[list[dict] | None, str]:
        """Download the DM's Feishu images (first _MAX_IMAGES, each ≤ _MAX_IMAGE_BYTES)
        into AgentRunRequest.images items. Returns (images|None, note): over-cap,
        oversize, and failed fetches are summarised in the note (folded into the
        prompt) instead of failing the turn."""
        requested = list(getattr(msg, "image_keys", ()) or ())
        keys = requested
        if not keys:
            return None, ""
        skipped: list[str] = []
        if len(keys) > _MAX_IMAGES:
            skipped.append(f"{len(keys) - _MAX_IMAGES} 张超出单条 {_MAX_IMAGES} 张上限")
            keys = keys[:_MAX_IMAGES]
        images: list[dict] = []
        oversize = failed = 0
        for i, key in enumerate(keys):
            fetched = await self._transport.fetch_image(msg.message_id, key)
            if fetched is None:
                failed += 1
                continue
            data, media_type = fetched
            if len(data) > _MAX_IMAGE_BYTES:
                oversize += 1
                continue
            images.append({
                "data": base64.b64encode(data).decode("ascii"),
                "media_type": media_type,
                "filename": f"feishu-image-{i + 1}.{media_type.removeprefix('image/')}",
            })
        if oversize:
            skipped.append(f"{oversize} 张超过 {_MAX_IMAGE_BYTES // (1024 * 1024)}MB 大小上限")
        if failed:
            skipped.append(f"{failed} 张下载失败")
        note = f"[提示: 用户发送的图片中 {'；'.join(skipped)}，未能附上]" if skipped else ""
        logger.info("feishu images: account={} requested={} sent={} skipped={}",
                    self.account_id, len(requested), len(images), "; ".join(skipped) or "-")
        return (images or None), note

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
            if state.pending_prompt is not None:
                # An interactive prompt is embedded in the card and the user may be mid-input;
                # a patch would wipe it. Pause here — the run is blocked server-side anyway, so
                # there's no fresh content to stream — until dial clears pending on resume.
                continue
            await self._patch_card(message_id, render_card(state, final=False, dots=dots))
            dots = dots % 3 + 1

    # --- interactive permission / AskUserQuestion cards -------------------
    def _permission_handler(self, msg, state, message_id, issued):
        """The async ``on_permission`` callback dial invokes per permission event. Embeds the
        prompt INTO the streaming card (``message_id``) so it stays one card; closed over this
        DM's context (chat + sender) and the run's ``state``. The card-action handler resolves
        the tap out-of-band (``card_actions`` → ``resolve``)."""
        async def _on_permission(event: str, data: dict) -> None:
            if event == "permission_request":
                await self._embed_permission(msg, state, message_id, data or {}, issued)
            elif event == "permission_timeout":
                self._clear_permission(state, data or {})
        return _on_permission

    def _expire_prompts(self, issued: list) -> None:
        """Run over — a prompt this turn left pending can never be answered (its run is
        gone), so drop it from the registry now instead of leaking it until the TTL
        sweep. A late tap gets the standard stale-card toast."""
        from .pending import discard
        for prompt in issued:
            if prompt.status == "pending":
                prompt.status = "expired"
            discard(prompt)
            state = prompt.state
            if state is not None and state.pending_prompt is prompt:
                state.pending_prompt = None

    async def _embed_permission(self, msg, state, message_id, data: dict, issued: list) -> None:
        from . import permission_cards
        from .pending import PendingPrompt, register
        tool_input = data.get("input") if isinstance(data.get("input"), dict) else {}
        questions = tool_input.get("questions")
        prompt = PendingPrompt(
            request_id=data.get("request_id") or "",
            session_id=data.get("session_id") or "",
            account_id=self.account_id,
            username=self._username,
            chat_id=msg.chat_id,
            kind=data.get("kind") or "ask_user",
            questions=questions if isinstance(questions, list) else [],
            tool_name=data.get("tool_name") or "",
            tool_input=tool_input or None,
            reason=data.get("reason") or "",
            sender_open_id=msg.sender_open_id or "",
            message_id=message_id or "",
            state=state,
        )
        if not message_id:
            # No streaming card to embed into (its post failed) → standalone-card fallback.
            mid = await self._send_card(msg.chat_id, permission_cards.permission_card(prompt))
            if not mid:
                logger.warning("feishu permission card send failed account={} rid={}",
                               self.account_id, prompt.request_id)
                return
            prompt.message_id, prompt.state = mid, None
            register(prompt)
            issued.append(prompt)
            return
        # Embed: flip pending on the shared state (the ticker pauses so the interactive card
        # isn't wiped), register by the streaming card's id, patch it once to show the prompt.
        state.pending_prompt = prompt
        register(prompt)
        issued.append(prompt)
        await self._patch_card(message_id, render_card(state, final=False))
        logger.info("feishu permission embedded account={} rid={} kind={} q={} mid={}",
                    self.account_id, prompt.request_id, prompt.kind, len(prompt.questions), message_id)

    def _clear_permission(self, state, data: dict) -> None:
        """Timeout: drop the embed so the ticker resumes (the pod already denied the tool; its
        errored tool_result renders in the process on the next tick)."""
        from .pending import discard, get_by_request
        prompt = get_by_request(data.get("request_id") or "")
        if prompt is None or prompt.status != "pending":
            return
        prompt.status = "timeout"
        discard(prompt)
        if state.pending_prompt is prompt:
            state.pending_prompt = None
        logger.info("feishu permission timeout account={} rid={}", self.account_id, prompt.request_id)

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
