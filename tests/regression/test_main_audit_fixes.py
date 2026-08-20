from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_arun_turn_does_not_block_event_loop():
    from kitt.core.turn_processor import TurnProcessor

    processor = object.__new__(TurnProcessor)

    def blocking_turn(_cmd):
        time.sleep(0.15)
        yield "done"

    processor.run_turn = blocking_turn
    cmd = SimpleNamespace(turn_id="turn_async_bridge")
    ticks: list[float] = []

    async def scenario():
        async def heartbeat():
            for _ in range(4):
                await asyncio.sleep(0.025)
                ticks.append(time.monotonic())

        async def consume():
            return [item async for item in processor.arun_turn(cmd)]

        consumer = asyncio.create_task(consume())
        beat = asyncio.create_task(heartbeat())
        result = await consumer
        await beat
        return result

    result = asyncio.run(scenario())
    assert result == ["done"]
    # At least one heartbeat must execute while run_turn is sleeping.
    assert ticks


def test_child_payload_uses_shared_state_root(tmp_path: Path):
    from kitt.children.manager import ChildAgentManager

    manager = object.__new__(ChildAgentManager)
    manager.root = tmp_path / "execution"
    manager.state_root = tmp_path / "durable-state"
    manager.coordinator = None
    manager._child_security_context = lambda child: SimpleNamespace(to_dict=lambda: {"ok": True})
    child = SimpleNamespace(
        id="child_a",
        runtime_conversation_id="conv_child",
        allowed_paths=[],
    )
    payload = manager._build_run_payload(child, "task")
    assert payload["root"] == str(manager.root)
    assert payload["state_root"] == str(manager.state_root)


def test_fallback_refuses_non_python_structural_edit(tmp_path: Path):
    from kitt.native import fallback

    source = tmp_path / "Thing.java"
    source.write_text("class Thing {\n  void run() {}\n}\n", encoding="utf-8")
    symbols = fallback.find_symbols(tmp_path, "Thing", 10)
    assert symbols
    before = source.read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="requires the KITT native engine"):
        fallback.replace_symbol(tmp_path, symbols[0]["id"], "class Other {}")
    assert source.read_text(encoding="utf-8") == before


def test_output_optimizer_final_output_never_expands():
    from kitt.native.output import OutputOptimizer

    class Engine:
        def compress_output(self, argv, stdout, stderr, returncode):
            raw = stdout if not stderr else stdout + "\n" + stderr
            return {
                "output": "x",
                "changed": True,
                "family": "generic",
                "raw_bytes": len(raw.encode()),
                "output_bytes": 1,
                "omitted_lines": 10,
                "raw_sha256": "abc",
            }

    class Store:
        def put(self, *args, **kwargs):
            return SimpleNamespace(id="art_" + "a" * 80)

    raw = "0123456789"
    result = OutputOptimizer(Engine()).optimize(
        ["tool"], raw, "", 0, artifact_store=Store(),
        workspace_id="ws", conversation_id="conv", turn_id="turn",
    )
    assert len(result.output.encode()) <= len(raw.encode())
    assert result.output_bytes == len(result.output.encode())


def test_atomic_write_lease_allows_only_one_owner(tmp_path: Path):
    from kitt.history.database import HistoryDatabase
    from kitt.native.coordinator import CoordinationConflict, WorkspaceCoordinator

    db = HistoryDatabase(str(tmp_path))
    c1 = WorkspaceCoordinator(str(tmp_path), str(tmp_path), db, "ws")
    c2 = WorkspaceCoordinator(str(tmp_path), str(tmp_path), db, "ws")
    barrier = threading.Barrier(2)
    results: list[str] = []

    def acquire(coord, owner):
        barrier.wait()
        try:
            coord.acquire("symbol:x", owner, "WRITE", "test", ttl_seconds=30)
            results.append("ok")
        except CoordinationConflict:
            results.append("conflict")

    threads = [threading.Thread(target=acquire, args=(c1, "a")), threading.Thread(target=acquire, args=(c2, "b"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    db.close()
    assert sorted(results) == ["conflict", "ok"]


def test_knowledge_links_cannot_cross_workspace(tmp_path: Path):
    from kitt.history.database import HistoryDatabase
    from kitt.native.storage import NativeStateRepository

    db = HistoryDatabase(str(tmp_path))
    one = NativeStateRepository(db, "workspace-one")
    two = NativeStateRepository(db, "workspace-two")
    a = one.upsert_concept("A", "A definition")
    b = two.upsert_concept("B", "B definition")
    with pytest.raises(PermissionError):
        one.add_link(a.id, b.id, "RELATED_TO")
    db.close()


def test_memory_query_does_not_mark_correction_as_applied(tmp_path: Path):
    from kitt.history.database import HistoryDatabase
    from kitt.native.memory import HybridMemoryService
    from kitt.native.storage import NativeStateRepository

    db = HistoryDatabase(str(tmp_path))
    state = NativeStateRepository(db, "ws")
    correction_id = state.add_correction("build tool", "gradle", "maven")

    class MemoryRepo:
        def get_active_memories(self, workspace_id):
            return []

    service = HybridMemoryService(SimpleNamespace(), MemoryRepo(), state)
    service.query("build tool maven", limit=5)
    row = next(item for item in state.list_corrections() if item["id"] == correction_id)
    db.close()
    assert row["applied_count"] == 0
