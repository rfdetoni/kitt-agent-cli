import json
import urllib.request
import urllib.error
import socket
from typing import Generator, List, Dict, Any, Optional
from kitt.domain.entities import ModelProfile

class LLMError(Exception):
    """Base exception for LLM client errors."""

class LLMConnectionError(LLMError):
    """Raised when HTTP connection fails."""

class LLMTimeoutError(LLMError):
    """Raised when request times out."""

class LLMProtocolError(LLMError):
    """Raised when HTTP status or JSON protocol format is invalid."""

class LLMEmptyResponseError(LLMError):
    """Raised when response is empty."""

class UnsupportedProviderError(LLMError):
    """Raised when backend provider is unknown or unsupported."""


class LLMClient:
    """Native Python HTTP client for Ollama and OpenAI-compatible APIs."""

    def __init__(self, profile: ModelProfile):
        self.profile = profile

    def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ) -> str:
        full_text = ""
        for chunk in self.chat_stream(messages, system_prompt=system_prompt, response_format=response_format):
            full_text += chunk
        if not full_text.strip():
            raise LLMEmptyResponseError("LLM returned empty response.")
        return full_text

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ) -> Generator[str, None, None]:
        backend = (self.profile.backend or "").lower()

        if backend == "ollama":
            yield from self._chat_ollama_stream(messages, system_prompt, response_format)
        elif backend == "openai":
            yield from self._chat_openai_stream(messages, system_prompt, response_format)
        else:
            raise UnsupportedProviderError(f"Backend '{self.profile.backend}' is unsupported or unknown.")

    def _chat_ollama_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ) -> Generator[str, None, None]:
        url = f"{self.profile.base_url.rstrip('/')}/api/chat"

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        payload: Dict[str, Any] = {
            "model": self.profile.model,
            "messages": formatted_messages,
            "stream": True,
            "options": {
                "temperature": self.profile.temperature,
                "num_ctx": self.profile.context_window
            }
        }

        if response_format == "json" or self.profile.supports_json:
            payload["format"] = "json"

        if self.profile.keep_alive:
            payload["keep_alive"] = self.profile.keep_alive

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=self.profile.request_timeout_seconds) as response:
                for line in response:
                    if line:
                        chunk = json.loads(line.decode('utf-8'))
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
        except socket.timeout:
            raise LLMTimeoutError(f"Ollama request timed out after {self.profile.request_timeout_seconds}s")
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                raise LLMTimeoutError(f"Ollama request timed out after {self.profile.request_timeout_seconds}s")
            raise LLMConnectionError(f"Could not connect to Ollama at {url}: {e}")

    def _chat_openai_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ) -> Generator[str, None, None]:
        url = f"{self.profile.base_url.rstrip('/')}/v1/chat/completions"

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        payload: Dict[str, Any] = {
            "model": self.profile.model,
            "messages": formatted_messages,
            "stream": True,
            "temperature": self.profile.temperature,
            "max_tokens": self.profile.max_output_tokens
        }

        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers
        )

        try:
            with urllib.request.urlopen(req, timeout=self.profile.request_timeout_seconds) as response:
                for line in response:
                    line_str = line.decode('utf-8').strip()
                    if line_str.startswith("data: "):
                        data_content = line_str[6:]
                        if data_content == "[DONE]":
                            break
                        chunk = json.loads(data_content)
                        delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            yield delta
        except socket.timeout:
            raise LLMTimeoutError(f"OpenAI request timed out after {self.profile.request_timeout_seconds}s")
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                raise LLMTimeoutError(f"OpenAI request timed out after {self.profile.request_timeout_seconds}s")
            raise LLMConnectionError(f"Could not connect to OpenAI endpoint at {url}: {e}")
