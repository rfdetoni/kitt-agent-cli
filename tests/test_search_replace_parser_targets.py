"""Regression tests for explicit SEARCH/REPLACE patch targets."""

import unittest

from kitt.edit_format.parser import PatchParser, SearchReplaceParser


class SearchReplaceTargetTests(unittest.TestCase):
    def test_multiple_blocks_keep_the_initial_file_not_a_version_number(self):
        patch = """reports/t01-win32u-first-batch.md
<<<<<<< SEARCH
Estado: INCOMPLETE
=======
Estado: VERIFICADO
>>>>>>> REPLACE
<<<<<<< SEARCH
Baseline Wine-MBS 11.20, not the checkout.
=======
Baseline Wine-MBS 11.20, not the checkout.
>>>>>>> REPLACE
<<<<<<< SEARCH
## PENDING
=======
## SELECTED
>>>>>>> REPLACE"""
        blocks = PatchParser().parse(patch)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(
            {block.file_path for block in blocks},
            {"reports/t01-win32u-first-batch.md"},
        )
        self.assertTrue(all(not block.is_new_file for block in blocks))

    def test_explicit_new_file_header_changes_target(self):
        patch = """src/a.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE
src/b.py
<<<<<<< SEARCH
one
=======
two
>>>>>>> REPLACE
<<<<<<< SEARCH
three
=======
four
>>>>>>> REPLACE"""
        blocks = SearchReplaceParser().parse(patch)
        self.assertEqual([block.file_path for block in blocks], [
            "src/a.py", "src/b.py", "src/b.py",
        ])

    def test_patch_directive_changes_target(self):
        patch = """*** Begin Patch
*** Update File: src/a.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE
*** Update File: src/b.py
<<<<<<< SEARCH
one
=======
two
>>>>>>> REPLACE
*** End Patch"""
        blocks = PatchParser().parse(patch)
        self.assertEqual([block.file_path for block in blocks], [
            "src/a.py", "src/b.py",
        ])

    def test_unlabeled_block_does_not_guess_path_from_body(self):
        patch = """<<<<<<< SEARCH
Wine-MBS 11.20.
=======
Version 11.20.
>>>>>>> REPLACE"""
        self.assertEqual(SearchReplaceParser().parse(patch), [])

    def test_numeric_first_line_is_not_a_file_path(self):
        self.assertEqual(SearchReplaceParser().parse("11.20\ncontents"), [])

    def test_whole_file_fallback_requires_an_explicit_path(self):
        parser = SearchReplaceParser()
        self.assertEqual(parser.parse("See src/a.py\ncontents"), [])
        blocks = parser.parse("src/a.py\nprint('ok')\n")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].file_path, "src/a.py")


if __name__ == "__main__":
    unittest.main()
