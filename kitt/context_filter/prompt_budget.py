import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

class PromptTooLargeError(Exception):
    """Raised when task prompt and mandatory constraints exceed the context window."""

class TokenCounter:
    """Conservative two-level token counter."""

    @staticmethod
    def count_tokens(text: str) -> int:
        if not text:
            return 0
        # Conservative multi-language token estimator (average 3.2 chars per token for code/pt/en)
        char_count = len(text)
        word_count = len(text.split())
        return max(int(char_count / 3.2), int(word_count * 1.3))

@dataclass
class TelemetryData:
    timestamp: float = field(default_factory=time.time)
    window_size: int = 8192
    section_tokens: Dict[str, int] = field(default_factory=dict)
    truncated_items: List[str] = field(default_factory=list)
    output_reserved: int = 1200
    bypassed_context_llm: bool = False
    context_llm_latency_ms: float = 0.0

class PromptBudget:
    """Manages token allocation, mandatory output reservation, and prioritized truncation enforcing global window limits."""

    def __init__(self, window_size: int = 8192, reserved_output: int = 1200):
        self.window_size = window_size
        self.reserved_output = max(reserved_output, 1200)

        # Budget allocations per section
        if window_size <= 8192:
            self.max_system = 1100
            self.max_task_constraints = 700
            self.max_repomap = 900
            self.max_files = 3000
            self.max_history = 600
            self.max_results = 500
        else:
            self.max_system = 1100
            self.max_task_constraints = 700
            self.max_repomap = 1200
            self.max_files = 6000
            self.max_history = 1000
            self.max_results = 800

        self.last_telemetry: Optional[TelemetryData] = None

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

        # 1. System + Schemas
        sys_tokens = TokenCounter.count_tokens(system_prompt)
        if sys_tokens > self.max_system:
            system_prompt = system_prompt[:self.max_system * 3]
            sys_tokens = TokenCounter.count_tokens(system_prompt)
            truncated.append("system_prompt")

        # 2. Task + Mandatory Constraints (NEVER TRUNCATE MANDATORY CONSTRAINTS)
        constraints_str = "\n".join(mandatory_constraints)
        task_str = f"{task_prompt}\n{constraints_str}".strip()
        task_tokens = TokenCounter.count_tokens(task_str)

        # 3. Files Context
        files_tokens = TokenCounter.count_tokens(files_context)
        if files_tokens > self.max_files:
            files_context = files_context[:self.max_files * 3] + "\n... [files truncated to budget]"
            files_tokens = TokenCounter.count_tokens(files_context)
            truncated.append("non_explicit_files")

        # 4. Repo Map
        repo_tokens = TokenCounter.count_tokens(repo_map)
        if repo_tokens > self.max_repomap:
            repo_map = repo_map[:self.max_repomap * 3] + "\n... [repo map truncated to budget]"
            repo_tokens = TokenCounter.count_tokens(repo_map)
            truncated.append("secondary_repo_map")

        # 5. History
        hist_tokens = TokenCounter.count_tokens(history_context)
        if hist_tokens > self.max_history:
            history_context = history_context[-self.max_history * 3:]
            hist_tokens = TokenCounter.count_tokens(history_context)
            truncated.append("old_history")

        # 6. Recent Results
        results_tokens = TokenCounter.count_tokens(recent_results)
        if results_tokens > self.max_results:
            recent_results = recent_results[-self.max_results * 3:]
            results_tokens = TokenCounter.count_tokens(recent_results)
            truncated.append("old_results")

        total_input_tokens = (
            sys_tokens + task_tokens + files_tokens + repo_tokens + hist_tokens + results_tokens
        )

        # MANDATORY GLOBAL INVARIANT ENFORCEMENT:
        # total_input_tokens + reserved_output <= window_size
        if total_input_tokens > max_allowed_input:
            excess = total_input_tokens - max_allowed_input

            # Prune Step 1: Recent Results
            if results_tokens > 0:
                cut = min(results_tokens, excess)
                recent_results = "" if cut == results_tokens else recent_results[:max(0, len(recent_results) - cut * 3)]
                results_tokens = TokenCounter.count_tokens(recent_results)
                excess = (sys_tokens + task_tokens + files_tokens + repo_tokens + hist_tokens + results_tokens) - max_allowed_input
                truncated.append("pruned_results_to_window")

            # Prune Step 2: History
            if excess > 0 and hist_tokens > 0:
                cut = min(hist_tokens, excess)
                history_context = "" if cut == hist_tokens else history_context[:max(0, len(history_context) - cut * 3)]
                hist_tokens = TokenCounter.count_tokens(history_context)
                excess = (sys_tokens + task_tokens + files_tokens + repo_tokens + hist_tokens + results_tokens) - max_allowed_input
                truncated.append("pruned_history_to_window")

            # Prune Step 3: Repo Map
            if excess > 0 and repo_tokens > 0:
                cut = min(repo_tokens, excess)
                repo_map = "" if cut == repo_tokens else repo_map[:max(0, len(repo_map) - cut * 3)]
                repo_tokens = TokenCounter.count_tokens(repo_map)
                excess = (sys_tokens + task_tokens + files_tokens + repo_tokens + hist_tokens + results_tokens) - max_allowed_input
                truncated.append("pruned_repomap_to_window")

            # Prune Step 4: Files Context
            if excess > 0 and files_tokens > 0:
                cut = min(files_tokens, excess)
                files_context = "" if cut == files_tokens else files_context[:max(0, len(files_context) - cut * 3)]
                files_tokens = TokenCounter.count_tokens(files_context)
                excess = (sys_tokens + task_tokens + files_tokens + repo_tokens + hist_tokens + results_tokens) - max_allowed_input
                truncated.append("pruned_files_to_window")

            # Prune Step 5: System Prompt down to base
            if excess > 0 and sys_tokens > 200:
                sys_tokens = 200
                system_prompt = system_prompt[:600]
                excess = (sys_tokens + task_tokens + files_tokens + repo_tokens + hist_tokens + results_tokens) - max_allowed_input
                truncated.append("pruned_system_to_window")

            total_input_tokens = sys_tokens + task_tokens + files_tokens + repo_tokens + hist_tokens + results_tokens

            if total_input_tokens > max_allowed_input:
                raise PromptTooLargeError(
                    f"Prompt and mandatory constraints ({total_input_tokens} tokens) exceed max allowed input ({max_allowed_input} tokens) for context window {self.window_size}."
                )

        telemetry.section_tokens["system"] = sys_tokens
        telemetry.section_tokens["task_constraints"] = task_tokens
        telemetry.section_tokens["files"] = files_tokens
        telemetry.section_tokens["repomap"] = repo_tokens
        telemetry.section_tokens["history"] = hist_tokens
        telemetry.section_tokens["results"] = results_tokens
        telemetry.section_tokens["total_input"] = total_input_tokens
        telemetry.truncated_items = truncated
        self.last_telemetry = telemetry

        return {
            "system_prompt": system_prompt,
            "task_str": task_str,
            "repo_map": repo_map,
            "files_context": files_context,
            "history_context": history_context,
            "recent_results": recent_results,
            "total_input_tokens": total_input_tokens,
            "reserved_output_tokens": self.reserved_output,
            "telemetry": telemetry
        }
