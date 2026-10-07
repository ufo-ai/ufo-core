# Ufo Spec

Ufo is a tenant runtime a developer can run, read, and extend. It composes the product-neutral
**`ufo.harness`** agent executor with workspace identity, durable scheduling, authorization,
accounting, surfaces, and an **extension** system through which nearly every easy-to-vary
capability is built — connectors, data sources, tools, subagents, onboarding. A **workspace** hosts
one team and its agents.
Agents accumulate capabilities through **grants made in chat** — never borrowed from whoever is
speaking.

## Principles

Long-term product principles (the destination all design serves):

1. Multiplayer / permissioned.
2. Open source / on-prem / hosted.
3. Agents build with real infrastructure (Kubernetes is the enterprise upgrade, wrapping this runtime).
4. Agents are granted access via connectors through chat — never via caller identity.

Runtime doctrine: **if a capability can be an extension, it is not runtime.** The harness contains
only product-neutral execution. The runtime earns a module only when an extension cannot express
it: tenant identity and authorization, durable persistence, surfaces, accounting, extension
hosting, and the adapters that bind those concerns to the harness. The example-extension list at
the bottom is the acceptance test for the extension API — every entry must be expressible without
touching runtime internals.

## Fixed decisions

| Decision | Value |
|---|---|
| Language | Python 3.12+, uv. One `ufo` distribution contains the source packages under `core/src/ufo`: `harness` owns agent execution, `runtime` owns the durable tenant host and the platform contracts — access, billing, identity, turns, surfaces, the extension API, tools, prompts, skills — and `host` provides the environment from above: extension discovery, builtin tools, the injected per-turn binding, the dev host. Onboarding, the SDK, and shared persistence remain siblings. `extensions/*` and `packs/*` compose around them; first-party extensions and packs register through entry points. Rust was considered and rejected for the active runtime: the salvage is Python, DBOS has no Rust SDK, the loop is I/O-bound, and extensions must be writable by users and agents in the AI ecosystem's default language. The one hot data plane, the egress proxy, is a standalone Rust service (`ufo-egress`) that resolves policy and keys through the runtime over an internal RPC. File rasterization and hosted-site capture are the second such service (`ufo-preview`): LibreOffice, pdfium, ffmpeg, and Chromium behind one HTTP verb, no storage credentials, no state. Share and hosted-read bytes go directly from sandbox or object store to the service; hosted sites arrive through a runtime-minted ingress view and leave through a preview-key PUT; a connected terminal, which cannot resolve the synthetic service host, relays one contained and bounded document through the deploy. |
| Persistence | One async-SQLAlchemy schema over **SQLite by default** (aiosqlite, WAL — zero services for dev) and **Postgres for deploys** (asyncpg); alembic migrations are the single schema source, dialect-neutral (integers for money/tokens; dialect-only types live inside IndexBackend impls). Plus a pluggable blob store (transcripts, rollover records, sandbox workspaces, shared artifacts): **local filesystem by default**, S3-compatible for deploys — the S3 API is the cloud-portability seam. Every row carries `workspace_id`; one shared fleet serves every workspace, each request, turn, and job binding its `workspace_id` as the ambient `ws(...)` scope its statements filter on. Blob keys are workspace-relative: the store prepends `workspaces/<id>/` from the ambient `ws(...)` scope, and deploy-owned data (`static/`, `term/`) rides a fleet store whose namespace is closed. |
| Durable execution | DBOS on the same database as the schema (SQLite dev / Postgres deploys): a turn is a durable workflow, a subagent a child workflow; queues, async cancel, crash recovery. Shutdown stops admission, gives requests `[serve].request_shutdown_seconds`, waits `[serve].graceful_shutdown_seconds` for active workflows (the standalone `ufo-egress` proxy drains its own live tunnels for that same window on its own SIGTERM), then retires the executor heartbeat only if no workflow remains active — a workflow that outlives the drain keeps the seat, so no peer re-dispatches work this process still executes; the seat ages out with the process. The supervisor's termination budget exceeds the sequential drains. DBOS-on-SQLite is verified in U1 — fail loud, never silently fall back to requiring Postgres. Dequeue poll interval and system-DB retention are configured from day one. |
| Streaming | Durable terminal frames in Postgres; live token deltas through a hub interface — in-process in the single-process default, a Redis hub extension for multi-instance deploys. A lost delta costs a redrawn token, never correctness. |
| Topology | `ufoctl serve` is one process on one event loop: surfaces + DBOS workers + jobs, with `ufo.harness` called in-process through typed ports. The package seam is not an HTTP seam. Everything is async-native — a blocking call stalls the whole deploy, so blocking-in-async fails lint. Scale-out = more instances plus a shared hub. `--fleet` divides those instances by the durable work they claim, and a deploy runs the two the queue registry partitions into: `turns` takes the member queues and the surfaces' live delivery, `jobs` takes the background execution queue (`ufo-serve` and `ufo-jobs`, one image, one config, one composition root). A third, `api`, claims no queue and no live delivery: it serves routes and recurring job ticks only, so route traffic scales apart from both. Recurring job ticks remain on DBOS's shared internal scheduler queue: their bounded candidate read and enqueue fan-out can run in any fleet, while the per-workspace handler they dispatch runs only in `jobs`. Turns are model rounds and network waits; a job's chunking and embedding holds the GIL, so on one interpreter a large reindex starves the portal, the turn loop, and every surface at once. Each fleet's replica count is then its own. A single node stays one process, which claims both. |
| Sandbox | A local temp-dir carrier is the runtime default: every command runs under the kernel's sandbox (`ufo sandbox` — Seatbelt on macOS, Landlock on Linux) with writes confined to the workspace, the conversation's `$UFO_HOME/runs/<id>`, the scratch home, and the temp dir, and the whole filesystem readable; egress is proxy-scoped/metered only for clients that honor the proxy env, not kernel-enforced (model keys still stay fail-closed via the sentinel). It is the development / trusted-input default; use Docker or E2B (carrier extensions on the `carriers` point) for untrusted input, isolation, or multi-tenant deploys. A container carrier's boundary is the kernel's. The file tools (`ufo fs`, Rust, in the image) refuse a symlink or an escape at the path they are handed; every other path the host names inside a container goes as named. A conversation opened from a connected CLI terminal takes the `client` carrier instead — its workspace is the member's own `$PWD`, its ops the member's own subprocesses — unconfined, since it is the member's own shell, offered only to a terminal the member connected. |
| Models | Model providers are an extension point; the runtime ships Anthropic + OpenAI direct clients behind one `ModelClient` interface. OpenRouter ships as an extension. |
| Observability | OpenTelemetry APIs only in product code; the OTLP export target (Datadog, …) is deploy config. No vendor SDK in the runtime. The operator debugger projects an opened turn's DBOS steps with their timestamps and durations, and links the turn to the pages `[debugger] turn_urls` names (its trace, its logs), filled from its `traceparent`, its conversation, and its window. Output the process did not write crosses on one record only: a tool call that did not end ok warns with the error result's text — bounded to the tool result cap, its credential shapes (a URL's userinfo, an Authorization header's value) scrubbed by value — so an operator reads what failed off the record rather than off a counter; that the text can echo the environment the failing command ran under, or a member's own secret, is a risk accepted for that record and no other. |
| Kubernetes | Absent from the runtime by construction. The enterprise offering wraps the runtime with k8s (principle 3); no runtime module may assume or import it. |
| CLI | One operator CLI: `ufoctl` (`serve`, `bundle`, `ext`, admin verbs). |

## Harness, runtime, and host

`core/src/ufo/harness` owns agent execution. Its engine core — the concrete agent engine with
canonical in-memory messages, reasoning, tool calls, tool results and schemas; immutable agent
definitions; model requests; round progression; tool scheduling and ordered result assembly;
finish and recovery behavior; round budgets; context-window decisions; the sandbox command/file
protocol; reply interpretation; and untrusted-content framing — imports only the
standard library and other engine-core modules (gated), so an outside runner executes it with no
tenant runtime, extensions, HTTP, database, or durable executor. Around that core the package
carries the rest of execution machinery: model provider clients, sandbox carriers, the replay-safe
DBOS serializer, and observability plumbing. The engine's four ports are model, tools,
conversation, and events.

`core/src/ufo/runtime` owns the durable tenant host and the platform's contracts: the turn
workflow that establishes workspace and agent scope from the durable turn record and adapts
product effects to the four harness ports; the tenant domains it composes — access, auth, billing,
seats, workspace scope, turns, surfaces, sources, kinds, and media; and everything its machinery
consumes — the extension API (`runtime.ext`: manifest schema, capability contexts, hooks), the
tool contract (`runtime.tools`), prompts, and skills. It cannot
supply whole-round close, dispatch, recovery, or exhaustion implementations. DBOS workflows and
steps remain runtime-owned; their arguments and outputs are pickle records, so a class that moves
lands with its `MOVED_MODULES` entry in the replay serializer and each step converts a harness
value to its runtime boundary type before returning.

`core/src/ufo/host` provides the environment from above: extension discovery (entry points,
lockfile, catalog), the first-party builtin tools, and per-turn assembly (`HostEnvironment.assemble`
in `ufo.host.assemble`, the runtime's `TurnEnvironment` port) — for every turn, the host composes
the prompt, the tool offer the runtime's access policy authorized, and the skills, and hands the
bundle back. The layering is one-way and gated — host imports runtime and harness; the runtime and
harness never import `ufo.host` — so host contributions reach the runtime only as values a
composition root injects (`Runtime.manifests`, `Runtime.environment`). A turn admitted with an
environment document pinned (`TurnRuntimeConfig.environment`, the `x-ufo-environment` header —
independent of `x-ufo-model` and internet narrowing) has that assembly reshaped through the
document before the engine runs: uploaded once as JSON or YAML through the terminal surface,
content-addressed by sha256, read from the workspace's blob store with no server anywhere,
replayed byte-identical on recovery, and applied per target — a `main` block for the member
agent's turns, a `profiles.<name>` block for each spawned profile, top-level `tools` wherever a
name is offered, and `skills` replacing or adding whole skills. A block pins its target's model,
replaces or edits the prompt, rewrites tool and parameter descriptions, withholds tools, and
defines `run` tools whose implementation is a command in the turn's own sandbox. Overrides narrow and cannot grant — a
scoped name outside the authorized offer fails the turn, and a `run` tool grants nothing the
sandbox's shell does not — so prompt, description, and skill experiments run against a shared
stack without expanding any member's access, and the turn row's digest is the audit. The `ufo`
client drives the whole experience: `--environment` takes a file or a digest.

Prompt and schema configuration finishes before execution starts. `AgentDefinition` holds the
system prompt and execution limits; `AgentTools` supplies the exact definitions offered each round
and the tool-call effect. `AgentEngine.run` never loads extensions or speaks an extension
protocol; the host layer's assembly consumes this seam from outside the engine.

A successful tool can supply `ToolResult.completion` to finish the member turn without another
model call. The engine records results and usage first. Errors, pending member guidance, and
structured output keep the normal model loop. Only the engine can enforce this boundary.
Extensions create agents through `ufo.sdk.objects.apply_agent`, which uses the same ownership,
duplicate, authorization, and change-journal checks as `object_apply`.

A gateway in front of a deploy calls an extension's deploy routes (`/internal/<extension>/*`) for
workspace choices, membership, seats, and fleet state. Rust `ufo-egress` remains the wire proxy; it
calls the runtime's `/internal/egress/*` routes for policy, credentials, and metering. Neither
service links or hosts the agent harness. The sandbox ingress is the third such process — a reverse
proxy with no turn engine — and calls the runtime's `/internal/site-not-answering` route so a hosted
site whose server has stopped reaches the conversation that built it. Its own report token is that
route's whole gate: the deploy secret signs the workspace, conversation and port the request was
already gated by, under a kind neither hop of a visit accepts. A stopped dynamic site's recovery
turn runs standalone with no speaker, connections, or public internet, so it keeps the model and
sandbox it needs without folding into a broader live turn.

## Workspace model

Every trusted boundary — turn execution, OAuth callback, sandbox proxy — binds
`with ws(workspace_id), agent(agent_id):` from its durable record or signed claims. Agent-scoped
capabilities derive both keys from that scope; only durable records, admission assertions, and
workspace-administration reads carry an explicit agent id.

Isolation is explicit: a statement carries a `workspace_id` predicate on the bound workspace, or
keys on an id read under one, and every sweep and job body filters on the workspace its dispatcher
bound. A deploy may add row-level security beneath it; core holds the hooks such a deploy needs as
contracts:

| Contract | Holds |
|---|---|
| `app.workspace_id` | `workspace_tx` pins the bound workspace as this transaction-local Postgres setting (`SET LOCAL`, cleared at commit). |
| `ufo_workspace_rls` | The policy name such a deploy installs on every public table, comparing `workspace_id` (`id` on `workspace`) with that setting. |
| `[database] owner_url`, `UFO_OWNER_DSN` | The owner pool `owner_tx` and ingress read through, `UFO_OWNER_DSN` first: the one cross-workspace path, naming workspaces to bind. `migrate` runs as `UFO_OWNER_DSN` when set, else as `database.url`. |

| Reach | Holds |
|---|---|
| Workspaces | `ufoctl init` founds a deploy's one workspace; a second exists only where an extension provisions it and signs members in. An operator verb acts on the workspace `--workspace-id` names. Without it, `turn cancel` and `turn steps` act on the turn's own workspace, and every other verb on the one workspace, refusing a deploy that holds several. |
| Sandboxes | The local carrier reads the whole host filesystem, so workspaces on one deploy are isolated from each other's sandboxes only under a container carrier (Docker, E2B). |

Tables (all keyed by `workspace_id`, `created_at`, `updated_at`):

| Table | Owns |
|---|---|
| `workspace` | The team unit: name, config digest. Members are unlimited — nothing bounded or counted here. |
| `member` | A human. `is_admin` grants workspace management to any number of members; onboarding makes the first member an admin, the last admin cannot be removed, and at least one seated admin remains able to act in chat. `seated_at` marks a member the agent answers: set by the row that creates them (the column's own default, so no creation path can mint a member the agent silently refuses), cleared only by an admin's revoke in chat, gated at admission and per round. `timezone` is the latest valid IANA zone received from chat metadata; UTC is the default for member-local jobs. Clearing the seat is the one way to remove a person's access, since the row is an identity and a memory subject that outlives it and the `member` kind refuses delete. Surface identities link here (Slack user id, CLI token, web session) — one human, many surfaces, one memory subject. |
| `surface_installation` | A chat installation's unique external identity → workspace binding, bound to one agent — the agent every conversation the surface creates lands on (a new binding lands on the workspace's explicit main agent). Shared ingress uses it only to select a candidate credential, authenticates the original request bytes, then binds that workspace. |
| `agent` | A configured agent: name, `purpose` (one sentence saying what it is for, which the portal states wherever a member meets the agent before opening it — required of an agent an extension ships, and a member's own to rewrite), icon (one of the portal's own element marks, stamped at creation and re-picked by a member from the ordered set the picker offers whole; any other name still validates and still draws wherever a tabler outline mark answers it, so a row written before the pack keeps its mark), prompt, model policy, reasoning effort, sandbox size, granted tool set, skill packs, `use_workspace_skills` (whether its turns load the workspace's member-authored skill set), memory scope, and an optional raw-JSON-Schema I/O contract a spawn of it validates against (reference- and pattern-free — $ref and regular expressions are refused at the write — and unset means task in, result out). Any speaking member creates one and owns what they created. `visibility` defaults to `private`: its owner and workspace admins read the object; `workspace` lets every member read it. The owner or a workspace admin edits it, and an ownerless row — the main agent, a provisioned agent — answers to admins alone. Exactly one per workspace is `is_main`: onboarding creates it and unbound surfaces route to it, and it is the chat app's row (`agents`, below) — the app's shipped page is the page of the agent every unbound surface already routes to. `archived_at` retires an app: the row admits no turn, leaves every surface, and releases its workspace name. `archived_name` keeps the name a member saw while `name` moves the retired row outside the object-name grammar, so the full unique constraint stays valid. The main agent is held live by CHECK. A row an extension shipped (`agents`, below) additionally records that extension, the name it declared, and the version that created the row. |
| `connection` | One provider account per `(workspace, provider, account)` — the only thing a member names, shares, or grants. It holds no secret. Its metadata is read by its owner, every member when shared, and an admin; that visibility grants no use or mutation authority. `owner_member_id` is the member who completed consent, and null for the workspace's own connection: a keyed provider, whose handle is empty and so is one per provider, or a configured feed, whose handle is its config identity and so is one per feed — a folder root or a repository is its own connection, and deleting one never cascades another. `shared` is the one sharing act, and CHECK holds an ownerless connection shared — nobody's to keep private. `account_label` names the account. `base_url` is the tenant API URL a per-tenant provider dials. Only the providers core's per-provider rule table names take one, and only matching that provider's host and path shape; every other provider has a fixed host and refuses a `base_url` outright. The table is a security boundary, not a convenience: a keyed connection's sync sends the workspace's provider key to whatever host it dials, so an unchecked origin would let an admin export a secret they cannot otherwise read. `backfill_days` is how far back each of its streams first reaches, bounded by CHECK between one day and all history. `connect_account` creates or reuses it and refuses to reassign another member's account. Deleting it deletes its source rows, their pages, and every connector grant, by cascade in one statement — nothing outlives the authority that fetched it. A keyed connection nobody consented to carries no `connector_grant` at all, so the main agent reads it and no specialist does until a member attaches it through the `connector_grant` kind: registering a feed grants nothing, because a grant is consent over an account a person owns. |
| `connector_grant` | One connection → agent attachment, and the only access edge there is: it records which agent may reach the connection and nothing else — sharing lives on the connection, and the turn that granted it is the audit record. `connect_account` creates the intended edge; applying the `connector_grant` object attaches a connection the workspace already holds — cross-agent from the main agent on a live member-requested call, so a held account reaches a new agent without another OAuth round trip — or revokes only the current agent's edge. Sharing is not among its verbs: `shared` is a field of the `connection` kind and `set_shared` takes a connection id, so one act on one row decides what every agent holding it may use. A call admits the acting member's private attachments plus shared connections, preferring private. The acting member is the speaker bound to the call, else the member the turn acts for — `turn.member_id`: its speaker, or the creator of the task, trigger, monitor or pause that fired it, inherited by spawned children, delivered results and pause resumes; a turn acting for nobody admits shared attachments only. The same decision governs connector selection, CLI export, proxy injection, git credentials, synced content, and object discovery. A broker call rechecks the exact edge generation after file staging and immediately before execution, so revoke, disconnect, regrant, or narrowing cannot spend a stale selection. |
| `source` | One stream of one connection: not nameable, not shareable, not grantable. It carries no authority of its own — `connection_id` is NOT NULL and cascades, disclosure derives from `connection.shared`, and which agents may read it is `connector_grant`'s answer at read time. Its id is content-addressed over the connection and the fields of its config that say which dataset it is, so one stream of one connection is one row however many callers register it, and the same stream under a second connection is a second row. There is no soft delete: a row that means gone has no reading where the connection is what goes. |
| `source_trigger` (sources extension) | One owning conversation → shared-source wake-up, named and described by the agent that applies it, as a scheduled task is. A member creates it, establishing consent for its exact connection; `created_by_member_id` owns the management row but is not an execution principal, while `requesting_message_ref` preserves the exact authenticated request each fired turn classifies its member-sensitive calls against. Every source batch returns to that conversation without a speaker, acting for its creator and under the creating turn's internet ceiling. Every alert founds its own internal turn and parks rather than discarding completed sync work on a spend refusal. What wakes a trigger is `when`: a list of JMESPath expressions evaluated over every changed page of the connection, against the page's `stream`, its `title`, and `page`, the provider's own record, through JMESPath's own functions plus `lower`, which folds a string so the case a provider stores a name in cannot decide a match. **A trigger wakes when a page starts to meet an expression, not while it keeps meeting one** — a page that met it last time and meets it again reports nothing, so a pull request whose checks went green is heard once and is silent through every later edit of its page, and its merge flips an expression of its own and is heard again. One bit per trigger, page and expression is what that rule remembers, and it dies with the trigger. A tombstoned page meets nothing, so a removal wakes nobody and takes its bits with it. An expression that does not parse is refused at apply; one that raises while running pauses its own trigger and writes the error where the member reads it, rather than failing the hook and replaying the batch for every trigger in the workspace. The extension's `user_prompt_submit` and `post_tool_use` hooks offer one, as text and nothing else, whenever a link in a member's message, a spawned child's result or a tool's output names a synced resource — never in a spawned child's conversation or a room the extension opened — and the offer carries the expressions for that resource, so nobody types one; it lists what the conversation already wakes on beneath itself, and whether that covers the link is the agent's reading of the expressions rather than a comparison of URLs. Each provider owns the two rules that turn a link into expressions and that name its whole-feed minimal set, inside the extension; a provider with neither resolves to one expression every page meets, which is a wake per new page. The name is the table's unique key, because it is what every surface asks for one trigger by; the expressions carry no index, so what a member may write is not what a btree page holds, and a conversation is kept from holding one watch twice by a read of its own rows before the insert. One thread holds as many watches on one feed as it has expression lists. `connection_id` is a foreign key that cascades, so disconnecting an account takes its triggers with its streams. Seen by whoever reads the owning conversation, plus its creator and an admin; deleting it is the creator's or an admin's. The extension owns the table and its migrations. |
| `credential` | BYOK secrets, encrypted at rest. Slots are declared by extensions; values are workspace-scoped. A model provider's key slot is additionally member-scoped (`<slot>:member:<id>`): a member connects their own provider account from the portal, and a live selected member may grant a spawn the exact provider slots its profile's ordered model route can use. The child persists those opaque slot capabilities and its descendants inherit them unchanged. Models before the route's final entry run only through matching connected accounts; the final entry is the workspace/deploy fallback. An explicit spawn model replaces that route and uses the matching member account when connected, otherwise the workspace/deploy key for that exact model. Ordinary turns and automatic work use the workspace or platform key and never infer a member slot from a creator, conversation owner, or turn. The slot holds the whole grant, refreshed in place under a claim so one refresh token is spent once. |
| `conversation` | Surface context ↔ turn-serialization key (Slack thread, CLI session, web session), permanently bound to one agent at creation — its surface's installation binding, else the workspace's main agent. Admission derives every turn's agent from that binding; a caller-supplied agent id is an assertion admission refuses on mismatch. Its persisted `Audience` atom is `shared`, `member:<uuid>`, `room:<surface>:<room>`, or sealed `foreign:<surface>:<room>` and is carried unchanged through the turn. `surface_label` is the origin in the surface's own grammar (Slack: `#general`, `DM`), written by the surface that owns the encoding and never parsed by core; a surface that names none leaves it null, and a rename corrects on the next message that carries the name. The `conversation` object exposes it as a spec field and a filter/order field, under the same audience gate as the row; its member listing and detail read are the portal's one row for a listed and a resolved conversation alike. `title` is what the conversation is called: the member's own opening words, written by the turn that opens it and rewritten by the title job with a summary of its opening exchange. `title_summarized` is that job's whole state — false until the summary has run, true afterwards whether or not the model wrote a usable name, so one conversation costs one summary and an exchange nothing can name is recorded rather than read on every tick. Every conversation a member spoke in and an agent answered is that job's work, whatever surface holds it, so a Slack thread and a CLI session are named the way a portal chat is; a conversation nobody spoke in is an extension's errand, and one whose turns have yet to answer has no exchange to name, so each keeps the name it opened with. It is content, withheld with the rest from a row the reader may not read, and it is what a conversations search narrows on — in the listing query, ahead of its bound, so a conversation older than the bound is still reachable by name. |
| `turn`, `transcript` | The loop's durable log: lifecycle, full-conversation transcript + rollover records, speaker attribution, and the member each turn acts for. `speaker_member_id` attributes a founding authenticated member message; automatic scheduled-task, source-trigger, monitor, pause, notification, site-recovery, and subagent turns carry none. `TurnRuntimeConfig` holds the model, internet ceiling, and environment a turn may use; `turn.model_accounts` holds its exact model-account slots. Pause, monitor, and source-trigger rows store the internet ceiling as a total bit; PostgreSQL derives an omitted insert from the intersection of all running turns in that conversation and denies it when none exists. `fired_by_kind`, `fired_by_name`, and `fired_by_title` name the object whose fire admitted the turn — a scheduled task, a source trigger — stamped by admission and never rewritten, so the core `turn` kind lists runs (`fired=true`) and addresses their settings after the object is edited or gone. One distinct active authenticated member speaking alone binds each call automatically. With another member or an unattributed active message, omission of `requested_by` means conversation-common work; a named active member message selects a candidate whose consent is checked before their private subjects, credentials, grants, or handler become reachable. A denied message grants neither route. Spawn, source-trigger, and monitor continuations carry the exact authenticated message that selected the work; their turns stay speakerless and automatically bind that sole causal requester across every round with the authority a live speaker has — the member's message was the granting act, so a fire of their own trigger, monitor, or spawn asks them nothing again. Other speakerless work binds no member. Seat liveness gates authenticated speakers and selected member calls, never automatic work. Every speaker may fold into the same live turn because the sandbox and conversation are shared; member access binds only around one gated call. Spawned and tool-bridge children inherit exact runtime capabilities, and a profile route adds only the requester's exact sealed model-account slots whose providers its preferred models name. A background child with a long provider `Retry-After` parks with `retry_at`; the turn dispatcher resumes it when due. Sub-turn steps — each model round, tool call, and rollover — are DBOS's own `operation_outputs` step log, memoized so crash recovery replays completed work instead of redoing it. `created_refs` records objects as they are created, before any terminal. |
| `member_authorization` | One requested member decision over one exact validated, policy-rewritten call and its unresolved object target in a conversation: selected message, deciding message, their dispatch identities, decision, grounded evidence, and agent/call/effect identity. With another member or an unattributed active message, each round preflights its selected calls concurrently through `gpt-6-luna`, which classifies only the selected authenticated member's words; dispatch runs pre-use policy before private member capabilities bind, reclassifies a rewritten effect, then rechecks durable state and settles in call order before target resolution, credential selection, sandbox wire authorization, or handler access. Unclear consent over a fully disclosable, bounded effect records one pending row per member and conversation and ends the turn with `Allow`, `Deny`, and `Always Allow`; the question carries the model's one-line summary of the effect beside the account, operation, and access it binds and one labeled line per argument and target leaf field, nested objects flattened under their path — a short plain-text value verbatim, any other by its length, a key that is not plain text quoted with its control characters escaped, `MEMBER_AUTHORIZATION_FACT_CHARS` characters of facts at most so the whole question fits every surface's control — never JSON. The classifier reads the disclosed effect clipped to `MEMBER_AUTHORIZATION_EFFECT_CHARS` and flagged incomplete past the clip, which its prompt answers with `ask`. A credential-bearing or incompletely disclosable effect fails closed with a factual refusal and records no authorization row. The answering message settles that exact pending row. `Deny` refuses one occurrence; a different later effect supersedes an unanswered request without attributing a denial to the member; explicit revocation is a distinct decision. Replaying either dispatch returns its recorded result; another dispatch cannot spend a one-shot allow. |
| `member_permission` | One active `Always Allow` decision for an exact member, agent, call, and effect digest. Only a fully disclosable, bounded effect can be stored: the projection preserves its exact safe request content and prompts and carries the keyed digest of those semantics; a withheld value rejects the write rather than producing a redacted permission. The member-private `permission` kind lists and reads these rows and revokes one by delete; explicit revocation in chat does the same, while `Deny` leaves it active. Create and update are refused because granting remains a chat decision. |
| `memory_item` (memory extension) | Memory scoped to one exact audience subject. The memory extension owns this table via its own migration; the index backend owns `chunk`. |
| `monitor` (monitors extension) | A durable watch: a speakerless shell probe run in the conversation's shared sandbox on an interval acting for its creator, whose changed output, failure streak, or deadline fires exactly one speakerless arrival acting for the same member. `created_by_member_id` owns the management row but is not an execution principal; `requesting_message_ref` carries the authenticated request whose authority each member-sensitive call in the fired turn rechecks. The arming turn's narrower internet policy reaches every probe and fire. Retired on fire, re-armed explicitly, read back as the `monitor` kind. The extension owns the table via its own migration. |
| `pause` (scheduled_tasks extension) | A workflow wait: one per conversation, fired without a speaker or member model accounts, acting for the member the arming turn acted for and under the arming turn's internet ceiling unless a member message arrived past its watermarks. A resumed turn gates any private connector call from its own active member messages. Re-arming replaces the generation and its internet ceiling atomically. The extension owns the table via its own migration. |
| `ledger` | Metered usage: every model and tool call, priced. |
| `spend_cap` | Caps by scope (`workspace` \| `member` \| `agent`), dimension, window; `reject` or `park` on breach. |
| `job` | Recurring/one-time background work (source sync, page-change fan-out, turn dispatch, subagent result delivery, extension jobs). |
| `scheduled_task` (scheduled_tasks extension) | Agent-namespaced, member-private recurring invocation with names unique per agent and optional UTC expiry, enforced before invocation. The extension owns the table — core migrations created it and it was adopted in place; fires ride the internal `invoke` capability as scheduled turns. Creation binds the executor, its reporting conversation, and that turn's internet restriction. `created_by_member_id` owns the management row but is not an execution principal. Stored internet scope either inherits the agent policy or denies internet; an omitted writer value denies internet. An internet-scoped turn addresses only tasks within its scope, so it cannot reactivate a wider task. Updates never move the task or widen its internet scope and never rewrite the marks a fire left, so run history outlives an edit. The main agent may target an existing child-agent task from any conversation: its creator may inspect, edit, pause, resume, run, or cancel it; an admin may list management metadata, pause a running task, or cancel it, but cannot resume or run it or read or change its cadence, expiry, prompt, description, or responses; another member cannot see it. A paused task keeps its definition and run history and fires nothing until its creator resumes it; a cancel deletes the row. Each recurring turn carries no speaker, acts for its creator, and carries only the exact claimed UTC occurrence and stored internet capability; when its following occurrence reaches expiry, runtime adds a continuation check-in to the completed work. An apply carrying `run_now` places the first fire in the present, so the next runner tick fires it and the schedule owns every fire after it; `run_now` is an act and is stored nowhere. |

## Workspace export

The memory extension binds `workspace:export` to the chat object-action dispatcher. An admin
requests it in their private conversation with the main agent. The workspace Admin tab is visible
only to admins; its export button admits the same action through the prepared-intent lane and
returns after recording a durable request. The memory extension owns the export row and job.
The runtime supplies authorized, batched reads of its conversation, turn, message, reply, and
artifact records; extensions cannot read these runtime-owned tables directly. Memory supplies its
own records and shared member profiles. The extension context supplies an export-only storage handle because a background job has no
tool context. That handle writes and deletes only export objects. Original files stream from the
authorized records staged from the runtime snapshot; extensions receive no general blob-store handle.

The job runs once per workspace through the durable job queue. A pending row survives a restart;
a recovered execution rebuilds an incomplete archive. The Admin tab reads queued, preparing,
ready, failed, or expired status from the database. Concurrent requests share the active export;
replaying a completed request never rebuilds it.

The TAR archive contains numbered JSON lines files and original artifact bytes. Database records,
including memory, share one repeatable-read snapshot. The job stages records in private temporary
files and closes that snapshot before any object-store transfer. Staging uses disk in proportion
to metadata size and closes and removes its files on completion or failure. Original files stream
in bounded chunks and must match
the size and digest in that snapshot. A missing file or mismatch fails the export without publishing
a download. The manifest states exclusions, the snapshot start, and completion time. Admin exports
include every member's private, private-agent, room, archived, and retained deleted conversations
and memory. Other workspaces, externally shared channels, connector content, source pages,
credentials, configuration, usage, audit records, model context, and search indexes are excluded.
Export objects live outside shared attachments and do not enter later exports. Previously generated
archives remain excluded by their artifact marker.

Production uploads use multipart S3 writes with 64 MiB parts and enforce the 10,000-part
object-store limit. Metadata is read in bounded row batches without a total record limit.
An admin-only download read creates a fresh signed S3 GET, valid for at most 15 minutes and no
longer than the archive's remaining retention. The browser downloads directly from S3. Local
filesystem deployments serve the same authenticated read as a stream. The requester must remain a
seated admin at execution and publication; every status and download read checks current access.
Downloads expire 24 hours after completion. The job deletes expired objects; S3 lifecycle rules
remove tagged export versions and abandoned multipart uploads. A signed link can be used by anyone
holding it until it expires. The confirmation and Data ownership documentation disclose private data.

## Agent loop

One runtime workflow: inbound → admission (identity, spend preflight) → queue row → harness-backed
worker turn → runtime effect steps → terminal frame. A client's wait always ends — the terminal
state commits on the failure path too.

- **Tool calling** — typed registry; per-call metering; results bounded before hitting the model.
  Every member inbound carries a stable `message_ref` from its existing turn/message id. A tool
  call's optional `requested_by` must name a visible, non-denied message already absorbed by this
  turn or carried as the authenticated cause of its spawn-result chain. Only a callable that consumes member-scoped connector or credential authority or mutates
  an access grant declares the field; its schema stays fixed as speakers change. One active
  authenticated member speaking alone binds such a call automatically, regardless of surface or
  conversation audience. With another member or an unattributed active message, omission means
  common work, while a named message selects its author for a side `gpt-6-luna` decision over the
  member's words, the validated call arguments, and the unresolved object target. A round
  preflights those decisions concurrently, then dispatches in call order. The preflight checkpoint
  stores policy-rewritten arguments and authorization decisions; dispatch rebuilds live context
  and checks the current standing binding against the recorded request.
  Pre-use policy runs before private member capabilities bind; a rewrite causes the final effect to
  be classified again.
  Exact standing permission admits automatically.
  Ambiguous consent over a fully disclosable effect ends the turn before any later call and
  carries `Allow`, `Deny`, and `Always Allow` as the terminal question on every surface, worded as
  the model's summary beside labeled facts read off the effect, never as JSON. A credential-bearing
  or incompletely disclosable effect instead fails closed with a factual refusal and records no
  pending decision. The next authenticated answer settles an exact
  pending request; `Always Allow` persists the exact agent/call/effect permission, `Deny` refuses
  this occurrence, and explicit revocation removes the standing grant.
  Only an admitted decision resolves a member-private object target or binds the selected member's
  readable subjects, credentials, grants, and handler. The sandbox stays the conversation's shared
  sandbox; only the call's exact wire and credential capabilities change.
  A call with no active selected member is conversation-common work. A handler refusing for want of
  a member (`SpeakerRequired`) can be retried with a `message_ref` already present in the turn.
  Reads combine the conversation's subjects with that member's own subject — never the shared atom
  their private audience also reads, so a sealed conversation stays sealed however it is driven.
  A write takes the conversation's audience whoever is bound: workspace conversation memory is
  shared, while a private room or foreign channel stays keyed to itself. A member wanting a private
  note makes it in their own conversation.
- **Skill loading** — skills are folders of files (SKILL.md + assets), loaded into the sandbox on
  `load_skill`. **A skill ships with the thing it teaches**: core ships two folder skills —
  `sandbox`, teaching core's own builtins, and `ufo-style`, carrying the portal theme's own tokens
  as the house style an extension's design skills default to — core is the only tier every pack can name in `depends` —
  and generates a `model-catalog` skill from
  the model registry at serve so the models a member can pin stay documented from the same records
  the runtime routes and bills on; an extension's skills ride its manifest; a pack may
  add pack-level skills of its own (see Packs). Serve packs the static deploy tier into one
  content-addressed archive. A terminal verifies and extracts that archive directly into
  `$UFO_HOME/skills` at startup, then removes every prior bundle and staging artifact; sandbox
  images set `UFO_HOME=/home/user/.ufo` and bake the same static tree at the same path.
  `load_skill` resolves a deploy closure from that local tree in one operation, verifying every
  file's digest without copying it. A member-authored or generated skill is materialized only when
  named and installed into the same `$UFO_HOME/skills/<name>` tree by that load.
  Member-authored skills persist for the workspace — one name is one skill, managed on the
  portal's workspace page — and join the registry of every agent whose `use_workspace_skills`
  setting holds, as its **member tier**; a skill's frontmatter `metadata.agents` narrows it to the
  named agents' turns. Each row carries the object `generation` the save fence reads: `object_get`
  returns it, an apply that echoes it is refused once another writer has saved, so two writers
  editing one skill cannot silently overwrite each other — the later save refuses and re-reads. A small tier (card lines within a 4,000-char fold) lists
  in `<available_skills>` beside the deploy tier; past the fold the index carries the deploy tier
  alone (core, pack, generated), byte-identical across workspaces, so at the sizes where
  invalidation matters a skill save never invalidates a cached prompt prefix. Past the fold,
  member skills reach the model as a bounded block in the turn message, on exactly the turns
  memory recall injects on — pinned skills first, every card while they fit, then lexical top-k
  as full lines with the rest as bare names and a count — rendered from **routing cards** (name,
  description, depends, pinned; columns written at save), so no turn decodes a skill's stored files
  except the one `load_skill` names. The skill kind's `skill_search` action searches every card
  and returns rows, never bodies; a vector retrieval leg runs shadow-only until measurement
  promotes or deletes it.
  Every-turn content belongs in the system prompt, situational/long content in skills; skills carry
  workflows, never restated tool docs (the tool's description is authoritative). `load_skill` loads
  each skill's files and injects its `SKILL.md` without the frontmatter, under a header that says
  whether the agent asked for it or a dependency pulled it, closing with one tree of everything
  loaded — the named skill first, then each skill it `depends` on. `depends` is
  the only pull: a nested child reaches its parent by declaring it, and nesting alone pulls nothing.
  A load costs the workflows it pulled and the paths to their files, never a restated catalog entry
  or a root prefix repeated once per bundled file. **A workflow enters the context once**: the turn
  tracks which skills the window holds by expanding each `load_skill` call's own requested name
  through the registry closure — never by reading a skill's prose, which is member-authored and may
  quote the header format — so it holds across turns, across a rollover, and across replay, and a
  repeat load revalidates the files and names the skill in one line. A result the engine offloaded or
  truncated cut the workflow off, so that one loads again.
- **Typed spawn** — one verb over two target kinds. A subagent profile (registry of name, prompt,
  tool subset, input/output schema; extensions register them) runs as a child turn under the
  spawning agent, in its sandbox: foreground awaits its validated output; background returns its
  identity at once and may deliver that same validated output to the parent's conversation when
  it finishes. A standard one-string `result` contract validates a prose closing of at most 400
  characters as that field without a second model round; a longer closing and other contracts use
  their typed `finish` call. The
  result is an arrival folded into the live turn or admitted as the next turn, so no parent
  holds a turn open waiting on a child; the caller records whether anyone will await. A member
  message waiting on the parent's conversation ends a foreground wait: the child moves to the
  background — not cancelled, stamped to deliver its own result — and the tool answers with its
  identity, so the parent answers the member while the work runs on, exactly as a bash command
  still running at its foreground budget continues detached. The child's own terminal is read
  first and the move is refused past it, so a message landing as the child finishes resolves to
  the child's result. A workspace
  agent target is a fully async peer: it runs as itself — its prompt, model, whole tool set, own
  sandbox, memory, and grants, under the declared (or default task/result) contract its row
  carries — spawnable by its owner or a workspace admin, an ownerless row (the main agent, a
  provisioned agent) being the admins', so no prompt the selected member did not write or vet runs
  from their request — the
  spawn returns its identity at once whatever the caller asked, and its
  contract-validated answer (walled as data — the child's tools read the open web), or the
  structured question it ended asking, arrives on the spawning
  conversation; `message_spawn` is the reply channel — a child whose turn is in flight reads the
  message at its next round boundary, an idle child as its next turn, and a follow-up's answer
  always arrives as a delivery — and neither lifecycle nor cancellation ties it to its spawner.
  Spawn control reads a finished child of that conversation as its exact identity, terminal, and
  validated output. A forced close records `incomplete_reason=round_budget`
  on the terminal beside its schema-shaped answer. A woken turn reads the durable record, never
  the ending turn's working memory. Two payload knobs any profile may declare: `preload_skills`
  loads the named skills with their `depends` closure and injects their instructions before the
  child's first round; `extended_context` lifts its round budget to the main ceiling. A third,
  `model`, runs the child on a named registry model instead of its target's own, under the
  precedence the client pin section states.
- **Context boundary** — full-conversation `messages.json.lz4` transcript with monotonic seq, and
  one extension point over it: what a turn does when its window crosses its line. Core owns the
  seam (`runtime/context_boundary.py`) and registers no strategy. Every strategy ships as an
  extension declaring a `ContextBoundarySpec(strategy, build, tools, prompt)` at the Manifest
  `context_boundaries` point, and the config toml names two of them: `[context] strategy`, the one a
  workspace crosses with the `enable-context-rollover` flag off, and `[context] flagged_strategy`
  (default `rollover`), the one it crosses with that flag on. The flag is read once per turn and
  reads closed, so a deploy with no flag service, an unseeded key, or an outage crosses
  `strategy` — which is how one build runs compaction in production and rollover in testing, off a
  flag flip rather than a per-environment toml. Exactly one is active over one window: a turn never
  runs both, both names resolve through the merged manifests at boot (before the readiness
  contracts) and selection fails loud — `NotRegisteredError` on a name nothing registers,
  `RuntimeError` on a name two providers claim — and never falls back to the default. The spec also
  owns its tools and its `{{context_window}}` prompt block, so a turn offers only the selected
  strategy's tools and its prompt describes only that behaviour — the block rides the turn's
  assembly request, not the deploy's.
  Whatever the strategy, the replacement is written by one memoized DBOS step reading the recorded
  window and the last persisted record — never process memory — so a crash-recovery replay decides
  the boundary exactly as the first run did. Two extensions ship in the wheel, so the default
  (`compact`) resolves on a stock deploy with no core import of either.
- **`context_rollover`** — registers `rollover`: reset the window at the line and keep everything
  that left it. `rollovers/<cid>/<n>/{before,after,recovery}` records in the blob store, and an
  append-only history
  file in the conversation's sandbox at `$UFO_HOME/runs/<sandbox>/history-<conversation>.jsonl`, one JSON line per
  message, beside the offloaded tool outputs. Nothing at the boundary is model-authored. When the
  window crosses its line — the model window less the recovery reserve and the buffer, a spec's
  pinned `rollover_trigger_tokens`, or the lowered line `repeated_tool_rollover` declares — when a
  provider overflow forces it, or when the last round landed a `new_context` call,
  `ContextRollover._roll_over` (a DBOS step) appends the outgoing window to the history file, renders
  a `RecoveryRecord` and installs it as the fresh window's one message: the member's recent enveloped
  messages verbatim, the tool results the model never read with the history lines that hold them, the
  checklist and the last handoff read off the record before (the handoff labelled possibly stale), the
  active member requests, the drained skill-load tracker, the history file's path and the lines that
  hold the window. `search_history` searches that file in place — one awk pass in the sandbox
  returns a newest-first page of hits, each with its line number — and the agent reads a line with
  its own shell. Every input is read off durable state — the recorded window and the last
  persisted record, never process memory — and a boundary is identified by a digest of the window it
  closed, so a crash-recovery replay finds its own record and cuts the file back before appending
  again. The history lives and dies with the sandbox, like the workspace beside it; a record says so
  when the file no longer reaches back. `get_context_remaining`, `new_context` and `search_history`
  are the agent's three tools over the window, and the strategy's `{{context_window}}` block in both
  shell prompts tells it once how
  the window behaves. Each boundary persists a `RolloverVerification` — tokens before and after, and
  any harvested anchor absent from both the record and the appended window — and emits
  `rollover_total`.
- **`context_compact`** — registers `compact`: spend one model call over the head
  instead of resetting the window. The head is compressed into a typed `CompactionSummary` and
  installed in front of a verbatim tail, through a deterministic pipeline — group into API rounds,
  render the head (images become markers, verbatim-repeated runs fold to one copy plus a count
  marker), summarize, harvest the durable references the head offloaded, reconstruct, verify the
  reconstruction against the head it replaces, persist. `conversations/<cid>/compactions/<n>/{before,after,summary}.json.lz4`
  keeps the pre-compaction window verbatim, the window that replaces it, and the summary carrying
  its `CompactionVerification`. The boundary keeps no history file and offers no reset tool, so
  `get_context_remaining` is the agent's one tool over the window. The last round summarizes only a
  window that cannot fit even with the summary's own reserve given back.

- **Memory** — an extension, not core: it owns the `memory_item` table, the `memory_search`/
  `memory_update` tools, the read-only `memory` object kind (each search hit carries its
  `memory/<id>` or `page/<id>` ref and date; passing that ref unchanged to `object_get` opens it — `created_from` links a
  derived item back to its synced page, `superseded_by` one that a newer row stands in place of
  (a dedup copy, a summary, or a stated correction), `overtaken_by` a row
  still true of its time to the row that says what is true now, `retired_at` one the nightly
  curation pass judged off the page; search excludes superseded and retired rows and serves an
  overtaken one with the live head of its pointer chain quoted under it), the nightly passes that
  write the wiki, the sweep that stamps every older row naming a thing a correction's
  `deprecates` declares out of date, and recall
  (lexical + vector RRF fusion), auto-injected each turn through a `user_prompt_submit` hook. Member and room audiences read their own subject
  plus shared; shared reads shared; foreign reads only its sealed subject. Automatic recall uses
  the conversation audience. Explicit memory tools and opened result objects combine the
  conversation's readable subjects with the bound requester's own member subject, so a foreign
  conversation reads only its sealed subject and the requester's, never shared. It also
  provides the typed `memory_search` seam: core routes that exact subject set to the extension's
  one search workflow. Scheduled tasks require this seam and search
  after admission and before the run's first model round. Recall carries the
  gbrain richness: per-kind recency decay against source information time, falling back to commit
  time (fact/preference/decision/event/task half-lives, on fact items and on summaries dated by the
  facts they stand for), a type-diversity cap so no
  class dominates, supersession suppression, and an episodic→topic pointer excluded from
  auto-injection. It rides two core selection seams: the
  **index backend** behind one lexical/vector interface and the **embed backend** behind
  one batched-embed interface. The dialect-native index (SQLite FTS5 + local cosine, Postgres
  tsvector + pgvector) ships as the base-pinned `index_default` extension and OpenAI embedding as
  the base-pinned `embed_openai` extension. Source
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
  that cannot arrive. One fact from one page is one row, keyed by the page and the body's digest,
  so a page can never retract another page's fact: retiring a page deletes its rows, index scope and
  all, and every read that serves a member deduplicates identical statements by subject and body,
  serving the newest. A source-derived candidate is kept only for a
  reader granted one of its sources — filtered by grant over an over-fetched candidate pool rather
  than an index partition, so the grant fence never starves a small recall limit — then rechecked
  against the live page, audience, and grant state before return. The main agent's owner exception
  applies to explicit work for that member; automatic room/shared recall carries no acting member
  and cannot inject their private source.
- **Minimal built-in tools** — `bash`, `read`, `write`, `edit`, `glob`, `grep`, `share_file`,
  `spawn`, `cancel_spawn`, `message_spawn`, `ask_user`, `load_skill`, `connect_account`, and the
  six object verbs (`object_list`/`get`/`explain`/`apply`/`delete`/`action`) over
  extension-registered kinds — one generic CRUD-plus-actions surface instead
  of per-extension config tools. Object lists accept
  exact first-class-field filters and field ordering over the fields each kind declares — its own
  vocabulary of scalars its rows carry, not its spec's shape, so a read-only kind indexes a column
  without widening the spec a form renders and its apply refuses; a row carrying an undeclared
  field is refused, and each kind's listing proof is what keeps its declaration and its rows in
  step. Every instance row carries its canonical `ref` string beside the `name`; the ref passes
  unchanged to `object_get`. A nonempty get ref is exactly `<kind>/<name>`. The sole contextual
  sentinel is an empty ref: it reads this turn's agent and the result carries the concrete
  `agent/<name>` ref. Gets return that ref and the kind's readable spec beside
  its live status, the owning row's timestamps (recency is the first arbitration signal when
  retrieved facts conflict), and its typed links — `created_from`, `synced_by`, `created_in`,
  `reports_to`, `superseded_by`, `overtaken_by`, `access_to` (the connection or credential slot a grant or binding
  authenticates through, and only where the linking row is no wider than that target), `scoped_to`
  (the row's one owning agent, taken only where no narrower relation already names it), a closed
  core vocabulary; each link's `target` is the exact canonical ref string the same verb accepts.
  Cross-agent scope stays in the link's separate `agent` field. Links are stored on the owning row and rendered forward-only: a forward link
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
  list/get/delete, `monitor` list/get/delete, `scheduled_task` list/get/update/delete,
  `source_trigger` list/get/delete;
  workspace-scoped kinds declare none.
  Apply resolves create or update from the current object and requires that exact declaration.
  Omission means the executing agent. Crossing that boundary requires the configured main agent,
  a non-subagent turn, and an exact live requesting message. The target is task-local to the object
  dispatch, keeps the requester's per-call member binding, conversation audience, and shared
  sandbox, and appears as a separate `agent` field in results and refs — object names never gain a
  second encoded form. A
  private target resolves for its owner and workspace admins; a `workspace` target resolves for
  every member, and a refused name reads as absent.
  Everything else arrives via extensions.
  An operation CRUD does not express is an **object action**: the same `ToolDef`, its
  `bound` naming a kind's collection or one visible instance, registered through the `tools`
  manifest point by whichever extension owns the behavior — the kind's owner or another. An action
  never enters the wire registry; the kind's `object_list`/`object_get`/`object_explain` return the
  actions the turn holds beside the rows, and `object_action` dispatches one under its canonical id
  `action:<kind>:<name>` — the one name allowlists, hook selectors, idempotency keys, activity, and
  telemetry address. Dispatch resolves the bound declaration exactly as a global call resolves its
  `ToolDef` — validation, requester binding, hooks, idempotency, trust wall, and metrics are the
  same code — after the arity, grant, and instance-target checks the binding adds; a bound
  handler reads its target from `ctx.target` and never from its input. Every registered tool rides
  every model round: the static member-facing registry is the retained global set, and the
  progressive-discovery mechanism is the object substrate itself, never a second catalog or
  deferred-schema layer. A declared `presentation` (label, confirmation) is the action's portal
  control and admits it through the prepared-intent lane; the panel is derived from the input
  schema, never hand-written per action.
  Two tools where one would do is a defect. `share_file` ports the shipped design: byte custody in
  the blob store, a TTL-bound token URL served by core's artifact route — no token, no bytes.

## Sandboxing

Every turn executes tools in a sandbox: Docker container from a pinned image (baked toolchain),
default-deny network egress with exactly one route out — the sandbox proxy. A sandbox belongs to a
conversation, and a subagent turn executes in the sandbox of the turn that spawned it — one
filesystem for a whole spawn tree, so a file a child leaves in `/workspace` is the handoff back to
its parent, and co-residency is the cost: session state at fixed paths, one serving port, one
`/proc` carrying the run token.
The sandbox is late-bound: the first operation that needs one creates it — a command, a file op, a
skill load, a dial — and the turn's start creates nothing, so a turn that answers out of its
context, calls a host-side tool, or only spawns a subagent leaves no container and no handle on the
row. One create serves the whole turn, whichever of its operations races there first.
Every model-generated shell command enters through `ufo run`, whichever carrier executes it. The
verb owns its process group, task journal, and proxy lifetime. On an off-cluster carrier it gives
the child a plaintext loopback proxy and carries that socket to the public proxy over TLS; the
proxy token is never sent on plaintext transport off the box. Each tool command gets a
deployment-signed token naming its turn and the member it acts for. Descendants retain the
launching command's environment. Every CONNECT also requires the named turn to remain running or,
for a probe token — the off-turn exec a jobs-role handler runs, which names a conversation, the
member it acts for and its internet restriction, and resolves no deployment model key — its own
deadline to be unspent.
This is process attribution, not isolation between cooperating processes sharing a sandbox UID. A
process-wide connection ceiling bounds proxy state; a per-workspace share keeps one workspace from
consuming it. Meter records cross a bounded, backpressured queue and write aggregated per run in
workspace-scoped transactions, so one failed run cannot roll back another.

The sandbox exports `UFO_TOOL_BRIDGE_URL` for `ufo tool`: a JSON stdin/stdout interface to the six
object verbs and the connector broker's list/describe/search/call verbs. Its synthetic HTTPS host
terminates only at the egress proxy; the proxy forwards the signed run capability to `serve`, never
into the request body. A schema read and a call both require the parent turn to remain live and the
verb to belong to that agent or subagent's tool set. A call runs as an intent child on the express
queue while sharing the parent's sandbox, so a `bash` dispatch can wait without deadlocking its
own conversation; the ordinary guarded tool step supplies validation, hooks, idempotency, result
walls, metering, and a terminal audit record.

A conversation born in a connected CLI terminal binds instead to the `client` carrier: its
`/workspace` is the member's own `$PWD`, and its ops run as the member's own subprocesses on the
member's machine. This is not isolation and does not claim to be — the agent acts as the member,
with model-authored Bash commands containing a literal forced `rm` refused before spawn, so it is
offered only to a terminal the member themselves connected, never a deploy default. The ops travel
down the surface's held stream and their results return as the client's next request (the
rendezvous, §Surfaces); egress metering is cooperative there, since a command that
ignores the proxy env reaches the member's own network, but the model sentinel is never exported,
so no key leaks. The client gives each operation a merged CA bundle; `ufo run` gives its child a
plaintext loopback proxy and forwards to the deploy's public proxy over verified TLS for the
command or supervised task's lifetime. An operation that invokes `gh` uses the client's embedded
build whose verifier reads that bundle without changing the member's certificate store. A
client-bound conversation with no connected terminal is unreachable — its turns and its file
browser fail loud rather than running somewhere the member cannot see. The op logic runs natively
in the client binary, held to the server's op contracts by the client's own tests. A deploy that
serves the client sets `UFO_CLIENT_VERSION` and `UFO_CLIENT_BINARY_URL` together (one alone fails
boot); a client whose `x-ufo-script` names another version is told to `install` from
`{UFO_CLIENT_BINARY_URL}/{target}`, and its sends answer 409 until it has. The client reaches
`WORKSPACE_URL` or `~/.ufo/workspace`; with neither it signs in through a gateway — `UFO_URL`, then
the one `~/.ufo` stored, then the build's `UFO_GATEWAY_URL_DEFAULT`, which this repository's build
leaves unset — and with no gateway either it exits naming `~/.ufo/workspace`.
`ufo --remote` sends no terminal binding, so the conversation opens on the deploy's configured
carrier; `--json` changes only the client event framing and composes with either carrier choice.
A client's `--model` travels with each admitted turn: that concrete model replaces every agent and
subagent profile model in the turn tree, and `--no-internet` beside it may only narrow the deployed
agent policy. The server validates both at admission and stores the selection on each turn; it
never changes the deployed configuration. A `spawn` call names its own `model` for the child it
admits — validated against the deploy's registry at the call, refused there when the id is unknown
— and that pin holds for the child turn and the work spawned under it. A matching connected account
serves it; without one the workspace or deploy key serves that same model. It never displaces a pin
the tree already carries: under an admitted `--model` (or `x-ufo-model`) selection the spawn's
argument is refused as a recoverable tool error, so the model the member selected stays the model
every agent and profile in the tree runs on. An environment document's per-target `model` outranks
both.
A packaged deploy's terminal end attests its revision, immutable image, config and sandbox digests,
and the terminal frame's selected model and reasoning.

**The sandbox proxy is core, not an extension** — it is the enforcement point for three core
invariants: **sentinel swap** (processes inside see placeholder credentials; the proxy swaps real
values onto the wire, so raw secrets never enter the sandbox), **grant scoping** (authenticated
calls use only accounts the agent's grants cover — principle 4 enforced at the wire), and **wire
metering** (every model/API call made from inside the sandbox lands in the ledger). An extension may
declare `sandbox_internet`; its deploy's live turns may then reach globally routable public IPv4
through a metered opaque tunnel. DNS is pinned and every IPv6, loopback, private, link-local,
reserved, multicast, or shared-space answer is refused. Tokenless and ended turns cannot use public
internet. An agent's owner or an admin may narrow that deploy capability per agent through the agent object's
`internet_access_allowed`; the proxy snapshots it into that turn's cached rules.
An admitted turn may narrow it further through its runtime config; no client config can enable
internet for an agent whose deployed policy blocks it.
Extensions never register raw network rules; the proxy's rewrite rules are *derived* from their
manifests — sandbox internet, a credential slot, a connector, or a model provider implies its
scoping, injection, and metering rules. Declare, don't open. The enterprise k8s layer later ships
its apiserver-rewrite / token-mint module through this same rewriter seam.

`/workspace` is the carrier's own storage and the only copy of a conversation's member files: a host
directory an in-cluster carrier bind-mounts (local, Docker), the sandbox's own disk off-cluster
(E2B, whose provider suspends an idle sandbox and keeps it indefinitely), the member's own `$PWD`
for the `client` carrier. ufo-owned task journals, offloaded results, REPL state, monitor output,
source change logs, clipboard images, and staging files live under
`$UFO_HOME/runs/<id>/{tasks,tool-output,repl,monitors,sources,images,staging}`. The id is the
conversation UUID in a managed sandbox and the first 32 hex characters of the terminal channel's
SHA-256 digest in the client carrier, so a terminal reconnect and its server-side handle name the
same directory while concurrent conversations never mix their files. File reads, globs, and greps
admit the current run; member-directed writes and edits remain confined to `/workspace`.
Transcripts, rollover records, and artifacts live in the blob store, which the
sandbox holds no credential for — sharing a file is the sandbox PUTting it to a single-key
presigned URL serve mints, bound to the size and sha256 an in-container preflight measured, so S3
itself refuses any other body. Everything that touches workspace files goes through the carrier —
a turn's tools, a surface landing an inbound attachment, a job appending a change log, the
operator's file browser — and reclaiming a container is the carrier's own business: the Docker
carrier stops its idle containers and any later touch starts one again (the bind mount and the
container persist), and nothing may reclaim a container whose disk is the workspace. Carrier interface: `create / attach / exec / write / read / file_op / dial` — a local
carrier is core's default and the `client` carrier (the connected terminal) is core's too; Docker
and E2B implement it as extensions on the `carriers` point. A conversation's durable
handle is `<backend>:<id>`, and the scheme routes: `[sandbox] backend` names where new sandboxes
open, `[sandbox] resume_backends` keeps prior backends live for the handles bearing their scheme —
a deploy moves providers without stranding the workspaces the old one still holds.

## Extension system

An extension is a Python package exposing one entry point (`ufo.extension`) that returns a
`Manifest`. Extensions import only the public SDK (`ufo.sdk`); a CI gate forbids reaching
into core internals.

The active manifest set reads back as the core-registered `extension` kind: one object per
extension, named lowercase and hyphenated, whose spec names what a member can encounter of it —
tools, object kinds, credential slots, surfaces, jobs, hook events, source backends, subagents, agents,
named and never valued — and whose status carries what it asks of the deploy (`sandbox_internet`,
`requires`). Instances are declarations rather than rows, so their envelope timestamps are null,
and every mutation refuses: installing and removing an extension is a lockfile act (`ufoctl ext`).

The registered chat surfaces read back as the core-registered `surface` kind: one object per
`SurfaceSpec` the active manifests declare, named by the surface name, installed or not — the
terminal and subagent transports declare none and get no row. Its spec is the declaration (the
declaring extension, addressed vs installation-routed, durable vs live delivery, the home surface);
its status is the workspace's `surface_installation` binding — bound or not, the bound agent, whether
the installation identity routes ingress — never an installation id or a provider secret. Foreign
audiences read no rows and the portal answers admins alone; apply and delete refuse, because setup is
the surface's own connect flow. No extension can express it: none sees the whole manifest set or owns
the shared routing table, so the loader builds it and binds it with no extension context. A surface
attaches its setup tools to it as instance actions (`action:surface:<tool>`), so connecting a
surface starts from a read of its object.

Manifest registers (each optional):

| Point | Contract |
|---|---|
| `tools` | Typed tool defs + handlers; appear in agents' granted tool sets. A def naming a `flag` is offered by it: off or unanswered for the turn's workspace, the tool is absent from the catalog and its action from the grants, never refused. A def whose `bound` names a registered kind's collection or instance is an object action: it never enters the wire registry, is discovered on that kind's list/get/explain, dispatches through `object_action`, and is granted, hooked, keyed, and metered under `action:<kind>:<name>`; an allowlist names canonical ids and never `object_action` itself (boot refuses it), and the dispatcher's schema rides exactly when the turn holds at least one action. A `profile_only` tool is withheld from the member-facing set, so it reaches only a subagent profile that names it or an extension-shipped agent whose declared allowlist names it (the raw browser surface reaches every other agent solely through `browser_task`/`wide_browse`). An allowlist is declared, never typed: `object_apply agent` writes no tool list and the main agent carries none, so a held-back primitive is reachable exactly where a manifest says so and no member can grant one. A prepared intent takes no model round and so runs past the allowlist — the panel's verb dispatches verbatim under the submitting member's authority, which is what keeps a shipped agent's own portal panel able to grant it an account or a key. |
| `objects` | Workspace-object kinds: name, one-line description, model-facing guidance, spec model (`extra="forbid"`, JSON-round-trippable, no secret fields — boot-gated), and store handlers over the extension's own tables. Core's six `object_*` verbs validate the YAML envelope and spec, then dispatch to the kind under its own ExtensionContext (`object_action` to the bound declaration under its contributor's); domain refusals live in the handlers. A kind whose rows belong to a member (`connection`, `connector_grant`, `scheduled_task`, `site`) is built on the `MemberOwnedObjects` base: it declares each row's owner (`member_id`) and the audience of the conversation it belongs to, read live, and the base enforces per-member access. A row is visible to its audience, its owner, or an admin (`readable`). Its member-owner controls expansion; an admin may inspect, restrict, or delete but cannot widen another member's access. |
| `agents` | Durable workspace agents the extension ships: a declared name, an `AgentSpec` (prompt, model, reasoning, internet policy, sandbox tier, portal visibility), and an optional tool allowlist. Activation — workspace onboarding, or the first turn of a workspace that predates the declaration — creates the ordinary `agent` row and stops owning it. The row is written once and never again, with one exception: the two fields that are the extension's own statement rather than the member's — the `setup` it declares, and the `purpose` where the row has none — are carried forward on every pass, or a release that gives an app a feature needing a second account would state that need to new workspaces only. Such a write moves the recorded version with it, so the version always names the declaration the row carries. Everything else a later version of the extension changes reaches new workspaces only, and a member's own edit stands — including a purpose they rewrote in their own words. Identity is `(extension, declared name)`, never the row's own name, so a name already in use — by a member's agent or by a second extension declaring the same one — sends the shipped agent to a free variant rather than overwriting anything or stopping the deploy. A provision may declare itself the workspace's **main agent**'s instead of an agent's of its own: it lands on that row, which keeps its name, prompt, mark and every edge hanging off it, so the app's page becomes the main agent's page and no second agent stands beside it — the chat app is this. It arrives with no connector grant, credential, source, or memory: installing an extension never hands it the workspace's connected accounts, and a member grants each in chat as they would for any agent. An `AgentSpec` shipped here must carry a `purpose` — the one sentence saying what the app is for, refused where the provision is written, because a member meets a shipped app with nobody to ask. A provision may declare a `setup` slot — the connector providers its agent can work from (a kind of authority, never an instance) and the instructions to obtain them. Connectors are offers, never readiness gates: the app works from the accounts the member grants and does not require accounts the workspace does not hold. Every offer must be settleable by that agent, which is why source feeds, having no attach verb, are absent. It lands on the row and renders as a loadable skill on the agent itself — a task, so the index costs one line and the instructions load on demand — derived from the grants themselves so it erases itself as they land. Every grant binds to the agent whose conversation it is made in, so setup happens there and nowhere else. That is the whole of what an extension may say about authority: a grant is consent over an account a person owns, so it is declared and never created. |
| `subagents` | Typed subagent profiles. A profile may isolate its named tools from deploy-wide defaults and grants; only core constructs the child's effective registry, so an extension cannot enforce that boundary itself. |
| `prompt_sections` | Capability sections a pack contributes to the agent's system prompt, rendered into the shell's `{{sections}}` slot ordered by name — a pack's rules (web search, browsing, office docs) reach the agent without core naming the capability. |
| `skills` | Skill folders (SKILL.md + bundled scripts/assets) contributed to the loadable set; the loader parses each into the registry `load_skill` and the `{{skill_index}}` consult, includes its content-addressed object in the terminal download and sandbox image, and installs it under `$UFO_HOME/skills/<name>/` beside core's own three. A skill script imports nothing from ufo (it runs in the sandbox) — a CI gate holds that boundary. |
| `connectors` | One brokered provider per declaration: its OAuth descriptor (connection flow), member-facing label, and `ConnectorBroker` — catalog, server-side execute, feed-sync credential. `serve` merges every declaration into the one `ConnectorRegistry`; the `connectors` extension's broker-generic dynamic tools (list/describe/search/call) dispatch through the current agent's connector-grant edges, and the sync runner resolves a stream's feed-sync `Credential` through the connection it hangs off, without an agent (a stream of a connection naming no broker account — the workspace's keyed feed, a member's private feed on that key, a repository or folder root under its `feed_handle` — resolves through the `auth_proxies` fallback instead). A broker may also declare an **open namespace** (`connector_resolver`): the connect flow and registry resolve any slug no connector explicitly registered through it, so the connectable set is drawn from the broker's whole live catalog rather than a fixed list. The namespace validates a slug against that catalog when the member connects, and claims only what the deploy can actually serve — a typo, a toolkit the broker holds no managed credentials for, one cataloguing no tools, and one whose tools cannot do the job its service exists for all fail loud at the connect request instead of minting a consent link that dies later. The first three are read off the catalog record; the last is a judgement the broker extension records per slug, because the catalog's scope metadata is too inconsistent to derive it (Composio's `BANNED`, with the evidence in `docs/composio-provider-coverage.md`). It is the catch-all, so at most one deploy-wide (two fail loud). Two broker extensions ship — `composio` (the open namespace: any of its brokered toolkits by slug, and no explicit connector of its own) and `pipedream` (an explicit allowlist of connectors the open namespace does not serve: GitHub, whose token the sandbox itself rides — its consent runs on the deploy's own GitHub OAuth client, `PIPEDREAM_GITHUB_OAUTH_APP_ID`, the one condition under which Pipedream releases a connected account's token; Gmail, whose consent Composio's shared client cannot pass — Google blocks restricted Gmail scopes, so the deploy's own Google OAuth client rides Pipedream Connect, set via `custom_oauth_env`; and providers Composio holds no managed credentials for while Pipedream operates its own OAuth client: Ramp, Brex, Xero, DocuSign, PandaDoc. A provider **no** broker holds managed auth for is not an allowlist entry: it authenticates with a workspace key through an injecting `credentials` slot, below). Pipedream is a fixed allowlist, not an open namespace: an explicit entry owns its slug — the namespace resolves only what no connector registered — and the catch-all stays Composio's alone. **A broker brokers auth by PROXY: every call goes through the broker (its execute API for tools; a proxying transport for feed-sync source HTTP) carrying `(external user, connected account id)` — the broker holds the provider token and injects it server-side. A connection stores the connected-account id, the provider's own subject for that account where the descriptor reads one, and its member owner; a connector-grant edge stores only agent binding, disclosure, and audit. **The subject is what a reconnect settles on.** A broker names a consent, not an account — Pipedream mints a fresh `apn_…` every time a member authorizes, and its create-token endpoint takes no account to re-consent in place — so keyed on the account id alone a reauthorization is a second connection beside the first. Keyed on the subject it replaces the account on the row already holding it, and two real accounts still carry two subjects and stay two connections. The confused-deputy check reads the account's owner metadata, never a token. A brokered connection derives NO egress `InjectionRule` of its own; the sentinel→secret swap (§Sandboxing) serves `credentials` slots and one connector shape: a connector whose consent rode the deploy's own OAuth client may declare a `CliCredential` — for github, `env="GH_TOKEN"`, `header="authorization"`, a `GrantSecret` that reads the account's token from the broker, and `GitWire("github.com", "x-access-token", "!gh auth git-credential")`. The sandbox exports the grant's sentinel as that env var — the acting member's private grant, else a shared live grant; two candidates export nothing, since a static variable names no account (and the cache daemon fetches anonymously for the same reason), which is why the connection is keyed on the subject: a reauthorization must not read as a second account — and sets git's credential helper for the git host, so `gh`, `git clone`, and `git push` all send the sentinel. The egress proxy swaps the account's token in on the API host behind the scheme the CLI sent (`token`, `Bearer`) and, decoding Basic, as the password of `x-access-token:<sentinel>` on the git host; the git cache daemon asks `serve` for the credential with the caller's run token stamped as `x-ufo-proxy-auth`, and `serve` answers the same account selection under the mirror principal `w<workspace>-<account>`, so two members sharing one account share one mirror. Both hosts are admitted and metered, gated on the signed member and turn liveness, and the sandbox never holds the token. One connection therefore covers private clone, push, `gh`, and the API; `connect_account` with `provider: github` is the one handoff.** Files cross the broker seam as references, never bytes: a tool's file outputs come back as presigned URLs on the broker's file store (every Pipedream run rides a File Stash; Composio marks `{name, mimetype, s3url}`), a binary provider response on the proxying and forwarding channels is answered as a redirect to that same store rather than fetched into the process (Composio marks `binary_data`; the sandbox client follows it, and a feed-sync run fails loud on it — a feed page is JSON, never a file), a `workspace_file` input stages to a broker-minted presigned PUT, and the sandbox runs both transfers itself into/out of `workspace/` — a connector-grant edge additionally admits (never injects) its connector's declared `transfer_hosts` (or the open namespace's, for a slug it brokers), so file bytes never cross the serve process. Base64 a provider inlines in its own JSON result (GitHub contents' `encoding: base64`) already has, so the connector tools translate it in place before the result enters context: decoded text inline under a cap, anything binary or larger written to `workspace/` through the sandbox's write seam and replaced by a reference. A sub-object a denormalized list response repeats identically per element (GitHub code search embeds the same `repository` in all 30 hits, 85% of the payload) crosses once the same way: first occurrence in full, later copies a `{same_as: <JSON Pointer>}` pointer into the same result — lossless, keyed on structural identity and never on a field name, so no provider is special-cased. It runs before dispatch measures the result, so a single-repo search that would have been offloaded whole (141K chars) stays inside the inline budget (21K); a cross-repo search collapses nothing and offloads, which is the same rule and not an exception. |
| `sources` | Data-feed backends: `sync(cursor) -> pages` run as jobs; core derives each digest from the body, stores changed bodies at immutable digest-qualified refs, and pages land in memory through the derivation pipeline. A stream declares whether its pages reach memory (`indexed`): one that does not still lands its pages for source triggers and `object_get`, and the memory consumers derive no chunks and no facts from them. The declaration is a fact of the run, not of a page — the sync driver writes it onto every row of the source each run, so a row an older image landed under the column default meets it one interval later — and `indexed` is in the revision trigger, so each row a flip moves replays once; the migration that added the column backfilled before the trigger learned the column, so the initial marking replayed nothing. A page's provider identity is distinct from the source-side reference that first located its row: the identity resolves every later fetch to that row, while attaching or changing identity metadata does not advance the page revision. Disclosure is the connection's, never the stream's: the sync driver stamps every page it commits with the subject `connection.shared` derives, read under the claim after the fetch so a member sharing an account mid-sync has that batch land shared. Flipping the flag restamps every page its streams already synced, in one transaction across them, so a multi-stream account cannot tear. Which members may see it is that subject; which agents may read it is `connector_grant`, checked at read time. A stream's row identity includes the connection it hangs off, so the same stream under a second connection is a second row. Each provider is a backend factory receiving only its declaring extension's scoped credential access; extensions cannot otherwise express an authenticated direct source without exposing encrypted secrets. Core ships only `folder` (local files). Connector source providers live in `extensions/sources`, built on the read-only REST connector framework core exposes through `ufo.sdk.sources`; the adapter stores and returns provider checkpoints without ordering record fields. The sources extension computes record watermarks and emits them through `StreamPage.next_cursor` after enumeration; numeric providers define numeric ordering. Each stream declares provider-record creation/update paths independently from its sync cursor, and each connector resolves a provider `Credential` through the pluggable **auth-proxy** seam, never importing a broker. A connected account feeds itself as it connects: the `sources` extension's `connection_recorded` hook gives the landed connection one row per syncing stream its connector declares, created inside the connect callback, and a per-minute job in the same extension is the retry path alone, creating what a hook that failed did not. **A connector's streams are a tree, not a list: a stream declares the parents it hangs under and its partitions are those parents' landed records, reached at a path whose placeholders are dotted field paths read off the parent record — so a collection a provider publishes only under a parent is one declaration rather than a descent each connector hand-rolls, and a child's page is addressed by the values its edge's path reads off the parent, which is what tells a record apart from the one of the same key under another parent.** A stream reached under more than one parent declares each edge. A partition also carries an opaque **factory checkpoint**, written by the page that produced it and handed back to that same partition next pass, pruned with it when the parent stops being enumerated: a provider that moves a record without moving its cursor value — GitHub publishes a check result against a pull request whose `updatedAt` stands still — has no watermark to find it by, and the connector compares its own index against what it wrote last pass instead. The syncing set is the canonical streams plus the ancestors they fan out from, derived from the edges and declared nowhere, so there is nothing for a member to pick and no per-stream state to hold: a stream whose row exists is left exactly as it is. A parent's page carries the fields its children build their paths from, projected as it lands; a placeholder the parent does not carry raises at fan-out rather than landing zero records quietly. A per-tenant provider registers no rows until the `connection` kind's apply names its `base_url` — including one whose address carries a company file (QuickBooks' `/v3/company/<realmId>`), which no broker holds — and the same per-minute job creates them on the tick after the member names it, so the URL needs no waiting state and no flag. **A first sync reaches back a bounded window, pinned at registration to an absolute instant** so a cursor reset replays it rather than re-windowing onto a fresh one. A stream declares its own reach (30 days for email messages) and the connection's `backfill_days` overrides it for every stream at once; a stream declaring none takes no override. The window is a parameter of a row rather than part of which dataset it is, so it never moves a `source_row_id` — and it only ever widens in place, re-pinned against the original registration instant; narrowing is disconnect-and-reconnect, since only that drops the pages a narrower window would not have fetched. When a same-named surface resolves the external user it speaks as, the runner puts that live identity on `SourceAuth`; the source drops only records authored by that exact user before they become pages. |
| `hooks` | Reactive lifecycle handlers on Claude Code's taxonomy, scoped like a job. Seven fire on the turn loop — `pre_tool_use`/`post_tool_use`/`post_tool_use_failure`, `user_prompt_submit`, `stop`, `pre_compact`/`post_compact` — as a runtime policy filter over the tools grants already admit (observe, deny, modify, or inject), never a second grant path. A turn-lifecycle hook is also where a surface's live side work is armed — the turn's own execution is the only place that happens once per execution — so a hook's context reads the turn's frames through the loop's tailer and its durable end, and starts side-channel work from them, never work that re-enters the turn. The eighth, `page_change`, is the data-plane seam (data → memory): a core batched cursor-runner replays each changed source page in database-assigned workspace revision order from each consumer's own cursor — the path the memory page indexer and fact deriver ride. The ninth, `connection_recorded`, is the control-plane seam: the connect flow publishes the connection it just committed, inside the callback request and with that workspace bound, so an extension creates what a connection implies — a connected account's source rows — with the connection rather than a sweep later. It is observe-only and swallowed on failure, so the connection lands whatever an extension makes of it and that extension's own job retries the rest. (Claude Code's session/permission/subagent-stop/notification events have no producer here and are not members until one lands with a consumer.) |
| `jobs` | Recurring/one-time background work. |
| `routes` | HTTP endpoints under `/ext/<name>/` (webhooks, OAuth callbacks, plugin UIs). |
| `surfaces` | A member-facing HTTP surface on the one privileged surface seam — the seam that asserts a member's identity — with its `SurfaceRoute`s mounted under `/surface/<name>`. A **durable** chat surface (Slack) declares two-phase delivery (`post` then `attach`) the poller drives — declaring `post` is what marks it durable, and admission registers every turn entering its conversations for delivery, whoever admits it; a **live** chat surface (web) tails the hub over SSE in its own route. A **page** surface (sites) admits no turn and delivers nothing: it resolves its workspace from a signed address in the URL, authenticates the viewer from the session cookie, reads one of the workspace's own rows under that row's access rule, then renders its frame or redirects a homepage to ingress. It is not `routes`, which carries no member identity and cannot resolve a workspace before binding one. |
| `credentials` | Named BYOK slots the workspace must fill (drives onboarding); `ufoctl init` seeds a slot from its upper-cased env var (`ACME_API_KEY` → `acme_api_key`), and the operator fills or rotates one anytime with `ufoctl credential set <slot>`. An admin fills one in chat through `request_credentials`: the speaking admin's ask seals which slots they will fill, a capable surface prompts for each value privately, and fulfillment verifies the seal before the encrypted store takes it — the plaintext never enters the transcript or the sandbox. An extension tool may instead seal provider authorization state to its own declared slot and the speaking admin, then fulfill that seal with the resulting credential; URL/code handoffs therefore stay in chat while provider secrets do not. Extensions may read their declared slots and compare-and-swap an existing value when an upstream client refreshes it; only an admin-sealed or operator path creates a value. The portal's workspace credentials view is the same admin-sealed path, prepared rather than typed: its set or replace asks for exactly that `request_credentials` prompt and the value crosses only in the prompt's fulfillment, and its clear rides the `credential` kind's own admin-gated delete. |
| `credentials` → `InjectionTarget` | A slot carrying one turns the workspace's stored secret into wire access without the sandbox ever holding it: the proxy admits the target host, swaps the declared sentinel in that header for the real secret, and meters the host — resolved **per workspace from the run token**, so the one shared proxy injects for every workspace and bakes none of them into its static base. The engine exports the sentinel (never the secret) as the declared `env`, so the agent's own HTTP client authenticates the provider the way the GitHub CLI does with a grant. A provider that pins its API host per account declares a `HostChoice` instead of a hostname: the closed set of hosts it publishes (a Datadog site, an OpsGenie region), the companion non-secret slot a member selects through, the default an unchosen workspace gets, and the env the resolved host is exported as. **The stored value is a choice, never a hostname** — it resolves to one of the declared literals or to nothing — so a scoped host is always a string the declaration wrote, which is what an exact ScopeRule requires: it bypasses the proxy's private-address check (that check guards the open-internet path), and free text there would let a stored address decide where the shared proxy dials. A closed set leaves no pattern, length cap, case fold or suffix bound to get wrong. Every host is resolved through one resolver by all three consumers — proxy rules, the sandbox export, and the `credential` object's own read — so no read reports a host the wire would not use. One sandbox variable carries one value: every exported name (a slot's env, a choice's env, a connector CLI's env) is claimed in a single namespace where both roles collect their slots, and a sentinel is unique across installed extensions. **This is how a provider no broker can front becomes connectable**: brokered OAuth where a broker hosts consent, a keyed slot where only the member holds a key. Two slots on one host each inject their own header, which is a provider taking more than one key (Datadog's API + application key); auth signed over the request (SigV4) and Basic composed from two stored values are not expressible and are out of scope. A slot holds what the member stored and injects it as one header on one host, nothing else; git authentication is not a slot's — smart-HTTP takes only Basic, and the sandbox's git is configured by a connector CLI credential's `GitWire` (`connectors`, above), never by a stored key. |
| `onboarding` | Steps contributed to the workspace/pack onboarding flow. |
| `workspace_founded` | Handlers run inside the transaction that founds a workspace, after its first admin and its main agent at the default model and reasoning are inserted (`ufoctl init` applies its chosen model and reasoning after them), on that connection with a frozen `Founded` payload and no `ExtensionContext`; a raise aborts the founding. First-party distributions only. |
| `deploy_routes` | Endpoints a deploy's own service calls, mounted at `/internal/<name>/<path>` behind a constant-time bearer check against `deploy_bearer_env`, which must be one of `deploy_keys`; `serve` fails boot when it is unset. The handler gets a `DeployContext`: `provision` (the founding seat), `bound(workspace_id)` (transaction, profiles, member model key, store), and cross-workspace reads through the owner pool — `memberships`, `workspaces_by_first_domain`, `first_member_emails`, `workspace_count`, `workspace_ids`, `seated_members`, `owner_transaction` — each taking a required `audit` that logs `deploy.cross_workspace_read` with the route and extension — plus `flag_backend` and `flag_keys` (core's and the pack's), so a flag-service write refuses a deploy it does not serve and a key no code reads. First-party distributions only. |
| `commands` | Operator verbs, `ufoctl <name> <command> --<field> <value>`: each field of the params model is one option, the command runs with a `DeployContext` and prints the line it returns; `CommandRefused` exits 1 with its message. A name that is one of `ufoctl`'s own verbs fails. First-party distributions only. |
| `models` | Model providers behind `ModelClient` (OpenRouter, local runtimes). |
| `carriers` | Sandbox carriers — Docker, E2B, remote runners; core's defaults are a local temp-dir carrier and the `client` carrier (a connected CLI terminal's own directory). |
| `indexes` | Index backends for memory/source retrieval; the dialect-native default (SQLite FTS5 + local cosine, Postgres tsvector + pgvector) ships as the base-pinned `index_default` extension registering name `"default"`, which core resolves when `memory.index_backend` is unset. |
| `embeds` | Embedding backends behind `EmbedClient`, selected by `memory.embed_backend`; OpenAI text-embedding-3-large ships as the base-pinned `embed_openai` extension registering name `"default"`. |
| `hubs` | Stream hubs for multi-instance deploys (Redis). |
| `cdp_providers` | CDP transport backends the one BUA browser engine (an extension, not core) connects, selected by `[browser] cdp_provider` (default `sandbox_chrome`). Core ships none: `sandbox_chrome` drives Chrome inside the sandbox the turn runs in; `browserbase` mints a hosted session per browser run against a Browserbase Context it keeps for that run and deletes with the session. A provider mints a per-turn `CdpLease` the loop releases at turn end, and the lease answers both file questions only the transport can: `place_file` where its Chrome can open a workspace file (the same path when Chrome shares the sandbox, an upload when it is remote) and `download_dir`/`fetch_download` where that Chrome may write a download and how its bytes come back (a sandbox path read back out of the sandbox, or a hosted provider's storage read back over its API) — so a file input works whichever transport is selected, and a hosted browser is never handed a path it refuses. The BUA engine is the browser extension, so only the transport is a core seam, never the engine. |
| `auth_proxies` | The credential backend every stream of a connection naming no broker account resolves through: the workspace's keyed feed under its empty handle, a member's private feed on that key under their member atom, a repository or folder root under its `feed_handle` identity JSON. A stream of a broker connection resolves through that broker's `credential` instead; `brokered_account` reads the handle, so the handle is the routing signal, not the provider name. The sole installed backend is automatic; `[connectors] auth_backend` selects one when several are installed and must name a registered choice. `direct` BYOK reads that key host-side, never reaching the sandbox. |
| `search_providers` | Web-search backends the research extension's tools call, selected by `[research] search_provider`. A backend runs host-side — it reads its BYOK key in-process and reaches its API over async HTTP, so the key never enters the sandbox — and answers a search query; `supports_fetch` marks whether it also fetches a URL's content (Perplexity's exact-URL search does; an answer-with-citations backend need not, and the `fetch_url` tool gates on it). Core ships no default: every backend is an extension, and the research extension `requires` this seam. |
| `flag_providers` | Feature-flag backends behind one OpenFeature client, selected by `[flags] backend`. Core ships none and reads every flag through `ufo.flags.flag_enabled`, so a deploy swaps providers with a config line; the selected backend is built once at boot from `[flags] cache_ttl_seconds`, the window it may answer a flag out of its own response cache. A flag is read as a string, because a string is what a flag service holds, so every backend serves `ufo.flags.SERVED_TRUE` or `SERVED_FALSE`. Every read fails closed to the default its call site passes: an unset knob, a backend the deploy carries no key for, an evaluation that raises, one that errors or answers neither spelling, and one that outlives `ufo.flags.FLAG_TIMEOUT_SECONDS` all resolve to that default and warn, so a flag service that cannot answer withholds the feature rather than failing a turn. The evaluation targets the ambient workspace, which is what turns a feature on for one workspace at a time. Selecting a name no extension registers, or a name two register, fails loud at boot — a deploy that thinks it reads flags and reads none would gate features on nothing. Each call site's default is the state its own feature ships in: a flag withholding something already offered reads open, so only an answered `false` takes it away — a deploy with no flag service, a key nobody has created, and a backend outage all leave a member what they had; a flag holding back something never offered reads closed. The `open` backend (the `flags_open` extension) answers every flag as its declaration's `open` says — on for a withheld feature, off for a flag that selects between shapes the fleet already serves one of — and any undeclared key on, for a dev stack that wants the whole product without a flag service. |
| `flags` | A flag this extension reads, declared by the extension that reads it: the key, one line saying what turning it on offers, and `open`, what the `open` backend serves for it (on, unless the flag selects between two shapes the fleet already serves one of). A key the flag service was never told about evaluates to its call-site default in every workspace. A flip reaches a member without a deploy — from an operator command the backend's extension declares (`commands`), or from the backend's own dashboard. |
| `memory_search` | A named workspace-scoped memory search provider (`default` is selected). Core passes the exact readable subject set; consumers declare `requires=("memory_search",)`, and boot fails unless exactly one default provider is active. |
| `requires` | Sub-seams this extension consumes from another (the browser pack `requires` `cdp_providers`); `serve` resolves each at boot and fails loud — naming the extension and the seam — if the backend is absent, unknown, or unkeyed, so a missing dependency stops startup rather than the first tool call. |
| `metrics` | Metric names the extension emits beside core's: name, kind (`counter`, `histogram`, `up_down`), dimensions. `load_manifests` installs the active set's; an emit of any other name, of the wrong kind, or with an undeclared dimension raises, and a name core or a second extension declares fails the load. A name one extension alone emits lives on its manifest (gated). |
| `census` | First-party only. A funnel stage, an onboarding step, or both, marked by a row the extension owns: `first_at(CensusRefs)` returns a scalar SQL expression over its own tables (`CensusRefs`: the bound workspace, a member's first turn), evaluated in the census's own transaction and counted by core's rules — a stage every tick once set, a step once on the tick it lands in. A name core or another contribution counts fails boot; SQL that fails fails the tick. |
| `operator_rules` | Who operates the deploy's operator surfaces (first-party only): an `OperatorRuleSpec` names a rule and builds it over core's lookup (seated admin, a workspace's first-member domain, the oldest workspace a domain addresses). `[operator] rule` selects exactly one at boot — the built-in `seated_admin` unless named — and an unknown or twice-registered name fails boot. The rule admits a bearer to a grant of reach `own` or `fleet`, resolves `?ws=` under fleet reach, says which workspace is the operator's own (`is_operator_workspace`), and names the sign-in page a credential-less page GET is sent to and an operator API's 401 carries (`sign_in`, null without one). |
| `member_added` | First-party only, and one per deploy: two fail boot. A handler `add_member` runs on its own transaction's connection, under the workspace lock, right after the member row is written, with a frozen `MemberAdded` (workspace, member, email, who added them, `notify`) and no `ExtensionContext`; a raise aborts the add. Its `notify_description`, `description`, and `notice(notify)` are the verb's only words about telling the member: with no listener `add_member` takes no `notify` and replies `<email> is <role>.` |
| `spend_gates` | First-party only. A named `SpendGate` built once at boot from the deploy's public base and browser home: `admit`, `sustain`, `charged` run on the caller's connection with a frozen payload and a raise aborts that write; `admits_sql` filters the owner-scope candidate reads; `absent` is the per-round no-database fast path. See **Spend gates**. |

A source parent catalog with no pages is readable only after its row has completed a sync. Before
that, an incremental child waits and a snapshot child fails without sweeping.

`ExtensionContext` (capability-scoped, handed to every handler): workspace-scoped store access,
seated-member paging, member-private scheduled agent admission, and member-bound read context for a
first-party manifest that declares `member_context_read`; that context spans the member's readable
conversations and artifacts across agents, and the pages disclosed to that member or to the
workspace,
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
directly; it opens a proposal (prompt, skills, tool grants) that applies through Governance
approval. This is what makes a full self-improvement extension expressible —
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
unnarrowed set (the lockfile's pins, or every discovered extension in dev); boot refuses a set in
which two extensions publish one tool — `browser` and its alternative `browser_use` both answer
`browser_task` — naming both and the `[pack]` that picks one. The flagship is
**assistant** — memory (with its index and embed backends), the browser pack (its BUA engine over
the default `sandbox_chrome` transport), brokered connectors (Composio's open namespace plus the
Pipedream allowlist), and web
research (the research tools over the Perplexity search backend).

## Third-party extensions

A third-party extension is JS, declared by a static `ufo.manifest.json` core reads without
executing any code — contribution points plus lazy activation, never a top-level import — and runs
isolated from core in **`runner`**, a standalone service, never part of `ufoctl serve`'s one
process. First-party (bundled) extensions are unaffected: they keep the
in-process Python mechanism above, unchanged. The line is provenance (reviewed-and-pinned vs.
store-installed-or-locally-loaded), not a rewrite of what already works.

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
| Local dev / test mode | `ufoctl ext dev [--remote <url>]` runs the extension as a plain local Node process (WASM only enters at `ext publish`) against a disposable workspace with real grants. The host may assemble a test-only system-prompt replacement, tool-description overrides, and added tools through the harness's immutable definition and tool port; these are not production manifest points. Every credentialed call routes over the channel through the same host/header-checked injection `runner` uses; no live workspace's data is ever reachable. |

## Surfaces

Core owns **one surface seam**, not every surface. A surface is trusted infrastructure — it asserts
a member's identity and admits turns as that member — so its `SurfaceContext` is deliberately
privileged (distinct from the scoped extension context): **admit** an inbound message onto the
durable turn queue through the member-only admission capability, with its ambient `TurnContext` —
the sender, IANA timezone, and source the surface knows,
which the engine renders as the `<context>` tag (stable message ref; the admission moment, local
when a timezone is known; sender; source) before each member inbound, a surface that can name a
source doing so in the form it has — a chat surface the message's own permalink, the portal a
link to the conversation, a terminal the client and the member's address — so anything the agent
creates elsewhere can name where it was asked for; a message arriving while the conversation's
newest turn is still live lands on the conversation's inbound queue, which the engine drains into
that turn at each round boundary as separate `<context>`-tagged messages — the terminal
commit refuses to close over a non-empty queue, so one FIFO aggregate ends in one closing reply and
one writeback across all speakers, and a turn that fails or is cancelled over one hands its
pending arrivals to the next turn at its exit, and a turn answers a speaker before it ends by marking a span of
a round's text with the `message_ref` it replies to: on a surface that implements `speak`
core delivers each marked span to that member as its own message, in the order the model wrote it,
exactly once across a provider retry, a second replica and a replayed turn, and before the closing
reply, which carries what the turn has not already delivered; a live surface shows a span only on
its stream, so there the closing reply carries those words too — **identity** resolution (an
external id → member + conversation,
linking a `surface_identity` on first contact — `join_member` also creates the member when a
channel-verified email matches the workspace's domain signup subject; a personal-mail workspace has
no domain subject, so only an exact member joins it — and `adopt_identity` to span a member across
surfaces), **stop** (`stop_turn` ends a running turn of a conversation the surface authorized —
a portal's stop button, the terminal's Esc — cancelling it durably and publishing its cancelled
terminal so every live tail ends now; descendants are the cancel reconciler's, as for every cancel
path), plus the reads a live view serves: `tail`/`turn_owner`, the admin-shaped
`spend_rollup`, and the per-agent projections — `object_kind` with
`list_member_objects`/`member_object`, `agent_skills`, and
`memory_available`/`search_memory`. An extension registers a `surfaces` Manifest point; core mounts its `SurfaceRoute`s under `/surface/<name>`, each bound to the
one context. A provider that sends events on a persistent stream declares `listen`; core starts and
cancels it with the app because core alone owns process lifetime and the pre-workspace installation
gate. One fenced fleet lease per surface admits one listener across all replicas. An external
provider failure reconnects from its cursor. An internal listener fault parks its cursor and lease
until shutdown without stopping unrelated server work. The listener resolves each exact
installation through `SurfaceListenerContext.workspace`, which checks the lease fence before it
reads or admits workspace data. The seam supports two delivery modes; a surface uses only the subset
it needs:

Jobs and evals receive the separate internal `invoke` capability, which never speaks as a member. It
carries the admission meanings a fire needs — a scheduled stamp, exact runtime capabilities, parking
work the ledger already booked, and refusal when a member has spoken past a named turn sequence —
so an extension that waits or fires on a schedule keeps its own rows and needs no state inside
admission. Both capabilities delegate to the same admission workflow, so spend enforcement, turn
allocation, delivery registration, and enqueue recovery remain one implementation. The invoker also
answers `member_reach`: the durable-surface conversations a member personally spoke in, on their own
audience, newest first — where an invoke reaches a member who is not in the invoking conversation,
because admission registers a writeback for a turn entering one. It is member data, so an extension
reads it only under `member_context_read`.

- **Durable** (a chat surface; the member is elsewhere) — declaring `post` is what marks the surface
  durable, and admission registers a writeback for every turn entering its conversations — a
  surface ingest, a scheduled fire, an extension invoke alike. A `WritebackPoller` delivers the
  terminal reply with its cost, tokens, cache-read percentage, model, and reasoning effort
  at-least-once (the hub is
  lossy), two-phase: `post` returns the reply's durable
  reference (recorded before any upload), then `attach` streams the turn's shared files into the
  conversation, with rich rendering. Recovery resumes `attach` without re-posting; attachment
  delivery is at-least-once and may repeat after a crash between upload and the delivered commit.
  A turn that ended by asking (`ask_user` as its final act) rides the writeback as a structured
  `question`, so the surface can render the questions as its own answer affordance, whose use
  admits the answer as the conversation's next turn — the first submit wins the idempotent admit, and `admitted_body` is how the surface confirms which
  landed before rewriting the affordance. A structured question may name one target member; only
  that member's authenticated submit admits or rewrites it. Free-text and multi-select questions
  keep those answer modes on every surface. A durable surface may also `tail` a turn for live
  feedback while it runs — a thread status, interim progress posts — and live feedback is never
  delivery, which stays the poller's: a `speak` post per marked span while the turn runs, then the
  terminal reply. A comment admitted from another surface writes its `speak` delivery beside the
  inbound in the same transaction, so the conversation's own surface posts the comment notice
  before the agent's reply. `Writeback` and `MidTurnReply` carry `speaker_member_id`, the member
  whose message the turn answers and None for a turn no member spoke (a scheduled run), so a
  surface that threads its posts under that message threads every post of one turn the same way,
  whichever surface admitted the message.
- **Live** (a portal; the terminal surface is its directive-stream twin) — the member's connection is held open, so
  admission registers nothing and the surface delivers by `tail`-ing the turn's frames off the hub
  over SSE in its own route. The poller only processes turns that registered a writeback, so it is a
  no-op for a live surface — the efficient downgrade, not a second seam. A marked span reaches a
  live member as a `Reply` frame on that stream and nowhere durable: a live surface stores no
  messages, so a page loaded mid-turn draws the transcript without the spans already shown — the
  accepted gap — and the closing reply carries those words again. What a step read rides the same
  stream as a `Sources` frame — the web pages and workspace pages a `user_prompt_submit`
  injection or a tool result names — so a live surface draws the places the answer is being drawn
  from while it waits, and nowhere durable either: the reply's citations are the record. What a
  surface folds those frames into is the **turn record** (`ufo.sdk.record`: the steps in arrival
  order, the runs, the meter, the end) — one pydantic shape, rendered as the portal's TypeScript
  and a fixture of every variant by ufo-hosted's `extensions/web/tests/contract.py`, so a
  frontend's model of a turn is the contract's and never its own bookkeeping. The same terminal
  handoffs
  ride the live stream that the writeback carries: a turn that ended by asking renders its options
  as the surface's own answer affordance under the same idempotent admit (first answer wins,
  `admitted_body` confirming which landed), credential prompts collect privately through the
  sealed handoff gated per slot by `credential_prompt_pending`, and the turn's shared files
  deliver as TTL `artifact_link` downloads read off `shared_artifacts`. Each committed share
  publishes an `ArtifactsChanged` frame, so the live surface reads and draws the file before the
  terminal frame; the terminal read remains the durable recovery path. A shared file carries a
  `role`. A `file` came through `share_file`: a surface that can attach attaches it, and it is an
  `artifact` object. A `details` file is the write-up the closing reply carried: a Markdown file
  the turn wrote to the workspace and linked from its closing message — the engine stages its bytes
  by the measure-and-store route `share_file` takes, lands the `details` row in the transaction that
  commits the terminal, and delivers the reply with the link reduced to its label — so every surface
  offers it beside the reply under that label, or Open detailed report when the label is unusable,
  under the answer and above the attachments,
  none attaches it, and no artifacts listing names it. A surface that cannot open the report itself
  links `report_url`, the portal address of the conversation carrying the file's id
  (`#/c/<conversation>?report=<artifact id>`), read under the presser's own session, falling back
  to the TTL download where the deploy has no portal or the conversation is a room the portal shows
  nobody; the terminal prints its link line. The
  model's window keeps the answer with its Markdown link, so a later ask for the file has its path;
  every surface reads the transcript through `read_transcript`, which reduces the link to its label.
  A child turn's answer keeps its Markdown link for the parent that reads it. The held stream is also
  where a `client`-carrier turn reaches the member's machine: the same connection that tails the
  turn's frames also carries each sandbox op down as one directive and takes its result back as the
  client's next request — the rendezvous the carrier awaits (§Sandboxing), gated so only the member
  the binding names may answer an op or read the bytes it stages. Each end the client reconnects
  from names the cursor it reached, and the reconnect carries it back, so the tail resumes after the
  last frame rendered: an op ends the stream, and a terminal that prints as it reads must not
  reprint the notes it already showed.

A member-filled credential slot may declare a pure merge for one private structured update. Core
locks the workspace, opens the encrypted row, applies that merge, and stores the whole value; the
surface never reads the existing secrets.

| Surface | Home | Delivery | Identity | Conversation key |
|---|---|---|---|---|
| Terminal | `extensions/ufo` | live (held directive stream) | member token | session (private); the client also lists every conversation the member reads across surfaces and agents and joins one by id on the same stream — another surface's thread with the comment notice, a conversation the member may speak in (their own or the workspace-shared one) plainly, any other read-only, and none claiming the terminal as its sandbox |
| Sites | `extensions/sites` | page (the site frame; a site with no card of its own unfurls as `[sites] share_card_url`, else a generic card the extension serves) | session cookie → member where `[serve] sign_in_path` names a browser sign-in, which a signed-out viewer is linked to unless `[sites] sign_in_url` names another; with it unset no browser signs in, so only a public site opens | — (admits nothing) |
| Debug | `extensions/debugger` | live (hub tail) | operator session: a bearer the `[operator] rule` grants; `?ws=` re-scopes under fleet reach alone | — (read-only; admits nothing) |
| Memory explorer | `extensions/memory` | live (page + JSON read) | operator session, as the debugger | — (read-only; admits nothing) |

A chat surface and a member portal are extensions a deploy adds on this seam; `sample` drives both
delivery modes through it.

A member portal is its own audience authority. Every member reaches every agent whose `visibility`
is `workspace` — an agent-kind spec field, `private` by default; main is born `workspace` and
refuses to narrow, since every surface routes an unbound member to it — and a portal may admit a
private agent's further audience by grants it keeps in its own store: core's member reads take the
agent ids that authority chose and never widen them. A member-private extension conversation
(`member_extension_agent_ids`) admits that member's replies but grants no other conversation or
agent read. A member speaks in any conversation whose audience is the workspace or themselves,
whatever surface holds it, through the same chat transport; another surface's thread also receives
the comment notice, which the admission publishes to a live terminal and records for a durable
surface's delivery; an admin disclosure of another member's private conversation never admits a
message. A workspace admin reaches and administers every agent. A subagent profile is deploy shape,
not an agent — no identity, no audience, no page of its own: a run's work is read inline under the
reply that spawned it.

The deploy has one browser sign-in. `GET /` redirects to the surface claiming `SurfaceSpec.home`,
which decides what an arriving browser sees — its own page for a resolved session, the sign-in for
none — and `home_url` is every other surface's link into it. A bearer enters the portal as a
session cookie through one POST, never a URL: a gateway's sign-in makes it, and `ufoctl portal`
makes the same POST with this machine's CLI token.

An agent's portal pane is its homepage — a hosted-site binding admitted through the sites surface's
per-visit agent-visibility gate, or a deploy-wide app page whose public immutable code is routed to
a workspace-specific origin — inside the portal's sandboxed iframe. A deploy-wide app page is a page
source an extension ships, which the portal's build compiles into one static tree of shared hashed
chunks, published under the digest of its own bytes. The versioned document and hashed assets are
public immutable responses at the frame's workspace-specific origin; every data read and mutation
still crosses the portal's authenticated bridge. Such a page needs no workspace row, no sandbox, and
no seed turn, so a workspace that has not edited its copy tracks the deploy. A page's whole
dependency surface is one module — the portal's own components and reads, with the JSX runtime
inside it — built beside the pages as the SDK a member's own copy compiles against: the app's agent
takes the page project and that SDK into its sandbox, edits the one source file, builds it there,
and deploys the result through the standing site tools, so the workspace then owns its copy at its
own origin and unbinding it returns the app to the page the deploy carries. That SDK reaches the
sandbox as the archive `[sites] page_kit` names, a deploy input the portal's build produces; a
deploy that names none builds no app page and refuses a deploy of one. Before hosting an app page,
sites validates its source imports and mount call, then builds it.

Beside chat, each agent carries read projections shaped by the same contracts chat enforces: its
homepage (the frame link of the hosted site `set_homepage` bound; a bound site's audience IS the
agent's, resolved at every read and frame visit from the agent's `visibility` rather than copied
onto the site row, so a private agent's homepage answers its owner and admins, a workspace agent's
answers every member, and flipping the agent is what moves the page — the bind itself is the
re-gating act, so it takes the site's creator acting and a live speaker unless the same turn
deployed the site, the seed's deploy-and-bind shape; a deploy-wide page's code is public and its
agent's audience decides only whether the portal offers it), its loadable skills (`agent_skills`,
the composition a turn loads), its conversations, and its settings (`agent_detail`: prompt, spec,
bound surfaces, the deploy's ceilings — answering the agent's whole audience), which hold its
connector accounts (`list_agent_connections`) as a section of their own and whose facts link to the
workspace usage view: spend is read there and nowhere else, per agent inside the admin's rollup.
`agent_turn_statuses` answers an apps index one row per agent the member's audience holds — the
liveest turn on it, what a running one is doing, when it last moved, and whether its most recent
terminal turn failed — with every fact on that row computed only from conversations the reader
reads, so a workspace-visible agent tells each member their own picture of it and never a
colleague's private turn. A screen polls it for as long as a tab is open, so it costs one turn
aggregate for all the agents at once and no per-agent read of a resting one.

The `conversation` kind's member listing lists the conversations this member is in across their
audience agents — bound to them or holding a turn they spoke — and, under a bound of its own, the
readable ones a colleague is in and they are not: the rail's projection, each row titled the way the
title job named it, flagged `mine`, and a colleague's naming who spoke it. It is the one row a
conversation has in the portal: `member_object` resolves a permalink to the same row past the
listing's bound, and every row states `readable`, `disclosable` and `speakable` — whether its
content reads now, whether an admin may open it by acknowledging, and whether the member's messages
land in it. An admin lists the rows they may not read as metadata under the declared filter
`readable=false`; no unfiltered read widens. Each row also states `unread`: the conversation moved
after the later of this member's read cursor (`conversation_read`, written by the transcript read
that serves them the messages) and their own last turn — speaking is reading, so a thread they
answered on another surface reads read without a portal visit, and one an agent or a colleague
moved draws its rail mark live. A thread holding neither a cursor nor a turn of theirs is unread
from its first turn: every conversation a workspace already held took a seeded cursor at its own
activity moment, so a thread carrying none is one opened since. A conversation no member spoke in
is an extension's errand and is in neither. A row names what last fired a turn in it
(`automation_kind`, `automation_name`, `automation_title`): every turn records what fired it and
only a scheduled task's run and a source trigger's delivery ever do, so core states the fact
without naming a kind an extension owns. `owner_email` is whose the conversation is — the member it
is bound to, and for a workspace conversation, which `conversation_audience_member` leaves bound to
nobody, whoever spoke first. An agent's conversations are the member's own plus the
workspace-shared ones, each opened as its turns, the turns those spawned nested beneath them (a
subagent runs in its own conversation carrying the parent's audience, in the spawning turn's
sandbox); an admin lists every conversation of the agent and reads another member's private one
only by acknowledging first that it may hold private information.
The acknowledgement is a granting act, so it rides the prepared-intent lane like every other panel
mutation — `read_private_transcript`, admin-only — and the turn is its audit record; the row it
writes names the reader, the subject, and the moment before any content is served, and is what the
content gate answers on, opening that conversation to that admin for an hour, so a second visit is
a second recorded access rather than a silent re-read. The disclosure opens the conversation and
the automations reporting into it together: a scheduled task's prompt, description and run
responses are words of that conversation, so an admin reads them on the same acknowledgement, for
the same hour, on the same record. An admin who has not acknowledged lists another member's private
task as the management row whose cadence, pause and expiry are already theirs, and its content
reads `private member task`. Chat never carries it: the disclosure answers where the reader's role
is established, which is a portal member read, so an agent asked in chat elides that content
however it asks. The record is the operator's, not a product
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
the team roster, connections and their streams, the deploy's member-fillable credential slots, memory, shared
files, hosted sites, and usage. The artifact listing is always a member browse: administration may
open a known private agent, site, or link, but never adds that agent's files or another member's
private site to it. The wiki is a second reading of two of them, as a document rather than a
listing: the workspace's shared memory set out under the kind that filed each item, and the roster
as the way into one member's page, where that member's own memory answers to them alone.
Four nightly passes write that document. One reads the whole page on the deploy model and retires
the rows that repeat one another; the other three then write from what survived — the paragraph the
page opens on, the paragraph over each band, and each member's part and what they carry. A page a
tool writes about its own runs derives no rows at all, and a row an earlier sync derived from one is
retired when the derivation reaches that page again. The rows stand under the paragraphs as the
record they were written from, each naming the source page it came from.
The roster (`list_members`) is the `member` kind's: every member reads who
their colleagues are, which of them administer the workspace, and who holds a seat, exactly what
the `member` kind answers a member asking the main agent in an internal conversation. The roster
is internal: a child agent and an externally shared channel answer the speaker's own row alone,
whoever asks, so a channel another organization sits in never hears the staff list; a portal
session is always the signed-in member's own audience, so the panel needs no such branch.
Adding someone is `add_member`, the one verb that mints a member before their first contact: a
member on the main agent names an email at any domain — unless the workspace turned
`members_can_add` off — and only an admin may make them an admin in the same act. Core tells them
nothing: the deploy's `member_added` listener runs on the add's own
transaction and records whatever reaches them, and only a listener brings the `notify` field an
admin who will tell them personally sets false. A workspace a sign-in founds is named by its
**signup subject** — the founder's full address for a personal mailbox, otherwise its domain — and
its id is `uuid5(NAMESPACE_DNS, signup_subject)` (`signup_workspace_id`); `workspace_subject` reads
the subject back off the first member, so a company domain subject lets a channel-verified
colleague join on first contact and a personal subject matches only that address. An address that
is already a member is refused rather than silently promoted; changing an existing member's role or
seat stays the `member` kind's admin-gated apply. The new member holds a seat from creation. The
address must parse as one `local@domain` with no whitespace: every creation path crosses that shape
gate, so an address no sign-in could normalize to and no channel-verified join could equal never
becomes a seated member the `member` kind cannot delete. Membership is managed in an internal
conversation only — the verb refuses in an externally shared channel, where its refusal would
confirm a colleague's membership and its success would mint a member.
The usage view (`member_spend`) answers every member their own window — the ledger rows their
conversations' turns wrote, the join a `member` cap binds on, beside their member-scoped caps —
and `spend_rollup` adds the workspace rollup for an admin, so a non-admin's payload names no other
member and no agent. Memory with no query (`recent_memory`) lists the live items under the viewer's
own subjects a page at a time, newest first and narrowable to item classes the provider itself
declares; a query searches every agent the member reaches, one per-agent reader each, unioned and
deduped — so source-derived pages stay fenced by that agent's source grants (the same reader a
turn's tools search under). Each memory row names one `source` — its page where a reader may
read it, else the conversation it was written in — and a condensed row (a summary, a correction, a
wiki paragraph) the union of the sources of the rows it stands for, under the same page fence; the
surface drops any conversation its viewer cannot open. A listing pages by keyset, never by offset: one shared cursor
(`ListingCursor`: `created_at` with the row id breaking its ties) and one page envelope
(`ListingPage`) carrying the positions its Newer and Older controls walk to, so a row landing
mid-read shifts no boundary and every listing pages identically. A cursor the surface never minted
is refused rather than answered with some other page. Shared files are the member's own
conversations' artifacts, every conversation's for an admin, each carrying the signed TTL link a
delivery would and paging by the same shared cursor, `shared_artifact.id` breaking a tie two files
one turn shared in one instant would otherwise leave unbroken. The artifact listing lists a file
when a member-admitted turn already stood in its conversation when it was shared, while a scheduled
file from a machine lane stays on its radar run. A member entering the conversation later does not
move its earlier files into the listing. A `details` file is never listed: it belongs to the reply
that carried it. A file opens to what a page can honestly render — the validated raster preview
`artifact_preview_link` signs, text up to a bounded read, and a plain refusal to preview anything
else — leaving the download an explicit act rather than the click's default. A raw SVG is never
drawn in the member's page.

Every object kind reaches the portal through two generic reads rather than a page of its own:
`list_member_objects` is one kind's rows and `member_object` is one row whole, each answering
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
needs a turn. A connection or source names its owner to everyone its own gate already admits: the
row reaches a member because it is shared or theirs, and a member told a binding exists but not
whose account it draws on cannot tell a colleague's grant from the workspace's. `own` stays the
separate answer to who may manage it. A panel mutation
is a **prepared intent**: the form's structured intent is admitted as a turn on the member's one
durable intent conversation with that agent and dispatched verbatim to the typed object or tool
action — no model round, no fold into a live chat turn — so a submit applies exactly or returns
the kind's refusal, the turn is the audit record, and the conversation's dispatch runs a
member's intents one at a time in order. Its activity uses the action's declared presentation
label without a model call; a tool bridge without a presentation emits no activity label. In a chat
turn a tool that declares an `activity` label publishes it as its call starts, also without a model
call, and every activity frame names the tool it labels, so a surface can draw a step its own way —
the portal draws a `memory_update` as the memory it wrote, beside the reasoning rather than in it. A
connect intent leaves the same private OAuth handoff
chat's connect_account does: the URL rides the turn's terminal and is minted per speaking member
at stream time, never in a transcript or an intent response. An admin
replaces any existing prompt or setting from the agent page's prepared intent. In chat, only the
main agent may replace an agent prompt, including its own; a child agent may replace none. The
agent kind writes the complete row directly, and the turn is the audit record. A member's role and
access change as one apply on the member kind, through that kind's guards.
Agent delete archives: the app stops, its record stays, and its name is available. Nothing is
dropped — the conversations, spend, scheduled work, grants and connected accounts stay on the row,
which is why the act is reversible and why no cascade over them is owed. A member's message founds
a turn carrying the refusal, exactly as a cleared seat does, so they read it on whatever surface
they said it on — the fold is closed too, so a message beside a live turn is refused rather than
joining it. Work fired by a clock has nobody to tell and a row of its own to settle, so it raises
`AgentArchived` instead: None already means a member ended the wait, which is what every runner
retires its row on, and an archived app ends nothing. Each runner catches it and leaves its row
where it is — a task keeps its occurrence, a pause and a watch stay armed, a spawned result stays
pending — so a restore runs what was owed. A source is the one clock-fired cost with no turn to
refuse: it belongs to the workspace and is reached through grants, so the sweep gates on its
readers — a source the archive took every one of them from waits for a restore, its pages and
their extracted facts being tokens spent on a feed nothing can read. The freed name is what takes the app out of the live
namespace: the row stays gettable under its durable `~archived-<id>` name — the exact
`~archived-<uuid>` form is reserved for these readable refs and caller-authored names retain the
ordinary object-name grammar — listed by
`object_list agent` under the `archived` filter and by `list_archived_agents` — and
`restore_application` is that row's own action, taking the name the app returns under, since
another app may hold the name it had.

A workspace-scoped view has no agent of its own, so its intents ride the main agent's lane — the
agent every surface already routes an unbound member to: a connection's share and its feed settings; a
memory correction; the radar's and the wiki's rebuilds; and a credential slot's set, replace, and
clear. Setting a slot's
value is the one mutation whose payload never enters an intent: the intent asks for the same
sealed `request_credentials` prompt a chat turn produces, and the value crosses only in that
prompt's private fulfillment, so a secret reaches no turn, transcript, or intent response.
The durable request carries one identity across refreshed seals: an authenticated admin read may
renew its short-lived seal for 24 hours from the request, and a stored marker keeps every fulfilled
prompt closed.

A rebuild is the lane's one act that writes nothing. The radar's entries and the wiki's
page-derived rows are a job's text, and derived state is a job's to produce, so the intent marks
that work due — dropping the entries inside the digest job's own window, clearing the cursor the
fact deriver rides — and the job writes it on its own interval. Each control names the band it
reaches rather than the page it stands on: a report the job will not read that far back for, a
consolidated summary the hourly pass re-forms on its own terms, and an item an agent recorded in a
conversation that has ended are not a rebuild's to redo, and a control named for the whole page
would promise them.

The debug and memory-explorer surfaces are the operator-audience surfaces — the `ufoctl`-verbs
audience, not a member action. They share one operator web session (`ufo.sdk.operator`, one
`ufo_debug` cookie, opened by `ufoctl debugger` posting this machine's CLI token): `identify` is the
entire authorization. It admits the first credential — posted token, then Authorization, then the
cookie — the deploy's operator rule grants: the built-in `seated_admin` grants a seated admin reach
`own` over the workspace the bearer claims, where any `?ws=` but its id answers 403; a
first-party rule may grant reach `fleet`, under which `?ws=` is the rule's to resolve and the
debugger lands on the fleet index (`FleetDirectory`, which raises for any other grant). Core binds
the resolved workspace and every read is RLS-scoped by construction. The cookie name is a contract:
a sign-in gateway in front of the deploy sets and clears the operator session by it. The debugger
reads a workspace's sessions (conversations, turns with terminal outcomes and subagent children,
DBOS steps, transcripts, rollover and compaction records, workspace files, a live SSE tail) and
links out through `[debugger] turn_urls.<label>` and `conversation_urls.<surface>`; the memory
explorer reads its durable memory store — every `memory_item`, shared and per-member, live and
superseded, indexed and due, carrying the recall-decay signals recall itself applies. Each surface
reads only its owning extension's data, so neither reaches a core internal nor the other extension's
tables.

A terminal paste never uploads: the client saves the clipboard image under its conversation's
`$UFO_HOME/runs/<id>/images` — capped at the image read's own bound, swept by the client at exit —
and the message names the `$UFO_HOME` path as `[Image #N: path]`, so the bytes cross only when the
agent reads that path through the current-run image read. A dropped image file arrives as its pasted
path and is copied into the same stash. The read runs off the client's loop and lands only in the
entry whose Ctrl+V asked for it. A conversation's Changes view renders what git reports uncommitted,
scanned when a turn commits and recorded against the conversation that owns the sandbox — a
subagent's work answers on the parent's screen, and a file outside a checkout is not a change. A
deploy sandbox walks its own disk and answers for every checkout under it, whoever changed it. A
terminal-bound workspace is the member's real directory, arbitrarily large, so it is asked only
where the turn's tools worked: the checkouts the turn's `write` and `edit` calls named, the
outermost checkout at the workspace root when the turn ran `bash`, and every checkout the last scan
reported dirty — a turn that ran no such tool asks nothing, and a checkout nested below a
non-checkout root answers only when a file tool names a path inside it. `surface_identity` and
`conversation.surface` are open namespaces validated by surface registration, not a fixed enum.

**An addressed surface resolves its tenant by the sender, not by the installation.** An
`addressed` surface's provider is the deploy's — one project, one line, many registered addresses —
so the installation identity selects no workspace: every workspace binds the same one and
`routes_ingress` is false, the `surface_installation` identity index narrowing to the installations
that do route (a chat installation, which belongs to one workspace). What selects the workspace is
`surface_address`: one fleet-unique row per (surface, address) naming the workspace and member that
address reaches, staged by a setup tool with the claim's expiry (`reserve_address`) and cleared
against the proving message (`confirm_address`). So an address belongs to one member across
the fleet, and one line serves every workspace and every member on it.

Shared surface requests authenticate their workspace before core binds it. `SurfaceSpec.identify`
maps the untrusted installation id through `SurfaceAuth` to one unique `surface_installation` and
verifies that workspace's credential over the original bytes before core binds it; unknown
installations, workspaces with no credential, and bad signatures share one rejection. A handshake
that carries no installation answers as a bounded, side-effect-free `Response` without binding a
workspace. Surface identities and conversation keys include
`workspace_id`. Durable writeback enumerates a bounded set of workspace ids through the owner
connection, then binds each before reading or delivering any tenant data.

Onboarding flow engine is core (steps are contributed by extensions/packs). `Provisioning.seat`
(`runtime/provisioning.py`) is the one write that founds a workspace: `ufoctl init` and
`DeployContext.provision` both call it. It creates the first admin and the main agent, seats a
later member idempotently, and runs the `workspace_founded` handlers on the founding transaction.
`init` refuses a deploy that already holds a member.

## Accounting / billing

Every model call and tool call meters into `ledger` in the same commit as the step.
Extensions record direct token calls through `ToolContext.meter_tokens` for turns or
`ExtensionContext.meter_tokens` for background jobs, with a unique provider-call
ID, token counts, price, and funding source. The ledger binds them to the workspace and, for tool
calls, the turn. Repeated writes for one call are deduplicated.
Realtime visibility: live per-turn cost on the stream, workspace/member/agent rollups in
`ufoctl spend`, the surface reads (`spend_rollup`, `member_spend`), and an extension handler's
`ctx.spend_rollup`, the workspace totals naming no member or agent; the workspace rollup also groups
spend by the service each row names (`by_service`). Caps
evaluated at inbound and per-step; `reject` refuses new turns, `park` suspends. Prices are a pinned
table per model; usage a key that is not the deploy's paid for still meters (visibility without
billing).

Every ledger row names the service that metered it and the unit it counts (`dimension`):

| Service | Units |
|---|---|
| `models` | `tokens` (with the six token classes), `images`, `videos` |
| `proxy` | `requests`, `gib` (bytes) |

An extension books what a service metered through `ExtensionContext.record_usage`: service, unit,
backend, amount, token, session, labels, resource, attempt, `occurred_at`, `byok`, price and rate
card digest, and for `tokens` the model and its six classes. One row is written and charged per
`(service, resource or session, unit, attempt)`; a replay with other content raises
`TurnUsageConflict`. The row is booked at `occurred_at`, which falls at most 900 seconds past, on a
day the fold has not closed, and at most 60 seconds ahead. Labels are at most 16 keys
(`[a-z0-9_.-]`) with values of at most 64 characters; a `turn` label naming a turn of the workspace
binds the row to that turn, so member and agent caps and attribution count it. Model calls the
sandbox makes through the egress proxy are `(models, tokens)` rows labelled `via: proxy` on their
turn, so the turn's cost counts them; its egress is a zero-priced `(proxy, requests)` count. A row
inserted with no service takes the one its dimension belongs to (trigger `ledger_fill_service`,
which files `sandbox_tokens` and `egress` as the rows above), and the per-minute
`ledger_service_backfill` job files any row still without one, 5000 per workspace per tick.

An extension reads usage back through `ctx.usage_lines`: a window `[since, until)` per UTC day,
grouped by any of `service`, `dimension`, `backend`, `byok` and `token` and by label keys, and
filtered by backend, `byok` and label values. The nightly fold of turn-less rows into
`ledger_job_day` keeps service, dimension, backend, token, `byok`, model and price digest, so a read
by key covers every day; labels and sessions stay on ledger rows, so a read grouped or filtered by a
label covers turn rows and the days the fold has not closed. Two spend windows serve a caller that
bounds a token or a session: `ctx.token_spend` sums what the platform paid for a token's records
over a window, counting a folded day whole, and `ctx.session_spend` sums a session's. Neither
counts a row the workspace's own key paid. `ctx.spend_admitted(platform_paid=True)` asks the gates
about work no key of the workspace pays for, so a stored model key exempts nothing.

Background and off-turn model calls clear the workspace spend cap and the spend gates before
reaching the provider. They carry no member or agent attribution, so narrower caps do not apply. An
ambient classifier refused on its deploy model retries on the surface agent's model, allowing a
workspace's own provider key to decide without platform spend. If both are refused, a park reaches
admission for resumption and a reject stays silent.

**Spend gates** are the Manifest point (`spend_gates`, first-party only) through which an extension
decides whether a workspace may spend; core has no balance of its own. Core calls every gate at the
points only it controls, on the caller's own connection, so a gate that raises aborts that write:

| Moment | Park | Reject |
|---|---|---|
| `admit`, `fold` | held, and the gate's words written once as the hold notice (round `-2`) | refused; a member's own message is held instead |
| `resume` (dispatcher) | stays held | stays held |
| each model round (`sustain`) | the running turn parks | — |
| `spawn` | the parent parks | raises to the spawning tool |
| `off_turn` (`ModelAccess`, `JobSpec.spends`) | deferred, the member told once | deferred, the member told once |
| `status` (`ctx.spend_admitted()`, the stream's park frame) | read only | read only |

Caps decide first, then each gate in lockfile order; `reject` beats `park` beats `allow`, and the
message is the first decision's carrying the winner. At `spawn` the gates alone decide, and the
child's caps hold it at its own rounds. `ctx.spend_admitted()` asks the gates alone too: a read that
sends a member to lift a gate must not report a cap, which that act does not lift. The park frame
names whatever holds its turn. A cap that holds alongside a gate refuses as a cap always does,
because what lifts a gate does not lift a cap; the gate is then asked with no member hold open, so
it words a refusal. Core owns what no gate can decide:
the moment, whether the workspace's own key pays for the model (`self_funded` — its own credential
row, never a member's), and the frozen `byok` a round is weighed by, so an own-key round is asked at
zero platform spend. A gate's `admits_sql` is ANDed into the owner-scope candidate reads — source
sync, page change, and any `JobSpec.spends` job — so a held workspace is never opened. `Ledger`
hands each gate `charged` once per positive priced delta after the replay guard, with
`platform_paid` false where the workspace's own key paid; a replayed snapshot charges nothing. The
gate is built once at boot from the deploy's public base and browser home, so its words can name
where to act. Shipped core hands the deploy's gates and ledger to every call that takes them
(gated); a context holding neither refuses `spend_admitted` and `meter_tokens` rather than answering
ungated. `sample` is the probe of every moment.

A gate sees the prepared intent it decides on (`SpendAsk.intent`: the wire tool and the kind and
action it names), so a gate that holds a workspace can admit the verb that ends its own refusal —
refusing it would refuse the act that lifts the hold. Admitting one costs nothing, because a
prepared intent runs no model round: the turn dispatches the verb and terminates.

A burn the workspace's own provider key paid for is metered and charged as not platform-paid,
whether a turn or a background job spent it — BYOK meters without billing.

What served the call also decides what it cost. A stored key is metered by its provider, so its
rows carry the rate card's real price: that money is owed, just not by us. A member's connected
account is a subscription they already bought, so no per-token money exists at all. An attempt
freezes one card over its whole model route: connected-account entries price at zero, and a
workspace/deploy entry at its real rate. Every segment's live cost, terminal cost, caps, rollups,
and usage export read that same card, so none puts a price on the member's screen that nobody is
owed and a fallback the deploy pays for never disappears. The tokens are recorded in full either
way, which is what makes plan usage visible without pricing it. Only a member slot can
hold a grant, so a background job — which binds no member — is never plan-served. Credential,
funding class, exact payer slot, and client resolve as one value: a rejected credential may refresh
within that slot but cannot fall through to another payer. A turn freezes the same payer beside its
model and rate card for the attempt, and recovery refuses a resolution that no longer matches it.

Seats decide who the agent
answers, and nothing bounds how many hold one: a workspace pays for what it spends, so no count of
members is shipped, rated, or enforced anywhere. A member holds a seat from creation and an admin
revokes it — the `member` kind's admin-gated apply — to remove that person's access. Core owns the rules: refusal at admission,
per-round park on revocation, and the last seated admin's irrevocable seat.

A member surface that could not resolve its speaker to a member is refused outright, on every
deploy and with nothing to configure. That is the workspace boundary: every member surface resolves
its speaker (the CLI token is minted for the member `init` creates; a chat surface, the portal and
prepared intents each name one), so a speaker none of them could name is a stranger, and answering them
would run a turn carrying the workspace's own audience for a person the workspace cannot name.
Making the refusal conditional on anything — a plan, a bound, a column — is how a workspace ends up
answering the one speaker it should not.

The core-registered `workspace` kind is the shape itself as one read-only object — one instance per
workspace, named by its id, carrying the member and seated counts and a `roster` naming who holds a
seat, which follows the roster rule above — whole to a member asking the main agent, the speaker's
own row alone to a child agent, and nothing at all to an externally shared channel, which reads
none of this kind. Its spec carries no field, because nothing it reports is authored: the counts
are derived, so create, update, and delete all refuse.

An external billing vendor is an extension draining the usage-export seam
(`ctx.pending_usage_exports` / `ctx.ack_usage_exports`): core mints frozen, consumer-keyed delta
intents from settled ledger rows — settlement and dedup keys are writer knowledge — and the
extension is a pure delivery adapter to the vendor's ingest API. Each
intent freezes a `byok` label at mint: host `tokens` whose model's serving provider key slot
(`ModelRegistry.key_slot_for` — the same resolution `client_for` applies, any provider) is stored
by the workspace, so the rate card bills only pass-through usage. An intent also names its row's
service, backend, token, session and labels. A priced `proxy` row is written once, so it mints at
once with its own `byok`; a zero-priced count never mints.

Paying for spend is a chat act like every other member action: a speaking admin asks, and an
extension tool returns a short-lived provider link — its customer id lives in the extension's own
store, so core gains no billing table, callback, webhook, or route. What a turn spends against is
whatever balance a spend gate keeps.

## Model abstraction

`ModelClient`: `complete(messages, tools, stream)` + token accounting + provider image/content
limits. Core implementations: Anthropic, OpenAI. Model policy per agent (`auto` routes by task
class); provider credentials come from workspace slots or deploy config.

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
model keys (env refs), enabled extensions + versions, the active pack (`[pack] name`), sandbox
carrier, stream hub, OTLP export target, extension-store
toggle, spend defaults, and the feature-flag backend (`[flags] backend`, unset selecting none, plus
`cache_ttl_seconds` — how long that backend may answer a flag out of its own response cache).

## Running it

The developer surface is a pip-installable CLI running as a **host process** — ufo is never
containerized for development:

```bash
uv tool install ufo        # the Python package is the primitive; brew formula = later wrapper
ufoctl init                   # writes ufo.toml; onboards workspace + initial admin + main agent + model key
ufoctl serve                  # one process: surfaces + workers + jobs + egress-control RPC — SQLite, zero services
ufoctl portal                 # opens your workspace in the browser; the terminal client connects via curl
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
| Turns, queues, jobs | DBOS coordinates through Postgres: any instance pulls. Each process's DBOS executor id is its instance id, so in-flight work is attributable to a heartbeat: the executor-recovery sweep re-dispatches workflows whose executor has no fresh `runtime_instance` row, and never touches a live peer's — recovering a live workflow would double-execute it. A turn's claim admits only the workflow that took it, so once that workflow ends or leaves the store no dispatch reaches the row and recovery has nothing to resume: the stranded-turn reconciler cancels it. Its DBOS status is the only signal that separates it from live work — a spawned agent outlives its spawner, and a stepping turn goes minutes between row writes. |
| Live deltas | Shared hub required (Redis hub extension); terminal frames stay durable in Postgres. |
| Blobs | S3 backend required; the filesystem backend is single-instance-only. |
| Sandboxes | Per-conversation, resumed across instances from the durable `sandbox_handle`; an in-cluster carrier needs its workspace root on storage every instance reaches. |
| Surfaces, webhooks | Stateless behind a load balancer; sessions and idempotency live in Postgres. |

Two invariants make this safe, and they hold even single-instance:

- **At most one running turn per conversation** — dispatch enqueues a conversation's frontmost
  queued turn only when no sibling runs, and every workflow exit offers the next under the
  conversation row's lock; the transcript's monotonic seq depends on it.
- **The workspace lives in the sandbox** — a turn's `sandbox_conversation_id` names the conversation
  whose row's `sandbox_handle` holds the container, its own unless it inherited the sandbox of the
  turn that spawned it, so any instance resumes exactly that sandbox and none may destroy one.

Misconfiguration fails loud at boot: the shared owner DSN must be set, no route may lie under a
`[serve] gateway_prefixes` prefix a gateway in front answers, and a set `[sites] page_kit` must name
a file. Instances heartbeat a `runtime_instance` row so the fleet tracks its live
executors; a peer that stops heartbeating has its in-flight turns recovered by the survivors.

### Runtime roles

An instance logically comprises three in-process runtime roles — **surfaces** (HTTP in, streams out),
**workers** (turn workflows), **jobs** (sync, derivation) — plus the **egress data plane**
(`ufo-egress`), a standalone Rust process that holds no keys and resolves every policy and
secret through the egress-control RPC these roles serve. The runtime runs the three roles in
every instance and defines no per-role deployment — mapping processes now would be speculation. What
the runtime does fix is the seam that makes the process split free: **roles share nothing in memory** —
cross-role communication is only Postgres/DBOS queues, the blob store, the hub, and the
egress-control RPC (any instance answers identical rules from DB state; sandboxes are co-located
with the instance that created them). An import-boundary gate enforces the in-process seam. The
enterprise k8s layer then splits roles into Deployments with per-role autoscaling by
configuration, not code change.
`ufoctl bundle` produces a runnable artifact (OCI image + pinned config + lockfile) — the same
bundle installs OSS, on-prem, or hosted.

## Example extensions (the API's acceptance tests)

| Extension | Points it exercises |
|---|---|
| OpenRouter (any model router) | models, deploy_keys |
| Brief pipeline (typed outline → draft → critic stages the agent chains) | subagents, skills |
| Composio / Pipedream connector brokers | connectors, routes (OAuth), deploy_keys |
| Docker, E2B | carriers |
| Redis stream hub | hubs |
| Open feature-flag backend | flag_providers |
| GitHub / Asana / Google Ads feed-sync sources | sources, credentials, auth_proxies (`direct`), deploy_keys |
| Agent-guided education / onboarding | onboarding, tools |
| Scheduled tasks (cron / one-time) | jobs, invoke, tools, requires (`memory_search`) |
| GH code review on PR | agent prompt, source trigger, coding subagent, GitHub connector |
| Service self-improvement / bug-fixing from o11y | sources (o11y), jobs, trajectories.read, invoke (evals), agents.propose_change |
| Security review | tools, subagents |
| gbrain-style memory (source page → derived facts, consolidated) | memory_search, sources, hooks (page_change) |
| CRM / ATS | connectors, sources, hooks (page_change), tools |
| Websites hosted at a permanent link | tools (sandbox serving), surfaces (the access-controlled frame), objects (`site`) |

Packs (activation bundles, not code — see Packs): **assistant** is the flagship. Each activates
one coherent config, no code of its own beyond what it references.

## Non-goals (core, now)

- No Kubernetes, CRDs, or operators in core (the enterprise k8s layer wraps core — principle 3).
  Workspace isolation is the `workspace_id` each statement filters on, bound per request, turn, and job.
- No Redis in the single-process default; no router service in core (both are extensions).
- No self-improvement machinery in core (the extension API carries it — see `trajectories.read` /
  `agents.propose_change`).
- No second representation of any fact: one transcript store, one schema source, one config file.
- No tool that another tool or `bash` subsumes.
- No Docker-in-Docker, ever: sandboxes are sibling containers (host daemon or mounted socket), or
  a remote carrier.
