from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]|\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def safe_text(value: object) -> str:
    return _CONTROL.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n")


import time

@dataclass
class TranscriptBlock:
    id: str
    kind: Literal["user", "assistant", "tool", "system", "error", "thought", "context"]
    text: str = ""
    status: str | None = None
    collapsed: bool = False
    call_id: str | None = None
    started_at: float = field(default_factory=time.time)
    duration_ms: int | None = None
    tokens: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


import time


@dataclass
class AgentTaskStep:
    id: str
    name: str
    role: str
    status: Literal["pending", "running", "done", "error", "cancelled"] = "pending"
    summary: str = ""
    progress: int = 0
    tokens: int = 0
    time_ms: int = 0
    kind: Literal["pipeline", "child_agent"] = "pipeline"
    child_id: str | None = None
    lane: int = 0
    scanner_phase: int = 0
    started_at: float = field(default_factory=time.time)
    error_message: str | None = None


@dataclass
class ContextRunStats:
    filter_source: str = ""          # LLM | DETERMINISTIC_BYPASS | FALLBACK
    filter_fallback_reason: str = ""
    filter_latency_ms: float = 0.0
    intent: str = ""
    index_state: str = ""            # READY | PARTIAL | BOOTSTRAP | DEGRADED | EMPTY
    index_generation: int = 0
    selected_count: int = 0
    rejected_count: int = 0
    context_tokens: int = 0
    coverage: float = 1.0
    degraded: bool = False
    duration_ms: int = 0
    index_scanned: int = 0
    index_updated: int = 0
    index_deleted: int = 0
    partial_reason: str = ""
    resolved_count: int = 0


@dataclass
class Toast:
    text: str
    persistent: bool = False
    created_at: float = field(default_factory=time.time)
    duration: float = 4.0


@dataclass
class UIState:
    route: str = "home"
    workspace_name: str = "Local Workspace"
    workspace_path: str = "."
    active_conversation_id: str | None = None
    active_turn_id: str | None = None
    small_model: str = "context"
    large_model: str = "execution"
    reasoning_effort: int = 50
    status_text: str = "SYSTEM ONLINE"
    is_thinking: bool = False
    is_executing_tool: bool = False
    active_tool_name: str | None = None
    active_tasks: list[AgentTaskStep] = field(default_factory=list)
    sidebar_open: bool = False
    active_overlay: str | None = None
    overlay_stack: list[str] = field(default_factory=list)
    transcript: list[TranscriptBlock] = field(default_factory=list)
    pending_approvals: list[dict[str, Any]] = field(default_factory=list)
    toasts: list[Toast] = field(default_factory=list)
    input_draft: str = ""
    tokens_used: int = 0
    context_window: int = 8192
    gross_saved_tokens: int = 0
    net_saved_tokens: int = 0
    context_stats: ContextRunStats = field(default_factory=ContextRunStats)
    follow_tail: bool = True
    unseen_output: bool = False
    scanner_step: int = 0
    planning_mode: bool = False
    width: int = 80
    height: int = 24
    turn_started_at: float = field(default_factory=time.time)

    def init_turn_tasks(self, prompt: str) -> None:
        self.active_tasks = [
            AgentTaskStep(
                id="core",
                name="Agente Principal (K.I.T.T.)",
                role=self.large_model,
                status="running",
                summary=f"Processando prompt ({len(prompt)} chars)...",
                progress=15,
                kind="core_agent",
            )
        ]

    def upsert_child_task(self, child_id: str, name: str, status: str,
                           summary: str = "", progress: int = 0) -> None:
        """Cria ou atualiza a linha do dashboard para um agente filho."""
        existing = next((t for t in self.active_tasks if t.child_id == child_id), None)
        if existing:
            existing.status = status
            existing.summary = safe_text(summary)
            existing.progress = progress
            return
        lane = len([t for t in self.active_tasks if t.kind == "child_agent"])
        self.active_tasks.append(AgentTaskStep(
            id=f"child-{child_id}", name=name, role="child_agent", status=status,
            summary=safe_text(summary), progress=progress, kind="child_agent",
            child_id=child_id, lane=lane + 1, scanner_phase=(lane + 1) * 5,
        ))

    def active_agent_count(self) -> int:
        return sum(1 for t in self.active_tasks if t.status in {"running", "pending"})

    @property
    def overall_progress(self) -> int:
        if not self.active_tasks:
            return 0
        active = [t for t in self.active_tasks if t.status != "pending"]
        if not active:
            return 0
        return sum(t.progress for t in active) // len(active)

    @property
    def pending_approval(self) -> dict[str, Any] | None:
        return self.pending_approvals[0] if self.pending_approvals else None

    def add_toast(self, message: str, persistent: bool = False, duration: float = 4.0) -> None:
        self.toasts.append(Toast(safe_text(message), persistent, created_at=time.time(), duration=duration))
        del self.toasts[:-5]

    def active_toasts(self) -> list[Toast]:
        now = time.time()
        return [t for t in self.toasts if t.persistent or (now - t.created_at < t.duration)]

    def clear_toasts(self) -> None:
        self.toasts.clear()


    def append_message(self, role: str, content: str) -> None:
        kind = role if role in {"user", "assistant", "tool", "system", "error", "context"} else "system"
        self.transcript.append(TranscriptBlock(f"block-{len(self.transcript)+1}", kind, safe_text(content)))
        del self.transcript[:-500]

    def push_overlay(self, name: str) -> None:
        if name in self.overlay_stack:
            self.overlay_stack.remove(name)
        self.overlay_stack.append(name)
        self.active_overlay = name

    def pop_overlay(self) -> str | None:
        closed = self.overlay_stack.pop() if self.overlay_stack else self.active_overlay
        self.active_overlay = self.overlay_stack[-1] if self.overlay_stack else None
        return closed

    def toggle_last_tool_collapse(self) -> None:
        """Ctrl+O — alterna colapso do último bloco tool/thought no transcript."""
        for block in reversed(self.transcript):
            if block.kind in {"tool", "thought"}:
                block.collapsed = not block.collapsed
                return

    def find_running_tool_block(self, call_id: str) -> TranscriptBlock | None:
        return next((b for b in self.transcript if b.call_id == call_id and b.status == "running"), None)
