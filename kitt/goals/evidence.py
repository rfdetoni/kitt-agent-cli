"""Structured evidence emitted by deterministic and semantic completion checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EvidenceEntry:
    source: str
    name: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class EvidenceLedger:
    entries: tuple[EvidenceEntry, ...]
    review_risk: str = ""

    @classmethod
    def from_verification(
        cls,
        verification: Any,
        *,
        review_risk: str = "",
    ) -> "EvidenceLedger":
        entries = tuple(
            EvidenceEntry(
                source=str(getattr(check, "kind", "check") or "check"),
                name=str(getattr(check, "name", "") or ""),
                passed=bool(getattr(check, "passed", False)),
                evidence=str(getattr(check, "evidence", "") or "")[:12000],
            )
            for check in list(getattr(verification, "checks", None) or [])
        )
        return cls(entries, review_risk)

    def to_dict(self) -> dict:
        return {
            "review_risk": self.review_risk,
            "entries": [asdict(entry) for entry in self.entries],
            "passed": all(entry.passed for entry in self.entries),
        }
