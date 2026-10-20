"""Shadow-only System One decision benchmark; never authorizes or dispatches tools.

Usage:
    python scripts/benchmark_systemone_decisions.py --dataset cases.jsonl \\
      --model laya --url http://127.0.0.1:11435/v1/systemone

Dataset JSONL: {"state":"...", "questions":{"routing":{...choice schema...}},
"expected":{"routing":"retry"}, "baseline":{"routing":"retry"}}.
Results measure choice accuracy and p50/p95 latency. No production KITT
control-flow changes are permitted by this evaluator.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def _local_endpoint(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1", "::1", "localhost",
    } or parsed.username or parsed.password or parsed.path != "/v1/systemone":
        raise ValueError("Decision benchmark requires an explicit loopback /v1/systemone endpoint")
    return url


def load_cases(path: str, *, max_cases: int = 512) -> list[dict]:
    result = []
    with Path(path).open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            if len(result) >= max_cases:
                raise ValueError("Dataset exceeds the bounded case limit")
            case = json.loads(line)
            if not isinstance(case, dict) or not case.get("questions") or not isinstance(case.get("expected"), dict):
                raise ValueError(f"Invalid dataset case at line {number}")
            result.append(case)
    if not result:
        raise ValueError("Dataset is empty")
    return result


def decide_http(url: str, model: str, case: dict, *, timeout: float) -> dict:
    body = json.dumps({
        "model": model, "state": case["state"], "questions": case["questions"],
    }, ensure_ascii=False).encode("utf-8")
    if len(body) > 64 * 1024:
        raise ValueError("Decision state exceeds 64 KiB")
    req = urllib.request.Request(
        url, body, {"Content-Type": "application/json"}, method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as response:
        payload = response.read(256 * 1024 + 1)
        if len(payload) > 256 * 1024:
            raise ValueError("Decision response exceeds 256 KiB")
    output = json.loads(payload)
    if not isinstance(output, dict) or not isinstance(output.get("answers"), dict):
        raise ValueError("Decision runtime returned invalid answers")
    return output


def evaluate(cases: list[dict], decide) -> dict:
    correct = total = baseline_correct = baseline_total = invalid = 0
    latencies: list[float] = []
    brier_values: list[float] = []
    for case in cases:
        started = time.perf_counter()
        try:
            payload = decide(case)
            answers = payload["answers"]
            if not isinstance(answers, dict):
                raise ValueError("Missing answers")
            for key, expected in case["expected"].items():
                if not isinstance(expected, str):
                    raise ValueError("Expected choice must be a string")
                baseline = case.get("baseline") or {}
                if key in baseline:
                    baseline_total += 1
                    baseline_correct += int(baseline[key] == expected)
                ans = answers.get(key)
                if not isinstance(ans, dict) or not isinstance(ans.get("choice"), str):
                    invalid += 1
                    continue
                total += 1
                correct += int(ans["choice"] == expected)
                probabilities = ans.get("probabilities")
                if isinstance(probabilities, dict):
                    numbers = {str(k): float(v) for k, v in probabilities.items()}
                    if numbers and all(0 <= v <= 1 for v in numbers.values()):
                        brier_values.append(sum(
                            (v - float(k == expected)) ** 2 for k, v in numbers.items()
                        ))
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
            invalid += max(1, len(case["expected"]))
        finally:
            latencies.append((time.perf_counter() - started) * 1000)
    ordered = sorted(latencies)
    return {
        "cases": len(cases), "answers": total, "correct": correct,
        "accuracy": correct / total if total else None,
        "baseline_accuracy": baseline_correct / baseline_total if baseline_total else None,
        "invalid_answers": invalid,
        "brier": statistics.mean(brier_values) if brier_values else None,
        "latency_p50_ms": statistics.median(ordered),
        "latency_p95_ms": ordered[min(len(ordered) - 1, max(0, int(len(ordered) * .95) - 1))],
        "mode": "shadow_only",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--url", default="http://127.0.0.1:11434/v1/systemone")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--max-cases", type=int, default=512)
    args = parser.parse_args()
    if not 0 < args.timeout <= 120 or not 0 < args.max_cases <= 512:
        parser.error("timeout and max-cases must be within bounded positive ranges")
    url = _local_endpoint(args.url)
    cases = load_cases(args.dataset, max_cases=args.max_cases)
    report = evaluate(cases, lambda case: decide_http(url, args.model, case, timeout=args.timeout))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
