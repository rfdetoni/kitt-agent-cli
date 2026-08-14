from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from kitt.index.repository import RepositoryIndex


def _make_fixture(root: Path, files: int) -> None:
    for i in range(files):
        path = root / f"pkg/mod_{i}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"def symbol_{i}():\n    return {i}\n", encoding="utf-8")


def _time_ms(call):
    started = time.perf_counter()
    result = call()
    return (time.perf_counter() - started) * 1000, result


def run_once(files: int) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_fixture(root, files)
        index = RepositoryIndex(root, in_memory=True, max_files=files + 1)
        cold_ms, cold_stats = _time_ms(index.build_or_update)
        warm_ms, warm_stats = _time_ms(index.build_or_update)
        search_ms, results = _time_ms(lambda: index.search_text(f"symbol_{files - 1}", limit=5))
        index.close()
        return {
            "files": files,
            "cold_ms": round(cold_ms, 2),
            "warm_ms": round(warm_ms, 2),
            "search_ms": round(search_ms, 2),
            "cold": cold_stats,
            "warm": warm_stats,
            "top_path": results[0]["path"] if results else None,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", default="1000")
    args = parser.parse_args(argv)
    sizes = [int(part) for part in args.files.split(",") if part.strip()]
    print(json.dumps([run_once(size) for size in sizes], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
