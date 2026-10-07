import os
import unittest
from unittest.mock import patch

from kitt.reverse_proxy.client import ReverseProxyClient


class CaptureReverseProxyClient(ReverseProxyClient):
    def __init__(self):
        super().__init__(executable="kitt-reverse-proxy")
        self.calls = []
        self.refreshes = 0

    def _refresh_managed_control_plane(self):
        self.refreshes += 1
        self._managed_control_refreshed = True

    def _request(self, action, params, *fallback_args):
        self.calls.append((action, dict(params), tuple(fallback_args)))
        if action == "service.start":
            return {
                "instance": {
                    "id": "agent-owned",
                    "provider": "chatgpt",
                    "model": "chatgpt-web",
                    "target": "https://chatgpt.com/",
                    "profileId": "coding",
                    "host": "127.0.0.1",
                    "port": 3001,
                    "pid": 321,
                    "status": "running",
                    "startedAt": "now",
                    "logFile": "/tmp/kitt/reverse-proxy-agent-owned.log",
                }
            }
        if action == "service.stop":
            return {"stopped": True}
        return {}


class ReverseProxyManagedLifecycleTests(unittest.TestCase):
    def test_agent_logging_and_owner_are_forwarded_to_started_proxy(self):
        client = CaptureReverseProxyClient()
        with patch.dict(
            os.environ,
            {
                "KITT_LOG_LEVEL": "2",
                "KITT_LOG_CONTENT": "full",
                "KITT_LOG_FILE": "/tmp/kitt/agent-cli.log",
            },
            clear=False,
        ):
            instance = client.start_instance(
                "chatgpt",
                profile="coding",
                port=3001,
            )

        self.assertEqual(instance.id, "agent-owned")
        self.assertEqual(instance.log_file, "/tmp/kitt/reverse-proxy-agent-owned.log")
        self.assertEqual(client.refreshes, 1)
        action, params, fallback = client.calls[0]
        self.assertEqual(action, "service.start")
        self.assertEqual(params["log_level"], 2)
        self.assertEqual(params["log_content"], "full")
        self.assertEqual(params["log_file"], "/tmp/kitt/agent-cli.log")
        self.assertEqual(params["owner_pid"], os.getpid())
        self.assertIn("--log-level", fallback)
        self.assertIn("2", fallback)
        self.assertIn("--log-content", fallback)
        self.assertIn("full", fallback)
        self.assertIn("--owner-pid", fallback)
        self.assertIn(str(os.getpid()), fallback)
        self.assertIn("agent-owned", client._owned_instance_ids)

    def test_refreshes_resident_control_plane_once_before_managed_start(self):
        client = ReverseProxyClient(executable="kitt-reverse-proxy")
        commands = []
        ready = iter([True, False])

        def fake_call(*args):
            commands.append(args)
            if args[:2] == ("control", "ensure"):
                return {"schema_version": 1, "ready": True, "started": True}
            return {"schema_version": 1, "stopped": True}

        with patch.object(client, "_call", side_effect=fake_call), patch.object(
            client,
            "_control_server_ready",
            side_effect=lambda: next(ready, False),
        ), patch("kitt.reverse_proxy.client.time.sleep"):
            client._refresh_managed_control_plane()
            client._refresh_managed_control_plane()

        self.assertEqual(
            commands,
            [("control", "stop"), ("control", "ensure")],
        )
        self.assertTrue(client._managed_control_refreshed)

    def test_shutdown_stops_only_instances_started_by_this_client(self):
        client = CaptureReverseProxyClient()
        with patch.dict(os.environ, {"KITT_LOG_LEVEL": "0"}, clear=False):
            client.start_instance("chatgpt", instance_id="agent-owned")

        stopped = client.stop_owned_instances()

        self.assertEqual(stopped, 1)
        self.assertEqual(client._owned_instance_ids, set())
        stop_calls = [
            params for action, params, _fallback in client.calls
            if action == "service.stop"
        ]
        self.assertEqual(stop_calls, [{"id": "agent-owned"}])


if __name__ == "__main__":
    unittest.main()
