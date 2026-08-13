import time
from typing import Tuple, Optional, Literal
from dataclasses import dataclass
from kitt.domain.entities import SemanticTask, ContextPlan, ModelProfile
from kitt.llm.client import LLMClient, LLMError
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

FilterSource = Literal['LLM', 'DETERMINISTIC_BYPASS', 'FALLBACK']

@dataclass
class SemanticFilterResult:
    task: SemanticTask
    plan: ContextPlan
    source: FilterSource
    fallback_reason: Optional[str] = None
    latency_ms: float = 0.0

class SemanticFilter:
    """Orchestrates dual-model context filtering: deterministic bypass, context LLM call, schema validation, and fallback."""

    def __init__(self, context_profile: ModelProfile, llm_client: Optional[LLMClient] = None):
        self.profile = context_profile
        self.llm_client = llm_client or LLMClient(context_profile)
        self.extractor = DeterministicExtractor()
        self.fallback_planner = DeterministicFallbackPlanner()
        self.planner = ContextPlanner()

    def filter_and_plan(self, prompt: str) -> SemanticFilterResult:
        start_t = time.time()

        # Rule 1: Trivial prompt bypass
        if self.extractor.is_trivial_prompt(prompt):
            task = self.fallback_planner.generate_task(prompt)
            plan = self.fallback_planner.generate_plan(task)
            latency = (time.time() - start_t) * 1000.0
            return SemanticFilterResult(
                task=task,
                plan=plan,
                source='DETERMINISTIC_BYPASS',
                fallback_reason=None,
                latency_ms=latency
            )

        if (self.profile.backend.lower() == "ollama" and not self.profile.supports_json
                and isinstance(self.llm_client, LLMClient)):
            task = self.fallback_planner.generate_task(prompt)
            plan = self.fallback_planner.generate_plan(task)
            return SemanticFilterResult(
                task=task,
                plan=plan,
                source="FALLBACK",
                fallback_reason="Ollama context profile does not support JSON responses.",
                latency_ms=(time.time() - start_t) * 1000.0,
            )

        # Rule 2: Call Context LLM
        try:
            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm_client.chat(messages, system_prompt=SYSTEM_CONTEXT_FILTER_PROMPT, response_format="json")

            if len(response_text) > 16384:
                raise ValueError("JSON response exceeded 16 KiB limit.")

            valid, task, err = ContextFilterSchemaValidator.validate_and_parse_task(response_text, prompt)
            latency = (time.time() - start_t) * 1000.0

            if valid:
                plan = self.planner.build_plan(task)
                return SemanticFilterResult(
                    task=task,
                    plan=plan,
                    source='LLM',
                    fallback_reason=None,
                    latency_ms=latency
                )
            else:
                task = self.fallback_planner.generate_task(prompt)
                plan = self.fallback_planner.generate_plan(task)
                return SemanticFilterResult(
                    task=task,
                    plan=plan,
                    source='FALLBACK',
                    fallback_reason=err,
                    latency_ms=latency
                )

        except LLMError as le:
            latency = (time.time() - start_t) * 1000.0
            task = self.fallback_planner.generate_task(prompt)
            plan = self.fallback_planner.generate_plan(task)
            return SemanticFilterResult(
                task=task,
                plan=plan,
                source='FALLBACK',
                fallback_reason=f"LLM Error: {le}",
                latency_ms=latency
            )
        except Exception as e:
            latency = (time.time() - start_t) * 1000.0
            task = self.fallback_planner.generate_task(prompt)
            plan = self.fallback_planner.generate_plan(task)
            return SemanticFilterResult(
                task=task,
                plan=plan,
                source='FALLBACK',
                fallback_reason=f"Unexpected Error: {e}",
                latency_ms=latency
            )
