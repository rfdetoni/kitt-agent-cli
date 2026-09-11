from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.update_check import (
    build_update_command,
    find_outdated_components,
    notify_if_update_available,
)


class UpdateCheckTests(unittest.TestCase):
    def test_detects_only_installed_components_that_differ_from_lock(self) -> None:
        state = {
            "repositories": {
                "rfdetoni/kitt-agent-cli": "1" * 40,
                "rfdetoni/kitt-reverse-proxy": "2" * 40,
            }
        }
        lock = {
            "components": {
                "rfdetoni/kitt-agent-cli": "3" * 40,
                "rfdetoni/kitt-reverse-proxy": "2" * 40,
                "rfdetoni/kitt-memory": "4" * 40,
            }
        }
        self.assertEqual(
            find_outdated_components(state, lock),
            ("rfdetoni/kitt-agent-cli",),
        )

    def test_posix_update_command_preserves_install_shape(self) -> None:
        state = {
            "requested_modules": ["agent-cli"],
            "launchers": ["/home/test/.local/bin/kitt"],
            "with_ai_workers": True,
            "portable": False,
        }
        command = build_update_command(
            Path("/home/test/.local/share/kitt/installed-state.json"),
            state,
            fallback_module="agent-cli",
            platform_name="linux",
        )
        self.assertIn("install.sh | sh -s --", command)
        self.assertIn("--modules agent-cli", command)
        self.assertIn("--root /home/test/.local/share/kitt", command)
        self.assertIn("--bin-dir /home/test/.local/bin", command)
        self.assertIn("--with-ai-workers", command)

    def test_windows_update_command_is_manual_powershell(self) -> None:
        state = {
            "requested_modules": ["reverse-proxy"],
            "launchers": [r"C:\Users\test\AppData\Local\KITT\bin\kitt-reverse-proxy.cmd"],
        }
        command = build_update_command(
            Path(r"C:\Users\test\AppData\Local\KITT\installed-state.json"),
            state,
            fallback_module="reverse-proxy",
            platform_name="windows",
        )
        self.assertIn("Invoke-RestMethod", command)
        self.assertIn("--modules 'reverse-proxy'", command)
        self.assertIn("--root 'C:\\Users\\test\\AppData\\Local\\KITT'", command)

    def test_notification_is_fail_open_when_network_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "installed-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "repositories": {
                            "rfdetoni/kitt-agent-cli": "1" * 40,
                        },
                        "requested_modules": ["agent-cli"],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with patch("kitt.update_check._fetch_lock", side_effect=OSError("offline")):
                notified = notify_if_update_available(
                    component="agent-cli",
                    state_path=state_path,
                    stream=output,
                )
            self.assertFalse(notified)
            self.assertEqual(output.getvalue(), "")

    def test_notification_prints_manual_command_when_update_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_path = root / "installed-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "repositories": {
                            "rfdetoni/kitt-agent-cli": "1" * 40,
                        },
                        "requested_modules": ["agent-cli"],
                        "launchers": [str(root / "bin" / "kitt")],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with patch(
                "kitt.update_check._fetch_lock",
                return_value={
                    "components": {
                        "rfdetoni/kitt-agent-cli": "2" * 40,
                    }
                },
            ):
                notified = notify_if_update_available(
                    component="agent-cli",
                    state_path=state_path,
                    stream=output,
                )
            self.assertTrue(notified)
            rendered = output.getvalue()
            self.assertIn("Update available", rendered)
            self.assertIn("Update manually with:", rendered)
            self.assertIn("--modules agent-cli", rendered)


if __name__ == "__main__":
    unittest.main()
