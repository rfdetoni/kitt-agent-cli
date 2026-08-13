from __future__ import annotations

from kitt.ui.layout import LayoutDimensions
from kitt.ui.state import UIState, safe_text


def render_snapshot(state: UIState, width: int, height: int) -> str:
    """ANSI-free in-memory renderer for golden and CI assertions."""
    dims = LayoutDimensions(width, height)
    lines = [f"K.I.T.T. | {state.status_text}"[:width], f"Workspace: {state.workspace_path}"[:width]]
    if state.route == "home":
        lines += ["", "[        █▓        ]", "Knowledge & Inference Task Tool", "", "Ask K.I.T.T.: " + state.input_draft]
    else:
        lines.append("-" * min(width, 60))
        for block in state.transcript:
            lines.append(f"{block.kind.upper()}: {safe_text(block.text)}"[:width])
        if dims.mode == "desktop" and state.sidebar_open:
            lines.append(f"SIDEBAR: {state.large_model} | tokens {state.tokens_used}")
        lines.append("PROMPT: " + state.input_draft)
    if state.pending_approval:
        lines += ["APPROVAL REQUIRED", f"{state.pending_approval['tool_name']} [y/n]"]
    if state.active_overlay:
        lines.append(f"OVERLAY: {state.active_overlay}")
    if state.toasts:
        lines.append("NOTICE: " + state.toasts[-1].text)
    lines.append(f"{state.status_text} | {state.large_model} | {state.tokens_used}/{state.context_window}"[:width])
    return "\n".join(lines[:height])
