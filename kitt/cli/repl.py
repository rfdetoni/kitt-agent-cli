import os
import sys
import glob
import subprocess
from pathlib import Path
from typing import List, Dict, Set

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

from kitt.context_engine.engine import ContextEngine
from kitt.edit_format.parser import SearchReplaceParser
from kitt.edit_format.applier import DiffApplier
from kitt.router.router import TaskRouter
from kitt.router.model_selector import ModelConfigurator
from kitt.memory.memory_manager import MemoryManager
from kitt.skills.skill_manager import SkillManager
from kitt.domain.entities import TaskStep
from kitt.llm.client import LLMClient

# Knight Rider Red LED Scanner & Tech Dashboard Banner
KITT_BANNER_TEMPLATE = """
\033[1;31m  ┌─────────────────────────────────────────────────────────────┐
  │  \033[1;91m[░▒▓█\033[1;31m██████████████████\033[1;91m█▓▒░]\033[1;31m   K.I.T.T. SYSTEM ONLINE    │
  └─────────────────────────────────────────────────────────────┘\033[0m
  \033[1;37mK.I.T.T.\033[0m — \033[1;36mKnowledge & Inference Task Tool\033[0m \033[90mv1.0.0\033[0m
  \033[90mKnight Rider Subsystem • Autonomous AI Coding Architecture\033[0m

  \033[1;32m●\033[0m \033[1;37mWorkspace\033[0m      : \033[36m{workspace}\033[0m \033[90m(git: {git_branch})\033[0m
  \033[1;33m●\033[0m \033[1;37mContext Model\033[0m  : \033[33m{context_model}\033[0m \033[90m[Gather & Summarize]\033[0m
  \033[1;31m●\033[0m \033[1;37mExecute Model\033[0m  : \033[31m{execute_model}\033[0m \033[90m[SEARCH/REPLACE Diffs]\033[0m
  \033[1;34m●\033[0m \033[1;37mEngine Status\033[0m  : \033[34mRepoMap AST Indexer Active • Router Ready\033[0m

  \033[90mType \033[33m/\033[90m for floating dropdown slash commands menu.\033[0m
"""

SYSTEM_PROMPT_TEMPLATE = """You are K.I.T.T. (Knowledge & Inference Task Tool), an advanced autonomous AI coding assistant.
You write clean, high-performance, maintainable code.

When editing code, ALWAYS emit changes in SEARCH/REPLACE diff blocks:

path/to/file.ext
<<<<<<< SEARCH
exact original text in target file
=======
new updated text
>>>>>>> REPLACE

{memory_context}

{skills_context}

{explicit_files_context}

Context Engine Repository Map:
{context_map}
"""

SLASH_COMMANDS = {
    "/add": "Add files to active chat context",
    "/drop": "Remove files from active chat context",
    "/files": "List active context files",
    "/memory": "Display persistent project & global memory rules",
    "/remember": "Add persistent rule or guideline to project memory",
    "/clear-memory": "Reset persistent project memory rules",
    "/skills": "List installed commercial agent skills",
    "/setup-skills": "Interactive checkbox setup for mandatory skills (caveman, ponytail, rtk, etc.)",
    "/skill-install": "Install skill from Git repo URL or GitHub user/repo",
    "/skill-remove": "Remove an installed skill",
    "/repomap": "Print AST symbol graph of repository",
    "/model": "View or switch active LLM model",
    "/setup-models": "Interactive provider model & exclusive role setup",
    "/router": "Display active dual-model task routing setup",
    "/diff": "Show uncommitted git diff",
    "/commit": "Create automatic git commit with AI message",
    "/undo": "Revert recent git commit or uncommitted changes",
    "/run": "Execute shell command in workspace",
    "/ask": "Ask question without making code edits",
    "/code": "Force code editing mode with SEARCH/REPLACE diffs",
    "/clear": "Reset conversation history",
    "/help": "Display full K.I.T.T. slash command menu",
    "/exit": "Shut down K.I.T.T. subsystem",
    "/quit": "Shut down K.I.T.T. subsystem",
}

class CustomCompleter(Completer):
    """CustomCompleter supporting slash commands & file path autocompletion."""

    def __init__(self, commands: Dict[str, str], root_dir: str = "."):
        self.commands = commands
        self.root_dir = root_dir

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        if text.startswith("/add ") or text.startswith("/drop "):
            parts = text.split(" ", 1)
            file_prefix = parts[1] if len(parts) > 1 else ""
            matches = glob.glob(f"{file_prefix}*")
            for m in matches:
                display_path = m + ("/" if os.path.isdir(m) else "")
                yield Completion(
                    text=display_path,
                    start_position=-len(file_prefix),
                    display=display_path,
                    display_meta="file"
                )
            return

        if text.startswith("/"):
            word = text
            for cmd, desc in self.commands.items():
                if cmd.startswith(word):
                    yield Completion(
                        text=cmd,
                        start_position=-len(word),
                        display=f"❯ {cmd}" if document.text_before_cursor == cmd else cmd,
                        display_meta=desc
                    )

PROMPT_STYLE = Style.from_dict({
    'completion-menu': 'bg:default fg:#8a8a8a',
    'completion-menu.completion': 'bg:default fg:#8a8a8a',
    'completion-menu.meta.completion': 'bg:default fg:#8a8a8a',
    'completion-menu.completion.current': 'bg:#a4b5fd fg:#000000 bold',
    'completion-menu.meta.completion.current': 'bg:#a4b5fd fg:#000000',
    'scrollbar.background': 'bg:default',
    'scrollbar.button': 'bg:#8a8a8a',
})

class KittREPL:
    """Knight Rider themed Interactive REPL with prompt_toolkit floating completion dropdown & Memory Manager."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = root_dir
        self.context_engine = ContextEngine()
        self.diff_parser = SearchReplaceParser()
        self.diff_applier = DiffApplier()
        self.router = TaskRouter(root_dir=root_dir)
        self.memory = MemoryManager(root_dir=root_dir)
        self.skill_manager = SkillManager(root_dir=root_dir)
        self.messages: List[Dict[str, str]] = []
        self.explicit_files: Set[str] = set()

        self.completer = CustomCompleter(SLASH_COMMANDS, root_dir=root_dir)
        self.session = PromptSession(
            completer=self.completer,
            complete_while_typing=True,
            style=PROMPT_STYLE
        )

    def _get_git_branch(self) -> str:
        try:
            res = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.root_dir,
                capture_output=True,
                text=True,
                timeout=2
            )
            branch = res.stdout.strip()
            return branch if branch else "main"
        except Exception:
            return "detached"

    def render_startup_dashboard(self):
        abs_root = str(Path(self.root_dir).resolve())
        branch = self._get_git_branch()
        ctx_profile = self.router.config.profiles.get("context")
        exe_profile = self.router.config.profiles.get("execute")

        ctx_str = f"{ctx_profile.backend}/{ctx_profile.model}" if ctx_profile else "ollama/qwen2.5:7b-instruct"
        exe_str = f"{exe_profile.backend}/{exe_profile.model}" if exe_profile else "ollama/qwen2.5:32b-instruct"

        banner = KITT_BANNER_TEMPLATE.format(
            workspace=abs_root,
            git_branch=branch,
            context_model=ctx_str,
            execute_model=exe_str
        )
        print(banner)

    def print_slash_menu(self):
        print("\n\033[1;37mAvailable K.I.T.T. Slash Commands Menu:\033[0m")
        for cmd, desc in SLASH_COMMANDS.items():
            print(f"  \033[1;33m{cmd:<15}\033[0m \033[90m-\033[0m \033[37m{desc}\033[0m")
        print()

    def start(self):
        self.render_startup_dashboard()
        while True:
            try:
                prompt_formatted = HTML('<ansired><b>kitt</b></ansired><ansigreen><b>&gt;</b></ansigreen> ')
                user_input = self.session.prompt(prompt_formatted).strip()
                if not user_input:
                    continue

                if user_input.startswith('/'):
                    if self._handle_slash_command(user_input):
                        break
                    continue

                self.process_turn(user_input)
            except (KeyboardInterrupt, EOFError):
                print("\n\033[1;31mK.I.T.T. Subsystem offline.\033[0m")
                break

    def _handle_slash_command(self, raw_cmd: str) -> bool:
        parts = raw_cmd.split(maxsplit=1)
        cmd_name = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd_name in ['/exit', '/quit']:
            print("\033[1;31mK.I.T.T. Subsystem offline.\033[0m")
            return True

        elif cmd_name == '/add':
            if not arg:
                print("\033[33mUsage: /add <file_path1> <file_path2>\033[0m")
            else:
                for f in arg.split():
                    p = Path(self.root_dir) / f
                    if p.exists():
                        self.explicit_files.add(f)
                        print(f"\033[32m+ Added file to context: {f}\033[0m")
                    else:
                        print(f"\033[31mFile not found: {f}\033[0m")

        elif cmd_name == '/drop':
            if not arg:
                print("\033[33mUsage: /drop <file_path>\033[0m")
            else:
                for f in arg.split():
                    if f in self.explicit_files:
                        self.explicit_files.remove(f)
                        print(f"\033[33m- Dropped file from context: {f}\033[0m")
                    else:
                        print(f"\033[31mFile was not in context: {f}\033[0m")

        elif cmd_name in ['/files', '/ls']:
            if not self.explicit_files:
                print("\033[90mNo explicit files added. (Use /add <file>)\033[0m")
            else:
                print("\033[1;34m--- Explicit Context Files ---\033[0m")
                for f in sorted(self.explicit_files):
                    print(f"  • {f}")

        elif cmd_name == '/memory':
            mem_text = self.memory.get_memory_context()
            print(f"\n\033[1;34m--- K.I.T.T. Persistent Memory ---\033[0m\n{mem_text}\n\033[1;34m---------------------------------\033[0m\n")

        elif cmd_name == '/remember':
            if not arg:
                print("\033[33mUsage: /remember <rule or guideline>\033[0m")
            else:
                self.memory.add_project_memory(arg)
                print(f"\033[32m+ Remembered guideline in project memory: '{arg}'\033[0m")

        elif cmd_name == '/clear-memory':
            self.memory.clear_project_memory()
            print("\033[33mProject memory guidelines reset.\033[0m")

        elif cmd_name == '/skills':
            skills = self.skill_manager.list_skills()
            if not skills:
                print("\033[90mNo skills installed. Use /skill-install <git_url> to install a skill.\033[0m")
            else:
                print("\n\033[1;34m--- Installed Commercial Agent Skills ---\033[0m")
                for s in skills:
                    print(f" • \033[1;33m{s.name}\033[0m (v{s.version} by {s.author})")
                    print(f"   \033[37m{s.description}\033[0m")
                    print(f"   \033[90mPath: {s.path}\033[0m")
                print("\033[1;34m----------------------------------------\033[0m\n")

        elif cmd_name == '/setup-skills':
            self.skill_manager.run_interactive_checkbox_config()

        elif cmd_name == '/skill-install':
            if not arg:
                print("\033[33mUsage: /skill-install <git_repo_url_or_shorthand> [--global]\033[0m")
                print("Examples:")
                print("  /skill-install owner/repository")
                print("  /skill-install https://github.com/owner/repository.git")
            else:
                is_global = "--global" in arg
                git_url = arg.replace("--global", "").strip()
                print(f"\033[90mFetching and installing skill from: {git_url}...\033[0m")
                try:
                    s = self.skill_manager.install_from_git(git_url, is_global=is_global)
                    print(f"\033[32m✓ Installed skill '{s.name}' (v{s.version}) successfully!\033[0m")
                except Exception as e:
                    print(f"\033[31mInstallation Error: {e}\033[0m")

        elif cmd_name == '/skill-remove':
            if not arg:
                print("\033[33mUsage: /skill-remove <skill_name>\033[0m")
            else:
                if self.skill_manager.remove_skill(arg):
                    print(f"\033[32mUninstalled skill '{arg}'.\033[0m")
                else:
                    print(f"\033[31mSkill '{arg}' not found.\033[0m")

        elif cmd_name == '/repomap':
            blocks = self.context_engine.get_relevant_context("", max_tokens=1024, root_dir=self.root_dir)
            print("\n\033[1;34m--- K.I.T.T. Repository Symbol Map ---\033[0m")
            for b in blocks:
                print(b.content)
            print("\033[1;34m--------------------------------------\033[0m\n")

        elif cmd_name == '/model':
            if not arg:
                profile_key = self.router.config.routing.get("chat", "model_a")
                profile = self.router.config.profiles.get(profile_key) or self.router.config.profiles.get("execute")
                model_name = profile.model if profile else "qwen2.5:32b-instruct"
                print(f"\n\033[1;37mActive Main Chat Model:\033[0m \033[1;36m{model_name}\033[0m")
                print("\033[90mUse \033[33m/model <model_name>\033[90m to update Main Chat model, or \033[33m/setup-models\033[90m for role configuration.\033[0m\n")
            else:
                profile_key = self.router.config.routing.get("chat", "model_a")
                if profile_key in self.router.config.profiles:
                    self.router.config.profiles[profile_key].model = arg
                elif "execute" in self.router.config.profiles:
                    self.router.config.profiles["execute"].model = arg
                print(f"\033[32mActive Main Chat Model updated to: {arg}\033[0m")

        elif cmd_name == '/setup-models':
            configurator = ModelConfigurator(root_dir=self.root_dir)
            configurator.run_interactive_setup()
            self.router = TaskRouter(root_dir=self.root_dir)

        elif cmd_name == '/router':
            print("\n\033[1;34m--- Task Router Configuration ---\033[0m")
            for k, v in self.router.config.profiles.items():
                print(f" Profile '{k}': {v.backend} ({v.model})")
            print(" Routing map:")
            for t, p in self.router.config.routing.items():
                print(f"   {t} -> {p}")
            print("\033[1;34m---------------------------------\033[0m\n")

        elif cmd_name == '/diff':
            res = subprocess.run(["git", "diff"], cwd=self.root_dir, capture_output=True, text=True)
            if res.stdout:
                print(f"\n\033[1;34m--- Git Uncommitted Diff ---\033[0m\n{res.stdout}")
            else:
                print("\033[90mNo uncommitted changes in working directory.\033[0m")

        elif cmd_name == '/commit':
            res = subprocess.run(["git", "diff"], cwd=self.root_dir, capture_output=True, text=True)
            if not res.stdout:
                print("\033[90mNo changes to commit.\033[0m")
            else:
                msg = arg or "feat: automated updates via K.I.T.T."
                subprocess.run(["git", "add", "."], cwd=self.root_dir)
                subprocess.run(["git", "commit", "-m", msg], cwd=self.root_dir)
                print(f"\033[32mCommitted changes with message: '{msg}'\033[0m")

        elif cmd_name == '/undo':
            subprocess.run(["git", "checkout", "--", "."], cwd=self.root_dir)
            print("\033[33mReverted uncommitted file changes in workspace.\033[0m")

        elif cmd_name == '/run':
            if not arg:
                print("\033[33mUsage: /run <shell_command>\033[0m")
            else:
                print(f"\033[90mExecuting: {arg}\033[0m")
                res = subprocess.run(arg, shell=True, cwd=self.root_dir, capture_output=True, text=True)
                if res.stdout:
                    print(res.stdout)
                if res.stderr:
                    print(f"\033[31m{res.stderr}\033[0m")

        elif cmd_name == '/ask':
            if arg:
                self.process_turn(f"[QUESTION ONLY - NO CODE EDITS]: {arg}")

        elif cmd_name == '/code':
            if arg:
                self.process_turn(f"[CODE EDIT REQUIRED]: {arg}")

        elif cmd_name == '/clear':
            self.messages.clear()
            print("\033[33mConversation history cleared.\033[0m")

        elif cmd_name in ['/help', '/']:
            self.print_slash_menu()

        else:
            print(f"\033[31mUnknown command: {raw_cmd}. Type /help for menu.\033[0m")

        return False

    def _build_explicit_files_context(self) -> str:
        if not self.explicit_files:
            return ""

        context_lines = ["Explicitly Attached Context Files:"]
        for rel_path in sorted(self.explicit_files):
            full_p = Path(self.root_dir) / rel_path
            if full_p.exists() and full_p.is_file():
                try:
                    content = full_p.read_text(encoding='utf-8', errors='ignore')
                    context_lines.append(f"\n--- {rel_path} ---\n{content}\n--- end {rel_path} ---")
                except Exception:
                    continue
        return "\n".join(context_lines)

    def process_turn(self, user_prompt: str):
        self.messages.append({"role": "user", "content": user_prompt})

        # 1. Memory, Skills, Context Engine & Explicit Files retrieval
        memory_ctx = self.memory.get_memory_context()
        skills_ctx = self.skill_manager.get_skills_summary_prompt()
        explicit_ctx = self._build_explicit_files_context()
        context_blocks = self.context_engine.get_relevant_context(user_prompt, max_tokens=2048, root_dir=self.root_dir)
        context_map_str = "\n\n".join(b.content for b in context_blocks)

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            memory_context=memory_ctx,
            skills_context=skills_ctx,
            explicit_files_context=explicit_ctx,
            context_map=context_map_str
        )

        # 2. Task Router selection
        step = TaskStep(prompt=user_prompt)
        task_type, profile_name, profile = self.router.route(step)
        print(f"\033[90m[Task Router: {task_type} -> profile '{profile_name}' ({profile.model})]\033[0m")

        # 3. LLM client execution
        llm = LLMClient(profile)
        print("\033[1;31mkitt:\033[0m ", end="", flush=True)

        full_response = ""
        for chunk in llm.chat_stream(self.messages, system_prompt=system_prompt):
            print(chunk, end="", flush=True)
            full_response += chunk
        print()

        self.messages.append({"role": "assistant", "content": full_response})

        # 4. Parse & apply SEARCH/REPLACE diff blocks
        edit_blocks = self.diff_parser.parse(full_response)
        if edit_blocks:
            print(f"\033[1;33mApplying {len(edit_blocks)} SEARCH/REPLACE diff edit block(s)...\033[0m")
            result = self.diff_applier.apply(edit_blocks, root_dir=self.root_dir)
            if result.success:
                if result.applied_files:
                    print(f"\033[32mApplied edits to: {', '.join(result.applied_files)}\033[0m")
                if result.created_files:
                    print(f"\033[32mCreated files: {', '.join(result.created_files)}\033[0m")
                if result.deleted_files:
                    print(f"\033[32mDeleted files: {', '.join(result.deleted_files)}\033[0m")
            else:
                print("\033[31mEdit Application Errors:\033[0m")
                for err in result.errors:
                    print(f"  - {err}")
