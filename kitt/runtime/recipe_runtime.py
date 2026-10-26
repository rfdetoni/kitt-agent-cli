"""Workspace-local, read-only recipes backed by the existing bounded program runtime.

Recipe files are untrusted data. They never grant capabilities, execute Python,
shell or arbitrary code, or install extensions. Every nested operation passes
through the usual SafeRuntime security context and read-only allowlist.
"""
from __future__ import annotations

import hashlib
import os
import json
import re
import stat
import tomllib
from pathlib import Path
from typing import Any

from kitt.security.workspace_fs import WorkspaceFileSystem


_SLUG = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_MAX_RECIPE_BYTES = 16 * 1024
_MAX_INPUT_BYTES = 8 * 1024
_MAX_RECIPES = 32


class ReadOnlyRecipeService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = Path(runtime.root)
        self.fs = WorkspaceFileSystem(self.root)

    @staticmethod
    def _name(value: Any) -> str:
        if not isinstance(value, str) or not _SLUG.fullmatch(value):
            raise ValueError("recipe name must be a lowercase slug (1..48 characters)")
        return value

    def _load(self, name: str, security_context=None) -> dict[str, Any]:
        name = self._name(name)
        relative = f".kitt/recipes/{name}.toml"
        if security_context is not None:
            security_context.assert_path_allowed(relative)
        path = self.root / relative
        # Do not follow workspace-config symlinks, including a substituted parent.
        for part in (self.root / ".kitt", self.root / ".kitt" / "recipes", path):
            if part.is_symlink():
                raise PermissionError("recipe symlink is not allowed")
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            raise ValueError(f"recipe not found: {name}") from exc
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_RECIPE_BYTES:
            raise ValueError("recipe must be a regular file up to 16 KiB")
        data = self.fs.read(relative).content
        if len(data) > _MAX_RECIPE_BYTES:
            raise ValueError("recipe exceeds 16 KiB")
        doc = tomllib.loads(data.decode("utf-8", errors="strict"))
        if not isinstance(doc, dict) or set(doc) - {"version", "title", "description", "program"}:
            raise ValueError("unsupported recipe fields")
        if doc.get("version") != 1:
            raise ValueError("recipe version must be 1")
        title, description = doc.get("title"), doc.get("description", "")
        program = doc.get("program")
        if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 120
                or not isinstance(description, str) or len(description) > 512
                or not isinstance(program, list) or not 1 <= len(program) <= 64
                or any(not isinstance(item, dict) for item in program)):
            raise ValueError("invalid title, description or program")
        return {
            "name": name,
            "title": title.strip(),
            "description": description,
            "program": program,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def list(self, arguments: dict[str, Any], security_context=None) -> dict[str, Any]:
        if arguments:
            raise ValueError("recipe.list takes no arguments")
        directory = self.root / ".kitt" / "recipes"
        if directory.is_symlink() or (self.root / ".kitt").is_symlink():
            raise PermissionError("recipe symlink is not allowed")
        if not directory.is_dir():
            return {"recipes": [], "truncated": False}
        # Directory discovery is bounded even if an untrusted workspace has
        # thousands of non-recipe entries. This avoids a sorted(iterdir()) spike.
        candidate_names = []
        truncated = False
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= 256:
                    truncated = True
                    break
                if (
                    entry.name.endswith(".toml")
                    and _SLUG.fullmatch(entry.name[:-5])
                    and entry.is_file(follow_symlinks=False)
                ):
                    candidate_names.append(entry.name[:-5])
        recipes = []
        for name in sorted(candidate_names):
            if len(recipes) >= _MAX_RECIPES:
                truncated = True
                break
            try:
                loaded = self._load(name, security_context)
            except (OSError, PermissionError, ValueError, UnicodeError, tomllib.TOMLDecodeError):
                continue
            recipes.append({key: loaded[key] for key in ("name", "title", "description", "sha256")})
        return {"recipes": recipes, "truncated": truncated}

    def inspect(self, arguments: dict[str, Any], security_context=None) -> dict[str, Any]:
        if not isinstance(arguments, dict) or set(arguments) != {"name"}:
            raise ValueError("recipe.inspect requires name")
        loaded = self._load(arguments["name"], security_context)
        return {
            "name": loaded["name"],
            "title": loaded["title"],
            "description": loaded["description"],
            "sha256": loaded["sha256"],
            "program": loaded["program"],
            "instructions_untrusted": True,
            "authority": "read_only_program_runtime",
        }

    def run(self, arguments: dict[str, Any], *, turn_id: str,
            origin: str, capabilities: set[str], security_context):
        if not isinstance(arguments, dict) or set(arguments) - {"name", "state", "max_tokens"}:
            raise ValueError("recipe.run requires name and optional state/max_tokens")
        loaded = self._load(arguments.get("name"), security_context)
        state = arguments.get("state", {})
        if (not isinstance(state, dict) or len(state) > 32
                or any(not isinstance(k, str) or len(k) > 64 for k in state)):
            raise ValueError("recipe state must be an object of up to 32 named keys")
        try:
            if len(json.dumps(state, ensure_ascii=False).encode("utf-8")) > _MAX_INPUT_BYTES:
                raise ValueError("recipe state exceeds 8 KiB")
        except (TypeError, RecursionError) as exc:
            raise ValueError("recipe state must be JSON-serializable") from exc
        tokens = arguments.get("max_tokens", 1600)
        if type(tokens) is not int or not 64 <= tokens <= 8000:
            raise ValueError("max_tokens must be an integer within 64..8000")
        result = self.runtime.program_runtime.execute(
            {"program": loaded["program"], "state": state, "max_tokens": tokens,
             "max_statements": 64, "max_tool_calls": 24},
            turn_id=turn_id,
            origin=origin,
            capabilities=capabilities,
            security_context=security_context,
        )
        result.operation = "recipe.run"
        if result.success and isinstance(result.data, dict):
            result.data["recipe"] = {"name": loaded["name"], "sha256": loaded["sha256"]}
        return result
