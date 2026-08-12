# Core contracts

The hard-to-vary interfaces U1–U10 implement, in Python-signature form, organized by module.
Once a unit lands, its code (with tests) is authoritative and the matching section here is
**deleted** — this file's destiny is a module map plus the invariants code can't express.

Container rules: Pydantic `BaseModel` for anything that crosses a boundary (wire, persisted,
config, untrusted) — never `arbitrary_types_allowed`; frozen dataclasses for internal value
objects and workflows; `Protocol` for pluggable seams; one concept, one container, never mirrored.
All persisted records carry `workspace_id`, `created_at`, `updated_at` (omitted below).

## Module map

```text
core/src/ufo/
  config.py      Config — ufo.toml, fail-loud            blob.py     BlobStore + fs/S3
  db.py          engine + workspace_tx() boundary             hub.py      Hub + in-process
  schema/        SQL migrations (single source)               o11y.py     OTel facade
  models/        ModelClient + anthropic/openai               jobs.py     JobSpec + runner
  loop/          queue (DBOS), engine, transcript, compaction, subagents
  tools/         registry, context, builtins/
  sandbox/       carrier (+docker), image/, proxy/ (rewriters, sentinel swap, metering)
  memory/        service (store+recall), index (pgvector), pipeline (condensers), embed
  sources.py     SourceBackend + folder + sync driver         grants.py   connection + agent edge
  credentials.py encrypted BYOK store                         accounting.py ledger, caps, prices
  surfaces/      slack, web, cli, onboarding                  ext/        loader, rule derivation
  sdk/           the ONLY public import surface for extensions
  serve.py       single-process assembly                      cli.py      `ufoctl` entrypoint
```

## Records (`schema/`, Pydantic)

```python
class Grant(BaseModel):            id: UUID; agent_id: UUID; kind: Literal["connector", "credential", "tool_group"]
                                   ref: str; account_id: str | None
                                   granted_by: UUID; conversation_id: UUID | None          # the granting act, audited
class Credential(BaseModel):       slot: str; ciphertext: bytes                            # encrypted at rest, values never logged
class MemoryItem(BaseModel):       id: UUID; subject: str; body: str; item_class: Literal["fact", "episodic", "semantic"]
                                   embedding_digest: str; superseded_by: UUID | None
class Page(BaseModel):             source_ref: str; body: str; stream: str; title: str
                                   created_at: str | None; updated_at: str | None
class SpendCap(BaseModel):         scope: Literal["workspace", "member", "agent"]; subject_id: UUID | None
                                   dimension: Dimension; cap: Decimal; window: Window
                                   on_breach: Literal["reject", "park"]
Dimension = Literal["usd", "tokens", "tool_calls", "connector_calls", "sandbox_minutes"]
```
Each unit's migration adds only what it wires: U2 grows turn status/queue state for tools, U5
adds `turn.parent_turn_id` + `turn.subagent_profile` and the `subagent` conversation surface, U6
relaxes `conversation.member_id` for shared surfaces, U7 adds ledger attribution + price-digest
audit columns.

## blob.py

Transcripts, compaction records, and shared artifacts — never a conversation's workspace, which
lives in its sandbox. Key layout: `artifacts/<uuid>/<name>` (U5),
`conversations/<cid>/compactions/<n>/{before,after}.json.lz4` (U5). On S3 a shared file lands by a
presigned PUT the sandbox performs, bound to the preflighted size and sha256.

## hub.py (later-unit remainder)

`LiveFrame` grows `ToolNote` (U2) and `CostTick` (U7); lossy by contract — the durable terminal
frame in Postgres stays authoritative.

## models/ (U2 remainder)

```python
class ModelRequest(BaseModel):  ...; tools: tuple[ToolSchema, ...]       # U2
ModelEvent grows ToolCallStart | ToolCallDelta                           # U2
class ModelPolicy(BaseModel):   mode: Literal["auto", "pinned"]; pinned: str | None   # auto routing, later
```
Provider image/content limits (Anthropic image-count trim) live in the provider client (U2+).

## loop/ (later-unit remainder)

`TurnEngine` grows fields as its deps land: `memory: MemoryService` with a `_recall` step (U4),
`spend: SpendEvaluator` per step (U7). Compaction (`loop/compaction.py`: window trigger, before/after
records, live-window swap) and typed subagents (`loop/subagents.py`: profile registry, DBOS child
spawn foreground/background, the `spawn_subagent` builtin) are code-authoritative.

## tools/

```python
@dataclass(frozen=True)
class ToolDef:
    name: str; description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]
class ToolContext(Protocol):    # capability-scoped view a handler gets
    sandbox: SandboxSession; memory: MemoryService; blob: BlobStore
    turn: Turn; agent: AgentRuntime
    speaker_member_id: UUID | None; audience: Audience
    acting_member_id: UUID | None; effective_audience: Audience
    async def ask_user(self, question: Question) -> Answer: ...
    async def spawn(self, profile: str, input: BaseModel, background: bool = False) -> SpawnResult: ...
class ToolResult(BaseModel):    content: tuple[ContentBlock, ...]; is_error: bool = False
```
Builtins: `bash read write edit memory_search memory_update ask_user spawn_subagent load_skill
share_file`. Registry rejects a second registration of an existing name.
Every tool schema also accepts optional `requested_by`: a visible active inbound message ref.
Dispatch strips it before validating the declared input and binds that message's member to the
context; omission is common authority unless the turn carries `on_behalf_of_member_id`.

## sandbox/

```python
class Carrier(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...      # create-or-attach
    async def attach(self, spec: SandboxSpec) -> SandboxHandle | None: ...  # attach-only (reads)
    async def exec(self, h: SandboxHandle, argv: tuple[str, ...], *, timeout_s: int) -> ExecResult: ...
    async def write(self, h: SandboxHandle, path: str, content: bytes) -> None: ...  # copy-in
    async def read(self, h: SandboxHandle, path: str) -> AsyncIterator[bytes]: ...   # copy-out
    async def file_op(self, h: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]: ...
    async def dial(self, h: SandboxHandle, port: int) -> DialTarget: ...  # inbound: host, tls, headers
class SandboxSpec:  conversation_id: UUID; image_ref: str; workspace_host_path: str
                    proxy: ProxyEndpoint; run_token: str; resume_id: str | None
```
`/workspace` is the carrier's own storage and the only copy of a conversation's files; the docker
carrier stops its own idle containers and any later touch restarts one, and nothing reclaims an
off-cluster sandbox — its provider suspends it. Proxy rules (module-private in `sandbox/proxy/`,
no public register API):

```python
def derive_rules(manifests: tuple[Manifest, ...], grants: tuple[Grant, ...],
                 credentials: CredentialStore) -> tuple[Rule, ...]: ...
Rule = InjectionRule(host_pattern, header, sentinel → real)   \
     | ScopeRule(host_pattern, allowed_accounts)              \
     | MeterRule(host_pattern, dimension)
```

## memory/

```python
class MemoryService(Protocol):
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple[Recalled, ...]: ...
    async def search_sources(self, query, subjects, limit) -> tuple[SourceMatch, ...]: ...  # source pages
    async def commit(self, write: MemoryWrite) -> None: ...
class IndexBackend(Protocol):                                  # pgvector core; turbopuffer ext
    async def upsert(self, chunks: tuple[Chunk, ...]) -> None: ...
    async def delete(self, scope: IndexScope) -> None: ...
    async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None: ...
    async def has_chunks(self, scope: IndexScope) -> bool: ...  # skip re-embed when already held
    async def lexical(self, query, subjects, owner_kind, limit) -> tuple[Hit, ...]: ...
    async def vector(self, embedding, subjects, owner_kind, limit) -> tuple[Hit, ...]: ...
class Condenser(Protocol):                                     # the pluggable derivation stage
    async def condense(self, pages: tuple[Page, ...], ctx: ExtensionContext) -> tuple[MemoryWrite, ...]: ...
```
Recall fuses lexical + vector (RRF). Member and room audiences include `shared`; foreign room
audiences include only their sealed subject. Derivation runs as jobs, batch-at-interval, never
inline with a write.

## sources.py

```python
class SourceAuth:               workspace_id: UUID; self_user_id: str | None
class SourceBackend(Protocol):
    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult: ...
class SyncResult(BaseModel):  pages: tuple[Page, ...]; next_cursor: str | None
```
Core ships `folder`; S3/GitHub/connector-API backends are extensions. Config `[[sources]]` blocks
register source rows at boot. The sync driver is a core job — claim (dialect-native lock) → fetch →
store bodies + upsert pages (skip unchanged by digest, tombstone removed) → advance cursor; it
writes no chunks. The page index job derives chunks (owner_kind `page`), and `memory_search` recalls
them via `search_sources`.

## surfaces/ (U6 remainder)

```python
class Surface(Protocol):
    async def to_inbound(self, request: HttpRequest) -> Inbound: ...     # verify, identify, key
    async def writeback(self, conversation: Conversation, terminal: TerminalFrame) -> None: ...
class Inbound(BaseModel):  conversation_key: str; audience: Audience; member_id: UUID | None; body: str
```
The protocol forms when the second surface lands (U6). Slack: signature verify,
`channel:thread_ts` key, member linking via `SurfaceIdentity`, Block Kit writeback. Web: session
identity, hub-tailed streaming. Onboarding engine runs contributed `OnboardingStep`s.

## accounting.py (U7 remainder)

```python
@dataclass(frozen=True)
class SpendEvaluator:
    caps: tuple[SpendCap, ...]; totals: Totals
    def decide(self, request: SpendRequest) -> SpendDecision: ...        # allow | reject | park
```
Evaluated at inbound and per step; wire metering in the sandbox proxy feeds the same ledger;
ledger rows gain attribution (agent/member) and price-digest audit columns with rollups.

## ext/ and sdk/

```python
@dataclass(frozen=True)
class Manifest:
    name: str; version: str
    tools: tuple[ToolDef, ...] = ();            subagents: tuple[SubagentProfile, ...] = ()
    connectors: tuple[ConnectorSpec, ...] = (); sources: tuple[SourceSpec, ...] = ()
    hooks: tuple[HookSpec, ...] = ();           jobs: tuple[JobSpec, ...] = ()
    routes: tuple[RouteSpec, ...] = ();         credentials: tuple[CredentialSlot, ...] = ()
    onboarding: tuple[OnboardingStep, ...] = ();packs: tuple[PackRef, ...] = ()
    models: tuple[ModelSpec, ...] = (); carriers: tuple[CarrierSpec, ...] = ()
    memory_search: tuple[MemorySearchProviderSpec, ...] = ()
    indexes: tuple[IndexBackendSpec, ...] = (); hubs: tuple[HubSpec, ...] = ()
    requires: tuple[str, ...] = ()

class ExtensionContext(Protocol):
    store: ScopedStore                                        # workspace-scoped queries, no raw engine
    credentials: CredentialAccess                             # get(slot) for declared slots only
    async def memory_write(self, write: MemoryWrite) -> None: ...
    async def invoke(self, agent: str, input: str, conversation_id: UUID | None = None) -> TurnRef: ...
    async def schedule(self, job: JobSpec) -> None: ...
    async def trajectories_read(self, query: TrajectoryQuery) -> tuple[TrajectoryRef, ...]: ...
    async def propose_change(self, proposal: AgentChange) -> ProposalRef: ...   # governed; never a direct write
```
Entry point group: `ufo.extension` → `() -> Manifest`. `ufo.sdk` re-exports every name an
extension may touch; a CI gate fails any `extensions/` import outside `ufo.sdk`.

## Invariants (permanent residents of this file)

- `workspace_tx()` is the only session source; the engine is unreachable elsewhere.
- The sandbox proxy is the only egress route; rules derive from manifests + grants; no register API.
- At most one running turn per conversation; every awaited turn ends in a committed terminal frame.
- A live turn absorbs every speaker's inbound FIFO and emits one terminal reply and writeback.
- A conversation persists one exact audience; foreign rooms cannot read workspace-shared subjects.
- A tool reaches only `/workspace`; transcripts and compactions live in a blob store the sandbox
  holds no credential for.
- Sandbox commands carry signed `(workspace, turn, acting member)` authority; descendants inherit
  their launch environment, while unbound commands carry common authority.
- Extensions import `ufo.sdk` only; credentials resolve only for declared slots.
- Derived state (embeddings, summaries, index rows) is produced by jobs, never inline.
- Every model/tool/proxy call meters into the ledger in the turn's terminal commit — one write
  per turn per dimension, idempotent on the turn-keyed ledger id.
- One event loop, never blocked: workflows/steps are `async def`; blocking calls in async code
  fail lint; sync I/O exists only off the loop (CLI startup, migrations, build scripts).
- Roles (surfaces, workers, jobs, proxy) share nothing in memory; cross-role communication is
  queues, blob store, hub, or HTTP — enforced by the import-boundary gate, so a per-role process
  split is a config change.
