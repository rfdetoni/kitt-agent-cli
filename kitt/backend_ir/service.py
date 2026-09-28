from __future__ import annotations

import copy
import hashlib
import json
import threading
from typing import Any

from kitt.backend_ir.compilers import compile_backend
from kitt.backend_ir.models import BackendPlan, RESOURCE_KINDS, ValidationIssue


class BackendService:
    """Host-owned Backend IR validator, impact planner and deterministic compiler."""

    def __init__(self, max_resources: int = 512, max_spec_bytes: int = 64 * 1024):
        self.max_resources = max(1, min(int(max_resources), 4096))
        self.max_spec_bytes = max(1024, min(int(max_spec_bytes), 1024 * 1024))
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _fingerprint(value: Any) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _revision(module: dict[str, Any]) -> int:
        try:
            return max(0, int(module.get("revision", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def validate(self, module: dict[str, Any]) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not isinstance(module, dict):
            return [ValidationIssue("backend.not_object", "backend module must be an object")]
        backend_id = str(module.get("id") or "").strip()
        if not backend_id or len(backend_id) > 128:
            issues.append(ValidationIssue("backend.invalid_id", "backend id must be 1..128 characters", path="id"))
        try:
            revision = int(module.get("revision", 0) or 0)
            if revision < 0:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(ValidationIssue("backend.invalid_revision", "revision must be a non-negative integer", path="revision"))
        resources = module.get("resources") or []
        if not isinstance(resources, list):
            return issues + [ValidationIssue("backend.resources_not_array", "resources must be an array", path="resources")]
        if len(resources) > self.max_resources:
            issues.append(ValidationIssue("backend.too_many_resources", f"resources exceeds max={self.max_resources}", path="resources"))

        by_id: dict[str, dict[str, Any]] = {}
        for index, resource in enumerate(resources[: self.max_resources]):
            path = f"resources[{index}]"
            if not isinstance(resource, dict):
                issues.append(ValidationIssue("backend.resource_not_object", "resource must be an object", path=path))
                continue
            resource_id = str(resource.get("id") or "").strip()
            kind = str(resource.get("kind") or "").strip().casefold()
            spec = resource.get("spec") or {}
            if not resource_id or len(resource_id) > 256:
                issues.append(ValidationIssue("backend.invalid_resource_id", "resource id must be 1..256 characters", path=f"{path}.id"))
                continue
            if resource_id in by_id:
                issues.append(ValidationIssue("backend.duplicate_resource", f"duplicate resource id: {resource_id}", path=f"{path}.id"))
            by_id[resource_id] = resource
            if kind not in RESOURCE_KINDS:
                issues.append(ValidationIssue("backend.invalid_kind", f"unsupported resource kind: {kind}", path=f"{path}.kind"))
            if not isinstance(spec, dict):
                issues.append(ValidationIssue("backend.invalid_spec", "resource spec must be an object", path=f"{path}.spec"))
                continue
            try:
                spec_bytes = len(json.dumps(spec, ensure_ascii=False).encode("utf-8"))
            except (TypeError, ValueError):
                issues.append(ValidationIssue("backend.non_json_spec", "resource spec must be JSON-serializable", path=f"{path}.spec"))
                continue
            if spec_bytes > self.max_spec_bytes:
                issues.append(ValidationIssue("backend.spec_too_large", f"resource spec exceeds {self.max_spec_bytes} bytes", path=f"{path}.spec"))
            if kind == "endpoint":
                method = str(spec.get("method") or "").upper()
                route = str(spec.get("path") or "")
                if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                    issues.append(ValidationIssue("backend.endpoint_method", "endpoint requires a supported HTTP method", path=f"{path}.spec.method"))
                if not route.startswith("/"):
                    issues.append(ValidationIssue("backend.endpoint_path", "endpoint path must start with '/'", path=f"{path}.spec.path"))
            if kind == "entity" and not isinstance(spec.get("fields", {}), (dict, list)):
                issues.append(ValidationIssue("backend.entity_fields", "entity fields must be an object or array", path=f"{path}.spec.fields"))

        graph: dict[str, list[str]] = {}
        for resource_id, resource in by_id.items():
            spec = resource.get("spec") or {}
            raw_deps = spec.get("depends_on") or []
            if not isinstance(raw_deps, list):
                issues.append(ValidationIssue("backend.invalid_dependencies", "depends_on must be an array", path=f"resource:{resource_id}"))
                continue
            deps: list[str] = []
            for dep in raw_deps:
                dep_id = str(dep)
                if dep_id == resource_id:
                    issues.append(ValidationIssue("backend.self_dependency", "resource cannot depend on itself", path=f"resource:{resource_id}"))
                elif dep_id not in by_id:
                    issues.append(ValidationIssue("backend.unknown_dependency", f"unknown dependency: {dep_id}", path=f"resource:{resource_id}"))
                else:
                    deps.append(dep_id)
            graph[resource_id] = deps

        visiting: set[str] = set()
        visited: set[str] = set()
        cycle_reported: set[str] = set()

        def walk(resource_id: str) -> None:
            if resource_id in visited:
                return
            if resource_id in visiting:
                if resource_id not in cycle_reported:
                    cycle_reported.add(resource_id)
                    issues.append(ValidationIssue("backend.dependency_cycle", f"dependency cycle detected at {resource_id}", path=f"resource:{resource_id}"))
                return
            visiting.add(resource_id)
            for dep in graph.get(resource_id, []):
                walk(dep)
            visiting.discard(resource_id)
            visited.add(resource_id)

        for resource_id in graph:
            walk(resource_id)
        return issues

    def plan(self, module: dict[str, Any]) -> BackendPlan:
        issues = self.validate(module)
        backend_id = str(module.get("id") or "") if isinstance(module, dict) else ""
        if any(issue.severity == "error" for issue in issues):
            return BackendPlan(backend_id, self._revision(module if isinstance(module, dict) else {}), issues=tuple(issues))

        with self._lock:
            previous = copy.deepcopy(self._snapshots.get(backend_id))
        old_resources = {
            str(item["id"]): item
            for item in (previous or {}).get("resources", [])
            if isinstance(item, dict) and item.get("id")
        }
        new_resources = {
            str(item["id"]): item
            for item in module.get("resources", [])
            if isinstance(item, dict) and item.get("id")
        }
        operations: list[dict[str, Any]] = []
        affected: list[str] = []
        for resource_id in sorted(set(old_resources) | set(new_resources)):
            old = old_resources.get(resource_id)
            new = new_resources.get(resource_id)
            if old is None:
                op, value = "add", new
            elif new is None:
                op, value = "remove", None
            elif self._fingerprint(old) != self._fingerprint(new):
                op, value = "replace", new
            else:
                continue
            operations.append(
                {
                    "op": op,
                    "target": f"resource:{resource_id}",
                    "value": value,
                    "effect": "write",
                    "evidence": [],
                }
            )
            affected.append(f"kitt://backend/{backend_id}/{resource_id}")
        return BackendPlan(
            backend_id=backend_id,
            base_revision=self._revision(previous or {}),
            operations=tuple(operations),
            affected_resources=tuple(affected),
            issues=tuple(issues),
        )

    def accept(self, module: dict[str, Any]) -> dict[str, Any]:
        issues = self.validate(module)
        if any(issue.severity == "error" for issue in issues):
            raise ValueError("; ".join(issue.message for issue in issues))
        snapshot = copy.deepcopy(module)
        backend_id = str(snapshot["id"])
        with self._lock:
            current = self._snapshots.get(backend_id)
            snapshot["revision"] = self._revision(current or {}) + 1
            self._snapshots[backend_id] = snapshot
        return copy.deepcopy(snapshot)

    def compile(self, module: dict[str, Any], target: str) -> dict[str, Any]:
        issues = self.validate(module)
        if any(issue.severity == "error" for issue in issues):
            return {"ok": False, "issues": [issue.to_dict() for issue in issues], "files": []}
        files = compile_backend(module, target)
        return {
            "ok": True,
            "target": str(target),
            "issues": [issue.to_dict() for issue in issues],
            "files": files,
        }
