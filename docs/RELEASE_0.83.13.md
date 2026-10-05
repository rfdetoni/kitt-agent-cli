# K.I.T.T. Agent CLI 0.83.13

Released: 2026-10-04

## Scope

This patch closes the remaining locally-testable agentic acceptance gaps with focused runtime changes and direct regression evidence. It does not introduce a new protocol version, scheduler, storage layer or authority.

## Runtime changes

- Require explicit approval for opaque inline interpreter wrappers such as `python -c` and `node -e`, including under `/autonomy allow-all`; hard shell wrappers remain denied.
- Preserve explicit command, stderr, exit-code/returncode and authority-denial evidence during context compaction.
- Stop deterministic no-progress loops when the agent repeats the same unchanged observation three times or alternates unchanged observations A/B/A/B.
- Reset the observation detector when output changes or a non-observational action occurs, so new evidence is not treated as stuck progress.
- Keep the existing bounded no-action completion recovery path explicit.

## Acceptance evidence

Focused regressions cover:

- managed-process stdin/signal control against the original `AuthoritySnapshot`;
- real Git worktree isolation, integration, discard and cancelled-worktree preservation;
- compaction preservation of error, exit code, command, path, constraint and validation failure;
- same-failure, alternating-failure, unchanged reread and monologue/no-action bounds;
- shell/interpreter wrapping resistance;
- `allow-all` as approval UX only, without capability/path/control-plane escalation;
- `kitt learn` signature privacy;
- explicit no-auto-promotion for control/candidate experiments.

The distinct experimental reread-detector acceptance row is intentionally folded into the host-owned no-progress detector rather than retaining two overlapping runtime heuristics.

## External evidence

Real Docker and Podman lifecycle/replacement checks are executed in PR CI. A real provider ContextEnvelope round trip, cross-provider secret observability and authenticated WebChat reconnect remain environment-dependent gates and must not be promoted without executing those environments.

## Tests

- `tests/test_agentic_remaining_acceptance.py`
- `tests/test_autonomy_policy.py`
- `tests/test_learn_privacy_acceptance.py`

No KITT Protocol schema or Reverse Proxy contract change is required.
