import tempfile
import unittest
from pathlib import Path
from kitt.domain.entities import EditBlock
from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.parser import SearchReplaceParser

class TestDiffApplierOverwrite(unittest.TestCase):
    def test_overwrite_existing_file_with_empty_search_block(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            target_file = Path(tmp_dir) / "page.html"
            target_file.write_text("<h1>Old Content</h1>", encoding="utf-8")

            applier = DiffApplier()
            # EditBlock with empty search_content (is_new_file=True) targeting an existing file
            blocks = [
                EditBlock(
                    file_path="page.html",
                    search_content="",
                    replace_content="<h1>New Overwritten Content</h1>",
                    is_new_file=True,
                    is_deletion=False
                )
            ]

            result = applier.apply(blocks, root_dir=tmp_dir, allow_overwrite_existing=True)

            self.assertTrue(result.success, f"Apply failed with errors: {result.errors}")
            self.assertEqual(target_file.read_text(encoding="utf-8"), "<h1>New Overwritten Content</h1>")
            self.assertIn("page.html", result.applied_files)

    def test_new_json_file_is_pretty_printed_before_changeset_commit(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            applier = DiffApplier()
            blocks = [
                EditBlock(
                    file_path="frontend/angular.json",
                    search_content="",
                    replace_content='{"version":1,"projects":{"app":{"projectType":"application"}}}',
                    is_new_file=True,
                )
            ]

            result = applier.apply(blocks, root_dir=tmp_dir)

            self.assertTrue(result.success, result.errors)
            self.assertEqual(
                (Path(tmp_dir) / "frontend" / "angular.json").read_text(encoding="utf-8"),
                '{\n  "version": 1,\n  "projects": {\n    "app": {\n      "projectType": "application"\n    }\n  }\n}\n',
            )

    def test_new_flattened_java_file_is_rejected_before_creation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            applier = DiffApplier()
            blocks = [
                EditBlock(
                    file_path="src/App.java",
                    search_content="",
                    replace_content=(
                        "package demo;\n"
                        "public class App {\n"
                        "private int value;\n"
                        "public void run() {\n"
                        "System.out.println(value);\n"
                        "}\n"
                        "}\n"
                    ),
                    is_new_file=True,
                )
            ]

            result = applier.apply(blocks, root_dir=tmp_dir)

            self.assertFalse(result.success)
            self.assertIn("fully left-aligned", result.errors[0])
            self.assertFalse((Path(tmp_dir) / "src" / "App.java").exists())

    def test_search_replace_parser_overwrite_flow(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            target_file = Path(tmp_dir) / "app.py"
            target_file.write_text("print('old')", encoding="utf-8")

            parser = SearchReplaceParser()
            patch_text = "app.py\n<<<<<<< SEARCH\n=======\nprint('new')\n>>>>>>> REPLACE"
            blocks = parser.parse(patch_text)
            self.assertEqual(len(blocks), 1)
            self.assertTrue(blocks[0].is_new_file)

            applier = DiffApplier()
            result = applier.apply(blocks, root_dir=tmp_dir, allow_overwrite_existing=True)
            self.assertTrue(result.success)
            self.assertEqual(target_file.read_text(encoding="utf-8"), "print('new')")

if __name__ == "__main__":
    unittest.main()
