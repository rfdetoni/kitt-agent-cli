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
        try:
            data = json.loads(raw_json_str)
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
                        start = int(c.get("source_start", -1))
                        end = int(c.get("source_end", -1))
                        kind: ConstraintKind = c.get("kind", "MANDATORY")
                        if kind not in VALID_KINDS:
                            kind = "MANDATORY"

                        # Validate constraint substring match if span provided
                        if 0 <= start < end <= len(original_prompt):
                            actual_substring = original_prompt[start:end]
                            if text != actual_substring and text not in actual_substring:
                                # Fix span
                                start = original_prompt.find(text) if text in original_prompt else -1
                                end = start + len(text) if start >= 0 else -1

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
                actions=[str(a) for a in data.get("actions", [])],
                symbols=[str(s) for s in data.get("symbols", [])],
                paths=[str(p) for p in data.get("paths", []) if ".." not in str(p)],
                technologies=[str(t) for t in data.get("technologies", [])],
                constraints=valid_constraints,
                risk=risk,
                confidence=confidence
            )

            if confidence < 0.6:
                return False, task, f"Low confidence score: {confidence}"

            return True, task, ""
        except Exception as e:
            return False, SemanticTask(original_prompt=original_prompt), f"JSON parse error: {e}"
