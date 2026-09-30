from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from kitt_protocol import ContextEpoch


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _profile_revision(profile: Any) -> str:
    if profile is None:
        return _digest({})
    names = (
        "backend",
        "model",
        "base_url",
        "protocol",
        "context_window",
        "max_output_tokens",
        "supports_tools",
        "supports_json",
        "enforce_local_limits",
    )
    return _digest({name: getattr(profile, name, None) for name in names})


def _repository_revision(
    root: Path,
    *,
    repo_map: str,
    files_context: str,
) -> str:
    git_state: dict[str, str] = {}
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.5,
            check=False,
        )
        if head.returncode == 0:
            git_state["head"] = head.stdout.strip()
            status = subprocess.run(
                [
                    "git",
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=normal",
                ],
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2.0,
                check=False,
            )
            if status.returncode == 0:
                git_state["status"] = status.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return _digest(
        {
            "git": git_state,
            # The exact repository evidence selected for this turn participates
            # in the epoch even when the workspace is not a git repository.
            "repo_map": repo_map,
            "files_context": files_context,
        }
    )


def _policy_revision(processor: Any) -> str:
    registry = getattr(processor, "registry", None)
    policy = getattr(registry, "policy", None)
    autonomy = getattr(policy, "autonomy", None)
    approval = getattr(registry, "approval_manager", None)
    remembered = []
    for item in list(getattr(approval, "remembered_rules", ()) or ()):
        if hasattr(item, "__dict__"):
            remembered.append(
                {
                    key: value
                    for key, value in vars(item).items()
                    if not key.startswith("_")
                }
            )
        else:
            remembered.append(str(item))
    autonomy_value = (
        autonomy.to_dict()
        if autonomy is not None and hasattr(autonomy, "to_dict")
        else str(autonomy)
    )
    return _digest(
        {
            "policy_type": type(policy).__name__ if policy is not None else "",
            "autonomy": autonomy_value,
            "remembered_approvals": remembered,
        }
    )


def _baseline_sequence(processor: Any, conversation_id: str) -> int:
    ledger = getattr(processor, "event_ledger", None)
    db = getattr(ledger, "db", None)
    if db is None or not conversation_id:
        return 0
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence),0) FROM session_events "
                "WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
        return int(row[0] if row else 0)
    except Exception:
        return 0


def build_context_epoch(
    processor: Any,
    *,
    conversation_id: str,
    turn_id: str,
    memory_context: str,
    harness_context: str,
    repo_map: str,
    files_context: str,
    guidelines_context: str,
    skills_context: str,
    tool_definitions: list[dict[str, Any]],
    policy_context: Any,
    provider_profile: Any,
) -> ContextEpoch:
    memory_revision = _digest(
        {
            "memory": memory_context,
            "harness": harness_context,
        }
    )
    repository_revision = _repository_revision(
        Path(processor.root_path),
        repo_map=repo_map,
        files_context="\n".join(
            part
            for part in (files_context, guidelines_context)
            if part
        ),
    )
    skills_revision = _digest(skills_context)
    plugins_revision = _digest(
        {
            "tool_definitions": tool_definitions,
            "runtime_operations": list(
                getattr(
                    getattr(processor, "registry", None),
                    "runtime_operation_names",
                    lambda: (),
                )()
            ),
        }
    )
    policy_revision = _digest(
        {
            "runtime": _policy_revision(processor),
            "context": policy_context,
        }
    )
    provider_revision = _profile_revision(provider_profile)
    baseline_seq = _baseline_sequence(processor, conversation_id)

    axes = {
        "baseline_seq": baseline_seq,
        "memory_revision": memory_revision,
        "repository_revision": repository_revision,
        "skills_revision": skills_revision,
        "plugins_revision": plugins_revision,
        "policy_revision": policy_revision,
        "provider_revision": provider_revision,
    }
    snapshot_digest = _digest(axes)
    return ContextEpoch(
        epoch_id=f"ctx_{snapshot_digest[:32]}",
        baseline_seq=baseline_seq,
        memory_revision=memory_revision,
        repository_revision=repository_revision,
        skills_revision=skills_revision,
        plugins_revision=plugins_revision,
        policy_revision=policy_revision,
        provider_revision=provider_revision,
        snapshot_digest=snapshot_digest,
    )


def persist_context_epoch(
    processor: Any,
    epoch: ContextEpoch,
    *,
    conversation_id: str,
    turn_id: str,
) -> None:
    ledger = getattr(processor, "event_ledger", None)
    if ledger is None:
        return
    try:
        ledger.append_event(
            conversation_id,
            "ContextEpochComputed",
            asdict(epoch),
            turn_id=turn_id,
            source="context-compiler",
            durability="DURABLE",
            replayable=True,
        )
    except Exception:
        # Context compilation can still proceed when optional history is off.
        return
