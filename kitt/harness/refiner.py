from __future__ import annotations

import json
from typing import Any


_KIND_MAP = {
    "prompt": "PROMPT",
    "memory": "KNOWLEDGE",
    "knowledge": "KNOWLEDGE",
    "skill": "SKILL",
    "subagent": "SUBAGENT",
}
_SCOPE_MAP = {
    "local": "SESSION",
    "session": "SESSION",
    "conversation": "SESSION",
    "global": "WORKSPACE",
    "workspace": "WORKSPACE",
}
_ACTIONS = {"create", "update", "delete"}

_REFINEMENT_SYSTEM = """You refine KITT's editable continual harness from trajectory evidence.
Return JSON only. Never rewrite the immutable system prompt. Prefer the smallest
evidence-backed change. Allowed kinds: prompt, memory, skill, subagent. Allowed
actions: create, update, delete. Session-local changes are preferred unless the
lesson is clearly reusable across future sessions."""


class HarnessRefiner:
    """Reviewable continual-harness planner with explicit preview/apply/rollback."""

    MAX_EDITS = 8
    MAX_CONTENT_CHARS = 12_000

    def __init__(self, repository):
        self.repo = repository

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        fence = chr(96) * 3
        if raw.startswith(fence):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rsplit(fence, 1)[0].strip()
            if raw.startswith("json"):
                raw = raw[4:].lstrip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("refinement model did not return a JSON object")
        value = json.loads(raw[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("refinement proposal must be a JSON object")
        return value

    def _normalize(self, proposal: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(proposal, dict):
            raise ValueError("proposal must be an object")
        edits = proposal.get("edits") or []
        if not isinstance(edits, list):
            raise ValueError("proposal.edits must be an array")
        if len(edits) > self.MAX_EDITS:
            raise ValueError(f"proposal exceeds {self.MAX_EDITS} edits")
        normalized = []
        for raw in edits:
            if not isinstance(raw, dict):
                raise ValueError("each refinement edit must be an object")
            action = str(raw.get("action") or "").strip().lower()
            kind = str(raw.get("kind") or "").strip().lower()
            scope = str(raw.get("scope") or "session").strip().lower()
            if action not in _ACTIONS:
                raise ValueError(f"unsupported refinement action: {action!r}")
            if kind not in _KIND_MAP:
                raise ValueError(f"unsupported refinement kind: {kind!r}")
            if scope not in _SCOPE_MAP:
                raise ValueError(f"unsupported refinement scope: {scope!r}")
            target_id = str(raw.get("id") or "").strip() or None
            name = str(raw.get("name") or raw.get("title") or "").strip()
            content = str(raw.get("content") or "")
            if action in {"update", "delete"} and not target_id:
                raise ValueError(f"{action} requires id")
            if action != "delete":
                if not name:
                    raise ValueError(f"{action} requires name")
                if not content.strip():
                    raise ValueError(f"{action} requires content")
                if len(content) > self.MAX_CONTENT_CHARS:
                    raise ValueError("refinement content exceeds bound")
            evidence = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}
            normalized.append({
                "action": action,
                "kind": _KIND_MAP[kind],
                "scope": _SCOPE_MAP[scope],
                "id": target_id,
                "name": name,
                "content": content,
                "evidence": evidence,
            })
        return {
            "summary": str(proposal.get("summary") or "").strip(),
            "rationale": str(proposal.get("rationale") or "").strip(),
            "expected_outcome": str(
                proposal.get("expected_outcome")
                or proposal.get("expectedOutcome")
                or ""
            ).strip(),
            "edits": normalized,
        }

    def plan_with_model(self, model_client, *, trajectory: str, active_entries=None):
        context = {
            "trajectory": str(trajectory)[-48_000:],
            "active_harness": list(active_entries or [])[:64],
            "output_contract": {
                "summary": "string",
                "rationale": "string",
                "expected_outcome": "string",
                "edits": [{
                    "action": "create|update|delete",
                    "kind": "prompt|memory|skill|subagent",
                    "id": "required for update/delete",
                    "name": "required for create/update",
                    "content": "required for create/update",
                    "scope": "session|workspace",
                    "evidence": {},
                }],
            },
        }
        response = model_client.chat(
            [{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
            system_prompt=_REFINEMENT_SYSTEM,
            response_format={"type": "json_object"},
        )
        return self._normalize(self._extract_json(response))

    def preview(self, proposal, *, workspace_id: str, conversation_id: str | None):
        normalized = self._normalize(proposal)
        active = self.repo.active(workspace_id, conversation_id)
        by_id = {entry.id: entry for entry in active}
        preview = []
        for edit in normalized["edits"]:
            target = by_id.get(edit["id"]) if edit["id"] else None
            if edit["action"] in {"update", "delete"} and target is None:
                raise ValueError(f"harness entry not active or out of scope: {edit['id']}")
            preview.append({
                **edit,
                "before": (
                    {
                        "id": target.id,
                        "kind": target.entry_kind,
                        "scope": target.scope,
                        "name": target.name,
                        "content": target.content,
                        "version": target.version,
                    }
                    if target else None
                ),
            })
        return {
            "proposal": normalized,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "edits": preview,
        }

    def propose(self, conversation_id, proposal, before_snapshot):
        return self.repo.save_proposal(
            conversation_id,
            self._normalize(proposal),
            before_snapshot,
        )

    def prepare(self, proposal, *, workspace_id: str, conversation_id: str | None):
        preview = self.preview(
            proposal,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
        before = {
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "entries": [item["before"] for item in preview["edits"] if item["before"]],
        }
        rid = self.repo.save_proposal(conversation_id, preview["proposal"], before)
        return rid, preview

    def apply(self, proposal_id, after_snapshot=None):
        record = self.repo.refinement(proposal_id)
        if record is None:
            raise ValueError("unknown refinement proposal")
        if record.state != "PROPOSED":
            raise ValueError(f"refinement is not applicable from state {record.state}")
        if after_snapshot is not None and not record.proposal.get("edits"):
            return self.repo.apply_proposal(proposal_id, after_snapshot)

        workspace_id = record.before_snapshot.get("workspace_id")
        conversation_id = record.before_snapshot.get("conversation_id")
        created_ids = []
        restored_ids = []
        try:
            for edit in record.proposal.get("edits", []):
                action = edit["action"]
                target_id = edit.get("id")
                if action == "delete":
                    if not self.repo.set_status(target_id, "DELETED"):
                        raise ValueError(f"cannot delete missing harness entry {target_id}")
                    restored_ids.append(target_id)
                    continue
                target = self.repo.get(target_id) if target_id else None
                entry = self.repo.add(
                    edit["kind"],
                    edit["scope"],
                    edit["name"],
                    edit["content"],
                    "refiner",
                    workspace_id=workspace_id,
                    conversation_id=conversation_id if edit["scope"] == "SESSION" else None,
                    evidence=edit.get("evidence") or {},
                    confidence=1.0,
                    supersedes_id=target.id if target else None,
                )
                created_ids.append(entry.id)
                if target is not None:
                    restored_ids.append(target.id)
            after = {
                "created_ids": created_ids,
                "deactivated_ids": restored_ids,
                "workspace_id": workspace_id,
                "conversation_id": conversation_id,
            }
            if not self.repo.apply_proposal(proposal_id, after):
                raise RuntimeError("refinement state changed before apply")
            return after
        except Exception:
            for hid in created_ids:
                self.repo.set_status(hid, "DELETED")
            for hid in restored_ids:
                self.repo.set_status(hid, "ACTIVE")
            raise

    def rollback(self, proposal_id):
        record = self.repo.refinement(proposal_id)
        if record is None:
            raise ValueError("unknown refinement proposal")
        if record.state != "APPLIED":
            raise ValueError(f"refinement is not rollbackable from state {record.state}")
        after = record.after_snapshot or {}
        for hid in after.get("created_ids", []):
            self.repo.set_status(hid, "DELETED")
        for hid in after.get("deactivated_ids", []):
            self.repo.set_status(hid, "ACTIVE")
        if not self.repo.rollback(proposal_id):
            raise RuntimeError("refinement state changed before rollback")
        return True
