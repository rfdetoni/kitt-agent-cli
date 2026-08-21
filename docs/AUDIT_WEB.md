# KITT Main + Remote Web Security/Correctness Audit

Audited base: `13ca438c81895b017f15f452d42fc1dc79e8180b`.

## Findings fixed by this bundle

### [High] Detached daemon command could not be parsed
- **Confidence:** High
- **Location:** `kitt/daemon/process.py`, `kitt/cli/main.py`
- **Problem:** detached startup generated `daemon run --workspace <root>`, but the parser accepted only `start|stop|status` and had no daemon `--workspace` option.
- **Impact:** background daemon startup can fail before the web gateway is usable.
- **Secure fix:** expose `daemon run` as the private foreground action and spawn `--root <root> daemon run` using the existing global root argument.
- **Validation:** CLI regression test parses the exact command shape.

### [High] `continue_turn()` blocked the daemon event loop
- **Confidence:** High
- **Location:** `kitt/daemon/server.py::_continue_turn`
- **Problem:** synchronous continuation/tool execution ran directly inside an async method.
- **Impact:** after remote/TUI approval, daemon clients, SSE, cancellation and heartbeats could freeze until the blocking tool/provider returned.
- **Secure fix:** bounded producer thread -> asyncio queue bridge, mirroring the corrected `arun_turn()` architecture.
- **Validation:** full suite + concurrency behavior mandatory before merge.

### [High] Daemon client reused across independent asyncio loops
- **Confidence:** High
- **Location:** `kitt/daemon/process.py`
- **Problem:** one `DaemonClient` was reused through multiple `asyncio.run()` calls. Stream reader/writer/tasks are loop-owned resources.
- **Impact:** lifecycle/status/stop can fail with cross-event-loop errors or stale stream state.
- **Secure fix:** one async operation owns one client and closes it in the same loop.

### [Medium] Foreground daemon could survive authenticated stop
- **Confidence:** High
- **Location:** `run_daemon_foreground`
- **Problem:** `loop.run_forever()` had no stop coupling to `DaemonServer._running`.
- **Impact:** daemon endpoint/runtime could close while the background Python process remained alive.
- **Secure fix:** `asyncio.run(_run_daemon_server)` watches server lifecycle and exits when authenticated stop completes.

### [Medium] Generic attach replay is page-bounded
- **Confidence:** High
- **Location:** daemon attach protocol
- **Problem:** attach returns at most one persisted page. A remote client reconnecting far behind cannot assume that one page is complete.
- **Impact:** silent UI history gaps if the client treats attach as exhaustive replay.
- **Secure fix:** add bounded `events_since`; Remote Gateway explicitly pages to the current cursor before attaching, then buffers/deduplicates the attach race.

### [Medium] Browser must not inherit daemon credentials
- **Confidence:** High
- **Location:** new remote auth boundary
- **Problem:** directly exposing daemon TCP/token would collapse the local trust boundary.
- **Impact:** XSS/browser compromise could obtain a durable privileged daemon credential.
- **Secure fix:** daemon remains private; independent ephemeral pairing/session auth fronts it. Daemon token and approval nonces never cross to the browser.

### [Medium] Multi-tab CSRF invalidation
- **Confidence:** High
- **Location:** `kitt/remote/auth.py`
- **Problem found during implementation:** rotating a single stored CSRF hash on every `/api/me` made a second browser tab invalidate the first.
- **Impact:** intermittent 403 failures and poor remote reliability.
- **Secure fix:** stable HMAC CSRF derived from the raw session token using a server-only secret.

### [Medium] Thread-per-request server was initially unbounded
- **Confidence:** High
- **Location:** `kitt/remote/server.py`
- **Problem found during implementation:** `ThreadingHTTPServer` can create unbounded request threads; SSE connections are long-lived.
- **Impact:** accidental/malicious LAN connection storms could exhaust memory/threads.
- **Secure fix:** bounded semaphore (64 concurrent requests), bounded accept queue and 45-second socket timeout.

### [Medium] `HEAD` initially delegated to GET/SSE
- **Confidence:** High
- **Location:** `RemoteRequestHandler.do_HEAD`
- **Problem found during implementation:** blindly calling `do_GET()` could open a long-lived SSE stream for a HEAD probe.
- **Impact:** leaked worker/thread and surprising behavior from health scanners/proxies.
- **Secure fix:** HEAD serves only static/health and returns 405 elsewhere.

### [Medium] Remote artifact ownership must be scoped
- **Confidence:** High
- **Location:** daemon `artifact.list` / `artifact.read`
- **Problem:** a paired browser must not retrieve an artifact merely by guessing an ID from another session/workspace.
- **Impact:** cross-session data disclosure.
- **Secure fix:** require session id and verify artifact workspace + conversation ownership before every read; page content <=32 KiB.

### [Medium] Workspace diff must not become a shell endpoint
- **Confidence:** High
- **Location:** daemon `workspace.diff`
- **Problem:** a generic command endpoint would turn the web UI into remote shell execution.
- **Impact:** severe privilege expansion.
- **Secure fix:** fixed `git -C <canonical-root> diff ...` argv, `shell=False`, sanitized env, 5-second timeout, 256 KiB cap; no browser-provided command/path.

### [Low] `list_sessions` omitted a field its CLI consumer expects
- **Confidence:** High
- **Location:** daemon `list_sessions`, CLI sessions command
- **Problem:** CLI reads `active_session_id`; daemon did not return it.
- **Fix:** daemon now includes the current active id.

### [Low] `kitt attach` treated `DaemonEvent` as dict
- **Confidence:** High
- **Location:** `kitt/cli/commands.py`
- **Problem:** `DaemonClient.attach()` converts replay entries to `DaemonEvent`, but CLI used `e.get(...)`.
- **Fix:** use `e.event_type` / `e.payload`.

### [Low] Stale external RTK validation documentation
- **Confidence:** High
- **Location:** `AGENTS.md`
- **Problem:** validation examples still instructed agents to use an external `rtk proxy`, conflicting with KITT-owned clean-room tooling.
- **Fix:** remove the external wrapper requirement.

## Security invariants retained

- KITT approvals remain authoritative; the web UI does not bypass policy.
- No arbitrary shell/PTY endpoint.
- No arbitrary filesystem HTTP route.
- No browser access to daemon token, approval nonce or internal socket path.
- No public-source clients.
- No dependency on a third-party JS CDN.
- No `innerHTML` rendering of model/tool output.
- No silent SSE loss: persisted sequence replay is the source of truth.

## Known intentional limitations

- Trusted-LAN HTTP can be observed by other parties who can sniff that LAN. TLS is supported but not auto-provisioned.
- No public internet/tunnel mode is included.
- Git diff reports tracked Git changes; untracked file contents are not implicitly exposed.
- Artifact preview is text-decoded and bounded; it is not a generic binary download endpoint.
