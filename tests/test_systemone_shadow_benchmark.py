"""Benchmark should score model outputs without affecting actual KITT routing."""
from scripts.benchmark_systemone_decisions import evaluate, _local_endpoint


def test_shadow_evaluation_reports_accuracy_and_baseline():
    cases = [{
        "state": "pytest failed", "expected": {"routing": "retry"},
        "baseline": {"routing": "stop"},
        "questions": {"routing": {"type": "choice", "instructions": "Next step?",
                                   "criteria": {"retry": None, "stop": None}}},
    }]
    result = evaluate(cases, lambda _: {
        "answers": {"routing": {"choice": "retry",
                                "probabilities": {"retry": 0.8, "stop": 0.2}}}
    })
    assert result["accuracy"] == 1.0
    assert result["baseline_accuracy"] == 0.0
    assert abs(result["brier"] - 0.08) < 0.00001
    assert result["mode"] == "shadow_only"


def test_only_loopback_decision_api_is_accepted():
    assert _local_endpoint("http://localhost:11435/v1/systemone")
    for target in ("https://localhost:11435/v1/systemone",
                   "http://example.org/v1/systemone",
                   "http://127.0.0.1:11435/v1/chat/completions"):
        try:
            _local_endpoint(target)
            assert False, "off-contract endpoint accepted"
        except ValueError:
            pass

def test_shadow_benchmark_supports_noul_and_score_without_dispatching_tools():
    cases = [{
        "state": "pytest blocked", "expected": {"blocked": True, "severity": 2.5},
        "questions": {
            "blocked": {"type": "noul", "instructions": "Is validation blocked?"},
            "severity": {"type": "score", "instructions": "Rate the severity",
                         "criteria": ["low", "medium", "high", "critical"]},
        },
    }]
    result = evaluate(cases, lambda _: {"answers": {
        "blocked": {"noul": 0.8}, "severity": {"score": 2.1},
    }})
    assert result["answers"] == 2
    assert result["noul_accuracy"] == 1.0
    assert abs(result["noul_brier"] - 0.04) < 0.000001
    assert abs(result["score_mae"] - 0.4) < 0.000001
