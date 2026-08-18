"""Anthropic Messages protocol runtime adapter."""
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

DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"


class AnthropicAdapter:
    """Adapter for Anthropic Messages API (/v1/messages)."""

    def stream(self, request: LLMRequest) -> Iterator[str]:
        base = (request.base_url or DEFAULT_ANTHROPIC_URL).rstrip("/")
        url = f"{base}/v1/messages" if not base.endswith("/v1/messages") else base

        formatted_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in request.messages
            if m.get("role") != "system"
        ]

        payload = {
            "model": request.model,
            "messages": formatted_messages,
            "stream": True,
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt

        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if request.api_key:
            headers["x-api-key"] = request.api_key
        headers.update(request.extra_headers)

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=request.timeout_seconds) as resp:
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if line_str.startswith("data: "):
                        data_content = line_str[6:]
                        if data_content == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_content)
                            if chunk.get("type") == "content_block_delta":
                                delta = chunk.get("delta", {}).get("text", "")
                                if delta:
                                    yield delta
                        except json.JSONDecodeError:
                            pass
        except socket.timeout:
            raise ProviderTimeoutError(f"Anthropic request timed out after {request.timeout_seconds}s")
        except urllib.error.HTTPError as e:
            handle_http_error(e, url)
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                raise ProviderTimeoutError(f"Anthropic request timed out after {request.timeout_seconds}s")
            raise ProviderConnectionError(f"Could not connect to endpoint at {url}: {e}")

    def list_models(
        self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0
    ) -> ModelDiscoveryResult:
        base = (base_url or DEFAULT_ANTHROPIC_URL).rstrip("/")
        url = f"{base}/v1/models"
        headers = {
            "User-Agent": "Kitt-Agent-CLI",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            headers["x-api-key"] = api_key

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_data = data.get("data", []) if isinstance(data, dict) else data
                models: List[ModelDescriptor] = []
                if isinstance(raw_data, list):
                    for item in raw_data:
                        if isinstance(item, dict) and (item.get("id") or item.get("name")):
                            mid = item.get("id") or item.get("name")
                            models.append(
                                ModelDescriptor(
                                    provider_id="anthropic",
                                    id=mid,
                                    name=mid,
                                    raw_metadata=item,
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
            elif e.code == 429:
                return ModelDiscoveryResult(status=ProviderDiscoveryStatus.RATE_LIMITED, message=str(e))
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
            status="unhealthy" if res.status == ProviderDiscoveryStatus.AUTH_INVALID else "unreachable",
            latency_ms=lat,
            authenticated=res.status != ProviderDiscoveryStatus.AUTH_INVALID,
            models_available=0,
            error_code=res.status.value,
        )
