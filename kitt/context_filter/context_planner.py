from kitt.domain.entities import SemanticTask, ContextPlan
from kitt.context_filter.fidelity import validate_semantic_fidelity

class ContextPlanner:
    """Converts SemanticTask into an actionable ContextPlan governing tool selection and search targets."""

    def build_plan(self, task: SemanticTask, original_prompt: str = "") -> ContextPlan:
        tools = ["read_file", "search", "repository_map", "python_compute", "artifact_read"]

        if task.intent in {'IMPLEMENT', 'DEBUG', 'REFACTOR', 'DOCUMENT', 'TEST'}:
            tools.extend(["write_file", "apply_patch", "run_command", "artifact_store"])

        search_queries = list(dict.fromkeys(task.paths + task.symbols + task.actions + ([task.goal] if task.goal else [])))

        prompt_to_check = original_prompt or task.original_prompt
        fidelity_passed, _ = validate_semantic_fidelity(prompt_to_check, task)

        # Shape/fidelity failure is a rejection, not a tool permission hint.
        # Even a direct caller bypassing SemanticFilter must fail closed.
        if not fidelity_passed:
            tools = ["read_file", "search", "repository_map", "artifact_read"]
        include_original_prompt = True

        return ContextPlan(
            search_queries=search_queries,
            candidate_symbols=task.symbols,
            preferred_paths=task.paths,
            enabled_tools=tools,
            instruction_modules=task.technologies,
            validation_commands=task.validation_hints,
            include_original_prompt=include_original_prompt,
            confidence=task.confidence
        )
