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
ARG FRIDA_VERSION=17.23.3
ARG FRIDA_TOOLS_VERSION=14.11.0
RUN python3 -m venv /opt/frida \
 && /opt/frida/bin/pip install --no-cache-dir \
    "frida==${FRIDA_VERSION}" "frida-tools==${FRIDA_TOOLS_VERSION}"
ENV PATH="/opt/frida/bin:${PATH}"
CMD ["/bin/sh"]
