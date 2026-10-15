"""Agent Manager command surface for the TUI. Mutations use existing policy tooling."""
from __future__ import annotations

import shlex

from kitt.agents.helper import create_agent, edit_persona


def _parse_paths(tokens: list[str]) -> tuple[list[str], tuple[str, ...]]:
    if tokens and tokens[0] == "--paths":
        if len(tokens) < 3:
            raise ValueError("Use --paths path1,path2 followed by a task")
        paths = tuple(p.strip() for p in tokens[1].split(",") if p.strip())
        return tokens[2:], paths
    return tokens, ()


async def handle_agent_manager(ui, arg: str) -> None:
    registry = ui.runtime.agent_registry
    try:
        tokens = shlex.split(arg)
        action = tokens.pop(0).lower() if tokens else "list"
        if action in {"list", "menu", "help"}:
            rows = [
                "KITT AGENT MANAGER",
                "━━━━━━━━━━━━━━━━━━",
                "Agent                   Role         Tools",
            ]
            for profile in registry.list():
                rows.append(
                    f"{profile.id:<23} {profile.role:<12} {', '.join(profile.tools[:3])}"
                )
            rows.extend((
                "",
                "/agent run <id> [--paths p1,p2] <task>",
                "/agent auto [--paths p1,p2] <task>",
                "/agent team <task>              (investigator + creative, parallel)",
                "/agent create <slug> [role]    (untrusted skeleton)",
                "/agent edit <slug> <new persona>",
                "/agent enable|disable <id>",
                "/agent inspect <id>",
                "/agent msg <from> <to> <message>",
                "/agent inbox                 (family messages)",
                "Guided creator: kitt agents create",
                "To trust a custom profile: kitt plugins trust agent-<slug>",
            ))
            ui._show_result("\n".join(rows))
            return
        if action == "inspect":
            p = registry.find(tokens[0])
            ui._show_result(
                f"{p.id} / {p.role} / {p.name}\n{p.persona}\n"
                f"Tools: {', '.join(p.tools)}\nTokens: {p.token_budget}"
                f"\nTimeout: {p.timeout_seconds}s\nContract: {p.output_contract}"
            )
            return
        if action == "create":
            if not tokens:
                raise ValueError("Usage: /agent create <slug> [role]")
            slug = tokens[0]
            role = tokens[1] if len(tokens) > 1 else "investigator"
            path = await ui._run_blocking(
                create_agent, ui.runtime.canonical_root, slug, role=role,
            )
            ui._show_result(
                f"Agent skeleton created: {path}\n"
                f"Edit {path / 'persona.md'} then run: kitt plugins trust agent-{slug}\n"
                "Reload the Agent Manager to discover the trusted profile."
            )
            return
        if action == "edit":
            if len(tokens) < 2:
                raise ValueError("Usage: /agent edit <slug> <new persona>")
            slug, persona = tokens[0], " ".join(tokens[1:])
            path = await ui._run_blocking(
                edit_persona, ui.runtime.canonical_root, slug, persona,
            )
            ui._show_result(f"Persona updated: {path}\nRe-trust: kitt plugins trust agent-{slug}")
            return
        if action in {"enable", "disable"}:
            if len(tokens) != 1:
                raise ValueError(f"Usage: /agent {action} <id>")
            await ui._run_blocking(registry.set_enabled, tokens[0], action == "enable")
            ui._show_result(f"{tokens[0]}: {action}d")
            return
        if action in {"run", "auto"}:
            if action == "run":
                if len(tokens) < 2:
                    raise ValueError("Usage: /agent run <id> [--paths a,b] <task>")
                profile = registry.find(tokens.pop(0))
            else:
                profile = None
            text, paths = _parse_paths(tokens)
            task = " ".join(text)
            if not task:
                raise ValueError("Agent task cannot be empty")
            if profile is None:
                profile = registry.select(task, require_write=bool(paths))
            args = profile.spawn_args(task, paths=paths)
            await ui._execute_direct_tool("child_spawn", args)
            return
        if action == "team":
            if not tokens:
                raise ValueError("Usage: /agent team <task>")
            if ui.runtime.children.max_children < 2:
                raise ValueError("Team needs at least 2 child slots")
            task = " ".join(tokens)
            team = (registry.find("investigator"), registry.find("creative"))
            for agent in team:
                await ui._execute_direct_tool("child_spawn", agent.spawn_args(task))
            ui._show_result("Specialist team submitted. Use /tasks and /agent inbox to monitor.")
            return
        if action == "msg":
            if len(tokens) < 3:
                raise ValueError("Usage: /agent msg <sender> <recipient> <message>")
            sender, recipient = tokens[0], tokens[1]
            message = " ".join(tokens[2:])
            # The host policy (children.send) validates conversation, sender and
            # recipient; it never bypasses the existing authorization boundary.
            await ui._execute_direct_tool("kitt_runtime", {
                "operation": "children.send",
                "arguments": {"sender": sender, "recipient": recipient, "message": message},
            })
            return
        if action == "inbox":
            conversation = ui.runtime.history.get_or_create_active()
            messages = await ui._run_blocking(
                ui.runtime.children.list_messages, conversation["id"],
            )
            ui._show_result("\n".join(
                f"{m.sender_id} -> {m.recipient_id} [{m.status}]: "
                f"{str(m.payload.get('text') or m.payload.get('question') or m.payload)[:180]}"
                for m in messages[-50:]
            ) or "No messages in this conversation.")
            return
        raise ValueError(f"Unknown agent action: {action}")
    except (ValueError, OSError, PermissionError, IndexError) as exc:
        ui._show_result(f"Agent Manager: {exc}")
