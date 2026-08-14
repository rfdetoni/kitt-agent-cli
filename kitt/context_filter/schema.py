import json
from typing import Dict, Any, Tuple
from kitt.domain.entities import (
    SemanticTask, ContextPlan, Constraint, TaskIntent, RiskLevel, ConstraintKind
)

VALID_INTENTS = {'ASK', 'PLAN', 'IMPLEMENT', 'DEBUG', 'TEST', 'REVIEW', 'DOCUMENT', 'REFACTOR', 'UNKNOWN'}
VALID_RISKS = {'LOW', 'MEDIUM', 'HIGH'}
VALID_KINDS = {'NEGATIVE', 'MANDATORY', 'LIMIT', 'SCOPE'}

class ContextFilterSchemaValidator:
    """Validates raw LLM JSON responses against SemanticTask and ContextPlan contracts."""

    @staticmethod
    def _parse_json_robust(s: str) -> dict:
        import re
        import ast

        cleaned = s.strip()
        if "```" in cleaned:
            match = re.search(r'```(?:json)?\s*(.*?)\s*```', cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1).strip()
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start:end+1]

        try:
            return json.loads(cleaned)
        except Exception:
            pass

        # 1. Strip line comments (// ...) outside strings
        lines = []
        for line in cleaned.splitlines():
            if "//" in line and not line.strip().startswith("http"):
                parts = line.split("//")
                if parts[0].count('"') % 2 == 0:
                    line = parts[0]
            lines.append(line)
        cleaned = "\n".join(lines)

        # 2. Fix unescaped inner double quotes in single-line key-value strings
        fixed_lines = []
        for line in cleaned.splitlines():
            m = re.match(r'^(\s*"[a-zA-Z0-9_]+"\s*:\s*")(.*)("[,]?\s*)$', line)
            if m:
                prefix, content, suffix = m.group(1), m.group(2), m.group(3)
                fixed_content = re.sub(r'(?<!\\)"', r'\"', content)
                line = f"{prefix}{fixed_content}{suffix}"
            fixed_lines.append(line)
        repaired = "\n".join(fixed_lines)

        # 3. Convert Python literals True/False/None outside strings
        repaired = re.sub(r':\s*True\b', ': true', repaired)
        repaired = re.sub(r':\s*False\b', ': false', repaired)
        repaired = re.sub(r':\s*None\b', ': null', repaired)

        # 4. Strip trailing commas before } or ]
        repaired = re.sub(r',\s*([\}\]])', r'\1', repaired)

        # 5. Fix missing commas between lines (nested arrays, objects, fields)
        lines = repaired.splitlines()
        comma_lines = []
        for i in range(len(lines)):
            curr = lines[i]
            curr_stripped = curr.strip()
            if i + 1 < len(lines):
                next_stripped = ""
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        next_stripped = lines[j].strip()
                        break

                if curr_stripped and next_stripped:
                    is_value_end = (
                        curr_stripped.endswith('"') or
                        curr_stripped[-1] in "0123456789]}" or
                        curr_stripped.endswith("true") or
                        curr_stripped.endswith("false") or
                        curr_stripped.endswith("null")
                    )
                    is_not_colon = (
                        not curr_stripped.endswith(":") and
                        not curr_stripped.endswith(",") and
                        not curr_stripped.endswith("{") and
                        not curr_stripped.endswith("[")
                    )
                    next_starts_token = (
                        next_stripped.startswith('"') or
                        next_stripped.startswith("{") or
                        next_stripped.startswith("[")
                    ) and not next_stripped.startswith("}") and not next_stripped.startswith("]")

                    if is_value_end and is_not_colon and next_starts_token:
                        curr = curr + ","
            comma_lines.append(curr)
        repaired = "\n".join(comma_lines)

        # 6. Strip any trailing commas introduced before closing delimiters
        repaired = re.sub(r',\s*([\}\]])', r'\1', repaired)

        # 7. Strip non-printable ASCII control chars except \n, \t, \r
        repaired = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', repaired)

        try:
            return json.loads(repaired)
        except Exception:
            pass

        # 8. Fallback: ast.literal_eval on python dict representation
        try:
            py_dict = ast.literal_eval(cleaned)
            if isinstance(py_dict, dict):
                return py_dict
        except Exception:
            pass

        try:
            py_repaired = re.sub(r'\btrue\b', 'True', repaired)
            py_repaired = re.sub(r'\bfalse\b', 'False', py_repaired)
            py_repaired = re.sub(r'\bnull\b', 'None', py_repaired)
            py_dict = ast.literal_eval(py_repaired)
            if isinstance(py_dict, dict):
                return py_dict
        except Exception:
            pass

        # 9. Fallback: Auto-close truncated JSON (unclosed strings, brackets, braces)
        try:
            closed = repaired.strip()
            unescaped_quotes = len(re.findall(r'(?<!\\)"', closed))
            if unescaped_quotes % 2 != 0:
                closed += '"'
            stack = []
            in_str = False
            escape = False
            for ch in closed:
                if escape:
                    escape = False
                    continue
                if ch == '\\':
                    escape = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if not in_str:
                    if ch in "{[":
                        stack.append(ch)
                    elif ch in "}]":
                        if stack and ((ch == "}" and stack[-1] == "{") or (ch == "]" and stack[-1] == "[")):
                            stack.pop()
            for opener in reversed(stack):
                if opener == "{":
                    closed += "}"
                elif opener == "[":
                    closed += "]"
            closed = re.sub(r',\s*([\}\]])', r'\1', closed)
            return json.loads(closed)
        except Exception:
            pass

        return json.loads(repaired)

    @staticmethod
    def validate_and_parse_task(raw_json_str: str, original_prompt: str) -> Tuple[bool, SemanticTask, str]:
        try:
            data = ContextFilterSchemaValidator._parse_json_robust(raw_json_str)
            if not isinstance(data, dict):
                return False, SemanticTask(original_prompt=original_prompt), "Root JSON is not an object."

            intent: TaskIntent = data.get("intent", "UNKNOWN")
            if intent not in VALID_INTENTS:
                intent = "UNKNOWN"

            confidence = float(data.get("confidence", 1.0))
            if not (0.0 <= confidence <= 1.0):
                confidence = 0.5

            risk: RiskLevel = data.get("risk", "LOW")
            if risk not in VALID_RISKS:
                risk = "LOW"

            constraints_data = data.get("constraints", [])
            valid_constraints = []
            if isinstance(constraints_data, list):
                for c in constraints_data:
                    if isinstance(c, dict):
                        text = str(c.get("text", "")).strip()
                        if not text:
                            continue

                        # Strict validation: constraint text MUST be a literal substring of original_prompt
                        idx = original_prompt.find(text)
                        if idx == -1:
                            # Reject invented constraint
                            continue

                        start = idx
                        end = idx + len(text)

                        kind: ConstraintKind = c.get("kind", "MANDATORY")
                        if kind not in VALID_KINDS:
                            kind = "MANDATORY"

                        valid_constraints.append(
                            Constraint(
                                text=text,
                                kind=kind,
                                source_start=start,
                                source_end=end,
                                mandatory=bool(c.get("mandatory", True))
                            )
                        )

            task = SemanticTask(
                original_prompt=original_prompt,
                intent=intent,
                secondary_intents=[i for i in data.get("secondary_intents", []) if i in VALID_INTENTS],
                actions=[str(a) for a in data.get("actions", [])[:8]],
                symbols=[str(s) for s in data.get("symbols", [])[:20]],
                paths=[str(p) for p in data.get("paths", [])[:12] if ".." not in str(p)],
                technologies=[str(t) for t in data.get("technologies", [])[:10]],
                constraints=valid_constraints[:12],
                risk=risk,
                confidence=confidence
            )

            if confidence < 0.6:
                return False, task, f"Low confidence score: {confidence}"

            return True, task, ""
        except Exception as e:
            return False, SemanticTask(original_prompt=original_prompt), f"JSON parse error: {e}"
