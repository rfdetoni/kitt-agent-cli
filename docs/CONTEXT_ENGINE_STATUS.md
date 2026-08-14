# Motor de contexto — evidência atual

## Pipeline ativo

```text
tarefa -> QueryPlanner -> RepositoryIndex SQLite/FTS5 -> retrieval híbrido
       -> ContextSelector -> ContextCompiler/quality -> PromptBudget
       -> TurnProcessor -> LLM/tool loop com rebudget
```

`KittRuntime`, `TurnProcessor` e `ToolRegistry` compartilham o mesmo
`ContextEngine` e `RepositoryIndex`. O índice usa bootstrap síncrono para paths
explícitos e atualização completa em background quando necessário.

## Entregas desta iteração

- Hash de conteúdo evita reparse quando somente mtime muda.
- Parser recebe conteúdo já lido pelo índice; elimina segunda leitura do arquivo.
- Índices SQLite para referências e arestas aceleram reconstrução do grafo.
- Follow-up de tool loop é rebudgetado antes de cada chamada ao provider; métricas
  registram input efetivamente enviado, incluindo mensagens de tools.
- Escritas de `write_file` usam arquivo temporário + `fsync` + `os.replace`.
- Cache em memória evita repetir resumo do mesmo pacote/modelo/tarefa.
- Quality gate marca índice parcial como degradado sem esconder paths explícitos.
- Evals de retrieval expõem seis ablações executando caminhos diferentes.

## Evidência executada

| Verificação | Resultado |
|---|---:|
| `compileall` | passou |
| `unittest discover -s tests` | 278 testes, passou |
| retrieval eval | Recall@5 1.0; MRR 0.9 |
| benchmark 1.000 arquivos | cold 506.66 ms; warm 101.58 ms; busca 0.37 ms |
| benchmark 20.000 arquivos | cold 77.957,75 ms; warm 1.913,88 ms; busca 0.52 ms |
| benchmark 100.000 arquivos | timeout em 300 s durante cold build |

O benchmark de 100.000 arquivos excedeu 300 s durante cold build, limitado pelo
parser stdlib e criação de fixtures; não há número inventado. O gargalo medido em 2.000 arquivos é
scanner/SQLite/parser, não retrieval warm. Próxima otimização de escala deve
ser batch de indexação/parser incremental, guiada por profiling.

## Limites conhecidos

- Parser sem Tree-sitter/LSP/SCIP permanece heurístico para linguagens não Python.
- Resolução de referências é por nome; overloads e tipos exigem adapter opcional.
- `ContextCompiler` mantém chunks bounded; slices por símbolo podem ser refinados
  quando parser estrutural fornecer ranges mais precisos.
