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
# Provide Debian Wine as a stable comparison baseline, including its
# user-facing launcher (wine64 alone does not install /usr/bin/wine).
FROM base AS wine
RUN apt-get update && apt-get install -y --no-install-recommends \
    wine wine64 mingw-w64 fonts-wine gnupg \
 && rm -rf /var/lib/apt/lists/*

# WineHQ Staging is an actual independent Wine distribution, not a renamed
# Debian Wine binary. Keep the Debian /usr/bin/wine as the reference engine.
# WineHQ's signed Debian Trixie repo supplies /opt/wine-staging/bin/wine.
# The known long-lived WineHQ key fingerprint is verified before apt uses it.
ARG WINEHQ_KEY_FINGERPRINT=D43F640145369C51D786DDEA76F1A20FF987672F
RUN set -eu; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl --fail --location --retry 3 --proto '=https' \
      https://dl.winehq.org/wine-builds/winehq.key \
      --output /etc/apt/keyrings/winehq-archive.asc; \
    actual="$(gpg --batch --show-keys --with-colons \
      /etc/apt/keyrings/winehq-archive.asc | awk -F: '$1=="fpr" {print $10; exit}')"; \
    test "$actual" = "$WINEHQ_KEY_FINGERPRINT"; \
    printf '%s\n' \
      'Types: deb' \
      'URIs: https://dl.winehq.org/wine-builds/debian' \
      'Suites: trixie' \
      'Components: main' \
      'Architectures: amd64' \
      'Signed-By: /etc/apt/keyrings/winehq-archive.asc' \
      > /etc/apt/sources.list.d/kitt-winehq-staging.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      wine-staging wine-staging-amd64; \
    rm -rf /var/lib/apt/lists/*; \
    test -x /usr/bin/wine; \
    test -x /usr/lib/wine/wine64; \
    test -x /opt/wine-staging/bin/wine; \
    ln -s /usr/lib/wine/wine64 /usr/local/bin/wine64; \
    ln -s /opt/wine-staging/bin/wine /usr/local/bin/wine-staging; \
    install -d -m 0755 /usr/share/kitt; \
    dpkg-query -W -f='${Package}=${Version}\n' \
      wine wine64 wine-staging wine-staging-amd64 \
      > /usr/share/kitt/re-wine-packages.lock; \
    wine --version; \
    wine64 --version; \
    wine-staging --version
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
# Validate both independent Wine distributions, REA, Ghidra and Frida at build time.
RUN wine --version && wine64 --version && wine-staging --version \
 && test -s /usr/share/kitt/re-wine-packages.lock \
 && node --version \
 && rea --version \
 && test -f /opt/ghidra/support/analyzeHeadless \
 && /opt/frida/bin/python -c 'import frida; print(frida.__version__)'
CMD ["/bin/sh"]
