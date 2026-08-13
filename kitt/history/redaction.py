import re
SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"),
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_\-]{12,}\b"),
]
def redact(text: str) -> str:
    value=text
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            value=pattern.sub(lambda m: (m.group(1) if m.lastindex else "")+"[REDACTED]",value)
        else: value=pattern.sub("[REDACTED]",value)
    return value
