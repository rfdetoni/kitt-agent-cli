"""Networkless OCI execution boundary for a clean-room implementation agent.

The image must be an independently reviewed, SHA-pinned offline agent image
containing /usr/local/bin/kitt-cleanroom-implementer and an offline model.
This module refuses to run if those preconditions cannot be met.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

from kitt.cleanroom_handoff import _secure_root, _read_source, _create_private_file, verify
from kitt_protocol.cleanroom import canonical_cleanroom_bytes, decode_cleanroom_contract

_HASH = re.compile(r"(?:sha256:)?[a-f0-9]{64}\Z")
_IMAGE = re.compile(r"sha256:[a-f0-9]{64}\Z")
_RUN = re.compile(r"[a-f0-9]{32}\Z")


class CleanRoomEnvironmentError(RuntimeError):
    pass


TRUSTED_REVIEW_KEY = Path("/etc/kitt/cleanroom-reviewer.pub.pem")


def _verify_independent_review(private: Path, run_id: str, spec: Path) -> None:
    """Require an external Ed25519 signature checked against a root-owned key.

    Neither a command-line --approve flag nor a reviewer name grants authority.
    The reviewer private key MUST be held outside both KITT agent environments.
    """
    key = TRUSTED_REVIEW_KEY
    signature = private / run_id / "reviewer.sig"
    if key.is_symlink() or not key.is_file():
        raise CleanRoomEnvironmentError("no trusted independent reviewer key")
    key_info = key.stat()
    if not stat.S_ISREG(key_info.st_mode) or key_info.st_uid != 0 or key_info.st_mode & 0o022:
        raise CleanRoomEnvironmentError("reviewer public key must be root-owned and not writable")
    if signature.is_symlink() or not signature.is_file():
        raise CleanRoomEnvironmentError("missing independent reviewer signature")
    sig_info = signature.stat()
    if not stat.S_ISREG(sig_info.st_mode) or sig_info.st_size != 64:
        raise CleanRoomEnvironmentError("invalid Ed25519 reviewer signature")
    openssl = shutil.which("openssl")
    if not openssl:
        raise CleanRoomEnvironmentError("OpenSSL signature verification unavailable")
    try:
        result = subprocess.run(
            [openssl, "pkeyutl", "-verify", "-pubin", "-inkey", str(key),
             "-rawin", "-in", str(spec), "-sigfile", str(signature)],
            env={"PATH": "/usr/bin:/bin", "LANG": "C"}, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanRoomEnvironmentError("review signature verifier unavailable") from exc
    if result.returncode:
        raise CleanRoomEnvironmentError("independent review signature failed")


def _worktree(path: Path, private: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise CleanRoomEnvironmentError("Wine source root must be absolute and not a symlink")
    source = path.resolve(strict=True)
    if (source == private or source in private.parents
            or private in source.parents or source == Path("/")):
        raise CleanRoomEnvironmentError("Wine source cannot overlap operator-private state")
    info = source.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise CleanRoomEnvironmentError("Wine source must be an operator-owned directory")
    git = source / ".git"
    if git.is_symlink() or not git.exists():
        raise CleanRoomEnvironmentError("Wine source needs a separately prepared Git checkout")
    return source


def _output_digest(path: Path, *, limit: int = 65536) -> dict:
    size = path.stat().st_size
    if size > limit:
        return {"size": size, "sha256": None, "truncated": True}
    return {"size": size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "truncated": False}


def run_offline_implementer(
    run_id: str, *,
    expected_sha256: str,
    wine_source: Path,
    image: str,
    state_root: Path | None = None,
    engine: str = "docker",
    approved: bool = False,
    timeout_seconds: int = 600,
) -> dict:
    """Run a fixed image entrypoint with exactly two bind mounts and no network.

    This is NOT an Oracle probe and cannot perform arbitrary Docker commands.
    An exit code 0 is not a test pass or proof of clean-room provenance.
    """
    if approved is not True:
        raise PermissionError("independent operator execution approval required")
    if not isinstance(image, str) or _IMAGE.fullmatch(image) is None:
        raise CleanRoomEnvironmentError("image must be a local immutable sha256 image ID")
    if engine not in {"docker", "podman"}:
        raise CleanRoomEnvironmentError("unsupported OCI runtime")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 900:
        raise ValueError("invalid bounded timeout")
    executable = shutil.which(engine)
    if not executable:
        raise CleanRoomEnvironmentError("container runtime unavailable")
    # Force local runtimes even when the operator's default client context
    # points at a remote daemon. Never inherit DOCKER_HOST or CONTAINER_HOST.
    command_prefix = ([executable, "--context", "default"] if engine == "docker"
                      else [executable, "--remote=false"])
    if os.name != "posix":
        raise CleanRoomEnvironmentError("offline implementer requires Linux")
    private = _secure_root(state_root or (Path.home() / ".local/share/kitt/cleanroom"))
    checked = verify(run_id, expected_sha256=expected_sha256, state_root=private)
    if checked["status"] != "VERIFIED_NOT_EXECUTABLE":
        raise CleanRoomEnvironmentError("unverified contract")
    source = _worktree(Path(wine_source), private)
    spec = private / run_id / "contract.json"
    if spec.is_symlink() or not spec.is_file():
        raise CleanRoomEnvironmentError("missing sealed contract")
    # Never inherit DOCKER_HOST, CONTAINER_HOST, model keys or dynamic CLI config.
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home()),
           "LANG": "C", "LC_ALL": "C"}
    try:
        inspect = subprocess.run(
            [*command_prefix, "image", "inspect", "--format", "{{.Id}}", image],
            env=env, capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanRoomEnvironmentError("offline OCI image inspection unavailable") from exc
    if inspect.returncode or inspect.stdout.decode("ascii", "replace").strip() != image:
        raise CleanRoomEnvironmentError("pinned offline image unavailable or wrong identity")
    with tempfile.TemporaryDirectory(prefix="kitt-cleanroom-run-", dir=private) as tmp:
        # Bind an immutable-by-convention, newly created snapshot. Verify the
        # signed bytes and expected digest of that same snapshot to avoid a
        # verify-then-mount race on the mutable original path.
        original = _read_source(spec)
        parsed = decode_cleanroom_contract(original)
        canonical = canonical_cleanroom_bytes(parsed)
        if hashlib.sha256(canonical).hexdigest() != expected_sha256 or original != canonical + b"\n":
            raise CleanRoomEnvironmentError("sealed contract changed before sandbox handoff")
        spec = Path(tmp) / "approved-contract.json"
        _create_private_file(Path(tmp), "approved-contract.json", original)
        spec.chmod(0o400)
        _verify_independent_review(private, run_id, spec)
        out, err = Path(tmp) / "stdout", Path(tmp) / "stderr"
        cidfile = Path(tmp) / "container.cid"
        argv = [
            *command_prefix, "run", "--rm", "--pull", "never",
            "--cidfile", str(cidfile),
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "128", "--memory", "4g", "--cpus", "2",
            "--user", str(os.getuid()) + ":" + str(os.getgid()),
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
            "--env", "HOME=/tmp", "--env", "KITT_CLEANROOM=1",
            "--env", "KITT_LOG_CONTENT=metadata",
            "--mount", "type=bind,src=" + str(source) + ",dst=/wine",
            "--mount", "type=bind,src=" + str(spec) + ",dst=/cleanroom/contract.json,readonly",
            "--workdir", "/wine",
            "--entrypoint", "/usr/local/bin/kitt-cleanroom-implementer",
            image, "--contract", "/cleanroom/contract.json",
            "--contract-sha256", expected_sha256,
        ]
        with out.open("wb") as stdout, err.open("wb") as stderr:
            try:
                completed = subprocess.run(
                    argv, env=env, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, timeout=timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                # Kill only the exact CID created by this invocation. Never use
                # a name or broad Docker cleanup.
                if cidfile.is_file() and not cidfile.is_symlink():
                    cid = cidfile.read_text("ascii").strip()
                    if re.fullmatch(r"[a-f0-9]{64}", cid):
                        try:
                            subprocess.run(
                                [*command_prefix, "rm", "--force", cid], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=20, check=False)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                raise CleanRoomEnvironmentError(
                    "implementer timed out; container cleanup may require operator verification"
                ) from exc
        return {
            "status": "IMPLEMENTER_EXITED_UNVERIFIED" if completed.returncode == 0
                      else "IMPLEMENTER_FAILED",
            "exit_code": completed.returncode,
            "contract_sha256": expected_sha256,
            "image": image,
            "network": "none",
            "mounts": ["wine-source", "approved-contract-readonly"],
            "stdout": _output_digest(out),
            "stderr": _output_digest(err),
            "independent_winetest_passed": False,
            "oracle_match_verified": False,
        }
