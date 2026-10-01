from __future__ import annotations

import re

from kitt.security.sensitive_data import SensitiveDataScanner


SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:sk|ghp|github_pat)[_-][A-Za-z0-9_\-]{12,}\b"),
]


def redact(text: str) -> str:
    value = SensitiveDataScanner.scan_and_redact(str(text or "")).clean_text
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            value = pattern.sub(
                lambda match: (
                    (match.group(1) if match.lastindex else "") + "[REDACTED]"
                ),
                value,
            )
        else:
            value = pattern.sub("[REDACTED]", value)
    return value
