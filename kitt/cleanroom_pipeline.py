"""Fail-closed clean-room orchestration and independently signed validation.

A successful implementer or test container exit is not Oracle conformance.
The host is the only party which reads private seals, reviewer keys or receipts.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time

from kitt.cleanroom_handoff import _create_private_file, _read_source, _secure_root
from kitt.cleanroom_runner import (
    CleanRoomEnvironmentError, TRUSTED_REVIEW_KEY, _worktree,
    run_offline_implementer, _output_digest,
)
from kitt_protocol.cleanroom import decode_cleanroom_contract

IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RUN = re.compile(r"[0-9a-f]{32}\Z")


def _trusted_script(script: Path, signature: Path, digest: str) -> tuple[Path, bytes]:
    if not SHA256.fullmatch(digest):
        raise CleanRoomEnvironmentError("trusted test SHA-256 is required")
    script = Path(script)
    signature = Path(signature)
    if not script.is_absolute() or not signature.is_absolute():
        raise CleanRoomEnvironmentError("trusted test and signature require absolute paths")
    data = _read_source(script)
    if hashlib.sha256(data).hexdigest() != digest:
        raise CleanRoomEnvironmentError("test script digest differs from pinned SHA-256")
    if not data.startswith(b"#!/bin/sh\n") or b"\r" in data or b"\x00" in data:
        raise CleanRoomEnvironmentError("test script must be bounded POSIX sh")
    key = TRUSTED_REVIEW_KEY
    if key.is_symlink() or not key.is_file():
        raise CleanRoomEnvironmentError("root-owned reviewer public key unavailable")
    info = key.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise CleanRoomEnvironmentError("untrusted reviewer public key permissions")
    if signature.is_symlink() or not signature.is_file() or signature.stat().st_size != 64:
        raise CleanRoomEnvironmentError("missing detached Ed25519 test signature")
    openssl = shutil.which("openssl")
    if not openssl:
        raise CleanRoomEnvironmentError("openssl verification unavailable")
    with tempfile.TemporaryDirectory(prefix="kitt-signed-test-") as scratch:
        copy = Path(scratch) / "reviewed-test.sh"
        _create_private_file(Path(scratch), "reviewed-test.sh", data)
        try:
            verified = subprocess.run(
                [openssl, "pkeyutl", "-verify", "-pubin", "-inkey", str(key),
                 "-rawin", "-in", str(copy), "-sigfile", str(signature)],
                env={"PATH": "/usr/bin:/bin", "LANG": "C"}, capture_output=True,
                stdin=subprocess.DEVNULL, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CleanRoomEnvironmentError("independent test-signature verification unavailable") from exc
        if verified.returncode:
            raise CleanRoomEnvironmentError("independent test-signature verification failed")
    return script, data


def _git_changes(source: Path, prefix: str) -> dict:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    try:
        changed = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-C", str(source), "status", "--porcelain=v1", "-z",
             "--untracked-files=all"], env=env, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=30, check=False)
        checked = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
             "-C", str(source), "diff", "--check", "--no-ext-diff"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanRoomEnvironmentError("source change inspection unavailable") from exc
    if changed.returncode or checked.returncode or len(changed.stdout) > 65536:
        raise CleanRoomEnvironmentError("modified Wine checkout fails git inspection")
    if not changed.stdout:
        raise CleanRoomEnvironmentError("implementer made no observable source changes")
    names = []
    for record in changed.stdout.split(b"\0"):
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise CleanRoomEnvironmentError("unrecognized git change record")
        flags = record[:2]
        if any(mark in flags for mark in (ord("R"), ord("C"), ord("D"), ord("U"), ord("!"))):
            raise CleanRoomEnvironmentError("unexpected rename/copy/delete/unmerged file")
        try:
            path = record[3:].decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise CleanRoomEnvironmentError("non-UTF-8 source path") from exc
        if (not path.startswith(prefix) or not path.endswith((".c", ".h", ".spec", ".in"))
                or ".." in path.split("/") or path.startswith("/") or
                (source / path).is_symlink()):
            raise CleanRoomEnvironmentError("implementer changed a file outside approved DLL")
        names.append(path)
    if not 1 <= len(names) <= 32:
        raise CleanRoomEnvironmentError("unexpected number of changed files")
    content_digest = hashlib.sha256()
    for path in sorted(names):
        file_path = source / path
        if (file_path.is_symlink() or not file_path.is_file()
                or file_path.stat().st_size > 16 * 1024 * 1024):
            raise CleanRoomEnvironmentError("modified source is not a bounded regular file")
        content_digest.update(path.encode("utf-8") + b"\0")
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                content_digest.update(chunk)
    return {"changed_count": len(names),
            "changed_paths_sha256": hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest(),
            "changed_content_sha256": content_digest.hexdigest()}


def _append_receipt(directory: Path, data: dict) -> dict:
    existing = sorted(directory.glob("audit-[0-9][0-9][0-9][0-9].json"))
    if len(existing) >= 500:
        raise CleanRoomEnvironmentError("audit chain capacity exceeded")
    previous = "0" * 64
    if existing:
        predecessor = existing[-1]
        if predecessor.is_symlink() or predecessor.stat().st_size > 65536:
            raise CleanRoomEnvironmentError("audit predecessor is untrusted")
        previous = hashlib.sha256(predecessor.read_bytes()).hexdigest()
    payload = {"schema": "kitt.cleanroom.audit.v1", "sequence": len(existing) + 1,
               "previous_sha256": previous, "host_time_untrusted": int(time.time()),
               "result": data}
    raw = (json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":")) + "\n").encode("ascii")
    _create_private_file(directory, f"audit-{len(existing)+1:04d}.json", raw)
    return {"sequence": len(existing) + 1, "receipt_sha256": hashlib.sha256(raw).hexdigest()}


def _validate_independently(source: Path, image: str, script: Path, *, engine: str,
                            timeout_seconds: int, state_root: Path) -> dict:
    if not IMAGE_ID.fullmatch(image):
        raise CleanRoomEnvironmentError("validation requires immutable image SHA-256")
    if engine not in {"docker", "podman"}:
        raise CleanRoomEnvironmentError("invalid OCI engine")
    executable = shutil.which(engine)
    if not executable:
        raise CleanRoomEnvironmentError("independent OCI runtime missing")
    prefix = ([executable, "--context", "default"] if engine == "docker"
              else [executable, "--remote=false"])
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
           "HOME": str(Path.home())}
    try:
        inspected = subprocess.run(
            [*prefix, "image", "inspect", "--format", "{{.Id}}", image],
            env=env, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanRoomEnvironmentError("independent validation image inspection failed") from exc
    if inspected.returncode or inspected.stdout.decode("ascii", "replace").strip() != image:
        raise CleanRoomEnvironmentError("unavailable pinned independent validation image")
    with tempfile.TemporaryDirectory(prefix="kitt-validation-", dir=state_root) as scratch:
        root = Path(scratch)
        out, err = root / "stdout", root / "stderr"
        cidfile = root / "container.cid"
        argv = [*prefix, "run", "--rm", "--pull", "never",
                "--cidfile", str(cidfile), "--network", "none",
                "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "128", "--memory", "4g", "--cpus", "2",
                "--user", f"{os.getuid()}:{os.getgid()}",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
                "--env", "HOME=/tmp", "--mount",
                f"type=bind,src={source},dst=/wine",
                "--mount", f"type=bind,src={script},dst=/validation/plan.sh,readonly",
                "--workdir", "/wine", "--entrypoint", "/bin/sh",
                image, "/validation/plan.sh"]
        with out.open("wb") as stdout, err.open("wb") as stderr:
            try:
                result = subprocess.run(
                    argv, env=env, stdin=subprocess.DEVNULL, stdout=stdout,
                    stderr=stderr, timeout=timeout_seconds, check=False)
            except subprocess.TimeoutExpired as exc:
                if cidfile.is_file() and not cidfile.is_symlink():
                    cid = cidfile.read_text("ascii").strip()
                    if re.fullmatch(r"[0-9a-f]{64}", cid):
                        try:
                            subprocess.run([*prefix, "rm", "--force", cid],
                                           env=env, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, timeout=20, check=False)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                raise CleanRoomEnvironmentError("independent validator timed out") from exc
        return {"status": "TEST_COMMAND_EXIT_0_NOT_ATTESTED" if result.returncode == 0
                else "VALIDATION_FAILED", "exit_code": result.returncode,
                "stdout": _output_digest(out), "stderr": _output_digest(err)}


def run_pipeline(
    run_id: str, *, expected_sha256: str, wine_source: Path,
    implementer_image: str, validator_image: str, test_script: Path,
    test_signature: Path, test_sha256: str,
    state_root: Path | None = None, engine: str = "docker",
    approved: bool = False, timeout_seconds: int = 600,
    require_fixed_oracle_pilot: bool = False,
) -> dict:
    """Run isolated implementation then independent signed tests.

    The optional fixed Windows/Wine pilot proves only that the known lab probe
    works; it does not establish conformance of new Wine code.
    """
    if approved is not True:
        raise PermissionError("operator approval required")
    private = _secure_root(state_root or (Path.home() / ".local/share/kitt/cleanroom"))
    if not _RUN.fullmatch(run_id) or not SHA256.fullmatch(expected_sha256):
        raise CleanRoomEnvironmentError("invalid pinned contract identity")
    directory = private / run_id
    if directory.is_symlink() or not directory.is_dir():
        raise CleanRoomEnvironmentError("sealed contract directory unavailable")
    source = _worktree(Path(wine_source), private)
    spec = decode_cleanroom_contract(_read_source(directory / "contract.json"))
    if hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":")).encode()).hexdigest() != expected_sha256:
        raise CleanRoomEnvironmentError("contract changed after independent review")
    _, script_bytes = _trusted_script(Path(test_script), Path(test_signature), test_sha256)
    # Copy the reviewed script away from the original mutable operator file.
    with tempfile.TemporaryDirectory(prefix="kitt-review-plan-", dir=private) as scratch:
        pinned = Path(scratch) / "plan.sh"
        _create_private_file(Path(scratch), "plan.sh", script_bytes)
        if script_bytes != _read_source(pinned):
            raise CleanRoomEnvironmentError("signed test plan changed during handoff")
        _append_receipt(directory, {"status": "ADMITTED", "contract_sha256": expected_sha256,
                                    "implementer_image": implementer_image,
                                    "validator_image": validator_image,
                                    "test_script_sha256": test_sha256})
        oracle = None
        if require_fixed_oracle_pilot:
            from kitt import re_lab
            oracle = re_lab.verify(full=True)
            if oracle.get("pilot", {}).get("status") != "PASS_SCENARIO":
                _append_receipt(directory, {"status": "BLOCKED_ENV", "gate": "fixed_oracle_pilot"})
                return {"status": "BLOCKED_ENV", "gate": "fixed_oracle_pilot"}
        implement = run_offline_implementer(
            run_id, expected_sha256=expected_sha256, wine_source=source,
            image=implementer_image, state_root=private, engine=engine,
            approved=True, timeout_seconds=timeout_seconds)
        _append_receipt(directory, {"stage": "implementer", **implement})
        if implement["status"] != "IMPLEMENTER_EXITED_UNVERIFIED":
            return {"status": "IMPLEMENTER_FAILED", "implementer": implement}
        prefix = "dlls/" + spec["target_dll"][:-4].lower() + "/"
        changed = _git_changes(source, prefix)
        verified = _validate_independently(
            source, validator_image, pinned, engine=engine,
            timeout_seconds=timeout_seconds, state_root=private)
        after_tests = _git_changes(source, prefix)
        if after_tests != changed:
            _append_receipt(directory, {"stage": "independent_test",
                                        "status": "VALIDATION_FAILED",
                                        "reason": "test changed approved implementation sources"})
            raise CleanRoomEnvironmentError("independent tests changed implementation source files")
        _append_receipt(directory, {"stage": "independent_test", **verified,
                                    "changed": changed})
        return {
            "status": "NEEDS_INDEPENDENT_ORACLE_CONFORMANCE"
            if verified["status"] == "TEST_COMMAND_EXIT_0_NOT_ATTESTED"
            else "VALIDATION_FAILED",
            "contract_sha256": expected_sha256, "changed": changed,
            "validation": verified,
            "fixed_oracle_pilot": oracle.get("status") if oracle else "NOT_RUN",
            "new_code_oracle_match_verified": False,
        }
