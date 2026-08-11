import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

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
    """Manages token allocation, mandatory output reservation, and prioritized truncation."""

    def __init__(self, window_size: int = 8192, reserved_output: int = 1200):
        self.window_size = window_size
        self.reserved_output = max(reserved_output, 1200)

        # Budget allocations for 8k window
        if window_size <= 8192:
            self.max_system = 1100
            self.max_task_constraints = 700
            self.max_repomap = 900
            self.max_files = 3000
            self.max_history = 600
            self.max_results = 500
        else: # 12k or larger window: scale files budget primarily
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

        # 1. System + Schemas
        sys_tokens = TokenCounter.count_tokens(system_prompt)
        if sys_tokens > self.max_system:
            system_prompt = system_prompt[:self.max_system * 3]
            sys_tokens = TokenCounter.count_tokens(system_prompt)
            truncated.append("system_prompt")
        telemetry.section_tokens["system"] = sys_tokens

        # 2. Task + Mandatory Constraints (NEVER TRUNCATE MANDATORY CONSTRAINTS)
        constraints_str = "\n".join(mandatory_constraints)
        task_str = f"{task_prompt}\n{constraints_str}"
        task_tokens = TokenCounter.count_tokens(task_str)
        telemetry.section_tokens["task_constraints"] = task_tokens

        # 3. Files Context
        files_tokens = TokenCounter.count_tokens(files_context)
        if files_tokens > self.max_files:
            files_context = files_context[:self.max_files * 3] + "\n... [files truncated to budget]"
            files_tokens = TokenCounter.count_tokens(files_context)
            truncated.append("non_explicit_files")
        telemetry.section_tokens["files"] = files_tokens

        # 4. Repo Map
        repo_tokens = TokenCounter.count_tokens(repo_map)
        if repo_tokens > self.max_repomap:
            repo_map = repo_map[:self.max_repomap * 3] + "\n... [repo map truncated to budget]"
            repo_tokens = TokenCounter.count_tokens(repo_map)
            truncated.append("secondary_repo_map")
        telemetry.section_tokens["repomap"] = repo_tokens

        # 5. History
        hist_tokens = TokenCounter.count_tokens(history_context)
        if hist_tokens > self.max_history:
            history_context = history_context[-self.max_history * 3:]
            hist_tokens = TokenCounter.count_tokens(history_context)
            truncated.append("old_history")
        telemetry.section_tokens["history"] = hist_tokens

        # 6. Recent Results
        results_tokens = TokenCounter.count_tokens(recent_results)
        if results_tokens > self.max_results:
            recent_results = recent_results[-self.max_results * 3:]
            results_tokens = TokenCounter.count_tokens(recent_results)
            truncated.append("old_results")
        telemetry.section_tokens["results"] = results_tokens

        total_input_tokens = (
            sys_tokens + task_tokens + files_tokens + repo_tokens + hist_tokens + results_tokens
        )
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
