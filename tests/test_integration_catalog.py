from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kitt.integrations.ast_grep import AstGrepAdapter
from kitt.integrations.catalog import IntegrationCatalog, IntegrationSpec
from kitt.integrations.semgrep import SemgrepAdapter


def test_catalog_indexes_optional_capabilities_without_importing_dependencies():
    catalog = IntegrationCatalog()
    index = catalog.capability_index()
    assert "repo.ast_search" in index
    assert "security.scan" in index
    assert "child.external" in index
    assert "eval.redteam" in index
    assert "telemetry.otlp" in index


def test_catalog_probe_uses_command_or_python_module(monkeypatch):
    spec = IntegrationSpec(
        "demo", "test", "demo", commands=("demo-bin",), python_modules=("json",)
    )
    monkeypatch.setattr("shutil.which", lambda _: None)
    status = IntegrationCatalog([spec]).probe("demo")
    assert status.available is True
    assert status.module == "json"


def test_ast_grep_rejects_workspace_escape(tmp_path: Path):
    adapter = AstGrepAdapter(tmp_path)
    with pytest.raises(PermissionError):
        adapter._safe_path("../outside.py")


def test_semgrep_requires_local_config_and_rejects_escape(tmp_path: Path):
    adapter = SemgrepAdapter(tmp_path)
    with pytest.raises(FileNotFoundError):
        adapter._config(None)
    with pytest.raises(PermissionError):
        adapter._safe_path("../outside.py")


def test_semgrep_accepts_workspace_local_config(tmp_path: Path):
    config = tmp_path / ".semgrep.yml"
    config.write_text("rules: []\n", encoding="utf-8")
    adapter = SemgrepAdapter(tmp_path)
    assert adapter._config(None) == config
