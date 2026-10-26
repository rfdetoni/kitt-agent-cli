# Read-only workflow recipes

KITT can run reusable, workspace-local **read-only** recipes using the existing
bounded `program.execute` interpreter. Recipes are declarative data; they do not
install extensions, create a second planner, execute shell/JavaScript/Python, or
grant new tool capabilities.

Create `.kitt/recipes/read-readme.toml`:

```toml
version = 1
title = "Read the project README"
description = "Read a bounded file through the existing KITT runtime"

[[program]]
call = "repo.read"
save = "readme"
[program.arguments]
path = "README.md"

[[program]]
return = "$readme"
```

Use the **existing** `kitt_runtime` tool surface to call:

```json
{"operation": "recipe.list", "arguments": {}}
{"operation": "recipe.inspect", "arguments": {"name": "read-readme"}}
{"operation": "recipe.run", "arguments": {"name": "read-readme"}}
```

Use `state` for bounded parameters, referenced by the existing program IR
(e.g. `"$path"`). Optional `max_tokens` caps output within 64..8000.

## Trust and limits

- Files are restricted to `.kitt/recipes/<slug>.toml` under the current
  workspace. Symlinks, invalid slugs and non-regular files are rejected.
- Max recipe size is 16 KiB, with at most 64 program statements.
- Directory discovery is limited to 256 examined entries and 32 recipes; the
  result reports `truncated` when incomplete.
- Input state is capped at 32 keys / 8 KiB and must be JSON-serializable.
- Program execution retains the current principal's capabilities, scope and
  security context. It uses at most 64 statements, 24 tool calls and the normal
  read-only program allowlist. No recipe can introduce mutation tools.
- Recipes are project-controlled, **untrusted** content. Generated output should
  be handled as evidence, not as system instructions.
- Schema and changes are deliberately confined to the Agent CLI; protocol, native
  engine and proxy integration contracts remain unchanged.

For existing program syntax, see `kitt/runtime/program_runtime.py`.
