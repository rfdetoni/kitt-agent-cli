import os
import sys
import glob
import shlex
import subprocess
from pathlib import Path
from typing import List, Dict, Set, Optional

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.styles import Style
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False
    class Completer: pass
    class Completion: pass

from kitt.context_engine.engine import ContextEngine
from kitt.edit_format.parser import SearchReplaceParser
from kitt.edit_format.applier import DiffApplier
from kitt.router.router import TaskRouter
from kitt.router.model_selector import ModelConfigurator
from kitt.memory.memory_manager import MemoryManager
from kitt.skills.skill_manager import SkillManager
from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.context_filter.prompt_budget import PromptBudget
from kitt.domain.entities import TaskStep
from kitt.llm.client import LLMClient
from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import (
    TurnEvent, TurnStarted, FilterCompleted, ContextResolved, BudgetApplied,
    ModelSelected, TextDelta, ApprovalRequired, ToolStarted, ToolCompleted,
    EditApplied, TurnCompleted, TurnFailed
)
from kitt.history.service import HistoryService
from kitt.cli.doctor import DoctorCheck

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

SLASH_COMMANDS = {
    "/new": "Start new persistent conversation",
    "/history": "List or search workspace conversations history",
    "/resume": "Resume specific conversation by index or ID",
    "/continue": "Resume most recent active conversation",
    "/conversation": "Show current active conversation details",
    "/fork": "Fork current conversation into new branch",
    "/export-conversation": "Export conversation history as markdown or JSON",
    "/doctor": "Run diagnostic check on local environment",
    "/add": "Add files to active chat context",
    "/drop": "Remove files from active chat context",
    "/files": "List active context files",
    "/memory": "Display persistent project & global memory rules",
    "/remember": "Add persistent rule or guideline to project memory",
    "/clear-memory": "Reset persistent project memory rules",
    "/skills": "List installed commercial agent skills",
    "/setup-skills": "Interactive checkbox setup for mandatory skills",
    "/skill-install": "Install skill from Git repo URL or GitHub user/repo",
    "/skill-remove": "Remove an installed skill",
    "/repomap": "Print AST symbol graph of repository",
    "/context-stats": "Display telemetry and section token budget breakdown",
    "/model": "View or switch active LLM model",
    "/setup-models": "Interactive provider model & exclusive role setup",
    "/router": "Display active dual-model task routing setup",
    "/diff": "Show uncommitted git diff",
    "/commit": "Create automatic git commit with AI message",
    "/undo": "Revert recent git commit or uncommitted changes",
    "/run": "Execute shell command in workspace",
    "/ask": "Ask question without making code edits",
    "/code": "Force code editing mode with SEARCH/REPLACE diffs",
    "/clear": "Reset active context",
    "/help": "Display full K.I.T.T. slash command menu",
    "/exit": "Shut down K.I.T.T. subsystem",
    "/quit": "Shut down K.I.T.T. subsystem",
}

class CustomCompleter(Completer):
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

if HAS_PROMPT_TOOLKIT:
    PROMPT_STYLE = Style.from_dict({
        'completion-menu': 'bg:default fg:#8a8a8a',
        'completion-menu.completion': 'bg:default fg:#8a8a8a',
        'completion-menu.meta.completion': 'bg:default fg:#8a8a8a',
        'completion-menu.completion.current': 'bg:#a4b5fd fg:#000000 bold',
        'completion-menu.meta.completion.current': 'bg:#a4b5fd fg:#000000',
        'scrollbar.background': 'bg:default',
        'scrollbar.button': 'bg:#8a8a8a',
    })
else:
    PROMPT_STYLE = None

class KittREPL:
    """Knight Rider themed Interactive REPL delegating execution to TurnProcessor.run_turn()."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = root_dir
        self.context_engine = ContextEngine()
        self.diff_parser = SearchReplaceParser()
        self.diff_applier = DiffApplier()
        self.router = TaskRouter(root_dir=root_dir)
        self.memory = MemoryManager(root_dir=root_dir)
        self.skill_manager = SkillManager(root_dir=root_dir)
        self.budget = PromptBudget(window_size=8192, reserved_output=1200)
        self.turn_processor = TurnProcessor(root_dir=root_dir)
        self.history_service = HistoryService(root_dir=root_dir)
        self.explicit_files: Set[str] = set()

        self.completer = CustomCompleter(SLASH_COMMANDS, root_dir=root_dir)
        if HAS_PROMPT_TOOLKIT:
            self.session = PromptSession(
                completer=self.completer,
                complete_while_typing=True,
                style=PROMPT_STYLE
            )
        else:
            self.session = None

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
            print(f"  \033[1;33m{cmd:<22}\033[0m \033[90m-\033[0m \033[37m{desc}\033[0m")
        print()

    def start(self):
        self.render_startup_dashboard()
        while True:
            try:
                if HAS_PROMPT_TOOLKIT and self.session:
                    prompt_formatted = HTML('<ansired><b>kitt</b></ansired><ansigreen><b>&gt;</b></ansigreen> ')
                    user_input = self.session.prompt(prompt_formatted).strip()
                else:
                    user_input = input("\033[1;31mkitt\033[1;32m>\033[0m ").strip()
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

        elif cmd_name == '/new':
            c = self.history_service.new_conversation(title=arg or "New Conversation")
            print(f"\033[32m✓ Started new conversation: [{c['id'][:8]}] {c['title']}\033[0m")

        elif cmd_name == '/history':
            convs = self.history_service.list_history(limit=20, search=arg if arg else None)
            print("\n\033[1;34m--- K.I.T.T. Persistent Conversation History ---\033[0m")
            for i, c in enumerate(convs, 1):
                marker = "*" if self.history_service.active_conversation and c["id"] == self.history_service.active_conversation["id"] else " "
                print(f" {marker} [{i}] {c['id'][:8]} | {c['title']} \033[90m({c['status']})\033[0m")
            print("\033[1;34m------------------------------------------------\033[0m\n")

        elif cmd_name in ['/resume', '/continue']:
            target = arg or "1"
            c = self.history_service.resume_conversation(target)
            if c:
                print(f"\033[32m✓ Resumed conversation: [{c['id'][:8]}] {c['title']}\033[0m")
            else:
                print(f"\033[31mConversation '{target}' not found.\033[0m")

        elif cmd_name == '/conversation':
            c = self.history_service.get_or_create_active()
            print(f"\n\033[1;34mActive Conversation:\033[0m [{c['id']}] {c['title']}")

        elif cmd_name == '/fork':
            c = self.history_service.fork_conversation(title_suffix=f" ({arg})" if arg else " (Fork)")
            print(f"\033[32m✓ Forked conversation: [{c['id'][:8]}] {c['title']}\033[0m")

        elif cmd_name == '/export-conversation':
            fmt = "json" if "json" in arg.lower() else "md"
            out = self.history_service.export_conversation(fmt=fmt)
            print(f"\n\033[1;34m--- Conversation Export ({fmt.upper()}) ---\033[0m\n{out}\n")

        elif cmd_name == '/doctor':
            chk = DoctorCheck(root_dir=self.root_dir)
            res = chk.run_diagnostics()
            print("\n\033[1;34m--- K.I.T.T. System Diagnostics ---\033[0m")
            for r in res:
                color = "\033[32m" if r["status"] == "PASS" else ("\033[33m" if r["status"] == "WARN" else "\033[31m")
                print(f" {color}[{r['status']}]\033[0m \033[1;37m{r['name']:<25}\033[0m: {r['detail']}")
            print("\033[1;34m------------------------------------\033[0m\n")

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
                print("\033[90mNo skills installed.\033[0m")
            else:
                print("\n\033[1;34m--- Installed Agent Skills ---\033[0m")
                for s in skills:
                    print(f" • \033[1;33m{s.name}\033[0m (v{s.version} by {s.author})")

        elif cmd_name == '/repomap':
            blocks = self.context_engine.get_relevant_context("", max_tokens=1024, root_dir=self.root_dir)
            print("\n\033[1;34m--- K.I.T.T. Repository Symbol Map ---\033[0m")
            for b in blocks:
                print(b.content)

        elif cmd_name == '/undo':
            cs = self.diff_applier.tracker.revert_last_changeset()
            if cs:
                print(f"\033[32m✓ Reverted K.I.T.T. ChangeSet [{cs.id}]\033[0m")
            else:
                print("\033[90mNo K.I.T.T. edit ChangeSets to revert.\033[0m")

        elif cmd_name in ['/help', '/']:
            self.print_slash_menu()

        else:
            print(f"\033[31mUnknown command: {raw_cmd}. Type /help for menu.\033[0m")

        return False

    def process_turn(self, user_prompt: str):
        active_conv = self.history_service.get_or_create_active()
        self.history_service.repo.save_message(active_conv["id"], "turn", "user", user_prompt)

        cmd = TurnCommand(
            conversation_id=active_conv["id"],
            prompt=user_prompt,
            explicit_files=self.explicit_files
        )

        full_resp = ""
        for event in self.turn_processor.run_turn(cmd):
            if isinstance(event, FilterCompleted):
                if event.filter_res:
                    t = event.filter_res.task
                    print(f"\033[90m[Filter: intent={t.intent}, confidence={t.confidence:.2f}]\033[0m")
            elif isinstance(event, ModelSelected):
                print(f"\033[90m[Router: profile={event.profile_name} ({event.model})]\033[0m")
                print("\033[1;31mkitt:\033[0m ", end="", flush=True)
            elif isinstance(event, TextDelta):
                print(event.delta, end="", flush=True)
                full_resp += event.delta
            elif isinstance(event, ApprovalRequired):
                print(f"\n\033[1;33m[ASK Confirmation Required]: Tool '{event.tool_name}' requires approval.\033[0m")
                grant = self.turn_processor.registry.issue_approval_grant(cmd.turn_id, event.tool_name, event.args)
                cmd.approval_grant = grant
                # Retry with approval grant
                for sub_event in self.turn_processor.run_turn(cmd):
                    if isinstance(sub_event, TextDelta):
                        print(sub_event.delta, end="", flush=True)
                        full_resp += sub_event.delta
                    elif isinstance(event, TurnCompleted):
                        full_resp = event.response
            elif isinstance(event, EditApplied):
                print(f"\n\033[32mApplied edit to: {', '.join(event.applied_files + event.created_files)}\033[0m")
            elif isinstance(event, TurnCompleted):
                full_resp = event.response

        print()
        if full_resp:
            self.history_service.repo.save_message(active_conv["id"], cmd.turn_id, "assistant", full_resp)
