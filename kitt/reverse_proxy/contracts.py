from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class ReverseProxyPlugin:
    id: str
    name: str
    version: str
    source: str
    default_url: str | None
    default_model: str
    transports: tuple[str, ...]
    auth: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReverseProxyPlugin":
        default_url = _text(value.get("default_url")) or None
        transports = value.get("transports")
        return cls(
            id=_text(value.get("id")),
            name=_text(value.get("name")),
            version=_text(value.get("version")),
            source=_text(value.get("source")),
            default_url=default_url,
            default_model=_text(value.get("default_model")),
            transports=tuple(str(item) for item in transports) if isinstance(transports, list) else (),
            auth=_text(value.get("auth")),
        )


@dataclass(frozen=True)
class ReverseProxyProfile:
    id: str
    name: str
    providers: tuple[str, ...]
    legacy: bool

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReverseProxyProfile":
        providers = value.get("providers")
        return cls(
            id=_text(value.get("id")),
            name=_text(value.get("name")) or _text(value.get("id")),
            providers=tuple(str(item) for item in providers) if isinstance(providers, list) else (),
            legacy=bool(value.get("legacy", False)),
        )


@dataclass(frozen=True)
class ReverseProxyInstance:
    id: str
    provider: str
    model: str
    target: str
    profile_id: str
    host: str
    port: int
    pid: int
    status: str
    started_at: str

    @property
    def endpoint(self) -> str:
        host = self.host or "127.0.0.1"
        return f"http://{host}:{self.port}"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReverseProxyInstance":
        return cls(
            id=_text(value.get("id")),
            provider=_text(value.get("provider")),
            model=_text(value.get("model")),
            target=_text(value.get("target")),
            profile_id=_text(value.get("profileId") or value.get("profile_id")),
            host=_text(value.get("host")) or "127.0.0.1",
            port=int(value.get("port") or 0),
            pid=int(value.get("pid") or 0),
            status=_text(value.get("status")) or "running",
            started_at=_text(value.get("startedAt") or value.get("started_at")),
        )
