import time
from kitt.core.turn_events import (
    ApprovalRequired, BudgetApplied, EditApplied, MetricsRecorded, ModelSelected,
    TextDelta, ToolCompleted, ToolStarted, TurnBlocked, TurnCancelled,
    TurnCompleted, TurnFailed, TurnStarted, ChildAgentSpawned, ChildAgentProgress, ChildAgentFinished,
    ThinkingStarted, ThinkingCompleted, FilterCompleted, ContextResolved, ContextBuildCompleted
)
from kitt.ui.state import AgentTaskStep, TranscriptBlock, UIState, safe_text


TERMINAL_EVENTS = (TurnCompleted, TurnFailed, TurnCancelled, TurnBlocked)


def format_tool_bullet(tool_name: str, args: dict | None) -> str:
    args = args or {}
    if tool_name == "search":
        pattern = args.get("pattern", args.get("query", ""))
        path = args.get("path", "")
        return f"● Search({pattern}{' in ' + path if path else ''})"
    elif tool_name == "read_file":
        path = args.get("path", args.get("file", ""))
        start = args.get("start_line")
        end = args.get("end_line")
        range_str = f":L{start}-{end}" if start and end else ""
        return f"● Read({path}{range_str})"
    elif tool_name == "write_file":
        path = args.get("path", args.get("file", ""))
        return f"● Write({path})"
    elif tool_name == "apply_patch":
        patch = str(args.get("patch", ""))
        first_line = patch.strip().split("\n")[0] if patch else ""
        return f"● Patch({first_line or 'SEARCH/REPLACE'})"
    elif tool_name == "run_command":
        cmd = args.get("command", args.get("cmd", ""))
        return f"● Run({cmd})"
    elif tool_name == "list_files":
        path = args.get("path", ".")
        return f"● List({path})"
    elif tool_name == "repository_map":
        query = args.get("query", "")
        return f"● RepoMap({query if query else 'AST'})"
    elif tool_name == "python_compute":
        return "● PythonCompute(python_compute)"
    elif tool_name == "child_spawn":
        prompt = str(args.get("prompt", args.get("task", "")))[:40]
        return f"● Subagent({prompt})"
    else:
        summary = ", ".join(f"{k}={v}" for k, v in list(args.items())[:2]) if args else ""
        name_cap = tool_name.replace("_", " ").title().replace(" ", "")
        return f"● {name_cap}({summary})"


def reduce_ui_event(state: UIState, event: object) -> UIState:
    if isinstance(event, TurnStarted):
        state.route = "session"
        state.active_turn_id = event.turn_id
        state.active_conversation_id = event.conversation_id
        state.is_thinking = True
        state.status_text = "SCANNING"
        state.append_message("user", event.prompt)
        state.init_turn_tasks(event.prompt)
        state.turn_started_at = time.time()
    elif isinstance(event, ThinkingStarted):
        state.status_text = "THINKING"
        state.transcript.append(TranscriptBlock(
            f"thought-{len(state.transcript)+1}", "thought", "▸ Pensando...", "running",
        ))
        core_task = next((t for t in state.active_tasks if t.id == "core" or t.kind == "core_agent"), None)
        if core_task:
            core_task.status = "running"
            core_task.summary = "Modelo pensando; aguardando primeira resposta visível..."
            core_task.progress = max(core_task.progress, 20)
    elif isinstance(event, ThinkingCompleted):
        for block in reversed(state.transcript):
            if block.kind == "thought" and block.status == "running":
                secs = event.duration_ms / 1000
                token_part = f", {event.tokens} tokens" if event.tokens else ""
                block.text = f"▸ Thought for {secs:.0f}s{token_part}"
                block.status = "done"
                block.duration_ms = event.duration_ms
                block.tokens = event.tokens
                thought_str = getattr(event, "thought", "")
                if thought_str:
                    block.collapsed = True
                    block.metadata["full_output"] = safe_text(thought_str)
                break
    elif isinstance(event, TextDelta):
        state.status_text = "RESPONDING"
        delta = safe_text(event.delta)
        if state.transcript and state.transcript[-1].kind == "assistant" and state.transcript[-1].status == "streaming":
            state.transcript[-1].text += delta
        else:
            state.transcript.append(TranscriptBlock(f"assistant-{state.active_turn_id or len(state.transcript)}", "assistant", delta, "streaming"))
        if not state.follow_tail:
            state.unseen_output = True

        acc_len = len(state.transcript[-1].text) if state.transcript else 0
        core_task = next((t for t in state.active_tasks if t.id == "core" or t.kind == "core_agent"), None)
        if core_task:
            core_task.status = "running"
            core_task.summary = f"Modelo principal gerando resposta ({acc_len} caracteres)..."
            core_task.progress = min(95, 15 + int(acc_len / 15))

    elif isinstance(event, ToolStarted):
        state.is_executing_tool = True
        state.active_tool_name = event.tool_name
        state.status_text = f"TOOL: {event.tool_name}"
        bullet_text = format_tool_bullet(event.tool_name, getattr(event, "args", {}))
        call_id = getattr(event, "call_id", "")
        state.transcript.append(TranscriptBlock(
            f"tool-{len(state.transcript)+1}", "tool", bullet_text, "running",
            call_id=call_id, started_at=time.time(),
        ))
        
        tool_task_id = f"tool-{call_id if call_id else event.tool_name}"
        tool_task = next((t for t in state.active_tasks if t.id == tool_task_id or t.id == "compute"), None)
        if not tool_task:
            tool_task = AgentTaskStep(tool_task_id, bullet_text, event.tool_name, "running", bullet_text, 50)
            state.active_tasks.append(tool_task)
        else:
            tool_task.status = "running"
            tool_task.summary = bullet_text
            tool_task.progress = 50
    elif isinstance(event, ToolCompleted):
        state.is_executing_tool = False
        state.active_tool_name = None
        state.status_text = "PROCESSING"
        call_id = getattr(event, "call_id", "")
        block = state.find_running_tool_block(call_id) if call_id else next(
            (b for b in reversed(state.transcript) if b.kind == "tool" and b.status == "running"), None)
        if block:
            secs = time.time() - block.started_at
            duration_str = f" ({secs:.1f}s)"
            token_str = f", {event.tokens} tok" if event.tokens else ""
            status_glyph = "✔" if event.success else "✖"
            block.text = f"{block.text}{duration_str}{token_str} {status_glyph}"
            block.status = "done" if event.success else "error"
            block.duration_ms = int(secs * 1000)
            block.tokens = event.tokens
            out_str = str(event.output or "")
            is_code_tool = getattr(event, "tool_name", "") in {"read_file", "apply_patch", "python_compute", "write_file"}
            if len(out_str) > 120 or "\n" in out_str or is_code_tool:
                block.collapsed = True
                block.metadata["full_output"] = safe_text(out_str)

        tool_task_id = f"tool-{call_id if call_id else event.tool_name}"
        tool_task = next((t for t in state.active_tasks if t.id == tool_task_id or t.id == f"tool-{event.tool_name}"), None)
        if tool_task:
            tool_task.status = "done" if event.success else "error"
            tool_task.summary = f"{tool_task.summary} {'✔' if event.success else '✖'}"
            tool_task.progress = 100
    elif isinstance(event, ApprovalRequired):
        state.status_text = "APPROVAL"
        args_dict = event.args if isinstance(event.args, dict) else {}
        patch = args_dict.get("patch", "")
        affected = [args_dict.get("path")] if "path" in args_dict and args_dict.get("path") else ([args_dict.get("file")] if "file" in args_dict and args_dict.get("file") else [])
        state.pending_approvals.append({
            "turn_id": event.turn_id, "conversation_id": event.conversation_id or state.active_conversation_id,
            "approval_id": event.approval_request_id, "workspace_id": event.workspace_id,
            "tool_name": event.tool_name, "args": event.args, "action_hash": event.action_hash,
            "affected_paths": affected, "diff_preview": patch,
        })
        state.push_overlay("permission")
    elif isinstance(event, FilterCompleted):
        if event.filter_res:
            res = event.filter_res
            state.context_stats.filter_source = str(res.source)
            state.context_stats.filter_fallback_reason = str(res.fallback_reason or "")
            state.context_stats.filter_latency_ms = float(res.latency_ms)
            state.context_stats.intent = str(getattr(res.task, "intent", ""))
            if res.source == "FALLBACK":
                reason_str = f" ({res.fallback_reason})" if res.fallback_reason else ""
                state.transcript.append(TranscriptBlock(
                    f"ctx-{len(state.transcript)+1}",
                    "context",
                    f"◐ Contexto: filtro semântico em fallback{reason_str} — usando heurística determinística",
                    status="warning",
                ))
    elif isinstance(event, ContextResolved):
        state.context_stats.resolved_count = event.resolved_count
    elif isinstance(event, ContextBuildCompleted):
        cs = state.context_stats
        cs.index_state = event.index_state
        cs.index_generation = event.index_generation
        cs.selected_count = event.selected_count
        cs.rejected_count = event.rejected_count
        cs.context_tokens = event.total_tokens
        cs.coverage = event.coverage
        cs.degraded = event.degraded
        cs.duration_ms = event.duration_ms
        cs.index_scanned = event.index_scanned
        cs.index_updated = event.index_updated
        cs.index_deleted = event.index_deleted
        cs.partial_reason = event.partial_reason
        
        core_task = next((t for t in state.active_tasks if t.id == "core" or t.kind == "core_agent"), None)
        if core_task:
            total_cand = event.selected_count + event.rejected_count
            core_task.summary = f"Contexto: {event.selected_count}/{total_cand} candidatos, cobertura {event.coverage:.0%}"
            
        if event.index_state in ("PARTIAL", "DEGRADED") or (event.degraded and event.partial_reason):
            detail = f"◐ Contexto: índice {event.index_state or 'DEGRADADO'} (gen {event.index_generation}), " \
                     f"selecionados {event.selected_count}, rejeitados {event.rejected_count}, " \
                     f"cobertura {event.coverage:.0%}"
            if event.partial_reason:
                detail += f" — motivo: {event.partial_reason}"
            state.transcript.append(TranscriptBlock(
                f"ctx-{len(state.transcript)+1}",
                "context",
                detail,
                status="error" if event.degraded or event.index_state == "DEGRADED" else "warning",
            ))
    elif isinstance(event, BudgetApplied):
        state.tokens_used = event.total_input_tokens
        state.context_window = event.window_size
        core_task = next((t for t in state.active_tasks if t.id == "core" or t.kind == "core_agent"), None)
        if core_task:
            core_task.summary = f"Contexto compilado ({event.total_input_tokens} tokens). Gerando..."
            core_task.progress = 30
    elif isinstance(event, ModelSelected):
        if event.profile_name in {"context", "context-gather", "summarize"}:
            state.small_model = event.model
        else:
            state.large_model = event.model
    elif isinstance(event, MetricsRecorded):
        state.tokens_used = event.input_tokens + event.output_tokens
        state.gross_saved_tokens += event.saved_tokens
        state.net_saved_tokens += event.saved_tokens
    elif isinstance(event, EditApplied):
        changed = event.applied_files + event.created_files
        state.append_message("system", f"📝 [EDIÇÃO APLICADA] {len(changed)} arquivo(s) modificado(s): " + ", ".join(changed))
        state.add_toast("📝 Modificado: " + ", ".join(changed))
    elif isinstance(event, ChildAgentSpawned):
        state.upsert_child_task(event.child_id, event.name, "pending", summary=event.task[:80], progress=0)
        state.append_message("system", f"🤖 [SUBAGENTE INICIADO] '{event.name}' (ID: {event.child_id})\n  ↳ Tarefa: {event.task[:100]}")
        state.add_toast(f"🤖 Subagente '{event.name}' iniciado")
    elif isinstance(event, ChildAgentProgress):
        state.upsert_child_task(event.child_id, event.child_id, event.status.lower(), summary=event.summary, progress=event.progress)
    elif isinstance(event, ChildAgentFinished):
        status_str = "done" if event.status == "COMPLETED" else "error"
        summary_str = "Concluído com sucesso" if status_str == "done" else (event.error or "Falha no agente filho")
        state.upsert_child_task(event.child_id, event.child_id, status_str, summary=summary_str, progress=100)
        if status_str == "done":
            state.append_message("system", f"✔ [SUBAGENTE CONCLUÍDO] '{event.child_id}' finalizado com sucesso.")
            state.add_toast(f"✔ Subagente '{event.child_id}' concluído!")
        else:
            state.append_message("error", f"✖ [SUBAGENTE FALHOU] '{event.child_id}' falhou: {event.error}")
            state.add_toast(f"✖ Subagente '{event.child_id}' falhou!", persistent=True)

    if isinstance(event, TERMINAL_EVENTS):
        current_assistant_block = next(
            (block for block in reversed(state.transcript) if block.kind == "assistant" and block.status == "streaming"),
            None,
        )
        state.is_thinking = False
        state.is_executing_tool = False
        state.active_tool_name = None
        state.active_turn_id = None
        for block in reversed(state.transcript):
            if block.kind == "assistant" and block.status == "streaming":
                block.status = "done"
                break
        
        # Mark all tasks as done on completion
        for task in state.active_tasks:
            if task.status != "error" and task.status != "cancelled":
                task.status = "done"
                task.progress = 100

        if isinstance(event, TurnCompleted):
            state.status_text = "✔ COMPLETED"

            # TurnCompleted.response is canonical. Streams may be absent or partial.
            if event.response:
                response = safe_text(event.response)
                if current_assistant_block:
                    if current_assistant_block.text.strip() != response.strip():
                        current_assistant_block.text = response
                else:
                    state.append_message("assistant", response)

            has_child_agent = any(t.kind == "child_agent" for t in state.active_tasks)
            tool_call_count = len([b for b in state.transcript if b.kind == "tool"])
            if has_child_agent or tool_call_count >= 3:
                summary_msg = f"✔ [PROCESSO CONCLUÍDO COM SUCESSO]\n" \
                              f"  ↳ Tokens utilizados: {state.tokens_used} | Economizados (RTK/AST): {state.net_saved_tokens}"
                state.append_message("system", summary_msg)
                state.add_toast("✔ Processo concluído com sucesso!")
        elif isinstance(event, TurnFailed):
            state.status_text = "✖ FAILED"
            state.append_message("error", f"✖ [PROCESSO FALHOU]\n  ↳ Causa: {event.error}")
            state.add_toast(f"✖ Processo falhou: {event.error}", persistent=True)
        elif isinstance(event, TurnCancelled):
            state.active_tasks = []
            state.status_text = "∅ CANCELLED"
            state.append_message("system", f"∅ [PROCESSO CANCELADO]\n  ↳ Motivo: {event.reason or 'Cancelado pelo usuário'}")
            state.add_toast(event.reason or "Processo cancelado")
        else:
            state.status_text = "BLOCKED"
            state.add_toast(event.reason, persistent=True)
    return state
