from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import List, Optional

from kitt.context_filter.prompt_budget import PromptBudget, TokenCounter
from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.core.execution_request import ExecutionRequest
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_helpers import (
    _attachment_path_key,
    _attachment_retrieval_prompt,
    _same_reverse_proxy_endpoint,
)
from kitt.domain.entities import ContextPlan, ModelProfile, SemanticTask
from kitt.formatting.contract import FormattingContractManager
from kitt.llm.client import LLMClient
from kitt.prompts import (
    CONTEXT_SUMMARY_SYSTEM as CONTEXT_SUMMARY_PROMPT,
    CONTEXT_SUMMARY_USER_TEMPLATE,
)


class TurnContextMixin:
    """Semantic filtering, retrieval and prompt construction phase."""

    @staticmethod
    def _needs_project_context(task, prompt: str) -> bool:
        return task.intent != "ASK" or any(term in prompt.lower() for term in ("projeto", "project", "repositório", "repository", "código", "codebase"))

    def _summarize_project_context(
        self, client: LLMClient, prompt: str, context_map: str,
        session_key: Optional[str] = None,
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
            if profile and "lfm" in profile.model.lower():
                lfm_profile = replace(
                    profile,
                    max_output_tokens=max(128, profile.max_output_tokens),
                    request_timeout_seconds=profile.request_timeout_seconds,
                )
                with LLMClient(lfm_profile) as summary_client:
                    summary = summary_client.chat(
                        [{"role": "user", "content": user_content}],
                        system_prompt=CONTEXT_SUMMARY_PROMPT,
                        session_key=session_key,
                    )
            else:
                summary = client.chat(
                    [{"role": "user", "content": user_content}],
                    system_prompt=CONTEXT_SUMMARY_PROMPT,
                    session_key=session_key,
                )
            summary = self._without_thinking(summary)[:6000] or fallback
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
        shared_reverse_proxy = (
            self.context_client is None
            and self.execution_client is None
            and _same_reverse_proxy_endpoint(ctx_profile, execution_profile)
        )
        if ctx_profile.max_output_tokens < 1024:
            ctx_profile = replace(ctx_profile, max_output_tokens=1024)
        sf_client = self.context_client
        semantic_filter = SemanticFilter(context_profile=ctx_profile, llm_client=sf_client)
        filter_res = semantic_filter.filter_and_plan(
            cmd.prompt,
            session_key=self._provider_session_key(ctx_profile, cmd.conversation_id),
            deterministic_only=shared_reverse_proxy,
        )
        sf_client = semantic_filter.llm_client
        task, plan = filter_res.task, filter_res.plan
        # The UI model occasionally labels explicit creation requests as a
        # conversational ASK (especially when prefixed with /ponytail).  Keep
        # workspace mutations autonomous by applying a small deterministic
        # safety override before tool planning.
        prompt_lower = cmd.prompt.lower()
        creation_request = (
            any(term in prompt_lower for term in (
                "crie", "criar", "create", "build", "implemente", "implementar",
                "gere", "gerar", "construa", "adicione",
            ))
            and any(term in prompt_lower for term in (
                "projeto", "pasta", "arquivo", "backend", "frontend", "front end",
            ))
        )
        if creation_request:
            if task.intent != "IMPLEMENT":
                task = replace(task, intent="IMPLEMENT", actions=["analyze", "edit"])
            plan.enabled_tools = list(dict.fromkeys([
                "create_directory", "write_file", "apply_patch", "read_file",
                "run_command", "repository_map", *plan.enabled_tools,
            ]))
            # Keep the emitted filter result consistent with the effective
            # task/plan consumed by the execution loop and daemon UI.
            filter_res.task = task
            filter_res.plan = plan
        agent_addressed = self._addresses_kitt(cmd.prompt)
        if cmd.mode == "plan":
            READ_ONLY_TOOLS = {"read_file", "search", "repository_map", "git_status", "git_diff", "list_files"}
            filtered_tools = [tool for tool in plan.enabled_tools if tool in READ_ONLY_TOOLS]
            plan.enabled_tools = filtered_tools if filtered_tools else ["read_file", "search", "repository_map"]
        elif "calculate" not in task.actions:
            plan.enabled_tools = [tool for tool in plan.enabled_tools if tool != "python_compute"]

        self.session_state.last_task = task
        self.session_state.last_plan = plan
        if cmd.explicit_files:
            self.working_set.touch_paths(cmd.conversation_id, cmd.explicit_files, cmd.turn_id, weight=2.0, kind="explicit")
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
                    sf_client, cmd.prompt, context_map_str,
                    session_key=self._provider_session_key(getattr(sf_client, "profile", None), cmd.conversation_id),
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

    def _build_system_prompt(self, cmd: TurnCommand, task: SemanticTask, plan: ContextPlan,
                             exe_profile: ModelProfile, context_map_str: str, explicit_str: str,
                             agents_str: str, skills_str: str, agent_addressed: bool,
                             workspace_id: str, budget: PromptBudget,
                             exposed_tools: Optional[List[str]] = None) -> tuple:
        mandatory_constraints = [c.text for c in task.constraints if c.mandatory]
        use_agent_prompt = bool(plan.enabled_tools) or agent_addressed
        tools_for_contract = exposed_tools if exposed_tools is not None else plan.enabled_tools
        if plan.enabled_tools:
            tool_contract = self._tool_instructions(
                tools_for_contract,
                planned_tools=plan.enabled_tools,
            )
            formatting_contract = FormattingContractManager(
                self.root_path
            ).prompt_summary()
            base_sys = (
                f"{'You are K.I.T.T., an autonomous coding agent.' if agent_addressed else 'Answer directly and concisely.'}\n\n"
                f"Tool Contract:\n{tool_contract}\n\n"
                f"Memory:\n{self.memory.get_memory_context(cmd.prompt)}\n\n"
                f"Active Skills:\n{skills_str}\n\n"
                f"Project Guidelines:\n{agents_str}\n\n"
                f"Formatting Contract:\n{formatting_contract}\n\n"
                f"Learned Harness:\n{self.harness_service.prompt(workspace_id, cmd.conversation_id, max_chars=self.config.max_harness_chars) if self.harness_service and self.history_service else ''}"
            ).strip()
        elif use_agent_prompt:
            base_sys = "You are K.I.T.T., the autonomous coding agent. Answer in one direct, concise sentence. Do not expose reasoning."
        else:
            base_sys = "Answer in one direct, concise sentence. Do not expose reasoning."

        # Single canonical task prompt decision (IR_ONLY / IR_PLUS_ORIGINAL / ORIGINAL):
        if plan.enabled_tools:
            if getattr(task, "confidence", 1.0) < 0.70:
                principal_task_prompt = cmd.prompt
            elif not plan.include_original_prompt and task.goal and task.intent != "UNKNOWN":
                principal_task_prompt = task.to_execution_prompt()
            elif task.goal and task.intent != "UNKNOWN" and task.confidence >= 0.70:
                principal_task_prompt = f"{task.to_execution_prompt()}\n\nOriginal Request:\n{cmd.prompt}"
            else:
                principal_task_prompt = cmd.prompt
        else:
            principal_task_prompt = cmd.prompt

        allocated = budget.allocate_context(
            system_prompt=base_sys,
            task_prompt=principal_task_prompt,
            mandatory_constraints=mandatory_constraints,
            repo_map=context_map_str,
            files_context=explicit_str,
            history_context=self._history_context(cmd.conversation_id, exclude_prompt=cmd.prompt),
            recent_results=""
        )

        constraints_part = f"Mandatory Constraints:\n{allocated['constraints_text']}\n\n" if allocated.get('constraints_text') else ""
        sys_prompt = (
            f"{allocated['system_prompt']}\n\n"
            f"{constraints_part}"
            f"Files Context:\n{allocated['files_context']}\n\n"
            f"Repo Map:\n{allocated['repo_map']}\n\n"
            f"Recent Conversation:\n{allocated['history_context']}"
        ).strip() if plan.enabled_tools else (
            f"{base_sys}\n\nProject context:\n{context_map_str}".strip() if context_map_str else base_sys
        )

        if cmd.mode == "plan":
            planning_instruction = (
                "\n\n[PLANNING MODE ACTIVE]\n"
                "You are operating in Planning Mode. Formulate a structured, actionable implementation plan.\n"
                "Do NOT modify files or propose write/patch tools in this turn.\n"
                "Inspect the codebase using available read tools (read_file, search, repository_map).\n"
                "Structure your final plan with:\n"
                "1. Context & Architecture Analysis: Summary of existing code and dependencies.\n"
                "2. Step-by-Step Implementation Steps: Atomic actions, targeted files, and symbol changes.\n"
                "3. Risk & Edge-Case Assessment: Potential regressions and mitigations.\n"
                "4. Verification & Testing Strategy: Exact test commands or reproduction steps."
            )
            sys_prompt = sys_prompt + planning_instruction

        request = ExecutionRequest(
            system_prompt=sys_prompt,
            messages=[{"role": "user", "content": principal_task_prompt}],
            enabled_tools=tools_for_contract,
            max_output_tokens=exe_profile.max_output_tokens,
            estimated_input_tokens=allocated["total_input_tokens"]
        )
        return sys_prompt, base_sys, allocated, request
