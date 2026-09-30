import tempfile
import unittest
from pathlib import Path

from kitt.skills.discovery import SkillDiscovery


def _skill(path: Path, name: str, body: str = "Use this skill.") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name}\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


class SkillDiscoveryLimitTests(unittest.TestCase):
    def test_depth_limit_is_applied_before_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "catalog"
            _skill(root / "one", "one")
            _skill(root / "deep" / "nested" / "skill", "too-deep")

            discovery = SkillDiscovery(
                max_roots=1,
                max_depth=2,
                max_files=20,
                max_file_bytes=4096,
                max_total_bytes=8192,
            )
            names = {item.name for item in discovery.discover([root])}

            self.assertIn("one", names)
            self.assertNotIn("too-deep", names)

    def test_file_and_total_byte_limits_precede_semantic_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "catalog"
            _skill(root / "small", "small", "ok")
            _skill(root / "large", "large", "x" * 4096)

            discovery = SkillDiscovery(
                max_roots=1,
                max_depth=4,
                max_files=10,
                max_file_bytes=512,
                max_total_bytes=1024,
            )
            names = {item.name for item in discovery.discover([root])}

            self.assertIn("small", names)
            self.assertNotIn("large", names)

    def test_root_limit_prevents_unbounded_catalog_fanout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            _skill(first / "a", "a")
            _skill(second / "b", "b")

            discovery = SkillDiscovery(
                max_roots=1,
                max_depth=4,
                max_files=20,
                max_file_bytes=4096,
                max_total_bytes=8192,
            )
            names = {item.name for item in discovery.discover([first, second])}

            self.assertEqual(names, {"a"})


if __name__ == "__main__":
    unittest.main()
