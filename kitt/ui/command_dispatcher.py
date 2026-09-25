from __future__ import annotations

import os
import shlex

from kitt.ui.model_commands import (
    handle_model_command, handle_setup_models_command, handle_add_provider_command,
    handle_edit_provider_command, handle_delete_provider_command, handle_local_limits_command,
)
from kitt.ui.session_commands import (
    handle_resume_command, handle_fork_command, handle_export_command,
    handle_compact_command, handle_stats_command, handle_gain_command, handle_status_command,
)
from kitt.ui.skill_commands import (
    handle_setup_skills_command, handle_skill_install_command, handle_skill_remove_command,
    handle_remember_command, handle_clear_memory_command, handle_doctor_command,
)
from kitt.ui.dream_commands import handle_dream_command, handle_memory_extended_command

async def _execute_command(ui, raw: str) -> bool:
    parts = raw.split(maxsplit=1)
    name = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    found = ui.commands.find(name)
    if not found:
        return False
    if found.id == "quit":
        ui.request_exit()
    elif found.id == "clear":
        ui._new_conversation()
        ui._show_result("Started a clean conversation.")
    elif found.id == "help":
        ui.open_overlay("help", ui.help_control)
    elif found.id == "model":
        await handle_model_command(ui, arg)
    elif found.id == "setup_skills":
        await handle_setup_skills_command(ui, arg)
    elif found.id == "setup_models":
        await handle_setup_models_command(ui, arg)
    elif found.id == "add_provider":
        await handle_add_provider_command(ui, arg)
    elif found.id == "edit_provider":
        await handle_edit_provider_command(ui, arg)
    elif found.id == "delete_provider":
        await handle_delete_provider_command(ui, arg)
    elif found.id == "local_limits":
        await handle_local_limits_command(ui, arg)
    elif found.id in {"mode", "toggle_mode"}:
        if arg:
            ui.toggle_turn_mode(arg.strip())
        else:
            ui.toggle_turn_mode()
    elif found.id == "new":
        ui._new_conversation()
    elif found.id == "history":
        await ui._show_active_history()
    elif found.id == "thread":
        await ui._show_history(arg)
    elif found.id in {"resume"}:
        await handle_resume_command(ui, arg)
    elif found.id == "conversation":
        conversation = ui.runtime.history.get_or_create_active()
        ui._show_result(f"Active conversation\n{conversation['id']}\n{conversation['title']}")
    elif found.id == "plan":
        if arg:
            if ui.state.is_thinking or (ui.bridge and ui.bridge.is_active):
                return True
            ui.state.is_thinking = True
            conversation = ui.runtime.history.get_or_create_active()
            try:
                await ui.bridge.start(arg, conversation["id"], explicit_files=ui.explicit_files, no_history=not ui.runtime.config.history_enabled, mode="plan")
            except Exception as err:
                ui.state.is_thinking = False
                ui.state.add_toast(f"Turn Error: {err}")
        else:
            ui.state.planning_mode = not ui.state.planning_mode
            status = "ATIVADO (Modo Leitura / Planejamento)" if ui.state.planning_mode else "DESATIVADO (Modo Normal / Execução)"
            ui._show_result(f"Modo de Planejamento: {status}")
    elif found.id == "fork":
        await handle_fork_command(ui, arg)
    elif found.id in ("export", "export_conversation"):
        await handle_export_command(ui, arg)
    elif found.id == "memory":
        if arg:
            await handle_memory_extended_command(ui, arg)
        else:
            ui._show_result(ui.runtime.memory.get_memory_context() or "No memory entries.")
    elif found.id == "dream":
        await handle_dream_command(ui, arg)
    elif found.id == "remember":
        await handle_remember_command(ui, arg)
    elif found.id == "clear_memory":
        await handle_clear_memory_command(ui)
    elif found.id == "skills":
        skills = ui.runtime.skills.list_skills()
        ui._show_result("\n".join(f"{s.name} v{s.version} — {s.author}" for s in skills) or "No skills installed.")
    elif found.id == "skill_install":
        await handle_skill_install_command(ui, arg)
    elif found.id == "skill_remove":
        await handle_skill_remove_command(ui, arg)
    elif found.id == "files":
        ui._show_result("\n".join(sorted(ui.explicit_files)) or "No explicit files added.")
    elif found.id == "add":
        added, missing = [], []
        for item in arg.split():
            path = Path(ui.state.workspace_path, item).resolve()
            if path.exists() and path.is_file() and Path(ui.state.workspace_path).resolve() in path.parents:
                ui.explicit_files.add(str(path.relative_to(ui.state.workspace_path)))
                added.append(item)
            else:
                missing.append(item)
        ui._show_result((f"Added: {', '.join(added)}" if added else "") + (f"\nNot found: {', '.join(missing)}" if missing else "") or "Usage: /add <file>")
    elif found.id == "drop":
        names = set(arg.split())
        removed = ui.explicit_files.intersection(names)
        ui.explicit_files.difference_update(names)
        ui._show_result(f"Dropped: {', '.join(sorted(removed))}" if removed else "No matching context files.")
    elif found.id == "repomap":
        blocks = await ui._run_blocking(ui.runtime.processor.context_engine.get_relevant_context, "", 1024, str(ui.runtime.canonical_root))
        ui._show_result("\n\n".join(block.content for block in blocks) or "Repository map empty.")
    elif found.id == "doctor":
        await handle_doctor_command(ui)
    elif found.id == "diff":
        await ui._open_diff_overlay()
    elif found.id == "status":
        handle_status_command(ui)
    elif found.id in {"restart_reverse_proxy", "stop_reverse_proxy"}:
        from kitt.ui.reverse_proxy_commands import manage_reverse_proxy
        try:
            message = await ui._run_blocking(
                manage_reverse_proxy, found.id == "restart_reverse_proxy"
            )
        except RuntimeError as error:
            message = f"Falha ao controlar KITT Reverse Proxy: {error}"
        ui._show_result(message)
    elif found.id == "stats":
        await handle_stats_command(ui)
    elif found.id == "gain":
        await handle_gain_command(ui, arg)
    elif found.id == "context_stats":
        config = ui.runtime.config
        ui._show_result(f"Context window: {config.context_window_default}\nReserved output: {config.reserved_output_tokens}")
    elif found.id == "verify_full":
        from kitt.ui.verification_commands import handle_verification_mode_command
        handle_verification_mode_command(ui, arg)
    elif found.id == "router":
        router = getattr(ui.runtime.processor, "router", None)
        profiles = getattr(getattr(router, "config", None), "profiles", {})
        ui._show_result("\n".join(f"{n}: {p.backend}/{p.model}" for n, p in profiles.items()) or "Router configuration unavailable.")
    elif found.id == "approvals":
        if await ui._ensure_daemon_management():
            pending = await ui.bridge.list_approvals()
            ui._show_result("\n".join(
                f"{str(r.get('approval_id',''))[:8]} {r.get('tool_name','')} ({str(r.get('turn_id',''))[:8]})"
                for r in pending
            ) or "No approval requests.")
        else:
            pending = ui.runtime.approval.list_pending(ui.runtime.workspace_id)
            ui._show_result("\n".join(f"{r.approval_id[:8]} {r.tool_name} ({r.turn_id[:8]})\n  summary: {r.summary}" for r in pending) or "No approval requests.")
    elif found.id == "compact":
        await handle_compact_command(ui, arg)
    elif found.id == "child":
        if not arg:
            ui._show_result("Usage: /child <task description>")
        else:
            conversation = ui.runtime.history.get_or_create_active()
            if await ui._ensure_daemon_management():
                await ui._execute_direct_tool("child_spawn", {"task": arg})
            else:
                child = await ui._run_blocking(
                    ui.runtime.children.spawn,
                    parent_conversation_id=conversation["id"], parent_turn_id="ui", task=arg,
                )
                ui._show_result(f"Spawned child: {child.id} ({child.state})")
    elif found.id == "tasks":
        if not ui.state.active_tasks:
            ui._show_result("Nenhum agente ou sub-tarefa ativo no momento.")
        else:
            lines = [f"─── MONITOR DE SUBAGENTES ({ui.state.overall_progress}% Concluído) ───\n"]
            for t in ui.state.active_tasks:
                lines.append(f"• [{t.status.upper()}] {t.name} ({t.role})\n  ↳ Resumo: {t.summary}\n  ↳ Progresso: {t.progress}%")
            ui._show_result("\n".join(lines))
    elif found.id in {"cancel", "stop"}:
        if ui.bridge and ui.bridge.is_active:
            await ui.bridge.cancel("Cancelled by user via command")
            ui._show_result("Active turn cancelled.")
        else:
            ui._show_result("No active turn to cancel.")
    elif found.id == "reasoning":
        if arg:
            try:
                val = max(0, min(100, int(arg.replace("%", "").strip())))
                await ui._set_reasoning_effort(val, notify=False)
                effective = ui.state.reasoning_effort
                blocks = int(effective / 10)
                bar = "█" * blocks + "░" * (10 - blocks)
                ui._show_result(f"Reasoning effort definido para {effective}% [{bar}] (Modelo: {ui.state.large_model})")
            except ValueError:
                ui._show_result("Uso: /reasoning <0-100> (ex: /reasoning 80)")
        else:
            blocks = int(ui.state.reasoning_effort / 10)
            bar = "█" * blocks + "░" * (10 - blocks)
            ui._show_result(f"Reasoning atual: {ui.state.reasoning_effort}% [{bar}]\nModelo em uso: {ui.state.large_model}\n\nUse Ctrl+← / Ctrl+→ para alterar em tempo de execução, ou /reasoning <0-100>.")
    elif found.id in {"autonomy", "permissions"}:
        if not arg:
            ui.open_overlay("autonomy_control", ui.autonomy_control)
        else:
            level = arg.strip().lower()
            preset_map = {
                "allow_all": "autonomous", "allow-all": "autonomous", "always_allow": "autonomous",
                "allow": "autonomous", "always": "autonomous",
                "ask": "supervised", "deny": "read_only",
                "files_free": "balanced", "1": "autonomous",
                "2": "supervised", "3": "read_only",
                "read_only": "read_only", "supervised": "supervised",
                "balanced": "balanced", "autonomous": "autonomous",
            }
            target = preset_map.get(level, level)
            try:
                await ui._set_autonomy_profile(target, notify=False)
                effective = ui.runtime.autonomy_store.get().level
                ui._show_result(f"Perfil de Autonomia alterado para: [{effective.upper()}]")
            except ValueError as err:
                ui.state.add_toast(f"Erro de autonomia: {err}")
                ui._show_result(f"Perfil inválido: {err}")
    elif found.id in {"ask", "code"}:
        if arg:
            if ui.state.is_thinking or (ui.bridge and ui.bridge.is_active):
                ui._show_result("A turn is already active.")
                return True
            marker = "[QUESTION ONLY - NO CODE EDITS]" if found.id == "ask" else "[CODE EDIT REQUIRED]"
            conversation = ui.runtime.history.get_or_create_active()
            try:
                await ui.bridge.start(f"{marker}: {arg}", conversation["id"], explicit_files=ui.explicit_files, no_history=not ui.runtime.config.history_enabled)
            except Exception as err:
                ui.state.add_toast(f"Turn Error: {err}")
        else:
            ui._show_result(f"Usage: /{found.id} <prompt>")
    elif found.id == "undo":
        if await ui._ensure_daemon_management():
            result = await ui.bridge.undo()
            ui._show_result(
                f"Reverted changeset {result.get('changeset_id')}."
                if result.get("reverted") else "No changeset to revert."
            )
        else:
            conversation = ui.runtime.history.get_or_create_active()
            changeset = await ui._run_blocking(
                ui.runtime.processor.diff_applier.tracker.revert_last_changeset,
                conversation["id"], ui.runtime.workspace_id,
            )
            ui._show_result(f"Reverted changeset {changeset.id}." if changeset else "No changeset to revert.")
    elif found.id == "workspace":
        if not arg:
            ui._show_result(str(ui.runtime.canonical_root))
        else:
            await ui._switch_workspace(arg)
    elif found.id == "mouse":
        ui.toggle_mouse_support()
    elif found.id == "sidebar":
        ui._toggle_sidebar()
    elif found.id == "run":
        if arg:
            try:
                argv = shlex.split(arg, posix=os.name != "nt")
            except ValueError as exc:
                ui._show_result(f"Invalid command syntax: {exc}")
            else:
                if argv:
                    await ui._execute_direct_tool("run_command", {"argv": argv})
                else:
                    ui._show_result("Usage: /run <command>")
        else:
            ui._show_result("Usage: /run <command>")
    elif found.id in {"remote", "web"}:
        from kitt.ui.remote_commands import handle_remote_command
        await handle_remote_command(ui, arg)
    elif found.id == "commit":
        message = arg or "Auto-commit by K.I.T.T."
        await ui._execute_direct_tool(
            "run_command",
            {"argv": ["git", "commit", "-am", message]},
        )
    else:
        from kitt.ui.prime_commands import handle_prime_command
        handled = await handle_prime_command(ui, found.id, arg)
        if not handled:
            ui._show_result(
                f"Command {found.aliases[0]} is registered but unavailable in this build."
            )
    if ui.application:
        if not ui.state.active_overlay:
            try:
                ui.application.layout.focus(ui.prompt_control)
            except Exception:
                pass
        ui.application.invalidate()
    return True

