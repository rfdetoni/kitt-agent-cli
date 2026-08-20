from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.extensions.errors import PluginLoadError
from kitt.extensions.models import PluginManifest
from kitt.extensions.plugins.security import (
    _assert_private_cache_dir,
    _verify_snapshot,
    plugin_content_digest,
    prepare_trusted_plugin_snapshot,
)


def _manifest(root: Path, name: str = "demo") -> PluginManifest:
    return PluginManifest(
        name=name,
        version="1.0.0",
        api_version="1",
        entrypoint="plugin:setup",
        permissions=set(),
        enabled_by_default=False,
        is_critical=False,
        source="runtime",
        manifest_path=root / "plugin.toml",
        trusted_in_process=True,
    )


def _write_plugin(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.toml").write_text(
        "name='demo'\n",
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(
        "def setup(ctx):\n    return None\n",
        encoding="utf-8",
    )


@unittest.skipIf(os.name == "nt", "POSIX symlink/permission semantics")
class TestTrustedSnapshotCacheBoundary(unittest.TestCase):
    def test_private_cache_dir_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "target"
            target.mkdir()
            os.chmod(target, 0o700)
            link = base / "cache"
            link.symlink_to(target, target_is_directory=True)

            with self.assertRaises(PluginLoadError):
                _assert_private_cache_dir(link)

    def test_private_cache_dir_repairs_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache"
            path.mkdir()
            os.chmod(path, 0o777)

            result = _assert_private_cache_dir(path)
            self.assertEqual(result, path.absolute())
            self.assertEqual(
                os.stat(path).st_mode & 0o777,
                0o700,
            )

    def test_verify_snapshot_rejects_symlink_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            _write_plugin(source)
            manifest = _manifest(source)
            digest = plugin_content_digest(manifest)

            target = base / "target"
            _write_plugin(target)
            link = base / "snapshot"
            link.symlink_to(target, target_is_directory=True)

            self.assertFalse(
                _verify_snapshot(
                    manifest,
                    link,
                    digest,
                )
            )

    def test_prepare_replaces_existing_snapshot_symlink_without_following(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            _write_plugin(source)
            manifest = _manifest(source)
            digest = plugin_content_digest(manifest)

            cache = base / "trusted-cache"
            cache.mkdir()
            os.chmod(cache, 0o700)

            workspace_cache = cache / "workspace-key" / manifest.name
            workspace_cache.mkdir(parents=True)
            os.chmod(cache / "workspace-key", 0o700)
            os.chmod(workspace_cache, 0o700)

            external = base / "external"
            external.mkdir()
            marker = external / "marker.txt"
            marker.write_text("do-not-touch", encoding="utf-8")

            symlink = workspace_cache / digest
            symlink.symlink_to(
                external,
                target_is_directory=True,
            )

            with (
                patch(
                    "kitt.extensions.plugins.security._trusted_plugin_cache_root",
                    return_value=cache,
                ),
                patch(
                    "kitt.extensions.plugins.security._workspace_key",
                    return_value="workspace-key",
                ),
            ):
                result = prepare_trusted_plugin_snapshot(
                    manifest,
                    digest,
                    base / "workspace",
                )

            self.assertFalse(result.is_symlink())
            self.assertTrue(result.is_dir())
            self.assertEqual(
                marker.read_text(encoding="utf-8"),
                "do-not-touch",
            )
            self.assertTrue(_verify_snapshot(manifest, result, digest))


if __name__ == "__main__":
    unittest.main()
