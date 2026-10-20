# Agent CLI 0.88.34

- Extend Goal review snapshot path tracking to `kitt_runtime.repo.write_file`, including source code and authored Markdown reports.
- Make `.md` an accepted reviewable file extension, so reconciliation/status documents produced by a Goal are eligible for deterministic inspection.
- Keep 0.88.33 bounded cross-turn host read evidence journal, context invalidation and partial-batch recovery.
- Host execution/verification remains authoritative; neither file writing nor a Markdown report proves successful code implementation without independent checks.

Validation: Agent PR Checks (critical contracts, Linux runtime, Windows package) and ecosystem integration. Authenticated WebChat and guest-VM Oracle checks require an actual workstation.
