# K.I.T.T. Agent CLI

[Português (Brasil)](README.pt-BR.md)

<p align="center">
  <strong>Local-first autonomous coding-agent control plane.</strong><br>
  Python orchestration · SQLite/FTS5 workspace intelligence · optional Rust acceleration · MCP · plugins · multi-agent execution
</p>

<p align="center">
  <a href="https://github.com/rfdetoni/kitt-agent-cli/blob/main/LICENSE"><img alt="License MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.14+" src="https://img.shields.io/badge/Python-3.14%2B-3776AB?logo=python&logoColor=white">
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
- Seventeen bundled first-party plugins for project intelligence, testing, LSP discovery, API/migration analysis, worktrees, quality, dependencies, CI, containers, Figma and opt-in integrations.
- Dreaming/memory consolidation and context compaction.
- Stable prompt-prefix layout with dynamic turn context moved behind invariant execution/tool contracts.
- Consumed tool-result receipts reclaim model context without mutating browser-backed conversation identity.
- Token-pressure history compaction and dependency-aware parallel read-only programmatic flows.
- Durable model-input ledger, Task Episodes, evidence states, sparse session projections and deterministic replay fingerprints.
- Bounded read-only `program.execute` for loops/branching without arbitrary code execution or approval bypass.
- Harness snapshots/materialization receipts plus evidence-backed intervention tracking for controlled self-improvement.
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
- **Quick start:** [QUICKSTART.md](QUICKSTART.md)
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Runtime reference:** [docs/RUNTIME_REFERENCE.md](docs/RUNTIME_REFERENCE.md)
- **Accessibility:** [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md)
- **Performance:** [docs/PERFORMANCE.md](docs/PERFORMANCE.md)
- **Domain architecture:** [docs/DOMAIN_ARCHITECTURE.md](docs/DOMAIN_ARCHITECTURE.md)
- **Naming/license notes:** [docs/NAMING_AND_TRADEMARK.md](docs/NAMING_AND_TRADEMARK.md)
- **Figma integration:** [docs/FIGMA.md](docs/FIGMA.md)

---

## Requirements & compatibility

- Python **3.14+**.
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


### TUI navigation

The full-screen TUI uses retained `prompt_toolkit` controls and enables application mouse support by default:

- **Mouse wheel** scrolls only the panel under the pointer. Scroll routing is isolated through a central panel registry.
- **F10** or `/mouse` toggles TUI mouse reporting off/on. Turn it off when native terminal text selection is preferred.
- **Ctrl+P** is the universal discovery path for commands and actions.
- Direct `Ctrl+X` chords are intentionally limited to **Ctrl+X N** (new conversation), **Ctrl+X B** (sidebar) and **Ctrl+X A** (agents).
- **F12** opens the model/provider wizard. Provider selection, endpoint configuration and authentication remain inside the same modal surface.
- Conversation, timeline, diff, agents, autonomy, **KITT Reverse Proxy** and help share one contextual panel; **Left/Right** switches its active tab.

See [docs/TUI_ARCHITECTURE.md](docs/TUI_ARCHITECTURE.md) for implementation boundaries and performance invariants.

### Durable evidence and controlled evolution

KITT persists the exact model request immediately before provider execution in a
local append-only session ledger. Model-visible inputs can therefore be replayed
without relying on transient prompt assembly state. Session projections fold that
event stream into bounded current views and checkpoint sparsely to avoid turning
SQLite into a hot-path query fan-out.

A Task Episode represents one user objective plus its acceptance boundary. It may
span multiple turns when backed by a durable Goal. Evidence is tracked separately
from outcome labels with the states `PRESENT`, `WIRED`, `EXERCISED`,
`OUTCOME_SUPPORTED`, `MISSING`, `UNOBSERVED`, and `NOT_APPLICABLE`.
Missing observation is never silently converted into success or failure.

Harness changes can be frozen as content-addressed snapshots, accompanied by
materialization receipts and compared through an intervention ledger. An
intervention records its baseline, primary metric, guardrail metric, validation
route, comparison window and stop/revert condition. KITT only marks a later result
as outcome-supported when the comparison is explicit and the guardrail did not
regress.

`flow.execute` remains the deterministic DAG fast path. `program.execute`
adds bounded `call`, `set`, `for_each`, `if`, and `return` control over
the same read-only SafeRuntime operations. It never evaluates model-supplied
Python, JavaScript or shell code.

Runtime state is also captured as revisioned presets and privacy-safe component
snapshots. Controlled baseline/candidate experiments reuse the existing
WorkspaceCoordinator worktree isolation, learning capture emits smallest-owner
proposals, Episode efficiency keeps missing telemetry distinct from zero, and
golden replay verifies provider-boundary request stability without calling a
model.

See [docs/HARNESS_EVOLUTION.md](docs/HARNESS_EVOLUTION.md) for the complete
evidence, experiment and controlled-evolution lifecycle.

### Runtime resilience and operations

KITT keeps maintenance work separate from the execution hot path. Prompt
construction also keeps the invariant agent/tool contract at the front of the
provider request; query-specific memory, skills, formatting, harness state,
repository evidence and conversation history are appended afterwards so
provider prefix caches can reuse the stable portion.

After a stateless/local model consumes a sufficiently large host-tool result and
proposes its next action, KITT replaces that old payload with a deterministic
receipt containing provenance, digest and a bounded excerpt. Browser-backed
reverse-proxy histories are left byte-stable to avoid session resets. History
compaction is driven by token pressure against the selected model's real input
budget instead of a fixed message-count threshold.

Read-only `flow.execute` plans form a bounded dependency DAG. Independent steps
run concurrently by default (bounded by `max_parallel`), while references such
as `$step.field` and explicit `depends_on` edges preserve ordering. Intermediate
payloads remain hidden from the model.

Context compaction can use the configured `summarize`/context route when that route
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
- In `allow-all` / autonomous mode, ordinary commands keep the user's explicit ALLOW decision and use the best available process isolation when a strong OS sandbox is unavailable. Explicitly denied argv, network elevation, control-plane mutation and other critical authority boundaries remain fail-closed.
- Pending tool conversations stay pinned in `kitt-reverse-proxy`, so session idle/LRU eviction cannot discard a conversation while KITT is waiting for the human decision.
- Daemon-backed approvals require `kitt-assistant-runtime >= 0.2.16`; that runtime treats `PENDING` decisions as durable state with no wall-clock expiry and never evicts an active approval to make room for a newer one.
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

KITT ships 17 bundled plugins through the same extension subsystem used by external plugins. Ten read-only/deterministic plugins are enabled by default: project intelligence, test impact, LSP discovery, OpenAPI inspection, migration guard, Git worktree planning, quality reporting, dependency audit, CI inspection and container inspection.

Release, GitHub, database, browser, cloud, observability and Figma plugins are bundled but disabled by default. Enable them per workspace with `kitt plugins enable <name>`. Bundled plugins are trusted as part of the installed KITT distribution, can be disabled, and cannot be shadowed by global/workspace plugins with the same name. Network access, credentials and mutations remain outside these plugin handlers and continue through MCP or the policy-governed runtime.

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for environment setup, ownership boundaries, validation gates, clean-room rules and pull-request expectations. Keep the hot path small, observable and deterministic; prefer KISS/DRY/YAGNI over framework accumulation.

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




### Mouse-first TUI interactions

Agent CLI 0.72 reimplements the useful interaction ideas from OpenTUI in the existing Python/prompt_toolkit TUI rather than introducing a second renderer. Interactive surfaces register local-cell hit regions during rendering, so mouse input follows the content actually visible after resize and scrolling.

Mouse and keyboard share the same controller actions:

- command-palette rows can be hovered and clicked;
- approval actions can be selected and confirmed with the pointer;
- conversation rows and timeline rows are clickable;
- autonomy policy choices are clickable;
- model and provider lists retain their existing hover/click behavior;
- KITT Reverse Proxy service, plugin, profile and action controls are clickable.

Mouse support remains enabled by default. Use `F10` or `/mouse` when native terminal text selection/copy is preferred. Wheel routing remains isolated to the panel under the pointer.

Agent CLI **0.72.3** hardens this path: mouse-down records the semantic target without virtualizing the list before mouse-up, plain-text modal scrolling no longer snaps back to row zero, modal projections strip raw ANSI escape sequences before prompt_toolkit renders them, and Reverse Proxy action buttons expose explicit hover feedback without moving their hit-test geometry.

The interaction layer is inspired by OpenTUI's rendered-cell hit testing, focus ownership and keyboard/mouse parity, but is an independent KITT implementation with no OpenTUI runtime dependency.

## KITT Reverse Proxy control center

Agent CLI 0.72 integrates the multi-instance control plane from KITT Reverse Proxy 4.2. Open **KITT Reverse Proxy** from `Ctrl+P` or run `/reverse-proxy`.

The panel opens immediately in a loading state and then refreshes active instances, named browser profiles and connection plugins without blocking the visible modal. Common controls:

- `n`: open **Novo serviço**, choose a provider plugin/profile and start the instance from inside the modal with **Enter** or a mouse click on **Iniciar serviço**.
- `p`: manage named browser profiles.
- `u`: start from a custom WebChat URL.
- `c`: bind the selected instance to Context.
- `e`: bind the selected instance to Principal/Code.
- `v`: bind the selected instance to Validation.
- `r` / `x`: restart or stop the selected instance.
- `F5`: refresh the control-plane snapshot.

Example with independent providers:

```text
Context        -> gemini-context -> Gemini Web  -> http://127.0.0.1:3000
Principal/Code -> chatgpt-code   -> ChatGPT Web -> http://127.0.0.1:3001
```

Equivalent commands:

```text
/reverse-proxy profile create context-google gemini
/reverse-proxy profile create coding-openai chatgpt
/reverse-proxy start gemini --profile context-google --id gemini-context --role context
/reverse-proxy start chatgpt --profile coding-openai --id chatgpt-code --role code
```

Role bindings reuse the existing model router and are persisted in `.kitt-router.json`. Agent CLI does not scan OS process tables; lifecycle and provider discovery remain owned by the reverse-proxy control plane.

See `docs/REVERSE_PROXY_CONTROL.md`.


### Agent CLI 0.72.3 — Reverse Proxy mouse polish

- Reverse Proxy action buttons now expose visible hover feedback while preserving identical cell geometry between normal and hovered states.
- Click activation continues to use the same semantic controller actions as keyboard shortcuts, with press/release target matching.
- This patch completes the mouse/scroll/modal cleanup introduced in 0.72.2 without adding a new renderer or UI dependency.

## Agent CLI 0.73.0 — performance hardening

Agent CLI 0.73.0 reduces steady-state overhead without changing the tool or TUI contracts:

- Reverse Proxy lifecycle operations prefer the resident loopback control plane and retain CLI subprocess fallback for older proxy versions or recovery.
- The local TUI event bridge wakes on producer notifications instead of polling an empty queue every 10 ms.
- Transcript rendering caches stable prompt_toolkit fragments per block; streaming/running blocks invalidate only their own cached projection.
- Turn finalization avoids building large temporary concatenated history strings purely for token accounting.
- `python scripts/benchmark_tui.py` measures cold and warm rendering for a 500-block transcript.

## Agent CLI 0.74.0 — long-session efficiency and approval durability

Agent CLI 0.74.0 completes the performance hardening started in 0.73:

- Pending human approvals and persisted PendingAction records no longer carry an automatic timeout. The existing TTL applies only to an already-issued single-use ApprovalGrant.
- The execution loop uses an incremental TokenLedger so unchanged messages are not re-estimated on every tool/rebudget pass.
- Prompt truncation starts from the calibrated character/token ratio and uses a small bounded correction loop instead of binary-searching large payloads.
- The retained transcript is virtualized during normal operation. The most recent block window is rendered first, scrolling upward expands it, and Ctrl+Home materializes the full retained transcript.
- Transcript block ids are monotonic across the 500-block retention rollover, preventing cache-key collisions in long sessions.
- `python scripts/benchmark_tui.py` now covers 100, 1,000 and 10,000-block scenarios; `python scripts/benchmark_context_budget.py` measures 32 KiB, 256 KiB and 1 MiB token-budget workloads.

## Agent CLI 0.74.1 — compatibility hardening

This patch completes the 0.74 performance work with two compatibility safeguards:

- token accounting is lazily initialized for partial test/harness TurnProcessor construction while normal runtime behavior remains incremental;
- persisted PendingAction records explicitly retain the zero-expiry sentinel across repository round trips, keeping user-facing approvals actionable until a decision/cancel while issued grants remain short-lived and single-use.

The Reverse Proxy control guide is also aligned with the profile-scoped BrowserHost introduced in Reverse Proxy 4.4.


### Shared memory contract 0.2

Agent 0.74.3 pins kitt-protocol 0.2.0. The shared-memory client can pass an optional conversation `scope_key` and point-in-time `as_of` to kittd while keeping workspace calls source-compatible. Conversation-scoped writes require an explicit key, and an explicit recall limit of `0` remains empty rather than being coerced to one result.


### Assistant/runtime alignment

Agent 0.74.3 pins the validated kitt-assistant 0.1.4 / runtime 0.2.16 snapshot in CI, PR checks, architecture validation and release composition. This freezes the schema-v4 shared-memory integration at the same Assistant revision used by the ecosystem lock.


## Agent CLI 0.74.4 — memory and approval consistency

Agent CLI 0.74.4 removes two remaining split-brain state paths:

- Agent-created project memory is persisted first in the Agent structured store and mirrored to shared `kitt-memory` when available. Shared recall is merged with local records instead of replacing them, so restarting or losing `kittd` cannot make locally durable memories disappear.
- Markdown memory is now recovery-only, uses cross-process lock + atomic replace, and no longer seeds invented user preferences or project rules.
- Clearing project memory archives the Agent's structured records and removes exact shared mirrors when the daemon is reachable.
- Approval denial now persists the durable state before updating the in-memory broker, matching grant/consume fail-closed semantics.
- That historical 0.74.4 release still supported Python 3.12+, but the current package and CI policy has since advanced to Python 3.14+ across supported desktop operating systems. See [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).


## Agent CLI 0.74.6 — current-interpreter and protocol alignment

Agent CLI 0.74.6 requires Python 3.14+, matching the interpreter validated across the ecosystem. It pins KITT Protocol 0.2.1 and the Assistant 0.1.6/runtime 0.2.18 snapshot, while preserving the memory-authority, approval-durability and daemon-protocol fixes introduced in 0.74.4.


### 0.74.6 container alignment

The official Agent container now uses Python 3.14, matching the package's declared and CI-validated interpreter floor. This fixes the 0.74.5 release-container failure where the Docker image still used Python 3.12.


## Agent CLI 0.75.0 — semantic UI, Backend IR and long-session state

Agent 0.75.0 adopts KITT Protocol 0.3.0 and completes the clean-room semantic UI/backend architecture.

- `surface.*` SafeRuntime operations publish, patch, query and route semantic actions through the host-owned allowlisted Surface catalog. Surface payloads are declarative data, never executable UI code; semantic actions return to the host runtime rather than invoking arbitrary tools.
- `backend.validate`, `backend.plan` and `backend.compile` provide a Backend IR for schemas, entities, queries, commands, endpoints, events, workflows, policies, jobs and observability. The model describes intent; deterministic host code validates dependencies, emits impact-oriented ChangeSets and compiles bounded Python/TypeScript/Rust contract files. Actual repository mutation still uses `repo.write_file` / `patch.apply` and the normal policy/approval path.
- Context compaction now stores a structured WorkingState containing objective, current state, constraints/decisions, affected artifacts, errors/corrections, pending work and validation evidence.
- Large consumed host-tool results are externalized to ArtifactStore before their active-context message is replaced by a deterministic receipt. `artifacts.search` and paged `artifacts.hydrate` recover only the relevant evidence later.
- Browser-backed reverse-proxy histories remain byte-stable and skip receipt mutation.

The implementation is independent KITT code and does not copy OpenUI/OpenViking source, APIs or naming.


### Agent CLI 0.75.1 release alignment

0.75.1 is the promoted semantic-IR snapshot after cross-platform validation. It keeps the 0.75 Surface/Backend IR feature set, fixes Surface projection to occur on completed tool events, and aligns runtime fallback/package/lock version metadata for deterministic release automation.


## Agent CLI 0.77.1 — Assistant 0.1.10 composition alignment

Agent CLI 0.77.1 keeps the 0.77 evidence-first runtime behavior unchanged and updates the frozen Assistant companion revision used by CI, PR validation, architecture checks and release composition to Assistant 0.1.10 / runtime 0.2.21. Package and uv-lock metadata are aligned to the patch release.

## Agent CLI 0.77.0 — evidence-first agentic execution

### Assistant compatibility for 0.77.0

Agent CLI 0.77.0 CI, PR checks, architecture validation and release composition are pinned to **K.I.T.T. Assistant 0.1.10 / kitt-assistant-runtime 0.2.21**. The companion runtime accepts Agent 0.76.x and 0.77.x; daemon/remote ownership, durable approvals and standalone kitt-memory authority remain unchanged.



Agent CLI 0.77.0 hardens application-sized coding requests without requiring the user to split prompts manually:

- broad IMPLEMENT/DEBUG/REFACTOR turns begin with exactly one read-only repository inspection;
- discovery runs before the optional Architect, reducing time-to-first-tool and avoiding planning against incomplete evidence;
- the complete user objective remains authoritative while the execution loop advances through bounded milestones and host observations;
- existing files continue to use the adaptive edit-strategy selector, with symbol edits and compact patches preferred over unnecessary whole-file rewrites;
- official OpenAI Chat, OpenAI Responses, Anthropic and Gemini streaming adapters report explicit max-output truncation as a typed recoverable condition;
- truncation recovery discards the incomplete action and requests one smaller complete action, bounded to two recoveries rather than concatenating partial JSON, diffs or source text.

## Agent CLI 0.76.1 — TUI and reverse-proxy reliability

Agent CLI 0.76.1 fixes retained-TUI interaction and managed reverse-proxy startup regressions:

- transcript mouse-wheel scrolling is bidirectional: when follow-tail is disabled, the retained cursor is anchored to the requested vertical scroll row instead of snapping the window back toward the top;
- the red K.I.T.T. scanner advances whenever animations are enabled, including idle/home states where the HUD remains visible;
- Reverse Proxy `service start` and `service restart` use a dedicated 330-second startup budget over both the resident control channel and CLI compatibility fallback, while ordinary management calls keep their short timeout;
- regression coverage now exercises downward scrolling, idle scanner movement, and startup-timeout selection.

## Agent CLI 0.76.0 — single memory authority

Agent memory is now exclusively owned by `kitt-memory 0.4+` through the standalone loopback service `kitt-memoryd`.

- The Agent no longer persists `memories`, `memory_evidence`, `dream_runs`, memory vectors, knowledge concepts/links or correction memories in its HistoryDatabase.
- Schema migration v7 removes those obsolete local tables.
- Dreaming still performs ORIENT/GATHER/CONSOLIDATE/VALIDATE in the Agent because it uses conversation history and model routing, but all durable dream commits, status transitions, evidence/provenance and maintenance are sent to kitt-memoryd.
- `.kitt/memory/MEMORY.md` is a regenerable projection only; it is not a fallback database.
- If kitt-memoryd is unavailable, memory operations fail explicitly instead of silently creating a second local authority.
- The native subsystem no longer maintains a separate memory vector/knowledge/correction store. Semantic retrieval belongs to kitt-memory.


## Agent CLI 0.77.3 — autonomy, modal and action-summary reliability

Agent CLI 0.77.3 fixes three user-facing execution issues:

- `/autonomy allow-all` is authoritative for ordinary model-initiated commands. Missing strong OS sandbox support no longer silently converts an ALLOW decision into a second ASK modal; dangerous argv remains DENY, while explicit network and control-plane elevation still require dedicated authority.
- The approval surface is a true pointer modal. Blank clicks are consumed by the permission surface and only the visible approval labels are clickable, preventing focus clicks or modal-body clicks from falling through to the underlying TUI.
- Reverse Proxy `reasoning_summary` metadata is preserved through the native tool bridge and projected into the TUI as the primary description of what the model is doing and why, with the concrete tool/command shown underneath as technical detail. The summary remains bounded public progress metadata, not chain-of-thought.

## Agent CLI 0.77.2 — Assistant 0.1.11 compatibility pin

- CI now composes the Agent against the promoted Assistant 0.1.11 snapshot, which fixes the Linux voice-disabled native build used by the root ecosystem installer when ALSA development headers are unavailable.
- Agent runtime behavior and public tool/protocol contracts are unchanged from 0.77.1.
- Package metadata, source-tree fallback version and `uv.lock` are aligned at 0.77.2.


## Agent CLI 0.78.2 — dead-code and authority cleanup

- removes the obsolete Agent-local hybrid memory implementation and keeps durable corrections/concepts/links in `kitt-memoryd` only;
- removes dead native event wiring and obsolete native-memory parameters;
- current Agent history schema is created directly for new state and older local schema revisions are intentionally rejected instead of migrated;
- bundled first-party inspection plugins share one loader implementation instead of one Python setup wrapper per plugin;
- CI validates the current Memory/Assistant/Toolbox ecosystem revisions and adds unused-symbol checks.


## Agent CLI 0.78.3 — validated dead-code cleanup

- removes unused Python imports and local bindings exposed by the new Ruff gate while preserving intentional public compatibility exports;
- restores explicit compatibility aliases for `PromptToolkitBackend` and historical turn helpers instead of relying on incidental imports;
- restores the history timestamp dependency required by workspace persistence;
- keeps the standalone `kitt-memoryd` authority and current-schema-only Agent state policy introduced in 0.78.2;
- validates the cleanup across Docker, Prime Architecture, full CI and multi-OS PR checks before ecosystem promotion.
