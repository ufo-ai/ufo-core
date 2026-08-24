---
rfc: 0039
title: "First-class apps"
status: proposed
date: 2026-08-22
---

# First-class apps

> User-defined apps and built-in apps run on one infrastructure and are equally capable. An app is
> an agent plus its homepage page; the page gets one capability surface — object reads, journaled
> object writes, entry points, and a synced component library — and the member edits any app from
> the chat column beside it. The built-in portal screens (wiki, artifacts, radar, tasks, plus a new
> chat screen) leave the web extension's compiled bundle and become five extensions, `app_chat`,
> `app_radar`, `app_tasks`, `app_artifacts`, `app_wiki`. This revises spec.md's portal section and
> the "prepared intents are the portal's one mutation path" doctrine.

## Current state

An app already is an agent: the Applications flyout is data-driven from `GET api/agents` +
`GET api/agents/status` (`extensions/web/ufo_ext_web/surface.py:750,798`), and every agent renders
through one generic pane — homepage iframe plus a chat column (`views/AgentPane.tsx:71`). The
built-in screens are not apps: they exist as hard-coded names in five parallel frontend tables
(`lib/route.ts:10`, `App.tsx:700-710`, `views/registry.tsx:70`, `lib/title.ts`, `lib/search.ts`)
and hand-written views. Pins already mix agent ids and screen names in one localStorage list
(`App.tsx:683`).

The gaps that keep built-ins from being apps:

| Gap | Evidence |
|---|---|
| An app page is blind: no CORS, no postMessage bridge, no data API — a framed site can reach only its own sandbox origin | RFC 0020; `ingress_serve.py`; zero `postMessage` hits in `extensions/web/frontend` |
| An app page is inert: no way to act as the member or navigate the portal (`allow-top-navigation` withheld) | `extensions/sites/ufo_ext_sites/surface.py:90` |
| Site bytes live only in the sandbox — serving means dialing it; a core screen cannot depend on that | RFC 0020 ("hosting is registering that port"); `tools.py:440` |
| Redeploys are invisible: the pane never remounts the iframe (permanent URL), and the frame's ingress session dies at 1h with no recovery | `views/AgentPane.tsx:140`; `ingress_serve.py:164` |
| Screen data bypasses the object model: radar hand-reads `report_digest`'s table via a literal `sa.table`; conversations, shared files, and memory are bespoke surface routes | `surface.py:3339-3386,1946,3229,2254` |
| Portal mutations are a hand-coded closed union in web's source, so extensions cannot declare actions | `panels.py:596-613` |
| Object changes have no uniform record: `created_refs`/`created_in` cover creation only; an update or delete is durable only as transcript text | `objects.py:64`, `cancellation.py:56` |

## Proposal

### One capability surface

Every framed app page — user-built and built-in identically — gets exactly this, and nothing else:

| Capability | Mechanism |
|---|---|
| Read | `objects/{kind}` and `objects/{kind}/{name}` under the member's own gates |
| Write | Direct object verbs, journaled (below), member-gated in the shell |
| Sync | A cursor tail of the object-change journal, pushed into the frame |
| Act beyond objects | The prepared-intent lane, reduced to tool acts with conversational delivery (connect, credentials, billing) |
| Navigate | Declared entry points, executed by the shell |

An app is "equally capable" by construction: `app_wiki` is expressible only if memory is a real
kind, `app_chat` only if conversations list as one — and the moment they do, any user app reads
the same kinds under the same gates.

### Object model completion

Every screen's data becomes an object kind with `member_page`/`member_detail`:

| Data | Today | Becomes |
|---|---|---|
| Radar entries | web reads `report_digest_entry` via a hand-written `sa.table` | `report_digest` registers the kind; the bespoke read dies |
| Conversations | `api/chats` bespoke projection; the kind reads one row at a time (spec.md:704) | the kind gains a member listing — the `api/chats` query becomes its gate. `mine`/`speaker` are viewer-relative computed fields, a new listing capability a kind may declare beside its row scalars |
| Shared files | `workspace/artifacts` bespoke read | the `artifact` kind's member listing carries the signed servers/preview/download links |
| Memory (wiki) | `workspace/memory` bespoke read | the `memory` kind's member listing covers it |
| Scheduled, triggers, members, sites | already kinds | unchanged |

### Direct object writes

`POST objects/{kind}` (apply) and `DELETE objects/{kind}/{name}` on the member surface execute the
kind's verb directly: the session already names the member, `ws(...)` is already bound, and the
internal/external split already exists (`/surface/*` is member-identified, `/ext/*` is anonymous).
Seat and solvency are one-line gates at the handler; the kind's own guards and generation fences
are unchanged and already own the concurrency story.

**The rule: object verbs cross as direct writes; tool acts cross as prepared intents.** The intent
lane keeps exactly the acts that need conversational delivery — `connect_account` (the OAuth URL is
minted per speaking member at stream time), `request_credentials` (the sealed prompt),
`manage_billing`, `read_private_transcript` — and stops carrying object mutations. The `PanelIntent`
union shrinks accordingly. The rebuilds leave the lane entirely: a rebuild is state mutation —
dropping the digest entries in the job's window, clearing the fact deriver's cursor — so once the
kinds land (A2) it is object writes the kind's own handlers interpret, and the owning job refills
on its interval; derived state stays the job's. An app button is never a tool dispatch.

Intent turns were checked for introspection before this cut: memory recall, the saved-skills
block, and the shadow selector already exclude them by construction (`loop/queue.py:130`); the
radar feed filters to scheduled admissions; the rail drops their conversations. What does read
them — `created_in` links, ledger attribution, the unfiltered `TrajectoryCorpus`
(`ext/context.py:338`), the operator debugger — is covered better by the journal.

### The object-change journal

One core table, written by the object layer itself so every path is covered — chat turn, direct
write, extension job alike:

```
object_change: workspace_id, id, kind, name, verb (create|update|delete),
               caller (member_id | turn_id | job), spec_before_ref, spec_after_ref, created_at
```

The before/after spec bodies live in the workspace blob store; the row carries only their refs, so
rows stay light whatever a kind's spec weighs. Retention is a config knob from day one.
Keyset-paged on the shared cursor like every listing. Three consumers, one mechanism:

1. **Audit** — the complete record of who changed what, including the updates and deletes that
   today survive only as transcript text. An admin portal read lists it.
2. **Component sync** — a journal with a cursor is a change feed; the shell tails it once and
   pushes deltas to every embedded component.
3. **Self-improvement signal** — member form actions as structured events, replacing the fishing
   of intent transcripts out of the trajectory corpus.

### The app runtime

- **Static serving** — RFC 0020's named follow-up, done: `deploy_website` promotes the served
  directory into the workspace blob store, and ingress serves those bytes behind the same tokens
  and per-site origins with no sandbox dial. `publish_website` (dynamic backends) stays
  sandbox-served. Every user's static app survives sandbox reclaim; a built-in default never
  depends on a sandbox.
- **The bridge** — a postMessage RPC between the pane and its iframe, origin-checked both ways,
  with a small client the portal serves: `read`, `write`, `intent`, `navigate`, `place` (the app's
  internal state rides the existing hash place-bag both ways, so permalinks keep working), and
  `tail` (journal subscription). An app page is member-authored content whose JS runs on load, and
  the shell cannot see clicks inside its frame — so a frame-initiated write to an admin-gated kind
  (`member`, `credential`, `agent`, …) takes a shell-rendered confirmation naming the kind and
  verb, because a privileged act needs the viewer's explicit grant. Ordinary-kind writes flow
  under the viewer's session, every one journaled. Shell-owned forms keep dispatching on their own
  clicks.
- **The loop closes** — `hosted_site` gains a deploy generation bumped on `register()`; the
  homepage read carries it; the pane keys the iframe on it, so a redeploy shows within one poll.
  The remount also re-mints the frame's ingress session, so a long-open app does not go dark at 1h.

### The component system

A library of portal-quality components — conversation list, chat UI, scheduling widget, contact
card, file card — served versioned from the portal's static assets, importable by any app page,
themed by the `ufo-style` tokens. Each component binds to a kind through the bridge (read + write)
and stays live on the journal tail. The chat component is the one exception to object reads: it
wraps the existing chat transport (transcript read, SSE turn stream, chat POST) through bridge
passthroughs, because turn frames are not objects. A component enters the library with the first
app that uses it, never speculatively.

### Apps as extensions

The `agents` manifest point's provision gains an optional app declaration:

```python
AgentProvision(name="Chat", spec=AgentSpec(...),
               app=AppSpec(slug="chat", bundle=BUNDLE_DIR,
                           entries=("new", "conversation")))
```

- **Slug** claims the route (`#/chat`) and the flyout identity; slugs are validated for collisions
  where the deploy is assembled, like every deploy-wide namespace.
- **Bundle** is a directory of static files shipped with the extension — the app's default page,
  served from the deploy's own assets at a stable frame URL, versioned with the extension. No
  per-workspace row, no LLM seed turn (`seed_homepages` skips a bundle-carrying agent).
- **The first edit forks**: the member asks in the chat column, the agent pulls the current page
  source (blob- or package-backed bytes are readable), edits in its sandbox, and runs the ordinary
  `deploy_website` + `set_homepage` — now it is a workspace site and the existing loop owns it.
  The homepage read grows a `shipped` state beside `set`/`building`/`none`; reset is unbinding the
  fork, falling back to the shipped bundle. Unedited workspaces track deploy upgrades; edited
  workspaces own their copy — the RFC 0030 pattern.
- **A fork is workspace-owned**: any member may ask the app's agent to edit and redeploy it — the
  site-creator gate relaxes for a homepage bound to an extension-shipped agent — and an admin may
  reset it to shipped. An app is a workspace surface, collaborative like the wiki's own content.
- The sample extension exercises the declaration (the conformance gate), `ufo.sdk` re-exports the
  type, `ExtensionSpec` names it, and spec.md's manifest table gains the row, all in the same unit.

### The five apps

| Extension | Default page | Entries | What it proves first |
|---|---|---|---|
| `app_chat` | conversation list (click opens `#/c/<id>`) | `new`, `conversation(id)` | bridge reads + navigation; the conversation kind listing |
| `app_radar` | digest feed | `at(place)` | the radar kind migration; tool intents (rebuild) |
| `app_tasks` | scheduled + triggers | `open(kind, name)` | direct writes with confirmation (pause/resume) |
| `app_artifacts` | shared files + sites shelf | `open(id)` | link minting in kind listings |
| `app_wiki` | memory document + roster | `member(id)` | the memory listing; contact-card component |

Each conversion tears out its React view, its registry rows, and its bespoke backend read in the
same unit. `app_chat` is new (conversation list on its homepage — no parity burden) and lands
first; the four conversions replicate today's screens. Connectors, admin, and the workspace views
(team, memory, sources, credentials, usage, billing) stay shell-rendered and are out of scope.

### Build order

| Unit | Delivers | Its consumer in the same unit |
|---|---|---|
| A1 | journal + direct object writes; intent union shrinks to tool acts | existing portal forms switch to direct writes |
| A2 | kind coverage (radar, conversation, artifact, memory listings) | existing React screens switch to `objects/{kind}` reads |
| B1 | static site serving + deploy generation + pane remount/session refresh | every existing homepage and user app |
| B2 | the bridge + the `app` manifest declaration + slug routing + generic app pane | `app_chat`, whole |
| C | `app_radar`, `app_tasks`, `app_artifacts`, `app_wiki`, one unit each | each tears out its screen |
| D | component library entries (conversation list, scheduling widget, contact card, chat UI), interleaved with C | the app whose bundle first embeds each |

A2 is deliberately before any app ships: converting the React screens to kind reads proves the
kinds against the exact UI the bundles must then match.

## Doctrine fit / implications

- **Revises doctrine**: CLAUDE.md and spec.md currently fix "prepared intents — a portal form's
  one mutation path." The new line is: *object verbs cross as direct writes journaled at the
  object layer; tool acts cross as prepared intents.* A shell-visible click gates shell-form
  writes; a frame-initiated write to a privileged kind takes the viewer's confirmation; an
  ordinary-kind frame write flows under the viewer's session with the journal as its record. Both
  docs update in the same commits as A1.
- **Core earns only what extensions cannot express**: the journal (the object layer is core; no
  extension can observe another's writes), static serving (ingress is core), the app declaration
  (the manifest is core, proven by the sample). The bridge and component library are the web
  extension's. The five apps are extensions importing only `ufo.sdk`.
- **One shape**: a converted screen exists only as its extension's app; the React view, its
  registry rows, and its bespoke read are deleted in the conversion unit. Unconverted screens stay
  React until their unit — conversion is per-app, whole.
- **Both ends or neither** holds per unit, per the table above.
- A deploy without an `app_*` extension simply lacks that app; kinds whose extension is absent
  state that absence, as today.

## Alternatives

- **Frontend registry refactor only** — one typed registry over the existing React views. Makes
  the shell data-driven but the apps remain compiled-in and uneditable; rejected as not the goal.
- **`portal_views` declarative manifest point** (RFC 0023 unit 5) — extensions declare columns,
  the kernel renders. Caps every app at what the declaration schema anticipates; full-featured
  apps need code, and RFC 0023 already deferred it for lack of a consumer.
- **Extension-defined member endpoints** — N hand-rolled authority implementations to audit, N
  client APIs for pages to speak, and identity-assertion is deliberately reserved to the surface
  seam; a dead end for third-party (WASM) extensions.
- **Prepared intents as the app write path** — rejected on the internal-call argument: the surface
  already holds the member and the workspace; wrapping a typed write in a turn adds a queue
  dispatch and buys authority the handler already has. The journal audits better than the turn
  record it replaces (updates and deletes included), and the acts that genuinely need
  conversational delivery keep the lane.
- **Provenance-tiered capability** (shipped bundles trusted, forks confirmed) — violates "equally
  capable"; the gate keys on the kind's privilege, never on who authored the page.
- **Declared member-action tools** — marking tools portal-invocable so app buttons dispatch them.
  Rejected: a button's call is an internal endpoint ending in object saves; the rebuilds reduce to
  kind writes, and tools stay a chat concern.
- **Sandbox-seeded defaults** — activation deploys each default app into a sandbox: five idle
  servers per workspace and `SITE_GONE` failure modes on core screens; rejected for static serving.

## Deferred for the prototype — security debt (documented, not built)

The build proceeds simplest-thing-first, reusing existing infra, with security deferred and written
down here. A four-lens adversarial review (2026-08-22) found these holes; none blocks the prototype,
all must be closed before this ships to untrusted input. Ranked by severity.

| # | Hole | Evidence | The fix, when we harden |
|---|---|---|---|
| 1 | **Frame JS speaks as the member.** If the chat component proxies the chat POST, a fork's onload JS founds a turn as whoever views it — a non-admin's `app_wiki` page, opened by an admin, silently runs `add_member` (its only gate is `speaker_is_admin`, satisfied by the viewer). Admin escalation, no click. | verified `members.py:324`; `surface.py:1143` | The bridge only proposes; the composer/write is shell chrome the frame can pre-fill but never send. |
| 2 | **The "privileged kinds" gate is unimplementable.** Privilege lives in each kind's handler (`ctx.speaker_is_admin()`), not in any declaration the shell can read, so the shell cannot know which frame writes to confirm. | verified `objects.py:8-9` | Axis is gesture provenance, not kind privilege: every frame-proposed write confirms (session-grantable per app); privilege only escalates the confirmation. |
| 3 | **The journal tail leaks across audience gates.** `object_change` rows carry no audience; a workspace-wide feed pushed to a frame announces colleagues' private `source`/`scheduled_task`/`connection` rows, and `spec_before` discloses content the viewer lost access to. | `objects.py:376-408`; spec.md:229 | Invalidation-only tail (`kind`+cursor, no bodies); the component re-reads through `objects/{kind}` under the viewer's own gate. |
| 4 | **Viewer-authority reads flow into fork-authored JS that can beacon out.** "Under the viewer's gates" authorizes the viewer, but the code is the fork author's; a rendered page exfiltrates. | RFC 0020 (egress while a turn runs; `<img>` beacon even static) | Content-bearing reads are shell-rendered components the frame places but can't read, **or** framed apps get a no-egress bridge-only CSP. |
| 5 | **navigate/place unallowlisted.** A frame can redirect/phish the member (the shell executes navigation the sandbox's withheld `allow-top-navigation` otherwise denies) or poison another app's place-bag. | doc §bridge; `sites/surface.py:90` | `navigate` accepts only the app's own declared entries + the conversation permalink; `place` writes only the calling app's namespace. |
| 6 | **The sibling-subdomain cookie risk** (RFC 0020, open) — site origins can plant cookies on the parent domain; framing core screens widens the exposure. | RFC 0020 risk note | A separate registrable site domain, or the gateway refusing multi-`ufo_session` requests. |
| 7 | **Site-frames-site is scoped per workspace.** `frame-ancestors` names the app origin plus every hosted-site and active shipped-app origin in the responding site's own workspace, so an app page embeds a sibling's live view (the artifacts app leads a site's record with it) while an origin in another workspace is refused as a framer. Within one workspace, site A framing site B still rides B's viewer cookie along. | `IngressServe._frame_ancestors` | Scope framing to the embeds a page declares, or per-site frame grants. |

The single reframing that closes 1, 2, 4, 5 together, and is more parsimonious than the current
prose: **the bridge proposes; shell chrome acts.** A frame reads (metadata/refs), places components,
navigates its own entries, and receives invalidation ticks — it never silently writes, sends chat,
or dispatches a tool. That shrinks the bridge to `read` / `place` / `navigate` / `tail` and deletes
the privileged-kind list, the `intent` verb, and the confirmation-axis problem in one move.

## Open decisions

1. **Journal retention default** — the knob exists from day one; its default is picked at A1.
2. **The sibling-subdomain cookie risk** (RFC 0020, open) is deliberately deferred: framing core
   screens widens exposure user apps already carry, and the fix (a separate registrable site
   domain, or the gateway refusing multi-`ufo_session` requests) is taken up when it is, not here.
   RFC 0020's risk note stands.
