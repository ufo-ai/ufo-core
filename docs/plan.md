# Build plan

One unit = one fresh session = one reviewable branch, proven end-to-end before the next (realistic
input → durable state → usable output). Contracts per unit are in `docs/contracts.md`; file-level
inputs in `docs/salvage.md`. Every unit ships its tests (pure logic direct; Postgres/Docker
integration real — never fakes) and updates spec/contracts in the same commit if a contract moved.

## U1 — heartbeat

A real streamed chat turn against Anthropic with durable everything, no sandbox yet.

- Scaffold: uv workspace (`core/`, `extensions/`, `packs/`), ruff/pytest/CI, `compose.yaml` (Postgres only).
- `schema/` migration 001: workspace, member, surface_identity, agent, conversation, turn, turn_step, ledger.
- `config.py` (selfhost.toml, fail-loud), `db.py` (`workspace_tx` boundary + gate), `o11y.py`, `blob.py`
  (FilesystemBlobStore + S3BlobStore), `hub.py` (in-process), `models/` (anthropic, openai),
  `loop/` (DBOS queue partitioned by conversation, TurnEngine minus compaction/tools, transcript
  `messages.json.lz4`), `accounting.py` (ledger writes + prices only), `surfaces/cli.py`, `cli.py`, `serve.py`.
- First-run bootstrap: create workspace + first owner + default agent (`selfhost init`).
- **Proof**: `selfhost chat` streams a real Anthropic turn; transcript in blob store; ledger rows
  priced; second message continues the conversation; mid-turn cancel commits a terminal frame;
  kill -9 during a turn → DBOS recovery still ends the client's wait.

## U2 — sandbox + proxy + file tools

- `sandbox/carrier.py` (Docker create-or-attach, async waits), `sandbox/image/` (Dockerfile: python,
  node, ripgrep, sbx toolchain), reaper job, `jobs.py` runner.
- `sandbox/proxy/`: sole egress route (container network default-deny), sentinel swap, ScopeRule /
  InjectionRule derivation (grants only — manifests arrive U3), MeterRule stub.
- `tools/registry.py`, `tools/context.py`, builtins `bash read write edit`.
- **Proof**: agent writes + runs code in the container; `curl https://example.com` from bash fails;
  a sentinel model key swaps at the proxy and the call succeeds; sandbox cannot read
  `conversations/<cid>/messages.json.lz4`; container killed between turns recreates transparently.

## U3 — extension system + credentials

- `sdk/` (public re-exports), `ext/loader.py` (entry points → Manifest, validation, rule
  derivation), `credentials.py` (encrypted BYOK slots), onboarding-step registry (engine in U6).
- CI gates: extensions import `selfhost.sdk` only; no k8s anywhere; no vendor o11y SDKs.
- `extensions/sample/`: registers one of each — tool, subagent, connector stub, source, trigger,
  job, route, credential slot, onboarding step, condenser — the acceptance harness for the API.
- `ExtensionContext` incl. `trajectories_read` + `propose_change` (approval = owner ack in chat).
- **Proof**: sample extension's tool/job/route/credential all fire; its credential slot injects via
  proxy rule; a `propose_change` round-trips through approval into agent config; an
  `extensions/` import of core internals fails CI.

## U4 — memory + sources

- `memory/`: schema (memory_item, memory_summary, page, chunk), `embed.py`, `index.py`
  (IndexBackend + pgvector), `service.py` (recall fusion, `{member:<id>, shared}` filter),
  `pipeline.py` (Condenser seam + default condenser as jobs, batch-at-interval).
- `sources.py`: SourceBackend + `folder` + sync driver job; trigger seam (pages → condense).
- Builtins `memory_search` (absorbs store_search/load_sessions modes), `memory_update`; auto-inject
  recall at turn load (bounded, best-effort).
- **Proof**: a fact stated in one conversation recalls in a new one; member A's memory invisible to
  member B; a folder source syncs a doc and its content recalls; derivation never runs inline.

## U5 — loop depth

- `loop/compaction.py` (window trigger, before/after records), `loop/subagents.py` (profiles,
  DBOS child spawn, foreground/background), skills (`load_skill`, packs layout `packs/<name>/skills/…`),
  builtins `ask_user`, `spawn_subagent`, `share_file` (TTL token URL).
- **Proof**: a conversation exceeding the window compacts and later turns still recall pre-compaction
  facts verbatim from transcript; a typed subagent round-trips schema I/O; a shared file downloads
  via its token URL and rejects without it.

## U6 — surfaces + onboarding

- `surfaces/slack.py` (signature verify, thread conversations, member linking, writeback claims,
  message-identity idempotency), `surfaces/web.py` (chat UI, hub tail, artifact delivery),
  onboarding engine (core steps: create workspace/owner/agent, model key; extension steps append).
- **Proof**: the same agent answers in a Slack thread and web; a second member links their Slack
  identity and gets their own memory subject; onboarding cold-start to first turn works on a
  clean machine.

## U7 — accounting completion

- `spend_cap` schema + `SpendEvaluator` at inbound and per step (reject/park; parked turns resume
  on cap change); proxy MeterRule → ledger; live CostTick frames; `selfhost` + web rollups
  (workspace/member/agent).
- Scheduled/extension-invoked turns pass through the same inbound evaluation (closes the old
  scheduled-fire bypass).
- **Proof**: a member cap breach parks the turn and says so in-surface; an in-sandbox API call
  meters; rollups match ledger sums.

## U8 — connectors + grants

- Port the connector framework + Composio client as `extensions/connectors`; `grants.py`
  (`/connect` → OAuth link → complete → Grant row with account id); proxy ScopeRules from grants;
  connector-API source backend.
- **Proof**: OAuth a real provider in chat; the agent calls it; a call to an ungranted account is
  blocked at the proxy; a connector source syncs pages that recall.

## U9 — bundling + extension store + scale-out guard

- `selfhost.toml` finalized (single config source), `selfhost bundle` (OCI image + pinned config +
  lockfile), `selfhost serve` hardening, `runtime_instance` heartbeat + boot guard, extension store
  (`selfhost ext search/install/remove`, digest pinning, disabled = bundle-only).
- **Proof**: one bundle boots on a clean machine; an extension installs from the store and fires;
  a second instance with filesystem blobs or in-process hub refuses to boot.

## U10 — packs + scheduled tasks

- Assistant pack (deep/wide research, browser subagent via BUA port, office docs skills);
  scheduled-tasks extension (JobSpec cron → `invoke`); pack onboarding steps.
- **Proof**: pack onboarding installs skills and they load; a cron fires a real turn on schedule;
  `wide_browse`-style fan-out runs as subagent spawns.

## Post-U10 backlog (extensions, in likely order)

e2b carrier · redis hub · turbopuffer index · openrouter · GH code review (webhook → review →
merge) · self-improvement from o11y (PR #62 replay design on `trajectories_read`/`propose_change`) ·
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
"both ends or neither".
