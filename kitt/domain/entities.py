from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal

TaskType = Literal[
    'context-gather',
    'summarize',
    'code-generation',
    'code-edit',
    'validate-diff'
]

@dataclass
class Tag:
    kind: str  # 'def' or 'ref'
    name: str
    line: int
    signature: str
    sub_kind: Optional[str] = None

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

@dataclass
class RouterConfig:
    profiles: Dict[str, ModelProfile] = field(default_factory=dict)
    routing: Dict[str, str] = field(default_factory=dict)

@dataclass
class TaskStep:
    tool_name: Optional[str] = None
    command: Optional[str] = None
    prompt: Optional[str] = None
