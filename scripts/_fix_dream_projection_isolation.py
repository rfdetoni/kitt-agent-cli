from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


repo_path = Path("kitt/dreaming/repository.py")
repo = repo_path.read_text(encoding="utf-8")
repo = replace_once(
    repo,
    '        """Reconstructs the human-readable .kitt/memory/MEMORY.md projection from SQLite."""\n',
    '        """Reconstructs the workspace-scoped human-readable memory projection."""\n',
    "projection docstring",
)
repo = replace_once(
    repo,
    '            target = Path(root_dir) / ".kitt" / "memory" / "MEMORY.md"\n',
    '''            target = (\n                Path(root_dir)\n                / ".kitt"\n                / "workspaces"\n                / workspace_id\n                / "memory"\n                / "MEMORY.md"\n            )\n''',
    "workspace projection target",
)
repo_path.write_text(repo, encoding="utf-8")


test_path = Path("tests/dreaming/test_e2e.py")
test = test_path.read_text(encoding="utf-8")
test = replace_once(
    test,
    "from pathlib import Path\nimport tempfile\nimport time\n",
    "from pathlib import Path\nimport tempfile\nimport time\nfrom unittest.mock import patch\n",
    "patch import",
)
test = replace_once(
    test,
    '''        self.root = Path(self.tmp.name)\n        self.config = RuntimeConfig(\n            dream_enabled=True,\n            dream_auto_enabled=False,\n            persistence_enabled=True,\n            history_enabled=True,\n        )\n        self.runtime = KittRuntime.build(str(self.root), config=self.config)\n        self.workspace_id = self.runtime.workspace_id\n\n    def tearDown(self):\n        self.runtime.close()\n        self.tmp.cleanup()\n''',
    '''        self.root = Path(self.tmp.name)\n        self.home = self.root / "home"\n        self.workspace = self.root / "workspace"\n        self.home.mkdir()\n        self.workspace.mkdir()\n        self._runtime_home = patch("kitt.core.runtime.Path.home", return_value=self.home)\n        self._private_home = patch("kitt.security.private_state.Path.home", return_value=self.home)\n        self._runtime_home.start()\n        self._private_home.start()\n        self.config = RuntimeConfig(\n            dream_enabled=True,\n            dream_auto_enabled=False,\n            persistence_enabled=True,\n            history_enabled=True,\n        )\n        self.runtime = KittRuntime.build(str(self.workspace), config=self.config)\n        self.workspace_id = self.runtime.workspace_id\n\n    def tearDown(self):\n        self.runtime.close()\n        self._private_home.stop()\n        self._runtime_home.stop()\n        self.tmp.cleanup()\n''',
    "isolated home setup",
)
test = replace_once(
    test,
    '''        # 3. Check materialized view (.kitt/memory/MEMORY.md)\n        mem_file = self.root / ".kitt" / "memory" / "MEMORY.md"\n        self.assertTrue(mem_file.exists())\n''',
    '''        # 3. Check the workspace-scoped materialized view under private home state.\n        mem_file = (\n            self.home\n            / ".kitt"\n            / "workspaces"\n            / self.workspace_id\n            / "memory"\n            / "MEMORY.md"\n        )\n        self.assertTrue(mem_file.exists())\n        self.assertFalse((self.home / ".kitt" / "memory" / "MEMORY.md").exists())\n        self.assertFalse((self.workspace / ".kitt").exists())\n''',
    "projection assertion",
)
test_path.write_text(test, encoding="utf-8")
