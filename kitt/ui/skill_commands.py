"""Skill, memory, and diagnostics command handlers for K.I.T.T. UI."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


async def handle_setup_skills_command(app: KittUIApp, arg: str) -> None:
    skills = app.runtime.skills.list_skills()
    active = set(app.runtime.skills.get_active_skills())
    action, _, skill_name = arg.partition(" ")
    if action in {"enable", "disable"} and skill_name.strip():
        if action == "enable":
            active.add(skill_name.strip())
        else:
            active.discard(skill_name.strip())
        await app._run_blocking(app.runtime.skills.set_active_skills, sorted(active))
        app._show_result(f"Skill {action}d: {skill_name.strip()}")
    else:
        body = "\n".join(
            f"  {'[x]' if s.name in active else '[ ]'} {s.name} (v{s.version}) — {s.author}"
            for s in skills
        ) if skills else "  No custom skills installed."
        app._show_result(f"Skill Configuration:\n\nInstalled Skills:\n{body}\n\n/setup-skills enable <name>\n/setup-skills disable <name>")


async def handle_skill_install_command(app: KittUIApp, arg: str) -> None:
    if not arg:
        app._show_result("Usage: /skill-install <github/repo or URL>")
    else:
        try:
            skill = await app._run_blocking(app.runtime.skills.install_from_git, arg)
            app._show_result(f"Installed: {skill.name} v{skill.version}")
        except Exception as exc:
            app._show_result(f"Install failed: {exc}")


async def handle_skill_remove_command(app: KittUIApp, arg: str) -> None:
    removed = await app._run_blocking(app.runtime.skills.remove_skill, arg) if arg else False
    app._show_result("Usage: /skill-remove <skill_name>" if not arg else ("Removed." if removed else "Skill not found."))


async def handle_remember_command(app: KittUIApp, arg: str) -> None:
    if arg:
        await app._run_blocking(app.runtime.memory.add_project_memory, arg)
        app._show_result(f"Remembered: {arg}")
    else:
        app._show_result("Usage: /remember <rule or guideline>")


async def handle_clear_memory_command(app: KittUIApp) -> None:
    await app._run_blocking(app.runtime.memory.clear_project_memory)
    app._show_result("Project memory cleared.")


async def handle_doctor_command(app: KittUIApp) -> None:
    from kitt.cli.doctor import DoctorCheck
    results = await app._run_blocking(DoctorCheck(str(app.runtime.canonical_root)).run_diagnostics)
    app._show_result("\n".join(f"[{item['status']}] {item['name']}: {item['detail']}" for item in results))
