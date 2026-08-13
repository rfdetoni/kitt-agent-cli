class ArtifactTools:
    def __init__(self,store):self.store=store
    def put(self,workspace_id,content,artifact_type,summary,conversation_id=None,turn_id=None):
        return self.store.put(workspace_id,content,artifact_type,summary,conversation_id,turn_id)
    def read_text(self,artifact_id):return self.store.read(artifact_id).decode("utf-8","replace")
    def list(self,conversation_id=None,limit=20,offset=0):return self.store.list(conversation_id,limit,offset)
