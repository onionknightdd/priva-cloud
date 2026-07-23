"""In-process registry of interactive-card prompts awaiting a user tap.

Keyed by the card's Feishu ``message_id``: every ``card.action.trigger`` carries
``context.open_message_id``, and a ``form_submit`` button carries no callback value —
so the message_id is the one uniform correlation key back to the prompt (one card = one
permission_request = one message_id). Also indexed by ``request_id`` for the timeout
path (``permission_timeout`` names the request, not the card).

One connector process fields many accounts/runs at once; entries are removed on resolve,
skip, or timeout. This is deliberately a plain module-level dict — the connector is a
single-replica Deployment and the maps are touched from the lark WS thread (sync lookup
to build the card-action response) and the asyncio loop (register/discard); dict ops are
atomic enough under the GIL for this use.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class PendingPrompt:
    """One AskUserQuestion / permission request surfaced as an interactive card, awaiting
    the user's tap. Built by the worker from the ``permission_request`` SSE event merged
    with the DM context; the card's message_id is filled in once the card is posted."""
    request_id: str
    session_id: str
    account_id: str
    username: str | None
    chat_id: str
    kind: str                  # "ask_user" | "permission"
    questions: list            # AUQ questions[] — used to render the card AND build the answer
    tool_name: str = ""        # for kind="permission" (which tool is being gated)
    tool_input: dict | None = None  # the gated tool's raw input (permission card shows the command/args)
    reason: str = ""           # optional human reason (risky-tool permission)
    sender_open_id: str = ""   # only this operator may answer (the original DM sender)
    message_id: str = ""       # the card's message id — the registry key; set after send_card
    status: str = "pending"    # pending | answered | skipped | timeout
    # Embedded mode: the prompt renders INSIDE the streaming process card (message_id == the
    # streaming card's id). `state` is that card's StreamState (so a tap can re-render it);
    # `reveal` flips true when the user picks '我有其他的想法' so the custom input is shown.
    state: object | None = None
    reveal: bool = False
    selected: str = ""         # model① dropdown pick held until the user clicks 提交 (no auto-submit)
    registered_at: float = 0.0  # time.monotonic() at register(); the TTL-sweep key


_BY_MESSAGE: dict[str, PendingPrompt] = {}
_BY_REQUEST: dict[str, PendingPrompt] = {}

# Backstop only: normal removal is resolve / skip / timeout / run-end expiry. A prompt
# outlives all of those when its run dies without a permission_timeout (crashed dial,
# severed SSE) — without a sweep the module dicts grow forever, each entry pinning its
# whole StreamState.
_TTL_SECONDS = 3600.0


def register(prompt: PendingPrompt) -> None:
    """Index a prompt once its card is posted (message_id known)."""
    prompt.registered_at = time.monotonic()
    _sweep()
    if prompt.message_id:
        _BY_MESSAGE[prompt.message_id] = prompt
    _BY_REQUEST[prompt.request_id] = prompt


def _sweep(now: float | None = None) -> None:
    """Opportunistic TTL sweep (piggybacks on register — no timer to manage)."""
    now = time.monotonic() if now is None else now
    for index in (_BY_MESSAGE, _BY_REQUEST):
        stale = [k for k, p in index.items() if now - p.registered_at > _TTL_SECONDS]
        for k in stale:
            prompt = index.pop(k, None)
            if prompt is not None and prompt.status == "pending":
                prompt.status = "expired"


def get_by_message(message_id: str) -> PendingPrompt | None:
    return _BY_MESSAGE.get(message_id) if message_id else None


def get_by_request(request_id: str) -> PendingPrompt | None:
    return _BY_REQUEST.get(request_id) if request_id else None


def discard(prompt: PendingPrompt) -> None:
    """Drop a prompt from both indexes (resolved / skipped / timed out)."""
    if prompt.message_id:
        _BY_MESSAGE.pop(prompt.message_id, None)
    _BY_REQUEST.pop(prompt.request_id, None)
