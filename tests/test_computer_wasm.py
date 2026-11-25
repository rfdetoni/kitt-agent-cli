import pytest

from kitt.runtime.wasm_compute import execute_wasm


def test_wasm_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KITT_WASM_ENABLED", raising=False)
    with pytest.raises(PermissionError):
        execute_wasm({"wat": "(module)"})


def test_wasm_integer_compute_and_isolation(monkeypatch):
    pytest.importorskip("wasmtime")
    monkeypatch.setenv("KITT_WASM_ENABLED", "1")
    result = execute_wasm({
        "wat": '(module (func (export "run") (param i32 i32) (result i32) local.get 0 local.get 1 i32.add))',
        "args": [20, 22],
    })
    assert result["result"] == 42
    assert result["fuel_used"] > 0
    for module in (
        '(module (import "host" "file" (func)) (func (export "run")))',
        '(module (func (export "run") (loop br 0)))',
        '(module (memory 1000) (func (export "run")))',
    ):
        with pytest.raises(RuntimeError):
            execute_wasm({"wat": module, "fuel": 1000, "memory_bytes": 65536})
