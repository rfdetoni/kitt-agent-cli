# Manual do K.I.T.T. Agent CLI (`kitt-agent-cli`)

> Agente de desenvolvimento autônomo local-first, Pair Programmer de terminal com interface TUI moderna, Context Engine (Aider-style repo map), Task Router, execução segura de ferramentas e memória persistente.

---

## 1. Visão Geral e Arquitetura

O **`kitt-agent-cli`** é a ferramenta de linha de comando para desenvolvedores, fornecendo capacidades avançadas de edição, refatoração, navegação em repositórios massivos e execução de testes.

```text
+------------------------------------------------------------------------------------+
|                         Terminal UI (prompt_toolkit / Rich)                        |
|   - Modo Chat & Linha de Comando                                                   |
|   - Model Setup Popup (Ctrl+Alt++) / Seletor de Provedores                         |
|   - Visualização de Diffs Interativa                                               |
+------------------------------------------+-----------------------------------------+
                                           |
                                           v
+------------------------------------------------------------------------------------+
|                            KittRuntime & TurnProcessor                             |
|                                                                                    |
|  [Context Engine]         [Task Router]             [Tool Registry & Security]     |
|  - Indexer SQLite         - Perfil de Contexto      - Leitura/Escrita Bounded      |
|  - Ranking Semântico      - Perfil de Execução      - Políticas de Aprovação (ASK) |
|  - Estimador de Tokens    - Routing Dinâmico        - Isolamento de Subprocessos   |
+------------------------------------------+-----------------------------------------+
                                           |
                                           v
                         +-----------------------------------+
                         | SQLite Store / Credential Registry|
                         | - Schema V1 Canônico              |
                         | - Lock In-Process Cross-Platform  |
                         +-----------------------------------+
```

---

## 2. Requisitos de Sistema

- **Python**: 3.12, 3.13 ou 3.14 (com `venv`, `pip`)
- **Git**: 2.30+
- **Provedor LLM**: Ollama local ou servidor remoto (ex: `http://localhost:11434` ou `http://192.168.100.51:11434`)

---

## 3. Instalação Passo a Passo por Sistema Operacional

### 🐧 A. LINUX (Ubuntu/Debian/Fedora/Arch)

```bash
# 1. Criar ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# 2. Instalar dependências em modo editável
pip install --upgrade pip
pip install -e ".[dev]"

# 3. Testar a instalação
kitt --version || python3 -m kitt --version
```

### 🍏 B. macOS (Homebrew / Terminal)

```bash
# 1. Criar ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# 2. Instalar dependências
pip install -e ".[dev]"

# 3. Executar o agente
./bin/kitt
```

### 🪟 C. WINDOWS (PowerShell)

```powershell
# 1. Criar ambiente virtual Python
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Instalar pacote em modo editável
pip install -e ".[dev]"

# 3. Executar o agente
python -m kitt
```

---

## 4. Configuração de Roteamento de Modelos (`.kitt-router.json`)

O arquivo `.kitt-router.json` permite separar modelos leves para leitura de contexto e modelos pesados para geração de código:

```json
{
  "profiles": {
    "context": {
      "backend": "ollama",
      "model": "qwen2.5-coder:1.5b",
      "base_url": "http://localhost:11434",
      "context_window": 8192,
      "max_output_tokens": 1200,
      "temperature": 0.0
    },
    "execute": {
      "backend": "ollama",
      "model": "qwen2.5-coder:14b",
      "base_url": "http://localhost:11434",
      "context_window": 32768,
      "max_output_tokens": 4096,
      "temperature": 0.0
    }
  },
  "routing": {
    "context-gather": "context",
    "summarize": "context",
    "code-generation": "execute",
    "code-edit": "execute",
    "validate-diff": "context",
    "chat": "execute"
  }
}
```

---

## 5. Guia de Uso da Interface TUI e Comandos

### Iniciando a TUI Interativa:
```bash
kitt
```

### Comandos Rápidos na Linha de Comando:
```bash
# Executar prompt direto no terminal
kitt -p "Escreva uma função em Python para calcular Fibonacci com cache LRU"

# Executar diagnóstico completo do agente e do ambiente
kitt doctor

# Resetar estado e migrar banco para Schema V1 canônico
kitt doctor --reset-state

# Indexar o repositório atual
kitt index --rebuild
```

### Atalhos na TUI:
- **`Ctrl+Alt++`**: Abre o **Model Setup Popup** para alternar modelos e URLs de provedor.
- **`F10`**: Alterna entre o modo Mouse TUI e o modo de seleção nativa do terminal.
- **`Tab` / `Shift+Tab`**: Navega entre campos e botões.

---

## 6. Testes Remotos em Ambientes Windows e macOS

Conforme as regras do repositório, testes de compatibilidade em Windows e macOS podem ser executados sob demanda no servidor dedicado:

```bash
# Executar validação no container Windows (dockurr/windows)
python3 ../scripts/remote_os_test.py --os windows --component kitt-agent-cli

# Executar validação no container macOS (dockurr/macos)
python3 ../scripts/remote_os_test.py --os macos --component kitt-agent-cli
```

---

## 7. Validação e Testes Locais
```bash
# Suíte completa de testes unitários
python -m unittest discover -s tests -v

# Suíte de arquitetura Prime e segurança
python -m unittest discover -s tests/prime_architecture -v

# Benchmarks de escalabilidade e economia de tokens
python benchmarks/scale_benchmark.py --files 1000
python benchmarks/safe_runtime_benchmark.py
```
