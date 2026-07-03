FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/usr/local/bin:/usr/bin:/bin" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/sandbox

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
        poppler-utils \
        ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace
