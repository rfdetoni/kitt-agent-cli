from __future__ import annotations

import json
import tempfile
from pathlib import Path

from kitt.index.repository import RepositoryIndex


CASES = (
    ("python exact symbol", "fix target symbol", "app.py"),
    ("tail content", "rare tail marker", "tail.py"),
)


def run_eval() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "app.py").write_text("def target_symbol():\n    return 1\n", encoding="utf-8")
        (root / "tail.py").write_text(("x = 1\n" * 900) + "rare_tail_marker = True\n", encoding="utf-8")
        index = RepositoryIndex(root, in_memory=True)
        index.build_or_update()
        hits = 0
        details = []
        for name, query, expected_path in CASES:
            results = index.search_text(query, limit=5)
            paths = [row["path"] for row in results]
            ok = expected_path in paths
            hits += int(ok)
            details.append({"case": name, "query": query, "expected": expected_path, "paths": paths, "ok": ok})
        index.close()
        return {"cases": len(CASES), "recall_at_5": hits / len(CASES), "details": details}


def main() -> int:
    print(json.dumps(run_eval(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
