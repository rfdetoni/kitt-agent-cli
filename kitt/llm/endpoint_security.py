"""Provider endpoint trust and credential egress binding.

Workspace configuration may describe endpoints, but it cannot authorize network
egress or bind a user's global credentials to a different origin. Trust for
non-default origins lives outside the workspace in a user-private state file.
"""
from __future__ import annotations

import json
import os
import stat
import threading
import urllib.parse
from pathlib import Path
from typing import Optional

from kitt.llm.catalog import BUILTIN_PROVIDERS
from kitt.llm.domain import ProviderAuthError
from kitt.security.credentials import atomic_write_secure


_MAX_TRUST_BYTES = 64 * 1024
_TRUST_VERSION = 1
_REFERENCE_PREFIXES = ("auth:", "env:", "session:")
_RESERVED_PROVIDER_IDS = frozenset(
    provider.id.strip().lower()
    for provider in BUILTIN_PROVIDERS
)


def normalize_endpoint_origin(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Provider endpoint is empty")

    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid provider endpoint: {raw!r}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(
            f"Unsupported provider endpoint scheme: {scheme or '<missing>'}"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Provider endpoint must not contain URL credentials")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Provider endpoint has no host")

    if port is None:
        port = 443 if scheme == "https" else 80

    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{rendered_host}:{port}"


_OFFICIAL_ORIGINS = {
    provider.id.strip().lower(): normalize_endpoint_origin(provider.base_url)
    for provider in BUILTIN_PROVIDERS
    if provider.base_url
}


def is_reserved_provider_id(provider_id: str) -> bool:
    return (provider_id or "").strip().lower() in _RESERVED_PROVIDER_IDS


class ProviderEndpointTrustStore:
    """User-local trust store for non-default provider endpoint origins."""

    def __init__(self, path: Optional[str | Path] = None):
        self.path = self._absolute_unresolved(
            path or (Path.home() / ".kitt" / "provider-endpoints.json")
        )
        self._lock = threading.RLock()

    @staticmethod
    def _absolute_unresolved(path: str | Path) -> Path:
        return Path(
            os.path.abspath(
                os.path.expanduser(str(path))
            )
        )

    def _ensure_private_parent(self) -> Path:
        parent = self.path.parent
        if parent.is_symlink():
            raise PermissionError(
                f"Provider endpoint trust directory must not be a symlink: {parent}"
            )
        try:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            st = parent.lstat()
        except OSError as exc:
            raise PermissionError(
                f"Unable to prepare provider endpoint trust directory {parent}: {exc}"
            ) from exc

        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise PermissionError(
                f"Provider endpoint trust parent must be a real directory: {parent}"
            )
        if os.name != "nt":
            if st.st_uid != os.getuid():
                raise PermissionError(
                    f"Provider endpoint trust parent owner mismatch: {parent}"
                )
            try:
                os.chmod(parent, 0o700)
            except OSError as exc:
                raise PermissionError(
                    f"Unable to secure provider endpoint trust parent {parent}: {exc}"
                ) from exc
            st = parent.lstat()
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise PermissionError(
                    f"Provider endpoint trust parent must be 0700: {parent}"
                )
        return parent

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

    def _read_unlocked(self) -> dict:
        self._ensure_private_parent()
        if self.path.is_symlink():
            raise PermissionError(
                f"Refusing symlink provider endpoint trust file: {self.path}"
            )
        try:
            fd = os.open(str(self.path), self._read_flags())
        except FileNotFoundError:
            return {"version": _TRUST_VERSION, "providers": {}}
        except OSError as exc:
            raise PermissionError(
                f"Unable to securely open provider endpoint trust file "
                f"{self.path}: {exc}"
            ) from exc

        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise PermissionError(
                    f"Provider endpoint trust state must be regular: {self.path}"
                )
            if os.name != "nt":
                if st.st_uid != os.getuid():
                    raise PermissionError(
                        f"Provider endpoint trust owner mismatch: {self.path}"
                    )
                if stat.S_IMODE(st.st_mode) & 0o077:
                    raise PermissionError(
                        f"Provider endpoint trust file must be 0600: {self.path}"
                    )
            if st.st_size > _MAX_TRUST_BYTES:
                raise PermissionError(
                    f"Provider endpoint trust state exceeds {_MAX_TRUST_BYTES} bytes"
                )

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(
                    fd,
                    min(4096, (_MAX_TRUST_BYTES + 1) - total),
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_TRUST_BYTES:
                    raise PermissionError(
                        "Provider endpoint trust state exceeds size limit"
                    )
                chunks.append(chunk)
        finally:
            os.close(fd)

        try:
            data = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"version": _TRUST_VERSION, "providers": {}}

        if not isinstance(data, dict):
            return {"version": _TRUST_VERSION, "providers": {}}
        providers = data.get("providers")
        if not isinstance(providers, dict):
            providers = {}

        clean: dict[str, list[str]] = {}
        for provider_id, origins in providers.items():
            if not isinstance(provider_id, str) or not isinstance(origins, list):
                continue
            normalized = []
            for origin in origins:
                if not isinstance(origin, str):
                    continue
                try:
                    normalized_origin = normalize_endpoint_origin(origin)
                except ValueError:
                    continue
                if normalized_origin not in normalized:
                    normalized.append(normalized_origin)
            if normalized:
                clean[provider_id.strip().lower()] = sorted(normalized)

        return {"version": _TRUST_VERSION, "providers": clean}

    def is_official(self, provider_id: str, url: str) -> bool:
        pid = (provider_id or "").strip().lower()
        try:
            origin = normalize_endpoint_origin(url)
        except ValueError:
            return False
        return _OFFICIAL_ORIGINS.get(pid) == origin

    def is_trusted(self, provider_id: str, url: str) -> bool:
        pid = (provider_id or "").strip().lower()
        if not pid:
            return False
        try:
            origin = normalize_endpoint_origin(url)
        except ValueError:
            return False

        if _OFFICIAL_ORIGINS.get(pid) == origin:
            return True

        with self._lock:
            state = self._read_unlocked()
        return origin in state["providers"].get(pid, [])

    def trust(self, provider_id: str, url: str) -> str:
        pid = (provider_id or "").strip().lower()
        if not pid:
            raise ValueError("Provider id is required to trust an endpoint")
        origin = normalize_endpoint_origin(url)

        if _OFFICIAL_ORIGINS.get(pid) == origin:
            return origin

        with self._lock:
            state = self._read_unlocked()
            providers = state.setdefault("providers", {})
            origins = list(providers.get(pid, []))
            if origin not in origins:
                origins.append(origin)
            providers[pid] = sorted(set(origins))
            payload = json.dumps(
                {
                    "version": _TRUST_VERSION,
                    "providers": providers,
                },
                indent=2,
                sort_keys=True,
            ) + "\n"
            if len(payload.encode("utf-8")) > _MAX_TRUST_BYTES:
                raise ValueError("Provider endpoint trust state exceeds size limit")
            self._ensure_private_parent()
            atomic_write_secure(self.path, payload)
        return origin

    def assert_trusted(self, provider_id: str, url: str) -> str:
        try:
            origin = normalize_endpoint_origin(url)
        except ValueError as exc:
            raise ProviderAuthError(str(exc)) from exc
        if not self.is_trusted(provider_id, url):
            pid = (provider_id or "").strip().lower() or "<unknown>"
            raise ProviderAuthError(
                f"Refusing provider egress: endpoint {origin} is not locally "
                f"trusted for provider '{pid}'"
            )
        return origin


def _validate_reference_identity(
    auth_service,
    provider_id: str,
    credential_ref: str,
) -> None:
    pid = (provider_id or "").strip().lower()
    ref = (credential_ref or "").strip()

    if ref.startswith("auth:") or ref.startswith("session:"):
        target = ref.split(":", 1)[1].strip().lower()
        if target != pid:
            raise ProviderAuthError(
                f"Credential reference for '{target}' cannot be used by "
                f"provider '{pid}'"
            )
        return

    if ref.startswith("env:"):
        env_name = ref[4:].strip()
        allowed = {auth_service.get_default_env_var(pid)}
        for descriptor in BUILTIN_PROVIDERS:
            if descriptor.id.strip().lower() == pid:
                allowed.update(descriptor.env_vars)
                break
        allowed.discard("")
        if env_name not in allowed:
            raise ProviderAuthError(
                f"Environment credential '{env_name}' is not bound to "
                f"provider '{pid}'"
            )
        return

    raise ProviderAuthError(
        "Credential references must use auth:, env:, or session:"
    )


def resolve_endpoint_credential(
    auth_service,
    provider_id: str,
    base_url: str,
    *,
    credential_ref: Optional[str] = None,
    raw_secret: Optional[str] = None,
    policy: Optional[ProviderEndpointTrustStore] = None,
) -> Optional[str]:
    """Resolve secret only after provider identity and endpoint are trusted."""
    pid = (provider_id or "").strip().lower()
    endpoint_policy = policy or ProviderEndpointTrustStore()

    endpoint_policy.assert_trusted(pid, base_url)

    ref = (credential_ref or "").strip() or None
    raw = (raw_secret or "").strip() or None

    if ref:
        _validate_reference_identity(auth_service, pid, ref)

    if raw and raw.startswith(_REFERENCE_PREFIXES):
        if ref and raw != ref:
            raise ProviderAuthError(
                "Conflicting credential references on model profile"
            )
        ref = raw
        raw = None
        _validate_reference_identity(auth_service, pid, ref)

    if ref and ref.startswith("auth:"):
        try:
            stored = auth_service.store.load().get(pid)
        except Exception:
            stored = None
        if (
            isinstance(stored, dict)
            and stored.get("type") == "oauth"
            and not endpoint_policy.is_official(pid, base_url)
        ):
            raise ProviderAuthError(
                f"OAuth credential for '{pid}' may only be used with its "
                "official provider endpoint"
            )
    elif not ref and not raw:
        env_var = auth_service.get_default_env_var(pid)
        if not auth_service.get_env_value(env_var):
            try:
                stored = auth_service.store.load().get(pid)
            except Exception:
                stored = None
            if (
                isinstance(stored, dict)
                and stored.get("type") == "oauth"
                and not endpoint_policy.is_official(pid, base_url)
            ):
                raise ProviderAuthError(
                    f"OAuth credential for '{pid}' may only be used with its "
                    "official provider endpoint"
                )

    resolved = auth_service.resolve(ref, provider_id=pid)
    if resolved:
        return resolved
    return raw
