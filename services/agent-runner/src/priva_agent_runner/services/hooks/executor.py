"""Dry-run hook test runner.

Executes a single hook handler with sample JSON input and captures exit code,
stdout, stderr, and duration. Runs under the SAME constructed environment as
real fires (build_hook_env) so a dry-run that depends on a denied secret fails
here too, not just in production.
"""

from __future__ import annotations

import asyncio
import json
import time

from priva_common.logging import get_app_logger
from priva_common.models.hooks import HookHandler, HookTestResponse

from .env import build_hook_env

logger = get_app_logger(__name__)


async def test_hook(
    event_type: str,
    handler: HookHandler,
    input_json: dict,
    cwd: str | None = None,
) -> HookTestResponse:
    """Execute a hook handler in a subprocess with sample input.

    Only ``type=command`` is supported for dry-run testing.  HTTP, prompt,
    and agent hooks are validated structurally but not executed.
    """
    if handler.type != "command":
        return HookTestResponse(
            exit_code=-1,
            stdout="",
            stderr=f"Dry-run is only supported for command hooks (got type={handler.type!r})",
            duration_ms=0,
        )

    if not handler.command:
        return HookTestResponse(
            exit_code=-1,
            stdout="",
            stderr="No command specified",
            duration_ms=0,
        )

    env = build_hook_env(
        handler.allowedEnvVars or [],
        extra={"CLAUDE_HOOK_EVENT_NAME": event_type, "CLAUDE_PROJECT_DIR": cwd or ""},
    )

    stdin_data = json.dumps(input_json).encode()
    timeout = handler.timeout or 30

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            handler.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_data),
            timeout=timeout,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        return HookTestResponse(
            exit_code=proc.returncode or 0,
            stdout=stdout.decode(errors="replace")[:10_000],
            stderr=stderr.decode(errors="replace")[:10_000],
            duration_ms=elapsed_ms,
        )
    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return HookTestResponse(
            exit_code=-1,
            stdout="",
            stderr=f"Hook timed out after {timeout}s",
            duration_ms=elapsed_ms,
        )
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return HookTestResponse(
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            duration_ms=elapsed_ms,
        )
