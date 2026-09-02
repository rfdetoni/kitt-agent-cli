"""Read-only client for KITT Control Center configuration overlay.

Target: kitt/settings/control_center.py
No external dependencies. Never loads secrets; only user-approved configuration
values stored in the local Control Center override file.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

_SCHEMA_VERSION = 1
_MAX_BYTES = 2 * 1024 * 1024


def config_root() -> Path:
    if os.name == "nt":
        base = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "kitt"


def overlay_path() -> Path:
    explicit = os.getenv("KITT_CONTROL_CENTER_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    return config_root() / "control-center" / "overrides.json"


def load_overlay(path: Path | None = None) -> dict[str, Any]:
    target = path or overlay_path()
    try:
        stat = target.stat()
    except FileNotFoundError:
        return {"schema_version": _SCHEMA_VERSION, "revision": 0, "components": {}}
    except OSError:
        return {"schema_version": _SCHEMA_VERSION, "revision": 0, "components": {}}
    if stat.st_size > _MAX_BYTES:
        raise ValueError("KITT Control Center overlay exceeds 2 MiB")
    raw = target.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("Unsupported KITT Control Center overlay schema")
    components = data.get("components")
    if not isinstance(components, dict):
        raise ValueError("KITT Control Center overlay components must be an object")
    return data


def section(section_id: str, *, overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    data = overlay if overlay is not None else load_overlay()
    value = data.get("components", {}).get(section_id, {})
    return dict(value) if isinstance(value, dict) else {}


def merged_sections(section_ids: Iterable[str]) -> dict[str, Any]:
    data = load_overlay()
    result: dict[str, Any] = {}
    for section_id in section_ids:
        result.update(section(section_id, overlay=data))
    return result


def runtime_overrides(allowed_fields: set[str]) -> dict[str, Any]:
    """Return Agent RuntimeConfig-compatible overrides only.

    Unknown keys are ignored here because the Control Center backend is the
    validation authority; keeping a local allowlist protects older Agent builds
    from newer catalogs.
    """
    values = merged_sections(("agent.runtime", "agent.context", "agent.security"))
    return {key: value for key, value in values.items() if key in allowed_fields}


FORBIDDEN_ENV_VARS: frozenset[str] = frozenset({
    "PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "NODE_OPTIONS",
    "RUBYOPT",
    "PERL5OPT",
    "SHELL",
})


class SettingsDescriptorProvider:
    """Produces canonical Control Center descriptors from Agent runtime config."""

    @classmethod
    def get_sections(cls) -> list[dict[str, Any]]:
        from kitt.core.runtime_config import RuntimeConfig
        default_config = RuntimeConfig()
        
        runtime_fields = [
            {"key": "history_enabled", "label": "History", "type": "boolean", "default": default_config.history_enabled, "apply_mode": "component_restart"},
            {"key": "persistence_enabled", "label": "Persistence", "type": "boolean", "default": default_config.persistence_enabled, "apply_mode": "component_restart"},
            {"key": "privacy_mode", "label": "Privacy mode", "type": "enum", "default": default_config.privacy_mode, "options": ["hybrid_redacted", "local_only", "ephemeral"], "apply_mode": "component_restart"},
            {"key": "max_tool_calls_per_turn", "label": "Tool calls / turn", "type": "integer", "default": default_config.max_tool_calls_per_turn, "minimum": 1, "maximum": 64, "apply_mode": "component_restart"},
            {"key": "process_timeout_seconds", "label": "Process timeout (s)", "type": "integer", "default": default_config.process_timeout_seconds, "minimum": 1, "maximum": 3600, "apply_mode": "component_restart"},
            {"key": "safe_runtime_enabled", "label": "Safe runtime", "type": "boolean", "default": default_config.safe_runtime_enabled, "apply_mode": "component_restart"},
            {"key": "daemon_enabled", "label": "Agent daemon", "type": "boolean", "default": default_config.daemon_enabled, "apply_mode": "component_restart"},
            {"key": "daemon_auto_start", "label": "Daemon auto-start", "type": "boolean", "default": default_config.daemon_auto_start, "apply_mode": "component_restart"},
            {"key": "scheduler_enabled", "label": "Scheduler", "type": "boolean", "default": default_config.scheduler_enabled, "apply_mode": "component_restart"},
            {"key": "executable_skills_enabled", "label": "Executable skills", "type": "boolean", "default": default_config.executable_skills_enabled, "apply_mode": "component_restart"},
            {"key": "max_children", "label": "Max child agents", "type": "integer", "default": default_config.max_children, "minimum": 0, "maximum": 16, "apply_mode": "component_restart"},
            {"key": "max_child_depth", "label": "Max child depth", "type": "integer", "default": default_config.max_child_depth, "minimum": 0, "maximum": 5, "apply_mode": "component_restart"},
            {"key": "child_token_budget", "label": "Child token budget", "type": "integer", "default": default_config.child_token_budget, "minimum": 256, "maximum": 65536, "apply_mode": "component_restart"},
            {"key": "child_timeout_seconds", "label": "Child timeout (s)", "type": "number", "default": default_config.child_timeout_seconds, "minimum": 1.0, "maximum": 3600.0, "apply_mode": "component_restart"},
            {"key": "process_output_bytes", "label": "Max process output bytes", "type": "integer", "default": default_config.process_output_bytes, "minimum": 1024, "maximum": 16777216, "apply_mode": "component_restart"},
            {"key": "process_grace_seconds", "label": "Process grace period (s)", "type": "number", "default": default_config.process_grace_seconds, "minimum": 0.5, "maximum": 60.0, "apply_mode": "component_restart"},
            {"key": "tool_runtime_mode", "label": "Tool runtime mode", "type": "enum", "default": default_config.tool_runtime_mode, "options": ["auto", "native", "restricted"], "apply_mode": "component_restart"},
            {"key": "daemon_local_fallback", "label": "Daemon local fallback", "type": "boolean", "default": default_config.daemon_local_fallback, "apply_mode": "component_restart"},
            {"key": "retained_agents_enabled", "label": "Retained agents", "type": "boolean", "default": default_config.retained_agents_enabled, "apply_mode": "component_restart"},
        ]

        context_fields = [
            {"key": "context_window_default", "label": "Default context window", "type": "integer", "default": default_config.context_window_default, "minimum": 2048, "maximum": 1048576, "apply_mode": "component_restart"},
            {"key": "reserved_output_tokens", "label": "Reserved output tokens", "type": "integer", "default": default_config.reserved_output_tokens, "minimum": 128, "maximum": 32768, "apply_mode": "component_restart"},
            {"key": "max_tool_output_chars", "label": "Max tool output chars", "type": "integer", "default": default_config.max_tool_output_chars, "minimum": 512, "maximum": 100000, "apply_mode": "component_restart"},
            {"key": "context_retrieval_token_ratio", "label": "Retrieval token ratio", "type": "number", "default": default_config.context_retrieval_token_ratio, "minimum": 0.05, "maximum": 0.8, "apply_mode": "component_restart"},
            {"key": "max_context_retrieval_tokens", "label": "Max retrieval tokens", "type": "integer", "default": default_config.max_context_retrieval_tokens, "minimum": 512, "maximum": 131072, "apply_mode": "component_restart"},
            {"key": "compaction_keep_recent", "label": "Keep recent turns", "type": "integer", "default": default_config.compaction_keep_recent, "minimum": 1, "maximum": 50, "apply_mode": "component_restart"},
            {"key": "compaction_min_tokens", "label": "Compaction min tokens", "type": "integer", "default": default_config.compaction_min_tokens, "minimum": 0, "maximum": 65536, "apply_mode": "component_restart"},
            {"key": "max_compaction_cycles", "label": "Compaction cycles", "type": "integer", "default": default_config.max_compaction_cycles, "minimum": 0, "maximum": 10, "apply_mode": "component_restart"},
            {"key": "max_skills_per_prompt", "label": "Max skills per prompt", "type": "integer", "default": default_config.max_skills_per_prompt, "minimum": 1, "maximum": 20, "apply_mode": "component_restart"},
            {"key": "max_skill_body_chars", "label": "Max skill body chars", "type": "integer", "default": default_config.max_skill_body_chars, "minimum": 1000, "maximum": 100000, "apply_mode": "component_restart"},
            {"key": "max_harness_chars", "label": "Max harness chars", "type": "integer", "default": default_config.max_harness_chars, "minimum": 1000, "maximum": 100000, "apply_mode": "component_restart"},
            {"key": "max_artifact_bytes", "label": "Max artifact bytes", "type": "integer", "default": default_config.max_artifact_bytes, "minimum": 1024, "maximum": 268435456, "apply_mode": "component_restart"},
            {"key": "artifact_inline_limit", "label": "Artifact inline limit", "type": "integer", "default": default_config.artifact_inline_limit, "minimum": 256, "maximum": 16777216, "apply_mode": "component_restart"},
            {"key": "artifact_page_bytes", "label": "Artifact page bytes", "type": "integer", "default": default_config.artifact_page_bytes, "minimum": 256, "maximum": 16777216, "apply_mode": "component_restart"},
            {"key": "max_index_file_bytes", "label": "Max index file bytes", "type": "integer", "default": default_config.max_index_file_bytes, "minimum": 1024, "maximum": 16777216, "apply_mode": "component_restart"},
            {"key": "max_index_files", "label": "Max index files", "type": "integer", "default": default_config.max_index_files, "minimum": 100, "maximum": 500000, "apply_mode": "component_restart"},
            {"key": "max_index_bytes", "label": "Max index bytes", "type": "integer", "default": default_config.max_index_bytes, "minimum": 1048576, "maximum": 1073741824, "apply_mode": "component_restart"},
            {"key": "dream_enabled", "label": "Dreaming mode", "type": "boolean", "default": default_config.dream_enabled, "apply_mode": "component_restart"},
            {"key": "dream_auto_enabled", "label": "Automatic dreaming", "type": "boolean", "default": default_config.dream_auto_enabled, "apply_mode": "component_restart"},
            {"key": "dream_auto_commit", "label": "Dream auto commit", "type": "boolean", "default": default_config.dream_auto_commit, "apply_mode": "component_restart"},
            {"key": "dream_min_interval_hours", "label": "Dream min interval (h)", "type": "integer", "default": default_config.dream_min_interval_hours, "minimum": 1, "maximum": 720, "apply_mode": "component_restart"},
            {"key": "dream_min_completed_sessions", "label": "Dream min sessions", "type": "integer", "default": default_config.dream_min_completed_sessions, "minimum": 1, "maximum": 100, "apply_mode": "component_restart"},
            {"key": "dream_max_sessions", "label": "Dream max sessions", "type": "integer", "default": default_config.dream_max_sessions, "minimum": 1, "maximum": 1000, "apply_mode": "component_restart"},
            {"key": "dream_max_entries", "label": "Dream max entries", "type": "integer", "default": default_config.dream_max_entries, "minimum": 10, "maximum": 10000, "apply_mode": "component_restart"},
            {"key": "max_correction_cycles", "label": "Max correction cycles", "type": "integer", "default": default_config.max_correction_cycles, "minimum": 0, "maximum": 10, "apply_mode": "component_restart"},
            {"key": "max_followup_generation", "label": "Max followup generation", "type": "integer", "default": default_config.max_followup_generation, "minimum": 0, "maximum": 10, "apply_mode": "component_restart"},
            {"key": "max_followup_per_turn", "label": "Max followup per turn", "type": "integer", "default": default_config.max_followup_per_turn, "minimum": 0, "maximum": 10, "apply_mode": "component_restart"},
        ]

        security_fields = [
            {"key": "approval_ttl_seconds", "label": "Approval TTL (s)", "type": "number", "default": default_config.approval_ttl_seconds, "minimum": 15, "maximum": 3600, "apply_mode": "component_restart"},
            {"key": "max_read_lines", "label": "Max read lines", "type": "integer", "default": default_config.max_read_lines, "minimum": 100, "maximum": 100000, "apply_mode": "component_restart"},
            {"key": "max_read_bytes", "label": "Max read bytes", "type": "integer", "default": default_config.max_read_bytes, "minimum": 4096, "maximum": 16777216, "apply_mode": "component_restart"},
            {"key": "max_search_results", "label": "Max search results", "type": "integer", "default": default_config.max_search_results, "minimum": 10, "maximum": 5000, "apply_mode": "component_restart"},
            {"key": "max_search_bytes", "label": "Max search bytes", "type": "integer", "default": default_config.max_search_bytes, "minimum": 1024, "maximum": 16777216, "apply_mode": "component_restart"},
            {"key": "max_search_time_ms", "label": "Search timeout (ms)", "type": "integer", "default": default_config.max_search_time_ms, "minimum": 100, "maximum": 60000, "apply_mode": "component_restart"},
            {"key": "switch_workspace_in_memory_only", "label": "Switch workspace in memory only", "type": "boolean", "default": default_config.switch_workspace_in_memory_only, "apply_mode": "component_restart"},
        ]

        return [
            {
                "id": "agent.runtime",
                "component": "kitt-agent-cli",
                "title": "Agent / Runtime",
                "description": "Execução, persistência e daemon do agente.",
                "version": 1,
                "fields": runtime_fields,
            },
            {
                "id": "agent.context",
                "component": "kitt-agent-cli",
                "title": "Agent / Context & Compaction",
                "description": "Orçamento de contexto para modelos pequenos e repositórios grandes.",
                "version": 1,
                "fields": context_fields,
            },
            {
                "id": "agent.security",
                "component": "kitt-agent-cli",
                "title": "Agent / Security",
                "description": "Approval, limites e execução segura.",
                "version": 1,
                "fields": security_fields,
            },
        ]

    @classmethod
    def is_safe_env_var(cls, var_name: str) -> bool:
        """Check if an environment variable is safe from process control hijacking."""
        normalized = var_name.strip().upper()
        return bool(normalized and normalized not in FORBIDDEN_ENV_VARS and not normalized.startswith("LD_") and not normalized.startswith("DYLD_"))

