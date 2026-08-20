from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import NativeCodeEngine


@dataclass(frozen=True)
class OptimizedOutput:
    output: str
    changed: bool
    family: str
    raw_bytes: int
    output_bytes: int
    omitted_lines: int
    raw_sha256: str
    raw_artifact_id: str | None = None


class OutputOptimizer:
    def __init__(self, engine: NativeCodeEngine):
        self.engine = engine

    def optimize(self, argv: list[str], stdout: str, stderr: str, returncode: int,
                 artifact_store: Any | None = None, workspace_id: str = "",
                 conversation_id: str = "", turn_id: str = "") -> OptimizedOutput:
        raw = stdout if not stderr else stderr if not stdout else stdout + "\n" + stderr
        result = self.engine.compress_output(argv, stdout, stderr, returncode)
        artifact_id = None
        if result.get("changed") and raw and artifact_store is not None:
            try:
                artifact = artifact_store.put(
                    workspace_id,
                    raw,
                    "TOOL_OUTPUT_RAW",
                    f"Uncompressed output for {' '.join(argv[:3])}",
                    conversation_id,
                    turn_id,
                    metadata={
                        "family": result.get("family"),
                        "sha256": result.get("raw_sha256"),
                        "raw_bytes": result.get("raw_bytes"),
                    },
                )
                artifact_id = getattr(artifact, "id", None)
            except Exception:
                artifact_id = None
        output = str(result.get("output", raw))
        if artifact_id:
            output += f"\n[KITT raw output artifact: {artifact_id}]"
        return OptimizedOutput(
            output=output,
            changed=bool(result.get("changed")),
            family=str(result.get("family", "generic")),
            raw_bytes=int(result.get("raw_bytes", len(raw.encode()))),
            output_bytes=int(result.get("output_bytes", len(output.encode()))),
            omitted_lines=int(result.get("omitted_lines", 0)),
            raw_sha256=str(result.get("raw_sha256", "")),
            raw_artifact_id=artifact_id,
        )
