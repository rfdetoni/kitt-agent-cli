from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


def test_catalog_preserves_explicit_empty_specs():
    assert IntegrationCatalog([]).specs == ()


def test_catalog_probe_uses_command_or_python_module(monkeypatch):
    spec = IntegrationSpec(
        "demo", "test", "demo", commands=("demo-bin",), python_modules=("json",)
    )
    monkeypatch.setattr("shutil.which", lambda _: None)
    status = IntegrationCatalog([spec]).probe("demo")
    assert status.available is True
    assert status.module == "json"


def test_catalog_version_probe_uses_bounded_runner(monkeypatch):
    captured = {}

    class FakeRunner:
        def __init__(self, root_dir: str, max_output_bytes: int):
            captured["root"] = root_dir
            captured["max_output_bytes"] = max_output_bytes

        def run(self, argv, timeout_seconds):
            captured["argv"] = argv
            captured["timeout_seconds"] = timeout_seconds
            return SimpleNamespace(
                stdout="demo 1.2.3\n",
                stderr="",
                timed_out=False,
                cancelled=False,
            )

    monkeypatch.setattr("kitt.integrations.catalog.shutil.which", lambda _: "/usr/bin/demo")
    monkeypatch.setattr("kitt.integrations.catalog.ProcessRunner", FakeRunner)

    assert IntegrationCatalog._version("demo") == "demo 1.2.3"
    assert captured["max_output_bytes"] == 16 * 1024
    assert captured["timeout_seconds"] == 2
    assert captured["argv"] == ["/usr/bin/demo", "--version"]


def test_ast_grep_rejects_workspace_escape(tmp_path: Path):
    adapter = AstGrepAdapter(tmp_path)
    with pytest.raises(PermissionError):
        adapter._safe_path("../outside.py")


def test_ast_grep_uses_bounded_process_runner(monkeypatch, tmp_path: Path):
    captured = {}

    class FakeRunner:
        def __init__(self, root_dir: str, max_output_bytes: int):
            captured["root"] = root_dir
            captured["max_output_bytes"] = max_output_bytes

        def run(self, argv, timeout_seconds, env):
            captured["argv"] = argv
            captured["timeout_seconds"] = timeout_seconds
            captured["env"] = env
            return SimpleNamespace(
                stdout="[]",
                stderr="",
                returncode=0,
                timed_out=False,
                cancelled=False,
                truncated=False,
                stdout_total_bytes=2,
            )

    monkeypatch.setattr("kitt.integrations.ast_grep.ProcessRunner", FakeRunner)
    adapter = AstGrepAdapter(tmp_path)
    adapter.executable = "ast-grep"

    result = adapter.search("$A", max_output_bytes=8192, timeout_seconds=1.1)

    assert result["matches"] == []
    assert captured["root"] == str(tmp_path.resolve())
    assert captured["max_output_bytes"] == 8192
    assert captured["timeout_seconds"] == 2
    assert captured["env"] == {"NO_COLOR": "1", "CLICOLOR": "0"}


def test_ast_grep_rejects_truncated_json_capture(monkeypatch, tmp_path: Path):
    class FakeRunner:
        def __init__(self, root_dir: str, max_output_bytes: int):
            pass

        def run(self, argv, timeout_seconds, env):
            return SimpleNamespace(
                stdout='[{"file":"partial"}',
                stderr="",
                returncode=0,
                timed_out=False,
                cancelled=False,
                truncated=True,
                stdout_total_bytes=100_000,
            )

    monkeypatch.setattr("kitt.integrations.ast_grep.ProcessRunner", FakeRunner)
    adapter = AstGrepAdapter(tmp_path)
    adapter.executable = "ast-grep"

    with pytest.raises(RuntimeError, match="capture limit"):
        adapter.search("$A", max_output_bytes=4096)


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


def test_semgrep_uses_bounded_process_runner(monkeypatch, tmp_path: Path):
    config = tmp_path / ".semgrep.yml"
    config.write_text("rules: []\n", encoding="utf-8")
    captured = {}

    class FakeRunner:
        def __init__(self, root_dir: str, max_output_bytes: int):
            captured["root"] = root_dir
            captured["max_output_bytes"] = max_output_bytes

        def run(self, argv, timeout_seconds, env):
            captured["argv"] = argv
            captured["timeout_seconds"] = timeout_seconds
            captured["env"] = env
            stdout = '{"results":[],"errors":[]}'
            return SimpleNamespace(
                stdout=stdout,
                stderr="",
                returncode=0,
                timed_out=False,
                cancelled=False,
                truncated=False,
                stdout_total_bytes=len(stdout.encode("utf-8")),
            )

    monkeypatch.setattr("kitt.integrations.semgrep.ProcessRunner", FakeRunner)
    adapter = SemgrepAdapter(tmp_path)
    adapter.executable = "semgrep"

    result = adapter.scan(max_output_bytes=128 * 1024, timeout_seconds=2.2)

    assert result["findings"] == []
    assert captured["root"] == str(tmp_path.resolve())
    assert captured["max_output_bytes"] == 160 * 1024
    assert captured["timeout_seconds"] == 3
    assert captured["env"] == {
        "SEMGREP_SEND_METRICS": "off",
        "NO_COLOR": "1",
        "CLICOLOR": "0",
    }
