import sqlite3
from contextlib import contextmanager

import pytest

from kitt.core.resource_coordinator import ExecutionResource, ResourceCoordinator
from kitt.native.coordinator import CoordinationConflict, WorkspaceCoordinator
from kitt.native.storage import NativeStateRepository


class Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")

    @contextmanager
    def get_connection(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


def _coordinator(tmp_path):
    db = Db()
    NativeStateRepository(db, "ws")
    workspace = WorkspaceCoordinator(str(tmp_path), str(tmp_path), db, "ws")
    return ResourceCoordinator(workspace)


def test_resource_mapping_is_typed_and_conversation_scoped(tmp_path):
    coordinator = _coordinator(tmp_path)
    assert coordinator.resources_for_tool(
        "kitt_runtime",
        {"operation": "process.start", "arguments": {}},
        conversation_id="conv-1",
    ) == [ExecutionResource("terminal", "conv-1")]
    assert coordinator.resources_for_tool(
        "kitt_runtime",
        {"operation": "browser.open", "arguments": {"session_id": "browser-1"}},
        conversation_id="conv-1",
    ) == [ExecutionResource("browser", "browser-1")]
    assert coordinator.resources_for_tool(
        "kitt_runtime",
        {"operation": "mcp.call", "arguments": {"server": "figma"}},
        conversation_id="conv-1",
    ) == [ExecutionResource("mcp", "figma")]


def test_same_resource_write_conflicts_but_distinct_resources_can_progress(tmp_path):
    coordinator = _coordinator(tmp_path)
    terminal = ExecutionResource("terminal", "conv-1")
    browser = ExecutionResource("browser", "conv-1")

    first = coordinator.acquire(terminal, "owner-a", wait_timeout=0)
    assert first.resource_id == "terminal:conv-1"

    with pytest.raises(CoordinationConflict):
        coordinator.acquire(terminal, "owner-b", wait_timeout=0)

    second = coordinator.acquire(browser, "owner-b", wait_timeout=0)
    assert second.resource_id == "browser:conv-1"

    assert coordinator.release_owner("owner-a") == 1
    reacquired = coordinator.acquire(terminal, "owner-b", wait_timeout=0)
    assert reacquired.owner_id == "owner-b"


def test_acquire_many_is_deterministic_and_atomic(tmp_path):
    coordinator = _coordinator(tmp_path)
    resources = [
        ExecutionResource("mcp", "server-z"),
        ExecutionResource("browser", "session-a"),
    ]
    grants = coordinator.acquire_many(resources, "owner-a", wait_timeout=0)
    assert [grant.resource_id for grant in grants] == [
        "browser:session-a",
        "mcp:server-z",
    ]

    with pytest.raises(CoordinationConflict):
        coordinator.acquire_many(
            [ExecutionResource("mcp", "server-z"), ExecutionResource("terminal", "conv-2")],
            "owner-b",
            wait_timeout=0,
        )
