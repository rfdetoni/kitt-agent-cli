# K.I.T.T. Agent CLI 0.88.30

Reliability-focused improvements over 0.88.28/0.88.29: scoped autonomy defaults, mandatory polyglot AGENTS.md preservation, bounded instruction bodies, host Goal budget checkpoints, independent batched repo.search, retained-child egress validation, and better categorization of sandbox and ambiguous Reverse-Proxy errors.

Shadow benchmark: `python scripts/benchmark_systemone_decisions.py --dataset decisions.jsonl --model laya --url http://127.0.0.1:11435/v1/systemone`. This is opt-in observation, not production model routing. Requires actual local decision model and representative labeled data for meaningful quality claims.

Before claiming production success, run all GitHub Agent PR Checks and validate authenticated browser/VM/Clean-Room behavior on an actual host with a functional strong sandbox.
