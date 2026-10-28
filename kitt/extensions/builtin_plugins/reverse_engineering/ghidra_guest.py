"""Operator-invoked, read-only Ghidra export inside an isolated Linux guest.

The ELF is never executed. Decompilation artifacts are persisted under a
workspace-relative output directory; incomplete batches are explicitly PARTIAL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

from .binary import MAX_BYTES, InspectionError, inspect
from .large_elf import inspect_large_elf

MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_BATCH_FUNCTIONS = 128
MAX_FUNCTION_PSEUDOCODE_BYTES = 256 * 1024
MAX_LOG_BYTES = 2 * 1024 * 1024
ANALYSIS_SECONDS = 300
DECOMPILE_SECONDS = 3
_BATCH_NAME = re.compile(r"batch-(\d{6,})\.json\Z")
_FUNCTION_NAME = re.compile(r"fn_[A-Za-z0-9_-]+\.c\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class GhidraError(ValueError):
    pass


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GhidraError("Duplicate JSON field")
        result[key] = value
    return result


def _json_file(path: Path, max_bytes: int) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise GhidraError("JSON artifact missing, symlinked, or oversized")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise GhidraError("Malformed JSON artifact") from exc


def _read_report(path: Path) -> dict:
    result = _json_file(path, MAX_REPORT_BYTES)
    required = {"language", "functions", "truncated", "start_index", "next_index"}
    if (
        not isinstance(result, dict)
        or set(result) != required
        or not isinstance(result["language"], str)
        or not result["language"]
        or len(result["language"]) > 160
        or not isinstance(result["functions"], list)
        or len(result["functions"]) > MAX_BATCH_FUNCTIONS
        or type(result["truncated"]) is not bool
        or type(result["start_index"]) is not int
        or type(result["next_index"]) is not int
        or result["start_index"] < 0
        or result["next_index"] < result["start_index"]
    ):
        raise GhidraError("Invalid Ghidra export structure")

    entries = set()
    filenames = set()
    for function in result["functions"]:
        fields = {"name", "entry", "status", "file", "error", "pseudocode_bytes"}
        if not isinstance(function, dict) or set(function) != fields:
            raise GhidraError("Invalid exported function fields")
        if (
            not isinstance(function["name"], str) or not function["name"] or len(function["name"]) > 160
            or not isinstance(function["entry"], str) or not function["entry"] or len(function["entry"]) > 160
            or function["status"] not in {"DECOMPILED", "FAILED", "TRUNCATED"}
            or not isinstance(function["file"], str) or len(function["file"]) > 100
            or not isinstance(function["error"], str) or len(function["error"]) > 500
            or type(function["pseudocode_bytes"]) is not int
            or function["pseudocode_bytes"] < 0
            or function["pseudocode_bytes"] > MAX_FUNCTION_PSEUDOCODE_BYTES
        ):
            raise GhidraError("Invalid exported function value")
        if function["entry"] in entries:
            raise GhidraError("Duplicate function entry point")
        entries.add(function["entry"])
        if function["status"] == "DECOMPILED":
            if (
                not _FUNCTION_NAME.fullmatch(function["file"])
                or function["file"] in filenames
                or function["pseudocode_bytes"] <= 0
                or function["error"]
            ):
                raise GhidraError("Invalid decompiled pseudocode reference")
            filenames.add(function["file"])
        elif function["file"] or function["pseudocode_bytes"] != 0 or not function["error"]:
            raise GhidraError("Failed/truncated functions must not claim a pseudocode file")

    if result["next_index"] - result["start_index"] != len(result["functions"]):
        raise GhidraError("Ghidra export indices do not match the exported function count")
    return result


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_workspace_dir(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\x00" in relative or "\\" in relative:
        raise GhidraError("Output directory must be a relative workspace path")
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts:
        raise GhidraError("Output directory must be a relative workspace path")
    if any(part in {"..", ".git", ".env"} for part in candidate.parts):
        raise GhidraError("Protected or traversing output path")
    current = root
    for part in candidate.parts:
        current = current / part
        if current.is_symlink():
            raise GhidraError("Symlink output paths are forbidden")
    current.mkdir(parents=True, exist_ok=True)
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise GhidraError("Output directory escapes workspace") from exc
    if not resolved.is_dir():
        raise GhidraError("Output path is not a directory")
    return resolved


def _atomic_write(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise GhidraError("Symlink output file is forbidden")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _read_batches(output_dir: Path, source_sha256: str) -> list[dict]:
    batches = []
    for path in sorted(output_dir.iterdir()):
        if not _BATCH_NAME.fullmatch(path.name):
            continue
        payload = _json_file(path, MAX_REPORT_BYTES)
        required = {
            "schema_version", "source_sha256", "source_path", "start_index",
            "next_index", "truncated", "language", "functions",
        }
        if (
            not isinstance(payload, dict) or set(payload) != required
            or payload["schema_version"] != 1
            or payload["source_sha256"] != source_sha256
            or not isinstance(payload["source_path"], str)
            or type(payload["start_index"]) is not int or payload["start_index"] < 0
            or type(payload["next_index"]) is not int or payload["next_index"] < payload["start_index"]
            or type(payload["truncated"]) is not bool
            or not isinstance(payload["language"], str)
            or not isinstance(payload["functions"], list)
            or payload["next_index"] - payload["start_index"] != len(payload["functions"])
        ):
            raise GhidraError("Existing Ghidra batch manifest is invalid or belongs to another ELF")
        for function in payload["functions"]:
            if not isinstance(function, dict) or set(function) != {
                "name", "entry", "status", "file", "sha256", "bytes", "error",
            }:
                raise GhidraError("Existing Ghidra function manifest is malformed")
            if function["status"] == "DECOMPILED":
                name = function["file"]
                if (
                    not isinstance(name, str) or not _FUNCTION_NAME.fullmatch(name)
                    or not isinstance(function["sha256"], str) or not _HASH.fullmatch(function["sha256"])
                    or type(function["bytes"]) is not int or function["bytes"] <= 0
                ):
                    raise GhidraError("Existing pseudocode reference is invalid")
                source = output_dir / name
                if source.is_symlink() or not source.is_file() or source.stat().st_size != function["bytes"]:
                    raise GhidraError("Existing pseudocode file is missing or has changed")
                if _sha256_file(source) != function["sha256"]:
                    raise GhidraError("Existing pseudocode SHA-256 mismatch")
            elif (
                function["status"] not in {"FAILED", "TRUNCATED"}
                or function["file"] or function["sha256"] or function["bytes"] != 0
                or not function["error"]
            ):
                raise GhidraError("Existing failed-function record is invalid")
        batches.append(payload)
    return sorted(batches, key=lambda item: item["start_index"])


def _aggregate_manifest(output_dir: Path, *, source_path: str, source: dict) -> dict:
    batches = _read_batches(output_dir, source["sha256"])
    cursor = 0
    contiguous = bool(batches)
    for index, batch in enumerate(batches):
        if batch["start_index"] != cursor:
            contiguous = False
            break
        cursor = batch["next_index"]
        if not batch["truncated"] and index != len(batches) - 1:
            contiguous = False
            break

    functions = [function for batch in batches for function in batch["functions"]]
    entries = [function["entry"] for function in functions]
    if len(entries) != len(set(entries)):
        raise GhidraError("Overlapping Ghidra batches contain duplicate function entries")
    decompiled = [function for function in functions if function["status"] == "DECOMPILED"]
    failed = [function for function in functions if function["status"] != "DECOMPILED"]
    terminal = contiguous and bool(batches) and not batches[-1]["truncated"]
    function_truncated = any(function["status"] == "TRUNCATED" for function in functions)
    complete = terminal and bool(decompiled) and not failed and not function_truncated
    return {
        "schema_version": 1,
        "status": "COMPLETE" if complete else "PARTIAL",
        "complete": bool(complete),
        "source_path": source_path,
        "source_sha256": source["sha256"],
        "source_size_bytes": source["size"],
        "format": source["format"],
        "architecture": source["architecture"],
        "tool": "Ghidra Headless",
        "language": batches[-1]["language"] if batches else "",
        "batch_count": len(batches),
        "batches": [
            {
                "start_index": batch["start_index"],
                "next_index": batch["next_index"],
                "truncated": batch["truncated"],
                "function_count": len(batch["functions"]),
                "decompiled_count": sum(f["status"] == "DECOMPILED" for f in batch["functions"]),
                "failed_count": sum(f["status"] != "DECOMPILED" for f in batch["functions"]),
            }
            for batch in batches
        ],
        "function_count": len(functions),
        "decompiled_count": len(decompiled),
        "failed_count": len(failed),
        "truncated": bool(not terminal or function_truncated),
        "next_index": cursor,
        "functions": functions,
        "original_executed": False,
    }


def _persist_logs(root: Path, output_dir_relative: str, start_index: int, stdout: Path, stderr: Path) -> None:
    logs_dir = _safe_workspace_dir(root, str(Path(output_dir_relative) / "logs"))
    prefix = f"batch-{start_index:06d}"
    for suffix, path in (("stdout", stdout), ("stderr", stderr)):
        if not path.is_file() or path.is_symlink():
            continue
        raw = path.read_bytes()
        if len(raw) > MAX_LOG_BYTES:
            raw = raw[:MAX_LOG_BYTES] + b"\n[log truncated by KITT safety limit]\n"
        _atomic_write(logs_dir / f"{prefix}.{suffix}.log", raw)


def _persist_batch(
    root: Path,
    *,
    output_dir_relative: str,
    source_path: str,
    source: dict,
    exported: dict,
    temporary_pseudoc: Path,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> dict:
    output_dir = _safe_workspace_dir(root, output_dir_relative)
    existing = _read_batches(output_dir, source["sha256"])
    for batch in existing:
        if batch["start_index"] == exported["start_index"]:
            continue
        if not batch["truncated"] and batch["start_index"] < exported["start_index"]:
            raise GhidraError("A previous batch already proved end-of-function-table")
        if max(batch["start_index"], exported["start_index"]) < min(batch["next_index"], exported["next_index"]):
            raise GhidraError("New Ghidra batch overlaps an existing batch")

    rows = []
    for function in exported["functions"]:
        row = {
            "name": function["name"], "entry": function["entry"],
            "status": function["status"], "file": "", "sha256": "",
            "bytes": 0, "error": function["error"],
        }
        if function["status"] == "DECOMPILED":
            filename = function["file"]
            source_file = temporary_pseudoc / filename
            if source_file.is_symlink() or not source_file.is_file():
                raise GhidraError("Ghidra reported pseudocode but did not create the file")
            raw = source_file.read_bytes()
            if len(raw) != function["pseudocode_bytes"] or not raw or len(raw) > MAX_FUNCTION_PSEUDOCODE_BYTES:
                raise GhidraError("Ghidra pseudocode size does not match its report")
            output_file = output_dir / filename
            _atomic_write(output_file, raw)
            row.update(file=filename, sha256=_sha256_bytes(raw), bytes=len(raw), error="")
        rows.append(row)

    batch = {
        "schema_version": 1,
        "source_sha256": source["sha256"],
        "source_path": source_path,
        "start_index": exported["start_index"],
        "next_index": exported["next_index"],
        "truncated": exported["truncated"],
        "language": exported["language"],
        "functions": rows,
    }
    batch_path = output_dir / f"batch-{exported['start_index']:06d}.json"
    _atomic_write(batch_path, (json.dumps(batch, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    if stdout_log is not None and stderr_log is not None:
        _persist_logs(root, output_dir_relative, exported["start_index"], stdout_log, stderr_log)

    manifest = _aggregate_manifest(output_dir, source_path=source_path, source=source)
    _atomic_write(
        output_dir / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    if manifest["complete"]:
        next_steps = "All recorded functions were decompiled and the contiguous batch sequence reached the end of the function table."
    else:
        next_steps = (
            "PARTIAL: do not claim full reverse engineering. Continue with --start-index "
            f"{manifest['next_index']} when the last batch reports more functions; inspect failed-function entries and logs."
        )
    readme = (
        "# Ghidra pseudocode export\n\n"
        f"- Status: {manifest['status']}\n"
        f"- Source: {source_path}\n"
        f"- SHA-256: {source['sha256']}\n"
        f"- Functions in manifest: {manifest['function_count']}\n"
        f"- Decompiled C files: {manifest['decompiled_count']}\n"
        f"- Failed/truncated functions: {manifest['failed_count']}\n"
        f"- Remaining-function truncation: {manifest['truncated']}\n"
        f"- Original executed: {manifest['original_executed']}\n\n"
        f"{next_steps}\n"
    )
    _atomic_write(output_dir / "README.md", readme.encode("utf-8"))
    return manifest


def _analyzer_executable(binary: Path) -> Path:
    binary = binary.absolute()
    if binary.is_symlink() or not binary.is_file():
        raise GhidraError("analyzeHeadless must be a regular operator-installed executable")
    if os.name != "nt" and not os.access(binary, os.X_OK):
        raise GhidraError("analyzeHeadless must be executable")
    return binary


def _inspect_source(root: Path, relative_path: str) -> dict:
    if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path or "\\" in relative_path:
        raise GhidraError("Source path must be workspace-relative")
    relative = Path(relative_path)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise GhidraError("Source path must not traverse the workspace")
    target = root.joinpath(*relative.parts)
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise GhidraError("Cannot stat source binary") from exc
    if size > MAX_BYTES:
        return inspect_large_elf(root, relative_path)
    return inspect(root, relative_path)


def _record_run_status(output_dir: Path, status: str, message: str, source: dict | None = None) -> None:
    payload = {
        "schema_version": 1, "status": status, "message": str(message)[:1000],
        "source_sha256": str((source or {}).get("sha256") or ""),
    }
    _atomic_write(output_dir / "last-run.json", (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def analyze_guest(
    root: Path,
    relative_path: str,
    headless: Path,
    *,
    output_dir: str = "reimplement/pseudoc",
    start_index: int = 0,
    max_functions: int = MAX_BATCH_FUNCTIONS,
) -> dict:
    """Decompile one bounded function batch inside a disposable Linux guest."""
    if os.name == "nt":
        raise GhidraError("Use a Linux guest for analyzeHeadless; Windows batch launcher is not supported")
    if type(start_index) is not int or not 0 <= start_index <= 10_000_000:
        raise GhidraError("start_index must be an integer between 0 and 10000000")
    if type(max_functions) is not int or not 1 <= max_functions <= MAX_BATCH_FUNCTIONS:
        raise GhidraError(f"max_functions must be between 1 and {MAX_BATCH_FUNCTIONS}")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise GhidraError("Workspace must be a directory")
    source = _inspect_source(root, relative_path)
    target = (root / relative_path).resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise GhidraError("Source binary escapes workspace") from exc
    output_path = _safe_workspace_dir(root, output_dir)
    try:
        headless = _analyzer_executable(headless)
    except GhidraError as exc:
        _record_run_status(output_path, "BLOCKED_ENV", str(exc), source)
        raise

    script_dir = Path(__file__).with_name("ghidra_scripts").resolve(strict=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="kitt-ghidra-") as scratch:
        scratch_dir = Path(scratch)
        project_dir = scratch_dir / "project"
        export_dir = scratch_dir / "export"
        temporary_pseudoc = export_dir / "pseudoc"
        report = export_dir / "functions.json"
        project_dir.mkdir()
        export_dir.mkdir()
        wait_seconds = ANALYSIS_SECONDS + max_functions * DECOMPILE_SECONDS + 60
        argv = [
            str(headless), str(project_dir), "KittResearch",
            "-import", str(target), "-scriptPath", str(script_dir),
            "-postScript", "KittExportFunctions.java", str(temporary_pseudoc),
            str(report), str(start_index), str(max_functions),
            "-readOnly", "-deleteProject",
            "-analysisTimeoutPerFile", str(ANALYSIS_SECONDS),
            "-max-cpu", "2",
        ]
        timed_out = False
        code = None
        stdout_log = export_dir / "stdout.log"
        stderr_log = export_dir / "stderr.log"
        with stdout_log.open("wb") as out, stderr_log.open("wb") as err:
            proc = subprocess.Popen(
                argv, cwd=scratch_dir, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                start_new_session=(os.name == "posix"),
                env={k: v for k, v in os.environ.items()
                     if k in ("PATH", "SYSTEMROOT", "WINDIR", "JAVA_HOME", "HOME", "USERPROFILE", "TMP", "TEMP")},
            )
            try:
                code = proc.wait(timeout=wait_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if proc.poll() is None:
                    proc.kill()
                proc.wait()

        _persist_logs(root, output_dir, start_index, stdout_log, stderr_log)
        if timed_out:
            _record_run_status(output_path, "BLOCKED_ENV", "Headless analysis exceeded its bounded time budget", source)
            raise GhidraError("Headless analysis timed out; bounded logs were saved")
        if code:
            _record_run_status(output_path, "FAILED", f"Headless analyzer exited with status {code}", source)
            raise GhidraError(f"Headless analyzer exited with status {code}")
        exported = _read_report(report)
        manifest = _persist_batch(
            root, output_dir_relative=output_dir, source_path=relative_path,
            source=source, exported=exported, temporary_pseudoc=temporary_pseudoc,
        )

    _record_run_status(output_path, manifest["status"], "Ghidra batch finished", source)
    return {
        "source_sha256": source["sha256"],
        "format": source["format"],
        "architecture": source["architecture"],
        "tool": "Ghidra Headless",
        "output_dir": output_dir,
        "manifest_path": f"{output_dir}/manifest.json",
        "status": manifest["status"],
        "complete": manifest["complete"],
        "function_count": manifest["function_count"],
        "decompiled_count": manifest["decompiled_count"],
        "failed_count": manifest["failed_count"],
        "truncated": manifest["truncated"],
        "next_index": manifest["next_index"],
        "batch_start_index": start_index,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "original_executed": False,
        "unverified_guest_attestation": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--analyze-headless", type=Path, required=True)
    parser.add_argument("--output-dir", default="reimplement/pseudoc")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max-functions", type=int, default=MAX_BATCH_FUNCTIONS)
    args = parser.parse_args()
    try:
        print(json.dumps(analyze_guest(
            args.workspace, args.path, args.analyze_headless,
            output_dir=args.output_dir, start_index=args.start_index,
            max_functions=args.max_functions,
        ), ensure_ascii=False))
    except (GhidraError, InspectionError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
