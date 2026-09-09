from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.history.database import HistoryDatabase
from kitt.history.migrations import MigrationRunner
from kitt.history.repository import HistoryRepository
from kitt.metrics.gain_report import (
    estimated_tokens_from_bytes,
    render_gain,
    saving_pct,
)
from kitt.metrics.models import ToolGainMetrics
from kitt.metrics.collector import MetricsCollector
from kitt.tools.registry import ToolRegistry, ToolResult
from kitt.ui.commands import CommandRegistry


class GainMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = HistoryDatabase(root, in_memory=False)
        with self.db.get_connection() as conn:
            MigrationRunner().migrate(conn)
        self.repo = HistoryRepository(self.db)
        ws = self.repo.get_or_create_workspace(str(root))
        self.conv = self.repo.create_conversation(ws["id"], "gain")
        self.turn = "turn_gain_1"
        self.repo.save_message(self.conv["id"], self.turn, "user", "hello")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _save_gain(self, tool, raw, returned, duration=10.0, turn=None):
        return self.repo.save_tool_gain(
            self.conv["id"],
            turn or self.turn,
            tool,
            time.time(),
            duration,
            raw,
            returned,
            max(0, raw - returned),
        )

    def test_repository_gain_queries_and_stats_are_separate(self):
        self.repo.save_telemetry(
            self.conv["id"], self.turn, "code-generation",
            time.time(), 100.0, 1000, 100, 200,
        )
        self._save_gain("run_command", 1000, 100)
        self._save_gain("read_file", 500, 250)

        stats = self.repo.get_telemetry_stats(self.conv["id"])
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["input"], 1000)
        self.assertEqual(stats["saved"], 200)

        gain = self.repo.get_gain_summary(self.conv["id"])
        self.assertEqual(gain["count"], 2)
        self.assertEqual(gain["raw"], 1500)
        self.assertEqual(gain["output"], 350)
        self.assertEqual(gain["saved"], 1150)

        tools = self.repo.get_gain_by_tool(self.conv["id"])
        self.assertEqual(tools[0]["tool"], "run_command")
        self.assertEqual(tools[0]["saved"], 900)

        history = self.repo.get_gain_history(self.conv["id"])
        self.assertEqual(len(history), 2)
        daily = self.repo.get_gain_daily(self.conv["id"], days=30)
        self.assertTrue(daily)

    def test_save_tool_gain_skips_unknown_turn_instead_of_fk_failure(self):
        event = self.repo.save_tool_gain(
            self.conv["id"], "missing-turn", "search",
            time.time(), 5.0, 100, 50, 50,
        )
        self.assertIsNone(event)

    def test_collector_persists_and_flushes_tool_gain(self):
        collector = MetricsCollector(self.repo)
        collector.record_tool_gain(ToolGainMetrics(
            turn_id=self.turn,
            conversation_id=self.conv["id"],
            tool_name="git_diff",
            raw_tokens=1000,
            output_tokens=200,
            duration_ms=20.0,
        ))
        collector.flush()
        gain = self.repo.get_gain_summary(self.conv["id"])
        self.assertEqual(gain["saved"], 800)
        collector.close()

    def test_gain_report_matches_bytes_div_four_estimator(self):
        self.assertEqual(estimated_tokens_from_bytes(400), 100)
        self.assertEqual(estimated_tokens_from_bytes(401), 101)
        self.assertEqual(saving_pct(1000, 800), 80.0)
        self._save_gain("run_command", 1000, 100)
        report = render_gain(self.repo, self.conv["id"])
        self.assertIn("KITT Token Savings", report)
        self.assertIn("90.0%", report)
        self.assertIn("bytes/4", report)

        graph = render_gain(self.repo, self.conv["id"], "--graph")
        self.assertIn("30 Day Graph", graph)
        exported = render_gain(self.repo, self.conv["id"], "--all --format json")
        self.assertIn('"scope": "all"', exported)

    def test_gain_command_registered(self):
        command, arg = CommandRegistry().resolve("/gain history")
        self.assertIsNotNone(command)
        self.assertEqual(command.id, "gain")
        self.assertEqual(arg, "history")


class RegistryGainInstrumentationTests(unittest.TestCase):
    def test_registry_uses_raw_process_bytes_and_visible_result(self):
        class Metrics:
            def __init__(self):
                self.items = []
            def record_tool_gain(self, item):
                self.items.append(item)

        class Handler:
            def execute(self, args, ctx):
                return ToolResult(
                    True,
                    "compact",
                    metadata={
                        "raw_total_bytes": 4000,
                        "tokens_saved": 900,
                    },
                )

        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(tmp)
            metrics = Metrics()
            registry.metrics_collector = metrics
            registry._handlers["git_status"] = Handler()
            result = registry.execute_tool(
                "git_status",
                {},
                turn_id="t",
                conversation_id="c",
                workspace_id="w",
            )
            self.assertTrue(result.success)
            self.assertEqual(len(metrics.items), 1)
            metric = metrics.items[0]
            self.assertEqual(metric.raw_tokens, 1000)
            self.assertEqual(metric.output_tokens, 2)
            self.assertEqual(metric.tokens_saved, 998)
            registry.close()

    def test_delegated_kitt_runtime_outer_result_is_not_double_counted(self):
        class Metrics:
            def __init__(self):
                self.items = []
            def record_tool_gain(self, item):
                self.items.append(item)

        class Handler:
            def execute(self, args, ctx):
                return ToolResult(
                    True,
                    "x",
                    metadata={
                        "operation": "repo.read",
                        "effective_tool_name": "read_file",
                        "tokens_saved": 50,
                    },
                )

        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(tmp)
            metrics = Metrics()
            registry.metrics_collector = metrics
            registry._handlers["kitt_runtime"] = Handler()
            result = registry.execute_tool(
                "kitt_runtime",
                {},
                turn_id="t",
                conversation_id="c",
                workspace_id="w",
            )
            self.assertTrue(result.success)
            self.assertEqual(metrics.items, [])
            registry.close()


if __name__ == "__main__":
    unittest.main()
