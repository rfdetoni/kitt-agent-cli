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
import time
from typing import Literal, Sequence


SandboxProfile = Literal[
    "read-only",
    "workspace-write",
    "workspace-write+network",
    "full-access",
]

DEFAULT_PROCESS_SANDBOX_PROFILE: SandboxProfile = "workspace-write"
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
        home_dir: str | Path | None = None,
    ):
        self.root = Path(root_dir).expanduser().resolve()
        self.home = (
            Path(home_dir).expanduser().resolve()
            if home_dir is not None
            else Path.home().expanduser().resolve()
        )
        self._bwrap_path_override = bwrap_path
        self._bwrap_version_override = bwrap_version
        self._landlock_abi_override = landlock_abi
        self._auto_detect = bool(auto_detect)
        self._bwrap_cache: tuple[str, tuple[int, int, int]] | None | bool = False
        self._bwrap_cache_until = 0.0
        self._bwrap_failures: list[str] = []
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

    def _bwrap_candidates(self) -> list[Path]:
        """Find independent installations, deduplicating symlinks and excluding the workspace."""
        if self._bwrap_path_override:
            paths = [Path(self._bwrap_path_override).expanduser()]
        elif not self._auto_detect or sys.platform != "linux":
            return []
        else:
            paths = [Path("/usr/local/bin/bwrap"), Path("/usr/bin/bwrap"), Path("/bin/bwrap")]
            paths.extend(Path(directory) / "bwrap" for directory in os.get_exec_path() if directory)

        candidates: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            try:
                resolved = path.resolve(strict=True)
                if resolved in seen or not resolved.is_file() or not os.access(resolved, os.X_OK):
                    continue
                resolved.relative_to(self.root)
            except ValueError:
                seen.add(resolved)
                candidates.append(resolved)
            except OSError:
                continue
        return candidates

    @staticmethod
    def _bwrap_runtime_probe(candidate: Path) -> tuple[bool, str]:
        true_path = next(
            (path for path in (Path("/usr/bin/true"), Path("/bin/true")) if path.exists()),
            None,
        )
        if true_path is None:
            return False, "system /usr/bin/true not found"
        try:
            probe = subprocess.run(
                [
                    str(candidate),
                    "--die-with-parent", "--new-session",
                    "--unshare-user", "--unshare-pid",
                    "--unshare-ipc", "--unshare-uts",
                    "--ro-bind", "/", "/", "--proc", "/proc",
                    "--dev", "/dev", "--", str(true_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=3,
                check=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"namespace probe failed: {type(exc).__name__}"
        if probe.returncode == 0:
            return True, ""
        detail = (probe.stderr or "").strip().replace("\n", " ")[:240]
        hint = (
            " Check the AppArmor userns profile for this exact executable path."
            if "uid map" in detail.casefold() or "permission denied" in detail.casefold()
            else ""
        )
        return False, f"namespace probe exit {probe.returncode}: {detail or 'no stderr'}.{hint}"

    def _probe_bwrap(self) -> tuple[str, tuple[int, int, int]] | None:
        if self._bwrap_cache is not False and time.monotonic() < self._bwrap_cache_until:
            return self._bwrap_cache
        self._bwrap_failures = []
        self._bwrap_cache = None
        for candidate in self._bwrap_candidates():
            version = self._bwrap_version_override
            if version is None:
                try:
                    result = subprocess.run(
                        [str(candidate), "--version"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        errors="replace",
                        timeout=2,
                        check=False,
                        env={"PATH": os.environ.get("PATH", "")},
                    )
                    version = (
                        self._parse_version(result.stdout or "")
                        if result.returncode == 0
                        else None
                    )
                except (OSError, subprocess.SubprocessError):
                    version = None
            if version is None:
                self._bwrap_failures.append(f"{candidate}: cannot determine version")
                continue
            if tuple(version) < _MIN_BWRAP_VERSION:
                self._bwrap_failures.append(
                    f"{candidate}: {'.'.join(map(str, version))} is below 0.12.0"
                )
                continue
            # Explicit version overrides are reserved for hermetic tests.
            if self._bwrap_version_override is None:
                works, reason = self._bwrap_runtime_probe(candidate)
                if not works:
                    self._bwrap_failures.append(f"{candidate}: {reason}")
                    continue
            self._bwrap_cache = (str(candidate), tuple(version))
            break
        if not self._bwrap_failures and self._bwrap_cache is None:
            self._bwrap_failures.append(
                "no executable bwrap installation found outside the workspace"
            )
        # Recheck periodically: an updated binary or AppArmor profile can fix a failed probe.
        self._bwrap_cache_until = time.monotonic() + 60
        return self._bwrap_cache

    def bubblewrap_diagnostic(self) -> str:
        """Bounded explanation of the actual selected candidates, without weakening isolation."""
        selected = self._probe_bwrap()
        if selected is not None:
            return f"functional bubblewrap {'.'.join(map(str, selected[1]))} at {selected[0]}"
        return "; ".join(self._bwrap_failures[:4])[:900]

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

    def bubblewrap_executable(self) -> str | None:
        """Return a functional bubblewrap executable path, when available."""
        probe = self._probe_bwrap()
        return probe[0] if probe is not None else None

    def is_strong_available(self, profile: SandboxProfile | None = None) -> bool:
        target = profile or self.default_profile
        # Landlock confines selected filesystem writes, but it cannot provide
        # the namespace/socket isolation required for unattended process.exec.
        return self.backend_for(target) == "bubblewrap"

    @staticmethod
    def _scratch() -> Path:
        return Path(tempfile.mkdtemp(prefix="kitt-sandbox-")).resolve()

    @staticmethod
    def _append_sequence(items: list[str], *values: str) -> None:
        items.extend(values)

    def _mask_private_path(self, wrapped: list[str], path: Path) -> None:
        try:
            if not path.exists():
                return
            resolved = path.resolve()
            if resolved.is_dir():
                try:
                    self.root.relative_to(resolved)
                except ValueError:
                    pass
                else:
                    # Never hide an ancestor that contains the workspace.
                    return
                self._append_sequence(wrapped, "--tmpfs", str(resolved))
            else:
                self._append_sequence(
                    wrapped, "--ro-bind", "/dev/null", str(resolved)
                )
        except OSError:
            # Sandbox construction stays conservative without turning transient
            # stat failures into a reason to drop all isolation.
            return

    def _sensitive_host_paths(self) -> tuple[Path, ...]:
        relatives = (
            ".ssh",
            ".gnupg",
            ".aws",
            ".azure",
            ".kube",
            ".docker",
            ".kitt",
            ".config/gcloud",
            ".config/gh",
            ".config/glab",
            ".local/share/keyrings",
            ".password-store",
            ".mozilla",
            ".config/google-chrome",
            ".config/chromium",
            ".netrc",
            ".git-credentials",
            ".npmrc",
            ".pypirc",
            ".cargo/credentials",
            ".cargo/credentials.toml",
            ".m2/settings.xml",
            ".gradle/gradle.properties",
            ".config/pip/pip.conf",
        )
        paths = [self.home / relative for relative in relatives]
        raw_kitt_home = str(os.environ.get("KITT_HOME", "") or "").strip()
        if raw_kitt_home:
            paths.append(Path(raw_kitt_home).expanduser())
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return tuple(unique)

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
        ]

        # Hide host runtime sockets and global temporary data. Bubblewrap bind
        # sources are resolved from /oldroot, so workspace/scratch mounts can be
        # reintroduced safely after these masks.
        for hidden_root in (Path("/run"), Path("/tmp"), Path("/var/tmp")):
            if hidden_root.is_dir():
                self._append_sequence(wrapped, "--tmpfs", str(hidden_root))

        # /etc/resolv.conf is commonly a symlink into /run. Rebind the host
        # resolver inputs as read-only after masking /run so network-enabled
        # profiles keep DNS without exposing runtime sockets.
        for resolver_path in (
            Path("/etc/resolv.conf"),
            Path("/etc/hosts"),
            Path("/etc/nsswitch.conf"),
        ):
            if resolver_path.exists():
                self._append_sequence(
                    wrapped,
                    "--ro-bind-try",
                    str(resolver_path),
                    str(resolver_path),
                )

        self._append_sequence(wrapped, "--bind", str(scratch), str(scratch))

        network_isolated = profile == "workspace-write"
        if network_isolated:
            wrapped.append("--unshare-net")
        if profile in {"workspace-write", "workspace-write+network"}:
            self._append_sequence(wrapped, "--bind", str(self.root), str(self.root))
        else:
            self._append_sequence(wrapped, "--ro-bind", str(self.root), str(self.root))

        # Commands may modify source files, but repository control-plane state
        # stays owned by KITT. This also prevents autonomous git metadata edits.
        for protected_name in (".git", ".kitt"):
            protected = self.root / protected_name
            if protected.exists():
                self._append_sequence(
                    wrapped, "--ro-bind", str(protected), str(protected)
                )

        # Environment scrubbing prevents variable-based credential leakage;
        # these masks additionally block direct filesystem reads of common host
        # credentials/browser profiles from arbitrary interpreters.
        for sensitive in self._sensitive_host_paths():
            self._mask_private_path(wrapped, sensitive)

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
            strong=False,
            network_isolated=False,
            host_cwd=self.root,
            env_overrides={
                "TMPDIR": str(scratch),
                "TMP": str(scratch),
                "TEMP": str(scratch),
            },
            scratch_dir=scratch,
            reason=(
                f"landlock-abi-{self._probe_landlock()}+no_new_privs"
                "-filesystem-only"
            ),
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

        if require_strong:
            raise SandboxUnavailable(
                f"Strong sandbox profile '{target}' requires functional bubblewrap >= 0.12. "
                f"{self.bubblewrap_diagnostic()}"
            )

        if (
            self._probe_landlock() > 0
            and target in {"read-only", "workspace-write+network"}
        ):
            return self._landlock_plan(target, argv, cwd_path)
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
