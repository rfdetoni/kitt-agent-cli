"""Regression for model-proposed Begin Patch updates in KITT tool envelopes."""

import tempfile
import unittest
from pathlib import Path

from kitt.edit_format.parser import PatchParser
from kitt.edit_format.applier import DiffApplier


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
                "    for path in sorted(ROOT.glob('*.c')):\n"
                "        text = path.read_text(encoding='utf-8', errors='replace')\n"
                "        for match in definition_re.finditer(text):\n"
            )
            result = DiffApplier().apply(blocks, root_dir=tmp)
            self.assertTrue(result.success, result.errors)
            changed = target.read_text()
            self.assertIn("definition_re.finditer(uncommented)", changed)
            self.assertNotIn("definition_re.finditer(text)", changed)

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
