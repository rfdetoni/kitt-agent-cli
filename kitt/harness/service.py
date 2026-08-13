from kitt.harness.repository import HarnessRepository
class HarnessService:
    def __init__(self,repository:HarnessRepository): self.repo=repository
    def prompt(self,workspace_id=None,conversation_id=None,max_chars=12000):
        blocks=[]; used=0
        for e in self.repo.active(workspace_id,conversation_id):
            block=f"[{e.scope}/{e.name}]\n{e.content}"
            if used+len(block)>max_chars:break
            blocks.append(block); used+=len(block)
        return "\n\n".join(blocks)
    def remember(self,name,content,workspace_id,conversation_id=None,evidence=None):
        return self.repo.add("KNOWLEDGE","WORKSPACE",name,content,"user",workspace_id,conversation_id,evidence)
