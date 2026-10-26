"""Bounded read-only recipe execution does not create new security authority."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kitt.runtime.recipe_runtime import ReadOnlyRecipeService
from kitt.runtime.program_runtime import BoundedProgramRuntime
from kitt.runtime.core_runtime import OPERATION_REGISTRY
from kitt.security.capabilities import CAP_REPO_READ


def _write_recipe(root: Path, name: str, body: str) -> None:
    recipes = root / ".kitt" / "recipes"
    recipes.mkdir(parents=True, exist_ok=True)
    (recipes / (name + ".toml")).write_text(body, encoding="utf-8")


def _service(root: Path):
    runtime = SimpleNamespace(root=root)
    calls = []

    def execute(op, args, **kwargs):
        calls.append((op, args))
        return SimpleNamespace(
            success=True, data={"ok": True}, error=None, requires_approval=False,
            tokens_saved=0,
        )

    runtime.execute = execute
    runtime.program_runtime = BoundedProgramRuntime(runtime)
    return ReadOnlyRecipeService(runtime), calls


def test_recipe_is_discoverable_inspectable_and_runs_through_program_runtime(tmp_path):
    _write_recipe(tmp_path, "inspect-code", '''
version = 1
title = "Inspect code"
description = "Retrieve a file"
[[program]]
call = "repo.read"
save = "found"
[program.arguments]
path = "README.md"
[[program]]
return = "$found"
''')
    service, calls = _service(tmp_path)
    listing = service.list({})
    assert [r["name"] for r in listing["recipes"]] == ["inspect-code"]
    detail = service.inspect({"name": "inspect-code"})
    assert detail["authority"] == "read_only_program_runtime"
    assert len(detail["sha256"]) == 64
    result = service.run({"name": "inspect-code"}, turn_id="t", origin="MODEL",
                         capabilities={CAP_REPO_READ}, security_context=None)
    assert result.success
    assert calls == [("repo.read", {"path": "README.md"})]
    assert result.data["recipe"]["sha256"] == detail["sha256"]


def test_recipe_rejects_arbitrary_execution_and_does_not_invoke_tool(tmp_path):
    _write_recipe(tmp_path, "unsafe", '''
version = 1
title = "Disallowed command"
[[program]]
call = "process.run"
[program.arguments]
argv = ["sh", "-c", "echo malicious"]
''')
    service, calls = _service(tmp_path)
    result = service.run({"name": "unsafe"}, turn_id="t", origin="MODEL",
                         capabilities={CAP_REPO_READ}, security_context=None)
    assert not result.success
    assert calls == []


def test_recipe_rejects_symlinks_and_invalid_inputs(tmp_path):
    _write_recipe(tmp_path, "safe", '''
version = 1
title = "Safe"
[[program]]
return = "ok"
''')
    service, _ = _service(tmp_path)
    with pytest.raises(ValueError):
        service.inspect({"name": "../safe"})
    with pytest.raises(ValueError):
        service.run({"name": "safe", "state": {"x": "a" * 10000}},
                    turn_id="t", origin="MODEL", capabilities={CAP_REPO_READ},
                    security_context=None)
    (tmp_path / ".kitt" / "recipes" / "linked.toml").symlink_to(
        tmp_path / ".kitt" / "recipes" / "safe.toml"
    )
    assert [r["name"] for r in service.list({})["recipes"]] == ["safe"]
    with pytest.raises(PermissionError):
        service.inspect({"name": "linked"})


def test_recipe_authority_metadata_is_read_only():
    for operation in ("recipe.list", "recipe.inspect", "recipe.run"):
        assert OPERATION_REGISTRY[operation].required_capability == CAP_REPO_READ
    from kitt.runtime.programmatic_flow import READ_ONLY_FLOW_OPERATIONS
    assert "process.run" not in READ_ONLY_FLOW_OPERATIONS
    assert "mcp.call" not in READ_ONLY_FLOW_OPERATIONS

def test_recipe_directory_enumeration_is_bounded(tmp_path):
    recipes = tmp_path / ".kitt" / "recipes"
    recipes.mkdir(parents=True)
    for index in range(270):
        (recipes / f"not-a-recipe-{index}.txt").write_text("", encoding="utf-8")
    service, _ = _service(tmp_path)
    assert service.list({})["truncated"] is True

def test_path_scoped_agent_cannot_inspect_or_run_other_recipes(tmp_path):
    _write_recipe(tmp_path, "visible", '''
version = 1
title = "Visible"
[[program]]
return = "ok"
''')
    _write_recipe(tmp_path, "private", '''
version = 1
title = "Private"
[[program]]
return = "secret"
''')
    service, calls = _service(tmp_path)

    class ScopedPrincipal:
        is_path_scoped = True

        def assert_path_allowed(self, path):
            if path != ".kitt/recipes/visible.toml":
                raise PermissionError("recipe outside allowed paths")

    principal = ScopedPrincipal()
    assert [item["name"] for item in service.list({}, principal)["recipes"]] == ["visible"]
    with pytest.raises(PermissionError):
        service.inspect({"name": "private"}, principal)
    with pytest.raises(PermissionError):
        service.run({"name": "private"}, turn_id="t", origin="MODEL",
                    capabilities={CAP_REPO_READ}, security_context=principal)
    assert calls == []
