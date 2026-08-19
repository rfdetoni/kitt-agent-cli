from __future__ import annotations

import argparse
import json
import resource
import tempfile
import time
from pathlib import Path

from kitt.index.repository import RepositoryIndex


def run(count: int) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"kitt-scale-{count}-") as td:
        root = Path(td)
        for i in range(count):
            p = root / f"pkg_{i // 1000}" / f"module_{i}.py"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"def symbol_{i}():\n    return {i}\n", encoding="utf-8")

        index = RepositoryIndex(root, max_files=max(count + 100, 1000))
        t0 = time.perf_counter()
        index.build_or_update()
        build_s = time.perf_counter() - t0

        t1 = time.perf_counter()
        matches = index.search_symbol(f"symbol_{count // 2}")
        lookup_ms = (time.perf_counter() - t1) * 1000
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        close = getattr(index, "close", None)
        if close:
            close()
        return {
            "files": count, "cold_index_seconds": build_s,
            "symbol_lookup_ms": lookup_ms, "matches": len(matches),
            "max_rss_kb": rss_kb,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, choices=[1000, 20000, 100000], default=1000)
    args = ap.parse_args()
    print(json.dumps(run(args.files), indent=2))


if __name__ == "__main__":
    main()
