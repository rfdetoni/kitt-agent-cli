"""Provider authentication, credential store (~/.kitt/auth.json), and runtime resolution."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
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


class _CredentialFileLock:
    """Cross-process lock for auth.json read-modify-write operations."""

    def __init__(self, path: Path, timeout_seconds: float = 5.0):
        self.path = path
        self.timeout_seconds = timeout_seconds
        self._handle = None

    def __enter__(self):
        parent = CredentialStore._ensure_private_dir(self.path.parent)
        if self.path.is_symlink():
            raise PermissionError(
                f"Refusing symlink credential lock: {self.path}"
            )

        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        try:
            fd = os.open(str(self.path), flags, 0o600)
        except OSError as exc:
            raise PermissionError(
                f"Unable to securely open credential lock {self.path}: {exc}"
            ) from exc

        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise PermissionError(
                    f"Credential lock must be a regular file: {self.path}"
                )
            if os.name != "nt":
                if st.st_uid != os.getuid():
                    raise PermissionError(
                        f"Credential lock owner mismatch: {self.path}"
                    )
                os.fchmod(fd, 0o600)
            self._handle = os.fdopen(fd, "r+b", buffering=0)
            fd = -1
        finally:
            if fd >= 0:
                os.close(fd)

        deadline = time.monotonic() + self.timeout_seconds
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
            while True:
                try:
                    self._handle.seek(0)
                    msvcrt.locking(
                        self._handle.fileno(),
                        msvcrt.LK_NBLCK,
                        1,
                    )
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out acquiring credential lock {self.path}"
                        )
                    time.sleep(0.05)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(
                        self._handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out acquiring credential lock {self.path}"
                        )
                    time.sleep(0.05)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(
                    self._handle.fileno(),
                    msvcrt.LK_UNLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(
                    self._handle.fileno(),
                    fcntl.LOCK_UN,
                )
        finally:
            self._handle.close()
            self._handle = None


class CredentialStore:
    """Private credential store for API keys and OAuth tokens."""

    _MAX_AUTH_BYTES = 1024 * 1024

    def __init__(self, auth_file: Optional[str] = None):
        self.auth_file = self._resolve_auth_file(auth_file)
        self.lock_file = self.auth_file.with_suffix(
            self.auth_file.suffix + ".lock"
        )
        self._thread_lock = threading.RLock()

    @staticmethod
    def _absolute_unresolved(path: str | Path) -> Path:
        return Path(
            os.path.abspath(
                os.path.expanduser(str(path))
            )
        )

    @staticmethod
    def _ensure_private_dir(path: str | Path) -> Path:
        path = CredentialStore._absolute_unresolved(path)
        if path.is_symlink():
            raise PermissionError(
                f"Credential directory must not be a symlink: {path}"
            )

        try:
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            st = path.lstat()
        except OSError as exc:
            raise PermissionError(
                f"Unable to prepare credential directory {path}: {exc}"
            ) from exc

        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise PermissionError(
                f"Credential directory must be a real directory: {path}"
            )

        if os.name != "nt":
            if st.st_uid != os.getuid():
                raise PermissionError(
                    f"Credential directory owner mismatch: {path}"
                )
            try:
                os.chmod(path, 0o700)
            except OSError as exc:
                raise PermissionError(
                    f"Unable to secure credential directory {path}: {exc}"
                ) from exc
            st = path.lstat()
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise PermissionError(
                    f"Credential directory permissions must be 0700: {path}"
                )
        return path

    @classmethod
    def _resolve_auth_file(cls, auth_file: Optional[str]) -> Path:
        if auth_file:
            candidate = cls._absolute_unresolved(auth_file)
            cls._ensure_private_dir(candidate.parent)
            return candidate

        home = Path.home().resolve()
        preferred_parent = home / ".kitt"
        preferred = preferred_parent / "auth.json"
        try:
            cls._ensure_private_dir(preferred_parent)
            return cls._absolute_unresolved(preferred)
        except PermissionError:
            owner = (
                str(os.getuid())
                if hasattr(os, "getuid")
                else str(os.getpid())
            )
            fallback_parent = (
                Path(tempfile.gettempdir())
                / f"kitt-{owner}"
            )
            cls._ensure_private_dir(fallback_parent)
            return cls._absolute_unresolved(
                fallback_parent / "auth.json"
            )

    @staticmethod
    def _read_flags() -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return flags

    def _validate_auth_fd(self, fd: int) -> os.stat_result:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PermissionError(
                f"Credential store must be a regular file: {self.auth_file}"
            )
        if os.name != "nt":
            if st.st_uid != os.getuid():
                raise PermissionError(
                    f"Credential store owner mismatch: {self.auth_file}"
                )
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise PermissionError(
                    f"Credential store permissions must be 0600: "
                    f"{self.auth_file}"
                )
        if st.st_size > self._MAX_AUTH_BYTES:
            raise PermissionError(
                f"Credential store exceeds {self._MAX_AUTH_BYTES} bytes: "
                f"{self.auth_file}"
            )
        return st

    def _load_unlocked(self) -> Dict[str, Dict[str, Any]]:
        if self.auth_file.is_symlink():
            raise PermissionError(
                f"Refusing symlink credential store: {self.auth_file}"
            )

        try:
            fd = os.open(
                str(self.auth_file),
                self._read_flags(),
            )
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise PermissionError(
                f"Unable to securely open credential store "
                f"{self.auth_file}: {exc}"
            ) from exc

        try:
            before = self._validate_auth_fd(fd)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(
                    fd,
                    min(
                        64 * 1024,
                        (self._MAX_AUTH_BYTES + 1) - total,
                    ),
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > self._MAX_AUTH_BYTES:
                    raise PermissionError(
                        f"Credential store exceeds "
                        f"{self._MAX_AUTH_BYTES} bytes"
                    )
                chunks.append(chunk)

            after = os.fstat(fd)
            before_fp = (
                getattr(before, "st_dev", None),
                getattr(before, "st_ino", None),
                before.st_size,
                getattr(before, "st_mtime_ns", None),
                getattr(before, "st_ctime_ns", None),
            )
            after_fp = (
                getattr(after, "st_dev", None),
                getattr(after, "st_ino", None),
                after.st_size,
                getattr(after, "st_mtime_ns", None),
                getattr(after, "st_ctime_ns", None),
            )
            if before_fp != after_fp or total != before.st_size:
                raise PermissionError(
                    f"Credential store changed while being read: "
                    f"{self.auth_file}"
                )
        finally:
            os.close(fd)

        try:
            parsed = json.loads(
                b"".join(chunks).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Preserve the historical recovery behavior for corrupt JSON,
            # while security violations above remain fail-closed.
            return {}

        if not isinstance(parsed, dict):
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for key, value in parsed.items():
            if isinstance(key, str) and isinstance(value, dict):
                result[key] = value
        return result

    def load(self) -> Dict[str, Dict[str, Any]]:
        with self._thread_lock:
            return self._load_unlocked()

    def save_credential(
        self,
        provider_id: str,
        auth_type: str,
        value_or_ref: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._thread_lock, _CredentialFileLock(self.lock_file):
            data = self._load_unlocked()
            pid = provider_id.strip().lower()
            payload: Dict[str, Any] = {
                "type": auth_type,
                "value_ref": value_or_ref,
            }
            if extra:
                payload.update(extra)
            data[pid] = payload
            self._write_atomic_unlocked(data)

    def remove_credential(self, provider_id: str) -> bool:
        with self._thread_lock, _CredentialFileLock(self.lock_file):
            data = self._load_unlocked()
            pid = provider_id.strip().lower()
            if pid in data:
                del data[pid]
                self._write_atomic_unlocked(data)
                return True
            return False

    def _write_atomic_unlocked(
        self,
        data: Dict[str, Any],
    ) -> None:
        parent = self._ensure_private_dir(self.auth_file.parent)
        if self.auth_file.is_symlink():
            raise PermissionError(
                f"Refusing symlink credential store: {self.auth_file}"
            )

        payload = (
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(payload) > self._MAX_AUTH_BYTES:
            raise ValueError(
                f"Credential store would exceed "
                f"{self._MAX_AUTH_BYTES} bytes"
            )

        fd, tmp_name = tempfile.mkstemp(
            prefix=".auth.",
            dir=str(parent),
        )
        tmp = Path(tmp_name)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise PermissionError(
                    f"Temporary credential file is not regular: {tmp}"
                )
            if os.name != "nt":
                os.fchmod(fd, 0o600)

            offset = 0
            while offset < len(payload):
                written = os.write(fd, payload[offset:])
                if written <= 0:
                    raise OSError("Short credential store write")
                offset += written
            os.fsync(fd)
            os.close(fd)
            fd = -1

            # The parent is private. Reject an already-present symlink rather
            # than silently replacing an unexpected attacker-controlled entry.
            if self.auth_file.is_symlink():
                raise PermissionError(
                    f"Refusing symlink credential store: {self.auth_file}"
                )
            os.replace(tmp, self.auth_file)
            if os.name != "nt":
                os.chmod(self.auth_file, 0o600)

            # Validate the exact final object before returning success.
            verify_fd = os.open(
                str(self.auth_file),
                self._read_flags(),
            )
            try:
                self._validate_auth_fd(verify_fd)
            finally:
                os.close(verify_fd)

            if os.name != "nt":
                try:
                    dir_fd = os.open(
                        str(parent),
                        os.O_RDONLY
                        | (
                            os.O_DIRECTORY
                            if hasattr(os, "O_DIRECTORY")
                            else 0
                        ),
                    )
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
        finally:
            if fd >= 0:
                os.close(fd)
            tmp.unlink(missing_ok=True)

    def _write_atomic(self, data: Dict[str, Any]) -> None:
        with self._thread_lock, _CredentialFileLock(self.lock_file):
            self._write_atomic_unlocked(data)


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
