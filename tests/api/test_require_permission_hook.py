"""Emit-shape tests for the require-permission-risky-tools hook.

The hook is now the `require-permission-risky-tools` hook-policy SEED (a
standalone python3 script in data-spine, stdlib-only port of
priva_common.risky_matcher). These tests run the actual seed script the way
the executor does — stdin JSON in, JSON on stdout out, rules read from
$PRIVA_HOOK_DIR/risky_tools.json — locking in the permissionDecision='ask'
emit shape. Interactive enforcement still happens in service.py via the
can_use_tool wrapper (direct matches_any), independent of this hook.
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
        self.hook_dir = Path(tempfile.mkdtemp())
        self.script = self.hook_dir / "hook.py"
        self.script.write_text(seed.script_body)

    def _invoke(self, *, risky_list, tool_name, tool_input, with_rules_file=True):
        if with_rules_file:
            (self.hook_dir / "risky_tools.json").write_text(json.dumps(risky_list))
        proc = subprocess.run(
            ["python3", str(self.script)],
            input=json.dumps({"tool_name": tool_name, "tool_input": tool_input}),
            capture_output=True, text=True, timeout=15,
            env={"PRIVA_HOOK_DIR": str(self.hook_dir), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.strip()
        return json.loads(out) if out else {}

    def test_empty_list_returns_noop(self) -> None:
        result = self._invoke(risky_list=[], tool_name="Bash", tool_input={"command": "rm -rf /tmp"})
        self.assertEqual(result, {})

    def test_match_returns_ask(self) -> None:
        result = self._invoke(
            risky_list=["Bash(rm:*)"],
            tool_name="Bash",
            tool_input={"command": "rm -rf /tmp"},
        )
        self.assertIn("hookSpecificOutput", result)
        out = result["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "ask")
        reason = out["permissionDecisionReason"]
        self.assertIn("Bash(rm:*)", reason)
        # Reason is hardcoded Chinese inside the hook script.
        self.assertIn("高风险", reason)
        self.assertIn("请再次确认", reason)

    def test_miss_returns_noop(self) -> None:
        result = self._invoke(
            risky_list=["Bash(rm:*)"],
            tool_name="Bash",
            tool_input={"command": "ls /tmp"},
        )
        self.assertEqual(result, {})

    def test_missing_tool_name_returns_noop(self) -> None:
        result = self._invoke(
            risky_list=["Bash(rm:*)"],
            tool_name="",
            tool_input={"command": "rm"},
        )
        self.assertEqual(result, {})

    def test_no_rules_file_returns_noop(self) -> None:
        result = self._invoke(
            risky_list=["Bash(rm:*)"],
            tool_name="Bash",
            tool_input={"command": "rm -rf /tmp"},
            with_rules_file=False,
        )
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
