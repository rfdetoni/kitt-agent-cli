"""Adapter from KITT's Rust native search results to ContextCandidate objects."""
from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

from kitt.context.candidates import ContextCandidate
from kitt.context.query_plan import QueryPlan
from kitt.native.bridge import NativeCodeEngine


class NativeLexicalRetriever:
    """Use Rust as the lexical discovery layer; never call its Python scanner fallback."""

    def __init__(self, engine: NativeCodeEngine) -> None:
        self.engine = engine

    @property
    def available(self) -> bool:
        status = getattr(self.engine, "status", None)
        return bool(status and status.available and status.backend == "rust")

    def retrieve(
        self,
        prompt: str,
        plan: QueryPlan,
        *,
        working_set_paths: set[str],
        max_results: int,
        context_lines: int,
        token_budget: int,
    ) -> list[ContextCandidate]:
        if not self.available:
            return []
        pattern = self._build_pattern(prompt, plan)
        if not pattern:
            return []
        try:
            response = self.engine.search(
                pattern,
                regex=True,
                case_sensitive=False,
                max_results=max_results,
                max_per_file=4,
                context_lines=context_lines,
                token_budget=token_budget,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return []

        hits = response.get("hits", []) if isinstance(response, dict) else []
        candidates: list[ContextCandidate] = []
        for idx, hit in enumerate(hits):
            if not isinstance(hit, dict) or not hit.get("path"):
                continue
            try:
                path = str(hit["path"]).replace("\\", "/")
                line = max(1, int(hit.get("line") or 1))
                raw_before = hit.get("before", [])
                raw_after = hit.get("after", [])
                before = [str(item) for item in raw_before] if isinstance(raw_before, list) else []
                after = [str(item) for item in raw_after] if isinstance(raw_after, list) else []
                body = "\n".join([*before, str(hit.get("text", "")), *after]).strip()
                native_score = float(hit.get("score") or 0.0)
            except (TypeError, ValueError):
                continue
            if not body:
                continue
            if not math.isfinite(native_score):
                native_score = 0.0
            start_line = max(1, line - len(before))
            end_line = line + len(after)
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            working_boost = 0.08 if path in working_set_paths else 0.0
            candidates.append(
                ContextCandidate(
                    candidate_id=f"native:{path}:{line}:{idx}",
                    source_type="file",
                    path=path,
                    start_line=start_line,
                    end_line=end_line,
                    content_hash=digest,
                    estimated_tokens=max(1, len(body) // 4),
                    relevance=max(0.35, min(0.92, 0.76 + working_boost + native_score * 0.08)),
                    confidence=0.92,
                    freshness=1.0,
                    mandatory=False,
                    trust_level="WORKSPACE_DATA",
                    dependencies=(),
                    selection_reason="Matched by KITT native Rust search"
                    + ("; working set boost" if working_boost else ""),
                    representation="SLICE",
                    content=body,
                )
            )

        terms = self._terms(plan)
        candidates.sort(
            key=lambda candidate: (
                self._coverage(candidate, terms),
                candidate.path in working_set_paths if candidate.path else False,
                candidate.relevance,
            ),
            reverse=True,
        )
        return candidates

    @classmethod
    def _build_pattern(cls, prompt: str, plan: QueryPlan) -> str:
        terms = cls._terms(plan)
        if not terms:
            terms = [
                token
                for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", prompt)
                if len(token) >= 4
            ][:8]
        if not terms:
            return ""
        escaped: list[str] = []
        length = 4
        for term in terms[:10]:
            value = re.escape(term)
            added = len(value) + (1 if escaped else 0)
            if escaped and length + added > 760:
                break
            escaped.append(value)
            length += added
        return "(?:" + "|".join(escaped) + ")" if escaped else ""

    @staticmethod
    def _terms(plan: QueryPlan) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in (*plan.exact_symbols, *plan.lexical_terms):
            value = str(value).strip()
            lowered = value.lower()
            if len(value) < 3 or lowered in seen:
                continue
            seen.add(lowered)
            result.append(value)
        for diagnostic in plan.diagnostics:
            for value in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", diagnostic):
                lowered = value.lower()
                if lowered not in seen:
                    seen.add(lowered)
                    result.append(value)
                if len(result) >= 10:
                    return result
        return result[:10]

    @staticmethod
    def _coverage(candidate: ContextCandidate, terms: Sequence[str]) -> float:
        if not terms:
            return 0.0
        haystack = f"{candidate.path or ''}\n{candidate.content}".lower()
        matched = sum(1 for term in terms if term.lower() in haystack)
        return matched / len(terms)
