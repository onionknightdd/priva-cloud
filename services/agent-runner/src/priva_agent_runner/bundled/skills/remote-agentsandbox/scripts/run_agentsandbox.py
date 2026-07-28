#!/usr/bin/env python3
"""run_agentsandbox.py — 以编程方式调用云端 agent sandbox（网关 SSE 流式接口）。

用法:
    AGENT_SANDBOX_GATEWAY_URL=<网关域名> python3 run_agentsandbox.py --prompt "任务描述" \
        [--session-id "上一轮的session_id"] \
        [--verbose] [--insecure]

网关地址没有内置默认值：首次调用时通过 AGENT_SANDBOX_GATEWAY_URL 提供网关域名，
脚本会把规范化后的地址写入 ~/.agentsandbox-gateway/session.json 的 gateway_url
字段，之后的调用不必再带该环境变量。两者都缺失时以退出码 1 失败并给出指引。

网关使用自签名或内部 CA 证书时，默认的 TLS 验证会让调用以退出码 2 失败（脚本会
指出这是证书问题并给出修复命令）。加 --insecure（或置 AGENT_SANDBOX_GATEWAY_INSECURE=1）
跳过验证，该选择同样持久化进 session.json；跳过时每次调用都会在 stderr 打印警告。

退出码:
    0 — 成功（result 事件的 data JSON 写到 stdout）
    1 — 参数错误、网关地址未配置，或 ~/.agentsandbox-gateway/auth 文件缺失/为空
    2 — 网络错误（连接、超时、SSE 读取中断）
    3 — API 返回非 2xx，或流结束未收到 result，或 stream_error
    4 — 并发冲突：同一 session_id 已有进行中的调用（fail-fast）
"""

import argparse
import atexit
import datetime
import fcntl
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO

GATEWAY_ENV_VAR = "AGENT_SANDBOX_GATEWAY_URL"
INSECURE_ENV_VAR = "AGENT_SANDBOX_GATEWAY_INSECURE"
# The runtime's streaming contract lives at a fixed path under the gateway; the
# operator only ever supplies the gateway domain.
#
# cp-proxy, not /api/sandbox: the latter rides agentgateway's GIE InferencePool,
# whose EPP ext_proc is hardcoded to buffer every response body and cuts it at
# ~8KB — an event stream included, so `result` never arrives on a real run. The
# cp-proxy lane reaches the same runtime endpoint through the control-panel,
# which has no ext_proc on its path (ADR 0003).
API_PATH = "/api/cp-proxy/agent/run/stream"
# Endpoints a persisted gateway_url may already carry, stripped before the
# canonical path is appended so an older saved value migrates itself.
_KNOWN_API_PATHS = (API_PATH, "/api/sandbox/agent/run/stream")
TIMEOUT_SECONDS = 300

# Per-user state (token, persisted gateway_url, session + verbose logs) under the
# home directory — shared across every working directory. Deliberately not derived
# from the skill name — renaming the skill must not orphan the user's token and
# session state.
STATE_DIR = Path.home() / ".agentsandbox-gateway"
AUTH_FILE = STATE_DIR / "auth"
SESSION_FILE = STATE_DIR / "session.json"


def load_bearer_token() -> str:
    if not AUTH_FILE.is_file():
        sys.stderr.write(
            f"错误：未找到 auth 文件 {AUTH_FILE}\n"
            f"请向用户索取云端 agent sandbox 的 Bearer token，并以明文单行形式写入该文件。\n"
        )
        sys.exit(1)
    token = AUTH_FILE.read_text(encoding="utf-8").strip()
    if not token:
        sys.stderr.write(f"错误：auth 文件 {AUTH_FILE} 为空\n")
        sys.exit(1)
    return token


def _read_session_state() -> dict:
    """Return session.json as a dict, or {} when absent/unparsable."""
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_gateway_url(raw: str) -> str:
    """Turn an operator-supplied gateway domain into the full endpoint URL.

    Accepts a bare domain (``agent.example.com``), an origin
    (``https://agent.example.com``), a base path, or an already-complete
    endpoint URL — so a caller who pastes the full URL isn't punished. A URL
    ending in a superseded endpoint is rewritten to the current one.
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = f"https://{url}"
    for path in _KNOWN_API_PATHS:
        if url.endswith(path):
            url = url[: -len(path)]
            break
    return url + API_PATH


def _persist_state(**updates) -> None:
    """Merge keys into session.json, preserving every other key."""
    state = _read_session_state()
    if all(state.get(k) == v for k, v in updates.items()):
        return
    state.update(updates)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as e:
        sys.stderr.write(f"警告：无法写入 {SESSION_FILE} — {e}\n")


def resolve_api_url() -> str:
    """Resolve the gateway endpoint: env var first, then the persisted value.

    There is deliberately no built-in default — a wrong one silently points the
    call at the wrong cluster. Supplying the env var once persists it, so later
    calls need no environment at all.
    """
    from_env = os.environ.get(GATEWAY_ENV_VAR, "").strip()
    url = _normalize_gateway_url(from_env or _read_session_state().get("gateway_url") or "")
    if not url:
        sys.stderr.write(
            f"错误：未配置云端 agent sandbox 的网关地址。\n"
            f"请向用户索取网关域名，然后带上环境变量重跑一次本命令（只需一次，"
            f"脚本会把它持久化到 {SESSION_FILE} 的 gateway_url 字段）：\n"
            f'  {GATEWAY_ENV_VAR}="agent.example.com" python3 <skill-path>/scripts/run_agentsandbox.py --prompt "..."\n'
        )
        sys.exit(1)
    # Unconditional: this also rewrites a stored URL whose endpoint has been
    # superseded. _persist_state no-ops when nothing changed.
    _persist_state(gateway_url=url)
    return url


def resolve_insecure(flag: bool) -> bool:
    """Whether to skip TLS verification: --insecure, env var, then persisted.

    A private cluster commonly fronts the gateway with a self-signed or
    internal-CA certificate, and the default verify turns that into an opaque
    "network error". Skipping is opt-in and never inferred — an unverified
    connection cannot tell the real gateway from something impersonating it,
    and this request carries a bearer token.
    """
    from_env = os.environ.get(INSECURE_ENV_VAR, "").strip().lower()
    if flag or from_env in ("1", "true", "yes", "on"):
        _persist_state(insecure=True)
        return True
    if from_env in ("0", "false", "no", "off"):
        _persist_state(insecure=False)
        return False
    return bool(_read_session_state().get("insecure"))


def build_ssl_context(url: str, insecure: bool):
    """None for ordinary verified TLS; a verification-disabled context otherwise."""
    if not insecure or not url.lower().startswith("https://"):
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # must precede CERT_NONE, else ValueError
    ctx.verify_mode = ssl.CERT_NONE
    # Loud every time: persisted state must never make this silent.
    sys.stderr.write("警告：已按要求跳过 TLS 证书验证，连接不防中间人攻击。\n")
    return ctx


class _StreamTimeout(Exception):
    """The call outlived TIMEOUT_SECONDS of wall-clock time."""


def iter_sse_events(resp, deadline: float | None = None):
    """Parse an SSE stream from a urlopen response, yielding (event, data_str).

    ``deadline`` is a ``time.monotonic()`` stamp; passing it raises
    ``_StreamTimeout`` once the wall clock runs past it. The check has to live
    here, on every raw line, rather than in the caller's per-event loop: the
    gateway's heartbeat is an SSE *comment* (``: keepalive``) that yields no
    event at all, and each heartbeat byte also resets urlopen's per-socket
    timeout — so a stalled run would otherwise stream heartbeats forever
    without either timeout ever firing.
    """
    event: str | None = None
    data_lines: list[str] = []
    for raw in resp:
        if deadline is not None and time.monotonic() > deadline:
            raise _StreamTimeout
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if event is not None or data_lines:
                yield (event or "message"), "\n".join(data_lines)
            event = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
    if event is not None or data_lines:
        yield (event or "message"), "\n".join(data_lines)


def acquire_session_lock(session_id: str) -> tuple["IO", Path]:
    """Take a non-blocking exclusive flock on <session_id>.lock.

    Concurrent calls with the same session_id corrupt the remote agent's
    on-disk conversation state. We fail-fast (exit 4) so the caller can
    retry serially.

    Returns (handle, lock_path). The caller must keep handle open for the
    duration of the API call. On exit the lock is released and the file
    is deleted automatically via atexit.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = STATE_DIR / f"{session_id}.lock"
    handle = lock_path.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        sys.stderr.write(
            f"错误：session {session_id} 已有进行中的调用（lock: {lock_path}）。\n"
            f"同一 session_id 的并发调用会损坏远端 agent 状态，"
            f"请等上一次调用返回后再试。\n"
        )
        sys.exit(4)
    return handle, lock_path


def _release_session_lock(handle: "IO", lock_path: Path) -> None:
    try:
        handle.close()
    except OSError:
        pass
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _open_verbose_log(session_id: str) -> tuple[IO, Path, bool]:
    """Open the verbose log file. Returns (handle, path, is_temp).

    When session_id is known upfront, write directly to <session_id>.jsonl
    (append mode — continuing sessions extend the same file). Otherwise
    write to a per-PID temp file that gets renamed once we learn the
    session_id from the result event.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if session_id:
        path = STATE_DIR / f"{session_id}.jsonl"
        return path.open("a", encoding="utf-8"), path, False
    temp = STATE_DIR / f"_pending.{os.getpid()}.jsonl"
    return temp.open("a", encoding="utf-8"), temp, True


def _write_event(handle: IO, event: str, data) -> None:
    record = {"ts": _now_iso(), "event": event, "data": data}
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="云端 agent sandbox 调用器（网关 SSE 接口）")
    parser.add_argument("--prompt", required=True, help="要交给远端 agent 的任务描述（对应 priva message 字段）")
    parser.add_argument("--session-id", default="", help="上一轮会话 ID，留空表示新会话")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="把所有中间 SSE 事件写入 ~/.agentsandbox-gateway/<session_id>.jsonl",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="跳过网关 TLS 证书验证（自签名/内部 CA 证书时使用），并持久化该选择",
    )
    args = parser.parse_args()

    api_url = resolve_api_url()
    ssl_context = build_ssl_context(api_url, resolve_insecure(args.insecure))
    token = load_bearer_token()

    # Fail-fast concurrency guard: only continuation calls (with an
    # explicit session_id) need a lock — new sessions are independent.
    # Lock is held for the entire process lifetime and released on
    # process exit (atexit + OS-level fd cleanup).
    if args.session_id:
        lock_handle, lock_path = acquire_session_lock(args.session_id)
        atexit.register(_release_session_lock, lock_handle, lock_path)

    body: dict = {"message": args.prompt}
    if args.session_id:
        body["session_id"] = args.session_id

    req = urllib.request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=ssl_context)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"错误：API 返回 HTTP {e.code}\n")
        sys.stderr.write(e.read().decode("utf-8", errors="replace"))
        return 3
    except urllib.error.URLError as e:
        sys.stderr.write(f"错误：网络异常 — {e.reason}\n")
        # A self-signed / internal-CA gateway lands here as an opaque failure;
        # name the actual cause and the one-line fix instead.
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            sys.stderr.write(
                "该错误是网关的 TLS 证书未通过验证（自签名或内部 CA 签发）。\n"
                "先与用户确认该网关可信，确认后加 --insecure 重跑一次即可跳过验证"
                f"（选择会持久化到 {SESSION_FILE}，无需每次都加）：\n"
                '  python3 <skill-path>/scripts/run_agentsandbox.py --insecure --prompt "..."\n'
            )
        return 2
    except (TimeoutError, OSError) as e:
        sys.stderr.write(f"错误：连接异常 — {type(e).__name__}: {e}\n")
        return 2

    log_handle: IO | None = None
    log_path: Path | None = None
    log_is_temp = False
    if args.verbose:
        try:
            log_handle, log_path, log_is_temp = _open_verbose_log(args.session_id)
        except OSError as e:
            sys.stderr.write(f"错误：无法打开 verbose 日志文件 — {e}\n")
            return 1

    result_payload: dict | None = None
    stream_error: dict | None = None
    timed_out = False
    # Real wall-clock deadline: urlopen(timeout=) is only a per-socket-op
    # timeout, so a stream that keeps heartbeating could otherwise run
    # unbounded. iter_sse_events enforces it per raw line — see its docstring
    # for why a per-event check here would never fire.
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        with resp:
            for event, data_str in iter_sse_events(resp, deadline):
                if not data_str:
                    continue
                try:
                    payload = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if log_handle is not None:
                    _write_event(log_handle, event, payload)
                if event == "result":
                    result_payload = payload
                    break
                if event in ("stream_error", "retry_exhausted"):
                    stream_error = {"event": event, **payload}
                    break
    except _StreamTimeout:
        timed_out = True
    except (TimeoutError, OSError) as e:
        sys.stderr.write(f"错误：SSE 流读取异常 — {type(e).__name__}: {e}\n")
        if log_handle is not None:
            log_handle.close()
        return 2
    finally:
        if log_handle is not None:
            log_handle.close()

    # If verbose+new-session, rename temp log file to final <session_id>.jsonl
    if log_is_temp and log_path is not None:
        final_sid = (result_payload or {}).get("session_id") if result_payload else None
        if final_sid:
            final_path = STATE_DIR / f"{final_sid}.jsonl"
            try:
                if final_path.exists():
                    with final_path.open("ab") as dst, log_path.open("rb") as src:
                        dst.write(src.read())
                    log_path.unlink()
                else:
                    log_path.replace(final_path)
            except OSError as e:
                sys.stderr.write(
                    f"警告：无法把临时日志 {log_path} 重命名为 {final_path} — {e}\n"
                )
        else:
            sys.stderr.write(
                f"警告：verbose 临时日志保留在 {log_path}（未获取到 session_id）\n"
            )

    if timed_out and result_payload is None:
        sys.stderr.write(
            f"错误：整体调用超过 {TIMEOUT_SECONDS} 秒仍未收到 result 事件，已中止。\n"
            f"建议把任务拆分为更小的步骤后重试。\n"
        )
        return 2

    if result_payload is None:
        if stream_error is not None:
            sys.stderr.write("错误：远端 stream 返回错误：\n")
            sys.stderr.write(json.dumps(stream_error, ensure_ascii=False))
            return 3
        sys.stderr.write("错误：SSE 流结束但未收到 result 事件\n")
        return 3

    sys.stdout.write(json.dumps(result_payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
