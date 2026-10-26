"""OS-backed execution sandbox planning for process.run."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import os
from pathlib import Path
import platform
import re
import json
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
# Fixed, operator-controlled toolchain catalog. Never accept an image from tool input.
_TOOLCHAIN_IMAGES = {
    "java": "maven:3.9-eclipse-temurin-25-noble",
    "gradle": "gradle:9-jdk25-noble",
    "node": "node:24-bookworm",
    "rust": "rust:1.99-bookworm",
}
_JAVA_TOOLS = frozenset({"mvn", "mvnw", "java", "javac"})
_GRADLE_TOOLS = frozenset({"gradle", "gradlew"})
_NODE_TOOLS = frozenset({"node", "npm", "npx", "pnpm", "yarn"})
_RUST_TOOLS = frozenset({"cargo", "rustc", "rustup"})
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
        self._docker_cache: tuple[str, str] | None | bool = False
        self._docker_cache_until = 0.0
        self._docker_ready_images: set[str] = set()
        self._broker_cache_until = 0.0
        self._broker_cache: str | None = None

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

    def _probe_broker(self) -> str | None:
        """A healthy Unix-socket broker is a separate trusted sandbox backend."""
        if time.monotonic() < self._broker_cache_until:
            return self._broker_cache
        self._broker_cache_until = time.monotonic() + 3
        self._broker_cache = None
        if not self._auto_detect or sys.platform != "linux":
            return None
        path = os.getenv("KITT_SANDBOX_BROKER_SOCKET", "").strip()
        if not path:
            return None
        try:
            if not Path(path).is_socket():
                return None
            from kitt.security.autopilot_broker import send
            if send(path, {"op": "ping"}, timeout=1).get("stdout") == "pong":
                self._broker_cache = path
        except (OSError, ValueError, TimeoutError, ConnectionError, json.JSONDecodeError):
            pass
        return self._broker_cache

    def _broker_plan(
        self, socket_path: str, profile: SandboxProfile,
        argv: Sequence[str], cwd: Path, timeout_seconds: int,
    ) -> SandboxPlan:
        rel = cwd.relative_to(self.root)
        return SandboxPlan(
            argv=[
                sys.executable, "-m", "kitt.security.autopilot_broker",
                "client", json.dumps({
                    "argv": list(argv), "cwd": str(rel),
                    "timeout": int(timeout_seconds), "profile": profile,
                }),
            ],
            profile=profile, backend="broker", strong=True,
            network_isolated=profile != "workspace-write+network",
            host_cwd=self.root,
            env_overrides={"KITT_SANDBOX_BROKER_SOCKET": socket_path},
            reason="trusted-local-broker;persisted-sandbox-worker",
        )

    def _probe_docker(self) -> tuple[str, str] | None:
        """Probe only a local, installed Docker daemon and an already-pulled image.

        No downloads or container execution occur during capability detection.
        The daemon is contacted by this trusted host broker, never by the agent.
        """
        if self._docker_cache is not False and time.monotonic() < self._docker_cache_until:
            return self._docker_cache
        self._docker_cache = None
        self._docker_cache_until = time.monotonic() + 60
        if sys.platform != "linux" or not self._auto_detect:
            return None
        executable = shutil.which("docker")
        if not executable:
            return None
        binary = Path(executable).resolve()
        try:
            binary.relative_to(self.root)
            return None  # Never execute a workspace-supplied Docker shim.
        except ValueError:
            pass
        try:
            st = binary.stat()
            if not binary.is_file() or st.st_uid != 0 or st.st_mode & 0o022:
                return None
        except OSError:
            return None
        image = os.environ.get(
            "KITT_AUTOPILOT_IMAGE", "ghcr.io/rfdetoni/kitt-agent-cli:latest"
        ).strip()
        if not image or any(char.isspace() for char in image) or len(image) > 256:
            return None
        try:
            for cmd in (
                [str(binary), "info", "--format", "{{.Server.Version}}"],
                [str(binary), "image", "inspect", image, "--format", "{{.Id}}"],
            ):
                check = subprocess.run(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=3, check=False,
                )
                if check.returncode != 0:
                    return None
        except (OSError, subprocess.SubprocessError):
            return None
        self._docker_cache = (str(binary), image)
        return self._docker_cache

    @staticmethod
    def _select_toolchain_image(
        default_image: str, argv: Sequence[str], cwd: Path,
    ) -> str:
        """Choose an image from fixed catalog, not from model-selected strings."""
        if os.environ.get("KITT_AUTOPILOT_IMAGE"):
            return default_image
        executable = Path(argv[0]).name.lower() if argv else ""
        if executable.startswith("mvnw"):
            executable = "mvn"
        elif executable.startswith("gradlew"):
            executable = "gradle"
        if executable in _JAVA_TOOLS:
            return _TOOLCHAIN_IMAGES["java"]
        if executable in _GRADLE_TOOLS:
            return _TOOLCHAIN_IMAGES["gradle"]
        if executable in _NODE_TOOLS:
            return _TOOLCHAIN_IMAGES["node"]
        if executable in _RUST_TOOLS:
            return _TOOLCHAIN_IMAGES["rust"]
        if executable in {"sh", "bash", "dash", "zsh"} and len(argv) >= 3 and argv[1] in {"-c", "-lc"}:
            names = re.findall(
                r"(?<![\\w.-])(mvnw?|gradlew?|java|javac|npm|npx|node|pnpm|yarn|cargo|rustc|rustup)(?![\\w.-])",
                argv[2], flags=re.IGNORECASE,
            )
            for name in names:
                tool = name.lower()
                if tool in _JAVA_TOOLS:
                    return _TOOLCHAIN_IMAGES["java"]
                if tool in _GRADLE_TOOLS:
                    return _TOOLCHAIN_IMAGES["gradle"]
                if tool in _NODE_TOOLS:
                    return _TOOLCHAIN_IMAGES["node"]
                if tool in _RUST_TOOLS:
                    return _TOOLCHAIN_IMAGES["rust"]
        # Project metadata alone cannot reroute generic git and inspection
        # commands to language workers missing these OS tools.
        return default_image

    def _ensure_toolchain_image(self, docker: str, image: str) -> None:
        """Provision known worker images with trusted host commands on demand."""
        if image in self._docker_ready_images:
            return
        if image not in _TOOLCHAIN_IMAGES.values():
            raise SandboxUnavailable("Only known toolchain images may be auto-provisioned")
        try:
            inspect = subprocess.run(
                [docker, "image", "inspect", image, "--format", "{{.Id}}"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5, check=False,
            )
            if inspect.returncode != 0:
                pull = subprocess.run(
                    [docker, "pull", image], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    timeout=600, check=False,
                )
                if pull.returncode != 0:
                    raise SandboxUnavailable(
                        f"Trusted Docker toolchain image {image} unavailable: "
                        + pull.stderr.decode("utf-8", "replace")[-500:]
                    )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxUnavailable(
                f"Unable to prepare trusted Docker toolchain image {image}: {exc}"
            ) from exc
        self._docker_ready_images.add(image)

    def _docker_plan(
        self, docker: str, image: str, profile: SandboxProfile,
        argv: Sequence[str], cwd: Path,
    ) -> SandboxPlan:
        """Run a workspace-scoped process without exposing Docker to the guest."""
        if any(char in str(self.root) for char in (",", ":")):
            raise SandboxUnavailable("Docker bind mount cannot represent workspace path")
        relative_cwd = cwd.relative_to(self.root)
        selected_image = self._select_toolchain_image(image, argv, cwd)
        if selected_image != image:
            self._ensure_toolchain_image(docker, selected_image)
        network_isolated = profile != "workspace-write+network"
        writable = profile != "read-only"
        wrapped = [
            docker, "run", "--rm", "--init", "--pull=never",
            "--network", "none" if network_isolated else "bridge",
            "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=256",
            "--memory=4g", "--cpus=2",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g",
            "--tmpfs", "/home/kitt:rw,nosuid,nodev,size=1g,mode=1777",
            "--env", "HOME=/home/kitt",
            "--env", "MAVEN_CONFIG=/home/kitt/.m2",
            "--env", "GRADLE_USER_HOME=/home/kitt/.gradle",
            "--env", "CARGO_HOME=/home/kitt/.cargo",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--mount", (
                f"type=bind,src={self.root},dst=/workspace"
                + (",readonly" if not writable else "")
            ),
            "--workdir", str(Path("/workspace") / relative_cwd),
        ]
        # KITT control-plane state is never directly visible or writable.
        if (self.root / ".kitt").is_dir():
            wrapped += ["--mount", "type=tmpfs,dst=/workspace/.kitt"]
        wrapped += ["--entrypoint", "/usr/bin/env", selected_image, *argv]
        return SandboxPlan(
            argv=wrapped, profile=profile, backend="docker", strong=True,
            network_isolated=network_isolated, host_cwd=self.root,
            reason=f"local-docker-container;workspace-scoped;image={selected_image}",
        )

    def backend_for(self, profile: SandboxProfile) -> str | None:
        if profile == "full-access":
            return "none"
        if self._probe_broker() is not None:
            return "broker"
        if self._probe_bwrap() is not None:
            return "bubblewrap"
        if profile != "full-access" and self._probe_docker() is not None:
            return "docker"
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
        return self.backend_for(target) in {"bubblewrap", "docker", "broker"}

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

    @classmethod
    def _append_resolver_mounts(
        cls,
        wrapped: list[str],
        resolver_paths: Sequence[Path],
        *,
        masked_root: Path,
    ) -> None:
        """Rebind only resolved resolver files masked by /run, never symlink destinations."""
        mounted: set[Path] = set()
        for resolver in resolver_paths:
            try:
                target = resolver.resolve(strict=True)
                relative = target.relative_to(masked_root)
                if target in mounted or not target.is_file():
                    continue
            except (OSError, ValueError):
                continue
            # /run has just been replaced with an empty tmpfs. Restore only
            # the directory chain needed by the resolver file, not host /run.
            parent = masked_root
            for component in relative.parts[:-1]:
                parent /= component
                cls._append_sequence(wrapped, "--dir", str(parent))
            cls._append_sequence(
                wrapped, "--ro-bind-try", str(target), str(target)
            )
            mounted.add(target)

    def _bwrap_plan(
        self,
        executable: str,
        profile: SandboxProfile,
        argv: Sequence[str],
        cwd: Path,
        *,
        autonomous_git_write: bool = False,
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

        # The root filesystem is already mounted read-only. Only resolver
        # files hidden behind the new /run tmpfs need to be reintroduced.
        # Binding /etc/resolv.conf to itself is invalid when it is a symlink
        # (Ubuntu/systemd-resolved): Bubblewrap rejects symlink destinations.
        network_isolated = profile == "workspace-write"
        if not network_isolated:
            self._append_resolver_mounts(
                wrapped,
                (
                    Path("/etc/resolv.conf"),
                    Path("/etc/hosts"),
                    Path("/etc/nsswitch.conf"),
                ),
                masked_root=Path("/run"),
            )

        self._append_sequence(wrapped, "--bind", str(scratch), str(scratch))

        if network_isolated:
            wrapped.append("--unshare-net")
        if profile in {"workspace-write", "workspace-write+network"}:
            self._append_sequence(wrapped, "--bind", str(self.root), str(self.root))
        else:
            self._append_sequence(wrapped, "--ro-bind", str(self.root), str(self.root))

        # Direct, policy-checked Git commands need to update their own index,
        # refs and object database. Keep metadata read-only for every other
        # executable; .kitt always belongs to the host control plane.
        git_writable = (
            profile in {"workspace-write", "workspace-write+network"}
            and (
                (autonomous_git_write and (self.root / ".git").is_dir())
                or (bool(argv) and argv[0] in {"git", "git.exe"})
            )
        )
        for protected_name in (".git", ".kitt"):
            if protected_name == ".git" and git_writable:
                continue
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
        autonomous_git_write: bool = False,
        prefer_docker_toolchain: bool = False,
        timeout_seconds: int = 120,
    ) -> SandboxPlan:
        target = profile or self.default_profile
        cwd_path = Path(cwd).resolve()
        if target == "full-access":
            return SandboxPlan(
                list(argv), target, "none", False, False, cwd_path,
                reason="explicit-full-access",
            )

        broker = self._probe_broker()
        if broker is not None:
            return self._broker_plan(
                broker, target, argv, cwd_path, timeout_seconds,
            )

        # Development commands use their fixed Docker toolchain even when
        # Bubblewrap is functional. Otherwise a missing host-side Maven/Node
        # would fail despite the image being provisionable by the broker.
        docker = self._probe_docker() if prefer_docker_toolchain else None
        if docker is not None and (
            self._select_toolchain_image(docker[1], argv, cwd_path) != docker[1]
            or bool(os.environ.get("KITT_AUTOPILOT_IMAGE"))
        ):
            return self._docker_plan(docker[0], docker[1], target, argv, cwd_path)

        bwrap = self._probe_bwrap()
        if bwrap is not None:
            return self._bwrap_plan(
                bwrap[0], target, argv, cwd_path,
                autonomous_git_write=autonomous_git_write,
            )

        docker = docker if docker is not None else self._probe_docker()
        if docker is not None:
            return self._docker_plan(docker[0], docker[1], target, argv, cwd_path)

        if require_strong:
            raise SandboxUnavailable(
                f"Strong sandbox profile '{target}' requires functional bubblewrap >= 0.12 or local Docker with a prepared image. "
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
