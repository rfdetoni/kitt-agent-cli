from __future__ import annotations

import json
import re
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


@dataclass(frozen=True)
class LeaseGrant:
    resource_id: str
    owner_id: str
    mode: str
    token: str
    expires_at: float


@dataclass(frozen=True)
class LeaseRequest:
    resource_id: str
    mode: str
    intent: str


@dataclass(frozen=True)
class WorktreeState:
    child_id: str
    path: str
    branch: str
    state: str


class CoordinationConflict(RuntimeError):
    pass


class WorkspaceCoordinator:
    """KITT-native child isolation, mutation fencing and serialized integration."""

    def __init__(
        self,
        execution_root: str,
        state_root: str,
        db: Any,
        workspace_id: str,
        engine: Any | None = None,
    ):
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

    @staticmethod
    def _normalize_resource(resource_id: str) -> str:
        value = str(resource_id or "").strip().replace("\\", "/")
        if not value:
            raise ValueError("coordination resource id is required")
        if value.startswith("path:"):
            raw = value[5:].strip() or "."
            while raw.startswith("./"):
                raw = raw[2:]
            path = PurePosixPath(raw or ".")
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"invalid coordinated path: {raw!r}")
            return f"path:{path.as_posix() or '.'}"
        return value[:1024]

    @staticmethod
    def _resource_overlaps(left: str, right: str) -> bool:
        if left == right:
            return True
        if not left.startswith("path:") or not right.startswith("path:"):
            return False
        left_path = left[5:]
        right_path = right[5:]
        if left_path == "." or right_path == ".":
            return True
        left_parts = PurePosixPath(left_path).parts
        right_parts = PurePosixPath(right_path).parts
        shorter = min(len(left_parts), len(right_parts))
        return left_parts[:shorter] == right_parts[:shorter]

    @classmethod
    def _requests_conflict(
        cls,
        left_resource: str,
        left_mode: str,
        right_resource: str,
        right_mode: str,
    ) -> bool:
        return cls._resource_overlaps(left_resource, right_resource) and (
            left_mode == "WRITE" or right_mode == "WRITE"
        )

    @classmethod
    def _normalize_requests(cls, requests: Iterable[LeaseRequest]) -> list[LeaseRequest]:
        merged: dict[str, LeaseRequest] = {}
        for request in requests:
            resource = cls._normalize_resource(request.resource_id)
            mode = str(request.mode or "").upper()
            if mode not in {"READ", "WRITE"}:
                raise ValueError("lease mode must be READ or WRITE")
            intent = str(request.intent or "").strip()[:500] or "workspace coordination"
            existing = merged.get(resource)
            if existing is None or (existing.mode == "READ" and mode == "WRITE"):
                merged[resource] = LeaseRequest(resource, mode, intent)
        return [merged[key] for key in sorted(merged)]

    def _git(
        self,
        args: list[str],
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.execution_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                proc.stderr.strip()
                or proc.stdout.strip()
                or f"git {' '.join(args)} failed"
            )
        return proc

    def is_git_repository(self) -> bool:
        try:
            return (
                self._git(
                    ["rev-parse", "--is-inside-work-tree"],
                    check=False,
                ).returncode
                == 0
            )
        except OSError:
            return False

    def gc_expired_leases(self) -> int:
        now = time.time()
        with self.db.get_connection() as conn:
            leases = conn.execute(
                "DELETE FROM coordination_leases WHERE workspace_id=? AND expires_at<=?",
                (self.workspace_id, now),
            ).rowcount
            conn.execute(
                "DELETE FROM coordination_wait_queue WHERE workspace_id=? AND expires_at<=?",
                (self.workspace_id, now),
            )
            return int(leases or 0)

    def _older_waiter_blocks(
        self,
        conn,
        ticket_id: str | None,
        queued_at: float | None,
        owner_id: str,
        requests: list[LeaseRequest],
    ) -> bool:
        if ticket_id and queued_at is not None:
            rows = conn.execute(
                """SELECT ticket_id,owner_id,resources_json
                   FROM coordination_wait_queue
                   WHERE workspace_id=?
                     AND (
                       created_at<?
                       OR (created_at=? AND ticket_id<?)
                     )
                   ORDER BY created_at ASC,ticket_id ASC""",
                (self.workspace_id, queued_at, queued_at, ticket_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT ticket_id,owner_id,resources_json
                   FROM coordination_wait_queue
                   WHERE workspace_id=?
                   ORDER BY created_at ASC,ticket_id ASC""",
                (self.workspace_id,),
            ).fetchall()
        for row in rows:
            if str(row[1]) == owner_id:
                continue
            try:
                older = json.loads(str(row[2]))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            for current in requests:
                for item in older if isinstance(older, list) else []:
                    other_resource = self._normalize_resource(item.get("resource_id", ""))
                    other_mode = str(item.get("mode", "")).upper()
                    if other_mode not in {"READ", "WRITE"}:
                        continue
                    if self._requests_conflict(
                        current.resource_id,
                        current.mode,
                        other_resource,
                        other_mode,
                    ):
                        return True
        return False

    def _try_acquire_many(
        self,
        requests: list[LeaseRequest],
        owner_id: str,
        ttl_seconds: float,
        *,
        ticket_id: str | None = None,
        queued_at: float | None = None,
    ) -> tuple[list[LeaseGrant] | None, str | None]:
        now = time.time()
        expires = now + max(5.0, min(float(ttl_seconds), 3600.0))
        with self.db.get_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM coordination_leases WHERE workspace_id=? AND expires_at<=?",
                    (self.workspace_id, now),
                )
                conn.execute(
                    "DELETE FROM coordination_wait_queue WHERE workspace_id=? AND expires_at<=?",
                    (self.workspace_id, now),
                )

                if self._older_waiter_blocks(
                    conn,
                    ticket_id,
                    queued_at,
                    owner_id,
                    requests,
                ):
                    conn.rollback()
                    return None, "an older conflicting mutation is queued"

                rows = conn.execute(
                    """SELECT resource_id,owner_id,mode,intent
                       FROM coordination_leases WHERE workspace_id=?""",
                    (self.workspace_id,),
                ).fetchall()
                for request in requests:
                    for row in rows:
                        other_resource = str(row[0])
                        other_owner = str(row[1])
                        other_mode = str(row[2]).upper()
                        if other_owner == owner_id:
                            continue
                        if self._requests_conflict(
                            request.resource_id,
                            request.mode,
                            other_resource,
                            other_mode,
                        ):
                            conn.rollback()
                            return (
                                None,
                                f"{request.resource_id} conflicts with "
                                f"{other_owner} ({other_mode}: {str(row[3])[:160]})",
                            )

                grants: list[LeaseGrant] = []
                for request in requests:
                    token = secrets.token_urlsafe(18)
                    conn.execute(
                        """INSERT INTO coordination_leases(
                               workspace_id,resource_id,owner_id,mode,intent,
                               lease_token,acquired_at,expires_at
                           ) VALUES(?,?,?,?,?,?,?,?)
                           ON CONFLICT(workspace_id,resource_id,owner_id) DO UPDATE SET
                               mode=excluded.mode,
                               intent=excluded.intent,
                               lease_token=excluded.lease_token,
                               acquired_at=excluded.acquired_at,
                               expires_at=excluded.expires_at""",
                        (
                            self.workspace_id,
                            request.resource_id,
                            owner_id,
                            request.mode,
                            request.intent,
                            token,
                            now,
                            expires,
                        ),
                    )
                    grants.append(
                        LeaseGrant(
                            request.resource_id,
                            owner_id,
                            request.mode,
                            token,
                            expires,
                        )
                    )
                if ticket_id:
                    conn.execute(
                        """DELETE FROM coordination_wait_queue
                           WHERE workspace_id=? AND ticket_id=?""",
                        (self.workspace_id, ticket_id),
                    )
                conn.commit()
                return grants, None
            except Exception:
                conn.rollback()
                raise

    def acquire_many(
        self,
        requests: Iterable[LeaseRequest],
        owner_id: str,
        *,
        ttl_seconds: float = 180.0,
        wait_timeout: float = 0.0,
    ) -> list[LeaseGrant]:
        normalized = self._normalize_requests(requests)
        if not normalized:
            return []
        owner = str(owner_id or "").strip()
        if not owner:
            raise ValueError("coordination owner id is required")

        grants, conflict = self._try_acquire_many(
            normalized,
            owner,
            ttl_seconds,
        )
        if grants is not None:
            return grants
        if wait_timeout <= 0:
            raise CoordinationConflict(conflict or "coordination resource is busy")

        queued_at = time.time()
        deadline = time.monotonic() + max(0.05, min(float(wait_timeout), 30.0))
        ticket_id = f"wait_{secrets.token_urlsafe(12)}"
        resources_json = json.dumps(
            [
                {"resource_id": request.resource_id, "mode": request.mode}
                for request in normalized
            ],
            separators=(",", ":"),
        )
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO coordination_wait_queue(
                       workspace_id,ticket_id,owner_id,resources_json,intent,
                       created_at,expires_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    self.workspace_id,
                    ticket_id,
                    owner,
                    resources_json,
                    "; ".join(request.intent for request in normalized)[:500],
                    queued_at,
                    time.time() + max(5.0, min(float(wait_timeout) + 5.0, 60.0)),
                ),
            )

        delay = 0.05
        last_conflict = conflict
        try:
            while time.monotonic() < deadline:
                grants, last_conflict = self._try_acquire_many(
                    normalized,
                    owner,
                    ttl_seconds,
                    ticket_id=ticket_id,
                    queued_at=queued_at,
                )
                if grants is not None:
                    return grants
                time.sleep(delay)
                delay = min(0.4, delay * 1.7)
        finally:
            with self.db.get_connection() as conn:
                conn.execute(
                    """DELETE FROM coordination_wait_queue
                       WHERE workspace_id=? AND ticket_id=?""",
                    (self.workspace_id, ticket_id),
                )
        raise CoordinationConflict(
            "timed out waiting for mutation resources"
            + (f": {last_conflict}" if last_conflict else "")
        )

    def acquire(
        self,
        resource_id: str,
        owner_id: str,
        mode: str,
        intent: str,
        ttl_seconds: float = 180.0,
        wait_timeout: float = 0.0,
    ) -> LeaseGrant:
        return self.acquire_many(
            [LeaseRequest(resource_id, mode, intent)],
            owner_id,
            ttl_seconds=ttl_seconds,
            wait_timeout=wait_timeout,
        )[0]

    def release(
        self,
        resource_id: str,
        owner_id: str,
        token: str | None = None,
    ) -> int:
        resource = self._normalize_resource(resource_id)
        with self.db.get_connection() as conn:
            if token is None:
                cursor = conn.execute(
                    """DELETE FROM coordination_leases
                       WHERE workspace_id=? AND resource_id=? AND owner_id=?""",
                    (self.workspace_id, resource, owner_id),
                )
            else:
                cursor = conn.execute(
                    """DELETE FROM coordination_leases
                       WHERE workspace_id=? AND resource_id=? AND owner_id=?
                         AND lease_token=?""",
                    (self.workspace_id, resource, owner_id, token),
                )
            return int(cursor.rowcount or 0)

    def refresh_owner(
        self,
        owner_id: str,
        ttl_seconds: float = 180.0,
    ) -> int:
        expires = time.time() + max(5.0, min(float(ttl_seconds), 3600.0))
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                """UPDATE coordination_leases SET expires_at=?
                   WHERE workspace_id=? AND owner_id=?""",
                (expires, self.workspace_id, owner_id),
            )
            return int(cursor.rowcount or 0)

    def release_owner(self, owner_id: str) -> int:
        with self.db.get_connection() as conn:
            leases = conn.execute(
                "DELETE FROM coordination_leases WHERE workspace_id=? AND owner_id=?",
                (self.workspace_id, owner_id),
            ).rowcount
            conn.execute(
                "DELETE FROM coordination_wait_queue WHERE workspace_id=? AND owner_id=?",
                (self.workspace_id, owner_id),
            )
            return int(leases or 0)

    def claim_symbol_for_edit(
        self,
        symbol_id: str,
        owner_id: str,
        intent: str,
        dependency_read_leases: bool = True,
        wait_timeout: float = 8.0,
    ) -> list[LeaseGrant]:
        requests = [LeaseRequest(f"symbol:{symbol_id}", "WRITE", intent)]
        if self.engine is not None:
            try:
                found = self.engine.read_symbol(symbol_id)
            except Exception:
                found = None
            if found and isinstance(found, dict):
                symbol = found.get("symbol") or {}
                path = str(symbol.get("path") or "").strip()
                if path:
                    requests.append(
                        LeaseRequest(f"path:{path}", "WRITE", intent)
                    )
        if dependency_read_leases and self.engine is not None:
            try:
                deps = self.engine.dependency_edges(max_symbols=20000).get(
                    symbol_id,
                    [],
                )
            except Exception:
                deps = []
            requests.extend(
                LeaseRequest(
                    f"symbol:{dep}",
                    "READ",
                    f"dependency of {symbol_id}",
                )
                for dep in deps[:64]
            )
        return self.acquire_many(
            requests,
            owner_id,
            wait_timeout=wait_timeout,
        )

    def claim_paths(
        self,
        paths: Iterable[str],
        owner_id: str,
        intent: str,
        *,
        wait_timeout: float = 8.0,
    ) -> list[LeaseGrant]:
        requests = [
            LeaseRequest(f"path:{Path(path).as_posix()}", "WRITE", intent)
            for path in sorted({str(path).strip() for path in paths if str(path).strip()})
        ]
        return self.acquire_many(
            requests,
            owner_id,
            wait_timeout=wait_timeout,
        )

    def prepare_isolated_workspace(
        self,
        owner_id: str,
        *,
        namespace: str = "child",
        base_ref: str = "HEAD",
    ) -> WorktreeState:
        owner = str(owner_id or "").strip()
        if not owner:
            raise ValueError("isolated workspace owner is required")
        safe = self._safe_id(owner)
        ns = self._safe_id(namespace or "work")
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        path = (
            self.worktree_root / safe
            if ns == "child"
            else self.worktree_root / f"{ns}-{safe}"
        )
        branch = (
            f"kitt/child/{safe}"
            if ns == "child"
            else f"kitt/{ns}/{safe}"
        )
        if not self.is_git_repository():
            return WorktreeState(
                owner,
                str(self.execution_root),
                "",
                "SHARED_FALLBACK",
            )
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT path,branch,state FROM child_worktrees WHERE child_id=?",
                (owner,),
            ).fetchone()
            if row and Path(row[0]).exists():
                return WorktreeState(
                    owner,
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                )
        if path.exists():
            raise RuntimeError(f"stale worktree path exists: {path}")
        branch_exists = (
            self._git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                check=False,
            ).returncode
            == 0
        )
        if branch_exists:
            self._git(["worktree", "add", "--", str(path), branch])
        else:
            self._git(
                ["worktree", "add", "-b", branch, "--", str(path), str(base_ref)]
            )
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO child_worktrees(
                       child_id,workspace_id,path,branch,base_ref,state,
                       created_at,updated_at,last_error
                   ) VALUES(?,?,?,?,?,?,?,?,NULL)""",
                (
                    owner,
                    self.workspace_id,
                    str(path),
                    branch,
                    str(base_ref),
                    "READY",
                    now,
                    now,
                ),
            )
        return WorktreeState(owner, str(path), branch, "READY")

    def discard_isolated_workspace(
        self,
        owner_id: str,
        *,
        delete_branch: bool = True,
    ) -> None:
        owner = str(owner_id or "").strip()
        if not owner:
            return
        self.release_owner(owner)
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT path,branch FROM child_worktrees WHERE child_id=?",
                (owner,),
            ).fetchone()
        if not row:
            return
        self._cleanup(
            owner,
            Path(row[0]),
            str(row[1]),
            delete_branch=bool(delete_branch),
        )

    def prepare_child(self, child_id: str) -> WorktreeState:
        return self.prepare_isolated_workspace(
            child_id,
            namespace="child",
            base_ref="HEAD",
        )

    def mark_running(self, child_id: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """UPDATE child_worktrees SET state='RUNNING',updated_at=?
                   WHERE child_id=?""",
                (time.time(), child_id),
            )

    def _main_branch(self) -> str:
        return self._git(["branch", "--show-current"]).stdout.strip() or "HEAD"

    def integrate_child(
        self,
        child_id: str,
        allowed_paths: Iterable[str] | None = None,
    ) -> WorktreeState:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT path,branch,state FROM child_worktrees WHERE child_id=?",
                (child_id,),
            ).fetchone()
        if not row:
            return WorktreeState(
                child_id,
                str(self.execution_root),
                "",
                "NO_WORKTREE",
            )
        path, branch = Path(row[0]), str(row[1])
        if not path.exists():
            raise RuntimeError(f"child worktree missing: {path}")
        status = self._git(["status", "--porcelain"], cwd=path).stdout
        if not status.strip():
            self.release_owner(child_id)
            self._cleanup(child_id, path, branch, delete_branch=True)
            return WorktreeState(child_id, str(path), branch, "CLEAN")

        scopes = [
            Path(value).as_posix()
            for value in (allowed_paths or [])
            if str(value).strip()
        ]
        if scopes:
            changed = self._git(
                ["status", "--porcelain", "-z"],
                cwd=path,
            ).stdout.split("\0")
            for record in changed:
                if not record:
                    continue
                rel = record[3:] if len(record) >= 4 else record
                rel = rel.split(" -> ")[-1].strip()
                candidate = Path(rel)
                if not any(
                    candidate == Path(scope) or Path(scope) in candidate.parents
                    for scope in scopes
                ):
                    self._record_error(
                        child_id,
                        f"out-of-scope child change: {rel}",
                    )
                    raise CoordinationConflict(
                        f"child changed out-of-scope path: {rel}"
                    )
            self._git(["add", "--all", "--", *scopes], cwd=path)
        else:
            self._git(["add", "--all", "--", ":/"], cwd=path)

        commit = self._git(
            [
                "-c",
                "user.name=KITT",
                "-c",
                "user.email=kitt@local.invalid",
                "commit",
                "-m",
                f"kitt: integrate child {self._safe_id(child_id)}",
            ],
            cwd=path,
            check=False,
        )
        if commit.returncode != 0 and "nothing to commit" not in (
            commit.stderr + commit.stdout
        ).casefold():
            self._record_error(child_id, commit.stderr or commit.stdout)
            raise RuntimeError(commit.stderr.strip() or "child commit failed")

        merge_resource = "workspace:integration"
        merge_grant: LeaseGrant | None = None
        try:
            merge_grant = self.acquire(
                merge_resource,
                child_id,
                "WRITE",
                f"integrate child {child_id}",
                ttl_seconds=300,
                wait_timeout=15.0,
            )
            with self._merge_lock:
                dirty = self._git(
                    ["status", "--porcelain", "--untracked-files=no"]
                ).stdout.strip()
                if dirty:
                    self._record_error(
                        child_id,
                        "main worktree has tracked uncommitted changes",
                    )
                    raise CoordinationConflict(
                        "main worktree is dirty; child branch preserved for explicit recovery"
                    )
                base = self._main_branch()
                rebase = self._git(
                    ["rebase", base],
                    cwd=path,
                    check=False,
                )
                if rebase.returncode != 0:
                    self._git(["rebase", "--abort"], cwd=path, check=False)
                    self._record_error(
                        child_id,
                        rebase.stderr or rebase.stdout,
                    )
                    raise CoordinationConflict(
                        "child rebase conflict; branch and worktree preserved"
                    )
                merge = self._git(
                    [
                        "merge",
                        "--no-ff",
                        branch,
                        "-m",
                        f"kitt: merge child {self._safe_id(child_id)}",
                    ],
                    check=False,
                )
                if merge.returncode != 0:
                    self._git(["merge", "--abort"], check=False)
                    self._record_error(
                        child_id,
                        merge.stderr or merge.stdout,
                    )
                    raise CoordinationConflict(
                        "child merge conflict; branch and worktree preserved"
                    )
        finally:
            if merge_grant is not None:
                self.release(
                    merge_resource,
                    child_id,
                    merge_grant.token,
                )

        self.release_owner(child_id)
        self._cleanup(child_id, path, branch, delete_branch=True)
        return WorktreeState(child_id, str(path), branch, "MERGED")

    def _record_error(self, child_id: str, error: str) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """UPDATE child_worktrees
                   SET state='CONFLICT',last_error=?,updated_at=?
                   WHERE child_id=?""",
                (error[-4000:], time.time(), child_id),
            )

    def _cleanup(
        self,
        child_id: str,
        path: Path,
        branch: str,
        delete_branch: bool,
    ) -> None:
        self._git(
            ["worktree", "remove", "--force", "--", str(path)],
            check=False,
        )
        if delete_branch and branch:
            self._git(["branch", "-D", "--", branch], check=False)
        with self.db.get_connection() as conn:
            conn.execute(
                """UPDATE child_worktrees SET state='CLOSED',updated_at=?
                   WHERE child_id=?""",
                (time.time(), child_id),
            )

    def abandon_child(
        self,
        child_id: str,
        preserve_worktree: bool = True,
    ) -> None:
        self.release_owner(child_id)
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT path,branch FROM child_worktrees WHERE child_id=?",
                (child_id,),
            ).fetchone()
        if not row:
            return
        if preserve_worktree:
            with self.db.get_connection() as conn:
                conn.execute(
                    """UPDATE child_worktrees SET state='PRESERVED',updated_at=?
                       WHERE child_id=?""",
                    (time.time(), child_id),
                )
        else:
            self._cleanup(
                child_id,
                Path(row[0]),
                str(row[1]),
                delete_branch=False,
            )
