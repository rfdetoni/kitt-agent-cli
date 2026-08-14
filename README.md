# K.I.T.T. Agent CLI

Agente de codificação Python local-first, com suporte a Ollama e APIs
OpenAI-compatible, histórico persistente, aprovações, ferramentas e TUI
opcional (`prompt_toolkit`). O caminho principal usa apenas biblioteca padrão e
SQLite (`sqlite3`).

## Arquitetura

- `KittRuntime` compõe uma única instância de `ContextEngine` e `RepositoryIndex`.
- O motor de contexto analisa a tarefa deterministicamente, indexa o workspace
  incrementalmente, recupera paths/símbolos/FTS5/grafo/testes/Git e compila um
  pacote de evidências com ranges, hashes, provenance e trust level.
- Bootstrap de paths explícitos é síncrono; varredura completa pode continuar em
  background. FTS5 tem fallback lexical explícito.
- Budget considera system prompt, tarefa, histórico, contexto, ferramentas e
  reserva de output. Resultados grandes viram artifacts; follow-ups são
  rebudgetados antes de cada chamada ao modelo.
- `AGENTS.md`, memória, skills e tool output entram como dados não confiáveis,
  subordinados à política do sistema.

## Uso

```bash
python3 -m kitt.cli.main --help
python3 -m kitt.cli.main
```

Instale dependências opcionais com `pip install -r requirements.txt`. Ollama e
outros providers são configurados pela CLI/configuração local.

## Validação

```bash
python3 -m compileall -q kitt tests
python3 -m unittest discover -s tests -v
python3 -m kitt.evals.retrieval
python3 -m kitt.benchmarks.context_benchmark --files 1000,20000,100000
```

O índice suporta limites de arquivos, bytes, resultados e timeout de operações
externas. Arquivos escritos por tools são substituídos atomicamente e tornam-se
visíveis ao índice antes da próxima chamada relevante.

## Licença

MIT. Consulte [LICENSE](LICENSE).
