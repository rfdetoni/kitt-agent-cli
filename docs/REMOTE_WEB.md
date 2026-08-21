# KITT Remote Web Architecture

## Goal

Expose an official browser UI for a running KITT workspace without turning the browser into a terminal emulator and without exposing the daemon's private IPC endpoint to the LAN.

```text
Browser (HTML/CSS/ESM)
   |  POST/GET              |  EventSource
   |  REST commands         |  SSE stream
   v                        v
KITT Remote HTTP Gateway (kitt.remote)
   |
   | authenticated private IPC
   v
KITT Daemon
   |
   +-- one KittRuntime per workspace
   +-- history / approvals / artifacts
   +-- TurnProcessor
   +-- plugins / MCP
   +-- native engine / children
```

The daemon remains local/private. On Unix it continues to use a Unix-domain socket; on Windows it remains loopback-only TCP. `kitt.remote` is the only LAN-facing component.

## Why REST + SSE

KITT's command flow is naturally request/response while turn progress is naturally one-way event streaming. REST + SSE maps directly onto that shape:

- Browser -> KITT: `POST` commands.
- KITT -> Browser: persistent `text/event-stream` connection.
- No `setInterval`/polling loop.
- No WebSocket dependency.
- Native browser reconnect behavior.
- KITT's persisted `daemon_events.sequence_id` is used as the replay cursor.

### Gap-free reconnect algorithm

1. Browser sends the last event id (`Last-Event-ID` and `after`).
2. Gateway pages `events_since` until persisted history is caught up.
3. Gateway attaches to realtime daemon subscription at that cursor.
4. Live events racing with attach are buffered.
5. Attach replay and buffered live events are sorted/deduplicated by `sequence_id`.
6. SSE sends `id: <sequence>` for every event.
7. Heartbeat comments keep NAT/proxy connections alive.

A slow/disconnected browser can reconnect and replay persisted events rather than forcing the daemon to retain an unbounded per-client queue.

## HTTP surface

### Public before pairing

- `GET /` + `/app.js` + `/app.css`
- `GET /api/health`
- `POST /api/pair`

### Authenticated reads

- `GET /api/me`
- `GET /api/status`
- `GET /api/extensions`
- `GET /api/sessions`
- `GET /api/sessions/{id}`
- `GET /api/sessions/{id}/events` (SSE)
- `GET /api/approvals?session_id=...`
- `GET /api/artifacts?session_id=...`
- `GET /api/artifacts/{id}?session_id=...&offset=...`
- `GET /api/diff`

### Authenticated + CSRF mutations

- `POST /api/sessions`
- `POST /api/sessions/{id}/input`
- `POST /api/turns/{turn_id}/cancel`
- `POST /api/approvals/{approval_id}/approve`
- `POST /api/approvals/{approval_id}/deny`
- `POST /api/logout`

## Authentication boundary

The existing daemon token remains private in `.kitt/daemon.token`. It is **never** returned to HTML/JS.

`kitt remote` creates a short-lived numeric pairing code. Successful pairing creates a random session token:

- raw token -> browser `HttpOnly; SameSite=Strict` cookie;
- server -> stores only SHA-256(token);
- CSRF -> HMAC(server-only secret, raw token), returned to JS and required on mutations;
- all remote session state -> memory only, discarded when `kitt remote` exits.

Approval nonces remain inside the daemon. The remote API sends only an `approval_id`; the daemon resolves the persisted pending action, atomically issues the grant, and consumes it during continuation.

## Network hardening

- Default bind: `127.0.0.1`.
- LAN requires explicit `--lan`.
- Remote request source must be loopback/private/link-local.
- `Host` must be localhost or a private IP; public/hostname rebinding targets fail closed.
- Cross-origin requests are rejected.
- No CORS enablement.
- CSP: self-only scripts/styles/connect.
- `X-Frame-Options: DENY`, `nosniff`, no-referrer and restrictive Permissions-Policy.
- Pairing and mutations are rate-limited.
- JSON body max: 256 KiB; prompt max: 128 Ki characters.
- HTTP request/SSE worker threads are bounded; sockets have a finite timeout.
- Static paths are a fixed allowlist; no arbitrary filesystem serving.
- Git diff uses fixed argv, no shell, fixed workspace root, timeout and 256 KiB output cap.
- Artifact reads require both workspace and conversation ownership and are paged to <=32 KiB.

## UI

The UI is semantic rather than a browser terminal emulator. It preserves KITT's terminal/Knight-Rider visual identity while rendering structured events directly.

Desktop:

- sessions/workspace/activity/children left sidebar;
- central conversation + streamed output + approvals + input/cancel controls;
- right inspector tabs for events, tool output, approvals, diff, artifacts and status.

Tablet/mobile:

- responsive single-column conversation;
- slide-out sessions drawer;
- slide-out inspector drawer;
- touch-sized approve/deny/cancel controls.

All untrusted model/tool content is inserted with DOM `textContent`; the implementation does not inject model HTML into the page.

## Deployment scope

Version 1 is for same-machine/private-LAN access. It deliberately does not add internet exposure, UPnP, tunnel creation, port forwarding or public-cloud relay. A future internet-facing mode should require a distinct threat model and strong TLS/device identity rather than relaxing the LAN boundary.
