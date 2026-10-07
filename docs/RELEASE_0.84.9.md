# kitt-agent-cli 0.84.9 — Cancellation, selected inputs and build verification

Automatic contracts register the planning turn for cancellation and refuse work after cancellation. Selected text files and attachment references survive planning and durable execution. Full Node verification includes check/build scripts and directory targets, and reports whether workspace verification actually ran. The native bridge invalidates changed files and validates symbol paths before scoped reads. Tool argument limits come from kitt-protocol.

## Verification

Regression checks cover the concrete bugs fixed by this release. Native changes are validated with Rust formatting, Clippy, workspace tests and a Python 3.14 wheel integration. Live provider accounts and STT model inference are not part of these local checks.
