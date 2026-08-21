# K.I.T.T. Agent CLI

Agente de codificação Python **local-first**, com suporte a Ollama e APIs
OpenAI-compatible, histórico persistente, aprovações, ferramentas, daemon e TUI
opcional (`prompt_toolkit`). O runtime Python suporta **Python 3.12+**; o engine
Rust é opcional e possui fallback Python.

## Arquitetura

- `KittRuntime` é o composition root e compartilha uma única instância de
  `ContextEngine`/`RepositoryIndex`.
- `WorkspaceFileSystem` é a boundary canônica de acesso a arquivos. Tools,
  retrieval e indexação devem rejeitar traversal, symlinks/reparse points,
  arquivos especiais e paths protegidos antes de consumir conteúdo.
- O motor de contexto indexa incrementalmente, recupera paths/símbolos/FTS5,
  grafo, testes e Git e compila evidências em um envelope JSONL marcado como
  **untrusted workspace data**.
- Bootstrap de paths explícitos é síncrono; varredura completa pode continuar em
  background. FTS5 possui fallback lexical.
- Budget mantém como invariante `input + output reservado <= context window`.
  Resultados grandes viram artifacts e follow-ups são rebudgetados.
- `AGENTS.md`, memória, skills e tool output entram como dados não confiáveis,
  subordinados à política do sistema.
- Providers usam protocolos explícitos; protocolos desconhecidos falham cedo em
  vez de cair silenciosamente em OpenAI Chat Completions.

## Uso

```bash
python3 -m kitt.cli.main --help
python3 -m kitt.cli.main
kitt --root ./repo models
kitt models --root ./repo
```

Instale dependências opcionais com `pip install -r requirements.txt`. Ollama e
outros providers são configurados pela CLI/configuração local.

## Validação

```bash
python3 -m compileall -q kitt tests
python3 -m pytest -q
python3 -m kitt.evals.retrieval
python3 -m kitt.benchmarks.context_benchmark --files 1000,20000,100000
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-features
```

O índice suporta limites de arquivos, bytes, resultados e timeout de operações
externas. Arquivos escritos por tools são substituídos atomicamente e tornam-se
visíveis ao índice antes da próxima chamada relevante.

## Licença

MIT. Consulte [LICENSE](LICENSE).
