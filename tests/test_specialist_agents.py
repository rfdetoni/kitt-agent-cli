"""Characterization tests for trusted declarative plugin agents."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitt.agents.helper import create_agent, edit_persona, handle_agents_command
from kitt.agents.registry import AgentRegistry
from kitt.extensions.manager import ExtensionManager


@pytest.fixture
def harness():
    with tempfile.TemporaryDirectory() as home:
        workspace = Path(home)
        ext = ExtensionManager(
            workspace_root=str(workspace),
            plugin_trust_path=str(workspace / "trust.json"),
            plugin_state_path=str(workspace / "state.json"),
        )
        yield workspace, ext, AgentRegistry(ext)


def test_default_personas_are_least_privileged(harness):
    _root, _ext, reg = harness
    profiles = {p.id: p for p in reg.list()}
    assert {"code", "creative", "investigator", "critic", "tester"} <= profiles.keys()
    assert profiles["critic"].read_only
    assert profiles["critic"].role == "REVIEW"
    assert not profiles["code"].read_only
    assert reg.select("review vulnerabilities").id == "critic"
    assert reg.select("implement a Java feature", require_write=True).role == "IMPLEMENT"
    with pytest.raises(ValueError):
        profiles["code"].spawn_args("implement feature")
    args = profiles["code"].spawn_args("implement feature", paths=("src/main.py",))
    assert args["agent_role"] == "IMPLEMENT"
    assert args["allowed_paths"] == ["src/main.py"]
    assert args["enabled_tools"] == list(profiles["code"].tools)


def test_custom_agent_requires_explicit_trust_and_retrust_after_edit(harness):
    root, ext, reg = harness
    path = create_agent(
        root, "java-specialist", role="code", tags=("spring", "java"),
        persona="Implement Java code with tests and evidence.",
    )
    plugin = "agent-java-specialist"
    assert all(p.plugin != plugin for p in reg.list())
    manifest = ext.plugins.discover()[plugin]
    assert not ext.plugin_trust.is_trusted(manifest)
    ext.plugin_trust.grant(manifest)
    profile = reg.find("agent-java-specialist:java-specialist")
    assert profile.role == "IMPLEMENT"
    assert profile.persona.startswith("Implement Java")
    edit_persona(root, "java-specialist", "New security-oriented Java persona.")
    assert all(p.plugin != plugin for p in reg.list())
    ext.plugin_trust.grant(manifest)
    assert "New security-oriented" in reg.find(profile.id).persona


def test_disable_enable_profiles_and_plugin_state(harness):
    root, ext, reg = harness
    reg.set_enabled("critic", False)
    assert "critic" not in {p.id for p in reg.list()}
    reg.set_enabled("critic", True)
    assert "critic" in {p.id for p in reg.list()}
    p = create_agent(root, "investigate-io")
    ext.plugin_trust.grant(ext.plugins.discover()["agent-investigate-io"])
    profile = reg.find("agent-investigate-io:investigate-io")
    ext.plugins.disable("agent-investigate-io")
    assert profile.id not in {x.id for x in reg.list()}


def test_manifest_rejects_unsafe_or_invalid_roles(harness):
    root, ext, reg = harness
    for value in ("../escape", "CAPS", "abc/def"):
        with pytest.raises(ValueError):
            create_agent(root, value)
    path = create_agent(root, "unsafe")
    manifest_path = path / "plugin.toml"
    contents = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(contents.replace(
        'read_only = true', 'read_only = false'
    ), encoding="utf-8")
    ext.plugin_trust.grant(ext.plugins.discover()["agent-unsafe"])
    assert all(p.plugin != "agent-unsafe" for p in reg.list())


def test_cli_list_and_select(harness, capsys):
    root, _ext, _reg = harness
    opts = SimpleNamespace(role="critic", persona=None, display_name=None, tags="")
    assert handle_agents_command("list", None, str(root), opts) == 0
    assert "Critic Reviewer" in capsys.readouterr().out
    assert handle_agents_command("select", "review security", str(root), opts) == 0
    assert "critic:" in capsys.readouterr().out


def test_no_override_or_implicit_plugin_trust(harness):
    root, ext, reg = harness
    create_agent(root, "debugger")
    with pytest.raises(FileExistsError):
        create_agent(root, "debugger")
    assert all(p.plugin != "agent-debugger" for p in reg.list())
    assert not ext.plugin_trust.is_trusted(ext.plugins.discover()["agent-debugger"])
