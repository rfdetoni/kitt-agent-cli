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
    def validate_and_parse_task(raw_json_str: str, original_prompt: str) -> Tuple[bool, SemanticTask, str]:
        import re
        try:
            # Fallback heuristic: strip markdown code blocks if present
            cleaned_str = raw_json_str.strip()
            if cleaned_str.startswith("```"):
                # Try to extract content inside ```json ... ``` or ``` ... ```
                match = re.search(r'```(?:json)?\s*(.*?)\s*```', cleaned_str, re.DOTALL)
                if match:
                    cleaned_str = match.group(1)
                
            data = json.loads(cleaned_str)
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
