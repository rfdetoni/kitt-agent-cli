from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error

import pytest

from kitt.llm.client import _wait_for_local_kitt_proxy
from kitt.llm.http_security import TransportCancellation

from kitt.context_filter.prompt_budget import TokenCounter
from kitt.core.execution_budget import ExecutionBudgetLedger
from kitt.core.turn_model import TurnModelMixin
from kitt.domain.entities import ModelProfile
from kitt.llm.domain import ProviderConnectionError
from kitt.llm.retry import RetryConfig, RetryPolicy
from kitt_protocol import ExecutionBudget


def _budget() -> ExecutionBudget:
    return ExecutionBudget(
        max_model_calls=6,
        max_input_tokens=1,
        max_output_tokens=1,
        max_total_tokens=2,
        max_cost=10.0,
        max_duration_ms=600_000,
        max_tool_calls=4,
        max_subagents=0,
    )


def test_pre_accept_connection_refusal_is_retryable_but_ambiguous_close_is_not():
    policy = RetryPolicy(
        RetryConfig(max_retries=2, retry_timeouts=False, jitter_ratio=0.0)
    )

    assert policy.is_retryable(
        ProviderConnectionError(
            "Could not connect to KITT reverse proxy: "
            "<urlopen error [Errno 111] Connection refused>"
        )
    )
    assert not policy.is_retryable(
        ProviderConnectionError(
            "KITT reverse proxy connection closed after request transmission"
        )
    )


def test_reverse_proxy_reserves_actual_prompt_usage_not_full_allowance(tmp_path):
    ledger = ExecutionBudgetLedger(_budget())
    captured = {}

    profile = ModelProfile(
        backend="kitt-reverse-proxy",
        protocol="kitt-reverse-proxy",
        model="gemini-web",
        base_url="http://127.0.0.1:3000",
        context_window=32_000,
        max_output_tokens=1200,
        supports_tools=True,
    )

    class FakeClient:
        def __init__(self):
            self.profile = profile

        def chat_stream(self, messages, **kwargs):
            captured["budget_at_dispatch"] = ledger.snapshot()
            captured["request_metadata"] = dict(kwargs["request_metadata"])
            kwargs["usage_callback"](
                {
                    "prompt_tokens": 5000,
                    "completion_tokens": 6000,
                    "upstream_attempts": 1,
                }
            )
            yield "ok"

    class Harness(TurnModelMixin):
        root_path = tmp_path
        registry = None
        reasoning_effort = 50
        execution_budgets = {"turn-1": ledger}
        _attachment_paths_by_turn = {}
        _attachment_wire_sent = set()
        _logical_request_ids = {}
        _request_parent_ids = {}
        _request_task_ids = {}
        _agent_role_policies = {}
        _transport_cancellations = {}

        @staticmethod
        def _cancel_requested(_turn_id):
            return False

        @staticmethod
        def _record_latency(*_args, **_kwargs):
            return None

    messages = [{"role": "user", "content": "inspect the workspace"}]
    system_prompt = "Use host tools."
    expected_estimate = (
        TokenCounter.count_tokens(system_prompt)
        + TokenCounter.count_tokens(messages[0]["content"])
    )

    output = list(
        Harness()._stream_execution_response(
            FakeClient(),
            messages,
            system_prompt,
            conversation_id="conv-1",
            turn_id="turn-1",
            route="agent-loop",
            session_key="conversation:conv-1",
            tool_definitions=[
                {
                    "name": "kitt_runtime",
                    "description": "runtime",
                    "args": {"operation": "string", "arguments": "object"},
                }
            ],
            context_envelope={"schema_version": 1, "epoch": "e1", "segments": []},
        )
    )

    assert output[-1][0] == "ok"
    dispatched = captured["budget_at_dispatch"]
    assert dispatched["usage"]["input_tokens"] == expected_estimate
    assert "max_prompt_tokens" not in captured["request_metadata"]
    assert 540_000 <= captured["request_metadata"]["deadline_ms"] <= 600_000
    assert ledger.snapshot()["usage"]["input_tokens"] == 5000
    assert ledger.snapshot()["usage"]["output_tokens"] == 6000
    assert not ledger.token_limits_enforced

def test_local_proxy_waits_for_listener_without_replaying_chat():
    cancellation = TransportCancellation()
    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    with patch(
        "kitt.llm.client.secure_urlopen",
        side_effect=[refused, nullcontext(SimpleNamespace(status=200))],
    ) as probe, patch.object(cancellation, "wait") as sleep:
        assert _wait_for_local_kitt_proxy("http://127.0.0.1:3000/v1", cancellation)
    assert probe.call_count == 2
    assert sleep.call_count == 1
    assert all(call.args[0].full_url.endswith("/healthz") for call in probe.call_args_list)


def test_proxy_wait_aborts_with_actionable_error_if_listener_never_starts():
    cancellation = TransportCancellation()
    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    with patch("kitt.llm.client.secure_urlopen", side_effect=refused):
        with pytest.raises(ProviderConnectionError, match="did not become ready"):
            _wait_for_local_kitt_proxy(
                "http://127.0.0.1:3000", cancellation, timeout_seconds=0
            )


def test_proxy_wait_does_not_probe_remote_endpoints():
    with patch("kitt.llm.client.secure_urlopen") as probe:
        assert not _wait_for_local_kitt_proxy(
            "https://provider.example/v1", TransportCancellation()
        )
    probe.assert_not_called()
