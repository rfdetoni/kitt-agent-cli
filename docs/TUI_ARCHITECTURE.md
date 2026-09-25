# KITT TUI Architecture

## Purpose

The TUI is a retained, low-overhead presentation layer for the KITT control plane. It does not own execution policy, repository intelligence, context selection, provider routing or turn persistence. Those contracts remain in `KittRuntime`, `TurnEventBridge` and their existing services.

The refactor in Agent CLI 0.70.0 replaces the former monolithic UI implementation with single-responsibility modules while preserving internal KITT ecosystem contracts.

## Responsibility map

| Module | Responsibility |
| --- | --- |
| `kitt/ui/app.py` | Lifecycle/orchestration, state ownership and thin compatibility wrappers |
| `kitt/ui/controls.py` | Stable Buffer/Control construction and completion wiring |
| `kitt/ui/layout.py` | Retained container tree and five logical modal surfaces |
| `kitt/ui/scroll.py` | Central scroll registry and isolated wheel routing |
| `kitt/ui/mouse.py` | Panel-specific hover/click behavior only |
| `kitt/ui/keymap.py` | Shortcut metadata/source of truth for discoverable actions |
| `kitt/ui/keybindings.py` | prompt_toolkit binding installation and contextual bindings |
| `kitt/ui/render/core.py` | Home, header, transcript, sidebar, status and toast projections |
| `kitt/ui/render/overlays.py` | Modal/context projection functions |
| `kitt/ui/command_dispatcher.py` | Slash-command dispatch |
| `kitt/ui/model_service.py` | Model/profile application and discovery |
| `kitt/ui/model_picker_state.py` | Model/role selection state and model badges |
| `kitt/ui/provider_picker_state.py` | Provider protocol-template selection |
| `kitt/ui/provider_catalog_state.py` | Provider ordering, favorites and custom-provider CRUD |
| `kitt/ui/provider_popup_state.py` | Provider-popup navigation and mouse row mapping |
| `kitt/ui/provider_flow.py` | Provider setup/authentication workflow |
| `kitt/ui/navigation.py` | Palette/sidebar/context navigation actions |
| `kitt/ui/runtime_actions.py` | UI-facing runtime/tool/workspace/approval actions |

No extracted module introduces another application owner. `ModelSetupModel` is a compatibility facade composed from small selection behaviors; each behavior has one reason to change.

## Scroll invariant

Every potentially scrollable surface is registered in `ui.scrollable_windows`. A wheel event is bound to the window under the pointer and cannot mutate the vertical scroll of another registered window. Virtualized model/provider selectors use the same routing contract while also advancing their selected index.

The prompt no longer delegates wheel events to the transcript. This is enforced by `tests/test_tui_scroll_routing.py`.

## Mouse policy

Application mouse support is enabled by default. This makes hover-local wheel navigation work immediately. `F10` or `/mouse` disables it when native terminal text selection is desired. The status bar exposes the current mouse state permanently.

## Modal architecture

The physical layout uses exactly five retained `Float` surfaces rather than one Float per task:

1. approval/security modal;
2. command palette;
3. model/provider wizard;
4. contextual panel;
5. toast/notice.

Prompt completion remains one of the five implementation Floats. Responsive sidebar and notices are inline retained containers, so they add no modal surface.

The model wizard keeps model selection, provider selection/addition, endpoint setup and authentication inside one retained modal surface. The contextual panel hosts conversation picker, timeline, diff, agents, autonomy and help; Left/Right switches tabs without allocating a new overlay frame.

## Keyboard discovery

`Ctrl+P` is the main discovery mechanism. Direct `Ctrl+X` chords are restricted to the frequent actions New Conversation, Sidebar and Agents. F4, F10 and F12 remain dedicated mode/mouse/model controls. Command Palette labels are derived from `KeyMap` when a command has a direct shortcut.

Context-specific keys are captured into the same runtime key map so active bindings cannot become invisible to introspection, while only intentional user-facing shortcuts are shown as global help.

## Performance invariants

- Controls and Windows are constructed once and retained.
- Rendering is functional over existing state; invalidation never rebuilds controls.
- Context-tab switching mutates the active overlay frame in place instead of pushing/popping a new frame.
- Scroll dispatch is O(1) to the target control; it does not scan panels.
- No animation task is created when animation is disabled or the terminal is dumb; idle/home rendering does not continuously invalidate the screen.
- Blocking work stays on the existing bounded UI executor rather than the render/event loop.
- TUI construction performs no reverse-proxy network probe; provider availability is resolved lazily on use.
- The bounded UI executor uses two workers, sufficient for interactive blocking calls without reserving a wider pool.
- The refactor adds no runtime dependency.

## Compatibility

The TUI preserves method-level contracts consumed by KITT runtime/bridge/command modules through thin `KittUIApp` wrappers. No protocol, daemon, native, assistant-runtime, memory or reverse-proxy interface changed in 0.70.0, so sibling KITT repositories do not require a compatibility bump for this UI-only change.


## Architectural regression guards

`tests/test_tui_architecture_boundaries.py` makes the structural limits executable:

- `KittUIApp` stays at or below 500 source lines;
- `layout.py` stays at or below five physical `Float` surfaces;
- global `Ctrl+X` chords stay capped at three;
- model/provider selection behaviors stay below 200 lines each.

These are guardrails, not style metrics: crossing one means a new responsibility should be extracted rather than appended to an existing owner.
