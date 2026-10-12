"""Focused safety and minimal protocol regressions."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from binary_inspect import BinaryInspectionError, inspect_file
from mcp_server import handle


class BinaryInspectTests(unittest.TestCase):
    def test_minimal_pe64(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = bytearray(512)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 0x3C, 0x80)
            data[0x80:0x84] = b"PE\0\0"
            struct.pack_into("<HH", data, 0x84, 0x8664, 1)
            struct.pack_into("<H", data, 0x94, 112)
            struct.pack_into("<H", data, 0x98, 0x20B)
            data[0x108:0x10d] = b".text"
            (Path(tmp) / "test.exe").write_bytes(data)
            result = inspect_file(Path(tmp), "test.exe")
            self.assertEqual(result["format"], "PE32+")
            self.assertEqual(result["architecture"], "x86_64")
            self.assertEqual(result["sections"], [".text"])
            reply = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "binary.inspect", "arguments": {"path": "test.exe"}}}, Path(tmp))
            self.assertFalse(reply["result"]["isError"])
            self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["section_count"], 1)

    def test_rejects_escapes_symlinks_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.exe").write_bytes(b"MZ")
            for name in ("../bad.exe", "/etc/passwd", "bad.exe"):
                with self.assertRaises(BinaryInspectionError):
                    inspect_file(root, name)
            (root / "link.exe").symlink_to(root / "bad.exe")
            with self.assertRaises(BinaryInspectionError):
                inspect_file(root, "link.exe")

    def test_mcp_handshake_and_unknown_tool(self):
        root = Path(".")
        init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, root)
        self.assertIn("protocolVersion", init["result"])
        error = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "unknown", "arguments": {}}}, root)
        self.assertEqual(error["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
