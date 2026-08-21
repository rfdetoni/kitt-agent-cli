import inspect
import socket
import unittest

from kitt.children.manager import ChildAgentManager
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_events import ThinkingCompleted
from kitt.core.turn_processor import TurnProcessor
from kitt.daemon.redaction import sanitize_public_event_payload
from kitt.daemon.server import DaemonServer
from kitt.remote.server import RemoteServer, _RemoteHTTPServerV6
from kitt.ui.event_bridge import TurnEventBridge
from kitt.ui.fallback import HeadlessUI, PlainLineUI


class MainHardeningTests(unittest.TestCase):
    def test_thinking_completed_has_no_thought_field(self):
        self.assertNotIn("thought", ThinkingCompleted.__dataclass_fields__)
        clean = sanitize_public_event_payload(
            "ThinkingCompleted", {"tokens": 4, "thought": "secret", "reasoning": "secret"}
        )
        self.assertEqual(clean, {"tokens": 4})

    def test_frontend_only_runtime_config_exists(self):
        self.assertTrue(hasattr(RuntimeConfig(), "frontend_only"))

    def test_cancel_is_scoped_and_never_global_child_shutdown(self):
        source = inspect.getsource(TurnProcessor.cancel_turn)
        self.assertIn("cancel_for_turn", source)
        self.assertNotIn("shutdown_all", source)
        self.assertTrue(hasattr(ChildAgentManager, "cancel_for_turn"))

    def test_daemon_cancel_requires_active_turn_ownership(self):
        source = inspect.getsource(DaemonServer._require_active_turn)
        self.assertIn("_active_turns", source)
        self.assertIn("requested session", source)

    def test_daemon_public_events_are_redacted_on_write_and_replay(self):
        self.assertIn("sanitize_public_event_payload", inspect.getsource(DaemonServer.record_event))
        self.assertIn("sanitize_public_event_payload", inspect.getsource(DaemonServer._get_events_since))

    def test_daemon_has_async_child_event_bridge(self):
        source = inspect.getsource(DaemonServer._attach_runtime_event_bridge)
        self.assertIn("call_soon_threadsafe", source)
        self.assertIn("ChildAgentFinished", source)

    def test_web_history_is_cursor_bounded(self):
        source = inspect.getsource(DaemonServer._session_detail)
        self.assertIn("512 * 1024", source)
        self.assertIn("384 * 1024", source)
        self.assertIn("messages_next_before", source)
        self.assertIn("message_limit", source)

    def test_direct_ui_tool_surface_is_narrow(self):
        source = inspect.getsource(DaemonServer._ui_tool_execute)
        self.assertIn('{"run_command", "child_spawn"}', source)
        self.assertIn("ExecutionSecurityContext", source)

    def test_ui_bridge_can_force_daemon_authority_before_first_turn(self):
        self.assertTrue(hasattr(TurnEventBridge, "ensure_daemon"))
        self.assertIn("_daemon_authoritative", inspect.getsource(PlainLineUI.run_turn))
        self.assertIn("_run_daemon_turn", inspect.getsource(HeadlessUI.run_async))

    def test_ipv6_server_class_and_url_brackets(self):
        self.assertEqual(_RemoteHTTPServerV6.address_family, socket.AF_INET6)
        self.assertEqual(RemoteServer._url_host("::1"), "[::1]")
        self.assertEqual(RemoteServer._url_host("127.0.0.1"), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
