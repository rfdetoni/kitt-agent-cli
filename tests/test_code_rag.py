from __future__ import annotations

from types import SimpleNamespace

from kitt.context.candidates import ContextCandidate
from kitt.context.rag.config import RagConfig
from kitt.context.rag.fusion import RankedSource, ReciprocalRankFusion
from kitt.context.rag.native_search import NativeLexicalRetriever
from kitt.context.rag.semantic import SemanticCandidateReranker
from kitt.context.retrieval import HybridRetrievalPipeline
from kitt.index.repository import RepositoryIndex


def _candidate(candidate_id: str, path: str, content: str, relevance: float = 0.5):
    return ContextCandidate(
        candidate_id=candidate_id,
        source_type="file",
        path=path,
        start_line=1,
        end_line=3,
        content_hash=candidate_id,
        estimated_tokens=max(1, len(content) // 4),
        relevance=relevance,
        confidence=0.8,
        freshness=1.0,
        mandatory=False,
        trust_level="WORKSPACE_DATA",
        dependencies=(),
        selection_reason=candidate_id,
        representation="SLICE",
        content=content,
    )


class KeywordEmbeddingProvider:
    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if any(word in lowered for word in ("concurrent", "lock", "race", "thread", "simultaneous")):
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


class BrokenEmbeddingProvider:
    def embed(self, texts):
        raise RuntimeError("embedding daemon unavailable")


class FakeRustEngine:
    def __init__(self, hits=None):
        self.status = SimpleNamespace(backend="rust", available=True)
        self.hits = list(hits or [])
        self.calls = 0

    def search(self, query, **kwargs):
        self.calls += 1
        assert kwargs["regex"] is True
        return {"hits": list(self.hits)}


class FakeFallbackEngine:
    def __init__(self):
        self.status = SimpleNamespace(backend="python", available=False)
        self.calls = 0

    def search(self, query, **kwargs):
        self.calls += 1
        raise AssertionError("Python scanner fallback must not be invoked")


def test_rrf_rewards_candidate_supported_by_multiple_retrievers():
    shared_a = _candidate("lex-a", "a.py", "alpha")
    shared_b = _candidate("graph-a", "a.py", "alpha dependency")
    only_b = _candidate("lex-b", "b.py", "beta")

    fused = ReciprocalRankFusion(k=20).fuse(
        [
            RankedSource("lexical", [only_b, shared_a], 1.0),
            RankedSource("graph", [shared_b], 1.0),
        ]
    )

    assert fused[0].path == "a.py"
    assert "RRF=" in fused[0].selection_reason


def test_semantic_reranker_changes_order_without_repository_scan():
    unrelated = _candidate("docs", "README.md", "project usage documentation")
    concurrent = _candidate(
        "history", "history.py", "with self._lock: database.commit()"
    )

    result = SemanticCandidateReranker(
        KeywordEmbeddingProvider(), max_candidates=8
    ).rank("fix concurrent history race", [unrelated, concurrent])

    assert result.degraded is False
    assert result.candidates[0].path == "history.py"


def test_semantic_failure_is_graceful():
    candidate = _candidate("x", "x.py", "lock")
    result = SemanticCandidateReranker(BrokenEmbeddingProvider()).rank(
        "concurrent lock", [candidate]
    )
    assert result.degraded is True
    assert result.candidates == ()


def test_native_retriever_refuses_python_scanner_fallback():
    engine = FakeFallbackEngine()
    retriever = NativeLexicalRetriever(engine)
    assert retriever.available is False
    # No QueryPlan is needed because unavailable short-circuits first.
    assert retriever.retrieve(
        "find lock",
        None,  # type: ignore[arg-type]
        working_set_paths=set(),
        max_results=10,
        context_lines=1,
        token_budget=500,
    ) == []
    assert engine.calls == 0


def test_pipeline_uses_rust_lexical_search_instead_of_index_fts(tmp_path, monkeypatch):
    source = tmp_path / "history.py"
    source.write_text(
        "class HistoryDatabase:\n"
        "    def save(self):\n"
        "        with self._lock:\n"
        "            self._conn.commit()\n",
        encoding="utf-8",
    )
    index = RepositoryIndex(tmp_path, in_memory=True)
    try:
        index.build_or_update()
        fake_native = FakeRustEngine(
            [
                {
                    "path": "history.py",
                    "line": 3,
                    "column": 9,
                    "text": "        with self._lock:",
                    "before": ["    def save(self):"],
                    "after": ["            self._conn.commit()"],
                    "score": 1.0,
                }
            ]
        )

        def forbidden_fts(*args, **kwargs):
            raise AssertionError("FTS must not run after successful Rust retrieval")

        monkeypatch.setattr(index, "search_text", forbidden_fts)
        pipeline = HybridRetrievalPipeline(
            index,
            native_engine=fake_native,  # type: ignore[arg-type]
            rag_config=RagConfig(semantic_enabled=False),
        )
        selected = pipeline.retrieve(
            "fix concurrent HistoryDatabase lock",
            max_tokens=1000,
        )

        assert fake_native.calls == 1
        assert pipeline.last_retrieval_stats["lexical_backend"] == "rust"
        assert any(candidate.path == "history.py" for candidate in selected)
    finally:
        index.close()


def test_semantic_seed_fallback_uses_existing_index_not_file_scanner(tmp_path):
    (tmp_path / "history.py").write_text(
        "class HistoryStore:\n"
        "    def save(self):\n"
        "        with self._lock:\n"
        "            self._conn.commit()\n",
        encoding="utf-8",
    )
    (tmp_path / "docs.py").write_text(
        "class HelpText:\n"
        "    def render(self):\n"
        "        return 'usage documentation'\n",
        encoding="utf-8",
    )
    index = RepositoryIndex(tmp_path, in_memory=True)
    try:
        index.build_or_update()
        fallback_engine = FakeFallbackEngine()
        config = RagConfig(
            semantic_enabled=True,
            embedding_model="fake",
            semantic_candidate_limit=12,
        )
        pipeline = HybridRetrievalPipeline(
            index,
            native_engine=fallback_engine,  # type: ignore[arg-type]
            rag_config=config,
            embedding_provider=KeywordEmbeddingProvider(),
        )
        selected = pipeline.retrieve(
            "prevent simultaneous updates safely",
            max_tokens=1000,
        )

        assert fallback_engine.calls == 0
        assert pipeline.last_retrieval_stats["semantic_seed_candidates"] >= 1
        assert pipeline.last_retrieval_stats["semantic_candidates"] >= 1
        assert any(candidate.path == "history.py" for candidate in selected)
    finally:
        index.close()
