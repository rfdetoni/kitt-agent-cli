from pathlib import Path
from typing import Optional, List

class BuildDetector:
    """Detects build tools and test runners in the current workspace."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()

    def detect_test_command(self, target_files: List[str] = None) -> Optional[List[str]]:
        target_files = target_files or []

        # 1. Python
        if (self.root_path / "tests").exists() or any(f.endswith(".py") for f in target_files):
            return ["python3", "-m", "unittest", "discover", "tests"]

        # 2. Maven / Java
        if (self.root_path / "mvnw").exists():
            return ["./mvnw", "test"]
        elif (self.root_path / "pom.xml").exists():
            return ["mvn", "test"]

        # 3. Node / Bun / NPM
        if (self.root_path / "package.json").exists():
            if (self.root_path / "bun.lock").exists() or (self.root_path / "bun.lockb").exists():
                return ["bun", "test"]
            return ["npm", "test"]

        return None
