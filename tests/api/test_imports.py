import importlib
import os
import unittest


class ImportSmokeTests(unittest.TestCase):
    """Phase 2: the monolith no longer boots (clean break). The two service
    apps are the boot-check now."""

    def test_agent_runner_app_imports(self) -> None:
        os.environ.setdefault("ACCOUNT_ID", "test-account")
        module = importlib.import_module("priva_agent_runner.app")
        self.assertTrue(callable(module.create_app))

    def test_control_panel_app_imports(self) -> None:
        module = importlib.import_module("priva_control_panel.app")
        self.assertTrue(callable(module.create_app))


if __name__ == "__main__":
    unittest.main()
