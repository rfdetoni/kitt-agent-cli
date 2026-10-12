"""Validate local probe receipts and compare the measured JSON observations.

SHA-256 detects accidental changes; it does not authenticate a VM or its operator.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .evidence import EvidenceError, compare


def _verify(receipt: Any) -> dict:
    if not isinstance(receipt, dict):
        raise EvidenceError("Probe receipt must be an object")
    required = {"schema_version", "probe", "probe_sha256", "platform", "release",
                "exit_code", "timed_out", "truncated", "duration_ms", "stdout", "stderr", "sha256"}
    if set(receipt) != required or receipt["schema_version"] != 1:
        raise EvidenceError("Unrecognized probe receipt schema")
    sha = receipt["sha256"]
    if not isinstance(sha, str) or len(sha) != 64:
        raise EvidenceError("Invalid probe receipt digest")
    original = dict(receipt)
    original.pop("sha256")
    try:
        canonical = json.dumps(original, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as ex:
        raise EvidenceError("Invalid receipt fields") from ex
    if len(canonical) > 140 * 1024 or hashlib.sha256(canonical).hexdigest() != sha:
        raise EvidenceError("Probe receipt digest mismatch")
    if receipt["exit_code"] != 0 or receipt["timed_out"] or receipt["truncated"]:
        raise EvidenceError("Probe execution was not complete and successful")
    if not isinstance(receipt["stdout"], str) or len(receipt["stdout"]) > 65536:
        raise EvidenceError("Invalid probe observation")
    try:
        output = json.loads(receipt["stdout"])
    except (ValueError, TypeError) as ex:
        raise EvidenceError("Probe did not emit one JSON observation") from ex
    if not isinstance(output, dict):
        raise EvidenceError("Probe output must be a JSON object")
    return output


def compare_probe_receipts(reference: dict, candidate: dict) -> dict:
    left, right = _verify(reference), _verify(candidate)
    if reference["probe"] != candidate["probe"]:
        raise EvidenceError("Probe IDs must match")
    result = compare(left, right)
    result["probe"] = reference["probe"]
    result["reference_receipt_sha256"] = reference["sha256"]
    result["candidate_receipt_sha256"] = candidate["sha256"]
    return result
