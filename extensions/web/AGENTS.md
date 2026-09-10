# Web surface — agent guidelines

## Two shells

`frontend/` builds two portal shells into one static tree: the lanes shell (`src/`, built to
`static/index.html`) and the sidebar shell (`sidebar/`, apps and chats in one sidebar, built to
`static/sidebar.html`). `src/` owns their portal, theme, components, kernel, views, assets, and
entry point. `sidebar/src/` holds only the sidebar's navigation and route seams; its config resolves
every other `@/` import to `src/`. `portal_page` serves the sidebar shell unless
`enable-lanes-shell` answers true for the workspace; every silence of the flag read serves the
sidebar shell. Both shells share `package.json` and its lockfile, and `pnpm test` runs their suites
as vitest projects (`lanes`, `sidebar`).

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
pnpm -C "$FRONTEND" install --frozen-lockfile
pnpm -C "$FRONTEND" run build

mkdir -p "$UFO_DEV_DIR" && cd "$UFO_DEV_DIR"
export UFO_ANTHROPIC_API_KEY=...
uv run --project "$UFO_REPO" ufoctl init --email developer@local.test
printf 'UFO_ANTHROPIC_API_KEY=%s\n' "$UFO_ANTHROPIC_API_KEY" >> "$UFO_DEV_DIR/.env"
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
pnpm -C "$FRONTEND" run dev --host ufo.localhost
```

The sidebar shell has the same loop under its own config:

```bash
pnpm -C "$FRONTEND" run dev --config sidebar/vite.config.ts --host ufo.localhost
```

Pass those flags bare. A `--` before them is swallowed by pnpm, and vite then serves the lanes
shell on `:5173` under its own config as though none were named.

Run `uv run --project "$UFO_REPO" ufoctl portal` once from `$UFO_DEV_DIR` to land the session
cookie, then edit against `http://ufo.localhost:5173/surface/web`. Use `ufo.localhost` on both:
`init` writes `public_base_url = "http://ufo.localhost:8710"`, `portal` opens it, and the session
cookie binds to that host — a dev server on any other host never sees it.

The dev server answers the page and its assets from source and proxies every read to `:8710`, so
the built tree under `ufo_ext_web/static` is out of the loop entirely — never rebuild it while
iterating. Port `8710` serves the backend and that built tree; `5173` serves current source.

Two of these running at once need different `[serve] port` values in their `$UFO_DEV_DIR/ufo.toml`.

### Edit loop against a Docker stack

`make stack STACK=N` serves both frontends from source through a `web` service beside the slot: the
front routes the portal page and its modules to the shell's dev server, and the ingress relays every
shipped app page to the app pages' dev server (`[sandbox] apps_dev_server` in `dev/ufo.toml`). Sign
in at the slot's gateway, then edit against `http://ufo-N.localhost:18080/surface/web` (add 100 per
slot after slot 1): a frontend change hot-updates the open page and every open app frame, except
that an edit to an app's own entry re-runs it whole and so reloads that frame. It serves the sidebar
shell; `SHELL_NAME=lanes` serves the lanes shell instead. `make stack` earns a re-run for a backend
change and nothing else — the image carries the Python tree and the built app bundle, whose digest
the homepage read still names.

### What does not need re-running

- `extensions/web` is on the editable path — `uv run python -c "import ufo_ext_web;
  print(ufo_ext_web.__file__)"` prints the repo, not the venv. A Python edit needs a `serve`
  restart and nothing else. `uv sync` earns a re-run when `pyproject.toml` moves an entry point or
  a dependency, never after an edit.
- `pnpm install` earns a re-run when `pnpm-lock.yaml` changes.
- `pnpm run build` earns a re-run only for something that reads the built tree: `docker compose`,
  the `test_ext_web.py` tests below, and each shell's `tests/agenticon.test.tsx`, which read the
  built pages and serve the icon sprites out of `static/assets`.

### Focused checks

A bare `uv run pytest` collects 6911 tests over the SQLite and Postgres matrix and is what makes an
edit loop take minutes — nothing else here does. Name a path every time, and an SQLite-only `-k`
while iterating:

```bash
uv run pytest extensions/web/tests/test_ext_web.py -k "sqlite and <name>"   # ~9s
pnpm -C "$FRONTEND" test --project lanes tests/chat.test.tsx
```

Pass the path bare, and name a project — a bare path runs its file in both shells' suites. A `--`
before it is swallowed by pnpm rather than handed to vitest, which then matches nothing and runs
both suites whole — 133 files, ~2500 tests — while looking like it filtered.

The whole of `test_ext_web.py` is ~45s; save it for the finished change. Several of its tests read
the built pages under `ufo_ext_web/static` and fail with the build command named when the frontend
has never been built in this worktree.

### When a request is slow

Read the `ufoctl serve` log before rebuilding anything. Model calls, site building, scheduled work,
and another worker sharing the workspace dominate request time — a rebuild changes none of them.
If startup fails, check the provider credential, ports `8710` and `5173`, and whether the pack's
entry points are current.

## Screenshots on a frontend-impacting pull request

A change that alters a screen carries screenshots by default. A reviewer cannot read a layout,
spacing or overflow change out of a diff, so a pull request that touches `frontend/` or anything a
portal page renders posts images or says in one line why it cannot.

Shoot **before and after** at both viewports: desktop 1440×900 and mobile 390×844. Take the before
shot on the base ref and the after shot on the branch, on the same route and the same seeded
content, so the only difference in the pair is the change. `ufoctl serve` binds one port, so bring
one ref up at a time rather than two deploys side by side. Name each file for its route, viewport
and ref, and post one pair per defect or screen — not one pair per route the shell chrome repeats
on.

GitHub needs a URL to draw an image. Commit the shots to the screenshot branch and link them from
there:

```bash
git switch -c ui-sweep-shots origin/ui-sweep-shots 2>/dev/null || git switch -c ui-sweep-shots
mkdir -p ui-sweep/pr-<number> && cp <shots> ui-sweep/pr-<number>/
git add ui-sweep/pr-<number> && git commit -m "ui shots pr-<number>" && git push -u origin ui-sweep-shots
```

The repository is private, so an inline `![](raw.githubusercontent.com/...)` embed stays blank for a
reader whose browser holds only a github.com session. Link the `blob/ui-sweep-shots/...` path on
`github.com` instead, and check the comment after posting it. Screenshots are evidence,
not source: they never join the pull request's own branch.

## Layout: the grid, the rhythm, and the four ways to divide

A screen is composed out of six spacing steps and four kinds of division, and each has one job. A
band that picks a fifth step or a second kind of edge for the same job is what makes two screens
read as two products. The measures are the theme's; the rule is which one a job takes.

### The rhythm — six steps, each with one job

The ramp declares fourteen steps two pixels apart. Composition uses **six**; the rest exist for a
control's own inset and never for the distance between two things a member reads.

| Step | px | The one job |
|---|---|---|
| `hair` | 2 | a rule's weight, a column's gap in a dense run |
| `2xs` | 4 | inside a word: a mark from its label, a delta from its figure, faces in a stack |
| `sm` | 8 | between the parts of one thing: a title from its note, rows of a reference list |
| `2xl` | 16 | between things in one group: cells on a band, cards in a grid, a header from its body |
| `6xl` | 32 | between groups: one band from the next, one column from the next |
| `8xl` | 64 | between a page's regions: the head from the first band |

`BANDS` in `src/kernel/pane.tsx` is `gap-6xl` and `Section` stacks its parts at `gap-6xl`, so a
page's vertical rhythm is 32 between bands and 16 within them, and a component that spaces itself
at any other step breaks the beat for every band under it. A component owns the gaps *inside* it;
the band owns the gap *around* it; nothing carries a margin.

### The grid

One measure, `--container-section` (728px), centred by `COLUMN`. A band divides it into equal
columns at `gap-6xl` — two, three or four, never mixed on one band — and every column below the
narrow breakpoint stacks (`max-narrow:grid-cols-1`). A cell never states its own width: the grid
gives it one, so a tile is as wide as the tile beside it whatever each holds. A band whose cells
need unequal widths is two bands.

### The four ways to divide, and when

| Division | Draws | Use it for | Never for |
|---|---|---|---|
| **Space** | nothing | parts of one thing, things in one group | — this is the default; reach for an edge only when space is not enough |
| **Rule** (`Separator`, a row's `border-t`) | one hairline in `--color-edge` | the boundary between two **groups** that share a column, and between **records** in a list | around a single thing; under a heading; between cells on a band |
| **Fill** (`bg-fill`) | a `bkgd-200` ground, no edge | a **plot** and a **chip**: something a member reads *into* rather than *across* — a chart's pane, a badge, a field | grouping rows; a card that also has a border |
| **Frame** (`Card`) | edge **and** ground, `rounded-card` | a **self-contained unit a member acts on**: a record with its own title, prose and act; a tile in a grid of tiles | wrapping a whole band; a single stat; nesting inside another frame's padding |

Two of these on one band means the band has not decided what it is. Copilot's widgets are all
frames because every widget is a unit a member taps; a band of measures is **all** frames or **no**
frames — `Stat` tiles beside a `Card` beside a filled `Chart` panel is three answers to one
question.

### Marks and figures

A provider or a member is a **circle**, one size, everywhere it appears; several are a **stack** that
overlaps by `2xs`. A figure is the largest type on its tile (`text-figure`) and its delta is a step
below the chrome (`text-small`) with a direction glyph beside the sign, so direction reaches a
reader who cannot see the colour. A graphic with more than one colour carries a `Legend`.

What reads it: the six-step and four-division rules are `gates.py`'s (a composition gap outside the
six, or a `border` on a `Stat`, is a text-level failure); the grid and the mark rules are a reviewer's.

## Rules the surface holds to

Ten rules, each closing a class of defect rather than an instance. The first five are the ones a
change pays for on every screen, so breaking one of them costs the most. Each rule names the
failure it prevents and who catches a breach: `gates.py` where a check reads it off the tree, `tsc`
or a test where a type can carry it, a reviewer where nothing else can.

### A route is one row

Pattern, kind, builder, view and title segment sit in one table row, and `parseHash`, the builders,
the dispatch in `src/App.tsx` and `pageTitle` all derive from that row. Dispatch is a `switch` with
a `never` arm, not a chain that ends in a fallback, and the kit publishes the builders so an app
page never spells a hash itself. That is what stops three unlinked edits from disagreeing: a route
that parses with no builder any caller but a test can reach (`conversation-slot`), a singleton
matched by whole-string equality so `#/first-run?x=1` opens the home composer instead, `"#/first-run"`
written as a literal in both `src/lib/route.ts` and `src/App.tsx`, and an unknown address becoming
the composer in silence.

`gates.py` holds the spelling half — a `#/` literal or a hash pattern outside the route module is a
text-level failure it reads, the way it already compares SSE event names across the two languages.
Exhaustive dispatch is `tsc`'s once the chain is a `switch`. Whether an edit is a row or a fourth
reader of the address is a reviewer's call.

### The router is the only writer of the address

`go` is the one act that moves the page: nothing else assigns `location.hash` or calls
`history.replaceState`, and the bridge's `navigate` message lands through that same writer, against
an allowlist that is a column on the route table rather than a second list of kinds. Nothing
rewrites the address or writes `sessionStorage` during render — `arrive` belongs in an effect,
never in a `useState` initializer. A second writer appears to work only because the `hashchange`
listener re-parses, so it skips the drawer close and lands the route a task late, and a
render-phase write is why the app cannot mount under `StrictMode`.

`gates.py` can hold this one outright: `location.hash =`, `history.pushState` and
`history.replaceState` outside the router module are text-level failures, and its walk covers the
app pages as well as the portal source. A reviewer still has to catch a write that hides inside a
render path.

### An open panel has one encoding

An open panel is a lane on the track (`?open=a~b~c`), and a lane id is minted and parsed by its
kind's codec, exported on the kit so both sides of the bridge use the one codec. A panel gets no
second spelling — not a `?slot=` parameter with its own inline pattern, not a `/slots/<slot>` path
segment — and a channel that can mean two things carries a typed kind instead of a membership test,
which is what `place.opens[0]` needs while it stands for either the pane's own lane or the framed
page's open target. Without the rule, lane ids from eight private namespaces share one space, a
bare word like `github-coverage` sits beside artifact ids and conversation uuids, and a
conversation's panel stays outside the track — not remembered per screen, not reorderable, not
holdable beside a second panel.

`gates.py` can refuse the narrow half: a lane-id pattern or an `open`/`slot` query key spelled
outside the codec module. Whether a new panel joins the track or invents a third encoding is a
reviewer's read.

### The crumb and the tab title come from one trail

`where` in `src/lib/title.ts` is the only exhaustive switch over the route, and it builds the trail
the tab title uses; the crumb reads that same trail, so "where am I" is derived once and the two
answers cannot disagree. A crumb names an address. Closing a lane is a lane verb and does not
travel as a parent callback, and a framed page draws the crumb the shell derives inside its own
band rather than the shell adding a second band above it. A `parent` prop that carries three
meanings across its producers — go to the agent screen, close a lane, plain text — is a prop a
container can answer by passing none, which is how a tab or a section shows no trail at all and a
section's title falls back to the agent's name.

A type carries most of it: make the crumb an address instead of a callback and `tsc` refuses the
other two meanings. One band per page, and a trail on every screen, a reviewer must check.

### A listing pages one way, and its state is in the address

One page envelope on the backend, one `Pager` in front of it, sort, direction, search and cursor in
the place rather than in React state, and a listing that cannot page says so instead of truncating.
Two protocols and two pagers are why Sources reads unlike every other table and why the tasks index
cuts off at fifty rows per kind per app with no notice. Listing state in component state is what
loses a member's page on reload, on Back and on a shared link — and for a framed listing the place
is the only channel the bridge carries, so a page held in React state cannot survive a reload at
all. A new read is a row on the endpoint table, never a new verb.

`gates.py` reads the backend half: it already walks the `SurfaceRoute` rows, so a route that
answers with a list and no cursor, or a second page shape, is a failure it can name. Whether a
screen's sort and search reached the address is a reviewer's read.

### The place is one record, carried whole

The place is one record type and the merge takes it whole — no per-key list that can fall behind
the key set — and the bridge's `init` payload carries the same record instead of a single id. Every
view takes `place` and `onPlace` from the registry. The gap this closes is invisible to `tsc`
because every key is optional: `PLACE_KEYS` declares seven keys and the merge in
`src/kernel/place.ts` lists seven and omits `range`, so any search, filter, page step or lane open
on the usage tab erases the range from the address — which is why that one screen re-implements
fragment parsing and writes the address itself.

Make the merge total and `tsc` holds it. Until then `gates.py` can compare the two key lists the
way it compares event names, and a reviewer counts keys.

### Content drawn in two containers has one renderer

The pop-out and the centre pane are one mechanism, so content that appears in both is one component
with an embedded flag — `ConversationSlotPane` is the shape — and every secondary panel uses the
exported `Sheet` while its content renderer stays shared. A second implementation is how
copy drifts where nothing catches it: one record lane says "reload the listing" where its twin says
"reload the page", the same act reads "Unshare" in one view and "Make private" in another, a member
row says "Disabled" against the member object's own "unseated", a theme preference held twice goes
visibly stale at phone width because both copies mount, and three hand-rolled flyouts duplicate the
Radix `DropdownMenu` the file already imports, keyboard path and all.

No gate reads "these two components draw the same thing", so this one is a reviewer's, and it is
the first question to ask of a new component: which existing one does it fork?

### A refresh never replaces a good answer

`usePanelRead` keeps the last good payload when a re-read fails and marks it stale. A skeleton
draws only while a screen has no answer yet, a page step is not a new screen, there is one shimmer
idiom, and no `Loading…` line goes above rows that are already drawn. A failed poll that lands as a
failure state blanks a filled table or the app status dots until the next tick thirty seconds
later, which is the worst thing the surface can show a member; and the remount on place destroys
the ref that would hold rows across a cursor step, so Older and Newer flash a skeleton on every
listing.

A test holds the hook, and a test that asserts the blank is a defect to delete rather than a
contract. `gates.py` can hold the idiom count: a `Loading…` literal outside the one waiting
component is a text-level failure across the portal source and the app pages alike.

### A fallback reserves the answer's shape

A placeholder occupies the space the answer will take, and a control that will be taken away never
draws as usable. `Building` in `src/views/AgentPane.tsx` is the model — it draws the page's shape
while the homepage read is in flight. Three hard-coded starter rows against a real answer of two or
three, on a centred column, move the composer out from under the member's cursor; a list keyed by
title remounts instead of swapping text; a screen that leaves its frame flag false during the read
draws a full-width composer and then replaces it with the frame. Every read clears its body and its
error when its key changes, or one artifact's text persists under the next and a page of earlier
messages grafts onto another conversation.

Whether a placeholder matches the answer's shape is a reviewer's judgement. The resets are a test
each, and they belong in the suite beside the read they guard.

### Nothing crosses the wire unchecked

A read payload type is generated from the backend, never hand-written behind a bare cast on
`res.json()`. A mutation crosses as a discriminated union on its verb, never as an `unknown`
envelope handed to `postIntent` from twenty-six call sites. Every `JSON.parse` in an SSE handler is
guarded like the sibling `fetch` paths already are, a payload URL reaches an `href` through one
`safeHref`, and a bridge reply is checked on both hops, because the frame's swapped `fetch` and
stand-in `EventSource` run the same modules inside a cross-origin page. Hand-written mirrors of
Python dicts, and a warning comment where a type belongs, are a wire mismatch that fails in a
member's browser instead of in CI.

`gates.py` owns this one: generated fixtures plus an equality check already hold the SSE event
names identical across the two languages, and the same shape extends to the JSON reads and to the
bridge's reply. One hole is left for a reviewer to remember — `.pre-commit-config.yaml` has no
frontend hook, so `make check` says nothing about a TypeScript change.
