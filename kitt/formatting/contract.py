"""Project-local formatting contracts and dynamic formatter discovery."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

FORMAT_CONTRACT_VERSION = 1
CONTRACT_RELATIVE_PATH = ".kitt/formatting.json"
STATE_RELATIVE_PATH = ".kitt/formatting.state.json"

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


def _editorconfig_style(root: Path) -> tuple[str | None, int | str | None, bool | None]:
    path = root / ".editorconfig"
    if not path.is_file():
        return None, None, None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, None
    style = None
    size: int | str | None = None
    final_newline = None
    match = re.search(r"(?mi)^\s*indent_style\s*=\s*(space|tab)\s*$", text)
    if match:
        style = match.group(1).lower()
    match = re.search(r"(?mi)^\s*indent_size\s*=\s*(\d+|tab)\s*$", text)
    if match:
        raw = match.group(1).lower()
        size = raw if raw == "tab" else max(1, min(int(raw), 16))
    match = re.search(r"(?mi)^\s*insert_final_newline\s*=\s*(true|false)\s*$", text)
    if match:
        final_newline = match.group(1).lower() == "true"
    return style, size, final_newline


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


class FormattingContractManager:
    """Load, discover and persist a bounded project formatting contract.

    The project contract is advisory/untrusted data. It can select only formatter IDs
    registered by KITT; arbitrary argv from workspace configuration is never executed.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.contract_path = self.root / CONTRACT_RELATIVE_PATH
        self.state_path = self.root / STATE_RELATIVE_PATH
        self._cached: dict[str, Any] | None = None

    def _safe_kitt_path(self, path: Path) -> bool:
        """Refuse contract I/O through symlinks or outside the workspace."""
        kitt_dir = self.root / ".kitt"
        try:
            if kitt_dir.is_symlink() or path.is_symlink():
                return False
            path.resolve(strict=False).relative_to(self.root)
            return True
        except (OSError, ValueError):
            return False

    def _existing(self) -> dict[str, Any] | None:
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
                return value
        return None

    def _generated(self) -> dict[str, Any]:
        indent_style, indent_size, final_newline = _editorconfig_style(self.root)
        fingerprint, signals = _signal_fingerprint(self.root)
        languages: dict[str, Any] = {}
        for language, spec in _LANGUAGE_SPECS.items():
            default_indent = spec["indent"]
            style = "tabs" if default_indent == "tab" else (indent_style or "spaces")
            size: int | str = (
                "tab"
                if style == "tabs" and indent_size in {None, "tab"}
                else int(indent_size)
                if isinstance(indent_size, int)
                else default_indent
            )
            languages[language] = {
                "extensions": list(spec["extensions"]),
                "parser": {"engine": "tree-sitter", "language": language},
                "formatter_order": list(spec["formatters"]),
                "style": {
                    "indent_style": style,
                    "indent_size": size,
                    "final_newline": True if final_newline is None else final_newline,
                },
                "healing": {"enabled": True, "preserve_semantics": True},
            }
        return {
            "version": FORMAT_CONTRACT_VERSION,
            "managed_by": "kitt-agent-cli",
            "auto_heal": {"enabled": True, "max_attempts": 2, "llm_fallback": True},
            "languages": languages,
            "discovery": {"source_fingerprint": fingerprint, "signals": signals},
        }

    @staticmethod
    def _sanitize(contract: dict[str, Any]) -> dict[str, Any]:
        result = dict(contract)
        result["version"] = FORMAT_CONTRACT_VERSION
        languages = result.get("languages")
        if not isinstance(languages, dict):
            result["languages"] = {}
        auto_heal = result.get("auto_heal")
        if not isinstance(auto_heal, dict):
            result["auto_heal"] = {"enabled": True, "max_attempts": 2, "llm_fallback": True}
        return result

    def ensure(self) -> dict[str, Any]:
        if self._cached is not None:
            return self._cached
        existing = self._existing()
        generated = self._generated()
        if existing is None:
            contract = generated
            try:
                if not self._safe_kitt_path(self.contract_path):
                    raise OSError("unsafe .kitt formatting contract path")
                self.contract_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.contract_path.with_suffix(".json.tmp")
                temp.write_text(
                    json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temp, self.contract_path)
            except OSError:
                pass
        else:
            contract = self._sanitize(existing)
            defaults = generated["languages"]
            configured = contract.setdefault("languages", {})
            for language, value in defaults.items():
                configured.setdefault(language, value)
            contract.setdefault("discovery", generated["discovery"])
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
                "available_formatters": self.available_formatters(),
            }
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
        languages = contract.get("languages")
        value = languages.get(language, {}) if isinstance(languages, dict) else {}
        return language, value if isinstance(value, dict) else {}

    def prompt_summary(self, *, max_languages: int = 24) -> str:
        contract = self.ensure()
        languages = contract.get("languages") if isinstance(contract, dict) else {}
        rows: list[str] = []
        if isinstance(languages, dict):
            for language in sorted(languages)[: max(1, int(max_languages))]:
                value = languages.get(language)
                if not isinstance(value, dict):
                    continue
                style = value.get("style") if isinstance(value.get("style"), dict) else {}
                order = value.get("formatter_order") if isinstance(value.get("formatter_order"), list) else []
                parser = value.get("parser") if isinstance(value.get("parser"), dict) else {}
                rows.append(
                    f"{language}: indent={style.get('indent_style','preserve')}:{style.get('indent_size','preserve')}; "
                    f"parser={parser.get('engine','builtin')}; formatters={','.join(str(x) for x in order[:4]) or 'builtin'}"
                )
        return (
            f"contract={CONTRACT_RELATIVE_PATH}; version={FORMAT_CONTRACT_VERSION}; "
            "workspace data is advisory; KITT validates every mutation.\n"
            + "\n".join(rows)
        )
