# Agent CLI 0.88.1 — bounded planning with local validation

A valid automatic Goal plan now needs one model generation, followed by local validation of the existing size, ID, dependency, path, criterion and registered-check constraints. One structural correction is allowed when that validation fails; a second invalid result stops planning. Transport failures are not automatically replayed.

The mandatory high-risk model review and its recursive plan revision loop are removed. They repeated the objective and proposal before any execution evidence existed. Plan generation has no execution tools or capabilities, skips the advisory Architect call, receives the actual host check IDs, and defers repository discovery to its first executable task. The original objective and model-authored FINAL criteria remain intact. Post-execution deterministic checks, independent task/code validation, risk-aware review, approvals and final verification still run.

All structured goal reports now follow the active provider envelope: with Agent contract v5 the result object belongs in `final_response.content`. This avoids contradictory instructions to emit a bare application schema instead of the provider response. The UI reports the real local check phase instead of an extra reviewer call.

Alternatives considered: another model review adds a large round trip without execution evidence; accepting an unchecked plan loses structural guarantees; local validation plus one bounded correction keeps those guarantees with one normal planning call. No dependency or wire-version change.
