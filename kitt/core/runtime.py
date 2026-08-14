from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from kitt.artifacts.store import ArtifactStore
from kitt.children.manager import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.compaction.service import CompactionService
from kitt.context_engine.indexer import LocalFileIndexer
from kitt.context_engine.engine import ContextEngine
from kitt.context.working_set import ConversationWorkingSetStore
from kitt.core.event_bus import EventBus
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_processor import TurnProcessor
from kitt.goals.service import GoalService
from kitt.harness.repository import HarnessRepository
from kitt.harness.service import HarnessService
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.history.service import HistoryService
from kitt.history.session_tree import SessionTreeRepository
from kitt.memory.memory_manager import MemoryManager
from kitt.metrics.collector import MetricsCollector
from kitt.queueing.repository import InputQueueRepository
from kitt.queueing.service import InputQueueService
from kitt.skills.skill_manager import SkillManager
from kitt.tools.approval import ApprovalManager
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry
from kitt.index.repository import RepositoryIndex


@dataclass
class KittRuntime:
    config: RuntimeConfig
    database: HistoryDatabase
    history: HistoryService
    artifacts: ArtifactStore
    metrics: MetricsCollector
    policy: PolicyEngine
    approval: ApprovalManager
    repository_index: RepositoryIndex
    context_engine: ContextEngine
    working_set: ConversationWorkingSetStore
    registry: ToolRegistry
    skills: SkillManager
    harness: HarnessService
    goals: GoalService
    queue: InputQueueService
    children: ChildAgentManager
    compaction: CompactionService
    session_tree: SessionTreeRepository
    events: EventBus
    processor: TurnProcessor
    memory: MemoryManager
    indexer: LocalFileIndexer
    autonomy_store: AutonomyStore

    def __post_init__(self):
        self._closed = False
        self._close_lock = threading.RLock()

    @classmethod
    def build(cls, root_dir: str, config: Optional[RuntimeConfig] = None) -> "KittRuntime":
        from kitt.core.workspace_identity import canonical_workspace_path
        from kitt.core.autonomy_store import AutonomyStore
        config = config or RuntimeConfig()
        canon_root = canonical_workspace_path(root_dir)
        ephemeral = config.ephemeral
        in_memory = not config.history_enabled
        persistence_enabled = not ephemeral
        db = HistoryDatabase(canon_root, in_memory=in_memory)

        tree = SessionTreeRepository(db)
        history_repo = HistoryRepository(db)
        identity = resolve_workspace_identity(db, canon_root)
        history = HistoryService(canon_root, db=db, repo=history_repo, tree=tree,
                                 enabled=config.history_enabled, identity=identity,
                                 persistence_enabled=persistence_enabled)
        autonomy_store = AutonomyStore(canon_root, persistence_enabled=persistence_enabled)
        approval = ApprovalManager(db=db, ttl_seconds=config.approval_ttl_seconds)
        policy = PolicyEngine(canon_root, autonomy=autonomy_store.get(), approval_manager=approval)
        from kitt.security.egress import EgressPolicy
        from kitt.security.sensitive_data import SensitiveDataScanner
        from kitt.security.path_policy import PathPolicy
        from kitt.security.network_policy import NetworkPolicy

        repository_index = RepositoryIndex(canon_root, in_memory=in_memory, max_files=config.max_index_files)
        context_engine = ContextEngine(repository_index=repository_index, persistence_enabled=persistence_enabled)
        working_set = ConversationWorkingSetStore(canon_root, persistence_enabled=persistence_enabled)
        registry = ToolRegistry(canon_root, context_engine=context_engine)
        registry.policy = policy
        registry.approval_manager = approval
        skills = SkillManager(canon_root, persistence_enabled=persistence_enabled)
        artifacts = ArtifactStore(
            canon_root, db,
            inline_limit=config.artifact_inline_limit,
            max_artifact_bytes=config.max_artifact_bytes,
            page_bytes=config.artifact_page_bytes,
            ephemeral=ephemeral,
        )
        metrics = MetricsCollector(history.repo)
        harness = HarnessService(HarnessRepository(db))
        goals = GoalService(db)
        queue = InputQueueService(InputQueueRepository(db))
        compaction = CompactionService(db, tree, keep_recent=config.compaction_keep_recent)
        events = EventBus()
        events.subscribe("MetricsRecorded", lambda name, payload: metrics.record(payload))
        children = ChildAgentManager(
            canon_root, ChildRepository(db), artifacts,
            max_children=config.max_children, max_depth=config.max_child_depth,
            workspace_id=identity.id,
            event_callback=lambda name, payload: events.publish(name, payload),
        )
        registry.attach_services(artifacts, queue, goals, children, harness)
        memory = MemoryManager(canon_root, persistence_enabled=persistence_enabled)
        egress_policy = EgressPolicy(mode=getattr(config, "privacy_mode", "hybrid_redacted"))
        sensitive_scanner = SensitiveDataScanner()
        path_policy = PathPolicy(canon_root)
        network_policy = NetworkPolicy()

        indexer = LocalFileIndexer(
            canon_root,
            persistence_enabled=persistence_enabled,
            max_file_bytes=config.max_index_file_bytes,
            max_files=config.max_index_files,
            max_total_bytes=config.max_index_bytes,
        )
        processor = TurnProcessor(
            canon_root,
            history_service=history,
            registry=registry,
            metrics_collector=metrics,
            harness_service=harness,
            compaction_service=compaction,
            memory_service=memory,
            skill_manager=skills,
            context_engine=context_engine,
            working_set=working_set,
            event_callback=lambda name, payload: events.publish(name, payload),
            config=config,
            enable_context_summary=True,
        )
        processor.repository_index = repository_index
        processor.egress_policy = egress_policy
        processor.sensitive_scanner = sensitive_scanner
        processor.path_policy = path_policy
        processor.network_policy = network_policy

        registry.path_policy = path_policy

        runtime = cls(
            config, db, history, artifacts, metrics, policy, approval, repository_index,
            context_engine, working_set, registry, skills,
            harness, goals, queue, children, compaction, tree, events, processor,
            memory, indexer, autonomy_store,
        )
        runtime.egress_policy = egress_policy
        runtime.sensitive_scanner = sensitive_scanner
        runtime.path_policy = path_policy
        runtime.network_policy = network_policy
        return runtime

    @property
    def workspace_id(self) -> str:
        return self.history.workspace_id

    @property
    def canonical_root(self):
        return self.history.workspace.canonical_root

    def snapshot(self):
        from kitt.core.runtime_snapshot import RuntimeSnapshot

        active_conv = self.history.get_active_read_only()
        conv_id = active_conv["id"] if active_conv else ""
        pending = len(self.processor.pending_actions)
        queued = len(self.queue.repo.pending(conv_id)) if conv_id else 0
        active_goal = self.goals.active(conv_id) if conv_id else None
        return RuntimeSnapshot(
            workspace_id=self.workspace_id,
            active_conversation_id=conv_id,
            pending_actions=pending,
            queued_inputs=queued,
            active_goal_id=active_goal.id if active_goal else ""
        )

    def close(self):
        """Thread-safe, idempotent shutdown of every owned resource."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            errors = []
            for name, close in (
                ("processor", self.processor.close), ("children", self.children.close),
                ("metrics", self.metrics.close), ("artifacts", self.artifacts.close),
                ("events", self.events.close), ("database", self.database.close),
                ("repository_index", getattr(self.repository_index, "close", lambda: None)),
            ):
                try:
                    close()
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
            if errors:
                raise RuntimeError("Runtime shutdown errors: " + "; ".join(errors))

    def switch_workspace(self, new_root: str) -> "KittRuntime":
        """Build a new runtime for ``new_root`` and close this one on success.

        The new runtime is fully constructed before the old one is closed, so a
        build failure keeps the current runtime operational.
        """
        new_runtime = KittRuntime.build(new_root, config=self.config)
        self.close()
        return new_runtime

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
