from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path

# Adjust import after copying into kitt/settings/control_center.py
from kitt.settings.control_center import (
    load_overlay,
    section,
    runtime_overrides,
    SettingsDescriptorProvider,
)


class ControlCenterOverlayTest(unittest.TestCase):
    def test_reads_section_and_filters_runtime(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "overrides.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "revision": 3,
                        "components": {
                            "agent.runtime": {
                                "safe_runtime_enabled": False,
                                "unknown": 1,
                            },
                            "agent.context": {"context_window_default": 12288},
                        },
                    }
                )
            )
            data = load_overlay(path)
            self.assertFalse(
                section("agent.runtime", overlay=data)["safe_runtime_enabled"]
            )

    def test_rejects_wrong_schema(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "overrides.json"
            path.write_text('{"schema_version":99,"components":{}}')
            with self.assertRaises(ValueError):
                load_overlay(path)

    def test_descriptor_provider_produces_valid_sections(self):
        sections = SettingsDescriptorProvider.get_sections()
        section_ids = [s["id"] for s in sections]
        self.assertIn("agent.runtime", section_ids)
        self.assertIn("agent.context", section_ids)
        self.assertIn("agent.security", section_ids)

    def test_safe_env_var_filtering(self):
        self.assertTrue(SettingsDescriptorProvider.is_safe_env_var("OPENAI_API_KEY"))
        self.assertTrue(SettingsDescriptorProvider.is_safe_env_var("KITT_DAEMON"))
        self.assertFalse(SettingsDescriptorProvider.is_safe_env_var("PATH"))
        self.assertFalse(SettingsDescriptorProvider.is_safe_env_var("LD_PRELOAD"))
        self.assertFalse(SettingsDescriptorProvider.is_safe_env_var("NODE_OPTIONS"))
        self.assertFalse(SettingsDescriptorProvider.is_safe_env_var("PYTHONPATH"))


if __name__ == "__main__":
    unittest.main()
