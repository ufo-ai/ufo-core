# Core Runtime Extension Contracts and Object Naming  `stage-18.1`

This stage is shared behind-the-scenes support for the runtime. It does not run the main conversation by itself. Instead, it defines the safe rules and common shapes that other parts rely on when extensions, agents, and built-in jobs do work.

The package marker files in host/kinds and runtime/kinds simply make those folders importable in Python, so the rest of the code can find their modules. The runtime/ext package marker defines the boundary for the extension API: what the platform gives to extensions and what extensions may provide back.

The main safety gate is context.py. It builds the “context” object handed to extensions and background jobs. This is like a limited toolbox: it allows access only to approved storage, credentials, workspace data, model calls, files, transcripts, sources, and turn calls.

object_name.py gives all runtime objects one shared naming rule and a small reference type for “this kind of object with this name.” contracts.py defines how agent inputs and outputs are checked, whether they use Pydantic models or JSON Schema.

## Files in this stage

### Host Kind Boundary
Package marker for host-side kind modules used by the runtime.

### `core/src/ufo/host/kinds/__init__.py`

`other` · `import time`

This is an empty package file. In Python projects, a file named `__init__.py` is commonly used to tell Python, “this folder is an importable package.” Here, it means code elsewhere can refer to the `ufo.host.kinds` area as a named part of the project.

Think of it like a label on a drawer. The drawer may contain useful files, but the label itself does not do any work. Without this file in projects or environments that rely on traditional Python packages, imports involving `ufo.host.kinds` could fail or behave differently.

Because the file is empty, it does not define classes, functions, settings, or side effects. Its value is structural: it helps organize the codebase and gives a stable place for modules related to “host kinds” to live.


### Extension Runtime Context
Defines the runtime extension API boundary and the safe context object exposed to extensions and background jobs.

### `core/src/ufo/runtime/ext/__init__.py`

`data_model` · `cross-cutting`

This package is the doorway into the system’s extension API. An extension API is the agreed set of tools, types, and rules that outside add-ons or built-in feature modules use to interact with the core platform. In everyday terms, it is like a socket shape on a wall: anything that wants to plug in must match the shape, and the wall promises what power it will provide.

This particular file does not contain executable code. Its job is to label the package and state its purpose for readers and tools. The surrounding package is where the actual extension-facing definitions live: what services the runtime makes available, and what structure extension contributions must follow. Without this package boundary, extension-related code would be harder to find, document, and import consistently.


### `core/src/ufo/runtime/ext/context.py`

`orchestration` · `cross-cutting: active whenever an extension, background job, surface handler, or tool uses scoped runtime capabilities`

An extension should not get the keys to the whole system. This file is the guardrail that turns broad internal services into small, scoped capabilities. Think of it like giving a contractor a badge that opens only the rooms they need, rather than handing over the master key.

The main object is `ExtensionContext`. It is built by `context_for`, and it gathers many smaller access objects. `ScopedStore` gives an extension a private key-value shelf inside the current workspace. `CredentialAccess` lets it read only credential slots it declared ahead of time. `TrajectoryCorpus` lets certain jobs read conversation transcripts, but only for the current workspace. `ConversationFiles` and `ConversationProbes` let approved code write files or run short commands in a conversation sandbox. `ModelAccess` lets background jobs call the configured language model while recording cost and timing.

Most methods read the “ambient workspace,” meaning the workspace already bound to the current job or request. Callers do not pass arbitrary workspace IDs, which helps prevent accidental cross-tenant access. Many methods also fail loudly when a needed capability was not wired in, instead of pretending nothing happened. Without this file, extensions would either be too weak to do useful work or too powerful to be safe.

#### Function details

##### `ScopedStore.workspace_id`  (lines 118–119)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace currently bound to the running job or request. This keeps the store tied to the active workspace instead of letting callers choose one.

**Data flow**: It reads the current workspace scope and returns its ID. It does not take input and does not change anything.

**Call relations**: All `ScopedStore` reads and writes rely on this property so their database queries stay inside the active workspace.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 121–132)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one JSON value from an extension’s private storage area. Extensions use it to remember durable state such as reply progress or browser session details.

**Data flow**: It takes a key, combines it with the current workspace and extension name, queries the extension store table, and returns the saved value or `None` if absent.

**Call relations**: Surface and browser extensions call this when resuming work. It opens a workspace transaction and uses a scoped SQL query so it cannot read another extension’s or workspace’s keys.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_web_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 134–149)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several named keys from an extension’s private store in one database trip. This is useful when a listing needs selected values without scanning everything.

**Data flow**: It takes a sequence of keys, returns an empty dictionary for no keys, otherwise fetches matching rows for the current workspace and extension and returns a key-to-value dictionary.

**Call relations**: It uses the same storage boundary as `get`, but batches the work through one transaction.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 151–175)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Writes or replaces one JSON value in the extension’s private store. It is designed to be safe when two tasks try to create the same key at the same time.

**Data flow**: It takes a key and value, builds an insert-or-update database command, and saves the value under the current workspace and extension.

**Call relations**: Browser, Slack, and web surface code call this to record state. It delegates the database work to the current workspace transaction.

*Call graph*: called by 4 (_start, _context, _hold_connect_message, _open_conversation); 1 external calls (workspace_tx).


##### `ScopedStore.put_if`  (lines 177–227)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the stored value still matches what the caller expected. This prevents stale background work from overwriting newer progress.

**Data flow**: It takes a key, new value, and expected old value. If the expected value is `None`, it tries to insert only if absent. Otherwise it locks the row, compares the current value, updates on a match, and returns `true` or `false`.

**Call relations**: Slack reply checkpoint code uses this for safe progress updates. It relies on database locking inside `workspace_tx` to make the compare-and-write act as one step.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 229–237)

```
async def delete(self, key: str) -> None
```

**Purpose**: Deletes one key from an extension’s private store. It is used to clean up temporary or completed state.

**Data flow**: It takes a key and removes the row matching the current workspace, extension, and key. It returns nothing.

**Call relations**: Slack and web surface cleanup paths call this. The workspace transaction enforces the same scoped boundary as the rest of `ScopedStore`.

*Call graph*: called by 2 (_drop_turn_reply_records, _open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 239–252)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists stored key-value pairs for this extension, optionally under a prefix. It gives an extension a controlled way to enumerate its own saved state.

**Data flow**: It takes an optional prefix, queries matching keys for the current workspace and extension, sorts them, and returns a tuple of key-value pairs.

**Call relations**: Slack cleanup and web audience code use it to discover stored records. It never lists outside the extension’s namespace.

*Call graph*: called by 3 (_drop_turn_reply_records, _granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 268–269)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose credentials are being accessed. It prevents credential access from being detached from the current run’s workspace.

**Data flow**: It reads the ambient workspace scope and returns its ID.

**Call relations**: Credential resolution methods use this property when they need to look up stored or source-derived secrets.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 271–277)

```
async def get(self, slot: str) -> str
```

**Purpose**: Fetches a declared credential slot’s current secret. It refuses requests for slots the extension did not declare in its manifest.

**Data flow**: It takes a slot name, checks it against the declared set, then asks the current workspace for that credential. It returns the secret string or raises an error.

**Call relations**: Slack verification uses this to read its signing material. The undeclared-slot check happens before any secret lookup.

*Call graph*: called by 1 (verifying_fingerprint); 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 279–286)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Reports whether a credential is supplied by the workspace itself rather than by a platform default. This matters for billing and ownership of provider costs.

**Data flow**: It takes a declared slot name, checks permission, then asks the current workspace whether that credential is stored locally. It returns a boolean.

**Call relations**: It follows the same declaration gate as `get`, but returns provenance rather than the secret.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.resolve`  (lines 288–301)

```
async def resolve(self, slot: str) -> str
```

**Purpose**: Resolves a declared credential slot through its configured source when one exists, otherwise through the workspace credential system. It supports both direct secrets and provider-installation based credentials.

**Data flow**: It takes a slot name, verifies it was declared, looks for a matching credential source, tries the source store if present, and falls back to the workspace credential value.

**Call relations**: It calls the credential-source helper `slot_secret` when a slot has special source rules, while keeping the raw credential store hidden from extensions.

*Call graph*: 3 external calls (__init__, slot_secret, ws_current).


##### `CredentialAccess.rotate`  (lines 303–308)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if the caller’s expected old value still matches. This is for safe provider key rotation.

**Data flow**: It takes a slot, expected old plaintext, and new plaintext. After checking the slot was declared, it asks the current workspace to do the compare-and-swap update and returns whether it succeeded.

**Call relations**: It delegates the actual secret update to the workspace credential layer, preserving the manifest-based access check here.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 310–323)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores a sealed provider installation reference in a declared credential slot. The seal proves the installation belongs to this workspace and slot.

**Data flow**: It takes a slot and installation ID, verifies the slot is declared, seals the workspace-slot-installation combination, and saves that sealed value as the workspace credential.

**Call relations**: It uses the installed credential request signer and the workspace credential writer. Extensions never receive the signing secret directly.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 356–357)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose transcripts this corpus may read. This keeps transcript access tenant-scoped.

**Data flow**: It reads the current workspace scope and returns its ID.

**Call relations**: Transcript enumeration and targeted transcript reads use this property to limit their database queries.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 359–366)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for the current workspace. Evaluation or improvement jobs use this as their local corpus.

**Data flow**: It builds a query for the newest eligible conversations in the workspace and passes that selection to `_read`, which loads and decodes the transcript blobs.

**Call relations**: This is the broad corpus entry point. It delegates the shared transcript-loading work to `_read`.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus.conversations`  (lines 368–380)

```
async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]
```

**Purpose**: Reads transcripts for exactly the conversation IDs the caller names, while still enforcing the current workspace boundary.

**Data flow**: It takes conversation IDs, builds a workspace-scoped selection for those IDs, and asks `_read` to load decoded trajectories.

**Call relations**: Jobs that already know their target conversations use this instead of the recent-corpus listing. `_read` does the actual database and blob work.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus._read`  (lines 382–426)

```
async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]
```

**Purpose**: Loads conversation metadata and transcript blobs, turning them into `Trajectory` records. It skips missing or corrupt transcripts instead of failing the whole corpus.

**Data flow**: It receives a SQL selection of conversation IDs, reads each conversation’s agent and prompt, fetches the transcript blob, decodes messages, computes the prompt digest, and returns trajectory objects.

**Call relations**: `trajectories` and `conversations` both call this. It combines database rows, blob storage, transcript decoding, and governance prompt hashing into the read-only corpus result.

*Call graph*: called by 2 (conversations, trajectories); 7 external calls (__init__, select, workspace_tx, log, prompt_digest, decode, transcript_key).


##### `ConversationFiles.write`  (lines 445–448)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s workspace so the agent can see the file later. It is an off-turn way to place files in the same sandbox used by agent tools.

**Data flow**: It takes a conversation ID, relative path, and content bytes, forwards them to the sandbox writer, and returns the visible `/workspace` path.

**Call relations**: This is a thin capability wrapper over `ConversationSandbox.write`, exposing only scoped file writing rather than the whole sandbox object.


##### `ConversationFiles.prune`  (lines 450–456)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Keeps only the newest files under a given path prefix in a conversation workspace. This prevents unattended writers from filling the sandbox forever.

**Data flow**: It takes a conversation ID, path prefix, and keep count, then asks the sandbox layer to delete older matching files.

**Call relations**: It pairs with `write` as the cleanup operation for extension-created conversation files.


##### `ConversationFiles.write_runtime`  (lines 458–462)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output into a named category inside a conversation’s sandbox. This separates system-produced files from ordinary workspace files.

**Data flow**: It takes a conversation ID, category, relative path, and bytes, forwards them to the sandbox runtime writer, and returns the written path.

**Call relations**: It is another narrow wrapper around the sandbox layer, exposing only categorized runtime writes.


##### `ConversationFiles.prune_runtime`  (lines 464–472)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Prunes old internal runtime files for one conversation and category. It bounds storage used by repeated runtime output.

**Data flow**: It takes a conversation ID, category, prefix, and keep count, then delegates deletion of older files to the sandbox layer.

**Call relations**: It is the cleanup partner for `write_runtime`.


##### `conversation_agent_id`  (lines 475–488)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation belongs to inside a workspace. It returns nothing if the conversation ID does not belong to that workspace.

**Data flow**: It takes workspace and conversation IDs, queries the conversation table, and returns the agent ID or `None`.

**Call relations**: `ConversationProbes.run` uses it before opening a sandbox, and `ExtensionContext.conversation_agent` exposes it to handlers.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 527–577)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, acting_member_id: UUID | None=None) -> ExecResult
```

**Purpose**: Runs one short shell command inside a conversation’s sandbox outside the normal turn flow. It is for bounded probes, not long-running background processes.

**Data flow**: It takes a conversation ID, command, timeout, and optional acting member. It checks the timeout, verifies the conversation belongs to the current workspace, creates a signed probe token, opens the sandbox under the conversation’s agent, runs the command, and returns stdout, stderr, and exit code.

**Call relations**: It calls `conversation_agent_id` to bind the probe to the right agent, uses the injected token codec and environment builder, and delegates execution to the sandbox session.

*Call graph*: calls 1 internal fn (conversation_agent_id); 5 external calls (__init__, now, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 580–602)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace candidate set for jobs that need conversation transcripts. Only workspaces with at least one turned conversation are candidates.

**Data flow**: It defines a query-producing function and wraps it in `owner_candidates`, returning an object the job dispatcher can use to find workspaces.

**Call relations**: The nested `with_a_turn` function supplies the actual SQL shape. The dispatcher later binds each workspace before the corpus is read.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 588–600)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces with at least one conversation that has at least one turn.

**Data flow**: It produces a select statement using existence checks over workspace, conversation, and turn tables.

**Call relations**: It is used only by `trajectory_workspaces` as the candidate query passed to `owner_candidates`.

*Call graph*: 2 external calls (exists, select).


##### `seated_member_workspaces`  (lines 605–615)

```
def seated_member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate set for jobs that should run only where at least one member has an active seat.

**Data flow**: It defines a query for distinct workspace IDs with seated members and wraps it as workspace candidates.

**Call relations**: The nested query function is handed to `owner_candidates` so the scheduler can choose eligible workspaces.

*Call graph*: 1 external calls (owner_candidates).


##### `seated_member_workspaces.with_a_seated_member`  (lines 608–613)

```
def with_a_seated_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces containing at least one seated member.

**Data flow**: It selects distinct workspace IDs from member rows whose seated time is present.

**Call relations**: It is the query body used by `seated_member_workspaces`.

*Call graph*: 1 external calls (select).


##### `connection_workspaces`  (lines 618–631)

```
def connection_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate set for connection-driven jobs. A workspace qualifies when its main agent has a connector grant.

**Data flow**: It defines a query over connector grants and agents, then wraps it in a workspace-candidate helper.

**Call relations**: The nested `with_a_main_agent_connection` query gives `owner_candidates` the selection logic.

*Call graph*: 1 external calls (owner_candidates).


##### `connection_workspaces.with_a_main_agent_connection`  (lines 623–629)

```
def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces where the main agent has at least one connected account grant.

**Data flow**: It joins connector grants to agents, filters for main agents, and returns distinct workspace IDs.

**Call relations**: It is used by `connection_workspaces` to tell the dispatcher where connection-based work may exist.

*Call graph*: 1 external calls (select).


##### `agent_is_live`  (lines 634–647)

```
def agent_is_live(workspace_id: sa.ColumnElement[UUID], agent_id: sa.ColumnElement[UUID]) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition that is true only for an unarchived agent in the given workspace. It lets sweeps avoid spending effort on work that the invoke seam would refuse.

**Data flow**: It takes SQL expressions for workspace ID and agent ID, and returns an existence predicate over the agent table.

**Call relations**: Other candidate or sweep queries can include this predicate before doing real work for an agent.

*Call graph*: 2 external calls (exists, select).


##### `awaiting_a_title`  (lines 653–669)

```
def awaiting_a_title() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the condition for conversations that still need an automatic title. A conversation qualifies only after a member turn has completed and no summary title has been recorded.

**Data flow**: It returns a SQL boolean expression combining the unsummarized flag with an existence check for a completed member-admitted turn.

**Call relations**: `conversations_awaiting_title` and the untitled-workspace candidate query both reuse this exact condition.

*Call graph*: called by 2 (conversations_awaiting_title, with_an_unsummarized_title); 3 external calls (and_, exists, select).


##### `untitled_conversation_workspaces`  (lines 672–681)

```
def untitled_conversation_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate set for the title-summary job. Only workspaces with conversations still needing summaries are selected.

**Data flow**: It defines a query for workspace IDs with `awaiting_a_title` conversations and wraps it for the dispatcher.

**Call relations**: Its nested query calls `awaiting_a_title`, keeping the workspace-level and conversation-level title rules consistent.

*Call graph*: 1 external calls (owner_candidates).


##### `untitled_conversation_workspaces.with_an_unsummarized_title`  (lines 678–679)

```
def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces containing at least one conversation awaiting a title.

**Data flow**: It selects distinct workspace IDs from conversations matching `awaiting_a_title`.

**Call relations**: It is used only by `untitled_conversation_workspaces` as the candidate query.

*Call graph*: calls 1 internal fn (awaiting_a_title); 1 external calls (select).


##### `unseeded_agent_workspaces`  (lines 684–713)

```
def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Builds a candidate set for once-per-agent setup jobs. A workspace qualifies while it has more agents than extension store markers for completed setup.

**Data flow**: It takes an extension name and key prefix, defines a query comparing agent count to stored marker count, and wraps it as workspace candidates.

**Call relations**: The nested count query lets a sweep run until every agent has a corresponding extension marker.

*Call graph*: 1 external calls (owner_candidates).


##### `unseeded_agent_workspaces.with_an_unsettled_agent`  (lines 696–711)

```
def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces where some agent has not yet been settled by an extension sweep.

**Data flow**: It counts agents and matching extension-store keys per workspace, then selects workspaces where the agent count is larger.

**Call relations**: It is used by `unseeded_agent_workspaces` to drive setup jobs such as homepage seeding.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 730–744)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, on_behalf_of_member_id: UUID | None=None, holds_work_already_done: bool=False, as_scheduled: bool=F
```

**Purpose**: Defines the interface for starting an internal turn from background code. It is a contract, not an implementation.

**Data flow**: Implementations receive conversation, agent, message, idempotency, member authority, and scheduling options, then return the admitted turn ID or `None` if admission is refused.

**Call relations**: `ExtensionContext.invoke` calls this protocol when an invoker has been wired into the context.


##### `ModelResolver.auto_model`  (lines 754–754)

```
def auto_model(self) -> str
```

**Purpose**: Defines the interface property for the deployment’s default model ID. It lets this file depend on a small contract instead of importing the full model registry.

**Data flow**: An implementation returns a string naming the default model.

**Call relations**: `ModelAccess.model` and `ModelAccess.turn` read this property before making model calls.


##### `ModelResolver.pricing`  (lines 757–757)

```
def pricing(self) -> Pricing
```

**Purpose**: Defines the interface property for model pricing data. Pricing is needed to convert token usage into billable cost.

**Data flow**: An implementation returns a pricing table object.

**Call relations**: `ModelAccess.turn` uses it while recording usage inside a billable event.


##### `ModelResolver.client_for`  (lines 759–759)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Defines how to obtain a model client for a model ID. The client is the object that actually streams model responses.

**Data flow**: An implementation receives a model ID and returns an asynchronous model client.

**Call relations**: `ModelAccess.turn` calls this before streaming a completion.


##### `ModelResolver.key_slot_for`  (lines 761–761)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Defines how to find which credential slot supplies a model, if any. This tells billing whether a workspace’s own key served the call.

**Data flow**: An implementation receives a model ID and returns a credential slot name or `None`.

**Call relations**: `ModelAccess._serves_itself` and usage export setup use this resolver.


##### `ModelResolver.provider_for`  (lines 763–763)

```
def provider_for(self, model: str) -> str
```

**Purpose**: Defines how to name the provider behind a model. Provider labels are used in metrics and billing views.

**Data flow**: An implementation receives a model ID and returns a provider string.

**Call relations**: `ModelAccess.turn` adds this provider name to emitted model metrics.


##### `ModelAccess.model`  (lines 790–792)

```
def model(self) -> str
```

**Purpose**: Returns the default model that this background model access object will call. It makes the fixed model visible to handlers.

**Data flow**: It reads `auto_model` from the resolver and returns it.

**Call relations**: This is a read-only convenience over the same resolver value used by `turn` and `complete`.


##### `ModelAccess.complete`  (lines 794–801)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one model request and returns only the assistant’s text. It is the simpler API for jobs that do not need tool calls or structured assistant blocks.

**Data flow**: It takes a model request, calls `turn`, then extracts text from the returned assistant message. It returns a string.

**Call relations**: Memory extension summarizers call this. It delegates metering, streaming, and message assembly to `ModelAccess.turn`.

*Call graph*: calls 1 internal fn (turn); called by 3 (_summarize, _write, _summarize).


##### `ModelAccess._serves_itself`  (lines 803–811)

```
async def _serves_itself(self, model: str) -> bool
```

**Purpose**: Checks whether the current workspace is using its own stored provider key for the requested model. This affects whether platform billing should charge for the provider usage.

**Data flow**: It takes a model ID, asks the resolver for the credential slot, and checks whether that slot is stored in the current workspace. It returns a boolean.

**Call relations**: `ModelAccess.turn` calls this just before metering a model call.

*Call graph*: called by 1 (turn); 1 external calls (ws_current).


##### `ModelAccess.turn`  (lines 813–916)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs a full streamed model turn, including text, reasoning blocks, tool calls, usage metering, and timing metrics. It returns an assistant message suitable for feeding back into later model rounds.

**Data flow**: It takes a model request, fixes the model and cache session, streams events from the model client, gathers text and tool-call JSON, records token usage in the billable event, emits metrics, and returns a `Message`.

**Call relations**: `complete` calls this for text-only use, and memory jobs call it directly when they need richer model output. It relies on the resolver, workspace billing, and model event types.

*Call graph*: calls 1 internal fn (_serves_itself); called by 3 (complete, _curate, _write); 10 external calls (__init__, __init__, __init__, __init__, model_copy, loads, monotonic, emit_histogram, emit_metric, ws_current).


##### `_source_readable`  (lines 980–1007)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that says whether a source is readable by a particular agent and member context. It combines workspace, subject, grants, and ownership rules.

**Data flow**: It takes a workspace ID and `SourceReader`, then returns a SQL boolean expression for live sources that match allowed subjects and either have an agent grant or are owned by the requesting member’s main agent.

**Call relations**: Page and source read methods reuse this condition so they all enforce the same access rule.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `MemberContextRecord._aware_utc`  (lines 1060–1061)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures member-context timestamps always have timezone information. If a datetime is naive, it treats it as UTC.

**Data flow**: It receives a datetime from model validation and returns it unchanged if timezone-aware, otherwise returns a UTC-marked version.

**Call relations**: Pydantic calls this validator when `MemberContextRecord` objects are created.

*Call graph*: 1 external calls (replace).


##### `_member_blob_text`  (lines 1067–1085)

```
async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str
```

**Purpose**: Reads a small text preview from blob storage for member context. It caps the amount read so large files do not flood the context.

**Data flow**: It takes a blob store and blob key, streams up to a fixed byte limit, closes the stream, decodes UTF-8 safely, and returns text.

**Call relations**: `ExtensionContext.member_context` uses this for text artifacts and synced page bodies.

*Call graph*: calls 1 internal fn (get_stream); called by 1 (member_context).


##### `ExtensionContext.workspace_id`  (lines 1114–1115)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID for this context. It is a convenience that follows the scoped store’s current workspace.

**Data flow**: It reads `store.workspace_id` and returns that UUID.

**Call relations**: Many context methods use this property to keep their database and link operations tied to the active workspace.


##### `ExtensionContext.image_preview_url`  (lines 1117–1131)

```
def image_preview_url(self, blob_key: str, size_bytes: int) -> str | None
```

**Purpose**: Creates a signed preview URL for an image blob when previews are supported. Extensions can show images without knowing the signing secret.

**Data flow**: It takes a blob key and size, passes the artifact secret, public base URL, blob details, and workspace ID to the URL minting helper, and returns a URL or `None`.

**Call relations**: Sites objects use this when rendering previewable rows. The actual signing is delegated to core media URL code.

*Call graph*: called by 1 (_preview_url); 1 external calls (mint_image_preview_url).


##### `ExtensionContext.artifact_link`  (lines 1133–1139)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a shared artifact. It lets extensions render links while keeping the artifact token secret inside core.

**Data flow**: It takes a shared artifact, combines it with the context’s secret, base URL, and workspace, and returns a URL or `None`.

**Call relations**: Report digest objects call this when building member-facing artifact rows.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_link).


##### `ExtensionContext.artifact_preview_link`  (lines 1141–1146)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview link for a shared artifact when that artifact is eligible for raster preview. It is used for member-facing visual previews.

**Data flow**: It takes a shared artifact and passes it with signing inputs to the surface helper, returning a URL or `None`.

**Call relations**: Report digest rendering calls this beside `artifact_link`.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_preview_link).


##### `ExtensionContext.scheduled_runs`  (lines 1148–1172)

```
async def scheduled_runs(self, member_id: UUID, *, agent_id: UUID | None, limit: int, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Reads recent completed scheduled turns visible to a member. It supports listings that show what automatic work ran and what it produced.

**Data flow**: It takes member, agent, turn, subject, and limit filters, passes them with the workspace ID to the surface query helper, and returns scheduled run records.

**Call relations**: Report digest objects call this for pages and single-run views. The core surface helper performs the detailed visibility and artifact lookup.

*Call graph*: called by 2 (_one, _page); 1 external calls (scheduled_runs).


##### `ExtensionContext.home_url`  (lines 1174–1185)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link to the deployment’s browser portal home surface. It returns nothing if the deployment has no public base URL or no home surface.

**Data flow**: It takes an optional URL fragment, trims the base URL, appends the surface path and fragment, and returns the full URL or `None`.

**Call relations**: Several extensions use it when they need to direct a user back to the web portal without knowing core routes.

*Call graph*: called by 3 (github_installed, _billing_portal, _hook).


##### `ExtensionContext.workspace_agents`  (lines 1187–1219)

```
async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]
```

**Purpose**: Returns the workspace’s agent roster for first-party jobs allowed to read member context. It includes archived agents so once-per-agent sweeps can settle every counted row.

**Data flow**: It checks permission, queries agents in the current workspace, converts each row to a `WorkspaceAgent`, and returns them oldest first.

**Call relations**: The web surface homepage seeding job calls this. It uses `workspace_tx` and refuses access unless member-context reading was explicitly enabled.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.agent_visibilities`  (lines 1221–1233)

```
async def agent_visibilities(self) -> dict[UUID, AgentVisibility]
```

**Purpose**: Reads each agent’s portal visibility setting. This helps extensions decide the minimum audience for things attached to agents.

**Data flow**: It queries agent IDs and visibility values in the current workspace and returns a dictionary keyed by agent ID.

**Call relations**: Unlike the full roster, this workspace-shape read is not gated by member-context permission.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.agent_named`  (lines 1235–1252)

```
async def agent_named(self, name: str) -> AgentIdentity | None
```

**Purpose**: Finds a live agent by name and returns its identity and owner. It is used when member-facing actions refer to agents by stable names.

**Data flow**: It takes an agent name, queries non-archived agents in the workspace, and returns an `AgentIdentity` or `None`.

**Call relations**: Object-kind and action code can use this to check whether a named agent exists and who owns it.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.earliest_seated_admin`  (lines 1254–1272)

```
async def earliest_seated_admin(self) -> UUID | None
```

**Purpose**: Finds the earliest seated admin in the workspace. This gives ownerless background work a deterministic member to act on behalf of.

**Data flow**: It checks member-context permission, queries seated admin members ordered by seat time and ID, and returns the first member ID or `None`.

**Call relations**: Homepage seeding uses this when an agent has no owner. The permission gate prevents general extensions from reading member roster details.

*Call graph*: called by 1 (seed_homepages); 2 external calls (select, workspace_tx).


##### `ExtensionContext.scheduled_member_timezone`  (lines 1274–1289)

```
async def scheduled_member_timezone(self) -> str
```

**Purpose**: Returns the scheduled member’s timezone, defaulting to UTC when none is set. Scheduled jobs use this to interpret member-local times.

**Data flow**: It verifies member-context access and a bound scheduled member, reads that member’s timezone in the current workspace, and returns it or `UTC`.

**Call relations**: It is available only when the context was built for a scheduled member.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.member_context`  (lines 1291–1447)

```
async def member_context(self, *, since: datetime, limit: int=200, exclude_conversation_id: UUID | None=None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Builds a bounded bundle of recent information visible to the scheduled member. This can include conversations, shared files, synced pages, memories, tasks, and objectives.

**Data flow**: It checks permission and limit, computes readable audiences, queries recent turns, artifacts, and pages, reads small blob previews where needed, adds extension-owned records, sorts by date, and returns the newest records up to the limit.

**Call relations**: It calls `_member_blob_text` for blob-backed text and `_member_extension_records` for memory/objective records. It is the main cross-agent context read for scheduled work.

*Call graph*: calls 2 internal fn (_member_extension_records, _member_blob_text); 5 external calls (__init__, exists, select, workspace_tx, readable_audiences).


##### `ExtensionContext._member_extension_records`  (lines 1449–1700)

```
async def _member_extension_records(self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int, exclude_conversation_id: UUID | None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Adds extension-backed memories, tasks, and open objectives to member context. It understands just enough of those extension tables to summarize useful context.

**Data flow**: It takes member, audience, time, limit, and exclusion inputs, queries memory and objective tables, computes which objectives remain open, creates stable subject keys with hashes, and returns context records.

**Call relations**: `member_context` calls this after collecting core conversation, artifact, and page records.

*Call graph*: called by 1 (member_context); 8 external calls (__init__, sha256, DateTime, column, or_, select, table, workspace_tx).


##### `ExtensionContext.retitle_conversation`  (lines 1702–1705)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Sets a conversation title inside the current workspace. It is for code that has chosen a better name for a conversation.

**Data flow**: It takes a conversation ID and title, then delegates the workspace-scoped update to the surface helper.

**Call relations**: This is a thin context method over core surface title-writing logic.

*Call graph*: 1 external calls (retitle_conversation).


##### `ExtensionContext.conversations_awaiting_title`  (lines 1707–1731)

```
async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]
```

**Purpose**: Returns the newest conversations in this workspace that still need summary titles. It lets the title job take a bounded batch each tick.

**Data flow**: It takes a limit, queries workspace conversations matching `awaiting_a_title`, orders newest first, and returns their IDs.

**Call relations**: The web surface title summarizer calls this, then later calls `summarized_conversation_title` for each processed conversation.

*Call graph*: calls 1 internal fn (awaiting_a_title); called by 1 (summarize_chat_titles); 2 external calls (select, workspace_tx).


##### `ExtensionContext.summarized_conversation_title`  (lines 1733–1738)

```
async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Writes a summary-generated title and marks the summary attempt as done. This removes the conversation from future title-summary work.

**Data flow**: It takes a conversation ID and title, and delegates to the surface helper that updates both the title and summarized marker.

**Call relations**: The web title summarizer calls this after generating a title.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (summarize_conversation_title).


##### `ExtensionContext.pending_usage_exports`  (lines 1740–1759)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Prepares and reads usage-export records that this extension has not yet acknowledged. It is the export seam for billing integrations.

**Data flow**: It takes a time floor and limit, requires a model key-slot resolver, mints any newly exportable usage records, reads pending ones, and returns them.

**Call relations**: It uses billing accounting helpers inside one workspace transaction so creation and reading of export intents are consistent.

*Call graph*: 3 external calls (workspace_tx, mint_usage_exports, read_pending_usage_exports).


##### `ExtensionContext.ack_usage_exports`  (lines 1761–1770)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered after an external receiver accepts them. Unacknowledged exports remain available for retry.

**Data flow**: It takes export records, does nothing for an empty tuple, otherwise writes acknowledgements in the current workspace transaction.

**Call relations**: It pairs with `pending_usage_exports` as the completion step of a reliable export flow.

*Call graph*: 2 external calls (workspace_tx, ack_usage_exports).


##### `ExtensionContext.transaction`  (lines 1773–1785)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Yields a database transaction for extension-owned tables and selected SDK operations. It is powerful, so the extension must still scope its own queries correctly.

**Data flow**: It opens `workspace_tx`, yields the raw async connection, commits on normal exit, and rolls back on error through the transaction manager.

**Call relations**: Memory, metronome, report digest, and other extension object code use this when they need custom table reads or writes.

*Call graph*: called by 15 (_item, _page, _entry, _page, _billing_autopay, _billing_projection, _billing_status, _entries, _task_names, record_sources (+5 more)); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 1787–1828)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, acting_member_id: UUID | None, holds_work_already_done: bool=False, as_scheduled: bool=False, stand
```

**Purpose**: Starts an internal turn in a conversation, on behalf of a stated member. It is how background work asks an agent to speak or act.

**Data flow**: It takes conversation, agent, message, idempotency key, acting member, and admission options. It requires a wired invoker, forwards the request, and returns a turn ID or `None`.

**Call relations**: Source triggers and homepage seeding call this. The actual admission logic lives behind the injected `TurnInvoker`.

*Call graph*: called by 2 (_fire_trigger, seed_homepages).


##### `ExtensionContext.tail`  (lines 1830–1839)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Subscribes to live frames from a turn until it ends. It lets side-channel work watch the turn it started or follows.

**Data flow**: It takes a turn ID and optional cursor, requires a wired tailer, and returns an async context manager for streaming frames.

**Call relations**: It delegates all live-hub behavior to the injected `TurnTailer` and fails loudly if none was provided.


##### `ExtensionContext.turn_is_terminal`  (lines 1841–1855)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final state. Missing turns are treated as terminal because there is nothing left to wait for.

**Data flow**: It takes a turn ID, reads the turn status in the current workspace, and returns true if no row exists or the status is terminal.

**Call relations**: Handlers can use this alongside `tail` to avoid speaking after a turn has already ended.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 1857–1861)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in the current workspace. It hides the workspace argument from callers.

**Data flow**: It takes a conversation ID and calls `conversation_agent_id` with this context’s workspace ID, returning an agent ID or `None`.

**Call relations**: It exposes the shared conversation-to-agent lookup as a context capability.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 1863–1898)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Reads audience and surface label facts for a batch of conversations. Member-facing listings use this to decide visibility and origin labels.

**Data flow**: It takes conversation IDs, returns an empty dictionary for none, otherwise queries matching rows in this workspace and returns `ConversationFacts` keyed by conversation ID.

**Call relations**: It parses stored audience strings into `Audience` objects and intentionally omits IDs outside the workspace.

*Call graph*: 4 external calls (__init__, select, workspace_tx, parse_audience).


##### `ExtensionContext.conversation_arrival_seq`  (lines 1900–1927)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Returns the latest member-arrival sequence number for a conversation. This is a watermark that helps background work tell whether a member spoke after it armed itself.

**Data flow**: It takes a conversation ID, queries the maximum sequence among member-admitted inbound messages in the workspace, and returns that number or zero.

**Call relations**: Invoke callers can use this value with admission guards to avoid waking on their own internal messages.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1929–1955)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads how a batch of turns ended, including terminal text when present. Status listings use this to show the result of previous runs.

**Data flow**: It takes turn IDs, returns an empty dictionary for none, otherwise queries statuses and terminal payloads in the workspace and returns `TurnOutcome` objects.

**Call relations**: Homepage seeding uses this to understand previous turn results. Missing turn IDs are simply absent from the result.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 1957–1963)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace belongs to the fleet operator. This gates operator-only details such as debugging or spend views.

**Data flow**: It reads the workspace domain through the seats helper and compares it to the operator email domain.

**Call relations**: It uses the same domain source as surface-level operator checks, keeping rendering decisions consistent.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 1965–2029)

```
async def open_conversation(self, agent_id: UUID, key: str, member_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for a workflow key and agent. This gives repeated events for the same subject one shared history and sandbox.

**Data flow**: It takes an agent ID, queue key, and optional member ID. It verifies the agent belongs to the workspace, inserts the conversation if absent, then returns the existing or new conversation ID.

**Call relations**: Source triggers and homepage seeding call this before invoking turns. It uses the extension name as the conversation surface to avoid cross-extension collisions.

*Call graph*: called by 2 (_fire_trigger, seed_homepages); 4 external calls (select, workspace_tx, conversation_audience, uuid4).


##### `ExtensionContext.agent_name`  (lines 2031–2045)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the stable name of the currently bound agent. Agent-scoped object kinds use it to build links and names.

**Data flow**: It reads the current agent scope, queries the agent row in the scoped workspace, and returns the live or archived name.

**Call relations**: It depends on `agent_current`, so it only works when an agent scope has been bound.

*Call graph*: 3 external calls (select, workspace_tx, agent_current).


##### `ExtensionContext.page_states`  (lines 2047–2076)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for named live pages in this workspace. It returns subject, revision, digest, body reference, title, and stream.

**Data flow**: It takes page IDs, returns empty for none, queries non-tombstoned pages in the workspace, and maps each to a `PageState`.

**Call relations**: This is the ungated workspace-scoped page-state read; readable variants apply source-access checks.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 2078–2116)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads page states only when the supplied reader is allowed to see the pages. It enforces source authority and page subject at the database level.

**Data flow**: It takes page IDs and a `SourceReader`, joins pages to sources, applies `_source_readable` and subject filters, and returns allowed page states.

**Call relations**: Memory object reads call this before showing page-backed memory. It reuses `_source_readable` for consistent source access.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 2118–2123)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the live source IDs readable by a particular agent/member reader. It is a compact authority check for source-backed features.

**Data flow**: It takes a `SourceReader`, queries source IDs matching `_source_readable`, and returns them as a frozen set.

**Call relations**: It shares the same source visibility rule as `source_pages` and `readable_page_states`.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 2125–2310)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers or revives a content-sync source for this workspace and grants it to an agent. It prevents duplicate syncing of the same authority while preserving clear ownership and disclosure rules.

**Data flow**: It takes backend, typed config, subject, owner, connection, and optional agent. It computes the deterministic source ID, verifies the target agent and optional connection, inserts or revives the source, checks for incompatible existing settings, grants the agent, and returns the source ID.

**Call relations**: The sample extension setup calls this. It uses `source_id`, source-grant rows, and source table locks to make registration safe under races.

*Call graph*: calls 1 internal fn (source_id); called by 1 (_setup); 5 external calls (now, model_dump, select, update, workspace_tx).


##### `ExtensionContext.grant_source`  (lines 2312–2368)

```
async def grant_source(self, source_id: UUID, *, agent_id: UUID, actor_member_id: UUID) -> None
```

**Purpose**: Grants an existing source to another agent in the workspace. This lets multiple agents read one synced feed without creating duplicate source rows.

**Data flow**: It takes a source ID, target agent, and acting member. It checks the source is live, checks the actor may grant it, verifies the agent belongs to the workspace, and inserts a grant if needed.

**Call relations**: It complements `register_source`, which grants only during registration.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.source_id`  (lines 2370–2388)

```
def source_id(self, backend: str, config: BaseModel, *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Computes the deterministic row ID that a source registration would use. Callers can compare planned sources with removed ones before deciding whether to register.

**Data flow**: It takes backend, config, and optional connection ID, dumps the config to JSON, applies the model’s non-identity fields when relevant, and returns a UUID from `source_row_id`.

**Call relations**: `register_source` calls this before touching the database. It is also useful for callers that need to reason about a source row before it exists.

*Call graph*: called by 1 (register_source); 2 external calls (model_dump, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 2390–2413)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which of the named source IDs are known removed in this workspace. It treats absence as unknown, not removed.

**Data flow**: It takes source IDs, returns an empty set for none, otherwise queries removed source rows in the workspace and returns the matching IDs.

**Call relations**: This pairs with `source_id` for extensions that avoid reviving sources a member deliberately removed.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 2415–2459)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists live registered sources in the current workspace, optionally for one backend. It is the read side of source registration.

**Data flow**: It builds a workspace-scoped query excluding removed sources, optionally filters by backend, converts rows into `SourceRecord` objects, and returns them.

**Call relations**: GBrain and sources extensions call this to discover current bindings.

*Call graph*: called by 2 (_registered_from_ext, _bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 2461–2509)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a given source reader. It applies workspace, page subject, and source authority checks.

**Data flow**: It takes a `SourceReader`, joins pages to sources, filters out tombstones, applies `_source_readable`, and returns `PageRecord` objects.

**Call relations**: It is the page-listing counterpart to `readable_page_states`, using the same access helper.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 2511–2527)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten. This triggers downstream cleanup of derived index state without deleting the row outright.

**Data flow**: It takes a page ID, updates the page to tombstoned in the current workspace, and raises an error if no live page matched.

**Call relations**: It is the single-page cleanup operation for source-page consumers.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 2529–2566)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source and tombstones its live pages. The source row remains as a historical reference and can be revived by re-registration.

**Data flow**: It takes a source ID, marks the source removed, clears claims, deletes grants, tombstones pages from that source, and raises if no live source matched.

**Call relations**: It coordinates source removal with page cleanup in one transaction so sync and indexing see a consistent change.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 2568–2594)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for live sources and their live pages together. This makes access changes visible to the page-change pipeline.

**Data flow**: It takes source IDs and a subject, updates matching live source rows, raises if none matched, then updates live pages with the new subject and timestamp.

**Call relations**: It is used when a binding’s visibility changes and all related stream rows should move together.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 2596–2669)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Updates non-identity configuration fields for live sources, optionally forcing selected sources to refetch. It protects source identity so later registrations still find the same row.

**Data flow**: It takes a mapping of source IDs to new configs and an optional refetch set. It validates inputs, locks matching live rows, recomputes each source ID to ensure it is unchanged, writes new config, and clears cursor/claim fields for refetched rows.

**Call relations**: It uses the same `source_row_id` logic as registration, ensuring rewindowing cannot silently turn one source into another.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 2671–2700)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Moves live sources’ next sync time to now and clears parking/refusal marks. This is the sanctioned way to request an on-demand resync.

**Data flow**: It takes source IDs, updates matching live rows with immediate sync time and reset refusal fields, and raises if no live source matched.

**Call relations**: The sync driver later claims these rows on its normal polling pass.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 2702–2708)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Submits a governed proposal to change an agent prompt. It records the extension as proposer instead of directly changing the agent.

**Data flow**: It takes an `AgentChange`, creates a `Governance` helper for the current workspace and extension, and returns a proposal reference.

**Call relations**: The sample extension calls this during its tick. Approval and compare-and-swap application happen in the governance layer.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 2710–2715)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns the current workspace’s trajectory corpus through the wired transcript reader. It fails clearly if transcript access was not provided.

**Data flow**: It checks that `corpus` exists, calls its `trajectories` method, and returns the resulting transcript records.

**Call relations**: The sample extension tick calls this. It delegates all transcript reading to `TrajectoryCorpus`.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 2718–2789)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: WorkspaceBlobStore | None=None, sandboxes:
```

**Purpose**: Builds the `ExtensionContext` handed to an extension or core job. It wires only the capabilities that this run is allowed to use.

**Data flow**: It takes extension name, declared credential slots, optional services, model resolver, surfaces, blob store, sandbox tools, and link settings. It validates model attribution, constructs scoped helper objects, and returns one `ExtensionContext`.

**Call relations**: This is the factory that makes core jobs and extensions receive the same capability-shaped object. It creates `ScopedStore`, `CredentialAccess`, optional `TrajectoryCorpus`, `ConversationFiles`, and `ModelAccess` as needed.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### Runtime Object Naming
Establishes runtime kind package boundaries and shared object naming/reference rules.

### `core/src/ufo/runtime/kinds/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, that means code elsewhere can refer to modules under `ufo.runtime.kinds` using normal import paths. Think of it like a label on a drawer: the label does not store the tools itself, but it lets people find and refer to the drawer reliably. Because this file is empty, it does not define any names, run setup code, or change behavior when imported. Its value is structural: without it, some Python tooling or older Python import behavior might not recognize this directory in the intended way.


### `core/src/ufo/runtime/object_name.py`

`data_model` · `cross-cutting`

This file is the naming rulebook for runtime objects. In this system, an object is identified by two main pieces: its kind, such as the category of thing it is, and its name, which is the specific item inside that category. The file makes sure both pieces use simple, predictable text formats.

This matters because object names travel through many parts of the system: they may be stored, shown in links, generated from provider data, or passed between agents. If one part accepted a strange name that another part could not read or display safely, the system could fail later in a harder-to-debug place. This file catches bad names at the boundary where they are created.

The naming rules are intentionally narrow. A kind must start with a lowercase letter and then use lowercase letters, numbers, or underscores. An object name may use lowercase letters, numbers, and hyphens, must start and end with a letter or number, and cannot be longer than 64 characters.

The main type, ObjectRef, is a frozen Pydantic model. Pydantic is a library that checks and shapes data when a model is created. “Frozen” means the reference cannot be changed after creation, like a printed mailing label. ObjectRef can also carry an optional agent name for cases where a reference crosses an agent boundary, but that agent is kept separate from the object name.

#### Function details

##### `validate_object_name`  (lines 22–30)

```
def validate_object_name(name: str) -> None
```

**Purpose**: This function checks whether a plain object name follows the shared object-name rules. It is useful before saving or accepting a caller-supplied name, so invalid names are rejected immediately instead of causing trouble later.

**Data flow**: It receives a text name. It checks the name length and compares the text against the allowed pattern. If the name is valid, nothing is returned and the caller can continue; if it is invalid, it raises InvalidName with a message explaining the rule.

**Call relations**: This is the standalone checker for places that only have a name string, not a full ObjectRef. When a write path wants to persist something under a supplied name, it can call this function first. If the rule is broken, this function creates the InvalidName error and stops the flow before bad data is stored.

*Call graph*: 1 external calls (__init__).


##### `ObjectRef.validate_kind`  (lines 46–49)

```
def validate_kind(cls, value: str) -> str
```

**Purpose**: This validator checks the kind part of an ObjectRef. It makes sure the kind has the simple shared format expected for registered object categories.

**Data flow**: It receives the proposed kind text while an ObjectRef is being built. It tests that text against the kind-name pattern. If it matches, the same value is passed through; if not, a ValueError is raised and the ObjectRef is not created.

**Call relations**: Pydantic calls this automatically when code creates an ObjectRef. It acts as the gatekeeper for the kind field before the reference becomes a trusted object identity.


##### `ObjectRef.validate_name`  (lines 53–59)

```
def validate_name(cls, value: str) -> str
```

**Purpose**: This validator checks the name part of an ObjectRef. It enforces the same object-name rules used elsewhere: allowed characters, valid start and end characters, and the maximum length.

**Data flow**: It receives the proposed object name while an ObjectRef is being created. It checks the length and the naming pattern. A valid name is returned unchanged; an invalid name causes a ValueError, preventing creation of the bad reference.

**Call relations**: Pydantic calls this automatically during ObjectRef construction. It works alongside ObjectRef.validate_kind so that a finished ObjectRef can be trusted to contain a valid kind/name pair.


##### `ObjectRef.__str__`  (lines 61–62)

```
def __str__(self) -> str
```

**Purpose**: This function gives an ObjectRef a compact human-readable form. It turns the reference into text like “kind/name,” which is useful for display, logs, and simple links.

**Data flow**: It reads the ObjectRef’s kind and name fields. It joins them with a slash. The result is a string; the ObjectRef itself is not changed.

**Call relations**: Python calls this when code asks for the ObjectRef as text, such as with str(ref) or in many formatting situations. It does not include the optional agent field, because the canonical printed object identity here is the kind and name pair.


### Turn Validation Contracts
Defines the shared interfaces for validating agent turn inputs and outputs.

### `core/src/ufo/runtime/turns/contracts.py`

`domain_logic` · `schema write, spawn validation, dispatch validation, delivery validation`

When one agent asks another agent to do something, both sides need to agree on the shape of the message. This file is that agreement layer. Think of it like a customs desk for messages: before a task or result crosses the boundary, it is checked against a declared form.

For built-in agent profiles, the form is a Pydantic model, which is a Python class that knows how to validate structured data. For workspace agents, the form may instead be raw JSON Schema, which is a standard way to describe valid JSON data. The JsonContract class wraps that raw schema so it behaves like a Pydantic model from the outside: callers can ask for its schema, validate a Python object, or validate JSON text.

The file also defines safe defaults: a task input is normally an object with a string task, and an output is normally an object with a string result. If a workspace declares its own schema, check_declared_schema rejects risky or expensive features before the schema is saved. It refuses outside references and regular-expression matching, because those could make validation reach beyond the stored data or consume too much time in the serving loop. Validation errors are converted into Pydantic’s ValidationError shape, so the rest of the system can catch and report one kind of error no matter which contract style was used.

#### Function details

##### `ValidatedJson.model_dump`  (lines 55–56)

```
def model_dump(self) -> object
```

**Purpose**: Returns the already-validated data in the same style as a Pydantic model would. This lets later code treat raw JSON Schema validation results and normal model validation results alike.

**Data flow**: It starts with a ValidatedJson object holding some data that has already passed validation. It does not transform the data. It simply gives that stored data back to the caller.

**Call relations**: JsonContract.model_validate creates ValidatedJson after a payload passes the raw JSON Schema check. Later code can call model_dump on that result just as it would on a normal Pydantic model result.


##### `ValidatedJson.model_dump_json`  (lines 58–59)

```
def model_dump_json(self) -> str
```

**Purpose**: Turns the already-validated data into a JSON text string. This is useful when the validated payload needs to be sent or stored as plain JSON.

**Data flow**: It reads the stored validated data, passes it to Python’s JSON encoder, and returns the resulting text. The object itself is not changed.

**Call relations**: ValidatedJson is produced by JsonContract.model_validate. This method gives the rest of the contract flow the same kind of JSON-output behavior that Pydantic model objects provide.

*Call graph*: 1 external calls (dumps).


##### `JsonContract.model_json_schema`  (lines 68–69)

```
def model_json_schema(self) -> dict[str, object]
```

**Purpose**: Returns the raw JSON Schema that this contract uses to judge messages. Callers use it when they need to inspect or publish the expected message shape.

**Data flow**: It reads the schema stored inside the JsonContract and returns a plain dictionary copy of it. Nothing is validated or changed at this point.

**Call relations**: JsonContract stands in for a Pydantic model class. This method supplies the schema-reading part of that shared interface, so spawn, dispatch, and delivery code can ask any contract what shape it expects.


##### `JsonContract.model_validate`  (lines 71–106)

```
def model_validate(self, data: object) -> ValidatedJson
```

**Purpose**: Checks a Python value against this contract’s JSON Schema. If the value fits, it wraps it as validated data; if it does not, it raises a Pydantic-style ValidationError so callers see the same error type they get from normal models.

**Data flow**: It receives any Python data value and the schema stored on the JsonContract. It runs the data through a JSON Schema validator with an empty reference registry, meaning the schema is not allowed to resolve outside links. If the schema contains an unresolvable reference, or if the data breaks one or more schema rules, it builds a ValidationError describing the problem. If there are no faults, it returns a ValidatedJson object containing the original data.

**Call relations**: JsonContract.model_validate_json calls this after it has parsed JSON text into Python data. More broadly, this method is the main bridge that lets raw JSON Schema contracts behave like Pydantic model contracts during agent spawning, dispatch, and result delivery.

*Call graph*: called by 1 (model_validate_json); 6 external calls (__init__, from_exception_data, Draft202012Validator, InitErrorDetails, PydanticCustomError, Registry).


##### `JsonContract.model_validate_json`  (lines 108–124)

```
def model_validate_json(self, text: str) -> ValidatedJson
```

**Purpose**: Checks JSON text against this contract. It first makes sure the text is valid JSON, then validates the parsed value against the stored JSON Schema.

**Data flow**: It receives a string. It tries to parse that string as JSON; if parsing fails, it raises a Pydantic-style ValidationError saying the JSON is invalid. If parsing succeeds, it sends the parsed data to JsonContract.model_validate and returns that method’s ValidatedJson result.

**Call relations**: This is the text-input companion to JsonContract.model_validate. It is used when a caller has JSON text rather than an already-decoded Python object, and it hands off the real schema checking to model_validate.

*Call graph*: calls 1 internal fn (model_validate); 4 external calls (from_exception_data, loads, InitErrorDetails, PydanticCustomError).


##### `freeform_result_contract`  (lines 130–137)

```
def freeform_result_contract(contract: Contract) -> bool
```

**Purpose**: Checks whether a contract is the simple built-in shape for returning a freeform string result. This helps the system recognize the common handoff format: an object with only a result string.

**Data flow**: It receives a contract. If the contract is a Pydantic model class, it looks at its declared fields and returns true only when there is exactly one field named result and that field is a string. If the contract is a raw JsonContract or anything else, it returns false.

**Call relations**: Other parts of the turn-delivery flow can use this as a quick question: “Is this the plain result-string contract?” It does not call deeper validation code; it only inspects the contract’s shape.


##### `input_contract`  (lines 140–141)

```
def input_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract used to validate an agent’s incoming task. If no custom schema is supplied, it uses the built-in task string shape; otherwise it wraps the supplied JSON Schema.

**Data flow**: It receives either a mapping that describes a JSON Schema or None. With None, it returns the TaskInput Pydantic model. With a schema, it creates and returns a JsonContract around that schema.

**Call relations**: This function is called when the system needs a uniform input contract for an agent. It hides the choice between the default Pydantic model and a workspace-declared JSON Schema so later validation code can treat both the same way.

*Call graph*: 1 external calls (__init__).


##### `output_contract`  (lines 144–145)

```
def output_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract used to validate an agent’s returned result. If no custom schema is supplied, it uses the built-in result string shape; otherwise it wraps the supplied JSON Schema.

**Data flow**: It receives either a mapping that describes a JSON Schema or None. With None, it returns the AgentResultOutput Pydantic model. With a schema, it creates and returns a JsonContract around that schema.

**Call relations**: This function is used when the system prepares to validate what an agent sends back. Like input_contract, it gives the rest of the runtime one contract-shaped object regardless of whether the contract came from built-in code or stored workspace data.

*Call graph*: 1 external calls (__init__).


##### `check_declared_schema`  (lines 148–163)

```
def check_declared_schema(candidate: Mapping[str, object], field: str) -> None
```

**Purpose**: Checks whether a user-declared input or output schema is safe and valid before it is stored. This prevents bad or risky schemas from surprising the runtime later when an agent is spawned or a message is delivered.

**Data flow**: It receives a candidate schema and the name of the field being checked. It measures the schema’s JSON size, requires the top-level type to be object, searches for refused keywords such as outside references and regular-expression patterns, and asks the JSON Schema library whether the schema itself is well-formed. If any check fails, it raises ValueError with a clear message. If all checks pass, it returns nothing and leaves the schema unchanged.

**Call relations**: This function runs at the write point, before a declared schema becomes stored contract data. It calls _refused_keyword to scan nested schema content, and it uses the JSON Schema library’s schema checker for the final correctness test.

*Call graph*: calls 1 internal fn (_refused_keyword); 2 external calls (dumps, check_schema).


##### `_refused_keyword`  (lines 166–178)

```
def _refused_keyword(node: object) -> str | None
```

**Purpose**: Searches a schema-like structure for keywords this system does not allow. These keywords are refused because they can point outside the stored schema or trigger potentially expensive pattern matching.

**Data flow**: It receives any nested value. If the value is a mapping, it checks each key and then searches each value. If the value is a list, it searches each item. It returns the first refused keyword it finds, or None if the whole structure is clean.

**Call relations**: check_declared_schema calls this helper while deciding whether a declared schema is safe to store. It is deliberately small and recursive, like looking through every drawer in a cabinet until a forbidden item is found.

*Call graph*: called by 1 (check_declared_schema).
