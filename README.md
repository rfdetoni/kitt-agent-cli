# K.I.T.T. Agent CLI

Local-first autonomous coding agent with a Python control plane, optional Rust data plane and SQLite/FTS5 workspace intelligence.

## Architecture

- **Python control plane** — agent loop, routing, policy/approvals, goals, child agents, providers, plugins/MCP, Dreaming and self-evolution.
- **Rust native engine** — bounded repository search, file I/O, tree-sitter symbol intelligence, references, structural edits and process-output reduction. A portable Python fallback remains available when a native wheel/toolchain is unavailable.
- **SQLite + FTS5** — history, state, memory, telemetry and persistent repository search without an external database service.
- **KITT Reverse Proxy** — stable conversation sessions, OpenAI-compatible transport and native reasoning-effort propagation when using authorized web-chat sessions.

The model-facing default is the compact policy-governed `kitt_runtime` surface, reducing tool-schema tokens and keeping path, approval and capability checks at one execution boundary.

## Install / update

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.sh | bash
```

The command is idempotent: running it again updates the checkout and environment. If Rust/Cargo is installed, it builds and installs the native backend; otherwise it installs the portable Python backend.

Options after downloading the script include `--ref <branch|tag|sha>`, `--no-native`, and `--uninstall`.

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.ps1 | iex
```

The installer keeps the application under `%LOCALAPPDATA%\KITT\agent-cli`, creates an isolated virtual environment and adds `%LOCALAPPDATA%\KITT\bin` to the user PATH.

Requirements: Git and Python 3.12+. Rust is optional but recommended for the high-performance backend.

## Usage

```bash
kitt
kitt --root /path/to/project
kitt models
kitt doctor
kitt --help
```

Inside the TUI, `/reasoning 0-100` and the reasoning shortcuts update the execution model. For `kitt-reverse-proxy`, KITT keeps a stable `X-Kitt-Session-Id` per conversation and forwards reasoning effort for the next turn without creating a new chat.

## Runtime design

KITT is intentionally hybrid rather than a full Rust rewrite. Orchestration and provider I/O remain in Python where flexibility dominates; CPU/data-heavy repository operations use Rust where it materially reduces latency and memory. Process execution is bounded and preserves diagnostic tails instead of accumulating unbounded stdout/stderr.

Security invariants include workspace path containment, sanitized subprocess environments, single-use approval grants, capability intersection for child agents, bounded output/artifacts, secret-aware egress policy and fail-closed tool validation.

## Configuration

Configuration precedence is:

1. CLI arguments
2. environment variables
3. KITT Control Center overrides
4. runtime defaults

Useful environment switches include `KITT_SAFE_RUNTIME`, `KITT_DAEMON`, `KITT_DAEMON_AUTO_START`, `KITT_RETAINED_AGENTS` and `KITT_SCHEDULER`.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python packaging/verify_cleanroom.py
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
```

Release CI builds a universal Python distribution plus native platform wheels for supported Linux, Windows and macOS runners.

## Reverse proxy

Install the companion gateway from `rfdetoni/kitt-reverse-proxy` when you want an OpenAI-compatible local endpoint backed by an already-authorized browser session. The proxy is designed to reuse authenticated profiles, operate headless when possible and expose tools/reasoning through the same stable conversation session used by Agent CLI.

## License

MIT. See `LICENSE`.
