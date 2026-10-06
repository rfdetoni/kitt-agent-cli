"""Disposable regex worker: the parent kills and reaps it at the deadline."""
import json
import sys

from kitt.runtime.search_fallback import full_scan_search

if __name__ == "__main__":
    request = json.load(sys.stdin)
    allowed = frozenset(request["paths"])
    try:
        result = full_scan_search(request["root"], request["args"], path_allowed=allowed.__contains__, _isolated=True)
    except (ValueError, OSError) as exc:
        result = {"error": str(exc)}
    print(json.dumps(result))
