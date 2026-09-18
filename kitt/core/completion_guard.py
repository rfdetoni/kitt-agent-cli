"""Deterministic completion checks for workspace mutation tasks.

The model is not a source of truth for filesystem effects. A successful mutation
is evidence of progress, not evidence that a multi-file implementation is done.
This adapter therefore tracks meaningful host progress, verifies conservative
completion contracts against the real workspace, rejects deferred hand-offs, and
fails closed when the model loops on exploration without making progress.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass, replace
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
_RUNTIME_EXPLORATION_OPERATIONS = frozenset({
    "repo.read", "repo.search", "repo.inspect_symbol", "repo.read_symbol",
    "repo.references", "repo.context_map", "repo.definition", "repo.hover",
    "repo.references_semantic", "repo.diagnostics", "repo.call_hierarchy",
    "repo.outline", "repo.ast_search", "repo.list", "goal.inspect", "memory.query",
    "session.search", "state.get", "state.list", "handles.resolve", "artifacts.read",
})
_EXPLORATION_TOOLS = frozenset({
    "read_file", "search", "repository_map", "list_files", "git_status", "git_diff",
})
_EXPLICIT_FIX_TERMS = (
    "fix", "corrija", "corrigir", "conserte", "consertar", "repare", "reparar",
    "atualize", "atualizar", "modifique", "modificar", "edite", "editar",
)
_READ_ONLY_INTENTS = frozenset({"ASK", "PLAN", "REVIEW", "TEST"})
_MAX_IDENTICAL_EXPLORATIONS_WITHOUT_PROGRESS = 3
_MAX_EXPLORATIONS_WITHOUT_PROGRESS = 12
_MAX_PROGRESS_RECOVERIES = 6

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
_DEFERRED_IMPLEMENTATION_RE = re.compile(
    r"(?:"
    r"\b(?:ask|send|give|provide)\s+me\b.{0,80}\b(?:code|files?|components?|details?|requirements?)\b|"
    r"\b(?:then|after(?:wards)?|once)\b.{0,60}\b(?:i\s+(?:can|will)|we\s+(?:can|will))\b.{0,80}"
    r"\b(?:implement|generate|create|write|build)\b|"
    r"\b(?:me\s+(?:solicite|peça|peca|envie|mande|forneça|forneca))\b.{0,80}"
    r"\b(?:c[oó]dig(?:o|os)|arquiv(?:o|os)|component(?:e|es)|detalh(?:e|es)|requisit(?:o|os))\b|"
    r"\b(?:ent[aã]o|depois|ap[oó]s)\b.{0,60}\b(?:eu\s+)?(?:posso|vou|poderei)\b.{0,80}"
    r"\b(?:implementar|gerar|criar|escrever|montar)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_VALIDATION_COMMAND_RE = re.compile(
    r"(?:^|\s)(?:"
    r"(?:\./)?mvnw?\s+(?:test|verify|package)|"
    r"(?:\./)?gradlew?\s+(?:test|check|build)|"
    r"npm\s+(?:test|run\s+(?:test|build|check|lint))|"
    r"pnpm\s+(?:test|run\s+(?:test|build|check|lint)|build)|"
    r"yarn\s+(?:test|build|lint)|"
    r"(?:npx\s+)?ng\s+(?:test|build)|"
    r"pytest(?:\s|$)|python(?:3)?\s+-m\s+pytest|"
    r"go\s+test(?:\s|$)|cargo\s+(?:test|check|build)|dotnet\s+(?:test|build)"
    r")",
    re.IGNORECASE,
)
_VALIDATION_CD_SCOPE_RE = re.compile(
    r"(?:^|(?:&&|;|\|\|)\s*)cd\s+(?:\./)?(?P<scope>[A-Za-z0-9_.@-]+)(?:/[^\s;&|]*)?\s*(?:&&|;)",
    re.IGNORECASE,
)
_IGNORED_PROJECT_DIRS = frozenset({
    ".git", ".idea", ".vscode", "node_modules", "dist", "build", "target",
    ".gradle", ".mvn", ".venv", "venv", "__pycache__", "coverage",
})
_BACKEND_MARKERS = (
    "pom.xml", "build.gradle", "build.gradle.kts", "package.json",
    "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml",
)
_BACKEND_SOURCE_SUFFIXES = (
    ".java", ".kt", ".kts", ".py", ".js", ".mjs", ".cjs", ".ts",
    ".go", ".rs", ".cs", ".php", ".rb",
)
_FRONTEND_MARKERS = (
    "package.json", "angular.json", "vite.config.ts", "vite.config.js",
    "next.config.js", "next.config.mjs", "next.config.ts",
)
_FRONTEND_SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".scss")


@dataclass(frozen=True)
class _ScopeRequirement:
    name: str
    root: str
    required_markers: tuple[str, ...] = ()
    any_markers: tuple[str, ...] = ()
    source_suffixes: tuple[str, ...] = ()
    source_under: str | None = None


@dataclass(frozen=True)
class CompletionContract:
    """Small host-verifiable contract derived only from explicit project scope."""

    scopes: tuple[_ScopeRequirement, ...] = ()
    require_validation: bool = False
    validation_scopes: tuple[str, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.scopes or self.require_validation or self.validation_scopes)

    def evaluate(
        self,
        root_dir: str | Path,
        *,
        validation_succeeded: bool = False,
        validated_scopes: frozenset[str] = frozenset(),
    ) -> list[str]:
        root = Path(root_dir).resolve()
        issues: list[str] = []
        for requirement in self.scopes:
            scope_root = root / requirement.root
            if not scope_root.is_dir():
                issues.append(f"missing required {requirement.name} directory: {requirement.root}/")
                continue

            for marker in requirement.required_markers:
                if not (scope_root / marker).is_file():
                    issues.append(
                        f"{requirement.name} is missing required project marker: "
                        f"{requirement.root}/{marker}"
                    )

            if requirement.any_markers and not any(
                (scope_root / marker).is_file() for marker in requirement.any_markers
            ):
                issues.append(
                    f"{requirement.name} has no recognized project manifest/build file under "
                    f"{requirement.root}/"
                )

            if requirement.source_suffixes:
                source_root = scope_root / requirement.source_under if requirement.source_under else scope_root
                if not source_root.is_dir() or not _contains_source_file(source_root, requirement.source_suffixes):
                    location = (
                        f"{requirement.root}/{requirement.source_under}"
                        if requirement.source_under else requirement.root
                    )
                    issues.append(
                        f"{requirement.name} has no implementation source file under {location}/"
                    )

        if self.require_validation and not validation_succeeded:
            issues.append(
                "project implementation has not been validated by a successful build/test/check command"
            )
        elif self.validation_scopes and not validation_succeeded:
            issues.append(
                "project implementation has not been validated by a successful build/test/check command"
            )
        elif self.validation_scopes and "workspace" not in validated_scopes:
            for scope in self.validation_scopes:
                if scope not in validated_scopes:
                    issues.append(f"{scope} has not been validated by a successful build/test/check command")
        return issues


class _ExecutionProgressLedger:
    """Track meaningful progress instead of treating every tool call as completion."""

    def __init__(self) -> None:
        self.successful_mutations: set[str] = set()
        self.successful_validations: set[str] = set()
        self.successful_validation_scopes: set[str] = set()
        self.pending_mutations: dict[str, str] = {}
        self.pending_validations: dict[str, tuple[str, str]] = {}
        self.exploration_counts: dict[str, int] = {}
        self.explorations_since_progress = 0

    @property
    def revision(self) -> int:
        return len(self.successful_mutations) + len(self.successful_validations)

    @property
    def validation_succeeded(self) -> bool:
        return bool(self.successful_validations)

    @property
    def validated_scopes(self) -> frozenset[str]:
        return frozenset(self.successful_validation_scopes)

    def start(self, event: ToolStarted) -> str | None:
        signature = _tool_signature(event.tool_name, event.args)
        if _is_mutation_call(event.tool_name, event.args):
            self.pending_mutations[event.call_id] = signature
        if _is_validation_call(event.tool_name, event.args):
            self.pending_validations[event.call_id] = (signature, _validation_scope(event.args))
        if _is_exploration_call(event.tool_name, event.args):
            self.explorations_since_progress += 1
            count = self.exploration_counts.get(signature, 0) + 1
            self.exploration_counts[signature] = count
            if count >= _MAX_IDENTICAL_EXPLORATIONS_WITHOUT_PROGRESS:
                return (
                    "identical exploration repeated without progress: "
                    f"{event.tool_name} ({count} times)"
                )
            if self.explorations_since_progress > _MAX_EXPLORATIONS_WITHOUT_PROGRESS:
                return (
                    "too many exploration calls without a successful mutation "
                    f"({_MAX_EXPLORATIONS_WITHOUT_PROGRESS} allowed)"
                )
        return None

    def complete(self, event: ToolCompleted) -> tuple[bool, bool]:
        mutation = self.pending_mutations.pop(event.call_id, None)
        validation_entry = self.pending_validations.pop(event.call_id, None)
        validation = validation_entry[0] if validation_entry else None
        validation_scope = validation_entry[1] if validation_entry else None
        if mutation is None and event.tool_name in _MUTATION_TOOLS:
            mutation = f"completed:{event.tool_name}:{event.call_id or 'legacy'}"
        new_mutation = bool(event.success and mutation and mutation not in self.successful_mutations)
        new_validation = bool(event.success and validation and validation not in self.successful_validations)
        if new_mutation and mutation:
            self.successful_mutations.add(mutation)
        if new_validation and validation:
            self.successful_validations.add(validation)
            if validation_scope:
                self.successful_validation_scopes.add(validation_scope)
        if new_mutation:
            self.exploration_counts.clear()
            self.explorations_since_progress = 0
        return new_mutation, new_validation


def _contains_source_file(root: Path, suffixes: tuple[str, ...]) -> bool:
    suffix_set = {suffix.lower() for suffix in suffixes}
    try:
        for path in root.rglob("*"):
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if any(part in _IGNORED_PROJECT_DIRS for part in relative.parts[:-1]):
                continue
            if path.is_file() and path.suffix.lower() in suffix_set:
                return True
    except OSError:
        return False
    return False


def _effective_prompt(prompt: str, task: Any = None) -> str:
    original = str(getattr(task, "original_prompt", "") or "").strip()
    current = str(prompt or "").strip()
    if original and current and original != current:
        return f"{original}\n{current}"
    return original or current


def build_completion_contract(prompt: str, task: Any = None) -> CompletionContract:
    """Derive a conservative contract for explicit backend/frontend project creation."""
    effective_prompt = _effective_prompt(prompt, task)
    text = effective_prompt.lower()
    explicit_creation = is_workspace_creation_request(effective_prompt)
    creation = explicit_creation or any(word in text for word in (
        "crie", "criar", "create", "build", "implemente", "implementar",
        "implementação", "implementacao", "implementation", "gere", "gerar", "construa",
    ))
    if not creation:
        return CompletionContract()

    intent = str(getattr(task, "intent", "") or "").upper()
    if intent and intent not in {"IMPLEMENT", "REFACTOR", "DOCUMENT", "DEBUG"} and not explicit_creation:
        return CompletionContract()

    scopes: list[_ScopeRequirement] = []
    has_backend = "backend" in text or "back end" in text
    has_frontend = "frontend" in text or "front end" in text
    angular = "angular" in text

    if has_backend:
        scopes.append(_ScopeRequirement(
            name="backend",
            root="backend",
            any_markers=_BACKEND_MARKERS,
            source_suffixes=_BACKEND_SOURCE_SUFFIXES,
        ))
    if has_frontend:
        scopes.append(_ScopeRequirement(
            name="frontend",
            root="frontend",
            required_markers=("package.json", "angular.json") if angular else (),
            any_markers=() if angular else _FRONTEND_MARKERS,
            source_suffixes=(".ts",) if angular else _FRONTEND_SOURCE_SUFFIXES,
            source_under="src",
        ))

    full_project = any(word in text for word in (
        "projeto", "project", "site", "aplicação", "aplicacao", "application",
    ))
    require_validation = bool(scopes) and full_project and (len(scopes) > 1 or angular)
    validation_scopes = tuple(scope.root for scope in scopes) if require_validation else ()
    return CompletionContract(
        tuple(scopes),
        require_validation=require_validation,
        validation_scopes=validation_scopes,
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
    """Return whether this turn must produce a real workspace mutation."""
    mode = str(getattr(cmd, "mode", "auto") or "auto").lower()
    if mode in {"plan", "ask"}:
        return False

    prompt = str(getattr(cmd, "prompt", "") or "")
    session_state = getattr(processor, "session_state", None)
    task = getattr(session_state, "last_task", None)
    effective_prompt = _effective_prompt(prompt, task)

    # Explicit creation/implementation text is stronger evidence than an LLM
    # semantic label. A misclassified ASK/REVIEW task must never disable host
    # completion verification for an unequivocal "create the project" request.
    if is_workspace_creation_request(effective_prompt):
        return True

    if task is not None:
        intent = str(getattr(task, "intent", "") or "").upper()
        actions = {str(action).lower() for action in (getattr(task, "actions", None) or ())}
        if intent in _READ_ONLY_INTENTS:
            return False
        if "edit" in actions:
            if intent in {"IMPLEMENT", "REFACTOR", "DOCUMENT"}:
                return True
            if intent == "DEBUG":
                lowered = effective_prompt.lower()
                return any(term in lowered for term in _EXPLICIT_FIX_TERMS)

    lowered = effective_prompt.lower().strip()
    if lowered.endswith("?") or lowered.startswith(("como ", "how ", "explique ", "explain ")):
        return False
    return is_workspace_creation_request(effective_prompt)


def is_deferred_implementation_response(response: str) -> bool:
    """Return True when an implementation answer hands execution back to the user."""
    if not response:
        return False
    return bool(_DEFERRED_IMPLEMENTATION_RE.search(response))


def _command_tokens(args: dict[str, Any]) -> tuple[list[str], str]:
    operation_args = args.get("arguments", {}) if isinstance(args.get("arguments"), dict) else args
    raw = operation_args.get("argv") or operation_args.get("command") or operation_args.get("cmd")
    if isinstance(raw, (list, tuple)):
        tokens = [str(token) for token in raw if str(token)]
        return tokens, " ".join(tokens)
    if isinstance(raw, str):
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = raw.split()
        return tokens, raw
    return [], ""


def _validation_scope(args: Any) -> str:
    if not isinstance(args, dict):
        return "workspace"
    operation_args = args.get("arguments", {}) if isinstance(args.get("arguments"), dict) else args
    for key in ("cwd", "workdir", "working_directory", "directory"):
        value = operation_args.get(key)
        if isinstance(value, str) and value.strip():
            normalized = value.strip().replace("\\", "/").strip("./")
            if normalized:
                return normalized.split("/", 1)[0].lower()
    _, raw = _command_tokens(args)
    match = _VALIDATION_CD_SCOPE_RE.search(raw)
    return match.group("scope").lower() if match else "workspace"


def _is_mutating_process_call(args: dict[str, Any]) -> bool:
    tokens, raw = _command_tokens(args)
    if not tokens:
        return False
    joined = raw.lower().strip()
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


def _is_exploration_call(tool_name: str, args: Any) -> bool:
    if tool_name in _EXPLORATION_TOOLS:
        return True
    if tool_name != "kitt_runtime" or not isinstance(args, dict):
        return False
    return str(args.get("operation") or "") in _RUNTIME_EXPLORATION_OPERATIONS


def _is_validation_call(tool_name: str, args: Any) -> bool:
    if not isinstance(args, dict):
        return False
    if tool_name == "kitt_runtime" and str(args.get("operation") or "") != "process.run":
        return False
    if tool_name not in {"kitt_runtime", "run_command", "process.run"}:
        return False
    _, raw = _command_tokens(args)
    return bool(raw and _VALIDATION_COMMAND_RE.search(raw.strip()))


def _tool_signature(tool_name: str, args: Any) -> str:
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(args)
    return hashlib.sha256(f"{tool_name}\0{payload}".encode("utf-8", "replace")).hexdigest()


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
        "The user requested implementation that changes the workspace, but the implementation "
        "is not complete yet. Do not answer with setup instructions, a plan, commands for the "
        "user to run, or a request for the user to provide component code. Use the available "
        "host tools now and perform the implementation yourself. You may inspect the workspace "
        "first when needed, but read/list/search results never satisfy this requirement. For "
        "files use kitt_runtime repo.write_file or patch.apply; for directories use "
        "repo.create_directory; process.run may be used for an appropriate project scaffold "
        "command. Continue executing until the requested implementation is materially applied, "
        "then summarize only what actually succeeded."
    )


def _contract_retry_message(issues: list[str]) -> str:
    rendered = "\n".join(f"- {issue}" for issue in issues)
    return (
        "[KITT COMPLETION CONTRACT]\n"
        "Host verification shows that the requested project is still incomplete:\n"
        f"{rendered}\n"
        "Continue the implementation with host tools. Do not claim completion until these "
        "host-verifiable requirements are satisfied. If validation is missing, run the "
        "appropriate build/test/check command with process.run for every requested project scope "
        "after creating the required files."
    )


def _failed_mutation_retry_message(failed_mutations: dict[str, str]) -> str:
    failures = "; ".join(f"{name}: {error}" for name, error in failed_mutations.items())
    return (
        "[KITT COMPLETION VERIFICATION]\n"
        "A workspace mutation failed and the requested change is not complete: "
        f"{failures}. Retry or replace the failed mutation before completing. "
        "For a complete/new file prefer kitt_runtime operation=repo.write_file; use "
        "patch.apply only for SEARCH/REPLACE edits to existing files."
    )


def install_completion_guard(processor: Any, registry: Any, *, max_retries: int = 1) -> None:
    """Install a bounded, progress-aware fail-closed completion check on a processor."""
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
        agent_route=None,
        **loop_kwargs,
    ) -> Iterator:
        current_request = request
        recoveries = 0
        last_recovery_revision = 0
        ledger = _ExecutionProgressLedger()
        failed_mutations: dict[str, str] = {}
        mutation_required = requires_workspace_mutation(self, cmd)
        task = getattr(getattr(self, "session_state", None), "last_task", None)
        contract = build_completion_contract(str(getattr(cmd, "prompt", "") or ""), task)

        while True:
            terminal: tuple[str, list] | None = None
            for event, response, messages in original_loop(
                cmd,
                current_request,
                exe_profile,
                exe_client,
                workspace_id,
                security_context,
                agent_route=agent_route,
                **loop_kwargs,
            ):
                if event is None and response is not None and messages is not None:
                    terminal = (response, messages)
                    continue

                if isinstance(event, ToolStarted):
                    stall = ledger.start(event)
                    if stall and mutation_required:
                        yield TurnFailed(
                            error=(
                                "Execution stalled: " + stall + ". The implementation requires "
                                "forward progress; repeated read/list/search calls cannot complete it."
                            )
                        ), None, None
                        return
                elif isinstance(event, ToolCompleted):
                    was_mutation = (
                        event.call_id in ledger.pending_mutations
                        or event.tool_name in _MUTATION_TOOLS
                    )
                    ledger.complete(event)
                    if was_mutation:
                        if event.success:
                            failed_mutations.pop(event.tool_name, None)
                        else:
                            failed_mutations[event.tool_name] = event.error or "resultado sem detalhes"
                yield event, response, messages

            if terminal is None:
                return

            response, messages = terminal
            missing = missing_claimed_workspace_files(registry.root_path, response)
            mutation_missing = mutation_required and not ledger.successful_mutations
            deferred_implementation = mutation_required and is_deferred_implementation_response(response)
            contract_issues = (
                contract.evaluate(
                    registry.root_path,
                    validation_succeeded=ledger.validation_succeeded,
                    validated_scopes=ledger.validated_scopes,
                )
                if mutation_required and contract.enabled
                else []
            )

            if (
                not missing
                and not failed_mutations
                and not mutation_missing
                and not deferred_implementation
                and not contract_issues
            ):
                yield None, response, messages
                return

            progress_since_recovery = ledger.revision > last_recovery_revision
            normal_budget_exhausted = recoveries >= retries_allowed
            progress_budget_exhausted = recoveries >= _MAX_PROGRESS_RECOVERIES
            if progress_budget_exhausted or (normal_budget_exhausted and not progress_since_recovery):
                reasons: list[str] = []
                if mutation_missing:
                    reasons.append("the task required a workspace mutation but no mutation tool succeeded")
                if deferred_implementation:
                    reasons.append("the response deferred required implementation work back to the user")
                if contract_issues:
                    reasons.append("completion contract remains unsatisfied: " + "; ".join(contract_issues))
                if missing:
                    reasons.append("claimed files are still missing after recovery: " + ", ".join(missing))
                if failed_mutations:
                    reasons.append(
                        "mutation tool failures remain: "
                        + "; ".join(f"{name}: {error}" for name, error in failed_mutations.items())
                    )
                yield TurnFailed(error="Completion verification failed: " + "; ".join(reasons)), None, None
                return

            recoveries += 1
            last_recovery_revision = ledger.revision
            retry_messages = list(messages)
            recovery_parts: list[str] = []
            if mutation_missing or deferred_implementation:
                recovery_parts.append(_required_mutation_retry_message())
            if contract_issues:
                recovery_parts.append(_contract_retry_message(contract_issues))
            if missing:
                recovery_parts.append(_claimed_files_retry_message(missing))
            if failed_mutations:
                recovery_parts.append(_failed_mutation_retry_message(failed_mutations))
            retry_messages.extend([
                {"role": "assistant", "content": response},
                {"role": "user", "content": "\n\n".join(recovery_parts)},
            ])
            current_request = replace(request, messages=retry_messages)

    processor._execute_tool_loop = MethodType(guarded_tool_loop, processor)
    processor._completion_guard_installed = True


__all__ = [
    "CompletionContract",
    "build_completion_contract",
    "install_completion_guard",
    "is_deferred_implementation_response",
    "missing_claimed_workspace_files",
    "requires_workspace_mutation",
]
