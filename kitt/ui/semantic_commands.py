"""Small local-semantic helper for the KITT TUI and command palette."""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import replace

from kitt.context.rag.embeddings import OllamaEmbeddingProvider
from kitt.context_filter.local_intent import get_local_classifier
from kitt.llm.http_security import secure_urlopen


def semantic_readiness(model: str, endpoint: str) -> tuple[str, str]:
    """Inspect prepared Ollama tags locally; do not pull or infer weights."""
    if not model.strip():
        return "not_configured", "Set KITT_SEMANTIC_LOCAL_MODEL to a prepared Ollama model."
    try:
        provider = OllamaEmbeddingProvider(model, endpoint)
        request = urllib.request.Request(f"{provider.base_url}/api/tags", method="GET")
        with secure_urlopen(request, timeout=3.0, max_body_bytes=512 * 1024, local_only=True) as response:
            data = json.loads(response.read().decode("utf-8"))
        for item in data.get("models", ()) if isinstance(data, dict) else ():
            if not isinstance(item, dict):
                continue
            if str(item.get("name", "")).casefold() == model.casefold():
                return "prepared", f"model={model} digest={str(item.get('digest', 'unknown'))[:64]}"
        return "model_missing", f"{model} is not in the local Ollama model list. Use an explicit ollama pull."
    except (OSError, ValueError, RuntimeError) as exc:
        return "unavailable", f"Local Ollama diagnostic failed: {type(exc).__name__}"


def _set_live_semantic_mode(runtime, mode: str) -> None:
    """Runtime-only toggle; persistent config remains owned by Control Center."""
    runtime.config = replace(runtime.config, semantic_mode=mode)
    processor = getattr(runtime, "processor", None)
    if processor is not None and getattr(processor, "config", None) is not None:
        processor.config = replace(processor.config, semantic_mode=mode)


async def handle_semantic_command(ui, arg: str) -> None:
    argument = str(arg or "").strip()
    mode = str(getattr(ui.runtime.config, "semantic_mode", "off"))
    model = os.getenv("KITT_SEMANTIC_LOCAL_MODEL", "").strip()
    endpoint = os.getenv("KITT_SEMANTIC_LOCAL_URL", "http://127.0.0.1:11434").strip()
    if argument in {"", "status"}:
        state = "disabled" if mode == "off" else ("model not configured" if not model else "not checked")
        ui._show_result(
            f"Local semantic: {state}\nMode: {mode}\nBackend: Ollama (loopback only)\n"
            f"Model: {model or '(not configured)'}\nEndpoint: {endpoint}\n"
            "Use /semantic doctor for availability; /semantic test <prompt> for a local classification.\n"
            "This is task classification, not agent-wide offline mode."
        )
        return
    if argument in {"doctor", "diagnose"}:
        state, detail = await ui._run_blocking(semantic_readiness, model, endpoint)
        ui._show_result(f"Local semantic: {state}\n{detail}\nNo model download was attempted.")
        return
    if argument in {"off", "auto", "local"}:
        _set_live_semantic_mode(ui.runtime, argument)
        ui._show_result(
            f"Local semantic mode: {argument} (this process only).\n"
            "Set KITT_SEMANTIC_MODE or the Control Center setting for persistence."
        )
        return
    if argument.startswith("test "):
        prompt = argument[5:].strip()
        if not prompt:
            ui._show_result("Usage: /semantic test <prompt>")
            return
        if not model:
            ui._show_result("Local semantic: no model configured; no generative fallback attempted.")
            return
        try:
            classifier = get_local_classifier(model, endpoint)
            task, reason = await ui._run_blocking(classifier.classify, prompt)
            if task is None:
                ui._show_result(f"Local semantic abstained: {reason}. No generative call made.")
            else:
                ui._show_result(
                    f"Local semantic suggestion: {task.intent}, secondary={task.secondary_intents}.\n"
                    "Advisory only; cannot authorize tools. Original prompt remains authoritative."
                )
        except (OSError, ValueError, RuntimeError) as exc:
            ui._show_result(f"Local semantic unavailable: {type(exc).__name__}")
        return
    ui._show_result("Usage: /semantic [status|doctor|off|auto|local|test <prompt>]")
