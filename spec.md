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
| Persistence | One async-SQLAlchemy schema over **SQLite by default** (aiosqlite, WAL — zero services for dev) and **Postgres for deploys** (asyncpg); alembic migrations are the single schema source, dialect-neutral (integers for money/tokens; dialect-only types live inside IndexBackend impls). Plus a pluggable blob store (transcripts, compaction records, sandbox workspaces, shared artifacts): **local filesystem by default**, S3-compatible for deploys — the S3 API is the cloud-portability seam. Every row carries `workspace_id`; a deploy serves ONE workspace (hosted multi-workspace is the enterprise layer). |
| Durable execution | DBOS on the same database as the schema (SQLite dev / Postgres deploys): a turn is a durable workflow, a subagent a child workflow; queues, async cancel, crash recovery. DBOS-on-SQLite is verified in U1 — fail loud, never silently fall back to requiring Postgres. Dequeue poll interval and system-DB retention are configured from day one. |
| Streaming | Durable terminal frames in Postgres; live token deltas through a hub interface — in-process in the single-process default, a Redis hub extension for multi-instance deploys. A lost delta costs a redrawn token, never correctness. |
| Topology | `selfhost serve` is one process on one event loop: surfaces + DBOS workers + jobs. Everything is async-native — a blocking call stalls the whole deploy, so blocking-in-async fails lint. Scale-out = more instances plus a shared hub. |
| Sandbox | A local temp-dir carrier is the core default: no kernel isolation (a raw shell reaches the host FS — only tool arguments are workspace-guarded), and egress is proxy-scoped/metered only for clients that honor the proxy env, not kernel-enforced (model keys still stay fail-closed via the sentinel). It is the development / trusted-input default; use Docker or E2B (carrier extensions on the `carriers` point) for untrusted input, isolation, or multi-tenant deploys. |
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
| `memory_item` (memory extension) | Memory: subject = member or `shared`. The memory extension owns this table via its own migration; the index backend owns `chunk`. |
| `ledger` | Metered usage: every model and tool call, priced. |
| `spend_cap` | Caps by scope (`workspace` \| `member` \| `agent`), dimension, window; `reject` or `park` on breach. |
| `job` | Recurring/one-time background work (source sync, triggers, extension jobs). |

## Agent loop

One workflow: inbound → admission (identity, spend preflight) → queue row → worker turn → steps →
terminal frame. A client's wait always ends — the terminal state commits on the failure path too.

- **Tool calling** — typed registry; per-call metering; results bounded before hitting the model.
- **Skill loading** — skills are folders of files (SKILL.md + assets), mounted into the sandbox on
  `load_skill`; packs are collections of skills plus onboarding steps. **A skill ships with the
  thing it teaches**: core ships exactly two — `sandbox`, `delegation` — teaching core's own
  builtins; an extension's skills ride its manifest (the `memory` skill ships with the memory
  extension); domain skills are packs. Every-turn
  content belongs in the system prompt, situational/long content in skills; skills carry
  workflows, never restated tool docs (the tool's description is authoritative).
- **Typed subagents** — a registry of profiles (name, prompt, tool subset, input/output schema);
  spawn = child turn with parent linkage; foreground awaits, background returns an id. Extensions
  register profiles.
- **Compaction** — full-conversation `messages.json.lz4` transcript with monotonic seq +
  `compactions/<cid>/{before,after}` records in the blob store (port of the shipped design);
  history compacts as it approaches the model window so a long turn never exceeds it.
- **Memory** — an extension, not core: it owns the `memory_item` table, the `memory_search`/
  `memory_update` tools, and recall (lexical + vector fusion, subject ∈ {member, shared}),
  auto-injected each turn through an `on_inbound` hook — no core memory seam. It rides two core
  selection seams: the **index backend** behind one lexical/vector/reindex interface and the
  **embed backend** behind one batched-embed interface. The dialect-native index (SQLite FTS5 +
  local cosine, Postgres tsvector + pgvector) ships as the base-pinned `index-default` extension
  and OpenAI embedding as the base-pinned `embed-openai` extension; turbopuffer is a drop-in index
  alternative. The gbrain-style condenser (source pages/events → condense → memory items) is the
  memory extension's own derivation job.
- **Minimal built-in tools** — `bash`, `read`, `write`, `edit`,
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
`create / exec / mount / route / destroy` — a local temp-dir carrier is core's default; Docker and
E2B implement it as extensions on the `carriers` point.

## Extension system

An extension is a Python package exposing one entry point (`selfhost.extension`) that returns a
`Manifest`. Extensions import only the public SDK (`selfhost.sdk`); a CI gate forbids reaching
into core internals.

Manifest registers (each optional):

| Point | Contract |
|---|---|
| `tools` | Typed tool defs + handlers; appear in agents' granted tool sets. |
| `subagents` | Typed subagent profiles. |
| `skills` | Skill folders (SKILL.md + bundled scripts/assets) contributed to the loadable set; the loader parses each into the registry `load_skill` and the `{{skill_index}}` consult, mounted into the sandbox under `.skills/<name>/` beside core's own three. A skill script imports nothing from selfhost (it runs in the sandbox) — a CI gate holds that boundary. |
| `connectors` | Provider actions behind the connector framework; OAuth via the grant flow. **Composio brokers auth by PROXY: every call goes through Composio (`/tools/execute` for tools; the proxy transport → `/tools/execute/proxy` for source HTTP) carrying `(user_id, connected_account_id)` — Composio holds the provider token and injects it server-side; the token is NEVER exposed to us. A connector grant stores only `connected_account_id`; the confused-deputy check reads the account's `user_id` metadata, never a token. Composio grants derive NO egress InjectionRule — the sentinel→key swap (§Sandboxing) is ONLY for user-supplied BYOK `credentials` keys.** |
| `sources` | Data-feed backends: `sync(cursor) -> pages` run as jobs; pages land in memory/knowledge via the derivation pipeline. Each backend is pluggable — S3, GitHub, provider APIs (via connectors), webhooks; core ships only `folder` (local files). |
| `triggers` | Data → memory (and → invocation): hooks on source pages and platform events. |
| `hooks` | Turn-lifecycle policy filters — `pre_tool_use`/`post_tool_use`/`on_inbound` handlers, scoped like a job, that observe, deny, modify, or inject over the tools grants already admit; a runtime filter on top of grants, never a second grant path. Distinct axis from `triggers` (data-plane). |
| `jobs` | Recurring/one-time background work. |
| `routes` | HTTP endpoints under `/ext/<name>/` (webhooks, OAuth callbacks, plugin UIs). |
| `surfaces` | A chat surface on the privileged surface seam: an ingest mounted at `/surface/<name>` plus two-phase writeback delivery (`post` then `attach`). Slack is one. |
| `credentials` | Named BYOK slots the workspace must fill (drives onboarding). |
| `onboarding` | Steps contributed to the workspace/pack onboarding flow. |
| `packs` | Bundled skill packs. |
| `models` | Model providers behind `ModelClient` (OpenRouter, local runtimes). |
| `carriers` | Sandbox carriers — Docker, E2B, remote runners; core's default is a local temp-dir carrier. |
| `indexes` | Index backends for memory/source retrieval (turbopuffer); the dialect-native default (SQLite FTS5 + local cosine, Postgres tsvector + pgvector) ships as the base-pinned `index-default` extension registering name `"default"`, which core resolves when `memory.index_backend` is unset. |
| `embeds` | Embedding backends behind `EmbedClient`, selected by `memory.embed_backend`; OpenAI text-embedding-3-large ships as the base-pinned `embed-openai` extension registering name `"default"`. |
| `hubs` | Stream hubs for multi-instance deploys (Redis). |
| `browsers` | Browser-automation backends at the tool-surface seam; the BUA engine driving Chrome over a CDP endpoint is the core default (browserbase swaps the endpoint provider, browser-use the whole surface). |

`ExtensionContext` (capability-scoped, handed to every handler): workspace-scoped store access,
`credentials.get(slot)`, the selected `index`/`embed` backends, `transaction()` over the
extension's own tables, `invoke(agent, input, conversation=...)`,
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

## Surfaces

Core owns the **surface seam**, not every surface. A surface is trusted infrastructure — it asserts
a member's identity and admits turns as that member — so its `SurfaceContext` is deliberately
privileged (distinct from the scoped extension context): the three capabilities are (1) **admit** an
inbound message onto the durable turn queue (the one `invoke` boundary scheduled tasks and the eval
harness also call, so the spend cap is evaluated once), (2) **identity** resolution — an external id
→ member + conversation, provisioning and linking a `surface_identity` on first contact, (3) durable
**writeback** — a `WritebackPoller` delivers the terminal reply at-least-once (the hub is lossy),
with attachments and rich rendering. Delivery is two-phase: `post` returns the reply's durable
reference (recorded before any upload), then `attach` streams the turn's shared files into that
reply. An extension registers a `surfaces` Manifest point; core mounts its ingest at
`/surface/<name>` and drives the poller.

| Surface | Home | Identity | Conversation key |
|---|---|---|---|
| CLI | core | member token | session (private) |
| Web | core | web session → member | session (private) |
| Slackbot | `extensions/slack` | Slack user → linked member | channel:thread_ts (shared) |

Two-way attachments stream end to end, never buffering a whole file: an inbound Slack file streams
from `url_private` into the conversation's workspace before the turn runs; a shared file
(`share_file` → a `shared_artifact` record) streams from the blob store to Slack's chunked
external-upload API, into the posted reply's thread. `surface_identity` and `conversation.surface`
are open namespaces validated by surface registration, not a fixed enum.

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

## Running it

The developer surface is a pip-installable CLI running as a **host process** — selfhost is never
containerized for development:

```bash
uv tool install selfhost        # the Python package is the primitive; brew formula = later wrapper
selfhost init                   # writes selfhost.toml; onboards workspace + first owner + agent + model key
selfhost serve                  # one process: surfaces + workers + jobs + proxy — SQLite, zero services
selfhost chat                   # a client; connects to serve's URL from selfhost.toml
```

Dev defaults are zero-services: SQLite, filesystem blobs, in-process hub. Docker enters only for
sandboxes (U2+); Postgres (the checked-in compose or an existing instance) enters only for deploys
and the Postgres half of the test matrix.

`serve` talks to the host Docker daemon; sandboxes are **sibling containers**, never children.
Docker is required for sandboxes, not for running selfhost. `chat` is only a client — if nothing
listens it says to run `selfhost serve`; there is no embedded auto-start. A containerized `serve`
(the `selfhost bundle` deploy) spawns siblings via the mounted Docker socket, or uses a remote
carrier extension and needs no host Docker at all.

## Scale-out

Scale-out is a deployment mode, not a feature: the same bundle with more instances. Nothing in core
is instance-aware except the boot guard; the only extension involved is the Redis hub.

| Concern | Multi-instance behavior |
|---|---|
| Database | Postgres required; SQLite is single-instance-only. |
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
sees a live peer while configured with any dev default — in-process hub, filesystem blob store, or
SQLite — refuses to start.

### Roles — the split that's already paid for

An instance logically comprises four roles: **surfaces** (HTTP in, streams out), **workers** (turn
workflows), **jobs** (sync, derivation, reaping), **proxy** (sandbox egress). Core runs all four in
every instance and defines no per-role deployment — mapping processes now would be speculation.
What core does fix is the seam that makes the eventual split free: **roles share nothing in
memory** — cross-role communication is only Postgres/DBOS queues, the blob store, the hub, and the
proxy's HTTP endpoint (any instance's proxy derives identical rules from DB state; sandboxes are
co-located with the instance that created them). An import-boundary gate enforces the seam. The
enterprise k8s layer then splits roles into Deployments with per-role autoscaling by
configuration, not code change.
`selfhost bundle` produces a runnable artifact (OCI image + pinned config + lockfile) — the same
bundle installs OSS, on-prem, or hosted.

## Example extensions (the API's acceptance tests)

| Extension | Points it exercises |
|---|---|
| OpenRouter (any model router) | models |
| Slack surface (ingest + writeback + attachments) | surfaces, credentials |
| Composio connectors | connectors, credentials, routes (OAuth) |
| Docker, E2B | carriers |
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
- No Docker-in-Docker, ever: sandboxes are sibling containers (host daemon or mounted socket), or
  a remote carrier.
