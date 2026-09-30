from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from kitt.security.capabilities import (
    CAP_ARTIFACT_READ,
    CAP_MEMORY_READ,
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
)


class AgentRole(StrEnum):
    DISCOVER = "DISCOVER"
    ARCHITECT = "ARCHITECT"
    IMPLEMENT = "IMPLEMENT"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"


_READ_TOOLS = frozenset(
    {
        "read_file",
        "search",
        "repository_map",
        "list_files",
        "git_status",
        "git_diff",
        "artifact_read",
        "memory_recall",
        "kitt_runtime",
    }
)
_VERIFY_TOOLS = _READ_TOOLS | frozenset({"run_command", "python_compute"})
_IMPLEMENT_TOOLS = frozenset({"*"})


@dataclass(frozen=True)
class AgentRolePolicy:
    role: AgentRole
    capabilities: frozenset[str]
    allowed_tools: frozenset[str]
    mutation_allowed: bool
    context_policy: str
    model_policy: str
    budget_policy: str

    def allows_tool(self, tool_name: str, args: Any = None) -> bool:
        name = str(tool_name or "")
        if "*" not in self.allowed_tools and name not in self.allowed_tools:
            return False
        if self.mutation_allowed:
            return True
        if name != "kitt_runtime":
            return name not in {
                "write_file",
                "apply_patch",
                "create_directory",
                "move",
                "rename",
                "delete",
                "child_spawn",
                "goal_create",
                "goal_add_gate",
                "harness_remember",
                "queue_input",
            }
        payload = args if isinstance(args, dict) else {}
        operation = str(payload.get("operation") or "")
        return operation not in {
            "repo.write_file",
            "repo.edit_symbol",
            "repo.create_directory",
            "repo.move",
            "repo.rename",
            "repo.delete",
            "patch.apply",
            "process.run",
            "process.start",
            "process.stdin",
            "process.signal",
            "process.stop",
            "process.resume",
            "flow.execute",
            "program.execute",
            "state.set",
            "children.spawn",
            "goal.update",
            "memory.correct",
            "memory.concept",
            "memory.link",
            "mcp.call",
            "browser.click",
            "browser.type",
            "browser.close",
        }


POLICIES: dict[AgentRole, AgentRolePolicy] = {
    AgentRole.DISCOVER: AgentRolePolicy(
        AgentRole.DISCOVER,
        frozenset({CAP_REPO_READ, CAP_REPO_SEARCH, CAP_ARTIFACT_READ, CAP_MEMORY_READ}),
        _READ_TOOLS,
        False,
        "evidence-first",
        "context",
        "low",
    ),
    AgentRole.ARCHITECT: AgentRolePolicy(
        AgentRole.ARCHITECT,
        frozenset({CAP_REPO_READ, CAP_REPO_SEARCH, CAP_ARTIFACT_READ, CAP_MEMORY_READ}),
        _READ_TOOLS,
        False,
        "architecture-and-evidence",
        "reasoning",
        "medium",
    ),
    AgentRole.IMPLEMENT: AgentRolePolicy(
        AgentRole.IMPLEMENT,
        frozenset(
            {
                CAP_REPO_READ,
                CAP_REPO_SEARCH,
                CAP_REPO_WRITE,
                CAP_ARTIFACT_READ,
                CAP_MEMORY_READ,
                CAP_PROCESS_RUN,
            }
        ),
        _IMPLEMENT_TOOLS,
        True,
        "implementation",
        "execution",
        "high",
    ),
    AgentRole.VERIFY: AgentRolePolicy(
        AgentRole.VERIFY,
        frozenset(
            {
                CAP_REPO_READ,
                CAP_REPO_SEARCH,
                CAP_ARTIFACT_READ,
                CAP_MEMORY_READ,
                CAP_PROCESS_RUN,
            }
        ),
        _VERIFY_TOOLS,
        False,
        "validation-evidence",
        "verification",
        "medium",
    ),
    AgentRole.REVIEW: AgentRolePolicy(
        AgentRole.REVIEW,
        frozenset({CAP_REPO_READ, CAP_REPO_SEARCH, CAP_ARTIFACT_READ, CAP_MEMORY_READ}),
        _READ_TOOLS,
        False,
        "review-evidence",
        "review",
        "medium",
    ),
}


def resolve_agent_role(cmd: Any, task: Any) -> AgentRolePolicy:
    mode = str(getattr(cmd, "mode", "") or "").lower()
    raw_intent = getattr(task, "intent", "")
    intent = str(getattr(raw_intent, "value", raw_intent) or "").upper()
    if mode == "plan":
        return POLICIES[AgentRole.ARCHITECT]
    if mode == "ask" or intent == "ASK":
        return POLICIES[AgentRole.DISCOVER]
    if intent == "REVIEW":
        return POLICIES[AgentRole.REVIEW]
    if intent == "TEST":
        return POLICIES[AgentRole.VERIFY]
    return POLICIES[AgentRole.IMPLEMENT]


def restrict_tools(policy: AgentRolePolicy, tools: list[str]) -> list[str]:
    if "*" in policy.allowed_tools:
        return list(dict.fromkeys(tools))
    return [
        tool
        for tool in dict.fromkeys(tools)
        if tool in policy.allowed_tools
    ]


__all__ = [
    "AgentRole",
    "AgentRolePolicy",
    "POLICIES",
    "resolve_agent_role",
    "restrict_tools",
]
