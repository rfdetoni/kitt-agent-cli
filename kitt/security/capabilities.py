from __future__ import annotations

from typing import FrozenSet, Iterable, Set

CAP_REPO_READ = "repo.read"
CAP_REPO_SEARCH = "repo.search"
CAP_REPO_WRITE = "repo.write"
CAP_PROCESS_RUN = "process.run"
CAP_ARTIFACT_READ = "artifact.read"
CAP_ARTIFACT_WRITE = "artifact.write"
CAP_CHILD_SPAWN = "child.spawn"
CAP_CHILD_MESSAGE = "child.message"
CAP_CHILD_INSPECT = "child.inspect"
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
    CAP_CHILD_INSPECT,
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

READ_ONLY_CAPABILITIES: FrozenSet[str] = frozenset({
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_ARTIFACT_READ,
    CAP_CHILD_INSPECT,
    CAP_MEMORY_READ,
})

TOOL_TO_CAPABILITY = {
    "read_file": CAP_REPO_READ,
    "inspect_symbol": CAP_REPO_READ,
    "list_files": CAP_REPO_READ,
    "repository_map": CAP_REPO_READ,
    "git_status": CAP_REPO_READ,
    "git_diff": CAP_REPO_READ,
    "search": CAP_REPO_SEARCH,
    "write_file": CAP_REPO_WRITE,
    "apply_patch": CAP_REPO_WRITE,
    "run_command": CAP_PROCESS_RUN,
    "python_compute": CAP_PROCESS_RUN,
    "artifact_store": CAP_ARTIFACT_WRITE,
    "artifacts_store": CAP_ARTIFACT_WRITE,
    "artifact_read": CAP_ARTIFACT_READ,
    "artifacts_read": CAP_ARTIFACT_READ,
    "artifact_list": CAP_ARTIFACT_READ,
    "artifacts_list": CAP_ARTIFACT_READ,
    "child_spawn": CAP_CHILD_SPAWN,
    "children_spawn": CAP_CHILD_SPAWN,
    "child_ask": CAP_CHILD_MESSAGE,
    "child_send": CAP_CHILD_MESSAGE,
    "children_send": CAP_CHILD_MESSAGE,
    "child_inspect": CAP_CHILD_INSPECT,
    "children_inspect": CAP_CHILD_INSPECT,
    "goal_create": CAP_GOAL_MANAGE,
    "goal_add_gate": CAP_GOAL_MANAGE,
    "goal_inspect": CAP_GOAL_MANAGE,
    "goal_update": CAP_GOAL_MANAGE,
    "memory_recall": CAP_MEMORY_READ,
    "memory_save": CAP_MEMORY_WRITE,
    "mcp_call": CAP_MCP_CALL,
}


def canonicalize_capabilities(requested: Iterable[str]) -> Set[str]:
    canon: Set[str] = set()
    invalid: Set[str] = set()
    for item in requested:
        if item in ALL_CAPABILITIES:
            canon.add(item)
        elif item in TOOL_TO_CAPABILITY:
            canon.add(TOOL_TO_CAPABILITY[item])
        else:
            invalid.add(item)
    if invalid:
        raise ValueError(f"Unknown capabilities requested: {sorted(invalid)}")
    return canon


def capabilities_for_tools(tools: Iterable[str], *, strict: bool = False) -> Set[str]:
    result: Set[str] = set()
    for tool in tools:
        try:
            result |= canonicalize_capabilities([tool])
        except ValueError:
            if strict:
                raise
    return result


def validate_capabilities(requested: Iterable[str]) -> Set[str]:
    return canonicalize_capabilities(requested)


def compute_child_privileges(
    requested: Iterable[str],
    parent_capabilities: Iterable[str],
    policy_allowed: Iterable[str] | None = None,
) -> Set[str]:
    requested_caps = validate_capabilities(requested)
    parent_caps = validate_capabilities(parent_capabilities)
    policy_caps = (
        validate_capabilities(policy_allowed)
        if policy_allowed is not None
        else set(parent_caps)
    )
    return requested_caps & parent_caps & policy_caps
