# Core runtime extension and shared record contracts  `stage-19.1`

This stage is shared behind-the-scenes support. It does not run the app by itself. Instead, it defines the common rules that other parts rely on when extensions, conversations, and background work need to talk to each other.

The ext package marker is the signpost. It says this folder is where the runtime extension API lives: the public set of shapes and promises that add-ons and built-in features must follow. The context file defines the safe toolbox given to an extension or background job. It lets that code do approved things, such as read its settings, call a model, open a conversation, or read synced pages, without giving it full access to private data or other workspaces.

The conversation slots file defines the small conversation panels an extension can fill, such as artifacts, sources, tasks, sites, automations, and workspace changes. The object name file gives one shared way to name and reference things. The records file defines the standard “work ticket” for a turn, so the user interface, storage, queues, agents, and runtime all understand the same facts.

## Files in this stage

### Extension runtime surface
Public extension package entry points define the safe runtime context and conversation slot interfaces available to extensions and built-in features.

### `core/src/ufo/runtime/ext/__init__.py`

`other` · `cross-cutting`

This file is intentionally small: it contains only a short package-level note. Its job is to label this part of the codebase as the boundary between the main platform and extension code. In plain terms, it is like a sign over a doorway saying, “Extensions enter here.”

The note says this package covers two related ideas. First, it describes what the platform offers to extensions and built-in code: the services, hooks, or shared tools that outside pieces are allowed to rely on. Second, it describes the schema of what those pieces contribute. A schema is a formal shape or contract for data, like saying a form must have a name field, a version field, and a list of commands.

There is no executable logic here, so nothing runs directly from this file. Its value is organizational and documentary. Without it, the package would still possibly work in modern Python, but readers would lose a clear clue about why this folder exists and what kind of code belongs inside it.


### `core/src/ufo/runtime/ext/context.py`

`orchestration` · `cross-cutting`

Extensions and background jobs need to do real work inside a workspace, but they should not get the keys to the whole system. This file is the boundary object for that. It builds an ExtensionContext, which is like a hotel keycard: it opens only the rooms the handler is meant to enter. The context exposes a small store for the extension’s own saved values, guarded access to declared credential slots, read-only access to conversation transcripts, controlled writes into a conversation sandbox, metered model calls, scheduled-run reads, source registration, member-visible context, and governed agent prompt proposals. Most methods quietly enforce the current workspace by reading the ambient workspace scope, so callers do not pass a workspace id around and cannot accidentally ask for another tenant’s data. The file also contains helper queries that tell background dispatchers which workspaces have work to run, such as conversations needing titles or sources with connected accounts. A major theme is “do the useful thing, but through a narrow door”: model calls are spend-checked and billed, source reads respect audience and grants, credential reads require manifest declaration, and raw transactions are documented as dangerous. Without this file, extension code would either be powerless or would need direct access to core internals, which would make tenant isolation, billing, and permission checks much harder to trust.

#### Function details

##### `spend_refusal_notice_key`  (lines 111–117)

```
def spend_refusal_notice_key(model: str) -> str
```

**Purpose**: Builds the saved-store key used to remember that off-turn model spending was refused for one specific model. It keeps refusal notices separate per model so one allowed model does not erase the warning state for another held model.

**Data flow**: It takes a model name, prefixes it with the fixed refusal-notice label, and returns the combined string. It does not read or change storage itself.

**Call relations**: ModelAccess.turn uses this when a later successful model call clears the old refusal mark for that model.

*Call graph*: called by 1 (turn).


##### `ScopedStore.workspace_id`  (lines 132–133)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently bound to the running job or request. This lets the store operate in the right tenant without callers passing a workspace id manually.

**Data flow**: It reads the ambient workspace scope and returns its workspace id. Nothing is written.

**Call relations**: All ScopedStore methods rely on this property before reading or writing extension-owned keys.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 135–146)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved JSON value from this extension’s private key space in the current workspace. Extensions use it for small durable state, such as remembered conversation or reply checkpoints.

**Data flow**: It receives a key, opens a workspace-scoped database transaction, looks for a matching workspace, extension, and key row, and returns the value or None if missing.

**Call relations**: Browser, Slack, and web surface code call this when they need previously saved extension state.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_web_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 148–163)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several specific saved values in one database query. This avoids scanning all extension state when the caller already knows the keys it wants.

**Data flow**: It receives a list of keys, returns an empty mapping if the list is empty, otherwise fetches matching rows for the current workspace and extension and returns a key-to-value dictionary.

**Call relations**: It is the batched sibling of ScopedStore.get and supports handlers that need multiple known settings at once.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 165–189)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Writes or replaces one JSON value in the extension’s private store. It uses an atomic upsert, meaning “insert if absent, update if present,” so two writers do not trip over each other creating the same key.

**Data flow**: It receives a key and value, opens a workspace transaction, and writes the row for the current workspace and extension with updated timestamps. It returns nothing.

**Call relations**: Browser, Browserbase, Slack, and web surfaces call this to persist small pieces of extension state.

*Call graph*: called by 4 (_start, _context, _hold_connect_message, _open_conversation); 1 external calls (workspace_tx).


##### `ScopedStore.put_if`  (lines 191–241)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Updates a stored value only if it still has the value the caller expected. This is a safety check against overwriting someone else’s newer change with stale information.

**Data flow**: It receives a key, new value, and expected old value. If expected is None, it inserts only if the key is absent; otherwise it locks the row, compares the current value, updates on a match, and returns true or false.

**Call relations**: Slack reply code uses this for checkpoints where competing progress updates must not clobber each other.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 243–251)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes one key from this extension’s private store in the current workspace. It is used when stored state is no longer valid.

**Data flow**: It receives a key, opens a workspace transaction, deletes the matching row for the current workspace and extension, and returns nothing.

**Call relations**: Slack cleanup, web conversation opening, and ModelAccess.turn use deletion to forget stale extension or refusal state.

*Call graph*: called by 2 (_drop_turn_reply_records, _open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 253–266)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists this extension’s saved key-value pairs, optionally limited by a prefix. It gives extensions a controlled way to enumerate only their own namespace.

**Data flow**: It receives an optional prefix, fetches matching rows for the current workspace and extension ordered by key, and returns a tuple of key-value pairs.

**Call relations**: Slack and web extension code use this when cleaning stored records or finding granted audience information.

*Call graph*: called by 3 (_drop_turn_reply_records, _granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 279–280)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose credentials this access object will read. It anchors secret lookups to the currently bound workspace.

**Data flow**: It reads the ambient workspace scope and returns the workspace id. It does not expose any secret.

**Call relations**: CredentialAccess methods use the same ambient workspace rule as the rest of the context.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 282–288)

```
async def get(self, slot: str) -> str
```

**Purpose**: Reads a declared credential slot for the current workspace. A credential slot is a named secret location, and the extension must have declared it before it can be read.

**Data flow**: It receives a slot name, rejects it if undeclared, then asks the current workspace for the live credential value. It returns the secret string or raises if unavailable.

**Call relations**: Slack uses this to read its verification secret, and the declaration check prevents extensions from probing arbitrary secrets.

*Call graph*: called by 1 (verifying_fingerprint); 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 290–297)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Tells whether a credential slot is supplied by the workspace itself instead of the platform default. This matters for billing and ownership of provider usage.

**Data flow**: It receives a slot name, verifies the slot was declared, then asks the current workspace whether that credential is stored there. It returns true or false.

**Call relations**: Handlers can use this before provider calls to understand whether the workspace’s own key is being used.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.rotate`  (lines 299–304)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if its current value matches an expected value. This supports safe rotation after an external provider changes a secret.

**Data flow**: It receives a slot, expected old plaintext, and new plaintext. After checking declaration, it asks the current workspace to rotate by compare-and-swap and returns whether it succeeded.

**Call relations**: It is the write-side companion to CredentialAccess.get, still fenced by declared credential slots.

*Call graph*: 2 external calls (__init__, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 337–338)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose transcripts this corpus can read. It keeps trajectory reads tied to the current workspace.

**Data flow**: It reads the ambient workspace scope and returns the workspace id.

**Call relations**: TrajectoryCorpus.trajectories and conversations use this before choosing which conversations to read.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 340–347)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for the current workspace. This gives evaluation or learning jobs a safe read-only corpus.

**Data flow**: It builds a database query for the most recent conversations in this workspace, then passes that selection to _read. It returns decoded Trajectory objects.

**Call relations**: ExtensionContext.trajectories reaches this method when a handler asks for its corpus.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus.conversations`  (lines 349–361)

```
async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]
```

**Purpose**: Reads transcripts for exactly the named conversations, still limited to the current workspace. It is useful when a job already knows which conversations it wants.

**Data flow**: It receives conversation ids, builds a workspace-scoped selection for those ids, and delegates transcript loading to _read. Conversations from other workspaces simply do not appear.

**Call relations**: It shares the actual transcript-loading path with trajectories through TrajectoryCorpus._read.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus._read`  (lines 363–407)

```
async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]
```

**Purpose**: Loads transcript blobs and turns them into Trajectory records. It skips missing or corrupt transcripts instead of failing the whole corpus read.

**Data flow**: It receives a database subquery selecting conversation ids, fetches conversation-agent-prompt rows, reads each transcript blob, decodes messages, computes the prompt digest, and returns a tuple of trajectories.

**Call relations**: Both corpus-wide and specific-conversation reads call this helper so transcript decoding and error handling stay consistent.

*Call graph*: called by 2 (conversations, trajectories); 7 external calls (__init__, select, workspace_tx, log, prompt_digest, decode, transcript_key).


##### `ConversationFiles.write`  (lines 426–429)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s workspace directory, where the agent can see them later. It is a controlled off-turn file drop, not general file-system access.

**Data flow**: It receives a conversation id, relative path, and bytes, passes them to the sandbox service, and returns the visible /workspace path.

**Call relations**: Handlers use this through ExtensionContext.files when they need to prepare files for a later agent turn.


##### `ConversationFiles.prune`  (lines 431–437)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a path prefix, keeping only a fixed number of newest names. This prevents unattended jobs from filling a conversation workspace forever.

**Data flow**: It receives a conversation id, prefix, and keep count, then asks the sandbox service to prune matching files. It returns nothing.

**Call relations**: It pairs with ConversationFiles.write for append-style off-turn file writers.


##### `ConversationFiles.write_runtime`  (lines 439–443)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output under a named category inside a conversation sandbox. This keeps system-generated files separate from ordinary workspace files.

**Data flow**: It receives a conversation id, category, relative path, and bytes, then delegates the write to the sandbox service and returns the resulting path.

**Call relations**: It is exposed through the context when core or extensions need categorized runtime artifacts.


##### `ConversationFiles.prune_runtime`  (lines 445–453)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Prunes old internal runtime files in a conversation sandbox. This bounds storage growth for system-generated categories.

**Data flow**: It receives a conversation id, category, prefix, and keep count, then asks the sandbox service to delete older matching files.

**Call relations**: It is the cleanup companion to ConversationFiles.write_runtime.


##### `conversation_agent_id`  (lines 456–469)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the agent permanently attached to a conversation in a workspace. It returns None if the conversation id does not belong to that workspace.

**Data flow**: It receives a workspace id and conversation id, queries the conversation table, and returns the agent id or None.

**Call relations**: ConversationProbes.run uses it before opening a sandbox, and ExtensionContext.conversation_agent exposes it to handlers.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 508–562)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, *, authority: ExecutionAuthority) -> ExecResult
```

**Purpose**: Runs a short bash command inside a conversation’s sandbox while no turn is active. It is a tightly limited probe for checking files or environment state, not an open-ended background process.

**Data flow**: It receives a conversation id, command, timeout, and execution authority. It checks the timeout, confirms the conversation and authority are valid, creates a short-lived probe token, opens the sandbox under the conversation’s agent scope, runs the command, and returns stdout, stderr, and exit code.

**Call relations**: It calls conversation_agent_id and the sandbox/token machinery; callers reach it only when ConversationProbes has been wired into the context.

*Call graph*: calls 1 internal fn (conversation_agent_id); 8 external calls (__init__, __init__, __init__, now, workspace_tx, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 565–587)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Defines which workspaces should be considered for jobs that read conversation trajectories. Only workspaces with at least one turned conversation are candidates.

**Data flow**: It builds a candidate provider around a SQL query factory and returns that provider.

**Call relations**: Background dispatch uses this seam so extensions do not need direct owner-level database access to discover eligible workspaces.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 573–585)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query for trajectory candidates. It selects workspaces that have a conversation with at least one turn.

**Data flow**: It reads no rows itself; it returns a selectable database expression for the dispatcher to run.

**Call relations**: trajectory_workspaces wraps this query with owner_candidates.

*Call graph*: 2 external calls (exists, select).


##### `seated_member_workspaces`  (lines 590–600)

```
def seated_member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Defines candidate workspaces for jobs that need at least one active seated member. A seated member is a member currently occupying a paid or allowed seat.

**Data flow**: It builds and returns a candidate provider based on a query factory.

**Call relations**: First-party member jobs can declare this so they run only where a real member is present.

*Call graph*: 1 external calls (owner_candidates).


##### `seated_member_workspaces.with_a_seated_member`  (lines 593–598)

```
def with_a_seated_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query selecting distinct workspaces with at least one seated member.

**Data flow**: It returns a SQL select expression over the member table. The actual execution happens elsewhere.

**Call relations**: seated_member_workspaces passes this query factory into owner_candidates.

*Call graph*: 1 external calls (select).


##### `connection_workspaces`  (lines 603–616)

```
def connection_workspaces() -> WorkspaceCandidates
```

**Purpose**: Defines candidate workspaces where the main agent has a connector grant. This avoids firing connection-driven jobs where there is no connected account to work with.

**Data flow**: It wraps a SQL query factory in a workspace-candidate object and returns it.

**Call relations**: Connection-driven background handlers use this candidate seam during dispatch.

*Call graph*: 1 external calls (owner_candidates).


##### `connection_workspaces.with_a_main_agent_connection`  (lines 608–614)

```
def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query selecting workspaces whose main agent has at least one connector grant.

**Data flow**: It returns a distinct workspace-id select expression joining connector grants to agents.

**Call relations**: connection_workspaces hands this query to owner_candidates.

*Call graph*: 1 external calls (select).


##### `agent_is_live`  (lines 619–632)

```
def agent_is_live(workspace_id: sa.ColumnElement[UUID], agent_id: sa.ColumnElement[UUID]) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition that is true only for an unarchived agent in its workspace. Jobs use this before doing work that should not run for archived agents.

**Data flow**: It receives SQL column expressions for workspace id and agent id, and returns an EXISTS predicate checking for a matching non-archived agent row.

**Call relations**: It is a reusable filter for sweeps that need to avoid spending effort before a later invoke refusal.

*Call graph*: 2 external calls (exists, select).


##### `awaiting_a_title`  (lines 638–654)

```
def awaiting_a_title() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the condition for conversations that still need an automatic title. A conversation qualifies only after a member spoke and an answer finished.

**Data flow**: It returns a SQL boolean expression checking that title_summarized is false and that a completed member-admitted turn exists.

**Call relations**: Both the workspace candidate query and ExtensionContext.conversations_awaiting_title use this same condition.

*Call graph*: called by 2 (conversations_awaiting_title, with_an_unsummarized_title); 3 external calls (and_, exists, select).


##### `untitled_conversation_workspaces`  (lines 657–666)

```
def untitled_conversation_workspaces() -> WorkspaceCandidates
```

**Purpose**: Defines candidate workspaces for the conversation-title summarizer job. It runs only where at least one member-facing answered conversation has not been summarized.

**Data flow**: It wraps a query factory and returns a workspace-candidate provider.

**Call relations**: The titling background job declares this seam so it does not scan every workspace.

*Call graph*: 1 external calls (owner_candidates).


##### `untitled_conversation_workspaces.with_an_unsummarized_title`  (lines 663–664)

```
def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query selecting workspaces with conversations that satisfy awaiting_a_title.

**Data flow**: It returns a distinct workspace-id select expression. It does not execute the query itself.

**Call relations**: untitled_conversation_workspaces passes it into owner_candidates.

*Call graph*: calls 1 internal fn (awaiting_a_title); 1 external calls (select).


##### `unseeded_agent_workspaces`  (lines 669–698)

```
def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Defines candidate workspaces for once-per-agent sweep jobs. It finds workspaces where the extension has not yet written one settlement key per agent.

**Data flow**: It receives an extension name and key prefix, builds a query factory that compares agent count to matching store-key count, and returns a candidate provider.

**Call relations**: Sweep jobs use this to keep running until every agent row has been marked as processed.

*Call graph*: 1 external calls (owner_candidates).


##### `unseeded_agent_workspaces.with_an_unsettled_agent`  (lines 681–696)

```
def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that compares how many agents a workspace has with how many matching extension-store keys exist.

**Data flow**: It returns a select expression for workspaces where agent count is greater than settled-key count.

**Call relations**: unseeded_agent_workspaces wraps this query for the dispatcher.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 731–745)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, authority: ExecutionAuthority, holds_work_already_done: bool=False, as_scheduled: bool=False, stand
```

**Purpose**: Protocol method describing how a background handler asks core to admit an internal turn. A protocol is a shape that another object promises to implement.

**Data flow**: An implementation receives conversation, agent, message, idempotency, authority, and admission options, then returns the admitted turn id or None if intentionally refused.

**Call relations**: ExtensionContext.invoke calls this when an invoker has been wired.


##### `TurnInvoker.redispatch`  (lines 747–747)

```
async def redispatch(self, conversation_id: UUID, ended_turn_id: UUID) -> UUID | None
```

**Purpose**: Protocol method describing how core re-admits pending work after a turn ends. It gives the turn system a way to continue queued arrivals.

**Data flow**: An implementation receives a conversation id and ended turn id, then returns a new turn id if redispatch admitted one.

**Call relations**: The protocol advertises this ability to code that receives a TurnInvoker, even though this file does not call it directly.


##### `TurnInvoker.member_reach`  (lines 749–749)

```
async def member_reach(self, member_id: UUID, limit: int) -> tuple[MemberReach, ...]
```

**Purpose**: Protocol method for finding durable conversations where a member can be reached. This supports background work that needs to report to a member outside the current conversation.

**Data flow**: An implementation receives a member id and limit, then returns MemberReach records describing recent reachable conversations.

**Call relations**: ExtensionContext.member_reach delegates to this method when member-context reading is allowed.


##### `ModelResolver.auto_model`  (lines 759–759)

```
def auto_model(self) -> str
```

**Purpose**: Protocol property naming the default model used by background model access. It fixes which model is called and billed through this seam.

**Data flow**: An implementation returns a model id string.

**Call relations**: ModelAccess.model and ModelAccess.turn read it before making a model request.


##### `ModelResolver.pricing`  (lines 762–762)

```
def pricing(self) -> Pricing
```

**Purpose**: Protocol property exposing the price table for model usage. The billing code needs this to turn token usage into cost.

**Data flow**: An implementation returns a Pricing object.

**Call relations**: ModelAccess.turn passes it to the billable event when usage events arrive.


##### `ModelResolver.client_for`  (lines 764–764)

```
async def client_for(self, model: str) -> ResolvedModelClient
```

**Purpose**: Protocol method for getting the model client for a specific model in the current workspace. The client may use a workspace-provided key or a platform key.

**Data flow**: An implementation receives a model id and returns a resolved client ready to stream completions.

**Call relations**: ModelAccess.turn calls this after spend gates allow the request.


##### `ModelResolver.key_slot_for`  (lines 766–766)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Protocol method mapping a model to the credential slot that supplies its key, if any. This helps spend and balance checks know what funding path applies.

**Data flow**: An implementation receives a model id and returns a slot name or None.

**Call relations**: ModelAccess.turn passes this into BalanceGate, and context_for exposes it for usage export logic.


##### `ModelResolver.provider_for`  (lines 768–768)

```
def provider_for(self, model: str) -> str
```

**Purpose**: Protocol method naming the provider behind a model, such as the service that serves it. Metrics use this label to group latency and token usage.

**Data flow**: An implementation receives a model id and returns a provider string.

**Call relations**: ModelAccess.turn includes this provider in emitted model metrics.


##### `ModelAccess.model`  (lines 797–799)

```
def model(self) -> str
```

**Purpose**: Returns the default model this ModelAccess object will call. It makes the model choice visible without letting handlers choose a different billing path.

**Data flow**: It reads auto_model from the resolver and returns it.

**Call relations**: Handlers can inspect this property before calling complete or turn.


##### `ModelAccess.complete`  (lines 801–808)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one background model completion and returns only the final text. It is the simple interface for jobs that do not need tool calls or structured assistant blocks.

**Data flow**: It receives a ModelRequest, delegates to turn, then extracts text from the returned assistant message. It returns a string.

**Call relations**: Memory extension summarizers call this; it relies on ModelAccess.turn for spend checks, streaming, billing, and metrics.

*Call graph*: calls 1 internal fn (turn); called by 3 (_summarize, _write, _summarize).


##### `ModelAccess.turn`  (lines 810–935)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs a metered background model call and returns the assistant message, including text, reasoning blocks, and tool calls when present. It enforces off-turn spending rules before any provider call.

**Data flow**: It receives a ModelRequest, checks balance and spend limits, clears any prior refusal mark for this model when allowed, resolves a client, streams model events, records usage for billing, emits latency and token metrics, assembles tool-call JSON, and returns a Message.

**Call relations**: ModelAccess.complete and memory extension code call this. It uses spend_refusal_notice_key, billing gates, the resolver, and the workspace billable event to keep model calls attributed and controlled.

*Call graph*: calls 2 internal fn (__init__, spend_refusal_notice_key); called by 3 (complete, _curate, _write); 14 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, model_copy, loads, monotonic (+4 more)).


##### `_source_readable`  (lines 999–1034)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database rule for which synced sources an agent/member reader may read. It combines workspace, subject, source grants, main-agent shared access, and member-private access.

**Data flow**: It receives a workspace id and SourceReader, then returns a SQL boolean expression over source rows. It does not fetch data itself.

**Call relations**: ExtensionContext.readable_page_states, readable_source_ids, and source_pages use this shared rule so source visibility stays consistent.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `MemberContextRecord._aware_utc`  (lines 1091–1092)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures member-context timestamps always have timezone information. If a timestamp is naive, meaning it lacks a timezone, it is treated as UTC.

**Data flow**: It receives a datetime and returns it unchanged if already timezone-aware, or with UTC attached if not.

**Call relations**: Pydantic calls this validator while creating MemberContextRecord objects.

*Call graph*: 1 external calls (replace).


##### `_member_blob_text`  (lines 1098–1116)

```
async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str
```

**Purpose**: Reads a small text preview from a blob without loading an unbounded file. It protects member context building from huge files and from cutting a multibyte character in half.

**Data flow**: It receives a blob store and key, streams bytes until a fixed limit plus one byte, closes the stream if needed, decodes the bounded data, and returns text. It may raise if the blob is missing or not valid text.

**Call relations**: ExtensionContext.member_context uses it for text artifacts and synced page bodies.

*Call graph*: calls 1 internal fn (get_stream); called by 1 (member_context).


##### `ExtensionContext.workspace_id`  (lines 1145–1146)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the current workspace id through the context’s scoped store. It gives handlers a convenient way to know which workspace they are operating in.

**Data flow**: It reads store.workspace_id and returns that UUID.

**Call relations**: Many ExtensionContext methods use this property when delegating to core helpers.


##### `ExtensionContext.image_preview_url`  (lines 1148–1162)

```
def image_preview_url(self, blob_key: str, size_bytes: int) -> str | None
```

**Purpose**: Creates a signed preview link for an image blob owned by this workspace. It returns None if previews are not configured or the image is not eligible.

**Data flow**: It receives a blob key and byte size, combines them with the context’s public URL, signing secret, and workspace id, and returns a URL or None.

**Call relations**: The sites extension calls it when presenting image previews without receiving the signing secret itself.

*Call graph*: called by 1 (_preview_url); 1 external calls (mint_image_preview_url).


##### `ExtensionContext.artifact_link`  (lines 1164–1170)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed download link for a shared artifact. This lets extensions show files to members without owning the artifact-link secret.

**Data flow**: It receives a SharedArtifact and passes the secret, base URL, workspace id, and artifact to the surface helper. It returns a URL or None.

**Call relations**: Report digest object rendering uses this for downloadable shared files.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_link).


##### `ExtensionContext.artifact_preview_link`  (lines 1172–1177)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview link for a shared artifact when its media type and size allow it. It is mainly for image-like artifacts.

**Data flow**: It receives a SharedArtifact, delegates to the surface preview helper with signing information, and returns a URL or None.

**Call relations**: Report digest rows call this beside artifact_link to show previews where possible.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_preview_link).


##### `ExtensionContext.scheduled_runs`  (lines 1179–1203)

```
async def scheduled_runs(self, member_id: UUID, *, agent_id: UUID | None, limit: int, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Reads recent scheduled turns visible to a member. This powers member-facing listings of automatic work that has fired and finished.

**Data flow**: It receives member, optional agent or turn filters, limit, and optional subjects, then delegates to the surface scheduled-run reader with the current workspace id.

**Call relations**: The report digest extension uses it to build pages and detail rows for scheduled runs.

*Call graph*: called by 2 (_one, _page); 1 external calls (scheduled_runs).


##### `ExtensionContext.home_url`  (lines 1205–1216)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link to the deploy’s browser home surface. It returns None if the deployment has no public base URL or no configured home surface.

**Data flow**: It receives an optional URL fragment, trims the base URL, combines it with the surface path and fragment, and returns the string.

**Call relations**: Metronome and sample extensions use this when they need to send a member back to the web portal.

*Call graph*: called by 2 (_billing_portal, _hook).


##### `ExtensionContext.workspace_agents`  (lines 1218–1250)

```
async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]
```

**Purpose**: Returns the workspace’s agent roster, including archived agents, for trusted first-party jobs. It is gated because the roster is member/workspace context.

**Data flow**: It checks permission, queries all agents in creation order, converts each row into a WorkspaceAgent, and returns the tuple.

**Call relations**: The app notification extension uses this through inbox_agent_id; the permission flag is set when context_for builds a privileged context.

*Call graph*: called by 1 (inbox_agent_id); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.agent_visibilities`  (lines 1252–1264)

```
async def agent_visibilities(self) -> dict[UUID, AgentVisibility]
```

**Purpose**: Reads every agent’s portal visibility setting. This tells extensions the audience floor for things attached to agents.

**Data flow**: It queries agent ids and visibility values for the current workspace and returns a dictionary keyed by agent id.

**Call relations**: It is not gated like member context because it describes workspace shape rather than private member content.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.agent_named`  (lines 1266–1283)

```
async def agent_named(self, name: str) -> AgentIdentity | None
```

**Purpose**: Finds a live agent by name in the current workspace. It returns the id and owner needed for instance actions and authority checks.

**Data flow**: It receives an agent name, queries for a non-archived matching agent, and returns AgentIdentity or None.

**Call relations**: Object-kind code can use this to resolve paths like an agent name to the actual agent row.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.earliest_seated_admin`  (lines 1285–1303)

```
async def earliest_seated_admin(self) -> UUID | None
```

**Purpose**: Finds the earliest seated admin in the workspace. This gives ownerless background work a deterministic member identity to act on behalf of.

**Data flow**: It checks permission, queries seated admin members ordered by seat time and id, and returns the first member id or None.

**Call relations**: Privileged first-party jobs call this when they need a stable admin representative.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.scheduled_member_timezone`  (lines 1305–1321)

```
async def scheduled_member_timezone(self) -> str
```

**Purpose**: Returns the timezone of the member whose authority is bound to a scheduled job. It falls back to UTC if the member has not set one.

**Data flow**: It extracts a member id from the context authority, checks permission, reads the member timezone row, and returns the timezone string.

**Call relations**: Scheduled handlers use this to interpret member-local schedules without reading arbitrary member data.

*Call graph*: 3 external calls (select, workspace_tx, authority_member_id).


##### `ExtensionContext.member_context`  (lines 1323–1479)

```
async def member_context(self, *, since: datetime, limit: int=200, exclude_conversation_id: UUID | None=None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Builds a bounded bundle of recent information visible to the scheduled member. It gathers conversations, artifacts, synced pages, memories, and objectives so background jobs can act with relevant context.

**Data flow**: It checks authority and limit, computes readable audiences, queries recent turns and artifacts, reads text blobs when safe, queries readable pages, adds extension-specific memory/objective records, sorts by information date, and returns the newest records up to the limit.

**Call relations**: It calls _member_blob_text and _member_extension_records, and is available only when context_for grants member-context reading.

*Call graph*: calls 2 internal fn (_member_extension_records, _member_blob_text); 7 external calls (__init__, exists, select, workspace_tx, authority_member_id, is_text_media, readable_audiences).


##### `ExtensionContext._member_extension_records`  (lines 1481–1732)

```
async def _member_extension_records(self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int, exclude_conversation_id: UUID | None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Adds extension-owned memory and open objective records to member context. It keeps scheduled work aware of remembered facts, tasks, and unfinished goals.

**Data flow**: It receives member, audience, time, limit, and exclusion inputs, queries memory and objective-related extension tables, evaluates which objectives are still open, builds stable record keys, and returns MemberContextRecord objects.

**Call relations**: ExtensionContext.member_context calls this after gathering core conversation, artifact, and page records.

*Call graph*: called by 1 (member_context); 8 external calls (__init__, sha256, DateTime, column, or_, select, table, workspace_tx).


##### `ExtensionContext.retitle_conversation`  (lines 1734–1737)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Sets a conversation title in the current workspace. It is for jobs or handlers that decide a better name for a conversation.

**Data flow**: It receives a conversation id and title, then delegates to the surface title writer with the current workspace id.

**Call relations**: It exposes the core retitle helper through the same workspace-scoped context as other extension actions.

*Call graph*: 1 external calls (retitle_conversation).


##### `ExtensionContext.conversations_awaiting_title`  (lines 1739–1763)

```
async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]
```

**Purpose**: Returns recent conversations in this workspace that still need an automatic title. It bounds the amount of title-summarizing work per job tick.

**Data flow**: It receives a limit, queries conversations matching awaiting_a_title ordered newest first, and returns their ids.

**Call relations**: The web extension’s summarize_chat_titles job calls this before generating and saving summaries.

*Call graph*: calls 1 internal fn (awaiting_a_title); called by 1 (summarize_chat_titles); 2 external calls (select, workspace_tx).


##### `ExtensionContext.summarized_conversation_title`  (lines 1765–1770)

```
async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Writes the summarized title for a conversation and marks the summarizer as having run. This removes the conversation from future title-summary work.

**Data flow**: It receives a conversation id and title, then delegates to the core summary-title helper with the current workspace id.

**Call relations**: The web title summarizer calls this after it decides on a title.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (summarize_conversation_title).


##### `ExtensionContext.pending_usage_exports`  (lines 1772–1791)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns settled billing usage records that this extension has not yet acknowledged. It also mints new export intents before reading pending ones.

**Data flow**: It receives a time floor and limit, requires a model key-slot resolver, opens a transaction, freezes eligible usage deltas for this extension, and returns pending UsageExport records.

**Call relations**: Billing export handlers use this before sending usage to an external receiver.

*Call graph*: 3 external calls (workspace_tx, mint_usage_exports, read_pending_usage_exports).


##### `ExtensionContext.ack_usage_exports`  (lines 1793–1802)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered after an external system accepts them. Unacknowledged exports remain available for retry.

**Data flow**: It receives a tuple of UsageExport records, returns immediately if empty, otherwise opens a transaction and acknowledges them for this workspace and extension.

**Call relations**: It is the completion step paired with pending_usage_exports.

*Call graph*: 2 external calls (workspace_tx, ack_usage_exports).


##### `ExtensionContext.transaction`  (lines 1805–1817)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides a database transaction for an extension’s own tables. It is powerful and intentionally documented as raw access, so extensions must still scope their own queries correctly.

**Data flow**: It opens a workspace transaction, yields the async database connection to the caller, commits on normal exit, and rolls back on error through the transaction context.

**Call relations**: Many enrichment, memory, metronome, and other extension object/page handlers use this for their private schemas.

*Call graph*: called by 18 (tick, _entry, _page, _item, _page, _entry, _page, _billing_autopay, _billing_projection, _billing_status (+8 more)); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 1819–1860)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, authority: ExecutionAuthority, holds_work_already_done: bool=False, as_scheduled: bool=False, stand
```

**Purpose**: Asks core to start an internal turn in a conversation. It refuses to run silently if no invoker was wired, because dropping work would be hard to diagnose.

**Data flow**: It receives conversation, agent, message, idempotency key, authority, and admission flags, checks that invoker exists, then delegates and returns the new turn id or None.

**Call relations**: The sources extension calls this when firing triggers; the actual turn admission is implemented by the wired TurnInvoker.

*Call graph*: called by 1 (_fire_trigger).


##### `ExtensionContext.member_reach`  (lines 1862–1872)

```
async def member_reach(self, member_id: UUID, limit: int=4) -> tuple[MemberReach, ...]
```

**Purpose**: Finds recent durable-surface conversations where a member can be reached. This is used when background work needs somewhere to report to that member.

**Data flow**: It checks member-context permission and invoker availability, then asks the invoker for reach records with the requested limit.

**Call relations**: It delegates to TurnInvoker.member_reach and keeps this member data behind the same gate as other member context.


##### `ExtensionContext.tail`  (lines 1874–1883)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Subscribes to live frames from a turn until it ends. A frame is a live update from the running turn, like streaming text or status.

**Data flow**: It receives a turn id and optional cursor, checks that a tailer is wired, and returns an async context manager for reading frames.

**Call relations**: Handlers use the injected TurnTailer rather than touching the live hub directly.


##### `ExtensionContext.turn_is_terminal`  (lines 1885–1899)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has already reached a final state. A missing turn is treated as terminal because there is nothing live left to observe.

**Data flow**: It receives a turn id, queries that turn’s status in the current workspace, and returns true if missing or in a terminal status set.

**Call relations**: Side-channel work can call this before posting updates for a turn it is following.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 1901–1905)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in this workspace, or None if the conversation is not here. It helps handlers resolve opaque conversation ids safely.

**Data flow**: It receives a conversation id and delegates to conversation_agent_id with the current workspace id.

**Call relations**: It exposes the shared conversation-agent lookup used by ConversationProbes.run.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 1907–1945)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Reads audience and surface information for a batch of conversations. This lets member-facing listings decide visibility and explain where something came from.

**Data flow**: It receives conversation ids, returns an empty dict if none, otherwise queries matching current-workspace conversations and maps each id to ConversationFacts with parsed audience.

**Call relations**: Extensions use the result to avoid leaking rows whose conversation id belongs to another workspace.

*Call graph*: 4 external calls (__init__, select, workspace_tx, parse_audience).


##### `ExtensionContext.conversation_arrival_seq`  (lines 1947–1974)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Returns the latest member-arrival sequence number for a conversation. This is a watermark for deciding whether a member spoke after some background work was armed.

**Data flow**: It receives a conversation id, queries the maximum member inbound-message sequence in the current workspace, and returns that number or 0.

**Call relations**: Invoke-style workflows can compare this watermark with stored values to avoid waking on their own internal messages.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1976–2002)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads final status and terminal text for a batch of turns. This supports compact status displays of earlier runs.

**Data flow**: It receives turn ids, returns an empty dict if none, otherwise queries matching current-workspace turns and maps ids to TurnOutcome records.

**Call relations**: It is the turn equivalent of conversation_facts: batched, workspace-scoped, and absence-safe.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 2004–2010)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Tells whether the current workspace belongs to the platform operator. This hides operator-only details in customer workspaces.

**Data flow**: It opens a transaction, reads the workspace domain, compares it to the operator email domain, and returns true or false.

**Call relations**: Renderers can call this before showing spend/debug details intended only for the operator.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 2012–2076)

```
async def open_conversation(self, agent_id: UUID, key: str, member_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for a workflow key and agent. This gives repeated events for the same subject a stable place to accumulate history.

**Data flow**: It receives an agent id, key, and optional member id, confirms the agent belongs to the workspace, inserts the conversation if absent with the extension as surface and the right audience, then returns the conversation id.

**Call relations**: The sources extension uses this before invoking trigger turns; ExtensionContext.invoke starts turns inside the conversation afterward.

*Call graph*: called by 1 (_fire_trigger); 4 external calls (select, workspace_tx, conversation_audience, uuid4).


##### `ExtensionContext.agent_name`  (lines 2078–2092)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the stable name of the currently bound agent. Agent-scoped object handlers use it to link back to the agent they belong to.

**Data flow**: It reads the current agent scope, queries the matching agent row in the workspace, and returns the live or archived name.

**Call relations**: It depends on agent_current, so it is meaningful only when an agent scope has been bound.

*Call graph*: 3 external calls (select, workspace_tx, agent_current).


##### `ExtensionContext.page_states`  (lines 2094–2123)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for specific live pages in the workspace. It includes subject, revision, digest, blob reference, title, and stream.

**Data flow**: It receives page ids, returns an empty dict if none, otherwise queries non-tombstoned pages in the workspace and maps ids to PageState records.

**Call relations**: It is an unrestricted workspace-level page-state read compared with readable_page_states, which also checks reader authority.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 2125–2163)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads page state only for pages the given reader is allowed to see. It combines page subject checks with source-readability rules.

**Data flow**: It receives page ids and a SourceReader, returns empty for no ids, joins pages to sources, filters by workspace, subjects, tombstone state, and _source_readable, then returns PageState records.

**Call relations**: Memory extension object/page handlers call this before showing page-backed memory to a reader.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 2165–2170)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the ids of live sources readable by a given SourceReader. It is a lightweight authority check over sources.

**Data flow**: It receives a SourceReader, builds a query with _source_readable, executes it in the current workspace, and returns a frozenset of source ids.

**Call relations**: It shares the same source visibility rule used by source_pages and readable_page_states.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 2172–2357)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers or revives a content-sync source for the workspace and grants it to an agent. A source is an external feed, such as a connected account or folder, whose pages will be synced later.

**Data flow**: It receives backend, typed config, disclosure subject, owner, optional connection, and optional agent. It derives the source id, validates the target agent and connection ownership, inserts or revives the source row, checks for conflicting authority or requested fields, creates the source grant, and returns the source id.

**Call relations**: The sample extension calls this during setup; sync drivers later read the registered rows, while source_id provides the stable id calculation.

*Call graph*: calls 1 internal fn (source_id); called by 1 (_setup); 5 external calls (now, model_dump, select, update, workspace_tx).


##### `ExtensionContext.grant_source`  (lines 2359–2415)

```
async def grant_source(self, source_id: UUID, *, agent_id: UUID, actor_member_id: UUID) -> None
```

**Purpose**: Grants an existing source to another agent without creating a duplicate sync row. This lets multiple agents read one feed when authority allows it.

**Data flow**: It receives a source id, target agent id, and acting member id. It locks and validates the source, checks that the actor owns it or it is shared, validates the agent, inserts the grant if missing, and returns nothing.

**Call relations**: It complements register_source, which grants only during registration.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.source_id`  (lines 2417–2435)

```
def source_id(self, backend: str, config: BaseModel, *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Computes the stable id that register_source would use for a source. This lets callers reason about a source before it exists.

**Data flow**: It receives backend, config, and optional connection id, dumps the config to JSON, applies any non-identity fields declared by the config type, and returns a UUID.

**Call relations**: register_source calls it before inserting; callers can pair it with removed_source_ids to detect deleted feeds.

*Call graph*: called by 1 (register_source); 2 external calls (model_dump, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 2437–2460)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which of a given set of source ids are known removed in this workspace. Absence is not treated as removal.

**Data flow**: It receives source ids, returns an empty frozenset if none, otherwise queries rows with removed_at set and returns their ids.

**Call relations**: Extensions use this with source_id when deciding whether to avoid reviving a member-deleted source.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 2462–2506)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists this workspace’s live registered sources, optionally filtered by backend. Removed sources are intentionally hidden.

**Data flow**: It builds a workspace-scoped source query, applies a backend filter if provided, converts rows to SourceRecord objects, and returns them.

**Call relations**: Gbrain and sources extension code call this to discover currently registered source bindings.

*Call graph*: called by 2 (_registered_from_ext, _bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 2508–2556)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a given agent/member reader. It applies both page-subject visibility and source-authority checks.

**Data flow**: It receives a SourceReader, joins pages to sources, filters by workspace, non-tombstone state, reader subjects, and _source_readable, then returns PageRecord objects.

**Call relations**: It is the page listing counterpart to readable_source_ids.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 2558–2574)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as tombstoned so downstream indexing can remove its derived state. It fails if the page is unknown or already forgotten.

**Data flow**: It receives a page id, updates that current-workspace page to tombstone true with a fresh timestamp, and raises if no row changed.

**Call relations**: It is the single-page cleanup path corresponding to source_pages reads.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 2576–2613)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a source from syncing and tombstones its live pages. The source row remains so references and future re-registration behave predictably.

**Data flow**: It receives a source id, marks the live source removed, clears claims, deletes grants, tombstones pages for that source, and raises if no live source matched.

**Call relations**: It is the removal counterpart to register_source; the page-change pipeline later clears derived index data.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 2615–2641)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for live sources and their live pages together. This keeps source and page visibility from being temporarily inconsistent.

**Data flow**: It receives source ids and a subject, updates matching live sources, raises if none matched, and restamps non-tombstoned pages with the new subject and timestamp.

**Call relations**: Callers use it when a feed changes from private to shared or similar disclosure changes.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 2643–2716)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Updates non-identity configuration fields for live sources, optionally forcing a full refetch. A non-identity field is a setting that changes how much to fetch, not which feed the row represents.

**Data flow**: It receives a mapping of source ids to configs and optional refetch ids, validates inputs, locks all rows, recomputes each source id to ensure the config still belongs to the same row, writes new config, and clears cursor/claims for refetch rows.

**Call relations**: It protects register_source’s stable identity rule while letting extensions adjust fetch windows in place.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 2718–2747)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Requests that live sources sync as soon as possible. It also unparks sources that had been slowed down after repeated provider refusals.

**Data flow**: It receives source ids, updates matching live sources to next_sync_at now, clears parked/refusal fields, and raises if none matched.

**Call relations**: Sync drivers later claim these rows on their normal polling loop.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 2749–2755)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Opens a governed proposal to change an agent prompt instead of changing it directly. Governance means an approval process can verify and apply the change safely.

**Data flow**: It receives an AgentChange, creates a Governance helper for this workspace and extension, and returns the ProposalRef from propose_change.

**Call relations**: The sample extension calls this during its tick to suggest agent changes through the governed path.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 2757–2762)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns the current workspace’s trajectory corpus through the context. It fails loudly if no corpus was wired, so missing setup is not mistaken for an empty history.

**Data flow**: It checks that corpus is present, delegates to TrajectoryCorpus.trajectories, and returns the resulting trajectories.

**Call relations**: The sample extension calls this in its tick when experimenting with transcript-driven work.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 2765–2830)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: WorkspaceBlobStore | None=None, sandboxes:
```

**Purpose**: Builds the ExtensionContext object handed to an extension or core job. It wires only the capabilities that the runtime has chosen to provide.

**Data flow**: It receives extension name, declared credential slots, optional services such as index, blob store, sandboxes, invoker, model resolver, surfaces, tailer, and member-context settings. It validates model attribution, constructs scoped access objects, and returns an ExtensionContext.

**Call relations**: This is the factory that makes core jobs and extensions use the same scoped path instead of receiving raw database, blob, model, or sandbox handles.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/runtime/ext/conversation_slots.py`

`data_model` · `cross-cutting`

This file is mostly a set of strict data contracts. A data contract says, “if something wants to appear in the conversation portal, it must look like this.” Without these rules, one extension could send a broken link, an oversized title, a task count that does not add up, or hidden authorization data to the user interface.

The models are built with Pydantic, a validation library that checks incoming Python objects and rejects values that do not fit the rules. For example, artifact and source links must be normal HTTP or HTTPS web links, not links with embedded usernames and passwords. Image preview links get extra checks because browsers will draw them inside pages, so unsafe characters, fragments, credentials, or odd control characters are rejected.

Each slot payload has a fixed type name, a limited list of items, and a `truncated` flag to say whether only part of the full list is being shown. This is like a display case with a maximum number of shelves: if there are too many items, the system shows a safe subset and says the list was cut short.

At the end, the file defines context and provider objects. These tell an extension what conversation it is reading, what the audience can see, and which callback functions it must provide to summarize or return slot content.

#### Function details

##### `ImagePreview.drawable_url`  (lines 52–69)

```
def drawable_url(cls, value: str) -> str
```

**Purpose**: Checks that an image preview URL is safe for the portal and embedded pages to draw. It allows only ordinary HTTP or HTTPS links with a real host and no credentials, fragments, backslashes, or hidden control characters.

**Data flow**: A URL string comes in. The function splits it into web-address parts, decodes any percent-encoded characters, scans for unsafe characters, and either returns the original URL unchanged or raises a validation error before the `ImagePreview` object can be created.

**Call relations**: Pydantic calls this validator when code builds an `ImagePreview`. The validator relies on standard URL parsing, URL decoding, and Unicode character classification helpers to decide whether the link is safe enough to store in the slot payload.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 85–96)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: Checks the optional download or viewing URL for a conversation artifact. It makes sure any provided artifact link is a normal HTTP or HTTPS URL and does not hide a username or password inside it.

**Data flow**: The input is either `None` or a URL string. If it is `None`, the function leaves it alone. If it is a string, it parses the URL and returns it unchanged only when the scheme and host are acceptable and no credentials are present; otherwise it stops model creation with a validation error.

**Call relations**: Pydantic runs this when a `ConversationArtifact` is created. It hands the raw URL to the standard URL splitter, then feeds back either a clean accepted value or an error so unsafe artifact links do not reach the portal.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 117–126)

```
def http_url(cls, value: str) -> str
```

**Purpose**: Checks that a cited source link is a safe web URL. Sources must be reachable by HTTP or HTTPS, must name a host, and must not include embedded login credentials.

**Data flow**: A source URL string comes in. The function parses it, checks the scheme, host, username, and password fields, and returns the same string if it passes. If the URL is not acceptable, it raises a validation error instead of allowing the source object to exist.

**Call relations**: This validator is called automatically while building a `ConversationSource`. It uses URL parsing to protect later readers and UI code from malformed or credential-bearing citation links.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 155–169)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: Makes sure the task summary numbers match the visible task list. It prevents impossible states, such as saying 10 tasks are completed when there are only 5 total, or showing more visible tasks than the total count.

**Data flow**: A fully built `TasksSlotPayload` comes in with a title, visible tasks, total count, completed count, and truncation flag. The function compares the counts against the visible task statuses. If everything adds up, it returns the same payload; if the numbers contradict each other, it raises a validation error.

**Call relations**: Pydantic calls this after the individual fields of `TasksSlotPayload` have been read. It does not call other project functions; it acts as the final sanity check before task progress is allowed into a conversation slot.


##### `ConversationSite.http_url`  (lines 184–193)

```
def http_url(cls, value: str) -> str
```

**Purpose**: Checks that a conversation site URL is a normal web link. It rejects missing hosts, non-HTTP schemes, and URLs that include embedded usernames or passwords.

**Data flow**: A site URL string comes in. The function parses it into parts, verifies that it is HTTP or HTTPS with a host and no credentials, and returns the original string when valid. If not, it raises a validation error and the site model is rejected.

**Call relations**: Pydantic invokes this while constructing a `ConversationSite`. It uses the standard URL splitter so that only safe site links are stored and later exposed through the sites slot.

*Call graph*: 1 external calls (urlsplit).


### Shared record contracts
Common identity and turn/work record models keep runtime, storage, worker, and user-facing surfaces aligned on shared object and execution terminology.

### `core/src/ufo/runtime/object_name.py`

`data_model` · `cross-cutting`

This file is the naming rulebook for runtime objects. In this system, an object is identified by two parts: its kind, such as the category it belongs to, and its name, which is the specific item inside that category. Together they form a stable reference like `kind/name`, much like a street address has both a street and a house number.

The file exists separately from the larger object system because many low-level parts of the runtime need to create or carry object names without importing all the code that reads and modifies objects. That keeps naming rules available everywhere without creating tangled dependencies.

It defines simple grammar rules using regular expressions, which are patterns for checking text. Object kinds must start with a lowercase letter and then use lowercase letters, numbers, or underscores. Normal object names use lowercase letters, numbers, and hyphens, with a maximum length of 64 characters. There is also a special reserved archived-name form, but it is only allowed for objects of kind `agent`.

The main type is `ObjectRef`, a frozen Pydantic model. Pydantic is a validation library that checks data when the object is created. “Frozen” means the reference cannot be changed after creation. This matters because object identities should stay stable once passed around.

#### Function details

##### `validate_object_name`  (lines 25–33)

```
def validate_object_name(name: str) -> None
```

**Purpose**: Checks whether a caller-supplied object name follows the shared naming rules before the system stores it. This lets the system reject a bad name at the moment it is provided, instead of discovering later that it cannot safely refer to the object.

**Data flow**: It receives a text name. It checks two things: the name must be no longer than 64 characters, and it must match the allowed object-name pattern. If the name is valid, nothing is returned and nothing changes. If it is invalid, it raises `InvalidName`, an error that clearly says what rule was broken.

**Call relations**: This helper is meant for write paths that persist an object under a user-provided name. Its only direct handoff is to the `InvalidName` error constructor when it needs to refuse a bad name.

*Call graph*: 1 external calls (__init__).


##### `ObjectRef.validate_kind`  (lines 49–52)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: Checks that the `kind` part of an object reference uses the allowed kind-name format. This keeps object categories predictable and safe to use across the runtime.

**Data flow**: It receives the proposed kind text while an `ObjectRef` is being built. It compares that text with the kind pattern. If the text is acceptable, it passes the same value onward. If not, it raises a validation error explaining the expected format.

**Call relations**: Pydantic calls this automatically when code creates an `ObjectRef`. It acts as the first gate for the `kind` field before the finished reference is allowed to exist.


##### `ObjectRef.validate_name`  (lines 56–65)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: Checks that the `name` part of an object reference is either a normal valid object name or the special reserved archived-agent form. This makes sure references can be safely written, read, and displayed in the system’s standard `kind/name` shape.

**Data flow**: It receives the proposed name text while an `ObjectRef` is being built. It checks the maximum length, then checks whether the name matches either the normal object-name pattern or the reserved archived-name pattern. A valid name is returned unchanged. An invalid name causes a validation error.

**Call relations**: Pydantic calls this automatically during `ObjectRef` creation. It prepares the name field for the later whole-object check that decides whether a reserved archived name is allowed with this kind.


##### `ObjectRef.validate_reserved_name_kind`  (lines 68–71)

```
def validate_reserved_name_kind(self) -> 'ObjectRef'
```

**Purpose**: Enforces the special rule that archived-style reserved names may only be used for `agent` objects. This prevents an internal reserved naming scheme from accidentally being used for unrelated object kinds.

**Data flow**: It receives the already-created `ObjectRef` after the individual fields have been checked. It looks at both `kind` and `name` together. If the name has the reserved archived form and the kind is not `agent`, it raises a validation error. Otherwise, it returns the same reference unchanged.

**Call relations**: Pydantic calls this after the field-level validators have run. It is the final consistency check that needs to see more than one field at the same time.


##### `ObjectRef.__str__`  (lines 73–74)

```
def __str__(self) -> str
```

**Purpose**: Turns an object reference into its standard human-readable and wire-friendly text form, `kind/name`. This gives the rest of the system one consistent way to spell a reference.

**Data flow**: It reads the reference’s `kind` and `name` fields. It joins them with a slash. The result is a string such as `agent/main`.

**Call relations**: This is used whenever Python needs the text form of an `ObjectRef`, such as for display, logging, or passing the reference through an interface that expects the canonical spelling.


##### `ObjectRef.parse`  (lines 77–82)

```
def parse(cls, value: str) -> 'ObjectRef'
```

**Purpose**: Builds an `ObjectRef` from its standard text form, `<kind>/<name>`. This is useful when a reference arrives as plain text and must be turned back into a checked object identity.

**Data flow**: It receives a string. It splits the string on `/` and requires exactly two parts. If the shape is wrong, it raises an error. If the shape is right, it creates an `ObjectRef` using the first part as `kind` and the second as `name`, which then triggers the normal validation rules.

**Call relations**: This parser is called when object-related code receives a reference as text, including `core/src/ufo/runtime/objects.ObjectGetInput.validate_ref` and `core/src/ufo/runtime/objects.ObjectVerbs._get`. It converts their incoming string into the shared `ObjectRef` form before the object system continues.

*Call graph*: called by 2 (validate_ref, _get).


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting: turn admission, queueing, execution, storage, and result delivery`

A “turn” is one unit of agent work, like one message or one prepared action waiting to be run. This file gives that unit a precise shape. It defines allowed status values, queue names, delivery states, billing identifiers, final result frames, user questions, credential requests, account connection requests, agent settings, and runtime details. Most records are Pydantic models, which means they both carry data and check that the data is safe and consistent when created.

The file matters because several parts of the system touch the same turn at different times. A surface admits a turn, a worker runs it, billing records token use, a sandbox may be involved, and a portal or chat surface may show the result. Without these shared records, each part could interpret the same row differently.

There are also small helper functions for stable choices. For example, IDs are made deterministically from workspace, conversation, and sequence values, so repeating the same workflow does not create duplicate records. Agent icons are chosen in a predictable but varied way, like assigning name badges that avoid duplicates when possible. Validation methods protect important invariants: terminal turns must have terminal frames, runtime image fields must come in matching pairs, timestamps are treated as UTC, and user-supplied context is flattened so it cannot fake internal markup.

#### Function details

##### `auto_agent_icon`  (lines 179–198)

```
def auto_agent_icon(name: str, taken: Collection[str]) -> TablerIcon
```

**Purpose**: Chooses a starting icon for a new agent. It tries to pick an icon that fits words in the agent name, avoids icons already used in the workspace when possible, and otherwise falls back to a stable hash-based choice.

**Data flow**: It receives an agent name and a collection of already-taken icon names. It lowercases and splits the name into simple word tokens, checks whether any token maps to a preferred icon, and computes a SHA-256 hash of the name so the fallback choice is repeatable. It returns one valid icon name: a matching unused keyword icon, an unused hash-selected icon, or a repeated icon if all choices are taken.

**Call relations**: This helper is used when a new agent needs a visual mark. It relies on the local icon lists and keyword map, and calls the standard hashing function so the same name tends to receive the same icon instead of changing randomly.

*Call graph*: 1 external calls (sha256).


##### `admits_spent_balance`  (lines 223–237)

```
def admits_spent_balance(intent: ToolIntent) -> bool
```

**Purpose**: Decides whether a prepared tool action should still be allowed when a workspace has run out of balance. The one allowed case is managing billing, because blocking that would prevent the user from fixing the billing problem.

**Data flow**: It receives a ToolIntent, looks at the tool name and selected input fields, and checks whether it is the workspace-level manage_billing action. It returns true only for that exact action; all other intents return false.

**Call relations**: This function sits near admission or billing gates. When those gates would normally refuse new work because of spent balance, this check lets through the specific prepared action that can lead to refilling or managing payment, while leaving the action’s own permission checks to later code.


##### `turn_queue_for`  (lines 251–259)

```
def turn_queue_for(parent_turn_id: UUID | None, admission_source: 'TurnAdmissionSource') -> str
```

**Purpose**: Chooses which queue a turn should enter. Ordinary root turns go to the normal turns queue, while spawned child turns and prepared intents go to the faster express queue to avoid blocking important waiting flows.

**Data flow**: It receives an optional parent turn ID and the source that admitted the turn. If there is a parent turn, or if the source is an intent, it returns the express queue name. Otherwise it returns the normal turns queue name.

**Call relations**: This function is used when a turn is admitted and needs a worker queue. It encodes an important scheduling rule: child work and direct prepared actions must start even when normal capacity is tight, because a parent may be waiting for the child or a user interface may be waiting on a bounded action.


##### `turn_id_for`  (lines 272–274)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Builds the stable ID for a turn. The same workspace, conversation, and sequence number always produce the same UUID, which helps retries avoid creating duplicate turns.

**Data flow**: It receives a workspace ID, conversation ID, and sequence number. It formats those values into one string and passes that string to UUID version 5 generation, which creates a deterministic UUID from a namespace and name. It returns that UUID.

**Call relations**: This helper is used when creating or replaying a turn. By calling deterministic UUID generation, it gives the turn and its workflow one repeatable identity instead of making a fresh random ID every time.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 277–282)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Builds a stable billing ledger ID for one spending record. It separates billing by workspace, turn, spending dimension, and run attempt so retries collapse correctly while resumed work can be billed separately.

**Data flow**: It receives a workspace ID, turn ID, billing dimension, and optional attempt string. It combines them into a canonical text key and turns that key into a deterministic UUID. It returns the resulting ledger ID.

**Call relations**: Billing code can use this helper when recording token or cost usage. It hands deterministic identity work to UUID version 5 so the same attempted write is recognized as the same ledger row, while a different attempt gets its own row.

*Call graph*: 1 external calls (uuid5).


##### `mid_turn_reply_id_for`  (lines 285–296)

```
def mid_turn_reply_id_for(turn_id: UUID, round_index: int, span_index: int, attempt: str='') -> UUID
```

**Purpose**: Builds a stable ID for a reply emitted before a turn fully ends. This lets the system avoid delivering the same replayed partial reply twice while still treating replies from a later resumed attempt as new.

**Data flow**: It receives the turn ID, round index, span index, and optional attempt string. It combines those details into a text key and uses deterministic UUID generation to produce the reply ID. It returns that UUID.

**Call relations**: Delivery code can use this when a running turn speaks partway through execution. The function delegates the actual ID creation to UUID version 5, making replayed spans land on the same delivery record while resumed attempts receive distinct identities.

*Call graph*: 1 external calls (uuid5).


##### `RuntimeIdentity._artifact_pair`  (lines 452–455)

```
def _artifact_pair(self) -> 'RuntimeIdentity'
```

**Purpose**: Checks that runtime revision and image digest appear together. A revision without the exact image, or an image without the revision, would describe the runtime only halfway.

**Data flow**: After a RuntimeIdentity record is built, it reads the revision and image_digest fields. If exactly one is missing, it raises a validation error. If both are present or both are absent, it returns the record unchanged.

**Call relations**: This validator runs automatically when RuntimeIdentity is created. It protects later readers of runtime attestations from seeing an incomplete pair of deployment facts.


##### `TurnRuntimeConfig._pinned_values`  (lines 474–482)

```
def _pinned_values(self) -> 'TurnRuntimeConfig'
```

**Purpose**: Checks that per-turn runtime choices are concrete and valid. A turn may pin a specific model or environment digest, but not the vague value “auto,” and an environment must look like a stored SHA-256 digest.

**Data flow**: After a TurnRuntimeConfig record is built, it reads the model and environment fields. It rejects model equal to "auto" and rejects an environment value that does not match the expected sha256 digest format. If all values are acceptable, it returns the record unchanged.

**Call relations**: This validator runs whenever turn runtime configuration is parsed or created. It ensures later execution code receives exact choices rather than ambiguous or malformed pins.


##### `TurnContext._tag_safe_line`  (lines 544–548)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans surface-provided text so it can be safely placed into an internal context tag. It removes angle brackets and flattens whitespace, preventing user text from pretending to be system markup.

**Data flow**: It receives a string or None for fields such as sender, question, source, or reply destination. None stays None. Text has < and > removed, is split and joined into a single clean line, and empty results become None. The cleaned value is returned.

**Call relations**: This field validator runs automatically when TurnContext is created. It prepares user- or surface-reported context before the engine renders it into the prompt-like context shown to the agent.


##### `TurnContext._known_zone`  (lines 552–559)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a timezone name is real. This catches bad timezone data at the boundary, before a turn is running and expecting time information to work.

**Data flow**: It receives a timezone string or None. None is accepted. For a string, it asks the system timezone database to load that zone. If the zone is unknown, it raises a validation error; otherwise it returns the original timezone name.

**Call relations**: This validator runs when TurnContext is constructed by an admitting surface. It calls the standard ZoneInfo loader so the rest of the system only sees timezone names that the runtime can actually understand.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn.spawned`  (lines 596–599)

```
def spawned(self) -> bool
```

**Purpose**: Reports whether this turn was created by another turn. In plain terms, it tells whether the turn is a child task rather than a root user-facing task.

**Data flow**: It reads the turn’s parent_turn_id field. If that field is present, it returns true. If there is no parent turn ID, it returns false.

**Call relations**: This property is available to code that has a Turn record and needs to branch based on whether the turn came from a spawn path. It does not call other helpers; it simply interprets the stored parent link.


##### `Turn.authority`  (lines 602–604)

```
def authority(self) -> ExecutionAuthority
```

**Purpose**: Returns the execution authority for the turn: the member or workspace permission identity under which the turn should act. This is important because tools must know whose rights they are using.

**Data flow**: It reads speaker_member_id and on_behalf_of_member_id from the turn. It passes those IDs to turn_authority, which decides the correct ExecutionAuthority. It returns that authority object.

**Call relations**: This property is used by code that needs to run or validate actions for a turn. It hands off the permission decision to the runtime authority helper, keeping the Turn model’s authority rule in one consistent place.

*Call graph*: 1 external calls (turn_authority).


##### `Turn._nothing_created`  (lines 608–611)

```
def _nothing_created(cls, value: object) -> object
```

**Purpose**: Converts a missing created_refs database value into an empty tuple. This lets code treat “nothing was created” as an empty list of references instead of special-casing SQL NULL.

**Data flow**: Before created_refs is fully validated, it receives the raw stored value. If the value is None, it returns an empty tuple. Otherwise it returns the value unchanged for normal validation.

**Call relations**: This validator runs while a Turn is loaded or created. It smooths over how the database may store absent created references so later turn logic can always read created_refs as a collection.


##### `Turn._aware_utc`  (lines 615–620)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps have timezone information. If a database driver gives back a timestamp without a timezone marker, this treats it as UTC rather than letting Python mistake it for local time.

**Data flow**: It receives a datetime or None for created_at, updated_at, or retry_at. None stays None. A datetime that already has timezone information is returned as-is. A timezone-less datetime is returned with UTC attached.

**Call relations**: This validator runs when Turn timestamps are parsed. It uses datetime.replace to attach UTC where needed, protecting scheduling and display code from silent timezone mistakes.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 623–629)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that the turn’s overall status and terminal result agree. A running, queued, or parked turn must not already have a final frame, and a done, failed, or cancelled turn must have one with the same status.

**Data flow**: After the Turn record is built, it first forces authority calculation, which also validates that the authority fields make sense. Then it compares the status with whether terminal is present, and compares terminal.status with the turn status when a terminal exists. It raises a validation error on mismatch or returns the turn unchanged.

**Call relations**: This model validator runs automatically whenever a Turn is constructed. It ties together the Turn record, its authority property, and its TerminalFrame so downstream workers and surfaces can trust that a terminal turn really has its final result and an unfinished turn does not.
