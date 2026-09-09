"""Privacy-preserving wrapper for evolution LLM calls."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

from kitt.security.egress import EgressPolicy
from kitt.security.sensitive_data import SensitiveDataScanner


def _profile_local(profile) -> tuple[bool, str]:
    base_url = str(getattr(profile, "base_url", "") or "")
    host = urlparse(base_url).hostname if base_url else ""
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True, host
    backend = str(getattr(profile, "backend", "") or "").casefold()
    if not base_url and backend in {"local", "lmstudio", "ollama"}:
        return True, backend
    return False, host or backend or "provider"


class EvolutionEgressClient:
    """Apply KITT privacy modes to every offline evolution LLM request."""

    def __init__(self, inner, *, root_dir: str | Path, privacy_mode: str):
        self.inner = inner
        self.profile = inner.profile
        self.policy = EgressPolicy(mode=privacy_mode)
        self.scanner = SensitiveDataScanner()
        self.workspace_id = hashlib.sha256(
            str(Path(root_dir).resolve()).encode("utf-8")
        ).hexdigest()[:16]

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close is not None:
            close()

    def _prepare(self, text: str) -> tuple[str, tuple[str, ...], int]:
        raw = str(text or "")
        scan = self.scanner.scan_and_redact(raw)
        if self.policy.mode == "hybrid_redacted":
            return scan.clean_text, scan.categories, scan.redaction_count
        return raw, scan.categories, 0

    def chat(self, messages, system_prompt=None, **kwargs):
        prepared_messages = []
        categories: set[str] = set()
        redactions = 0
        byte_count = 0

        for message in messages:
            clean, cats, count = self._prepare(message.get("content", ""))
            categories.update(cats)
            redactions += count
            byte_count += len(clean.encode("utf-8"))
            prepared_messages.append({**message, "content": clean})

        clean_system = None
        if system_prompt is not None:
            clean_system, cats, count = self._prepare(system_prompt)
            categories.update(cats)
            redactions += count
            byte_count += len(clean_system.encode("utf-8"))

        is_local, host = _profile_local(self.profile)
        provider = str(getattr(self.profile, "backend", "") or "provider")
        model = str(getattr(self.profile, "model", "") or "model")
        allowed, _manifest, reason = self.policy.evaluate_egress(
            host=host,
            is_local=is_local,
            provider=provider,
            model=model,
            workspace_id=self.workspace_id,
            bytes_out=byte_count,
            estimated_tokens=(byte_count + 3) // 4,
            sensitive_categories=tuple(sorted(categories)),
            redaction_count=redactions,
        )
        if not allowed:
            raise PermissionError(reason)

        return self.inner.chat(
            prepared_messages,
            system_prompt=clean_system,
            **kwargs,
        )
