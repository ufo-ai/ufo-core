# The Rust control plane, built once and copied in beside the Python runtime below, so one dev
# image still serves every compose service. The registry and target caches persist across builds,
# so a src change recompiles only this crate, never its dependencies; the binary is copied out
# because a cache mount's contents are not part of the image.
FROM rust:1-bookworm AS control
WORKDIR /control
COPY servers/control/Cargo.toml servers/control/Cargo.lock ./
COPY servers/control/src ./src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/control/target \
    cargo build --release --locked --bin ufo-control \
    && cp target/release/ufo-control /usr/local/bin/ufo-control

FROM golang:1.27.0-bookworm AS gh
RUN GOTOOLCHAIN=local CGO_ENABLED=0 \
    go install -trimpath -ldflags='-s -w' github.com/cli/cli/v2/cmd/gh@v2.97.0 \
    && gzip -9 /go/bin/gh

FROM node:24.19.0-bookworm-slim AS node
RUN npm install -g --prefix /usr/local --no-fund --no-audit vite playwright@1.62.0

FROM rust:1-bookworm AS client
WORKDIR /client
# Cargo validates every target the manifest declares before it selects one, so the benches the
# manifest names are copied even though this stage builds only the binary.
COPY client/Cargo.toml client/Cargo.lock ./
COPY client/build.rs ./
COPY client/licenses ./licenses
COPY client/src ./src
COPY client/benches ./benches
COPY --from=gh /go/bin/gh.gz /tmp/ufo-gh.gz
ENV UFO_GH_ARCHIVE=/tmp/ufo-gh.gz
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/client/target \
    cargo build --release --locked --bin ufo \
    && cp target/release/ufo /usr/local/bin/ufo

# One image for the whole local stack: it carries both entry points — `ufoctl` (core serve, proxy,
# migrate) and `ufo-control` (gateway, migrate, rls-bootstrap) — so every compose service is this
# image with a different command. Built from source via uv, not the production `ufoctl bundle`
# image (that needs a bundle step, far too heavy for a local loop).
FROM python:3.12-slim

# `docker` CLI for the docker sandbox carrier (it shells out to `docker`, reaching the host daemon
# through the mounted socket); the rest are wheel-build basics.
RUN apt-get update && apt-get install -y --no-install-recommends \
      docker.io ca-certificates git build-essential libpq-dev postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv
COPY --from=control /usr/local/bin/ufo-control /usr/local/bin/ufo-control
COPY --from=client /usr/local/bin/ufo /usr/local/bin/ufo
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/vite/bin/vite.js /usr/local/bin/vite \
    && ln -s ../lib/node_modules/playwright/cli.js /usr/local/bin/playwright

ENV NODE_PATH=/usr/local/lib/node_modules
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/local/lib/playwright

RUN playwright install-deps chromium \
    && playwright install chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chromium_path="$(node -e "process.stdout.write(require('playwright').chromium.executablePath())")" \
    && test -x "$chromium_path" \
    && ln -s "$chromium_path" /usr/local/bin/chromium \
    && chromium --version

# The local Postgres speaks no TLS, so `sslmode=prefer` falls back to plaintext and no bundle is
# needed here — the compose DSNs stay as they are.

WORKDIR /app
COPY . .
# Install core `ufo` with every bundled extension and pack into /app/.venv, then prove the pack's
# extensions actually import from the install. The wheel cache persists across builds; copy link
# mode keeps the venv self-contained since the cache is gone at runtime.
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_LINK_MODE=copy uv sync --frozen --no-dev --all-packages \
    && /app/.venv/bin/python -c "import ufo_ext_memory, ufo_ext_research, ufo_ext_sites"

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["/app/dev/entrypoint.sh"]
