import time
from typing import Optional, Literal
from dataclasses import dataclass
from kitt.domain.entities import SemanticTask, ContextPlan, ModelProfile
from kitt.llm.client import LLMClient, LLMError
from kitt.context_filter.deterministic_extractor import DeterministicExtractor
from kitt.context_filter.schema import ContextFilterSchemaValidator
from kitt.context_filter.fallback import DeterministicFallbackPlanner
from kitt.context_filter.context_planner import ContextPlanner

SYSTEM_CONTEXT_FILTER_PROMPT = """You are a Semantic Task Compiler.
Convert the user's request into a minimal, execution-oriented canonical semantic representation.

Emit ONLY a valid JSON object matching this schema:
{
  "intent": "IMPLEMENT|ASK|PLAN|DEBUG|TEST|REVIEW|DOCUMENT|REFACTOR|UNKNOWN",
  "goal": "Concise summary in English (<= 300 chars)",
  "secondary_intents": [],
  "actions": ["atomic action in English"],
  "symbols": ["ExactSymbolName"],
  "paths": ["exact/path/to/file.ext"],
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
  "validation_hints": ["run test command or check"],
  "risk": "LOW|MEDIUM|HIGH",
  "confidence": 1.0
}

RULES:
1. DO NOT solve the task or write implementation code.
2. DO NOT invent files, symbols, requirements, or dependencies not in the prompt.
3. Normalize natural language fields ('goal', 'actions', 'validation_hints') to concise, imperative English.
4. Preserve VERBATIM: file paths, identifiers, symbols, class/method names, commands, diagnostics, quoted literals, and constraint 'text'.
5. Every constraint 'text' MUST be an exact literal substring of the prompt.
6. Output JSON ONLY. No markdown, no conversation.
"""

FilterSource = Literal['LLM', 'LLM_FIRST', 'DETERMINISTIC_BYPASS', 'FALLBACK']

LLM_FIRST_CAPABILITY_TOOLS = (
    "kitt_runtime",
    "run_command",
    "artifact_store",
    "artifact_read",
    "child_spawn",
    "child_ask",
    "child_inspect",
    "goal_update",
    "goal_inspect",
    "memory_recall",
    "memory_save",
    "mcp_call",
)


def _is_reverse_proxy_profile(profile: ModelProfile) -> bool:
    """Return whether semantic planning would consume a browser-backed API turn."""
    backend = str(getattr(profile, "backend", "") or "").strip().lower()
    protocol = str(getattr(profile, "protocol", "") or "").strip().lower()
    return backend in {"kitt-reverse-proxy", "kitt-proxy"} or protocol == "kitt-reverse-proxy"


@dataclass
class SemanticFilterResult:
    task: SemanticTask
    plan: ContextPlan
    source: FilterSource
    fallback_reason: Optional[str] = None
    latency_ms: float = 0.0


def llm_first_filter_result(prompt: str, *, latency_ms: float = 0.0) -> SemanticFilterResult:
    """Build a non-semantic execution envelope for browser-backed agents.

    The prompt is carried verbatim. No intent, scope, language, technology or
    domain inference is performed by KITT; WebChat owns those decisions.
    """
    task = SemanticTask(
        original_prompt=prompt,
        intent="UNKNOWN",
        goal="",
        actions=[],
        paths=[],
        confidence=1.0,
    )
    plan = ContextPlan(
        search_queries=[prompt],
        preferred_paths=[],
        enabled_tools=list(LLM_FIRST_CAPABILITY_TOOLS),
        include_original_prompt=True,
        confidence=1.0,
    )
    return SemanticFilterResult(
        task=task,
        plan=plan,
        source="LLM_FIRST",
        fallback_reason="reverse_proxy_llm_first",
        latency_ms=latency_ms,
    )


class SemanticFilter:
    """Orchestrates dual-model context filtering: deterministic bypass, context LLM call, schema validation, and fallback."""

    def __init__(self, context_profile: ModelProfile, llm_client: Optional[LLMClient] = None):
        self.profile = context_profile
        self.llm_client = llm_client
        self.extractor = DeterministicExtractor()
        self.fallback_planner = DeterministicFallbackPlanner()
        self.planner = ContextPlanner()

    def filter_and_plan(
        self, prompt: str, session_key: Optional[str] = None, *, deterministic_only: bool = False
    ) -> SemanticFilterResult:
        start_t = time.time()

        reverse_proxy = _is_reverse_proxy_profile(self.profile)
        if reverse_proxy:
            # Browser-backed execution is LLM-first. Do not compile, classify,
            # translate, summarize, or otherwise reinterpret the human request.
            self.llm_client = None
            return llm_first_filter_result(
                prompt,
                latency_ms=(time.time() - start_t) * 1000.0,
            )

        if deterministic_only or self.extractor.is_trivial_prompt(prompt):
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

        # Rule 2: Call Context LLM lazily.
        try:
            if self.llm_client is None:
                self.llm_client = LLMClient(self.profile)
            messages = [{"role": "user", "content": prompt}]
            response_text = self.llm_client.chat(
                messages,
                system_prompt=SYSTEM_CONTEXT_FILTER_PROMPT,
                response_format="json",
                session_key=session_key,
            )

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
