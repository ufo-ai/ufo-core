# Core Extension and Model Contracts  `stage-23.1`

This stage is shared behind-the-scenes support. It defines the “rules of the road” for extensions and AI model providers, so the rest of the system can work with them safely and consistently.

The context file builds the safe workbench an extension receives when it runs. Instead of giving an add-on full access to the whole system, it hands over only approved tools: its own storage, declared credentials, model access, synced sources, conversation files, and read-only transcripts.

The conversation slots file defines the shapes of small conversation side panels, such as artifacts, tasks, sources, sites, automations, and workspace changes. It also defines how extensions can provide summaries or readable views of that data.

The manifest file is the declaration form for extensions and packs. It says what they add, such as tools, jobs, routes, agents, hooks, skills, or search backends.

The model interface file defines the common format for talking to AI providers, including messages, tool calls, streaming updates, and errors. Together, these contracts let optional pieces plug in without surprising the core system.

## Files in this stage

### Extension Runtime Contracts
Defines the safe runtime workbench for extensions plus the shared data contracts for conversation surfaces, extension declarations, and model-provider communication.

### `core/src/ufo/ext/context.py`

`orchestration` · `cross-cutting`

Extensions need to do useful work without being handed the keys to the whole system. This file is the gatekeeper for that. Instead of giving an extension a raw database connection, raw secret store, or unrestricted blob storage, it gives a carefully shaped context object. Think of it like a hotel key card: it opens your room and the gym, but not every room in the building.

The main object, `ExtensionContext`, gathers many smaller capabilities. `ScopedStore` gives one extension a private key-value area inside the current workspace. `CredentialAccess` lets an extension read only the secret slots it declared in its manifest. `TrajectoryCorpus` reads this workspace’s conversation transcripts for evaluation or learning jobs, but cannot write them. `ConversationFiles` and `ConversationProbes` let approved background work write files or run short commands inside a conversation sandbox. `ModelAccess` lets background code call the language model while recording cost and timing the same way normal turns do.

Most methods rely on an ambient workspace, meaning the workspace is already bound by the runner and is not passed around by the extension. That is important: it keeps tenant boundaries central and consistent. The file also contains helper queries that tell job schedulers which workspaces have relevant work, such as conversations needing titles or sources needing attention.

#### Function details

##### `ScopedStore.workspace_id`  (lines 118–119)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently bound to this running job or handler. It lets the store automatically stay inside the right workspace without the caller passing a workspace id.

**Data flow**: It reads the current workspace scope and returns its id. Nothing is written or changed.

**Call relations**: Other `ScopedStore` methods use this property before database reads and writes so every key-value operation is tied to the active workspace.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 121–132)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one stored JSON value for this extension. It is used when an extension needs to remember small durable state, such as a saved session id or progress marker.

**Data flow**: The caller gives a key. The method looks in the extension’s private store for that key in the current workspace and returns the value, or `None` if it is missing.

**Call relations**: Browser, Slack, and web surfaces call this when resuming prior state. It uses the workspace-scoped database transaction so callers never choose another workspace by accident.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_web_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 134–149)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several stored JSON values at once. This avoids making one database trip per key when an extension already knows exactly which keys it needs.

**Data flow**: The caller gives a list of keys. The method fetches matching rows for this extension and workspace and returns a dictionary for keys that exist.

**Call relations**: It is a batch version of `ScopedStore.get`, using the same scoped storage boundary.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 151–175)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Writes or replaces one stored JSON value for this extension. It is safe for first-time writes even if two callers try to create the same key at the same time.

**Data flow**: The caller gives a key and value. The method inserts a new row or updates the existing row, then commits with the workspace transaction.

**Call relations**: Browser, Slack, and web code call this when recording durable extension state. It uses database upsert behavior so callers do not need a separate read-before-write step.

*Call graph*: called by 4 (_start, _context, _hold_connect_message, _open_conversation); 1 external calls (workspace_tx).


##### `ScopedStore.put_if`  (lines 177–227)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the current stored value still matches what the caller expected. This prevents an older worker from overwriting newer progress.

**Data flow**: The caller gives a key, a new value, and an expected old value. The method compares against the stored value inside one transaction and returns `true` only if it wrote the new value.

**Call relations**: Slack reply progress uses this to checkpoint safely while multiple updates may race. It is the cautious alternative to `put`.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 229–237)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes one key from this extension’s private store. It is used when saved state should no longer affect future runs.

**Data flow**: The caller gives a key. The method removes the matching row for this extension and current workspace.

**Call relations**: The web surface calls this while opening conversations, using the same workspace and extension boundary as reads and writes.

*Call graph*: called by 1 (_open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 239–252)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists this extension’s stored keys, optionally limited by a prefix. It lets code discover a controlled slice of its own saved state.

**Data flow**: The caller gives an optional prefix. The method returns sorted key-value pairs from this extension’s store in the current workspace.

**Call relations**: Web audience code uses it to find granted agents or emails. It deliberately lists only this extension’s namespace, not the whole store.

*Call graph*: called by 2 (_granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 268–269)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose credentials will be read. This keeps credential lookup bound to the current job or turn.

**Data flow**: It reads the active workspace scope and returns its id. It does not expose the credential store itself.

**Call relations**: Credential resolution methods use this value when looking up stored or source-backed secrets.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 271–277)

```
async def get(self, slot: str) -> str
```

**Purpose**: Gets the live secret for a declared credential slot. It refuses access if the extension did not declare that slot.

**Data flow**: The caller gives a slot name. The method checks the allowed slot list, then asks the current workspace for the secret value and returns it.

**Call relations**: This is the simplest credential path. If the slot was not declared, it raises `UndeclaredCredentialSlot` before any secret lookup happens.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 279–286)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Reports whether a credential slot is backed by this workspace’s own stored secret. This matters for billing, because using a customer’s own provider key is different from using the platform key.

**Data flow**: The caller gives a slot name. After checking that it was declared, the method returns a boolean from the current workspace.

**Call relations**: It uses the same declaration gate as `get`, but returns ownership information instead of the secret.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.resolve`  (lines 288–301)

```
async def resolve(self, slot: str) -> str
```

**Purpose**: Resolves a declared credential slot through its configured source when one exists, otherwise through the workspace’s normal credential lookup. It supports secrets that come from provider installations as well as plain stored slots.

**Data flow**: The caller gives a slot name. The method checks permission, tries the slot’s source and credential store if configured, and falls back to the workspace credential value.

**Call relations**: It calls the credential source helper for source-backed slots. If a slot has a source but no store was wired, it fails loudly because it cannot safely resolve the secret.

*Call graph*: 3 external calls (__init__, slot_secret, ws_current).


##### `CredentialAccess.rotate`  (lines 303–308)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if it still has the expected old value. This is useful after an external provider rotates a secret.

**Data flow**: The caller gives a slot, expected current plaintext, and new plaintext. The method checks the slot is allowed, then asks the workspace to rotate it and returns whether the swap succeeded.

**Call relations**: It delegates the actual compare-and-swap to the workspace credential layer after enforcing the extension’s declared-slot boundary.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 310–323)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores a sealed provider installation reference in a declared credential slot. The seal proves the installation belongs to this workspace and slot instead of saving a raw installation id.

**Data flow**: The caller gives a slot and installation id. The method checks permission, seals the workspace-slot-installation tuple, and writes that sealed value as the credential.

**Call relations**: It uses installed credential request settings and the sealing helper. This keeps installation credentials from being forged or copied across workspaces.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 356–357)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose transcripts this corpus may read. It keeps transcript access tied to the active workspace.

**Data flow**: It reads the current workspace scope and returns its id.

**Call relations**: The corpus read methods use this property to limit database queries before loading transcript blobs.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 359–366)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for the current workspace. It is meant for evaluation or learning jobs that need examples of past conversations.

**Data flow**: It chooses recent conversation ids from the database, then passes that choice to `_read`. The result is a tuple of decoded trajectory records.

**Call relations**: This is the broad corpus read path. It delegates the actual transcript loading and decoding to `_read`.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus.conversations`  (lines 368–380)

```
async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]
```

**Purpose**: Reads transcripts for specific conversation ids. It lets a job inspect known conversations even if they are older than the default recent limit.

**Data flow**: The caller gives conversation ids. The method filters them to conversations in the current workspace and asks `_read` to load those transcripts.

**Call relations**: It shares the same protected transcript reader as `trajectories`, but starts from caller-selected ids.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus._read`  (lines 382–426)

```
async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]
```

**Purpose**: Loads and decodes transcript blobs for selected conversations, skipping missing or corrupt transcripts instead of failing the whole batch.

**Data flow**: It receives a database subquery choosing conversations. It finds each conversation’s agent and prompt, loads the transcript blob, decodes messages, and returns trajectory objects with prompt digests.

**Call relations**: `trajectories` and `conversations` both hand their chosen conversation set here. It logs corrupt transcripts and continues so one bad record does not stop the corpus.

*Call graph*: called by 2 (conversations, trajectories); 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `ConversationFiles.write`  (lines 445–448)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s visible workspace directory. This lets approved off-turn work place files where the agent can see them later.

**Data flow**: The caller gives a conversation id, relative path, and content. The method delegates to the conversation sandbox and returns the `/workspace` path visible to the agent.

**Call relations**: It is a narrow wrapper over the sandbox file writer, keeping the raw sandbox object private from extensions.


##### `ConversationFiles.prune`  (lines 450–456)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a path prefix, keeping only the newest configured number. This prevents background writers from filling a conversation workspace forever.

**Data flow**: The caller gives a conversation id, path prefix, and keep count. The sandbox removes excess files under that prefix.

**Call relations**: It pairs with `write` for append-style off-turn output: write new files, then prune old ones.


##### `ConversationFiles.write_runtime`  (lines 458–462)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output into a named category for a conversation. It separates system-produced files from ordinary workspace files.

**Data flow**: The caller gives a conversation id, category, relative path, and bytes. The sandbox stores the content in that runtime area and returns the path.

**Call relations**: It exposes only a scoped runtime write, not the full sandbox.


##### `ConversationFiles.prune_runtime`  (lines 464–472)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Keeps runtime files under a category bounded in size. This stops internal output directories from growing without limit.

**Data flow**: The caller gives a conversation id, category, path prefix, and keep count. The sandbox deletes older matching runtime files.

**Call relations**: It is the cleanup partner to `write_runtime`.


##### `conversation_agent_id`  (lines 475–488)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation belongs to, if that conversation exists in the given workspace. It is a small shared check used before agent-scoped work runs.

**Data flow**: The caller gives workspace and conversation ids. The method queries the conversation row and returns the agent id, or `None` if not found in that workspace.

**Call relations**: `ConversationProbes.run` uses it before opening a sandbox, and `ExtensionContext.conversation_agent` exposes it to handlers.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 527–577)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, acting_member_id: UUID | None=None) -> ExecResult
```

**Purpose**: Runs one short shell command inside a conversation sandbox while no normal turn is active. It is for bounded probes, not long-running background processes.

**Data flow**: The caller gives a conversation id, command, timeout, and optional acting member. The method validates the timeout, finds the conversation’s agent, creates a signed probe token, opens the sandbox with the right environment, and returns command output and exit code.

**Call relations**: It calls `conversation_agent_id` first to enforce workspace ownership, then binds the agent scope while opening the sandbox. The sandbox session runs the command and supplies the result.

*Call graph*: calls 1 internal fn (conversation_agent_id); 5 external calls (__init__, now, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 580–602)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a scheduler candidate set for workspaces that have at least one conversation with at least one turn. Jobs that read trajectory corpuses use it to avoid running where there is no transcript-like work.

**Data flow**: It defines a database query builder and wraps it as workspace candidates. The returned object lets the job dispatcher find matching workspaces.

**Call relations**: The nested `with_a_turn` function supplies the actual query. `owner_candidates` turns that query into the scheduler-facing candidate seam.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 588–600)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds workspaces with turn-bearing conversations. It is the actual filter behind `trajectory_workspaces`.

**Data flow**: It produces a select statement over workspaces where a matching conversation and turn exist. It does not execute the query itself.

**Call relations**: The enclosing `trajectory_workspaces` passes this query builder to the candidate system.

*Call graph*: 2 external calls (exists, select).


##### `seated_member_workspaces`  (lines 605–615)

```
def seated_member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a scheduler candidate set for workspaces with at least one seated member. First-party member jobs use this to run only where there are active members.

**Data flow**: It defines a query for distinct workspaces with seated members and wraps it for the scheduler.

**Call relations**: The nested query builder is handed to `owner_candidates`, which is the common candidate mechanism.

*Call graph*: 1 external calls (owner_candidates).


##### `seated_member_workspaces.with_a_seated_member`  (lines 608–613)

```
def with_a_seated_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds workspaces containing at least one member with a seat. It describes eligibility but does not run the job.

**Data flow**: It returns a select statement of distinct workspace ids from member rows where `seated_at` is set.

**Call relations**: The enclosing candidate function passes it to the scheduler helper.

*Call graph*: 1 external calls (select).


##### `connection_workspaces`  (lines 618–631)

```
def connection_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a scheduler candidate set for workspaces whose main agent has a connector grant. Connection-driven jobs use it to avoid waking workspaces with no connected accounts.

**Data flow**: It creates a query builder and wraps it as workspace candidates.

**Call relations**: The nested `with_a_main_agent_connection` query is passed to `owner_candidates`.

*Call graph*: 1 external calls (owner_candidates).


##### `connection_workspaces.with_a_main_agent_connection`  (lines 623–629)

```
def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query for workspaces where the main agent has at least one connector grant. This is the database-level test for connection-backed work.

**Data flow**: It joins connector grants to agents, filters to main agents, and returns distinct workspace ids.

**Call relations**: It is used only through the enclosing `connection_workspaces` candidate seam.

*Call graph*: 1 external calls (select).


##### `agent_is_live`  (lines 634–647)

```
def agent_is_live(workspace_id: sa.ColumnElement[UUID], agent_id: sa.ColumnElement[UUID]) -> sa.ColumnElement[bool]
```

**Purpose**: Creates a SQL condition that checks whether an agent belongs to a workspace and is not archived. Sweeps use it before spending work on an agent that might later be refused.

**Data flow**: The caller gives SQL expressions for workspace id and agent id. The function returns an `exists` condition that can be placed inside a larger query.

**Call relations**: It is a reusable predicate for jobs that need to filter live agents before invoking work.

*Call graph*: 2 external calls (exists, select).


##### `awaiting_a_title`  (lines 653–669)

```
def awaiting_a_title() -> sa.ColumnElement[bool]
```

**Purpose**: Creates a SQL condition for conversations that still need an automatically summarized title. It only counts conversations where a member spoke and a turn completed.

**Data flow**: It returns a database condition requiring `title_summarized` to be false and at least one completed member-admitted turn to exist.

**Call relations**: `conversations_awaiting_title` and the untitled-workspace candidate query both use this shared condition.

*Call graph*: called by 2 (conversations_awaiting_title, with_an_unsummarized_title); 3 external calls (and_, exists, select).


##### `untitled_conversation_workspaces`  (lines 672–681)

```
def untitled_conversation_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a scheduler candidate set for workspaces that contain conversations needing title summaries. This keeps the title job from running where nothing needs naming.

**Data flow**: It wraps a query builder that selects workspace ids for unsummarized conversations.

**Call relations**: The nested function uses `awaiting_a_title`, and `owner_candidates` exposes the result to the dispatcher.

*Call graph*: 1 external calls (owner_candidates).


##### `untitled_conversation_workspaces.with_an_unsummarized_title`  (lines 678–679)

```
def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds workspaces with at least one conversation awaiting a title. It is the concrete filter for the title summarization job.

**Data flow**: It selects distinct conversation workspace ids where `awaiting_a_title` is true.

**Call relations**: The enclosing function hands it to the shared candidate system.

*Call graph*: calls 1 internal fn (awaiting_a_title); 1 external calls (select).


##### `unseeded_agent_workspaces`  (lines 684–713)

```
def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Builds a scheduler candidate set for workspaces whose agents have not all been settled by a once-per-agent sweep. It helps seeding jobs run until every agent has a marker.

**Data flow**: The caller gives an extension name and key prefix. The returned candidate object compares agent count with matching stored-marker count per workspace.

**Call relations**: The nested query builder does the counting. The scheduler uses the wrapped candidate set to choose workspaces.

*Call graph*: 1 external calls (owner_candidates).


##### `unseeded_agent_workspaces.with_an_unsettled_agent`  (lines 696–711)

```
def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds workspaces where agent rows outnumber settlement keys. This means at least one agent still needs the sweep’s work.

**Data flow**: It constructs subqueries counting agents and extension store keys, then returns workspace ids where agents are greater than settled keys.

**Call relations**: It is used by the enclosing `unseeded_agent_workspaces` function as the scheduler’s eligibility query.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 730–742)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Describes the interface for starting an internal agent turn from background code. It is a protocol, meaning this file states the expected shape without implementing the turn engine.

**Data flow**: Implementations receive conversation, agent, message, idempotency key, and admission options, then return a turn id or `None` if admission is refused.

**Call relations**: `ExtensionContext.invoke` calls whatever object implements this protocol. This avoids importing the full turn layer here.


##### `ModelResolver.auto_model`  (lines 752–752)

```
def auto_model(self) -> str
```

**Purpose**: Describes the property that returns the deployment’s default model id. `ModelAccess` uses it so background work calls the same configured model consistently.

**Data flow**: An implementation returns a string model name. No mutation is implied.

**Call relations**: It is part of the resolver protocol consumed by `ModelAccess.model`, `complete`, and `turn`.


##### `ModelResolver.pricing`  (lines 755–755)

```
def pricing(self) -> Pricing
```

**Purpose**: Describes the property that returns the model price table. Billing code needs it to turn token usage into cost.

**Data flow**: An implementation returns pricing information for models. The context only reads it.

**Call relations**: `ModelAccess.turn` uses it while recording billable usage.


##### `ModelResolver.client_for`  (lines 757–757)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Describes how to get a model client for a named model. The client is the object that actually talks to the language model provider.

**Data flow**: An implementation receives a model id and returns an asynchronous model client.

**Call relations**: `ModelAccess.turn` calls it before streaming a completion.


##### `ModelResolver.key_slot_for`  (lines 759–759)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Describes how to find which credential slot supplies a model’s provider key. This lets billing know whether the workspace used its own key.

**Data flow**: An implementation receives a model id and returns a slot name or `None`.

**Call relations**: `ModelAccess._serves_itself` and usage export code rely on this mapping.


##### `ModelResolver.provider_for`  (lines 761–761)

```
def provider_for(self, model: str) -> str
```

**Purpose**: Describes how to identify the provider behind a model, such as which model service is being used. Metrics use this as a label.

**Data flow**: An implementation receives a model id and returns a provider name.

**Call relations**: `ModelAccess.turn` includes this provider name in timing and token metrics.


##### `ModelAccess.model`  (lines 785–787)

```
def model(self) -> str
```

**Purpose**: Returns the default model that this background model access will call. It makes the chosen model visible without letting the caller override it here.

**Data flow**: It reads `auto_model` from the resolver and returns it.

**Call relations**: It is the simple read side of the model seam used by background handlers.


##### `ModelAccess.complete`  (lines 789–796)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs a model request and returns only the assistant’s text. It is a convenience method for jobs that do not need tool-call details.

**Data flow**: The caller gives a model request. The method calls `turn`, then extracts plain text from the returned assistant message.

**Call relations**: Memory extension summarizers use this path when they want text summaries. It relies on `ModelAccess.turn` for streaming, billing, and metrics.

*Call graph*: calls 1 internal fn (turn); called by 3 (_summarize, _write, _summarize).


##### `ModelAccess._serves_itself`  (lines 798–805)

```
async def _serves_itself(self, model: str) -> bool
```

**Purpose**: Checks whether the current workspace is using its own provider key for a model call. This determines whether platform billing should charge for provider usage.

**Data flow**: The caller gives a model id. The method opens a workspace transaction, checks key ownership through billing helpers, and returns a boolean.

**Call relations**: `ModelAccess.turn` calls this immediately before metering the model stream.

*Call graph*: called by 1 (turn); 3 external calls (workspace_owns_the_key, workspace_tx, ws_current).


##### `ModelAccess.turn`  (lines 807–908)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one full model turn for background work, including streamed text, tool calls, reasoning blocks, usage accounting, and metrics. It returns an assistant message in the same shape normal turns use.

**Data flow**: The caller gives a model request. The method fixes the model to the deployment default, streams events from the provider, assembles text and tool calls, records usage in a billable event, emits timing and token metrics, and returns a `Message`.

**Call relations**: `complete` uses it for text-only calls, and memory extension code uses it when tool-aware or structured assistant messages are needed. It calls `_serves_itself` so billing matches the key that served the request.

*Call graph*: calls 1 internal fn (_serves_itself); called by 3 (complete, _curate, _write); 10 external calls (__init__, __init__, __init__, __init__, model_copy, loads, monotonic, emit_histogram, emit_metric, ws_current).


##### `_source_readable`  (lines 972–999)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition saying whether a synced source is readable by a particular agent and member context. It combines workspace, deletion, subject, grant, and ownership rules.

**Data flow**: The caller gives a workspace id and reader description. The function returns a database condition to embed in source or page queries.

**Call relations**: `readable_page_states`, `readable_source_ids`, and `source_pages` all use this shared access rule so source visibility stays consistent.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `MemberContextRecord._aware_utc`  (lines 1043–1044)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a member context record’s date has timezone information. If a date is naive, it treats it as UTC.

**Data flow**: Pydantic passes in a datetime. The validator returns it unchanged if timezone-aware, or returns a UTC-marked copy if not.

**Call relations**: It runs automatically when `MemberContextRecord` objects are created.

*Call graph*: 1 external calls (replace).


##### `_member_blob_text`  (lines 1050–1068)

```
async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str
```

**Purpose**: Reads a small text preview from a blob without loading the entire file. It protects member context assembly from huge artifacts.

**Data flow**: The caller gives a blob store and blob key. The method streams up to a fixed byte limit, decodes it as text, and returns the decoded preview, trimming a partial final character if needed.

**Call relations**: `ExtensionContext.member_context` uses it for text artifacts and synced pages.

*Call graph*: calls 1 internal fn (get_stream); called by 1 (member_context).


##### `ExtensionContext.workspace_id`  (lines 1097–1098)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id for this context. It is the common shortcut used by methods that need the current workspace.

**Data flow**: It delegates to the context’s `ScopedStore` and returns that workspace id.

**Call relations**: Many `ExtensionContext` methods use this property to keep reads and writes scoped.


##### `ExtensionContext.image_preview_url`  (lines 1100–1114)

```
def image_preview_url(self, blob_key: str, size_bytes: int) -> str | None
```

**Purpose**: Creates a signed preview URL for an image blob when preview links are configured and the blob is eligible. This lets extension-owned rows show images without exposing signing secrets.

**Data flow**: The caller gives a blob key and size. The method combines the deployment secret, public base URL, and workspace id, and returns a URL or `None`.

**Call relations**: The sites extension uses it when rendering preview URLs. The actual signing is delegated to the artifact URL helper.

*Call graph*: called by 1 (_preview_url); 1 external calls (mint_image_preview_url).


##### `ExtensionContext.artifact_link`  (lines 1116–1122)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a shared artifact. It lets extensions show files to members without holding the artifact signing secret themselves.

**Data flow**: The caller gives a shared artifact record. The method passes the secret, base URL, workspace id, and artifact to the surface helper and returns a URL or `None`.

**Call relations**: Report digest object rendering calls this when building rows with downloadable artifacts.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_link).


##### `ExtensionContext.artifact_preview_link`  (lines 1124–1129)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed raster preview link for a shared artifact when that artifact can be previewed. It is used for safe inline previews.

**Data flow**: The caller gives an artifact. The method asks the surface helper to mint a preview link using the context’s signing settings.

**Call relations**: Report digest object rendering uses it alongside `artifact_link`.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_preview_link).


##### `ExtensionContext.scheduled_runs`  (lines 1131–1155)

```
async def scheduled_runs(self, member_id: UUID, *, agent_id: UUID | None, limit: int, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Reads scheduled turns visible to a member, including their final replies and shared files. It is used to build member-facing listings of automated work.

**Data flow**: The caller supplies member id, optional agent or turn filters, subject filters, and a limit. The method delegates to the surface read helper and returns scheduled run records.

**Call relations**: Report digest object pages call it to display recent scheduled runs.

*Call graph*: called by 2 (_one, _page); 1 external calls (scheduled_runs).


##### `ExtensionContext.home_url`  (lines 1157–1168)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link to the deployment’s browser portal home surface. It returns nothing if the deployment has no public base URL or no home surface.

**Data flow**: The caller may give a URL fragment. The method combines the base URL, surface name, and fragment into a portal URL.

**Call relations**: Coding, metronome, and sample extensions use it when sending users back to the product UI.

*Call graph*: called by 3 (github_installed, _billing_portal, _hook).


##### `ExtensionContext.workspace_agents`  (lines 1170–1202)

```
async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]
```

**Purpose**: Returns the workspace’s agents for privileged first-party jobs. It includes archived agents because some sweeps must settle every counted agent row.

**Data flow**: After checking that member-context reading is allowed, it queries agents in the current workspace and converts rows into `WorkspaceAgent` records.

**Call relations**: The web surface uses it while seeding homepages. It fails with `PermissionError` if the context was not wired for this privileged read.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.agent_visibilities`  (lines 1204–1216)

```
async def agent_visibilities(self) -> dict[UUID, AgentVisibility]
```

**Purpose**: Returns each agent’s portal visibility setting. This helps code decide the minimum audience for things attached to an agent.

**Data flow**: It queries agent ids and visibility values in the current workspace and returns a dictionary keyed by agent id.

**Call relations**: Unlike member roster reads, this workspace-shape information is not gated by member-context permission.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.earliest_seated_admin`  (lines 1218–1236)

```
async def earliest_seated_admin(self) -> UUID | None
```

**Purpose**: Finds a stable admin member to act on behalf of when background work needs an owner and none is attached. It chooses the earliest seated admin for deterministic behavior.

**Data flow**: After permission checks, it queries seated admins in this workspace ordered by seat time and id, returning the first id or `None`.

**Call relations**: The web surface uses it while seeding homepages for ownerless agents.

*Call graph*: called by 1 (seed_homepages); 2 external calls (select, workspace_tx).


##### `ExtensionContext.scheduled_member_timezone`  (lines 1238–1253)

```
async def scheduled_member_timezone(self) -> str
```

**Purpose**: Returns the timezone for the member tied to a scheduled job, defaulting to UTC if unset. It ensures scheduled work can interpret dates in the member’s local time.

**Data flow**: It checks that member context is allowed and a scheduled member is bound, then reads that member’s timezone row and returns it.

**Call relations**: It is available only when the context was created for scheduled member-aware work.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.member_context`  (lines 1255–1411)

```
async def member_context(self, *, since: datetime, limit: int=200, exclude_conversation_id: UUID | None=None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Builds a bounded bundle of recent information visible to the scheduled member, such as conversations, artifacts, synced pages, memories, tasks, and open objectives. This gives scheduled work useful context without handing it all workspace data.

**Data flow**: The caller gives a starting date, limit, and optional conversation to exclude. The method checks permissions, reads visible conversation turns, artifact previews, readable page text, and extension records, then sorts and trims the combined records.

**Call relations**: It calls `_member_blob_text` for blob previews and `_member_extension_records` for memory/objective information. It uses audience rules so the scheduled member only receives context they can read.

*Call graph*: calls 2 internal fn (_member_extension_records, _member_blob_text); 5 external calls (__init__, exists, select, workspace_tx, readable_audiences).


##### `ExtensionContext._member_extension_records`  (lines 1413–1664)

```
async def _member_extension_records(self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int, exclude_conversation_id: UUID | None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Reads member-visible records stored by certain extensions, especially memories, tasks, and open objectives. It fills in context that is not stored in the core conversation tables.

**Data flow**: The caller gives member, audience, time, limit, and exclusion information. The method queries extension-owned tables, computes whether objectives are still open, and returns `MemberContextRecord` objects.

**Call relations**: `member_context` calls this as its final source of records. It uses table definitions built locally to avoid importing those extensions directly.

*Call graph*: called by 1 (member_context); 8 external calls (__init__, sha256, DateTime, column, or_, select, table, workspace_tx).


##### `ExtensionContext.retitle_conversation`  (lines 1666–1669)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Sets a conversation title in the current workspace. It is for jobs that derive a better title from conversation content.

**Data flow**: The caller gives a conversation id and title. The method passes the workspace id, conversation id, and title to the surface helper.

**Call relations**: This is a direct context wrapper around the core retitling function.

*Call graph*: 1 external calls (retitle_conversation).


##### `ExtensionContext.conversations_awaiting_title`  (lines 1671–1695)

```
async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]
```

**Purpose**: Finds recent conversations in this workspace that still need a summarized title. It bounds how much title work a job takes per tick.

**Data flow**: The caller gives a limit. The method queries matching conversations using `awaiting_a_title`, orders newest first, and returns their ids.

**Call relations**: The web surface title summarizer calls this before generating titles.

*Call graph*: calls 1 internal fn (awaiting_a_title); called by 1 (summarize_chat_titles); 2 external calls (select, workspace_tx).


##### `ExtensionContext.summarized_conversation_title`  (lines 1697–1702)

```
async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Writes a summarized title and marks that summarization has been attempted. This removes the conversation from future title-summary work.

**Data flow**: The caller gives a conversation id and title. The method delegates to the core summary-title helper with the current workspace id.

**Call relations**: The web surface title summarizer calls it after producing a title.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (summarize_conversation_title).


##### `ExtensionContext.pending_usage_exports`  (lines 1704–1723)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns this extension’s pending billing usage exports, minting new export intents first. It is the read side of a reliable external billing handoff.

**Data flow**: The caller gives a floor date and limit. The method opens a transaction, freezes eligible usage deltas into export records, and returns unacknowledged exports.

**Call relations**: It requires a model key-slot resolver so usage can be classified correctly. `ack_usage_exports` is the partner method after delivery succeeds.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 1725–1734)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered so they do not appear in the pending list again. It should be called only after the external receiver accepts them.

**Data flow**: The caller gives export records. If the list is non-empty, the method marks them acknowledged in a workspace transaction.

**Call relations**: It completes the workflow started by `pending_usage_exports`; unacknowledged exports are intentionally read again for retry.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 1737–1749)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides a database transaction for an extension’s own tables and approved SDK helpers. It is powerful and therefore documented as not automatically restricted beyond the workspace transaction boundary.

**Data flow**: The caller enters the async context manager. It yields a database connection, commits on normal exit, and rolls back if an error occurs.

**Call relations**: Memory, metronome, report digest, and other extension object readers use it to query their own tables.

*Call graph*: called by 15 (_item, _page, _entry, _page, _billing_autopay, _billing_projection, _billing_status, _entries, _task_names, record_sources (+5 more)); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 1751–1788)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Starts an internal agent turn through the wired turn invoker. It lets background jobs wake an agent in a specific conversation with idempotency and admission safeguards.

**Data flow**: The caller supplies conversation, agent, message, idempotency key, and optional admission controls. The method checks that an invoker exists and delegates, returning a turn id or `None`.

**Call relations**: Sources and web surface code call it to fire background-triggered turns. The actual turn engine lives behind the `TurnInvoker` protocol.

*Call graph*: called by 2 (_fire_trigger, seed_homepages).


##### `ExtensionContext.tail`  (lines 1790–1799)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn until the turn ends. It lets side-channel work watch progress without direct access to the hub.

**Data flow**: The caller gives a turn id and optional cursor. The method returns an async context manager from the wired tailer or raises if none is available.

**Call relations**: It delegates entirely to the injected `TurnTailer`, keeping hub subscription details out of extension code.


##### `ExtensionContext.turn_is_terminal`  (lines 1801–1815)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final status. A missing turn is treated as terminal because there is nothing still running to observe.

**Data flow**: The caller gives a turn id. The method reads the turn status in the current workspace and returns true if missing or terminal.

**Call relations**: It is useful before side-channel code speaks for or follows a turn.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 1817–1821)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in this workspace, or `None` if the conversation id is not local. It helps handlers resolve opaque conversation ids safely.

**Data flow**: The caller gives a conversation id. The method calls `conversation_agent_id` with the current workspace id and returns the result.

**Call relations**: It exposes the shared conversation-to-agent lookup through the context.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 1823–1858)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Reads audience and surface-label facts for several conversations. Member-facing listings use these facts to decide visibility and origin labels.

**Data flow**: The caller gives conversation ids. The method queries matching conversations in this workspace and returns a dictionary of `ConversationFacts` by id.

**Call relations**: It parses stored audience strings into audience objects. Missing ids are left absent, which callers should treat as not visible.

*Call graph*: 4 external calls (__init__, select, workspace_tx, parse_audience).


##### `ExtensionContext.conversation_arrival_seq`  (lines 1860–1887)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Returns the latest member-arrival sequence number for a conversation. This acts as a watermark so background work can tell whether a member spoke after it armed itself.

**Data flow**: The caller gives a conversation id. The method queries the maximum member-sourced inbound sequence in this workspace and returns it, or 0 if none exists.

**Call relations**: Invocation admission controls can use this watermark to avoid waking work after newer member activity.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1889–1915)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads final status and terminal text for several turns. It lets status listings show what happened last without one query per turn.

**Data flow**: The caller gives turn ids. The method queries matching turns in this workspace and returns `TurnOutcome` objects keyed by id.

**Call relations**: The web surface uses it while seeding homepages. Missing turns are simply absent from the result.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 1917–1923)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Reports whether the current workspace belongs to the platform operator. This gates operator-only information such as debugging or spend details.

**Data flow**: It reads the workspace domain and compares it with the configured operator email domain, returning a boolean.

**Call relations**: It uses the same domain lookup as surface-level operator checks.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 1925–1989)

```
async def open_conversation(self, agent_id: UUID, key: str, member_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for a workflow key and agent. This gives repeated events for the same subject one durable conversation history.

**Data flow**: The caller gives an agent id, key, and optional member id. The method verifies the agent belongs to the workspace, inserts the conversation if missing, and returns the existing or new conversation id.

**Call relations**: Sources and web seeding code call it before invoking turns. `invoke` starts turns; this method only creates or finds the room they run in.

*Call graph*: called by 2 (_fire_trigger, seed_homepages); 4 external calls (select, workspace_tx, conversation_audience, uuid4).


##### `ExtensionContext.agent_name`  (lines 1991–2005)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the name of the currently bound agent, using archived name when needed. Agent-scoped object kinds use it for display.

**Data flow**: It reads the current agent scope, queries that agent in the workspace, and returns its display name.

**Call relations**: It depends on an agent scope already being bound by the caller’s runtime.

*Call graph*: 3 external calls (select, agent_current, workspace_tx).


##### `ExtensionContext.page_states`  (lines 2007–2036)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Returns current state for named live synced pages in this workspace. It is a lightweight way to check subject, revision, digest, body reference, title, and stream.

**Data flow**: The caller gives page ids. The method filters to non-tombstoned pages in this workspace and returns `PageState` objects by id.

**Call relations**: It is the unrestricted workspace-scoped page-state read; `readable_page_states` adds reader visibility checks.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 2038–2076)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Returns page state only for pages a given reader may read. It adds source authority and subject checks to the basic page-state lookup.

**Data flow**: The caller gives page ids and a `SourceReader`. The method joins pages to sources, applies `_source_readable`, and returns matching `PageState` objects.

**Call relations**: Memory object readers use it before showing or using synced page content.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 2078–2083)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the ids of live sources readable by a given reader. It is a compact access check for source-backed features.

**Data flow**: The caller gives a `SourceReader`. The method applies `_source_readable` to the source table and returns a frozen set of ids.

**Call relations**: It shares the same source visibility rule as `source_pages` and `readable_page_states`.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 2085–2270)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers or revives a content-sync source for this workspace, then grants an agent access to it. It prevents duplicate sync rows for the same authority while refusing unsafe changes to ownership, disclosure, or requested fields.

**Data flow**: The caller gives backend, typed config, subject, owner, optional connection, and optional target agent. The method computes the source id, validates the agent and connection, inserts or revives the source row, checks conflicts, grants the agent, and returns the source id.

**Call relations**: The sample extension setup calls it. It uses `source_id` to derive stable row identity and writes both `source` and `source_grant` rows in one transaction.

*Call graph*: calls 1 internal fn (source_id); called by 1 (_setup); 5 external calls (now, model_dump, select, update, workspace_tx).


##### `ExtensionContext.grant_source`  (lines 2272–2328)

```
async def grant_source(self, source_id: UUID, *, agent_id: UUID, actor_member_id: UUID) -> None
```

**Purpose**: Grants another agent access to an already registered source without creating a second syncing row. It enforces that the actor owns the private source or that the source is shared.

**Data flow**: The caller gives a source id, target agent, and actor member. The method verifies the source, permission, and target agent, then inserts a source grant if missing.

**Call relations**: It complements `register_source`, which grants only during registration.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.source_id`  (lines 2330–2348)

```
def source_id(self, backend: str, config: BaseModel, *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Computes the deterministic id that a source registration would use. Callers can identify the row for a source before it exists.

**Data flow**: The caller gives backend, config, and optional connection id. The method dumps the config and hashes workspace, backend, config identity fields, and connection into a UUID.

**Call relations**: `register_source` calls it before writing the source row. It also pairs with `removed_source_ids` for detecting previously removed sources.

*Call graph*: called by 1 (register_source); 2 external calls (model_dump, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 2350–2373)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which of a set of source ids are explicitly removed in this workspace. Absence is not treated as removal.

**Data flow**: The caller gives source ids. The method queries rows with `removed_at` set and returns the matching ids as a frozen set.

**Call relations**: Callers use it when deciding whether to revive or ignore a source that may have been deleted by a member.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 2375–2419)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists this workspace’s live registered sources, optionally for one backend. Removed sources are hidden.

**Data flow**: The caller may give a backend name. The method queries live source rows, orders them, and returns `SourceRecord` value objects.

**Call relations**: Gbrain and source-tool code use it to discover existing bindings.

*Call graph*: called by 2 (_registered_from_ext, _bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 2421–2469)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a particular agent/member reader. It applies both page visibility and source authority rules.

**Data flow**: The caller gives a `SourceReader`. The method joins pages to sources, filters by workspace, subject, tombstone, and `_source_readable`, then returns `PageRecord` objects.

**Call relations**: It is the page listing side of the source registration and sync system.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 2471–2487)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Tombstones one live page so downstream indexing cleanup can remove derived state. It is the page-level forget operation.

**Data flow**: The caller gives a page id. The method marks that page tombstoned in the current workspace and raises if no live page matched.

**Call relations**: It follows the same cleanup model as removing a source: mark the page and let page-change processing reap derived data.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 2489–2526)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a source by marking it removed, deleting its grants, and tombstoning its live pages. The row remains so references and future revival are possible.

**Data flow**: The caller gives a source id. The method updates the source, deletes grants, tombstones pages in one transaction, and raises if the source was not live here.

**Call relations**: It is the source-level counterpart to `forget_page` and triggers the existing page cleanup pipeline through tombstones.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 2528–2554)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for live sources and their live pages together. This keeps source and page visibility from being torn between old and new subjects.

**Data flow**: The caller gives source ids and a new subject. The method updates matching live sources, updates their pages with a fresh timestamp, and raises if none matched.

**Call relations**: Updating page timestamps causes downstream page-change replay to re-index under the new subject.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 2556–2629)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Updates non-identity source configuration fields, optionally forcing selected sources to refetch from scratch. It refuses changes that would make a source’s id no longer match its config.

**Data flow**: The caller gives a map of source ids to new configs and optional ids to refetch. The method locks live rows, validates identities, writes configs, and clears cursor/claim state for refetch rows.

**Call relations**: It uses `source_row_id` to prove the row remains the same source. This supports changing windows or backfill settings without duplicating source rows.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 2631–2660)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Moves live sources’ next sync time to now, so the sync driver will pick them up soon. It also clears parking caused by provider refusals.

**Data flow**: The caller gives source ids. The method updates matching live sources with `next_sync_at` now, clears parking/refusal fields, and raises if none matched.

**Call relations**: It is the approved on-demand resync path for source integrations.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 2662–2668)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Creates a governed proposal for changing an agent, instead of directly changing agent configuration. This supports review and digest checks before applying prompt changes.

**Data flow**: The caller gives an `AgentChange`. The method creates a `Governance` helper for this workspace and extension and returns the proposal reference it creates.

**Call relations**: The sample extension tick calls it when proposing a change.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 2670–2675)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace’s trajectory corpus through the context. It fails loudly if no corpus reader was wired.

**Data flow**: It checks that `corpus` exists, then returns the result of `corpus.trajectories()`.

**Call relations**: The sample extension tick uses it. It is the context-level doorway to `TrajectoryCorpus`.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 2678–2749)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: WorkspaceBlobStore | None=None, sandboxes:
```

**Purpose**: Builds the `ExtensionContext` object that a handler receives. It wires together only the capabilities available for that extension, job, deployment, and runtime.

**Data flow**: The caller supplies extension name, declared credentials, optional backends, stores, model resolver, sandbox access, URLs, and permission flags. The function validates model attribution, constructs the capability objects, and returns one frozen context.

**Call relations**: This is the assembly point for the file. Core jobs and extensions get the same context shape, so first-party and extension code travel through the same safety gates.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/ext/conversation_slots.py`

`data_model` · `cross-cutting`

This file is mostly a set of safety rules and shared data contracts. A “conversation slot” is a piece of extra conversation-related information that the portal can display, such as files produced during the chat, web sources, task progress, or generated sites. Without this file, each extension could send slightly different data, unsafe links, or counts that do not add up, and the portal would have a harder time displaying the information reliably.

The file uses Pydantic models, which are Python classes that validate incoming data before the rest of the system trusts it. Most models are frozen, meaning they cannot be changed after creation, and they reject unexpected fields. This makes the slot data behave like a sealed package with a known label and contents.

Several limits protect the system and user interface from oversized text or huge lists. URL validators require ordinary HTTP or HTTPS links and reject embedded usernames or passwords. Image preview URLs are checked even more strictly because browsers will draw them inside portal pages, so the code blocks fragments, control characters, backslashes, and credential-style URLs.

At the end, the file defines provider and context objects. These are the bridge between an extension and the portal: the context says what conversation is being viewed, and the provider says how to summarize and read one slot’s data.

#### Function details

##### `ImagePreview.drawable_url`  (lines 52–69)

```
def drawable_url(cls, value: str) -> str
```

**Purpose**: This validates that an image preview URL is safe and usable by a browser. It exists because preview images may be drawn inside portal pages, so the link must not contain credentials, hidden control characters, or browser-confusing parts.

**Data flow**: A URL string comes in when an ImagePreview is created. The function splits the URL into parts, decodes escaped characters, and checks that it is an HTTP or HTTPS URL with a host, no username or password, no fragment after a #, no backslashes, and no control characters. If the URL passes, the same string comes out; if not, model creation fails with a clear validation error.

**Call relations**: Pydantic calls this validator automatically while building an ImagePreview. Inside the check, it relies on urlsplit to understand the URL, unquote to reveal hidden encoded characters, and unicodedata.category to detect control characters that should not appear in a drawable browser URL.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 85–96)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: This validates the optional download or viewing URL for an artifact, such as a file produced during a conversation. It keeps artifact links limited to normal web URLs and blocks credentials embedded in the link.

**Data flow**: An artifact URL, or None, comes in when a ConversationArtifact is created. If there is no URL, it stays as None. If there is a URL, the function splits it into parts and checks that it uses HTTP or HTTPS, has a host name, and does not include a username or password. A valid URL is returned unchanged; an invalid one stops the artifact from being accepted.

**Call relations**: Pydantic calls this validator during ConversationArtifact creation. The function hands the URL to urlsplit so it can make decisions based on the URL’s scheme, host, and credential fields rather than guessing from raw text.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 117–126)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validates the URL for a cited source shown in a conversation. It makes sure source links are ordinary HTTP or HTTPS pages and do not smuggle login details inside the URL.

**Data flow**: A source URL comes in as text. The function breaks it into URL parts, checks for an allowed scheme, a real host name, and no username or password, then returns the original URL if it is acceptable. If the link is malformed or unsafe by these rules, it raises an error and the source payload is rejected.

**Call relations**: Pydantic invokes this check automatically when a ConversationSource is built. It uses urlsplit to inspect the link in a structured way before the source can be included in a SourcesSlotPayload.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 155–169)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: This checks that a task list’s summary numbers match the visible tasks. It prevents the portal from showing impossible progress, such as more completed tasks than total tasks.

**Data flow**: A TasksSlotPayload comes in after its individual fields have been read. The function compares total_count, completed_count, the number of visible tasks, each visible task’s status, and the truncated flag. If all numbers make sense together, it returns the same payload. If any count contradicts the others, it raises a validation error.

**Call relations**: Pydantic calls this model-level validator after creating the basic TasksSlotPayload fields. It acts as the final consistency check before task progress is handed to the portal for display.


##### `ConversationSite.http_url`  (lines 184–193)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validates the public URL for a conversation-created site. It ensures the portal only receives normal HTTP or HTTPS site links without embedded credentials.

**Data flow**: A site URL comes in as text when a ConversationSite is created. The function splits the URL into parts, confirms it has an HTTP or HTTPS scheme and a host, and rejects any username or password included in the URL. A valid link is returned unchanged; an invalid one causes validation to fail.

**Call relations**: Pydantic runs this validator while building a ConversationSite. The function uses urlsplit to examine the URL before that site can appear inside a SitesSlotPayload.

*Call graph*: 1 external calls (urlsplit).


### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and extension loading`

This file is like the checklist an add-on hands to the main program at startup. Instead of an extension directly registering itself by calling many core APIs, it returns a frozen Manifest object: a bundle of facts saying “I provide these tools,” “I need these credentials,” “I add this background job,” or “I react to this event.” A Pack is similar, but it groups several extensions into one product-shaped setup.

Most of the file is made of small frozen data classes. “Frozen” means they are meant to be read, not changed later. This matters because the loader can safely gather all declarations, validate them, and build the runtime from a stable description.

The declarations cover many seams in the system. Some describe user-facing features, such as shipped agents, prompt sections, conversation slots, and skills. Some describe infrastructure, such as sandbox carriers, browser providers, terminal transports, feature flags, search backends, and memory indexes. Others describe safety and access boundaries, such as credential slots, auth proxy injection, connector providers, and lifecycle hooks.

A few helper functions validate global rules across all active manifests. For example, conversation slot IDs must be unique, and only one open connector namespace may exist. Without these checks, two extensions could claim the same name or catch-all behavior, leaving the system unable to know which one owns a request.

#### Function details

##### `HookSpec.__post_init__`  (lines 556–558)

```
def __post_init__(self) -> None
```

**Purpose**: This checks one safety rule when a HookSpec is created: only hooks for user-submitted prompts may be marked as “best effort.” A best-effort hook is allowed to fail without blocking the user, and the file deliberately limits that relaxed behavior to prompt-time context injection.

**Data flow**: A new HookSpec comes in with an event name and a best_effort flag. The function checks whether best_effort is true while the event is anything other than user_prompt_submit. If that invalid combination appears, it raises an error; otherwise the HookSpec remains usable.

**Call relations**: This runs as part of creating a HookSpec declaration. Later, the hook system can trust that tool-gating hooks are never accidentally marked as best effort, so a tool safety check cannot fail open.


##### `AgentProvision.__post_init__`  (lines 590–611)

```
def __post_init__(self) -> None
```

**Purpose**: This validates a shipped agent declaration before it can be accepted. It makes sure the agent has a safe internal name, a valid icon if one is provided, a real prompt, a clear purpose, and the setup tools needed if the agent is expected to help obtain its own connections.

**Data flow**: A new AgentProvision comes in with a name, AgentSpec, optional tool allowlist, setup requirements, icon, and main-agent flag. The function checks the name against the allowed object-name pattern, checks the icon against the portal’s allowed marks, confirms that the prompt and purpose are not blank, and, when setup connectors are declared with a tool allowlist, verifies that the allowlist includes the setup tools. If any rule fails, it raises an error; otherwise the provision can be loaded.

**Call relations**: This runs when an extension declares a durable workspace agent. It protects the later activation flow: by the time the loader or workspace setup creates the agent row, the declaration already has the minimum information a user needs and the tools needed for its own setup path.


##### `conversation_slot_declarations`  (lines 759–788)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: This gathers all conversation slot providers from the active manifests and validates them as one shared namespace. Conversation slots are typed pieces of conversation-related data that extensions can show or summarize, so their IDs and display details must be unambiguous.

**Data flow**: The function receives a tuple of Manifest objects. It walks through each manifest’s conversation_slots, checks that each provider has a valid ID, short non-empty label, known icon, callable read and summarize callbacks, and a supported content type. It also records which extension owns each ID and rejects duplicates. It returns a tuple of pairs: the manifest and the provider it declared.

**Call relations**: This is used when the system assembles the active extension set. It turns many per-extension declarations into one validated list that the rest of the application can safely expose, knowing that no two extensions claimed the same conversation slot ID.


##### `open_connector_namespace`  (lines 791–803)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This finds the single catch-all connector namespace, if one is declared. A catch-all connector namespace can resolve connector provider slugs that were not registered one by one, so there must be at most one or routing would be ambiguous.

**Data flow**: The function receives the active manifests and scans their connector_resolver fields. If none are present, it returns None. If it finds one, it remembers it. If it finds a second one, it raises an error because the system would not know which resolver owns an unregistered connector slug.

**Call relations**: This belongs to startup assembly of connector support. Later connect flows, connector registry lookups, and related routing can use the single returned namespace for unknown provider slugs instead of repeating the conflict check each time.


##### `declared_slots`  (lines 826–839)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: This turns every extension’s credential-slot declarations into the simpler DeclaredSlot records used by shared views such as credential objects and the portal credentials panel. A credential slot is a named secret an extension says it may need, like an API key or provider token.

**Data flow**: The function receives active manifests. For each manifest, it reads each CredentialSlot and creates a DeclaredSlot with the slot name, description, owning extension name, whether a member may fill it manually, and the host from its injection target if there is one. It returns all of those DeclaredSlot objects as a tuple.

**Call relations**: This is the common projection from extension manifests into credential-facing parts of the system. It calls DeclaredSlot.__init__ to build each output record, so downstream code can work with one uniform credential declaration shape instead of the richer manifest-only CredentialSlot objects.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/models/interface.py`

`data_model` · `request construction and model streaming`

This file is the contract between the rest of the system and any AI model service, such as Anthropic or OpenAI. It defines the shapes of the things that travel across that boundary: user and assistant messages, text blocks, image blocks, tool calls, tool results, model reasoning blocks, usage records, and streamed response events. In plain terms, it is the packing list for every model conversation.

The central request type is `ModelRequest`. It says which model to use, what system instructions to send, what conversation history to include, which tools are available, how many tokens the model may spend, and how much reasoning effort to request. It also checks one important rule: if the caller forces the model to use a specific tool, that tool must actually be in the offered tool list.

The `ModelClient` protocol is the promise every real model client must keep: given a `ModelRequest`, it streams back model events. A protocol is like a job description; different providers can implement it in their own way as long as they produce the same kind of events.

The file also protects requests from image limits. Some providers cap how many images or how many image bytes can be sent. `trim_images` keeps the newest useful images and replaces older or oversized ones with a note. `omit_images` replaces all images when a model only accepts text.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool`  (lines 151–156)

```
def _forced_choice_names_an_offered_tool(self) -> 'ModelRequest'
```

**Purpose**: This validates a `ModelRequest` after it is built. If the request says the model must use a particular tool, it makes sure that tool was actually offered, so the system does not send an impossible instruction to the model provider.

**Data flow**: It reads the request's `tool_choice` field and its list of `tools`. If no forced tool was requested, it leaves the request unchanged. If a forced tool was named, it checks the offered tool names; a match lets the request continue, while no match turns into a clear validation error.

**Call relations**: This runs as part of Pydantic's request validation when a `ModelRequest` is created. It does not hand work to other project functions; it acts as a gatekeeper before any model client sends the request onward.


##### `ModelClient.complete`  (lines 203–203)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the required interface for anything that wants to behave like a model client. Given a complete model request, it must stream back events such as text, tool-call pieces, reasoning blocks, or usage information.

**Data flow**: A caller provides a `ModelRequest` containing the conversation, tools, model name, and settings. A concrete provider client consumes that request and yields `ModelEvent` items over time, rather than waiting for one finished response all at once.

**Call relations**: Other parts of the system can call `complete` without caring whether the actual provider is Anthropic, OpenAI, or something else. Provider-specific clients implement this method and translate the shared request and event types into their own network format.


##### `trim_images`  (lines 211–242)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares messages for models that accept images but enforce strict image limits. It keeps the most recent images that fit the count and byte budgets, and replaces dropped images with a short note so the model knows something was left out.

**Data flow**: It takes the full tuple of conversation messages. First it finds every image, including images nested inside tool results. Then it decides which images fit per-message limits, total request limits, and the overall byte budget, favoring newer images. It returns either the original messages if nothing must change, or copied messages where removed images are replaced by text placeholders.

**Call relations**: Before a provider client sends a request, this function can be used to make the shared message history safe for image-capable providers. It asks `_image_positions` where the images are, uses `_image_data_len` to spend the byte budget, and asks `_trim_message` to build the cleaned message copies.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `omit_images`  (lines 245–253)

```
def omit_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares messages for a model that cannot accept images at all. Instead of silently deleting them, it replaces each image with a clear text marker saying the image was omitted because the model only accepts text.

**Data flow**: It receives the conversation messages and finds every image position. If there are no images, it returns the messages unchanged. If there are images, it returns copied messages where each image has been swapped for the unsupported-image note.

**Call relations**: A text-only model path can call this before sending the request. It relies on `_image_positions` to find images and `_trim_message` to make the replacement while preserving the rest of each message.

*Call graph*: calls 2 internal fn (_image_positions, _trim_message).


##### `_image_data_len`  (lines 256–267)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This helper measures the encoded size of one image already known to exist in the message list. It is used so `trim_images` can stop sending images once the request would exceed the provider's total image byte budget.

**Data flow**: It receives the full messages tuple and one image position made of a message index, a block index, and optionally a nested sub-index. It follows that address to either a top-level image or an image inside a tool result, then returns the length of the image's base64 data string. If the address does not point to an image, it raises an error because the caller's bookkeeping is wrong.

**Call relations**: Only `trim_images` calls this, after `_image_positions` has already mapped where images live. It provides the size information that lets `trim_images` keep newer images while staying under the byte limit.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 270–290)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This helper makes a map of where every image appears in the conversation. It looks both for direct image blocks and for images embedded inside a tool result, because both count against provider limits.

**Data flow**: It reads the messages from oldest to newest. Plain string messages are skipped. Structured messages are scanned block by block; each image is recorded as a small address showing which message, which block, and, for nested tool-result images, which inner part contains it. It returns the list of these addresses in conversation order.

**Call relations**: `trim_images` uses this map to decide which images survive provider limits, and `omit_images` uses it to replace every image for text-only models. It is the shared image-finding step for both cleanup paths.

*Call graph*: called by 2 (omit_images, trim_images).


##### `_trim_message`  (lines 293–320)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]], replacement: str) -> Message
```

**Purpose**: This helper creates a safe copy of one message with selected images replaced by a text note. It preserves the rest of the message, including non-image blocks and images that are still allowed.

**Data flow**: It receives one message, the message's index, a set of image positions to remove, and the replacement text. If the message is plain text, it returns it unchanged. If the message has structured blocks, it walks through them and swaps any dropped top-level image for a `TextBlock`; for tool results with nested content, it copies the tool result while replacing only the dropped inner images. It returns a copied `Message` with the updated content.

**Call relations**: `trim_images` and `omit_images` both call this after deciding which image positions should disappear. It creates new model objects rather than editing the originals in place, which keeps earlier conversation data from being accidentally changed.

*Call graph*: called by 2 (omit_images, trim_images); 2 external calls (__init__, model_copy).
