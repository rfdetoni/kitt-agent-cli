from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import io
import json
import subprocess
import time

import pytest

from kitt.native.bridge import NativeCodeEngine
from kitt.core.run_coordinator import RunCoordinator
from kitt.ui.daemon_bridge import DaemonUIBridge
from kitt.llm.domain import ProviderProtocolError
from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.kitt_reverse_proxy import KittReverseProxyAdapter
from kitt.memory.shared_client import KittMemoryClient, KittMemoryUnavailable
from kitt.runtime.search_fallback import full_scan_search


def test_fallback_search_refuses_external_links_and_gitignored_files(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    (tmp_path / "external.txt").write_text("BOUNDARY_MARKER")
    (root / "linked.txt").symlink_to(tmp_path / "external.txt")
    (root / ".gitignore").write_text("ignored.txt\n")
    (root / "ignored.txt").write_text("BOUNDARY_MARKER")
    (root / "allowed.txt").write_text("BOUNDARY_MARKER")
    engine = NativeCodeEngine(root)
    for regex in (False, True):
        found = engine.search("BOUNDARY_MARKER", regex=regex)
        assert [hit["path"] for hit in found["hits"]] == ["allowed.txt"]
    with pytest.raises(PermissionError):
        engine.read_symbol("linked.txt::marker")


def test_regex_deadline_and_scope(tmp_path):
    (tmp_path / "evil.txt").write_text("a" * 40 + "!")
    started = time.monotonic()
    with pytest.raises(ValueError, match="deadline"):
        full_scan_search(tmp_path, {"query": "^(a+)+$", "regex": True})
    assert time.monotonic() - started < 6
    found = full_scan_search(tmp_path, {"query": "a+", "regex": True}, path_allowed=lambda _: False)
    assert found["hits"] == []


@pytest.mark.parametrize("operation", ["repo.move", "repo.rename"])
@pytest.mark.parametrize("arguments", [{"source": "a.py", "destination": "b.py"}, {"path": "a.py", "target": "b.py"}])
def test_move_reserves_both_paths(operation, arguments):
    assert RunCoordinator.mutation_paths("kitt_runtime", {"operation": operation, "arguments": arguments}) == ["a.py", "b.py"]


def test_daemon_cursor_advances_only_after_delivery_and_deduplicates():
    delivered = []
    bridge = DaemonUIBridge(".", event_sink=delivered.append)
    bridge.attached_session_id = "session"
    event = SimpleNamespace(session_id="session", sequence_id=1, event_type="TextDelta", payload={"text": "one"})
    bridge._on_wire_event(event)
    bridge._on_wire_event(event)
    assert len(delivered) == 1
    bridge.event_sink = lambda _: (_ for _ in ()).throw(RuntimeError("sink failure"))
    event.sequence_id = 2
    with pytest.raises(RuntimeError):
        bridge._on_wire_event(event)
    assert bridge.last_sequence_id == 1


@pytest.mark.asyncio
async def test_daemon_replay_buffers_live_events_until_history_is_delivered():
    delivered = []
    bridge = DaemonUIBridge(".", event_sink=lambda event: delivered.append(event.delta))
    events = [SimpleNamespace(session_id="session", sequence_id=n, event_type="TextDelta", payload={"delta": str(n)}) for n in (1, 2)]
    async def attach(*args, **kwargs):
        kwargs["on_event"](events[1])
        return {"status": "ok", "events": [events[0]], "has_more": False}
    bridge.client = SimpleNamespace(attach=attach)
    assert await bridge.attach("session")
    assert delivered == ["1", "2"]


def test_invalid_utf8_never_becomes_a_tool_path():
    chunk = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_test", "function": {"name": "read_file", "arguments": '{"path":"badPLACEHOLDER.txt"}'}}]}}]}
    raw = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n").encode().replace(b"PLACEHOLDER", b"\xff")
    with patch("kitt.llm.providers.kitt_reverse_proxy.secure_urlopen", return_value=io.BytesIO(raw)):
        with pytest.raises(ProviderProtocolError, match="UTF-8"):
            list(KittReverseProxyAdapter().stream(LLMRequest(model="fixture", messages=[])))


def test_memory_budget_is_total_and_nested():
    client = KittMemoryClient()
    with client.request_budget(0):
        with pytest.raises(KittMemoryUnavailable, match="budget"):
            client._deadline(6)
    assert client._deadline(6) > time.monotonic()
