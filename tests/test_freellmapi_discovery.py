"""FreeLLMAPI discovery uses the gateway catalog and its own credentials."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kitt.cli.commands import handle_models_command
from kitt.llm.catalog import ProviderCatalogService
from kitt.llm.domain import ProviderDiscoveryStatus
from kitt.llm.endpoint_security import ProviderEndpointTrustStore
from kitt.llm.registry import ProviderRegistry
from kitt.router.model_selector import fetch_provider_models
from kitt.ui.model_service import _provider_defaults
from kitt.ui.provider_catalog_state import ProviderCatalogBehavior


class FreeLLMAPIDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.auth = MagicMock()
        self.auth.resolve.return_value = "gateway-test-key"
        self.policy = ProviderEndpointTrustStore(root / "trust" / "endpoints.json")
        self.registry = ProviderRegistry(
            catalog=ProviderCatalogService(cache_dir=str(root / "cache")),
            auth_service=self.auth,
            endpoint_policy=self.policy,
        )

    def response(self):
        return io.BytesIO(json.dumps({"object": "list", "data": [
            {"id": "auto"}, {"id": "vendor/coder:free"},
        ]}).encode())

    def test_cli_lists_authenticated_gateway_models(self):
        output = io.StringIO()
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", return_value=self.response()) as send:
            with contextlib.redirect_stdout(output):
                code = handle_models_command("freellmapi", registry=self.registry)
        self.assertEqual(code, 0)
        self.assertIn("freellmapi/vendor/coder:free", output.getvalue())
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:3001/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer gateway-test-key")
        self.assertNotIn("gateway-test-key", output.getvalue())
        self.auth.resolve.assert_called_once_with(None, provider_id="freellmapi")

    def test_discovery_retains_provider_identity(self):
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", return_value=self.response()):
            result = self.registry.discover_runtime_models("freellmapi")
        self.assertEqual(result.status, ProviderDiscoveryStatus.SUCCESS)
        self.assertEqual({model.provider_id for model in result.models}, {"freellmapi"})

    def test_ui_discovery_and_defaults(self):
        self.assertIn("freellmapi", ProviderCatalogBehavior.default_providers)
        self.assertEqual(_provider_defaults("freellmapi")[0], "http://127.0.0.1:3001")
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", return_value=self.response()):
            models = fetch_provider_models("freellmapi", registry=self.registry)
        self.assertEqual(models, ["auto", "vendor/coder:free"])

    def test_custom_endpoint_requires_explicit_trust_even_on_loopback(self):
        with patch("kitt.llm.providers.openai_chat.secure_urlopen") as send:
            result = self.registry.discover_runtime_models("freellmapi", "http://127.0.0.1:9999")
        self.assertEqual(result.status, ProviderDiscoveryStatus.AUTH_REQUIRED)
        self.auth.resolve.assert_not_called()
        send.assert_not_called()

    def test_trusted_custom_endpoint_with_v1_uses_saved_credentials(self):
        endpoint = "http://127.0.0.1:9999/v1"
        self.policy.trust("freellmapi", endpoint)
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", return_value=self.response()) as send:
            result = self.registry.discover_runtime_models("freellmapi", endpoint)
        self.assertEqual(result.status, ProviderDiscoveryStatus.SUCCESS)
        self.assertEqual(send.call_args.args[0].full_url, endpoint + "/models")
        self.assertEqual(send.call_args.args[0].get_header("Authorization"), "Bearer gateway-test-key")

    def test_cli_uses_workspace_endpoint(self):
        from kitt.domain.entities import ModelProfile
        from kitt.router.router import TaskRouter
        workspace = Path(self.directory.name) / "workspace"
        workspace.mkdir()
        endpoint = "http://127.0.0.1:9999/v1"
        router = TaskRouter(str(workspace))
        router.config.profiles["freellmapi"] = ModelProfile(
            backend="freellmapi", model="auto", base_url=endpoint,
        )
        router.save_config(str(workspace))
        self.policy.trust("freellmapi", endpoint)
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", return_value=self.response()) as send:
            with contextlib.redirect_stdout(io.StringIO()):
                code = handle_models_command("freellmapi", registry=self.registry, root_dir=str(workspace))
        self.assertEqual(code, 0)
        self.assertEqual(send.call_args.args[0].full_url, endpoint + "/models")
