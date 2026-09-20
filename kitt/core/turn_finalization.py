from __future__ import annotations

import hashlib
import re
import time
from typing import Iterator, Optional

from kitt.context_filter.deterministic_extractor import DeterministicExtractor
from kitt.context_filter.prompt_budget import TokenCounter
from kitt.core.execution_request import ExecutionRequest
from kitt.core.pending_action import PendingAction
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    ApprovalRequired,
    EditApplied,
    MetricsRecorded,
    TurnBlocked,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from kitt.domain.entities import EditResult, ModelProfile
from kitt.metrics.cost_estimator import estimate_cost
from kitt.metrics.models import TurnMetrics
from kitt.security.capabilities import CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext


class TurnFinalizationMixin:
    """Patch, metrics, goal and completion phase extracted from TurnProcessor."""

    def _finalize_turn(self, cmd: TurnCommand, full_response: str, execution_messages: list,
                       request: ExecutionRequest, exe_profile: ModelProfile, ctx_profile: ModelProfile,
                       allocated: dict, base_sys: str, context_map_str: str, explicit_str: str,
                       workspace_id: str, turn_started_at: float,
                       security_context: ExecutionSecurityContext) -> Iterator:
        blocks = self.diff_parser.parse(full_response)
        if not blocks:
            clean_resp = self._without_thinking(full_response).strip()
            target_candidates = list(cmd.explicit_files or ())
            last_task = getattr(self.session_state, "last_task", None)
            if last_task and getattr(last_task, "paths", None):
                target_candidates.extend(list(last_task.paths))
            if not target_candidates:
                target_candidates = DeterministicExtractor().extract_paths(cmd.prompt)

            if target_candidates:
                primary_target = target_candidates[0].lstrip('@')
                code_match = re.search(r'```(?:[a-zA-Z0-9_\-]+)?\s*\n([\s\S]*?)\n```', clean_resp)
                code_content = code_match.group(1) if code_match else (clean_resp if len(clean_resp) > 20 and "<" in clean_resp else "")
                if code_content and (code_match or primary_target.endswith(('.html', '.css', '.js', '.ts', '.tsx', '.jsx', '.py', '.json', '.md', '.txt', '.sh'))):
                    from kitt.domain.entities import EditBlock
                    blocks = [
                        EditBlock(
                            file_path=primary_target,
                            search_content="",
                            replace_content=code_content,
                            is_new_file=not (self.root_path / primary_target).exists(),
                            is_deletion=False
                        )
                    ]

        edit_result: Optional[EditResult] = None
        if blocks and not security_context.has_capability(CAP_REPO_WRITE):
            yield TurnBlocked(reason="Write capability is not granted for this turn (fail-closed).")
            return
        if blocks:
            args = {"patch": full_response}
            action_hash = self.registry.policy.generate_action_hash("apply_patch", args)
            perm = self.registry.policy.evaluate_tool(
                "apply_patch", args, conversation_id=cmd.conversation_id
            )

            if perm == 'ASK':
                pa_ws = workspace_id
                now = time.time()
                from kitt.security.mutation_preconditions import capture_preconditions
                preconditions = capture_preconditions(self.root_path, "apply_patch", args)
                affected_paths = [p.path for p in preconditions]
                before_hashes = {p.path: p.expected_sha256 for p in preconditions}
                approval_id = f"req_{cmd.turn_id}_{hashlib.sha256(action_hash.encode()).hexdigest()[:8]}"
                self.registry.approval_manager.register_request(
                    cmd.turn_id, cmd.conversation_id, pa_ws, action_hash, approval_id, tool_name="apply_patch"
                )
                sec_dict = security_context.to_dict()
                sec_dict["mutation_preconditions"] = [p.to_dict() for p in preconditions]
                pa = PendingAction(
                    id=f"pa_{cmd.turn_id}",
                    approval_request_id=approval_id,
                    turn_id=cmd.turn_id,
                    conversation_id=cmd.conversation_id,
                    workspace_id=pa_ws,
                    tool_name="apply_patch",
                    normalized_args=args,
                    action_hash=action_hash,
                    source_response_sha256=self._args_digest(args),
                    affected_paths=affected_paths,
                    before_hashes=before_hashes,
                    created_at=now,
                    expires_at=now + self.config.approval_ttl_seconds,
                    state="pending",
                    security_context=sec_dict,
                )

                if self.history_service:
                    self.history_service.repo.save_pending_action(pa)

                self.registry.approval_manager.register_request(
                    cmd.turn_id, cmd.conversation_id, pa_ws, action_hash, approval_id, "apply_patch", f"Apply patch to {affected_paths}"
                )
                self.pending_actions[cmd.turn_id] = pa
                yield ApprovalRequired(
                    turn_id=cmd.turn_id,
                    conversation_id=cmd.conversation_id,
                    tool_name="apply_patch",
                    args=args,
                    action_hash=action_hash,
                    approval_request_id=approval_id,
                    workspace_id=pa_ws,
                )
                return
            elif perm == 'DENY':
                yield TurnFailed(error="Execution denied by PolicyEngine for apply_patch.")
                return

            # ALLOW. Patch application and its bookkeeping form one
            # cancellation-ordered mutation unit.
            if not self.turn_guard.begin(cmd.turn_id):
                yield TurnCancelled(reason="Turn cancelled before patch mutation")
                return
            try:
                edit_result = self.diff_applier.apply(
                    blocks,
                    root_dir=str(self.root_path),
                    allow_overwrite_existing=True,
                    workspace_id=workspace_id,
                    conversation_id=cmd.conversation_id,
                    turn_id=cmd.turn_id,
                )
                if edit_result.success:
                    self.session_state.last_changeset = edit_result.changeset
                    self.working_set.touch_paths(
                        cmd.conversation_id,
                        edit_result.applied_files + edit_result.created_files,
                        cmd.turn_id,
                        weight=2.0,
                        kind="apply_patch",
                    )
                    self._emit(
                        "EditApplied",
                        {
                            "applied": edit_result.applied_files,
                            "created": edit_result.created_files,
                        },
                    )
            finally:
                self.turn_guard.end(cmd.turn_id)
            if edit_result.success:
                yield EditApplied(
                    applied_files=edit_result.applied_files,
                    created_files=edit_result.created_files,
                )

        output_tokens = TokenCounter.count_tokens(full_response)
        naive_tokens = TokenCounter.count_tokens(
            base_sys + cmd.prompt + context_map_str + explicit_str
            + self._history_context(cmd.conversation_id, 100, cmd.prompt)
        )
        saved = max(0, naive_tokens - allocated["total_input_tokens"])
        actual_input_tokens = TokenCounter.count_tokens(request.system_prompt) + TokenCounter.count_messages(execution_messages).count
        metrics = TurnMetrics(
            turn_id=cmd.turn_id, conversation_id=cmd.conversation_id,
            context_model=ctx_profile.model, execution_model=exe_profile.model,
            naive_input_tokens=naive_tokens,
            actual_input_tokens=actual_input_tokens,
            actual_output_tokens=output_tokens,
            duration_ms=(time.time() - turn_started_at) * 1000,
        )
        cost = estimate_cost(exe_profile.model, allocated["total_input_tokens"], output_tokens)
        if self.metrics_collector and not self.event_callback:
            self.metrics_collector.record_turn(metrics)
        self._emit("MetricsRecorded", metrics)
        yield MetricsRecorded(
            input_tokens=allocated["total_input_tokens"],
            output_tokens=output_tokens, saved_tokens=saved,
            estimated_usd=cost.estimated_usd,
        )
        if self.compaction_service and self.history_service and hasattr(self.history_service, "tree"):
            try:
                path = self.history_service.tree.get_active_path(cmd.conversation_id)
                if len(path) > 12:
                    self.compaction_service.compact(cmd.conversation_id, keep_recent=self.config.compaction_keep_recent)
            except Exception:
                pass

        if self.registry.goal_service:
            try:
                active_goal = self.registry.goal_service.active(cmd.conversation_id)
                if active_goal and active_goal.gates:
                    from kitt.goals.gates import QualityGateRunner
                    from kitt.goals.continuation import ContinuationPolicy
                    gate_runner = QualityGateRunner(self.registry.process_runner)
                    gate_results = [gate_runner.run(g.argv) for g in active_goal.gates if g.argv]
                    cont_policy = ContinuationPolicy()
                    should_cont = cont_policy.should_continue(active_goal, gate_results)
                    if not should_cont:
                        all_passed = all(getattr(r, "returncode", 1) == 0 for r in gate_results)
                        self.registry.goal_service.finish(active_goal.id, "SUCCEEDED" if all_passed else "FAILED")
            except Exception:
                pass

        clean_response = self._without_thinking(full_response)
        self._emit("TurnCompleted", {"response": clean_response, "edit_result": edit_result})
        yield TurnCompleted(response=clean_response, edit_result=edit_result)
