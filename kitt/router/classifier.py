from typing import Iterable

from kitt.domain.entities import TaskStep, TaskType


class TaskClassifier:
    """Classifies task steps into specific task types for optimal model routing."""

    def classify(self, step: TaskStep) -> TaskType:
        tool = (step.tool_name or "").lower()
        cmd = (step.command or "").lower()

        if any(k in tool for k in ['read', 'grep', 'glob', 'list', 'search', 'repository_map', 'repo.read', 'repo.search', 'repo.context_map', 'repo.inspect']):
            return 'context-gather'

        if any(k in tool for k in ['edit', 'write', 'replace', 'patch', 'create_directory', 'repo.create', 'repo.move', 'repo.rename', 'repo.delete']):
            return 'code-edit'

        if any(k in cmd for k in ['test', 'lint', 'typecheck', 'pytest']) or any(k in tool for k in ['validate', 'test', 'lint', 'diagnostic', 'git_diff', 'git_status']):
            return 'validate-diff'

        if step.prompt and any(k in step.prompt.lower() for k in ['summary', 'summarize', 'resumo', 'map', 'explain']):
            return 'summarize'

        return 'code-generation'

    def classify_tool_surface(self, tool_names: Iterable[str], prompt: str = "") -> TaskType:
        """Collapse a declared tool surface into the safest matching router contract.

        Mixed read/write surfaces are edit-capable; validation outranks pure reads;
        pure read surfaces remain context-gather. This uses the same task taxonomy
        consumed by .kitt-router.json instead of creating a proxy-only route model.
        """
        routes = {
            self.classify(TaskStep(tool_name=name, prompt=prompt))
            for name in tool_names
            if name
        }
        for route in ('code-edit', 'validate-diff', 'context-gather', 'summarize'):
            if route in routes:
                return route  # type: ignore[return-value]
        return 'code-generation'
