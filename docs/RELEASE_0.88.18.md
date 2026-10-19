# Agent CLI 0.88.18 — tool-loop feedback and scout refactor

- Move pure host-evidence, environment-discovery, identity and browser-trace helpers to `kitt/core/turn_tool_helpers.py`. Retain import aliases in `turn_tool_loop.py` for current consumers.
- Replace the repeated completion exhortation attached to **every** host tool result with one initial reminder and a concise discovery-stall checkpoint. This reduces repeated prompt text without hiding tool output or policy failures.
- Fail closed for a blank or bare empty-object response in `agent-loop` instead of representing it as successful task completion.
- Keep host approval, execution budgets, verification, and snapshot-based state unchanged. Do not cache mutable filesystem reads without invalidation.
- Validation: `python -m compileall -q kitt/core/turn_tool_loop.py kitt/core/turn_tool_helpers.py`; `python -m pytest -q tests/test_turn_processor_decomposition.py tests/test_completion_contract.py tests/test_goal_contract.py`. Browser/provider benchmarks must be repeated on an authenticated local instance before claims about speedups.
