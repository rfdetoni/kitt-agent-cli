"""DreamingService: Orchestrates the 4 phases of Dreaming Mode (ORIENT, GATHER, CONSOLIDATE, PRUNE & INDEX)."""
from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path
from typing import Callable, List, Optional, Tuple, Dict, Any

from kitt.dreaming.models import (
    CandidateSignal,
    DreamOperation,
    DreamPlan,
    DreamResult,
    DreamRun,
    DreamSnapshot,
    MemoryEvidence,
    MemoryRecord,
)
from kitt.dreaming.orient import DreamOrientPhase
from kitt.dreaming.gather import DreamGatherPhase
from kitt.dreaming.consolidate import DreamConsolidatePhase
from kitt.dreaming.validator import DreamValidator
from kitt.dreaming.prune_index import DreamPruneAndIndexPhase
from kitt.dreaming.repository import MemoryRepository
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.history.session_tree import SessionTreeRepository
from kitt.llm.client import LLMClient
from kitt.security.egress import EgressPolicy
import threading


class DreamingService:
    """Consolidates cross-session history into durable, validated, minimal semantic memory."""

    def __init__(
        self,
        db: HistoryDatabase,
        memory_repo: MemoryRepository,
        history_repo: HistoryRepository,
        session_tree: SessionTreeRepository,
        root_dir: Optional[Path] = None,
        llm_client: Optional[LLMClient] = None,
        egress_policy: Optional[EgressPolicy] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        min_confidence_commit: float = 0.90,
        min_confidence_candidate: float = 0.70,
    ):
        self.db = db
        self.memory_repo = memory_repo
        self.history_repo = history_repo
        self.session_tree = session_tree
        self.root_dir = root_dir
        self.llm_client = llm_client
        self.egress_policy = egress_policy
        self.event_callback = event_callback
        self.min_confidence_commit = min_confidence_commit
        self.min_confidence_candidate = min_confidence_candidate

        # Phases
        self.orient_phase = DreamOrientPhase(db, memory_repo, history_repo, session_tree)
        self.gather_phase = DreamGatherPhase()
        self.consolidate_phase = DreamConsolidatePhase(llm_client=llm_client, egress_policy=egress_policy)
        self.validator = DreamValidator(
            min_confidence_commit=min_confidence_commit,
            min_confidence_candidate=min_confidence_candidate,
        )
        self.prune_phase = DreamPruneAndIndexPhase(memory_repo)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _check_cancelled(self, phase_name: str = "") -> None:
        if self._cancel_event.is_set():
            raise InterruptedError(f"Dreaming cancelled{f' at {phase_name}' if phase_name else ''}")

    def dream(
        self,
        workspace_id: str,
        dry_run: bool = False,
    ) -> DreamResult:
        """Executes a full dream cycle: ORIENT -> GATHER -> CONSOLIDATE -> VALIDATE -> PRUNE & INDEX."""
        self._check_cancelled("before start")
        self._cancel_event.clear()
        dream_run_id = f"dream_{uuid.uuid4().hex[:12]}"
        started_at = time.time()
        self._emit("DreamStarted", {"run_id": dream_run_id, "workspace_id": workspace_id, "dry_run": dry_run})

        try:
            # Phase 1: ORIENT
            self._check_cancelled("before ORIENT")
            snapshot = self.orient_phase.orient(workspace_id)
            self._check_cancelled("after ORIENT")
            self._emit("DreamPhaseCompleted", {"run_id": dream_run_id, "phase": "ORIENT", "sessions": len(snapshot.recent_sessions)})

            # Phase 2: GATHER SIGNAL
            self._check_cancelled("before GATHER")
            signals = self.gather_phase.gather(snapshot)
            self._check_cancelled("after GATHER")
            self._emit("DreamPhaseCompleted", {"run_id": dream_run_id, "phase": "GATHER", "signals": len(signals)})

            # Phase 3: CONSOLIDATE
            self._check_cancelled("before CONSOLIDATE")
            plan = self.consolidate_phase.consolidate(snapshot, signals)
            self._check_cancelled("after CONSOLIDATE")
            self._emit("DreamPhaseCompleted", {"run_id": dream_run_id, "phase": "CONSOLIDATE", "proposals": len(plan.operations)})

            # Validation
            self._check_cancelled("before VALIDATE")
            accepted_ops, rejected_ops = self.validator.validate_plan(plan, snapshot)
            self._check_cancelled("after VALIDATE")
            self._emit("DreamPhaseCompleted", {"run_id": dream_run_id, "phase": "VALIDATE", "accepted": len(accepted_ops), "rejected": len(rejected_ops)})

            # Prepare mutations
            new_memories: List[MemoryRecord] = []
            op_updated_memories: List[MemoryRecord] = []
            new_evidence: List[MemoryEvidence] = []
            existing_by_id = {m.id: m for m in snapshot.memories}
            new_evidence: List[MemoryEvidence] = []
            existing_by_id = {m.id: m for m in snapshot.memories}

            added_count = 0
            merged_count = 0
            superseded_count = 0
            archived_count = 0

            for op in accepted_ops:
                op_now = time.time()

                if op.operation == "ADD":
                    mem_id = f"mem_{uuid.uuid4().hex[:12]}"
                    status = "ACTIVE" if op.confidence >= self.min_confidence_commit else "CANDIDATE"
                    new_mem = MemoryRecord(
                        id=mem_id,
                        workspace_id=workspace_id,
                        kind=op.proposed_kind or "TECHNICAL_FACT",
                        content=op.proposed_content or "",
                        normalized_content=(op.proposed_content or "").strip(),
                        status=status,
                        importance=0.6,
                        confidence=op.confidence,
                        created_at=op_now,
                        updated_at=op_now,
                    )
                    new_memories.append(new_mem)
                    added_count += 1

                    for eid in op.source_entry_ids:
                        new_evidence.append(
                            MemoryEvidence(
                                id=f"ev_{uuid.uuid4().hex[:12]}",
                                memory_id=mem_id,
                                workspace_id=workspace_id,
                                session_entry_id=eid,
                                conversation_id=None,
                                source_kind="dream_consolidation",
                                evidence_text=op.proposed_content or "",
                                created_at=op_now,
                            )
                        )

                elif op.operation == "SUPERSEDE":
                    new_mem_id = f"mem_{uuid.uuid4().hex[:12]}"
                    superseded_id = op.source_memory_ids[0] if op.source_memory_ids else None
                    if superseded_id and superseded_id in existing_by_id:
                        old_mem = existing_by_id[superseded_id]
                        op_updated_memories.append(
                            MemoryRecord(
                                id=old_mem.id,
                                workspace_id=old_mem.workspace_id,
                                kind=old_mem.kind,
                                content=old_mem.content,
                                normalized_content=old_mem.normalized_content,
                                status="SUPERSEDED",
                                importance=old_mem.importance,
                                confidence=old_mem.confidence,
                                created_at=old_mem.created_at,
                                updated_at=op_now,
                                last_accessed_at=old_mem.last_accessed_at,
                                access_count=old_mem.access_count,
                                valid_from=old_mem.valid_from,
                                valid_until=op_now,
                                supersedes_id=old_mem.supersedes_id,
                                content_hash=old_mem.content_hash,
                                pinned=old_mem.pinned,
                                metadata_json=old_mem.metadata_json,
                            )
                        )
                        superseded_count += 1

                    status = "ACTIVE" if op.confidence >= self.min_confidence_commit else "CANDIDATE"
                    new_mem = MemoryRecord(
                        id=new_mem_id,
                        workspace_id=workspace_id,
                        kind=op.proposed_kind or "TECHNICAL_FACT",
                        content=op.proposed_content or "",
                        normalized_content=(op.proposed_content or "").strip(),
                        status=status,
                        importance=0.7,
                        confidence=op.confidence,
                        created_at=op_now,
                        updated_at=op_now,
                        valid_from=op_now,
                        supersedes_id=superseded_id,
                    )
                    new_memories.append(new_mem)
                    added_count += 1

                    for eid in op.source_entry_ids:
                        new_evidence.append(
                            MemoryEvidence(
                                id=f"ev_{uuid.uuid4().hex[:12]}",
                                memory_id=new_mem_id,
                                workspace_id=workspace_id,
                                session_entry_id=eid,
                                conversation_id=None,
                                source_kind="dream_supersession",
                                evidence_text=op.proposed_content or "",
                                created_at=op_now,
                            )
                        )

                elif op.operation == "MERGE":
                    new_mem_id = f"mem_{uuid.uuid4().hex[:12]}"
                    for mid in op.source_memory_ids:
                        if mid in existing_by_id:
                            old_mem = existing_by_id[mid]
                            op_updated_memories.append(
                                MemoryRecord(
                                    id=old_mem.id,
                                    workspace_id=old_mem.workspace_id,
                                    kind=old_mem.kind,
                                    content=old_mem.content,
                                    normalized_content=old_mem.normalized_content,
                                    status="SUPERSEDED",
                                    importance=old_mem.importance,
                                    confidence=old_mem.confidence,
                                    created_at=old_mem.created_at,
                                    updated_at=op_now,
                                    last_accessed_at=old_mem.last_accessed_at,
                                    access_count=old_mem.access_count,
                                    valid_from=old_mem.valid_from,
                                    valid_until=op_now,
                                    supersedes_id=old_mem.supersedes_id,
                                    content_hash=old_mem.content_hash,
                                    pinned=old_mem.pinned,
                                    metadata_json=old_mem.metadata_json,
                                )
                            )
                            merged_count += 1

                    new_mem = MemoryRecord(
                        id=new_mem_id,
                        workspace_id=workspace_id,
                        kind=op.proposed_kind or "TECHNICAL_FACT",
                        content=op.proposed_content or "",
                        normalized_content=(op.proposed_content or "").strip(),
                        status="ACTIVE",
                        importance=0.8,
                        confidence=op.confidence,
                        created_at=op_now,
                        updated_at=op_now,
                        valid_from=op_now,
                    )
                    new_memories.append(new_mem)
                    added_count += 1

            # Phase 4: PRUNE & INDEX
            self._check_cancelled("before PRUNE")
            pruned_records, _projection = self.prune_phase.prune_and_index(
                workspace_id, snapshot, root_dir=self.root_dir, dry_run=dry_run
            )
            self._check_cancelled("after PRUNE")

            # Mutation Precedence: SUPERSEDE/MERGE > ARCHIVE > others
            mutations_by_id: Dict[str, MemoryRecord] = {}
            for mem in op_updated_memories:
                mutations_by_id[mem.id] = mem

            for pr in pruned_records:
                if pr.id not in mutations_by_id:
                    mutations_by_id[pr.id] = pr
                    if pr.status == "ARCHIVED":
                        archived_count += 1
                elif mutations_by_id[pr.id].status != "SUPERSEDED":
                    mutations_by_id[pr.id] = pr
                    if pr.status == "ARCHIVED":
                        archived_count += 1

            final_updated_memories = list(mutations_by_id.values())

            finished_at = time.time()
            dream_run = DreamRun(
                id=dream_run_id,
                workspace_id=workspace_id,
                started_at=started_at,
                finished_at=finished_at,
                status="COMPLETED",
                sessions_scanned=len(snapshot.recent_sessions),
                entries_scanned=len(snapshot.recent_entries),
                signals_found=len(signals),
                memories_added=added_count,
                memories_merged=merged_count,
                memories_superseded=superseded_count,
                memories_archived=archived_count,
                model=getattr(self.consolidate_phase, "model_name", "deterministic"),
                input_tokens=0,
                output_tokens=0,
                dry_run=dry_run,
            )

            # Atomic commit if not dry_run
            if not dry_run:
                self._check_cancelled("before COMMIT")
                self._emit("DreamCommitStarted", {"run_id": dream_run_id, "workspace_id": workspace_id})
                self.memory_repo.commit_dream(
                    workspace_id=workspace_id,
                    dream_run=dream_run,
                    new_memories=new_memories,
                    updated_memories=final_updated_memories,
                    new_evidence=new_evidence,
                )
                self._emit("DreamCommitCompleted", {"run_id": dream_run_id, "workspace_id": workspace_id})
                try:
                    self.memory_repo.rebuild_materialized_view(workspace_id, root_dir=self.root_dir)
                except Exception as proj_err:
                    self._emit("DreamProjectionFailed", {"run_id": dream_run_id, "error": str(proj_err)})

            result = DreamResult(
                run=dream_run,
                plan=plan,
                snapshot=snapshot,
                accepted_operations=accepted_ops,
                rejected_operations=rejected_ops,
            )

            self._emit("DreamCompleted", {
                "run_id": dream_run_id,
                "added": added_count,
                "merged": merged_count,
                "superseded": superseded_count,
                "archived": archived_count,
                "dry_run": dry_run,
            })
            return result

        except Exception as exc:
            finished_at = time.time()
            failed_run = DreamRun(
                id=dream_run_id,
                workspace_id=workspace_id,
                started_at=started_at,
                finished_at=finished_at,
                status="CANCELLED" if isinstance(exc, InterruptedError) else "FAILED",
                sessions_scanned=0,
                entries_scanned=0,
                signals_found=0,
                memories_added=0,
                memories_merged=0,
                memories_superseded=0,
                memories_archived=0,
                model="",
                input_tokens=0,
                output_tokens=0,
                failure_reason=str(exc),
                dry_run=dry_run,
            )
            if not dry_run:
                try:
                    self.memory_repo.record_dream_run(failed_run)
                except Exception:
                    pass
            self._emit("DreamFailed", {"run_id": dream_run_id, "error": str(exc)})
            raise

    def _emit(self, name: str, payload: Dict[str, Any]) -> None:
        if self.event_callback:
            try:
                self.event_callback(name, payload)
            except Exception:
                pass
