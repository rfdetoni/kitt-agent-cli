"""OpenAI Chat Completions protocol runtime adapter."""
from __future__ import annotations

import json
import re
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
    ProviderProtocolError,
    ProviderOutputLimitError,
    ProviderRecoverableError,
)
from kitt.llm.http_security import secure_urlopen, sanitize_remote_text
from kitt.llm.providers.base import LLMRequest, handle_http_error
from kitt_protocol import MAX_TOOL_ARGUMENT_BYTES

DEFAULT_OPENAI_URL = "https://api.openai.com"


class OpenAIChatAdapter:
    """Adapter for OpenAI Chat Completions API (/v1/chat/completions)."""

    def stream(self, request: LLMRequest) -> Iterator[str]:
        # Import lazily: the proxy subclass already uses this adapter for discovery.
        from kitt.llm.providers.kitt_reverse_proxy import (
            normalize_native_tool_messages, openai_tools_from_definitions,
        )

        base = (request.base_url or DEFAULT_OPENAI_URL).rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        elif base.endswith("/v1") or base.endswith("/v1beta"):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"

        formatted_messages = []
        if request.system_prompt:
            formatted_messages.append({"role": "system", "content": request.system_prompt})
        tools = openai_tools_from_definitions(request.tool_definitions)
        formatted_messages.extend(
            normalize_native_tool_messages(request.messages) if tools else request.messages
        )

        payload = {
            "model": request.model,
            "messages": formatted_messages,
            "stream": True,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
        if request.usage_callback is not None:
            payload["stream_options"] = {"include_usage": True}

        headers = {"Content-Type": "application/json"}
        if request.api_key:
            headers["Authorization"] = f"Bearer {request.api_key}"
        headers.update(request.extra_headers)

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
        )

        stream_complete = False
        output_limited = False
        call = {"id": "", "name": "", "arguments": ""}
        has_call = False
        call_index = None
        content_parts = []
        try:
            with secure_urlopen(req, timeout=request.timeout_seconds) as resp:
                for line in resp:
                    line_str = line.decode("utf-8").strip()
                    if not line_str.startswith("data: "):
                        continue
                    data_content = line_str[6:]
                    if data_content == "[DONE]":
                        stream_complete = True
                        break
                    try:
                        chunk = json.loads(data_content)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        raise ProviderProtocolError("OpenAI stream returned a non-object event")
                    if "error" in chunk:
                        error = chunk["error"]
                        message = error.get("message", "Provider streaming failure") if isinstance(error, dict) else error
                        if request.api_key:
                            message = str(message).replace(request.api_key, "[REDACTED]")
                        raise ProviderProtocolError("OpenAI streaming error: " + sanitize_remote_text(message))
                    usage = chunk.get("usage")
                    if isinstance(usage, dict) and request.usage_callback is not None:
                        request.usage_callback({
                            key: value for key, value in usage.items()
                            if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                            and isinstance(value, int) and not isinstance(value, bool)
                            and value >= 0
                        })
                    choices = chunk.get("choices", [])
                    if (
                        not isinstance(choices, list)
                        or not choices
                        or not isinstance(choices[0], dict)
                    ):
                        continue
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason")
                    if finish_reason is not None:
                        stream_complete = True
                        output_limited = str(finish_reason).lower() in {"length", "max_tokens"}
                    delta = choice.get("delta", {})
                    if isinstance(delta, dict):
                        content = delta.get("content", "")
                        if isinstance(content, str) and content:
                            if tools:
                                content_parts.append(content)
                            else:
                                yield content
                        for part in delta.get("tool_calls") or []:
                            if not isinstance(part, dict):
                                raise ProviderProtocolError("OpenAI returned an invalid tool call")
                            index = part.get("index", 0)
                            if type(index) is not int or not 0 <= index <= 15:
                                raise ProviderProtocolError("OpenAI returned an invalid tool-call index")
                            if call_index is not None and call_index != index:
                                raise ProviderRecoverableError(
                                    "Multiple native tool calls were rejected; no host action was executed. Return exactly one tool call.",
                                    code="native_tool_call_invalid",
                                )
                            call_index = index
                            has_call = True
                            if isinstance(part.get("id"), str):
                                if call["id"] and call["id"] != part["id"]:
                                    raise ProviderRecoverableError(
                                        "Multiple native tool-call identities were rejected; no host action was executed. Return exactly one tool call.",
                                        code="native_tool_call_invalid",
                                    )
                                call["id"] = part["id"]
                                if len(call["id"]) > 128:
                                    raise ProviderProtocolError("OpenAI tool-call identity exceeds protocol limits")
                            function = part.get("function", {})
                            if not isinstance(function, dict):
                                raise ProviderProtocolError("OpenAI returned an invalid tool function")
                            for key in ("name", "arguments"):
                                value = function.get(key)
                                if isinstance(value, str):
                                    call[key] += value
                            if len(call["name"]) > 64 or len(call["arguments"].encode("utf-8")) > MAX_TOOL_ARGUMENT_BYTES:
                                raise ProviderProtocolError("OpenAI tool call exceeds protocol limits")
            if output_limited:
                raise ProviderOutputLimitError(
                    "OpenAI output stopped because the configured token limit was reached"
                )
            if not stream_complete:
                raise ProviderProtocolError(
                    "OpenAI stream ended before a completion marker"
                )
            if has_call:
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", call["id"]):
                    raise ProviderProtocolError("OpenAI returned an invalid tool-call identity")
                if call["name"] not in {tool["function"]["name"] for tool in tools}:
                    raise ProviderProtocolError("OpenAI returned a tool outside the request allowlist")
                try:
                    arguments = json.loads(call["arguments"])
                except (ValueError, RecursionError) as exc:
                    raise ProviderProtocolError("OpenAI returned invalid tool arguments") from exc
                if not isinstance(arguments, dict):
                    raise ProviderProtocolError("OpenAI returned non-object tool arguments")
                bridge = {"id": call["id"], "name": call["name"], "arguments": arguments}
                yield "<kitt-tool>" + json.dumps(bridge, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e") + "</kitt-tool>"
            elif tools and content_parts:
                yield "".join(content_parts)
        except socket.timeout as exc:
            raise ProviderTimeoutError(f"OpenAI request timed out after {request.timeout_seconds}s") from exc
        except urllib.error.HTTPError as e:
            handle_http_error(e, url)
        except urllib.error.URLError as e:
            if isinstance(e.reason, socket.timeout):
                raise ProviderTimeoutError(f"OpenAI request timed out after {request.timeout_seconds}s") from e
            raise ProviderConnectionError(f"Could not connect to endpoint at {url}: {e}") from e

    def list_models(
        self, base_url: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 5.0
    ) -> ModelDiscoveryResult:
        base = (base_url or DEFAULT_OPENAI_URL).strip().rstrip("/")
        if base and not base.startswith(("http://", "https://")):
            base = f"http://{base}"
        url = f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
        headers = {"User-Agent": "Kitt-Agent-CLI"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(url, headers=headers)
        try:
            with secure_urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_data = data.get("data", []) if isinstance(data, dict) else data
                models: List[ModelDescriptor] = []
                if isinstance(raw_data, list):
                    for item in raw_data:
                        if isinstance(item, dict) and (item.get("id") or item.get("name")):
                            mid = item.get("id") or item.get("name")
                            models.append(
                                ModelDescriptor(
                                    provider_id="openai",
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
