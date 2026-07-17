---
rfc: 0017
title: "Workspace objects — registered kinds, YAML CRUD in chat"
status: implemented
date: 2026-07-16
---

# Workspace objects — registered kinds, YAML CRUD in chat

> Give every durable thing a workspace configures — scheduled tasks, skills, sources, connectors,
> credentials,
> the agent — one address (`kind` + `name`), one authored form (a YAML manifest validated against
> the kind's declared model), and one tool surface (five generic verbs in chat). Extensions
> register kinds through a new Manifest point and are the code that runs on every create, update,
> and delete. Supersedes RFC 0013, which bundled this substrate with governance, revisions,
> settings, packs, messaging, and self-improvement; those layer on later (§Doctrine fit) — the
> CRUD substrate is what every one of them presupposed, and it lands alone.

## Why supersede 0013

RFC 0013 was seven programs in one design: typed resources, a revision journal, proposal/approval
governance, a settings overlay, pack seeding, agent messaging, and the self-improvement gate. Its
six implementation units live on branches, none merged. The product target that motivated it —
durable operational units created, inspected, and deleted in conversation — needs only the first
program. This RFC keeps that one, sized to land, and drops the rest from scope (not from the
roadmap): each dropped layer wraps this surface later without reshaping it, because every one of
them was defined *over* generic CRUD, never beside it.

The metalcraft evidence reads the same way with two years of hindsight compressed into one line:
what was worth exporting from agent-as-CRD was never k8s — it was the explorability mechanism.
Generic verbs over self-describing schemas, live status beside spec, in-band to the agent's
normal tooling, so a new kind is usable with zero new tools. metalcraft split that mechanism
across two surfaces (humans got kubectl-shaped `/apply`/`/get`/`/explain` commands; the model got
raw `kubectl` + YAML heredocs in the sandbox, taught by skills —
`~/src/metalcraft/src/metalcraft_k8s/control_plane_service.py:1-14`). This RFC collapses both
into one: the verbs are ordinary tools, the manifests are the same YAML the model already writes
fluently, and no apiserver, RBAC, or sandbox token minting is involved.

## Current state

Every durable-config surface today is a bespoke, gap-ridden island:

| Concern | Today | Gap |
|---|---|---|
| Scheduled tasks | `schedule_task` / `cancel_scheduled_task` / `list_scheduled_tasks` (`extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py:170-198`) over core's `ScheduleStore` (`core/src/ufo/scheduling.py:116`) | Three tools for one row family; no inspect-one verb |
| User skills | `save_custom_skill` reads an authored directory into the extension's `user_skill` table (`extensions/skill_create/ufo_ext_skill_create/manifest.py:120-135`) | **No list, no get, no delete** — a saved skill is invisible and immortal in chat |
| Sources | `sync_source` registers via progressive disclosure (`extensions/sources/ufo_ext_sources/tools.py:279`) | Create-only — **no list, no remove**; a wrong source syncs forever |
| Connector grants | `connect_account` → OAuth → `grant` row (`core/src/ufo/grants.py:251`, `tables.py:228-245`) | **No tool lists or revokes a grant at all** |
| Credentials | `request_credentials` seals BYOK values into the `credential` table via private handoff (`core/src/ufo/credentials.py:106`, `tables.py:218-226`) | **No tool shows which slots exist or are filled; no unset at all** |
| Agent | `Governance.propose_change`/`approve_proposal`, prompt-only CAS (`core/src/ufo/governance.py:28-115`); approval is an HTTP endpoint (`core/src/ufo/surfaces/cli.py:146-164`) | `model` is not updatable at all; approval is a member action outside chat — the thing `CLAUDE.md` names as forbidden |

Sixteen core builtins exist (`core/src/ufo/tools/builtins.py:689-870`); none is CRUD over durable
workspace config — all config CRUD lives in the per-extension islands above. `spec.md:95-99`
("two tools where one would do is a defect") indicts this shape at scale: every new configurable
extension mints two-to-three more tools, or — as connectors and skills show — ships without the
read and delete halves entirely. Nothing of RFC 0013 is on `main`: no `revision`, `resource`, or
settings migration exists (head is `0035`), and `config_propose`/`ResourceKind` appear nowhere.

## Proposal

### 1. The object model

An **object** is a workspace-scoped durable row addressed `kind` + `name`. Its authored form is
one YAML document with exactly three top-level keys:

```yaml
kind: scheduled_task
name: investor-digest
spec:
  schedule: "0 9 * * 1-5"
  prompt: Summarize new investor emails and post the digest here.
  description: Weekday-morning investor email digest
```

Names obey one grammar for every kind, enforced at every create:
`[a-z0-9](?:[a-z0-9-]*[a-z0-9])?`, at most 64 chars — the existing skill-slug rule
(`extensions/skill_create/ufo_ext_skill_create/store.py:28`) promoted to the whole surface. The
grammar deliberately admits derived, id-shaped names (`gmail-3f9a21c4`), so authored and
system-derived names share one rule (§5).

A kind is registered data plus handlers:

```python
@dataclass(frozen=True)
class ObjectKind:
    name: str                    # the YAML `kind:` — snake_case, singular
    description: str             # one line, shown by object_list(""); says in prose what
                                 # mutations the kind accepts and by whom
    guidance: str                # the model-facing how-to object_explain returns verbatim;
                                 # a kind replacing bespoke tools carries their tuned
                                 # descriptions ~verbatim here, so no instruction is lost
    spec_model: type[BaseModel]  # validates every authored spec
    store: ObjectStore           # the kind's handlers over its own tables

class ObjectStore(Protocol):
    async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage: ...
    async def get(self, ctx: ToolContext, name: str) -> BaseModel | None: ...
    async def status(self, ctx: ToolContext, name: str) -> JsonDict | None: ...
    async def apply(self, ctx: ToolContext, name: str, spec: BaseModel,
                    old: BaseModel | None) -> None: ...
    async def delete(self, ctx: ToolContext, name: str) -> None: ...
```

`ObjectPage` is `(rows: tuple[ObjectRow, ...], next_cursor: str | None)`; `ObjectRow` is
`(name, summary)` — one line per instance, never a full spec. `status` is kind-specific live
state rendered beside the spec on get (next fire time, last sync, grantor); it is rendered to the
model as YAML and read by no code, the one place a loose mapping is the honest type.

**Refusal lives in the handler, not a declaration.** There is no verb set and no owner flag on
`ObjectKind`: a kind that doesn't support a mutation raises from `apply`/`delete` with the domain
reason, and a kind that gates on role calls `ctx.speaker_is_owner()` in its own handler — the
gate `sync_source` already uses (`extensions/sources/ufo_ext_sources/tools.py:132`). Since kinds
own their storage, a declared verb set could enforce nothing the handler couldn't bypass in its
own code — it would be static metadata beside behavior, a second answer to "what does this kind
do" that drifts. The handler's exception is the single truth and carries the better message
("connecting an account involves a third party and a secret — use `connect_account`"). The SDK
ships `VerbNotSupported` and `OwnerRequired` so refusals render uniformly (§2's error table); a
read-only kind is simply one whose handlers refuse every mutation.

**Kinds keep their own storage.** `store` handlers run over the tables and stores that already
exist — `ScheduleStore`, `user_skill`, `source`, `grant`, `agent` — so this RFC adds **no new
table and migrates no data**. The handler *is* the trigger: core validates the envelope, the
grammar, and the spec against `spec_model`, then calls `apply(ctx, name, spec, old)` /
`delete(ctx, name)`, and the kind's code performs the write plus its side effects (compute
`next_run_at`, start a sync, revoke at the broker) in the same call. Operational state — claims,
cursors, error counters — stays in the kind's own columns and surfaces through `status`, never
through spec. RFC 0013 rejected kind handlers because its governance had to be unbypassable;
with governance out of scope the objection dissolves, and when governance returns it gates the
two mutation verbs *before* the handler runs — one seam, kinds untouched.

Registration is a new Manifest point, `objects: tuple[ObjectKind, ...]`
(`core/src/ufo/ext/manifest.py:519-553` gains the field; SDK types live in `ufo/sdk/objects.py`).
Boot fails loud on: a kind name collision (one global namespace, core's kinds pre-registered), a
`spec_model` without `extra="forbid"`, one that fails JSON round-tripping
(`arbitrary_types_allowed` rejected), or one carrying a typed secret field (`SecretStr`/
`SecretBytes` rejected, recursively through nested models) — specs are stored, rendered into
transcripts, and echoed by `object_get`, so no *declared* secret can enter one. The gate cannot
see a secret smuggled into a plain `str` field; that stays a kind-author defect first-party
review owns, exactly as it does for any tool argument today. Third-party extensions (RFC 0015)
cannot register kinds — `ObjectKind.store` is a live Python value; the point joins RFC 0015's
deferred list.

### 2. Five builtins

The verbs are core builtins — the registry spans every extension plus core's own `agent` kind,
and a uniform verb set must have exactly one implementation (the same reason the tool registry
itself is core). Registry, envelope parsing, and the five `ToolDef`s live in one file,
`core/src/ufo/objects.py`; the loader threads each dispatch to the owning kind's
`ExtensionContext` exactly as it does for the kind-owner's own tools.

```python
class ObjectListInput(BaseModel):
    kind: str = ""    # "" lists the kinds; a kind name lists its instances
    query: str = ""   # kind-interpreted filter; the landing kinds substring-match name and summary
    cursor: str = ""  # opaque, from the previous page's next_cursor

class ObjectGetInput(BaseModel):
    kind: str
    name: str

class ObjectExplainInput(BaseModel):
    kind: str

class ObjectApplyInput(BaseModel):
    manifest: str     # one YAML document: kind, name, spec — nothing else

class ObjectDeleteInput(BaseModel):
    kind: str
    name: str
```

| Tool | Returns |
|---|---|
| `object_list ""` | `[{kind, description}]` — every registered kind |
| `object_list <kind>` | up to `OBJECT_LIST_PAGE = 50` rows `{name, summary}` + `next_cursor` |
| `object_get` | the object as YAML: `spec:` and `status:` side by side |
| `object_explain` | the kind's `guidance` verbatim, its `spec_model` JSON schema (per-field `Field(description=…)` included), its description, and the name grammar — `kubectl explain` without the structural-schema projection, since there is no CRD to feed |
| `object_apply` | `{kind, name, result: created \| updated}` |
| `object_delete` | `{kind, name, deleted: true}` plus the final spec — for a kind that supports create, an accidental delete is re-applyable straight from the transcript; a deleted connector or credential returns only through its handoff flow |

Semantics, fixed here so implementation doesn't relitigate them:

- **Apply is upsert.** `old is None` → create, else update; the handler receives both and
  refuses what it doesn't support (§1). `object_apply` and `object_delete` are
  `side_effecting=True`, and every kind must make a replayed call effect-at-most-once: an
  update-accepting kind's upsert absorbs it; an update-refusing kind treats an identical
  re-apply as an idempotent no-op and refuses only a *differing* spec (the `source` kind's
  rule). A replayed delete finds the name already gone and fails loud with `unknown_object` —
  except on a kind whose instances are declared rather than created (`credential` slots stay
  listed when emptied), where it finds nothing left to clear and succeeds idempotently. All
  five ship `subagent_default=False`; a profile opts in through the existing grant mechanism.
- **Last write wins.** No CAS, no digests: two racing updates resolve like any two tool calls
  today. The concurrency story returns with governance, not before.
- **One manifest per apply**, `yaml.safe_load`, bounded at `OBJECT_MANIFEST_MAX_BYTES = 65_536`
  next to the parse. Unknown top-level keys, multi-document streams, and non-mapping specs are
  refused at the envelope, before any kind code runs.
- **Gating is the handler's.** A kind that owner-gates calls `ctx.speaker_is_owner()` in its own
  mutation handlers, exactly as `sync_source` does today
  (`extensions/sources/ufo_ext_sources/tools.py:132`); a finer rule needs no new mechanism (the
  connector kind admits the grantor, §3).

Every failure is terminal for the call and names its cause:

| Error | Raised when |
|---|---|
| `unknown_kind` | kind unregistered — the message lists the registered kinds |
| `invalid_name` | name violates the grammar |
| `invalid_manifest` | YAML parse failure, wrong envelope keys, non-mapping spec, over the byte bound |
| `validation_failed` | spec fails `spec_model` — field and reason named |
| `unknown_object` | get/delete on a name that doesn't exist |
| `VerbNotSupported` | handler-raised: the kind doesn't do that mutation — the message names the path that does (create on `connector` → `connect_account`) |
| `OwnerRequired` | handler-raised: mutation gated on role, speaker isn't an owner |
| kind-raised `ValueError` | domain rules: bad cron, skill shadowing a core skill, unsupported stream — rendered with the handler's message |

### 3. The kinds landing with this RFC

| Kind | Registered by | Spec | Mutations | Status | Replaces |
|---|---|---|---|---|---|
| `scheduled_task` | scheduled_tasks | `schedule` (5-field cron), `prompt`, `description` | create · update · delete | `next_run_at`, `last_run_at` | the three scheduling tools |
| `skill` | skill_create | `files` (path → text) | create · update · delete | description, file count, bytes | `save_custom_skill`, plus the missing list/get/delete |
| `source` | sources | `provider`, `streams`, `account_id?`, `base_url?` | create · delete | last sync, items, error count | `sync_source` |
| `connector` | connectors | `provider`, `account_id` | delete | grantor, granted-at, host | nothing existed — fills the list/revoke gap |
| `credential` | core | `slot`, `description`, injection host — never the value | delete | filled or empty, updated-at | nothing existed — fills the what's-filled / unset gap |
| `agent` | core | `model` | update | prompt + its digest, read-only | nothing deleted — `prompt` stays proposal-owned, so the two write paths are disjoint by construction |
| `artifact` | core | `filename`, `media_type`, `subject` — the share record, never authored | delete | shared-at, sharing turn + conversation, size, version count, fresh download link, workspace copy path | nothing existed — fills the list/re-fetch/delete gap over `share_file` |

**`scheduled_task`.** Handlers are thin adapters over `ScheduleStore.create/cancel/list`
(`core/src/ufo/scheduling.py:133,313,323`); apply validates through the extension's existing
`validate_cron` (`extensions/scheduled_tasks/ufo_ext_scheduled_tasks/cron.py:14`) and computes
the first `next_run_at`. The report conversation is the applying turn's conversation — today's
upsert behavior, kept: it is operational binding, so it lives in the row, not the spec. Pause
rows (`@pause:` / `@once`) are workflow internals and never surface as objects; `pause_and_wait`
is untouched. Claims, leases, and the job runner do not change.

**`skill`.** Spec is the file map alone: `files: dict[str, str | FileFrom]`, where `FileFrom`
(`{from: <workspace-relative path>}`) is resolved **at apply** — the handler reads the named file
out of the conversation workspace exactly as `save_custom_skill` does today (same path scoping,
same text-only and `MAX_SKILL_TOTAL_BYTES` bounds), stores it inline, and the persisted spec is
always fully inline. `SKILL.md` stays the one home of name and description; its declared name
must equal the object name, checked at apply. Validation, the core-skill shadow refusal, and the
per-workspace cap are the existing `UserSkillStore.save` rules unchanged
(`extensions/skill_create/ufo_ext_skill_create/store.py:85-114`); the `runtime_skills` provider
keeps mounting saved skills per turn exactly as today.

**`source`.** Names are derived — `<provider>-<8-hex digest of the canonical config>` — because a
source's identity *is* its config (`register_source` is already idempotent on the config hash).
The envelope still requires a name, and a model cannot precompute a digest, so the reconciliation
is a taught retry: an apply whose name doesn't match the derivation is refused with the exact
derived name to re-apply under — one extra round trip on first create, none after. Hence create +
delete, no update: repointing a source is delete + create, stated plainly rather than simulated —
while an *identical* re-apply is an idempotent no-op (registration is config-hash idempotent),
which is also what makes a replayed apply safe (§2). Apply validates provider and streams against the boot-time catalog and schedules
an immediate first sync; an unknown provider or stream fails naming the valid set — the
progressive disclosure `sync_source` performed becomes error-driven discovery plus
`object_explain`. Mutations stay owner-gated in the handler, as `sync_source` gates today.
Delete removes the row
and stops the sync; the source's synced pages are derived state, reaped by the existing job
machinery, never inline in the delete.

**`connector`.** A grant row projected as an object: derived name
`<provider>-<account-slug>` (account id canonicalized into the grammar, short digest suffix on
collision), spec is the two identifying fields, status carries grantor, grant time, and host.
The only mutation is delete — revocation: the handler removes the `grant` row and issues the
provider broker's revoke as a best-effort external call. **Create and update raise
`VerbNotSupported`**: connecting an account involves a third party and a secret, so it stays
the `connect_account` chat flow where the speaker gates the granting act — the refusal message
says exactly that. Delete is admitted to the grantor or an owner, checked in the handler against
`grant.grantor_member_id`.

**`credential`.** The declared BYOK slots across installed extensions (the `credentials`
Manifest point, `core/src/ufo/ext/manifest.py:41-58`) projected over core's sealed store
(`credential` table, `tables.py:218-226`; `CredentialStore`, `core/src/ufo/credentials.py:106`).
Every declared slot lists, filled or not — the declaration lives in the manifest, the row only
holds the sealed value — with names derived from the slot (`acme_api_key` → `acme-api-key`).
Spec is the declaration: slot, description, injection host. Status is filled-or-empty and when
that changed. **The value appears in no read — no field, no digest** (a digest of a low-entropy
secret invites offline guessing), so the kind meets §1's no-secrets gate by shape, not
exemption. Fill and rotate stay `request_credentials` — a secret and a private handoff, the
speaker gating the act — so create and update raise `VerbNotSupported` naming it. Delete clears
the stored value, owner-gated in the handler: the in-chat unset that previously had no path.
The slot stays listed as empty afterward.

**`agent`.** One instance today — the workspace's main agent, named at `ufoctl init` — so the
handlers accept update alone, owner-gated; create and delete raise `VerbNotSupported` with the
reason (one agent per workspace today — a domain rule the handler owns, and the message changes
the day multi-agent lands). **Each agent field has exactly one write path.** The kind's spec is
`model` alone — the knob nothing could update before — and apply writes it directly. `prompt`
belongs to the existing proposal path — `Governance`'s prompt-only CAS
(`core/src/ufo/governance.py`), the `proposal` table, `ctx.propose_change`, and the
self-improvement extension over them (RFC 0016's subject) — which **stays and runs independently
beside the kind**. Disjoint fields mean the two surfaces cannot conflict by construction: an
`object_apply` cannot move a prompt under a pending proposal (the spec model refuses a `prompt`
key), and a model update leaves a pending proposal's digest CAS untouched — pinned by a test.
Status renders the prompt read-only beside its digest, which is exactly the `from_digest` a
proposal presents, so `object_get agent` is the read half of the proposal flow. The proposal
machinery is the seed of the governance layer this RFC defers; prompt-writing dissolves into the
object surface when that layer lands (proposals gating `object_apply`/`object_delete`), which is
also when its HTTP approval endpoint — a member action outside chat — finally dies.

**`artifact`.** `shared_artifact` rows (`share_file`'s record of every file shared out of a turn)
projected as objects, **one object per conversation and filename** — `share_file` already treats
a re-share of the same name as version history, and that history is a conversation's: re-sharing
`report.txt` in one conversation versions the same object, while `report.txt` from another
conversation is a different file and a different object. Names carry both halves of the identity
— `<conversation-prefix>-<filename-slug>` (`3f2a9c1b-report-txt`; the grammar admits no dots) —
so one session's artifacts cluster in a listing and generic filenames never collide across
sessions; the name rides in `share_file`'s result, so the producer and the kind agree by
construction, and a short identity-digest suffix appears only when distinct identities still
collide on one name (the `connector` kind's precedent). The kind is read + delete; **create and
update raise `VerbNotSupported`** naming `share_file`, the one producer. `object_get` is the
re-fetch path: it copies the latest bytes back into the conversation workspace at
`artifacts/<name>/<filename>` (bounded at 32 MiB; larger files report `workspace_path: null` and
are fetched via the link) — how a turn reuses a file an earlier turn produced, including another
conversation's. The copy runs in the `status` handler, which the seam calls only on `object_get`,
so `apply`/`delete` reading the current spec never write into the workspace as a side effect.
Status also mints a fresh TTL download link from the deploy secret, exactly as `share_file` does,
and carries the sharing turn, conversation, and version count. Delete removes every version's row
and blob bytes (`BlobStore.delete`, added with this kind); already-minted links then 404 at the
download route, which decides on blob presence.

### 4. Deleted and kept

| Surface | Disposition |
|---|---|
| `schedule_task`, `cancel_scheduled_task`, `list_scheduled_tasks` | deleted → `scheduled_task` kind |
| `save_custom_skill` | deleted → `skill` kind (`FileFrom` covers its one advantage) |
| `sync_source` | deleted → `source` kind |
| `Governance` proposals + `ctx.propose_change` + `self_improvement` | kept — they own `prompt`; the `agent` kind owns `model`, so the two write paths are disjoint and cannot conflict; they dissolve into the governance layer when it wraps the mutation verbs (RFC 0016 builds on them meanwhile) |
| `pause_and_wait` | kept — a workflow pause, not config CRUD |
| `connect_account`, `request_credentials` | kept — secret-bearing handoffs; the speaker gates the granting act (the `connector` and `credential` kinds carry the read and revoke/unset halves) |
| `todos`, `memory_update` | kept — conversation working state and data-plane writes, not workspace config (but see `memory` in §5) |

Per `CLAUDE.md` §Completing work, each deletion lands whole in the unit that ships its
replacement, with a grep for the dead tool names as the tear-out proof. **A deleted tool's
model-facing description survives as its kind's `guidance`** — `object_explain` returns it
near-verbatim, so replacing a bespoke tool never costs the instructions that were tuned into it;
the unit deleting a tool moves its description into the kind in the same commit.

### 5. Future kinds — requirements they pin on this surface now

These kinds are **out of scope for implementation** and land only when their owning extension
registers them. They are specified here because their shapes constrain the wire format, and a
surface that would need reshaping to admit them would be the wrong surface:

| Kind | Backing today | Cardinality | Likely mutations | What it demands |
|---|---|---|---|---|
| `memory` | memory extension items | thousands | delete (writes stay `memory_update`) | `query` maps to the kind's own search, never a table scan |
| `page` | `page` table (`tables.py:376-391`) | tens of thousands | delete | cursor paging; spec = metadata, body by reference |
| `conversation` | `conversation` table | thousands | delete | derived, id-shaped names |
| `website` | sites extension (no durable rows today) | few | create · update · delete | a kind needs a durable row family first — the extension persists deployments before it registers |
| `seat` | `member` table | tens | create · update · delete | role gating; the last-live-owner refusal is the kind's own invariant |
| `surface` | `surfaces` manifest point (deploy config, not rows) | few | create · update · delete | a workspace-level surface→agent binding. metalcraft's precedent is a **separate kind** (`Channel`: surface, `bind.agentRef`, identity — `~/src/metalcraft/src/metalcraft_contracts/kinds/channel.py:49-66`), not an agent field: a binding carries its own identity config and lifecycle, and an agent field could not say *which* of several bindings changed. Recommended: separate kind, designed when multi-agent or multi-binding lands |

What those rows pin on the surface now — all already in §1–§2, none deferrable:

1. `object_list` carries `query` and `cursor` from day one. Small kinds substring-match and
   ignore the cursor; the wire shape never changes when a ten-thousand-row kind arrives.
2. List returns `(name, summary)` rows only. `object_get` is the sole full-spec read, so a
   high-cardinality kind can never flood a turn.
3. A read-only kind is legal and costs nothing: a system-produced kind (pages, conversations)
   is one whose handlers refuse every mutation.
4. The name grammar admits derived ids; authored and derived names are one rule.
5. Spec holds what a member authors or audits. Bulk payloads — page bodies, artifact bytes —
   stay in the kind's own storage, referenced from spec, so get stays bounded.
6. Deleting a high-cardinality object clears derived state via jobs, never inline
   (`CLAUDE.md` §Hot paths).
7. Mutations gate on speaker role inside handlers — the seam `seat` needs
   (`ctx.speaker_is_owner()`, finer grantor-style rules) is the one `source` and `connector`
   already exercise.

### 6. Conformance

The sample extension registers a probe kind through its own store, per the testing doctrine: the
proof drives create, update, get, list (with paging), and delete through the real tool dispatch,
reads the results back through the extension's own capability APIs, and asserts that a refused
mutation, a bad spec, a non-owner mutation on an owner-gated kind, and a kind-name collision at
boot each fail loud with the named error.

### 7. Landing order

| # | Unit | Proof |
|---|---|---|
| 1 | SDK types + kind registry + five builtins + sample probe kind | §6 end to end; boot fails on a colliding or gate-violating kind |
| 2 | `scheduled_task` kind; three tools deleted | "every weekday at 9, digest investor email" creates it in chat; the fire re-enters the conversation; get shows `next_run_at`; delete stops it; a bad cron names the field; `pause_and_wait` untouched |
| 3 | `skill` kind; `save_custom_skill` deleted | author in workspace → apply with `FileFrom` → `load_skill` mounts it next turn; get shows the files; delete removes it from the index; a core-skill shadow is refused |
| 4 | `source` kind; `sync_source` deleted | create syncs pages; a wrong stream error lists the valid ones; a non-owner mutation is refused; delete stops the sync and pages are reaped by job |
| 5 | `connector` + `credential` kinds | connector: list shows granted accounts with grantors; delete revokes (row gone, broker revoke attempted); create is refused pointing at `connect_account`. credential: list shows filled and empty slots; **no read contains a value, asserted**; delete clears one and status flips to empty; fill is refused pointing at `request_credentials` |
| 6 | `agent` kind (core-registered) | "switch the agent to claude-fable-5" applies owner-gated and the next turn runs on it; a non-owner or speakerless apply is refused; a spec carrying `prompt` is refused naming the proposal path; create and delete refuse with the domain reason; a model update under a pending prompt proposal leaves its approval clean — the disjoint-writers contract, pinned by a test |

Each unit updates `spec.md` in the same commit (§Minimal built-in tools gains the five verbs;
§Extension system gains the `objects` point — `propose_change` stays documented there, since the
proposal path survives this RFC, §3/§4); rows for `docs/plan.md` when accepted.

## Doctrine fit / implications

- **Why core:** the kind registry spans every extension plus core's own `agent` kind, and the
  verb set must have exactly one implementation — the same argument that makes the tool registry
  core. Extensions cannot express it: an extension-hosted verb set could not reach the `agent`
  table, and per-extension verb sets are the sprawl this RFC deletes.
- **Every member action stays in chat** — and the one standing exception shrinks: model updates
  now happen through the object verbs in chat (prompt updates stay on the proposal path, §3),
  while proposal approval keeps its bespoke HTTP endpoint (the pre-existing doctrine violation)
  until the governance layer replaces the proposal surface whole — deleting it here would leave
  pending proposals unapprovable.
- **CRUD-only means the model applies directly.** RFC 0013's injection defense (no write applies
  in the turn that proposes it) is deliberately deferred, not refuted: today's exposure is the
  same as `schedule_task`'s — an ungated tool call acting on the member's ask. When governance
  returns it wraps `object_apply`/`object_delete` — one seam, already the only write path — and
  the kinds, manifests, and read verbs are untouched.
- **One shape.** A durable config fact is exactly one object kind; its bespoke tools are deleted
  in the unit that ships it (both ends). No transition period, no aliases.
- **Enforce, don't document:** the registration gates (`extra="forbid"`, JSON round-trip, no
  typed secret fields) make "no declared secret ever appears in a spec, a transcript, or a get"
  a construction property (§1 scopes what the gate can and cannot see); the envelope bound sits
  next to the parse. The same rule shapes `ObjectKind` itself:
  no declared verb set or owner flag beside the handlers that would have to enforce them — the
  refusal in the handler is the one truth (§1).
- **Hot paths:** every verb is a turn-time tool call over indexed rows the kinds already own;
  no reconciler, no watch loop, no derived work inline on a write.

## Alternatives

| Alternative | Rejected because |
|---|---|
| RFC 0013 as written | Six unmerged units of coupled machinery in front of the first durable unit; every layer beyond CRUD is separable and returns incrementally over this surface |
| One generic core `object` table + change-notification hooks | Migrates scheduled tasks, skills, and sources for no behavior change; `agent` (FK consumers: `turn`, `grant`, `conversation`) needs a typed-table exception anyway — two storage shapes where handlers over existing stores are one |
| Agent-as-CRD / k8s, or a k8s-lite reconciler | Settled by RFC 0013's own audit: the exportable part of metalcraft was the explorability mechanism, not the control plane; the DB is already the truth, so a reconciler would reconcile nothing |
| kubectl-in-sandbox (metalcraft's literal model surface) | Requires an apiserver, RBAC, and minted tokens — the machinery this repo deliberately lacks; native tools are strictly simpler and visible to the engine's flags (`side_effecting`, untrusted-content walls) |
| Fewer tools via a ref/view mini-grammar (`config_get(ref, view)`, RFC 0013 §6) | An in-band addressing DSL the model must learn; five flat verbs match how models already use kubectl-shaped surfaces, and each stays trivially simple |
| Per-kind bespoke tools (status quo) | The Current-state table is the verdict: three tools where six kinds needed eighteen, and the read/delete halves simply never got built |
| A `files` parameter on `object_apply` (RFC 0013's content channel) | `FileFrom` inside the one kind that needs it keeps content handling out of the envelope and the other kinds' way |
| Declared `verbs` / `owner_mutations` fields on `ObjectKind` | Kinds own their storage, so a declaration enforces nothing the handler couldn't bypass in its own code — static metadata beside behavior, free to drift (the agent kind's "no create" is a domain rule that changes with multi-agent, not a capability fact). The handler's exception is the single truth and carries the better message; the cost is one instructive failed call in place of pre-flight verb discovery, and the kind `description` carries that in prose |

## Open decisions

1. **Owner-gating defaults.** Proposed: match today's behavior, kind by kind, in each handler
   (`source`, `agent`, `connector`, `credential` gated; `scheduled_task`, `skill` open to any
   member). Open only if the owner wants mutations gated across the board from day one.
2. **`ufoctl` read twins.** `ufoctl objects list|get <kind>` as operator conveniences over the
   same registry. Proposed: not now — chat is the member surface, and the operator has the DB.
