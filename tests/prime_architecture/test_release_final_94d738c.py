from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kitt.core.pending_action import PendingAction, canonical_args_digest
from kitt.core.runtime import KittRuntime
from kitt.core.turn_events import TurnCompleted, TurnFailed
from kitt.extensions.errors import ExtensionStartupFailed, MCPTransportError, PluginLoadError
from kitt.extensions.manager import ExtensionManager
from kitt.extensions.manifest import parse_manifest_file
from kitt.extensions.mcp.transport import HTTPTransport
from kitt.extensions.plugins.loader import PluginLoader
from kitt.extensions.plugins.security import PluginTrustStore, prepare_trusted_plugin_snapshot
from kitt.goals.scheduler import GoalScheduler
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext


class TestFinalGoalFencing(unittest.TestCase):
    def test_goal_fence_uses_subject_conversation_for_retained_child(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "visible.txt").write_text("ok", encoding="utf-8")
            with KittRuntime.build(str(root)) as runtime:
                parent = runtime.history.new_conversation("parent")
                child_conv = runtime.history.new_conversation("child")
                goal = runtime.goals.create(
                    parent["id"],
                    "read",
                    capabilities=[CAP_REPO_READ],
                    max_wall_seconds=60,
                )
                scheduler = GoalScheduler(runtime.database, runtime.goals)
                lease_id = scheduler._claim(goal.id)
                self.assertIsNotNone(lease_id)

                child_ctx = ExecutionSecurityContext(
                    workspace_id=runtime.workspace_id,
                    conversation_id=child_conv["id"],
                    turn_id="turn",
                    origin="AGENT",
                    principal_type="CHILD",
                    principal_id="child1",
                    capabilities=frozenset({CAP_REPO_READ}),
                    trace_id="trace",
                    fencing_token=lease_id,
                    fencing_owner_id=scheduler.worker_id,
                    fencing_subject_type="GOAL",
                    fencing_subject_id=goal.id,
                    fencing_subject_conversation_id=parent["id"],
                )
                result = runtime.registry.execute_tool(
                    "read_file",
                    {"path": "visible.txt"},
                    turn_id="turn",
                    conversation_id=child_conv["id"],
                    workspace_id=runtime.workspace_id,
                    origin="AGENT",
                    security_context=child_ctx,
                )
                self.assertTrue(result.success, result.error)

    def test_child_approval_continuation_does_not_resume_goal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "visible.txt").write_text("ok", encoding="utf-8")
            with KittRuntime.build(str(root)) as runtime:
                parent = runtime.history.new_conversation("parent")
                child_conv = runtime.history.new_conversation("child")
                child_ctx = ExecutionSecurityContext(
                    workspace_id=runtime.workspace_id,
                    conversation_id=child_conv["id"],
                    turn_id="turn",
                    origin="AGENT",
                    principal_type="CHILD",
                    principal_id="child1",
                    capabilities=frozenset({CAP_REPO_READ}),
                    trace_id="trace",
                    fencing_subject_type="GOAL",
                    fencing_subject_id="goal1",
                    fencing_subject_conversation_id=parent["id"],
                )
                args = {"path": "visible.txt"}
                action_hash = runtime.registry.policy.generate_action_hash(
                    "read_file", args
                )
                approval_id = "req_turn"
                runtime.approval.register_request(
                    "turn",
                    child_conv["id"],
                    runtime.workspace_id,
                    action_hash,
                    approval_id,
                    tool_name="read_file",
                )
                grant = runtime.approval.issue_grant(
                    "turn",
                    child_conv["id"],
                    runtime.workspace_id,
                    action_hash,
                    approval_id=approval_id,
                )
                self.assertIsNotNone(grant)
                runtime.history.repo.save_message(
                    child_conv["id"], "turn", "user", "approve read"
                )
                pending = PendingAction(
                    id="pa_turn",
                    approval_request_id=approval_id,
                    turn_id="turn",
                    conversation_id=child_conv["id"],
                    workspace_id=runtime.workspace_id,
                    tool_name="read_file",
                    normalized_args=args,
                    action_hash=action_hash,
                    source_response_sha256=canonical_args_digest(args),
                    affected_paths=[],
                    before_hashes={},
                    created_at=1.0,
                    expires_at=9999999999.0,
                    state="pending",
                    security_context=child_ctx.to_dict(),
                )
                runtime.processor.pending_actions["turn"] = pending
                runtime.history.repo.save_pending_action(pending)
                child_calls = []
                goal_resumes = []
                runtime.children.on_approved_action_executed = (
                    lambda *payload: child_calls.append(payload) or True
                )
                runtime.goals.resume_after_approval = (
                    lambda *payload, **kw: goal_resumes.append((payload, kw))
                )

                events = list(runtime.processor.continue_turn("turn", grant))
                self.assertTrue(
                    any(isinstance(event, TurnCompleted) for event in events)
                )
                self.assertEqual(len(child_calls), 1)
                self.assertEqual(goal_resumes, [])


class TestApprovalTOCTOU(unittest.TestCase):
    def test_new_file_created_after_approval_is_denied(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with KittRuntime.build(str(root)) as runtime:
                conversation = runtime.history.new_conversation("approval")
                security = ExecutionSecurityContext.create_user_context(
                    runtime.workspace_id,
                    conversation["id"],
                    "turn",
                    capabilities={CAP_REPO_WRITE},
                )
                args = {
                    "patch": (
                        "new.py\n<<<<<<< SEARCH\n=======\nvalue = 1\n>>>>>>> REPLACE"
                    )
                }
                action_hash = runtime.registry.policy.generate_action_hash(
                    "apply_patch", args
                )
                approval_id = "req_turn"
                runtime.approval.register_request(
                    "turn",
                    conversation["id"],
                    runtime.workspace_id,
                    action_hash,
                    approval_id,
                    tool_name="apply_patch",
                )
                grant = runtime.approval.issue_grant(
                    "turn",
                    conversation["id"],
                    runtime.workspace_id,
                    action_hash,
                    approval_id=approval_id,
                )
                runtime.history.repo.save_message(
                    conversation["id"], "turn", "user", "approve patch"
                )
                pending = PendingAction(
                    id="pa_turn",
                    approval_request_id=approval_id,
                    turn_id="turn",
                    conversation_id=conversation["id"],
                    workspace_id=runtime.workspace_id,
                    tool_name="apply_patch",
                    normalized_args=args,
                    action_hash=action_hash,
                    source_response_sha256=canonical_args_digest(args),
                    affected_paths=["new.py"],
                    before_hashes={"new.py": None},
                    created_at=1.0,
                    expires_at=9999999999.0,
                    state="pending",
                    security_context=security.to_dict(),
                )
                runtime.processor.pending_actions["turn"] = pending
                runtime.history.repo.save_pending_action(pending)
                (root / "new.py").write_text("race\n", encoding="utf-8")

                first = next(runtime.processor.continue_turn("turn", grant))
                self.assertIsInstance(first, TurnFailed)
                self.assertIn("created after approval", first.error)


class TestPluginSnapshotAndImports(unittest.IsolatedAsyncioTestCase):
    def _create_plugin(self, root: Path, name: str, body: str) -> Path:
        plugin = root / ".kitt" / "plugins" / name
        plugin.mkdir(parents=True)
        (plugin / "plugin.py").write_text(body, encoding="utf-8")
        (plugin / "plugin.toml").write_text(
            "\n".join(
                [
                    f'name = "{name}"',
                    'version = "1.0.0"',
                    'api_version = "1"',
                    'entrypoint = "plugin:setup"',
                    "permissions = []",
                    "trusted_in_process = true",
                ]
            ),
            encoding="utf-8",
        )
        return plugin

    async def test_snapshot_cache_rehashes_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = self._create_plugin(
                root,
                "demo",
                "def setup(ctx):\n    return None\n",
            )
            manifest = parse_manifest_file(plugin / "plugin.toml")
            trust = PluginTrustStore(root, path=root / "trust.json")
            digest = trust.grant(manifest)
            snapshot = prepare_trusted_plugin_snapshot(manifest, digest, root)
            original = (snapshot / "plugin.py").read_text(encoding="utf-8")
            os.chmod(snapshot / "plugin.py", 0o600)
            (snapshot / "plugin.py").write_text("tampered\n", encoding="utf-8")

            repaired = prepare_trusted_plugin_snapshot(manifest, digest, root)
            self.assertEqual(repaired, snapshot)
            self.assertEqual(
                (repaired / "plugin.py").read_text(encoding="utf-8"),
                original,
            )

    async def test_loader_rejects_absolute_local_imports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = self._create_plugin(
                root,
                "badimports",
                "import helper\n"
                "def setup(ctx):\n"
                "    return helper.VALUE\n",
            )
            (plugin / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            manifest = parse_manifest_file(plugin / "plugin.toml")
            trust = PluginTrustStore(root, path=root / "trust.json")
            trust.grant(manifest)
            loader = PluginLoader(workspace_root=str(root), trust_store=trust)

            with self.assertRaises(PluginLoadError):
                await loader.load_async(manifest)

    async def test_loader_uses_digest_scoped_package_for_relative_imports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = self._create_plugin(
                root,
                "goodimports",
                "from .helper import VALUE\n"
                "def setup(ctx):\n"
                "    return VALUE\n",
            )
            (plugin / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
            manifest = parse_manifest_file(plugin / "plugin.toml")
            trust = PluginTrustStore(root, path=root / "trust.json")
            digest = trust.grant(manifest)
            loader = PluginLoader(workspace_root=str(root), trust_store=trust)

            await loader.load_async(manifest)
            prefix = f"kitt_plugin_goodimports_{digest}"
            self.assertTrue(
                any(name == prefix or name.startswith(f"{prefix}.") for name in sys.modules)
            )


class TestExtensionManagerStartup(unittest.IsolatedAsyncioTestCase):
    async def test_start_failure_rolls_back_and_emits_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            events = []

            class EventBus:
                def publish(self, name, payload):
                    events.append((name, payload))

            manager = ExtensionManager(workspace_root=temp_dir, event_bus=EventBus())
            calls = []

            async def broken_start_all():
                calls.append("start_all")
                raise RuntimeError("critical boom")

            async def stop_all():
                calls.append("stop_all")

            async def disconnect_all():
                calls.append("disconnect_all")

            manager.plugins.start_all = broken_start_all
            manager.plugins.stop_all = stop_all
            manager.mcp.disconnect_all = disconnect_all

            with self.assertRaises(ExtensionStartupFailed):
                await manager.start()
            self.assertEqual(manager.state, manager.STATE_STOPPED)
            self.assertFalse(manager._started)
            self.assertIn(("ExtensionStartupFailed", mock.ANY), events)
            self.assertIn("disconnect_all", calls)
            self.assertIn("stop_all", calls)


class TestHTTPTransport(unittest.IsolatedAsyncioTestCase):
    async def test_remote_plain_http_is_rejected(self):
        transport = HTTPTransport("http://example.com/mcp")
        with self.assertRaises(MCPTransportError):
            await transport.connect()

    async def test_sse_response_and_delete_close(self):
        methods = []

        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeResponse:
            def __init__(self, method: str):
                self.method = method
                self.headers = FakeHeaders(
                    {
                        "Content-Type": "text/event-stream",
                        "MCP-Session-Id": "sess-1",
                    }
                )
                self._lines = iter(
                    [
                        b"event: message\n",
                        b"data: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ok\":true}}\n",
                        b"\n",
                    ]
                )

            def read(self, _limit=None):
                if self.method == "DELETE":
                    return b""
                return (
                    b"event: message\n"
                    b"data: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ok\":true}}\n\n"
                )

            def readline(self, _limit=None):
                if self.method == "DELETE":
                    return b""
                return next(self._lines, b"")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def fake_urlopen(request, timeout=0):
            methods.append(request.get_method())
            return FakeResponse(request.get_method())

        transport = HTTPTransport("http://127.0.0.1:8765/mcp", timeout_seconds=1.0)
        with mock.patch.object(HTTPTransport, "_open", side_effect=fake_urlopen):
            await transport.connect()
            await transport.send(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
            message = await transport.receive()
            self.assertEqual(message["id"], 1)
            self.assertEqual(transport._session_id, "sess-1")
            await transport.close()

        self.assertEqual(methods, ["POST", "DELETE"])


if __name__ == "__main__":
    unittest.main()
