# TUI Accessibility

KITT treats keyboard operation and semantic text as the baseline. Mouse and color are enhancements, not requirements.

## Supported modes

### Keyboard-first

Every user action exposed by the retained TUI must remain reachable through keyboard navigation or a command. `Ctrl+P` is the canonical discovery path. Mouse handlers call the same semantic controller actions as keyboard bindings.

### No color

Set:

```bash
NO_COLOR=1 kitt
```

KITT removes prompt-toolkit color styles and formatter ANSI colors. `TERM=dumb` also disables retained color styling.

Status, errors, approvals and progress must remain understandable from words/symbols without depending only on color.

### High contrast

Set:

```bash
KITT_HIGH_CONTRAST=1 kitt
```

The high-contrast palette uses black surfaces, white primary text, explicit bright borders and text-first state labels. `NO_COLOR` takes precedence if both variables are set.

## Mouse

Mouse reporting is enabled by default because the TUI supports local-panel scrolling and clickable semantic actions. Use `F10` or `/mouse` to turn application mouse reporting off, including when native terminal text selection is preferred.

No command may become mouse-only.

## Screen readers and linear terminals

The retained full-screen TUI cannot guarantee identical behavior across every terminal/screen-reader pair. For constrained environments:

- prefer keyboard navigation;
- use `NO_COLOR=1`;
- use a terminal configuration that exposes text content to the accessibility stack;
- use plain CLI commands such as `kitt doctor`, `kitt models`, `kitt sessions --json` and incident JSON output for machine/linear consumption.

When adding a TUI feature, keep its state expressible as text and avoid meaning encoded only through position, animation or color.

## Contributor checklist

- action has a keyboard path;
- visible state has a textual label;
- focus does not disappear after activation;
- ANSI bytes are sanitized before retained text rendering;
- `NO_COLOR` still produces meaningful output;
- high-contrast mode preserves selection/focus visibility;
- mouse hit targets reuse semantic controller actions;
- scrolling is local to the focused/pointed surface.
