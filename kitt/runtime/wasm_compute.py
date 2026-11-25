"""Optional import-free WASM computation; never expose WASI or host callbacks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def _validate(args):
    if not isinstance(args, dict) or set(args) - {"wat", "export", "args", "fuel", "memory_bytes"}:
        raise ValueError("sandbox.wasm accepts wat, export, args, fuel and memory_bytes")
    wat = args.get("wat")
    name = args.get("export", "run")
    values = args.get("args", [])
    fuel = args.get("fuel", 100_000)
    memory = args.get("memory_bytes", 1024 * 1024)
    if not isinstance(wat, str) or not wat or len(wat.encode("utf-8")) > 32 * 1024:
        raise ValueError("WAT module must contain at most 32 KiB")
    if not isinstance(name, str) or not name or len(name) > 128:
        raise ValueError("invalid WASM export")
    if not isinstance(values, list) or len(values) > 16 or any(
        type(value) is not int or not -(2 ** 63) <= value < 2 ** 63 for value in values
    ):
        raise ValueError("WASM accepts at most 16 signed integer arguments")
    if type(fuel) is not int or not 1 <= fuel <= 1_000_000:
        raise ValueError("fuel must be 1..1000000")
    if type(memory) is not int or not 65536 <= memory <= 16 * 1024 * 1024:
        raise ValueError("memory_bytes must be 64 KiB..16 MiB")
    return wat, name, values, fuel, memory


def execute_wasm(args):
    if os.getenv("KITT_WASM_ENABLED") != "1":
        raise PermissionError("WASM compute is disabled by the operator")
    _validate(args)
    # No inherited application credentials, CWD or Python import overrides.
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
           if key in os.environ}
    with tempfile.TemporaryDirectory(prefix="kitt-wasm-") as root:
        try:
            result = subprocess.run(
                [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
                input=json.dumps(args, ensure_ascii=False).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=root, env=env,
                timeout=5, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("WASM worker exceeded wall-time budget") from exc
    if result.returncode or len(result.stdout) > 4096:
        raise RuntimeError("WASM worker failed")
    reply = json.loads(result.stdout)
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply


def _compute(args):
    import wasmtime

    wat, name, values, fuel, memory = _validate(args)
    config = wasmtime.Config()
    config.consume_fuel = True
    config.parallel_compilation = False
    engine = wasmtime.Engine(config)
    module = wasmtime.Module(engine, wat)
    if module.imports:
        raise ValueError("WASM imports are forbidden")
    store = wasmtime.Store(engine)
    store.set_limits(memory_size=memory, table_elements=1024, instances=1, tables=1, memories=1)
    store.set_fuel(fuel)
    instance = wasmtime.Instance(store, module, [])
    func = instance.exports(store)[name]
    if not isinstance(func, wasmtime.Func):
        raise ValueError("export is not a function")
    signature = func.type(store)
    integer_types = {"i32", "i64"}
    if len(signature.params) != len(values) or any(
        str(item) not in integer_types for item in signature.params
    ) or len(signature.results) > 1 or any(
        str(item) not in integer_types for item in signature.results
    ):
        raise ValueError("only integer parameters and at most one integer result are supported")
    for value, kind in zip(values, signature.params):
        if str(kind) == "i32" and not -(2 ** 31) <= value < 2 ** 31:
            raise ValueError("i32 argument outside signed range")
    value = func(store, *values)
    return {"result": value, "fuel_used": fuel - store.get_fuel()}


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    try:
        payload = sys.stdin.buffer.read(256 * 1024 + 1)
        if len(payload) > 256 * 1024:
            raise ValueError("WASM request too large")
        output = _compute(json.loads(payload))
    except ImportError:
        output = {"error": "Install optional wasmtime==51.0.0 in the Agent environment"}
    except Exception as exc:
        # Never copy arbitrarily large guest diagnostics into the model context.
        output = {"error": f"WASM rejected or trapped: {type(exc).__name__}"}
    print(json.dumps(output, allow_nan=False))
