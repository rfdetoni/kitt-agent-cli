from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Optional

from kitt.artifacts.store import ArtifactStore
from kitt.children.manager import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.compaction.service import CompactionService
from kitt.context.working_set import ConversationWorkingSetStore
from kitt.context_engine.engine import ContextEngine
from kitt.core.autonomy_store import AutonomyStore
from kitt.core.event_bus import EventBus
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_processor import TurnProcessor
from kitt.dreaming.repository import MemoryRepository
from kitt.dreaming.scheduler import DreamScheduler
from kitt.dreaming.service import DreamingService
from kitt.goals.scheduler import GoalScheduler
from kitt.goals.service import GoalService
from kitt.harness.repository import HarnessRepository
from kitt.harness.service import HarnessService
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity
from kitt.history.service import HistoryService
from kitt.history.session_tree import SessionTreeRepository
from kitt.index.repository import RepositoryIndex
from kitt.llm.client import LLMClient
from kitt.memory.memory_manager import MemoryManager
from kitt.metrics.collector import MetricsCollector
from kitt.queueing.repository import InputQueueRepository
from kitt.queueing.service import InputQueueService
from kitt.router.router import TaskRouter
from kitt.security.egress import EgressPolicy
from kitt.security.network_policy import NetworkPolicy
from kitt.security.path_policy import PathPolicy
from kitt.security.sensitive_data import SensitiveDataScanner
from kitt.skills.skill_manager import SkillManager
from kitt.tools.approval import ApprovalManager
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry


@dataclass
class KittRuntime:
    """Primary composition root for K.I.T.T. runtime services and lifecycle."""

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
    autonomy_store: AutonomyStore
    egress_policy: EgressPolicy
    sensitive_scanner: SensitiveDataScanner
    path_policy: PathPolicy
    network_policy: NetworkPolicy
    memory_repo: Optional[MemoryRepository] = None
    dream_service: Optional[DreamingService] = None
    dream_scheduler: Optional[DreamScheduler] = None
    goal_scheduler: Optional[GoalScheduler] = None
    extensions: Optional[ExtensionManager] = None

    def __post_init__(self):
        self._closed = False
        self._closing = False
        self._started = False
        self._close_lock = threading.RLock()
        self._lifecycle_loop: Optional[asyncio.AbstractEventLoop] = None

    @classmethod
    def build(
        cls, root_dir: str, config: Optional[RuntimeConfig] = None
    ) -> KittRuntime:
        from kitt.core.workspace_identity import canonical_workspace_path

        config = config or RuntimeConfig.from_env()
        canonical_root = canonical_workspace_path(root_dir)
        ephemeral = config.ephemeral
        in_memory = not config.history_enabled
        persistence_enabled = not ephemeral
        database = HistoryDatabase(canonical_root, in_memory=in_memory)

        session_tree = SessionTreeRepository(database)
        history_repo = HistoryRepository(database)
        identity = resolve_workspace_identity(database, canonical_root)
        history = HistoryService(
            canonical_root,
            db=database,
            repo=history_repo,
            tree=session_tree,
            enabled=config.history_enabled,
            identity=identity,
            persistence_enabled=persistence_enabled,
        )

        autonomy_store = AutonomyStore(
            canonical_root, persistence_enabled=persistence_enabled
        )
        approval = ApprovalManager(
            db=database, ttl_seconds=config.approval_ttl_seconds
        )
        policy = PolicyEngine(
            canonical_root,
            autonomy=autonomy_store.get(),
            approval_manager=approval,
        )

        repository_index = RepositoryIndex(
            canonical_root,
            in_memory=in_memory,
            max_files=config.max_index_files,
            max_file_bytes=config.max_index_file_bytes,
            max_total_bytes=config.max_index_bytes,
        )
        context_engine = ContextEngine(
            repository_index=repository_index,
            persistence_enabled=persistence_enabled,
        )
        working_set = ConversationWorkingSetStore(
            canonical_root, persistence_enabled=persistence_enabled
        )

        registry = ToolRegistry(canonical_root, context_engine=context_engine)
        registry.policy = policy
        registry.approval_manager = approval
        registry.runtime_config = config

        skills = SkillManager(
            canonical_root, persistence_enabled=persistence_enabled
        )
        skills.executable_enabled = config.executable_skills_enabled

        artifacts = ArtifactStore(
            canonical_root,
            database,
            inline_limit=config.artifact_inline_limit,
            max_artifact_bytes=config.max_artifact_bytes,
            page_bytes=config.artifact_page_bytes,
            ephemeral=ephemeral,
        )
        metrics = MetricsCollector(history.repo)
        harness = HarnessService(HarnessRepository(database))
        goals = GoalService(database)
        queue = InputQueueService(InputQueueRepository(database))
        compaction = CompactionService(
            database,
            session_tree,
            keep_recent=config.compaction_keep_recent,
        )
        events = EventBus()

        from kitt.metrics.prime import PrimeMetrics

        prime_metrics = PrimeMetrics(canonical_root)
        events.subscribe("MetricsRecorded", lambda name, payload: metrics.record(payload))
        events.subscribe("*", prime_metrics.observe)
        registry.event_bus = events

        children = ChildAgentManager(
            canonical_root,
            ChildRepository(database),
            artifacts,
            max_children=config.max_children,
            max_depth=config.max_child_depth,
            workspace_id=identity.id,
            max_worker_seconds=config.child_timeout_seconds,
            event_callback=lambda name, payload: events.publish(name, payload),
            enabled=config.retained_agents_enabled,
        )

        memory_repo = MemoryRepository(database)
        memory = MemoryManager(
            canonical_root,
            persistence_enabled=persistence_enabled,
            memory_repo=memory_repo,
            workspace_id=identity.id,
        )
        registry.attach_services(
            artifacts=artifacts,
            queue_service=queue,
            goal_service=goals,
            child_manager=children,
            harness_service=harness,
            memory_service=memory,
            skill_manager=skills,
            db=database,
        )

        egress_policy = EgressPolicy(
            mode=getattr(config, "privacy_mode", "hybrid_redacted")
        )
        sensitive_scanner = SensitiveDataScanner()
        path_policy = PathPolicy(canonical_root)
        network_policy = NetworkPolicy()

        task_router = TaskRouter(root_dir=canonical_root)
        _, context_profile = task_router.resolve_profile_for_task("context-gather")
        dream_llm = LLMClient(context_profile)
        dream_service = DreamingService(
            db=database,
            memory_repo=memory_repo,
            history_repo=history_repo,
            session_tree=session_tree,
            root_dir=canonical_root,
            llm_client=dream_llm,
            egress_policy=egress_policy,
            event_callback=lambda name, payload: events.publish(name, payload),
        )

        processor = TurnProcessor(
            canonical_root,
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
        processor.child_manager = children
        registry.path_policy = path_policy
        registry.attach_processor(processor)

        def is_idle() -> bool:
            active_conversation = history.get_active_read_only()
            conversation_id = active_conversation["id"] if active_conversation else ""
            pending = len(processor.pending_actions)
            queued = len(queue.repo.pending(conversation_id)) if conversation_id else 0
            pending_approval = bool(approval.list_pending())
            return pending == 0 and queued == 0 and not pending_approval

        dream_scheduler = DreamScheduler(
            dream_service=dream_service,
            memory_repo=memory_repo,
            db=database,
            config=config,
            idle_checker=is_idle,
            workspace_id_getter=lambda: identity.id,
        )

        runtime_holder = {}
        from kitt.goals.executor import GoalStepExecutor

        goal_scheduler = GoalScheduler(
            db=database,
            goal_service=goals,
            runtime_step_executor=GoalStepExecutor(lambda: runtime_holder["runtime"]),
            event_callback=lambda name, payload: events.publish(name, payload),
        )

        from kitt.extensions.manager import ExtensionManager

        extensions = ExtensionManager(
            workspace_root=canonical_root,
            event_bus=events,
            tool_registry=registry,
        )

        runtime = cls(
            config,
            database,
            history,
            artifacts,
            metrics,
            policy,
            approval,
            repository_index,
            context_engine,
            working_set,
            registry,
            skills,
            harness,
            goals,
            queue,
            children,
            compaction,
            session_tree,
            events,
            processor,
            memory,
            autonomy_store,
            egress_policy,
            sensitive_scanner,
            path_policy,
            network_policy,
            memory_repo=memory_repo,
            dream_service=dream_service,
            dream_scheduler=dream_scheduler,
            goal_scheduler=goal_scheduler,
            extensions=extensions,
        )
        runtime_holder["runtime"] = runtime
        runtime.prime_metrics = prime_metrics
        return runtime

    async def start(self) -> None:
        """Start async-owned runtime services exactly once."""
        with self._close_lock:
            if self._closed:
                raise RuntimeError("Cannot start a closed KittRuntime")
            if self._closing:
                raise RuntimeError("Cannot start KittRuntime while it is closing")
            if self._started:
                return
            self._lifecycle_loop = asyncio.get_running_loop()

        started_goal_scheduler = False
        try:
            if self.extensions is not None:
                await self.extensions.start()
            if self.config.scheduler_enabled and self.goal_scheduler is not None:
                self.goal_scheduler.start(interval_seconds=1.0)
                started_goal_scheduler = True
            with self._close_lock:
                self._started = True
        except Exception:
            errors = []
            if started_goal_scheduler and self.goal_scheduler is not None:
                try:
                    self.goal_scheduler.stop()
                except Exception as exc:
                    errors.append(f"goal_scheduler: {exc}")
            if self.extensions is not None:
                try:
                    await self.extensions.stop()
                except Exception as exc:
                    errors.append(f"extensions: {exc}")
            with self._close_lock:
                self._started = False
                self._lifecycle_loop = None
            if errors:
                raise RuntimeError("; ".join(errors))
            raise

    @property
    def workspace_id(self) -> str:
        return self.history.workspace_id

    @property
    def canonical_root(self):
        return self.history.workspace.canonical_root

    def snapshot(self):
        from kitt.core.runtime_snapshot import RuntimeSnapshot

        active_conversation = self.history.get_active_read_only()
        conversation_id = active_conversation["id"] if active_conversation else ""
        pending = len(self.processor.pending_actions)
        queued = len(self.queue.repo.pending(conversation_id)) if conversation_id else 0
        active_goal = self.goals.active(conversation_id) if conversation_id else None
        return RuntimeSnapshot(
            workspace_id=self.workspace_id,
            active_conversation_id=conversation_id,
            pending_actions=pending,
            queued_inputs=queued,
            active_goal_id=active_goal.id if active_goal else "",
        )

    async def aclose(self) -> None:
        """Thread-safe, idempotent shutdown of every owned resource."""
        with self._close_lock:
            if self._closed:
                return
            if self._closing:
                return
            current_loop = asyncio.get_running_loop()
            if self._lifecycle_loop is not None and self._lifecycle_loop is not current_loop:
                raise RuntimeError(
                    "KittRuntime.aclose() must run on same event loop used for start()."
                )
            self._closing = True
            self._closed = True
            self._started = False

        errors = []
        for name, close_async, close_sync in (
            ("goal_scheduler", None, getattr(self.goal_scheduler, "stop", None)),
            ("dream_scheduler", None, getattr(self.dream_scheduler, "close", None)),
            ("extensions", getattr(self.extensions, "stop", None), None),
            ("children", None, getattr(self.children, "close", None)),
            ("processor", None, getattr(self.processor, "close", None)),
            ("metrics", None, getattr(self.metrics, "close", None)),
            ("artifacts", None, getattr(self.artifacts, "close", None)),
            ("events", None, getattr(self.events, "close", None)),
            (
                "repository_index",
                None,
                getattr(self.repository_index, "close", None),
            ),
            ("database", None, getattr(self.database, "close", None)),
        ):
            try:
                if close_async is not None:
                    await close_async()
                elif close_sync is not None:
                    close_sync()
            except Exception as exc:
                errors.append(f"{name}: {exc}")

        with self._close_lock:
            self._closing = False
            self._lifecycle_loop = None
        if errors:
            raise RuntimeError("Runtime shutdown errors: " + "; ".join(errors))

    def close(self):
        """Synchronous compatibility wrapper for runtime shutdown."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            raise RuntimeError(
                "KittRuntime.close() cannot run inside an active event loop; await aclose()."
            )
        asyncio.run(self.aclose())

    async def aswitch_workspace(self, new_root: str) -> KittRuntime:
        """Build the new runtime before closing the current runtime."""
        new_runtime = KittRuntime.build(new_root, config=self.config)
        try:
            await new_runtime.start()
        except Exception:
            await new_runtime.aclose()
            raise
        await self.aclose()
        return new_runtime

    def switch_workspace(self, new_root: str) -> KittRuntime:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            raise RuntimeError(
                "KittRuntime.switch_workspace() cannot run inside active event loop; await aswitch_workspace()."
            )
        return asyncio.run(self.aswitch_workspace(new_root))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()
