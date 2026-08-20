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

        # 3. Ripgrep executable
        rg_path = shutil.which("rg") or shutil.which("grep")
        results.append({
            "name": "Search Utility",
            "status": "PASS" if rg_path else "WARN",
            "detail": f"Using {rg_path}" if rg_path else "rg/grep not found"
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
