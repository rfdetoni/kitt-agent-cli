from __future__ import annotations

import hashlib
import inspect
import re
from difflib import SequenceMatcher
from dataclasses import replace
from typing import Any, Callable, List, Optional

from kitt.context_filter.prompt_budget import PromptBudget, TokenCounter
from kitt.context_filter.semantic_filter import SemanticFilter, llm_first_filter_result
from kitt.core.execution_request import ExecutionRequest
from kitt.core.roles import resolve_agent_role, restrict_tools
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_helpers import (
    _attachment_path_key,
    _attachment_retrieval_prompt,
    _reverse_proxy_identity,
)
from kitt.domain.entities import ContextPlan, ModelProfile, SemanticTask
from kitt.formatting.contract import FormattingContractManager
from kitt.context.envelope import (
    ContextEnvelopeBuilder,
    envelope_mapping,
    envelope_token_cost,
    lower_context_envelope,
)
from kitt.context.epoch import build_context_epoch, persist_context_epoch
from kitt.context.recovery import recoverable_text_body
from kitt.context.reconcile import reconcile_context_envelope
from kitt_protocol import CacheRegion, ContextKind, ContextStability, ContextTrust, RecoveryMode
from kitt.llm.client import LLMClient
from kitt.metrics.cost_estimator import estimate_execution_cost
from kitt.prompts import (
    CONTEXT_SUMMARY_SYSTEM as CONTEXT_SUMMARY_PROMPT,
    CONTEXT_SUMMARY_USER_TEMPLATE,
)



class TurnContextMixin:
    """Semantic filtering, retrieval and prompt construction phase."""

    # Composition contract supplied by TurnProcessor.
    root_path: Any
    config: Any
    router: Any
    context_client: Any
    execution_client: Any
    context_engine: Any
    context_resolver: Any
    deterministic_extractor: Any
    enable_context_summary: bool
    execution_budgets: dict[str, Any]
    session_state: Any
    working_set: Any
    skill_discovery: Any
    skill_loader: Any
    harness_service: Any
    history_service: Any
    memory: Any
    _cache_lock: Any
    _context_summary_cache: dict[str, str]
    _attachment_paths_by_turn: dict[str, Any]
    _agent_role_policies: dict[str, Any]
    _provider_session_key: Callable[..., str]
    _adaptive_retrieval_ratio_fn: Callable[..., float]
    _tool_definitions: Callable[..., Any]
    _history_context: Callable[..., Any]
    _without_thinking: Callable[[str], str]

    @staticmethod
    def _needs_project_context(task, prompt: str) -> bool:
        return task.intent != "ASK" or any(term in prompt.lower() for term in ("projeto", "project", "repositório", "repository", "código", "codebase"))

    def _summarize_project_context(
        self,
        client: LLMClient,
        prompt: str,
        context_map: str,
        session_key: Optional[str] = None,
        turn_id: str | None = None,
    ) -> str:
        if not context_map:
            return ""
        # The compiled context pack is already selected by value/token. Calling
        # a small model here would spend tokens to summarize a bounded package.
        if "## Context v" in context_map and TokenCounter.count_tokens(context_map) <= 4096:
            return context_map
        profile = getattr(client, "profile", None)
        cache_key = hashlib.sha256(
            f"{getattr(profile, 'model', '')}\0{prompt}\0{context_map}".encode("utf-8")
        ).hexdigest()
        with self._cache_lock:
            if cache_key in self._context_summary_cache:
                return self._context_summary_cache[cache_key]
        fallback = context_map[:2400]
        try:
            user_content = CONTEXT_SUMMARY_USER_TEMPLATE.format(
                prompt=prompt, context_map=context_map[:6000]
            )
            execution_budget = getattr(self, "execution_budgets", {}).get(
                str(turn_id or "")
            )
            estimated_input = (
                TokenCounter.count_tokens(CONTEXT_SUMMARY_PROMPT)
                + TokenCounter.count_tokens(user_content)
            )
            provider_usage: dict[str, object] = {}

            def observe_usage(usage: dict[str, object]) -> None:
                provider_usage.clear()
                provider_usage.update(dict(usage or {}))

            summary_profile = profile
            if profile and "lfm" in profile.model.lower():
                summary_profile = replace(
                    profile,
                    max_output_tokens=max(128, profile.max_output_tokens),
                    request_timeout_seconds=profile.request_timeout_seconds,
                )

            estimated_input_cost = estimate_execution_cost(
                str(getattr(summary_profile, "model", "") or ""),
                estimated_input,
                0,
                backend=str(getattr(summary_profile, "backend", "") or ""),
                workspace_root=str(self.root_path),
            ).estimated_usd
            if execution_budget is not None:
                execution_budget.reserve_model_call(
                    input_tokens=estimated_input,
                    cost=estimated_input_cost,
                    stage="condenser",
                )

            def reserve_condenser_retry(attempt: int) -> None:
                if execution_budget is not None and int(attempt) > 0:
                    execution_budget.reserve_model_call(
                        input_tokens=estimated_input,
                        cost=estimated_input_cost,
                        stage="condenser",
                    )

            def call_summary(target_client) -> str:
                kwargs = {
                    "system_prompt": CONTEXT_SUMMARY_PROMPT,
                    "session_key": session_key,
                    "attempt_callback": reserve_condenser_retry,
                    "usage_callback": observe_usage,
                }
                try:
                    signature = inspect.signature(target_client.chat)
                    has_var_kwargs = any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in signature.parameters.values()
                    )
                    if not has_var_kwargs:
                        kwargs = {
                            key: value
                            for key, value in kwargs.items()
                            if key in signature.parameters
                        }
                except (TypeError, ValueError):
                    pass
                return target_client.chat(
                    [{"role": "user", "content": user_content}],
                    **kwargs,
                )

            if summary_profile is not profile:
                with LLMClient(summary_profile) as summary_client:
                    summary = call_summary(summary_client)
            else:
                summary = call_summary(client)

            summary = self._without_thinking(summary)[:6000] or fallback
            if execution_budget is not None:
                actual_input_raw = provider_usage.get("prompt_tokens")
                actual_output_raw = provider_usage.get("completion_tokens")
                actual_input_tokens = (
                    int(actual_input_raw)
                    if isinstance(actual_input_raw, (int, float))
                    and not isinstance(actual_input_raw, bool)
                    else estimated_input
                )
                actual_output_tokens = (
                    int(actual_output_raw)
                    if isinstance(actual_output_raw, (int, float))
                    and not isinstance(actual_output_raw, bool)
                    else TokenCounter.count_tokens(summary)
                )
                actual_input_cost = estimate_execution_cost(
                    str(getattr(summary_profile, "model", "") or ""),
                    actual_input_tokens,
                    0,
                    backend=str(getattr(summary_profile, "backend", "") or ""),
                    workspace_root=str(self.root_path),
                ).estimated_usd
                output_cost = estimate_execution_cost(
                    str(getattr(summary_profile, "model", "") or ""),
                    0,
                    actual_output_tokens,
                    backend=str(getattr(summary_profile, "backend", "") or ""),
                    workspace_root=str(self.root_path),
                ).estimated_usd
                execution_budget.reconcile_model_input(
                    estimated_tokens=estimated_input,
                    actual_tokens=actual_input_tokens,
                    estimated_cost=estimated_input_cost,
                    actual_cost=actual_input_cost,
                    stage="condenser",
                )
                execution_budget.record_model_output(
                    output_tokens=actual_output_tokens,
                    cost=output_cost,
                    stage="condenser",
                )
        except Exception:
            summary = fallback
        with self._cache_lock:
            self._context_summary_cache[cache_key] = summary
            if len(self._context_summary_cache) > 32:
                self._context_summary_cache.pop(next(iter(self._context_summary_cache)))
        return summary

    def _source_context_excerpt(self, paths: List[str]) -> str:
        items = self.context_resolver.resolve_explicit_files(paths[:4], max_lines_per_file=80)
        text = "\n\n".join(item.content for item in items)
        return text[:2400]

    @staticmethod
    def _addresses_kitt(prompt: str) -> bool:
        return bool(re.search(r"\bk\.?i\.?t\.?t\.?\b", prompt, flags=re.IGNORECASE))

    def _run_semantic_filter(self, cmd: TurnCommand) -> tuple:
        ctx_profile_name, ctx_profile = self.router.resolve_profile_for_task("context-gather")
        _, execution_profile = self.router.resolve_profile_for_task("code-generation")
        execution_client_profile = getattr(self.execution_client, "profile", None)
        llm_first_reverse_proxy = (
            _reverse_proxy_identity(execution_profile) is not None
            or _reverse_proxy_identity(execution_client_profile) is not None
        )
        agent_addressed = self._addresses_kitt(cmd.prompt)

        if llm_first_reverse_proxy:
            filter_res = llm_first_filter_result(cmd.prompt)
            task, plan = filter_res.task, filter_res.plan
            if cmd.explicit_files:
                task = replace(task, paths=list(cmd.explicit_files))
                plan = replace(
                    plan,
                    preferred_paths=list(cmd.explicit_files),
                )
            if cmd.mode in {"plan", "ask"}:
                plan = replace(
                    plan,
                    enabled_tools=[
                        "read_file",
                        "search",
                        "repository_map",
                        "artifact_read",
                        "memory_recall",
                    ],
                )
            role_policy = resolve_agent_role(cmd, task)
            plan = replace(
                plan,
                enabled_tools=restrict_tools(
                    role_policy,
                    list(plan.enabled_tools),
                ),
            )
            roles = getattr(self, "_agent_role_policies", None)
            if roles is None:
                roles = {}
                self._agent_role_policies = roles
            roles[cmd.turn_id] = role_policy
            self.session_state.last_task = task
            self.session_state.last_plan = plan
            if cmd.explicit_files:
                self.working_set.touch_paths(
                    cmd.conversation_id,
                    cmd.explicit_files,
                    cmd.turn_id,
                    weight=2.0,
                    kind="explicit",
                )
            return task, plan, filter_res, None, ctx_profile, agent_addressed

        if ctx_profile.max_output_tokens < 1024:
            ctx_profile = replace(ctx_profile, max_output_tokens=1024)
        semantic_filter = SemanticFilter(
            context_profile=ctx_profile,
            llm_client=self.context_client,
        )
        execution_budget = getattr(self, "execution_budgets", {}).get(cmd.turn_id)
        classifier_input = TokenCounter.count_tokens(cmd.prompt) + 256
        classifier_input_cost = estimate_execution_cost(
            str(getattr(ctx_profile, "model", "") or ""),
            classifier_input,
            0,
            backend=str(getattr(ctx_profile, "backend", "") or ""),
            workspace_root=str(self.root_path),
        ).estimated_usd
        classifier_usage: dict[str, object] = {}

        def observe_classifier_usage(usage: dict[str, object]) -> None:
            classifier_usage.clear()
            classifier_usage.update(dict(usage or {}))

        def reserve_classifier_attempt(_attempt: int) -> None:
            if execution_budget is not None:
                execution_budget.reserve_model_call(
                    input_tokens=classifier_input,
                    cost=classifier_input_cost,
                    stage="classifier",
                )

        filter_res = semantic_filter.filter_and_plan(
            cmd.prompt,
            session_key=self._provider_session_key(ctx_profile, cmd.conversation_id),
            attempt_callback=reserve_classifier_attempt,
            usage_callback=observe_classifier_usage,
        )
        if execution_budget is not None and classifier_usage:
            actual_input_raw = classifier_usage.get("prompt_tokens")
            actual_output_raw = classifier_usage.get("completion_tokens")
            actual_input_tokens = (
                int(actual_input_raw)
                if isinstance(actual_input_raw, (int, float))
                and not isinstance(actual_input_raw, bool)
                else classifier_input
            )
            actual_output_tokens = (
                int(actual_output_raw)
                if isinstance(actual_output_raw, (int, float))
                and not isinstance(actual_output_raw, bool)
                else TokenCounter.count_tokens(str(filter_res))
            )
            actual_input_cost = estimate_execution_cost(
                str(getattr(ctx_profile, "model", "") or ""),
                actual_input_tokens,
                0,
                backend=str(getattr(ctx_profile, "backend", "") or ""),
                workspace_root=str(self.root_path),
            ).estimated_usd
            output_cost = estimate_execution_cost(
                str(getattr(ctx_profile, "model", "") or ""),
                0,
                actual_output_tokens,
                backend=str(getattr(ctx_profile, "backend", "") or ""),
                workspace_root=str(self.root_path),
            ).estimated_usd
            execution_budget.reconcile_model_input(
                estimated_tokens=classifier_input,
                actual_tokens=actual_input_tokens,
                estimated_cost=classifier_input_cost,
                actual_cost=actual_input_cost,
                stage="classifier",
            )
            execution_budget.record_model_output(
                output_tokens=actual_output_tokens,
                cost=output_cost,
                stage="classifier",
            )
        elif execution_budget is not None and getattr(filter_res, "source", "") == "LLM":
            # Injected clients may not report usage. The attempt callback still
            # reserved the call; conservatively charge the observed result text.
            execution_budget.record_model_output(
                output_tokens=TokenCounter.count_tokens(str(filter_res)),
                cost=estimate_execution_cost(
                    str(getattr(ctx_profile, "model", "") or ""),
                    0,
                    TokenCounter.count_tokens(str(filter_res)),
                    backend=str(getattr(ctx_profile, "backend", "") or ""),
                    workspace_root=str(self.root_path),
                ).estimated_usd,
                stage="classifier",
            )
        sf_client = semantic_filter.llm_client
        task, plan = filter_res.task, filter_res.plan

        if cmd.mode == "plan":
            read_only_tools = {
                "read_file", "search", "repository_map", "git_status", "git_diff", "list_files"
            }
            filtered_tools = [tool for tool in plan.enabled_tools if tool in read_only_tools]
            plan.enabled_tools = filtered_tools or ["read_file", "search", "repository_map"]
        elif "calculate" not in task.actions:
            plan.enabled_tools = [tool for tool in plan.enabled_tools if tool != "python_compute"]

        role_policy = resolve_agent_role(cmd, task)
        plan = replace(
            plan,
            enabled_tools=restrict_tools(
                role_policy,
                list(plan.enabled_tools),
            ),
        )
        roles = getattr(self, "_agent_role_policies", None)
        if roles is None:
            roles = {}
            self._agent_role_policies = roles
        roles[cmd.turn_id] = role_policy
        self.session_state.last_task = task
        self.session_state.last_plan = plan
        if cmd.explicit_files:
            self.working_set.touch_paths(
                cmd.conversation_id,
                cmd.explicit_files,
                cmd.turn_id,
                weight=2.0,
                kind="explicit",
            )
        return task, plan, filter_res, sf_client, ctx_profile, agent_addressed

    def _build_context(
        self,
        cmd: TurnCommand,
        task: SemanticTask,
        plan: ContextPlan,
        exe_profile: ModelProfile,
        sf_client: LLMClient,
    ) -> tuple:
        attachments = self._attachment_paths_by_turn.get(cmd.turn_id, ())
        if attachments:
            attachment_keys = {_attachment_path_key(path) for path in attachments}
            task = replace(
                task,
                paths=[
                    path for path in task.paths
                    if _attachment_path_key(path) not in attachment_keys
                ],
            )
            cmd = replace(
                cmd,
                prompt=_attachment_retrieval_prompt(cmd.prompt, attachments),
                explicit_files={
                    path for path in cmd.explicit_files
                    if _attachment_path_key(path) not in attachment_keys
                },
            )

        needs_project_context = bool(plan.enabled_tools) or (self.enable_context_summary and self._needs_project_context(task, cmd.prompt))
        working_paths = self.working_set.paths(cmd.conversation_id)
        diagnostics = self.deterministic_extractor.extract_diagnostics(cmd.prompt)
        query_elements = [
            *task.paths,
            *task.symbols,
        ]
        if task.goal:
            query_elements.append(task.goal)
        query_elements.extend(task.actions)
        query_elements.extend(diagnostics)
        if plan.include_original_prompt:
            query_elements.append(cmd.prompt)
        query_elements.extend(working_paths)
        context_query = " ".join(dict.fromkeys(query_elements)) if query_elements else cmd.prompt
        retrieval_ratio = getattr(self.config, "context_retrieval_token_ratio", 0.25)
        adaptive_fn = self._adaptive_retrieval_ratio_fn
        if adaptive_fn is not None:
            try:
                retrieval_ratio = adaptive_fn(self, task, cmd)
                self.session_state.adaptive_retrieval_ratio = retrieval_ratio
            except Exception:
                pass
        retrieval_ratio = max(0.05, min(float(retrieval_ratio), 0.75))
        max_retrieval_cap = getattr(self.config, "max_context_retrieval_tokens", 8192)
        retrieval_budget = min(
            max_retrieval_cap,
            max(1024, int(exe_profile.context_window * retrieval_ratio))
        )
        context_blocks = (
            self.context_engine.get_relevant_context(
                context_query,
                max_tokens=retrieval_budget,
                root_dir=str(self.root_path),
                working_set_paths=working_paths,
            )
            if needs_project_context else []
        )
        context_map_str = "\n\n".join(b.content for b in context_blocks)
        build_stats = getattr(self.context_engine, "last_build_stats", {}) if needs_project_context else {}

        if self.enable_context_summary:
            sources = self._source_context_excerpt([block.path for block in context_blocks])
            overview = []
            if any(term in cmd.prompt.lower() for term in ("projeto", "project")):
                overview = self.context_resolver.resolve_explicit_files(["README.md"], max_lines_per_file=80)
            context_map_str = "\n\n".join(part for part in (
                *(item.content for item in overview),
                f"Repository map:\n{context_map_str}" if context_map_str else "",
                f"Source excerpts:\n{sources}" if sources else "",
            ) if part)
            # A deterministic bypass leaves sf_client unset; keep the summarizer
            # LLM-free so it cannot reopen the reverse-proxy chat we deliberately skipped.
            if sf_client is not None:
                context_map_str = self._summarize_project_context(
                    sf_client,
                    cmd.prompt,
                    context_map_str,
                    session_key=self._provider_session_key(
                        getattr(sf_client, "profile", None),
                        cmd.conversation_id,
                    ),
                    turn_id=cmd.turn_id,
                )

        working_context = self.working_set.context(cmd.conversation_id)
        if working_context:
            context_map_str = f"Working Set:\n{working_context}\n\n{context_map_str}".strip()

        explicit_items = []
        if cmd.explicit_files:
            explicit_items = self.context_resolver.resolve_explicit_files(list(cmd.explicit_files))
        explicit_str = "\n\n".join(item.content for item in explicit_items)

        target_paths = list(dict.fromkeys([*(cmd.explicit_files or ()), *task.paths, *working_paths]))
        if (plan.enabled_tools or needs_project_context) and target_paths:
            seen_agents = set()
            agents_items = []
            for target_path in target_paths[:4]:
                for item in self.context_resolver.resolve_agents_instructions(target_path):
                    digest = hashlib.sha256(item.content.encode("utf-8")).hexdigest()
                    if digest not in seen_agents:
                        seen_agents.add(digest)
                        agents_items.append(item)
        else:
            agents_items = self.context_resolver.resolve_agents_instructions() if plan.enabled_tools else []
        agents_str = "\n\n".join(item.content for item in agents_items)
        if agents_str and not plan.enabled_tools:
            context_map_str = f"Project Guidelines:\n{agents_str}\n\n{context_map_str}".strip()

        discovery_dirs = []
        if self.config.persistence_enabled:
            discovery_dirs.append(self.root_path / ".kitt" / "skills")
        skills_found = self.skill_discovery.discover(discovery_dirs)
        selected_skills = self.skill_loader.select(
            skills_found,
            cmd.prompt,
            max_skills=self.config.max_skills_per_prompt,
        )
        skills_str = "\n\n".join(
            self.skill_loader.load(s, max_chars=self.config.max_skill_body_chars)
            for s in selected_skills
        ) if selected_skills else "No specific skills loaded."

        return context_map_str, explicit_str, agents_str, skills_str, context_blocks, explicit_items, build_stats, needs_project_context

    @staticmethod
    def _goal_preserves_original_request(task: SemanticTask, prompt: str) -> bool:
        """Skip a duplicate raw request only when the semantic goal is nearly identical."""
        original = " ".join(re.findall(r"\w+", str(prompt or "").casefold()))
        goal = " ".join(re.findall(r"\w+", str(task.goal or "").casefold()))
        if len(original) < 40 or len(goal) < 40:
            return False
        return SequenceMatcher(None, original, goal, autojunk=False).ratio() >= 0.90

    def _build_system_prompt(self, cmd: TurnCommand, task: SemanticTask, plan: ContextPlan,
                             exe_profile: ModelProfile, context_map_str: str, explicit_str: str,
                             agents_str: str, skills_str: str, agent_addressed: bool,
                             workspace_id: str, budget: PromptBudget,
                             exposed_tools: Optional[List[str]] = None,
                             execution_slice: Any = None) -> tuple:
        mandatory_constraints = [c.text for c in task.constraints if c.mandatory]
        use_agent_prompt = bool(plan.enabled_tools) or agent_addressed
        tools_for_contract = exposed_tools if exposed_tools is not None else plan.enabled_tools
        tool_definitions = self._tool_definitions(
            tools_for_contract,
            planned_tools=plan.enabled_tools,
        )

        if plan.enabled_tools:
            base_sys = (
                "You are K.I.T.T., an autonomous coding agent."
                if agent_addressed
                else "You are an execution agent. Follow the original user request and host policy."
            )
        elif use_agent_prompt:
            base_sys = (
                "You are K.I.T.T., the autonomous coding agent. "
                "Answer in one direct, concise sentence. Do not expose reasoning."
            )
        else:
            base_sys = "Answer in one direct, concise sentence. Do not expose reasoning."

        # Browser-backed execution is LLM-first: the original human request is
        # authoritative and is never replaced by a KITT-generated semantic summary.
        llm_first_proxy = _reverse_proxy_identity(exe_profile) is not None
        if llm_first_proxy:
            principal_task_prompt = cmd.prompt
        elif plan.enabled_tools:
            if getattr(task, "confidence", 1.0) < 0.70:
                principal_task_prompt = cmd.prompt
            elif not plan.include_original_prompt and task.goal and task.intent != "UNKNOWN":
                principal_task_prompt = task.to_execution_prompt()
            elif task.goal and task.intent != "UNKNOWN" and task.confidence >= 0.70:
                semantic_prompt = task.to_execution_prompt()
                principal_task_prompt = (
                    semantic_prompt
                    if self._goal_preserves_original_request(task, cmd.prompt)
                    else f"{semantic_prompt}\n\nOriginal Request:\n{cmd.prompt}"
                )
            else:
                principal_task_prompt = cmd.prompt
        else:
            principal_task_prompt = cmd.prompt

        history_context = self._history_context(
            cmd.conversation_id,
            exclude_prompt=cmd.prompt,
        )
        allocated = budget.allocate_context(
            system_prompt=base_sys,
            task_prompt=principal_task_prompt,
            mandatory_constraints=mandatory_constraints,
            repo_map=context_map_str,
            files_context=explicit_str,
            history_context=history_context,
            recent_results="",
        )

        loop_action_budget = max(
            1,
            int(getattr(self.config, "agent_loop_action_budget", 4)),
        )
        formatting_contract = (
            FormattingContractManager(self.root_path).prompt_summary(
                paths=[*(cmd.explicit_files or ()), *task.paths]
            )
            if plan.enabled_tools
            else ""
        )
        memory_context = ""
        if plan.enabled_tools:
            try:
                memory_context = self.memory.get_memory_context(
                    cmd.prompt,
                    max_tokens=600,
                    turn_id=cmd.turn_id,
                )
            except TypeError:
                # Preserve compatibility with injected test/facade memory providers
                # that implement the historical two-argument contract.
                memory_context = self.memory.get_memory_context(
                    cmd.prompt,
                    max_tokens=600,
                )
        harness_context = (
            self.harness_service.prompt(
                workspace_id,
                cmd.conversation_id,
                max_chars=self.config.max_harness_chars,
            )
            if plan.enabled_tools and self.harness_service and self.history_service
            else ""
        )
        tool_instructions = (
            self._tool_instructions(
                tools_for_contract,
                planned_tools=plan.enabled_tools,
            )
            if plan.enabled_tools
            else ""
        )
        execution_instruction = (
            execution_slice.render()
            if execution_slice is not None
            else ""
        )
        planning_instruction = (
            "Planning Mode is active. Inspect with read-only tools only. "
            "Return an actionable implementation plan with architecture context, "
            "atomic implementation steps, risks/edge cases and exact validation steps. "
            "Do not mutate files in this turn."
            if cmd.mode == "plan"
            else ""
        )

        context_budget = max(
            256,
            budget.max_input_tokens
            - TokenCounter.count_tokens(principal_task_prompt)
            - TokenCounter.count_tokens(base_sys)
            - 128,
        )
        context_epoch = build_context_epoch(
            self,
            conversation_id=cmd.conversation_id,
            turn_id=cmd.turn_id,
            memory_context=memory_context,
            harness_context=harness_context,
            repo_map=allocated.get("repo_map", ""),
            files_context=allocated.get("files_context", ""),
            guidelines_context=agents_str,
            skills_context=skills_str,
            tool_definitions=tool_definitions,
            policy_context={
                "formatting_contract": formatting_contract,
                "mandatory_constraints": mandatory_constraints,
                "execution_instruction": execution_instruction,
                "planning_instruction": planning_instruction,
                "loop_action_budget": loop_action_budget,
                "mode": cmd.mode,
            },
            provider_profile=exe_profile,
        )
        persist_context_epoch(
            self,
            context_epoch,
            conversation_id=cmd.conversation_id,
            turn_id=cmd.turn_id,
        )
        builder = ContextEnvelopeBuilder(
            epoch=context_epoch.epoch_id,
            max_tokens=context_budget,
        )
        builder.add(
            ContextKind.SYSTEM_INSTRUCTION,
            base_sys,
            source="kitt-agent-cli",
            trust=ContextTrust.TRUSTED,
            stability=ContextStability.BUILD,
            priority=100,
            recovery=RecoveryMode.RECOMPUTE,
            cache_region=CacheRegion.FROZEN_PREFIX,
            lifecycle="build",
        )
        builder.add(
            ContextKind.USER_INTENT,
            principal_task_prompt,
            source="user",
            trust=ContextTrust.TRUSTED,
            stability=ContextStability.TURN,
            priority=100,
            sensitivity="private",
            recovery=RecoveryMode.NONE,
            cache_region=CacheRegion.LIVE_ZONE,
            lifecycle="turn",
            ttl_turns=1,
        )
        if tool_definitions:
            builder.add(
                ContextKind.TOOL_SCHEMA,
                {
                    "definitions": tool_definitions,
                    "instructions": tool_instructions,
                },
                source="tool-registry",
                trust=ContextTrust.TRUSTED,
                stability=ContextStability.BUILD,
                priority=98,
                recovery=RecoveryMode.RECOMPUTE,
                cache_region=CacheRegion.FROZEN_PREFIX,
                lifecycle="build",
            )
        builder.add(
            ContextKind.OUTPUT_CONTRACT,
            {
                "instructions": "\n".join(
                    item for item in (
                        execution_instruction,
                        planning_instruction,
                        (
                            "Mandatory constraints:\n"
                            + "\n".join(f"- {item}" for item in mandatory_constraints)
                            if mandatory_constraints
                            else ""
                        ),
                    )
                    if item
                ),
                "loop_action_budget": loop_action_budget,
                "planning_mode": cmd.mode == "plan",
                "discovery_required": execution_slice is not None,
            },
            source="run-coordinator",
            trust=ContextTrust.TRUSTED,
            stability=ContextStability.TURN,
            priority=97,
            recovery=RecoveryMode.RECOMPUTE,
            cache_region=CacheRegion.LIVE_ZONE,
            lifecycle="turn",
            ttl_turns=1,
        )
        builder.add(
            ContextKind.MEMORY_RECALL,
            recoverable_text_body(
                self,
                memory_context,
                conversation_id=cmd.conversation_id,
                turn_id=cmd.turn_id,
                artifact_type="CONTEXT_MEMORY",
                summary="Exact memory context before prompt budgeting",
                sensitivity="PRIVATE",
            ),
            source="kitt-memoryd",
            trust=ContextTrust.TRUSTED,
            stability=ContextStability.SESSION,
            priority=94,
            sensitivity="private",
            recovery=RecoveryMode.SOURCE_REF,
            cache_region=CacheRegion.SESSION_PREFIX,
            lifecycle="session",
        )
        builder.add(
            ContextKind.HARNESS_KNOWLEDGE,
            recoverable_text_body(
                self,
                harness_context,
                conversation_id=cmd.conversation_id,
                turn_id=cmd.turn_id,
                artifact_type="CONTEXT_HARNESS",
                summary="Exact harness context before prompt budgeting",
            ),
            source="harness",
            trust=ContextTrust.TRUSTED,
            stability=ContextStability.SESSION,
            priority=86,
            recovery=RecoveryMode.SOURCE_REF,
            cache_region=CacheRegion.SESSION_PREFIX,
            lifecycle="session",
        )
        builder.add(
            ContextKind.SKILL,
            recoverable_text_body(
                self,
                skills_str if skills_str != "No specific skills loaded." else "",
                conversation_id=cmd.conversation_id,
                turn_id=cmd.turn_id,
                artifact_type="CONTEXT_SKILLS",
                summary="Exact skill context before prompt budgeting",
            ),
            source="workspace-skills",
            trust=ContextTrust.UNTRUSTED_WORKSPACE,
            stability=ContextStability.SESSION,
            priority=82,
            recovery=RecoveryMode.SOURCE_REF,
            cache_region=CacheRegion.SESSION_PREFIX,
            lifecycle="session",
        )
        builder.add(
            ContextKind.PROJECT_GUIDELINE,
            recoverable_text_body(
                self,
                agents_str,
                conversation_id=cmd.conversation_id,
                turn_id=cmd.turn_id,
                artifact_type="CONTEXT_GUIDELINES",
                summary="Exact project guidelines before prompt budgeting",
            ),
            source="workspace-guidelines",
            trust=ContextTrust.UNTRUSTED_WORKSPACE,
            stability=ContextStability.SESSION,
            priority=84,
            recovery=RecoveryMode.SOURCE_REF,
            cache_region=CacheRegion.SESSION_PREFIX,
            lifecycle="session",
        )
        builder.add(
            ContextKind.OUTPUT_CONTRACT,
            recoverable_text_body(
                self,
                formatting_contract,
                conversation_id=cmd.conversation_id,
                turn_id=cmd.turn_id,
                artifact_type="CONTEXT_FORMATTING",
                summary="Exact formatting contract before prompt budgeting",
            ),
            source="formatting-policy",
            trust=ContextTrust.UNTRUSTED_WORKSPACE,
            stability=ContextStability.SESSION,
            priority=80,
            recovery=RecoveryMode.RECOMPUTE,
            cache_region=CacheRegion.SESSION_PREFIX,
            lifecycle="session",
        )
        builder.add(
            ContextKind.FILE_EVIDENCE,
            recoverable_text_body(
                self,
                allocated.get("files_context", ""),
                conversation_id=cmd.conversation_id,
                turn_id=cmd.turn_id,
                artifact_type="CONTEXT_FILE_EVIDENCE",
                summary="Exact file evidence before prompt budgeting",
            ),
            source="repository",
            trust=ContextTrust.UNTRUSTED_WORKSPACE,
            stability=ContextStability.TURN,
            priority=78,
            recovery=RecoveryMode.SOURCE_REF,
            cache_region=CacheRegion.LIVE_ZONE,
            lifecycle="turn",
            ttl_turns=1,
        )
        builder.add(
            ContextKind.REPOSITORY_MAP,
            recoverable_text_body(
                self,
                allocated.get("repo_map", ""),
                conversation_id=cmd.conversation_id,
                turn_id=cmd.turn_id,
                artifact_type="CONTEXT_REPOSITORY_MAP",
                summary="Exact repository map before prompt budgeting",
            ),
            source="repository",
            trust=ContextTrust.UNTRUSTED_WORKSPACE,
            stability=ContextStability.TURN,
            priority=76,
            recovery=RecoveryMode.RECOMPUTE,
            cache_region=CacheRegion.LIVE_ZONE,
            lifecycle="turn",
            ttl_turns=1,
        )
        builder.add(
            ContextKind.SEARCH_EVIDENCE,
            recoverable_text_body(
                self,
                allocated.get("history_context", ""),
                conversation_id=cmd.conversation_id,
                turn_id=cmd.turn_id,
                artifact_type="CONTEXT_HISTORY",
                summary="Exact history context before prompt budgeting",
                sensitivity="PRIVATE",
            ),
            source="conversation-ledger",
            trust=ContextTrust.EXTERNAL,
            stability=ContextStability.SESSION,
            priority=72,
            sensitivity="private",
            recovery=RecoveryMode.SOURCE_REF,
            cache_region=CacheRegion.LIVE_ZONE,
            lifecycle="session",
        )
        context_envelope = builder.build()
        reconciliation, cache_plan = reconcile_context_envelope(
            self,
            context_envelope,
            conversation_id=cmd.conversation_id,
            turn_id=cmd.turn_id,
            provider_profile=exe_profile,
        )
        allocated["context_reconciliation"] = reconciliation
        allocated["context_cache_plan"] = cache_plan

        # Reverse-proxy requests carry the typed envelope as data. Text-only
        # providers receive a deterministic one-way lowering of the same IR.
        has_project_context = bool(
            context_map_str.strip()
            or explicit_str.strip()
            or agents_str.strip()
        )
        sys_prompt = (
            base_sys
            if llm_first_proxy or (not plan.enabled_tools and not has_project_context)
            else lower_context_envelope(context_envelope)
        )
        allocated["total_input_tokens"] = (
            TokenCounter.count_tokens(principal_task_prompt)
            + (
                envelope_token_cost(context_envelope)
                if llm_first_proxy
                else TokenCounter.count_tokens(sys_prompt)
            )
        )
        allocated["context_envelope_tokens"] = envelope_token_cost(context_envelope)

        request = ExecutionRequest(
            system_prompt=sys_prompt,
            messages=[{"role": "user", "content": principal_task_prompt}],
            enabled_tools=tools_for_contract,
            tool_definitions=tool_definitions,
            max_output_tokens=exe_profile.max_output_tokens,
            estimated_input_tokens=allocated["total_input_tokens"],
            agent_role=str(
                getattr(
                    getattr(self, "_agent_role_policies", {}).get(cmd.turn_id),
                    "role",
                    "",
                )
                or ""
            ),
            loop_action_budget=loop_action_budget,
            context_envelope=envelope_mapping(context_envelope),
        )
        return sys_prompt, base_sys, allocated, request

