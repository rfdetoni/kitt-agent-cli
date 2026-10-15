"""Local helper for custom declarative agent plugins (no Python execution)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from kitt.agents.registry import AgentRegistry

_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_ROLES = {"code": "IMPLEMENT", "creative": "ARCHITECT",
          "investigator": "DISCOVER", "critic": "REVIEW", "tester": "VERIFY"}


def create_agent(
    workspace: str | Path, slug: str, *, role: str = "DISCOVER",
    display_name: str | None = None, persona: str | None = None,
    tags: tuple[str, ...] = (), token_budget: int = 4096,
) -> Path:
    """Create a new workspace plugin; never overwrite files or trust it implicitly."""
    if not _SLUG.fullmatch(slug):
        raise ValueError("Invalid agent slug")
    role = _ROLES.get(role.lower(), role.upper())
    if role not in _ROLES.values():
        raise ValueError("Role must be DISCOVER, ARCHITECT, IMPLEMENT, VERIFY or REVIEW")
    if type(token_budget) is not int or not 512 <= token_budget <= 32768:
        raise ValueError("Token budget out of range")
    if len(tags) > 24 or any(not isinstance(t, str) or not 1 <= len(t) <= 64 for t in tags):
        raise ValueError("Invalid tags")
    persona = persona or f"You are a {role.lower()} specialist. Report evidence, limitations and verification."
    if not 1 <= len(persona) <= 8192:
        raise ValueError("Persona must have 1..8192 characters")
    workspace = Path(workspace).resolve()
    plugins = workspace / ".kitt" / "plugins"
    if (workspace / ".kitt").is_symlink() or plugins.is_symlink():
        raise PermissionError("Workspace plugin directory cannot be symlinked")
    plugins.mkdir(parents=True, exist_ok=True)
    target = plugins / f"agent-{slug}"
    target.mkdir(mode=0o700, exist_ok=False)
    try:
        manifest = (
            f"name = {json.dumps('agent-' + slug)}\n"
            "version = \"1.0.0\"\n"
            "api_version = \"1\"\n"
            "entrypoint = \"declarative\"\n"
            'permissions = ["agents.register"]\n'
            "trusted_in_process = false\n"
            "enabled_by_default = true\n"
            f"\n[agents.{slug}]\n"
            f"name = {json.dumps(display_name or slug.replace('-', ' ').title())}\n"
            f"role = {json.dumps(role)}\n"
            'persona_file = "persona.md"\n'
            f"read_only = {'false' if role == 'IMPLEMENT' else 'true'}\n"
            f"max_tokens = {token_budget}\n"
            "timeout_seconds = 120\n"
            f"tags = {json.dumps(list(tags))}\n"
            'output_contract = "agent.evidence.v1"\n'
        )
        # Exclusive file creation; a newly created plugin remains untrusted.
        for filename, text in (("plugin.toml", manifest), ("persona.md", persona + "\n")):
            fd = os.open(target / filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        return target
    except BaseException:
        for path in target.iterdir():
            path.unlink()
        target.rmdir()
        raise


def edit_persona(workspace: str | Path, slug: str, text: str) -> Path:
    """Atomic persona update: an existing plugin's trust digest becomes invalid."""
    if not _SLUG.fullmatch(slug) or not 1 <= len(text) <= 8192:
        raise ValueError("Invalid agent slug or persona")
    root = Path(workspace).resolve() / ".kitt" / "plugins" / f"agent-{slug}"
    if root.is_symlink() or (root / "persona.md").is_symlink():
        raise PermissionError("Symlinked agent files are not editable")
    if not (root / "plugin.toml").is_file() or not (root / "persona.md").is_file():
        raise ValueError("Only local wizard-created agents can be edited")
    fd, tmp = tempfile.mkstemp(prefix=".persona-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, root / "persona.md")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return root / "persona.md"


def interactive_wizard(root: str) -> Path:
    """CLI wizard; TUI uses /agent create skeleton without blocking its event loop."""
    print("\nKITT Agent Creator — workspace-local, trust required after creation")
    slug = input("ID (ex: java-reviewer): ").strip().lower()
    print("Roles: code | creative | investigator | critic | tester")
    role = input("Specialty [investigator]: ").strip().lower() or "investigator"
    name = input("Display name [auto]: ").strip() or None
    persona = input("Behavior/persona [default]: ").strip() or None
    tags = tuple(t.strip() for t in input("Routing keywords (comma separated): ").split(",") if t.strip())
    return create_agent(root, slug, role=role, display_name=name, persona=persona, tags=tags)


def handle_agents_command(action: str, name: str | None, workspace: str, options) -> int:
    from kitt.extensions.manager import ExtensionManager
    ext = ExtensionManager(workspace_root=workspace)
    registry = AgentRegistry(ext)
    try:
        if action == "create":
            path = (create_agent(
                workspace, name,
                role=getattr(options, "role", "investigator"),
                persona=getattr(options, "persona", None),
                display_name=getattr(options, "display_name", None),
                tags=tuple((getattr(options, "tags", "") or "").split(",")) if getattr(options, "tags", "") else (),
            ) if name else interactive_wizard(workspace))
            print(f"Created {path}\nNext: kitt plugins trust {path.name}")
        elif action == "edit":
            if not name:
                raise ValueError("Agent slug required")
            persona = getattr(options, "persona", None)
            if persona is None:
                persona = input("New persona: ")
            path = edit_persona(workspace, name, persona)
            print(f"Updated {path}; re-trust plugin: kitt plugins trust agent-{name}")
        elif action in {"enable", "disable"}:
            if not name:
                raise ValueError("Agent id required")
            registry.set_enabled(name, action == "enable")
            print(f"{name}: {action}d")
        elif action == "select":
            if not name:
                raise ValueError("Task text required")
            profile = registry.select(name)
            print(f"{profile.id}: {profile.role} — {profile.description}")
        elif action == "inspect":
            if not name:
                raise ValueError("Agent id required")
            item = registry.find(name)
            print(f"Agent: {item.id}\nRole: {item.role}\nTools: {', '.join(item.tools)}"
                  f"\nBudget: {item.token_budget}\nTimeout: {item.timeout_seconds}s"
                  f"\nContract: {item.output_contract}\nPersona:\n{item.persona}")
        elif action == "list":
            print("\nKITT Specialist Agents")
            for item in registry.list():
                print(f"  {item.id:<32} {item.role:<10} {item.name}")
            print("\nManage: kitt agents create | inspect | enable | disable | edit | select")
        else:
            raise ValueError("Unknown agents action")
        return 0
    except (ValueError, OSError, PermissionError) as exc:
        print(f"Agent management failed: {exc}")
        return 2
