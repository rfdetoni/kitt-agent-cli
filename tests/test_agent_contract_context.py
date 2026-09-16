import json
import unittest

from kitt.llm.agent_contract import (
    TURN_CONTEXT_MARKER,
    UNTRUSTED_WORKSPACE_LABEL,
    infer_agent_route,
    inject_agent_turn_context,
    normalize_agent_route,
    split_workspace_context,
)


class TestAgentContractContext(unittest.TestCase):
    def test_workspace_is_removed_from_system_prompt_and_returned_as_turn_data(self):
        system_prompt = (
            "Execution rules.\n\n"
            "Tool Contract:\nAvailable host tools: []\n\n"
            "Project context:\n"
            "Repository map:\n/home/dev/project/src/main.py\n"
            "UNTRUSTED_WORKSPACE_DATA: evidence"
        )

        orchestration, workspace = split_workspace_context(system_prompt)

        self.assertIn("Tool Contract:", orchestration or "")
        self.assertNotIn("/home/dev/project", orchestration or "")
        self.assertEqual(workspace["trust"], UNTRUSTED_WORKSPACE_LABEL)
        self.assertEqual(workspace["source"], "kitt-agent-cli")
        self.assertIn("/home/dev/project/src/main.py", workspace["data"])

    def test_missing_workspace_is_explicitly_not_provided(self):
        orchestration, workspace = split_workspace_context("Execution rules only.")
        self.assertEqual(orchestration, "Execution rules only.")
        self.assertEqual(workspace, "not_provided")

    def test_turn_context_is_structured_and_does_not_mutate_input_messages(self):
        original = [{"role": "user", "content": "Inspect the repository"}]
        result = inject_agent_turn_context(
            original,
            workspace_context={"data": "repo evidence"},
            route="context-gather",
        )

        self.assertEqual(len(original), 1)
        self.assertEqual(result[0]["role"], "developer")
        self.assertTrue(result[0]["content"].startswith(TURN_CONTEXT_MARKER))
        payload = json.loads(result[0]["content"].split("\n", 1)[1])
        self.assertEqual(payload["route"], "context-gather")
        self.assertEqual(payload["workspace_context"], {"data": "repo evidence"})
        self.assertEqual(result[1], original[0])

    def test_route_contract_is_closed_to_known_router_routes(self):
        self.assertEqual(normalize_agent_route(None), "chat")
        self.assertEqual(normalize_agent_route("validate-diff"), "validate-diff")
        with self.assertRaises(ValueError):
            normalize_agent_route("write-anything")

    def test_route_is_inferred_from_existing_tool_classifier_taxonomy(self):
        messages = [{"role": "user", "content": "Edit src/app.py and fix the bug"}]
        prompt = (
            "Execution rules.\n\nTool Contract:\n"
            "Available host tools: [{'name': 'read_file'}, {'name': 'write_file'}]\n\n"
            "Memory:\nnone"
        )
        self.assertEqual(infer_agent_route(prompt, messages), "code-edit")

        validation_prompt = (
            "Execution rules.\n\nTool Contract:\n"
            "Available host tools: [{'name': 'run_command'}, {'name': 'git_diff'}]\n\n"
            "Memory:\nnone"
        )
        validation_messages = [{"role": "user", "content": "Run tests and validate the diff"}]
        self.assertEqual(
            infer_agent_route(validation_prompt, validation_messages),
            "validate-diff",
        )

    def test_route_is_chat_without_a_tool_contract(self):
        self.assertEqual(
            infer_agent_route(
                "Answer directly and concisely.",
                [{"role": "user", "content": "Hello"}],
            ),
            "chat",
        )


if __name__ == "__main__":
    unittest.main()
