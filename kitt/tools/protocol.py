import json
import re
from typing import Any, Dict, Optional, Tuple

TOOL_CALL_OPEN = "<kitt-tool>"
TOOL_CALL_CLOSE = "</kitt-tool>"

CANONICAL_TOOLS = {
    "write_file": "write_file",
    "write": "write_file",
    "create_file": "write_file",
    "save_file": "write_file",
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
        # If closing ``` is missing or different:
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def parse_tool_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not text:
        return None

    cleaned_text = _strip_think_blocks(text)
    if not cleaned_text:
        return None

    # Strategy 0: Check raw SEARCH/REPLACE diff block directly
    if "<<<<<<< SEARCH" in cleaned_text and "=======" in cleaned_text and ">>>>>>> REPLACE" in cleaned_text:
        # Check if wrapped inside tool call or bare
        if not ("<kitt-tool>" in cleaned_text or "<tool>" in cleaned_text or '"name"' in cleaned_text):
            return "apply_patch", {"patch": cleaned_text.strip()}

    # Strategy 1: Check XML-style tool calls (e.g. <write_file path="...">...</write_file>)
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

    # Strategy 2: Check function-call style: Write(path="...", content="...") or write_file(...)
    func_match = re.search(r'\b(write_file|write|apply_patch|patch|read_file|read|run_command|bash|list_files|search)\s*\(\s*(?:path\s*=\s*)?["\']([^"\']+)["\'](?:\s*,\s*(?:content|patch|command|query)\s*=\s*["\']([\s\S]*?)["\'])?\s*\)', cleaned_text, re.IGNORECASE)
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
        elif tname == "search":
            return tname, {"query": arg1}

    # Strategy 3: Envelope extraction (<kitt-tool>, <tool>, <tool_call>, <function_call>, or JSON block)
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
        # Check markdown code blocks with json containing a tool name
        code_blocks = re.findall(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', cleaned_text, re.IGNORECASE)
        for cb in code_blocks:
            if re.search(r'"(?:name|tool|action|function)"\s*:', cb):
                body = cb
                break

    if not body:
        # Check bare JSON in text
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
        return None

    body = _strip_markdown_fences(body)
    if not body:
        raise ValueError("Incomplete tool envelope")

    # Strategy 4: Standard json.loads with strict=False
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
                # Top-level arguments dictionary
                flat_args = {k: v for k, v in val.items() if k not in ("name", "tool", "action", "function", "type", "id")}
                if flat_args:
                    return tool_name, flat_args
                return tool_name, {}
    except Exception:
        pass

    # Strategy 5: Robust regex rescue for complex multi-line tools (write_file, apply_patch, etc.)
    name_match = re.search(r'"(?:name|tool|action|function)"\s*:\s*"([a-zA-Z0-9_-]+)"', body)
    if name_match:
        raw_name = name_match.group(1)
        tool_name = CANONICAL_TOOLS.get(raw_name.lower(), raw_name.lower())

        if tool_name == "write_file":
            path_match = re.search(r'"(?:path|filename|file|target)"\s*:\s*"([^"]+)"', body)
            # Find content start
            content_marker = re.search(r'"(?:content|text|body)"\s*:\s*"', body)
            if path_match and content_marker:
                path = path_match.group(1)
                c_start = content_marker.end()
                # Find content end: find the closing quote before the end of the JSON object
                # Look backwards from the end of body for '"' followed by optional whitespace and '}'
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
            # If SEARCH/REPLACE diff is in body directly
            if "<<<<<<< SEARCH" in body and "=======" in body and ">>>>>>> REPLACE" in body:
                diff_start = body.find("<<<<<<< SEARCH")
                # find previous line with filename
                lines_before = body[:diff_start].strip().splitlines()
                filename = lines_before[-1].strip() if lines_before else ""
                diff_end = body.rfind(">>>>>>> REPLACE") + len(">>>>>>> REPLACE")
                raw_diff = body[diff_start:diff_end]
                full_patch = f"{filename}\n{raw_diff}" if filename and not filename.startswith("{") else raw_diff
                return tool_name, {"patch": full_patch}

        elif tool_name in ("read_file", "list_files", "run_command", "search"):
            path_match = re.search(r'"(?:path|filename|file|command|cmd|query)"\s*:\s*"([^"]+)"', body)
            if path_match:
                key = "command" if tool_name == "run_command" else ("query" if tool_name == "search" else "path")
                return tool_name, {key: path_match.group(1)}

    # Strategy 6: Fallback to SemanticFilterSchema robust parser
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
