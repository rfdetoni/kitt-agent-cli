# Agent CLI 0.87.17 — targeted contract review corrections

## Incident and cause

The 2026-10-14 Windows API Observatory run generated three distinct
task plans, followed by three high-risk reviews. All reviews returned
`REVISE`; no task tool calls were dispatched. The three reviewer
responses exposed different legitimate problems. The former retry
logic repeated the full original decomposition, then appended only a
truncated excerpt of the rejected response. This encouraged plans to
drift between attempts, losing previously acceptable work.

## Changes

- When the host has already validated the task plan structure, a
  `REVISE` review now yields a **targeted revision** request containing
  the complete validated plan and the concrete reviewer findings.
- The prompt asks to retain task IDs, order, acceptance criteria,
  dependencies and task boundaries unless a specific reviewer issue
  requires changes. The model must return the whole updated plan for
  **fresh host validation** and another independent risk review.
- If a revision is syntactically invalid, keep outstanding review
  findings in the next correction prompt rather than dropping them.
- If the initial plan itself is invalid, preserve the original
  schema-correction workflow. Retain the three-attempt cap, host-owned
  permissions, independent high-risk review and clear TUI phases.

A targeted retry improves continuity but cannot guarantee acceptance:
three unresolved review passes still terminate without executing any
task. This is intentional safety behavior rather than permission bypass.

## Validation

Regression tests cover host-validated revision baselines, preservation
of outstanding findings on malformed revisions, and the existing
daemon-to-TUI event presentation with no false tool activity.
The critical release suite also exercises the Goal and contract paths.

The user's actual Windows oracle, Wine guests, GPU, on-host TUI and
observatory workspace have **not** been accessed by CI. Only the user
host can validate a real Windows/Wine differential probe.
