# Agent CLI 0.88.14 — Windows Oracle SSH diagnostics

- Known SSH exit failures are categorized without returning sensitive SSH stderr.
- One retry is permitted **only** for side-effect-free `probe.info` after transient SSH connection failures. There is no replay for `probe.run`.
- Windows first-boot OEM records `C:\\KittOracle\\bootstrap.status` as `PROVISIONING`, `FAILED` (when reachable on installer failure), or `READY`.
- Tests exercise failure categories, transient retry, nonretryable authentication failures and doctor reporting.
- `uv.lock` and project metadata are aligned at 0.88.14. No dependency was changed; the immutable kitt-protocol SHA remains `ac9baf8b4b8f03e839471c7bbb7d5e52e970a9ec`.

Real Windows 11/Dockur E2E requires the operator's KVM host and is not represented by the CI mocks.
