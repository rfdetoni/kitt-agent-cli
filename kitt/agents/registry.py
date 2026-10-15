"""Declarative agent profiles, backed by trusted plugin snapshots.

An agent persona is *data*, never a capability grant or executable plugin.
The existing ChildAgentManager, tool policy, goal budget and worktree isolation
are always the execution authorities.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

from kitt.agents.model_router import AgentLLMPolicy
from kitt.extensions.manager import ExtensionManager
from kitt.extensions.plugins.security import prepare_trusted_plugin_snapshot

_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_ROLES = frozenset({"DISCOVER", "ARCHITECT", "IMPLEMENT", "VERIFY", "REVIEW"})
_READ = ("read_file", "search", "repository_map", "git_status", "git_diff", "kitt_runtime", "children_send")
_EDIT = ("read_file", "search", "repository_map", "write_file", "apply_patch", "kitt_runtime", "children_send")
_ALLOWED = frozenset((*_READ, "write_file", "apply_patch", "artifact_read", "memory_recall"))
_DEFAULTS = (
    ("code", "Code Worker", "IMPLEMENT", "Implement small, correct changes; run no shell commands. Return changes and tests.", ("code","implement","feature","fix","refactor","java","rust","python","angular"), False),
    ("creative", "Creative Worker", "ARCHITECT", "Generate multiple pragmatic alternatives, evaluate tradeoffs and choose simplest.", ("design","idea","creative","brainstorm","architecture","alternatives"), True),
    ("investigator", "Investigator", "DISCOVER", "Form testable hypotheses; inspect evidence, traces and reproduction. Never guess.", ("bug","investigate","debug","logs","failure","diagnose","root cause"), True),
    ("critic", "Critic Reviewer", "REVIEW", "Review changes independently; report severity, location, proof and minimal fix. No invented findings.", ("review","critique","security","audit","code review"), True),
    ("tester", "Test Worker", "VERIFY", "Design verification cases and identify missing regression coverage; report evidence.", ("test","verify","validation","regression","coverage"), True),
)


@dataclass(frozen=True)
class AgentProfile:
    id: str
    plugin: str
    name: str
    role: str
    description: str
    persona: str
    tools: tuple[str, ...]
    token_budget: int
    timeout_seconds: int
    output_contract: str
    tags: tuple[str, ...]
    read_only: bool
    llm: "AgentLLMPolicy"

    def spawn_args(self, task: str, *, paths: tuple[str, ...] = ()) -> dict:
        if not task.strip() or len(task) > 12000:
            raise ValueError("Agent task must contain 1..12000 characters")
        if not self.read_only and not paths:
            raise ValueError("Implementer requires explicit allowed paths")
        # This is task-scoped context, not a system instruction or security grant.
        prompt = (
            f"[SPECIALIST PROFILE: {self.id}]\n{self.persona}\n"
            f"[OUTPUT CONTRACT: {self.output_contract}]\n"
            "Return verifiable results; never claim tools/tests were used without evidence.\n"
            f"[ASSIGNED TASK]\n{task}"
        )
        return {
            "name": self.id, "task": prompt, "agent_role": self.role,
            "allowed_paths": list(paths) if not self.read_only else [],
            "enabled_tools": list(self.tools),
            "token_budget": self.token_budget,
            "timeout_seconds": self.timeout_seconds,
        }


def _profile(plugin: str, slug: str, raw: dict, root: Path | None) -> AgentProfile:
    if not _SLUG.fullmatch(slug):
        raise ValueError(f"Invalid agent name: {slug}")
    role = str(raw.get("role", "")).upper()
    if role not in _ROLES:
        raise ValueError(f"Invalid role for {slug}: {role}")
    read_only = raw.get("read_only", role != "IMPLEMENT")
    if not isinstance(read_only, bool) or (role != "IMPLEMENT" and not read_only):
        raise ValueError("Only IMPLEMENT role can request write access")
    tools = raw.get("tools", list(_READ if read_only else _EDIT))
    if not isinstance(tools, list) or len(tools) > 16 or not all(isinstance(v, str) for v in tools):
        raise ValueError("Agent tools must be an array of at most 16 names")
    if not set(tools) <= _ALLOWED or (read_only and not set(tools) <= set(_READ) | {"artifact_read", "memory_recall"}):
        raise ValueError("Agent tools exceed the allowlisted role capabilities")
    persona = raw.get("persona", "")
    if not isinstance(persona, str):
        raise ValueError("Persona must be text")
    persona_file = raw.get("persona_file")
    if persona_file is not None:
        if not isinstance(persona_file, str) or root is None:
            raise ValueError("Persona file requires a trusted plugin snapshot")
        relative = Path(persona_file)
        if relative.is_absolute() or not relative.parts or any(p in (".", "..") for p in relative.parts):
            raise ValueError("Invalid persona path")
        candidate = root.joinpath(*relative.parts)
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):
            raise ValueError("Persona escapes trusted snapshot")
        if candidate.stat().st_size > 8192:
            raise ValueError("Persona is too large")
        persona = candidate.read_text(encoding="utf-8")
    if not persona.strip() or len(persona) > 8192:
        raise ValueError("Persona must contain 1..8192 characters")
    budget, timeout = raw.get("max_tokens", 4096), raw.get("timeout_seconds", 120)
    if (type(budget) is not int or not 512 <= budget <= 32768
            or type(timeout) is not int or not 10 <= timeout <= 600):
        raise ValueError("Agent budget or timeout out of range")
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 24 or not all(isinstance(x, str) and 1 <= len(x) <= 64 for x in tags):
        raise ValueError("Invalid agent tags")
    contract = raw.get("output_contract", "agent.evidence.v1")
    if not isinstance(contract, str) or not _SLUG.fullmatch(contract.replace(".", "_")):
        raise ValueError("Invalid output contract")
    return AgentProfile(
        id=slug if plugin == "kitt-core" else f"{plugin}:{slug}",
        plugin=plugin, name=str(raw.get("name", slug))[:100], role=role,
        description=str(raw.get("description", ""))[:250], persona=persona,
        tools=tuple(dict.fromkeys(tools)), token_budget=budget,
        timeout_seconds=timeout, output_contract=contract, tags=tuple(tags),
        read_only=read_only,
        llm=AgentLLMPolicy.parse(raw.get("llm")),
    )


class AgentRegistry:
    """Discovery uses plugin enablement + hash trust; no separate plugin scheduler."""

    def __init__(self, extensions: ExtensionManager):
        self.extensions = extensions

    def list(self) -> list[AgentProfile]:
        profiles: list[AgentProfile] = []
        for slug, name, role, persona, tags, read_only in _DEFAULTS:
            raw = {
                "name": name, "role": role, "persona": persona, "tags": list(tags),
                "read_only": read_only,
                "output_contract": "review.findings.v1" if slug == "critic" else "agent.evidence.v1",
            }
            profiles.append(_profile("kitt-core", slug, raw, None))
        manifests = self.extensions.plugins.discover()
        for plugin_id, manifest in sorted(manifests.items()):
            if "agents.register" not in manifest.permissions:
                continue
            if not self.extensions.plugins.is_enabled(plugin_id, manifest):
                continue
            try:
                digest = self.extensions.plugin_trust.approved_digest(manifest)
                if digest is None:
                    continue
                root = prepare_trusted_plugin_snapshot(
                    manifest, digest, self.extensions.workspace_root,
                )
                doc = tomllib.loads((root / "plugin.toml").read_text(encoding="utf-8"))
                agents = doc.get("agents", {})
                if not agents:
                    continue
                if "agents.register" not in manifest.permissions or not isinstance(agents, dict):
                    continue
                if len(agents) > 32:
                    continue
                for slug, raw in agents.items():
                    if not isinstance(raw, dict):
                        continue
                    try:
                        profiles.append(_profile(plugin_id, slug, raw, root))
                    except (OSError, ValueError, UnicodeError):
                        continue
            except Exception:
                # Invalid/revoked plugins never block the bundled specialists.
                continue
        enabled, disabled = self.extensions.plugin_state.load()
        return [p for p in profiles if f"agent:{p.id}" not in disabled]

    def find(self, name: str) -> AgentProfile:
        key = str(name).strip().lower()
        matches = [p for p in self.list() if p.id == key]
        if not matches:
            raise ValueError(f"Agent unavailable, untrusted or disabled: {key}")
        return matches[0]

    def set_enabled(self, name: str, enabled: bool) -> None:
        # Only agents in trusted/discovered sources can be manipulated.
        profile = next((p for p in self.list() if p.id == name), None)
        if profile is None and enabled:
            # An explicitly disabled profile is absent from the list; recover
            # only after validating its source again, never blindly add grants.
            _enabled, disabled = self.extensions.plugin_state.load()
            if f"agent:{name}" not in disabled:
                raise ValueError(f"Unknown agent: {name}")
            self.extensions.plugin_state.set_enabled(f"agent:{name}", True)
            try:
                self.find(name)
            except ValueError:
                self.extensions.plugin_state.set_enabled(f"agent:{name}", False)
                raise
            return
        if profile is None:
            raise ValueError(f"Unknown agent: {name}")
        self.extensions.plugin_state.set_enabled(f"agent:{profile.id}", enabled)

    def select(self, task: str, *, require_write: bool = False) -> AgentProfile:
        text = str(task).lower()
        candidates = [p for p in self.list() if not require_write or not p.read_only]
        if not candidates:
            raise ValueError("No enabled agent has the required capabilities")
        scores = [
            (sum(1 for tag in profile.tags if tag.lower() in text), profile)
            for profile in candidates
        ]
        scores.sort(key=lambda item: (-item[0], item[1].id))
        if scores[0][0]:
            return scores[0][1]
        preferred = "code" if require_write else "investigator"
        return next((p for p in candidates if p.id == preferred), scores[0][1])
