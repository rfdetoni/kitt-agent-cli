from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.index.repository import RepositoryIndex
from kitt.index.scanner import RepositoryScanner


class Round3RepositoryLifecycleTests(unittest.TestCase):
    def test_scanner_honors_immediate_cancellation(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            for idx in range(20):
                (root / f"file_{idx}.py").write_text("def value(): return 1\n", encoding="utf-8")
            scanner = RepositoryScanner(root)

            self.assertEqual(scanner.scan_relative_files(should_stop=lambda: True), [])
            self.assertEqual(scanner.detect_modules(should_stop=lambda: True), [])

    def test_close_cancels_background_before_closing_sqlite(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            (root / "app.py").write_text("def app(): return 1\n", encoding="utf-8")
            index = RepositoryIndex(root, in_memory=False)
            started = threading.Event()

            def cancellable_scan(_scanner, *args, should_stop=None, **kwargs):
                started.set()
                deadline = time.monotonic() + 5.0
                while not (should_stop and should_stop()):
                    if time.monotonic() > deadline:
                        raise AssertionError("background scan did not receive cancellation")
                    time.sleep(0.01)
                return []

            with patch.object(RepositoryScanner, "scan_relative_files", cancellable_scan):
                index.bootstrap_then_background()
                self.assertTrue(started.wait(1.0), "background index did not start")
                started_close = time.monotonic()
                index.close()
                elapsed = time.monotonic() - started_close

            thread = index._background_thread
            self.assertIsNotNone(thread)
            self.assertFalse(thread.is_alive(), "background index survived close()")
            self.assertLess(elapsed, 2.0, "cooperative shutdown should not wait for timeout")

            # Windows refuses this operation while SQLite still owns the file.
            db_path = root / ".kitt" / "index" / "index.db"
            moved_path = db_path.with_name("index.closed.db")
            os.replace(db_path, moved_path)
            self.assertTrue(moved_path.exists())

            # Idempotency is part of the public lifecycle contract.
            index.close()

    def test_close_signals_cancellation_while_database_lock_is_busy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            (root / "app.py").write_text("def app(): return 1\n", encoding="utf-8")
            index = RepositoryIndex(root, in_memory=True)
            entered_index = threading.Event()

            def hold_index_lock(*args, **kwargs):
                entered_index.set()
                deadline = time.monotonic() + 5.0
                while not index._stop_event.is_set():
                    if time.monotonic() > deadline:
                        raise AssertionError("close() could not signal cancellation while DB lock was busy")
                    time.sleep(0.01)

            with (
                patch.object(RepositoryScanner, "scan_relative_files", return_value=["app.py"]),
                patch.object(RepositoryScanner, "detect_modules", return_value=[]),
                patch.object(index, "_index_file_locked", side_effect=hold_index_lock),
            ):
                index.bootstrap_then_background()
                self.assertTrue(entered_index.wait(1.0), "background index never reached DB phase")
                started_close = time.monotonic()
                index.close()
                elapsed = time.monotonic() - started_close

            self.assertLess(elapsed, 2.0)
            self.assertTrue(index._stop_event.is_set())
            self.assertFalse(index._background_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
