from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from kitt.core.turn_events import ApprovalRequired
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.git import read_git_branch_name
from kitt.ui.overlay_models import SessionPickerModel, TimelineModel
from kitt.ui.state import safe_text

def _show_result(ui, text: str) -> None:
    ui.state.route = "session"
    ui.state.append_message("system", safe_text(text)[:12000])
    if ui.application:
        if not ui.state.active_overlay:
            try:
                ui.application.layout.focus(ui.prompt_control)
            except Exception:
                pass
        ui.application.invalidate()


async def _show_history(ui, search: str = "") -> None:
    conversations = await ui._run_blocking(ui.runtime.history.list_history, 20, 0, search or None)
    active = ui.runtime.history.active_conversation
    active_id = active.get("id") if active else None
    ui._show_result("\n".join(
        f"{'*' if c['id'] == active_id else ' '} {idx}. {c['id'][:8]} | {c['title']}"
        for idx, c in enumerate(conversations, 1)
    ) or "No conversations.")


async def _show_active_history(ui) -> None:
    conversation = ui.runtime.history.get_or_create_active()
    messages = await ui._run_blocking(ui.runtime.history.repo.get_messages_for_conversation, conversation["id"])
    ui._show_result("\n\n".join(f"{message['role'].upper()}: {message['content']}" for message in messages) or "No messages in active conversation.")


async def _load_conversation(ui, conversation: dict) -> bool:
    if ui.state.status_text in {"STREAMING", "THINKING", "PROCESSING"}:
        ui.state.add_toast("Cannot switch conversation while a turn is active.")
        return False
    conv_id = str(conversation.get("id", ""))
    if not conv_id:
        return False
    if ui.bridge and ui.bridge.daemon_mode:
        ok = await ui.bridge.attach(conv_id)
        if not ok:
            ui.state.add_toast(f"Failed to attach daemon to session '{conversation.get('title', conv_id)}'")
            return False
    messages = await ui._run_blocking(ui.runtime.history.repo.get_messages_for_conversation, conv_id)
    ui.state.active_conversation_id = conv_id
    ui.state.route = "session"
    ui.state.transcript.clear()
    for message in messages[-500:]:
        ui.state.append_message(message.get("role", "system"), message.get("content", ""))
    return True


async def _execute_direct_tool(ui, tool_name: str, args: dict) -> None:
    conversation = ui.runtime.history.get_or_create_active()
    ui.state.active_conversation_id = conversation["id"]
    if await ui._ensure_daemon_management():
        try:
            response = await ui.bridge.execute_direct_tool(tool_name, args)
        except Exception as exc:
            ui._show_result(f"Error: {exc}")
            return
        if response.get("requires_approval"):
            # ApprovalRequired is journaled and delivered by daemon exactly once.
            ui.state.status_text = "APPROVAL"
            return
        ui._show_result(
            str(response.get("output") or "")
            if response.get("success", False)
            else f"Error: {response.get('error') or response.get('output') or 'tool failed'}"
        )
        return

    turn_id = f"ui-{uuid.uuid4().hex[:12]}"
    workspace_id = ui.runtime.workspace_id
    result = await ui._run_blocking(
        ui.runtime.registry.execute_tool, tool_name, args,
        turn_id, conversation["id"], workspace_id,
    )
    if result.requires_approval:
        action_hash = ui.runtime.policy.generate_action_hash(tool_name, args)
        approval_id = f"req_{turn_id}_{action_hash[:8]}"
        ui.runtime.approval.register_request(
            turn_id, conversation["id"], workspace_id, action_hash, approval_id, tool_name=tool_name,
            summary=f"{tool_name}: {safe_text(args)}",
        )
        ui._on_event(ApprovalRequired(
            turn_id=turn_id, conversation_id=conversation["id"], tool_name=tool_name, args=args, action_hash=action_hash,
            approval_request_id=approval_id, workspace_id=workspace_id,
        ))
        pending = ui.state.pending_approval
        if pending:
            pending["direct_tool"] = True
        return
    ui._show_result(result.output if result.success else f"Error: {result.error or result.output}")


async def _switch_workspace(ui, raw_path: str) -> None:
    target = Path(raw_path).expanduser().resolve()
    if ui.state.is_thinking:
        ui._show_result("Cannot switch workspace while a turn is running.")
        return
    if not target.is_dir():
        ui._show_result(f"Directory not found: {raw_path}")
        return
    try:
        new_runtime = await ui.runtime.aswitch_workspace(str(target))
    except Exception as exc:
        ui._show_result(f"Workspace switch failed: {exc}")
        return
    ui.runtime = new_runtime
    ui.state.workspace_path = str(new_runtime.canonical_root)
    ui.state.workspace_name = new_runtime.canonical_root.name or str(new_runtime.canonical_root)
    ui.state.current_branch = read_git_branch_name(new_runtime.canonical_root)
    ui.state.active_conversation_id = None
    ui.state.transcript.clear()
    ui.explicit_files.clear()
    ui._init_models_from_runtime()
    ui.session_picker_model = SessionPickerModel(new_runtime)
    ui.timeline_model = TimelineModel(new_runtime)
    old_bridge = ui.bridge
    if old_bridge:
        try:
            await old_bridge.shutdown()
        except Exception:
            pass
    if ui.application:
        ui.bridge = TurnEventBridge(new_runtime, ui._on_event, ui.application.invalidate)
    ui._show_result(f"Switched workspace: {new_runtime.canonical_root}")


async def _set_reasoning_effort(ui, value: int, *, notify: bool = True) -> None:
    value = max(0, min(100, int(value)))
    if await ui._ensure_daemon_management():
        response = await ui.bridge.set_reasoning(value)
        value = max(0, min(100, int(response.get("value", value))))
    ui.state.reasoning_effort = value
    if hasattr(ui.runtime, "processor"):
        ui.runtime.processor.reasoning_effort = value
    if notify:
        blocks = int(value / 10)
        bar = "█" * blocks + "░" * (10 - blocks)
        model_name = ui.state.large_model or "execution"
        ui.state.add_toast(f"🧠 Reasoning: {value}% [{bar}] ({model_name})", duration=2.0)
    if ui.application:
        ui.application.invalidate()


async def _set_autonomy_profile(ui, preset: str, *, notify: bool = True) -> None:
    effective = preset
    if await ui._ensure_daemon_management():
        response = await ui.bridge.set_autonomy(preset)
        effective = str(response.get("preset") or preset)
    store = getattr(ui.runtime, "autonomy_store", None)
    policy = store.set_preset(effective) if store else None
    if policy is not None and hasattr(ui.runtime.processor.registry, "policy"):
        ui.runtime.processor.registry.policy.autonomy = policy
    if notify:
        ui.state.add_toast(f"Perfil de autonomia: {effective}")
    if ui.application:
        ui.application.invalidate()


async def _clear_remembered_approvals(ui, scope: str = "session") -> None:
    conv = ui.runtime.history.get_or_create_active()
    if await ui._ensure_daemon_management():
        result = await ui.bridge.clear_remembered(scope)
        removed = int(result.get("removed", 0))
        try:
            ui.runtime.approval.clear_remembered(
                scope=scope, conversation_id=conv["id"] if scope == "session" else None
            )
        except Exception:
            pass
    else:
        removed = ui.runtime.approval.clear_remembered(
            scope=scope, conversation_id=conv["id"] if scope == "session" else None
        )
    ui.state.add_toast(f"Regras removidas: {removed}")
    if ui.application:
        ui.application.invalidate()


async def resolve_approval(ui, mode: str | bool = "once") -> None:
    pending = ui.state.pending_approval
    if not pending:
        return

    remember_scope = None
    if mode is True or mode == "once":
        allow = True
    elif mode is False or mode == "deny":
        allow = False
    elif mode in {"always_workspace", "always", "A"}:
        allow = True
        remember_scope = "workspace"
    elif mode in {"always_session", "session", "s"}:
        allow = True
        remember_scope = "session"
    elif mode == "deny_all":
        if ui.bridge and ui.bridge.daemon_mode:
            for req in list(ui.state.pending_approvals):
                try:
                    await ui.bridge.resolve_approval(req["approval_id"], False)
                except Exception:
                    pass
        else:
            for req in list(ui.state.pending_approvals):
                try:
                    ui.runtime.approval.deny(req["approval_id"], "Denied all in queue")
                except Exception:
                    pass
        ui.state.pending_approvals.clear()
        ui.close_overlay()
        if ui.bridge and ui.bridge.is_active:
            await ui.bridge.cancel("Denied all in queue")
        if ui.application:
            ui.application.invalidate()
        return
    else:
        allow = bool(mode)

    ui.state.status_text = "APPROVING" if allow else "DENYING"
    if ui.bridge and ui.bridge.daemon_mode:
        try:
            tool_name = pending.get("tool_name", "apply_patch")
            if remember_scope:
                await ui.bridge.remember_approval(tool_name, remember_scope)
                ui.state.add_toast(
                    f"Sempre permitir {tool_name} ativado para este {remember_scope}."
                )
            await ui.bridge.resolve_approval(pending["approval_id"], allow)
            if ui.state.pending_approvals:
                ui.state.pending_approvals.pop(0)
            ui.close_overlay()
            ui.state.status_text = "PROCESSING" if allow else "SYSTEM ONLINE"
        except Exception as exc:
            ui.state.add_toast(f"Approval failed: {exc}", persistent=True)
            ui.state.status_text = "ERROR"
        if ui.application:
            ui.application.invalidate()
        return

    if remember_scope:
        tool_name = pending.get("tool_name", "apply_patch")
        ui.runtime.approval.remember(
            tool_name, "**", "allow", remember_scope,
            conversation_id=pending.get("conversation_id") if remember_scope == "session" else None,
        )
        ui.state.add_toast(f"Sempre permitir {tool_name} ativado para este {remember_scope}.")

    if allow:
        try:
            grant = ui.runtime.approval.issue_grant(
                pending["turn_id"], pending["conversation_id"], pending["workspace_id"], pending["action_hash"],
                approval_id=pending["approval_id"],
            )
            ui.state.pending_approvals.pop(0)
            ui.close_overlay()
            if pending.get("direct_tool"):
                result = await ui._run_blocking(
                    ui.runtime.registry.execute_tool,
                    pending["tool_name"], pending["args"], pending["turn_id"], pending["conversation_id"],
                    pending["workspace_id"], None, grant, pending["approval_id"], "USER",
                )
                ui._show_result(result.output if result.success else f"Error: {result.error or result.output}")
                ui.state.status_text = "SYSTEM ONLINE"
            else:
                running_tool = next((b for b in reversed(ui.state.transcript) if b.kind == "tool" and b.status == "waiting_approval"), None)
                if running_tool:
                    running_tool.status = "running"
                    running_tool.started_at = time.time()
                    running_tool.text = running_tool.text.replace(" ⏸ (aguardando aprovação)", "").replace(" ⏸", "").strip()
                await ui.bridge.continue_turn(pending["turn_id"], grant)
        except Exception as exc:
            ui.state.add_toast(f"Approval failed: {exc}", persistent=True)
            ui.state.status_text = "ERROR"
    else:
        try:
            ui.runtime.approval.deny(pending["approval_id"], "Approval denied")
        except Exception:
            pass
        ui.state.pending_approvals.pop(0)
        ui.close_overlay()
        if pending.get("direct_tool"):
            ui._show_result("Command denied.")
            ui.state.status_text = "SYSTEM ONLINE"
        else:
            running_tool = next((b for b in reversed(ui.state.transcript) if b.kind == "tool" and b.status == "waiting_approval"), None)
            if running_tool:
                running_tool.status = "cancelled"
                clean_text = running_tool.text.replace(" ⏸ (aguardando aprovação)", "").replace(" ⏸", "").strip()
                running_tool.text = f"{clean_text} ∅"
            await ui.bridge.cancel("Approval denied")
    if ui.application:
        ui.application.invalidate()



async def _export_conversation(ui, fmt: str) -> None:
    conv = ui.runtime.history.get_active_read_only()
    if not conv:
        ui._show_result("Nenhuma conversa ativa.")
        return
    msgs = await ui._run_blocking(
        ui.runtime.history.repo.get_messages_for_conversation, conv["id"]
    )
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if fmt == "json":
        content = json.dumps(msgs, indent=2, ensure_ascii=False)
        filename = f"kitt_export_{timestamp}.json"
    else:
        lines = ["# K.I.T.T. Conversation Export\n"]
        for m in msgs:
            role = "**User**" if m["role"] == "user" else "**K.I.T.T.**"
            lines.append(f"\n{role}:\n\n{m['content']}\n\n---")
        content = "\n".join(lines)
        filename = f"kitt_export_{timestamp}.md"
    out_path = Path(ui.state.workspace_path) / filename
    out_path.write_text(content, encoding="utf-8")
    ui._show_result(f"Exportado: {filename}")

_parse_model_command = _model_service._parse_model_command
_role_tasks = _model_service._role_tasks
_model_for_role = _model_service._model_for_role
_profile_for_role = _model_service._profile_for_role
