"""Prove receipt comparisons reject tampering and incomplete execution."""
import hashlib
import json
import unittest

from kitt.extensions.builtin_plugins.reverse_engineering.evidence import EvidenceError
from kitt.extensions.builtin_plugins.reverse_engineering.probe_evidence import compare_probe_receipts


def receipt(observation):
    data = {"schema_version": 1, "probe": "event_auto_reset", "probe_sha256": "a" * 64,
            "platform": "Windows", "release": "11", "exit_code": 0, "timed_out": False,
            "truncated": False, "duration_ms": 3.0, "stdout": json.dumps(observation),
            "stderr": ""}
    blob = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**data, "sha256": hashlib.sha256(blob).hexdigest()}


class ProbeEvidenceTests(unittest.TestCase):
    def test_difference_from_two_valid_receipts(self):
        win = receipt({"initial": 258, "after_signal": 0})
        linux = receipt({"initial": 0, "after_signal": 0})
        result = compare_probe_receipts(win, linux)
        self.assertFalse(result["match"])
        self.assertEqual(result["differences"][0]["path"], "/initial")

    def test_invalid_receipt_rejected(self):
        good = receipt({"result": 1})
        changed = dict(good, stdout='{"result":2}')
        with self.assertRaises(EvidenceError):
            compare_probe_receipts(good, changed)

    def test_boolean_exit_code_rejected(self):
        good = receipt({"result": 1})
        bad = dict(good, exit_code=False)
        bad.pop("sha256")
        bad["sha256"] = hashlib.sha256(json.dumps(bad, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaises(EvidenceError):
            compare_probe_receipts(good, bad)

    def test_timeout_rejected_even_with_correct_digest(self):
        good = receipt({"result": 1})
        bad = dict(good, timed_out=True)
        bad.pop("sha256")
        bad["sha256"] = hashlib.sha256(json.dumps(bad, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.assertRaises(EvidenceError):
            compare_probe_receipts(good, bad)


if __name__ == "__main__":
    unittest.main()
