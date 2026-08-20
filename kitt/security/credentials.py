"""Credential security, resolution, and safe atomic file storage.

Ensures credentials are never persisted in plain text, resolving references
such as `env:OPENAI_API_KEY` or `session:KEY`. Protects POSIX permissions (0600 file / 0700 dir)
and prevents secrets from leaking in repr, logs, or exceptions.
"""

from __future__ import annotations

import os
import stat
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


def atomic_write_secure(
    target_path: str | Path,
    content: str,
    encoding: str = "utf-8",
) -> None:
    """Atomically write private file without following final symlink."""
    path = Path(
        os.path.abspath(
            os.path.expanduser(str(target_path))
        )
    )
    parent = path.parent

    def _lstat_optional(candidate: Path):
        try:
            return candidate.lstat()
        except FileNotFoundError:
            return None

    parent_pre = _lstat_optional(parent)
    if parent_pre is None:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_stat = _lstat_optional(parent)
    if parent_stat is None:
        raise PermissionError(
            f"Secure write parent was not created: {parent}"
        )
    if stat.S_ISLNK(parent_stat.st_mode):
        raise PermissionError(
            f"Secure write parent must not be a symlink: {parent}"
        )
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise PermissionError(
            f"Secure write parent must be a directory: {parent}"
        )
    if os.name == "posix":
        if parent_stat.st_uid != os.getuid():
            raise PermissionError(
                f"Secure write parent owner mismatch: {parent}"
            )
        if parent_pre is None:
            try:
                os.chmod(parent, 0o700)
            except OSError as exc:
                raise PermissionError(
                    f"Unable to secure new parent {parent}: {exc}"
                ) from exc

    target_stat = _lstat_optional(path)
    if target_stat is not None:
        if stat.S_ISLNK(target_stat.st_mode):
            raise PermissionError(
                f"Refusing secure write through symlink: {path}"
            )
        if not stat.S_ISREG(target_stat.st_mode):
            raise PermissionError(
                f"Secure write target must be a regular file: {path}"
            )
        if os.name == "posix" and target_stat.st_uid != os.getuid():
            raise PermissionError(
                f"Secure write target owner mismatch: {path}"
            )

    temp_fd, temp_path_str = tempfile.mkstemp(
        dir=str(parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_path_str)

    try:
        temp_stat = os.fstat(temp_fd)
        if not stat.S_ISREG(temp_stat.st_mode):
            raise PermissionError(
                f"Secure write temp is not a regular file: {temp_path}"
            )
        if os.name == "posix":
            os.fchmod(temp_fd, 0o600)

        payload = content.encode(encoding)
        offset = 0
        while offset < len(payload):
            written = os.write(temp_fd, payload[offset:])
            if written <= 0:
                raise OSError("Short secure write")
            offset += written
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = -1

        target_now = _lstat_optional(path)
        if target_now is not None:
            if stat.S_ISLNK(target_now.st_mode):
                raise PermissionError(
                    f"Refusing secure write through symlink: {path}"
                )
            if not stat.S_ISREG(target_now.st_mode):
                raise PermissionError(
                    f"Secure write target must be a regular file: {path}"
                )
            if os.name == "posix" and target_now.st_uid != os.getuid():
                raise PermissionError(
                    f"Secure write target owner mismatch: {path}"
                )

        os.replace(temp_path, path)
        if os.name == "posix":
            os.chmod(path, 0o600)

        final_stat = path.lstat()
        if stat.S_ISLNK(final_stat.st_mode) or not stat.S_ISREG(final_stat.st_mode):
            raise PermissionError(
                f"Secure write produced an unsafe target: {path}"
            )
        if os.name == "posix":
            if final_stat.st_uid != os.getuid():
                raise PermissionError(
                    f"Secure write final owner mismatch: {path}"
                )
            if stat.S_IMODE(final_stat.st_mode) & 0o077:
                raise PermissionError(
                    f"Secure write final permissions are not private: {path}"
                )

        try:
            dir_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                dir_flags |= os.O_DIRECTORY
            dir_fd = os.open(str(parent), dir_flags)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
