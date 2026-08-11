from kitt.domain.entities import TaskStep, TaskType

class TaskClassifier:
    """Classifies task steps into specific task types for optimal model routing."""

    def classify(self, step: TaskStep) -> TaskType:
        tool = (step.tool_name or "").lower()
        cmd = (step.command or "").lower()

        if any(k in tool for k in ['read', 'grep', 'glob', 'list']):
            return 'context-gather'

        if any(k in tool for k in ['edit', 'write', 'replace']):
            return 'code-edit'

        if any(k in cmd for k in ['test', 'lint', 'typecheck', 'pytest']) or 'validate' in tool:
            return 'validate-diff'

        if step.prompt and any(k in step.prompt.lower() for k in ['summary', 'map', 'explain']):
            return 'summarize'

        return 'code-generation'
