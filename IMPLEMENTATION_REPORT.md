# KITT Prime + Safe Python — relatório da implementação

## Resultado

Esta iteração transforma a camada Prime anteriormente composta por stubs em serviços
executáveis e conectados por um único `KittRuntime`. O núcleo continua Standard-Library-first
e o Safe Python continua sendo um interpretador AST sem `eval`, `exec`, imports, arquivos,
rede ou subprocessos.

Validação final:

- `python3 -m compileall -q kitt tests`
- `python3 -m unittest discover -s tests -v`
- 69 testes aprovados.

## Correções críticas

- O construtor do `TurnProcessor` aceita injeções reais e a CLI usa `KittRuntime.build()`.
- `arun_turn()` entrega eventos por fila assíncrona limitada enquanto provedores bloqueantes
  rodam fora do event loop.
- Migrações v2, v3 e v4 atualizam bancos v1 existentes; `schema_info` legado também é reparado.
- O cursor `active_entry_id` define o branch ativo e é atualizado na mesma transação do append.
- Aprovações são vinculadas ao request, turno, conversa, workspace, hash dos argumentos e hashes
  dos arquivos. Expiração, replay e alteração concorrente falham de forma fechada.
- Nonces consumidos são persistidos no SQLite e sobrevivem a reinícios.
- Histórico real entra no orçamento, sem repetir o prompt atual.
- O prompt de sistema e as restrições obrigatórias nunca são truncados.
- O launcher não baixa `get-pip.py`, não cria venv e não instala pacotes.

## Serviços implementados

| Área | Implementação |
| --- | --- |
| Runtime | composição única, event bus, config e fechamento |
| Histórico | paginação SQL, árvore, fork, cursor, contexto e redaction |
| Artefatos | inline/arquivo por tamanho, SHA-256, leitura verificada e paginação |
| Compactação | branch resumido validado com retenção dos eventos recentes |
| Fila | steering prioritário, follow-up, entrega única e cancelamento |
| Metas | orçamento de tokens/turnos/tempo, critérios e quality gates |
| Harness | conhecimento versionado, propostas, aplicação e rollback |
| Filhos | profundidade, concorrência, paths, timeout, orçamento e resultado em artefato |
| Skills | discovery e carregamento progressivo por relevância |
| Ferramentas | search, repo map, artefatos, fila, metas, processos argv e protocolo local |
| Métricas | economia bruta/líquida, latência e persistência serializada |

## Limites intencionais

- Child agents usam um worker injetado. A escolha do modelo e a estratégia de prompt ficam no
  chamador, preservando separação e testabilidade.
- Quality gates armazenam `argv`; a execução deve passar pelo `ProcessRunner` e pela política.
- A compactação determinística pode receber um summarizer pequeno injetado, mas valida fatos
  obrigatórios antes de mudar o cursor.
- Dependências externas continuam opcionais. Nenhuma é necessária para os 69 testes.
