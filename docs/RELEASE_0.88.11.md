# Agent CLI 0.88.11 — Protocol 0.14.2 and changelog hygiene

- Pin Protocol 0.14.2 at an immutable commit and align Agent CLI metadata (pyproject, uv.lock, source-tree fallback).
- Preserve the existing offline Clean-Room implementation and signed handoff boundary from 0.88.10; no new execution privileges or wire changes.
- Move historical release announcements out of README into a consolidated CHANGELOG.md.
- Respect immutable release tags: metadata changes require a new version; documentation-only updates do not.
- Validation remains subject to full Agent CI. Independent Windows Oracle comparison for newly generated Wine code remains **UNVERIFIED**.
