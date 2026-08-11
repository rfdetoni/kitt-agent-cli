from typing import Tuple
from kitt.domain.entities import SemanticTask, ContextPlan, ModelProfile
from kitt.llm.client import LLMClient
from kitt.context_filter.deterministic_extractor import DeterministicExtractor
from kitt.context_filter.schema import ContextFilterSchemaValidator
from kitt.context_filter.fallback import DeterministicFallbackPlanner
from kitt.context_filter.context_planner import ContextPlanner

SYSTEM_CONTEXT_FILTER_PROMPT = """You are K.I.T.T. Context Filter, a lightweight, deterministic task classification engine.
Your sole duty is to analyze the user's prompt and emit ONLY a valid JSON object matching this schema:

{
  "intent": "IMPLEMENT|ASK|PLAN|DEBUG|TEST|REVIEW|DOCUMENT|REFACTOR|UNKNOWN",
  "secondary_intents": [],
  "actions": ["action1"],
  "symbols": ["SymbolName"],
  "paths": ["path/to/file.ext"],
  "technologies": ["python"],
  "constraints": [
    {
      "text": "exact substring from prompt",
      "kind": "NEGATIVE|MANDATORY|LIMIT|SCOPE",
      "source_start": 0,
      "source_end": 10,
      "mandatory": true
    }
  ],
  "risk": "LOW|MEDIUM|HIGH",
  "confidence": 1.0
}

RULES:
1. DO NOT solve the task or write code.
2. DO NOT invent files or symbols not in the prompt.
3. Every constraint "text" MUST be an exact literal substring of the prompt.
4. Output JSON ONLY. No markdown, no conversation.
"""

class SemanticFilter:
    """Orchestrates dual-model context filtering: deterministic bypass, context LLM call, schema validation, and fallback."""

    def __init__(self, context_profile: ModelProfile):
        self.profile = context_profile
        self.extractor = DeterministicExtractor()
        self.fallback_planner = DeterministicFallbackPlanner()
        self.planner = ContextPlanner()

    def filter_and_plan(self, prompt: str) -> Tuple[SemanticTask, ContextPlan, bool]:
        # Rule: Trivial prompt bypass (short prompt, explicit target file)
        if self.extractor.is_trivial_prompt(prompt):
            task = self.fallback_planner.generate_task(prompt)
            plan = self.fallback_planner.generate_plan(task)
            return task, plan, True  # Bypassed small LLM call

        # Call Context LLM
        try:
            llm = LLMClient(self.profile)
            messages = [{"role": "user", "content": prompt}]
            response_text = llm.chat(messages, system_prompt=SYSTEM_CONTEXT_FILTER_PROMPT)

            valid, task, err = ContextFilterSchemaValidator.validate_and_parse_task(response_text, prompt)
            if valid:
                plan = self.planner.build_plan(task)
                return task, plan, False

        except Exception:
            pass

        # Fallback on LLM failure or validation error
        task = self.fallback_planner.generate_task(prompt)
        plan = self.fallback_planner.generate_plan(task)
        return task, plan, False
