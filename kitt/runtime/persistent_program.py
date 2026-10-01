from __future__ import annotations


class PersistentProgramSessions:
    """Durable variable scope for the bounded declarative program runtime."""

    PREFIX = "program-session:"

    def __init__(self, state_store, program_runtime):
        self.state_store = state_store
        self.program_runtime = program_runtime

    def _key(self, name):
        value = str(name or "default").strip()
        if not value or len(value) > 64:
            raise ValueError("program session name must contain 1-64 characters")
        return self.PREFIX + value

    def snapshot(self, name="default"):
        if self.state_store is None:
            return {}
        value = self.state_store.get(self._key(name))
        return dict(value) if isinstance(value, dict) else {}

    def clear(self, name="default"):
        if self.state_store is None:
            return False
        return self.state_store.delete(self._key(name))

    def execute(self, name, arguments, *, turn_id, origin, capabilities, security_context):
        from kitt.runtime.safe_runtime import SafeRuntimeResult

        if self.state_store is None:
            return SafeRuntimeResult(
                False,
                "program.session.execute",
                error="persistent runtime state is unavailable",
            )
        prior = self.snapshot(name)
        request = dict(arguments)
        explicit = request.get("state")
        if explicit is not None and not isinstance(explicit, dict):
            return SafeRuntimeResult(
                False,
                "program.session.execute",
                error="state must be an object",
            )
        request["state"] = {**prior, **(explicit or {})}
        request["capture_state"] = True
        result = self.program_runtime.execute(
            request,
            turn_id=turn_id,
            origin=origin,
            capabilities=capabilities,
            security_context=security_context,
        )
        if result.success:
            state = (result.data or {}).get("state")
            if isinstance(state, dict):
                self.state_store.set(self._key(name), state)
        result.operation = "program.session.execute"
        return result
