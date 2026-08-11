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
| Durable execution | DBOS on the same database as the schema (SQLite dev / Postgres deploys): a turn is a durable workflow, a subagent a child workflow; queues, async cancel, crash recovery. Shutdown stops admission, gives requests `[serve].request_shutdown_seconds`, waits `[serve].graceful_shutdown_seconds` for active workflows and proxy connections, then retires the executor heartbeat only if no workflow remains active — a workflow that outlives the drain keeps the seat, so no peer re-dispatches work this process still executes; the seat ages out with the process. The supervisor's termination budget exceeds the sequential drains. DBOS-on-SQLite is verified in U1 — fail loud, never silently fall back to requiring Postgres. Dequeue poll interval and system-DB retention are configured from day one. |
| Streaming | Durable terminal frames in Postgres; live token deltas through a hub interface — in-process in the single-process default, a Redis hub extension for multi-instance deploys. A lost delta costs a redrawn token, never correctness. |
| Topology | `ufoctl serve` is one process on one event loop: surfaces + DBOS workers + jobs. Everything is async-native — a blocking call stalls the whole deploy, so blocking-in-async fails lint. Scale-out = more instances plus a shared hub. |
| Sandbox | A local temp-dir carrier is the core default: no kernel isolation (a raw shell reaches the host FS — only tool arguments are workspace-guarded), and egress is proxy-scoped/metered only for clients that honor the proxy env, not kernel-enforced (model keys still stay fail-closed via the sentinel). It is the development / trusted-input default; use Docker or E2B (carrier extensions on the `carriers` point) for untrusted input, isolation, or multi-tenant deploys. |
| Models | Model providers are an extension point; core ships Anthropic + OpenAI direct clients behind one `ModelClient` interface. Bedrock Mantle and OpenRouter ship as extensions. |
| Observability | OpenTelemetry APIs only in product code; the OTLP export target (Datadog, …) is deploy config. No vendor SDK in core. |
| Kubernetes | Absent from core by construction. The enterprise offering later wraps core with k8s (principle 3); nothing in core may assume or import it. |
| Runtime authorization rollout | Deploys are serialized. IAM authorization and runtime consumers cannot change together: expand IAM, roll and drain the runtime, then contract IAM. CI rejects a change spanning that boundary. |
| CLI | One CLI: `ufo` (`chat`, `serve`, `bundle`, `ext`, admin verbs). |

## Workspace model

Every trusted boundary — turn execution, OAuth callback, sandbox proxy — binds
`with ws(workspace_id), agent(agent_id):` from its durable record or signed claims. Agent-scoped
capabilities derive both keys from that scope; only durable records, admission assertions, and
workspace-administration reads carry an explicit agent id.

Tables (all keyed by `workspace_id`, `created_at`, `updated_at`):

| Table | Owns |
|---|---|
| `workspace` | The team unit: name, config digest, `included_seats` (the silent auto-seat allowance), `seat_limit` (the grantable ceiling) — both NULL = unlimited, the shape every deploy without a billing extension keeps. |
| `member` | A human. `is_admin` grants workspace management to any number of members; onboarding makes the first member an admin, the last admin cannot be removed, and at least one seated admin remains able to act in chat. `seated_at` marks a member the agent answers: auto-seated at creation while an included seat is open, granted beyond that by an admin in chat, gated at admission and per round. Surface identities link here (Slack user id, CLI token, web session) — one human, many surfaces, one memory subject. |
| `surface_installation` | A chat installation's unique external identity → workspace binding, bound to one agent — the agent every conversation the surface creates lands on (a new binding lands on the workspace's explicit main agent). Shared ingress uses it only to select a candidate credential, authenticates the original request bytes, then binds that workspace. |
| `agent` | A configured agent: name, prompt, model policy, reasoning effort, granted tool set, skill packs, memory scope. Exactly one per workspace is `is_main`: onboarding creates it, unbound surfaces route to it, and it may update any agent's settings without crossing member or audience boundaries. |
| `connection` | One member-owned broker account identity per `(workspace, provider, account)`. It holds no secret. `connect_account` creates or reuses it and refuses to reassign another member's account. The `connection` object is agent-neutral; deleting it atomically stops its sources, tombstones their pages, and removes every connector grant. |
| `connector_grant` | One connection → agent edge. It records the granting conversation and `shared` disclosure; identity and ownership remain on the connection. `connect_account` creates the intended edge. The `connector_grant` object flips or revokes only the current agent's edge. Account resolution admits the exact acting member's private edges plus agent-shared ones, preferring private; egress remains agent-scoped. |
| `source` | One member-owned, agent-neutral synced dataset. `shared` widens its member audience; it never grants an agent access. Sync uses the owner's connection independently of agent grants and stores one physical copy. |
| `source_grant` | One source → agent recall edge. Registration grants the agent it names, whether the feed is new or already live and syncing; removing the source deletes every edge. The main agent may read an owned source without an edge only while that source's exact owner is the live speaker; a scheduled run or subagent carries its initiator's authority but never this exception. |
| `credential` | BYOK secrets, encrypted at rest. Slots are declared by extensions; values are workspace-scoped. |
| `conversation` | Surface context ↔ queue key (Slack thread, CLI session, web session), permanently bound to one agent at creation — its surface's installation binding, else the workspace's main agent. Admission derives every turn's agent from that binding; a caller-supplied agent id is an assertion admission refuses on mismatch. Its persisted `Audience` atom is `shared`, `member:<uuid>`, `room:<surface>:<room>`, or sealed `foreign:<surface>:<room>` and is carried unchanged through the turn. `surface_label` is the origin in the surface's own grammar (Slack: `#general`, `Direct message`), written by the surface that owns the encoding and never parsed by core; a surface that names none leaves it null, and a rename corrects on the next message that carries the name. The `conversation` object exposes it as a spec field and a filter/order field, under the same audience gate as the row. |
| `turn`, `transcript` | The loop's durable log: lifecycle, per-member-inbound `speaker_member_id` distinct from the conversation's `Audience`, full-conversation transcript + compaction records. A turn's speaker attributes its founding message; authority binds per inbound message. Timer, system, and subagent turns carry no speaker. `on_behalf_of_member_id` is the initiating member a speakerless scheduled fire or subagent acts on behalf of for capability use — distinct from message attribution and conversation audience; scheduled fires derive it from `scheduled_task.created_by_member_id`, subagents copy the bound requester at spawn, a turn woken by a delivered subagent result carries forward the one its child held, and a member takeover clears it. Sub-turn steps — each model round, tool call, and compaction — are DBOS's own `operation_outputs` step log, memoized so a crash-recovery re-run replays completed work instead of redoing it. |
| `memory_item` (memory extension) | Memory scoped to one exact audience subject. The memory extension owns this table via its own migration; the index backend owns `chunk`. |
| `ledger` | Metered usage: every model and tool call, priced. |
| `spend_cap` | Caps by scope (`workspace` \| `member` \| `agent`), dimension, window; `reject` or `park` on breach. |
| `job` | Recurring/one-time background work (source sync, page-change fan-out, turn dispatch, subagent result delivery, extension jobs). |
| `scheduled_task` | Agent-namespaced, member-private recurring invocation with names unique per agent and optional UTC expiry, enforced before invocation. Creation binds the executor and its reporting conversation; updates never move either. The main agent may target an existing child-agent task from any conversation: its creator may inspect, edit, or cancel it; an admin may list management metadata, change cadence or expiry, or cancel, but cannot read or change its prompt or responses; another member cannot see it. Each recurring turn carries the exact claimed UTC occurrence; when its following occurrence reaches expiry, runtime adds a continuation check-in to the completed work. One-time workflow pauses keep their raw resume prompt. |

## Agent loop

One workflow: inbound → admission (identity, spend preflight) → queue row → worker turn → steps →
terminal frame. A client's wait always ends — the terminal state commits on the failure path too.

- **Tool calling** — typed registry; per-call metering; results bounded before hitting the model.
  Every member inbound carries a stable `message_ref` from its existing turn/message id. A tool
  call's optional `requested_by` must name a visible, non-denied message already absorbed by this
  turn; core strips it before input validation and binds the call's member capabilities. Omission
  means common work, except a scheduled turn or subagent retains its durable `on_behalf` member.
  Reads combine the conversation's subjects with that member's own subject — never the shared atom
  their private audience also reads, so a sealed conversation stays sealed however it is driven.
  A write takes the member's private subject only in a workspace-shared conversation; a private room
  or a foreign channel is its own memory space, so a write there stays keyed to that space — a
  member wanting a private note makes it in their own conversation.
- **Skill loading** — skills are folders of files (SKILL.md + assets), mounted into the sandbox on
  `load_skill`. **A skill ships with the thing it teaches**: core ships exactly one folder skill —
  `sandbox` — teaching core's own builtins, and generates a `model-catalog` skill from
  the model registry at serve so the models a member can pin stay documented from the same records
  the runtime routes and bills on (RFC 0018); an extension's skills ride its manifest; a pack may
  add pack-level skills of its own (see Packs).
  Member-authored skills persist for the bound agent and join only that agent's registry.
  Every-turn content belongs in the system prompt, situational/long content in skills; skills carry
  workflows, never restated tool docs (the tool's description is authoritative). `load_skill` mounts
  each skill's files and injects its `SKILL.md` without the frontmatter, under a header that says
  whether the agent asked for it or a dependency pulled it, closing with one tree of everything
  mounted — the named skill first, then each skill it `depends` on. `depends` is
  the only pull: a nested child reaches its parent by declaring it, and nesting alone pulls nothing.
  A load costs the workflows it pulled and the paths to their files, never a restated catalog entry
  or a mount prefix repeated once per bundled file. **A workflow enters the context once**: the turn
  tracks which skills the window holds by expanding each `load_skill` call's own requested name
  through the registry closure — never by reading a skill's prose, which is member-authored and may
  quote the header format — so it holds across turns, across a compaction, and across replay, and a
  repeat load re-mounts the files and names the skill in one line. A result the engine offloaded or
  truncated cut the workflow off, so that one loads again.
- **Typed subagents** — a registry of profiles (name, prompt, tool subset, input/output schema);
  spawn = child turn with parent linkage; foreground awaits and returns the child's
  schema-validated output; background returns an id at once and the child delivers that same
  validated output to the parent's conversation when it finishes — an arrival, folded into the
  parent's live turn at the next round boundary or admitted as its next turn, so no parent holds
  a turn open waiting on a child. Only the caller knows whether anyone will await, so the choice
  is written onto the child at admission. A woken turn reads the durable record, never the
  ending turn's working memory. Two payload
  knobs any profile may declare: `preload_skills` mounts the named skills with their `depends`
  closure and injects their instructions before the child's first round; `extended_context` lifts
  its round budget to the
  main ceiling. Extensions register profiles.
- **Compaction** — full-conversation `messages.json.lz4` transcript with monotonic seq +
  `compactions/<cid>/{before,after,summary}` records in the blob store; a deterministic pipeline
  groups the over-window head into API rounds, compresses it into a validated structured
  `CompactionSummary` (one metered model call, bounded prompt-too-long retry), re-references the
  durable `.tool-output` files it offloaded, and keeps the recent tail verbatim. The summary's
  `loaded_skills` is the one field the pipeline fills rather than the model: it drains the turn's
  skill-load tracker, which knows what the head held. Draining empties the tracker, so a compaction
  that may be followed by another `load_skill` re-derives it from the window it just wrote — the kept
  tail can still carry a load whose workflow is still in front of the model. The compaction forcing a
  final answer is the exception: that round never offers the tool again. The trigger derives from
  the model window less the summary reserve. Before swapping, deterministic checks remove
  ungrounded paths, retry once for missing head anchors, reject a feasible replacement that fails to
  shrink or clear the trigger, and record any remaining loss. An irreducible verbatim tail may
  remain over the trigger.
- **Memory** — an extension, not core: it owns the `memory_item` table, the `memory_search`/
  `memory_update` tools, the read-only `memory` object kind (each search hit carries its
  `memory/<id>` or `page/<id>` ref and date; `object_get` opens it — `created_from` links a
  derived item back to its synced page, `superseded_by` a consolidated one to its replacement,
  and search excludes superseded items), and recall (lexical + vector RRF fusion), auto-injected
  each turn through a `user_prompt_submit` hook. Member and room audiences read their own subject
  plus shared; shared reads shared; foreign reads only its sealed subject. Automatic recall uses
  the conversation audience. Explicit memory tools and opened result objects combine the
  conversation's readable subjects with the bound requester's own member subject, so a foreign
  conversation reads only its sealed subject and the requester's, never shared. It also
  provides the typed `memory_search` seam: core routes that exact subject set to the extension's
  one search workflow. Scheduled tasks require this seam and search
  after admission and before the run's first model round. Recall carries the
  gbrain richness: per-kind recency decay against source information time, falling back to commit
  time (fact/preference/decision/event/task half-lives, fact items only), a type-diversity cap so no
  class dominates, supersession suppression, and an episodic→topic pointer excluded from
  auto-injection. It rides two core selection seams: the
  **index backend** behind one lexical/vector interface and the **embed backend** behind
  one batched-embed interface. The dialect-native index (SQLite FTS5 + local cosine, Postgres
  tsvector + pgvector) ships as the base-pinned `index_default` extension and OpenAI embedding as
  the base-pinned `embed_openai` extension; turbopuffer is a drop-in index alternative. Source
  pages reach recall through the memory extension's own page-index job over the core `PageFeed`;
  every page-derived memory binds to the revision of the page it currently reads from, so recall
  drops that derivation as soon as that page changes, and the page-index pass makes the revisions
  the page left due again so the index job withdraws their chunks — no superseded fact spends a
  candidate slot the fact that replaced it could have taken, whether or not a replacement is ever
  derived. The fact derivation that settles the new revision is the one writer that retires what it
  replaced, and it retires only the revisions its own committed facts replace — so a revision that
  derives nothing destroys nothing, and no consumer that cannot replace a fact can remove it. A page
  that is gone is the one unconditional retirement: nothing can ever replace what a deleted or
  tombstoned page derived, so what it derived retires outright rather than waiting for a replacement
  that cannot arrive. The same fact learned from two feeds is one row carrying a link per page it was
  derived from, so a reader granted any one of those sources reaches it: retiring one page drops only
  that page's link and re-points the row to a feed that still holds it, and the row is removed, index
  scope and all, only when its last link is gone. A source-derived candidate is kept only for a
  reader granted one of its sources — filtered by grant over an over-fetched candidate pool rather
  than an index partition, so the grant fence never starves a small recall limit — then rechecked
  against the live page, audience, and grant state before return. The main agent's owner exception
  applies to explicit work for that member; automatic room/shared recall carries no acting member
  and cannot inject their private source.
- **Minimal built-in tools** — `bash`, `read`, `write`, `edit`,
  `ask_user`, `request_credentials`, `spawn_subagent`, `load_skill`, `share_file`, and the five
  object verbs (`object_list`/`get`/`explain`/`apply`/`delete`) over extension-registered kinds
  (RFC 0017) — one generic CRUD surface instead of per-extension config tools. Object lists accept
  exact first-class-field filters and field ordering over the fields each kind declares — its own
  vocabulary of scalars its rows carry, not its spec's shape, so a read-only kind indexes a column
  without widening the spec a form renders and its apply refuses; a row carrying an undeclared
  field is refused, and each kind's listing proof is what keeps its declaration and its rows in
  step. Gets return the kind's readable spec beside
  its live status, the owning row's timestamps (recency is the first arbitration signal when
  retrieved facts conflict), and its typed links — `created_from`, `synced_by`, `created_in`,
  `reports_to`, `superseded_by`, `access_to` (the connection or credential slot a grant or binding
  authenticates through, and only where the linking row is no wider than that target), `scoped_to`
  (the row's one owning agent, taken only where no narrower relation already names it), a closed
  core vocabulary; each link's
  `kind/name` target opens with the same verb. Links are stored on the owning row and rendered forward-only: a forward link
  is O(1) and points toward equal-or-wider visibility, so there is no edge table, no backlinks, and
  no target elision — a reverse question is a structured query over the forward column, exposed
  only when a flow needs it. A link never grants visibility.
  A conversation get writes its text exchange to `status.workspace_path`; bulk transcript content
  never enters the tool result.
  A kind whose rows carry an opaque generation is fenced on it: an active verb refuses once the name
  holds a row its own read never saw. A kind without one is last-write-wins — a concurrent create or
  removal is the verb's ordinary absent-or-present case, not a lost race — while every kind
  rechecks visibility after a disclosure read.
  Each object kind declares its supported cross-agent verbs: `conversation` list/get, `artifact`
  list/get/delete, `scheduled_task` list/get/update/delete; workspace-scoped kinds declare none.
  Apply resolves create or update from the current object and requires that exact declaration.
  Omission means the executing agent. Crossing that boundary requires the configured main agent,
  a non-subagent turn, and an exact live requesting message. The target is task-local to the object
  dispatch, keeps the requester's member authority, audience, and sandbox, and appears as a
  separate `agent` field in results and refs — object names never gain a second encoded form.
  Everything else arrives via extensions.
  Two tools where one would do is a defect. `share_file` ports the shipped design: byte custody in
  the blob store, a TTL-bound token URL served by core's artifact route — no token, no bytes.

## Sandboxing

Every turn executes tools in a sandbox: Docker container from a pinned image (baked toolchain),
default-deny network egress with exactly one route out — the sandbox proxy. A sandbox belongs to a
conversation, and a subagent turn executes in the sandbox of the turn that spawned it — one
filesystem for a whole spawn tree, so a file a child leaves in `/workspace` is the handoff back to
its parent, and co-residency is the cost: session state at fixed paths, one serving port, one
`/proc` carrying the run token.
An off-cluster carrier reaches the proxy only over TLS; the proxy token is never sent on plaintext
transport. Each tool command gets a deployment-signed token naming its turn and acting member;
unbound commands name no member. Descendants retain the launching command's environment while later
commands may carry another member. Every CONNECT also requires the named turn to remain running.
This is process attribution, not isolation between cooperating processes sharing a sandbox UID. A
process-wide connection ceiling bounds proxy state; a per-workspace share keeps one workspace from
consuming it. Meter records cross a bounded, backpressured queue and write aggregated per run in
workspace-scoped transactions, so one failed run cannot roll back another.

**The sandbox proxy is core, not an extension** — it is the enforcement point for three core
invariants: **sentinel swap** (processes inside see placeholder credentials; the proxy swaps real
values onto the wire, so raw secrets never enter the sandbox), **grant scoping** (authenticated
calls use only accounts the agent's grants cover — principle 4 enforced at the wire), and **wire
metering** (every model/API call made from inside the sandbox lands in the ledger). An extension may
declare `sandbox_internet`; its deploy's live turns may then reach globally routable public IPv4
through a metered opaque tunnel. DNS is pinned and every IPv6, loopback, private, link-local,
reserved, multicast, or shared-space answer is refused. Tokenless and ended turns cannot use public
internet. An admin may narrow that deploy capability per agent through the agent object's
`internet_access_allowed`; the proxy snapshots it into that turn's cached rules.
Extensions never register raw network rules; the proxy's rewrite rules are *derived* from their
manifests — sandbox internet, a credential slot, a connector, or a model provider implies its
scoping, injection, and metering rules. Declare, don't open. The enterprise k8s layer later ships
its apiserver-rewrite / token-mint module through this same rewriter seam.

`/workspace` is the carrier's own storage and the only copy of a conversation's files: a host
directory an in-cluster carrier bind-mounts (local, Docker), the sandbox's own disk off-cluster
(E2B, whose provider suspends an idle sandbox and keeps it indefinitely). A tool reaches only
`/workspace`; transcripts, compaction records, and artifacts live in the blob store, which the
sandbox holds no credential for — sharing a file is the sandbox PUTting it to a single-key
presigned URL serve mints, bound to the size and sha256 an in-container preflight measured, so S3
itself refuses any other body. Everything that touches workspace files goes through the carrier —
a turn's tools, a surface landing an inbound attachment, a job appending a change log, the
operator's file browser — and reclaiming a container is the carrier's own business: the Docker
carrier stops its idle containers and any later touch starts one again (the bind mount and the
container persist), and nothing may reclaim a container whose disk is the workspace. Carrier interface: `create / attach / exec / write / read / host` — a local
carrier is core's default; Docker and E2B implement it as extensions on the `carriers` point.

## Extension system

An extension is a Python package exposing one entry point (`ufo.extension`) that returns a
`Manifest`. Extensions import only the public SDK (`ufo.sdk`); a CI gate forbids reaching
into core internals.

The active manifest set reads back as the core-registered `extension` kind: one object per
extension, named lowercase and hyphenated, whose spec names what a member can encounter of it —
tools, object kinds, credential slots, surfaces, jobs, hook events, source backends, subagents,
named and never valued — and whose status carries what it asks of the deploy (`sandbox_internet`,
`requires`). Instances are declarations rather than rows, so their envelope timestamps are null,
and every mutation refuses: installing and removing an extension is a lockfile act (`ufoctl ext`).

Manifest registers (each optional):

| Point | Contract |
|---|---|
| `tools` | Typed tool defs + handlers; appear in agents' granted tool sets. A `profile_only` tool never reaches a main agent — only the subagent profiles that name it (the raw browser surface reaches main agents solely through `browser_task`/`wide_browse`). |
| `objects` | Workspace-object kinds (RFC 0017): name, one-line description, model-facing guidance, spec model (`extra="forbid"`, JSON-round-trippable, no secret fields — boot-gated), and store handlers over the extension's own tables. Core's five `object_*` verbs validate the YAML envelope and spec, then dispatch to the kind under its own ExtensionContext; domain refusals live in the handlers. A kind whose rows belong to a member (`connection`, `connector_grant`, `source`, `scheduled_task`, `site`) is built on the `MemberOwnedObjects` base: it declares each row's owner (`member_id`, `shared`) and the base enforces per-member access. A row is visible when shared, owned by the acting member, or inspected by an admin. Its member-owner controls expansion; an admin may inspect, restrict, or delete but cannot widen another member's access. |
| `subagents` | Typed subagent profiles. A profile may isolate its named tools from deploy-wide defaults and grants; only core constructs the child's effective registry, so an extension cannot enforce that boundary itself. |
| `prompt_sections` | Capability sections a pack contributes to the agent's system prompt, rendered into the shell's `{{sections}}` slot ordered by name — a pack's rules (web search, browsing, office docs) reach the agent without core naming the capability. |
| `skills` | Skill folders (SKILL.md + bundled scripts/assets) contributed to the loadable set; the loader parses each into the registry `load_skill` and the `{{skill_index}}` consult, mounted into the sandbox under `.skills/<name>/` beside core's own three. A skill script imports nothing from ufo (it runs in the sandbox) — a CI gate holds that boundary. |
| `connectors` | One brokered provider per declaration: its OAuth descriptor (connection flow), member-facing label, and `ConnectorBroker` — catalog, server-side execute, feed-sync credential. `serve` merges every declaration into the one `ConnectorRegistry`; the `connectors` extension's broker-generic dynamic tools (list/describe/search/call) dispatch through the current agent's connector-grant edges, and the sync runner resolves a source's feed-sync `Credential` through its registering member's active connection without an agent (a source registered against `DIRECT_ACCOUNT` holds no connection and resolves through the `auth_proxies` fallback instead). A broker may also declare an **open namespace** (`connector_resolver`): the connect flow and registry resolve any slug no connector explicitly registered through it, so the connectable set is drawn from the broker's whole live catalog rather than a fixed list. The namespace validates a slug against that catalog when the member connects, and claims only what the deploy can actually serve — a typo, a toolkit the broker holds no managed credentials for, one cataloguing no tools, and one whose tools cannot do the job its service exists for all fail loud at the connect request instead of minting a consent link that dies later. The first three are read off the catalog record; the last is a judgement the broker extension records per slug, because the catalog's scope metadata is too inconsistent to derive it (Composio's `BANNED`, with the evidence in `docs/composio-provider-coverage.md`). It is the catch-all, so at most one deploy-wide (two fail loud). Two broker extensions ship — `composio` (the open namespace: any of its brokered toolkits by slug, plus an explicit connector only for an exception its catch-all can't serve, the `github` CLI) and `pipedream` (an explicit allowlist of connectors Composio's namespace cannot broker because its shared client cannot pass their consent: Gmail — Google blocks restricted Gmail scopes, so the deploy's own Google OAuth client rides Pipedream Connect, set via `custom_oauth_env`. A provider **no** broker holds managed auth for is not an allowlist entry: it authenticates with a workspace key through an injecting `credentials` slot, below). Pipedream is a fixed allowlist, not an open namespace: each entry names a provider the open Composio namespace declines, and the catch-all stays Composio's alone. **A broker brokers auth by PROXY: every call goes through the broker (its execute API for tools; a proxying transport for feed-sync source HTTP; a request forwarder for sandbox CLI HTTP) carrying `(external user, connected account id)` — the broker holds the provider token and injects it server-side; the token is NEVER exposed to us. A connection stores only the connected-account id and its member owner; a connector-grant edge stores only agent binding, disclosure, and audit. The confused-deputy check reads the account's owner metadata, never a token. Brokered connections derive NO egress `InjectionRule` — the sentinel→key swap (§Sandboxing) is ONLY for user-supplied BYOK `credentials` keys. A connector may instead declare a `cli` credential (github → `GH_TOKEN`): the sandbox exports the connector-grant edge's sentinel in that env var, and the egress proxy forwards a request carrying it through the broker under the connected account (a `ForwardRule`) — still auth by proxy, gated on the acting member and turn liveness, metered like any granted host.** Files cross the broker seam as references, never bytes: a tool's file outputs come back as presigned URLs on the broker's file store (every Pipedream run rides a File Stash; Composio marks `{name, mimetype, s3url}`), a binary provider response on the proxying and forwarding channels is answered as a redirect to that same store rather than fetched into the process (Composio marks `binary_data`; the sandbox client follows it, and a feed-sync run fails loud on it — a feed page is JSON, never a file), a `workspace_file` input stages to a broker-minted presigned PUT, and the sandbox runs both transfers itself into/out of `workspace/` — a connector-grant edge additionally admits (never injects) its connector's declared `transfer_hosts` (or the open namespace's, for a slug it brokers), so file bytes never cross the serve process. Base64 a provider inlines in its own JSON result (GitHub contents' `encoding: base64`) already has, so the connector tools translate it in place before the result enters context: decoded text inline under a cap, anything binary or larger written to `workspace/` through the sandbox's write seam and replaced by a reference. A sub-object a denormalized list response repeats identically per element (GitHub code search embeds the same `repository` in all 30 hits, 85% of the payload) crosses once the same way: first occurrence in full, later copies a `{same_as: <JSON Pointer>}` pointer into the same result — lossless, keyed on structural identity and never on a field name, so no provider is special-cased. It runs before dispatch measures the result, so a single-repo search that would have been offloaded whole (141K chars) stays inside the inline budget (21K); a cross-repo search collapses nothing and offloads, which is the same rule and not an exception. |
| `sources` | Data-feed backends: `sync(cursor) -> pages` run as jobs; core derives each digest from the body, stores changed bodies at immutable digest-qualified refs, and pages land in memory through the derivation pipeline. A source is private to its registering member by default — its rows carry a recall subject the sync driver stamps onto every page — and flips to workspace-shared only by the registrar's opt-in, which restamps its live pages. Audience controls which members may see it; source grants independently control which agents may use it. A broker source's row identity includes the exact member-owned connection generation that registered it; reconnecting creates a new source and cannot reactivate the removed source or its tombstoned pages. Each provider is a backend factory receiving only its declaring extension's scoped credential access; extensions cannot otherwise express an authenticated direct source without exposing encrypted secrets. Core ships only `folder` (local files). Connector source providers live in `extensions/sources`, built on the read-only REST connector framework core exposes through `ufo.sdk.sources`; each stream declares provider-record creation/update paths independently from its sync cursor, and each connector resolves a provider `Credential` through the pluggable **auth-proxy** seam, never importing a broker. **A first sync reaches back a bounded window, pinned at registration to an absolute instant** so a cursor reset replays it rather than re-windowing onto a fresh one. A stream declares its own reach (30 days for email messages) and the registering member may widen it or ask for all history per binding; a stream declaring none takes no override, and the knob is refused where no selected stream would honour it. The window is a parameter of a row rather than part of which dataset it is, so it never moves a `source_row_id` — and it only ever widens in place, re-pinned against the original registration instant; narrowing is delete-and-recreate, since only that tombstones the pages a narrower window drops. When a same-named surface resolves the external user it speaks as, the runner puts that live identity on `SourceAuth`; the source drops only records authored by that exact user before they become pages. |
| `hooks` | Reactive lifecycle handlers on Claude Code's taxonomy, scoped like a job. Seven fire on the turn loop — `pre_tool_use`/`post_tool_use`/`post_tool_use_failure`, `user_prompt_submit`, `stop`, `pre_compact`/`post_compact` — as a runtime policy filter over the tools grants already admit (observe, deny, modify, or inject), never a second grant path. The eighth, `page_change`, is the data-plane seam (data → memory): a core batched cursor-runner replays each changed source page in database-assigned workspace revision order from each consumer's own cursor — the path the memory page indexer and fact deriver ride. (Claude Code's session/permission/subagent-stop/notification events have no producer here and are not members until one lands with a consumer.) |
| `jobs` | Recurring/one-time background work. |
| `routes` | HTTP endpoints under `/ext/<name>/` (webhooks, OAuth callbacks, plugin UIs). |
| `surfaces` | A member-facing HTTP surface on the one privileged surface seam — the seam that asserts a member's identity — with its `SurfaceRoute`s mounted under `/surface/<name>`. A **durable** chat surface (Slack) declares two-phase delivery (`post` then `attach`) the poller drives — declaring `post` is what marks it durable, and admission registers every turn entering its conversations for delivery, whoever admits it; a **live** chat surface (web) tails the hub over SSE in its own route. Core's CLI is the built-in live twin. A **page** surface (the sites frame) admits no turn and delivers nothing: it resolves its workspace from a signed address in the URL, authenticates the viewer from the session cookie, and renders one of the workspace's own rows under that row's access rule. It is not `routes`, which carries no member identity and cannot resolve a workspace before binding one. |
| `credentials` | Named BYOK slots the workspace must fill (drives onboarding); `ufoctl init` seeds a slot from its upper-cased env var (`ACME_API_KEY` → `acme_api_key`), and the operator fills or rotates one anytime with `ufoctl credential set <slot>`. An admin fills one in chat through `request_credentials`: the speaking admin's ask seals which slots they will fill, a capable surface prompts for each value privately, and fulfillment verifies the seal before the encrypted store takes it — the plaintext never enters the transcript or the sandbox. An extension tool may instead seal provider authorization state to its own declared slot and the speaking admin, then fulfill that seal with the resulting credential; URL/code handoffs therefore stay in chat while provider secrets do not. Extensions may read their declared slots and compare-and-swap an existing value when an upstream client refreshes it; only an admin-sealed or operator path creates a value. The portal's workspace credentials view is the same admin-sealed path, prepared rather than typed: its set or replace asks for exactly that `request_credentials` prompt and the value crosses only in the prompt's fulfillment, and its clear rides the `credential` kind's own admin-gated delete. |
| `credentials` → `InjectionTarget` | A slot carrying one turns the workspace's stored secret into wire access without the sandbox ever holding it: the proxy admits the target host, swaps the declared sentinel in that header for the real secret, and meters the host — resolved **per workspace from the run token**, so the one shared proxy injects for every workspace and bakes none of them into its static base. The engine exports the sentinel (never the secret) as the declared `env`, so the agent's own HTTP client authenticates the provider the way the GitHub CLI does with a grant. A provider that pins its API host per account declares a `HostChoice` instead of a hostname: the closed set of hosts it publishes (a Datadog site, an OpsGenie region), the companion non-secret slot a member selects through, the default an unchosen workspace gets, and the env the resolved host is exported as. **The stored value is a choice, never a hostname** — it resolves to one of the declared literals or to nothing — so a scoped host is always a string the declaration wrote, which is what an exact ScopeRule requires: it bypasses the proxy's private-address check (that check guards the open-internet path), and free text there would let a stored address decide where the shared proxy dials. A closed set leaves no pattern, length cap, case fold or suffix bound to get wrong. Every host is resolved through one resolver by all three consumers — proxy rules, the sandbox export, and the `credential` object's own read — so no read reports a host the wire would not use. One sandbox variable carries one value: every exported name (a slot's env, a choice's env, a connector CLI's env) is claimed in a single namespace where both roles collect their slots, and a sentinel is unique across installed extensions. **This is how a provider no broker can front becomes connectable**: brokered OAuth where a broker hosts consent, a keyed slot where only the member holds a key. Two slots on one host each inject their own header, which is a provider taking more than one key (Datadog's API + application key); auth signed over the request (SigV4) and Basic composed from two stored values are not expressible and are out of scope. A slot naming `git_basic_user` composes Basic from its one secret and that declared literal, and configures the sandbox's git to send the sentinel as its `Authorization` header for the host: git is the one client whose auth is configured rather than read from an env var, and smart-HTTP takes only Basic — a bearer is refused even for a public repository. That is what makes `git clone` and `git push` of a private repository work from the sandbox, with the sandbox holding only the sentinel. A slot may also name a `source`: a credential this deploy MINTS per workspace at rule derivation rather than the member storing one, falling back to the stored value when it has nothing to mint from. The published GitHub App is the one: an admin installs it from a link carrying a seal that names this workspace and slot, GitHub returns them to the extension's own route, and the authorization code there exchanges for that member's own token so `GET /user/installations` decides whether they actually reach the installation the redirect claims — the seal alone could not, since an admin who obtains a link could return with another organization's id. Each turn's rules then carry an installation token minted against it, expiring in an hour. What the slot stores is a **seal** over `(workspace, installation)`, not the id: an id is a small integer anyone could type into the slot, and the deploy's App key mints against any installation of it — so the durable secret is the deploy's App key, not a member token at rest, and a member's own token remains the fallback for a repository outside that organization. |
| `onboarding` | Steps contributed to the workspace/pack onboarding flow. |
| `models` | Model providers behind `ModelClient` (OpenRouter, local runtimes). |
| `carriers` | Sandbox carriers — Docker, E2B, remote runners; core's default is a local temp-dir carrier. |
| `indexes` | Index backends for memory/source retrieval (turbopuffer); the dialect-native default (SQLite FTS5 + local cosine, Postgres tsvector + pgvector) ships as the base-pinned `index_default` extension registering name `"default"`, which core resolves when `memory.index_backend` is unset. |
| `embeds` | Embedding backends behind `EmbedClient`, selected by `memory.embed_backend`; OpenAI text-embedding-3-large ships as the base-pinned `embed_openai` extension registering name `"default"`. |
| `hubs` | Stream hubs for multi-instance deploys (Redis). |
| `cdp_providers` | CDP transport backends the one BUA browser engine (an extension, not core) connects, selected by `[browser] cdp_provider` (default `sandbox_chrome`). Core ships none: `sandbox_chrome` drives Chrome inside the sandbox the turn runs in; `browserbase` mints a hosted session per browser run (the hosted default) against a Browserbase Context it keeps for that run and deletes with the session. A provider mints a per-turn `CdpLease` the loop releases at turn end, and the lease answers both file questions only the transport can: `place_file` where its Chrome can open a workspace file (the same path when Chrome shares the sandbox, an upload when it is remote) and `download_dir`/`fetch_download` where that Chrome may write a download and how its bytes come back (a sandbox path read back out of the sandbox, or a hosted provider's storage read back over its API) — so a file input works whichever transport is selected, and a hosted browser is never handed a path it refuses. The BUA engine is the browser extension, so only the transport is a core seam, never the engine. |
| `auth_proxies` | The credential backend a feed-sync source resolves through when it registered against `DIRECT_ACCOUNT` because the member set a workspace credential instead of connecting an account. The sole installed backend is automatic; `[connectors] auth_backend` selects one when several are installed and must name a registered choice. `direct` BYOK reads that key host-side, never reaching the sandbox. A source holding an account id resolves only while its registering member owns the active connection, independent of agent grants; another member reconnecting the same account does not reactivate it. **The account handle is the routing signal, not the provider name.** |
| `search_providers` | Web-search backends the research extension's tools call, selected by `[research] search_provider`. A backend runs host-side — it reads its BYOK key in-process and reaches its API over async HTTP, so the key never enters the sandbox — and answers a search query; `supports_fetch` marks whether it also fetches a URL's content (Exa's search + contents does; an answer-with-citations backend need not, and the `fetch_url` tool gates on it). Core ships no default: every backend is an extension, and the research extension `requires` this seam. |
| `memory_search` | A named workspace-scoped memory search provider (`default` is selected). Core passes the exact readable subject set; consumers declare `requires=("memory_search",)`, and boot fails unless exactly one default provider is active. |
| `requires` | Sub-seams this extension consumes from another (the browser pack `requires` `cdp_providers`); `serve` resolves each at boot and fails loud — naming the extension and the seam — if the backend is absent, unknown, or unkeyed, so a missing dependency stops startup rather than the first tool call. |

`ExtensionContext` (capability-scoped, handed to every handler): workspace-scoped store access,
`credentials.get(slot)` / `credentials.resolve(slot)` /
`credentials.rotate(slot, expected, value)`, the selected `index`/`embed`
backends,
`pages` (the `PageFeed` replaying
source-page changes under a resumable cursor), `transaction()` over the extension's own tables,
`invoke(agent, input, conversation=...)`, metered `model.complete(...)`/`model.turn(...)`,
`schedule(job)`, `trajectories.read(...)` (transcript/turn evidence),
`files.write(conversation, path, bytes)` (a file into that conversation's `workspace/` subtree, so
an off-turn handler hands the agent a payload too large for context and it reads it with its file
tools next turn — scoped to the ambient workspace, never another's), and
`agents.propose_change(...)` — the governed promotion path: an extension never edits agent config
directly; it opens a proposal (prompt, skills, tool grants) that applies through the same
grant/approval flow chat uses. This is what makes a full self-improvement extension expressible —
mine trajectories, evaluate candidates via `invoke`, promote through `propose_change` — not just
prompt files on disk. Extensions never see raw DB handles or other workspaces.

### Pull request review

The coding extension reviews GitHub pull requests from the existing `pull_requests` source stream.

| Concern | Contract |
|---|---|
| Registration | A workspace admin designates one existing conversation as the inbox for one shared GitHub pull-request source. The source's current revision is the baseline. |
| Trigger | A later `page_change` for an open, non-draft pull request creates one automatic run per `(workspace, repository, pull request, base SHA, head SHA)`. Closed, draft, tombstoned, baselined, and metadata-only changes create none. |
| Wake | The coding hook invokes the registered inbox with the run id and exact repository, pull request, base SHA, and head SHA. The inbox turn spawns exactly one `code_review` child and publishes its final typed result. |
| Review | The child starts a fresh conversation and records that conversation on the run when it takes the checkout, clones the base repository, fetches `refs/pull/<number>/head`, verifies both SHAs, detaches at the head, removes every remote, and reads the complete binary `base...head` diff. Its exact tools are checkout plus review-specific text read, tracked-path glob, and tracked-text grep; each derives this turn's verified checkout rather than accepting another root, and every result enters its context as untrusted data. It cannot ask a question, read binary model content, write, run arbitrary commands, browse, or read review material through an API. |
| Finding | A finding publishes only when the changed code causes a concrete reachable severe defect, a specific supported input or execution path triggers it, and its impact is a security or workspace-boundary breach; data loss, corruption, or wrong-target mutation; production outage, deadlock, or permanently unfinished work; a supported operation that fails or cannot complete for valid input; a materially incorrect result or state for a supported workflow; a substantial availability, reliability, or performance regression; a feature that cannot function in its supported production configuration; or failed build or required CI. Every finding is merge-blocking and carries path, line, title, trigger, failure, and impact. There are no severities or suggestions; an empty list means no severe defect found. |
| Publication | A host-side tool resolves the run from its opaque id under the inbox conversation and publishes a completed `ufo review` GitHub Check on the stored head: `success` with no severe defect, `action_required` with any finding. The check's details link and a closing summary line carry the portal page of the reviewer child's run under its profile, the one page that opens a subagent conversation; a deploy with no public base URL, and a run no reviewer recorded, publish neither. Its GitHub App installation token requests only Contents and Checks write permission and fails at minting when the installation lacks either. The repository ruleset requires `ufo review`; no GitHub Actions review workflow exists. |

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
the default `sandbox_chrome` transport), brokered connectors (Composio's open namespace plus the
Pipedream allowlist), and web
research (the research tools over the Exa search backend).

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
pause, with its ambient `TurnContext` — the sender, IANA timezone, and source the surface knows,
which the engine renders as the `<context>` tag (stable message ref; the admission moment, local
when a timezone is known; sender; source) before each member inbound, a surface that can name a
source doing so in the form it has — a chat surface the message's own permalink, the portal a
link to the conversation, a terminal the client and the member's address — so anything the agent
creates elsewhere can name where it was asked for; a message arriving while the conversation's
newest turn is still live lands on the conversation's inbound queue, which the engine drains into
that turn at each round boundary as separate `<context>`-tagged messages — the terminal
commit refuses to close over a non-empty queue, so one FIFO aggregate produces one reply and one
writeback across all speakers — **identity** resolution (an external id → member + conversation,
linking a `surface_identity` on first contact — `join_member` also creates the member when a
channel-verified email matches the workspace's own domain, the first member's vetted email domain,
so only that initial member onboards through provisioning — and `adopt_identity` to span a member across
surfaces), plus the reads a live view serves: `tail`/`turn_owner`, the admin-shaped
`spend_rollup`, and the per-agent projections — `object_kind` with
`list_member_objects`/`member_object`, `agent_skills`, `agent_spend`, and
`memory_available`/`search_memory`. An extension registers a
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
  live feedback while it runs — Slack's native thread status, and interim progress posts on an
  exponentially growing cadence once a turn outlives its first interval — never for delivery, which
  stays the poller's terminal reply.
- **Live** (web; core's CLI is the built-in twin) — the member's connection is held open, so
  admission registers nothing and the surface delivers by `tail`-ing the turn's frames off the hub
  over SSE in its own route. The poller only processes turns that registered a writeback, so it is a
  no-op for a live surface — the efficient downgrade, not a second seam. The same terminal handoffs
  ride the live stream that the writeback carries: a turn that ended by asking renders its options
  as the surface's own answer affordance under the same idempotent admit (first answer wins,
  `admitted_body` confirming which landed), credential prompts collect privately through the
  sealed handoff gated per slot by `credential_prompt_pending`, and the turn's shared files
  deliver as TTL `artifact_link` downloads read off `shared_artifacts`.

| Surface | Home | Delivery | Identity | Conversation key |
|---|---|---|---|---|
| CLI | core | live (hub tail) | member token | session (private) |
| Web | `extensions/web` | live (hub tail) | web session → member (adopted from CLI) | agent/email/hex (private; a member opens any number of conversations per agent, each behind `conversation=new`; conversations that predate the rail keep bare agent/email keys, reachable by id) + intent/agent/email (the member's prepared-intent lane to that agent) |
| Slackbot | `extensions/slack` | durable (writeback) | Slack user → member (linked; a Slack-confirmed same-domain email joins as new) | channel:thread_ts; public = shared, private channel/MPIM = room, DM = member, Slack Connect = foreign. `surface_label` is `#name` off the `conversations.info` the audience decision already fetched (never a call of its own, and never an MPIM's member-naming name), `Direct message` for a DM, else null |
| Debug | `extensions/debugger` | live (hub tail) | gateway bearer whose email domain is `OPERATOR_EMAIL_DOMAIN`; `?ws=` re-scopes to any workspace | — (read-only; admits nothing) |
| Memory explorer | `extensions/memory` | live (page + JSON read) | gateway bearer whose email domain is `OPERATOR_EMAIL_DOMAIN`; `?ws=` re-scopes to any workspace | — (read-only; admits nothing) |

The web surface is the member portal and its own audience authority: every member reaches the
workspace's main agent — the agent every surface routes an unbound member to — and beyond it the
portal lists and admits exactly the non-main agents whose web audience holds the signed-in
member — grants kept in the web extension's own store, granted and revoked in chat
(`grant_web_access`/`revoke_web_access`, admin-only, applying to the conversation's agent) —
while a workspace admin reaches and administers every agent. The deploy's typed subagent profiles
are listed beside those agents unfiltered — a subagent belongs to no member, so no audience gates
it and no chat route reaches it. Each opens a page of the same deploy shape: the system prompt its
children run under, the deploy skills it can load (none without `load_skill`; a spawn adds the
spawning agent's own member-authored ones), and the conversations it ran in — which a spawn stamps
with the spawning conversation's audience, so whose work a member sees is the parent's answer while
the agents their audience reaches still bound the page. An out-of-audience agent is not-found on
every portal route,
the administration view (agents with their policy,
installations, and web-audience grants; members and seats; spend caps with their subjects named;
the deploy's installed extensions and public-internet ceiling) answers a workspace admin only,
and the signed bearer enters as a session cookie through one POST (the gateway's signed-in
card), never a URL. That card is the deploy's one sign-in: the portal takes no bearer from a
member, so a request reaching it without a session — the bare host `/`, which redirects to the
surface claiming `SurfaceSpec.home`, or the portal path itself — is sent to `/login` and nothing of
the shell is served to a stranger. The plan,
invoices, and payment methods stay chat acts (`manage_billing`), and caps have no object kind
yet, so the billing view is a read. Beside chat, each selected agent
carries read projections shaped by the same contracts chat enforces: its loadable skills (the
composition a turn loads), its connector accounts, its conversations, its configuration overview
(prompt, spec, bound surfaces, the deploy's ceilings — answering the agent's whole web audience,
with the grant list inside it the admin's), and its rolling-window spend beside its agent-scoped
caps — the ledger spans every member's turns, so spend answers an admin or a member whose
explicit grant holds the agent, never the main-agent default alone. Beside the per-agent reads,
`api/chats` lists the member's own web conversations across their audience agents — the rail's
projection, each row titled from its first message. A `#/c/<conversation_id>` permalink opens a web
chat normally and opens another surface's readable conversation in that same conversation view,
read-only. A link into the portal from another surface names its target as `?c=<conversation_id>`,
because a fragment never reaches the server: the sign-in redirect and the signed-in card carry that
target, so a signed-out click lands on the conversation rather than a new chat, and a permalink
whose id is not a conversation id reports the bad link rather than opening one. A reply links the
subagent conversations its turn spawned through the child turns' existing parent linkage. The
conversations view lists the member's own plus the workspace-shared ones and opens each as its
turns, the turns those spawned nested beneath them (a subagent runs in its own
conversation carrying the parent's audience, in the spawning turn's sandbox), and the live
workspace files; an admin lists every conversation of the agent and reads another member's private
one only by acknowledging first that it may hold private information.
The acknowledgement is a granting act, so it rides the prepared-intent lane like every other panel
mutation — `read_private_transcript`, admin-only — and the turn is its audit record; the row it
writes names the reader, the subject, and the moment before any content is served, and is what the
content gate answers on, opening that conversation to that admin for an hour, so a second visit is
a second recorded access rather than a silent re-read. The record is the operator's, not a product
surface: no portal read lists those rows, the disclosure emits `surface.transcript_disclosed`, and
`ufoctl transcript-reads` reads the table — so the acknowledgement tells the admin their email,
the subject's, and the time are recorded, and promises no member-facing listing. Chat stays
narrower on purpose — no tool reads another member's transcript, and none is added: a portal read
discloses to one authenticated person, once, on the record, while an agent asked in chat would
pull that content into a context that summarizes, embeds, and recalls it, turning one bounded
disclosure into an unbounded one. A room or an externally-shared channel remains content nobody
reads here, admin included, because participation there is the peer surface's live roster and no
portal read can check it. What the data never scoped to an
agent reads — and, where a verb exists, mutates:
the team roster, source bindings, the deploy's member-fillable credential slots, memory, shared
files, hosted sites, and usage. The team view is the workspace roster — every member reads who
their colleagues are, which of them administer the workspace, and who holds a seat, exactly what
the `member` kind answers a member asking the main agent in an internal conversation. The roster
is internal: a child agent and an externally shared channel answer the speaker's own row alone,
whoever asks, so a channel another organization sits in never hears the staff list; a portal
session is always the signed-in member's own audience, so the panel needs no such branch.
Adding someone is `add_member`, the one verb that mints a member before their first contact: a
speaking admin on the main agent names a work email at the workspace's own domain — the domain
every join path already anchors on, and the one a sign-in resolves a workspace by, so a row at any
other domain could never answer for this workspace — and optionally makes them an admin in the
same act, since both fields carry the identical gate. An address that is already a member is
refused rather than silently promoted; changing an existing member's role or seat stays the
`member` kind's admin-gated apply. The new member is auto-seated while an included seat is open
and the verb reports which happened, because an unseated member is one the agent refuses. The
address must also parse as one `local@domain` with no whitespace: every creation path crosses that
shape gate, so an address no sign-in could normalize to and no channel-verified join could equal
never becomes a seated member the `member` kind cannot delete. Membership is managed in an internal
conversation only — the verb refuses in an externally shared channel, where its refusal would
confirm a colleague's membership and its success would mint a member.
The usage view answers every member their own window — the ledger rows their
conversations' turns wrote, the join a `member` cap binds on, beside their member-scoped caps —
and adds the workspace rollup for an admin, so a non-admin's payload names no other member and no
agent. Memory with no query lists 100 live items under the viewer's own subjects at a time,
newest first and narrowable to one item class the provider itself declares; a query searches every
agent the member reaches, one per-agent reader each, unioned and deduped — so source-derived pages
stay fenced by that agent's source grants (the same reader a turn's tools search under). A listing
pages by keyset, never by offset: one shared cursor (`created_at` with the row id breaking its
ties) and one page envelope carrying the positions its Newer and Older controls walk to, so a row
landing mid-read shifts no boundary and every listing pages identically. A cursor the surface
never minted is refused rather than answered with some other page. Shared files are the
member's own conversations' artifacts, every conversation's for an admin, each carrying the
signed TTL link a delivery would and paging by the same shared cursor, `shared_artifact.id`
breaking a tie two files one turn shared in one instant would otherwise leave unbroken; opening
one pins a viewer over the listing that renders what the page honestly can — an image inline,
text up to a bounded read, and a plain refusal to preview anything else — leaving the download an
explicit act rather than the click's default.

Every object kind reaches the portal through two generic reads rather than a page of its own:
`objects/{kind}` is one kind's rows and `objects/{kind}/{name}` is one row whole, each answering
through the kind's own gate (`member_page` / `member_detail`) in the agent namespace the request
names and the viewer's audience allows. Implementing those handlers is the opt-in: a kind that
cannot answer a member outside a turn is refused by name, never a fault from inside it. The index
searches, filters, and orders on exactly the kind's declared `list_fields` — the portal stamps that
vocabulary onto the query, so a page can offer only what the kind admits — and an index with no
rows states that the kind has none, or that none match what it was narrowed to; a view whose
extension a deploy need not install states that absence in its own words instead. A kind's own
description and guidance are the agent's tool prose and never reach a member. The detail reads in
the order `ObjectDetail` carries: the spec that was applied (null where the kind elides content
this member may not read, the row's own summary standing in its place), the declared fields that
are its live state, its typed links — each a kind and a name, opened by this same page where that
row answers this member, so a scheduled task's `reports_to` lands on its conversation, and stated
as plain text where it does not, since a link's kind reading for members is not that row reading
for this one — and the row's timestamps. Where the prepared-intent lane does not accept the kind,
the page offers no control at all; where the kind elided the spec, it offers no form to edit what
this member cannot read. A kind lists this way once its gate is derived from a member and an agent
alone, so the portal and chat's `object_list` cannot disagree; conversations read one row at a time,
as the target of the links that reach them. A kind whose gate cannot leave a turn stays refused by
name — `agent`, whose listing reports the concrete model an `auto` agent resolves to and takes that
resolution off the reading turn, and `page`, whose detail reads the body through the turn's blob
capability and whose listing scans every readable page. A
gate is never widened to admit a kind — the kind stays refused by name until its read no longer
needs a turn. A shared connection or source names
its owner only to an admin or the owner: the roster tells every member who their colleagues are,
but which colleague registered a given binding is the owner's to disclose, and chat names it to
nobody else either, so neither does a panel. A panel mutation
is a **prepared intent**: the form's structured intent is admitted as a turn on the member's one
durable intent conversation with that agent and dispatched verbatim to the typed object or tool
action — no model round, no fold into a live chat turn — so a submit applies exactly or returns
the kind's refusal, the turn is the audit record, and the per-conversation partition runs a
member's intents one at a time in order. A connect intent leaves the same private OAuth handoff
chat's connect_account does: the URL rides the turn's terminal and is minted per speaking member
at stream time, never in a transcript or an intent response. The Agents screen creates an agent
through the same lane — admin-only, on the main agent's lane, taking the initial prompt, the one
prompt write that is not an edit — and the boot read carries the kind's spec schema only to a
member it admits a create from, so the act is drawn exactly where the lane honours it. The
administration view mutates through the lane as well: member role and seat changes through the
member kind's guards, and web-audience grants riding the target agent's own lane to the same store
the chat verbs write.
Agent delete stays refused — the cascade over an agent's conversations, memory, and resources is
unbuilt. A workspace-scoped view has no agent of its own, so its intents ride the main agent's
lane — the agent every surface already routes an unbound member to: a source's resync, share, and
remove; a memory correction; and a credential slot's set, replace, and clear. Setting a slot's
value is the one mutation whose payload never enters an intent: the intent asks for the same
sealed `request_credentials` prompt a chat turn produces, and the value crosses only in that
prompt's private fulfillment, so a secret reaches no turn, transcript, or intent response.

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

Two-way attachments cross under explicit bounds at every hop: an inbound Slack file streams from
`url_private` in bounded chunks into the conversation's workspace before the turn runs; a shared
file (`share_file` → a `shared_artifact` record) streams from the blob store to Slack's chunked
external-upload API, into the conversation's thread (Slack forbids threading on a reply's ts). The
web composer's inbound files are refused unless the request declares a length the server frames
the body by — a chunked body, whose length no header can state, is refused rather than parsed — and
only then does the parse buffer each part within that length (in memory up to the parser's spool
threshold, a temp file past it); a plain text body is instead bounded by the bytes actually read,
and the workspace write accumulates one size-capped body per file before the turn runs. Outbound, the portal renders `artifact_link` downloads instead of
an upload. The portal renders agent replies as markdown through one sanitizing chokepoint: raw
HTML in a reply renders as visible text, never as elements; links open in a new tab carrying
`noopener noreferrer`; and an image renders only from the portal's own origin, so a reply can
never direct the member's browser to fetch an attacker-chosen host. A reply the parser cannot
handle renders as its raw text rather than failing the page. A conversation's Changes view renders
bounded file deltas from its durable tool results. `surface_identity` and `conversation.surface`
are open namespaces validated by surface registration, not a fixed enum.
Slack renders links to the exact web conversation and its agent configuration as the reply's final
context block for every workspace when the deploy has a public base URL. In the operator's own
workspace — the one whose first member's email domain is `OPERATOR_EMAIL_DOMAIN`, the fleet-level
constant naming us, never a tenant-level role — an internal channel's block also renders terminal
accounting, model metadata, and a debugger link.

Slack installs by either of two paths in chat, both landing the same per-workspace bot token and
identity. **Preferred — OAuth on the deploy's own app**: its client id, client secret, and signing
secret are read from the deploy's env (never the sandbox), `slack_connect` (default) returns an
**"Add to Slack" link** whose sealed state names the speaking admin and workspace, and the state-verified
OAuth callback exchanges the code for that workspace's `xoxb` bot token, binds the team, and records
the identity. **Alternative — bring-your-own app** (`slack_connect method="manifest"` + the
`slack-app-setup` skill): an admin creates an app from `slack_app_manifest`, fills the per-workspace
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
workspace, its initial admin, and its main agent.

## Accounting / billing

Every model call and tool call meters into `ledger` in the same commit as the step. Realtime
visibility: live per-turn cost on the stream, workspace/member/agent rollups in CLI and web. Caps
evaluated at inbound and per-step; `reject` refuses new turns, `park` suspends. Prices are a pinned
table per model; BYOK usage still meters (visibility without billing). Seats gate who the
agent answers: `workspace.included_seats` bounds silent auto-seating, `workspace.seat_limit` the
grantable ceiling; core owns the rules (admission refusal — including a member-surface speaker
who never resolved to a member, per-round park on revocation, the last seated admin's irrevocable
seat), and a billing extension's tools drive grants and ask an admin to approve overage seats. The
core-registered `workspace` kind is the shape itself as one read-only object — one instance per
workspace, named by its id, listing both bounds beside the member and seated counts and reporting
those with `billed_overage_seats` and a `roster` naming who holds a seat, which follows the roster
rule above — whole to a member asking the main agent, the speaker's own row alone to a child
agent, and nothing at all to an externally shared channel, which reads none of this kind. Its
spec carries no field, because nothing it reports is authored: both bounds are the plan's, the
counts are derived, and seating one member is the `member` kind's admin-gated apply, so create,
update, and delete all refuse.

An external billing vendor is an extension draining the usage-export seam
(`ctx.pending_usage_exports` / `ctx.ack_usage_exports`): core mints frozen, consumer-keyed delta
intents from settled ledger rows — settlement and dedup keys are writer knowledge — and the
extension is a pure delivery adapter (`metronome` ships them to Metronome's ingest API). Each
intent freezes a `byok` label at mint: host `tokens` whose model's serving provider key slot
(`ModelRegistry.key_slot_for` — the same resolution `client_for` applies, any provider) is stored
by the workspace, so the rate card bills only pass-through usage.

Buying the plan is a chat act like every other member action: a speaking admin asks, and an
extension tool returns a short-lived provider portal link for the payment method,
and one of the extension's jobs activates the plan once the payment provider reports a card —
provider ids and the pending package intent live in the extension's own store, so core gains no
billing table, callback, webhook, or route (`metronome`'s `manage_billing` + `billing_activation`
over Stripe and Metronome). The pending record pins the initiating conversation and agent so the
completion returns there rather than whichever admin spoke most recently. Hosted onboarding only
offers an admin the choice; the chat transport carries it.

## Model abstraction

`ModelClient`: `complete(messages, tools, stream)` + token accounting + provider image/content
limits. Core implementations: Anthropic, OpenAI. The Bedrock extension serves Mantle model IDs over
the native Anthropic Messages API and OpenAI-compatible Chat Completions and Responses APIs. Model
policy per agent (`auto` routes by task class); its `bedrock_api_key` credential falls back to the
deploy's `AWS_BEARER_TOKEN_BEDROCK`, as provider credentials come from workspace slots or deploy
config.

Image and video generation are not a `ModelClient`: `complete` yields text, tool calls and token
usage, and a registered `ModelSpec` is a brain an agent can be pinned to. So a media model is a tool
in the provider extension (`openrouter`'s `generate_image` over OpenRouter's Image API and
`generate_video` over its asynchronous Video API, both on the same key as its chat models), never a
registry entry. A video is minutes of provider work, so its tool posts the job and polls it to
`completed` or `failed` under its own bound, and a failed job's reason reaches the model as tool
text. The bytes land in the sandbox workspace and reach a member through `share_file`, and the
charge — per image, or per output second at the resolution tier filmed, not per token — meters under
the ledger's `images` and `videos` dimensions: the extension reads what the provider charged and
books it through the turn's context, because metering is core's. Only a generation on the platform's
key meters. Those rows export as platform-served, since `byok` resolves a key slot through the model
registry and no image or video model is in it, so a workspace running its own provider key — already
billed by that provider — is not metered at all rather than billed twice.

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
ufoctl init                   # writes ufo.toml; onboards workspace + initial admin + main agent + model key
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
| Sandboxes | Per-conversation, resumed across instances from the durable `sandbox_handle`; an in-cluster carrier needs its workspace root on storage every instance reaches. |
| Surfaces, webhooks | Stateless behind a load balancer; sessions and idempotency live in Postgres. |

Two invariants make this safe, and they hold even single-instance:

- **At most one running turn per conversation** — the DBOS queue serializes on the conversation
  key; the transcript's monotonic seq depends on it.
- **The workspace lives in the sandbox** — a turn's `sandbox_conversation_id` names the conversation
  whose row's `sandbox_handle` holds the container, its own unless it inherited the sandbox of the
  turn that spawned it, so any instance resumes exactly that sandbox and none may destroy one.

Misconfiguration fails loud at boot: the shared owner DSN must be set and no surface may claim a
reserved onboarding route. Instances heartbeat a `runtime_instance` row so the fleet tracks its live
executors; a peer that stops heartbeating has its in-flight turns recovered by the survivors.

### Roles — the split that's already paid for

An instance logically comprises four roles: **surfaces** (HTTP in, streams out), **workers** (turn
workflows), **jobs** (sync, derivation), **proxy** (sandbox egress). Core runs all four in
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
| Brief pipeline (typed outline → draft → critic stages the agent chains) | subagents, skills |
| Composio / Pipedream connector brokers | connectors, routes (OAuth) |
| Docker, E2B | carriers |
| Redis stream hub | hubs |
| turbopuffer index | indexes |
| GitHub / Asana feed-sync sources | sources, credentials, auth_proxies (`direct`) |
| Agent-guided education / onboarding | onboarding, tools |
| Scheduled tasks (cron / one-time) | jobs, invoke, tools, requires (`memory_search`) |
| GH code review on PR | sources, hooks (page_change), credentials, invoke, subagents, tools |
| Service self-improvement / bug-fixing from o11y | sources (o11y), jobs, trajectories.read, invoke (evals), agents.propose_change |
| Security review | tools, subagents |
| gbrain-style memory (source page → derived facts, consolidated) | memory_search, sources, hooks (page_change) |
| CRM / ATS | connectors, sources, hooks (page_change), tools |
| Websites hosted at a permanent link | tools (sandbox serving), surfaces (the access-controlled frame), objects (`site`) |

Packs (activation bundles, not code — see Packs): **assistant** bundles memory, the browser pack
(its BUA engine over the default `sandbox_chrome` transport), brokered connectors, and web research
(the research tools over the Exa search backend) (the flagship); **chief-of-staff** bundles
brokered connector grants plus feed sync (Google Meet transcripts and Gemini smart notes, Slack, a
folder-synced state repo) with memory, the Slack surface, scheduling, todos, workspace
skills, and self-improvement behind four pack skills (`sync`, `prep`, `triage`, setup).
**support bot** bundles knowledge sources, keys onboarding, and websites.
Each activates one coherent config, no code of its own beyond what it references.

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
