"""Session and history command handlers for K.I.T.T. UI."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


async def handle_resume_command(app: KittUIApp, arg: str) -> None:
    target = arg or "1"
    conversation = await app._run_blocking(app.runtime.history.resume_conversation, target)
    if conversation:
        await app._load_conversation(conversation)
        app._show_result(f"Resumed: {conversation['title']}")
    else:
        app._show_result(f"Conversation not found: {target}")


async def handle_fork_command(app: KittUIApp, arg: str) -> None:
    conversation = await app._run_blocking(
        app.runtime.history.fork_conversation,
        title_suffix=f" ({arg})" if arg else " (Fork)"
    )
    app._show_result(f"Forked: {conversation['title']}")


async def handle_export_command(app: KittUIApp, arg: str) -> None:
    fmt = arg.strip().lower() or "markdown"
    if fmt not in ("markdown", "json", "md"):
        app._show_result("Usage: /export [markdown|json]")
    else:
        await app._export_conversation(fmt)


async def handle_compact_command(app: KittUIApp, arg: str) -> None:
    conversation = app.runtime.history.get_or_create_active()
    result = await app._run_blocking(app.runtime.compaction.compact, conversation["id"], 4)
    app._show_result("History compacted." if result else "History already small.")


async def handle_stats_command(app: KittUIApp) -> None:
    stats = await app._run_blocking(app.runtime.history.repo.get_telemetry_stats)
    app._show_result(f"Turns: {stats['count']}  Input: {stats['input']}  Output: {stats['output']}  Saved: {stats['saved']}")


def handle_status_command(app: KittUIApp) -> None:
    snapshot = app.runtime.snapshot()
    app._show_result(
        f"Workspace: {snapshot.workspace_id}\n"
        f"Conversation: {snapshot.active_conversation_id}\n"
        f"Pending actions: {snapshot.pending_actions}\n"
        f"Queued inputs: {snapshot.queued_inputs}"
    )
