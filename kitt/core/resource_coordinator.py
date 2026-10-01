from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from kitt.native.coordinator import LeaseGrant, LeaseRequest, WorkspaceCoordinator


_RESOURCE_KINDS = frozenset({
    "file",
    "terminal",
    "browser",
    "mcp",
    "workspace",
    "artifact",
})

_DEFAULT_WAIT_TIMEOUTS = {
    "file": 8.0,
    "terminal": 4.0,
    "browser": 10.0,
    "mcp": 6.0,
    "workspace": 15.0,
    "artifact": 4.0,
}


@dataclass(frozen=True)
class ExecutionResource:
    kind: str
    identity: str

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip().lower()
        identity = str(self.identity or "").strip()
        if kind not in _RESOURCE_KINDS:
            raise ValueError(f"unsupported execution resource kind: {self.kind}")
        if not identity:
            raise ValueError("execution resource identity is required")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "identity", identity)

    @property
    def resource_id(self) -> str:
        return f"{self.kind}:{self.identity}"


class ResourceCoordinator:
    """Typed facade over the existing fair WorkspaceCoordinator.

    This class deliberately owns no second lock table or scheduler. It maps
    execution resources to the same FIFO lease mechanism already used for
    workspace/path coordination.
    """

    def __init__(self, workspace: WorkspaceCoordinator):
        self.workspace = workspace

    @staticmethod
    def resource(kind: str, identity: str) -> ExecutionResource:
        return ExecutionResource(kind, identity)

    def acquire(
        self,
        resource: ExecutionResource,
        owner_id: str,
        *,
        mode: str = "WRITE",
        intent: str = "execution",
        ttl_seconds: float = 180.0,
        wait_timeout: float | None = None,
    ) -> LeaseGrant:
        timeout = (
            _DEFAULT_WAIT_TIMEOUTS[resource.kind]
            if wait_timeout is None
            else max(0.0, float(wait_timeout))
        )
        return self.workspace.acquire(
            resource.resource_id,
            owner_id,
            mode,
            intent,
            ttl_seconds=ttl_seconds,
            wait_timeout=timeout,
        )

    def acquire_many(
        self,
        resources: Iterable[ExecutionResource],
        owner_id: str,
        *,
        mode: str = "WRITE",
        intent: str = "execution",
        ttl_seconds: float = 180.0,
        wait_timeout: float | None = None,
    ) -> list[LeaseGrant]:
        materialized = list(resources)
        if not materialized:
            return []
        timeout = (
            max(_DEFAULT_WAIT_TIMEOUTS[item.kind] for item in materialized)
            if wait_timeout is None
            else max(0.0, float(wait_timeout))
        )
        requests = [
            LeaseRequest(item.resource_id, mode, intent)
            for item in sorted(materialized, key=lambda item: item.resource_id)
        ]
        return self.workspace.acquire_many(
            requests,
            owner_id,
            ttl_seconds=ttl_seconds,
            wait_timeout=timeout,
        )

    def refresh_owner(self, owner_id: str, ttl_seconds: float = 180.0) -> int:
        return self.workspace.refresh_owner(owner_id, ttl_seconds=ttl_seconds)

    def release_owner(self, owner_id: str) -> int:
        return self.workspace.release_owner(owner_id)


__all__ = ["ExecutionResource", "ResourceCoordinator"]
