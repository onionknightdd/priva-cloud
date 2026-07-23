"""Inbound decisioning — access gate, slash-command classification, and binding
lifecycle. Pure-ish: every dataplane call goes through the injected client, so this
is unit-testable with a fake client (no gRPC, no live pod).

Session lifecycle (user-defined 2026-07-15): inherit the SDK's slash commands.
  - ``/new`` (``/新``) → DETACH: rebind the binding's session_uuid to NULL so the next
    DM starts a fresh session. The command itself does NOT run the agent.
  - ``/clear`` and ``/compact`` → NOT intercepted: they flow through as the run prompt
    and the Claude SDK interprets them (clear context / summarise-and-continue).
  - anything else → RUN with the current bound session_uuid (resume), or None (fresh).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from priva_common.logging import get_app_logger

from .transport import InboundMessage

logger = get_app_logger(__name__)

# "/new" family only. "/clear" and "/compact" are deliberately absent — they pass
# straight to the SDK.
NEW_COMMANDS = frozenset({"/new", "/新", "/reset"})

# "/link A7K2MQ" / "/绑定 A7K2MQ" — owner link-code binding (feat_feishu_DM.md §4).
# Loose length here; data-spine does the real (hashed, constant-time) validation.
_LINK_RE = re.compile(r"^/(?:link|绑定)\s+([A-Za-z0-9]{4,12})$")


def match_link_code(text: str) -> str | None:
    """Return the normalized (uppercase) link code if the DM is a bind command.
    Checked BEFORE the access gate: re-binding from a new Feishu identity must not
    be blocked by the previous owner's gate — code possession (minted behind the
    platform login) IS the authorization."""
    m = _LINK_RE.match((text or "").strip())
    return m.group(1).upper() if m else None


@dataclass
class Decision:
    kind: str                              # "detach" | "run"
    prompt: str | None = None              # the run prompt (raw text; incl. /clear, /compact)
    resume_session_id: str | None = None   # None => fresh session


class SessionRouter:
    def __init__(self, client):
        self._client = client

    # --- binding helpers (unique per account+chat, feat_feishu_DM.md §5.2) --
    def _binding(self, account_id: str, chat_id: str | None):
        """Per-chat session: every p2p chat and every group holds its own binding.
        Matched by feishu_chat_id equality; a legacy row whose chat_id never matches
        simply goes dormant (the chat starts a fresh session and its own row)."""
        for b in self._client.bindings.list_bindings(account_id):
            if (b.feishu_chat_id or "") == (chat_id or ""):
                return b
        return None

    # --- decisioning ------------------------------------------------------
    def decide(self, msg: InboundMessage) -> Decision:
        text = (msg.text or "").strip()
        if text in NEW_COMMANDS:
            return Decision(kind="detach")
        b = self._binding(msg.account_id, msg.chat_id)
        return Decision(
            kind="run",
            prompt=msg.text,
            resume_session_id=(b.session_uuid if b else None),
        )

    def detach(self, account_id: str, chat_id: str | None = None) -> None:
        """/new: rebind THIS chat's session_uuid → NULL (keeps first_run_done=0).
        Other chats' sessions are untouched; no-op when the chat was never bound."""
        if self._binding(account_id, chat_id):
            self._client.bindings.rebind(account_id, None, chat_id)

    def commit_session(self, account_id: str, assigned_sid: str, chat_id: str | None = None) -> None:
        """After a run, persist the SDK's session id on THIS chat's binding. bind() on
        the chat's first ever run, rebind() when the id changed (fresh session, or the
        SDK rotated it). No-op if unchanged."""
        b = self._binding(account_id, chat_id)
        if b is None:
            self._client.bindings.bind(account_id, assigned_sid, chat_id)
        elif b.session_uuid != assigned_sid:
            self._client.bindings.rebind(account_id, assigned_sid, chat_id)

    # --- access gate ------------------------------------------------------
    def access_allowed(self, cfg, msg: InboundMessage) -> bool:
        """Owner gate (feat_feishu_DM.md §4.2). The comparison key is the sender's
        union_id in the BOT app's namespace, self-bootstrapped via link-code binding
        (the platform ``account.feishu_user_id`` lives in the SSO app's namespace and
        cannot be compared directly — spec §12-2). Until an owner is bound every mode
        allows (ruling #1: compat with pre-binding tenants; the UI surfaces 未绑定)."""
        mode = getattr(cfg, "single_chat_access_mode", "owner_only")
        if mode == "all":
            return True
        owner = getattr(cfg, "owner_union_id", "") or ""
        if not owner:
            return True  # unbound → allow (Feishu 可用范围 remains the only boundary)
        sender = getattr(msg, "sender_union_id", "") or ""
        if sender and sender == owner:
            return True
        if mode == "allowlist":
            try:
                allowed = json.loads(getattr(cfg, "allowed_union_ids", "[]") or "[]")
            except ValueError:
                allowed = []
            return bool(sender) and sender in allowed
        return False
