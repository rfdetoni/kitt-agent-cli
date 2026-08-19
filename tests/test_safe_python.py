import json
import tempfile
import unittest
from pathlib import Path

from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry
from kitt.tools.safe_python import SafePythonConfig, SafePythonExecutor
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ApprovalRequired, TextDelta, ToolCompleted, ToolStarted, TurnCompleted, TurnFailed
from kitt.core.turn_processor import TurnProcessor
from tests.test_fake_llm_e2e import FakeLLMClient


class TestSafePythonExecutor(unittest.TestCase):
    def setUp(self):
        self.executor = SafePythonExecutor(
            SafePythonConfig(
                timeout_seconds=3.0,
                cpu_seconds=2,
                max_steps=10_000,
                max_collection_items=1_000,
                max_output_chars=2_048,
            )
        )

    def execute(self, code, inputs=None, result_var="_result"):
        return self.executor.execute(code, inputs=inputs, result_var=result_var)

    def parse_output(self, execution):
        self.assertTrue(execution.success, execution.error)
        return json.loads(execution.output)

    def test_arithmetic_loop_and_result(self):
        execution = self.execute(
            """
total = 0
for number in range(1, 11):
    total += number
_result = total
"""
        )
        payload = self.parse_output(execution)
        self.assertEqual(payload["result"], 55)
        self.assertGreater(payload["steps"], 0)

    def test_comprehension_inputs_and_allowlisted_modules(self):
        execution = self.execute(
            """
values = [value * 2 for value in inputs["values"] if value % 2 == 0]
_result = {
    "values": values,
    "mean": statistics.mean(values),
    "root": math.sqrt(81),
    "json": json.loads('{"ok": true}')["ok"],
}
""",
            inputs={"values": [1, 2, 3, 4, 5, 6]},
        )
        payload = self.parse_output(execution)
        self.assertEqual(payload["result"]["values"], [4, 8, 12])
        self.assertEqual(payload["result"]["mean"], 8)
        self.assertEqual(payload["result"]["root"], 9)
        self.assertTrue(payload["result"]["json"])

    def test_print_is_captured_and_result_variable_is_configurable(self):
        execution = self.execute(
            'print("count", len(inputs["items"]))\nanswer = sum(inputs["items"])',
            inputs={"items": [1, 2, 3]},
            result_var="answer",
        )
        payload = self.parse_output(execution)
        self.assertEqual(payload["stdout"], "count 3\n")
        self.assertEqual(payload["result"], 6)

    def test_import_file_network_shell_and_reflection_are_rejected(self):
        rejected = [
            "import os\n_result = os.getcwd()",
            "_result = open('/etc/passwd').read()",
            "_result = __import__('subprocess').run(['id'])",
            "_result = (1).__class__.__mro__",
            "_result = globals()",
            "while True:\n    pass",
            "def f():\n    return 1\n_result = f()",
            "class X:\n    pass",
        ]
        for code in rejected:
            with self.subTest(code=code):
                execution = self.execute(code)
                self.assertFalse(execution.success)
                self.assertTrue(execution.error)

    def test_cannot_read_or_write_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sentinel = Path(temp_dir) / "secret.txt"
            sentinel.write_text("secret", encoding="utf-8")
            read_attempt = self.execute(f"_result = open({str(sentinel)!r}).read()")
            write_attempt = self.execute(f"open({str(sentinel)!r}, 'w').write('changed')")
            self.assertFalse(read_attempt.success)
            self.assertFalse(write_attempt.success)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "secret")

    def test_step_and_collection_limits(self):
        limited = SafePythonExecutor(
            SafePythonConfig(
                timeout_seconds=2.0,
                cpu_seconds=1,
                max_steps=20,
                max_collection_items=10,
            )
        )
        step_result = limited.execute(
            "total = 0\nfor value in range(10):\n    total += value\n_result = total"
        )
        collection_result = limited.execute("_result = list(range(100))")
        self.assertFalse(step_result.success)
        self.assertIn("Step limit", step_result.error)
        self.assertFalse(collection_result.success)
        self.assertIn("limit", collection_result.error)

    def test_source_and_request_limits(self):
        limited = SafePythonExecutor(
            SafePythonConfig(max_code_bytes=32, max_input_bytes=128)
        )
        code_result = limited.execute("_result = '" + ("x" * 100) + "'")
        input_result = limited.execute("_result = inputs", inputs={"x": "y" * 200})
        self.assertFalse(code_result.success)
        self.assertIn("code exceeds", code_result.error)
        self.assertFalse(input_result.success)
        self.assertIn("request exceeds", input_result.error)

    def test_output_is_truncated_without_unbounded_capture(self):
        limited = SafePythonExecutor(
            SafePythonConfig(max_output_chars=20, max_output_bytes=4_096)
        )
        execution = limited.execute("print('x' * 100)\n_result = 1")
        payload = self.parse_output(execution)
        self.assertTrue(execution.truncated)
        self.assertEqual(len(payload["stdout"]), 20)
        self.assertEqual(payload["result"], 1)

    def test_reserved_names_cannot_be_overwritten(self):
        for code in ("math = 1", "len = 2", "__builtins__ = {}"):
            with self.subTest(code=code):
                execution = self.execute(code)
                self.assertFalse(execution.success)


class TestSafePythonRegistryIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = ToolRegistry(root_dir=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tool_is_allowlisted_because_it_has_no_side_effect_capabilities(self):
        self.assertEqual(PolicyEngine(self.temp_dir.name).evaluate_tool("python_compute"), "ALLOW")

    def test_tool_definition_describes_restrictions(self):
        definitions = self.registry.get_tool_definitions(["python_compute"])
        self.assertEqual(len(definitions), 1)
        self.assertIn("No imports", definitions[0]["description"])

    def test_registry_executes_safe_python(self):
        result = self.registry.execute_tool(
            "python_compute",
            {
                "code": "_result = statistics.mean(inputs['samples'])",
                "inputs": {"samples": [10, 20, 30]},
            },
            enabled_tools=["python_compute"],
        )
        self.assertTrue(result.success, result.error)
        self.assertEqual(json.loads(result.output)["result"], 20)
        self.assertGreater(result.bytes_count, 0)

    def test_registry_rejects_when_context_plan_does_not_enable_tool(self):
        result = self.registry.execute_tool(
            "python_compute",
            {"code": "_result = 1"},
            enabled_tools=["read_file"],
        )
        self.assertFalse(result.success)
        self.assertIn("not enabled", result.error)


class TestSafePythonTurnIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.context_client = FakeLLMClient([
            '{"intent":"ASK","confidence":0.95,"actions":["calculate"]}'
        ])

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_model_can_compute_then_continue_without_exposing_envelope(self):
        from kitt.core.runtime_config import RuntimeConfig
        tool_call = (
            '<kitt-python-compute>\n'
            '{"code":"_result = statistics.mean(inputs[\\"values\\"])",'
            '"inputs":{"values":[10,20,30]},"result_var":"_result"}\n'
            '</kitt-python-compute>'
        )
        execution_client = FakeLLMClient([tool_call, "A média calculada é 20."])
        processor = TurnProcessor(
            root_dir=self.temp_dir.name,
            context_client=self.context_client,
            execution_client=execution_client,
            config=RuntimeConfig(tool_runtime_mode="legacy"),
        )

        events = list(processor.run_turn(TurnCommand(conversation_id="conv", prompt="Calcule a média.")))
        deltas = "".join(event.delta for event in events if isinstance(event, TextDelta))
        completed = [event for event in events if isinstance(event, TurnCompleted)]

        self.assertEqual(len(execution_client.calls), 2)
        self.assertEqual(sum(isinstance(event, ToolStarted) for event in events), 1)
        tool_results = [event for event in events if isinstance(event, ToolCompleted)]
        self.assertEqual(len(tool_results), 1)
        self.assertTrue(tool_results[0].success, tool_results[0].error)
        self.assertNotIn("kitt-python-compute", deltas)
        self.assertEqual(deltas, "A média calculada é 20.")
        self.assertEqual(completed[-1].response, "A média calculada é 20.")
        self.assertIn("python_compute result", execution_client.calls[1]["messages"][-1]["content"])
        self.assertIn("untrusted data", execution_client.calls[1]["messages"][-1]["content"])

    def test_malformed_tool_envelope_retries_without_exposing_it(self):
        from kitt.core.runtime_config import RuntimeConfig
        execution_client = FakeLLMClient([
            '<kitt-python-compute>{not-json}</kitt-python-compute>',
            "Resposta recuperada.",
        ])
        processor = TurnProcessor(
            root_dir=self.temp_dir.name,
            context_client=self.context_client,
            execution_client=execution_client,
            config=RuntimeConfig(tool_runtime_mode="legacy"),
        )
        events = list(processor.run_turn(TurnCommand(conversation_id="conv", prompt="Calcule.")))
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertFalse(any(isinstance(event, TurnFailed) for event in events))
        self.assertEqual(len(execution_client.calls), 2)
        self.assertEqual(completed[-1].response, "Resposta recuperada.")
        self.assertFalse(any(isinstance(event, ToolStarted) for event in events))

    def test_invalid_patch_retries_before_requesting_approval(self):
        from kitt.core.runtime_config import RuntimeConfig
        execution_client = FakeLLMClient([
            '<kitt-tool>{"name":"apply_patch","arguments":{"patch":"<html/>"}}</kitt-tool>',
            "Resposta recuperada.",
        ])
        processor = TurnProcessor(
            root_dir=self.temp_dir.name,
            context_client=self.context_client,
            execution_client=execution_client,
            enable_context_summary=True,
            config=RuntimeConfig(tool_runtime_mode="legacy"),
        )
        events = list(processor.run_turn(TurnCommand(conversation_id="conv", prompt="Crie uma pagina html.")))
        self.assertFalse(any(isinstance(event, (TurnFailed, ApprovalRequired)) for event in events))
        self.assertEqual(len(execution_client.calls), 2)
        self.assertTrue(any(isinstance(event, TurnCompleted) for event in events))


if __name__ == "__main__":
    unittest.main()
