from kitt.domain.entities import SemanticTask, ContextPlan

class ContextPlanner:
    """Converts SemanticTask into an actionable ContextPlan governing tool selection and search targets."""

    def build_plan(self, task: SemanticTask) -> ContextPlan:
        tools = ["read_file", "search", "repository_map"]

        if task.intent in {'IMPLEMENT', 'DEBUG', 'REFACTOR'}:
            tools.extend(["apply_patch", "run_command"])
        elif task.intent == 'TEST':
            tools.extend(["run_command", "read_file"])

        search_queries = list(set(task.paths + task.symbols + task.actions))

        return ContextPlan(
            search_queries=search_queries,
            candidate_symbols=task.symbols,
            preferred_paths=task.paths,
            enabled_tools=tools,
            instruction_modules=task.technologies,
            include_original_prompt=True,
            confidence=task.confidence
        )
