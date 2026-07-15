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

from dataclasses import dataclass

from priva_common.logging import get_app_logger

from .transport import InboundMessage

logger = get_app_logger(__name__)

# "/new" family only. "/clear" and "/compact" are deliberately absent — they pass
# straight to the SDK.
NEW_COMMANDS = frozenset({"/new", "/新", "/reset"})


@dataclass
class Decision:
    kind: str                              # "detach" | "run"
    prompt: str | None = None              # the run prompt (raw text; incl. /clear, /compact)
    resume_session_id: str | None = None   # None => fresh session


class SessionRouter:
    def __init__(self, client):
        self._client = client

    # --- binding helpers (unique per account) -----------------------------
    def _binding(self, account_id: str):
        bs = self._client.bindings.list_bindings(account_id)
        return bs[0] if bs else None

    # --- decisioning ------------------------------------------------------
    def decide(self, msg: InboundMessage) -> Decision:
        text = (msg.text or "").strip()
        if text in NEW_COMMANDS:
            return Decision(kind="detach")
        b = self._binding(msg.account_id)
        return Decision(
            kind="run",
            prompt=msg.text,
            resume_session_id=(b.session_uuid if b else None),
        )

    def detach(self, account_id: str, chat_id: str | None = None) -> None:
        """/new: rebind session_uuid → NULL (keeps first_run_done=0). No-op when the
        account was never bound (nothing to detach — the next DM is already fresh)."""
        if self._binding(account_id):
            self._client.bindings.rebind(account_id, None, chat_id)

    def commit_session(self, account_id: str, assigned_sid: str, chat_id: str | None = None) -> None:
        """After a run, persist the SDK's session id. bind() on first ever DM, rebind()
        when the id changed (fresh session, or the SDK rotated it). No-op if unchanged."""
        b = self._binding(account_id)
        if b is None:
            self._client.bindings.bind(account_id, assigned_sid, chat_id)
        elif b.session_uuid != assigned_sid:
            self._client.bindings.rebind(account_id, assigned_sid, chat_id)

    # --- access gate ------------------------------------------------------
    def access_allowed(self, cfg, msg: InboundMessage) -> bool:
        """Model B MVP: connection == owner. A self-built app only serves people in its
        Feishu 可用范围, so every in-range DM routes to the owner's pod. ``owner_only`` and
        ``all`` both allow; ``allowlist`` is not enforced yet — the inbound sender's
        union_id lives in the *bot app's* namespace, which doesn't match the platform
        ``account.feishu_user_id`` (spec §12-2), so the list is kept but inert."""
        mode = getattr(cfg, "single_chat_access_mode", "owner_only")
        if mode in ("owner_only", "all", "allowlist"):
            return True
        return True
