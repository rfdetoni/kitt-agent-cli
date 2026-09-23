# KITT Agent CLI plugins

KITT extensions are composed by `ExtensionManager` from three independent mechanisms: Python plugins, lifecycle/interception hooks, and MCP servers. Skills remain a separate knowledge/workflow layer.

## Discovery and precedence

Plugins are discovered in this order:

1. bundled first-party plugins shipped inside `kitt.extensions.builtin_plugins`;
2. global user plugins under `~/.kitt/plugins/<plugin>/plugin.toml`;
3. workspace plugins under `<workspace>/.kitt/plugins/<plugin>/plugin.toml`.

Bundled names are reserved. A global or workspace plugin cannot shadow a bundled plugin, and a workspace plugin cannot shadow a global plugin.

External plugins continue to use API version 1 and require explicit content trust. Trust is workspace-scoped, version-aware, and bound to the SHA-256 digest of the plugin content. A content change invalidates the grant. Bundled plugins are trusted as part of the installed KITT distribution; disable them when they are not desired.

## Bundled catalog

| Plugin | Tool | Default | Purpose |
| --- | --- | --- | --- |
| `kitt-project-intel` | `project_intel` | enabled | Detect languages, frameworks, manifests and build systems. |
| `kitt-test-impact` | `test_impact` | enabled | Rank likely affected tests from changed paths. |
| `kitt-lsp` | `lsp_inspect` | enabled | Discover language servers already available on the host. |
| `kitt-openapi` | `openapi_inspect` | enabled | Inspect local OpenAPI/Swagger contracts. |
| `kitt-migration-guard` | `migration_guard` | enabled | Detect destructive/high-risk migration patterns. |
| `kitt-git-worktree` | `git_worktree` | enabled | Inspect worktrees and produce a governed worktree creation argv. |
| `kitt-quality-report` | `quality_report` | enabled | Build targeted/full deterministic verification plans. |
| `kitt-dependency-audit` | `dependency_audit` | enabled | Inspect dependency manifests and lockfiles. |
| `kitt-ci` | `ci_inspect` | enabled | Inspect CI systems and GitHub Actions references. |
| `kitt-container` | `container_inspect` | enabled | Inspect Docker/Compose/Kubernetes descriptors and common risks. |
| `kitt-release` | `release_plan` | disabled | Produce semantic version/release plans without mutating files. |
| `kitt-github` | `github_inspect` | disabled | Inspect local GitHub remote/MCP integration state. |
| `kitt-database` | `database_inspect` | disabled | Detect database technologies and migration posture without connecting. |
| `kitt-browser` | `browser_inspect` | disabled | Detect browser automation configuration without launching a browser. |
| `kitt-cloud` | `cloud_inspect` | disabled | Detect Terraform/cloud/Kubernetes signals without credentials or network. |
| `kitt-observability` | `observability_inspect` | disabled | Detect local observability libraries/configuration. |

All bundled handlers are lazy and read-only. They do not open database connections, contact cloud/GitHub services, launch browsers, start LSP processes, modify Git state, or read credentials.

## Why mutating plugins return plans

The plugin API can register model-facing tools. Direct filesystem/process mutations from a plugin would bypass the Agent's canonical `PolicyEngine`, single-use approvals, goal fencing and audit path. First-party plugins therefore follow this rule:

`plugin analysis -> explicit plan/argv -> kitt_runtime -> policy/approval -> mutation`

For example, `git_worktree` returns the proposed `git worktree add ...` argv but does not execute it. `release_plan` computes version changes but does not edit version or lock files.

Network-backed workflows use MCP or another authorized KITT integration instead of embedding credentials in plugin code.

## Managing plugins

Use the CLI:

```bash
kitt plugins list
kitt plugins inspect kitt-project-intel
kitt plugins disable kitt-container
kitt plugins enable kitt-release
kitt plugins reload kitt-project-intel
```

External plugins additionally use:

```bash
kitt plugins trust my-plugin
kitt plugins untrust my-plugin
```

Bundled plugins cannot be independently untrusted because their authority is the installed KITT distribution. They can always be disabled per workspace.

## External plugin structure

A minimal external plugin remains:

```text
my-plugin/
├── plugin.toml
└── plugin.py
```

Example manifest:

```toml
name = "my-plugin"
version = "1.0.0"
api_version = "1"
entrypoint = "plugin:setup"
permissions = ["tools.register"]
trusted_in_process = false
enabled_by_default = true
```

Example entrypoint:

```python
def setup(ctx):
    def inspect(args):
        return {"status": "ok"}

    ctx.tools.register(
        "my_inspect",
        inspect,
        description="Inspect something deterministically.",
        schema={},
    )
```

Third-party plugins should normally keep `trusted_in_process = false`. They then execute in the isolated worker. On Linux, KITT uses Bubblewrap when available to scope filesystem/network access according to permissions; otherwise it records that only process isolation is available.

## Capability context

API v1 exposes a restricted `PluginContext` containing identity, manifest, events, hooks, tools, commands, namespaced configuration, logger and the canonical workspace path. The workspace path is context, not an authority grant: isolated worker permissions and the runtime policy boundary remain authoritative for untrusted code.

Supported manifest permissions currently include event/tool/model/context/memory/session/filesystem/network/command/provider/MCP/credential capabilities. Plugins receive only the APIs implemented by the current SDK and should not infer authority from a permission name alone.

## Lifecycle

At runtime:

```text
discover manifests
      |
      v
trust/content verification
      |
      v
immutable snapshot
      |
      v
load entrypoint
      |
      v
register tools/hooks/commands
      |
      v
start
      |
      v
active
      |
      v
stop + unregister-by-owner
```

`ExtensionManager.start()` is transactional. A critical extension startup failure triggers rollback of plugins and MCP connections before startup is reported as failed.

## First-party implementation rules

Bundled plugins must remain dependency-light, bounded and deterministic. New first-party plugins should:

- prefer local parsing over LLM calls;
- bound repository scans and output;
- never return secrets;
- never bypass `kitt_runtime` for mutation;
- avoid network by default;
- keep optional external integrations opt-in;
- return structured evidence that can feed verification/review;
- add tests for discovery, trust, enablement and representative output.
