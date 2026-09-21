import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.cli.main import build_parser
from kitt.core.logging import (
    configure_logging,
    sanitize_message,
    summarize_trace_messages,
    summarize_trace_text,
    trace_event,
)
from kitt.core.turn_tool_loop import _browser_trace_call, _browser_trace_result


class LoggingLevelTests(unittest.TestCase):
    def tearDown(self):
        configure_logging(0, None)

    def test_cli_accepts_level_2_and_log_file(self):
        with patch.dict(os.environ, {}, clear=False):
            args = build_parser().parse_args([
                "--log-level", "2",
                "--log-file", "/tmp/kitt-agent-trace.log",
            ])
        self.assertEqual(args.log_level, 2)
        self.assertEqual(args.log_file, "/tmp/kitt-agent-trace.log")

    def test_level_2_records_full_payload_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            path = Path(temp) / "agent.log"
            configured = configure_logging(2, path)
            self.assertEqual(configured, path.resolve())

            logger = logging.getLogger("kitt.test.logging")
            trace_event(
                logger,
                "agent.trace.full",
                prompt="create backend and frontend",
                api_key="super-secret",
                nested={"token": "hidden", "content": "visible payload"},
                items=[{"index": index} for index in range(40)],
            )

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines)
            payload = json.loads(lines[-1])
            rendered = json.dumps(payload, ensure_ascii=False)

            self.assertIn("agent.trace.full", rendered)
            self.assertIn("create backend and frontend", rendered)
            self.assertIn("visible payload", rendered)
            self.assertIn('"index": 39', rendered)
            self.assertNotIn("super-secret", rendered)
            self.assertNotIn('"hidden"', rendered)
            self.assertIn("[REDACTED]", rendered)


    def test_trace_sanitizer_redacts_inline_binary_data_and_url_credentials(self):
        rendered = sanitize_message(
            "visual=data:image/png;base64,QUJDREVGRw== "
            "target=https://user:pw@example.com/private?q=value"
        )
        self.assertIn("data:image/png;base64,[REDACTED]", rendered)
        self.assertNotIn("QUJDREVGRw==", rendered)
        self.assertNotIn("user:pw@", rendered)
        self.assertNotIn("q=value", rendered)
        self.assertIn("https://example.com/private?[redacted]", rendered)


    def test_trace_summaries_do_not_serialize_message_contents(self):
        secret = "typed-secret-value"
        text_summary = summarize_trace_text(secret)
        messages_summary = summarize_trace_messages([
            {"role": "user", "content": secret},
            {"role": "tool", "content": "<html>private-dom</html>"},
        ])
        rendered = json.dumps(
            {"text": text_summary, "messages": messages_summary},
            ensure_ascii=False,
        )
        self.assertNotIn(secret, rendered)
        self.assertNotIn("private-dom", rendered)
        self.assertEqual(messages_summary[0]["role"], "user")
        self.assertEqual(messages_summary[1]["role"], "tool")
        self.assertGreater(messages_summary[0]["content"]["bytes"], 0)
        self.assertEqual(len(messages_summary[0]["content"]["sha256"]), 16)

    def test_browser_trace_keeps_only_safe_metadata(self):
        call = _browser_trace_call(
            "kitt_runtime",
            {
                "operation": "browser.type",
                "arguments": {
                    "selector": "#password",
                    "text": "do-not-log-this-secret",
                    "url": "https://user:pw@example.com/login?q=secret",
                    "submit": True,
                },
            },
        )
        self.assertIsNotNone(call)
        rendered_call = json.dumps(call, ensure_ascii=False)
        self.assertNotIn("do-not-log-this-secret", rendered_call)
        self.assertNotIn("#password", rendered_call)
        self.assertNotIn("user:pw", rendered_call)
        self.assertNotIn("q=secret", rendered_call)
        self.assertEqual(call["action"], "type")
        self.assertEqual(call["origin"], "https://example.com")

        result = _browser_trace_result(
            call,
            success=True,
            output='{"url":"https://example.com/private","dom":"sensitive-dom"}',
            error="",
            duration_ms=12.5,
        )
        rendered_result = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("sensitive-dom", rendered_result)
        self.assertNotIn("/private", rendered_result)
        self.assertEqual(
            set(result),
            {
                "action",
                "origin",
                "status",
                "duration_ms",
                "bytes",
                "blocked_by_origin_policy",
            },
        )


if __name__ == "__main__":
    unittest.main()
