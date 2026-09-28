# KITT Runtime Operation Reference

This file is generated from the authoritative runtime operation registry.

Regenerate it with:

```bash
python scripts/generate_runtime_reference.py > docs/RUNTIME_REFERENCE.md
```

The table documents authority metadata. Operation-specific argument validation remains owned by the runtime/handler implementing the operation; callers must use the model-facing tool contract and examples supplied by the Agent.

| Operation | Capability | Policy action | Sensitive | Risk | Sandbox | Resume tool |
| --- | --- | --- | ---: | ---: | --- | --- |
| artifacts.hydrate | CAP_ARTIFACT_READ | - | no | 0 | - | - |
| artifacts.read | CAP_ARTIFACT_READ | artifact_read | no | 0 | - | - |
| artifacts.search | CAP_ARTIFACT_READ | - | no | 0 | - | - |
| artifacts.store | CAP_ARTIFACT_WRITE | artifact_store | yes | 1 | - | artifact_store |
| backend.compile | CAP_REPO_READ | - | no | 0 | - | - |
| backend.plan | CAP_REPO_READ | - | no | 0 | - | - |
| backend.validate | CAP_REPO_READ | - | no | 0 | - | - |
| browser.click | CAP_BROWSER_WRITE | browser.click | yes | 1 | - | - |
| browser.close | CAP_BROWSER_WRITE | browser.close | yes | 1 | - | - |
| browser.inspect | CAP_BROWSER_READ | - | no | 0 | - | - |
| browser.open | CAP_BROWSER_READ | - | no | 0 | - | - |
| browser.screenshot | CAP_BROWSER_READ | - | no | 0 | - | - |
| browser.type | CAP_BROWSER_WRITE | browser.type | yes | 1 | - | - |
| children.inspect | CAP_CHILD_INSPECT | - | no | 0 | - | - |
| children.send | CAP_CHILD_MESSAGE | - | no | 0 | - | - |
| children.spawn | CAP_CHILD_SPAWN | child_spawn | yes | 2 | - | child_spawn |
| flow.execute | - | - | no | 0 | - | - |
| goal.inspect | CAP_GOAL_MANAGE | - | no | 0 | - | - |
| goal.update | CAP_GOAL_MANAGE | goal_update | yes | 0 | - | - |
| handles.resolve | - | - | no | 0 | - | - |
| mcp.call | CAP_MCP_CALL | mcp_call | yes | 0 | - | - |
| memory.concept | CAP_MEMORY_WRITE | memory_save | yes | 0 | - | - |
| memory.correct | CAP_MEMORY_WRITE | memory_save | yes | 0 | - | - |
| memory.link | CAP_MEMORY_WRITE | memory_save | yes | 0 | - | - |
| memory.query | CAP_MEMORY_READ | - | no | 0 | - | - |
| patch.apply | CAP_REPO_WRITE | apply_patch | yes | 1 | - | apply_patch |
| process.run | CAP_PROCESS_RUN | run_command | yes | 3 | workspace-write | run_command |
| program.execute | - | - | no | 0 | - | - |
| repo.ast_search | CAP_REPO_SEARCH | search | no | 0 | - | - |
| repo.call_hierarchy | CAP_REPO_SEARCH | search | no | 0 | - | - |
| repo.context_map | CAP_REPO_SEARCH | search | no | 0 | - | - |
| repo.create_directory | CAP_REPO_WRITE | create_directory | yes | 1 | - | create_directory |
| repo.definition | CAP_REPO_READ | read_file | no | 0 | - | - |
| repo.delete | CAP_REPO_WRITE | write_file | yes | 4 | - | - |
| repo.diagnostics | CAP_REPO_READ | read_file | no | 0 | - | - |
| repo.edit_symbol | CAP_REPO_WRITE | write_file | yes | 1 | - | - |
| repo.hover | CAP_REPO_READ | read_file | no | 0 | - | - |
| repo.inspect_symbol | CAP_REPO_READ | read_file | no | 0 | - | - |
| repo.list | CAP_REPO_READ | list_files | no | 0 | - | - |
| repo.move | CAP_REPO_WRITE | write_file | yes | 1 | - | - |
| repo.outline | CAP_REPO_READ | read_file | no | 0 | - | - |
| repo.read | CAP_REPO_READ | read_file | no | 0 | - | - |
| repo.read_symbol | CAP_REPO_READ | read_file | no | 0 | - | - |
| repo.references | CAP_REPO_SEARCH | search | no | 0 | - | - |
| repo.references_semantic | CAP_REPO_SEARCH | search | no | 0 | - | - |
| repo.rename | CAP_REPO_WRITE | write_file | yes | 1 | - | - |
| repo.search | CAP_REPO_SEARCH | search | no | 0 | - | - |
| repo.write_file | CAP_REPO_WRITE | write_file | yes | 1 | - | write_file |
| security.scan | CAP_REPO_SEARCH | search | no | 0 | - | - |
| session.search | CAP_MEMORY_READ | - | no | 0 | - | - |
| skill.call | CAP_REPO_READ | - | no | 0 | - | - |
| state.get | CAP_REPO_READ | - | no | 0 | - | - |
| state.list | CAP_REPO_READ | - | no | 0 | - | - |
| state.set | CAP_REPO_WRITE | - | no | 0 | - | - |
| surface.action | - | - | no | 0 | - | - |
| surface.capabilities | - | - | no | 0 | - | - |
| surface.delete | - | - | no | 0 | - | - |
| surface.get | - | - | no | 0 | - | - |
| surface.patch | - | - | no | 0 | - | - |
| surface.publish | - | - | no | 0 | - | - |
