from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kitt.extensions.errors import PluginManifestError
from kitt.extensions.manifest import parse_manifest_file
from kitt.extensions.plugins.loader import PluginLoader


def _manifest(name: str) -> str:
    return (
        f'name = "{name}"\n'
        'version = "1.0.0"\n'
        'api_version = "1"\n'
        'entrypoint = "plugin:setup"\n'
        'permissions = []\n'
        'trusted_in_process = true\n'
        'enabled_by_default = false\n'
        'is_critical = false\n'
    )


@unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
class TestPluginManifestPathHardening(unittest.TestCase):
    def test_manifest_final_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "actual.toml"
            target.write_text(_manifest("demo"), encoding="utf-8")
            link = root / "plugin.toml"
            link.symlink_to(target)

            with self.assertRaises(PluginManifestError):
                parse_manifest_file(link, source="workspace")

    def test_workspace_plugin_directory_symlink_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            external = Path(tmp) / "external-plugin"
            root.mkdir()
            external.mkdir()
            (external / "plugin.toml").write_text(
                _manifest("external"),
                encoding="utf-8",
            )
            plugins = root / ".kitt" / "plugins"
            plugins.mkdir(parents=True)
            (plugins / "external").symlink_to(
                external,
                target_is_directory=True,
            )

            loader = PluginLoader(
                workspace_root=str(root),
                global_plugins_dir=str(Path(tmp) / "global"),
            )
            self.assertEqual(loader.discover_manifests(), {})

    def test_workspace_kitt_symlink_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            external_kitt = Path(tmp) / "external-kitt"
            root.mkdir()
            (external_kitt / "plugins" / "demo").mkdir(parents=True)
            (external_kitt / "plugins" / "demo" / "plugin.toml").write_text(
                _manifest("demo"),
                encoding="utf-8",
            )
            (root / ".kitt").symlink_to(
                external_kitt,
                target_is_directory=True,
            )

            loader = PluginLoader(
                workspace_root=str(root),
                global_plugins_dir=str(Path(tmp) / "global"),
            )
            self.assertEqual(loader.discover_manifests(), {})


class TestPluginNameCollisionBoundary(unittest.TestCase):
    def test_workspace_cannot_shadow_global_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            global_dir = base / "global"
            root.mkdir()

            global_plugin = global_dir / "shared"
            global_plugin.mkdir(parents=True)
            (global_plugin / "plugin.toml").write_text(
                _manifest("shared"),
                encoding="utf-8",
            )

            workspace_plugin = root / ".kitt" / "plugins" / "shared"
            workspace_plugin.mkdir(parents=True)
            (workspace_plugin / "plugin.toml").write_text(
                _manifest("shared"),
                encoding="utf-8",
            )

            loader = PluginLoader(
                workspace_root=str(root),
                global_plugins_dir=str(global_dir),
            )
            manifests = loader.discover_manifests()

            self.assertEqual(set(manifests), {"shared"})
            self.assertEqual(manifests["shared"].source, "global")
            self.assertTrue(
                str(manifests["shared"].manifest_path).startswith(
                    str(global_dir.resolve())
                )
            )


if __name__ == "__main__":
    unittest.main()
