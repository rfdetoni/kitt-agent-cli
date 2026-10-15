# Agent CLI v0.87.24 — per-agent configurable model routing

## New functionality
- AgentLLMPolicy data in trusted TOML `[agents.<id>.llm]` with strict validation (mode, configured primary/fallbacks, reasoning, output/temperature, tool capability and egress restrictions).
- Workspace overrides in `.kitt-agent-models.json`, validated/atomic and shared across CLI and TUI Agent Manager; interactive agent creator now asks for an LLM policy.
- Role-aware automatic ranking uses already-configured TaskRouter profiles; preferred mode uses explicit ordered candidates; fixed mode cannot fail over.
- ChildSpawn model policy resolved by host at admission; persisted as a sanitized, immutable child lineage snapshot, independent of git worktree config. No inline API keys are persisted.
- Each worker applies its own selected ModelProfile to execution-model routing, and ProviderCircuitPool restricts fallback to the allowed profiles. Reverse-proxy retains child-specific browser session namespaces; no new transport.
- Privacy revalidated when worker starts; offline/local_only rejects remote primary/fallback, and `remote_egress=deny` blocks remote candidates.
- Version metadata, uv.lock, docs and release-critical test suite updated.

## Examples

```bash
kitt agents model critic --mode fixed --primary review-proxy
kitt agents model creative --mode preferred --primary creative-proxy --fallbacks execute --reasoning high
kitt agents model investigator --mode auto --local-only
```

`/agent model code auto` and `/agent model critic preferred review-proxy review-direct,execute` are available from the agent command surface. Configure the profiles themselves in the existing Model Manager or `.kitt-router.json`; don't place secrets in either routing file.

## Test

```bash
python scripts/run_release_critical.py
python -m pytest -q tests/test_agent_model_router.py tests/test_specialist_agents.py
```
