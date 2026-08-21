"""Dreaming Mode and Memory command handlers for K.I.T.T. UI."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


def execute_dream_command(runtime, command_str: str) -> str:
    """Synchronous execution of dreaming commands for headless and tests."""
    arg = command_str.replace("/dream", "").strip()
    parts = arg.split()
    flag = parts[0].lower() if parts else ""

    if not runtime.dream_service:
        return "Dreaming Mode is not initialized."

    ws_id = getattr(runtime, "workspace_id", "local")

    if flag in ("--help", "-h", "help"):
        return (
            "Dreaming Mode\n\n"
            "/dream\n"
            "    Analyze and preview memory consolidation without writing changes.\n\n"
            "/dream --dry-run\n"
            "    Same as /dream. No persistent changes.\n\n"
            "/dream --commit\n"
            "    Consolidate and persist memory.\n\n"
            "/dream --status\n"
            "    Show Dreaming Mode status.\n\n"
            "/dream --cancel\n"
            "    Cancel the current Dreaming Mode cycle.\n\n"
            "/dream --help\n"
            "    Show this help."
        )

    if flag in ("--status", "-s", "status"):
        mem_repo = runtime.memory_repo
        last_run = mem_repo.get_last_dream_run(ws_id) if mem_repo else None
        active = mem_repo.get_all_memories(ws_id, status="ACTIVE") if mem_repo else []
        candidates = mem_repo.get_all_memories(ws_id, status="CANDIDATE") if mem_repo else []
        superseded = mem_repo.get_all_memories(ws_id, status="SUPERSEDED") if mem_repo else []
        archived = mem_repo.get_all_memories(ws_id, status="ARCHIVED") if mem_repo else []

        is_eligible = runtime.dream_scheduler.should_run(ws_id) if runtime.dream_scheduler else False
        is_running = runtime.dream_scheduler.is_dreaming if runtime.dream_scheduler else False

        last_str = "None"
        if last_run and last_run.finished_at:
            elapsed_hrs = (time.time() - last_run.finished_at) / 3600.0
            last_str = f"{elapsed_hrs:.1f}h ago ({time.strftime('%Y-%m-%d %H:%M', time.localtime(last_run.finished_at))})"

        model_name = getattr(runtime.dream_service.consolidate_phase, "model_name", "deterministic")
        model_role = getattr(runtime.config, "dream_model_role", "context")

        return (
            f"=== Dreaming Mode Status ===\n\n"
            f"  Dreaming enabled     : {'Yes' if getattr(runtime.config, 'dream_enabled', True) else 'No'}\n"
            f"  Auto dreaming enabled: {'Yes' if getattr(runtime.config, 'dream_auto_enabled', False) else 'No'}\n"
            f"  Auto commit          : {'Yes' if getattr(runtime.config, 'dream_auto_commit', False) else 'No'}\n"
            f"  Dream model role     : {model_role}\n"
            f"  Dream model          : {model_name}\n"
            f"  Fallback             : deterministic\n"
            f"  Currently running    : {'Yes' if is_running else 'No'}\n"
            f"  Last committed dream : {last_str}\n"
            f"  Eligible for auto    : {'Yes' if is_eligible else 'No'}\n\n"
            f"=== Memory Store ===\n"
            f"  Active memories      : {len(active)}\n"
            f"  Candidate memories   : {len(candidates)}\n"
            f"  Superseded memories  : {len(superseded)}\n"
            f"  Archived memories    : {len(archived)}\n"
        )

    if flag in ("--cancel", "cancel"):
        if runtime.dream_scheduler:
            runtime.dream_scheduler.cancel()
        else:
            runtime.dream_service.cancel()
        return "Dreaming Mode cancellation signal sent."

    if flag in ("--commit", "-c", "run"):
        if runtime.dream_scheduler:
            result = runtime.dream_scheduler.run_manual(ws_id, dry_run=False)
        else:
            result = runtime.dream_service.dream(ws_id, dry_run=False)

        model_name = getattr(runtime.dream_service.consolidate_phase, "model_name", "deterministic")
        model_role = getattr(runtime.config, "dream_model_role", "context")

        return (
            f"K.I.T.T. Dreaming Mode — Consolidation Complete\n\n"
            f"  Model role           : {model_role}\n"
            f"  Model                : {model_name}\n"
            f"  Sessions scanned     : {result.run.sessions_scanned}\n"
            f"  Entries scanned      : {result.run.entries_scanned}\n"
            f"  Signals found        : {result.run.signals_found}\n\n"
            f"  Memories added       : {result.run.memories_added}\n"
            f"  Memories merged      : {result.run.memories_merged}\n"
            f"  Memories superseded  : {result.run.memories_superseded}\n"
            f"  Memories archived    : {result.run.memories_archived}\n\n"
            f"  MEMORY.md            : rebuilt\n"
            f"  Database             : committed"
        )

    # Default dry run preview
    if runtime.dream_scheduler:
        result = runtime.dream_scheduler.run_manual(ws_id, dry_run=True)
    else:
        result = runtime.dream_service.dream(ws_id, dry_run=True)

    model_name = getattr(runtime.dream_service.consolidate_phase, "model_name", "deterministic")
    model_role = getattr(runtime.config, "dream_model_role", "context")

    op_counts = {"ADD": 0, "MERGE": 0, "SUPERSEDE": 0, "ARCHIVE": 0, "IGNORE": 0}
    for op in result.accepted_operations:
        op_type = getattr(op, "operation", "ADD")
        op_counts[op_type] = op_counts.get(op_type, 0) + 1
    for _op, _reason in result.rejected_operations:
        op_counts["IGNORE"] = op_counts.get("IGNORE", 0) + 1

    return (
        f"K.I.T.T. Dreaming Mode — Preview\n\n"
        f"  Model role           : {model_role}\n"
        f"  Model                : {model_name}\n"
        f"  Sessions scanned     : {result.run.sessions_scanned}\n"
        f"  Entries scanned      : {result.run.entries_scanned}\n"
        f"  Signals found        : {result.run.signals_found}\n\n"
        f"  Proposed:\n"
        f"    Add                : {op_counts.get('ADD', 0)}\n"
        f"    Merge              : {op_counts.get('MERGE', 0)}\n"
        f"    Supersede          : {op_counts.get('SUPERSEDE', 0)}\n"
        f"    Archive            : {op_counts.get('ARCHIVE', 0)}\n"
        f"    Ignore             : {op_counts.get('IGNORE', 0)}\n\n"
        f"  Persistent changes   : NO\n\n"
        f"  Use /dream --commit to apply this consolidation."
    )


async def handle_dream_command(app: KittUIApp, arg: str) -> None:
    """Handles /dream, /dream --commit, /dream --dry-run, /dream --status, /dream --cancel, /dream --help."""
    res = await app._run_blocking(execute_dream_command, app.runtime, arg)
    app._show_result(res)


async def handle_memory_extended_command(app: KittUIApp, arg: str) -> None:
    """Extended /memory command supporting list, inspect <id>, stats, forget <id>."""
    parts = arg.strip().split(maxsplit=1)
    subcmd = parts[0].lower() if parts else "list"
    subarg = parts[1].strip() if len(parts) > 1 else ""

    mem_repo = app.runtime.memory_repo
    if not mem_repo:
        app._show_result("Memory repository not available.")
        return

    ws_id = app.runtime.workspace_id

    if subcmd in ("list", "all"):
        records = mem_repo.get_all_memories(ws_id)
        if not records:
            app._show_result("No memories stored in workspace database.")
            return

        lines = [f"=== Stored Memories ({len(records)}) ==="]
        for m in records:
            pin = " [PINNED]" if m.pinned else ""
            lines.append(f"• [{m.status}] [{m.kind}] {m.id}{pin}\n  {m.content}\n")
        app._show_result("\n".join(lines))

    elif subcmd == "inspect":
        if not subarg:
            app._show_result("Usage: /memory inspect <memory_id>")
            return
        mem = mem_repo.get_memory(subarg)
        if not mem:
            app._show_result(f"Memory '{subarg}' not found.")
            return
        evidence = mem_repo.get_evidence_for_memory(subarg)
        lines = [
            f"=== Memory Inspection: {mem.id} ===",
            f"Kind: {mem.kind}",
            f"Status: {mem.status}",
            f"Pinned: {mem.pinned}",
            f"Importance: {mem.importance:.2f}",
            f"Confidence: {mem.confidence:.2f}",
            f"Access count: {mem.access_count}",
            f"Supersedes: {mem.supersedes_id or 'None'}",
            f"Created: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mem.created_at))}",
            f"Content: {mem.content}",
            "",
            f"--- Evidence ({len(evidence)}) ---",
        ]
        for ev in evidence:
            lines.append(f"• [{ev.source_kind}] Entry: {ev.session_entry_id or 'direct'}\n  Text: {ev.evidence_text}")
        app._show_result("\n".join(lines))

    elif subcmd == "stats":
        active = mem_repo.get_all_memories(ws_id, status="ACTIVE")
        candidates = mem_repo.get_all_memories(ws_id, status="CANDIDATE")
        superseded = mem_repo.get_all_memories(ws_id, status="SUPERSEDED")
        archived = mem_repo.get_all_memories(ws_id, status="ARCHIVED")
        total_access = sum(m.access_count for m in active)
        app._show_result(
            f"=== Memory Statistics ===\n\n"
            f"  Active Memories: {len(active)}\n"
            f"  Candidate Memories: {len(candidates)}\n"
            f"  Superseded Memories: {len(superseded)}\n"
            f"  Archived Memories: {len(archived)}\n"
            f"  Total Retrieval Accesses: {total_access}\n"
        )

    elif subcmd == "forget":
        if not subarg:
            app._show_result("Usage: /memory forget <memory_id>")
            return
        ok = mem_repo.set_memory_status(subarg, "ARCHIVED")
        if ok:
            app._show_result(f"Memory '{subarg}' marked as ARCHIVED.")
        else:
            app._show_result(f"Memory '{subarg}' not found.")

    else:
        app._show_result("Usage: /memory [list|inspect <id>|stats|forget <id>]")
