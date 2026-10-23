"""Bounded, deterministic summaries of build and test logs.

The full tool output remains available through the artifact retention mechanism.
"""
from __future__ import annotations

import re


class LogReducer:
    """Preserve actionable diagnostics and terminal status within a line budget."""

    NOISE_PATTERNS = (
        re.compile(r"^\s*Progress\s*\(\d+\):", re.IGNORECASE),
        re.compile(r"^\s*Downloading\s+http", re.IGNORECASE),
        re.compile(r"^\s*Download\s+[a-z0-9.:/]+", re.IGNORECASE),
        re.compile(r"^\s*\[INFO\]\s+---", re.IGNORECASE),
        re.compile(r"^\s*Warning:\s+.*duplicate", re.IGNORECASE),
    )
    ERROR = re.compile(
        r"\b(?:ERROR|FAIL|FAILED|FAILURE|Exception|Traceback|Caused by|"
        r"AssertionError|panic:|fatal:)\b|^\s*error:",
        re.IGNORECASE,
    )
    IMPORTANT = re.compile(
        r"\b(?:ERROR|FAIL|FAILED|FAILURE|Exception|Traceback|Caused by|"
        r"AssertionError|panic:|fatal:)\b|^\s*error:|"
        r"(?:^|\s)(?:[\w./\\-]+:\d+(?::\d+)?):?\s|"
        r"^\s*File\s+\"[^\"]+\",\s+line\s+\d+|"
        r"^\s*at\s+[\w.$]+\([^)]+\)",
        re.IGNORECASE,
    )
    STATUS = re.compile(
        r"\b(?:BUILD (?:FAILURE|FAILED|SUCCESS|SUCCESSFUL)|FAILURES!|"
        r"exit (?:status|code)|Tests run:)\b|\b\d+\s+(?:passed|failed|skipped)\b",
        re.IGNORECASE,
    )

    def reduce_log(self, log_text: str, max_lines: int = 40) -> str:
        """Retain first error context and last outcome, even for very long logs.

        Priority is based on source line numbers. Selected lines are emitted in
        original order, and a truncation marker counts against the line budget.
        """
        if not log_text or max_lines <= 0:
            return ""

        lines = log_text.splitlines()
        visible = [
            i for i, line in enumerate(lines)
            if not any(pattern.match(line) for pattern in self.NOISE_PATTERNS)
            or self.IMPORTANT.search(line)
        ]
        if len(visible) <= max_lines:
            return "\n".join(lines[i] for i in visible)

        limit = max_lines - 1 if max_lines > 1 else 1
        candidates: list[int] = []
        seen: set[int] = set()
        visible_set = set(visible)

        def offer(index: int) -> None:
            if index in visible_set and index not in seen:
                seen.add(index)
                candidates.append(index)

        errors = [i for i in visible if self.ERROR.search(lines[i])]
        if errors:
            first = errors[0]
            offer(first)
            # Keep the multiline compiler/traceback context following the first
            # failure without consuming the whole budget on repeated errors.
            for index in range(first + 1, min(len(lines), first + 6)):
                if not lines[index].strip():
                    break
                offer(index)
            offer(first - 1)

        status = [i for i in visible if self.STATUS.search(lines[i])]
        for index in reversed(status[-2:]):
            offer(index)
        for index in visible[-2:]:
            offer(index)
        for index in visible[:5]:
            offer(index)
        for index in visible:
            if self.IMPORTANT.search(lines[index]):
                offer(index)

        indices = sorted(candidates[:limit])
        omitted = len(visible) - len(indices)
        if omitted <= 0:
            return "\n".join(lines[i] for i in indices)
        marker = f"... [{omitted} log lines omitted for token efficiency] ..."
        if max_lines == 1:
            return lines[indices[0]]
        result: list[str] = []
        inserted = False
        previous = -1
        for index in indices:
            if not inserted and index > previous + 1:
                result.append(marker)
                inserted = True
            result.append(lines[index])
            previous = index
        if not inserted:
            result.insert(max(0, len(result) - 1), marker)
        return "\n".join(result)
