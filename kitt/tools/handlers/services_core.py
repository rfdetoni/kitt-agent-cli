"""Domain services tool handlers (artifacts, goals, queue, child agents, harness)."""
from __future__ import annotations

import json
from typing import Any, Dict
from kitt.tools.handlers import ToolContext


class ArtifactStoreHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        if not ctx.registry.artifact_tools:
            return ToolResult(False, "", "Artifact tools service unavailable.")
        artifact = ctx.registry.artifact_tools.put(
            ctx.workspace_id,
            args.get("content", ""),
            args.get("artifact_type", "TEXT"),
            args.get("summary", "Agent artifact"),
            ctx.conversation_id,
            ctx.turn_id,
        )
        return ToolResult(True, artifact.id, metadata={"artifact": artifact, "artifact_id": artifact.id})


class ArtifactReadHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        if not ctx.registry.artifact_tools:
            return ToolResult(False, "", "Artifact tools service unavailable.")
        raw = ctx.registry.artifact_tools.read_text(
            str(args.get("artifact_id", "")),
            workspace_id=ctx.workspace_id,
            conversation_id=ctx.conversation_id,
        )
        return ToolResult(True, raw, bytes_count=len(raw.encode()))


class ArtifactListHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        if not ctx.registry.artifact_tools:
            return ToolResult(False, "", "Artifact tools service unavailable.")
        items = ctx.registry.artifact_tools.list(
            conversation_id=ctx.conversation_id,
            limit=int(args.get("limit", 20)),
            workspace_id=ctx.workspace_id,
        )
        return ToolResult(True, "\n".join(f"{a.id} {a.artifact_type} {a.size_bytes}B {a.summary}" for a in items))


class QueueInputHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        if not ctx.registry.queue_service:
            return ToolResult(False, "", "Queue service unavailable.")
        kind = str(args.get("kind", "FOLLOW_UP")).upper()
        if kind not in {"STEERING", "FOLLOW_UP"}:
            return ToolResult(False, "", "Invalid queue kind: must be STEERING or FOLLOW_UP.")
        item = (ctx.registry.queue_service.steer if kind == "STEERING" else ctx.registry.queue_service.follow_up)(
            ctx.conversation_id, str(args.get("content", ""))
        )
        return ToolResult(True, item.id)


class GoalCreateHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        service = ctx.registry.goal_tools or ctx.registry.goal_service
        if not service:
            return ToolResult(False, "", "Goal service unavailable.")
        goal = service.create(
            ctx.conversation_id,
            str(args.get("objective", "")),
            args.get("success_criteria", []),
            args.get("token_budget"),
            int(args.get("max_turns", 12)),
            int(args.get("max_wall_seconds", 1800)),
        )
        goal_id = goal.id if hasattr(goal, "id") else str(goal)
        return ToolResult(True, goal_id, metadata={"goal_id": goal_id})


class GoalAddGateHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        if not ctx.registry.goal_service:
            return ToolResult(False, "", "Goal service unavailable.")
        gate = ctx.registry.goal_service.add_gate(
            goal_id=str(args.get("goal_id", "")),
            name=str(args.get("name", "QualityGate")),
            argv=args.get("argv", []),
            timeout_seconds=int(args.get("timeout_seconds", 120)),
        )
        return ToolResult(True, f"Gate '{gate.name}' added with ID {gate.id}.")


class ChildSpawnHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        mgr = ctx.registry.child_tools or ctx.registry.child_manager
        if not mgr:
            return ToolResult(False, "", "Child manager unavailable.")
        child = mgr.spawn(
            parent_conversation_id=ctx.conversation_id,
            parent_turn_id=ctx.turn_id,
            name=str(args.get("name", "child_task")),
            task=str(args.get("task", "")),
            workspace_id=ctx.workspace_id,
            allowed_paths=args.get("allowed_paths", []),
            enabled_tools=args.get("enabled_tools") or args.get("allowed_tools") or ["read_file", "search"],
            token_budget=int(args.get("token_budget", 4000)),
            timeout_seconds=float(args.get("timeout_seconds", 60.0)),
            security_context=ctx.security_context,
        )
        return ToolResult(True, f"Child task spawned with ID {child.id}.", metadata={"child_id": child.id, "child": child})


class HarnessRememberHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        if not ctx.registry.harness_service:
            return ToolResult(False, "", "Harness service unavailable.")
        evidence_raw = args.get("evidence", {})
        if isinstance(evidence_raw, str):
            try:
                evidence_raw = json.loads(evidence_raw)
            except Exception:
                evidence_raw = {"note": evidence_raw}
        entry = ctx.registry.harness_service.remember(
            name=str(args.get("name", "")),
            content=str(args.get("content", "")),
            workspace_id=ctx.workspace_id,
            conversation_id=ctx.conversation_id,
            evidence=evidence_raw,
        )
        return ToolResult(True, f"Harness entry '{entry.name}' saved with ID {entry.id}.")
