class ArtifactTools:
    def __init__(self, store):
        self.store = store

    def put(self, workspace_id, content, artifact_type, summary,
            conversation_id=None, turn_id=None):
        return self.store.put(
            workspace_id, content, artifact_type, summary,
            conversation_id, turn_id
        )

    def read_text(self, artifact_id, workspace_id=None, conversation_id=None):
        artifact = self.store.get(artifact_id)
        if not artifact:
            raise KeyError(artifact_id)
        if workspace_id and artifact.workspace_id != workspace_id:
            raise PermissionError("Cross-workspace artifact access blocked")
        if conversation_id and artifact.conversation_id not in (None, conversation_id):
            raise PermissionError("Cross-conversation artifact access blocked")
        return self.store.read(artifact_id).decode("utf-8", "replace")

    def list(self, conversation_id=None, limit=20, offset=0, workspace_id=None):
        return self.store.list(
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
            workspace_id=workspace_id,
        )
