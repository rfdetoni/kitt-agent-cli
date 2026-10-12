"""Fixed-script Frida tracer for operator-pinned binaries INSIDE a disposable VM.

Never registered as a model-callable MCP tool. No arbitrary JS or PID attach.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import threading
import time

from .probe_runner import _pin_file, NAME_RE, ProbeError

MAX_EVENTS = 512
MAX_TRACE_SECONDS = 10


class TraceError(ValueError):
    pass


def trace_guest(root: Path, manifest_file: Path, name: str,
                *, device=None, duration: float = MAX_TRACE_SECONDS) -> dict:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise TraceError("Invalid probe name")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < duration <= MAX_TRACE_SECONDS:
        raise TraceError("Invalid trace duration")
    root = root.resolve(strict=True)
    if (manifest_file.is_symlink() or not manifest_file.is_file()
            or manifest_file.stat().st_size > 64 * 1024):
        raise TraceError("Invalid manifest")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as ex:
        raise TraceError("Invalid manifest JSON") from ex
    entry = manifest.get("probes", {}).get(name)
    if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
        raise TraceError("Unknown probe")
    binary = _pin_file(root, entry["path"], entry["sha256"])
    if device is None:
        try:
            import frida  # optional dependency, intentionally guest-only
        except ImportError as ex:
            raise TraceError("Install Frida only inside the test VM") from ex
        device = frida.get_local_device()

    script_text = Path(__file__).with_name("frida_scripts").joinpath("win32_events.js").read_text("utf-8")
    done = threading.Event()
    events: list[dict] = []
    overflow = False

    def on_message(message, _data):
        nonlocal overflow
        if not isinstance(message, dict) or message.get("type") != "send":
            return
        payload = message.get("payload")
        if (not isinstance(payload, dict) or payload.get("kind") != "api_enter"
                or payload.get("api") not in {"CreateEventW", "SetEvent", "WaitForSingleObject"}):
            return
        if len(events) >= MAX_EVENTS:
            overflow = True
        else:
            events.append({"api": payload["api"]})

    pid = None
    session = None
    start = time.monotonic()
    try:
        pid = device.spawn([str(binary)])
        session = device.attach(pid)
        session.on("detached", lambda *_: done.set())
        script = session.create_script(script_text)
        script.on("message", on_message)
        script.load()
        device.resume(pid)
        done.wait(duration)
    except Exception as ex:
        raise TraceError(f"Frida guest trace failed: {type(ex).__name__}") from ex
    finally:
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass
        if pid is not None:
            try:
                device.kill(pid)
            except Exception:
                pass
    return {
        "schema_version": 1, "probe": name, "binary_sha256": entry["sha256"],
        "events": events, "truncated": overflow, "duration_ms": round((time.monotonic()-start)*1000),
        "observation_only": True, "guest_attested": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--probe", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(trace_guest(args.probe_root, args.manifest, args.probe)))
    except (ProbeError, TraceError, OSError) as ex:
        parser.error(str(ex))


if __name__ == "__main__":
    main()
