from __future__ import annotations

import re
import shlex
from dataclasses import replace
from kitt.llm.privacy import profile_processing_is_local

from pathlib import PurePosixPath
from urllib.parse import urlparse
from typing import Any

from kitt.context_filter.prompt_budget import TokenCounter
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    ApprovalRequired,
    EditApplied,
    MetricsRecorded,
    ToolCompleted,
    ToolStarted,
    TurnBlocked,
    TurnCompleted,
    TurnFailed,
)
from kitt.goals.completion import (
    AutonomousCompletionEngine,
    completion_state_key,
)
from kitt.goals.contract_checks import ContractCheckRunner
from kitt.goals.contract_validation import ContractValidator
from kitt.goals.gates import QualityGateRunner
from kitt.goals.evidence import EvidenceLedger
from kitt.goals.risk import ReviewRisk, classify_review_risk
from kitt.goals.review import AdversarialCodeReviewer, combine_adversarial_reviews
from kitt.llm.client import LLMClient
from kitt.metrics.cost_estimator import estimate_cost, estimate_execution_cost
from kitt.runtime.state import RuntimeStateStore
from kitt.security.context import ExecutionSecurityContext


class GoalStepExecutor:
    """Execute scheduler work through the canonical TurnProcessor policy path."""

    RESUME_KEY_PREFIX = "goal.resume:"
    COMPLETION_STATE_TTL_SECONDS = 24 * 60 * 60
    MAX_REVIEW_SNAPSHOT_CHARS = 120_000
    MAX_REVIEW_FILES = 24
    MAX_REVIEW_FILE_CHARS = 24_000
    REVIEWABLE_EXTENSIONS = frozenset({
        ".py", ".pyi", ".rs", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
        ".java", ".kt", ".kts", ".go", ".c", ".cc", ".cpp", ".h", ".hpp",
        ".cs", ".rb", ".php", ".sql", ".sh", ".bash", ".zsh", ".ps1",
        ".toml", ".yaml", ".yml", ".json", ".xml", ".gradle", ".properties",
        ".html", ".css", ".scss", ".proto",
    })
    REVIEWABLE_BASENAMES = frozenset({
        "Dockerfile", "Makefile", "Jenkinsfile", "Procfile", "Rakefile",
        "pyproject.toml", "Cargo.toml", "package.json", "pom.xml",
        "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    })

    def __init__(self, runtime_getter, reviewer_factory=None):
        self.runtime_getter = runtime_getter
        self.reviewer_factory = reviewer_factory

    @classmethod
    def _resume_key(cls, goal_id: str) -> str:
        return f"{cls.RESUME_KEY_PREFIX}{goal_id}"

    @staticmethod
    def _completion_engine(runtime, goal) -> AutonomousCompletionEngine:
        def authorize_gate(argv):
            return runtime.policy.evaluate_tool(
                "run_command",
                {"argv": list(argv)},
                origin="SCHEDULE",
                conversation_id=goal.conversation_id,
                workspace_id=runtime.workspace_id,
            )

        return AutonomousCompletionEngine(
            gate_runner=QualityGateRunner(runtime.registry.process_runner),
            gate_result_recorder=runtime.goals.record_gate_result,
            gate_authorizer=authorize_gate,
        )

    @staticmethod
    def _review_profile(runtime, route: str = "adversarial-review"):
        """Prefer a role-specific reviewer route, then adversarial-review, then execution."""
        router = runtime.processor.router
        configured = str(router.config.routing.get(route) or "").strip()
        if not configured and route != "adversarial-review":
            configured = str(
                router.config.routing.get("adversarial-review") or ""
            ).strip()
        if configured and configured in router.config.profiles:
            return configured, router.config.profiles[configured]
        return router.resolve_profile_for_task("code-generation")

    @staticmethod
    def _redact_for_review(runtime, text: str):
        scanner = getattr(runtime, "sensitive_scanner", None)
        if scanner is None:
            return text, (), 0
        result = scanner.scan_and_redact(text)
        return result.clean_text, tuple(result.categories), int(result.redaction_count)

    @classmethod
    def _is_reviewable_path(cls, path: str) -> bool:
        normalized = str(path or "").replace("\\", "/").lstrip("./")
        if not normalized or normalized.startswith("../"):
            return False
        pure = PurePosixPath(normalized)
        return pure.name in cls.REVIEWABLE_BASENAMES or pure.suffix.lower() in cls.REVIEWABLE_EXTENSIONS

    @staticmethod
    def _resolve_symbol_path(runtime, inner: dict) -> str:
        value = str(inner.get("symbol_id") or inner.get("symbol") or "").strip()
        if not value:
            return ""
        engine = getattr(runtime.registry, "native_engine", None)
        if engine is None:
            return ""
        try:
            found = engine.read_symbol(value)
            if not found and hasattr(engine, "find_symbols"):
                matches = engine.find_symbols(value, limit=1)
                if matches:
                    found = engine.read_symbol(matches[0].get("id", ""))
            if isinstance(found, dict):
                symbol = found.get("symbol")
                if isinstance(symbol, dict):
                    return str(symbol.get("path") or "")
        except Exception:
            return ""
        return ""

    @classmethod
    def _paths_from_tool_start(cls, runtime, event: ToolStarted) -> list[str]:
        tool_name = str(getattr(event, "tool_name", "") or "")
        args = dict(getattr(event, "args", None) or {})
        paths = []
        if tool_name == "write_file":
            path = args.get("path") or args.get("file")
            if path:
                paths.append(str(path))
        elif tool_name == "apply_patch":
            try:
                paths.extend(block.file_path for block in runtime.registry.parser.parse(str(args.get("patch") or "")))
            except Exception:
                pass
        elif tool_name == "kitt_runtime":
            operation = str(args.get("operation") or "")
            raw_inner = args.get("arguments")
            inner: dict[str, Any] = (
                dict(raw_inner) if isinstance(raw_inner, dict) else {}
            )
            if operation == "repo.edit_symbol":
                path = inner.get("path") or inner.get("file") or cls._resolve_symbol_path(runtime, inner)
                if path:
                    paths.append(str(path))
            elif operation == "patch.apply":
                try:
                    paths.extend(
                        block.file_path
                        for block in runtime.registry.parser.parse(str(inner.get("patch") or ""))
                    )
                except Exception:
                    pass
        return [
            path for path in paths
            if path and not path.startswith("/") and cls._is_reviewable_path(path)
        ]

    @staticmethod
    def _filter_diff_for_paths(diff_text: str, paths: list[str]) -> tuple[str, set[str], bool]:
        def normalize(path):
            value = str(path).replace("\\", "/")
            while value.startswith("./"):
                value = value[2:]
            return value

        wanted = {normalize(path) for path in paths if path}
        if not wanted or not diff_text.strip():
            return "", set(), True
        blocks = []
        current: list[str] = []
        matched_paths = set()
        complete = True

        def flush():
            nonlocal current, complete
            if not current:
                return
            header = current[0]
            try:
                tokens = shlex.split(header)
                if len(tokens) < 4:
                    complete = False
                    current = []
                    return
                old_path = tokens[2][2:] if tokens[2].startswith("a/") else tokens[2]
                new_path = tokens[3][2:] if tokens[3].startswith("b/") else tokens[3]
                candidates = {old_path.replace("\\", "/"), new_path.replace("\\", "/")}
                matches = wanted & candidates
                if matches:
                    blocks.append("\n".join(current))
                    matched_paths.update(matches)
            except Exception:
                complete = False
            current = []

        for line in diff_text.splitlines():
            if line.startswith("diff --git "):
                flush()
                current = [line]
            elif current:
                current.append(line)
        flush()
        return "\n".join(blocks), matched_paths, complete

    @staticmethod
    def _diff_context_ranges(diff_text: str, path: str) -> list[tuple[int, int]]:
        """Return bounded final-file line ranges around changed hunks."""
        normalized = str(path or "").replace("\\", "/").lstrip("./")
        current_matches = False
        ranges: list[tuple[int, int]] = []
        hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
        for line in str(diff_text or "").splitlines():
            if line.startswith("diff --git "):
                try:
                    tokens = shlex.split(line)
                    candidates = set()
                    for token in tokens[2:4]:
                        value = token[2:] if token.startswith(("a/", "b/")) else token
                        candidates.add(value.replace("\\", "/"))
                    current_matches = normalized in candidates
                except Exception:
                    current_matches = False
                continue
            if not current_matches:
                continue
            match = hunk_re.match(line)
            if not match:
                continue
            start = max(1, int(match.group(1)) - 24)
            count = max(1, int(match.group(2) or 1))
            end = start + min(count + 48, 120)
            if ranges and start <= ranges[-1][1] + 8:
                ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
            else:
                ranges.append((start, end))
            if len(ranges) >= 8:
                break
        return ranges

    @classmethod
    def _collect_review_snapshot(
        cls,
        runtime,
        goal,
        security: ExecutionSecurityContext,
        turn_id: str,
        review_paths: list[str],
    ) -> tuple[str, bool]:
        """Collect a policy/capability-governed review of only agent-mutated paths."""

        clean_paths = list(
            dict.fromkeys(
                (str(path).replace("\\", "/")[2:] if str(path).replace("\\", "/").startswith("./") else str(path).replace("\\", "/"))
                for path in review_paths
                if (
                    str(path).strip()
                    and not str(path).startswith("/")
                    and cls._is_reviewable_path(str(path))
                )
            )
        )
        if not clean_paths:
            return "", True
        complete = len(clean_paths) <= cls.MAX_REVIEW_FILES
        clean_paths = clean_paths[: cls.MAX_REVIEW_FILES]

        def execute(tool_name, args=None):
            return runtime.registry.execute_tool(
                tool_name,
                args or {},
                turn_id=turn_id,
                conversation_id=goal.conversation_id,
                workspace_id=runtime.workspace_id,
                origin="SCHEDULE",
                security_context=security,
            )

        status = execute("git_status")
        diff = execute("git_diff")
        status_text = str(getattr(status, "output", "") or "") if getattr(status, "success", False) else ""
        diff_text = str(getattr(diff, "output", "") or "") if getattr(diff, "success", False) else ""
        runner_limit = int(getattr(runtime.registry.process_runner, "max_output_bytes", 0) or 0)
        if bool(getattr(diff, "truncated", False)) or (
            runner_limit and len(diff_text.encode("utf-8")) >= runner_limit
        ):
            complete = False

        wanted = set(clean_paths)
        filtered_status = []
        for line in status_text.splitlines():
            raw = line[3:].strip() if len(line) >= 4 else ""
            candidates = {raw}
            if " -> " in raw:
                candidates.update(part.strip() for part in raw.split(" -> ", 1))
            if wanted & candidates:
                filtered_status.append(line)

        filtered_diff, diff_paths, diff_complete = cls._filter_diff_for_paths(diff_text, clean_paths)
        complete = complete and diff_complete
        blocks = []
        if filtered_status:
            blocks.append("[GIT STATUS — AGENT MUTATED PATHS]\n" + "\n".join(filtered_status))
        if filtered_diff.strip():
            blocks.append("[GIT DIFF — AGENT MUTATED PATHS]\n" + filtered_diff)

        # Tracked files are represented by their complete bounded diff plus only
        # local final-file context around changed hunks. New/untracked files have
        # no diff coverage, so they still require a full bounded read.
        for path in clean_paths:
            if path in diff_paths:
                ranges = cls._diff_context_ranges(filtered_diff, path)
                if not ranges:
                    ranges = [(1, 240)]
                for start_line, end_line in ranges:
                    result = execute(
                        "read_file",
                        {
                            "path": path,
                            "start_line": start_line,
                            "end_line": end_line,
                            "max_bytes": 8_000,
                        },
                    )
                    if not getattr(result, "success", False):
                        # The diff remains authoritative coverage for a tracked
                        # path, so missing supplemental context is fail-soft.
                        continue
                    body = str(getattr(result, "output", "") or "")
                    if body.strip():
                        blocks.append(
                            f"[CHANGED CONTEXT {path}:{start_line}-{end_line}]\n{body}"
                        )
                continue

            result = execute(
                "read_file",
                {
                    "path": path,
                    "start_line": 1,
                    "end_line": 3000,
                    "max_bytes": cls.MAX_REVIEW_FILE_CHARS,
                },
            )
            if not getattr(result, "success", False):
                complete = False
                blocks.append(f"[UNREADABLE MUTATED PATH {path}]")
                continue
            body = str(getattr(result, "output", "") or "")
            file_truncated = (
                bool(getattr(result, "truncated", False))
                or len(body) >= cls.MAX_REVIEW_FILE_CHARS
            )
            if file_truncated:
                complete = False
            label = "NEW FILE EXCERPT" if file_truncated else "NEW FILE"
            blocks.append(f"[{label} {path}]\n{body}")

        snapshot = "\n\n".join(blocks).strip()
        if len(snapshot) > cls.MAX_REVIEW_SNAPSHOT_CHARS:
            snapshot = snapshot[: cls.MAX_REVIEW_SNAPSHOT_CHARS] + "\n...[snapshot truncated]"
            complete = False
        return snapshot, complete

    def _build_reviewer(
        self,
        runtime,
        goal,
        completion_state,
        usage,
        *,
        route="adversarial-review",
        pass_index=1,
        turn_id="",
    ):
        if self.reviewer_factory is not None:
            return self.reviewer_factory(runtime, goal, completion_state, usage)

        profile_name, profile = self._review_profile(runtime, route)
        execution_name, execution_profile = runtime.processor.router.resolve_profile_for_task(
            "code-generation"
        )
        reviewer_identity = (
            str(getattr(profile, "backend", "") or "").casefold(),
            str(getattr(profile, "model", "") or ""),
            str(getattr(profile, "base_url", "") or ""),
        )
        execution_identity = (
            str(getattr(execution_profile, "backend", "") or "").casefold(),
            str(getattr(execution_profile, "model", "") or ""),
            str(getattr(execution_profile, "base_url", "") or ""),
        )
        usage["route"] = route
        usage["independent"] = reviewer_identity != execution_identity
        usage["execution_profile"] = execution_name
        iteration = int((completion_state or {}).get("iteration", 0) or 0) + 1

        def review_fn(system_prompt: str, user_prompt: str) -> str:
            clean_system, system_categories, system_redactions = self._redact_for_review(
                runtime, system_prompt
            )
            clean_user, user_categories, user_redactions = self._redact_for_review(
                runtime, user_prompt
            )
            categories = tuple(sorted(set(system_categories) | set(user_categories)))
            redactions = system_redactions + user_redactions
            backend = str(getattr(profile, "backend", "") or "").strip().lower()
            base_url = str(getattr(profile, "base_url", "") or "")
            host = urlparse(base_url).hostname
            is_local = profile_processing_is_local(profile)

            input_tokens = (
                TokenCounter.count_tokens(clean_system)
                + TokenCounter.count_tokens(clean_user)
            )
            if not is_local:
                policy = getattr(runtime, "egress_policy", None)
                if policy is None:
                    raise PermissionError("Remote adversarial review requires EgressPolicy")
                host = host or backend or "remote-provider"
                allowed, _manifest, reason = policy.evaluate_egress(
                    host=host,
                    is_local=False,
                    provider=backend,
                    model=str(getattr(profile, "model", "") or ""),
                    workspace_id=runtime.workspace_id,
                    bytes_out=len((clean_system + clean_user).encode("utf-8")),
                    estimated_tokens=input_tokens,
                    sensitive_categories=categories,
                    redaction_count=redactions,
                )
                if not allowed:
                    raise PermissionError(reason)

            execution_budget = getattr(
                runtime.processor, "execution_budgets", {}
            ).get(str(turn_id or ""))
            estimated_input_cost = estimate_execution_cost(
                str(getattr(profile, "model", "") or ""),
                input_tokens,
                0,
                backend=str(getattr(profile, "backend", "") or ""),
                workspace_root=str(runtime.canonical_root),
            ).estimated_usd
            provider_usage: dict[str, object] = {}

            def observe_usage(value):
                provider_usage.clear()
                provider_usage.update(dict(value or {}))

            def reserve_retry(attempt):
                if execution_budget is not None and int(attempt) > 0:
                    execution_budget.reserve_model_call(
                        input_tokens=input_tokens,
                        cost=estimated_input_cost,
                        stage=route,
                    )

            if execution_budget is not None:
                execution_budget.reserve_model_call(
                    input_tokens=input_tokens,
                    cost=estimated_input_cost,
                    stage=route,
                )

            with LLMClient(profile) as client:
                response = client.chat(
                    [{"role": "user", "content": clean_user}],
                    system_prompt=clean_system,
                    session_key=f"goal-review:{goal.id}:{iteration}:{route}:{pass_index}",
                    usage_callback=observe_usage,
                    attempt_callback=reserve_retry,
                )

            output_tokens = TokenCounter.count_tokens(response)
            actual_input = provider_usage.get("prompt_tokens")
            actual_output = provider_usage.get("completion_tokens")
            actual_input_tokens = (
                int(actual_input)
                if isinstance(actual_input, (int, float))
                and not isinstance(actual_input, bool)
                else input_tokens
            )
            actual_output_tokens = (
                int(actual_output)
                if isinstance(actual_output, (int, float))
                and not isinstance(actual_output, bool)
                else output_tokens
            )
            if execution_budget is not None:
                actual_input_cost = estimate_execution_cost(
                    str(getattr(profile, "model", "") or ""),
                    actual_input_tokens,
                    0,
                    backend=str(getattr(profile, "backend", "") or ""),
                    workspace_root=str(runtime.canonical_root),
                ).estimated_usd
                output_cost = estimate_execution_cost(
                    str(getattr(profile, "model", "") or ""),
                    0,
                    actual_output_tokens,
                    backend=str(getattr(profile, "backend", "") or ""),
                    workspace_root=str(runtime.canonical_root),
                ).estimated_usd
                execution_budget.reconcile_model_input(
                    estimated_tokens=input_tokens,
                    actual_tokens=actual_input_tokens,
                    estimated_cost=estimated_input_cost,
                    actual_cost=actual_input_cost,
                    stage=route,
                )
                execution_budget.record_model_output(
                    output_tokens=actual_output_tokens,
                    cost=output_cost,
                    stage=route,
                )
            cost = estimate_cost(
                str(getattr(profile, "model", "") or ""),
                actual_input_tokens,
                actual_output_tokens,
                workspace_root=str(runtime.canonical_root),
            )
            usage["tokens"] += actual_input_tokens + actual_output_tokens
            usage["cost"] += float(cost.estimated_usd)
            usage["profile"] = profile_name
            usage["model"] = str(getattr(profile, "model", "") or "")
            usage["redactions"] += redactions
            return response

        return AdversarialCodeReviewer(review_fn)

    def __call__(self, goal, *, lease_id=None, lease_owner_id=None):
        runtime = self.runtime_getter()
        state = RuntimeStateStore(
            runtime.database,
            runtime.workspace_id,
            goal.conversation_id,
        )
        resume = state.get(self._resume_key(goal.id))
        contract_item = runtime.goals.current_item(goal.id)
        step_goal = goal
        if contract_item is not None:
            step_goal = replace(
                goal,
                objective=contract_item.prompt,
                success_criteria=list(contract_item.criteria),
                gates=[],
            )
            completion_key = completion_state_key(
                f"{goal.id}:{contract_item.local_id}"
            )
        else:
            completion_key = completion_state_key(goal.id)
        completion_state = state.get(completion_key)
        if (
            contract_item is not None
            and contract_item.attempts == 1
            and contract_item.last_feedback is None
            and completion_state is not None
        ):
            state.delete(completion_key)
            completion_state = None
        completion = self._completion_engine(runtime, step_goal)

        prompt = step_goal.objective
        if contract_item is not None:
            progress = " | ".join(
                f"{item.local_id}:{item.status}"
                for item in runtime.goals.contract_items(goal.id)
            )
            prompt = (
                f"{prompt}\n\n[KITT CONTRACT PROGRESS]\n{progress}\n"
                "Work only on the current item. Do not redo DONE items."
            )
        if isinstance(resume, dict):
            approved_output = str(resume.get("tool_output") or "")[:32768]
            prompt = (
                "Continue the existing persistent goal after an approved host "
                "action. The approved action already succeeded; do not repeat "
                "it. Use only the remaining work for the current contract item.\n\n"
                f"Approved host result:\n{approved_output}\n\n"
                f"Current objective:\n{prompt}"
            )
        prompt = completion.build_execution_prompt(step_goal, prompt, completion_state)

        security = ExecutionSecurityContext(
            workspace_id=runtime.workspace_id,
            conversation_id=goal.conversation_id,
            turn_id="",
            origin="SCHEDULE",
            principal_type="GOAL",
            principal_id=goal.id,
            capabilities=frozenset(goal.capabilities),
            trace_id=f"goal:{goal.id}",
            fencing_token=lease_id,
            fencing_owner_id=lease_owner_id,
            fencing_subject_type="GOAL",
            fencing_subject_id=goal.id,
        )
        command = TurnCommand(
            conversation_id=goal.conversation_id,
            prompt=prompt,
            mode="auto",
            security_context=security,
        )
        result = {
            "status": "FAILED",
            "tokens": 0,
            "cost": 0.0,
            "turn_id": command.turn_id,
            "response": "",
            "resumed": bool(resume),
        }
        prior_review_paths = list((completion_state or {}).get("review_paths") or [])
        if isinstance(resume, dict):
            prior_review_paths.extend(list(resume.get("affected_paths") or []))
        current_review_paths = []
        pending_mutation_paths = {}
        for event in runtime.processor.run_turn(command):
            if isinstance(event, ToolStarted):
                paths = self._paths_from_tool_start(runtime, event)
                if paths:
                    pending_mutation_paths[event.call_id] = paths
            elif isinstance(event, ToolCompleted):
                paths = pending_mutation_paths.pop(event.call_id, [])
                if event.success and paths:
                    current_review_paths.extend(paths)
            elif isinstance(event, EditApplied):
                current_review_paths.extend(list(event.applied_files) + list(event.created_files))
            elif isinstance(event, MetricsRecorded):
                result["tokens"] += event.input_tokens + event.output_tokens
                result["cost"] += float(event.estimated_usd or 0.0)
            elif isinstance(event, ApprovalRequired):
                result.update(
                    status="WAITING_APPROVAL",
                    approval_id=event.approval_request_id,
                )
                return result
            elif isinstance(event, TurnCompleted):
                result.update(status="SUCCEEDED", response=event.response)
                edit_result = getattr(event, "edit_result", None)
                if edit_result is not None and getattr(edit_result, "success", False):
                    current_review_paths.extend(
                        list(getattr(edit_result, "applied_files", None) or [])
                        + list(getattr(edit_result, "created_files", None) or [])
                    )
            elif isinstance(event, TurnBlocked):
                result.update(status="BLOCKED", error=event.reason)
            elif isinstance(event, TurnFailed):
                result.update(status="FAILED", error=event.error)

        if result["status"] == "SUCCEEDED":
            review_paths = [
                path
                for path in dict.fromkeys([*prior_review_paths, *current_review_paths])
                if self._is_reviewable_path(path)
            ]
            contract_history_paths = []
            if contract_item is not None:
                for previous_item in runtime.goals.contract_items(goal.id):
                    if previous_item.status == "DONE":
                        contract_history_paths.extend(
                            list(previous_item.evidence.get("changed_paths") or [])
                        )
                if contract_item.kind == "final":
                    prior_review_paths.extend(contract_history_paths)
                review_paths = [
                    path
                    for path in dict.fromkeys([*prior_review_paths, *current_review_paths])
                    if self._is_reviewable_path(path)
                ]

            verification = completion.verify(step_goal, result["response"])
            contract_check_result = None
            if contract_item is not None:
                verification_paths = list(
                    dict.fromkeys(
                        [
                            *contract_item.paths,
                            *(
                                contract_history_paths
                                if contract_item.kind == "final"
                                else []
                            ),
                            *review_paths,
                        ]
                    )
                )
                contract_check_result = ContractCheckRunner(runtime).run(
                    goal,
                    contract_item,
                    verification_paths,
                )
                verification = completion.include_checks(
                    verification,
                    contract_check_result.checks,
                )
                result["contract_checks"] = contract_check_result.evidence
            risk_name = ""
            snapshot = ""
            snapshot_complete = True
            if verification.success and review_paths:
                snapshot, snapshot_complete = self._collect_review_snapshot(
                    runtime,
                    goal,
                    security,
                    command.turn_id,
                    review_paths,
                )
                if not snapshot:
                    snapshot = "[REVIEW SNAPSHOT UNAVAILABLE FOR RECORDED MUTATED PATHS]"
                    snapshot_complete = False

                assessment = classify_review_risk(review_paths, snapshot)
                risk_name = assessment.name
                result["review_risk"] = assessment.to_dict()

                if assessment.level != ReviewRisk.LOW and snapshot:
                    review_usage: dict[str, Any] = {
                        "tokens": 0,
                        "cost": 0.0,
                        "redactions": 0,
                        "passes": [],
                    }
                    reviews = []
                    pass_count = (
                        2 if assessment.level == ReviewRisk.CRITICAL else 1
                    )
                    for pass_index in range(1, pass_count + 1):
                        route = (
                            "critical-review"
                            if pass_index == 2
                            else "adversarial-review"
                        )
                        pass_usage: dict[str, Any] = {
                            "tokens": 0,
                            "cost": 0.0,
                            "redactions": 0,
                        }
                        reviewer = self._build_reviewer(
                            runtime,
                            step_goal,
                            completion_state,
                            pass_usage,
                            route=route,
                            pass_index=pass_index,
                            turn_id=command.turn_id,
                        )
                        review = reviewer.review(
                            objective=step_goal.objective,
                            success_criteria=list(
                                getattr(step_goal, "success_criteria", None) or []
                            ),
                            verification=verification,
                            change_snapshot=snapshot,
                            previous_feedback=str(
                                (completion_state or {}).get("feedback") or ""
                            ),
                            snapshot_complete=snapshot_complete,
                            risk_level=assessment.name,
                        )
                        reviews.append(review)
                        review_usage["tokens"] += int(
                            pass_usage.get("tokens", 0) or 0
                        )
                        review_usage["cost"] += float(
                            pass_usage.get("cost", 0.0) or 0.0
                        )
                        review_usage["redactions"] += int(
                            pass_usage.get("redactions", 0) or 0
                        )
                        review_usage["passes"].append(
                            {
                                "profile": pass_usage.get("profile"),
                                "model": pass_usage.get("model"),
                                "approved": review.approved,
                                "status": review.status,
                            }
                        )
                        if not review.approved:
                            break

                    review = combine_adversarial_reviews(reviews)
                    result["review"] = review.to_dict()
                    result["tokens"] += int(
                        review_usage.get("tokens", 0) or 0
                    )
                    result["cost"] += float(
                        review_usage.get("cost", 0.0) or 0.0
                    )
                    result["review_usage"] = review_usage
                    budget = getattr(
                        runtime.processor, "execution_budgets", {}
                    ).get(command.turn_id)
                    if budget is not None:
                        snapshots = getattr(
                            runtime.processor,
                            "execution_budget_snapshots",
                            None,
                        )
                        if snapshots is None:
                            snapshots = {}
                            runtime.processor.execution_budget_snapshots = snapshots
                        snapshots[command.turn_id] = budget.snapshot()
                    verification = completion.include_adversarial_review(
                        verification,
                        review,
                    )
                    events = getattr(runtime, "events", None)
                    if events is not None:
                        events.publish(
                            "GoalAdversarialReviewCompleted",
                            {
                                "goal_id": goal.id,
                                "approved": review.approved,
                                "status": review.status,
                                "risk": assessment.name,
                                "review_passes": len(reviews),
                                "required_findings": sum(
                                    1
                                    for item in review.findings
                                    if item.required
                                ),
                                "profiles": [
                                    item.get("profile")
                                    for item in review_usage["passes"]
                                ],
                                "models": [
                                    item.get("model")
                                    for item in review_usage["passes"]
                                ],
                            },
                        )
                else:
                    result["review"] = {
                        "applicable": False,
                        "approved": True,
                        "status": "SKIPPED_LOW_RISK",
                        "risk": assessment.name,
                        "summary": (
                            "Deterministic verification is authoritative "
                            "for this low-risk change."
                        ),
                    }

            if contract_item is not None and verification.success:
                if review_paths and not snapshot:
                    snapshot, snapshot_complete = self._collect_review_snapshot(
                        runtime,
                        goal,
                        security,
                        command.turn_id,
                        review_paths,
                    )
                validation_snapshot, _, snapshot_redactions = self._redact_for_review(
                    runtime,
                    snapshot,
                )
                deterministic_text = (
                    contract_check_result.evidence_text
                    if contract_check_result is not None
                    else "No host verification step was applicable."
                )
                deterministic_text, _, evidence_redactions = self._redact_for_review(
                    runtime,
                    deterministic_text,
                )
                validation, validation_tokens, validation_cost = ContractValidator(
                    runtime
                ).validate(
                    goal=goal,
                    item=contract_item,
                    deterministic_evidence=deterministic_text,
                    changed_paths=review_paths,
                    snapshot=validation_snapshot,
                )
                result["tokens"] += validation_tokens
                result["cost"] += validation_cost
                result["validation_redactions"] = snapshot_redactions + evidence_redactions
                result["contract_validation"] = {
                    "verdict": validation.verdict,
                    "evidence": list(validation.evidence),
                    "issues": list(validation.issues),
                }
                verification = completion.include_validation(verification, validation)

            result["verification"] = verification.to_dict()
            result["evidence"] = EvidenceLedger.from_verification(
                verification,
                review_risk=risk_name,
            ).to_dict()
            if verification.success:
                state.delete(completion_key)
                if resume is not None:
                    state.delete(self._resume_key(goal.id))
                if contract_item is not None:
                    result["status"] = "ITEM_DONE"
                    result["contract_item_id"] = contract_item.id
                    result["contract_evidence"] = {
                        "changed_paths": review_paths[: self.MAX_REVIEW_FILES],
                        "host_checks": result.get("contract_checks", {}),
                        "validation": result.get("contract_validation", {}),
                        "verification": verification.to_dict(),
                    }
            else:
                next_state = completion.next_state(completion_state, verification)
                next_state["review_paths"] = review_paths[: self.MAX_REVIEW_FILES]
                state.set(
                    completion_key,
                    next_state,
                    ttl_seconds=self.COMPLETION_STATE_TTL_SECONDS,
                )
                if next_state.get("review_exhausted"):
                    terminal_status = "REVIEW_EXHAUSTED"
                    terminal_error = (
                        "Adversarial review correction budget exhausted after "
                        f"{next_state.get('review_cycles')} cycle(s). "
                        + verification.feedback
                    )
                elif next_state.get("stagnation_exhausted"):
                    terminal_status = "STAGNATION_EXHAUSTED"
                    terminal_error = (
                        "Autonomous correction stopped because the same failure "
                        "repeated without measurable improvement. "
                        + verification.feedback
                    )
                else:
                    terminal_status = (
                        "ITEM_EXHAUSTED"
                        if contract_item is not None
                        and contract_item.attempts >= contract_item.max_attempts
                        else "INCOMPLETE"
                    )
                    terminal_error = verification.feedback
                result.update(
                    status=terminal_status,
                    error=terminal_error,
                    stagnated=bool(next_state.get("stagnated")),
                    completion_iteration=int(next_state.get("iteration", 0)),
                    review_cycles=int(next_state.get("review_cycles", 0)),
                )
        return result
