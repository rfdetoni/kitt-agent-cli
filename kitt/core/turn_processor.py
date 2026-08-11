import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Callable
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
        self.budget = PromptBudget(window_size=8192, reserved_output=1200)
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

    def process(self, user_prompt: str, explicit_files: Optional[Set[str]] = None) -> Dict[str, Any]:
        self.session_state.current_prompt = user_prompt

        # 1. Semantic Filter
        ctx_profile_name, ctx_profile = self.router.resolve_profile_for_task("context-gather")
        sf_client = self.context_client or LLMClient(ctx_profile)
        semantic_filter = SemanticFilter(context_profile=ctx_profile, llm_client=sf_client)
        filter_res = semantic_filter.filter_and_plan(user_prompt)
        task, plan = filter_res.task, filter_res.plan
        self.session_state.last_task = task
        self.session_state.last_plan = plan

        self._emit("FilterCompleted", {"filter_res": filter_res})

        # 2. Context Engine retrieval
        context_blocks = self.context_engine.get_relevant_context(user_prompt, max_tokens=2048, root_dir=str(self.root_path))
        context_map_str = "\n\n".join(b.content for b in context_blocks)

        explicit_items = []
        if explicit_files:
            explicit_items = self.context_resolver.resolve_explicit_files(list(explicit_files))
        explicit_str = "\n\n".join(item.content for item in explicit_items)

        mandatory_constraints = [c.text for c in task.constraints if c.mandatory]

        base_sys = f"You are K.I.T.T., an advanced autonomous AI coding assistant.\n\nMemory:\n{self.memory.get_memory_context()}\n\nSkills:\n{self.skill_manager.get_skills_summary_prompt()}"

        # 3. Prompt Budgeting
        allocated = self.budget.allocate_context(
            system_prompt=base_sys,
            task_prompt=user_prompt,
            mandatory_constraints=mandatory_constraints,
            repo_map=context_map_str,
            files_context=explicit_str,
            history_context="",
            recent_results=""
        )

        self._emit("BudgetApplied", {"allocated": allocated})

        sys_prompt = f"{allocated['system_prompt']}\n\nFiles Context:\n{allocated['files_context']}\n\nRepo Map:\n{allocated['repo_map']}".strip()

        # 4. Resolve Execution Profile
        exe_profile_name, exe_profile = self.router.resolve_profile_for_task("code-generation")
        self._emit("ModelSelected", {"profile_name": exe_profile_name, "model": exe_profile.model})

        request = ExecutionRequest(
            system_prompt=sys_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            enabled_tools=plan.enabled_tools,
            max_output_tokens=exe_profile.max_output_tokens
        )

        return {
            "filter_res": filter_res,
            "allocated": allocated,
            "request": request,
            "exe_profile_name": exe_profile_name,
            "exe_profile": exe_profile
        }

    def execute_full_turn(self, user_prompt: str, explicit_files: Optional[Set[str]] = None) -> Dict[str, Any]:
        prep = self.process(user_prompt, explicit_files=explicit_files)
        request: ExecutionRequest = prep["request"]
        exe_profile: ModelProfile = prep["exe_profile"]

        exe_client = self.execution_client or LLMClient(exe_profile)
        response_text = exe_client.chat(request.messages, system_prompt=request.system_prompt)

        blocks = self.diff_parser.parse(response_text)
        edit_result: Optional[EditResult] = None
        if blocks:
            edit_result = self.diff_applier.apply(blocks, root_dir=str(self.root_path))
            if edit_result.success:
                self.session_state.last_changeset = edit_result.changeset
                self._emit("EditApplied", {"applied": edit_result.applied_files, "created": edit_result.created_files})

        self._emit("TurnCompleted", {"response": response_text, "edit_result": edit_result})

        return {
            "prep": prep,
            "response": response_text,
            "edit_result": edit_result
        }
