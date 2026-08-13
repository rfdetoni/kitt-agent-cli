from __future__ import annotations

import argparse
import asyncio
import sys

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.ui.capabilities import create_backend
from kitt.ui.fallback import HeadlessUI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kitt", description="K.I.T.T. autonomous coding agent")
    parser.add_argument("-p", "--print", dest="prompt", help="Print one response and exit")
    parser.add_argument("--root", default=".", help="Workspace root")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--ui", choices=["auto", "tui", "plain"], default="auto")
    parser.add_argument("--plain", action="store_true", help="Alias for --ui plain")
    parser.add_argument("--no-animation", action="store_true")
    return parser


async def async_main(args) -> int:
    config = RuntimeConfig(history_enabled=not args.no_history, persistence_enabled=not args.no_history)
    runtime = KittRuntime.build(args.root, config=config)
    backend = HeadlessUI(runtime, args.prompt) if args.prompt is not None else create_backend(
        runtime, "plain" if args.plain else args.ui, no_animation=args.no_animation,
    )
    code = 1
    errors = []
    try:
        code = await backend.run_async()
    finally:
        try: await backend.shutdown()
        except BaseException as exc: errors.append(exc)
        try: runtime.close()
        except BaseException as exc: errors.append(exc)
    if errors:
        raise RuntimeError("Shutdown failed: " + "; ".join(map(str, errors)))
    return code


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
