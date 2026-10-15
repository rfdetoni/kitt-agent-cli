"""Lazy, opt-in Ollama embeddings for the durable kitt-memory vector index."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import ipaddress
import logging
import math
import os
import threading
from urllib.parse import urlsplit

from kitt.context.rag.embeddings import OllamaEmbeddingProvider

_LOG = logging.getLogger(__name__)
_SLOTS = threading.BoundedSemaphore(16)
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kitt-vector")


class LocalVectorIndexer:
    def __init__(self, model: str, url: str):
        if not model or len(model) > 128 or not model.isascii() or any(ord(c) < 32 for c in model):
            raise ValueError("invalid embedding model")
        parts = urlsplit(url)
        if parts.scheme != "http" or parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("embedding URL must be an HTTP loopback origin")
        host = parts.hostname or ""
        if host.lower() != "localhost":
            try:
                if not ipaddress.ip_address(host).is_loopback:
                    raise ValueError("remote embedding host is forbidden")
            except ValueError as exc:
                raise ValueError("remote embedding host is forbidden") from exc
        if parts.port is None or not 1 <= parts.port <= 65535:
            raise ValueError("embedding URL requires a valid port")
        self.model = model
        self._backfill_lock = threading.Lock()
        self.provider = OllamaEmbeddingProvider(
            model, url, timeout_seconds=0.75, total_timeout_seconds=0.9, batch_size=4,
        )

    @classmethod
    def from_environment(cls):
        model = os.getenv("KITT_MEMORY_EMBED_MODEL", "").strip()
        if not model:
            return None
        try:
            return cls(model, os.getenv("KITT_MEMORY_EMBED_URL", "http://127.0.0.1:11434"))
        except ValueError:
            _LOG.warning("KITT vector memory disabled: invalid local embedding configuration")
            return None

    @lru_cache(maxsize=128)
    def _cached(self, content: str) -> tuple[float, ...]:
        output = self.provider.embed([content])
        if len(output) != 1 or not 2 <= len(output[0]) <= 2048:
            raise ValueError("invalid embedding dimensions")
        values = tuple(float(item) for item in output[0])
        if not all(math.isfinite(item) for item in values):
            raise ValueError("non-finite embedding")
        return values

    def query(self, content: str):
        if not content.strip():
            return None
        try:
            return list(self._cached(content[:8192]))
        except Exception as exc:
            _LOG.debug("KITT vector embedding unavailable (%s); using FTS5", type(exc).__name__)
            return None

    def schedule(self, client, memory_id: str, content: str, expected_content_hash: str) -> None:
        if (not content.strip() or len(expected_content_hash) != 64
                or not all(c in '0123456789abcdef' for c in expected_content_hash.lower())
                or not _SLOTS.acquire(blocking=False)):
            return

        def work():
            try:
                vector = self.query(content)
                if vector is not None:
                    client.manage("vector.upsert", {
                        "memory_id": memory_id, "model": self.model, "embedding": vector,
                        "expected_content_hash": expected_content_hash,
                    })
            except Exception as exc:
                _LOG.debug("KITT vector upsert failed (%s)", type(exc).__name__)
            finally:
                _SLOTS.release()

        _POOL.submit(work)

    def schedule_backfill(self, client, workspace_id: str, namespace: str) -> None:
        """Retry missing embeddings using durable Memory records as the work source."""
        if not self._backfill_lock.acquire(blocking=False):
            return

        def work() -> None:
            try:
                result = client.manage("vector.pending", {
                    "workspace_id": workspace_id,
                    "namespace": namespace,
                    "model": self.model,
                    "limit": 4,
                })
                for record in result.get("pending", [])[:4]:
                    if not isinstance(record, dict):
                        continue
                    memory_id, content, expected_hash = (
                        record.get("id"), record.get("content"), record.get("content_hash")
                    )
                    if not all(isinstance(item, str) for item in (memory_id, content, expected_hash)):
                        continue
                    if not content.strip() or len(expected_hash) != 64:
                        continue
                    vector = self.query(content)
                    if vector is not None:
                        client.manage("vector.upsert", {
                            "memory_id": memory_id, "model": self.model,
                            "embedding": vector, "expected_content_hash": expected_hash,
                        })
            except Exception as exc:
                _LOG.debug("KITT vector backfill deferred (%s)", type(exc).__name__)
            finally:
                self._backfill_lock.release()

        try:
            _POOL.submit(work)
        except RuntimeError:
            self._backfill_lock.release()
