#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${KITT_AGENT_REPO:-https://github.com/rfdetoni/kitt-agent-cli.git}"
REF="${KITT_AGENT_REF:-main}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_ROOT="${KITT_AGENT_HOME:-$DATA_HOME/kitt-agent-cli}"
BIN_DIR="${KITT_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
NATIVE=1
UNINSTALL=0

usage() {
  cat <<'EOF'
K.I.T.T. Agent CLI installer/updater

Usage: install.sh [--ref REF] [--no-native] [--uninstall]

Environment:
  KITT_AGENT_HOME  installation root
  KITT_BIN_DIR     command directory (default ~/.local/bin)
  KITT_AGENT_REPO  git repository URL
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
VENV="$INSTALL_ROOT/venv"
DIST="$INSTALL_ROOT/dist-native"
LAUNCHER="$BIN_DIR/kitt"

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

mkdir -p "$INSTALL_ROOT" "$BIN_DIR"
if [[ ! -d "$SRC/.git" ]]; then
  rm -rf "$SRC"
  git clone --filter=blob:none --no-checkout "$REPO_URL" "$SRC"
fi
git -C "$SRC" remote set-url origin "$REPO_URL"
git -C "$SRC" fetch --force --depth 1 origin "$REF"
git -C "$SRC" checkout --detach --force FETCH_HEAD
git -C "$SRC" clean -ffd

if [[ ! -x "$VENV/bin/python" ]]; then
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV"
fi
VPY="$VENV/bin/python"
"$VPY" -m pip install --disable-pip-version-check -U pip wheel >/dev/null

installed_native=0
if [[ $NATIVE -eq 1 ]] && command -v cargo >/dev/null 2>&1; then
  rm -rf "$DIST"; mkdir -p "$DIST"
  if "$VPY" -m pip install --disable-pip-version-check -U 'maturin>=1.8,<2' >/dev/null \
    && "$VPY" "$SRC/packaging/build_native_release.py" --out "$DIST" \
    && compgen -G "$DIST/*.whl" >/dev/null \
    && "$VPY" -m pip install --disable-pip-version-check --force-reinstall "$DIST"/*.whl; then
    installed_native=1
  else
    echo "Native build unavailable; using portable Python package." >&2
  fi
fi

if [[ $installed_native -eq 0 ]]; then
  "$VPY" -m pip install --disable-pip-version-check --upgrade --force-reinstall "$SRC"
fi

ln -sfn "$VENV/bin/kitt" "$LAUNCHER"
"$LAUNCHER" --help >/dev/null
backend="$($VPY -c "from kitt.native.bridge import NativeCodeEngine; print(NativeCodeEngine('$SRC').status.backend)")"
echo "K.I.T.T. Agent CLI installed/updated at $INSTALL_ROOT (backend: $backend)."
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "Add $BIN_DIR to PATH to use: kitt" ;; esac
