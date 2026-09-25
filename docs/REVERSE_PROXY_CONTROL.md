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

A named profile can accumulate login state for multiple providers, but Reverse Proxy 4.2 locks a Chromium user-data directory to one active proxy instance at a time. Use distinct profiles for concurrently active Gemini and ChatGPT instances. A later BrowserHost/CDP layer can safely share one live Chromium owner without changing Agent role bindings.


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
