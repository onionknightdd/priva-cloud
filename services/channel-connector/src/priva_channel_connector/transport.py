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
    text: str             # extracted message text
    message_id: str       # feishu message id (idempotency)


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
    _rid_seq: int = 0

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

    # --- test helpers -----------------------------------------------------
    async def inject(self, msg: InboundMessage) -> None:
        await self.on_message(msg)

    @property
    def emojis(self) -> list[str]:
        """emoji_types added, in order — handy for asserting the Typing→terminal swap."""
        return [e for _, e in self.reactions]
