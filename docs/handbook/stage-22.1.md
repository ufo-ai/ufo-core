# Core extension, model, object, and turn contracts  `stage-22.1`

This stage is shared behind-the-scenes support. It defines the basic “forms” and rules that many other parts of UFO rely on, so the core system, extensions, background workers, and user screens all agree on what things mean.

The extension context is the safe toolbox given to an extension or background job. It limits that code to the current workspace’s approved data, credentials, conversations, models, sources, and transcripts. The manifest file is the extension’s menu of promises: it describes what the extension adds, such as tools, jobs, routes, hooks, credentials, or onboarding steps. Conversation slots define the side panels an extension can show next to a chat, like files, tasks, sources, or workspace changes.

The model interface gives UFO one common way to talk to different AI providers. It standardizes requests, streamed replies, images, reasoning text, and tool calls. The object name helper enforces one rule for safe names before they are stored or linked. The records schema defines conversation “turns,” meaning one unit of work, so queues, workers, and user views all track the same states and results.

## Files in this stage

### Scoped extension runtime
Defines the safe workspace-bound context object that extensions and background jobs use to access core UFO services.

### `core/src/ufo/ext/context.py`

`orchestration` · `cross-cutting: active whenever an extension, job, hook, or background handler runs`

This file is the border guard between extension code and the core system. An extension should not get a raw database connection, every secret, or access to every workspace. Instead, it gets an ExtensionContext: a carefully shaped object full of smaller capabilities. Think of it like giving a contractor a keycard that opens only the rooms needed for the job, not the master key to the building.

The file provides a durable key-value store for one extension, a credential reader that refuses undeclared secret slots, read-only access to conversation transcripts, controlled writing into conversation sandboxes, short-lived off-turn command probes, metered model calls, usage export helpers, source registration and page listing, and safe ways to open or invoke conversations.

Most methods derive the workspace from the ambient run scope, rather than accepting a workspace id from the caller. That matters because it prevents accidental or malicious cross-tenant access. Many reads are also batched, so listings can ask for many facts at once without one database round trip per row.

The file is also a wiring point. The context can include optional services such as indexing, embeddings, blob storage, sandboxes, model access, turn invocation, and live turn tailing. If a service is not wired, methods fail loudly instead of pretending there is no data.

#### Function details

##### `ScopedStore.workspace_id`  (lines 93–94)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id currently bound to the running job or turn. This keeps the store tied to the current workspace without letting callers choose another one.

**Data flow**: It reads the ambient workspace scope and returns that workspace's id. It does not take input and does not change stored data.

**Call relations**: Other ScopedStore methods use this value when building database queries, so every read or write lands in the current workspace's part of the extension store.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 96–107)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one JSON value from this extension's private key-value store. It is used when an extension needs durable state, such as saved browser or web-surface information.

**Data flow**: It takes a key, opens a workspace-scoped database transaction, looks for a row matching the current workspace, this extension, and that key, then returns the stored value or null if none exists.

**Call relations**: Browser, Browserbase, Slack, and web surface code call this when they need previously saved extension state. It relies on the workspace transaction helper and SQL selection to keep the read scoped.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 109–124)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several named keys from the extension store in one database query. This is useful for listings that know the exact keys they need and should not scan the whole store.

**Data flow**: It receives a list of keys. If the list is empty, it returns an empty dictionary. Otherwise it fetches matching rows for the current workspace and extension, then returns a key-to-value dictionary for the keys that exist.

**Call relations**: It is a batched companion to ScopedStore.get. Callers use it through the store capability when they want fewer database trips and no broad store listing.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 126–147)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Stores or replaces one JSON value in this extension's private store. It gives extensions a simple durable memory tied to the current workspace.

**Data flow**: It takes a key and value. It first tries to update an existing row for this workspace and extension. If no row was updated, it inserts a new row with timestamps.

**Call relations**: Browser, Browserbase, and web surface code call this to remember state. It uses the same workspace transaction path as reads, so writes follow the same scoping rules.

*Call graph*: called by 3 (_start, _context, _open_conversation); 3 external calls (insert, update, workspace_tx).


##### `ScopedStore.put_if`  (lines 149–199)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the currently stored value is still what the caller expected. This prevents one worker from overwriting newer data with an old copy.

**Data flow**: It takes a key, a new value, and an expected old value. If expected is null, it attempts an insert only if no row exists. Otherwise it locks the row, compares the stored value, updates only on a match, and returns true or false to say whether the write happened.

**Call relations**: Slack reply checkpoint and progress code use this when several updates could race. It builds on database locking and conflict-safe insert behavior.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 201–209)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes one key from this extension's private store. It is used when remembered state is no longer valid.

**Data flow**: It takes a key, opens a workspace-scoped transaction, and removes the row matching the current workspace, this extension, and that key. It returns nothing.

**Call relations**: The web surface calls this while opening conversations when stale state must be cleared. It follows the same scoped database path as get and put.

*Call graph*: called by 1 (_open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 211–224)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists this extension's stored keys and values, optionally limited to keys starting with a prefix. This supports extension-owned listings without exposing other extensions' state.

**Data flow**: It takes an optional prefix, queries rows for the current workspace and extension whose keys begin with that prefix, sorts them by key, and returns pairs of key and value.

**Call relations**: Web audience code uses this to discover saved grants. The prefix filter keeps callers from reading more of their own key space than needed.

*Call graph*: called by 2 (_granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 240–241)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the currently bound workspace id for credential operations. Credential lookups are always tied to the workspace running the handler.

**Data flow**: It reads the ambient workspace scope and returns its id. It does not accept caller-supplied workspace information.

**Call relations**: Credential resolution and installation binding use this to make sure secrets are looked up or sealed for the current workspace only.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 243–249)

```
async def get(self, slot: str) -> str
```

**Purpose**: Reads a declared credential slot from the current workspace. It refuses access if the extension did not declare that slot in its manifest.

**Data flow**: It takes a slot name. If the slot is not declared, it raises an error before any secret lookup. If declared, it asks the current workspace for the credential value and returns it.

**Call relations**: Extension handlers use this through the credentials capability. The undeclared-slot check is the important gate before reaching workspace secrets.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 251–258)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Reports whether a declared credential comes from the workspace's own stored secret rather than a platform default. This helps billing and provider-cost decisions.

**Data flow**: It takes a slot name, checks that the slot was declared, then asks the current workspace whether that slot has a stored value. It returns a boolean.

**Call relations**: Handlers use this when they need to know whose provider key paid for an action. It shares the same declaration gate as CredentialAccess.get.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.resolve`  (lines 260–273)

```
async def resolve(self, slot: str) -> str
```

**Purpose**: Resolves a declared credential slot using any manifest-defined credential source, falling back to the normal workspace credential. It supports credentials that may come from connected installations or other configured sources.

**Data flow**: It takes a slot name, rejects undeclared slots, looks for a matching declared source, and if one exists asks the credential store for a source-specific secret. If none is found, it returns the workspace credential value.

**Call relations**: This is the more flexible version of get. It calls the credential source helper when a source is configured, otherwise it follows the normal workspace credential path.

*Call graph*: 3 external calls (__init__, slot_secret, ws_current).


##### `CredentialAccess.rotate`  (lines 275–280)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if its current value matches the expected old value. This is for safe credential rotation after an external provider changes a key.

**Data flow**: It takes a slot, expected old plaintext, and new plaintext. It rejects undeclared slots, then asks the current workspace to perform the compare-and-swap update and returns whether it succeeded.

**Call relations**: Handlers call this when rotating a credential without creating a new initial secret. The workspace object performs the actual protected update.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 282–295)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores proof that a workspace credential slot is bound to a provider installation. It seals the installation id so only this workspace and slot can later use it.

**Data flow**: It takes a slot and installation id. After checking the slot is declared, it seals the workspace id, slot, and installation id into a protected value, then writes that value as the workspace credential.

**Call relations**: This is used after a caller has already proved the member can reach the provider installation. It uses the installed-credential sealing helpers before writing through the workspace credential path.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 328–329)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id whose transcripts this corpus may read. The corpus is read-only and workspace-bound.

**Data flow**: It reads the ambient workspace scope and returns that workspace's id. No input is accepted and no data is changed.

**Call relations**: TrajectoryCorpus.trajectories uses this value to find only conversations in the current workspace.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 331–382)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for evaluation or learning jobs. Missing or corrupt transcripts are skipped instead of stopping the whole job.

**Data flow**: It finds recent conversations in the current workspace that have turns, loads each transcript blob, decodes it, computes the current agent prompt digest, and returns Trajectory objects containing the conversation id, agent id, prompt, digest, and messages.

**Call relations**: ExtensionContext.trajectories delegates to this when a corpus is wired. It uses database reads for conversation metadata, blob storage for transcript bodies, and logging when a transcript cannot be decoded.

*Call graph*: 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `ConversationFiles.write`  (lines 401–404)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation's sandbox workspace so the agent can see the file later under /workspace. It is an off-turn file drop, not a command execution feature.

**Data flow**: It takes a conversation id, relative path, and file contents. It passes them to the sandbox service, which writes the file and returns the path visible to the agent.

**Call relations**: Handlers use this through the optional files capability. The actual sandbox work stays behind ConversationSandbox rather than being exposed directly.


##### `ConversationFiles.prune`  (lines 406–412)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a conversation sandbox prefix, keeping only the newest configured number. This prevents unattended writers from filling the workspace forever.

**Data flow**: It takes a conversation id, a relative prefix, and a keep count. It asks the sandbox service to remove all but the newest matching files and returns nothing.

**Call relations**: This pairs with ConversationFiles.write. A handler that repeatedly appends files can prune through this narrow capability without receiving general sandbox control.


##### `conversation_agent_id`  (lines 415–428)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation belongs to, or reports that no such conversation exists in the workspace. This is a small shared lookup used before agent-scoped operations.

**Data flow**: It receives a workspace id and conversation id, queries the conversation table for that exact pair, and returns the agent id or null.

**Call relations**: ConversationProbes.run uses it before opening a sandbox, and ExtensionContext.conversation_agent exposes it to handlers. It centralizes the workspace-safe conversation-to-agent lookup.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 467–516)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, acting_member_id: UUID | None=None) -> ExecResult
```

**Purpose**: Runs one short shell command inside a conversation's sandbox, outside of a normal turn. It is meant for bounded checks, not long-running work.

**Data flow**: It takes a conversation id, command, timeout, and optional acting member id. It validates the timeout, confirms the conversation belongs to the current workspace, creates a short-lived probe token, binds the conversation's agent scope, opens the sandbox with environment variables, runs the command, and returns stdout, stderr, and exit code.

**Call relations**: It calls conversation_agent_id to enforce workspace ownership and uses the sandbox plus probe-token services to run safely. The agent scope binding makes any nested agent-scoped reads agree with the conversation's agent.

*Call graph*: calls 1 internal fn (conversation_agent_id); 5 external calls (__init__, now, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 519–541)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for jobs that need conversation transcripts. It identifies workspaces that actually have at least one conversation with a turn.

**Data flow**: It creates a query-producing helper and hands it to the owner-candidate mechanism. The result can later enumerate eligible workspaces without the extension touching cross-workspace database access directly.

**Call relations**: Trajectory-reading jobs declare this as their candidate source. The nested query builder supplies the actual database condition.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 527–539)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the database query that finds workspaces with at least one turn-bearing conversation. It is the concrete test used by trajectory_workspaces.

**Data flow**: It produces a select query over workspaces using existence checks for conversations and turns. The query itself is returned for the candidate system to run later.

**Call relations**: It is defined inside trajectory_workspaces and handed indirectly to owner_candidates. It keeps the cross-workspace discovery rule in core code.

*Call graph*: 2 external calls (exists, select).


##### `store_key_workspaces`  (lines 544–560)

```
def store_key_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for jobs driven by extension store keys. It lets a job run only in workspaces where this extension has pending state under a prefix.

**Data flow**: It takes an extension name and key prefix, creates a query-producing helper, and returns a candidate source that finds distinct workspaces with matching store rows.

**Call relations**: Store-backed jobs use this to avoid scanning all workspaces. The nested query builder holds the database condition.

*Call graph*: 1 external calls (owner_candidates).


##### `store_key_workspaces.with_a_key`  (lines 550–558)

```
def with_a_key() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the database query that finds workspaces containing this extension's store keys under a prefix. It is the concrete selector behind store_key_workspaces.

**Data flow**: It builds a select query over the extension store table, filters by extension and key prefix, and returns distinct workspace ids.

**Call relations**: It is defined inside store_key_workspaces and supplied to owner_candidates so dispatch can bind each matching workspace before running the job.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 567–579)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Describes the interface for starting an internal turn from a background handler. It is a protocol method, meaning this file states the shape expected but does not implement it.

**Data flow**: An implementation receives conversation and agent ids, a message, an idempotency key, and optional scheduling or member-watermark controls. It should return the admitted turn id, or null when a guarded invocation is skipped.

**Call relations**: ExtensionContext.invoke calls whatever object implements this protocol. This keeps the context file from importing the full turn-running system.


##### `ModelResolver.auto_model`  (lines 589–589)

```
def auto_model(self) -> str
```

**Purpose**: Describes how to read the deployment's default model id. This is part of the model resolver protocol rather than concrete logic here.

**Data flow**: An implementation exposes a string naming the model to use. No input is needed.

**Call relations**: ModelAccess.model and ModelAccess.turn read this property so background model calls use the same default model as the deployment.


##### `ModelResolver.pricing`  (lines 592–592)

```
def pricing(self) -> Pricing
```

**Purpose**: Describes how to read the price table used for model billing. The context needs this to meter model calls correctly.

**Data flow**: An implementation exposes pricing information. ModelAccess later combines it with token usage from the provider.

**Call relations**: ModelAccess.turn uses this property when recording billable usage after a model stream completes.


##### `ModelResolver.client_for`  (lines 594–594)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Describes how to get a model client for a named model. The actual resolver can choose the right provider key for the current workspace.

**Data flow**: An implementation receives a model id and returns a client object capable of streaming a completion.

**Call relations**: ModelAccess.turn calls this before sending the request. Keeping it as a protocol avoids an import cycle with the full model registry.


##### `ModelResolver.key_slot_for`  (lines 596–596)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Describes how to map a model id to the credential slot used for that model, if any. This supports labeling usage exports as bring-your-own-key or platform-key usage.

**Data flow**: An implementation receives a model id and returns a credential slot name or null.

**Call relations**: context_for stores this function on ExtensionContext when a model resolver is wired, and pending_usage_exports uses it to label exported usage.


##### `ModelAccess.model`  (lines 612–614)

```
def model(self) -> str
```

**Purpose**: Returns the deployment's default model id used by this model access object. It makes clear which model every request will be forced to use.

**Data flow**: It reads auto_model from the resolver and returns it as a string. No request data is changed.

**Call relations**: Handlers can inspect this property before calling complete or turn. The actual calls also read the same resolver value.


##### `ModelAccess.complete`  (lines 616–623)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one model completion and returns only the assistant's text. It is a simple helper for handlers that do not need tool calls or reasoning blocks.

**Data flow**: It takes a ModelRequest, delegates to ModelAccess.turn, then extracts text from the returned assistant message. If the message content is already a string, it returns it directly; otherwise it joins text blocks.

**Call relations**: The memory condenser calls this to summarize content. It depends on ModelAccess.turn for streaming, model selection, and billing.

*Call graph*: calls 1 internal fn (turn); called by 1 (_summarize).


##### `ModelAccess.turn`  (lines 625–680)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs a full model turn, including streamed text, reasoning blocks, tool calls, and usage billing. It returns an assistant message in the same shape used by the rest of the conversation system.

**Data flow**: It takes a ModelRequest, replaces its model with the deployment default, streams events from the model client, collects text and tool-call JSON, totals usage records, records the billable event for the current workspace, then returns a Message containing text or structured blocks.

**Call relations**: ModelAccess.complete calls this when it only needs text. This method calls the model client through the resolver and records usage through the current workspace billing context.

*Call graph*: called by 1 (complete); 7 external calls (__init__, __init__, __init__, __init__, model_copy, loads, ws_current).


##### `_source_readable`  (lines 738–765)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for whether a source is readable by a given agent and member context. It is the shared access rule for source and page reads.

**Data flow**: It receives a workspace id and SourceReader, then creates a SQL condition requiring the source to be live, in the workspace, in an allowed subject, and either granted to the agent or owned by the requesting member for the main agent.

**Call relations**: Readable page-state, source-id, and source-page methods all use this helper. Centralizing the rule prevents each read path from inventing a slightly different access check.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `ExtensionContext.workspace_id`  (lines 811–812)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id for the current context. It is a convenience wrapper around the context's scoped store.

**Data flow**: It reads workspace_id from the store and returns it. It does not query the database itself.

**Call relations**: Many context methods use the store's workspace id directly; this property exposes the same value to handlers.


##### `ExtensionContext.retitle_conversation`  (lines 814–817)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Changes the title of a conversation in the current workspace. It is used when a job can produce a better conversation name than the original opening text.

**Data flow**: It takes a conversation id and title, adds the current workspace id, and delegates the actual title update to the surface layer.

**Call relations**: The web surface's chat-title summarizer calls this after generating titles. The surface helper performs the actual database update.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (retitle_conversation).


##### `ExtensionContext.pending_usage_exports`  (lines 819–841)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns this extension's settled but not-yet-acknowledged usage export records. It first mints export intents so the reader sees a stable batch.

**Data flow**: It takes a time floor and limit. It requires a model key-slot resolver, opens a workspace transaction, freezes eligible usage deltas into exports, then reads up to the limit of pending exports for this workspace and extension.

**Call relations**: External billing exporters use this read side before delivering records. It calls accounting helpers owned by core because extensions should not know the private ledger schema.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 843–852)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered after an external receiver accepts them. Unacknowledged exports remain pending for retry.

**Data flow**: It receives a tuple of export records. If empty, it does nothing. Otherwise it opens a workspace transaction and asks accounting to acknowledge those exports for this workspace and extension.

**Call relations**: This is the write-back companion to pending_usage_exports. Export consumers call it only after successful delivery.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 855–867)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Gives an extension a database transaction for its own tables and approved SDK helpers. It commits on success and rolls back on error.

**Data flow**: It opens the current workspace transaction and yields the raw async database connection to the caller's block. When the block exits, the transaction machinery handles commit or rollback.

**Call relations**: Memory, research, and web audience code use this when they need custom extension-table queries. The method is powerful: the connection itself is not automatically limited to the extension's tables, so callers must scope their own SQL correctly.

*Call graph*: called by 5 (_item, _page, record_sources, _gate, web_audience); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 869–905)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Starts an internal turn in a conversation, if a turn invoker has been wired. It is how background work asks the core turn system to speak or act.

**Data flow**: It takes conversation, agent, message, idempotency key, and optional member or scheduling guards. It refuses to run if no invoker exists, otherwise passes all inputs to the invoker and returns the created turn id or null if guarded conditions skip it.

**Call relations**: Handlers call this through ExtensionContext instead of importing the turn system. The actual admission and idempotency behavior belongs to the wired TurnInvoker implementation.


##### `ExtensionContext.tail`  (lines 907–916)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Subscribes to live frames from a turn until that turn ends. This lets side-channel work watch the turn it triggered or follows.

**Data flow**: It takes a turn id and optional cursor string. It requires a tailer to be wired, then returns an async context manager that yields live frame updates.

**Call relations**: The context delegates to the injected TurnTailer. Exiting the returned context closes the subscription, so callers do not leave background listeners running.


##### `ExtensionContext.turn_is_terminal`  (lines 918–932)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final state. It reads the database rather than relying on live hub events, because a caller may arrive after the final event.

**Data flow**: It takes a turn id, queries the turn row in the current workspace, and returns true if the row is missing or its status is terminal. Otherwise it returns false.

**Call relations**: Side-channel work can use this before speaking for a turn. It uses the same terminal-status constants as the surface layer.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 934–938)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in the current workspace, or null if the conversation id is not local. It helps handlers resolve opaque conversation ids safely.

**Data flow**: It takes a conversation id and passes the current workspace id plus that id to conversation_agent_id. The result is an agent id or null.

**Call relations**: This is the public context wrapper over the shared conversation_agent_id helper.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 940–975)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Reads visibility and origin facts for several conversations in the current workspace. This supports member-facing listings that need to know who may see each row and where it came from.

**Data flow**: It takes conversation ids. If none are given, it returns an empty dictionary. Otherwise it fetches matching rows in one workspace-scoped query, parses each audience value, and returns a mapping from id to ConversationFacts.

**Call relations**: Listing code can call this once for a page of rows instead of one query per row. Missing ids are omitted, which preserves tenant isolation.

*Call graph*: 4 external calls (__init__, select, parse_audience, workspace_tx).


##### `ExtensionContext.conversation_arrival_seq`  (lines 977–1004)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Returns the latest member-message arrival sequence for a conversation. This is a watermark used to tell whether a member spoke after some work was armed.

**Data flow**: It takes a conversation id, queries inbound member messages in the current workspace, finds the maximum sequence number, and returns it or 0 if none exist.

**Call relations**: Invocation guards can compare this value with a previously recorded watermark. Only member arrivals count, so system-generated messages do not wake watchers by accident.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1006–1032)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads final status information for several turns. It helps a listing or status line show what happened on previous runs.

**Data flow**: It takes turn ids. If the input is empty, it returns an empty dictionary. Otherwise it fetches matching turn rows for the current workspace and returns each status plus terminal text, when present.

**Call relations**: Like conversation_facts, this is batched and workspace-scoped. Missing turn ids are simply absent from the result.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 1034–1040)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace belongs to the fleet operator. It is used to hide operator-only details in customer workspaces.

**Data flow**: It opens a workspace transaction, reads the workspace's email domain, compares it with the operator domain, and returns a boolean.

**Call relations**: Rendering code can call this before showing internal spend or debugging links. It delegates the domain lookup to the seats helper.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 1042–1098)

```
async def open_conversation(self, agent_id: UUID, key: str) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for a workflow key and agent. This lets recurring external events for the same subject continue in the same conversation history.

**Data flow**: It takes an agent id and key. It confirms the agent belongs to the current workspace, inserts a conversation for this extension and key if one does not already exist, then returns the matching conversation id.

**Call relations**: Handlers use this before invoking turns for trigger-driven work. Extension name and key form the conversation identity, so different extensions do not collide.

*Call graph*: 3 external calls (select, workspace_tx, uuid4).


##### `ExtensionContext.agent_name`  (lines 1100–1113)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the name of the agent currently bound in agent scope. It is useful for agent-scoped object listings that need to link back to the agent.

**Data flow**: It reads the current agent scope, queries the agent table for that workspace and agent id, and returns the agent name.

**Call relations**: The skill-create extension calls this while reading skill objects. It depends on an agent scope already being active.

*Call graph*: called by 1 (_skill); 3 external calls (select, agent_current, workspace_tx).


##### `ExtensionContext.page_states`  (lines 1115–1140)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads the current subject, revision, digest, and body reference for live pages in this workspace. It does not apply source-readability rules.

**Data flow**: It takes page ids, returns an empty dictionary for no input, otherwise queries non-tombstoned pages in the current workspace and maps each id to PageState.

**Call relations**: This is a direct workspace-scoped page-state read. Call readable_page_states when access must also be checked against a SourceReader.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 1142–1176)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads page state only for pages the given reader is allowed to see. It combines page ids with source grants and subject visibility.

**Data flow**: It takes page ids and a SourceReader. It returns empty for no ids, otherwise joins pages to sources, applies tombstone, subject, workspace, and _source_readable rules, then returns PageState objects by page id.

**Call relations**: Memory object code calls this when resolving readable page-backed objects. It uses _source_readable so page-state checks match source-page listings.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 1178–1183)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the set of live source ids readable by a given agent/member context. It is a compact way to apply source access rules.

**Data flow**: It takes a SourceReader, queries source ids that satisfy _source_readable in the current workspace, and returns them as a frozen set.

**Call relations**: This shares the same helper as source_pages and readable_page_states, so all source readability decisions follow one rule.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 1185–1378)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers a content-sync source for the current workspace and grants an agent access to it. It either creates, reuses, or revives the source row while protecting its authority and identity.

**Data flow**: It takes a backend name, typed config model, subject, owner, optional connection id, and optional agent id. It computes a stable source id, validates the target agent and connection when needed, inserts or locks the source row, rejects conflicting owner or requested-field changes, revives removed rows when appropriate, grants the agent, and returns the source id.

**Call relations**: The sample extension calls this during setup. Later source sync drivers poll these rows, while source listing and page reading methods expose the registered results.

*Call graph*: called by 1 (_setup); 6 external calls (now, model_dump, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 1380–1403)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which requested source ids are known to be removed in the current workspace. It treats absence as unknown, not as removed.

**Data flow**: It takes source ids, returns an empty frozen set for no input, otherwise queries rows in this workspace with removed_at set and returns their ids.

**Call relations**: Extensions can use this to decide what cleanup is safe. The method gives positive evidence only, avoiding destructive behavior when a row simply was not visible.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 1405–1445)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists live registered sources in the current workspace, optionally for one backend. Removed sources are hidden.

**Data flow**: It builds a workspace-scoped query over live source rows, optionally filters by backend, orders the result, and converts each row into a SourceRecord.

**Call relations**: The sources extension uses this to build bindings from extension-visible source state. It is the read side of register_source.

*Call graph*: called by 1 (_bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 1447–1495)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a specific reader. It applies workspace, tombstone, subject, and source-authority rules.

**Data flow**: It takes a SourceReader, joins pages to sources, filters by allowed subjects and _source_readable, fetches page metadata, and returns PageRecord objects. Page bodies remain referenced by body_ref rather than loaded inline.

**Call relations**: This is the page-listing counterpart to readable_page_states. It uses the shared readability helper to stay consistent with source access checks.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 1497–1513)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten by tombstoning it. Downstream page-change processing can then remove derived index data.

**Data flow**: It takes a page id, updates that live non-tombstoned page in the current workspace to tombstone=true with a fresh timestamp, and raises an error if no row was changed.

**Call relations**: This is the write side for callers that read pages and decide one should be removed. It uses the normal page tombstone path rather than deleting the row outright.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 1515–1552)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a source from syncing and tombstones its live pages in the same transaction. The source row remains so existing references still point somewhere.

**Data flow**: It takes a source id, marks the live source removed, clears any claim, deletes its grants, tombstones its non-tombstoned pages, and raises an error if the source was not live in this workspace.

**Call relations**: This is the removal counterpart to register_source. Page-change processing later cleans up derived index state from the tombstoned pages.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 1554–1580)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for one or more live sources and their live pages. This is how a source's visibility is moved atomically.

**Data flow**: It takes source ids and a new subject, updates matching live source rows in the current workspace, raises if none matched, then updates live pages from those sources with the same subject and a fresh timestamp.

**Call relations**: Because source rows and pages change in one transaction, listings and re-indexing do not see a half-changed source binding.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 1582–1655)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Updates non-identity configuration fields for live sources, optionally forcing selected sources to refetch from scratch. It prevents changes that would make the row's id no longer match its identity.

**Data flow**: It takes a mapping of source ids to new configs and a set to refetch. It validates that refetched ids are included, locks all live rows, recomputes each source id from the new config, rejects configs that would change identity, updates config fields, and for refetch rows clears cursor and claim state.

**Call relations**: This supports changing sync windows or similar parameters without deleting and recreating sources. It uses the same source_row_id logic as register_source to enforce identity safety.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 1657–1674)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Requests that live sources sync as soon as possible. It is the sanctioned way to trigger an on-demand resync.

**Data flow**: It takes source ids, updates next_sync_at to now for matching live sources in the current workspace, refreshes updated_at, and raises an error if no live source matched.

**Call relations**: The sync driver later notices these rows and claims them. Claim handling in the sync system prevents overlapping syncs of the same source.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 1676–1682)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Creates a governed proposal to change an agent, rather than directly editing the agent. This supports review and safe compare-and-swap application.

**Data flow**: It takes an AgentChange, constructs a Governance object for the current workspace and extension, asks it to create the proposal, and returns a ProposalRef.

**Call relations**: The sample extension calls this from its tick handler. Governance owns the approval and final application flow.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 1684–1689)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace's trajectory corpus through the context. It fails clearly if transcript reading was not wired for this handler.

**Data flow**: It checks whether a TrajectoryCorpus exists on the context. If not, it raises an error; otherwise it delegates to the corpus and returns its tuple of trajectories.

**Call relations**: The sample extension calls this from its tick handler. The actual transcript loading is done by TrajectoryCorpus.trajectories.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 1692–1740)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: BlobStore | None=None, sandboxes: Conversa
```

**Purpose**: Builds the ExtensionContext object handed to an extension or core job. It assembles only the capabilities that were declared or wired for that run.

**Data flow**: It receives the extension name, declared credential slots, optional services such as index, embeddings, pages, blobs, sandboxes, invoker, model resolver, surfaces, credential sources, tailer, probes, audience, and public base URL. It constructs the scoped store, credential access, optional corpus, file access, model access, installation access, and returns the complete context.

**Call relations**: This is the factory that makes core jobs and extensions receive the same shaped context. Optional inputs determine which features work and which methods fail loudly because they were not wired.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### Extension contribution contracts
Describes the structured extension-facing declarations for conversation side panels and pack or extension capabilities.

### `core/src/ufo/ext/conversation_slots.py`

`data_model` · `request handling`

A conversation can have useful side information that is not just another chat message. For example, an extension might want to show downloaded artifacts, cited web sources, a task checklist, or published sites. This file acts like the rulebook for that information: it says what each kind of slot is allowed to contain, how much data is allowed, and which links are safe enough to show.

Most of the file is made of Pydantic models. Pydantic is a validation library: it checks that incoming data has the right fields and rejects data that is missing, too large, or suspicious. The models are frozen, meaning once created they cannot be changed, which makes them safer to pass around.

The file also defines provider and context objects. A provider describes an extension-owned slot: its id, label, icon, content type, and two async callbacks for summarizing and reading the slot. The context tells that provider which conversation, agent, audience, messages, and already-visible slot items it is working with.

The most important protective behavior is URL validation. Public links must be normal HTTP or HTTPS links without embedded usernames or passwords. Image preview links are stricter: they must be same-origin, root-relative paths, like “/preview/123”, so an extension cannot sneak in an outside or malformed image URL.

#### Function details

##### `ImagePreview.same_origin_url`  (lines 52–65)

```
def same_origin_url(cls, value: str) -> str
```

**Purpose**: This validates the URL used for an image preview. It only allows a local, root-relative path, so previews come from the same web origin as the app instead of from an arbitrary outside address.

**Data flow**: It receives a URL string from an ImagePreview. It breaks the URL into parts, decodes escaped characters, and checks for unsafe patterns such as an external host, a scheme like “https:”, a fragment, backslashes, or hidden control characters. If the URL is safe, the same string comes out; if not, validation stops with an error.

**Call relations**: Pydantic calls this automatically when an ImagePreview is created. The function relies on standard URL parsing and text inspection helpers to make the safety decision before the preview can be stored inside a conversation artifact.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 81–92)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: This checks the optional public URL for a conversation artifact, such as a generated file. It allows no URL at all, but if a URL is present, it must be a normal HTTP or HTTPS link without login details hidden inside it.

**Data flow**: It receives either a URL string or None. None is returned unchanged. For a string, it parses the URL and checks that it has an HTTP or HTTPS scheme, a real hostname, and no embedded username or password. A valid URL is returned; an invalid one raises a validation error.

**Call relations**: Pydantic runs this during ConversationArtifact creation. It protects artifact slot payloads before they are shown through the conversation slot system.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 113–122)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validates the URL for a cited source. It makes sure source links are ordinary HTTP or HTTPS web links and do not contain embedded credentials.

**Data flow**: It receives a source URL string. It parses that string into URL parts, checks the scheme and hostname, and rejects URLs with usernames or passwords. The accepted URL is returned unchanged; rejected data produces a validation error.

**Call relations**: Pydantic calls this when a ConversationSource is built. It is part of the safety gate for source lists that extensions attach to a conversation.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 151–165)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: This checks that a task slot’s summary numbers match the visible task list. It prevents confusing displays like “3 completed out of 2 total” or a non-truncated list that does not actually contain every task.

**Data flow**: It reads the task payload after its fields have been filled in: total count, completed count, visible tasks, and whether the list was truncated. It counts how many visible tasks are completed and incomplete, then compares those numbers with the totals. If everything agrees, it returns the same payload; if the numbers contradict each other, it raises a validation error.

**Call relations**: Pydantic runs this after creating a TasksSlotPayload. It does not call other project functions; it acts as an internal consistency check before task data can be returned by a conversation slot provider.


##### `ConversationSite.http_url`  (lines 181–190)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validates the URL for a site connected to the conversation. It requires a normal HTTP or HTTPS address and blocks URLs that hide usernames or passwords.

**Data flow**: It receives a site URL string, parses it into parts, and checks that it uses HTTP or HTTPS, includes a hostname, and has no embedded credentials. A safe URL is returned unchanged; an unsafe one is rejected with a validation error.

**Call relations**: Pydantic calls this when a ConversationSite is created. That validated site can then be included in a sites slot payload, while private authorization fields remain excluded from serialized output.

*Call graph*: 1 external calls (urlsplit).


### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and extension/pack loading`

This file is like the application form an extension fills out before UFO starts using it. Instead of letting extensions directly poke at core internals, each extension returns a frozen Manifest object that lists what it offers and what it needs. A pack returns a Pack object that groups extensions and pack-level additions into one named product setup.

Most of the file is made of small frozen data classes. “Frozen” means they are meant to be read-only after creation, which keeps startup decisions predictable. These classes describe many kinds of contribution: a credential slot for a needed secret, a route for an HTTP endpoint, a scheduled job, a connector provider, a sandbox backend, a search provider, a lifecycle hook, a subagent profile, a skill folder, and so on. Core can then collect all active manifests and build the runtime from those declarations.

The important idea is separation. Extensions declare their pieces; core decides how to wire them in safely. For example, credentials can be injected through a proxy so secrets do not sit inside the sandbox, hooks can filter tool use without granting extra power, and background jobs are scoped to workspaces rather than running across the whole fleet blindly.

The few functions near the end validate and assemble global views from all manifests, such as conversation slot providers, open connector namespaces, and declared credential slots. Without this file, extensions would not have one clear, safe, uniform way to tell UFO what they add.

#### Function details

##### `conversation_slot_declarations`  (lines 622–651)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: This function collects all typed conversation slot providers declared by active extensions and checks that they form one valid global set. A conversation slot is a named extra piece of conversation-related data that the portal and runtime can read in a typed way.

**Data flow**: It receives the active manifests. It looks through each manifest’s conversation slot providers, checks each provider’s id, label, icon, callbacks, and payload type, and remembers which extension owns each id. If anything is malformed or two extensions claim the same id, it raises an error immediately. If everything is valid, it returns a tuple of pairs, each pairing the owning manifest with its provider.

**Call relations**: This is used when core is assembling the active extension declarations at startup. It turns many per-extension declarations into one checked list that later portal and conversation code can trust, so later code does not need to keep re-checking ids, icons, callback presence, or ownership conflicts.


##### `open_connector_namespace`  (lines 654–666)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This function finds the single catch-all connector namespace, if any extension declares one. A connector namespace is a resolver that can serve connector provider slugs that were not individually registered.

**Data flow**: It receives the active manifests and scans their connector_resolver fields. If none are present, it returns None. If it finds exactly one, it returns that resolver. If it finds more than one, it raises an error, because two catch-all resolvers would make it unclear which one owns an unregistered connector slug.

**Call relations**: This runs during extension assembly so connector setup has a single answer for unknown provider slugs. The connect flow, connector registry, and transfer-host derivation can then all route through the same resolver instead of making separate, possibly conflicting choices.


##### `declared_slots`  (lines 687–700)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: This function builds the public list of credential slots declared by all active extensions. These are the “bring your own key” or provider-secret places a workspace member or deploy may need to fill.

**Data flow**: It receives the active manifests. For every credential slot in every manifest, it creates a DeclaredSlot record containing the slot name, description, owning extension name, whether a member is allowed to fill it, and the target host if the slot has wire injection configured. It returns all of those records as one tuple and does not change the manifests.

**Call relations**: This is the shared assembly point for views that need to know which credentials exist, such as the credential object kind and the portal’s credentials panel. It hands off each slot’s plain declaration into DeclaredSlot objects so the rest of the system can present and reason about credentials without reading every manifest directly.

*Call graph*: 1 external calls (__init__).


### Shared core data contracts
Defines provider-neutral model interfaces, object naming validation, and turn-related record schemas shared across core, workers, and user surfaces.

### `core/src/ufo/models/interface.py`

`data_model` · `cross-cutting request and response handling`

This file is the contract between the application and the model services it can use. Different providers, such as Anthropic or OpenAI-style APIs, have different wire formats. This file gives the project one shared shape for messages, tools, images, reasoning traces, streamed text, usage records, and errors, so the rest of the code does not need to know every provider’s details.

Most of the file is made of small data models. A message can contain plain text or structured blocks such as text, images, tool requests, tool results, and reasoning blocks. The reasoning blocks are kept carefully because some providers require them to be sent back unchanged in later turns, like returning a sealed receipt exactly as it was given.

`ModelRequest` is the main request object. It says which model to use, what system prompt and conversation to send, what tools are available, how many tokens may be used, and whether extra reasoning is requested. It also enforces an important safety rule: if the caller forces the model to use one specific tool, that tool must actually be offered, and extended reasoning must be off.

The file also includes `trim_images`, which protects requests from provider image limits. It keeps newer images first, replaces removed images with a short placeholder, and counts images both at the top level and inside tool results. Without this trimming, a request with too many or too-large images could be rejected before the model ever answers.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool_with_reasoning_off`  (lines 147–154)

```
def _forced_choice_names_an_offered_tool_with_reasoning_off(self) -> 'ModelRequest'
```

**Purpose**: This validates a `ModelRequest` after it is built. It makes sure that if the caller forces the model to use a specific tool, that tool is actually in the list of available tools, and reasoning mode is turned off because the provider does not allow forced tool use with extended thinking.

**Data flow**: It reads the request’s `tool_choice`, `tools`, and `reasoning` fields. If no forced tool is requested, it leaves the request unchanged. If a forced tool is named but missing from the offered tools, or if reasoning is still enabled, it raises an error instead of allowing a bad request to continue.

**Call relations**: This runs as part of Pydantic’s model validation when a `ModelRequest` is created. It acts as an early gatekeeper, so provider-specific clients receive only requests that obey these shared rules.


##### `ModelClient.complete`  (lines 201–201)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the shared interface that every model client must provide. It says: given a `ModelRequest`, stream back model events such as text pieces, tool call pieces, reasoning blocks, and usage information.

**Data flow**: A caller gives it one complete model request. An implementation sends that request to a real provider or compatible backend, then yields events over time as the provider responds. The method itself is only a protocol definition here, so it describes the expected shape rather than doing the work.

**Call relations**: Other parts of the system can call `complete` without caring whether the underlying implementation talks to Anthropic, OpenAI, OpenRouter, or another provider. Concrete model clients fulfill this promise and translate provider-specific responses into the common `ModelEvent` shapes defined in this file.


##### `trim_images`  (lines 209–237)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This reduces the images in a conversation so the request stays within provider limits. It keeps the most recent images, removes older or over-budget ones, and leaves a clear text note where an image was omitted.

**Data flow**: It receives the full tuple of messages. First it asks `_image_positions` where all inline images are. It then chooses which images survive per-message limits, whole-request image-count limits, and a total image byte budget, favoring newer images. If nothing must be removed, it returns the original messages. Otherwise it asks `_trim_message` to rebuild affected messages with removed images replaced by placeholder text.

**Call relations**: This is used before model-provider clients translate the shared message format into provider-specific requests. It coordinates the helper functions: `_image_positions` finds images, `_image_data_len` measures their encoded size, and `_trim_message` produces the cleaned messages.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `_image_data_len`  (lines 240–251)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This finds the size of one image’s base64 data at a known position in the message list. It is used to decide whether keeping that image would exceed the request-wide image byte budget.

**Data flow**: It receives all messages plus a position describing where one image should be. It looks up either a top-level image block or an image nested inside a tool result, then returns the length of that image’s encoded data string. If the position does not actually point to an image, it raises an error because the caller’s bookkeeping is inconsistent.

**Call relations**: It is called by `trim_images` while that function walks backward through the images it would like to keep. Each returned size is subtracted from the remaining byte budget so `trim_images` can stop before the request becomes too large.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 254–274)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This scans the conversation and records where every inline image appears. It includes images sent directly in a message and images nested inside a tool result.

**Data flow**: It receives the tuple of messages and walks through them from oldest to newest. Plain string messages are skipped because they cannot contain image blocks. For structured messages, it records each image as a small address: message index, block index, and, for nested tool-result images, the inner content index. It returns the full list of these addresses in conversation order.

**Call relations**: It is the first helper used by `trim_images`. Its list gives `trim_images` the map it needs to apply per-message limits, whole-request limits, and size limits without changing the messages yet.

*Call graph*: called by 1 (trim_images).


##### `_trim_message`  (lines 277–301)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]]) -> Message
```

**Purpose**: This rebuilds one message after `trim_images` has decided which images must be removed. Removed images are replaced with a short text placeholder so the model can still tell that something visual was present but omitted.

**Data flow**: It receives a message index, one message, and a set of image positions to drop. If the message is plain text, it returns it unchanged. If the message has structured blocks, it walks through each block. Top-level images marked for removal become a `TextBlock` containing the omission message. Tool results with nested removed images are copied with only those inner image parts replaced. The result is a new message with safe content.

**Call relations**: It is called by `trim_images` only when at least one image must be removed. It performs the final rewrite step after `trim_images` has already calculated the drop set.

*Call graph*: called by 1 (trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/object_name.py`

`util` · `cross-cutting, especially during object creation or writes`

Object names need to be predictable because they are used across different parts of the system, including places that create names before the full object system is available. This file is the single source of truth for those names. In everyday terms, it is like the rule printed on a form that says, “Your username may only contain these characters and must fit in this box.”

The allowed name is simple: it must use lowercase letters, digits, and hyphens; it must start and end with a lowercase letter or digit; and it cannot be longer than 64 characters. That means names like `abc`, `thing-1`, and `a1-b2` are fine, while `-thing`, `Thing`, `thing_1`, and an overly long name are refused.

The file also defines `InvalidName`, a special kind of `ValueError`, which means “the value exists, but it is not acceptable here.” Code that writes or creates objects can call `validate_object_name` at the moment a name is supplied. This matters because it stops invalid names from being saved first and only causing trouble later when another part of the system tries to show, link to, or look them up.

#### Function details

##### `validate_object_name`  (lines 17–25)

```
def validate_object_name(name: str) -> None
```

**Purpose**: Checks whether a proposed object name follows the project-wide naming rule. Code uses it before accepting or saving a caller-supplied name, so invalid names are caught early.

**Data flow**: It receives a text string called `name`. It checks two things: whether the string is no more than 64 characters long, and whether every character and position fits the allowed pattern. If the name is valid, it returns nothing and changes nothing. If the name is invalid, it raises `InvalidName` with a message explaining the expected rule.

**Call relations**: This function is the enforcement point for the naming rule defined in this file. When it finds a bad name, it creates and raises an `InvalidName` error, so the calling code can stop the write or creation step instead of letting an unusable object name enter the system.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

This file is the project’s common vocabulary for conversation work. A “turn” is like a numbered ticket at a service desk: it records who asked for something, where it came from, what state it is in, and what final answer or error it produced. Without these shared models, the parts that accept user messages and the parts that process them could disagree about basic facts, such as whether a turn is still running or already finished.

Most of the file is made of Pydantic models. Pydantic is a library that checks and cleans data when records are created. The file defines allowed status words, constants for queue and workflow names, and structured payloads for special outcomes: asking the user a question, requesting credentials privately, connecting an account, or reporting token usage and cost.

Two helper functions create stable UUIDs, which are unique identifiers. They are based on the workspace, conversation, turn number, or billing dimension, so repeating the same operation produces the same ID. That matters for safe retries: the system can replay work without accidentally creating duplicate turns or duplicate billing rows.

The most important records are `TurnContext` and `Turn`. `TurnContext` cleans user-reported text so it cannot fake internal markup, and checks that time zones are real. `Turn` checks timestamps and enforces a key rule: unfinished turns must not have a final result, and finished turns must have one that matches their status.

#### Function details

##### `turn_id_for`  (lines 76–78)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the stable ID for a turn from its workspace, conversation, and sequence number. This lets the same turn get the same identifier every time, which is important when work may be retried or replayed.

**Data flow**: It receives a workspace ID, a conversation ID, and a turn sequence number. It combines those values into one text key and feeds that key into UUID generation. It returns a UUID that represents exactly that turn.

**Call relations**: When code needs to create or refer to a turn, it can call this helper before storing or running the turn. Internally it hands the combined key to `uuid.uuid5`, which makes a repeatable UUID from the same input instead of a random one.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 81–86)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing ledger ID for one turn, one billing category, and one run attempt. This prevents the same attempt from being billed twice while still allowing separate resumed attempts to be counted separately.

**Data flow**: It receives the workspace ID, turn ID, billing dimension, and optionally an attempt ID. It builds one text key from those pieces and turns it into a repeatable UUID. The returned UUID can be used as the unique identity of that billing row.

**Call relations**: Billing or usage-recording code can call this when writing token or cost records. Like `turn_id_for`, it relies on `uuid.uuid5` so repeated writes for the same attempt collapse onto the same ID instead of creating duplicates.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 204–208)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans the sender and source text that come from outside systems before the engine includes them in context. It removes angle brackets and collapses whitespace so that user-controlled text cannot pretend to be internal markup.

**Data flow**: It receives either text or nothing. If there is text, it removes `<` and `>`, turns runs of spaces and line breaks into single spaces, and returns the cleaned one-line value. If the cleaned result is empty, it returns nothing.

**Call relations**: Pydantic calls this automatically when a `TurnContext` is created for the `sender` and `source` fields. It does not call other project code; it acts as a small safety gate before the context is saved onto a turn.


##### `TurnContext._known_zone`  (lines 212–219)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a supplied time zone name is a real IANA time zone, such as `America/New_York`. This catches bad time zone data at the edge of the system instead of letting it fail later during a turn.

**Data flow**: It receives a time zone string or nothing. If no time zone is supplied, it leaves it alone. If a value is supplied, it tries to load that zone; success means the original string is returned, and failure becomes a clear validation error.

**Call relations**: Pydantic calls this automatically when creating a `TurnContext`. It uses Python’s `zoneinfo.ZoneInfo` as the source of truth for known time zones, so later code can trust that the stored value is usable.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn._aware_utc`  (lines 245–250)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Makes sure turn timestamps carry UTC time zone information. This protects against database drivers that return UTC timestamps without the marker saying they are UTC.

**Data flow**: It receives a `created_at` or `updated_at` datetime value, or nothing. If the value is missing, it stays missing. If it already has time zone information, it is returned unchanged; if it has none, the function adds UTC as its time zone marker.

**Call relations**: Pydantic calls this automatically when a `Turn` is built from incoming or database data. When it needs to add the missing marker, it uses `datetime.replace` so the clock time is treated as UTC rather than accidentally interpreted as local time.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 253–258)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Enforces the rule that a turn’s final result and its status must agree. A queued, running, or parked turn cannot already have a terminal frame, and a done, failed, or cancelled turn must have one.

**Data flow**: It looks at the completed `Turn` object after its fields have been parsed. It compares the turn status with whether a terminal frame is present, and if present, checks that the terminal frame’s own status matches the turn’s status. It returns the turn unchanged if everything is consistent, or raises a validation error if not.

**Call relations**: Pydantic calls this after creating a `Turn`. It is the final consistency check that keeps queue records honest before other parts of the system read them, process them, or display their result.
