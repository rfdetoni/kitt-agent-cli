from types import SimpleNamespace

from kitt.skills.loader import ProgressiveSkillLoader


def test_progressive_skill_loader_accepts_none_path():
    skill = SimpleNamespace(
        name="python-expert",
        description="Python debugging skill",
        path=None,
        active=True,
        trusted=True,
        always_apply=False,
        priority=0,
        globs=(),
        keywords=("python", "debug"),
        body_hint="debug python code",
        depends_on=(),
    )

    selected = ProgressiveSkillLoader().select(
        [skill], "Please debug my python code with python-expert"
    )

    assert len(selected) == 1
    assert selected[0].selected_content
