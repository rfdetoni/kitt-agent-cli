import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.core import completion_guard as base_guard
from kitt.core.execution_request import ExecutionRequest
from kitt.core.completion_guard import (
    _ProgressAwareExecutionLedger,
    install_completion_guard,
)
from kitt.core.turn_events import ToolCompleted, ToolStarted


class ProgressGuardRedirectBudgetTests(unittest.TestCase):
    @staticmethod
    def _exploration(call_id: str, path: str) -> ToolStarted:
        return ToolStarted(
            tool_name="kitt_runtime",
            args={"operation": "repo.list", "arguments": {"path": path}},
            call_id=call_id,
        )

    @staticmethod
    def _focused_read(call_id: str, path: str) -> ToolStarted:
        return ToolStarted(
            tool_name="kitt_runtime",
            args={"operation": "repo.read", "arguments": {"path": path}},
            call_id=call_id,
        )

    @staticmethod
    def _complete(call_id: str, path: str) -> ToolCompleted:
        return ToolCompleted(
            tool_name="kitt_runtime",
            success=True,
            output=f"result:{path}",
            call_id=call_id,
        )

    def test_redirect_renews_aggregate_exploration_window(self):
        ledger = _ProgressAwareExecutionLedger()

        for index in range(base_guard._MAX_EXPLORATIONS_WITHOUT_PROGRESS):
            call_id = f"read-{index}"
            path = f"src/file_{index}.py"
            self.assertIsNone(ledger.start(self._exploration(call_id, path)))
            ledger.complete(self._complete(call_id, path))

        blocked = self._exploration("blocked", "src/next.py")
        self.assertIn("too many exploration calls", ledger.start(blocked) or "")

        ledger.renew_exploration_budget()

        self.assertIsNone(ledger.start(blocked))

    def test_focused_read_is_allowed_after_broad_exploration_budget_and_resets_it(self):
        ledger = _ProgressAwareExecutionLedger()

        for index in range(base_guard._MAX_EXPLORATIONS_WITHOUT_PROGRESS):
            call_id = f"list-{index}"
            path = f"src/level_{index}"
            self.assertIsNone(ledger.start(self._exploration(call_id, path)))
            ledger.complete(self._complete(call_id, path))

        read = self._focused_read("read-pom", "backend/pom.xml")
        self.assertIsNone(ledger.start(read))
        ledger.complete(self._complete("read-pom", "backend/pom.xml"))

        self.assertEqual(ledger.explorations_since_progress, 0)
        self.assertIsNone(ledger.start(self._exploration("list-next", "frontend")))

    def test_guard_redirect_reuses_latest_execution_history_snapshot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            class Processor:
                def __init__(self):
                    self.calls = 0
                    self._execution_message_snapshots = {}

                def _execute_tool_loop(
                    self,
                    cmd,
                    request,
                    exe_profile,
                    exe_client,
                    workspace_id,
                    security_context,
                ):
                    self.calls += 1
                    if self.calls == 1:
                        history = list(request.messages)
                        for index in range(base_guard._MAX_EXPLORATIONS_WITHOUT_PROGRESS):
                            call_id = f"list-{index}"
                            yield ToolStarted(
                                tool_name="kitt_runtime",
                                args={
                                    "operation": "repo.list",
                                    "arguments": {"path": f"dir-{index}"},
                                },
                                call_id=call_id,
                            ), None, None
                            history = [
                                *history,
                                {"role": "assistant", "content": f"list-{index}"},
                                {"role": "user", "content": f"result-{index}"},
                            ]
                            self._execution_message_snapshots[cmd.turn_id] = list(history)
                            yield ToolCompleted(
                                tool_name="kitt_runtime",
                                success=True,
                                output=f"result-{index}",
                                call_id=call_id,
                            ), None, None

                        yield ToolStarted(
                            tool_name="kitt_runtime",
                            args={
                                "operation": "repo.list",
                                "arguments": {"path": "blocked-next"},
                            },
                            call_id="blocked",
                        ), None, None
                        raise AssertionError("guard must close first loop at stall")

                    self.assert_preserved_history(request.messages)
                    yield ToolStarted(
                        tool_name="kitt_runtime",
                        args={
                            "operation": "repo.write_file",
                            "arguments": {"path": "done.txt", "content": "done"},
                        },
                        call_id="write",
                    ), None, None
                    Path(temp, "done.txt").write_text("done", encoding="utf-8")
                    yield ToolCompleted(
                        tool_name="kitt_runtime",
                        success=True,
                        output='{"path":"done.txt"}',
                        call_id="write",
                    ), None, None
                    yield None, "Implementação concluída.", list(request.messages)

                @staticmethod
                def assert_preserved_history(messages):
                    contents = [
                        str(message.get("content", ""))
                        for message in messages
                        if isinstance(message, dict)
                    ]
                    assert "result-11" in contents
                    assert any(
                        "[KITT FORWARD PROGRESS REQUIRED]" in content
                        for content in contents
                    )

            class Registry:
                root_path = Path(temp)

            processor = Processor()
            install_completion_guard(processor, Registry())
            cmd = SimpleNamespace(
                turn_id="turn-history",
                prompt="crie um arquivo no workspace",
                mode="auto",
            )
            request = ExecutionRequest(
                system_prompt="system",
                messages=[{"role": "user", "content": "crie um arquivo no workspace"}],
                enabled_tools=["kitt_runtime"],
                agent_route="code-generation",
            )

            items = list(
                processor._execute_tool_loop(
                    cmd,
                    request,
                    object(),
                    object(),
                    "workspace",
                    object(),
                )
            )

            self.assertEqual(processor.calls, 2)
            self.assertTrue(Path(temp, "done.txt").is_file())
            self.assertTrue(any(item[1] == "Implementação concluída." for item in items))

    def test_redirect_keeps_identical_exploration_history(self):
        ledger = _ProgressAwareExecutionLedger()
        path = "src/same.py"

        for index in range(base_guard._MAX_IDENTICAL_EXPLORATIONS_WITHOUT_PROGRESS - 1):
            call_id = f"same-{index}"
            self.assertIsNone(ledger.start(self._exploration(call_id, path)))
            ledger.complete(self._complete(call_id, path))

        repeated = self._exploration("same-blocked", path)
        self.assertIn("identical exploration repeated", ledger.start(repeated) or "")

        ledger.renew_exploration_budget()

        self.assertIn("identical exploration repeated", ledger.start(repeated) or "")


if __name__ == "__main__":
    unittest.main()
