"""Forced-command SSH guest endpoint: one bounded request, one JSON response.

Operator installs it INSIDE a disposable VM and pins --probe-root/--manifest.
Never expose it as a network daemon or allow arbitrary remote commands.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .probe_runner import ProbeError, run_probe

MAX_REQUEST_BYTES = 4096


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate JSON field")
        obj[key] = value
    return obj


def handle(request: dict, root: Path, manifest: Path,
           wine_binary: Path | None = None) -> dict:
    if not isinstance(request, dict) or set(request) != {"version", "method", "probe"}:
        raise ProbeError("Invalid guest request")
    if type(request["version"]) is not int or request["version"] != 1:
        raise ProbeError("Unsupported guest protocol")
    if request["method"] != "probe.run":
        raise ProbeError("Unsupported guest operation")
    receipt = run_probe(root, manifest, request["probe"], wine_binary=wine_binary)
    return {"version": 1, "receipt": receipt}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--wine-binary", type=Path)
    args = parser.parse_args()
    # Accept one request only. No interactive shell, file edits, dynamic scripts or arbitrary commands.
    raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
        print('{"version":1,"error":"Invalid request framing"}')
        raise SystemExit(2)
    try:
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite number")))
        result = handle(request, args.probe_root, args.manifest, args.wine_binary)
    except (ValueError, TypeError, OSError, ProbeError, UnicodeError) as ex:
        print(json.dumps({"version": 1, "error": str(ex)[:200]}))
        raise SystemExit(2) from ex
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
