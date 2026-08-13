"""Sensitive data scanner and redaction engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple, List, Dict


SENSITIVE_PATTERNS = [
    ("PEM_PRIVATE_KEY", re.compile(r'-----BEGIN (?:RSA|EC|OPENSSH|DSA|PRIVATE) KEY-----[\s\S]+?-----END \w+ KEY-----')),
    ("API_KEY_PREFIX", re.compile(r'\b(?:sk-[a-zA-Z0-9_\-]{16,}|ghp_[a-zA-Z0-9]{36}|xoxb-[a-zA-Z0-9\-]+|glpat-[a-zA-Z0-9\-]{20,})\b')),
    ("AWS_KEY", re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ("AUTHORIZATION_HEADER", re.compile(r'(?i)\bBearer\s+[a-zA-Z0-9._\-]{15,}')),
    ("CONNECTION_STRING", re.compile(r'(?i)\b(?:postgres|mysql|mongodb|redis)://[^:\s]+:[^@\s]+@[^/\s]+\b')),
    ("ENV_SECRET", re.compile(r'(?i)\b(?:SECRET|PASSWORD|PASS|API_KEY|AUTH_TOKEN)\s*=\s*["\']?[^\s"\'#]{4,}["\']?'))
]


@dataclass(frozen=True)
class ScanResult:
    has_sensitive: bool
    categories: Tuple[str, ...]
    redaction_count: int
    clean_text: str


class SensitiveDataScanner:
    """Scans and redacts sensitive data (tokens, keys, PEMs, connection strings) from text."""

    @staticmethod
    def scan_and_redact(text: str) -> ScanResult:
        if not text:
            return ScanResult(has_sensitive=False, categories=(), redaction_count=0, clean_text="")

        categories: List[str] = []
        redaction_count = 0
        cleaned = text

        for cat, pattern in SENSITIVE_PATTERNS:
            matches = pattern.findall(cleaned)
            if matches:
                categories.append(cat)
                redaction_count += len(matches)
                cleaned = pattern.sub(f"[REDACTED_{cat}]", cleaned)

        return ScanResult(
            has_sensitive=bool(categories),
            categories=tuple(categories),
            redaction_count=redaction_count,
            clean_text=cleaned
        )
