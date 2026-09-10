from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


path = Path("kitt/core/turn_processor.py")
text = path.read_text(encoding="utf-8")

old = '''        logger.info(
            "latency turn=%s phase=%s duration_ms=%.2f elapsed_ms=%.2f detail=%s",
            turn_id, phase, payload["duration_ms"], payload["elapsed_ms"], payload["detail"],
        )
        self._emit("LatencyRecorded", payload)
        return payload
'''
new = '''        logger.info(
            "latency turn=%s phase=%s duration_ms=%.2f elapsed_ms=%.2f detail=%s",
            turn_id, phase, payload["duration_ms"], payload["elapsed_ms"], payload["detail"],
        )
        callback = getattr(self, "event_callback", None)
        if callback and not getattr(self, "_closed", False):
            try:
                callback("LatencyRecorded", payload)
            except Exception:
                # Observability is strictly fail-open: a broken metrics consumer
                # must never alter tool, approval, cancellation or response flow.
                logger.debug("latency callback failed", exc_info=True)
        return payload
'''
text = replace_once(text, old, new, "fail-open latency callback")

old = '''        self._record_latency(
            turn_id,
            "approval_wait",
            (time.time() - pa.created_at) * 1000,
            detail={"tool": pa.tool_name},
        )

        from kitt.security.mutation_preconditions import validate_preconditions
'''
new = '''        from kitt.security.mutation_preconditions import validate_preconditions
'''
text = replace_once(text, old, new, "remove premature approval metric")

old = '''        if not self.turn_guard.begin(turn_id):
            yield TurnCancelled(reason="Turn cancelled before approved action execution")
            return

        consume_failed = False
'''
new = '''        if not self.turn_guard.begin(turn_id):
            yield TurnCancelled(reason="Turn cancelled before approved action execution")
            return

        pending_created_at = getattr(pa, "created_at", None)
        if isinstance(pending_created_at, (int, float)):
            self._record_latency(
                turn_id,
                "approval_wait",
                max(0.0, (time.time() - pending_created_at) * 1000),
                detail={"tool": str(getattr(pa, "tool_name", ""))},
            )

        consume_failed = False
'''
text = replace_once(text, old, new, "approval metric after cancellation barrier")

path.write_text(text, encoding="utf-8")
