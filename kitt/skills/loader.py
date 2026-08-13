import re
class ProgressiveSkillLoader:
    def select(self,skills,prompt,max_skills=3):
        words=set(re.findall(r"[a-z0-9_+-]{3,}",prompt.lower()))
        scored=[]
        for skill in skills:
            hay=f"{skill.name} {skill.description}".lower()
            score=sum(w in hay for w in words)
            if score:scored.append((score,skill.name,skill))
        return [item[2] for item in sorted(scored,reverse=True)[:max_skills]]
    def load(self,skill,max_chars=12000):
        text=(skill.path/"SKILL.md").read_text("utf-8",errors="ignore")
        return text[:max_chars]
