from __future__ import annotations

import json
from typing import Any, List, Optional
from kitt.core.runtime_config import RuntimeConfig
from kitt.domain.entities import ContextPlan


class ToolSurfaceSelector:
    """Select legacy vs compact runtime surface.

    Token measurements are intentionally computed from the real serialized
    definitions rather than fixed constants.
    """

    def __init__(self, config: Optional[RuntimeConfig] = None):
        self.config = config or RuntimeConfig()

    def select_tools(
        self,
        plan: ContextPlan,
        model_capabilities: Optional[Any] = None,
        runtime_mode_override: Optional[str] = None,
    ) -> List[str]:
        if not plan.enabled_tools:
            return []

        mode = runtime_mode_override or self.config.tool_runtime_mode
        if mode not in {"legacy", "safe_runtime", "auto"}:
            raise ValueError(f"Invalid tool runtime mode: {mode}")

        if mode == "legacy" or not self.config.safe_runtime_enabled:
            return list(plan.enabled_tools)

        if mode == "safe_runtime":
            return ["kitt_runtime"]

        supports_tools = True
        if model_capabilities is not None:
            supports_tools = bool(
                getattr(model_capabilities, "supports_tools",
                        getattr(model_capabilities, "supports_native_tools", True))
            )
        return ["kitt_runtime"] if supports_tools else list(plan.enabled_tools)

    @staticmethod
    def measure_definition_tokens(registry, tools: List[str], token_counter) -> int:
        payload = json.dumps(
            registry.get_tool_definitions(tools),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return token_counter.count_tokens(payload)

    @classmethod
    def compare_surfaces(cls, registry, legacy_tools: List[str], token_counter) -> dict:
        legacy = cls.measure_definition_tokens(registry, legacy_tools, token_counter)
        compact = cls.measure_definition_tokens(registry, ["kitt_runtime"], token_counter)
        saved = max(0, legacy - compact)
        return {
            "legacy_tokens": legacy,
            "safe_runtime_tokens": compact,
            "saved_tokens": saved,
            "saved_pct": (saved / legacy * 100.0) if legacy else 0.0,
        }
