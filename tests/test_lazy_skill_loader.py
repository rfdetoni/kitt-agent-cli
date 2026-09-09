from __future__ import annotations

from pathlib import Path

from kitt.skills.loader import ProgressiveSkillLoader
from kitt.skills.models import SkillDescriptor


def skill(
    tmp_path: Path,
    name: str,
    body: str,
    *,
    description: str = "",
    active: bool = True,
    trusted: bool = True,
    always_apply: bool = False,
    priority: int = 0,
    globs: tuple[str, ...] = (),
    depends_on: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
) -> SkillDescriptor:
    root = tmp_path / name
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    return SkillDescriptor(
        name=name,
        description=description,
        version="1",
        author="test",
        path=root,
        active=active,
        trusted=trusted,
        always_apply=always_apply,
        priority=priority,
        globs=globs,
        depends_on=depends_on,
        keywords=keywords,
        body_hint=body[:512],
    )


def test_lazy_selector_ignores_inactive_and_untrusted(tmp_path: Path):
    loader = ProgressiveSkillLoader(max_total_chars=4000)
    skills = [
        skill(tmp_path, "java", "# Java\nSpring Boot", description="Java Spring", active=False),
        skill(tmp_path, "secret", "# Secret\nSpring", description="Spring", trusted=False),
        skill(tmp_path, "safe", "# Safe\nSpring", description="Spring"),
    ]
    selected = loader.select(skills, "improve Spring service", max_skills=3)
    assert [item.name for item in selected] == ["safe"]


def test_always_apply_and_dependency_order_are_deterministic(tmp_path: Path):
    loader = ProgressiveSkillLoader(max_total_chars=5000)
    base = skill(tmp_path, "base", "# Base\nRules", always_apply=True)
    java = skill(
        tmp_path,
        "java",
        "# Java\nSpring Boot repository rules",
        description="Spring Java",
        depends_on=("base",),
    )
    selected = loader.select([java, base], "fix Spring repository", max_skills=2)
    assert [item.name for item in selected] == ["base", "java"]


def test_glob_and_keyword_routing_select_relevant_skill(tmp_path: Path):
    loader = ProgressiveSkillLoader(max_total_chars=4000)
    java = skill(
        tmp_path,
        "java",
        "# Java\nUse records carefully",
        globs=("src/**/*.java",),
        keywords=("spring", "hibernate"),
    )
    web = skill(tmp_path, "web", "# Web\nReact", keywords=("react",))
    selected = loader.select(
        [web, java],
        "fix hibernate mapping in src/main/java/App.java",
        max_skills=1,
    )
    assert [item.name for item in selected] == ["java"]


def test_materialized_skills_share_one_global_budget(tmp_path: Path):
    loader = ProgressiveSkillLoader(max_total_chars=3000, min_skill_chars=500)
    a = skill(tmp_path, "alpha", "# Alpha\n" + "alpha details\n" * 500, always_apply=True)
    b = skill(tmp_path, "beta", "# Beta\n" + "beta details\n" * 500, keywords=("beta",))
    selected = loader.select([a, b], "beta task", max_skills=2)
    total = sum(len(item.selected_content) for item in selected)
    assert total <= 3000
    assert all(item.selected_content for item in selected)
    assert "lazy_excerpt: true" in selected[0].selected_content


def test_excerpt_keeps_relevant_section_instead_of_full_skill(tmp_path: Path):
    loader = ProgressiveSkillLoader(max_total_chars=1400, min_skill_chars=1000)
    body = (
        "# Unrelated\n" + "noise\n" * 400 +
        "# Database Migration\nAlways validate foreign keys and rollback paths.\n" +
        "# Other\n" + "more noise\n" * 400
    )
    db = skill(tmp_path, "database", body, description="Database migration", keywords=("migration",))
    selected = loader.select([db], "review database migration rollback", max_skills=1)
    assert selected
    excerpt = selected[0].selected_content
    assert len(excerpt) <= 1400
    assert "Database Migration" in excerpt
    assert len(excerpt) < len((db.path / "SKILL.md").read_text(encoding="utf-8"))
