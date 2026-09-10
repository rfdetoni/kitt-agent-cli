# K.I.T.T. Agent CLI

Local-first autonomous coding-agent **control plane** built in Python with SQLite/FTS5 workspace intelligence and optional shared native acceleration.

## Ownership

`kitt-agent-cli` owns the hot-path coding-agent orchestration:

- agent loop, routing and model/provider selection;
- policy, approvals, capabilities and workspace security;
- goals, child agents, history, Dreaming, state and telemetry;
- plugins, MCP, hooks and external-tool integrations;
- `kitt_runtime`, repository adapters and the portable Python native fallback.

Heavy or independently deployable capabilities are intentionally not vendored here:

- **`kitt-toolbox`** owns the Rust `kitt-native-engine`, PyO3 binding and `kitt_native` wheel;
- **`kitt-assistant`** owns the persistent Rust service/control center plus the Python `kitt-assistant-runtime` package providing `kitt.daemon` and `kitt.remote`;
- **`kitt-ai-workers`** owns the separate `kitt-evolution` and `kitt-evals` packages plus optional heavy STT/ML workers;
- **`kitt-reverse-proxy`** owns the authorized browser/API gateway.

These Python distributions compose through the shared `kitt.*` namespace. The Agent itself remains a portable control-plane wheel; official installers compose the companion packages without duplicating their source.

## Native acceleration

`kitt/native/bridge.py` selects the shared `kitt_native` extension when available and otherwise uses the safe Python fallback. The model-facing API does not change: the compact policy-governed `kitt_runtime` surface remains the execution boundary.

The Rust implementation and its Rust CI belong exclusively to `rfdetoni/kitt-toolbox`; this repository no longer contains or builds Rust crates.

## Install / update

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.sh | bash
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.ps1 | iex
```

The standalone installer composes the Agent with the Assistant runtime and Evolution/Evals packages. If Rust/Cargo is available it also builds the shared native wheel from `kitt-toolbox`; otherwise the Agent keeps the portable Python backend. Use `--no-native` / `-NoNative` to explicitly skip native acceleration.

For the complete ecosystem, including the resident Assistant service and reverse proxy, use `rfdetoni/kitt`.

Requirements: Git and Python 3.12+. Rust is optional for Agent execution.

## Usage

```bash
kitt
kitt --root /path/to/project
kitt models
kitt doctor
kitt daemon status
kitt remote status
kitt evolve runs
kitt --help
```

Daemon/remote/evolution commands are available in the composed installation through their separately owned companion packages.

Inside the TUI, `/reasoning 0-100` and the reasoning shortcuts update the execution model. When using `kitt-reverse-proxy`, KITT keeps a stable conversation/session ID and forwards reasoning effort without creating a new browser chat per turn.

## Runtime design

KITT stays intentionally hybrid. Flexible orchestration and provider I/O remain Python; deterministic CPU/data-heavy repository work can use the Rust accelerator. Process execution is bounded and diagnostic tails are preserved rather than accumulating unbounded stdout/stderr.

Security invariants include workspace path containment, sanitized subprocess environments, single-use approval grants, capability intersection for child agents, bounded output/artifacts, secret-aware egress policy and fail-closed tool validation.

## Configuration

Configuration precedence is:

1. CLI arguments
2. environment variables
3. KITT Control Center overrides
4. runtime defaults

Useful switches include `KITT_SAFE_RUNTIME`, `KITT_DAEMON`, `KITT_DAEMON_AUTO_START`, `KITT_RETAINED_AGENTS` and `KITT_SCHEDULER`. Runtime-related switches take effect when `kitt-assistant-runtime` is installed.

## Development

Agent control-plane validation is Python-only:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python packaging/verify_cleanroom.py
```

Rust formatting, Clippy, tests and native wheel builds run in `kitt-toolbox`. Assistant daemon/remote tests run in `kitt-assistant`; Evolution/Evals tests run in `kitt-ai-workers`. The `rfdetoni/kitt` integration workflow freezes every repository to immutable SHAs and validates the composed namespace across repositories.

## License

MIT. See `LICENSE`.
