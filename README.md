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
- Child agents, retained agents and bounded concurrent execution with atomic mutation fencing and fair contention handling.
- Stable per-child reverse-proxy sessions: each child gets its own browser conversation, while a retained child reuses that same session across reassigned tasks.
- Workspace capability policy and single-use approvals.
- MCP servers/tools, plugins, hooks and external integrations.
- Sixteen bundled first-party plugins for project intelligence, testing, LSP discovery, API/migration analysis, worktrees, quality, dependencies, CI, containers and opt-in integrations.
- Dreaming/memory consolidation and context compaction.
- Quality gates, evidence-led completion, risk-aware adversarial review and self-evolution integration.
- Portable Python fallback plus optional native Rust acceleration.
- Daemon/remote integration through the separately packaged Assistant runtime.

---

## Quick links

- **Complete ecosystem installer:** https://github.com/rfdetoni/kitt
- **GHCR packages:** https://github.com/rfdetoni?tab=packages
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
kitt sessions
kitt incident --since 30m
kitt doctor
kitt --help
```

Composed installations also expose companion workflows:

```bash
kitt daemon status
kitt remote status
kitt evolve runs
```

### Runtime resilience and operations

KITT keeps maintenance work separate from the execution hot path. Context
compaction can use the configured `summarize`/context route when that route
is independent from the execution lane, fits the request, and is not a
browser-backed reverse proxy session. If no suitable maintenance model is
available, compaction stays deterministic.

Provider recovery is replay-aware: rate-limit responses honor `Retry-After`,
transient retries use bounded proportional jitter, and a request is never
automatically replayed after it has already emitted stream output. Direct
OpenAI-compatible, OpenAI Responses, and Anthropic streams also require an
explicit protocol completion signal instead of silently accepting a truncated
connection.

For operator visibility:

```bash
kitt sessions
kitt sessions --all --json
kitt incident --since 30m
kitt incident --since 2h --session <id> --json
```

`kitt sessions` enriches daemon/local sessions with durable turn state,
staleness, last error and token usage. `kitt incident` reconstructs a bounded
timeline from KITT's structured local logs. See
[docs/OPERATIONS.md](docs/OPERATIONS.md) for details.

Completed, failed, cancelled and timed-out child history no longer consumes
the resident-child admission limit; only live/reusable retained state counts
toward that bound.

Child mutations are coordinated at the real side-effect boundary. Path and
symbol leases are acquired atomically after policy/approval checks, overlapping
directory/file writes conflict, waiting writers use a durable FIFO queue, and
active child owners renew their leases while running or waiting for approval.
Structural edits may additionally hold read leases on bounded dependencies.
This complements per-child Git worktrees instead of replacing them.

Dynamic plugin/MCP tools pass a bounded registration contract before becoming
model-visible: names, handler shape, description and JSON schema are validated,
built-in tools cannot be shadowed, and another extension cannot take over an
existing tool name. Mutable GitHub Actions used by install-hygiene are pinned to
immutable revisions as part of the same supply-chain boundary.

### Approval and update lifecycle

- Tool/command approval prompts remain active **without an automatic timeout** until the user explicitly allows, denies, or cancels them.
- An issued grant remains short-lived, action-bound and single-use; removing the waiting timeout does not make grants reusable.
- If autonomous `process.run` cannot use a strong OS sandbox, KITT degrades to a durable user approval instead of returning a terminal ASK-policy error to the model.
- Pending tool conversations stay pinned in `kitt-reverse-proxy`, so session idle/LRU eviction cannot discard a conversation while KITT is waiting for the human decision.
- Daemon-backed approvals require `kitt-assistant-runtime >= 0.2.14`; that runtime treats `PENDING` decisions as durable state with no wall-clock expiry and never evicts an active approval to make room for a newer one.
- Standalone install/update scripts stop resident KITT services, including the daemon and known proxy/gateway services, before replacing the runtime.

Inside the TUI, `/reasoning 0-100` and reasoning shortcuts update providers that support API-side reasoning control. `/verify-full` is available from the command palette/menu and toggles the persistent `KITT_AGENT_VERIFY_FULL` runtime flag. When enabled, KITT adds bounded project compile/typecheck/lint/test gates after edits; when disabled, fast structural validation and targeted checks remain active. With `kitt-reverse-proxy`, the Agent keeps a stable conversation/session ID without creating a new browser conversation every turn. For browser-backed WebChat providers, reasoning/thinking remains configured in the authenticated WebChat UI; the legacy `X-Kitt-Reasoning-Effort` header is compatibility-only and is ignored by the reverse proxy.

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

GHCR packages index: https://github.com/rfdetoni?tab=packages

The direct package page is created by GitHub after the image is published for the first time. Until then, use the packages index above and the canonical image name `ghcr.io/rfdetoni/kitt-agent-cli`.

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
| `kitt-reverse-proxy` | authorized browser/API gateway with isolated child sessions and provider-plugin SDK |
| `kitt-memory` | shared persistent memory engine |
| `kitt-protocol` | cross-component contracts |

These Python distributions compose through the shared `kitt.*` namespace rather than duplicating source.

---

## First-party plugins

KITT 0.69 ships 16 bundled plugins through the same extension subsystem used by external plugins. Ten read-only/deterministic plugins are enabled by default: project intelligence, test impact, LSP discovery, OpenAPI inspection, migration guard, Git worktree planning, quality reporting, dependency audit, CI inspection and container inspection.

Release, GitHub, database, browser, cloud and observability plugins are bundled but disabled by default. Enable them per workspace with `kitt plugins enable <name>`. Bundled plugins are trusted as part of the installed KITT distribution, can be disabled, and cannot be shadowed by global/workspace plugins with the same name. Network access, credentials and mutations remain outside these plugin handlers and continue through MCP or the policy-governed runtime.

See [docs/plugins.md](docs/plugins.md) for the full catalog, tool names, trust model and extension authoring guidance.

---

## Native acceleration

`kitt/native/bridge.py` selects the shared `kitt_native` extension when available and otherwise uses the safe Python implementation.

The model-facing contract does not change between backends. Native acceleration is an implementation detail behind the same bounded `kitt_runtime` surface.

Rust implementation and Rust CI belong in `kitt-toolbox`; this repository intentionally does not build Rust crates.

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

`KITT_AGENT_VERIFY_FULL` is managed persistently from inside KITT (`/verify-full` or the command palette). The environment variable remains a compatibility fallback only until a persisted menu choice exists. Verification uses a trusted global baseline at `~/.kitt/verification/baselines.json` plus optional constrained project overrides in `.kitt/verification.json`; project files can tune known checks and timeouts but cannot inject arbitrary commands.

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

## Prompt language policy

K.I.T.T.-generated system, developer, orchestration, recovery, retry, tool-protocol, and validation prompts sent to models are authored in English. User-authored requests are preserved verbatim in their original language. Multilingual routing and intent-detection vocabularies remain multilingual because they are classifier data, not model instructions.

Deterministic semantic routing treats imperative workspace conversions and migrations (for example, converting a Gradle backend to Maven) as mutation-capable implementation work, while explanatory `how to` questions remain read-only.
