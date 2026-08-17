"""Model setup and configuration command handlers for K.I.T.T. UI."""
from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Tuple, Optional

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


def parse_model_command(arg: str) -> Tuple[str, str, Optional[str], Optional[str]]:
    tokens = shlex.split(arg) if arg else []
    if not tokens:
        return "", "", None, None
    role = tokens[0].lower()
    valid_roles = {"principal", "context", "validation", "all"}
    if role not in valid_roles:
        return "all", tokens[0], None, tokens[1] if len(tokens) > 1 else None
    if len(tokens) == 1:
        return role, "", None, None
    if len(tokens) == 2:
        return role, tokens[1], None, None
    if len(tokens) == 3:
        return role, tokens[2], tokens[1], None
    return role, tokens[2], tokens[1], tokens[3]


async def handle_model_command(app: KittUIApp, arg: str) -> None:
    if not arg:
        await app._open_model_setup_overlay()
    else:
        role, model, provider, base_url = parse_model_command(arg)
        if model:
            roles = app.model_setup_model.roles if role == "all" else (role,)
            for selected_role in roles:
                await app._set_model_role(selected_role, model, provider, base_url)
            self_backend = app._profile_for_role(roles[0]).backend
            app._show_result(f"{role.title()} model saved and active: {(provider or self_backend)}/{model}")
        else:
            app._show_result("Usage: /model <principal|context|validation|all> [provider] <model> [base_url]")


async def handle_setup_models_command(app: KittUIApp, arg: str) -> None:
    if arg and not arg.startswith(("http://", "https://")):
        app._show_result("Usage: /setup-models [http://server:11434]")
    else:
        await app._open_model_setup_overlay(arg or None)
