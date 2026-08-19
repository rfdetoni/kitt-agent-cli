import os
import re
import json
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import List, Dict, Set, Optional
from dataclasses import dataclass
from kitt.cli.ui import prompt_dropdown

ANSI_REGEX = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def sanitize_prompt_text(text: str) -> str:
    return ANSI_REGEX.sub('', text)

@dataclass
class SkillMetadata:
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "Unknown"
    path: Path = Path(".")
    skill_md_content: str = ""

DEFAULT_SKILLS = {
    "caveman": {
        "description": "Concise, token-compressed terse communication style",
        "content": """---
name: caveman
description: Concise, token-compressed terse communication style
version: 1.0.0
author: K.I.T.T. Core
---

# Mandatory Caveman Protocol
Respond tersely. Technical accuracy 100%. Drop fluff, filler words, articles.
Pattern: [thing] [action] [reason].
"""
    },
    "ponytail": {
        "description": "Lazy minimalist code rung ladder (YAGNI, standard library first)",
        "content": """---
name: ponytail
description: Lazy minimalist code rung ladder (YAGNI, standard library first)
version: 1.0.0
author: K.I.T.T. Core
---

# Mandatory Ponytail Rung Ladder
Write minimal, safe, high-quality code. Evaluate:
1. YAGNI - skip unneeded work.
2. Reuse existing codebase patterns.
3. Use standard library over dependencies.
4. Short safe diffs over complex abstractions.
"""
    },
    "rtk": {
        "description": "Rust Token Killer CLI output optimization proxy rules (when available in PATH)",
        "content": """---
name: rtk
description: Rust Token Killer CLI output optimization proxy rules (conditional)
version: 1.0.0
author: K.I.T.T. Core
---

# RTK Protocol (Conditional Execution)
- Use `rtk` proxy wrapper (`rtk <command>`) ONLY when the `rtk` binary is present and available in the system PATH.
- If `rtk` is NOT present or fails with 'command not found', execute commands directly without `rtk` to avoid errors.
"""
    }
}

class SkillManager:
    """Manages Agent Skills with Mandatory Always-On Checkbox Configuration."""

    def __init__(self, root_dir: str = ".", persistence_enabled: bool = True):
        self.root_dir = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.project_skills_dir = self.root_dir / ".kitt" / "skills"
        self.global_skills_dir = Path.home() / ".kitt" / "skills"
        self.active_skills_file = self.root_dir / ".kitt" / "active_skills.json"

        if persistence_enabled:
            self._ensure_dirs()
            self._ensure_default_skills()

    def _ensure_dirs(self):
        self.project_skills_dir.mkdir(parents=True, exist_ok=True)
        self.global_skills_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_default_skills(self):
        for name, info in DEFAULT_SKILLS.items():
            skill_dir = self.project_skills_dir / name
            if not skill_dir.exists():
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(info["content"], encoding='utf-8')

        if not self.active_skills_file.exists():
            self.set_active_skills(["caveman", "ponytail", "rtk"])

    def get_active_skills(self) -> List[str]:
        if not self.persistence_enabled or not self.active_skills_file.exists():
            return ["caveman", "ponytail", "rtk"]
        try:
            return json.loads(self.active_skills_file.read_text(encoding='utf-8'))
        except Exception:
            return ["caveman", "ponytail", "rtk"]

    def set_active_skills(self, active: List[str]):
        if not self.persistence_enabled:
            return
        self.active_skills_file.write_text(json.dumps(active, indent=2), encoding='utf-8')

    def _parse_yaml_frontmatter(self, content: str) -> Dict[str, str]:
        meta = {}
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            yaml_block = match.group(1)
            for line in yaml_block.splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    meta[key.strip().lower()] = val.strip().strip("\"'")
        return meta

    def _load_skill_from_dir(self, skill_dir: Path) -> Optional[SkillMetadata]:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        try:
            content = skill_md.read_text(encoding='utf-8', errors='ignore')
            frontmatter = self._parse_yaml_frontmatter(content)
            name = frontmatter.get("name", skill_dir.name)
            desc = frontmatter.get("description", "No description provided.")
            version = frontmatter.get("version", "1.0.0")
            author = frontmatter.get("author", "Unknown")

            return SkillMetadata(
                name=name,
                description=desc,
                version=version,
                author=author,
                path=skill_dir,
                skill_md_content=content
            )
        except Exception:
            return None

    def list_skills(self) -> List[SkillMetadata]:
        skills = []
        if self.project_skills_dir.exists():
            for child in self.project_skills_dir.iterdir():
                if child.is_dir():
                    s = self._load_skill_from_dir(child)
                    if s:
                        skills.append(s)

        if self.global_skills_dir.exists():
            for child in self.global_skills_dir.iterdir():
                if child.is_dir():
                    if not any(existing.name == child.name for existing in skills):
                        s = self._load_skill_from_dir(child)
                        if s:
                            skills.append(s)

        return skills

    def install_from_git(self, git_url: str, is_global: bool = False) -> SkillMetadata:
        if not self.persistence_enabled:
            raise RuntimeError("Skill installation requires persistence enabled.")
        if not git_url.startswith("http://") and not git_url.startswith("https://") and not git_url.startswith("git@"):
            git_url = f"https://github.com/{git_url}.git"

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            res = subprocess.run(
                ["git", "clone", "--depth", "1", git_url, str(tmp_path)],
                capture_output=True,
                text=True
            )
            if res.returncode != 0:
                raise RuntimeError(f"Git clone failed: {res.stderr.strip()}")

            skill_md = tmp_path / "SKILL.md"
            if not skill_md.exists():
                raise RuntimeError("Invalid skill repository: missing SKILL.md specification file.")

            content = skill_md.read_text(encoding='utf-8', errors='ignore')
            frontmatter = self._parse_yaml_frontmatter(content)
            skill_name = frontmatter.get("name", tmp_path.name).lower().replace(" ", "-")

            target_base = self.global_skills_dir if is_global else self.project_skills_dir
            target_dir = target_base / skill_name

            if target_dir.exists():
                shutil.rmtree(target_dir)

            shutil.copytree(tmp_path, target_dir, ignore=shutil.ignore_patterns(".git"))

            s = self._load_skill_from_dir(target_dir)
            if not s:
                raise RuntimeError("Failed to load skill after installation.")
            
            # Auto-enable newly installed skill
            active = self.get_active_skills()
            if s.name not in active:
                active.append(s.name)
                self.set_active_skills(active)

            return s

    def remove_skill(self, skill_name: str) -> bool:
        if not self.persistence_enabled:
            return False
        removed = False
        p_path = self.project_skills_dir / skill_name
        if p_path.exists():
            shutil.rmtree(p_path)
            removed = True

        g_path = self.global_skills_dir / skill_name
        if g_path.exists():
            shutil.rmtree(g_path)
            removed = True

        active = self.get_active_skills()
        if skill_name in active:
            active.remove(skill_name)
            self.set_active_skills(active)

        return removed

    def execute_skill(self, skill_name: str, arguments: dict, runtime=None, security_context=None):
        if not getattr(self, "executable_enabled", True):
            raise PermissionError("Executable skills are disabled by runtime configuration")
        from kitt.skills.executable import ExecutableSkillRunner
        runner = ExecutableSkillRunner(runtime)
        return runner.execute(skill_name, arguments, security_context=security_context)

    def get_skills_summary_prompt(self) -> str:
        skills = self.list_skills()
        active = set(self.get_active_skills())
        if not skills:
            return ""

        mandatory_blocks = []
        summary_lines = ["Installed Agent Skills:"]
        for s in skills:
            status = "[\033[32mx\033[0m] ACTIVE" if s.name in active else "[ ] INACTIVE"
            summary_lines.append(f"- **{s.name}** {status} (v{s.version}): {s.description}")

            if s.name in active:
                mandatory_blocks.append(f"--- Mandatory Skill: {s.name} ---\n{s.skill_md_content}")

        skills_prompt = "\n".join(summary_lines)
        if mandatory_blocks:
            skills_prompt += "\n\n" + "\n\n".join(mandatory_blocks)

        return sanitize_prompt_text(skills_prompt)

    def run_interactive_checkbox_config(self):
        skills = self.list_skills()
        if not skills:
            print("\033[33mNo skills found to configure.\033[0m")
            return

        active_set = set(self.get_active_skills())

        print("\n\033[1;36m=== K.I.T.T. Mandatory Skills Configurator ===\033[0m")
        print("\033[90mSelect skills to be mandatory always-on for LLM prompts:\033[0m\n")

        for idx, s in enumerate(skills, start=1):
            checked = "[\033[32mx\033[0m]" if s.name in active_set else "[ ]"
            print(f"  [{idx}] {checked} \033[1;33m{s.name:<15}\033[0m \033[90m-\033[0m {s.description}")

        print("\nType skill numbers to toggle (e.g. '1 2' to toggle caveman & ponytail, or 'all'/'none'):")
        options = ["all", "none", "1", "1 2", "1 2 3"] + [str(i) for i in range(1, len(skills) + 1)]
        userInput = prompt_dropdown("\033[1;31mkitt-config\033[1;32m>\033[0m ", options).lower()

        if not userInput:
            print("\033[90mNo changes made.\033[0m")
            return

        if userInput == "all":
            active_set = {s.name for s in skills}
        elif userInput == "none":
            active_set = set()
        else:
            for part in userInput.split():
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(skills):
                        s_name = skills[idx].name
                        if s_name in active_set:
                            active_set.remove(s_name)
                        else:
                            active_set.add(s_name)
                except ValueError:
                    pass

        new_active = list(active_set)
        self.set_active_skills(new_active)

        print("\n\033[1;32m✓ Active Mandatory Skills Updated!\033[0m")
        for s in skills:
            chk = "[\033[32mx\033[0m] ACTIVE" if s.name in active_set else "[ ] INACTIVE"
            print(f"  {chk:<15} \033[1;33m{s.name}\033[0m")
        print()
