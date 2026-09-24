import io
import json
import unittest
from unittest.mock import patch

from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.kitt_reverse_proxy import KittReverseProxyAdapter
from kitt.prompts import KITT_AGENT_PERSONA


MEUFAZTUDO_PROMPT = (
    'crie um site moderno e limpo para registrar prestadores de serviço, será chamado '
    'meufaztudo e juntará maridos de aluguel a pessoas que precisam contratar o serviço, '
    'crie pasta de backend com o conteudo de backend e pasta de front end com todo o front '
    'em angular. Crie o projeto e a implementação'
)


class _DoneResponse:
    def __init__(self):
        self._stream = io.BytesIO(b'data: [DONE]\n')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def readline(self, size=-1):
        return self._stream.readline(size)


class ReverseProxyWorkspaceToolFailsafeTests(unittest.TestCase):
    def _capture_payload(self, request):
        captured = {}

        def fake_open(http_request, timeout):
            captured['payload'] = json.loads(http_request.data.decode('utf-8'))
            return _DoneResponse()

        with patch(
            'kitt.llm.providers.kitt_reverse_proxy.secure_urlopen',
            side_effect=fake_open,
        ):
            list(KittReverseProxyAdapter().stream(request))
        return captured['payload']

    def test_mutation_text_does_not_resurrect_runtime_when_contract_is_missing(self):
        payload = self._capture_payload(LLMRequest(
            model='gemini-web',
            system_prompt=(
                'Answer in one direct, concise sentence. Do not expose reasoning.\n\n'
                'Project context:\nRepository map:\n## Context v2'
            ),
            messages=[{'role': 'user', 'content': MEUFAZTUDO_PROMPT}],
            extra_headers={'X-Kitt-Route': 'code-generation'},
        ))

        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)
        self.assertEqual(
            payload['messages'][0]['content'],
            (
                'Answer in one direct, concise sentence. Do not expose reasoning.\n\n'
                'Project context:\nRepository map:\n## Context v2'
            ),
        )

    def test_execution_followup_without_contract_does_not_gain_runtime_tool(self):
        payload = self._capture_payload(LLMRequest(
            model='gemini-web',
            system_prompt='Answer directly and concisely.',
            messages=[
                {'role': 'user', 'content': MEUFAZTUDO_PROMPT},
                {'role': 'assistant', 'content': 'Posso implementar.'},
                {'role': 'user', 'content': 'faça isso'},
            ],
            extra_headers={'X-Kitt-Route': 'code-edit'},
        ))

        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)

    def test_explicit_context_plan_contract_is_the_only_runtime_tool_authority(self):
        payload = self._capture_payload(LLMRequest(
            model='gemini-web',
            system_prompt=(
                "Tool Contract:\n"
                "Available host tools: [{'name': 'kitt_runtime', 'description': 'Workspace runtime', "
                "'args': {'operation': {'type': 'string', 'enum': ['repo.read', 'repo.write_file']}, "
                "'arguments': {'type': 'object', 'additionalProperties': True}}}]\n"
                "For a host tool, respond with exactly: <kitt-tool>...</kitt-tool>\n\n"
                "Memory:\nnone"
            ),
            messages=[{'role': 'user', 'content': MEUFAZTUDO_PROMPT}],
            extra_headers={'X-Kitt-Route': 'code-generation'},
        ))

        self.assertEqual(payload['tool_choice'], 'auto')
        self.assertEqual(
            [tool['function']['name'] for tool in payload['tools']],
            ['kitt_runtime'],
        )
        self.assertIn(KITT_AGENT_PERSONA, payload['messages'][0]['content'])

    def test_meufaztudo_context_summary_never_gains_mutation_tool(self):
        payload = self._capture_payload(LLMRequest(
            model='gemini-web',
            system_prompt='Prepare a short technical context for another model to answer the task.',
            messages=[{'role': 'user', 'content': MEUFAZTUDO_PROMPT}],
            extra_headers={'X-Kitt-Route': 'summarize'},
        ))

        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)

    def test_plain_question_without_contract_does_not_gain_workspace_tool(self):
        payload = self._capture_payload(LLMRequest(
            model='gemini-web',
            system_prompt='Answer in one direct, concise sentence. Do not expose reasoning.',
            messages=[{'role': 'user', 'content': 'explique o que é Spring Boot'}],
        ))

        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)
        self.assertEqual(
            payload['messages'][0]['content'],
            'Answer in one direct, concise sentence. Do not expose reasoning.',
        )


if __name__ == '__main__':
    unittest.main()
