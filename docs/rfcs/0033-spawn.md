---
rfc: 0033
title: "Spawn — one verb over typed targets: profiles and workspace agents"
status: implemented
date: 2026-08-15
---

# Spawn — one verb over typed targets: profiles and workspace agents

> A turn can delegate to a subagent profile but not to a workspace agent. The specialists a
> workspace actually accumulates — member-built agents, extension-shipped agents (RFC 0030), each
> with its own prompt, model, tool allowlist, source grants, and memory — are reachable only by a
> member addressing them on a surface. This RFC gives every agent a declared input/output contract,
> replaces `spawn_subagent` with `spawn` — one verb that dispatches a typed child turn against
> either target kind, where an agent target is a fully async peer rather than a nested task — and
> opens agent creation to every member, each owning what they created.

## Current state

| | Subagent profile | Workspace agent |
|---|---|---|
| Registered | boot, from manifests (`core/src/ufo/ext/manifest.py:523`) | `agent` rows: `object_apply agent`, RFC 0030 provisioning |
| Identity | none — child runs under the spawning turn's `agent_id` | its own: prompt, model, reasoning, tools allowlist, source grants, memory, spend |
| Input/output schema | `input_model`/`output_model`, payload validated at spawn (`core/src/ufo/loop/subagents.py:184`), output forced through the `finish` tool | none — free-text turns on a surface |
| Reachable by delegation | `spawn_subagent` (`core/src/ufo/tools/builtins.py:1211`) | not at all |

Dispatch already branches on the discriminator (`core/src/ufo/loop/queue.py:412`): a turn with
`subagent_profile` set runs the profile's prompt, tool subset, and round cap; a turn without runs
the agent row's own prompt, sections, runtime skills, and allowlist-filtered tools. The agent
branch is the dispatch path a delegated agent run needs — what it lacks is a way to be admitted
with parent linkage, an output contract, and result delivery.

## Proposal

### Agents declare their contract

`AgentSpec` gains two optional fields, stored as `jsonb` columns on `agent`:

| Field | Meaning | Unset |
|---|---|---|
| `input_schema` | raw JSON Schema for the spawn payload | `{task: string}` |
| `output_schema` | raw JSON Schema for the final answer — the `finish` tool's input schema | `{result: string}` |

Each side defaults independently. `object_apply agent` validates a declared schema at write time —
top-level `type: object`, compiles, bounded size, no `$ref`/`$dynamicRef` anywhere (a
member-declared reference is a URI the serve process would otherwise fetch), and no
`pattern`/`patternProperties` (a member-declared regular expression can cost the one serving loop
unbounded time against a chosen payload) — so a bad schema is refused at the write, never
discovered at a spawn. Validation itself resolves against an empty
registry: nothing a contract holds can reach the network, and a reference that arrives anyway
raises as the same `ValidationError` every catch site already reads. Payloads and outputs are validated against the raw schema through one
wrapper seam, the same raw-JSON-Schema position spec.md already fixes for third-party
`args_schema`; this RFC builds that seam first. Profiles keep their code-defined pydantic models;
the wrapper and a model expose one validating interface, so `spawn` holds one code path.

### One verb

`spawn(target, payload, background, name, user_description)` replaces `spawn_subagent`;
`message_subagent`/`cancel_subagent` become `message_spawn`/`cancel_spawn` unchanged — they key on
child turn ids and never cared what kind of child. The child of a spawn is a spawn, and "subagent"
stays the name of a profile (RFC 0030: subagents stay subagents). A profile target keeps its
foreground/background choice; an agent target always runs background and always delivers — it is
a peer nobody blocks on.

`target` is an exact name resolved against the profile registry, then the workspace's agent rows.
Qualified forms `profile:<name>` and `agent:<name>` are always accepted; a bare name both kinds
hold is refused naming the two qualified forms. Host-side callers (`ctx.spawn` in extension
delegation tools) pass the qualified form.

### An agent child turn

A spawn of an agent target admits a child conversation and turn exactly as a profile spawn does —
same dedup key derivation, same idempotent admission, same queue partition — differing only where
the target's identity demands it:

| | Profile child (today, unchanged) | Agent child |
|---|---|---|
| `agent_id` | the spawning turn's | the target agent's |
| `subagent_profile` | the profile name | NULL |
| System prompt | profile prompt + output discipline + finish contract | the agent's own rendered prompt + finish contract |
| Tools | profile subset + grants | the agent's granted set |
| Model, reasoning | profile's or inherited | the agent's own (`auto` resolved) |
| Round cap | profile's `max_rounds` | the main ceiling |
| Sandbox | the spawning turn's | its own, at the agent's `sandbox_size` and internet policy |
| Output contract | profile `output_model` | the agent row's `output_schema`, read at dispatch and at delivery |
| Trust | profile's `untrusted_output` | walled — the whole member-facing tool set reads the open web, so the answer is data like an untrusted profile's |

The discriminator generalizes: `parent_turn_id IS NOT NULL` means spawned; `subagent_profile` then
names which kind. Only the spawn path writes parent linkage, and every site that today reads
`subagent_profile is None` to mean "member-facing turn" — the context tag, the pending guard, the
cache TTL, adoption replay, run lineage — moves to one `Turn.spawned` predicate.

An agent child never shares the spawning turn's sandbox: the target's `sandbox_size` and
`internet_access_allowed` are its own settings, and a shared filesystem under a different egress
policy is a bypass. Files cross as typed output and `share_file`.

Authority is unchanged in both directions. `on_behalf_of_member_id` carries the acting member
through the hop, so member gates — connector use, credential access — hold inside the child. The
agent wall moves to the target: its source grants, its memory, its allowlist. The child
conversation copies the parent's audience, so recall stays audience-scoped.

### Fully async peers

A spawned agent is not tied to the agent that spawned it. It holds its whole tool set — `spawn`
included, at any depth — and the spawning conversation is simply where its messages arrive: the
contract-validated answer walled as data, or the question it ended asking, delivered as an
arrival. Reading them
to a member is the receiving conversation's business, and `message_spawn` is how it replies to a
parallel agent. Cancellation honours the same independence: the cancel reconciler's cascade never
crosses an agent-child boundary, so cancelling a spawner leaves the peers it launched running,
while cancelling a peer still reaps the profile children it spawned. Cost is bounded where cost
is governed — spend caps — never by structure.

### Member interaction bubbles

A spawned turn keeps `ask_user`, and its question is the bubble carrier: `ask_user` already ends
a turn with the structured question on the terminal frame, and in a spawned turn that terminal
delivers up to the spawning conversation as a typed arrival (`status="question"`), exactly as a
result does. The level that receives it either answers — `message_spawn` continues the child on
its own transcript, and the continuation delivers its result back — or re-raises with its own
`ask_user`, which bubbles again until a conversation with a member renders it as the ordinary
member-facing question it is today.

The granting acts do not bubble as acts. `request_credentials` and `connect_account` already
require a speaking member — a seal binds to the speaker, a grant is the speaker's — so in a
spawned turn they refuse as recoverable tool errors, the child expresses the need as a question,
and the member-facing level performs the act with its live speaker. Secrets enter the credential
store at that surface and never travel down the chain as text; the child reads its slots through
the normal injection path on its next turn. Whether a given profile holds `ask_user` stays that
profile's `tool_names` choice; nothing is stripped structurally.

### Members own their agents

`agent` gains `owner_member_id`. Any speaking member creates an agent — in chat through
`object_apply`, or through the portal's create form, now offered to every member — and the row is
stamped theirs. One rule gates every write: the owner or a workspace admin edits, any field, from
any lane; an ownerless row — the main agent, a provisioned agent — answers to admins alone, which
is what "the main agent is owned by the admin" means in rows. The prior special lanes (create
admin-gated on the main agent, chat edits restricted to the main agent changing prompts,
settings changes intent-only) dissolve into that one rule. Delete stays refused.

Ownership also gates the spawn, and it mirrors the portal's reach. A turn spawns the agents its
acting member owns; a workspace admin spawns any, the ownerless rows (main, provisioned)
included — they are the admins'. A non-admin never reaches an agent they do not own, exactly as
the portal gives them only the main agent, their grants, and their own rows — an owner's agent
joins their web audience without a grant, so the member who created an agent can always open it.
What the gate buys is authority, not content trust: no prompt you did not write or vet runs under
your member gates, while the child's answer still walls as data (its tool set reads the open
web). The catalog lists exactly the spawnable set, and the activity tree names an agent run by
its qualified target, showing its work inline — profile runs alone link out to their own
conversation page. The acting member is one fold everywhere
the spawn machinery reads it — the bound `requested_by` requester, else the turn's founding
speaker, else the initiator a speakerless turn acts on behalf of — so a member's plain chat
request spawns their own agent without ceremony, and the child is stamped with that same member's
authority.

A spawned agent that itself spawns keeps the chain whole: a turn founded by an arrival on a
spawned conversation — a grandchild's result waking its parent after that parent's turn ended —
inherits the conversation's spawn identity from its founding turn (parent linkage, profile,
display name, and whether it delivers), so the continuation runs under the same contract and its
own answer still reaches the conversation that spawned it.

### Result delivery and the catalog

The result envelope names its target: `<spawn_result target="agent:support" ...>`. Delivery
validates against the contract resolved by kind — the registry for a profile, the child turn's
agent row for an agent, read at delivery so the row is always the contract. An unregistered
profile still walls as untrusted; an agent row cannot vanish (agents are undeletable).

The boot-generated `subagent-catalog` skill becomes the **spawn catalog**, assembled per turn
(agent rows are workspace state, like member-authored skills): every profile and every workspace
agent, each with its payload and output keys, qualified where a name is shadowed.

### Migration

One migration: `agent.input_schema`, `agent.output_schema` (jsonb, NULL), and
`agent.owner_member_id` (uuid, NULL — NULL is admin-owned, which is what every pre-existing, main,
and provisioned row means). The turn table is untouched: an agent child is recognized by parent
linkage without a profile, columns that already exist. The rename lands whole — `spawn_subagent`
survives nowhere: builtins, prompts, catalog, extension guidance text, evals, tests.

## Doctrine fit / implications

- **Core, not extension**: spawn is the delegation primitive itself — profiles and agents are both
  core registries, and only core constructs a child's effective tool set.
- **Both ends**: schema columns land with their producer (`object_apply` validation) and
  consumers (finish contract, payload validation, delivery validation) in one unit; the owner
  column lands with its stamp and its gate.
- **Fail loud**: bad schema refused at write; ambiguous target refused with the qualified forms;
  a speakerless create refused.
- **Enforce, don't document**: the ownership gate, the schema write-time validation, the
  speaker-bound refusals of the granting verbs, and the allowlist intersection on the agent
  branch are all code gates; no behavior leans on prompt text.
- **Member actions stay in chat**: nothing here adds a member surface. A bubbled need surfaces in
  the member's own conversation as the act it always was — the speaker gate on granting holds at
  any depth. Spawn is the agent-to-agent register.

## Alternatives

- **`spawn_agent` beside `spawn_subagent`.** Rejected: two verbs for one act; the model must learn
  which register a specialist lives in before it can delegate.
- **Delegate by messaging the target agent's conversation.** Rejected: no typed contract, no parent
  linkage, no delivery validation — the result comes back as prose on a surface, and delegation
  becomes chat plumbing.
- **Fixed `{task}`→`{result}` contract only, no declared schemas.** Rejected: a specialist whose
  answer is prose forces every parent to re-parse it; the finish tool already speaks JSON Schema,
  so the declared contract rides existing mechanism.
- **Translate declared JSON Schema into generated pydantic models.** Rejected by spec.md already:
  raw-schema validation through a wrapper, no translation layer to maintain.
- **A depth cap on agent recursion.** Rejected: a spawned agent is an independent peer, not a
  nested computation — structure is not where cost is governed; spend caps are.
- **Share the parent's sandbox with an agent child.** Rejected: a shared filesystem across
  different egress policies and sizes is a policy bypass, not a convenience.
- **Trust an agent child's answer because its prompt is vouched.** Rejected: the prompt does not
  vouch what the child read — the member-facing set carries open-web readers — so the answer
  walls like an untrusted profile's, and the ownership gate separately keeps a foreign prompt off
  the spawner's authority.
- **Strip the member-interaction verbs from spawned turns.** Rejected: a specialist blocked on one
  fact would fail the whole delegation; the parent chain is exactly the escalation path, and the
  terminal already carries each need as a structured record.

## Open decisions

- Which subagent profiles gain `ask_user` in `tool_names`, and what their prompts say about
  asking — profile curation, not mechanism.
- Whether an owner can hand an agent to another member. No transfer verb exists; today an admin
  edit is the workaround, and ownership moves only if a column write says so.
