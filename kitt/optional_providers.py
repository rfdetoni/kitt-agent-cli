"""Configure the existing OpenAI-compatible adapter for an optional gateway."""
from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

from kitt.domain.entities import ModelProfile
from kitt.router.router import TaskRouter


def register_freellmapi(
    workspace: str | Path,
    *,
    model: str = "auto",
    base_url: str = "http://127.0.0.1:3001",
    activate: bool = False,
    allow_remote: bool = False,
) -> dict[str, object]:
    """Store only a credential *reference*, never a provider key in the repo."""
    address = urlsplit(base_url)
    if address.scheme not in ("http", "https") or not address.hostname:
        raise ValueError("Gateway URL must be an absolute http(s) URL")
    if address.username or address.password or address.query or address.fragment:
        raise ValueError("Gateway URL must not contain credentials, queries or fragments")
    host = address.hostname.lower()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if not loopback and not allow_remote:
        raise PermissionError("Remote gateways require --allow-remote")
    if not loopback and address.scheme != "https":
        raise ValueError("Remote gateway must use HTTPS")
    if not model.strip() or len(model) > 128:
        raise ValueError("Model must be nonempty and at most 128 characters")

    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace is not a directory")
    router = TaskRouter(str(root))
    router.config.profiles["freellmapi"] = ModelProfile(
        backend="openai-compatible",
        model=model.strip(),
        base_url=base_url.rstrip("/"),
        credential_ref="env:FREELLMAPI_API_KEY",
        context_window=8192,
        supports_tools=True,
        supports_json=True,
    )
    if activate:
        router.config.routing.update({
            "code-generation": "freellmapi",
            "code-edit": "freellmapi",
        })
    router.save_config(str(root))
    return {
        "profile": "freellmapi",
        "model": model.strip(),
        "gateway": base_url.rstrip("/"),
        "active_for_code": activate,
        "credential": "env:FREELLMAPI_API_KEY",
    }
