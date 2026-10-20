"""Bounded independent read-only calls; every child passes the normal host policy.

This is a host operation, not an extra model parser or approval bypass. Atomic
validation precedes any child execution. Results preserve request ordering.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from kitt.runtime.core_runtime import SafeRuntimeResult

_ALLOWED = frozenset({'repo.read', 'repo.list', 'repo.search'})
_ID = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,63}\Z')
_MAX_CALLS = 8
_MAX_RESULT_BYTES = 64 * 1024
# Canonical host-owned positional schema, not an order supplied by the model.
POSITIONAL_SCHEMA = 'repo.batch_read/v1|id,operation,path'
POSITIONAL_DIGEST = hashlib.sha256(POSITIONAL_SCHEMA.encode('utf-8')).hexdigest()


def execute_read_batch(
    runtime: Any,
    arguments: dict[str, Any],
    *,
    turn_id: str,
    origin: str,
    capabilities: set[str],
    security_context: Any,
) -> SafeRuntimeResult:
    raw_calls = arguments.get('calls')
    if not isinstance(raw_calls, list) or not 1 <= len(raw_calls) <= _MAX_CALLS:
        return SafeRuntimeResult(False, 'repo.batch_read', error='calls must contain 1..8 entries')
    positional = set(arguments) == {'calls', 'schema_digest'}
    if positional:
        if arguments['schema_digest'] != POSITIONAL_DIGEST:
            return SafeRuntimeResult(False, 'repo.batch_read', error='unknown positional schema digest')
        if any(not isinstance(row, dict) or set(row) != {'id', 'operation', 'path'} for row in raw_calls):
            return SafeRuntimeResult(False, 'repo.batch_read', error='positional rows do not match host schema')
        raw_calls = [{'id': row['id'], 'operation': row['operation'],
                      'arguments': {'path': row['path']}} for row in raw_calls]
    elif set(arguments) != {'calls'}:
        return SafeRuntimeResult(False, 'repo.batch_read', error='unknown batch fields')

    prepared: list[tuple[str, str, dict[str, Any]]] = []
    ids: set[str] = set()
    for raw in raw_calls:
        if not isinstance(raw, dict) or set(raw) != {'id', 'operation', 'arguments'}:
            return SafeRuntimeResult(False, 'repo.batch_read', error='invalid batch entry')
        identifier, operation, child_args = raw['id'], raw['operation'], raw['arguments']
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in ids:
            return SafeRuntimeResult(False, 'repo.batch_read', error='invalid or duplicate call id (unique 1..64 chars, starts with a letter; letters/digits/_/- only)')
        if not isinstance(operation, str) or operation not in _ALLOWED:
            return SafeRuntimeResult(False, 'repo.batch_read', error='batch permits only repo.read/repo.list/repo.search')
        if not isinstance(child_args, dict):
            return SafeRuntimeResult(False, 'repo.batch_read', error='call arguments must be objects')
        # Strict per-call token/window bounds also bound memory and response size.
        if operation == 'repo.read':
            if not isinstance(child_args.get('path'), str) or not child_args['path']:
                return SafeRuntimeResult(False, 'repo.batch_read', error='repo.read requires path')
            try:
                max_tokens = int(child_args.get('max_tokens', 800))
                start_line = int(child_args.get('start_line', 1))
                end_line = int(child_args.get('end_line', start_line + 99))
            except (ValueError, TypeError):
                return SafeRuntimeResult(False, 'repo.batch_read', error='invalid read bounds')
            if not 1 <= max_tokens <= 1200 or start_line < 1 or end_line < start_line or end_line - start_line > 199:
                return SafeRuntimeResult(False, 'repo.batch_read', error='read limits exceeded')
        if operation == 'repo.search':
            query = child_args.get('query', child_args.get('pattern'))
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 256:
                return SafeRuntimeResult(False, 'repo.batch_read', error='repo.search requires a 1..256-character query')
            try:
                max_tokens = int(child_args.get('max_tokens', 600))
                max_results = int(child_args.get('max_results', child_args.get('limit', 20)))
            except (ValueError, TypeError):
                return SafeRuntimeResult(False, 'repo.batch_read', error='invalid repo.search bounds')
            if not (1 <= max_tokens <= 1200 and 1 <= max_results <= 20):
                return SafeRuntimeResult(False, 'repo.batch_read', error='repo.search limits exceeded')
        ids.add(identifier)
        prepared.append((identifier, operation, dict(child_args)))

    def invoke(call: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
        identifier, operation, child_args = call
        result = runtime.execute(
            operation, child_args, turn_id=turn_id, origin=origin,
            effective_capabilities=capabilities, security_context=security_context,
        )
        # No grants are forwarded: any unexpected approval is a rejected read.
        return {
            'id': identifier, 'operation': operation,
            'success': bool(result.success and not result.requires_approval),
            'data': result.data if result.success and not result.requires_approval else None,
            'error': result.error if not result.success or result.requires_approval else None,
        }

    # Only disjoint read operations are permitted; there are no writes, nested
    # batches, calls with side effects or model-controlled executor functions.
    with ThreadPoolExecutor(max_workers=min(4, len(prepared))) as pool:
        rows = list(pool.map(invoke, prepared))
    try:
        result_size = len(json.dumps(rows, ensure_ascii=False, default=str).encode('utf-8'))
    except (TypeError, ValueError):
        return SafeRuntimeResult(False, 'repo.batch_read', error='batch result not serializable')
    if result_size > _MAX_RESULT_BYTES:
        return SafeRuntimeResult(False, 'repo.batch_read', error='batch response exceeds 64 KiB')
    return SafeRuntimeResult(
        all(row['success'] for row in rows),
        'repo.batch_read',
        data={'calls': rows},
        error=None if all(row['success'] for row in rows) else 'one or more independent reads failed',
        metadata={'calls': len(rows), 'parallel': min(4, len(rows))},
    )
