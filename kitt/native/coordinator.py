from __future__ import annotations

import os
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class LeaseGrant:
    resource_id: str
    owner_id: str
    mode: str
    token: str
    expires_at: float


@dataclass(frozen=True)
class WorktreeState:
    child_id: str
    path: str
    branch: str
    state: str


class CoordinationConflict(RuntimeError):
    pass


class WorkspaceCoordinator:
    """KITT-native child isolation, leases and serialized integration."""

    def __init__(self, execution_root: str, state_root: str, db: Any, workspace_id: str,
                 engine: Any | None = None):
        self.execution_root = Path(execution_root).resolve()
        self.state_root = Path(state_root).resolve()
        self.db = db
        self.workspace_id = workspace_id
        self.engine = engine
        self._merge_lock = threading.RLock()
        self.worktree_root = self.state_root / ".kitt" / "worktrees"

    @staticmethod
    def _safe_id(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")[:80] or "child"

    def _git(self, args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd or self.execution_root), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
        return proc

    def is_git_repository(self) -> bool:
        try:
            return self._git(["rev-parse", "--is-inside-work-tree"], check=False).returncode == 0
        except OSError:
            return False

    def gc_expired_leases(self) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM coordination_leases WHERE workspace_id=? AND expires_at<=?",
                (self.workspace_id, time.time()),
            )
            return int(cursor.rowcount or 0)

    def acquire(self, resource_id: str, owner_id: str, mode: str, intent: str,
                ttl_seconds: float = 180.0) -> LeaseGrant:
        mode = mode.upper()
        if mode not in {"READ", "WRITE"}:
            raise ValueError("lease mode must be READ or WRITE")
        now = time.time(); expires = now + max(5.0, min(float(ttl_seconds), 3600.0))
        token = secrets.token_urlsafe(18)
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM coordination_leases WHERE workspace_id=? AND expires_at<=?", (self.workspace_id, now))
            rows = conn.execute(
                "SELECT owner_id,mode,intent FROM coordination_leases WHERE workspace_id=? AND resource_id=?",
                (self.workspace_id, resource_id),
            ).fetchall()
            for row in rows:
                other_owner, other_mode, other_intent = str(row[0]), str(row[1]), str(row[2])
                if other_owner == owner_id:
                    continue
                if mode == "WRITE" or other_mode == "WRITE":
                    raise CoordinationConflict(
                        f"resource {resource_id} is leased by {other_owner} ({other_mode}: {other_intent})"
                    )
            conn.execute(
                """INSERT INTO coordination_leases(workspace_id,resource_id,owner_id,mode,intent,lease_token,acquired_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,resource_id,owner_id) DO UPDATE SET
                   mode=excluded.mode,intent=excluded.intent,lease_token=excluded.lease_token,
                   acquired_at=excluded.acquired_at,expires_at=excluded.expires_at""",
                (self.workspace_id, resource_id, owner_id, mode, intent[:500], token, now, expires),
            )
        return LeaseGrant(resource_id, owner_id, mode, token, expires)

    def refresh_owner(self, owner_id: str, ttl_seconds: float = 180.0) -> int:
        expires = time.time() + max(5.0, min(float(ttl_seconds), 3600.0))
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE coordination_leases SET expires_at=? WHERE workspace_id=? AND owner_id=?",
                (expires, self.workspace_id, owner_id),
            )
            return int(cursor.rowcount or 0)

    def release_owner(self, owner_id: str) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM coordination_leases WHERE workspace_id=? AND owner_id=?",
                (self.workspace_id, owner_id),
            )
            return int(cursor.rowcount or 0)

    def claim_symbol_for_edit(self, symbol_id: str, owner_id: str, intent: str,
                              dependency_read_leases: bool = True) -> list[LeaseGrant]:
        grants = [self.acquire(f"symbol:{symbol_id}", owner_id, "WRITE", intent)]
        if dependency_read_leases and self.engine is not None:
            try:
                deps = self.engine.dependency_edges(max_symbols=20000).get(symbol_id, [])
            except Exception:
                deps = []
            for dep in deps[:64]:
                grants.append(self.acquire(f"symbol:{dep}", owner_id, "READ", f"dependency of {symbol_id}"))
        return grants

    def claim_paths(self, paths: Iterable[str], owner_id: str, intent: str) -> list[LeaseGrant]:
        return [self.acquire(f"path:{Path(path).as_posix()}", owner_id, "WRITE", intent) for path in sorted(set(paths))]

    def prepare_child(self, child_id: str) -> WorktreeState:
        safe = self._safe_id(child_id)
        path = self.worktree_root / safe
        branch = f"kitt/child/{safe}"
        if not self.is_git_repository():
            return WorktreeState(child_id, str(self.execution_root), "", "SHARED_FALLBACK")
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT path,branch,state FROM child_worktrees WHERE child_id=?", (child_id,)).fetchone()
            if row and Path(row[0]).exists():
                return WorktreeState(child_id, str(row[0]), str(row[1]), str(row[2]))
        if path.exists():
            raise RuntimeError(f"stale worktree path exists: {path}")
        branch_exists = self._git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False).returncode == 0
        if branch_exists:
            self._git(["worktree", "add", "--", str(path), branch])
        else:
            self._git(["worktree", "add", "-b", branch, "--", str(path), "HEAD"])
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO child_worktrees(child_id,workspace_id,path,branch,base_ref,state,created_at,updated_at,last_error)
                   VALUES(?,?,?,?,?,?,?,?,NULL)""",
                (child_id, self.workspace_id, str(path), branch, "HEAD", "READY", now, now),
            )
        return WorktreeState(child_id, str(path), branch, "READY")

    def mark_running(self, child_id: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute("UPDATE child_worktrees SET state='RUNNING',updated_at=? WHERE child_id=?", (time.time(), child_id))

    def _main_branch(self) -> str:
        return self._git(["branch", "--show-current"]).stdout.strip() or "HEAD"

    def integrate_child(self, child_id: str) -> WorktreeState:
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT path,branch,state FROM child_worktrees WHERE child_id=?", (child_id,)).fetchone()
        if not row:
            return WorktreeState(child_id, str(self.execution_root), "", "NO_WORKTREE")
        path, branch = Path(row[0]), str(row[1])
        if not path.exists():
            raise RuntimeError(f"child worktree missing: {path}")
        status = self._git(["status", "--porcelain"], cwd=path).stdout
        if not status.strip():
            self._cleanup(child_id, path, branch, delete_branch=True)
            return WorktreeState(child_id, str(path), branch, "CLEAN")
        self._git(["add", "--all", "--", ":/"], cwd=path)
        commit = self._git(["commit", "-m", f"kitt: integrate child {self._safe_id(child_id)}"], cwd=path, check=False)
        if commit.returncode != 0 and "nothing to commit" not in (commit.stderr + commit.stdout).casefold():
            self._record_error(child_id, commit.stderr or commit.stdout)
            raise RuntimeError(commit.stderr.strip() or "child commit failed")

        with self._merge_lock:
            # Main tracked edits could be overwritten or mixed with child work. Preserve child branch instead.
            dirty = self._git(["status", "--porcelain", "--untracked-files=no"]).stdout.strip()
            if dirty:
                self._record_error(child_id, "main worktree has tracked uncommitted changes")
                raise CoordinationConflict("main worktree is dirty; child branch preserved for explicit recovery")
            base = self._main_branch()
            rebase = self._git(["rebase", base], cwd=path, check=False)
            if rebase.returncode != 0:
                self._git(["rebase", "--abort"], cwd=path, check=False)
                self._record_error(child_id, rebase.stderr or rebase.stdout)
                raise CoordinationConflict("child rebase conflict; branch and worktree preserved")
            merge = self._git(["merge", "--no-ff", branch, "-m", f"kitt: merge child {self._safe_id(child_id)}"], check=False)
            if merge.returncode != 0:
                self._git(["merge", "--abort"], check=False)
                self._record_error(child_id, merge.stderr or merge.stdout)
                raise CoordinationConflict("child merge conflict; branch and worktree preserved")

        self.release_owner(child_id)
        self._cleanup(child_id, path, branch, delete_branch=True)
        return WorktreeState(child_id, str(path), branch, "MERGED")

    def _record_error(self, child_id: str, error: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE child_worktrees SET state='CONFLICT',last_error=?,updated_at=? WHERE child_id=?",
                (error[-4000:], time.time(), child_id),
            )

    def _cleanup(self, child_id: str, path: Path, branch: str, delete_branch: bool) -> None:
        self._git(["worktree", "remove", "--force", "--", str(path)], check=False)
        if delete_branch and branch:
            self._git(["branch", "-D", "--", branch], check=False)
        with self.db.get_connection() as conn:
            conn.execute("UPDATE child_worktrees SET state='CLOSED',updated_at=? WHERE child_id=?", (time.time(), child_id))

    def abandon_child(self, child_id: str, preserve_worktree: bool = True) -> None:
        self.release_owner(child_id)
        with self.db.get_connection() as conn:
            row = conn.execute("SELECT path,branch FROM child_worktrees WHERE child_id=?", (child_id,)).fetchone()
        if not row:
            return
        if preserve_worktree:
            with self.db.get_connection() as conn:
                conn.execute("UPDATE child_worktrees SET state='PRESERVED',updated_at=? WHERE child_id=?", (time.time(), child_id))
        else:
            self._cleanup(child_id, Path(row[0]), str(row[1]), delete_branch=False)
