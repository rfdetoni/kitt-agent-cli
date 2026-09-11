from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_all_required(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"{path}: missing expected pattern {old!r}")
    p.write_text(text.replace(old, new), encoding="utf-8")


# Deterministic planner: recognize the exact natural-language folder request and expose the native tool.
replace_once(
    "kitt/context_filter/fallback.py",
    '("crie o arquivo", "crie um arquivo", "crie a pasta", "execute", "rode")',
    '("crie o arquivo", "crie um arquivo", "crie a pasta", "crie uma pasta", "crie o diretório", "crie um diretório", "execute", "rode")',
)
replace_once(
    "kitt/context_filter/fallback.py",
    '        tools = [\n            "write_file",',
    '        tools = [\n            "create_directory",\n            "write_file",',
)

# Capability and policy: directory creation is a repository write, never a process capability.
replace_once(
    "kitt/security/capabilities.py",
    '    "write_file": CAP_REPO_WRITE,',
    '    "create_directory": CAP_REPO_WRITE,\n    "write_file": CAP_REPO_WRITE,',
)
for old, new in (
    ('{"apply_patch", "write_file"}', '{"apply_patch", "write_file", "create_directory"}'),
    ('{"apply_patch", "write_file", "run_command", "child_spawn", "child"}', '{"apply_patch", "write_file", "create_directory", "run_command", "child_spawn", "child"}'),
):
    replace_all_required("kitt/tools/policy_engine.py", old, new)

# Canonical filesystem trust boundary: create one component at a time and refuse symlink/reparse traversal.
workspace_method = '''    def create_directory(\n        self, rel: str | Path, *, parents: bool = True, exist_ok: bool = True\n    ) -> str:\n        """Create a real directory inside the workspace without invoking a shell."""\n        parts = self._normalize(rel)\n        rel_path = "/".join(parts) if parts else "."\n        if not parts:\n            if exist_ok:\n                return rel_path\n            raise FileExistsError(str(self.root))\n\n        if os.name == "nt":\n            current = self.root\n            for index, component in enumerate(parts):\n                current = current / component\n                final = index == len(parts) - 1\n                created = False\n                try:\n                    st = current.lstat()\n                except FileNotFoundError:\n                    if not parents and not final:\n                        raise\n                    current.mkdir()\n                    created = True\n                    st = current.lstat()\n                if self._windows_reparse_point(st) or not stat.S_ISDIR(st.st_mode):\n                    raise PermissionError(\n                        f"Workspace directory traversal refused: {current}"\n                    )\n                if final and not created and not exist_ok:\n                    raise FileExistsError(str(current))\n            return rel_path\n\n        fd = self._open_root_fd()\n        try:\n            for index, component in enumerate(parts):\n                final = index == len(parts) - 1\n                flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))\n                flags |= int(getattr(os, "O_NOFOLLOW", 0))\n                flags |= int(getattr(os, "O_CLOEXEC", 0))\n                created = False\n                try:\n                    next_fd = os.open(component, flags, dir_fd=fd)\n                except FileNotFoundError:\n                    if not parents and not final:\n                        raise\n                    os.mkdir(component, 0o755, dir_fd=fd)\n                    created = True\n                    next_fd = os.open(component, flags, dir_fd=fd)\n                except OSError as exc:\n                    if exc.errno in (\n                        getattr(errno, "ELOOP", 40),\n                        getattr(errno, "EMLINK", 31),\n                    ):\n                        raise PermissionError(\n                            f"Workspace symlink traversal refused: {component}"\n                        ) from exc\n                    raise\n                try:\n                    if not stat.S_ISDIR(os.fstat(next_fd).st_mode):\n                        raise PermissionError(\n                            f"Workspace component is not a directory: {component}"\n                        )\n                    if final and not created and not exist_ok:\n                        raise FileExistsError(component)\n                except Exception:\n                    os.close(next_fd)\n                    raise\n                os.close(fd)\n                fd = next_fd\n            return rel_path\n        finally:\n            os.close(fd)\n\n'''
replace_once(
    "kitt/security/workspace_fs.py",
    "    def is_safe_directory(self, rel: str | Path) -> bool:\n",
    workspace_method + "    def is_safe_directory(self, rel: str | Path) -> bool:\n",
)

# Native handler delegates all path semantics to WorkspaceFileSystem and scope checks.
handler = '''class CreateDirectoryHandler:\n    def execute(self, args: Dict[str, Any], ctx: ToolContext):\n        from kitt.tools.registry import ToolResult\n\n        requested = str(args.get("path", "") or "").strip()\n        if not requested:\n            return ToolResult(False, "", "Argument 'path' is required.")\n        try:\n            fs = _fs(ctx)\n            relative = _scope(ctx, fs.relative(requested))\n            created = fs.create_directory(relative, parents=True, exist_ok=True)\n        except (OSError, PermissionError, ValueError) as exc:\n            return ToolResult(False, "", f"Workspace directory creation refused: {exc}")\n        return ToolResult(\n            True,\n            f"Created directory {created}.",\n            metadata={"path": created, "changed_paths": [created]},\n        )\n\n\n'''
replace_once(
    "kitt/tools/handlers/files.py",
    "class WriteFileHandler:",
    handler + "class WriteFileHandler:",
)

# Registry surface: dedicated filesystem mutation tool; run_command is explicitly shell-less.
replace_once(
    "kitt/tools/registry_core.py",
    "from kitt.tools.handlers.files import ListFilesHandler, ReadFileHandler, WriteFileHandler",
    "from kitt.tools.handlers.files import (CreateDirectoryHandler, ListFilesHandler, ReadFileHandler, WriteFileHandler)",
)
replace_once(
    "kitt/tools/registry_core.py",
    '            "write_file": WriteFileHandler(),',
    '            "create_directory": CreateDirectoryHandler(),\n            "write_file": WriteFileHandler(),',
)
replace_once(
    "kitt/tools/registry_core.py",
    '            {"name": "write_file", "description": "Create or overwrite content to a file"},',
    '            {"name": "create_directory", "description": "Create a directory inside the workspace without invoking a shell"},\n            {"name": "write_file", "description": "Create or overwrite content to a file"},',
)
replace_once(
    "kitt/tools/registry_core.py",
    '            {"name": "run_command", "description": "Run shell command within security policy"},',
    '            {"name": "run_command", "description": "Run an executable directly without a shell; prefer dedicated filesystem tools for mutations"},',
)
replace_once(
    "kitt/tools/registry_core.py",
    '            "write_file": {\n                "path": "relative file",',
    '            "create_directory": {"path": "relative directory"},\n            "write_file": {\n                "path": "relative file",',
)
replace_once(
    "kitt/tools/registry_core.py",
    '                "(repo.*, artifacts.*, patch.*, process.*, children.*, goal.*, memory.*, state.*, handles.*)."',
    '                "(repo.*, artifacts.*, patch.*, process.*, children.*, goal.*, memory.*, state.*, handles.*), including repo.create_directory."',
)
replace_once(
    "kitt/tools/registry_core.py",
    '                "command": "shell command allowed by policy",',
    '                "command": "executable and arguments allowed by policy (no shell)",',
)

# Safe runtime operation used by the compact kitt_runtime model-facing surface.
replace_once(
    "kitt/runtime/core_runtime.py",
    '    "patch.apply": RuntimeOperationSpec(\n',
    '    "repo.create_directory": RuntimeOperationSpec(\n        "repo.create_directory",\n        CAP_REPO_WRITE,\n        "create_directory",\n        sensitive=True,\n        resume_tool_name="create_directory",\n    ),\n    "patch.apply": RuntimeOperationSpec(\n',
)
replace_once(
    "kitt/runtime/core_runtime.py",
    '            "patch.apply": lambda: self._op_registry_tool("patch.apply", "apply_patch", args, turn_id, origin, security_context, grant, expected_approval_id),',
    '            "repo.create_directory": lambda: self._op_registry_tool("repo.create_directory", "create_directory", args, turn_id, origin, security_context, grant, expected_approval_id),\n            "patch.apply": lambda: self._op_registry_tool("patch.apply", "apply_patch", args, turn_id, origin, security_context, grant, expected_approval_id),',
)
replace_once(
    "kitt/runtime/core_runtime.py",
    '            elif result.success and op in {"repo.edit_symbol", "patch.apply"}:',
    '            elif result.success and op in {"repo.edit_symbol", "repo.create_directory", "patch.apply"}:',
)

# A missing executable is a normal tool failure, never an uncaught turn/process failure.
replace_once(
    "kitt/tools/handlers/system.py",
    '        result = ctx.registry.process_runner.run(argv, timeout_seconds=30)\n        output, metadata, compacted = _optimized_process_output(',
    '        try:\n            result = ctx.registry.process_runner.run(argv, timeout_seconds=30)\n        except FileNotFoundError as exc:\n            return ToolResult(False, "", f"Executable not found: {exc}")\n        except OSError as exc:\n            return ToolResult(False, "", f"Command execution failed: {exc}")\n        output, metadata, compacted = _optimized_process_output(',
)

# Regression tests: exact user phrase, trust boundary and compact runtime contract.
Path("tests/test_directory_creation.py").write_text('''import tempfile\nimport unittest\nfrom pathlib import Path\n\nfrom kitt.context_filter.fallback import DeterministicFallbackPlanner\nfrom kitt.runtime.safe_runtime import OPERATION_SPECS\nfrom kitt.security.workspace_fs import WorkspaceFileSystem\n\n\nclass DirectoryCreationRegressionTests(unittest.TestCase):\n    def test_exact_portuguese_prompt_is_mutating_and_exposes_native_tool(self):\n        planner = DeterministicFallbackPlanner()\n        task = planner.generate_task("crie uma pasta chamada testePasta")\n        self.assertEqual(task.intent, "IMPLEMENT")\n        self.assertIn("create_directory", planner.generate_plan(task).enabled_tools)\n\n    def test_workspace_filesystem_creates_directory_without_shell(self):\n        with tempfile.TemporaryDirectory() as temp:\n            root = Path(temp)\n            fs = WorkspaceFileSystem(root)\n            relative = fs.create_directory("testePasta")\n            self.assertEqual(relative, "testePasta")\n            self.assertTrue((root / "testePasta").is_dir())\n            self.assertEqual(fs.create_directory("testePasta"), "testePasta")\n\n    def test_directory_creation_rejects_escape_and_protected_paths(self):\n        with tempfile.TemporaryDirectory() as temp:\n            fs = WorkspaceFileSystem(temp)\n            with self.assertRaises(PermissionError):\n                fs.create_directory("../fora")\n            with self.assertRaises(PermissionError):\n                fs.create_directory(".git/hooks")\n\n    def test_safe_runtime_exposes_directory_operation(self):\n        spec = OPERATION_SPECS["repo.create_directory"]\n        self.assertEqual(spec.policy_tool_action, "create_directory")\n        self.assertEqual(spec.resume_tool_name, "create_directory")\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")
