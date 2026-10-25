# Changelog — K.I.T.T. Agent CLI

## 0.88.84 — shared RE image verification for fresh lab setup

Fresh `kitt re lab setup` now delegates Docker toolbox image preparation to the same pinned image reconciler used by the installer (`kitt re lab images prepare`), ensuring Ghidra, Frida and REA/Wine toolbox are present and smoke-verified without needless registry pulls of already healthy images. If any image cannot be repaired, setup stops before creating the Linux guest; existing Linux/Windows guests remain untouched. Regression tests assert reuse and failure behavior. The default installer already provisions the three independent images on Linux when Docker is available; no changes to Frida/Ghidra source versions or third-party pins.


## 0.88.83 — visible Docker installer preflight and actionable RE image recovery

- Linux first install now visibly warns when Docker Engine is unavailable and none of the Toolbox/Ghidra/Frida images were provisioned. `--no-re-tools` remains a quiet explicit opt-out, while `--with-re-tools` still requires successful provision or fails closed; existing labs still require successful repair. The installer never silently claims that RE executors are ready.
- RE image repair failures include bounded, per-method diagnostics for pinned registry pull and local Docker build, without revealing secrets, changing guest mounts, or relaxing container isolation. Documentation reflects actual auto-provisioning behavior. Regression tests cover missing daemon and recovery hints.
- Latest provided Agent CLI/Reverse Proxy logs (2026-10-24) were reviewed: the proxy's single HTTP cancellation was intentional and the Agent CLI now handles that signal as `TurnCancelled`; unrelated plan-mode/policy refusals are contracts, not permission bugs. No reverse-proxy changes required.
- Version metadata and `uv.lock` updated together. Docker image/version pins and all third-party dependencies are unchanged.


## 0.88.82 — child worktree fallback for an unborn Git HEAD

- Fix the observed `fatal: invalid reference: HEAD` when a child agent starts inside a repository created by `git init` but without an initial commit. Worktree isolation is enabled only after `git rev-parse --verify HEAD^{commit}` succeeds. Until then the coordinator reuses the existing explicit shared-workspace fallback; it never commits files or rewrites Git metadata automatically.
- Add regression coverage for the unborn-to-committed transition, preserving isolated child worktrees after a real initial commit. `pyproject.toml`, `uv.lock`, and installed package fallback version are synchronized; third-party pins unchanged.
- Installer-provisioned Toolbox, Ghidra, and Frida images and the host-policy RE execution route from 0.88.81 remain unchanged. Sandbox Docker socket access is intentionally not granted to `process.run`; use `re.lab.images` and `re.lab.analyze` operations.



## 0.88.81 — self-repair damaged RE executor images and diagnose sandbox Docker access

- KITT RE installer now retries **present but broken** local Toolbox/Ghidra/Frida image tags when their offline smoke test fails. A pinned GHCR pull is verified first, falling back to an isolated local Dockerfile build with a second offline smoke check. A failure of both methods remains explicit and blocking; no stale image is accepted as ready.
- When agent `process.run docker ...` fails due to an inaccessible Docker socket, KITT records a non-retryable `DOCKER_SANDBOX_SOCKET_UNAVAILABLE` diagnostic and points to the existing host-policy-governed `re.lab.images` and `re.lab.analyze` operations, rather than sending the agent into repeated container probes. This does not expose the Docker socket or elevate model permissions.
- Tests for stale image recovery, double failure and scoped Docker error classification; installer and existing Docker image version pins remain unchanged. Package metadata and uv.lock synchronized.


## 0.88.80 — RE Docker provisioning on first install; quiet intentional cancellations

- Linux installations with an already usable Docker daemon now automatically prepare and smoke-check all three pinned dedicated RE images (REA/Wine Toolbox, Ghidra, Frida), without requiring `--with-re-tools`; `--no-re-tools` skips the large downloads. Existing RE labs still fail visibly if Docker cannot be reached. Fresh machines without a working daemon are not silently assumed to have Docker.
- Operator-triggered `TransportCancelled` is classified as `TurnCancelled` rather than `TurnFailed` with duplicate error tracebacks in the Agent's tool loop. Other provider exceptions remain failures.
- Added regression checks for cancellation and the RE installer auto-detection contract. Version and `uv.lock` synchronized; RE executor image pins remain `0.88.77`, with no image changes, Windows guest resets, credentials or Docker socket exposure.


## 0.88.79 — repair missing RE Docker images during installation

- Added narrow model-visible `kitt_runtime re.lab.images` (host image inspection, read-only) and `re.lab.analyze` (root-agent-only, CAP_PROCESS_RUN and policy-gated host Ghidra execution using strictly validated source and resume IDs). This eliminates the need to expose a Docker socket to the model's process sandbox. Analysis is synchronous and remains subject to user/policy approvals; long-running job background management is a follow-up.
- Added `kitt re lab images status|prepare` for dedicated `ghidra`, `frida` and full RE Toolbox images. Reuse existing immutable image IDs; when missing, pull the version-pinned GHCR image, otherwise build from the project Dockerfile, then run a bounded offline smoke test for installed executables.
- The Linux `install.sh` now supports `--with-re-tools`; it automatically reconciles images when an existing operator RE lab is detected, with `--no-re-tools` opt-out. No Windows licensing, guest resets, Docker-socket exposure to agent sandboxes, or implicit unrequested VM creation.
- Tested image pull/build fallbacks, idempotence, errors, installer command flags. Third-party pins unchanged, uv.lock synchronized.


## 0.88.78 — fix RE Toolbox registry publication

- Corrected the Linux RE Toolbox publisher: it had built `base/wine/full-0.88.77` but attempted to tag/push the nonexistent `0.86.11` images. The GHCR publication now uses the pinned `0.88.77` Toolbox image tags, exactly matching the build and `vm_container.RE_TOOLBOX_VERSION` consumer.
- Added a focused regression check ensuring build, publish, and CLI image references stay aligned for all three Toolbox tiers, independently of the core Agent CLI version. Ghidra/Frida remain isolated `0.88.77` images (both build and publish previously passed).
- No runtime isolation, resource budget, external dependency, or Windows guest changes. Agent CLI version and `uv.lock` updated to `0.88.78`.



## 0.88.77 — dedicated Ghidra and Frida Docker executors (breaking)

- Ghidra 12.1.4 now runs from `kitt/re-ghidra:0.88.77`, Java plus SHA256-verified official Ghidra release only. `kitt re lab analyze` pins the local dedicated image by Docker image ID and never falls back to `kitt-re-full`; project resumption, read-only workspace, CPU/memory budgets, progress/heartbeat and logs remain.
- Frida 17.23.3 and frida-tools 14.11.0 now run from `kitt/re-frida:0.88.77`, separate from Wine/REA. Operator-only `kitt vm container toolbox --profile frida --workspace DIR`; `--allow-ptrace` explicitly grants SYS_PTRACE for container-local processes, never host PID/network access. Windows instrumentation remains inside Windows guest.
- `kitt/linux-re:full-0.88.77` now contains REA/Wine, not Ghidra/Frida. Standalone images can be built/pulled using `kitt vm container build|pull --image ghidra|frida`; they are produced and smoke-checked in separate GHCR publish jobs. No legacy tool fallback or old all-in-one image requirement. Existing workspace projects remain intact.
- `pyproject.toml` and `uv.lock` synchronized. Upstream tools remain on their previously pinned versions. Performance benefit is image/lifecycle isolation and per-tool resources; no unmeasured CPU speedup is claimed.


## 0.88.76 — Ghidra activity supervisor without mandatory timeouts

Replace mandatory Ghidra/host analysis timeouts with opt-in `--analysis-timeout-seconds` and `--host-timeout-seconds`. Use `Popen.wait` with bounded heartbeats, incremental 64 KiB log reads and optional Docker CPU/RAM/block I/O snapshots; silence is only a warning. Preserve operator-only sandbox, SHA256 verification, project resume, cancellation and exclusive project locking. Inspect actual Docker OOMKilled before explicitly removing the transient container. Require Ghidra analysis and save report in addition to process exit code for `GHIDRA_COMPLETED`; even this does not guarantee every analyzer succeeded. CPU/RAM/heap limits configurable without raising defaults; 2 GiB heap fits 4 GiB container. Add regression tests and document host verification requirement; synchronized Python and uv.lock versions, external pins unchanged.

## 0.88.75 — retomar projetos Ghidra salvos e identificar timeout parcial

Novo `kitt re lab analyze --path ARQUIVO --resume-job job-<id>` valida o projeto Ghidra existente, confirma o recibo de importação do mesmo arquivo (ou o SHA-256 registrado pelas novas execuções), mantém arquivos e logs originais e retoma por `-process` em vez de importar outra vez. Análise retomada com teto de 1200 segundos por arquivo e 1380 segundos no host; 600/780 permanecem para novas importações. O resultado `ANALYSIS_TIMED_OUT` agora informa corretamente quando o Ghidra salva a importação após esgotar o tempo de análise, em vez de declarar sucesso pelo exit code 0. Testes de validação do projeto, seleção do modo, retenção de logs e tempo limite; versões e uv.lock sincronizados.


## 0.88.74 — feedback durante análises Ghidra demoradas

Em `kitt re lab analyze`, exibir imediatamente o binário, a pasta de saída e o caminho do log no stderr. Um heartbeat periódico (15s) informa duração e tamanho do log enquanto a saída JSON permanece reservada à conclusão. Ao pressionar Ctrl+C, interromper exclusivamente o contêiner temporário da análise e preservar logs; timeout continua limitado a 780s. Testes focados cobrem heartbeat e cancelamento. Dependências e pins externos inalterados; versão e uv.lock sincronizados.


## 0.88.73 — on-demand current project workspace sharing with KITT RE

New operator-owned `kitt re lab analyze --path ...` uses the current project workspace by default and mounts it read-only into an ephemeral, networkless container from the SHA256-pinned managed RE toolbox image. A separate, private output bind mount writes Ghidra results directly into `reverse-engineering/decompiled/ghidra` of the current project. No file copying, persistent Linux container replacement, Docker socket/host credentials or automatic Windows guest mount. Strong limits, before/after streaming SHA256 and dry-run plan. Focused tests, uv.lock synchronized; external dependency pins unchanged.


## 0.88.72 — explicit Git subprocess sandbox diagnostics

Distinguish real host Git policy failures from attempts to commit via python3 -c/subprocess. The strong sandbox keeps .git read-only for wrappers; preserve that boundary, return a typed non-retriable GIT_METADATA_READ_ONLY_WRAPPED_PROCESS result and guide agents to governed git.action or direct Git argv, avoiding repeated ineffective probes. Focused tests; version + uv.lock updated, external pins unchanged.

## 0.88.71 — independent streaming verification of large workspace binaries

Fix host `workspace-read` validation errors for large non-source artifacts such as the 292 MB `2.1.308` ELF. Add a file-descriptor-safe workspace SHA256 streaming API (1 MiB blocks, hard cap 512 MiB, stable inode/size/mtime/ctime, strict symlink restrictions), and route oversized non-source artifacts to this independent read rather than the 8 MiB in-memory syntax parser. Keep all source syntax checks and normal workspace-read/write limits unchanged; return SHA256/size as verifiable evidence, not a claim of comparison with an external pinned manifest or of decompilation. Focused boundary tests and synchronized uv.lock. Existing dependency pins unchanged.


## 0.88.70 — host-targeted Python check works in RE workspaces

BuildDetector finds directly selected test_*.py files and sibling test_<module>.py alongside source, not only tests/test_*.py. Air-gapped guests without pytest use a bounded stdlib unittest discovery target instead of reporting python.targeted-tests UNAVAILABLE when a real test exists. Host policy, approval, sandbox and independent verification remain mandatory. Focused regression and uv.lock synchronized, pins unchanged.


## 0.88.69 — streaming inventory for large ELF binaries

The opt-in KITT RE MCP plugin adds `elf_inspect_stream` for read-only ELF metadata and incremental SHA256 hashing up to 512 MiB in constant RAM. Symlink-safe workspace traversal, bounded regular-file check, and concurrent size/mtime change detection. Plugin version 0.4.0. This is inventory, not decompilation; Ghidra analysis remains separately governed. No new dependencies or pin changes.


## 0.88.68 — distinguish task IDs from persisted goal IDs

Help autonomous RE workers avoid repeated invalid goal.inspect calls by redirecting missing local orchestrator task IDs (T01 etc.) to plan.inspect / plan.next without auto-converting IDs or granting capabilities. Targeted regression, synchronized uv.lock and unchanged dependency pins.


## 0.88.67 — bounded Git operations through host policy

Adds git.action for status, diff, stage and commit with strictly shaped arguments, bounded workspace paths and reject-on-path-scoped commit. Only direct argv through existing process policy, approval and strong sandbox; dry_run only previews commands. No network or shell. Focused security tests. uv.lock/version aligned, dependency pins unchanged.


## 0.88.66 — host-governed targeted test.run

The test.run runtime operation selects an existing deterministic BuildDetector test recipe from workspace-contained source paths and delegates it through the regular capability-gated, approved and strongly sandboxed run_command path. Supports read-only dry_run preview, 16 input paths maximum, timeout <= 240 seconds, network off and no custom argv. Focused tests; unchanged dependency pins and uv.lock synchronized.


## 0.88.65 — bounded host-governed MCP tool discovery

Expose mcp.discover to inspect already registered, trusted MCP tools, filtering by query/server with bounded results and scope-aware visibility. The operation requires the existing CAP_MCP_CALL capability and never connects servers, imports schemas, or executes a tool. mcp.call remains separately policy-checked. Focused regression; uv.lock synchronized; dependency pins unchanged.


## 0.88.64 — opt-in ruDevolution RE plan

Adds a restricted, read-only MCP tool for SHA-pinned, bounded JavaScript decompilation plans. No auto-download or execution. Operator-authorized, strongly sandboxed process.run required for the upstream Rust example. Plugin bumped 0.3.0, focused security regression; no new dependencies or dependency pin changes.


## 0.88.63 — normalize equivalent progress observations

Share small, deterministic progress-only argument normalization across the tool loop and completion guard. Equivalent simple search-term order and redundant local path prefixes no longer evade repeat detection; failed observations normalize transient timestamps. Preserve exact tool execution identities, permissions, and successful output fingerprints, and avoid reordering phrase/boolean queries. Targeted regression tests, no dependency changes.


## 0.88.62 — bounded configurable tool-loop recovery

Expose guarded 0..8 environment-configurable recovery thresholds for malformed calls, policy denials, output-limit errors, completion claims and Python calls. Defaults preserve previous behavior and hard execution budgets still apply. Reject invalid environment values rather than silently degrading. One targeted regression module, no new dependencies or privilege changes.


## 0.88.61 — category-aware goal retry scheduling

Honor the existing host-owned policy/environment classification in legacy scheduled goals: stop repeating policy-denied actions, back off environment failures, and use bounded jitter for retryable failures. Preserve lease fencing and retry accounting. One focused goal integration regression; no changes to dependency pins.


## 0.88.60 — preserve actionable host-tool diagnostics

Preserve first build/test failure context and terminal status in bounded log summaries. Add advisory structured host-error categories without changing existing policies, capabilities, or retry behavior. Three focused regression checks; no dependency pin changes.


## 0.88.59 — align adversarial review category prompt and validator

The adversarial reviewer now receives the exact ten required production review category identifiers and a valid schema-complete APPROVED example. Canonical labels with explicit descriptive suffixes (e.g., "Correctness: ...") normalize to their verified category identifiers without inferring unchecked coverage. A partial review still fails closed; deterministic host checks, evidence anchors, independent validation and review gates are unchanged. Regression covers compliant review, annotated categories and the actual four-category mismatch in the 2026-10-22 logs. CI runs the focused regression before long suites. Update uv.lock, preserve dependency pins.


## 0.88.58 — FreeLLMAPI rate-limit cooldown and safe retry

Honor FreeLLMAPI HTTP 429 reset hints from Retry-After, JSON retryAtMs or routing exhaustion ETA; retry one time after cooldown, with cancellation support and a daemon-process-wide provider+credential gate shared by concurrent agent clients. Never automatically retry mixed HTTP 502 upstream failures or requests with already emitted output. Add contract tests for quota hints, bounded retries, mixed 502 and cancellation. Keep version lock synchronized and all dependency pins unchanged.


## 0.88.57 — honor no-op directory creation in post-edit verification

The create_directory handler now reports whether the target already existed. Idempotent mkdir returns a successful no-change receipt and does not mark pre-existing files as changed, trigger post-edit verification, invalidate code memory, or record a deliverable. This fixes the repeated false "Directory validation refuses unsafe links" failure for existing mbs-wine/.git checkouts without loosening protected metadata or directory-verification security. Fresh directory creation continues to take a recovery snapshot and run verification. Regression exercises the real handler and engineering wrapper. Lock version updated, dependency pins unchanged.


## 0.88.56 — skip no-op directory snapshots in existing Git checkouts

When repo.create_directory targets an existing real directory, the operation is idempotent and cannot change its contents. The Agent no longer recursively snapshots that directory before running mkdir. This avoids traversing protected .git metadata (seen in the 2026-10-22 traceback) while keeping all file mutations, directory creation at missing paths, Git safety controls and recovery snapshots intact. An integration regression covers both existing .git checkout and fresh directory creation; dependency pins are unchanged.


## 0.88.55 — guide Git metadata inspection and diagnose failing turns

Git initialization and status already work through argv-only process.run. Direct workspace file tools must still reject .git for safety; their denial now points to Git commands, and both model-facing agent formats steer Git inspection away from protected internals. Unexpected turn failures now preserve a traceback in the agent log, while the user receives a meaningful error even for empty exceptions. No dependency pins were changed; uv.lock is synchronized with the version bump.

## 0.88.54 — restore Git metadata writes and fail-closed journal diagnostics

Direct Git operations invoked with the literal `git` executable through policy-controlled `process.run` may update the workspace root `.git` when the execution sandbox has a writable workspace profile. Other executables and read-only profiles retain the `.git` read-only mount, and `.kitt` remains protected. A workspace-contained `git -C DIR` now handles four-argument reads and policy-gated mutations without bypassing the existing hard denials for push/reset/clean or network authority.

If the durable tool execution ledger cannot reserve an execution receipt because of filesystem permissions or SQLite locking, the turn now emits a bounded BLOCKED result with the underlying error and does **not** execute the tool. This replaces the unhandled `PermissionError` and prevents unjournaled mutation. Regressions cover Git sandbox mount policy, `-C` command classification and unwritable journal handling. No dependency pins change; Python metadata and `uv.lock` are synchronized.

## 0.88.53 — FreeLLMAPI model discovery

Register FreeLLMAPI in the shared provider catalog and model picker. Authenticate gateway model discovery, retain provider identity, and use the configured workspace endpoint in `kitt models freellmapi --root PATH`. Alternate endpoints still require explicit user-local trust.

## 0.88.52 — FreeLLMAPI autonomous planning and native tools

Plain and headless CLI frontends now display host-owned planning/checking/revision progress while waiting for the provider, including through the daemon, instead of appearing idle until the full plan arrives.

The Wine workflow exposed a read-only planning response being misinterpreted as a file replacement because its JSON mentioned a Markdown path and contained `<` characters. Plan/ask finalization now returns response data without parsing or inferring edits; execution-mode write checks remain enforced. OpenAI-compatible streaming now sends the existing host tool schemas, validates bounded native tool calls, restores native tool-result messages, records usage, and surfaces redacted SSE errors. A provider returning multiple native calls despite serial mode receives at most two corrective rounds within the same turn, preserving earlier host receipts instead of restarting the contract item. Tools remain subject to the existing host policy and approval path.

FreeLLMAPI reuses saved authentication, disables unsupported native JSON mode, reserves 8192 output tokens within a 32768-token context window, and relies on gateway failover without adding outer retries or sleeping through gateway exhaustion. Timed-out requests are not replayed. Native API Goal turns now use the existing GoalStepVerifier when no nested TaskPlan exists, just like browser-backed turns: the legacy mutation guard no longer forces a duplicate write merely to verify an already-initialized project. Contract checks, independent validation and ordinary user-turn mutation safeguards remain active. The cancellable HTTP transport preserves the configured response-header timeout while retaining the ten-second connection bound. Regression tests cover read-only file preservation, native tool round trips, request allowlists, malformed arguments, usage, errors and gateway retry limits. Metadata, source fallback and uv lock are synchronized. Free provider availability, model quality and Wine/Windows Oracle validation remain separate runtime requirements.

## 0.88.51 — avoid Gemini empty-action retry storms

Logs from 22 October confirmed five `agent_contract_empty_action` errors after valid workspace inspection, with WebChat DOM fallback taking up to about 110 seconds for one answer. The reverse proxy correctly rejected empty `{}` without inventing a tool action, but the autonomous Goal scheduler rapidly resubmitted the unchanged task. After the current agent-loop receives a verified no-action error, a configured *trusted alternative provider* can be attempted once through the existing bounded FailoverLLMClient (never after partial output or with a provider outside the allowlist). If no provider succeeds, the contract item is marked BLOCKED_ENV for explicit continuation instead of consuming consecutive automatic retry turns. The browser session is not silently reset and no tool call is automatically replayed.

The compact agent guidance now clarifies workspace-root-relative repository paths: after inspecting `mbs-wine/`, read `mbs-wine/.gitignore` rather than `.gitignore` from the parent root, and prefer structured `repo.list` for discovery. Adds regressions for one-shot alternate fallback, partial-output safety and blocked goal retries. Bumps package metadata, `uv.lock` and release version; no new privileges or dependency changes.

## 0.88.50 — avoid approval loops for shell-wrapped Git checkout

On 22 October the model bundled Wine checkout setup into a network-enabled `bash -c` command. The narrow autonomous public Git delegation only accepts exact argv-only `git clone` calls, so the agent asked for generic network approval and waited 84 seconds. The SafeRuntime now returns an actionable tool error for shell-wrapped Git clone/submodule requests lacking network authority; the agent should create directories and initialize Git separately, and issue one direct public Git checkout per call. Unlike automatic shell execution, this preserves the original network approval boundary. Arbitrary network shell commands still need authorization, and sandbox protection remains mandatory. Tool schema instructions and regression tests cover both paths. Python metadata, source version fallback and `uv.lock` are synchronized.

## 0.88.49 — recover protected Git metadata planning scopes

Automatic contract planning no longer aborts immediately when a model includes `.git` as a proposed validation path: the planner turns the filesystem policy rejection into a bounded, actionable structural validation error and requests a corrected plan. Git metadata remains inaccessible through workspace file tools; the model must scope real worktree files or use an empty path list for Git-only operations. A regression covers an invalid first plan followed by a valid revision, and confirms that direct `.git` reads remain denied. No command permissions or network privileges were changed. Python metadata and uv lock stay synchronized.

## 0.88.48 — read-only Git inspection with workspace-relative -C

A Wine MBS autonomous contract stalled on 21 October after issuing `git -C mbs-wine status --short --branch`. The PolicyEngine hard-denied the `-C` flag before it could inspect the existing Git repository, causing repeated edits to README and BLOCKED_ENV even though no approval was requested. Git inspection now accepts an existing, workspace-contained, relative `-C` directory **only** for read-only `status`, `diff`, `log` and read-only `branch` commands. All original per-subcommand flag checks remain in force; path traversal, symlinks out of workspace, internal .git directory access and mutations (push/reset/clean) remain DENY, supervised policy remains ASK. The fix does not grant network access or change Git clone timeout. Regression reproduces the exact Wine MBS command and blocked variants.

The same run also showed `session_limit_exceeded` for a child under Reverse Proxy resource pressure (about 1.10 GB measured versus a 768 MB configured threshold), and unnecessary repeated README rewrites. Those are separate limitations, not resolved by the Git policy adjustment.

## 0.88.47 — autonomous Wine Git checkout without recurring approval

Fixes a 2026-10-21 KITT run where workspace autonomy was already `autonomous`, but `process.run` with `network=true` for a public Git submodule download still demanded manual approval. A narrowly scoped delegation now permits unscoped root-user model turns to fetch public HTTPS Git repositories from GitHub or Wine's GitLab into workspace-relative directories, only with a strong network-enabled sandbox. Both SafeRuntime and nested ToolRegistry use the same rule.

Other network commands, private hosts, unsafe destinations, child principals, supervised mode and unavailable strong sandboxes retain their existing explicit approval or denial. This is not a general network capability. The Git fetch can still time out independently; the logs also report two missing Ollama model profiles, which this release does not install. Adds regression coverage and updates Python package metadata, lock and release version.

## 0.88.46 — prevent repository-index SQLite writer contention from crashing startup

When another KITT daemon/process holds a persistent SQLite writer transaction, the new CLI no longer fails during the index schema/metadata initialization with `sqlite3.OperationalError: database is locked`. A bounded connection busy timeout (2s) now controls index initialization; the schema script no longer silently overrides it. On SQLite BUSY/LOCKED **only**, the contending process closes its incomplete disk connection (without forcing WAL checkpoint/truncate), starts a process-local in-memory index, and continues normal indexing/search. This avoids deleting or modifying the daemon-owned index and exposes `index_storage=memory_fallback` / `index_fallback_reason` through metadata. Permanent SQLite errors are still surfaced; the next KITT process will use the disk index normally once contention disappears. Regression locks the disk index with a separate SQLite writer and verifies fallback isolation, search, writer transaction continuity and later persistent reopen. This does not force-stop another KITT process or override system isolation.

## 0.88.45 — support non-mutating Begin Patch navigation hunks

Fixes a concrete 21 October Wine-MBS regression: `patch.apply` rejected `*** Begin Patch` edits containing a standalone context-only `@@` section (for example `def main():`) before changed hunks. The parser now skips navigation-only sections but still rejects entire no-op patches, unsupported Add/Delete/Move and unanchored insertions. Actual modifications retain unique SEARCH matching, protected workspace policy, transactional rollback and generated-content validation. Regression checks a realistic three-change `mbs-wine/bootstrap.py` patch applied through the existing DiffApplier. This release does not override command permissions or replay unattributed ChatGPT Web responses. Package metadata, source fallback and uv.lock updated.

## 0.88.44 — optional agent integrations without duplicated runtimes

Add a read-only workspace advisor for stack-specific hooks, skills and existing MCP plugins; an explicitly authorized, workspace-bounded Strix CLI adapter; a FreeLLMAPI profile using KITT's existing OpenAI-compatible protocol, endpoint trust and environment-only credential references; and a native memoryd doctor. Default models and memory authority remain unchanged unless the user explicitly opts in. Regression tests cover target authorization, provider registration, privacy and CLI contracts. No third-party implementation was copied or bundled. Python metadata, source fallback and uv lock are synchronized at 0.88.44.


## 0.88.43 — safe Begin Patch Update File compatibility

`patch.apply` now accepts content-anchored `*** Begin Patch` / `*** Update File` / `@@` / `*** End Patch` changes without rerouting them to SEARCH/REPLACE. The parser converts updates into the existing guarded EditBlock mutation path (workspace limits, ambiguous-match rejection, approvals and rollback); Add/Delete/Move directives and unanchored insertions remain unsupported and fail closed. Regression covers the exact Wine-MBS win32u reconciliation patch from 21 October 2026, multi-file updates and path-policy enforcement. Error guidance documents the supported dialect. Python metadata, uv lock and source fallback are synchronized at 0.88.43. The distinct `ui_capture_unverified` provider problem still requires explicit safe continuation and was not changed here.

## 0.88.42 — bounded automatic continuation of budgeted Goals

A host-owned `Execution budget checkpoint:` now returns a dedicated contract outcome instead of consuming a failed-item attempt. The scheduler requeues the pending item with its prior evidence, while preserving the global Goal limits for turns, elapsed time, tokens and cost. An invalid CHECKPOINT reason is rejected at the contract-store boundary. Regression tests cover a contract item with one available attempt continuing across the tool-budget boundary and prove that global turns are still charged. Project metadata, source fallback version and `uv.lock` are synchronized.

## 0.88.41 — safer patch targets and actionable batch diagnostics

SEARCH/REPLACE patches keep their explicit target across consecutive blocks, and numeric version strings such as `11.20` are not guessed as workspace files. Failed `repo.batch_read` calls now report bounded per-ID errors and preserve successful sibling results; the tool catalog explains context handles and retry scope. The default bounded per-turn host-tool budget increases from 24 to 64 while model/time/sandbox authorization limits stay intact. Regression checks cover patch target isolation and partial batch failures. Project metadata and `uv.lock` are synchronized at 0.88.41.

## 0.88.40 — persistent read-graph evidence and scoped Goal recall

Durable Goals now preserve bounded search and read-only `flow.execute`/`program.execute` receipts between attempts; repository-wide observations are invalidated by any successful mutation, avoiding stale context after patches. The memory recall query uses the original task objective instead of Goal/verification scaffolding, improving retrieval relevance without treating model-generated summaries as authority. Python project metadata, source-tree version fallback and `uv.lock` are synchronized at 0.88.40; runtime and CI contracts remain compatible with Agent Contract v5 and Reverse Proxy 5.4.17. Source/regression checks and ecosystem pinned integration must pass before calling this release validated.


## 0.88.39 — actionable unavailable native-build diagnostics

When no configured Makefile or native verification executor is available, the existing VerificationReport now includes the bounded host `command_output` in `failure_message` rather than returning the unhelpful generic `verification failed`. The durable Goal verifier surfaces the actual `UNAVAILABLE` reason, so missing Wine/C build configuration is not confused with successful compilation. Regressions assert the user-visible failure reason. Includes all 0.88.38 host-flow, memory/context, parallel planning and safe WebChat continuation improvements.


## 0.88.38 — safe Goal handoff for unattributed WebChat capture

Aligns the durable GoalScheduler with the Reverse Proxy 5.4.17's `ui_capture_unverified` contract. An already-submitted WebChat turn that ended without provable current-turn response is terminal BLOCKED/ENVIRONMENT for that attempt, requiring explicit continuation; it is never automatically reissued as a normal retry. Includes all Agent CLI 0.88.37 read-only graph guidance, context, independent planning and C/Wine verification enhancements. Focused regression covers the new typed recovery result without treating unrelated provider errors as capture ambiguity.


## 0.88.37 — efficient tool flow, native-source verification and Goal handoff

Expose existing read-only `flow.execute`, `program.execute`, structured symbol navigation, context handles and memory retrieval in the Agent's model-facing operation guidance; recurrent discovery checkpoints recommend combining independent host reads without granting write access through the read-only graph. Keep durable Goal evidence intact after successful read-only flow/program operations, and trace empty memory recall distinctly from unavailable memory. Contract planning now favors independent nonoverlapping paths and actual host checks rather than artificial dependency chains. Native C/C++ edits require a configured Makefile-driven `c.build` step before the host treats the change as verified; missing builds are UNAVAILABLE rather than falsely passing structural checks. Added targeted regressions and synchronized tool contracts.


## 0.88.36 — fix Bubblewrap resolver symlink mount on Ubuntu

When creating the process sandbox, the host mounts `/run` on a private tmpfs. Rebinding `/etc/resolv.conf` to its original path fails on systemd-resolved hosts where it is a symlink into `/run`. The command planner now skips resolver rebinds for network-isolated `workspace-write` and for regular `/etc` files already visible from the read-only root; network-enabled profiles restore only the canonical resolver target file and its minimal directory chain under the private `/run`, never mounting over a symlink destination. Two focused regressions cover canonical target selection and isolated/network-enabled behavior. No relaxation of AppArmor, sandbox policy, tool authority or network restrictions.


## 0.88.35 — probe all Bubblewrap candidates and explain userns failures

The host execution sandbox now enumerates /usr/local/bin/bwrap, /usr/bin/bwrap, /bin/bwrap and all PATH candidates, rejects binaries inside the workspace, deduplicates symlinks and skips versions below 0.12.0. Every suitable candidate must actually pass the namespace isolation probe; a failing older, broken or AppArmor-restricted binary no longer masks another functional installation. Failure diagnostics include the attempted path and bounded stderr (with an AppArmor userns/uid_map hint when relevant) in strong-sandbox errors and runtime capabilities. Positive and negative detection results are refreshed after 60 seconds for installation and policy changes. Focused regressions cover fallback selection, uid_map denial, workspace isolation, and negative-cache refresh. The strong sandbox requirement and host authorization remain unchanged.


## 0.88.34 — include runtime-written source and reports in Goal verification

Goal review snapshots now recognize `kitt_runtime.repo.write_file` as a source mutation and include the changed path among host-verifiable review targets. Previously this canonical API could write a file without contributing it to the outer Goal verification path set. Authored Markdown reports are now reviewable alongside source files. Adds focused regressions for Python and Markdown paths; no host authority or sandbox changes. Includes all bounded Goal host-evidence reuse from 0.88.33.


## 0.88.33 — durable host evidence for long-running goal retries

A new bounded Goal evidence journal persists successful read/list/search receipts in the existing session-scoped RuntimeStateStore and rehydrates them into the next turn of the same contract item. The journal handles successful children of partially failed repo.batch_read calls, preserves previously captured text when an unchanged-read dedup placeholder is returned, and invalidates entries for successful file mutations or unscoped process actions. Host evidence is explicitly marked untrusted and never authorizes actions or satisfies acceptance gates. Two focused regressions cover cross-turn reuse, stale evidence invalidation and partial batch recovery. This addresses repeated repository inspection without relying on optional semantic memory recall.


## 0.88.32 — no-progress detection for batches and terminal blocker classification

A successful `repo.batch_read` is now treated as an observation rather than as task progress, closing a loop escape where repeating unchanged batched read results reset the stall detector. A Goal step now classifies repeated host policy denials and unavailable sandbox operations with their original POLICY/ENVIRONMENT categories instead of generic blockers. These paths cannot be mistaken for finished work or resolved by identical retries. Two small regressions exercise both cases. This release preserves the host's security boundary, existing proxy contract and all 0.88.31 behavior.


## 0.88.31 — host sandbox preflight in tool definitions and runtime capabilities

Report the availability of the actual strong workspace process sandbox before the first attempted process.run operation. The Agent model-facing tool description and the host runtime.capabilities result expose a bounded diagnostic (`functional bubblewrap>=0.12`) while leaving authorized repo mutations and VM executor tools available. No unsandboxed fallback, capability escalation or weaker Bubblewrap version is introduced. Adds a regression for unavailable strong sandbox and preserves the same trusted host operation catalog.


## 0.88.30 — durable-Goal recovery, context and routing hardening

Completes another tranche of the prioritized reliability backlog on the existing KITT architecture. Long-running work now uses a resumable budget checkpoint without false completion; read batches support bounded capability-scoped searches. All explicit saved ASK/DENY autonomy choices remain authoritative, while partial new policy dictionaries match autonomous workspace defaults. Preserve polyglot AGENTS.md guidance, enforce the instruction body budget and revalidate retained child-model egress and credential references on the worker. Categorize strong-sandbox provisioning failures as environment blockers and protect against duplicate WebChat replays on ambiguous submission timeouts. Add a strictly optional localhost-only System One Choice/Score/Noul shadow evaluator with accuracy, calibration and latency metrics. The decision evaluator never controls approvals, tool execution or completion status. Strict host sandbox requirements remain unchanged. Targeted regressions and release-critical CI verify the amended Python surfaces.


## 0.88.29 — consistent workspace autonomy and polyglot instructions

Partial autonomy settings now default to the same autonomous level as newly created workspaces. An explicitly persisted supervised or read-only choice still wins; the host policy and sandbox remain authoritative. ContextResolver no longer silently discards legitimate AGENTS.md content from Python/Node/Bun/TypeScript polyglot repositories based on a filename heuristic. Both behaviors have focused regressions.


## 0.88.28 — resumable execution budgets and descriptive batch IDs

Exhausting an Agent turn's model/tool wallet now emits a typed host checkpoint instead of an unqualified terminal failure. The existing GoalScheduler classifies this as a resumable INCOMPLETE item under its finite retry/turn policy; successful host actions remain durable, and rejected budget calls are not misreported as executed. Descriptive unique `repo.batch_read` IDs up to 64 characters are supported with actionable diagnostics. The automatic contract planner now scopes very large target inventories to bounded initial implementation batches with explicit remaining PENDING work. The host's strict sandbox and independent clean-room validation requirements are unchanged.


## 0.88.27 — detect alternating no-progress host outcomes

The agent-loop now detects unchanged two-step failure/observation cycles such as a failing build followed by repeatedly reading the same file. The same bounded detector handles identical failed operations and identical read results; a successful non-observational host action resets the streak. This closes a progress-monitoring gap without adding model calls, overriding host authorization, or changing the Reverse Proxy protocol. Adds a targeted regression for the alternating case.


## 0.88.26 — do not replay ambiguous WebChat timeouts

The KITT Reverse Proxy client now treats socket/URL read timeouts and HTTP 408/504 without explicit recoverable metadata as ambiguous WebChat submissions. It raises a non-retryable protocol error instead of allowing the generic LLM retry policy or model failover to resend an already accepted request. Explicitly recoverable structured proxy errors retain their existing handling. The native WebChat tool contract and browser session ownership remain unchanged. Focused compatibility regressions cover all three timeout paths.


## 0.88.25 — stop repeating unavailable host sandbox operations

A failed host process action caused by missing Bubblewrap sandbox support remains visible as an execution failure. The agent is told that unchanged retries cannot succeed and the same action is blocked on its next attempt within the turn. Other authorized host tools and validation routes remain available. No sandbox policy, approval authority or reverse-proxy wire contract is weakened. A focused regression covers sandbox failures without classifying ordinary process test failures as environmental blockers.


## 0.88.24 — actionable plan contracts and host sandbox diagnostics

Explain the exact `arguments.proposal` wire contract when models mistakenly submit `tasks` at the top level, and teach both model-facing tool envelopes the validated `schema_version`, `objective`, `local_id`, `title`, `role`, `depends_on`, `paths` and `check_ids` format. `plan.verify` reports the missing plan prerequisite explicitly. When automatic host execution cannot acquire the required Bubblewrap OS sandbox, explain the environment repair instead of suggesting another autonomy approval. Preserve strict isolation, workspace scope, capabilities and execution budgets. Regression coverage in `tests/test_plan_runtime_diagnostics.py`.


## 0.88.23 — workspace-wide allow-all from approval menu

The approval modal now distinguishes [A] Allow-all (persist autonomous mode for the current workspace) from [w] remembering a single tool. The pending grant executes under its original authority snapshot; later actions use the persisted autonomous mode. A failed autonomy RPC cannot reopen an already granted request. The TUI rejects daemon autonomy errors rather than silently diverging from the daemon. Host path and sandbox capability boundaries remain enforced.

## 0.88.22 — autonomous workspace/VM default, restricted by host boundaries

New workspaces default to autonomous/allow-all for ordinary workspace and authenticated guest operations; previous operator choices are preserved. Expand the automatic per-turn budget from 40/80 to 1000/10000 for long tasks. Require a strong OS sandbox for automatic *host* process execution so autonomy never silently becomes an unsandboxed host escape. ASK/DENY remain accessible in Autonomy; control-plane, network, secrets, out-of-workspace and VM identity gates are unchanged. [Notes](docs/RELEASE_0.88.22.md).


## 0.88.21 — daemon-authoritative autonomy on startup

The CLI now reads the daemon's effective autonomy on attach instead of overwriting it with the local TUI snapshot. Keeps local display synchronized without granting authority on reconnect, and preserves explicit user-initiated `/autonomy allow-all` updates. Requires Assistant Runtime with `runtime.get_autonomy` for synchronized status; older daemons are not silently escalated. [Notes](docs/RELEASE_0.88.21.md).


## 0.88.20 — approval hotkeys and tool-loop recovery

Make permission and autonomy shortcuts eager and add A/S/D shortcuts to the autonomy modal; clarify displayed controls. Prevent artifact reads from generating further artifacts, retain a bounded preview in oversized host feedback, and refuse repeating an identical policy-denied action within one turn. Record model-round count in tool-loop telemetry. [Notes](docs/RELEASE_0.88.20.md).


## 0.88.19 — explicit-only Agent loop tool dispatch

Agent-loop execution accepts only canonical JSON actions or complete `<kitt-tool>` and `<tool_call>` envelopes; never derive host tools from prose, code examples, or incomplete frames. The existing `repo.batch_read` handles independent parallel reads with bounded receipts; writes remain sequential. Bump project and lock, add focused tests. [Notes](docs/RELEASE_0.88.19.md).


## 0.88.18 — smaller host-tool feedback and safer empty-completion handling

Scout refactoring extracts deterministic tool evidence, discovery, and browser-trace helpers from the agent tool loop without changing their public import surface. Repeated host-result instructions are sent only on the initial tool or when exploration stalls; an empty JSON/blank agent-loop answer cannot close a turn as success. No new provider calls or additional authorization capabilities. [Notas](docs/RELEASE_0.88.18.md).


## 0.88.17 — execution discovery bootstrap and RE host source staging

Adds one host-owned capability snapshot for reverse-proxy agent-loop prompts, a single JSON action format rather than conflicting XML tool examples, contract-aware context retrieval, discovery metrics and a bounded repeated-discovery checkpoint. Adds operator-only, non-destructive Wine-Staging host clone into the networkless RE toolbox's shared workspace, without exposing SSH credentials or guest internet access. Tests, uv.lock and runbooks updated. [Notas](docs/RELEASE_0.88.17.md).


## 0.88.16 — isolated Wine pilot private HOME

Fix the Windows-versus-Wine differential pilot: the non-root Wine process previously received root-owned `HOME=/tmp` and exited 1 before executing the known PE. Use a mode 0700 private HOME beneath the disposable tmpfs and a fixed, constant shell bootstrap to create it, preserving non-root UID, immutable image, pinned PE, read-only root, no network and no privileges. Reject invalid Windows reference receipts before Wine execution; report only the bounded Wine exit code, not raw probe stderr. Adds regression tests, runbook and synchronized uv.lock / protocol pin. [Notas](docs/RELEASE_0.88.16.md).


## 0.88.15 — preserve SSH diagnostics on early TCP rejection

A closed SSH transport may raise BrokenPipeError during stdin write/close before the client exit status is read. Handle it so the sanitized SSH_UNAVAILABLE classification remains actionable; add a deterministic fake-process regression. No dependency or security-policy changes. [Notas](docs/RELEASE_0.88.15.md).


## 0.88.14 — diagnósticos SSH no Windows Oracle

Classifica SSH_UNAVAILABLE, SSH_TIMEOUT, SSH_DISCONNECTED, SSH_AUTH_FAILED e SSH_HOST_KEY_REJECTED sem expor stderr privado. Apenas o handshake read-only probe.info pode repetir uma vez falha de transporte transitória; execução de probes jamais é repetida automaticamente. Bootstrap registra PROVISIONING/FAILED/READY no guest. Mantém pins do Protocol 0.14.2; sincroniza uv.lock e metadados. [Notas](docs/RELEASE_0.88.14.md).


## 0.88.13 — Linux RE guest: Wine, Wine64 and WineHQ Staging

New image 0.86.11 adds independent Debian Wine and signed WineHQ Staging amd64/i386 with runtime checks. Adds `kitt re lab upgrade-linux --approve` with isolated preflight, workspace preservation and rollback. [Instruções](docs/RE_WINE_STAGING.md) · [Notas](docs/RELEASE_0.88.13.md).

## 0.88.12 — MCP de guests RE com acesso somente leitura

Servidor MCP stdio `kitt.re_guest_mcp` com status Linux/Windows e verificação do Windows Oracle. Bloqueia shell/execução arbitrária, exige manifesto SHA-256 e remove caminhos/identificadores privados; ativação explícita pelo operador. [Notas](docs/RELEASE_0.88.12.md).

## 0.88.11 — Protocol 0.14.2 pin and canonical CHANGELOG

Aligns immutable Protocol commit, Agent metadata and uv.lock. No behavioral changes to the offline Clean-Room pipeline; E2E Windows Oracle remains unverified. [Notas](docs/RELEASE_0.88.11.md).

Histórico integral dos anúncios de release anteriormente mantidos no README. Detalhes por versão permanecem em `docs/RELEASE_*.md`.

## Agent CLI 0.88.10 — offline Clean-Room RE pipeline

Adds locally built GGUF implementer images, signed operator-only `kitt re cleanroom run` orchestration, independent offline tests and chained audit receipts. No new-code Windows Oracle conformance claim. [Release notes](docs/RELEASE_0.88.10.md) and [workflow](docs/CLEANROOM_RE.md).

## Agent CLI 0.88.9 — controlled clean-room RE contracts

Validated black-box API contract sealing and detached Ed25519 independent-review authorization before optional networkless OCI implementer execution use an immutable image and a separately reviewed Wine workspace. Run-offline never claims independent winetest or Windows Oracle success. See [Clean-Room RE workflow](docs/CLEANROOM_RE.md) and [release 0.88.9](docs/RELEASE_0.88.9.md).

## Agent CLI 0.88.6 — disposable Dockur Windows Oracle bootstrapping

Windows 11 RE lab setup can now generate a deterministic Win32 probe, private Ed25519 SSH keys and a Dockur /oem package; Windows installs OpenSSH and a restricted PowerShell probe RPC automatically on first boot. Resetting any existing Windows guest requires explicit --recreate --yes --accept-license. The Oracle is only ready after a validated SSH handshake, never merely because Docker is running.

[Bootstrap and runbook](docs/WINDOWS_ORACLE_DOCKUR.md). [Release notes](docs/RELEASE_0.88.6.md).

## Agent CLI 0.88.5 — managed Docker RE lab discovery and runtime auto-selection

Detect an available Docker/Podman engine (Docker preferred) without silently overriding explicit selection. Inspect installer-owned `kitt-re-full` and `kitt-re-win11` with image identity, private bind-mount and isolation validation; allow operator-only start/stop and a Linux toolbox shell, without creating duplicate QEMU guests or granting Windows oracle/SSH authority. Existing QEMU profiles and unattended approval boundaries are preserved.

[Release details](docs/RELEASE_0.88.5.md).

## Agent CLI 0.88.4 — durable approvals and collision-safe parallel work

Approvals are persisted before appearing in the UI, cannot reopen after a decision, and resume the exact concrete action with its original mutation preconditions. Cancelling a turn retires its pending approval. Parallel Goal batches include the scheduled task and prevent overlapping directory/file work. Keeps the 0.88.3 allow-all and Goal fencing contracts, with focused regressions and no new dependencies.

[Release details](docs/RELEASE_0.88.4.md).

## Agent CLI 0.88.3 — honor allow-all and resume concrete approved actions

The user's autonomous process permission now also applies to inline Python/Node commands. `evaluate_argv` still classifies those payloads as opaque; supervised/balanced modes require approval, read-only mode and hard denials remain enforced, and network/control-plane capability gates are unchanged.

[Release details](docs/RELEASE_0.88.3.md).

## Agent CLI 0.88.2 — remembered approvals resume without a loop

Saving “always allow” for a session or workspace no longer invalidates the waiting action's authority snapshot. Only explicit, action-bound approval resumes accept appended allow rules, with the entire original rule prefix preserved. Revocations, deny additions, policy/autonomy/sandbox changes, principal changes, grant mismatches and nonce replays still fail closed. Other snapshot consumers remain strict.

[Release details](docs/RELEASE_0.88.2.md).

## Agent CLI 0.88.1 — bounded planning with local validation

A valid automatic Goal plan now needs one model generation, followed by local validation of the existing size, ID, dependency, path, criterion and registered-check constraints. One structural correction is allowed when that validation fails; a second invalid result stops planning. Transport failures are not automatically replayed.

[Release details](docs/RELEASE_0.88.1.md).

## Agent CLI 0.88.0 — JSON model responses

Planning, advisory architecture, contract review, independent validation, senior review, completion reports and host-tool recovery now request ordinary JSON. Structured results use the strict shared JSON decoder; textual host actions dispatch from the canonical JSON action object. Duplicate keys, non-finite values and competing objects fail closed.

[Release details](docs/RELEASE_0.88.0.md).

## Agent CLI 0.87.29 — optional local multilingual semantic engine

PT-BR, English and Spanish explicit task commands are resolved locally without generating an auxiliary LLM classification request. An optional Ollama multilingual E5 Q8 classifier abstains on uncertain input; the original user request is always authoritative and remains in the execution prompt. The engine is **disabled by default** until independently calibrated. Use `/semantic doctor`, `/semantic status`, and the [Code-RAG guide](docs/CODE_RAG.md). Direct installers support explicit `--prepare-semantic-model` (Linux) or `-PrepareSemanticModel` (Windows) and never pull weights implicitly. [Release notes](docs/RELEASE_0.87.29.md).

## Agent CLI 0.87.27 — live planning and reviewer progress

Planning now shows **the actual phase** (PLAN/REVIEW/REVISION, attempt 1/3), a continually updating stage timer, an explicit *waiting for LLM* label, and scoped entries in the transcript. The Agent dashboard replaces the misleading fixed progress percentage with **AGUARDO** during provider waits. The phase closes cleanly on success, cancellation, or errors. [Release notes](docs/RELEASE_0.87.27.md).

## Agent CLI 0.87.26 — non-mutating final contract validation

Preserves model-authored FINAL acceptance criteria, checks, paths and dependencies during host contract validation, while retaining aggregate 64-path/64-criterion/12-check limits. The independent pre-mutation reviewer sees the exact validated proposal, not an expanded 49-criterion final step. This prevents systematic revision loops for large mission plans with optional/environment-gated stages; task checks continue running at their own steps. See [release details](docs/RELEASE_0.87.26.md).

## Agent CLI 0.87.25 — configurable LLM routing per specialist

Specialist workers can now select distinct existing TaskRouter profiles (direct API, local Ollama or separate KITT reverse-proxy instances), using automatic, preferred or fixed routing with privacy-aware fallbacks. Settings are editable via `kitt agents model` and `/agent model`; models are bound to the child session and reused after approval/resume. See [routing release notes](docs/RELEASE_0.87.25.md).

## Agent CLI 0.87.23 — pluggable specialist agents

Adds a trusted, declarative AgentRegistry with built-in Code, Creative, Investigator, Critic and Tester profiles, per-agent role/tool/budget/time contracts, and deterministic task-to-specialist selection. Workspace/global plugins can register agents via [agents.<id>] TOML plus a hashed persona Markdown; plugin content requires explicit per-workspace trust. Agents use existing ChildAgentManager and scheduler/worktree security boundaries, with no second planner or privilege elevation. CLI wizard: `kitt agents create`; TUI: `/agent` menu, `/agent run`, `/agent team`, `/agent msg`, `/agent inbox`. Family messages are durable and delivered at the next child turn boundary, not as live interrupts. See [release details](docs/RELEASE_0.87.23.md).

## Agent CLI 0.87.22 — selective indexed retrieval and graph changes

Repository context now prefers a complete FTS5 index for regular lexical requests; native Rust lexical search remains the fallback and the primary path for diagnostic and exact-path requests. Targeted graph edits refresh only impacted source adjacencies instead of rebuilding every edge. [Release notes](docs/RELEASE_0.87.22.md).

## Agent CLI 0.87.21 — responsive repository indexing

Full reconciliation now reads and parses source files outside the shared SQLite lock. File updates use short transactions, re-check metadata before committing parsed snapshots, and avoid deleting paths changed by targeted updates after scan start. [Release notes](docs/RELEASE_0.87.21.md).

## Agent CLI 0.87.20 — revision-safe embeddings and durable-source backfill

Asynchronous embedding writes carry the original Memory content hash. A bounded workspace backfill uses Memory's durable missing-vector queue when semantic search succeeds; unfinished items survive process restarts. Automatic backfill excludes secret, ephemeral and conversation memories. Full graph rebuild batches SQL edges. Requires Memory 0.10.2. [Release details](docs/RELEASE_0.87.20.md).

## Agent CLI 0.87.19 — opt-in local vector memory

The existing Ollama embedding provider now supports opt-in hybrid memory retrieval via kitt-memoryd 0.10.0. Set `KITT_MEMORY_EMBED_MODEL` to an installed model; `KITT_MEMORY_EMBED_URL` defaults to `http://127.0.0.1:11434` and accepts loopback HTTP only. A single lazy worker and bounded queue index successful remembers; search uses a short provider deadline and falls back to FTS5. No remote egress or automatic model downloads. See [release details](docs/RELEASE_0.87.19.md).

## Agent CLI 0.87.18 — incremental index performance

FTS5 rows are deleted by indexed rowid, avoiding full scans of UNINDEXED file_id. SQLite indexes now cover qualified symbols and per-file chunks. Full index refresh avoids reading unchanged files and redundant consistency scans on each edit; consistency is still verified at startup. See [release notes](docs/RELEASE_0.87.18.md).

## Agent CLI 0.87.17 — targeted Goal review correction

A `REVISE` reviewer verdict now retains the prior host-validated
plan as an editing baseline and sends only its unresolved issues for
correction. Revisions are revalidated and reviewed before any
execution; bounded retries and permission gates remain intact.
See [release notes](docs/RELEASE_0.87.17.md).

## Agent CLI 0.87.16 — live goal planning feedback

Automatic Goals now report planner/reviewer phases, bounded retry attempts
and task execution status directly in the TUI, including over daemon IPC.
A valid plan can still be revised up to three times before host execution;
the UI now distinguishes that activity from real tool calls.
See [release notes](docs/RELEASE_0.87.16.md).

## Agent CLI 0.87.15 — lazy RE lab cleanup and toolbox menu

A host-owned mission lease prevents concurrent lazy lab runs. Mission-started
guests are stopped on normal completion or errors, while already-running VMs
are preserved. The Agent CLI `/re` panel can now launch the temporary RE
toolbox shell directly. See [release 0.87.15](docs/RELEASE_0.87.15.md).

## Agent CLI 0.87.14 — host-owned lazy RE environments and TUI

The Agent CLI now offers `/re` and `kitt re` for discovery, guest
profile enrollment, controlled on-demand VM startup, and guided ISO/QCOW2
setup with Podman/Docker. Regular coding sessions do not start VMs.
Only operator-approved lab profiles can auto-start. See the
[RE environment guide](docs/RE_LAZY_ENVIRONMENTS.md) and
[release 0.87.14](docs/RELEASE_0.87.14.md).

## 0.87.3 — browser gateway forwarding

FailoverLLMClient now delegates scoped browser-gateway capability discovery to the selected primary client only, without granting authority through fallback providers. Host trust and origin checks remain mandatory.

## Release 0.86.5 — Virtualized list mouse wheel

Fixes a second mouse-wheel movement in virtualized TUI panels (Conversations, Timeline, command palette, model selection, diff and Reverse Proxy): selecting the next/previous item already refreshes the visible slice, so scrolling the underlying Window as well moved the content twice. The wheel handler now changes the model index only, leaving ordinary transcript and nonvirtual scroll handlers unchanged. Includes a behavioral regression test. Full notes: [v0.86.5](docs/RELEASE_0.86.5.md).

## Release 0.86.4 — Rendered mouse coordinates and accurate transcript scrolling

Fixed the follow-up mouse regression: prompt_toolkit already translates screen clicks to content coordinates, so a second KITT scroll offset incorrectly targeted modal actions. Transcript wheel scrolling now uses the renderer's actual viewport instead of subtracting visual window height from logical line count (which breaks wrapped output). History expansion preserves the visible position, and debug logs record received wheel events by surface. Includes terminal-level SGR wheel and scrolled-modal tests, without adding a TUI framework or changing the proxy. See [release notes](docs/RELEASE_0.86.4.md).

## Release 0.86.3 — TUI approvals, workarea scrolling and Reverse Proxy controls

Selections in the approval modal dismiss it immediately without canceling the pending decision; concurrent mouse/keyboard submissions are serialized, and failed daemon acknowledgments restore the request. Workspace-scoped remembered `run_command` rules now apply only to the matching executor identity; explicit denials remain authoritative. Transcript scrolling is routed to the workarea even without an overlay, and Reverse Proxy context tabs restore focus and refresh data. OpenTUI-inspired interaction principles are implemented using the existing `prompt_toolkit` stack. See [release notes](docs/RELEASE_0.86.3.md).

## Release 0.86.1 — Parallel child admission and live monitoring

The task-plan host can dispatch multiple **independent** child tasks using `plan.dispatch_ready` (up to four by default), sharing one parent budget while keeping per-child permissions, worktrees, browser sessions and verification requirements. `Ctrl+X, A` opens the Agent monitor; the provider endpoint chord is now `Ctrl+X, E`. Automatic durable Goals contracts now opportunistically fan out independent file-scoped implementation items in autonomous mode, while the existing GoalScheduler retains lease ownership and independently validates each completed item. Tasks with overlapping files or unsatisfied dependencies remain sequential. See [release notes](docs/RELEASE_0.86.1.md).

## Release 0.85.0 — Structured Agent results

Planning and review/validation/completion reports return one JSON object without textual markers. With reverse-proxy contract v3, the model puts that object directly in content, avoiding double serialization. Protocol 0.10.0 owns the identifiers and strict result decoder; Proxy 5.0.0 is the compatible companion. WebChat continues to own token limits. See [release notes](docs/RELEASE_0.85.0.md).

## Release 0.84.10 — WebChat owns token limits

Reverse-proxy turns leave context and output token limits to WebChat. Local profile placeholders and token quotas no longer reject or truncate prompts, reviews, conversation messages or tool observations. Token estimates remain telemetry, and operational limits stay active. Failed prompt preparation receives scoped diagnostics.

See [release notes](docs/RELEASE_0.84.10.md).

## Release 0.84.9 — Cancellation, selected inputs and build verification

Automatic contracts register the planning turn for cancellation and refuse work after cancellation. Selected text files and attachment references survive planning and durable execution. Full Node verification includes check/build scripts and directory targets, and reports whether workspace verification actually ran. The native bridge invalidates changed files and validates symbol paths before scoped reads. Tool argument limits come from kitt-protocol.

See [release notes](docs/RELEASE_0.84.9.md).

## Release 0.84.8 — live durable-contract progress

Automatic durable contracts now forward safe inner-turn progress to the outer user turn. While T01/T02/... execute, the TUI receives thinking, context, tool and edit events instead of appearing stalled between contract checkpoints.

Planning itself now emits visible thinking state. Interactive memory recall is bounded to four seconds and degrades to empty recall when kitt-memoryd is temporarily unavailable; recall remains contextual enrichment rather than execution authority. Prompt construction and memory recall now have separate latency telemetry. See [release notes](docs/RELEASE_0.84.8.md).

## Release 0.84.7 — immediate TUI session transition on submit

Submitting the first prompt now leaves the home screen immediately, before daemon connection/attachment and automatic-contract bootstrap complete. The session shows the submitted prompt, `STARTING` status and the active core task instead of appearing frozen.

`TurnStarted` reconciles the optimistic prompt instead of duplicating it. Startup failures keep the session visible, switch to `ERROR`, mark the core task failed and surface a persistent toast. See [release notes](docs/RELEASE_0.84.7.md).

## Release 0.84.6 — complete Goals/TaskPlan verification separation

GOAL-owned durable contract items without a nested TaskPlan no longer receive TaskPlan host-execution/verification state in their model context. This removes the remaining contradictory instruction that could still tell the model to run registered TaskPlan verification even though `GoalStepVerifier` owns post-turn validation.

Agent 0.84.5 already stopped the generic TaskPlan gate from rejecting such final responses; 0.84.6 completes the same root-cause fix at context construction. Real TaskPlans retain their host context and completion gate unchanged. Tool budgets are unchanged. See [release notes](docs/RELEASE_0.84.6.md).

## Release 0.84.5 — goal-contract loop verification ownership

Goal-owned durable contract items no longer pass through the nested per-turn TaskPlan completion gate when that turn has no TaskPlan. The Goals contract already owns post-turn deterministic checks, independent validation and retry; forcing the inner turn through TaskPlan verification produced a false `plan.verify` path, consumed the remaining tool budget, and prevented the contract from advancing.

Contract item prompts now explicitly keep `plan.submit/plan.verify` out of that nested path. Real TaskPlans still retain the existing host completion gate unchanged. See [release notes](docs/RELEASE_0.84.5.md).

## Release 0.84.4 — durable loop hardening

Automatic contracts now require at least one executable task plus final integration validation, and HIGH/CRITICAL planned paths receive a bounded read-only semantic review before the first mutation. Host-owned resolvable blocks retry the same contract item instead of consuming global scheduler failure budgets; terminal blocks are committed explicitly.

Existing LSP operations for definition, hover, semantic references, diagnostics, outline and call hierarchy are now wired to the runtime instead of being dead registrations. ContextEpoch/cache-region reconciliation remains authoritative, with provider-reported cache hits recorded as durable telemetry when available. See [release notes](docs/RELEASE_0.84.4.md).

## Release 0.84.3 — refresh stale managed Reverse Proxy control plane

Managed Reverse Proxy start/restart now refreshes a resident control plane once per Agent process before creating services. This fixes upgrades where an older control-plane process remained on port 2999 and silently ignored the newer `log_file` and ownership fields even though the installed Reverse Proxy binary was current.

When Agent logging is configured, the returned managed instance must also report a proxy log path in the same directory; otherwise startup fails explicitly instead of silently logging elsewhere. Restart recreates the instance with the Agent's current logging settings. See [release notes](docs/RELEASE_0.84.3.md).

## Release 0.84.2 — automatic durable contracts

Persisted user turns in `mode=auto` now use the durable Plan → Execute → Validate → Retry contract by default. The planner decomposes the request, GoalScheduler executes one item at a time, validation failures retry the same item, and completion still requires every item plus final integration validation to pass.

Managed Reverse Proxy instances started from the Agent inherit the Agent logging level/content, write their own log file in the same log directory, and are tied to the Agent process lifecycle. See [release notes](docs/RELEASE_0.84.2.md).

## Release 0.84.1 — focused goal-step verification

The durable task-contract behavior from 0.84.0 is unchanged, but the implementation is now structurally smaller: `GoalStepExecutor` only executes turns and delegates post-turn verification. Mutation snapshots, adversarial-review runtime and completion verification live in focused modules instead of one oversized executor. See [release notes](docs/RELEASE_0.84.1.md).

## Release 0.84.0 — durable Plan → Execute → Validate → Retry contracts

Large requests can now be decomposed into a bounded persistent task contract on top of the existing Goals scheduler. Each item is executed, checked with host-owned verification, independently validated, and retried in place until it passes or its attempt budget is exhausted. The scheduler owns the lease-fenced state transition, and a contract goal cannot become `SUCCEEDED` until every item, including final integration validation, is `DONE`.

Contract plans cannot grant capabilities or inject arbitrary command argv. `no_history=True` now also isolates local conversation context and Reverse Proxy provider sessions for planner/validator turns. No KITT Protocol, Reverse Proxy, or KITT Memory wire change was required.

Use `kitt -p "..." --contract --allow write,run` to plan and inspect before execution, add `--contract-yes` for immediate start, or use `/contract`, `/contract-status`, and `/contract-resume` interactively. See [release notes](docs/RELEASE_0.84.0.md).

## Release 0.83.19 — consistent endpoint trust in model selection

The pending model-selection/authentication path now grants the same exact-origin trust as explicit Reverse Proxy role binding and `/model`. Non-default managed ports stay usable when selected through either UI path, without trusting workspace configuration or neighboring endpoints. See [release notes](docs/RELEASE_0.83.19.md).

## Release 0.83.18 — Trust the explicitly selected managed proxy endpoint

Selecting a Reverse Proxy instance for Context, Code or Validation now records its exact origin in the existing private endpoint trust store before saving the router. This fixes `Refusing provider egress` for newly allocated ports such as 3001. Merely listing or starting a service and workspace configuration do not grant trust. See [release notes](docs/RELEASE_0.83.18.md).

## Release 0.83.17 — REA MCP and evidence-qualified completion

Add an optional built-in REA adapter that registers an installed local `rea mcp` server through the existing governed MCP boundary; KITT never downloads REA implicitly. Evidence records now retain authority, kind, confidence, coverage, limitations, producer metadata and a provenance digest in schema 11. The existing task-plan ledger projects verification obligations and residual unknowns into the trusted output contract so incomplete work stays explicit without introducing a second scheduler, ledger or wire protocol.

See [release notes](docs/RELEASE_0.83.17.md) and [REA integration](docs/REA.md).

## Release 0.83.16 — execution boundary hardening

Contain native Python fallback reads with RepositoryScanner and WorkspaceFileSystem. Run user regex in a disposable process with a 3 second execution deadline. Reserve both move/rename paths. Capture file and directory mutations before execution and guard rollback against changed contents. Directory recovery is bounded to 1000 entries/32 MiB and refuses links. Verify directory descendants. Recover the latest run state beyond 10000 events. Reject invalid UTF-8 in proxy SSE. Bound memory context to 8 seconds and receipt telemetry to 1 second; submit one receipt batch. A memory outage blocks the turn with a recoverable retry failure. Reconnect daemon streams by delivered cursor, deduplicate replay and buffer live events during attachment without resubmitting mutations. Release path leases if resource acquisition fails. Source-tree CLI version agrees with package version. Add release-critical regressions.

See [release notes](docs/RELEASE_0.83.16.md).

## Agent CLI 0.83.15 — task-plan child-manager wiring

Task-plan host-state evaluation now uses the retained-agent manager rather than the spawn-only `ChildTools` adapter, fixing the pre-dispatch `ChildTools.repo` crash seen before the first Reverse Proxy request.

See [release notes](docs/RELEASE_0.83.15.md).

## Agent CLI 0.83.14 — reverse-proxy pre-dispatch hardening

Reverse-proxy turns now retry only provably pre-accept connectivity failures, reserve actual estimated prompt usage instead of the whole remaining allowance, and emit explicit diagnostics around the boundary before model transport. Ambiguous connection failures remain non-retryable to avoid duplicating browser-backed turns.

See [release notes](docs/RELEASE_0.83.14.md).

## Agent CLI 0.83.13 — remaining local agentic invariants

This patch closes the remaining locally-testable authority, isolation, compaction, no-progress and execution-policy gaps without adding a new protocol, scheduler or authority. Managed-process controls are regression-tested against their original AuthoritySnapshot; real Git worktrees prove child isolation/integration/discard; compaction retains critical execution diagnostics; unchanged reread loops stop deterministically; opaque interpreter eval wrappers fail closed even under `allow-all`; and learning/experiment analytics remain privacy-safe and never auto-promote candidates.

See [release notes](docs/RELEASE_0.83.13.md).

## Agent CLI 0.83.12 — per-conversation concurrency and proven recovery

Active turns in the same conversation are now serialized at admission while different conversations continue to execute in parallel. This release also adds deterministic evidence for FIFO resource fairness, selective rollback, exact snapshot/artifact recovery, and bounded query/page retrieval for large artifacts.

See [release notes](docs/RELEASE_0.83.12.md).

## Agent CLI 0.83.11 — bounded cancellation and race-free artifact GC

The Agent now bounds both active turns and synchronous producer threads that remain alive after cancellation: new prompts can recover from blocked calls without allowing orphan threads to grow without limit. Artifact GC also rechecks references under a SQLite write reservation immediately before deleting a shared blob, closing the TOCTOU window found during the 0.83.10 review.

See [release notes](docs/RELEASE_0.83.11.md).

## Agent CLI 0.83.10 — runtime integrity and cancellation

This release fixes three concrete runtime failures without introducing a new architecture: artifact garbage collection no longer removes content-addressed blobs that are still referenced; cancelled synchronous turns no longer permanently consume all global prompt capacity; and long-running mutations renew the existing coordination leases while the tool is active.

Artifact ownership is also stricter: a `turn_id` now requires a valid matching conversation.

See [release notes](docs/RELEASE_0.83.10.md).

## Agent CLI 0.83.9 — authoritative native execution flow

Agent CLI now keeps turn lifecycle, completion validation, and tool execution on native `TurnProcessor`, `TurnToolLoopMixin`, and `ToolRegistry` entrypoints instead of replacing methods at runtime. Existing policy, approval, durable evidence, replay, post-edit verification, and cancellation controls are reused unchanged. Role-policy denials also return a valid `ToolResult` instead of raising while constructing the denial.

No shared Protocol field or cross-repository contract changed. Reverse Proxy **4.9.3** and KITT Protocol **0.9.0** remain compatible.

See [release notes](docs/RELEASE_0.83.9.md).

## Agent CLI 0.83.8 — canonical Reverse Proxy transport

Agent CLI now locks the Reverse Proxy request boundary to structural `tools/tool_choice`, typed `kitt_context` and protocol-owned `kitt_meta`. Regression coverage explicitly rejects reintroduction of legacy `functions/function_call` transport and stale textual `Tool Contract` authority. The existing canonical `<kitt-tool>` bridge remains an internal Agent representation after native tool-call extraction; no additional semantic aliases are introduced.

This release pairs with Reverse Proxy **4.9.3** and KITT Protocol **0.9.0**.

See [release notes](docs/RELEASE_0.83.8.md).

## Agent CLI 0.83.7 — recoverable retry attempt identity

Persisted safe retries now recover the latest durable `attempt_id` after interruption, use deterministic attempt numbering, and reject stale completion from an older attempt. Side-effecting uncertain operations remain fail-closed.

See [release notes](docs/RELEASE_0.83.7.md).

## Agent CLI 0.83.6 — durable tool attempt identity

Tool execution receipts now expose a stable logical `operation_id` plus a per-attempt `attempt_id`. Confirmed side effects remain replay-only, unresolved side effects remain blocked as uncertain, and only safe non-side-effect retries allocate a new attempt without changing the logical operation.

See [release notes](docs/RELEASE_0.83.6.md) for the invariants and regression evidence.

## Agent CLI 0.83.5 — Memory 0.9 protocol alignment

Agent CLI now resolves KITT Protocol **0.9.0** and aligns its shared-memory client with KITT Memory **0.9.0**. Progressive hydration explicitly skips unused provenance, search exposes bounded exclusion/context-hint controls, and the client supports deterministic baseline/ETag requests without changing the existing agent loop or creating a second memory authority.

See [release notes](docs/RELEASE_0.83.5.md) for compatibility and regression coverage.

## Agent CLI 0.83.4 — bounded model-journal checkpointing

Model dispatch no longer forces every derived event-ledger projection to checkpoint before the provider request. `ModelRequestPrepared` remains durable in the append-only ledger, while projection checkpoints keep their normal sparse cadence and periodic checkpoints are written in one SQLite transaction. This removes redundant write-lock acquisitions from the Agent → Reverse Proxy critical path without weakening replay durability. The release also removes a duplicate `concurrent.futures` import.

See [release notes](docs/RELEASE_0.83.4.md) for the failure analysis and regression coverage.

## Agent CLI 0.83.3 — gateway hardening and host surface actions

An OpenAI-compatible local backend needs an explicit loopback endpoint to count as local. Only the Ollama protocol has a known implicit loopback default.

## Agent CLI 0.83.0 — bounded gateway lifecycle

See [release notes](docs/RELEASE_0.83.0.md) for cancellation, privacy, upstream budgets, exact file pagination and Memory reconciliation.

## Agent CLI 0.82.3 — durable continuous-agent runtime

Agent CLI 0.82.3 adds durable agent-family messaging, passivation/revival, a
reviewable continual-harness refinement pipeline, persistent heartbeats and
schedules, bounded program-session state, provider parking/failover and a
line-oriented \`kitt rpc\` headless surface. These features extend the existing
host-owned runtime; no arbitrary model \`eval\`/\`exec\`, second memory store or
parallel workflow engine was introduced.

The implementation reuses existing child/session ledgers, \`scheduled_tasks\`,
\`RuntimeStateStore\`, SafeRuntime capabilities, session branching/export and
Models.dev freshness/runtime discovery. See
[docs/CONTINUOUS_AGENT_RUNTIME.md](docs/CONTINUOUS_AGENT_RUNTIME.md).

## Agent CLI 0.82.0 — host-owned plans, evidence and child containment

Agent CLI 0.82.0 adds optional bounded task DAGs to the existing `kitt_runtime` pipeline. The host assigns IDs, selects dependency-ready tasks, delegates through the existing child manager and approves completion from current verification evidence. Required checks use exact registered argv through normal policy, approval, sandbox and budget controls. A child report or stdout success marker cannot approve completion.

This release pairs with Protocol **0.7.0** and Reverse Proxy **4.8.0**. Typed host state is refreshed before each model round trip; incompatible proxy capabilities produce an actionable error. Child spawn is serialized, leaf workers cannot delegate, late results cannot resurrect cancelled children, active tool commands receive cancellation, and POSIX child workers supervise loss of their parent. Approval waits pause active duration without resetting usage. Automatic rollback checks current content before restoring an earlier snapshot.

See [docs/AGENTIC_PLANNING.md](docs/AGENTIC_PLANNING.md) for operations, limits, verification semantics and remaining E2E gates. The 30 acceptance requirements still need the release-SHA evidence prescribed by [docs/AGENTIC_RUNTIME_ACCEPTANCE.md](docs/AGENTIC_RUNTIME_ACCEPTANCE.md).

## Previous release: Agent CLI 0.81.1 — safe state upgrades and bounded-loop hardening

Agent CLI 0.81.1 restores transactional upgrades for persisted Agent state through schema 10, so workspaces on schemas 7, 8 or 9 no longer fail at startup after an update. The v10 security migration invalidates only legacy global remembered-approval rules that cannot be safely assigned to a workspace; conversation history, event ledger and child-session state are preserved. `kitt doctor` now reports state-schema compatibility explicitly, reset backups use SQLite's backup API so WAL-backed state is captured safely, and predictable schema failures are rendered as concise CLI errors instead of raw tracebacks.

The Agent now treats `agent_loop_action_budget` as a bounded-loop checkpoint cadence rather than a total per-turn tool limit. The independent runaway ceiling remains a turn-level safety budget and defaults to 24 tool calls. Installers require the same Python **3.14+** floor declared by the package and perform an installed state-schema smoke check.

This release is paired with KITT Reverse Proxy **4.7.5**, which carries explicit loop epoch/action counters across tool round trips and prevents checkpoint state from disappearing after the boundary action.

## Agent CLI 0.81.0 — progressive memory, replay safety and structural-only transport

Agent CLI 0.81.0 completes the current Agent Engineering slice without introducing a second workflow or memory engine. Provider transport is now structural-only: typed `ContextEnvelope`, native tool definitions and `KittRequestMetadata` are the authoritative path, while the old prompt-parsing/injection helpers were removed. Sync, async and recoverable Reverse Proxy retries preserve request metadata and conversation correlation.

Memory context now uses KITT Memory 0.7 progressive retrieval (`memory.search` → `memory.get`, with bounded snippets retained when full hydration exceeds the memory budget) instead of a fixed `recall(limit=8)` path. KITT Memory remains the sole durable semantic-memory authority.

Execution durability is hardened with explicit event replay/idempotency tests, selective workspace snapshot diff/preview/restore, per-conversation runtime lifecycle tests, repeated-reread no-progress coverage and one global stage-aware execution budget. Classifier, condenser, execution model calls, validation tools and child leases all debit the same turn wallet; stage accounting is telemetry, not separate authority.

The earlier engineering baseline targeted KITT Protocol **0.6.0**, KITT Memory **0.7.0**, KITT Reverse Proxy **4.7.4** and Assistant Runtime **0.2.27**. See [docs/AGENT_ENGINEERING.md](docs/AGENT_ENGINEERING.md) and [docs/AGENTIC_RUNTIME_ACCEPTANCE.md](docs/AGENTIC_RUNTIME_ACCEPTANCE.md).

## Agent CLI 0.80.5 — strict structural request identity

Agent CLI 0.80.5 keeps `KittRequestMetadata` strict end to end. The execution-stream boundary now requires explicit `conversation_id`, `turn_id` and structured `route`; the tool-loop boundary likewise rejects execution without an explicit agent route. Production paths pass the real turn identity, while test harnesses use explicit structural fixtures instead of empty strings, placeholder identities or prompt-derived routing.

The same request ID remains unique per model call and shared through the protocol-owned metadata/header correlation path. Typed `kitt_context`, structural tool definitions, streaming/reasoning cleanup, TTFT telemetry and reverse-proxy session continuity remain unchanged; no textual `Memory:`, `Repo Map:` or `Tool Contract:` parsing was restored.

This patch keeps the Agent Engineering ownership model introduced in 0.80.x: durable EventLedger/RunCoordinator state, ContextEpoch/recovery refs, global budgets with child leases, authority snapshots, selective rollback, managed background processes, structural roles, bounded skill/plugin discovery, Memory lifecycle evidence and privacy-safe `kitt learn`. It is paired with KITT Protocol **0.5.2**, KITT Memory **0.6.1**, KITT Reverse Proxy **4.7.4** and Assistant Runtime **0.2.26**.

## Agent CLI 0.80.2 — hardened agent engineering runtime

Agent CLI 0.80.2 completes the durable Agent Engineering rollout with structural agent roles, bounded skill discovery, managed background-process lifecycle, public Memory lifecycle evidence, privacy-safe `kitt learn` analytics, stronger no-progress recovery and provider usage reconciliation.

`DISCOVER / ARCHITECT / IMPLEMENT / VERIFY / REVIEW` are enforced by host-side tool/mutation policy rather than prompt persona. Managed processes now expose `process.start/read/stdin/signal/stop/resume`, persist `PROCESS_OUTPUT` / `PROCESS_EXIT` observations and revalidate the original authority snapshot before control operations. Skill discovery is bounded by roots, depth, file count and byte budgets before semantic selection.

`kitt learn`, `kitt learn suggest` and `kitt learn experiment ...` expose anonymized local evidence and control/candidate comparisons without automatic promotion. Memory lifecycle hooks are digest-only and feed the shared `kitt-memoryd` job pipeline; raw prompts/tool arguments are not copied into a parallel memory store.

Agent CLI 0.80.0 moved execution durability, recovery and authority into deterministic host-owned contracts without creating a second agent runtime. The append-only EventLedger and RunCoordinator now track replayable run state, workspace mutations stay leased through verification and rollback, and each turn uses one global execution budget that delegates bounded wallet slices to retained subagents with persisted lineage.

Model context now carries a content-derived ContextEpoch across memory, repository evidence, skills, plugin/tool surface, policy/formatting constraints and provider configuration. Large exact context remains recoverable through ArtifactStore references, and compaction writes a structured checkpoint with exact source recovery instead of making the summary the only remaining evidence.

Approval resumes revalidate an ExecutionAuthoritySnapshot against current policy, autonomy, sandbox and capabilities. Workspace “always allow” rules use typed SavedPermission semantics and can be bound to the executable principal, while plugins may declare typed export capabilities that are enforced in both in-process and isolated-worker execution.

Pre-mutation WorkspaceSnapshots support exact rollback when post-edit verification fails. Memory recall is traceable through kitt-memory RecallTrace/MemoryConsumptionReceipt contracts, and terminal Task Episodes emit efficiency plus evidence-backed learning candidates without silently auto-applying runtime changes.

This release is designed for KITT Protocol **0.5.1**, KITT Memory **0.6.1**, KITT Reverse Proxy **4.7.1** and Assistant Runtime **0.2.26**. See [docs/AGENT_ENGINEERING.md](docs/AGENT_ENGINEERING.md) for ownership boundaries, invariants and implementation details.

## Agent CLI 0.79.0 — LLM-first bounded execution loops

Reverse-proxy/WebChat execution now uses agent-contract **v2** with a language-neutral `agent-loop`. The original human request is forwarded verbatim and is the semantic authority: Agent CLI no longer compiles, translates, summarizes, or lexically classifies natural-language intent before WebChat decides what to do.

KITT remains deterministic where it should be: capability scoping, policy/approval, repository/process execution, host evidence, cancellation, token bounds and validation gates. The model owns a short execution loop with an objective, completion criteria and status. After the configured `agent_loop_action_budget` host round trips (default **4**), the Reverse Proxy requires a checkpoint so the model reassesses actual evidence before continuing.

The first agent-loop mutation requires repository evidence, and any mutation still requires a successful host build/test/check before final completion when validation is available. This is an internal ecosystem protocol change; Agent CLI 0.79.0 is paired with Reverse Proxy 4.7.0 and does not retain agent-contract v1 compatibility.

## Agent CLI 0.78.11 — transcript copy and WebChat continuity

Agent CLI 0.78.11 restores native terminal text selection/copy on the main transcript while keeping mouse interaction available in menus and modal surfaces. The `Ctrl+O` expand/collapse hint is now rendered only on the latest tool/thought block that the shortcut can actually toggle.

Together with KITT Reverse Proxy 4.6.8, fresh browser-backed WebChat sessions also preserve prior API conversation context without repeatedly replaying it on subsequent turns.

## Agent CLI 0.78.10 — main-first ecosystem dependencies

Internal K.I.T.T. VCS dependencies now follow sibling `main` branches instead of embedding cross-repository commit SHAs. The root ecosystem installer remains responsible for composing the current sibling sources and recording the exact fetched revisions as provenance.

## Agent CLI 0.78.9 — Ctrl+C cancellation recovery

Agent CLI 0.78.9 makes cancellation a hard boundary for the local TUI execution lane. If a provider/tool thread is still unwinding after **Ctrl+C**, the bridge retires that single-worker executor and starts the next prompt on a fresh worker instead of queueing behind the cancelled turn. Generation-aware cleanup also prevents a stale consumer from clearing the replacement turn state.

Agent CLI 0.78.8 no longer treats an invalid model/chat response as an automatic end of the conversation. Recoverable reverse-proxy failures preserve the active session and are surfaced in the TUI with explicit **Continue** and **Retry** actions (keyboard or mouse).

Recovery starts from the same conversation state and instructs the model not to replay tool calls or mutations that already succeeded. Network/protocol failures that are not declared recoverable remain normal failures.

Agent CLI 0.78.7 also keeps the original user prompt authoritative when building bounded execution slices and emits the `agent.startup` debug identity event for stale-runtime diagnosis.

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

## Agent CLI 0.72.3 — Reverse Proxy mouse polish

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

## Agent CLI 0.75.1 release alignment

0.75.1 is the promoted semantic-IR snapshot after cross-platform validation. It keeps the 0.75 Surface/Backend IR feature set, fixes Surface projection to occur on completed tool events, and aligns runtime fallback/package/lock version metadata for deterministic release automation.

## Agent CLI 0.77.1 — Assistant 0.1.10 composition alignment

Agent CLI 0.77.1 keeps the 0.77 evidence-first runtime behavior unchanged and updates the frozen Assistant companion revision used by CI, PR validation, architecture checks and release composition to Assistant 0.1.10 / runtime 0.2.21. Package and uv-lock metadata are aligned to the patch release.

## Agent CLI 0.77.0 — evidence-first agentic execution

## Assistant compatibility for 0.77.0

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

## Agent CLI 0.78.4 — staged reverse-proxy execution

- Broad implementation turns now preserve the deterministic `DISCOVERY` phase in the structured reverse-proxy turn envelope even after the textual tool contract is converted to native tools.
- The first model action is one evidence-gathering repository operation; execution then advances one host tool/result round trip at a time instead of asking the model to produce a project-sized action bundle.
- Equivalent semantic IR and raw user requests are deduplicated before provider execution when the extracted Goal is nearly identical to the original request; non-equivalent requirements keep the original text.
- This release is designed for Reverse Proxy 4.6.3, whose session bootstrap/delta contract prevents full workspace/tool context from being replayed on every tool continuation.

## Agent CLI 0.78.5 — staged reverse-proxy prompts

- Reverse-proxy execution no longer forwards the generated Agent persona or textual Tool Contract after route/tool extraction; the proxy receives tools structurally.
- The turn envelope carries a deterministic three-stage plan: `discovery -> mutation -> validation`, with only the current phase exposed to the model.
- Trusted incremental orchestration sent to the reverse proxy is bounded to 4 KiB; workspace evidence remains a structured untrusted turn payload.
- Direct/local providers keep their existing prompt behavior; the compaction applies specifically to the KITT reverse-proxy boundary.

## Agent CLI 0.78.6 — structural reverse-proxy tools

- Tool schemas now travel structurally from the execution request through the model/client boundary instead of being rediscovered from prompt text.
- Reverse-proxy prompt compaction can safely remove the textual `Tool Contract` without dropping `kitt_runtime`.
- The structural schema preserves the exact per-turn `kitt_runtime.operation` allowlist; host policy and approval checks remain authoritative.
- Added a regression test for compact discovery prompts with structural tools.

### Optional offline multilingual lexicon

The local PT-BR/EN/ES lexicon optionally imports UniMorph inflections, Open Multilingual Wordnet suggestions, and build-time wordfreq/Snowball/spaCy metadata. It makes no runtime downloads; all corpus licensing and operator-owned index setup are documented in [SEMANTIC_LEXICON.md](docs/SEMANTIC_LEXICON.md). This path is opt-in and never grants execution permission.
