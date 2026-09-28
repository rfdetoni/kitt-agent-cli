# KITT Reverse Proxy in Agent CLI

## Goal

Agent CLI may use multiple KITT Reverse Proxy instances at once. The reverse-proxy repository owns process lifecycle, provider plugins, named browser profiles and port allocation. Agent CLI owns task-role routing.

## TUI

Open `Ctrl+P -> KITT Reverse Proxy` or `/reverse-proxy`. Agent CLI opens the retained contextual modal immediately with a loading state, then refreshes the control-plane snapshot asynchronously from the UI event loop.

The contextual surface has three views. Every listed row and action control is usable by keyboard or mouse:

- **Services**: active instances and their local endpoints; click a row to select it, then click Restart, Stop or a role-binding action.
- **Plugins / Novo serviço**: connection plugins reported by reverse-proxy; click a plugin row to select it, choose/cycle the Chromium profile, then click **Iniciar serviço** (or press Enter) to launch a new instance without leaving the modal.
- **Profiles**: named Chromium profiles. Click a profile row to select it. Profile data is credential material and remains owned by reverse-proxy.

The panel reuses the existing contextual Float, so the TUI architectural limit remains five physical Floats.

## Role binding

Selecting an instance and pressing:

- `c` binds Context (`context-gather`, `summarize`).
- `e` binds Principal/Code (`chat`, `code-generation`, `code-edit`).
- `v` binds Validation (`validate-diff`).

Bindings are persisted through the existing router save path. No new routing database is introduced.

## Example

```text
gemini-context
  provider: gemini
  model: gemini-web
  endpoint: http://127.0.0.1:3000
  role: Context

chatgpt-code
  provider: chatgpt
  model: chatgpt-web
  endpoint: http://127.0.0.1:3001
  role: Principal/Code
```

Equivalent commands:

```text
/reverse-proxy start gemini --profile context-google --id gemini-context --role context
/reverse-proxy start chatgpt --profile coding-openai --id chatgpt-code --role code
```

## Profile concurrency

A named profile can accumulate login state for multiple providers. Reverse Proxy 4.4 keeps the credential boundary at the profile directory while allowing compatible managed services to attach to one profile-scoped BrowserHost through loopback CDP. Different profile directories are never merged, and service processes never open the same user-data directory independently.

Gemini remains outside BrowserHost pooling because its Google-account bootstrap intentionally stays human-driven before automation attaches. For the recommended Context=Gemini / Code=ChatGPT topology, separate profiles remain the correct configuration.


## Tested dual-instance topology

The release regression target is intentionally asymmetric so independent routing is exercised:

```text
Context        -> gemini-context -> Gemini Web  -> http://127.0.0.1:3000
Principal/Code -> chatgpt-code   -> ChatGPT Web -> http://127.0.0.1:3001
```

The Agent test validates that both roles persist through the existing model router with distinct `base_url` values. The reverse-proxy suite validates that the Gemini and ChatGPT presets resolve to different canonical plugins and can coexist as separate instance records.


## TUI interaction hardening in Agent CLI 0.72.3

- The Reverse Proxy tab is explicitly included in the contextual-panel visibility map; setting `active_overlay=reverse_proxy` therefore always paints the modal.
- Pointer press no longer changes a virtualized selection before release, so a normal click cannot move its own semantic target during rerender.
- Wheel routing advances virtualized selections and retained plain-text windows keep their requested vertical scroll across renders.
- Modal strings are ANSI-sanitized before prompt_toolkit rendering; terminal escape sequences are not part of the retained text contract.


### Pointer feedback

Action labels in Services, Profiles and Novo serviço expose a visible hover state. The hover transformation preserves the same character count, so the interaction map keeps identical coordinates before and after repaint. Clicking the hovered action reaches the same controller path as its keyboard shortcut.

## Resident control channel — Agent CLI 0.73.0 / Reverse Proxy 4.3

Agent CLI first sends control operations to the reverse-proxy loopback control server. When the server is not running, the client performs one bounded `kitt-reverse-proxy control ensure --json` bootstrap and retries the HTTP operation. If the installed proxy predates the resident channel or bootstrap fails, the existing machine-readable subprocess command remains the compatibility fallback.

The channel is only a lifecycle/control optimization. Model traffic continues to use the selected reverse-proxy service endpoint, and provider/profile/process ownership remains in `kitt-reverse-proxy`.

## Startup and TUI reliability — Agent CLI 0.76.1 / Reverse Proxy 4.4.2

Starting a browser-backed service is intentionally a long-running control operation. Agent CLI therefore uses a dedicated 330-second timeout for `service.start` and `service.restart`; listing, profile management and other control requests keep the short management timeout. The operation still runs through the UI blocking executor, so the retained TUI can repaint while Chromium starts or waits for a human login.

Reverse Proxy 4.4.2 uses the same 330-second readiness budget before declaring a managed service unhealthy. For model traffic, the first useful WebChat delta now honors the configured UI response timeout instead of failing at an unrelated 90-second ceiling.

The retained transcript also anchors its cursor to the manual scroll row while follow-tail is disabled, restoring downward mouse-wheel scrolling after the user has scrolled upward. The K.I.T.T. scanner animation advances whenever animations are enabled rather than only during active model work.

## Agentic response reliability — Agent CLI 0.77.0 / Reverse Proxy 4.5.0

Broad mutation requests now enter discovery-first execution: the Agent preserves the complete goal but constrains the first model action to one repository inspection before optional architecture planning or mutation. The reverse proxy mirrors that state in its agent contract and rejects non-exploration actions until the read-only round trip is observed.

WebChat response liveness is activity-aware. Streaming indicators, response-text changes and DOM mutations refresh the inactivity budget, while a separate bounded absolute ceiling prevents an infinite browser wait. Official provider APIs continue to use their native token streams and are preferred when configured.

## 0.74 control-path note

The resident reverse-proxy control channel introduced in 0.73 remains the management fast path. Agent 0.74 does not alter that wire contract; the new performance work is inside token accounting, approval persistence and transcript rendering.

