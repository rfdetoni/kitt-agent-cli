"""Native compatibility adapter for kitt-reverse-proxy."""
from __future__ import annotations

import ast
import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional, Tuple

from kitt.llm.domain import ProviderConnectionError, ProviderProtocolError, ProviderTimeoutError
from kitt.llm.http_security import read_error_body, secure_urlopen
from kitt.llm.providers.base import LLMRequest, handle_http_error
from kitt.llm.providers.openai_chat import OpenAIChatAdapter

_TOOL_LIST_RE = re.compile(r"Available host tools?:\s*(\[[^\n]*\])", re.IGNORECASE)
_BRIDGE_RE = re.compile(r"<kitt-tool>\s*(\{[\s\S]*\})\s*</kitt-tool>", re.IGNORECASE)
_SAFE_CALL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_REQUIRED_ARGS = {
    "read_file": ("path",),
    "kitt_runtime": ("operation",),
    "list_files": ("path",),
    "repository_map": ("mode",),
    "artifact_store": ("content",),
    "search": ("pattern",),
    "write_file": ("path", "content"),
    "apply_patch": ("patch",),
    "run_command": ("command",),
    "python_compute": ("code",),
    "artifact_read": ("artifact_id",),
    "goal_create": ("objective",),
    "goal_add_gate": ("command",),
    "child_spawn": ("task",),
    "harness_remember": ("text",),
}


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
    if name == "kitt_runtime" and "operation" in properties:
        from kitt.runtime.safe_runtime import OPERATION_SPECS
        properties["operation"]["enum"] = list(OPERATION_SPECS)
    return schema


def extract_openai_tools(system_prompt: Optional[str]) -> List[Dict[str, Any]]:
    """Convert TurnProcessor's existing host-tool descriptor into OpenAI tools."""
    if not system_prompt:
        return []
    match = _TOOL_LIST_RE.search(system_prompt)
    if not match:
        return []
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    result: List[Dict[str, Any]] = []
    seen = set()
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", name):
            continue
        if name in seen:
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


def strip_legacy_tool_contract(system_prompt: Optional[str]) -> Optional[str]:
    """Remove only TurnProcessor's textual Tool Contract for native proxy calls."""
    if not system_prompt:
        return system_prompt
    cleaned = re.sub(
        r"(?:^|\n)Tool Contract:\n[\s\S]*?\n\nMemory:\n",
        "\nMemory:\n",
        system_prompt,
        count=1,
    )
    return cleaned.strip()


def _decode_bridge_call(content: Any) -> Optional[Tuple[str, str, Dict[str, Any]]]:
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
    return call_id, name, arguments


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
                call_id, name, arguments = bridged
                normalized.append({
                    "role": "assistant",
                    "content": None,
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
            if "result from the host" in result_text[:512].lower():
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

        messages: List[Dict[str, Any]] = []
        native_system_prompt = strip_legacy_tool_contract(request.system_prompt)
        if native_system_prompt:
            messages.append({"role": "system", "content": native_system_prompt})
        messages.extend(normalize_native_tool_messages([dict(m) for m in request.messages]))

        tools = extract_openai_tools(request.system_prompt)
        payload: Dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
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
        except socket.timeout:
            raise ProviderTimeoutError(
                f"KITT reverse proxy timed out after {request.timeout_seconds}s"
            )
        except urllib.error.HTTPError as exc:
            body = read_error_body(exc)
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
                raise ProviderProtocolError(
                    f"KITT reverse proxy rejected the request: {matched}"
                ) from exc
            handle_http_error(exc, url)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                raise ProviderTimeoutError(
                    f"KITT reverse proxy timed out after {request.timeout_seconds}s"
                )
            raise ProviderConnectionError(
                f"Could not connect to KITT reverse proxy at {url}: {exc}"
            )

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
            bridge = {"id": call_id, "name": name, "arguments": arguments}
            yield (
                "<kitt-tool>"
                + json.dumps(bridge, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e")
                + "</kitt-tool>"
            )
            return

        if content_parts:
            yield "".join(content_parts)
