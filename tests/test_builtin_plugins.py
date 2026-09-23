"""Tests for bundled first-party plugin discovery and execution."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from kitt.extensions.manager import ExtensionManager
from kitt.tools.registry import ToolRegistry


EXPECTED = {
    "kitt-project-intel",
    "kitt-test-impact",
    "kitt-lsp",
    "kitt-openapi",
    "kitt-migration-guard",
    "kitt-git-worktree",
    "kitt-quality-report",
    "kitt-dependency-audit",
    "kitt-ci",
    "kitt-container",
    "kitt-release",
    "kitt-github",
    "kitt-database",
    "kitt-browser",
    "kitt-cloud",
    "kitt-observability",
}

OPT_IN = {
    "kitt-release",
    "kitt-github",
    "kitt-database",
    "kitt-browser",
    "kitt-cloud",
    "kitt-observability",
}


class TestBuiltinPlugins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        (self.root / "src" / "main" / "java").mkdir(parents=True)
        (self.root / "src" / "test" / "java").mkdir(parents=True)
        (self.root / "db" / "migration").mkdir(parents=True)
        (self.root / "pom.xml").write_text(
            "<project><parent><artifactId>spring-boot-starter-parent</artifactId>"
            "</parent></project>",
            encoding="utf-8",
        )
        (self.root / "src" / "main" / "java" / "UserService.java").write_text(
            "class UserService {}",
            encoding="utf-8",
        )
        (self.root / "src" / "test" / "java" / "UserServiceTest.java").write_text(
            "class UserServiceTest {}",
            encoding="utf-8",
        )
        (self.root / "db" / "migration" / "V1__drop.sql").write_text(
            "DROP TABLE obsolete_table;",
            encoding="utf-8",
        )

        self.tools = ToolRegistry(root_dir=str(self.root))
        self.manager = ExtensionManager(
            workspace_root=str(self.root),
            tool_registry=self.tools,
            plugin_trust_path=str(self.root / "trust.json"),
            plugin_state_path=str(self.root / "state.json"),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_discovers_all_bundled_plugins_as_distribution_trusted(self):
        manifests = self.manager.plugins.discover()
        self.assertTrue(EXPECTED.issubset(manifests))
        for plugin_id in EXPECTED:
            manifest = manifests[plugin_id]
            self.assertEqual(manifest.source, "builtin")
            self.assertTrue(self.manager.plugin_trust.is_trusted(manifest))
            self.assertEqual(
                self.manager.plugins.is_enabled(plugin_id, manifest),
                plugin_id not in OPT_IN,
            )

    def test_default_plugins_register_tools_and_produce_structured_results(self):
        async def run():
            await self.manager.plugins.start_all()
            try:
                names = {
                    item["name"]
                    for item in self.tools.get_tool_definitions()
                }
                self.assertIn("project_intel", names)
                self.assertIn("migration_guard", names)
                self.assertIn("test_impact", names)
                self.assertNotIn("release_plan", names)

                intel = self.tools.execute_tool("project_intel", {})
                self.assertTrue(intel.success)
                intel_data = json.loads(intel.output)
                self.assertIn("java", intel_data["languages"])
                self.assertIn("spring-boot", intel_data["frameworks"])

                migration = self.tools.execute_tool("migration_guard", {})
                self.assertTrue(migration.success)
                migration_data = json.loads(migration.output)
                self.assertEqual(migration_data["risk"], "critical")
                self.assertTrue(migration_data["destructive"])

                impact = self.tools.execute_tool(
                    "test_impact",
                    {"paths": ["src/main/java/UserService.java"]},
                )
                self.assertTrue(impact.success)
                impact_data = json.loads(impact.output)
                self.assertIn(
                    "src/test/java/UserServiceTest.java",
                    impact_data["candidate_tests"],
                )
            finally:
                await self.manager.plugins.stop_all()

        asyncio.run(run())

    def test_opt_in_plugin_can_be_enabled_without_network_or_mutation(self):
        async def run():
            self.manager.plugins.discover()
            self.manager.plugins.enable("kitt-release")
            await self.manager.plugins.start("kitt-release")
            try:
                result = self.tools.execute_tool(
                    "release_plan",
                    {"bump": "minor"},
                )
                self.assertTrue(result.success)
                payload = json.loads(result.output)
                self.assertFalse(payload["mutates"])
            finally:
                await self.manager.plugins.stop_all()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
