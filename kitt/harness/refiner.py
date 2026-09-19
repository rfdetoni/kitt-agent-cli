class HarnessRefiner:
    def __init__(self,repository): self.repo=repository
    def propose(self,conversation_id,proposal,before_snapshot):
        return self.repo.save_proposal(conversation_id,proposal,before_snapshot)
    def apply(self,proposal_id,after_snapshot):
        self.repo.apply_proposal(proposal_id,after_snapshot)
    def rollback(self,proposal_id):
        self.repo.rollback(proposal_id)
