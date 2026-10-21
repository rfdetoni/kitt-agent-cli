# K.I.T.T. Agent CLI 0.88.37

- Use the existing read-only `flow.execute` graph for independent multi-step workspace queries; `program.execute` for bounded read-only repetition; `repo.batch_read` for grouped file reads. Code-navigation, memory and handle-resolution tools are surfaced without changing tool authority.
- Record empty `MEMORY_RECALL` separately from unavailable memory; preserve durable host reads when a Goal executes read-only flow/program operations.
- Prefer path-disjoint contracts and no artificial dependencies so existing bounded child parallelization can engage when eligible.
- Add host-owned `c.build` verification for C/C++ changes if a generated, workspace-contained Makefile exists; fail closed as UNAVAILABLE otherwise, rather than treating Wine native source as compiled based on structural checks. Verification requires the normal authorized process runner and can still be blocked by the local sandbox/VM environment.
- Regression gates: Agent PR Checks, Docker, Podman, Windows packaging, deterministic C-verification and persisted Goal evidence. Benchmark efficiency must be measured in actual representative runs; code visibility alone does not establish performance gains.
