# Agent CLI 0.87.19 — opt-in local vector memory

- `KITT_MEMORY_EMBED_MODEL` unset by default; select an installed local Ollama embedding model to enable.
- `KITT_MEMORY_EMBED_URL` defaults to http://127.0.0.1:11434 and requires loopback HTTP.
- Existing OllamaEmbeddingProvider with 0.9 second total budget; query results cached in RAM (128 entries).
- Successful remembers schedule a bounded 16-entry, single-worker embedding+vector.upsert queue.
- Queries supply model/embedding to kitt-memoryd >=0.10.0; model failures degrade to FTS5.
- No new dependencies; 0.87.19 is reflected in uv.lock.
- Jobs are best effort and are not durable across abrupt Agent termination; existing memories require deliberate backfill. Future work: queue durability and evidence-based benchmark against vector ANN.
