import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from kitt.artifacts.store import ArtifactStore
from kitt.children.manager import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.cli.main import build_parser
from kitt.cli.operations import build_session_report, collect_incidents
from kitt.core.runtime import _build_compaction_summarizer
from kitt.domain.entities import ModelProfile
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.llm.domain import (
    ProviderConnectionError,
    ProviderProtocolError,
    ProviderRateLimitError,
)
from kitt.llm.providers.anthropic import AnthropicAdapter
from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.openai_chat import OpenAIChatAdapter
from kitt.llm.providers.openai_responses import OpenAIResponsesAdapter
from kitt.llm.retry import RetryConfig, RetryPolicy


class _Router:
    def __init__(self, profiles):
        self.profiles = profiles

    def resolve_profile_for_task(self, route):
        return route, self.profiles[route]


class _SummaryClient:
    calls = []

    def __init__(self, profile, retry_policy=None):
        self.profile = profile
        self.retry_policy = retry_policy
        self.calls.append(profile.model)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def chat(self, messages, **kwargs):
        if kwargs.get("reasoning_effort") is not None:
            raise AssertionError("maintenance summary must not request reasoning")
        return "maintenance summary"


class RuntimeResilienceTests(unittest.TestCase):
    def test_compaction_uses_distinct_maintenance_route(self):
        execution = ModelProfile(
            backend="openai",
            model="execution",
            base_url="https://example.invalid",
            context_window=8192,
        )
        maintenance = ModelProfile(
            backend="ollama",
            model="maintenance",
            base_url="http://localhost:11434",
            context_window=8192,
            max_output_tokens=512,
        )
        router = _Router(
            {
                "code-generation": execution,
                "summarize": maintenance,
                "context-gather": maintenance,
            }
        )
        _SummaryClient.calls = []
        with patch("kitt.core.runtime.LLMClient", _SummaryClient):
            summary = _build_compaction_summarizer(router)(
                "user: changed src/app.py"
            )

        self.assertEqual(summary, "maintenance summary")
        self.assertEqual(_SummaryClient.calls, ["maintenance"])

    def test_compaction_falls_back_when_only_execution_lane_is_available(self):
        execution = ModelProfile(
            backend="openai",
            model="same-model",
            base_url="https://example.invalid",
            context_window=8192,
        )
        router = _Router(
            {
                "code-generation": execution,
                "summarize": execution,
                "context-gather": execution,
            }
        )
        with patch(
            "kitt.core.runtime.LLMClient",
            side_effect=AssertionError("must not run"),
        ):
            summary = _build_compaction_summarizer(router)(
                "decision: keep durable state"
            )
        self.assertIn("decision: keep durable state", summary)

    def test_rate_limit_retry_honors_server_delay(self):
        attempts = 0

        def stream():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ProviderRateLimitError(
                    "rate limited",
                    retry_after=2.5,
                )
            yield "ok"

        policy = RetryPolicy(
            RetryConfig(
                max_retries=1,
                base_delay_ms=10,
                max_delay_ms=100,
            )
        )
        with patch("kitt.llm.retry.time.sleep") as sleep:
            self.assertEqual(
                list(policy.execute_with_retry(stream)),
                ["ok"],
            )
        self.assertEqual(attempts, 2)
        sleep.assert_called_once_with(2.5)

    def test_partial_stream_is_never_replayed_by_generic_retry(self):
        attempts = 0

        def stream():
            nonlocal attempts
            attempts += 1
            yield "partial"
            raise ProviderConnectionError(
                "HTTP 503 temporarily unavailable"
            )

        policy = RetryPolicy(
            RetryConfig(
                max_retries=3,
                base_delay_ms=0,
                max_delay_ms=0,
            )
        )
        with self.assertRaises(ProviderConnectionError):
            list(policy.execute_with_retry(stream))
        self.assertEqual(attempts, 1)

    def test_completed_children_do_not_exhaust_future_spawn_admission(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = HistoryDatabase(tmp)
            history = HistoryRepository(db)
            workspace = history.get_or_create_workspace(tmp)
            conversation = history.create_conversation(
                workspace["id"],
                "parent",
            )
            repo = ChildRepository(db)
            artifacts = ArtifactStore(tmp, db)
            for index in range(10):
                child = repo.create(
                    conversation["id"],
                    "turn-history",
                    f"done-{index}",
                    "historical task",
                    1,
                    "context",
                    [],
                    [],
                    128,
                    10,
                )
                repo.update(
                    child.id,
                    state="COMPLETED",
                    completed_at=time.time(),
                )

            manager = ChildAgentManager(
                tmp,
                repo,
                artifacts,
                workspace_id=workspace["id"],
                max_children=2,
            )
            try:
                child = manager.spawn(
                    conversation["id"],
                    "turn-current",
                    name="fresh",
                    task="new task",
                    worker=lambda _task: "ok",
                )
                finished = manager.wait(child.id, timeout=5)
                self.assertEqual(finished.state, "COMPLETED")
            finally:
                manager.close()
                db.close()

    def test_direct_provider_streams_require_protocol_completion(self):
        request = LLMRequest(
            model="model",
            messages=[{"role": "user", "content": "hello"}],
            base_url="https://example.invalid",
        )
        response = MagicMock()
        response.__iter__.return_value = iter(
            [
                b'data: {"choices":[{"delta":{"content":"partial"}}]}\n'
            ]
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with patch(
            "kitt.llm.providers.openai_chat.secure_urlopen",
            return_value=response,
        ):
            with self.assertRaises(ProviderProtocolError):
                list(OpenAIChatAdapter().stream(request))

    def test_anthropic_accepts_native_message_stop_marker(self):
        request = LLMRequest(
            model="claude",
            messages=[{"role": "user", "content": "hello"}],
            base_url="https://api.anthropic.com",
        )
        response = MagicMock()
        response.__iter__.return_value = iter(
            [
                b'data: {"type":"content_block_delta","delta":{"text":"ok"}}\n',
                b'data: {"type":"message_stop"}\n',
            ]
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with patch(
            "kitt.llm.providers.anthropic.secure_urlopen",
            return_value=response,
        ):
            self.assertEqual(
                list(AnthropicAdapter().stream(request)),
                ["ok"],
            )

    def test_responses_accepts_native_completed_event(self):
        request = LLMRequest(
            model="gpt",
            messages=[{"role": "user", "content": "hello"}],
            base_url="https://api.openai.com",
        )
        response = MagicMock()
        response.__iter__.return_value = iter(
            [
                b'data: {"type":"response.output_text.delta","delta":"ok"}\n',
                b'data: {"type":"response.completed"}\n',
            ]
        )
        response.__enter__.return_value = response
        response.__exit__.return_value = None

        with patch(
            "kitt.llm.providers.openai_responses.secure_urlopen",
            return_value=response,
        ):
            self.assertEqual(
                list(OpenAIResponsesAdapter().stream(request)),
                ["ok"],
            )

    def test_session_report_enriches_state_and_sanitizes_title(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = HistoryDatabase(tmp)
            history = HistoryRepository(db)
            workspace = history.get_or_create_workspace(tmp)
            conversation = history.create_conversation(
                workspace["id"],
                "saved",
            )
            with db.get_connection() as conn:
                conn.execute(
                    """INSERT INTO turns
                       (id,conversation_id,ordinal,state,mode,semantic_intent,
                        started_at,completed_at,error_code)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        "turn-1",
                        conversation["id"],
                        1,
                        "FAILED",
                        "auto",
                        "DEBUG",
                        100.0,
                        120.0,
                        "boom",
                    ),
                )
                conn.execute(
                    """INSERT INTO telemetry_events
                       (id,conversation_id,turn_id,route,start_time,duration_ms,
                        input_tokens,output_tokens,tokens_saved)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        "evt-1",
                        conversation["id"],
                        "turn-1",
                        "routing:test:failure",
                        100.0,
                        20.0,
                        30,
                        7,
                        0,
                    ),
                )
            db.close()

            rows = build_session_report(
                tmp,
                [
                    {
                        "id": conversation["id"],
                        "title": "Bad\nTitle",
                        "status": "ACTIVE",
                    }
                ],
                active_id=conversation["id"],
                now=200.0,
            )
            self.assertEqual(rows[0]["title"], "Bad Title")
            self.assertEqual(rows[0]["last_state"], "FAILED")
            self.assertEqual(rows[0]["last_error"], "boom")
            self.assertEqual(rows[0]["input_tokens"], 30)
            self.assertEqual(rows[0]["output_tokens"], 7)
            self.assertTrue(rows[0]["active"])
            self.assertEqual(rows[0]["age"], "1m")

    def test_incident_timeline_filters_noise_and_session(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            log_dir = Path(tmp) / ".kitt" / "logs"
            log_dir.mkdir(parents=True)
            now = datetime(
                2026,
                9,
                24,
                18,
                0,
                tzinfo=timezone.utc,
            )
            noteworthy = {
                "ts": (
                    now - timedelta(minutes=10)
                ).isoformat().replace("+00:00", "Z"),
                "level": "ERROR",
                "module": "kitt.llm",
                "msg": "provider timeout",
                "extra_data": {
                    "event": "llm.response.error",
                    "conversation_id": "conv-123",
                    "error": {"message": "timed out"},
                },
            }
            noise = {
                "ts": (
                    now - timedelta(minutes=5)
                ).isoformat().replace("+00:00", "Z"),
                "level": "INFO",
                "module": "kitt.ui",
                "msg": "render complete",
                "extra_data": {
                    "event": "ui.render",
                    "conversation_id": "conv-123",
                },
            }
            (log_dir / "agent-cli.log").write_text(
                json.dumps(noteworthy)
                + "\n"
                + json.dumps(noise)
                + "\n",
                encoding="utf-8",
            )

            incidents = collect_incidents(
                tmp,
                since="1h",
                session="conv-123",
                now=now,
            )
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0]["subject"], "conv-123")
            self.assertEqual(incidents[0]["detail"], "timed out")

    def test_cli_exposes_operator_flags(self):
        sessions = build_parser().parse_args(
            ["sessions", "--json", "--all", "--limit", "7"]
        )
        self.assertTrue(sessions.json_output)
        self.assertTrue(sessions.show_all)
        self.assertEqual(sessions.limit, 7)

        incident = build_parser().parse_args(
            [
                "incident",
                "--since",
                "30m",
                "--session",
                "abc",
                "--json",
            ]
        )
        self.assertEqual(incident.since, "30m")
        self.assertEqual(incident.session, "abc")
        self.assertTrue(incident.json_output)


if __name__ == "__main__":
    unittest.main()
