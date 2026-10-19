# Agent CLI 0.88.17 — capability-first tool discovery

Evidence: the operator's 2026-10-19 Agent CLI and reverse-proxy logs showed
repeated Git/Docker/KITT status/help calls, a 16-call budget exhaustion, context
searches for `Prefer`/`validation_prompt` instead of Wine-Staging source
symbols, and multi-turn web UI latency dominating the task. This release fixes
the host-owned causes, not ChatGPT Web processing speed.

- The agent-loop reverse-proxy prompt uses one JSON `use_tool` envelope
  consistent with its external output contract. Full authorized schemas
  remain in the host tool registry; XML `<kitt-tool>` examples are omitted
  on that route, without changing legacy tool interfaces.
- A small, deterministic host capability snapshot announces the RE Lab's
  networkless Linux toolbox, shared workspace, fixed Windows Oracle, and
  operator-only host source staging. A snapshot is advisory and grants no
  network permissions or command execution.
- Retrieval strips scaffolded original-request markers and subsequent KITT
  contract/checklists, and ignores orchestration field names for repository
  symbol ranking.
- Tool-loop metrics distinguish repeated environment discovery, with an
  explicit checkpoint after four consecutive discovery calls. Normal
  coding/test operations and safety policy remain unchanged.
- `kitt re lab stage-wine --approve` stages only the official Wine-Staging
  Git repository on the networked operator host into the existing Linux
  bind mount. Requires an interactive terminal; never modifies Windows or
  grants the guest network access, and refuses overwrite.
- Synchronized `pyproject.toml`, `uv.lock`, namespace fallback and docs
  to 0.88.17; Protocol SHA pin stays unchanged.

Regression/CI validation covers the capabilities contract, retrieval filter,
CLI and source staging fail-closed behavior. This change does not claim
automated guest coding, direct guest GitHub pushes or a completed Wine DLL
stub inventory; those require an explicit authorized execution backend and
separate end-to-end testing.
