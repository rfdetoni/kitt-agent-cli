"""Read-only, bounded recommendations for a workspace's existing toolchain."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def _regular(root: Path, relative: str) -> Path | None:
    """Never follow symlinks in workspace discovery."""
    target = root / relative
    return target if target.is_file() and not target.is_symlink() else None


def _read(root: Path, relative: str, limit: int = 131072) -> str:
    path = _regular(root, relative)
    if path is None or path.stat().st_size > limit:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""


def recommend(workspace: str | Path) -> dict[str, Any]:
    """Inspect only known manifests; no subprocesses, writes, network or secret files."""
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace is not a directory")
    signals: list[str] = []
    suggestions: list[dict[str, str]] = []

    def suggest(category: str, name: str, reason: str, action: str) -> None:
        suggestions.append({
            "category": category, "name": name,
            "reason": reason, "action": action,
        })

    pom = _read(root, "pom.xml")
    gradle = _read(root, "build.gradle") or _read(root, "build.gradle.kts")
    package = _read(root, "package.json")
    pyproject = _read(root, "pyproject.toml")
    rust = _regular(root, "Cargo.toml") is not None
    java = bool(pom or gradle)
    frontend = False

    if java:
        signals.append("java")
        if pom:
            signals.append("maven")
        if gradle:
            signals.append("gradle")
        if "spring-boot" in (pom + gradle).lower():
            signals.append("spring-boot")
            suggest("skills", "spring-boot-api-review",
                "Spring Boot manifests detected",
                "Use existing API/security review tools for Spring routes and authorization")
        suggest("hooks", "java-targeted-checks",
            "Java build detected",
            "Run impacted tests through the existing KITT test-impact plugin after edits")

    if package:
        signals.append("node")
        try:
            metadata = json.loads(package)
            deps = {
                **(metadata.get("dependencies") or {}),
                **(metadata.get("devDependencies") or {}),
            } if isinstance(metadata, dict) else {}
            if any(name in deps for name in ("@angular/core", "react", "vue")):
                frontend = True
        except (ValueError, TypeError):
            pass

    if frontend:
        signals.append("frontend")
        suggest("mcp", "browser-testing",
            "Frontend dependencies detected",
            "Use KITT browser tools or install the existing Playwright MCP integration")
    if pyproject:
        signals.append("python")
    if rust:
        signals.append("rust")
    if _regular(root, "compose.yaml") or _regular(root, "docker-compose.yml"):
        signals.append("docker-compose")
        suggest("mcp", "container-inspection",
            "Compose file detected",
            "Use the existing KITT container plugin before adding an MCP server")
    if (root / ".github" / "workflows").is_dir() and not (root / ".github" / "workflows").is_symlink():
        signals.append("github-actions")
        suggest("hooks", "ci-parity",
            "GitHub Actions workflows detected",
            "Check the repository CI tasks with KITT quality-report before opening a PR")

    suggest("memory", "kitt-memoryd",
        "KITT already provides durable scoped memory and progressive recall",
        "Keep the existing KITT memory service; do not install a competing default memory daemon")
    suggest("providers", "freellmapi-optional",
        "An OpenAI-compatible gateway can add quotas and fallback without a second KITT router",
        "Optionally self-host FreeLLMAPI and configure it as an OpenAI-compatible profile")
    suggest("security", "strix-optional",
        "Dynamic pentests are separate from dependency/static checks",
        "Install strix-agent, then invoke kitt security strix scan --target PATH --authorized")
    if "spring-boot" in signals:
        suggest("subagents", "security-reviewer",
            "Backend routes and authorization deserve focused review",
            "Use a scoped KITT security reviewer for authentication and tenancy boundaries")

    return {
        "workspace": str(root), "signals": sorted(set(signals)),
        "tools": {"strix": bool(shutil.which("strix"))},
        "recommendations": suggestions,
        "read_only": True,
    }
