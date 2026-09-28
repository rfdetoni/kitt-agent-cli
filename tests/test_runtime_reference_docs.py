from __future__ import annotations

import re
import unittest
from pathlib import Path

from kitt.runtime.core_runtime import OPERATION_SPECS
from scripts.generate_runtime_reference import render_runtime_reference


class RuntimeReferenceDocumentationTests(unittest.TestCase):
    def test_runtime_reference_is_generated_from_authoritative_registry(self):
        root = Path(__file__).resolve().parents[1]
        committed = (root / "docs" / "RUNTIME_REFERENCE.md").read_text(encoding="utf-8")
        self.assertEqual(committed, render_runtime_reference())

    def test_every_runtime_operation_is_documented_once(self):
        rendered = render_runtime_reference()
        names = re.findall(r"^\| ([a-z0-9_.]+) \|", rendered, flags=re.MULTILINE)
        self.assertEqual(names, sorted(OPERATION_SPECS))
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
