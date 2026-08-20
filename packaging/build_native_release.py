#!/usr/bin/env python3
"""Build a platform KITT wheel containing Python sources + kitt_native.

The checkout remains setuptools-based so unsupported platforms can install the
pure-Python compatibility wheel without a Rust compiler. Release CI calls this
helper to build same-version platform wheels; pip prefers a compatible native
wheel and falls back to the universal wheel when no platform wheel exists.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_BUILD_SYSTEM = '''[build-system]\nrequires = ["maturin>=1.8,<2"]\nbuild-backend = "maturin"\n'''
_MATURIN = '''\n[tool.maturin]\nmanifest-path = "crates/kitt-native-python/Cargo.toml"\npython-packages = ["kitt"]\nmodule-name = "kitt_native"\nbindings = "pyo3"\nstrip = true\n'''


def _native_pyproject(source: str) -> str:
    # Replace only the leading build-system table and retain the canonical
    # [project] metadata/version/dependencies from the actual checkout.
    replaced, count = re.subn(
        r"\A\[build-system\]\n.*?(?=\n\[project\])",
        _BUILD_SYSTEM.rstrip(),
        source,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("unable to locate canonical [build-system] table")
    # The source pyproject should not carry a conflicting maturin table.
    if "[tool.maturin]" in replaced:
        raise RuntimeError("source pyproject already defines [tool.maturin]")
    return replaced.rstrip() + "\n" + _MATURIN


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "dist-native")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kitt-native-release-") as tmp:
        staging = Path(tmp) / "repo"
        shutil.copytree(
            ROOT,
            staging,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "__pycache__", ".pytest_cache", "dist*", "target"
            ),
        )
        canonical = (staging / "pyproject.toml").read_text(encoding="utf-8")
        (staging / "pyproject.toml").write_text(_native_pyproject(canonical), encoding="utf-8")
        import sys
        maturin_bin = shutil.which("maturin")
        cmd = [maturin_bin] if maturin_bin else [sys.executable, "-m", "maturin"]
        subprocess.run(
            [*cmd, "build", "--release", "--locked", "--out", str(args.out.resolve())],
            cwd=staging,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
