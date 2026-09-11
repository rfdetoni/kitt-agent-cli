# K.I.T.T. Agent CLI

<p align="center">
  <strong>Local-first autonomous coding-agent control plane.</strong><br>
  Python orchestration · SQLite/FTS5 workspace intelligence · optional Rust acceleration · MCP · plugins · multi-agent execution
</p>

<p align="center">
  <a href="https://github.com/rfdetoni/kitt-agent-cli/blob/main/LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white">
  <img alt="Local first" src="https://img.shields.io/badge/design-local--first-6f42c1">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-FTS5-003B57?logo=sqlite&logoColor=white">
</p>

K.I.T.T. Agent CLI is the execution and orchestration layer of the K.I.T.T. ecosystem. It combines repository intelligence, provider/model routing, goals, child agents, memory-aware context, policy enforcement, approvals, plugins and external tools behind a compact model-facing runtime.

The Agent remains portable Python. Deterministic CPU/data-heavy work can be accelerated by the shared Rust `kitt_native` extension without changing the model-facing API.

---

## What’s included

- Autonomous agent loop and goal-oriented execution.
- Provider/model routing for local and remote models.
- SQLite/FTS5 repository intelligence and history.
- Compact, policy-governed `kitt_runtime` tool surface.
- Child agents, retained agents and bounded concurrent execution.
- Workspace capability policy and single-use approvals.
- MCP servers/tools, plugins, hooks and external integrations.
- Dreaming/memory consolidation and context compaction.
- Quality gates, evaluation hooks and self-evolution integration.
- Portable Python fallback plus optional native Rust acceleration.
- Daemon/remote integration through the separately packaged Assistant runtime.

---

## Quick links

- **Complete ecosystem installer:** https://github.com/rfdetoni/kitt
- **Container image (GHCR):** https://github.com/rfdetoni/kitt-agent-cli/pkgs/container/kitt-agent-cli
- **Reverse proxy:** https://github.com/rfdetoni/kitt-reverse-proxy
- **Native engine:** https://github.com/rfdetoni/kitt-toolbox
- **Resident assistant:** https://github.com/rfdetoni/kitt-assistant
- **AI workers & evolution:** https://github.com/rfdetoni/kitt-ai-workers
- **Protocol contracts:** https://github.com/rfdetoni/kitt-protocol
- **Persistent memory:** https://github.com/rfdetoni/kitt-memory

---

## Requirements & compatibility

- Python **3.12+**.
- Git for repository workflows.
- Rust/Cargo is optional for standalone Agent execution; the pure-Python backend remains available.
- The complete K.I.T.T. ecosystem installer resolves the native/runtime dependencies automatically.

For the full supported stack, prefer installing through [`rfdetoni/kitt`](https://github.com/rfdetoni/kitt).

---

## Installation

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.sh | bash
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.ps1 | iex
```

The standalone installer composes the Agent with the Assistant runtime and Evolution/Evals packages. If Rust/Cargo is available it can also build the shared native wheel from `kitt-toolbox`; otherwise K.I.T.T. keeps the portable Python backend.

Use `--no-native` / `-NoNative` to explicitly skip native acceleration.

---

## Running the Agent

```bash
kitt
kitt --root /path/to/project
kitt models
kitt doctor
kitt --help
```

Composed installations also expose companion workflows:

```bash
kitt daemon status
kitt remote status
kitt evolve runs
```

Inside the TUI, `/reasoning 0-100` and reasoning shortcuts update the execution model. With `kitt-reverse-proxy`, the Agent keeps a stable conversation/session ID and forwards reasoning effort without creating a new browser conversation every turn.

---

## Docker

Docker is an optional execution mode; native installation remains supported. Every semantic release tag publishes the Agent image to GitHub Container Registry (GHCR) with OCI provenance/SBOM metadata.

Official image:

```text
ghcr.io/rfdetoni/kitt-agent-cli
```

Release tags publish the following aliases:

```text
vMAJOR.MINOR.PATCH
MAJOR.MINOR.PATCH
MAJOR.MINOR
MAJOR
latest
```

`latest` tracks the newest stable release. For reproducible environments, pin the complete `vMAJOR.MINOR.PATCH` tag or an immutable image digest instead.

Pull and run the published image against the current directory:

```bash
docker pull ghcr.io/rfdetoni/kitt-agent-cli:latest

docker run --rm -it \
  -v "$PWD:/workspace" \
  -v kitt-agent-state:/home/kitt/.kitt \
  ghcr.io/rfdetoni/kitt-agent-cli:latest
```

Package page: https://github.com/rfdetoni/kitt-agent-cli/pkgs/container/kitt-agent-cli

The release image currently targets `linux/amd64`. The image runs as a non-root `kitt` user with UID/GID `1000`.

### Build from source

To build the current checkout locally:

```bash
docker build -t kitt-agent-cli:dev .
docker run --rm -it \
  -v "$PWD:/workspace" \
  -v kitt-agent-state:/home/kitt/.kitt \
  kitt-agent-cli:dev
```

On Linux hosts with different IDs, build with matching values so bind-mounted workspaces remain writable:

```bash
docker build \
  --build-arg KITT_UID="$(id -u)" \
  --build-arg KITT_GID="$(id -g)" \
  -t kitt-agent-cli:dev .
```

The base image intentionally does not contain every project toolchain. For Java, Node, Rust or other project-specific builds, derive a project image with the required SDKs or use the native runtime. Avoid mounting `/var/run/docker.sock`, the host root filesystem or using `--privileged` unless that authority is explicitly required and understood.

For the integrated Agent + reverse-proxy + browser stack, use the root [`rfdetoni/kitt`](https://github.com/rfdetoni/kitt) `compose.yaml`. Services running on the host, such as Ollama or LM Studio, can be addressed through `host.docker.internal` where supported; the root Compose also defines the Linux `host-gateway` mapping for the Agent container.

---

## Architecture

`kitt-agent-cli` owns the **hot-path coding-agent control plane**:

```text
User / TUI
    │
    ▼
Turn Processor
    │
    ├── Context / memory / compaction
    ├── Model & provider routing
    ├── Policy / approvals / quality gates
    ├── Goals / child agents / scheduling
    │
    ▼
kitt_runtime
    │
    ├── repository operations
    ├── process execution
    ├── plugins / MCP / hooks
    └── optional kitt_native acceleration
```

Heavy or independently deployable capabilities are intentionally owned elsewhere:

| Repository | Ownership |
| --- | --- |
| `kitt-toolbox` | Rust `kitt-native-engine`, PyO3 binding and `kitt_native` wheel |
| `kitt-assistant` | resident Rust daemon/control center and `kitt-assistant-runtime` Python package |
| `kitt-ai-workers` | `kitt-evolution`, `kitt-evals` and optional STT/ML workers |
| `kitt-reverse-proxy` | authorized browser/API gateway |
| `kitt-memory` | shared persistent memory engine |
| `kitt-protocol` | cross-component contracts |

These Python distributions compose through the shared `kitt.*` namespace rather than duplicating source.

---

## Native acceleration

`kitt/native/bridge.py` selects the shared `kitt_native` extension when available and otherwise uses the safe Python implementation.

The model-facing contract does not change between backends. Native acceleration is an implementation detail behind the same bounded `kitt_runtime` surface.

Rust implementation and Rust CI belong to `kitt-toolbox`; this repository intentionally does not build Rust crates.

---

## Security & autonomy

K.I.T.T. treats tool execution as an authority boundary rather than a convenience API. Core invariants include:

- workspace path containment;
- sanitized subprocess environments;
- single-use approval grants;
- capability intersection for child agents;
- bounded tool output and artifacts;
- secret-aware egress policy;
- fail-closed tool validation;
- explicit mutation/exploration policy;
- model output treated as untrusted input to the runtime.

Autonomy can vary by workspace, but permissions remain explicit and policy-governed.

---

## Configuration

Configuration precedence is:

1. CLI arguments;
2. environment variables;
3. K.I.T.T. Control Center overrides;
4. runtime defaults.

Useful runtime switches include:

```text
KITT_SAFE_RUNTIME
KITT_DAEMON
KITT_DAEMON_AUTO_START
KITT_RETAINED_AGENTS
KITT_SCHEDULER
```

Daemon/remote switches become active when `kitt-assistant-runtime` is installed.

---

## Performance philosophy

The Agent keeps flexible orchestration in Python and avoids native complexity where it does not materially help. Native code is reserved for deterministic data-plane work where profiling justifies it.

Hot-path design emphasizes:

- bounded repository/process output;
- SQLite/FTS5 local indexing;
- semantic context filtering before model calls;
- context caching and compaction;
- minimal model-facing tool schemas;
- optional native acceleration without mandatory native runtime cost.

---

## Development

Install the development environment and run the Agent-owned validation suite:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python packaging/verify_cleanroom.py
```

Rust formatting, Clippy, tests and native wheel builds run in `kitt-toolbox`. Assistant daemon/remote tests run in `kitt-assistant`; Evolution/Evals tests run in `kitt-ai-workers`.

The root `rfdetoni/kitt` integration workflow freezes every component to immutable SHAs and validates the composed shared namespace across repositories.

---

## Contributing

Keep the hot path small, observable and deterministic. Prefer KISS/DRY/YAGNI over framework accumulation, preserve repository ownership boundaries and add dependencies only when they provide measurable value to execution quality, latency or maintainability.

---

## K.I.T.T. ecosystem

| Repository | Responsibility |
| --- | --- |
| [`kitt`](https://github.com/rfdetoni/kitt) | installer and ecosystem composition |
| [`kitt-agent-cli`](https://github.com/rfdetoni/kitt-agent-cli) | autonomous agent control plane |
| [`kitt-reverse-proxy`](https://github.com/rfdetoni/kitt-reverse-proxy) | authorized provider gateway |
| [`kitt-protocol`](https://github.com/rfdetoni/kitt-protocol) | shared contracts and SDKs |
| [`kitt-memory`](https://github.com/rfdetoni/kitt-memory) | persistent memory engine |
| [`kitt-toolbox`](https://github.com/rfdetoni/kitt-toolbox) | native data plane |
| [`kitt-ai-workers`](https://github.com/rfdetoni/kitt-ai-workers) | isolated AI/ML workers and evals |
| [`kitt-assistant`](https://github.com/rfdetoni/kitt-assistant) | resident assistant and Control Center |

---

## License

MIT. See [LICENSE](LICENSE).