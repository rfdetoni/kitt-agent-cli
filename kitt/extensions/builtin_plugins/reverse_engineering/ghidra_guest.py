"""Operator-invoked Ghidra headless analysis INSIDE an isolated guest only.

Not registered as an MCP tool. Ghidra processes untrusted formats; do not run it
on the developer host. Use a disposable VM with filesystem/network quotas.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from .binary import inspect, InspectionError

MAX_REPORT_BYTES = 256 * 1024
ANALYSIS_SECONDS = 90


class GhidraError(ValueError):
    pass


def _analyzer_executable(binary: Path) -> Path:
    binary = binary.absolute()
    if binary.is_symlink() or not binary.is_file():
        raise GhidraError("analyzeHeadless must be a regular operator-installed executable")
    if os.name != "nt" and not os.access(binary, os.X_OK):
        raise GhidraError("analyzeHeadless must be executable")
    return binary


def _read_report(path: Path) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_REPORT_BYTES:
        raise GhidraError("Headless export missing or oversized")
    def unique_pairs(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise GhidraError("Duplicate export field")
            obj[key] = value
        return obj
    try:
        result = json.loads(path.read_text(encoding="utf-8"),
                            object_pairs_hook=unique_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite")))
    except (ValueError, UnicodeError, RecursionError) as ex:
        raise GhidraError("Malformed Ghidra export") from ex
    if (not isinstance(result, dict) or set(result) != {"language", "functions", "truncated"}
            or not isinstance(result["functions"], list)
            or type(result["truncated"]) is not bool or len(result["functions"]) > 512):
        raise GhidraError("Invalid Ghidra export structure")
    for function in result["functions"]:
        if (not isinstance(function, dict) or set(function) != {"name", "entry"}
                or not all(isinstance(v, str) and len(v) <= 160 for v in function.values())):
            raise GhidraError("Invalid exported function")
    return result


def analyze_guest(root: Path, relative_path: str, headless: Path) -> dict:
    """Called from CLI inside disposable VM; no model-provided tool execution."""
    if os.name == "nt":
        raise GhidraError("Use a Linux guest for analyzeHeadless; Windows batch launcher is not supported")
    root = root.resolve(strict=True)
    # Ensure input is inside workspace and parse header before passing to analyzer.
    meta = inspect(root, relative_path)
    target = (root / relative_path).resolve(strict=True)
    # Avoid parsing symlink changes between inspection and Ghidra invocation;
    # running in a private disposable guest is still required.
    headless = _analyzer_executable(headless)
    script_dir = Path(__file__).with_name("ghidra_scripts").resolve(strict=True)
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="kitt-ghidra-") as scratch:
        temporary = Path(scratch)
        report = temporary / "functions.json"
        argv = [
            str(headless), str(temporary), "KittResearch",
            "-import", str(target), "-scriptPath", str(script_dir),
            "-postScript", "KittExportFunctions.java", str(report),
            "-readOnly", "-deleteProject",
            "-analysisTimeoutPerFile", str(ANALYSIS_SECONDS),
            "-max-cpu", "2",
        ]
        with (temporary / "stdout.log").open("wb") as out, (temporary / "stderr.log").open("wb") as err:
            proc = subprocess.Popen(
                argv, cwd=temporary, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                start_new_session=(os.name == "posix"),
                env={k: v for k, v in os.environ.items()
                     if k in ("PATH", "SYSTEMROOT", "WINDIR", "JAVA_HOME", "HOME", "USERPROFILE", "TMP", "TEMP")},
            )
            try:
                code = proc.wait(timeout=ANALYSIS_SECONDS + 30)
            except subprocess.TimeoutExpired as ex:
                raise GhidraError("Headless analysis timed out") from ex
            finally:
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
        if code:
            raise GhidraError(f"Headless analyzer exited with status {code}")
        exported = _read_report(report)
    return {
        "source_sha256": meta["sha256"], "format": meta["format"],
        "architecture": meta["architecture"],
        "analysis": exported, "elapsed_ms": round((time.monotonic() - start) * 1000),
        "tool": "Ghidra Headless", "unverified_guest_attestation": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--analyze-headless", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(analyze_guest(args.workspace, args.path, args.analyze_headless)))
    except (GhidraError, InspectionError, OSError, ValueError) as ex:
        parser.error(str(ex))


if __name__ == "__main__":
    main()
