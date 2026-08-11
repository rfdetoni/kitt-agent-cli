import unittest
import tempfile
from pathlib import Path
from kitt.skills.skill_manager import SkillManager

class TestSkillManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.manager = SkillManager(root_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_create_and_list_skill(self):
        skill_dir = Path(self.tmp_dir.name) / ".kitt" / "skills" / "refactor-clean"
        skill_dir.mkdir(parents=True, exist_ok=True)

        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("""---
name: refactor-clean
description: Refactors codebase adhering to clean code principles
version: 2.0.0
author: K.I.T.T. Team
---

# Refactor Clean Skill
Apply clean code principles automatically.
""", encoding='utf-8')

        skills = self.manager.list_skills()
        skill_names = [s.name for s in skills]
        self.assertIn("refactor-clean", skill_names)

        target = [s for s in skills if s.name == "refactor-clean"][0]
        self.assertEqual(target.version, "2.0.0")
        self.assertEqual(target.author, "K.I.T.T. Team")

        prompt = self.manager.get_skills_summary_prompt()
        self.assertIn("refactor-clean", prompt)

    def test_remove_skill(self):
        skill_dir = Path(self.tmp_dir.name) / ".kitt" / "skills" / "temp-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: temp-skill\ndescription: temp\n---\n", encoding='utf-8')

        skill_names_before = [s.name for s in self.manager.list_skills()]
        self.assertIn("temp-skill", skill_names_before)

        removed = self.manager.remove_skill("temp-skill")
        self.assertTrue(removed)

        skill_names_after = [s.name for s in self.manager.list_skills()]
        self.assertNotIn("temp-skill", skill_names_after)

if __name__ == '__main__':
    unittest.main()
