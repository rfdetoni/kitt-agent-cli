import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Callable, Iterator
from kitt.domain.entities import TaskStep, ModelProfile, EditResult, ChangeSet
from kitt.router.router import TaskRouter
from kitt.memory.memory_manager import MemoryManager
from kitt.skills.skill_manager import SkillManager
from kitt.context_engine.engine import ContextEngine
from kitt.context_filter.semantic_filter import SemanticFilter, SemanticFilterResult
from kitt.context_filter.context_resolver import ContextResolver
from kitt.context_filter.prompt_budget import PromptBudget
from kitt.edit_format.parser import SearchReplaceParser
from kitt.edit_format.applier import DiffApplier
from kitt.tools.build_detector import BuildDetector
from kitt.tools.log_reducer import LogReducer
from kitt.tools.agent_loop import AgentLoop
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry
from kitt.llm.client import LLMClient
from kitt.core.session_state import SessionState
from kitt.core.execution_request import ExecutionRequest
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    TurnEvent, TurnStarted, FilterCompleted, ContextResolved, BudgetApplied,
    ModelSelected, TextDelta, ApprovalRequired, ToolStarted, ToolCompleted,
    EditApplied, ValidationCompleted, MetricsRecorded, TurnCompleted, TurnFailed
)

class TurnProcessor:
    """Decoupled core turn processing engine for K.I.T.T."""

    def __init__(
        self,
        root_dir: str = ".",
        context_client: Optional[LLMClient] = None,
        execution_client: Optional[LLMClient] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ):
        self.root_path = Path(root_dir).resolve()
        self.router = TaskRouter(root_dir=root_dir)
        self.memory = MemoryManager(root_dir=root_dir)
        self.skill_manager = SkillManager(root_dir=root_dir)
        self.context_engine = ContextEngine()
        self.context_resolver = ContextResolver(root_dir=root_dir)
        self.diff_parser = SearchReplaceParser()
        self.diff_applier = DiffApplier()
        self.build_detector = BuildDetector(root_dir=root_dir)
        self.log_reducer = LogReducer()
        self.registry = ToolRegistry(root_dir=root_dir)
        self.session_state = SessionState()

        self.context_client = context_client
        self.execution_client = execution_client
        self.event_callback = event_callback

    def _emit(self, event_name: str, payload: Dict[str, Any]):
        if self.event_callback:
            self.event_callback(event_name, payload)

    def run_turn(self, cmd: TurnCommand) -> Iterator[TurnEvent]:
        start_ev = TurnStarted(turn_id=cmd.turn_id, conversation_id=cmd.conversation_id, prompt=cmd.prompt)
        self._emit("TurnStarted", {"turn_id": cmd.turn_id})
        yield start_ev

        try:
            self.session_state.current_prompt = cmd.prompt

            # 1. Semantic Filter
            ctx_profile_name, ctx_profile = self.router.resolve_profile_for_task("context-gather")
            sf_client = self.context_client or LLMClient(ctx_profile)
            semantic_filter = SemanticFilter(context_profile=ctx_profile, llm_client=sf_client)
            filter_res = semantic_filter.filter_and_plan(cmd.prompt)
            task, plan = filter_res.task, filter_res.plan
            self.session_state.last_task = task
            self.session_state.last_plan = plan

            self._emit("FilterCompleted", {"filter_res": filter_res})
            yield FilterCompleted(filter_res=filter_res)

            # 2. Resolve Execution Profile
            exe_profile_name, exe_profile = self.router.resolve_profile_for_task("code-generation")
            self._emit("ModelSelected", {"profile_name": exe_profile_name, "model": exe_profile.model})
            yield ModelSelected(profile_name=exe_profile_name, model=exe_profile.model)

            budget = PromptBudget(
                window_size=exe_profile.context_window,
                reserved_output=exe_profile.max_output_tokens
            )

            # 3. Context Engine & AGENTS.md retrieval
            context_blocks = self.context_engine.get_relevant_context(cmd.prompt, max_tokens=2048, root_dir=str(self.root_path))
            context_map_str = "\n\n".join(b.content for b in context_blocks)

            explicit_items = []
            if cmd.explicit_files:
                explicit_items = self.context_resolver.resolve_explicit_files(list(cmd.explicit_files))
            explicit_str = "\n\n".join(item.content for item in explicit_items)

            agents_items = self.context_resolver.resolve_agents_instructions()
            agents_str = "\n\n".join(item.content for item in agents_items)

            yield ContextResolved(resolved_count=len(context_blocks) + len(explicit_items))

            mandatory_constraints = [c.text for c in task.constraints if c.mandatory]

            base_sys = (
                f"You are K.I.T.T., an advanced autonomous AI coding assistant.\n\n"
                f"Memory:\n{self.memory.get_memory_context()}\n\n"
                f"Skills:\n{self.skill_manager.get_skills_summary_prompt()}\n\n"
                f"Project Guidelines:\n{agents_str}"
            ).strip()

            # 4. Prompt Budgeting
            allocated = budget.allocate_context(
                system_prompt=base_sys,
                task_prompt=cmd.prompt,
                mandatory_constraints=mandatory_constraints,
                repo_map=context_map_str,
                files_context=explicit_str,
                history_context="",
                recent_results=""
            )

            self._emit("BudgetApplied", {"allocated": allocated})
            yield BudgetApplied(
                total_input_tokens=allocated["total_input_tokens"],
                reserved_output_tokens=allocated["reserved_output_tokens"],
                window_size=exe_profile.context_window
            )

            sys_prompt = (
                f"{allocated['system_prompt']}\n\n"
                f"Task & Constraints:\n{allocated['task_str']}\n\n"
                f"Files Context:\n{allocated['files_context']}\n\n"
                f"Repo Map:\n{allocated['repo_map']}"
            ).strip()

            request = ExecutionRequest(
                system_prompt=sys_prompt,
                messages=[{"role": "user", "content": cmd.prompt}],
                enabled_tools=plan.enabled_tools,
                max_output_tokens=exe_profile.max_output_tokens,
                estimated_input_tokens=allocated["total_input_tokens"]
            )

            exe_client = self.execution_client or LLMClient(exe_profile)

            if cmd.dry_run:
                yield TurnCompleted(response="[Dry Run Completed]", edit_result=None)
                return

            full_response = ""
            for chunk in exe_client.chat_stream(request.messages, system_prompt=request.system_prompt):
                full_response += chunk
                yield TextDelta(delta=chunk)

            # 5. Parse & Apply edits if present
            blocks = self.diff_parser.parse(full_response)
            edit_result: Optional[EditResult] = None
            if blocks:
                action_hash = self.registry.policy.generate_action_hash("apply_patch", {"patch": full_response})
                if not cmd.approval_grant:
                    perm = self.registry.policy.evaluate_tool("apply_patch")
                    if perm == 'ASK':
                        yield ApprovalRequired(
                            turn_id=cmd.turn_id,
                            tool_name="apply_patch",
                            args={"patch": full_response},
                            action_hash=action_hash
                        )
                        return

                edit_result = self.diff_applier.apply(blocks, root_dir=str(self.root_path))
                if edit_result.success:
                    self.session_state.last_changeset = edit_result.changeset
                    self._emit("EditApplied", {"applied": edit_result.applied_files, "created": edit_result.created_files})
                    yield EditApplied(applied_files=edit_result.applied_files, created_files=edit_result.created_files)

            self._emit("TurnCompleted", {"response": full_response, "edit_result": edit_result})
            yield TurnCompleted(response=full_response, edit_result=edit_result)

        except Exception as e:
            yield TurnFailed(error=str(e))

    def process(self, user_prompt: str, explicit_files: Optional[Set[str]] = None) -> Dict[str, Any]:
        cmd = TurnCommand(conversation_id="default", prompt=user_prompt, explicit_files=explicit_files or set())
        prep = {}
        for event in self.run_turn(cmd):
            if isinstance(event, FilterCompleted):
                prep["filter_res"] = event.filter_res
            elif isinstance(event, BudgetApplied):
                prep["allocated"] = {
                    "total_input_tokens": event.total_input_tokens,
                    "reserved_output_tokens": event.reserved_output_tokens
                }
            elif isinstance(event, ModelSelected):
                prep["exe_profile_name"] = event.profile_name
                profile_name, profile = self.router.resolve_profile_for_task("code-generation")
                prep["exe_profile"] = profile
        
        ctx = prep.get("exe_profile")
        if ctx:
            req = ExecutionRequest(
                system_prompt="You are K.I.T.T.",
                messages=[{"role": "user", "content": user_prompt}],
                enabled_tools=prep["filter_res"].plan.enabled_tools,
                max_output_tokens=ctx.max_output_tokens,
                estimated_input_tokens=prep["allocated"]["total_input_tokens"]
            )
            prep["request"] = req
        return prep

    def execute_full_turn(self, user_prompt: str, explicit_files: Optional[Set[str]] = None) -> Dict[str, Any]:
        cmd = TurnCommand(conversation_id="default", prompt=user_prompt, explicit_files=explicit_files or set())
        full_response = ""
        edit_res = None
        prep = self.process(user_prompt, explicit_files=explicit_files)

        for event in self.run_turn(cmd):
            if isinstance(event, TextDelta):
                full_response += event.delta
            elif isinstance(event, ApprovalRequired):
                cmd.approval_grant = self.registry.issue_approval_grant(cmd.turn_id, event.tool_name, event.args)
                # Retry turn loop with approval grant
                for sub_ev in self.run_turn(cmd):
                    if isinstance(sub_ev, TextDelta):
                        full_response += sub_ev.delta
                    elif isinstance(sub_ev, TurnCompleted):
                        full_response = sub_ev.response
                        edit_res = sub_ev.edit_result
            elif isinstance(event, TurnCompleted):
                full_response = event.response
                edit_res = event.edit_result

        return {
            "prep": prep,
            "response": full_response,
            "edit_result": edit_res
        }
