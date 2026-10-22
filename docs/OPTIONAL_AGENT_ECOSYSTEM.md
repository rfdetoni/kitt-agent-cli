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
at https://github.com/tashfeenahmed/freellmapi. For reproducibility, the
version reviewed on 2026-10-21 was **v0.16.7**, commit
`ecdc9cbe27aa3db5e3d8935be13a5a17b9a513b1`:

```bash
git clone https://github.com/tashfeenahmed/freellmapi.git
git -C freellmapi checkout --detach ecdc9cbe27aa3db5e3d8935be13a5a17b9a513b1
# Follow that checkout's official Docker deployment instructions.
```

Obtain its unified token from
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
workspace model profile containing only a credential reference, **never the
key itself**. An existing `kitt auth login freellmapi` login is reused;
otherwise the profile references `env:FREELLMAPI_API_KEY`. Only
`code-generation` and `code-edit` routes change with `--activate`;
summary/context routes keep their existing models.
The profile leaves native JSON mode disabled because not every routed free
provider accepts `response_format`. KITT still requests and validates JSON
through its normal prompts and parsers.
New profiles use a 32768-token context window and an 8192-token output limit
to leave room for structured plans and reasoning. Adjust the profile for
smaller deployed models. The gateway owns provider failover; KITT bounds its
outer retries and does not automatically replay timed-out gateway requests.

Default address: `http://127.0.0.1:3001`. A remote origin requires both
`--allow-remote` and HTTPS. The external provider may change its model
catalog independently; choose an alias/model supported by your deployment.

The unified gateway token authenticates KITT to FreeLLMAPI. You must also
enable at least one upstream provider in the FreeLLMAPI dashboard. For
keyless free access, enable Kilo or OVH without entering an upstream key;
other providers require their own API keys. Free availability and rate limits
vary. Kilo's Auto Free may route to services that log prompts and outputs;
do not send personal or confidential data through those routes.
An HTTP 200 from `/v1/models`, or an `auto` entry in that catalog, does not
prove inference is available. HTTP 503 with `no_providers_configured` means
the gateway has no usable upstream provider credential, including anonymous
provider registrations. `integrations doctor` checks
reachability only; it does not send an inference request.

**Important:** this is an alternative inference backend; do not put it in
front of a browser-backed KITT proxy and assume that web subscriptions behave
like metered API credentials. Model/tool capabilities and rate limits depend
on the real upstream provider.

## Strix — install, don't rewrite

Install the official Strix CLI separately following
https://github.com/usestrix/strix#quick-start. Example using an isolated
Python application installer:

```bash
uv tool install 'strix-agent==1.7.0'
kitt security strix status
kitt security strix scan --target . --authorized --root /path/to/disposable-worktree
```

The selected Strix package is pinned to **1.7.0** (upstream release tag
`v1.7.0`, commit `55bc07991aacb1c26a81b43e7ca53148730d2019`).
It is an **optional independent executable**, not a dependency in KITT's
`uv.lock`. Its own Docker image and transitive dependencies must be locked
separately for strict end-to-end reproducibility.

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

## Dependency locks and pins

The KITT Agent CLI uses its checked-in `uv.lock` and commit-pinned
`kitt-protocol` dependency (`ac9baf8b4b8f03e839471c7bbb7d5e52e970a9ec`).
KITT 0.88.44 does **not** add the Strix, FreeLLMAPI or Claude-Mem
runtime to its default package lock. Optional tools can change independently
and must be installed by an operator using versioned references. No upstream
`main` branch or moving `latest` release is required for these examples.

The `integrations doctor` command probes only localhost `127.0.0.1:3001`
for the FreeLLMAPI-compatible models endpoint and checks whether the Strix CLI
is on PATH. It does not install tools. The memory doctor may start the existing
local KITT memory daemon on demand.

Track outcomes before expanding the feature set: percentage of sessions
resumed with relevant evidence, context tokens per task, needless tool calls,
provider retry/failover success, security findings reproduced, and runtime
startup/memory cost.
