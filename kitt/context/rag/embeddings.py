"""Embedding providers used only for bounded candidate reranking."""
from __future__ import annotations

import json
import math
import socket
import time
import urllib.error
import urllib.request
from typing import Protocol, Sequence

from kitt.llm.http_security import read_error_body, secure_urlopen


class EmbeddingError(RuntimeError):
    """A recoverable embedding-provider failure."""


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one finite vector for each input text or raise EmbeddingError."""


class OllamaEmbeddingProvider:
    """Small stdlib-only client for Ollama's ``/api/embed`` endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 4.0,
        total_timeout_seconds: float = 8.0,
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
        self.total_timeout_seconds = max(0.5, float(total_timeout_seconds))
        self.batch_size = max(2, min(64, int(batch_size)))
        for value, name in (
            (self.timeout_seconds, "timeout_seconds"),
            (self.total_timeout_seconds, "total_timeout_seconds"),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = [str(text) for text in texts]
        if not items:
            return []
        deadline = time.monotonic() + self.total_timeout_seconds
        vectors: list[list[float]] = []
        expected_dimension: int | None = None
        for start in range(0, len(items), self.batch_size):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EmbeddingError("Ollama embedding total deadline exceeded")
            timeout = min(self.timeout_seconds, remaining)
            batch = self._embed_batch(items[start : start + self.batch_size], timeout)
            for vector in batch:
                if expected_dimension is None:
                    expected_dimension = len(vector)
                elif len(vector) != expected_dimension:
                    raise EmbeddingError("Ollama returned inconsistent embedding dimensions")
            vectors.extend(batch)
        if len(vectors) != len(items):
            raise EmbeddingError(
                f"Ollama returned {len(vectors)} embeddings for {len(items)} inputs"
            )
        return vectors

    def _embed_batch(
        self,
        texts: Sequence[str],
        timeout_seconds: float,
    ) -> list[list[float]]:
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
                timeout=max(0.05, float(timeout_seconds)),
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
                parsed = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingError("Ollama returned a non-numeric embedding vector") from exc
            if not all(math.isfinite(value) for value in parsed):
                raise EmbeddingError("Ollama returned a non-finite embedding vector")
            vectors.append(parsed)
        return vectors
