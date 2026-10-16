"""Operator-only fixed Win32 synchronization pilot, Windows versus managed Docker Wine.

Only the KITT-authored event_auto_reset PE is executable. This is NOT a
general-purpose model selected shell and is NOT suitable for hostile samples.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import time

from kitt import re_oracle, re_oracle_bootstrap
from kitt.extensions.builtin_plugins.reverse_engineering.mission import _hosts, _ssh_request
from kitt.extensions.builtin_plugins.reverse_engineering.probe_evidence import (
    EvidenceError, _verify, compare_probe_receipts,
)
from kitt.vm_supervisor import VMError

PILOT = "event_auto_reset"


def pilot(*, manifest_sha256: str, hosts: Path | None = None, yes: bool = False) -> dict:
    """Run exactly one bounded authored probe after current SSH Oracle doctor."""
    if not yes:
        raise VMError("Operator must approve the fixed pilot using --yes")
    checked = re_oracle.doctor(manifest_sha256=manifest_sha256, hosts=hosts)
    if not checked["oracle_ready"]:
        return {"status": "BLOCKED_ENV", "reason": checked.get("reason", "Oracle not ready")}
    lab, linux = re_oracle_bootstrap._lab()
    private = lab / "oracle"
    local_manifest = private / "oem" / "manifest.json"
    if local_manifest.is_symlink() or not local_manifest.is_file() or local_manifest.stat().st_size > 65536:
        raise VMError("No pinned local Oracle manifest")
    original = local_manifest.read_bytes()
    if hashlib.sha256(original).hexdigest() != manifest_sha256:
        raise VMError("Local manifest differs from Windows Oracle approved SHA-256")
    try:
        descriptor = json.loads(original)
        row = descriptor["probes"][PILOT]
        expected_sha = row["sha256"]
        if row["path"] != "event_probe.exe" or not isinstance(expected_sha, str):
            raise ValueError("Wrong fixed pilot path")
    except (KeyError, TypeError, ValueError) as exc:
        raise VMError("Unrecognized fixed pilot manifest") from exc
    path = lab / "workspace" / ".kitt-oracle" / "event_probe.exe"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise VMError("Missing bounded compiled Wine pilot binary")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
        raise VMError("Compiled Wine pilot digest differs from authorized Windows binary")
    cfg = _hosts(hosts if hosts is not None else private / "hosts.json")
    reference = _ssh_request(cfg, cfg["reference"], PILOT)
    if reference["platform"] != "Windows" or reference["probe_sha256"] != expected_sha:
        raise VMError("Windows pilot receipt identity or binary SHA differs")
    # Same exact PE, fixed path, no arbitrary caller-provided CLI arguments.
    start = time.monotonic()
    try:
        wine = subprocess.run(
            ["docker", "exec", linux["container_id"], "wine",
             "/workspace/.kitt-oracle/event_probe.exe"],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "BLOCKED_ENV", "reason": "Wine guest pilot unavailable or timed out"}
    if len(wine.stdout) > 65536 or len(wine.stderr) > 65536:
        return {"status": "BLOCKED_ENV", "reason": "Wine pilot output truncated"}
    candidate = {
        "schema_version": 1, "probe": PILOT, "probe_sha256": expected_sha,
        "platform": "Linux+Wine", "release": "docker-linux-wine",
        "exit_code": wine.returncode, "timed_out": False, "truncated": False,
        "duration_ms": round((time.monotonic() - start) * 1000, 2),
        "stdout": wine.stdout.decode("utf-8", "replace"),
        "stderr": wine.stderr.decode("utf-8", "replace"),
    }
    candidate["sha256"] = hashlib.sha256(
        json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        _verify(reference)
        _verify(candidate)
        outcome = compare_probe_receipts(reference, candidate)
    except (EvidenceError, ValueError) as exc:
        return {"status": "BLOCKED_ENV", "reason": "Invalid probe receipt: " + str(exc)[:120]}
    status = "PASS_SCENARIO" if outcome["match"] else "FAIL_SCENARIO"
    report = {
        "status": status, "probe": PILOT,
        "oracle": {"platform": reference["platform"], "release": reference["release"],
                   "receipt_sha256": reference["sha256"]},
        "candidate": {"platform": "Linux+Wine", "environment": "managed-toolbox",
                      "receipt_sha256": candidate["sha256"]},
        "differences": outcome["differences"],
        "source_binary_sha256": expected_sha,
        "guest_attested": False,
        "toolbox_vm_isolated": False,
    }
    re_oracle_bootstrap._write(
        private / "pilot.json", (json.dumps(report, indent=2, ensure_ascii=True) + "\n").encode()
    )
    return report
