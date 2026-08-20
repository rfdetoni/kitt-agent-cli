"""Tests for plugin loader discovery, loading, lifecycle, and rollback on failure."""
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


class TestExtensionLoader(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
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
