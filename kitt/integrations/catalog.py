from __future__ import annotations

import importlib.util
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable, Tuple


@dataclass(frozen=True)
class IntegrationSpec:
    id: str
    category: str
    description: str
    commands: Tuple[str, ...] = ()
    python_modules: Tuple[str, ...] = ()
    capabilities: Tuple[str, ...] = ()
    mutating: bool = False
    recommended: bool = False


@dataclass(frozen=True)
class IntegrationStatus:
    spec: IntegrationSpec
    available: bool
    command: str | None = None
    module: str | None = None
    version: str | None = None


_SPECS: tuple[IntegrationSpec, ...] = (
    IntegrationSpec(
        "ast-grep", "semantic-code", "Structural AST search/rewrite backend",
        commands=("ast-grep",), capabilities=("repo.ast_search", "repo.ast_rewrite"),
        mutating=True, recommended=True,
    ),
    IntegrationSpec(
        "serena", "semantic-code", "LSP-backed semantic coding/MCP backend",
        commands=("serena",), python_modules=("serena",),
        capabilities=("repo.definition", "repo.hover", "repo.references_semantic", "repo.diagnostics"),
        recommended=True,
    ),
    IntegrationSpec(
        "pyright", "lsp", "Python language server",
        commands=("pyright-langserver",), capabilities=("repo.lsp",), recommended=True,
    ),
    IntegrationSpec(
        "typescript-language-server", "lsp", "TypeScript/JavaScript language server",
        commands=("typescript-language-server",), capabilities=("repo.lsp",), recommended=True,
    ),
    IntegrationSpec(
        "rust-analyzer", "lsp", "Rust language server",
        commands=("rust-analyzer",), capabilities=("repo.lsp",), recommended=True,
    ),
    IntegrationSpec(
        "gopls", "lsp", "Go language server",
        commands=("gopls",), capabilities=("repo.lsp",),
    ),
    IntegrationSpec(
        "jdtls", "lsp", "Java language server",
        commands=("jdtls",), capabilities=("repo.lsp",), recommended=True,
    ),
    IntegrationSpec(
        "semgrep", "security", "Static analysis and security gate",
        commands=("semgrep",), python_modules=("semgrep",), capabilities=("security.scan",),
        recommended=True,
    ),
    IntegrationSpec(
        "codex", "external-agent", "OpenAI Codex CLI child-agent backend",
        commands=("codex",), capabilities=("child.external"), recommended=True,
    ),
    IntegrationSpec(
        "claude-code", "external-agent", "Claude Code child-agent backend",
        commands=("claude",), capabilities=("child.external"), recommended=True,
    ),
    IntegrationSpec(
        "openhands", "external-agent", "OpenHands agent/SDK backend",
        commands=("openhands",), python_modules=("openhands",), capabilities=("child.external",),
        recommended=True,
    ),
    IntegrationSpec(
        "opencode", "external-agent", "OpenCode CLI child-agent backend",
        commands=("opencode",), capabilities=("child.external",),
    ),
    IntegrationSpec(
        "aider", "external-agent", "Aider CLI child-agent backend",
        commands=("aider",), python_modules=("aider",), capabilities=("child.external",),
    ),
    IntegrationSpec(
        "gemini-cli", "external-agent", "Gemini CLI child-agent backend",
        commands=("gemini",), capabilities=("child.external",),
    ),
    IntegrationSpec(
        "prime-agent", "external-agent", "Prime Intellect agent backend",
        commands=("prime-agent", "prime"), capabilities=("child.external",),
    ),
    IntegrationSpec(
        "litellm", "model-gateway", "Optional API-provider routing gateway",
        commands=("litellm",), python_modules=("litellm",), capabilities=("model.gateway",),
        recommended=True,
    ),
    IntegrationSpec(
        "inspect-ai", "evaluation", "Agent capability/evaluation harness",
        commands=("inspect",), python_modules=("inspect_ai",), capabilities=("eval.agent",),
        recommended=True,
    ),
    IntegrationSpec(
        "promptfoo", "evaluation", "Coding-agent red-team and regression harness",
        commands=("promptfoo",), capabilities=("eval.redteam",), recommended=True,
    ),
    IntegrationSpec(
        "opentelemetry", "observability", "OpenTelemetry tracing bridge",
        python_modules=("opentelemetry",), capabilities=("telemetry.otlp",), recommended=True,
    ),
    IntegrationSpec(
        "langfuse", "observability", "Optional tracing/evaluation sink",
        python_modules=("langfuse",), capabilities=("telemetry.langfuse",),
    ),
)


class IntegrationCatalog:
    """Zero-dependency registry for optional KITT ecosystem capabilities.

    Probing never installs packages and never invokes a shell. Version probing
    is opt-in and bounded so `kitt doctor` can safely expose what is available.
    """

    def __init__(self, specs: Iterable[IntegrationSpec] | None = None):
        self.specs = tuple(specs or _SPECS)

    def get(self, integration_id: str) -> IntegrationSpec | None:
        wanted = str(integration_id or "").strip().lower()
        return next((spec for spec in self.specs if spec.id == wanted), None)

    @staticmethod
    def _module_available(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    @staticmethod
    def _version(command: str) -> str | None:
        executable = shutil.which(command)
        if not executable:
            return None
        for flag in ("--version", "-V", "version"):
            try:
                result = subprocess.run(
                    [executable, flag],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            line = (result.stdout or "").strip().splitlines()
            if line:
                return line[0][:256]
        return None

    def probe(self, spec: IntegrationSpec | str, *, include_version: bool = False) -> IntegrationStatus:
        if isinstance(spec, str):
            resolved = self.get(spec)
            if resolved is None:
                raise KeyError(f"Unknown integration: {spec}")
            spec = resolved

        command = next((name for name in spec.commands if shutil.which(name)), None)
        module = next((name for name in spec.python_modules if self._module_available(name)), None)
        available = command is not None or module is not None
        version = self._version(command) if include_version and command else None
        return IntegrationStatus(spec, available, command=command, module=module, version=version)

    def probe_all(self, *, include_versions: bool = False) -> list[IntegrationStatus]:
        return [self.probe(spec, include_version=include_versions) for spec in self.specs]

    def available(self, category: str | None = None) -> list[IntegrationStatus]:
        statuses = self.probe_all()
        return [
            status for status in statuses
            if status.available and (category is None or status.spec.category == category)
        ]

    def capability_index(self) -> dict[str, tuple[str, ...]]:
        index: dict[str, list[str]] = {}
        for spec in self.specs:
            for capability in spec.capabilities:
                index.setdefault(capability, []).append(spec.id)
        return {key: tuple(values) for key, values in sorted(index.items())}
