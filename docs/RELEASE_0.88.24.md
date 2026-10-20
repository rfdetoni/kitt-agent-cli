# Agent CLI 0.88.24 — planos e diagnóstico de execução

O log de 20/10/2026 demonstrou que pressionar A persistiu o perfil `autonomous`: o daemon recebeu `runtime.set_autonomy` e as chamadas seguintes tiveram `run_command decision=ALLOW`. A falha remanescente é `Strong sandbox profile 'workspace-write' requires functional bubblewrap >= 0.12`, não uma nova exigência de aprovação.

## Correções

1. `plan.submit` reporta o contrato exigido quando `arguments.proposal` não é enviado; `plan.verify` explica que um plano válido precisa existir antes da verificação.
2. Os prompts do agente, com e sem envelope, incluem exemplo concreto do contrato `proposal: {schema_version, objective, tasks}`, com campos completos para cada tarefa.
3. A execução de comandos que falha pelo sandbox mostra como diagnosticar o host (Bubblewrap e suporte a user namespaces) ou usar ferramentas de execução de VM autenticadas. Nenhuma alternativa sem isolamento é ativada automaticamente.
4. `tests/test_plan_runtime_diagnostics.py` cobre os caminhos de erro e o encaminhamento de propostas válidas.

## Restrições não alteradas

O budget de ferramentas é independente de `allow-all`. O número de ações pode esgotar-se em um turno longo, sem implicar perda da aprovação. Não removemos limites de tempo, consumo de tokens, controle do host, validação clean-room ou barreiras de isolamento.

Requer validação dos checks da PR, seguida de atualização do Assistant Runtime e dos pins do ecossistema.
