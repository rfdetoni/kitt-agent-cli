from __future__ import annotations

import json
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.context_filter.prompt_budget import TokenCounter
from kitt.tools.surface_selector import ToolSurfaceSelector


LEGACY = [
    "list_files", "search", "read_file", "repository_map", "python_compute",
    "write_file", "apply_patch", "run_command", "git_status", "git_diff",
    "artifact_store", "artifact_read", "artifact_list", "goal_create",
    "goal_add_gate", "child_spawn",
]


def main():
    runtime = KittRuntime.build(".")
    try:
        result = ToolSurfaceSelector.compare_surfaces(
            runtime.registry, LEGACY, TokenCounter
        )
        result["sha_note"] = "record git SHA externally with benchmark output"
        print(json.dumps(result, indent=2))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
