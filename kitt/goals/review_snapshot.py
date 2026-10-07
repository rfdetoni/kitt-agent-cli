from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath
from typing import Any

from kitt.security.context import ExecutionSecurityContext


MAX_REVIEW_SNAPSHOT_CHARS = 120_000
MAX_REVIEW_FILES = 24
MAX_REVIEW_FILE_CHARS = 24_000
_REVIEWABLE_EXTENSIONS = frozenset({
    ".py", ".pyi", ".rs", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".java", ".kt", ".kts", ".go", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".cs", ".rb", ".php", ".sql", ".sh", ".bash", ".zsh", ".ps1",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".gradle", ".properties",
    ".html", ".css", ".scss", ".proto",
})
_REVIEWABLE_BASENAMES = frozenset({
    "Dockerfile", "Makefile", "Jenkinsfile", "Procfile", "Rakefile",
    "pyproject.toml", "Cargo.toml", "package.json", "pom.xml",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
})


def is_reviewable_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    if not normalized or normalized.startswith("../"):
        return False
    pure = PurePosixPath(normalized)
    return (
        pure.name in _REVIEWABLE_BASENAMES
        or pure.suffix.lower() in _REVIEWABLE_EXTENSIONS
    )


def _resolve_symbol_path(runtime, inner: dict) -> str:
    value = str(inner.get("symbol_id") or inner.get("symbol") or "").strip()
    if not value:
        return ""
    engine = getattr(runtime.registry, "native_engine", None)
    if engine is None:
        return ""
    try:
        found = engine.read_symbol(value)
        if not found and hasattr(engine, "find_symbols"):
            matches = engine.find_symbols(value, limit=1)
            if matches:
                found = engine.read_symbol(matches[0].get("id", ""))
        if isinstance(found, dict):
            symbol = found.get("symbol")
            if isinstance(symbol, dict):
                return str(symbol.get("path") or "")
    except Exception:
        return ""
    return ""


def paths_from_tool_start(runtime, event) -> list[str]:
    tool_name = str(getattr(event, "tool_name", "") or "")
    args = dict(getattr(event, "args", None) or {})
    paths: list[str] = []
    if tool_name == "write_file":
        path = args.get("path") or args.get("file")
        if path:
            paths.append(str(path))
    elif tool_name == "apply_patch":
        try:
            paths.extend(
                block.file_path
                for block in runtime.registry.parser.parse(
                    str(args.get("patch") or "")
                )
            )
        except Exception:
            pass
    elif tool_name == "kitt_runtime":
        operation = str(args.get("operation") or "")
        raw_inner = args.get("arguments")
        inner: dict[str, Any] = (
            dict(raw_inner) if isinstance(raw_inner, dict) else {}
        )
        if operation == "repo.edit_symbol":
            path = (
                inner.get("path")
                or inner.get("file")
                or _resolve_symbol_path(runtime, inner)
            )
            if path:
                paths.append(str(path))
        elif operation == "patch.apply":
            try:
                paths.extend(
                    block.file_path
                    for block in runtime.registry.parser.parse(
                        str(inner.get("patch") or "")
                    )
                )
            except Exception:
                pass
    return [
        path
        for path in paths
        if path and not path.startswith("/") and is_reviewable_path(path)
    ]


def _filter_diff_for_paths(
    diff_text: str,
    paths: list[str],
) -> tuple[str, set[str], bool]:
    def normalize(path):
        value = str(path).replace("\\", "/")
        while value.startswith("./"):
            value = value[2:]
        return value

    wanted = {normalize(path) for path in paths if path}
    if not wanted or not diff_text.strip():
        return "", set(), True
    blocks: list[str] = []
    current: list[str] = []
    matched_paths: set[str] = set()
    complete = True

    def flush():
        nonlocal current, complete
        if not current:
            return
        try:
            tokens = shlex.split(current[0])
            if len(tokens) < 4:
                complete = False
                current = []
                return
            old_path = tokens[2][2:] if tokens[2].startswith("a/") else tokens[2]
            new_path = tokens[3][2:] if tokens[3].startswith("b/") else tokens[3]
            matches = wanted & {
                old_path.replace("\\", "/"),
                new_path.replace("\\", "/"),
            }
            if matches:
                blocks.append("\n".join(current))
                matched_paths.update(matches)
        except Exception:
            complete = False
        current = []

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush()
            current = [line]
        elif current:
            current.append(line)
    flush()
    return "\n".join(blocks), matched_paths, complete


def _diff_context_ranges(diff_text: str, path: str) -> list[tuple[int, int]]:
    normalized = str(path or "").replace("\\", "/").lstrip("./")
    current_matches = False
    ranges: list[tuple[int, int]] = []
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for line in str(diff_text or "").splitlines():
        if line.startswith("diff --git "):
            try:
                tokens = shlex.split(line)
                candidates = set()
                for token in tokens[2:4]:
                    value = token[2:] if token.startswith(("a/", "b/")) else token
                    candidates.add(value.replace("\\", "/"))
                current_matches = normalized in candidates
            except Exception:
                current_matches = False
            continue
        if not current_matches:
            continue
        match = hunk_re.match(line)
        if not match:
            continue
        start = max(1, int(match.group(1)) - 24)
        count = max(1, int(match.group(2) or 1))
        end = start + min(count + 48, 120)
        if ranges and start <= ranges[-1][1] + 8:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
        if len(ranges) >= 8:
            break
    return ranges


def collect_review_snapshot(
    runtime,
    goal,
    security: ExecutionSecurityContext,
    turn_id: str,
    review_paths: list[str],
) -> tuple[str, bool]:
    clean_paths = list(
        dict.fromkeys(
            (
                str(path).replace("\\", "/")[2:]
                if str(path).replace("\\", "/").startswith("./")
                else str(path).replace("\\", "/")
            )
            for path in review_paths
            if (
                str(path).strip()
                and not str(path).startswith("/")
                and is_reviewable_path(str(path))
            )
        )
    )
    if not clean_paths:
        return "", True
    complete = len(clean_paths) <= MAX_REVIEW_FILES
    clean_paths = clean_paths[:MAX_REVIEW_FILES]

    def execute(tool_name, args=None):
        return runtime.registry.execute_tool(
            tool_name,
            args or {},
            turn_id=turn_id,
            conversation_id=goal.conversation_id,
            workspace_id=runtime.workspace_id,
            origin="SCHEDULE",
            security_context=security,
        )

    status = execute("git_status")
    diff = execute("git_diff")
    status_text = (
        str(getattr(status, "output", "") or "")
        if getattr(status, "success", False)
        else ""
    )
    diff_text = (
        str(getattr(diff, "output", "") or "")
        if getattr(diff, "success", False)
        else ""
    )
    runner_limit = int(
        getattr(runtime.registry.process_runner, "max_output_bytes", 0) or 0
    )
    if bool(getattr(diff, "truncated", False)) or (
        runner_limit and len(diff_text.encode("utf-8")) >= runner_limit
    ):
        complete = False

    wanted = set(clean_paths)
    filtered_status = []
    for line in status_text.splitlines():
        raw = line[3:].strip() if len(line) >= 4 else ""
        candidates = {raw}
        if " -> " in raw:
            candidates.update(part.strip() for part in raw.split(" -> ", 1))
        if wanted & candidates:
            filtered_status.append(line)

    filtered_diff, diff_paths, diff_complete = _filter_diff_for_paths(
        diff_text,
        clean_paths,
    )
    complete = complete and diff_complete
    blocks = []
    if filtered_status:
        blocks.append(
            "[GIT STATUS — AGENT MUTATED PATHS]\n"
            + "\n".join(filtered_status)
        )
    if filtered_diff.strip():
        blocks.append("[GIT DIFF — AGENT MUTATED PATHS]\n" + filtered_diff)

    for path in clean_paths:
        if path in diff_paths:
            ranges = _diff_context_ranges(filtered_diff, path) or [(1, 240)]
            for start_line, end_line in ranges:
                result = execute(
                    "read_file",
                    {
                        "path": path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "max_bytes": 8_000,
                    },
                )
                if not getattr(result, "success", False):
                    continue
                body = str(getattr(result, "output", "") or "")
                if body.strip():
                    blocks.append(
                        f"[CHANGED CONTEXT {path}:{start_line}-{end_line}]\n{body}"
                    )
            continue

        result = execute(
            "read_file",
            {
                "path": path,
                "start_line": 1,
                "end_line": 3000,
                "max_bytes": MAX_REVIEW_FILE_CHARS,
            },
        )
        if not getattr(result, "success", False):
            complete = False
            blocks.append(f"[UNREADABLE MUTATED PATH {path}]")
            continue
        body = str(getattr(result, "output", "") or "")
        file_truncated = (
            bool(getattr(result, "truncated", False))
            or len(body) >= MAX_REVIEW_FILE_CHARS
        )
        if file_truncated:
            complete = False
        label = "NEW FILE EXCERPT" if file_truncated else "NEW FILE"
        blocks.append(f"[{label} {path}]\n{body}")

    snapshot = "\n\n".join(blocks).strip()
    if len(snapshot) > MAX_REVIEW_SNAPSHOT_CHARS:
        snapshot = (
            snapshot[:MAX_REVIEW_SNAPSHOT_CHARS]
            + "\n...[snapshot truncated]"
        )
        complete = False
    return snapshot, complete
