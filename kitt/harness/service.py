from kitt.evidence.efficiency import EpisodeEfficiencyService
from kitt.harness.components import HarnessComponentSnapshotService
from kitt.harness.experiments import HarnessExperimentService
from kitt.harness.learning import LearningCaptureService
from kitt.harness.presets import HarnessPresetService
from kitt.harness.repository import HarnessRepository
from kitt.harness.evolution import HarnessEvolutionService


class HarnessService:
    def __init__(self,repository:HarnessRepository):
        self.repo=repository
        self.evolution=HarnessEvolutionService(repository.db, repository)
        self.presets=HarnessPresetService(repository.db)
        self.components=HarnessComponentSnapshotService(repository.db)
        self.experiments=HarnessExperimentService(repository.db)
        self.learning=LearningCaptureService(repository.db)
        self.efficiency=EpisodeEfficiencyService(repository.db)

    def attach_coordinator(self, coordinator):
        self.experiments.attach_coordinator(coordinator)
        return self
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


    def ensure_preset(self, workspace_id, name, payload, activate=False):
        return self.presets.ensure_revision(
            workspace_id,
            name,
            payload,
            activate=activate,
        )

    def active_preset(self, workspace_id):
        return self.presets.active(workspace_id)

    def capture_components(self, workspace_id, components):
        return self.components.capture(workspace_id, components)

    def diff_components(self, before_id, after_id):
        return self.components.diff(before_id, after_id)

    def create_experiment(self, workspace_id, **kwargs):
        return self.experiments.create(workspace_id, **kwargs)

    def run_experiment(self, experiment_id, evaluator):
        return self.experiments.run(experiment_id, evaluator)

    def experiment(self, experiment_id):
        return self.experiments.get(experiment_id)

    def learning_capture(self, workspace_id, min_occurrences=2, limit=20):
        return self.learning.candidates(
            workspace_id,
            min_occurrences=min_occurrences,
            limit=limit,
        )

    def episode_efficiency(self, episode_id):
        return self.efficiency.summarize(episode_id)
