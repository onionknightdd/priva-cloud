"""Emit-shape tests for the require-permission-risky-tools hook.

The hook is the `require-permission-risky-tools` hook-policy SEED (a standalone
python3 script in data-spine, stdlib-only port of priva_common.risky_matcher).
Since v3 the risky patterns are EMBEDDED in the script (no $PRIVA_HOOK_DIR
context file), so the script is self-contained wherever the CLI runs it. These
tests run the actual seed script the way the executor does — stdin JSON in,
JSON on stdout out — locking in the permissionDecision='ask' emit shape.
Interactive enforcement still happens in service.py via the can_use_tool
wrapper (direct matches_any), independent of this hook.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from priva_common.hook_seeds import seed_by_id


class RequirePermissionRiskyHookTests(unittest.TestCase):
    def setUp(self) -> None:
        seed = seed_by_id("require-permission-risky-tools")
        assert seed is not None
        self.script = Path(tempfile.mkdtemp()) / "hook.py"
        self.script.write_text(seed.script_body)

    def _invoke(self, *, tool_name, tool_input):
        proc = subprocess.run(
            ["python3", str(self.script)],
            input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
            capture_output=True, text=True, timeout=15,
            env={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.strip()
        return json.loads(out) if out else {}

    def _assert_ask(self, result, pattern):
        self.assertIn("hookSpecificOutput", result)
        out = result["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "ask")
        reason = out["permissionDecisionReason"]
        self.assertIn(pattern, reason)
        # Reason is hardcoded Chinese inside the hook script.
        self.assertIn("高风险", reason)
        self.assertIn("请再次确认", reason)

    def test_embedded_bash_prefix_returns_ask(self) -> None:
        result = self._invoke(tool_name="Bash", tool_input={"command": "rm -rf /tmp"})
        self._assert_ask(result, "Bash(rm:*)")

    def test_embedded_path_glob_returns_ask(self) -> None:
        result = self._invoke(tool_name="Write",
                              tool_input={"file_path": "/etc/hosts", "content": "x"})
        self._assert_ask(result, "Write(/etc/**)")

    def test_embedded_mcp_glob_returns_ask(self) -> None:
        result = self._invoke(tool_name="mcp__github__delete_repo", tool_input={})
        self._assert_ask(result, "mcp__*__delete_*")

    def test_safe_command_returns_noop(self) -> None:
        result = self._invoke(tool_name="Bash", tool_input={"command": "ls /tmp"})
        self.assertEqual(result, {})

    def test_prefix_must_break_at_word_boundary(self) -> None:
        # "rmdir" must NOT match the "rm" prefix rule.
        result = self._invoke(tool_name="Bash", tool_input={"command": "rmdir /tmp/x"})
        self.assertEqual(result, {})

    def test_missing_tool_name_returns_noop(self) -> None:
        result = self._invoke(tool_name="", tool_input={"command": "rm"})
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
