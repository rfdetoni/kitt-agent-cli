import hashlib,json,time,uuid
from kitt.harness.models import HarnessEntry,RefinementProposal
class HarnessRepository:
    def __init__(self,db): self.db=db
    @staticmethod
    def _entry(r):
        d=dict(r); d["evidence"]=json.loads(d.pop("evidence_json")); return HarnessEntry(**d)
    def add(self,entry_kind,scope,name,content,created_by,workspace_id=None,
            conversation_id=None,evidence=None,confidence=1.0,supersedes_id=None):
        hid=f"h_{uuid.uuid4().hex}"; digest=hashlib.sha256(content.encode()).hexdigest(); now=time.time()
        with self.db.get_connection() as c:
            version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM harness_entries WHERE scope=? AND name=?",(scope,name)).fetchone()[0]
            c.execute("""INSERT INTO harness_entries(id,workspace_id,conversation_id,entry_kind,scope,name,
                content,evidence_json,confidence,status,version,supersedes_id,content_hash,created_at,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,?,?,?)""",(hid,workspace_id,conversation_id,entry_kind,
                scope,name,content,json.dumps(evidence or {}),max(0,min(confidence,1)),version,
                supersedes_id,digest,now,created_by))
            if supersedes_id:c.execute("UPDATE harness_entries SET status='SUPERSEDED' WHERE id=?",(supersedes_id,))
        return self.get(hid)
    def get(self,hid):
        with self.db.get_connection() as c:
            r=c.execute("SELECT * FROM harness_entries WHERE id=?",(hid,)).fetchone(); return self._entry(r) if r else None
    def active(self,workspace_id=None,conversation_id=None):
        sql="SELECT * FROM harness_entries WHERE status='ACTIVE'"; args=[]
        if workspace_id: sql+=" AND (workspace_id=? OR workspace_id IS NULL)"; args.append(workspace_id)
        if conversation_id: sql+=" AND (conversation_id=? OR conversation_id IS NULL)"; args.append(conversation_id)
        sql+=" ORDER BY scope,name,version"
        with self.db.get_connection() as c:return [self._entry(r) for r in c.execute(sql,args).fetchall()]
    def save_proposal(self,conversation_id,proposal,before):
        rid=f"ref_{uuid.uuid4().hex}"; now=time.time()
        with self.db.get_connection() as c:c.execute("""INSERT INTO harness_refinements
            (id,conversation_id,proposal_json,before_snapshot_json,state,created_at)
            VALUES(?,?,?,?, 'PROPOSED',?)""",(rid,conversation_id,json.dumps(proposal),json.dumps(before),now))
        return rid
    def apply_proposal(self,rid,after):
        with self.db.get_connection() as c:c.execute("UPDATE harness_refinements SET state='APPLIED',after_snapshot_json=?,applied_at=? WHERE id=? AND state='PROPOSED'",(json.dumps(after),time.time(),rid))
    def rollback(self,rid):
        with self.db.get_connection() as c:c.execute("UPDATE harness_refinements SET state='ROLLED_BACK',rolled_back_at=? WHERE id=? AND state='APPLIED'",(time.time(),rid))
