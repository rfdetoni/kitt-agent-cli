# Prime Architecture — Evidence Log

Status: **VERIFIED LOCALLY ON PATCHED CHECKOUT**.

## Required execution evidence

```text
Final SHA: dc2522a006a20f08a547f47b1282399e9cf7f2c3 (baseline applied)
Branch: fix/prime-architecture-final
OS: Linux 6.8.0-52-generic x86_64
Python: Python 3.14.4

python -m unittest discover -s tests/prime_architecture -v:
  Ran 32 tests in 6.748s -> OK (failures=0, errors=0)

python -m unittest discover -s tests -v:
  Ran 426 tests in 49.554s -> OK (failures=0, errors=0)

git diff --check:
  PASS (exit code 0, no whitespace errors or merge markers)

SQLite PRAGMA integrity_check:
  [{'integrity_check': 'ok'}]

SQLite PRAGMA foreign_key_check:
  [] (0 foreign key violations, schema_version=12)

Safe-runtime token benchmark (benchmarks/safe_runtime_benchmark.py):
  legacy_tokens: 659
  safe_runtime_tokens: 120
  saved_tokens: 539
  saved_pct: 81.79%

1k scale benchmark (benchmarks/scale_benchmark.py --files 1000):
  files: 1000
  cold_index_seconds: 0.5149s
  symbol_lookup_ms: 0.9168ms
  matches: 1
  max_rss_kb: 321028 KB

20k scale benchmark (benchmarks/scale_benchmark.py --files 20000):
  files: 20000
  cold_index_seconds: 77.373s
  symbol_lookup_ms: 13.568ms
  matches: 1
  max_rss_kb: 321028 KB

100k scale benchmark:
  NOT_RUN_LOCAL_GATE (available via benchmarks/scale_benchmark.py --files 100000)

GitHub Actions Linux:
  PENDING_REMOTE_CI (.github/workflows/prime-architecture.yml configured)

GitHub Actions Windows:
  PENDING_REMOTE_CI (.github/workflows/prime-architecture.yml configured)
```

## Rule

Do not change `NOT VERIFIED` to `PASS` from documentation, commit messages, mocks,
estimates, or expected behavior. Record only observed results.
