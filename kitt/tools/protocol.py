import json
import re
from typing import Any, Dict, Optional, Tuple

TOOL_CALL_OPEN = "<kitt-tool>"
TOOL_CALL_CLOSE = "</kitt-tool>"

CANONICAL_TOOLS = {
    "kitt_runtime": "kitt_runtime",
    "write_file": "write_file",
    "write": "write_file",
    "create_file": "write_file",
    "save_file": "write_file",
    "create_directory": "create_directory",
    "createdirectory": "create_directory",
    "mkdir": "create_directory",
    "apply_patch": "apply_patch",
    "patch": "apply_patch",
    "edit_file": "apply_patch",
    "edit": "apply_patch",
    "read_file": "read_file",
    "read": "read_file",
    "view_file": "read_file",
    "run_command": "run_command",
    "bash": "run_command",
    "exec": "run_command",
    "terminal": "run_command",
    "command": "run_command",
    "sh": "run_command",
    "list_files": "list_files",
    "ls": "list_files",
    "list_dir": "list_files",
    "dir": "list_files",
    "repository_map": "repository_map",
    "repomap": "repository_map",
    "search": "search",
    "python_compute": "python_compute",
    "git_status": "git_status",
    "git_diff": "git_diff",
    "artifact_store": "artifact_store",
    "artifact_read": "artifact_read",
    "artifact_list": "artifact_list",
    "queue_input": "queue_input",
    "goal_create": "goal_create",
    "goal_add_gate": "goal_add_gate",
    "child_spawn": "child_spawn",
    "harness_remember": "harness_remember",
}


def _unescape_json_string_content(raw: str) -> str:
    """Best-effort unescape for JSON string content."""
    res = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "n":
                res.append("\n")
                i += 2
            elif nxt == "r":
                res.append("\r")
                i += 2
            elif nxt == "t":
                res.append("\t")
                i += 2
            elif nxt == '"':
                res.append('"')
                i += 2
            elif nxt == "\\":
                res.append("\\")
                i += 2
            elif nxt == "/":
                res.append("/")
                i += 2
            else:
                res.append(nxt)
                i += 2
        else:
            res.append(raw[i])
            i += 1
    return "".join(res)


def _strip_think_blocks(text: str) -> str:
    """Removes <think>...</think> and <thought>...</thought> blocks from text."""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<thought>.*?</thought>", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def _strip_markdown_fences(s: str) -> str:
    """Strips outer markdown code fences from a string."""
    cleaned = s.strip()
    if cleaned.startswith("```"):
        m = re.match(r"^```(?:json|xml|html|text)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _parse_directory_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Normalize direct and namespaced directory calls emitted by tool bridges."""
    match = re.search(
        r"\b(?:(?:repo)\.)?(?:create_directory|createdirectory|mkdir)\s*\(\s*"
        r"(?:(?:operation\s*=\s*[\"']?repo\.create_directory[\"']?\s*,\s*)?)"
        r"(?:path\s*=\s*)?(?:[\"'](?P<quoted>[^\"']+)[\"']|(?P<bare>[^,\)]+))\s*\)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    path = (match.group("quoted") or match.group("bare") or "").strip()
    if not path:
        return None
    if re.search(r"operation\s*=\s*[\"']?repo\.create_directory", match.group(0), re.IGNORECASE):
        return "kitt_runtime", {
            "operation": "repo.create_directory",
            "arguments": {"path": path},
        }
    return "create_directory", {"path": path}


def parse_tool_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not text:
        return None

    canonical = text.strip()
    if canonical.startswith(TOOL_CALL_OPEN):
        if not canonical.endswith(TOOL_CALL_CLOSE):
            raise ValueError("Incomplete kitt-tool envelope")
        try:
            body = _strip_markdown_fences(canonical[len(TOOL_CALL_OPEN):-len(TOOL_CALL_CLOSE)])
            value = json.loads(body, strict=False)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("Invalid kitt-tool envelope JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("kitt-tool payload must be an object")
        name, arguments = value.get("name"), value.get("arguments")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", name):
            raise ValueError("Invalid kitt-tool name")
        if not isinstance(arguments, dict):
            raise ValueError("kitt-tool arguments must be an object")
        return CANONICAL_TOOLS.get(name.lower(), name), arguments

    cleaned_text = _strip_think_blocks(text)
    if not cleaned_text:
        return None

    directory_call = _parse_directory_call(cleaned_text)
    if directory_call is not None:
        return directory_call

    if "<<<<<<< SEARCH" in cleaned_text and "=======" in cleaned_text and ">>>>>>> REPLACE" in cleaned_text:
        if not ("<kitt-tool>" in cleaned_text or "<tool>" in cleaned_text or '"name"' in cleaned_text):
            return "apply_patch", {"patch": cleaned_text.strip()}

    xml_write = re.search(r'<write_file(?:\s+path="([^"]+)")?>(.*?)(?:</write_file>|$)', cleaned_text, re.DOTALL | re.IGNORECASE)
    if xml_write:
        path = xml_write.group(1) or ""
        content = xml_write.group(2)
        if not path:
            path_tag = re.search(r"<path>(.*?)</path>", content, re.DOTALL | re.IGNORECASE)
            content_tag = re.search(r"<content>(.*?)</content>", content, re.DOTALL | re.IGNORECASE)
            if path_tag:
                path = path_tag.group(1).strip()
                content = content_tag.group(1) if content_tag else re.sub(r"<path>.*?</path>", "", content, flags=re.DOTALL).strip()
        if path:
            return "write_file", {"path": path, "content": content}

    func_match = re.search(r'\b(write_file|write|apply_patch|patch|read_file|read|run_command|bash|list_files|create_directory|mkdir|search)\s*\(\s*(?:path\s*=\s*)?["\']([^"\']+)["\'](?:\s*,\s*(?:content|patch|command|query)\s*=\s*["\']([\s\S]*?)["\'])?\s*\)', cleaned_text, re.IGNORECASE)
    if func_match:
        tname = CANONICAL_TOOLS.get(func_match.group(1).lower(), func_match.group(1).lower())
        arg1 = func_match.group(2)
        arg2 = func_match.group(3) or ""
        if tname == "write_file":
            return tname, {"path": arg1, "content": arg2}
        elif tname == "apply_patch":
            return tname, {"patch": arg1 if not arg2 else arg2}
        elif tname == "read_file":
            return tname, {"path": arg1}
        elif tname == "run_command":
            return tname, {"command": arg1}
        elif tname == "create_directory":
            return tname, {"path": arg1}
        elif tname == "search":
            return tname, {"query": arg1}

    body = ""
    for tag_open, tag_close in [
        (TOOL_CALL_OPEN, TOOL_CALL_CLOSE),
        ("<tool>", "</tool>"),
        ("<tool_call>", "</tool_call>"),
        ("<function_call>", "</function_call>"),
        ("<kitt_tool>", "</kitt_tool>"),
    ]:
        start_idx = cleaned_text.find(tag_open)
        if start_idx != -1:
            raw_inside = cleaned_text[start_idx + len(tag_open) :]
            end_idx = raw_inside.find(tag_close)
            if end_idx != -1:
                body = raw_inside[:end_idx]
            else:
                body = raw_inside
            break

    if not body:
        code_blocks = re.findall(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', cleaned_text, re.IGNORECASE)
        for cb in code_blocks:
            if re.search(r'"(?:name|tool|action|function)"\s*:', cb):
                body = cb
                break

    if not body:
        m = re.search(r'\{\s*"(?:name|tool|action|function)"\s*:\s*"([a-zA-Z0-9_-]+)"', cleaned_text)
        if m:
            raw_body = cleaned_text[m.start() :]
            brace_count = 0
            end_pos = -1
            for idx, ch in enumerate(raw_body):
                if ch == "{":
                    brace_count += 1
                elif ch == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = idx + 1
                        break
            body = raw_body[:end_pos].strip() if end_pos != -1 else raw_body.strip()

    if not body:
        cb_tagged = re.search(r'```[a-zA-Z0-9_-]+[:\s]+(?:path|filename|file)?=?["\']?([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9]+)["\']?\s*\n([\s\S]*?)```', cleaned_text)
        if cb_tagged:
            path = cb_tagged.group(1).strip()
            content = cb_tagged.group(2)
            if "<<<<<<< SEARCH" in content and "=======" in content and ">>>>>>> REPLACE" in content:
                return "apply_patch", {"patch": f"{path}\n{content}"}
            return "write_file", {"path": path, "content": content}

        labeled_cb = re.search(
            r'([a-zA-Z0-9_\-./\\]+\.(?:html|htm|py|js|ts|jsx|tsx|css|json|md|txt|sh|bash|toml|yaml|yml|rs|go|sql|c|cpp|h|hpp))\b[^\n]*\n+```(?:[a-zA-Z0-9_-]+)?\s*\n([\s\S]*?)```',
            cleaned_text,
            re.IGNORECASE
        )
        if labeled_cb:
            path = labeled_cb.group(1).strip().strip("`'\"*:()[]{}")
            content = labeled_cb.group(2)
            if "<<<<<<< SEARCH" in content and "=======" in content and ">>>>>>> REPLACE" in content:
                return "apply_patch", {"patch": f"{path}\n{content}"}
            return "write_file", {"path": path, "content": content}

        return None

    body = _strip_markdown_fences(body)
    if not body:
        raise ValueError("Incomplete tool envelope")

    try:
        val = json.loads(body, strict=False)
        if isinstance(val, dict):
            raw_name = val.get("name") or val.get("tool") or val.get("action") or val.get("function")
            if isinstance(raw_name, str):
                tool_name = CANONICAL_TOOLS.get(raw_name.lower(), raw_name.lower())
                args = val.get("arguments") or val.get("parameters") or val.get("action_input") or val.get("input")
                if isinstance(args, dict):
                    return tool_name, args
                elif isinstance(args, str):
                    try:
                        parsed_inner = json.loads(args, strict=False)
                        if isinstance(parsed_inner, dict):
                            return tool_name, parsed_inner
                    except Exception:
                        pass
                flat_args = {k: v for k, v in val.items() if k not in ("name", "tool", "action", "function", "type", "id")}
                if flat_args:
                    return tool_name, flat_args
                return tool_name, {}
    except Exception:
        pass

    name_match = re.search(r'"(?:name|tool|action|function)"\s*:\s*"([a-zA-Z0-9_-]+)"', body)
    if name_match:
        raw_name = name_match.group(1)
        tool_name = CANONICAL_TOOLS.get(raw_name.lower(), raw_name.lower())

        if tool_name == "write_file":
            path_match = re.search(r'"(?:path|filename|file|target)"\s*:\s*"([^"]+)"', body)
            content_marker = re.search(r'"(?:content|text|body)"\s*:\s*"', body)
            if path_match and content_marker:
                path = path_match.group(1)
                c_start = content_marker.end()
                trailing_match = re.search(r'"\s*(?:,\s*"[^"]+"\s*:\s*"[^"]+"\s*)?\}\s*\}?$', body)
                if trailing_match:
                    c_end = trailing_match.start()
                else:
                    c_end = body.rfind('"')
                if c_end > c_start:
                    raw_content = body[c_start:c_end]
                    return tool_name, {"path": path, "content": _unescape_json_string_content(raw_content)}

        elif tool_name == "apply_patch":
            patch_marker = re.search(r'"(?:patch|diff)"\s*:\s*"', body)
            if patch_marker:
                p_start = patch_marker.end()
                trailing_match = re.search(r'"\s*\}\s*\}?$', body)
                p_end = trailing_match.start() if trailing_match else body.rfind('"')
                if p_end > p_start:
                    raw_patch = body[p_start:p_end]
                    return tool_name, {"patch": _unescape_json_string_content(raw_patch)}
            if "<<<<<<< SEARCH" in body and "=======" in body and ">>>>>>> REPLACE" in body:
                diff_start = body.find("<<<<<<< SEARCH")
                lines_before = body[:diff_start].strip().splitlines()
                filename = lines_before[-1].strip() if lines_before else ""
                diff_end = body.rfind(">>>>>>> REPLACE") + len(">>>>>>> REPLACE")
                raw_diff = body[diff_start:diff_end]
                full_patch = f"{filename}\n{raw_diff}" if filename and not filename.startswith("{") else raw_diff
                return tool_name, {"patch": full_patch}

        elif tool_name in ("read_file", "list_files", "create_directory", "run_command", "search"):
            path_match = re.search(r'"(?:path|filename|file|command|cmd|query)"\s*:\s*"([^"]+)"', body)
            if path_match:
                key = "command" if tool_name == "run_command" else ("query" if tool_name == "search" else "path")
                return tool_name, {key: path_match.group(1)}

    try:
        from kitt.context_filter.schema import ContextFilterSchemaValidator

        val = ContextFilterSchemaValidator._parse_json_robust(body)
        if isinstance(val, dict):
            raw_name = val.get("name") or val.get("tool") or val.get("action")
            if isinstance(raw_name, str):
                tool_name = CANONICAL_TOOLS.get(raw_name.lower(), raw_name.lower())
                args = val.get("arguments") or val.get("parameters") or val.get("action_input")
                if isinstance(args, dict):
                    return tool_name, args
                flat_args = {k: v for k, v in val.items() if k not in ("name", "tool", "action", "type", "id")}
                if flat_args:
                    return tool_name, flat_args
                return tool_name, {}
    except Exception:
        pass

    raise ValueError("Incomplete or invalid kitt-tool envelope JSON")