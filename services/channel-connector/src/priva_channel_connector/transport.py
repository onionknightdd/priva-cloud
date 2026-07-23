"""IM transport seam — one transport instance == one Feishu app == one account
(Model B: connection == account). The reconcile engine owns a transport per
effective account; the concrete lark_oapi WS lives in ``lark_ws.LarkTransport`` and
``FakeTransport`` backs the unit tests. Keeping the socket behind this Protocol is
what lets the routing/reconcile logic be tested without lark_oapi or a live Feishu
app."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol


@dataclass(frozen=True)
class InboundMessage:
    """A normalized inbound Feishu DM (transport-agnostic)."""
    account_id: str       # the app/account this socket belongs to (== routing target)
    sender_open_id: str   # who sent it (access gate + proactive addressing)
    chat_id: str          # p2p chat id (reply target)
    text: str             # extracted message text (text DM, or flattened post runs)
    message_id: str       # feishu message id (idempotency + image-resource fetch scope)
    image_keys: tuple[str, ...] = ()  # image/post 图片 run 的 image_key，保序；字节需另行拉取
    sender_union_id: str = ""  # bot 应用命名空间的 union_id — owner/allowlist gate 的比对键
    chat_type: str = ""        # "p2p" | "group"（空视为 p2p — 兼容旧 fake/测试）
    # 群聊 @ 触发（feat_feishu_DM.md §5.2）：该消息是否带 @ 提及。配套权限
    # im:message.group_at_msg:readonly 下只有 @bot 消息会推到长连接（权限即过滤器），
    # 所以 mentioned=True 即视为 @bot；text 已由 transport 完成占位符剥离。
    mentioned: bool = False


# Async callback the transport fires per inbound message (it has already ack'd <3s).
InboundHandler = Callable[[InboundMessage], Awaitable[None]]
# (status, error_code|None, error_message|None) — the connector writes it to conn_status.
StatusHandler = Callable[[str, "int | None", "str | None"], Awaitable[None]]

# Factory the engine calls to build a transport for one armed account:
#   (account_id, app_id, app_secret, domain, on_message, on_status) -> IMTransport
TransportFactory = Callable[
    [str, str, str, str, InboundHandler, "StatusHandler | None"], "IMTransport"
]


class IMTransport(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_text(self, chat_id: str, text: str) -> None: ...
    # Message-reaction lifecycle (emoji stamped on an *inbound* DM). ``add_reaction``
    # returns the created reaction_id (None if it failed / no message id) so the caller
    # can later ``remove_reaction`` it. Best-effort: cosmetic, never fail the run.
    async def add_reaction(self, message_id: str, emoji_type: str) -> "str | None": ...
    async def remove_reaction(self, message_id: str, reaction_id: str) -> None: ...
    # Interactive-card streaming. ``send_card`` posts a card and returns its message_id
    # (None on failure); ``patch_card`` replaces that card in place (Feishu patch is a
    # wholesale replace). Both best-effort — a card failure falls back to send_text.
    async def send_card(self, chat_id: str, card: dict) -> "str | None": ...
    async def patch_card(self, message_id: str, card: dict) -> None: ...
    # Download one inbound image (message-scoped resource fetch) → (bytes, media_type),
    # or None on any failure (missing im:resource scope, unknown format, network).
    async def fetch_image(self, message_id: str, image_key: str) -> "tuple[bytes, str] | None": ...


@dataclass
class FakeTransport:
    """In-memory transport for unit tests: capture sent messages, inject inbound."""
    account_id: str
    app_id: str
    app_secret: str
    domain: str
    on_message: InboundHandler
    on_status: "StatusHandler | None" = None
    started: bool = False
    stopped: bool = False
    sent: list[tuple[str, str]] = field(default_factory=list)  # (chat_id, text)
    reactions: list[tuple[str, str]] = field(default_factory=list)  # (message_id, emoji_type) added
    removed: list[str] = field(default_factory=list)               # reaction_ids removed
    cards: list[tuple[str, dict]] = field(default_factory=list)    # (chat_id, card) sent
    patches: list[tuple[str, dict]] = field(default_factory=list)  # (message_id, card) patched
    images: dict = field(default_factory=dict)                     # image_key -> (bytes, media_type)
    fetches: list[tuple[str, str]] = field(default_factory=list)   # (message_id, image_key) requested
    _rid_seq: int = 0
    _mid_seq: int = 0

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))

    async def add_reaction(self, message_id: str, emoji_type: str) -> "str | None":
        if not message_id:
            return None
        self._rid_seq += 1
        rid = f"r{self._rid_seq}"
        self.reactions.append((message_id, emoji_type))
        return rid

    async def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        self.removed.append(reaction_id)

    async def send_card(self, chat_id: str, card: dict) -> "str | None":
        self._mid_seq += 1
        mid = f"m{self._mid_seq}"
        self.cards.append((chat_id, card))
        return mid

    async def patch_card(self, message_id: str, card: dict) -> None:
        self.patches.append((message_id, card))

    async def fetch_image(self, message_id: str, image_key: str) -> "tuple[bytes, str] | None":
        self.fetches.append((message_id, image_key))
        return self.images.get(image_key)

    # --- test helpers -----------------------------------------------------
    async def inject(self, msg: InboundMessage) -> None:
        await self.on_message(msg)

    @property
    def emojis(self) -> list[str]:
        """emoji_types added, in order — handy for asserting the Typing→terminal swap."""
        return [e for _, e in self.reactions]
