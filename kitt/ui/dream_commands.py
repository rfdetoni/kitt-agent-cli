"""Dreaming Mode and Memory command handlers for K.I.T.T. UI."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


async def handle_dream_command(app: KittUIApp, arg: str) -> None:
    """Handles /dream, /dream status, /dream inspect, /dream run, /dream cancel."""
    parts = arg.strip().split()
    subcmd = parts[0].lower() if parts else "status"

    if not app.runtime.dream_service:
        app._show_result("Dreaming Mode is not initialized.")
        return

    ws_id = app.runtime.workspace_id

    if subcmd == "status":
        mem_repo = app.runtime.memory_repo
        last_run = mem_repo.get_last_dream_run(ws_id) if mem_repo else None
        active = mem_repo.get_all_memories(ws_id, status="ACTIVE") if mem_repo else []
        candidates = mem_repo.get_all_memories(ws_id, status="CANDIDATE") if mem_repo else []
        superseded = mem_repo.get_all_memories(ws_id, status="SUPERSEDED") if mem_repo else []
        archived = mem_repo.get_all_memories(ws_id, status="ARCHIVED") if mem_repo else []

        is_eligible = app.runtime.dream_scheduler.should_run(ws_id) if app.runtime.dream_scheduler else False
        is_running = app.runtime.dream_scheduler.is_dreaming if app.runtime.dream_scheduler else False

        last_str = "None"
        if last_run and last_run.finished_at:
            elapsed_hrs = (time.time() - last_run.finished_at) / 3600.0
            last_str = f"{elapsed_hrs:.1f}h ago ({time.strftime('%Y-%m-%d %H:%M', time.localtime(last_run.finished_at))})"

        app._show_result(
            f"=== Dreaming Mode Status ===\n\n"
            f"  Enabled: {'Yes' if getattr(app.runtime.config, 'dream_enabled', True) else 'No'}\n"
            f"  Auto Scheduler: {'Yes' if getattr(app.runtime.config, 'dream_auto_enabled', False) else 'No'}\n"
            f"  Currently Running: {'Yes' if is_running else 'No'}\n"
            f"  Last Run: {last_str}\n"
            f"  Eligible for Auto-Dream: {'Yes' if is_eligible else 'No'}\n\n"
            f"=== Memory Store ===\n"
            f"  Active: {len(active)}\n"
            f"  Candidates: {len(candidates)}\n"
            f"  Superseded: {len(superseded)}\n"
            f"  Archived: {len(archived)}\n\n"
            f"Commands:\n"
            f"  /dream inspect   - Dry-run inspect proposed consolidations\n"
            f"  /dream run       - Run manual dream consolidation and commit\n"
            f"  /dream cancel    - Cancel active background dream run"
        )

    elif subcmd == "inspect":
        app._show_result("Dreaming inspection in progress (dry-run)...")
        try:
            result = await app._run_blocking(app.runtime.dream_service.dream, ws_id, dry_run=True)
            lines = [
                f"=== Dreaming Mode Inspection (Dry Run) ===",
                f"Sessions scanned: {result.run.sessions_scanned}",
                f"Signals found: {result.run.signals_found}",
                f"Proposals accepted: {len(result.accepted_operations)}",
                f"Proposals rejected: {len(result.rejected_operations)}",
                "",
                "--- Proposed Operations ---",
            ]
            if not result.accepted_operations:
                lines.append("  (No new consolidation operations proposed)")
            for op in result.accepted_operations:
                lines.append(f"  [{op.operation}] ({op.proposed_kind}) {op.proposed_content} (conf: {op.confidence:.2f})")

            if result.rejected_operations:
                lines.append("\n--- Rejected Proposals ---")
                for op, reason in result.rejected_operations:
                    lines.append(f"  [{op.operation}] {op.proposed_content} -> Reason: {reason}")

            app._show_result("\n".join(lines))
        except Exception as exc:
            app._show_result(f"Dream inspection failed: {exc}")

    elif subcmd == "run":
        app._show_result("Executing Dreaming Mode consolidation & commit...")
        try:
            result = await app._run_blocking(app.runtime.dream_service.dream, ws_id, dry_run=False)
            app._show_result(
                f"=== Dreaming Mode Completed ===\n\n"
                f"  Memories added: {result.run.memories_added}\n"
                f"  Memories merged: {result.run.memories_merged}\n"
                f"  Memories superseded: {result.run.memories_superseded}\n"
                f"  Memories archived: {result.run.memories_archived}\n"
                f"  Materialized view updated: .kitt/memory/MEMORY.md"
            )
        except Exception as exc:
            app._show_result(f"Dream execution failed: {exc}")

    elif subcmd == "cancel":
        app.runtime.dream_service.cancel()
        app._show_result("Dreaming Mode cancellation signal sent.")

    else:
        app._show_result("Usage: /dream [status|inspect|run|cancel]")


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
