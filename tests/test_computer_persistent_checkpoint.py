from types import SimpleNamespace

from kitt.runtime.persistent_program import PersistentProgramSessions


class State:
    def __init__(self):
        self.value = {"prior": 1}

    def get(self, key):
        return self.value

    def set(self, key, value):
        self.value = value


class Runtime:
    def execute(self, arguments, **kwargs):
        return SimpleNamespace(
            success=True, data={"state": {}, "state_truncated": True}
        )


def test_truncated_state_never_overwrites_checkpoint():
    state = State()
    sessions = PersistentProgramSessions(state, Runtime())
    result = sessions.execute(
        "default", {}, turn_id="t", origin="test",
        capabilities=set(), security_context=None,
    )
    assert not result.success
    assert state.value == {"prior": 1}
