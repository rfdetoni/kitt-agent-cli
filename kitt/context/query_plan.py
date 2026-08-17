"""Deterministic query planning for repository context."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Tuple

from kitt.router.features import TaskFeatureExtractor


@dataclass(frozen=True)
class QueryPlan:
    intent: str
    exact_paths: Tuple[str, ...]
    exact_symbols: Tuple[str, ...]
    lexical_terms: Tuple[str, ...]
    diagnostics: Tuple[str, ...]
    preferred_languages: Tuple[str, ...]
    preferred_modules: Tuple[str, ...]
    include_tests: bool
    include_dependents: bool
    include_dependencies: bool
    graph_hops: int
    candidate_limit: int
    deadline_ms: int
    token_budget: int
    confidence: float


class QueryPlanner:
    """Cheap host-side planner. LLM rerank can sit after this, not before it."""

    _IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
    _TRACE_PATH_RE = re.compile(r'File ["\']([^"\']+)["\'], line \d+')
    _DIAG_RE = re.compile(r"(?i)\b(error|exception|traceback|failed|cannot find symbol|undefined|typeerror|valueerror)\b.*")
    _STOP = {
        "the", "and", "for", "with", "this", "that", "from", "como", "para", "este",
        "esta", "isso", "projeto", "project", "analise", "analyze", "indique", "melhore",
        "crie", "create", "corrija", "corrigir", "fix", "bug", "code", "file", "class", "function", "method",
    }

    @classmethod
    def plan(
        cls,
        prompt: str,
        explicit_files=(),
        token_budget: int = 2048,
        candidate_limit: int = 20,
        deadline_ms: int = 120,
    ) -> QueryPlan:
        return cls._plan_cached(prompt, tuple(explicit_files or ()), token_budget, candidate_limit, deadline_ms)

    @classmethod
    @functools.lru_cache(maxsize=128)
    def _plan_cached(
        cls,
        prompt: str,
        explicit_files: Tuple[str, ...],
        token_budget: int,
        candidate_limit: int,
        deadline_ms: int,
    ) -> QueryPlan:
        features = TaskFeatureExtractor.extract(prompt, explicit_files=explicit_files)
        trace_paths = tuple(path for path in cls._TRACE_PATH_RE.findall(prompt) if not path.startswith("<"))
        paths = tuple(dict.fromkeys((*features.paths, *trace_paths, *explicit_files)))
        quoted = re.findall(r"`([^`]+)`", prompt)
        quoted_ids = set(cls._IDENT_RE.findall(" ".join(quoted)))
        identifiers = [
            item for item in cls._IDENT_RE.findall(" ".join(quoted) + " " + prompt)
            if item.lower() not in cls._STOP
        ]
        symbol_like = [
            item for item in identifiers
            if item in quoted_ids or "_" in item or "." in item or (item[:1].isupper() and not item.isupper())
        ]
        symbols = tuple(dict.fromkeys([*features.symbols, *symbol_like]))[:16]
        terms = tuple(dict.fromkeys(term.lower() for term in cls._IDENT_RE.findall(prompt) if term.lower() not in cls._STOP))[:12]
        diagnostics = tuple(line.strip()[:240] for line in prompt.splitlines() if cls._DIAG_RE.search(line))[:8]
        prompt_l = prompt.lower()
        return QueryPlan(
            intent=features.intent,
            exact_paths=paths,
            exact_symbols=symbols,
            lexical_terms=terms,
            diagnostics=diagnostics,
            preferred_languages=features.languages,
            preferred_modules=(),
            include_tests=any(t in prompt_l for t in ("test", "tests", "teste", "pytest", "unittest")),
            include_dependents=features.intent in ("REFACTOR", "DEBUG") or features.cross_module,
            include_dependencies=features.intent in ("IMPLEMENT", "DEBUG", "READ") or features.cross_module,
            graph_hops=2 if features.cross_module else 1,
            candidate_limit=candidate_limit,
            deadline_ms=deadline_ms,
            token_budget=token_budget,
            confidence=features.confidence,
        )

    @classmethod
    def plan_task(
        cls,
        task: Any,
        explicit_files=(),
        token_budget: int = 2048,
        candidate_limit: int = 20,
        deadline_ms: int = 120,
    ) -> QueryPlan:
        """Derive QueryPlan directly from compiled SemanticTask IR."""
        if not hasattr(task, "intent") or not hasattr(task, "paths"):
            return cls.plan(str(task), explicit_files, token_budget, candidate_limit, deadline_ms)

        paths = tuple(dict.fromkeys((*task.paths, *explicit_files)))
        symbols = tuple(dict.fromkeys(task.symbols))[:16]
        
        # Extract lexical terms from goal and actions
        raw_words = []
        if getattr(task, "goal", ""):
            raw_words.extend(cls._IDENT_RE.findall(task.goal))
        for act in getattr(task, "actions", ()):
            raw_words.extend(cls._IDENT_RE.findall(act))
        terms = tuple(dict.fromkeys(w.lower() for w in raw_words if w.lower() not in cls._STOP))[:12]
        
        diagnostics = tuple(
            h.strip()[:240] for h in getattr(task, "validation_hints", ())
            if cls._DIAG_RE.search(h)
        )[:8]

        intent = str(getattr(task, "intent", "UNKNOWN"))
        is_test_related = intent == "TEST" or any(
            "test" in a.lower() for a in getattr(task, "actions", ())
        )

        return QueryPlan(
            intent=intent,
            exact_paths=paths,
            exact_symbols=symbols,
            lexical_terms=terms,
            diagnostics=diagnostics,
            preferred_languages=tuple(getattr(task, "technologies", ())),
            preferred_modules=(),
            include_tests=is_test_related,
            include_dependents=intent in ("REFACTOR", "DEBUG"),
            include_dependencies=intent in ("IMPLEMENT", "DEBUG", "READ", "TEST"),
            graph_hops=1,
            candidate_limit=candidate_limit,
            deadline_ms=deadline_ms,
            token_budget=token_budget,
            confidence=float(getattr(task, "confidence", 1.0)),
        )
