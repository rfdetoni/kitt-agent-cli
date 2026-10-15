# Agent CLI v0.87.29 — local multilingual semantic stage (opt-in)

## Changes
- Host-side anchored intent detection for explicit PT-BR, EN and ES requests, before auxiliary generative classification. Optional Ollama E5-small Q8 embeddings run locally and abstain conservatively on ambiguous instructions.
- Preserve original instructions verbatim for every execution path. Reject an LLM-compiled task when structural fidelity contradicts the user's read-only request; the planner fails closed even if directly invoked with a rejected task.
- Explicit `--prepare-semantic-model` / `-PrepareSemanticModel` installer option; Ollama remains external and user-owned. Preparation verifies the complete model digest. Normal installation and operation never pull weights.
- Optional TUI `/semantic status`, `doctor`, `test` commands and Control Center mode. The semantic mode controls auxiliary classification, not LLM execution or global offline/privacy policies.
- Python package/runtime version 0.87.29, synchronized `uv.lock` editable version, release checks validate the lock using the official `uv lock --check` command.

## Tests and limitations
- CI Python 3.14: full regression suite, release-critical contracts, cleanroom, lint, Windows package smoke, Docker/Podman E2E; separate CPU Ollama E5 integration verifies embeddings and digest.
- Initial real E5 CPU sample used 384-dimensional vectors; 24/24 explicit requests passed via rules, **not via semantic-vector classification**. This is not a production holdout; it does not establish a 100% probability, statistical guarantee, or total token savings.
- Actual tokens per completed task, cross-language global code discovery, skills and memory semantic retrieval, preparation time across platforms, and an independent intent holdout are not benchmarked in this release. Therefore semantic mode defaults to off; operator opt-in is required.
- No cross-repository protocol changes, schema migrations or required runtime dependencies. Root release manifest must pin the final Agent main SHA after merge.

See [CODE_RAG](CODE_RAG.md) for operating instructions, digest, and measured data.
