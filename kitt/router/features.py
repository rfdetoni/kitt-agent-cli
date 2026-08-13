"""Deterministic feature extractor for incoming task prompts."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Set, List
from kitt.router.models import TaskFeatures

READ_KEYWORDS = {
    "show", "list", "view", "find", "search", "explain", "read", "check", "describe", "what", "where", "how",
    "mostre", "liste", "veja", "procure", "busque", "explique", "leia", "verifique", "descreva", "oque", "onde", "como"
}

MUTATION_KEYWORDS = {
    "create", "write", "edit", "update", "fix", "add", "delete", "remove", "refactor", "change", "patch", "modify",
    "crie", "escreva", "edite", "atualize", "corrija", "adicione", "remova", "delete", "refatore", "altere", "modifique"
}

DEBUG_KEYWORDS = {
    "bug", "error", "exception", "fail", "failure", "traceback", "crash", "fix", "debug", "broken",
    "erro", "excecao", "falha", "corrigir", "depurar", "quebrado"
}

PATH_REGEX = re.compile(r'(?:[a-zA-Z0-9_\-.]+/)+[a-zA-Z0-9_\-.]+\.[a-zA-Z0-9]+|\b[a-zA-Z0-9_\-.]+\.(?:py|js|ts|java|go|rs|sql|json|md|c|cpp|h|yml|yaml|toml)\b')
SHELL_OPERATORS = {";", "&&", "||", "|", "`", "$("}


def _normalize(text: str) -> str:
    n = unicodedata.normalize('NFD', text.lower())
    return "".join(c for c in n if unicodedata.category(c) != 'Mn')


class TaskFeatureExtractor:
    """Extracts deterministic TaskFeatures from prompt and workspace state."""

    @staticmethod
    def extract(
        prompt: str,
        explicit_files: Set[str] | None = None,
        is_continuation: bool = False
    ) -> TaskFeatures:
        normalized = _normalize(prompt)
        words = set(re.findall(r'\b\w+\b', normalized))

        paths = list(set(PATH_REGEX.findall(prompt)))
        if explicit_files:
            paths = list(set(paths + list(explicit_files)))

        exts = {Path(p).suffix.lstrip('.') for p in paths if Path(p).suffix}
        languages = tuple(sorted(list(exts)))

        # Intent detection
        is_debug = bool(words & DEBUG_KEYWORDS)
        is_mutation = bool(words & MUTATION_KEYWORDS)
        is_read = bool(words & READ_KEYWORDS) and not is_mutation

        if is_debug:
            intent = "DEBUG"
        elif is_mutation:
            intent = "IMPLEMENT"
        elif is_read:
            intent = "READ"
        else:
            intent = "GENERAL"

        secondary_intents = []
        if is_debug and is_mutation:
            secondary_intents.append("IMPLEMENT")
        if is_mutation:
            secondary_intents.append("TEST")
        secondary = tuple(secondary_intents)

        # Risk assessment
        has_shell_ops = any(op in prompt for op in SHELL_OPERATORS)
        has_secrets = any(k in normalized for k in ("secret", "key", "token", "password", "senha"))
        has_escape = "../" in prompt or "..\\" in prompt
        has_delete = any(k in normalized for k in ("delete", "remove", "deletar", "remover", "rm "))

        if has_escape or has_secrets or (has_shell_ops and has_delete):
            risk = "CRITICAL"
        elif has_shell_ops or has_delete or is_mutation or is_debug:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        # Complexity assessment
        est_files = max(len(paths), 1 if is_mutation else 0)
        cross_module = len(paths) >= 3 or ("refactor" in words or "refatore" in words)

        if cross_module or est_files >= 4 or risk == "CRITICAL":
            complexity = "HIGH"
        elif est_files >= 2 or is_mutation or is_debug:
            complexity = "MEDIUM"
        else:
            complexity = "LOW"

        prompt_tokens = max(1, len(prompt) // 4)
        expected_context = prompt_tokens + (2000 if cross_module else 500)

        requires_tools = is_mutation or is_debug or bool(paths) or ("search" in words or "find" in words)
        requires_validation = is_mutation or is_debug

        ambiguity = 0.1 if (paths or is_mutation or is_read) else 0.5
        confidence = round(1.0 - ambiguity, 2)

        return TaskFeatures(
            intent=intent,
            secondary_intents=secondary,
            complexity=complexity,
            risk=risk,
            requires_repository=requires_tools,
            requires_tools=requires_tools,
            requires_validation=requires_validation,
            estimated_files=est_files,
            cross_module=cross_module,
            prompt_tokens=prompt_tokens,
            expected_context_tokens=expected_context,
            ambiguity=ambiguity,
            confidence=confidence,
            languages=languages,
            paths=tuple(sorted(paths)),
            symbols=(),
            actions=("edit" if is_mutation else "read",),
            source="deterministic"
        )
