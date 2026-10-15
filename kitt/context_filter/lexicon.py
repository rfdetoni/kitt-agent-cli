"""Conservative PT/EN/ES lexical intent hints; no model or network is required.

Only KITT-reviewed phrases or imperative UniMorph inflections may drive an
intent. Wordnet expansions remain unapproved suggestions until reviewed.
"""
from __future__ import annotations

import os
import re
import sqlite3
from functools import lru_cache
from pathlib import Path

# These are intentionally COMMAND forms, not all synonyms in a dictionary.
CURATED: dict[str, dict[str, tuple[str, ...]]] = {
    "DEBUG": {
        "pt": ("conserte", "solucione", "repare o bug", "resolva o erro"),
        "en": ("resolve the bug", "repair the bug", "troubleshoot"),
        "es": ("resuelve el error", "soluciona el fallo", "arregle el error"),
    },
    "REFACTOR": {
        "pt": ("reestruture", "simplifique a implementação"),
        "en": ("restructure", "simplify the implementation"),
        "es": ("reestructura", "simplifica la implementación"),
    },
    "TEST": {
        "pt": ("verifique os testes", "rode a suíte"),
        "en": ("verify the tests", "execute the test suite"),
        "es": ("comprueba las pruebas", "ejecuta la suite"),
    },
    "REVIEW": {
        "pt": ("inspecione", "examine o código"),
        "en": ("inspect the code", "examine the implementation"),
        "es": ("inspecciona", "examina el código"),
    },
    "DOCUMENT": {
        "pt": ("redija a documentação",),
        "en": ("write the api docs",),
        "es": ("redacta la documentación",),
    },
    "IMPLEMENT": {
        "pt": ("desenvolva", "construa"),
        "en": ("develop", "construct"),
        "es": ("desarrolla", "construye"),
    },
    "ASK": {
        "pt": ("esclareça",),
        "en": ("clarify",),
        "es": ("aclara",),
    },
    "PLAN": {
        "pt": ("projete a solução",),
        "en": ("design the solution",),
        "es": ("diseña la solución",),
    },
}

# Morphology is imported only for these domain-reviewed lemmas.
SEED_LEMMAS: dict[str, dict[str, tuple[str, ...]]] = {
    "DEBUG": {
        "pt": ("consertar", "corrigir", "solucionar", "reparar"),
        "en": ("fix", "repair", "resolve"),
        "es": ("arreglar", "corregir", "solucionar"),
    },
    "REFACTOR": {
        "pt": ("refatorar", "reestruturar"),
        "en": ("refactor", "restructure"),
        "es": ("refactorizar", "reestructurar"),
    },
    "TEST": {
        "pt": ("testar", "verificar"),
        "en": ("test", "verify"),
        "es": ("probar", "verificar"),
    },
    "REVIEW": {
        "pt": ("revisar", "inspecionar"),
        "en": ("review", "inspect"),
        "es": ("revisar", "inspeccionar"),
    },
    "DOCUMENT": {
        "pt": ("documentar",),
        "en": ("document",),
        "es": ("documentar",),
    },
    "IMPLEMENT": {
        "pt": ("implementar", "desenvolver"),
        "en": ("implement", "develop"),
        "es": ("implementar", "desarrollar"),
    },
}

_PREFIX = re.compile(r"(?i)^\s*(?:(?:please|por favor)\s*[:,]?\s*)?")
_FIRST = re.compile(r"^([^\W_]+(?:[-'][^\W_]+)?)", re.UNICODE)
_NEGATION = re.compile(
    r"(?i)\b(?:não|nao|nunca|never|don't|do not|sin|sem|without|no)\b"
    r"[^.!?;\n]{0,65}\b(?:crie|create|crea|corrija|fix|edit|edite|"
    r"altere|change|modifique|modify|cambies|editar|alterar|"
    r"aplique|apply|execute|ejecute|escreva|write)\b"
)
_CONTINUE = re.compile(
    r"(?i)^\s*(?:continue|continua|continúe|continúa|faça isso|"
    r"faca isso|do the same|haz lo mismo|siga|go on)\b"
)

def normalize(text: str) -> str:
    # Casefold is deterministic and does not erase meaningful accents.
    return " ".join(text.casefold().strip().split())


@lru_cache(maxsize=1)
def _builtins() -> dict[str, str]:
    mapping: dict[str, str] = {}
    clashes: set[str] = set()
    for intent, languages in CURATED.items():
        for phrases in languages.values():
            for phrase in phrases:
                phrase = normalize(phrase)
                if phrase in mapping and mapping[phrase] != intent:
                    clashes.add(phrase)
                mapping[phrase] = intent
    for phrase in clashes:
        mapping.pop(phrase, None)
    return mapping


def _database_intents(path: str, prefix: str) -> set[str]:
    """Read a bounded, explicitly configured, immutable user-owned SQLite index."""
    resolved = Path(path).expanduser().resolve(strict=True)
    if not resolved.is_file():
        return set()
    uri = resolved.as_uri() + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True, timeout=0.1) as db:
        db.execute("PRAGMA query_only=ON")
        # Never trust a database from a workspace as a tool permission source.
        rows = db.execute(
            "SELECT DISTINCT intent FROM entries WHERE approved=1 AND form=? LIMIT 3",
            (prefix,),
        ).fetchall()
        return {row[0] for row in rows if row[0] in CURATED}


def lexical_intent(prompt: str) -> str | None:
    """Recognize only an unambiguous leading command; fail closed on uncertainty."""
    if not 12 <= len(prompt.strip()) <= 1200 or _CONTINUE.match(prompt):
        return None
    if _NEGATION.search(prompt):
        return None
    text = normalize(_PREFIX.sub("", prompt))
    match = _FIRST.match(text)
    if not match:
        return None
    first = match.group(1)
    candidates = (text, first)
    matched: set[str] = set()
    mapping = _builtins()
    for item in candidates:
        # Only a full word/phrase at the start is an instruction.
        for phrase, intent in mapping.items():
            if item.startswith(phrase) and (
                len(item) == len(phrase) or not item[len(phrase)].isalnum()
            ):
                matched.add(intent)
    path = os.environ.get("KITT_SEMANTIC_LEXICON_DB", "").strip()
    if path:
        try:
            matched.update(_database_intents(path, first))
        except (OSError, ValueError, sqlite3.DatabaseError):
            pass  # A corrupt/missing optional index cannot alter permissions.
    return next(iter(matched)) if len(matched) == 1 else None
