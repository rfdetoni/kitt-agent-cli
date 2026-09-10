from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from kitt.llm.http_security import secure_urlopen

_STATE_STATUS = {"AUTHENTICATED": "PASS", "CONFIGURED": "PASS", "AVAILABLE": "PASS", "DEGRADED": "WARN", "UNAVAILABLE": "INFO"}


def _check(name: str, state: str, detail: str, *, status: str | None = None) -> dict[str, str]:
    normalized = state.strip().upper()
    return {"name": name, "state": normalized, "status": status or _STATE_STATUS.get(normalized, "INFO"), "detail": detail}


class DoctorCheck:
    """System and ecosystem diagnostics with stable capability lifecycle states."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()

    @staticmethod
    def _module_state(module: str, name: str) -> dict[str, str]:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            found = False
        return _check(name, "AVAILABLE" if found else "UNAVAILABLE", f"Python module {module} {'available' if found else 'not installed'}")

    def run_diagnostics(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        results.append(_check("Python Version", "CONFIGURED" if sys.version_info >= (3, 12) else "DEGRADED", f"Python {py_ver}; KITT requires Python >=3.12", status="PASS" if sys.version_info >= (3, 12) else "WARN"))
        git_path = shutil.which("git")
        results.append(_check("Git Utility", "AVAILABLE" if git_path else "UNAVAILABLE", git_path or "git binary not found in PATH", status="PASS" if git_path else "FAIL"))

        try:
            from kitt.native.bridge import NativeCodeEngine
            native = NativeCodeEngine(str(self.root_path))
            results.append(_check("KITT Native Code Engine", "AVAILABLE" if native.status.backend == "rust" else "DEGRADED", f"Rust {native.status.version}" if native.status.backend == "rust" else f"Python compatibility backend ({native.status.detail or 'native wheel unavailable'})", status="PASS" if native.status.backend == "rust" else "INFO"))
        except Exception as exc:
            results.append(_check("KITT Native Code Engine", "DEGRADED", str(exc), status="WARN"))

        rg_path = shutil.which("rg") or shutil.which("grep")
        results.append(_check("External Search Utility", "AVAILABLE" if rg_path else "UNAVAILABLE", f"Optional: {rg_path}" if rg_path else "Optional and not installed", status="INFO"))
        git_repo = (self.root_path / ".git").exists()
        results.append(_check("Child Worktree Isolation", "CONFIGURED" if git_path and git_repo else "DEGRADED", "Git worktrees available" if git_path and git_repo else "Shared-root compatibility fallback; external agents remain unavailable", status="PASS" if git_path and git_repo else "INFO"))

        kitt_dir = self.root_path / ".kitt"
        try:
            kitt_dir.mkdir(parents=True, exist_ok=True)
            test_file = kitt_dir / ".perm_check"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            results.append(_check("Workspace .kitt Directory", "CONFIGURED", "Writable"))
        except Exception as exc:
            results.append(_check("Workspace .kitt Directory", "DEGRADED", str(exc), status="FAIL"))

        db_path = kitt_dir / "history" / "history.sqlite3"
        if db_path.exists():
            try:
                with sqlite3.connect(str(db_path)) as conn:
                    quick_check = conn.execute("PRAGMA quick_check;").fetchone()
                ok = bool(quick_check and quick_check[0] == "ok")
                results.append(_check("SQLite History Database", "CONFIGURED" if ok else "DEGRADED", f"{db_path.name} integrity: {quick_check[0] if quick_check else 'unknown'}", status="PASS" if ok else "WARN"))
            except Exception as exc:
                results.append(_check("SQLite History Database", "DEGRADED", str(exc), status="FAIL"))
        else:
            results.append(_check("SQLite History Database", "AVAILABLE", "Ready (uninitialized)"))

        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "kitt-doctor"})
            with secure_urlopen(req, timeout=1.5) as resp:
                online = resp.status == 200
                results.append(_check("Local Ollama Endpoint", "CONFIGURED" if online else "DEGRADED", "Online at 127.0.0.1:11434" if online else f"HTTP {resp.status}", status="PASS" if online else "WARN"))
        except Exception:
            results.append(_check("Local Ollama Endpoint", "UNAVAILABLE", "Not running on 127.0.0.1:11434", status="INFO"))

        for module, name in (("kitt.daemon", "KITT Assistant Runtime"), ("kitt.remote", "KITT Remote Runtime"), ("kitt.evals", "KITT Evals"), ("kitt.evolution", "KITT Evolution"), ("kitt_native", "KITT Toolbox Native Wheel")):
            results.append(self._module_state(module, name))

        try:
            from kitt.integrations.catalog import IntegrationCatalog
            for item in IntegrationCatalog().probe_all(include_versions=False):
                detail = f"{item.command or item.module or item.spec.id} -> {', '.join(item.spec.capabilities) or item.spec.category}" if item.available else "not detected"
                results.append(_check(f"Integration: {item.spec.id}", "AVAILABLE" if item.available else "UNAVAILABLE", detail))
        except Exception as exc:
            results.append(_check("Optional Integration Capabilities", "DEGRADED", str(exc), status="WARN"))

        try:
            from kitt.integrations.lsp import LanguageServerCatalog
            for item in LanguageServerCatalog().statuses():
                results.append(_check(f"LSP: {item.name}", "AVAILABLE" if item.available else "UNAVAILABLE", item.executable or f"not installed ({', '.join(item.extensions)})"))
        except Exception as exc:
            results.append(_check("LSP Catalog", "DEGRADED", str(exc), status="WARN"))

        try:
            from kitt.children.backends import ChildAgentBackendRegistry
            registry = ChildAgentBackendRegistry()
            for item in registry.statuses():
                state = "CONFIGURED" if item.available and item.enabled else "AVAILABLE" if item.available else "UNAVAILABLE"
                results.append(_check(f"External Agent: {item.name}", state, item.detail))
        except Exception as exc:
            results.append(_check("External Agent Backends", "DEGRADED", str(exc), status="WARN"))

        otel_enabled = os.getenv("KITT_OTEL_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
        langfuse_enabled = os.getenv("KITT_LANGFUSE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
        results.append(_check("OpenTelemetry Sink", "CONFIGURED" if otel_enabled else "AVAILABLE", "enabled by KITT_OTEL_ENABLED" if otel_enabled else "optional; disabled"))
        results.append(_check("Langfuse Sink", "CONFIGURED" if langfuse_enabled else "AVAILABLE", "enabled by KITT_LANGFUSE_ENABLED" if langfuse_enabled else "optional; disabled"))
        return results

    def reset_state(self, backup: bool = True) -> str:
        import time
        from kitt.history.database import HistoryDatabase
        db_path = self.root_path / ".kitt" / "history" / "history.sqlite3"
        backup_path = None
        if db_path.exists():
            if backup:
                backup_path = db_path.parent / f"history.sqlite3.pre-modernization.{int(time.time())}"
                shutil.copy2(db_path, backup_path)
            for path in (db_path, db_path.with_name("history.sqlite3-wal"), db_path.with_name("history.sqlite3-shm")):
                if path.exists():
                    path.unlink()
        db = HistoryDatabase(root_dir=str(self.root_path))
        db.close()
        message = "SQLite database state successfully reset to Schema V1."
        if backup_path:
            message += f" (Backup saved to {backup_path.name})"
        return message
