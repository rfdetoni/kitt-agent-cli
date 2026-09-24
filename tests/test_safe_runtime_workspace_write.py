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

    def test_write_file_auto_heals_fully_flattened_block_source(self):
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

                self.assertTrue(result.success, result.error)
                healed = (Path(temp) / "src" / "App.java").read_text(encoding="utf-8")
                self.assertRegex(healed, r"(?m)^\s+private int value;")
                self.assertRegex(healed, r"(?m)^\s+public void run\(\)")
                self.assertRegex(healed, r"(?m)^\s+System\.out\.println")
                self.assertTrue(result.metadata["formatting"]["healed"])
                self.assertEqual(result.metadata["post_edit_gate"]["skipped"], 0)
                self.assertGreaterEqual(result.metadata["post_edit_gate"]["checked"], 1)
                self.assertTrue((Path(temp) / ".kitt" / "formatting.json").is_file())
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
                rendered = (Path(temp) / "src" / "App.java").read_text(encoding="utf-8")
                self.assertIn("public class App", rendered)
                self.assertIn("System.out.println(value);", rendered)
                self.assertEqual(result.metadata["post_edit_gate"]["skipped"], 0)
            finally:
                registry.close()

    def test_apply_patch_auto_heals_existing_java_file(self):
        with tempfile.TemporaryDirectory() as temp:
            registry = self._registry(temp)
            try:
                target = Path(temp) / "App.java"
                target.write_text(
                    "public class App {\n"
                    "    public void run() {\n"
                    "        System.out.println(1);\n"
                    "    }\n"
                    "}\n",
                    encoding="utf-8",
                )
                result = registry.execute_tool(
                    "apply_patch",
                    {
                        "patch": (
                            "--- a/App.java\n"
                            "+++ b/App.java\n"
                            "@@ -1,5 +1,5 @@\n"
                            " public class App {\n"
                            "-    public void run() {\n"
                            "-        System.out.println(1);\n"
                            "-    }\n"
                            "+ public void run() {\n"
                            "+ System.out.println(2);\n"
                            "+ }\n"
                            " }\n"
                        )
                    },
                    conversation_id="conversation",
                    workspace_id="workspace",
                )
                self.assertTrue(result.success, result.error)
                rendered = target.read_text(encoding="utf-8")
                self.assertRegex(rendered, r"(?m)^\s+public void run\(\)")
                self.assertRegex(rendered, r"(?m)^\s+System\.out\.println\(2\)")
                self.assertEqual(result.metadata["post_edit_gate"]["skipped"], 0)
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
                self.assertIn(
                    "process.run {argv:[...],cwd?,timeout_seconds?,network?:bool=false}",
                    description,
                )
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



class _LeaseProbe:
    def __init__(self):
        self.calls = []

    def claim_paths(self, paths, owner_id, intent, *, wait_timeout=0.0):
        self.calls.append((list(paths), owner_id, intent, wait_timeout))
        return [
            type(
                "Grant",
                (),
                {"resource_id": f"path:{path}", "owner_id": owner_id},
            )()
            for path in paths
        ]


def test_kitt_runtime_child_write_acquires_mutation_fence(tmp_path):
    from kitt.core.autonomy_policy import AutonomyPolicy
    from kitt.security.capabilities import CAP_REPO_WRITE
    from kitt.security.context import ExecutionSecurityContext
    from kitt.tools.registry import ToolRegistry

    registry = ToolRegistry(root_dir=str(tmp_path))
    registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
    registry.policy.evaluate_tool = lambda *_args, **_kwargs: "ALLOW"
    probe = _LeaseProbe()
    registry.coordinator = probe
    context = ExecutionSecurityContext(
        workspace_id="ws",
        conversation_id="conv",
        turn_id="turn",
        origin="AGENT",
        principal_type="CHILD",
        principal_id="child-safe-runtime",
        capabilities=frozenset({CAP_REPO_WRITE}),
        trace_id="trace",
        path_scope=frozenset({"src"}),
    )
    try:
        result = registry.execute_tool(
            "kitt_runtime",
            {
                "operation": "repo.write_file",
                "arguments": {
                    "path": "src/runtime_fenced.py",
                    "content": "value = 1\n",
                },
            },
            turn_id="turn",
            conversation_id="conv",
            workspace_id="ws",
            enabled_tools=["kitt_runtime"],
            origin="AGENT",
            security_context=context,
        )
        assert result.success, result.error
        assert probe.calls == [
            (
                ["src/runtime_fenced.py"],
                "child-safe-runtime",
                "write_file mutation",
                8.0,
            )
        ]
        assert result.metadata["coordination"]["resources"] == [
            "path:src/runtime_fenced.py"
        ]
    finally:
        registry.close()


def test_child_process_fence_covers_entire_path_scope(tmp_path):
    from kitt.security.capabilities import CAP_PROCESS_RUN
    from kitt.security.context import ExecutionSecurityContext
    from kitt.tools.registry import ToolRegistry

    registry = ToolRegistry(root_dir=str(tmp_path))
    probe = _LeaseProbe()
    registry.coordinator = probe
    context = ExecutionSecurityContext(
        workspace_id="ws",
        conversation_id="conv",
        turn_id="turn",
        origin="AGENT",
        principal_type="CHILD",
        principal_id="child-process",
        capabilities=frozenset({CAP_PROCESS_RUN}),
        trace_id="trace",
        path_scope=frozenset({"backend", "frontend"}),
    )
    try:
        metadata = registry._fence_child_mutation(
            "run_command",
            {"argv": ["python", "-c", "print('probe')"]},
            context,
        )
        assert probe.calls == [
            (
                ["backend", "frontend"],
                "child-process",
                "run_command mutation",
                8.0,
            )
        ]
        assert metadata["coordination"]["resources"] == [
            "path:backend",
            "path:frontend",
        ]
    finally:
        registry.close()



def test_kitt_runtime_child_move_fences_source_and_destination(tmp_path):
    from kitt.core.autonomy_policy import AutonomyPolicy
    from kitt.security.capabilities import CAP_REPO_WRITE
    from kitt.security.context import ExecutionSecurityContext
    from kitt.tools.registry import ToolRegistry

    source = tmp_path / "src"
    source.mkdir()
    (source / "old.py").write_text("value = 1\n", encoding="utf-8")

    registry = ToolRegistry(root_dir=str(tmp_path))
    registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
    registry.policy.evaluate_tool = lambda *_args, **_kwargs: "ALLOW"
    probe = _LeaseProbe()
    registry.coordinator = probe
    context = ExecutionSecurityContext(
        workspace_id="ws",
        conversation_id="conv",
        turn_id="turn",
        origin="AGENT",
        principal_type="CHILD",
        principal_id="child-move",
        capabilities=frozenset({CAP_REPO_WRITE}),
        trace_id="trace",
        path_scope=frozenset({"src"}),
    )
    try:
        result = registry.execute_tool(
            "kitt_runtime",
            {
                "operation": "repo.move",
                "arguments": {
                    "source": "src/old.py",
                    "destination": "src/new.py",
                },
            },
            turn_id="turn",
            conversation_id="conv",
            workspace_id="ws",
            enabled_tools=["kitt_runtime"],
            origin="AGENT",
            security_context=context,
        )
        assert result.success, result.error
        assert probe.calls == [
            (
                ["src/old.py", "src/new.py"],
                "child-move",
                "repo.move mutation",
                8.0,
            )
        ]
        assert not (source / "old.py").exists()
        assert (source / "new.py").exists()
    finally:
        registry.close()
