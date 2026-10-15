# Agent CLI 0.87.20

- Require original content hash for vector.upsert after memory.remember.
- Single background workspace backfill pass, max four items, derived from durable Memory 0.10.2 state.
- No synchronous model work added to backfill scheduling, no external network egress.
- Skip secret and ephemeral automatic embedding; keep FTS5 fallback.
- Batch full reference edge inserts; package, uv.lock and runtime fallback aligned.
