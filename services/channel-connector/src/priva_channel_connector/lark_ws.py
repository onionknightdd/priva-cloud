"""LarkTransport — one Feishu/Lark app's WS long-connection on its own thread.

``lark_oapi``'s ``ws.Client.start()`` blocks its thread running an internal event loop,
so each app gets a dedicated thread (spec fact ②: 1 conn/app; same-app 2nd conn splits
events — hence exactly one). Inbound events fire on that thread and are bridged onto the
connector's asyncio loop via ``run_coroutine_threadsafe`` (the handler returns
immediately, honoring Feishu's <3s ack). Outbound sends use the REST client on a worker
thread. ``lark_oapi`` is imported lazily so the package (and its unit tests) import
without the dependency.

The SDK keeps ONE module-global event loop (``ws.client.loop``) and ``start()`` parks it
in ``run_until_complete(_select())`` forever — so out of the box a process can hold exactly
one live WS, and any later ``start()`` (second account, or re-arm after teardown) dies with
"This event loop is already running". ``_ThreadLocalLoopProxy`` swaps that global for a
per-thread loop so each app owns its connection AND its teardown. ``stop()`` is a bounded,
ordered sequence (reconnect off → ``_disconnect`` on the connection's own loop →
``loop.stop`` → join); the exiting thread then cancels leftover tasks, force-closes a
socket the graceful path missed, and closes its loop — deterministic release of the
socket, the thread, and the loop's selector fds. Failures at any step are logged and fall
through to the next backstop (final backstop: daemon thread dies with the process).

NOTE: written against the lark_oapi v1 Python SDK surface documented in the design
(register_p2_im_message_receive_v1 over a long-connection); internals we touch
(``_disconnect``/``_auto_reconnect``/``_conn``, reconnect hooks) are hasattr-guarded and
verified on 1.7.1. card.action.trigger is delivered over the SAME long-connection via
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

_LOOP_PROXY_LOCK = threading.Lock()


class _ThreadLocalLoopProxy:
    """Stand-in for ``lark_oapi.ws.client``'s module-global ``loop``: forwards every
    attribute access to the calling thread's adopted loop (fallback: the SDK's original
    global). The SDK only touches ``loop`` from the thread running ``start()``, so
    per-thread forwarding gives every app its own event loop. Teardown code must hold a
    direct reference to the real loop (``LarkTransport._tloop``) — going through the
    proxy from another thread would resolve to the wrong loop."""

    def __init__(self, fallback):
        self._fallback = fallback
        self._local = threading.local()

    def adopt(self, loop) -> None:
        self._local.loop = loop

    def release(self) -> None:
        self._local.loop = None

    def __getattr__(self, name):
        target = getattr(self._local, "loop", None) or self._fallback
        return getattr(target, name)


def _install_loop_proxy(wsc) -> _ThreadLocalLoopProxy:
    """Idempotently replace the SDK's global loop with the thread-local proxy."""
    with _LOOP_PROXY_LOCK:
        current = getattr(wsc, "loop", None)
        if isinstance(current, _ThreadLocalLoopProxy):
            return current
        proxy = _ThreadLocalLoopProxy(current)
        wsc.loop = proxy
        return proxy


_POST_TEXT_TAGS = {"text", "md", "code_block"}


def _parse_post_content(content: dict) -> tuple[str, list[str]]:
    """Flatten a Feishu rich-text (post) body: text-ish runs join into the prompt
    (title first, one line per paragraph), ``img`` runs contribute their image_key in
    order. Unsupported runs (media/file/emotion/at/hr) are dropped — text + images is
    the DM scope."""
    lines: list[str] = []
    image_keys: list[str] = []
    title = (content.get("title") or "").strip()
    if title:
        lines.append(title)
    for paragraph in content.get("content") or []:
        if not isinstance(paragraph, list):
            continue
        parts: list[str] = []
        for run in paragraph:
            if not isinstance(run, dict):
                continue
            tag = run.get("tag")
            if tag in _POST_TEXT_TAGS:
                parts.append(run.get("text") or "")
            elif tag == "a":
                text, href = run.get("text") or "", run.get("href") or ""
                parts.append(f"{text} ({href})" if href and href != text else (text or href))
            elif tag == "img":
                key = run.get("image_key") or ""
                if key:
                    image_keys.append(key)
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines), image_keys


def _strip_mention_placeholders(text: str, mentions) -> str:
    """群聊 @ 占位符处理（feat_feishu_DM.md §5.2）：行首的提及（典型 "@bot 指令"，
    在 group_at_msg 权限契约下就是 @bot 触发词）整体剥离，让 "/new" 等命令照常
    命中；句中提及替换为 "@名字" 保留人类可读语义。"""
    s = text or ""
    pairs: list[tuple[str, str]] = []
    for m in mentions or []:
        key = getattr(m, "key", "") or ""
        if key:
            pairs.append((key, getattr(m, "name", "") or ""))
    if not pairs:
        return s
    changed = True
    while changed:
        changed = False
        lead = s.lstrip()
        for key, _ in pairs:
            if lead.startswith(key):
                s = lead[len(key):]
                changed = True
                break
    for key, name in pairs:
        s = s.replace(key, f"@{name}" if name else "")
    return s.strip()


def _sniff_image_media_type(data: bytes) -> str | None:
    """Magic-byte sniff. Exactly the four formats the runner's image validator accepts
    (routers/agent.py _ALLOWED_IMAGE_TYPES) — anything else returns None and is skipped."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
        self._tloop: asyncio.AbstractEventLoop | None = None  # the lark thread's own loop
        self._thread: threading.Thread | None = None
        self._ws = None
        self._rest = None
        self._stopping = False
        self._chat_names: dict[str, str] = {}  # chat_id -> 群名称 (diagnostic log cache)

    # --- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._rest = await asyncio.to_thread(self._build_rest)
        self._thread = threading.Thread(
            target=self._run_ws, name=f"lark-ws-{self.account_id}", daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        """Bounded, ordered teardown. Each step degrades to the next backstop on
        failure (graceful close → thread-exit force-close → daemon flag), and every
        failure is logged — the socket must never linger silently: a live zombie WS
        keeps STEALING events from whichever connection replaces it (Feishu delivers
        each event to only one of an app's connections)."""
        self._stopping = True
        ws, tloop, thread = self._ws, self._tloop, self._thread
        # 1. Reconnect off BEFORE closing, or the SDK races us back to connected.
        if ws is not None and hasattr(ws, "_auto_reconnect"):
            ws._auto_reconnect = False
        if tloop is not None and not tloop.is_closed():
            # 2. Graceful close ON the connection's own loop. (_disconnect is a
            #    coroutine — calling it via to_thread would just create and drop the
            #    coroutine object without ever running it: the original zombie bug.)
            if ws is not None and hasattr(ws, "_disconnect"):
                coro = ws._disconnect()
                try:
                    fut = asyncio.run_coroutine_threadsafe(coro, tloop)
                except Exception:
                    coro.close()
                    logger.exception("lark ws disconnect not schedulable account={}", self.account_id)
                else:
                    try:
                        await asyncio.to_thread(fut.result, 5)
                    except Exception:
                        logger.exception(
                            "lark ws disconnect failed account={} (thread cleanup will force-close)",
                            self.account_id)
            # 3. Stop the loop → run_until_complete(_select()) returns → thread exits
            #    through _cleanup_ws_thread.
            try:
                tloop.call_soon_threadsafe(tloop.stop)
            except RuntimeError:
                pass  # loop already closed by the exiting thread — that's the goal
        # 4. Join with a bound; a wedged thread is an observable error, and the daemon
        #    flag (single-replica maxSurge=0) remains the process-exit backstop.
        if thread is not None and thread.is_alive():
            await asyncio.to_thread(thread.join, 5)
            if thread.is_alive():
                logger.error("lark ws thread did not exit account={} (daemon backstop)", self.account_id)
        self._ws = None
        self._thread = None
        self._rest = None

    async def send_text(self, chat_id: str, text: str) -> None:
        await asyncio.to_thread(self._send_sync, chat_id, text)

    async def send_text_to_user(self, open_id: str, text: str) -> str | None:
        if not open_id:
            return None
        return await asyncio.to_thread(self._send_to_user_sync, open_id, text)

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

    async def send_card_to_user(self, open_id: str, card: dict) -> str | None:
        if not open_id:
            return None
        return await asyncio.to_thread(self._send_card_to_user_sync, open_id, card)

    async def patch_card(self, message_id: str, card: dict) -> None:
        if not message_id:
            return
        await asyncio.to_thread(self._patch_card_sync, message_id, card)

    async def fetch_image(self, message_id: str, image_key: str) -> tuple[bytes, str] | None:
        if not message_id or not image_key:
            return None
        try:
            return await asyncio.to_thread(self._fetch_image_sync, message_id, image_key)
        except Exception:
            logger.exception("lark image fetch crashed account={} key={}",
                             self.account_id, image_key[:24])
            return None

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

    def _rest_client(self):
        # stop() drops the REST client; a straggler send (cancelled turn settling its
        # reaction) must not silently rebuild it and keep talking on a disabled account.
        if self._rest is None:
            if self._stopping:
                raise RuntimeError("transport stopped")
            self._rest = self._build_rest()
        return self._rest

    def _run_ws(self) -> None:
        import lark_oapi as lark
        from lark_oapi.ws import client as wsc

        # Own loop for this connection (see module docstring: the SDK's global loop
        # allows exactly one WS per process and can never be re-entered after stop).
        proxy = _install_loop_proxy(wsc)
        tloop = asyncio.new_event_loop()
        proxy.adopt(tloop)
        asyncio.set_event_loop(tloop)
        self._tloop = tloop
        try:
            self._run_ws_on(tloop, lark, wsc)
        finally:
            self._cleanup_ws_thread(tloop)
            proxy.release()
            asyncio.set_event_loop(None)
            self._tloop = None

    def _run_ws_on(self, tloop, lark, wsc) -> None:
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

        def _on_p2_bot_menu(data) -> None:
            self._dispatch_bot_menu(data)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_on_p2_message)
            .register_p2_card_action_trigger(_on_p2_card_action)
            # 机器人自定义菜单（租户在开发者后台配置，需额外勾选「事件与回调」里的
            # application.bot.menu_v6）——没订阅时这个 handler 只是永不触发。
            .register_p2_application_bot_menu_v6(_on_p2_bot_menu)
            .build()
        )
        try:
            if self._stopping:
                return
            self._ws = _CardAwareClient(
                self._app_id, self._app_secret,
                event_handler=handler,
                domain=self._domain_const(lark),
                log_level=lark.LogLevel.WARNING,
            )
            # Surface reconnect churn as status (1.7.1 hooks; harmless no-op if absent):
            # without this the DB says "connected" while the SDK is mid-backoff.
            if hasattr(self._ws, "on_reconnecting"):
                self._ws.on_reconnecting = lambda: self._notify_status("error", None, "reconnecting")
            if hasattr(self._ws, "on_reconnected"):
                self._ws.on_reconnected = lambda: self._notify_status("connected", None, None)
            if self._stopping:
                return
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

    def _cleanup_ws_thread(self, tloop) -> None:
        """Runs on the lark thread as it exits — deterministic resource release, on
        every exit path (graceful stop, auth failure, crash). Cancels whatever the SDK
        left on the loop (_ping_loop / _receive_message_loop / handler tasks),
        force-closes a socket the graceful _disconnect didn't get to, then closes the
        loop itself (frees its selector + self-pipe fds)."""
        try:
            tasks = asyncio.all_tasks(tloop)
            for task in tasks:
                task.cancel()
            if tasks:
                tloop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            conn = getattr(self._ws, "_conn", None)
            if conn is not None:
                try:
                    tloop.run_until_complete(asyncio.wait_for(conn.close(), timeout=2))
                except Exception:
                    transport = getattr(conn, "transport", None)
                    if transport is not None:
                        try:
                            transport.abort()
                        except Exception:
                            pass
        except Exception:
            logger.exception("lark ws thread cleanup failed account={}", self.account_id)
        finally:
            try:
                tloop.close()
            except Exception:
                logger.exception("lark ws loop close failed account={}", self.account_id)

    def _dispatch(self, data) -> None:
        if self._stopping:
            return  # defense-in-depth: a not-yet-dead socket must not feed the pipeline
        try:
            event = data.event
            message = event.message
            mtype = getattr(message, "message_type", None)
            # Diagnostic meta line (owner/link-code groundwork): full sender identity +
            # chat context for EVERY inbound event, before any type filtering — union_id
            # is the bot-app-namespace id the owner binding will key on, tenant_key
            # flags external-tenant senders, chat_type separates 群/私聊.
            chat_id = getattr(message, "chat_id", "") or ""
            chat_type = getattr(message, "chat_type", "") or ""
            sender = getattr(event, "sender", None)
            sender_id = getattr(sender, "sender_id", None) if sender is not None else None
            s_open = (getattr(sender_id, "open_id", "") or "") if sender_id is not None else ""
            s_union = (getattr(sender_id, "union_id", "") or "") if sender_id is not None else ""
            s_user = (getattr(sender_id, "user_id", "") or "") if sender_id is not None else ""
            s_type = (getattr(sender, "sender_type", "") or "") if sender is not None else ""
            s_tenant = (getattr(sender, "tenant_key", "") or "") if sender is not None else ""
            logger.info(
                "feishu inbound meta: account={} chat={} chat_type={} msg_type={} "
                "sender_type={} tenant_key={} open_id={} user_id={} union_id={}",
                self.account_id, chat_id, chat_type or "?", mtype,
                s_type or "?", s_tenant or "?", s_open or "?", s_user or "?", s_union or "?")
            if chat_type == "group":
                self._log_group_name(chat_id)
            if chat_type and chat_type not in ("p2p", "group"):
                # p2p 走单聊链路，group 由 worker 按 effective_group_enabled + @ 触发
                # 裁决（feat_feishu_DM.md §5.3）；其余会话形态不进 pipeline。
                logger.info("feishu inbound skipped (unsupported chat_type): account={} chat_type={}",
                            self.account_id, chat_type)
                return
            mentions = getattr(message, "mentions", None) or []
            content = json.loads(getattr(message, "content", None) or "{}")
            image_keys: list[str] = []
            if mtype == "text":
                text = _strip_mention_placeholders(content.get("text", ""), mentions)
            elif mtype == "image":
                text = ""
                key = content.get("image_key") or ""
                if key:
                    image_keys.append(key)
            elif mtype == "post":
                text, image_keys = _parse_post_content(content)
            else:
                logger.info("feishu inbound skipped (unsupported type): account={} type={}",
                            self.account_id, mtype)
                return  # text / image / post(图文) only
            if not text and not image_keys:
                logger.info("feishu inbound skipped (empty): account={} type={}",
                            self.account_id, mtype)
                return
            inbound = InboundMessage(
                account_id=self.account_id,
                sender_open_id=s_open,
                chat_id=chat_id,
                text=text,
                message_id=getattr(message, "message_id", "") or "",
                image_keys=tuple(image_keys),
                sender_union_id=s_union,
                chat_type=chat_type,
                mentioned=bool(mentions),
            )
        except Exception:
            logger.exception("feishu inbound parse failed account={}", self.account_id)
            return
        preview = text if len(text) <= 60 else text[:57] + "…"
        logger.info("feishu inbound: account={} chat={} from={} text={!r} images={}",
                    self.account_id, inbound.chat_id, s_open[:14], preview, len(image_keys))
        if self._loop is not None and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._on_message(inbound), self._loop)

    def _dispatch_bot_menu(self, data) -> None:
        """``application.bot.menu_v6``：机器人自定义菜单点击。事件体只有 operator +
        event_key（无 chat_id / message_id），所以解析后直接交给 card_actions 路由到
        本账号的 worker，由它解出 p2p 会话再走合成消息管道。跑在 lark 线程上（<3s ack），
        任何真活都必须调度回 connector loop。"""
        if self._stopping:
            return
        try:
            ev = getattr(data, "event", None)
            operator = getattr(ev, "operator", None)
            oid = getattr(operator, "operator_id", None) if operator else None
            event_key = getattr(ev, "event_key", None) or ""
            open_id = getattr(oid, "open_id", None) or ""
            union_id = getattr(oid, "union_id", None) or ""
        except Exception:
            logger.exception("feishu bot menu parse failed account={}", self.account_id)
            return
        logger.info("feishu BOT MENU account={} key={!r} from={}",
                    self.account_id, event_key, (open_id or "-")[:14])
        try:
            from . import card_actions
            coro = card_actions.handle_bot_menu(self.account_id, event_key, open_id, union_id)
        except Exception:
            logger.exception("feishu bot menu handle failed account={}", self.account_id)
            return
        if coro is None:
            return
        if self._loop is not None and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        else:
            coro.close()

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
        if self._stopping:
            return P2CardActionTriggerResponse(
                {"toast": {"type": "info", "content": "连接已关闭，请重新发起对话"}})
        try:
            ev = getattr(data, "event", None)
            action = getattr(ev, "action", None)
            operator = getattr(ev, "operator", None)
            context = getattr(ev, "context", None)
            parsed = {
                "open_id": getattr(operator, "open_id", None) if operator else None,
                # 引导卡片按钮合成的入站消息要过 owner/allowlist gate → 需要 union_id。
                "union_id": getattr(operator, "union_id", None) if operator else None,
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
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        rest = self._rest_client()
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
        resp = rest.im.v1.message.create(req)
        if not resp.success():
            logger.warning(
                "lark send failed account={} code={} msg={}",
                self.account_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )

    def _send_to_user_sync(self, open_id: str, text: str) -> str | None:
        """按 open_id 投递（receive_id_type=open_id）并从响应里读回 p2p ``chat_id``。
        自定义菜单点击的事件体没有 chat_id，这是唯一能解出会话的官方途径（§9 方案②）。"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        rest = self._rest_client()
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = rest.im.v1.message.create(req)
        if not resp.success():
            logger.warning(
                "lark send-to-user failed account={} code={} msg={}",
                self.account_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )
            return None
        return getattr(getattr(resp, "data", None), "chat_id", None)

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> str | None:
        # POST /im/v1/messages/:message_id/reactions {reaction_type:{emoji_type}} → reaction_id.
        # emoji_type is a fixed Feishu enum key (Typing / CheckMark / CrossMark, case-sensitive).
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )

        rest = self._rest_client()
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
        resp = rest.im.v1.message_reaction.create(req)
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

        rest = self._rest_client()
        req = (
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        resp = rest.im.v1.message_reaction.delete(req)
        if not resp.success():
            logger.warning(
                "lark reaction remove failed account={} rid={} code={} msg={}",
                self.account_id, reaction_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )

    def _send_card_sync(self, chat_id: str, card: dict) -> str | None:
        # POST /im/v1/messages msg_type="interactive", content=<card json string> → message_id.
        import lark_oapi as lark  # noqa: F401  (kept lazy like the rest of this module)
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        rest = self._rest_client()
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
        resp = rest.im.v1.message.create(req)
        if not resp.success():
            logger.warning(
                "lark card send failed account={} code={} msg={}",
                self.account_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )
            return None
        return getattr(getattr(resp, "data", None), "message_id", None)

    def _send_card_to_user_sync(self, open_id: str, card: dict) -> str | None:
        """Post an interactive card proactively using the bot-scoped owner open_id."""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        rest = self._rest_client()
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("interactive")
                .content(json.dumps(card))
                .build()
            )
            .build()
        )
        resp = rest.im.v1.message.create(req)
        if not resp.success():
            logger.warning(
                "lark card send-to-user failed account={} code={} msg={}",
                self.account_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )
            return None
        return getattr(getattr(resp, "data", None), "message_id", None)

    # --- group-chat diagnostics ------------------------------------------
    def _log_group_name(self, chat_id: str) -> None:
        """群聊消息补一条群名称日志。REST 调用不能阻塞 lark 线程（<3s ack），调度到
        connector 主 loop 执行；按 chat_id 缓存，同一群只拉一次。"""
        if not chat_id:
            return
        name = self._chat_names.get(chat_id)
        if name is not None:
            logger.info("feishu group chat: account={} chat={} name={!r} (cached)",
                        self.account_id, chat_id, name)
            return
        if self._loop is not None and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._fetch_and_log_group_name(chat_id), self._loop)

    async def _fetch_and_log_group_name(self, chat_id: str) -> None:
        try:
            name = await asyncio.to_thread(self._get_chat_name_sync, chat_id)
        except Exception:
            logger.exception("lark chat info fetch crashed account={} chat={}",
                             self.account_id, chat_id)
            return
        if name is None:
            return
        if len(self._chat_names) > 256:
            self._chat_names.clear()
        self._chat_names[chat_id] = name
        logger.info("feishu group chat: account={} chat={} name={!r}",
                    self.account_id, chat_id, name)

    def _get_chat_name_sync(self, chat_id: str) -> str | None:
        # GET /im/v1/chats/:chat_id — 群信息需要 im:chat:readonly（或 im:chat）权限。
        from lark_oapi.api.im.v1 import GetChatRequest

        rest = self._rest_client()
        resp = rest.im.v1.chat.get(GetChatRequest.builder().chat_id(chat_id).build())
        if not resp.success():
            logger.warning(
                "lark chat info fetch failed account={} chat={} code={} msg={} (群信息需要 im:chat:readonly)",
                self.account_id, chat_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )
            return None
        return getattr(getattr(resp, "data", None), "name", None) or None

    def _get_user_name_sync(self, open_id: str) -> str | None:
        # GET /contact/v3/users/:user_id — 用户名需要 contact:user.base:readonly 权限；
        # 未开通时降级（调用方 fallback 到 open_id 缩写），不影响消息处理。
        from lark_oapi.api.contact.v3 import GetUserRequest

        rest = self._rest_client()
        req = GetUserRequest.builder().user_id(open_id).user_id_type("open_id").build()
        resp = rest.contact.v3.user.get(req)
        if not resp.success():
            logger.warning(
                "lark user info fetch failed account={} open_id={} code={} msg={} "
                "(人名需要 contact:user.base:readonly)",
                self.account_id, open_id[:14], getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )
            return None
        user = getattr(getattr(resp, "data", None), "user", None)
        return getattr(user, "name", None) or None

    async def fetch_display_name(self, chat_id: str, chat_type: str, sender_open_id: str) -> str:
        """会话展示名（设置页会话列表用）：群聊 → 群名（im:chat:readonly），私聊 →
        对方人名（contact:user.base:readonly）。取不到（缺权限/网络）返回 ""，
        调用方自行降级。群名共用诊断缓存。"""
        try:
            if chat_type == "group":
                name = self._chat_names.get(chat_id)
                if name is None:
                    name = await asyncio.to_thread(self._get_chat_name_sync, chat_id)
                    if name:
                        if len(self._chat_names) > 256:
                            self._chat_names.clear()
                        self._chat_names[chat_id] = name
                return name or ""
            if sender_open_id:
                return (await asyncio.to_thread(self._get_user_name_sync, sender_open_id)) or ""
        except Exception:
            logger.exception("display name fetch failed account={} chat={}", self.account_id, chat_id)
        return ""

    def _fetch_image_sync(self, message_id: str, image_key: str) -> tuple[bytes, str] | None:
        # GET /im/v1/messages/:message_id/resources/:file_key?type=image — message-scoped
        # resource fetch with the tenant token. Needs the app to have the im:resource
        # scope published; without it Feishu returns 403 (logged, run continues w/o image).
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        rest = self._rest_client()
        req = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(image_key)
            .type("image")
            .build()
        )
        resp = rest.im.v1.message_resource.get(req)
        if not resp.success() or getattr(resp, "file", None) is None:
            logger.warning(
                "lark image fetch failed account={} key={} code={} msg={} (403 → check im:resource scope)",
                self.account_id, image_key[:24], getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )
            return None
        data = resp.file.read()
        media_type = _sniff_image_media_type(data)
        if media_type is None:
            logger.warning("lark image fetch: unsupported format account={} key={} bytes={}",
                           self.account_id, image_key[:24], len(data))
            return None
        return data, media_type

    def _patch_card_sync(self, message_id: str, card: dict) -> None:
        # PATCH /im/v1/messages/:message_id — wholesale replace of the interactive card.
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        rest = self._rest_client()
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
        resp = rest.im.v1.message.patch(req)
        if not resp.success():
            logger.warning(
                "lark card patch failed account={} mid={} code={} msg={}",
                self.account_id, message_id, getattr(resp, "code", "?"), getattr(resp, "msg", "?"),
            )

    # --- status bridge (called from the lark thread) ----------------------
    def _notify_status(self, status: str, code: int | None, message: str | None) -> None:
        if self._stopping:
            return  # engine's positive-ACK "disabled" must not be overwritten by a late status
        if self._on_status is None or self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._on_status(status, code, message), self._loop)


def make_lark_transport(account_id, app_id, app_secret, domain, on_message, on_status=None):
    """TransportFactory: build one app's WS transport (matches transport.TransportFactory)."""
    return LarkTransport(account_id, app_id, app_secret, domain, on_message, on_status)
