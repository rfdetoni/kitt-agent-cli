"""Bounded opt-in multilingual task classification with local Ollama embeddings.

The decision is advisory and NEVER replaces the original user request or grants
tool permissions. Scores are cosine similarity, NOT calibrated probabilities.
"""
from __future__ import annotations

import math
import re
import threading
from functools import lru_cache
from typing import Sequence

from kitt.context.rag.embeddings import EmbeddingError, OllamaEmbeddingProvider
from kitt.context_filter.fallback import DeterministicFallbackPlanner
from kitt.domain.entities import SemanticTask

# Dataset revision is part of the decision policy and model-cache identity.
EXAMPLE_REVISION = "2026-10-15-v1"
EXAMPLES: tuple[tuple[str, str], ...] = (
    ("ASK", "Explique como o cache funciona sem modificar arquivos."),
    ("ASK", "Explain the implementation without changing any files."),
    ("ASK", "Explica el funcionamiento del código sin editar archivos."),
    ("PLAN", "Planeje a migração, mas não execute nenhuma alteração."),
    ("PLAN", "Draft a migration plan without applying it."),
    ("PLAN", "Planifica la migración sin ejecutarla."),
    ("IMPLEMENT", "Crie um arquivo de configuração e implemente a integração."),
    ("IMPLEMENT", "Create a configuration file and implement the feature."),
    ("IMPLEMENT", "Crea un archivo de configuración e implementa la función."),
    ("DEBUG", "Corrija o bug de autenticação no serviço."),
    ("DEBUG", "Fix the authentication bug in the service."),
    ("DEBUG", "Corrige el error de autenticación en el servicio."),
    ("TEST", "Execute os testes do módulo de usuários."),
    ("TEST", "Run the user module tests."),
    ("TEST", "Ejecuta las pruebas del módulo de usuarios."),
    ("REVIEW", "Revise o código e relate riscos sem aplicar mudanças."),
    ("REVIEW", "Review the code and report issues without editing."),
    ("REVIEW", "Revisa el código e informa problemas sin modificarlo."),
    ("DOCUMENT", "Escreva a documentação do endpoint."),
    ("DOCUMENT", "Document the API endpoint."),
    ("DOCUMENT", "Documenta el endpoint de la API."),
    ("REFACTOR", "Refatore o serviço preservando as assinaturas públicas."),
    ("REFACTOR", "Refactor the service preserving public signatures."),
    ("REFACTOR", "Refactoriza el servicio preservando las firmas públicas."),
)
_MUTATING = {"IMPLEMENT", "DEBUG", "REFACTOR", "DOCUMENT"}
_CONTINUATION = re.compile(
    r"(?i)^\s*(?:continue|continua|continúe|continúa|faça isso|faca isso|"
    r"do the same|haz lo mismo|siga|go on)\b"
)
_READONLY = re.compile(
    r"(?i)^\s*(?:como|cómo|how|explique|explica|explain|descreva|describe|"
    r"revisa|review|revise|não\s+crie|nao\s+crie|no\s+crees|do\s+not\s+create)\b"
)
_NEGATED_MUTATION = re.compile(
    r"(?i)\b(?:não|nao|no|do not|don't|never)\s+"
    r"(?:crie|crea|crees|create|altere|altera|cambies|change|edit|edite|"
    r"modifique|modifica|modify|apliques|apply|execute|ejecutes)\b"
)
_TEST_THEN = re.compile(
    r"(?i)\b(?:e|and|y)\s+(?:rode|execute|ejecuta|run|corra|"
    r"teste|testa|test|valide|validate)\b"
)



# Host-side imperative patterns are deliberately anchored to the beginning of
# the human request. Matching a code symbol such as "UserService.fix" must not
# classify it as a mutation. These are not probability scores.
_DIRECT_INTENTS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (intent, re.compile(r"(?i)^\s*(?:(?:please|por favor)[:,]?\s+)?" + pattern))
    for intent, pattern in (
        ("ASK", r"(?:explique|explica|explain|describe|descreva|cómo|como|how|why|what|"
                r"qual|quais|por\s+que|por\s+qué|o\s+que|qué)\b"),
        ("PLAN", r"(?:planeje|planifique|planifica|plan|planejar|elabore\s+um\s+plano|"
                 r"draft\s+(?:a\s+)?(?:plan|strategy)|outline)\b"),
        ("REVIEW", r"(?:revise|revisa|review|revisar|inspecione|inspect|audit|audite|"
                   r"avalie|evaluate)\b"),
        ("REFACTOR", r"(?:refatore|refactor|refactoriza|refactorize|restructure|"
                     r"reestruture|reestructura)\b"),
        ("DEBUG", r"(?:corrija|corrige|fix|debug|depure|depura|diagnostique|"
                  r"diagnostica|troubleshoot|repare|repair|arregla)\b"),
        ("TEST", r"(?:teste|test|run\s+(?:the\s+)?tests|run\s+the\s+\w+\s+tests|"
                 r"execute\s+(?:os|as|o|a)\s+testes|execute\s+(?:a\s+)?suíte|"
                 r"ejecuta\s+(?:las|los)\s+pruebas|rode\s+(?:os|as)\s+testes|"
                 r"launch\s+(?:the\s+)?(?:regression|integration)\s+tests)\b"),
        ("DOCUMENT", r"(?:documente|documenta|document|atualize\s+(?:a\s+)?documentação|"
                     r"update\s+(?:the\s+)?documentation|"
                     r"actualiza\s+(?:la\s+)?documentación|"
                     r"write\s+(?:the\s+)?documentation|escreva\s+(?:a\s+)?documentação|"
                     r"redacta\s+una\s+guía)\b"),
        ("IMPLEMENT", r"(?:crie|create|crea|implemente|implement|implementa|"
                      r"adicione|add|agrega|construa|build|construye|"
                      r"escreva|write|escribe|gere|generate)\b"),
    )
)

def deterministic_intent(prompt: str) -> str | None:
    """Return only a direct unambiguous leading imperative; otherwise abstain."""
    original = prompt.strip()
    if len(original) < 12 or len(original) > 1200:
        return None
    if _CONTINUATION.search(original):
        return None
    # "Don't edit; explain..." is a read-only prohibition, not an edit.
    if re.match(
        r"(?i)^\s*(?:não|nao|no|do\s+not|don't)\s+"
        r"(?:crie|crees|create|edit|edite|altere|change|cambies|"
        r"modifique|modifiques|modify)\b", original
    ):
        return "ASK"
    if re.search(r"(?i)\b(?:e\s+depois|and\s+then|y\s+después)\b", original):
        return None
    # Resolve deliverable-specific instructions before broad imperative patterns.
    normalized = original.casefold()
    if re.match(r"(?i)^write\s+(?:a\s+)?(?:database\s+)?(?:migration\s+)?strategy\b", original):
        return "PLAN"
    if re.match(r"(?i)^(?:atualize|update|actualiza)\b", original) and any(
        word in normalized for word in ("readme", "instruções", "instrucciones", "instructions")
    ):
        return "DOCUMENT"
    if re.match(r"(?i)^(?:execute|run|launch|ejecuta|rode)\b", original) and any(
        word in normalized for word in ("tests", "testes", "pruebas", "suite", "suíte")
    ):
        return "TEST"

    for intent, pattern in _DIRECT_INTENTS:
        if pattern.match(original):
            if intent in _MUTATING and _NEGATED_MUTATION.search(original):
                # Scope of a negation (API vs file) is not safely inferred here.
                # A complex mix is left to the original generative planner.
                return None
            return intent
    return None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or len(a) != len(b):
        raise EmbeddingError("Local semantic vector dimension mismatch")
    if not all(math.isfinite(float(x)) for x in (*a, *b)):
        raise EmbeddingError("Non-finite local semantic vector")
    na = math.sqrt(sum(float(x) ** 2 for x in a))
    nb = math.sqrt(sum(float(x) ** 2 for x in b))
    if na <= 1e-10 or nb <= 1e-10:
        raise EmbeddingError("Zero-norm local semantic vector")
    return sum(float(x) * float(y) for x, y in zip(a, b, strict=True)) / (na * nb)


class LocalIntentClassifier:
    """Small exact prototype search; one bounded local encoder shared by turns."""

    def __init__(self, provider: OllamaEmbeddingProvider):
        self.provider = provider
        self._lock = threading.Lock()
        self._examples: list[list[float]] | None = None
        self._prototype_revision = ""
        self.last_reason = "not_started"
        self.last_score: float | None = None

    def classify(self, prompt: str) -> tuple[SemanticTask | None, str]:
        if not prompt.strip() or len(prompt) < 12:
            return None, "short_or_empty"
        if len(prompt) > 1200:
            return None, "complex_prompt"
        if _CONTINUATION.search(prompt):
            return None, "conversation_dependency"
        explicit = deterministic_intent(prompt)
        if explicit is not None:
            task = DeterministicFallbackPlanner().generate_task(prompt)
            task.intent = explicit
            task.confidence = 0.8  # Rule evidence, not calibrated probability.
            task.secondary_intents = (
                ["TEST"] if explicit in _MUTATING and _TEST_THEN.search(prompt) else []
            )
            if explicit in {"ASK", "PLAN", "REVIEW"}:
                task.actions = ["analyze", "answer without modifying workspace"]
                task.validation_hints = []
            if explicit == "TEST":
                task.actions = ["analyze", "run the requested tests"]
            return task, "explicit_imperative"
        if not self._lock.acquire(timeout=2.0):
            return None, "local_encoder_busy"
        try:
            revision_check = getattr(self.provider, "cache_identity", None)
            if callable(revision_check):
                current_revision = str(revision_check()) + "|" + EXAMPLE_REVISION
                if current_revision != self._prototype_revision:
                    self._examples = None
                    self._prototype_revision = current_revision
            if self._examples is None:
                self._examples = self.provider.embed_queries([x[1] for x in EXAMPLES])
                if len(self._examples) != len(EXAMPLES):
                    raise EmbeddingError("Prototype vector count mismatch")
            query = self.provider.embed_queries([prompt])
            if len(query) != 1:
                raise EmbeddingError("Query vector count mismatch")
            by_intent: dict[str, list[float]] = {}
            for (intent, _), vector in zip(EXAMPLES, self._examples, strict=True):
                by_intent.setdefault(intent, []).append(_cosine(query[0], vector))
            # Two nearest prototypes per intent, not a single-example lookup.
            ranked = sorted(
                ((sum(sorted(scores, reverse=True)[:2]) / 2.0, intent)
                 for intent, scores in by_intent.items()),
                reverse=True,
            )
            score, intent = ranked[0]
            margin = score - ranked[1][0]
            self.last_score = score
            # Deliberately conservative provisional gates; must be calibrated
            # on a held-out multilingual dataset before production default-on.
            if score < 0.87 or margin < 0.035:
                return None, "score_or_margin"
            if _READONLY.match(prompt) and intent in _MUTATING:
                return None, "read_only_conflict"
            if _NEGATED_MUTATION.search(prompt) and intent in _MUTATING:
                return None, "negated_mutation"
            task = DeterministicFallbackPlanner().generate_task(prompt)
            task.intent = intent  # tool permissions are enforced later by runtime policy
            task.secondary_intents = (
                ["TEST"] if intent in _MUTATING and _TEST_THEN.search(prompt) else []
            )
            if intent in {"ASK", "PLAN", "REVIEW"}:
                task.actions = ["analyze", "answer without modifying workspace"]
                task.validation_hints = []
            elif intent == "TEST":
                task.actions = ["analyze", "run the requested tests"]
                task.validation_hints = []
            # This is not a probability: cap confidence conservatively while
            # exposing the raw similarity only as diagnostics.
            task.confidence = 0.65
            return task, "accepted"
        except (EmbeddingError, OSError, ValueError, RuntimeError) as exc:
            return None, "local_embedding_unavailable:" + type(exc).__name__
        finally:
            self._lock.release()


@lru_cache(maxsize=2)
def get_local_classifier(model: str, endpoint: str) -> LocalIntentClassifier:
    """Lazy load, shared cache; never downloads or starts an Ollama process."""
    return LocalIntentClassifier(
        OllamaEmbeddingProvider(model=model, base_url=endpoint,
                                timeout_seconds=4, total_timeout_seconds=8,
                                batch_size=32)
    )
