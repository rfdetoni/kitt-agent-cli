"""Unit tests for OAuth 2.0 PKCE, Device Code flow, and token refresh subsystem."""
import json
import time
import unittest
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

from kitt.llm.auth import CredentialStore, ProviderAuthService
from kitt.llm.oauth import (
    LocalCallbackServer,
    OAuthManager,
    OAuthProviderConfig,
    OAuthToken,
    PKCE,
    DeviceCodeChallenge,
)


class TestOAuthFlow(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = "/tmp/kitt_oauth_test"
        self.store = CredentialStore(auth_file=f"{self.tmp_dir}/auth.json")
        self.auth_service = ProviderAuthService(store=self.store)
        self.manager = OAuthManager()

    def test_pkce_generation(self):
        verifier = PKCE.generate_verifier()
        self.assertTrue(len(verifier) >= 43)
        challenge = PKCE.generate_challenge(verifier)
        self.assertTrue(len(challenge) > 0)
        self.assertNotIn("=", challenge)
        self.assertNotIn("+", challenge)
        self.assertNotIn("/", challenge)

    def test_oauth_token_expiration(self):
        token_fresh = OAuthToken(
            access_token="tok-123",
            expires_at=time.time() + 3600,
        )
        self.assertFalse(token_fresh.is_expired)

        token_expired = OAuthToken(
            access_token="tok-456",
            expires_at=time.time() - 10,
        )
        self.assertTrue(token_expired.is_expired)

    def test_local_callback_server_captures_auth_code(self):
        try:
            server = LocalCallbackServer()
            server.start()
        except PermissionError as exc:
            self.skipTest(f"Sandbox blocks local callback socket: {exc}")
        try:
            port = server.port
            url = f"http://127.0.0.1:{port}/callback?code=mock_code_xyz&state=mock_state_123"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as resp:
                self.assertEqual(resp.status, 200)
                html = resp.read().decode("utf-8")
                self.assertIn("Autenticado com Sucesso", html)

            result = server.wait_for_callback(timeout=2)
            self.assertIsNotNone(result)
            self.assertEqual(result.get("code"), "mock_code_xyz")
            self.assertEqual(result.get("state"), "mock_state_123")
        finally:
            server.stop()

    @patch("urllib.request.urlopen")
    def test_exchange_code_for_token(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps({
            "access_token": "sk-oauth-token-12345",
            "refresh_token": "rt-refresh-token-67890",
            "expires_in": 3600,
            "token_type": "Bearer",
        }).encode("utf-8")
        mock_urlopen.return_value = mock_resp

        token = self.manager.exchange_code_for_token(
            provider_id="openai",
            code="test_code",
            verifier="test_verifier",
            redirect_uri="http://127.0.0.1:8000/callback",
        )
        self.assertEqual(token.access_token, "sk-oauth-token-12345")
        self.assertEqual(token.refresh_token, "rt-refresh-token-67890")
        self.assertIsNotNone(token.expires_at)

    @patch("urllib.request.urlopen")
    def test_device_code_flow_and_poll(self, mock_urlopen):
        # Step 1: Device code request
        mock_resp1 = MagicMock()
        mock_resp1.__enter__.return_value = mock_resp1
        mock_resp1.read.return_value = json.dumps({
            "device_code": "dev-code-123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 1,
        }).encode("utf-8")

        # Step 2: Poll token endpoint
        mock_resp2 = MagicMock()
        mock_resp2.__enter__.return_value = mock_resp2
        mock_resp2.read.return_value = json.dumps({
            "access_token": "ghu_token_copilot_999",
            "token_type": "bearer",
            "scope": "read:user",
        }).encode("utf-8")

        mock_urlopen.side_effect = [mock_resp1, mock_resp2]

        challenge = self.manager.start_device_code_flow("github-copilot")
        self.assertEqual(challenge.user_code, "ABCD-1234")

        token = self.manager.poll_device_code_token("github-copilot", challenge, timeout=10)
        self.assertEqual(token.access_token, "ghu_token_copilot_999")

    @patch("kitt.llm.oauth.OAuthManager.refresh_token")
    def test_login_oauth_and_auto_refresh_on_resolve(self, mock_refresh):
        # Save an expired token with refresh token
        expired_token = OAuthToken(
            access_token="old_access_token",
            refresh_token="valid_refresh_token",
            expires_at=time.time() - 100,  # Expired
            provider_id="google",
        )
        self.auth_service.login_oauth("google", expired_token)

        # Mock refresh returning new token
        mock_refresh.return_value = OAuthToken(
            access_token="new_fresh_token_google",
            refresh_token="valid_refresh_token",
            expires_at=time.time() + 3600,
            provider_id="google",
        )

        resolved_secret = self.auth_service.resolve(None, "google")
        self.assertEqual(resolved_secret, "new_fresh_token_google")
        mock_refresh.assert_called_once_with("google", "valid_refresh_token")


if __name__ == "__main__":
    unittest.main()
