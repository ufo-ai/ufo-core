# Core extension API contracts  `stage-19.3`

This stage is shared behind-the-scenes support for UFO’s extension system. It defines the rules and safe boundaries that let extra features plug into the core app without getting unlimited access.

The manifest file is the extension’s “menu.” It tells UFO what the extension offers, such as tools, credentials it needs, routes, hooks, agents, skills, or backend services. The core can read this menu and decide how to wire those pieces into the larger system.

The context file builds the safe toolbox an extension receives when it actually runs. Instead of handing over the whole system, UFO gives a carefully scoped runtime context: the current workspace, the extension’s own stored data, declared credentials, allowed files, model access, conversations, synced sources, and similar approved abilities.

The conversation slots file defines what extensions may show inside conversation panels. It gives clear data shapes for things like artifacts, sources, tasks, sites, and automations, plus provider objects that know how to summarize and read that slot content. Together, these contracts let extensions add useful features while staying predictable and contained.

## Files in this stage

### Extension API contracts
Defines the scoped runtime context extensions receive, the conversation slot data/provider shapes they can expose, and the manifest contract for declaring extension capabilities.

### `core/src/ufo/ext/context.py`

`orchestration` · `cross-cutting: active whenever an extension, background job, surface handler, or off-turn helper runs`

Extensions need to do useful work, but they must not get a master key to the whole system. This file is the boundary that turns powerful internal services, such as the database, blob storage, credentials, sandboxes, model clients, and conversation tools, into smaller safe handles. Think of it like giving a contractor a keycard that opens only the rooms listed on their work order, rather than handing them the building’s master key.

The central object is ExtensionContext. It is assembled by context_for and passed to extension handlers or core jobs. Most methods use the “ambient” workspace already bound to the current run, so callers do not pass workspace IDs around and cannot easily cross into another tenant’s data. Smaller helper classes provide specific capabilities: ScopedStore is private durable storage for one extension, CredentialAccess limits secret access to declared slots, TrajectoryCorpus reads conversation transcripts, ConversationFiles writes into a conversation sandbox, ConversationProbes runs short off-turn commands, and ModelAccess performs billed large-language-model calls.

The file also contains workspace candidate queries for background jobs, helpers for conversation titles and source syncing, and read models used by member-facing listings. Its most important behavior is defensive scoping: missing wiring fails loudly, undeclared credentials are rejected, cross-workspace IDs return nothing or raise, and raw database access is rare and clearly marked as the caller’s responsibility.

#### Function details

##### `ScopedStore.workspace_id`  (lines 118–119)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID currently bound to this run. This keeps the store tied to the active workspace instead of letting callers choose one.

**Data flow**: It reads the current workspace scope and returns its workspace_id. Nothing is changed.

**Call relations**: All ScopedStore reads and writes use this property before touching the extension store, so every key lookup is automatically fenced to the active workspace.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 121–132)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one JSON value from this extension’s private key-value store. It is used when an extension needs a saved setting, checkpoint, or conversation mapping.

**Data flow**: The caller gives a key. The function opens a workspace transaction, looks for a row matching the current workspace, this extension, and that key, then returns the stored value or None.

**Call relations**: Browser, Slack, and web extension code call this when resuming earlier work. It relies on workspace_tx and SQL selection rather than exposing a raw database handle.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_web_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 134–149)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several named keys from this extension’s store in one database query. It avoids scanning the whole store when only a short list is needed.

**Data flow**: The caller gives keys. Empty input returns an empty dict; otherwise the function fetches matching rows for the current workspace and extension and returns a key-to-value dictionary for keys that exist.

**Call relations**: It is the batched form of ScopedStore.get and uses the same workspace-scoped storage table.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 151–175)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Writes or replaces one value in this extension’s private store. It is safe for first-time creation and updates.

**Data flow**: The caller gives a key and JSON value. The function opens a transaction and performs an upsert, meaning insert if missing or update if present, then commits through the workspace transaction.

**Call relations**: Browser, Slack, and web extension flows call this to save durable state. The single upsert avoids races where two callers both try to create the same key.

*Call graph*: called by 4 (_start, _context, _hold_connect_message, _open_conversation); 1 external calls (workspace_tx).


##### `ScopedStore.put_if`  (lines 177–227)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the stored value still matches what the caller expected. This protects a newer update from being overwritten by stale work.

**Data flow**: The caller gives a key, new value, and expected old value. If expected is None, it inserts only if the key is absent. Otherwise it locks the row, compares the current value, updates only on a match, and returns true or false.

**Call relations**: Slack reply checkpoint code uses this compare-and-swap style update when progress may be reported by overlapping tasks.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 229–237)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes one key from this extension’s workspace-scoped store. It is used to clean up temporary or obsolete extension state.

**Data flow**: The caller gives a key. The function deletes only the row for the current workspace, this extension, and that key.

**Call relations**: Slack and web surface code call this during cleanup. It uses workspace_tx so deletion stays inside the active workspace.

*Call graph*: called by 2 (_drop_turn_reply_records, _open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 239–252)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists this extension’s stored keys, optionally only those beginning with a prefix. This lets an extension discover its own saved records without seeing anyone else’s.

**Data flow**: The caller may give a prefix. The function fetches matching key-value rows for the current workspace and extension, sorts them by key, and returns them as tuples.

**Call relations**: Slack cleanup and web audience helpers use it to find stored records by naming convention.

*Call graph*: called by 3 (_drop_turn_reply_records, _granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 268–269)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the current workspace ID for credential lookups. It ensures secrets are resolved for the workspace currently running the handler.

**Data flow**: It reads the current workspace scope and returns its workspace_id. It changes nothing.

**Call relations**: CredentialAccess methods use the same ambient workspace boundary as ScopedStore.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 271–277)

```
async def get(self, slot: str) -> str
```

**Purpose**: Returns the live secret for a declared credential slot. It refuses access if the extension did not declare that slot in its manifest.

**Data flow**: The caller gives a slot name. The function checks the declared slot set, then asks the current workspace for that credential and returns the secret string.

**Call relations**: This is the basic credential gate. If a slot is undeclared, it raises UndeclaredCredentialSlot before any secret lookup happens.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 279–286)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Tells whether a declared credential comes from the workspace’s own stored secret rather than the platform default. This matters for billing and provider cost ownership.

**Data flow**: The caller gives a slot. After the declaration check, it asks the current workspace whether that slot is stored locally and returns a boolean.

**Call relations**: It follows the same slot gate as get, preventing undeclared credential probing.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.resolve`  (lines 288–301)

```
async def resolve(self, slot: str) -> str
```

**Purpose**: Resolves a declared credential slot through its configured source first, then falls back to the normal workspace credential. This supports credentials derived from provider installations or other declared sources.

**Data flow**: The caller gives a slot. The function checks declaration, finds any manifest source, tries to obtain a source-specific secret from the credential store, and otherwise returns the workspace credential.

**Call relations**: It calls slot_secret when a source is present. If a source was configured but no store was wired, it raises because safe resolution is impossible.

*Call graph*: 3 external calls (__init__, slot_secret, ws_current).


##### `CredentialAccess.rotate`  (lines 303–308)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Updates an existing declared credential only if the caller’s expected old value still matches. It is for provider key rotation, not initial setup.

**Data flow**: The caller gives a slot, expected value, and new plaintext secret. After declaration checking, it delegates to the current workspace’s rotate operation and returns whether the swap happened.

**Call relations**: It uses the same undeclared-slot protection as other CredentialAccess methods.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 310–323)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores proof that a workspace is bound to a provider installation for a declared credential slot. It saves a sealed value instead of a bare installation ID.

**Data flow**: The caller gives a slot and installation ID. The function checks the slot, seals workspace, slot, and installation together, then writes that sealed credential into the current workspace.

**Call relations**: It calls installed_credential_requests and seal_installation so later credential minting can trust that the installation really belongs to this workspace.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 356–357)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the current workspace ID used for transcript reads. It keeps the corpus limited to the active workspace.

**Data flow**: It reads the workspace bound to the run and returns its ID.

**Call relations**: TrajectoryCorpus.trajectories and conversations use this property before querying conversation rows.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 359–366)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for the current workspace. This is useful for evaluation or learning jobs that need examples of past agent behavior.

**Data flow**: It builds a query for the most recent conversations in the workspace and passes that selection to _read. The result is a tuple of decoded Trajectory objects.

**Call relations**: It is a public corpus method and delegates the actual database-and-blob work to TrajectoryCorpus._read.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus.conversations`  (lines 368–380)

```
async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]
```

**Purpose**: Reads transcripts for exactly the conversation IDs the caller names, while still enforcing the workspace boundary. It can reach older conversations that are outside the recent-corpus limit.

**Data flow**: The caller provides conversation IDs. The function builds a workspace-scoped selection for those IDs and returns the decoded trajectories from _read.

**Call relations**: Like trajectories, it hands the actual read to _read, but with a caller-specified set instead of the recent list.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus._read`  (lines 382–426)

```
async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]
```

**Purpose**: Loads conversation metadata from the database and transcript bodies from blob storage, then turns them into Trajectory records. Missing or corrupt transcripts are skipped rather than crashing the whole corpus read.

**Data flow**: It receives a SQL selection of conversation IDs. It fetches conversation, turn, and agent prompt rows, reads each transcript blob, decodes messages, computes the prompt digest, and returns successful Trajectory objects.

**Call relations**: This is the shared worker behind trajectories and conversations. It calls transcript_key, decode, prompt_digest, and logs corrupt transcripts.

*Call graph*: called by 2 (conversations, trajectories); 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `ConversationFiles.write`  (lines 445–448)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s sandbox workspace so the agent can see the file later. It is for off-turn work that prepares files for a conversation.

**Data flow**: The caller provides a conversation ID, relative path, and content bytes. The request is passed to the sandbox service, which writes the file and returns the visible /workspace path.

**Call relations**: It is a narrow wrapper over ConversationSandbox.write, exposing file writing without exposing the whole sandbox object.


##### `ConversationFiles.prune`  (lines 450–456)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a prefix in a conversation workspace, keeping only a fixed number. This prevents unattended writers from filling the sandbox forever.

**Data flow**: The caller gives a conversation ID, path prefix, and keep count. The sandbox service removes older matching files and keeps the newest ones by name order.

**Call relations**: It delegates to ConversationSandbox.prune and pairs with write for bounded off-turn file output.


##### `ConversationFiles.write_runtime`  (lines 458–462)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output into a named runtime category for one conversation. This separates system-generated files from ordinary agent-visible files.

**Data flow**: The caller supplies conversation ID, category, path, and bytes. The sandbox writes the content into that runtime area and returns the resulting path.

**Call relations**: It delegates directly to ConversationSandbox.write_runtime.


##### `ConversationFiles.prune_runtime`  (lines 464–472)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Prunes older internal runtime files for a conversation category. It keeps runtime directories from growing without bound.

**Data flow**: The caller provides conversation ID, category, prefix, and keep count. The sandbox deletes older matching files and leaves the newest set.

**Call relations**: It delegates to ConversationSandbox.prune_runtime and mirrors prune for runtime-only files.


##### `conversation_agent_id`  (lines 475–488)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation belongs to inside a workspace. It returns None if the conversation ID is not part of that workspace.

**Data flow**: The caller gives workspace ID and conversation ID. The function queries the conversation table and returns the agent_id or None.

**Call relations**: ConversationProbes.run uses it before opening a sandbox, and ExtensionContext.conversation_agent exposes it to handlers.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 527–577)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, acting_member_id: UUID | None=None) -> ExecResult
```

**Purpose**: Runs one short shell command inside a conversation’s sandbox outside the normal turn flow. It is meant for bounded probes, not long-running background processes.

**Data flow**: The caller gives a conversation, command, timeout, and optional acting member. The function validates the timeout, confirms the conversation belongs to the current workspace, creates a signed probe token, opens the sandbox with a prepared environment, runs bash, and returns stdout, stderr, and exit code.

**Call relations**: It calls conversation_agent_id, binds the conversation’s agent scope, uses ProbeToken encoding, and then hands the command to the sandbox session.

*Call graph*: calls 1 internal fn (conversation_agent_id); 5 external calls (__init__, now, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 580–602)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds the workspace candidate set for jobs that need conversation transcripts. Only workspaces with at least one turned conversation are candidates.

**Data flow**: It defines a database query factory and wraps it in owner_candidates, producing a WorkspaceCandidates object for the job dispatcher.

**Call relations**: The nested with_a_turn function supplies the actual SQL condition used by owner_candidates.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 588–600)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds workspaces containing a conversation with at least one turn. It avoids dispatching transcript jobs where no transcript work exists.

**Data flow**: It produces a select statement over workspace IDs with nested existence checks for conversations and turns.

**Call relations**: It is created inside trajectory_workspaces and consumed by owner_candidates.

*Call graph*: 2 external calls (exists, select).


##### `seated_member_workspaces`  (lines 605–615)

```
def seated_member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds candidates for jobs that should run only in workspaces with at least one seated member. A seated member is someone who has an active seat in the workspace.

**Data flow**: It wraps a query factory in owner_candidates and returns the resulting workspace candidate source.

**Call relations**: The nested with_a_seated_member function supplies the database query.

*Call graph*: 1 external calls (owner_candidates).


##### `seated_member_workspaces.with_a_seated_member`  (lines 608–613)

```
def with_a_seated_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds distinct workspaces with at least one seated member.

**Data flow**: It selects member workspace IDs where seated_at is not null and removes duplicates.

**Call relations**: It is used by seated_member_workspaces as the candidate query body.

*Call graph*: 1 external calls (select).


##### `connection_workspaces`  (lines 618–631)

```
def connection_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds candidates for connection-driven jobs. A workspace is included when its main agent has at least one connector grant.

**Data flow**: It creates a query factory and passes it to owner_candidates, yielding a WorkspaceCandidates object.

**Call relations**: The nested with_a_main_agent_connection function supplies the query.

*Call graph*: 1 external calls (owner_candidates).


##### `connection_workspaces.with_a_main_agent_connection`  (lines 623–629)

```
def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds workspaces where the main agent has a connected account grant.

**Data flow**: It selects workspace IDs from connector grants joined to agents, filters to main agents, and returns distinct IDs.

**Call relations**: It is the database part of connection_workspaces.

*Call graph*: 1 external calls (select).


##### `agent_is_live`  (lines 634–647)

```
def agent_is_live(workspace_id: sa.ColumnElement[UUID], agent_id: sa.ColumnElement[UUID]) -> sa.ColumnElement[bool]
```

**Purpose**: Creates a SQL condition that says an agent exists in a workspace and is not archived. It lets sweep jobs avoid doing expensive work for agents that would later be refused.

**Data flow**: The caller supplies SQL columns for workspace ID and agent ID. The function returns an EXISTS condition checking matching, unarchived agent rows.

**Call relations**: It is a reusable query predicate for job sweeps that need to test agent liveness before invoking work.

*Call graph*: 2 external calls (exists, select).


##### `awaiting_a_title`  (lines 653–669)

```
def awaiting_a_title() -> sa.ColumnElement[bool]
```

**Purpose**: Creates a SQL condition for conversations that still need an automatic title. A conversation qualifies only after a member spoke and an agent answered.

**Data flow**: It returns a combined SQL condition: title_summarized is false and there is a done member-admitted turn in the conversation.

**Call relations**: ExtensionContext.conversations_awaiting_title and untitled_conversation_workspaces.with_an_unsummarized_title both use this shared predicate.

*Call graph*: called by 2 (conversations_awaiting_title, with_an_unsummarized_title); 3 external calls (and_, exists, select).


##### `untitled_conversation_workspaces`  (lines 672–681)

```
def untitled_conversation_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds candidates for the conversation-title summarizing job. It includes only workspaces with conversations that still need titles.

**Data flow**: It wraps a query factory in owner_candidates and returns it for the dispatcher.

**Call relations**: The nested with_an_unsummarized_title query uses awaiting_a_title.

*Call graph*: 1 external calls (owner_candidates).


##### `untitled_conversation_workspaces.with_an_unsummarized_title`  (lines 678–679)

```
def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds workspaces with at least one conversation awaiting a title.

**Data flow**: It selects distinct conversation workspace IDs where awaiting_a_title is true.

**Call relations**: It is the query body used by untitled_conversation_workspaces.

*Call graph*: calls 1 internal fn (awaiting_a_title); 1 external calls (select).


##### `unseeded_agent_workspaces`  (lines 684–713)

```
def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Builds candidates for once-per-agent sweep jobs. A workspace is included when it has more agents than the extension has recorded as settled.

**Data flow**: The caller gives an extension name and key prefix. The function returns workspace candidates based on a query comparing agent count to matching store-key count.

**Call relations**: The nested with_an_unsettled_agent query is wrapped by owner_candidates and used by sweep-style jobs.

*Call graph*: 1 external calls (owner_candidates).


##### `unseeded_agent_workspaces.with_an_unsettled_agent`  (lines 696–711)

```
def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the SQL query that finds workspaces with at least one agent not yet marked by an extension store key.

**Data flow**: It counts all agents in each workspace and counts this extension’s stored keys with the prefix, then selects workspaces where agents outnumber settled keys.

**Call relations**: It is the query body created by unseeded_agent_workspaces.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 730–742)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Defines the interface for starting an internal agent turn from background code. It is a protocol method, so this file says what shape an invoker must have without implementing it.

**Data flow**: An implementation receives conversation, agent, message, idempotency key, and admission options, then returns the admitted turn ID or None if admission is refused.

**Call relations**: ExtensionContext.invoke calls this method when an invoker has been wired into the context.


##### `ModelResolver.auto_model`  (lines 752–752)

```
def auto_model(self) -> str
```

**Purpose**: Defines the protocol property for the deployment’s default model name. ModelAccess uses it so background model calls all target the configured default.

**Data flow**: An implementation returns a model identifier string.

**Call relations**: ModelAccess.model and ModelAccess.turn read this property through the ModelResolver protocol.


##### `ModelResolver.pricing`  (lines 755–755)

```
def pricing(self) -> Pricing
```

**Purpose**: Defines the protocol property for model pricing information. ModelAccess needs this to turn token usage into billable usage.

**Data flow**: An implementation returns a Pricing object.

**Call relations**: ModelAccess.turn passes this pricing table into billing when usage events arrive.


##### `ModelResolver.client_for`  (lines 757–757)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Defines how to obtain a model client for a model name. The client is what actually streams completions from the provider.

**Data flow**: An implementation receives a model ID and returns a ModelClient, possibly using workspace credentials.

**Call relations**: ModelAccess.turn calls this before starting a background model round.


##### `ModelResolver.key_slot_for`  (lines 759–759)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Defines how to find which credential slot, if any, supplies a model’s provider key. This is needed to tell whether the workspace used its own key.

**Data flow**: An implementation receives a model name and returns a credential slot name or None.

**Call relations**: ModelAccess._serves_itself and ExtensionContext.pending_usage_exports use this resolver path.


##### `ModelResolver.provider_for`  (lines 761–761)

```
def provider_for(self, model: str) -> str
```

**Purpose**: Defines how to map a model name to its provider name. The provider label is used for metrics and reporting.

**Data flow**: An implementation receives a model ID and returns a provider string.

**Call relations**: ModelAccess.turn includes this provider in emitted model metrics.


##### `ModelAccess.model`  (lines 788–790)

```
def model(self) -> str
```

**Purpose**: Returns the default model that this background model access will call and bill. It hides resolver details from handlers.

**Data flow**: It reads auto_model from the resolver and returns that string.

**Call relations**: Handlers can inspect this property, while complete and turn enforce the same model on actual requests.


##### `ModelAccess.complete`  (lines 792–799)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one model completion and returns only the assistant’s text. It is the simple text-only wrapper around the fuller tool-aware turn method.

**Data flow**: The caller gives a ModelRequest. The function calls turn, then either returns string content directly or joins text blocks from structured content.

**Call relations**: Memory extension summarizers call this for text summaries. It delegates billing, streaming, and message assembly to ModelAccess.turn.

*Call graph*: calls 1 internal fn (turn); called by 3 (_summarize, _write, _summarize).


##### `ModelAccess._serves_itself`  (lines 801–808)

```
async def _serves_itself(self, model: str) -> bool
```

**Purpose**: Checks whether the current workspace is using its own provider key for a model call. That distinction affects how platform billing is recorded.

**Data flow**: The caller gives a model name. The function opens a transaction, asks accounting whether the workspace owns the relevant key slot, and returns a boolean.

**Call relations**: ModelAccess.turn calls this before metering usage so token accounting knows whether the provider cost is already paid by the workspace.

*Call graph*: called by 1 (turn); 3 external calls (workspace_owns_the_key, workspace_tx, ws_current).


##### `ModelAccess.turn`  (lines 810–913)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one streaming model round, records billing and metrics, and returns the assistant message, including tool calls when present. It is the main safe model-calling seam for background handlers.

**Data flow**: The caller gives a ModelRequest. The function fixes the model and session ID, streams events from the client, collects text, reasoning blocks, tool-call JSON, and usage, bills usage, emits latency and token metrics, and returns a Message.

**Call relations**: complete calls it for text-only use, and memory extension code calls it directly when it needs structured assistant messages. It calls _serves_itself, the model client, billing, JSON parsing, and metric emitters.

*Call graph*: calls 1 internal fn (_serves_itself); called by 3 (complete, _curate, _write); 10 external calls (__init__, __init__, __init__, __init__, model_copy, loads, monotonic, emit_histogram, emit_metric, ws_current).


##### `_source_readable`  (lines 977–1004)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that decides whether an agent/member reader may see a source. It combines workspace, removal, subject visibility, explicit grants, and main-agent owner rules.

**Data flow**: The caller gives a workspace ID and SourceReader. The function returns a SQL boolean expression; it does not run the query itself.

**Call relations**: ExtensionContext.readable_page_states, readable_source_ids, and source_pages use this predicate so their source and page reads share the same access rule.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `MemberContextRecord._aware_utc`  (lines 1057–1058)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures member-context timestamps always have timezone information. If a datetime is naive, it treats it as UTC.

**Data flow**: Pydantic passes in an information_date value. The validator returns it unchanged if timezone-aware, or returns a UTC-marked copy if not.

**Call relations**: It runs automatically when MemberContextRecord objects are created in member_context and _member_extension_records.

*Call graph*: 1 external calls (replace).


##### `_member_blob_text`  (lines 1064–1082)

```
async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str
```

**Purpose**: Reads a bounded amount of text from a blob for member context. It avoids loading huge files and handles a cut-off multibyte character cleanly.

**Data flow**: The caller gives a blob store and key. The function streams chunks until the byte limit is reached, closes the stream, decodes the bounded bytes, and returns text.

**Call relations**: ExtensionContext.member_context calls it for shared text artifacts and synced page bodies.

*Call graph*: calls 1 internal fn (get_stream); called by 1 (member_context).


##### `ExtensionContext.workspace_id`  (lines 1111–1112)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID for this context. It is a convenience wrapper around the scoped store’s workspace.

**Data flow**: It reads self.store.workspace_id and returns it.

**Call relations**: Many ExtensionContext methods use this property when calling lower-level helpers that need a workspace ID.


##### `ExtensionContext.image_preview_url`  (lines 1114–1128)

```
def image_preview_url(self, blob_key: str, size_bytes: int) -> str | None
```

**Purpose**: Creates a signed preview URL for an image blob when preview links are configured and the file is eligible. This lets extension listings show images without exposing signing secrets.

**Data flow**: The caller gives a blob key and size. The function passes the deploy secret, public base URL, blob details, and workspace ID to the URL minting helper and returns a URL or None.

**Call relations**: The sites extension uses this for preview links. The signing work stays in core through mint_image_preview_url.

*Call graph*: called by 1 (_preview_url); 1 external calls (mint_image_preview_url).


##### `ExtensionContext.artifact_link`  (lines 1130–1136)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a shared artifact. It gives extensions a safe way to link files produced by turns.

**Data flow**: The caller gives a SharedArtifact. The function combines it with the token secret, public base URL, and workspace ID and returns a link or None.

**Call relations**: Report digest objects call this when rendering rows with downloadable files.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_link).


##### `ExtensionContext.artifact_preview_link`  (lines 1138–1143)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed image-preview link for a shared artifact when the artifact can be previewed. It returns None for unsupported files.

**Data flow**: The caller provides a SharedArtifact. The function passes it with signing and workspace information to shared_artifact_preview_link.

**Call relations**: Report digest objects call this alongside artifact_link to show previews where possible.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_preview_link).


##### `ExtensionContext.scheduled_runs`  (lines 1145–1169)

```
async def scheduled_runs(self, member_id: UUID, *, agent_id: UUID | None, limit: int, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Reads recent scheduled turns visible to a member. It is used by member-facing object listings that show what automatic runs happened.

**Data flow**: The caller gives member ID, optional agent or turn filters, a limit, and optional subjects. The function delegates to the surface-layer scheduled_runs reader with the current workspace.

**Call relations**: Report digest object pages call this to build scheduled-run views.

*Call graph*: called by 2 (_one, _page); 1 external calls (scheduled_runs).


##### `ExtensionContext.home_url`  (lines 1171–1182)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a browser link to the deployment’s home surface. It returns None when no public base URL or home surface is configured.

**Data flow**: The caller may provide a URL fragment. The function trims the base URL, appends /surface/<home_surface>, appends the fragment, and returns the string.

**Call relations**: Several extensions use this after setup or billing actions to send members back to the portal.

*Call graph*: called by 3 (github_installed, _billing_portal, _hook).


##### `ExtensionContext.workspace_agents`  (lines 1184–1216)

```
async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]
```

**Purpose**: Reads the full agent roster for the workspace, including archived agents. It is restricted because it reveals broad workspace structure.

**Data flow**: If member-context reading is allowed, it queries agents in creation order and returns WorkspaceAgent records with names, owners, tools, provisioning source, and archived status.

**Call relations**: The web extension’s homepage seeding job calls this. It raises PermissionError when the context was not granted roster access.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.agent_visibilities`  (lines 1218–1230)

```
async def agent_visibilities(self) -> dict[UUID, AgentVisibility]
```

**Purpose**: Reads each agent’s portal visibility setting. This helps listings decide the minimum audience for agent-related objects.

**Data flow**: It queries agent IDs and visibility values for the current workspace and returns a dictionary keyed by agent ID.

**Call relations**: It uses workspace_tx directly and is not gated by member-context permission because it reads workspace shape rather than personal member content.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.agent_named`  (lines 1232–1249)

```
async def agent_named(self, name: str) -> AgentIdentity | None
```

**Purpose**: Finds a live agent by its stable name and returns its identity and owner. Archived agents do not match.

**Data flow**: The caller gives an agent name. The function queries the current workspace for an unarchived agent with that name and returns AgentIdentity or None.

**Call relations**: Instance actions can use this to turn an object name such as agent/<name> into the agent ID that authorization should check.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.earliest_seated_admin`  (lines 1251–1269)

```
async def earliest_seated_admin(self) -> UUID | None
```

**Purpose**: Finds the earliest seated admin in a workspace. This gives ownerless background work a deterministic member to act on behalf of.

**Data flow**: After permission checking, it queries seated admin members ordered by seat time and ID and returns the first member ID or None.

**Call relations**: The web extension’s homepage seeding uses this when it needs an acting member for admin-owned work.

*Call graph*: called by 1 (seed_homepages); 2 external calls (select, workspace_tx).


##### `ExtensionContext.scheduled_member_timezone`  (lines 1271–1286)

```
async def scheduled_member_timezone(self) -> str
```

**Purpose**: Returns the scheduled member’s timezone, defaulting to UTC if none is set. It is for scheduled jobs that need member-local time.

**Data flow**: It verifies member-context access and that a scheduled member is bound, reads the member row, and returns its timezone or UTC.

**Call relations**: It fails with PermissionError if the context was not built for scheduled member context or the member no longer exists.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.member_context`  (lines 1288–1444)

```
async def member_context(self, *, since: datetime, limit: int=200, exclude_conversation_id: UUID | None=None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Builds a bounded recent context bundle visible to the scheduled member. It gathers conversations, artifacts, synced pages, memories, and objectives that can inform scheduled work.

**Data flow**: The caller gives a since time, limit, and optional conversation to exclude. The function checks permissions, reads visible turns, shared artifacts, readable synced pages, adds extension-owned memory/objective records, sorts everything newest first, and returns up to the limit.

**Call relations**: It calls readable_audiences, _member_blob_text, and _member_extension_records. This is one of the broadest read methods, so it is explicitly permission-gated.

*Call graph*: calls 2 internal fn (_member_extension_records, _member_blob_text); 5 external calls (__init__, exists, select, workspace_tx, readable_audiences).


##### `ExtensionContext._member_extension_records`  (lines 1446–1697)

```
async def _member_extension_records(self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int, exclude_conversation_id: UUID | None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Adds extension-owned memory and objective records to scheduled member context. It reads extension tables by name without importing those extensions directly.

**Data flow**: It receives member, audience, time, limit, and exclusion information. It queries memory items and objectives, checks objective steps/events/checks to keep only open objectives, builds stable keys, and returns MemberContextRecord objects.

**Call relations**: Only member_context calls this helper. It complements core conversation, artifact, and page context with records from memory/objective extensions.

*Call graph*: called by 1 (member_context); 8 external calls (__init__, sha256, DateTime, column, or_, select, table, workspace_tx).


##### `ExtensionContext.retitle_conversation`  (lines 1699–1702)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Sets a conversation title inside the current workspace. It is a direct naming operation for jobs that already decided the title.

**Data flow**: The caller gives conversation ID and title. The function delegates to the surface helper with the current workspace ID.

**Call relations**: It is separate from summarized_conversation_title, which also records that automatic summarization has been attempted.

*Call graph*: 1 external calls (retitle_conversation).


##### `ExtensionContext.conversations_awaiting_title`  (lines 1704–1728)

```
async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]
```

**Purpose**: Finds recent conversations in this workspace that still need automatic title summaries. It limits work per job tick.

**Data flow**: The caller gives a limit. The function queries workspace conversations matching awaiting_a_title, orders newest first, and returns their IDs.

**Call relations**: The web extension’s title summarizer calls this, then later records completion through summarized_conversation_title.

*Call graph*: calls 1 internal fn (awaiting_a_title); called by 1 (summarize_chat_titles); 2 external calls (select, workspace_tx).


##### `ExtensionContext.summarized_conversation_title`  (lines 1730–1735)

```
async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores a summarized title and marks the conversation as having been summarized. This removes it from future title-summary work.

**Data flow**: The caller provides conversation ID and title. The function delegates to summarize_conversation_title with the current workspace.

**Call relations**: The web title summarizer calls this after producing or attempting a summary.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (summarize_conversation_title).


##### `ExtensionContext.pending_usage_exports`  (lines 1737–1756)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns settled billing usage records that this extension has not yet acknowledged as exported. It also mints new export intents before reading.

**Data flow**: The caller provides a time floor and limit. The function requires a model key-slot resolver, opens a transaction, mints usage exports for this workspace and extension, then reads pending exports.

**Call relations**: It calls mint_usage_exports and read_pending_usage_exports from accounting. ack_usage_exports is the matching completion step.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 1758–1767)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage export records as delivered so they are not returned again. It should be called only after the external receiver accepts them.

**Data flow**: The caller gives exported UsageExport records. Empty input does nothing; otherwise the function opens a transaction and acknowledges them for this workspace and extension.

**Call relations**: It is the counterpart to pending_usage_exports and delegates to accounting’s ack_usage_exports.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 1770–1782)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides an async database transaction for an extension’s own tables. It is powerful and intentionally documented as raw access that the extension must scope correctly.

**Data flow**: The caller enters the async context manager. The function yields the workspace transaction connection, committing on normal exit and rolling back on error through workspace_tx.

**Call relations**: Memory, metronome, report digest, and other extension object readers use it for their own SQL tables.

*Call graph*: called by 15 (_item, _page, _entry, _page, _billing_autopay, _billing_projection, _billing_status, _entries, _task_names, record_sources (+5 more)); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 1784–1821)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Starts an internal turn in a conversation through the wired turn invoker. It is how background handlers wake an agent to do work.

**Data flow**: The caller provides conversation, agent, message, idempotency key, and admission options. The function verifies an invoker is wired, forwards all arguments, and returns the new turn ID or None.

**Call relations**: Source triggers and web homepage seeding call this. If no invoker is present, it raises instead of silently dropping work.

*Call graph*: called by 2 (_fire_trigger, seed_homepages).


##### `ExtensionContext.tail`  (lines 1823–1832)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn until it ends. This lets side-channel work watch a turn it started or follows.

**Data flow**: The caller gives a turn ID and optional cursor. The function requires a wired tailer and returns its async context manager for frame iteration.

**Call relations**: It delegates to the injected TurnTailer. Missing wiring raises immediately.


##### `ExtensionContext.turn_is_terminal`  (lines 1834–1848)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final state. Missing turns are treated as terminal because there is nothing left to watch.

**Data flow**: The caller gives a turn ID. The function queries the current workspace’s turn row and returns true if absent or if status is one of the terminal statuses.

**Call relations**: It reads the database directly instead of relying on the live hub, which may have already finished delivering frames.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 1850–1854)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in this workspace, or None if the conversation is not here. It helps handlers resolve opaque conversation IDs safely.

**Data flow**: The caller gives a conversation ID. The function calls conversation_agent_id with the current workspace and returns its result.

**Call relations**: It is the ExtensionContext-facing wrapper around the shared conversation_agent_id helper.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 1856–1891)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Reads the audience and surface label for several conversations. Member-facing listings use this to decide visibility and origin.

**Data flow**: The caller gives conversation IDs. Empty input returns an empty dict; otherwise it queries matching conversations in the current workspace and returns ConversationFacts by ID.

**Call relations**: It parses stored audience strings with parse_audience and deliberately omits IDs outside the workspace.

*Call graph*: 4 external calls (__init__, select, workspace_tx, parse_audience).


##### `ExtensionContext.conversation_arrival_seq`  (lines 1893–1920)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Returns the latest member-arrival sequence number for a conversation. This is a watermark used to tell whether a member spoke after some work was armed.

**Data flow**: The caller gives a conversation ID. The function queries the maximum member-sourced inbound message sequence for that conversation and returns it, or 0 if none exists.

**Call relations**: Invoke callers can compare this value with unless_member_arrival_since to avoid waking work that a newer member message already superseded.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1922–1948)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads final status and terminal text for several turns. It is useful for rendering status lines about previous runs.

**Data flow**: The caller gives turn IDs. Empty input returns an empty dict; otherwise it fetches matching rows in this workspace and returns TurnOutcome records keyed by turn ID.

**Call relations**: The web extension’s homepage seeding uses this to understand earlier turn results.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 1950–1956)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace belongs to the platform operator. This gates operator-only rendering such as internal spend or debug links.

**Data flow**: It opens a transaction, reads the workspace domain, compares it to the operator email domain, and returns a boolean.

**Call relations**: It delegates the domain lookup to workspace_domain and treats unidentified workspaces as non-operator workspaces.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 1958–2022)

```
async def open_conversation(self, agent_id: UUID, key: str, member_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for a workflow key and agent. Reusing the same key keeps repeated events for one subject in one history.

**Data flow**: The caller gives agent ID, queue key, and optional member ID. The function verifies the agent belongs to this workspace, inserts a conversation if absent using extension and key as identity, sets shared or member audience, then returns the conversation ID.

**Call relations**: Source triggers and web homepage seeding call this before invoking turns. It uses uuid4 for new rows and conversation_audience for member-scoped rooms.

*Call graph*: called by 2 (_fire_trigger, seed_homepages); 4 external calls (select, workspace_tx, conversation_audience, uuid4).


##### `ExtensionContext.agent_name`  (lines 2024–2038)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the stable name of the currently bound agent. Agent-scoped object kinds use it to build links or labels.

**Data flow**: It reads the current agent scope, queries that agent in the matching workspace, and returns its current or archived name.

**Call relations**: It relies on agent_current being bound by the surrounding turn or portal read.

*Call graph*: 3 external calls (select, agent_current, workspace_tx).


##### `ExtensionContext.page_states`  (lines 2040–2069)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for named live synced pages in this workspace. It ignores tombstoned pages.

**Data flow**: The caller gives page IDs. Empty input returns an empty dict; otherwise it fetches page subject, revision, digest, blob reference, title, and stream, then returns PageState records by ID.

**Call relations**: This is an unrestricted workspace-scoped page-state read; readable_page_states adds reader access checks.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 2071–2109)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads page states only for pages the given reader is allowed to see. It combines page visibility with source authority.

**Data flow**: The caller gives page IDs and a SourceReader. The function joins pages to sources, applies subject and _source_readable checks, and returns PageState records by page ID.

**Call relations**: Memory object readers call this when showing stored page-backed records to a member or agent.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 2111–2116)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the live source IDs readable by a given reader. It is the source-level version of the readable page filter.

**Data flow**: The caller gives a SourceReader. The function builds a source query using _source_readable and returns the resulting IDs as a frozenset.

**Call relations**: It shares the same access predicate used by source_pages and readable_page_states.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 2118–2303)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers or revives a content-sync source for this workspace and grants an agent access to it. A source represents an external feed such as an account, folder, or stream that the sync driver will poll.

**Data flow**: The caller gives backend, typed config, subject, owner, optional connection, and optional target agent. The function computes a stable source ID, verifies the target agent and connection authority, inserts or locks the source row, rejects conflicting authority or requested fields, revives removed rows when needed, grants the agent, and returns the source ID.

**Call relations**: The sample extension setup calls this. It uses source_id, model_dump, workspace_tx, database upserts, and source_grant insertion.

*Call graph*: calls 1 internal fn (source_id); called by 1 (_setup); 5 external calls (now, model_dump, select, update, workspace_tx).


##### `ExtensionContext.grant_source`  (lines 2305–2361)

```
async def grant_source(self, source_id: UUID, *, agent_id: UUID, actor_member_id: UUID) -> None
```

**Purpose**: Grants another agent access to an already registered source without duplicating the synced feed. It enforces that the actor may share that source.

**Data flow**: The caller gives source ID, target agent, and actor member. The function locks the source, checks ownership or shared subject, verifies the target agent is in this workspace, inserts the grant if missing, and returns nothing.

**Call relations**: It complements register_source, which grants the initial agent while creating or finding the source.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.source_id`  (lines 2363–2381)

```
def source_id(self, backend: str, config: BaseModel, *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Computes the stable row ID that a source registration would use. This lets callers refer to a source before or without reading it.

**Data flow**: The caller gives backend, config, and optional connection ID. The function serializes the config and passes workspace, backend, config, connection, and non-identity fields to source_row_id.

**Call relations**: register_source calls this to decide which row to insert or revive. removed_source_ids can then check whether such IDs were removed.

*Call graph*: called by 1 (register_source); 2 external calls (model_dump, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 2383–2406)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which of the given source IDs are known removed in this workspace. Absence is not treated as removal.

**Data flow**: The caller gives source IDs. Empty input returns an empty frozenset; otherwise it queries rows whose removed_at is set and returns their IDs.

**Call relations**: It helps extensions distinguish sources that were deliberately deleted from sources that simply have not been registered.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 2408–2452)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists this workspace’s live registered sources, optionally for one backend. Removed sources are hidden.

**Data flow**: The caller may give a backend. The function queries live source rows, orders them, and converts each row into a SourceRecord.

**Call relations**: Gbrain and sources extensions call this to discover registered feeds and bindings.

*Call graph*: called by 2 (_registered_from_ext, _bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 2454–2502)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a specific source reader. It applies workspace, page, subject, and source authority checks.

**Data flow**: The caller gives a SourceReader. The function joins pages to sources, filters out tombstones, applies _source_readable, and returns PageRecord objects.

**Call relations**: It is the page listing counterpart to readable_source_ids and readable_page_states.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 2504–2520)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Tombstones one live page so downstream indexing can remove derived data. It fails if the page is missing or already forgotten.

**Data flow**: The caller gives a page ID. The function updates that page in the current workspace to tombstone true and a new updated_at timestamp; if no row changed, it raises ValueError.

**Call relations**: It is the single-page cleanup operation matching source_pages reads.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 2522–2559)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source and tombstones its live pages in one transaction. The source row remains as history and can be revived by registering the same config later.

**Data flow**: The caller gives a source ID. The function marks the source removed, clears claims, deletes its grants, tombstones its live pages, and raises if no live source matched.

**Call relations**: It uses database update and delete operations so the sync driver stops claiming the source and page-change processing can clear indexes.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 2561–2587)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for live sources and their live pages together. This makes re-indexing happen under the new visibility rules.

**Data flow**: The caller gives source IDs and a subject. The function updates matching live sources, raises if none matched, then updates all live pages under those sources with the new subject and timestamp.

**Call relations**: It keeps source rows and page rows consistent in one transaction.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 2589–2662)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Updates non-identity configuration fields for live sources, optionally forcing some to refetch from scratch. It prevents changes that would make the row’s stable ID no longer match its config.

**Data flow**: The caller gives a mapping from source IDs to new configs and an optional refetch set. The function validates inputs, locks rows, recomputes each source ID to prove identity did not move, writes the new config, and clears cursor and claim fields for refetched rows.

**Call relations**: It calls source_row_id for safety and uses one workspace transaction so multi-stream bindings are not partly changed.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 2664–2693)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Requests that live sources sync as soon as possible. It also clears parking markers from sources that were slowed down after repeated refusals.

**Data flow**: The caller gives source IDs. The function updates matching live sources to next_sync_at now, clears parked/refusal fields, and raises if no live source matched.

**Call relations**: The sync driver later sees the updated schedule and claims the rows through its normal lease mechanism.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 2695–2701)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Opens a governed proposal to change an agent prompt instead of editing the agent directly. This keeps prompt changes reviewable and digest-checked.

**Data flow**: The caller gives an AgentChange. The function creates a Governance object for this workspace and extension, submits the change, and returns a ProposalRef.

**Call relations**: The sample extension tick calls this. Governance owns the proposal and later approval flow.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 2703–2708)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace’s transcript corpus through the wired TrajectoryCorpus. It fails loudly if no corpus was configured.

**Data flow**: It checks that self.corpus exists, then calls its trajectories method and returns the tuple of Trajectory records.

**Call relations**: The sample extension tick calls this for evaluation-style work. context_for wires the corpus only when blob storage is supplied.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 2711–2782)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: WorkspaceBlobStore | None=None, sandboxes:
```

**Purpose**: Builds the ExtensionContext object handed to an extension handler or core job. It wires only the capabilities that run is allowed to have.

**Data flow**: The caller supplies extension name, declared credentials, optional model/index/blob/sandbox/invoker/source/tailer settings, surface declarations, member-context permissions, URLs, and secrets. The function validates model wiring, constructs ScopedStore, CredentialAccess, installation access, optional corpus/files/model access, and returns the complete ExtensionContext.

**Call relations**: This is the factory that makes core jobs and extensions receive the same scoped shape. It calls the constructors for the smaller capability objects and refuses a model resolver without a model_job label.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### `core/src/ufo/ext/conversation_slots.py`

`data_model` · `conversation slot validation and extension read/summarize calls`

A conversation can have side panels or “slots” in the portal that show useful extra information: files created during the chat, web sources, task lists, related sites, scheduled automations, or workspace changes. This file is the rulebook for what those pieces of information must look like before they are accepted.

Most of the file is made of Pydantic models. Pydantic is a validation library: it checks incoming data and turns it into predictable Python objects. Each model sets limits, such as maximum title length or maximum number of items, so an extension cannot accidentally send a huge or malformed payload. Several URL fields are checked to make sure they are normal HTTP or HTTPS links and do not include embedded usernames or passwords. That matters because these links may be displayed in a browser.

The file also defines context and provider dataclasses. A context is the bundle of information a slot reader receives, such as the conversation id, agent id, audience, messages, and currently visible items. A provider says: “this extension offers this kind of slot, with this label and icon, and here are the functions to summarize and read it.” Without this file, extensions would not have a shared, safe contract for conversation slot data.

#### Function details

##### `ImagePreview.drawable_url`  (lines 52–69)

```
def drawable_url(cls, value: str) -> str
```

**Purpose**: This validator checks that an image preview URL is safe and usable by the portal when drawing an image. It rejects links that are not HTTP or HTTPS, links with embedded login details, links with fragments, backslashes, or hidden control characters.

**Data flow**: A URL string comes in as part of an ImagePreview. The function splits it into parts, decodes escaped characters, and inspects it for unsafe or unsuitable pieces. If the URL passes all checks, the same string comes out; if not, validation stops with an error.

**Call relations**: Pydantic calls this automatically when an ImagePreview is created. Inside the check, it uses standard URL parsing, URL decoding, and Unicode character classification so the rest of the portal can treat accepted preview links as safe enough to place in browser-facing image markup.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 85–96)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: This validator checks the optional download or viewing URL for a conversation artifact, such as a generated file. It allows missing URLs, but any provided URL must be a normal HTTP or HTTPS link without embedded credentials.

**Data flow**: The artifact URL value comes in, either as None or as a string. If it is None, it is returned unchanged. If it is a string, the function parses it and verifies its scheme, host, username, and password fields; a valid URL is returned, while an invalid one raises a validation error.

**Call relations**: Pydantic calls this while building a ConversationArtifact. The function relies on standard URL parsing, and its result helps ensure artifact records shown in conversation slots do not carry unsafe or private credential-bearing links.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 117–126)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validator checks that a source citation URL is a safe web link. It requires HTTP or HTTPS, a real host name, and no username or password hidden inside the link.

**Data flow**: A source URL string comes in. The function parses it into its URL parts, checks that it is a web URL with a host and no embedded credentials, then returns the original string if it is acceptable. If the URL fails those checks, it raises a validation error.

**Call relations**: Pydantic calls this whenever a ConversationSource is created. This protects the sources slot before the portal displays citations or search results to a user.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 155–169)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: This validator makes sure the task list summary tells a coherent story. For example, it prevents saying there are 3 total tasks, 5 completed tasks, or showing more visible tasks than the stated total.

**Data flow**: A full TasksSlotPayload object comes in after its fields have been filled. The function compares total_count, completed_count, the visible tasks, their statuses, and the truncated flag. If the numbers agree, the same object comes out; if any count contradicts another, validation fails with a clear error.

**Call relations**: Pydantic calls this after creating a TasksSlotPayload. It does not hand off to other project functions; instead, it acts as the final consistency check before task data can be exposed through a conversation slot.


##### `ConversationSite.http_url`  (lines 184–193)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validator checks that a related site URL is a safe HTTP or HTTPS address with no embedded login details. It helps keep site links suitable for display and navigation in the portal.

**Data flow**: A site URL string comes in. The function parses it, confirms it has an accepted web scheme and host, and rejects it if it includes a username or password. A valid URL is returned unchanged; an invalid one raises a validation error.

**Call relations**: Pydantic calls this while building a ConversationSite. It uses standard URL parsing so site slot entries follow the same basic safety rule as artifact and source links.

*Call graph*: 1 external calls (urlsplit).


### `core/src/ufo/ext/manifest.py`

`data_model` · `extension loading and startup, with the declared shapes reused during turns, jobs, routes, and hooks`

This file is mostly a set of frozen data shapes: small, read-only records that say what an extension provides. Think of it like a customs declaration form for plugins. Instead of an extension directly wiring itself into the app, it returns a Manifest, and the core loader reads that manifest to decide what to install, expose, validate, or run.

The declarations cover many parts of the system. An extension can declare tools the agent may call, HTTP routes, background jobs, credential slots, OAuth connectors, search providers, browser providers, sandbox backends, hooks that react to turn events, shipped agents, subagent profiles, skills, prompt sections, and more. A Pack is similar, but works at a product-bundle level: it names a set of extensions plus pack-level skills and onboarding steps.

The file also includes a few guardrails. Some records check their own values as soon as they are created, for example making sure shipped agent names and icons are valid, or making sure hook settings are only used where safe. The helper functions gather global declarations across all active manifests and reject ambiguous setups, such as two extensions claiming the same conversation slot or two catch-all connector namespaces. Without this file, extensions would not have a clear, safe, shared language for telling core what they add.

#### Function details

##### `PreToolUse.__post_init__`  (lines 397–399)

```
def __post_init__(self) -> None
```

**Purpose**: Fills in the meaningful tool-call name for a pre-tool hook when the caller did not provide one. This lets hooks match either the raw tool name or a more specific action name, while keeping the common case simple.

**Data flow**: A PreToolUse event is created with a tool name, validated tool input, and maybe an empty call field. After creation, this method checks whether call is blank. If it is, it copies tool_name into call; otherwise it leaves the supplied call alone.

**Call relations**: This is run automatically by the dataclass machinery when a PreToolUse payload is built before a tool is dispatched. Later hook matching can rely on call always having a usable value instead of checking both call and tool_name.


##### `PostToolUse.__post_init__`  (lines 415–417)

```
def __post_init__(self) -> None
```

**Purpose**: Makes sure a successful tool-use event always has a call identifier. That identifier is what hook rules use to recognize which tool or object action just ran.

**Data flow**: A PostToolUse event comes in with the tool name, input, output text, and possibly no call value. The method replaces a missing call value with the tool name. The event object then carries a consistent identity for later hook processing.

**Call relations**: This runs automatically right after a PostToolUse payload is created for a tool that completed successfully. The hook system can then pass the event to handlers without special casing missing call names.


##### `PostToolUseFailure.__post_init__`  (lines 435–437)

```
def __post_init__(self) -> None
```

**Purpose**: Gives failed tool-use events the same reliable call identifier that successful tool-use events have. This helps failure-observing hooks know exactly what failed.

**Data flow**: A PostToolUseFailure event is created with the tool name, input, error output, and maybe a blank call field. The method fills call from tool_name only when call was not provided. Nothing else is changed.

**Call relations**: This is automatically invoked when the system builds a failure payload for a tool that actually ran and errored. Any later failure hook receives a payload whose call field is safe to use for matching.


##### `HookSpec.__post_init__`  (lines 582–584)

```
def __post_init__(self) -> None
```

**Purpose**: Rejects unsafe hook declarations. In this design, only user-prompt hooks may be marked best effort, meaning their failure can be ignored instead of blocking the turn.

**Data flow**: A HookSpec is created with an event name, handler, optional tool filters, and a best_effort flag. The method checks whether best_effort is true on anything other than user_prompt_submit. If so, it raises an error; otherwise the hook declaration is accepted.

**Call relations**: This validation happens as extension manifests are built or loaded. It protects the larger hook flow: prompt-context additions may fail softly, but tool-safety gates are not allowed to fail open.


##### `AgentProvision.__post_init__`  (lines 616–642)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a shipped agent is usable and safe before it can be installed into a workspace. It catches mistakes such as bad names, invalid icons, missing prompts, missing purposes, or tool allowlists that would prevent setup from working.

**Data flow**: An AgentProvision is created from an agent name, its AgentSpec, optional tool allowlist, setup instructions, icon, and main-agent flag. The method validates the name format, forbids allowlisting the generic object-action dispatcher, validates the icon, requires a non-empty prompt and purpose, and, when setup connectors are declared with a tool allowlist, makes sure the setup tools are included. If anything is wrong, it raises an error; if all checks pass, the provision remains unchanged.

**Call relations**: This runs automatically when an extension declares an agent to ship. Later activation can create or update the workspace’s agent row knowing the provision already satisfies these basic rules.


##### `SubagentProfile.__post_init__`  (lines 685–707)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the declaration for a typed subagent profile. It prevents extensions from granting the wrong kind of tool name and checks that the “concise parent handoff” setting matches the profile’s output schema.

**Data flow**: A SubagentProfile is created with a name, prompt, allowed tool names, input and output models, model options, and behavior flags. The method rejects the generic object-action dispatcher in the tool list. It then inspects the output model’s fields to see whether it has the special one-field concise result contract. If the concise flag and the schema disagree, it raises an error. Otherwise the profile is accepted unchanged.

**Call relations**: This is triggered when a subagent profile is declared by an extension. The subagent registry and spawn flow can later trust that concise handoffs are marked honestly and that tool allowlists name real callable actions rather than the internal dispatcher.


##### `SubagentToolGrant.__post_init__`  (lines 726–731)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a grant adding tools to another subagent profile names actual callable tool or action identifiers, not the internal object-action dispatcher.

**Data flow**: A SubagentToolGrant is created with a target profile name and a list of tool names to add. The method scans the list. If it contains the generic object-action tool, it raises an error; otherwise the grant is left as declared.

**Call relations**: This runs as extensions declare cross-profile tool grants. Later, when the loader combines grants into subagent profiles, it can treat the grant as an add-only widening of allowed tools without dealing with this invalid dispatcher case.


##### `conversation_slot_declarations`  (lines 824–853)

```
def conversation_slot_declarations(manifests: tuple[Manifest, ...]) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]
```

**Purpose**: Collects all conversation-slot providers from the active manifests and validates them as one shared namespace. A conversation slot is a typed extra piece of conversation state, so duplicate or malformed slot IDs would confuse the portal and runtime readers.

**Data flow**: The function receives all active manifests. It walks through each manifest’s conversation slot providers, checks that each slot ID has the right format, label, icon, callbacks, and supported content type, and remembers which extension claimed each ID. If a slot is invalid or two extensions use the same ID, it raises an error. If all are valid, it returns pairs of the owning manifest and provider.

**Call relations**: The loader uses this during startup when assembling extension contributions. It hands back a clean list that later UI and conversation code can use without guessing who owns a slot or whether its read functions exist.


##### `open_connector_namespace`  (lines 856–868)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: Finds the single catch-all connector namespace, if any, across all active manifests. This matters because a catch-all connector resolver answers for connector names that were not explicitly registered, so having two would make ownership ambiguous.

**Data flow**: The function receives all manifests and scans their connector_resolver field. If none are present, it returns None. If one is present, it returns that resolver. If it finds a second one, it raises an error because the system could not safely choose between them.

**Call relations**: Startup code uses this while building connector and OAuth routing. The returned namespace becomes the fallback path for unregistered connector slugs; the error case prevents later connect flows, catalogs, and transfer-host decisions from disagreeing.


##### `declared_slots`  (lines 891–904)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: Turns extension credential declarations into the simpler credential-slot records used by the rest of the system. These records feed places like the credential object kind and the portal’s credentials panel.

**Data flow**: The function receives all active manifests. For every CredentialSlot in every manifest, it builds a DeclaredSlot containing the slot name, description, owning extension name, whether a member may fill it, and the injected host if the slot has wire injection. It returns all of those DeclaredSlot records as a tuple.

**Call relations**: This is called when the system needs a unified view of all BYOK credentials, meaning “bring your own key” secrets supplied by a workspace member. It calls DeclaredSlot.__init__ to create each public declaration from the richer manifest data.

*Call graph*: 1 external calls (__init__).
