"""Provider authentication, credential store (~/.kitt/auth.json), and runtime resolution."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

PROVIDER_DEFAULT_ENV_VARS: Dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "google": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "xai": "XAI_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "cohere": "COHERE_API_KEY",
    "antigravity": "ANTIGRAVITY_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}

_SESSION_CREDENTIALS: Dict[str, str] = {}


@dataclass(frozen=True)
class ProviderAuthState:
    provider_id: str
    auth_type: str  # "api_key", "env", "session", "oauth"
    credential_ref: str
    is_valid: bool = True


class CredentialStore:
    """Secure global credential store at ~/.kitt/auth.json with 0600 POSIX permissions."""

    def __init__(self, auth_file: Optional[str] = None):
        self.auth_file = Path(auth_file or (Path.home() / ".kitt" / "auth.json")).resolve()

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not self.auth_file.exists():
            return {}
        try:
            return json.loads(self.auth_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_credential(
        self,
        provider_id: str,
        auth_type: str,
        value_or_ref: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Stores a credential reference or secret with strict 0600 permissions."""
        data = self.load()
        pid = provider_id.strip().lower()
        payload: Dict[str, Any] = {
            "type": auth_type,
            "value_ref": value_or_ref,
        }
        if extra:
            payload.update(extra)
        data[pid] = payload
        self._write_atomic(data)

    def remove_credential(self, provider_id: str) -> bool:
        data = self.load()
        pid = provider_id.strip().lower()
        if pid in data:
            del data[pid]
            self._write_atomic(data)
            return True
        return False

    def _write_atomic(self, data: Dict[str, Any]) -> None:
        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        # Create temp file with 0600 permissions
        fd, tmp = tempfile.mkstemp(prefix=".auth.", dir=str(self.auth_file.parent))
        try:
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            with os.fdopen(fd, "w", encoding="utf-8") as h:
                h.write(json.dumps(data, indent=2))
                h.flush()
                os.fsync(h.fileno())
            os.replace(tmp, self.auth_file)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class ProviderAuthService:
    """Handles authentication lifecycle, login, logout, and credential resolution."""

    def __init__(self, store: Optional[CredentialStore] = None):
        self.store = store or CredentialStore()

    @staticmethod
    def get_default_env_var(provider_id: str) -> str:
        pid = (provider_id or "").strip().lower()
        return PROVIDER_DEFAULT_ENV_VARS.get(pid, f"{pid.upper()}_API_KEY")

    def methods(self, provider_id: str) -> List[str]:
        pid = (provider_id or "").strip().lower()
        if pid in ("ollama", "lmstudio"):
            return ["none", "api_key"]
        from kitt.llm.oauth import OAUTH_PROVIDERS
        m = ["api_key", "env", "session"]
        if pid in OAUTH_PROVIDERS:
            m.insert(0, "oauth")
        return m

    def login_oauth(self, provider_id: str, token: Any) -> ProviderAuthState:
        pid = provider_id.strip().lower()
        self.store.save_credential(
            provider_id=pid,
            auth_type="oauth",
            value_or_ref=token.access_token,
            extra={
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at,
                "token_type": token.token_type,
                "scope": token.scope,
            },
        )
        return ProviderAuthState(
            provider_id=pid,
            auth_type="oauth",
            credential_ref=f"auth:{pid}",
            is_valid=True,
        )

    def login(self, provider_id: str, secret: str, method: str = "api_key") -> ProviderAuthState:
        pid = provider_id.strip().lower()
        if method == "session":
            _SESSION_CREDENTIALS[pid] = secret
            ref = f"session:{pid}"
        elif method == "env":
            ref = f"env:{secret}"
        else:
            ref = f"auth:{pid}"
            self.store.save_credential(pid, "api_key", secret)

        return ProviderAuthState(provider_id=pid, auth_type=method, credential_ref=ref, is_valid=True)

    def logout(self, provider_id: str) -> None:
        pid = provider_id.strip().lower()
        self.store.remove_credential(pid)
        _SESSION_CREDENTIALS.pop(pid, None)

    @staticmethod
    def get_env_value(env_var: str) -> Optional[str]:
        """Gets environment variable from os.environ or local .env file."""
        if not env_var:
            return None
        val = os.environ.get(env_var)
        if val:
            return val
        # Check .env in current working directory
        dotenv_file = Path.cwd() / ".env"
        if dotenv_file.exists():
            try:
                for line in dotenv_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k == env_var:
                            return v
            except Exception:
                pass
        return None

    def state(self, provider_id: str) -> ProviderAuthState:
        pid = (provider_id or "").strip().lower()
        if pid in ("ollama", "lmstudio"):
            return ProviderAuthState(provider_id=pid, auth_type="none", credential_ref="", is_valid=True)
        stored = self.store.load().get(pid)
        if stored:
            return ProviderAuthState(
                provider_id=pid,
                auth_type=stored.get("type", "api_key"),
                credential_ref=f"auth:{pid}",
                is_valid=True,
            )
        if pid in _SESSION_CREDENTIALS:
            return ProviderAuthState(
                provider_id=pid,
                auth_type="session",
                credential_ref=f"session:{pid}",
                is_valid=True,
            )
        env_var = self.get_default_env_var(pid)
        if self.get_env_value(env_var):
            return ProviderAuthState(
                provider_id=pid,
                auth_type="env",
                credential_ref=f"env:{env_var}",
                is_valid=True,
            )
        return ProviderAuthState(provider_id=pid, auth_type="missing", credential_ref="", is_valid=False)

    def authenticated(self) -> List[ProviderAuthState]:
        states: List[ProviderAuthState] = []
        stored = self.store.load()
        for pid, data in stored.items():
            states.append(
                ProviderAuthState(
                    provider_id=pid,
                    auth_type=data.get("type", "api_key"),
                    credential_ref=f"auth:{pid}",
                    is_valid=True,
                )
            )

        # Also check session credentials
        for pid in _SESSION_CREDENTIALS:
            if not any(s.provider_id == pid for s in states):
                states.append(
                    ProviderAuthState(
                        provider_id=pid,
                        auth_type="session",
                        credential_ref=f"session:{pid}",
                        is_valid=True,
                    )
                )

        # Also check active env variables
        for pid, env_var in PROVIDER_DEFAULT_ENV_VARS.items():
            if self.get_env_value(env_var) and not any(s.provider_id == pid for s in states):
                states.append(
                    ProviderAuthState(
                        provider_id=pid,
                        auth_type="env",
                        credential_ref=f"env:{env_var}",
                        is_valid=True,
                    )
                )

        return states

    def resolve(self, credential_ref: Optional[str], provider_id: Optional[str] = None) -> Optional[str]:
        """Resolves a credential reference (auth:..., env:..., session:...) into the actual secret."""
        if not credential_ref and provider_id:
            # Try default env var for provider
            env_var = self.get_default_env_var(provider_id)
            val = self.get_env_value(env_var)
            if val:
                return val
            # Try auth store
            stored = self.store.load().get(provider_id.strip().lower())
            if stored and stored.get("value_ref"):
                return self.resolve(f"auth:{provider_id.strip().lower()}", provider_id)
            # Try session
            return _SESSION_CREDENTIALS.get(provider_id.strip().lower())

        if not credential_ref:
            return None

        if credential_ref.startswith("auth:"):
            pid = credential_ref[5:].strip().lower()
            stored = self.store.load().get(pid)
            if stored:
                if stored.get("type") == "oauth":
                    expires_at = stored.get("expires_at")
                    refresh_token = stored.get("refresh_token")
                    import time
                    if expires_at and time.time() >= (float(expires_at) - 60) and refresh_token:
                        try:
                            from kitt.llm.oauth import OAuthManager
                            mgr = OAuthManager()
                            new_token = mgr.refresh_token(pid, refresh_token)
                            self.login_oauth(pid, new_token)
                            return new_token.access_token
                        except Exception:
                            pass
                return stored.get("value_ref")
            # Fallback to env var for this provider
            env_var = self.get_default_env_var(pid)
            return self.get_env_value(env_var)

        if credential_ref.startswith("env:"):
            env_name = credential_ref[4:]
            return self.get_env_value(env_name)

        if credential_ref.startswith("session:"):
            sess_key = credential_ref[8:].strip().lower()
            return _SESSION_CREDENTIALS.get(sess_key)

        # Raw string fallback (never persisted to file, but returned if passed in runtime)
        return credential_ref
