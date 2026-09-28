from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RESOURCE_KINDS = frozenset(
    {
        "schema",
        "entity",
        "query",
        "command",
        "endpoint",
        "event",
        "workflow",
        "policy",
        "job",
        "observability",
    }
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "path": self.path,
        }


@dataclass(frozen=True)
class BackendPlan:
    backend_id: str
    base_revision: int
    operations: tuple[dict[str, Any], ...] = ()
    affected_resources: tuple[str, ...] = ()
    issues: tuple[ValidationIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "base_revision": self.base_revision,
            "changeset": {
                "id": f"backend:{self.backend_id}:{self.base_revision + 1}",
                "domain": "backend",
                "base_revision": str(self.base_revision),
                "operations": list(self.operations),
            },
            "affected_resources": list(self.affected_resources),
            "issues": [issue.to_dict() for issue in self.issues],
        }
