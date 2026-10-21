"""Regression tests for bounded setup inspection and explicit Strix delegation."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from kitt.setup_advisor import recommend
from kitt.strix_integration import scan_strix, strix_status


class SetupAdvisorTests(unittest.TestCase):
    def test_detects_java_frontend_without_exec_or_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pom.xml").write_text("<artifactId>spring-boot-starter</artifactId>")
            (root / "package.json").write_text(json.dumps({
                "dependencies": {"@angular/core": "20.0.0"}
            }))
            result = recommend(root)
            self.assertIn("spring-boot", result["signals"])
            self.assertIn("frontend", result["signals"])
            self.assertTrue(result["read_only"])
            self.assertTrue(any(item["name"] == "kitt-memoryd" for item in result["recommendations"]))
            self.assertFalse((root / ".kitt").exists())

    def test_ignores_symlinked_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").symlink_to(root / "not-present.json")
            self.assertNotIn("node", recommend(root)["signals"])


class StrixTests(unittest.TestCase):
    def test_missing_authorization_never_executes(self):
        with tempfile.TemporaryDirectory() as tmp, patch("subprocess.run") as runner:
            with self.assertRaises(PermissionError):
                scan_strix(workspace=tmp, target=".", authorized=False)
            runner.assert_not_called()

    def test_installed_binary_executes_with_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value="/usr/bin/strix"), patch(
                "subprocess.run", return_value=Mock(returncode=0)
            ) as runner:
                self.assertEqual(
                    scan_strix(workspace=tmp, target=".", authorized=True), 0
                )
                self.assertEqual(runner.call_args.args[0][:2], ["/usr/bin/strix", "--target"])
                self.assertIs(runner.call_args.kwargs["shell"], False)

    def test_absolute_workspace_path_is_accepted_cross_platform(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value="strix"), patch(
                "subprocess.run", return_value=Mock(returncode=0)
            ):
                self.assertEqual(
                    scan_strix(
                        workspace=tmp, target=str(Path(tmp).resolve()),
                        authorized=True,
                    ),
                    0,
                )

    def test_escaping_workspace_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            with patch("shutil.which", return_value="/usr/bin/strix"):
                with self.assertRaises(PermissionError):
                    scan_strix(workspace=workspace, target="..", authorized=True)

    def test_url_credentials_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("shutil.which", return_value="/usr/bin/strix"):
                with self.assertRaises(ValueError):
                    scan_strix(
                        workspace=tmp, target="https://user:secret@example.com",
                        authorized=True,
                    )



class FreeLLMAPIIntegrationTests(unittest.TestCase):
    def test_registration_reuses_native_adapter_and_preserves_routing(self):
        from kitt.optional_providers import register_freellmapi
        from kitt.router.router import TaskRouter

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "kitt.optional_providers.ProviderEndpointTrustStore.trust"
            ) as trust:
                result = register_freellmapi(tmp, model="auto")
                self.assertEqual(result["active_for_code"], False)
                trust.assert_called_once_with("freellmapi", "http://127.0.0.1:3001")
            cfg = json.loads((Path(tmp) / ".kitt-router.json").read_text())
            profile = cfg["profiles"]["freellmapi"]
            self.assertEqual(profile["protocol"], "openai-compatible")
            self.assertEqual(profile["credential_ref"], "env:FREELLMAPI_API_KEY")
            self.assertNotIn("SECRET", json.dumps(cfg))
            self.assertEqual(TaskRouter(tmp).config.routing["code-edit"], "execute")

    def test_remote_requires_explicit_opt_in_and_https(self):
        from kitt.optional_providers import register_freellmapi

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionError):
                register_freellmapi(tmp, base_url="https://gateway.example")
            with self.assertRaises(ValueError):
                register_freellmapi(
                    tmp, base_url="http://gateway.example", allow_remote=True
                )
            self.assertFalse((Path(tmp) / ".kitt-router.json").exists())

    def test_activation_changes_only_code_routes(self):
        from kitt.optional_providers import register_freellmapi
        from kitt.router.router import TaskRouter

        with tempfile.TemporaryDirectory() as tmp:
            with patch("kitt.optional_providers.ProviderEndpointTrustStore.trust"):
                register_freellmapi(tmp, activate=True)
            routes = TaskRouter(tmp).config.routing
            self.assertEqual(routes["code-edit"], "freellmapi")
            self.assertEqual(routes["code-generation"], "freellmapi")
            self.assertEqual(routes["summarize"], "context")


class CLISurfaceTests(unittest.TestCase):
    def test_setup_cli_does_not_check_for_updates(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from kitt.cli.main import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            with patch("kitt.cli.main.notify_if_update_available") as checker:
                with redirect_stdout(StringIO()) as captured:
                    self.assertEqual(
                        cli_main(["setup", "recommend", "--root", tmp, "--json"]), 0
                    )
                checker.assert_not_called()
                self.assertTrue(json.loads(captured.getvalue())["read_only"])

    def test_cli_registration_writes_only_referenced_key(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from kitt.cli.main import main as cli_main

        with tempfile.TemporaryDirectory() as tmp:
            with patch("kitt.cli.main.notify_if_update_available"):
                with patch("kitt.optional_providers.ProviderEndpointTrustStore.trust"):
                    with redirect_stdout(StringIO()):
                        status = cli_main([
                            "integrations", "configure-freellmapi", "--root", tmp
                        ])
            self.assertEqual(status, 0)
            config = (Path(tmp) / ".kitt-router.json").read_text()
            self.assertIn("env:FREELLMAPI_API_KEY", config)
            self.assertNotIn("FREELLAPI_SECRET", config)

    def test_new_commands_parse(self):
        from kitt.cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["setup", "recommend", "--json"])
        self.assertEqual((args.subcommand, args.setup_action), ("setup", "recommend"))
        self.assertTrue(args.json_output)
        args = parser.parse_args([
            "security", "strix", "scan", "--target", ".", "--authorized"
        ])
        self.assertEqual(args.strix_action, "scan")
        self.assertTrue(args.authorized)
        args = parser.parse_args([
            "integrations", "configure-freellmapi", "--activate"
        ])
        self.assertTrue(args.activate)


if __name__ == "__main__":
    unittest.main()
