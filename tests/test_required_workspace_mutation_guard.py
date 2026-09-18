import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.core.completion_guard import (
    install_completion_guard,
    is_deferred_implementation_response,
    requires_workspace_mutation,
)
from kitt.core.execution_request import ExecutionRequest
from kitt.core.runtime import KittRuntime
from kitt.core.turn_events import ToolCompleted, ToolStarted, TurnFailed


PROJECT_PROMPT = (
    "crie um site moderno e limpo para registrar prestadores de serviço, será chamado "
    "meufaztudo e juntará maridos de aluguel a pessoas que precisam contratar o serviço, "
    "crie pasta de backend com o conteudo de backend e pasta de front end com todo o front "
    "em angular. Crie o projeto e a implementação"
)
GEMINI_DEFERRED_RESPONSE = (
    "Para implementar o 'meufaztudo', inicialize o workspace com `ng new frontend` e "
    "`mkdir backend`, e então me solicite os códigos específicos de cada componente para "
    "que eu gere o frontend em Angular e a API."
)


class _Registry:
    def __init__(self, root_path: Path):
        self.root_path = root_path


class _Processor:
    def __init__(self, responses, root_path: Path):
        self.responses = list(responses)
        self.calls = []
        self.root_path = root_path
        self.session_state = SimpleNamespace(
            last_task=SimpleNamespace(intent="IMPLEMENT", actions=["analyze", "edit"])
        )

    def _write(self, relative_path: str, content: str):
        target = self.root_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _materialize_complete_project(self):
        self._write("backend/pom.xml", "<project/>")
        self._write("backend/src/main/java/App.java", "class App {}")
        self._write("frontend/package.json", "{}")
        self._write("frontend/angular.json", "{}")
        self._write("frontend/src/app/app.component.ts", "export class AppComponent {}")

    def _execute_tool_loop(
        self,
        cmd,
        request,
        exe_profile,
        exe_client,
        workspace_id,
        security_context,
        agent_route=None,
        **loop_kwargs,
    ):
        self.calls.append(request)
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if response in {"__MUTATE__", "__MUTATE_DEFER__"}:
            call_id = f"write-{len(self.calls)}"
            args = {
                "operation": "repo.write_file",
                "arguments": {
                    "path": "frontend/src/app/app.component.ts",
                    "content": "export class App {}",
                },
            }
            if response == "__MUTATE__":
                self._materialize_complete_project()
            else:
                self._write("frontend/src/app/app.component.ts", "export class App {}")
            yield ToolStarted(tool_name="kitt_runtime", args=args, call_id=call_id), None, None
            yield ToolCompleted(
                tool_name="kitt_runtime",
                success=True,
                output="written",
                call_id=call_id,
            ), None, None
            if response == "__MUTATE__":
                validation_id = f"build-{len(self.calls)}"
                validation_args = {
                    "operation": "process.run",
                    "arguments": {"command": "npm run build"},
                }
                yield ToolStarted(
                    tool_name="kitt_runtime",
                    args=validation_args,
                    call_id=validation_id,
                ), None, None
                yield ToolCompleted(
                    tool_name="kitt_runtime",
                    success=True,
                    output="build ok",
                    call_id=validation_id,
                ), None, None
            final_response = (
                GEMINI_DEFERRED_RESPONSE
                if response == "__MUTATE_DEFER__"
                else "Projeto implementado no workspace."
            )
            yield None, final_response, list(request.messages)
            return
        yield None, response, list(request.messages)


def _request():
    return ExecutionRequest(
        system_prompt="system",
        messages=[{"role": "user", "content": PROJECT_PROMPT}],
        enabled_tools=["kitt_runtime"],
    )


class RequiredWorkspaceMutationGuardTests(unittest.TestCase):
    def test_runtime_installs_completion_guard(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with KittRuntime.build(root_dir=tmp_dir) as runtime:
                self.assertTrue(runtime.processor._completion_guard_installed)

    def test_project_creation_requires_workspace_mutation(self):
        processor = SimpleNamespace(
            session_state=SimpleNamespace(
                last_task=SimpleNamespace(intent="IMPLEMENT", actions=["analyze", "edit"])
            )
        )
        cmd = SimpleNamespace(prompt=PROJECT_PROMPT, mode="auto")

        self.assertTrue(requires_workspace_mutation(processor, cmd))

    def test_detects_deferred_implementation_handoff(self):
        self.assertTrue(is_deferred_implementation_response(GEMINI_DEFERRED_RESPONSE))
        self.assertTrue(
            is_deferred_implementation_response(
                "Create the folders first, then send me the component code and I will implement it."
            )
        )
        self.assertFalse(
            is_deferred_implementation_response(
                "Implementei frontend e backend; os testes relevantes passaram."
            )
        )

    def test_deferred_project_response_is_retried_until_mutation_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            processor = _Processor([GEMINI_DEFERRED_RESPONSE, "__MUTATE__"], root)
            install_completion_guard(processor, _Registry(root))
            cmd = SimpleNamespace(prompt=PROJECT_PROMPT, mode="auto")

            events = list(
                processor._execute_tool_loop(cmd, _request(), None, None, "local", None)
            )

            self.assertEqual(len(processor.calls), 2)
            recovery_prompt = processor.calls[1].messages[-1]["content"]
            self.assertIn("[KITT EXECUTION REQUIRED]", recovery_prompt)
            self.assertIn("Do not answer with setup instructions", recovery_prompt)
            self.assertTrue(
                any(
                    event is None and response == "Projeto implementado no workspace."
                    for event, response, _ in events
                )
            )
            self.assertFalse(any(isinstance(event, TurnFailed) for event, _, _ in events))

    def test_partial_mutation_does_not_allow_deferred_handoff(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            processor = _Processor(["__MUTATE_DEFER__", "__MUTATE__"], root)
            install_completion_guard(processor, _Registry(root))
            cmd = SimpleNamespace(prompt=PROJECT_PROMPT, mode="auto")

            events = list(
                processor._execute_tool_loop(cmd, _request(), None, None, "local", None)
            )

            self.assertEqual(len(processor.calls), 2)
            recovery_prompt = processor.calls[1].messages[-1]["content"]
            self.assertIn("[KITT EXECUTION REQUIRED]", recovery_prompt)
            self.assertIn("perform the implementation yourself", recovery_prompt)
            self.assertIn("[KITT COMPLETION CONTRACT]", recovery_prompt)
            self.assertFalse(any(isinstance(event, TurnFailed) for event, _, _ in events))
            self.assertTrue(
                any(
                    event is None and response == "Projeto implementado no workspace."
                    for event, response, _ in events
                )
            )

    def test_repeated_deferred_handoff_after_mutation_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            processor = _Processor(["__MUTATE_DEFER__", "__MUTATE_DEFER__"], root)
            install_completion_guard(processor, _Registry(root), max_retries=1)
            cmd = SimpleNamespace(prompt=PROJECT_PROMPT, mode="auto")

            events = list(
                processor._execute_tool_loop(cmd, _request(), None, None, "local", None)
            )

            failures = [event for event, _, _ in events if isinstance(event, TurnFailed)]
            self.assertEqual(len(processor.calls), 2)
            self.assertEqual(len(failures), 1)
            self.assertIn("deferred required implementation work", failures[0].error)

    def test_repeated_deferred_project_response_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            processor = _Processor([GEMINI_DEFERRED_RESPONSE, GEMINI_DEFERRED_RESPONSE], root)
            install_completion_guard(processor, _Registry(root), max_retries=1)
            cmd = SimpleNamespace(prompt=PROJECT_PROMPT, mode="auto")

            events = list(
                processor._execute_tool_loop(cmd, _request(), None, None, "local", None)
            )

            failures = [event for event, _, _ in events if isinstance(event, TurnFailed)]
            self.assertEqual(len(processor.calls), 2)
            self.assertEqual(len(failures), 1)
            self.assertIn("required a workspace mutation", failures[0].error)

    def test_question_does_not_require_mutation(self):
        processor = SimpleNamespace(
            session_state=SimpleNamespace(
                last_task=SimpleNamespace(intent="ASK", actions=["analyze"])
            )
        )
        cmd = SimpleNamespace(prompt="como criar um projeto Angular?", mode="auto")

        self.assertFalse(requires_workspace_mutation(processor, cmd))


if __name__ == "__main__":
    unittest.main()
