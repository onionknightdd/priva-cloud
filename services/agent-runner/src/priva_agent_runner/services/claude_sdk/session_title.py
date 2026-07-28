"""Ask the CLI to name a new session from its opening prompt.

The bundled CLI can generate a session title, but only when the client asks for
it: the SDK never fires the request on its own, so SDK-driven sessions end up
displaying the raw first prompt instead of a title. See
https://github.com/anthropics/claude-agent-sdk-python/issues/854.

`generate_session_title` is an undocumented control-protocol subtype reached
through a private `_send_control_request`, so every failure path here is
swallowed: a title is a nicety, never a reason to fail a run. Note the CLI
itself is best-effort too — it validates the model's answer against a
``{title: string}`` schema and, on a parse failure, replies *successfully* with
a null title and writes nothing. Small local models miss that schema fairly
often, so a null answer is expected, not an error.
"""

from __future__ import annotations

import asyncio
from typing import Any

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

# The CLI answers only after its own model call returns. Generous enough for a
# one-line title, short enough that a wedged CLI can't hold the request open.
_REQUEST_TIMEOUT_SEC = 30.0
# How long the turn may wait for a still-running title at teardown. The request
# was fired at the top of the turn, so it has normally long since returned.
_SETTLE_BUDGET_SEC = 5.0
# Only the opening of the prompt carries naming signal; keep the request small.
_MAX_DESCRIPTION_CHARS = 2000


async def _generate(client: Any, description: str) -> None:
    query = getattr(client, "_query", None)
    send = getattr(query, "_send_control_request", None)
    if send is None:
        # Either the client isn't connected yet or an SDK upgrade moved the
        # private control channel. Nothing to do; the session keeps its
        # first-prompt fallback.
        return

    try:
        response = await send(
            {
                "subtype": "generate_session_title",
                "description": description,
                # Without this the CLI returns the title but never appends the
                # ai-title record, so nothing survives the run.
                "persist": True,
            },
            _REQUEST_TIMEOUT_SEC,
        )
    except Exception as e:
        logger.debug("[TITLE] generate_session_title request failed: {}", e)
        return

    title = (response or {}).get("title")
    if title:
        logger.info("[TITLE] named session: {}", title)
    else:
        logger.debug("[TITLE] CLI returned no title (model output failed its schema)")


def spawn(client: Any, prompt: str) -> asyncio.Task[None] | None:
    """Start naming the session alongside the turn it belongs to.

    Runs concurrently with the assistant response so it adds no latency of its
    own. Returns the task so the caller can settle it before the CLI is torn
    down, or None when there is nothing to name.
    """
    description = (prompt or "").strip()
    if not description:
        return None
    return asyncio.create_task(_generate(client, description[:_MAX_DESCRIPTION_CHARS]))


async def settle(task: asyncio.Task[None] | None) -> None:
    """Give a spawned title task a moment to land before the client closes.

    Tearing the CLI down mid-request would drop the title, so wait — briefly.
    Giving up is harmless: an unnamed session just shows its first prompt.
    """
    if task is None:
        return
    try:
        await asyncio.wait_for(task, timeout=_SETTLE_BUDGET_SEC)
    except asyncio.TimeoutError:
        # wait_for already cancelled it.
        logger.debug("[TITLE] title still pending at teardown; dropped")
    except Exception:
        # _generate swallows its own failures; this is belt and braces.
        pass
