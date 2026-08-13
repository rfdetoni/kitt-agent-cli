from __future__ import annotations

from typing import List, Dict, Any, Optional

from kitt.core.workspace_identity import WorkspaceIdentity
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity


class HistoryService:
    """Service providing workspace history commands, resume, fork, and summary operations.

    ``workspace`` is a ``WorkspaceIdentity`` whose ``id`` is the persisted
    ``workspaces.id`` used as the single foreign-key value across the runtime.
    """

    def __init__(self, root_dir: str = ".", db: Optional[HistoryDatabase] = None,
                 repo: Optional[HistoryRepository] = None, tree: Optional[Any] = None,
                 enabled: bool = True, identity: Optional[WorkspaceIdentity] = None,
                 persistence_enabled: bool = True):
        self.db = db or HistoryDatabase(root_dir=root_dir)
        self.repo = repo or HistoryRepository(self.db)
        from kitt.history.session_tree import SessionTreeRepository
        self.tree = tree or SessionTreeRepository(self.db)
        self.enabled = enabled
        self.persistence_enabled = persistence_enabled
        if identity is not None:
            self.workspace = identity
        else:
            ident = resolve_workspace_identity(self.db, root_dir)
            self.workspace = ident
        self.active_conversation: Optional[Dict[str, Any]] = None

    @property
    def workspace_id(self) -> str:
        return self.workspace.id

    def new_conversation(self, title: str = "New Conversation") -> Dict[str, Any]:
        conv = self.repo.create_conversation(self.workspace_id, title=title)
        self.active_conversation = conv
        return conv

    def get_or_create_active(self) -> Dict[str, Any]:
        if not self.active_conversation:
            convs = self.repo.list_conversations(self.workspace_id, limit=1)
            if convs:
                self.active_conversation = convs[0]
            else:
                self.active_conversation = self.new_conversation()
        return self.active_conversation

    def get_active_read_only(self) -> Optional[Dict[str, Any]]:
        if self.active_conversation:
            return self.active_conversation
        convs = self.repo.list_conversations(self.workspace_id, limit=1)
        return convs[0] if convs else None

    def list_history(self, limit: int = 20, offset: int = 0, search: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.repo.list_conversations(self.workspace_id, limit=limit, offset=offset, search=search)

    def list_turns(self, conversation_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 100))
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, ordinal, started_at FROM turns WHERE conversation_id = ? ORDER BY ordinal ASC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

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
        """Fork the active conversation copying the active path exactly once.

        The repository clones the materialized messages; the session tree is
        cloned atomically and its active cursor moved to the new leaf.  A
        failure in either step raises and leaves no partial fork behind.
        """
        active = self.get_or_create_active()
        new_title = f"{active['title']}{title_suffix}"
        forked = self.repo.fork_conversation(active["id"], new_title=new_title)
        if not forked:
            raise ValueError("Active conversation no longer exists")
        try:
            self.tree.clone_active_path(active["id"], forked["id"])
        except Exception:
            # Avoid leaving a half-created fork: remove the cloned conversation.
            try:
                self.repo.delete_conversation(forked["id"])
            except Exception:
                pass
            raise
        self.active_conversation = forked
        return forked

    def close(self) -> None:
        self.db.close()

    def export_conversation(self, conv_id: Optional[str] = None, fmt: str = "md",
                            include_tree: bool = False) -> str:
        target_id = conv_id or (self.active_conversation["id"] if self.active_conversation else None)
        if not target_id:
            return ""
        conv = self.repo.get_conversation(target_id)
        msgs = self.repo.get_messages_for_conversation(target_id)
        if fmt == "json":
            import json
            payload: Dict[str, Any] = {"conversation": conv, "messages": msgs}
            if include_tree:
                payload["session_tree"] = [
                    {
                        "id": e.id, "parent_entry_id": e.parent_entry_id, "turn_id": e.turn_id,
                        "entry_type": e.entry_type, "payload": e.payload,
                        "include_in_context": e.include_in_context, "generation": e.generation,
                    }
                    for e in self.tree.get_active_path(target_id)
                ]
            return json.dumps(payload, indent=2)

        lines = [f"# Conversation Export: {conv['title']}", f"Date: {conv['created_at']}\n"]
        for m in msgs:
            lines.append(f"### {m['role'].capitalize()}\n{m['content']}\n")
        return "\n".join(lines)
