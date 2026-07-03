# Selfhost Spec

Selfhost is an agent runtime a developer can run, read, and extend: a hard-to-vary **core**
(sandboxed agent loop, memory, surfaces, accounting, model abstraction, the extension system) plus
**extensions** through which nearly every easy-to-vary capability is built — connectors, data
sources, triggers, tools, subagents, onboarding. A **workspace** hosts one team and its agents.
Agents accumulate capabilities through **grants made in chat** — never borrowed from whoever is
speaking.

## Principles

Long-term product principles (the destination all design serves):

1. Multiplayer / permissioned.
2. Open source / on-prem / hosted.
3. Agents build with real infrastructure (Kubernetes is the enterprise upgrade, wrapping this core).
4. Agents are granted access via connectors through chat — never via caller identity.

Core doctrine: **if a capability can be an extension, it is not core.** Core earns a module only
when extensions cannot express it: the loop, the sandbox, persistence, surfaces, accounting, model
access, and the extension system itself. The example-extension list at the bottom is the acceptance
test for the extension API — every entry must be expressible without touching core.

## Fixed decisions

| Decision | Value |
|---|---|
| Language | Python 3.12+, uv. Monorepo: `core/` + `extensions/*` + `packs/*` (uv workspace). Rust was considered and rejected for core: the salvage is Python, DBOS has no Rust SDK, the loop is I/O-bound, and extensions must be writable by users and agents in the AI ecosystem's default language. A hot data plane (egress proxy) may become a Rust component later without changing this. |
| Persistence | Postgres (relational state, queues, memory index) + a pluggable blob store (transcripts, compaction records, sandbox workspaces, shared artifacts): **local filesystem by default**, S3-compatible for deploys. One schema; every row carries `workspace_id`; a deploy serves ONE workspace (hosted multi-workspace is the enterprise layer). The S3 API is the cloud-portability seam — any S3-compatible store works, no per-cloud code. |
| Durable execution | DBOS on Postgres: a turn is a durable workflow, a subagent a child workflow; queues, async cancel, crash recovery. Dequeue poll interval and system-DB retention are configured from day one. |
| Streaming | Durable terminal frames in Postgres; live token deltas through a hub interface — in-process in the single-process default, a Redis hub extension for multi-instance deploys. A lost delta costs a redrawn token, never correctness. |
| Topology | `selfhost serve` is one process: surfaces + DBOS workers + jobs. Scale-out = more instances plus a shared hub. |
| Sandbox | Docker is the default carrier, built into core; carriers are an extension point (E2B is an extension). No unsandboxed mode. |
| Models | Model providers are an extension point; core ships Anthropic + OpenAI direct clients behind one `ModelClient` interface. OpenRouter (or any router) is an extension, never core. |
| Observability | OpenTelemetry APIs only in product code; the OTLP export target (Datadog, …) is deploy config. No vendor SDK in core. |
| Kubernetes | Absent from core by construction. The enterprise offering later wraps core with k8s (principle 3); nothing in core may assume or import it. |
| CLI | One CLI: `selfhost` (`chat`, `serve`, `bundle`, `ext`, admin verbs). |

## Workspace model

Tables (all keyed by `workspace_id`, `created_at`, `updated_at`):

| Table | Owns |
|---|---|
| `workspace` | The team unit: name, config digest. |
| `member` | A human. Role: `owner` or `member`. Surface identities link here (Slack user id, CLI token, web session) — one human, many surfaces, one memory subject. |
| `agent` | A configured agent: name, prompt, model policy, granted tool set, skill packs, memory scope. |
| `grant` | Agent ← capability binding: a connector account, a credential slot, a tool group. Records grantor, when, via which conversation. Created through chat; the speaker gates the *granting act*, never subsequent use. |
| `credential` | BYOK secrets, encrypted at rest. Slots are declared by extensions; values are workspace-scoped. |
| `conversation` | Surface context ↔ queue key (Slack thread, CLI session, web session). Private to its creating member unless the surface is shared (a Slack channel is shared by construction). |
| `turn`, `turn_step`, `transcript` | The loop's durable log: lifecycle, steps, full-conversation transcript + compaction records. |
| `memory_item`, `memory_summary` | Memory: subject = member or `shared`. |
| `ledger` | Metered usage: every model and tool call, priced. |
| `spend_cap` | Caps by scope (`workspace` \| `member` \| `agent`), dimension, window; `reject` or `park` on breach. |
| `job` | Recurring/one-time background work (source sync, triggers, extension jobs). |

## Agent loop

One workflow: inbound → admission (identity, spend preflight) → queue row → worker turn → steps →
terminal frame. A client's wait always ends — the terminal state commits on the failure path too.

- **Tool calling** — typed registry; per-call metering; results bounded before hitting the model.
- **Skill loading** — skills are folders of files (SKILL.md + assets), mounted into the sandbox on
  `load_skill`; packs are collections of skills plus onboarding steps.
- **Typed subagents** — a registry of profiles (name, prompt, tool subset, input/output schema);
  spawn = child turn with parent linkage; foreground awaits, background returns an id. Extensions
  register profiles.
- **Compaction** — full-conversation `messages.json.lz4` transcript with monotonic seq +
  `compactions/<cid>/{before,after}` records in the blob store (port of the shipped design);
  history compacts as it approaches the model window so a long turn never exceeds it.
- **Memory** — the store and recall (lexical + vector fusion, subject ∈ {member, shared},
  auto-injected at turn load; `memory_update` writes) are core. Two pluggable seams: the **index
  backend** (pgvector default; turbopuffer as an extension) behind one lexical/vector/reindex
  interface, and the **derivation mechanics** — extensions register pipeline stages (source
  pages/events → condense — e.g. to markdown — → memory items + graph updates). Core ships a
  default condenser; a gbrain-style pipeline replaces or extends it.
- **Minimal built-in tools** — `bash`, `read`, `write`, `edit`, `memory_search`, `memory_update`,
  `ask_user`, `spawn_subagent`, `load_skill`, `share_file`. Everything else arrives via extensions.
  Two tools where one would do is a defect. `share_file` ports the shipped design: byte custody in
  the blob store, a TTL-bound token URL served by the web surface — no token, no bytes.

## Sandboxing

Every turn executes tools in a per-conversation sandbox: Docker container from a pinned image
(baked toolchain), default-deny network egress with exactly one route out — the sandbox proxy.

**The sandbox proxy is core, not an extension** — it is the enforcement point for three core
invariants: **sentinel swap** (processes inside see placeholder credentials; the proxy swaps real
values onto the wire, so raw secrets never enter the sandbox), **grant scoping** (an outbound call
is allowed only for hosts/accounts the agent's grants cover — principle 4 enforced at the wire),
and **wire metering** (every model/API call made from inside the sandbox lands in the ledger).
Extensions never register raw network rules; the proxy's rewrite rules are *derived* from their
manifests — a credential slot, a connector, a model provider each imply their injection and scoping
rules. Declare, don't open. The enterprise k8s layer later ships its apiserver-rewrite / token-mint
module through this same rewriter seam.

The working directory mounts
from the blob store — a bind mount on the filesystem backend, the sandbox-fs design on S3 — and the
invariant holds on every backend: the sandbox reaches only the conversation's `workspace/` subtree;
transcripts and compaction records live above it, framework-only. The workspace is the truth and
the container is disposable cache — carriers create-or-attach, and a reaper reclaims idle
containers. Carrier interface:
`create / exec / mount / route / destroy` — Docker implements it in core; E2B implements it as an
extension.

## Extension system

An extension is a Python package exposing one entry point (`selfhost.extension`) that returns a
`Manifest`. Extensions import only the public SDK (`selfhost.sdk`); a CI gate forbids reaching
into core internals.

Manifest registers (each optional):

| Point | Contract |
|---|---|
| `tools` | Typed tool defs + handlers; appear in agents' granted tool sets. |
| `subagents` | Typed subagent profiles. |
| `connectors` | Provider actions behind the connector framework; OAuth via the grant flow. |
| `sources` | Data-feed backends: `sync(cursor) -> pages` run as jobs; pages land in memory/knowledge via the derivation pipeline. Each backend is pluggable — S3, GitHub, provider APIs (via connectors), webhooks; core ships only `folder` (local files). |
| `triggers` | Data → memory (and → invocation): hooks on source pages and platform events. |
| `jobs` | Recurring/one-time background work. |
| `routes` | HTTP endpoints under `/ext/<name>/` (webhooks, OAuth callbacks, plugin UIs). |
| `credentials` | Named BYOK slots the workspace must fill (drives onboarding). |
| `onboarding` | Steps contributed to the workspace/pack onboarding flow. |
| `packs` | Bundled skill packs. |
| `models` | Model providers behind `ModelClient` (OpenRouter, local runtimes). |
| `carriers` | Sandbox carriers (E2B, remote runners). |
| `memory` | Derivation pipeline stages (condensers, graph updaters) — see Agent loop / Memory. |
| `indexes` | Index backends for memory/source retrieval (turbopuffer); pgvector is the core default. |
| `hubs` | Stream hubs for multi-instance deploys (Redis). |

`ExtensionContext` (capability-scoped, handed to every handler): workspace-scoped store access,
`credentials.get(slot)`, `memory.write(...)`, `invoke(agent, input, conversation=...)`,
`schedule(job)`, `trajectories.read(...)` (transcript/turn evidence), and
`agents.propose_change(...)` — the governed promotion path: an extension never edits agent config
directly; it opens a proposal (prompt, skills, tool grants) that applies through the same
grant/approval flow chat uses. This is what makes a full self-improvement extension expressible —
mine trajectories, evaluate candidates via `invoke`, promote through `propose_change` — not just
prompt files on disk. Extensions never see raw DB handles or other workspaces.

### Extension store

Extensions are Python packages. A deploy may enable the extension store — a registry index that
the CLI (and, when granted, an agent in chat) searches and installs from: `selfhost ext search /
install / remove`. Installs pin version + digest and are recorded in the bundle lockfile; with the
store disabled, a deploy runs only what its bundle ships.

## Surfaces (core)

| Surface | Identity | Conversation key |
|---|---|---|
| Slackbot | Slack user → linked member | channel:thread_ts (shared) |
| CLI | member token | session (private) |
| Web | web session → member | session (private) |

Onboarding flow engine is core (steps are contributed by extensions/packs); first-run creates the
workspace and its first `owner`.

## Accounting / billing

Every model call and tool call meters into `ledger` in the same commit as the step. Realtime
visibility: live per-turn cost on the stream, workspace/member/agent rollups in CLI and web. Caps
evaluated at inbound and per-step; `reject` refuses new turns, `park` suspends. Prices are a pinned
table per model; BYOK usage still meters (visibility without billing).

## Model abstraction

`ModelClient`: `complete(messages, tools, stream)` + token accounting + provider image/content
limits. Implementations: Anthropic, OpenAI. Model policy per agent (`auto` routes by task class);
keys come from `credential` slots or deploy config.

## Deploy config bundling

One declarative file, `selfhost.toml`: Postgres URL, blob store (filesystem root or S3 endpoint),
model keys (env refs), enabled extensions + versions, installed packs, surface config (Slack app,
web host), sandbox carrier, stream hub, OTLP export target, extension-store toggle, spend defaults.

## Scale-out

Scale-out is a deployment mode, not a feature: the same bundle with more instances. Nothing in core
is instance-aware except the boot guard; the only extension involved is the Redis hub.

| Concern | Multi-instance behavior |
|---|---|
| Turns, queues, jobs | DBOS coordinates through Postgres: any instance pulls; a crashed instance's workflows recover on peers. |
| Live deltas | Shared hub required (Redis hub extension); terminal frames stay durable in Postgres. |
| Blobs | S3 backend required; the filesystem backend is single-instance-only. |
| Sandboxes | Per-instance disposable cache over durable workspace state; any instance recreates the container on demand. |
| Surfaces, webhooks | Stateless behind a load balancer; sessions and idempotency live in Postgres. |

Two invariants make this safe, and they hold even single-instance:

- **At most one running turn per conversation** — the DBOS queue serializes on the conversation
  key; the transcript's monotonic seq depends on it.
- **The workspace is the truth, the container is cache** — a sandbox may be destroyed and
  recreated between turns from the blob store without a turn noticing beyond latency.

Misconfiguration fails loud at boot: instances heartbeat a `runtime_instance` row; an instance that
sees a live peer while configured with an in-process hub or a filesystem blob store refuses to
start.
`selfhost bundle` produces a runnable artifact (OCI image + pinned config + lockfile) — the same
bundle installs OSS, on-prem, or hosted.

## Example extensions (the API's acceptance tests)

| Extension | Points it exercises |
|---|---|
| OpenRouter (any model router) | models |
| Composio connectors | connectors, credentials, routes (OAuth) |
| E2B | carriers |
| Redis stream hub | hubs |
| turbopuffer index | indexes |
| GitHub / S3 source backends | sources |
| Agent-guided education / onboarding | onboarding, tools, packs |
| Scheduled tasks (cron / one-time) | jobs, invoke, tools |
| GH code review on PR + auto-merge | routes (webhook), credentials, invoke, tools |
| Service self-improvement / bug-fixing from o11y | sources (o11y), jobs, trajectories.read, invoke (evals), agents.propose_change |
| Security review | tools, subagents, packs |
| gbrain-style memory (source → condense to markdown + graph) | memory, sources, triggers |
| CRM / ATS | connectors, sources, triggers, tools, packs |
| Websites | tools (sandbox serving), routes |

Skill packs (content, not code): **assistant** (deep research, wide research/browse, browser
subagent, office docs), **startup** (onboarding, YC document questions, bookface search, deals,
fundraising docs, marketing/ad loops), **support bot** (onboarding: connect knowledgebase + keys;
intercom-style website plugin via the websites extension).

## Non-goals (core, now)

- No Kubernetes, CRDs, operators, or RLS multi-tenancy (the `workspace_id` column is the only
  concession to the future).
- No Redis in the single-process default; no router service in core (both are extensions).
- No self-improvement machinery in core (the extension API carries it — see `trajectories.read` /
  `agents.propose_change`).
- No second representation of any fact: one transcript store, one schema source, one config file.
- No tool that another tool or `bash` subsumes.
