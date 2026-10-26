"""Workspace-local declarative recipes backed by the bounded program and review runtimes.

Recipe files are untrusted data. Write recipes prepare guarded reviews; no
recipe grants capabilities, executes arbitrary code or bypasses policy approval.
"""
from __future__ import annotations

import hashlib
import os
import json
import re
import stat
from string import Template
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
        if not isinstance(doc, dict) or set(doc) - {"version", "title", "description", "program", "changes", "parameters", "checks", "after_apply"}:
            raise ValueError("unsupported recipe fields")
        if doc.get("version") != 1:
            raise ValueError("recipe version must be 1")
        title, description = doc.get("title"), doc.get("description", "")
        program = doc.get("program")
        changes = doc.get("changes")
        parameters = doc.get("parameters", [])
        checks = doc.get("checks", [])
        after_apply = doc.get("after_apply", [])
        if not isinstance(parameters, list) or len(parameters) > 16 or any(
            not isinstance(name, str) or not _SLUG.fullmatch(name) for name in parameters
        ) or len(set(parameters)) != len(parameters):
            raise ValueError("recipe parameters must be up to 16 unique lowercase names")
        if not isinstance(checks, list) or len(checks) > 16 or any(
            not isinstance(c, dict) or set(c) != {"path", "contains"}
            or not isinstance(c["path"], str) or not isinstance(c["contains"], str)
            for c in checks
        ):
            raise ValueError("invalid recipe checks")
        if not isinstance(after_apply, list) or len(after_apply) > 16 or any(
            not isinstance(statement, dict) for statement in after_apply
        ):
            raise ValueError("invalid recipe after_apply program")
        if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 120
                or not isinstance(description, str) or len(description) > 512
                or (program is None) == (changes is None)):
            raise ValueError("recipe must declare exactly one program or changes")
        if program is not None and (not isinstance(program, list) or not 1 <= len(program) <= 64
                                    or any(not isinstance(item, dict) for item in program)):
            raise ValueError("invalid read-only recipe program")
        if changes is not None and (
            not isinstance(changes, list) or not 1 <= len(changes) <= 8
            or any(not isinstance(item, dict) or set(item) != {"path", "content"}
                   or not isinstance(item["path"], str) or not isinstance(item["content"], str)
                   for item in changes)
        ):
            raise ValueError("invalid change recipe files")
        return {
            "name": name,
            "title": title.strip(),
            "description": description,
            "program": program,
            "changes": changes,
            "parameters": parameters,
            "checks": checks,
            "after_apply": after_apply,
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
            "changes": loaded["changes"],
            "parameters": loaded["parameters"],
            "checks": loaded["checks"],
            "after_apply": loaded["after_apply"],
            "instructions_untrusted": True,
            "authority": "read_only_program_runtime",
        }

    def run(self, arguments: dict[str, Any], *, turn_id: str,
            origin: str, capabilities: set[str], security_context):
        if not isinstance(arguments, dict) or set(arguments) - {"name", "state", "max_tokens"}:
            raise ValueError("recipe.run requires name and optional state/max_tokens")
        loaded = self._load(arguments.get("name"), security_context)
        if loaded["program"] is None:
            raise ValueError("write recipes require recipe.preview and recipe.apply")
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

    def preview(self, arguments: dict[str, Any], security_context):
        if not isinstance(arguments, dict) or set(arguments) - {"name", "values"} or "name" not in arguments:
            raise ValueError("recipe.preview requires name and optional values")
        loaded = self._load(arguments["name"], security_context)
        if loaded["changes"] is None:
            raise ValueError("recipe.preview requires a change recipe")
        values = arguments.get("values", {})
        if not isinstance(values, dict) or set(values) != set(loaded["parameters"]):
            raise ValueError("recipe values must match declared parameters exactly")
        if any(not isinstance(value, str) or len(value.encode("utf-8")) > 256 for value in values.values()):
            raise ValueError("recipe parameter values must be strings of at most 256 bytes")
        files = []
        for item in loaded["changes"]:
            # Paths are literal: templates may modify only UTF-8 content.
            files.append({"path": item["path"],
                          "content": Template(item["content"]).substitute(values)})
        report = self.runtime.reviews.preview({"files": files}, security_context)
        record = self.runtime.reviews._load(report["review_id"], security_context)
        record["recipe_name"] = loaded["name"]
        record["recipe_sha256"] = loaded["sha256"]
        record["recipe_values"] = values
        self.runtime.reviews.state.set(report["review_id"], record, ttl_seconds=86400)
        report["recipe"] = {"name": loaded["name"], "sha256": loaded["sha256"]}
        return report

    def _transition(self, arguments: dict[str, Any], security_context, *, reverse: bool):
        if not isinstance(arguments, dict) or set(arguments) != {"name", "review_id"}:
            raise ValueError("recipe transition requires name and review_id")
        loaded = self._load(arguments["name"], security_context)
        record = self.runtime.reviews._load(arguments["review_id"], security_context)
        if record.get("recipe_name") != loaded["name"] or record.get("recipe_sha256") != loaded["sha256"]:
            raise ValueError("recipe changed after preview; generate a new preview")
        action = self.runtime.reviews.rewind if reverse else self.runtime.reviews.apply
        result = action({"review_id": arguments["review_id"]}, security_context)
        if reverse:
            return result
        try:
            for check in loaded["checks"]:
                rel = self.runtime.reviews._path(check["path"], security_context)
                actual = self.fs.read_text(rel, max_bytes=16384)
                if check["contains"] not in actual:
                    raise ValueError(f"recipe verification failed for {rel}")
            if loaded["after_apply"]:
                from kitt.security.capabilities import CAP_REPO_READ
                program_result = self.runtime.program_runtime.execute(
                    {"program": loaded["after_apply"], "max_statements": 16,
                     "max_tool_calls": 8, "max_tokens": 1200},
                    turn_id=getattr(security_context, "turn_id", "recipe"),
                    origin=getattr(security_context, "origin", "MODEL"),
                    capabilities={CAP_REPO_READ}, security_context=security_context,
                )
                if not program_result.success:
                    raise ValueError("recipe post-apply read-only hook failed")
        except Exception:
            self.runtime.reviews.rewind({"review_id": arguments["review_id"]}, security_context)
            raise
        return result

    def apply(self, arguments: dict[str, Any], security_context):
        return self._transition(arguments, security_context, reverse=False)

    def rewind(self, arguments: dict[str, Any], security_context):
        return self._transition(arguments, security_context, reverse=True)
