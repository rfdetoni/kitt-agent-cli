import tempfile
import unittest
from pathlib import Path

from kitt.llm.providers.kitt_reverse_proxy import extract_openai_tools
from kitt.runtime.safe_runtime import OPERATION_SPECS, SafeRuntime
from kitt.security.capabilities import CAP_REPO_WRITE
from kitt.tools.registry import ToolRegistry, compact_runtime_operation_catalog


class SafeRuntimeWorkspaceWriteTests(unittest.TestCase):
    def _registry(self, root: str) -> ToolRegistry:
        registry = ToolRegistry(root_dir=root)
        registry.policy.evaluate_tool = lambda *args, **kwargs: "ALLOW"
        return registry

    def test_runtime_exposes_direct_workspace_file_write(self):
        spec = OPERATION_SPECS["repo.write_file"]
        self.assertEqual(spec.required_capability, CAP_REPO_WRITE)
        self.assertEqual(spec.policy_tool_action, "write_file")
        self.assertEqual(spec.resume_tool_name, "write_file")

    def test_every_runtime_resume_tool_has_an_executable_registry_handler(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = ToolRegistry(root_dir=temp)
            try:
                missing = {
                    operation: spec.resume_tool_name
                    for operation, spec in OPERATION_SPECS.items()
                    if spec.resume_tool_name
                    and spec.resume_tool_name not in registry._handlers
                }
                self.assertEqual(missing, {})
            finally:
                registry.close()

    def test_create_directory_then_write_file_creates_real_workspace_script(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self._registry(temp)
            try:
                runtime = SafeRuntime(
                    temp,
                    "workspace",
                    "conversation",
                    tool_registry=registry,
                )
                capabilities = {CAP_REPO_WRITE}

                created = runtime.execute(
                    "repo.create_directory",
                    {"path": "scriptContext"},
                    effective_capabilities=capabilities,
                )
                self.assertTrue(created.success, created.error)

                script = "print('context generated')\n"
                written = runtime.execute(
                    "repo.write_file",
                    {
                        "path": "scriptContext/generate_context.py",
                        "content": script,
                    },
                    effective_capabilities=capabilities,
                )
                self.assertTrue(written.success, written.error)
                self.assertEqual(written.metadata["effective_tool_name"], "write_file")

                target = Path(temp) / "scriptContext" / "generate_context.py"
                self.assertTrue(target.is_file())
                self.assertEqual(target.read_text(encoding="utf-8"), script)
            finally:
                registry.close()

    def test_artifact_store_rejects_workspace_path_instead_of_false_success(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self._registry(temp)
            try:
                result = registry.execute_tool(
                    "artifact_store",
                    {
                        "path": "scriptContext/generate_context.py",
                        "content": "print('wrong destination')\n",
                    },
                    enabled_tools=["artifact_store"],
                )
                self.assertFalse(result.success)
                self.assertIn("repo.write_file", result.error)
                self.assertFalse(
                    (Path(temp) / "scriptContext" / "generate_context.py").exists()
                )
            finally:
                registry.close()

    def test_model_contract_is_compact_but_native_proxy_schema_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = ToolRegistry(root_dir=temp)
            try:
                definition = registry.get_tool_definitions(["kitt_runtime"])[0]
                self.assertIn("repo.write_file", definition["description"])
                self.assertIn("artifacts.store", definition["description"])
                self.assertEqual(
                    definition["args"]["operation"],
                    compact_runtime_operation_catalog(),
                )
                self.assertIsInstance(definition["args"]["arguments"], str)

                tools = extract_openai_tools(f"Available host tools: {[definition]}")
                native = tools[0]["function"]["parameters"]
                self.assertIn(
                    "repo.write_file",
                    native["properties"]["operation"]["enum"],
                )
                self.assertEqual(native["properties"]["arguments"]["type"], "object")
            finally:
                registry.close()


if __name__ == "__main__":
    unittest.main()
