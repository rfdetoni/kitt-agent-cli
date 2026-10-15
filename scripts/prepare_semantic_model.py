"""Explicit local Ollama E5 preparation. Never called during normal startup."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

from kitt.context.rag.embeddings import EmbeddingError, OllamaEmbeddingProvider

DEFAULT_MODEL = "qllama/multilingual-e5-small:q8_0"
# Verified with the real CPU integration in GitHub Actions.
DEFAULT_DIGEST = "96316f5cc78414b0418c9c948934064dda96bf14b903ba357a4db75a6e538813"
LOCAL_URL = "http://127.0.0.1:11434"


def prepare(model: str, digest: str, *, pull: bool) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Expected SHA-256 must have exactly 64 lowercase hex digits")
    # Validate the address before invoking the Ollama executable. It cannot be
    # redirected by OLLAMA_HOST to a remote endpoint for this operation.
    provider = OllamaEmbeddingProvider(model, LOCAL_URL, timeout_seconds=20)
    if pull:
        env = dict(os.environ, OLLAMA_HOST="127.0.0.1:11434")
        subprocess.run(["ollama", "pull", model], check=True, env=env)
    identity = provider.cache_identity()
    observed = identity.split("|sha256:", 1)[1].split("|", 1)[0]
    if observed != digest:
        raise EmbeddingError(
            "Local model digest mismatch; model changed or is not the verified build"
        )
    return observed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify or explicitly download local E5 weights")
    parser.add_argument("--pull", action="store_true", help="explicitly download weights via local Ollama")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--digest", default=DEFAULT_DIGEST, help="expected 64-char SHA-256")
    args = parser.parse_args(argv)
    if args.model != DEFAULT_MODEL and args.digest == DEFAULT_DIGEST:
        parser.error("Custom models must explicitly specify their own --digest")
    try:
        actual = prepare(args.model, args.digest, pull=args.pull)
    except (EmbeddingError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Local semantic preparation unavailable: {exc}", file=sys.stderr)
        return 1
    print(f"Verified local embedding model {args.model}: sha256:{actual}")
    print("Enable with KITT_SEMANTIC_MODE=auto and KITT_SEMANTIC_LOCAL_MODEL=" + args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
