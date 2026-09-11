"""Deterministic completion checks for model-claimed workspace file mutations.

The model is not a source of truth for filesystem effects. This adapter wraps the
existing tool loop and only allows a terminal prose response to pass when files
that the response claims were created/implemented actually exist in the workspace.
A single bounded recovery round asks the model to perform the missing write via
KITT's host tool surface; repeated false completion fails closed.
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from types import MethodType
from typing import Any, Iterator

from kitt.core.turn_events import TurnFailed


_MUTATION_CLAIM_RE = re.compile(
    r"\b(?:"
    r"created|implemented|wrote|written|saved|generated|updated|modified|"
    r"criad[oa]s?|criei|implementad[oa]s?|implementei|escrevi|grav(?:ei|ad[oa]s?)|"
    r"salv(?:ei|ad[oa]s?)|gerad[oa]s?|gerei|atualizad[oa]s?|atualizei|modificad[oa]s?"
    r")\b",
    re.IGNORECASE,
)
_NEGATED_CLAIM_RE = re.compile(
    r"\b(?:not|never|failed|unable|cannot|can['’]?t|couldn['’]?t|didn['’]?t|"
    r"não|nao|nunca|falhou|impossível|impossivel)\b",
    re.IGNORECASE,
)
_FUTURE_CLAIM_RE = re.compile(
    r"\b(?:will\s+(?:be\s+)?(?:create|write|save|generate|implement)|"
    r"would\s+be|should\s+be|vai\s+ser|será|sera)\b",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(
    r"(?<![\w:/.-])"
    r"(?P<path>(?:(?:[A-Za-z0-9_.@-]+)[\\/])*"
    r"[A-Za-z0-9_.@-]+\."
    r"(?:py|pyi|js|mjs|cjs|ts|tsx|jsx|java|kt|kts|go|rs|c|cc|cpp|h|hpp|"
    r"cs|php|rb|swift|scala|sql|sh|bash|zsh|ps1|html|htm|css|scss|sass|"
    r"json|jsonl|yaml|yml|toml|ini|cfg|conf|xml|md|txt|properties|gradle))"
    r"(?![\w/.-])",
    re.IGNORECASE,
)


def _safe_workspace_file(root: Path, raw_path: str) -> tuple[str, Path] | None:
    relative = raw_path.strip().strip("`'\".,;:()[]{}<>").replace("\\", "/")
    if not relative:
        return None
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    try:
        resolved = (root / candidate).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return candidate.as_posix(), resolved


def missing_claimed_workspace_files(root_dir: str | Path, response: str) -> list[str]:
    """Return positively claimed workspace files that do not physically exist.

    Only lines that assert a completed mutation are considered. Negative/future
    statements are ignored so explanatory text such as "will be created" does
    not accidentally become a completion obligation.
    """
    if not response:
        return []
    root = Path(root_dir).resolve()
    missing: list[str] = []
    seen: set[str] = set()

    for line in response.splitlines():
        if not _MUTATION_CLAIM_RE.search(line):
            continue
        if _NEGATED_CLAIM_RE.search(line) or _FUTURE_CLAIM_RE.search(line):
            continue
        for match in _FILE_PATH_RE.finditer(line):
            safe = _safe_workspace_file(root, match.group("path"))
            if safe is None:
                continue
            relative, resolved = safe
            if relative in seen:
                continue
            seen.add(relative)
            if not resolved.is_file():
                missing.append(relative)
    return missing


def _retry_message(missing: list[str]) -> str:
    rendered = ", ".join(missing)
    return (
        "[KITT COMPLETION VERIFICATION]\n"
        "Your previous response claimed that workspace file(s) were created, implemented, "
        f"written, or updated, but they do not exist on the host filesystem: {rendered}.\n"
        "Do not report success yet. For each missing new/full file, call kitt_runtime with "
        "operation=repo.write_file and arguments={path,content}. patch.apply is only for "
        "SEARCH/REPLACE edits to existing files and never accepts unified diff. "
        "Wait for a successful host tool result before summarizing completion."
    )


def install_completion_guard(processor: Any, registry: Any, *, max_retries: int = 1) -> None:
    """Install one bounded physical-filesystem completion check on a processor."""
    if getattr(processor, "_completion_guard_installed", False):
        return

    original_loop = processor._execute_tool_loop
    retries_allowed = max(0, int(max_retries))

    def guarded_tool_loop(
        self,
        cmd,
        request,
        exe_profile,
        exe_client,
        workspace_id,
        security_context,
    ) -> Iterator:
        current_request = request
        retries = 0

        while True:
            terminal: tuple[str, list] | None = None
            for event, response, messages in original_loop(
                cmd,
                current_request,
                exe_profile,
                exe_client,
                workspace_id,
                security_context,
            ):
                # _execute_tool_loop uses (None, response, messages) as its final
                # hand-off to _finalize_turn. Hold only that sentinel until the
                # filesystem claim is checked; stream every real event unchanged.
                if event is None and response is not None and messages is not None:
                    terminal = (response, messages)
                    continue
                yield event, response, messages

            if terminal is None:
                return

            response, messages = terminal
            missing = missing_claimed_workspace_files(registry.root_path, response)
            if not missing:
                yield None, response, messages
                return

            if retries >= retries_allowed:
                yield TurnFailed(
                    error=(
                        "Completion verification failed: the model claimed workspace file(s) "
                        f"that are still missing after recovery: {', '.join(missing)}"
                    )
                ), None, None
                return

            retries += 1
            retry_messages = list(messages)
            retry_messages.extend(
                [
                    {"role": "assistant", "content": response},
                    {"role": "user", "content": _retry_message(missing)},
                ]
            )
            current_request = replace(request, messages=retry_messages)

    processor._execute_tool_loop = MethodType(guarded_tool_loop, processor)
    processor._completion_guard_installed = True


__all__ = ["install_completion_guard", "missing_claimed_workspace_files"]
