from dataclasses import dataclass, field


@dataclass
class ExecutionRequest:
    """Structured request payload sent to the execution model."""

    system_prompt: str
    messages: list[dict[str, str]]
    enabled_tools: list[str]
    tool_definitions: list[dict] = field(default_factory=list)
    max_output_tokens: int = 1200
    estimated_input_tokens: int = 0
    agent_route: str | None = None
    agent_role: str | None = None
    loop_action_budget: int = 4
    context_envelope: dict | None = None
