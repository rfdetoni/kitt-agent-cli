"""Complete OAuth 2.0 subsystem with PKCE loopback, Device Code flow, and token refresh."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, Optional, Tuple


def open_browser_url(url: str) -> bool:
    """Robust URL opener for Linux/macOS/Windows desktop sessions."""
    import shutil
    import subprocess
    import webbrowser
    if shutil.which("xdg-open"):
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:
            pass
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def validate_state(expected: str, received: str) -> bool:
    """Constant-time comparison to prevent OAuth CSRF / state injection attacks."""
    if not expected or not received:
        return False
    return secrets.compare_digest(expected, received)


@dataclass(frozen=True)
class AuthMethodDescriptor:
    id: str
    kind: str  # "oauth_browser", "device_code", "api_key", "env", "none", "custom"
    label: str
    recommended: bool = False
    interactive: bool = True


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[float] = None
    token_type: str = "Bearer"
    scope: str = ""
    provider_id: str = ""
    account_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() >= (self.expires_at - 60)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
            "scope": self.scope,
            "provider_id": self.provider_id,
            "account_id": self.account_id,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> OAuthToken:
        return cls(
            access_token=data.get("access_token") or data.get("value_ref", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            token_type=data.get("token_type", "Bearer"),
            scope=data.get("scope", ""),
            provider_id=data.get("provider_id", ""),
            account_id=data.get("account_id"),
            extra=data.get("extra", {}),
        )


@dataclass(frozen=True)
class OAuthProviderConfig:
    provider_id: str
    client_id: str
    auth_url: Optional[str] = None
    token_url: Optional[str] = None
    device_code_url: Optional[str] = None
    scopes: Tuple[str, ...] = ()
    use_pkce: bool = True
    client_secret: Optional[str] = None
    flow_type: str = "browser"  # "browser" | "device_code"


# Default OAuth configs matching OpenCode parity
OAUTH_PROVIDERS: Dict[str, OAuthProviderConfig] = {
    "github": OAuthProviderConfig(
        provider_id="github",
        client_id="Iv1.b507a08c87ecfe81",
        device_code_url="https://github.com/login/device/code",
        token_url="https://github.com/login/oauth/access_token",
        scopes=("read:user", "user:email"),
        flow_type="device_code",
    ),
    "github-copilot": OAuthProviderConfig(
        provider_id="github-copilot",
        client_id="Iv1.b507a08c87ecfe81",
        device_code_url="https://github.com/login/device/code",
        token_url="https://github.com/login/oauth/access_token",
        scopes=("read:user", "copilot"),
        flow_type="device_code",
    ),
    "google": OAuthProviderConfig(
        provider_id="google",
        client_id="845233155700-47522502f6k04332.apps.googleusercontent.com",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=("https://www.googleapis.com/auth/generative-language", "openid", "email"),
        use_pkce=True,
        flow_type="browser",
    ),
    "gemini": OAuthProviderConfig(
        provider_id="gemini",
        client_id="845233155700-47522502f6k04332.apps.googleusercontent.com",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=("https://www.googleapis.com/auth/generative-language", "openid", "email"),
        use_pkce=True,
        flow_type="browser",
    ),
    "openai": OAuthProviderConfig(
        provider_id="openai",
        client_id="app-kitt-cli",
        auth_url="https://auth.openai.com/authorize",
        token_url="https://auth.openai.com/oauth/token",
        scopes=("openid", "profile", "email", "model.request"),
        use_pkce=True,
        flow_type="browser",
    ),
    "anthropic": OAuthProviderConfig(
        provider_id="anthropic",
        client_id="app-kitt-anthropic",
        auth_url="https://console.anthropic.com/oauth/authorize",
        token_url="https://console.anthropic.com/oauth/token",
        scopes=("org:read", "model:read"),
        use_pkce=True,
        flow_type="browser",
    ),
}


class PKCE:
    """Helper for generating PKCE code_verifier and code_challenge (RFC 7636)."""

    @staticmethod
    def generate_verifier(length: int = 64) -> str:
        return secrets.token_urlsafe(length)[:length]

    @staticmethod
    def generate_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    @staticmethod
    def generate_state() -> str:
        return secrets.token_hex(16)


@dataclass
class DeviceCodeChallenge:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class LocalCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler capturing OAuth loopback redirect callback."""

    def log_message(self, format, *args):
        pass  # Suppress default server stderr logging

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            error = params.get("error", [None])[0]

            self.server.callback_result = {
                "code": code,
                "state": state,
                "error": error,
            }

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

            if error:
                html = (
                    "<html><body style='font-family:sans-serif;text-align:center;padding:40px;background:#1e1e1e;color:#fff;'>"
                    "<h2 style='color:#f87171;'>✖ Falha na Autenticação</h2>"
                    f"<p>Erro retornado pelo provedor: <code>{error}</code></p>"
                    "<p>Você pode fechar esta janela e tentar novamente no KITT.</p>"
                    "</body></html>"
                )
            else:
                html = (
                    "<html><body style='font-family:sans-serif;text-align:center;padding:40px;background:#1e1e1e;color:#fff;'>"
                    "<h2 style='color:#4ade80;'>✔ Autenticado com Sucesso!</h2>"
                    "<p>Sua conta foi conectada ao KITT CLI com segurança.</p>"
                    "<p>Você já pode fechar esta aba do navegador e voltar ao terminal.</p>"
                    "</body></html>"
                )
            self.wfile.write(html.encode("utf-8"))
            # Signal completion
            if hasattr(self.server, "completion_event"):
                self.server.completion_event.set()
        else:
            self.send_response(404)
            self.end_headers()


class LocalCallbackServer:
    """Ephemeral loopback HTTP server on 127.0.0.1."""

    def __init__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), LocalCallbackHandler)
        self.port = self.httpd.server_port
        self.callback_result: Optional[Dict[str, Any]] = None
        self.completion_event = threading.Event()
        self.httpd.callback_result = None
        self.httpd.completion_event = self.completion_event
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self.completion_event.is_set():
            self.httpd.handle_request()

    def wait_for_callback(self, timeout: float = 120.0) -> Optional[Dict[str, Any]]:
        finished = self.completion_event.wait(timeout=timeout)
        if finished:
            return self.httpd.callback_result
        return None

    def stop(self) -> None:
        self.completion_event.set()
        try:
            self.httpd.server_close()
        except Exception:
            pass


class OAuthManager:
    """Manages PKCE browser flows, Device Code polling, and token refreshing."""

    def __init__(self, configs: Optional[Dict[str, OAuthProviderConfig]] = None):
        self.configs = configs or dict(OAUTH_PROVIDERS)

    def is_oauth_supported(self, provider_id: str) -> bool:
        pid = (provider_id or "").strip().lower()
        return pid in self.configs

    def get_config(self, provider_id: str) -> Optional[OAuthProviderConfig]:
        pid = (provider_id or "").strip().lower()
        return self.configs.get(pid)

    # --- Browser Flow with PKCE ---

    def start_browser_flow(
        self,
        provider_id: str,
        open_browser: bool = True,
    ) -> Tuple[str, LocalCallbackServer, str, str]:
        """Sets up local callback server and returns (auth_url, server, code_verifier, state)."""
        cfg = self.get_config(provider_id)
        if not cfg or not cfg.auth_url:
            raise ValueError(f"Provider '{provider_id}' does not support browser OAuth flow")

        server = LocalCallbackServer()
        server.start()

        redirect_uri = f"http://127.0.0.1:{server.port}/callback"
        state = PKCE.generate_state()
        verifier = PKCE.generate_verifier()
        challenge = PKCE.generate_challenge(verifier)

        params = {
            "client_id": cfg.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(cfg.scopes),
            "state": state,
        }
        if cfg.use_pkce:
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"

        auth_url = f"{cfg.auth_url}?{urllib.parse.urlencode(params)}"
        if open_browser:
            open_browser_url(auth_url)
        return auth_url, server, verifier, state

    def exchange_code_for_token(
        self,
        provider_id: str,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> OAuthToken:
        """Exchanges authorization code for access and refresh tokens."""
        cfg = self.get_config(provider_id)
        if not cfg or not cfg.token_url:
            raise ValueError(f"Provider '{provider_id}' has no token endpoint configured")

        data = {
            "grant_type": "authorization_code",
            "client_id": cfg.client_id,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        if cfg.client_secret:
            data["client_secret"] = cfg.client_secret

        encoded = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            cfg.token_url,
            data=encoded,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw)

        access_token = body.get("access_token")
        if not access_token:
            raise ValueError(f"No access_token in token response: {body}")

        expires_in = body.get("expires_in")
        expires_at = (time.time() + float(expires_in)) if expires_in else None

        return OAuthToken(
            access_token=access_token,
            refresh_token=body.get("refresh_token"),
            expires_at=expires_at,
            token_type=body.get("token_type", "Bearer"),
            scope=body.get("scope", " ".join(cfg.scopes)),
            provider_id=provider_id,
            extra=body,
        )

    # --- Device Code Flow (GitHub Copilot, etc.) ---

    def start_device_code_flow(self, provider_id: str) -> DeviceCodeChallenge:
        cfg = self.get_config(provider_id)
        if not cfg or not cfg.device_code_url:
            raise ValueError(f"Provider '{provider_id}' does not support device code OAuth flow")

        data = {
            "client_id": cfg.client_id,
            "scope": " ".join(cfg.scopes),
        }
        req = urllib.request.Request(
            cfg.device_code_url,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        return DeviceCodeChallenge(
            device_code=body["device_code"],
            user_code=body["user_code"],
            verification_uri=body["verification_uri"],
            expires_in=int(body.get("expires_in", 900)),
            interval=int(body.get("interval", 5)),
        )

    def poll_device_code_token(
        self,
        provider_id: str,
        challenge: DeviceCodeChallenge,
        timeout: float = 300.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> OAuthToken:
        """Polls token endpoint until device code is authorized by user."""
        cfg = self.get_config(provider_id)
        if not cfg or not cfg.token_url:
            raise ValueError(f"Provider '{provider_id}' has no token endpoint configured")

        deadline = time.time() + min(timeout, challenge.expires_in)
        interval = max(3, challenge.interval)

        data = {
            "client_id": cfg.client_id,
            "device_code": challenge.device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }

        while time.time() < deadline:
            if cancel_event and cancel_event.is_set():
                raise TimeoutError("Device code authentication was cancelled")

            req = urllib.request.Request(
                cfg.token_url,
                data=urllib.parse.urlencode(data).encode("utf-8"),
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    if "access_token" in body:
                        expires_in = body.get("expires_in")
                        expires_at = (time.time() + float(expires_in)) if expires_in else None
                        return OAuthToken(
                            access_token=body["access_token"],
                            refresh_token=body.get("refresh_token"),
                            expires_at=expires_at,
                            token_type=body.get("token_type", "Bearer"),
                            scope=body.get("scope", " ".join(cfg.scopes)),
                            provider_id=provider_id,
                            extra=body,
                        )
                    err = body.get("error")
                    if err == "authorization_pending":
                        pass
                    elif err == "slow_down":
                        interval += 5
                    elif err in ("expired_token", "access_denied"):
                        raise ValueError(f"Device authentication error: {err}")
            except Exception as exc:
                if "authorization_pending" not in str(exc).lower():
                    pass

            time.sleep(interval)

        raise TimeoutError("Device code authentication timed out")

    # --- Token Refresh ---

    def refresh_token(self, provider_id: str, refresh_token: str) -> OAuthToken:
        """Refreshes expired access token using refresh_token."""
        cfg = self.get_config(provider_id)
        if not cfg or not cfg.token_url:
            raise ValueError(f"Provider '{provider_id}' has no token endpoint configured for refresh")

        data = {
            "grant_type": "refresh_token",
            "client_id": cfg.client_id,
            "refresh_token": refresh_token,
        }
        if cfg.client_secret:
            data["client_secret"] = cfg.client_secret

        req = urllib.request.Request(
            cfg.token_url,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))

        access_token = body.get("access_token")
        if not access_token:
            raise ValueError(f"Failed to refresh token: {body}")

        expires_in = body.get("expires_in")
        expires_at = (time.time() + float(expires_in)) if expires_in else None

        return OAuthToken(
            access_token=access_token,
            refresh_token=body.get("refresh_token") or refresh_token,
            expires_at=expires_at,
            token_type=body.get("token_type", "Bearer"),
            scope=body.get("scope", " ".join(cfg.scopes)),
            provider_id=provider_id,
            extra=body,
        )
