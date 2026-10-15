"""Offline lexicon: real SQLite compiler boundary and protected prompt semantics."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kitt.context_filter.local_intent import deterministic_intent
from kitt.context_filter.lexicon import lexical_intent
from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.domain.entities import ModelProfile


class LexiconContracts(unittest.TestCase):
    def test_curated_commands_trilingual(self):
        for prompt, expected in (
            ("Conserte o erro em UserService.", "DEBUG"),
            ("Resolve the bug in UserService.", "DEBUG"),
            ("Soluciona el fallo de autenticación.", "DEBUG"),
            ("Desenvolva uma integração de API.", "IMPLEMENT"),
            ("Develop an API integration.", "IMPLEMENT"),
            ("Desarrolla una integración API.", "IMPLEMENT"),
            ("Examine o código do UserService.", "REVIEW"),
            ("Examine the implementation of UserService.", "REVIEW"),
            ("Examina el código UserService.", "REVIEW"),
        ):
            with self.subTest(prompt=prompt):
                self.assertEqual(deterministic_intent(prompt), expected)

    def test_negated_multi_step_and_continuations_abstain(self):
        for prompt in (
            "Conserte o erro sem alterar o código.",
            "Resolve the bug, but do not edit files.",
            "Soluciona el fallo sin cambiar el código.",
            "Desenvolva e depois execute o deploy.",
            "Continue a correção anterior.",
            "How to fix UserService, without changing code?",
        ):
            with self.subTest(prompt=prompt):
                self.assertIsNone(lexical_intent(prompt))

    def test_dotted_code_symbol_is_not_a_command(self):
        self.assertIsNone(lexical_intent("fix.bug é o nome do arquivo a revisar."))

    def test_lexicon_does_not_expand_reverse_proxy_semantics(self):
        proxy = ModelProfile(backend="kitt-reverse-proxy", model="webchat")
        result = SemanticFilter(proxy, local_mode="auto").filter_and_plan(
            "Conserte o erro em UserService."
        )
        self.assertEqual(result.source, "LLM_FIRST")
        self.assertEqual(result.task.original_prompt, "Conserte o erro em UserService.")

    def test_import_real_format_and_preserve_unreviewed_omw(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = folder / "lexicon.db"
            unimorph = folder / "pt"
            # UniMorph: lemma, surface, semicolon-separated UD features.
            unimorph.write_text(
                "consertar\tconserte\tV;IMP;2;SG\n"
                "consertar\tconsertou\tV;IND;PST;3;SG\n"
                "corrigir\tcorrija\tV;IMP;2;SG\n", encoding="utf-8"
            )
            omw = folder / "omw"
            omw.write_text(
                "11111111-v\tlemma\tconsertar\n"
                "11111111-v\tlemma\treparar\n"
                "11111111-v\tlemma\tremediar\n", encoding="utf-8"
            )
            reviewed = folder / "reviewed.tsv"
            reviewed.write_text("pt\tajuste\tDEBUG\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "scripts/build_semantic_lexicon.py",
                 "--output", str(path), "--unimorph", f"pt={unimorph}",
                 "--omw", f"pt={omw}", "--approve", str(reviewed),
                 "--accept-upstream-licenses"],
                capture_output=True, text=True, check=False, timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            import sqlite3
            with sqlite3.connect(path) as connection:
                sources = connection.execute(
                    "SELECT origin, lang, sha256 FROM sources ORDER BY origin"
                ).fetchall()
                self.assertEqual(len(sources), 3)
                self.assertTrue(all(len(s[2]) == 64 for s in sources))
                suggestion = connection.execute(
                    "SELECT approved FROM entries WHERE form='remediar'"
                ).fetchone()
                self.assertEqual(suggestion, (0,))
                forbidden = connection.execute(
                    "SELECT count(*) FROM entries WHERE form='consertou' AND approved=1"
                ).fetchone()
                self.assertEqual(forbidden, (0,))
            with patch.dict(os.environ, {"KITT_SEMANTIC_LEXICON_DB": str(path)}):
                self.assertEqual(lexical_intent("Conserte a falha em UserService."), "DEBUG")
                self.assertEqual(lexical_intent("Ajuste a falha em UserService."), "DEBUG")
                self.assertIsNone(lexical_intent("Remediar a falha em UserService."))
                self.assertIsNone(lexical_intent("Consertou a falha ontem."))
                self.assertIsNone(lexical_intent("Ajuste o código, sem alterar arquivos."))

    def test_optional_corrupted_index_is_not_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.db"
            path.write_text("invalid sqlite", encoding="utf-8")
            with patch.dict(os.environ, {"KITT_SEMANTIC_LEXICON_DB": str(path)}):
                self.assertIsNone(lexical_intent("Ajuste a falha em UserService."))
                self.assertEqual(lexical_intent("Conserte a falha em UserService."), "DEBUG")

    def test_import_must_acknowledge_licenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pt"
            path.write_text("corrigir\tcorrija\tV;IMP;2;SG\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "scripts/build_semantic_lexicon.py",
                 "--output", str(Path(tmp) / "index.db"), "--unimorph", f"pt={path}"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("licenses", proc.stderr)


if __name__ == "__main__":
    unittest.main()
