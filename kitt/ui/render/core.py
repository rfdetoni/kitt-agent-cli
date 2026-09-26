from __future__ import annotations

import time
from importlib.metadata import PackageNotFoundError, version as package_version

from kitt.ui.theme import DEFAULT_THEME


def _agent_version() -> str:
    try:
        return package_version("kitt-agent-cli")
    except PackageNotFoundError:
        return "dev"


def _home_text(ui):
    scanner = DEFAULT_THEME.scanner_frame(ui.state.scanner_step, 36)
    return [
        ("class:primary.bright", "┌──────────────────────────────────────────────────────────────┐\n"),
        ("class:primary.bright", f"│  [ {scanner} ]  │\n"),
        ("class:primary.bright", "└──────────────────────────────────────────────────────────────┘\n"),
        ("class:primary", "██╗  ██╗    ██╗    ████████╗   ████████╗\n"),
        ("class:primary", "██║ ██╔╝    ██║    ╚══██╔══╝   ╚══██╔══╝\n"),
        ("class:primary", "█████╔╝     ██║       ██║         ██║   \n"),
        ("class:primary", "██╔═██╗     ██║       ██║         ██║   \n"),
        ("class:primary", "██║  ██╗    ██║       ██║         ██║   \n"),
        ("class:primary", "╚═╝  ╚═╝    ╚═╝       ╚═╝         ╚═╝   \n"),
        ("class:primary", "K.I.T.T. "),
        ("class:text.muted", f"— Knowledge & Inference Task Tool • v{_agent_version()}\n"),
        ("class:accent", f"{ui.state.workspace_path}\n"),
        ("class:text.muted", f"Models: {ui.state.small_model} (Context) • {ui.state.large_model} (Execute)")
    ]


def _header_text(ui):
    model_name = ui.state.large_model or "execution"
    mode_tag = ("class:warning", " [PLAN MODE] ") if ui.state.planning_mode else ("class:status", f" [{model_name}] ")
    return [
        ("class:primary", " K.I.T.T. "),
        ("class:text.muted", f" {ui.state.workspace_path} "),
        mode_tag,
        ("class:primary", f" 🧠 Reasoning: {ui.state.reasoning_effort}% (Ctrl+←/→) "),
    ]


def _transcript_text(ui):
    out = []
    labels = {
        "user": "YOU",
        "assistant": "K.I.T.T.",
        "tool": "TOOL",
        "error": "ERROR",
        "system": "SYSTEM",
        "thought": "THOUGHT",
    }
    now = time.time()
    cache = getattr(ui, "_transcript_render_cache", None)
    if cache is None:
        cache = {}
        ui._transcript_render_cache = cache
    transcript = ui.state.transcript
    limit = max(1, int(getattr(ui.state, "transcript_window_blocks", 120)))
    hidden_count = max(0, len(transcript) - limit)
    visible_blocks = transcript[hidden_count:]
    live_ids = set()

    if hidden_count:
        out.append((
            "class:text.muted",
            f"\n… {hidden_count} blocos anteriores virtualizados — role para cima para carregar mais …\n",
        ))

    for block in visible_blocks:
        live_ids.add(block.id)
        running = (
            block.kind in {"tool", "thought"}
            and block.status == "running"
            and bool(
                ui.state.active_turn_id
                or ui.state.is_thinking
                or ui.state.is_executing_tool
            )
        )
        elapsed = int(now - block.started_at) if running and block.started_at else None
        full_output = block.metadata.get("full_output")
        signature = (
            block.kind,
            block.text,
            block.status,
            block.collapsed,
            str(full_output) if full_output is not None else None,
            elapsed,
        )
        cached = cache.get(block.id)
        if cached and cached[0] == signature:
            out.extend(cached[1])
            continue

        fragment = []
        if block.kind in {"tool", "thought"}:
            text = block.text
            if running:
                if block.kind == "thought":
                    text = f"▸ Pensando ({elapsed or 0}s...)"
                else:
                    text = f"{text} ({elapsed or 0}s...)"

            if block.collapsed:
                first_line = text.split("\n")[0]
                fragment.append(
                    (f"class:{block.kind}", f"{first_line} (ctrl+o para expandir)\n")
                )
            elif full_output is not None:
                fragment.append(
                    (
                        f"class:{block.kind}",
                        f"{text}\n    {full_output}\n    (ctrl+o para recolher)\n",
                    )
                )
            else:
                fragment.append((f"class:{block.kind}", f"{text}\n"))
        else:
            label = labels.get(block.kind, block.kind.upper())
            fragment.extend(
                [
                    (f"class:{block.kind}", f"\n{label}  "),
                    ("class:text", block.text + "\n"),
                ]
            )

        cached_fragment = tuple(fragment)
        cache[block.id] = (signature, cached_fragment)
        out.extend(cached_fragment)

    # Avoid an O(total transcript) cleanup on every frame. The retained
    # transcript itself is capped at 500 blocks, so prune only when stale cache
    # entries prove that the rolling window has crossed that bound.
    if len(cache) > 600:
        retained_ids = {block.id for block in transcript}
        for block_id in tuple(cache):
            if block_id not in retained_ids:
                cache.pop(block_id, None)

    if ui.state.unseen_output:
        out.append(("class:warning", "\n[new output below]"))
    if not out:
        return [
            ("class:primary", "  ┌─────────────────────────────────────────────────────────────────────────────┐\n"),
            ("class:error",   "  │  [ ░▒▓████████████████████████████████████████████████████████████████▓▒░ ]  │\n"),
            ("class:primary", "  └─────────────────────────────────────────────────────────────────────────────┘\n"),
            ("class:error",   "   ██╗  ██╗    ██╗    ████████╗   ████████╗\n"),
            ("class:error",   "   ██║ ██╔╝    ██║    ╚══██╔══╝   ╚══██╔══╝\n"),
            ("class:error",   "   █████╔╝     ██║       ██║         ██║   \n"),
            ("class:error",   "   ██╔═██╗     ██║       ██║         ██║   \n"),
            ("class:error",   "   ██║  ██╗    ██║       ██║         ██║   \n"),
            ("class:error",   "   ╚═╝  ╚═╝    ╚═╝       ╚═╝         ╚═╝   \n"),
            ("class:primary", "  K.I.T.T. "),
            ("class:text.muted", "— Knowledge & Inference Task Tool • Autonomous AI Coding Agent\n"),
            ("class:text.muted", "  Digite sua instrução abaixo ou /help para ver a lista de comandos.\n\n"),
        ]
    return out

def _transcript_cursor_position(ui):
    from prompt_toolkit.data_structures import Point
    if not ui.state.follow_tail:
        return None
    if not ui.state.transcript:
        return Point(x=0, y=0)
    text_content = ui._transcript_text()
    total_lines = 0
    for style, txt in text_content:
        total_lines += txt.count("\n")
    return Point(x=0, y=max(0, total_lines - 1))


def _sidebar_text(ui):
    pct = min(100, ui.state.tokens_used * 100 // max(1, ui.state.context_window))
    files_section = ""
    if ui.explicit_files:
        files_lines = "\n".join(f"  • {f}" for f in sorted(ui.explicit_files))
        files_section = f"\n\n ATTACHED FILES ({len(ui.explicit_files)})\n{files_lines}"
    else:
        files_section = ""
    return (
        f" WORKSPACE\n {ui.state.workspace_name}\n\n"
        f" CONVERSATION\n {(ui.state.active_conversation_id or 'new')[:12]}\n\n"
        f" MODELS\n {ui.state.small_model}\n {ui.state.large_model}\n"
        f" 🧠 Reasoning: {ui.state.reasoning_effort}%\n\n"
        f" CONTEXT\n {ui.state.tokens_used}/{ui.state.context_window} ({pct}%)\n"
        f" SAVED {ui.state.net_saved_tokens}"
        f"{files_section}"
    )


def _status_text(ui):
    pct = min(100, ui.state.tokens_used * 100 // max(1, ui.state.context_window))
    plan_badge = "[PLAN] " if ui.state.planning_mode else ""
    if ui.state.is_thinking:
        elapsed = max(0, int(time.time() - ui.state.turn_started_at))
        active = next((t for t in ui.state.active_tasks if t.status == "running"), None)
        detail = active.summary if active else "processando solicitação"
        return f" {plan_badge}{ui.state.status_text} {elapsed}s | {detail[:48]} | context {pct}% "
    if ui.state.width < 80:
        branch_part = (
            f" | branch:{ui.state.current_branch[:12]}"
            if ui.state.current_branch
            else ""
        )
        return f" {plan_badge}{ui.state.status_text}{branch_part} | {ui.state.large_model[:16]} | {pct}% "
    branch_part = (
        f" | branch:{ui.state.current_branch}"
        if ui.state.current_branch
        else ""
    )
    return (
        f" {ui.state.workspace_name}{branch_part} | "
        f"{plan_badge}{ui.state.status_text} | "
        f"{ui.state.large_model} | context {pct}% "
    )


def _context_details_text(ui) -> str:
    cs = ui.state.context_stats
    total = cs.selected_count + cs.rejected_count
    lines = [
        "◈ DETALHES DO MOTOR DE CONTEXTO / CONTEXT ENGINE ◈",
        f"• Estado do Índice: {cs.index_state or 'READY'} (Geração: {cs.index_generation})",
        f"• Candidatos: {cs.selected_count} selecionados / {cs.rejected_count} rejeitados (Total: {total})",
        f"• Cobertura: {cs.coverage:.0%}{' [DEGRADADO]' if cs.degraded else ''}",
        f"• Tokens no Pacote: {cs.context_tokens} tokens",
        f"• Filtro Semântico: {cs.filter_source or 'N/A'}{f' ({cs.filter_fallback_reason})' if cs.filter_fallback_reason else ''} - Latência: {int(cs.filter_latency_ms)}ms",
    ]
    if cs.partial_reason:
        lines.append(f"• Motivo parcial: {cs.partial_reason}")
    if cs.index_scanned or cs.index_updated or cs.index_deleted:
        lines.append(f"• Índice: {cs.index_scanned} escaneados, {cs.index_updated} atualizados, {cs.index_deleted} removidos")
    return "\n".join(lines)


def _toast_text(ui) -> str:
    toasts = ui.state.active_toasts()
    if not toasts:
        return ""
    t = toasts[-1]
    if ui.state.active_overlay is None and not ui.prompt_buffer.text.strip():
        return f" {t.text}\n  [Esc/Enter: Fechar Aviso]"
    return f" {t.text}"

