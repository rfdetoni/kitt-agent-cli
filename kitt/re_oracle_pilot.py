"""Operator-only fixed Win32 synchronization pilot, Windows versus managed Docker Wine.

Only the KITT-authored event_auto_reset PE is executable. This is NOT a
general-purpose model selected shell and is NOT suitable for hostile samples.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import os
import re
import tempfile
import shutil
import time

from kitt import re_oracle, re_oracle_bootstrap
from kitt.extensions.builtin_plugins.reverse_engineering.mission import _hosts, _ssh_request
from kitt.extensions.builtin_plugins.reverse_engineering.probe_evidence import (
    EvidenceError, _verify, compare_probe_receipts,
)
from kitt.vm_supervisor import VMError

PILOT = "event_auto_reset"


def _run_isolated_wine(lab: Path, linux: dict, path: Path) -> tuple[int, bytes, bytes]:
    """Run only the fixed approved PE in a disposable networkless container."""
    if (path.parent != lab / "workspace" / ".kitt-oracle" or path.name != "event_probe.exe"
            or path.is_symlink() or not path.is_file()):
        raise VMError("Untrusted fixed Wine pilot path")
    docker = shutil.which("docker")
    if not docker:
        raise VMError("Docker is not installed")
    identity = subprocess.run(
        [docker, "container", "inspect", "--format", "{{.Image}}", linux["container_id"]],
        capture_output=True, timeout=15, check=False,
    )
    image_id = identity.stdout.decode("ascii", "replace").strip()
    if identity.returncode or not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise VMError("Cannot pin the running Linux RE toolbox image")
    with tempfile.TemporaryDirectory(prefix=".wine-", dir=lab / "oracle") as scratch:
        cidfile = Path(scratch) / "container.cid"
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            argv = [
                docker, "run", "--rm", "--pull", "never",
                "--cidfile", str(cidfile), "--network", "none",
                "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "64", "--memory", "1g", "--cpus", "1",
                "--user", str(os.getuid()) + ":" + str(os.getgid()),
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
                "--env", "HOME=/tmp", "--env", "WINEPREFIX=/tmp/.wine",
                "--env", "WINEDEBUG=-all",
                "--mount", "type=bind,src=" + str(path.parent) + ",dst=/probes,readonly",
                "--workdir", "/probes", image_id, "/usr/lib/wine/wine64",
                "/probes/event_probe.exe",
            ]
            try:
                result = subprocess.run(argv, stdout=out, stderr=err,
                                        timeout=60, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                # The Docker CLI may time out while the container is still alive.
                # Clean up only the exact immutable ID written for this invocation.
                if cidfile.is_file() and not cidfile.is_symlink():
                    cid = cidfile.read_text("ascii").strip()
                    if re.fullmatch(r"[a-f0-9]{64}", cid):
                        subprocess.run([docker, "rm", "--force", cid],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       timeout=20, check=False)
                raise VMError("Isolated Wine pilot failed or timed out") from exc
            out.seek(0, 2)
            err.seek(0, 2)
            if out.tell() > 65536 or err.tell() > 65536:
                raise VMError("Wine pilot output exceeds size limit")
            out.seek(0)
            err.seek(0)
            return result.returncode, out.read(65537), err.read(65537)


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
        exit_code, stdout, stderr = _run_isolated_wine(lab, linux, path)
    except (VMError, OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "BLOCKED_ENV", "reason": "Isolated Wine candidate unavailable: "
                + str(exc)[:120]}
    candidate = {
        "schema_version": 1, "probe": PILOT, "probe_sha256": expected_sha,
        "platform": "Linux+Wine", "release": "docker-linux-wine",
        "exit_code": exit_code, "timed_out": False, "truncated": False,
        "duration_ms": round((time.monotonic() - start) * 1000, 2),
        "stdout": stdout.decode("utf-8", "replace"),
        "stderr": stderr.decode("utf-8", "replace"),
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
        "candidate": {"platform": "Linux+Wine", "environment": "isolated-ephemeral-wine-container",
                      "receipt_sha256": candidate["sha256"]},
        "differences": outcome["differences"],
        "source_binary_sha256": expected_sha,
        "guest_attested": False,
        "toolbox_vm_isolated": True,
    }
    re_oracle_bootstrap._write(
        private / "pilot.json", (json.dumps(report, indent=2, ensure_ascii=True) + "\n").encode()
    )
    return report
