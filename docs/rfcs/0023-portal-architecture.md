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

- **No build step, no dependency, no CDN in the portal's path.** `test_portal_page_is_self_contained`
  forbids any literal external URL, and one process serves the page as static files. The repo does
  run npm builds elsewhere (`ci.yaml:30`, `:35-36` — the edge module and the debugger's React app),
  which is exactly the precedent argued against below: the debugger's build is what makes it awkward
  to change, and the portal is not to acquire one.
- **Mutations are audited turns** through the prepared-intent lane; reads are projections. RFC 0022
  proposes the declaration that removes the surface's duplicate copy of the callable set — this RFC
  assumes it and completes the other half (how the control is *rendered*).
- **Behaviour is proven by the stub-DOM walk with mutation checks**, not by asserting rendered
  markup in pytest. The harness does read the page source in three places, and each is a coupling
  the split must answer rather than inherit: the walk extracts the one inline script
  (`portal_smoke.mjs:919`), the CSS-scoping legs grep rule text (`:1648`, `:1650`, `:1678`), and
  `test_portal_page_is_self_contained` (`test_ext_web.py:1606-1611`) asserts against the page whole.
  Modules break the first, tokens and components make the second unnecessary, and unit 1 replaces
  the third with an origin gate — so this is a constraint the proposal *changes*, budgeted in unit 1,
  not one it inherits untouched.

## Proposal

Four separable pieces: a file layout that lets units work in parallel, a kernel, a declaration for
the repetitive view, and a token-based design system. Extension contribution falls out of the
declaration; third-party isolation falls out of RFC 0020.

### 1. Many files, still no build

ES modules over same-origin routes need no bundler. The web extension serves
`/surface/web/static/*.js`, the page carries one `<script type="module" src="…/kernel.js">`, and
each view is its own module. The self-contained gate changes from *one file* to *no external
origin* — the property that actually matters for CSP and for offline serving — and gets stricter
about it: the gate should assert every `src`/`href` resolves under the surface's own path.

```
static/
  portal.html      markup skeleton + <link>/<script> only
  tokens.css       the design system's variables
  components.css   the fixed component set
  kernel.js        boot, session, routing, fence, registry, primitives
  views/
    listing.js     the generic declared-listing renderer
    chat.js        layout-bearing
    conversation.js  layout-bearing (turn tree, file pane)
    artifact.js    layout-bearing (pinned viewer)
    admin.js       layout-bearing (workspace administration)
```

Ten units editing ten files conflict where they genuinely overlap (the registry line) instead of
everywhere. The same split applies to the harness: a kernel harness proving the primitives once,
and one fixture file per view.

### 2. The kernel

Small and owned centrally, because these are exactly the things that must not be re-implemented:

- **Boot and session** — the `api/agents` read, the token card branch, the signed-out state.
- **Routing** — the hash grammar (`#/agents/<id>/<tab>`, `#/workspace/<view>`, `#/admin`) parsed
  once, with the listing cursor and filter carried as query state so a reload lands where the
  member was.
- **The request fence** — one `load()` helper that owns the monotonic token, the abort of a
  superseded read, the error arm, and the empty arm. A view never writes `load !== panelLoad`
  again; the 19 hand-written copies become one.
- **The primitives** — `table`, `row`, `cell`, `emptyState`, `errorLine`, `notice`, `pageControls`
  (`ListingCursor`, already on `main` at `core/src/ufo/listings.py` and re-exported through
  `ufo.sdk.listings`), `drawer` (the artifact viewer's pinned panel, reusable),
  `formFromSchema` (generalizing `specField`, `portal.html:1440-1467` — which today builds a
  `<select>` only when its caller passes an options list, never reads `prop.enum` or
  `prop.description`, and labels a field with its raw key, so the kernel builds those), `confirm`.
- **The view registry** — `register({id, placement, label, render})`, one line per view. A view is
  reachable because it registered, not because a `TABS` array elsewhere lists it.

### 3. Declared listings

Most portal views are the same view: fetch a projection, render rows with per-row actions, page,
show an empty state. That is a declaration, not code:

```js
register(listing({
  id: 'sources',
  placement: 'workspace',
  label: 'Sources',
  read: 'workspace/sources',
  key: 'sources',
  columns: [
    { field: 'backend', label: 'source' },
    { field: 'streams', label: 'streams', render: joinSorted },
    { field: 'owner_email', label: 'owner' },
    { field: 'shared', label: 'access', render: (v) => v ? 'shared' : 'private' },
    { field: 'next_sync_at', label: 'next sync', render: minute },
  ],
  actions: ['source.resync', 'source.share', 'source.remove'],   // RFC 0022 declarations
  empty: 'No sources are registered. Register one in chat …',
}));
```

`actions` names RFC 0022 `MemberAction` declarations; the kernel renders each control, derives its
form from the action's `submit_model`, submits the intent, and renders the callee's outcome
verbatim.
Sources, credentials, artifacts, sites, tasks, skills, connections, and usage all collapse to
declarations of this shape. Memory does not: RFC 0022 places its paging on the hand-written side and
this RFC follows. So the hand-written set is chat, the conversations debugger, the artifact viewer,
memory, and the administration view — their value *is* layout — and each registers identically, so
the kernel does not care which kind a view is.

The payoff is in the predictable changes: **a new column** is one line; **a new action** is one
declaration on the kind (RFC 0022) plus one name here; **a new listing view** is one declaration
and no kernel edit; **copy** lives beside the declaration where a gate can check it.

### 4. The design system

One token layer, one component set, no per-view CSS — which is what makes selector collisions
structurally impossible rather than review-caught.

**Tokens** (`tokens.css`) — colour, space, type scale, radius, border, elevation, motion, as CSS
custom properties, defined once for light and dark. Today's `Canvas`/`CanvasText` system colours
stay the light/dark source (they give correct contrast free and cost nothing), but they resolve
*into* tokens, so the eventual convergence with the `/login` redesign (#503) is a token file swap,
not a sweep through 181 lines of rules. Values are named by role (`--surface`, `--edge`,
`--muted`, `--accent`, `--space-2`), never by appearance.

**Components** (`components.css`) — the fixed set the kernel renders: page shell, sidebar, tab
strip, table, row, cell, action control, drawer, form field, hint, empty, notice, pager, badge,
bubble. Each carries its own class; **a view may not author a selector**. That single rule kills
the `#panel form` class of defect: a view cannot restyle a sibling because it never names one.

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
                             read="…", columns=…, actions=…, empty=…),),
)
```

Core validates declarations at boot in the registry that already validates kinds and tools (unknown
field → fail loud, duplicate id → fail loud, an action naming no declaration → fail loud). The
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
- **Testing changes shape**, and this is the real risk. Today's proof is a mutation-red walk over
  hand-written markup. Generated markup is harder to mutate-test per string, so the harness must
  split: the kernel's primitives get the mutation treatment once (fence, empty, error, drawer,
  pager, form derivation), and a declared view is proven by its declaration and its envelope — the
  columns it names, the actions it offers, the read it calls — not by re-walking generated DOM. A
  layout-bearing view keeps its bespoke walk. Getting this wrong would trade merge pain for test
  blindness; it is the piece to prototype first.
- **A migration, not a rewrite.** The kernel and tokens land under the existing page; views move one
  at a time, each PR moving one view and deleting its hand-written twin. Nothing needs a flag day.
- **Cost**: roughly five units (below). This competes with product work; it earns its place only
  because the next wave of views (deploy status, billing detail, per-extension panels, a second
  surface) is a repeat of the last one.

## Doctrine fit / implications

**What stays out of core.** Everything here is the web extension's own: the kernel, the tokens, the
components, and the `listing()` declaration all live under `extensions/web/ufo_ext_web/static/`,
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
(`extensions/web/tests/test_ext_web.py:1333-1338`) asserts the page string carries no `http://`,
`https://`, or `//cdn`. Splitting into modules makes that assertion insufficient on its own — it
would pass a page referencing a file that does not exist — so it is replaced by a gate asserting
every `src`/`href` resolves under the surface's own static path and that each referenced file is
served.

## Alternatives

- **Adopt a framework** (React/Svelte/Lit). Rejected: it buys componentisation we can get from ES
  modules plus one CSS layer, and it costs a build step, a dependency, and a second idiom in a repo
  whose whole surface is served as static files by one process. The debugger's React app is
  precedent *for* an operator tool, not for the member portal — and its build is exactly what makes
  it awkward to change.
- **Keep one file, add discipline.** Rejected on evidence: discipline is what we applied this wave,
  with review rounds catching selector and slot collisions after the fact, and the file grew 2,488
  lines in two days.
- **Server-rendered HTML per view.** Rejected: the portal's live behaviour (SSE turn streaming,
  optimistic composer state, the drawer) is client state; server rendering would split it in two.
- **A UI schema language** (widgets, ordering, groups declared generically). Rejected, per
  the issue's own rule and RFC 0022's: no second consumer has demonstrated the need. Columns and
  actions are enough; add a widget hint when a real view is blocked without one.

## Open decisions

1. **Does the split survive the no-build rule in practice?** ES modules over same-origin routes need
   no bundler, but each module is one more request on first paint and the portal currently ships as
   a single string with no cache story. Unit 1 measures it; if first paint regresses materially, the
   fallback is concatenation at serve time, which is a build step in everything but name and should
   be argued before it is adopted.
2. **How is a declared view tested?** Named as the concentrated risk below and deliberately left
   open: unit 3 settles it against three real views before units 4–5 mechanize the rest.
3. **Does `listing()` cover a view with per-row heterogeneity?** Sources renders grouped bindings
   and plain rows differently today. Either the declaration grows a row-variant concept — the
   generic machinery this RFC otherwise avoids — or that view stays hand-written. Unit 3 includes
   sources precisely to find out.
4. **Where does chat live?** It is the one view that is neither a listing nor a static layout: it
   holds a live SSE stream, optimistic composer state, and handoff renderers. It stays hand-written
   here, but if the kernel's fence and drawer end up serving it too, the "layout-bearing" category
   may be smaller than this RFC assumes.

## Units

1. **Kernel + tokens, no behaviour change.** Split `portal.html` into the skeleton, `tokens.css`,
   `components.css`, and `kernel.js`; the existing views move as-is into modules with no rewrite;
   the self-contained gate becomes an origin gate. Proves the split and the serving path alone.
2. **The fence and the primitives.** One `load()` owning the token, abort, error, and empty; the 17
   hand-written fences delete; the harness gains a kernel fixture that mutation-tests them once.
3. **`listing()` + the first three declarations** (sources, artifacts, sites — one mutating, one
   layout-adjacent, one extension-owned). Each PR deletes the renderer it replaces. This is where
   the test-shape question is settled in practice.
4. **The remaining declared views**, one per PR, mechanical once unit 3 lands.
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
most-copied defect surface. Unit 5 depends on RFC 0022's `MemberAction` for its actions half; the
read-only half stands alone.
