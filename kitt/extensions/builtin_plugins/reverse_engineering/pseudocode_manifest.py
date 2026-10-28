"""Strict validation for persisted Ghidra pseudocode exports.

The manifest is the only completion receipt. A successful Ghidra import/save log
alone is not evidence that pseudocode files were generated.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FUNCTION_FILE = re.compile(r"fn_[A-Za-z0-9_-]+\.c\Z")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _safe_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    path, windows = Path(value), PureWindowsPath(value)
    return (
        not path.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and all(part not in {"", ".", "..", ".git", ".kitt"} for part in path.parts)
    )


def _hash_regular(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("not a regular file")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            total += len(block)
            digest.update(block)
        after = os.fstat(fd)
        if (
            total != before.st_size
            or (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
            != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)
        ):
            raise ValueError("file changed while hashing")
        return digest.hexdigest(), total
    finally:
        os.close(fd)


def validate_pseudocode_manifest(
    root_dir: str | Path,
    relative_manifest: str = "reimplement/pseudoc/manifest.json",
    *,
    expected_source_path: str | None = None,
    expected_source_sha256: str | None = None,
    expected_source_size: int | None = None,
) -> tuple[dict | None, str]:
    """Return the manifest and an empty issue only for a complete verified export."""
    if not _safe_relative(relative_manifest):
        return None, "Pseudocode manifest path is unsafe."
    try:
        root = Path(root_dir).resolve(strict=True)
    except (OSError, RuntimeError):
        return None, "Workspace root is unavailable."
    if not root.is_dir():
        return None, "Workspace root is not a directory."

    manifest_path = root.joinpath(*Path(relative_manifest).parts)
    cursor = root
    for part in Path(relative_manifest).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None, "Pseudocode manifest path contains a symlink."
    if not manifest_path.exists():
        return None, f"Pseudocode manifest is missing: {relative_manifest}"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return None, "Pseudocode manifest is not a regular file."
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            return None, "Pseudocode manifest exceeds the size limit."
        payload = json.loads(
            manifest_path.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
        )
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None, "Pseudocode manifest is malformed or unreadable."
    if not isinstance(payload, dict):
        return None, "Pseudocode manifest must be a JSON object."

    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != 1
        or payload.get("status") != "COMPLETE"
        or payload.get("complete") is not True
        or payload.get("truncated") is not False
        or type(payload.get("failed_count")) is not int
        or payload["failed_count"] != 0
        or type(payload.get("decompiled_count")) is not int
        or payload["decompiled_count"] <= 0
        or type(payload.get("function_count")) is not int
        or payload["function_count"] != payload["decompiled_count"]
        or not isinstance(payload.get("functions"), list)
        or len(payload["functions"]) != payload["function_count"]
        or not isinstance(payload.get("batches"), list)
        or not payload["batches"]
    ):
        return payload, (
            f"Pseudocode export is incomplete: status={payload.get('status')!r}, "
            f"complete={payload.get('complete')!r}, truncated={payload.get('truncated')!r}, "
            f"decompiled={payload.get('decompiled_count')!r}, failed={payload.get('failed_count')!r}."
        )

    cursor_index = 0
    batch_functions = batch_decompiled = batch_failed = 0
    for index, batch in enumerate(payload["batches"]):
        if not isinstance(batch, dict):
            return payload, "Pseudocode manifest contains an invalid batch."
        start, end = batch.get("start_index"), batch.get("next_index")
        count, good, failed = (
            batch.get("function_count"), batch.get("decompiled_count"), batch.get("failed_count")
        )
        if (
            type(start) is not int or type(end) is not int or start != cursor_index or end < start
            or type(count) is not int or count != end - start
            or type(good) is not int or type(failed) is not int
            or good < 0 or failed < 0 or good + failed != count
            or type(batch.get("truncated")) is not bool
            or (not batch["truncated"] and index != len(payload["batches"]) - 1)
        ):
            return payload, "Pseudocode manifest has gaps or inconsistent batch counters."
        cursor_index = end
        batch_functions += count
        batch_decompiled += good
        batch_failed += failed
    if (
        payload["batches"][-1]["truncated"]
        or cursor_index != payload.get("next_index")
        or cursor_index != payload["function_count"]
        or batch_functions != payload["function_count"]
        or batch_decompiled != payload["decompiled_count"]
        or batch_failed != payload["failed_count"]
    ):
        return payload, "Pseudocode manifest does not prove complete function-table coverage."

    source_path = payload.get("source_path")
    source_sha256 = payload.get("source_sha256")
    source_size = payload.get("source_size_bytes")
    if (
        not _safe_relative(source_path)
        or not isinstance(source_sha256, str) or not _HASH.fullmatch(source_sha256)
        or type(source_size) is not int or source_size <= 0
    ):
        return payload, "Pseudocode manifest has invalid source identity fields."
    if expected_source_path is not None and source_path != expected_source_path:
        return payload, "Pseudocode manifest refers to a different source path."
    if expected_source_sha256 is not None and source_sha256 != expected_source_sha256:
        return payload, "Pseudocode manifest source SHA-256 does not match the analyzed binary."
    if expected_source_size is not None and source_size != expected_source_size:
        return payload, "Pseudocode manifest source size does not match the analyzed binary."

    source = root.joinpath(*Path(source_path).parts)
    cursor = root
    for part in Path(source_path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return payload, "Pseudocode source path contains a symlink."
    try:
        resolved_source = source.resolve(strict=True)
        resolved_source.relative_to(root)
        source_hash, actual_size = _hash_regular(resolved_source)
    except (OSError, ValueError, RuntimeError):
        return payload, "Pseudocode source is missing, unsafe, or changed while hashing."
    if actual_size != source_size or source_hash != source_sha256:
        return payload, "Pseudocode source size or SHA-256 does not match the manifest."

    seen = set()
    for function in payload["functions"]:
        if not isinstance(function, dict):
            return payload, "Pseudocode manifest contains an invalid function."
        filename = function.get("file")
        digest = function.get("sha256")
        size = function.get("bytes")
        if (
            function.get("status") != "DECOMPILED"
            or not isinstance(filename, str) or not _FUNCTION_FILE.fullmatch(filename)
            or filename in seen
            or not isinstance(digest, str) or not _HASH.fullmatch(digest)
            or type(size) is not int or size <= 0
            or function.get("error") not in ("", None)
        ):
            return payload, "Pseudocode manifest contains a failed or invalid function record."
        seen.add(filename)
        output = manifest_path.parent / filename
        if output.is_symlink() or not output.is_file():
            return payload, f"Pseudocode output is missing: {filename}"
        try:
            actual_hash, actual_bytes = _hash_regular(output)
        except (OSError, ValueError):
            return payload, f"Pseudocode output is unsafe or changed: {filename}"
        if actual_bytes != size or actual_hash != digest:
            return payload, f"Pseudocode output size or SHA-256 mismatch: {filename}"
    return payload, ""


def pseudocode_completion_issue(root_dir: str | Path, objective: str) -> str:
    """Require a complete verified pseudocode export for explicit user requests."""
    import re

    request = re.compile(
        r"\b(?:pseudo[\s-]?c(?:ódigo|odigo)?|pseudocode|decompila(?:ção|cao|tion|ted)|decompiled\s+c)\b",
        re.IGNORECASE,
    )
    if not request.search(str(objective or "")):
        return ""
    manifest, issue = validate_pseudocode_manifest(root_dir)
    if issue:
        return issue
    if manifest is None:
        return "Pseudocode manifest is missing."
    return ""
