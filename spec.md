# Ufo Spec

Ufo is an agent runtime a developer can run, read, and extend: a hard-to-vary **core**
(sandboxed agent loop, memory, surfaces, accounting, model abstraction, the extension system) plus
**extensions** through which nearly every easy-to-vary capability is built — connectors, data
sources, tools, subagents, onboarding. A **workspace** hosts one team and its agents.
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
| Language | Python 3.12+, uv. Monorepo: `core/` + `extensions/*` + `packs/*` + the top-level `evals/` operator package, built and shipped as one pip-installable distribution (`ufo`). `pip install ufo` brings core and evals directly; first-party extensions and packs register through entry points. Rust was considered and rejected for core: the salvage is Python, DBOS has no Rust SDK, the loop is I/O-bound, and extensions must be writable by users and agents in the AI ecosystem's default language. A hot data plane (egress proxy) may become a Rust component later without changing this. |
| Persistence | One async-SQLAlchemy schema over **SQLite by default** (aiosqlite, WAL — zero services for dev) and **Postgres for deploys** (asyncpg); alembic migrations are the single schema source, dialect-neutral (integers for money/tokens; dialect-only types live inside IndexBackend impls). Plus a pluggable blob store (transcripts, compaction records, sandbox workspaces, shared artifacts): **local filesystem by default**, S3-compatible for deploys — the S3 API is the cloud-portability seam. Every row carries `workspace_id`; a deploy serves ONE workspace (hosted multi-workspace is the enterprise layer). |
| Durable execution | DBOS on the same database as the schema (SQLite dev / Postgres deploys): a turn is a durable workflow, a subagent a child workflow; queues, async cancel, crash recovery. DBOS-on-SQLite is verified in U1 — fail loud, never silently fall back to requiring Postgres. Dequeue poll interval and system-DB retention are configured from day one. |
| Streaming | Durable terminal frames in Postgres; live token deltas through a hub interface — in-process in the single-process default, a Redis hub extension for multi-instance deploys. A lost delta costs a redrawn token, never correctness. |
| Topology | `ufoctl serve` is one process on one event loop: surfaces + DBOS workers + jobs. Everything is async-native — a blocking call stalls the whole deploy, so blocking-in-async fails lint. Scale-out = more instances plus a shared hub. |
| Sandbox | A local temp-dir carrier is the core default: no kernel isolation (a raw shell reaches the host FS — only tool arguments are workspace-guarded), and egress is proxy-scoped/metered only for clients that honor the proxy env, not kernel-enforced (model keys still stay fail-closed via the sentinel). It is the development / trusted-input default; use Docker or E2B (carrier extensions on the `carriers` point) for untrusted input, isolation, or multi-tenant deploys. |
| Models | Model providers are an extension point; core ships Anthropic + OpenAI direct clients behind one `ModelClient` interface. OpenRouter (or any router) is an extension, never core. |
| Observability | OpenTelemetry APIs only in product code; the OTLP export target (Datadog, …) is deploy config. No vendor SDK in core. |
| Kubernetes | Absent from core by construction. The enterprise offering later wraps core with k8s (principle 3); nothing in core may assume or import it. |
| CLI | One CLI: `ufo` (`chat`, `serve`, `bundle`, `ext`, admin verbs). |

## Workspace model

Tables (all keyed by `workspace_id`, `created_at`, `updated_at`):

| Table | Owns |
|---|---|
| `workspace` | The team unit: name, config digest. |
| `member` | A human. Role: `owner` or `member`. Surface identities link here (Slack user id, CLI token, web session) — one human, many surfaces, one memory subject. |
| `surface_installation` | A chat installation's unique external identity → workspace binding. Shared ingress uses it only to select a candidate credential, authenticates the original request bytes, then binds that workspace. |
| `agent` | A configured agent: name, prompt, model policy, granted tool set, skill packs, memory scope. |
| `grant` | Agent ← capability binding: a connector account, a credential slot, a tool group. Records grantor, when, via which conversation. Created through chat; the speaker gates the *granting act*, never subsequent use. |
| `credential` | BYOK secrets, encrypted at rest. Slots are declared by extensions; values are workspace-scoped. |
| `conversation` | Surface context ↔ queue key (Slack thread, CLI session, web session). Private to its creating member unless the surface is shared (a Slack channel is shared by construction). |
| `turn`, `transcript` | The loop's durable log: lifecycle, full-conversation transcript + compaction records. Sub-turn steps — each model round, tool call, and compaction — are DBOS's own `operation_outputs` step log, memoized so a crash-recovery re-run replays completed work instead of redoing it. |
| `memory_item` (memory extension) | Memory: subject = member or `shared`. The memory extension owns this table via its own migration; the index backend owns `chunk`. |
| `ledger` | Metered usage: every model and tool call, priced. |
| `spend_cap` | Caps by scope (`workspace` \| `member` \| `agent`), dimension, window; `reject` or `park` on breach. |
| `job` | Recurring/one-time background work (source sync, page-change fan-out, extension jobs). |

## Agent loop

One workflow: inbound → admission (identity, spend preflight) → queue row → worker turn → steps →
terminal frame. A client's wait always ends — the terminal state commits on the failure path too.

- **Tool calling** — typed registry; per-call metering; results bounded before hitting the model.
- **Skill loading** — skills are folders of files (SKILL.md + assets), mounted into the sandbox on
  `load_skill`. **A skill ships with the thing it teaches**: core ships exactly two — `sandbox`,
  `delegation` — teaching core's own builtins; an extension's skills ride its manifest (the `memory`
  skill ships with the memory extension); a pack may add pack-level skills of its own (see Packs).
  Every-turn content belongs in the system prompt, situational/long content in skills; skills carry
  workflows, never restated tool docs (the tool's description is authoritative).
- **Typed subagents** — a registry of profiles (name, prompt, tool subset, input/output schema);
  spawn = child turn with parent linkage; foreground awaits, background returns an id. Two payload
  knobs any profile may declare: `preload_skills` mounts the named skills and injects their
  instructions before the child's first round; `extended_context` lifts its round budget to the
  main ceiling. Extensions register profiles.
- **Compaction** — full-conversation `messages.json.lz4` transcript with monotonic seq +
  `compactions/<cid>/{before,after,summary}` records in the blob store; a deterministic pipeline
  groups the over-window head into API rounds, compresses it into a validated structured
  `CompactionSummary` (one metered model call, bounded prompt-too-long retry), re-references the
  durable `.tool-output` files it offloaded, and keeps the recent tail verbatim. The trigger derives
  from the model's real window less the summary reserve, so a long turn never exceeds it.
- **Memory** — an extension, not core: it owns the `memory_item` table, the `memory_search`/
  `memory_update` tools, and recall (lexical + vector RRF fusion, subject ∈ {member, shared}),
  auto-injected each turn through a `user_prompt_submit` hook — no core memory seam. Recall carries the
  gbrain richness: per-kind recency decay (fact/preference/decision/event/task half-lives, fact
  items only), a type-diversity cap so no class dominates, supersession suppression, and an
  episodic→topic pointer excluded from auto-injection. It rides two core selection seams: the
  **index backend** behind one lexical/vector/reindex interface and the **embed backend** behind
  one batched-embed interface. The dialect-native index (SQLite FTS5 + local cosine, Postgres
  tsvector + pgvector) ships as the base-pinned `index_default` extension and OpenAI embedding as
  the base-pinned `embed_openai` extension; turbopuffer is a drop-in index alternative. Source
  pages reach recall through the memory extension's own page-index job over the core `PageFeed`.
- **Minimal built-in tools** — `bash`, `read`, `write`, `edit`,
  `ask_user`, `request_credentials`, `spawn_subagent`, `load_skill`, `share_file`. Everything else
  arrives via extensions.
  Two tools where one would do is a defect. `share_file` ports the shipped design: byte custody in
  the blob store, a TTL-bound token URL served by core's artifact route — no token, no bytes.

## Sandboxing

Every turn executes tools in a per-conversation sandbox: Docker container from a pinned image
(baked toolchain), default-deny network egress with exactly one route out — the sandbox proxy.
An off-cluster carrier reaches the proxy only over TLS; the per-turn proxy token is never sent on
plaintext transport.

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

An extension is a Python package exposing one entry point (`ufo.extension`) that returns a
`Manifest`. Extensions import only the public SDK (`ufo.sdk`); a CI gate forbids reaching
into core internals.

Manifest registers (each optional):

| Point | Contract |
|---|---|
| `tools` | Typed tool defs + handlers; appear in agents' granted tool sets. |
| `subagents` | Typed subagent profiles. |
| `prompt_sections` | Capability sections a pack contributes to the agent's system prompt, rendered into the shell's `{{sections}}` slot ordered by name — a pack's rules (web search, browsing, office docs) reach the agent without core naming the capability. |
| `skills` | Skill folders (SKILL.md + bundled scripts/assets) contributed to the loadable set; the loader parses each into the registry `load_skill` and the `{{skill_index}}` consult, mounted into the sandbox under `.skills/<name>/` beside core's own three. A skill script imports nothing from ufo (it runs in the sandbox) — a CI gate holds that boundary. |
| `connectors` | One brokered provider per declaration: its OAuth descriptor (grant flow), member-facing label, and `ConnectorBroker` — catalog, server-side execute, feed-sync credential. `serve` merges every declaration into the one `ConnectorRegistry`; the `connectors` extension's broker-generic dynamic tools (list/describe/search/call) dispatch through it, and the sync runner resolves a brokered provider's feed-sync `Credential` through its own broker. Two broker extensions ship — `composio` (most providers) and `pipedream` (Gmail: Google blocks restricted Gmail scopes on Composio's shared client; the deploy's own Google OAuth client rides Pipedream Connect). **A broker brokers auth by PROXY: every call goes through the broker (its execute API for tools; a proxying transport for feed-sync source HTTP) carrying `(external user, connected account id)` — the broker holds the provider token and injects it server-side; the token is NEVER exposed to us. A connector grant stores only the connected-account id; the confused-deputy check reads the account's owner metadata, never a token. Broker grants derive NO egress InjectionRule — the sentinel→key swap (§Sandboxing) is ONLY for user-supplied BYOK `credentials` keys.** |
| `sources` | Data-feed backends: `sync(cursor) -> pages` run as jobs; pages land in memory/knowledge via the derivation pipeline. Each backend is pluggable — S3, connector/provider APIs, webhooks; core ships only `folder` (local files). Connector source providers live in `extensions/sources`, built on the read-only REST connector framework core exposes through `ufo.sdk.sources` (so any extension can provide a source); each resolves a provider `Credential` through the pluggable **auth-proxy** seam, never importing a broker. |
| `hooks` | Reactive lifecycle handlers on Claude Code's taxonomy, scoped like a job. Seven fire on the turn loop — `pre_tool_use`/`post_tool_use`/`post_tool_use_failure`, `user_prompt_submit`, `stop`, `pre_compact`/`post_compact` — as a runtime policy filter over the tools grants already admit (observe, deny, modify, or inject), never a second grant path. The eighth, `page_change`, is the data-plane seam (data → memory): a core batched cursor-runner replays each changed source page to a consumer's hook off that extension's own cursor — the path the memory indexer and knowledge-graph extractor ride. (Claude Code's session/permission/subagent-stop/notification events have no producer here and are not members until one lands with a consumer.) |
| `jobs` | Recurring/one-time background work. |
| `routes` | HTTP endpoints under `/ext/<name>/` (webhooks, OAuth callbacks, plugin UIs). |
| `surfaces` | A chat surface on the one privileged surface seam: its `SurfaceRoute`s mounted under `/surface/<name>`. A **durable** surface (Slack) declares two-phase delivery (`post` then `attach`) the poller drives — declaring `post` is what marks it durable, and admission registers every turn entering its conversations for delivery, whoever admits it; a **live** surface (web) tails the hub over SSE in its own route. Core's CLI is the built-in live twin. |
| `credentials` | Named BYOK slots the workspace must fill (drives onboarding); `ufoctl init` seeds a slot from its upper-cased env var (`SLACK_BOT_TOKEN` → `slack_bot_token`), and the operator fills or rotates one anytime with `ufoctl credential set <slot>`. A member fills one in chat through `request_credentials`: the owner's ask seals which slots they will fill, a capable surface prompts for each value privately, and fulfillment verifies the seal before the encrypted store takes it — the plaintext never enters the transcript or the sandbox. |
| `onboarding` | Steps contributed to the workspace/pack onboarding flow. |
| `models` | Model providers behind `ModelClient` (OpenRouter, local runtimes). |
| `carriers` | Sandbox carriers — Docker, E2B, remote runners; core's default is a local temp-dir carrier. |
| `indexes` | Index backends for memory/source retrieval (turbopuffer); the dialect-native default (SQLite FTS5 + local cosine, Postgres tsvector + pgvector) ships as the base-pinned `index_default` extension registering name `"default"`, which core resolves when `memory.index_backend` is unset. |
| `embeds` | Embedding backends behind `EmbedClient`, selected by `memory.embed_backend`; OpenAI text-embedding-3-large ships as the base-pinned `embed_openai` extension registering name `"default"`. |
| `hubs` | Stream hubs for multi-instance deploys (Redis). |
| `cdp_providers` | CDP transport backends the one BUA browser engine (an extension, not core) connects, selected by `[browser] cdp_provider` (default `sandbox_cdp`): core's `sandbox_cdp` wraps the `BROWSER_CDP_URL` endpoint in a static lease; browserbase mints a fresh hosted session per turn. A provider mints a per-turn `CdpLease` the loop releases at turn end. The BUA engine is the browser extension, so only the transport is a core seam, never the engine. |
| `auth_proxies` | The fallback credential backend for a feed-sync provider no installed broker claims: the sole installed backend is automatic; `[connectors] auth_backend` selects one when several are installed and must name a registered choice. `direct` BYOK reads a member-added key host-side from the credential store, never reaching the sandbox. A brokered provider resolves through its own broker's `credential`, never this seam; folder sources need none. |
| `search_providers` | Web-search backends the research extension's tools call, selected by `[research] search_provider`. A backend runs host-side — it reads its BYOK key in-process and reaches its API over async HTTP, so the key never enters the sandbox — and answers a search query; `supports_fetch` marks whether it also fetches a URL's content (Exa's search + contents does; an answer-with-citations backend need not, and the `fetch_url` tool gates on it). Core ships no default: every backend is an extension, and the research extension `requires` this seam. |
| `requires` | Sub-seams this extension consumes from another (the browser pack `requires` `cdp_providers`); `serve` resolves each at boot and fails loud — naming the extension and the seam — if the backend is absent, unknown, or unkeyed, so a missing dependency stops startup rather than the first tool call. |

`ExtensionContext` (capability-scoped, handed to every handler): workspace-scoped store access,
`credentials.get(slot)`, the selected `index`/`embed` backends, `pages` (the `PageFeed` replaying
source-page changes under a resumable cursor), `transaction()` over the extension's own tables,
`invoke(agent, input, conversation=...)`, metered `model.complete(...)`/`model.turn(...)`,
`schedule(job)`, `trajectories.read(...)` (transcript/turn evidence), and
`agents.propose_change(...)` — the governed promotion path: an extension never edits agent config
directly; it opens a proposal (prompt, skills, tool grants) that applies through the same
grant/approval flow chat uses. This is what makes a full self-improvement extension expressible —
mine trajectories, evaluate candidates via `invoke`, promote through `propose_change` — not just
prompt files on disk. Extensions never see raw DB handles or other workspaces.

### Extension store

Extensions are Python packages. A deploy may enable the extension store — a registry index that
the CLI (and, when granted, an agent in chat) searches and installs from: `ufoctl ext search /
install / remove`. Installs pin version + digest and are recorded in the bundle lockfile; with the
store disabled, a deploy runs only what its bundle ships.

### Packs

A **pack** is the deploy's product configuration as one activation. It is a workspace member under
`packs/<name>/` whose `ufo.pack` entry point returns a `Pack` — the set of installed extensions
it bundles (by manifest name) plus any pack-level skills and onboarding steps of its own. A deploy
names the active pack in config (`[pack] name`, one active pack); activating it narrows the active
manifest set to exactly the bundled extensions' manifests plus one manifest carrying the pack's own
contributions, so the pack fully determines what comes up — a coherent config online together. Packs
are discovered through their entry-point group exactly as extensions are, import only `ufo.sdk`
(the same CI gate), name only installed extensions (an uninstalled one fails loud at boot), and own
no tables — a pack's pack-level skills and onboarding ride the same manifest-consuming paths an
extension's do. The lockfile is the installed+verified universe; a pack selects the active subset,
so pack integrity derives from the pinned extensions it names. Absent config, the deploy runs the
unnarrowed set (the lockfile's pins, or every discovered extension in dev). The flagship is
**assistant** — memory (with its index and embed backends), the browser pack (its BUA engine over
the default `sandbox_cdp` transport), brokered connectors (Composio; Pipedream for Gmail), and web
research (the research tools over the Exa search backend).

## Surfaces

Core owns **one surface seam**, not every surface. A surface is trusted infrastructure — it asserts
a member's identity and admits turns as that member — so its `SurfaceContext` is deliberately
privileged (distinct from the scoped extension context): **admit** an inbound message onto the
durable turn queue through the member-only admission capability, consuming any pending one-time
pause, with its ambient `TurnContext` — the sender and IANA timezone the
surface knows, which the engine renders as the `<context>` tag (the admission moment, local when a
timezone is known; sender) before each member inbound — **identity** resolution (an external id → member + conversation,
linking a `surface_identity` on first contact — `join_member` also creates the member when a
channel-verified email matches the workspace's own domain, the owner's email domain, so only the
owner onboards through provisioning — and `adopt_identity` to span a member across
surfaces), plus `tail`/`turn_owner`/`spend_rollup` for a live view. An extension registers a
`surfaces` Manifest point; core mounts its `SurfaceRoute`s under `/surface/<name>`, each bound to the
one context. The seam supports two delivery modes; a surface uses only the subset it needs:

Jobs and evals receive the separate internal `invoke` capability, which never consumes a member's
pause. Both capabilities delegate to the same admission workflow, so spend enforcement, turn
allocation, delivery registration, and enqueue recovery remain one implementation.

- **Durable** (Slack) — the member is elsewhere; declaring `post` is what marks the surface
  durable, and admission registers a writeback for every turn entering its conversations — a
  surface ingest, a scheduled fire, an extension invoke alike. A `WritebackPoller` delivers the
  terminal reply with its cost, tokens, cache-read percentage, model, and reasoning effort
  at-least-once (the hub is
  lossy), two-phase: `post` returns the reply's durable
  reference (recorded before any upload), then `attach` streams the turn's shared files into the
  conversation, with rich rendering. Recovery resumes `attach` without re-posting; attachment
  delivery is at-least-once and may repeat after a crash between upload and the delivered commit.
  A turn that ended by asking (`ask_user` as its final act) rides the writeback as a structured
  `question`, so the surface can render the options as its own answer
  affordance (Slack buttons) whose use admits the answer as the conversation's next turn — the
  first answer wins the idempotent admit, and `turn_inbound` is how the surface confirms which
  landed before rewriting the affordance; a durable surface may also `tail` a turn it admitted for
  ephemeral live feedback (Slack's native thread status), never for delivery.
- **Live** (web; core's CLI is the built-in twin) — the member's connection is held open, so
  admission registers nothing and the surface delivers by `tail`-ing the turn's frames off the hub
  over SSE in its own route. The poller only processes turns that registered a writeback, so it is a
  no-op for a live surface — the efficient downgrade, not a second seam.

| Surface | Home | Delivery | Identity | Conversation key |
|---|---|---|---|---|
| CLI | core | live (hub tail) | member token | session (private) |
| Web | `extensions/web` | live (hub tail) | web session → member (adopted from CLI) | session (private) |
| Slackbot | `extensions/slack` | durable (writeback) | Slack user → member (linked; a Slack-confirmed same-domain email joins as new) | channel:thread_ts (shared) |

Two-way attachments stream end to end, never buffering a whole file: an inbound Slack file streams
from `url_private` into the conversation's workspace before the turn runs; a shared file
(`share_file` → a `shared_artifact` record) streams from the blob store to Slack's chunked
external-upload API, into the conversation's thread (Slack forbids threading on a reply's ts). `surface_identity` and `conversation.surface`
are open namespaces validated by surface registration, not a fixed enum.
Slack renders each terminal's accounting and model metadata as the reply's final context block.

Shared surface requests authenticate their workspace before core binds it. Slack uses canonical
event and interactive URLs: the untrusted team id selects one unique `surface_installation`, its
workspace's signing secret verifies the original bounded bytes, and only then does the request bind
that workspace. Unknown installations and bad signatures share one rejection. URL verification has
no team id, so its bounded challenge echoes without binding a workspace or marking it connected.
Surface identities and conversation keys include `workspace_id`. Durable writeback enumerates a
bounded set of workspace ids through the owner connection, then binds each before reading or
delivering any tenant data.

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

One declarative file, `ufo.toml`: Postgres URL, blob store (filesystem root or S3 endpoint),
model keys (env refs), enabled extensions + versions, the active pack (`[pack] name`), surface
config (Slack app), sandbox carrier, stream hub, OTLP export target, extension-store
toggle, spend defaults.

## Running it

The developer surface is a pip-installable CLI running as a **host process** — ufo is never
containerized for development:

```bash
uv tool install ufo        # the Python package is the primitive; brew formula = later wrapper
ufoctl init                   # writes ufo.toml; onboards workspace + first owner + agent + model key
ufoctl serve                  # one process: surfaces + workers + jobs + proxy — SQLite, zero services
ufoctl chat                   # a client; connects to serve's URL from ufo.toml
```

Dev defaults are zero-services: SQLite, filesystem blobs, in-process hub. Docker enters only for
sandboxes (U2+); Postgres (the checked-in compose or an existing instance) enters only for deploys
and the Postgres half of the test matrix.

`serve` talks to the host Docker daemon; sandboxes are **sibling containers**, never children.
Docker is required for sandboxes, not for running ufo. `chat` is only a client — if nothing
listens it says to run `ufoctl serve`; there is no embedded auto-start. A containerized `serve`
(the `ufoctl bundle` deploy) spawns siblings via the mounted Docker socket, or uses a remote
carrier extension and needs no host Docker at all.

## Scale-out

Scale-out is a deployment mode, not a feature: the same bundle with more instances. Nothing in core
is instance-aware except the boot guard, its heartbeat, and the executor-recovery sweep; the only
extension involved is the Redis hub.

| Concern | Multi-instance behavior |
|---|---|
| Database | Postgres required; SQLite is single-instance-only. |
| Turns, queues, jobs | DBOS coordinates through Postgres: any instance pulls. Each process's DBOS executor id is its instance id, so in-flight work is attributable to a heartbeat: the executor-recovery sweep re-dispatches workflows whose executor has no fresh `runtime_instance` row, and never touches a live peer's — recovering a live workflow would double-execute it. |
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
`ufoctl bundle` produces a runnable artifact (OCI image + pinned config + lockfile) — the same
bundle installs OSS, on-prem, or hosted.

## Example extensions (the API's acceptance tests)

| Extension | Points it exercises |
|---|---|
| OpenRouter (any model router) | models |
| Slack surface (ingest + writeback + attachments) | surfaces, credentials, skills |
| Page alerts (chat-bound watches over synced pages, off-turn classify + alert turn) | tools, hooks (page_change) |
| Brief pipeline (typed outline → draft → critic stages the agent chains) | subagents, skills |
| Composio / Pipedream connector brokers | connectors, routes (OAuth) |
| Docker, E2B | carriers |
| Redis stream hub | hubs |
| turbopuffer index | indexes |
| GitHub / Asana feed-sync sources | sources, credentials, auth_proxies (`direct`) |
| Agent-guided education / onboarding | onboarding, tools |
| Scheduled tasks (cron / one-time) | jobs, invoke, tools |
| GH code review on PR + auto-merge | routes (webhook), credentials, invoke, tools |
| Service self-improvement / bug-fixing from o11y | sources (o11y), jobs, trajectories.read, invoke (evals), agents.propose_change |
| Security review | tools, subagents |
| gbrain-style memory (source → condense to markdown + graph) | memory, sources, hooks (page_change) |
| CRM / ATS | connectors, sources, hooks (page_change), tools |
| Websites | tools (sandbox serving), routes |

Packs (activation bundles, not code — see Packs): **assistant** bundles memory, the browser pack
(its BUA engine over the default `sandbox_cdp` transport), brokered connectors, and web research
(the research tools over the Exa search backend) (the flagship); **chief-of-staff** bundles
brokered connector grants plus feed sync (Google Meet transcripts and Gemini smart notes, Slack, a
folder-synced state repo) with memory and the graph, the Slack surface, scheduling, page watches,
todos, workspace skills, and self-improvement behind four pack skills (`sync`, `prep`, `triage`,
setup); **startup** and
**support bot** name the extensions plus pack-level onboarding a product needs (YC/fundraising docs
and search; knowledgebase + keys onboarding with the websites plugin). Each activates one coherent
config, no code of its own beyond what it references.

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
