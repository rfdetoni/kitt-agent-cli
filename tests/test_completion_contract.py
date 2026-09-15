import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.core.completion_guard import build_completion_contract


class CompletionContractTests(unittest.TestCase):
    def test_full_stack_angular_project_requires_both_scopes_and_validation(self):
        prompt = "crie um site com backend e frontend em angular. Crie o projeto e a implementação"
        contract = build_completion_contract(prompt, SimpleNamespace(intent="IMPLEMENT"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            issues = contract.evaluate(root)
            self.assertTrue(any("backend" in issue for issue in issues))
            self.assertTrue(any("frontend" in issue for issue in issues))
            self.assertTrue(any("validated" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
