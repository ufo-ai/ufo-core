---
rfc: 0013
title: "Workspace resources — governed CRUD, revisions, settings, pack agents, agent messaging"
status: withdrawn
date: 2026-07-13
superseded_by: 0017
---

# Workspace resources — governed CRUD, revisions, settings, pack agents, agent messaging

> **Withdrawn 2026-07-16, superseded by [RFC 0017](../0017-workspace-objects.md)**: the CRUD
> substrate lands alone as workspace objects; governance, revisions, settings, packs, messaging,
> and self-improvement layer on later over that surface. The analysis below stands as the record
> of what each layer wants and why.

> Make everything a workspace configures — agents, per-extension settings, workspace-authored
> skills — one family of **governed resources**: typed rows as the live truth, one append-only
> revision journal, one proposal/approval path driven in chat, two introspection builtins, one
> export. The same machinery carries member edits, pack installs, system upgrades, agent
> self-improvement, and (via a new `message_agent` builtin) persistent agents addressing each
> other. Extends `spec.md` §Workspace model, §Extension system, §Packs; supersedes the
> prompt-only governance in `core/src/ufo/runtime/kinds/governance.py`.

## Evidence (why this shape)

Four inputs, audited 2026-07-13:

| Source | Finding that binds this design |
|---|---|
| `~/src/metalcraft` audit | Agent-as-CRD was complete (7 kinds, RBAC dual-principal, kubectl-in-sandbox self-CRUD behind a create-only namespace-pinned token) — yet held **no durable change history** (etcd `resourceVersion` is ephemeral; git only for git-mode tenants; `promotion_record` stored digests, not content), **no audit of agent self-edits**, an accept-only `product_audit` with zero readers, a five-scheme skill-catalog resolver with zero runtime callers, spec-only export, and one behavior knob. k8s bought authority and schema; none of the capabilities this RFC wants came from it. Its two exportable lessons: the **explorability mechanism** (generic verbs over self-describing schemas, live status beside spec, in-band to the agent) and the **subagent split** (durable identity = a full Agent resource; invocation kind = a thin compiled-in table). Its crown jewel — the Refinement statistical promotion gate — ports as §9. |
| Hosted-product landscape (Bedrock AgentCore, Azure Foundry, Agentforce, ServiceNow, Glean) | All converged independently on: agent = DB record → every save an immutable version → gated promotion → audit recording the **agent identity separately from the authorizing human**. None sells the tenant's agent as a CRD; k8s-lite reconcilers are rebuilt-hard-parts with no payoff below dozens-of-agents-×-teams-×-envs. |
| OSS field (Hermes, OpenClaw, Letta, Claude Code, GBrain) | Governance of **self-modification** is the differentiator: Hermes ships write-approval off with no history of self-edits (the named complaints of the YC teams ufo targets); OpenClaw's post-ClawHavoc answer is pending proposals + digest pinning. Letta's `.af` sets the export bar: secrets nulled by construction. Credential→agent scoping is the least-converged piece industry-wide — ufo's grants are ahead; nothing here dilutes them. |
| Founders' design session, 2026-07-13 | The product target is **durable operational units**: created/updated/deleted in chat, waking on cron or event, bounded capabilities, the system holding read+write over their prompt/trajectories/capabilities — "no difference between that and the ufo agent itself… a generalization of self-editable agents visible to the system." Upgrades must never break workspace customizations (the Hermes "upgrades break the mods" complaint; the VS Code rule: extensible, but you can't break the editor). Sequencing: **eliciting guidance precedes autonomous self-improvement**. The member's main agent is the **fleet commander** over the units. |

## Current state

| Concern | Today | Gap |
|---|---|---|
| Agent definition | `agent(id, workspace_id, name, prompt, model)` — `core/src/ufo/schema/tables.py:57-68`; created only by `ufoctl init` (`core/src/ufo/onboard/onboarding.py:150-160`); runtime record `Agent{prompt, model}` (`core/src/ufo/schema/records.py:138`) | Not the spec's promised bundle; no CRUD after init; one agent per workspace in practice |
| Governance | `propose_change`/`approve_proposal` CAS on `prompt_digest` (`core/src/ufo/runtime/kinds/governance.py:28-115`); `proposal` table (`tables.py:219-234`); `ctx.propose_change` (`core/src/ufo/runtime/ext/context.py:441-447`) | Prompt-only (`AgentChange`, `records.py:206`); approval is a bespoke HTTP endpoint (`core/src/ufo/runtime/surfaces/cli.py:146-164`) — a member action outside chat |
| History / audit | `proposal` rows, `grant` audit fields (`tables.py:200-217`), `updated_at` | No revision history of anything; no answer to "what changed, who approved, what did they see" |
| Behavior config | `ufo.toml` frozen at boot (`core/src/ufo/config.py`); `ext_store` per-extension KV (`tables.py:275`) | No typed, explorable, governed runtime config; Slack behavior has no knobs |
| Subagents | Static `SubagentRegistry` from manifests (`core/src/ufo/runtime/subagents.py:58`; profile shape `core/src/ufo/runtime/ext/manifest.py:447-464`); child reuses parent `agent_id` (`subagents.py:307`); named profiles only (`builtins.py:180-191`) | No durable, member-authored agent identities; no runtime authoring path |
| Agent-to-agent | `ctx.invoke(agent, …)` for jobs (`core/src/ufo/runtime/surfaces/admission.py:488-510`) | No peer messaging between persistent agents |
| Packs | Extensions + pack skills + onboarding (`spec.md` §Packs) | Packs cannot ship agent definitions |
| Export | `ufoctl bundle` (deploy artifact only, `core/src/ufo/bundle.py`) | No workspace definition export/import |

## Proposal

### 1. The resource model — typed rows, one registry, no generic table

A **resource** is a workspace-scoped row whose spec is a `BaseModel`, addressed as `kind/name`
(`agent/assistant`, `setting/slack`, `skill/founder-updates`). Names obey one grammar, enforced
at every create and backfilled onto today's unrestricted `agent.name`: lowercase alphanumerics
with single `-`/`_` separators, no `/`, no `--` (reserved as the derived-ref joiner), bounded
length. Manifest and pack names obey the same grammar at registration — they appear as ref
segments (`setting/<extension>`, `extension/<name>`, `pack/<name>`). Live truth stays in typed
tables; the uniform layer is governance, not storage.

```python
@dataclass(frozen=True)
class ResourceKind:
    name: str
    spec_model: type[BaseModel] | Callable[[str], type[BaseModel]]  # one model, or per-instance
    status: Callable[...] | None   # optional live state rendered beside the spec
    exportable: bool = False       # rows + referenced blobs travel in workspace export (§10)
```

`spec_model` is per-instance for exactly one kind: core's `setting` resolves
`setting/<extension>` to that extension's declared settings model, so `view=schema` and
propose-time validation stay exact. References between resources are typed — a spec field naming
another resource is the SDK's `Ref[kind]` — so `Governance` extracts the dependency graph from
the model itself; the §4 dependency and dependent checks need no per-kind extractor.

Core registers five kinds over their own typed tables:

| Kind | Spec | Notes |
|---|---|---|
| `agent` | §2 | the durable unit |
| `setting` | the extension's declared model, sparse (§5) | per-instance schema |
| `peer` | from-agent → to-agent (§8) | ref derived from endpoints |
| `spend_cap` | scope, subject, dimension, window, action | the existing caps table brought under governance; ref derived: `spend_cap/<scope>--<subject>--<window>` |
| `member` | `{role}` | owners promote a backup approver in chat; the apply transaction holds **at least one live owner**, so a workspace cannot govern itself into lockout |

Composite identities derive their refs; only authored kinds choose names. Subjects in specs are
**symbolic, never row UUIDs**: an agent-scoped cap holds `Ref[agent]`, a member-scoped cap the
member's email (held exactly in the spec; canonicalized into the grammar with a short digest
suffix for the ref segment). Apply resolves the symbol to the live row id and stores it beside
the spec, so admission's spend evaluator stays a UUID compare on the hot path, while the `Ref`
keeps the spec exportable and makes deleting a capped agent refuse like any other dependent (§4).

The Manifest point `resources` lets an extension declare kinds. Kind names share one global
namespace that pre-registers the read-only kinds (`grant`, `credential`, `extension`, `pack`,
`proposal` — §6) so no extension can shadow an audit view; boot fails loud on collision.
Registration requires of every spec model: `extra="forbid"`; full JSON round-trippability
(`arbitrary_types_allowed` rejected — specs are stored, hashed, journaled, and exported as
canonical JSON); and **no secret-bearing fields** — a credential is referenced only by slot (the
SDK's `CredentialSlotRef`), `SecretStr` rejected. These registration gates are what make the
install-time validation (§5) and "secrets never enter journal, proposal, or export" hold by
construction for extension kinds.

Extension-kind specs live in one **core-owned** table —
`resource(workspace_id, extension, kind, name, spec, seed, deleted_at)` — written only by
`Governance`: validate against the declared model → CAS on the spec digest (sha256 of canonical
JSON, one function, replacing `prompt_digest`) → write → revision, one transaction. Extensions
read their kinds through a typed accessor (`ctx.resources(kind)` yields model instances) and
have no write path: the same unit narrows today's raw `ExtensionContext.transaction()`
(`core/src/ufo/runtime/ext/context.py:387-397`, a whole-database connection) to the extension's own
operational tables. Both-ends probe: the sample extension declares a kind, drives
create/propose/approve/history through public surfaces, and asserts no extension-side write path
exists.

Two extension kinds land with this RFC:

**`skill`** (a `workspace_skills` extension, bundled by the flagship packs): spec = description +
content files — text only (§4's review rule), digests in the spec, bytes in the blob store, and
no `name` field: the ref is the identity, and SKILL.md's declared name must equal it, checked at
propose. Its `runtime_skills` handler merges workspace skills into the per-turn skill index
(`core/src/ufo/host/ext/loader.py:368`), so `load_skill` mounts them like any shipped skill —
agent-authored procedural memory becomes governed: a skill edit is a proposal, not a file write.
Content stages content-addressed at propose time; an unapplied digest is inert (`load_skill`
mounts only digests reachable from an applied spec), and the orphan reaper ships with the
staging path (unit 4), reclaiming blobs referenced by no applied spec and no pending proposal.

**`automation`** (the scheduled-tasks extension): spec = target agent, report conversation, wake
condition (`cron` expression or a `page_change` filter over synced sources), directive prompt.
The report conversation defaults to the creating conversation; a pack-seeded automation leaves
it unset, its runs landing in the automation's own ref-keyed conversation until re-pointed in
chat. The field is **workspace-local binding, not portable definition** — the exporter nulls it
like credential values (§10), so the symbolic-subjects rule holds. Due-ness is not spec: runtime
state (`next_run_at`, last fire, in-flight claim) lives in the extension's operational table
keyed by the automation's **row id — its incarnation** — so firing never writes the governed row
and a recreated automation inherits neither claims nor conversation. The kind declares
`exportable=True`. The extension's job machinery reads due automations through the accessor
(batch-at-interval) and fires `ctx.invoke(agent_id, directive, …)`. This is the "durable
operational unit": agent row (identity, prompt, capabilities) + automation rows (when it
wakes) — "competitor tracker at 9am watching this folder" is two governed creates, no new core
machinery, no bespoke tasks API.

### 2. The agent row — the full bundle, and subagent unification

`agent` becomes the spec's promised bundle:

| Column | Type | Meaning |
|---|---|---|
| `name` | existing | the ref's identity: set at create, **immutable** — a rename is delete + create, so refs, history, peer edges, and exports never dangle; like `seed`, it sits beside the governed spec, not in it |
| `prompt`, `model` | existing | unchanged semantics |
| `description` | str | one line for `config_get` listings and the spawn index |
| `tools` | JSON list \| NULL | tool-name allowlist. NULL is a declared policy: the workspace's full tool surface, growing as extensions install — growth journaled as `extension/<name>` revisions (§7), which is where "when did this agent gain tool X" is answered. Pack seeds name explicit lists |
| `enabled` | bool | admission refuses turns for a disabled agent — triggers stop firing, messages are refused; the fleet's pause/kill verb, itself a governed one-field change |
| `seed` | str \| NULL | `pack:<name>@<digest>` — provenance beside the spec, written only by system actors, excluded from the spec model and its CAS digest (an untouched seeded row still digest-matches its seed); drives §7 upgrades |

Validation is loud at both entrances to the one spawn namespace: a proposed spec naming an
unregistered tool, or an agent name shadowing a manifest profile, is rejected at propose; `ext
install` / pack activation refuses a new profile colliding with an existing row. Drift after the
fact (an extension uninstalled from under a `tools` entry) does not brick the agent: turns run
on the available intersection and `config_get` status names the missing entries as degraded.

Two candidate columns fail the hard-to-vary test (§Doctrine fit) and are excluded: **memory
scoping** (its only consumer is the memory extension — it ships as that extension's own
per-agent resource or setting, never core schema carrying an extension's knob) and a **skills
allowlist** (no consumer yet; if one lands it must use `Ref[skill]` fields, so deleting a
referenced skill refuses and a shipped-skill rename degrades visibly rather than breaking
silently). `tools` stays: capability confinement is core's job.

Subagents keep **two representations, deliberately** (the metalcraft split, mirrored by Claude
Code's `.claude/agents/*.md` and ufo's own manifests):

| Representation | What it is | Versioned by |
|---|---|---|
| Manifest `SubagentProfile` | Typed invocation kind — code (in/out `BaseModel`s, tool subset, round budget) | The extension's lockfile digest |
| `agent` row, invocable via a `peer` edge | Durable identity — data, member- or pack-authored | The revision journal |

`spawn_subagent` resolves its `profile` argument against manifest profiles ∪ agent rows the
caller holds a `peer` edge to (§8). A row-spawned child runs as **its own** `agent_id` — its own
prompt/model/tools, its own grants, its own ledger attribution — with payload `{message: str}`,
output = final text, the subagent round budget. Manifest-profile spawns run under the parent's
identity and need no edge. A "thread-scoped agent" needs no third representation: a definition
that matters becomes a row; one that doesn't is just the spawn payload.

Crossing capability boundaries is **granted, not flagged**: wiring agent A to spawn or message
agent B is a governed `peer` edge (§8). A boolean on the target could not say *who* may drive it.

### 3. The revision journal

One append-only table, written in the same transaction as every governed apply — a write without
its revision is unrepresentable:

```
revision(id, workspace_id, ref, row_id,         -- ref = "kind/name"; id is a monotonic DB
         actor_member_id | actor_agent_id | actor_system,   -- sequence (BIGINT identity, both
         conversation_id?, proposal_id?,                    -- dialects), never a UUID — history
         spec, audit_context?,                  -- order and adjacent-snapshot diffs depend on it
         created_at)                            -- spec: full snapshot; NULL = delete
```

`row_id` is the **incarnation**: a ref reused after a tombstone (§4) starts a fresh row, and
history keeps the lineages distinct — `ufoctl history agent/foo` shows both in order, each diff
computed within its incarnation, the dead row still addressable by id. `audit_context`
(nullable, kind-specific) snapshots what a digest alone cannot reconstruct: for a `setting`
change, the effective values at apply, kept separate from `spec` (the sparse overrides) so
rollback re-proposes overrides — never copied defaults, which §5 rejects.

No digest or sequence columns: CAS digests live on the proposal, and a diff is two adjacent
snapshots, derived on read.

- **Dual identity** — a revision applied from a proposal copies its proposer triple (§4); the
  approving member sits on the proposal. "The agent changed this" and "Alex authorized it" are
  separately queryable, for pending and rejected proposals too.
  `actor_system ∈ {pack_seed, upgrade, import, operator, extension:<name>}` covers writes and
  proposals with no member or agent behind them, keeping authorship when a background extension
  proposes.
- **Full snapshots, not diffs** (the product-platform convergence): rollback is
  `config_propose(ref, spec_at_revision_N)` — no special mechanism.
- **Secrets never enter it.** Grant create/revoke journals its row snapshot (account ids only —
  grants hold no tokens). Credential fill/rotate journals the event and slot name: no value, no
  value digest (a digest of a low-entropy secret invites offline guessing).
- **Both ends in the same unit**: `ufoctl history <ref>` and the `config_get` history view (§6)
  are the readers — metalcraft's accept-only, zero-reader `product_audit` is the named
  anti-pattern. The history view merges revisions with proposal lifecycle rows, so "what was
  refused" is as queryable as "what was applied".

### 4. Proposals — the one write path

`proposal` generalizes from prompt-only to any resource and becomes the **only** write path for
member- and model-authored change; operator/system acts (pack seed, untouched-seed upgrade,
import) journal directly with their `actor_system`, gated by the operator boundary, not chat.

| Column | Change |
|---|---|
| `ref` | replaces `agent_id` — any `kind/name`; a new name = create; `spec = NULL` = delete |
| `spec` | replaces `body` — the full proposed spec |
| `rationale` | proposer's stated reason, rendered in the approval question, kept for audit |
| actor triple + `conversation_id` | the proposer — the same exactly-one shape as `revision` (§3), replacing `extension`; rejected proposals stay attributable, and the conversation is where the request surfaces |
| `from_digest`, `to_digest`, `status` | as today (`tables.py:219-234`); `from_digest` is the `base_digest` the proposer passes from its own `config_get` read (§6) — staleness fails at creation, CAS re-checks at approval (`governance.py:70-115`), now over the whole spec |
| `proposal_dependency(proposal_id, ref, depends_on_proposal_id?, reviewed_digest?)` | one row per referenced `Ref`: a pending ref pins the proposal it was reviewed with (a `peer` edge between two new agents pins both); an applied ref pins the digest reviewed, so an edge to a low-privilege agent stale-fails if that agent's capabilities changed between rendering and click. Rejecting or replacing any pinned dependency supersedes the dependent |
| `decided_by`, `decided_at`, `decided_in_conversation_id`, `decision_basis` | generalizes `approved_by`: rejections carry their decider; a proposal decided away from its origin conversation records where the diff was shown. `decided_by` is member \| system — a member's signed click, or `operator` for the `ufoctl` twin; an agent can never occupy it. `decision_basis` (nullable) records the delegation revision a system decision acted under (§9) |

Approver context — what the approver saw — is durable by construction, not by surface behavior:
the engine writes the rendered proposal (diff + rationale) into the conversation transcript as a
system message in the same commit as the terminal frame, and the receipt follows the decision;
the specs it was rendered from are the proposal row itself. Content-bearing specs render their
staged text **in full** — a spec exceeding the content bound is refused at propose (bound every
payload), never truncated past a reviewable cap — and binary files are refused in
workspace-skill content: an unreviewable executable cannot ride a digest into what `load_skill`
mounts; binaries belong in the git-reviewed shipped layer.

Approval semantics:

- **No write applies in the turn that proposes it.** `config_propose` always creates a pending
  proposal; application happens only through the signed interaction (or the operator verb). The
  model authors specs and never applies them — an owner-speaker turn carrying untrusted content
  cannot be steered into a silent self-edit; the injected call only stages a diff.
- **Owner friction stays one click.** Every proposal rides its turn's terminal frame as a
  `ProposalRequest` — the fourth terminal request kind beside `AskUserInput` /
  `CredentialRequest` / `ConnectRequest` (RFC 0012) — and the engine accumulates every proposal
  created during the turn, not just a final act (unlike today's single last-tool request,
  `core/src/ufo/runtime/engine.py:329-345`). Surfaces render their own affordance (Slack buttons;
  CLI/web prompt); the signed interaction resolves the acting member, applies the ref's decision
  rule — `owner` everywhere except `grant/<id>` deletes, where the grantor may also decide
  (§6) — and calls `Governance.approve/reject` directly: **the model is never in the approval
  loop**. `ufoctl proposals list|approve|reject` is the operator twin.
- **Reference integrity holds at both boundaries.** A proposal may reference applied refs or
  refs pending in the same conversation's earlier proposals — a new agent and its `peer` edge
  propose together — with `proposal_dependency` pinning exactly what was reviewed (table above);
  approving a dependent before its dependency refuses, naming it. A delete (`spec = NULL`) with
  live dependents — peer edges, automations targeting the agent, connector grants — is refused
  at propose, naming each; turns and ledger rows are history, never blockers. A delete is a
  **tombstone**: `deleted_at` excludes the row from listings, spawn, and admission while
  `turn.agent_id`, ledger rows, and the journal keep their referent forever; unique-name
  constraints are partial over live rows, so recreation mints a fresh incarnation (§3). The full
  propose-time validation set — references, dependents, name and spawn-namespace collisions —
  re-runs inside the approval transaction, refusing with what changed. Applying a delete
  supersedes every pending proposal on the ref and every pending proposal referencing it through
  the `Ref` graph, so no stale approval survives recreation.
- **Delivery follows the proposal's conversation.** An automation or job turn was admitted on a
  conversation whose durable writeback carries the `ProposalRequest` like any terminal frame. A
  proposal with no member-visible conversation (an extension cron) rides the terminal frame of
  the next owner-speaker turn, oldest first, bounded; every pending proposal is listable via the
  read-only `proposal` kind (§6). No proposal can exist that no owner can find.
- **The same affordance is the suggestion UX**: "want me to do this whenever an investor email
  arrives?" is the agent proposing an `automation` — one button, and the click *is* the approval.
  Growth surface and governance surface are one mechanism.
- **A pending proposal can be tested before approval**: the §9 replay harness runs the proposed
  spec against the workspace's own recent transcripts and reports the comparison in the
  conversation — no new write surface. Offered only for prompt-shaped specs: a
  capability-changing proposal cannot be meaningfully scored against archived tool results (§9),
  so it shows no test affordance rather than a misleading one. The same replay serves upgrade
  proposals (§7).
- The HTTP approval endpoint (`surfaces/cli.py:146-164`) is deleted. `ctx.propose_change` keeps
  its name with the generalized signature (`ref`, `spec`, `rationale`, `base_digest`,
  `content`): the SDK path presents the digest it read exactly as the tool does, and `content`
  (host-side bytes) is the SDK twin of the tool's file staging (§6), so an optimizer-authored
  `skill` stages its blobs through the same governed call.

### 5. Settings — the explorable behavior config

New Manifest point: `settings: type[BaseModel] | None`. The model must construct with no
arguments (every field defaults) and passes §1's registration gates (`extra="forbid"` means a
stale override key fails validation rather than vanishing). Each field's `Field(description=…)`
is its documentation, surfaced by the schema view (§6) — enforce-don't-document, applied to
knobs.

- **Storage**: `setting(workspace_id, extension, overrides JSON)` — sparse overrides, never full
  snapshots. Effective settings = model defaults + overrides, so an untouched knob follows the
  shipped default across upgrades: the settings half of the layering rule (§7). The journal
  snapshots overrides as `spec` and effective values as `audit_context` (§3). `config_get`
  returns effective values with the overridden subset marked; a propose carries only overrides,
  and the copied-defaults shape (the full effective model submitted back) is rejected at the
  propose boundary — it would silently pin every default.
- **Pin-aware staleness**: a proposal's `base_digest` for any extension-provided ref (`setting`,
  `skill`, `automation`) incorporates the declaring extension's pinned digest — a pending
  proposal reviewed under one schema's semantics or defaults must not apply under another's just
  because the JSON still validates.
- **Runtime**: `ctx.settings` returns the extension's own model instance, resolved once per turn
  like agent config (`queue.py:355-386`) — frozen, typed, no live-reload machinery.
- **Upgrade safety at the boundary**: `ufoctl ext install <new>` validates every workspace's
  stored overrides and every stored `resource` row of the extension's declared kinds against the
  new models before switching the pin, and refuses an upgrade that drops or renames a kind still
  holding rows — the operator migrates or deletes first. Incompatibility blocks the install
  naming extension, kind, and field; boot re-validates as defense.
- **First consumer, same unit**: the Slack extension declares `SlackSettings` (ack reaction,
  reply verbosity, thread broadcast, accounting-block rendering). "Make Slack replies terser" →
  the agent reads the schema, proposes an override, the owner clicks, the next reply is terse.

`ufo.toml` is untouched: the operator's boot-frozen *deploy* config (infra, keys, pins).
Workspace resources are the runtime-mutable *behavior* layer. Two files, two questions, two
audiences; neither mirrors the other.

### 6. Introspection — two builtins, kubectl-shaped without k8s

The property to replicate from metalcraft's kubectl surface is not kubectl: one generic verb set
over self-describing schemas, live status beside spec, in-band to the agent's normal tooling —
so a new kind or settings model is explorable with zero new tools. Two builtins (they operate
core-owned authority machinery — speaker gating, CAS, journal — which must not be
re-implementable per extension):

| Tool | Contract |
|---|---|
| `config_get` | Read anything. Read-only kinds appear beside the governed ones: `grant` (the audit view), `credential` (slot names, values never), `extension` (pins + versions), `pack`, `proposal` (pending + decided). |
| `config_propose` | The one mutation verb (§4); always pends, the reply carries the affordance. `files` is the content channel: core resolves each path and refuses symlinks or any escape from the conversation workspace before reading a byte, then streams the files content-addressed into the blob store and checks the spec's digests are covered — one verb, no companion upload path. |

The agent-facing API, exactly as the typed registry renders it into the model's tool schema
(field descriptions included):

```python
class ConfigGetInput(BaseModel):
    ref: str = ""      # "" lists the kinds; "agent" lists that kind's instances; "agent/reviewer" addresses one
    view: Literal["spec", "schema", "status", "history"] = "spec"

class ConfigProposeInput(BaseModel):
    ref: str                      # existing ref = update; new name = create; grammar-checked (§1)
    spec: JsonDict | None         # full proposed spec; None = delete (tombstone, §4)
    rationale: str                # rendered in the approval question, kept for audit
    base_digest: str              # from the config_get read this was built on; ABSENT sentinel for a create
    files: tuple[str, ...] = ()   # workspace-relative paths, content-bearing kinds only

class MessageAgentInput(BaseModel):
    agent: str                    # target agent name; the caller must hold a peer edge to it (§8)
    message: str
```

`spec` crosses the tool wire as JSON and is validated into the kind's `spec_model` before
anything else reads it — the boundary type is the model (§1). `spawn_subagent` is unchanged
except that `profile` also accepts an agent name the caller holds a `peer` edge to; a row
spawn's payload is `{message: str}` and its output the child's final text (§2).

| Call | Returns |
|---|---|
| `config_get ""` | `[{kind, description, instance_count}]` |
| `config_get "agent"` | `[{ref, description, enabled}]` — one line per live instance |
| `view=spec` | `{ref, spec, digest, seed?, live \| deleted}` — `digest` is the `base_digest` a subsequent propose must present |
| `view=schema` | the kind's JSON schema with per-field docs (the `kubectl explain` of rows) |
| `view=status` | kind-specific live state — an agent's grants, spend rollup, degraded tool refs |
| `view=history` | ordered revisions merged with proposal lifecycle: actor, decision, `row_id` incarnation, rejections included |
| `config_propose` | `{proposal_id, ref, from_digest, to_digest, status: "pending"}` — **never** `"applied"`: application is the member's click, in a later interaction (§4) |
| `message_agent` | `{turn_id, conversation_id}` — asynchronous; no reply is implied, and none arrives unless the peer chooses to message back |

Every failure is terminal for the call and names its cause — this is also how §4's propose-time
validation reports:

| Error | Raised when |
|---|---|
| `unknown_ref` / `invalid_name` | kind unregistered, or name violates the §1 grammar |
| `validation_failed(field, reason)` | spec fails the kind's model — unknown keys, wrong types, the copied-defaults settings shape (§5) |
| `stale_base(current_digest)` | `base_digest` ≠ the live digest; or the ref exists and ABSENT was passed |
| `missing_reference(ref)` | a `Ref` field names neither an applied row nor a pending proposal in this conversation |
| `has_dependents(refs…)` | delete while peer edges, automations, or grants still reference the row (§4) |
| `name_collision(profile)` | an agent name would shadow a manifest profile (§2) |
| `content_refused(reason)` | text over the content bound, a binary file in skill content, or a path escaping the workspace |
| `no_peer_edge(from, to)` | message or row-spawn without the required edge (§8) |
| `depth_exceeded(limit)` | `CAUSATION_DEPTH_LIMIT` reached at admission (§8) |
| `agent_disabled(ref)` | the target's `enabled` is false (§2) |

**Connector-grant creation** stays outside `config_propose`: connecting an account is its own
chat flow with a private handoff (RFC 0012) — a third party and a secret are involved.
Revocation is the governed delete, `config_propose(grant/<id>, spec=NULL)`, decided by **the
grantor or an owner** (the member who gated the granting act can withdraw it), tombstoned like
every governed delete so the revoked grant stays visible in status and history with its
revocation attributed. **Peer edges have no third party and no secret, so no special flow**:
ordinary `peer` resources, created and revoked through `config_propose`.

### 7. System changes vs user changes — the layering rule

Three layers, composed — never merged:

| Layer | Contents | History | Change path |
|---|---|---|---|
| **Shipped** | Extension/pack code: prompt sections, skills, subagent profiles, tool defs, settings *defaults*, resource *seeds* | Lockfile pin (git upstream; RFC 0007) | `ufoctl ext install` / bundle; a pin change appends an `extension/<name>` revision — upgrades land in the same audit stream as everything else |
| **Workspace** | `agent` rows, `setting` overrides, `skill`/`automation` rows, `peer` edges, spend caps, grants | Revision journal | Proposals in chat (grants: created via their own flow, revoked as governed deletes — §6) |
| **Composition** | System prompt = core shell + pack `prompt_sections` + `agent.prompt`; settings = defaults + overrides; skill index = shipped ∪ workspace rows | — | Slots and unions only; a workspace never edits shipped content, so no three-way text merge exists anywhere |

**Packs install units.** `Pack` gains `seeds: tuple[ResourceSeed, …]` — full specs as data for
any kind `config_propose` can create: agents, the automations that wake them, the peer edges
that wire them. Read-only kinds — `grant` above all — are **unseedable by construction**: a pack
can never mint connector authority; it lists needed grants as onboarding steps for a speaker to
complete. Each seed lands with `seed=pack:<name>@<digest>` provenance (a column on every
governed table) and an `actor_system=pack_seed` revision. Seeding rules:

- The seed set applies **atomically**: preflight-validate everything (grammar, collisions,
  references between seeds), then one transaction — a failure leaves nothing half-installed.
- A name collision with a member-authored row or another pack's row **fails activation loud**
  (one ref, one default owner). A `peer` seed's ref derives from its endpoints, so re-seeding an
  identical edge is idempotent for the seeding pack and a cross-pack collision otherwise.
- Re-activation at the **same** seed digest (the §10 import case) is a no-op. At a **different**
  digest it is an upgrade and runs the table below — a pack cannot show active at v2 while its
  untouched units run v1 defaults.

**Upgrade vs user edits** — the one place layers could collide, resolved by the seed digest:

| Row state at upgrade | Action |
|---|---|
| Current digest == recorded `seed` digest (never customized) | New seed auto-applies; revision `actor_system=upgrade` |
| Customized since seeding | The upgrade **opens a proposal** — old seed, new seed, and the customization are all in hand, so it may carry an agent-drafted reconciliation as its spec, replay-testable (§4); the owner approves the merge, keeps their version, or takes the new default. No silent overwrite (the Hermes failure), no blocked upgrade |
| Pack deactivated | Rows persist as workspace data; `config_get` status marks the origin pack inactive and tool refs degraded. An extension cannot leave the active set while its kinds hold rows, its `setting` overrides are stored, its declared credential slots are filled, or its kinds have pending proposals: deactivation is refused like a kind-dropping upgrade (§5) until rows are migrated or deleted, proposals decided, and secrets cleared — `ufoctl credential unset <slot>` and an owner-sealed chat deletion land with this rule, since a block without a clearing path is a trap. Export is a copy, not a removal |

The layering rule is the VS Code rule for agents: extensible everywhere, but a workspace change
can never patch the shipped layer — an upgrade never finds the core loop modified, and a broken
unit is repairable by the main agent in chat, because units are rows it can read, diff, and
re-propose.

Packs become **union-installable**: multiple packs active, manifest sets unioned — identical
extension pins dedupe so packs share base extensions; only conflicting pins or duplicate
contribution names fail loud — each installable from a git URL by RFC 0007's pin mechanics. A
pack is precisely "capabilities + seeded units": `ufoctl pack install <url>` → extensions
pinned, units seeded, everything journaled. The main agent stays the one conversational front —
the **fleet commander** — listing units via `config_get`, reaching them via `message_agent`,
stopping one by proposing `enabled=false`.

### 8. Agent messaging — `message_agent`

Persistent agents need a peer channel that is not parent-child delegation:

| | `spawn_subagent` | `message_agent(agent, message)` (new builtin) |
|---|---|---|
| Relationship | Parent-child; typed in/out; await or background | Peers; free text; always asynchronous |
| Identity | Profile spawns run as parent; row spawns as target (peer edge required) | Recipient runs as itself — own prompt, grants, ledger |
| Use | "Do this sub-task, give me the result" | "FYI / please handle / requesting input across identities" |

Mechanics:

- **Agent→agent invocation is a governed edge.** Sending (and row-spawning, §2) requires a
  `peer` edge from caller to target — a core kind with its own typed table
  (`peer(workspace_id, from_agent_id, to_agent_id, seed)`), never a row in the connector-shaped
  `grant` table. Its ref is canonical from its endpoints (`peer/<from>--<to>`): the pair is the
  identity — never typed, only composed from two validated names, which is why the grammar
  reserves `--`. As a resource kind it gets creation and revocation through `config_propose`'s
  signed click (an injected owner turn cannot silently wire a low-privilege agent to a
  high-privilege peer — the §4 rule, uniformly), journal history, pack seeding (§7), and export
  (§10). A flag on the target would have opened it to every agent the moment one legitimate peer
  needed it — the confused deputy; the edge names *who*, and ledger + journal attribute each use.
- **One durable conversation per pair** (`surface="agent"`, key = the unordered pair — the open
  surface namespace, as `"subagent"` today per `subagents.py:271-307`); the existing
  one-running-turn-per-conversation invariant serializes the exchange. **Rendering is
  reader-relative**: an agent-message turn records `sender_agent_id` beside the recipient's
  `agent_id` (the peer analog of `speaker_member_id`, RFC 0012), and pair-conversation
  transcript messages carry a nullable author agent id (today's flat role tuple cannot answer
  who said what) — so only the reading agent's own turns render as assistant history, and a
  peer's turns render as inbounds under the sender's `<context>` tag. Identities never bleed.
- **No speaker authority** (RFC 0012): a received message cannot approve proposals, grant, or
  fill credentials; its content is untrusted input. Replies are not automatic — responding is
  the recipient calling `message_agent` back, which needs its own edge in that direction. Every
  hop is a granted, metered, capped, journaled act.
- **Loop safety is admission's job.** `turn` gains `causation_depth` and `caused_by_turn_id` —
  depth bounds a chain, identity names it — on every caused admission: agent message, `invoke`,
  automation fire. Past `CAUSATION_DEPTH_LIMIT`, admission refuses and the causing call fails
  loud. `page_change` attribution rides per-change **event rows**, not the page row (a page
  mutated twice keeps both attributions): the folder source stamps the event's
  `committed_by_turn_id` when its root lies inside a conversation workspace, and the
  cursor-runner filters self-caused changes from every delivery — a mixed batch fires on only
  its external changes; an all-self batch fires nothing. Connector sources never stamp: external
  writes — including the agent's own connector mutations syncing back — are unattributable by
  construction and arrive as new roots. There the posture is layered, not promised: the broker
  execute path journals outbound mutations and the provider's sync runner suppresses matches
  best-effort; the approval diff warns when an automation's agent holds mutating tools for its
  own watched provider; the hard bounds stay depth, spend caps, and `enabled`. Only folder
  sources carry the guarantee — an external reply to the agent's email is new work, not a loop.
- **Members watch**: pair conversations are ordinary conversations — tailable live and readable
  by owner tooling.

The hard-to-vary parts are admission-side: depth enforcement, speakerless authority, the
peer-edge check. The tool wrapper and pair-conversation convention are softer — a base-pinned
extension over `ctx.invoke` could carry them once depth lands in admission; the builtin is
proposed for the authority checks' locality (Open decision 7).

### 9. Self-improvement — guidance first, gate second

Stays out of core (spec §Non-goals). Two tiers, sequenced deliberately — the product is at the
eliciting-guidance phase, and the machinery serves that before any optimizer.

**Tier 1 — guidance elicitation.** Repeated member feedback *is* the corpus: when the same
correction or manual ask recurs (mined from `ctx.trajectories.read`, no new events), the
extension proposes the config change or `automation` that encodes it — "I've flagged investor
emails to you three times; want me to draft the reply each time?" — surfaced as a one-click
`ProposalRequest`. A human with an opinion and an agent with a suggestion drive the same tools;
only the spec's author varies. No statistics: the member's acceptance is the evidence.

**Tier 2 — the gated optimizer.** The existing extension
(`extensions/self_improvement/…/cron.py:112`) upgrades from prompt candidates to full-bundle
changesets and imports metalcraft's proven gate (`~/src/metalcraft/src/metalcraft_improve/`):

1. **Corpus** — `ctx.trajectories.read` over flagged turns (errored tool rounds, member
   corrections); task classes deduped at `(class, from_digest)`.
2. **Candidates** — a revised agent spec (prompt and/or tools — the agent row carries no skills
   field, §2) or a separate `skill` spec, pinning a held-out eval set.
3. **Gate** — counterfactual replay of eval threads against both arms via `ctx.model` (model
   legs only, archived tool results fed back, nothing executed), LLM-judged, then the two-stage
   statistical test as shipped: per-class lift lower bound (Newcombe/MOVER-W) ≥ 0.05 with ≥ 4
   per arm **and** global non-inferiority, passed twice consecutively; a failure resets. The
   replay gates **prompt-shaped changes only** — a capability-changing candidate would be scored
   against the old capability's archived outputs and could pass while broken, so it goes to the
   owner as a plain tier-1 proposal. (No live pre-approval eval: it would run the unapproved
   capability against real connectors, a side-effect path around the approval boundary.)
4. **Promotion** — `ctx.propose_change(ref, spec, rationale=evidence, base_digest=the digest the
   candidate was mined against)`: a moved base fails at creation like any stale read. The
   proposal is the changeset, the chat approval the promotion, the journal the record. Rejected
   candidates are suppressed at `(ref, task class, from_digest, candidate digest)` in the
   extension's `ext_store` — one bad candidate must not block another class's improvement.
5. **Governance default** — the extension's settings declare `auto_approve: bool = False`, the
   inverse of Hermes' default. Turning it on is a **standing delegation, itself a signed setting
   change**, bounded: gate-passed, prompt-shaped candidates on the agent's own spec apply with
   `decided_by = system` and `decision_basis` = the delegating setting revision — never tools,
   skills, peers, grants, or another extension's settings. The model still applies nothing; a
   deterministic gate does, under an owner-signed, journaled, revocable rule — and revocable in
   fact: the system decision re-reads the delegating setting inside the apply transaction, so
   disabling `auto_approve` halts in-flight candidates immediately. The stored reference is
   provenance, never authority.

System-tier improvement — changing shipped prompts/skills across workspaces — is the operator's
loop: the top-level `evals/` package plus PRs to this repo, where git already governs code. The
workspace extension never writes the shipped layer.

### 10. Export / import

- `ufoctl workspace export` → one archive: `definition.yaml` (agent rows as full specs, setting
  overrides, `spend_cap` rows — an imported workspace comes back with its spend boundaries —
  `peer` edges as full definitions (pure workspace data, no external authority to fake),
  exportable extension kinds, connector grants **as references** (provider + account + agent: a
  re-grant checklist, never live authority), credential slot names with **null values** (the
  Letta rule), lockfile pins and active packs) plus `blobs/<digest>` for every content digest
  the specs reference — a workspace skill's files travel content-addressed and tamper-evident.
  Every exported row carries its `seed` provenance, so post-import pack upgrades still tell
  untouched from customized.
- `ufoctl workspace import` is **staged end-to-end**: pins and manifests load into a scratch
  universe (the kind universe must exist before any spec can validate), blobs verify,
  definitions validate **in topological order over the `Ref` graph** (an automation restores
  after its agent; archive order is irrelevant). Only a fully valid archive commits — lockfile
  write and `Governance` replay (`actor_system=import`) land together; failure changes nothing.
  Packs are marked active last (seeding no-ops over imported rows, §7). Onboarding steps are
  emitted for every grant and credential the export could not carry — re-granting happens in
  chat because the speaker gates the granting act; an import cannot impersonate one.
- The SOC2 artifact is not a new verb: `ufoctl history --json` at workspace width emits
  revisions + proposal lifecycle as JSONL — who/what/when, approver conversation, rejections
  included.

### 11. Schema and landing order

Migrations, each with its unit:

- `member` gains `role` (`owner` \| `member` — spec.md's declared role, absent from today's
  table (`tables.py:15-24`); backfill makes the founding member owner).
- `agent` gains the §2 columns; every deletable governed table (`agent`, `peer`, `spend_cap`,
  `resource`, `grant`) gains `deleted_at` (tombstones, §4; unique-name constraints go partial
  over live rows; a `setting` delete is revert-to-defaults, journaled, no tombstone).
- `proposal` gains `ref/spec/rationale`, the proposer triple, and the decision fields (§4); new
  `proposal_dependency`.
- New `revision` (monotonic id, `row_id`, `audit_context`), `resource`, `setting`, `peer` —
  governed tables carry `seed`.
- `turn` gains `causation_depth`, `caused_by_turn_id`, `sender_agent_id`; a new `page_change`
  event table carries per-change `committed_by_turn_id` (bounded retention); the transcript
  codec gains a nullable per-message author agent id (a blob-format version, unit 6).
- Extension pins stay in the lockfile — the boot-offline truth for what code runs (RFC 0007) —
  and the journal mirrors it: `ufoctl ext install` appends the `extension/<name>` revision in
  the same command, and boot reconciles any lockfile↔journal drift with a catch-up revision
  (`actor_system=operator`) before serving.

Landing order — each unit both-ends complete with its proof; rows for `docs/plan.md` when
accepted:

| # | Unit | Proof |
|---|---|---|
| 1 | `member.role` + `revision` + generalized `proposal` + `config_get/propose` builtins + `ProposalRequest` chat approval + grant/credential journal writers (`GrantStore.record`, `CredentialStore.put/rotate` gain same-transaction revision appends) + `ufoctl history/proposals`; HTTP approve deleted | Owner asks for a prompt change in Slack; the agent proposes via `config_propose`; the same reply carries diff + approve button; the click applies; journal shows agent proposer + member approver; stale `base_digest` fails at creation; a non-owner click is refused; a model turn cannot apply anything; connecting an account and rotating a credential each land a revision |
| 2 | Agent bundle columns + `peer`, `spend_cap`, `member` kinds + peer-edged rows in the spawn registry + full `config_get` views; `ufoctl spend-cap set` re-routes through governance (`actor_system=operator`; the direct writer in `cli.py` is deleted) | Member creates a reviewer agent and its `peer` edge (two clicks), spawns it; tool allowlist enforced; status shows a missing tool as degraded; an un-edged spawn is refused; owner tightens a cap in chat, journaled; owner promotes a second owner; the last owner's self-demotion is refused; deleting the reviewer tombstones it and its turns stay readable |
| 3 | `settings` manifest point + `setting` overlays + `SlackSettings` | "Make Slack replies terser" → proposal → one click → observably terser reply; `ext install` refuses a settings-incompatible version |
| 4 | `resources` point + core `resource` storage + `workspace_skills` (with the orphan-blob reaper) + the `automation` kind + `causation_depth`/`caused_by_turn_id`, `page_change` event rows, self-caused filtering — loop guards ship with the first event-fired consumer; sample extension declares a probe kind | Agent authors a skill via proposal; owner clicks; `load_skill` mounts it next turn; a rejected staging's blobs are reaped; "watch this folder daily at 9am" creates an automation whose fire invokes its agent; an automation writing its watched folder does not re-fire and a mixed batch delivers only the external change; the sample extension asserts no extension-side write path exists |
| 5 | Pack `seeds` + upgrade and deactivation semantics + extension-pin revisions | Upgrading a pack auto-applies to an untouched seeded agent, opens a proposal on a customized one, and carries a seeded automation through the same path; a `grant` seed is rejected at pack load; deactivating an extension with stored rows or overrides is refused naming them; all visible in history |
| 6 | `message_agent` over `peer` edges + the transcript author-id codec | Two edged agents converse, each reading the other's turns as inbounds, never its own assistant history; an un-edged sender is refused; depth cap refuses hop N+1 loudly; a received message asking the recipient to approve a proposal is refused — no speaker authority; ledger attributes per agent; a cron-born proposal reaches the owner on their next turn |
| 7 | `workspace export/import`; `ufoctl history --json` at workspace width | Export → fresh-deploy import → agents/settings/spend caps live, an authored skill's bytes travel and mount, a member-created automation fires on schedule (symbolic subjects remapped, never row UUIDs), and a peer edge round-trips — imported agents message and spawn each other without re-wiring; grants re-listed as onboarding; no secret in the artifact (asserted); a truncated archive imports nothing |
| 8 | Self-improvement gate port | A seeded bad-prompt workspace produces a gated, evidence-carrying proposal that improves the eval set; a no-lift candidate is rejected and suppressed |

## Doctrine fit / implications

Every part of this spec is classified. **Hard-to-vary** = changing it breaks the design's
explanatory structure, so it lives in core; everything else is deliberately easy-to-vary. Core
is five mechanisms — journal, proposals + `ProposalRequest`, the kind registry, the settings
overlay, depth-in-admission — plus columns on the agent row.

**Hard-to-vary (core invariants):**

| Invariant | Why it cannot live elsewhere |
|---|---|
| One write path: every member- or model-authored mutation is a proposal; operator/system acts journal with `actor_system`; nothing else writes | "Who approved" must be always-answerable; any second path makes the journal a partial record — the metalcraft failure restated |
| Same-transaction revision append: a write without its revision is unrepresentable | Only the one write boundary can enforce it; an extension cannot wrap other writers |
| Exactly-one actor (member \| agent \| system) on every revision | Dual-identity audit cannot be retrofitted onto rows written without it |
| CAS on the whole-spec digest | One concurrency and staleness story for every kind at once |
| A spec applies only via a signed member interaction or an operator/system act — never a model tool call, never in the turn that proposed it | The injection defense: an owner speaker in context does not let an injected model apply; terminal-request carriage is the loop's (RFC 0012) |
| The `ResourceKind` seam; extension-kind specs in core-owned storage with no extension write path | The extension system's own extensibility point — enforcement inside extension-owned tables would be advisory, since `transaction()` is a raw connection |
| Agent→agent invocation requires a governed `peer` edge | A flag on the target cannot say who may drive it; edge creation is itself click-gated (spec §Principles 4) |
| Secrets never in journal, proposal, or export — by construction of core serializers and §1's registration gates | A property, not a policy; must hold before the first consumer exists |
| Settings are sparse overlays on shipped defaults, validated at install time | This IS the upgrade semantics; it cannot differ per extension |
| Workspace changes never patch the shipped layer; `seed` three-way at upgrade | The no-silent-overwrite / no-blocked-upgrade guarantee |
| `causation_depth` refused in admission; received agent messages carry no speaker authority | Loop prevention and authority containment must be un-bypassable |

**Easy-to-vary (deliberately elsewhere, or left unfrozen):**

- Product opinion: the `skill` and `automation` kinds, every settings *schema*, both
  self-improvement tiers and the replay harness (spec §Non-goals), suggestion behavior, the
  fleet-commander pattern, wake vocabulary and any event bus, export format details, pack
  contents. System-tier improvement is operator `evals/` + repo PRs; distribution is the store +
  RFC 0007.
- Presentation: `config_get` view ergonomics and verb names — only the boundary is fixed
  (validation and apply happen exactly once, in core).
- Behavior: who drafts an upgrade reconciliation and how well; the proposal carrying an
  alternative spec is the mechanism.
- Excluded from the agent row (§2): memory scoping, skills allowlist. `tools` stays.
- `message_agent`'s hard parts are admission-side; the tool's placement is Open decision 7.

Connector-grant creation and credentials keep their existing flows — this RFC adds their journal
entries, the governed revoke (§6), and read views; peer edges are resources, not grant rows.

One shape holds: one write path, one history, one digest function, one mutation verb; settings
overlay defaults rather than mirroring them; `ufo.toml` and workspace resources answer different
questions and never overlap. Fail-loud at boundaries; drift-tolerant at runtime (degraded
status, never a silent fallback). `spec.md` (§Workspace model, §Extension system, §Packs, §Agent
loop tool list) updates in the same commits.

The product consequence is the anti-Hermes sentence made structural: any model-authored change —
self-edit, owner-asked edit, optimizer candidate — cannot apply without a signed member
interaction on a rendered diff, journaled with both identities; the one carve-out, §9's
`auto_approve`, is itself such an interaction (owner-signed, scope-bounded, re-checked at apply,
journaled, off by default). The buyer can read the whole trail in Slack or export it.

## Alternatives

- **Agent CRDs / k8s in core** — the audit's central fact: metalcraft had complete CRD machinery
  and still lacked history, self-edit audit, sourcing, and export; those were absent *beside*
  k8s, not delivered by it. k8s remains the enterprise wrapper (spec §Principles 3, §Non-goals).
- **k8s-lite (declared kinds + reconciler, no k8s)** — a reconcile loop needs something to drift
  from its declaration; here the DB *is* the truth, so the loop would reconcile nothing while
  costing leader election, drift handling, and schema versioning. Reconciliation earns its keep
  against external live infra: the enterprise rewriter seam, later.
- **One generic table for everything, core kinds included** — `agent` has FK consumers (grants,
  turns, conversations); typed tables keep those. Extension kinds *do* live in one core-owned
  table, but with the declared `spec_model` validating at every boundary — which is what the
  `dict[str, Any]` ban protects. Specs in extension-owned tables were rejected: governance there
  is advisory, since `transaction()` is a raw connection.
- **Files-in-git as the live store** — the OSS authoring convergence, but PR latency breaks
  approval-in-chat and per-workspace repos are infrastructure. Git governs the shipped layer;
  the export archive (§10) is the portable file.
- **Per-extension bespoke config tools** — tool sprawl growing with every extension; the
  metalcraft kubectl lesson says one generic verb set over self-describing schemas. Two tools,
  flat forever.
- **Messaging as an extension over `ctx.invoke`** — expressible in the happy path, but depth
  capping and speakerless admission would live in bypassable extension code, and pair
  conversations would be second bookkeeping. (The admission invariants land in core regardless;
  only the tool's placement stays open — decision 7.)

Cut on parsimony — proposed in drafting, removed because a goal survives without it:

| Cut | Why the goal survives |
|---|---|
| A third tool (`config_list`) | A bare-kind `ref` on `config_get` lists; two tools where three would do |
| `presented_digest` on `proposal` | The rendered diff and receipt are in the durable transcript the proposal links; approver context is a read, not a column |
| `seq` + digest columns on `revision` | History order is the id; CAS digests are the proposal's; a diff is two adjacent snapshots |
| Separate `origin` column | `seed = pack:<name>@<digest>` carries provenance and upgrade identity in one field |
| `ufoctl audit export` verb | `ufoctl history --json` at workspace width is the same reader |
| Evidence written onto proposals | Replay results reply in the conversation — no second write surface |
| `memory_scope`, `skills` columns | Extension concern / no consumer yet (§2) |
| `spawnable` / `addressable` booleans | A `peer` edge names *who* may drive an agent; a flag on the target cannot (confused deputy) |
| `peer` as a `grant`-table kind | The grant table is connector-shaped; a `peer` resource kind gets typed endpoints, click-gated creation, seeding, and export from machinery this RFC already builds |
| `list/get/apply` handlers on `ResourceKind` | Core-owned storage does all three uniformly; a handler surface would be governance-by-convention (bypassable via `transaction()`) |
| Owner-speaker auto-apply fast path | An injected model in an owner turn could apply silently; the affordance on the same reply keeps friction at one click while the model never applies |

## Open decisions

1. **Disclosure of specs to non-owner members** — `config_get agent/x` shows another agent's
   full prompt. Recommended: workspace-visible (the workspace is the team unit; grants and
   credentials are already the guarded layer). Open if a customer demands member-hidden prompts.
2. **`CAUSATION_DEPTH_LIMIT` value** — 8 proposed; with no auto-replies, `peer` edges gating
   every hop, self-caused page-change filtering, and spend caps, the limit is a backstop.
3. **Auto-approve scope** — per-workspace boolean (proposed) vs per-agent vs per-kind. Ships
   `False` regardless; bounds per §9.
4. **Export default for extension kinds** — `ResourceKind.exportable` is the declared contract
   (§1, decided); open is only the default (`False` proposed; core kinds always export).
5. **Pack install target** — union-installable packs are decided; open is whether a third-party
   pack's seeded units front through the main agent only (recommended first — one conversational
   identity, the fleet pattern) or may bring their own surface binding (`@ufo-yc` as a separate
   Slack presence), which costs a per-pack Slack app install.
6. **Automation wake vocabulary** — `cron` + `page_change` filters (proposed: the two seams that
   exist) vs a general internal event bus with interceptable handlers; the bus is deferred until
   a consumer exists the two seams cannot express.
7. **`message_agent` placement** — core builtin (proposed: the peer-edge and pair-conversation
   authority checks stay beside admission) vs a base-pinned extension over `ctx.invoke` once
   `causation_depth` lands in admission. The admission-side invariants are core either way.
