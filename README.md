# K.I.T.T. Agent CLI

> Local-first autonomous coding agent and terminal pair programmer.

Supports Ollama, OpenRouter, and OpenAI-compatible providers, structured workspace context indexing, tool execution, single-use approval grants, persistent conversation history, Dreaming mode memory consolidation, and seamless integration with the KITT ecosystem.

---

## ✨ Features

- **Local-First & Multi-Model**: Native support for local Ollama models (`qwen2.5-coder`, `deepseek-coder`, etc.) and remote endpoints.
- **Shared Memory via Protocol-v1**: Seamlessly integrates with the ecosystem's `kitt-memory` backend via `kittd` authenticated loopback IPC (`127.0.0.1:41827`).
- **Standalone Resilience**: If `kittd` is offline, `kitt-agent-cli` operates with 100% standalone autonomy using local SQLite storage.
- **KITT Control Center Integration**: Loads settings overrides layered as `defaults < overlay < env < CLI` from `${XDG_CONFIG_HOME:-~/.config}/kitt/control-center/overrides.json`.
- **Security & Safety**:
  - Strict loopback validation prevents auth token egress to remote hosts.
  - Process-control environment variable protection (forbids tampering with `PATH`, `LD_PRELOAD`, `PYTHONPATH`).
  - Single-use ApprovalGrant tokens and P0 command security boundaries.
- **Context Engine**: Fast repo map, AST indexing, FTS5 lexical search, budget invariant enforcement, and compact multi-turn rolling context.

---

## 🚀 Installation & Setup

### 1. Create Virtual Environment

```bash
cd kitt-agent-cli
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Package & Dependencies

```bash
pip install --upgrade pip
pip install -e .
```

---

## 💻 Usage

### Interactive Pair Programmer

```bash
# Launch interactive REPL / TUI in current repository
kitt

# Or specify a target repository root
kitt --root /path/to/project
```

### Direct CLI Commands

```bash
# Display available models and providers
kitt models

# Run diagnostics and doctor check
kitt doctor

# View help and options
kitt --help
```

---

## ⚙️ Configuration & Precedence

`kitt-agent-cli` dynamically merges configurations following this strict priority ladder:

1. **CLI Arguments**: (e.g. `--model`, `--provider`, `--root`)
2. **Environment Variables**: (e.g. `KITT_SAFE_RUNTIME`, `KITT_DAEMON`, `OPENAI_API_KEY`)
3. **KITT Control Center Overlay**: Managed centrally via `http://127.0.0.1:41828`
4. **Native Defaults**: Dataclass defaults defined in `RuntimeConfig`.

---

## 🧪 Testing & Validation

```bash
# Run complete test suite (760+ unit and integration tests)
pytest

# Run specific memory client loopback tests
pytest tests/memory/test_shared_memory_client.py

# Run control center overlay tests
pytest tests/settings/test_control_center.py
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE).
