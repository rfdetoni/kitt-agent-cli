from __future__ import annotations

import importlib.util
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class VerificationStep:
    name: str
    argv: list[str]
    timeout_seconds: int = 120
    kind: str = "test"


class BuildDetector:
    """Detect project toolchains and plan bounded deterministic verification."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()

    def _relevant(self, target_files: List[str], suffixes: set[str], manifests: set[str]) -> bool:
        if not target_files:
            return False
        for raw in target_files:
            path = Path(raw)
            if path.suffix.casefold() in suffixes or path.name in manifests:
                return True
        return False

    def _python_targeted(self, target_files: List[str]) -> list[VerificationStep]:
        if importlib.util.find_spec("pytest") is None:
            return []
        paired: list[str] = []
        for path in (Path(p) for p in target_files if p.endswith(".py")):
            candidate = self.root_path / "tests" / f"test_{path.stem}.py"
            if candidate.is_file():
                paired.append(str(candidate.relative_to(self.root_path)))
        if not paired:
            return []
        return [
            VerificationStep(
                "python.targeted-tests",
                ["python3", "-m", "pytest", "-q", *paired],
                120,
            )
        ]

    def _package_scripts(self) -> tuple[str, dict[str, str]]:
        package = self.root_path / "package.json"
        if not package.is_file():
            return "", {}
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "", {}
        scripts = data.get("scripts") if isinstance(data, dict) else {}
        if not isinstance(scripts, dict):
            scripts = {}
        if (self.root_path / "pnpm-lock.yaml").exists():
            manager = "pnpm"
        elif (self.root_path / "yarn.lock").exists():
            manager = "yarn"
        elif (self.root_path / "bun.lock").exists() or (self.root_path / "bun.lockb").exists():
            manager = "bun"
        else:
            manager = "npm"
        return manager, {str(k): str(v) for k, v in scripts.items()}

    @staticmethod
    def _script_step(manager: str, script: str, kind: str, timeout: int = 180) -> VerificationStep:
        if manager == "npm":
            argv = ["npm", "run", "-s", script]
        elif manager == "yarn":
            argv = ["yarn", script]
        else:
            argv = [manager, "run", script]
        return VerificationStep(f"node.{script}", argv, timeout, kind)

    def plan_verification(
        self,
        target_files: List[str] | None = None,
        *,
        full: bool = False,
    ) -> list[VerificationStep]:
        target_files = list(dict.fromkeys(target_files or []))
        if not target_files:
            return []

        if not full:
            return self._python_targeted(target_files)

        steps: list[VerificationStep] = []

        if self._relevant(
            target_files,
            {".py", ".pyi"},
            {"pyproject.toml", "setup.cfg", "tox.ini"},
        ):
            py_files = [
                p for p in target_files
                if Path(p).suffix.casefold() in {".py", ".pyi"}
            ]
            if py_files and shutil.which("ruff"):
                steps.append(
                    VerificationStep(
                        "python.lint",
                        ["ruff", "check", *py_files],
                        90,
                        "lint",
                    )
                )
            if (self.root_path / "tests").exists():
                if importlib.util.find_spec("pytest") is not None:
                    steps.append(
                        VerificationStep(
                            "python.tests",
                            ["python3", "-m", "pytest", "-q"],
                            180,
                        )
                    )
                else:
                    steps.append(
                        VerificationStep(
                            "python.tests",
                            ["python3", "-m", "unittest", "discover", "tests"],
                            180,
                        )
                    )

        java_relevant = self._relevant(
            target_files,
            {".java", ".kt", ".kts"},
            {
                "pom.xml", "build.gradle", "build.gradle.kts",
                "settings.gradle", "settings.gradle.kts",
            },
        )
        if java_relevant:
            if (self.root_path / "mvnw").exists() or (self.root_path / "pom.xml").exists():
                mvn = "./mvnw" if (self.root_path / "mvnw").exists() else "mvn"
                steps.append(
                    VerificationStep(
                        "java.compile",
                        [mvn, "-q", "-DskipTests", "compile"],
                        180,
                        "compile",
                    )
                )
                steps.append(
                    VerificationStep("java.tests", [mvn, "-q", "test"], 240)
                )
            elif any(
                (self.root_path / name).exists()
                for name in ("gradlew", "build.gradle", "build.gradle.kts")
            ):
                gradle = "./gradlew" if (self.root_path / "gradlew").exists() else "gradle"
                steps.append(
                    VerificationStep("jvm.compile", [gradle, "classes"], 180, "compile")
                )
                steps.append(
                    VerificationStep("jvm.check", [gradle, "check"], 240)
                )

        if self._relevant(
            target_files,
            {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte"},
            {"package.json", "tsconfig.json"},
        ):
            manager, scripts = self._package_scripts()
            if manager:
                for script, kind in (("typecheck", "typecheck"), ("lint", "lint")):
                    if script in scripts:
                        steps.append(self._script_step(manager, script, kind))
                test_script = scripts.get("test", "")
                if test_script and "no test specified" not in test_script.casefold():
                    steps.append(self._script_step(manager, "test", "test", 240))

        if self._relevant(target_files, {".go"}, {"go.mod", "go.work"}):
            steps.append(
                VerificationStep("go.vet", ["go", "vet", "./..."], 180, "lint")
            )
            steps.append(
                VerificationStep("go.tests", ["go", "test", "./..."], 240)
            )

        if self._relevant(target_files, {".rs"}, {"Cargo.toml", "Cargo.lock"}):
            steps.append(
                VerificationStep(
                    "rust.check", ["cargo", "check", "--quiet"], 180, "compile"
                )
            )
            steps.append(
                VerificationStep(
                    "rust.tests", ["cargo", "test", "--quiet"], 240
                )
            )

        if self._relevant(target_files, {".cs"}, {".sln", ".csproj"}):
            steps.append(
                VerificationStep(
                    "dotnet.build", ["dotnet", "build", "--no-restore"], 240, "compile"
                )
            )
            steps.append(
                VerificationStep(
                    "dotnet.tests", ["dotnet", "test", "--no-build"], 240
                )
            )

        if self._relevant(target_files, {".swift"}, {"Package.swift"}):
            steps.append(
                VerificationStep("swift.build", ["swift", "build"], 240, "compile")
            )
            steps.append(
                VerificationStep("swift.tests", ["swift", "test"], 240)
            )

        if self._relevant(target_files, {".dart"}, {"pubspec.yaml"}):
            steps.append(
                VerificationStep(
                    "dart.analyze", ["dart", "analyze"], 180, "typecheck"
                )
            )
            if (self.root_path / "test").exists():
                steps.append(
                    VerificationStep("dart.tests", ["dart", "test"], 240)
                )

        if (
            self._relevant(target_files, {".rb"}, {"Gemfile", "Rakefile"})
            and (self.root_path / "Rakefile").exists()
        ):
            steps.append(
                VerificationStep(
                    "ruby.tests", ["bundle", "exec", "rake", "test"], 240
                )
            )

        if self._relevant(target_files, {".php"}, {"composer.json"}):
            composer = self.root_path / "composer.json"
            try:
                data = json.loads(composer.read_text(encoding="utf-8")) if composer.exists() else {}
                scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
            except (OSError, UnicodeError, json.JSONDecodeError):
                scripts = {}
            if isinstance(scripts, dict):
                for script, kind in (("lint", "lint"), ("test", "test")):
                    if script in scripts:
                        steps.append(
                            VerificationStep(
                                f"php.{script}",
                                ["composer", script],
                                240,
                                kind,
                            )
                        )

        unique: list[VerificationStep] = []
        seen: set[tuple[str, ...]] = set()
        for step in steps[:12]:
            key = tuple(step.argv)
            if key not in seen:
                unique.append(step)
                seen.add(key)
        return unique

    def detect_test_command(self, target_files: List[str] = None) -> Optional[List[str]]:
        """Compatibility API used by older callers/tests."""
        target_files = target_files or []
        if (self.root_path / "tests").exists() or any(
            f.endswith(".py") for f in target_files
        ):
            return ["python3", "-m", "unittest", "discover", "tests"]
        if (self.root_path / "mvnw").exists():
            return ["./mvnw", "test"]
        if (self.root_path / "pom.xml").exists():
            return ["mvn", "test"]
        if (self.root_path / "gradlew").exists():
            return ["./gradlew", "test"]
        manager, scripts = self._package_scripts()
        if manager and "test" in scripts:
            return self._script_step(manager, "test", "test").argv
        if (self.root_path / "go.mod").exists():
            return ["go", "test", "./..."]
        if (self.root_path / "Cargo.toml").exists():
            return ["cargo", "test", "--quiet"]
        return None
