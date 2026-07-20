"""LarkTransport — one Feishu/Lark app's WS long-connection on its own thread.

``lark_oapi``'s ``ws.Client.start()`` blocks its thread running an internal event loop,
so each app gets a dedicated thread (spec fact ②: 1 conn/app; same-app 2nd conn splits
events — hence exactly one). Inbound events fire on that thread and are bridged onto the
connector's asyncio loop via ``run_coroutine_threadsafe`` (the handler returns
immediately, honoring Feishu's <3s ack). Outbound sends use the REST client on a worker
thread. ``lark_oapi`` is imported lazily so the package (and its unit tests) import
without the dependency.

NOTE: written against the lark_oapi v1 Python SDK surface documented in the design
(register_p2_im_message_receive_v1 over a long-connection). The stop() API is probed by
name across versions. card.action.trigger is delivered over the SAME long-connection via
``register_p2_card_action_trigger`` — its handler returns a ``P2CardActionTriggerResponse``
(toast + optional card replace) that the SDK sends back synchronously; the interactive-card
(AskUserQuestion / permission) reply pipeline builds on ``_dispatch_card_action``.
"""

from __future__ import annotations

import asyncio
import json
import threading

from priva_common.logging import get_app_logger

from .transport import InboundHandler, InboundMessage, StatusHandler

logger = get_app_logger(__name__)


def _classify_ws_error(exc: Exception) -> tuple[str, int | None, str]:
    """Map a WS/auth exception to (conn_status, feishu_code, message). Auth failures
    are parked (digest-gated re-arm only) so we don't hammer the token endpoint."""
    s = str(exc).lower()
    detail = str(exc)[:200]
    if any(k in s for k in ("auth", "credential", "app_secret", "app_id", "invalid app", "514")):
        return "auth_failed", 514, detail
    return "error", None, detail


class LarkTransport:
    def __init__(
        self,
        account_id: str,
        app_id: str,
        app_secret: str,
        domain: str,
        on_message: InboundHandler,
        on_status: StatusHandler | None = None,
    ):
        self.account_id = account_id
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain
        self._on_message = on_message
        self._on_status = on_status
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ws = None
        self._rest = None
        self._stopping = False

    # --- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._rest = await asyncio.to_thread(self._build_rest)
        self._thread = threading.Thread(
            target=self._run_ws, name=f"lark-ws-{self.account_id}", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stopping = True
        ws = self._ws
        if ws is not None:
            # lark_oapi's ws.Client stop API varies by version — try the known names.
            for name in ("stop", "_disconnect", "disconnect", "close"):
                fn = getattr(ws, name, None)
                if callable(fn):
                    try:
                        await asyncio.to_thread(fn)
                    except Exception:
                        logger.exception("lark ws stop via {} failed account={}", name, self.account_id)
                    break
        # The thread is a daemon; if no stop() exists it dies with the process
        # (single-replica maxSurge=0 makes that the backstop for re-arm safety).

    async def send_text(self, chat_id: str, text: str) -> None:
        await asyncio.to_thread(self._send_sync, chat_id, text)

    async def add_reaction(self, message_id: str, emoji_type: str) -> str | None:
        if not message_id:
            return None
        return await asyncio.to_thread(self._add_reaction_sync, message_id, emoji_type)

    async def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        if not message_id or not reaction_id:
            return
        await asyncio.to_thread(self._remove_reaction_sync, message_id, reaction_id)

    async def send_card(self, chat_id: str, card: dict) -> str | None:
        if not chat_id:
            return None
        return await asyncio.to_thread(self._send_card_sync, chat_id, card)

    async def patch_card(self, message_id: str, card: dict) -> None:
        if not message_id:
            return
        await asyncio.to_thread(self._patch_card_sync, message_id, card)

    # --- lark thread ------------------------------------------------------
    def _domain_const(self, lark):
        return lark.LARK_DOMAIN if self._domain == "lark" else lark.FEISHU_DOMAIN

    def _build_rest(self):
        import lark_oapi as lark
        return (
            lark.Client.builder()
            .app_id(self._app_id)
            .app_secret(self._app_secret)
            .domain(self._domain_const(lark))
            .build()
        )

    def _run_ws(self) -> None:
        import lark_oapi as lark
        from lark_oapi.ws import client as wsc

        account_id = self.account_id

        class _CardAwareClient(wsc.Client):
            """lark_oapi's ws client DROPS ``MessageType.CARD`` frames in every version
            (``_handle_data_frame``: ``elif message_type == MessageType.CARD: return``),
            so ``card.action.trigger`` never reaches the handler over the long-connection —
            even though the dispatcher already has a callback processor for it. This routes
            CARD frames through that same ``_do_without_validation`` callback path (and logs
            the raw payload) so interactive-card replies work without an HTTP callback URL."""

            async def _handle_data_frame(self, frame):
                hs = frame.headers
                type_ = wsc._get_by_key(hs, wsc.HEADER_TYPE)
                try:
                    mt = wsc.MessageType(type_)
                except ValueError:
                    mt = None
                if mt is not wsc.MessageType.CARD:
                    return await super()._handle_data_frame(frame)

                import base64
                pl = frame.payload
                msg_id = wsc._get_by_key(hs, wsc.HEADER_MESSAGE_ID)
                sum_ = wsc._get_by_key(hs, wsc.HEADER_SUM)
                seq = wsc._get_by_key(hs, wsc.HEADER_SEQ)
                try:
                    if int(sum_) > 1:  # multi-packet payload — reassemble like the base impl
                        pl = self._combine(msg_id, int(sum_), int(seq), pl)
                        if pl is None:
                            return
                except (TypeError, ValueError):
                    pass
                try:
                    logger.info("feishu CARD frame account={} payload={}",
                                account_id, pl.decode(wsc.UTF_8, "replace")[:2000])
                except Exception:
                    pass
                resp = wsc.Response(code=200)
                try:
                    result = self._event_handler._do_without_validation(pl)
                    if result is not None:
                        resp.data = base64.b64encode(wsc.JSON.marshal(result).encode(wsc.UTF_8))
                except Exception:
                    logger.exception("feishu CARD dispatch failed account={}", account_id)
                    resp = wsc.Response(code=500)
                frame.payload = wsc.JSON.marshal(resp).encode(wsc.UTF_8)
                await self._write_message(frame.SerializeToString())

        def _on_p2_message(data) -> None:
            self._dispatch(data)

        def _on_p2_card_action(data):
            # Card-action handler must RETURN a response (toast/card) the SDK sends back.
            return self._dispatch_card_action(data)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_on_p2_message)
            .register_p2_card_action_trigger(_on_p2_card_action)
            .build()
        )
        try:
            self._ws = _CardAwareClient(
                self._app_id, self._app_secret,
                event_handler=handler,
                domain=self._domain_const(lark),
                log_level=lark.LogLevel.WARNING,
            )
            # Optimistic: start() blocks for the whole session; a bad-cred start raises
            # almost immediately and flips us to auth_failed below. NOTE: "connected"
            # means the socket is up — the app still only receives DMs if it subscribed
            # im.message.receive_v1 (长连接) with im:message.p2p_msg:readonly published.
            logger.info("feishu WS connected: account={} app_id={} domain={}",
                        self.account_id, self._app_id, self._domain)
            self._notify_status("connected", None, None)
            self._ws.start()
        except Exception as exc:
            if not self._stopping:
                status, code, msg = _classify_ws_error(exc)
                logger.warning("feishu WS ended account={} status={}: {}",
                               self.account_id, status, exc)
                self._notify_status(status, code, msg)
        else:
            if not self._stopping:
                logger.info("feishu WS closed account={}", self.account_id)

    def _dispatch(self, data) -> None:
        try:
            event = data.event
            message = event.message
            mtype = getattr(message, "message_type", None)
            if mtype != "text":
                logger.info("feishu inbound skipped (non-text): account={} type={}",
                            self.account_id, mtype)
                return  # MVP: text DMs only
            content = json.loads(getattr(message, "content", None) or "{}")
            text = content.get("text", "")
            open_id = ""
            sender = getattr(event, "sender", None)
            if sender is not None and getattr(sender, "sender_id", None) is not None:
                open_id = getattr(sender.sender_id, "open_id", "") or ""
            inbound = InboundMessage(
                account_id=self.account_id,
                sender_open_id=open_id,
                chat_id=getattr(message, "chat_id", "") or "",
                text=text,
                message_id=getattr(message, "message_id", "") or "",
            )
        except Exception:
            logger.exception("feishu inbound parse failed account={}", self.account_id)
            return
        preview = text if len(text) <= 60 else text[:57] + "…"
        logger.info("feishu inbound: account={} chat={} from={} text={!r}",
                    self.account_id, inbound.chat_id, open_id[:14], preview)
        if self._loop is not None and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._on_message(inbound), self._loop)

    def _dispatch_card_action(self, data):
        """Feishu ``card.action.trigger`` over the long-connection. Runs on the lark thread
        and MUST return a ``P2CardActionTriggerResponse`` synchronously (the SDK sends it back
        to the tapper). Parses the action, hands it to ``card_actions.handle`` (pure decision →
        response card + optional resolve coroutine), and schedules the resolve POST on the
        connector loop. The synchronous response updates the card in place (reveal / terminal)."""
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )
        _fail = P2CardActionTriggerResponse({"toast": {"type": "error", "content": "处理失败"}})
        try:
            ev = getattr(data, "event", None)
            action = getattr(ev, "action", None)
            operator = getattr(ev, "operator", None)
            context = getattr(ev, "context", None)
            parsed = {
                "open_id": getattr(operator, "open_id", None) if operator else None,
                "tag": getattr(action, "tag", None) if action else None,
                "name": getattr(action, "name", None) if action else None,
                "value": getattr(action, "value", None) if action else None,
                "option": getattr(action, "option", None) if action else None,
                "options": getattr(action, "options", None) if action else None,
                "checked": getattr(action, "checked", None) if action else None,
                "input_value": getattr(action, "input_value", None) if action else None,
                "form_value": getattr(action, "form_value", None) if action else None,
                "message_id": getattr(context, "open_message_id", None) if context else None,
                "chat_id": getattr(context, "open_chat_id", None) if context else None,
            }
        except Exception:
            logger.exception("feishu card action parse failed account={}", self.account_id)
            return _fail

        logger.info("feishu CARD ACTION account={} tag={} name={} option={} msg={}",
                    self.account_id, parsed.get("tag"), parsed.get("name"),
                    parsed.get("option"), parsed.get("message_id"))
        try:
            from . import card_actions
            response, coro = card_actions.handle(self.account_id, parsed)
            if coro is not None:
                if self._loop is not None and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(coro, self._loop)
                else:
                    coro.close()
            return P2CardActionTriggerResponse(response)
        except Exception:
            logger.exception("feishu card action handle failed account={}", self.account_id)
            return _fail

    def _send_sync(self, chat_id: str, text: str) -> None:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        if self._rest is None:
            self._rest = self._build_rest()
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = self._rest.im.v1.message.create(req)
        if not resp.success():
            logger.warning(
                "lark send failed account={} code={} msg={}",
                self.account_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> str | None:
        # POST /im/v1/messages/:message_id/reactions {reaction_type:{emoji_type}} → reaction_id.
        # emoji_type is a fixed Feishu enum key (Typing / CheckMark / CrossMark, case-sensitive).
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )

        if self._rest is None:
            self._rest = self._build_rest()
        req = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                .build()
            )
            .build()
        )
        resp = self._rest.im.v1.message_reaction.create(req)
        if not resp.success():
            logger.warning(
                "lark reaction add failed account={} emoji={} code={} msg={}",
                self.account_id, emoji_type, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )
            return None
        return getattr(getattr(resp, "data", None), "reaction_id", None)

    def _remove_reaction_sync(self, message_id: str, reaction_id: str) -> None:
        # DELETE /im/v1/messages/:message_id/reactions/:reaction_id (removes the bot's own).
        from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

        if self._rest is None:
            self._rest = self._build_rest()
        req = (
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        resp = self._rest.im.v1.message_reaction.delete(req)
        if not resp.success():
            logger.warning(
                "lark reaction remove failed account={} rid={} code={} msg={}",
                self.account_id, reaction_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )

    def _send_card_sync(self, chat_id: str, card: dict) -> str | None:
        # POST /im/v1/messages msg_type="interactive", content=<card json string> → message_id.
        import lark_oapi as lark  # noqa: F401  (kept lazy like the rest of this module)
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        if self._rest is None:
            self._rest = self._build_rest()
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card))
                .build()
            )
            .build()
        )
        resp = self._rest.im.v1.message.create(req)
        if not resp.success():
            logger.warning(
                "lark card send failed account={} code={} msg={}",
                self.account_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )
            return None
        return getattr(getattr(resp, "data", None), "message_id", None)

    def _patch_card_sync(self, message_id: str, card: dict) -> None:
        # PATCH /im/v1/messages/:message_id — wholesale replace of the interactive card.
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        if self._rest is None:
            self._rest = self._build_rest()
        req = (
            PatchMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                PatchMessageRequestBody.builder()
                .content(json.dumps(card))
                .build()
            )
            .build()
        )
        resp = self._rest.im.v1.message.patch(req)
        if not resp.success():
            logger.warning(
                "lark card patch failed account={} mid={} code={} msg={}",
                self.account_id, message_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )

    # --- status bridge (called from the lark thread) ----------------------
    def _notify_status(self, status: str, code: int | None, message: str | None) -> None:
        if self._on_status is None or self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._on_status(status, code, message), self._loop)


def make_lark_transport(account_id, app_id, app_secret, domain, on_message, on_status=None):
    """TransportFactory: build one app's WS transport (matches transport.TransportFactory)."""
    return LarkTransport(account_id, app_id, app_secret, domain, on_message, on_status)
