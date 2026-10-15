# KITT Hybrid Code-RAG

Base de implementação analisada: `rfdetoni/kitt-agent-cli` `main` em
`dfac110b93612372ac2041ffd25748df38bfc0f6`.

## Objetivo

Adicionar recuperação semântica ao Context Engine sem transformar embeddings em um
segundo mecanismo de busca de arquivos e sem introduzir Vector DB obrigatório.

A implementação mantém as responsabilidades separadas:

1. **Path explícito** — leitura segura via `WorkspaceFileSystem`.
2. **Símbolos** — índice incremental já existente (`RepositoryIndex`).
3. **Busca lexical de arquivos** — `NativeCodeEngine` somente quando o backend Rust está ativo.
4. **Fallback lexical** — FTS do `RepositoryIndex` quando a extensão Rust não está disponível.
5. **Working set / Git / testes / grafo** — sinais estruturais existentes.
6. **Semantic seed fallback** — quando os sinais determinísticos são escassos, extrai um conjunto pequeno e diverso de chunks do índice SQLite existente; não lê/varre arquivos novamente.
7. **Semantic reranker** — embeddings apenas sobre o conjunto candidato limitado.
8. **RRF** — Reciprocal Rank Fusion combina os rankings sem comparar diretamente scores incompatíveis.
9. **ContextSelector** — continua responsável pelo orçamento final de tokens e deduplicação.

## Regra crítica sobre o mecanismo Rust

`NativeCodeEngine` possui fallback Python. O Code-RAG **não o utiliza para pesquisa de arquivos**.
`NativeLexicalRetriever.available` só retorna verdadeiro quando:

- `status.available == True`; e
- `status.backend == "rust"`.

Se a extensão Rust não estiver presente, o pipeline chama o FTS já indexado. Isso evita duas
varreduras independentes do workspace.

## Semântica local e opcional

Por padrão, embeddings ficam desligados. Prepare o modelo explicitamente com `ollama pull qllama/multilingual-e5-small:q8_0` e verifique com `ollama list`. A tag pública do conversor qllama identifica digest `96316f5cc784` (confira o digest completo instalado antes de considerar a versão fixada). O modelo upstream `intfloat/multilingual-e5-small` é MIT, 384 dimensões e 512 tokens; a conversão é publicada por terceiro e não foi validada nesta alteração com pesos executados. Para usar Ollama:

```bash
export KITT_RAG_SEMANTIC_ENABLED=true
export KITT_RAG_EMBEDDING_MODEL=qllama/multilingual-e5-small:q8_0
export KITT_RAG_OLLAMA_URL=http://localhost:11434
```

O KITT não faz `pull` automático do modelo. O operador decide qual embedding model instalar.

Outras opções:

```bash
export KITT_RAG_EMBEDDING_TIMEOUT=4
export KITT_RAG_SEMANTIC_CANDIDATES=24
export KITT_RAG_NATIVE_CANDIDATES=32
export KITT_RAG_NATIVE_TOKEN_BUDGET=1400
export KITT_RAG_RRF_K=60

export KITT_RAG_WEIGHT_LEXICAL=1.0
export KITT_RAG_WEIGHT_SYMBOL=1.35
export KITT_RAG_WEIGHT_SEMANTIC=1.20
export KITT_RAG_WEIGHT_GRAPH=0.75
export KITT_RAG_WEIGHT_WORKING_SET=0.90
export KITT_RAG_WEIGHT_GIT=0.95
export KITT_RAG_WEIGHT_TEST=0.85
```

## Degradação segura

- Rust indisponível -> FTS existente.
- Ollama indisponível -> ranking determinístico continua normalmente.
- Embedding inválido/timeout -> semantic rank é ignorado e `semantic_degraded` aparece nas stats.
- Nenhuma dependência Python obrigatória foi adicionada.
- Nenhum banco vetorial foi adicionado.

## Observabilidade

`ContextEngine.last_build_stats["retrieval"]` passa a expor, entre outros:

- `native_backend`
- `native_available`
- `lexical_backend`
- `symbol_candidates`
- `lexical_candidates`
- `working_set_candidates`
- `git_candidates`
- `test_candidates`
- `graph_candidates`
- `semantic_seed_candidates`
- `semantic_enabled`
- `semantic_candidates`
- `semantic_degraded`
- `semantic_reason`
- `fused_candidates`
- `selected`
- `discarded`

## Arquivos

### Novos

- `kitt/context/rag/__init__.py`
- `kitt/context/rag/config.py`
- `kitt/context/rag/embeddings.py`
- `kitt/context/rag/fusion.py`
- `kitt/context/rag/native_search.py`
- `kitt/context/rag/semantic.py`
- `tests/test_code_rag.py`
- `docs/CODE_RAG.md`

### Substituídos

- `kitt/context/retrieval.py`
- `kitt/context_engine/engine.py`

## Validação mínima

```bash
python -m compileall -q kitt
pytest -q tests/test_code_rag.py
pytest -q
cargo test --workspace
```

O último comando é uma regressão do mecanismo nativo; esta implementação não altera código Rust.

## Classificação de intenções PT-BR, inglês e espanhol (experimental)

O classificador local é **opt-in**, independente do RAG. O modo padrão continua desligado:
```bash
export KITT_SEMANTIC_MODE=auto  # off | auto | local
export KITT_SEMANTIC_LOCAL_MODEL=qllama/multilingual-e5-small:q8_0
export KITT_SEMANTIC_LOCAL_URL=http://127.0.0.1:11434
```
- `off`: mantém a classificação generativa anterior. A preservação literal continua sempre ativa.
- `auto`: tenta embeddings locais para intenções elegíveis, e chama o classificador generativo apenas em caso de abstenção.
- `local`: não substitui a classificação local por outra chamada de classificação generativa; a abstenção leva ao fallback determinístico. A execução do agente ainda pode usar um LLM remoto conforme a política do provedor.
- Para reverse-proxy, a classificação generativa auxiliar continua desativada (`LLM_FIRST`); o pedido humano chega sem reescrita.
- As chamadas a `/api/embed` são somente por HTTP loopback, sem proxy de ambiente e sem seguir redirects. Não existem pulls automáticos.
- O E5 recebe `query:` para consultas e protótipos e `passage:` para documentos. Modelos não E5 não recebem prefixos artificialmente.
- A similaridade por cosseno é **score de ranqueamento, não probabilidade calibrada**. Os thresholds 0.87 e margem 0.035 são **provisórios**. O caminho local não deve ser habilitado globalmente até avaliação com conjunto retido, incluindo casos negativos, três idiomas e comportamento com pesos reais.
- Negação, continuidade, pedidos longos e ambiguidade têm regras conservadoras de abstenção; isto não é uma prova de equivalência semântica.
- O pedido original é sempre preservado em execução. A classificação não altera autorizações de ferramenta.
- O classificador mantém protótipos em RAM por processo; não armazena texto do usuário em cache persistente.

### Limitações e testes ainda exigidos
Este PR não fornece descoberta vetorial global de código, seleção semântica completa de skills/memória, menu TUI, diagnóstico com preparação/cancelamento ou benchmark de tokens por tarefa. A implementação não está concluída até que esses itens, os testes Python 3.14 e a avaliação de inferência Ollama real estejam validados. Não atribuir ganho de chamadas/tokens às execuções sem medi-lo.

Reproduzir verificações de regressão:
```bash
python -m pytest -q tests/test_multilingual_semantic_fidelity.py tests/test_local_semantic_integration.py
python -m compileall -q kitt tests
python packaging/verify_cleanroom.py
```
