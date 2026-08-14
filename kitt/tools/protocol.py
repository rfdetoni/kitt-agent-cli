import json
import re
from typing import Any, Dict, Optional, Tuple

TOOL_CALL_OPEN = "<kitt-tool>"
TOOL_CALL_CLOSE = "</kitt-tool>"


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


def parse_tool_call(text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not text:
        return None

    # 1. Locate TOOL_CALL_OPEN in text
    start_idx = text.find(TOOL_CALL_OPEN)
    if start_idx == -1:
        return None

    body = text[start_idx + len(TOOL_CALL_OPEN) :]
    end_idx = body.find(TOOL_CALL_CLOSE)
    if end_idx != -1:
        body = body[:end_idx]
    body = body.strip()

    if not body:
        raise ValueError("Incomplete kitt-tool envelope")

    # 2. Try standard json.loads with strict=False (allows unescaped newlines/tabs)
    try:
        val = json.loads(body, strict=False)
        if isinstance(val, dict) and isinstance(val.get("name"), str):
            args = val.get("arguments", {})
            if isinstance(args, dict):
                return val["name"], args
    except Exception:
        pass

    # 3. Robust rescue for large multi-line tools (e.g. write_file, apply_patch)
    # where unescaped double quotes inside content break standard JSON parsing
    name_match = re.search(r'"name"\s*:\s*"([a-zA-Z0-9_-]+)"', body)
    if name_match:
        tool_name = name_match.group(1)
        args_idx = body.find('"arguments"')
        if args_idx != -1:
            args_part = body[args_idx:]
            if tool_name == "write_file":
                path_match = re.search(r'"path"\s*:\s*"([^"]+)"', args_part)
                content_match = re.search(r'"content"\s*:\s*"', args_part)
                if path_match and content_match:
                    path = path_match.group(1)
                    c_start = content_match.end()
                    c_end = args_part.rfind('"')
                    if c_end > c_start:
                        raw_content = args_part[c_start:c_end]
                        return tool_name, {"path": path, "content": _unescape_json_string_content(raw_content)}
            elif tool_name == "apply_patch":
                patch_match = re.search(r'"patch"\s*:\s*"', args_part)
                if patch_match:
                    p_start = patch_match.end()
                    p_end = args_part.rfind('"')
                    if p_end > p_start:
                        raw_patch = args_part[p_start:p_end]
                        return tool_name, {"patch": _unescape_json_string_content(raw_patch)}

    # 4. Fallback to schema robust parser
    try:
        from kitt.context_filter.schema import SemanticFilterSchema

        val = SemanticFilterSchema._parse_json_robust(body)
        if isinstance(val, dict) and isinstance(val.get("name"), str):
            args = val.get("arguments", {})
            if isinstance(args, dict):
                return val["name"], args
    except Exception:
        pass

    raise ValueError("Incomplete or invalid kitt-tool envelope JSON")
