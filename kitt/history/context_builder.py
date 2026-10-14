"""Bounded provider context derived from durable session-tree entries.

The complete original messages always remain in SQLite. Large prompts cannot
be replayed in full under the 1200-token context budget, so preserve both
their beginning and end instead of silently skipping them altogether.
"""
from kitt.context_filter.prompt_budget import TokenCounter
from kitt.history.redaction import redact
from kitt.history.session_tree import SessionTreeRepository


class HistoryContextBuilder:
    def __init__(self, tree: SessionTreeRepository):
        self.tree = tree

    @staticmethod
    def _bounded(content: str, max_tokens: int) -> str:
        if TokenCounter.count_tokens(content) <= max_tokens:
            return content
        # Character bounds are deliberately conservative; token counter is
        # the authority below, including Unicode/non-Latin content.
        chars = max(64, max_tokens * 2)
        marker = "\n[rest of this message retained in persistent conversation history]\n"
        head = chars // 2
        tail = chars - head
        excerpt = content[:head] + marker + content[-tail:]
        while TokenCounter.count_tokens(excerpt) > max_tokens and chars > 64:
            chars = max(64, chars * 3 // 4)
            head, tail = chars // 2, chars - chars // 2
            excerpt = content[:head] + marker + content[-tail:]
        return excerpt

    def build(self, conversation_id: str, max_tokens: int = 1200) -> str:
        if max_tokens <= 0:
            return ""
        lines: list[str] = []
        for entry in reversed(self.tree.get_active_path(conversation_id)):
            if not entry.include_in_context:
                continue
            payload = entry.payload
            content = payload.get("content") or payload.get("summary") or payload.get("text")
            if not content:
                continue
            role = payload.get("role", entry.entry_type.lower())
            remaining = max_tokens - TokenCounter.count_tokens("\n".join(reversed(lines)))
            if remaining <= 16:
                break
            candidate = f"{role}: {redact(str(content))}"
            excerpt = self._bounded(candidate, remaining - 8)
            if TokenCounter.count_tokens("\n".join(reversed(lines + [excerpt]))) <= max_tokens:
                lines.append(excerpt)
        return "\n".join(reversed(lines))
