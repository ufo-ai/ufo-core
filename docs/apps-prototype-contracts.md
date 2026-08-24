# Apps prototype — locked seam contracts

Implementation coordination for RFC 0039, prototype phase. Simplest-thing-first, security
deferred (see RFC 0039 "Deferred for the prototype — security debt"). These seams are FIXED so
parallel workstreams build compatible edges. Do not renegotiate a contract in a workstream; if one
is wrong, stop and flag it.

## The model (settled)

- **An app is a shipped agent** — an `AgentProvision` on the `agents` manifest point (see
  `extensions/app_chat/ufo_ext_app_chat/manifest.py`). It appears in the Applications flyout and
  gets an editable homepage through existing infra (RFC 0030 provisioning, `AgentPane`, the
  `deploy_website` edit loop). User-defined apps and built-in apps are the same thing.
- **An app's homepage is a static site built from the page source the extension ships** — `app.tsx`
  in the app's home skill, built by the portal's own vite build into the deploy-wide apps tree and
  served row-less to every workspace that has not forked it (§The built app pages). Editing is the
  existing chat-column loop: the agent edits the page in its sandbox and runs `deploy_website` +
  `set_homepage`, which founds a workspace site the standing loop owns.
- **Live data + navigation reach the page through the bridge** (below), because the framed page
  is cross-origin from the portal and cannot call the portal API directly.

## Contract 1 — the bridge (postMessage)

Parent = the portal shell (the window hosting `AgentPane`'s iframe). Child = the framed homepage.
All messages are JSON objects carrying a `ufo` discriminator. The shell validates `event.source`
is the app iframe's contentWindow and ignores everything else. Prototype trust: the shell acts on
the frame's requests under the viewer's own session with NO per-message gate (debt #1, #2).

**Child → parent:**

| `ufo` | Fields | Shell behavior |
|---|---|---|
| `ready` | — | shell replies with an `init` message |
| `read` | `id` (string), `path` (string) | shell fetches `GET /surface/web/<path>` if `path` is in the read allowlist, replies `data`; else replies `data` with `ok:false` |
| `write` | `id`, `kind`, `name` (nullable), `spec` (object) | shell POSTs the direct-write endpoint (Contract 3), replies `data` with the typed result or refusal |
| `navigate` | `to` (string hash) | shell validates `to` parses to a known route (`parseHash`) and is same-app or a conversation permalink, then `go(to)`; else ignored |
| `resize` | `height` (number) | shell may set the iframe height (optional, cosmetic) |

**Parent → child:**

| `ufo` | Fields | Meaning |
|---|---|---|
| `init` | `member` ({email, admin}), `agents` (optional), `agentId` | sent after `ready`; the page's boot payload |
| `data` | `id` (echoes the request), `ok` (bool), `body` (any) or `error` (string) | reply to a `read`/`write` |

**Read allowlist** (the existing portal GET reads the shell forwards; extend only by adding here):
`api/chats`, `api/agents`, `api/agents/status`, `workspace/radar`, `workspace/artifacts`,
`workspace/memory`, `workspace/team`, `objects/{kind}`, `objects/{kind}/{name}`,
`agents/{id}/conversations`. A `path` is allowed if it equals one of these or matches a
`{...}`-templated one after segment substitution. No query-string restriction in the prototype.

**Navigation targets** the shell accepts from `navigate.to`:
`#/c/<uuid>` (open a conversation), `#/new/<agentId>` (new conversation), `#/agents/<agentId>`
(open an app), `#/<section>` (a built-in section). Anything else is ignored (debt #5).

**The bridge client is part of the kit** — `extensions/web/frontend/src/apps/runtime.ts`, exported
through `ufo/kit`. `connect()` performs the ready/init handshake; `installShims()` reroutes the
page's `fetch` and its `EventSource` for `/surface/web/*` paths through the shell's `call` verb, so
`lib/api`, `lib/turnStream`, and every kernel read run inside the page verbatim and the page never
speaks the protocol itself. `mountApp` does both before it renders. Pages never hand-roll
postMessage.

## Contract 2 — homepage generation (edits show)

`hosted_site` gains an integer `generation`, default 0, bumped by `HostedSites.register` on every
(re)deploy of an existing `(workspace, conversation, name)`. The web `GET agents/{id}/homepage`
read adds `generation` to its `set` state: `{state:"set", url, generation}`. The frontend keys the
homepage iframe on `url + ":" + generation`, so a redeploy remounts the frame within one poll.
Field name is exactly `generation` everywhere.

## Contract 3 — direct object writes + journal

**Core** (`core/src/ufo/objects.py`): expose an async function the web surface calls to apply or
delete an object under a member's authority WITHOUT a turn — the object verb path constructs a
tool context whose speaker/acting-member is the passed member id. Signature (lock the names):

```python
async def write_object(registry, *, member_id: UUID, agent_id: UUID, kind: str,
                       name: str | None, spec: dict | None, delete: bool) -> ObjectWriteResult
```

`ObjectWriteResult` = `{ok: bool, name: str | None, detail: str}` (a refusal sets `ok=False`,
`detail` the reason). Reuse the existing kind handlers and their guards; the only new thing is
building the context at the surface instead of in the engine.

**Journal** — one core table:

```
object_change: workspace_id, id (uuid), kind, name, verb ('create'|'update'|'delete'),
               caller (text: 'member:<uuid>' | 'turn:<uuid>' | 'job:<name>'),
               spec_before_ref (text, nullable), spec_after_ref (text, nullable), created_at
```

Spec bodies go to the workspace blob store; the row holds only refs. Write one row inside the same
transaction as every object create/update/delete, from the object verb path, so chat-turn writes,
direct writes, and job writes all journal identically. Prototype: the admin audit read and the
component sync tail are NOT built yet — the table + the write is the deliverable, verified by a
test that a create/update/delete each lands a row with the right verb and caller.

**Web endpoints** (`extensions/web/ufo_ext_web/surface.py`): `POST objects/{kind}` (body
`{name?, spec, agent?}`) → `write_object(delete=False)`; `DELETE objects/{kind}/{name}` (query
`agent?`) → `write_object(delete=True)`. Member from the session, agent from the query or the
member's main. Return `ObjectWriteResult` as JSON. These are the bridge `write` verb's target.

## Contract 4 — the app-extension pattern

Each app is a package `extensions/app_<name>/ufo_ext_app_<name>/` mirroring
`extensions/app_chat`: `__init__.py` (empty), `manifest.py` returning
`Manifest(name="app_<name>", version, agents=(AgentProvision(...),), skills=(...))`, and
`tests/test_ext_app_<name>.py`.

- The agent: `visibility="workspace"`, `model="auto"`, a prompt describing the page it maintains
  and that it edits+redeploys the page when asked.
- The homepage skill: a skill folder `skills/app-<name>-home/` with `SKILL.md` (the edit-and-deploy
  workflow) and `app.tsx` — the page as TSX, importing `ufo/kit` and nothing else. The build turns
  it into the app's static site (§The built app pages); the agent edits that same file to change
  the page.

Apps and their pages (prototype):

| App | Page reads | Page acts |
|---|---|---|
| app_chat | `api/chats`, the transcript read, the turn stream | one conversation whole — composer, streamed replies, starters; click → `navigate #/c/<id>` |
| app_radar | `workspace/radar` | list digest entries; click → `navigate #/c/<id>`; "Rebuild" → the intents lane's fenced rebuild verb |
| app_tasks | `objects/scheduled_task`, `objects/source_trigger` | list; pause/resume → `write scheduled_task` |
| app_artifacts | `workspace/artifacts`, `objects/site` | list files+sites; click → open link |
| app_wiki | `workspace/memory`, `objects/member` | list memory + roster; click a member → navigate |

`app_chat` ships the agent, its homepage skill, and the prompt that maintains the page.

## Ownership (parallel workstreams — file-disjoint)

| Workstream | Owns (only) | Implements |
|---|---|---|
| WS-Writes | `core/src/ufo/objects.py`, a core migration, `extensions/web/ufo_ext_web/surface.py` | Contract 3 (journal + `write_object` + endpoints) AND Contract 2's `generation` field in the homepage read |
| WS-Sites | `extensions/sites/ufo_ext_sites/` | Contract 2's `hosted_site.generation` column + register bump + migration |
| WS-Frontend | `extensions/web/frontend/` | Contract 1 (the bridge module, the kit's transport, and the AgentPane wiring) + Contract 2's iframe remount + flyout showing apps + vitest |
| WS-Apps | `extensions/app_*/` (not pyproject) | Contract 4 (4 new app extensions + homepage skills + the app_chat page) |

The orchestrator owns `pyproject.toml` (entry points + wheel packages for the 4 new extensions),
`uv sync`, cross-workstream reconciliation, and end-to-end verification. Each workstream commits
its slice to its own worktree branch and reports the branch and SHA. Security is deferred and
documented; do not add gates the contracts don't name.

## As-built (reconciled) — where the build diverged from the contract

The four workstreams landed and were cherry-picked onto the branch, then reconciled. Divergences
that the contracts above do NOT reflect (the code is authoritative):

| Contract | As built | Why |
|---|---|---|
| C2 field `generation` | **`deploy_generation`** everywhere (site field, homepage payload key, frontend key) | `hosted_site.generation` already exists as a `Uuid` (grant invalidation); a distinct integer was needed |
| C3 `DELETE objects/{kind}/{name}` | **`POST objects/{kind}/{name}/delete`** | `SurfaceRoute.method` is `Literal["GET","POST"]` — no DELETE verb exists. The bridge only applies, never deletes, so no frontend impact |
| C3 turnless `write_object(...)` | The endpoint **admits a prepared-intent turn** (the proven `submit_intent` path); the real engine builds the context and applies the kind's real guards. External endpoint shape unchanged | A faithful turnless `ToolContext` is heavy/fragile (doctrine review finding #2). A direct write **is** a turn; a faithful turnless write is deferred |
| C3 journal `spec_before_ref`/`spec_after_ref` (blob) | Specs stored **inline** (`spec_before`/`spec_after` TEXT) | Blob access from the core object layer isn't readily available; inline is simplest for the prototype. Row's external shape (verb/caller/kind/name) unaffected. Blob promotion deferred |
| C3 journal ordering | **Journal-first**: the row is inserted ahead of the mutation under a deterministic id (`uuid5` of the dispatch's idempotency key) and withdrawn if the store refuses — so a reported failure is always a write that did not happen, and a crash-recovery re-run journals nothing twice. Rows carry non-null `name` and `agent_id` (the target agent on cross-agent writes) | one `workspace_tx()` cannot wrap the kind store's own transaction (`db.py` opens a fresh connection per tx; sqlite is single-writer), so atomicity is had by ordering + idempotency instead |
| C2 `deploy_generation` counter | A **clock stamp** (µs epoch, BigInteger), strictly above the row's prior value on update | a counter dies with its row, so delete-and-recreate under the same name (same URL) repeated old values and the iframe key could collide |
| C3 "reader deferred" | The **admin audit read** (`GET workspace/object-changes`) shipped in A1. It states verb, kind, name, caller, agent, and moment — never the stored specs: a kind's own read redacts what its owner withheld, and the audit must not answer what the record refuses | The repo gate rejects a column with no read site (the both-ends rule) — so the reader had to land now. Better than planned |
| C1 `write` verb | Requires a non-empty `name` in the body (no nameless create) | endpoint validation; fine for the prototype's update-by-name uses |
| C4 radar "Rebuild → write/intent" | the page's rebuild control posts **the intents lane's fenced verb** (`RebuildDialog`, `verb="rebuild_reports"`), never an object write | a rebuild is a tool act, so it rides the lane that carries tool acts; the fence admits only the verbs the shipped pages carry as controls |
| C4 skill assets under `assets/` | the page at the **skill folder root** (`.skills/app-<slug>-home/app.tsx`) | the loader treats every non-`SKILL.md` file as an asset, and a flat path is the one both the build and the editing agent name |
| C1 `ready` once | client **retries `ready`** (≤30×, 100ms) until `init`, then stops | the shell attaches its listener after mount; a single `ready` races and is lost |
| local carrier `dial` raised | returns `DialTarget("127.0.0.1:<port>", tls=False)` (`core/src/ufo/sandbox/local.py:267`) | local sandboxes are host subprocesses sharing the host network, so an in-sandbox port is a loopback port; cost: one port namespace for every conversation — the newest deploy's server owns a contended port |
| ingress base https-only | `http://localhost[:port]` admitted, that host alone (`core/src/ufo/config.py:224`); `ufoctl init` writes `ingress_public_url = "http://localhost:8100"` (`core/src/ufo/cli.py:88`) | a zero-services dev run serves sites with no certificate; every other host still requires https |
| ingress cookie always `Secure` | `Secure` follows the configured base scheme — `IngressServe.cookie_secure` (`core/src/ufo/ingress_serve.py:207,740`), `set_session_cookie(secure=...)` (`core/src/ufo/sdk/http.py:29`) | browsers refuse to store a `Secure` cookie set by an http `*.localhost` origin, so every local site visit 403'd "needs a fresh link" |

| C1 protocol verbs | **One generic `call`** (method + path + optional body + optional `x-ufo-*` headers, every other header dropped) over an endpoint table (GET reads incl. transcripts, starters, and conversation slots; POST object writes, deletes, the chat admit, the intents lane), plus a `credentials` row for the composer's credential form, an intent-verb fence (a frame posts only the rebuild verbs the shipped pages carry as controls), and stream-marked rows relayed as `opened` then `frame`/`end` messages (`turns/{id}/stream`, SSE parsed shell-side, `close` aborts, cap 8). A `data` reply relays the surface's answer verbatim: `ok`, `status`, the body text, and the refusal/session-fault headers. The built kit's transport shims ride it. `init` carries the portal's loaded agents and the pane's whole place | the protocol never names a capability — adding one is adding an endpoint row (Alex: "this is a generic system"); verbatim relay is what lets the portal's own `lib/api` and `EventSource` consumers run inside a page unchanged |
| C1 frame identity | the app page is the pane iframe's direct document after the sites surface gates and redirects it to ingress: the client posts to `window.top`, and the shell accepts only its pane's `contentWindow` | source identity binds every bridge call to the one page the pane mounted |
| deploy port 8000 fixed | **per-conversation ports** — `serve_port(conversation_id)` in 20000-39999 | on the local carrier all sandboxes share the host port namespace; one fixed port meant every deploy killed the previous server and every dial reached whoever deployed last |
| C4 hand-written `index.html` pages | **the pages are the portal's own React views** (Alex: "reuse the original react code as much as possible" while apps stay self-editable extensions). Each extension commits one file, `app.tsx`, whose whole dependency surface is the name `ufo/kit` — `src/apps/kit.ts`, the frontend's `ufo.sdk`: React and its hooks, the JSX runtime, the portal's components and kernel reads, the section host (`SectionApp`, `mountApp`), and the bridge transport (`src/apps/runtime.ts`). An agent-directed rewrite recomposes kit exports in `app.tsx`. The chat app mounts `ChatPane` (one conversation, or the start screen with starters); section names stay in the address codec (`SECTIONS`), and a section address the shell does not host itself lands on the app that ships it. A mark sprite is a file of the page's own tree, fetched off `/assets/` natively | a transcribed page re-breaks on every portal chrome change (lanes proved it); composing the portal's own views means every app page renders the current chrome, and the editable surface stays in the extension |
| app-page chat attachments | a `FormData` body decomposes into `form` parts — strings and Files, which structured-clone whole — and the shell reassembles the multipart for the chat admit; any other non-string body is refused page-side | `postMessage` cannot carry `FormData` itself, only its entries |
| pane band over a framed homepage | the pane draws **no band** — the page heads itself, and the shell's two acts (settings, the chat-lane toggle) float top right just under the band line, and the right-side chat is one editing conversation per app — the newest of the member's own directive conversations, or the composer when none exists | the page is the portal's own screen and draws the band the section drew; a pane band above it stated the name twice and pushed the page a row below the reference |
| in-frame connect links | a reply's `/turns/{id}/connect` link resolves against the site origin and dies | it is an href, not a fetch, so the tunnel never sees it; acceptable for the prototype |

Coupling to revisit for the real version: jobs that write objects outside `ObjectVerbs` do not
journal (the verb path is the hook point). An app page's source is blob-backed (RFC 0039 B1,
built): `deploy_website` promotes the served directory into the workspace blob store and
`hosted_site.source_manifest` names the files, the ingress serves those bytes with no sandbox dial,
the site kind's `object_get` materializes the deployed source into the reading
conversation's sandbox (its status names the `source_path`), and a homepage
redeploy from another conversation updates the bound row in place — same origin, same link, no
fork. `publish_website` backends stay sandbox-served. All flagged, none blocks the prototype.

## The built app pages

Row-less, auto-updating app homepages: one vite build turns the five extensions' page sources into
one static tree, published under `apps/<digest>/` in the fleet store and served to every workspace
that has not forked its copy.

**The build.** `extensions/web/frontend/vite.apps.config.ts` — root `frontend/apps`, base `/`,
outDir `extensions/web/ufo_ext_web/apps`. Five html entries, one per app, each naming that app's
`app.tsx` in its extension's home skill. `ufo/kit` and `ufo/kit/jsx-runtime` resolve to
`src/apps/kit.ts` and `jsxImportSource` is `ufo/kit`, so a page's whole dependency surface is that
one name and the React its JSX lands on is the React inside the kit. The five pages share hashed
chunks: 1.1 MB raw / 365 KB gzip of first paint held once between them, and 4-16 KB of entry chunk
each. `vite.sdk.config.ts` emits the same kit as one portable ES module — `kit.js` + `kit.css` —
into the sites extension's own package, where a single-page project can resolve it and the pages'
split chunks would not.

**The tree:**

| Path | Contents |
|---|---|
| `<slug>/index.html` | the built page: a module script and a stylesheet under `/assets/` |
| `assets/**` | the hashed chunks, the stylesheet, the fonts, the mark sprites |

The slugs are the tree's top-level directories other than `assets`. Nothing else rides it: the tree
is what a browser fetches, so its digest is a serving generation.

**Serving.** The digest is `sha256` over the sorted `(path, bytes)` of the whole tree, first 16 hex:
content derived, so it is identical on every pod, names the fleet prefix `apps/<digest>/`, and is
the etag every file of the tree answers with. The frame link the homepage read hands out carries
slug + digest, and the ingress serves the tree at the frame origin's root — a file inside the
claim's slug directory answers a root-relative request (`<slug>/index.html` is `/`), a file outside
it answers at its own path (`assets/x` is `/assets/x`) — so one copy of the chunks and the fonts
serves all five pages.

**A member's own page.** On its first change, the app's agent copies `app.tsx` and `index.html` from
its home skill. On later changes, `object_get` materialises the last deployed source under the
site's `src/` directory. The agent edits that source and passes it to `deploy_website`; the tool
writes the current `vite.config.ts` and `sdk/` beside it, runs Vite, hosts the output, and carries
the project's source in that output for the next read. The build needs no npm install or registry
egress, and a page edited a year ago still builds against today's components.

Where the build diverged from the intended design (the code is authoritative):

| Design | As built | Why |
|---|---|---|
| the extension mints the shipped ingress token | the **producer lives in core**: `SurfaceContext.ingress_url` gains `shipped_slug`/`shipped_digest` | the SDK gate (`gates.py`) forbids an extension importing `ufo.sandbox.ingress_token`, so the token can only be minted through a core surface. `WorkspaceAgent.provisioned_by` identifies the app extension |
| shipped url resolves direct-to-ingress | the homepage read hands a **portal embed link** (`shipped_homepage_url` → a `SURFACE_SITES` token carrying `{ws, agent, slug, digest, portal_embed}`); the sites surface recognizes it, gates on the app agent's visibility, and redirects an iframe request to a minted shipped-claim ingress URL | a stable sites link re-mints a fresh 900s view token per visit (a baked view URL would 403 on a reload past its TTL) and re-gates every visit; Fetch Metadata takes the direct path, the wrapper self-redirects when a framed client omits it, and ingress `frame-ancestors` admits only the portal and the workspace's app origins |
| a `current` pointer file names the live tree | **no pointer** — the digest travels in the frame token, and the fleet keys are the whole record | a pointer with no reader is a declared surface with one end; the digest a page's link carries is what says which tree serves it |
| fork = anchor-continuity upsert; reset = delete row | `deploy_website` builds and persists the page's source, `set_homepage` binds it, and `object_get` materialises that source for another edit; object-`delete` unhosts the row back to the built page | the standing tools own the fork and reset; a first fork uses its deploying conversation's origin |
| the five manifests' first-turn prompt lines go | **gone**, and each prompt now names the home skill for a change a member asks for; seeding is off mechanically too — `seed_homepages` marks an app agent `shipped` (settled, no turn), so the candidate query settles | a shipped page needs no build turn, so a prompt directing one describes work that does not exist |
