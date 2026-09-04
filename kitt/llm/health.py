"""Health check diagnostics for LLM endpoints."""
from __future__ import annotations

import urllib.request
import urllib.error
import json
from typing import Tuple
from kitt.llm.http_security import secure_urlopen


class ProviderHealthChecker:
    """Checks reachability and availability of LLM provider endpoints."""

    @staticmethod
    def check_ollama(base_url: str = "http://localhost:11434", timeout: float = 1.5) -> Tuple[bool, str]:
        url = f"{base_url.rstrip('/')}/api/tags"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "KITT-Agent-CLI"})
            with secure_urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    count = len(data.get("models", []))
                    return True, f"Ollama online ({count} models available)"
                return False, f"Ollama HTTP {resp.status}"
        except Exception as exc:
            return False, f"Ollama unreachable: {exc}"

    @staticmethod
    def check_openai_compatible(base_url: str, api_key: str = "", timeout: float = 2.0) -> Tuple[bool, str]:
        url = f"{base_url.rstrip('/')}/v1/models"
        headers = {"User-Agent": "KITT-Agent-CLI"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with secure_urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 201):
                    return True, "OpenAI-compatible endpoint reachable"
                return False, f"HTTP {resp.status}"
        except urllib.error.HTTPError as err:
            if err.code in (401, 403):
                return True, f"Endpoint reachable (auth required: HTTP {err.code})"
            return False, f"HTTP {err.code}: {err.reason}"
        except Exception as exc:
            return False, f"Endpoint unreachable: {exc}"

    @staticmethod
    def check_kitt_reverse_proxy(base_url: str = "http://127.0.0.1:3000", timeout: float = 1.5) -> Tuple[bool, str]:
        url = f"{base_url.rstrip('/')}/v1/kitt/status"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "KITT-Agent-CLI"})
            with secure_urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    model = data.get("model", "unknown")
                    transport = data.get("transport", "unknown")
                    return True, f"KITT Reverse Proxy online (model: {model}, transport: {transport})"
                return False, f"HTTP {resp.status}"
        except Exception as exc:
            # Fallback to /healthz or /v1/models
            try:
                fallback_url = f"{base_url.rstrip('/')}/healthz"
                req = urllib.request.Request(fallback_url, headers={"User-Agent": "KITT-Agent-CLI"})
                with secure_urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        return True, "KITT Reverse Proxy online"
            except Exception:
                pass
            return False, f"KITT Reverse Proxy unreachable: {exc}"

