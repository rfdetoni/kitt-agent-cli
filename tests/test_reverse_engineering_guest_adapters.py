"""Focused regression coverage; fake external tools are not live Ghidra/Frida E2E."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from kitt.extensions.builtin_plugins.reverse_engineering.evidence import EvidenceError
from kitt.extensions.builtin_plugins.reverse_engineering.evidence_store import import_receipt, query
from kitt.extensions.builtin_plugins.reverse_engineering.ghidra_guest import _read_report, GhidraError
from kitt.extensions.builtin_plugins.reverse_engineering.frida_guest import trace_guest, TraceError


def receipt(probe="auto_reset", observation=None):
    obj = {"schema_version": 1, "probe": probe, "probe_sha256": "a" * 64,
           "platform": "Windows", "release": "11", "exit_code": 0,
           "timed_out": False, "truncated": False, "duration_ms": 1,
           "stdout": json.dumps(observation or {"value": 1}), "stderr": ""}
    digest = hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**obj, "sha256": digest}


class EvidenceStoreTests(unittest.TestCase):
    def test_idempotent_import_and_bounded_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = receipt()
            self.assertTrue(import_receipt(root, item))
            self.assertFalse(import_receipt(root, item))
            rows = query(root, "auto_reset", 5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["observation"], {"value": 1})
            self.assertEqual(rows[0]["receipt_sha256"], item["sha256"])
            self.assertEqual(query(root, "unknown"), [])
            with self.assertRaises(EvidenceError):
                query(root, "' OR 1=1 --")
            with self.assertRaises(EvidenceError):
                query(root, "auto_reset", True)

    def test_modified_receipt_not_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = receipt()
            item["stdout"] = '{"value":2}'
            with self.assertRaises(EvidenceError):
                import_receipt(Path(tmp), item)

    def test_symlink_evidence_location_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".kitt").symlink_to(root, target_is_directory=True)
            with self.assertRaises(EvidenceError):
                import_receipt(root, receipt())


class GhidraBoundaryTests(unittest.TestCase):
    def test_valid_and_malformed_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(
                '{"language":"x86","functions":[{"name":"main","entry":"00400000",'
                '"status":"DECOMPILED","file":"fn_00400000.c","error":"",'
                '"pseudocode_bytes":12}],"truncated":false,"start_index":0,"next_index":1}'
            )
            self.assertEqual(_read_report(path)["functions"][0]["name"], "main")
            path.write_text(
                '{"language":"x86","functions":[],"truncated":false,"start_index":0,'
                '"next_index":0,"functions":[]}'
            )
            with self.assertRaises(GhidraError):
                _read_report(path)
            path.write_text("A" * (256 * 1024 + 1))
            with self.assertRaises(GhidraError):
                _read_report(path)


class _FakeScript:
    def __init__(self):
        self.callback = None

    def on(self, event, callback):
        assert event == "message"
        self.callback = callback

    def load(self):
        self.callback({"type": "send", "payload": {"kind": "api_enter", "api": "CreateEventW"}}, None)
        self.callback({"type": "send", "payload": {"kind": "api_enter", "api": "unknown"}}, None)


class _FakeSession:
    def __init__(self):
        self.detached = None
        self.script = _FakeScript()
        self.cleanup = False

    def on(self, event, callback):
        assert event == "detached"
        self.detached = callback

    def create_script(self, source):
        assert "WaitForSingleObject" in source
        return self.script

    def detach(self):
        self.cleanup = True


class _FakeDevice:
    def __init__(self):
        self.session = _FakeSession()
        self.pid = 321
        self.killed = False

    def spawn(self, argv):
        assert len(argv) == 1
        return self.pid

    def attach(self, pid):
        assert pid == self.pid
        return self.session

    def resume(self, pid):
        self.session.detached("process-terminated")

    def kill(self, pid):
        self.killed = True


class FridaBoundaryTests(unittest.TestCase):
    def test_pinned_probe_only_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "probe.exe").write_bytes(b"not executed by fake")
            sha = hashlib.sha256((root / "probe.exe").read_bytes()).hexdigest()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"probes": {"auto_reset": {"path": "probe.exe", "sha256": sha}}}))
            fake = _FakeDevice()
            result = trace_guest(root, manifest, "auto_reset", device=fake, duration=0.01)
            self.assertTrue(fake.killed)
            self.assertTrue(fake.session.cleanup)
            self.assertEqual(result["events"], [{"api": "CreateEventW"}])
            (root / "probe.exe").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                trace_guest(root, manifest, "auto_reset", device=fake, duration=0.01)

    def test_invalid_duration(self):
        with self.assertRaises(TraceError):
            trace_guest(Path("."), Path("."), "good", duration=True)


if __name__ == "__main__":
    unittest.main()
