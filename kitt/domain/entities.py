from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal

@dataclass(frozen=True)
class WorkspaceIdentity:
    id: str
    canonical_root: Path
    canonical_path_hash: str

TaskType = Literal[
    'context-gather',
    'summarize',
    'code-generation',
    'code-edit',
    'validate-diff'
]

TaskIntent = Literal[
    'ASK',
    'PLAN',
    'IMPLEMENT',
    'DEBUG',
    'TEST',
    'REVIEW',
    'DOCUMENT',
    'REFACTOR',
    'UNKNOWN'
]

RiskLevel = Literal['LOW', 'MEDIUM', 'HIGH']
Permission = Literal['ALLOW', 'ASK', 'DENY']
ConstraintKind = Literal['NEGATIVE', 'MANDATORY', 'LIMIT', 'SCOPE']
SourceKind = Literal['USER_LITERAL', 'DETERMINISTIC', 'LLM_NORMALIZED', 'REPOSITORY_EVIDENCE', 'TOOL_RESULT']


@dataclass
class SemanticConfidence:
    intent: float = 1.0
    goal: float = 1.0
    targets: float = 1.0
    constraints: float = 1.0
    actions: float = 1.0
    overall: float = 1.0

    @classmethod
    def from_overall(cls, val: float) -> SemanticConfidence:
        return cls(intent=val, goal=val, targets=val, constraints=val, actions=val, overall=val)


@dataclass
class Constraint:
    text: str
    kind: ConstraintKind
    source_start: int
    source_end: int
    mandatory: bool = True
    source: SourceKind = 'USER_LITERAL'


@dataclass
class SemanticTask:
    original_prompt: str
    intent: TaskIntent = 'UNKNOWN'
    secondary_intents: List[TaskIntent] = field(default_factory=list)
    goal: str = ""
    actions: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)
    technologies: List[str] = field(default_factory=list)
    constraints: List[Constraint] = field(default_factory=list)
    validation_hints: List[str] = field(default_factory=list)
    risk: RiskLevel = 'LOW'
    confidence: float = 1.0
    semantic_confidence: SemanticConfidence = field(default_factory=SemanticConfidence)

    def fingerprint(self) -> str:
        import hashlib
        import json
        payload = {
            "intent": self.intent,
            "goal": self.goal.strip().lower(),
            "paths": sorted(self.paths),
            "symbols": sorted(self.symbols),
            "constraints": sorted([c.text.strip().lower() for c in self.constraints]),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]

    def to_execution_prompt(self) -> str:
        parts = [f"Intent: {self.intent}"]
        if self.goal:
            parts.append(f"Goal:\n{self.goal}")
        if self.actions:
            acts = "\n".join(f"- {a}" for a in self.actions)
            parts.append(f"Actions:\n{acts}")
        targets = list(dict.fromkeys(self.paths + self.symbols))
        if targets:
            tgts = "\n".join(f"- {t}" for t in targets)
            parts.append(f"Targets:\n{tgts}")
        if self.validation_hints:
            val = "\n".join(f"- {v}" for v in self.validation_hints)
            parts.append(f"Validation:\n{val}")
        return "\n\n".join(parts)

@dataclass
class ContextPlan:
    search_queries: List[str] = field(default_factory=list)
    candidate_symbols: List[str] = field(default_factory=list)
    preferred_paths: List[str] = field(default_factory=list)
    enabled_tools: List[str] = field(default_factory=list)
    instruction_modules: List[str] = field(default_factory=list)
    validation_commands: List[str] = field(default_factory=list)
    include_original_prompt: bool = True
    confidence: float = 1.0

@dataclass
class Tag:
    kind: str  # 'def' or 'ref'
    name: str
    line: int
    signature: str
    sub_kind: Optional[str] = None
    end_line: Optional[int] = None
    qualified_name: Optional[str] = None

@dataclass
class FileTags:
    path: str
    tags: List[Tag]

@dataclass
class ContextBlock:
    path: str
    content: str
    token_count: int = 0

@dataclass
class TaskFocus:
    focus_files: List[str] = field(default_factory=list)
    focus_symbols: List[str] = field(default_factory=list)

@dataclass
class EditBlock:
    file_path: str
    search_content: str
    replace_content: str
    is_new_file: bool = False
    is_deletion: bool = False

@dataclass
class FileSnapshot:
    relative_path: str
    existed: bool
    content: Optional[str] = None

@dataclass
class ChangeSet:
    id: str
    timestamp: float
    description: str
    snapshots: List[FileSnapshot] = field(default_factory=list)

@dataclass
class EditResult:
    success: bool
    applied_files: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    deleted_files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    changeset: Optional[ChangeSet] = None

@dataclass
class ModelProfile:
    backend: str  # 'ollama', 'openai'
    model: str
    base_url: str = "http://localhost:11434"
    api_key: str = ""
    context_window: int = 8192
    max_output_tokens: int = 1200
    temperature: float = 0.0
    supports_tools: bool = False
    supports_json: bool = False
    keep_alive: Optional[str] = None
    request_timeout_seconds: int = 300

@dataclass
class RouterConfig:
    profiles: Dict[str, ModelProfile] = field(default_factory=dict)
    routing: Dict[str, str] = field(default_factory=dict)

@dataclass
class TaskStep:
    tool_name: Optional[str] = None
    command: Optional[str] = None
    prompt: Optional[str] = None
