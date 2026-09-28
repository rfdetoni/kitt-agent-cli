from kitt.core.turn_events import ToolCompleted
from kitt.ui.reducer_handlers import handle_tool_completed
from kitt.ui.state import UIState


def test_tool_completed_carries_surface_to_tui_projection():
    state = UIState()
    event = ToolCompleted(
        tool_name="kitt_runtime",
        success=True,
        metadata={
            "operation": "surface.publish",
            "surface": {
                "id": "s1",
                "revision": 1,
                "catalog_id": "kitt.core.v1",
                "root": "root",
                "components": [
                    {
                        "id": "root",
                        "component": "Heading",
                        "props": {"text": "Deploy ready"},
                        "children": [],
                    }
                ],
                "state": {},
                "metadata": {},
            },
        },
    )
    handle_tool_completed(state, event)
    block = state.transcript[-1]
    assert block.metadata["surface"]["id"] == "s1"
    assert "Deploy ready" in block.text
