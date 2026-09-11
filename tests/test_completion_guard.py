import tempfile
import unittest
from pathlib import Path

from kitt.core.completion_guard import (
    install_completion_guard,
    missing_claimed_workspace_files,
)
from kitt.core.execution_request import ExecutionRequest
from kitt.core.turn_events import TurnFailed


class _Registry:
    def __init__(self, root: str):
        self.root_path = Path(root).resolve()


class _Processor:
    def __init__(self, root: str, *, recover: bool = True):
        self.root = Path(root)
        self.calls = 0
        self.recover = recover

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
            yield (
                None,
                "Diretório scriptContext/ criado e script "
                "scriptContext/generate_context.py implementado.",
                list(request.messages),
            )
            return

        self.assert_retry_contract(request)
        if self.recover:
            target = self.root / "scriptContext" / "generate_context.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("print('context')\n", encoding="utf-8")
        yield (
            None,
            "scriptContext/generate_context.py implementado com sucesso.",
            list(request.messages),
        )

    @staticmethod
    def assert_retry_contract(request):
        correction = request.messages[-1]["content"]
        assert "KITT COMPLETION VERIFICATION" in correction
        assert "repo.write_file" in correction
        assert "scriptContext/generate_context.py" in correction


class CompletionGuardTests(unittest.TestCase):
    def test_portuguese_false_completion_claim_detects_missing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = missing_claimed_workspace_files(
                temp,
                "Diretório scriptContext/ criado e script "
                "scriptContext/generate_context.py implementado.",
            )
            self.assertEqual(missing, ["scriptContext/generate_context.py"])

    def test_future_or_negative_statements_do_not_claim_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(
                missing_claimed_workspace_files(
                    temp,
                    "scriptContext/generate_context.py will be created when the command runs.",
                ),
                [],
            )
            self.assertEqual(
                missing_claimed_workspace_files(
                    temp,
                    "Não foi criado scriptContext/generate_context.py.",
                ),
                [],
            )

    def test_guard_retries_and_only_completes_after_file_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            processor = _Processor(temp, recover=True)
            registry = _Registry(temp)
            install_completion_guard(processor, registry)
            request = ExecutionRequest(
                system_prompt="test",
                messages=[{"role": "user", "content": "crie o script"}],
                enabled_tools=["kitt_runtime"],
            )

            items = list(
                processor._execute_tool_loop(
                    object(), request, object(), object(), "workspace", object()
                )
            )

            self.assertEqual(processor.calls, 2)
            self.assertTrue((Path(temp) / "scriptContext" / "generate_context.py").is_file())
            terminal = [item for item in items if item[1] is not None]
            self.assertEqual(len(terminal), 1)
            self.assertIn("implementado com sucesso", terminal[0][1])
            self.assertFalse(any(isinstance(item[0], TurnFailed) for item in items))

    def test_guard_fails_closed_after_bounded_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            processor = _Processor(temp, recover=False)
            registry = _Registry(temp)
            install_completion_guard(processor, registry, max_retries=1)
            request = ExecutionRequest(
                system_prompt="test",
                messages=[{"role": "user", "content": "crie o script"}],
                enabled_tools=["kitt_runtime"],
            )

            items = list(
                processor._execute_tool_loop(
                    object(), request, object(), object(), "workspace", object()
                )
            )

            failures = [item[0] for item in items if isinstance(item[0], TurnFailed)]
            self.assertEqual(processor.calls, 2)
            self.assertEqual(len(failures), 1)
            self.assertIn("still missing after recovery", failures[0].error)
            self.assertFalse(any(item[1] is not None for item in items))


if __name__ == "__main__":
    unittest.main()
