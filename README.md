# K.I.T.T. Agent CLI

Agente de codificação Python **local-first**, com suporte a Ollama e APIs OpenAI-compatible, histórico persistente, aprovações, ferramentas, daemon, TUI opcional (`prompt_toolkit`) e integração com o ecossistema KITT. O runtime Python suporta **Python 3.12+**; o engine Rust é opcional e possui fallback Python.

---

## 🧠 Memória e Integração com o Ecossistema KITT

- **Memória Estruturada Local & Dreaming Mode**: Armazenamento SQLite WAL nativo com consolidação transacional e categorização semântica de regras e decisões.
- **Dual-Write para o Ecossistema**: Quando o daemon `kittd` (`kitt-assistant`) está em execução na máquina, as memórias do projeto são escritas de forma dual e assíncrona/não-bloqueante no backend compartilhado de memória (`kitt-memory`).
- **Resiliência Standalone**: Se o `kittd` estiver indisponível ou ausente, a operação do `kitt-agent-cli` continua 100% autônoma e idêntica, sem latência adicional.

---

## 🏛️ Arquitetura

- `KittRuntime` é o composition root e compartilha uma única instância de `ContextEngine`/`RepositoryIndex`.
- `WorkspaceFileSystem` é a boundary canônica de acesso a arquivos. Tools, retrieval e indexação rejeitam traversal, symlinks/reparse points, arquivos especiais e paths protegidos antes de consumir conteúdo.
- O motor de contexto indexa incrementalmente, recupera paths/símbolos/FTS5, grafo, testes e Git e compila evidências em um envelope JSONL marcado como **untrusted workspace data**.
- Bootstrap de paths explícitos é síncrono; varredura completa pode continuar em background. FTS5 possui fallback lexical.
- Budget mantém como invariante `input + output reservado <= context window`. Resultados grandes viram artifacts e follow-ups são rebudgetados.
- `AGENTS.md`, memória, skills e tool output entram como dados não confiáveis, subordinados à política do sistema.
- Providers usam protocolos explícitos; protocolos desconhecidos falham cedo em vez de cair silenciosamente em OpenAI Chat Completions.

---

## 🚀 Uso

```bash
python3 -m kitt.cli.main --help
python3 -m kitt.cli.main
kitt --root ./repo models
kitt models --root ./repo
```

Instale dependências com `pip install -e .` ou `pip install -r requirements.txt`.

---

## 🧪 Validação

```bash
python3 -m pytest
python3 -m unittest tests/memory/test_shared_memory_client.py
```

---

## 📄 Licença

MIT. Consulte [LICENSE](LICENSE).
