import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.cli.main import build_parser
from kitt.core.logging import configure_logging, trace_event


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


if __name__ == "__main__":
    unittest.main()
