from __future__ import annotations

import json
import logging

from kitt.context.tool_receipts import externalize_large_tool_results
from kitt.core.logging import StructuredFormatter, summarize_trace_messages
from kitt.evidence.ledger import EventLedger
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.runtime.conversation_runtime import _sanitized_config
from kitt.security.sensitive_data import SensitiveDataScanner


SECRET = "sk-supersecretvalue123456789"


class _Artifact:
    id = "art-redacted"


class _CaptureStore:
    def __init__(self):
        self.content = ""
        self.kwargs = {}

    def put(self, _workspace_id, content, _artifact_type, _summary, **kwargs):
        self.content = str(content)
        self.kwargs = dict(kwargs)
        return _Artifact()


def test_secret_sentinel_is_removed_from_observability_boundaries(tmp_path):
    scanner = SensitiveDataScanner.scan_and_redact(
        f"Authorization: Bearer {SECRET} API_KEY={SECRET}"
    )
    assert scanner.has_sensitive
    assert SECRET not in scanner.clean_text

    # Provider diagnostics summarize message content and never log raw prompt.
    summary = summarize_trace_messages(
        [{"role": "user", "content": f"do not leak {SECRET}"}]
    )
    assert SECRET not in json.dumps(summary)

    # Free-text log messages and exception text are redacted, not only
    # structured fields whose key happens to contain 'token' or 'secret'.
    record = logging.LogRecord(
        "kitt.test",
        logging.ERROR,
        __file__,
        1,
        f"provider failed with {SECRET}",
        (),
        None,
    )
    formatted = StructuredFormatter().format(record)
    assert SECRET not in formatted
    assert "REDACTED" in formatted

    # Durable observability redacts both secret-looking text and sensitive keys
    # before SQLite persistence.
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    workspace = repo.get_or_create_workspace(str(tmp_path))
    conversation = repo.create_conversation(workspace["id"], "secret-boundary")
    ledger = EventLedger(db)
    record = ledger.append_event(
        conversation["id"],
        "SecurityProbe",
        {
            "message": f"credential observed {SECRET}",
            "api_key": SECRET,
        },
        turn_id="turn-secret",
    )
    assert SECRET not in json.dumps(record.payload)
    with db.get_connection() as conn:
        raw = conn.execute(
            "SELECT payload_json FROM session_events WHERE id=?",
            (record.id,),
        ).fetchone()[0]
    assert SECRET not in raw

    # Runtime configuration never persists obvious credential fields.
    clean = _sanitized_config(
        {
            "image": "alpine:3.20",
            "token": SECRET,
            "nested": {"password": SECRET, "mode": "safe"},
        }
    )
    assert SECRET not in json.dumps(clean)
    assert clean["nested"] == {"mode": "safe"}

    # Large host-tool output is redacted before entering ArtifactStore and the
    # active message is also replaced with the redacted form.
    store = _CaptureStore()
    messages = [
        {
            "role": "user",
            "content": (
                "repo.read result from the host.\n"
                + f"Authorization: Bearer {SECRET}\n"
                + ("payload " * 400)
            ),
        }
    ]
    assert externalize_large_tool_results(
        messages,
        store=store,
        workspace_id=workspace["id"],
        conversation_id=conversation["id"],
        turn_id="turn-secret",
        min_tokens=10,
    ) == 1
    assert SECRET not in store.content
    assert SECRET not in messages[0]["content"]
    assert store.kwargs["sensitivity"] == "REDACTED"
    assert store.kwargs["metadata"]["redacted"] is True

    db.close()
