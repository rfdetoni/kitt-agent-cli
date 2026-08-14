from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuntimeConfig:
    """Central runtime configuration, propagated to every component.

    ``history_enabled`` controls the SQLite conversation history. When
    ``False``, an in-memory database is used and no history entry is written.

    ``persistence_enabled`` controls *all* content persistence (skills
    defaults, active-skill list, memory files, artifact blobs, index cache).
    When ``False``, no constructor creates ``.kitt`` and every store keeps its
    state ephemeral (in-memory or a temporary directory removed on close).
    """

    history_enabled: bool = True
    persistence_enabled: bool = True
    privacy_mode: str = "hybrid_redacted"

    # Artifacts
    artifact_inline_limit: int = 32768
    max_artifact_bytes: int = 8 * 1024 * 1024
    artifact_page_bytes: int = 32768

    # Children
    max_children: int = 2
    max_child_depth: int = 1
    child_token_budget: int = 2048
    child_timeout_seconds: float = 120.0

    # Processes
    process_timeout_seconds: int = 120
    process_output_bytes: int = 262144
    process_grace_seconds: float = 3.0

    # Tools / reads / search
    max_read_lines: int = 5000
    max_read_bytes: int = 262144
    max_search_results: int = 200
    max_search_bytes: int = 262144
    max_search_time_ms: int = 3000
    max_tool_calls_per_turn: int = 8

    # Compaction
    compaction_keep_recent: int = 6
    compaction_min_tokens: int = 0
    max_compaction_cycles: int = 2

    # Skills / harness / index
    max_skills_per_prompt: int = 3
    max_skill_body_chars: int = 16000
    max_harness_chars: int = 12000
    max_index_file_bytes: int = 512 * 1024
    max_index_files: int = 20000
    max_index_bytes: int = 256 * 1024 * 1024

    # Context / budget
    context_window_default: int = 8192
    reserved_output_tokens: int = 1200
    max_tool_output_chars: int = 4000

    # Approval
    approval_ttl_seconds: float = 300.0

    # Correction cycles after failed validation
    max_correction_cycles: int = 2

    # Queue
    max_followup_generation: int = 3
    max_followup_per_turn: int = 2

    # Switch workspace
    switch_workspace_in_memory_only: bool = False

    @property
    def ephemeral(self) -> bool:
        return not (self.history_enabled and self.persistence_enabled)
