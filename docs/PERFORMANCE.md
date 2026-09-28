# Performance and benchmark methodology

Performance claims in KITT must be reproducible and scoped to the measured layer.

## Existing benchmarks

Run from the repository root:

```bash
python benchmarks/native_engine_bench.py /path/to/repo --query Service --rounds 8
python benchmarks/scale_benchmark.py --files 1000
python benchmarks/safe_runtime_benchmark.py
python scripts/benchmark_tui.py
python scripts/benchmark_context_budget.py
```

## Python vs native comparison

`benchmarks/native_engine_bench.py` reports the selected backend through `engine.status`. Compare the same repository, query, machine and round count twice:

1. with the optional `kitt_native` wheel unavailable/disabled so the portable Python path is selected;
2. with the validated `kitt_native` wheel installed so native acceleration is selected.

Record:

- backend identity/status;
- mean latency;
- p95 latency;
- result count;
- repository size;
- CPU/model/machine;
- Python version;
- native/toolbox revision.

Do not compare different repositories or queries and call the result a backend speedup.

## Benchmark levels

**Microbenchmark:** one deterministic operation, useful for regression detection.

**Component benchmark:** repository indexing/search/runtime behavior under a controlled workload.

**End-to-end Agent benchmark:** user objective through provider/tool loop and validation. This includes provider/network/model variance and must not be presented as native-engine performance alone.

## Resource measurements

Latency is always collected. CPU and resident memory should be collected by the external OS/process harness when a performance investigation needs them; the benchmark scripts intentionally avoid adding a runtime dependency solely for metrics.

## Reporting

When documenting a performance change, include the command, revisions, machine description, input size and repeated-run statistics. Prefer p50/p95/p99 or mean+p95 over one-shot timings.

No benchmark result is a compatibility contract. Correctness, bounded resources and security gates take priority over throughput.
