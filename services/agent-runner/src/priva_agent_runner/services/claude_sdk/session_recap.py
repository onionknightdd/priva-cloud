"""Summarize a session in one line, without going through the CLI.

The bundled CLI has a recap of its own — ``/recap``, the "away summary" shown
when you return after being idle — but every route to it is unusable here.
There is no ``recap`` control-protocol subtype, so the private channel
:mod:`session_title` rides has no counterpart; and invoking the slash command
writes six entries into the transcript, one of them a plain user message the
model then reads back on the next turn.

So we generate it ourselves: a plain ``POST /v1/messages`` against the account's
own gateway, exactly like :func:`routers.credentials.load_model_list`. Three
things fall out of not using the CLI:

* the transcript stays clean — nothing we do is visible to the next turn;
* nothing is tied to a live CLI subprocess, so this is pure fire-and-forget with
  no teardown race to settle (contrast :mod:`session_title`, which must be
  joined before ``ClaudeSDKClient.__aexit__`` kills the subprocess);
* we can ask for a bare line of text instead of a ``{title: string}`` schema —
  the very constraint 4-bit local models miss often enough to leave most
  sessions showing their raw first prompt.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from claude_agent_sdk import get_session_messages

from priva_common.logging import get_app_logger
from priva_common.skill_exclude import get_user_yaml_key
from priva_common.user_env import read_settings_env

from ..http_client import external_async_client
from . import session_meta

logger = get_app_logger(__name__)

# Toggle key in ``.priva.user.yml``, alongside ``vision_model``. Absent means
# on: the file starts empty, so defaulting to off would hide the feature behind
# a setting nobody knows to look for.
_ENABLED_KEY = "recap_enabled"

_TIMEOUT_SEC = 30.0
_MAX_TOKENS = 80
# Budget for the digest we hand the model. Comfortably inside any context
# window while still carrying the shape of a long session.
_MAX_DIGEST_CHARS = 6000
_MAX_MESSAGE_CHARS = 600
# The opening exchange states the topic, so it is always worth its space even
# when the tail has to be cut.
_HEAD_MESSAGES = 2
# One user turn with no reply yet says nothing worth summarizing.
_MIN_MESSAGES = 2

_SYSTEM_PROMPT = (
    "You write one-line recaps of coding-assistant sessions.\n"
    "Given a transcript digest, reply with a single sentence describing what "
    "the session is about — what the user is trying to do and where it got to.\n"
    "Write in the same language the conversation uses.\n"
    "Keep it under 30 characters for Chinese, or 15 words for English.\n"
    "Output only that sentence: no quotes, no label, no bullet, no markdown, "
    "no trailing period-only filler, no newlines."
)

# Wrappers the CLI injects around slash commands and reminders. They are not
# conversation and would otherwise dominate a short session's digest.
_META_PREFIXES = (
    "<local-command-",
    "<command-name>",
    "<command-message>",
    "<system-reminder>",
)
# Models that ignore "no label" tend to reach for one of these.
_LABEL_RE = re.compile(r"^\s*(recap|summary|摘要|总结|概括)\s*[:：]\s*", re.IGNORECASE)

# create_task() only holds a weak reference, so a task nobody awaits can be
# garbage-collected mid-flight. Keep a strong ref until it finishes.
_pending: set[asyncio.Task] = set()


def _message_text(msg: Any) -> str:
    """The human-readable text of a transcript message, tool calls dropped."""
    raw = msg.message if isinstance(msg.message, dict) else None
    content = raw.get("content") if raw else None
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(p for p in parts if p)


def _build_digest(messages: list[Any]) -> str:
    """Head + as much of the tail as the budget allows, oldest first.

    A recap should reflect where the session ended up, so the tail wins ties;
    the head is kept regardless because it names the subject.
    """
    lines: list[str] = []
    for msg in messages:
        text = " ".join(_message_text(msg).split())
        if not text or text.startswith(_META_PREFIXES):
            continue
        lines.append(f"{msg.type}: {text[:_MAX_MESSAGE_CHARS]}")

    if not lines:
        return ""

    head = lines[:_HEAD_MESSAGES]
    budget = _MAX_DIGEST_CHARS - sum(len(x) + 1 for x in head)
    tail: list[str] = []
    for line in reversed(lines[len(head):]):
        if budget - len(line) - 1 < 0:
            break
        tail.append(line)
        budget -= len(line) + 1
    tail.reverse()

    elided = len(lines) - len(head) - len(tail)
    middle = [f"... ({elided} more messages) ..."] if elided > 0 else []
    return "\n".join([*head, *middle, *tail])


def _clean(raw: str) -> str:
    """First non-empty line, stripped of the decorations we asked not to get."""
    line = next((x.strip() for x in raw.splitlines() if x.strip()), "")
    line = _LABEL_RE.sub("", line)
    return line.strip().strip("\"'“”「」").strip()


async def _ask_model(digest: str) -> str:
    env = read_settings_env()
    base_url = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    auth_token = env.get("ANTHROPIC_AUTH_TOKEN") or ""
    # Prefer the account's small/fast model: a one-liner does not need the
    # model the session itself runs on.
    model = env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or env.get("ANTHROPIC_MODEL") or ""
    if not base_url or not auth_token or not model:
        # BYOK not configured, or no model id we could name. Nothing to do —
        # this account simply has no recaps.
        return ""

    # The operator's explicit egress proxy is used, while NO_PROXY remains
    # ignored so a tenant-controlled bypass cannot turn this into a direct call.
    async with external_async_client(base_url, timeout=_TIMEOUT_SEC) as client:
        resp = await client.post(
            f"{base_url}/v1/messages",
            headers={
                "Authorization": f"Bearer {auth_token}",
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": _MAX_TOKENS,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": digest}],
            },
        )
    if resp.status_code != 200:
        logger.debug("[RECAP] upstream returned {}: {}", resp.status_code, resp.text[:200])
        return ""

    content = resp.json().get("content")
    if not isinstance(content, list):
        return ""
    return _clean(
        "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    )


def is_enabled(username: str) -> bool:
    """Whether this account wants a model call per turn spent on recaps."""
    return bool(get_user_yaml_key(username, _ENABLED_KEY, True))


async def _refresh(session_id: str, username: str, cwd: str | None) -> None:
    if not is_enabled(username):
        # Checked before any work, so switching the toggle off really does cost
        # zero model calls rather than just hiding the result.
        return

    messages = get_session_messages(session_id, directory=cwd)
    if len(messages) < _MIN_MESSAGES:
        return

    existing = session_meta.get_recap(session_id)
    if existing and existing["turns"] >= len(messages):
        # Already summarized at this depth — a duplicate fire, not a new turn.
        return

    digest = _build_digest(messages)
    if not digest:
        return

    text = await _ask_model(digest)
    if not text:
        logger.debug("[RECAP] no usable text for session {}", session_id)
        return

    await session_meta.set_recap(session_id, text, len(messages))
    logger.info("[RECAP] {} -> {}", session_id, text)


async def _guarded(session_id: str, username: str, cwd: str | None) -> None:
    try:
        await _refresh(session_id, username, cwd)
    except Exception as e:
        # A recap is a nicety. Nothing here may surface to the caller, which by
        # now has already finished streaming the turn.
        logger.debug("[RECAP] refresh failed for {}: {}", session_id, e)


def spawn(session_id: str | None, username: str | None, cwd: str | None = None) -> None:
    """Kick off a recap for a just-finished turn and return immediately.

    Safe to call unconditionally: the toggle, the too-short guard and the
    already-summarized guard all live inside the task.
    """
    if not session_id or not username:
        return
    task = asyncio.create_task(_guarded(session_id, username, cwd))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
