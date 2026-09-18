from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, TextIO
from urllib.request import Request, urlopen

LOCK_URL = "https://raw.githubusercontent.com/rfdetoni/kitt/main/ecosystem.lock.json"
INSTALL_SH_URL = "https://raw.githubusercontent.com/rfdetoni/kitt/main/install.sh"
INSTALL_PS1_URL = "https://raw.githubusercontent.com/rfdetoni/kitt/main/install.ps1"

_MAX_RESPONSE_BYTES = 64 * 1024
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_MODULE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_LOCKED_REFS = frozenset({"lock", "locked"})


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _state_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    configured = os.environ.get("KITT_HOME", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser() / "installed-state.json")

    try:
        candidates.append(Path(sys.prefix).resolve().parent / "installed-state.json")
    except OSError:
        pass

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(str(candidate)))
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def _find_state_path() -> Path | None:
    return next((path for path in _state_candidates() if path.is_file()), None)


def _fetch_lock(timeout: float) -> dict[str, Any] | None:
    request = Request(
        LOCK_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "kitt-update-check",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        return None
    payload = json.loads(raw.decode("utf-8"))
    return payload if isinstance(payload, dict) else None


def _source_ref(state: Mapping[str, Any]) -> str | None:
    for key in ("source_ref", "ref"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _tracks_tested_ecosystem(state: Mapping[str, Any]) -> bool:
    source_ref = _source_ref(state)
    return source_ref is not None and source_ref.lower() in _LOCKED_REFS


def find_outdated_components(
    state: Mapping[str, Any],
    lock_payload: Mapping[str, Any],
) -> tuple[str, ...]:
    installed = state.get("repositories")
    current = lock_payload.get("components")
    if not isinstance(installed, Mapping) or not isinstance(current, Mapping):
        return ()

    outdated: list[str] = []
    for repository, installed_sha in installed.items():
        current_sha = current.get(repository)
        if not isinstance(repository, str):
            continue
        if not isinstance(installed_sha, str) or not _SHA_RE.fullmatch(installed_sha):
            continue
        if not isinstance(current_sha, str) or not _SHA_RE.fullmatch(current_sha):
            continue
        if installed_sha != current_sha:
            outdated.append(repository)
    return tuple(sorted(outdated))


def _requested_modules(state: Mapping[str, Any], fallback_module: str) -> tuple[str, ...]:
    values = state.get("requested_modules")
    modules = [
        value
        for value in values
        if isinstance(value, str) and _MODULE_RE.fullmatch(value)
    ] if isinstance(values, list) else []
    if modules:
        return tuple(dict.fromkeys(modules))
    return (fallback_module,)


def _bin_dir(
    state: Mapping[str, Any],
    *,
    windows: bool,
) -> str | None:
    values = state.get("launchers")
    if not isinstance(values, list):
        return None
    path_type = PureWindowsPath if windows else PurePosixPath
    for value in values:
        if isinstance(value, str) and value.strip():
            return str(path_type(value).parent)
    return None


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_update_command(
    state_path: Path,
    state: Mapping[str, Any],
    *,
    fallback_module: str,
    platform_name: str | None = None,
) -> str:
    target_platform = platform_name or ("windows" if os.name == "nt" else "posix")
    windows = target_platform == "windows"
    path_type = PureWindowsPath if windows else PurePosixPath

    modules = ",".join(_requested_modules(state, fallback_module))
    raw_state_path = str(state_path)
    if not windows:
        # Tests and orchestration may render a POSIX installation path while
        # running on Windows. Normalize host separators before applying the
        # target platform's lexical path semantics.
        raw_state_path = raw_state_path.replace("\\", "/")
    root = str(path_type(raw_state_path).parent)
    bin_dir = _bin_dir(state, windows=windows)
    common: list[str] = ["--yes", "--modules", modules, "--root", root]
    if bin_dir is not None:
        common.extend(["--bin-dir", bin_dir])
    if bool(state.get("with_ai_workers")):
        common.append("--with-ai-workers")
    if bool(state.get("portable")):
        common.append("--portable")
    if _tracks_tested_ecosystem(state):
        # The warning compares against ecosystem.lock.json, so the suggested
        # command must converge to that exact tested ecosystem as well.
        common.extend(["--ref", "locked"])

    if windows:
        rendered: list[str] = []
        for value in common:
            if value.startswith("--"):
                rendered.append(value)
            else:
                rendered.append(_ps_quote(value))
        return (
            "& ([ScriptBlock]::Create((Invoke-RestMethod "
            + _ps_quote(INSTALL_PS1_URL)
            + "))) "
            + " ".join(rendered)
        )

    return (
        f"curl -fsSL {shlex.quote(INSTALL_SH_URL)} | sh -s -- "
        + " ".join(shlex.quote(value) for value in common)
    )


def notify_if_update_available(
    *,
    component: str,
    timeout: float = 0.8,
    stream: TextIO | None = None,
    state_path: Path | None = None,
) -> bool:
    if os.environ.get("KITT_DISABLE_UPDATE_CHECK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False

    output = stream or sys.stderr
    try:
        selected_state_path = state_path or _find_state_path()
        if selected_state_path is None:
            return False
        state = _load_json(selected_state_path)
        if state is None:
            return False

        # This checker advertises updates for the tested/locked ecosystem only.
        # Main/custom installations intentionally track a moving ref and must not
        # be compared against ecosystem.lock.json. Legacy state without a source
        # ref is fail-open to avoid the previous permanent false-positive loop.
        if not _tracks_tested_ecosystem(state):
            return False

        lock_payload = _fetch_lock(timeout)
        if lock_payload is None:
            return False
        outdated = find_outdated_components(state, lock_payload)
        if not outdated:
            return False

        fallback_module = "reverse-proxy" if component == "reverse-proxy" else "agent-cli"
        command = build_update_command(
            selected_state_path,
            state,
            fallback_module=fallback_module,
        )
        names = ", ".join(repository.rsplit("/", 1)[-1] for repository in outdated)
        print(
            f"\n[K.I.T.T.] Update available for the tested ecosystem ({names}).",
            file=output,
        )
        print("Update manually with:", file=output)
        print(f"  {command}\n", file=output)
        return True
    except Exception:
        # Update discovery is advisory and must never prevent K.I.T.T. from starting.
        return False
