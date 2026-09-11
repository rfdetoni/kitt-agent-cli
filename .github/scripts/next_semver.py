#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9_-]*)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:"
)


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, raw: str) -> "Version":
        match = SEMVER_RE.fullmatch(raw.strip())
        if not match:
            raise ValueError(f"invalid semantic version: {raw!r}")
        return cls(*(int(part) for part in match.groups()))

    def bump(self, level: int) -> "Version":
        if level == 3:
            return Version(self.major + 1, 0, 0)
        if level == 2:
            return Version(self.major, self.minor + 1, 0)
        if level == 1:
            return Version(self.major, self.minor, self.patch + 1)
        return self

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def latest_semver_tag() -> tuple[str, Version] | None:
    output = git("tag", "--list", "v*", "--sort=-version:refname")
    for raw in output.splitlines():
        try:
            return raw, Version.parse(raw)
        except ValueError:
            continue
    return None


def release_level(commit_range: str) -> int:
    raw = git("log", commit_range, "--format=%s%x1f%b%x1e")
    level = 0
    for record in raw.split("\x1e"):
        if not record.strip():
            continue
        subject, _, body = record.partition("\x1f")
        subject = subject.strip()
        body = body.strip()

        if subject.startswith("chore(release):"):
            continue

        match = CONVENTIONAL_RE.match(subject)
        if (match and match.group("bang")) or re.search(
            r"(?im)^BREAKING(?: |-)CHANGE:\s*", body
        ):
            return 3

        if not match:
            level = max(level, 1)
            continue

        commit_type = match.group("type").lower()
        scope = (match.group("scope") or "").lower()

        if commit_type == "feat":
            level = max(level, 2)
        elif commit_type in {
            "fix",
            "perf",
            "refactor",
            "security",
            "build",
            "ci",
            "deps",
            "revert",
        } or (commit_type == "chore" and scope in {"deps", "security"}):
            level = max(level, 1)
        elif commit_type in {"docs", "test", "tests", "style", "chore"}:
            continue
        else:
            level = max(level, 1)

    return level


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: next_semver.py CURRENT_VERSION", file=sys.stderr)
        return 2

    current = Version.parse(sys.argv[1])
    latest = latest_semver_tag()

    # Bootstrap the first release from the version already declared by the project.
    if latest is None:
        print(current)
        return 0

    tag, released = latest
    if current < released:
        raise SystemExit(f"declared version {current} is older than latest tag {tag}")

    # A version already advanced in source but not tagged should be released as-is.
    if current > released:
        print(current)
        return 0

    level = release_level(f"{tag}..HEAD")
    if level == 0:
        return 0

    print(current.bump(level))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
