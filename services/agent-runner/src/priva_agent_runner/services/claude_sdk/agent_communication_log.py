"""Durable receipt log for main/sub-agent message deliveries.

Claude's transcript does not consistently persist peer messages injected while
an agent is already running.  The live SDK stream still emits those deliveries
as sidechain ``UserMessage`` frames, so record that authoritative receive event
in a small JSONL sidecar instead of inferring receipt from ``SendMessage``.

The sidecar lives inside the session's existing companion directory:

    <project>/<session_id>/agent-communications.jsonl

It never modifies the CLI-owned transcript and is removed together with the
session companion directory by retention cleanup.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk._internal.sessions import _canonicalize_path, _get_project_dir

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

_SIDECAR_FILENAME = "agent-communications.jsonl"
_AGENT_MESSAGE_RE = re.compile(
    r"<agent-message\b([^>]*)>([\s\S]*?)</agent-message>",
    re.IGNORECASE,
)
_AGENT_MESSAGE_FROM_RE = re.compile(
    r"\bfrom\s*=\s*(?:\"([^\"]*)\"|'([^']*)')",
    re.IGNORECASE,
)
_COORDINATOR_PREFIX = "The coordinator sent a message while you were working:\n"
_COORDINATOR_SUFFIX = "\n\nAddress this before completing your current task."
_append_lock = threading.Lock()


def _sidecar_path(cwd: str, session_id: str) -> Path:
    project_dir = _get_project_dir(_canonicalize_path(cwd))
    return project_dir / session_id / _SIDECAR_FILENAME


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            value = block.get("text") or block.get("content")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def parse_delivery_content(content: object) -> dict[str, str | None] | None:
    """Extract only the real delivered body from an SDK policy envelope."""
    text = _content_text(content)
    if not text:
        return None

    peer = _AGENT_MESSAGE_RE.search(text)
    if peer:
        body = peer.group(2).strip()
        if not body:
            return None
        sender = _AGENT_MESSAGE_FROM_RE.search(peer.group(1))
        sender_name = (sender.group(1) or sender.group(2)).strip() if sender else None
        return {
            "source": "peer",
            "body": body,
            "sender_name": sender_name or None,
            "sender_agent_id": None,
        }

    prefix_at = text.find(_COORDINATOR_PREFIX)
    if prefix_at < 0:
        return None
    body = text[prefix_at + len(_COORDINATOR_PREFIX):]
    suffix_at = body.rfind(_COORDINATOR_SUFFIX)
    if suffix_at >= 0:
        body = body[:suffix_at]
    body = body.strip()
    if not body:
        return None
    return {
        "source": "main",
        "body": body,
        "sender_name": "main",
        "sender_agent_id": "main",
    }


def record_stream_delivery(
    cwd: str,
    session_id: str | None,
    event_type: str,
    data: dict[str, Any] | None,
    *,
    received_at_ms: int | None = None,
) -> dict[str, Any] | None:
    """Persist an authoritative sidechain receive event from the live stream."""
    if event_type != "tool_result" or not session_id or not isinstance(data, dict):
        return None
    parent_tool_use_id = data.get("parent_tool_use_id")
    if not isinstance(parent_tool_use_id, str) or not parent_tool_use_id:
        return None
    delivery = parse_delivery_content(data.get("content"))
    if not delivery:
        return None

    now_ns = time.time_ns()
    timestamp = received_at_ms if received_at_ms is not None else now_ns // 1_000_000
    event_id = data.get("uuid")
    if not isinstance(event_id, str) or not event_id:
        event_id = f"agent-delivery-{uuid.uuid4()}"
    row = {
        "version": 1,
        "event_id": event_id,
        "parent_tool_use_id": parent_tool_use_id,
        "source": delivery["source"],
        "body": delivery["body"],
        "sender_name": delivery["sender_name"],
        "sender_agent_id": delivery["sender_agent_id"],
        "received_at": int(timestamp),
        # Microseconds remain within JavaScript's safe integer range and give
        # equal-millisecond events a deterministic replay order.
        "sequence": now_ns // 1_000,
    }

    path = _sidecar_path(cwd, session_id)
    payload = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _append_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
    return row


def read_stream_deliveries(cwd: str, session_id: str) -> list[dict[str, Any]]:
    """Read normalized, event-id-deduplicated delivery rows in append order."""
    path = _sidecar_path(cwd, session_id)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    deliveries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Malformed agent communication sidecar row at %s:%d", path, line_number + 1)
            continue
        if not isinstance(row, dict):
            continue
        event_id = row.get("event_id")
        parent_tool_use_id = row.get("parent_tool_use_id")
        body = row.get("body")
        received_at = row.get("received_at")
        if (
            not isinstance(event_id, str)
            or not event_id
            or event_id in seen
            or not isinstance(parent_tool_use_id, str)
            or not parent_tool_use_id
            or not isinstance(body, str)
            or not body.strip()
            or not isinstance(received_at, int)
        ):
            continue
        seen.add(event_id)
        deliveries.append({
            **row,
            "body": body.strip(),
            "sequence": row.get("sequence") if isinstance(row.get("sequence"), int) else line_number,
        })
    return deliveries


def delete_stream_deliveries(cwd: str, session_id: str) -> None:
    """Remove the receipt sidecar without touching CLI-owned transcripts."""
    path = _sidecar_path(cwd, session_id)
    try:
        path.unlink()
    except OSError:
        return
    try:
        path.parent.rmdir()
    except OSError:
        pass
