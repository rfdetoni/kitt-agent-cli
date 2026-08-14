"""Compact context compiler and quality gate."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from kitt.context.candidates import ContextCandidate
from kitt.context.query_plan import QueryPlan
from kitt.context_filter.prompt_budget import TokenCounter


@dataclass(frozen=True)
class ContextAtom:
    atom_id: str
    kind: str
    path: str | None
    start_line: int | None
    end_line: int | None
    content_hash: str
    trust_level: str
    estimated_tokens: int
    reason: str
    content: str


@dataclass(frozen=True)
class ContextQuality:
    ok: bool
    coverage: float
    missing: Tuple[str, ...] = ()
    degraded: bool = False
    reason: str = ""


@dataclass(frozen=True)
class CompiledContext:
    text: str
    atoms: Tuple[ContextAtom, ...]
    quality: ContextQuality
    total_tokens: int
    selected_count: int
    rejected_count: int
    path_ids: Dict[str, str] = field(default_factory=dict)


class ContextQualityGate:
    @staticmethod
    def evaluate(plan: QueryPlan, atoms: Tuple[ContextAtom, ...], partial: bool = False) -> ContextQuality:
        present_paths = {atom.path for atom in atoms if atom.path}
        text = "\n".join(atom.content for atom in atoms).lower()
        missing: List[str] = []
        for path in plan.exact_paths:
            if path not in present_paths:
                missing.append(f"path:{path}")
        for symbol in plan.exact_symbols[:5]:
            if symbol.lower() not in text:
                missing.append(f"symbol:{symbol}")
        required = len(plan.exact_paths) + min(5, len(plan.exact_symbols))
        found = max(0, required - len(missing))
        coverage = 1.0 if required == 0 else found / required
        return ContextQuality(
            ok=not missing,
            coverage=coverage,
            missing=tuple(missing),
            degraded=bool(missing) or partial,
            reason=("explicit_requirement_missing" if missing else "index_partial" if partial else ""),
        )


class ContextCompiler:
    """Compile selected candidates into one stable, compact evidence pack."""

    version = "context-v1"

    def compile(
        self,
        plan: QueryPlan,
        selected: List[ContextCandidate],
        rejected: List[ContextCandidate],
        generation: int = 0,
        partial: bool = False,
    ) -> CompiledContext:
        atoms: List[ContextAtom] = []
        for idx, cand in enumerate(selected, 1):
            content_hash = cand.content_hash or hashlib.sha256(cand.content.encode("utf-8")).hexdigest()
            atoms.append(ContextAtom(
                atom_id=f"A{idx}",
                kind=getattr(cand, "representation", None) or cand.source_type.upper(),
                path=cand.path,
                start_line=cand.start_line,
                end_line=cand.end_line,
                content_hash=content_hash,
                trust_level=cand.trust_level,
                estimated_tokens=cand.estimated_tokens,
                reason=cand.selection_reason,
                content=cand.content,
            ))
        atom_tuple = tuple(atoms)
        quality = ContextQualityGate.evaluate(plan, atom_tuple, partial=partial)
        path_ids: Dict[str, str] = {}
        for atom in atom_tuple:
            if atom.path and atom.path not in path_ids:
                path_ids[atom.path] = f"P{len(path_ids) + 1}"
        lines = [f"## Context v1 gen={generation} partial={str(partial).lower()} ok={str(quality.ok).lower()} coverage={quality.coverage:.2f}"]
        if path_ids:
            lines.append("\n### Paths")
            for path, path_id in sorted(path_ids.items(), key=lambda item: item[1]):
                lines.append(f"[{path_id}] {path}")
        if atom_tuple:
            lines.append("\n### Evidence")
            for atom in atom_tuple:
                path_ref = path_ids.get(atom.path or "", "-")
                loc = ""
                if atom.start_line is not None:
                    loc = f":{atom.start_line}"
                    if atom.end_line and atom.end_line != atom.start_line:
                        loc += f"-{atom.end_line}"
                lines.append(f"\n[{atom.atom_id} {path_ref}{loc}] {atom.kind} trust={atom.trust_level} reason={atom.reason}")
                if atom.content:
                    lines.append("```")
                    lines.append(atom.content.rstrip())
                    lines.append("```")
        if quality.missing:
            lines.append("\n### Missing")
            lines.extend(f"- not_found:{item}" for item in quality.missing)
        text = "\n".join(lines).strip()
        return CompiledContext(
            text=text,
            atoms=atom_tuple,
            quality=quality,
            total_tokens=TokenCounter.count_tokens(text),
            selected_count=len(selected),
            rejected_count=len(rejected),
            path_ids=path_ids,
        )
