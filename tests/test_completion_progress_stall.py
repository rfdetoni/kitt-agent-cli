import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.core.completion_guard import install_completion_guard
from kitt.core.execution_request import ExecutionRequest
from kitt.core.turn_events import ToolCompleted, ToolStarted, TurnFailed


class _Registry:
    def __init__(self, root: Path):
        self.root_path = root


class CompletionProgressStallTests(unittest.TestCase):
    def test_three_identical_repo_lists_fail_before_runaway_loop(self):
        class Processor:
            def __init__(self):
                self.session_state = SimpleNamespace(
                    last_task=SimpleNamespace(intent="IMPLEMENT", actions=["analyze", "edit"])
                )

            def _execute_tool_loop(self, cmd, request, *args, **kwargs):
                for index in range(3):
                    call_id = f"list-{index}"
                    tool_args = {"operation": "repo.list", "arguments": {"path": "."}}
                    yield ToolStarted(tool_name="kitt_runtime", args=tool_args, call_id=call_id), None, None
                    yield ToolCompleted(tool_name="kitt_runtime", success=True, output="[]", call_id=call_id), None, None
                yield None, "Ainda analisando.", list(request.messages)

        with tempfile.TemporaryDirectory() as tmp:
            processor = Processor()
            install_completion_guard(processor, _Registry(Path(tmp)))
            cmd = SimpleNamespace(prompt="crie o projeto e implemente o backend", mode="auto")
            request = ExecutionRequest(
                system_prompt="test",
                messages=[{"role": "user", "content": cmd.prompt}],
                enabled_tools=["kitt_runtime"],
            )
            events = list(processor._execute_tool_loop(cmd, request, None, None, "local", None))
            failures = [event for event, _, _ in events if isinstance(event, TurnFailed)]
            self.assertEqual(len(failures), 1)
            self.assertIn("Execution stalled", failures[0].error)
            self.assertIn("identical exploration repeated", failures[0].error)


if __name__ == "__main__":
    unittest.main()
