# Internal Notes — OpenClaude Codebase Mapping (Fase 0)

## 1. Definição e Configuração de Agentes

### Módulos principais
- `src/tools/AgentTool/loadAgentsDir.ts`: Responsável pelo carregamento, validação (via Zod `AgentJsonSchema`) e parsing do frontmatter/JSON dos agentes.
- `src/tools/AgentTool/builtInAgents.ts`: Contém os agentes nativos embutidos.
- `src/tools/AgentTool/AgentTool.tsx`, `runAgent.ts` e `forkSubagent.ts`: Gerenciam a instância, execução, ciclo de vida e estado do subagente.

### Estrutura de Configuração do Agente
Cada agente pode definir:
- `description`: Descrição do propósito do agente.
- `prompt`: Instrução base / system prompt.
- `model`: Modelo de linguagem específico (ou `'inherit'` para herdar do pai).
- `tools` / `disallowedTools`: Whitelist ou blacklist de ferramentas permitidas.
- `permissionMode`, `effort`, `maxTurns`, `maxSteps`, `mcpServers`, `hooks`, `skills`.

---

## 2. Roteamento "Agente → Modelo" e Providers

### Módulos principais
- `src/utils/model/model.ts`: Lógica de resolução do modelo (`getMainLoopModel`, `getSmallFastModel`, `getProviderRequestModel`).
- `src/utils/providerProfiles.ts` e `src/utils/providerProfile.ts`: Gestão de perfis de provedores salvos no arquivo `.openclaude-profile.json` ou na configuração global.
- Suporta múltiplos backends nativos: `ollama`, `anthropic`, `openai`, `gemini`, `bedrock`, `vertex`, `github`, `mistral`, etc.

### Como o roteamento funciona hoje
1. O modelo global da sessão é determinado pelo parâmetro `--model` ou perfil de provider ativo.
2. Quando um subagente é invocado via `AgentTool`, ele pode sobrescrever o modelo usando o campo `model` da sua definição.
3. As chamadas ao provedor (via SDK/HTTP) convertem o nome do modelo para as configurações de endpoint e chave do perfil ativo.

---

## 3. Construção do Payload de Contexto pelas Tools

### Módulos principais
- `src/services/tools/toolOrchestration.ts`: Orquestra a execução concorrente ou sequencial das ferramentas chamadas pelo assistente.
- `src/tools/FileReadTool/FileReadTool.ts`: Lê o conteúdo bruto de arquivos (inteiros ou por range de linhas) e retorna o texto diretamente no resultado da ferramenta.
- `src/tools/GrepTool/` e `src/tools/GlobTool/`: Retornam caminhos de arquivos e trechos correspondentes em formato texto plano.
- `src/tools/BashTool/`: Executa comandos shell e insere stdout/stderr brutos no resultado da ferramenta.
- `src/utils/messages.js` e `src/utils/toolResultStorage.ts`: Empacotam o retorno da ferramenta como mensagem do tipo `user` / `tool_result` no array `messages` enviado na requisição do LLM.

---

## 4. Loop Principal de Turno do Agente

### Módulos principais
- `src/QueryEngine.ts`: Classe wrapper de alto nível. Controla histórico, auto-compactação (`autoCompact`), tratamento de erros e chamadas para o motor de query.
- `src/query.ts`: Função principal `query(...)`.
  - Recebe o array de `messages`, `systemPrompt`, `tools` e `model`.
  - Dispara a requisição streaming para a API do modelo (Anthropic/OpenAI/Ollama/Gemini).
  - Intercepta blocos de `tool_use`.
  - Executa as ferramentas via `runTools()`.
  - Anexa os `tool_result` ao histórico.
  - Re-invoca iterativamente a API do LLM até o assistente finalizar o turno ou atingir os limites configurados (`maxTurns`/`maxSteps`).

---

## 5. Arquitetura de Plugins e MCP

### Módulos principais
- `src/utils/plugins/pluginLoader.ts` e `installedPluginsManager.ts`: Carregam plugins locais ou do marketplace, permitindo estender agentes, comandos, skills e hooks.
- `src/services/mcp/` e `mcpPluginIntegration.ts`: Suporte ao Model Context Protocol (MCP) conectando servidores externos via stdio/SSE para registrar ferramentas dinâmicas.

### Decisão de Arquitetura para o Context Engine e Router
O **Context Engine** e o **Task Router** devem ser implementados como **módulos core** (`src/context-engine/`, `src/edit-format/`, `src/router/`), e não como plugins externos. Motivo:
- Precisam se integrar profundamente no loop interno em `src/query.ts` e `src/QueryEngine.ts`.
- Precisam interceptar a leitura de arquivos e substituir o payload de contexto antes de enviar para o LLM de execução.
- Precisam alternar dinamicamente os perfis de modelo do Ollama local (`context` vs `execute`) a cada passo do loop do agente.
