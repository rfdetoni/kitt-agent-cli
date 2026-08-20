from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.models import PluginManifest
from kitt.extensions.plugins.security import (
    PluginTrustStore,
    _secure_read_plugin_file,
    plugin_content_digest,
)


def _manifest(path: Path, name: str = "demo") -> PluginManifest:
    return PluginManifest(
        name=name,
        version="1.0.0",
        api_version="1",
        entrypoint="plugin:setup",
        permissions=set(),
        enabled_by_default=False,
        is_critical=False,
        source="workspace",
        manifest_path=path,
        trusted_in_process=True,
    )


@unittest.skipIf(os.name == "nt", "POSIX symlink/hardlink semantics")
class TestPluginDigestTOCTOUHardening(unittest.TestCase):
    def test_secure_reader_rejects_symlink_even_if_iterator_was_bypassed(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.py"
            target.write_text("print('external')\n", encoding="utf-8")
            link = root / "plugin.py"
            link.symlink_to(target)

            with self.assertRaises(PluginLoadError):
                _secure_read_plugin_file(link, "demo")

    def test_secure_reader_rejects_hardlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "original.py"
            original.write_text("print('x')\n", encoding="utf-8")
            linked = root / "linked.py"
            os.link(original, linked)

            with self.assertRaises(PluginLoadError):
                _secure_read_plugin_file(linked, "demo")

    def test_digest_rejects_workspace_plugins_ancestor_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            external_plugins = base / "external-plugins"
            workspace.mkdir()
            (workspace / ".kitt").mkdir()
            (external_plugins / "demo").mkdir(parents=True)
            manifest_path = external_plugins / "demo" / "plugin.toml"
            manifest_path.write_text("name='demo'\n", encoding="utf-8")
            (external_plugins / "demo" / "plugin.py").write_text(
                "print('external')\n",
                encoding="utf-8",
            )
            (workspace / ".kitt" / "plugins").symlink_to(
                external_plugins,
                target_is_directory=True,
            )

            lexical_manifest = (
                workspace / ".kitt" / "plugins" / "demo" / "plugin.toml"
            )
            manifest = _manifest(lexical_manifest)

            with self.assertRaises(PluginLoadError):
                plugin_content_digest(manifest, workspace)

    def test_trust_grant_rechecks_workspace_path_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            workspace = base / "workspace"
            external_plugins = base / "external-plugins"
            workspace.mkdir()
            (workspace / ".kitt").mkdir()
            (external_plugins / "demo").mkdir(parents=True)
            (external_plugins / "demo" / "plugin.toml").write_text(
                "name='demo'\n",
                encoding="utf-8",
            )
            (external_plugins / "demo" / "plugin.py").write_text(
                "print('external')\n",
                encoding="utf-8",
            )
            (workspace / ".kitt" / "plugins").symlink_to(
                external_plugins,
                target_is_directory=True,
            )

            manifest = _manifest(
                workspace / ".kitt" / "plugins" / "demo" / "plugin.toml"
            )
            store = PluginTrustStore(
                workspace,
                path=base / "plugin-trust.json",
            )

            with self.assertRaises(PluginLoadError):
                store.grant(manifest)


if __name__ == "__main__":
    unittest.main()
