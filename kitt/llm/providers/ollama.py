"""Ollama protocol runtime adapter."""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Iterator, List, Optional

from kitt.llm.domain import (
    ModelDescriptor,
    ModelDiscoveryResult,
    ProviderDiscoveryStatus,
    ProviderHealth,
    ProviderTimeoutError,
    ProviderConnectionError,
)
from kitt.llm.providers.base import LLMRequest, handle_http_error

DEFAULT_OLLAMA_URL = "http://localhost:11434"

LFM_CHAT_TEMPLATE = """{{ if .System }}<|startoftext|><|im_start|>system
{{ .System }}<|im_end|>
{{ end }}<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant
"""


class OllamaAdapter:
    """Adapter for Ollama local daemon (/api/chat, /api/tags, /api/generate)."""

    def stream(self, request: LLMRequest) -> Iterator[str]:
        base_url = (request.base_url or DEFAULT_OLLAMA_URL).rstrip("/")
        url = f"{base_url}/api/chat"

        payload = {
            "model": request.model,
            "messages": request.messages,
            "stream": True,
            "options": {
                "temperature": request.temperature,
                "num_ctx": request.context_window,
                "num_predict": request.max_output_tokens,
            },
        }
        if request.response_format == "json":
            payload["format"] = "json"
        if "lfm" in request.model.lower():
            payload["template"] = LFM_CHAT_TEMPLATE
        if request.keep_alive:
            payload["keep_alive"] = request.keep_alive

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

        in_thinking = False
        try:
            with urllib.request.urlopen(req, timeout=request.timeout_seconds) as resp:
                for line in resp:
                    if not line:
                        continue
                    data = json.loads(line.decode("utf-8"))
                    msg = data.get("message", {}) if isinstance(data.get("message"), dict) else {}
                    content = msg.get("content", "") or data.get("response", "")
                    thinking = msg.get("thinking", "")
                    if thinking:
                        if not in_thinking:
                            in_thinking = True
                            yield "<think>"
                        yield thinking
                    else:
                        if in_thinking:
                            in_thinking = False
                            yield "</think>\n"
                        if content:
                            yield content
                if in_thinking:
                    yield "</think>\n"
        except socket.timeout:
            raise ProviderTimeoutError(f"Ollama request timed out after {request.timeout_seconds}s")
        except urllib.error.HTTPError as e:
            handle_http_error(e, url)
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                raise ProviderTimeoutError(f"Ollama request timed out after {request.timeout_seconds}s")
            raise ProviderConnectionError(f"Could not connect to Ollama at {url}: {e}")

    def list_models(
        self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0
    ) -> ModelDiscoveryResult:
        base = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
        url = f"{base}/api/tags"
        req = urllib.request.Request(url, headers={"User-Agent": "Kitt-Agent-CLI"})

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models_raw = data.get("models", [])
                models: List[ModelDescriptor] = []
                for m in models_raw:
                    if isinstance(m, dict) and m.get("name"):
                        m_name = m["name"]
                        models.append(
                            ModelDescriptor(
                                provider_id="ollama",
                                id=m_name,
                                name=m_name,
                                raw_metadata=m,
                            )
                        )
                if not models:
                    return ModelDiscoveryResult(status=ProviderDiscoveryStatus.NO_MODELS, models=[])
                return ModelDiscoveryResult(status=ProviderDiscoveryStatus.SUCCESS, models=models)
        except socket.timeout:
            return ModelDiscoveryResult(status=ProviderDiscoveryStatus.TIMEOUT, message="Request timed out")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return ModelDiscoveryResult(status=ProviderDiscoveryStatus.AUTH_INVALID, message=str(e))
            return ModelDiscoveryResult(status=ProviderDiscoveryStatus.UNREACHABLE, message=str(e))
        except Exception as e:
            return ModelDiscoveryResult(status=ProviderDiscoveryStatus.UNREACHABLE, message=str(e))

    def health(
        self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0
    ) -> ProviderHealth:
        start = time.monotonic()
        res = self.list_models(base_url, api_key, timeout)
        lat = (time.monotonic() - start) * 1000
        if res.status == ProviderDiscoveryStatus.SUCCESS:
            return ProviderHealth(
                status="healthy",
                latency_ms=lat,
                authenticated=True,
                models_available=len(res.models),
            )
        return ProviderHealth(
            status="unreachable",
            latency_ms=lat,
            authenticated=False,
            models_available=0,
            error_code=res.status.value,
        )
