"""Task-isolated Git worktrees for the autonomous root principal."""
from pathlib import Path
import re
import uuid

from kitt.runtime.autopilot_publish import _git

KEY = "autopilot.transaction"
PREFIX = ".autopilot-tasks/"
PATTERN = re.compile(r"\.autopilot-tasks/[0-9a-f]{32}\Z")


def clean(root):
    return not _git(root, "status", "--porcelain", "--untracked-files=all",
                    "--", ".", ":(exclude).autopilot-tasks").strip()


class TaskWorktree:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = Path(runtime.root).resolve()

    def current(self):
        if self.runtime.state is None:
            return None
        info = self.runtime.state.get(KEY)
        if info is None:
            return None
        if not isinstance(info, dict) or not PATTERN.fullmatch(str(info.get("path"))):
            raise PermissionError("invalid transaction record")
        task = self.root / info["path"]
        if task.is_symlink() or not task.is_dir():
            raise PermissionError("transaction workspace missing or unsafe")
        if _git(task, "rev-parse", "HEAD").decode().strip() != info["head"]:
            raise PermissionError("transaction revision drift")
        return info

    def directory(self):
        info = self.current()
        return self.root / info["path"] if info else self.root

    def start(self, args):
        if args or self.current():
            raise ValueError("transaction arguments invalid or transaction already active")
        if not clean(self.root):
            raise PermissionError("base checkout has uncommitted changes")
        head = _git(self.root, "rev-parse", "HEAD").decode().strip()
        home = self.root / PREFIX.rstrip("/")
        if home.is_symlink():
            raise PermissionError("task directory is a symlink")
        home.mkdir(exist_ok=True, mode=0o700)
        rel = PREFIX + uuid.uuid4().hex
        _git(self.root, "worktree", "add", "--detach", str(self.root / rel), head)
        info = {"path": rel, "head": head}
        self.runtime.state.set(KEY, info)
        self.runtime.state.delete("autopilot.task.verification")
        return info

    def status(self, args):
        if args:
            raise ValueError("transaction.status takes no arguments")
        info = self.current()
        if info is None:
            return {"active": False}
        dirty = bool(_git(self.directory(), "status", "--porcelain").strip())
        return {"active": True, "path": info["path"], "modified": dirty}

    def abandon(self, args):
        """Rollback by dropping the active pointer; retain files for audit."""
        if args:
            raise ValueError("transaction.abandon takes no arguments")
        info = self.current()
        if info is None:
            raise ValueError("no active transaction")
        self.runtime.state.delete(KEY)
        self.runtime.state.delete("autopilot.task.verification")
        return {"abandoned": True, "retained_path": info["path"]}
