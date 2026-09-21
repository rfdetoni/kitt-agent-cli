import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.formatting.contract import (
    FORMAT_CONTRACT_VERSION,
    GLOBAL_FORMATTING_VERSION,
    FormattingContractManager,
    GlobalFormattingRegistry,
)
from kitt.formatting.engine import DynamicFormattingEngine


class FormattingContractTests(unittest.TestCase):
    def _paths(self, temp: str) -> tuple[Path, Path]:
        root = Path(temp) / "project"
        home = Path(temp) / "home"
        root.mkdir(parents=True)
        home.mkdir(parents=True)
        return root, home

    def test_global_baseline_is_persisted_and_project_keeps_only_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            (root / ".editorconfig").write_text(
                "root = true\n[*]\nindent_style = space\nindent_size = 3\n"
                "insert_final_newline = true\n",
                encoding="utf-8",
            )
            with patch("pathlib.Path.home", return_value=home):
                manager = FormattingContractManager(root)
                contract = manager.ensure()
                language, java = manager.language_contract("src/App.java")

            global_path = home / ".kitt" / "formatting" / "baselines.json"
            local_path = root / ".kitt" / "formatting.json"
            self.assertTrue(global_path.is_file())
            self.assertTrue(local_path.is_file())

            global_doc = json.loads(global_path.read_text(encoding="utf-8"))
            local_doc = json.loads(local_path.read_text(encoding="utf-8"))
            self.assertEqual(global_doc["version"], GLOBAL_FORMATTING_VERSION)
            self.assertIn("java", global_doc["languages"])
            self.assertEqual(local_doc["version"], FORMAT_CONTRACT_VERSION)
            self.assertEqual(local_doc["languages"], {})
            self.assertEqual(
                local_doc["project_defaults"]["style"]["indent_size"],
                3,
            )
            self.assertEqual(language, "java")
            self.assertEqual(java["style"]["indent_size"], 3)
            self.assertEqual(java["formatter_order"], ["google-java-format"])
            self.assertEqual(contract["baseline"]["scope"], "user-global")

    def test_second_project_reuses_successful_global_formatter_order(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            home = base / "home"
            project_a = base / "a"
            project_b = base / "b"
            home.mkdir()
            project_a.mkdir()
            project_b.mkdir()

            with patch("pathlib.Path.home", return_value=home):
                first = FormattingContractManager(project_a)
                first.ensure()
                first.remember_formatter_success("javascript", "prettier")

                second = FormattingContractManager(project_b)
                language, profile = second.language_contract("src/app.js")

            self.assertEqual(language, "javascript")
            self.assertEqual(profile["formatter_order"][0], "prettier")
            stored = json.loads(
                (home / ".kitt" / "formatting" / "baselines.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                stored["usage"]["javascript"]["formatter_successes"]["prettier"],
                1,
            )

    def test_prompt_summary_sends_only_delta_not_all_language_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            with patch("pathlib.Path.home", return_value=home):
                manager = FormattingContractManager(root)
                summary = manager.prompt_summary(paths=["src/App.java"])

            self.assertIn("global-first/delta-only", summary)
            self.assertIn("~/.kitt/formatting/baselines.json", summary)
            self.assertNotIn("google-java-format", summary)
            self.assertNotIn("typescript:", summary)
            self.assertLess(len(summary), 400)

    def test_discovered_project_style_refreshes_when_editorconfig_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            editor = root / ".editorconfig"
            editor.write_text(
                "[*]\nindent_style = space\nindent_size = 2\n",
                encoding="utf-8",
            )
            with patch("pathlib.Path.home", return_value=home):
                first = FormattingContractManager(root)
                _language, profile = first.language_contract("web/app.ts")
                self.assertEqual(profile["style"]["indent_size"], 2)

                editor.write_text(
                    "[*]\nindent_style = space\nindent_size = 5\n",
                    encoding="utf-8",
                )
                second = FormattingContractManager(root)
                _language, refreshed = second.language_contract("web/app.ts")

            self.assertEqual(refreshed["style"]["indent_size"], 5)
            persisted = json.loads(
                (root / ".kitt" / "formatting.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                persisted["project_defaults_source"],
                "discovery",
            )
            self.assertEqual(
                persisted["project_defaults"]["style"]["indent_size"],
                5,
            )

    def test_project_override_wins_over_global_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            kitt = root / ".kitt"
            kitt.mkdir()
            (kitt / "formatting.json").write_text(
                json.dumps(
                    {
                        "version": FORMAT_CONTRACT_VERSION,
                        "languages": {
                            "javascript": {
                                "formatter_order": ["prettier", "biome"],
                                "style": {"indent_size": 6},
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("pathlib.Path.home", return_value=home):
                manager = FormattingContractManager(root)
                _language, profile = manager.language_contract("web/app.js")

            self.assertEqual(profile["formatter_order"], ["prettier", "biome"])
            self.assertEqual(profile["style"]["indent_size"], 6)

    def test_v1_generated_contract_is_compacted_during_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            kitt = root / ".kitt"
            kitt.mkdir()
            old_java = {
                "extensions": [".java"],
                "parser": {"engine": "tree-sitter", "language": "java"},
                "formatter_order": ["google-java-format"],
                "style": {
                    "indent_style": "spaces",
                    "indent_size": 4,
                    "final_newline": True,
                },
                "healing": {"enabled": True, "preserve_semantics": True},
            }
            (kitt / "formatting.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "managed_by": "kitt-agent-cli",
                        "languages": {"java": old_java},
                    }
                ),
                encoding="utf-8",
            )

            with patch("pathlib.Path.home", return_value=home):
                manager = FormattingContractManager(root)
                contract = manager.ensure()

            self.assertEqual(contract["version"], FORMAT_CONTRACT_VERSION)
            self.assertEqual(contract["languages"], {})
            persisted = json.loads(
                (kitt / "formatting.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["languages"], {})
            self.assertEqual(persisted["baseline"]["scope"], "user-global")

    def test_global_style_drives_new_file_healing_without_llm(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            with patch("pathlib.Path.home", return_value=home):
                engine = DynamicFormattingEngine(root)
                prepared = engine.prepare_content(
                    "web/app.ts",
                    "function run() {\nconsole.log('ok');\n}\n",
                )

            self.assertIn("\n  console.log('ok');\n", prepared.content)
            self.assertTrue(prepared.normalized)

    def test_editorconfig_style_overrides_global_healer_for_new_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            (root / ".editorconfig").write_text(
                "[*]\nindent_style = space\nindent_size = 3\n",
                encoding="utf-8",
            )
            with patch("pathlib.Path.home", return_value=home):
                engine = DynamicFormattingEngine(root)
                prepared = engine.prepare_content(
                    "web/app.ts",
                    "function run() {\nconsole.log('ok');\n}\n",
                )

            self.assertIn("\n   console.log('ok');\n", prepared.content)

    def test_arbitrary_workspace_formatter_id_is_not_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            kitt = root / ".kitt"
            kitt.mkdir()
            (kitt / "formatting.json").write_text(
                json.dumps(
                    {
                        "version": FORMAT_CONTRACT_VERSION,
                        "languages": {
                            "java": {
                                "formatter_order": ["rm-everything"],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with patch("pathlib.Path.home", return_value=home):
                engine = DynamicFormattingEngine(root)
                self.assertIsNone(
                    engine._formatter_argv("rm-everything", "App.java")
                )

    def test_global_registry_is_private_state_not_project_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root, home = self._paths(temp)
            with patch("pathlib.Path.home", return_value=home):
                registry = GlobalFormattingRegistry()
                registry.ensure()

            self.assertTrue(
                (home / ".kitt" / "formatting" / "baselines.json").is_file()
            )
            self.assertFalse((root / ".kitt" / "formatting" / "baselines.json").exists())


if __name__ == "__main__":
    unittest.main()
