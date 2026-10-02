from types import SimpleNamespace
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import pytest
from kitt.llm.privacy import profile_processing_is_local
from kitt.llm.http_security import TransportCancellation, TransportCancelled, cancellation_scope, secure_urlopen
from kitt.context.envelope import ContextEnvelopeBuilder
from kitt_protocol import ContextKind, ContextTrust, ContextStability
from kitt.memory.shared_client import KittMemoryClient, KittMemoryUnavailable
from kitt.tools.registry import ToolRegistry
from kitt.core.execution_budget import ExecutionBudgetLedger, ExecutionBudgetExceeded
from kitt_protocol import ExecutionBudget


def test_gateway_bind_address_never_implies_local_processing():
    assert not profile_processing_is_local(SimpleNamespace(backend='kitt-proxy', base_url='http://127.0.0.1:3000'))
    assert not profile_processing_is_local(SimpleNamespace(backend='ollama', base_url='https://remote.example'))
    assert profile_processing_is_local(SimpleNamespace(backend='ollama', base_url='http://127.0.0.1:11434'))


def test_required_structured_context_cannot_silently_overspend():
    builder = ContextEnvelopeBuilder('turn', max_tokens=8)
    builder.add(ContextKind.REPOSITORY_MAP, {'files': ['file.py'] * 100}, source='repository', trust=ContextTrust.UNTRUSTED_WORKSPACE, stability=ContextStability.TURN, priority=95)
    with pytest.raises(ValueError, match='Required structured'): builder.build()


def test_gateway_grant_reserves_wallet_and_refunds_unused_calls():
    ledger = ExecutionBudgetLedger(ExecutionBudget(max_model_calls=3, max_input_tokens=100, max_output_tokens=100, max_total_tokens=150, max_cost=1, max_duration_ms=1000, max_tool_calls=3, max_subagents=2))
    assert ledger.reserve_model_attempts(max_attempts=3, input_tokens=80) == 3
    with pytest.raises(ExecutionBudgetExceeded): ledger.reserve_model_call()
    assert ledger.gateway_prompt_allowance(100) == 20
    ledger.settle_model_attempts(granted=3, used=1)
    assert ledger.model_calls == 1
    with pytest.raises(ExecutionBudgetExceeded): ledger.settle_model_attempts(granted=1, used=True)


def test_terminal_memory_error_is_never_replayed_or_restarted(tmp_path):
    client = KittMemoryClient(token_path=tmp_path / 'token')
    with patch.object(client, '_call_once', side_effect=KittMemoryUnavailable('post-send timeout')) as call, patch.object(client, '_start_local_service') as start:
        with pytest.raises(KittMemoryUnavailable) as error: client.ping()
        assert error.value.request_id
        assert call.call_count == 1
        start.assert_not_called()


def test_read_cursor_roundtrips_long_utf8_lines_and_crlf(tmp_path):
    content = ('á😀' * 200 + '\r\nlast\n').encode('utf-8')
    (tmp_path / 'long.txt').write_bytes(content)
    registry = ToolRegistry(root_dir=str(tmp_path))
    try:
        cursor = 0; output = b''; expected = None
        while True:
            args = {'path': 'long.txt', 'start_byte': cursor, 'max_tokens': 64}
            if expected: args['expected_file_hash'] = expected
            result = registry.execute_tool('read_file', args, enabled_tools=['read_file'])
            assert result.success, result.error
            output += result.output.encode('utf-8'); expected = result.metadata['full_file_hash']
            cursor = result.metadata['next_start_byte']
            if cursor is None: break
        assert output == content
        (tmp_path / 'long.txt').write_text('changed')
        assert not registry.execute_tool('read_file', {'path': 'long.txt', 'start_byte': 0, 'expected_file_hash': expected}, enabled_tools=['read_file']).success
    finally: registry.close()


def test_cancel_unblocks_a_stalled_http_body_without_waiting_for_read_timeout():
    started = threading.Event(); release = threading.Event(); finished = threading.Event(); errors = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.flush(); release.wait(3)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token = TransportCancellation()
    def read():
        try:
            with cancellation_scope(token), secure_urlopen(f'http://127.0.0.1:{server.server_port}', timeout=30) as response:
                started.set(); response.readline()
        except Exception as exc: errors.append(exc)
        finally: finished.set()
    thread = threading.Thread(target=read, daemon=True); thread.start()
    try:
        assert started.wait(2)
        token.cancel(); assert finished.wait(1)
        assert errors and isinstance(errors[0], TransportCancelled)
    finally:
        release.set(); server.shutdown(); server.server_close(); thread.join(1)
