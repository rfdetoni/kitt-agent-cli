from kitt.harness.repository import HarnessRepository
from kitt.harness.evolution import HarnessEvolutionService


class HarnessService:
    def __init__(self,repository:HarnessRepository):
        self.repo=repository
        self.evolution=HarnessEvolutionService(repository.db, repository)
    def prompt(self,workspace_id=None,conversation_id=None,max_chars=12000):
        blocks=[]; used=0
        for e in self.repo.active(workspace_id,conversation_id):
            block=f"[{e.scope}/{e.name}]\n{e.content}"
            if used+len(block)>max_chars:break
            blocks.append(block); used+=len(block)
        return "\n\n".join(blocks)
    def remember(self,name,content,workspace_id,conversation_id=None,evidence=None):
        return self.repo.add("KNOWLEDGE","WORKSPACE",name,content,"user",workspace_id,conversation_id,evidence)


    def capture_snapshot(self, workspace_id, conversation_id=None, runtime_facts=None):
        return self.evolution.capture_snapshot(
            workspace_id,
            conversation_id,
            runtime_facts=runtime_facts,
        )

    def record_materialization(self, snapshot_id, **kwargs):
        return self.evolution.record_materialization(snapshot_id, **kwargs)

    def record_materializations(self, snapshot_id, receipts):
        return self.evolution.record_materializations(snapshot_id, receipts)

    def create_intervention(self, workspace_id, **kwargs):
        return self.evolution.create_intervention(workspace_id, **kwargs)

    def record_intervention_result(self, intervention_id, **kwargs):
        return self.evolution.record_comparison(intervention_id, **kwargs)

    def learning_candidates(self, workspace_id, min_occurrences=2, limit=20):
        return self.evolution.learning_candidates(
            workspace_id,
            min_occurrences=min_occurrences,
            limit=limit,
        )
