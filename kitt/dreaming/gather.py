"""Phase 2: GATHER SIGNAL — Deterministic extraction, normalization, and deduplication of durable memory signals."""
from __future__ import annotations

import datetime
import hashlib
import re
import uuid
from typing import List, Tuple, Optional, Set, Dict, Any

from kitt.dreaming.models import (
    CandidateSignal,
    DreamSnapshot,
    MemoryKind,
    SessionDigest,
    SessionEntryDigest,
)

# Signal patterns for deterministic extraction
PREFERENCE_PATTERNS = [
    (re.compile(r'\b(?:eu\s+prefiro|sempre\s+use|prefer|always\s+use|i\s+prefer|minha\s+preferência)\s+([^.\n]+)', re.IGNORECASE), "USER_PREFERENCE"),
    (re.compile(r'\b(?:não\s+use|nao\s+use|nunca\s+use|don\'t\s+use|never\s+use|do\s+not\s+use|sem\s+adicionar|without\s+adding)\s+([^.\n]+)', re.IGNORECASE), "USER_PREFERENCE"),
]

RULE_PATTERNS = [
    (re.compile(r'\b(?:regra\s+do\s+projeto|project\s+rule|padrão\s+do\s+projeto|obrigatoriamente|must\s+always|never\s+commit|strict\s+rule)\b\s*:?\s*([^.\n]+)', re.IGNORECASE), "PROJECT_RULE"),
    (re.compile(r'\b(?:sempre\s+use\s+\./mvnw|always\s+use\s+\./mvnw|use\s+rtk|stdlib\s+first|standard\s+library\s+first)\b', re.IGNORECASE), "PROJECT_RULE"),
]

ARCH_PATTERNS = [
    (re.compile(r'\b(?:decidimos|arquitetura|architecture\s+decision|migramos\s+para|migrated\s+to|adotamos|we\s+decided\s+to\s+use)\s+([^.\n]+)', re.IGNORECASE), "ARCHITECTURE_DECISION"),
]

FAILURE_PATTERNS = [
    (re.compile(r'\b(?:falhou\s+porque|caused\s+timeout|abordagem\s+falhou|failed\s+approach|não\s+funcionou|did\s+not\s+work)\s*:?\s*([^.\n]+)', re.IGNORECASE), "FAILED_APPROACH"),
]

ISSUE_PATTERNS = [
    (re.compile(r'\b(?:todo|fixme|open\s+issue|problema\s+aberto|pendência|pending\s+fix)\s*:?\s*([^.\n]+)', re.IGNORECASE), "OPEN_ISSUE"),
]

NOISE_WORDS = {
    "oi", "olá", "ola", "hello", "hi", "hey", "bom dia", "boa tarde", "boa noite",
    "ok", "certo", "thanks", "obrigado", "valeu", "show", "beleza", "por favor", "please"
}


class DreamGatherPhase:
    """Extracts, normalizes, resolves relative dates, and deduplicates candidate signals from session data."""

    def gather(self, snapshot: DreamSnapshot) -> Tuple[CandidateSignal, ...]:
        candidates: List[CandidateSignal] = []
        seen_hashes: Set[str] = set()

        # 1. Inspect recent sessions
        for session in snapshot.recent_sessions:
            conv_id = session.conversation_id
            session_time = session.completed_at or session.started_at

            # Extract from user requests
            for req in session.user_requests:
                signals = self._extract_from_text(req, conv_id, session.entry_ids, session_time)
                for sig in signals:
                    if sig.content_hash not in seen_hashes:
                        seen_hashes.add(sig.content_hash)
                        candidates.append(sig)

            # Extract from decisions
            for dec in session.decisions:
                norm_dec = self._normalize_text(dec)
                norm_dec = self._resolve_relative_dates(norm_dec, session_time)
                c_hash = hashlib.sha256(norm_dec.lower().encode("utf-8")).hexdigest()
                if c_hash not in seen_hashes:
                    seen_hashes.add(c_hash)
                    candidates.append(
                        CandidateSignal(
                            id=f"sig_{uuid.uuid4().hex[:12]}",
                            source_entry_ids=session.entry_ids[:2] if session.entry_ids else (),
                            conversation_id=conv_id,
                            kind_hint="ARCHITECTURE_DECISION",
                            raw_content=dec,
                            normalized_content=norm_dec,
                            occurred_at=session_time,
                            deterministic_score=0.95,
                            content_hash=c_hash,
                        )
                    )

            # Extract from failures
            for fail in session.failures:
                norm_fail = self._normalize_text(fail)
                norm_fail = self._resolve_relative_dates(norm_fail, session_time)
                c_hash = hashlib.sha256(norm_fail.lower().encode("utf-8")).hexdigest()
                if c_hash not in seen_hashes:
                    seen_hashes.add(c_hash)
                    candidates.append(
                        CandidateSignal(
                            id=f"sig_{uuid.uuid4().hex[:12]}",
                            source_entry_ids=session.entry_ids[:2] if session.entry_ids else (),
                            conversation_id=conv_id,
                            kind_hint="FAILED_APPROACH",
                            raw_content=fail,
                            normalized_content=norm_fail,
                            occurred_at=session_time,
                            deterministic_score=0.85,
                            content_hash=c_hash,
                        )
                    )

        # 2. Inspect individual session entry digests only if recent_sessions is empty
        if not snapshot.recent_sessions:
            for entry in snapshot.recent_entries:
                if entry.entry_type in ("USER_TURN", "USER_PROMPT", "PROMPT", "DECISION"):
                    signals = self._extract_from_text(entry.summary_text, entry.conversation_id, (entry.entry_id,), entry.created_at)
                    for sig in signals:
                        if sig.content_hash not in seen_hashes:
                            seen_hashes.add(sig.content_hash)
                            candidates.append(sig)

        return tuple(candidates)

    def _extract_from_text(
        self,
        text: str,
        conversation_id: str,
        entry_ids: Tuple[str, ...],
        occurred_at: float
    ) -> List[CandidateSignal]:
        clean = text.strip()
        if not clean or clean.lower() in NOISE_WORDS or len(clean) < 6:
            return []

        results: List[CandidateSignal] = []

        # Check preferences
        for pattern, kind in PREFERENCE_PATTERNS:
            if pattern.search(clean):
                norm = self._normalize_text(clean)
                norm = self._resolve_relative_dates(norm, occurred_at)
                results.append(
                    CandidateSignal(
                        id=f"sig_{uuid.uuid4().hex[:12]}",
                        source_entry_ids=entry_ids,
                        conversation_id=conversation_id,
                        kind_hint=kind,  # type: ignore
                        raw_content=clean,
                        normalized_content=norm,
                        occurred_at=occurred_at,
                        deterministic_score=0.90,
                    )
                )
                return results

        # Check project rules
        for pattern, kind in RULE_PATTERNS:
            if pattern.search(clean):
                norm = self._normalize_text(clean)
                norm = self._resolve_relative_dates(norm, occurred_at)
                results.append(
                    CandidateSignal(
                        id=f"sig_{uuid.uuid4().hex[:12]}",
                        source_entry_ids=entry_ids,
                        conversation_id=conversation_id,
                        kind_hint=kind,  # type: ignore
                        raw_content=clean,
                        normalized_content=norm,
                        occurred_at=occurred_at,
                        deterministic_score=0.90,
                    )
                )
                return results

        # Check architecture decisions
        for pattern, kind in ARCH_PATTERNS:
            if pattern.search(clean):
                norm = self._normalize_text(clean)
                norm = self._resolve_relative_dates(norm, occurred_at)
                results.append(
                    CandidateSignal(
                        id=f"sig_{uuid.uuid4().hex[:12]}",
                        source_entry_ids=entry_ids,
                        conversation_id=conversation_id,
                        kind_hint=kind,  # type: ignore
                        raw_content=clean,
                        normalized_content=norm,
                        occurred_at=occurred_at,
                        deterministic_score=0.85,
                    )
                )
                return results

        # Check failures
        for pattern, kind in FAILURE_PATTERNS:
            if pattern.search(clean):
                norm = self._normalize_text(clean)
                norm = self._resolve_relative_dates(norm, occurred_at)
                results.append(
                    CandidateSignal(
                        id=f"sig_{uuid.uuid4().hex[:12]}",
                        source_entry_ids=entry_ids,
                        conversation_id=conversation_id,
                        kind_hint=kind,  # type: ignore
                        raw_content=clean,
                        normalized_content=norm,
                        occurred_at=occurred_at,
                        deterministic_score=0.85,
                    )
                )
                return results

        # Check open issues
        for pattern, kind in ISSUE_PATTERNS:
            if pattern.search(clean):
                norm = self._normalize_text(clean)
                norm = self._resolve_relative_dates(norm, occurred_at)
                results.append(
                    CandidateSignal(
                        id=f"sig_{uuid.uuid4().hex[:12]}",
                        source_entry_ids=entry_ids,
                        conversation_id=conversation_id,
                        kind_hint=kind,  # type: ignore
                        raw_content=clean,
                        normalized_content=norm,
                        occurred_at=occurred_at,
                        deterministic_score=0.80,
                    )
                )
                return results

        return results

    def _normalize_text(self, text: str) -> str:
        # Collapse multi-spaces and newlines
        clean = re.sub(r'\s+', ' ', text).strip()
        # Remove trailing periods for consistency
        clean = clean.rstrip('.')
        # Ensure sentence capitalization
        if clean and clean[0].islower():
            clean = clean[0].upper() + clean[1:]
        return clean

    def _resolve_relative_dates(self, text: str, source_timestamp: float) -> str:
        if not source_timestamp:
            return text

        try:
            base_dt = datetime.datetime.fromtimestamp(source_timestamp, tz=datetime.timezone.utc)
            yesterday_dt = base_dt - datetime.timedelta(days=1)
            today_str = base_dt.strftime("%Y-%m-%d")
            yesterday_str = yesterday_dt.strftime("%Y-%m-%d")

            # Replace "ontem" / "yesterday"
            res = re.sub(r'\b(?:ontem|yesterday)\b', f'on {yesterday_str}', text, flags=re.IGNORECASE)
            # Replace "hoje" / "today"
            res = re.sub(r'\b(?:hoje|today)\b', f'on {today_str}', res, flags=re.IGNORECASE)
            return res
        except Exception:
            return text
