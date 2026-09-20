"""Tests for plugin loader discovery, loading, lifecycle, and rollback on failure."""
import asyncio
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.hooks.registry import HookRegistry
from kitt.extensions.models import PluginState
from kitt.extensions.plugins.loader import PluginLoader
from kitt.extensions.plugins.registry import PluginRegistry
from kitt.extensions.plugins.security import PluginTrustStore
from kitt.extensions.plugins.worker import PluginWorkerSandbox
from kitt.tools.registry import ToolRegistry


class TestExtensionLoader(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp_dir.name)
        self.ws_plugins_dir = self.root / ".kitt" / "plugins"
        self.ws_plugins_dir.mkdir(parents=True, exist_ok=True)
        self.hooks = HookRegistry()
        self.loader = PluginLoader(
            workspace_root=str(self.root), hook_registry=self.hooks,
            trust_store=PluginTrustStore(self.root, path=self.root / 'trust.json'),
        )
        self.registry = PluginRegistry(loader=self.loader)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_discover_and_load_valid_plugin(self):
        p_dir = self.ws_plugins_dir / "test-plugin"
        p_dir.mkdir(parents=True, exist_ok=True)

        manifest_file = p_dir / "plugin.toml"
        manifest_file.write_text(
            textwrap.dedent("""
            name = "test-plugin"
            version = "1.0.0"
            api_version = "1"
            entrypoint = "plugin:setup"
            permissions = ["events.read"]
            trusted_in_process = true
            """),
            encoding="utf-8",
        )

        code_file = p_dir / "plugin.py"
        code_file.write_text(
            textwrap.dedent("""
            def setup(ctx):
                ctx.logger.info("Setting up test-plugin")
                def on_start(payload):
                    pass
                ctx.hooks.register("app.started", on_start)
            """),
            encoding="utf-8",
        )

        manifests = self.registry.discover()
        self.assertIn("test-plugin", manifests)
        self.loader.trust_store.grant(manifests["test-plugin"])

        instance = self.registry.load("test-plugin")
        self.assertEqual(instance.state, PluginState.LOADED)
        self.assertEqual(len(self.hooks.get_chain("app.started")), 1)

    def test_default_plugin_runs_in_worker_and_proxies_registered_tool(self):
        p_dir = self.ws_plugins_dir / "worker-plugin"
        p_dir.mkdir(parents=True, exist_ok=True)

        (p_dir / "plugin.toml").write_text(
            textwrap.dedent("""
            name = "worker-plugin"
            version = "1.0.0"
            api_version = "1"
            entrypoint = "plugin:setup"
            permissions = ["tools.register"]
            """),
            encoding="utf-8",
        )
        (p_dir / "plugin.py").write_text(
            textwrap.dedent("""
            import os

            def setup(ctx):
                ctx.tools.register(
                    "worker_pid",
                    lambda args: str(os.getpid()),
                    description="Return isolated worker PID",
                    schema={},
                )
            """),
            encoding="utf-8",
        )

        tool_registry = ToolRegistry(root_dir=str(self.root))
        worker_loader = PluginLoader(
            workspace_root=str(self.root),
            hook_registry=self.hooks,
            tool_registry=tool_registry,
            trust_store=PluginTrustStore(
                self.root,
                path=self.root / "worker-trust.json",
            ),
        )
        worker_registry = PluginRegistry(loader=worker_loader)
        try:
            manifests = worker_registry.discover()
            manifest = manifests["worker-plugin"]
            self.assertFalse(manifest.trusted_in_process)
            worker_loader.trust_store.grant(manifest)

            instance = worker_registry.load("worker-plugin")
            self.assertEqual(instance.state, PluginState.LOADED)
            self.assertEqual(instance.execution_mode, "worker")
            self.assertIsNotNone(instance.worker_client)
            self.assertIsNone(instance.worker_client._process.poll())
            if os.name != "nt":
                self.assertEqual(instance.worker_client.ipc_transport, "unix")
            self.assertIsInstance(instance.worker_sandbox, dict)

            result = tool_registry.execute_tool(
                "worker_pid",
                {},
                origin="SAFE_RUNTIME_BROKER",
            )
            self.assertTrue(result.success, result.error)
            self.assertNotEqual(int(result.output), os.getpid())

            asyncio.run(worker_registry.unload("worker-plugin"))
            missing = tool_registry.execute_tool(
                "worker_pid",
                {},
                origin="SAFE_RUNTIME_BROKER",
            )
            self.assertFalse(missing.success)
        finally:
            if worker_registry.get("worker-plugin") is not None:
                asyncio.run(worker_registry.unload("worker-plugin"))
            tool_registry.close()


    def test_worker_bubblewrap_plan_scopes_workspace_and_network(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            snapshot = base / "snapshot"
            ipc_dir = base / "ipc"
            worker_home = base / "home"
            home = base / "user-home"
            for path in (workspace, snapshot, ipc_dir, worker_home, home):
                path.mkdir(parents=True, exist_ok=True)
            (workspace / ".git").mkdir()
            (workspace / ".kitt").mkdir()
            fake_bwrap = base / "bwrap"
            fake_bwrap.write_text("", encoding="utf-8")

            sandbox = PluginWorkerSandbox(
                workspace,
                {"tools.register"},
                bwrap_path=str(fake_bwrap),
                bwrap_version=(0, 12, 0),
                auto_detect=False,
                home_dir=home,
            )
            plan = sandbox.plan(
                ["python", "worker.py"],
                snapshot_root=snapshot,
                ipc_dir=ipc_dir,
                worker_home=worker_home,
                env={"PATH": os.environ.get("PATH", "")},
            )

            self.assertEqual(plan.backend, "bubblewrap")
            self.assertTrue(plan.strong)
            self.assertTrue(plan.network_isolated)
            self.assertEqual(plan.workspace_access, "none")
            self.assertIn("--unshare-net", plan.argv)

            def has_sequence(*items):
                width = len(items)
                return any(
                    plan.argv[index:index + width] == list(items)
                    for index in range(len(plan.argv) - width + 1)
                )

            self.assertTrue(
                has_sequence("--tmpfs", str(workspace.resolve()))
            )
            self.assertTrue(
                has_sequence(
                    "--ro-bind",
                    str(snapshot.resolve()),
                    str(snapshot.resolve()),
                )
            )
            self.assertTrue(
                has_sequence(
                    "--ro-bind",
                    str(ipc_dir.resolve()),
                    str(ipc_dir.resolve()),
                )
            )

            write_network = PluginWorkerSandbox(
                workspace,
                {"filesystem.write", "network"},
                bwrap_path=str(fake_bwrap),
                bwrap_version=(0, 12, 0),
                auto_detect=False,
                home_dir=home,
            ).plan(
                ["python", "worker.py"],
                snapshot_root=snapshot,
                ipc_dir=ipc_dir,
                worker_home=worker_home,
                env={},
            )
            self.assertEqual(write_network.workspace_access, "write")
            self.assertFalse(write_network.network_isolated)
            self.assertNotIn("--unshare-net", write_network.argv)
            self.assertTrue(
                has_sequence(
                    "--bind",
                    str(workspace.resolve()),
                    str(workspace.resolve()),
                )
                or [
                    "--bind",
                    str(workspace.resolve()),
                    str(workspace.resolve()),
                ] in [
                    write_network.argv[i:i + 3]
                    for i in range(max(0, len(write_network.argv) - 2))
                ]
            )

    def test_transactional_rollback_on_setup_failure(self):
        p_dir = self.ws_plugins_dir / "faulty-plugin"
        p_dir.mkdir(parents=True, exist_ok=True)

        manifest_file = p_dir / "plugin.toml"
        manifest_file.write_text(
            textwrap.dedent("""
            name = "faulty-plugin"
            version = "1.0.0"
            api_version = "1"
            entrypoint = "plugin:setup"
            permissions = ["events.read"]
            trusted_in_process = true
            """),
            encoding="utf-8",
        )

        code_file = p_dir / "plugin.py"
        code_file.write_text(
            textwrap.dedent("""
            def setup(ctx):
                ctx.hooks.register("app.started", lambda x: None)
                # Fail midway through setup
                raise RuntimeError("Boom! Setup exploded.")
            """),
            encoding="utf-8",
        )

        manifests = self.registry.discover()
        self.loader.trust_store.grant(manifests["faulty-plugin"])
        with self.assertRaises(PluginLoadError):
            self.registry.load("faulty-plugin")

        # Verify transactional rollback: registered hook was cleanly undone
        chain = self.hooks.get_chain("app.started")
        self.assertEqual(len(chain), 0)


if __name__ == "__main__":
    unittest.main()
