"""Deterministic completion checks for workspace mutation tasks.

The model is not a source of truth for filesystem effects. This adapter wraps the
existing tool loop and only allows terminal prose to pass when required workspace
mutations actually produced successful host-tool evidence. It also verifies files
that the model explicitly claims were created/implemented. A bounded recovery
round asks the model to execute the missing mutation; repeated non-execution fails
closed instead of returning setup instructions as if the task were complete.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import replace
from pathlib import Path
from types import MethodType
from typing import Any, Iterator

from kitt.context_filter.fallback import is_workspace_creation_request
from kitt.core.turn_events import ToolCompleted, ToolStarted, TurnFailed


_MUTATION_TOOLS = frozenset({
    "write_file", "apply_patch", "create_directory", "move", "rename", "delete",
    "repo.write_file", "patch.apply", "repo.create_directory", "repo.move",
    "repo.rename", "repo.delete", "repo.edit_symbol",
})
_RUNTIME_MUTATION_OPERATIONS = frozenset({
    "repo.write_file", "patch.apply", "repo.create_directory", "repo.move",
    "repo.rename", "repo.delete", "repo.edit_symbol",
})
_EXPLICIT_FIX_TERMS = (
    "fix", "corrija", "corrigir", "conserte", "consertar", "repare", "reparar",
    "atualize", "atualizar", "modifique", "modificar", "edite", "editar",
)

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
    r"\b(?:"
    r"will\s+(?:be\s+)?(?:creat(?:e|ed)|writ(?:e|ten)|sav(?:e|ed)|generat(?:e|ed)|implement(?:ed)?)|"
    r"would\s+be|should\s+be|vai\s+ser|será|sera"
    r")\b",
    re.IGNORECASE,
)
_FILE_PATH_RE = re.compile(
    r"(?<![\w:/.-])"
    r"(?P<path>(?:(?:[A-Za-z0-9_.@-]+)[\\/])*"
    r"[A-Za-z0-9_.@-]+\."
    r"(?:py|pyi|js|mjs|cjs|ts|tsx|jsx|java|kt|kts|go|rs|c|cc|cpp|h|hpp|"
    r"cs|php|rb|swift|scala|sql|sh|bash|zsh|ps1|html|htm|css|scss|sass|"
    r"json|jsonl|yaml|yml|toml|ini|cfg|conf|xml|md|txt|properties|gradle))"
    r"(?![\w/-])",
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
    """Return positively claimed workspace files that do not physically exist."""
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


def requires_workspace_mutation(processor: Any, cmd: Any) -> bool:
    """Return whether this turn must produce a real workspace mutation.

    Planning/ask modes are always read-only. Explicit project/file creation is
    deterministic and takes precedence over occasionally-wrong model intent.
    For compiled tasks, implementation/refactor/document intents require an
    ``edit`` action; debug only becomes mandatory when the user explicitly asks
    for a fix/update rather than merely asking for diagnosis.
    """
    mode = str(getattr(cmd, "mode", "auto") or "auto").lower()
    if mode in {"plan", "ask"}:
        return False

    prompt = str(getattr(cmd, "prompt", "") or "")
    if is_workspace_creation_request(prompt):
        return True

    session_state = getattr(processor, "session_state", None)
    task = getattr(session_state, "last_task", None)
    if task is None:
        return False

    intent = str(getattr(task, "intent", "") or "").upper()
    actions = {str(action).lower() for action in (getattr(task, "actions", None) or ())}
    if "edit" not in actions:
        return False
    if intent in {"IMPLEMENT", "REFACTOR", "DOCUMENT"}:
        return True
    if intent == "DEBUG":
        lowered = prompt.lower()
        return any(term in lowered for term in _EXPLICIT_FIX_TERMS)
    return False


def _is_mutating_process_call(args: dict[str, Any]) -> bool:
    operation_args = args.get("arguments", {}) if isinstance(args.get("arguments"), dict) else {}
    raw = operation_args.get("argv") or operation_args.get("command") or operation_args.get("cmd")
    if isinstance(raw, (list, tuple)):
        tokens = [str(token) for token in raw if str(token)]
    elif isinstance(raw, str):
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
    else:
        return False
    if not tokens:
        return False

    joined = " ".join(tokens).lower()
    head = tokens[0].lower()
    if head in {"mkdir", "touch", "cp", "mv", "rm", "install"}:
        return True
    return joined.startswith((
        "ng new ", "npm create ", "npm init ", "pnpm create ", "yarn create ",
        "npx create-", "mvn archetype:", "gradle init",
    ))


def _is_mutation_call(tool_name: str, args: Any) -> bool:
    if tool_name in _MUTATION_TOOLS:
        return True
    if tool_name != "kitt_runtime" or not isinstance(args, dict):
        return False
    operation = str(args.get("operation") or "")
    if operation in _RUNTIME_MUTATION_OPERATIONS:
        return True
    if operation == "process.run":
        return _is_mutating_process_call(args)
    return False


def _claimed_files_retry_message(missing: list[str]) -> str:
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


def _required_mutation_retry_message() -> str:
    return (
        "[KITT EXECUTION REQUIRED]\n"
        "The user requested implementation that changes the workspace, but no workspace "
        "mutation has succeeded in this turn. Do not answer with setup instructions, a plan, "
        "commands for the user to run, or a request for the user to provide component code. "
        "Use the available host tools now and perform the implementation yourself. You may "
        "inspect the workspace first when needed, but read/list/search results never satisfy "
        "this requirement. For files use kitt_runtime repo.write_file or patch.apply; for "
        "directories use repo.create_directory; process.run may be used for an appropriate "
        "project scaffold command. Continue executing until the requested implementation is "
        "materially applied, then summarize only what actually succeeded."
    )


def install_completion_guard(processor: Any, registry: Any, *, max_retries: int = 1) -> None:
    """Install a bounded fail-closed completion check on a processor."""
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
        successful_mutation = False
        failed_mutations: dict[str, str] = {}

        while True:
            terminal: tuple[str, list] | None = None
            pending_mutation_calls: set[str] = set()
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
                # execution requirement and filesystem claims are checked.
                if event is None and response is not None and messages is not None:
                    terminal = (response, messages)
                    continue

                if isinstance(event, ToolStarted) and _is_mutation_call(event.tool_name, event.args):
                    pending_mutation_calls.add(event.call_id)
                elif isinstance(event, ToolCompleted):
                    is_mutation = (
                        event.tool_name in _MUTATION_TOOLS
                        or event.call_id in pending_mutation_calls
                    )
                    if is_mutation:
                        if event.success:
                            successful_mutation = True
                            failed_mutations.pop(event.tool_name, None)
                        else:
                            failed_mutations[event.tool_name] = event.error or "resultado sem detalhes"
                    pending_mutation_calls.discard(event.call_id)
                yield event, response, messages

            if terminal is None:
                return

            response, messages = terminal
            missing = missing_claimed_workspace_files(registry.root_path, response)
            mutation_missing = requires_workspace_mutation(self, cmd) and not successful_mutation
            if not missing and not failed_mutations and not mutation_missing:
                yield None, response, messages
                return

            if retries >= retries_allowed:
                reasons: list[str] = []
                if mutation_missing:
                    reasons.append("the task required a workspace mutation but no mutation tool succeeded")
                if missing:
                    reasons.append("claimed files are still missing: " + ", ".join(missing))
                if failed_mutations:
                    reasons.append(
                        "mutation tool failures remain: "
                        + "; ".join(f"{name}: {error}" for name, error in failed_mutations.items())
                    )
                yield TurnFailed(error="Completion verification failed: " + "; ".join(reasons)), None, None
                return

            retries += 1
            retry_messages = list(messages)
            recovery_parts: list[str] = []
            if mutation_missing:
                recovery_parts.append(_required_mutation_retry_message())
            if missing:
                recovery_parts.append(_claimed_files_retry_message(missing))
            if failed_mutations:
                recovery_parts.append(
                    "[KITT MUTATION FAILURE]\nRetry or replace the failed mutation before completing: "
                    + "; ".join(f"{name}: {error}" for name, error in failed_mutations.items())
                )
            retry_messages.extend([
                {"role": "assistant", "content": response},
                {"role": "user", "content": "\n\n".join(recovery_parts)},
            ])
            current_request = replace(request, messages=retry_messages)

    processor._execute_tool_loop = MethodType(guarded_tool_loop, processor)
    processor._completion_guard_installed = True


__all__ = [
    "install_completion_guard",
    "missing_claimed_workspace_files",
    "requires_workspace_mutation",
]
