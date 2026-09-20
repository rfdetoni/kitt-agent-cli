from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import queue
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Iterator
from kitt.domain.entities import ContextPlan, EditResult, ModelProfile, SemanticTask
from kitt.router.router import TaskRouter
from kitt.router.features import TaskFeatureExtractor
from kitt.router.models import ModelCapabilities
from kitt.router.policy import RoutingPolicy
from kitt.memory.memory_manager import MemoryManager
from kitt.skills.skill_manager import SkillManager
from kitt.skills.discovery import SkillDiscovery
from kitt.skills.loader import ProgressiveSkillLoader
from kitt.context_engine.engine import ContextEngine
from kitt.context.working_set import ConversationWorkingSetStore
from kitt.context_filter.semantic_filter import SemanticFilter
from kitt.context_filter.context_resolver import ContextResolver
from kitt.context_filter.prompt_budget import PromptBudget, TokenCounter
from kitt.context_filter.deterministic_extractor import DeterministicExtractor
from kitt.edit_format.parser import SearchReplaceParser
from kitt.tools.build_detector import BuildDetector
from kitt.tools.log_reducer import LogReducer
from kitt.tools.registry import ToolRegistry
from kitt.tools.safe_python import (
    PYTHON_TOOL_CALL_OPEN,
    parse_python_compute_call,
)
from kitt.tools.protocol import TOOL_CALL_OPEN, parse_tool_call
from kitt.tools.surface_selector import ToolSurfaceSelector
from kitt.llm.client import LLMClient
from kitt.llm.attachments import (
    AttachmentError,
    attach_to_first_user_message,
)
from kitt.core.session_state import SessionState
from kitt.core.execution_request import ExecutionRequest
from kitt.core.turn_command import TurnCommand
import uuid
from urllib.parse import urlsplit
from kitt.core.turn_events import (
    TurnEvent, TurnStarted, FilterCompleted, ContextResolved, BudgetApplied,
    ContextBuildCompleted, ModelSelected, TextDelta, ToolCallProposed, ApprovalRequired, ToolStarted, ToolCompleted,
    ThinkingStarted, ThinkingCompleted,
    EditApplied, MetricsRecorded, TurnCompleted, TurnFailed,
    TurnCancelled, TurnBlocked
)
from kitt.core.pending_action import PendingAction
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.logging import trace_event
from kitt.core.turn_execution_guard import TurnExecutionGuard
from kitt.core.turn_helpers import (
    _attachment_path_key,
    _attachment_retrieval_prompt,
    _reverse_proxy_identity,
    _same_reverse_proxy_endpoint,
    detect_chat_limit_message,
)
from kitt.core.turn_tool_loop import TurnToolLoopMixin
from kitt.core.turn_finalization import TurnFinalizationMixin
from kitt.core.turn_context import TurnContextMixin
from kitt.core.turn_model import TurnModelMixin
from kitt.security.context import ExecutionSecurityContext
from kitt.security.capabilities import (
    capabilities_for_tools,
    READ_ONLY_CAPABILITIES,
    CAP_REPO_WRITE,
    CAP_BROWSER_READ,
    CAP_BROWSER_WRITE,
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
)
from kitt.metrics.models import TurnMetrics
from kitt.metrics.cost_estimator import estimate_cost
from kitt.prompts import (
    CONTEXT_SUMMARY_SYSTEM as CONTEXT_SUMMARY_PROMPT,
    CONTEXT_SUMMARY_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

class TurnProcessor(
    TurnToolLoopMixin,
    TurnFinalizationMixin,
    TurnContextMixin,
    TurnModelMixin,
):
    """Decoupled core turn coordinator for K.I.T.T."""
    reasoning_effort: int = 50

    def __init__(
        self,
        root_dir: str = ".",
        context_client: Optional[LLMClient] = None,
        execution_client: Optional[LLMClient] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        history_service: Any = None,
        registry: Optional[ToolRegistry] = None,
        metrics_collector: Any = None,
        harness_service: Any = None,
        compaction_service: Any = None,
        memory_service: Any = None,
        skill_manager: Any = None,
        context_engine: Any = None,
        working_set: Any = None,
        config: Optional[RuntimeConfig] = None,
        workspace_id: Optional[str] = None,
        enable_context_summary: bool = False,
    ):
        self.root_path = Path(root_dir).resolve()
        self.config = config or RuntimeConfig()
        self.router = TaskRouter(root_dir=root_dir)
        self.routing_policy = RoutingPolicy()
        self.memory = memory_service or MemoryManager(
            root_dir=root_dir, persistence_enabled=self.config.persistence_enabled)
        self.skill_manager = skill_manager or SkillManager(
            root_dir=root_dir, persistence_enabled=self.config.persistence_enabled)
        self.context_engine = context_engine or ContextEngine(
            persistence_enabled=self.config.persistence_enabled
        )
        self.working_set = working_set or ConversationWorkingSetStore(
            root_dir=root_dir,
            persistence_enabled=self.config.persistence_enabled,
        )
        self.context_resolver = ContextResolver(root_dir=root_dir)
        self.deterministic_extractor = DeterministicExtractor()
        self.skill_discovery = SkillDiscovery()
        self.skill_loader = ProgressiveSkillLoader()
        self.diff_parser = SearchReplaceParser()
        self.build_detector = BuildDetector(root_dir=root_dir)
        self.log_reducer = LogReducer()
        self.registry = registry or ToolRegistry(root_dir=root_dir)
        self.diff_applier = self.registry.applier
        self.surface_selector = ToolSurfaceSelector(config=self.config)
        self.session_state = SessionState()
        self.pending_actions: Dict[str, PendingAction] = {}
        self.cancelled_turns: set[str] = set()
        self.turn_guard = TurnExecutionGuard(self.cancelled_turns)
        self.reasoning_effort: int = 50
        # Browser-session namespace for this Agent CLI window. The actual
        # reverse-proxy session is also scoped by KITT conversation_id so two
        # logical conversations can never queue incompatible cumulative
        # histories into the same browser chat.
        self._proxy_session_key = f"agent-window:{uuid.uuid4().hex}"
        self._closed = False

        self.context_client = context_client
        self.execution_client = execution_client
        self.event_callback = event_callback
        self.history_service = history_service
        self.metrics_collector = metrics_collector
        self.harness_service = harness_service
        self.compaction_service = compaction_service
        self._workspace_id = workspace_id
        self.enable_context_summary = enable_context_summary
        self._context_summary_cache: Dict[str, str] = {}
        self._cache_lock = threading.Lock()
        self._agent_trace_context: Optional[tuple[str, str]] = None
        self._adaptive_retrieval_ratio_fn: Optional[Callable[[Any, Any, TurnCommand], float]] = None
        self._routing_feedback_snapshot_fn: Optional[Callable[[Any], Dict[str, Dict[str, Any]]]] = None
        self._attachment_paths_by_turn: Dict[str, tuple[str, ...]] = {}
        self._attachment_wire_sent: set[str] = set()

    def _provider_session_key(self, profile, conversation_id: str) -> str:
        if _reverse_proxy_identity(profile):
            return f"{self._proxy_session_key}:conversation:{conversation_id}"
        return conversation_id

    @staticmethod
    def _agent_route_for_task(task, mode: str = "", prompt: str = "") -> str:
        """Pin the proxy route, with the original user mutation intent as the highest signal."""
        if mode == "plan":
            return "context-gather"

        original_prompt = str(getattr(task, "original_prompt", "") or "")
        text = (original_prompt or prompt or "").casefold()
        workspace_targets = (
            "projeto", "project", "site", "app", "aplicação", "aplicacao",
            "backend", "frontend", "front end", "repositório", "repositorio",
            "repository", "repo", "arquivo", "file", "pasta", "folder",
            "diretório", "diretorio", "directory", "código", "codigo", "code",
        )
        edit_terms = (
            "corrija", "corrigir", "conserte", "consertar", "repare", "reparar",
            "refatore", "refatorar", "atualize", "atualizar", "modifique",
            "modificar", "altere", "alterar", "edite", "editar", "remova",
            "remover", "fix", "repair", "refactor", "update", "modify",
            "change", "edit", "remove", "delete",
        )
        create_terms = (
            "crie", "criar", "cria", "implemente", "implementar", "gere",
            "gerar", "construa", "monte", "create", "build", "implement",
            "generate", "scaffold", "write", "mkdir",
        )
        if any(target in text for target in workspace_targets):
            if any(term in text for term in edit_terms):
                return "code-edit"
            if any(term in text for term in create_terms):
                return "code-generation"

        raw_intent = getattr(task, "intent", "")
        intent = str(getattr(raw_intent, "value", raw_intent) or "").upper()
        if intent == "IMPLEMENT":
            return "code-generation"
        if intent in {"DEBUG", "REFACTOR"}:
            return "code-edit"
        if intent == "TEST":
            return "validate-diff"
        return "chat"

    @property
    def workspace_id(self) -> str:
        if self._workspace_id:
            return self._workspace_id
        if self.history_service and hasattr(self.history_service, "workspace"):
            return self.history_service.workspace_id
        return "local"

    def __enter__(self) -> "TurnProcessor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        self._closed = True
        if hasattr(self, "context_engine") and self.context_engine:
            try:
                self.context_engine.close()
            except Exception:
                pass
        if hasattr(self, "working_set") and self.working_set:
            try:
                self.working_set.close()
            except Exception:
                pass

    def _cancel_requested(self, turn_id: str) -> bool:
        guard = getattr(self, "turn_guard", None)
        return bool(guard and guard.is_cancelled(turn_id))

    def _mark_cancelled(self, turn_id: str) -> bool:
        guard = getattr(self, "turn_guard", None)
        if guard is None:
            cancelled = getattr(self, "cancelled_turns", None)
            if cancelled is not None:
                cancelled.add(turn_id)
            return False
        return guard.cancel(turn_id)

    def _emit(self, event_name: str, payload: Dict[str, Any]):
        callback = getattr(self, "event_callback", None)
        if not callback or getattr(self, "_closed", False):
            return
        data = payload
        context = self._agent_trace_context
        if context and isinstance(payload, dict):
            data = dict(payload)
            data.setdefault("turn_id", context[0])
            data.setdefault("conversation_id", context[1])
        callback(event_name, data)

    def _record_latency(
        self,
        turn_id: str,
        phase: str,
        duration_ms: float,
        *,
        elapsed_ms: float = 0.0,
        detail: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "turn_id": turn_id,
            "phase": phase,
            "duration_ms": round(max(0.0, float(duration_ms)), 2),
            "elapsed_ms": round(max(0.0, float(elapsed_ms)), 2),
            "detail": dict(detail or {}),
        }
        logger.info(
            "latency turn=%s phase=%s duration_ms=%.2f elapsed_ms=%.2f detail=%s",
            turn_id, phase, payload["duration_ms"], payload["elapsed_ms"], payload["detail"],
        )
        try:
            self._emit("LatencyRecorded", payload)
        except Exception:
            # Observability is strictly fail-open: a broken metrics consumer
            # must never alter tool, approval, cancellation or response flow.
            logger.debug("latency callback failed", exc_info=True)
        return payload

    @staticmethod
    def _paths_from_tool(tool_name: str, tool_args: Dict[str, Any], tool_result: Any = None) -> List[str]:
        if tool_name in {"read_file", "write_file"}:
            path = tool_args.get("path") or tool_args.get("file")
            return [path] if path else []
        if tool_name == "apply_patch":
            edit_result = getattr(tool_result, "metadata", {}).get("edit_result") if tool_result else None
            if edit_result:
                return list(edit_result.applied_files + edit_result.created_files)
        return []

    def _fit_tool_output(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        output: str,
        profile,
        wrapper_prefix: str = "",
        wrapper_suffix: str = "",
    ) -> str:
        prompt_budget = PromptBudget(profile.context_window, profile.max_output_tokens)
        max_allowed = prompt_budget.max_input_tokens
        used = (
            TokenCounter.count_tokens(system_prompt)
            + sum(TokenCounter.count_tokens(m.get("content", "")) for m in messages)
            + TokenCounter.count_tokens(wrapper_prefix + wrapper_suffix)
        )
        remaining = max(0, max_allowed - used - 80)
        if remaining <= 0:
            return ""
        if TokenCounter.count_tokens(output) <= remaining:
            return output
        return prompt_budget._truncate_to_tokens(output, remaining)

    def _rebudget_execution_messages(
        self, messages: List[Dict[str, str]], system_prompt: str, profile
    ) -> None:
        """Keep each follow-up request inside provider input budget."""
        available = PromptBudget(
            profile.context_window, profile.max_output_tokens
        ).max_input_tokens
        used = TokenCounter.count_tokens(system_prompt) + TokenCounter.count_messages(messages).count
        if used <= available:
            return
        excess = used - available
        for index in range(1, len(messages)):
            if excess <= 0:
                break
            message = messages[index]
            current = message.get("content", "")
            # The browser proxy uses user history to identify conversation resets.
            # Keep that identity stable across follow-up tool requests.
            if _reverse_proxy_identity(profile) is not None and message.get("role") == "user":
                continue
            # Preserve structural tool envelopes: truncating bridge tags or tool result
            # envelopes corrupts JSON/tool protocol and causes reverse-proxy session resets.
            if message.get("role") == "assistant" and ("<kitt-tool>" in current or "</kitt-tool>" in current):
                continue
            if message.get("role") == "user" and "result from the host" in current[:512].lower():
                continue
            current_tokens = TokenCounter.count_tokens(current)
            if current_tokens <= 64:
                continue
            target = max(64, current_tokens - excess)
            trimmed = PromptBudget(profile.context_window, profile.max_output_tokens)._truncate_to_tokens(current, target)
            message["content"] = trimmed
            excess -= max(0, current_tokens - TokenCounter.count_tokens(trimmed))

    def _routing_capabilities(self) -> Dict[str, ModelCapabilities]:
        local_backends = {"ollama", "lmstudio", "antigravity", "local", "kitt-reverse-proxy", "kitt-proxy"}
        caps: Dict[str, ModelCapabilities] = {}
        for name, profile in self.router.config.profiles.items():
            is_local = profile.backend in local_backends
            tier = "small" if name == "context" or profile.context_window <= 8192 else "large"
            caps[name] = ModelCapabilities(
                profile_name=name,
                tier=tier,
                input_context_limit=profile.context_window,
                max_output_tokens=profile.max_output_tokens,
                supports_json=profile.supports_json,
                supports_native_tools=True,
                tool_call_reliability=0.8 if profile.supports_tools else 0.6,
                code_edit_score=0.75 if tier == "small" else 0.9,
                reasoning_score=0.75 if tier == "small" else 0.9,
                languages=(),
                is_local=is_local,
                privacy_class="local" if is_local else "cloud",
            )

        feedback_fn = self._routing_feedback_snapshot_fn
        if feedback_fn is None:
            return caps
        try:
            snapshot = feedback_fn(self)
        except Exception:
            return caps
        for name, cap in tuple(caps.items()):
            feedback = snapshot.get(name) or {}
            samples = int(feedback.get("samples", 0))
            if samples < 3:
                continue
            rate = max(0.0, min(float(feedback.get("success_rate", 0.5)), 1.0))
            weight = min(0.40, samples / 50.0)
            def blend(old: float) -> float:
                return max(0.05, min(1.0, float(old) * (1.0 - weight) + rate * weight))
            caps[name] = replace(
                cap,
                tool_call_reliability=blend(cap.tool_call_reliability),
                code_edit_score=blend(cap.code_edit_score),
                reasoning_score=blend(cap.reasoning_score),
            )
        return caps

    async def arun_turn(self, cmd: TurnCommand):
        """Stream the blocking synchronous turn loop without blocking asyncio.

        Providers and host tools are intentionally synchronous today. Run the
        iterator on a producer thread and bridge events through a bounded async
        queue so TUI/daemon heartbeats, cancellation and other tasks continue
        to make progress while a model/tool call is blocked.
        """
        import asyncio as _asyncio

        loop = _asyncio.get_running_loop()
        queue: _asyncio.Queue = _asyncio.Queue(maxsize=128)
        sentinel = object()
        stop = threading.Event()

        def put(item, timeout: float = 30.0) -> bool:
            if stop.is_set():
                return False
            future = _asyncio.run_coroutine_threadsafe(queue.put(item), loop)
            try:
                future.result(timeout=timeout)
                return True
            except Exception:
                future.cancel()
                return False

        def produce() -> None:
            iterator = None
            try:
                iterator = self.run_turn(cmd)
                for event in iterator:
                    if stop.is_set() or not put(event):
                        break
            except BaseException as exc:
                if not stop.is_set():
                    put(TurnFailed(error=str(exc)), timeout=5.0)
            finally:
                if iterator is not None and hasattr(iterator, "close"):
                    try:
                        iterator.close()
                    except Exception:
                        pass
                if not stop.is_set():
                    put(sentinel, timeout=5.0)

        producer = threading.Thread(
            target=produce, name=f"kitt-turn-{cmd.turn_id[:8]}", daemon=True
        )
        producer.start()
        stream_completed = False
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    stream_completed = True
                    break
                yield item
        finally:
            # Thread liveness is not cancellation state. The producer can still
            # be alive for a scheduling tick after it has published the natural
            # completion sentinel. Only an abandoned stream should cancel the turn.
            if not stream_completed:
                self._mark_cancelled(cmd.turn_id)
            stop.set()
            if producer.is_alive():
                await _asyncio.to_thread(producer.join, 2.0)

    def _history_context(self, conversation_id: str, max_messages: int = 12,
                         exclude_prompt: Optional[str] = None) -> str:
        if not self.history_service:
            return ""
        if hasattr(self.history_service, "tree"):
            from kitt.history.context_builder import HistoryContextBuilder
            return HistoryContextBuilder(self.history_service.tree).build(conversation_id, max_tokens=1200)
        messages = self.history_service.repo.get_messages_for_conversation(conversation_id)
        selected = messages[-max_messages:]
        if exclude_prompt and selected and selected[-1]["role"] == "user" and selected[-1]["content"] == exclude_prompt:
            selected = selected[:-1]
        return "\n".join(f"{m['role']}: {m['content']}" for m in selected)

    @staticmethod
    def _args_digest(args: Dict[str, Any]) -> str:
        raw = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _runtime_operations_for_tools(planned_tools) -> tuple[str, ...]:
        """Expose only runtime operations authorized by the turn's planned capabilities."""
        from kitt.runtime.core_runtime import OPERATION_SPECS

        capabilities = capabilities_for_tools(planned_tools or [])
        preferred_order = (
            "repo.read", "repo.list", "repo.search", "repo.inspect_symbol",
            "repo.read_symbol", "repo.references", "repo.edit_symbol",
            "repo.write_file", "repo.create_directory", "patch.apply",
            "process.run", "artifacts.store", "artifacts.read",
            "children.spawn", "children.send", "children.inspect",
            "goal.inspect", "goal.update", "memory.query", "memory.correct",
            "memory.concept", "memory.link",
            "browser.open", "browser.inspect", "browser.screenshot",
            "browser.click", "browser.type", "browser.close",
            "state.get", "state.set", "state.list", "handles.resolve",
        )
        allowed = []
        for name in preferred_order:
            spec = OPERATION_SPECS.get(name)
            if spec is None:
                continue
            if spec.required_capability is None or spec.required_capability in capabilities:
                allowed.append(name)
        return tuple(allowed)

    def _tool_instructions(self, enabled_tools, planned_tools=None) -> str:
        if not enabled_tools:
            return "No host tools are enabled. Answer directly."

        if "kitt_runtime" in enabled_tools and len(enabled_tools) == 1:
            operations = self._runtime_operations_for_tools(planned_tools or enabled_tools)
            operations_text = ", ".join(operations) or "(none)"

            runtime_definition = dict(
                self.registry.get_tool_definitions(["kitt_runtime"])[0]
            )
            runtime_args = dict(runtime_definition.get("args") or {})
            runtime_args["operation"] = {
                "type": "string",
                "enum": list(operations),
            }
            runtime_definition["args"] = runtime_args

            examples = []
            if "repo.read" in operations:
                examples.append("""To read a file:
<kitt-tool>
{"name":"kitt_runtime","arguments":{"operation":"repo.read","arguments":{"path":"path.ext","start_line":1,"end_line":100}}}
</kitt-tool>""")
            if "repo.write_file" in operations:
                examples.append("""To create or replace a file, use repo.write_file:
<kitt-tool>
{"name":"kitt_runtime","arguments":{"operation":"repo.write_file","arguments":{"path":"path/to/file.ext","content":"complete file content"}}}
</kitt-tool>""")
            if "patch.apply" in operations:
                examples.append("""To edit an existing file, patch.apply uses SEARCH/REPLACE blocks:
<kitt-tool>
{"name":"kitt_runtime","arguments":{"operation":"patch.apply","arguments":{"patch":"path/to/file.ext\\n<<<<<<< SEARCH\\nexact original text\\n=======\\nreplacement content\\n>>>>>>> REPLACE"}}}
</kitt-tool>""")
            if "process.run" in operations:
                examples.append("""To run a build/test/validation command, process.run is argv-only and never invokes a shell:
<kitt-tool>
{"name":"kitt_runtime","arguments":{"operation":"process.run","arguments":{"argv":["npm","run","build"],"cwd":"frontend","timeout_seconds":120}}}
</kitt-tool>
Do not use command, cmd, args, sh -c, bash -c, cmd.exe /c, PowerShell, redirection, pipes, or &&.""")
            if "browser.open" in operations:
                examples.append("""Browser automation uses a separate reverse-proxy tab.
<kitt-tool>
{"name":"kitt_runtime","arguments":{"operation":"browser.open","arguments":{"url":"http://localhost:4200"}}}
</kitt-tool>
Use browser.inspect for bounded DOM/text and browser.screenshot for visual validation. Screenshots are attached only to the next model request; never request or reproduce base64.""")
            if "browser.click" in operations:
                examples.append("""Interactive browser actions require browser.write and may require approval:
<kitt-tool>
{"name":"kitt_runtime","arguments":{"operation":"browser.click","arguments":{"selector":"#submit"}}}
</kitt-tool>""")
            examples_text = "\n".join(examples)

            return f"""
Available host tool: {[runtime_definition]}
{examples_text}
Supported operations for this turn: {operations_text}.
RULES:
- Never use process.run, shell redirection, printf, cat, echo, heredocs, or mkdir to create/edit workspace files. Use repo.write_file, repo.create_directory, or patch.apply instead.
1. Focus strictly on user request.
2. Do not expose chain-of-thought. Emit the tool call directly when action is needed.
3. Once fulfilled, STOP calling tools and answer directly.
""".strip()

        instructions = f"""
Available host tools: {self.registry.get_tool_definitions(enabled_tools)}
For a host tool, respond with exactly:
<kitt-tool>
{{"name":"read_file","arguments":{{"path":"relative/path.py","start_line":1,"end_line":200}}}}
</kitt-tool>
RULES:
1. Focus strictly on the user's explicit request. Do not make unrequested changes or edits to other files.
2. Exact Path Adherence: When the user references a file (e.g. `@apresentacao.html` or `apresentacao.html`) or when files are provided in context, you MUST use the EXACT relative file path specified. NEVER invent new directory structures (such as `src/`, `src/html/`, `html/`, `app/`) and NEVER duplicate the file under a new directory.
3. Reason internally. Never emit chain-of-thought or <think>/<thought> blocks.
4. Once the requested task is fulfilled, STOP calling tools and answer directly with a concise summary.
5. ACTION MANDATE: If the user asks to edit, update, modify, fix, or create a file, you MUST emit a tool call (`write_file` or `apply_patch`). NEVER output the updated file content as plain chat text or markdown without a tool call; call the tool directly so K.I.T.T. writes the changes to disk.
"""
        if "write_file" in enabled_tools:
            instructions += """
To create or overwrite a file, use write_file:
<kitt-tool>
{"name":"write_file","arguments":{"path":"relative/path.ext","content":"file content here"}}
</kitt-tool>
"""
        if "apply_patch" in enabled_tools:
            instructions += """
To edit an existing file, use apply_patch with SEARCH/REPLACE blocks:
<kitt-tool>
{"name":"apply_patch","arguments":{"patch":"path/to/file.ext\\n<<<<<<< SEARCH\\nexact original lines to find\\n=======\\nreplacement lines\\n>>>>>>> REPLACE"}}
</kitt-tool>
Alternatively, you may emit standard SEARCH/REPLACE diff blocks directly:
path/to/file.ext
<<<<<<< SEARCH
exact original lines to find
=======
replacement lines
>>>>>>> REPLACE
"""
        if "python_compute" in enabled_tools:
            instructions += """
Safe computation tool: python_compute
Use it only when deterministic calculation or JSON transformation is useful.
To call it, your entire response must be exactly:
<kitt-python-compute>
{{"code":"Python-subset source; assign final value to _result","inputs":{{}},"result_var":"_result"}}
</kitt-python-compute>
Do not add markdown or prose around a tool call. Wait for the tool result before answering.
The tool has no imports, files, network, shell, reflection, functions, classes, threads, or external packages.
Available modules are math, statistics, and json; Decimal and Fraction are also available.
Use read_file/search/repository_map for project data and pass only selected JSON values through inputs.
"""
        return instructions.strip()


    @staticmethod
    def _browser_intent(prompt: str) -> tuple[bool, bool]:
        text = str(prompt or "").casefold()
        read_terms = (
            "browser", "navegador", "website", "web site", "site", "webpage",
            "web page", "página web", "pagina web", "frontend", "front-end",
            "preview", "screenshot", "captura de tela", "localhost", "renderize",
            "renderizar", "visual da página", "visual da pagina",
        )
        write_terms = (
            "click", "clique", "clicar", "preencha", "preencher", "fill",
            "digite", "digitar", "type", "submit", "envie o formulário",
            "envie o formulario", "interaja", "interagir", "login", "log in",
            "autentique", "autenticar",
        )
        has_url = bool(
            re.search(
                r"https?://|\blocalhost(?::\d+)?\b|\b127\.0\.0\.1(?::\d+)?\b",
                text,
            )
        )
        read_requested = has_url or any(term in text for term in read_terms)
        write_requested = read_requested and any(term in text for term in write_terms)
        return read_requested, write_requested

    @staticmethod
    def _browser_origin_scope(prompt: str) -> tuple[str, ...]:
        text = str(prompt or "")
        folded = text.casefold()
        scope: list[str] = []
        local_terms = (
            "localhost", "127.0.0.1", "frontend", "front-end", "preview",
            "captura de tela", "screenshot", "renderize", "renderizar",
        )
        if any(term in folded for term in local_terms):
            scope.append("loopback")

        for match in re.findall(r"https?://[^\s<>'\"\]\)]+", text, flags=re.IGNORECASE):
            candidate = match.rstrip(".,;:!?}")
            try:
                parsed = urlsplit(candidate)
                host = parsed.hostname
                if not host or parsed.username or parsed.password:
                    continue
                scheme = parsed.scheme.lower()
                if scheme not in {"http", "https"}:
                    continue
                host_ascii = host.encode("idna").decode("ascii").lower()
                if host_ascii in {"localhost", "127.0.0.1", "::1"}:
                    scope.append("loopback")
                    continue
                display_host = (
                    f"[{host_ascii}]" if ":" in host_ascii and not host_ascii.startswith("[")
                    else host_ascii
                )
                port = parsed.port
                default_port = 80 if scheme == "http" else 443
                origin = f"{scheme}://{display_host}"
                if port is not None and port != default_port:
                    origin += f":{port}"
                scope.append(origin)
            except (UnicodeError, ValueError):
                continue
        return tuple(dict.fromkeys(scope))

    def _browser_authorities_for_turn(
        self,
        cmd: TurnCommand,
        exe_client: LLMClient,
        exe_profile: ModelProfile,
    ) -> tuple[str, ...]:
        read_requested, write_requested = self._browser_intent(cmd.prompt)
        origin_scope = self._browser_origin_scope(cmd.prompt)
        if (
            not read_requested
            or not origin_scope
            or _reverse_proxy_identity(exe_profile) is None
        ):
            return ()
        session_key = self._provider_session_key(exe_profile, cmd.conversation_id)
        try:
            gateway = exe_client.create_kitt_proxy_browser_gateway(
                session_key,
                origin_scope=origin_scope,
            )
        except Exception as exc:
            logger.debug("browser gateway discovery failed: %s", exc)
            return ()
        if gateway is None:
            return ()
        self.registry.bind_browser_gateway(cmd.conversation_id, gateway)
        authorities = [CAP_BROWSER_READ]
        if write_requested and cmd.mode not in {"plan", "ask"}:
            authorities.append(CAP_BROWSER_WRITE)
        return tuple(authorities)

    def _security_context_for_turn(self, cmd: TurnCommand, planned_tools) -> ExecutionSecurityContext:
        if cmd.security_context is not None:
            ctx = cmd.security_context
            if isinstance(ctx, dict):
                ctx = ExecutionSecurityContext.from_dict(ctx)
            ctx.assert_scope(self.workspace_id, cmd.conversation_id)
            return ctx.with_turn(cmd.turn_id)
        caps = capabilities_for_tools(planned_tools)
        caps.add(CAP_ARTIFACT_READ)
        if cmd.mode not in {"plan", "ask"}:
            caps.add(CAP_ARTIFACT_WRITE)
        if cmd.mode in {"plan", "ask"}:
            caps &= set(READ_ONLY_CAPABILITIES)
        return ExecutionSecurityContext.create_user_context(
            workspace_id=self.workspace_id,
            conversation_id=cmd.conversation_id,
            turn_id=cmd.turn_id,
            capabilities=caps,
        )

    def run_turn(self, cmd: TurnCommand) -> Iterator[TurnEvent]:
        if cmd.attachments:
            self._attachment_paths_by_turn[cmd.turn_id] = tuple(sorted(cmd.attachments))

        turn_started_at = time.time()
        start_ev = TurnStarted(turn_id=cmd.turn_id, conversation_id=cmd.conversation_id, prompt=cmd.prompt)
        self._emit("TurnStarted", {"turn_id": cmd.turn_id})
        yield start_ev

        # Check steering queue items
        if self.registry.queue_service:
            try:
                steering_items = self.registry.queue_service.repo.pending(cmd.conversation_id, kind="STEERING")
                for st in steering_items:
                    self.registry.queue_service.repo.deliver(st.id)
                    cmd.prompt += f"\n\n[STEERING PRIORITY INPUT]: {st.content}"
            except Exception:
                pass

        workspace_id = self.workspace_id

        try:
            self.session_state.current_prompt = cmd.prompt

            if cmd.turn_id in self.cancelled_turns:
                self.cancelled_turns.discard(cmd.turn_id)
                yield TurnCancelled(reason="Turn cancelled before processing")
                return

            # 1. Semantic Filter
            semantic_started_at = time.perf_counter()
            task, plan, filter_res, sf_client, ctx_profile, agent_addressed = self._run_semantic_filter(cmd)
            self._record_latency(
                cmd.turn_id,
                "semantic",
                (time.perf_counter() - semantic_started_at) * 1000,
                elapsed_ms=(time.time() - turn_started_at) * 1000,
                detail={"source": str(getattr(filter_res, "source", ""))},
            )
            if cmd.turn_id in self.cancelled_turns:
                self.cancelled_turns.discard(cmd.turn_id)
                yield TurnCancelled(reason="Turn cancelled after semantic filter")
                return
            self._emit("FilterCompleted", {"filter_res": filter_res})
            yield FilterCompleted(filter_res=filter_res)

            # 2. Execution Profile Resolution
            exe_profile_name, exe_profile, routing_decision, block_reason = self._resolve_execution_profile(cmd, task)
            if cmd.turn_id in self.cancelled_turns:
                self.cancelled_turns.discard(cmd.turn_id)
                yield TurnCancelled(reason="Turn cancelled during profile resolution")
                return
            if block_reason or not exe_profile:
                self._emit("TurnBlocked", {"reason": block_reason})
                yield TurnBlocked(reason=block_reason or "Blocked")
                return

            self._emit("ModelSelected", {"profile_name": exe_profile_name, "model": exe_profile.model})
            yield ModelSelected(profile_name=exe_profile_name, model=exe_profile.model)

            budget = PromptBudget(
                window_size=exe_profile.context_window,
                reserved_output=exe_profile.max_output_tokens
            )

            # 3. Context Engine & Retrieval
            if cmd.turn_id in self.cancelled_turns:
                self.cancelled_turns.discard(cmd.turn_id)
                yield TurnCancelled(reason="Turn cancelled before context building")
                return
            context_started_at = time.perf_counter()
            context_map_str, explicit_str, agents_str, skills_str, context_blocks, explicit_items, build_stats, needs_project_context = self._build_context(
                cmd, task, plan, exe_profile, sf_client
            )
            self._record_latency(
                cmd.turn_id,
                "context_retrieval",
                (time.perf_counter() - context_started_at) * 1000,
                elapsed_ms=(time.time() - turn_started_at) * 1000,
                detail={"selected": len(context_blocks), "explicit": len(explicit_items)},
            )
            if cmd.turn_id in self.cancelled_turns:
                self.cancelled_turns.discard(cmd.turn_id)
                yield TurnCancelled(reason="Turn cancelled after context building")
                return
            if build_stats:
                self._emit("ContextBuildCompleted", build_stats)
                yield ContextBuildCompleted(
                    index_generation=int(build_stats.get("generation", 0)),
                    index_state=str(build_stats.get("state", "")),
                    selected_count=int(build_stats.get("selected", 0)),
                    rejected_count=int(build_stats.get("rejected", 0)),
                    total_tokens=int(build_stats.get("tokens", 0)),
                    coverage=float(build_stats.get("coverage", 1.0)),
                    degraded=bool(build_stats.get("degraded", False)),
                    duration_ms=int(build_stats.get("duration_ms", 0)),
                    index_scanned=int(build_stats.get("scanned", 0)),
                    index_updated=int(build_stats.get("updated", 0)),
                    index_deleted=int(build_stats.get("deleted", 0)),
                    freshness=str(build_stats.get("freshness", "")),
                    partial_reason=str(build_stats.get("partial_reason", "")),
                    schema_version=str(build_stats.get("schema_version", "")),
                )
            yield ContextResolved(resolved_count=len(context_blocks) + len(explicit_items))

            # 4. System Prompt and Budgeting
            if cmd.turn_id in self.cancelled_turns:
                self.cancelled_turns.discard(cmd.turn_id)
                yield TurnCancelled(reason="Turn cancelled before prompt budgeting")
                return

            exe_client = self.execution_client or LLMClient(exe_profile)
            browser_authorities = self._browser_authorities_for_turn(
                cmd, exe_client, exe_profile
            )
            planned_authorities = list(plan.enabled_tools)
            for authority in browser_authorities:
                if authority not in planned_authorities:
                    planned_authorities.append(authority)
            exposed_tools = self.surface_selector.select_tools(
                plan,
                model_capabilities=getattr(exe_client, "capabilities", None),
            )
            if browser_authorities:
                exposed_tools = ["kitt_runtime"]
            security_context = self._security_context_for_turn(cmd, planned_authorities)
            prompt_plan = replace(plan, enabled_tools=planned_authorities)

            sys_prompt, base_sys, allocated, request = self._build_system_prompt(
                cmd, task, prompt_plan, exe_profile, context_map_str, explicit_str, agents_str,
                skills_str, agent_addressed, workspace_id, budget, exposed_tools=exposed_tools
            )
            self._emit("BudgetApplied", {"allocated": allocated})
            yield BudgetApplied(
                total_input_tokens=allocated["total_input_tokens"],
                reserved_output_tokens=allocated["reserved_output_tokens"],
                window_size=exe_profile.context_window
            )

            if cmd.dry_run:
                yield TurnCompleted(response="[Dry Run Completed]", edit_result=None)
                return

            # 5. Tool execution loop
            if cmd.turn_id in self.cancelled_turns:
                self.cancelled_turns.discard(cmd.turn_id)
                yield TurnCancelled(reason="Turn cancelled before tool loop")
                return
            full_response = ""
            execution_messages = []
            agent_route = self._agent_route_for_task(task, cmd.mode, cmd.prompt)
            request = replace(request, agent_route=agent_route)
            for ev, resp, msgs in self._execute_tool_loop(
                cmd, request, exe_profile, exe_client, workspace_id, security_context,
            ):
                if cmd.turn_id in self.cancelled_turns:
                    self.cancelled_turns.discard(cmd.turn_id)
                    yield TurnCancelled(reason="Turn cancelled during tool loop")
                    return
                if ev is not None:
                    yield ev
                if resp is not None:
                    full_response = resp
                if msgs is not None:
                    execution_messages = msgs

            if cmd.turn_id in self.cancelled_turns:
                self.cancelled_turns.discard(cmd.turn_id)
                yield TurnCancelled(reason="Turn cancelled after tool loop")
                return

            if not full_response and not execution_messages:
                return

            # 6. Finalize turn (edits, metrics, compaction, goals, completion)
            for ev in self._finalize_turn(
                cmd, full_response, execution_messages, request, exe_profile,
                ctx_profile, allocated, base_sys, context_map_str, explicit_str,
                workspace_id, turn_started_at, security_context
            ):
                if cmd.turn_id in self.cancelled_turns:
                    self.cancelled_turns.discard(cmd.turn_id)
                    yield TurnCancelled(reason="Turn cancelled during finalize")
                    return
                yield ev

        except Exception as e:
            yield TurnFailed(error=str(e))
        finally:
            self._attachment_paths_by_turn.pop(cmd.turn_id, None)
            self._attachment_wire_sent.discard(cmd.turn_id)

    def continue_turn(self, turn_id: str, grant: Any) -> Iterator[TurnEvent]:
        if grant is None:
            yield TurnFailed(error="No valid approval grant provided; tool requires explicit user confirmation (ASK policy).")
            return

        # 1. First, check memory cache, but prefer DB if history service exists
        pa: Optional[PendingAction] = self.pending_actions.get(turn_id)
        hist_svc = getattr(self, "history_service", None)
        grant_ws = getattr(grant, "workspace_id", "local")
        if hist_svc:
            db_pa = hist_svc.repo.get_valid_pending_action(f"pa_{turn_id}", grant_ws)
            if db_pa:
                pa = db_pa

        if not pa or pa.state != "pending" or time.time() > pa.expires_at:
            if pa and hist_svc:
                hist_svc.repo.cancel_pending_action(pa.id)
            self.pending_actions.pop(turn_id, None)
            yield TurnFailed(error="No valid pending action; tool requires explicit user confirmation (ASK policy).")
            return

        if grant is None or grant.approval_id != pa.approval_request_id:
            yield TurnFailed(error="Approval grant does not match the pending request.")
            return
        if (grant.turn_id != pa.turn_id or grant.conversation_id != pa.conversation_id
                or grant.workspace_id != pa.workspace_id or grant.action_hash != pa.action_hash
                or time.time() > grant.expires_at
                or self.registry.approval_manager.is_nonce_used(grant.nonce)):
            yield TurnFailed(error="Tool requires explicit user confirmation (ASK policy); grant is invalid.")
            return
        if self._args_digest(pa.normalized_args) != pa.source_response_sha256:
            yield TurnFailed(error="Pending action source integrity check failed.")
            return

        from kitt.security.mutation_preconditions import validate_preconditions
        valid, prec_error = validate_preconditions(self.root_path, pa.get_preconditions())
        if not valid:
            yield TurnFailed(error=prec_error or "Approval precondition verification failed.")
            return

        # Execute exactly the approved action by delegating to registry.
        # Acquire the cancellation barrier BEFORE consuming the pending action,
        # otherwise cancellation can make a valid approval disappear without
        # ever executing it.
        if not pa.security_context:
            yield TurnFailed(
                error=(
                    "Pending action has no persisted security context; "
                    "refusing fail-open resume."
                )
            )
            return
        sec_ctx = ExecutionSecurityContext.from_dict(pa.security_context)
        sec_ctx.assert_scope(pa.workspace_id, pa.conversation_id)

        if not self.turn_guard.begin(turn_id):
            yield TurnCancelled(reason="Turn cancelled before approved action execution")
            return

        pending_created_at = getattr(pa, "created_at", None)
        if isinstance(pending_created_at, (int, float)):
            self._record_latency(
                turn_id,
                "approval_wait",
                max(0.0, (time.time() - pending_created_at) * 1000),
                detail={"tool": str(getattr(pa, "tool_name", ""))},
            )

        consume_failed = False
        edit_result = None
        approved_tool_started_at = time.perf_counter()
        try:
            if hist_svc and not hist_svc.repo.consume_pending_action(pa.id):
                consume_failed = True
                res = None
            else:
                res = self.registry.execute_tool(
                    pa.tool_name,
                    pa.normalized_args,
                    turn_id=turn_id,
                    conversation_id=pa.conversation_id,
                    workspace_id=pa.workspace_id,
                    grant=grant,
                    expected_approval_id=pa.approval_request_id,
                    security_context=sec_ctx.with_turn(turn_id),
                )
                if res.success:
                    self.working_set.touch_paths(
                        pa.conversation_id,
                        self._paths_from_tool(pa.tool_name, pa.normalized_args, res),
                        turn_id,
                        weight=2.0,
                        kind=pa.tool_name,
                        content_hash=str(res.metadata.get("content_hash", "")),
                    )
                    if pa.tool_name == "apply_patch":
                        edit_result = res.metadata.get("edit_result")
                        if edit_result:
                            self.session_state.last_changeset = edit_result.changeset
                            self._emit(
                                "EditApplied",
                                {
                                    "applied": edit_result.applied_files,
                                    "created": edit_result.created_files,
                                },
                            )
        finally:
            self.turn_guard.end(turn_id)

        self._record_latency(
            turn_id,
            "tool_execution",
            (time.perf_counter() - approved_tool_started_at) * 1000,
            detail={"tool": pa.tool_name, "approved": True},
        )

        if consume_failed:
            yield TurnFailed(error="Pending action was already consumed or cancelled.")
            return

        if res is not None and res.success:
            if pa.tool_name == "apply_patch":
                if edit_result:
                    yield EditApplied(
                        applied_files=edit_result.applied_files,
                        created_files=edit_result.created_files,
                    )
                yield TurnCompleted(
                    response="[Patch applied successfully]",
                    edit_result=edit_result,
                )
            else:
                yield TurnCompleted(
                    response=f"[{pa.tool_name} applied successfully]",
                    edit_result=None,
                )
        elif res is not None:
            yield TurnFailed(error=f"Execution failed: {res.error or res.output}")

        self.pending_actions.pop(turn_id, None)

    def cancel_turn(
        self,
        turn_id: str,
        reason: str,
        conversation_id: Optional[str] = None,
    ) -> Iterator[TurnEvent]:
        had_inflight = self._mark_cancelled(turn_id)
        # Do not tear down a pending action that already crossed the execution
        # barrier. Its in-flight path owns consumption/cleanup. Waiting
        # approvals (no in-flight operation) are cancelled immediately.
        pa = (
            self.pending_actions.get(turn_id)
            if had_inflight
            else self.pending_actions.pop(turn_id, None)
        )
        if (
            pa
            and not had_inflight
            and self.history_service
            and hasattr(pa, "id")
        ):
            self.history_service.repo.cancel_pending_action(pa.id)
        owning_conversation = conversation_id or (getattr(pa, "conversation_id", None) if pa else None)
        if owning_conversation and hasattr(self, "child_manager") and self.child_manager:
            try:
                self.child_manager.cancel_for_turn(
                    owning_conversation,
                    turn_id,
                    workspace_id=self.workspace_id,
                )
            except Exception:
                pass
        yield TurnCancelled(reason=reason)
