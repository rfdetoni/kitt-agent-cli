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
    "improve", "style", "modernize", "rewrite", "enhance", "redesign", "customize", "adjust",
    "crie", "escreva", "edite", "dite", "atualize", "corrija", "adicione", "remova", "delete", "refatore", "altere", "modifique",
    "melhore", "estilize", "modernize", "reformule", "recrie", "aprimore", "customize", "ajuste", "deixe", "mude"
}

DEBUG_KEYWORDS = {
    "bug", "error", "exception", "fail", "failure", "traceback", "crash", "fix", "debug", "broken",
    "erro", "excecao", "falha", "corrigir", "depurar", "quebrado"
}

PATH_REGEX = re.compile(r'(?:@)?(?:[a-zA-Z0-9_\-.]+/)+[a-zA-Z0-9_\-.]+\.[a-zA-Z0-9]+|(?:@)?[a-zA-Z0-9_\-.]+\.(?:py|js|ts|tsx|jsx|html|css|json|md|yaml|yml|toml|sh|rs|go|c|cpp|h|java|sql|txt)\b')
SHELL_OPERATORS = {";", "&&", "||", "|", "`", "$("}


def _normalize(text: str) -> str:
    n = unicodedata.normalize('NFD', text.lower())
    return "".join(c for c in n if unicodedata.category(c) != 'Mn')


class TaskFeatureExtractor:
    """Extracts TaskFeatures directly from compiled SemanticTask IR or prompt fallback."""

    @classmethod
    def from_task(
        cls,
        task: Any,
        prompt: str = "",
        explicit_files: Set[str] | None = None,
        is_continuation: bool = False
    ) -> TaskFeatures:
        """Autonomously extracts features from the small model's canonical Task IR."""
        if not hasattr(task, "intent") or not hasattr(task, "paths"):
            return cls.extract(prompt or str(task), explicit_files, is_continuation)

        raw_paths = list(getattr(task, "paths", []))
        if explicit_files:
            raw_paths.extend(list(explicit_files))
        paths = tuple(sorted(list(dict.fromkeys(p.lstrip('@') for p in raw_paths))))

        exts = {Path(p).suffix.lstrip('.') for p in paths if Path(p).suffix}
        techs = tuple(getattr(task, "technologies", ()))
        languages = tuple(sorted(list(set(techs).union(exts))))

        intent = getattr(task, "intent", "GENERAL")
        if intent == "UNKNOWN":
            intent = "GENERAL"

        secondary = tuple(getattr(task, "secondary_intents", ()))
        is_mutation = intent in {"IMPLEMENT", "REFACTOR"}
        is_debug = intent == "DEBUG"
        is_read = intent in {"ASK", "READ", "PLAN", "DOCUMENT"} and not (is_mutation or is_debug)

        risk = str(getattr(task, "risk", "LOW")).upper()
        if risk not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            risk = "MEDIUM" if (is_mutation or is_debug) else "LOW"

        est_files = max(len(paths), 1 if is_mutation else 0)
        cross_module = len(paths) >= 3 or intent == "REFACTOR"

        if cross_module or est_files >= 4 or risk in {"HIGH", "CRITICAL"}:
            complexity = "HIGH"
        elif est_files >= 2 or is_mutation or is_debug:
            complexity = "MEDIUM"
        else:
            complexity = "LOW"

        prompt_str = prompt or getattr(task, "original_prompt", "")
        prompt_tokens = max(1, len(prompt_str) // 4)
        expected_context = prompt_tokens + (2000 if cross_module else 500)

        requires_tools = bool(paths) or bool(getattr(task, "symbols", ())) or intent in {"IMPLEMENT", "DEBUG", "REFACTOR", "DOCUMENT", "TEST"}
        requires_validation = is_mutation or is_debug

        confidence = float(getattr(task, "confidence", 1.0))
        ambiguity = round(max(0.0, min(1.0, 1.0 - confidence)), 2)

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
            paths=paths,
            symbols=tuple(sorted(list(dict.fromkeys(getattr(task, "symbols", ()))))),
            actions=tuple(getattr(task, "actions", ())),
            source="semantic"
        )

    @staticmethod
    def extract(
        prompt: str,
        explicit_files: Set[str] | None = None,
        is_continuation: bool = False
    ) -> TaskFeatures:
        normalized = _normalize(prompt)
        words = set(re.findall(r'\b\w+\b', normalized))

        raw_paths = PATH_REGEX.findall(prompt)
        cleaned_paths = [p.lstrip('@') for p in raw_paths]
        paths = list(set(cleaned_paths))
        if explicit_files:
            cleaned_explicit = [p.lstrip('@') for p in explicit_files]
            paths = list(set(paths + cleaned_explicit))

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
