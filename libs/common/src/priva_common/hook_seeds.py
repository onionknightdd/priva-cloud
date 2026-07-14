"""Predefined hook-policy seeds — the legacy built-in hooks as data.

Single source of truth for the `hook_policy` predefined rows. data-spine seeds
these insert-if-absent at startup (ids = legacy builtin slugs so per-account
user_hook_prefs carry over); the control-panel imports them to compute the
"newer seed available" diff for edited rows.

Script bodies are STRICTLY stdlib-only (bash / python3 stdlib): they run as
subprocesses on the hook hot path where a `priva_common` import would cost
200-500 ms per fire. Hook input arrives as Claude Code hook JSON on stdin;
blocking decisions ride the documented JSON output protocol on stdout.

Upgrading a seed: change the body, bump `seed_version`, and append the OLD
body's sha256 to `previous_hashes` — the startup migration auto-updates rows
still carrying a known previous hash (unedited) and leaves admin-edited rows
untouched (the admin UI shows a diff banner instead).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# Claude Code hook events the Python agent-sdk dispatches (claude_agent_sdk
# HookEvent literal). Validation + UI grouping both key off this order.
SUPPORTED_HOOK_EVENTS: tuple[str, ...] = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "UserPromptSubmit",
    "Notification",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
)

HOOK_TYPES: tuple[str, ...] = ("command", "http", "mcp_tool")
INTERPRETERS: tuple[str, ...] = ("bash", "python3")

MAX_SCRIPT_BYTES = 64 * 1024
DEFAULT_COMMAND_TIMEOUT = 30
DEFAULT_HTTP_TIMEOUT = 5


def content_hash(script_body: str) -> str:
    return hashlib.sha256(script_body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HookSeed:
    id: str
    name: str
    description: str  # zh-only content, shown verbatim to users
    events: tuple[str, ...]
    matcher: str
    interpreter: str
    script_body: str
    timeout_seconds: int
    default_on: bool
    # rev-5: admin hooks are enforced-only (delivered natively via the managed
    # ConfigMap). ``enforced`` is the seed's default enforcement; ``default_on``
    # is retained for schema/back-compat but no longer drives a user opt-in tier.
    enforced: bool = False
    seed_version: int = 1
    previous_hashes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def hash(self) -> str:
        return content_hash(self.script_body)

    def known_hashes(self) -> tuple[str, ...]:
        return (self.hash, *self.previous_hashes)


# ── block-dangerous-bash ─────────────────────────────────────────────────────

_BLOCK_DANGEROUS_BASH = '''#!/usr/bin/env python3
# Priva hook: block destructive bash commands (PreToolUse / Bash).
# Patterns live HERE — editing this script edits the policy.
import json
import re
import sys

DANGEROUS_PATTERNS = [
    r"rm\\s+-[rR]f\\s+/\\*",      # rm -rf /* (root wildcard)
    r"rm\\s+-[rR]f\\s+/\\s",      # rm -rf / (root itself)
    r"rm\\s+-[rR]f\\s+/$",        # rm -rf / (at end of command)
    r"rm\\s+-f[rR]\\s+/\\*",      # rm -fr /* variant
    r"rm\\s+-f[rR]\\s+/\\s",      # rm -fr / variant
    r"mkfs\\.",                   # mkfs.ext4 etc.
    r"dd\\s+if=/dev/zero",        # dd zero-fill
    r"dd\\s+if=/dev/random",      # dd random-fill
    r">\\s*/dev/sd",              # write to raw disk
    r"chmod\\s+-R\\s+777\\s+/",   # chmod 777 from root
    r"chown\\s+-R\\s+.*\\s+/",    # chown from root
    r":\\(\\)\\s*\\{\\s*:\\|:&\\s*\\};:",  # fork bomb (legacy pattern had unescaped parens and never matched)
    r"mv\\s+/\\s+",               # move root
]


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str) or not command:
        return 0
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Blocked by safety hook: matches dangerous pattern '%s'" % pattern
                    ),
                }
            }))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

# ── audit-tool-use ───────────────────────────────────────────────────────────
# Appends entries byte-compatible with priva_common.audit_log.AuditEntry to the
# per-day JSONL in $PRIVA_AUDIT_DIR (the executor sets it to priva_home()). The
# counts sidecar self-heals on the next read, so the script never touches it.

_AUDIT_TOOL_USE = '''#!/usr/bin/env python3
# Priva hook: append each tool call to the Priva audit JSONL (Pre/PostToolUse).
import datetime
import fcntl
import json
import os
import sys
import uuid


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    base = os.environ.get("PRIVA_AUDIT_DIR", "")
    if not base:
        return 0
    now = datetime.datetime.now()
    entry = {
        "id": uuid.uuid4().hex,
        "timestamp": now.isoformat(),
        "actor": "system",
        "action": "hook.%s" % str(data.get("hook_event_name", "unknown")).lower(),
        "target": data.get("tool_name", "unknown"),
        "details": {
            "tool_use_id": data.get("tool_use_id") or "",
            "session_id": data.get("session_id", ""),
        },
    }
    path = os.path.join(base, ".priva.audit.%s.jsonl" % now.strftime("%Y-%m-%d"))
    try:
        os.makedirs(base, exist_ok=True)
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + "\\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

# ── lint-on-write ────────────────────────────────────────────────────────────

_LINT_ON_WRITE = '''#!/usr/bin/env bash
# Priva hook: run ruff --fix on the written/edited file (PostToolUse / Write|Edit).
set -u
input=$(cat)
file_path=$(printf '%s' "$input" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = d.get("tool_input") or {}
print(ti.get("file_path", "") if isinstance(ti, dict) else "")
' 2>/dev/null)
[ -z "${file_path}" ] && exit 0
command -v ruff >/dev/null 2>&1 || exit 0
ruff check --fix "${file_path}" >/dev/null 2>&1 || true
exit 0
'''

# ── require-permission-risky-tools ──────────────────────────────────────────
# Standalone port of priva_common.risky_matcher (same grammar, same semantics).
# v3: patterns are EMBEDDED in the script (like block-dangerous-bash) — no
# runtime context file, so the hook is self-contained wherever the CLI runs it.

_REQUIRE_PERMISSION_RISKY_TOOLS = '''#!/usr/bin/env python3
# Priva hook: pause for user confirmation on risky tool patterns (PreToolUse).
# Patterns live HERE — editing this script edits the policy.
# Grammar: Bash | Bash(rm:*) | Write(/etc/**) | WebFetch(domain:x) | mcp__a__b
import fnmatch
import json
import re
import sys
from urllib.parse import urlparse

RISKY_RULES = [
    "Bash(rm:*)",
    "Bash(sudo:*)",
    "Bash(chmod:*)",
    "Bash(chown:*)",
    "Bash(dd:*)",
    "Bash(mkfs:*)",
    "Bash(shutdown:*)",
    "Bash(reboot:*)",
    "Write(/etc/**)",
    "Edit(/etc/**)",
    "Write(**/.ssh/**)",
    "Edit(**/.ssh/**)",
    "mcp__*__delete_*",
]

PATH_TOOLS = {"Write", "Edit", "Read", "NotebookEdit", "MultiEdit"}
RULE_RE = re.compile(r"^(?P<tool>[A-Za-z_]\\w*|mcp__\\S+)(?:\\((?P<arg>.*)\\))?$")


def glob_to_regex(glob):
    out, i, n = [], 0, len(glob)
    while i < n:
        c = glob[i]
        if c == "*":
            if i + 1 < n and glob[i + 1] == "*":
                out.append(".*")
                i += 2
            else:
                out.append("[^/]*")
                i += 1
        elif c == "?":
            out.append(".")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def parse_rule(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.startswith("mcp__"):
        return {"raw": raw, "tool": s, "kind": "mcp_glob", "arg": None}
    m = RULE_RE.match(s)
    if not m:
        return None
    tool, arg = m.group("tool"), m.group("arg")
    if arg is None:
        return {"raw": raw, "tool": tool, "kind": "any", "arg": None}
    if tool == "Bash":
        prefix = arg[:-2] if arg.endswith(":*") else arg
        prefix = prefix.strip()
        if not prefix:
            return {"raw": raw, "tool": tool, "kind": "any", "arg": None}
        return {"raw": raw, "tool": tool, "kind": "bash_prefix", "arg": prefix}
    if tool in PATH_TOOLS:
        return {"raw": raw, "tool": tool, "kind": "path_glob", "arg": arg}
    if tool == "WebFetch":
        if arg.startswith("domain:"):
            return {"raw": raw, "tool": tool, "kind": "webfetch_domain",
                    "arg": arg[len("domain:"):].strip()}
        return None
    return {"raw": raw, "tool": tool, "kind": "exact", "arg": arg}


def matches_bash_prefix(command, prefix):
    cmd = command.lstrip()
    if not cmd.startswith(prefix):
        return False
    if len(prefix) == len(cmd):
        return True
    next_ch = cmd[len(prefix)]
    return next_ch.isspace() or next_ch in (";", "&", "|")


def rule_matches(rule, tool_name, tool_input):
    if not isinstance(tool_input, dict):
        tool_input = {}
    if rule["kind"] == "mcp_glob":
        return fnmatch.fnmatchcase(tool_name, rule["tool"])
    if rule["tool"] != tool_name:
        return False
    if rule["kind"] == "any":
        return True
    if rule["kind"] == "bash_prefix":
        command = tool_input.get("command") or ""
        return isinstance(command, str) and matches_bash_prefix(command, rule["arg"] or "")
    if rule["kind"] == "path_glob":
        file_path = tool_input.get("file_path") or ""
        if not isinstance(file_path, str) or not file_path:
            return False
        try:
            regex = glob_to_regex(rule["arg"] or "")
        except re.error:
            return False
        return bool(regex.match(file_path))
    if rule["kind"] == "webfetch_domain":
        url = tool_input.get("url") or ""
        if not isinstance(url, str) or not url:
            return False
        try:
            host = urlparse(url).hostname
        except Exception:
            return False
        target = rule["arg"] or ""
        return bool(host) and (host == target or host.endswith("." + target))
    if rule["kind"] == "exact":
        return any(isinstance(v, str) and v == rule["arg"] for v in tool_input.values())
    return False


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    if not tool_name:
        return 0
    for raw in RISKY_RULES:
        rule = parse_rule(raw)
        if rule and rule_matches(rule, tool_name, tool_input):
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        "匹配到高风险工具模式 '%s'。请再次确认 Agent 即将要执行的操作是否符合预期。" % raw
                    ),
                }
            }, ensure_ascii=False))
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


HOOK_SEEDS: tuple[HookSeed, ...] = (
    HookSeed(
        id="block-dangerous-bash",
        name="Block Dangerous Commands",
        description="在执行前拦截 rm -rf、mkfs、dd if=/dev/zero 等破坏性 bash 命令。",
        events=("PreToolUse",),
        matcher="Bash",
        interpreter="python3",
        script_body=_BLOCK_DANGEROUS_BASH,
        timeout_seconds=10,
        default_on=True,
        enforced=True,
        seed_version=2,
    ),
    HookSeed(
        id="audit-tool-use",
        name="Audit Tool Use",
        description="将每次工具调用写入 Priva 审计日志（JSONL），记录工具名、会话 ID 和事件类型，用于合规与调试。",
        events=("PreToolUse", "PostToolUse"),
        matcher="",
        interpreter="python3",
        script_body=_AUDIT_TOOL_USE,
        timeout_seconds=10,
        default_on=True,
        enforced=True,
        seed_version=2,
    ),
    HookSeed(
        id="lint-on-write",
        name="Lint on Write",
        description="在文件写入/编辑完成后运行 ruff 对修改的文件执行检查并自动修复。",
        events=("PostToolUse",),
        matcher="Write|Edit",
        interpreter="bash",
        script_body=_LINT_ON_WRITE,
        timeout_seconds=30,
        default_on=False,
    ),
    HookSeed(
        id="require-permission-risky-tools",
        name="Require Permission for Risky Tools",
        description="当工具调用匹配预定义的高风险工具模式（如 Bash(rm:*)、Write(/etc/**)）时，暂停智能体并请求用户确认。",
        events=("PreToolUse",),
        matcher="",
        interpreter="python3",
        script_body=_REQUIRE_PERMISSION_RISKY_TOOLS,
        timeout_seconds=10,
        default_on=True,
        enforced=True,
        seed_version=3,
        # v2 body read rules from $PRIVA_HOOK_DIR/risky_tools.json (runner-
        # materialized); v3 embeds them. Unedited v2 rows auto-refresh.
        previous_hashes=(
            "2c5b3089e71b4ee3ef5fa9b307deebfc72eec81a2685e60b7dc2081eaf600a58",
        ),
    ),
)


def seed_by_id(seed_id: str) -> HookSeed | None:
    for seed in HOOK_SEEDS:
        if seed.id == seed_id:
            return seed
    return None
