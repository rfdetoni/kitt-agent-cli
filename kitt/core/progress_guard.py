"""Progress-aware completion guard overrides.

This module keeps the existing host-verifiable completion contract while making
exploration loop detection depend on completed tool results rather than call
starts. Repeated unchanged/unsupported exploration is redirected before the
hard fail-safe fires.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from types import MethodType
from typing import Any, Iterator

from kitt.core import completion_guard as _base
from kitt.core.turn_events import ToolCompleted, ToolStarted, TurnFailed


_MAX_STALL_REDIRECTS = 2
_TERMINAL_EXPLORATION_ERROR_RE = re.compile(
    r"(?:unknown (?:runtime )?operation|not supported|unsupported|not available|"
    r"not granted|no such tool|does not allow|não permite|nao permite|"
    r"capability .+ required|invalid operation for (?:this )?route)",
    re.IGNORECASE,
)


def _canonical_exploration(tool_name: str, args: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(args, dict):
        return None

    if tool_name == "kitt_runtime":
        operation = str(args.get("operation") or "").strip().lower()
        if operation not in _base._RUNTIME_EXPLORATION_OPERATIONS:
            return None
        raw_arguments = args.get("arguments", {})
        arguments = dict(raw_arguments) if isinstance(raw_arguments, dict) else {"value": raw_arguments}
    else:
        operation = str(tool_name or "").strip().lower()
        if not (_base._is_exploration_call(tool_name, args) or operation in _base._RUNTIME_EXPLORATION_OPERATIONS):
            return None
        arguments = dict(args)

    for key in ("force_refresh", "max_tokens", "token_budget"):
        arguments.pop(key, None)
    return operation, arguments


def _exploration_signature(tool_name: str, args: Any) -> str | None:
    canonical = _canonical_exploration(tool_name, args)
    if canonical is None:
        return None
    operation, arguments = canonical
    payload = json.dumps(
        [operation, arguments],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def _result_fingerprint(event: ToolCompleted) -> str:
    payload = json.dumps(
        {
            "success": bool(event.success),
            "output": str(event.output or "").strip(),
            "error": str(event.error or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


class _ProgressAwareExecutionLedger:
    """Track progress only after observing host results."""

    def __init__(self) -> None:
        self.successful_mutations: set[str] = set()
        self.successful_validations: set[str] = set()
        self.successful_validation_scopes: set[str] = set()
        self.pending_mutations: dict[str, str] = {}
        self.pending_validations: dict[str, tuple[str, str]] = {}
        self.pending_explorations: dict[str, tuple[str, str]] = {}
        self.exploration_results: dict[str, tuple[str, int]] = {}
        self.explorations_since_progress = 0

    @property
    def revision(self) -> int:
        return len(self.successful_mutations) + len(self.successful_validations)

    @property
    def validation_succeeded(self) -> bool:
        return bool(self.successful_validations)

    @property
    def validated_scopes(self) -> frozenset[str]:
        return frozenset(self.successful_validation_scopes)

    def start(self, event: ToolStarted) -> str | None:
        signature = _base._tool_signature(event.tool_name, event.args)
        if _base._is_mutation_call(event.tool_name, event.args):
            self.pending_mutations[event.call_id] = signature
        if _base._is_validation_call(event.tool_name, event.args):
            self.pending_validations[event.call_id] = (
                signature,
                _base._validation_scope(event.args),
            )

        exploration_signature = _exploration_signature(event.tool_name, event.args)
        if exploration_signature is None:
            return None

        if self.explorations_since_progress >= _base._MAX_EXPLORATIONS_WITHOUT_PROGRESS:
            return (
                "too many exploration calls without a successful mutation "
                f"({_base._MAX_EXPLORATIONS_WITHOUT_PROGRESS} allowed)"
            )

        previous = self.exploration_results.get(exploration_signature)
        previous_count = previous[1] if previous else 0
        if previous_count >= _base._MAX_IDENTICAL_EXPLORATIONS_WITHOUT_PROGRESS - 1:
            canonical = _canonical_exploration(event.tool_name, event.args)
            operation = canonical[0] if canonical else event.tool_name
            return (
                "identical exploration repeated without progress: "
                f"{operation} ({previous_count + 1} attempted times)"
            )

        self.pending_explorations[event.call_id] = (
            exploration_signature,
            event.tool_name,
        )
        return None

    def complete(self, event: ToolCompleted) -> tuple[bool, bool]:
        mutation = self.pending_mutations.pop(event.call_id, None)
        validation_entry = self.pending_validations.pop(event.call_id, None)
        exploration_entry = self.pending_explorations.pop(event.call_id, None)
        validation = validation_entry[0] if validation_entry else None
        validation_scope = validation_entry[1] if validation_entry else None

        if mutation is None and event.tool_name in _base._MUTATION_TOOLS:
            mutation = f"completed:{event.tool_name}:{event.call_id or 'legacy'}"

        new_mutation = bool(
            event.success and mutation and mutation not in self.successful_mutations
        )
        new_validation = bool(
            event.success and validation and validation not in self.successful_validations
        )

        if new_mutation and mutation:
            self.successful_mutations.add(mutation)
        if new_validation and validation:
            self.successful_validations.add(validation)
            if validation_scope:
                self.successful_validation_scopes.add(validation_scope)

        if exploration_entry is not None:
            exploration_signature, _ = exploration_entry
            fingerprint = _result_fingerprint(event)
            previous = self.exploration_results.get(exploration_signature)
            count = previous[1] + 1 if previous and previous[0] == fingerprint else 1

            if not event.success and _TERMINAL_EXPLORATION_ERROR_RE.search(
                str(event.error or event.output or "")
            ):
                count = max(
                    count,
                    _base._MAX_IDENTICAL_EXPLORATIONS_WITHOUT_PROGRESS - 1,
                )

            self.exploration_results[exploration_signature] = (fingerprint, count)
            self.explorations_since_progress += 1

        if new_mutation:
            self.exploration_results.clear()
            self.pending_explorations.clear()
            self.explorations_since_progress = 0

        return new_mutation, new_validation

    def renew_exploration_budget(self) -> None:
        """Grant a fresh aggregate exploration window after a guard redirect.

        Preserve result/signature history so an identical read/search loop remains
        blocked. Only the aggregate budget is renewed; otherwise the very first
        legitimate exploration after a redirect would immediately stall again.
        """
        self.pending_explorations.clear()
        self.explorations_since_progress = 0


def _forward_progress_retry_message(stall: str) -> str:
    return (
        "[KITT FORWARD PROGRESS REQUIRED]\n"
        f"The previous exploration is now blocked because it made no new progress: {stall}.\n"
        "Do not repeat the same read/list/search/inspect call with equivalent arguments. "
        "Reuse the result already obtained. If a tool reported an unsupported operation, "
        "route mismatch, missing capability, or unavailable feature, do not retry it unchanged. "
        "Choose a genuinely new action: read a concrete file/symbol discovered earlier, change "
        "the query/range, perform the required workspace mutation, run an appropriate validation, "
        "or finish if the user's request is already satisfied."
    )


def install_completion_guard(processor: Any, registry: Any, *, max_retries: int = 1) -> None:
    """Install a bounded completion guard with result-aware stall recovery."""
    if getattr(processor, "_completion_guard_installed", False):
        return

    original_loop = processor._execute_tool_loop
    retries_allowed = max(0, int(max_retries))

    def guarded_tool_loop(
        self,
        cmd,
        request,
        exe_profile,
        exe_client,
        workspace_id,
        security_context,
        agent_route=None,
        **loop_kwargs,
    ) -> Iterator:
        current_request = request
        recoveries = 0
        stall_redirects = 0
        last_recovery_revision = 0
        ledger = _ProgressAwareExecutionLedger()
        failed_mutations: dict[str, str] = {}
        mutation_required = _base.requires_workspace_mutation(self, cmd)
        task = getattr(getattr(self, "session_state", None), "last_task", None)
        contract = _base.build_completion_contract(
            str(getattr(cmd, "prompt", "") or ""),
            task,
        )

        while True:
            terminal: tuple[str, list] | None = None
            restart_for_stall: str | None = None
            stream = original_loop(
                cmd,
                current_request,
                exe_profile,
                exe_client,
                workspace_id,
                security_context,
                agent_route=agent_route,
                **loop_kwargs,
            )
            try:
                for event, response, messages in stream:
                    if event is None and response is not None and messages is not None:
                        terminal = (response, messages)
                        continue

                    if isinstance(event, ToolStarted):
                        stall = ledger.start(event)
                        if stall and mutation_required:
                            if stall_redirects < _MAX_STALL_REDIRECTS:
                                stall_redirects += 1
                                restart_for_stall = stall
                                break
                            yield TurnFailed(
                                error=(
                                    "Execution stalled: "
                                    + stall
                                    + ". The implementation requires forward progress; "
                                    "repeated read/list/search calls cannot complete it."
                                )
                            ), None, None
                            return
                    elif isinstance(event, ToolCompleted):
                        was_mutation = (
                            event.call_id in ledger.pending_mutations
                            or event.tool_name in _base._MUTATION_TOOLS
                        )
                        new_mutation, _ = ledger.complete(event)
                        if new_mutation:
                            stall_redirects = 0
                        if was_mutation:
                            if event.success:
                                failed_mutations.pop(event.tool_name, None)
                            else:
                                failed_mutations[event.tool_name] = (
                                    event.error or "resultado sem detalhes"
                                )
                    yield event, response, messages
            finally:
                if restart_for_stall is not None and hasattr(stream, "close"):
                    stream.close()

            if restart_for_stall is not None:
                # A redirect is a bounded recovery attempt, so it must grant a real
                # exploration window. Preserve identical-call history to keep the
                # guard fail-closed against repeating the exact same exploration.
                ledger.renew_exploration_budget()
                retry_messages = list(current_request.messages)
                retry_messages.append(
                    {
                        "role": "user",
                        "content": _forward_progress_retry_message(restart_for_stall),
                    }
                )
                current_request = replace(request, messages=retry_messages)
                continue

            if terminal is None:
                return

            response, messages = terminal
            missing = _base.missing_claimed_workspace_files(
                registry.root_path,
                response,
            )
            mutation_missing = mutation_required and not ledger.successful_mutations
            deferred_implementation = (
                mutation_required
                and _base.is_deferred_implementation_response(response)
            )
            contract_issues = (
                contract.evaluate(
                    registry.root_path,
                    validation_succeeded=ledger.validation_succeeded,
                    validated_scopes=ledger.validated_scopes,
                )
                if mutation_required and contract.enabled
                else []
            )

            if (
                not missing
                and not failed_mutations
                and not mutation_missing
                and not deferred_implementation
                and not contract_issues
            ):
                yield None, response, messages
                return

            progress_since_recovery = ledger.revision > last_recovery_revision
            normal_budget_exhausted = recoveries >= retries_allowed
            progress_budget_exhausted = recoveries >= _base._MAX_PROGRESS_RECOVERIES
            if progress_budget_exhausted or (
                normal_budget_exhausted and not progress_since_recovery
            ):
                reasons: list[str] = []
                if mutation_missing:
                    reasons.append(
                        "the task required a workspace mutation but no mutation tool succeeded"
                    )
                if deferred_implementation:
                    reasons.append(
                        "the response deferred required implementation work back to the user"
                    )
                if contract_issues:
                    reasons.append(
                        "completion contract remains unsatisfied: "
                        + "; ".join(contract_issues)
                    )
                if missing:
                    reasons.append(
                        "claimed files are still missing after recovery: "
                        + ", ".join(missing)
                    )
                if failed_mutations:
                    reasons.append(
                        "mutation tool failures remain: "
                        + "; ".join(
                            f"{name}: {error}"
                            for name, error in failed_mutations.items()
                        )
                    )
                yield TurnFailed(
                    error="Completion verification failed: " + "; ".join(reasons)
                ), None, None
                return

            recoveries += 1
            last_recovery_revision = ledger.revision
            retry_messages = list(messages)
            recovery_parts: list[str] = []
            if mutation_missing or deferred_implementation:
                recovery_parts.append(_base._required_mutation_retry_message())
            if contract_issues:
                recovery_parts.append(_base._contract_retry_message(contract_issues))
            if missing:
                recovery_parts.append(_base._claimed_files_retry_message(missing))
            if failed_mutations:
                recovery_parts.append(
                    _base._failed_mutation_retry_message(failed_mutations)
                )
            retry_messages.extend(
                [
                    {"role": "assistant", "content": response},
                    {"role": "user", "content": "\n\n".join(recovery_parts)},
                ]
            )
            current_request = replace(request, messages=retry_messages)

    processor._execute_tool_loop = MethodType(guarded_tool_loop, processor)
    processor._completion_guard_installed = True


__all__ = ["install_completion_guard"]
