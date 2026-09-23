"""Deterministic risk classification for semantic code review."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from pathlib import PurePosixPath
from typing import Iterable


class ReviewRisk(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass(frozen=True)
class ReviewRiskAssessment:
    level: ReviewRisk
    reasons: tuple[str, ...]

    @property
    def name(self) -> str:
        return self.level.name

    def to_dict(self) -> dict:
        value = asdict(self)
        value["level"] = self.level.name
        return value


_CRITICAL_PATH_TERMS = (
    "security", "auth", "credential", "secret", "policy", "approval",
    "sandbox", "egress", "permission", "release", ".github/workflows",
)
_HIGH_PATH_TERMS = (
    "migration", "database", "payment", "billing", "settlement", "transaction",
    "concurrency", "thread", "async", "filesystem", "network", "process",
)
_CRITICAL_CONTENT_TERMS = (
    "subprocess", "os.system", "eval(", "exec(", "authorization", "credential",
    "approval_grant", "network_requested", "shell=true",
)
_HIGH_CONTENT_TERMS = (
    "transaction", "lock", "mutex", "semaphore", "asyncio", "threading",
    "delete from", "drop table", "alter table",
)


def classify_review_risk(paths: Iterable[str], snapshot: str = "") -> ReviewRiskAssessment:
    normalized = [
        str(path).replace("\\", "/").casefold()
        for path in paths
        if path
    ]

    if any(
        any(term in path for term in _CRITICAL_PATH_TERMS)
        for path in normalized
    ):
        return ReviewRiskAssessment(
            ReviewRisk.CRITICAL,
            ("critical control/security path changed",),
        )

    snapshot_lower = str(snapshot or "").casefold()
    if any(term in snapshot_lower for term in _CRITICAL_CONTENT_TERMS):
        return ReviewRiskAssessment(
            ReviewRisk.CRITICAL,
            ("critical execution/security primitive present",),
        )

    if any(
        any(term in path for term in _HIGH_PATH_TERMS)
        for path in normalized
    ):
        return ReviewRiskAssessment(
            ReviewRisk.HIGH,
            ("high-impact data/runtime path changed",),
        )
    if any(term in snapshot_lower for term in _HIGH_CONTENT_TERMS):
        return ReviewRiskAssessment(
            ReviewRisk.HIGH,
            ("high-impact concurrency/data primitive present",),
        )

    if normalized and all(
        "/tests/" in f"/{path}/"
        or PurePosixPath(path).name.startswith("test_")
        or PurePosixPath(path).suffix in {".md", ".txt"}
        for path in normalized
    ):
        return ReviewRiskAssessment(
            ReviewRisk.LOW,
            ("tests/docs-only change",),
        )

    return ReviewRiskAssessment(
        ReviewRisk.MEDIUM,
        ("application code change",),
    )
