# Agent CLI v0.86.5 — Virtualized mouse-wheel regression

## Scope

The `kitt.ui.scroll.make_index_wheel_handler` used by command palette, model selector, provider selector, Conversations, Timeline, Diff and Reverse Proxy advanced the selection and then also incremented `Window.vertical_scroll`. Those panels already update their visible list (or diff text) based on the selection/scroll model; the additional offset made mouse wheel navigation jump and sometimes positioned items outside of the visible panel.

## Fix

The wheel handler updates the model exactly once and invalidates the UI. The unused `get_window` and `min_scroll` arguments are removed from its seven callers. Plain text panels and the transcript still use their separate `make_wheel_scroll_handler` with ordinary renderer scrolling.

Regression: a behavioral test drives the real registered session picker `FormattedTextControl.mouse_handler` and verifies one selection step without changing the underlying Window offset. No new dependencies, no reverse-proxy or protocol changes.

## Logs reviewed

The submitted `agent-cli(20261009-183229).log` records daemon processing, not `tui.mouse.wheel` from the interactive client; it cannot prove whether mouse input reaches the UI. The accompanying reverse-proxy file records provider latency and memory pressure, which are separate from the local terminal UI issue.

The Angular `TS2582` errors in the logs reflect the requested project's test-runner typings rather than an Agent CLI mouse bug.

The actual local terminal capture/scroll user experience still requires user validation after installation.
