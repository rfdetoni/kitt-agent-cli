"""Regression for model-proposed Begin Patch updates in KITT tool envelopes."""

import tempfile
import unittest
from pathlib import Path

from kitt.edit_format.parser import PatchParser
from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.strategy import strategy_for_tool_call


class BeginPatchUpdateTests(unittest.TestCase):
    def test_win32u_reconcile_patch_from_incident_is_supported(self):
        patch = r"""*** Begin Patch
*** Update File: reports/t01-win32u-reconcile.py
@@
     for path in sorted(ROOT.glob('*.c')):
         text = path.read_text(encoding='utf-8', errors='replace')
-        for match in definition_re.finditer(text):
+        # Preserve offsets while removing C comments.
+        uncommented = re.sub(r'/\*.*?\*/',
+                             lambda match: re.sub(r'[^\n]', ' ', match.group()),
+                             text, flags=re.S)
+        for match in definition_re.finditer(uncommented):
*** End Patch"""
        parser = PatchParser()
        self.assertEqual(parser.format(patch), "begin_patch")
        self.assertEqual(
            strategy_for_tool_call("kitt_runtime", {
                "operation": "patch.apply", "arguments": {"patch": patch},
            }), "unified_diff",
        )
        blocks = parser.parse(patch)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].file_path, "reports/t01-win32u-reconcile.py")
        self.assertIn("definition_re.finditer(text)", blocks[0].search_content)
        self.assertIn("definition_re.finditer(uncommented)", blocks[0].replace_content)
        self.assertFalse(blocks[0].is_new_file)

        # Run the existing guarded applier, not a separate patch engine.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "reports", "t01-win32u-reconcile.py")
            target.parent.mkdir()
            target.write_text(
                "import re\n"
                "def scan():\n"
                "    for path in sorted(ROOT.glob('*.c')):\n"
                "        text = path.read_text(encoding='utf-8', errors='replace')\n"
                "        for match in definition_re.finditer(text):\n"
                "            pass\n"
            )
            result = DiffApplier().apply(blocks, root_dir=tmp)
            self.assertTrue(result.success, result.errors)
            changed = target.read_text()
            self.assertIn("definition_re.finditer(uncommented)", changed)
            self.assertNotIn("definition_re.finditer(text)", changed)

    def test_navigation_context_only_header_before_multiple_changed_hunks(self):
        # Reproduction: 2026-10-21 mbs-wine/bootstrap.py uses a separate
        # @@ section only to navigate to def main(), before actual edits.
        patch = """*** Begin Patch
*** Update File: mbs-wine/bootstrap.py
@@
 def main():
@@
     for name, expected, alternatives in SOURCES:
         target = ROOT / "upstream" / name
+        if target.is_symlink():
+            raise SystemExit("BLOCKED_ENV: symbolic link destination")
         target.mkdir(exist_ok=True)
@@
             existing = next((p for p in alternatives if p.is_dir() and checkout_info(p, expected)), None)
             if existing:
                 ok, detail = git("clone", "--no-hardlinks", str(existing), str(target), timeout=1200)
-                status = "CLONED_FROM_LOCAL" if ok else "BLOCKED_ENV: " + detail[:160]
+                if ok:
+                    ok, detail = git("remote", "set-url", "origin", expected, cwd=target)
+                status = "CLONED_FROM_LOCAL" if ok else "BLOCKED_ENV: " + detail[:160]
@@
     for name, url, path, status, info in rows:
+        sha = f"`{info['sha']}`" if info else "UNVERIFIED"
         lines.append(f"| {name} | {url} | {path} | "
                      f"{info['branch'] if info else 'UNVERIFIED'} | "
-                     f"{info['sha']}" * bool(info) + ("UNVERIFIED" if not info else "") +
-                     f" | {info['state'] if info else 'UNVERIFIED'} |")
+                     f"{sha} | {info['state'] if info else 'UNVERIFIED'} |")
*** End Patch"""
        parser = PatchParser()
        blocks = parser.parse(patch)
        self.assertEqual(len(blocks), 3)
        self.assertTrue(all(block.file_path == "mbs-wine/bootstrap.py" for block in blocks))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "mbs-wine", "bootstrap.py")
            target.parent.mkdir()
            target.write_text(
                "def main():\n"
                "    for name, expected, alternatives in SOURCES:\n"
                '        target = ROOT / "upstream" / name\n'
                "        target.mkdir(exist_ok=True)\n"
                "            existing = next((p for p in alternatives if p.is_dir() and checkout_info(p, expected)), None)\n"
                "            if existing:\n"
                '                ok, detail = git("clone", "--no-hardlinks", str(existing), str(target), timeout=1200)\n'
                '                status = "CLONED_FROM_LOCAL" if ok else "BLOCKED_ENV: " + detail[:160]\n'
                "    for name, url, path, status, info in rows:\n"
                '        lines.append(f"| {name} | {url} | {path} | "\n'
                '                     f"{info[\'branch\'] if info else \'UNVERIFIED\'} | "\n'
                '                     f"{info[\'sha\']}" * bool(info) + ("UNVERIFIED" if not info else "") +\n'
                '                     f" | {info[\'state\'] if info else \'UNVERIFIED\'} |")\n'
            )
            result = DiffApplier().apply(blocks, root_dir=tmp)
            self.assertTrue(result.success, result.errors)
            changed = target.read_text()
            self.assertIn('target.is_symlink()', changed)
            self.assertIn('git("remote", "set-url"', changed)
            self.assertIn('f"{sha} |', changed)

    def test_navigation_sections_without_any_mutation_are_rejected(self):
        patch = """*** Begin Patch
*** Update File: mbs-wine/bootstrap.py
@@
 def main():
@@
     for name, expected, alternatives in SOURCES:
*** End Patch"""
        self.assertEqual(PatchParser().parse(patch), [])

    def test_two_files_and_hunks_have_stable_targets(self):
        patch = """*** Begin Patch
*** Update File: src/a.py
@@
-old
+new
@@
 other
-end
+finish
*** Update File: src/b.py
@@
-eins
+zwei
*** End Patch"""
        blocks = PatchParser().parse(patch)
        self.assertEqual(
            [block.file_path for block in blocks],
            ["src/a.py", "src/a.py", "src/b.py"],
        )

    def test_rejects_unanchored_and_unsupported_operations(self):
        invalid = (
            "*** Begin Patch\n*** Update File: x.py\n@@\n+only addition\n*** End Patch",
            "*** Begin Patch\n*** Update File: x.py\n@@\n same\n*** End Patch",
            "*** Begin Patch\n*** Add File: x.py\n+new\n*** End Patch",
            "*** Begin Patch\n*** Delete File: x.py\n*** End Patch",
            "*** Begin Patch\n*** Update File: x.py\n@@\n-wrong\n+right\n",
            "*** Begin Patch\n*** Update File: x.py\n@@\n-wrong\n+right\n*** End Patch\nextra",
        )
        for patch in invalid:
            with self.subTest(patch=patch):
                self.assertEqual(PatchParser().parse(patch), [])

    def test_cannot_edit_outside_workspace(self):
        patch = """*** Begin Patch
*** Update File: ../outside.py
@@
-old
+new
*** End Patch"""
        blocks = PatchParser().parse(patch)
        self.assertEqual(len(blocks), 1)
        with tempfile.TemporaryDirectory() as tmp:
            result = DiffApplier().apply(blocks, root_dir=tmp)
            self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
