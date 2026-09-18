import tempfile
import unittest
from pathlib import Path

from kitt.llm.providers.kitt_reverse_proxy import extract_openai_tools
from kitt.runtime.safe_runtime import OPERATION_SPECS, SafeRuntime
from kitt.security.capabilities import CAP_REPO_WRITE
from kitt.tools.registry import ToolRegistry


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

    def test_write_file_pretty_prints_json_before_persisting(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self._registry(temp)
            try:
                runtime = SafeRuntime(
                    temp,
                    "workspace",
                    "conversation",
                    tool_registry=registry,
                )
                result = runtime.execute(
                    "repo.write_file",
                    {
                        "path": "frontend/angular.json",
                        "content": '{"version":1,"projects":{"app":{"projectType":"application"}}}',
                    },
                    effective_capabilities={CAP_REPO_WRITE},
                )

                self.assertTrue(result.success, result.error)
                target = Path(temp) / "frontend" / "angular.json"
                self.assertEqual(
                    target.read_text(encoding="utf-8"),
                    '{\n  "version": 1,\n  "projects": {\n    "app": {\n      "projectType": "application"\n    }\n  }\n}\n',
                )
                self.assertTrue(result.metadata["formatting"]["normalized"])
                self.assertEqual(
                    result.metadata["formatting"]["strategy"],
                    "json.pretty",
                )
            finally:
                registry.close()

    def test_write_file_rejects_invalid_python_indentation_before_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self._registry(temp)
            try:
                runtime = SafeRuntime(
                    temp,
                    "workspace",
                    "conversation",
                    tool_registry=registry,
                )
                result = runtime.execute(
                    "repo.write_file",
                    {
                        "path": "src/app.py",
                        "content": 'def main():\nprint("broken")\n',
                    },
                    effective_capabilities={CAP_REPO_WRITE},
                )

                self.assertFalse(result.success)
                self.assertIn("indentation", result.error.lower())
                self.assertFalse((Path(temp) / "src" / "app.py").exists())
            finally:
                registry.close()

    def test_write_file_rejects_fully_flattened_block_source(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self._registry(temp)
            try:
                runtime = SafeRuntime(
                    temp,
                    "workspace",
                    "conversation",
                    tool_registry=registry,
                )
                flattened = (
                    "package demo;\n"
                    "public class App {\n"
                    "private int value;\n"
                    "public void run() {\n"
                    "System.out.println(value);\n"
                    "}\n"
                    "}\n"
                )
                result = runtime.execute(
                    "repo.write_file",
                    {"path": "src/App.java", "content": flattened},
                    effective_capabilities={CAP_REPO_WRITE},
                )

                self.assertFalse(result.success)
                self.assertIn("fully left-aligned", result.error)
                self.assertFalse((Path(temp) / "src" / "App.java").exists())
            finally:
                registry.close()

    def test_write_file_accepts_readably_indented_block_source(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self._registry(temp)
            try:
                runtime = SafeRuntime(
                    temp,
                    "workspace",
                    "conversation",
                    tool_registry=registry,
                )
                source = (
                    "package demo;\n"
                    "public class App {\n"
                    "    private int value;\n"
                    "    public void run() {\n"
                    "        System.out.println(value);\n"
                    "    }\n"
                    "}\n"
                )
                result = runtime.execute(
                    "repo.write_file",
                    {"path": "src/App.java", "content": source},
                    effective_capabilities={CAP_REPO_WRITE},
                )

                self.assertTrue(result.success, result.error)
                self.assertEqual(
                    (Path(temp) / "src" / "App.java").read_text(encoding="utf-8"),
                    source,
                )
                self.assertFalse(result.metadata["formatting"]["normalized"])
            finally:
                registry.close()

    def test_write_file_without_path_is_rejected_with_retry_guidance(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self._registry(temp)
            try:
                result = registry.execute_tool(
                    "write_file",
                    {"content": "=== PROJECT STRUCTURE ===\n"},
                    enabled_tools=["write_file"],
                )
                self.assertFalse(result.success)
                self.assertIn("path", result.error)
                self.assertIn("repo.write_file", result.error)
                self.assertIn("retry", result.error.lower())
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
                description = definition["description"]
                self.assertIn("process.run {argv:[...],cwd?,timeout_seconds?}", description)
                self.assertIn("argv-only/no shell", description)
                self.assertIn("repo.write_file or patch.apply", description)
                self.assertEqual(
                    definition["args"]["operation"],
                    "runtime operation",
                )
                argument_schema = definition["args"]["arguments"]
                self.assertEqual(argument_schema["type"], "object")
                self.assertIn(
                    "process.run never accepts command/cmd/args",
                    argument_schema["description"],
                )

                tools = extract_openai_tools(f"Available host tools: {[definition]}")
                native = tools[0]["function"]["parameters"]
                self.assertIn(
                    "repo.write_file",
                    native["properties"]["operation"]["enum"],
                )
                self.assertIn(
                    "process.run",
                    native["properties"]["operation"]["enum"],
                )
                self.assertEqual(native["properties"]["arguments"]["type"], "object")
                self.assertIn(
                    "process.run never accepts command/cmd/args",
                    native["properties"]["arguments"]["description"],
                )
            finally:
                registry.close()


if __name__ == "__main__":
    unittest.main()
