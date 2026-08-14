import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from kitt.context.token_estimator import CalibratedTokenEstimator

class PromptTooLargeError(Exception):
    """Raised when task prompt and mandatory constraints exceed the context window."""

class TokenCounter:
    """Compatibility facade over the single calibrated token estimator."""

    estimator = CalibratedTokenEstimator()

    @staticmethod
    def count_tokens(text: str, num_messages: int = 1) -> int:
        if not text:
            return 0
        return TokenCounter.estimator.count_text(text).count + (num_messages * 3)

    @staticmethod
    def count_messages(messages: List[Dict[str, Any]]) -> Any:
        """Estimate serialized provider messages with one shared estimator."""
        return TokenCounter.estimator.count_messages(messages)

@dataclass
class TelemetryData:
    timestamp: float = field(default_factory=time.time)
    window_size: int = 8192
    section_tokens: Dict[str, int] = field(default_factory=dict)
    truncated_items: List[str] = field(default_factory=list)
    output_reserved: int = 1200
    bypassed_context_llm: bool = False
    context_llm_latency_ms: float = 0.0

@dataclass(frozen=True)
class PromptSections:
    system_instructions: str
    constraints_text: str
    retrieved_context: str
    memory_context: str
    history_summary: str
    user_prompt: str

class PromptBudget:
    """Manages token allocation, mandatory output reservation, and prioritized truncation enforcing global window limits."""

    def __init__(self, window_size: int = 8192, reserved_output: int = 1200):
        self.window_size = window_size
        self.reserved_output = max(64, min(reserved_output, max(64, window_size - 128)))
        self.last_telemetry: Optional[TelemetryData] = None

    def _truncate_to_tokens(self, text: str, target_tokens: int) -> str:
        if target_tokens <= 0:
            return ""
        if TokenCounter.count_tokens(text) <= target_tokens:
            return text
        suffix = "\n... [truncated]"
        if TokenCounter.count_tokens(suffix) > target_tokens:
            return ""
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if TokenCounter.count_tokens(text[:mid] + suffix) <= target_tokens:
                low = mid
            else:
                high = mid - 1
        if low < len(text):
            return text[:low] + suffix
        return text

    def allocate_context(
        self,
        system_prompt: str,
        task_prompt: str,
        mandatory_constraints: List[str],
        repo_map: str,
        files_context: str,
        history_context: str,
        recent_results: str
    ) -> Dict[str, Any]:
        telemetry = TelemetryData(window_size=self.window_size, output_reserved=self.reserved_output)
        truncated: List[str] = []

        max_allowed_input = max(500, self.window_size - self.reserved_output)

        sys_tokens = TokenCounter.count_tokens(system_prompt)
        constraints_str = "\n".join(mandatory_constraints).strip()
        task_tokens = TokenCounter.count_tokens(task_prompt)
        constraints_tokens = TokenCounter.count_tokens(constraints_str)

        if sys_tokens + task_tokens + constraints_tokens > max_allowed_input:
            raise PromptTooLargeError(
                f"System, task and mandatory constraints ({sys_tokens + task_tokens + constraints_tokens}t) "
                f"exceed available context ({max_allowed_input}t)."
            )
        
        # Priority components to truncate (first is truncated first)
        components = [
            {"name": "repo_map", "text": repo_map, "tokens": TokenCounter.count_tokens(repo_map)},
            {"name": "history_context", "text": history_context, "tokens": TokenCounter.count_tokens(history_context)},
            {"name": "recent_results", "text": recent_results, "tokens": TokenCounter.count_tokens(recent_results)},
            {"name": "files_context", "text": files_context, "tokens": TokenCounter.count_tokens(files_context)}
        ]

        def get_total() -> int:
            return sys_tokens + task_tokens + constraints_tokens + sum(c["tokens"] for c in components)

        # Iterative truncation
        for comp in components:
            excess = get_total() - max_allowed_input
            if excess > 0 and comp["tokens"] > 0:
                cut_tokens = min(comp["tokens"], excess)
                new_tokens = comp["tokens"] - cut_tokens
                comp["text"] = self._truncate_to_tokens(comp["text"], new_tokens)
                comp["tokens"] = TokenCounter.count_tokens(comp["text"])
                truncated.append(comp["name"])

        final_repo = components[0]["text"]
        final_hist = components[1]["text"]
        final_results = components[2]["text"]
        final_files = components[3]["text"]

        total_input_tokens = get_total()
        
        # Hard safety fail
        if total_input_tokens > max_allowed_input:
            raise PromptTooLargeError("Prompt allocation could not satisfy the context window.")

        telemetry.section_tokens = {
            "system": sys_tokens,
            "task": task_tokens,
            "constraints": constraints_tokens,
            "files": components[3]["tokens"],
            "repo": components[0]["tokens"],
            "history": components[1]["tokens"],
            "results": components[2]["tokens"]
        }
        telemetry.truncated_items = truncated
        self.last_telemetry = telemetry

        sections = PromptSections(
            system_instructions=system_prompt,
            constraints_text=constraints_str,
            retrieved_context=f"{final_files}\n\n{final_repo}".strip(),
            memory_context="",
            history_summary=final_hist,
            user_prompt=task_prompt
        )

        return {
            "sections": sections,
            "system_prompt": system_prompt,
            "constraints_text": constraints_str,
            "task_str": constraints_str,
            "user_prompt": task_prompt,
            "repo_map": final_repo,
            "files_context": final_files,
            "history_context": final_hist,
            "recent_results": final_results,
            "total_input_tokens": total_input_tokens,
            "reserved_output_tokens": self.reserved_output,
            "truncated": truncated,
            "telemetry": telemetry
        }
