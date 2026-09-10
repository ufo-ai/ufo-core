#!/usr/bin/env bash
set -euo pipefail

# pnpm rewrites the lockfile beside the manifest on every install, and the source is mounted
# read-only: it installs from a copy of the manifest into the volume the frontend also mounts.
mkdir -p /app/deps
cp /app/extensions/web/frontend/package.json /app/extensions/web/frontend/pnpm-lock.yaml \
    /app/extensions/web/frontend/pnpm-workspace.yaml /app/deps/
(cd /app/deps && pnpm install --frozen-lockfile --store-dir /app/deps/node_modules/.pnpm-store)
cd /app/extensions/web/frontend
trap 'kill $(jobs -p) 2>/dev/null' INT TERM
node_modules/.bin/vite --config "$UFO_WEB_VITE_CONFIG" --host 0.0.0.0 --port 5173 --strictPort &
node_modules/.bin/vite --config vite.apps.config.ts --host 0.0.0.0 --port 5174 --strictPort &
wait -n
