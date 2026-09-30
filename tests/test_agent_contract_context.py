import json
import unittest

from kitt.llm.agent_contract import (
    TURN_CONTEXT_END_MARKER,
    TURN_CONTEXT_MARKER,
    UNTRUSTED_WORKSPACE_LABEL,
    compact_reverse_proxy_orchestration,
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
        self.assertEqual(workspace["sections"][0]["section"], "Project context")
        self.assertIn("/home/dev/project/src/main.py", workspace["sections"][0]["data"])

    def test_tool_enabled_repo_sections_are_removed_but_orchestration_stays(self):
        system_prompt = (
            "Execution rules.\n\n"
            "Tool Contract:\nAvailable host tools: [{'name': 'read_file'}]\n\n"
            "Memory:\ntrusted memory\n\n"
            "Active Skills:\nworkspace skill body\n\n"
            "Project Guidelines:\nAGENTS.md says do something\n\n"
            "Learned Harness:\ntrusted harness\n\n"
            "Mandatory Constraints:\nkeep this constraint\n\n"
            "Files Context:\n/home/dev/project/src/app.py\n\n"
            "Repo Map:\nsrc/app.py\n\n"
            "Recent Conversation:\nprior evidence\n\n"
            "[PLANNING MODE ACTIVE]\nDo not modify files."
        )

        orchestration, workspace = split_workspace_context(system_prompt)
        self.assertIn("Tool Contract:", orchestration or "")
        self.assertIn("Memory:\ntrusted memory", orchestration or "")
        self.assertIn("Learned Harness:\ntrusted harness", orchestration or "")
        self.assertIn("Mandatory Constraints:\nkeep this constraint", orchestration or "")
        self.assertIn("[PLANNING MODE ACTIVE]", orchestration or "")
        self.assertNotIn("/home/dev/project", orchestration or "")
        sections = {item["section"]: item["data"] for item in workspace["sections"]}
        self.assertIn("Active Skills", sections)
        self.assertIn("Project Guidelines", sections)
        self.assertIn("Files Context", sections)
        self.assertIn("Repo Map", sections)
        self.assertIn("Recent Conversation", sections)

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
        self.assertEqual(result[0]["role"], "user")
        self.assertTrue(result[0]["content"].startswith(TURN_CONTEXT_MARKER))
        envelope, original_content = result[0]["content"].split(
            f"\n{TURN_CONTEXT_END_MARKER}\n\n", 1
        )
        payload = json.loads(envelope.split("\n", 1)[1])
        self.assertEqual(payload["route"], "context-gather")
        self.assertEqual(payload["workspace_context"], {"data": "repo evidence"})
        self.assertNotIn("discovery_required", payload)
        self.assertEqual(original_content, original[0]["content"])
        self.assertEqual(original, [{"role": "user", "content": "Inspect the repository"}])

    def test_turn_context_is_appended_as_user_turn_when_no_text_user_exists(self):
        original = [{"role": "assistant", "content": "previous answer"}]
        result = inject_agent_turn_context(
            original,
            workspace_context="not_provided",
            route="chat",
        )

        self.assertEqual(result[0], original[0])
        self.assertEqual(result[-1]["role"], "user")
        self.assertTrue(result[-1]["content"].startswith(TURN_CONTEXT_MARKER))
        self.assertTrue(result[-1]["content"].endswith(TURN_CONTEXT_END_MARKER))


    def test_agent_loop_turn_context_carries_bounded_action_budget(self):
        original = [{"role": "user", "content": "Faça o trabalho solicitado"}]
        result = inject_agent_turn_context(
            original,
            workspace_context={"files": ["package.json"]},
            route="agent-loop",
            loop_action_budget=6,
        )
        envelope = result[0]["content"].split(
            f"\n{TURN_CONTEXT_END_MARKER}\n\n", 1
        )[0]
        payload = json.loads(envelope.split("\n", 1)[1])
        self.assertEqual(payload["route"], "agent-loop")
        self.assertEqual(payload["loop_action_budget"], 6)
        self.assertEqual(
            result[0]["content"].split(f"\n{TURN_CONTEXT_END_MARKER}\n\n", 1)[1],
            original[0]["content"],
        )

    def test_turn_context_preserves_discovery_phase_for_reverse_proxy(self):
        original = [{"role": "user", "content": "Build the application"}]
        result = inject_agent_turn_context(
            original,
            workspace_context={"files": [".kitt-router.json"]},
            route="code-generation",
            discovery_required=True,
        )

        envelope = result[0]["content"].split(
            f"\n{TURN_CONTEXT_END_MARKER}\n\n", 1
        )[0]
        payload = json.loads(envelope.split("\n", 1)[1])
        self.assertTrue(payload["discovery_required"])
        self.assertEqual(payload["execution_phase"], "discovery")


    def test_reverse_proxy_orchestration_drops_duplicate_persona_and_tool_contract(self):
        system_prompt = (
            "You are an autonomous coding agent operating inside the user's workspace.\n\n"
            "Tool Contract:\nAvailable host tools: [{'name': 'kitt_runtime'}]\n\n"
            "Memory:\ntrusted memory\n\n"
            "Learned Harness:\ncompact harness\n\n"
            "[KITT EXECUTION SLICE: DISCOVERY]\nInspect first."
        )
        compact = compact_reverse_proxy_orchestration(system_prompt)
        self.assertNotIn("autonomous coding agent", compact or "")
        self.assertNotIn("Tool Contract:", compact or "")
        self.assertIn("Memory:\ntrusted memory", compact or "")
        self.assertIn("Learned Harness:\ncompact harness", compact or "")
        self.assertIn("[KITT EXECUTION SLICE: DISCOVERY]", compact or "")
        self.assertLessEqual(len((compact or "").encode("utf-8")), 4096)

    def test_mutation_turn_context_includes_compact_execution_plan(self):
        result = inject_agent_turn_context(
            [{"role": "user", "content": "Build the application"}],
            workspace_context={"files": ["package.json"]},
            route="code-generation",
            discovery_required=True,
        )
        envelope = result[0]["content"].split(
            f"\n{TURN_CONTEXT_END_MARKER}\n\n", 1
        )[0]
        payload = json.loads(envelope.split("\n", 1)[1])
        self.assertEqual(
            payload["execution_plan"],
            ["discovery", "mutation", "validation"],
        )
        self.assertEqual(payload["execution_phase"], "discovery")

    def test_route_contract_is_closed_to_known_router_routes(self):
        self.assertEqual(normalize_agent_route(None), "chat")
        self.assertEqual(normalize_agent_route("validate-diff"), "validate-diff")
        self.assertEqual(normalize_agent_route("agent-loop"), "agent-loop")
        with self.assertRaises(ValueError):
            normalize_agent_route("write-anything")

    def test_tool_enabled_route_is_agent_loop_independent_of_user_language(self):
        prompt = (
            "Execution rules.\n\nTool Contract:\n"
            "Available host tools: [{'name': 'kitt_runtime'}]\n\n"
            "Memory:\nnone"
        )
        for user_text in (
            "Edit src/app.py and fix the bug",
            "Corrija src/app.py sem criar backend",
            "src/app.py を修正してください",
            "Bitte korrigieren Sie src/app.py",
        ):
            with self.subTest(user_text=user_text):
                self.assertEqual(
                    infer_agent_route(prompt, [{"role": "user", "content": user_text}]),
                    "agent-loop",
                )

    def test_tool_result_continuation_keeps_agent_loop_route(self):
        prompt = (
            "Execution rules.\n\nTool Contract:\n"
            "Available host tools: [{'name': 'kitt_runtime'}]\n\n"
            "Memory:\nnone"
        )
        messages = [
            {"role": "user", "content": "Não crie backend; implemente apenas o escopo pedido."},
            {
                "role": "assistant",
                "content": (
                    '<kitt-tool>{"id":"call_1","name":"kitt_runtime","arguments":'
                    '{"operation":"repo.list","arguments":{"path":"."}}}</kitt-tool>'
                ),
            },
            {
                "role": "user",
                "content": (
                    "kitt_runtime result from the host.\n"
                    "HOST_STATUS: success\n"
                    "UNTRUSTED_TOOL_OUTPUT: untrusted data follows:\n"
                    '{"entries":[{"path":"src","type":"directory"}]}'
                ),
            },
        ]

        self.assertEqual(infer_agent_route(prompt, messages), "agent-loop")


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
