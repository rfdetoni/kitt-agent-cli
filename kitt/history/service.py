from typing import List, Dict, Any, Optional
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository

class HistoryService:
    """Service providing workspace history commands, resume, fork, and summary operations."""

    def __init__(self, root_dir: str = "."):
        self.db = HistoryDatabase(root_dir=root_dir)
        self.repo = HistoryRepository(self.db)
        self.workspace = self.repo.get_or_create_workspace(root_dir)
        self.active_conversation: Optional[Dict[str, Any]] = None

    def new_conversation(self, title: str = "New Conversation") -> Dict[str, Any]:
        conv = self.repo.create_conversation(self.workspace["id"], title=title)
        self.active_conversation = conv
        return conv

    def get_or_create_active(self) -> Dict[str, Any]:
        if not self.active_conversation:
            convs = self.repo.list_conversations(self.workspace["id"], limit=1)
            if convs:
                self.active_conversation = convs[0]
            else:
                self.active_conversation = self.new_conversation()
        return self.active_conversation

    def list_history(self, limit: int = 20, search: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.repo.list_conversations(self.workspace["id"], limit=limit, search=search)

    def resume_conversation(self, conv_id_or_index: str) -> Optional[Dict[str, Any]]:
        convs = self.list_history(limit=50)
        # Check by numeric index or ID
        if conv_id_or_index.isdigit():
            idx = int(conv_id_or_index) - 1
            if 0 <= idx < len(convs):
                self.active_conversation = convs[idx]
                return self.active_conversation
        for c in convs:
            if c["id"].startswith(conv_id_or_index):
                self.active_conversation = c
                return c
        return None

    def fork_conversation(self, title_suffix: str = " (Fork)") -> Dict[str, Any]:
        active = self.get_or_create_active()
        new_title = f"{active['title']}{title_suffix}"
        forked = self.repo.create_conversation(self.workspace["id"], title=new_title, parent_id=active["id"])
        msgs = self.repo.get_messages_for_conversation(active["id"])
        for m in msgs:
            self.repo.save_message(forked["id"], m["turn_id"], m["role"], m["content"], m["token_count"])
        self.active_conversation = forked
        return forked

    def export_conversation(self, conv_id: Optional[str] = None, fmt: str = "md") -> str:
        target_id = conv_id or (self.active_conversation["id"] if self.active_conversation else None)
        if not target_id:
            return ""
        conv = self.repo.get_conversation(target_id)
        msgs = self.repo.get_messages_for_conversation(target_id)
        if fmt == "json":
            import json
            return json.dumps({"conversation": conv, "messages": msgs}, indent=2)
        
        lines = [f"# Conversation Export: {conv['title']}", f"Date: {conv['created_at']}\n"]
        for m in msgs:
            lines.append(f"### {m['role'].capitalize()}\n{m['content']}\n")
        return "\n".join(lines)
