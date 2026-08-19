# Final Acceptance Matrix

| Requirement | Implementation Component | Test Coverage | Evidence / Verification | Status |
|---|---|---|---|---|
| TUI can close without stopping session | `DaemonServer`, `DaemonClient` | `test_daemon_process_survives_cli_exit` | Background asyncio task continues on socket disconnect | PASS |
| Reattach works | `DaemonClient.attach` | `test_daemon_reconnect_events`, `test_daemon_attach_detach` | Events replayed with sequence IDs | PASS |
| Safe runtime reduces schema overhead | `SafeRuntime`, `ToolSurfaceSelector` | `test_safe_runtime_schema_token_reduction` | ~88% reduction in schema tokens | PASS |
| No arbitrary Python in skills | `RestrictedSkillExecutor` | `test_executable_skill_import_os_blocked`, `test_executable_skill_subprocess_blocked` | AST validation rejects forbidden modules/builtins | PASS |
| Policy protects every sensitive operation | `SafeRuntime` Operation Specs | `test_runtime_inner_operation_cannot_bypass_policy`, `test_runtime_capability_enforcement_end_to_end` | Every inner operation calls PolicyEngine | PASS |
| Retained agents reusable | `ChildAgentManager.retain`, `assign_task` | `test_retained_child_reuse_real_runtime` | Specialist state preserved, new task dispatched | PASS |
| Parent/child messaging | `ChildMessageRepository` | `test_parent_child_message`, `test_parent_child_ask_reply` | Direct & correlated ask/reply | PASS |
| Child isolated context | `ChildSession` context | `test_retained_child_preserves_bounded_specialist_context` | Working set and budgets bounded | PASS |
| Executable skills progressive | `ExecutableSkillRunner` | `test_executable_skill_load`, `test_executable_skill_capabilities` | SKILL.md loaded first; code only executed when called | PASS |
| Goals resume safely | `GoalScheduler` | `test_goal_resume` | PolicyEngine & budgets enforced on resume | PASS |
| Scheduler budgets | `GoalScheduler` | `test_goal_budget_token_stop`, `test_goal_budget_turn_stop` | `PAUSED_BUDGET_EXCEEDED` transition | PASS |
| Crash recovery | `DaemonServer`, SQLite WAL | `test_crash_recovery_daemon_kill9`, `test_crash_recovery_child_crash` | Sessions and goals reconciled on restart | PASS |
| SQLite integrity | `HistoryDatabase`, Migration v11 | `test_sqlite_integrity_after_upgrade`, `test_sqlite_foreign_key_check` | PRAGMA integrity_check == ok | PASS |
| Legacy compatibility | `ToolRegistry`, `ToolSurfaceSelector` | `test_legacy_tool_compatibility`, `test_legacy_mode_exposes_legacy_tools` | 100% legacy tool suite passing | PASS |
| Plugins | `ExtensionManager` | `test_plugins_regression` | Plugins registered and operational | PASS |
| MCP | `ExtensionManager` MCP bridge | `test_mcp_regression` | MCP server connection and tool registration | PASS |
| Providers | `ProviderRegistry`, `LLMClient` | `test_provider_regression`, `test_all_providers_support` | All providers operational | PASS |
| TUI regression free | `TerminalUI` | `test_tui_regression`, `test_tui_commands` | All slash commands dispatching | PASS |
| Full tests passing | Entire test suite | `python3 -m unittest discover` | 400+ unit & integration tests passing | PASS |
| Benchmarks recorded | `benchmarks/` | `benchmarks/safe_runtime_benchmark.py` | Empirical metrics documented | PASS |
