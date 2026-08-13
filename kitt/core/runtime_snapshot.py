from dataclasses import asdict,dataclass
from typing import Any,Dict
@dataclass(frozen=True)
class RuntimeSnapshot:
    workspace_id:str
    active_conversation_id:str=""
    pending_actions:int=0
    queued_inputs:int=0
    active_goal_id:str=""
    def to_dict(self)->Dict[str,Any]: return asdict(self)
