"""Tests for plugin manifest validation, schema enforcement, and bounds limits."""
import tempfile
import unittest
from pathlib import Path

from kitt.extensions.errors import (
    PluginCompatibilityError,
    PluginManifestError,
    PluginPermissionError,
)
from kitt.extensions.manifest import (
    MAX_MANIFEST_SIZE_BYTES,
    parse_manifest_data,
    parse_manifest_file,
    validate_plugin_name,
)


class TestExtensionManifest(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_valid_manifest_parsing(self):
        manifest_file = self.root / "plugin.toml"
        toml_content = """
        name = "my-test-plugin"
        version = "1.2.3"
        api_version = "1"
        entrypoint = "main:init"
        description = "Test plugin"
        author = "Developer"
        permissions = ["events.read", "tools.observe"]
        dependencies = ["requests"]
        """
        manifest_file.write_text(toml_content, encoding="utf-8")

        manifest = parse_manifest_file(manifest_file)
        self.assertEqual(manifest.name, "my-test-plugin")
        self.assertEqual(manifest.version, "1.2.3")
        self.assertEqual(manifest.api_version, "1")
        self.assertEqual(manifest.entrypoint, "main:init")
        self.assertIn("events.read", manifest.permissions)
        self.assertIn("tools.observe", manifest.permissions)
        self.assertEqual(manifest.dependencies, ["requests"])

    def test_invalid_plugin_name_and_traversal(self):
        with self.assertRaises(PluginManifestError):
            validate_plugin_name("../bad-plugin")

        with self.assertRaises(PluginManifestError):
            validate_plugin_name("bad/plugin")

        with self.assertRaises(PluginManifestError):
            validate_plugin_name("")

    def test_missing_required_fields(self):
        # Missing entrypoint
        with self.assertRaises(PluginManifestError):
            parse_manifest_data({"name": "test", "version": "1.0.0", "api_version": "1"})

        # Missing version
        with self.assertRaises(PluginManifestError):
            parse_manifest_data({"name": "test", "entrypoint": "plugin:setup", "api_version": "1"})

    def test_incompatible_api_version(self):
        with self.assertRaises(PluginCompatibilityError):
            parse_manifest_data({
                "name": "future-plugin",
                "version": "1.0.0",
                "api_version": "999",
                "entrypoint": "plugin:setup",
            })

    def test_unknown_permission(self):
        with self.assertRaises(PluginPermissionError):
            parse_manifest_data({
                "name": "greedy-plugin",
                "version": "1.0.0",
                "api_version": "1",
                "entrypoint": "plugin:setup",
                "permissions": ["super_admin_root_access"],
            })

    def test_oversized_manifest_rejected(self):
        oversized_file = self.root / "huge_plugin.toml"
        oversized_file.write_bytes(b"a = 1\n" * (MAX_MANIFEST_SIZE_BYTES // 4))

        with self.assertRaises(PluginManifestError):
            parse_manifest_file(oversized_file)


if __name__ == "__main__":
    unittest.main()
