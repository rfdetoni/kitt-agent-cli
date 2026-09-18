import unittest
from types import SimpleNamespace

from kitt.core.execution_request import ExecutionRequest
from kitt.core.turn_processor import TurnProcessor


class ExecutionRequestRouteRuntimeTests(unittest.TestCase):
    def test_tool_loop_reads_route_from_execution_request(self):
        processor = object.__new__(TurnProcessor)
        processor.cancelled_turns = set()
        processor._rebudget_execution_messages = lambda *args, **kwargs: None
        processor._provider_session_key = lambda *args, **kwargs: "session"

        seen = {}

        def stream(
            exe_client,
            messages,
            system_prompt,
            *,
            turn_id,
            started_at,
            session_key=None,
            route=None,
        ):
            seen["route"] = route
            yield "final answer", None

        processor._stream_execution_response = stream

        cmd = SimpleNamespace(
            turn_id="turn-1",
            conversation_id="conversation-1",
            explicit_files=set(),
            prompt="hello",
            mode="auto",
        )
        request = ExecutionRequest(
            system_prompt="system",
            messages=[{"role": "user", "content": "hello"}],
            enabled_tools=[],
            agent_route="code-generation",
        )

        items = list(
            processor._execute_tool_loop(
                cmd,
                request,
                object(),
                object(),
                "workspace",
                object(),
            )
        )

        self.assertEqual(seen["route"], "code-generation")
        self.assertEqual(items[-1][1], "final answer")


if __name__ == "__main__":
    unittest.main()
