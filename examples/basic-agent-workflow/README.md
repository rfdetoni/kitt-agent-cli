# Basic Agent workflow

This example shows the preferred evidence-first flow.

## Start

```bash
kitt --root /path/to/project
```

Ask for a bounded change:

> Inspect the validation code, add one missing regression test, make the smallest implementation change required and run the targeted test.

Expected flow:

1. inspect repository/context;
2. identify the smallest relevant files/symbols;
3. explain the next mutation in the reasoning summary;
4. request approval only when policy requires it;
5. edit through `repo.write_file`, `repo.edit_symbol` or `patch.apply`;
6. run the smallest targeted validation;
7. broaden validation only if needed;
8. finish with concrete evidence rather than an unsupported success claim.

For a read-only dry run, explicitly request: `Do not modify files`.
