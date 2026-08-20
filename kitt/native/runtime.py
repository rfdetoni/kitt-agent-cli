from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import NativeCodeEngine
from .coordinator import WorkspaceCoordinator
from .memory import HybridMemoryService
from .output import OutputOptimizer
from .storage import NativeStateRepository


@dataclass
class NativeSubsystem:
    engine: NativeCodeEngine
    state: NativeStateRepository
    memory: HybridMemoryService
    output: OutputOptimizer
    coordinator: WorkspaceCoordinator

    @classmethod
    def build(cls, execution_root: str, state_root: str, db: Any, workspace_id: str,
              memory_repo: Any, memory_manager: Any) -> "NativeSubsystem":
        state = NativeStateRepository(db, workspace_id)
        engine = NativeCodeEngine(execution_root)
        memory = HybridMemoryService(memory_manager, memory_repo, state)
        output = OutputOptimizer(engine)
        coordinator = WorkspaceCoordinator(execution_root, state_root, db, workspace_id, engine)
        return cls(engine, state, memory, output, coordinator)

    def on_event(self, name: str, payload: dict[str, Any]) -> None:
        if name == "DreamCompleted" and not payload.get("dry_run"):
            self.memory.refresh_after_dream()
