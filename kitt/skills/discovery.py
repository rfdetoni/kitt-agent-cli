import re
from pathlib import Path
from typing import List, Dict, Tuple, Any
from kitt.skills.models import SkillDescriptor


def _frontmatter(text: str) -> dict:
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    out = {}
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip().lower()] = v.strip().strip("'\"")
    return out


class SkillDiscovery:
    def discover(self, roots: List[Any]) -> List[SkillDescriptor]:
        found: Dict[str, SkillDescriptor] = {}
        for root in roots:
            root = Path(root)
            if not root.exists():
                continue
            # Search flat, nested skills, and plugins directories
            patterns = [
                "*/SKILL.md",
                "skills/*/SKILL.md",
                "*/skills/*/SKILL.md",
                "plugins/*/skills/*/SKILL.md",
            ]
            skill_files = []
            for pat in patterns:
                skill_files.extend(root.glob(pat))
            if not skill_files:
                skill_files = list(root.glob("**/SKILL.md"))

            for md in sorted(set(skill_files)):
                try:
                    text = md.read_text("utf-8", errors="ignore")
                    meta = _frontmatter(text)
                    name = meta.get("name", md.parent.name)
                    if name not in found:
                        found[name] = SkillDescriptor(
                            name=name,
                            description=meta.get("description", ""),
                            version=meta.get("version", "1.0.0"),
                            author=meta.get("author", "Unknown"),
                            path=md.parent,
                        )
                except Exception:
                    continue
        return list(found.values())

    def get_skill_completions(self, roots: List[Any]) -> List[Tuple[str, str]]:
        """Returns list of (slash_command, display_meta) for skills and subskills."""
        skills = self.discover(roots)
        completions: Dict[str, str] = {}

        for skill in skills:
            main_cmd = f"/{skill.name}"
            desc = skill.description[:60] if skill.description else "Skill"
            completions[main_cmd] = f"skill: {desc}"

            # Extract subskills / subcommands mentioned in SKILL.md
            try:
                skill_md = skill.path / "SKILL.md"
                if skill_md.exists():
                    content = skill_md.read_text("utf-8", errors="ignore")
                    # Find slash commands mentioned in triggers/instructions
                    matches = re.findall(r"/(?:[a-zA-Z0-9_\-]+(?::[a-zA-Z0-9_\-]+)?)", content)
                    for match in matches:
                        cmd = match.strip()
                        # Avoid matching URLs or file paths
                        if cmd.startswith("//") or "." in cmd or "/" in cmd[1:]:
                            continue
                        if cmd.lower().startswith(main_cmd.lower()) or skill.name.lower() in cmd.lower():
                            if cmd not in completions:
                                completions[cmd] = f"subskill: {skill.name}"

                    # Standard intensity subskills if supported in doc
                    if "intensity level" in content.lower() or "mode" in content.lower():
                        for mode in ["lite", "full", "ultra", "wenyan"]:
                            if mode in content.lower():
                                mode_cmd = f"{main_cmd}:{mode}"
                                if mode_cmd not in completions:
                                    completions[mode_cmd] = f"skill mode: {mode}"
            except Exception:
                continue

        # Sort alphabetically
        return sorted(completions.items(), key=lambda x: x[0])

