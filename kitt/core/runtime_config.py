from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class RuntimeConfig:
    history_enabled: bool = True
    persistence_enabled: bool = True
    privacy_mode: str = "hybrid_redacted"

    artifact_inline_limit: int = 32768
    max_artifact_bytes: int = 8 * 1024 * 1024
    artifact_page_bytes: int = 32768

    max_children: int = 2
    max_child_depth: int = 1
    child_token_budget: int = 2048
    child_timeout_seconds: float = 120.0

    process_timeout_seconds: int = 120
    process_output_bytes: int = 262144
    process_grace_seconds: float = 3.0

    max_read_lines: int = 5000
    max_read_bytes: int = 262144
    max_search_results: int = 200
    max_search_bytes: int = 262144
    max_search_time_ms: int = 3000
    max_tool_calls_per_turn: int = 8

    compaction_keep_recent: int = 6
    compaction_min_tokens: int = 0
    max_compaction_cycles: int = 2

    max_skills_per_prompt: int = 3
    max_skill_body_chars: int = 16000
    max_harness_chars: int = 12000
    max_index_file_bytes: int = 512 * 1024
    max_index_files: int = 20000
    max_index_bytes: int = 256 * 1024 * 1024

    dream_enabled: bool = True
    dream_auto_enabled: bool = False
    dream_auto_commit: bool = False
    dream_min_interval_hours: int = 24
    dream_min_completed_sessions: int = 5
    dream_max_sessions: int = 20
    dream_max_entries: int = 200

    context_window_default: int = 8192
    reserved_output_tokens: int = 1200
    max_tool_output_chars: int = 4000
    context_retrieval_token_ratio: float = 0.25
    max_context_retrieval_tokens: int = 8192

    approval_ttl_seconds: float = 300.0
    max_correction_cycles: int = 2
    max_followup_generation: int = 3
    max_followup_per_turn: int = 2
    switch_workspace_in_memory_only: bool = False

    tool_runtime_mode: str = "auto"
    safe_runtime_enabled: bool = True
    daemon_enabled: bool = True
    daemon_auto_start: bool = True
    daemon_local_fallback: bool = False
    retained_agents_enabled: bool = True
    executable_skills_enabled: bool = True
    scheduler_enabled: bool = True

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        mode = os.getenv("KITT_TOOL_RUNTIME_MODE", "auto").strip().lower()
        if mode not in {"legacy", "safe_runtime", "auto"}:
            mode = "auto"
        return cls(
            safe_runtime_enabled=_env_bool("KITT_SAFE_RUNTIME", True),
            daemon_enabled=_env_bool("KITT_DAEMON", True),
            daemon_auto_start=_env_bool("KITT_DAEMON_AUTO_START", True),
            daemon_local_fallback=_env_bool("KITT_DAEMON_LOCAL_FALLBACK", False),
            retained_agents_enabled=_env_bool("KITT_RETAINED_AGENTS", True),
            executable_skills_enabled=_env_bool("KITT_EXECUTABLE_SKILLS", True),
            scheduler_enabled=_env_bool("KITT_SCHEDULER", True),
            tool_runtime_mode=mode,
        )

    @property
    def ephemeral(self) -> bool:
        return not (self.history_enabled and self.persistence_enabled)
