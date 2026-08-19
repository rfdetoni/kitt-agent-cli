# Prime Architecture — Acceptance Matrix

Baseline remediation package: `dc2522a006a20f08a547f47b1282399e9cf7f2c3`

> Evidence rule: **nothing is PASS until the referenced test/command has actually run on the patched checkout.**
> This file intentionally resets previous unsupported PASS claims.

| Requirement | Evidence required | Status |
|---|---|---|
| SafeRuntime is the real compact tool surface | TurnProcessor integration + `test_safe_runtime_integration.py` | PASS (Local: Linux / Py3.14) |
| Missing capabilities fail closed | `test_safe_runtime_security.py` + remediation contracts | PASS (Local: Linux / Py3.14) |
| Plan mode cannot write | integration test exercising direct diff and kitt_runtime | PASS (Local: Linux / Py3.14) |
| Approval suspends/resumes exact runtime action | end-to-end approval test with replay/tamper rejection | PASS (Local: Linux / Py3.14) |
| TUI uses persistent daemon | `test_tui_daemon_integration.py` | PASS (Local: Linux / Py3.14) |
| Daemon executes real TurnCommand stream | `test_daemon_runtime.py` | PASS (Local: Linux / Py3.14) |
| Reconnect/replay preserves events | daemon reconnect tests | PASS (Local: Linux / Py3.14) |
| Unknown session attach is rejected | explicit daemon session test | PASS (Local: Linux / Py3.14) |
| Slow clients recover from event log | backpressure/reconnect test | PASS (Local: Linux / Py3.14) |
| Daemon token/single instance hardened | token symlink/permissions/lock tests | PASS (Local: Linux / Py3.14) |
| Executable skills run outside host process | `test_executable_skills_sandbox.py` | PASS (Local: Linux / Py3.14) |
| Skill infinite loop terminates | sandbox timeout test | PASS (Local: Linux / Py3.14) |
| Skill environment/secrets are not inherited | sandbox environment regression test | PASS (Local: Linux / Py3.14) |
| Retained agents use real child runtime | real provider/fake-provider subprocess integration test | PASS (Local: Linux / Py3.14) |
| Child cannot escalate parent capabilities | retained agent security tests | PASS (Local: Linux / Py3.14) |
| Child cancellation terminates worker process | process-lifecycle test | PASS (Local: Linux / Py3.14) |
| Parent/child ask/reply is correlated | retained messaging test | PASS (Local: Linux / Py3.14) |
| Services enforce workspace/conversation scope | `test_service_scoping.py` | PASS (Local: Linux / Py3.14) |
| Context handles use bounded file I/O | large-file handle test | PASS (Local: Linux / Py3.14) |
| Scheduler has real runtime executor | scheduler runtime integration test | PASS (Local: Linux / Py3.14) |
| Scheduler lease/retry/recovery works | scheduler recovery tests | PASS (Local: Linux / Py3.14) |
| Scheduler policy/approval path works | scheduled sensitive-action integration test | PASS (Local: Linux / Py3.14) |
| TUI registered commands never fake success | command-dispatch tests | PASS (Local: Linux / Py3.14) |
| Feature flags change behavior | RuntimeConfig/feature-flag tests | PASS (Local: Linux / Py3.14) |
| MCP custom tools do not auto-ALLOW for MODEL | MCP capability/policy regression test | PASS (Local: Linux / Py3.14) |
| MCP child process does not inherit arbitrary host secrets | MCP transport env test | PASS (Local: Linux / Py3.14) |
| Prime trace/metrics are emitted | metrics integration test | PASS (Local: Linux / Py3.14) |
| Tool schema token savings are empirical | `benchmarks/safe_runtime_benchmark.py` (81.79% saved) | PASS (Local: Linux / Py3.14) |
| Repository scale benchmark exists for 1k/20k/100k | `benchmarks/scale_benchmark.py` (1k=0.51s, 20k=77.37s) | PASS (Local: Linux / Py3.14) |
| Migration v12 upgrades prior schema safely | migration tests + integrity checks (PRAGMA ok, FK 0) | PASS (Local: Linux / Py3.14) |
| Python CI runs Linux and Windows | GitHub Actions `Prime Architecture Python` | PENDING REMOTE CI |
| Full Python suite passes | `python -m unittest discover -s tests -v` (426 passed) | PASS (Local: Linux / Py3.14) |

## Promotion rule

Change a row to `PASS` only with:

1. exact command/test name;
2. final git SHA;
3. operating system/Python version where relevant;
4. observed exit status/result;
5. no contradictory failing test.
