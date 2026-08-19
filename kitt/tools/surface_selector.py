from __future__ import annotations

from typing import Any, List, Optional
from kitt.core.runtime_config import RuntimeConfig
from kitt.domain.entities import ContextPlan


class ToolSurfaceSelector:
    """Selects the active tool surface (legacy vs safe_runtime vs auto) and estimates token overhead."""

    def __init__(self, config: Optional[RuntimeConfig] = None):
        self.config = config or RuntimeConfig()

    def select_tools(
        self,
        plan: ContextPlan,
        model_capabilities: Optional[Any] = None,
        runtime_mode_override: Optional[str] = None,
    ) -> List[str]:
        """Determine the set of tool names to expose to the LLM for a given turn."""
        if not plan.enabled_tools:
            return []

        mode = runtime_mode_override or getattr(self.config, "tool_runtime_mode", "auto")

        if mode == "safe_runtime":
            return ["kitt_runtime"]

        if mode == "legacy":
            return plan.enabled_tools

        # Auto mode: if safe_runtime is enabled and provider/model supports tool calling, use safe_runtime
        if getattr(self.config, "safe_runtime_enabled", False):
            if model_capabilities is None or getattr(model_capabilities, "supports_tools", True):
                return ["kitt_runtime"]

        return plan.enabled_tools

    @staticmethod
    def estimate_legacy_tokens(tools: List[str]) -> int:
        """Empirically estimates token footprint of legacy tool definitions (~120 tokens per tool)."""
        return len(tools) * 120 + 80

    @staticmethod
    def estimate_safe_runtime_tokens() -> int:
        """Empirically estimates token footprint of compact safe runtime tool (~110 tokens total)."""
        return 110
