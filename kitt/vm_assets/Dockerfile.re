# REA has a Node 22.19+/24.11+ runtime requirement; Debian Trixie ships Node 20.
# Install the published version in an isolated, build-time-only Node stage.
FROM node:24.11.0-bookworm-slim AS rea-cli
ARG REA_VERSION=6.4.0
RUN npm install --global --prefix /opt/rea --no-audit --no-fund "rea-agents@${REA_VERSION}" \
 && /opt/rea/bin/rea --version

# Prebuilt Linux RE toolbox: analysis tools run in a *container*, not a VM.
FROM debian:trixie-slim AS base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git file binutils gdb strace ltrace \
    build-essential clang lldb cmake ninja-build pkg-config \
    python3 python3-venv python3-pip default-jdk-headless \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
CMD ["/bin/sh"]
FROM base AS wine
RUN apt-get update && apt-get install -y --no-install-recommends \
    wine64 mingw-w64 && rm -rf /var/lib/apt/lists/*
CMD ["/bin/sh"]
FROM wine AS full
# Keep the REA runtime in full only. base/wine images remain lightweight.
COPY --from=rea-cli /usr/local/bin/node /usr/local/bin/node
COPY --from=rea-cli /opt/rea /opt/rea
ENV PATH="/opt/rea/bin:${PATH}"
ARG GHIDRA_SHA256=ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db
RUN apt-get update && apt-get install -y --no-install-recommends unzip \
 && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --retry 3 --proto "=https" \
    https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip \
    --output /tmp/ghidra.zip \
 && echo "${GHIDRA_SHA256}  /tmp/ghidra.zip" | sha256sum --check - \
 && unzip -q /tmp/ghidra.zip -d /opt \
 && rm /tmp/ghidra.zip \
 && ln -s /opt/ghidra_12.1.4_PUBLIC /opt/ghidra
ENV GHIDRA_HOME=/opt/ghidra
ENV GHIDRA_INSTALL_DIR=/opt/ghidra
ENV PATH="/opt/ghidra/support:${PATH}"
ARG FRIDA_VERSION=17.23.3
ARG FRIDA_TOOLS_VERSION=14.11.0
RUN python3 -m venv /opt/frida \
 && /opt/frida/bin/pip install --no-cache-dir \
    "frida==${FRIDA_VERSION}" "frida-tools==${FRIDA_TOOLS_VERSION}"
ENV PATH="/opt/frida/bin:${PATH}"
# Validate that the installed REA CLI, Node runtime, Ghidra and Frida are available.
RUN node --version \
 && rea --version \
 && test -f /opt/ghidra/support/analyzeHeadless \
 && /opt/frida/bin/python -c 'import frida; print(frida.__version__)'
CMD ["/bin/sh"]
