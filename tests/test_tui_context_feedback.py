import unittest
from kitt.core.turn_events import FilterCompleted, ContextResolved, ContextBuildCompleted
from kitt.context_filter.semantic_filter import SemanticFilterResult
from kitt.domain.entities import SemanticTask, ContextPlan
from kitt.ui.state import UIState, ContextRunStats, TranscriptBlock
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.components.context_panel import ContextPanelComponent
from kitt.ui.components.status_bar import StatusBarComponent


class TestTUIContextFeedback(unittest.TestCase):
    def test_filter_completed_healthy_does_not_pollute_transcript(self):
        state = UIState()
        task = SemanticTask(original_prompt="test", intent="IMPLEMENT")
        plan = ContextPlan()
        filter_res = SemanticFilterResult(task=task, plan=plan, source="LLM", latency_ms=120.0)
        event = FilterCompleted(filter_res=filter_res)

        reduce_ui_event(state, event)

        self.assertEqual(state.context_stats.filter_source, "LLM")
        self.assertEqual(state.context_stats.filter_latency_ms, 120.0)
        self.assertEqual(state.context_stats.intent, "IMPLEMENT")
        # Healthy turn: no extra context warning in transcript
        self.assertEqual(len([b for b in state.transcript if b.kind == "context"]), 0)

    def test_filter_completed_fallback_emits_warning_transcript_block(self):
        state = UIState()
        task = SemanticTask(original_prompt="test", intent="ASK")
        plan = ContextPlan()
        filter_res = SemanticFilterResult(
            task=task, plan=plan, source="FALLBACK", fallback_reason="LLM timeout", latency_ms=500.0
        )
        event = FilterCompleted(filter_res=filter_res)

        reduce_ui_event(state, event)

        self.assertEqual(state.context_stats.filter_source, "FALLBACK")
        self.assertEqual(state.context_stats.filter_fallback_reason, "LLM timeout")
        ctx_blocks = [b for b in state.transcript if b.kind == "context"]
        self.assertEqual(len(ctx_blocks), 1)
        self.assertIn("fallback", ctx_blocks[0].text)
        self.assertIn("LLM timeout", ctx_blocks[0].text)
        self.assertEqual(ctx_blocks[0].status, "warning")

    def test_context_resolved_updates_resolved_count(self):
        state = UIState()
        event = ContextResolved(resolved_count=5)

        reduce_ui_event(state, event)

        self.assertEqual(state.context_stats.resolved_count, 5)

    def test_context_build_completed_healthy_is_silent_in_transcript(self):
        state = UIState()
        state.init_turn_tasks("test prompt")
        event = ContextBuildCompleted(
            index_generation=42,
            index_state="READY",
            selected_count=7,
            rejected_count=4,
            total_tokens=1900,
            coverage=1.0,
            degraded=False,
            duration_ms=85,
        )

        reduce_ui_event(state, event)

        self.assertEqual(state.context_stats.index_state, "READY")
        self.assertEqual(state.context_stats.selected_count, 7)
        self.assertEqual(state.context_stats.rejected_count, 4)
        self.assertEqual(state.context_stats.coverage, 1.0)
        self.assertFalse(state.context_stats.degraded)
        # Silent by default on healthy build
        self.assertEqual(len([b for b in state.transcript if b.kind == "context"]), 0)
        # Updates core_task summary
        core_task = next(t for t in state.active_tasks if t.id == "core")
        self.assertIn("7/11 candidatos", core_task.summary)
        self.assertIn("100%", core_task.summary)

    def test_context_build_completed_degraded_emits_transcript_block(self):
        state = UIState()
        event = ContextBuildCompleted(
            index_generation=12,
            index_state="PARTIAL",
            selected_count=3,
            rejected_count=2,
            total_tokens=800,
            coverage=0.6,
            degraded=True,
            partial_reason="file limit reached",
        )

        reduce_ui_event(state, event)

        self.assertEqual(state.context_stats.index_state, "PARTIAL")
        self.assertTrue(state.context_stats.degraded)
        ctx_blocks = [b for b in state.transcript if b.kind == "context"]
        self.assertEqual(len(ctx_blocks), 1)
        self.assertIn("PARTIAL", ctx_blocks[0].text)
        self.assertIn("file limit reached", ctx_blocks[0].text)
        self.assertEqual(ctx_blocks[0].status, "error")

    def test_context_panel_component_render(self):
        state = UIState()
        panel = ContextPanelComponent()
        # Empty state renders nothing
        self.assertEqual(panel.render(state), "")

        state.context_stats = ContextRunStats(
            index_state="READY",
            index_generation=42,
            selected_count=7,
            rejected_count=4,
            context_tokens=1900,
            coverage=1.0,
            degraded=False,
            filter_source="LLM",
            filter_latency_ms=120.0,
        )
        rendered = panel.render(state, width=80)
        self.assertIn("◈", rendered)
        self.assertIn("Contexto", rendered)
        self.assertIn("idx:READY(gen 42)", rendered)
        self.assertIn("sel 7/11", rendered)
        self.assertIn("cov 100%", rendered)
        self.assertIn("1.9k tok", rendered)
        self.assertIn("filtro:LLM(120ms)", rendered)

        # Narrow width fallback
        narrow = panel.render(state, width=30)
        self.assertIn("Ctx: 7/11 (100%)", narrow)

    def test_status_bar_component_includes_context_summary(self):
        state = UIState()
        state.context_stats = ContextRunStats(
            selected_count=5,
            rejected_count=3,
            coverage=0.85,
        )
        sb = StatusBarComponent()
        rendered = sb.render(state, width=80)
        self.assertIn("Ctx: 5/8 (85%)", rendered)


if __name__ == "__main__":
    unittest.main()
