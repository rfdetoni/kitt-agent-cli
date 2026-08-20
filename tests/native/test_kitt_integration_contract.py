from __future__ import annotations

import importlib.util
import inspect

import pytest


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


@pytest.mark.skipif(not _has_module("kitt.core.runtime"), reason="run after overlay is applied to full KITT checkout")
def test_runtime_has_state_root_contract():
    from kitt.core.runtime import KittRuntime
    signature = inspect.signature(KittRuntime.build)
    assert "state_root_dir" in signature.parameters


@pytest.mark.skipif(not _has_module("kitt.runtime.safe_runtime"), reason="run after overlay is applied to full KITT checkout")
def test_safe_runtime_exposes_compact_native_operations():
    from kitt.runtime.safe_runtime import OPERATION_SPECS
    required = {
        "repo.search", "repo.inspect_symbol", "repo.read_symbol", "repo.references", "repo.edit_symbol",
        "memory.query", "memory.correct", "memory.concept", "memory.link", "process.run",
    }
    assert required.issubset(OPERATION_SPECS)
