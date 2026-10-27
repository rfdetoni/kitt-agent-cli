# Autonomous task worktrees

Each task starts from a clean Git checkout using
`autopilot.transaction.start`. A linked worktree at
`.autopilot-tasks/<random-id>` receives all `sandbox.run` commands and
`autopilot.verify` checks. The original checkout remains unmodified.

## Isolation

- Native task execution **requires Docker** and mounts only the task root;
  Bubblewrap's read-only host-root profile is not accepted for worktrees.
- The Compose broker validates the task identifier and mounts only that
  subdirectory into an isolated Docker worker. Parent workspace files and
  broker Docker socket are absent from the worker.
- Repository mutators, background processes, child-agent work and state writes
  are denied during the task; read-only inspection is still permitted.
- The linked Git metadata pointer is fingerprinted at task creation and
  verified before subsequent trusted operations.

## Delivery and recovery

After successful `autopilot.verify`, `autopilot.publish` may publish the
verified content to an operator-authorized GitHub branch, subject to network
permission, unchanged fingerprint, and non-forced remote update.

`autopilot.transaction.abandon` clears the active task without modifying the
original checkout. Task files remain available for inspection; a bounded
retention limit prevents unbounded accumulation.

There is **no automatic local promotion** of task files onto the parent
checkout in this release. Local merge and cleanup remain operator activities,
avoiding accidental overwrite of unrelated changes. A failed test or missing
strong Docker isolation blocks publication rather than claiming completion.
