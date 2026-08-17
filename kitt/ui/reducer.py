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
