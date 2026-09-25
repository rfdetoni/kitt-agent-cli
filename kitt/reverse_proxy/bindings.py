from __future__ import annotations

from kitt.reverse_proxy.contracts import ReverseProxyInstance

_ROLE_ALIASES = {
    "context": "context",
    "ctx": "context",
    "principal": "principal",
    "main": "principal",
    "code": "principal",
    "coding": "principal",
    "validation": "validation",
    "validate": "validation",
}


def normalize_role(role: str) -> str:
    normalized = _ROLE_ALIASES.get(role.strip().lower())
    if not normalized:
        raise ValueError("Role must be context, principal/code, or validation.")
    return normalized


async def bind_instance_to_role(ui, role: str, instance: ReverseProxyInstance) -> None:
    """Reuse Agent CLI's existing model-router persistence for a proxy instance."""
    normalized = normalize_role(role)
    await ui._set_model_role(
        normalized,
        instance.model,
        "kitt-reverse-proxy",
        instance.endpoint,
    )
