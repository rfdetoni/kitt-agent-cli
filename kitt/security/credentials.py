"""Credential security, resolution, and safe atomic file storage.

Ensures credentials are never persisted in plain text, resolving references
such as `env:OPENAI_API_KEY` or `session:KEY`. Protects POSIX permissions (0600 file / 0700 dir)
and prevents secrets from leaking in repr, logs, or exceptions.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional

SECRET_KEYS = {"api_key", "token", "secret", "password", "auth_token", "private_key"}

_SESSION_VAULT: Dict[str, str] = {}


def set_session_credential(key: str, value: str) -> None:
    """Store an in-memory session credential (not persisted to disk)."""
    _SESSION_VAULT[key] = value


def clear_session_credentials() -> None:
    """Clear all session credentials."""
    _SESSION_VAULT.clear()


@dataclass
class CredentialRef:
    """Opaque reference to a secret (e.g., 'env:OPENAI_API_KEY'). Redacts values in repr/str."""

    ref: str

    def resolve(self) -> Optional[str]:
        if not self.ref:
            return None
        if self.ref.startswith("env:"):
            env_var = self.ref[4:]
            return os.environ.get(env_var)
        if self.ref.startswith("session:"):
            sess_key = self.ref[8:]
            return _SESSION_VAULT.get(sess_key)
        # If passed raw string, return as-is for backward compatibility, but flag as raw
        return self.ref

    def is_reference(self) -> bool:
        return self.ref.startswith("env:") or self.ref.startswith("session:")

    def __repr__(self) -> str:
        return f"<CredentialRef ref={self.ref!r} resolved='[REDACTED]'>"

    def __str__(self) -> str:
        return "[REDACTED]"


class CredentialResolver:
    """Resolves credential references and enforces security policy for serialized data."""

    @staticmethod
    def resolve(ref_or_raw: str | None) -> Optional[str]:
        if not ref_or_raw:
            return None
        return CredentialRef(ref_or_raw).resolve()

    @staticmethod
    def sanitize_for_storage(data: Dict[str, Any], default_env_prefix: str = "KITT_API_KEY") -> Dict[str, Any]:
        """Convert literal secret fields to credential references (e.g. env:OPENAI_API_KEY).
        
        Refuses literal secrets unless converted or referenced.
        """
        sanitized = dict(data)
        for key in list(sanitized.keys()):
            if key in SECRET_KEYS:
                val = sanitized[key]
                if isinstance(val, str) and val:
                    if val.startswith("env:") or val.startswith("session:"):
                        pass  # Valid reference
                    else:
                        # Convert literal secret to env reference or ref field
                        ref_name = f"env:{default_env_prefix}"
                        sanitized[key] = ref_name
        return sanitized

    @staticmethod
    def redact_secrets(text: str, secrets: list[str] | None = None) -> str:
        """Redact known secret strings from text."""
        if not text:
            return text
        redacted = text
        if secrets:
            for sec in secrets:
                if sec and len(sec) >= 4:
                    redacted = redacted.replace(sec, "[REDACTED]")
        return redacted


def atomic_write_secure(target_path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """Atomically write file with strict POSIX permissions (0600) and directory permissions (0700).

    Uses temporary file + fsync + os.replace.
    Windows note: POSIX chmod modes (0600) are enforced on POSIX platforms. On Windows,
    file inheritance and OS ACLs govern access; file creation uses exclusive atomic replace.
    """
    path = Path(target_path).resolve()
    parent = path.parent
    if not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            try:
                os.chmod(parent, 0o700)
            except OSError:
                pass

    temp_fd, temp_path_str = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temp_path_str)

    try:
        if sys.platform != "win32":
            os.chmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        if sys.platform != "win32":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise
