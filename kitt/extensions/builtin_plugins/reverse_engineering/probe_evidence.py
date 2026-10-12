"""Validate local probe receipts and compare the measured JSON observations.

SHA-256 detects accidental changes; it does not authenticate a VM or its operator.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .evidence import EvidenceError, compare


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("Probe observation has duplicate JSON fields")
        result[key] = value
    return result


def _verify(receipt: Any) -> dict:
    if not isinstance(receipt, dict):
        raise EvidenceError("Probe receipt must be an object")
    required = {"schema_version", "probe", "probe_sha256", "platform", "release",
                "exit_code", "timed_out", "truncated", "duration_ms", "stdout", "stderr", "sha256"}
    if set(receipt) != required or receipt["schema_version"] != 1:
        raise EvidenceError("Unrecognized probe receipt schema")
    sha = receipt["sha256"]
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise EvidenceError("Invalid probe receipt digest")
    if (type(receipt["schema_version"]) is not int
            or type(receipt["exit_code"]) is not int
            or type(receipt["timed_out"]) is not bool
            or type(receipt["truncated"]) is not bool
            or not isinstance(receipt["probe"], str)
            or not receipt["probe"]
            or not isinstance(receipt["probe_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", receipt["probe_sha256"])
            or not isinstance(receipt["platform"], str)
            or not isinstance(receipt["release"], str)
            or not isinstance(receipt["stderr"], str)
            or type(receipt["duration_ms"]) not in (int, float)
            or not math.isfinite(receipt["duration_ms"])
            or receipt["duration_ms"] < 0):
        raise EvidenceError("Invalid probe receipt field types")
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
        output = json.loads(receipt["stdout"], object_pairs_hook=_unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
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
