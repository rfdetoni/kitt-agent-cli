"""Deterministic specialist model decisions, privacy, fallback and persistence."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitt.agents.model_router import (
    AgentLLMPolicy, AgentModelRouter, AgentModelStore, apply_child_model_route,
)
from kitt.domain.entities import ModelProfile


def _models():
    return {
        "execute": ModelProfile(backend="ollama", model="coder", base_url="http://127.0.0.1:11434",
                                supports_tools=True, context_window=16384),
        "review-proxy": ModelProfile(backend="kitt-reverse-proxy", model="review",
                                     base_url="http://127.0.0.1:3001", supports_tools=True),
        "creative-proxy": ModelProfile(backend="kitt-reverse-proxy", model="creative",
                                       base_url="http://127.0.0.1:3000", supports_tools=True),
        "review-direct": ModelProfile(backend="openai", model="critical",
                                      base_url="https://api.openai.com/v1", supports_tools=True),
        "context": ModelProfile(backend="ollama", model="small",
                                 base_url="http://127.0.0.1:11434", supports_tools=False),
    }


def _route():
    return AgentModelRouter(SimpleNamespace(config=SimpleNamespace(profiles=_models())))


def test_different_agents_can_select_distinct_providers():
    router = _route()
    code = router.resolve("IMPLEMENT", AgentLLMPolicy(), privacy_mode="hybrid_redacted")
    critic = router.resolve(
        "REVIEW",
        AgentLLMPolicy.parse({"mode": "fixed", "primary": "review-proxy"}),
        privacy_mode="hybrid_redacted",
    )
    assert code["primary"] == "execute"
    assert critic["primary"] == "review-proxy"
    assert critic["fallbacks"] == []
    assert critic["profile"]["backend"] == "kitt-reverse-proxy"


def test_preferred_fallback_is_explicit_and_ordered():
    policy = AgentLLMPolicy.parse({
        "mode": "preferred", "primary": "review-proxy",
        "fallbacks": ["review-direct", "execute"],
        "temperature": 0.15, "max_output_tokens": 2048, "reasoning": "high",
    })
    decision = _route().resolve("REVIEW", policy, privacy_mode="hybrid_redacted")
    assert decision["primary"] == "review-proxy"
    assert [f["name"] for f in decision["fallbacks"]] == ["review-direct", "execute"]
    assert decision["profile"]["temperature"] == 0.15
    assert decision["reasoning"] == "high"


def test_local_only_rejects_remote_primary_and_filters_automatic_fallbacks():
    router = _route()
    with pytest.raises(PermissionError):
        router.resolve("REVIEW", AgentLLMPolicy.parse({
            "mode": "fixed", "primary": "review-proxy",
        }), privacy_mode="offline")
    policy = AgentLLMPolicy.parse({"mode": "auto", "remote_egress": "deny"})
    selected = router.resolve("REVIEW", policy, privacy_mode="hybrid_redacted")
    assert selected["primary"] in {"execute", "context"}
    assert all(profile["profile"]["backend"] == "ollama" for profile in selected["fallbacks"])


@pytest.mark.parametrize("bad", [
    {"mode": "fixed"},
    {"mode": "fixed", "primary": "execute", "fallbacks": ["review-proxy"]},
    {"mode": "preferred", "primary": "missing", "fallbacks": []},
    {"temperature": float("nan")},
    {"remote_egress": "always"},
    {"max_output_tokens": "1024"},
    {"unknown_field": "anything"},
])
def test_rejects_invalid_model_policy(bad):
    if bad.get("primary") == "missing":
        with pytest.raises(ValueError):
            _route().resolve("REVIEW", AgentLLMPolicy.parse(bad), privacy_mode="hybrid_redacted")
    else:
        with pytest.raises(ValueError):
            AgentLLMPolicy.parse(bad)


def test_model_preferences_roundtrip_atomic(tmp_path):
    store = AgentModelStore(tmp_path)
    assert store.load() == {}
    policy = AgentLLMPolicy.parse({
        "mode": "preferred", "primary": "review-proxy", "fallbacks": ["execute"],
        "remote_egress": "inherit", "require_tools": True,
    })
    store.save("critic", policy)
    assert store.load()["critic"] == policy
    assert (tmp_path / ".kitt-agent-models.json").is_file()


def test_snapshot_never_contains_plaintext_credentials():
    profiles = _models()
    profiles["review-direct"] = replace(profiles["review-direct"],
                                        api_key="FORBIDDEN_SECRET",
                                        credential_ref="env:OPENAI_API_KEY")
    router = AgentModelRouter(SimpleNamespace(config=SimpleNamespace(profiles=profiles)))
    result = router.resolve("REVIEW", AgentLLMPolicy.parse({
        "mode": "fixed", "primary": "review-direct",
    }), privacy_mode="hybrid_redacted")
    assert result["profile"]["api_key"] == ""
    assert result["profile"]["credential_ref"] == "env:OPENAI_API_KEY"


def test_child_runtime_uses_immutable_model_snapshot_and_proxy_scope():
    choice = _route().resolve("REVIEW", AgentLLMPolicy.parse({
        "mode": "preferred", "primary": "review-proxy", "fallbacks": ["execute"],
        "reasoning": "high",
    }), privacy_mode="hybrid_redacted")
    processor = SimpleNamespace()
    runtime = SimpleNamespace(processor=processor,
                              config=SimpleNamespace(privacy_mode="hybrid_redacted",
                                                     provider_park_seconds=5))
    apply_child_model_route(runtime, {"lineage": {"model_route": choice}})
    name, profile = processor._specialist_execution
    assert name == "review-proxy"
    assert profile.model == "review"
    assert processor.reasoning_effort == 90
    wrapped = processor.provider_client_factory(profile)
    assert [p.model for p in wrapped.alternatives] == ["coder"]
    wrapped.close()


def test_child_snapshot_privacy_revalidated_on_worker():
    choice = _route().resolve("REVIEW", AgentLLMPolicy.parse({
        "mode": "fixed", "primary": "review-proxy",
    }), privacy_mode="hybrid_redacted")
    runtime = SimpleNamespace(processor=SimpleNamespace(),
                              config=SimpleNamespace(privacy_mode="local_only",
                                                     provider_park_seconds=5))
    with pytest.raises(PermissionError):
        apply_child_model_route(runtime, {"lineage": {"model_route": choice}})
