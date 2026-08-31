from __future__ import annotations

import math
import sqlite3
import threading

import pytest

from kitt.context.cache import ContextCache
from kitt.context.candidates import ContextCandidate, ContextSelector
from kitt.context.rag.config import RagConfig
from kitt.context.rag.fusion import RankedSource, ReciprocalRankFusion
from kitt.context.rag.semantic import SemanticCandidateReranker
from kitt.context_engine.engine import ContextEngine
from kitt.history.database import _InMemoryConnectionContext
from kitt.index.parser_registry import ParserAdapter
from kitt.index.repository import RepositoryIndex
from kitt.tools.agent_loop import AgentLoop


def candidate(cid: str, path: str, start: int, end: int, content: str) -> ContextCandidate:
    return ContextCandidate(
        candidate_id=cid,
        source_type="file",
        path=path,
        start_line=start,
        end_line=end,
        content_hash=cid,
        estimated_tokens=max(1, len(content) // 4),
        relevance=0.7,
        confidence=0.8,
        freshness=1.0,
        mandatory=False,
        trust_level="WORKSPACE_DATA",
        dependencies=(),
        selection_reason=cid,
        representation="SLICE",
        content=content,
    )


class CountingProvider:
    model = "fake"
    base_url = "local"

    def __init__(self):
        self.batch_sizes: list[int] = []

    def embed(self, texts):
        self.batch_sizes.append(len(texts))
        result = []
        for text in texts:
            lowered = text.lower()
            result.append([1.0, 0.0] if "lock" in lowered else [0.0, 1.0])
        return result


class NonFiniteProvider:
    def embed(self, texts):
        return [[1.0, 0.0], *([[float("nan"), 0.0]] * (len(texts) - 1))]


def test_rag_config_rejects_non_finite_env(monkeypatch):
    monkeypatch.setenv("KITT_RAG_WEIGHT_SEMANTIC", "nan")
    monkeypatch.setenv("KITT_RAG_EMBEDDING_TIMEOUT", "inf")
    config = RagConfig.from_env()
    assert math.isfinite(config.semantic_weight)
    assert math.isfinite(config.embedding_timeout_seconds)


def test_semantic_document_cache_does_not_cache_query():
    provider = CountingProvider()
    reranker = SemanticCandidateReranker(provider, cache_size=16)
    items = [
        candidate("a", "a.py", 1, 3, "with lock: save()"),
        candidate("b", "b.py", 1, 3, "render docs"),
    ]
    assert reranker.rank("fix lock race", items).degraded is False
    assert reranker.rank("fix lock race again", items).degraded is False
    assert provider.batch_sizes == [3, 1]


def test_semantic_non_finite_vector_degrades():
    result = SemanticCandidateReranker(NonFiniteProvider()).rank(
        "lock", [candidate("a", "a.py", 1, 2, "lock")]
    )
    assert result.degraded is True
    assert result.candidates == ()


def test_rrf_coalesces_overlapping_ranges():
    first = candidate("a", "x.py", 10, 20, "alpha beta gamma")
    second = candidate("b", "x.py", 12, 18, "beta gamma")
    fused = ReciprocalRankFusion(k=20).fuse(
        [RankedSource("lex", [first]), RankedSource("fts", [second])]
    )
    assert len(fused) == 1
    assert "lex" in fused[0].selection_reason and "fts" in fused[0].selection_reason


def test_context_selector_rejects_highly_overlapping_ranges():
    first = candidate("a", "x.py", 10, 20, "alpha beta gamma delta")
    second = candidate("b", "x.py", 12, 18, "alpha beta gamma")
    selected, discarded = ContextSelector.select_candidates([first, second], 1000)
    assert len(selected) == 1
    assert len(discarded) == 1


def test_context_cache_namespace_isolated():
    class Compiled:
        pass

    cache = ContextCache()
    value = Compiled()
    cache.put("p", 1, value, 100, namespace="workspace:a")
    assert cache.get("p", 1, 100, namespace="workspace:a") is value
    assert cache.get("p", 1, 100, namespace="workspace:b") is None


def test_in_memory_commit_failure_propagates():
    class BrokenConnection:
        def commit(self):
            raise sqlite3.OperationalError("commit failed")

        def rollback(self):
            pass

    ctx = _InMemoryConnectionContext(BrokenConnection(), threading.RLock())  # type: ignore[arg-type]
    with pytest.raises(sqlite3.OperationalError, match="commit failed"):
        with ctx:
            pass


def test_agent_repair_must_verify_before_done():
    loop = AgentLoop(max_steps=10, max_same_failures=3)
    loop.state = "VERIFY"
    assert loop.record_step("pytest", False, "", "failed") == "REPAIR"
    assert loop.record_step("edit", True, "fixed") == "VERIFY"
    assert loop.record_step("pytest", True, "green") == "DONE"


def test_repository_graph_restored_after_reopen(tmp_path):
    (tmp_path / "a.py").write_text("from b import B\nclass A:\n    value = B\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("class B:\n    pass\n", encoding="utf-8")
    index = RepositoryIndex(tmp_path)
    index.build_or_update()
    assert "b.py" in index.graph.expand_neighborhood({"a.py"}, max_hops=1)
    index.close()

    reopened = RepositoryIndex(tmp_path)
    try:
        assert "b.py" in reopened.graph.expand_neighborhood({"a.py"}, max_hops=1)
    finally:
        reopened.close()


def test_parser_version_change_forces_reindex(tmp_path, monkeypatch):
    source = tmp_path / "a.py"
    source.write_text("def hello():\n    return 1\n", encoding="utf-8")
    index = RepositoryIndex(tmp_path, in_memory=True)
    try:
        index.build_or_update()
        original = index.parser_registry.adapter_for

        def v2(path):
            adapter = original(path)
            return ParserAdapter(adapter.id, "v2", adapter.extensions)

        monkeypatch.setattr(index.parser_registry, "adapter_for", v2)
        stats = index.update_paths(["a.py"])
        assert stats["updated"] == 1
    finally:
        index.close()


def test_context_engine_does_not_return_stale_explicit_file(tmp_path):
    source = tmp_path / "a.py"
    source.write_text("value = 1\n", encoding="utf-8")
    engine = ContextEngine(persistence_enabled=False, refresh_interval_seconds=60)
    try:
        first = engine.get_relevant_context("review a.py", root_dir=str(tmp_path), max_tokens=800)
        assert first and "value = 1" in first[0].content
        source.write_text("value = 2\n", encoding="utf-8")
        second = engine.get_relevant_context("review a.py", root_dir=str(tmp_path), max_tokens=800)
        assert second and "value = 2" in second[0].content
        assert "value = 1" not in second[0].content
    finally:
        engine.close()
