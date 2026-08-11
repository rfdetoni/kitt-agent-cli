import re
from typing import List

class LogReducer:
    """Filters build/test execution logs to compress tokens while preserving error diagnostics."""

    NOISE_PATTERNS = [
        re.compile(r'^\s*Progress\s*\(\d+\):', re.IGNORECASE),
        re.compile(r'^\s*Downloading\s+http', re.IGNORECASE),
        re.compile(r'^\s*Download\s+[a-z0-9.:/]+', re.IGNORECASE),
        re.compile(r'^\s*\[INFO\]\s+---', re.IGNORECASE),
        re.compile(r'^\s*Warning:\s+.*duplicate', re.IGNORECASE)
    ]

    IMPORTANT_PATTERNS = [
        re.compile(r'\b(ERROR|FAIL|FAILED|FAILURE|Exception|Traceback|Caused by|AssertionError)\b', re.IGNORECASE),
        re.compile(r'^\s*File\s+"[^"]+",\s+line\s+\d+'),
        re.compile(r'^\s*at\s+[a-zA-Z0-9_.]+\([^)]+\)')
    ]

    def reduce_log(self, log_text: str, max_lines: int = 40) -> str:
        if not log_text:
            return ""

        lines = log_text.splitlines()
        filtered_lines: List[str] = []

        for line in lines:
            if any(np.match(line) for np in self.NOISE_PATTERNS):
                continue
            if any(ip.search(line) for ip in self.IMPORTANT_PATTERNS) or len(filtered_lines) < 5:
                filtered_lines.append(line)

        if len(filtered_lines) > max_lines:
            truncated = filtered_lines[:max_lines // 2] + [f"\n... [{len(filtered_lines) - max_lines} log lines omitted for token efficiency] ...\n"] + filtered_lines[-max_lines // 2:]
            return "\n".join(truncated)

        return "\n".join(filtered_lines)
