# Core Public Protocol and Provider Contracts  `stage-22.1`

This stage is shared behind-the-scenes support. It defines the “contracts” that other parts of the system rely on: agreed shapes for data and agreed promises about what a component can do. These files do not run the main work by themselves. They make it possible to swap pieces in and out safely.

The extension context file defines the limited toolbox an extension gets inside a workspace, so add-ons cannot freely reach databases, secrets, files, or other workspaces. The browser file defines how the system asks for a Chrome connection without caring where Chrome is running. The manifest file defines how an extension describes the tools, jobs, credentials, and other features it brings.

The memory file sets the common form for recall results and memory providers. The model interface defines the shared language for AI messages, tool calls, images, reasoning, and response events. The search file gives one contract for web search and page fetching. The source connector file defines how outside data arrives in streams and pages, and how progress is tracked. The subjects file keeps audience labels consistent.

## Files in this stage

### Runtime and Extension Contracts
Defines the bounded workspace runtime, browser-access abstraction, and extension manifest format that frame how add-ons and jobs participate in the system.

### `core/src/ufo/ext/context.py`

`orchestration` · `cross-cutting during extension handlers, background jobs, and turn-related work`

Extensions need useful powers: saving state, reading declared credentials, calling a model, registering synced content, writing files into a conversation, or asking for a prompt change. But giving an extension the whole database or all secrets would be like handing a hotel guest the master key. This file instead builds a room key: an ExtensionContext that only opens the doors that extension is allowed to use, and only inside the currently bound workspace.

The main idea is “ambient workspace scope.” Most methods do not accept a workspace id from the caller. They ask ws_current() which workspace is active for this turn or job. That makes the same context object safe to reuse while still keeping each operation tied to the workspace that the scheduler or turn runner selected.

The file includes small access objects. ScopedStore gives an extension its own durable key-value storage. CredentialAccess only resolves credential slots named in the extension manifest. TrajectoryCorpus reads recent conversation transcripts but cannot write blobs. ConversationFiles can write or prune files in a conversation sandbox, but cannot run arbitrary sandbox commands. ModelAccess calls the configured language model and records billing usage. ExtensionContext ties these pieces together and adds source registration, page reads, usage export acknowledgements, internal turn invocation, scheduling hooks, and governed agent prompt proposals.

#### Function details

##### `ScopedStore.workspace_id`  (lines 81–82)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id that is currently active for this operation. This keeps the store tied to the workspace already chosen by the turn or job runner.

**Data flow**: It takes no direct input. It reads the current workspace from the workspace context and returns that workspace's id.

**Call relations**: Store methods use this property before reading or writing extension state, so callers never supply their own workspace id.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 84–95)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved JSON-like value from this extension's private storage area. Extensions use it to remember small durable facts, such as a run id or saved configuration.

**Data flow**: The caller gives a key. The method opens a workspace-scoped database transaction, looks for a row matching the current workspace, this extension name, and that key, then returns the stored value or None if nothing is saved.

**Call relations**: Browser and web extensions call this when they need previously saved state. It relies on the workspace transaction helper so the read is scoped to the active workspace.

*Call graph*: called by 3 (_start, _context, _own_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 97–112)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several saved values from the extension's private storage in one database query. This is useful when a caller already knows the keys it wants and does not need to list everything.

**Data flow**: The caller gives a sequence of keys. If the list is empty, it returns an empty dictionary. Otherwise it queries rows for the current workspace and extension, then returns a dictionary from found keys to values; missing keys are simply left out.

**Call relations**: It is a batch version of ScopedStore.get and uses the same workspace-scoped database path.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 114–135)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Saves or replaces one JSON-like value in this extension's private storage. Extensions use it to persist state between turns or jobs.

**Data flow**: The caller gives a key and value. The method first tries to update an existing row for the current workspace and extension. If no row exists, it inserts a new one with creation and update timestamps. It returns nothing.

**Call relations**: Browser and web extensions call this after creating or updating state. It performs all writes through workspace_tx so the write lands in the active workspace only.

*Call graph*: called by 3 (_start, _context, _open_conversation); 3 external calls (insert, update, workspace_tx).


##### `ScopedStore.put_if`  (lines 137–187)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the stored value still matches what the caller expected. This prevents a stale worker from overwriting a newer update made by another worker.

**Data flow**: The caller gives a key, a new value, and the value it believes is currently stored. If the expected value is None, the method tries to insert only if the key is absent. Otherwise it locks the row, compares the current value with the expected one, updates only on a match, and returns True or False to say whether the write happened.

**Call relations**: This is the safer, compare-before-write version of ScopedStore.put. It uses database conflict handling and row locking through workspace_tx.

*Call graph*: 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 189–197)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes one key from this extension's private storage. Extensions use it when saved state should no longer exist.

**Data flow**: The caller gives a key. The method opens a workspace-scoped transaction and deletes the row for the current workspace, this extension, and that key. It returns nothing even if the key was already absent.

**Call relations**: The web extension calls this while changing conversation-related state. It uses the same scoped store table as get and put.

*Call graph*: called by 1 (_open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 199–212)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists saved key-value pairs for this extension, optionally limited to keys that start with a prefix. This lets an extension keep a small namespace of related values.

**Data flow**: The caller may provide a prefix. The method queries the current workspace and extension for matching keys, orders them by key, and returns a tuple of key-value pairs.

**Call relations**: Audience-related web extension code calls this to discover stored grants. It still only sees this extension's key space inside the active workspace.

*Call graph*: called by 2 (_granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 226–227)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the id of the workspace whose credentials are currently available through this access object.

**Data flow**: It reads the current workspace context and returns that workspace's id. It does not accept a workspace id from the caller.

**Call relations**: Credential methods use this workspace identity when resolving or sealing credential values.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 229–235)

```
async def get(self, slot: str) -> str
```

**Purpose**: Fetches the live secret value for a declared credential slot. It blocks access to any slot the extension did not declare in its manifest.

**Data flow**: The caller gives a slot name. The method first checks that the slot is in the declared set. If not, it raises UndeclaredCredentialSlot before any secret lookup. If allowed, it asks the current workspace to resolve the credential, which may come from a workspace-owned secret or a platform default.

**Call relations**: Extension code uses this instead of reading secrets directly. It hands the actual lookup to ws_current().credential after enforcing the manifest gate.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.rotate`  (lines 237–242)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Updates a declared credential slot after an external service rotates the secret, but only if the current stored value matches the expected old value. This avoids replacing someone else's newer secret.

**Data flow**: The caller gives a slot, the expected current plaintext, and the new plaintext. The method rejects undeclared slots. For declared slots, it asks the current workspace to perform the compare-and-swap update and returns whether it succeeded.

**Call relations**: It follows the same declared-slot rule as CredentialAccess.get, then delegates the actual secret rotation to the workspace object.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 244–257)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores proof that a workspace member has connected a provider installation for a declared credential slot. It saves a sealed token rather than the raw installation id.

**Data flow**: The caller gives a slot and installation id. The method rejects undeclared slots, creates a sealed value tied to the workspace, slot, and installation, then stores that sealed value as the workspace credential.

**Call relations**: It uses the installed credential request machinery to get the sealing key, then writes through the current workspace. This keeps provider installation credentials from being forged by just knowing an installation id.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 290–291)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose conversation transcripts this corpus may read.

**Data flow**: It reads the active workspace context and returns its id.

**Call relations**: TrajectoryCorpus.trajectories uses this property to make sure transcript discovery is limited to the currently bound workspace.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 293–344)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Builds a read-only set of recent conversation transcripts for evaluation or learning jobs. Missing or corrupt transcripts are skipped instead of stopping the whole job.

**Data flow**: It finds recent conversations in the current workspace that have turns, reads their transcript blobs, decodes them into messages, computes the current agent prompt digest, and returns Trajectory objects. If a blob is missing it moves on; if decoding fails it logs the problem and moves on.

**Call relations**: ExtensionContext.trajectories delegates to this when a corpus is wired. It combines database metadata, blob storage, transcript decoding, and governance prompt hashing into one safe read path.

*Call graph*: 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `ConversationFiles.write`  (lines 362–365)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation's agent-visible workspace. This lets an off-turn handler prepare files that the agent can see on its next turn.

**Data flow**: The caller gives a conversation id, a relative path, and file content. The method passes them to the conversation sandbox writer and returns the /workspace path visible to the agent.

**Call relations**: ConversationFiles is added to ExtensionContext only when sandbox support is wired. This method exposes file writing without exposing arbitrary sandbox execution.


##### `ConversationFiles.prune`  (lines 367–373)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a path prefix, keeping only the newest named files. This prevents unattended background writers from filling a conversation workspace forever.

**Data flow**: The caller gives a conversation id, a relative prefix, and optionally how many files to keep. The method asks the sandbox layer to prune files under that prefix and returns nothing.

**Call relations**: It pairs with ConversationFiles.write. Both hand off to ConversationSandbox while keeping the sandbox object itself private.


##### `trajectory_workspaces`  (lines 376–398)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a workspace candidate selector for jobs that need conversation trajectories. It finds workspaces that actually have at least one conversation with at least one turn.

**Data flow**: It defines a database query builder and wraps it with owner_candidates, producing a WorkspaceCandidates object that a dispatcher can use to decide which workspaces to run against.

**Call relations**: Extensions that read trajectories can declare this as their candidate source. The nested query function builds the actual SQL condition.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 384–396)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the database query used to find workspaces containing at least one turn-bearing conversation.

**Data flow**: It takes no arguments. It returns a SQL select statement that chooses workspace ids where a conversation exists and that conversation has a turn.

**Call relations**: trajectory_workspaces passes this query builder into owner_candidates so scheduling code can enumerate eligible workspaces.

*Call graph*: 2 external calls (exists, select).


##### `TurnInvoker.invoke`  (lines 405–407)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Defines the interface for starting an internal agent turn from background code. It is a protocol method, meaning this file states the shape required but another component supplies the implementation.

**Data flow**: An implementation receives a conversation id, agent id, message, and idempotency key. It should admit or reuse a turn and return the resulting turn id.

**Call relations**: ExtensionContext.invoke calls this when an invoker is wired. The protocol keeps this file from importing the concrete turn runner.


##### `ModelResolver.auto_model`  (lines 417–417)

```
def auto_model(self) -> str
```

**Purpose**: Defines how ModelAccess learns the deployment's default model name. This is a protocol property supplied by the model registry.

**Data flow**: An implementation returns a model identifier string. No mutation happens.

**Call relations**: ModelAccess.model and ModelAccess.turn read this property so background model calls use the same default model as the rest of the system.


##### `ModelResolver.pricing`  (lines 420–420)

```
def pricing(self) -> Pricing
```

**Purpose**: Defines how ModelAccess obtains the pricing table used to bill model usage.

**Data flow**: An implementation returns pricing information. ModelAccess later combines that with token usage from the model stream.

**Call relations**: ModelAccess.turn reads this property when recording a billable event.


##### `ModelResolver.client_for`  (lines 422–422)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Defines how to get a model client for a named model. The client is the object that actually talks to the language model provider.

**Data flow**: An implementation receives a model name and returns a ModelClient, usually using the active workspace's credential rules.

**Call relations**: ModelAccess.turn calls this before streaming a completion. Keeping it as a protocol avoids an import cycle with the model registry.


##### `ModelResolver.key_slot_for`  (lines 424–424)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Defines how to identify which credential slot, if any, supplies the key for a model. This is used for usage export labels such as bring-your-own-key.

**Data flow**: An implementation receives a model name and returns a credential slot name or None.

**Call relations**: context_for stores this callable on ExtensionContext, and pending_usage_exports uses it when minting export records.


##### `ModelAccess.model`  (lines 440–442)

```
def model(self) -> str
```

**Purpose**: Reports the model that this access object will use for every completion. It is fixed to the deployment default.

**Data flow**: It reads auto_model from the resolver and returns that model name.

**Call relations**: Callers can inspect this before using ModelAccess.complete or ModelAccess.turn. The actual model call also uses the same resolver value.


##### `ModelAccess.complete`  (lines 444–451)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one language-model completion and returns only the final text. It is the simple path for extension code that does not need tool calls or structured assistant blocks.

**Data flow**: The caller gives a ModelRequest. The method calls ModelAccess.turn, receives an assistant Message, and extracts text from either a plain string response or text blocks. It returns the assembled text.

**Call relations**: The memory extension uses this for summarization. It delegates the real model streaming and billing work to ModelAccess.turn.

*Call graph*: calls 1 internal fn (turn); called by 1 (_summarize).


##### `ModelAccess.turn`  (lines 453–507)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one full language-model turn, including streamed text, reasoning blocks, tool calls, and usage billing. It returns the assistant message in the shape needed for follow-up tool use.

**Data flow**: The caller gives a ModelRequest. The method replaces the request's model with the deployment default, gets the correct client, streams events, collects text fragments, tool call JSON fragments, reasoning blocks, and usage records, then records total token usage as a billable event. It returns a Message containing plain text if there were no tool calls, or structured blocks if there were.

**Call relations**: ModelAccess.complete calls this for the simple text case. It calls the resolver for a client and pricing, uses ws_current for billing, and constructs Message, TextBlock, ToolUseBlock, and Usage objects from the model stream.

*Call graph*: called by 1 (complete); 7 external calls (__init__, __init__, __init__, __init__, model_copy, loads, ws_current).


##### `_source_readable`  (lines 565–592)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the permission rule for deciding whether an agent may read a synced content source. It checks workspace, removal status, allowed subjects, explicit grants, and a special owner/main-agent case.

**Data flow**: It receives a workspace id and SourceReader description. It returns a SQL condition, not actual rows. That condition says the source must belong to the workspace, be live, match the reader's subjects, and be either granted to the agent or owned by the requesting member for the main agent.

**Call relations**: Readable page and source methods reuse this helper so they all apply the same access rule.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `ExtensionContext.pending_usage_exports`  (lines 613–635)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns settled usage records that this extension should export to an external billing or accounting receiver. It first freezes any newly eligible usage into export records.

**Data flow**: The caller gives a time floor and maximum count. The method requires a model registry key-slot lookup to be wired, opens a workspace transaction, mints export records for usage at or after the floor, then reads pending unacknowledged exports up to the limit.

**Call relations**: This is the read side of the usage export flow. After the external receiver accepts records, callers should use ExtensionContext.ack_usage_exports.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 637–646)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered so they stop appearing in future pending reads. It should be called only after the outside system has accepted them.

**Data flow**: The caller gives a tuple of UsageExport objects. If it is empty, the method does nothing. Otherwise it opens a workspace transaction and records acknowledgements for this workspace and extension.

**Call relations**: It completes the flow started by pending_usage_exports. Unacknowledged exports remain pending and can be retried with the same deduplication identity.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 649–661)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Gives an extension a database transaction for its own extension-created tables and certain SDK-supported core operations. This is powerful and less guarded than the other access objects.

**Data flow**: The caller enters the async context manager. It yields a raw async database connection inside the active workspace transaction. On normal exit the transaction commits; on error it rolls back through workspace_tx behavior.

**Call relations**: Web audience extension code uses this when it needs custom SQL. The docstring warns that the connection itself does not enforce extension-table-only access, so the extension must scope its own queries correctly.

*Call graph*: called by 2 (_gate, web_audience); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 663–672)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Starts an internal turn in a conversation for a specific agent. It fails clearly if no turn invoker has been wired.

**Data flow**: The caller provides conversation id, agent id, message, and idempotency key. The method checks that an invoker exists, then passes those values to it and returns the resulting turn id.

**Call relations**: This is the safe public method extensions use instead of directly calling the turn runner. The actual admission checks live in the invoker implementation.


##### `ExtensionContext.page_states`  (lines 674–699)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for named live pages in the active workspace. It returns only lightweight metadata, not page bodies.

**Data flow**: The caller gives page ids. If none are given, it returns an empty dictionary. Otherwise it queries live, non-tombstoned pages in this workspace and returns a mapping from page id to PageState containing subject, revision, digest, and body reference.

**Call relations**: This is the basic workspace-scoped page state read. For reads that also enforce source visibility for a specific agent, ExtensionContext.readable_page_states adds the source permission rule.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 701–735)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads page state only for pages a particular agent/member reader is allowed to see. It combines page ids with source access checks.

**Data flow**: The caller gives page ids and a SourceReader. The method returns an empty dictionary for no ids. Otherwise it joins pages to sources, filters to live pages in the current workspace, checks subject and source readability, and returns PageState objects by page id.

**Call relations**: It calls _source_readable so it uses the same source permission rule as source_pages and readable_source_ids.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 737–742)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the ids of live sources that a reader may access in the current workspace.

**Data flow**: The caller provides a SourceReader. The method builds a source query using _source_readable, runs it in a workspace transaction, and returns a frozenset of source ids.

**Call relations**: It shares the same permission helper used by readable_page_states and source_pages, so source and page visibility stay consistent.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 744–903)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers a content-sync source, such as an external feed or connected account folder, for this workspace. It also grants an agent permission to read that source.

**Data flow**: The caller provides a backend name, typed config, subject label, owner member, optional connection id, and optional target agent. The method turns the config into JSON, computes a stable source id, validates the target agent and any connection-bound authority, inserts or revives the source row, rejects conflicting owner/disclosure changes, grants the target agent, and returns the source id.

**Call relations**: Sample and YC extensions call this during setup. Later, the core sync driver polls registered source rows and syncs pages from them.

*Call graph*: called by 2 (_setup, setup_sources); 6 external calls (now, model_dump, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.sources`  (lines 905–945)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists this workspace's live registered sources, optionally filtered to one backend. Removed sources are hidden.

**Data flow**: The caller may provide a backend name. The method builds a workspace-scoped query for live sources, applies the backend filter if present, and returns SourceRecord value objects with identity, config, ownership, timing, and error status.

**Call relations**: The sources extension uses this to build tool-facing source bindings. It is the read counterpart to register_source.

*Call graph*: called by 1 (_bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 947–995)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages that a given reader is allowed to see. It returns metadata and blob references, not full page bodies.

**Data flow**: The caller gives a SourceReader. The method joins pages to sources, filters to current workspace, non-tombstoned pages, allowed subjects, and readable sources, then returns PageRecord objects.

**Call relations**: It calls _source_readable for the permission rule. Consumers can then use page metadata and body references without bypassing audience checks.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 997–1013)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten by tombstoning it. This triggers downstream cleanup of derived index data through the normal page-change pipeline.

**Data flow**: The caller gives a page id. The method updates that page only if it belongs to the current workspace and is not already tombstoned. If no row is changed, it raises an error.

**Call relations**: It is the delete-like counterpart to source_pages for individual pages. It uses a tombstone rather than removing the row outright.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 1015–1052)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source and tombstones all of its live pages in one transaction. This stops future syncing while preserving row identity for existing page references.

**Data flow**: The caller gives a source id. The method marks the live source removed, clears any sync claim, deletes its grants, and tombstones live pages for that source. If no live source matches in this workspace, it raises an error.

**Call relations**: This is the source-level removal path. Re-registering the same source configuration can later revive the row fresh.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 1054–1080)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for one or more live sources and restamps their live pages with the same subject. This makes re-indexing happen under the new visibility label.

**Data flow**: The caller gives source ids and a subject. The method updates matching live sources in the current workspace, raises if none matched, then updates live pages for those sources with the new subject and timestamp.

**Call relations**: It keeps source metadata and page metadata in sync. Downstream page-change processing notices the updated timestamps.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.schedule_source_sync`  (lines 1082–1099)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Requests an on-demand resync for live sources by moving their next sync time to now. The sync driver will pick them up on its next pass.

**Data flow**: The caller gives source ids. The method updates next_sync_at for matching live sources in the current workspace and raises if none matched.

**Call relations**: This is the sanctioned way for extension code to ask the core sync system to run soon, without directly claiming or running sync work itself.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 1101–1107)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Creates a governed proposal to change an agent, rather than directly editing the agent configuration. This gives review and digest checks a chance to protect against stale or unwanted changes.

**Data flow**: The caller gives an AgentChange. The method creates a Governance object for the current workspace and extension, submits the proposal, and returns a ProposalRef.

**Call relations**: The sample extension calls this during its tick flow. Governance owns the later approval and compare-and-swap application.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 1109–1114)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace's trajectory corpus through the context. It fails clearly if trajectory reading was not wired for this context.

**Data flow**: It takes no input. If no TrajectoryCorpus exists, it raises a RuntimeError. Otherwise it delegates to the corpus and returns the tuple of Trajectory objects.

**Call relations**: The sample extension calls this when evaluating or proposing changes. It is a guarded wrapper around TrajectoryCorpus.trajectories.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 1117–1151)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: BlobStore | None=None, sandboxes: Conversa
```

**Purpose**: Builds the ExtensionContext object handed to an extension or core job. It wires only the capabilities supplied by the caller and scopes credentials and storage to the extension name.

**Data flow**: The caller provides the extension name, declared credential slots, optional backends such as index, pages, blob store, sandboxes, invoker, model resolver, scheduler, declared surfaces, and audience. The function constructs ScopedStore, CredentialAccess, optional TrajectoryCorpus, ConversationFiles, ModelAccess, ScheduleStore, and SurfaceInstallationAccess, then returns a single ExtensionContext.

**Call relations**: This is the factory that makes core jobs and extensions receive the same shape of safe context. Optional arguments decide which powers are present; absent powers become None or fail loudly when used.

*Call graph*: 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/browser.py`

`data_model` · `cross-cutting; used when a turn leases, reattaches to, uses, and releases browser access`

This file is a boundary line between the core system and browser providers. The core needs a Chrome DevTools Protocol endpoint, or CDP endpoint, which is the address a browser-driving engine uses to control Chrome. But core does not want to own Chrome itself. Instead, extensions provide a CdpProvider, and the provider gives out a CdpLease for each turn.

A lease is like borrowing a keycard for one visit. It gives the browser engine the connection address, offers a token that can be saved for reconnecting later, and knows how to clean up when the turn ends. It also answers practical file questions. If Chrome runs in the same sandbox as the task, a file path can be reused directly. If Chrome is remote, the provider may need to upload the file and return a new remote path. Downloads work the same way in reverse: the lease says where Chrome should put downloaded files and how to fetch the bytes afterward.

The file also defines SessionGone, the signal used when a saved browser session can no longer be reattached. That lets the caller start fresh instead of pretending an old page still exists. Overall, this file matters because it keeps browser transport flexible while keeping the core browser-engine-free.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the Chrome DevTools Protocol connection details for this leased browser. The browser-driving engine uses this address, and any needed headers, to connect to Chrome.

**Data flow**: The lease already represents a particular browser session. This method reads that session's connection information and returns a CdpEndpoint containing a URL and optional connection headers. If a concrete provider cannot supply an endpoint, its implementation may fail rather than returning a fake address.

**Call relations**: A browser provider creates the lease first. Then the browser engine calls this method when it is ready to connect and drive the page. The details returned here are handed off to the engine, while the core remains unaware of how the browser was actually created.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: Returns a saved reconnect handle for this browser lease. The token lets a later recovered turn try to attach to the same browser session instead of always starting over.

**Data flow**: The lease knows the provider-specific identity of the session, such as a hosted browser session id or a stable URL. This method turns that identity into a string that can be stored safely and used later. The output is not the browser itself, just a durable label for finding it again.

**Call relations**: After a lease is minted, the browser extension or turn state can call this method to persist a reattach handle. Later, that saved string is passed to CdpProvider.reattach, which either returns a new lease over the old session or reports that the session is gone.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Tells the caller where the leased Chrome can open a workspace file. It hides the difference between a browser that shares the sandbox filesystem and a remote browser that needs the file uploaded first.

**Data flow**: The caller provides a sandbox path and a read function that can produce the file's bytes. The lease decides what is needed for its kind of browser. A local sandbox browser can return the same path without reading bytes; a remote provider can call the read function, send the bytes elsewhere, and return the remote location Chrome should use.

**Call relations**: The browser engine uses this when it needs Chrome to open or upload a file from the workspace. The method sits between sandbox storage and the actual browser transport, so the engine does not need separate code for local and hosted Chrome.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the directory or storage location where this Chrome should write downloads. This gives the browser engine a safe place to point Chrome before downloads begin.

**Data flow**: The lease inspects its browser environment and returns a path-like location suitable for downloads. For sandbox-local Chrome, this can be a directory inside the sandbox. For a hosted browser, it can be a provider-controlled download area.

**Call relations**: The browser engine calls this before configuring Chrome's download behavior. Later, when Chrome reports a completed download by its download id, the companion method CdpLease.fetch_download is used to retrieve the bytes.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves the bytes of a completed Chrome download. It uses Chrome's download identifier, called a guid, as the key for finding the finished file.

**Data flow**: The input is the guid that Chrome assigned to a completed download. The lease looks in the right place for its browser type: a sandbox path for local Chrome, or provider storage/API for hosted Chrome. The output is the downloaded file as raw bytes.

**Call relations**: This method is used after the browser engine has allowed Chrome to download into the lease's download location. It pairs with CdpLease.download_dir: one tells Chrome where to write, and this one brings the resulting file back to the system.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: Releases the browser lease at the end of a turn. This prevents temporary browser resources, especially remote hosted sessions, from being left open unnecessarily.

**Data flow**: The method receives no extra input beyond the lease itself. A concrete implementation performs whatever cleanup its provider needs. A static local endpoint may do nothing; a remote provider may release or close the hosted session. The visible result is that the hold on the browser is ended.

**Call relations**: Turn cleanup code calls this when browser work is done. It is the final step after the engine has connected, used files and downloads as needed, and no longer needs the leased browser.


##### `CdpProvider.lease`  (lines 82–82)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a fresh browser lease for a turn. This is the main way the system asks, 'Give me a Chrome I can use for this unit of work.'

**Data flow**: The input may include a SandboxSession, which represents the task's isolated working environment. The provider uses that sandbox if it needs to find Chrome inside it, or ignores it if the browser is remote or static. The output is a CdpLease that the rest of the turn can use to connect, place files, collect downloads, and clean up.

**Call relations**: A provider is selected at startup by configuration and contributed by an extension. During a turn, orchestration code calls this method before the browser engine connects. The returned lease then supplies endpoint, file, download, token, and cleanup behavior.


##### `CdpProvider.reattach`  (lines 84–84)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Tries to reconnect to a browser session named by a previously saved token. This supports recovery after interruption without losing the browser state when the old session is still alive.

**Data flow**: The input is a token that was earlier returned by CdpLease.token. The provider resolves that token into a live browser session and returns a new CdpLease for it. If the token points to a session that has expired or disappeared, the implementation raises SessionGone so the caller can create a fresh lease instead.

**Call relations**: Recovery code calls this when it has a saved token from an earlier run. If reattachment succeeds, the returned lease flows back into the same browser-driving path as a new lease. If SessionGone is raised, the caller falls back to CdpProvider.lease and starts with a fresh browser.


### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and extension loading`

This file is mostly a set of small, immutable data shapes. Think of it like an application form for extensions: an extension fills in the form, and the core system reads it at startup to decide what should exist. The core does not call registration functions one by one. Instead, each extension returns a Manifest, which lists its tools, web routes, background jobs, credential needs, connector providers, hooks, prompt sections, sandbox backends, search backends, and more.

This matters because many parts of the system are optional and pluggable. For example, one extension may add a browser provider, another may add a search provider, and another may add a credential slot that can be safely injected into outbound network requests. Without this shared declaration file, the loader would not have one reliable place to discover what extensions provide or require.

The file also defines Pack, which is a higher-level bundle of extensions plus pack-level skills and onboarding steps. A pack is like a curated product setup: turn it on, and a known group of extensions comes up together.

Most classes here do not perform work themselves. They describe work that other parts of the system will do later. The two helper functions at the bottom gather special declarations from multiple manifests: one finds the single allowed open connector namespace, and the other turns credential declarations into user-facing credential-slot records.

#### Function details

##### `open_connector_namespace`  (lines 584–596)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: Finds the one extension, if any, that declares an open connector namespace. This is a catch-all connector resolver, so the system must reject having two of them because it would not know which one owns an unknown connector name.

**Data flow**: It receives the active manifests. It walks through them looking for a connector_resolver value. If none are found, it returns None. If exactly one is found, it returns that resolver. If it finds a second one, it raises an error immediately so the server fails at startup instead of making ambiguous routing decisions later.

**Call relations**: This helper is used during the loading and derivation of extension capabilities. Later connector lookup, connect-flow routing, and related network rules depend on there being at most one catch-all resolver, so this function acts as the early gatekeeper.


##### `declared_slots`  (lines 617–630)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: Collects all credential slots declared by active extensions and converts them into the standard DeclaredSlot records used elsewhere. These records tell the system and user interface what secrets a workspace may need to provide.

**Data flow**: It receives the active manifests. For every manifest, it reads each declared credential slot, copies its name, description, owning extension, whether a member is allowed to fill it, and the target host if the credential is injected into network traffic. It returns one tuple containing all of those DeclaredSlot records.

**Call relations**: This function is the shared assembly point for credential declarations. Other parts of the system can use its output when showing credentials to users or when treating credentials as declared objects, without each caller having to understand the full Manifest structure.

*Call graph*: 1 external calls (__init__).


### Provider Interfaces
Defines common contracts for memory recall, AI model messaging and events, and web search or page-fetching providers.

### `core/src/ufo/memory.py`

`data_model` · `cross-cutting`

This file is a contract between two sides of the project. One side provides memory: stored notes, records, or other past context that can be searched or browsed. The other side consumes memory: it asks for useful past information and injects it into the current work. Without this file, each memory extension could invent its own result format and search methods, making them hard to swap or combine.

The central result type is `MemoryMatch`. It is a small, frozen data record for one memory hit. It contains the kind of item, the text snippet to show, and, when available, a durable object reference that can be opened later plus a creation time for sorting or judging recency.

`MemorySearchProvider` is a protocol, meaning a plain-language “promise” that any provider must follow. A provider must support searching by query text, listing recent readable memory items, and reporting which item kinds can be listed. Listing recent items uses a cursor, which is like a bookmark in a list, so new items arriving during browsing do not cause rows to be skipped or repeated.

`MemorySearch` is a tiny forwarding wrapper around a chosen provider. It gives callers one stable object to use while the actual provider can vary behind the scenes.

#### Function details

##### `MemorySearchProvider.search`  (lines 36–42)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Defines the required search operation for a memory provider. A caller uses it to ask, “given these query phrases and this readable context, what past memory items are relevant?”

**Data flow**: It receives one or more query strings, a `SourceReader` that represents what sources the caller is allowed to read, and optional start and end times. The provider is expected to search its own memory store using those limits, then return a tuple of `MemoryMatch` results. This protocol method does not implement the search itself; it states what real providers must implement.

**Call relations**: This is the method that `MemorySearch.search` forwards to. In the larger flow, consumers call the wrapper, and the wrapper hands the request to whichever memory extension has been selected as the provider.


##### `MemorySearchProvider.list_recent`  (lines 44–50)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Defines the required browse operation for recent memory items. It is for showing readable memory in recency order without running a text search.

**Data flow**: It receives a set of subject names that define what the caller may read, a maximum number of items to return, optional item kinds to filter by, and an optional cursor that marks where the previous page ended. The provider is expected to return a `ListingPage` of `MemoryMatch` items, including whatever paging information is needed to continue. The protocol only defines the shape of this operation; actual providers supply the behavior.

**Call relations**: This is the method that `MemorySearch.list_recent` delegates to. It supports browsing flows where the caller wants the next page of recent memory items from the selected provider.


##### `MemorySearchProvider.listable_kinds`  (lines 52–52)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Defines how a memory provider tells callers which categories of memory items can be browsed. This lets user interfaces or consumers offer valid filters instead of guessing.

**Data flow**: It takes no input beyond the provider itself. The provider returns a tuple of kind names, such as categories or classes of memory items that its `list_recent` method can list. The protocol does not decide those names; each provider does.

**Call relations**: This is called through `MemorySearch.listable_kinds` when a consumer needs to know what filters it can safely present or request from the selected provider.


##### `MemorySearch.search`  (lines 61–68)

```
async def search(self, reader: SourceReader, queries: tuple[str, ...], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Runs a memory search through the selected provider. It gives callers a stable, simple method even though the actual search engine may come from an extension.

**Data flow**: It receives a `SourceReader`, query strings, and optional time boundaries. It passes those values unchanged to `self.provider.search`, waits for the provider’s answer, and returns the resulting tuple of `MemoryMatch` objects. It does not alter the query or interpret the results.

**Call relations**: This wrapper method is the consumer-facing path into `MemorySearchProvider.search`. When something in the system wants recall by query, it calls `MemorySearch.search`, which immediately hands the work to the active provider.


##### `MemorySearch.list_recent`  (lines 70–77)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Asks the selected provider for a page of recent memory items. It is used when the caller wants to browse memory rather than search it by keywords.

**Data flow**: It receives the readable subjects, a result limit, optional kind filters, and an optional paging cursor. It forwards all of that to `self.provider.list_recent` and returns the `ListingPage` it gets back. The page contains memory matches and paging state for continuing the browse.

**Call relations**: This method sits between callers and `MemorySearchProvider.list_recent`. It keeps the caller insulated from the specific provider while preserving the provider’s paging and filtering behavior.


##### `MemorySearch.listable_kinds`  (lines 79–80)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the item kinds that the selected provider can list. Callers use this to build valid filters for browsing recent memory.

**Data flow**: It takes no extra input. It asks `self.provider.listable_kinds` for the provider’s supported kind names and returns them directly. Nothing is transformed or cached here.

**Call relations**: This method is the wrapper path to `MemorySearchProvider.listable_kinds`. It is used before or around browsing flows so consumers know what kinds they can request from `list_recent`.


### `core/src/ufo/models/interface.py`

`data_model` · `request handling`

This file is the contract between the application and the AI models it uses. Different model providers have different APIs, but the rest of the system should not have to care about those details. This file creates shared message shapes, such as text blocks, image blocks, tool calls, tool results, and reasoning blocks, so the rest of the code can speak one internal format.

The central request type is ModelRequest. It says which model to use, what system instruction to send, what conversation messages to include, what tools are available, how many tokens the model may spend, and whether the model should use extra reasoning. It also checks one important rule: if the caller forces the model to use a specific tool, that tool must actually be offered, and extended reasoning must be turned off because at least one provider rejects that combination.

The file also defines ModelClient, a protocol. A protocol is like a promise: any provider client counts as a model client if it has a complete method that takes a ModelRequest and streams back ModelEvent items.

A practical helper, trim_images, protects requests from provider image limits. It counts images in normal message content and inside tool results, keeps the newest images first, and replaces dropped images with a short text note. Without this, a conversation with too many screenshots or scans could be rejected before the model ever sees it.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool_with_reasoning_off`  (lines 139–146)

```
def _forced_choice_names_an_offered_tool_with_reasoning_off(self) -> 'ModelRequest'
```

**Purpose**: This validation step protects ModelRequest from an invalid forced tool setup. If the caller says the model must use a particular tool, this function makes sure that tool was actually included and that reasoning is turned off.

**Data flow**: It starts with a completed ModelRequest object. If there is no forced tool choice, it leaves the request unchanged. If there is a forced tool choice, it checks the offered tools by name and checks the reasoning setting; if either rule is broken, it raises an error, otherwise it returns the same request as valid.

**Call relations**: This is run automatically by Pydantic, the data validation library, after a ModelRequest is built. It prevents bad requests from reaching provider clients, where they would fail later in a harder-to-understand way.


##### `ModelClient.complete`  (lines 187–187)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the shared method every model provider client must offer. It takes a prepared ModelRequest and streams back pieces of the model's answer, such as text, tool-call updates, reasoning blocks, or usage information.

**Data flow**: A caller gives it one ModelRequest. An implementation sends that request to a real provider or compatible backend, then yields ModelEvent items over time as the provider responds. The method itself is only a required shape here; the actual sending and streaming are implemented elsewhere.

**Call relations**: Other parts of the system can call complete without knowing whether the backend is Anthropic, OpenAI, or another provider. Provider-specific clients satisfy this protocol by implementing this method and translating between the shared internal types and the provider's own API format.


##### `trim_images`  (lines 195–223)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function makes a conversation safe to send to providers that limit how many images, and how much image data, can be included. It keeps the most recent images and replaces older or oversized ones with a small text placeholder.

**Data flow**: It receives a tuple of Message objects. First it finds every inline image, including images inside tool results. Then it applies three limits: maximum images per message, maximum images per whole request, and maximum total image bytes. Images that survive all limits stay as they are; images that do not are replaced with the text '[image omitted: over the provider image limit]'. It returns either the original messages, if no trimming is needed, or a copied tuple with the replacements.

**Call relations**: Before provider-specific clients translate messages into their own API formats, this function can normalize the image load once for everyone. It calls _image_positions to locate images, _image_data_len to measure kept images against the byte budget, and _trim_message to build the final message copies with placeholders.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `_image_data_len`  (lines 226–237)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This helper measures the size of one image's base64 data string. It is used so trim_images can enforce the total image byte budget.

**Data flow**: It receives all messages plus a position that points to one image. The position says which message, which content block, and, for images inside a tool result, which nested part. It follows that address, reads the image data string, and returns its length. If the position does not actually point to an image, it raises an error because the caller's bookkeeping is wrong.

**Call relations**: trim_images calls this while walking through the images it is considering keeping. The returned lengths let trim_images spend the request-wide image budget starting from the newest images and stop once the budget would be exceeded.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 240–260)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This helper finds every image in a set of messages and records where each one lives. It treats top-level image blocks and images nested inside tool results as real images that count toward provider limits.

**Data flow**: It receives the message tuple. It scans messages from oldest to newest, skips plain string messages, and inspects structured content blocks. For each image it finds, it records a small address made of message index, block index, and either a nested sub-index or None for a top-level image. It returns the full list in oldest-first order.

**Call relations**: trim_images calls this first to get the map of all images that might need trimming. That ordered map is then used to decide which images are oldest, which are newest, and which ones fit within the per-message, per-request, and byte limits.

*Call graph*: called by 1 (trim_images).


##### `_trim_message`  (lines 263–287)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]]) -> Message
```

**Purpose**: This helper rebuilds one message after trim_images has decided which images must be removed. It does not leave gaps; each removed image becomes a clear text note saying an image was omitted.

**Data flow**: It receives a message index, one Message, and a set of image positions to drop. If the message is plain text, it returns it unchanged. If the message has structured content, it walks through each block. Top-level dropped images are replaced with a TextBlock placeholder. For tool results that contain nested image parts, only the dropped nested images are replaced, while the rest of the tool result is copied through. It returns a copied Message with updated content.

**Call relations**: trim_images calls this for each message when at least one image must be dropped. This helper performs the actual rewriting, using TextBlock to create placeholders and Message.model_copy to preserve the original message while changing only its content.

*Call graph*: called by 1 (trim_images); 2 external calls (__init__, model_copy).


### `core/src/ufo/search.py`

`data_model` · `startup and request handling`

This file is a boundary, or “seam,” between the core application and any real web search provider. The core code does not talk directly to Google, Tavily, Exa, or any other backend. Instead, it speaks in simple project-owned shapes: a search query, a search result, a fetched page, and a provider interface.

That matters because search services need API keys and have different features. This design keeps those details outside the core. A provider is chosen when the system starts, and that provider reads its own key inside the host process. The sandboxed tool environment never sees the key.

The data classes describe the information moving across this boundary. `SearchQuery` says what to search for. `SearchResults` contains ranked `SearchHit` entries and possibly a direct answer. `FetchRequest` asks for one URL to be read, and `FetchedPage` returns extracted page text and maybe a summary.

`SearchProvider` is a protocol, meaning a promise about what methods a real provider must offer. It says every provider can search, but only some can fetch pages. If a caller asks a non-fetching provider to fetch, `SearchUnsupported` is the loud failure case. In normal use, tools check `supports_fetch` first, like checking whether a machine has a feature before pressing that button.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 87–87)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells callers whether the selected search provider can fetch and extract the contents of a specific web page. It exists so tools can hide or skip page-fetch behavior when the backend only supports search.

**Data flow**: A caller asks the provider for this true-or-false value. The concrete provider returns whether fetching is available. Nothing else is changed; the value is used as a safety check before attempting a page fetch.

**Call relations**: This is part of the `SearchProvider` promise rather than working code in this file. Research tools consult it before calling `SearchProvider.fetch`, so a provider that cannot fetch is avoided instead of being asked to do something it does not support.


##### `SearchProvider.search`  (lines 89–89)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This method is the standard way to run a web search through whatever provider was selected at startup. It lets the rest of the system ask for results using one common request shape instead of learning each provider's API.

**Data flow**: It receives a `SearchQuery` containing the search text, result count, and optional filters such as recency, allowed domains, or category. A concrete provider translates that request into its own backend call, then returns `SearchResults` containing page hits and possibly a direct answer.

**Call relations**: This file only defines the method that providers must implement. During a turn, research tools reach the selected provider through the tool context and call this method when they need web search results.


##### `SearchProvider.fetch`  (lines 91–91)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This method is the standard way to fetch the readable text from one web page when the chosen provider supports that feature. It is used after a URL is known and the system wants page content, not just a search listing.

**Data flow**: It receives a `FetchRequest` with the URL and optional instructions such as an extraction prompt, maximum text length, or whether to bypass a cache. A concrete provider retrieves and extracts the page, then returns a `FetchedPage` with the URL, text, and possibly a summary. If the provider does not support fetching, the contract says it should raise `SearchUnsupported`.

**Call relations**: This method is paired with `SearchProvider.supports_fetch`. The research fetch tool is expected to check that flag first; only when fetching is supported does it call this method on the selected provider.


### Source Connector Standards
Defines the protocol for paged source ingestion and the standard labels used to identify shared or member-specific audiences.

### `core/src/ufo/sources/connector.py`

`domain_logic` · `sync run`

A connector is the bridge between UFO and an outside service, such as a document app, code host, or email provider. This file sets the rules for that bridge so every provider can be synced in a predictable way. It defines stream descriptions, page shapes, pagination settings, and the base Connector class that provider-specific connectors inherit from.

The most important idea is that data arrives in pages. A page may just be a list of live records, or it may also say which old records were deleted and what cursor should be saved to resume later. A cursor is like a bookmark in a long book: it lets the next sync continue from the right place instead of rereading everything.

The file also solves a harder problem: some streams are split into partitions, such as one GitHub repository at a time or one Slack channel at a time. PartitionWalk keeps a separate bookmark for each partition, packs those bookmarks into one saved cursor, and carefully resumes without skipping records. It supports streams ordered oldest-first, newest-first, or with no useful ordering field.

Finally, Connector.render provides a safe default way to turn a raw provider record into a title and body text. Content-heavy connectors can override it to produce nicer prose, but this fallback means even plain JSON records can still be stored and recalled.

#### Function details

##### `PartitionWalk.stream`  (lines 207–299)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This is the main engine for syncing a stream that is split into many partitions, such as many repositories or channels. It reads the saved cursor, walks each partition in the right order, yields records page by page, and emits updated cursors so the sync can resume safely if it stops early.

**Data flow**: It starts with an optional cursor string from a previous run and decodes it into a map of partition names to saved positions. It asks the connector for partitions, then for each partition asks for pages using a PartitionBound that says where to resume. As pages arrive, it updates the in-memory checkpoint, yields StreamPage objects containing records, deletes, and the next cursor, and finally cleans up cursor entries for partitions that are no longer present or no longer need temporary state.

**Call relations**: The sync flow calls this when a connector needs per-partition progress tracking instead of one simple cursor. It relies on PartitionWalk._decode at the start to understand the stored bookmark and PartitionWalk._encode throughout the walk to turn updated progress back into a cursor string. It creates PartitionBound values to tell the connector-side page factory what slice to fetch, and it hands StreamPage objects back to the caller as the common page format.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 302–331)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This converts a saved cursor string back into the per-partition bookmark map that PartitionWalk.stream can use. It is deliberately forgiving of cursors that clearly came from some other cursor style, but strict about malformed partition-walk cursors.

**Data flow**: It receives a cursor string or nothing. If the cursor is missing, not JSON, or not a JSON object, it returns an empty map so the walk starts fresh. If it is a JSON object, each partition entry becomes either a plain watermark string or a validated in-progress window with high and until bounds; invalid entries raise an error instead of being silently ignored.

**Call relations**: PartitionWalk.stream calls this once at the beginning of a partitioned sync. Its output becomes the starting checkpoint used to decide which partitions are already done, which ones are mid-backfill, and where each partition should resume.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 334–339)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This turns the current per-partition bookmark map into a stable JSON cursor string that can be saved after each page. That saved string is what makes interrupted or capped syncs resumable.

**Data flow**: It receives a mapping from partition names to either simple watermark strings or temporary window objects. It converts window objects into plain dictionaries, then serializes the whole map as sorted JSON. The result is a cursor string suitable for placing on a StreamPage.

**Call relations**: PartitionWalk.stream calls this whenever it yields progress to the rest of the sync system. The encoded cursor is later passed back into PartitionWalk._decode on the next run, closing the loop between one sync slice and the next.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 353–354)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This abstract method requires every connector to list the streams it knows how to sync. A stream is one named collection from the outside service, such as users, issues, messages, or documents.

**Data flow**: A concrete connector implementation supplies no special input here beyond its own configuration and returns a list of StreamSpec objects. Each StreamSpec tells the sync system the stream name, record identity field, cursor field if any, deletion behavior, and related settings.

**Call relations**: The broader sync runner asks a connector for its streams before deciding what to sync. This base method is only a contract: provider-specific connector classes implement it with the actual stream list for that service.


##### `Connector.fetch_page`  (lines 357–366)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This abstract method is the connector’s promise to fetch records for one stream and yield them in pages. It lets the rest of the system treat many different services in the same way, even though their APIs may work very differently.

**Data flow**: It receives a StreamSpec, an optional saved cursor, a resolved Credential for authentication, a base URL, and optionally the current user’s id so self-authored records can be excluded where needed. A concrete connector uses those inputs to call the provider and asynchronously yields either plain lists of records or richer StreamPage objects containing records, deletes, and a next cursor.

**Call relations**: The sync adapter calls this while running a stream. This base method defines the shape all connectors must follow, while each provider-specific connector supplies the real network calls, paging rules, and record conversion.


##### `Connector.render`  (lines 368–387)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This provides the default way to turn one raw provider record into readable page content. It chooses a human-friendly title when possible and otherwise falls back to the record’s stable id.

**Data flow**: It receives one record dictionary and the stream description. It looks for common title-like fields such as title, name, login, or subject. If none exists, it uses the stream’s primary key value; if that is also missing or empty, it raises an error because the record cannot be identified. It returns a pair: the chosen title and a body containing a heading plus the record serialized as sorted JSON.

**Call relations**: The sync adapter uses this after records have been fetched, when turning provider data into stored recallable content. Content-focused connectors can override this method to produce cleaner text, such as an email body or document prose, but this default keeps simple record-based streams usable without custom rendering.

*Call graph*: 1 external calls (dumps).


### `core/src/ufo/subjects.py`

`data_model` · `cross-cutting`

This small file is a naming guide for “subjects,” which are labels that say what audience or owner something is tied to. One subject is always the same: `shared`, meaning the thing is meant for everyone or is not tied to a single member. The other kind is member-specific. For that, the file uses the prefix `member:` followed by a member’s unique ID. A unique ID here is a UUID, which is a long identifier designed to avoid collisions, like a very reliable serial number.

The value of this file is consistency. Without it, different parts of the system might invent slightly different labels, such as `user:...`, `member-...`, or `members/...`, and then they would fail to recognize that they are talking about the same person or audience. This file acts like a shared label maker: everyone gets the same format every time.

There is one helper function, `member_subject`, which turns a member ID into the exact subject string the rest of the system expects.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: This function creates the standard subject label for one specific member. Someone would use it when they need to tag or look up data that belongs to that member.

**Data flow**: It receives a member ID as a UUID. It places the fixed text `member:` in front of that ID, producing a string such as `member:<id>`. It returns that string and does not change anything else.

**Call relations**: This helper is meant to be called by code that needs a member-specific subject label. It relies on the shared prefix constant in this file so callers do not have to remember or rebuild the format themselves.
