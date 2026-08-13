# Implementation Traceability Matrix (K.I.T.T. Agent CLI - Iteração 9)

| Requisito | Arquivos Relevantes | Testes Comportamentais | Evidência Operacional | Estado |
| --- | --- | --- | --- | --- |
| **Import integral** | `kitt/children/manager.py`, `kitt/core/turn_processor.py` | `test_iteration9_regressions.py:test_01` | Todos os submódulos importam sem erro via `pkgutil.walk_packages` | DONE |
| **Workspace / FK** | `kitt/history/repository.py`, `kitt/domain/entities.py` | `test_iteration9_regressions.py:test_02`, `test_04` | `WorkspaceIdentity` unificado; FKs de Artifact/Child validadas | DONE |
| **No-history** | `kitt/history/database.py`, `kitt/core/runtime.py` | `test_iteration9_regressions.py:test_06` | Modo efêmero em `:memory:` cria 0 arquivos `.kitt` no workspace | DONE |
| **Runtime / lifecycle** | `kitt/core/runtime.py`, `kitt/cli/repl.py` | `test_prime_runtime.py:test_runtime_composition_and_cli_contract` | Single composition root com close thread-safe e `switch_workspace` | DONE |
| **Tool schemas** | `kitt/tools/registry.py`, `kitt/tools/policy_engine.py` | `test_tool_registry.py:test_tool_definitions_filtering` | Schemas validados runtime por capability e tipo | DONE |
| **Policy / capabilities** | `kitt/tools/policy_engine.py` | `test_iteration9_regressions.py:test_05` | Origem `MODEL` exige `ASK` em tools mutáveis; bloqueio emite `TurnBlocked` | DONE |
| **Approval transacional** | `kitt/tools/approval.py`, `kitt/core/turn_processor.py` | `test_iteration9_regressions.py:test_08`, `test_prime_runtime.py` | Transações com nonce de uso único e tratamento gracioso de `grant=None` | DONE |
| **Budget incremental** | `kitt/context_filter/prompt_budget.py`, `kitt/core/turn_processor.py` | `test_phase0_security.py` | Rebudgeting dinâmico; outputs > 4k chars salvos em `ArtifactStore` | DONE |
| **Artifact / read / search** | `kitt/artifacts/store.py`, `kitt/tools/registry.py` | `test_prime_runtime.py:test_artifact_integrity_and_pagination` | Leitura paginada com verificação de integridade sha256 | DONE |
| **Branch / fork** | `kitt/history/service.py`, `kitt/history/session_tree.py` | `test_phase2_history.py:test_fork_conversation` | Fork sem duplicação de ordinais via `clone_active_path` | DONE |
| **Steering / follow-up** | `kitt/queueing/service.py`, `kitt/core/turn_processor.py` | `test_prime_runtime.py:test_queue_steering_priority_and_single_delivery` | Fila com prioridade `STEERING` consumida nos checkpoints do turno | DONE |
| **Child isolation** | `kitt/children/manager.py`, `kitt/tools/registry.py` | `test_iteration9_regressions.py:test_03` | Disparo isolado; estados `FAILED`/`TIMED_OUT` retornam erro estruturado | DONE |
| **Goals / gates** | `kitt/goals/service.py`, `kitt/goals/models.py` | `test_prime_runtime.py:test_goal_budget_gate` | Schema v5 com colunas `name`/`timeout_seconds` para quality gates | DONE |
| **Validation loop** | `kitt/tools/build_detector.py`, `kitt/tools/log_reducer.py`, `kitt/core/turn_processor.py` | `test_phase4_validation.py` | Loop de testes pós-edição com LogReducer e BuildDetector | DONE |
| **Token metrics** | `kitt/metrics/collector.py`, `kitt/metrics/models.py` | `test_prime_runtime.py` | Registro de TurnMetrics suportando payloads dict via EventBus | DONE |
| **Skills / harness** | `kitt/skills/discovery.py`, `kitt/harness/service.py` | `test_prime_runtime.py:test_progressive_skills` | Seleção progressiva por relevância com orçamento de tokens | DONE |
| **Compaction** | `kitt/compaction/service.py`, `kitt/history/session_tree.py` | `test_prime_runtime.py:test_compaction_replaces_old_active_path` | Reescrita de branch ativo preservando fatos estruturados | DONE |
| **LLM cancellation** | `kitt/llm/client.py` | `test_prime_runtime.py:test_async_turn_stream` | Cancelamento cooperativo e fechamento de conexões | DONE |
| **Process tree kill** | `kitt/tools/process_runner.py` | `test_prime_runtime.py` | Encerramento de grupo de processos em timeout/cancel | DONE |
| **Index cache** | `kitt/context_engine/indexer.py` | `test_phase2_incremental_context.test_index_caching` | Cache incremental reutilizável com checagem de mtime/hash | DONE |
| **CLI / migrations / docs** | `kitt/cli/repl.py`, `kitt/history/migrations.py` | `test_prime_runtime.py:test_v1_database_upgrades_to_v4` | Migrações v1->v5 validadas e CLI operacional com `--help` OK | DONE |
