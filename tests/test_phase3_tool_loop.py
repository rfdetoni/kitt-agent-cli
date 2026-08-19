import unittest
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.agent_loop import AgentLoop

class TestPhase3ToolLoop(unittest.TestCase):
    def setUp(self):
        self.policy = PolicyEngine()
        self.loop = AgentLoop(max_steps=5, max_same_failures=2)

    def test_policy_engine_permissions(self):
        self.assertEqual(self.policy.evaluate_tool('list_files'), 'ALLOW')
        self.assertEqual(self.policy.evaluate_tool('python_compute'), 'ALLOW')
        self.assertEqual(self.policy.evaluate_tool('apply_patch'), 'ASK')

        self.assertEqual(self.policy.evaluate_command('git status'), 'ALLOW')
        self.assertEqual(self.policy.evaluate_command('python3 -m unittest'), 'ASK')
        self.assertEqual(self.policy.evaluate_command('rm -rf /'), 'DENY')

    def test_agent_loop_state_transitions(self):
        self.assertEqual(self.loop.state, 'DISCOVER')
        self.assertTrue(self.loop.can_continue())

        s1 = self.loop.record_step('read_file', success=True, output="content")
        self.assertEqual(s1, 'EDIT')

        s2 = self.loop.record_step('apply_patch', success=True, output="applied")
        self.assertEqual(s2, 'VERIFY')

        s3 = self.loop.record_step('run_command', success=True, output="tests pass")
        self.assertEqual(s3, 'DONE')
        self.assertFalse(self.loop.can_continue())

    def test_repetition_failure_blocks_loop(self):
        s1 = self.loop.record_step('apply_patch', success=False, output="", error="SEARCH mismatch")
        self.assertEqual(s1, 'REPAIR')
        self.assertTrue(self.loop.can_continue())

        s2 = self.loop.record_step('apply_patch', success=False, output="", error="SEARCH mismatch")
        self.assertEqual(s2, 'BLOCKED')
        self.assertFalse(self.loop.can_continue())

    def test_parse_tool_call_robust(self):
        from kitt.tools.protocol import parse_tool_call

        # 1. Standard call
        sample1 = '<kitt-tool>\n{"name": "read_file", "arguments": {"path": "main.py"}}\n</kitt-tool>'
        name, args = parse_tool_call(sample1)
        self.assertEqual(name, "read_file")
        self.assertEqual(args["path"], "main.py")

        # 2. Call preceded by thinking block
        sample2 = '<think>Vou criar o arquivo de apresentação</think>\n<kitt-tool>\n{"name": "write_file", "arguments": {"path": "apresentacao.html", "content": "<h1>Knight Rider</h1>"}}\n</kitt-tool>'
        name, args = parse_tool_call(sample2)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["path"], "apresentacao.html")
        self.assertEqual(args["content"], "<h1>Knight Rider</h1>")

        # 3. Call with unescaped internal double quotes in HTML/JS string
        sample3 = '<kitt-tool>\n{"name": "write_file", "arguments": {"path": "apresentacao.html", "content": "<div class=\\"hero\\"><p>\\"Eu estou aqui para ajudar.\\" — K.I.T.T.</p></div>"}}\n</kitt-tool>'
        name, args = parse_tool_call(sample3)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["path"], "apresentacao.html")
        self.assertIn("Eu estou aqui para ajudar.", args["content"])

        # 4. Call with unescaped raw newlines (strict=False)
        sample4 = '<kitt-tool>\n{"name": "write_file", "arguments": {"path": "test.txt", "content": "Linha 1\nLinha 2"}}\n</kitt-tool>'
        name, args = parse_tool_call(sample4)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["content"], "Linha 1\nLinha 2")

        # 5. Call wrapped in markdown code fence with unescaped HTML quotes
        sample5 = '<kitt-tool>\n```json\n{\n  "name": "write_file",\n  "arguments": {\n    "path": "src/html/apresentacao.html",\n    "content": "<!DOCTYPE html>\\n<html lang=\\"pt-BR\\">\\n<head><meta charset=\\"UTF-8\\"><title>K.I.T.T.</title></head>\\n</html>"\n  }\n}\n```\n</kitt-tool>'
        name, args = parse_tool_call(sample5)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["path"], "src/html/apresentacao.html")
        self.assertIn("<meta charset=\"UTF-8\">", args["content"])

        # 6. Flat top-level parameters and alias
        sample6 = '```json\n{"name": "write", "path": "src/html/apresentacao.html", "content": "<h1>Title</h1>"}\n```'
        name, args = parse_tool_call(sample6)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["path"], "src/html/apresentacao.html")

        # 7. Function call syntax Write(path="...", content="...")
        sample7 = 'Write(path="src/html/apresentacao.html", content="<!DOCTYPE html><html><body>Test</body></html>")'
        name, args = parse_tool_call(sample7)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["path"], "src/html/apresentacao.html")

        # 8. XML tool syntax
        sample8 = '<write_file path="src/html/apresentacao.html">\n<!DOCTYPE html><html><body>XML Content</body></html>\n</write_file>'
        name, args = parse_tool_call(sample8)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["path"], "src/html/apresentacao.html")
        self.assertIn("XML Content", args["content"])

        # 9. Non-tool text returns None
        self.assertIsNone(parse_tool_call("Apenas uma resposta em texto sem ferramentas."))

        # 10. Tagged code block (e.g. ```html:apresentacao.html)
        sample10 = 'Aqui está a atualização:\n```html:apresentacao.html\n<!DOCTYPE html><html><body>Tagged</body></html>\n```'
        name, args = parse_tool_call(sample10)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["path"], "apresentacao.html")
        self.assertIn("Tagged", args["content"])

        # 11. Labeled code block (e.g. File: `apresentacao.html`)
        sample11 = 'Atualizei o arquivo:\nArquivo: `apresentacao.html`\n```html\n<!DOCTYPE html><html><body>Labeled</body></html>\n```'
        name, args = parse_tool_call(sample11)
        self.assertEqual(name, "write_file")
        self.assertEqual(args["path"], "apresentacao.html")
        self.assertIn("Labeled", args["content"])


if __name__ == '__main__':
    unittest.main()
