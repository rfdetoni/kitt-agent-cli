"""Global-first formatting contracts with compact project overrides."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from kitt.security.private_state import (
    InterProcessFileLock,
    kitt_home,
    secure_read_json,
    secure_write_json,
)

FORMAT_CONTRACT_VERSION = 2
GLOBAL_FORMATTING_VERSION = 1
CONTRACT_RELATIVE_PATH = ".kitt/formatting.json"
STATE_RELATIVE_PATH = ".kitt/formatting.state.json"
GLOBAL_BASELINES_DISPLAY_PATH = "~/.kitt/formatting/baselines.json"

_LANGUAGE_SPECS: dict[str, dict[str, Any]] = {
    "python": {"extensions": [".py", ".pyi"], "formatters": ["ruff", "black"], "indent": 4},
    "java": {"extensions": [".java"], "formatters": ["google-java-format"], "indent": 4},
    "kotlin": {"extensions": [".kt", ".kts"], "formatters": ["ktlint"], "indent": 4},
    "javascript": {"extensions": [".js", ".jsx", ".mjs", ".cjs"], "formatters": ["biome", "prettier"], "indent": 2},
    "typescript": {"extensions": [".ts", ".tsx"], "formatters": ["biome", "prettier"], "indent": 2},
    "go": {"extensions": [".go"], "formatters": ["gofmt"], "indent": "tab"},
    "rust": {"extensions": [".rs"], "formatters": ["rustfmt"], "indent": 4},
    "c": {"extensions": [".c", ".h"], "formatters": ["clang-format"], "indent": 4},
    "cpp": {"extensions": [".cc", ".cpp", ".cxx", ".hh", ".hpp"], "formatters": ["clang-format"], "indent": 4},
    "csharp": {"extensions": [".cs"], "formatters": ["clang-format"], "indent": 4},
    "swift": {"extensions": [".swift"], "formatters": ["swift-format"], "indent": 4},
    "dart": {"extensions": [".dart"], "formatters": ["dart-format"], "indent": 2},
    "zig": {"extensions": [".zig"], "formatters": ["zig-fmt"], "indent": 4},
    "php": {"extensions": [".php"], "formatters": ["prettier"], "indent": 4},
    "ruby": {"extensions": [".rb"], "formatters": ["rubocop"], "indent": 2},
    "shell": {"extensions": [".sh", ".bash", ".zsh"], "formatters": ["shfmt"], "indent": 2},
    "lua": {"extensions": [".lua"], "formatters": ["stylua"], "indent": 2},
    "json": {"extensions": [".json"], "formatters": ["biome", "prettier"], "indent": 2},
    "yaml": {"extensions": [".yaml", ".yml"], "formatters": ["prettier"], "indent": 2},
    "html": {"extensions": [".html", ".htm", ".xhtml", ".vue", ".svelte"], "formatters": ["prettier"], "indent": 2},
    "css": {"extensions": [".css", ".scss", ".less"], "formatters": ["biome", "prettier"], "indent": 2},
    "xml": {"extensions": [".xml", ".svg"], "formatters": [], "indent": 2},
    "sql": {"extensions": [".sql"], "formatters": [], "indent": 2},
}

_PROJECT_SIGNAL_FILES = (
    ".editorconfig", ".prettierrc", ".prettierrc.json", "prettier.config.js",
    "prettier.config.mjs", "biome.json", "biome.jsonc", ".clang-format",
    "pyproject.toml", "ruff.toml", "rustfmt.toml", "pom.xml",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "package.json", "go.mod", "Cargo.toml", "pubspec.yaml",
)


def _deep_merge(*values: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if isinstance(item, dict) and isinstance(result.get(key), dict):
                result[key] = _deep_merge(result[key], item)
            else:
                result[key] = deepcopy(item)
    return result


def _dict_diff(value: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key not in baseline:
            result[key] = deepcopy(item)
            continue
        base = baseline[key]
        if isinstance(item, dict) and isinstance(base, dict):
            nested = _dict_diff(item, base)
            if nested:
                result[key] = nested
        elif item != base:
            result[key] = deepcopy(item)
    return result


def _base_language_profile(language: str, spec: dict[str, Any]) -> dict[str, Any]:
    default_indent = spec["indent"]
    tabs = default_indent == "tab"
    return {
        "extensions": list(spec["extensions"]),
        "parser": {"engine": "tree-sitter", "language": language},
        "formatter_order": list(spec["formatters"]),
        "style": {
            "indent_style": "tabs" if tabs else "spaces",
            "indent_size": "tab" if tabs else int(default_indent),
            "final_newline": True,
        },
        "healing": {"enabled": True, "preserve_semantics": True},
    }


def _builtin_global_document() -> dict[str, Any]:
    return {
        "version": GLOBAL_FORMATTING_VERSION,
        "managed_by": "kitt-agent-cli",
        "languages": {
            language: _base_language_profile(language, spec)
            for language, spec in _LANGUAGE_SPECS.items()
        },
        "usage": {},
    }


def _json_file(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _yaml_file(path: Path) -> dict[str, Any] | None:
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _editorconfig_style(root: Path) -> dict[str, Any]:
    path = root / ".editorconfig"
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    style: dict[str, Any] = {}
    match = re.search(r"(?mi)^\s*indent_style\s*=\s*(space|tab)\s*$", text)
    if match:
        style["indent_style"] = "tabs" if match.group(1).lower() == "tab" else "spaces"
    match = re.search(r"(?mi)^\s*indent_size\s*=\s*(\d+|tab)\s*$", text)
    if match:
        raw = match.group(1).lower()
        style["indent_size"] = raw if raw == "tab" else max(1, min(int(raw), 16))
    match = re.search(r"(?mi)^\s*insert_final_newline\s*=\s*(true|false)\s*$", text)
    if match:
        style["final_newline"] = match.group(1).lower() == "true"
    return style


def _signal_fingerprint(root: Path) -> tuple[str, list[str]]:
    digest = hashlib.sha256()
    found: list[str] = []
    for name in _PROJECT_SIGNAL_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        found.append(name)
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()[:24], found


def language_for_path(path: str) -> str | None:
    suffix = Path(path).suffix.casefold()
    for language, spec in _LANGUAGE_SPECS.items():
        if suffix in spec["extensions"]:
            return language
    try:
        from tree_sitter_language_pack import detect_language_from_path
        detected = detect_language_from_path(path)
        return str(detected) if detected else None
    except Exception:
        return None


class GlobalFormattingRegistry:
    """Trusted user-global formatter baselines shared by every KITT workspace."""

    def __init__(self):
        self.root = kitt_home() / "formatting"
        self.path = self.root / "baselines.json"
        self._cached: dict[str, Any] | None = None

    @staticmethod
    def _valid(document: Any) -> bool:
        return (
            isinstance(document, dict)
            and int(document.get("version", 0) or 0) == GLOBAL_FORMATTING_VERSION
            and isinstance(document.get("languages"), dict)
        )

    def ensure(self) -> dict[str, Any]:
        if self._cached is not None:
            return self._cached

        current = secure_read_json(self.path, default=None)
        builtin = _builtin_global_document()
        if self._valid(current):
            missing = set(builtin["languages"]) - set(current["languages"])
            if not missing:
                self._cached = current
                return current

        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with InterProcessFileLock(lock_path):
            document = secure_read_json(self.path, default=None)
            if not isinstance(document, dict):
                document = deepcopy(builtin)
            document["version"] = GLOBAL_FORMATTING_VERSION
            document.setdefault("managed_by", "kitt-agent-cli")
            languages = document.setdefault("languages", {})
            if not isinstance(languages, dict):
                document["languages"] = languages = {}
            for language, profile in builtin["languages"].items():
                languages.setdefault(language, deepcopy(profile))
            if not isinstance(document.get("usage"), dict):
                document["usage"] = {}
            secure_write_json(self.path, document)
            self._cached = deepcopy(document)
        return self._cached or builtin

    def language_profile(self, language: str) -> dict[str, Any]:
        document = self.ensure()
        languages = document.get("languages")
        profile = (
            deepcopy(languages.get(language, {}))
            if isinstance(languages, dict)
            else {}
        )
        if not isinstance(profile, dict):
            return {}

        usage = document.get("usage")
        language_usage = usage.get(language, {}) if isinstance(usage, dict) else {}
        successes = (
            language_usage.get("formatter_successes", {})
            if isinstance(language_usage, dict)
            else {}
        )
        order = profile.get("formatter_order")
        if isinstance(order, list) and isinstance(successes, dict):
            indexed = {str(name): index for index, name in enumerate(order)}
            profile["formatter_order"] = sorted(
                [str(name) for name in order],
                key=lambda name: (-int(successes.get(name, 0) or 0), indexed.get(name, 999)),
            )
        return profile

    def remember_formatter_success(self, language: str, formatter_id: str) -> None:
        base = self.language_profile(language)
        allowed = base.get("formatter_order")
        if not isinstance(allowed, list) or formatter_id not in allowed:
            return
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with InterProcessFileLock(lock_path):
            document = secure_read_json(
                self.path, default=_builtin_global_document()
            )
            if not isinstance(document, dict):
                document = _builtin_global_document()
            usage = document.setdefault("usage", {})
            if not isinstance(usage, dict):
                document["usage"] = usage = {}
            language_usage = usage.setdefault(language, {})
            if not isinstance(language_usage, dict):
                usage[language] = language_usage = {}
            successes = language_usage.setdefault("formatter_successes", {})
            if not isinstance(successes, dict):
                language_usage["formatter_successes"] = successes = {}
            successes[formatter_id] = int(successes.get(formatter_id, 0) or 0) + 1
            language_usage["preferred_formatter"] = max(
                successes,
                key=lambda name: int(successes.get(name, 0) or 0),
            )
            secure_write_json(self.path, document)
            self._cached = deepcopy(document)


class FormattingContractManager:
    """Resolve global formatter baselines plus compact project-specific overrides."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.contract_path = self.root / CONTRACT_RELATIVE_PATH
        self.state_path = self.root / STATE_RELATIVE_PATH
        self.global_registry = GlobalFormattingRegistry()
        self._cached: dict[str, Any] | None = None

    def _safe_kitt_path(self, path: Path) -> bool:
        kitt_dir = self.root / ".kitt"
        try:
            if kitt_dir.is_symlink() or path.is_symlink():
                return False
            path.resolve(strict=False).relative_to(self.root)
            return True
        except (OSError, ValueError):
            return False

    def _existing(self) -> tuple[dict[str, Any] | None, Path | None]:
        candidates = (
            self.contract_path,
            self.root / ".kitt" / "formatting.yaml",
            self.root / ".kitt" / "formatting.yml",
        )
        for path in candidates:
            if not self._safe_kitt_path(path) or not path.is_file():
                continue
            value = _json_file(path) if path.suffix.casefold() == ".json" else _yaml_file(path)
            if value is not None:
                return value, path
        return None, None

    def _generated(self) -> dict[str, Any]:
        fingerprint, signals = _signal_fingerprint(self.root)
        project_style = _editorconfig_style(self.root)
        result: dict[str, Any] = {
            "version": FORMAT_CONTRACT_VERSION,
            "managed_by": "kitt-agent-cli",
            "baseline": {
                "scope": "user-global",
                "version": GLOBAL_FORMATTING_VERSION,
                "path": GLOBAL_BASELINES_DISPLAY_PATH,
            },
            "auto_heal": {"enabled": True, "max_attempts": 2, "llm_fallback": True},
            "project_defaults_source": "discovery",
            "languages": {},
            "discovery": {"source_fingerprint": fingerprint, "signals": signals},
        }
        if project_style:
            result["project_defaults"] = {"style": project_style}
        return result

    def _sanitize_v2(self, contract: dict[str, Any]) -> dict[str, Any]:
        generated = self._generated()
        result = dict(contract)
        was_managed = result.get("managed_by") == "kitt-agent-cli"
        result["version"] = FORMAT_CONTRACT_VERSION
        result["baseline"] = generated["baseline"]
        result.setdefault("managed_by", "kitt-agent-cli")
        if not isinstance(result.get("languages"), dict):
            result["languages"] = {}
        if not isinstance(result.get("auto_heal"), dict):
            result["auto_heal"] = generated["auto_heal"]

        defaults_source = str(result.get("project_defaults_source", "") or "")
        if was_managed and defaults_source in {"", "discovery"}:
            if "project_defaults" in generated:
                result["project_defaults"] = generated["project_defaults"]
            else:
                result.pop("project_defaults", None)
            result["project_defaults_source"] = "discovery"
        elif not isinstance(result.get("project_defaults"), dict):
            result.pop("project_defaults", None)
            result.pop("project_defaults_source", None)

        result["discovery"] = generated["discovery"]
        return result

    def _migrate_v1(self, contract: dict[str, Any]) -> dict[str, Any]:
        generated = self._generated()
        project_defaults = (
            generated.get("project_defaults")
            if isinstance(generated.get("project_defaults"), dict)
            else {}
        )
        overrides: dict[str, Any] = {}
        languages = contract.get("languages")
        if isinstance(languages, dict):
            for language, value in languages.items():
                if not isinstance(value, dict):
                    continue
                reference = _deep_merge(
                    self.global_registry.language_profile(str(language)),
                    project_defaults,
                )
                difference = _dict_diff(value, reference)
                if difference:
                    overrides[str(language)] = difference
        generated["languages"] = overrides
        if isinstance(contract.get("auto_heal"), dict):
            generated["auto_heal"] = dict(contract["auto_heal"])
        return generated

    def _persist(self, contract: dict[str, Any]) -> None:
        if not self._safe_kitt_path(self.contract_path):
            raise OSError("unsafe .kitt formatting contract path")
        self.contract_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.contract_path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, self.contract_path)

    def ensure(self) -> dict[str, Any]:
        if self._cached is not None:
            return self._cached

        self.global_registry.ensure()
        existing, source_path = self._existing()
        needs_persist = source_path != self.contract_path
        if existing is None:
            contract = self._generated()
            needs_persist = True
        elif int(existing.get("version", 0) or 0) < FORMAT_CONTRACT_VERSION:
            contract = self._migrate_v1(existing)
            needs_persist = True
        else:
            contract = self._sanitize_v2(existing)
            needs_persist = needs_persist or contract != existing

        if needs_persist:
            try:
                self._persist(contract)
            except OSError:
                pass

        self._cached = contract
        self._write_state(contract)
        return contract

    def _write_state(self, contract: dict[str, Any]) -> None:
        try:
            if not self._safe_kitt_path(self.state_path):
                return
            fingerprint, signals = _signal_fingerprint(self.root)
            state = {
                "version": FORMAT_CONTRACT_VERSION,
                "source_fingerprint": fingerprint,
                "signals": signals,
                "contract": self.contract_path.relative_to(self.root).as_posix(),
                "global_baseline": GLOBAL_BASELINES_DISPLAY_PATH,
                "available_formatters": self.available_formatters(),
                "project_override_languages": sorted(
                    contract.get("languages", {}).keys()
                    if isinstance(contract.get("languages"), dict)
                    else []
                ),
            }
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".json.tmp")
            temp.write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temp, self.state_path)
        except OSError:
            return

    @staticmethod
    def available_formatters() -> list[str]:
        probes = {
            "ruff": "ruff", "black": "black", "google-java-format": "google-java-format",
            "clang-format": "clang-format", "ktlint": "ktlint", "biome": "biome",
            "prettier": "prettier", "gofmt": "gofmt", "rustfmt": "rustfmt",
            "swift-format": "swift-format", "dart-format": "dart", "zig-fmt": "zig",
            "shfmt": "shfmt", "stylua": "stylua", "rubocop": "rubocop",
        }
        return sorted(name for name, executable in probes.items() if shutil.which(executable))

    def language_contract(self, path: str) -> tuple[str | None, dict[str, Any]]:
        contract = self.ensure()
        language = language_for_path(path)
        if not language:
            return None, {}

        global_base = self.global_registry.language_profile(language)
        project_defaults = (
            contract.get("project_defaults")
            if isinstance(contract.get("project_defaults"), dict)
            else {}
        )
        languages = contract.get("languages")
        override = (
            languages.get(language, {})
            if isinstance(languages, dict)
            else {}
        )
        if not isinstance(override, dict):
            override = {}
        return language, _deep_merge(global_base, project_defaults, override)

    def remember_formatter_success(self, language: str | None, formatter_id: str | None) -> None:
        if language and formatter_id:
            self.global_registry.remember_formatter_success(language, formatter_id)

    @staticmethod
    def _override_row(language: str, value: dict[str, Any]) -> str:
        parts: list[str] = []
        style = value.get("style") if isinstance(value.get("style"), dict) else {}
        if style:
            indent_style = style.get("indent_style", "preserve")
            indent_size = style.get("indent_size", "preserve")
            parts.append(f"indent={indent_style}:{indent_size}")
            if "final_newline" in style:
                parts.append(f"final_newline={str(bool(style['final_newline'])).lower()}")
        order = value.get("formatter_order")
        if isinstance(order, list):
            parts.append("formatters=" + ",".join(str(item) for item in order[:4]))
        parser = value.get("parser")
        if isinstance(parser, dict) and parser.get("engine"):
            parts.append(f"parser={parser['engine']}")
        return f"{language}: " + "; ".join(parts) if parts else language

    def prompt_summary(
        self,
        *,
        paths: Iterable[str] | None = None,
        max_overrides: int = 8,
    ) -> str:
        """Emit only project deltas; global language defaults stay out of model tokens."""
        contract = self.ensure()
        lines = [
            f"baseline={GLOBAL_BASELINES_DISPLAY_PATH}; local={CONTRACT_RELATIVE_PATH}; "
            "policy=global-first/delta-only; KITT formats and validates mutations before LLM repair."
        ]

        project_defaults = (
            contract.get("project_defaults")
            if isinstance(contract.get("project_defaults"), dict)
            else {}
        )
        project_style = (
            project_defaults.get("style")
            if isinstance(project_defaults.get("style"), dict)
            else {}
        )
        if project_style:
            lines.append(
                "project-style="
                f"{project_style.get('indent_style', 'preserve')}:"
                f"{project_style.get('indent_size', 'preserve')};"
                f"final_newline={project_style.get('final_newline', 'preserve')}"
            )

        overrides = contract.get("languages")
        if not isinstance(overrides, dict) or not overrides:
            return "\n".join(lines)

        requested_languages = {
            language
            for path in (paths or ())
            if (language := language_for_path(str(path)))
        }
        candidates = (
            [name for name in overrides if name in requested_languages]
            if requested_languages
            else list(overrides)
        )
        for language in sorted(candidates)[: max(1, int(max_overrides))]:
            value = overrides.get(language)
            if isinstance(value, dict):
                lines.append("override " + self._override_row(language, value))
        return "\n".join(lines)
