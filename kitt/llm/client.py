import json
import urllib.request
import urllib.error
from typing import Generator, List, Dict, Any, Optional
from kitt.domain.entities import ModelProfile

class LLMClient:
    """Native Python HTTP client for Ollama, OpenAI, and Anthropic APIs."""

    def __init__(self, profile: ModelProfile):
        self.profile = profile

    def chat_stream(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        if self.profile.backend == "ollama":
            yield from self._chat_ollama_stream(messages, system_prompt)
        elif self.profile.backend == "openai":
            yield from self._chat_openai_stream(messages, system_prompt)
        else:
            yield from self._chat_ollama_stream(messages, system_prompt)

    def _chat_ollama_stream(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        url = f"{self.profile.base_url.rstrip('/')}/api/chat"

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        payload = {
            "model": self.profile.model,
            "messages": formatted_messages,
            "stream": True
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                for line in response:
                    if line:
                        chunk = json.loads(line.decode('utf-8'))
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content
        except urllib.error.URLError as e:
            yield f"\n[LLM Error: Could not connect to Ollama at {url}: {e}]\n"

    def _chat_openai_stream(self, messages: List[Dict[str, str]], system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        url = f"{self.profile.base_url.rstrip('/')}/v1/chat/completions"

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        formatted_messages.extend(messages)

        payload = {
            "model": self.profile.model,
            "messages": formatted_messages,
            "stream": True
        }

        headers = {"Content-Type": "application/json"}
        if self.profile.api_key:
            headers["Authorization"] = f"Bearer {self.profile.api_key}"

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers=headers
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as response:
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
        except urllib.error.URLError as e:
            yield f"\n[LLM Error: Could not connect to OpenAI endpoint at {url}: {e}]\n"
