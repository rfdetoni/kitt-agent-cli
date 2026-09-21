from __future__ import annotations

from pathlib import Path

from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.parser import PatchParser, UnifiedDiffParser, detect_patch_format


def test_unified_diff_parser_converts_existing_file_hunk():
    patch = """diff --git a/service.py b/service.py
--- a/service.py
+++ b/service.py
@@ -1,2 +1,2 @@
 def run():
-    return 1
+    return 2
"""
    blocks = UnifiedDiffParser().parse(patch)

    assert len(blocks) == 1
    assert blocks[0].file_path == "service.py"
    assert blocks[0].search_content == "def run():\n    return 1"
    assert blocks[0].replace_content == "def run():\n    return 2"
    assert blocks[0].is_new_file is False
    assert blocks[0].is_deletion is False


def test_patch_parser_accepts_fenced_unified_diff_and_multiple_hunks():
    patch = """```diff
diff --git a/service.py b/service.py
--- a/service.py
+++ b/service.py
@@ -1,2 +1,2 @@
 def first():
-    return 1
+    return 2
@@ -4,2 +4,2 @@
 def second():
-    return 3
+    return 4
```"""
    parser = PatchParser()

    assert parser.format(patch) == "unified_diff"
    blocks = parser.parse(patch)
    assert len(blocks) == 2
    assert blocks[0].replace_content.endswith("return 2")
    assert blocks[1].replace_content.endswith("return 4")


def test_unified_diff_uses_existing_atomic_applier_and_undo_boundary(tmp_path: Path):
    target = tmp_path / "service.py"
    target.write_text(
        "def first():\n    return 1\n\ndef second():\n    return 3\n",
        encoding="utf-8",
    )
    patch = """diff --git a/service.py b/service.py
--- a/service.py
+++ b/service.py
@@ -1,2 +1,2 @@
 def first():
-    return 1
+    return 2
@@ -4,2 +4,2 @@
 def second():
-    return 3
+    return 4
"""
    blocks = PatchParser().parse(patch)
    applier = DiffApplier()

    result = applier.apply(blocks, root_dir=str(tmp_path))
    assert result.success is True
    assert target.read_text(encoding="utf-8") == (
        "def first():\n    return 2\n\ndef second():\n    return 4\n"
    )

    reverted = applier.tracker.revert_last_changeset()
    assert reverted is not None
    assert target.read_text(encoding="utf-8") == (
        "def first():\n    return 1\n\ndef second():\n    return 3\n"
    )


def test_unified_diff_new_file_and_delete_file_are_translated():
    parser = PatchParser()
    create_patch = """diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+print("one")
+print("two")
"""
    delete_patch = """diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-print("one")
-print("two")
"""

    created = parser.parse(create_patch)
    deleted = parser.parse(delete_patch)

    assert len(created) == 1
    assert created[0].file_path == "new.py"
    assert created[0].is_new_file is True
    assert created[0].replace_content == 'print("one")\nprint("two")\n'
    assert len(deleted) == 1
    assert deleted[0].file_path == "old.py"
    assert deleted[0].is_deletion is True


def test_unified_diff_rejects_malformed_counts_and_unanchored_existing_insertions():
    malformed_counts = """--- a/service.py
+++ b/service.py
@@ -1,2 +1,2 @@
-old
+new
"""
    unanchored_insert = """--- a/service.py
+++ b/service.py
@@ -2,0 +3,1 @@
+inserted
"""

    parser = PatchParser()
    assert parser.parse(malformed_counts) == []
    assert parser.parse(unanchored_insert) == []


def test_unified_diff_cannot_escape_workspace(tmp_path: Path):
    patch = """--- a/../outside.py
+++ b/../outside.py
@@ -1 +1 @@
-old
+new
"""
    outside = tmp_path.parent / "outside.py"
    outside.write_text("old\n", encoding="utf-8")

    result = DiffApplier().apply(PatchParser().parse(patch), root_dir=str(tmp_path))

    assert result.success is False
    assert "Validation error" in result.errors[0]
    assert outside.read_text(encoding="utf-8") == "old\n"


def test_patch_format_detection_prefers_search_replace_when_markers_are_present():
    search_replace = """service.py
<<<<<<< SEARCH
old
=======
new
>>>>>>> REPLACE
"""
    assert detect_patch_format(search_replace) == "search_replace"
    assert detect_patch_format("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n") == "unified_diff"
