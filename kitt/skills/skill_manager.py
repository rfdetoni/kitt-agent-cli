from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from kitt.cli.ui import prompt_dropdown
from kitt.security.private_state import (
    secure_read_json,
    secure_write_json,
    workspace_state_dir,
)

ANSI_REGEX = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_SKILL_MD = 256 * 1024
_MAX_SKILL_PY = 512 * 1024


def sanitize_prompt_text(text: str) -> str:
    return ANSI_REGEX.sub("", text)


@dataclass
class SkillMetadata:
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "Unknown"
    path: Path = Path(".")
    skill_md_content: str = ""
    source: str = "managed"
    trusted: bool = True


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
""",
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
""",
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
- If `rtk` is NOT present or fails with 'command not found', execute commands directly without `rtk`.
""",
    },
}


class SkillManager:
    """Skills manager separating repository content from user authorization."""

    def __init__(self, root_dir: str = ".", persistence_enabled: bool = True):
        self.root_dir = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.workspace_skills_dir = self.root_dir / ".kitt" / "skills"  # read-only/untrusted
        state = workspace_state_dir(self.root_dir, "skills")
        self.project_skills_dir = state / "installed"  # user-managed project scope
        self.global_skills_dir = Path.home() / ".kitt" / "skills"
        self.active_skills_file = state / "active_skills.json"
        self.trust_file = state / "workspace-skill-trust.json"
        if persistence_enabled:
            self._ensure_dirs()
            self._ensure_default_skills()

    @staticmethod
    def _validate_name(name: str) -> str:
        clean = str(name or "").strip().lower()
        if not _SAFE_NAME.fullmatch(clean):
            raise ValueError("Skill name must match [a-z0-9][a-z0-9._-]{0,63}")
        return clean

    def _ensure_dirs(self) -> None:
        self.project_skills_dir.mkdir(parents=True, exist_ok=True)
        self.global_skills_dir.mkdir(parents=True, exist_ok=True)

    def _ensure_default_skills(self) -> None:
        for name, info in DEFAULT_SKILLS.items():
            skill_dir = self.project_skills_dir / name
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                skill_md.write_text(info["content"], encoding="utf-8")
        if not self.active_skills_file.exists():
            secure_write_json(
                self.active_skills_file,
                ["caveman", "ponytail", "rtk"],
                max_bytes=64 * 1024,
            )

    @staticmethod
    def _regular_bounded_text(path: Path, limit: int) -> str:
        if path.is_symlink():
            raise PermissionError(f"Refusing symlink skill file: {path}")
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode):
            raise PermissionError(f"Skill file is not regular: {path}")
        if st.st_size > limit:
            raise ValueError(f"Skill file exceeds {limit} bytes: {path}")
        return path.read_text(encoding="utf-8", errors="replace")

    def _workspace_digest(self, skill_dir: Path) -> str:
        digest = hashlib.sha256()
        for name, limit in (("SKILL.md", _MAX_SKILL_MD), ("skill.py", _MAX_SKILL_PY)):
            path = skill_dir / name
            if not path.exists():
                continue
            content = self._regular_bounded_text(path, limit).encode("utf-8")
            digest.update(name.encode("utf-8") + b"\0" + content + b"\0")
        return digest.hexdigest()

    def _trust_map(self) -> dict[str, str]:
        data = secure_read_json(self.trust_file, default={}, max_bytes=256 * 1024)
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def trust_workspace_skill(self, skill_name: str) -> str:
        name = self._validate_name(skill_name)
        skill_dir = self.workspace_skills_dir / name
        if skill_dir.is_symlink() or not skill_dir.is_dir():
            raise FileNotFoundError(f"Workspace skill not found: {name}")
        digest = self._workspace_digest(skill_dir)
        trust = self._trust_map()
        trust[name] = digest
        secure_write_json(self.trust_file, trust, max_bytes=256 * 1024)
        return digest

    def is_workspace_skill_trusted(self, skill_name: str) -> bool:
        try:
            name = self._validate_name(skill_name)
            skill_dir = self.workspace_skills_dir / name
            if skill_dir.is_symlink() or not skill_dir.is_dir():
                return False
            return self._trust_map().get(name) == self._workspace_digest(skill_dir)
        except Exception:
            return False

    def get_active_skills(self) -> List[str]:
        if not self.persistence_enabled:
            return ["caveman", "ponytail", "rtk"]
        value = secure_read_json(
            self.active_skills_file,
            default=["caveman", "ponytail", "rtk"],
            max_bytes=64 * 1024,
        )
        if not isinstance(value, list):
            return ["caveman", "ponytail", "rtk"]
        result = []
        for item in value[:256]:
            try:
                name = self._validate_name(item)
            except Exception:
                continue
            if name not in result:
                result.append(name)
        return result

    def set_active_skills(self, active: List[str], *, trust_workspace: bool = False):
        if not self.persistence_enabled:
            return
        clean = []
        for raw in active[:256]:
            name = self._validate_name(raw)
            workspace_dir = self.workspace_skills_dir / name
            if workspace_dir.is_dir() and not self.is_workspace_skill_trusted(name):
                if trust_workspace:
                    self.trust_workspace_skill(name)
                else:
                    raise PermissionError(
                        f"Workspace skill '{name}' is untrusted; explicit trust is required"
                    )
            if name not in clean:
                clean.append(name)
        secure_write_json(self.active_skills_file, clean, max_bytes=64 * 1024)

    @staticmethod
    def _parse_yaml_frontmatter(content: str) -> Dict[str, str]:
        meta: Dict[str, str] = {}
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if match:
            for line in match.group(1).splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip().strip("\"'")
        return meta

    def _load_skill_from_dir(self, skill_dir: Path, source: str) -> Optional[SkillMetadata]:
        if skill_dir.is_symlink() or not skill_dir.is_dir():
            return None
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None
        try:
            content = self._regular_bounded_text(skill_md, _MAX_SKILL_MD)
            front = self._parse_yaml_frontmatter(content)
            name = self._validate_name(front.get("name", skill_dir.name))
            trusted = source != "workspace" or self.is_workspace_skill_trusted(skill_dir.name)
            return SkillMetadata(
                name=name,
                description=front.get("description", "No description provided.")[:1024],
                version=front.get("version", "1.0.0")[:128],
                author=front.get("author", "Unknown")[:256],
                path=skill_dir,
                skill_md_content=content,
                source=source,
                trusted=trusted,
            )
        except Exception:
            return None

    def list_skills(self) -> List[SkillMetadata]:
        result: List[SkillMetadata] = []
        seen: set[str] = set()
        for base, source in (
            (self.project_skills_dir, "managed"),
            (self.global_skills_dir, "global"),
            (self.workspace_skills_dir, "workspace"),
        ):
            if base.is_symlink() or not base.exists():
                continue
            for child in sorted(base.iterdir()):
                skill = self._load_skill_from_dir(child, source)
                if not skill or skill.name in seen:
                    continue
                seen.add(skill.name)
                result.append(skill)
        return result

    def install_from_git(self, git_url: str, is_global: bool = False) -> SkillMetadata:
        if not self.persistence_enabled:
            raise RuntimeError("Skill installation requires persistence enabled")
        if not git_url.startswith(("http://", "https://", "git@")):
            git_url = f"https://github.com/{git_url}.git"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir) / "repo"
            result = subprocess.run(
                ["git", "clone", "--depth", "1", "--", git_url, str(tmp)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Git clone failed: {result.stderr.strip()[:2000]}")
            skill_md = tmp / "SKILL.md"
            if skill_md.is_symlink() or not skill_md.is_file():
                raise RuntimeError("Invalid skill repository: missing regular SKILL.md")
            content = self._regular_bounded_text(skill_md, _MAX_SKILL_MD)
            front = self._parse_yaml_frontmatter(content)
            skill_name = self._validate_name(front.get("name", "skill"))
            target_base = self.global_skills_dir if is_global else self.project_skills_dir
            target = target_base / skill_name
            if target.exists():
                if target.is_symlink():
                    raise PermissionError(f"Refusing symlink skill target: {target}")
                shutil.rmtree(target)
            shutil.copytree(
                tmp,
                target,
                symlinks=False,
                ignore=shutil.ignore_patterns(".git"),
            )
            skill = self._load_skill_from_dir(target, "global" if is_global else "managed")
            if not skill:
                raise RuntimeError("Failed to load skill after installation")
            active = self.get_active_skills()
            if skill.name not in active:
                active.append(skill.name)
                self.set_active_skills(active)
            return skill

    def remove_skill(self, skill_name: str) -> bool:
        if not self.persistence_enabled:
            return False
        name = self._validate_name(skill_name)
        removed = False
        for base in (self.project_skills_dir, self.global_skills_dir):
            target = base / name
            if target.is_symlink():
                raise PermissionError(f"Refusing symlink skill removal: {target}")
            if target.exists():
                shutil.rmtree(target)
                removed = True
        active = [item for item in self.get_active_skills() if item != name]
        self.set_active_skills(active)
        return removed

    def execute_skill(self, skill_name: str, arguments: dict, runtime=None, security_context=None):
        if not getattr(self, "executable_enabled", True):
            raise PermissionError("Executable skills are disabled by runtime configuration")
        name = self._validate_name(skill_name)
        workspace = self.workspace_skills_dir / name
        if workspace.is_dir() and not self.is_workspace_skill_trusted(name):
            raise PermissionError(f"Executable workspace skill '{name}' is not trusted")
        if security_context is None:
            raise PermissionError("Executable skill requires an inherited security context")
        from kitt.skills.executable import ExecutableSkillRunner
        runner = ExecutableSkillRunner(runtime)
        return runner.execute(name, arguments, security_context=security_context)

    def get_skills_summary_prompt(self) -> str:
        skills = self.list_skills()
        active = set(self.get_active_skills())
        if not skills:
            return ""
        summary = ["Installed Agent Skills:"]
        mandatory = []
        for skill in skills:
            enabled = skill.name in active
            trust_label = "trusted" if skill.trusted else "UNTRUSTED"
            summary.append(
                f"- **{skill.name}** {'ACTIVE' if enabled else 'INACTIVE'} "
                f"[{skill.source}/{trust_label}] (v{skill.version}): {skill.description}"
            )
            if enabled and skill.trusted:
                mandatory.append(
                    f"--- Mandatory Skill: {skill.name} ---\n{skill.skill_md_content}"
                )
        if mandatory:
            summary.extend(["", *mandatory])
        return sanitize_prompt_text("\n".join(summary))

    def run_interactive_checkbox_config(self):
        skills = self.list_skills()
        if not skills:
            print("No skills found to configure.")
            return
        active = set(self.get_active_skills())
        for idx, skill in enumerate(skills, 1):
            marker = "x" if skill.name in active else " "
            trust = "" if skill.trusted else " [UNTRUSTED]"
            print(f"[{idx}] [{marker}] {skill.name}{trust} - {skill.description}")
        options = ["all", "none"] + [str(i) for i in range(1, len(skills) + 1)]
        user_input = prompt_dropdown("kitt-config> ", options).lower()
        if not user_input:
            return
        if user_input == "all":
            active = {skill.name for skill in skills}
        elif user_input == "none":
            active = set()
        else:
            for part in user_input.split():
                try:
                    skill = skills[int(part) - 1]
                except (ValueError, IndexError):
                    continue
                if skill.name in active:
                    active.remove(skill.name)
                else:
                    active.add(skill.name)
        # Interactive selection is the explicit user trust ceremony.
        self.set_active_skills(sorted(active), trust_workspace=True)
