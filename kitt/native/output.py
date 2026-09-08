from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import NativeCodeEngine


def _estimated_tokens(value: str) -> int:
    return (len(value.encode("utf-8")) + 3) // 4


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
    capture_truncated: bool = False
    raw_total_bytes: int | None = None
    raw_estimated_tokens: int = 0
    output_estimated_tokens: int = 0
    tokens_saved: int = 0


class OutputOptimizer:
    def __init__(self, engine: NativeCodeEngine):
        self.engine = engine

    def optimize(
        self,
        argv: list[str],
        stdout: str,
        stderr: str,
        returncode: int,
        artifact_store: Any | None = None,
        workspace_id: str = "",
        conversation_id: str = "",
        turn_id: str = "",
        *,
        capture_truncated: bool = False,
        raw_total_bytes: int | None = None,
        token_budget: int = 1200,
    ) -> OptimizedOutput:
        token_budget = max(64, min(int(token_budget or 1200), 32_000))
        raw = stdout if not stderr else stderr if not stdout else stdout + "\n" + stderr
        try:
            result = self.engine.compress_output(
                argv, stdout, stderr, returncode, token_budget=token_budget
            )
        except TypeError:
            result = self.engine.compress_output(argv, stdout, stderr, returncode)
        candidate = str(result.get("output", raw))
        artifact_id = None
        if result.get("changed") and raw and artifact_store is not None:
            try:
                artifact = artifact_store.put(
                    workspace_id,
                    raw,
                    "TOOL_OUTPUT_RAW",
                    f"Captured {'truncated ' if capture_truncated else ''}output for {' '.join(argv[:3])}",
                    conversation_id,
                    turn_id,
                    metadata={
                        "family": result.get("family"),
                        "sha256": result.get("raw_sha256"),
                        "captured_bytes": len(raw.encode("utf-8")),
                        "raw_total_bytes": raw_total_bytes,
                        "capture_truncated": bool(capture_truncated),
                        "token_budget": token_budget,
                    },
                )
                artifact_id = getattr(artifact, "id", None)
            except Exception:
                artifact_id = None

        output = candidate
        if artifact_id:
            footer = (
                f"\n[KITT raw capture artifact: {artifact_id}; "
                f"truncated={'true' if capture_truncated else 'false'}]"
            )
            if len((candidate + footer).encode("utf-8")) < len(raw.encode("utf-8")):
                output = candidate + footer

        changed = len(output.encode("utf-8")) < len(raw.encode("utf-8"))
        if not changed:
            output = raw

        raw_tokens = _estimated_tokens(raw)
        output_tokens = _estimated_tokens(output)
        return OptimizedOutput(
            output=output,
            changed=changed,
            family=str(result.get("family", "generic")),
            raw_bytes=len(raw.encode("utf-8")),
            output_bytes=len(output.encode("utf-8")),
            omitted_lines=int(result.get("omitted_lines", 0)) if changed else 0,
            raw_sha256=str(result.get("raw_sha256", "")),
            raw_artifact_id=artifact_id,
            capture_truncated=bool(capture_truncated),
            raw_total_bytes=raw_total_bytes,
            raw_estimated_tokens=raw_tokens,
            output_estimated_tokens=output_tokens,
            tokens_saved=max(0, raw_tokens - output_tokens),
        )
