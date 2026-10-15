"""Prevent frozen installs from using a Protocol older than runtime imports."""
import importlib.metadata
import pathlib
import tomllib
import unittest


class ProtocolLockTests(unittest.TestCase):
    def test_resolved_protocol_matches_installed_contract(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        entries = [item for item in lock.get("package", []) if item["name"] == "kitt-protocol"]
        self.assertEqual(len(entries), 1)
        expected = importlib.metadata.version("kitt-protocol")
        self.assertEqual(entries[0]["version"], expected)
        from kitt_protocol import AGENT_CONTRACT_VERSION, decode_json_object
        self.assertEqual(AGENT_CONTRACT_VERSION, "v5")
        self.assertEqual(decode_json_object('{"format":"json"}'), {"format":"json"})


if __name__ == "__main__":
    unittest.main()
