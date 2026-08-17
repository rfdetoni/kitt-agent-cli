"""Localization and UI string resources for K.I.T.T."""
from __future__ import annotations

STRINGS_EN = {
    "welcome": "K.I.T.T. Autonomous Coding Agent",
    "export_done": "Exported: {filename}",
    "no_active_conv": "No active conversation.",
    "turn_cancelled": "Active turn cancelled.",
    "usage_export": "Usage: /export [markdown|json]",
}

STRINGS_PT = {
    "welcome": "K.I.T.T. Agente de Codificação Autônomo",
    "export_done": "Exportado: {filename}",
    "no_active_conv": "Nenhuma conversa ativa.",
    "turn_cancelled": "Turno ativo cancelado.",
    "usage_export": "Uso: /export [markdown|json]",
}


def get_string(key: str, lang: str = "en", **kwargs) -> str:
    bundle = STRINGS_PT if lang.lower().startswith("pt") else STRINGS_EN
    msg = bundle.get(key, STRINGS_EN.get(key, key))
    if kwargs:
        return msg.format(**kwargs)
    return msg
