import json
TOOL_CALL_OPEN="<kitt-tool>"
TOOL_CALL_CLOSE="</kitt-tool>"
def parse_tool_call(text):
    stripped=text.strip()
    if not stripped.startswith(TOOL_CALL_OPEN): return None
    if not stripped.endswith(TOOL_CALL_CLOSE): raise ValueError("Incomplete kitt-tool envelope")
    value=json.loads(stripped[len(TOOL_CALL_OPEN):-len(TOOL_CALL_CLOSE)].strip())
    if not isinstance(value,dict) or not isinstance(value.get("name"),str):
        raise ValueError("Tool call requires string name")
    args=value.get("arguments",{})
    if not isinstance(args,dict): raise ValueError("Tool arguments must be an object")
    return value["name"],args
