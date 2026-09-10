# The dependencies install at start into the slot's own volume rather than into this image: the
# host's node_modules hold macOS binaries, and a lockfile change then needs no image rebuild.
FROM node:24.19.0-bookworm-slim
WORKDIR /app/deps
COPY extensions/web/frontend/package.json ./
RUN corepack enable && corepack install
ENTRYPOINT ["/app/dev/web.sh"]
