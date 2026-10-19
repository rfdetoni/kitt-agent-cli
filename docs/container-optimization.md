# Container optimization evaluation

KITT Agent CLI uses Python 3.14 slim, non-root `kitt`, a reduced APT package list and a persistent agent data volume. A BuildKit pip cache now speeds repeat image builds without adding downloaded archives to the resulting image.

For read-only SlimToolkit 1.40.11 analysis: `bash scripts/slim-audit.sh IMAGE_TAG`. This coding agent runs git, ssh, ripgrep and other subprocess tools conditionally; execution-time profiling of only `kitt --help` will underapproximate dependencies. Preserve tool binaries, CA bundles, Python modules, dynamic plugins, and container/SSH access. No optimized image is promoted without end-to-end agent/task tests.
