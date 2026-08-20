from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kitt.domain.entities import ModelProfile
from kitt.llm.auth import CredentialStore, ProviderAuthService
from kitt.llm.client import LLMClient
from kitt.llm.domain import (
    ModelDiscoveryResult,
    ProviderAuthError,
    ProviderDiscoveryStatus,
)
from kitt.llm.endpoint_security import (
    ProviderEndpointTrustStore,
    normalize_endpoint_origin,
    resolve_endpoint_credential,
)
from kitt.llm.registry import ProviderRegistry


class _RecordingAdapter:
    def __init__(self):
        self.requests = []
        self.list_calls = []
        self.health_calls = []

    def stream(self, request):
        self.requests.append(request)
        yield "ok"

    def list_models(self, base_url=None, api_key=None, timeout=5.0):
        self.list_calls.append((base_url, api_key, timeout))
        return ModelDiscoveryResult(
            status=ProviderDiscoveryStatus.SUCCESS,
            models=[],
        )

    def health(self, base_url=None, api_key=None, timeout=5.0):
        from kitt.llm.domain import ProviderHealth
        self.health_calls.append((base_url, api_key, timeout))
        return ProviderHealth(status="healthy", authenticated=bool(api_key))


class _StubRegistry:
    def __init__(self, auth_service, endpoint_policy, adapter):
        self.auth_service = auth_service
        self.endpoint_policy = endpoint_policy
        self.adapter = adapter

    def get_adapter_for_protocol(self, protocol):
        return self.adapter

    def get_adapter_for_provider(self, provider):
        return self.adapter


class _SpyAuthService(ProviderAuthService):
    def __init__(self, store):
        super().__init__(store)
        self.resolve_calls = 0

    def resolve(self, credential_ref, provider_id=None):
        self.resolve_calls += 1
        return super().resolve(credential_ref, provider_id)


class TestProviderEndpointCredentialEgress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.auth_file = self.root / "auth" / "auth.json"
        self.trust_file = self.root / "trust" / "provider-endpoints.json"
        self.auth = _SpyAuthService(
            CredentialStore(auth_file=str(self.auth_file))
        )
        self.policy = ProviderEndpointTrustStore(self.trust_file)

    def tearDown(self):
        self.tmp.cleanup()

    def _client(self, profile):
        adapter = _RecordingAdapter()
        registry = _StubRegistry(
            self.auth,
            self.policy,
            adapter,
        )
        client = LLMClient(
            profile,
            registry=registry,
            auth_service=self.auth,
            endpoint_policy=self.policy,
        )
        return client, adapter

    def test_untrusted_workspace_endpoint_is_rejected_before_auth_resolution(self):
        self.auth.login("openai", "sk-global-openai")
        baseline_calls = self.auth.resolve_calls
        profile = ModelProfile(
            backend="openai",
            model="gpt-x",
            base_url="https://attacker.example/v1",
            credential_ref="auth:openai",
            protocol="openai-chat-completions",
        )
        client, adapter = self._client(profile)
        try:
            with self.assertRaises(ProviderAuthError):
                list(client.chat_stream([{"role": "user", "content": "hello"}]))
        finally:
            client.close()

        self.assertEqual(adapter.requests, [])
        self.assertEqual(self.auth.resolve_calls, baseline_calls)

    def test_implicit_provider_credential_is_also_blocked_before_resolution(self):
        self.auth.login("openai", "sk-global-openai")
        baseline_calls = self.auth.resolve_calls
        profile = ModelProfile(
            backend="openai",
            model="gpt-x",
            base_url="https://attacker.example",
            protocol="openai-chat-completions",
        )
        client, adapter = self._client(profile)
        try:
            with self.assertRaises(ProviderAuthError):
                list(client.chat_stream([{"role": "user", "content": "hello"}]))
        finally:
            client.close()

        self.assertEqual(adapter.requests, [])
        self.assertEqual(self.auth.resolve_calls, baseline_calls)

    def test_official_openai_origin_receives_openai_credential(self):
        self.auth.login("openai", "sk-global-openai")
        profile = ModelProfile(
            backend="openai",
            model="gpt-x",
            base_url="https://api.openai.com/v1",
            credential_ref="auth:openai",
            protocol="openai-chat-completions",
        )
        client, adapter = self._client(profile)
        try:
            self.assertEqual(
                list(client.chat_stream([{"role": "user", "content": "hello"}])),
                ["ok"],
            )
        finally:
            client.close()

        self.assertEqual(len(adapter.requests), 1)
        self.assertEqual(adapter.requests[0].api_key, "sk-global-openai")

    def test_trusted_custom_endpoint_uses_only_matching_custom_identity(self):
        self.auth.login("my-proxy", "proxy-secret")
        self.policy.trust("my-proxy", "https://proxy.example/v1")
        resolved = resolve_endpoint_credential(
            self.auth,
            "my-proxy",
            "https://proxy.example/v1/chat",
            credential_ref="auth:my-proxy",
            policy=self.policy,
        )
        self.assertEqual(resolved, "proxy-secret")

        self.auth.login("openai", "openai-secret")
        with self.assertRaises(ProviderAuthError):
            resolve_endpoint_credential(
                self.auth,
                "my-proxy",
                "https://proxy.example/v1",
                credential_ref="auth:openai",
                policy=self.policy,
            )

    def test_env_reference_must_match_provider_identity(self):
        self.policy.trust("my-proxy", "https://proxy.example")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-secret"}):
            with self.assertRaises(ProviderAuthError):
                resolve_endpoint_credential(
                    self.auth,
                    "my-proxy",
                    "https://proxy.example",
                    credential_ref="env:OPENAI_API_KEY",
                    policy=self.policy,
                )

    def test_oauth_token_never_goes_to_non_official_trusted_origin(self):
        class _Token:
            access_token = "oauth-access"
            refresh_token = "oauth-refresh"
            expires_at = None
            token_type = "Bearer"
            scope = "openid"

        self.auth.login_oauth("openai", _Token())
        self.policy.trust("openai", "https://proxy.example")
        with self.assertRaises(ProviderAuthError):
            resolve_endpoint_credential(
                self.auth,
                "openai",
                "https://proxy.example/v1",
                credential_ref="auth:openai",
                policy=self.policy,
            )


class TestProviderEndpointRegistryBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.auth = ProviderAuthService(
            CredentialStore(auth_file=str(root / "auth" / "auth.json"))
        )
        self.policy = ProviderEndpointTrustStore(
            root / "trust" / "provider-endpoints.json"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_custom_provider_cannot_shadow_builtin_openai(self):
        registry = ProviderRegistry(
            auth_service=self.auth,
            endpoint_policy=self.policy,
        )
        with self.assertRaises(ValueError):
            registry.register_custom_provider(
                "openai",
                "fake-openai",
                base_url="https://attacker.example",
            )

    def test_model_discovery_does_not_call_adapter_for_untrusted_endpoint(self):
        registry = ProviderRegistry(
            auth_service=self.auth,
            endpoint_policy=self.policy,
        )
        adapter = _RecordingAdapter()
        registry._adapters_by_protocol["openai-chat-completions"] = adapter
        self.auth.login("openai", "secret")

        result = registry.discover_runtime_models(
            "openai",
            base_url="https://attacker.example/v1",
        )
        self.assertEqual(result.status, ProviderDiscoveryStatus.AUTH_REQUIRED)
        self.assertEqual(adapter.list_calls, [])

    def test_trusted_custom_discovery_uses_custom_credential(self):
        registry = ProviderRegistry(
            auth_service=self.auth,
            endpoint_policy=self.policy,
        )
        adapter = _RecordingAdapter()
        registry._adapters_by_protocol["openai-chat-completions"] = adapter
        registry.register_custom_provider(
            "my-proxy",
            "My Proxy",
            protocol="openai-chat-completions",
            base_url="https://proxy.example/v1",
        )
        self.auth.login("my-proxy", "proxy-secret")
        self.policy.trust("my-proxy", "https://proxy.example")

        registry.discover_runtime_models("my-proxy")
        self.assertEqual(len(adapter.list_calls), 1)
        self.assertEqual(adapter.list_calls[0][1], "proxy-secret")


@unittest.skipIf(os.name == "nt", "POSIX permission semantics")
class TestProviderEndpointTrustStoreSecurity(unittest.TestCase):
    def test_state_is_private_and_origin_normalization_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "provider-endpoints.json"
            policy = ProviderEndpointTrustStore(path)
            origin = policy.trust(
                "my-proxy",
                "https://Proxy.Example:443/v1?x=1",
            )
            self.assertEqual(origin, "https://proxy.example:443")
            self.assertTrue(
                policy.is_trusted(
                    "my-proxy",
                    "https://proxy.example/other",
                )
            )
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode),
                0o600,
            )
            self.assertEqual(
                stat.S_IMODE(path.parent.stat().st_mode),
                0o700,
            )

    def test_trust_file_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text('{"version":1,"providers":{}}', encoding="utf-8")
            os.chmod(target, 0o600)
            link = root / "trust.json"
            link.symlink_to(target)
            policy = ProviderEndpointTrustStore(link)
            with self.assertRaises(PermissionError):
                policy.trust("my-proxy", "https://proxy.example")


if __name__ == "__main__":
    unittest.main()
