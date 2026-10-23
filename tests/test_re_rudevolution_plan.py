"""Verify the RE adapter plans without executing, downloading or modifying inputs."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kitt.extensions.builtin_plugins.reverse_engineering.binary import InspectionError
from kitt.extensions.builtin_plugins.reverse_engineering.rudevolution import plan
from kitt.extensions.builtin_plugins.reverse_engineering.server import respond


class RuDevolutionPlanTest(unittest.TestCase):
    def test_operator_pinned_plan_does_not_execute_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bundle.min.js"
            source.write_text("var n=1;\n", encoding="utf-8")
            binary = root / "decompiler"
            binary.write_bytes(b"trusted fixture executable")
            binary.chmod(0o700)
            (root / "analysis").mkdir()
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            with patch("subprocess.run", side_effect=AssertionError("executed")), patch(
                "subprocess.Popen", side_effect=AssertionError("executed")
            ):
                result = plan(root, "bundle.min.js", str(binary), digest, "analysis/result")
            self.assertFalse(result["executed"])
            self.assertTrue(result["requires_approved_process_run"])
            self.assertEqual(result["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(result["argv"][0], str(binary))
            self.assertFalse((root / "analysis/result").exists())
            reply = respond({"jsonrpc": "2.0", "id": 5, "method": "tools/list"}, root)
            self.assertIn("rudevolution_plan", [entry["name"] for entry in reply["result"]["tools"]])

    def test_denies_untrusted_paths_and_changed_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.js").write_text("var x=1", encoding="utf-8")
            binary = root / "example"
            binary.write_bytes(b"example")
            binary.chmod(0o700)
            (root / "out").mkdir()
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            for path, pin, output in (
                ("../outside.js", digest, "out/new"),
                ("sample.js", "0" * 64, "out/new"),
                ("sample.js", digest, "../outside"),
                ("sample.js", digest, "out"),
            ):
                with self.subTest(path=path, pin=pin, output=output):
                    with self.assertRaises(InspectionError):
                        plan(root, path, str(binary), pin, output)
            link = root / "link.js"
            try:
                link.symlink_to(root / "sample.js")
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(InspectionError):
                    plan(root, "link.js", str(binary), digest, "out/new")
