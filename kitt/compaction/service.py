import json,time,uuid
from typing import Callable,List,Optional
from kitt.compaction.models import CompactionResult
from kitt.compaction.validator import CompactionValidator
from kitt.context_filter.prompt_budget import TokenCounter
from kitt.history.database import HistoryDatabase
from kitt.history.session_tree import SessionTreeRepository

class CompactionService:
    def __init__(self, db: HistoryDatabase, tree: SessionTreeRepository,
                 summarizer: Optional[Callable[[str],str]]=None, keep_recent: int=6):
        self.db=db; self.tree=tree; self.summarizer=summarizer
        self.default_keep_recent=keep_recent; self.validator=CompactionValidator()
    def compact(self, conversation_id: str, keep_recent: int=6,
                mandatory_facts: Optional[List[str]]=None) -> Optional[CompactionResult]:
        path=[e for e in self.tree.get_active_path(conversation_id) if e.include_in_context]
        if len(path)<=keep_recent: return None
        old=path[:-keep_recent]; kept=path[-keep_recent:]
        raw="\n".join(str(e.payload.get("content") or e.payload.get("summary") or e.payload) for e in old)
        summary=self.summarizer(raw) if self.summarizer else self._deterministic_summary(raw)
        valid,details=self.validator.validate(summary,mandatory_facts or [])
        if not valid: raise ValueError(f"Unsafe compaction: {details}")
        entry=self.tree.append_entry(conversation_id,"COMPACTION",
            {"summary":summary,"compacted_entry_ids":[e.id for e in old]},
            parent_entry_id=old[0].parent_entry_id,use_active_parent=False)
        parent=entry.id
        for recent in kept:
            cloned=self.tree.append_entry(
                conversation_id,recent.entry_type,recent.payload,turn_id=recent.turn_id,
                parent_entry_id=parent,include_in_context=recent.include_in_context)
            parent=cloned.id
        cid=f"cmp_{uuid.uuid4().hex}"; before=TokenCounter.count_tokens(raw); after=TokenCounter.count_tokens(summary)
        with self.db.get_connection() as conn:
            conn.execute("""INSERT INTO compactions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid,conversation_id,entry.id,old[0].id,kept[0].id,summary,before,after,1,
                 None,json.dumps(details),time.time()))
        return CompactionResult(cid,conversation_id,entry.id,summary,before,after,valid,details)
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
