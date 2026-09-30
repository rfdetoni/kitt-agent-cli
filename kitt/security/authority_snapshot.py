from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from kitt_protocol import ExecutionAuthoritySnapshot


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "to_dict"):
        try:
            return _jsonable(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {
            str(key): _jsonable(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


def _digest(value: Any) -> str:
    raw = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _approval_revision(approval_manager: Any) -> str:
    rules = []
    for rule in list(getattr(approval_manager, "remembered_rules", ()) or ()):
        rules.append(_jsonable(rule))
    return _digest(rules)


def capture_authority_snapshot(
    security_context,
    *,
    policy: Any,
    autonomy: Any,
    approval_manager: Any,
    sandbox_profile: str,
) -> dict[str, Any]:
    capabilities = sorted(str(item) for item in security_context.capabilities)
    filesystem_caps = [
        item
        for item in capabilities
        if item.startswith(("repo.", "artifact.", "filesystem.", "state."))
    ]
    network_caps = [
        item
        for item in capabilities
        if item.startswith(("network.", "browser.", "mcp."))
    ]
    policy_revision = _digest(
        {
            "policy": type(policy).__name__,
            "root": str(getattr(policy, "root_path", "")),
            "disallowed_shells": sorted(
                str(item)
                for item in getattr(policy, "DISALLOWED_PROCESS_EXECUTABLES", ())
            ),
        }
    )
    autonomy_revision = _digest(_jsonable(autonomy))
    snapshot = ExecutionAuthoritySnapshot(
        policy_revision=policy_revision,
        autonomy_revision=autonomy_revision,
        approval_revision=_approval_revision(approval_manager),
        workspace_id=security_context.workspace_id,
        conversation_id=security_context.conversation_id,
        turn_id=security_context.turn_id,
        sandbox_profile=str(sandbox_profile or "default"),
        filesystem_caps=tuple(filesystem_caps),
        network_caps=tuple(network_caps),
        executable_identity=(
            f"{security_context.principal_type}:{security_context.principal_id}"
        ),
        approval_grant=None,
    )
    mapping = asdict(snapshot)
    return {"snapshot": mapping, "digest": _digest(mapping)}


def validate_authority_snapshot(
    envelope: dict[str, Any] | None,
    security_context,
    *,
    policy: Any | None = None,
    autonomy: Any | None = None,
    approval_manager: Any | None = None,
    sandbox_profile: str | None = None,
) -> None:
    if not envelope:
        raise PermissionError("missing execution authority snapshot")
    snapshot = envelope.get("snapshot")
    digest = str(envelope.get("digest") or "")
    if not isinstance(snapshot, dict) or not digest:
        raise PermissionError("invalid execution authority snapshot")
    if _digest(snapshot) != digest:
        raise PermissionError("execution authority snapshot integrity check failed")
    expected_identity = (
        f"{security_context.principal_type}:{security_context.principal_id}"
    )
    checks = {
        "workspace_id": security_context.workspace_id,
        "conversation_id": security_context.conversation_id,
        "turn_id": security_context.turn_id,
        "executable_identity": expected_identity,
    }
    for key, expected in checks.items():
        if str(snapshot.get(key) or "") != str(expected or ""):
            raise PermissionError(f"execution authority snapshot {key} mismatch")

    current_caps = sorted(str(item) for item in security_context.capabilities)
    current_fs = [
        item
        for item in current_caps
        if item.startswith(("repo.", "artifact.", "filesystem.", "state."))
    ]
    current_network = [
        item
        for item in current_caps
        if item.startswith(("network.", "browser.", "mcp."))
    ]
    if list(snapshot.get("filesystem_caps") or []) != current_fs:
        raise PermissionError("execution authority filesystem capability mismatch")
    if list(snapshot.get("network_caps") or []) != current_network:
        raise PermissionError("execution authority network capability mismatch")

    if policy is not None and autonomy is not None and approval_manager is not None:
        current = capture_authority_snapshot(
            security_context,
            policy=policy,
            autonomy=autonomy,
            approval_manager=approval_manager,
            sandbox_profile=(
                sandbox_profile
                if sandbox_profile is not None
                else str(snapshot.get("sandbox_profile") or "default")
            ),
        )["snapshot"]
        for key in (
            "policy_revision",
            "autonomy_revision",
            "approval_revision",
            "sandbox_profile",
        ):
            if str(snapshot.get(key) or "") != str(current.get(key) or ""):
                raise PermissionError(
                    f"execution authority snapshot {key} is stale"
                )
