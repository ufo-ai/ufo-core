FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/sandbox

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
        passwd \
        poppler-utils \
        ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash sandbox \
    && mkdir -p /workspace \
    && chown sandbox:sandbox /workspace

COPY sbxfs /usr/local/bin/sbxfs
RUN chmod 0755 /usr/local/bin/sbxfs

USER sandbox
WORKDIR /workspace
