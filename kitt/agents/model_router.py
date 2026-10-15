"""Per-specialist model policy over the existing TaskRouter and ProviderCircuitPool.

No new provider, auth store, HTTP transport or reverse-proxy session is created.
A route is snapshotted at admission, so retained children keep their model
across isolated processes/worktrees and retries.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from typing import Any

from kitt.domain.entities import ModelProfile
from kitt.llm.privacy import profile_processing_is_local
from kitt.router.router import TaskRouter
from kitt.security.workspace_fs import WorkspaceFileSystem

_FILE = ".kitt-agent-models.json"
_MAX_BYTES = 65536
_MODES = frozenset({"auto", "preferred", "fixed"})
_EGRESS = frozenset({"inherit", "deny"})
_ROLE_HINTS = {
    "IMPLEMENT": ("coding", "code", "execute", "reasoning"),
    "ARCHITECT": ("creative", "reasoning", "execute", "context"),
    "DISCOVER": ("investigation", "research", "context", "execute"),
    "REVIEW": ("review", "critic", "validation", "reasoning", "execute"),
    "VERIFY": ("validation", "verify", "context", "execute"),
}
_ALLOWED_FIELDS = frozenset({
    "mode", "primary", "fallbacks", "temperature", "max_output_tokens",
    "require_tools", "remote_egress", "reasoning",
})


@dataclass(frozen=True)
class AgentLLMPolicy:
    mode: str = "auto"
    primary: str = ""
    fallbacks: tuple[str, ...] = ()
    temperature: float | None = None
    max_output_tokens: int | None = None
    require_tools: bool = False
    remote_egress: str = "inherit"
    reasoning: str = "auto"

    @classmethod
    def parse(cls, data: dict | None) -> AgentLLMPolicy:
        if data is None:
            return cls()
        if not isinstance(data, dict) or set(data) - _ALLOWED_FIELDS:
            raise ValueError("Unknown agent LLM policy field")
        mode = data.get("mode", "auto")
        primary = data.get("primary", "")
        raw_fallbacks = data.get("fallbacks", [])
        if mode not in _MODES or not isinstance(primary, str) or len(primary) > 64:
            raise ValueError("Invalid agent LLM mode or primary")
        if not isinstance(raw_fallbacks, (tuple, list)) or len(raw_fallbacks) > 8:
            raise ValueError("Agent LLM fallbacks require an array of at most 8 profiles")
        if not all(isinstance(p, str) and 1 <= len(p) <= 64 for p in raw_fallbacks):
            raise ValueError("Invalid agent LLM fallback profile")
        if mode == "fixed" and (not primary or raw_fallbacks):
            raise ValueError("Fixed mode requires a primary and no fallbacks")
        temp = data.get("temperature")
        if temp is not None and (type(temp) not in (float, int) or not 0 <= temp <= 2):
            raise ValueError("Agent temperature must be between 0 and 2")
        output = data.get("max_output_tokens")
        if output is not None and (type(output) is not int or not 64 <= output <= 32768):
            raise ValueError("Agent output limit must be between 64 and 32768")
        tools = data.get("require_tools", False)
        if type(tools) is not bool:
            raise ValueError("require_tools must be boolean")
        egress = data.get("remote_egress", "inherit")
        if egress not in _EGRESS:
            raise ValueError("remote_egress must be inherit or deny")
        reasoning = data.get("reasoning", "auto")
        if reasoning not in ("auto", "low", "medium", "high"):
            raise ValueError("Invalid reasoning preference")
        return cls(mode, primary, tuple(dict.fromkeys(raw_fallbacks)), temp,
                   output, tools, egress, reasoning)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fallbacks"] = list(self.fallbacks)
        return data


class AgentModelStore:
    """Local, bounded, atomic overrides. Never stores provider secrets."""

    def __init__(self, workspace: str | Path):
        self.root = Path(workspace).resolve()

    def load(self) -> dict[str, AgentLLMPolicy]:
        fs = WorkspaceFileSystem(self.root, max_file_bytes=_MAX_BYTES)
        try:
            raw = fs.read(_FILE, max_bytes=_MAX_BYTES).content
        except FileNotFoundError:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or len(data) > 128:
            raise ValueError("Invalid agent model preferences")
        if any(not isinstance(k, str) or len(k) > 128 for k in data):
            raise ValueError("Invalid agent IDs in model preferences")
        return {k: AgentLLMPolicy.parse(v) for k, v in data.items()}

    def save(self, agent: str, policy: AgentLLMPolicy) -> None:
        if not isinstance(agent, str) or not agent or len(agent) > 128:
            raise ValueError("Invalid agent ID")
        policies = self.load()
        policies[agent] = policy
        if len(policies) > 128:
            raise ValueError("Too many model bindings")
        content = json.dumps({k: v.as_dict() for k, v in sorted(policies.items())},
                             separators=(",", ":"), ensure_ascii=False)
        WorkspaceFileSystem(self.root, max_file_bytes=_MAX_BYTES).atomic_write(
            _FILE, content, max_bytes=_MAX_BYTES
        )


def _public_profile(profile: ModelProfile, policy: AgentLLMPolicy) -> dict[str, Any]:
    # Never marshal plaintext secrets into child DB records or process stdin.
    values = asdict(profile)
    values["api_key"] = ""
    if values.get("credential_ref") and not str(values["credential_ref"]).startswith(
        ("auth:", "env:", "session:")
    ):
        values["credential_ref"] = None
    if policy.temperature is not None:
        values["temperature"] = policy.temperature
    if policy.max_output_tokens is not None:
        values["max_output_tokens"] = min(
            policy.max_output_tokens, max(64, int(profile.context_window) - 512),
        )
    return values


def _safe(profile: ModelProfile, policy: AgentLLMPolicy, privacy_mode: str) -> bool:
    local = profile_processing_is_local(profile)
    if (privacy_mode in {"offline", "local_only"} or policy.remote_egress == "deny") and not local:
        return False
    if policy.require_tools and not profile.supports_tools:
        return False
    return True


class AgentModelRouter:
    def __init__(self, router: TaskRouter):
        self.router = router

    def resolve(self, role: str, policy: AgentLLMPolicy, *, privacy_mode: str) -> dict:
        configured = self.router.config.profiles
        role = str(role).upper()
        if role not in _ROLE_HINTS:
            raise ValueError("Unknown agent role")
        if policy.primary and policy.primary not in configured:
            raise ValueError(f"Unknown agent model profile: {policy.primary}")
        for key in policy.fallbacks:
            if key not in configured:
                raise ValueError(f"Unknown fallback model profile: {key}")
        # References are only to already configured TaskRouter profiles. No
        # plugin is allowed to install a new endpoint or inline a credential.
        hints = _ROLE_HINTS[role]
        eligible = [k for k, p in configured.items() if _safe(p, policy, privacy_mode)]
        if not eligible:
            raise PermissionError("No agent model satisfies privacy/capability policy")
        if policy.primary and policy.primary not in eligible:
            raise PermissionError("Selected agent model violates privacy/capability policy")
        if policy.mode == "fixed":
            order = [policy.primary]
        elif policy.mode == "preferred":
            order = [policy.primary] if policy.primary else []
            order.extend(policy.fallbacks)
            if not order:
                raise ValueError("Preferred mode requires a primary or fallbacks")
        else:
            order = [policy.primary] if policy.primary else []
            order.extend(policy.fallbacks)
            ranked = sorted(
                eligible,
                key=lambda k: (
                    next((i for i, hint in enumerate(hints) if hint in k.lower()), len(hints)),
                    -int(configured[k].context_window), k,
                ),
            )
            order.extend(ranked)
        unique = [k for k in dict.fromkeys(order) if k in eligible]
        if not unique:
            raise PermissionError("No configured model satisfies the agent's policy")
        primary = unique[0]
        fallbacks = unique[1:9]
        return {
            "primary": primary,
            "profile": _public_profile(configured[primary], policy),
            "fallbacks": [
                {"name": k, "profile": _public_profile(configured[k], policy)}
                for k in fallbacks
            ],
            "reasoning": policy.reasoning,
            "mode": policy.mode,
        }


def apply_child_model_route(runtime, request: dict) -> None:
    """Apply trusted persisted child admission decision before the first LLM turn."""
    route = (request.get("lineage") or {}).get("model_route")
    if not route:
        return
    if not isinstance(route, dict):
        raise ValueError("Invalid retained child route")
    primary = route.get("profile")
    name = route.get("primary")
    if not isinstance(primary, dict) or not isinstance(name, str):
        raise ValueError("Retained child missing selected model")
    allowed = set(ModelProfile.__dataclass_fields__)
    if set(primary) - allowed or primary.get("api_key"):
        raise ValueError("Child route cannot contain credentials")
    selected = ModelProfile(**primary)
    from kitt.llm.privacy import profile_processing_is_local
    privacy = str(runtime.config.privacy_mode)
    if privacy in {"offline", "local_only"} and not profile_processing_is_local(selected):
        raise PermissionError("Remote child route forbidden by privacy policy")
    fallbacks = []
    for item in route.get("fallbacks", []):
        if not isinstance(item, dict) or not isinstance(item.get("profile"), dict):
            raise ValueError("Invalid child route fallback")
        values = item["profile"]
        if set(values) - allowed or values.get("api_key"):
            raise ValueError("Child fallback cannot contain credentials")
        alternate = ModelProfile(**values)
        if privacy in {"offline", "local_only"} and not profile_processing_is_local(alternate):
            raise PermissionError("Remote child fallback forbidden")
        fallbacks.append(alternate)
    runtime.processor._specialist_execution = (name, selected)
    levels = {"low": 25, "medium": 50, "high": 90}
    if route.get("reasoning") in levels:
        runtime.processor.reasoning_effort = levels[route["reasoning"]]
    from kitt.llm.failover import ProviderCircuitPool
    pool = ProviderCircuitPool(runtime.config.provider_park_seconds)
    runtime.processor.provider_client_factory = lambda profile: pool.wrap(profile, fallbacks)
