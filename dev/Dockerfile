# One image for the whole local stack: it carries both entry points — `ufoctl` (core serve,
# proxy, migrate) and `ufo-control` (gateway, rls-bootstrap) — so every compose service is this
# image with a different command. Built from source via uv, not the production `ufoctl bundle`
# image (that needs an npm build + bundle step, far too heavy for a local loop).
FROM python:3.12-slim

# `docker` CLI for the docker sandbox carrier (it shells out to `docker`, reaching the host daemon
# through the mounted socket); the rest are wheel-build basics.
RUN apt-get update && apt-get install -y --no-install-recommends \
      docker.io ca-certificates git build-essential libpq-dev postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY . .
# Install the whole workspace (core `ufo` with every bundled extension + pack, and `ufo-control`)
# into /app/.venv, then prove the pack's extensions actually import from the install.
RUN uv sync --frozen --no-dev --all-packages \
    && /app/.venv/bin/python -c "import ufo_ext_memory, ufo_ext_research, ufo_ext_sites, ufo_control.gateway"

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["/app/dev/entrypoint.sh"]
