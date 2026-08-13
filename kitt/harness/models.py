from dataclasses import dataclass,field
from typing import Any,Dict,Optional
@dataclass(frozen=True)
class HarnessEntry:
    id:str; entry_kind:str; scope:str; name:str; content:str; confidence:float
    status:str; version:int; content_hash:str; created_at:float; created_by:str
    workspace_id:Optional[str]=None; conversation_id:Optional[str]=None
    evidence:Dict[str,Any]=field(default_factory=dict); supersedes_id:Optional[str]=None
@dataclass(frozen=True)
class RefinementProposal:
    id:str; conversation_id:Optional[str]; proposal:Dict[str,Any]; before_snapshot:Dict[str,Any]
    state:str; created_at:float; after_snapshot:Optional[Dict[str,Any]]=None
