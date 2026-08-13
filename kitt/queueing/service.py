from typing import List
from kitt.queueing.repository import InputQueueRepository

class InputQueueService:
    def __init__(self, repository: InputQueueRepository):
        self.repo=repository
    def steer(self, conversation_id: str, content: str, generation: int=0):
        return self.repo.enqueue(conversation_id,"STEERING",content,generation)
    def follow_up(self, conversation_id: str, content: str, generation: int=0):
        return self.repo.enqueue(conversation_id,"FOLLOW_UP",content,generation)
    def drain(self, conversation_id: str, kind: str=None, limit: int=20) -> List[str]:
        items=self.repo.pending(conversation_id,kind,limit); out=[]
        for item in items:
            if self.repo.deliver(item.id): out.append(item.content)
        return out
