# Build plan

One unit = one fresh session = one reviewable branch, proven end-to-end before the next (realistic
input → durable state → usable output). Contracts per unit are in `docs/contracts.md`; file-level
inputs in `docs/salvage.md`. Every unit ships its tests (pure logic direct; Postgres/Docker
integration real — never fakes) and updates spec/contracts in the same commit if a contract moved.

## U1 — heartbeat

A real streamed chat turn against Anthropic with durable everything, no sandbox yet — zero
services: SQLite + filesystem blobs + in-process hub.

- Scaffold: uv workspace (`core/`, `extensions/`, `packs/`), ruff/pytest/CI, `compose.yaml`
  (Postgres, for deploys + the Postgres half of the test matrix only).
- `schema/` as SQLAlchemy metadata + alembic (the single schema source), dialect-neutral; engines:
  SQLite/aiosqlite (default, WAL) and Postgres/asyncpg. **Verify DBOS on SQLite against the
  installed version first** — fail loud if unsupported; do not silently keep Postgres as default.
- DB-touching tests run on both dialects (SQLite everywhere; Postgres suite gates on the live
  service, skip when absent).
- migration 001: workspace, member, surface_identity, agent, conversation, turn, ledger.
- `config.py` (ufo.toml, fail-loud), `db.py` (`workspace_tx` boundary + gate), `o11y.py`, `blob.py`
  (FilesystemBlobStore + S3BlobStore), `hub.py` (in-process), `models/` (anthropic, openai),
  `loop/` (DBOS queue partitioned by conversation, TurnEngine minus compaction/tools, transcript
  `messages.json.lz4`), `accounting.py` (ledger writes + prices only), `surfaces/cli.py`, `cli.py`, `serve.py`.
- First-run bootstrap: create workspace + first owner + default agent (`ufoctl init`).
- **Proof**: `ufoctl chat` streams a real Anthropic turn; transcript in blob store; ledger rows
  priced; second message continues the conversation; mid-turn cancel commits a terminal frame;
  kill -9 during a turn → DBOS recovery still ends the client's wait.

## U2 — sandbox + proxy + file tools

- `sandbox/local.py` (core default carrier: temp-dir workspace + subprocess), `extensions/{docker,e2b}`
  (carrier extensions, create-or-attach, async waits), `sandbox/build_template.py` (one image
  definition rendering both the Docker Dockerfile and the E2B template so they can't drift; the
  `sbx`/`sbxfs` binaries live in `core/.../sandbox/image/`), `jobs.py` runner.
- `sandbox/proxy/`: sole egress route (container network default-deny), sentinel swap, ScopeRule /
  InjectionRule derivation (grants only — manifests arrive U3), MeterRule stub.
- `tools/registry.py`, `tools/context.py`, builtins `bash read write edit`.
- **Proof**: agent writes + runs code in the container; `curl https://example.com` from bash fails;
  a sentinel model key swaps at the proxy and the call succeeds; sandbox cannot read
  `conversations/<cid>/messages.json.lz4`; container killed between turns recreates transparently.

## U3 — extension system + credentials

- `sdk/` (public re-exports), `ext/loader.py` (entry points → Manifest, validation, rule
  derivation), `credentials.py` (encrypted BYOK slots), onboarding-step registry (engine in U6).
- CI gates: extensions import `ufo.sdk` only; no k8s anywhere; no vendor o11y SDKs.
- `extensions/sample/`: registers one of each — tool, subagent, connector stub, source, hook,
  job, route, credential slot, onboarding step, condenser — the conformance harness for the API.
  It records every call it receives via its own `ExtensionContext` (durable, no mock call-logs);
  `tests/ext_conformance/` drives each point through public surfaces and reads the records back.
  Negative cases ride along: undeclared credential slot raises, cross-workspace access is
  unrepresentable, core-internal import fails CI. Breaking the sample = breaking the public SDK.
- `ExtensionContext` incl. `trajectories_read` + `propose_change` (approval = owner ack in chat).
- **Proof**: sample extension's tool/job/route/credential all fire; its credential slot injects via
  proxy rule; a `propose_change` round-trips through approval into agent config; an
  `extensions/` import of core internals fails CI.

## U4 — memory + sources

- `memory/`: schema (memory_item, memory_summary, page, chunk), `embed.py`, `index.py`
  (IndexBackend + the two dialect-native impls: SQLite FTS5 + local cosine, Postgres tsvector +
  pgvector — selected by engine dialect, fail-loud), `service.py` (recall fusion,
  `{member:<id>, shared}` filter), `pipeline.py` (Condenser seam + default condenser as jobs,
  batch-at-interval).
- `sources.py`: SourceBackend + `folder` + sync driver job; page_change hook seam (pages → derive).
- Builtins `memory_search` (absorbs store_search/load_sessions modes), `memory_update`; auto-inject
  recall at turn load (bounded, best-effort).
- **Proof**: a fact stated in one conversation recalls in a new one; member A's memory invisible to
  member B; a folder source syncs a doc and its content recalls; derivation never runs inline.

## U5 — loop depth

- `loop/compaction.py` (window trigger, before/after records), `loop/subagents.py` (profiles,
  DBOS child spawn, foreground/background), skills (`load_skill`, packs layout `packs/<name>/skills/…`),
  builtins `ask_user`, `spawn_subagent`, `share_file` (TTL token URL).
- Core's three skills — `sandbox`, `memory`, `delegation` — land here (workflow guidance for
  core builtins only; the skill-ships-with-what-it-teaches rule is in spec.md).
- **Proof**: a conversation exceeding the window compacts and later turns still recall pre-compaction
  facts verbatim from transcript; a typed subagent round-trips schema I/O; a shared file downloads
  via its token URL and rejects without it.

## U6 — surfaces + onboarding

- `surfaces/slack.py` (signature verify, thread conversations, member linking, writeback claims,
  message-identity idempotency, Block Kit markdown replies, two-way file attachments: share_file
  uploads to the thread, inbound Slack files into the turn), `surfaces/web.py` (chat UI, hub tail,
  artifact delivery), onboarding engine (core steps: create workspace/owner/agent, model key;
  extension steps append).
- **Proof**: the same agent answers in a Slack thread and web; a second member links their Slack
  identity and gets their own memory subject; a file the agent shares lands in the thread and a
  file a member posts reaches the turn; onboarding cold-start to first turn works on a clean
  machine.

## U7 — accounting completion

- `spend_cap` schema + `SpendEvaluator` at inbound and per step (reject/park; parked turns resume
  on cap change); proxy MeterRule → ledger; live CostTick frames; `ufoctl` + web rollups
  (workspace/member/agent).
- Scheduled/extension-invoked turns pass through the same inbound evaluation (closes the old
  scheduled-fire bypass).
- **Proof**: a member cap breach parks the turn and says so in-surface; an in-sandbox API call
  meters; rollups match ledger sums.

## U8 — connectors + grants

- Port the connector framework as `extensions/connectors` (broker-generic tools) with the
  Composio and Pipedream brokers beside it (`extensions/composio`, `extensions/pipedream`);
  `grants.py`
  (`/connect` → OAuth link → complete → Grant row with account id); proxy ScopeRules from grants;
  connector-API source backend.
- **Proof**: OAuth a real provider in chat; the agent calls it; a call to an ungranted account is
  blocked at the proxy; a connector source syncs pages that recall.

## U9 — bundling + extension store + scale-out guard

- `ufo.toml` finalized (single config source), `ufoctl bundle` (OCI image + pinned config +
  lockfile), `ufoctl serve` hardening, `runtime_instance` heartbeat + boot guard, extension store
  (`ufoctl ext search/install/remove`, digest pinning, disabled = bundle-only).
- **Proof**: one bundle boots on a clean machine; an extension installs from the store and fires;
  a second instance with any dev default (SQLite, filesystem blobs, in-process hub) refuses to
  boot.

## U10 — packs + scheduled tasks

- Packs seam (`ufo.pack` entry point → `Pack` bundling installed extensions + pack-level
  skills/onboarding; `[pack] name` activates one, narrowing the active manifest set); assistant pack
  bundling memory + browser + connectors + exa; scheduled-tasks extension (JobSpec cron → `invoke`).
- **Proof**: activating a pack makes exactly its bundled extensions' manifests active and its
  pack-level skill loads; a cron fires a real turn on schedule; `wide_browse`-style fan-out runs as
  subagent spawns.

## Post-U10 backlog (extensions, in likely order)

e2b carrier · redis hub · turbopuffer index · openrouter · self-improvement (port the
main-merged offline-replay loop 8e20fa70 onto
`trajectories_read`/`propose_change`/jobs — corpus is transcripts, promotion is governed) ·
security review pack · websites extension (serve tool + routes) · startup + support-bot packs ·
eval harness port · enterprise k8s layer (apiserver rewriter module, multi-workspace hosting).

## Standing gates (legibility gates from U1; sdk gates from U3)

sdk-only imports in `extensions/` · no k8s imports anywhere · no vendor o11y SDKs (OTel APIs only) ·
Redis only inside the hubs extension · engine/`begin()` only inside `db.py` · no proxy-rule
registration API (derivation only) · tool-count budget (a new tool must prove no existing tool
subsumes it) · **legibility (AST gate)**: a module-level single-return function with exactly one
call site fails CI (`sdk/` re-exports exempt); no module named `utils`/`helpers`/`common` ·
`arbitrary_types_allowed` forbidden (a BaseModel needing it is a misclassified internal object) ·
**wiring gate (from U1)**: every schema column has ≥1 write site and ≥1 read site outside tests;
every persisted enum/`Literal` member has a producer; every `LiveFrame` kind has an emitter —
"both ends or neither" · **ext conformance (from U3)**: the sample extension exercises every
Manifest point + the negative cases; a change that breaks it is a public-SDK break ·
**async (from U1)**: ruff `ASYNC` rules on (blocking call inside `async def` fails lint);
`requests`/`psycopg2` banned imports; `to_thread` call sites name the no-async-API library they
wrap · **import boundaries (from U1)**: declared edges between role-owning modules (`surfaces/`,
`loop/`, jobs, `sandbox/proxy/`); a cross-role in-memory import fails CI — roles talk through
queues/blob/hub/HTTP only · **core skills (from U5)**: a skill in core naming a non-core tool
fails CI; core's skill set is exactly {`sandbox`, `memory`, `delegation`}.
