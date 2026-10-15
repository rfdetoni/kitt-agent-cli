"""Explicit opt-in integration using real prepared Ollama/E5 Q8 weights on CPU."""
from __future__ import annotations

import json
import math
import os
import time

import pytest

from kitt.context.rag.embeddings import OllamaEmbeddingProvider
from kitt.context_filter.local_intent import LocalIntentClassifier, _cosine

pytestmark = pytest.mark.skipif(
    os.getenv("KITT_SEMANTIC_REAL_OLLAMA") != "1",
    reason="Requires explicit local Ollama model preparation",
)

_MODEL = "qllama/multilingual-e5-small:q8_0"
_INPUTS = (
    "Crie um arquivo de configuração.",
    "Create a configuration file.",
    "Crea un archivo de configuración.",
)
_EVALUATION = (
    ("pt", "IMPLEMENT", "Implemente um endpoint de cadastro de usuários."),
    ("en", "IMPLEMENT", "Implement an endpoint for user registration."),
    ("es", "IMPLEMENT", "Implementa un endpoint para registrar usuarios."),
    ("pt", "ASK", "Explique os detalhes do método UserService.load."),
    ("en", "ASK", "Explain the implementation of UserService.load."),
    ("es", "ASK", "Explica los detalles de UserService.load."),
    ("pt", "TEST", "Execute a suíte de testes de integração."),
    ("en", "TEST", "Execute the integration test suite."),
    ("es", "TEST", "Ejecuta las pruebas de integración."),
    ("pt", "REVIEW", "Revise UserService e liste problemas sem editar."),
    ("en", "REVIEW", "Inspect UserService for issues without edits."),
    ("es", "REVIEW", "Revisa UserService, pero no edites nada."),
    ("pt", "PLAN", "Planeje uma migração do banco sem executá-la."),
    ("en", "PLAN", "Write a database migration strategy without running it."),
    ("es", "PLAN", "Planifica la migración de base de datos sin ejecutarla."),
    ("pt", "DOCUMENT", "Atualize as instruções de integração no README."),
    ("en", "DOCUMENT", "Update the integration instructions in README."),
    ("es", "DOCUMENT", "Actualiza las instrucciones de integración en README."),
    ("pt", "DEBUG", "Corrija a falha de token expirado na autenticação."),
    ("en", "DEBUG", "Fix the expired-token authentication error."),
    ("es", "DEBUG", "Corrige el fallo de token caducado en autenticación."),
    ("pt", "REFACTOR", "Refatore BillingService para simplificar as condições."),
    ("en", "REFACTOR", "Refactor BillingService to simplify conditions."),
    ("es", "REFACTOR", "Refactoriza BillingService para simplificar condiciones."),
)


def test_real_multilingual_e5_cpu_embeddings():
    provider = OllamaEmbeddingProvider(
        _MODEL, "http://127.0.0.1:11434", timeout_seconds=30,
        total_timeout_seconds=90, batch_size=12,
    )
    started = time.perf_counter()
    vectors = provider.embed_queries(_INPUTS)
    doc = provider.embed_documents(["User registration and configuration file"])
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert len(vectors) == 3 and len(doc) == 1
    assert all(len(v) == 384 and all(math.isfinite(x) for x in v) for v in [*vectors, *doc])
    print(json.dumps({
        "experiment": "e5_cpu_real_inference", "model": _MODEL,
        "elapsed_ms_cold": round(elapsed_ms, 3), "dimensions": len(vectors[0]),
        "cosine_pt_en": round(_cosine(vectors[0], vectors[1]), 4),
        "cosine_pt_es": round(_cosine(vectors[0], vectors[2]), 4),
    }, ensure_ascii=False))


def test_real_local_intent_coverage_and_safety():
    provider = OllamaEmbeddingProvider(
        _MODEL, "http://127.0.0.1:11434", timeout_seconds=30,
        total_timeout_seconds=120, batch_size=32,
    )
    classifier = LocalIntentClassifier(provider)
    began = time.perf_counter()
    per_lang = {}
    accepted = 0
    correct = 0
    for language, expected, prompt in _EVALUATION:
        task, _reason = classifier.classify(prompt)
        lang = per_lang.setdefault(language, {"accepted": 0, "correct": 0, "total": 0})
        lang["total"] += 1
        if task is not None:
            accepted += 1
            lang["accepted"] += 1
            if task.intent == expected:
                correct += 1
                lang["correct"] += 1
            assert task.original_prompt == prompt
    total = len(_EVALUATION)
    assert accepted >= max(1, total // 3), "Local automatic mode has near-zero accepted coverage"
    if accepted:
        assert correct / accepted >= 0.95, "Unsafe intent acceptance precision"
    for prompt in (
        "Explica el código sin modificar ningún archivo.",
        "Do not edit the code; explain what it does.",
        "Não crie arquivos, apenas explique o formato.",
    ):
        task, _ = classifier.classify(prompt)
        assert task is None or task.intent in {"ASK", "REVIEW"}, (
            f"Unsafe mutation classification: {task.intent}"
        )
    report = {
        "experiment": "e5_real_local_classifier_holdout",
        "model": _MODEL, "total": total, "accepted": accepted, "correct": correct,
        "precision_accepted": (correct / accepted if accepted else None),
        "coverage": accepted / total,
        "abstention_rate": (total - accepted) / total,
        "per_language": per_lang,
        "elapsed_ms_including_prototype_warmup": round((time.perf_counter() - began) * 1000, 3),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    # This is a diagnostic holdout, not a certified production quality gate.
    # Production auto-default remains disabled until calibrated.


# Calibration examples are not part of the final holdout above. They are used
# only to inspect score distributions and design safe acceptance thresholds.
_CALIBRATION = (
    ("IMPLEMENT", "Crie uma nova configuração de ambiente."),
    ("IMPLEMENT", "Add a new settings module."),
    ("IMPLEMENT", "Crea el módulo para configuraciones."),
    ("ASK", "Como funciona o sistema de permissões?"),
    ("ASK", "What does the provider routing code do?"),
    ("ASK", "¿Cómo funciona el enrutamiento de proveedores?"),
    ("PLAN", "Desenhe os passos para migrar o armazenamento, sem executar."),
    ("PLAN", "Outline database migration phases without applying changes."),
    ("PLAN", "Elabora los pasos para migrar la base de datos sin ejecutarlo."),
    ("DEBUG", "Investigue e corrija a falha na autenticação."),
    ("DEBUG", "Troubleshoot and repair the auth issue."),
    ("DEBUG", "Diagnostica y arregla un fallo de permisos."),
    ("TEST", "Rode a suíte de testes do projeto."),
    ("TEST", "Launch the regression test suite."),
    ("TEST", "Ejecuta las pruebas de regresión."),
    ("REVIEW", "Avalie o código e aponte problemas sem alterar."),
    ("REVIEW", "Audit the code for defects; do not edit it."),
    ("REVIEW", "Inspecciona el código y señala fallos, sin cambios."),
    ("DOCUMENT", "Documente a configuração no README."),
    ("DOCUMENT", "Write documentation for environment setup."),
    ("DOCUMENT", "Redacta una guía de configuración."),
    ("REFACTOR", "Refatore o módulo de autenticação preservando as interfaces."),
    ("REFACTOR", "Clean up the authentication module without API changes."),
    ("REFACTOR", "Refactoriza el módulo de autenticación sin alterar las firmas."),
)

def test_calibration_score_distribution():
    from kitt.context_filter.local_intent import EXAMPLES
    provider = OllamaEmbeddingProvider(
        _MODEL, "http://127.0.0.1:11434", timeout_seconds=30,
        total_timeout_seconds=120, batch_size=32,
    )
    prototypes = provider.embed_documents([s for _, s in EXAMPLES])
    probes = provider.embed_queries([s for _, s in _CALIBRATION])
    details = []
    for ((expected, text), probe) in zip(_CALIBRATION, probes, strict=True):
        by_intent = {}
        for (intent, _), prototype in zip(EXAMPLES, prototypes, strict=True):
            by_intent.setdefault(intent, []).append(_cosine(probe, prototype))
        ranked = sorted(
            ((round(sum(sorted(values, reverse=True)[:2]) / 2, 5), label)
             for label, values in by_intent.items()),
            reverse=True,
        )
        details.append({
            "expected": expected, "pred": ranked[0][1],
            "score": ranked[0][0], "margin": round(ranked[0][0] - ranked[1][0], 5),
            "correct": ranked[0][1] == expected,
        })
    print(json.dumps({"experiment": "e5_calibration_scores_query_passage",
                      "cases": details}, ensure_ascii=False))
