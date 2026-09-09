from __future__ import annotations

from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_MEMORY_READ


def _conversation(repo: HistoryRepository, workspace_id: str, title: str, text: str):
    conv = repo.create_conversation(workspace_id, title=title)
    repo.save_message(conv["id"], f"turn-{conv['id'][:8]}", "user", text)
    return conv


def test_session_search_is_workspace_scoped_compact_and_capability_guarded(tmp_path):
    db = HistoryDatabase(":memory:", in_memory=True)
    repo = HistoryRepository(db)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first_ws = repo.get_or_create_workspace(str(first_root))
    second_ws = repo.get_or_create_workspace(str(second_root))

    target = _conversation(
        repo,
        first_ws["id"],
        "Reverse proxy lifecycle",
        "The browser should migrate to headless after authentication without losing the session.",
    )
    _conversation(
        repo,
        second_ws["id"],
        "Secret other workspace",
        "headless authentication should never leak across workspaces",
    )

    runtime = SafeRuntime(
        workspace_root=first_root,
        workspace_id=first_ws["id"],
        conversation_id=target["id"],
        db=db,
    )

    denied = runtime.execute(
        "session.search",
        {"query": "headless authentication"},
        effective_capabilities=set(),
    )
    assert denied.success is False
    assert "memory.read" in str(denied.error)

    result = runtime.execute(
        "session.search",
        {"query": "headless authentication", "limit": 10, "max_tokens": 256},
        effective_capabilities={CAP_MEMORY_READ},
    )
    assert result.success is True
    assert result.metadata["workspace_scoped"] is True
    assert result.metadata["max_tokens"] == 256
    assert result.data
    assert {row["id"] for row in result.data} == {target["id"]}

    hit = result.data[0]
    assert hit["match_source"] == "message"
    assert "headless" in hit["match_snippet"].lower()
    assert "content" not in hit
    assert "messages" not in hit
    assert "compact_summary" not in hit
    assert len(hit["match_snippet"]) <= 480

    db.close()
