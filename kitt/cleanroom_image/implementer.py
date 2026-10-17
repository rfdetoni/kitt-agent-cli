#!/usr/bin/env python3
"""Offline, single-purpose Wine patch agent; no remote provider, browser or shell.

This entrypoint is intended to execute ONLY inside the networkless KITT OCI sandbox.
The GGUF model and llama-cli binary are supplied by an operator-built immutable image.
A successful patch application is not evidence of conformance or code provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

MAX_SPEC = 65536
MAX_PATCH = 262144
MAX_OUTPUT_TOKENS = 4096
MODULE = re.compile(r"[A-Za-z0-9_-]{1,96}\.dll\Z")
FUNCTION = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def fail(reason: str) -> int:
    print(json.dumps({"status": "BLOCKED_ENV", "reason": reason}), flush=True)
    return 2


def _duplicates(pairs):
    output = {}
    for key, val in pairs:
        if key in output:
            raise ValueError("duplicate contract key")
        output[key] = val
    return output


def load_contract(path: Path, digest: str) -> dict:
    if str(path) != "/cleanroom/contract.json":
        raise ValueError("contract must be fixed read-only mount")
    data = path.read_bytes()
    if len(data) > MAX_SPEC:
        raise ValueError("oversized contract")
    obj = json.loads(data.decode("utf-8"), object_pairs_hook=_duplicates)
    if not isinstance(obj, dict) or set(obj) != {
        "target_dll", "function_name", "signature", "calling_convention", "observed_behaviors"
    }:
        raise ValueError("invalid contract structure")
    if not isinstance(obj["target_dll"], str) or not MODULE.fullmatch(obj["target_dll"]):
        raise ValueError("invalid target dll")
    if not isinstance(obj["function_name"], str) or not FUNCTION.fullmatch(obj["function_name"]):
        raise ValueError("invalid function")
    canonical = json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != digest or data != canonical + b"\n":
        raise ValueError("contract digest mismatch")
    return obj


def _module_path(obj: dict) -> str:
    return "dlls/" + obj["target_dll"][:-4].lower() + "/"


def _patch_paths(patch: str, prefix: str) -> None:
    """Restrict all diff metadata and changes to the target DLL tree."""
    if not patch.startswith("diff --git "):
        raise ValueError("response does not begin with unified git patch")
    if len(patch.encode("utf-8")) > MAX_PATCH:
        raise ValueError("patch exceeds byte limit")
    heads = 0
    for line in patch.splitlines():
        if line.startswith(("GIT binary patch", "Binary files ", "rename ", "copy ",
                            "old mode ", "new mode ", "deleted file mode ", "new file mode ",
                            "similarity index ", "dissimilarity index ", "Submodule ")):
            raise ValueError("unsafe patch metadata")
        if line.startswith("diff --git "):
            fields = line.split(" ")
            if len(fields) != 4 or not fields[2].startswith("a/") or not fields[3].startswith("b/"):
                raise ValueError("invalid git diff header")
            if fields[2][2:] != fields[3][2:]:
                raise ValueError("renamed path not allowed")
            heads += 1
            _check_path(fields[2][2:], prefix)
        elif line.startswith("--- ") or line.startswith("+++ "):
            path = line[4:]
            if path == "/dev/null":
                continue
            if not path.startswith(("a/", "b/")):
                raise ValueError("invalid unified diff path")
            _check_path(path[2:], prefix)
    if not 1 <= heads <= 12:
        raise ValueError("missing or excessive patch sections")


def _check_path(name: str, prefix: str) -> None:
    path = PurePosixPath(name)
    if (not name.startswith(prefix) or name.endswith("/") or
            any(bit in ("", ".", "..") for bit in name.split("/")) or
            "\x00" in name or "\\" in name or path.is_absolute() or
            len(name) > 256 or any(ord(c) < 32 for c in name)):
        raise ValueError("patch targets outside approved Wine DLL tree")
    on_disk = Path("/wine") / name
    if on_disk.is_symlink() or any(p.is_symlink() for p in on_disk.parents if p != Path("/")):
        raise ValueError("patch traverses a symlink")
    if on_disk.exists() and not on_disk.is_file():
        raise ValueError("patch targets non-file")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--contract-sha256", required=True)
    args = parser.parse_args()
    if os.environ.get("KITT_CLEANROOM") != "1" or not Path("/wine/.git").exists():
        return fail("not running inside isolated Wine implementation checkout")
    if not re.fullmatch(r"[a-f0-9]{64}", args.contract_sha256):
        return fail("invalid approved SHA-256")
    try:
        spec = load_contract(args.contract, args.contract_sha256)
        prefix = _module_path(spec)
        if not (Path("/wine") / prefix).is_dir():
            return fail("target module not present in authorized Wine checkout")
        model = Path("/opt/models/implementer.gguf")
        binary = Path("/usr/local/bin/llama-cli")
        if not model.is_file() or not binary.is_file():
            return fail("preinstalled offline model or llama-cli missing")
        prompt = (
            "You implement public Wine API compatibility. Work independently from black-box "
            "behavioral observations. Do NOT assume any Microsoft private implementation. "
            "Return ONLY a git unified diff starting with 'diff --git'. "
            "All modified files MUST be beneath " + prefix + ". "
            "Do not use code fences, absolute paths or shell commands.\n"
            "Approved specification:\n" +
            json.dumps(spec, sort_keys=True, separators=(",", ":")) + "\n"
        )
        response = subprocess.run(
            [str(binary), "--model", str(model), "--prompt", prompt,
             "--n-predict", str(MAX_OUTPUT_TOKENS), "--temp", "0"],
            cwd="/wine", env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=300, check=False,
        )
        if response.returncode:
            return fail("offline local inference failed")
        if len(response.stdout) > MAX_PATCH:
            return fail("model patch exceeds byte limit")
        patch = response.stdout.decode("utf-8", "strict").strip() + "\n"
        _patch_paths(patch, prefix)
        # Disable user/system git configuration and Git external diff filters.
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C",
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        # Reject local unreviewed modifications before applying generated patch.
        dirty = subprocess.run(["git", "-C", "/wine", "status", "--porcelain"],
                               env=env, capture_output=True, timeout=20, check=False)
        if dirty.returncode or dirty.stdout:
            return fail("Wine checkout must be clean before model writes")
        verified = subprocess.run(
            ["git", "-C", "/wine", "apply", "--check", "-"],
            input=patch.encode(), env=env, capture_output=True, timeout=30, check=False,
        )
        if verified.returncode:
            return fail("offline patch failed git apply --check")
        applied = subprocess.run(
            ["git", "-C", "/wine", "apply", "-"],
            input=patch.encode(), env=env, capture_output=True, timeout=30, check=False,
        )
        if applied.returncode:
            return fail("offline patch application failed")
        print(json.dumps({"status": "PATCH_APPLIED_UNVERIFIED",
                          "contract_sha256": args.contract_sha256,
                          "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
                          "target": prefix}), flush=True)
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return fail(type(exc).__name__ + ": validation or tool execution failed")


if __name__ == "__main__":
    sys.exit(main())
