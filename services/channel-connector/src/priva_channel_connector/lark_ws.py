"""LarkTransport — one Feishu/Lark app's WS long-connection on its own thread.

``lark_oapi``'s ``ws.Client.start()`` blocks its thread running an internal event loop,
so each app gets a dedicated thread (spec fact ②: 1 conn/app; same-app 2nd conn splits
events — hence exactly one). Inbound events fire on that thread and are bridged onto the
connector's asyncio loop via ``run_coroutine_threadsafe`` (the handler returns
immediately, honoring Feishu's <3s ack). Outbound sends use the REST client on a worker
thread. ``lark_oapi`` is imported lazily so the package (and its unit tests) import
without the dependency.

NOTE: written against the lark_oapi v1 Python SDK surface documented in the design
(register_p2_im_message_receive_v1 over a long-connection). The exact stop() API and the
card.action.trigger payload need a live validation pass before production — see
`feishu-bot-bytepath.md` §9.
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

        def _on_p2_message(data) -> None:
            self._dispatch(data)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_on_p2_message)
            .build()
        )
        try:
            self._ws = lark.ws.Client(
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

    # --- status bridge (called from the lark thread) ----------------------
    def _notify_status(self, status: str, code: int | None, message: str | None) -> None:
        if self._on_status is None or self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._on_status(status, code, message), self._loop)


def make_lark_transport(account_id, app_id, app_secret, domain, on_message, on_status=None):
    """TransportFactory: build one app's WS transport (matches transport.TransportFactory)."""
    return LarkTransport(account_id, app_id, app_secret, domain, on_message, on_status)
