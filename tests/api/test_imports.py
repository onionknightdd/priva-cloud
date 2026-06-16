import importlib
import unittest


class ImportSmokeTests(unittest.TestCase):
    def test_can_import_priva_api_main_from_repo_root(self) -> None:
        module = importlib.import_module("priva.api.main")
        self.assertTrue(callable(module.create_app))


if __name__ == "__main__":
    unittest.main()
