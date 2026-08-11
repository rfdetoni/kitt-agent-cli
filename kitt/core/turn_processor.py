import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from kitt.domain.entities import TaskStep, ModelProfile, EditResult, ChangeSet
from kitt.router.router import TaskRouter
from kitt.memory.memory_manager import MemoryManager
from kitt.skills.skill_manager import SkillManager
from kitt.context_engine.engine import ContextEngine
from kitt.context_filter.semantic_filter import SemanticFilter, SemanticFilterResult
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

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()
        self.router = TaskRouter(root_dir=root_dir)
        self.memory = MemoryManager(root_dir=root_dir)
        self.skill_manager = SkillManager(root_dir=root_dir)
        self.context_engine = ContextEngine()
        self.budget = PromptBudget(window_size=8192, reserved_output=1200)
        self.diff_parser = SearchReplaceParser()
        self.diff_applier = DiffApplier()
        self.build_detector = BuildDetector(root_dir=root_dir)
        self.log_reducer = LogReducer()
        self.registry = ToolRegistry(root_dir=root_dir)
        self.session_state = SessionState()

    def process(self, user_prompt: str, explicit_files: Optional[Set[str]] = None) -> Dict[str, Any]:
        self.session_state.current_prompt = user_prompt

        # 1. Semantic Filter
        ctx_profile_name, ctx_profile = self.router.resolve_profile_for_task("context-gather")
        semantic_filter = SemanticFilter(context_profile=ctx_profile)
        filter_res = semantic_filter.filter_and_plan(user_prompt)
        task, plan = filter_res.task, filter_res.plan
        self.session_state.last_task = task
        self.session_state.last_plan = plan

        # 2. Context Engine retrieval
        context_blocks = self.context_engine.get_relevant_context(user_prompt, max_tokens=2048, root_dir=str(self.root_path))
        context_map_str = "\n\n".join(b.content for b in context_blocks)

        mandatory_constraints = [c.text for c in task.constraints if c.mandatory]

        base_sys = f"You are K.I.T.T., an advanced autonomous AI coding assistant.\n\nMemory:\n{self.memory.get_memory_context()}\n\nSkills:\n{self.skill_manager.get_skills_summary_prompt()}"

        # 3. Prompt Budgeting
        allocated = self.budget.allocate_context(
            system_prompt=base_sys,
            task_prompt=user_prompt,
            mandatory_constraints=mandatory_constraints,
            repo_map=context_map_str,
            files_context="",
            history_context="",
            recent_results=""
        )

        sys_prompt = f"{allocated['system_prompt']}\n\nRepo Map:\n{allocated['repo_map']}"

        # 4. Resolve Execution Profile
        exe_profile_name, exe_profile = self.router.resolve_profile_for_task("code-generation")

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
