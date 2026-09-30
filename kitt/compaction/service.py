from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Callable, List, Optional

from kitt.compaction.models import CompactionResult, WorkingState
from kitt.compaction.validator import CompactionValidator
from kitt.context_filter.prompt_budget import TokenCounter
from kitt.history.database import HistoryDatabase
from kitt.history.session_tree import SessionTreeRepository
from kitt_protocol import ContextRecoveryRef, RecoveryMode


class CompactionService:
    def __init__(
        self,
        db: HistoryDatabase,
        tree: SessionTreeRepository,
        summarizer: Optional[Callable[[str], str]] = None,
        keep_recent: int = 6,
        artifact_store=None,
        workspace_id: str = "",
    ):
        self.db = db
        self.tree = tree
        self.summarizer = summarizer
        self.default_keep_recent = keep_recent
        self.artifact_store = artifact_store
        self.workspace_id = str(workspace_id or "")
        self.validator = CompactionValidator()

    def compact(
        self,
        conversation_id: str,
        keep_recent: int = 6,
        mandatory_facts: Optional[List[str]] = None,
    ) -> Optional[CompactionResult]:
        path = [e for e in self.tree.get_active_path(conversation_id) if e.include_in_context]
        if len(path) <= keep_recent:
            return None
        old = path[:-keep_recent]
        kept = path[-keep_recent:]
        raw = "\n".join(
            str(e.payload.get("content") or e.payload.get("summary") or e.payload)
            for e in old
        )
        recovery_refs: list[dict[str, object]] = []
        if self.artifact_store is not None and self.workspace_id and raw:
            raw_bytes = raw.encode("utf-8")
            try:
                artifact = self.artifact_store.put(
                    self.workspace_id,
                    raw_bytes,
                    "COMPACTION_SOURCE",
                    "Exact pre-compaction conversation context",
                    conversation_id=conversation_id,
                    metadata={
                        "compacted_entry_ids": [e.id for e in old],
                        "recovery": "EXACT",
                    },
                )
                recovery_refs.append(
                    {
                        "artifact_id": artifact.id,
                        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                        "original_bytes": len(raw_bytes),
                        "token_estimate": TokenCounter.count_tokens(raw),
                        "media_type": "text/plain; charset=utf-8",
                        "recovery": RecoveryMode.EXACT.value,
                    }
                )
            except Exception:
                # Compaction remains available in ephemeral/test runtimes, but
                # callers can see that no exact recovery reference was created.
                recovery_refs = []
        narrative = self.summarizer(raw) if self.summarizer else self._deterministic_summary(raw)
        working_state = self._working_state(raw, narrative, mandatory_facts or [])
        summary = working_state.render() or narrative
        valid, details = self.validator.validate(summary, mandatory_facts or [])
        if not valid:
            raise ValueError(f"Unsafe compaction: {details}")
        entry = self.tree.append_entry(
            conversation_id,
            "COMPACTION",
            {
                "summary": summary,
                "working_state": working_state.to_dict(),
                "compacted_entry_ids": [e.id for e in old],
                "recovery_refs": recovery_refs,
            },
            parent_entry_id=old[0].parent_entry_id,
            use_active_parent=False,
        )
        parent = entry.id
        for recent in kept:
            cloned = self.tree.append_entry(
                conversation_id,
                recent.entry_type,
                recent.payload,
                turn_id=recent.turn_id,
                parent_entry_id=parent,
                include_in_context=recent.include_in_context,
            )
            parent = cloned.id
        cid = f"cmp_{uuid.uuid4().hex}"
        before = TokenCounter.count_tokens(raw)
        after = TokenCounter.count_tokens(summary)
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO compactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cid,
                    conversation_id,
                    entry.id,
                    old[0].id,
                    kept[0].id,
                    summary,
                    before,
                    after,
                    1,
                    None,
                    json.dumps({
                        **details,
                        "working_state": working_state.to_dict(),
                        "recovery_refs": recovery_refs,
                    }),
                    time.time(),
                ),
            )
        return CompactionResult(
            cid,
            conversation_id,
            entry.id,
            summary,
            before,
            after,
            valid,
            details,
            working_state,
            tuple(recovery_refs),
        )

    @staticmethod
    def _dedupe(lines: list[str], limit: int = 12) -> tuple[str, ...]:
        seen: set[str] = set()
        result: list[str] = []
        for raw in lines:
            value = " ".join(str(raw).split()).strip()
            key = value.casefold()
            if not value or key in seen:
                continue
            seen.add(key)
            result.append(value)
            if len(result) >= limit:
                break
        return tuple(result)

    @classmethod
    def _working_state(
        cls,
        raw: str,
        narrative: str,
        mandatory_facts: list[str],
    ) -> WorkingState:
        lines = [
            " ".join(line.split()).strip()
            for line in (raw + "\n" + narrative).splitlines()
            if line.strip()
        ]
        objective = next(
            (
                line
                for line in lines
                if any(key in line.casefold() for key in ("objective", "goal", "objetivo", "task"))
            ),
            lines[0] if lines else "",
        )
        decisions: list[str] = list(mandatory_facts)
        artifacts: list[str] = []
        errors: list[str] = []
        pending: list[str] = []
        validation: list[str] = []
        current: list[str] = []
        path_re = re.compile(
            r"(?:^|\s)([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9_.-]+)"
        )
        for line in lines:
            lower = line.casefold()
            matched = False
            if any(key in lower for key in ("must ", "must not", "constraint", "decision", "decid", "regra", "requirement")):
                decisions.append(line)
                matched = True
            if any(key in lower for key in ("error", "fail", "failed", "exception", "corrig", "fix", "repair")):
                errors.append(line)
                matched = True
            if any(key in lower for key in ("todo", "pending", "next", "remaining", "falta", "pendente", "open issue")):
                pending.append(line)
                matched = True
            if any(key in lower for key in ("test", "lint", "compile", "build", "validate", "passed", "green", "quality gate")):
                validation.append(line)
                matched = True
            paths = path_re.findall(line)
            if paths:
                artifacts.extend(paths)
                matched = True
            if not matched:
                current.append(line)
        return WorkingState(
            objective=objective[:500],
            current_state=cls._dedupe(current, 10),
            constraints_and_decisions=cls._dedupe(decisions, 12),
            affected_artifacts=cls._dedupe(artifacts, 20),
            errors_and_corrections=cls._dedupe(errors, 10),
            pending_work=cls._dedupe(pending, 10),
            validation_state=cls._dedupe(validation, 10),
        )

    @staticmethod
    def _deterministic_summary(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 24:
            return "\n".join(lines)

        signal_keywords = (
            "error", "fail", "failed", "warning", "todo", "fixme", "constraint",
            "must", "must not", "path", "file", "class", "def", "function",
            "method", "import", "return", "raise", "test", "pytest", "git",
            "changed", "created", "deleted", "decision", "result", "applied"
        )

        selected_indices = set(range(min(8, len(lines))))
        selected_indices.update(range(max(0, len(lines) - 8), len(lines)))

        for i in range(8, len(lines) - 8):
            line_lower = lines[i].lower()
            if any(sig in line_lower for sig in signal_keywords):
                selected_indices.add(i)
                if len(selected_indices) >= 32:
                    break

        ordered = sorted(selected_indices)
        res = []
        last_idx = -1
        for idx in ordered:
            if last_idx != -1 and idx > last_idx + 1:
                res.append("[... compacted ...]")
            res.append(lines[idx])
            last_idx = idx

        return "\n".join(res)
