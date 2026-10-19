#!/usr/bin/env bash
set -euo pipefail
IMAGE="${1:?Usage: slim-audit.sh IMAGE_TAG}"
command -v docker >/dev/null || exit 1
command -v slim >/dev/null || { echo "SlimToolkit CLI required" >&2; exit 1; }
docker image inspect --format '{{.RepoTags}} {{.Size}} bytes (virtual uncompressed)' "$IMAGE"
slim xray "$IMAGE"
echo "No minification: git, SSH, ripgrep, shell tools and optional Python agent tools must be exercised before pruning."
