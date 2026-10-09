# Release 0.86.4 — Mouse and scroll rendering fix

This release addresses the ineffective wheel/click behavior observed after 0.86.3.

## Root cause and changes

- **Mouse coordinates:** `prompt_toolkit.Window` already converts terminal screen coordinates into UIControl content coordinates using its rendered row/column map. The additional `vertical_scroll` offset in `kitt/ui/mouse.py` was incorrect. Removed it so clicks in scrolled approval and Reverse Proxy panels select the rendered action.
- **Wrapped transcript:** `WindowRenderInfo.content_height` represents logical content lines, while `window_height` is screen rows. The previous calculation `content_height - window_height` reset scrolling to zero on wrapped output. Wheel events now start from the actual `render_info.vertical_scroll` position and allow `prompt_toolkit` to enforce the display bounds.
- **Incremental history:** Older transcript blocks are added only when the user reaches the top of the loaded history window, and the viewport is adjusted by the number of prepended logical lines, preserving the visible content.
- **Diagnostics:** At DEBUG level, received wheel events emit `tui.mouse.wheel surface=<name> direction=<event> scroll=<offset>` without logging prompt contents. This distinguishes terminal mouse reporting from renderer failures.
- **Mouse UX:** The toggle message now documents the actual mode: mouse capture enabled for wheel/hit testing, Shift+drag for native terminal selection where supported, or toggle capture off for native copy.

## Validation

- End-to-end SGR wheel event via `prompt_toolkit` application and a separate prompt buffer.
- A scrolled approval dialog hit-region test (no double scroll offset).
- A wrapped-content scroll test with fewer logical lines than visible screen rows.
- Existing TUI and security regression suite via GitHub Actions.

Version metadata: `pyproject.toml`, `uv.lock`, and the source-tree fallback `kitt/__init__.py` are now consistent at 0.86.4. No protocol or reverse-proxy contract changes.

The provided execution logs do not contain terminal mouse events: they cannot independently confirm the user's terminal sent wheel input. Use DEBUG `tui.mouse.wheel` lines to verify delivery in a future session.
