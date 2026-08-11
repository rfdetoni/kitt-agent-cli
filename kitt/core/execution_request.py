from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class ExecutionRequest:
    """Structured request payload sent to the execution model."""
    system_prompt: str
    messages: List[Dict[str, str]]
    enabled_tools: List[str]
    max_output_tokens: int = 1200
    estimated_input_tokens: int = 0
