# Web surface — agent guidelines

## Local development

`README.md` covers the zero-services run. These exports put the workspace outside the repo, so a
branch switch or a `git clean` leaves the database, the dev secrets, and the CLI token alone, and
two worktrees do not share one `~/.ufoctl`. Re-export them in every terminal.

```bash
export UFO_REPO="$(git rev-parse --show-toplevel)"
export UFO_DEV_DIR="$HOME/.ufo-dev/$(basename "$UFO_REPO")"
export UFO_CONFIG="$UFO_DEV_DIR/ufo.toml"
export UFOCTL_DIR="$UFO_DEV_DIR/ufoctl"
export FRONTEND="$UFO_REPO/extensions/web/frontend"
```

### Bootstrap once per worktree

```bash
uv sync
npm --prefix "$FRONTEND" ci
npm --prefix "$FRONTEND" run build

mkdir -p "$UFO_DEV_DIR" && cd "$UFO_DEV_DIR"
export ANTHROPIC_API_KEY=...
uv run --project "$UFO_REPO" ufoctl init --email developer@local.test
printf 'ANTHROPIC_API_KEY=%s\n' "$ANTHROPIC_API_KEY" >> "$UFO_DEV_DIR/.env"
chmod 600 "$UFO_DEV_DIR/.env"
```

`init` mints the remaining dev secrets into `$UFO_DEV_DIR/.env` and every later verb auto-loads it,
so nothing needs re-exporting. Add provider credentials with `ufoctl credential set`; keep none of
them in the repo.

### Edit loop

Backend, in `$UFO_DEV_DIR`, restarted after a Python change:

```bash
uv run --project "$UFO_REPO" ufoctl serve
```

Frontend, reloading on every source change:

```bash
npm --prefix "$FRONTEND" run dev -- --host 127.0.0.1
```

Run `uv run --project "$UFO_REPO" ufoctl portal` once from `$UFO_DEV_DIR` to land the session
cookie, then edit against `http://127.0.0.1:5173/surface/web`. Use `127.0.0.1`, not `localhost`:
the cookie is scoped to the host the portal handshake set it on, and `serve` binds `127.0.0.1`.

The dev server answers the page and its assets from source and proxies every read to `:8710`, so
the built tree under `ufo_ext_web/static` is out of the loop entirely — never rebuild it while
iterating. Port `8710` serves the backend and that built tree; `5173` serves current source.

Two of these running at once need different `[serve] port` values in their `$UFO_DEV_DIR/ufo.toml`.

### What does not need re-running

- `extensions/web` is on the editable path — `uv run python -c "import ufo_ext_web;
  print(ufo_ext_web.__file__)"` prints the repo, not the venv. A Python edit needs a `serve`
  restart and nothing else. `uv sync` earns a re-run when `pyproject.toml` moves an entry point or
  a dependency, never after an edit.
- `npm ci` earns a re-run when `package-lock.json` changes.
- `npm run build` earns a re-run only for something that reads the built tree: `docker compose`, or
  the four `test_ext_web.py` tests below.

### Focused checks

A bare `uv run pytest` collects 6911 tests over the SQLite and Postgres matrix and is what makes an
edit loop take minutes — nothing else here does. Name a path every time, and an SQLite-only `-k`
while iterating:

```bash
uv run pytest extensions/web/tests/test_ext_web.py -k "sqlite and <name>"   # ~9s
npm --prefix "$FRONTEND" test -- tests/chat.test.tsx                        # ~2s
```

The whole of `test_ext_web.py` is ~45s; save it for the finished change. Four of its tests read
`ufo_ext_web/static/index.html` and fail with the build command named when the frontend has never
been built in this worktree.

### When a request is slow

Read the `ufoctl serve` log before rebuilding anything. Model calls, site building, scheduled work,
and another worker sharing the workspace dominate request time — a rebuild changes none of them.
If startup fails, check the provider credential, ports `8710` and `5173`, and whether the pack's
entry points are current.
