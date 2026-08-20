# The Rust control plane, built once and copied in beside the Python runtime below, so one dev
# image still serves every compose service. The registry and target caches persist across builds,
# so a src change recompiles only this crate, never its dependencies; the binary is copied out
# because a cache mount's contents are not part of the image.
FROM rust:1-bookworm AS control
WORKDIR /control
COPY control/Cargo.toml control/Cargo.lock ./
COPY control/src ./src
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/control/target \
    cargo build --release --locked --bin ufo-control \
    && cp target/release/ufo-control /usr/local/bin/ufo-control

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
