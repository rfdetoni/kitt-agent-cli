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

## Guarded write recipes

A change recipe replaces `[[program]]` with one or more `[[changes]]`
entries, each specifying a workspace-relative `path` and UTF-8 `content`:

```toml
version = 1
title = "Update metadata"
[[changes]]
path = "metadata.txt"
content = "new content"
```

1. Call `recipe.preview` with the name to obtain the hash-guarded
   `review_id` and a unified diff; no file is modified.
2. Call `recipe.apply` with `{"name":"...", "review_id":"..."}`. Normal
   KITT `repo.write` capability, host write policy, risk budget and
   approval process apply to this operation.
3. Call `recipe.rewind` with the same arguments to restore exact preimages
   only if the current files still match the applied hashes.

`review.preview`, `review.diff`, `review.apply`, and `review.rewind`
provide the same staging lifecycle without a recipe.
The total original plus proposed content limit is 16 KiB per review, across
at most eight files. Git and KITT control-plane files are not eligible.
A recipe digest change invalidates its staged plan. Files changed by
other processes are never silently overwritten.

## Parameterized change recipes

Change recipes can declare `parameters = ["name"]` and use `${name}` only
in UTF-8 content (not paths). Each supplied value must be at most 256 bytes;
undeclared or missing parameters are rejected.

Declare `[[checks]]` with `path` and `contains` to test post-apply file
content within the workspace, and `[[after_apply]]` with read-only program
statements to run bounded validation hooks. A failed check/hook attempts
to revert files using the same SHA guards; conflicting external edits are
never overwritten. Hooks cannot execute shell or grant additional tools.

Conditional and looped read-only recipes use existing `program.execute`
`if` and `for_each` statements; mutable changes remain explicitly staged.
