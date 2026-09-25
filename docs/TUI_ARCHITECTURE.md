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
| `kitt/ui/mouse.py` | Mouse event routing and semantic action activation |\n| `kitt/ui/interaction.py` | Local-cell hit regions, hover/press state and hit testing |
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
| `kitt/ui/reverse_proxy_panel.py` | Reverse-proxy control-center projection and user actions |\n| `kitt/reverse_proxy/client.py` | Typed subprocess boundary for reverse-proxy control plane |

No extracted module introduces another application owner. `ModelSetupModel` is a compatibility facade composed from small selection behaviors; each behavior has one reason to change.

## Scroll invariant

Every potentially scrollable surface is registered in `ui.scrollable_windows`. A wheel event is bound to the window under the pointer and cannot mutate the vertical scroll of another registered window. Virtualized selectors advance their selected index through the same router.

Plain `FormattedTextControl` surfaces receive a synthetic cursor anchored to the requested scroll row. This prevents prompt_toolkit's cursor-visibility algorithm from resetting `Window.vertical_scroll` to row zero on the next render. The prompt still keeps its own buffer cursor and the transcript keeps its tail-follow cursor; neither is overwritten.

The prompt no longer delegates wheel events to the transcript. This is enforced by `tests/test_tui_scroll_routing.py`.

## Mouse policy

Application mouse support is enabled by default. This makes hover-local wheel navigation work immediately. `F10` or `/mouse` disables it when native terminal text selection is desired. The status bar exposes the current mouse state permanently.

### OpenTUI-inspired interaction model

Agent CLI 0.72 adopts the interaction principles that fit the existing retained prompt_toolkit architecture without porting OpenTUI's renderer, Zig runtime or flexbox layer. Agent CLI 0.72.3 additionally requires actionable text controls to expose hover feedback without changing their cell geometry.

- `InteractionMap` is a lightweight local-cell hit grid rebuilt by the render function for each interactive surface.
- Visible rows and buttons register semantic action ids rather than embedding business logic in mouse handlers.
- Hover updates the same selected-index state used by keyboard navigation.
- Mouse down records the pressed target **without changing selection**; this keeps virtualized geometry stable until mouse up.
- Mouse up previews and activates only when it resolves to the same semantic target, avoiding both accidental drag-release activation and click loss caused by rerendered lists.
- Clicking a surface restores focus to that surface before activation.
- Action activation calls the same controller methods used by keybindings, keeping keyboard and mouse behavior equivalent.
- The interaction registry stores only visible hit regions, so memory and dispatch cost remain bounded by the rendered viewport.

This design is inspired by OpenTUI's rendered-cell hit testing, one-focused-renderable model and mouse/keyboard parity, but is independently implemented in Python and adds no OpenTUI dependency.

## Modal architecture

The physical layout uses exactly five retained `Float` surfaces rather than one Float per task:

1. approval/security modal;
2. command palette;
3. model/provider wizard;
4. contextual panel;
5. toast/notice.

Prompt completion remains one of the five implementation Floats. Responsive sidebar and notices are inline retained containers, so they add no modal surface.

The model wizard keeps model selection, provider selection/addition, endpoint setup and authentication inside one retained modal surface. The contextual panel hosts conversation picker, timeline, diff, agents, autonomy, KITT Reverse Proxy and help; Left/Right switches tabs without allocating a new overlay frame.

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


## Retained-text hygiene — Agent CLI 0.72.2

Modal projections that return plain strings are sanitized before they enter prompt_toolkit. Raw CSI/ANSI escape sequences are never used as styling inside retained controls; prompt_toolkit style classes remain the renderer-owned styling mechanism. This prevents artifacts such as `^[`, `^[[0m` and other escape bytes from appearing as modal content.

The Reverse Proxy context tab is part of the same retained contextual Float. `/reverse-proxy` opens that surface first, displays a loading state, and only then performs the control-plane snapshot refresh so subprocess latency cannot make the command appear inert.


## Hover geometry invariant — Agent CLI 0.72.3

Hover feedback must never change the start column or visible width of a registered hit region. Reverse Proxy text actions therefore use same-length label transformations for hover state. This keeps the semantic target stable across repaint and preserves the press/release invariant.
