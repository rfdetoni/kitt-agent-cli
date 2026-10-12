"""Unattended differential mission controller over operator-pinned SSH guests.

Mutation is invoked ONLY as a normal KITT-approved process.run command (not via
the model-facing MCP server). SSH aliases resolve through operator-owned private
config, and remote authorized_keys MUST use forced-command guest_rpc endpoints.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sqlite3
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

from .evidence import EvidenceError
from .evidence_store import import_receipt
from .probe_evidence import _verify, compare_probe_receipts

MAX_MISSION_BYTES = 8 * 1024
MAX_REMOTE_OUTPUT = 256 * 1024
MAX_PROBES = 16
ALIAS = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")
PROBE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class MissionError(ValueError):
    pass


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise MissionError("Duplicate JSON field")
        obj[key] = value
    return obj


def _read_json(path: Path, max_size: int, *, private: bool = False) -> dict:
    if path.is_symlink():
        raise MissionError("Symlink configuration is forbidden")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise MissionError("Cannot open configuration") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > max_size:
            raise MissionError("Configuration is not a regular bounded file")
        if private and os.name == "posix" and (
            meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) & 0o077
        ):
            raise MissionError("Guest host profile must be owned by user with mode 0600")
        raw = os.read(fd, max_size + 1)
        if len(raw) > max_size:
            raise MissionError("Configuration exceeds limit")
    finally:
        os.close(fd)
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(MissionError("Nonfinite number")),
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise MissionError("Invalid configuration JSON") from exc
    if not isinstance(value, dict):
        raise MissionError("Configuration must be an object")
    return value


def _hosts(path: Path) -> dict:
    config = _read_json(path, 8192, private=True)
    if set(config) != {"schema_version", "ssh_config", "reference", "candidate"} or config["schema_version"] != 1:
        raise MissionError("Invalid guest host profile schema")
    for kind in ("reference", "candidate"):
        alias = config[kind]
        if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
            raise MissionError("Invalid SSH host alias")
    ssh_config = Path(config["ssh_config"])
    if not ssh_config.is_absolute() or ssh_config.is_symlink() or not ssh_config.is_file():
        raise MissionError("SSH configuration must be an absolute existing file")
    # SSH itself verifies known_hosts and private identity permissions.
    return config


def _mission(path: Path, workspace: Path) -> list[str]:
    root = workspace.resolve(strict=True)
    if not path.resolve(strict=True).is_relative_to(root):
        raise MissionError("Mission must remain in workspace")
    payload = _read_json(path, MAX_MISSION_BYTES)
    if set(payload) != {"schema_version", "probes"} or type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise MissionError("Invalid mission schema")
    names = payload["probes"]
    if not isinstance(names, list) or not 1 <= len(names) <= MAX_PROBES:
        raise MissionError("Mission needs 1..16 probes")
    if any(not isinstance(x, str) or not PROBE.fullmatch(x) for x in names):
        raise MissionError("Invalid probe name")
    if len(set(names)) != len(names):
        raise MissionError("Duplicate probes")
    return names


def _ssh_request(hosts: dict, alias: str, probe: str) -> dict:
    ssh = shutil.which("ssh")
    if not ssh:
        raise MissionError("OpenSSH client is not installed")
    argv = [
        ssh, "-T", "-F", hosts["ssh_config"],
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", "ConnectTimeout=10",
        "--", alias,
    ]
    body = json.dumps({"version": 1, "method": "probe.run", "probe": probe}, separators=(",", ":")) + "\n"
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=out, stderr=err,
            start_new_session=(os.name == "posix"),
        )
        try:
            assert process.stdin is not None
            process.stdin.write(body.encode("utf-8"))
            process.stdin.close()
            status = process.wait(timeout=90)
        except subprocess.TimeoutExpired as exc:
            raise MissionError(f"Remote guest {alias} timed out; automatic replay is forbidden") from exc
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait()
        out.seek(0, os.SEEK_END)
        size = out.tell()
        if size > MAX_REMOTE_OUTPUT:
            raise MissionError("Guest response exceeds size limit")
        out.seek(0)
        raw = out.read(MAX_REMOTE_OUTPUT)
        if status != 0:
            raise MissionError(f"Guest {alias} exited {status}; do not auto-retry side effects")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise MissionError("Malformed guest response") from exc
    if not isinstance(data, dict) or set(data) != {"version", "receipt"} or data["version"] != 1:
        raise MissionError("Unexpected guest response")
    receipt = data["receipt"]
    _verify(receipt)
    if receipt["probe"] != probe:
        raise MissionError("Guest receipt probe mismatch")
    return receipt


def run_mission(workspace: Path, mission_file: Path, hosts_file: Path,
                *, remote_runner=None) -> dict:
    """Run independent probes, with reference and candidate in parallel.

    Injected remote_runner exists solely for tests. No model-provided commands.
    """
    workspace = workspace.resolve(strict=True)
    hosts = _hosts(hosts_file)
    names = _mission(mission_file, workspace)
    remote = remote_runner or _ssh_request
    report = {"schema_version": 1, "probes": [], "completed": 0, "matched": 0,
              "divergent": 0, "blocked": 0, "guest_attested": False}
    for probe in names:
        with ThreadPoolExecutor(max_workers=2) as executor:
            left = executor.submit(remote, hosts, hosts["reference"], probe)
            right = executor.submit(remote, hosts, hosts["candidate"], probe)
            try:
                ref, candidate = left.result(), right.result()
                _verify(ref)
                _verify(candidate)
                if ref["probe"] != probe or candidate["probe"] != probe:
                    raise MissionError("Guest returned wrong probe identity")
                # Never compare a reference guest with another accidental Windows guest.
                if ref["platform"] != "Windows" or candidate["platform"] != "Linux+Wine":
                    raise MissionError("Guests do not have expected roles")
                compared = compare_probe_receipts(ref, candidate)
                import_receipt(workspace, ref)
                import_receipt(workspace, candidate)
            except (MissionError, EvidenceError, ValueError, OSError, sqlite3.Error) as ex:
                report["blocked"] += 1
                report["probes"].append({"name": probe, "status": "BLOCKED",
                                         "error": str(ex)[:200]})
                continue
        report["completed"] += 1
        report["matched" if compared["match"] else "divergent"] += 1
        report["probes"].append({
            "name": probe, "status": "MATCH" if compared["match"] else "DIVERGENT",
            "differences": compared["differences"],
            "difference_count": compared["difference_count"],
            "reference_sha256": ref["sha256"],
            "candidate_sha256": candidate["sha256"],
        })
    return report


def plan_mission(workspace: Path, relative_mission: str) -> dict:
    """Read-only helper. KITT must execute argv via host policy, never via MCP."""
    from pathlib import PureWindowsPath
    if not isinstance(relative_mission, str) or not relative_mission:
        raise MissionError("Mission path is required")
    path = Path(relative_mission)
    windows = PureWindowsPath(relative_mission)
    if path.is_absolute() or windows.is_absolute() or windows.drive or ".." in path.parts:
        raise MissionError("Mission path must be workspace-relative")
    root = workspace.resolve(strict=True)
    selected = root / path
    probes = _mission(selected, root)
    hosts = Path.home() / ".kitt" / "reverse-engineering" / "hosts.json"
    # Only operator-owned private configuration is accepted.
    if not hosts.is_file():
        return {"ready": False, "probes": probes,
                "reason": "Operator must provision private ~/.kitt/reverse-engineering/hosts.json"}
    _hosts(hosts)
    return {"ready": True, "probes": probes,
            "requires_host_approval": True,
            "argv": [sys.executable, "-m",
                     "kitt.extensions.builtin_plugins.reverse_engineering.mission",
                     "--workspace", str(root), "--mission", str(selected),
                     "--hosts", str(hosts)]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Unattended approved guest differential mission")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--hosts", type=Path, required=True,
                        help="Operator-controlled private SSH aliases file, mode 0600")
    args = parser.parse_args()
    try:
        result = run_mission(args.workspace, args.mission, args.hosts)
    except (MissionError, EvidenceError, OSError, ValueError) as ex:
        print(json.dumps({"status": "BLOCKED", "error": str(ex)[:200]}))
        raise SystemExit(2) from ex
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(2 if result["blocked"] else 1 if result["divergent"] else 0)


if __name__ == "__main__":
    main()
