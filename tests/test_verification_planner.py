from __future__ import annotations

import json
import tempfile
from pathlib import Path

from kitt.tools.build_detector import BuildDetector


def _names(detector, paths):
    return [step.name for step in detector.plan_verification(paths, full=True)]


def test_maven_java_gets_compile_and_tests():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pom.xml").write_text("<project/>", encoding="utf-8")
        names = _names(BuildDetector(tmp), ["src/main/java/App.java"])
        assert "java.compile" in names
        assert "java.tests" in names


def test_go_gets_vet_and_tests():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "go.mod").write_text("module example", encoding="utf-8")
        names = _names(BuildDetector(tmp), ["main.go"])
        assert names == ["go.vet", "go.tests"]


def test_node_uses_existing_scripts_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "typecheck": "tsc --noEmit",
                        "lint": "eslint .",
                        "test": "vitest",
                    }
                }
            ),
            encoding="utf-8",
        )
        names = _names(BuildDetector(tmp), ["src/app.ts"])
        assert names == ["node.typecheck", "node.lint", "node.test"]


def test_fast_mode_does_not_run_project_wide_java_build():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pom.xml").write_text("<project/>", encoding="utf-8")
        steps = BuildDetector(tmp).plan_verification(
            ["src/main/java/App.java"],
            full=False,
        )
        assert steps == []
