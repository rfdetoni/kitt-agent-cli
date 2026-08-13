import json
import urllib.request
import urllib.error
import socket
import threading
from typing import Generator, List, Dict, Any, Optional
from kitt.domain.entities import ModelProfile

LFM_CHAT_TEMPLATE = """{{ if .System }}<|startoftext|><|im_start|>system
{{ .System }}<|im_end|>
{{ end }}<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant
"""

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

    def __init__(self, profile: ModelProfile, executor=None):
        import concurrent.futures
        self.profile = profile
        self._external_executor = executor is not None
        self._executor = executor or concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm_client_worker")

    def close(self):
        """Shut down owned thread pool executor."""
        if self._executor and not self._external_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

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

    async def achat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ):
        import asyncio
        queue = asyncio.Queue(maxsize=128)
        loop = asyncio.get_running_loop()
        sentinel = object()
        stop = threading.Event()

        def worker():
            try:
                for chunk in self.chat_stream(messages, system_prompt, response_format):
                    if stop.is_set():
                        break
                    # Blocks worker thread if queue is full (backpressure)
                    asyncio.run_coroutine_threadsafe(queue.put(("data", chunk)), loop).result(timeout=10.0)
                asyncio.run_coroutine_threadsafe(queue.put(("done", sentinel)), loop)
            except Exception as e:
                # Catch URLError, timeout, etc.
                try:
                    asyncio.run_coroutine_threadsafe(queue.put(("error", e)), loop)
                except RuntimeError:
                    pass # loop closed

        # Submit worker to shared pool
        future = self._executor.submit(worker)

        try:
            while True:
                msg_type, payload = await queue.get()
                if msg_type == "done":
                    break
                elif msg_type == "error":
                    raise payload
                elif msg_type == "data":
                    yield payload
        finally:
            stop.set()
            if not future.done():
                future.cancel() # Best effort cancel signal


    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ) -> Generator[str, None, None]:
        backend = (self.profile.backend or "").lower()

        OPENAI_COMPATIBLE = {
            "openai", "openai-codex", "lmstudio", "deepseek", "groq", "together",
            "mistral", "openrouter", "xai", "grok", "fireworks", "cohere", "azure",
            "gemini", "vllm", "localai"
        }

        if backend == "ollama":
            yield from self._chat_ollama_stream(messages, system_prompt, response_format)
        elif backend in OPENAI_COMPATIBLE:
            yield from self._chat_openai_stream(messages, system_prompt, response_format)
        elif backend == "anthropic":
            yield from self._chat_anthropic_stream(messages, system_prompt, response_format)
        elif backend == "antigravity":
            yield from self._chat_antigravity_stream(messages, system_prompt, response_format)
        else:
            raise UnsupportedProviderError(f"Backend '{self.profile.backend}' is unsupported or unknown.")

    def _chat_ollama_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ) -> Generator[str, None, None]:
        yield from self._generate_ollama_stream(messages, system_prompt, response_format)

    def _generate_ollama_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> Generator[str, None, None]:
        url = f"{self.profile.base_url.rstrip('/')}/api/generate"
        prompt = "\n\n".join(f"{message['role'].upper()}: {message['content']}" for message in messages)
        payload: Dict[str, Any] = {
            "model": self.profile.model,
            "prompt": prompt,
            "system": system_prompt or "",
            "stream": True,
            "options": {
                "temperature": self.profile.temperature,
                "num_ctx": self.profile.context_window,
                "num_predict": self.profile.max_output_tokens,
            },
        }
        if response_format == "json":
            payload["format"] = "json"
        if "lfm" in self.profile.model.lower():
            payload["template"] = LFM_CHAT_TEMPLATE
        if self.profile.keep_alive:
            payload["keep_alive"] = self.profile.keep_alive
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.profile.request_timeout_seconds) as response:
                for line in response:
                    if line:
                        content = json.loads(line.decode("utf-8")).get("response", "")
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
        base = self.profile.base_url.rstrip('/')
        if base.endswith('/chat/completions'):
            url = base
        elif base.endswith('/v1') or base.endswith('/v1beta'):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"

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
            headers["api-key"] = self.profile.api_key

        backend = (self.profile.backend or "").lower()
        if backend == "openrouter":
            headers["HTTP-Referer"] = "https://kitt-agent-cli"
            headers["X-Title"] = "KITT Agent CLI"

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
            raise LLMTimeoutError(f"LLM request timed out after {self.profile.request_timeout_seconds}s")
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                raise LLMTimeoutError(f"LLM request timed out after {self.profile.request_timeout_seconds}s")
            raise LLMConnectionError(f"Could not connect to {self.profile.backend} endpoint at {url}: {e}")

    def _chat_anthropic_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ) -> Generator[str, None, None]:
        url = f"{self.profile.base_url.rstrip('/')}/v1/messages"

        # Anthropic doesn't allow 'system' in messages list directly, it's a top-level param
        formatted_messages = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]

        payload: Dict[str, Any] = {
            "model": self.profile.model,
            "messages": formatted_messages,
            "stream": True,
            "max_tokens": self.profile.max_output_tokens,
            "temperature": self.profile.temperature
        }
        
        if system_prompt:
            payload["system"] = system_prompt

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.profile.api_key,
            "anthropic-version": "2023-06-01"
        }

        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=self.profile.request_timeout_seconds) as response:
                for line in response:
                    line_str = line.decode('utf-8').strip()
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
            raise LLMTimeoutError(f"Anthropic request timed out after {self.profile.request_timeout_seconds}s")
        except urllib.error.URLError as e:
            raise LLMConnectionError(f"Could not connect to Anthropic endpoint at {url}: {e}")

    def _chat_antigravity_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None
    ) -> Generator[str, None, None]:
        # Antigravity is generally OpenAI compatible but might have different endpoints or headers.
        # Here we map it to OpenAI behavior via its own custom url.
        url = f"{self.profile.base_url.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"
            headers["x-antigravity-auth"] = self.profile.api_key  # custom header fallback

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

        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=self.profile.request_timeout_seconds) as response:
                for line in response:
                    line_str = line.decode('utf-8').strip()
                    if line_str.startswith("data: "):
                        data_content = line_str[6:]
                        if data_content == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_content)
                            delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if delta:
                                yield delta
                        except json.JSONDecodeError:
                            pass
        except socket.timeout:
            raise LLMTimeoutError(f"Antigravity request timed out after {self.profile.request_timeout_seconds}s")
        except urllib.error.URLError as e:
            raise LLMConnectionError(f"Could not connect to Antigravity endpoint at {url}: {e}")
