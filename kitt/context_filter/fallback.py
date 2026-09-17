import re

from kitt.domain.entities import SemanticTask, ContextPlan, TaskIntent
from kitt.context_filter.deterministic_extractor import DeterministicExtractor

_WORKSPACE_CREATION_VERBS = (
    "crie", "criar", "create", "build", "implemente", "implementar",
    "gere", "gerar", "construa", "adicione",
)
_WORKSPACE_CREATION_NOUNS = (
    "projeto", "project", "site", "app", "aplicação", "aplicacao",
    "pasta", "folder", "diretório", "diretorio", "directory",
    "arquivo", "file", "backend", "frontend", "front end",
)
_WORKSPACE_HOW_TO_PREFIXES = (
    "como ", "how ", "explique ", "explain ", "qual a forma de ",
    "qual é a forma de ", "qual e a forma de ", "como faço para ", "como faco para ",
)


def is_workspace_creation_request(prompt: str) -> bool:
    """Detect explicit requests to create workspace/project content without an LLM.

    Imperative creation requests are mutations. Explanatory how-to questions such as
    "como criar um projeto Angular?" are deliberately read-only even though they
    contain the same creation verbs and nouns.
    """
    text = prompt.lower().strip()
    if text.startswith(_WORKSPACE_HOW_TO_PREFIXES):
        return False
    return (
        any(term in text for term in _WORKSPACE_CREATION_VERBS)
        and any(term in text for term in _WORKSPACE_CREATION_NOUNS)
    )


def _execution_actions(prompt_lower: str, intent: TaskIntent, creation_request: bool) -> list[str]:
    """Build host-oriented steps while preserving stable semantic action markers."""
    if intent == 'ASK':
        return ['analyze', 'answer without changing the workspace']

    # ``analyze`` and ``edit`` are stable semantic markers consumed by existing
    # routing/completion logic. Keep them in addition to the richer execution
    # checklist so detailed planning does not silently change task semantics.
    actions = ['analyze', 'edit']

    if intent == 'PLAN':
        return actions + [
            'inspect the relevant workspace structure and existing implementation',
            'produce an ordered implementation checklist with target files, risks, and validation',
        ]

    actions.extend((
        'inspect the relevant workspace structure and existing implementation before changing files',
        'build an ordered execution checklist from the explicit user requirements and repository evidence',
    ))

    if creation_request:
        explicit_scopes = False
        if 'backend' in prompt_lower or 'back end' in prompt_lower:
            actions.append('create and implement the requested backend scope using host mutation tools')
            explicit_scopes = True
        if 'frontend' in prompt_lower or 'front end' in prompt_lower:
            actions.append('create and implement the requested frontend scope using host mutation tools')
            explicit_scopes = True
        if not explicit_scopes:
            actions.append('create and implement the requested project structure using host mutation tools')
    else:
        actions.append('apply the requested workspace change using the available host mutation tools')

    actions.extend((
        'run the relevant build, test, lint, or check commands for every changed project scope',
        'review host tool results and fix remaining failures before reporting completion',
    ))
    return actions


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
        creation_request = is_workspace_creation_request(prompt)
        direct_execution = any(kw in prompt_lower for kw in (
            "crie o arquivo", "crie um arquivo", "crie a pasta", "crie uma pasta",
            "crie o diretório", "crie um diretório", "execute", "rode",
        ))
        conversational_request = any(word in prompt_lower for word in (
            'explique', 'diga', 'responda', 'como ', 'por que', 'porque', '?',
        ))
        if creation_request:
            # Creation is the primary workspace intent even when the same request also
            # asks to run tests/builds afterward. Validation is a completion step, not
            # a reason to downgrade the execution route to validate-diff.
            intent = 'IMPLEMENT'
        elif (
            (not paths and not symbols and not direct_execution)
            or prompt_lower.strip() in {'oi', 'olá', 'ola', 'hello', 'hi'}
            or conversational_request
        ):
            intent = 'ASK'
        elif re.search(r'(?<!\w)(?:test|tests|testing|unittest|pytest|teste|testes|testar)(?!\w)', prompt_lower):
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

        goal = prompt.strip()[:300]
        actions = _execution_actions(prompt_lower, intent, creation_request)
        validation_hints = []
        if intent == 'TEST':
            validation_hints = ['run the requested tests and inspect their results']
        elif intent not in {'ASK', 'PLAN'}:
            validation_hints = [
                'run relevant build, test, lint, or check commands for every changed project scope'
            ]

        return SemanticTask(
            original_prompt=prompt,
            intent=intent,
            goal=goal,
            actions=actions,
            symbols=symbols,
            paths=paths,
            constraints=constraints,
            validation_hints=validation_hints,
            risk='LOW',
            confidence=1.0
        )

    def generate_plan(self, task: SemanticTask) -> ContextPlan:
        if task.intent == 'ASK' and not task.paths and not task.symbols:
            return ContextPlan(confidence=1.0)
        tools = [
            "create_directory",
            "write_file",
            "apply_patch",
            "read_file",
            "run_command",
            "repository_map",
            "python_compute",
            "artifact_read",
            "artifact_store",
        ]
        return ContextPlan(
            search_queries=task.symbols + task.paths,
            candidate_symbols=task.symbols,
            preferred_paths=task.paths,
            enabled_tools=tools,
            validation_commands=task.validation_hints,
            confidence=1.0
        )
