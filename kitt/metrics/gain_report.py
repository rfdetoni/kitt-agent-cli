"""RTK-style token savings reports for K.I.T.T. native/structured tools."""
from __future__ import annotations

import json
from typing import Any, Iterable


def estimated_tokens_from_bytes(byte_count: int) -> int:
    """Match RTK's intentionally simple bytes/4 estimation."""
    return max(0, int(byte_count or 0) + 3) // 4


def format_tokens(value: int | float) -> str:
    value = max(0.0, float(value or 0))
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(int(value))


def format_duration_ms(value: int | float) -> str:
    value = max(0.0, float(value or 0))
    if value >= 60_000:
        return f"{value / 60_000:.1f}m"
    if value >= 1_000:
        return f"{value / 1_000:.1f}s"
    return f"{value:.0f}ms"


def saving_pct(raw_tokens: int, saved_tokens: int) -> float:
    if raw_tokens <= 0:
        return 0.0
    return max(0.0, min(100.0, (saved_tokens / raw_tokens) * 100.0))


def efficiency_bar(percent: float, width: int = 24) -> str:
    percent = max(0.0, min(100.0, float(percent)))
    filled = round((percent / 100.0) * width)
    return "█" * filled + "░" * (width - filled)


def _summary_payload(summary: dict[str, Any]) -> dict[str, Any]:
    raw = int(summary.get("raw", 0) or 0)
    output = int(summary.get("output", 0) or 0)
    saved = int(summary.get("saved", 0) or 0)
    calls = int(summary.get("count", 0) or 0)
    duration = float(summary.get("duration", 0.0) or 0.0)
    pct = saving_pct(raw, saved)
    return {
        "calls": calls,
        "raw_tokens": raw,
        "returned_tokens": output,
        "saved_tokens": saved,
        "saved_percent": round(pct, 1),
        "duration_ms": duration,
        "avg_duration_ms": round(duration / max(1, calls), 1),
    }


def _tool_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        raw = int(row.get("raw", 0) or 0)
        output = int(row.get("output", 0) or 0)
        saved = int(row.get("saved", 0) or 0)
        normalized.append({
            "tool": str(row.get("tool", "unknown")),
            "calls": int(row.get("count", 0) or 0),
            "raw_tokens": raw,
            "returned_tokens": output,
            "saved_tokens": saved,
            "saved_percent": round(saving_pct(raw, saved), 1),
            "duration_ms": float(row.get("duration", 0.0) or 0.0),
        })
    return normalized


def gain_json(repository, conversation_id: str | None = None) -> str:
    payload = {
        "scope": "conversation" if conversation_id else "all",
        "estimation": "utf8_bytes_div_4",
        "summary": _summary_payload(repository.get_gain_summary(conversation_id)),
        "tools": _tool_rows(repository.get_gain_by_tool(conversation_id, limit=50)),
        "daily": repository.get_gain_daily(conversation_id, days=30),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def render_gain_dashboard(
    repository,
    conversation_id: str | None = None,
    *,
    include_tools: bool = True,
) -> str:
    summary = _summary_payload(repository.get_gain_summary(conversation_id))
    scope = "Current Conversation" if conversation_id else "All Persisted Sessions"
    pct = summary["saved_percent"]
    lines = [
        f"KITT Token Savings — {scope}",
        "═" * 52,
        f"Tool calls:       {summary['calls']:>10}",
        f"Raw estimate:     {format_tokens(summary['raw_tokens']):>10} tokens",
        f"Returned:         {format_tokens(summary['returned_tokens']):>10} tokens",
        f"Saved:            {format_tokens(summary['saved_tokens']):>10} tokens ({pct:.1f}%)",
        f"Tool time:        {format_duration_ms(summary['duration_ms']):>10}  "
        f"(avg {format_duration_ms(summary['avg_duration_ms'])})",
        f"Efficiency:       {efficiency_bar(pct)} {pct:.1f}%",
    ]
    if include_tools:
        rows = _tool_rows(repository.get_gain_by_tool(conversation_id, limit=10))
        if rows:
            lines += ["", "By tool", "─" * 52]
            for row in rows:
                lines.append(
                    f"{row['tool'][:18]:<18} "
                    f"{format_tokens(row['raw_tokens']):>7} → "
                    f"{format_tokens(row['returned_tokens']):>7}  "
                    f"{format_tokens(row['saved_tokens']):>7} saved "
                    f"{row['saved_percent']:>5.1f}%"
                )
    lines += [
        "",
        "Estimate: UTF-8 bytes/4. Measures KITT tool-output/context",
        "avoidance; it is not an API billing/tokenizer report.",
        "Use: /gain tools | history | daily | graph | all | json",
    ]
    return "\n".join(lines)


def render_gain_tools(repository, conversation_id: str | None = None) -> str:
    rows = _tool_rows(repository.get_gain_by_tool(conversation_id, limit=50))
    if not rows:
        return "KITT Token Savings\nNo tool gain telemetry recorded yet."
    lines = [
        "KITT Token Savings — By Tool",
        "═" * 66,
        f"{'Tool':<20} {'Calls':>6} {'Raw':>9} {'Return':>9} {'Saved':>9} {'Gain':>7}",
        "─" * 66,
    ]
    for row in rows:
        lines.append(
            f"{row['tool'][:20]:<20} {row['calls']:>6} "
            f"{format_tokens(row['raw_tokens']):>9} "
            f"{format_tokens(row['returned_tokens']):>9} "
            f"{format_tokens(row['saved_tokens']):>9} "
            f"{row['saved_percent']:>6.1f}%"
        )
    return "\n".join(lines)


def render_gain_history(repository, conversation_id: str | None = None, limit: int = 20) -> str:
    rows = repository.get_gain_history(conversation_id, limit=limit)
    if not rows:
        return "KITT Token Savings\nNo tool gain history recorded yet."
    lines = [
        "KITT Token Savings — Recent Calls",
        "═" * 76,
        f"{'Tool':<20} {'Raw':>9} {'Return':>9} {'Saved':>9} {'Gain':>7} {'Time':>8}",
        "─" * 76,
    ]
    for row in rows:
        raw = int(row.get("raw", 0) or 0)
        returned = int(row.get("output", 0) or 0)
        saved = int(row.get("saved", 0) or 0)
        lines.append(
            f"{str(row.get('tool', 'unknown'))[:20]:<20} "
            f"{format_tokens(raw):>9} {format_tokens(returned):>9} "
            f"{format_tokens(saved):>9} {saving_pct(raw, saved):>6.1f}% "
            f"{format_duration_ms(row.get('duration', 0)):>8}"
        )
    return "\n".join(lines)


def render_gain_daily(repository, conversation_id: str | None = None, days: int = 30) -> str:
    rows = repository.get_gain_daily(conversation_id, days=days)
    if not rows:
        return "KITT Token Savings\nNo daily gain telemetry recorded yet."
    lines = [
        "KITT Token Savings — Daily",
        "═" * 60,
        f"{'Date':<12} {'Calls':>6} {'Raw':>10} {'Returned':>10} {'Saved':>10} {'Gain':>7}",
        "─" * 60,
    ]
    for row in rows:
        raw = int(row.get("raw", 0) or 0)
        returned = int(row.get("output", 0) or 0)
        saved = int(row.get("saved", 0) or 0)
        lines.append(
            f"{str(row.get('day', '')):<12} {int(row.get('count', 0)):>6} "
            f"{format_tokens(raw):>10} {format_tokens(returned):>10} "
            f"{format_tokens(saved):>10} {saving_pct(raw, saved):>6.1f}%"
        )
    return "\n".join(lines)


def render_gain_graph(repository, conversation_id: str | None = None, days: int = 30) -> str:
    rows = repository.get_gain_daily(conversation_id, days=days)
    if not rows:
        return "KITT Token Savings\nNo daily gain telemetry recorded yet."
    max_saved = max(int(row.get("saved", 0) or 0) for row in rows) or 1
    lines = ["KITT Token Savings — 30 Day Graph", "═" * 58]
    for row in rows:
        saved = int(row.get("saved", 0) or 0)
        width = round((saved / max_saved) * 28)
        lines.append(
            f"{str(row.get('day', '')):<10} "
            f"{'█' * width:<28} {format_tokens(saved):>8}"
        )
    return "\n".join(lines)


def render_gain(
    repository,
    conversation_id: str | None,
    mode: str = "",
) -> str:
    raw_mode = (mode or "").strip().lower()
    parts = raw_mode.split()
    global_scope = "all" in parts or "--all" in parts
    scope = None if global_scope else conversation_id

    wants_json = (
        raw_mode in ("json", "--json")
        or "--json" in parts
        or (
            "--format" in parts
            and parts.index("--format") + 1 < len(parts)
            and parts[parts.index("--format") + 1] == "json"
        )
    )
    if wants_json:
        return gain_json(repository, scope)

    if not parts or parts == ["summary"]:
        return render_gain_dashboard(repository, conversation_id)
    if parts in (["all"], ["--all"]):
        return render_gain_dashboard(repository, None)
    if "tools" in parts or "--tools" in parts or "tool" in parts:
        return render_gain_tools(repository, scope)
    if "history" in parts or "--history" in parts or "recent" in parts:
        return render_gain_history(repository, scope)
    if "daily" in parts or "--daily" in parts or "day" in parts:
        return render_gain_daily(repository, scope)
    if "graph" in parts or "--graph" in parts or "chart" in parts:
        return render_gain_graph(repository, scope)

    return (
        "Usage: /gain [tools|history|daily|graph|all|json]\n"
        "RTK-style flags also work: --graph --history --daily --all --format json."
    )
