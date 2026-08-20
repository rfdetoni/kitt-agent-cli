#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPES = [ROOT / "kitt" / "native", ROOT / "crates"]
FORBIDDEN = ("rtk-ai", "rtk_ai", "icm-core", "icm_store", "grit::", "grit_")


def main() -> int:
    failures = []
    for base in SCOPES:
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".rs", ".toml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore").casefold()
            for token in FORBIDDEN:
                if token.casefold() in text:
                    failures.append(f"{path.relative_to(ROOT)}: forbidden production identifier {token!r}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("clean-room production-source guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
