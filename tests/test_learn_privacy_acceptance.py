from __future__ import annotations

from kitt.learn.service import LearnService, safe_tool_signature


SENTINEL = "KITT_PRIVACY_SENTINEL_24680"


def test_kitt_learn_never_exposes_raw_sensitive_tool_arguments():
    signature = safe_tool_signature(
        {
            "tool_name": "kitt_runtime",
            "args": {
                "operation": "process.run",
                "arguments": {
                    "argv": ["python", "-c", f"print({SENTINEL!r})"],
                    "env": {"SENSITIVE_VALUE": SENTINEL},
                },
            },
        }
    )
    assert SENTINEL not in signature
    assert signature == "Bash(other)"

    read_signature = safe_tool_signature(
        {
            "tool_name": "read_file",
            "args": {"path": f"private/{SENTINEL}/.env"},
        }
    )
    assert SENTINEL not in read_signature
    assert read_signature == "Read(file)"


def test_kitt_learn_experiments_never_auto_promote_candidates():
    evidence_required = LearnService._experiment_verdict(
        {"observed": False},
        {"observed": False},
    )
    assert evidence_required["state"] == "EVIDENCE_REQUIRED"
    assert evidence_required["promote"] is False

    control = {
        "observed": True,
        "turns": 4,
        "success": {"rate": 1.0},
        "validation": {"observed": True, "rate": 1.0},
        "tokens": {"input": 1000, "output": 500},
        "latency": {"avg_ms": 500.0},
    }
    candidate = {
        "observed": True,
        "turns": 4,
        "success": {"rate": 1.0},
        "validation": {"observed": True, "rate": 1.0},
        "tokens": {"input": 700, "output": 300},
        "latency": {"avg_ms": 350.0},
    }
    better = LearnService._experiment_verdict(control, candidate)
    assert better["state"] == "CANDIDATE_BETTER"
    assert better["measurable_gain"] is True
    assert better["promote"] is False
