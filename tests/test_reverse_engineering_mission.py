"""Host-owned unattended mission/guest protocol regressions; no remote SSH required."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kitt.extensions.builtin_plugins.reverse_engineering.guest_rpc import handle as guest_handle
from kitt.extensions.builtin_plugins.reverse_engineering.mission import (
    MissionError, _mission, _hosts, plan_mission, run_mission,
)


def receipt(probe, platform, observation):
    row = {
        "schema_version": 1, "probe": probe, "probe_sha256": "a" * 64,
        "platform": platform, "release": "test", "exit_code": 0,
        "timed_out": False, "truncated": False, "duration_ms": 1,
        "stdout": json.dumps(observation), "stderr": "",
    }
    row["sha256"] = hashlib.sha256(
        json.dumps(row, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return row


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / "hosts.json"
        ssh_conf = self.root / "ssh_config"
        ssh_conf.write_text("Host windows-test\n  HostName 127.0.0.1\n")
        self.profile.write_text(json.dumps({
            "schema_version": 1, "ssh_config": str(ssh_conf),
            "reference": "windows-test", "candidate": "wine-test",
        }))
        if os.name == "posix":
            self.profile.chmod(0o600)
        self.mission = self.root / "mission.json"
        self.mission.write_text('{"schema_version":1,"probes":["event_auto_reset"]}')

    def test_mission_match_and_archive(self):
        def remote(_hosts, alias, name):
            return receipt(name, "Windows" if alias == "windows-test" else "Linux+Wine", {"value": 1})
        result = run_mission(self.root, self.mission, self.profile, remote_runner=remote)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["blocked"], 0)
        self.assertEqual(result["divergent"], 0)
        self.assertEqual(result["completed"], 1)

    def test_mission_divergence_and_block(self):
        def remote(_hosts, alias, name):
            return receipt(name, "Windows" if alias == "windows-test" else "Linux+Wine",
                           {"value": 1 if alias == "windows-test" else 2})
        result = run_mission(self.root, self.mission, self.profile, remote_runner=remote)
        self.assertEqual(result["divergent"], 1)
        self.assertEqual(result["probes"][0]["differences"][0]["path"], "/value")

        def offline(_hosts, alias, name):
            if alias == "wine-test":
                raise MissionError("Guest unreachable")
            return receipt(name, "Windows", {"value": 1})
        blocked = run_mission(self.root, self.mission, self.profile, remote_runner=offline)
        self.assertEqual(blocked["blocked"], 1)
        self.assertEqual(blocked["completed"], 0)

    def test_fails_closed_on_modified_guest_role_and_untrusted_profile(self):
        def rogue(_hosts, alias, name):
            return receipt(name, "Windows", {"value": 1})
        result = run_mission(self.root, self.mission, self.profile, remote_runner=rogue)
        self.assertEqual(result["blocked"], 1)
        if os.name == "posix":
            self.profile.chmod(0o644)
            with self.assertRaises(MissionError):
                _hosts(self.profile)

    def test_rejects_unknown_or_duplicate_probes(self):
        self.mission.write_text('{"schema_version":1,"probes":["a","a"]}')
        with self.assertRaises(MissionError):
            _mission(self.mission, self.root)
        self.mission.write_text('{"schema_version":1,"probes":["a","a"],"probes":["b"]}')
        with self.assertRaises(MissionError):
            _mission(self.mission, self.root)
        self.mission.write_text('{"schema_version":1,"probes":["../../secrets"]}')
        with self.assertRaises(MissionError):
            _mission(self.mission, self.root)

    def test_guest_request_does_not_accept_arbitrary_command(self):
        with self.assertRaises(ValueError):
            guest_handle({"version": 1, "method": "shell.run", "probe": "event_auto_reset"},
                         self.root, self.mission)
        with self.assertRaises(ValueError):
            guest_handle({"version": 1, "method": "probe.run", "probe": "event_auto_reset",
                          "argv": ["cmd.exe"]}, self.root, self.mission)

    def test_lab_plan_only_returns_preflight_without_profile(self):
        with patch("pathlib.Path.home", return_value=self.root):
            result = plan_mission(self.root, "mission.json")
        self.assertFalse(result["ready"])
        self.assertNotIn("argv", result)


if __name__ == "__main__":
    unittest.main()
