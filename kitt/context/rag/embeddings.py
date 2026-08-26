"""Embedding providers used only for candidate reranking.

No vector database is introduced. The repository continues to be searched by
KITT's native/indexed retrieval; embeddings are calculated for a bounded set of
candidate snippets and the user query.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Protocol, Sequence

from kitt.llm.http_security import read_error_body, secure_urlopen


class EmbeddingError(RuntimeError):
    """A recoverable embedding-provider failure."""


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector for each input text or raise EmbeddingError."""


class OllamaEmbeddingProvider:
    """Small stdlib-only client for Ollama's `/api/embed` endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 4.0,
        batch_size: int = 24,
    ) -> None:
        if not model.strip():
            raise ValueError("An Ollama embedding model is required")
        base = base_url.strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            base = f"http://{base}"
        self.model = model.strip()
        self.base_url = base
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.batch_size = max(2, min(64, int(batch_size)))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = [str(text) for text in texts]
        if not items:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(items), self.batch_size):
            vectors.extend(self._embed_batch(items[start : start + self.batch_size]))
        if len(vectors) != len(items):
            raise EmbeddingError(
                f"Ollama returned {len(vectors)} embeddings for {len(items)} inputs"
            )
        return vectors

    def _embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        url = f"{self.base_url}/api/embed"
        payload = json.dumps({"model": self.model, "input": list(texts)}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "Kitt-Agent-CLI"},
            method="POST",
        )
        try:
            with secure_urlopen(
                request,
                timeout=self.timeout_seconds,
                max_body_bytes=32 * 1024 * 1024,
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except socket.timeout as exc:
            raise EmbeddingError(f"Ollama embedding request timed out: {exc}") from exc
        except urllib.error.HTTPError as exc:
            detail = read_error_body(exc)
            suffix = f": {detail}" if detail else ""
            raise EmbeddingError(f"Ollama embedding HTTP {exc.code}{suffix}") from exc
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise EmbeddingError(f"Ollama embedding request failed: {exc}") from exc

        raw = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            raise EmbeddingError("Ollama response did not contain an embeddings array")

        vectors: list[list[float]] = []
        for vector in raw:
            if not isinstance(vector, list) or not vector:
                raise EmbeddingError("Ollama returned an invalid embedding vector")
            try:
                vectors.append([float(value) for value in vector])
            except (TypeError, ValueError) as exc:
                raise EmbeddingError("Ollama returned a non-numeric embedding vector") from exc
        return vectors
