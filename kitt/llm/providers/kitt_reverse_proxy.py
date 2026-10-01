"""Native compatibility adapter for kitt-reverse-proxy."""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional, Tuple

from kitt.llm.domain import (
    ProviderConnectionError,
    ProviderProtocolError,
    ProviderRecoverableError,
    ProviderTimeoutError,
)
from kitt.llm.http_security import read_error_body, secure_urlopen
from kitt.llm.providers.base import LLMRequest, handle_http_error
from kitt.llm.providers.openai_chat import OpenAIChatAdapter

_TOOL_LIST_MARKER_RE = re.compile(r"Available host tools?:\s*", re.IGNORECASE)
_BRIDGE_RE = re.compile(r"<kitt-tool>\s*(\{[\s\S]*\})\s*</kitt-tool>", re.IGNORECASE)
_SAFE_CALL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TOOL_FEEDBACK_PREFIXES = (
    "apply_patch was rejected before approval:",
    "the host tool call is invalid (",
    "the python_compute call is invalid (",
)
_REQUIRED_ARGS = {
    "read_file": ("path",),
    "kitt_runtime": ("operation",),
    "list_files": ("path",),
    "repository_map": ("mode",),
    "artifact_store": ("content",),
    "search": ("pattern",),
    "write_file": ("path", "content"),
    "apply_patch": ("patch",),
    "run_command": ("argv",),
    "python_compute": ("code",),
    "artifact_read": ("artifact_id",),
    "goal_create": ("objective",),
    "goal_add_gate": ("command",),
    "child_spawn": ("task",),
    "harness_remember": ("text",),
}
_TOOL_RETRY_PROMPT = (
    "[KITT TOOL RETRY] The request is not complete yet. "
    "Emit exactly one valid JSON <tool_call> envelope now, with quotes, backslashes, "
    "and line breaks correctly escaped; execute the requested mutation. "
    "Do not respond with code, a read result, or an explanation."
)
def _property_schema(name: str, hint: Any) -> Dict[str, Any]:
    if isinstance(hint, dict):
        if isinstance(hint.get("type"), str):
            return dict(hint)
        return {"type": "object", "additionalProperties": True}
    text = str(hint or "").lower()
    if "bool" in text:
        return {"type": "boolean"}
    if "int" in text or "number" in text or "token_budget" in name:
        return {"type": "integer"}
    if "json object" in text or name in {"arguments", "inputs", "scope"}:
        return {"type": "object", "additionalProperties": True}
    return {"type": "string"}


def _normalize_parameters(name: str, raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict) and raw.get("type") == "object":
        return dict(raw)
    source = raw if isinstance(raw, dict) else {}
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for key, hint in source.items():
        if not isinstance(key, str) or not key:
            continue
        properties[key] = _property_schema(key, hint)
        if key in _REQUIRED_ARGS.get(name, ()):
            required.append(key)
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    if required:
        schema["required"] = required
    if (
        name == "kitt_runtime"
        and "operation" in properties
        and "enum" not in properties["operation"]
    ):
        from kitt.runtime.safe_runtime import OPERATION_SPECS
        properties["operation"]["enum"] = list(OPERATION_SPECS)
    return schema


def openai_tools_from_definitions(
    definitions: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Convert structural KITT tool definitions into OpenAI function tools."""
    result: List[Dict[str, Any]] = []
    seen = set()
    for entry in definitions or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", name)
            or name in seen
        ):
            continue
        seen.add(name)
        function: Dict[str, Any] = {
            "name": name,
            "parameters": _normalize_parameters(name, entry.get("args")),
        }
        description = entry.get("description")
        if isinstance(description, str) and description.strip():
            function["description"] = description.strip()[:4096]
        result.append({"type": "function", "function": function})
    return result


def _reject_legacy_tool_contract(system_prompt: Optional[str]) -> None:
    if system_prompt and "Tool Contract:" in system_prompt:
        raise ProviderProtocolError(
            "Legacy textual Tool Contract is not supported; send tool_definitions structurally."
        )


def _decode_bridge_call(content: Any) -> Optional[Tuple[str, str, Dict[str, Any], str]]:
    if not isinstance(content, str):
        return None
    match = _BRIDGE_RE.search(content.strip())
    if not match:
        return None
    try:
        value = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    call_id = value.get("id")
    name = value.get("name")
    arguments = value.get("arguments")
    if (
        not isinstance(call_id, str)
        or not _SAFE_CALL_ID.fullmatch(call_id)
        or not isinstance(name, str)
        or not isinstance(arguments, dict)
    ):
        return None
    summary = value.get("reasoning_summary")
    reasoning_summary = (
        " ".join(summary.split())[:400]
        if isinstance(summary, str)
        else ""
    )
    return call_id, name, arguments, reasoning_summary


def _is_tool_feedback_message(content: Any) -> bool:
    """Identify KITT-generated execution/preflight feedback for a prior tool call."""
    if content is None:
        return False
    text = str(content).strip().lower()
    if "result from the host" in text[:512]:
        return True
    return any(text.startswith(prefix) for prefix in _TOOL_FEEDBACK_PREFIXES)


def _proxy_error_details(body: str) -> Tuple[str, str, bool, str]:
    """Extract bounded recovery metadata from a structured proxy error."""
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return "", "", False, ""
    if not isinstance(value, dict):
        return "", "", False, ""
    error = value.get("error")
    if not isinstance(error, dict):
        return "", "", False, ""
    message = error.get("message")
    code = error.get("code")
    action = error.get("recovery_action")
    recoverable = error.get("recoverable") is True
    return (
        " ".join(message.split())[:500] if isinstance(message, str) else "",
        str(code)[:128] if isinstance(code, str) else "",
        recoverable,
        str(action)[:32] if isinstance(action, str) else "",
    )


def _proxy_error_message(body: str) -> str:
    return _proxy_error_details(body)[0]


def normalize_native_tool_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Restore native assistant.tool_calls -> tool(tool_call_id) from KITT's text loop."""
    normalized: List[Dict[str, Any]] = []
    pending_call_id: Optional[str] = None
    pending_name: Optional[str] = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role == "assistant":
            bridged = _decode_bridge_call(content)
            if bridged:
                call_id, name, arguments, reasoning_summary = bridged
                normalized.append({
                    "role": "assistant",
                    "content": reasoning_summary or None,
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(
                                arguments, ensure_ascii=False, separators=(",", ":")
                            ),
                        },
                    }],
                })
                pending_call_id = call_id
                pending_name = name
                continue
        if role == "user" and pending_call_id:
            result_text = "" if content is None else str(content)
            if _is_tool_feedback_message(result_text):
                tool_msg: Dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": pending_call_id,
                    "content": result_text,
                }
                if pending_name:
                    tool_msg["name"] = pending_name
                normalized.append(tool_msg)
                pending_call_id = None
                pending_name = None
                continue
            pending_call_id = None
            pending_name = None
        clean: Dict[str, Any] = {"role": role, "content": content}
        for key in ("name", "tool_call_id", "tool_calls"):
            if key in message:
                clean[key] = message[key]
        normalized.append(clean)
    return normalized


class KittReverseProxyAdapter(OpenAIChatAdapter):
    """OpenAI Chat Completions adapter with native KITT tool round trips."""

    def stream(self, request: LLMRequest) -> Iterator[str]:
        base = (request.base_url or "http://127.0.0.1:3000").rstrip("/")
        if base.endswith("/chat/completions"):
            url = base
        elif base.endswith("/v1"):
            url = f"{base}/chat/completions"
        else:
            url = f"{base}/v1/chat/completions"

        _reject_legacy_tool_contract(request.system_prompt)
        tools = openai_tools_from_definitions(request.tool_definitions)

        messages: List[Dict[str, Any]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(normalize_native_tool_messages([dict(m) for m in request.messages]))

        payload: Dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if request.context_envelope:
            payload["kitt_context"] = request.context_envelope
        if request.request_metadata:
            payload["kitt_meta"] = request.request_metadata
        if tools:
            payload.update({
                "tools": tools,
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            })
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "Kitt-Agent-CLI/reverse-proxy",
        }
        if request.api_key:
            headers["Authorization"] = f"Bearer {request.api_key}"
        headers.update(request.extra_headers)

        http_request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        content_parts: List[str] = []
        calls: Dict[int, Dict[str, str]] = {}
        stream_complete = False
        received_bytes = 0

        try:
            with secure_urlopen(http_request, timeout=request.timeout_seconds) as response:
                for raw_line in iter(lambda: response.readline(4 * 1024 * 1024 + 1), b""):
                    received_bytes += len(raw_line)
                    if received_bytes > 4 * 1024 * 1024:
                        raise ProviderProtocolError("KITT reverse proxy stream exceeds 4 MiB")
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_content = line[5:].lstrip()
                    if data_content == "[DONE]":
                        stream_complete = True
                        break
                    try:
                        chunk = json.loads(data_content)
                    except (json.JSONDecodeError, RecursionError) as exc:
                        raise ProviderProtocolError("KITT reverse proxy returned invalid SSE JSON") from exc
                    if not isinstance(chunk, dict):
                        raise ProviderProtocolError("KITT reverse proxy returned a non-object SSE event")
                    if "error" in chunk:
                        raise ProviderProtocolError("KITT reverse proxy returned an SSE error")
                    usage = chunk.get("usage")
                    if isinstance(usage, dict) and request.usage_callback is not None:
                        safe_usage = {
                            key: value
                            for key, value in usage.items()
                            if key in {
                                "prompt_tokens",
                                "completion_tokens",
                                "total_tokens",
                                "kitt_estimated",
                            }
                            and isinstance(value, (int, float, bool))
                        }
                        if safe_usage:
                            try:
                                request.usage_callback(safe_usage)
                            except Exception:
                                pass
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        continue
                    delta = choices[0].get("delta")
                    if not isinstance(delta, dict):
                        continue
                    text = delta.get("content")
                    if isinstance(text, str) and text:
                        content_parts.append(text)
                    tool_parts = delta.get("tool_calls")
                    if not isinstance(tool_parts, list):
                        continue
                    for part in tool_parts:
                        if not isinstance(part, dict):
                            continue
                        index = part.get("index", 0)
                        if not isinstance(index, int) or index < 0 or index > 15:
                            continue
                        state = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        call_id = part.get("id")
                        if isinstance(call_id, str) and call_id:
                            state["id"] = call_id
                        function = part.get("function")
                        if isinstance(function, dict):
                            name = function.get("name")
                            arguments = function.get("arguments")
                            if isinstance(name, str) and name:
                                state["name"] += name
                            if isinstance(arguments, str) and arguments:
                                state["arguments"] += arguments
                            if len(state["name"]) > 64 or len(state["arguments"].encode("utf-8")) > 64 * 1024:
                                raise ProviderProtocolError("KITT reverse proxy tool call exceeds protocol limits")
        except socket.timeout as exc:
            raise ProviderTimeoutError(
                f"KITT reverse proxy timed out after {request.timeout_seconds}s"
            ) from exc
        except urllib.error.HTTPError as exc:
            body = read_error_body(exc)
            if "X-Kitt-Reasoning-Effort" in request.extra_headers and (
                "reasoning_level_unavailable" in body
                or "reasoning_not_supported" in body
                or "Reasoning" in body
            ):
                retry_headers = {
                    k: v
                    for k, v in request.extra_headers.items()
                    if k != "X-Kitt-Reasoning-Effort"
                }
                retry_request = LLMRequest(
                    model=request.model,
                    messages=request.messages,
                    system_prompt=request.system_prompt,
                    response_format=request.response_format,
                    tool_definitions=list(request.tool_definitions),
                    context_envelope=(
                        dict(request.context_envelope)
                        if request.context_envelope
                        else None
                    ),
                    request_metadata=(
                        dict(request.request_metadata)
                        if request.request_metadata
                        else None
                    ),
                    usage_callback=request.usage_callback,
                    temperature=request.temperature,
                    context_window=request.context_window,
                    max_output_tokens=request.max_output_tokens,
                    keep_alive=request.keep_alive,
                    api_key=request.api_key,
                    base_url=request.base_url,
                    timeout_seconds=request.timeout_seconds,
                    extra_headers=retry_headers,
                )
                yield from self.stream(retry_request)
                return

            detail, proxy_code, recoverable, recovery_action = _proxy_error_details(body)
            if recoverable and proxy_code:
                raise ProviderRecoverableError(
                    f"KITT reverse proxy returned a recoverable model response error: "
                    f"{proxy_code}{f': {detail}' if detail else ''}",
                    code=proxy_code,
                    recovery_action=recovery_action or "continue",
                ) from exc

            semantic_codes = (
                "tool_required_but_not_called",
                "tool_parse_failed",
                "invalid_tool_call",
                "invalid_tool_request",
                "invalid_session_id",
                "session_limit_exceeded",
            )
            matched = next((code for code in semantic_codes if code in body), None)
            if matched:
                if matched in {"tool_required_but_not_called", "tool_parse_failed", "invalid_tool_call"} and not any(
                    message.get("role") == "user"
                    and "KITT TOOL RETRY" in str(message.get("content", ""))
                    for message in request.messages
                ):
                    retry_messages = [dict(message) for message in request.messages]
                    retry_messages.append({
                        "role": "user",
                        "content": _TOOL_RETRY_PROMPT,
                    })
                    retry_request = LLMRequest(
                        model=request.model,
                        messages=retry_messages,
                        system_prompt=request.system_prompt,
                        response_format=request.response_format,
                        tool_definitions=list(request.tool_definitions),
                        context_envelope=(
                            dict(request.context_envelope)
                            if request.context_envelope
                            else None
                        ),
                        request_metadata=(
                            dict(request.request_metadata)
                            if request.request_metadata
                            else None
                        ),
                        usage_callback=request.usage_callback,
                        temperature=request.temperature,
                        context_window=request.context_window,
                        max_output_tokens=request.max_output_tokens,
                        keep_alive=request.keep_alive,
                        api_key=request.api_key,
                        base_url=request.base_url,
                        timeout_seconds=request.timeout_seconds,
                        extra_headers=request.extra_headers,
                    )
                    yield from self.stream(retry_request)
                    return
                suffix = f": {detail}" if detail else ""
                raise ProviderProtocolError(
                    f"KITT reverse proxy rejected the request: {matched}{suffix}"
                ) from exc
            handle_http_error(exc, url, body=body)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise ProviderTimeoutError(
                    f"KITT reverse proxy timed out after {request.timeout_seconds}s"
                ) from exc
            raise ProviderConnectionError(
                f"Could not connect to KITT reverse proxy at {url}: {exc}"
            ) from exc

        if not stream_complete:
            raise ProviderProtocolError("KITT reverse proxy stream ended before [DONE]")

        if calls:
            if len(calls) != 1:
                raise ProviderProtocolError(
                    "KITT reverse proxy returned multiple tool calls while "
                    "parallel_tool_calls=false"
                )
            state = calls[min(calls)]
            call_id, name = state["id"], state["name"]
            if not _SAFE_CALL_ID.fullmatch(call_id) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,64}", name
            ):
                raise ProviderProtocolError(
                    "KITT reverse proxy returned an invalid tool-call identity"
                )
            if name not in {tool["function"]["name"] for tool in tools}:
                raise ProviderProtocolError("KITT reverse proxy returned a tool outside the request allowlist")
            try:
                arguments = json.loads(state["arguments"] or "{}")
            except json.JSONDecodeError as exc:
                raise ProviderProtocolError(
                    f"KITT reverse proxy returned invalid tool arguments: {exc}"
                ) from exc
            if not isinstance(arguments, dict):
                raise ProviderProtocolError(
                    "KITT reverse proxy returned non-object tool arguments"
                )
            reasoning_summary = " ".join("".join(content_parts).split())[:400]
            bridge = {"id": call_id, "name": name, "arguments": arguments}
            if reasoning_summary:
                bridge["reasoning_summary"] = reasoning_summary
            yield (
                "<kitt-tool>"
                + json.dumps(bridge, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
                + "</kitt-tool>"
            )
            return

        if content_parts:
            yield "".join(content_parts)
