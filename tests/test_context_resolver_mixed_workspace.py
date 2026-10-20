"""Regression: legitimate polyglot AGENTS.md instructions are not discarded."""

from pathlib import Path

from kitt.context_filter.context_resolver import ContextResolver


def test_python_workspace_keeps_polyglot_agents_guidance(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='polyglot'\nversion='0.1'\n")
    (tmp_path / "AGENTS.md").write_text(
        "Python backend and Node Bun TypeScript frontend; follow all rules.\n"
    )
    resolver = ContextResolver(str(tmp_path))
    instructions = resolver.resolve_agents_instructions()
    assert len(instructions) == 1
    assert instructions[0].mandatory
    assert "TypeScript" in instructions[0].content
