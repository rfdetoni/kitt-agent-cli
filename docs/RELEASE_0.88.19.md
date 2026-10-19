# Agent CLI 0.88.19 — native function calling and read batching

- Agent-loop messages explain the existing WebChat JSON tool envelope and allow exactly one provider-native function call where the proxy offers one.
- Prefer the existing `repo.batch_read` host operation for up to eight independent reads when authorized; each call retains `id`, `operation`, and `arguments`.
- Do not infer executable tool actions from prose, bypass host permissions, or enable more than one host action per model turn.
- CI confirms packaging/regression checks; measured provider-side latency reduction requires a real controlled integration benchmark.
