import hashlib
import tempfile
import unittest
from pathlib import Path

from kitt.runtime.safe_runtime import OPERATION_SPECS, SafeRuntime
from kitt.security.capabilities import (
    CAP_CONTROL_PLANE_WRITE,
    CAP_REPO_READ,
    CAP_REPO_WRITE,
)


class TestSafeRuntimeFilesystemOps(unittest.TestCase):
    def _runtime(self, root: str) -> SafeRuntime:
        return SafeRuntime(root, "workspace", "conversation")

    def test_list_returns_files_and_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "README.md").write_text("hello", encoding="utf-8")
            runtime = self._runtime(tmp)

            result = runtime.execute(
                "repo.list",
                {"path": "."},
                effective_capabilities={CAP_REPO_READ},
            )

            self.assertTrue(result.success, result.error)
            by_path = {entry["path"]: entry["type"] for entry in result.data["entries"]}
            self.assertEqual(by_path["src"], "directory")
            self.assertEqual(by_path["README.md"], "file")

    def test_list_honors_depth_and_hides_kitt_diagnostic_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend" / "src" / "main").mkdir(parents=True)
            (root / "backend" / "src" / "main" / "App.java").write_text(
                "class App {}\n",
                encoding="utf-8",
            )
            (root / "kitt-agent-full.log").write_text("trace", encoding="utf-8")
            (root / "kitt-reverse-proxy-full.log").write_text("trace", encoding="utf-8")
            runtime = self._runtime(tmp)

            shallow = runtime.execute(
                "repo.list",
                {"path": ".", "depth": 1},
                effective_capabilities={CAP_REPO_READ},
            )
            self.assertTrue(shallow.success, shallow.error)
            shallow_paths = {entry["path"] for entry in shallow.data["entries"]}
            self.assertIn("backend", shallow_paths)
            self.assertNotIn("backend/src", shallow_paths)
            self.assertNotIn("kitt-agent-full.log", shallow_paths)
            self.assertNotIn("kitt-reverse-proxy-full.log", shallow_paths)

            recursive = runtime.execute(
                "repo.list",
                {"path": ".", "recursive": True, "max_depth": 4},
                effective_capabilities={CAP_REPO_READ},
            )
            self.assertTrue(recursive.success, recursive.error)
            recursive_paths = {entry["path"] for entry in recursive.data["entries"]}
            self.assertIn("backend/src", recursive_paths)
            self.assertIn("backend/src/main", recursive_paths)
            self.assertIn("backend/src/main/App.java", recursive_paths)
            self.assertEqual(recursive.data["depth"], 4)

    def test_move_and_rename_file_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "old.txt"
            source.write_text("payload", encoding="utf-8")
            digest = hashlib.sha256(b"payload").hexdigest()
            runtime = self._runtime(tmp)

            moved = runtime.execute(
                "repo.move",
                {
                    "source": "old.txt",
                    "destination": "nested/new.txt",
                    "expected_content_hash": digest,
                },
                effective_capabilities={CAP_REPO_WRITE},
            )
            self.assertTrue(moved.success, moved.error)
            self.assertFalse(source.exists())
            self.assertEqual((root / "nested/new.txt").read_text(encoding="utf-8"), "payload")

            (root / "occupied.txt").write_text("keep", encoding="utf-8")
            blocked = runtime.execute(
                "repo.rename",
                {"source": "nested/new.txt", "destination": "occupied.txt"},
                effective_capabilities={CAP_REPO_WRITE},
            )
            self.assertFalse(blocked.success)
            self.assertEqual((root / "occupied.txt").read_text(encoding="utf-8"), "keep")

    def test_delete_file_and_directory_requires_explicit_recursive_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "folder").mkdir()
            (root / "folder/item.txt").write_text("x", encoding="utf-8")
            runtime = self._runtime(tmp)

            blocked = runtime.execute(
                "repo.delete",
                {"path": "folder"},
                effective_capabilities={CAP_REPO_WRITE},
            )
            self.assertFalse(blocked.success)
            self.assertTrue((root / "folder/item.txt").exists())

            deleted = runtime.execute(
                "repo.delete",
                {"path": "folder", "recursive": True},
                effective_capabilities={CAP_REPO_WRITE},
            )
            self.assertTrue(deleted.success, deleted.error)
            self.assertFalse((root / "folder").exists())

    def test_symlinks_and_workspace_root_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root.parent / f"{root.name}-outside"
            outside.write_text("outside", encoding="utf-8")
            try:
                link = root / "link.txt"
                try:
                    link.symlink_to(outside)
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks unavailable on this platform")
                runtime = self._runtime(tmp)
                delete_link = runtime.execute(
                    "repo.delete",
                    {"path": "link.txt"},
                    effective_capabilities={CAP_REPO_WRITE},
                )
                self.assertFalse(delete_link.success)
                self.assertTrue(outside.exists())
                delete_root = runtime.execute(
                    "repo.delete",
                    {"path": ".", "recursive": True},
                    effective_capabilities={CAP_REPO_WRITE},
                )
                self.assertFalse(delete_root.success)
            finally:
                outside.unlink(missing_ok=True)

    def test_control_plane_delete_requires_dedicated_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = root / ".kitt"
            control.mkdir()
            target = control / "security.json"
            target.write_text("{}", encoding="utf-8")
            runtime = self._runtime(tmp)

            blocked = runtime.execute(
                "repo.delete",
                {"path": ".kitt/security.json"},
                effective_capabilities={CAP_REPO_WRITE},
            )
            self.assertFalse(blocked.success)
            self.assertTrue(blocked.requires_approval)
            self.assertEqual(
                blocked.required_capability,
                CAP_CONTROL_PLANE_WRITE,
            )
            self.assertTrue(target.exists())

            allowed = runtime.execute(
                "repo.delete",
                {"path": ".kitt/security.json"},
                effective_capabilities={
                    CAP_REPO_WRITE,
                    CAP_CONTROL_PLANE_WRITE,
                },
            )
            self.assertTrue(allowed.success, allowed.error)
            self.assertFalse(target.exists())

    def test_runtime_specs_mark_mutations_sensitive(self):
        self.assertEqual(OPERATION_SPECS["repo.list"].required_capability, CAP_REPO_READ)
        for operation in ("repo.move", "repo.rename", "repo.delete"):
            spec = OPERATION_SPECS[operation]
            self.assertEqual(spec.required_capability, CAP_REPO_WRITE)
            self.assertTrue(spec.sensitive)
            self.assertEqual(spec.policy_tool_action, "write_file")


if __name__ == "__main__":
    unittest.main()
