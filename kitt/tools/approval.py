from __future__ import annotations

import hashlib
import hmac
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Literal, Optional, Set


@dataclass(frozen=True)
class RememberedRule:
    tool_name: str
    path_glob: str | None
    decision: Literal["allow", "deny"]
    scope: Literal["session", "workspace"]
    created_at: float
    conversation_id: str | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    turn_id: str
    conversation_id: str
    workspace_id: str
    tool_name: str
    normalized_args_hash: str
    created_at: float
    expires_at: float
    summary: str
    state: str = "PENDING"


@dataclass(frozen=True)
class ApprovalGrant:
    approval_id: str
    turn_id: str
    conversation_id: str
    workspace_id: str
    action_hash: str
    granted_at: float
    expires_at: float
    nonce: str


class ApprovalManager:
    """Single-use approval broker with nonce-bound CAS consumption."""

    def __init__(self, ttl_seconds: float = 300.0, db=None):
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self.used_nonces: Set[str] = set()
        self.used_grants: Set[str] = set()
        self._requests_by_id: dict[str, ApprovalRequest] = {}
        self._requests_by_binding: dict[tuple, ApprovalRequest] = {}
        self._issued_nonce_hashes: dict[str, str] = {}
        self.db = db
        self.remembered_rules: list[RememberedRule] = []
        self._lock = threading.RLock()
        self._load_remembered_rules()

    @property
    def _requests(self) -> dict:
        with self._lock:
            merged = dict(self._requests_by_id)
            merged.update(self._requests_by_binding)
            return merged

    def _load_remembered_rules(self) -> None:
        # HistoryDatabase is private state after the full hardening package.
        if not self.db:
            return
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT tool_name, path_glob, decision, created_at "
                    "FROM remembered_approval_rules ORDER BY created_at ASC"
                ).fetchall()
            with self._lock:
                for row in rows:
                    decision = row[2]
                    if decision not in {"allow", "deny"}:
                        continue
                    self.remembered_rules.append(
                        RememberedRule(row[0], row[1], decision, "workspace", row[3], None)
                    )
        except Exception:
            pass

    def remember(
        self,
        tool_name: str,
        path_glob: str | None,
        decision: str,
        scope: str = "workspace",
        conversation_id: str | None = None,
    ) -> None:
        if decision not in {"allow", "deny"}:
            raise ValueError("Approval decision must be allow or deny")
        if scope not in {"session", "workspace"}:
            raise ValueError("Approval scope must be session or workspace")
        conv = str(conversation_id or "").strip() or None
        if scope == "session" and conv is None:
            raise ValueError("Session-scoped approval requires conversation_id")
        if scope == "workspace":
            conv = None
        rule = RememberedRule(tool_name, path_glob, decision, scope, time.time(), conv)

        # Workspace rules are durable authority. Persist first so a failed DB
        # write cannot create an in-memory permission that differs after restart.
        if scope == "workspace" and self.db:
            with self.db.get_connection() as conn:
                conn.execute(
                    "INSERT INTO remembered_approval_rules"
                    "(tool_name,path_glob,decision,created_at) VALUES(?,?,?,?)",
                    (tool_name, path_glob, decision, rule.created_at),
                )
        with self._lock:
            self.remembered_rules.append(rule)

    def clear_remembered(
        self,
        *,
        scope: str | None = None,
        conversation_id: str | None = None,
    ) -> int:
        if scope == "all":
            scope = None
        if scope not in {None, "session", "workspace"}:
            raise ValueError("Invalid remembered approval scope")
        conv = str(conversation_id or "").strip() or None
        if scope == "session" and conv is None:
            raise ValueError("Session-scoped clear requires conversation_id")

        # Delete durable authority before mutating the in-memory mirror. A DB
        # failure therefore fails closed and leaves the effective policy intact.
        if self.db and scope in {None, "workspace"}:
            with self.db.get_connection() as conn:
                conn.execute("DELETE FROM remembered_approval_rules")

        def matches(rule: RememberedRule) -> bool:
            if scope is None:
                return True
            if scope == "workspace":
                return rule.scope == "workspace"
            return rule.scope == "session" and rule.conversation_id == conv

        with self._lock:
            before = len(self.remembered_rules)
            self.remembered_rules = [
                rule for rule in self.remembered_rules if not matches(rule)
            ]
            return before - len(self.remembered_rules)

    def check_remembered(
        self,
        tool_name: str,
        path: str | None,
        conversation_id: str | None = None,
    ) -> str | None:
        import fnmatch
        import re
        conv = str(conversation_id or "").strip() or None
        with self._lock:
            rules = list(self.remembered_rules)
        for rule in reversed(rules):
            if rule.tool_name != tool_name:
                continue
            if rule.scope == "session" and rule.conversation_id != conv:
                continue
            if rule.path_glob is None or rule.path_glob == "**":
                return rule.decision
            if path:
                pattern = rule.path_glob
                if fnmatch.fnmatch(path, pattern):
                    return rule.decision
                if "**" in pattern:
                    regex = "^" + pattern.replace(".", "\\.").replace("/**/", "(?:/|/.+/)").replace("**", ".*").replace("*", "[^/]*") + "$"
                    if re.match(regex, path):
                        return rule.decision
        return None

    def _nonce_used_unlocked(self, nonce: str) -> bool:
        if nonce in self.used_nonces:
            return True
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    row = conn.execute(
                        "SELECT 1 FROM consumed_approval_nonces WHERE nonce=?",
                        (nonce,),
                    ).fetchone()
                return row is not None
            except Exception:
                return True
        return False

    def is_nonce_used(self, nonce: str) -> bool:
        with self._lock:
            return self._nonce_used_unlocked(nonce)

    def register_request(
        self,
        turn_id: str,
        conversation_id: str,
        workspace_id: str,
        action_hash: str,
        approval_id: str,
        tool_name: str = "",
        summary: str = "",
        scope_json: str = "{}",
        risk_level: str = "MEDIUM",
    ) -> ApprovalRequest:
        now = time.time()
        req = ApprovalRequest(
            approval_id=approval_id,
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            tool_name=tool_name,
            normalized_args_hash=action_hash,
            created_at=now,
            expires_at=now + self.ttl_seconds,
            summary=summary,
        )
        with self._lock:
            self._requests_by_id[approval_id] = req
            self._requests_by_binding[
                (turn_id, conversation_id, workspace_id, action_hash)
            ] = req

        if self.db:
            with self.db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO approval_requests (
                        approval_id,conversation_id,turn_id,workspace_id,tool_name,
                        arguments_hash,scope_json,risk_level,state,nonce_hash,
                        requested_at,expires_at
                    ) VALUES (?,?,?,?,?,?,?,?,'PENDING',?,?,?)
                    """,
                    (
                        approval_id, conversation_id, turn_id, workspace_id,
                        tool_name, action_hash, scope_json, risk_level, "",
                        str(now), str(req.expires_at),
                    ),
                )
        return req

    def _load_request_from_db(
        self,
        approval_id: Optional[str],
        turn_id: str,
        conversation_id: str,
        workspace_id: str,
        action_hash: str,
    ) -> Optional[ApprovalRequest]:
        if not self.db:
            return None
        with self.db.get_connection() as conn:
            if approval_id:
                row = conn.execute(
                    "SELECT approval_id,turn_id,conversation_id,workspace_id,"
                    "arguments_hash,expires_at,state FROM approval_requests "
                    "WHERE approval_id=?",
                    (approval_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT approval_id,turn_id,conversation_id,workspace_id,"
                    "arguments_hash,expires_at,state FROM approval_requests "
                    "WHERE turn_id=? AND conversation_id=? AND workspace_id=? "
                    "AND arguments_hash=?",
                    (turn_id, conversation_id, workspace_id, action_hash),
                ).fetchone()
        if not row:
            return None
        return ApprovalRequest(
            approval_id=row["approval_id"],
            turn_id=row["turn_id"],
            conversation_id=row["conversation_id"],
            workspace_id=row["workspace_id"],
            tool_name="",
            normalized_args_hash=row["arguments_hash"],
            created_at=time.time(),
            expires_at=float(row["expires_at"]),
            summary="",
            state=row["state"],
        )

    def issue_grant(
        self,
        turn_id: str,
        conversation_id: str,
        workspace_id: str,
        action_hash: str,
        approval_id: Optional[str] = None,
    ) -> Optional[ApprovalGrant]:
        with self._lock:
            req = (
                self._requests_by_id.get(approval_id)
                if approval_id
                else None
            )
            if not req:
                req = self._requests_by_binding.get(
                    (turn_id, conversation_id, workspace_id, action_hash)
                )

        if not req:
            try:
                req = self._load_request_from_db(
                    approval_id, turn_id, conversation_id, workspace_id, action_hash
                )
            except Exception:
                return None
        if not req or req.state != "PENDING":
            return None
        if (
            req.turn_id != turn_id
            or req.conversation_id != conversation_id
            or req.workspace_id != workspace_id
            or req.normalized_args_hash != action_hash
            or time.time() > req.expires_at
        ):
            return None

        nonce = uuid.uuid4().hex
        nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        now = time.time()

        if self.db:
            try:
                with self.db.get_connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    cur = conn.execute(
                        "UPDATE approval_requests SET state='GRANTED',"
                        "decided_at=?,nonce_hash=? WHERE approval_id=? "
                        "AND state='PENDING' AND CAST(expires_at AS REAL)>?",
                        (str(now), nonce_hash, req.approval_id, now),
                    )
                    if cur.rowcount != 1:
                        conn.rollback()
                        return None
                    conn.commit()
            except Exception:
                return None

        granted_req = replace(req, state="GRANTED")
        with self._lock:
            self._issued_nonce_hashes[req.approval_id] = nonce_hash
            self._requests_by_id[req.approval_id] = granted_req
            self._requests_by_binding[
                (req.turn_id, req.conversation_id, req.workspace_id, req.normalized_args_hash)
            ] = granted_req

        return ApprovalGrant(
            approval_id=req.approval_id,
            turn_id=req.turn_id,
            conversation_id=req.conversation_id,
            workspace_id=req.workspace_id,
            action_hash=req.normalized_args_hash,
            granted_at=now,
            expires_at=min(req.expires_at, now + self.ttl_seconds),
            nonce=nonce,
        )

    def validate_and_consume(
        self,
        grant: Optional[ApprovalGrant],
        expected_action_hash: str,
        expected_turn_id: str,
        expected_conv_id: str,
        expected_ws_id: str,
        expected_approval_id: Optional[str] = None,
    ) -> bool:
        if not grant or time.time() > grant.expires_at:
            return False
        if not (
            grant.action_hash == expected_action_hash
            and grant.turn_id == expected_turn_id
            and grant.conversation_id == expected_conv_id
            and grant.workspace_id == expected_ws_id
        ):
            return False
        if expected_approval_id is not None and grant.approval_id != expected_approval_id:
            return False

        nonce_hash = hashlib.sha256(grant.nonce.encode("utf-8")).hexdigest()
        now = time.time()

        with self._lock:
            if self._nonce_used_unlocked(grant.nonce) or grant.approval_id in self.used_grants:
                return False

            if self.db:
                try:
                    with self.db.get_connection() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        row = conn.execute(
                            "SELECT state,expires_at,nonce_hash FROM approval_requests "
                            "WHERE approval_id=?",
                            (grant.approval_id,),
                        ).fetchone()
                        if (
                            not row
                            or row["state"] != "GRANTED"
                            or float(row["expires_at"]) < now
                            or not row["nonce_hash"]
                            or not hmac.compare_digest(str(row["nonce_hash"]), nonce_hash)
                        ):
                            conn.rollback()
                            return False
                        cur = conn.execute(
                            "UPDATE approval_requests SET state='CONSUMED',consumed_at=? "
                            "WHERE approval_id=? AND state='GRANTED' AND nonce_hash=?",
                            (str(now), grant.approval_id, nonce_hash),
                        )
                        if cur.rowcount != 1:
                            conn.rollback()
                            return False
                        conn.execute(
                            "INSERT INTO consumed_approval_nonces"
                            "(nonce,approval_id,consumed_at) VALUES(?,?,?)",
                            (grant.nonce, grant.approval_id, now),
                        )
                        conn.commit()
                except Exception:
                    return False
            else:
                expected_nonce_hash = self._issued_nonce_hashes.get(grant.approval_id)
                if not expected_nonce_hash or not hmac.compare_digest(expected_nonce_hash, nonce_hash):
                    return False
                self._issued_nonce_hashes.pop(grant.approval_id, None)

            self.used_nonces.add(grant.nonce)
            self.used_grants.add(grant.approval_id)
            req = self._requests_by_id.get(grant.approval_id)
            if req:
                consumed = replace(req, state="CONSUMED")
                self._requests_by_id[grant.approval_id] = consumed
                self._requests_by_binding[
                    (req.turn_id, req.conversation_id, req.workspace_id, req.normalized_args_hash)
                ] = consumed
            return True

    def list_pending(
        self,
        workspace_id: str = "",
        conversation_id: Optional[str] = None,
    ) -> list[ApprovalRequest]:
        self.expire_pending()
        now = time.time()
        with self._lock:
            requests = list(self._requests_by_id.values())
        return [
            req for req in requests
            if req.expires_at > now
            and req.state == "PENDING"
            and (not workspace_id or req.workspace_id == workspace_id)
            and (not conversation_id or req.conversation_id == conversation_id)
        ]

    def deny(self, approval_id: str, reason: str = "Denied by user") -> bool:
        now = time.time()
        updated = False
        with self._lock:
            req = self._requests_by_id.get(approval_id)
            if req:
                denied = replace(req, state="DENIED")
                self._requests_by_id[approval_id] = denied
                self._requests_by_binding[
                    (req.turn_id, req.conversation_id, req.workspace_id, req.normalized_args_hash)
                ] = denied
                self._issued_nonce_hashes.pop(approval_id, None)
                updated = True

        if self.db:
            try:
                with self.db.get_connection() as conn:
                    cur = conn.execute(
                        "UPDATE approval_requests SET state='DENIED',decided_at=?,"
                        "failure_reason=? WHERE approval_id=? AND state IN ('PENDING','GRANTED')",
                        (str(now), reason, approval_id),
                    )
                    updated = updated or cur.rowcount > 0
            except Exception:
                pass
        return updated

    def expire_pending(self) -> int:
        now = time.time()
        expired_count = 0
        with self._lock:
            for aid, req in list(self._requests_by_id.items()):
                if req.expires_at <= now and req.state in {"PENDING", "GRANTED"}:
                    expired = replace(req, state="EXPIRED")
                    self._requests_by_id[aid] = expired
                    self._requests_by_binding[
                        (req.turn_id, req.conversation_id, req.workspace_id, req.normalized_args_hash)
                    ] = expired
                    self._issued_nonce_hashes.pop(aid, None)
                    expired_count += 1
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    cur = conn.execute(
                        "UPDATE approval_requests SET state='EXPIRED' "
                        "WHERE state IN ('PENDING','GRANTED') "
                        "AND CAST(expires_at AS REAL)<=?",
                        (now,),
                    )
                    expired_count += cur.rowcount
            except Exception:
                pass
        return expired_count

    def get_audit(self, limit: int = 50) -> list[dict]:
        if not self.db:
            return []
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT approval_id,conversation_id,turn_id,tool_name,state,"
                    "requested_at,consumed_at FROM approval_requests "
                    "ORDER BY requested_at DESC LIMIT ?",
                    (min(max(int(limit), 1), 500),),
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def get_full_audit(self, limit: int = 100) -> list[dict]:
        if not self.db:
            return []
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    """SELECT approval_id,conversation_id,turn_id,workspace_id,tool_name,
                              arguments_hash,scope_json,risk_level,state,nonce_hash,
                              requested_at,expires_at,decided_at,consumed_at,
                              decision_source,failure_reason
                       FROM approval_requests ORDER BY requested_at DESC LIMIT ?""",
                    (min(max(int(limit), 1), 500),),
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                if item.get("nonce_hash"):
                    item["nonce_hash"] = "[REDACTED]"
                result.append(item)
            return result
        except Exception:
            return []
