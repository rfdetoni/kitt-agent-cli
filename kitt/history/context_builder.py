from typing import List
from kitt.context_filter.prompt_budget import TokenCounter
from kitt.history.redaction import redact
from kitt.history.session_tree import SessionTreeRepository

class HistoryContextBuilder:
    def __init__(self, tree: SessionTreeRepository):
        self.tree=tree
    def build(self, conversation_id: str, max_tokens: int=1200) -> str:
        lines: List[str]=[]
        for entry in reversed(self.tree.get_active_path(conversation_id)):
            if not entry.include_in_context: continue
            payload=entry.payload
            content=payload.get("content") or payload.get("summary") or payload.get("text")
            if not content: continue
            role=payload.get("role",entry.entry_type.lower())
            candidate=f"{role}: {redact(str(content))}"
            if TokenCounter.count_tokens("\n".join(reversed(lines+[candidate]))) > max_tokens:
                continue
            lines.append(candidate)
        return "\n".join(reversed(lines))
