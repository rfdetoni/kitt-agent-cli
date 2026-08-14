import unittest
import tempfile
from pathlib import Path
from kitt.context_filter.context_resolver import ContextResolver
from kitt.core.session_state import SessionState
from kitt.core.execution_request import ExecutionRequest

class TestContextResolver(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.resolver = ContextResolver(root_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_safe_path_resolution(self):
        is_safe, full_p = self.resolver.is_safe_path("../../etc/passwd")
        self.assertFalse(is_safe)

        is_safe_env, _ = self.resolver.is_safe_path(".env")
        self.assertFalse(is_safe_env)

        valid_file = self.root_path / "app.py"
        valid_file.write_text("print('hello')\n", encoding='utf-8')
        is_safe_valid, resolved = self.resolver.is_safe_path("app.py")
        self.assertTrue(is_safe_valid)
        self.assertEqual(resolved, valid_file)

    def test_session_state_and_execution_request(self):
        state = SessionState(current_prompt="Fix bug")
        self.assertEqual(state.current_prompt, "Fix bug")

        req = ExecutionRequest(
            system_prompt="You are K.I.T.T.",
            messages=[{"role": "user", "content": "Fix bug"}],
            enabled_tools=["read_file", "apply_patch"],
            max_output_tokens=1200
        )
        self.assertEqual(req.max_output_tokens, 1200)
        self.assertIn("read_file", req.enabled_tools)

    def test_agents_instructions_skip_incoherent_stack_claims(self):
        (self.root_path / "kitt").mkdir()
        (self.root_path / "AGENTS.md").write_text(
            "This project uses Node.js, Bun, TypeScript, React and Ink.\n",
            encoding="utf-8",
        )

        items = self.resolver.resolve_agents_instructions()

        self.assertEqual(items, [])

if __name__ == '__main__':
    unittest.main()
