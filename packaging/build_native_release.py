#!/usr/bin/env python3
"""Build a platform KITT wheel with the Rust extension bundled.

The normal source checkout keeps setuptools/fallback usability. This helper
creates a temporary release tree with the Maturin pyproject template so end
users receive one platform wheel and never install Rust manually.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist-native")
    parser.add_argument("--release", action="store_true", default=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kitt-native-release-") as tmp:
        staging = Path(tmp) / "repo"
        shutil.copytree(ROOT, staging, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", "dist*"))
        shutil.copy2(staging / "packaging" / "pyproject.maturin.toml", staging / "pyproject.toml")
        command = ["maturin", "build", "--release", "--out", str(args.out.resolve())]
        subprocess.run(command, cwd=staging, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
