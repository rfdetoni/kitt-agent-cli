from kitt.domain.entities import SemanticTask, ContextPlan
from kitt.context_filter.fidelity import validate_semantic_fidelity, IR_ONLY_CONFIDENCE_THRESHOLD

class ContextPlanner:
    """Converts SemanticTask into an actionable ContextPlan governing tool selection and search targets."""

    def build_plan(self, task: SemanticTask, original_prompt: str = "") -> ContextPlan:
        tools = ["read_file", "search", "repository_map", "python_compute"]

        if task.intent in {'IMPLEMENT', 'DEBUG', 'REFACTOR', 'DOCUMENT', 'TEST'}:
            tools.extend(["write_file", "apply_patch", "run_command"])

        search_queries = list(dict.fromkeys(task.paths + task.symbols + task.actions + ([task.goal] if task.goal else [])))

        prompt_to_check = original_prompt or task.original_prompt
        fidelity_passed, _ = validate_semantic_fidelity(prompt_to_check, task)

        # High confidence (>=0.90) AND fidelity passed -> IR_ONLY (include_original_prompt = False)
        # Otherwise -> include_original_prompt = True
        include_original_prompt = not (task.confidence >= IR_ONLY_CONFIDENCE_THRESHOLD and fidelity_passed)

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
