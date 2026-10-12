"""Deterministic structural comparison for bounded experiment observations."""
from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_JSON = 128 * 1024
MAX_DIFFERENCES = 64


class EvidenceError(ValueError):
    pass


def _encoded(value: Any) -> bytes:
    try:
        blob = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as ex:
        raise EvidenceError("Invalid observation") from ex
    if len(blob) > MAX_JSON:
        raise EvidenceError("Observation too large")
    return blob


def compare(reference: Any, candidate: Any) -> dict:
    ref_hash = hashlib.sha256(_encoded(reference)).hexdigest()
    candidate_hash = hashlib.sha256(_encoded(candidate)).hexdigest()
    differences: list[dict] = []
    total = 0

    def walk(a: Any, b: Any, path: str, depth: int = 0) -> None:
        nonlocal total
        if depth > 32:
            raise EvidenceError("Observation nesting exceeds limit")
        if isinstance(a, dict) and isinstance(b, dict):
            keys = sorted(set(a) | set(b))
            for key in keys:
                if not isinstance(key, str):
                    raise EvidenceError("Observation object keys must be strings")
                escaped = key.replace("~", "~0").replace("/", "~1")
                if key not in a or key not in b:
                    total += 1
                    if len(differences) < MAX_DIFFERENCES:
                        differences.append({"path": path + "/" + escaped, "kind": "missing"})
                else:
                    walk(a[key], b[key], path + "/" + escaped, depth + 1)
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                total += 1
                if len(differences) < MAX_DIFFERENCES:
                    differences.append({"path": path, "kind": "length"})
            for i in range(min(len(a), len(b))):
                walk(a[i], b[i], path + "/" + str(i), depth + 1)
        elif type(a) is not type(b) or a != b:
            total += 1
            if len(differences) < MAX_DIFFERENCES:
                differences.append({"path": path or "/", "kind": "value"})

    walk(reference, candidate, "")
    return {"match": total == 0, "difference_count": total, "differences": differences,
            "truncated": total > len(differences), "reference_sha256": ref_hash, "candidate_sha256": candidate_hash}
