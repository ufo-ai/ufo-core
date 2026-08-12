---
rfc: 0023
title: "Portal architecture — a view kernel, declared views, and one design system"
status: proposed
date: 2026-07-30
---

# Portal architecture — a view kernel, declared views, and one design system

> The portal's every view is written by hand into one 2,722-line page against one 2,418-line test
> harness, so ten parallel units spent more effort merging than building and each new view
> re-implements the same fetch-fence-render-empty-error-notice shape. Split it: a small kernel
> owning navigation, the request fence, and a fixed component set styled from tokens; views that
> are listings become declarations the kernel renders; views whose value is layout stay
> hand-written but register the same way; and an extension contributes a view by declaring one,
> never by editing the portal. Third-party UI code never runs in the portal's origin — it gets an
> isolated frame, the mechanism RFC 0020 already built for hosted sites.

## Current state, measured

Wave 2 of #624 landed twelve mutating and reading views across ten parallel units in two days.
What that cost, counted on `main`:

| | |
|---|---|
| `static/portal.html` | 2,722 lines — 181 CSS, one `<script>`, **81 functions** |
| `tests/portal_smoke.mjs` | 2,418 lines — one stub DOM, one sequential walk |
| `ufo_ext_web/surface.py` | 1,243 lines, 25 routes |
| churn in two days | 21 commits to `portal.html`, **+2,488 / −354** |
| the fence, hand-threaded | `load !== panelLoad` written out **19 times** |

Every unit edited the same three files. The merge cost was not incidental — it was the dominant
cost, and it produced defects of its own:

- **Selector collisions.** `#panel form` was written for the memory search bar and silently
  restyled the settings form into one flex row, hiding the save outcome; the fix scoped it to
  `form.search`, and when memory moved to the workspace view the rule moved again.
- **Slot collisions.** Three units independently took `showWorkspace`'s third argument for their
  own meaning; reconciling them produced a `placement` object no unit designed.
- **Harness collisions.** Units kept adding stub-DOM methods (`createTHead`, `insertRow`,
  `addEventListener`, `TextDecoder`, a focus recorder, a body-reader arm) and collided on binding
  names (`previewed` vs `filePreview`).
- **Repetition as a defect surface.** Each view hand-writes fetch → fence → error arm → empty arm
  → rows → actions → outcome. One unit forgot the fence in one arm; one left a viewer on
  `Loading…` forever because its `try` enclosed only the initial fetch; one asserted a byte bound
  it cut in characters.
- **Copy drift.** Register (sentence case, ending punctuation, no metaphors) was enforced by review
  rounds, repeatedly, per string.

None of that is a people problem — it is the architecture asking for the same code twelve times and
providing one file to write it in.

Constraints that stay, because they are load-bearing:

- **No external origin at runtime.** `test_portal_page_is_self_contained` forbids any literal
  external URL: no CDN, no remote font, no third-party host — everything the page loads comes from
  the surface's own path. This is the CSP and offline-serving property, and it survives every
  proposal below. It is *not* the same as "no build step": the repo already builds a
  frontend in CI — the debugger's React 19 + TypeScript app, `npm ci` then `npm run build`
  (`ci.yaml:35-36`), deliberately before the wheel gate. (The edge module is installed and tested
  there, not built; its sources are hand-written.) A bundler emitting local assets
  satisfies the origin rule exactly as a hand-written file does.
- **Mutations are audited turns** through the prepared-intent lane; reads are projections. RFC 0022
  proposes the declaration that removes the surface's duplicate copy of the callable set — this RFC
  assumes it and completes the other half (how the control is *rendered*).
- **Behaviour is proven by mutation-checked tests**, never by asserting rendered markup in
  pytest. *How* it is proven is not load-bearing and this RFC changes it. Today's harness is a
  2,418-line file whose hand-rolled substrate — `StubElement` and the global shims — exists
  precisely because the portal has no test toolchain. That substrate grows a
  method every time a view touches a new browser API (`createTHead`, `insertRow`,
  `addEventListener`, `TextDecoder`, a focus recorder, a body reader), and those additions were
  themselves a merge-conflict source this wave. The rest of the file is fixtures and the walk
  itself, which a toolchain does not delete — what vitest removes is the substrate, not the suite. It also reads page source in three
  places — the walk extracts the one inline script (`portal_smoke.mjs:919`), the CSS-scoping legs
  grep rule text (`:1648`, `:1650`, `:1678`), and `test_portal_page_is_self_contained`
  (`test_ext_web.py:1633-1638`) asserts against the page whole — each a coupling to the single-file
  shape. That test asserts five things, not three: the doctype and the presence of `EventSource`
  alongside the three external-URL refusals. An origin gate replaces the URL half; the doctype and
  the live-stream assertion have to keep a home rather than vanish with the string they read.

## Proposal

Four separable pieces: a file layout that lets units work in parallel, a kernel, a declaration for
the repetitive view, and a token-based design system. Extension contribution falls out of the
declaration; third-party isolation falls out of RFC 0020.

### 1. Many files, and a real toolchain

**React + Tailwind + shadcn/ui (Radix underneath) in TypeScript is the standard for every web UI in
the system.** The portal adopts it here; the sites skill's webapp template already ships exactly
that (`tailwind.config.ts`, `components.json`, Radix primitives); the debugger already runs React 19
+ TS + Vite and converges on the theme and component set as it is next touched. One stack means one idiom, one theme, one test
toolchain, and a component learned in one surface transferring to the next — a per-surface stack
choice is what produced a hand-rolled 2,722-line page in the first place. Three things drive the
adoption, and only the first is about file count:

- **Types, and this is measured, not asserted.** `tsc` over the current portal script in *loose*
  mode (no `strict`, no `noImplicitAny`) reports **50 errors**, 48 of them in five codes: 22 `TS2339` (a
  `querySelector` result used as `HTMLInputElement`), 14 `TS2554` (wrong argument count — the
  API-drift class), **8 `TS2393`, duplicate function implementations**, 3 `TS2345`
  (`setAttribute(name, boolean)`), and 1 `TS2322`. Those
  eight are a live defect: a merge duplicated a 228-line block, the later copy wins by hoisting and
  calls `showWorkspace('credentials', undefined, 'Stored …')` with a bare string where the callee
  reads `place.notice`, so **setting a credential from the portal currently succeeds and says
  nothing**. The stub-DOM harness never saw it; `tsc` reports it as a compile error.
- **A test toolchain.** vitest + React Testing Library over jsdom gives real DOM semantics and
  deletes the hand-rolled stub. Measured under vitest + jsdom on node 26: the table API
  (`insertRow`, `createTHead`), `document.activeElement`, `TextDecoder`, `AbortController`,
  `FormData`, and `ReadableStream` all work; `matchMedia` is absent and unused. **`EventSource` is
  absent** in bare jsdom, under vitest, and from node itself — so the chat turn stream keeps one
  ~30-line fake with an event queue. That is the whole residue of the stub, and it is named here so
  it is budgeted rather than discovered. The harness split this RFC previously listed as its concentrated
  risk stops being a risk to manage and becomes a problem that dissolves: there is no stub to split.
- **Components and utilities instead of hand-built DOM.** React ends the manual
  `createElement`/`appendChild` construction where several of this wave's defects lived — a viewer
  that never tore down on navigation, a panel left on `Loading…`, an empty state that
  short-circuited a populated render. Tailwind ends bespoke CSS: a view composes utilities from one
  theme instead of authoring a selector, which is what makes the selector-collision class
  structurally impossible rather than review-caught.

Vite emits hashed assets served from the surface's own static path. **Not** `vite-plugin-singlefile`
— the debugger wants one file, the portal wants per-view code splitting, which is the point of the
split. The self-contained gate becomes an origin gate over built output: every `src`/`href` resolves
under the surface's own path, and each referenced asset is actually served.

```
extensions/web/
  frontend/                   source, never served
    index.html                skeleton + entry <script type="module">
    src/
      theme.css               the design system: one @theme block, nothing themed elsewhere
      main.tsx                boot, session, router mount
      kernel/                 panel (the fence), listing, table, form, pager
      components/ui/          shadcn/ui, vendored and themed — button, field, table, sheet
      views/
        registry.tsx          Record<WorkspaceTab, WorkspaceView> — one map, no second list
        Sources.tsx           a declaration plus a payload projection
        Chat.tsx              layout-bearing
        Conversations.tsx     layout-bearing (turn tree, file pane)
        Artifacts.tsx         declared, with a detail pane
        Admin.tsx             layout-bearing (workspace administration)
  ufo_ext_web/
    static/                   build output (`outDir: ../ufo_ext_web/static`), gitignored,
                              shipped via `artifacts`, and what `surface.py` serves
```

The served path stays inside the Python package, where the debugger's
`outDir: ../ufo_ext_debugger/static` also points. What shipped is `frontend/index.html` as the entry
and `surface.py:91-92` reading `static/index.html`, guarded — `read_text() if PORTAL_FILE.is_file()
else None`, raising at request time rather than at import, so a missing build names the npm command
instead of breaking extension import for every web test. (An earlier draft of this section reasoned
from an unguarded read of `static/portal.html` and concluded the entry must keep that name; both
halves were wrong about the code as built.) Only the source tree is new, and it sits outside the
package because it is never served.

Ten units editing ten files conflict where they genuinely overlap (the registry line) instead of
everywhere.

**The build must be real in every environment the portal runs in**, and this is unit 1's actual
risk. The debugger's wiring is the template — `outDir` into the extension's `static/`, the
directory gitignored, `pyproject.toml`'s `artifacts` naming the built file so hatchling ships it
despite the ignore, `npm ci && npm run build` in CI before the wheel gate, and the same before
`ufoctl bundle` in `deploy.yml` — and it is a template with four known gaps for the portal:

- **`test-shard` has no build and no pinned node** (`ci.yaml:55-84`). Node itself is present —
  `ubuntu-latest` ships it, which is why `test_portal_page_smoke_walks_every_view`
  (`test_ext_web.py:3597-3622`) shells out to it and the behavioural walk really does run in the
  shards today; `checks`' `setup-node` pins a version rather than supplying one. What is missing is
  the build. The debugger survives that because no Python test reads its built page; the portal
  does the opposite — `surface.py:89` reads the page at **import**, unguarded, so a missing build
  breaks extension import for every web test, not just the page. Either the shards gain a build
  step or `checks` uploads the assets as an artifact the shards download. The debugger's own
  tolerance (`if APP_FILE.is_file() else None`, raising at request time naming the npm command) is
  the pattern to copy for the read.
- **The walk leaves pytest.** It runs today *from* a pytest test; under vitest it becomes its own
  suite and needs its own CI job, or it silently stops running — the failure mode being a green
  board that proves nothing about the page.
- **`artifacts` is a single literal path**; hashed multi-file output needs a glob, and `static/`
  must become fully generated and ignored — which means `portal.html` moves to the frontend source
  tree, since `emptyOutDir` would otherwise delete a tracked file.
- **`gates.py:57-70` walks `extensions/` with `rglob("*.py")` excluding only `.venv`**, so it will
  descend into `node_modules`. Both existing JS trees contain zero `.py` files, so this is safe
  today by luck; a vitest tree is larger, and one vendored `.py` fails the gate. Exclude
  `node_modules` beside `.venv`.

### 2. The kernel

Small and owned centrally, because these are exactly the things that must not be re-implemented:

- **Boot and session** — the `api/agents` read, the token card branch, the signed-out state.
- **Routing** — the hash grammar (`#/agents/<id>/<tab>`, `#/workspace/<view>`, `#/<section>`,
  `#/admin`) parsed once, with the listing cursor and filter carried as query state so a reload
  lands where the member was. A section the sidebar reaches directly is one path segment
  (`#/scheduled`, `#/artifacts`) and carries the same query state a workspace tab does.
  A kind read across the audience and read in one agent's namespace share a label but not a hash:
  `#/scheduled` is every agent's, `#/agents/<id>/scheduled` is that one's.
- **The request fence** — one `load()` helper that owns the monotonic token, the abort of a
  superseded read, the error arm, and the empty arm. A view never writes `load !== panelLoad`
  again; the 19 hand-written copies become one.
- **The primitives, eight of them** — `Table` (rows and cells are its props, not primitives of
  their own, once a component takes columns and data), `Empty`, `ErrorLine`, `Notice`, `Pager` (over
  `ListingCursor`, already on `main` at `core/src/ufo/listings.py` and re-exported through
  `ufo.sdk.listings`), `Drawer` (the artifact viewer's pinned panel, generalized over shadcn's
  sheet), `FormFromSchema` (generalizing `specField`, `portal.html:1663-1692`, whose choices come from
  **two** sources it must keep — `options || prop.enum` at `:1670`, where `reasoning` selects from
  the schema's own enum and `model` selects from caller-supplied options because `AgentSpec.model`
  is a bare `str` and the deploy's model ids arrive in the payload (`panels.py:310`, consumed at
  `portal.html:2340-2341`); a form deriving choices from the schema alone renders `model` as a text
  input and drops the deploy-model constraint the form exists to express. What it adds over that
  code: `prop.description` as a hint, which is never read today, and a label better than the raw
  key (`:1667`)), and
  `Confirm`. The same eight everywhere this document lists them.
- **The view registry** — `register({id, placement, label, render})`, one line per view. A view is
  reachable because it registered, not because a `TABS` array elsewhere lists it.

### 3. Declared listings

Most portal views are the same view: fetch a projection, render rows with per-row actions, page,
show an empty state. That is a declaration, not code:

```tsx
export const SOURCES: ListingSpec<SourcesPayload, SourceRow> = {
  read: "/workspace/sources",
  rows,                                    // payload → one uniform row list
  rowKey: (row) => row.key,
  columns: [
    { field: "backend", label: "source" },
    { field: "streams", label: "streams" },
    { field: "access", label: "access" },
    { field: "next_sync", label: "next sync" },
  ],
  empty: "No sources are registered. Register one in chat …",
  actions: (row, { act, busy }) => …,      // controls that post one intent
};
```

`columns[].field` is `keyof Row`, so a typo is a compile error rather than a blank cell, and
`render` receives that field's exact value type. `actions` is a **render function**, not a list of
names: RFC 0022 was closed, so there is no `MemberAction` declaration to name and no
`submit_model`-derived form. The kernel still owns the mutation machinery uniformly — `act` posts
one prepared intent on the main agent's lane, raises `busy` so a control cannot post twice, hands
an applied outcome to the placement, and states a refusal in place.

Each adopter earned exactly one capability, which is a real matrix rather than speculative
generality: sites contributed `unavailable`, artifacts `paged` and `detail`, sources `rows` and
`actions`. Credentials, tasks, skills, connections, and usage follow the same shape. Memory does
not: its paging sits on the hand-written side. So the hand-written set is chat, the conversations
debugger, the artifact viewer, memory, and the administration view — their value *is* layout — and
each registers identically, so the kernel does not care which kind a view is.

**A view's label and renderer are one entry, because three lists made "one declaration, no kernel
edit" false.** Reachability was governed by `WORKSPACE_TABS`, `WORKSPACE_LABELS`, and a ternary
chain in `Workspace.tsx`. The label map and the chain are gone; `WORKSPACE_TABS` remains and is
still what orders the sidebar, but `Record<WorkspaceTab, WorkspaceView>` totality now makes a tab
without its view a **compile error** — the paired-edit trap that shipped a silently empty sidebar
twice during wave 2 cannot recur.

The payoff is in the predictable changes: **a new column** is one line; **a new listing view** is
one declaration and one registry entry; **copy** lives beside the declaration where a gate can
check it. An **action** is a render function on the declaration, not a name resolved elsewhere —
RFC 0022, which would have supplied that, is not being built.

### 4. The design system

One theme, one component set, no bespoke CSS — which is what makes selector collisions
structurally impossible rather than review-caught.

**Tokens are the Tailwind theme** (`tailwind.config.ts`) — colour, space, type scale, radius,
border, elevation, motion, extended once and named by role (`surface`, `edge`, `muted`, `accent`),
never by appearance. Light and dark are the theme's two modes; today's `Canvas`/`CanvasText` system
colours are the starting values (correct contrast, free), resolved *through* the theme so the
convergence with the `/login` redesign (#503) is a theme edit rather than a sweep through 181 lines
of rules. A utility class in a view that names a raw value instead of a theme token is the thing to
gate against — that is how a design system erodes under Tailwind.

**Components are shadcn/ui, vendored and themed.** shadcn is not a dependency but source copied
into the tree (`components.json` drives it), which suits this repo: the components are ours to edit,
they carry no version to chase, and the theme reaches them because they are plain Tailwind. Take
what the portal actually needs — table, dialog/sheet for the drawer, form field, select, checkbox,
badge, tooltip, dropdown — and no more; an unused vendored component is dead code, and `sites`'
template vendoring forty of them is not a precedent to copy wholesale. Radix underneath is what
makes focus trapping, dismissal, and keyboard semantics correct rather than approximated.

On top of that sits the portal's own composition layer — page shell, sidebar, tab strip, the kernel
primitives (Table, Empty, ErrorLine, Notice, Pager, Drawer, FormFromSchema, Confirm) — each built
*from* shadcn components, never beside them. A view composes those and theme utilities; **a view may
not author a stylesheet or a selector**. That rule kills the `#panel form` class of defect: a view
cannot restyle a sibling because it never names one.

**Copy** — strings live in the declaration, and the register rule (sentence case, ending
punctuation, no UFO metaphors, no lowercase-as-aesthetic) becomes a gate over declaration strings
instead of a reviewer reading every PR. Enforce, don't document.

**Density and responsiveness** are properties of the components, decided once: the drawer becomes a
full-width sheet under a threshold, tables scroll rather than squeeze, the sidebar collapses. No
view re-decides.

### 5. Extensions contribute views

Today the portal's tabs are a literal array inside `portal.html`, so an extension cannot add a view
without editing the web extension — which a third party cannot do at all. With the registry and the
declaration, the manifest gains one point:

```python
Manifest(
    name="sites",
    portal_views=(PortalView(id="sites", placement="workspace", label="Sites",
                             read="…", columns=…, empty=…),),
)
```

No `actions` field: RFC 0022 was closed, so nothing can consume one, and a field with no consumer
does not ship. Core validates declarations at boot in the registry that already validates kinds and
tools (unknown field → fail loud, duplicate id → fail loud, unknown placement → fail loud). The
portal fetches the deploy's view declarations and renders them. **An extension ships a portal view
without a line of portal code**, which is the property the long-term third-party story needs and the
short-term first-party one already wants — sites and memory both had to be special-cased into the
workspace sidebar by hand.

Reads stay the extension's own surface routes or a core projection; the declaration names the route,
and the audience gate stays where it already is (the kind's or the surface's), never in the view.

### 6. Third-party code, when declarations are not enough

A declaration cannot express every UI, and a third-party extension will eventually want its own.
The line to hold: **foreign code never runs in the portal's origin**, where the member's session
cookie lives. Two tiers:

1. **Declarative** (default, and what first-party extensions should use): data + declaration, the
   kernel renders. No foreign JS anywhere.
2. **Isolated frame** (escape hatch): the extension serves its own UI and the portal embeds it in a
   sandboxed frame on a distinct origin with its own scoped token — exactly the access-controlled
   frame RFC 0020 built for hosted sites, reused rather than reinvented. The frame gets a
   capability-scoped token, not the session cookie, and talks to its own extension routes only.

Tier 2 is not in this RFC's units; naming it now is what keeps tier 1's declaration from being
designed into a corner (hence `placement`, `id`, and a versioned declaration schema from the start).

## Consequences

- **Parallel work stops colliding.** Ten units touch ten files; the registry line is the only shared
  edit. On this wave that would have removed the dominant cost.
- **The repetitive view stops being code.** Twelve hand-written renderers become declarations plus
  four layout modules; the fence, the empty state, the error arm, and paging exist once.
- **Predictable changes get cheap**: a column, an action, a view, a copy string, a theme.
- **Testing changes shape, and this RFC's first answer was wrong.** It proposed that a declared view
  be proven "by its declaration and its envelope — the columns it names, the actions it offers, the
  read it calls — not by re-walking generated DOM." Unit 3 wrote that test and measured it: twelve
  renderer mutations (columns reversed, every cell reading the first declared field, `render`
  ignored, empty copy never shown, an unavailable payload treated as a normal read, cursor never
  appended, pager never rendered, an actions column rendered without a declaration, refusal notice
  dropped, an applied outcome never reaching the placement, detail never rendered, the busy flag
  never raised) each red a rendering test, and **the declaration-only test survived all twelve** —
  it asserts the declaration equals itself, so it passes while the renderer ignores `columns`
  entirely.
  Tautological, not caught-elsewhere. The test stays in the suite under a name saying so, so the
  next reader meets the counter-evidence rather than rediscovering it.

  What replaces it is three-part, and each part covers what the others cannot. **Types** retire a
  whole class of test: `Column<Row>` is a distributive mapped type, so `field` must be `keyof Row`
  and `render` receives that field's exact value type — a field-name typo is a compile error, not a
  blank cell. **The renderer is mutation-tested once**, those twelve legs, for everything generic.
  **Each adopter carries one thin payload-binding test**, the leg types cannot reach: payload types
  are hand-written, so a server-side key rename type-checks and yields blank cells. Trading merge
  pain for test blindness is still the way this goes wrong; jsdom and `tsc` make it less likely, not
  impossible.
- **A new dependency surface.** npm packages in the portal's path are a supply-chain and upgrade
  cost the single file did not have. Lockfile committed, dependencies few and justified, and the
  runtime origin rule unchanged — nothing is fetched from a third-party host at run time.
- **A staged replacement, not a flag day.** Unit 1 replaces the page: `static/` becomes generated
  output and the hand-written `portal.html` moves into the frontend tree, so there is no "existing
  page" for later units to land beside. What stays incremental is everything after: views port one
  at a time, each PR porting one and deleting its imperative twin, with the member-visible surface
  unchanged at every step.
- **Cost**: roughly five units (below). This competes with product work; it earns its place only
  because the next wave of views (deploy status, billing detail, per-extension panels, a second
  surface) is a repeat of the last one.

## Doctrine fit / implications

**What stays out of core.** Everything here is the web extension's own: the kernel, the tokens, the
components, and the `listing()` declaration all live under `extensions/web/frontend/`,
and `PortalView` is a `Manifest` point an extension fills, not a core capability. Core gains one
thing — the manifest field and its boot validation — because a deploy-wide view-id namespace and a
declaration that names an action must be checked where the deploy is assembled; an extension cannot
validate another extension's ids. That is the same argument `Manifest`'s existing points carry.

**Extensions import only `ufo.sdk`** (gated at `gates.py:158`), so `PortalView` ships as an SDK
export alongside `Manifest`. The reads a declared view names stay the extension's own surface routes
or a core projection, and the audience gate stays where it already is — in the kind or the surface —
never in a declaration. A view declaration is presentation; it grants nothing.

**One shape.** A view is reachable because it registered; there is no second list. Today `TABS`
(`portal.html:279`) and the workspace label map are that second list, and a view added to one and
not the other is a defect the type system cannot see.

**Both ends or neither.** Each unit ships its consumer: the kernel with the views it hosts, the
`listing()` declaration with the three views that adopt it in unit 3, `PortalView` with sites moving
off its special case in unit 5. Nothing is declared for a later consumer — the isolated-frame tier
is named as a *constraint on the declaration's shape*, not added as a field.

**Fail loud.** A duplicate view id, an unknown placement, or an action naming no declaration fails
boot rather than rendering nothing. The current failure mode is silence: a view missing from `TABS`
simply never appears.

**Self-contained stays enforced, and gets stricter.** `test_portal_page_is_self_contained`
(`extensions/web/tests/test_ext_web.py:1633-1638`) asserts five things: the doctype, the presence of
`EventSource`, and no `http://`, `https://`, or `//cdn`. Splitting into modules makes the URL half
insufficient on its own — it would pass a page referencing a file that does not exist — so that half
becomes a gate asserting every `src`/`href` resolves under the surface's own static path and that
each referenced file is served. The doctype and the live-stream assertion do not disappear with the
string they read: the doctype belongs to the same gate, and `EventSource` becomes a behavioural
assertion in the vitest suite, where a stream that never opens fails rather than a substring going
missing.

## Alternatives

- **Stay hand-written with no build.** Rejected — this was the previous version of this RFC, and
  it was wrong. It reasoned from "the repo has no build" when the repo already builds the debugger's
  React app, with node in CI; the portal's single-file shape was a local habit,
  not a property to preserve. It also paid for that habit twice over: untyped client code, and a
  hand-rolled stub DOM whose growth and collisions were themselves a top-three merge cost this wave.
- **Plain TypeScript modules without React.** Rejected — by the system-wide standard, and the
  dissenting evidence is recorded rather than buried. Sampling wave 2's defects found they cluster
  in data (the turn tree's ordering and orphan bugs), async protocol (a `try` enclosing only the
  fetch), and lifecycle (teardown, focus) — *not* in DOM construction, which produced no defect
  found. On that evidence alone one `drawer` primitive and one `load()` fence would cover the real
  win, and plain TS would do. The standard decides otherwise for reasons the defect count does not
  measure: one idiom across every surface, one component set, one theme, and transferable
  familiarity. The portal was the outlier, not the adopter.
- **Hand-authored CSS instead of Tailwind.** Rejected. The rule that matters is that a view never
  authors a selector; utilities from one theme enforce it mechanically, where a component
  stylesheet enforces it only by review — which is exactly how `#panel form` shipped.
- **Keep one file, add discipline.** Rejected on evidence: discipline is what we applied this wave,
  with review rounds catching selector and slot collisions after the fact, and the file grew 2,488
  lines in two days.
- **Server-rendered HTML per view.** Rejected: the portal's live behaviour (SSE turn streaming,
  optimistic composer state, the drawer) is client state; server rendering would split it in two.
- **A UI schema language** (widgets, ordering, groups declared generically). Rejected, per
  the issue's own rule and RFC 0022's: no second consumer has demonstrated the need. Columns and
  actions are enough; add a widget hint when a real view is blocked without one.

## Open decisions

1. **Where does the build live in each environment?** The stack is settled — TypeScript, React,
   Tailwind, shadcn/ui, Vite, vitest + React Testing Library. What unit 1 must answer concretely is the
   build's placement: assets produced at image-build time, served by `ufoctl serve`, built in CI
   before the tests that need them, and a dev loop that does not make a Python change require an
   npm invocation. That is where an adopted build fails, not in the editor.
2. **How is a declared view tested? — answered, against this RFC's own proposal.** Declaration-only
   proof is tautological: measured in unit 3, it survived all twelve renderer mutations. The shipped
   answer is types for field binding, one mutation-tested renderer, and a payload-binding test per
   adopter. See the consequence above; this was the document's one concentrated risk and it resolved
   by refutation, not by confirmation.
3. **Does `listing()` cover a view with per-row heterogeneity? — answered: yes, and the premise was
   wrong.** This RFC predicted sources as the likely holdout needing a row-variant concept. The
   heterogeneity is in the **payload**, not the table: all seven columns are identical for grouped
   bindings and bare streams, and only the values are computed differently. A `rows: (payload) =>
   Row[]` projection normalizes them into one uniform list, so `Sources.tsx` became a declaration
   plus a projection — a 177-line component retired with no row-variant concept added.
4. **Where does chat live?** It is the one view that is neither a listing nor a static layout: it
   holds a live SSE stream, optimistic composer state, and handoff renderers. It stays hand-written
   here, but if the kernel's fence and drawer end up serving it too, the "layout-bearing" category
   may be smaller than this RFC assumes.

## Units

1. **Toolchain + kernel + theme, no *member-visible* behaviour change.** Adopt TypeScript, React,
   Tailwind, shadcn/ui, Vite, and vitest + React Testing Library; split `portal.html` into the
   skeleton, the theme, the component set, and the kernel; port the existing views into components.
   Porting an imperative view into a `.tsx` component **is a rewrite of the code** — what must not
   change is what a member sees and can do. The panel suites read payloads rather than markup and
   carry over untouched; **four** pytest tests read the page's markup and none of them survives as
   written, so each is replaced deliberately rather than silently:
   `test_portal_page_is_self_contained` (`test_ext_web.py:1633`) becomes an origin gate over built
   output, carrying the doctype, with `EventSource` becoming a behavioural assertion;
   `test_portal_page_smoke_walks_every_view` (`:3597`) moves off pytest into vitest with its own CI
   job; and `:1241` and `:1651` both assert `token-form` in the served body, which is static markup
   today (`portal.html:194`) and becomes a kernel branch the moment the route serves a skeleton — so
   httpx stops seeing it. Their route contract stays in pytest (the 200 behind a stale cookie, the
   303, the cookie's flags), including the absent-versus-stale distinction, which only the server
   can see: `surface.py:122-127` serves one shell for every unresolved portal GET and the session
   cookie is `httponly`, so the page cannot tell those states apart. What moves to vitest is the
   branch the page does decide — `api/agents` answering 401 renders the token form rather than an
   empty shell, the same arm a dropped fetch takes by design, since `boot()` folds a null response
   into it (`portal.html:2683`). That relocation is unit 1's work,
   not a later unit's. Any defect the port fixes on
   the way — the duplicated block, and whatever `tsc` surfaces — is named in the PR, not absorbed
   into "no behaviour change". Wire the build into the image, CI, and the dev loop, and prove
   the served path end to end. Splitting this into two PRs — adopt the toolchain, then move the
   views — is the expected shape; the toolchain half is the one that must not be rushed.
2. **The fence and the primitives.** One `load()` owning the token, abort, error, and empty; the 19
   hand-written fences delete; the harness gains a kernel fixture that mutation-tests them once.
3. **`listing()` + the first three declarations** (sources, artifacts, sites — one mutating, one
   paged with a detail pane, one minimal). Sites is *not* extension-owned, as an earlier draft of
   this list said: it was an inline component reading a core route, and nothing in unit 3 tests
   extension ownership. That property arrives in unit 5. Each PR deletes the renderer it replaces.
   This is where the test-shape question was settled in practice — by refuting the proposal above.
4. **The remaining declared views**, one PR, mechanical once unit 3 lands.
5. **`portal_views` in the manifest**, boot validation, and sites moving from a special case to a
   declaration — the proof that an extension can contribute a view without touching the portal.
   **The sample extension must declare one too**: `gates.py:221-223` requires the sample's non-empty
   Manifest points to equal the Manifest's point fields, so adding `portal_views` fails CI as
   `conformance: sample does not register Manifest point 'portal_views'` until
   `extensions/sample/ufo_ext_sample.py` exercises it. That is not bookkeeping — the sample's view
   renders in every deploy that loads it, which forces this unit to answer whether a declared view
   is placement-gated, audience-gated, or unconditionally rendered. `PortalView` as sketched in
   section 5 has no field for any of that, so the gate is what makes the question unavoidable, and
   the answer belongs in this unit rather than after it.

Units 1–2 are worth doing whether or not 3–5 follow: they remove the merge bottleneck and the
most-copied defect surface. **RFC 0022 was closed**, so unit 5 ships its read-only half only — a
declaration carries its read and its presentation, never actions, and no `actions` field reaches the
manifest until something can consume one.
