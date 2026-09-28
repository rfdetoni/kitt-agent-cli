# KITT Runtime Operation Reference

This file is generated from the authoritative runtime operation registry.

Regenerate it with:

```bash
python scripts/generate_runtime_reference.py > docs/RUNTIME_REFERENCE.md
```

The table documents authority metadata. Operation-specific argument validation remains owned by the runtime/handler implementing the operation; callers must use the model-facing tool contract and examples supplied by the Agent.

| Operation | Capability | Policy action | Sensitive | Risk | Sandbox | Resume tool |
| --- | --- | --- | ---: | ---: | --- | --- |
| artifacts.hydrate | artifact.read | - | no | 0 | - | - |
| artifacts.read | artifact.read | artifact_read | no | 0 | - | - |
| artifacts.search | artifact.read | - | no | 0 | - | - |
| artifacts.store | artifact.write | artifact_store | yes | 1 | - | artifact_store |
| backend.compile | repo.read | - | no | 0 | - | - |
| backend.plan | repo.read | - | no | 0 | - | - |
| backend.validate | repo.read | - | no | 0 | - | - |
| browser.click | browser.write | browser.click | yes | 1 | - | - |
| browser.close | browser.write | browser.close | yes | 1 | - | - |
| browser.inspect | browser.read | - | no | 0 | - | - |
| browser.open | browser.read | - | no | 0 | - | - |
| browser.screenshot | browser.read | - | no | 0 | - | - |
| browser.type | browser.write | browser.type | yes | 1 | - | - |
| children.inspect | child.inspect | - | no | 0 | - | - |
| children.send | child.message | - | no | 0 | - | - |
| children.spawn | child.spawn | child_spawn | yes | 2 | - | child_spawn |
| flow.execute | - | - | no | 0 | - | - |
| goal.inspect | goal.manage | - | no | 0 | - | - |
| goal.update | goal.manage | goal_update | yes | 0 | - | - |
| handles.resolve | - | - | no | 0 | - | - |
| mcp.call | mcp.call | mcp_call | yes | 0 | - | - |
| memory.concept | memory.write | memory_save | yes | 0 | - | - |
| memory.correct | memory.write | memory_save | yes | 0 | - | - |
| memory.link | memory.write | memory_save | yes | 0 | - | - |
| memory.query | memory.read | - | no | 0 | - | - |
| patch.apply | repo.write | apply_patch | yes | 1 | - | apply_patch |
| process.run | process.run | run_command | yes | 3 | workspace-write | run_command |
| program.execute | - | - | no | 0 | - | - |
| repo.ast_search | repo.search | search | no | 0 | - | - |
| repo.call_hierarchy | repo.search | search | no | 0 | - | - |
| repo.context_map | repo.search | search | no | 0 | - | - |
| repo.create_directory | repo.write | create_directory | yes | 1 | - | create_directory |
| repo.definition | repo.read | read_file | no | 0 | - | - |
| repo.delete | repo.write | write_file | yes | 4 | - | - |
| repo.diagnostics | repo.read | read_file | no | 0 | - | - |
| repo.edit_symbol | repo.write | write_file | yes | 1 | - | - |
| repo.hover | repo.read | read_file | no | 0 | - | - |
| repo.inspect_symbol | repo.read | read_file | no | 0 | - | - |
| repo.list | repo.read | list_files | no | 0 | - | - |
| repo.move | repo.write | write_file | yes | 1 | - | - |
| repo.outline | repo.read | read_file | no | 0 | - | - |
| repo.read | repo.read | read_file | no | 0 | - | - |
| repo.read_symbol | repo.read | read_file | no | 0 | - | - |
| repo.references | repo.search | search | no | 0 | - | - |
| repo.references_semantic | repo.search | search | no | 0 | - | - |
| repo.rename | repo.write | write_file | yes | 1 | - | - |
| repo.search | repo.search | search | no | 0 | - | - |
| repo.write_file | repo.write | write_file | yes | 1 | - | write_file |
| security.scan | repo.search | search | no | 0 | - | - |
| session.search | memory.read | - | no | 0 | - | - |
| skill.call | repo.read | - | no | 0 | - | - |
| state.get | repo.read | - | no | 0 | - | - |
| state.list | repo.read | - | no | 0 | - | - |
| state.set | repo.write | - | no | 0 | - | - |
| surface.action | - | - | no | 0 | - | - |
| surface.capabilities | - | - | no | 0 | - | - |
| surface.delete | - | - | no | 0 | - | - |
| surface.get | - | - | no | 0 | - | - |
| surface.patch | - | - | no | 0 | - | - |
| surface.publish | - | - | no | 0 | - | - |
