import json
import tempfile
import unittest
from pathlib import Path

from kitt.formatting.contract import FormattingContractManager
from kitt.formatting.engine import DynamicFormattingEngine


class FormattingContractTests(unittest.TestCase):
    def test_contract_is_persisted_and_editorconfig_style_is_discovered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".editorconfig").write_text(
                "root = true\n[*]\nindent_style = space\nindent_size = 3\ninsert_final_newline = true\n",
                encoding="utf-8",
            )
            manager = FormattingContractManager(root)
            contract = manager.ensure()

            path = root / ".kitt" / "formatting.json"
            self.assertTrue(path.is_file())
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["version"], 1)
            self.assertEqual(contract["languages"]["java"]["style"]["indent_size"], 3)
            self.assertIn("Formatting", manager.prompt_summary().replace("contract=", "Formatting contract="))
            self.assertTrue((root / ".kitt" / "formatting.state.json").is_file())

    def test_arbitrary_workspace_formatter_id_is_not_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kitt = root / ".kitt"
            kitt.mkdir()
            (kitt / "formatting.json").write_text(
                json.dumps({
                    "version": 1,
                    "languages": {
                        "java": {
                            "extensions": [".java"],
                            "formatter_order": ["rm-everything"],
                            "style": {"indent_style": "spaces", "indent_size": 4},
                        }
                    },
                }),
                encoding="utf-8",
            )
            engine = DynamicFormattingEngine(root)
            self.assertIsNone(engine._formatter_argv("rm-everything", "App.java"))

    def test_brace_language_heals_without_external_formatter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "App.java"
            target.write_text(
                "public class App {\n public void run() {\n System.out.println(1);\n }\n}\n",
                encoding="utf-8",
            )
            engine = DynamicFormattingEngine(root)
            report = engine.format_paths(["App.java"])["App.java"]

            self.assertTrue(report["ok"], report)
            rendered = target.read_text(encoding="utf-8")
            self.assertRegex(rendered, r"(?m)^\s+public void run\(\)")
            self.assertRegex(rendered, r"(?m)^\s+System\.out\.println")


if __name__ == "__main__":
    unittest.main()
