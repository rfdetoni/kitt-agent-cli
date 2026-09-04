import tempfile
import unittest

from kitt.core.runtime import KittRuntime


class TestFreshCliSession(unittest.TestCase):
    def test_fresh_session_is_lazy_and_does_not_resume_latest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with KittRuntime.build(tmp) as runtime:
                old = runtime.history.new_conversation("old")
                self.assertEqual(runtime.history.get_or_create_active()["id"], old["id"])

                runtime.history.begin_fresh_session()
                self.assertIsNone(runtime.history.get_active_read_only())

                new = runtime.history.get_or_create_active()
                self.assertNotEqual(new["id"], old["id"])

                ids = {item["id"] for item in runtime.history.list_history(limit=20)}
                self.assertIn(old["id"], ids)
                self.assertIn(new["id"], ids)

    def test_resume_cancels_fresh_session_marker(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            with KittRuntime.build(tmp) as runtime:
                old = runtime.history.new_conversation("old")
                runtime.history.begin_fresh_session()

                resumed = runtime.history.resume_conversation(old["id"][:8])
                self.assertIsNotNone(resumed)
                self.assertEqual(runtime.history.get_or_create_active()["id"], old["id"])


if __name__ == "__main__":
    unittest.main()
