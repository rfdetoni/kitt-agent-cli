# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

ARG KITT_UID=1000
ARG KITT_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/kitt

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        jq \
        openssh-client \
        ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "$KITT_GID" kitt \
    && useradd --uid "$KITT_UID" --gid "$KITT_GID" --create-home --shell /bin/bash kitt

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY kitt ./kitt
RUN python -m pip install --no-cache-dir .

RUN mkdir -p /workspace /home/kitt/.kitt \
    && chown -R kitt:kitt /workspace /home/kitt /app

USER kitt
WORKDIR /workspace

VOLUME ["/home/kitt/.kitt"]

ENTRYPOINT ["kitt"]
CMD ["--root", "/workspace"]
