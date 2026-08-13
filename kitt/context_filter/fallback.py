from kitt.domain.entities import SemanticTask, ContextPlan, TaskIntent
from kitt.context_filter.deterministic_extractor import DeterministicExtractor

class DeterministicFallbackPlanner:
    """Generates conservative, deterministic SemanticTask and ContextPlan without LLM calls."""

    def __init__(self):
        self.extractor = DeterministicExtractor()

    def generate_task(self, prompt: str) -> SemanticTask:
        paths = self.extractor.extract_paths(prompt)
        symbols = self.extractor.extract_symbols(prompt)
        constraints = self.extractor.extract_constraints(prompt)

        intent: TaskIntent = 'IMPLEMENT'
        prompt_lower = prompt.lower()
        if (not paths and not symbols and not any(kw in prompt_lower for kw in ("crie o arquivo", "crie um arquivo", "crie a pasta", "execute", "rode"))) or prompt_lower.strip() in {'oi', 'olá', 'ola', 'hello', 'hi'} or any(word in prompt_lower for word in ('explique', 'diga', 'responda', 'como ', 'por que', 'porque', '?')):
            intent = 'ASK'
        elif 'test' in prompt_lower or 'unittest' in prompt_lower:
            intent = 'TEST'
        elif 'debug' in prompt_lower or 'fix' in prompt_lower or 'bug' in prompt_lower:
            intent = 'DEBUG'
        elif 'refactor' in prompt_lower or 'clean' in prompt_lower:
            intent = 'REFACTOR'
        elif 'review' in prompt_lower:
            intent = 'REVIEW'
        elif 'doc' in prompt_lower or 'readme' in prompt_lower:
            intent = 'DOCUMENT'
        elif 'plan' in prompt_lower:
            intent = 'PLAN'

        return SemanticTask(
            original_prompt=prompt,
            intent=intent,
            actions=['analyze'] if intent == 'ASK' else ['analyze', 'edit'],
            symbols=symbols,
            paths=paths,
            constraints=constraints,
            confidence=1.0
        )

    def generate_plan(self, task: SemanticTask) -> ContextPlan:
        if task.intent == 'ASK' and not task.paths and not task.symbols:
            return ContextPlan(confidence=1.0)
        tools = ["write_file", "apply_patch", "read_file", "run_command", "repository_map", "python_compute"]
        return ContextPlan(
            search_queries=task.symbols + task.paths,
            candidate_symbols=task.symbols,
            preferred_paths=task.paths,
            enabled_tools=tools,
            confidence=1.0
        )
