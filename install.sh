#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${KITT_AGENT_REPO:-https://github.com/rfdetoni/kitt-agent-cli.git}"
ASSISTANT_REPO="${KITT_ASSISTANT_REPO:-https://github.com/rfdetoni/kitt-assistant.git}"
WORKERS_REPO="${KITT_AI_WORKERS_REPO:-https://github.com/rfdetoni/kitt-ai-workers.git}"
TOOLBOX_REPO="${KITT_TOOLBOX_REPO:-https://github.com/rfdetoni/kitt-toolbox.git}"
REF="${KITT_AGENT_REF:-main}"
ASSISTANT_REF="${KITT_ASSISTANT_REF:-main}"
WORKERS_REF="${KITT_AI_WORKERS_REF:-main}"
TOOLBOX_REF="${KITT_TOOLBOX_REF:-main}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_ROOT="${KITT_AGENT_HOME:-$DATA_HOME/kitt-agent-cli}"
BIN_DIR="${KITT_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
NATIVE=1
UNINSTALL=0

usage() {
  cat <<'EOF'
K.I.T.T. Agent CLI installer/updater

Usage: install.sh [--ref REF] [--no-native] [--uninstall]

The installer composes the portable Agent control plane with the Assistant
runtime and Evolution/Evals packages. The shared Rust backend is optional.

Environment:
  KITT_AGENT_HOME       installation root
  KITT_BIN_DIR          command directory (default ~/.local/bin)
  KITT_AGENT_REPO       Agent CLI repository URL
  KITT_ASSISTANT_REPO   Assistant repository URL
  KITT_AI_WORKERS_REPO  Evolution/Evals repository URL
  KITT_TOOLBOX_REPO     shared native toolbox repository URL
  KITT_ASSISTANT_REF    Assistant branch/tag/SHA (default main)
  KITT_AI_WORKERS_REF   workers branch/tag/SHA (default main)
  KITT_TOOLBOX_REF      toolbox branch/tag/SHA (default main)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF="${2:?missing ref}"; shift 2 ;;
    --no-native) NATIVE=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

SRC="$INSTALL_ROOT/src"
ASSISTANT_SRC="$INSTALL_ROOT/assistant"
WORKERS_SRC="$INSTALL_ROOT/ai-workers"
TOOLBOX_SRC="$INSTALL_ROOT/toolbox"
VENV="$INSTALL_ROOT/venv"
DIST="$INSTALL_ROOT/dist-native"
LAUNCHER="$BIN_DIR/kitt"

stop_kitt_services() {
  echo "Stopping active K.I.T.T. services before install/update..."

  # Stop registered resident services first so restart policies cannot respawn
  # old binaries while the installation tree is being replaced.
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user stop kitt-assistant.service kitt-reverse-proxy.service kitt-agent-gateway.service >/dev/null 2>&1 || true
  fi
  if command -v launchctl >/dev/null 2>&1; then
    launchctl stop com.kitt.assistant >/dev/null 2>&1 || true
    launchctl stop com.kitt.reverse-proxy >/dev/null 2>&1 || true
  fi
  if command -v kittctl >/dev/null 2>&1; then
    kittctl service stop >/dev/null 2>&1 || true
  fi

  # Ask the currently installed workspace daemon to stop cleanly when possible.
  local existing_kitt=""
  if [[ -x "$VENV/bin/kitt" ]]; then
    existing_kitt="$VENV/bin/kitt"
  elif command -v kitt >/dev/null 2>&1; then
    existing_kitt="$(command -v kitt)"
  fi
  if [[ -n "$existing_kitt" ]]; then
    "$existing_kitt" daemon stop >/dev/null 2>&1 || true
  fi

  # Final best-effort cleanup for detached KITT service entrypoints. Interactive
  # kitt clients are intentionally not matched.
  if command -v pgrep >/dev/null 2>&1; then
    local uid pattern pid kitt_pids any_alive
    uid="$(id -u)"
    pattern='(kittd([[:space:]]|$)|kitt[.]cli[.]main.*daemon[[:space:]]+run|kitt-reverse-proxy([[:space:]]|$)|kitt-agent-gateway([[:space:]]|$)|kitt-reverse-proxy/.*/dist/(gateway/)?cli[.]js)'
    kitt_pids="$(pgrep -u "$uid" -f "$pattern" 2>/dev/null || true)"
    for pid in $kitt_pids; do
      [[ "$pid" != "$" && "$pid" != "$PPID" ]] || continue
      kill -TERM "$pid" >/dev/null 2>&1 || true
    done
    for _ in {1..30}; do
      any_alive=0
      for pid in $kitt_pids; do
        if kill -0 "$pid" >/dev/null 2>&1; then any_alive=1; break; fi
      done
      [[ $any_alive -eq 0 ]] && break
      sleep 0.1
    done
    for pid in $kitt_pids; do
      [[ "$pid" != "$" && "$pid" != "$PPID" ]] || continue
      kill -KILL "$pid" >/dev/null 2>&1 || true
    done
  fi
}

stop_kitt_services

if [[ $UNINSTALL -eq 1 ]]; then
  if [[ -L "$LAUNCHER" ]] && [[ "$(readlink "$LAUNCHER")" == "$VENV/bin/kitt" ]]; then rm -f "$LAUNCHER"; fi
  rm -rf "$INSTALL_ROOT"
  echo "K.I.T.T. Agent CLI removed."
  exit 0
fi

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
PYTHON="${PYTHON:-$(command -v python3 || true)}"
[[ -n "$PYTHON" ]] || { echo "Python 3.12+ is required" >&2; exit 1; }
"$PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit(f"Python 3.12+ required; found {sys.version.split()[0]}")
PY

sync_repo() {
  local repo="$1" ref="$2" dest="$3"
  if [[ ! -d "$dest/.git" ]]; then
    rm -rf "$dest"
    git clone --filter=blob:none --no-checkout "$repo" "$dest"
  fi
  git -C "$dest" remote set-url origin "$repo"
  git -C "$dest" fetch --force --depth 1 origin "$ref"
  git -C "$dest" checkout --detach --force FETCH_HEAD
  git -C "$dest" clean -ffd
}

mkdir -p "$INSTALL_ROOT" "$BIN_DIR"
sync_repo "$REPO_URL" "$REF" "$SRC"
sync_repo "$ASSISTANT_REPO" "$ASSISTANT_REF" "$ASSISTANT_SRC"
sync_repo "$WORKERS_REPO" "$WORKERS_REF" "$WORKERS_SRC"

if [[ ! -x "$VENV/bin/python" ]]; then
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV"
fi
VPY="$VENV/bin/python"
"$VPY" -m pip install --disable-pip-version-check -U pip wheel >/dev/null

# The Agent remains the portable control plane. Runtime/evolution features are
# separately owned packages composed into the same kitt namespace.
"$VPY" -m pip install --disable-pip-version-check --upgrade --force-reinstall "$SRC"
"$VPY" -m pip install \
  --disable-pip-version-check --no-deps --upgrade --force-reinstall \
  "$ASSISTANT_SRC/packages/kitt-assistant-runtime" \
  "$WORKERS_SRC/packages/kitt-evolution" \
  "$WORKERS_SRC/packages/kitt-evals"

installed_native=0
if [[ $NATIVE -eq 1 ]] && command -v cargo >/dev/null 2>&1; then
  sync_repo "$TOOLBOX_REPO" "$TOOLBOX_REF" "$TOOLBOX_SRC"
  rm -rf "$DIST"; mkdir -p "$DIST"
  if "$VPY" -m pip install --disable-pip-version-check -U 'maturin>=1.8,<2' >/dev/null \
    && "$VPY" "$TOOLBOX_SRC/packaging/build_native_release.py" --out "$DIST" \
    && compgen -G "$DIST/*.whl" >/dev/null \
    && "$VPY" -m pip install --disable-pip-version-check --no-deps --force-reinstall "$DIST"/*.whl; then
    installed_native=1
  else
    echo "Shared native acceleration unavailable; keeping portable Python backend." >&2
  fi
fi

ln -sfn "$VENV/bin/kitt" "$LAUNCHER"
"$LAUNCHER" --help >/dev/null
"$VPY" - <<'PY'
import kitt.daemon.client
import kitt.remote.server
import kitt.evolution
import kitt.evals.corpus
print('KITT companion packages: ok')
PY
backend="$($VPY -c "from kitt.native.bridge import NativeCodeEngine; print(NativeCodeEngine('$SRC').status.backend)")"
echo "K.I.T.T. Agent CLI installed/updated at $INSTALL_ROOT (backend: $backend; native wheel: $installed_native)."
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "Add $BIN_DIR to PATH to use: kitt" ;; esac
