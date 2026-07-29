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



DEFAULT_HOOK_TIMEOUT = 30
MAX_HOOK_TIMEOUT = 300            # mirrors the ceiling on HookHandler.timeout
MAX_HOOK_OUTPUT_BYTES = 256 * 1024


async def _communicate_bounded(proc, stdin_data: bytes, limit: int) -> tuple[bytes, bytes]:
    """asyncio.subprocess.communicate() with a cap on what is kept.

    The stock helper accumulates everything the child writes. The child here is
    arbitrary shell from the request body, so that is an unbounded-memory
    primitive; read at most `limit` from each pipe and drop the rest. The child
    is killed once both pipes are done so a writer that ignores a closed stdout
    cannot linger.
    """

    def _kill() -> None:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def _drain(stream) -> bytes:
        if stream is None:
            return b""
        out = bytearray()
        while len(out) <= limit:
            chunk = await stream.read(8192)
            if not chunk:
                break
            out.extend(chunk)
        if len(out) > limit:
            # Killing here is what unblocks the OTHER pipe: a child that never
            # writes to stderr never closes it either, so a sibling drain would
            # wait for EOF forever once this one stopped reading.
            _kill()
        return bytes(out[: limit + 1])

    async def _feed() -> None:
        if proc.stdin is None:
            return
        try:
            proc.stdin.write(stdin_data)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    _, stdout, stderr = await asyncio.gather(
        _feed(), _drain(proc.stdout), _drain(proc.stderr))
    _kill()
    await proc.wait()
    return stdout, stderr

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
    # Clamp: `timeout` arrives on the request body, so an unbounded value is a
    # free "hold a subprocess open forever" primitive.
    timeout = min(handler.timeout or DEFAULT_HOOK_TIMEOUT, MAX_HOOK_TIMEOUT)

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
        # communicate() buffers all of stdout+stderr with no ceiling, and the
        # command is arbitrary shell from the request — `cat /dev/zero` is an
        # unbounded-memory primitive. Read bounded amounts instead.
        stdout, stderr = await asyncio.wait_for(
            _communicate_bounded(proc, stdin_data, MAX_HOOK_OUTPUT_BYTES),
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
