import re
from pathlib import Path
from typing import List
from kitt.skills.models import SkillDescriptor
def _frontmatter(text):
    match=re.match(r"^---\s*\n(.*?)\n---",text,re.S); out={}
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                k,v=line.split(":",1);out[k.strip().lower()]=v.strip().strip("'\"")
    return out
class SkillDiscovery:
    def discover(self,roots)->List[SkillDescriptor]:
        found={}
        for root in roots:
            root=Path(root)
            if not root.exists():continue
            for md in sorted(root.glob("*/SKILL.md")):
                meta=_frontmatter(md.read_text("utf-8",errors="ignore")); name=meta.get("name",md.parent.name)
                found.setdefault(name,SkillDescriptor(name,meta.get("description",""),meta.get("version","1.0.0"),meta.get("author","Unknown"),md.parent))
        return list(found.values())
