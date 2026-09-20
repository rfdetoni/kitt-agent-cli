from __future__ import annotations

import unittest

from kitt.extensions.errors import (
    PluginLoadError,
    PluginManifestError,
)
from kitt.extensions.manifest import parse_manifest_data
from kitt.extensions.models import PluginManifest
from kitt.extensions.plugins.registry import PluginRegistry
from kitt.extensions.plugins.security import PluginTrustStore


class _StateStore:
    def load(self):
        return set(), set()

    def set_enabled(self, name, enabled):
        return None


class _TrustStore:
    def __init__(self, trusted: bool):
        self.trusted = trusted

    def is_trusted(self, manifest):
        return self.trusted


class _Loader:
    def __init__(
        self,
        manifest: PluginManifest,
        trusted: bool,
        fail_start_load: bool = False,
    ):
        self.workspace_root = "."
        self.manifest = manifest
        self.trust_store = _TrustStore(trusted)
        self.fail_start_load = fail_start_load
        self.load_calls = 0
        self.hook_registry = None
        self.tool_registry = None

    def discover_manifests(self):
        return {self.manifest.name: self.manifest}

    async def load_async(self, manifest):
        self.load_calls += 1
        if self.fail_start_load:
            raise RuntimeError("boom")
        raise AssertionError("load_async should not be called")


class TestPluginAutostartTrustBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_untrusted_critical_default_plugin_cannot_abort_startup(
        self,
    ):
        manifest = PluginManifest(
            name="evil",
            version="1.0.0",
            api_version="1",
            entrypoint="plugin:setup",
            enabled_by_default=True,
            is_critical=True,
            trusted_in_process=True,
            source="workspace",
        )
        loader = _Loader(
            manifest,
            trusted=False,
            fail_start_load=True,
        )
        registry = PluginRegistry(
            loader=loader,
            state_store=_StateStore(),
        )

        await registry.start_all()
        self.assertEqual(loader.load_calls, 0)

    async def test_trusted_critical_plugin_failure_still_aborts(self):
        manifest = PluginManifest(
            name="critical",
            version="1.0.0",
            api_version="1",
            entrypoint="plugin:setup",
            enabled_by_default=True,
            is_critical=True,
            trusted_in_process=True,
            source="workspace",
        )
        loader = _Loader(
            manifest,
            trusted=True,
            fail_start_load=True,
        )
        registry = PluginRegistry(
            loader=loader,
            state_store=_StateStore(),
        )

        with self.assertRaises(PluginLoadError):
            await registry.start_all()
        self.assertEqual(loader.load_calls, 1)


class TestPluginManifestBooleanSchema(unittest.TestCase):
    @staticmethod
    def _base():
        return {
            "name": "demo",
            "version": "1.0.0",
            "api_version": "1",
            "entrypoint": "plugin:setup",
            "permissions": [],
            "trusted_in_process": False,
        }

    def test_enabled_by_default_string_false_is_rejected(self):
        data = self._base()
        data["enabled_by_default"] = "false"
        with self.assertRaises(PluginManifestError):
            parse_manifest_data(data)

    def test_is_critical_string_false_is_rejected(self):
        data = self._base()
        data["is_critical"] = "false"
        with self.assertRaises(PluginManifestError):
            parse_manifest_data(data)

    def test_real_booleans_are_preserved(self):
        data = self._base()
        data["enabled_by_default"] = False
        data["is_critical"] = False
        manifest = parse_manifest_data(data)
        self.assertFalse(manifest.enabled_by_default)
        self.assertFalse(manifest.is_critical)

    def test_content_trust_does_not_require_in_process_opt_in(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            plugin_dir = root / ".kitt" / "plugins" / "demo"
            plugin_dir.mkdir(parents=True)
            manifest_path = plugin_dir / "plugin.toml"
            manifest_path.write_text(
                "\n".join([
                    'name = "demo"',
                    'version = "1.0.0"',
                    'api_version = "1"',
                    'entrypoint = "plugin:setup"',
                    'permissions = []',
                    'trusted_in_process = false',
                ]) + "\n",
                encoding="utf-8",
            )
            (plugin_dir / "plugin.py").write_text(
                "def setup(ctx):\n    return None\n",
                encoding="utf-8",
            )
            manifest = parse_manifest_data(
                self._base(),
                manifest_path=manifest_path,
                source="workspace",
            )
            store = PluginTrustStore(
                root,
                path=root / "plugin-trust.json",
            )
            digest = store.grant(manifest)
            self.assertTrue(digest)
            self.assertTrue(store.is_trusted(manifest))


if __name__ == "__main__":
    unittest.main()
