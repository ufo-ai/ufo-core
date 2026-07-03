# Core contracts

The hard-to-vary interfaces U1–U10 implement, in Python-signature form, organized by module.
Once a unit lands, its code (with tests) is authoritative and the matching section here is
**deleted** — this file's destiny is a module map plus the invariants code can't express.

Container rules: Pydantic `BaseModel` for wire/persisted records; frozen dataclasses for internal
value objects and workflows; `Protocol` for pluggable seams. All persisted records carry
`workspace_id`, `created_at`, `updated_at` (omitted below).

## Module map

```text
core/src/selfhost/
  config.py      Config — selfhost.toml, fail-loud            blob.py     BlobStore + fs/S3
  db.py          engine + workspace_tx() boundary             hub.py      Hub + in-process
  schema/        SQL migrations (single source)               o11y.py     OTel facade
  models/        ModelClient + anthropic/openai               jobs.py     JobSpec + runner
  loop/          queue (DBOS), engine, transcript, compaction, subagents
  tools/         registry, context, builtins/
  sandbox/       carrier (+docker), image/, proxy/ (rewriters, sentinel swap, metering)
  memory/        service (store+recall), index (pgvector), pipeline (condensers), embed
  sources.py     SourceBackend + folder + sync driver         grants.py   grant flow
  credentials.py encrypted BYOK store                         accounting.py ledger, caps, prices
  surfaces/      slack, web, cli, onboarding                  ext/        loader, rule derivation
  sdk/           the ONLY public import surface for extensions
  serve.py       single-process assembly                      cli.py      `selfhost` entrypoint
```

## Records (`schema/`, Pydantic)

```python
class Workspace(BaseModel):        id: UUID; name: str; config_digest: str
class Member(BaseModel):           id: UUID; email: str; role: Literal["owner", "member"]
class SurfaceIdentity(BaseModel):  member_id: UUID; surface: Surface; external_id: str   # slack user / cli token / web session
class Agent(BaseModel):            id: UUID; name: str; prompt: str; model_policy: ModelPolicy
                                   tool_names: tuple[str, ...]; pack_names: tuple[str, ...]
class Grant(BaseModel):            id: UUID; agent_id: UUID; kind: Literal["connector", "credential", "tool_group"]
                                   ref: str; account_id: str | None
                                   granted_by: UUID; conversation_id: UUID | None          # the granting act, audited
class Credential(BaseModel):       slot: str; ciphertext: bytes                            # encrypted at rest, values never logged
class Conversation(BaseModel):     id: UUID; surface: Surface; queue_key: str
                                   member_id: UUID | None                                  # None = shared (Slack channel)
class Turn(BaseModel):             id: UUID; conversation_id: UUID; agent_id: UUID; parent_turn_id: UUID | None
                                   status: Literal["queued", "running", "done", "failed", "cancelled", "parked"]
                                   terminal: TerminalFrame | None
class TurnStep(BaseModel):         turn_id: UUID; seq: int; kind: Literal["model", "tool"]; usage: Usage | None
class MemoryItem(BaseModel):       id: UUID; subject: str; body: str; item_class: Literal["fact", "episodic", "semantic"]
                                   embedding_digest: str; superseded_by: UUID | None
class Page(BaseModel):             id: UUID; source_ref: str; digest: str; body_ref: BlobKey; meta: PageMeta
class LedgerEntry(BaseModel):      turn_id: UUID | None; dimension: Dimension; amount: Decimal; priced_usd: Decimal
                                   agent_id: UUID | None; member_id: UUID | None
class SpendCap(BaseModel):         scope: Literal["workspace", "member", "agent"]; subject_id: UUID | None
                                   dimension: Dimension; cap: Decimal; window: Window
                                   on_breach: Literal["reject", "park"]
Dimension = Literal["usd", "tokens", "tool_calls", "connector_calls", "sandbox_minutes"]
```

## db.py — the tenancy boundary

```python
@asynccontextmanager
async def workspace_tx() -> AsyncIterator[AsyncSession]: ...
```
The engine is module-private. `workspace_tx` is the only way to obtain a session; it is already
scoped to the deploy's workspace. A CI gate forbids engine/`begin()` outside this module.

## blob.py

```python
class BlobStore(Protocol):
    async def put(self, key: BlobKey, data: bytes) -> None: ...
    async def get(self, key: BlobKey) -> bytes: ...
    async def exists(self, key: BlobKey) -> bool: ...
    def workspace_mount(self, conversation_id: UUID) -> MountSpec: ...   # reaches ONLY .../workspace/
```
Key layout: `conversations/<cid>/messages.json.lz4`, `conversations/<cid>/compactions/<n>/{before,after}.json.lz4`,
`conversations/<cid>/workspace/**`, `artifacts/<digest>`. Impls: `FilesystemBlobStore` (default,
`MountSpec` = bind mount), `S3BlobStore` (deploys, `MountSpec` = sandbox-fs cred scoped to the
`workspace/` prefix).

## hub.py

```python
class Hub(Protocol):
    async def publish(self, turn_id: UUID, frame: LiveFrame) -> None: ...
    def subscribe(self, turn_id: UUID) -> AsyncIterator[LiveFrame]: ...
LiveFrame = TextDelta | ToolNote | CostTick | Terminal
```
Lossy by contract; the durable terminal frame in Postgres is authoritative.

## models/

```python
class ModelClient(Protocol):
    def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
class ModelRequest(BaseModel):  model: str; system: str; messages: tuple[Message, ...]
                                tools: tuple[ToolSchema, ...]; max_tokens: int
ModelEvent = TextDelta | ToolCallStart | ToolCallDelta | UsageReport | Stop
class ModelPolicy(BaseModel):   mode: Literal["auto", "pinned"]; pinned: str | None
```
Core registers `anthropic`, `openai`; extension `models` add providers. Provider image/content
limits (Anthropic image-count trim) live in the provider client.

## loop/

```python
@dataclass(frozen=True)
class TurnEngine:                       # ONE workflow; steps read top-to-bottom
    turn: Turn; agent: AgentRuntime    # frozen per-turn config: prompt, tools, model, grants
    model: ModelClient; tools: ToolRegistry; transcript: Transcript
    memory: MemoryService; hub: Hub; spend: SpendEvaluator; sandbox: SandboxSession
    async def run(self) -> TerminalFrame: ...
    # _recall → _load_transcript → _maybe_compact → _model_rounds (tool calls, per-step spend) → _terminal
```
DBOS: `turn_workflow` durable, queue partitioned by `conversation_id` (max one running turn per
conversation), subagent = child workflow, cancel = DBOS cancel + terminal commit. One `try`
encloses `run`; the `except` commits the terminal frame — a client's wait always ends.

`Transcript`: append `Message` with monotonic `seq` to `messages.json.lz4`; `compact()` writes
`before/after` records and swaps the live window.

```python
@dataclass(frozen=True)
class SubagentProfile:
    name: str; prompt: str; tool_names: tuple[str, ...]
    input_model: type[BaseModel]; output_model: type[BaseModel]
```

## tools/

```python
@dataclass(frozen=True)
class ToolDef:
    name: str; description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]
class ToolContext(Protocol):    # capability-scoped view a handler gets
    sandbox: SandboxSession; memory: MemoryService; blob: BlobStore
    turn: Turn; agent: AgentRuntime; member_id: UUID | None
    async def ask_user(self, question: Question) -> Answer: ...
    async def spawn(self, profile: str, input: BaseModel, background: bool = False) -> SpawnResult: ...
class ToolResult(BaseModel):    content: tuple[ContentBlock, ...]; is_error: bool = False
```
Builtins: `bash read write edit memory_search memory_update ask_user spawn_subagent load_skill
share_file`. Registry rejects a second registration of an existing name.

## sandbox/

```python
class Carrier(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxHandle: ...      # create-or-attach
    async def exec(self, h: SandboxHandle, argv: tuple[str, ...], *, stdin: bytes, timeout_s: int) -> ExecResult: ...
    async def route(self, h: SandboxHandle, port: int) -> str: ...       # serving URL
    async def destroy(self, h: SandboxHandle) -> None: ...
class SandboxSpec(BaseModel):  conversation_id: UUID; image_digest: str; mount: MountSpec; proxy: ProxyEndpoint
```
Docker implements in core; a reaper job reclaims idle containers. Proxy rules (module-private in
`sandbox/proxy/`, no public register API):

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
    async def commit(self, write: MemoryWrite) -> None: ...
class IndexBackend(Protocol):                                  # pgvector core; turbopuffer ext
    async def upsert(self, chunks: tuple[Chunk, ...]) -> None: ...
    async def lexical(self, query: str, limit: int) -> tuple[Hit, ...]: ...
    async def vector(self, embedding: tuple[float, ...], limit: int) -> tuple[Hit, ...]: ...
    async def reindex(self, scope: ReindexScope) -> None: ...
class Condenser(Protocol):                                     # the pluggable derivation stage
    async def condense(self, pages: tuple[Page, ...], ctx: ExtensionContext) -> tuple[MemoryWrite, ...]: ...
```
Recall fuses lexical + vector (RRF), filters `subjects ⊆ {member:<id>, "shared"}`. Derivation runs
as jobs, batch-at-interval, never inline with a write.

## sources.py

```python
class SourceBackend(Protocol):
    async def sync(self, config: SourceConfig, cursor: str | None) -> SyncResult: ...
class SyncResult(BaseModel):  pages: tuple[Page, ...]; next_cursor: str | None
```
Core ships `folder`; S3/GitHub/connector-API backends are extensions. The sync driver is a core
job: claim → sync → commit pages + due-mark for derivation.

## surfaces/

```python
class Surface(Protocol):
    async def to_inbound(self, request: HttpRequest) -> Inbound: ...     # verify, identify, key
    async def writeback(self, conversation: Conversation, terminal: TerminalFrame) -> None: ...
class Inbound(BaseModel):  conversation_key: str; member_id: UUID | None; agent_name: str; body: str
```
Slack: signature verify, `channel:thread_ts` key, member linking via `SurfaceIdentity`, Block Kit
writeback. CLI/web: session key, token/session identity, hub-tailed streaming. Onboarding engine
runs contributed `OnboardingStep`s; first run creates workspace + first `owner`.

## accounting.py

```python
@dataclass(frozen=True)
class SpendEvaluator:
    caps: tuple[SpendCap, ...]; totals: Totals
    def decide(self, request: SpendRequest) -> SpendDecision: ...        # allow | reject | park
```
Ledger writes commit with the step that incurred them. Prices: pinned per-model table. Evaluated
at inbound and per step; wire metering in the sandbox proxy feeds the same ledger.

## ext/ and sdk/

```python
@dataclass(frozen=True)
class Manifest:
    name: str; version: str
    tools: tuple[ToolDef, ...] = ();            subagents: tuple[SubagentProfile, ...] = ()
    connectors: tuple[ConnectorSpec, ...] = (); sources: tuple[SourceSpec, ...] = ()
    triggers: tuple[TriggerSpec, ...] = ();     jobs: tuple[JobSpec, ...] = ()
    routes: tuple[RouteSpec, ...] = ();         credentials: tuple[CredentialSlot, ...] = ()
    onboarding: tuple[OnboardingStep, ...] = ();packs: tuple[PackRef, ...] = ()
    models: tuple[ModelProviderSpec, ...] = (); carriers: tuple[CarrierSpec, ...] = ()
    memory: tuple[Condenser, ...] = ();         indexes: tuple[IndexBackendSpec, ...] = ()
    hubs: tuple[HubSpec, ...] = ()

class ExtensionContext(Protocol):
    store: ScopedStore                                        # workspace-scoped queries, no raw engine
    credentials: CredentialAccess                             # get(slot) for declared slots only
    async def memory_write(self, write: MemoryWrite) -> None: ...
    async def invoke(self, agent: str, input: str, conversation_id: UUID | None = None) -> TurnRef: ...
    async def schedule(self, job: JobSpec) -> None: ...
    async def trajectories_read(self, query: TrajectoryQuery) -> tuple[TrajectoryRef, ...]: ...
    async def propose_change(self, proposal: AgentChange) -> ProposalRef: ...   # governed; never a direct write
```
Entry point group: `selfhost.extension` → `() -> Manifest`. `selfhost.sdk` re-exports every name an
extension may touch; a CI gate fails any `extensions/` import outside `selfhost.sdk`.

## Invariants (permanent residents of this file)

- `workspace_tx()` is the only session source; the engine is unreachable elsewhere.
- The sandbox proxy is the only egress route; rules derive from manifests + grants; no register API.
- At most one running turn per conversation; every awaited turn ends in a committed terminal frame.
- The sandbox reaches only `conversations/<cid>/workspace/`; the container is disposable cache.
- Extensions import `selfhost.sdk` only; credentials resolve only for declared slots.
- Derived state (embeddings, summaries, index rows) is produced by jobs, never inline.
- Every model/tool/proxy call meters into the ledger in the same commit as its step.
