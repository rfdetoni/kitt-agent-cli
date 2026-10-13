"""Representative safety and MCP boundary regressions."""
import asyncio
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from kitt.extensions.builtin_plugins.reverse_engineering.binary import InspectionError, inspect
from kitt.extensions.builtin_plugins.reverse_engineering.evidence import compare, EvidenceError
from kitt.extensions.builtin_plugins.reverse_engineering.server import respond


class ReverseEngineeringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_pe(self):
        data = bytearray(512)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3C, 0x80)
        data[0x80:0x84] = b'PE\x00\x00'
        struct.pack_into('<H', data, 0x84, 0x8664)
        struct.pack_into('<H', data, 0x86, 1)
        struct.pack_into('<H', data, 0x94, 112)
        struct.pack_into('<H', data, 0x98, 0x20B)
        data[0x108:0x10D] = b'.text'
        (self.root / 'sample.exe').write_bytes(data)

    def test_pe_valid(self):
        self.make_pe()
        report = inspect(self.root, 'sample.exe')
        self.assertEqual(report['architecture'], 'x86_64')
        self.assertEqual(report['sections'][0]['name'], '.text')
        self.assertEqual(len(report['sha256']), 64)

    def test_elf_valid(self):
        data = bytearray(64)
        data[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', data, 18, 62)
        (self.root / 'sample.elf').write_bytes(data)
        self.assertEqual(inspect(self.root, 'sample.elf')['architecture'], 'x86_64')

    def test_denied_paths(self):
        (self.root / 'bad').write_bytes(b'MZ')
        (self.root / 'link').symlink_to('bad')
        for path in ['../bad', '/etc/passwd', 'C:\\Windows\\system32', 'link', 'bad']:
            with self.subTest(path=path), self.assertRaises(InspectionError):
                inspect(self.root, path)
        (self.root / 'directory').mkdir()
        with self.assertRaises(InspectionError):
            inspect(self.root, 'directory')

    def test_evidence(self):
        result = compare({'a': [1, 2]}, {'a': [1, 3]})
        self.assertFalse(result['match'])
        self.assertEqual(result['differences'][0]['path'], '/a/1')
        self.assertTrue(compare({'a': 1}, {'a': 1})['match'])
        self.assertFalse(compare({'a': 1}, {'a': True})['match'])
        with self.assertRaises(EvidenceError):
            compare({'x': 'a' * 150000}, {})

    def test_mcp_requests(self):
        self.make_pe()
        base = {'jsonrpc': '2.0', 'id': 1}
        self.assertIn('tools', respond({**base, 'method': 'tools/list'}, self.root)['result'])
        r = respond({**base, 'method': 'tools/call', 'params': {'name': 'binary_inspect', 'arguments': {'path': 'sample.exe'}}}, self.root)
        self.assertFalse(r['result']['isError'])
        self.assertEqual(json.loads(r['result']['content'][0]['text'])['format'], 'PE32+')
        denied = respond({**base, 'method': 'tools/call', 'params': {'name': 'binary_inspect', 'arguments': {'path': '../bad'}}}, self.root)
        self.assertTrue(denied['result']['isError'])

    def test_stdio_process(self):
        env = dict(os.environ)
        cmd = [sys.executable, '-m', 'kitt.extensions.builtin_plugins.reverse_engineering.server',
               '--workspace', str(self.root)]
        p = subprocess.run(cmd, input='{"jsonrpc":"2.0","id":1,"method":"initialize"}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
                           capture_output=True, text=True, timeout=5, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(p.stdout.splitlines()), 2)
        self.assertEqual(json.loads(p.stdout.splitlines()[0])['result']['protocolVersion'], '2024-11-05')



class McpStdioFramingTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_rpc_line_larger_than_asyncio_default_buffer(self):
        from kitt.extensions.mcp.transport import StdioTransport

        program = (
            "import json,sys; sys.stdin.readline();"
            "print(json.dumps({'jsonrpc':'2.0','id':1,"
            "'result':{'data':'x'*131072}}),flush=True)"
        )
        transport = StdioTransport(sys.executable, ["-c", program])
        await transport.connect()
        try:
            await transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            response = await asyncio.wait_for(transport.receive(), timeout=5)
            self.assertEqual(len(response["result"]["data"]), 131072)
        finally:
            await transport.close()

    async def test_json_rpc_line_still_rejects_messages_over_two_mib(self):
        from kitt.extensions.errors import MCPTransportError
        from kitt.extensions.mcp.transport import StdioTransport

        program = (
            "import json,sys; sys.stdin.readline();"
            "print(json.dumps({'jsonrpc':'2.0','id':1,"
            "'result':{'data':'x'*(2*1024*1024)}}),flush=True)"
        )
        transport = StdioTransport(sys.executable, ["-c", program])
        await transport.connect()
        try:
            await transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            with self.assertRaisesRegex(MCPTransportError, "2 MB transport limit"):
                await asyncio.wait_for(transport.receive(), timeout=5)
        finally:
            await transport.close()


if __name__ == '__main__':
    unittest.main()
