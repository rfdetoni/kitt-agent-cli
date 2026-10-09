# Agent CLI 0.86.3 — TUI approval and scroll reliability

## Failure modes

- `Always allow` stored a workspace approval, but `run_command` did not consult remembered rules. In particular, `node -e` and other inline interpreters remained `ASK` even after a matching human authorization.
- Approval clicks could leave a modal open until daemon RPC completion, and the generic close path cancelled outstanding requests.
- A daemon error-shaped acknowledgment could be mistaken for a successful approval.
- Mouse scroll was enabled only while a modal was visible. The workarea could not receive wheel events; keyboard arrows on a focused composer navigate prompt history.
- The Reverse Proxy context tab did not consistently restore focus or refresh its data.

## Implemented

1. Remembered process authorization is checked after hard-denial guards. Workspace/session and a nonempty exact executor identity must match. Existing unbound process rules cannot grant execution; read-only mode, forbidden executables, network capabilities and other mandatory gates remain enforced.
2. Approve/deny buttons dismiss their modal immediately **without** cancelling the decision. Duplicate click and keyboard submissions are ignored while an approval is in flight. Daemon errors or rejected responses keep the request pending and reopen the modal.
3. Enabled mouse support now routes wheel events to the hovered transcript/area rather than requiring an overlay; prompt history remains in the editor. Use Shift+drag for terminal-native selection or temporarily disable KITT mouse support when unmodified selection is needed.
4. Reverse Proxy tab changes load instances and restore keyboard focus. Mouse targets use rendered row offsets and reject stray mouse-up events.
5. Regressions cover modal-close safety, in-flight deduplication, failed/negative daemon responses, scoped remembered process approvals, scroll bounds and Reverse Proxy tab activation.

OpenTUI's interaction principles informed the approach; no Bun/Zig TUI dependency was introduced. The KITT Agent contract remains v4, and no proxy or protocol update is required.

## Verification

```sh
python -m pytest -q tests/test_tui_behavioral.py tests/test_autonomy_policy.py
```

CI also runs the existing release-critical suite. Version updated to 0.86.3 in `pyproject.toml` and `uv.lock`.
