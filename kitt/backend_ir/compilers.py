from __future__ import annotations

import hashlib
import json
import keyword
import re
from typing import Any


def _identifier(value: str, *, upper: bool = False) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_") or "Item"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    if keyword.iskeyword(cleaned):
        cleaned += "_"
    if upper:
        return "".join(part[:1].upper() + part[1:] for part in cleaned.split("_") if part) or "Item"
    return cleaned


def _field_items(spec: dict[str, Any]) -> list[tuple[str, str]]:
    raw = spec.get("fields") or {}
    if isinstance(raw, dict):
        return [(_identifier(name), str(type_name or "any")) for name, type_name in raw.items()]
    result: list[tuple[str, str]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                result.append((_identifier(str(item["name"])), str(item.get("type") or "any")))
    return result


def _py_type(value: str) -> str:
    return {
        "string": "str",
        "str": "str",
        "integer": "int",
        "int": "int",
        "number": "float",
        "float": "float",
        "boolean": "bool",
        "bool": "bool",
        "object": "dict[str, object]",
        "array": "list[object]",
        "uuid": "str",
        "date": "str",
        "datetime": "str",
    }.get(value.casefold(), "object")


def _ts_type(value: str) -> str:
    return {
        "string": "string",
        "str": "string",
        "integer": "number",
        "int": "number",
        "number": "number",
        "float": "number",
        "boolean": "boolean",
        "bool": "boolean",
        "object": "Record<string, unknown>",
        "array": "unknown[]",
        "uuid": "string",
        "date": "string",
        "datetime": "string",
    }.get(value.casefold(), "unknown")


def _rust_type(value: str) -> str:
    return {
        "string": "String",
        "str": "String",
        "integer": "i64",
        "int": "i64",
        "number": "f64",
        "float": "f64",
        "boolean": "bool",
        "bool": "bool",
        "uuid": "String",
        "date": "String",
        "datetime": "String",
        "array": "Vec<String>",
    }.get(value.casefold(), "String")


def compile_backend(module: dict[str, Any], target: str) -> list[dict[str, str]]:
    target = str(target or "").strip().casefold()
    entities = [
        item
        for item in module.get("resources", [])
        if isinstance(item, dict) and item.get("kind") == "entity"
    ]
    endpoints = [
        item
        for item in module.get("resources", [])
        if isinstance(item, dict) and item.get("kind") == "endpoint"
    ]
    files: list[tuple[str, str]] = [
        (
            "backend.ir.json",
            json.dumps(module, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
    ]

    if target == "python":
        lines = ["from __future__ import annotations", "", "from dataclasses import dataclass", ""]
        for entity in entities:
            name = _identifier(entity.get("id", "Entity"), upper=True)
            lines.extend(["@dataclass(frozen=True)", f"class {name}:"])
            fields = _field_items(entity.get("spec") or {})
            lines.extend([f"    {field}: {_py_type(kind)}" for field, kind in fields] or ["    pass"])
            lines.append("")
        lines.append("ENDPOINTS = (")
        for endpoint in endpoints:
            spec = endpoint.get("spec") or {}
            lines.append(
                "    "
                + repr(
                    {
                        "id": endpoint.get("id"),
                        "method": str(spec.get("method") or "GET").upper(),
                        "path": str(spec.get("path") or "/"),
                        "operation": str(spec.get("operation") or endpoint.get("id")),
                    }
                )
                + ","
            )
        lines.append(")")
        files.append(("backend_contracts.py", "\n".join(lines) + "\n"))
    elif target == "typescript":
        lines = ["/* Generated from KITT Backend IR. */", ""]
        for entity in entities:
            name = _identifier(entity.get("id", "Entity"), upper=True)
            lines.append(f"export interface {name} {{")
            for field, kind in _field_items(entity.get("spec") or {}):
                lines.append(f"  {field}: {_ts_type(kind)};")
            lines.extend(["}", ""])
        lines.append("export const endpoints = [")
        for endpoint in endpoints:
            spec = endpoint.get("spec") or {}
            lines.append(
                "  "
                + json.dumps(
                    {
                        "id": endpoint.get("id"),
                        "method": str(spec.get("method") or "GET").upper(),
                        "path": str(spec.get("path") or "/"),
                        "operation": str(spec.get("operation") or endpoint.get("id")),
                    },
                    ensure_ascii=False,
                )
                + ","
            )
        lines.append("] as const;")
        files.append(("backend-contracts.ts", "\n".join(lines) + "\n"))
    elif target == "rust":
        lines = ["// Generated from KITT Backend IR.", ""]
        for entity in entities:
            name = _identifier(entity.get("id", "Entity"), upper=True)
            lines.extend(["#[derive(Debug, Clone)]", f"pub struct {name} {{"])
            for field, kind in _field_items(entity.get("spec") or {}):
                lines.append(f"    pub {field}: {_rust_type(kind)},")
            lines.extend(["}", ""])
        files.append(("backend_contracts.rs", "\n".join(lines) + "\n"))
    else:
        raise ValueError("target must be one of: python, typescript, rust")

    result: list[dict[str, str]] = []
    for path, content in files:
        raw = content.encode("utf-8")
        result.append(
            {
                "path": path,
                "content": content,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return result
