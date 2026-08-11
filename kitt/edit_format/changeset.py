import time
import uuid
from pathlib import Path
from typing import List, Optional
from kitt.domain.entities import FileSnapshot, ChangeSet

class ChangeSetTracker:
    """Manages transactional file edit snapshots and safe undo operations."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = Path(root_dir).resolve()
        self.history: List[ChangeSet] = []

    def create_snapshot(self, relative_path: str) -> FileSnapshot:
        full_path = (self.root_dir / relative_path).resolve()
        if full_path.exists() and full_path.is_file():
            try:
                content = full_path.read_text(encoding='utf-8', errors='ignore')
                return FileSnapshot(relative_path=relative_path, existed=True, content=content)
            except Exception:
                return FileSnapshot(relative_path=relative_path, existed=True, content=None)
        return FileSnapshot(relative_path=relative_path, existed=False, content=None)

    def record_changeset(self, description: str, snapshots: List[FileSnapshot]) -> ChangeSet:
        cs = ChangeSet(
            id=str(uuid.uuid4())[:8],
            timestamp=time.time(),
            description=description,
            snapshots=snapshots
        )
        self.history.append(cs)
        return cs

    def revert_last_changeset(self) -> Optional[ChangeSet]:
        if not self.history:
            return None

        cs = self.history.pop()
        for snap in cs.snapshots:
            full_path = (self.root_dir / snap.relative_path).resolve()
            if not snap.existed:
                # File was created in this changeset; delete it safely
                if full_path.exists():
                    full_path.unlink()
            else:
                # Restore previous content
                if snap.content is not None:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(snap.content, encoding='utf-8')

        return cs
