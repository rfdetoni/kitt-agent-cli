import time
from kitt.core.turn_events import (
    ApprovalRequired, BudgetApplied, EditApplied, MetricsRecorded, ModelSelected,
    TextDelta, ToolCallProposed, ToolCompleted, ToolStarted, TurnBlocked, TurnCancelled,
    TurnCompleted, TurnFailed, TurnStarted, ChildAgentSpawned, ChildAgentProgress, ChildAgentFinished,
    ThinkingStarted, ThinkingCompleted, FilterCompleted, ContextResolved, ContextBuildCompleted
)
from kitt.ui.state import AgentTaskStep, TranscriptBlock, UIState, safe_text


TERMINAL_EVENTS = (TurnCompleted, TurnFailed, TurnCancelled, TurnBlocked)


def format_tool_bullet(tool_name: str, args: dict | None) -> str:
    args = args or {}

    # Handle composite kitt_runtime operations and dotted names
    if tool_name == "kitt_runtime" or "operation" in args or "." in tool_name:
        op = str(args.get("operation") or tool_name).strip()
        inner_args = args.get("arguments")
        if not isinstance(inner_args, dict):
            inner_args = args

        if op in {"repo.search", "search"}:
            query = inner_args.get("query", inner_args.get("pattern", ""))
            path = inner_args.get("path", "")
            loc = f" em {path}" if path else ""
            return f"● Buscar: '{query}'{loc}" if query else "● Buscar no projeto"
        elif op in {"repo.read", "read_file"}:
            path = inner_args.get("path", inner_args.get("file", ""))
            start = inner_args.get("start_line")
            end = inner_args.get("end_line")
            range_str = f":L{start}-{end}" if start and end else ""
            return f"● Ler arquivo: {path}{range_str}" if path else "● Ler arquivo"
        elif op in {"repo.inspect_symbol", "repo.read_symbol"}:
            sym = inner_args.get("symbol", "")
            path = inner_args.get("path", "")
            loc = f" em {path}" if path else ""
            return f"● Inspecionar símbolo: {sym}{loc}" if sym else "● Inspecionar símbolo"
        elif op == "repo.references":
            sym = inner_args.get("symbol", "")
            return f"● Referências de: {sym}" if sym else "● Buscar referências"
        elif op in {"repo.edit_symbol", "patch.apply", "apply_patch", "write_file"}:
            path = inner_args.get("path", inner_args.get("file", ""))
            if not path and "patch" in inner_args:
                patch = str(inner_args.get("patch", ""))
                first_line = patch.strip().split("\n")[0] if patch else ""
                path = first_line.split("<<<<<<<")[0].strip() or first_line
            if not path and "symbol" in inner_args:
                path = f"símbolo {inner_args.get('symbol')}"
            return f"● Editar: {path}" if path else "● Editar arquivo"
        elif op in {"process.run", "run_command", "bash"}:
            cmd = inner_args.get("command", inner_args.get("cmd", ""))
            return f"● Executar: {cmd}" if cmd else "● Executar comando"
        elif op == "artifacts.read":
            art_id = inner_args.get("artifact_id", "")
            return f"● Ler artefato: {art_id}" if art_id else "● Ler artefato"
        elif op == "artifacts.store":
            art_type = inner_args.get("artifact_type", "artefato")
            summary = inner_args.get("summary", "")
            return f"● Salvar {art_type}: {summary}" if summary else f"● Salvar {art_type}"
        elif op in {"children.spawn", "child_spawn"}:
            prompt = str(inner_args.get("prompt", inner_args.get("task", "")))[:50]
            return f"● Subagente: {prompt}" if prompt else "● Subagente"
        elif op == "memory.query":
            q = inner_args.get("query", "")
            return f"● Consultar memória: {q}" if q else "● Consultar memória"
        elif op.startswith("memory."):
            sub = op.split(".")[-1]
            return f"● Memória ({sub})"
        elif op.startswith("state."):
            k = inner_args.get("key", inner_args.get("prefix", ""))
            sub = op.split(".")[-1]
            return f"● Estado ({sub}): {k}" if k else f"● Estado ({sub})"
        elif op == "handles.resolve":
            h = inner_args.get("handle", "")
            return f"● Resolver handle: {h}" if h else "● Resolver handle"
        elif op == "list_files":
            path = inner_args.get("path", ".")
            return f"● Listar arquivos: {path}"
        elif op == "repository_map":
            query = inner_args.get("query", "")
            return f"● Mapa do repositório: {query}" if query else "● Mapa do repositório (AST)"
        elif op == "python_compute":
            return "● Executar computação Python"
        else:
            summary = ", ".join(f"{k}={v}" for k, v in list(inner_args.items())[:2]) if inner_args else ""
            name_cap = op.replace("_", " ").title().replace(" ", "")
            return f"● {name_cap}({summary})" if summary else f"● {name_cap}"

    # Legacy direct tools formatting
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
        target = first_line.split("<<<<<<<")[0].strip() or first_line
        return f"● Edit({target or 'patch'})"
    elif tool_name in {"run_command", "bash"}:
        cmd = args.get("command", args.get("cmd", ""))
        return f"● Bash({cmd})"
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



from kitt.ui.reducer_handlers import (
    handle_turn_started, handle_thinking_started, handle_thinking_completed,
    handle_tool_proposed, handle_text_delta, handle_tool_started, handle_tool_completed,
    handle_approval_required, handle_context_events, handle_child_agent_events,
    handle_terminal_events
)


def reduce_ui_event(state: UIState, event: object) -> UIState:
    if isinstance(event, TurnStarted):
        handle_turn_started(state, event)
    elif isinstance(event, ThinkingStarted):
        handle_thinking_started(state, event)
    elif isinstance(event, ThinkingCompleted):
        handle_thinking_completed(state, event)
    elif isinstance(event, ToolCallProposed):
        handle_tool_proposed(state, event, format_tool_bullet)
    elif isinstance(event, TextDelta):
        handle_text_delta(state, event)
    elif isinstance(event, ToolStarted):
        handle_tool_started(state, event, format_tool_bullet)
    elif isinstance(event, ToolCompleted):
        handle_tool_completed(state, event)
    elif isinstance(event, ApprovalRequired):
        handle_approval_required(state, event)
    elif isinstance(event, (FilterCompleted, ContextResolved, ContextBuildCompleted)):
        handle_context_events(state, event)
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
    elif isinstance(event, (ChildAgentSpawned, ChildAgentProgress, ChildAgentFinished)):
        handle_child_agent_events(state, event)

    if isinstance(event, TERMINAL_EVENTS):
        handle_terminal_events(state, event)

    return state
