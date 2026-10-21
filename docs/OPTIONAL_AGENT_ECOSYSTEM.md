# Optional agent-ecosystem integrations

KITT already provides scoped durable memory, model routing, MCP, hooks and
plugins. These integrations reuse that machinery instead of copying third-party
implementations. Nothing is installed or sent to the network by `kitt setup
recommend`.

## Project setup advisor (Claude Code Setup-inspired)

```bash
kitt setup recommend --root /path/to/project
kitt setup recommend --json --root /path/to/project
```

The analyzer reads only bounded, known project manifests in the root. It does
not read secrets, walk source trees, run untrusted scripts or modify the
workspace. Recommendations are suggestions, not auto-installed packages.
The existing KITT project-intel, test-impact, browser, container, quality and
MCP features remain the executors.

## FreeLLMAPI — optional provider gateway

FreeLLMAPI already implements provider health, quota tracking, model ranking
and failover. Do **not** duplicate those functions in KITT's proxy. Deploy
FreeLLMAPI separately, for example through the upstream Docker Compose setup
at https://github.com/tashfeenahmed/freellmapi. Obtain its unified token from
its local dashboard. This is experimental/free-tier infrastructure, not a
promise of guaranteed capacity or a replacement for KITT's authorized WebChat
bridge.

```bash
export FREELLMAPI_API_KEY='<your-unified-key>'
kitt integrations doctor
kitt integrations configure-freellmapi --model auto --activate --root /path/to/project
```

The command registers a new profile via KITT's existing
`openai-compatible` protocol. It trusts **only** the explicitly configured
gateway origin in KITT's user-owned endpoint trust store, then writes a
workspace model profile containing only `env:FREELLMAPI_API_KEY`, **never the
key itself**. Only `code-generation` and `code-edit` routes change with
`--activate`; summary/context routes keep their existing models.

Default address: `http://127.0.0.1:3001`. A remote origin requires both
`--allow-remote` and HTTPS. The external provider may change its model
catalog independently; choose an alias/model supported by your deployment.

**Important:** this is an alternative inference backend; do not put it in
front of a browser-backed KITT proxy and assume that web subscriptions behave
like metered API credentials. Model/tool capabilities and rate limits depend
on the real upstream provider.

## Strix — install, don't rewrite

Install the official Strix CLI separately following
https://github.com/usestrix/strix#quick-start. Example using an isolated
Python application installer:

```bash
uv tool install strix-agent
kitt security strix status
kitt security strix scan --target . --authorized --root /path/to/disposable-worktree
```

`--authorized` confirms explicit authorization for this **specific** target.
No scan starts in `status` or `setup recommend`. Filesystem targets must be
inside the chosen workspace; URL targets must have an http(s) scheme and
cannot contain embedded credentials. The process receives an argv array (not
a shell) and has a bounded execution time. Actual pentest isolation and network
scope are supplied by the external Strix/Docker setup: use a disposable
worktree/container and restrict its network to owned, authorized systems.
Do not aim tests at third-party hosts without explicit written permission.
Strix may need Docker, LLM credentials, and a sandbox image.

## Claude-Mem — rely on native memory

Do not run a second persistent memory authority by default. `kitt-memoryd`
already supports FTS5, temporal validity, provenance, progressive
search/timeline/get, bounded context and workspace scoping:

```bash
kitt memory doctor
```

Claude-Mem remains a third-party option **only** when the user separately uses
another compatible coding harness. Never synchronize raw transcripts or
secrets to another memory service silently.

## Validation and operation

```bash
python -m pytest -q tests/test_optional_agent_ecosystem.py
python -m compileall -q kitt
kitt setup recommend --root .
kitt security strix status
kitt integrations doctor
```

The `integrations doctor` command probes only localhost `127.0.0.1:3001`
for the FreeLLMAPI-compatible models endpoint and checks whether the Strix CLI
is on PATH. It does not install tools. The memory doctor may start the existing
local KITT memory daemon on demand.

Track outcomes before expanding the feature set: percentage of sessions
resumed with relevant evidence, context tokens per task, needless tool calls,
provider retry/failover success, security findings reproduced, and runtime
startup/memory cost.
