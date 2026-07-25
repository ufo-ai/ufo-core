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
| Persistence | One async-SQLAlchemy schema over **SQLite by default** (aiosqlite, WAL — zero services for dev) and **Postgres for deploys** (asyncpg); alembic migrations are the single schema source, dialect-neutral (integers for money/tokens; dialect-only types live inside IndexBackend impls). Plus a pluggable blob store (transcripts, compaction records, sandbox workspaces, shared artifacts): **local filesystem by default**, S3-compatible for deploys — the S3 API is the cloud-portability seam. Every row carries `workspace_id`; one shared fleet serves every workspace, scoping each request and turn to its `workspace_id` under row-level security. |
| Durable execution | DBOS on the same database as the schema (SQLite dev / Postgres deploys): a turn is a durable workflow, a subagent a child workflow; queues, async cancel, crash recovery. DBOS-on-SQLite is verified in U1 — fail loud, never silently fall back to requiring Postgres. Dequeue poll interval and system-DB retention are configured from day one. |
| Streaming | Durable terminal frames in Postgres; live token deltas through a hub interface — in-process in the single-process default, a Redis hub extension for multi-instance deploys. A lost delta costs a redrawn token, never correctness. |
| Topology | `ufoctl serve` is one process on one event loop: surfaces + DBOS workers + jobs. Everything is async-native — a blocking call stalls the whole deploy, so blocking-in-async fails lint. Scale-out = more instances plus a shared hub. |
| Sandbox | A local temp-dir carrier is the core default: no kernel isolation (a raw shell reaches the host FS — only tool arguments are workspace-guarded), and egress is proxy-scoped/metered only for clients that honor the proxy env, not kernel-enforced (model keys still stay fail-closed via the sentinel). It is the development / trusted-input default; use Docker or E2B (carrier extensions on the `carriers` point) for untrusted input, isolation, or multi-tenant deploys. |
| Models | Model providers are an extension point; core ships Anthropic + OpenAI direct clients behind one `ModelClient` interface. Bedrock Mantle and OpenRouter ship as extensions. |
| Observability | OpenTelemetry APIs only in product code; the OTLP export target (Datadog, …) is deploy config. No vendor SDK in core. |
| Kubernetes | Absent from core by construction. The enterprise offering later wraps core with k8s (principle 3); nothing in core may assume or import it. |
| CLI | One CLI: `ufo` (`chat`, `serve`, `bundle`, `ext`, admin verbs). |

## Workspace model

Tables (all keyed by `workspace_id`, `created_at`, `updated_at`):

| Table | Owns |
|---|---|
| `workspace` | The team unit: name, config digest, `included_seats` (the silent auto-seat allowance), `seat_limit` (the grantable ceiling) — both NULL = unlimited, the shape every deploy without a billing extension keeps. |
| `member` | A human. The owner is the earliest member by (created_at, id) — no role column. `seated_at` marks a seat — a member the agent answers: auto-seated at creation while an included seat is open, granted beyond that only by the owner in chat (billed overage, asked via a seat approval turn in the owner's conversation), gated at admission and per round. Surface identities link here (Slack user id, CLI token, web session) — one human, many surfaces, one memory subject. |
| `surface_installation` | A chat installation's unique external identity → workspace binding. Shared ingress uses it only to select a candidate credential, authenticates the original request bytes, then binds that workspace. |
| `agent` | A configured agent: name, prompt, model policy, granted tool set, skill packs, memory scope. |
| `grant` | Agent ← capability binding: a connector account, a credential slot, a tool group. Records grantor, when, via which conversation. Created through chat; `connect_account` leaves a terminal private handoff that memoizes one URL for the persisted turn speaker until expiry, so the OAuth URL never enters a shared transcript. Tool-visible extension authorization requires the speaker's private audience. A grant is private to its grantor by default; the model sets `shared` at connect time from the conversation's intent, and the grantor (or owner) flips it later through the `connector` object. Account resolution (via `ToolContext.connector_accounts`) admits the acting member's own grants plus shared ones — the acting member is the speaker, or `on_behalf_of_member_id` for a speakerless scheduled fire or subagent; egress proxy rules stay agent-scoped (a broker grant's host carries no token). |
| `credential` | BYOK secrets, encrypted at rest. Slots are declared by extensions; values are workspace-scoped. |
| `conversation` | Surface context ↔ queue key (Slack thread, CLI session, web session). Private to its creating member unless the surface is shared (a Slack channel is shared by construction). |
| `turn`, `transcript` | The loop's durable log: lifecycle, per-member-inbound `speaker_member_id` distinct from the conversation's disclosure member, full-conversation transcript + compaction records. Timer, system, and subagent turns carry no speaker authority. `on_behalf_of_member_id` is the initiating member a speakerless scheduled fire or subagent acts on behalf of for capability use — distinct from `speaker_member_id` (which gates granting) and from the conversation's disclosure member; scheduled fires derive it from `scheduled_task.created_by_member_id`, subagents copy it forward at spawn. Sub-turn steps — each model round, tool call, and compaction — are DBOS's own `operation_outputs` step log, memoized so a crash-recovery re-run replays completed work instead of redoing it. |
| `memory_item` (memory extension) | Memory: subject = member or `shared`. The memory extension owns this table via its own migration; the index backend owns `chunk`. |
| `ledger` | Metered usage: every model and tool call, priced. |
| `spend_cap` | Caps by scope (`workspace` \| `member` \| `agent`), dimension, window; `reject` or `park` on breach. |
| `job` | Recurring/one-time background work (source sync, page-change fan-out, extension jobs). |
| `scheduled_task` | Member-owned recurring agent invocation with optional UTC expiry, enforced before invocation. Each recurring turn carries the exact claimed UTC occurrence; when its following occurrence reaches expiry, runtime adds a continuation check-in to the completed work. One-time workflow pauses keep their raw resume prompt. |

## Agent loop

One workflow: inbound → admission (identity, spend preflight) → queue row → worker turn → steps →
terminal frame. A client's wait always ends — the terminal state commits on the failure path too.

- **Tool calling** — typed registry; per-call metering; results bounded before hitting the model.
- **Skill loading** — skills are folders of files (SKILL.md + assets), mounted into the sandbox on
  `load_skill`. **A skill ships with the thing it teaches**: core ships exactly two folder skills —
  `sandbox`, `delegation` — teaching core's own builtins, and generates a `model-catalog` skill from
  the model registry at serve so the models a member can pin stay documented from the same records
  the runtime routes and bills on (RFC 0018); an extension's skills ride its manifest (the `memory`
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
  auto-injected each turn through a `user_prompt_submit` hook. It also provides the typed
  `memory_search` seam: core resolves the conversation's member under workspace scope and routes
  a consumer to the extension's one search workflow. Scheduled tasks require this seam and search
  after admission and before the run's first model round. Recall carries the
  gbrain richness: per-kind recency decay against source information time, falling back to commit
  time (fact/preference/decision/event/task half-lives, fact items only), a type-diversity cap so no
  class dominates, supersession suppression, and an episodic→topic pointer excluded from
  auto-injection. It rides two core selection seams: the
  **index backend** behind one lexical/vector interface and the **embed backend** behind
  one batched-embed interface. The dialect-native index (SQLite FTS5 + local cosine, Postgres
  tsvector + pgvector) ships as the base-pinned `index_default` extension and OpenAI embedding as
  the base-pinned `embed_openai` extension; turbopuffer is a drop-in index alternative. Source
  pages reach recall through the memory extension's own page-index job over the core `PageFeed`.
- **Minimal built-in tools** — `bash`, `read`, `write`, `edit`,
  `ask_user`, `request_credentials`, `spawn_subagent`, `load_skill`, `share_file`, and the five
  object verbs (`object_list`/`get`/`explain`/`apply`/`delete`) over extension-registered kinds
  (RFC 0017) — one generic CRUD surface instead of per-extension config tools. Object lists accept
  exact first-class-field filters and field ordering; gets return the kind's readable spec.
  Everything else arrives via extensions.
  Two tools where one would do is a defect. `share_file` ports the shipped design: byte custody in
  the blob store, a TTL-bound token URL served by core's artifact route — no token, no bytes.

## Sandboxing

Every turn executes tools in a per-conversation sandbox: Docker container from a pinned image
(baked toolchain), default-deny network egress with exactly one route out — the sandbox proxy.
An off-cluster carrier reaches the proxy only over TLS; the per-turn proxy token is never sent on
plaintext transport.

**The sandbox proxy is core, not an extension** — it is the enforcement point for three core
invariants: **sentinel swap** (processes inside see placeholder credentials; the proxy swaps real
values onto the wire, so raw secrets never enter the sandbox), **grant scoping** (authenticated
calls use only accounts the agent's grants cover — principle 4 enforced at the wire), and **wire
metering** (every model/API call made from inside the sandbox lands in the ledger). An extension may
declare `sandbox_internet`; its deploy's live turns may then reach globally routable public IPv4
through a metered opaque tunnel. DNS is pinned and every IPv6, loopback, private, link-local,
reserved, multicast, or shared-space answer is refused. Tokenless and ended turns cannot use public
internet. The owner may narrow that deploy capability per agent through the agent object's
`internet_access_allowed`; the proxy snapshots it into that turn's cached rules.
Extensions never register raw network rules; the proxy's rewrite rules are *derived* from their
manifests — sandbox internet, a credential slot, a connector, or a model provider implies its
scoping, injection, and metering rules. Declare, don't open. The enterprise k8s layer later ships
its apiserver-rewrite / token-mint module through this same rewriter seam.

The working directory mounts
from the blob store — a bind mount on the filesystem backend, the sandbox-fs design on S3 — and the
invariant holds on every backend: the sandbox reaches only the conversation's `workspace/` subtree;
transcripts and compaction records live above it, framework-only. The workspace is the truth and
the container is disposable cache — carriers create-or-attach, and a reaper reclaims idle
containers. On S3, a dedicated unprivileged s3fs daemon refreshes through its ECS metadata
interface: a local relay forwards its opaque mount token to the sandbox proxy over the carrier's
isolated bridge or TLS route, which mints a one-hour STS session with an inline policy for that
conversation's `workspace/` prefix. The signed token names the current turn; every refresh checks
that the turn remains live, so one turn can outlive any STS session while an ended turn cannot mint
another. The token and redeemed credentials never enter an agent process.
Carrier interface:
`create / exec / mount / route / destroy` — a local temp-dir carrier is core's default; Docker and
E2B implement it as extensions on the `carriers` point.

## Extension system

An extension is a Python package exposing one entry point (`ufo.extension`) that returns a
`Manifest`. Extensions import only the public SDK (`ufo.sdk`); a CI gate forbids reaching
into core internals.

Manifest registers (each optional):

| Point | Contract |
|---|---|
| `tools` | Typed tool defs + handlers; appear in agents' granted tool sets. A `profile_only` tool never reaches a main agent — only the subagent profiles that name it (the raw browser surface reaches main agents solely through `browser_task`/`wide_browse`). |
| `objects` | Workspace-object kinds (RFC 0017): name, one-line description, model-facing guidance, spec model (`extra="forbid"`, JSON-round-trippable, no secret fields — boot-gated), and store handlers over the extension's own tables. Core's five `object_*` verbs validate the YAML envelope and spec, then dispatch to the kind under its own ExtensionContext; domain refusals live in the handlers. A kind whose rows belong to a member (connector, source, scheduled_task) is built on the `MemberOwnedObjects` base: it declares each row's owner (`member_id`, `shared`) and the base — not the handler — enforces per-member access, so no kind can ship ungated. A row is visible only when shared, owned by the acting member, or the caller is the workspace owner; an invisible row is not-found to reads and to mutation resolution (no existence oracle), and only the owner or workspace owner may mutate. |
| `subagents` | Typed subagent profiles. |
| `prompt_sections` | Capability sections a pack contributes to the agent's system prompt, rendered into the shell's `{{sections}}` slot ordered by name — a pack's rules (web search, browsing, office docs) reach the agent without core naming the capability. |
| `skills` | Skill folders (SKILL.md + bundled scripts/assets) contributed to the loadable set; the loader parses each into the registry `load_skill` and the `{{skill_index}}` consult, mounted into the sandbox under `.skills/<name>/` beside core's own three. A skill script imports nothing from ufo (it runs in the sandbox) — a CI gate holds that boundary. |
| `connectors` | One brokered provider per declaration: its OAuth descriptor (grant flow), member-facing label, and `ConnectorBroker` — catalog, server-side execute, feed-sync credential. `serve` merges every declaration into the one `ConnectorRegistry`; the `connectors` extension's broker-generic dynamic tools (list/describe/search/call) dispatch through it, and the sync runner resolves a brokered provider's feed-sync `Credential` through its own broker. A broker may also declare an **open namespace** (`connector_resolver`): the connect flow and registry resolve any slug no connector explicitly registered through it, so the connectable set is drawn from the broker's whole live catalog rather than a fixed list. The namespace validates a slug against that catalog when the member connects, and claims only what the deploy can actually serve — a typo, a toolkit the broker holds no managed credentials for, one cataloguing no tools, and one whose tools cannot do the job its service exists for all fail loud at the connect request instead of minting a consent link that dies later. The first three are read off the catalog record; the last is a judgement the broker extension records per slug, because the catalog's scope metadata is too inconsistent to derive it (Composio's `BANNED`, with the evidence in `docs/composio-provider-coverage.md`). It is the catch-all, so at most one deploy-wide (two fail loud). Two broker extensions ship — `composio` (the open namespace: any of its brokered toolkits by slug, plus an explicit connector only for an exception its catch-all can't serve, the `github` CLI) and `pipedream` (Gmail: Google blocks restricted Gmail scopes on Composio's shared client; the deploy's own Google OAuth client rides Pipedream Connect). **A broker brokers auth by PROXY: every call goes through the broker (its execute API for tools; a proxying transport for feed-sync source HTTP; a request forwarder for sandbox CLI HTTP) carrying `(external user, connected account id)` — the broker holds the provider token and injects it server-side; the token is NEVER exposed to us. A connector grant stores only the connected-account id; the confused-deputy check reads the account's owner metadata, never a token. Broker grants derive NO egress InjectionRule — the sentinel→key swap (§Sandboxing) is ONLY for user-supplied BYOK `credentials` keys. A connector may instead declare a `cli` credential (github → `GH_TOKEN`): the sandbox exports the grant's sentinel in that env var, and the egress proxy forwards a request carrying it through the broker under the granted account (a `ForwardRule`) — still auth by proxy, gated on the acting member and turn liveness, metered like any granted host.** Files cross the broker seam as references, never bytes: a tool's file outputs come back as presigned URLs on the broker's file store (every Pipedream run rides a File Stash; Composio marks `{name, mimetype, s3url}`), a `workspace_file` input stages to a broker-minted presigned PUT, and the sandbox runs both transfers itself into/out of `workspace/` — a grant additionally admits (never injects) its connector's declared `transfer_hosts` (or the open namespace's, for a slug it brokers), so file bytes never cross the serve process. |
| `sources` | Data-feed backends: `sync(cursor) -> pages` run as jobs; pages land in memory/knowledge via the derivation pipeline. A source is private to its registering member by default — its rows carry a recall subject the sync driver stamps onto every page — and flips to workspace-shared only by the registrar's (or owner's) opt-in, which restamps its live pages; recall's subject filter is the runtime check. Each provider is a backend factory receiving only its declaring extension's scoped credential access; extensions cannot otherwise express an authenticated direct source without exposing encrypted secrets. Core ships only `folder` (local files). Connector source providers live in `extensions/sources`, built on the read-only REST connector framework core exposes through `ufo.sdk.sources`; each stream declares provider-record creation/update paths independently from its sync cursor, and each connector resolves a provider `Credential` through the pluggable **auth-proxy** seam, never importing a broker. |
| `hooks` | Reactive lifecycle handlers on Claude Code's taxonomy, scoped like a job. Seven fire on the turn loop — `pre_tool_use`/`post_tool_use`/`post_tool_use_failure`, `user_prompt_submit`, `stop`, `pre_compact`/`post_compact` — as a runtime policy filter over the tools grants already admit (observe, deny, modify, or inject), never a second grant path. The eighth, `page_change`, is the data-plane seam (data → memory): a core batched cursor-runner replays each changed source page to a consumer's hook off that extension's own cursor — the path the memory indexer and knowledge-graph extractor ride. (Claude Code's session/permission/subagent-stop/notification events have no producer here and are not members until one lands with a consumer.) |
| `jobs` | Recurring/one-time background work. |
| `routes` | HTTP endpoints under `/ext/<name>/` (webhooks, OAuth callbacks, plugin UIs). |
| `surfaces` | A chat surface on the one privileged surface seam: its `SurfaceRoute`s mounted under `/surface/<name>`. A **durable** surface (Slack) declares two-phase delivery (`post` then `attach`) the poller drives — declaring `post` is what marks it durable, and admission registers every turn entering its conversations for delivery, whoever admits it; a **live** surface (web) tails the hub over SSE in its own route. Core's CLI is the built-in live twin. |
| `credentials` | Named BYOK slots the workspace must fill (drives onboarding); `ufoctl init` seeds a slot from its upper-cased env var (`ACME_API_KEY` → `acme_api_key`), and the operator fills or rotates one anytime with `ufoctl credential set <slot>`. A member fills one in chat through `request_credentials`: the owner's ask seals which slots they will fill, a capable surface prompts for each value privately, and fulfillment verifies the seal before the encrypted store takes it — the plaintext never enters the transcript or the sandbox. An extension tool may instead seal provider authorization state to its own declared slot and the speaking owner, then fulfill that seal with the resulting credential (the Slack OAuth install mints the bot token this way at its callback); URL/code handoffs therefore stay in chat while provider secrets do not. Extensions may read their declared slots and compare-and-swap an existing value when an upstream client refreshes it; only an owner-sealed or operator path creates a value. |
| `onboarding` | Steps contributed to the workspace/pack onboarding flow. |
| `models` | Model providers behind `ModelClient` (OpenRouter, local runtimes). |
| `carriers` | Sandbox carriers — Docker, E2B, remote runners; core's default is a local temp-dir carrier. |
| `indexes` | Index backends for memory/source retrieval (turbopuffer); the dialect-native default (SQLite FTS5 + local cosine, Postgres tsvector + pgvector) ships as the base-pinned `index_default` extension registering name `"default"`, which core resolves when `memory.index_backend` is unset. |
| `embeds` | Embedding backends behind `EmbedClient`, selected by `memory.embed_backend`; OpenAI text-embedding-3-large ships as the base-pinned `embed_openai` extension registering name `"default"`. |
| `hubs` | Stream hubs for multi-instance deploys (Redis). |
| `cdp_providers` | CDP transport backends the one BUA browser engine (an extension, not core) connects, selected by `[browser] cdp_provider` (default `sandbox_cdp`): core's `sandbox_cdp` wraps the `BROWSER_CDP_URL` endpoint in a static lease; browserbase mints a fresh hosted session per turn. A provider mints a per-turn `CdpLease` the loop releases at turn end. The BUA engine is the browser extension, so only the transport is a core seam, never the engine. |
| `auth_proxies` | The fallback credential backend for a feed-sync provider no installed broker claims: the sole installed backend is automatic; `[connectors] auth_backend` selects one when several are installed and must name a registered choice. `direct` BYOK reads a member-added key host-side from the credential store, never reaching the sandbox. A brokered provider resolves through its own broker's `credential`, never this seam; folder sources need none. |
| `search_providers` | Web-search backends the research extension's tools call, selected by `[research] search_provider`. A backend runs host-side — it reads its BYOK key in-process and reaches its API over async HTTP, so the key never enters the sandbox — and answers a search query; `supports_fetch` marks whether it also fetches a URL's content (Exa's search + contents does; an answer-with-citations backend need not, and the `fetch_url` tool gates on it). Core ships no default: every backend is an extension, and the research extension `requires` this seam. |
| `memory_search` | A named workspace-scoped memory search provider (`default` is selected). Core resolves a conversation to its member before calling it; consumers declare `requires=("memory_search",)`, and boot fails unless exactly one default provider is active. |
| `requires` | Sub-seams this extension consumes from another (the browser pack `requires` `cdp_providers`); `serve` resolves each at boot and fails loud — naming the extension and the seam — if the backend is absent, unknown, or unkeyed, so a missing dependency stops startup rather than the first tool call. |

`ExtensionContext` (capability-scoped, handed to every handler): workspace-scoped store access,
`credentials.get(slot)` / `credentials.rotate(slot, expected, value)`, the selected `index`/`embed`
backends,
`pages` (the `PageFeed` replaying
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
research (the research tools over the Exa search backend). **yc** is the founder workspace: the
authenticated YC CLI, Bookface Knowledge Base and Startup Library sources, memory and graph
derivations, documents, scheduled tasks, todos, and pack-level founder-operations and diligence
skills.

The YC extension's `yc_auth` tool runs that owner-sealed device flow: the member receives the YC
URL and code in chat, approves in the browser, then the encrypted credential is available to the
CLI, tools, and sources without entering the transcript. One owner-authorized YC identity serves
the workspace, and every provider operation exposed by the extension is read-only.

## Third-party extensions

A third-party extension is JS, declared by a static `ufo.manifest.json` core reads without
executing any code — contribution points plus lazy activation, never a top-level import — and runs
isolated from core in **`runner`**, a standalone service alongside `control/`, never part of
`ufoctl serve`'s one process. First-party (bundled) extensions are unaffected: they keep the
in-process Python mechanism above, unchanged. The line is provenance (reviewed-and-pinned vs.
store-installed-or-locally-loaded), not a rewrite of what already works. RFC 0015 carries the
rationale, alternatives, and open questions; this section is the settled model.

| Concern | Mechanism |
|---|---|
| Manifest points | `tools`, `subagent_tool_grants` (scoped to the manifest's own declared tools), and credential slot declaration — no other Manifest point. `surfaces` (identity-asserting) and every backend/SPI point (`indexes`, `embeds`, `models`, `carriers`, …) stay first-party-only. A third-party extension reaches an external API either uncredentialed (any public host, never a brokered connector call) or via a declared, injected credential (below). |
| Manifest safety | `untrusted` and `side_effecting` are forced `true` for every third-party tool, never author-controlled. `args_schema` is raw JSON Schema, validated through a dynamically-built `BaseModel` wrapper — no JSON-Schema-to-pydantic translation. Publish/install validation, re-run at every boot/upgrade, rejects a tool or extension name colliding with a built-in or another installed extension's, a `subagent_tool_grants` entry naming a tool the manifest doesn't declare, and a duplicate credential name; a credential slot key contains no colon, so the composite key below stays unambiguous. |
| Isolation | `wasmtime`, embedded directly: `Config.consume_fuel` (50,000,000 units) and `epoch_interruption` (~5s) bound CPU; `Store.set_limits` bounds memory (64MiB) and every other resource it takes a limit for, each fixed to one call's single instantiation. One `Store` per call, discarded after. The guest declares no import except the two `runner` defines (HTTP; a `ctx.store`/`ctx.model` callback), everything else trapped. A dispatch request may ask only for *less* than these defaults, and its payload is length-capped in `runner`'s own memory before reaching the guest. |
| Egress / credentials | `runner` defines one HTTP host import; core resolves the call's declared credential slots at dispatch and sends `runner` a short-lived, call-scoped list of injection rules (host, header, sentinel, real value), resolved by a composite `f"{extension_name}:{slot_name}"` key into the existing `credential` table's `slot` column (no schema change) so two extensions never collide on a slot name. `runner` substitutes the real value only when a request's target host, scheme (`https://` only), and header all match a declared rule; anything else is sent with the sentinel unsubstituted. A guest reaches no loopback, link-local, private, reserved, multicast, or shared (CGNAT-style `100.64.0.0/10`) address regardless of its rules, and the validated address is pinned through the connection (no DNS rebinding). Public egress is otherwise unrestricted by design. The response is redacted (every injected value stripped) and read under a byte cap and wall-clock timeout outside the WASM limits, off `runner`'s event loop. Every call is metered as it completes — matched rule or not, success or error — under the `egress` dimension by default. |
| State scope | `ctx.store` and other durable state key on the extension's stable **name**, never the compiled digest — a digest says which code to run, not whose state it is. `ctx.store` writes are size- and count-bounded per call; `ctx.model` calls are spend-checked before the call (the same `SpendEvaluator` turn admission uses), not only metered after. |
| Scope integrity | `runner` authenticates to core as the fleet, so a callback's claimed `(workspace_id, extension_name, extension_digest)` isn't taken on faith. Core mints an opaque dispatch capability at `dispatch_tool` time, scoped to that call and valid for its whole duration (one call makes several callbacks); every callback must present it, and core accepts one only while that dispatch is open. |
| Module distribution | Compiled at `ufoctl ext publish` (JS → WASM); content-addressed like `ExtensionPin.digest` and stored in the existing S3-compatible blob store. `runner` fetches and caches by digest on first use. |
| Deployment | Plain stateless processes behind a load balancer — an autoscaling group, VM scale set, on-prem pool, or a container scheduler if one is already in play. No Kubernetes requirement, matching core's own. |
| Local dev / test mode | `ufoctl ext dev [--remote <url>]` runs the extension as a plain local Node process (WASM only enters at `ext publish`) against a disposable workspace with real grants. Every credentialed call routes over the channel through the same host/header-checked injection `runner` uses; no live workspace's data is ever reachable. |

## Surfaces

Core owns **one surface seam**, not every surface. A surface is trusted infrastructure — it asserts
a member's identity and admits turns as that member — so its `SurfaceContext` is deliberately
privileged (distinct from the scoped extension context): **admit** an inbound message onto the
durable turn queue through the member-only admission capability, consuming any pending one-time
pause, with its ambient `TurnContext` — the sender and IANA timezone the
surface knows, which the engine renders as the `<context>` tag (the admission moment, local when a
timezone is known; sender) before each member inbound; a message arriving while the conversation's
newest turn is still live lands on the conversation's inbound queue, which the engine drains into
that turn at each round boundary as separate `<context>`-tagged messages — the terminal commit
refuses to close over a non-empty queue, so one reply answers everything pending — **identity** resolution (an external id → member + conversation,
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
  first answer wins the idempotent admit, and `admitted_body` is how the surface confirms which
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
| Debug | `extensions/debugger` | live (hub tail) | gateway bearer whose email domain is `OPERATOR_EMAIL_DOMAIN`; `?ws=` re-scopes to any workspace | — (read-only; admits nothing) |
| Memory explorer | `extensions/memory` | live (page + JSON read) | gateway bearer whose email domain is `OPERATOR_EMAIL_DOMAIN`; `?ws=` re-scopes to any workspace | — (read-only; admits nothing) |

The debug and memory-explorer surfaces are the operator-audience surfaces — the `ufoctl`-verbs
audience, not a member action. They share one operator web session (`ufo.sdk.operator`, one
`ufo_debug` cookie): the operator-domain bearer's `identify` is the entire authorization — it picks
the target workspace (a UUID or a customer domain via `uuid5(NAMESPACE_DNS, domain)`), core binds it,
and every read is RLS-scoped by construction. The debugger reads a workspace's sessions
(conversations, turns with terminal outcomes and subagent children, transcripts, compaction records,
workspace files, a live SSE tail); the memory explorer reads its durable memory store — every
`memory_item`, shared and per-member, live and superseded, indexed and due, carrying the
recall-decay signals recall itself applies. Each surface reads only its owning extension's data, so
neither reaches a core internal nor the other extension's tables.

Two-way attachments stream end to end, never buffering a whole file: an inbound Slack file streams
from `url_private` into the conversation's workspace before the turn runs; a shared file
(`share_file` → a `shared_artifact` record) streams from the blob store to Slack's chunked
external-upload API, into the conversation's thread (Slack forbids threading on a reply's ts). `surface_identity` and `conversation.surface`
are open namespaces validated by surface registration, not a fixed enum.
Slack renders terminal accounting and model metadata as the reply's final context block only in
the operator's own workspace — the one whose owner's email domain is `OPERATOR_EMAIL_DOMAIN`, the
fleet-level constant naming us, never a tenant-level role.

Slack installs by either of two paths in chat, both landing the same per-workspace bot token and
identity. **Preferred — OAuth on the deploy's own app**: its client id, client secret, and signing
secret are read from the deploy's env (never the sandbox), `slack_connect` (default) returns an
**"Add to Slack" link** whose sealed state names the owner and workspace, and the state-verified
OAuth callback exchanges the code for that workspace's `xoxb` bot token, binds the team, and records
the identity. **Alternative — bring-your-own app** (`slack_connect method="manifest"` + the
`slack-app-setup` skill): the owner creates an app from `slack_app_manifest`, fills the per-workspace
`slack_bot_token` and `slack_signing_secret` slots privately, and `slack_connect` derives the
identity with `auth.test`.

Shared surface requests authenticate their workspace before core binds it. The untrusted team id
selects one unique `surface_installation`, and that workspace's signing secret — its own slot (a
bring-your-own app) if set, else the deploy's env secret (an OAuth install) — verifies the original
bytes before core binds it. Unknown installations, workspaces with no signing secret, and bad
signatures share one rejection. The `url_verification` handshake carries no team, so its bounded
challenge echoes without binding a workspace. Surface identities and conversation keys include
`workspace_id`. Durable writeback enumerates a bounded set of workspace ids through the owner
connection, then binds each before reading or delivering any tenant data.

Onboarding flow engine is core (steps are contributed by extensions/packs); first-run creates the
workspace and its first `owner`.

## Accounting / billing

Every model call and tool call meters into `ledger` in the same commit as the step. Realtime
visibility: live per-turn cost on the stream, workspace/member/agent rollups in CLI and web. Caps
evaluated at inbound and per-step; `reject` refuses new turns, `park` suspends. Prices are a pinned
table per model; BYOK usage still meters (visibility without billing). Seats gate who the
agent answers: `workspace.included_seats` bounds silent auto-seating, `workspace.seat_limit` the
grantable ceiling; core owns the rules (admission refusal — including a member-surface speaker
who never resolved to a member, per-round park on revocation, the owner's irrevocable seat), and
a billing extension's tools drive grants, ask the owner to approve overage seats, and report
counts.

An external billing vendor is an extension draining the usage-export seam
(`ctx.pending_usage_exports` / `ctx.ack_usage_exports`): core mints frozen, consumer-keyed delta
intents from settled ledger rows — settlement and dedup keys are writer knowledge — and the
extension is a pure delivery adapter (`metronome` ships them to Metronome's ingest API). Each
intent freezes a `byok` label at mint: host `tokens` whose model's serving provider key slot
(`ModelRegistry.key_slot_for` — the same resolution `client_for` applies, any provider) is stored
by the workspace, so the rate card bills only pass-through usage.

## Model abstraction

`ModelClient`: `complete(messages, tools, stream)` + token accounting + provider image/content
limits. Core implementations: Anthropic, OpenAI. The Bedrock extension serves Mantle model IDs over
the native Anthropic Messages API and OpenAI-compatible Chat Completions and Responses APIs. Model
policy per agent (`auto` routes by task class); its `bedrock_api_key` credential falls back to the
deploy's `AWS_BEARER_TOKEN_BEDROCK`, as provider credentials come from workspace slots or deploy
config.

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
is instance-aware except the `runtime_instance` heartbeat and the executor-recovery sweep; the only
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

Misconfiguration fails loud at boot: the shared owner DSN must be set and no surface may claim a
reserved onboarding route. Instances heartbeat a `runtime_instance` row so the fleet tracks its live
executors; a peer that stops heartbeating has its in-flight turns recovered by the survivors.

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
| YC CLI + Bookface guidance | tools, sources, credentials, skills, onboarding |
| Agent-guided education / onboarding | onboarding, tools |
| Scheduled tasks (cron / one-time) | jobs, invoke, tools, requires (`memory_search`) |
| GH code review on PR + auto-merge | routes (webhook), credentials, invoke, tools |
| Service self-improvement / bug-fixing from o11y | sources (o11y), jobs, trajectories.read, invoke (evals), agents.propose_change |
| Security review | tools, subagents |
| gbrain-style memory (source → condense to markdown + graph) | memory_search, sources, hooks (page_change) |
| CRM / ATS | connectors, sources, hooks (page_change), tools |
| Websites | tools (sandbox serving), routes |

Packs (activation bundles, not code — see Packs): **assistant** bundles memory, the browser pack
(its BUA engine over the default `sandbox_cdp` transport), brokered connectors, and web research
(the research tools over the Exa search backend) (the flagship); **chief-of-staff** bundles
brokered connector grants plus feed sync (Google Meet transcripts and Gemini smart notes, Slack, a
folder-synced state repo) with memory and the graph, the Slack surface, scheduling, page watches,
todos, workspace skills, and self-improvement behind four pack skills (`sync`, `prep`, `triage`,
setup); **yc** bundles authenticated YC
research, indexed YC guidance, memory, graph, documents, scheduled tasks, todos, and founder
workflows. **support bot** bundles knowledge sources, keys onboarding, and websites. Each activates
one coherent config, no code of its own beyond what it references.

## Non-goals (core, now)

- No Kubernetes, CRDs, or operators in core (the enterprise k8s layer wraps core — principle 3).
  Workspace isolation is row-level security on `workspace_id`, scoped per request and turn.
- No Redis in the single-process default; no router service in core (both are extensions).
- No self-improvement machinery in core (the extension API carries it — see `trajectories.read` /
  `agents.propose_change`).
- No second representation of any fact: one transcript store, one schema source, one config file.
- No tool that another tool or `bash` subsumes.
- No Docker-in-Docker, ever: sandboxes are sibling containers (host daemon or mounted socket), or
  a remote carrier.
