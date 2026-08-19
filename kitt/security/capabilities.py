from __future__ import annotations

from typing import Iterable, Set, FrozenSet

# Standard capabilities
CAP_REPO_READ = "repo.read"
CAP_REPO_SEARCH = "repo.search"
CAP_REPO_WRITE = "repo.write"
CAP_PROCESS_RUN = "process.run"
CAP_ARTIFACT_READ = "artifact.read"
CAP_ARTIFACT_WRITE = "artifact.write"
CAP_CHILD_SPAWN = "child.spawn"
CAP_CHILD_MESSAGE = "child.message"
CAP_GOAL_MANAGE = "goal.manage"
CAP_MEMORY_READ = "memory.read"
CAP_MEMORY_WRITE = "memory.write"
CAP_NETWORK_ACCESS = "network.access"
CAP_MCP_CALL = "mcp.call"

ALL_CAPABILITIES: FrozenSet[str] = frozenset({
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
    CAP_PROCESS_RUN,
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_CHILD_SPAWN,
    CAP_CHILD_MESSAGE,
    CAP_GOAL_MANAGE,
    CAP_MEMORY_READ,
    CAP_MEMORY_WRITE,
    CAP_NETWORK_ACCESS,
    CAP_MCP_CALL,
})

DEFAULT_CHILD_CAPABILITIES: FrozenSet[str] = frozenset({
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_ARTIFACT_READ,
})


def validate_capabilities(requested: Iterable[str]) -> Set[str]:
    """Validate requested capabilities against the known set."""
    req_set = set(requested)
    invalid = req_set - ALL_CAPABILITIES
    if invalid:
        raise ValueError(f"Unknown capabilities requested: {sorted(invalid)}")
    return req_set


def compute_child_privileges(
    requested: Iterable[str],
    parent_capabilities: Iterable[str],
    policy_allowed: Iterable[str] | None = None,
) -> Set[str]:
    """Compute effective child capabilities using strict intersection.
    
    Rule: child permissions <= parent permissions, never escalated.
    """
    req_set = validate_capabilities(requested)
    parent_set = set(parent_capabilities)
    policy_set = set(policy_allowed) if policy_allowed is not None else ALL_CAPABILITIES
    return req_set & parent_set & policy_set
