import sys
import shutil
from pathlib import Path
from typing import Dict, Any, List

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

        return results
