# K.I.T.T. — Knowledge & Inference Task Tool (`kitt-agent-cli`)

> **Knight Rider Subsystem • Autonomous AI Coding Architecture for Local & Cloud LLMs**

K.I.T.T. is a high-performance, autonomous CLI coding agent built from the ground up in Python with zero external runtime dependencies. Inspired by the best of Aider and OpenClaude, K.I.T.T. optimizes local model execution (e.g., Ollama) using structural context indexing, dual-model task routing, persistent memory, and a commercial Agent Skills system.

---

## ⚡ Key Architecture & Features

- 🧠 **Dual-Model Task Router (`/setup-models`)**:
  - Automatically routes sub-tasks between small local models (e.g. `qwen2.5:7b-instruct` for context gathering/summaries) and large execution models (e.g. `qwen2.5:32b-instruct` for diff generation).
  - Mutually exclusive role assignments (`main_chat`, `context`, `commit`, `edit`, `code_generation`).

- 🗺️ **Context Engine (`/repomap`)**:
  - AST symbol graph extraction (Python, TypeScript, JavaScript, Go, Rust, C/C++) with PageRank file scoring.
  - Automatically trims repo context to exact symbol signatures to maximize token budget efficiency.

- 📝 **Structured `SEARCH/REPLACE` Diffs**:
  - Precise block diff parser and applier with fuzzy-whitespace tolerance designed specifically for local LLMs.

- 📌 **Persistent Memory System (`/memory`, `/remember`)**:
  - Remembers project-level and global coding guidelines, patterns, and rules across sessions (`.kitt/memory/project_memory.md`).

- 🔌 **Commercial Agent Skills System (`/skills`, `/setup-skills`, `/skill-install`)**:
  - Full support for commercial `SKILL.md` format (YAML frontmatter + instructions).
  - Git skill installer: clone skills directly from any GitHub or Git repository (`/skill-install owner/repo`).
  - Interactive checkbox configurator to toggle mandatory always-on skills (`caveman`, `ponytail`, `rtk`).

- 🖥️ **Interactive Dropdown CLI (`prompt_toolkit`)**:
  - Knight Rider themed terminal interface.
  - Auto-opening floating completion dropdown with two-column layout (`command` - `description`) and `❯ ` cursor.

---

## 🚀 Quick Start

### 1. Run Directly
```bash
./bin/kitt
```

### 2. Run Single Prompt (`-p`)
```bash
./bin/kitt -p "/router"
./bin/kitt -p "/repomap"
```

---

## 🛠️ Slash Commands Reference

| Command | Description |
|---|---|
| `/add <files>` | Add explicit files to active chat context |
| `/drop <files>` | Remove explicit files from active chat context |
| `/files` | List active context files |
| `/repomap` | Print AST symbol graph of repository |
| `/model [<name>]` | View or update active Main Chat model |
| `/setup-models` | Interactive provider model & exclusive role setup |
| `/router` | Display active dual-model task routing setup |
| `/memory` | Display persistent project & global memory rules |
| `/remember <rule>` | Add persistent rule to project memory |
| `/clear-memory` | Reset project memory guidelines |
| `/skills` | List installed agent skills |
| `/setup-skills` | Interactive checkbox setup for mandatory skills |
| `/skill-install <git_url>` | Install skill from Git repo URL or `user/repo` |
| `/skill-remove <name>` | Uninstall an agent skill |
| `/diff` | Show uncommitted git diff |
| `/commit [<msg>]` | Create automatic git commit with AI message |
| `/undo` | Revert recent uncommitted changes |
| `/run <cmd>` | Execute shell command in workspace |
| `/ask <prompt>` | Ask question without making code edits |
| `/code <prompt>` | Force code editing mode with `SEARCH/REPLACE` diffs |
| `/clear` | Reset conversation history |
| `/help` | Display slash command menu |
| `/exit` | Exit K.I.T.T. subsystem |

---

## 📄 License
MIT
