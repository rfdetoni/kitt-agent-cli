"""Conversation-scoped revision handles and compare-and-swap write receipts.

Opaque handle lookup uses RuntimeStateStore (workspace/conversation scoped).
WorkspaceFileSystem is the existing host file authority.
"""
from __future__ import annotations

import re
import secrets
from typing import Any

from kitt.runtime.core_runtime import SafeRuntimeResult
from kitt.security.workspace_fs import WorkspaceFileSystem

_REF = re.compile(r'ref:[A-Za-z0-9_-]{24,64}\Z')
_MAX_BYTES = 64 * 1024
_TTL = 3600


def execute_revision_ref(runtime: Any, operation: str, arguments: dict[str, Any],
                         *, security_context: Any) -> SafeRuntimeResult:
    def denied(reason: str) -> SafeRuntimeResult:
        return SafeRuntimeResult(False, operation, error=reason)

    if runtime.state is None:
        return denied('Revision handles require persistent conversation state')
    if not isinstance(arguments, dict):
        return denied('Revision arguments must be an object')
    try:
        fs = WorkspaceFileSystem(runtime.root)
        if operation == 'repo.ref.create':
            if set(arguments) != {'path'} or not isinstance(arguments['path'], str):
                return denied('create requires exactly path')
            path = fs.relative(arguments['path'])
            if security_context is not None:
                security_context.assert_path_allowed(path)
            data = fs.read(path, max_bytes=_MAX_BYTES)
            token = 'ref:' + secrets.token_urlsafe(24)
            if not runtime.state.set('kap2:' + token,
                                     {'path': path, 'sha256': data.sha256},
                                     ttl_seconds=_TTL):
                return denied('Unable to persist revision')
            return SafeRuntimeResult(True, operation,
                data={'ref': token, 'path': path, 'revision': data.sha256,
                      'bytes': data.size, 'ttl_seconds': _TTL})
        token = arguments.get('ref')
        if not isinstance(token, str) or not _REF.fullmatch(token):
            return denied('Invalid revision reference')
        record = runtime.state.get('kap2:' + token)
        if (not isinstance(record, dict) or not isinstance(record.get('path'), str)
                or not isinstance(record.get('sha256'), str)):
            return denied('Unknown or expired revision reference')
        path = record['path']
        if security_context is not None:
            security_context.assert_path_allowed(path)
        if operation == 'repo.ref.read':
            if set(arguments) not in ({'ref'}, {'ref', 'max_bytes'}):
                return denied('read accepts only ref and max_bytes')
            limit = arguments.get('max_bytes', 32768)
            if type(limit) is not int or not 1 <= limit <= 32768:
                return denied('Invalid read byte limit')
            data = fs.read(path, max_bytes=_MAX_BYTES)
            if data.sha256 != record['sha256']:
                return denied('Revision changed; create a new reference')
            return SafeRuntimeResult(True, operation,
                data={'ref': token, 'path': path, 'revision': data.sha256,
                      'bytes': data.size,
                      'content': data.content[:limit].decode('utf-8', errors='replace'),
                      'truncated': data.size > limit})
        if operation == 'repo.ref.write':
            # Cleartext path is mandatory for policy and control-plane checks.
            if set(arguments) != {'ref', 'path', 'content', 'expected_revision'}:
                return denied('write requires ref, path, content and expected_revision')
            if security_context is None:
                return denied('Writes require a scoped ExecutionSecurityContext')
            if not isinstance(arguments['path'], str) or fs.relative(arguments['path']) != path:
                return denied('Reference path mismatch')
            content = arguments['content']
            if not isinstance(content, str) or len(content.encode('utf-8')) > _MAX_BYTES:
                return denied('Invalid bounded UTF-8 content')
            expected = arguments['expected_revision']
            if not isinstance(expected, str) or expected != record['sha256']:
                return denied('Expected revision mismatch')
            digest = fs.atomic_write(path, content, expected_sha256=record['sha256'],
                                     expected_exists=True, max_bytes=_MAX_BYTES)
            runtime.state.delete('kap2:' + token)
            return SafeRuntimeResult(True, operation,
                data={'path': path, 'previous_revision': expected,
                      'revision': digest, 'receipt': 'committed'})
        return denied('Unknown revision operation')
    except (PermissionError, OSError, UnicodeError, ValueError) as error:
        return denied(f'Revision operation rejected: {error}')
