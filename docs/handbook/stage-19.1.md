# Public SDK authoring and execution helpers  `stage-19.1`

This stage is the public workbench for people who write UFO extensions. It sits behind the scenes, between outside extension code and the deeper runtime, so authors can use stable, safe entry points instead of depending on private internals.

The manifest files define what an extension brings to the system: tools, web routes, background jobs, credentials, hooks, agents, search or browser backends, and other plug-in parts. The context files build the limited “toolbox” an extension receives while running, such as scoped access to storage, credentials, conversations, files, model calls, and workspace records. Tool, job, scheduled-run, authority, and flag modules act as public doors to approved definitions used during execution.

The HTTP and callback-page helpers support browser-facing flows, such as routes, session cookies, and the page shown after a user grants consent or finishes installation. The observability helper lets extensions report logs and metrics in approved ways. The untrusted-content helper marks risky outside text so the system treats it carefully. Together, these files make extension authoring safer, clearer, and more stable.

## Files in this stage

### Runtime extension foundations
Internal runtime modules define the scoped execution toolbox and the manifest language that the public SDK exposes.

### `core/src/ufo/runtime/ext/context.py`

`orchestration` · `cross-cutting: used while extension handlers, background jobs, turn hooks, and member-facing object reads run`

This file is a boundary layer. Instead of handing an extension the whole database, all secrets, or raw file storage, it hands over an `ExtensionContext`: a carefully limited set of abilities for the current workspace. Think of it like giving a contractor a badge that opens only the rooms needed for their job, not the whole building.

The file covers many practical needs. `ScopedStore` gives each extension its own durable key-value storage inside the workspace. `CredentialAccess` lets code read only credential slots the extension declared ahead of time. `TrajectoryCorpus`, `ConversationFiles`, and `ConversationProbes` expose limited ways to read transcripts, write files into a conversation sandbox, or run short diagnostic commands. `ModelAccess` lets background jobs call the language model while checking spending rules and recording billing and metrics. The larger `ExtensionContext` ties these abilities together and adds helpers for scheduled runs, member-visible context, source syncing, conversation lookup, artifact links, turn invocation, and governed agent changes.

The important theme is safety by shape. Most methods first use the currently bound workspace, then query or write only rows belonging to that workspace. Missing wiring fails loudly instead of silently doing nothing. Without this file, extensions would either be too powerless to do useful work or too powerful to trust.

#### Function details

##### `spend_refusal_notice_key`  (lines 111–117)

```
def spend_refusal_notice_key(model: str) -> str
```

**Purpose**: Builds the storage key used to remember that off-turn model spending was refused for one specific model. Keeping the model name in the key prevents one allowed model from accidentally clearing another model’s refusal notice.

**Data flow**: It receives a model name, prefixes it with the fixed refusal-notice label, and returns the combined string. It reads no outside state and changes nothing.

**Call relations**: ModelAccess.turn uses this after a spending check succeeds, so it can delete the old refusal marker for that exact model.

*Call graph*: called by 1 (turn).


##### `ScopedStore.workspace_id`  (lines 132–133)

```
def workspace_id(self) -> UUID
```

**Purpose**: Reports which workspace this scoped store is currently operating in. The workspace is taken from the ambient runtime scope, not passed in by the caller.

**Data flow**: It reads the current workspace binding and returns its workspace id. Nothing is written.

**Call relations**: All ScopedStore database methods rely on this property so extension storage reads and writes stay inside the currently bound workspace.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 135–146)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one JSON value from an extension’s private key-value area in the current workspace. It returns nothing when the key has not been stored.

**Data flow**: It receives a key, opens a workspace transaction, looks for a row matching workspace, extension, and key, then returns the stored value or None.

**Call relations**: Surface and browser-related extensions call this when they need remembered state such as reply progress, hosted run state, or conversation ownership.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_web_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 148–163)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several named keys from an extension’s private storage with one database query. It is useful when a caller already knows the exact keys it wants.

**Data flow**: It receives a sequence of keys. If the list is empty it returns an empty dictionary; otherwise it fetches matching rows for this workspace and extension and returns a key-to-value dictionary, omitting missing keys.

**Call relations**: It is a batched companion to ScopedStore.get and supports callers that want to avoid one database round trip per key.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 165–189)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Writes a JSON value into an extension’s private storage, creating or replacing the key. It uses an atomic upsert, meaning insert-or-update in one database action.

**Data flow**: It receives a key and value, opens a transaction, and writes the row for the current workspace and extension. Existing values are replaced and timestamps are updated.

**Call relations**: Extensions use it to remember durable state, such as browser provider context, Slack progress markers, or web conversation mappings.

*Call graph*: called by 4 (_start, _context, _hold_connect_message, _open_conversation); 1 external calls (workspace_tx).


##### `ScopedStore.put_if`  (lines 191–241)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the current stored value still matches what the caller expected. This protects against accidentally overwriting a newer value written by another task.

**Data flow**: It receives a key, new value, and expected old value. If expected is None, it inserts only if the key is absent; otherwise it locks the row, compares the stored value, updates on a match, and returns true or false.

**Call relations**: Slack reply checkpoint code uses this compare-and-swap behavior when multiple updates might race with each other.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 243–251)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes one key from an extension’s private storage in the current workspace.

**Data flow**: It receives a key, opens a workspace transaction, and deletes the row matching workspace, extension, and key. It returns no value.

**Call relations**: Callers use it to clean up extension state, and ModelAccess.turn indirectly uses the same store to clear spend-refusal notices.

*Call graph*: called by 2 (_drop_turn_reply_records, _open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 253–266)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists stored key-value pairs for this extension, optionally limited to keys with a prefix. This lets an extension inspect its own namespace without reading other extensions’ data.

**Data flow**: It receives an optional prefix, queries rows for the current workspace and extension whose keys start with that prefix, sorts them by key, and returns pairs of key and value.

**Call relations**: Slack and web extension code use it when cleaning up stored records or checking stored access grants.

*Call graph*: called by 3 (_drop_turn_reply_records, _granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 279–280)

```
def workspace_id(self) -> UUID
```

**Purpose**: Reports the workspace whose credentials this access object will read from. Like the store, it uses the ambient workspace binding.

**Data flow**: It reads the current workspace context and returns its id. It does not touch secrets.

**Call relations**: CredentialAccess methods use the same ambient workspace so a handler cannot choose a different tenant’s credentials.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 282–288)

```
async def get(self, slot: str) -> str
```

**Purpose**: Fetches the live secret value for a declared credential slot. It refuses undeclared slots before any secret lookup happens.

**Data flow**: It receives a slot name, checks that the slot was declared, then asks the current workspace for the credential value and returns it. If the slot was not declared, it raises UndeclaredCredentialSlot.

**Call relations**: Slack verification code calls this to read a configured secret, while the declaration check enforces the extension manifest’s promised limits.

*Call graph*: called by 1 (verifying_fingerprint); 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 290–297)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Answers whether a declared credential is stored by the workspace rather than coming from a platform default. This matters for deciding who is paying an outside provider.

**Data flow**: It receives a slot name, checks that it is declared, asks the current workspace whether the credential is workspace-owned, and returns a boolean.

**Call relations**: It supports billing-aware extensions that need to know whether a call used the customer’s own key or the platform’s key.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.rotate`  (lines 299–304)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if its current value matches an expected value. This is used after an external provider rotates a secret.

**Data flow**: It receives a slot, expected old plaintext, and new plaintext. After the declaration check, it asks the workspace to perform the compare-and-swap and returns whether it succeeded.

**Call relations**: It follows the same safety rule as get and stored: undeclared credential slots are rejected before workspace secrets are touched.

*Call graph*: 2 external calls (__init__, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 337–338)

```
def workspace_id(self) -> UUID
```

**Purpose**: Reports the workspace whose conversation transcripts this corpus can read.

**Data flow**: It reads the current workspace binding and returns its workspace id. It does not read transcript data itself.

**Call relations**: TrajectoryCorpus read methods use it to keep transcript access inside the currently bound workspace.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 340–347)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for evaluation or learning jobs. It focuses on the most recent conversations in the current workspace.

**Data flow**: It builds a query for recent conversation ids in this workspace, limited by the corpus limit, then hands that query to _read. It returns decoded Trajectory objects.

**Call relations**: ExtensionContext.trajectories ultimately exposes this to handlers when a trajectory corpus has been wired.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus.conversations`  (lines 349–361)

```
async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]
```

**Purpose**: Reads transcripts for exactly the named conversations, even if they are older than the usual recent-corpus limit. Workspace scoping still applies.

**Data flow**: It receives conversation ids, builds a workspace-scoped query for those ids, sends it to _read, and returns the trajectories that could be decoded.

**Call relations**: This is the targeted counterpart to trajectories, sharing the same decoding and skipping behavior through _read.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus._read`  (lines 363–407)

```
async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]
```

**Purpose**: Turns chosen conversation rows into Trajectory objects by fetching transcript blobs and decoding them. Missing or corrupt transcripts are skipped instead of failing the whole read.

**Data flow**: It receives a SQL subquery choosing conversation ids, fetches conversation, turn, and agent prompt rows, reads each transcript blob, decodes it into messages, computes the prompt digest, and returns a tuple of Trajectory objects.

**Call relations**: Both TrajectoryCorpus.trajectories and TrajectoryCorpus.conversations delegate here so all transcript reads share the same workspace-safe, skip-bad-records behavior.

*Call graph*: called by 2 (conversations, trajectories); 7 external calls (__init__, select, workspace_tx, log, prompt_digest, decode, transcript_key).


##### `ConversationFiles.write`  (lines 426–429)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s visible workspace files. The agent can see the resulting path on a later turn.

**Data flow**: It receives a conversation id, relative path, and bytes. It delegates to the conversation sandbox and returns the `/workspace` path that was written.

**Call relations**: This is the narrow file-writing capability exposed through ExtensionContext.files, while sandbox internals remain hidden.


##### `ConversationFiles.prune`  (lines 431–437)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a conversation file prefix, keeping only the newest few. This prevents unattended writers from filling the sandbox forever.

**Data flow**: It receives a conversation id, path prefix, and keep count, then asks the sandbox layer to remove older matching files. It returns nothing.

**Call relations**: It pairs with ConversationFiles.write for extensions that append files over time and need cleanup.


##### `ConversationFiles.write_runtime`  (lines 439–443)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output under a named category in a conversation’s sandbox. This separates system files from ordinary workspace files.

**Data flow**: It receives a conversation id, category, relative path, and bytes, then delegates the categorized write to the sandbox layer and returns the visible path.

**Call relations**: It is exposed only through the conversation file capability, keeping category and sandbox details behind the context boundary.


##### `ConversationFiles.prune_runtime`  (lines 445–453)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Prunes older internal runtime files under a category and prefix. It keeps runtime directories bounded just like regular conversation files.

**Data flow**: It receives a conversation id, runtime category, path prefix, and keep count, then asks the sandbox layer to delete old matching files.

**Call relations**: It complements write_runtime for recurring internal outputs.


##### `conversation_agent_id`  (lines 456–469)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Looks up which agent a conversation belongs to inside a workspace. It returns None when the conversation id is not in that workspace.

**Data flow**: It receives a workspace id and conversation id, queries the conversation table, and returns the agent id or None.

**Call relations**: ConversationProbes.run uses it before opening a sandbox under the right agent, and ExtensionContext.conversation_agent exposes the same lookup to handlers.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 508–562)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, *, authority: ExecutionAuthority) -> ExecResult
```

**Purpose**: Runs a short shell command inside a conversation’s sandbox outside the normal turn flow. It is for bounded probes, not long-running background work.

**Data flow**: It receives a conversation id, command, timeout, and execution authority. It validates the timeout, checks the conversation and authority, creates a short-lived probe token, opens the sandbox under the conversation’s agent, runs `bash`, and returns stdout, stderr, and exit code.

**Call relations**: It depends on conversation_agent_id, seat checks, probe token encoding, the sandbox opener, and an injected environment builder. Extension contexts wire it only where off-turn sandbox execution is allowed.

*Call graph*: calls 1 internal fn (conversation_agent_id); 8 external calls (__init__, __init__, __init__, now, workspace_tx, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 565–587)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate-workspace selector for jobs that need conversation transcripts. It finds workspaces that have at least one conversation with at least one turn.

**Data flow**: It defines the inner SQL query and wraps it in owner_candidates, returning a WorkspaceCandidates object used by job dispatch.

**Call relations**: Background jobs declare this selector so the dispatcher knows which workspaces might have trajectory data before binding and running them.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 573–585)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query used by trajectory_workspaces to find workspaces with turn-bearing conversations.

**Data flow**: It produces a select statement over workspaces with nested existence checks for conversations and turns. It does not execute the query itself.

**Call relations**: trajectory_workspaces passes this query factory to owner_candidates, which later uses it during candidate discovery.

*Call graph*: 2 external calls (exists, select).


##### `seated_member_workspaces`  (lines 590–600)

```
def seated_member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate-workspace selector for first-party jobs that require at least one active seated member.

**Data flow**: It defines a query for distinct workspace ids from members with a non-null seated time and wraps it as WorkspaceCandidates.

**Call relations**: Jobs use this seam when there is no point running in workspaces without active members.

*Call graph*: 1 external calls (owner_candidates).


##### `seated_member_workspaces.with_a_seated_member`  (lines 593–598)

```
def with_a_seated_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces containing at least one seated member.

**Data flow**: It selects distinct workspace ids from the member table where seated_at is present. The query is returned, not executed here.

**Call relations**: seated_member_workspaces hands this query factory to owner_candidates.

*Call graph*: 1 external calls (select).


##### `connection_workspaces`  (lines 603–616)

```
def connection_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate-workspace selector for jobs driven by connected accounts. It finds workspaces whose main agent has a connector grant.

**Data flow**: It defines a query over connector grants joined to main agents, then wraps that query factory in owner_candidates.

**Call relations**: Connection-driven background handlers use this so they run only where a main agent has something connected.

*Call graph*: 1 external calls (owner_candidates).


##### `connection_workspaces.with_a_main_agent_connection`  (lines 608–614)

```
def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces where the main agent has a connector grant.

**Data flow**: It selects distinct workspace ids from connector grants joined to agents marked as main. It returns the query for later execution.

**Call relations**: connection_workspaces supplies this query factory to the candidate dispatcher.

*Call graph*: 1 external calls (select).


##### `agent_is_live`  (lines 619–632)

```
def agent_is_live(workspace_id: sa.ColumnElement[UUID], agent_id: sa.ColumnElement[UUID]) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition that is true only for an unarchived agent belonging to a workspace. It is used by sweeps that should avoid doing expensive work for archived agents.

**Data flow**: It receives SQL expressions for workspace id and agent id, creates an exists condition checking matching, unarchived agent rows, and returns that condition.

**Call relations**: Other query builders can include this predicate before reaching the turn-invocation seam, so they do not spend effort on work that would later be refused.

*Call graph*: 2 external calls (exists, select).


##### `awaiting_a_title`  (lines 638–654)

```
def awaiting_a_title() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition for conversations that still need an automatic title. A conversation qualifies only after a member turn has completed.

**Data flow**: It returns a condition requiring title_summarized to be false and requiring at least one completed member-admitted turn in that conversation.

**Call relations**: Untitled-conversation candidate discovery and ExtensionContext.conversations_awaiting_title both use this same definition of title work.

*Call graph*: called by 2 (conversations_awaiting_title, with_an_unsummarized_title); 3 external calls (and_, exists, select).


##### `untitled_conversation_workspaces`  (lines 657–666)

```
def untitled_conversation_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate-workspace selector for the title-summary job. It finds workspaces containing conversations that still need titles.

**Data flow**: It defines a query selecting distinct workspace ids from conversations matching awaiting_a_title and wraps it in owner_candidates.

**Call relations**: The titling job declares this so it does not run in workspaces whose conversations are already summarized.

*Call graph*: 1 external calls (owner_candidates).


##### `untitled_conversation_workspaces.with_an_unsummarized_title`  (lines 663–664)

```
def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query for workspaces with at least one conversation awaiting a title.

**Data flow**: It selects distinct conversation workspace ids where awaiting_a_title is true and returns the query.

**Call relations**: untitled_conversation_workspaces passes this query factory to the owner-candidate machinery.

*Call graph*: calls 1 internal fn (awaiting_a_title); 1 external calls (select).


##### `unseeded_agent_workspaces`  (lines 669–698)

```
def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Builds a candidate-workspace selector for once-per-agent setup sweeps. It finds workspaces where the number of agents is greater than the number of extension store markers with a given prefix.

**Data flow**: It receives an extension name and marker prefix, defines count subqueries for agents and settled keys, and returns owner_candidates around a query selecting workspaces where agents outnumber markers.

**Call relations**: Extensions use this pattern to run setup once per agent and stop scheduling that workspace once every agent has been marked settled.

*Call graph*: 1 external calls (owner_candidates).


##### `unseeded_agent_workspaces.with_an_unsettled_agent`  (lines 681–696)

```
def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that compares agent count with extension marker count for each workspace.

**Data flow**: It counts agent rows and matching extension-store keys per workspace, then returns a query for workspace ids where the agent count is larger.

**Call relations**: unseeded_agent_workspaces supplies this query factory to owner_candidates.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 730–744)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, authority: ExecutionAuthority, holds_work_already_done: bool=False, as_scheduled: bool=False, stand
```

**Purpose**: Protocol method describing how a background handler starts an internal turn. A protocol is an interface: it says what methods an object must provide without naming its concrete class.

**Data flow**: An implementation receives conversation, agent, message, idempotency, authority, and admission options, then returns the admitted turn id or None when admission is intentionally skipped.

**Call relations**: ExtensionContext.invoke calls this injected method when a handler needs the normal turn system to do work.


##### `TurnInvoker.member_reach`  (lines 746–746)

```
async def member_reach(self, member_id: UUID, limit: int) -> tuple[MemberReach, ...]
```

**Purpose**: Protocol method describing how to find durable conversations where a member can be reached.

**Data flow**: An implementation receives a member id and limit, looks up recent reachable conversations, and returns MemberReach records.

**Call relations**: ExtensionContext.member_reach delegates to this when member-context reads are allowed and an invoker is wired.


##### `ModelResolver.auto_model`  (lines 756–756)

```
def auto_model(self) -> str
```

**Purpose**: Protocol property naming the deployment’s default model for background model calls.

**Data flow**: An implementation returns a model id string. It changes no state.

**Call relations**: ModelAccess.model and ModelAccess.turn read it to force every call through the configured background model.


##### `ModelResolver.pricing`  (lines 759–759)

```
def pricing(self) -> Pricing
```

**Purpose**: Protocol property exposing the price table used to bill model usage.

**Data flow**: An implementation returns Pricing information. ModelAccess uses it when recording token usage.

**Call relations**: ModelAccess.turn reads this while inside a billable event so usage is priced consistently with normal turns.


##### `ModelResolver.client_for`  (lines 761–761)

```
async def client_for(self, model: str) -> ResolvedModelClient
```

**Purpose**: Protocol method for getting the actual model client for a model in the current workspace. The client may use a workspace-provided key or a platform key.

**Data flow**: An implementation receives a model name, resolves credentials and provider setup, and returns a ResolvedModelClient.

**Call relations**: ModelAccess.turn calls it after spending gates pass, just before streaming the completion.


##### `ModelResolver.key_slot_for`  (lines 763–763)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Protocol method that tells which credential slot, if any, a model uses.

**Data flow**: An implementation receives a model name and returns a credential slot name or None.

**Call relations**: ModelAccess.turn passes this into balance checks, and usage export code uses it to understand model-key ownership.


##### `ModelResolver.provider_for`  (lines 765–765)

```
def provider_for(self, model: str) -> str
```

**Purpose**: Protocol method that names the provider behind a model, such as the company or backend serving it.

**Data flow**: An implementation receives a model name and returns a provider string.

**Call relations**: ModelAccess.turn uses it as a metric label so operators can compare model cost and latency by provider.


##### `ModelAccess.model`  (lines 794–796)

```
def model(self) -> str
```

**Purpose**: Returns the background model id this access object will use. Callers cannot choose a different model through this seam.

**Data flow**: It reads the resolver’s auto_model property and returns it.

**Call relations**: This mirrors the model that ModelAccess.turn will force onto every request.


##### `ModelAccess.complete`  (lines 798–805)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one background model call and returns only the assistant’s text. It is a convenience wrapper for callers that do not need tool-call blocks.

**Data flow**: It receives a ModelRequest, calls turn, then extracts plain text from the returned assistant message and returns that string.

**Call relations**: Memory extension writers and summarizers call this when they need text output; it delegates all spending, billing, streaming, and metrics to ModelAccess.turn.

*Call graph*: calls 1 internal fn (turn); called by 3 (_summarize, _write, _summarize).


##### `ModelAccess.turn`  (lines 807–932)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs a metered background language-model turn, including spending checks, streaming, billing, metrics, and tool-call reconstruction. It prevents off-turn jobs from bypassing normal cost controls.

**Data flow**: It receives a ModelRequest, checks balance and spend gates for the current workspace, clears any old refusal notice on success, resolves the model client, streams model events, records usage, builds text, reasoning, and tool-call blocks, emits metrics, and returns an assistant Message. If gates refuse or the model fails, it raises an error and still records latency for failures.

**Call relations**: ModelAccess.complete calls it for text-only completions, and memory-extension code calls it directly when it needs tool-aware messages.

*Call graph*: calls 2 internal fn (__init__, spend_refusal_notice_key); called by 3 (complete, _curate, _write); 14 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, model_copy, loads, monotonic (+4 more)).


##### `_source_readable`  (lines 996–1023)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the SQL rule for whether a source can be read by a specific agent/member reader. It combines workspace, removal status, subject visibility, and source grants.

**Data flow**: It receives a workspace id and SourceReader, creates SQL existence checks for granted sources and owner-main-agent access, and returns a boolean SQL expression.

**Call relations**: Readable page and source lookup methods reuse this helper so they all enforce the same source-access rule.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `MemberContextRecord._aware_utc`  (lines 1076–1077)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures member-context timestamps have timezone information. Naive timestamps are treated as UTC.

**Data flow**: It receives a datetime value and returns it unchanged if already timezone-aware, otherwise returns a copy marked with UTC.

**Call relations**: Pydantic calls this validator when MemberContextRecord objects are created.

*Call graph*: 1 external calls (replace).


##### `_member_blob_text`  (lines 1083–1101)

```
async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str
```

**Purpose**: Reads a bounded amount of text from blob storage for member context. The size cap prevents large files from being pulled fully into memory.

**Data flow**: It receives a blob store and blob key, streams chunks until the byte limit is reached, closes the stream when needed, decodes the bounded bytes as text, and handles a cut-off multibyte character at the end.

**Call relations**: ExtensionContext.member_context uses it to include text previews from shared artifacts and synced pages.

*Call graph*: calls 1 internal fn (get_stream); called by 1 (member_context).


##### `ExtensionContext.workspace_id`  (lines 1130–1131)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id for this context. It follows the context’s scoped store, so all helpers share the same workspace boundary.

**Data flow**: It reads `self.store.workspace_id` and returns that UUID.

**Call relations**: Many ExtensionContext methods use this property when calling helper functions that need an explicit workspace id.


##### `ExtensionContext.image_preview_url`  (lines 1133–1147)

```
def image_preview_url(self, blob_key: str, size_bytes: int) -> str | None
```

**Purpose**: Creates a signed preview URL for an image blob when artifact links are configured and the blob is eligible.

**Data flow**: It receives a blob key and size, passes the deploy secret, public base URL, blob details, and workspace id to the artifact URL helper, and returns a URL or None.

**Call relations**: The sites extension uses this when rendering rows with image previews, without receiving the signing secret directly.

*Call graph*: called by 1 (_preview_url); 1 external calls (mint_image_preview_url).


##### `ExtensionContext.artifact_link`  (lines 1149–1155)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed temporary download link for a shared artifact. It returns None when this deployment cannot mint artifact links.

**Data flow**: It receives a SharedArtifact and passes the artifact, workspace id, public URL, and signing secret to the shared-artifact helper.

**Call relations**: Report digest object rendering uses this to show downloadable files safely.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_link).


##### `ExtensionContext.artifact_preview_link`  (lines 1157–1162)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview link for a shared artifact when the artifact can be previewed as an image.

**Data flow**: It receives a SharedArtifact, sends it with signing and workspace data to the preview-link helper, and returns a URL or None.

**Call relations**: Report digest rendering calls this beside artifact_link to show previews where possible.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_preview_link).


##### `ExtensionContext.scheduled_runs`  (lines 1164–1188)

```
async def scheduled_runs(self, member_id: UUID, *, agent_id: UUID | None, limit: int, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Reads recent scheduled turns visible to a member, including their terminal replies and shared files. It is used for member-facing listings.

**Data flow**: It receives member id, filters such as agent id or turn id, a limit, and optional subjects, then delegates to the surface-layer scheduled_runs helper with this workspace id.

**Call relations**: Report digest object pages use this to show scheduled run history without duplicating the visibility logic.

*Call graph*: called by 2 (_one, _page); 1 external calls (scheduled_runs).


##### `ExtensionContext.home_url`  (lines 1190–1201)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link to the deployment’s browser portal surface. It returns None when no public base URL or home surface is configured.

**Data flow**: It receives an optional URL fragment, trims the public base URL, combines it with `/surface/` and the configured home surface, and returns the final link.

**Call relations**: Metronome and sample extension code use this to send members back to the product’s web surface.

*Call graph*: called by 2 (_billing_portal, _hook).


##### `ExtensionContext.workspace_agents`  (lines 1203–1235)

```
async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]
```

**Purpose**: Returns the workspace’s agents, including archived ones, for trusted first-party jobs. It is permission-gated because it exposes member and agent roster data.

**Data flow**: It checks member-context read permission, queries agents in the workspace ordered by creation, converts rows into WorkspaceAgent objects, and returns them.

**Call relations**: App notification and web homepage seeding code call this during setup sweeps.

*Call graph*: called by 2 (inbox_agent_id, seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.agent_visibilities`  (lines 1237–1249)

```
async def agent_visibilities(self) -> dict[UUID, AgentVisibility]
```

**Purpose**: Reads every agent’s portal visibility setting in the workspace. This tells extensions the audience floor for things attached to each agent.

**Data flow**: It queries agent ids and visibility values for the current workspace and returns a dictionary keyed by agent id.

**Call relations**: It is available without the broader member-context gate because it describes workspace shape rather than private member content.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.agent_named`  (lines 1251–1268)

```
async def agent_named(self, name: str) -> AgentIdentity | None
```

**Purpose**: Finds a live agent by its stable name and returns its id and owner. Archived agents do not match.

**Data flow**: It receives an agent name, queries the workspace for a non-archived agent with that name, and returns an AgentIdentity or None.

**Call relations**: Member-facing object actions can use this to resolve names like `agent/<name>` before checking authority.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.earliest_seated_admin`  (lines 1270–1288)

```
async def earliest_seated_admin(self) -> UUID | None
```

**Purpose**: Finds the earliest seated admin in the workspace. This gives ownerless background work a deterministic member to act on behalf of.

**Data flow**: It checks member-context read permission, queries seated admin members ordered by seat time and id, and returns the first member id or None.

**Call relations**: Web homepage seeding uses it when an agent has no owner member.

*Call graph*: called by 1 (seed_homepages); 2 external calls (select, workspace_tx).


##### `ExtensionContext.scheduled_member_timezone`  (lines 1290–1306)

```
async def scheduled_member_timezone(self) -> str
```

**Purpose**: Returns the timezone of the member whose authority is bound to scheduled work. If the member has no timezone, it returns UTC.

**Data flow**: It extracts a member id from the execution authority, checks permissions, queries that member’s timezone in this workspace, and returns the timezone string or UTC.

**Call relations**: Scheduled jobs use this when they need to interpret member-local times.

*Call graph*: 3 external calls (select, workspace_tx, authority_member_id).


##### `ExtensionContext.member_context`  (lines 1308–1464)

```
async def member_context(self, *, since: datetime, limit: int=200, exclude_conversation_id: UUID | None=None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Collects recent context visible to the scheduled member, such as conversations, shared files, synced pages, memories, tasks, and objectives. It is bounded so a job gets a useful recent slice, not the entire workspace history.

**Data flow**: It checks permission and member authority, validates the limit, reads recent turns, readable artifacts, readable pages, and extension-owned records, fetches small text bodies from blobs when needed, builds MemberContextRecord objects, sorts them newest first, and returns up to the limit.

**Call relations**: It calls _member_blob_text for blob previews and _member_extension_records for memory and objective records, giving scheduled handlers one combined context feed.

*Call graph*: calls 2 internal fn (_member_extension_records, _member_blob_text); 7 external calls (__init__, exists, select, workspace_tx, authority_member_id, is_text_media, readable_audiences).


##### `ExtensionContext._member_extension_records`  (lines 1466–1717)

```
async def _member_extension_records(self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int, exclude_conversation_id: UUID | None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Adds extension-owned memory, task, and objective records to member context. It knows about these extension tables without making the main member_context method even larger.

**Data flow**: It receives member visibility inputs, time bounds, limit, and an optional conversation exclusion. It queries memory and objective tables, checks which objective steps are still open, computes stable keys, builds MemberContextRecord objects, and returns them.

**Call relations**: Only ExtensionContext.member_context calls this, merging its records with core conversations, artifacts, and synced pages.

*Call graph*: called by 1 (member_context); 8 external calls (__init__, sha256, DateTime, column, or_, select, table, workspace_tx).


##### `ExtensionContext.retitle_conversation`  (lines 1719–1722)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Sets a conversation title in this workspace. It is a simple write path for jobs that decide a better name.

**Data flow**: It receives a conversation id and title, then delegates to the surface-layer retitle helper with this workspace id.

**Call relations**: It gives extensions a scoped way to rename conversations without direct access to the surface implementation.

*Call graph*: 1 external calls (retitle_conversation).


##### `ExtensionContext.conversations_awaiting_title`  (lines 1724–1748)

```
async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]
```

**Purpose**: Lists this workspace’s newest conversations that still need automatic title summaries. The limit bounds how much model work a title job does per tick.

**Data flow**: It receives a limit, queries workspace conversations matching awaiting_a_title, orders newest first, and returns their ids.

**Call relations**: The web extension’s title summarizer calls this before generating and storing conversation titles.

*Call graph*: calls 1 internal fn (awaiting_a_title); called by 1 (summarize_chat_titles); 2 external calls (select, workspace_tx).


##### `ExtensionContext.summarized_conversation_title`  (lines 1750–1755)

```
async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores a summarized title and marks the title-summary attempt complete. Even an unhelpful generated title can retire the work.

**Data flow**: It receives a conversation id and title, then delegates to summarize_conversation_title with this workspace id.

**Call relations**: The web title summarizer calls it after processing ids from conversations_awaiting_title.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (summarize_conversation_title).


##### `ExtensionContext.pending_usage_exports`  (lines 1757–1776)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Reads settled usage records that this extension has not yet acknowledged for export. It mints export intents first so the returned records are stable for retry.

**Data flow**: It checks that a model key-slot resolver is wired, opens a workspace transaction, mints usage exports for this extension after the floor time, reads up to the limit pending exports, and returns them.

**Call relations**: Billing export handlers use this before sending usage to an external receiver.

*Call graph*: 3 external calls (workspace_tx, mint_usage_exports, read_pending_usage_exports).


##### `ExtensionContext.ack_usage_exports`  (lines 1778–1787)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered after an external system accepts them. Unacknowledged records remain pending for retry.

**Data flow**: It receives a tuple of UsageExport records. If empty it returns immediately; otherwise it opens a transaction and acknowledges those exports for this workspace and extension.

**Call relations**: It is the second half of pending_usage_exports: exporters call it only after successful delivery.

*Call graph*: 2 external calls (workspace_tx, ack_usage_exports).


##### `ExtensionContext.transaction`  (lines 1790–1802)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides an async database transaction for an extension’s own tables and selected SDK helpers. This is powerful because it yields a raw connection, so callers must scope their own queries correctly.

**Data flow**: It opens workspace_tx, yields the database connection to the caller’s async block, commits on normal exit, and rolls back if an error escapes.

**Call relations**: Many extension object pages and jobs use this when they need to read or write their own extension-defined tables.

*Call graph*: called by 18 (tick, _entry, _page, _item, _page, _entry, _page, _billing_autopay, _billing_projection, _billing_status (+8 more)); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 1804–1845)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, authority: ExecutionAuthority, holds_work_already_done: bool=False, as_scheduled: bool=False, stand
```

**Purpose**: Starts an internal turn in a conversation through the injected turn invoker. It requires explicit authority so automatic work never runs anonymously.

**Data flow**: It receives conversation, agent, message, idempotency key, authority, and admission options. It fails if no invoker is wired, then forwards the request and returns the resulting turn id or None.

**Call relations**: Source trigger and web homepage seeding code use this to ask the normal turn engine to perform work.

*Call graph*: called by 2 (_fire_trigger, seed_homepages).


##### `ExtensionContext.member_reach`  (lines 1847–1857)

```
async def member_reach(self, member_id: UUID, limit: int=4) -> tuple[MemberReach, ...]
```

**Purpose**: Finds recent durable conversations where a member can be reached. It is gated because it reveals member conversation routing.

**Data flow**: It checks member-context read permission and that an invoker is wired, then forwards the member id and limit to the invoker and returns MemberReach records.

**Call relations**: It uses the same injected TurnInvoker seam as invoke, but for lookup rather than starting a turn.


##### `ExtensionContext.tail`  (lines 1859–1868)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Subscribes to live frames from a turn until the turn ends. This lets side-channel work watch a turn it started or follows.

**Data flow**: It receives a turn id and optional cursor string, checks that a tailer is wired, and returns the tailer’s async context manager for streaming frames.

**Call relations**: Handlers use this instead of touching the live hub directly; leaving the async context cleans up the subscription.


##### `ExtensionContext.turn_is_terminal`  (lines 1870–1884)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a terminal status. A missing turn is treated as terminal because there is nothing left to follow.

**Data flow**: It receives a turn id, queries that turn’s status inside this workspace, and returns true if no row exists or the status is terminal.

**Call relations**: Side-channel handlers can call this before speaking for a turn they are tailing or polling.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 1886–1890)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in this workspace, or None if the id does not belong here.

**Data flow**: It receives a conversation id and delegates to conversation_agent_id with this context’s workspace id.

**Call relations**: This exposes the shared conversation-agent lookup used internally by ConversationProbes.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 1892–1927)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Reads audience and surface label for a batch of conversations. These facts help member-facing listings decide visibility and origin.

**Data flow**: It receives conversation ids, returns an empty dictionary for none, otherwise queries matching workspace conversations, parses audience strings, builds ConversationFacts, and returns them keyed by conversation id.

**Call relations**: Object listings can batch this lookup instead of doing one database read per row.

*Call graph*: 4 external calls (__init__, select, workspace_tx, parse_audience).


##### `ExtensionContext.conversation_arrival_seq`  (lines 1929–1956)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Reads the latest member-message arrival sequence for a conversation. This acts like a watermark for deciding whether a member spoke after some work was armed.

**Data flow**: It receives a conversation id, queries the maximum sequence number among member-admitted inbound messages in this workspace conversation, and returns that number or 0.

**Call relations**: Wakeup logic can compare this value to a saved watermark to avoid reacting to its own internal messages.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1958–1984)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads final status and terminal text for a batch of turns. Missing turns are simply absent from the result.

**Data flow**: It receives turn ids, returns an empty dictionary for none, otherwise queries matching workspace turns and builds TurnOutcome objects keyed by turn id.

**Call relations**: Web homepage seeding uses this to render the status of previous runs without one query per turn.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 1986–1992)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace belongs to the platform operator. This gates operator-only rendering such as debugging or spend details.

**Data flow**: It opens a workspace transaction, reads the workspace domain, compares it to the operator email domain, and returns a boolean.

**Call relations**: Member-facing code can call this before exposing information meant only for the operator’s own workspace.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 1994–2058)

```
async def open_conversation(self, agent_id: UUID, key: str, member_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for a workflow key and agent. Reusing the same key keeps related automatic events in one history.

**Data flow**: It receives an agent id, key, and optional member id. It verifies the agent belongs to the workspace, inserts a conversation if one does not already exist for this extension and key, assigns shared or member-specific audience, then returns the conversation id.

**Call relations**: Source triggers and web homepage seeding use it before invoking turns in extension-created conversations.

*Call graph*: called by 2 (_fire_trigger, seed_homepages); 4 external calls (select, workspace_tx, conversation_audience, uuid4).


##### `ExtensionContext.agent_name`  (lines 2060–2074)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the stable name of the currently bound agent. It can use an archived name when the agent has been archived.

**Data flow**: It reads the current agent scope, queries that agent in its workspace, and returns the coalesced archived or live name.

**Call relations**: Agent-scoped object kinds use this to link back to the agent they belong to.

*Call graph*: 3 external calls (select, workspace_tx, agent_current).


##### `ExtensionContext.page_states`  (lines 2076–2105)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for named live synced pages in this workspace. It does not check source readability; it is a workspace-level lookup.

**Data flow**: It receives page ids, returns an empty dictionary for none, otherwise queries non-tombstoned pages and returns PageState objects keyed by page id.

**Call relations**: Extensions can use this when they already have authority to name the pages and need current revision or blob references.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 2107–2145)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for named pages only if a given reader can see them. It applies both page subject visibility and source authority.

**Data flow**: It receives page ids and a SourceReader, returns empty for none, joins pages to sources, applies _source_readable and subject filters, then returns PageState objects keyed by page id.

**Call relations**: Memory object rendering calls this before showing page-backed records to a reader.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 2147–2152)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the ids of live sources readable by a given reader. It is a compact authority check over all sources.

**Data flow**: It receives a SourceReader, builds a query using _source_readable, executes it for this workspace, and returns a frozenset of source ids.

**Call relations**: It shares the same visibility helper as source_pages and readable_page_states.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 2154–2339)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers or revives a content-sync source for the workspace and grants an agent access to it. It keeps one source row for the same backend, authority, and identity-defining configuration.

**Data flow**: It receives backend, typed config, subject, owner, optional connection id, and optional agent id. It derives the source id, verifies the target agent and connection authority, inserts or revives the source row, checks conflicting owner/subject/requested fields, writes a source grant, and returns the source id.

**Call relations**: The sample extension setup calls this; the sync driver later claims registered source rows and writes pages from them.

*Call graph*: calls 1 internal fn (source_id); called by 1 (_setup); 5 external calls (now, model_dump, select, update, workspace_tx).


##### `ExtensionContext.grant_source`  (lines 2341–2397)

```
async def grant_source(self, source_id: UUID, *, agent_id: UUID, actor_member_id: UUID) -> None
```

**Purpose**: Grants an existing live source to another agent without creating a duplicate sync row. It enforces that the actor owns the source or that the source is shared.

**Data flow**: It receives a source id, target agent id, and actor member id. It verifies the source and actor authority, verifies the target agent is in the workspace, then inserts a source_grant row if absent.

**Call relations**: It complements register_source: registration creates the source and first grant, while this adds later grants.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.source_id`  (lines 2399–2417)

```
def source_id(self, backend: str, config: BaseModel, *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Computes the deterministic id that register_source would use for a source. This lets callers refer to a source before it exists.

**Data flow**: It receives backend, config, and optional connection id, dumps the config to JSON, passes workspace, backend, config, connection id, and non-identity fields to source_row_id, and returns the UUID.

**Call relations**: register_source calls it, and callers can pair it with removed_source_ids to distinguish never-registered sources from deleted ones.

*Call graph*: called by 1 (register_source); 2 external calls (model_dump, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 2419–2442)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which of a given set of source ids have been removed in this workspace. Absence is not treated as removal.

**Data flow**: It receives source ids, returns an empty frozenset if none, otherwise queries rows in this workspace whose removed_at is set and returns their ids.

**Call relations**: Extensions use this positive evidence before deciding how to clean up state tied to deleted feeds.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 2444–2488)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists this workspace’s live registered sources, optionally for one backend. Removed sources are hidden.

**Data flow**: It builds a workspace-scoped query, optionally filters by backend, orders rows, converts them into SourceRecord objects, and returns them.

**Call relations**: Gbrain and source-extension tooling use this to discover currently registered feeds.

*Call graph*: called by 2 (_registered_from_ext, _bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 2490–2538)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a specific source reader. It applies workspace, tombstone, subject, and source-authority filters.

**Data flow**: It receives a SourceReader, joins page and source rows, applies _source_readable, converts result rows into PageRecord objects, and returns them.

**Call relations**: This is the read side of the source-sync system for extensions that need page metadata without inlining page bodies.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 2540–2556)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Tombstones one live page so downstream indexing can remove derived data. It fails if the page is unknown or already tombstoned.

**Data flow**: It receives a page id, updates the matching live page in this workspace to tombstone true with a fresh timestamp, and raises ValueError if no row was changed.

**Call relations**: It is the page-level cleanup counterpart to remove_source.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 2558–2595)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source and tombstones its live pages in one transaction. The source row remains as a durable reference and can be revived later.

**Data flow**: It receives a source id, marks the live source removed, clears claims, deletes source grants, tombstones live pages for that source, and raises if no live source matched.

**Call relations**: The sync driver will stop claiming removed rows, and page-change processing can clean derived index state from tombstoned pages.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 2597–2623)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for live sources and their live pages together. This makes page visibility and source visibility move atomically.

**Data flow**: It receives source ids and a subject, updates matching live source rows, raises if none matched, then updates non-tombstoned pages for those sources with the new subject and timestamp.

**Call relations**: Downstream page-change replay sees the updated timestamps and can re-index pages under their new disclosure subject.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 2625–2698)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Updates non-identity configuration fields for live sources, optionally forcing selected sources to refetch from scratch. It refuses changes that would make a row’s id no longer match its identity.

**Data flow**: It receives a mapping of source ids to configs and an optional refetch set. It validates inputs, locks live source rows, recomputes each row id from the proposed config, raises on mismatch or missing rows, updates config, and clears cursor/claim fields for refetched rows.

**Call relations**: Source-management code uses this when changing sync windows or similar parameters without changing which dataset the source represents.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 2700–2729)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Requests that live sources sync as soon as possible. It also clears parked/refusal markers so a manual resync can wake a source that had slowed down.

**Data flow**: It receives source ids, updates matching live source rows to set next_sync_at to now, clear parking and refusal counters, and raises if no live source matched.

**Call relations**: The source sync driver will claim these rows on its next pass.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 2731–2737)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Creates a governed proposal to change an agent, rather than directly editing the agent. Approval later applies the change only if the expected prompt digest still matches.

**Data flow**: It receives an AgentChange, constructs Governance for this workspace and extension, submits the change, and returns a ProposalRef.

**Call relations**: The sample extension tick calls this to demonstrate safe, reviewable agent changes.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 2739–2744)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace’s trajectory corpus through the wired corpus reader. It fails loudly if transcript access was not wired into the context.

**Data flow**: It checks that `self.corpus` is present, calls its trajectories method, and returns the resulting Trajectory tuple.

**Call relations**: Sample extension code calls this during ticks that inspect past conversation transcripts.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 2747–2812)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: WorkspaceBlobStore | None=None, sandboxes:
```

**Purpose**: Constructs the ExtensionContext object handed to an extension handler or core job. It wires only the capabilities that the caller supplied and enforces that metered model access has an attribution label.

**Data flow**: It receives extension identity, declared credential slots, optional services such as index, blob store, sandboxes, invoker, model resolver, surfaces, tailer, probe access, member-context settings, public URL data, and audience. It validates model wiring, builds ScopedStore, CredentialAccess, optional corpus/files/model access, surface installation access, and returns the completed ExtensionContext.

**Call relations**: This is the main factory for the file: callers use it at setup time so extension code receives a safe context instead of raw database, blob, credential, or model handles.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/runtime/ext/manifest.py`

`data_model` · `startup and cross-cutting runtime declaration`

Think of this file as the menu format for add-ons. An extension does not directly wire itself into the running app. Instead, it returns a Manifest, a frozen bundle of promises such as “I provide these tools,” “I need this credential,” “I listen for this event,” or “I can run this background job.” Core startup code reads those declarations and plugs them into the right places.

Most of the file is made of small frozen data classes. “Frozen” means they are meant to be created once and not changed later, like a signed order form. Some classes describe runtime features, such as HTTP routes, recurring jobs, connector providers, source backends, feature-flag providers, browser providers, and terminal transports. Others describe policy points, such as hooks that can block or adjust a tool call, and credential slots that say which secrets an extension needs.

The file also protects important boundaries. It checks that shipped agent names and icons are valid, that tool allowlists name real semantic actions rather than the low-level dispatcher tool, that only user-message hooks can be “best effort,” and that only one open connector namespace exists. Without these declarations and checks, extensions could collide silently, credentials could be exposed in the wrong way, or missing backends would only fail much later during a user request.

#### Function details

##### `JobFault.__init__`  (lines 118–120)

```
def __init__(self, reason: str) -> None
```

**Purpose**: Creates a named failure for a background job. A job uses this when it can explain the failure in safe, human-written words instead of leaking raw provider errors or stack details.

**Data flow**: It receives a reason string. It stores that reason both as the normal exception message and as a separate reason field. The result is an exception object that job reporting code can recognize and copy into the job failure record.

**Call relations**: A job handler raises this when work fails for a known reason. Later job-running code can treat it differently from an unexpected crash, because the reason is intentionally authored by the handler.


##### `PreToolUse.__post_init__`  (lines 431–433)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the semantic tool-call name for a pre-tool hook event when the caller did not provide one. This keeps hook matching simple: every event has a call value.

**Data flow**: It starts with a newly created PreToolUse object containing a tool name, input model, and possibly an empty call field. If call is empty, it copies tool_name into call. The object then always has a usable call identity.

**Call relations**: This runs automatically after a PreToolUse event object is created. Hook matching later relies on the call field to decide which hook should see or filter the tool call.


##### `PostToolUse.__post_init__`  (lines 449–451)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the semantic tool-call name for a successful post-tool hook event when it was not explicitly set. This lets later code treat ordinary tools and object actions through one field.

**Data flow**: It receives the just-created PostToolUse object. If the call field is blank, it sets call to the tool_name. The output is the same event object, now carrying a consistent call identity.

**Call relations**: This runs automatically after a PostToolUse event is constructed. Hook dispatch code can then compare hook tool filters against call without needing special fallback logic.


##### `PostToolUseFailure.__post_init__`  (lines 469–471)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the semantic tool-call name for a failed tool-call hook event when the caller did not provide one. This makes failed-call notifications match hooks the same way successful-call notifications do.

**Data flow**: It starts with a PostToolUseFailure object that includes the tool name, input, and error output. If call is empty, it sets call to tool_name. The event leaves construction with a dependable call value.

**Call relations**: This runs automatically after a failed tool-use event is created. Hook filtering later uses the call value to decide which failure observers should run.


##### `HookSpec.__post_init__`  (lines 616–618)

```
def __post_init__(self) -> None
```

**Purpose**: Rejects an invalid hook declaration where an extension tries to make a non-user-message hook “best effort.” Best effort means a hook failure is allowed to be ignored, and this file only permits that for user prompt submission hooks.

**Data flow**: It reads the HookSpec fields after creation. If best_effort is true and the event is not user_prompt_submit, it raises a ValueError. Otherwise the hook declaration is accepted unchanged.

**Call relations**: This runs automatically when an extension creates a HookSpec. It protects the later hook runner from an unsafe configuration where a tool gate might fail open.


##### `AgentProvision.__post_init__`  (lines 650–676)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a shipped workspace agent is safe and complete before it can be declared by an extension. It makes sure the agent has a valid name, valid icon, prompt, purpose, and a tool allowlist that can actually complete its setup.

**Data flow**: It reads the new AgentProvision fields: name, tool list, icon, agent spec, setup needs, and whether tools are restricted. It raises clear ValueErrors for invalid names, forbidden dispatcher-tool allowlists, bad icons, missing prompts, missing purpose text, or setup plans that lack the tools needed to perform setup. If all checks pass, nothing is changed.

**Call relations**: This runs automatically when an extension declares an AgentProvision. The extension loader can then trust that any provisioned agent has the minimum information a user and the system need.


##### `SubagentProfile.__post_init__`  (lines 727–749)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a subagent profile’s tool list and output contract are coherent. A subagent is a smaller agent spawned for a task, so its permissions and final-answer shape must be precise.

**Data flow**: It reads the profile name, allowed tool names, output model, and concise handoff flag. It rejects tool lists that include the low-level object-action dispatcher instead of canonical action names. It also compares the output model to the required concise-result contract and raises an error if the flag and schema disagree. If valid, the profile is left unchanged.

**Call relations**: This runs automatically when an extension creates a SubagentProfile. Later, the subagent registry and spawn logic can depend on the profile having a safe allowlist and a truthful output contract.


##### `SubagentToolGrant.__post_init__`  (lines 768–773)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a cross-extension grant gives subagents real tool or action names, not the low-level dispatcher tool. This keeps permission widening explicit and meaningful.

**Data flow**: It reads the grant’s target profile and tool_names. If the forbidden object-action dispatcher appears in the list, it raises a ValueError. Otherwise the grant is accepted as written.

**Call relations**: This runs automatically when an extension declares a SubagentToolGrant. Later loading code can union these grants into matching subagent profiles without having to re-check this particular mistake.


##### `conversation_slot_declarations`  (lines 871–900)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: Collects all conversation-slot providers from active manifests and validates that they form one clean global namespace. Conversation slots are typed pieces of conversation-side context that extensions can provide.

**Data flow**: It receives a tuple of manifests. It walks through each manifest’s conversation slot providers, checking each provider’s id, label, icon, callbacks, payload type, and uniqueness. It returns a tuple of pairs, each pairing the owning manifest with its provider. If anything is malformed or two extensions use the same id, it raises RuntimeError.

**Call relations**: Startup or extension-loading code calls this after manifests are known. The returned declarations can then be used by the portal or conversation runtime knowing that slot ids do not collide and that each provider has the callbacks needed to read and summarize its content.


##### `open_connector_namespace`  (lines 903–915)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: Finds the single catch-all connector namespace declared by installed extensions, if one exists. This matters because a catch-all connector resolver answers for connector slugs that were not registered one by one, so there can only be one clear owner.

**Data flow**: It receives all active manifests. It scans for manifests with connector_resolver set. If none are found, it returns None. If exactly one is found, it returns that resolver. If more than one is found, it raises RuntimeError because the system would not know which resolver owns an unknown connector slug.

**Call relations**: Connector setup code uses this during startup when building the connect flow and connector registry. The function prevents later routing ambiguity before any user tries to connect an account.


##### `declared_slots`  (lines 938–951)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: Turns extension credential declarations into the shared credential-slot records that the rest of the system can display and use. These are the “bring your own key” or secret slots an extension says it needs.

**Data flow**: It receives the active manifests. For every credential slot in every manifest, it creates a DeclaredSlot containing the slot name, description, owning extension, optional injected host, and optional merge function. It returns all of those DeclaredSlot objects as a tuple.

**Call relations**: Credential-related parts of the system call this when they need one common view of declared secrets, such as the credential object kind or the portal credentials panel. Inside the function, each output item is built by calling DeclaredSlot.__init__ with data copied from the manifest credential slot.

*Call graph*: 1 external calls (__init__).


### Authority and runtime access
Public SDK shims expose execution authority, callback-page flows, runtime context objects, and feature-flag helpers from stable import paths.

### `core/src/ufo/sdk/authority.py`

`data_model` · `cross-cutting import-time API surface`

This file does not define new behavior of its own. Instead, it re-exports a small set of authority-related names from `ufo.runtime.authority` so extension-owned code can depend on `ufo.sdk.authority` as the public interface. In this project, an execution authority represents what kind of work a piece of code is allowed to do, such as work tied to a workspace or a member. That matters because extensions need a clear, safe way to say “run this under this authority” without reaching into internal runtime modules.

The file imports and republishes `ExecutionAuthority`, `WorkspaceAuthority`, `MemberAuthority`, the shared `WORKSPACE_AUTHORITY` value, an `AuthorityUnavailable` error, and helper functions for converting to and from member IDs. The repeated `as same_name` style makes the exported public names explicit, which is useful for tools that check public APIs.

Without this file, extension code would have to import directly from the runtime layer. That would make the boundary between the public SDK and internal implementation blurrier, and future refactors would be harder because outside users might depend on private paths.


### `core/src/ufo/sdk/callback_page.py`

`io_transport` · `request handling`

When someone connects a provider or installs through a browser, they may have started from Slack, a command-line tool, or another place that is not tied to a normal web session. This file creates the one simple page that can safely greet them at the end of that journey. It cannot rely on knowing who they are from cookies or an app session, so it only says what the just-finished return step knows: a headline, an optional detail line, and possibly the next action.

The page is intentionally tiny. It uses the browser’s built-in light or dark theme, fetches only the UFO logo, and avoids extra styling files. That matters because this page is often opened briefly on a phone or in a temporary browser tab.

The file also solves a small but important browser problem: closing a tab only works when the browser believes the page was opened by script. If the user opened the tab themselves from a chat link, the browser usually refuses to close it. So the page can either try to close, show a normal link, or use a forwarding script that closes UFO’s own popup window but redirects ordinary tabs back to the right conversation or screen. All text and URLs are escaped before being placed into HTML or JavaScript, so user-facing content cannot accidentally break the page or inject code.

#### Function details

##### `forward_script`  (lines 74–104)

```
def forward_script(url: str) -> str
```

**Purpose**: This function creates the small JavaScript snippet that decides what should happen after the callback page appears. It closes a consent popup opened by UFO itself, but redirects a normal browser tab to a given URL.

**Data flow**: It receives a destination URL. It turns that URL into a safe JavaScript string, also escaping the less-than character so the URL cannot accidentally end the script tag. The returned text is a script that waits briefly, checks browser session storage for UFO’s consent-window marker, and then either closes the window or replaces the current page with the destination URL.

**Call relations**: This helper is used by callback_page when the caller asks for automatic forwarding. It relies on json.dumps to safely quote the URL and the storage marker before handing the finished script back to be inserted into the HTML page.

*Call graph*: called by 1 (callback_page); 1 external calls (dumps).


##### `callback_page`  (lines 107–130)

```
def callback_page(*, headline: str, detail: str='', link: PageLink | None=None, status: int=200, close: bool=False, forward: str='') -> HTMLResponse
```

**Purpose**: This function builds the complete HTML response shown to the user at the end of a connect, consent, or install flow. It combines the message, optional detail text, optional return link, and optional close-or-forward behavior into one small page.

**Data flow**: It receives the headline, optional detail text, optional PageLink, HTTP status code, and instructions about whether to close or forward the page. It escapes visible text and link data so they are safe inside HTML. It fills the page template with those pieces, chooses the right close or forward script if needed, and returns an HTMLResponse with the chosen status code.

**Call relations**: Route or callback code calls this when it needs to answer the browser after a return leg. If a forward URL is provided, it asks forward_script to make the browser behavior script. It uses html.escape to make displayed content safe and wraps the finished page in HTMLResponse so the HTTP layer can send it to the browser.

*Call graph*: calls 1 internal fn (forward_script); 2 external calls (escape, HTMLResponse).


### `core/src/ufo/sdk/context.py`

`util` · `cross-cutting import-time SDK access`

This file does not create new behavior of its own. Instead, it works like a front desk for the SDK: extension code can import context-related tools from `ufo.sdk.context` without needing to know where each item lives inside the project.

That matters because many extensions need the same kinds of information: who the current agent is, what has happened in the conversation, what pages or sources are available, what credentials may be used, and where temporary or scoped data can be stored. Without this file, extension authors would have to import from internal runtime and schema paths directly. That would make extension code more fragile, because internal paths can change as the project grows.

The file re-exports several context and record types, such as `ExtensionContext`, `ConversationFacts`, `ScopedStore`, `CredentialAccess`, `ModelAccess`, and `Trajectory`. A “re-export” means it imports a name from somewhere else and exposes it again under this module. The `as SameName` style makes the public name explicit.

In short, this is a stable public doorway into the context system. It keeps extension-facing code cleaner and helps separate the project’s public SDK from its internal layout.


### `core/src/ufo/sdk/flags.py`

`util` · `cross-cutting`

This file is a small public doorway into the project’s feature-flag system. A feature flag is a switch the software can check to decide whether a capability should be treated as turned on or off. Rather than making outside code import directly from the deeper internal module `ufo.flags`, this file re-exports the pieces that are meant to be part of the SDK surface.

It exposes two constants, `SERVED_TRUE` and `SERVED_FALSE`, which represent the two backend spellings for whether a flag is served as enabled or disabled. It also exposes `flag_enabled`, the helper used to read a flag value and decide if it should count as enabled.

The important value here is stability and clarity. Think of it like a reception desk: visitors do not need to know which office actually stores the forms; they just go to the desk that the building promises will be there. If the internal `ufo.flags` module is reorganized later, this SDK file can keep the public import path unchanged. Without this file, SDK users would either depend on internal paths or duplicate flag interpretation rules, making their code more fragile.


### Extension authoring surfaces
SDK entry points provide supported imports for HTTP routes, background jobs, manifests, scheduled runs, and tools.

### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file exists so extensions can build web routes without reaching into the underlying web framework directly. In practice, UFO uses Starlette for HTTP, but this file acts like a front desk: extension code imports Request, Response, HTMLResponse, StreamingResponse, UploadFile, and form parsing errors from here instead of depending on Starlette’s layout. That keeps the public surface small and stable.

The file also protects one especially sensitive thing: session cookies. A session cookie is the small browser-stored token that lets the server recognize a returning user. If it is set too broadly, such as for a whole parent domain, it could leak between subdomains or preview environments. The helper set_session_cookie deliberately does not allow a cookie domain, so cookies remain “host-only,” meaning they belong only to the exact site that set them.

Two smaller helpers support that decision. cookie_secure decides whether a cookie should use the Secure flag, which tells browsers to send it only over HTTPS. It bases this on the published public scheme, not the internal request, because deployments often receive plain HTTP internally after HTTPS was already handled by a front proxy. plain_local recognizes the one normal exception: development sites served as plain HTTP on localhost.

#### Function details

##### `cookie_secure`  (lines 24–33)

```
def cookie_secure(published_scheme: str) -> bool
```

**Purpose**: This function decides whether a session cookie should be marked Secure. Secure means the browser should only send the cookie over HTTPS, which protects it from being exposed on plain HTTP connections.

**Data flow**: It receives the scheme the site publicly advertises, such as "https" or "http". It checks only that value, not the incoming request, and returns true for anything except plain "http". The result is then used as the cookie’s Secure setting.

**Call relations**: This helper sits before cookie creation. Route or session code can ask it what Secure value to use, then pass that answer into set_session_cookie so the actual cookie is written consistently.


##### `plain_local`  (lines 36–43)

```
def plain_local(published_base: str | None) -> bool
```

**Purpose**: This function tells whether a published site address is plain HTTP but only for a local development host. It treats localhost and subdomains ending in .localhost as safe local-only exceptions.

**Data flow**: It receives a published base URL, or nothing. It breaks the URL into parts with urllib.parse.urlsplit, then checks whether the scheme is "http" and whether the hostname is localhost or a localhost subdomain. It returns true only when both conditions are met.

**Call relations**: This helper is used when code needs to distinguish a real public plain-HTTP deployment from a local developer setup. Its main handoff is to Python’s urlsplit function, which performs the URL parsing so this function can make a simple yes-or-no decision.

*Call graph*: 1 external calls (urlsplit).


##### `set_session_cookie`  (lines 46–65)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none'], secure: bool) -> None
```

**Purpose**: This is the approved way for UFO code to set its own session cookies. It always makes the cookie HttpOnly, meaning browser scripts cannot read it, and it never allows a Domain setting, so the cookie cannot spread to sibling or parent sites.

**Data flow**: It receives a response object, a cookie name, a session token, a SameSite policy, and a Secure flag. It passes those values to the response’s set_cookie method, while forcing httponly to true and omitting any domain. The response is changed in place by adding a Set-Cookie header; the function does not return a separate value.

**Call relations**: This function is the final step when route or session code wants the browser to store a session token. It delegates the low-level header writing to Starlette’s Response.set_cookie, but keeps UFO’s safety rules in front of that call.

*Call graph*: 1 external calls (set_cookie).


### `core/src/ufo/sdk/jobs.py`

`other` · `cross-cutting; used when extensions are imported or declare background jobs`

This file is a small but important boundary marker. It does not implement job behavior itself. Instead, it says: “If an extension needs to declare background work, use these names from here.” That matters because the real job machinery lives inside runtime modules, which are internal parts of the system and may change. By re-exporting selected pieces through `ufo.sdk.jobs`, the project gives extension authors a stable public surface.

The exported pieces cover the main things an extension needs for jobs. `JobSpec` describes a job. `owner_candidates` and `WorkspaceCandidates` help a job say which workspaces may have work ready. This is built fresh on each scheduler tick, so time-based checks can use the current time. Several helper functions name common groups of workspaces, such as workspaces with live agents or unseeded agents. `JobFault` gives job code a clear way to report a failure reason in words chosen by the handler, instead of leaving operators with only a stack trace. `PAGE_CHANGE_CURSOR_KEY` exposes the official cursor key prefix used when a page-change consumer needs to reset where it left off.

An everyday analogy: this file is like a service counter. The tools are stored in the back room, but users are told to request them at the counter so the layout behind the counter can change without breaking everyone.


### `core/src/ufo/sdk/manifest.py`

`other` · `cross-cutting import surface`

This file is a compatibility and clarity layer. It does not define new behavior of its own. Instead, it gathers many manifest-related names from deeper internal modules and re-publishes them from one stable place.

The problem it solves is boundary control. Extensions need to describe things like agents, hooks, credentials, conversation slots, setup schedules, image previews, and workspace changes. Those definitions live inside runtime modules, but extension authors should not have to know that internal layout. If they imported directly from the internals, a future reorganization could break their code. This file acts like a front desk: outsiders ask here, and the front desk points them to the right internal object without exposing the building’s floor plan.

The repeated `as Name` imports are intentional. They make it explicit that each imported name is part of this module’s public surface. Because the package avoids putting code in `__init__.py` files, named modules like this one become the official import locations.

Without this file, extension code would either become more fragile by depending on internal paths, or each extension author would need to discover many separate modules by hand.


### `core/src/ufo/sdk/scheduled_fire.py`

`other` · `import time / SDK use`

This file is a thin public wrapper. Its job is not to create new behavior, but to make two scheduled-fire helpers available from the SDK layer, where outside users are expected to look. A “scheduled fire” is a run that is started by a schedule, such as a cron-style timer. Each such run needs a key so the system can admit it correctly, and later needs a way to trace that run back to the task it belongs to. The real implementation lives in `ufo.runtime.ext.scheduled_fire`; this file simply re-exports `scheduled_fire_key` and `scheduled_fire_task_id` under the same names. Think of it like a front desk sign that points visitors to the right service without exposing the building’s internal corridors. Without this file, users might have to import from internal runtime paths, which would make their code more fragile if the project reorganizes its internals later.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting public SDK surface`

This file does not define new behavior of its own. Instead, it gathers selected names from deeper parts of the system and re-exports them as the supported public interface for writing tools and tool handlers. Think of it like a front desk: extension authors should ask here for the things they need, rather than wandering through private back rooms of the codebase.

That matters because the runtime internals can change over time. If every extension imported internal modules directly, even small reorganizations could break outside code. By providing stable names here, the project can keep a cleaner boundary between “what extension authors may rely on” and “how the runtime happens to be built today.”

The exported items cover the main pieces a tool needs: `ToolContext` for the environment a tool runs in, `ToolResult` and content types for returning answers, `ToolFailure` for reporting problems, connector and permission-related types, file-change constants, registry definitions for declaring tools, and task helpers such as `run_task`. The comment highlights `run_task` as especially important: it supports detached command execution, so a long-running command can keep going after the original caller’s time budget is exceeded and can still be tracked through shared task handles.


### Diagnostics and safety helpers
Public helpers let extensions report approved observability data and consistently mark risky outside content as untrusted.

### `core/src/ufo/sdk/o11y.py`

`io_transport` · `cross-cutting`

This file is a small public-facing wrapper around the project’s observability tools. Observability means the signals a system gives off so people can understand what it is doing, such as logs, warnings, metrics, and timing profiles.

Extensions need a way to say things like “this event happened,” “this warning should be shown,” or “this counter should increase.” But the project does not want every extension to create its own random metric names, because that would make the system hard to monitor and compare. This file solves that by exposing only the observability functions that are already defined in the core harness layer.

In practice, it imports and re-exports four tools: `log`, `warn`, `emit_metric`, and `turn_profile`. Extension authors can import them from this SDK path instead of reaching into the internal harness package. That is like giving guests a front desk phone number instead of letting them wander through the building looking for staff.

There is no local behavior here. Its value is in setting a boundary: extensions get an approved interface, while the core project keeps control over the official logging and metrics surface.


### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

This file is a small but important bridge. Some text that enters the system comes from places the program should not fully trust, such as a tool's output, a provider's response, or another component handing back results. That text may be useful, but it should be fenced off so it is not mistaken for the system's own instructions or trusted content.

Rather than inventing a second SDK-specific version of that fence, this file imports `wall` from `ufo.harness.untrusted` and exposes it again from the SDK path. In plain terms, it is like putting the same warning label dispenser at two doors: the core system door and the extension developer door. Both produce the same kind of label, so renderers and safety-sensitive paths can recognize it reliably.

Without this file, extension authors might need to know the internal harness location, or different parts of the project might start using slightly different ways to mark untrusted content. This file keeps the public-facing SDK simple while preserving one shared definition of what “untrusted wall content” means.
