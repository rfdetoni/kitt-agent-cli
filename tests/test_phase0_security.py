import unittest
import tempfile
import shlex
import subprocess
from pathlib import Path
from kitt.domain.entities import EditBlock
from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.changeset import ChangeSetTracker
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry
from kitt.context_filter.schema import ContextFilterSchemaValidator
from kitt.context_filter.prompt_budget import PromptBudget, PromptTooLargeError

class TestPhase0SecurityAndContainment(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.tmp_dir.name).resolve()
        self.tracker = ChangeSetTracker(root_dir=self.tmp_dir.name)
        self.applier = DiffApplier(changeset_tracker=self.tracker)
        self.policy = PolicyEngine()
        self.registry = ToolRegistry(root_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_path_traversal_blocked(self):
        block = EditBlock(
            file_path="../../etc/passwd",
            search_content="",
            replace_content="malicious_entry",
            is_new_file=True
        )
        res = self.applier.apply([block], root_dir=self.tmp_dir.name)
        self.assertFalse(res.success)
        self.assertTrue(any("Path containment violation" in err for err in res.errors))

    def test_forbidden_files_blocked(self):
        block_env = EditBlock(
            file_path=".env",
            search_content="",
            replace_content="API_KEY=stolen",
            is_new_file=True
        )
        res = self.applier.apply([block_env], root_dir=self.tmp_dir.name)
        self.assertFalse(res.success)
        self.assertTrue(any("Access denied" in err for err in res.errors))

    def test_cat_env_and_find_etc_denied(self):
        denied_commands = [
            "cat .env",
            "cat /etc/passwd",
            "find /etc",
            "find .",
            "git diff --no-index /etc/hosts /etc/passwd",
            "git -C /tmp status",
            "git --git-dir=/tmp/.git status",
            "git --work-tree=/tmp status"
        ]
        for cmd in denied_commands:
            perm = self.policy.evaluate_command(cmd)
            self.assertEqual(perm, 'DENY', f"Command '{cmd}' should have been DENIED by PolicyEngine.")
            grant = self.registry.issue_approval_grant("turn-1", "run_command", {"command": cmd})
            res = self.registry.execute_tool("run_command", {"command": cmd}, grant=grant)
            self.assertFalse(res.success, f"Command '{cmd}' execution should have been blocked.")

    def test_ask_policy_requires_explicit_approval_grant(self):
        res_unapproved = self.registry.execute_tool("apply_patch", {"patch": ""})
        self.assertFalse(res_unapproved.success)
        self.assertTrue(res_unapproved.requires_approval)
        self.assertIn("requires explicit user confirmation", res_unapproved.error)

        grant = self.registry.issue_approval_grant("turn-1", "apply_patch", {"patch": ""})
        res_approved = self.registry.execute_tool("apply_patch", {"patch": ""}, grant=grant)
        self.assertFalse(res_approved.requires_approval)

    def test_chained_shell_commands_denied(self):
        chained_cmds = [
            "git status; touch sentinel",
            "git status && touch sentinel",
            "git status || touch sentinel",
            "git status | grep master",
            "echo $(whoami)",
            "echo `whoami`",
            "git status\ntouch sentinel"
        ]
        for cmd in chained_cmds:
            perm = self.policy.evaluate_command(cmd)
            self.assertEqual(perm, 'DENY', f"Command '{cmd}' should have been DENIED.")

            grant = self.registry.issue_approval_grant("turn-1", "run_command", {"command": cmd})
            res = self.registry.execute_tool("run_command", {"command": cmd}, grant=grant)
            self.assertFalse(res.success, f"Command '{cmd}' should have failed execution.")

    def test_is_new_file_overwrite_rejected(self):
        target = self.root_path / "existing.py"
        target.write_text("ORIGINAL_CONTENT\n", encoding='utf-8')

        block = EditBlock(
            file_path="existing.py",
            search_content="",
            replace_content="OVERWRITTEN\n",
            is_new_file=True
        )
        res = self.applier.apply([block], root_dir=self.tmp_dir.name)
        self.assertFalse(res.success)
        self.assertIn("Cannot overwrite existing file", res.errors[0])
        self.assertEqual(target.read_text(), "ORIGINAL_CONTENT\n")

    def test_invented_constraint_rejected(self):
        prompt = "Fix bug in kitt/cli/repl.py without modifying prompt budget."
        raw_json = """{
            "intent": "DEBUG",
            "constraints": [
                {
                    "text": "INVENTED_CONSTRAINT_NOT_IN_PROMPT",
                    "kind": "MANDATORY"
                },
                {
                    "text": "without modifying prompt budget",
                    "kind": "NEGATIVE"
                }
            ],
            "confidence": 0.9
        }"""
        valid, task, err = ContextFilterSchemaValidator.validate_and_parse_task(raw_json, prompt)
        self.assertTrue(valid)
        self.assertEqual(len(task.constraints), 1)
        self.assertEqual(task.constraints[0].text, "without modifying prompt budget")

    def test_prompt_budget_enforces_global_window(self):
        budget = PromptBudget(window_size=8192, reserved_output=1200)
        giant_context = "x = 1\n" * 15000  # ~20,000 tokens

        alloc = budget.allocate_context(
            system_prompt="You are K.I.T.T.",
            task_prompt="Refactor code",
            mandatory_constraints=["without breaking API"],
            repo_map="repo_map_content",
            files_context=giant_context,
            history_context="history_content",
            recent_results="recent_results_content"
        )

        total_input = alloc["total_input_tokens"]
        reserved = alloc["reserved_output_tokens"]
        self.assertLessEqual(total_input + reserved, budget.window_size)

    def test_prompt_too_large_exception_raised(self):
        budget = PromptBudget(window_size=2000, reserved_output=1200)
        giant_task = "Do work " * 5000  # Exceeds max allowed 800 input tokens

        with self.assertRaises(PromptTooLargeError):
            budget.allocate_context(
                system_prompt="Base sys",
                task_prompt=giant_task,
                mandatory_constraints=["mandatory"],
                repo_map="",
                files_context="",
                history_context="",
                recent_results=""
            )

    def test_changeset_undo_preserves_user_uncommitted_changes(self):
        user_file = self.root_path / "user_work.py"
        user_file.write_text("def user_feature(): pass\n", encoding='utf-8')

        kitt_file = self.root_path / "kitt_module.py"
        kitt_file.write_text("def old_function(): pass\n", encoding='utf-8')

        edit_block = EditBlock(
            file_path="kitt_module.py",
            search_content="def old_function(): pass",
            replace_content="def new_function(): pass"
        )
        res = self.applier.apply([edit_block], root_dir=self.tmp_dir.name)
        self.assertTrue(res.success)
        self.assertEqual(kitt_file.read_text(), "def new_function(): pass\n")

        user_file.write_text("def user_feature(): return 42\n", encoding='utf-8')

        reverted_cs = self.tracker.revert_last_changeset()
        self.assertIsNotNone(reverted_cs)

        self.assertEqual(kitt_file.read_text(), "def old_function(): pass\n")
        self.assertEqual(user_file.read_text(), "def user_feature(): return 42\n")

if __name__ == '__main__':
    unittest.main()
