from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from kitt.security.private_state import (
    locked_json_update,
    secure_read_json,
    workspace_state_dir,
)


_LOCKFILES = {
    "cargo.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "go.sum",
    "gradle.lockfile",
}


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_signature(path: Any) -> str:
    value = str(path or "").replace("\\", "/")
    name = Path(value).name.casefold()
    if name in _LOCKFILES:
        return "Read(lockfile)"
    suffix = Path(value).suffix.casefold()
    return f"Read(*{suffix})" if suffix else "Read(file)"


def _command_signature(argv: Any) -> str:
    if not isinstance(argv, list):
        return "Bash(other)"
    words = [str(item).strip().casefold() for item in argv[:4] if str(item).strip()]
    if not words:
        return "Bash(other)"
    candidates = (
        ("git", "diff"),
        ("git", "status"),
        ("mvn", "test"),
        ("mvn", "verify"),
        ("mvnw", "test"),
        ("gradle", "test"),
        ("gradlew", "test"),
        ("gradlew", "build"),
        ("npm", "test"),
        ("npm", "run", "test"),
        ("npm", "run", "build"),
        ("pnpm", "test"),
        ("pnpm", "build"),
        ("cargo", "test"),
        ("cargo", "check"),
        ("go", "test"),
        ("pytest",),
    )
    for candidate in candidates:
        if tuple(words[: len(candidate)]) == candidate:
            return f"Bash({' '.join(candidate)})"
    return "Bash(other)"


def safe_tool_signature(payload: dict[str, Any]) -> str:
    name = str(payload.get("tool_name") or payload.get("tool") or "")
    args = payload.get("args")
    args = args if isinstance(args, dict) else {}
    operation = name
    inner = args
    if name == "kitt_runtime":
        operation = str(args.get("operation") or "runtime")
        nested = args.get("arguments")
        inner = nested if isinstance(nested, dict) else {}

    if operation in {"repo.read", "read_file"}:
        return _read_signature(inner.get("path") or inner.get("file"))
    if operation in {"repo.list", "list_files"}:
        return "List(repository)"
    if operation in {"repo.search", "search"}:
        return "Search(repository)"
    if operation in {"process.run", "run_command"}:
        return _command_signature(inner.get("argv"))
    if operation in {"patch.apply", "apply_patch"}:
        return "Edit(patch)"
    if operation in {"repo.write_file", "write_file"}:
        return _read_signature(inner.get("path") or inner.get("file")).replace(
            "Read(", "Write(", 1
        )
    if operation.startswith("mcp.") or name.startswith("mcp"):
        server = str(inner.get("server") or args.get("server") or "server")
        tool = str(inner.get("tool") or args.get("tool") or "tool")
        clean = lambda value: "".join(
            ch if ch.isalnum() or ch in "_-" else "_"
            for ch in value
        )[:64] or "unknown"
        return f"mcp__{clean(server)}__{clean(tool)}"
    if operation.startswith("children.") or name == "child_spawn":
        return "Subagent(spawn)"
    return f"Tool({operation[:80] or 'unknown'})"


class LearnService:
    """Local evidence-only optimizer analytics.

    Raw tool arguments never leave the canonical event store through this
    service. Reports expose only bounded, category-level signatures.
    """

    def __init__(
        self,
        root_dir: str | Path,
        db,
        workspace_id: str,
        *,
        memory_client: Any = None,
    ) -> None:
        self.root = Path(root_dir).resolve()
        self.db = db
        self.workspace_id = str(workspace_id)
        self.memory_client = memory_client
        self.state_file = workspace_state_dir(self.root, "learn") / "experiments.json"

    def _events(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        limit: int = 20000,
    ) -> list[dict[str, Any]]:
        where = ["c.workspace_id=?"]
        args: list[Any] = [self.workspace_id]
        if since is not None:
            where.append("e.created_at>=?")
            args.append(float(since))
        if until is not None:
            where.append("e.created_at<?")
            args.append(float(until))
        args.append(max(1, min(int(limit), 50000)))
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT e.conversation_id,e.turn_id,e.event_type,e.payload_json,
                          e.created_at
                   FROM session_events e
                   JOIN conversations c ON c.id=e.conversation_id
                   WHERE """
                + " AND ".join(where)
                + " ORDER BY e.created_at ASC LIMIT ?",
                args,
            ).fetchall()
        return [
            {
                "conversation_id": str(row["conversation_id"] or ""),
                "turn_id": str(row["turn_id"] or ""),
                "event_type": str(row["event_type"] or ""),
                "payload": _payload(row["payload_json"]),
                "created_at": float(row["created_at"] or 0.0),
            }
            for row in rows
        ]

    def _telemetry(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
    ) -> list[dict[str, Any]]:
        where = ["c.workspace_id=?"]
        args: list[Any] = [self.workspace_id]
        if since is not None:
            where.append("t.start_time>=?")
            args.append(float(since))
        if until is not None:
            where.append("t.start_time<?")
            args.append(float(until))
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT t.route,t.duration_ms,t.input_tokens,t.output_tokens,
                          t.tokens_saved,t.turn_id,t.conversation_id
                   FROM telemetry_events t
                   JOIN conversations c ON c.id=t.conversation_id
                   WHERE """
                + " AND ".join(where),
                args,
            ).fetchall()
        return [dict(row) for row in rows]

    def _memory_receipts(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
    ) -> list[dict[str, Any]]:
        if self.memory_client is None:
            return []
        try:
            body = self.memory_client.manage(
                "receipt.list",
                {"workspace_id": self.workspace_id, "limit": 1000},
            )
        except Exception:
            return []
        rows = body.get("receipts") if isinstance(body, dict) else []
        result = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            ts = float(row.get("consumed_at") or 0.0)
            if since is not None and ts < since:
                continue
            if until is not None and ts >= until:
                continue
            result.append(row)
        return result

    def metrics(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
    ) -> dict[str, Any]:
        events = self._events(since=since, until=until)
        telemetry = self._telemetry(since=since, until=until)
        receipts = self._memory_receipts(since=since, until=until)

        conversations = {item["conversation_id"] for item in events if item["conversation_id"]}
        turns = {item["turn_id"] for item in events if item["turn_id"]}
        event_counts = Counter(item["event_type"] for item in events)
        completed = event_counts["TurnCompleted"]
        failed_turns = event_counts["TurnFailed"] + event_counts["TurnBlocked"]
        terminal = completed + failed_turns

        validation_total = 0
        validation_ok = 0
        tool_calls = 0
        tool_errors = 0
        portfolio: Counter[str] = Counter()
        by_turn_signatures: dict[str, Counter[str]] = defaultdict(Counter)
        output_waste = 0
        subagent_spend = 0
        epoch_by_turn: dict[str, list[str]] = defaultdict(list)
        context_hashes: Counter[str] = Counter()

        for item in events:
            typ = item["event_type"]
            body = item["payload"]
            turn_id = item["turn_id"]
            if typ == "ToolStarted":
                signature = safe_tool_signature(body)
                portfolio[signature] += 1
                by_turn_signatures[turn_id][signature] += 1
                tool_calls += 1
            elif typ == "ToolCompleted":
                if body.get("success") is False:
                    tool_errors += 1
                visible = str(body.get("output") or body.get("error") or "")
                if len(visible.encode("utf-8", errors="replace")) > 8192:
                    output_waste += 1
            elif typ == "ValidationCompleted":
                validation_total += 1
                if body.get("success") is not False:
                    validation_ok += 1
            elif typ == "ChildAgentFinished":
                try:
                    subagent_spend += max(0, int(body.get("tokens_used") or 0))
                except (TypeError, ValueError):
                    pass
            elif typ in {"ContextEpochComputed", "ContextEpochCaptured"}:
                digest = str(body.get("snapshot_digest") or "")
                if digest:
                    epoch_by_turn[turn_id].append(digest)
            elif typ == "ModelRequestPrepared":
                digest = str(body.get("context_hash") or "")
                if digest:
                    context_hashes[digest] += 1

        reread_waste = sum(
            max(0, count - 1)
            for signatures in by_turn_signatures.values()
            for signature, count in signatures.items()
            if signature.startswith("Read(")
        )
        retry_waste = sum(
            max(0, count - 1)
            for signatures in by_turn_signatures.values()
            for signature, count in signatures.items()
            if count > 1
        )
        compaction_churn = event_counts["ContextCompacted"] + event_counts["CompactionCompleted"]
        cache_instability = sum(
            max(0, len(set(digests)) - 1)
            for digests in epoch_by_turn.values()
        )
        context_duplication = sum(max(0, count - 1) for count in context_hashes.values())
        mcp_overhead = sum(count for signature, count in portfolio.items() if signature.startswith("mcp__"))

        input_tokens = sum(int(row.get("input_tokens") or 0) for row in telemetry)
        output_tokens = sum(int(row.get("output_tokens") or 0) for row in telemetry)
        duration_values = [float(row.get("duration_ms") or 0.0) for row in telemetry]
        router_rows = [
            row
            for row in telemetry
            if str(row.get("route") or "").startswith("routing:")
        ]
        router_overhead = sum(float(row.get("duration_ms") or 0.0) for row in router_rows)

        referenced = sum(1 for row in receipts if bool(row.get("referenced")))
        used_for_action = sum(1 for row in receipts if bool(row.get("used_for_action")))
        memory_low_utility = max(0, len(receipts) - max(referenced, used_for_action))

        return {
            "window": {"since": since, "until": until},
            "sessions": len(conversations),
            "turns": len(turns),
            "success": {
                "completed": completed,
                "failed": failed_turns,
                "rate": (completed / terminal) if terminal else None,
            },
            "validation": {
                "observed": validation_total > 0,
                "passed": validation_ok,
                "total": validation_total,
                "rate": (validation_ok / validation_total) if validation_total else None,
            },
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "cache": None,
                "cost": None,
            },
            "latency": {
                "observed": bool(duration_values),
                "total_ms": sum(duration_values),
                "avg_ms": (
                    sum(duration_values) / len(duration_values)
                    if duration_values
                    else None
                ),
            },
            "errors": tool_errors + failed_turns,
            "tool_calls": tool_calls,
            "memory_consumption": {
                "observed": bool(receipts),
                "receipts": len(receipts),
                "referenced": referenced,
                "used_for_action": used_for_action,
            },
            "waste": {
                "REREAD_WASTE": reread_waste,
                "TOOL_OUTPUT_WASTE": output_waste,
                "COMPACTION_CHURN": compaction_churn,
                "MCP_OVERHEAD": mcp_overhead,
                "SUBAGENT_SPEND": subagent_spend,
                "RETRY_WASTE": retry_waste,
                "MEMORY_LOW_UTILITY": memory_low_utility,
                "CACHE_INSTABILITY": cache_instability,
                "CONTEXT_DUPLICATION": context_duplication,
                "ROUTER_OVERHEAD": router_overhead,
            },
            "tool_portfolio": [
                {"signature": signature, "calls": calls}
                for signature, calls in portfolio.most_common(30)
            ],
        }

    def suggestions(self) -> list[dict[str, Any]]:
        report = self.metrics()
        waste = report["waste"]
        mapping = {
            "REREAD_WASTE": ("context-cache", "Avoid repeated unchanged source reads."),
            "TOOL_OUTPUT_WASTE": ("artifact-recovery", "Externalize large tool output earlier."),
            "COMPACTION_CHURN": ("compaction", "Stabilize epoch/compaction thresholds."),
            "MCP_OVERHEAD": ("mcp-routing", "Reduce unnecessary MCP round trips."),
            "SUBAGENT_SPEND": ("subagent-budget", "Tighten child wallet or task scope."),
            "RETRY_WASTE": ("progress-guard", "Redirect equivalent retries earlier."),
            "MEMORY_LOW_UTILITY": ("memory-recall", "Reduce low-utility recalled memories."),
            "CACHE_INSTABILITY": ("context-epoch", "Investigate unstable context revisions."),
            "CONTEXT_DUPLICATION": ("context-dedup", "Deduplicate repeated model context."),
            "ROUTER_OVERHEAD": ("router", "Measure whether route selection work is excessive."),
        }
        result = []
        for category, (feature, message) in mapping.items():
            value = waste.get(category)
            numeric = float(value or 0.0)
            if numeric <= 0:
                continue
            result.append(
                {
                    "category": category,
                    "feature": feature,
                    "evidence": value,
                    "suggestion": message,
                    "auto_apply": False,
                }
            )
        return sorted(
            result,
            key=lambda item: -float(item.get("evidence") or 0.0),
        )

    def _state(self) -> dict[str, Any]:
        value = secure_read_json(
            self.state_file,
            default={"experiments": {}},
            max_bytes=1024 * 1024,
        )
        if not isinstance(value, dict):
            return {"experiments": {}}
        if not isinstance(value.get("experiments"), dict):
            value["experiments"] = {}
        return value

    def experiment_start(self, feature: str) -> dict[str, Any]:
        name = str(feature or "").strip().casefold()
        if not name:
            raise ValueError("feature is required")
        now = time.time()
        with locked_json_update(
            self.state_file,
            default={"experiments": {}},
            max_bytes=1024 * 1024,
        ) as state:
            experiments = state.setdefault("experiments", {})
            current = experiments.get(name)
            if isinstance(current, dict) and current.get("state") == "RUNNING":
                raise ValueError(f"experiment {name!r} is already running")
            experiments[name] = {
                "feature": name,
                "state": "RUNNING",
                "current_arm": "control",
                "control": {"started_at": now, "ended_at": None},
                "candidate": {"started_at": None, "ended_at": None},
                "created_at": now,
            }
            return dict(experiments[name])

    def experiment_switch(self, feature: str, arm: str) -> dict[str, Any]:
        name = str(feature or "").strip().casefold()
        target = str(arm or "").strip().casefold()
        if target not in {"control", "candidate"}:
            raise ValueError("arm must be control or candidate")
        now = time.time()
        with locked_json_update(
            self.state_file,
            default={"experiments": {}},
            max_bytes=1024 * 1024,
        ) as state:
            experiment = state.setdefault("experiments", {}).get(name)
            if not isinstance(experiment, dict) or experiment.get("state") != "RUNNING":
                raise ValueError(f"experiment {name!r} is not running")
            current = str(experiment.get("current_arm") or "")
            if current == target:
                return dict(experiment)
            if current in {"control", "candidate"}:
                experiment[current]["ended_at"] = now
            if experiment[target].get("started_at") is None:
                experiment[target]["started_at"] = now
            experiment[target]["ended_at"] = None
            experiment["current_arm"] = target
            return dict(experiment)

    def _arm_metrics(self, arm: dict[str, Any], now: float) -> dict[str, Any]:
        started = arm.get("started_at")
        if started is None:
            return {"observed": False}
        ended = arm.get("ended_at")
        return {
            "observed": True,
            **self.metrics(
                since=float(started),
                until=float(ended) if ended is not None else now,
            ),
        }

    @staticmethod
    def _experiment_verdict(
        control: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        if not control.get("observed") or not candidate.get("observed"):
            return {"state": "EVIDENCE_REQUIRED", "promote": False}
        if int(control.get("turns") or 0) < 1 or int(candidate.get("turns") or 0) < 1:
            return {"state": "EVIDENCE_REQUIRED", "promote": False}

        c_success = control.get("success", {}).get("rate")
        n_success = candidate.get("success", {}).get("rate")
        c_validation = control.get("validation", {})
        n_validation = candidate.get("validation", {})
        if c_success is not None and n_success is not None and n_success < c_success:
            return {"state": "REGRESSION", "promote": False, "reason": "success-rate"}
        if (
            c_validation.get("observed")
            and n_validation.get("observed")
            and float(n_validation.get("rate") or 0.0)
            < float(c_validation.get("rate") or 0.0)
        ):
            return {"state": "REGRESSION", "promote": False, "reason": "validation"}

        c_total = int(control.get("tokens", {}).get("input") or 0) + int(
            control.get("tokens", {}).get("output") or 0
        )
        n_total = int(candidate.get("tokens", {}).get("input") or 0) + int(
            candidate.get("tokens", {}).get("output") or 0
        )
        c_latency = control.get("latency", {}).get("avg_ms")
        n_latency = candidate.get("latency", {}).get("avg_ms")
        measurable_gain = (
            (c_total > 0 and n_total < c_total)
            or (
                c_latency is not None
                and n_latency is not None
                and float(n_latency) < float(c_latency)
            )
        )
        return {
            "state": "CANDIDATE_BETTER" if measurable_gain else "NO_CHANGE",
            # Promotion remains explicit even with positive evidence.
            "promote": False,
            "measurable_gain": bool(measurable_gain),
        }

    def experiment_report(self, feature: str) -> dict[str, Any]:
        name = str(feature or "").strip().casefold()
        state = self._state()
        experiment = state.get("experiments", {}).get(name)
        if not isinstance(experiment, dict):
            raise ValueError(f"experiment {name!r} not found")
        now = time.time()
        control = self._arm_metrics(dict(experiment.get("control") or {}), now)
        candidate = self._arm_metrics(dict(experiment.get("candidate") or {}), now)
        verdict = self._experiment_verdict(control, candidate)
        return {
            "feature": name,
            "current_arm": experiment.get("current_arm"),
            "control": control,
            "candidate": candidate,
            "verdict": verdict,
            "auto_promotion": False,
        }
