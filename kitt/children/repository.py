import json,time,uuid
from typing import List,Optional
from kitt.children.models import ChildSession
from kitt.history.database import HistoryDatabase
class ChildRepository:
    def __init__(self,db:HistoryDatabase): self.db=db
    @staticmethod
    def _row(r):
        d=dict(r); d["allowed_paths"]=json.loads(d.pop("allowed_paths_json")); d["enabled_tools"]=json.loads(d.pop("enabled_tools_json"))
        return ChildSession(**d)
    def create(self,parent_conversation_id,parent_turn_id,name,task,depth,model_profile,
               allowed_paths,enabled_tools,token_budget,timeout_seconds):
        cid=f"child_{uuid.uuid4().hex}"; now=time.time()
        with self.db.get_connection() as c:
            c.execute("""INSERT INTO child_sessions(id,parent_conversation_id,parent_turn_id,name,task,state,depth,
                model_profile,allowed_paths_json,enabled_tools_json,token_budget,timeout_seconds,created_at)
                VALUES(?,?,?,?,?,'CREATED',?,?,?,?,?,?,?)""",(cid,parent_conversation_id,parent_turn_id,name,task,depth,
                model_profile,json.dumps(allowed_paths),json.dumps(enabled_tools),token_budget,timeout_seconds,now))
        return self.get(cid)
    def get(self,cid)->Optional[ChildSession]:
        with self.db.get_connection() as c:
            r=c.execute("SELECT * FROM child_sessions WHERE id=?",(cid,)).fetchone(); return self._row(r) if r else None
    def list(self,parent_conversation_id,limit=20)->List[ChildSession]:
        with self.db.get_connection() as c:
            return [self._row(r) for r in c.execute("SELECT * FROM child_sessions WHERE parent_conversation_id=? ORDER BY created_at DESC LIMIT ?",(parent_conversation_id,min(max(limit,1),100))).fetchall()]
    def update(self,cid,**fields):
        allowed={"state","tokens_used","result_artifact_id","error","started_at","completed_at"}
        fields={k:v for k,v in fields.items() if k in allowed}
        if not fields:return
        with self.db.get_connection() as c:
            c.execute("UPDATE child_sessions SET "+",".join(f"{k}=?" for k in fields)+" WHERE id=?",[*fields.values(),cid])
