import sys
import shutil
from pathlib import Path
from typing import Dict, Any, List

from kitt.llm.http_security import secure_urlopen

class DoctorCheck:
    """System diagnostic utility for K.I.T.T. environment, tools, database, and configuration."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()

    def run_diagnostics(self) -> List[Dict[str, str]]:
        results = []

        # 1. Python version
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        results.append({
            "name": "Python Version",
            "status": "PASS" if sys.version_info >= (3, 10) else "WARN",
            "detail": f"Python {py_ver}"
        })

        # 2. Git executable
        git_path = shutil.which("git")
        results.append({
            "name": "Git Utility",
            "status": "PASS" if git_path else "FAIL",
            "detail": git_path or "git binary not found in PATH"
        })

        # 3. KITT native code engine. External rg/grep is optional; KITT does not require it.
        try:
            from kitt.native.bridge import NativeCodeEngine
            native = NativeCodeEngine(str(self.root_path))
            results.append({
                "name": "KITT Native Code Engine",
                "status": "PASS" if native.status.backend == "rust" else "INFO",
                "detail": (
                    f"Rust {native.status.version}" if native.status.backend == "rust"
                    else f"Python compatibility backend ({native.status.detail or 'native wheel unavailable'})"
                ),
            })
        except Exception as e:
            results.append({"name": "KITT Native Code Engine", "status": "WARN", "detail": str(e)})

        rg_path = shutil.which("rg") or shutil.which("grep")
        results.append({
            "name": "External Search Utility",
            "status": "INFO",
            "detail": f"Optional: {rg_path}" if rg_path else "Optional and not installed"
        })

        git_repo = (self.root_path / ".git").exists()
        results.append({
            "name": "Child Worktree Isolation",
            "status": "PASS" if git_path and git_repo else "INFO",
            "detail": "Git worktrees available" if git_path and git_repo else "Compatibility shared-root fallback for non-Git workspace",
        })

        # 4. Workspace .kitt directory permissions
        kitt_dir = self.root_path / ".kitt"
        try:
            kitt_dir.mkdir(parents=True, exist_ok=True)
            test_file = kitt_dir / ".perm_check"
            test_file.write_text("ok", encoding='utf-8')
            test_file.unlink()
            results.append({"name": "Workspace .kitt Directory", "status": "PASS", "detail": "Writable"})
        except Exception as e:
            results.append({"name": "Workspace .kitt Directory", "status": "FAIL", "detail": str(e)})

        # 5. History SQLite Database
        db_path = kitt_dir / "history" / "history.sqlite3"
        if db_path.exists():
            import sqlite3
            try:
                conn = sqlite3.connect(str(db_path))
                quick_check = conn.execute("PRAGMA quick_check;").fetchone()
                conn.close()
                results.append({
                    "name": "SQLite History Database",
                    "status": "PASS" if quick_check and quick_check[0] == "ok" else "WARN",
                    "detail": f"{db_path.name} integrity: {quick_check[0] if quick_check else 'unknown'}"
                })
            except Exception as e:
                results.append({"name": "SQLite History Database", "status": "FAIL", "detail": str(e)})
        else:
            results.append({"name": "SQLite History Database", "status": "PASS", "detail": "Ready (uninitialized)"})

        # 6. Local Ollama Server
        import urllib.request
        try:
            req = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "kitt-doctor"})
            with secure_urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    results.append({"name": "Local Ollama Endpoint", "status": "PASS", "detail": "Online at 127.0.0.1:11434"})
                else:
                    results.append({"name": "Local Ollama Endpoint", "status": "WARN", "detail": f"HTTP {resp.status}"})
        except Exception:
            results.append({"name": "Local Ollama Endpoint", "status": "INFO", "detail": "Not running on 127.0.0.1:11434"})

        return results

    def reset_state(self, backup: bool = True) -> str:
        """Explicitly reset incompatible SQLite database state with optional backup."""
        import time
        import sqlite3
        from kitt.history.database import HistoryDatabase

        db_path = self.root_path / ".kitt" / "history" / "history.sqlite3"
        backup_path = None
        if db_path.exists():
            if backup:
                ts = int(time.time())
                backup_path = db_path.parent / f"history.sqlite3.pre-modernization.{ts}"
                shutil.copy2(db_path, backup_path)
            # Remove existing DB and WAL/SHM files
            for p in (db_path, db_path.with_name("history.sqlite3-wal"), db_path.with_name("history.sqlite3-shm")):
                if p.exists():
                    p.unlink()

        # Re-initialize clean Schema V1
        db = HistoryDatabase(root_dir=str(self.root_path))
        db.close()

        msg = "SQLite database state successfully reset to Schema V1."
        if backup_path:
            msg += f" (Backup saved to {backup_path.name})"
        return msg
