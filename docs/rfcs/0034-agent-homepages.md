---
rfc: 0034
title: "Agent homepages — every agent hosts a site the dash frames"
status: proposed
date: 2026-08-16
---

# Agent homepages — every agent hosts a site the dash frames

> The agents page is a table over boot facts: name, details, model. What an agent is for, what it
> is watching, what it did lately, and what it needs from members lives nowhere a member can look.
> This RFC gives every agent a homepage: a hosted site (RFC 0020) the agent builds and rewrites
> with the tools it already has, bound to the agent by a pointer, framed in a revamped
> master-detail agents page — thin agent index beside a wide pane whose default tab is the
> homepage. A member changes a homepage the way they change anything: by asking the agent in chat.

## Current state

| | Today | The gap |
|---|---|---|
| Agents page | one table (agents + subagent profiles merged) with a List \| Graph toggle (`views/Agents.tsx`); per-agent detail is a drawer over the list (`views/AgentPane.tsx:33`) | no per-agent place that says what the agent is doing |
| Agent-authored pages | `deploy_website`/`publish_website` host a sandbox port at a permanent link (`extensions/sites/ufo_ext_sites/tools.py`) | site identity is `(conversation, name)` (`store.py:46`) — nothing names one site as *the agent's* |
| Site liveness | on e2b, idle pauses unbilled and the ingress `dial` is a `connect` that resumes in ~110–360ms with the server still running (`extensions/e2b/ufo_ext_e2b.py:110`); a sandbox the provider lost answers 503 (`core/src/ufo/sandbox/ingress_serve.py`) | acceptable: the dash's frame pays a sub-second resume, and 503 is the rare lost-sandbox case (and single-shape dev carriers) |
| Rewrites | any member request in chat; a re-deploy of the same name updates behind the same link | a rewrite from another conversation is a *different site* — the binding must be movable |

## The homepage is a hosted site

The homepage reuses the whole sites stack — build tools, `hosted_site` registry, visibility
column, frame, ingress — and adds exactly one thing: an agent-scoped pointer to the site that is
currently the homepage. No new storage for bytes, no snapshotting, no second serving path.
Consequences accepted by this design:

- The page is as live as its sandbox: paused resumes on view; a provider-lost sandbox renders the
  frame's own 503 until the agent redeploys.
- The page may be a real app (scripts, its own backend via `publish_website`), sandboxed and
  origin-isolated exactly as every hosted site already is.
- Who may open it is the site's `visibility`, gated per visit by the frame — the dash adds no
  second disclosure gate.

## Binding

The binding lives where the registry lives: `hosted_site` gains a nullable `homepage_agent_id`
column with a partial unique index on `(workspace_id, homepage_agent_id)` — at most one homepage
per agent, held by the row itself (sites migration). Unhosting or deleting the bound site clears
the binding by construction; there is no dangling-pointer state.

One new tool, registered by the sites extension beside its deploy tools:

| Tool | Input | Act |
|---|---|---|
| `set_homepage` | `site` — the site object name from the deploy result | resolve the name against the registry (the kind's own `_find`, `objects.py:226`); refuse a name that resolves to nothing; in one transaction clear the acting agent's current homepage row and stamp this one |

- The tool binds the turn's own agent (`ctx.agent`) — an agent designates its own homepage.
- **Speakerless-safe.** Binding widens nothing — the frame still gates every viewer on the site's
  own visibility — so `set_homepage` runs on scheduled turns, which seeding requires. Deploying
  and re-gating keep their existing speaker rules.
- A same-conversation re-deploy of the bound name updates in place and needs no rebind; a rebind
  leaves the old site an ordinary site.
- The tool's prose carries the one trap: a site deployed in a DM defaults `private`
  (`store.py:100`), so a homepage should be deployed `visibility="workspace"` unless the agent's
  audience is one member. The result echoes the bound site's visibility so the model self-corrects.
- The `site` kind's `list_fields` gains `homepage_agent` (the agent id, absent on ordinary
  sites) — the declared, filterable field the portal reads the binding through.

## Read

One new panel read behind `_panel_gate` (`extensions/web/ufo_ext_web/surface.py:1551` — an
out-of-audience agent stays 404):

```
GET agents/{agent_id}/homepage
  → {"state": "set", "url": <site_url>}
  → {"state": "none"}
```

The handler queries `ctx.list_member_objects("site", ...)` filtered on
`homepage_agent=<agent_id>` — the sanctioned cross-extension seam the object pages already use
(`surface.py:3018`), whose rows carry `site_url` (`extensions/sites/ufo_ext_sites/objects.py:123`).
The payload is exactly what the Home tab consumes; a query matching no row, a site this viewer may
not see, and a deploy hosting no links all answer `none`.

## Embed

The portal frames the existing sites frame (`/surface/sites/<token>`) in a plain same-origin
iframe filling the pane. The frame is the app's own trusted page and already does everything —
session-cookie gate, per-visit visibility check, the creator's visibility selector in its header,
and the sandboxed cross-origin inner iframe around the model-authored bytes
(`extensions/sites/ufo_ext_sites/surface.py:239`). The outer iframe takes no `sandbox` attribute:
sandbox flags inherit, and the inner site is promised scripts. A dead sandbox, an unregistered
site, and a not-public site render as the frame's own states inside the pane. The portal's CSP
admits `frame-src 'self'` if it does not already.

## Seeding — and the backfill is the same sweep

No agent-created hook exists, and batch-at-interval is the default, so seeding is one recurring
web-extension `JobSpec` (the title-job pattern, `extensions/web/ufo_ext_web/manifest.py:39`). The
marker alone decides — the sweep never reads homepage state, so it cannot fire on rows it caused.
Each tick, per workspace, for every agent with no `homepage-seed/<agent_id>` marker in the
extension's store:

1. Write the marker — once ever, written at admit.
2. `ctx.open_conversation(agent_id, f"homepage/{agent_id}")` — agent-held, no member,
   `SHARED_AUDIENCE` (`core/src/ufo/ext/context.py:1643`), so the deploy's default visibility is
   `workspace` and the room never reaches a rail.
3. Admit one turn: `ctx.invoke(conversation, agent_id, SEED_PROMPT, "homepage-seed:<agent_id>",
   on_behalf_of_member_id=..., as_scheduled=True)` — the pause-runner pattern
   (`extensions/scheduled_tasks/ufo_ext_scheduled_tasks/pause_runner.py:46`).

- **On-behalf member:** the agent's `owner_member_id`; for an ownerless agent (main,
  provisioned), the workspace's earliest-seated admin. `deploy_website` requires an acting
  member, who becomes the site's creator and visibility owner.
- **One core addition.** No `ExtensionContext` accessor answers the workspace's agent roster; the
  sweep needs `(agent_id, owner_member_id)` rows and the earliest-seated admin. Extensions cannot
  express this, and `invoke_agent_for_member` (`core/src/ufo/ext/context.py:939`) is the
  precedent for mediating core tables behind the context — the unit adds the read accessor there.
- **Once ever.** A failed seed turn does not retry; the absent state names the recovery (ask in
  chat). An agent that already built a homepage before its marker exists gets one redundant seed
  turn, which its own `set_homepage` state makes a no-op in substance.
- Existing agents fleet-wide seed through the same sweep on first deploy of this change — the
  backfill is not a separate mechanism.
- `SEED_PROMPT` tells the agent to build a homepage stating what it is, what it watches, recent
  work, and what it needs from members, then `deploy_website` and `set_homepage` it.

## Rewrites

Pure chat, no new machinery: the member asks, the agent edits in that conversation's sandbox,
`deploy_website` (+ `set_homepage` when the conversation differs from the bound one). The turn is
the audit record, as every member action already is.

## Portal: the agents page becomes master-detail

Three columns: the existing nav, a thin agent index, a wide pane.

| Piece | Change |
|---|---|
| Index column | new `--container-index` token (~272px) in `theme.css`; search, List \| Graph segmented, one row per **agent** (name, model), the New agent act |
| Routes | `#/agents/<uuid>` renders the pane in place — the drawer-over-list dies (`views/AgentPane.tsx` stops being a `RecordPanel`); bare `#/agents` renders the main agent selected without navigating |
| Halves | the pane is the conversation and the homepage side by side, equal, read at once — no tab strip and no `AGENT_TABS`: the homepage iframe stands in its own half, headed `Home` |
| Overview → Settings | with the homepage in its own half, Overview is the config surface and its name says so: tab label, hash segment, `views/Overview.tsx`, and the `agents/{agent_id}/overview` read rename to `settings` — one name on every end, its own commit |
| Tab headers | one header shape across every tab of the pane: the agent's name beside the pill tab strip (`kernel/tabs.tsx` Segmented), each body opening at the band pitch Settings' `Group` headings set (`components/ui/facts.tsx:15`) — no tab draws a header variant of its own |
| Graph | draws in the wide pane; an agent node click routes to that agent's Home |
| Narrow | the index is the page; a selected agent overlays it, the existing `max-narrow` record pattern |
| Absent state | no half at all — the pane is one column, because a column whose only content is the sentence that it is empty says nothing the missing half does not |

**Subagent profiles leave the agents page.** They are profiles, not agents — no identity, no
sandbox, no homepage. The index lists agents only; `views/SubagentPane.tsx`, the `#/subagents`
routes, `SUBAGENT_TABS`, and the three backend `subagents/{name}/*` reads
(`extensions/web/ufo_ext_web/surface.py:3167`) are torn out whole. The boot payload's `subagents`
keeps exactly one consumer: the graph's group tile (`views/AgentGraph.tsx`), which stays — it is
live topology, not a browsable record.

## Non-goals

- **A durable snapshot store.** A blob-backed homepage decoupled from the sandbox was considered
  and rejected: it is a second serving path, a second identity, and a second staleness model for
  bytes the sites stack already serves.
- **Structured homepage content.** No widget schema, no portal-rendered markdown homepage; the
  page is the agent's own site, whole.
- **Liveness guarantees on single-shape carriers.** Docker/local sandboxes may be reclaimed; the
  frame's 503 is the honest dev-rig answer.
- **Scheduled homepage refresh.** Staleness is cured by asking; a refresh cadence is a member's
  scheduled task if they want one.
- **Homepages for subagent profiles.**

## Proof

| Surface | Tests |
|---|---|
| `set_homepage` | binds, rebinds (old row cleared, one homepage per agent), refuses a dangling name, binds on a speakerless turn; unhost clears the binding |
| homepage read | `none`, `set` carrying exactly `{state, url}`, out-of-audience 404; sqlite + postgres |
| seed job | one turn + marker per agent; second tick admits nothing; the seeded conversation is workspace-audience and agent-held; on-behalf resolution (owner, else earliest-seated admin) |
| portal | index lists agents only; `#/agents` shows the main agent's Home; Home tab renders the iframe from a stubbed read and the absent state without one; tab strip and hash round-trip; graph node click routes; drawer and subagent tests rewritten or deleted with their views |
| copy/theme | new token declared in `theme.css`; `theme.test.tsx` and `gates.py` hold as-is; fresh worktree needs `npm run build` before theme tests |

`spec.md` (surfaces, agents screen) updates in the same commits as the changes it describes.

## Units

Four units, three of them parallel; landing order is the dependency order.

| Unit | Contents | Depends on |
|---|---|---|
| A — binding | sites migration (`homepage_agent_id` + partial unique index), `set_homepage`, `homepage_agent` list field, tests | — |
| B — read + seed | `GET agents/{agent_id}/homepage`, the seed job + `SEED_PROMPT`, the `ExtensionContext` roster accessor, tests | A (the list field) |
| C — portal revamp | three-column agents page, Home tab, uniform tab headers, graph placement, subagent tear-out (views, routes, and the three backend `subagents/*` reads together), tests | B's payload shape (stubbed until B lands) |
| D — rename | Overview → Settings on every end, alone | — |

A, C, and D develop in parallel; B follows A; D lands first so C rebases over the rename once.
