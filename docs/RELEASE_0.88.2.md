# Agent CLI 0.88.2 — remembered approvals resume without a loop

Saving “always allow” for a session or workspace no longer invalidates the waiting action's authority snapshot. Only explicit, action-bound approval resumes accept appended allow rules, with the entire original rule prefix preserved. Revocations, deny additions, policy/autonomy/sandbox changes, principal changes, grant mismatches and nonce replays still fail closed. Other snapshot consumers remain strict.

Authority validation failures retire the pending action and revoke its unused grant. The TUI removes an acknowledged decision even if execution failed, showing the execution error instead of reopening an ungrantable permission modal. Assistant Runtime 0.4.2 supplies that acknowledgment.

Validation: the original main reproduces the stale approval revision for both remembered scopes; the corrected regressions execute the real approved command exactly once, including database restoration, and retain denial/revocation guards. TUI tests cover the terminal execution failure. Daemon/Goal integration is exercised in Assistant's runtime suite.
