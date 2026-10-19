# Agent CLI 0.88.18 — tool-loop feedback and scout refactor

- Move pure host-evidence, environment-discovery, identity and browser-trace helpers to `kitt/core/turn_tool_helpers.py`. Retain import aliases in `turn_tool_loop.py` for current consumers.
- Replace the repeated completion exhortation attached to **every** host tool result with one initial reminder and a concise discovery-stall checkpoint. This reduces repeated prompt text without hiding tool output or policy failures.
- Fail closed for a blank or bare empty-object response in `agent-loop` instead of representing it as successful task completion.
- Keep host approval, execution budgets, verification, and snapshot-based state unchanged. Do not cache mutable filesystem reads without invalidation.
- Validation: `python -m compileall -q kitt/core/turn_tool_loop.py kitt/core/turn_tool_helpers.py`; `python -m pytest -q tests/test_turn_processor_decomposition.py tests/test_completion_contract.py tests/test_goal_contract.py`. Browser/provider benchmarks must be repeated on an authenticated local instance before claims about speedups.

- The model-facing `kitt_runtime` catalog now advertises the existing `repo.batch_read` operation with its bounded read-only syntax. The capability snapshot prefers batch reads over repeated LLM round-trips; the host keeps its unchanged operation schema and per-child policy validation.

- Fix the effective operation catalog used to generate structural model tool schemas: enumerate the executing `SafeRuntime` extensions, not only base `core_runtime` operations. `repo.batch_read` had previously been implemented but could be silently absent from the Agent model's allowed enum. One focused existing-suite regression verifies the model/host boundary.
