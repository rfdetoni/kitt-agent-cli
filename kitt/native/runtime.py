from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import NativeCodeEngine
from .coordinator import WorkspaceCoordinator
from .output import OutputOptimizer
from .storage import NativeStateRepository


@dataclass
class NativeSubsystem:
    engine: NativeCodeEngine
    state: NativeStateRepository
    memory: Any
    output: OutputOptimizer
    coordinator: WorkspaceCoordinator

    @classmethod
    def build(cls, execution_root: str, state_root: str, db: Any, workspace_id: str,
              memory_repo: Any, memory_manager: Any) -> "NativeSubsystem":
        del memory_repo
        state = NativeStateRepository(db, workspace_id)
        engine = NativeCodeEngine(execution_root)
        output = OutputOptimizer(engine)
        coordinator = WorkspaceCoordinator(execution_root, state_root, db, workspace_id, engine)
        return cls(engine, state, memory_manager, output, coordinator)

    def on_event(self, name: str, payload: dict[str, Any]) -> None:
        del name, payload
