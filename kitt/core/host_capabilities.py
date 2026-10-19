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
            "repo.read or repo.search, patch.apply or repo.write_file, "
            "process.run for authorized validation; avoid redundant status/help commands"
        ),
    }
    if re_task:
        # Docker is deliberately not probed from the model: the lab topology is
        # operator-configured. Never assume a container is running/attested.
        snapshot["re_lab"] = {
            "linux_container": "kitt-re-full (verify live status before use)",
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
