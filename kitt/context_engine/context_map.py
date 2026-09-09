"""Goal-aware, token-light repository context map."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

_STOP = {
    "the", "and", "for", "with", "from", "this", "that", "into", "when",
    "then", "uma", "para", "com", "como", "este", "esta", "isso", "que",
    "implementar", "implementation", "fix", "corrigir", "adicionar", "add",
}


def _terms(text: str, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.$/-]{2,}", text or ""):
        folded = token.casefold()
        if folded in _STOP or folded in seen:
            continue
        seen.add(folded)
        result.append(token)
        if len(result) >= limit:
            break
    return result


class ContextMapBuilder:
    def __init__(self, index, goals=None, registry=None):
        self.index = index
        self.goals = goals
        self.registry = registry

    def _goal_text(self, goal_id: str | None, conversation_id: str) -> tuple[str, str | None]:
        if self.goals is None:
            return "", None
        goal = (
            self.goals.get_scoped(goal_id, conversation_id)
            if goal_id
            else self.goals.active(conversation_id)
        )
        if not goal:
            return "", None
        criteria = " ".join(str(item) for item in (goal.success_criteria or []))
        return f"{goal.objective} {criteria}".strip(), goal.id

    def _changed_files(self, security_context) -> list[str]:
        # Do not run a workspace-wide git command for path-scoped principals.
        if security_context is not None and getattr(security_context, "is_path_scoped", False):
            return []
        registry = self.registry
        if registry is None or getattr(registry, "process_runner", None) is None:
            return []
        try:
            result = registry.process_runner.run(
                ["git", "status", "--short"], timeout_seconds=5
            )
        except Exception:
            return []
        if result.returncode != 0:
            return []
        changed: list[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if security_context is not None:
                try:
                    security_context.assert_path_allowed(path)
                except PermissionError:
                    continue
            changed.append(path)
            if len(changed) >= 64:
                break
        return changed

    def build(
        self,
        arguments: dict[str, Any],
        *,
        conversation_id: str,
        security_context=None,
    ) -> dict[str, Any]:
        if self.index is None:
            raise RuntimeError("Repository index unavailable")

        goal_text, resolved_goal = self._goal_text(
            str(arguments.get("goal_id") or "") or None, conversation_id
        )
        query = " ".join(
            part for part in [str(arguments.get("query") or "").strip(), goal_text] if part
        ).strip()
        terms = _terms(query)
        if not terms:
            raise ValueError("repo.context_map requires query text or an active goal")

        try:
            limit = max(3, min(int(arguments.get("limit", 20) or 20), 60))
        except (TypeError, ValueError):
            limit = 20

        scores: dict[str, float] = defaultdict(float)
        reasons: dict[str, set[str]] = defaultdict(set)
        matched_terms: dict[str, set[str]] = defaultdict(set)

        for term_index, term in enumerate(terms):
            rows = self.index.search_text(term, limit=24)
            for rank, row in enumerate(rows):
                path = str(row.get("path") or "")
                if not path:
                    continue
                if security_context is not None:
                    try:
                        security_context.assert_path_allowed(path)
                    except PermissionError:
                        continue
                weight = 3.0 / (1.0 + rank) + 0.1 * (len(terms) - term_index)
                scores[path] += weight
                reasons[path].add("text")
                matched_terms[path].add(term)
                if term.casefold() in path.casefold():
                    scores[path] += 2.0
                    reasons[path].add("path")

            # Aider-style symbol awareness, but using KITT's own RepositoryIndex.
            try:
                symbols = self.index.repository_map(
                    mode="symbol", query=term, path="", limit=12
                )
            except Exception:
                symbols = []
            for rank, row in enumerate(symbols):
                path = str(row.get("path") or "")
                if not path:
                    continue
                if security_context is not None:
                    try:
                        security_context.assert_path_allowed(path)
                    except PermissionError:
                        continue
                scores[path] += 4.0 / (1.0 + rank)
                reasons[path].add("symbol")
                matched_terms[path].add(term)

        changed = self._changed_files(security_context)
        for path in changed:
            scores[path] += 3.0
            reasons[path].add("git_changed")

        ranked = sorted(
            scores,
            key=lambda path: (-scores[path], path.casefold()),
        )[:limit]
        try:
            max_tokens = max(128, min(int(arguments.get("max_tokens", 900) or 900), 4000))
        except (TypeError, ValueError):
            max_tokens = 900

        files: list[dict[str, Any]] = []
        for path in ranked:
            row = {
                "path": path,
                "score": round(scores[path], 3),
                "reasons": sorted(reasons[path]),
                "matched_terms": sorted(matched_terms[path]),
            }
            candidate = files + [row]
            raw = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if files and (len(raw) + 3) // 4 > max_tokens:
                break
            files = candidate

        return {
            "query": query,
            "goal_id": resolved_goal,
            "terms": terms,
            "files": files,
            "ranked_files": len(ranked),
            "omitted_files": max(0, len(ranked) - len(files)),
            "changed_files": changed[:20],
            "max_tokens": max_tokens,
            "hint": (
                "Use repo.read on the top files; request exact search matches only "
                "when the map is insufficient."
            ),
        }
