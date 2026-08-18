"""Google Gemini native protocol runtime adapter."""
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

DEFAULT_GEMINI_URL = "https://generativelanguage.googleapis.com"


class GeminiAdapter:
    """Adapter for Google Gemini native generateContent API (/v1beta/models/{model}:streamGenerateContent)."""

    def stream(self, request: LLMRequest) -> Iterator[str]:
        base = (request.base_url or DEFAULT_GEMINI_URL).rstrip("/")
        model_name = request.model
        if not model_name.startswith("models/"):
            model_path = f"models/{model_name}"
        else:
            model_path = model_name

        url = f"{base}/v1beta/{model_path}:streamGenerateContent?alt=sse"

        contents = []
        system_instruction = None
        if request.system_prompt:
            system_instruction = {"parts": [{"text": request.system_prompt}]}

        for m in request.messages:
            role = "user" if m.get("role") in ("user", "human") else "model"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        if request.response_format == "json":
            payload["generationConfig"]["responseMimeType"] = "application/json"

        headers = {"Content-Type": "application/json"}
        if request.api_key:
            headers["x-goog-api-key"] = request.api_key
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
                        try:
                            chunk = json.loads(data_content)
                            candidates = chunk.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                for p in parts:
                                    txt = p.get("text", "")
                                    if txt:
                                        yield txt
                        except json.JSONDecodeError:
                            pass
        except socket.timeout:
            raise ProviderTimeoutError(f"Gemini request timed out after {request.timeout_seconds}s")
        except urllib.error.HTTPError as e:
            handle_http_error(e, url)
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                raise ProviderTimeoutError(f"Gemini request timed out after {request.timeout_seconds}s")
            raise ProviderConnectionError(f"Could not connect to Gemini endpoint at {url}: {e}")

    def list_models(
        self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0
    ) -> ModelDiscoveryResult:
        base = (base_url or DEFAULT_GEMINI_URL).rstrip("/")
        url = f"{base}/v1beta/models"
        headers = {"User-Agent": "Kitt-Agent-CLI"}
        if api_key:
            headers["x-goog-api-key"] = api_key

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models_raw = data.get("models", [])
                models: List[ModelDescriptor] = []
                for m in models_raw:
                    if isinstance(m, dict):
                        methods = m.get("supportedGenerationMethods", [])
                        if not methods or "generateContent" in methods:
                            m_name = m.get("name", "")
                            if m_name.startswith("models/"):
                                m_name = m_name[7:]
                            if m_name:
                                models.append(
                                    ModelDescriptor(
                                        provider_id="gemini",
                                        id=m_name,
                                        name=m.get("displayName") or m_name,
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
