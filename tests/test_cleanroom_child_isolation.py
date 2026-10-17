"""Reserved Clean-Room identities must never use shared retained-agent state."""
import threading
import pytest

from kitt.children.lifecycle import ChildAgentManager


@pytest.mark.parametrize("name,task", [
    ("wine-implementer", "Modify Wine without analyst context"),
    ("spec-extractor", "Read only Windows black-box observations"),
    ("harmless-alias", "[SPECIALIST PROFILE: wine-implementer]\nImplement public API"),
])
def test_cleanroom_persona_rejects_child_spawning_before_repository(name, task):
    manager = object.__new__(ChildAgentManager)
    manager._execution_lock = threading.RLock()
    with pytest.raises(PermissionError, match="Clean-Room agents require"):
        manager.spawn(parent_conversation_id="conv", parent_turn_id="turn",
                      name=name, task=task)
