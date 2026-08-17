import uuid
import time
import hashlib
from dataclasses import dataclass
from typing import Optional, Set, Literal

@dataclass(frozen=True)
class RememberedRule:
    tool_name: str
    path_glob: str | None
    decision: Literal["allow", "deny"]
    scope: Literal["session", "workspace"]
    created_at: float

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
    """Manages single-use, persistent, bound approval requests and grants using CAS transactions."""

    def __init__(self, ttl_seconds: float = 300.0, db=None):
        self.ttl_seconds = ttl_seconds
        self.used_nonces: Set[str] = set()
        self.used_grants: Set[str] = set()
        self._requests_by_id: dict[str, ApprovalRequest] = {}
        self._requests_by_binding: dict[tuple, ApprovalRequest] = {}
        self.db = db
        self.remembered_rules: list[RememberedRule] = []
        self._load_remembered_rules()

    @property
    def _requests(self) -> dict:
        merged = dict(self._requests_by_id)
        merged.update(self._requests_by_binding)
        return merged

    def _load_remembered_rules(self) -> None:
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    rows = conn.execute("SELECT tool_name, path_glob, decision, created_at FROM remembered_approval_rules ORDER BY created_at ASC").fetchall()
                    for r in rows:
                        self.remembered_rules.append(RememberedRule(r[0], r[1], r[2], "workspace", r[3]))
            except Exception:
                pass

    def remember(self, tool_name: str, path_glob: str | None, decision: str, scope: str = "workspace") -> None:
        rule = RememberedRule(tool_name, path_glob, decision, scope, time.time())
        self.remembered_rules.append(rule)
        if scope == "workspace" and self.db:
            try:
                with self.db.get_connection() as conn:
                    conn.execute(
                        "INSERT INTO remembered_approval_rules(tool_name, path_glob, decision, created_at) VALUES (?,?,?,?)",
                        (tool_name, path_glob, decision, time.time())
                    )
            except Exception:
                pass

    def check_remembered(self, tool_name: str, path: str | None) -> str | None:
        import fnmatch, re
        for rule in reversed(self.remembered_rules):
            if rule.tool_name != tool_name:
                continue
            if rule.path_glob is None or rule.path_glob == "**":
                return rule.decision
            if path:
                pattern = rule.path_glob
                if fnmatch.fnmatch(path, pattern):
                    return rule.decision
                if "**" in pattern:
                    reg = "^" + pattern.replace(".", "\\.").replace("/**/", "(?:/|/.+/)" ).replace("**", ".*").replace("*", "[^/]*") + "$"
                    if re.match(reg, path):
                        return rule.decision
        return None

    def is_nonce_used(self, nonce: str) -> bool:
        if nonce in self.used_nonces:
            return True
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    row = conn.execute(
                        "SELECT 1 FROM consumed_approval_nonces WHERE nonce=?", (nonce,)
                    ).fetchone()
                    return row is not None
            except Exception:
                pass
        return False

    def register_request(self, turn_id: str, conversation_id: str, workspace_id: str,
                         action_hash: str, approval_id: str, tool_name: str = "", summary: str = "",
                         scope_json: str = "{}", risk_level: str = "MEDIUM") -> ApprovalRequest:
        now = time.time()
        expires_at = now + self.ttl_seconds
        req = ApprovalRequest(
            approval_id=approval_id,
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            tool_name=tool_name,
            normalized_args_hash=action_hash,
            created_at=now,
            expires_at=expires_at,
            summary=summary,
            state="PENDING"
        )
        self._requests_by_id[approval_id] = req
        self._requests_by_binding[(turn_id, conversation_id, workspace_id, action_hash)] = req

        if self.db:
            try:
                with self.db.get_connection() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO approval_requests (
                            approval_id, conversation_id, turn_id, workspace_id, tool_name,
                            arguments_hash, scope_json, risk_level, state, nonce_hash,
                            requested_at, expires_at
                        ) VALUES (?,?,?,?,?,?,?,?,'PENDING',?,?,?)
                        """,
                        (
                            approval_id, conversation_id, turn_id, workspace_id, tool_name,
                            action_hash, scope_json, risk_level, "",
                            str(now), str(expires_at)
                        )
                    )
            except Exception:
                pass
        return req

    def issue_grant(self, turn_id: str, conversation_id: str, workspace_id: str, action_hash: str,
                    approval_id: Optional[str] = None) -> Optional[ApprovalGrant]:
        req = None
        if approval_id and approval_id in self._requests_by_id:
            req = self._requests_by_id[approval_id]
        if not req:
            req = self._requests_by_binding.get((turn_id, conversation_id, workspace_id, action_hash))

        # Check DB if not in memory
        if not req and self.db:
            try:
                with self.db.get_connection() as conn:
                    row = None
                    if approval_id:
                        row = conn.execute(
                            "SELECT approval_id, turn_id, conversation_id, workspace_id, arguments_hash, expires_at, state FROM approval_requests WHERE approval_id=?",
                            (approval_id,)
                        ).fetchone()
                    if not row:
                        row = conn.execute(
                            "SELECT approval_id, turn_id, conversation_id, workspace_id, arguments_hash, expires_at, state FROM approval_requests WHERE turn_id=? AND conversation_id=? AND workspace_id=? AND arguments_hash=? AND state='PENDING'",
                            (turn_id, conversation_id, workspace_id, action_hash)
                        ).fetchone()
                    if row and row["state"] == "PENDING":
                        req = ApprovalRequest(
                            approval_id=row["approval_id"],
                            turn_id=row["turn_id"],
                            conversation_id=row["conversation_id"],
                            workspace_id=row["workspace_id"],
                            tool_name="",
                            normalized_args_hash=row["arguments_hash"],
                            created_at=time.time(),
                            expires_at=float(row["expires_at"]),
                            summary="",
                            state=row["state"]
                        )
                        self._requests_by_id[req.approval_id] = req
                        self._requests_by_binding[(req.turn_id, req.conversation_id, req.workspace_id, req.normalized_args_hash)] = req
            except Exception:
                pass

        # If no approval request exists, issue_grant is strictly refused per spec
        if not req:
            return None

        if time.time() > req.expires_at:
            return None

        nonce = uuid.uuid4().hex
        nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        now = time.time()

        if self.db:
            try:
                with self.db.get_connection() as conn:
                    cur = conn.execute(
                        "UPDATE approval_requests SET state='GRANTED', decided_at=?, nonce_hash=? WHERE approval_id=? AND state='PENDING'",
                        (str(now), nonce_hash, req.approval_id)
                    )
            except Exception:
                pass

        return ApprovalGrant(
            approval_id=req.approval_id,
            turn_id=req.turn_id,
            conversation_id=req.conversation_id,
            workspace_id=req.workspace_id,
            action_hash=req.normalized_args_hash,
            granted_at=now,
            expires_at=now + self.ttl_seconds,
            nonce=nonce
        )

    def validate_and_consume(self, grant: Optional[ApprovalGrant], expected_action_hash: str,
                             expected_turn_id: str, expected_conv_id: str,
                             expected_ws_id: str, expected_approval_id: Optional[str] = None) -> bool:
        if not grant:
            return False

        now = time.time()
        if now > grant.expires_at:
            return False

        if grant.action_hash != expected_action_hash:
            return False
        if grant.turn_id != expected_turn_id:
            return False
        if grant.conversation_id != expected_conv_id:
            return False
        if grant.workspace_id != expected_ws_id:
            return False
        if expected_approval_id is not None and grant.approval_id != expected_approval_id:
            return False

        # Check nonces / grants for replay
        if self.is_nonce_used(grant.nonce) or grant.approval_id in self.used_grants:
            return False

        if self.db:
            try:
                with self.db.get_connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT state, expires_at FROM approval_requests WHERE approval_id=?",
                        (grant.approval_id,)
                    ).fetchone()
                    if not row or row["state"] not in ("GRANTED", "PENDING"):
                        conn.rollback()
                        return False
                    if float(row["expires_at"]) < now:
                        conn.execute(
                            "UPDATE approval_requests SET state='EXPIRED' WHERE approval_id=?",
                            (grant.approval_id,)
                        )
                        conn.commit()
                        return False

                    # CAS: GRANTED or PENDING -> CONSUMED
                    cur = conn.execute(
                        "UPDATE approval_requests SET state='CONSUMED', consumed_at=? WHERE approval_id=? AND state IN ('GRANTED','PENDING')",
                        (str(now), grant.approval_id)
                    )
                    if cur.rowcount != 1:
                        conn.rollback()
                        return False

                    conn.execute(
                        "INSERT OR IGNORE INTO consumed_approval_nonces(nonce,approval_id,consumed_at) VALUES(?,?,?)",
                        (grant.nonce, grant.approval_id, now)
                    )
                    conn.commit()
            except Exception:
                return False

        self.used_nonces.add(grant.nonce)
        self.used_grants.add(grant.approval_id)
        return True

    def list_pending(self, workspace_id: str = "", conversation_id: Optional[str] = None) -> list[ApprovalRequest]:
        self.expire_pending()
        now = time.time()
        res = []
        for req in self._requests_by_id.values():
            if req.expires_at > now and req.state == "PENDING":
                if workspace_id and req.workspace_id != workspace_id:
                    continue
                if conversation_id and req.conversation_id != conversation_id:
                    continue
                res.append(req)
        return res

    def deny(self, approval_id: str, reason: str = "Denied by user") -> bool:
        now = time.time()
        updated = False
        req = self._requests_by_id.pop(approval_id, None)
        if req:
            self._requests_by_binding.pop((req.turn_id, req.conversation_id, req.workspace_id, req.normalized_args_hash), None)
            updated = True

        if self.db:
            try:
                with self.db.get_connection() as conn:
                    cur = conn.execute(
                        "UPDATE approval_requests SET state='DENIED', decided_at=?, failure_reason=? WHERE approval_id=? AND state IN ('PENDING','GRANTED')",
                        (str(now), reason, approval_id)
                    )
                    if cur.rowcount > 0:
                        updated = True
            except Exception:
                pass
        return updated

    def expire_pending(self) -> int:
        now = time.time()
        expired_count = 0
        expired_ids = [aid for aid, req in list(self._requests_by_id.items()) if getattr(req, "expires_at", 0) <= now]
        for aid in expired_ids:
            req = self._requests_by_id.pop(aid, None)
            if req:
                self._requests_by_binding.pop((req.turn_id, req.conversation_id, req.workspace_id, req.normalized_args_hash), None)
                expired_count += 1

        if self.db:
            try:
                with self.db.get_connection() as conn:
                    cur = conn.execute(
                        "UPDATE approval_requests SET state='EXPIRED' WHERE state IN ('PENDING','GRANTED') AND CAST(expires_at AS REAL) <= ?",
                        (now,)
                    )
                    expired_count += cur.rowcount
            except Exception:
                pass
        return expired_count

    def get_audit(self, limit: int = 50) -> list[dict]:
        res = []
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    rows = conn.execute(
                        "SELECT approval_id, conversation_id, turn_id, tool_name, state, requested_at, consumed_at FROM approval_requests ORDER BY requested_at DESC LIMIT ?",
                        (limit,)
                    ).fetchall()
                    for row in rows:
                        res.append(dict(row))
            except Exception:
                pass
        return res

    def get_full_audit(self, limit: int = 100) -> list[dict]:
        res = []
        if self.db:
            try:
                with self.db.get_connection() as conn:
                    rows = conn.execute(
                        """SELECT approval_id, conversation_id, turn_id, workspace_id, tool_name,
                                  arguments_hash, scope_json, risk_level, state, nonce_hash,
                                  requested_at, expires_at, decided_at, consumed_at, decision_source, failure_reason
                           FROM approval_requests ORDER BY requested_at DESC LIMIT ?""",
                        (limit,)
                    ).fetchall()
                    for row in rows:
                        res.append(dict(row))
            except Exception:
                pass
        return res
