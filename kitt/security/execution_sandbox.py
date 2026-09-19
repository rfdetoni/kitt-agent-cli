"""OS-backed execution sandbox planning for process.run."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Literal, Sequence


SandboxProfile = Literal[
    "read-only",
    "workspace-write",
    "workspace-write+network",
    "full-access",
]

DEFAULT_PROCESS_SANDBOX_PROFILE: SandboxProfile = "workspace-write+network"
_MIN_BWRAP_VERSION = (0, 12, 0)
_LANDLOCK_CREATE_RULESET_VERSION = 1
_SUPPORTED_LANDLOCK_MACHINES = frozenset(
    {"x86_64", "amd64", "aarch64", "arm64", "riscv64"}
)


class SandboxUnavailable(RuntimeError):
    pass


def query_landlock_abi() -> int:
    """Return the supported Landlock ABI, or zero when unavailable."""
    if sys.platform != "linux":
        return 0
    if platform.machine().casefold() not in _SUPPORTED_LANDLOCK_MACHINES:
        return 0
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        result = libc.syscall(
            444,
            ctypes.c_void_p(),
            ctypes.c_size_t(0),
            ctypes.c_uint(_LANDLOCK_CREATE_RULESET_VERSION),
        )
    except Exception:
        return 0
    if int(result) < 0:
        return 0
    return int(result)


@dataclass
class SandboxPlan:
    argv: list[str]
    profile: SandboxProfile
    backend: str
    strong: bool
    network_isolated: bool
    host_cwd: Path
    env_overrides: dict[str, str] = field(default_factory=dict)
    scratch_dir: Path | None = None
    reason: str = ""

    def metadata(self) -> dict:
        return {
            "profile": self.profile,
            "backend": self.backend,
            "strong": self.strong,
            "network_isolated": self.network_isolated,
            "reason": self.reason,
        }


class ExecutionSandbox:
    """Select a real OS sandbox without silently weakening automatic execution."""

    default_profile: SandboxProfile = DEFAULT_PROCESS_SANDBOX_PROFILE

    def __init__(
        self,
        root_dir: str | Path,
        *,
        bwrap_path: str | None = None,
        bwrap_version: tuple[int, int, int] | None = None,
        landlock_abi: int | None = None,
        auto_detect: bool = True,
    ):
        self.root = Path(root_dir).expanduser().resolve()
        self._bwrap_path_override = bwrap_path
        self._bwrap_version_override = bwrap_version
        self._landlock_abi_override = landlock_abi
        self._auto_detect = bool(auto_detect)
        self._bwrap_cache: tuple[str, tuple[int, int, int]] | None | bool = False
        self._landlock_cache: int | None = None

    def _probe_landlock(self) -> int:
        if self._landlock_cache is None:
            if self._landlock_abi_override is not None:
                self._landlock_cache = max(0, int(self._landlock_abi_override))
            elif self._auto_detect:
                self._landlock_cache = query_landlock_abi()
            else:
                self._landlock_cache = 0
        return self._landlock_cache

    @staticmethod
    def _parse_version(value: str) -> tuple[int, int, int] | None:
        match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
        if not match:
            return None
        return (
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
        )

    def _candidate_bwrap(self) -> Path | None:
        if self._bwrap_path_override:
            candidate = Path(self._bwrap_path_override).expanduser()
            return candidate.resolve() if candidate.exists() else None
        if not self._auto_detect or sys.platform != "linux":
            return None
        candidates = [Path("/usr/bin/bwrap"), Path("/bin/bwrap")]
        discovered = shutil.which("bwrap")
        if discovered:
            candidates.append(Path(discovered))
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, FileNotFoundError):
                continue
            try:
                resolved.relative_to(self.root)
            except ValueError:
                return resolved
        return None

    def _probe_bwrap(self) -> tuple[str, tuple[int, int, int]] | None:
        if self._bwrap_cache is not False:
            return self._bwrap_cache
        candidate = self._candidate_bwrap()
        if candidate is None:
            self._bwrap_cache = None
            return None
        version = self._bwrap_version_override
        if version is None:
            try:
                probe = subprocess.run(
                    [str(candidate), "--version"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=2,
                    check=False,
                    env={"PATH": os.environ.get("PATH", "")},
                )
                version = self._parse_version(probe.stdout or "")
            except (OSError, subprocess.SubprocessError):
                version = None
        if version is None or tuple(version) < _MIN_BWRAP_VERSION:
            self._bwrap_cache = None
            return None
        self._bwrap_cache = (str(candidate), tuple(version))
        return self._bwrap_cache

    def backend_for(self, profile: SandboxProfile) -> str | None:
        if profile == "full-access":
            return "none"
        if self._probe_bwrap() is not None:
            return "bubblewrap"
        if (
            self._probe_landlock() > 0
            and profile in {"read-only", "workspace-write+network"}
        ):
            return "landlock"
        return None

    def is_strong_available(self, profile: SandboxProfile | None = None) -> bool:
        target = profile or self.default_profile
        return self.backend_for(target) not in {None, "none"}

    @staticmethod
    def _scratch() -> Path:
        return Path(tempfile.mkdtemp(prefix="kitt-sandbox-")).resolve()

    def _bwrap_plan(
        self,
        executable: str,
        profile: SandboxProfile,
        argv: Sequence[str],
        cwd: Path,
    ) -> SandboxPlan:
        scratch = self._scratch()
        wrapped = [
            executable,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--bind",
            str(scratch),
            str(scratch),
        ]
        network_isolated = profile == "workspace-write"
        if network_isolated:
            wrapped.append("--unshare-net")
        if profile in {"workspace-write", "workspace-write+network"}:
            wrapped.extend(["--bind", str(self.root), str(self.root)])
        else:
            wrapped.extend(["--ro-bind", str(self.root), str(self.root)])
        wrapped.extend(["--chdir", str(cwd), "--", *argv])
        return SandboxPlan(
            argv=wrapped,
            profile=profile,
            backend="bubblewrap",
            strong=True,
            network_isolated=network_isolated,
            host_cwd=self.root,
            env_overrides={
                "TMPDIR": str(scratch),
                "TMP": str(scratch),
                "TEMP": str(scratch),
            },
            scratch_dir=scratch,
            reason="bubblewrap>=0.12",
        )

    def _landlock_plan(
        self,
        profile: SandboxProfile,
        argv: Sequence[str],
        cwd: Path,
    ) -> SandboxPlan:
        scratch = self._scratch()
        worker = Path(__file__).with_name("landlock_exec.py").resolve()
        mode = "read-only" if profile == "read-only" else "workspace-write"
        wrapped = [
            sys.executable,
            "-I",
            str(worker),
            "--mode",
            mode,
            "--root",
            str(self.root),
            "--scratch",
            str(scratch),
            "--cwd",
            str(cwd),
            "--",
            *argv,
        ]
        return SandboxPlan(
            argv=wrapped,
            profile=profile,
            backend="landlock",
            strong=True,
            network_isolated=False,
            host_cwd=self.root,
            env_overrides={
                "TMPDIR": str(scratch),
                "TMP": str(scratch),
                "TEMP": str(scratch),
            },
            scratch_dir=scratch,
            reason=f"landlock-abi-{self._probe_landlock()}+no_new_privs",
        )

    def plan(
        self,
        argv: Sequence[str],
        cwd: str | Path,
        *,
        profile: SandboxProfile | None = None,
        require_strong: bool = False,
    ) -> SandboxPlan:
        target = profile or self.default_profile
        cwd_path = Path(cwd).resolve()
        if target == "full-access":
            return SandboxPlan(
                list(argv), target, "none", False, False, cwd_path,
                reason="explicit-full-access",
            )

        bwrap = self._probe_bwrap()
        if bwrap is not None:
            return self._bwrap_plan(bwrap[0], target, argv, cwd_path)

        if (
            self._probe_landlock() > 0
            and target in {"read-only", "workspace-write+network"}
        ):
            return self._landlock_plan(target, argv, cwd_path)

        if require_strong:
            raise SandboxUnavailable(
                f"Strong sandbox profile '{target}' is unavailable on this host"
            )
        return SandboxPlan(
            list(argv),
            target,
            "none",
            False,
            False,
            cwd_path,
            reason="explicit-approval-fallback",
        )

    @staticmethod
    def cleanup(plan: SandboxPlan | None) -> None:
        if plan is None or plan.scratch_dir is None:
            return
        shutil.rmtree(plan.scratch_dir, ignore_errors=True)
