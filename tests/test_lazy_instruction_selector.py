from __future__ import annotations

from pathlib import Path

from kitt.context_engine.agents_reader import HierarchicalAgentsReader
from kitt.context_filter.instruction_selector import LazyInstructionSelector


def test_always_on_rule_is_included_without_target(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# Root\nroot rules\n", encoding="utf-8")
    rules = tmp_path / ".agents" / "rules"
    rules.mkdir(parents=True)
    (rules / "always.md").write_text(
        "---\ntrigger: always_on\n---\n# Always\nquality gate\n",
        encoding="utf-8",
    )
    text = HierarchicalAgentsReader(tmp_path).get_merged_agents_rules()
    assert "AGENTS.md" in text
    assert ".agents/rules/always.md" in text
    assert "quality gate" in text


def test_glob_rule_is_lazy_for_matching_target(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "App.java").write_text("class App {}\n", encoding="utf-8")
    rules = tmp_path / ".agents" / "rules"
    rules.mkdir(parents=True)
    (rules / "java.md").write_text(
        "---\nglobs: [src/*.java]\n---\n# Java\njava-only rule\n",
        encoding="utf-8",
    )
    (rules / "python.md").write_text(
        "---\nglobs: [src/*.py]\n---\n# Python\npython-only rule\n",
        encoding="utf-8",
    )
    text = HierarchicalAgentsReader(tmp_path).get_merged_agents_rules("src/App.java")
    assert "java-only rule" in text
    assert "python-only rule" not in text


def test_rule_dependencies_are_loaded_before_dependents(tmp_path: Path):
    rules = tmp_path / ".agents" / "rules"
    rules.mkdir(parents=True)
    (rules / "base.md").write_text("---\nname: base\n---\n# Base\nbase\n", encoding="utf-8")
    (rules / "java.md").write_text(
        "---\nname: java\ntrigger: always_on\ndepends_on: [base]\n---\n# Java\njava\n",
        encoding="utf-8",
    )
    text = HierarchicalAgentsReader(tmp_path).get_merged_agents_rules()
    assert text.index("base.md") < text.index("java.md")


def test_global_instruction_budget_bounds_pack(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# Security\n" + "secure rule\n" * 1000, encoding="utf-8")
    rules = tmp_path / ".agents" / "rules"
    rules.mkdir(parents=True)
    (rules / "always.md").write_text(
        "---\ntrigger: always_on\n---\n# Always\n" + "quality\n" * 1000,
        encoding="utf-8",
    )
    reader = HierarchicalAgentsReader(tmp_path, max_instruction_chars=3000)
    text = reader.get_merged_agents_rules()
    # Formatting adds small source headers outside the body budget.
    assert len(text) <= 3400
    assert "Security" in text


def test_selector_cuts_dependency_cycle_deterministically(tmp_path: Path):
    rules = tmp_path / ".agents" / "rules"
    rules.mkdir(parents=True)
    (rules / "a.md").write_text(
        "---\nname: a\ntrigger: always_on\ndepends_on: [b]\n---\n# A\na\n",
        encoding="utf-8",
    )
    (rules / "b.md").write_text(
        "---\nname: b\ndepends_on: [a]\n---\n# B\nb\n",
        encoding="utf-8",
    )
    selector = LazyInstructionSelector(tmp_path)
    selected = selector.select([], query="", target_path="")
    names = [item.name for item in selected]
    assert names == ["b", "a"]
