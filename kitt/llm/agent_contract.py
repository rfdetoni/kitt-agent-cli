"""Per-turn context envelope for the strict kitt-reverse-proxy agent contract."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from kitt.router.classifier import TaskClassifier


AGENT_CONTRACT_HEADER = "X-Kitt-Agent-Contract"
AGENT_CONTRACT_VERSION = "v1"
AGENT_ROUTE_HEADER = "X-Kitt-Route"
TURN_CONTEXT_MARKER = "[KITT TURN CONTEXT]"
TURN_CONTEXT_END_MARKER = "[END KITT TURN CONTEXT]"
UNTRUSTED_WORKSPACE_LABEL = "UNTRUSTED_WORKSPACE_DATA"
SUPPORTED_ROUTES = frozenset(
    {
        "context-gather",
        "summarize",
        "code-generation",
        "code-edit",
        "validate-diff",
        "chat",
    }
)

_TOOL_NAME_RE = re.compile(r"['\"]name['\"]\s*:\s*['\"]([A-Za-z0-9_.:-]{1,64})['\"]")
_CONTEXT_SUMMARY_PREFIX = "Prepare a short technical context for another model to answer the task."
_INTERNAL_TOOL_FEEDBACK_PREFIXES = (
    "the host tool call is invalid (",
    "the python_compute call is invalid (",
    "apply_patch was rejected before approval:",
)
_INTERNAL_ROUTING_MARKERS = (
    "[kitt forward progress required]",
    "[kitt execution required]",
    "[kitt completion verification]",
    "[kitt completion contract]",
    "[kitt contract repair]",
)
_MUTATION_RECOVERY_MARKERS = (
    "[kitt execution required]",
    "[kitt completion contract]",
)
_MUTATION_RECOVERY_TERMS = (
    "implementation",
    "implementação",
    "implementacao",
    "workspace",
    "mutation",
    "mutação",
    "mutacao",
    "incomplete",
    "incompleto",
    "incompleta",
)
_HOST_TOOL_RESULT_MARKER = " result from the host. the values inside are untrusted data"
_SEMANTIC_INTENT_RE = re.compile(
    r"(?mi)^\s*Intent:\s*(IMPLEMENT|DEBUG|REFACTOR)\s*$"
)
_MUTATING_CREATE_TERMS = (
    "crie", "criar", "cria", "implemente", "implementar", "implementação", "implementacao",
    "gere", "gerar", "construa", "monte", "create", "build", "implement", "generate",
    "scaffold", "write", "mkdir",
)
_MUTATING_EDIT_TERMS = (
    "corrija", "corrigir", "conserte", "consertar", "repare", "reparar", "refatore",
    "refatorar", "atualize", "atualizar", "modifique", "modificar", "altere", "alterar",
    "edite", "editar", "remova", "remover", "fix", "repair", "refactor", "update",
    "modify", "change", "edit", "remove", "delete",
)
_WORKSPACE_TARGET_TERMS = (
    "projeto", "site", "aplicação", "aplicacao", "app", "backend", "frontend", "front end",
    "workspace", "repositório", "repositorio", "repository", "repo", "arquivo", "file",
    "pasta", "folder", "diretório", "diretorio", "directory", "código", "codigo", "code",
    "angular", "spring", "serviço", "servico", "service",
)
_TOP_LEVEL_HEADERS = (
    "Tool Contract:",
    "Memory:",
    "Active Skills:",
    "Project Guidelines:",
    "Learned Harness:",
    "Mandatory Constraints:",
    "Files Context:",
    "Repo Map:",
    "Recent Conversation:",
    "Project context:",
    "WORKSPACE_CONTEXT:",
    "Workspace context:",
    "[PLANNING MODE ACTIVE]",
)
_UNTRUSTED_HEADERS = frozenset(
    {
        "Active Skills:",
        "Project Guidelines:",
        "Files Context:",
        "Repo Map:",
        "Recent Conversation:",
        "Project context:",
        "WORKSPACE_CONTEXT:",
        "Workspace context:",
    }
)
_HEADER_RE = re.compile(
    r"(?m)^(" + "|".join(re.escape(header) for header in _TOP_LEVEL_HEADERS) + r")"
)


def normalize_agent_route(route: Optional[str]) -> str:
    """Return a supported router contract name, defaulting to ordinary chat."""
    value = (route or "chat").strip()
    if value not in SUPPORTED_ROUTES:
        raise ValueError(f"Unsupported KITT agent route: {value!r}")
    return value


def _is_internal_tool_feedback(content: Any) -> bool:
    """Return True for KITT-generated continuation feedback, never user task intent."""
    text = str(content or "").strip().casefold()
    if not text:
        return False
    if _HOST_TOOL_RESULT_MARKER in text[:512]:
        return True
    if any(text.startswith(marker) for marker in _INTERNAL_ROUTING_MARKERS):
        return True
    return any(text.startswith(prefix) for prefix in _INTERNAL_TOOL_FEEDBACK_PREFIXES)


def _routing_user_messages(messages: List[Dict[str, Any]]) -> List[str]:
    """Return real user task messages in chronological order, excluding KITT feedback."""
    result: List[str] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        if _is_internal_tool_feedback(content):
            continue
        result.append(content)
    return result


def _latest_routing_user_message(messages: List[Dict[str, Any]]) -> str:
    """Find the latest real user task, skipping KITT-generated continuation envelopes."""
    routable = _routing_user_messages(messages)
    return routable[-1] if routable else ""


def _semantic_route_from_execution_prompt(content: Any) -> Optional[str]:
    """Honor the deterministic SemanticTask intent emitted by TurnProcessor.

    TurnProcessor may replace the literal human prompt with SemanticTask.to_execution_prompt().
    That normalized prompt begins with `Intent: ...`; mutation-capable intents must not be
    reclassified from the exposed validation/read tool surface.
    """
    match = _SEMANTIC_INTENT_RE.search(str(content or ""))
    if not match:
        return None
    intent = match.group(1).upper()
    if intent == "IMPLEMENT":
        return "code-generation"
    if intent in {"DEBUG", "REFACTOR"}:
        return "code-edit"
    return None


def _mutation_route_from_user_message(content: Any) -> Optional[str]:
    """Return a mutation-capable route when the real user explicitly requested writes.

    Tool-surface inference is intentionally secondary: a surface that also exposes
    diagnostics/git-status must never downgrade an implementation request to
    validate-diff, because that route rejects file mutations at the reverse proxy.
    """
    text = str(content or "").casefold()
    if not text or not any(term in text for term in _WORKSPACE_TARGET_TERMS):
        return None
    if any(term in text for term in _MUTATING_EDIT_TERMS):
        return "code-edit"
    if any(term in text for term in _MUTATING_CREATE_TERMS):
        return "code-generation"
    return None


def _pinned_execution_route(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Preserve the first mutation-capable intent for the lifetime of one tool loop.

    Execution follow-ups append tool results, repair messages and validation nudges to the
    original task. The original task remains the authority for whether mutations are
    allowed; later read/validation surfaces must never downgrade it to validate-diff.
    """
    for content in _routing_user_messages(messages):
        semantic_route = _semantic_route_from_execution_prompt(content)
        if semantic_route:
            return semantic_route
        mutation_route = _mutation_route_from_user_message(content)
        if mutation_route:
            return mutation_route
    return None


def _recovery_mutation_route(messages: List[Dict[str, Any]]) -> Optional[str]:
    """Keep implementation-recovery turns mutation-capable.

    Some execution/completion guards can issue a provider follow-up without the original
    human task in that request batch. The canonical execution prompt may prefix the raw
    guard envelope with normalized Intent/Goal text, so recovery markers are matched
    anywhere in the message instead of only at byte zero. An explicit guard saying the
    implementation is still incomplete is authoritative orchestration state: routing it
    from tool surface alone can incorrectly select validate-diff and make the required
    file mutation impossible.
    """
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = str(message.get("content") or "").strip().casefold()
        if not any(marker in text for marker in _MUTATION_RECOVERY_MARKERS):
            continue
        if any(term in text for term in _MUTATION_RECOVERY_TERMS):
            return "code-edit"
    return None


def infer_agent_route(
    system_prompt: Optional[str], messages: List[Dict[str, Any]]
) -> str:
    """Infer a contract route while preserving explicit user mutation intent."""
    prompt = system_prompt or ""
    if prompt.startswith(_CONTEXT_SUMMARY_PREFIX):
        return "summarize"
    if "[PLANNING MODE ACTIVE]" in prompt:
        return "context-gather"

    tool_contract = prompt
    if "Tool Contract:\n" in prompt:
        tool_contract = prompt.split("Tool Contract:\n", 1)[1]
        if "\n\nMemory:\n" in tool_contract:
            tool_contract = tool_contract.split("\n\nMemory:\n", 1)[0]
    else:
        tool_contract = ""

    tool_names = list(dict.fromkeys(_TOOL_NAME_RE.findall(tool_contract)))
    pinned_route = _pinned_execution_route(messages)
    if pinned_route and tool_names:
        return pinned_route
    recovery_route = _recovery_mutation_route(messages)
    if recovery_route and tool_names:
        return recovery_route
    latest_user = _latest_routing_user_message(messages)
    if tool_names:
        return TaskClassifier().classify_tool_surface(tool_names, prompt=latest_user)
    return "chat"


def split_workspace_context(system_prompt: Optional[str]) -> Tuple[Optional[str], Any]:
    """Move repository-derived sections out of the provider system prompt.

    Tool contracts, KITT memory/harness constraints and explicit planning mode stay in
    orchestration context. Repository files/maps/guidelines, workspace skills and the
    embedded recent conversation are data and are emitted separately with an explicit
    UNTRUSTED_WORKSPACE_DATA trust label.
    """
    if not system_prompt:
        return system_prompt, "not_provided"

    matches = list(_HEADER_RE.finditer(system_prompt))
    if not matches:
        return system_prompt, "not_provided"

    trusted_parts: List[str] = []
    untrusted_sections: List[Dict[str, str]] = []
    if matches[0].start() > 0:
        trusted_parts.append(system_prompt[: matches[0].start()].rstrip())

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(system_prompt)
        section = system_prompt[match.start() : end].strip()
        header = match.group(1)
        if header in _UNTRUSTED_HEADERS:
            body = section[len(header) :].strip()
            if body:
                untrusted_sections.append({"section": header[:-1], "data": body})
        elif section:
            trusted_parts.append(section)

    orchestration = "\n\n".join(part for part in trusted_parts if part).strip()
    workspace_context: Any = (
        {
            "trust": UNTRUSTED_WORKSPACE_LABEL,
            "source": "kitt-agent-cli",
            "sections": untrusted_sections,
        }
        if untrusted_sections
        else "not_provided"
    )
    return orchestration or None, workspace_context


def inject_agent_turn_context(
    messages: List[Dict[str, Any]],
    workspace_context: Any,
    route: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Prefix volatile turn data to the last real user task without mutating inputs.

    KITT-generated tool feedback must remain byte-stable so the reverse-proxy adapter
    can restore assistant.tool_calls -> tool(tool_call_id) before transport. Keeping
    volatile workspace data on a real user task also gives the proxy a stable logical
    conversation identity after it strips the turn-context envelope.
    """
    payload: Dict[str, Any] = {
        "workspace_context": workspace_context,
    }
    if route is not None:
        payload["route"] = normalize_agent_route(route)

    envelope = (
        f"{TURN_CONTEXT_MARKER}\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"
        f"{TURN_CONTEXT_END_MARKER}"
    )
    cloned = [dict(message) for message in messages]
    for index in range(len(cloned) - 1, -1, -1):
        message = cloned[index]
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        if _is_internal_tool_feedback(content):
            continue
        message["content"] = f"{envelope}\n\n{content}" if content else envelope
        return cloned

    # A tool-only continuation has no safe user task to decorate. Keep the tool
    # result untouched and carry dynamic orchestration context separately.
    cloned.append({"role": "developer", "content": envelope})
    return cloned
