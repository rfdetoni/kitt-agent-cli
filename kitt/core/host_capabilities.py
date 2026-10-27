"""Small, operator-owned capability snapshot for the LLM-first execution prompt.

Observations are advisory, never approvals or capabilities granted to a model.
Only describe the managed RE lab when the operator explicitly mentions it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil


def capability_snapshot_for_prompt(prompt: str, workspace: Path) -> str:
    root = Path(workspace)
    text = str(prompt or "").casefold()
    re_task = bool(re.search(
        r"\b(?:re.lab|kitt[ -]re|guest[ -]linux|linux[ -]guest|wine[ -]staging|oracle)\b",
        text,
    ))
    snapshot = {
        "schema_version": 1,
        "workspace_is_git": (root / ".git").exists(),
        "host_commands": {name: bool(shutil.which(name)) for name in ("git", "docker")},
        "tools": "use exact tool-schema operations; policy approval is still required",
        "recommended_workflow": (
            "repo.batch_read for up to eight independent repo.read/repo.list calls, "
            "repo.search for targeted lookup, patch.apply or repo.write_file for edits, "
            "flow.execute is read-only and cannot contain process.run; "
            "use process.run separately for authorized validation; "
            "do not repeat cached host receipts"
        ),
    }
    if re_task:
        # Docker is deliberately not probed from the model: the lab topology is
        # operator-configured. Never assume a container is running/attested.
        # Bound the expensive Docker read to one snapshot per relevant turn.
        # These observations remain advisory and are never an execution grant.
        try:
            from kitt import re_docker_lab
            current = re_docker_lab.inspect("linux")
            linux_state = str(current.get("status") or "unknown") if current else "missing"
        except (OSError, ValueError, RuntimeError):
            linux_state = "unknown"
        snapshot["re_lab"] = {
            "linux_container": "kitt-re-full",
            "linux_status": linux_state,
            "linux_network": "none",
            "guest_workspace": "/workspace",
            "host_shared_workspace": "~/.local/share/kitt/re-lab/workspace",
            "host_network_staging": "kitt re lab stage-wine --approve (operator only)",
            "guest_network_clone": False,
            "windows_oracle": "fixed hash-pinned probes only; not an interactive coding shell",
            "guest_git_push": False,
        }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return "HOST CAPABILITY SNAPSHOT (advisory, not authorization; id=" + fingerprint + "): " + canonical
