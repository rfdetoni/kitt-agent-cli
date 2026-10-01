from __future__ import annotations

import base64
import inspect
import re
import time
import uuid
from dataclasses import replace
from typing import Dict, List, Optional

from kitt.context_filter.prompt_budget import PromptBudget, TokenCounter
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_helpers import _reverse_proxy_identity
from kitt.core.turn_events import (
    TextDelta,
    ThinkingCompleted,
    ToolCallProposed,
)
from kitt.domain.entities import SemanticTask
from kitt.llm.attachments import attach_to_first_user_message
from kitt.llm.client import LLMClient
from kitt.metrics.cost_estimator import estimate_execution_cost
from kitt.router.features import TaskFeatureExtractor
from kitt.router.models import RoutingDecision
from kitt.tools.protocol import TOOL_CALL_OPEN
from kitt.tools.safe_python import PYTHON_TOOL_CALL_OPEN
from kitt_protocol import KittRequestMetadata


class TurnModelMixin:
    """Execution-model routing, streaming and visible-response phase."""

    @staticmethod
    def _attach_browser_images(messages, images):
        if not images:
            return messages
        normalized = []
        for message in messages:
            copy = dict(message)
            if isinstance(copy.get("content"), list):
                copy["content"] = list(copy["content"])
            normalized.append(copy)
        for message in normalized:
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                parts = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                parts = list(content)
            else:
                continue
            for image in list(images)[:8]:
                mime = str(getattr(image, "mime_type", "") or "").lower()
                data = getattr(image, "data", b"")
                if mime not in {"image/png", "image/jpeg"} or not isinstance(data, bytes):
                    continue
                encoded = base64.b64encode(data).decode("ascii")
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                })
            message["content"] = parts
            return normalized
        return normalized

    @staticmethod
    def _without_thinking(response: str) -> str:
        if "</think>" in response:
            response = response.rsplit("</think>", 1)[-1]
        return re.sub(r"<think>.*?(?:</think>|$)\s*", "", response, flags=re.DOTALL).strip()

    @classmethod
    def _visible_lfm_response(cls, response: str) -> str:
        visible = cls._without_thinking(response)
        if visible:
            return visible
        marker = re.search(
            r"(?:resposta\s+final|final\s+answer|resposta|answer)\s*[:：]\s*(.+)\Z",
            response,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if marker and marker.group(1).strip():
            return marker.group(1).strip()
        if "<think" in response.lower():
            return (
                "Não recebi uma resposta final do modelo; ele retornou apenas "
                "raciocínio interno sem fechamento. Tente novamente ou selecione "
                "um modelo que finalize a resposta."
            )
        return ""

    @staticmethod
    def _clean_visible_text(text: str) -> str:
        """Strip internal tags (<think>, </think>, <kitt-tool>, etc.) from user-visible stream deltas."""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)
        text = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL)
        text = re.sub(r"<thought>.*$", "", text, flags=re.DOTALL)
        text = re.sub(r"<kitt-tool>.*?</kitt-tool>", "", text, flags=re.DOTALL)
        text = re.sub(r"<kitt-python-compute>.*?</kitt-python-compute>", "", text, flags=re.DOTALL)
        for tag in ("<think>", "</think>", "<thought>", "</thought>", "<kitt-tool>", "</kitt-tool>", "<kitt-python-compute>", "</kitt-python-compute>"):
            text = text.replace(tag, "")
        return text

    def _stream_execution_response(
        self,
        client: LLMClient,
        messages: List[Dict[str, str]],
        system_prompt: str,
        *,
        conversation_id: str,
        turn_id: str,
        route: str,
        started_at: float = 0.0,
        session_key: str = "",
        tool_definitions: Optional[List[Dict[str, object]]] = None,
        loop_action_budget: int = 4,
        context_envelope: Optional[Dict[str, object]] = None,
    ):
        """Stream normal text while capturing <think>...</think> blocks and hiding exact tool-call envelopes."""
        conversation_id = str(conversation_id or "").strip()
        turn_id = str(turn_id or "").strip()
        route = str(route or "").strip()
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not turn_id:
            raise ValueError("turn_id is required")
        if not route:
            raise ValueError("route is required")

        profile = getattr(client, "profile", None)
        wire_messages = messages
        attachment_paths = getattr(self, "_attachment_paths_by_turn", {}).get(turn_id)
        attachment_wire_sent = getattr(self, "_attachment_wire_sent", None)
        if attachment_wire_sent is None:
            attachment_wire_sent = set()
            self._attachment_wire_sent = attachment_wire_sent
        if attachment_paths and turn_id not in attachment_wire_sent:
            wire_messages = attach_to_first_user_message(
                self.root_path,
                list(messages),
                attachment_paths,
            )
            attachment_wire_sent.add(turn_id)
        registry = getattr(self, "registry", None)
        if conversation_id and registry is not None and hasattr(registry, "drain_browser_images"):
            browser_images = registry.drain_browser_images(conversation_id)
            if browser_images:
                wire_messages = self._attach_browser_images(
                    list(wire_messages), browser_images
                )
        recorder = getattr(self, "_record_model_request", None)
        if recorder is not None and conversation_id and turn_id:
            try:
                recorder(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    system_prompt=system_prompt,
                    messages=list(wire_messages),
                    route=str(route or ""),
                    profile=str(getattr(profile, "name", "") or ""),
                    model=str(getattr(profile, "model", "") or ""),
                )
            except Exception:
                pass

        provider_usage: Dict[str, object] = {}

        def _observe_usage(usage: Dict[str, object]) -> None:
            provider_usage.clear()
            provider_usage.update(dict(usage))

        execution_budget = getattr(self, "execution_budgets", {}).get(turn_id)
        estimated_input = 0
        estimated_input_cost = 0.0
        budget_stage = str(route or "execution")
        if execution_budget is not None:
            estimated_input = TokenCounter.count_tokens(system_prompt or "")
            for message in wire_messages:
                if isinstance(message, dict):
                    estimated_input += TokenCounter.count_tokens(
                        str(message.get("content") or "")
                    )
            estimated_input_cost = estimate_execution_cost(
                str(getattr(profile, "model", "") or ""),
                estimated_input,
                0,
                backend=str(getattr(profile, "backend", "") or ""),
                workspace_root=str(self.root_path),
            ).estimated_usd
            # The first attempt is reserved here so injected/fake clients that
            # do not expose the retry hook remain covered. LLMClient invokes the
            # callback for every provider attempt; attempts > 0 are retries.
            execution_budget.reserve_model_call(
                input_tokens=estimated_input,
                cost=estimated_input_cost,
                stage=budget_stage,
            )

        def _reserve_retry_attempt(attempt: int) -> None:
            if execution_budget is not None and int(attempt) > 0:
                execution_budget.reserve_model_call(
                    input_tokens=estimated_input,
                    cost=estimated_input_cost,
                    stage=budget_stage,
                )

        def _invoke_chat_stream(msgs, sys_prompt):
            kwargs = {
                "system_prompt": sys_prompt,
                "session_key": session_key or None,
                "reasoning_effort": getattr(self, "reasoning_effort", 50),
                "route": route,
                "tool_definitions": list(tool_definitions or ()),
                "loop_action_budget": loop_action_budget,
                "context_envelope": context_envelope,
                "request_metadata": KittRequestMetadata(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    request_id=uuid.uuid4().hex,
                    route=route,
                ).to_mapping(),
                "usage_callback": _observe_usage,
                "attempt_callback": _reserve_retry_attempt,
            }
            try:
                sig = inspect.signature(client.chat_stream)
                has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                if not has_varkw:
                    kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
            except (ValueError, TypeError):
                pass
            return client.chat_stream(msgs, **kwargs)

        if "lfm" in getattr(profile, "model", "").lower():
            raw_text = "".join(_invoke_chat_stream(wire_messages, system_prompt))
            if execution_budget is not None:
                actual_input = provider_usage.get("prompt_tokens")
                actual_output = provider_usage.get("completion_tokens")
                actual_input_tokens = (
                    int(actual_input)
                    if isinstance(actual_input, (int, float))
                    and not isinstance(actual_input, bool)
                    else estimated_input
                )
                actual_output_tokens = (
                    int(actual_output)
                    if isinstance(actual_output, (int, float))
                    and not isinstance(actual_output, bool)
                    else TokenCounter.count_tokens(raw_text)
                )
                actual_input_cost = estimate_execution_cost(
                    str(getattr(profile, "model", "") or ""),
                    actual_input_tokens,
                    0,
                    backend=str(getattr(profile, "backend", "") or ""),
                    workspace_root=str(self.root_path),
                ).estimated_usd
                output_cost = estimate_execution_cost(
                    str(getattr(profile, "model", "") or ""),
                    0,
                    actual_output_tokens,
                    backend=str(getattr(profile, "backend", "") or ""),
                    workspace_root=str(self.root_path),
                ).estimated_usd
                execution_budget.reconcile_model_input(
                    estimated_tokens=estimated_input,
                    actual_tokens=actual_input_tokens,
                    estimated_cost=estimated_input_cost,
                    actual_cost=actual_input_cost,
                    stage=budget_stage,
                )
                execution_budget.record_model_output(
                    output_tokens=actual_output_tokens,
                    cost=output_cost,
                    stage=budget_stage,
                )
            thought_match = re.search(r"<think>(.*?)(?:</think>|$)", raw_text, re.DOTALL)
            thought_text = thought_match.group(1).strip() if thought_match else ""
            dur_ms = int((time.time() - (started_at or time.time())) * 1000)
            yield raw_text, ThinkingCompleted(duration_ms=dur_ms, tokens=TokenCounter.count_tokens(thought_text))
            full_response = self._visible_lfm_response(raw_text)
            if full_response:
                yield full_response, TextDelta(delta=full_response)
            yield full_response, None
            return

        full_response = ""
        thinking_completed = False
        thought_text = ""
        tool_detected = False
        buffer = ""

        TAG_OPENERS = ("<kitt-python-compute>", "<kitt-tool>", "<think>", "<thought>", "</think>", "</thought>", "</kitt-tool>", "</kitt-python-compute>")

        model_request_started_at = time.perf_counter()
        first_model_chunk = True
        for chunk in _invoke_chat_stream(wire_messages, system_prompt):
            if first_model_chunk:
                first_model_chunk = False
                self._record_latency(
                    turn_id,
                    "model_ttft",
                    (time.perf_counter() - model_request_started_at) * 1000,
                    detail={"session": "named" if session_key else "default"},
                )
            if turn_id and self._cancel_requested(turn_id):
                break
            full_response += chunk

            # 1. Handle reasoning inside <think>...</think> or <thought>...</thought>
            if not thinking_completed:
                clean_lstrip = full_response.lstrip()
                if clean_lstrip.startswith("<think>") or clean_lstrip.startswith("<thought>"):
                    close_tag = "</think>" if clean_lstrip.startswith("<think>") else "</thought>"
                    if close_tag in full_response:
                        parts = full_response.split(close_tag, 1)
                        m = re.search(r"<(?:think|thought)>(.*)", parts[0], re.DOTALL)
                        thought_text = m.group(1).strip() if m else re.sub(r"<(?:think|thought)>", "", parts[0]).strip()
                        dur_ms = int((time.time() - (started_at or time.time())) * 1000)
                        thinking_completed = True
                        yield full_response, ThinkingCompleted(
                            duration_ms=dur_ms,
                            tokens=TokenCounter.count_tokens(thought_text),
                        )
                        buffer = parts[1]
                    else:
                        continue
                elif any(tag.startswith(clean_lstrip) for tag in ("<think>", "<thought>")):
                    # Still forming opening think tag
                    continue
                else:
                    thinking_completed = True
                    dur_ms = int((time.time() - (started_at or time.time())) * 1000)
                    yield full_response, ThinkingCompleted(duration_ms=dur_ms, tokens=0)
                    buffer = full_response
            else:
                buffer += chunk

            # 2. Process buffer for tool envelopes vs visible assistant text
            if buffer:
                if tool_detected or any(tag in full_response for tag in (PYTHON_TOOL_CALL_OPEN, TOOL_CALL_OPEN, "<tool>")):
                    tool_detected = True
                    buffer = ""
                    tool_match = re.search(r'"name"\s*:\s*"([a-zA-Z0-9_-]+)"', full_response)
                    tool_name = tool_match.group(1) if tool_match else "código"
                    path_match = re.search(r'"path"\s*:\s*"([^"]+)"', full_response)
                    target_path = path_match.group(1) if path_match else ""
                    yield full_response, ToolCallProposed(tool_name=tool_name, args={"path": target_path, "bytes": len(full_response)})
                    continue

                if "<<<<<<< SEARCH" in full_response:
                    tool_detected = True
                    buffer = ""
                    path_match = re.search(r'([a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+)\s*\n<<<<<<< SEARCH', full_response)
                    target_path = path_match.group(1) if path_match else ""
                    yield full_response, ToolCallProposed(tool_name="apply_patch", args={"path": target_path, "bytes": len(full_response)})
                    continue

                buf_lstrip = buffer.lstrip()
                if any(buf_lstrip.startswith(tag) for tag in (PYTHON_TOOL_CALL_OPEN, TOOL_CALL_OPEN, "<tool>")):
                    tool_detected = True
                    buffer = ""
                    tool_match = re.search(r'"name"\s*:\s*"([a-zA-Z0-9_-]+)"', full_response)
                    tool_name = tool_match.group(1) if tool_match else "código"
                    path_match = re.search(r'"path"\s*:\s*"([^"]+)"', full_response)
                    target_path = path_match.group(1) if path_match else ""
                    yield full_response, ToolCallProposed(tool_name=tool_name, args={"path": target_path, "bytes": len(full_response)})
                    continue

                if any(tag.startswith(buf_lstrip) for tag in (PYTHON_TOOL_CALL_OPEN, TOOL_CALL_OPEN, "<tool>")):
                    # Buffer is actively forming <kitt-tool>
                    continue

                # Check if buffer ends with a partial tag prefix (e.g. "<", "<kitt", "</think")
                partial_tag = None
                for tag_candidate in TAG_OPENERS:
                    for length in range(1, len(tag_candidate)):
                        prefix = tag_candidate[:length]
                        if buffer.endswith(prefix):
                            partial_tag = prefix
                            break
                    if partial_tag:
                        break

                if partial_tag:
                    safe_part = buffer[:-len(partial_tag)]
                    clean_safe = self._clean_visible_text(safe_part)
                    if clean_safe:
                        yield full_response, TextDelta(delta=clean_safe)
                    buffer = partial_tag
                else:
                    clean = self._clean_visible_text(buffer)
                    if clean:
                        yield full_response, TextDelta(delta=clean)
                    buffer = ""

        if not thinking_completed:
            dur_ms = int((time.time() - (started_at or time.time())) * 1000)
            thought_text = ""
            m = re.search(r"<(?:think|thought)>(.*?)(?:</(?:think|thought)>|$)", full_response, re.DOTALL)
            if m:
                thought_text = m.group(1).strip()
            yield full_response, ThinkingCompleted(duration_ms=dur_ms, tokens=TokenCounter.count_tokens(thought_text))

        if not tool_detected and buffer:
            clean = self._clean_visible_text(buffer)
            if clean:
                yield full_response, TextDelta(delta=clean)
        if execution_budget is not None:
            actual_input = provider_usage.get("prompt_tokens")
            actual_output = provider_usage.get("completion_tokens")
            actual_input_tokens = (
                int(actual_input)
                if isinstance(actual_input, (int, float))
                and not isinstance(actual_input, bool)
                else estimated_input
            )
            actual_output_tokens = (
                int(actual_output)
                if isinstance(actual_output, (int, float))
                and not isinstance(actual_output, bool)
                else TokenCounter.count_tokens(full_response)
            )
            actual_input_cost = estimate_execution_cost(
                str(getattr(profile, "model", "") or ""),
                actual_input_tokens,
                0,
                backend=str(getattr(profile, "backend", "") or ""),
                workspace_root=str(self.root_path),
            ).estimated_usd
            output_cost = estimate_execution_cost(
                str(getattr(profile, "model", "") or ""),
                0,
                actual_output_tokens,
                backend=str(getattr(profile, "backend", "") or ""),
                workspace_root=str(self.root_path),
            ).estimated_usd
            execution_budget.reconcile_model_input(
                estimated_tokens=estimated_input,
                actual_tokens=actual_input_tokens,
                estimated_cost=estimated_input_cost,
                actual_cost=actual_input_cost,
                stage=budget_stage,
            )
            execution_budget.record_model_output(
                output_tokens=actual_output_tokens,
                cost=output_cost,
                stage=budget_stage,
            )
        yield full_response, None

    def _resolve_execution_profile(self, cmd: TurnCommand, task: Optional[SemanticTask] = None) -> tuple:
        configured_exe_name, configured_exe = self.router.resolve_profile_for_task("code-generation")
        explicit_client_profile = getattr(self.execution_client, "profile", None)
        llm_first_profile = (
            explicit_client_profile
            if _reverse_proxy_identity(explicit_client_profile) is not None
            else configured_exe
            if _reverse_proxy_identity(configured_exe) is not None
            else None
        )
        if llm_first_profile is not None:
            profile_name = (
                configured_exe_name
                if llm_first_profile is configured_exe
                else str(getattr(llm_first_profile, "model", "") or "reverse-proxy")
            )
            desired_output = max(
                llm_first_profile.max_output_tokens,
                min(4096, max(1024, llm_first_profile.context_window // 2)),
            )
            safe_output = min(
                desired_output,
                llm_first_profile.context_window - PromptBudget.MIN_INPUT_TOKENS,
            )
            llm_first_profile = replace(
                llm_first_profile,
                max_output_tokens=max(64, safe_output),
            )
            decision = RoutingDecision(
                route_id=f"llm-first-{cmd.turn_id[:12]}",
                selected_profile=profile_name,
                selected_tier="large" if llm_first_profile.context_window > 8192 else "small",
                context_profile=None,
                reasons=("Reverse-proxy execution profile is explicitly configured; natural-language model routing is bypassed.",),
                component_scores={profile_name: 1.0},
                escalation_conditions=("validation_failed", "provider_unavailable"),
                privacy_mode=getattr(self.config, "privacy_mode", "hybrid_redacted"),
                privacy_decision="ALLOWED",
                policy_version="llm-first-v2",
                created_at=str(time.time()),
            )
            return profile_name, llm_first_profile, decision, None

        features = TaskFeatureExtractor.from_task(task, prompt=cmd.prompt, explicit_files=tuple(cmd.explicit_files)) if task else TaskFeatureExtractor.extract(cmd.prompt, explicit_files=tuple(cmd.explicit_files))
        routing_decision = self.routing_policy.select_route(
            features,
            self._routing_capabilities(),
            privacy_mode=getattr(self.config, "privacy_mode", "hybrid_redacted"),
            user_override_profile=configured_exe_name,
        )
        if not routing_decision.selected_profile:
            reason = "; ".join(routing_decision.reasons) or "No eligible execution profile"
            return None, None, routing_decision, reason
        exe_profile_name = routing_decision.selected_profile
        exe_profile = self.router.config.profiles.get(exe_profile_name, configured_exe)
        if exe_profile:
            desired_output = max(
                exe_profile.max_output_tokens,
                min(4096, max(1024, exe_profile.context_window // 2)),
            )
            safe_output = min(
                desired_output,
                exe_profile.context_window - PromptBudget.MIN_INPUT_TOKENS,
            )
            exe_profile = replace(exe_profile, max_output_tokens=max(64, safe_output))
        return exe_profile_name, exe_profile, routing_decision, None
