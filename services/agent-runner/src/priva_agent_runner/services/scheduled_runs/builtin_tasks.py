"""
Built-in task execution handlers for non-agent job types (http_call /
user_script), ported from the monolith ``priva/api/services/scheduler/
builtin_tasks.py`` (design §7). They run under the pod's own identity —
uid 10001, pod cwd, config timeouts — and open no agent session.

Each handler follows the interface:
    async def execute_*(config, username, cwd, emit, cancelled) -> dict
    Returns a legacy display ``result`` plus typed fields used by callbacks.

Event types emitted per job type:

  http_call:
    http_request  — {method, url, headers}
    http_response — {status_code, reason, body, elapsed_ms, is_error}
    http_error    — {error, elapsed_ms}

  user_script:
    script_start  — {language, source, file_path}
    script_output — {line}
    script_exit   — {exit_code, elapsed_ms, timed_out}
    script_error  — {error, elapsed_ms}
"""
from __future__ import annotations

import asyncio
import codecs
import os
import signal
import tempfile
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from priva_common.models.scheduler import HttpCallConfig, UserScriptConfig

_CALLBACK_CAPTURE_CHARS = 4001
_SCRIPT_DISPLAY_CHARS = 2000
_SCRIPT_DRAIN_SECONDS = 2.0


class _ScriptOutput:
    """Bound one stream while retaining the useful end for its callback.

    The connector accepts one character beyond its 4000-char presentation cap
    so it can reliably mark the value as truncated.  A short head is retained
    separately for the run-history summary/error message.
    """

    def __init__(self) -> None:
        self.head = ""
        self.tail = ""
        self.seen = False

    def append(self, text: str) -> None:
        self.seen = True
        if len(self.head) < _SCRIPT_DISPLAY_CHARS:
            remaining = _SCRIPT_DISPLAY_CHARS - len(self.head)
            self.head += text[:remaining]
        if len(text) >= _CALLBACK_CAPTURE_CHARS:
            self.tail = text[-_CALLBACK_CAPTURE_CHARS:]
        else:
            self.tail = (self.tail + text)[-_CALLBACK_CAPTURE_CHARS:]


async def execute_http_call(
    config: HttpCallConfig,
    username: str,
    cwd: str,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
    cancelled: asyncio.Event | None = None,
) -> dict:
    """Execute an HTTP call job."""
    start = time.monotonic()

    await emit("http_request", {
        "method": config.method,
        "url": config.url,
        "headers": config.headers or {},
    })

    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.request(
                method=config.method,
                url=config.url,
                headers=config.headers or {},
                content=config.body if config.body else None,
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        is_error = response.status_code >= 400
        # One character past the connector's display cap lets it render an
        # explicit truncation marker without an unbounded RunRecord payload.
        body_text = response.text[:_CALLBACK_CAPTURE_CHARS]

        await emit("http_response", {
            "status_code": response.status_code,
            "reason": response.reason_phrase,
            "body": body_text,
            "elapsed_ms": elapsed_ms,
            "is_error": is_error,
        })

        result_text = f"HTTP {response.status_code} {response.reason_phrase}\n\n{body_text}"
        return {
            "is_error": is_error,
            "result": result_text,
            "duration_ms": elapsed_ms,
            "method": config.method,
            "url": config.url,
            "status_code": response.status_code,
            "reason": response.reason_phrase,
            "body": body_text,
            "error": None,
        }

    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        msg = f"HTTP request timed out after {config.timeout_seconds}s"
        await emit("http_error", {"error": msg, "elapsed_ms": elapsed_ms})
        return _http_error_result(config, msg, elapsed_ms)

    except httpx.ConnectError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        msg = f"Connection error: {e}"
        await emit("http_error", {"error": msg, "elapsed_ms": elapsed_ms})
        return _http_error_result(config, msg, elapsed_ms)

    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        msg = f"HTTP call failed: {e}"
        await emit("http_error", {"error": msg, "elapsed_ms": elapsed_ms})
        return _http_error_result(config, msg, elapsed_ms)


def _http_error_result(config: HttpCallConfig, message: str, elapsed_ms: int) -> dict:
    bounded = message[:_CALLBACK_CAPTURE_CHARS]
    return {
        "is_error": True,
        "result": message[:_SCRIPT_DISPLAY_CHARS],
        "duration_ms": elapsed_ms,
        "method": config.method,
        "url": config.url,
        "status_code": None,
        "reason": "",
        "body": "",
        "error": bounded,
    }


def _script_result(
    *,
    is_error: bool,
    result: str,
    duration_ms: int,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    timed_out: bool = False,
) -> dict:
    return {
        "is_error": is_error,
        "result": result,
        "duration_ms": duration_ms,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "timed_out": timed_out,
    }


def _script_setup_error(message: str, duration_ms: int = 0) -> dict:
    # Setup/runner errors have no child-process stderr stream.  Put the
    # diagnostic in the typed stderr lane so a script callback still carries
    # the reason it failed.
    return _script_result(
        is_error=True,
        result=message[:_SCRIPT_DISPLAY_CHARS],
        duration_ms=duration_ms,
        stderr=message[-_CALLBACK_CAPTURE_CHARS:],
    )


async def execute_user_script(
    config: UserScriptConfig,
    username: str,
    cwd: str,
    emit: Callable[[str, dict[str, Any]], Awaitable[None]],
    cancelled: asyncio.Event | None = None,
) -> dict:
    """Execute a user script (python or shell)."""
    start = time.monotonic()
    tmp_file = None

    try:
        # Determine script path
        if config.source == "file":
            if not config.file_path:
                return _script_setup_error("No file_path specified")

            # Expand ~ and resolve path
            expanded = os.path.expanduser(config.file_path)
            script_path = expanded if os.path.isabs(expanded) else os.path.join(cwd, expanded)
            script_path = os.path.realpath(script_path)

            if not os.path.isfile(script_path):
                return _script_setup_error(f"Script file not found: {config.file_path}")

            if not os.access(script_path, os.R_OK):
                return _script_setup_error(f"Script file not readable: {config.file_path}")

        elif config.source == "inline":
            if not config.script:
                return _script_setup_error("No inline script content")

            suffix = ".py" if config.language == "python" else ".sh"
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=cwd, prefix=".scheduler_")
            tmp_file = tmp_path
            with os.fdopen(tmp_fd, "w") as f:
                f.write(config.script)
            script_path = tmp_path
        else:
            return _script_setup_error(f"Unknown source: {config.source}")

        # Choose interpreter
        interpreter = "python3" if config.language == "python" else "/bin/bash"

        await emit("script_start", {
            "language": config.language,
            "source": config.source,
            "file_path": config.file_path or "(inline)",
            "command": f"{interpreter} {script_path}",
            "cwd": cwd,
        })

        # Execute
        proc = await asyncio.create_subprocess_exec(
            interpreter, script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            # A timeout/cancel must terminate descendants too. Otherwise a
            # forked child can retain the stdout/stderr pipe and make the
            # callback drain wait forever after the direct child is killed.
            start_new_session=True,
        )

        stdout_output = _ScriptOutput()
        stderr_output = _ScriptOutput()
        stdout_task = None
        stderr_task = None

        try:
            async def read_stream(stream, stream_name, sink):
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

                async def record(decoded: str) -> None:
                    if not decoded:
                        return
                    sink.append(decoded)
                    # A chunk can itself be large; bound the replay event just
                    # like callback data. ``line`` is the historical wire key.
                    text = decoded.rstrip("\n")[:_CALLBACK_CAPTURE_CHARS]
                    await emit("script_output", {"line": text, "stream": stream_name})

                while True:
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    # Chunked reads tolerate arbitrarily long output without a
                    # newline (StreamReader.readline has a ~64 KiB limit).
                    await record(decoder.decode(chunk))
                await record(decoder.decode(b"", final=True))

            stdout_task = asyncio.create_task(
                read_stream(proc.stdout, "stdout", stdout_output)
            )
            stderr_task = asyncio.create_task(
                read_stream(proc.stderr, "stderr", stderr_output)
            )

            async def drain_output() -> tuple[str, str]:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(stdout_task, stderr_task),
                        timeout=_SCRIPT_DRAIN_SECONDS,
                    )
                except asyncio.TimeoutError:
                    # A child that deliberately escapes the process group can
                    # still retain an inherited pipe. Output is best-effort
                    # after the child process has reached a terminal state.
                    for task in (stdout_task, stderr_task):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(
                        stdout_task, stderr_task, return_exceptions=True,
                    )
                return stdout_output.tail, stderr_output.tail

            async def kill_process_group() -> None:
                if proc.returncode is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await proc.wait()

            # Wait for process with timeout, also check cancellation
            timed_out = False
            cancelled_by_user = False
            if cancelled is not None:
                # Race process completion against both the configured wall clock
                # and explicit abort.  Do not wrap proc.wait() in wait_for: on a
                # timeout we still need a clean kill/wait/drain sequence.
                wait_task = asyncio.create_task(proc.wait())
                cancel_task = asyncio.create_task(cancelled.wait())
                done, pending = await asyncio.wait(
                    [wait_task, cancel_task],
                    timeout=config.timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if wait_task in done:
                    pass
                elif cancel_task in done:
                    cancelled_by_user = True
                else:
                    timed_out = True
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            else:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=config.timeout_seconds)
                except asyncio.TimeoutError:
                    timed_out = True

            if cancelled_by_user:
                await kill_process_group()
                stdout_text, stderr_text = await drain_output()
                elapsed_ms = int((time.monotonic() - start) * 1000)
                await emit("script_exit", {
                    "exit_code": -1, "elapsed_ms": elapsed_ms, "timed_out": False,
                })
                return _script_result(
                    is_error=True,
                    result="Cancelled by user",
                    duration_ms=elapsed_ms,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=-1,
                )

            if timed_out:
                await kill_process_group()
                stdout_text, stderr_text = await drain_output()
                elapsed_ms = int((time.monotonic() - start) * 1000)
                await emit("script_exit", {"exit_code": -1, "elapsed_ms": elapsed_ms, "timed_out": True})
                msg = f"Script timed out after {config.timeout_seconds}s"
                return _script_result(
                    is_error=True,
                    result=msg,
                    duration_ms=elapsed_ms,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    exit_code=-1,
                    timed_out=True,
                )

            # Drain remaining output
            stdout_text, stderr_text = await drain_output()

            elapsed_ms = int((time.monotonic() - start) * 1000)
            # User ruling: any bytes written to stderr make the script run a
            # failure, even when the process exits zero.  A non-zero exit still
            # fails when stderr is empty.
            is_error = proc.returncode != 0 or stderr_output.seen

            await emit("script_exit", {
                "exit_code": proc.returncode,
                "elapsed_ms": elapsed_ms,
                "timed_out": False,
            })

            output_text = "".join((stdout_output.head, stderr_output.head))
            if proc.returncode != 0:
                result_text = f"Script exited with code {proc.returncode}\n\n{output_text}"
            elif stderr_text:
                result_text = f"Script wrote to stderr\n\n{output_text}"
            else:
                result_text = stdout_output.head or "(no output)"

            return _script_result(
                is_error=is_error,
                result=result_text,
                duration_ms=elapsed_ms,
                stdout=stdout_text,
                stderr=stderr_text,
                exit_code=proc.returncode,
            )

        except (asyncio.CancelledError, Exception):
            # Ensure subprocess is killed on any unexpected error or cancellation
            if proc.returncode is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    await proc.wait()
                except Exception:
                    pass
            raise

        finally:
            # Always clean up stream reader tasks
            for t in (stdout_task, stderr_task):
                if t is not None and not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

    except asyncio.CancelledError:
        raise
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        msg = f"Script execution failed: {e}"
        await emit("script_error", {"error": msg, "elapsed_ms": elapsed_ms})
        return _script_setup_error(msg, elapsed_ms)

    finally:
        if tmp_file and os.path.exists(tmp_file):
            try:
                os.unlink(tmp_file)
            except OSError:
                pass
