from dataclasses import dataclass


@dataclass
class ExecutionRequest:
    """Structured request payload sent to the execution model."""

    system_prompt: str
    messages: list[dict[str, str]]
    enabled_tools: list[str]
    max_output_tokens: int = 1200
    estimated_input_tokens: int = 0
    agent_route: str | None = None
