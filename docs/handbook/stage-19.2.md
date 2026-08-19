# Internal extension contracts, runtime context, and conversation slots  `stage-19.2`

This stage is shared behind-the-scenes support for extensions, which are plugin-like add-ons that can expand the main application. It defines the rules for what an extension can promise, what it is allowed to do while running, and what extra information it may place inside a conversation.

The manifest file is the “application form” for an extension or pack. It lists what the add-on provides, such as tools, scheduled jobs, credentials it needs, agents, skills, routes, hooks, or backends. The core system reads this declaration to know how to wire the add-on in safely.

The context file defines the limited workbench given to extension code and background jobs at runtime. Instead of full access to the system, they receive scoped abilities, such as reading their own settings, using approved credentials, opening conversations, reading transcripts, or registering synced sources.

The conversation slots file defines the extra panels extensions can add to conversations, such as artifacts, sources, tasks, sites, and automations. It validates these payloads so they stay safe, well-shaped, and displayable.

## Files in this stage

### Runtime context
Defines the restricted workspace-scoped capabilities exposed to extensions and background jobs while they run.

### `core/src/ufo/ext/context.py`

`orchestration` · `cross-cutting: active whenever extension handlers, background jobs, tool hooks, or source-sync workflows need scoped access`

This file is the boundary between untrusted or semi-independent extension code and the core UFO system. Instead of handing an extension the whole database, blob store, sandbox, or credential vault, the system gives it an ExtensionContext: a carefully shaped toolbox. Each tool is already tied to the current workspace, and many tools check extra permissions before doing anything. This is like giving a contractor a keycard that opens only the rooms needed for today’s job, rather than the building’s master key.

The file provides several capability objects. ScopedStore is durable storage for one extension’s own key-value data. CredentialAccess lets code read only credential slots that its manifest declared. TrajectoryCorpus reads conversation transcripts for evaluation jobs, without allowing arbitrary blob access. ConversationFiles and ConversationProbes let trusted jobs write files or run short commands inside a conversation sandbox. ModelAccess lets background jobs call the default language model while recording billing and metrics.

ExtensionContext gathers these pieces into one object and adds higher-level operations: listing members and agents for first-party jobs, scheduling turns, reading member-visible context, registering content-sync sources, managing synced pages, opening extension-owned conversations, exporting usage, and proposing governed agent changes. The context_for function builds this object consistently for both core jobs and extensions, so all callers go through the same safety path.

#### Function details

##### `ScopedStore.workspace_id`  (lines 112–113)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id currently bound to the running job or turn. This keeps the store tied to the active workspace instead of letting callers choose one.

**Data flow**: It reads the current workspace scope from the ambient workspace context and returns its id. Nothing is written.

**Call relations**: All ScopedStore reads and writes rely on this property so their database queries stay inside the workspace that the dispatcher already bound.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 115–126)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved JSON value from this extension’s private key-value store. Extensions use it for small durable state such as checkpoints or remembered browser/session information.

**Data flow**: The caller gives a key. The function looks for a row matching the current workspace, this extension name, and that key, then returns the stored value or null if there is none.

**Call relations**: Browser, Browserbase, Slack, and Web extension code call this when they need previously saved extension state. It uses the same workspace transaction path as the rest of the context.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_web_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 128–143)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several saved values from the extension’s private store in one database trip. This avoids scanning the whole store when the caller already knows the keys.

**Data flow**: The caller gives a list of keys. The function fetches matching rows for the current workspace and extension, then returns a dictionary for only the keys that exist.

**Call relations**: This is the batch version of ScopedStore.get. It participates in the same workspace-scoped storage flow but is optimized for listings or grouped lookups.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 145–169)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Writes or replaces one JSON value in the extension’s private store. It is safe when two workers might try to create the same key at the same time.

**Data flow**: The caller gives a key and value. The function performs a database upsert, meaning it inserts the row if missing or updates it if already present, then returns nothing.

**Call relations**: Browser, Browserbase, and Web extension code call this to save state. It hides database differences between PostgreSQL and SQLite so callers get one simple operation.

*Call graph*: called by 3 (_start, _context, _open_conversation); 1 external calls (workspace_tx).


##### `ScopedStore.put_if`  (lines 171–221)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the current stored value still matches what the caller expected. This prevents one worker from accidentally overwriting newer state written by another worker.

**Data flow**: The caller gives a key, a new value, and an expected old value. The function compares the stored value inside a transaction; if it still matches, it writes and returns true, otherwise it leaves the row alone and returns false.

**Call relations**: Slack progress checkpoint code uses this for compare-and-swap style updates, where a stale checkpoint must not clobber a fresher one.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 223–231)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes one key from this extension’s private store. It is used when saved extension state should no longer affect later runs.

**Data flow**: The caller gives a key. The function deletes the matching row for the current workspace and extension, then returns nothing.

**Call relations**: The Web extension calls this while opening conversations to clear extension-owned state. The workspace and extension filters keep deletion narrow.

*Call graph*: called by 1 (_open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 233–246)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists this extension’s stored keys and values, optionally only those beginning with a prefix. This supports extension features that keep a small namespace of related settings.

**Data flow**: The caller may give a prefix. The function fetches matching rows for the current workspace and extension, orders them by key, and returns key-value pairs.

**Call relations**: The Web extension uses this to discover audience grants. It is intentionally limited to the extension’s own namespace.

*Call graph*: called by 2 (_granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 262–263)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id whose credentials this access object may resolve. The caller cannot pass a different workspace id.

**Data flow**: It reads the ambient workspace scope and returns its id. It changes nothing.

**Call relations**: Credential resolution, source-backed secrets, and installation binding all use this workspace id so credentials follow the job’s current workspace.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 265–271)

```
async def get(self, slot: str) -> str
```

**Purpose**: Fetches a declared credential slot’s live secret. It refuses slots the extension did not declare, so a handler cannot guess secret names.

**Data flow**: The caller gives a slot name. The function checks that the slot was declared, then asks the current workspace for the secret value and returns it.

**Call relations**: Extension code uses this when it needs a simple credential. The undeclared-slot check happens before any secret lookup.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 273–280)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Reports whether a declared credential is supplied by the workspace itself rather than by the platform default. This matters for billing and ownership of provider spend.

**Data flow**: The caller gives a slot name. After checking that the slot was declared, the function asks the current workspace whether that slot has a stored workspace secret and returns true or false.

**Call relations**: This sits beside CredentialAccess.get: one answers the secret, the other answers who supplied it.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.resolve`  (lines 282–295)

```
async def resolve(self, slot: str) -> str
```

**Purpose**: Resolves a declared credential slot, including slots backed by a manifest-defined source such as an installed provider connection. It falls back to the normal workspace credential when no source secret is available.

**Data flow**: The caller gives a slot. The function checks declaration, finds any configured credential source, asks the credential store for a source-derived secret if needed, and returns the first usable secret.

**Call relations**: This is the richer credential path used when an extension can bind credentials to external installations while still preserving the same slot declaration gate.

*Call graph*: 3 external calls (__init__, slot_secret, ws_current).


##### `CredentialAccess.rotate`  (lines 297–302)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if its current plaintext matches an expected value. This is for safe provider-side key rotation.

**Data flow**: The caller gives a slot, expected old secret, and new plaintext. The function checks the slot declaration, then asks the workspace credential system to rotate it and returns whether the swap happened.

**Call relations**: It delegates the actual secret update to the workspace credential layer while keeping the extension-level permission check here.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 304–317)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores a sealed provider installation reference into a declared credential slot. The stored value is protected so a plain installation id cannot be forged into a credential.

**Data flow**: The caller gives a slot and installation id. The function checks the slot, seals workspace, slot, and installation together, and saves that sealed value as the workspace credential.

**Call relations**: This connects surface/provider installation proof to the credential system. It uses the shared installation sealing service rather than exposing raw credential-store details.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 350–351)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose transcripts this corpus may read. It prevents trajectory reads from choosing another tenant’s workspace.

**Data flow**: It reads the current workspace scope and returns its id. It writes nothing.

**Call relations**: TrajectoryCorpus.trajectories and TrajectoryCorpus.conversations both use this property when selecting allowed conversations.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 353–360)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for the current workspace. Evaluation or learning jobs use this as their read-only corpus.

**Data flow**: It builds a query for the most recent conversations in the workspace, then hands that query to the shared transcript reader and returns decoded trajectory records.

**Call relations**: It is the broad corpus read. The detailed database and blob-reading work is done by TrajectoryCorpus._read.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus.conversations`  (lines 362–374)

```
async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]
```

**Purpose**: Reads transcripts for exactly the conversation ids the caller names, still limited to the current workspace. This lets a job work on known conversations even if they are older than the normal corpus limit.

**Data flow**: The caller gives conversation ids. The function builds a workspace-filtered query for those ids, then returns decoded trajectories for the ones that exist and decode cleanly.

**Call relations**: Like trajectories, it delegates the actual transcript loading to TrajectoryCorpus._read.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus._read`  (lines 376–420)

```
async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]
```

**Purpose**: Turns selected conversation rows into Trajectory objects by joining database metadata with transcript blobs. Missing or corrupt transcripts are skipped instead of failing the whole corpus.

**Data flow**: It receives a database subquery identifying chosen conversations. It fetches conversation, agent, and prompt data, loads each transcript blob, decodes messages, computes the prompt digest, and returns a tuple of trajectories.

**Call relations**: Both public corpus methods call this. It coordinates database reads, blob reads, transcript decoding, and logging for bad transcript data.

*Call graph*: called by 2 (conversations, trajectories); 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `ConversationFiles.write`  (lines 439–442)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s sandbox workspace so the agent can see the file later under /workspace. It is for off-turn jobs that need to prepare files for an agent.

**Data flow**: The caller gives a conversation id, relative path, and file content. The function forwards the write to the conversation sandbox service and returns the path visible to the agent.

**Call relations**: This is a narrow wrapper over the sandbox capability. It keeps callers from receiving the full sandbox object.


##### `ConversationFiles.prune`  (lines 444–450)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a sandbox path prefix, keeping only the newest named files. This stops unattended writers from filling a conversation workspace forever.

**Data flow**: The caller gives a conversation id, path prefix, and keep count. The function asks the sandbox service to prune matching files and returns nothing.

**Call relations**: It pairs with ConversationFiles.write as the cleanup operation for off-turn file generation.


##### `conversation_agent_id`  (lines 453–466)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation belongs to within a workspace. It returns nothing if the conversation id is not part of that workspace.

**Data flow**: The caller gives a workspace id and conversation id. The function queries the conversation table for a matching row and returns its agent id or null.

**Call relations**: ConversationProbes.run uses it before opening a sandbox, and ExtensionContext.conversation_agent exposes it as a context method.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 505–555)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, acting_member_id: UUID | None=None) -> ExecResult
```

**Purpose**: Runs one short shell command in a conversation’s sandbox outside a normal turn. This lets trusted background work inspect or prepare the same workspace the agent uses, without granting unlimited runtime.

**Data flow**: The caller gives a conversation id, command, timeout, and optional acting member. The function checks the timeout, verifies the conversation is in the current workspace, creates a short-lived probe token, opens the sandbox with a scoped environment, runs bash, and returns stdout, stderr, and exit code.

**Call relations**: It uses conversation_agent_id to bind the probe to the conversation’s agent, then opens the sandbox through ConversationSandbox. The agent scope is set during the run so downstream agent-scoped checks agree with the probe.

*Call graph*: calls 1 internal fn (conversation_agent_id); 5 external calls (__init__, now, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 558–580)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds the workspace-candidate rule for jobs that need conversation trajectories. It finds workspaces that have at least one conversation with a turn.

**Data flow**: It defines a database query factory and wraps it in the owner-candidate mechanism. The result is a candidate provider, not the workspace rows themselves.

**Call relations**: Background job registration can declare this candidate provider so the dispatcher only runs trajectory-reading jobs where there is transcript-like work to read.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 566–578)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query used to identify workspaces containing at least one turn-bearing conversation. It is the concrete filter behind trajectory_workspaces.

**Data flow**: It builds a select statement over workspaces with nested existence checks for conversations and turns. The statement is returned for the candidate system to execute.

**Call relations**: This nested helper is passed to owner_candidates by trajectory_workspaces.

*Call graph*: 2 external calls (exists, select).


##### `seated_member_workspaces`  (lines 583–593)

```
def seated_member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds the workspace-candidate rule for first-party jobs that operate on seated members. A seated member is a member who currently has a usable seat in the workspace.

**Data flow**: It defines a query for distinct workspace ids from seated member rows and wraps that query for owner-side candidate scanning.

**Call relations**: Member-oriented scheduled jobs use this to avoid running in workspaces with no seated members.

*Call graph*: 1 external calls (owner_candidates).


##### `seated_member_workspaces.with_a_seated_member`  (lines 586–591)

```
def with_a_seated_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces with at least one seated member. It is the database part of seated_member_workspaces.

**Data flow**: It selects distinct workspace ids from member rows where seated_at is present. The query is returned to the candidate wrapper.

**Call relations**: This helper is only used by seated_member_workspaces.

*Call graph*: 1 external calls (select).


##### `connection_workspaces`  (lines 596–609)

```
def connection_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds the workspace-candidate rule for jobs driven by connected accounts. It selects workspaces whose main agent has at least one connector grant.

**Data flow**: It defines a query over connector grants joined to agents and wraps it in the owner-candidate mechanism.

**Call relations**: Connection-driven background handlers can declare this so they run only where the main agent has a connected account to use.

*Call graph*: 1 external calls (owner_candidates).


##### `connection_workspaces.with_a_main_agent_connection`  (lines 601–607)

```
def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces where the main agent has connector access. It is the concrete filter behind connection_workspaces.

**Data flow**: It selects distinct workspace ids from connector grants joined to agents and filters to main agents. The query is returned.

**Call relations**: This helper is passed to owner_candidates by connection_workspaces.

*Call graph*: 1 external calls (select).


##### `awaiting_a_title`  (lines 615–631)

```
def awaiting_a_title() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition for conversations that still need an automatic title. A conversation qualifies only after a member turn has completed, so there is something real to summarize.

**Data flow**: It returns a SQL boolean expression requiring title_summarized to be false and requiring at least one completed member-admitted turn.

**Call relations**: ExtensionContext.conversations_awaiting_title uses it for per-workspace work, and untitled_conversation_workspaces uses it to find candidate workspaces.

*Call graph*: called by 2 (conversations_awaiting_title, with_an_unsummarized_title); 3 external calls (and_, exists, select).


##### `untitled_conversation_workspaces`  (lines 634–643)

```
def untitled_conversation_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds the workspace-candidate rule for the conversation-title summarizing job. It finds workspaces with conversations that still need titles.

**Data flow**: It defines a query for workspace ids from conversations matching awaiting_a_title and wraps it for owner-side candidate scanning.

**Call relations**: The title summarization job uses this to avoid waking up in workspaces where every eligible conversation is already titled.

*Call graph*: 1 external calls (owner_candidates).


##### `untitled_conversation_workspaces.with_an_unsummarized_title`  (lines 640–641)

```
def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces containing conversations awaiting title summaries.

**Data flow**: It selects distinct workspace ids from conversations filtered by awaiting_a_title. The query is returned to the candidate wrapper.

**Call relations**: This helper is used inside untitled_conversation_workspaces.

*Call graph*: calls 1 internal fn (awaiting_a_title); 1 external calls (select).


##### `unseeded_agent_workspaces`  (lines 646–671)

```
def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Builds a candidate rule for jobs that must do once-per-agent setup. It finds workspaces where the number of agents is greater than the number of extension store keys marking agents as settled.

**Data flow**: The caller gives an extension name and key prefix. The function defines a query comparing counts of agents and matching store keys, then wraps it as workspace candidates.

**Call relations**: First-party sweep or seeding jobs can use this so they run only until every agent has been processed.

*Call graph*: 1 external calls (owner_candidates).


##### `unseeded_agent_workspaces.with_an_unsettled_agent`  (lines 654–669)

```
def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that detects whether a workspace has agents not yet marked as settled by extension store keys.

**Data flow**: It builds two count subqueries, one for agents and one for matching extension-store keys, then selects workspaces where the agent count is larger.

**Call relations**: This helper is enclosed by unseeded_agent_workspaces and handed to owner_candidates.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 678–690)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Describes the interface for scheduling an internal agent turn. It is a protocol method, meaning this file defines the shape expected but not the actual implementation.

**Data flow**: An implementation receives conversation, agent, message, idempotency, and scheduling flags, then returns the created turn id or null when guarded conditions refuse admission.

**Call relations**: ExtensionContext.invoke calls this protocol when an invoker has been wired into the context.


##### `ModelResolver.auto_model`  (lines 700–700)

```
def auto_model(self) -> str
```

**Purpose**: Describes the property that returns the deployment’s default model id. ModelAccess uses this so background jobs call a fixed default model.

**Data flow**: An implementation provides a string model name. This protocol property itself has no body or side effects.

**Call relations**: ModelAccess.model, complete, and turn rely on this property through the resolver protocol.


##### `ModelResolver.pricing`  (lines 703–703)

```
def pricing(self) -> Pricing
```

**Purpose**: Describes the property that returns model pricing data. ModelAccess uses it to calculate billable usage from token counts.

**Data flow**: An implementation provides a Pricing object. The protocol only states that this value must be available.

**Call relations**: ModelAccess.turn reads this while recording usage inside a billable event.


##### `ModelResolver.client_for`  (lines 705–705)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Describes how to obtain a model client for a model id. A model client is the object that actually streams completions from a provider.

**Data flow**: An implementation receives a model name and returns an asynchronous model client. The protocol defines the contract only.

**Call relations**: ModelAccess.turn calls this before streaming a background model response.


##### `ModelResolver.key_slot_for`  (lines 707–707)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Describes how to map a model id to the credential slot that supplies its provider key, if any. This is needed to tell whether the workspace is using its own key.

**Data flow**: An implementation receives a model name and returns a credential slot name or null. The protocol itself changes nothing.

**Call relations**: ModelAccess._serves_itself and usage-export code use this resolver method.


##### `ModelResolver.provider_for`  (lines 709–709)

```
def provider_for(self, model: str) -> str
```

**Purpose**: Describes how to identify the provider behind a model id, such as the company or service serving that model. Metrics use this label.

**Data flow**: An implementation receives a model name and returns a provider string. The protocol only declares the requirement.

**Call relations**: ModelAccess.turn uses this when emitting model latency and token metrics.


##### `ModelAccess.model`  (lines 733–735)

```
def model(self) -> str
```

**Purpose**: Returns the default model id this background model access will call and bill. It prevents handlers from silently changing the billed model.

**Data flow**: It reads the resolver’s auto_model property and returns it. Nothing is written.

**Call relations**: This is the public read-only view of the same model that ModelAccess.turn forces into every request.


##### `ModelAccess.complete`  (lines 737–744)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one model turn and returns only the assembled text. It is the simple helper for jobs that do not need tool-call blocks or reasoning blocks.

**Data flow**: The caller gives a model request. The function calls ModelAccess.turn, then extracts and joins text from the assistant message and returns that string.

**Call relations**: The memory condenser uses this for summarization. It delegates metering, streaming, and model selection to ModelAccess.turn.

*Call graph*: calls 1 internal fn (turn); called by 1 (_summarize).


##### `ModelAccess._serves_itself`  (lines 746–753)

```
async def _serves_itself(self, model: str) -> bool
```

**Purpose**: Checks whether the workspace is using its own provider key for a given model. If it is, the platform should not treat provider spend the same way as platform-key spend.

**Data flow**: The caller gives a model id. The function opens a workspace transaction, checks the model’s key slot against workspace-owned credentials, and returns true or false.

**Call relations**: ModelAccess.turn calls this just before billing model usage so usage is priced under the right ownership assumption.

*Call graph*: called by 1 (turn); 3 external calls (workspace_owns_the_key, workspace_tx, ws_current).


##### `ModelAccess.turn`  (lines 755–856)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Streams one background language-model response, records billing and metrics, and returns the assistant message in the system’s normal message format. It preserves tool calls and reasoning blocks when the provider sends them.

**Data flow**: The caller gives a model request. The function replaces its model with the deployment default, streams events from the model client, collects text, tool-call JSON, reasoning blocks, and usage records, bills usage, emits latency and token metrics, and returns a Message.

**Call relations**: ModelAccess.complete calls this for text-only use. Internally it asks the resolver for the model client, pricing, provider, and key slot, and uses _serves_itself for billing context.

*Call graph*: calls 1 internal fn (_serves_itself); called by 1 (complete); 10 external calls (__init__, __init__, __init__, __init__, model_copy, loads, monotonic, emit_histogram, emit_metric, ws_current).


##### `_source_readable`  (lines 914–941)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database permission condition for whether a synced source is readable by a particular agent/member reader. It combines workspace, removal, subject visibility, explicit grants, and member-owned main-agent access.

**Data flow**: The caller gives a workspace id and SourceReader. The function returns a SQL boolean expression; it does not execute the query itself.

**Call relations**: Readable page and source listing methods reuse this helper so they all apply the same source-access rule.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `MemberContextRecord._aware_utc`  (lines 998–999)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a member context record’s information date has timezone information. If a datetime is naive, it is treated as UTC.

**Data flow**: Pydantic passes in a datetime value. The validator returns it unchanged if it already has a timezone, or returns a UTC-marked copy otherwise.

**Call relations**: This runs automatically when MemberContextRecord objects are created by member-context reading methods.

*Call graph*: 1 external calls (replace).


##### `_member_blob_text`  (lines 1005–1023)

```
async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str
```

**Purpose**: Reads a bounded amount of text from a blob for member context. The byte limit prevents large files from being pulled into memory or prompts.

**Data flow**: The caller gives a workspace blob store and key. The function streams up to a little over the byte limit, closes the stream if needed, decodes valid UTF-8 text, and returns at most the allowed text.

**Call relations**: ExtensionContext.member_context calls this for text artifacts and synced pages whose body is stored in blob storage.

*Call graph*: calls 1 internal fn (get_stream); called by 1 (member_context).


##### `ExtensionContext.workspace_id`  (lines 1050–1051)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id for this context through its scoped store. It is a convenience property used by many context methods.

**Data flow**: It reads self.store.workspace_id and returns that UUID. No state changes.

**Call relations**: Most ExtensionContext database methods use this property or the store’s workspace id to scope their queries.


##### `ExtensionContext.seated_members`  (lines 1053–1078)

```
async def seated_members(self, *, cursor: UUID | None=None, limit: int=100) -> SeatedMemberPage
```

**Purpose**: Lists seated workspace members in pages for first-party member jobs. It is permission-gated because member rosters are sensitive.

**Data flow**: The caller may provide a cursor and limit. The function checks permission and limit, reads seated members after the cursor, returns member records, and includes a next cursor if more rows exist.

**Call relations**: The sweep extension calls this during its tick. It is available only when context_for enables member-context reading.

*Call graph*: called by 1 (_tick); 4 external calls (__init__, __init__, select, workspace_tx).


##### `ExtensionContext.workspace_agents`  (lines 1080–1106)

```
async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]
```

**Purpose**: Lists all agents in the workspace with their owner and tool list. First-party setup jobs use this to sweep or seed agent-related data.

**Data flow**: After checking permission, it queries agents in creation order and returns WorkspaceAgent records.

**Call relations**: The Web extension uses this while seeding homepages. The permission check keeps ordinary extensions from reading the full agent roster.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.agent_visibilities`  (lines 1108–1120)

```
async def agent_visibilities(self) -> dict[UUID, AgentVisibility]
```

**Purpose**: Returns each agent’s portal visibility setting. This is workspace shape information rather than private member data, so it is not behind the member-context gate.

**Data flow**: It queries agent ids and visibility values for the current workspace and returns a dictionary keyed by agent id.

**Call relations**: Other extension code can use this to decide how agent-attached objects should be shown without reading private member records.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.earliest_seated_admin`  (lines 1122–1140)

```
async def earliest_seated_admin(self) -> UUID | None
```

**Purpose**: Finds the earliest seated admin in the workspace. This gives background work a deterministic member to act on behalf of when an agent has no owner.

**Data flow**: After checking permission, it queries seated admins ordered by seat time and id, then returns the first member id or null.

**Call relations**: The Web extension uses this during homepage seeding. It shares the same member-context permission gate as member roster reads.

*Call graph*: called by 1 (seed_homepages); 2 external calls (select, workspace_tx).


##### `ExtensionContext.invoke_agent_for_member`  (lines 1142–1232)

```
async def invoke_agent_for_member(self, *, agent_name: str, member_id: UUID, conversation_key: str, message: str, idempotency_key: str) -> ScheduledMemberTurn
```

**Purpose**: Opens or reuses a private scheduled conversation for an extension-provisioned agent and schedules one turn for a specific seated member. This is how a first-party job sends a member-specific agent message.

**Data flow**: The caller provides agent name, member id, conversation key, message, and idempotency key. The function verifies the member and agent, creates or checks the extension conversation, invokes a turn on behalf of the member, and returns the conversation id plus turn id.

**Call relations**: The sweep extension calls this during its tick. It ends by calling ExtensionContext.invoke, so actual turn admission stays in the injected turn invoker.

*Call graph*: calls 1 internal fn (invoke); called by 1 (_tick); 6 external calls (__init__, insert, select, conversation_audience, workspace_tx, uuid4).


##### `ExtensionContext.member_context`  (lines 1234–1377)

```
async def member_context(self, *, since: datetime, limit: int=200) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Collects recent context visible to the scheduled member, including conversations, shared artifacts, synced pages, memories, and objectives. It gives scheduled jobs enough background to act usefully without reading unrelated private data.

**Data flow**: The caller gives a since time and limit. The function checks that it is bound to a scheduled member, queries visible turns and artifacts, reads text blobs where safe, reads visible pages, adds extension-owned memory/objective records, sorts everything by date, and returns a bounded tuple.

**Call relations**: It calls _member_blob_text for blob-backed text and _member_extension_records for memory/objective data. It relies on audience rules from readable_audiences.

*Call graph*: calls 2 internal fn (_member_extension_records, _member_blob_text); 5 external calls (__init__, exists, select, readable_audiences, workspace_tx).


##### `ExtensionContext._member_extension_records`  (lines 1379–1616)

```
async def _member_extension_records(self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Adds extension-owned memory and open objective records to member context. It knows the private extension tables well enough to turn their rows into plain context records.

**Data flow**: The caller gives member id, readable audience strings, since time, and limit. The function queries memory, objectives, steps, events, and checks, determines which objectives are still open, builds stable record keys, and returns MemberContextRecord objects.

**Call relations**: ExtensionContext.member_context calls this after collecting core conversation, artifact, and page records.

*Call graph*: called by 1 (member_context); 8 external calls (__init__, sha256, DateTime, column, or_, select, table, workspace_tx).


##### `ExtensionContext.retitle_conversation`  (lines 1618–1621)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation in the current workspace. Jobs use it when they have computed a better title than the original.

**Data flow**: The caller gives a conversation id and title. The function passes the current workspace id, conversation id, and title to the surface-layer retitle helper.

**Call relations**: It is a thin context-safe wrapper around the core surface retitling function.

*Call graph*: 1 external calls (retitle_conversation).


##### `ExtensionContext.conversations_awaiting_title`  (lines 1623–1647)

```
async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]
```

**Purpose**: Returns recent conversations in this workspace that still need title summaries. This gives the titling job a bounded batch of work.

**Data flow**: The caller gives a limit. The function queries conversations matching awaiting_a_title, orders newest first, limits the result, and returns their ids.

**Call relations**: The Web extension’s title summarizer calls this, then later calls summarized_conversation_title for each processed conversation.

*Call graph*: calls 1 internal fn (awaiting_a_title); called by 1 (summarize_chat_titles); 2 external calls (select, workspace_tx).


##### `ExtensionContext.summarized_conversation_title`  (lines 1649–1654)

```
async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores the summarized title for a conversation and marks title summarization as done. Even an empty or fallback title can mark the attempt complete.

**Data flow**: The caller gives a conversation id and title. The function passes them with the workspace id to the surface title-summary helper.

**Call relations**: The Web extension’s title summarizer calls this after it has generated a title for ids from conversations_awaiting_title.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (summarize_conversation_title).


##### `ExtensionContext.pending_usage_exports`  (lines 1656–1675)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns settled usage-export records for this extension that have not yet been acknowledged. It first mints any newly eligible export intents.

**Data flow**: The caller gives a floor datetime and limit. The function checks that a model key-slot resolver is present, mints export rows inside a transaction, reads pending exports, and returns them.

**Call relations**: Billing exporters use this read seam. It works with ack_usage_exports, which removes successfully delivered exports from the pending set.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 1677–1686)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as acknowledged after an external receiver has accepted them. Unacknowledged exports remain pending for safe retry.

**Data flow**: The caller gives export records. If the tuple is empty, nothing happens; otherwise the function writes acknowledgements inside a workspace transaction.

**Call relations**: This is the completion half of pending_usage_exports. Together they provide at-least-once export delivery with deduplication keys.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 1689–1701)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Gives an extension a database transaction for its own tables and certain SDK-supported core operations. It is powerful because it yields a raw connection, so callers must still scope their own rows correctly.

**Data flow**: The caller enters the async context manager. The function opens a workspace transaction, yields the connection, commits when the block exits normally, and rolls back if an error escapes.

**Call relations**: Memory, metronome, research, and sweep extension code call this for extension-owned SQL work that must be atomic.

*Call graph*: called by 11 (_item, _page, _billing_autopay, _billing_projection, _billing_status, record_sources, _finalize, _prior_ledgers, _tick, _gate (+1 more)); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 1703–1739)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Schedules an internal turn through the injected turn invoker. It refuses to silently drop work when no invoker was wired.

**Data flow**: The caller provides conversation, agent, message, idempotency key, and optional scheduling guards. The function checks an invoker exists, forwards all inputs, and returns the created turn id or null if the invoker refuses under guard rules.

**Call relations**: invoke_agent_for_member and Web homepage seeding call this. Actual admission logic lives behind the TurnInvoker implementation.

*Call graph*: called by 2 (invoke_agent_for_member, seed_homepages).


##### `ExtensionContext.tail`  (lines 1741–1750)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn so side-channel work can watch progress until the turn ends. It fails clearly if no tailer was provided.

**Data flow**: The caller gives a turn id and optional last-seen cursor. The function returns the tailer’s async context manager, which yields live frame updates while entered.

**Call relations**: This exposes the injected TurnTailer without handing extensions direct hub access.


##### `ExtensionContext.turn_is_terminal`  (lines 1752–1766)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final state. A missing turn is treated as terminal because there is nothing live left to follow.

**Data flow**: The caller gives a turn id. The function reads the turn status for the current workspace and returns true if no row exists or the status is one of the terminal statuses.

**Call relations**: Side-channel code can use this alongside tailing to avoid speaking after a turn has already ended.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 1768–1772)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in this workspace, or null if the id does not belong here. It helps callers resolve opaque conversation ids safely.

**Data flow**: The caller gives a conversation id. The function delegates to conversation_agent_id with the current workspace id and returns the result.

**Call relations**: This is the context-level wrapper around the shared conversation_agent_id helper.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 1774–1809)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Fetches audience and surface-label facts for multiple conversations. Member-facing listings use these facts to decide visibility and show where a row came from.

**Data flow**: The caller gives conversation ids. The function queries matching conversations in the current workspace, parses each audience string, and returns a dictionary of ConversationFacts by id.

**Call relations**: It batches what could otherwise become one query per row and treats ids outside the workspace as absent, preserving tenant isolation.

*Call graph*: 4 external calls (__init__, select, parse_audience, workspace_tx).


##### `ExtensionContext.conversation_arrival_seq`  (lines 1811–1838)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Returns the highest member-message arrival sequence for a conversation. This acts as a watermark so later work can tell whether a member spoke after it was armed.

**Data flow**: The caller gives a conversation id. The function queries the maximum member-admitted inbound sequence in the current workspace and returns that number, or zero if none exists.

**Call relations**: Background watchers use this kind of watermark to avoid waking themselves from internal messages or old member messages.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1840–1866)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads how named turns ended, including their final status and final text if present. Status listings use this to show the last run’s outcome.

**Data flow**: The caller gives turn ids. The function queries matching current-workspace turns and returns a dictionary of TurnOutcome objects by id.

**Call relations**: The Web extension uses this while seeding homepages. Missing turn ids are simply absent from the result.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 1868–1874)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Reports whether the current workspace belongs to the fleet operator. This lets UI or extension output hide operator-only details in customer workspaces.

**Data flow**: It reads the workspace domain inside a transaction and compares it with the configured operator email domain, returning true or false.

**Call relations**: It uses the same domain source as surface-level operator gates, keeping operator-only checks consistent.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 1876–1940)

```
async def open_conversation(self, agent_id: UUID, key: str, member_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for an agent and workflow key. Repeated events for the same key return the same conversation instead of creating scattered histories.

**Data flow**: The caller gives an agent id, key, and optional member id. The function verifies the agent belongs to the workspace, inserts a conversation if one does not already exist for this extension/key, sets shared or member-private audience, and returns the conversation id.

**Call relations**: The Web extension uses this while seeding homepages. Turns are not admitted here; callers use invoke afterward.

*Call graph*: called by 1 (seed_homepages); 4 external calls (select, conversation_audience, workspace_tx, uuid4).


##### `ExtensionContext.agent_name`  (lines 1942–1955)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the name of the currently bound agent. Agent-scoped extension objects use this to link or label data by the agent they belong to.

**Data flow**: It reads the current agent scope, queries the matching agent row in that workspace, and returns its name.

**Call relations**: The skill-create extension calls this when resolving skill objects. It depends on agent_current being set by the surrounding turn or portal read.

*Call graph*: called by 1 (_skill); 3 external calls (select, agent_current, workspace_tx).


##### `ExtensionContext.page_states`  (lines 1957–1982)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for live pages by id, without applying reader-specific source permissions. It returns subject, revision, digest, and body reference.

**Data flow**: The caller gives page ids. The function queries non-tombstoned pages in the current workspace and returns PageState objects keyed by page id.

**Call relations**: This is the basic page-state lookup; readable_page_states is the permission-filtered version for reader-specific access.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 1984–2018)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for pages that a specific source reader is allowed to see. It combines page state with source-access rules.

**Data flow**: The caller gives page ids and a SourceReader. The function joins pages to sources, filters by workspace, tombstone status, page subject, and _source_readable, then returns PageState objects by id.

**Call relations**: Memory extension object lookups call this so they only resolve pages visible to the requesting reader.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 2020–2025)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the ids of live sources a reader can access. It is a compact permission-filtered source listing.

**Data flow**: The caller gives a SourceReader. The function queries source ids satisfying _source_readable and returns them as a frozen set.

**Call relations**: It reuses the same helper as readable_page_states and source_pages, keeping source visibility consistent.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 2027–2209)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers or revives a content-sync source for this workspace and grants an agent access to it. A source is something like an external account, folder, or feed whose pages the sync system will later import.

**Data flow**: The caller gives a backend name, typed config, subject, owner, optional connection, and optional target agent. The function derives the stable source id, validates the agent and connection, inserts or revives the source row, checks that authority and identity-sensitive config did not conflict with an existing row, creates a source grant, and returns the source id.

**Call relations**: The sample extension calls this during setup. It uses source_id for stable identity and prepares rows that the source-sync driver later claims and syncs.

*Call graph*: calls 1 internal fn (source_id); called by 1 (_setup); 5 external calls (now, model_dump, select, update, workspace_tx).


##### `ExtensionContext.grant_source`  (lines 2211–2267)

```
async def grant_source(self, source_id: UUID, *, agent_id: UUID, actor_member_id: UUID) -> None
```

**Purpose**: Grants an additional agent access to an existing source without creating another sync row. This avoids syncing the same external feed twice.

**Data flow**: The caller gives a source id, target agent, and acting member. The function verifies the source is live, checks the actor may grant it, verifies the target agent is in the workspace, and inserts the grant if missing.

**Call relations**: It complements register_source, which grants only during registration. This method widens agent authority over an existing feed, not the page disclosure subject itself.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.source_id`  (lines 2269–2287)

```
def source_id(self, backend: str, config: BaseModel, *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Computes the stable id that register_source would use for a source. Callers can use it to recognize a source before it exists or to check whether it was removed.

**Data flow**: The caller gives backend, config, and optional connection id. The function dumps the config to JSON and hashes workspace, backend, config identity fields, and connection into a UUID-like source id.

**Call relations**: register_source calls this before writing. removed_source_ids can then answer whether such computed ids refer to removed rows.

*Call graph*: called by 1 (register_source); 2 external calls (model_dump, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 2289–2312)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which of the given source ids are known removed rows in this workspace. Absence is not treated as removal.

**Data flow**: The caller gives source ids. The function queries rows in the current workspace with removed_at set and returns the matching ids as a frozen set.

**Call relations**: This supports callers that compute source ids themselves and need to distinguish “member deleted this” from “not registered yet.”

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 2314–2354)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists live registered sources in the workspace, optionally for one backend. This is the read side of source registration.

**Data flow**: The caller may give a backend name. The function queries non-removed source rows, orders them, and returns SourceRecord value objects.

**Call relations**: The sources extension uses this when building bindings from extension state. Removed sources are intentionally hidden.

*Call graph*: called by 1 (_bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 2356–2404)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a specific reader. It applies workspace, tombstone, subject, and source-authority checks.

**Data flow**: The caller gives a SourceReader. The function joins pages to sources, filters using _source_readable and reader subjects, and returns PageRecord objects.

**Call relations**: This is the page listing counterpart to readable_source_ids and readable_page_states.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 2406–2422)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten by tombstoning it. Downstream page-change processing can then remove derived index data.

**Data flow**: The caller gives a page id. The function updates that current-workspace page if it is live, setting tombstone and updated time; if no row was updated, it raises an error.

**Call relations**: This provides the write half of a read-and-forget workflow for pages returned by source_pages.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 2424–2461)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source by marking it removed, deleting its grants, and tombstoning its live pages. The source row stays behind so old references remain meaningful.

**Data flow**: The caller gives a source id. The function updates the live source row as removed, clears claim fields, deletes source grants, tombstones pages for that source, and raises if the source was not live.

**Call relations**: The sync driver will no longer claim removed sources, and page-change processing can clean up indexed page state.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 2463–2489)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for one or more live sources and their live pages in one transaction. This restamps pages so indexing can replay under the new visibility.

**Data flow**: The caller gives source ids and a subject. The function updates matching live sources, raises if none matched, then updates all non-tombstoned pages for those sources with the new subject and timestamp.

**Call relations**: This keeps source-level disclosure and page-level disclosure from being torn across separate commits.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 2491–2564)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Updates non-identity sync configuration for live sources, optionally forcing selected sources to refetch from the beginning. It refuses changes that would turn a row into a different source identity.

**Data flow**: The caller gives a mapping from source id to new config and an optional refetch set. The function validates all rows are live, recomputes each source id from its new config, writes allowed config changes, and clears cursor/claim fields for refetched rows.

**Call relations**: This is used when a source’s window or similar non-identity parameter changes. It coordinates with the sync driver’s claim leasing so a refetch request survives in-flight syncs.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 2566–2583)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Moves live sources’ next sync time to now so the sync driver picks them up soon. This is the sanctioned “sync again” button.

**Data flow**: The caller gives source ids. The function updates next_sync_at and updated_at for matching live sources and raises if no live source matched.

**Call relations**: It does not run syncing itself; it signals the existing sync driver to claim the sources on its next pass.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 2585–2591)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Creates a governed proposal to change an agent prompt instead of editing the prompt directly. Approval later checks the prompt digest before applying the change.

**Data flow**: The caller gives an AgentChange. The function constructs a Governance object for the current workspace and extension, submits the change, and returns a proposal reference.

**Call relations**: The sample extension calls this during its tick. It routes prompt changes through the governance system.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 2593–2598)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace’s trajectory corpus through the context. It fails loudly if transcript-reading was not wired into this context.

**Data flow**: It checks that corpus exists, then delegates to TrajectoryCorpus.trajectories and returns the resulting trajectory tuple.

**Call relations**: The sample extension calls this during its tick. It is the ExtensionContext-level entry to the read-only transcript corpus.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 2601–2665)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: WorkspaceBlobStore | None=None, sandboxes:
```

**Purpose**: Builds the ExtensionContext object that a handler receives. It assembles only the capabilities that were wired and declared for that extension or core job.

**Data flow**: The caller supplies the extension name, declared credential slots, optional services such as model resolver, blob store, sandbox access, invoker, tailer, source pages, and member-context flags. The function validates model attribution, constructs capability wrappers, and returns a fully formed ExtensionContext.

**Call relations**: This is the factory that makes core jobs and extensions travel the same scoped path. It wires ScopedStore, CredentialAccess, TrajectoryCorpus, ConversationFiles, ModelAccess, surface installation access, and other optional seams into one context object.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### Conversation slot payloads
Defines and validates the safe, displayable payload shapes that extensions can attach to conversation panels.

### `core/src/ufo/ext/conversation_slots.py`

`data_model` · `cross-cutting`

A conversation can show more than just chat messages. It may also show downloaded files, cited web sources, task lists, published sites, workspace changes, or scheduled automations. This file is the shared contract for that side information: it says exactly what fields each kind of slot may contain, how large they may be, and which values are allowed.

Most of the models here are Pydantic models, meaning they are data objects that validate themselves when created. They reject unexpected fields, are frozen so they cannot be changed after creation, and enforce limits such as maximum title length or maximum number of items. This matters because slot data may come from extensions, and the main app needs predictable, safe data before showing it to users.

The file also includes URL safety checks. Artifact, source, and site links must be normal HTTP or HTTPS links without embedded usernames or passwords. Image preview URLs are stricter: they must be same-origin, root-relative paths, like “/preview/123,” so previews cannot silently point the browser at an outside site.

At the end, the file defines small dataclasses that describe the context given to a slot provider and how an extension registers a provider. In short, it is both the rulebook for conversation-side content and the plug shape that extensions must fit.

#### Function details

##### `ImagePreview.same_origin_url`  (lines 52–65)

```
def same_origin_url(cls, value: str) -> str
```

**Purpose**: This function checks that an image preview URL is safe to use inside the current site. It only allows root-relative links, such as “/images/x.png,” and blocks outside domains, fragments, backslashes, and hidden control characters.

**Data flow**: It receives a URL string from an ImagePreview being created. It decodes and parses the string, then tests whether it stays within the same origin and has no suspicious pieces. If the URL passes, the same string comes out; if not, creation of the preview fails with a clear validation error.

**Call relations**: Pydantic calls this automatically when an ImagePreview is built. Inside the check, it uses standard URL parsing and character inspection tools to understand the string before allowing it into a conversation slot.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 81–92)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: This function checks the optional link attached to an artifact, such as a generated file or uploaded result. It allows no link at all, or a normal HTTP/HTTPS link without usernames or passwords hidden inside it.

**Data flow**: It receives either a URL string or None from a ConversationArtifact. None is passed through unchanged. A real string is parsed, checked for an allowed web scheme, a real host name, and no embedded credentials; valid URLs are returned, while invalid ones stop the artifact from being created.

**Call relations**: Pydantic runs this validator while constructing a ConversationArtifact. The function relies on URL parsing so the rest of the system can trust that any artifact URL is a safe, ordinary web link.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 113–122)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This function validates the URL for a cited source, such as a web page used to support an answer. It keeps source links limited to ordinary HTTP or HTTPS web addresses and rejects links with embedded login details.

**Data flow**: It receives the source URL string, parses it into its parts, and checks the scheme, host name, username, and password fields. If the link is acceptable, the original URL is returned; otherwise the source object is rejected with a validation error.

**Call relations**: Pydantic calls this when a ConversationSource is created. It sits at the boundary where extension-provided citation data enters the app, making sure downstream display code receives a clean web URL.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 151–165)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: This function makes sure a task summary tells a consistent story. For example, it prevents saying there are 3 total tasks but 5 completed tasks, or showing more visible tasks than the stated total.

**Data flow**: It receives the full TasksSlotPayload after its fields have been filled in. It compares total_count, completed_count, the visible task list, each task’s status, and the truncated flag. If all counts match the visible tasks and truncation rules, the same payload is returned; otherwise validation fails with an explanatory error.

**Call relations**: Pydantic runs this after building a TasksSlotPayload, once all fields can be compared together. It does not hand work off to other project functions; its role is to protect the rest of the app from contradictory task data before the slot is displayed.


##### `ConversationSite.http_url`  (lines 181–190)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This function checks that a published or shared site URL is a normal web link. It rejects missing hosts, non-HTTP schemes, and URLs that contain embedded usernames or passwords.

**Data flow**: It receives the site URL string from a ConversationSite being created. It parses the string, verifies that it uses HTTP or HTTPS, has a host name, and carries no credentials. A valid URL is returned unchanged; an invalid one prevents the site object from being accepted.

**Call relations**: Pydantic calls this automatically during ConversationSite validation. It uses URL parsing at the moment site data enters the conversation slot system, so later code can display or serialize the site without repeating these safety checks.

*Call graph*: 1 external calls (urlsplit).


### Extension manifests
Defines the declaration contracts extensions and packs use to advertise their tools, routes, jobs, credentials, agents, skills, hooks, and backends.

### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and cross-cutting extension loading`

This file is like the application’s extension menu template. An extension does not directly wire itself into the running system. Instead, it returns a Manifest: a frozen bundle of declarations saying “I provide these tools,” “I need these credentials,” “mount these web routes,” “run these jobs,” or “add these prompt sections.” The core loader reads these declarations at startup and turns them into real runtime behavior.

Most of the file is made of small immutable data classes. “Immutable” means that once one of these objects is created, its fields are not meant to change. That matters because manifests are treated as trusted declarations, not live control panels. The same pattern is used for packs, which bundle a chosen set of extensions plus pack-level skills or onboarding steps.

The file also defines hook payloads and hook outcomes. Hooks are extension callbacks that can observe or narrow actions during a turn, such as blocking a tool call or adding context, but they cannot grant access that was not already allowed.

A few helper functions check global rules across all active manifests. For example, conversation slot IDs must be unique, and there can only be one open connector namespace. Without these shared declarations and checks, extensions could collide silently or fail much later, during a user request, instead of failing clearly at startup.

#### Function details

##### `AgentProvision.__post_init__`  (lines 531–543)

```
def __post_init__(self) -> None
```

**Purpose**: This validates an agent that an extension wants to create for a workspace. It catches bad agent declarations early, such as an invalid name, a missing prompt, or a setup flow that asks the agent to connect accounts but does not give it the tools needed to do that.

**Data flow**: It starts with a newly created AgentProvision object. It reads the agent name, the embedded agent specification, the optional tool allowlist, and the setup requirements. If the name does not match the project’s safe object-name format, or the prompt is blank, it raises an error. If the agent declares connector setup and also uses a restricted tool list, it checks that the required setup tools are present. Nothing new is returned; a valid object simply finishes construction, while an invalid one is rejected.

**Call relations**: This runs automatically when AgentProvision is created, usually while an extension manifest is being assembled or loaded. It does not hand off to other project functions; its job is to guard the declaration before the loader later turns that declaration into a durable workspace agent.


##### `conversation_slot_declarations`  (lines 676–705)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: This collects all conversation-slot providers declared by active extensions and verifies that they form one clean global set. A conversation slot is a typed piece of extra conversation state that the portal and runtime can read or summarize.

**Data flow**: It receives the active manifests. For each manifest, it looks at each declared conversation slot provider. It checks that the slot ID has a safe format, the label is present and short enough, the icon is one the portal supports, the read and summarize callbacks are callable, and the payload type is supported. It also tracks ownership so two extensions cannot claim the same slot ID. The result is a tuple of pairs, each pairing the owning manifest with its provider. If any rule is broken, it raises an error instead of returning a partial list.

**Call relations**: The extension-loading path uses this when building the deploy-wide view of available conversation slots. It does not call deeper project logic; it acts as the checkpoint that prevents conflicting or unusable slot declarations from reaching the rest of the system.


##### `open_connector_namespace`  (lines 708–720)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This finds the single catch-all connector namespace, if one extension provides it. A connector namespace is a resolver for connector provider names that were not explicitly registered one by one.

**Data flow**: It receives the active manifests and scans them in order. If a manifest has no connector resolver, it skips it. If it finds the first resolver, it remembers it. If it later finds a second one, it raises an error because the system would not know which resolver owns an unknown connector name. The result is either that one resolver or None if no extension declared one.

**Call relations**: The startup wiring uses this before connector flows and registries rely on open-ended provider lookup. By enforcing the “only one catch-all” rule here, later connection handling, catalog lookup, and proxy routing can make one clear decision instead of guessing.


##### `declared_slots`  (lines 741–754)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: This turns extension credential declarations into the simpler credential-slot records used by shared parts of the system, such as the credentials object view and the portal credentials panel. A credential slot is a named secret an extension says it may need.

**Data flow**: It receives the active manifests. It walks through every credential slot in every manifest and creates a DeclaredSlot for each one, copying the slot name, description, owning extension name, whether a member is allowed to fill it, and the host if the slot has network injection settings. It returns all of those DeclaredSlot objects as a tuple. It does not store them itself; it produces the common projection other code can consume.

**Call relations**: This is called when the system needs a deploy-wide list of credential slots derived from manifests. For each manifest credential, it hands the selected fields into DeclaredSlot construction, so downstream code can work with one normalized credential declaration shape instead of the richer extension-only CredentialSlot objects.

*Call graph*: 1 external calls (__init__).
