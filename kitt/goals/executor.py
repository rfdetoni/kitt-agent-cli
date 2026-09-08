from __future__ import annotations

import shlex
from pathlib import PurePosixPath
from urllib.parse import urlparse

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
from kitt.goals.gates import QualityGateRunner
from kitt.goals.review import AdversarialCodeReviewer
from kitt.llm.client import LLMClient
from kitt.metrics.cost_estimator import estimate_cost
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
            command = shlex.join(list(argv))
            return runtime.policy.evaluate_tool(
                "run_command",
                {"command": command},
                origin="SCHEDULE",
                conversation_id=goal.conversation_id,
            )

        return AutonomousCompletionEngine(
            gate_runner=QualityGateRunner(runtime.registry.process_runner),
            gate_result_recorder=runtime.goals.record_gate_result,
            gate_authorizer=authorize_gate,
        )

    @staticmethod
    def _review_profile(runtime):
        """Prefer an explicit reviewer route, otherwise use the execution model."""
        router = runtime.processor.router
        configured = str(router.config.routing.get("adversarial-review") or "").strip()
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
            inner = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
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
        current = []
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

        # Read final content as additional context even when a diff exists. For a
        # large tracked file, the bounded diff still covers the mutation, so a
        # truncated final-file excerpt does not by itself make review incomplete.
        # New/untracked files have no diff coverage and therefore must be fully readable.
        for path in clean_paths:
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
                if path not in diff_paths:
                    complete = False
                blocks.append(f"[UNREADABLE MUTATED PATH {path}]")
                continue
            body = str(getattr(result, "output", "") or "")
            file_truncated = bool(getattr(result, "truncated", False)) or len(body) >= cls.MAX_REVIEW_FILE_CHARS
            if file_truncated and path not in diff_paths:
                complete = False
            label = "FINAL FILE EXCERPT" if file_truncated else "FINAL FILE"
            blocks.append(f"[{label} {path}]\n{body}")

        snapshot = "\n\n".join(blocks).strip()
        if len(snapshot) > cls.MAX_REVIEW_SNAPSHOT_CHARS:
            snapshot = snapshot[: cls.MAX_REVIEW_SNAPSHOT_CHARS] + "\n...[snapshot truncated]"
            complete = False
        return snapshot, complete

    def _build_reviewer(self, runtime, goal, completion_state, usage):
        if self.reviewer_factory is not None:
            return self.reviewer_factory(runtime, goal, completion_state, usage)

        profile_name, profile = self._review_profile(runtime)
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
            loopback = host in {None, "", "localhost", "127.0.0.1", "::1"}
            is_local = backend in LLMClient.LOCAL_BACKENDS and loopback

            if not is_local:
                policy = getattr(runtime, "egress_policy", None)
                if policy is None:
                    raise PermissionError("Remote adversarial review requires EgressPolicy")
                host = host or backend or "remote-provider"
                input_tokens = TokenCounter.count_tokens(clean_system) + TokenCounter.count_tokens(clean_user)
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

            with LLMClient(profile) as client:
                response = client.chat(
                    [{"role": "user", "content": clean_user}],
                    system_prompt=clean_system,
                    session_key=f"goal-review:{goal.id}:{iteration}",
                )

            input_tokens = TokenCounter.count_tokens(clean_system) + TokenCounter.count_tokens(clean_user)
            output_tokens = TokenCounter.count_tokens(response)
            cost = estimate_cost(
                str(getattr(profile, "model", "") or ""),
                input_tokens,
                output_tokens,
                workspace_root=str(runtime.canonical_root),
            )
            usage["tokens"] += input_tokens + output_tokens
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
        completion_key = completion_state_key(goal.id)
        completion_state = state.get(completion_key)
        completion = self._completion_engine(runtime, goal)

        prompt = goal.objective
        if isinstance(resume, dict):
            approved_output = str(resume.get("tool_output") or "")[:32768]
            prompt = (
                "Continue the existing persistent goal after an approved host "
                "action. The approved action already succeeded; do not repeat "
                "it. Use the existing conversation/history and complete only "
                "the remaining work.\n\n"
                f"Approved host result:\n{approved_output}\n\n"
                f"Original objective:\n{goal.objective}"
            )
        prompt = completion.build_execution_prompt(goal, prompt, completion_state)

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
            verification = completion.verify(goal, result["response"])
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
                if snapshot:
                    review_usage = {"tokens": 0, "cost": 0.0, "redactions": 0}
                    reviewer = self._build_reviewer(
                        runtime,
                        goal,
                        completion_state,
                        review_usage,
                    )
                    review = reviewer.review(
                        objective=goal.objective,
                        success_criteria=list(getattr(goal, "success_criteria", None) or []),
                        verification=verification,
                        change_snapshot=snapshot,
                        previous_feedback=str((completion_state or {}).get("feedback") or ""),
                        snapshot_complete=snapshot_complete,
                    )
                    result["review"] = review.to_dict()
                    result["tokens"] += int(review_usage.get("tokens", 0) or 0)
                    result["cost"] += float(review_usage.get("cost", 0.0) or 0.0)
                    if review_usage:
                        result["review_usage"] = dict(review_usage)
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
                                "required_findings": sum(
                                    1 for item in review.findings if item.required
                                ),
                                "profile": review_usage.get("profile"),
                                "model": review_usage.get("model"),
                            },
                        )

            result["verification"] = verification.to_dict()
            if verification.success:
                state.delete(completion_key)
                if resume is not None:
                    state.delete(self._resume_key(goal.id))
            else:
                next_state = completion.next_state(completion_state, verification)
                next_state["review_paths"] = review_paths[: self.MAX_REVIEW_FILES]
                state.set(
                    completion_key,
                    next_state,
                    ttl_seconds=self.COMPLETION_STATE_TTL_SECONDS,
                )
                result.update(
                    status="INCOMPLETE",
                    error=verification.feedback,
                    stagnated=bool(next_state.get("stagnated")),
                    completion_iteration=int(next_state.get("iteration", 0)),
                )
        return result
