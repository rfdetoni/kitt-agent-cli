import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from kitt.context.token_estimator import CalibratedTokenEstimator


class PromptTooLargeError(Exception):
    """Raised when mandatory prompt sections cannot fit the model context window."""


class TokenCounter:
    """Compatibility facade over the single calibrated token estimator."""

    estimator = CalibratedTokenEstimator()

    @staticmethod
    def count_tokens(text: str, num_messages: int = 0) -> int:
        if not text:
            return 0
        overhead = num_messages * 3 if num_messages > 0 else 0
        return TokenCounter.estimator.count_text(text).count + overhead

    @staticmethod
    def count_messages(messages: List[Dict[str, Any]]) -> Any:
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
    """Global context-window allocator with a strict input+output invariant."""

    MIN_INPUT_TOKENS = 128
    MIN_OUTPUT_TOKENS = 64

    def __init__(self, window_size: int = 8192, reserved_output: int = 1200):
        self.window_size = int(window_size)
        if self.window_size < self.MIN_INPUT_TOKENS + self.MIN_OUTPUT_TOKENS:
            raise ValueError(
                "Context window is too small: "
                f"{self.window_size} < {self.MIN_INPUT_TOKENS + self.MIN_OUTPUT_TOKENS}"
            )

        requested_output = max(self.MIN_OUTPUT_TOKENS, int(reserved_output))
        maximum_output = self.window_size - self.MIN_INPUT_TOKENS
        self.reserved_output = min(requested_output, maximum_output)
        self.max_input_tokens = self.window_size - self.reserved_output
        if self.max_input_tokens < self.MIN_INPUT_TOKENS:
            raise ValueError("Context window leaves no safe input budget")
        self.last_telemetry: Optional[TelemetryData] = None

    def _truncate_context_pack(self, text: str, target_tokens: int) -> str | None:
        if not text.startswith("## Context v"):
            return None
        lines = text.splitlines()
        if not lines:
            return ""

        # V2 is JSONL, so removing complete evidence records cannot corrupt the
        # serialization. V1 remains supported during rolling upgrades.
        if any(line == "### Evidence JSONL" for line in lines):
            evidence_idx = lines.index("### Evidence JSONL")
            prefix = lines[: evidence_idx + 1]
            suffix_idx = next(
                (i for i in range(evidence_idx + 1, len(lines)) if lines[i] == "### Missing"),
                len(lines),
            )
            evidence = lines[evidence_idx + 1 : suffix_idx]
            suffix = lines[suffix_idx:]
            for keep in range(len(evidence), -1, -1):
                candidate_lines = prefix + evidence[:keep]
                if keep < len(evidence):
                    candidate_lines.append('{"truncated_evidence":true}')
                candidate_lines.extend(suffix)
                candidate = "\n".join(candidate_lines)
                if TokenCounter.count_tokens(candidate) <= target_tokens:
                    return candidate
            return ""

        # Legacy Context v1 fence representation.
        if "\n[" in text:
            parts = text.split("\n\n[")
            if len(parts) > 1:
                for keep_count in range(len(parts) - 1, 0, -1):
                    rebuilt = "\n\n[".join(parts[:keep_count])
                    missing_idx = text.rfind("\n### Missing")
                    if missing_idx != -1 and "\n### Missing" not in rebuilt:
                        rebuilt_with_missing = rebuilt + text[missing_idx:]
                        if TokenCounter.count_tokens(rebuilt_with_missing) <= target_tokens:
                            return rebuilt_with_missing
                    if TokenCounter.count_tokens(rebuilt) <= target_tokens:
                        return rebuilt
        return None

    def _truncate_to_tokens(self, text: str, target_tokens: int) -> str:
        if target_tokens <= 0:
            return ""
        if TokenCounter.count_tokens(text) <= target_tokens:
            return text

        packed = self._truncate_context_pack(text, target_tokens)
        if packed is not None:
            return packed

        suffix = "\n... [truncated]"
        if TokenCounter.count_tokens(suffix) > target_tokens:
            return ""

        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            cand = text[:mid]
            # Legacy fence safety. V2 context packs do not use fences.
            if cand.count("```") % 2 == 1:
                cand += "\n```"
            if TokenCounter.count_tokens(cand + suffix) <= target_tokens:
                low = mid
            else:
                high = mid - 1

        if low < len(text):
            cand = text[:low]
            nl = cand.rfind("\n")
            if nl > low // 2:
                cand = cand[:nl]
            if cand.count("```") % 2 == 1:
                cand += "\n```"
            return cand + suffix
        return text

    def allocate_context(
        self,
        system_prompt: str,
        task_prompt: str,
        mandatory_constraints: List[str],
        repo_map: str,
        files_context: str,
        history_context: str,
        recent_results: str,
    ) -> Dict[str, Any]:
        telemetry = TelemetryData(
            window_size=self.window_size,
            output_reserved=self.reserved_output,
        )
        truncated: List[str] = []
        max_allowed_input = self.max_input_tokens

        sys_tokens = TokenCounter.count_tokens(system_prompt)
        constraints_str = "\n".join(mandatory_constraints).strip()
        task_tokens = TokenCounter.count_tokens(task_prompt)
        constraints_tokens = TokenCounter.count_tokens(constraints_str)

        mandatory_total = sys_tokens + task_tokens + constraints_tokens
        if mandatory_total > max_allowed_input:
            raise PromptTooLargeError(
                f"System, task and mandatory constraints ({mandatory_total}t) "
                f"exceed available input context ({max_allowed_input}t) after reserving "
                f"{self.reserved_output}t for output."
            )

        components = [
            {"name": "repo_map", "text": repo_map, "tokens": TokenCounter.count_tokens(repo_map)},
            {"name": "history_context", "text": history_context, "tokens": TokenCounter.count_tokens(history_context)},
            {"name": "recent_results", "text": recent_results, "tokens": TokenCounter.count_tokens(recent_results)},
            {"name": "files_context", "text": files_context, "tokens": TokenCounter.count_tokens(files_context)},
        ]

        def get_total() -> int:
            return mandatory_total + sum(c["tokens"] for c in components)

        for comp in components:
            excess = get_total() - max_allowed_input
            if excess <= 0 or comp["tokens"] <= 0:
                continue
            target = max(0, comp["tokens"] - excess)
            comp["text"] = self._truncate_to_tokens(comp["text"], target)
            comp["tokens"] = TokenCounter.count_tokens(comp["text"])
            truncated.append(comp["name"])

        total_input_tokens = get_total()
        if total_input_tokens > max_allowed_input:
            raise PromptTooLargeError(
                "Prompt allocation could not satisfy the context-window invariant: "
                f"input={total_input_tokens}, reserved_output={self.reserved_output}, "
                f"window={self.window_size}."
            )
        if total_input_tokens + self.reserved_output > self.window_size:
            raise AssertionError("PromptBudget invariant violated")

        telemetry.section_tokens = {
            "system": sys_tokens,
            "task": task_tokens,
            "constraints": constraints_tokens,
            "files": components[3]["tokens"],
            "repo": components[0]["tokens"],
            "history": components[1]["tokens"],
            "results": components[2]["tokens"],
        }
        telemetry.truncated_items = truncated
        self.last_telemetry = telemetry

        final_repo = components[0]["text"]
        final_hist = components[1]["text"]
        final_results = components[2]["text"]
        final_files = components[3]["text"]
        sections = PromptSections(
            system_instructions=system_prompt,
            constraints_text=constraints_str,
            retrieved_context=f"{final_files}\n\n{final_repo}".strip(),
            memory_context="",
            history_summary=final_hist,
            user_prompt=task_prompt,
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
            "telemetry": telemetry,
        }
