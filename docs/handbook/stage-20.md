# Public SDK, protocol types, and extension contracts  `stage-20` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for the whole system. It is not the startup, main work loop, or shutdown. It is the set of public “agreement papers” that let extensions, model code, browser code, and generated services fit together without guessing each other’s shapes.

The SDK re-export surface is the front counter for extension authors. It exposes approved imports for browsers, tools, credentials, subjects, jobs, manifests, search, sandboxes, and other platform features. The generated protocol definitions are the fixed message formats used when services talk across process or network boundaries, including the iMessage extension APIs. The package marker files make the main Python folders importable so these contracts can be found.

The direct files fill in key agreements. Model specs and model interfaces define what AI models can do, how requests and streamed replies look, and how billing and credentials work. The extension context gives running extensions a limited toolbox. The browser contract hides where Chrome comes from. Conversation slots define side panels beside chats. The iMessage provider defines common message-source and attachment shapes.

## Sub-stages

- [SDK re-export surface for extension authors](stage-20.1.md) `stage-20.1` — 38 files
- [Generated and wire protocol definitions](stage-20.2.md) `stage-20.2` — 24 files
- [stage-20.3](stage-20.3.md) `stage-20.3` — 6 files

## Files in this stage

### Model capability metadata
Defines the public registry of model abilities, invocation modes, billing facts, and credential requirements.

### `core/src/ufo/harness/models/spec.py`

`data_model` · `model registry setup and per-request model lookup`

This file is the project’s “model fact sheet” format. Each supported model gets a `ModelSpec`, a frozen record that says who provides the model, how to build its client, what it costs, how much context it can read, whether it can reason, whether it accepts images, and which API style it uses. Without this central record, different parts of the system might each keep their own small table of model rules. That would make mistakes likely: one part might route a model correctly, another might price it wrongly, and another might crash while preparing a request.

The file also defines `ReasoningSupport`, which describes whether a model supports extra reasoning effort and whether that feature can be used at the same time as tools. “Tools” here means callable helper functions the model can use during a turn. The code treats reasoning carefully because some providers allow it only in certain situations, and some models have reasoning on by default.

`ModelSpec` validates important facts as soon as a spec is created. For example, the knowledge cutoff must look like `YYYY-MM`, and a model cannot claim tool-compatible reasoning if it does not support reasoning at all. It also translates provider errors into project-specific errors: a rejected key becomes a credential problem, and a rate limit becomes an account capacity problem.

#### Function details

##### `ReasoningSupport.internal_effort`  (lines 37–40)

```
def internal_effort(self) -> ReasoningEffort
```

**Purpose**: This decides what reasoning setting the system should treat as the model’s internal baseline. It returns `off` when reasoning is unsupported or can be turned off, otherwise it returns the model’s required minimum effort.

**Data flow**: It reads the `ReasoningSupport` fields already stored on the object: whether reasoning is supported, whether it can be disabled, and the minimum effort. If reasoning is unavailable or optional, the output is `off`. If reasoning is mandatory, the output is the minimum allowed reasoning effort.

**Call relations**: This is a small helper on the reasoning facts attached to a model. Other model setup or request-preparation code can ask it for the safe baseline instead of repeating the same reasoning rules in multiple places.


##### `ModelSpec.__post_init__`  (lines 67–77)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a newly created model specification is internally consistent. It catches bad registry entries early, before a user request reaches a provider and fails in a harder-to-understand way.

**Data flow**: It receives the freshly created `ModelSpec` through `self` and reads fields such as `knowledge_cutoff` and `reasoning`. It verifies that the cutoff date is written as year and month, and that reasoning-related flags do not contradict each other. If everything is valid, nothing is returned; if something is wrong, it raises a `ValueError` explaining the bad model entry.

**Call relations**: This runs automatically after a `ModelSpec` dataclass is created. It acts like a gatekeeper for the model registry, making sure later code can trust the facts it reads from each spec.


##### `ModelSpec.key_rejected`  (lines 79–90)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This turns a provider’s “unauthorized” response into a clear project-level credential error. It tells the caller that the configured API key was rejected and should be replaced.

**Data flow**: It reads the model id, provider name, key environment variable name, and bring-your-own-key slot from the spec. It builds a human-readable message that points to the possible places the bad key may have come from. It returns a `CredentialValueInvalid` error object containing that message.

**Call relations**: When a provider reports that a key was rejected, caller code can ask the relevant `ModelSpec` for the correct project-specific error. This function hands off to `CredentialValueInvalid` so the rest of the system sees a consistent credential failure instead of provider-specific authentication details.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.rate_limited`  (lines 92–100)

```
def rate_limited(self) -> ModelAccountRateLimited
```

**Purpose**: This turns a provider’s “too many requests” response into a clear project-level account-capacity error. It explains that the account behind the credential cannot serve more work right now.

**Data flow**: It reads the model id and provider name from the spec. It writes those facts into a message about the account being rate limited. It returns a `ModelAccountRateLimited` error object containing that message.

**Call relations**: After the model client has used up its own retries and the provider still says the account is rate limited, caller code can use this function to produce one consistent error type. It hands off to `ModelAccountRateLimited`, hiding provider-specific status classes from the wider system.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.wire_reasoning`  (lines 102–116)

```
def wire_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort | None
```

**Purpose**: This decides what reasoning setting should actually be sent with a model request. It prevents the system from asking a model for reasoning in situations where that model or API surface does not support it.

**Data flow**: It takes the user-requested reasoning effort and the tools included in the request. It reads the model’s reasoning rules from the spec. If the model does not support reasoning, or if tools are present but the model cannot combine tools with reasoning, it returns `None`, meaning no reasoning setting should be sent. If the request asks for `off` but the model has mandatory default reasoning, it returns the model’s minimum effort. Otherwise it returns the requested setting unchanged.

**Call relations**: This function is used during request preparation, when the system is turning an internal model turn into the provider’s wire format. It keeps provider calls valid by filtering or adjusting the reasoning value before the request is sent.


### Extension execution context
Defines the scoped runtime toolbox that extensions and background jobs use to access permitted workspace data, credentials, files, model calls, conversations, and feeds.

### `core/src/ufo/runtime/ext/context.py`

`orchestration` · `cross-cutting: active whenever extension handlers, background jobs, surface code, or off-turn helpers run`

This file is the boundary between trusted core code and extension/job code. Instead of handing an extension a raw database connection, all secrets, or direct blob storage, core gives it an `ExtensionContext`: a curated set of abilities for the currently bound workspace. Think of it like a hotel key card. The guest can open their room, maybe the gym, but not every door in the building.

The file provides small capability objects. `ScopedStore` is durable key-value storage for one extension inside one workspace. `CredentialAccess` only reveals credential slots the extension declared ahead of time. `TrajectoryCorpus` gives read-only access to conversation transcripts. `ConversationFiles` and `ConversationProbes` let trusted background work write files or run bounded commands inside a conversation sandbox. `ModelAccess` allows model calls, but only after spend checks and with usage metered to the workspace.

`ExtensionContext` gathers those pieces and adds higher-level reads and writes for conversations, scheduled runs, source feeds, pages, member context, billing exports, artifact links, and governed agent-prompt proposals. Many methods check the ambient workspace instead of accepting one as an argument, which is important: code cannot accidentally ask for another tenant’s data by passing the wrong workspace id. The `context_for` factory builds this context consistently for both first-party jobs and extensions.

#### Function details

##### `ScopedStore.workspace_id`  (lines 120–121)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id that is currently bound to the running job or turn. This keeps the store tied to the active workspace instead of trusting callers to pass one.

**Data flow**: It reads the ambient workspace scope, takes its workspace id, and returns that id. Nothing is written.

**Call relations**: All `ScopedStore` reads and writes use this property so extension storage is automatically limited to the workspace the runtime has already bound.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 123–134)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved JSON value from this extension’s private key-value store. It is used when an extension needs to remember small durable state, such as a conversation mapping or progress marker.

**Data flow**: It receives a key, opens a workspace-scoped database transaction, searches for that key under the current workspace and this extension name, and returns the stored value or `None` if absent.

**Call relations**: Surface and browser extensions call this before deciding whether to create or resume their own stored state. It relies on `workspace_tx` and SQL selection to keep the read scoped.

*Call graph*: called by 4 (_start, _context, _slack_reply_progress, _own_web_chat); 2 external calls (select, workspace_tx).


##### `ScopedStore.get_many`  (lines 136–151)

```
async def get_many(self, keys: Sequence[str]) -> dict[str, JsonValue]
```

**Purpose**: Reads several named keys from the extension store in one database query. This avoids scanning the whole store when the caller already knows which keys matter.

**Data flow**: It receives a list of keys, returns an empty mapping if the list is empty, otherwise fetches matching rows for the current workspace and extension and returns a dictionary of found keys to values.

**Call relations**: It is the batched version of `ScopedStore.get`, useful for listing-style code that needs several known records without paying one database round trip per key.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 153–177)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Saves or replaces one JSON value in the extension’s private store. It uses an atomic upsert, meaning insert-or-update as one safe database action.

**Data flow**: It receives a key and value, opens a scoped transaction, inserts the row if new, or updates the existing row’s value and timestamp if it already exists. It returns nothing.

**Call relations**: Browser, Slack, and web surface code use it to persist extension state. It chooses the correct database-specific insert helper for PostgreSQL or SQLite.

*Call graph*: called by 4 (_start, _context, _hold_connect_message, _open_conversation); 1 external calls (workspace_tx).


##### `ScopedStore.put_if`  (lines 179–229)

```
async def put_if(self, key: str, value: JsonValue, expected: JsonValue | None) -> bool
```

**Purpose**: Writes a value only if the stored value is still what the caller expected. This prevents one worker from overwriting a newer update made by another worker.

**Data flow**: It receives a key, new value, and expected old value. If the expected value is `None`, it tries to insert only if the key is absent. Otherwise it locks the row, compares the stored value, updates only on a match, and returns `true` or `false` for whether the write happened.

**Call relations**: Slack progress-checkpoint code uses this compare-and-swap behavior to avoid stale reply state. It depends on database transactions and row locking for correctness.

*Call graph*: called by 2 (_checkpoint_slack_reply, _slack_reply_progress); 3 external calls (select, update, workspace_tx).


##### `ScopedStore.delete`  (lines 231–239)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes one key from this extension’s workspace-local store.

**Data flow**: It receives a key, opens a scoped database transaction, deletes the row matching the current workspace, extension, and key, and returns nothing.

**Call relations**: Slack and web surface cleanup paths call this when saved records are no longer needed.

*Call graph*: called by 2 (_drop_turn_reply_records, _open_conversation); 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 241–254)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists stored key-value pairs for this extension, optionally limited to keys with a prefix. This is useful when an extension stores related records under a naming pattern.

**Data flow**: It receives an optional prefix, fetches matching rows for the current workspace and extension ordered by key, and returns a tuple of key-value pairs.

**Call relations**: Slack cleanup and web audience code use it to find groups of extension-owned records without seeing any other extension’s keys.

*Call graph*: called by 3 (_drop_turn_reply_records, _granted_agent_ids, granted_emails); 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 267–268)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id whose credentials this credential gateway will use.

**Data flow**: It reads the currently bound workspace scope and returns its id. It does not read any secret.

**Call relations**: Credential methods use the same ambient workspace model as the store, so extensions cannot choose another workspace’s secrets.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 270–276)

```
async def get(self, slot: str) -> str
```

**Purpose**: Returns the live secret value for a declared credential slot. If the extension did not declare that slot, it fails before any secret is touched.

**Data flow**: It receives a slot name, checks it against the declared set, then asks the current workspace for that credential. It returns the plaintext credential or raises an error.

**Call relations**: Slack verification code calls this to read its configured secret. The undeclared-slot check is the important security gate.

*Call graph*: called by 1 (verifying_fingerprint); 2 external calls (__init__, ws_current).


##### `CredentialAccess.stored`  (lines 278–285)

```
async def stored(self, slot: str) -> bool
```

**Purpose**: Tells whether a credential comes from the workspace’s own stored secret rather than a platform default. This matters for billing and ownership of provider costs.

**Data flow**: It receives a slot name, verifies it was declared, then asks the current workspace whether that credential is stored there. It returns a boolean.

**Call relations**: It shares the same declared-slot guard as `CredentialAccess.get`, but answers provenance instead of the secret itself.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.rotate`  (lines 287–292)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces an existing declared credential only if its current value matches an expected value. This supports safe credential rotation after an outside provider changes a key.

**Data flow**: It receives a slot, expected old plaintext, and new plaintext. After checking declaration, it delegates to the workspace credential rotator and returns whether the replacement happened.

**Call relations**: It is the write-side companion to credential reads, still constrained by the manifest-declared slots.

*Call graph*: 2 external calls (__init__, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 325–326)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace whose transcripts this corpus reader may inspect.

**Data flow**: It reads the ambient workspace scope and returns its workspace id.

**Call relations**: The corpus methods use this to ensure transcript reads stay within the workspace bound by the runtime.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 328–335)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts from the current workspace. It is used by evaluation or learning jobs that need examples of prior conversations.

**Data flow**: It builds a query for recent conversation ids in the current workspace, then passes that query to `_read`. It returns decoded `Trajectory` records.

**Call relations**: It is a public, safe entry to `_read`, choosing conversations by recency rather than by caller-supplied ids.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus.conversations`  (lines 337–349)

```
async def conversations(self, conversation_ids: tuple[UUID, ...]) -> tuple[Trajectory, ...]
```

**Purpose**: Reads transcripts for exactly the conversation ids the caller names, while still enforcing the workspace boundary.

**Data flow**: It receives conversation ids, builds a workspace-scoped query for those ids, delegates transcript loading to `_read`, and returns only trajectories found in this workspace.

**Call relations**: It lets jobs work on known conversations, including older ones outside the recent corpus limit, without opening arbitrary blob access.

*Call graph*: calls 1 internal fn (_read); 1 external calls (select).


##### `TrajectoryCorpus._read`  (lines 351–395)

```
async def _read(self, chosen: sa.ScalarSelect[UUID]) -> tuple[Trajectory, ...]
```

**Purpose**: Loads and decodes the transcript blobs for chosen conversations and packages them with the agent prompt that produced them.

**Data flow**: It receives a database subquery of chosen conversation ids, fetches conversation-agent-prompt rows, retrieves each transcript blob, decodes it, skips missing or corrupt blobs, and returns `Trajectory` objects.

**Call relations**: `trajectories` and `conversations` both funnel through this method. It is where database rows, blob storage, transcript decoding, and prompt digesting come together.

*Call graph*: called by 2 (conversations, trajectories); 7 external calls (__init__, select, workspace_tx, log, prompt_digest, decode, transcript_key).


##### `ConversationFiles.write`  (lines 414–417)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s agent-visible workspace. The agent can then see the file on a later turn.

**Data flow**: It receives a conversation id, relative path, and file bytes, delegates the write to the conversation sandbox layer, and returns the `/workspace` path visible to the agent.

**Call relations**: This is the narrow file-writing capability exposed to off-turn code; it does not expose the whole sandbox object.


##### `ConversationFiles.prune`  (lines 419–425)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Deletes older files under a path prefix so unattended writers do not fill a conversation workspace forever.

**Data flow**: It receives a conversation id, path prefix, and keep count, then asks the sandbox layer to keep only the newest matching files. It returns nothing.

**Call relations**: It complements `write` by giving background work a safe cleanup tool.


##### `ConversationFiles.write_runtime`  (lines 427–431)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output into a named runtime area for one conversation.

**Data flow**: It receives a conversation id, runtime category, relative path, and bytes, delegates to the sandbox runtime writer, and returns the resulting path.

**Call relations**: It is like `write`, but for system-owned runtime directories rather than the ordinary agent workspace.


##### `ConversationFiles.prune_runtime`  (lines 433–441)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int=CONVERSATION_FILES_KEEP) -> None
```

**Purpose**: Bounds the number of internal runtime files kept under a category and prefix.

**Data flow**: It receives a conversation id, runtime category, prefix, and keep count, then delegates deletion of older runtime files to the sandbox layer.

**Call relations**: It is the cleanup partner to `write_runtime`.


##### `conversation_agent_id`  (lines 444–457)

```
async def conversation_agent_id(workspace_id: UUID, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation belongs to, or returns `None` if the conversation is not in the given workspace.

**Data flow**: It receives a workspace id and conversation id, queries the conversation table under that workspace, and returns the agent id if found.

**Call relations**: Probe execution and `ExtensionContext.conversation_agent` both use this shared lookup so conversation-to-agent checks are consistent.

*Call graph*: called by 2 (run, conversation_agent); 2 external calls (select, workspace_tx).


##### `ConversationProbes.run`  (lines 496–550)

```
async def run(self, conversation_id: UUID, command: str, timeout_s: int=PROBE_TIMEOUT_SECONDS, *, authority: ExecutionAuthority) -> ExecResult
```

**Purpose**: Runs a short shell command inside a conversation’s sandbox, outside the normal turn flow. It is meant for bounded probes, not long-running background programs.

**Data flow**: It receives a conversation id, command, timeout, and execution authority. It checks timeout limits, confirms the conversation and authority are valid in the current workspace, mints a short-lived probe token, opens the sandbox under the conversation’s agent, runs `bash`, and returns stdout, stderr, and exit code.

**Call relations**: It uses `conversation_agent_id` to bind the probe to the right agent, `Seats` to check authority, the token codec to authorize sandbox egress, and the sandbox session to actually run the command.

*Call graph*: calls 1 internal fn (conversation_agent_id); 8 external calls (__init__, __init__, __init__, now, workspace_tx, agent, ws_current, uuid4).


##### `trajectory_workspaces`  (lines 553–575)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for jobs that need conversation trajectories. Only workspaces with at least one turned conversation are candidates.

**Data flow**: It defines a SQL-producing helper, hands it to `owner_candidates`, and returns a `WorkspaceCandidates` object.

**Call relations**: Background dispatchers use this kind of selector before binding each workspace and running a trajectory-reading job.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 561–573)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the actual SQL query for `trajectory_workspaces`: workspaces that contain a conversation with at least one turn.

**Data flow**: It produces a select statement over workspaces with nested existence checks for conversations and turns. The result is a query, not executed here.

**Call relations**: It is enclosed inside `trajectory_workspaces` and is passed to the candidate system.

*Call graph*: 2 external calls (exists, select).


##### `seated_member_workspaces`  (lines 578–588)

```
def seated_member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for first-party jobs that need at least one active seated member.

**Data flow**: It defines a query helper selecting distinct workspaces from seated members and returns it wrapped as `WorkspaceCandidates`.

**Call relations**: Job dispatch can use this to skip empty or inactive workspaces.

*Call graph*: 1 external calls (owner_candidates).


##### `seated_member_workspaces.with_a_seated_member`  (lines 581–586)

```
def with_a_seated_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces with at least one member who has a seat.

**Data flow**: It selects distinct workspace ids from member rows where `seated_at` is set.

**Call relations**: It is the query body used by `seated_member_workspaces`.

*Call graph*: 1 external calls (select).


##### `connection_workspaces`  (lines 591–604)

```
def connection_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for jobs driven by connected accounts. It only chooses workspaces whose main agent has a connector grant.

**Data flow**: It defines a SQL helper for main-agent connector grants and returns it through `owner_candidates`.

**Call relations**: Connection-related background jobs use this to avoid running in workspaces with no relevant connected account.

*Call graph*: 1 external calls (owner_candidates).


##### `connection_workspaces.with_a_main_agent_connection`  (lines 596–602)

```
def with_a_main_agent_connection() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces where the main agent has a connector grant.

**Data flow**: It joins connector grants to agents, filters to main agents, selects distinct workspace ids, and returns the query.

**Call relations**: It is the inner query supplied by `connection_workspaces` to the candidate system.

*Call graph*: 1 external calls (select).


##### `agent_is_live`  (lines 607–620)

```
def agent_is_live(workspace_id: sa.ColumnElement[UUID], agent_id: sa.ColumnElement[UUID]) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition that is true only when an agent exists in a workspace and is not archived.

**Data flow**: It receives SQL expressions for workspace id and agent id, returns an `exists` predicate checking matching non-archived agent rows.

**Call relations**: Sweep-style jobs can use this predicate before doing work that would be wasted or refused for archived agents.

*Call graph*: 2 external calls (exists, select).


##### `awaiting_a_title`  (lines 626–642)

```
def awaiting_a_title() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition for conversations that still need an automatically summarized title.

**Data flow**: It returns a condition requiring `title_summarized` to be false and at least one completed member-originated turn to exist.

**Call relations**: `untitled_conversation_workspaces` and `ExtensionContext.conversations_awaiting_title` use this shared definition so candidate selection and per-workspace work agree.

*Call graph*: called by 2 (conversations_awaiting_title, with_an_unsummarized_title); 3 external calls (and_, exists, select).


##### `untitled_conversation_workspaces`  (lines 645–654)

```
def untitled_conversation_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for the conversation-title summarizing job.

**Data flow**: It defines a query for workspaces with conversations matching `awaiting_a_title`, wraps it in `owner_candidates`, and returns it.

**Call relations**: The titling job uses this to run only where there is title work to do.

*Call graph*: 1 external calls (owner_candidates).


##### `untitled_conversation_workspaces.with_an_unsummarized_title`  (lines 651–652)

```
def with_an_unsummarized_title() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that finds workspaces with at least one conversation awaiting a title.

**Data flow**: It selects distinct workspace ids from conversations where `awaiting_a_title` is true.

**Call relations**: It is the query body enclosed by `untitled_conversation_workspaces`.

*Call graph*: calls 1 internal fn (awaiting_a_title); 1 external calls (select).


##### `unseeded_agent_workspaces`  (lines 657–686)

```
def unseeded_agent_workspaces(extension: str, prefix: str) -> WorkspaceCandidates
```

**Purpose**: Builds a workspace-candidate selector for jobs that must do once-per-agent setup. It finds workspaces where not every agent has a matching extension-store marker.

**Data flow**: It receives an extension name and key prefix, defines a query comparing agent count to settled-key count, and returns it as workspace candidates.

**Call relations**: Seed jobs use this so they keep running until every agent has been recorded as settled.

*Call graph*: 1 external calls (owner_candidates).


##### `unseeded_agent_workspaces.with_an_unsettled_agent`  (lines 669–684)

```
def with_an_unsettled_agent() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the SQL query that detects workspaces with more agents than extension settlement keys.

**Data flow**: It builds count subqueries for agents and matching extension-store keys, then selects workspace ids where the agent count is larger.

**Call relations**: It is the internal query supplied by `unseeded_agent_workspaces`.

*Call graph*: 1 external calls (select).


##### `TurnInvoker.invoke`  (lines 703–717)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, authority: ExecutionAuthority, holds_work_already_done: bool=False, as_scheduled: bool=False, stand
```

**Purpose**: Defines the interface for starting an internal turn from background code. It is a protocol method, so this file states what callers can expect without importing the concrete turn engine.

**Data flow**: Implementations receive conversation, agent, message, idempotency key, authority, and admission options, then return the created turn id or `None` when admission is intentionally skipped.

**Call relations**: `ExtensionContext.invoke` calls through this protocol when an invoker has been wired.


##### `ModelResolver.auto_model`  (lines 727–727)

```
def auto_model(self) -> str
```

**Purpose**: Defines the interface for asking which default model background jobs should use.

**Data flow**: An implementation returns a model id string. This protocol property reads no data itself.

**Call relations**: `ModelAccess` uses it to force all requests through the deployment’s chosen default model.


##### `ModelResolver.pricing`  (lines 730–730)

```
def pricing(self) -> Pricing
```

**Purpose**: Defines the interface for reading the model pricing table.

**Data flow**: An implementation returns pricing information used to convert model usage into billable amounts.

**Call relations**: `ModelAccess.turn` uses this when recording token usage.


##### `ModelResolver.client_for`  (lines 732–732)

```
async def client_for(self, model: str) -> ResolvedModelClient
```

**Purpose**: Defines the interface for getting a model client for a specific model, using the current workspace’s credentials where appropriate.

**Data flow**: An implementation receives a model id and returns a resolved client that can stream completions.

**Call relations**: `ModelAccess.turn` calls this after spend checks pass.


##### `ModelResolver.key_slot_for`  (lines 734–734)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Defines the interface for finding which credential slot, if any, supplies a model.

**Data flow**: An implementation receives a model id and returns a credential slot name or `None`.

**Call relations**: Spend and usage-export code use this to connect model calls to workspace-owned keys.


##### `ModelResolver.provider_for`  (lines 736–736)

```
def provider_for(self, model: str) -> str
```

**Purpose**: Defines the interface for naming the provider behind a model, such as the company or backend serving it.

**Data flow**: An implementation receives a model id and returns a provider string.

**Call relations**: `ModelAccess.turn` uses this name in metrics so operators can compare model providers.


##### `ModelAccess.model`  (lines 765–767)

```
def model(self) -> str
```

**Purpose**: Returns the default model id this background model access object will use.

**Data flow**: It reads `auto_model` from the resolver and returns it.

**Call relations**: Callers can inspect this before using `complete` or `turn`; the actual methods also enforce the same model.


##### `ModelAccess.complete`  (lines 769–776)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs a model request and returns only the assistant’s text. It is the simple text-completion wrapper around the fuller tool-aware turn method.

**Data flow**: It receives a `ModelRequest`, calls `turn`, then extracts plain text from the returned assistant message and returns that string.

**Call relations**: Memory summarization code uses this convenience method when it does not need tool-call blocks. It delegates all billing and streaming work to `ModelAccess.turn`.

*Call graph*: calls 1 internal fn (turn); called by 3 (_summarize, _write, _summarize).


##### `ModelAccess.turn`  (lines 778–894)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one metered model call for background work and returns the assistant message, including reasoning and tool calls when present. It prevents off-turn work from bypassing spend limits.

**Data flow**: It receives a model request, checks workspace balance and spend rules, resolves the model client, streams text, tool-call, reasoning, and usage events, records billable usage and metrics, and returns a `Message`. On failure it records latency with an error label and re-raises.

**Call relations**: `complete` and several memory-extension writers call this. It coordinates billing gates, model client streaming, usage accounting, JSON tool-call assembly, and observability metrics.

*Call graph*: calls 1 internal fn (__init__); called by 3 (complete, _curate, _write); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_copy, loads, monotonic, workspace_tx (+3 more)).


##### `_source_readable`  (lines 958–985)

```
def _source_readable(workspace_id: UUID, reader: SourceReader) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition that says whether a source is readable by a particular agent/member context.

**Data flow**: It receives a workspace id and `SourceReader`, then returns a database predicate requiring the source to be live, in an allowed subject, and either granted to the agent or owned by the requesting member for the main agent.

**Call relations**: Page and source listing methods reuse this condition so source visibility rules are identical everywhere.

*Call graph*: called by 3 (readable_page_states, readable_source_ids, source_pages); 5 external calls (and_, exists, false, or_, select).


##### `MemberContextRecord._aware_utc`  (lines 1038–1039)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures member-context timestamps have timezone information. If a timestamp is naive, it treats it as UTC.

**Data flow**: It receives a datetime, returns it unchanged if timezone-aware, or returns a copy with UTC attached.

**Call relations**: Pydantic calls this validator when creating `MemberContextRecord` objects.

*Call graph*: 1 external calls (replace).


##### `_member_blob_text`  (lines 1045–1063)

```
async def _member_blob_text(blob: WorkspaceBlobStore, key: str) -> str
```

**Purpose**: Reads a bounded amount of text from a blob for member context. The byte limit prevents large files from being pulled fully into memory.

**Data flow**: It receives blob storage and a key, streams chunks until just over the byte limit, closes the stream if needed, decodes the bounded bytes as text, and carefully handles a cut-off multibyte character.

**Call relations**: `ExtensionContext.member_context` uses it to include text artifacts and synced page bodies in a member’s context.

*Call graph*: calls 1 internal fn (get_stream); called by 1 (member_context).


##### `ExtensionContext.workspace_id`  (lines 1092–1093)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace id for this context.

**Data flow**: It asks the contained `ScopedStore` for its workspace id and returns it.

**Call relations**: Most context methods use this property or the same store-backed id so all operations stay tied to the ambient workspace.


##### `ExtensionContext.image_preview_url`  (lines 1095–1109)

```
def image_preview_url(self, blob_key: str, size_bytes: int) -> str | None
```

**Purpose**: Creates a signed preview URL for an image blob when previews are available and safe.

**Data flow**: It receives a blob key and size, passes the deployment secret, public base URL, blob details, and workspace id to the preview URL helper, and returns a URL or `None`.

**Call relations**: Site object rendering calls this so extensions can show image previews without learning the signing secret.

*Call graph*: called by 1 (_preview_url); 1 external calls (mint_image_preview_url).


##### `ExtensionContext.artifact_link`  (lines 1111–1117)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a shared artifact.

**Data flow**: It receives a `SharedArtifact`, passes signing data and workspace id to the shared-artifact helper, and returns a link or `None`.

**Call relations**: Report digest objects use it to display downloadable files while keeping link signing in core.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_link).


##### `ExtensionContext.artifact_preview_link`  (lines 1119–1124)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed image-preview link for a shared artifact when that artifact is eligible.

**Data flow**: It receives a `SharedArtifact`, sends it with signing data and workspace id to the preview helper, and returns a URL or `None`.

**Call relations**: Report digest rendering uses it next to `artifact_link` for visual previews.

*Call graph*: called by 1 (_row); 1 external calls (shared_artifact_preview_link).


##### `ExtensionContext.scheduled_runs`  (lines 1126–1150)

```
async def scheduled_runs(self, member_id: UUID, *, agent_id: UUID | None, limit: int, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Reads recent scheduled turns visible to a member, including their final replies and shared files.

**Data flow**: It receives member id, optional agent or turn filters, limit, and optional subjects, then delegates to the surface-layer scheduled-run reader with the current workspace id.

**Call relations**: Report digest object pages call this to show scheduled work that a member is allowed to read.

*Call graph*: called by 2 (_one, _page); 1 external calls (scheduled_runs).


##### `ExtensionContext.home_url`  (lines 1152–1163)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link into the deployment’s browser portal when a public base URL and home surface are configured.

**Data flow**: It receives an optional URL fragment, checks whether portal configuration exists, trims the base URL, appends the surface path and fragment, and returns the URL or `None`.

**Call relations**: Metronome and sample extension code call this when they need to send a user back to the web home.

*Call graph*: called by 2 (_billing_portal, _hook).


##### `ExtensionContext.workspace_agents`  (lines 1165–1197)

```
async def workspace_agents(self) -> tuple[WorkspaceAgent, ...]
```

**Purpose**: Returns the workspace’s agent roster, including archived agents, for trusted first-party jobs. It is gated because it exposes broad workspace structure.

**Data flow**: It checks permission, queries agents in creation order, maps each row into `WorkspaceAgent`, and returns the tuple.

**Call relations**: The web extension’s homepage seeding job calls this to seed per-agent homepages.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.agent_visibilities`  (lines 1199–1211)

```
async def agent_visibilities(self) -> dict[UUID, AgentVisibility]
```

**Purpose**: Returns each agent’s portal visibility setting. This helps extension objects decide the minimum audience for agent-attached content.

**Data flow**: It queries agent ids and visibility values in the current workspace and returns a dictionary keyed by agent id.

**Call relations**: Unlike full roster reads, this is treated as workspace shape rather than private member context.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.agent_named`  (lines 1213–1230)

```
async def agent_named(self, name: str) -> AgentIdentity | None
```

**Purpose**: Finds a live agent by stable name and returns its id and owner. Archived agents are ignored.

**Data flow**: It receives a name, queries the current workspace for a non-archived agent with that name, and returns `AgentIdentity` or `None`.

**Call relations**: Instance-action code can use this to turn an object name like `agent/alice` into the agent identity to authorize against.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.earliest_seated_admin`  (lines 1232–1250)

```
async def earliest_seated_admin(self) -> UUID | None
```

**Purpose**: Finds a deterministic admin member to act for ownerless background work.

**Data flow**: It checks permission, queries seated admins ordered by seat time and id, and returns the first member id or `None`.

**Call relations**: The web homepage seeding job uses it when an agent has no member owner.

*Call graph*: called by 1 (seed_homepages); 2 external calls (select, workspace_tx).


##### `ExtensionContext.scheduled_member_timezone`  (lines 1252–1268)

```
async def scheduled_member_timezone(self) -> str
```

**Purpose**: Returns the timezone of the member whose authority a scheduled job is using, defaulting to UTC if unset.

**Data flow**: It extracts the member id from the stored authority, checks member-context permission, reads the member row in the current workspace, and returns the timezone string.

**Call relations**: Scheduled jobs use this when dates must be interpreted from the acting member’s local perspective.

*Call graph*: 3 external calls (select, workspace_tx, authority_member_id).


##### `ExtensionContext.member_context`  (lines 1270–1426)

```
async def member_context(self, *, since: datetime, limit: int=200, exclude_conversation_id: UUID | None=None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Builds a bounded bundle of recent information visible to the scheduled member. This can include conversations, shared artifacts, synced pages, memories, and open objectives.

**Data flow**: It checks that member context is allowed and bound to a member, validates the limit, reads recent visible turns, artifacts, and pages, streams text bodies when safe, asks `_member_extension_records` for extension-owned context, sorts everything newest first, and returns at most the limit.

**Call relations**: It is a high-level context-gathering tool for scheduled/member-personalized background work. It uses `_member_blob_text` for blob bodies and `_member_extension_records` for memory/objective records.

*Call graph*: calls 2 internal fn (_member_extension_records, _member_blob_text); 7 external calls (__init__, exists, select, workspace_tx, authority_member_id, is_text_media, readable_audiences).


##### `ExtensionContext._member_extension_records`  (lines 1428–1679)

```
async def _member_extension_records(self, member_id: UUID, audiences: tuple[str, ...], since: datetime, limit: int, exclude_conversation_id: UUID | None) -> tuple[MemberContextRecord, ...]
```

**Purpose**: Adds extension-owned memory and objective records to a member-context bundle.

**Data flow**: It receives member id, readable audience strings, time limit, result limit, and optional conversation exclusion. It queries memory and objective tables, determines which objectives are still open from their steps, events, and checks, creates stable `MemberContextRecord` entries, and returns them.

**Call relations**: `member_context` calls this after core conversation, artifact, and page reads. It keeps extension-specific tables out of the main query while returning one common record shape.

*Call graph*: called by 1 (member_context); 8 external calls (__init__, sha256, DateTime, column, or_, select, table, workspace_tx).


##### `ExtensionContext.retitle_conversation`  (lines 1681–1684)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Sets a conversation title inside the current workspace.

**Data flow**: It receives a conversation id and title, then delegates to the surface-layer retitle helper with the current workspace id.

**Call relations**: Title-writing jobs use this when they have produced a human-friendly conversation name.

*Call graph*: 1 external calls (retitle_conversation).


##### `ExtensionContext.conversations_awaiting_title`  (lines 1686–1710)

```
async def conversations_awaiting_title(self, limit: int) -> tuple[UUID, ...]
```

**Purpose**: Lists this workspace’s conversations that still need summarized titles.

**Data flow**: It receives a limit, queries conversations matching `awaiting_a_title` ordered newest first, and returns their ids.

**Call relations**: The web surface title summarizer calls this before generating and saving titles.

*Call graph*: calls 1 internal fn (awaiting_a_title); called by 1 (summarize_chat_titles); 2 external calls (select, workspace_tx).


##### `ExtensionContext.summarized_conversation_title`  (lines 1712–1717)

```
async def summarized_conversation_title(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores an automatically summarized title and marks the summarization as done.

**Data flow**: It receives a conversation id and title, then delegates to the surface helper that writes the title and completion marker.

**Call relations**: The web title summarizer calls this after summarizing so the same conversation is not repeatedly charged for title work.

*Call graph*: called by 1 (summarize_chat_titles); 1 external calls (summarize_conversation_title).


##### `ExtensionContext.pending_usage_exports`  (lines 1719–1738)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Returns this extension’s unacknowledged billing usage export records, minting fresh export intents first.

**Data flow**: It receives a floor time and limit, verifies a model key-slot resolver exists, opens a transaction, freezes eligible usage deltas for this extension, reads pending exports, and returns them.

**Call relations**: External billing exporters use this read side before delivering usage to another system.

*Call graph*: 3 external calls (workspace_tx, mint_usage_exports, read_pending_usage_exports).


##### `ExtensionContext.ack_usage_exports`  (lines 1740–1749)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as acknowledged after an external receiver accepts them.

**Data flow**: It receives export records, returns immediately if empty, otherwise opens a transaction and marks those exports acknowledged for this workspace and extension.

**Call relations**: It is the commit step after `pending_usage_exports`; unacknowledged exports remain pending and can be retried.

*Call graph*: 2 external calls (workspace_tx, ack_usage_exports).


##### `ExtensionContext.transaction`  (lines 1752–1764)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides a workspace-scoped database transaction for extension-owned tables and SDK-approved core operations. It is powerful and therefore relies on extension code to scope its own SQL correctly.

**Data flow**: It opens `workspace_tx`, yields the async database connection to the caller, commits on normal exit, and rolls back if an error leaves the context.

**Call relations**: Many extension object and job handlers use this when they need to query or update their own tables.

*Call graph*: called by 18 (tick, _entry, _page, _item, _page, _entry, _page, _billing_autopay, _billing_projection, _billing_status (+8 more)); 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 1766–1807)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str, *, authority: ExecutionAuthority, holds_work_already_done: bool=False, as_scheduled: bool=False, stand
```

**Purpose**: Starts an internal agent turn from extension or background code. It requires explicit execution authority and refuses to silently drop work if no invoker is wired.

**Data flow**: It receives conversation, agent, message, idempotency key, authority, and admission flags. It checks an invoker exists, forwards the request to it, and returns the admitted turn id or `None` when admission conditions say not to run.

**Call relations**: Source triggers and web homepage seeding call this to make agents act. The concrete turn engine sits behind the `TurnInvoker` protocol.

*Call graph*: called by 2 (_fire_trigger, seed_homepages).


##### `ExtensionContext.tail`  (lines 1809–1818)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn until it ends. This lets side-channel work watch a turn it triggered or follows.

**Data flow**: It receives a turn id and optional cursor, checks a tailer is wired, and returns the tailer’s async context manager for streaming frame ids and frames.

**Call relations**: It exposes the live hub only through an injected `TurnTailer`, so handlers cannot subscribe arbitrarily.


##### `ExtensionContext.turn_is_terminal`  (lines 1820–1834)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final status. A missing turn is treated as terminal because there is nothing left to wait for.

**Data flow**: It receives a turn id, queries its status in the current workspace, and returns true if absent or in the terminal status set.

**Call relations**: Side-channel work can call this before speaking on behalf of a turn, especially when live tailing may have missed the final frame.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.conversation_agent`  (lines 1836–1840)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent bound to a conversation in this workspace, or `None` if the conversation id does not belong here.

**Data flow**: It receives a conversation id and delegates to `conversation_agent_id` with the current workspace id.

**Call relations**: It is the context-facing wrapper around the shared conversation-agent lookup.

*Call graph*: calls 1 internal fn (conversation_agent_id).


##### `ExtensionContext.conversation_facts`  (lines 1842–1877)

```
async def conversation_facts(self, conversation_ids: tuple[UUID, ...]) -> dict[UUID, ConversationFacts]
```

**Purpose**: Reads audience and surface-label facts for a batch of conversations. These facts help member-facing listings decide visibility and display origin.

**Data flow**: It receives conversation ids, returns an empty mapping if none, otherwise queries matching conversations in the current workspace, parses their audiences, and returns `ConversationFacts` by id.

**Call relations**: Listing code can batch this instead of doing one query per row. Missing ids are treated as not visible rather than guessed.

*Call graph*: 4 external calls (__init__, select, workspace_tx, parse_audience).


##### `ExtensionContext.conversation_arrival_seq`  (lines 1879–1906)

```
async def conversation_arrival_seq(self, conversation_id: UUID) -> int
```

**Purpose**: Returns the latest member-message arrival sequence for a conversation. This acts as a watermark for deciding whether a member spoke after some work was armed.

**Data flow**: It receives a conversation id, queries the maximum member-originated inbound sequence in the current workspace, converts missing results to zero, and returns the integer.

**Call relations**: Invoke/admission logic can compare this watermark against stored values to avoid waking work because of messages that already existed.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.turn_outcomes`  (lines 1908–1934)

```
async def turn_outcomes(self, turn_ids: tuple[UUID, ...]) -> dict[UUID, TurnOutcome]
```

**Purpose**: Reads final status and terminal text for a batch of turns.

**Data flow**: It receives turn ids, returns an empty mapping if none, otherwise fetches matching turns in the current workspace and maps each to `TurnOutcome`.

**Call relations**: The web homepage seeding code uses this to render status lines for past runs.

*Call graph*: called by 1 (seed_homepages); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.is_operator_workspace`  (lines 1936–1942)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace belongs to the fleet operator. This protects operator-only display details from showing in customer workspaces.

**Data flow**: It reads the workspace domain in a transaction and compares it to the configured operator email domain. It returns a boolean.

**Call relations**: Rendering code can call this before showing operator-only links or spend details.

*Call graph*: 2 external calls (workspace_tx, workspace_domain).


##### `ExtensionContext.open_conversation`  (lines 1944–2008)

```
async def open_conversation(self, agent_id: UUID, key: str, member_id: UUID | None=None) -> UUID
```

**Purpose**: Gets or creates an extension-owned conversation for a workflow key and agent. Reusing the same key keeps repeated events for one subject in one conversation.

**Data flow**: It receives an agent id, key, and optional member id. It verifies the agent belongs to the current workspace, inserts a conversation if none exists for this extension and key, sets the audience based on whether a member is named, and returns the conversation id.

**Call relations**: Source triggers and web seeding call this before invoking turns. It creates the room; `invoke` creates the actual turn.

*Call graph*: called by 2 (_fire_trigger, seed_homepages); 4 external calls (select, workspace_tx, conversation_audience, uuid4).


##### `ExtensionContext.agent_name`  (lines 2010–2024)

```
async def agent_name(self) -> str
```

**Purpose**: Returns the stable name of the currently bound agent.

**Data flow**: It reads the current agent scope, queries that agent in the current workspace, using archived name when needed, and returns the name.

**Call relations**: Agent-scoped object kinds use this to link or label content for the agent they are running under.

*Call graph*: 3 external calls (select, workspace_tx, agent_current).


##### `ExtensionContext.page_states`  (lines 2026–2055)

```
async def page_states(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]
```

**Purpose**: Reads current state for live pages by id, without applying reader visibility rules.

**Data flow**: It receives page ids, returns an empty mapping if none, otherwise queries non-tombstoned pages in the current workspace and maps them to `PageState`.

**Call relations**: This is the direct workspace-scoped page-state read; `readable_page_states` adds source and audience checks.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_page_states`  (lines 2057–2095)

```
async def readable_page_states(self, page_ids: tuple[UUID, ...], reader: SourceReader) -> dict[UUID, PageState]
```

**Purpose**: Reads current page state only for pages a given source reader is allowed to see.

**Data flow**: It receives page ids and a `SourceReader`, joins pages to sources, filters by workspace, live page status, page subject, and `_source_readable`, then returns `PageState` objects by page id.

**Call relations**: Memory object views call this to avoid showing page data outside the reader’s source grants and subjects.

*Call graph*: calls 1 internal fn (_source_readable); called by 2 (_item, _page); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.readable_source_ids`  (lines 2097–2102)

```
async def readable_source_ids(self, reader: SourceReader) -> frozenset[UUID]
```

**Purpose**: Returns the ids of live sources readable by a particular agent/member context.

**Data flow**: It receives a `SourceReader`, applies `_source_readable` to the source table in the current workspace, and returns a frozen set of source ids.

**Call relations**: It uses the same visibility predicate as page reads, keeping source authorization consistent.

*Call graph*: calls 1 internal fn (_source_readable); 2 external calls (select, workspace_tx).


##### `ExtensionContext.register_source`  (lines 2104–2289)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None, connection_id: UUID | None=None, agent_id: UUID | None=None) -> UUID
```

**Purpose**: Registers or revives a content-sync source for the current workspace and grants an agent access to it. It prevents one source identity from silently changing owner, disclosure, or requested fields.

**Data flow**: It receives backend, typed config, subject, owner, optional connection and agent. It derives the source id, validates the target agent and connection authority, inserts or revives the source row, checks existing rows for incompatible authority or requested-field changes, inserts a source grant, and returns the source id.

**Call relations**: Sample setup code calls this to create a feed. Sync drivers later poll these source rows, while page readers use grants and subjects to decide visibility.

*Call graph*: calls 1 internal fn (source_id); called by 1 (_setup); 5 external calls (now, model_dump, select, update, workspace_tx).


##### `ExtensionContext.grant_source`  (lines 2291–2347)

```
async def grant_source(self, source_id: UUID, *, agent_id: UUID, actor_member_id: UUID) -> None
```

**Purpose**: Grants an existing source to another agent without creating a duplicate sync row.

**Data flow**: It receives a source id, target agent id, and acting member id. It verifies the source is live, checks the actor may grant it, verifies the target agent belongs to the workspace, inserts the grant if absent, and returns nothing.

**Call relations**: This complements `register_source`, which only grants during registration. It widens agent access to the feed but does not change page disclosure.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.source_id`  (lines 2349–2367)

```
def source_id(self, backend: str, config: BaseModel, *, connection_id: UUID | None=None) -> UUID
```

**Purpose**: Computes the deterministic id that `register_source` would use for a source. This lets callers compare intended sources without reading the database first.

**Data flow**: It receives backend, config, and optional connection id, dumps the config to JSON, includes only identity fields, and returns the derived UUID.

**Call relations**: `register_source` calls this before inserting. Callers can pair it with `removed_source_ids` to tell absent sources from removed ones.

*Call graph*: called by 1 (register_source); 2 external calls (model_dump, source_row_id).


##### `ExtensionContext.removed_source_ids`  (lines 2369–2392)

```
async def removed_source_ids(self, source_ids: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Reports which of a set of source ids are known removed in the current workspace.

**Data flow**: It receives source ids, returns an empty set if none, otherwise queries rows with `removed_at` set and returns their ids.

**Call relations**: This gives positive evidence of removal. Live-source listings use `sources`; this method is for callers that already have expected ids.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ExtensionContext.sources`  (lines 2394–2438)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists live registered sources in the current workspace, optionally for one backend.

**Data flow**: It builds a query for non-removed source rows, optionally filters by backend, maps rows into `SourceRecord`, and returns them ordered by backend and id.

**Call relations**: GBrain and sources-extension code call this to discover registered feeds and bindings.

*Call graph*: called by 2 (_registered_from_ext, _bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 2440–2488)

```
async def source_pages(self, reader: SourceReader) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages readable by a given source reader.

**Data flow**: It receives a `SourceReader`, joins pages to sources, filters by workspace, non-tombstone status, readable subjects, and `_source_readable`, then maps rows into `PageRecord` objects.

**Call relations**: It is the page-listing read side of the source system, combining page state with source authority rules.

*Call graph*: calls 1 internal fn (_source_readable); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 2490–2506)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten so downstream indexing can remove derived state.

**Data flow**: It receives a page id, updates the matching live page in the current workspace to `tombstone=true` with a fresh timestamp, and raises if no live page matched.

**Call relations**: This is the single-page cleanup partner to `source_pages`; page-change processing later reaps indexes.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 2508–2545)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a live source and tombstones its live pages in one transaction.

**Data flow**: It receives a source id, marks the source removed and unclaimed, deletes its grants, tombstones its pages, and raises if the source was not live in this workspace.

**Call relations**: Sync drivers stop claiming removed sources, while page-change delivery cleans up derived page/index data.

*Call graph*: 4 external calls (now, delete, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 2547–2573)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the disclosure subject for live sources and their live pages together.

**Data flow**: It receives source ids and a subject, updates matching live sources, raises if none matched, then updates non-tombstoned pages for those sources with the new subject and timestamp.

**Call relations**: This keeps source-level disclosure and page-level disclosure in sync so re-indexing sees the changed visibility.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.rewindow_sources`  (lines 2575–2648)

```
async def rewindow_sources(self, configs: Mapping[UUID, BaseModel], *, refetch: frozenset[UUID]=frozenset()) -> None
```

**Purpose**: Changes non-identity sync settings for existing live sources, optionally forcing some to refetch from scratch.

**Data flow**: It receives a mapping of source ids to new configs and an optional refetch set. It validates inputs, locks live rows, recomputes each source id to ensure the config still belongs to the same row, updates config and timestamps, and clears cursor/claim fields for refetched rows.

**Call relations**: This lets a binding adjust windows or similar parameters without changing which dataset the source represents.

*Call graph*: 5 external calls (now, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.schedule_source_sync`  (lines 2650–2679)

```
async def schedule_source_sync(self, source_ids: tuple[UUID, ...]) -> None
```

**Purpose**: Requests that live sources sync as soon as possible and clears parked/refusal state.

**Data flow**: It receives source ids, updates matching live rows so `next_sync_at` is now, clears parking fields and refusal counts, and raises if no live source matched.

**Call relations**: This is the sanctioned resync-on-demand path used by source registrars or repair flows.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 2681–2687)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Opens a governed proposal to change an agent prompt instead of directly editing it.

**Data flow**: It receives an `AgentChange`, creates a `Governance` helper for the current workspace and extension, submits the proposal, and returns its reference.

**Call relations**: The sample extension calls this to propose prompt changes. Approval and safe compare-and-swap happen in the governance subsystem.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 2689–2694)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns the current workspace’s trajectory corpus through the context. It fails clearly if no corpus reader was wired.

**Data flow**: It checks that `corpus` exists, delegates to `TrajectoryCorpus.trajectories`, and returns the resulting trajectories.

**Call relations**: The sample extension uses this as the extension-facing path to transcript data, never direct blob access.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 2697–2762)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: WorkspaceBlobStore | None=None, sandboxes:
```

**Purpose**: Builds the `ExtensionContext` object handed to an extension or core job. It wires only the capabilities that the caller’s environment supports and the extension declared.

**Data flow**: It receives extension name, declared credential slots, optional services such as index, blobs, sandboxes, invoker, model resolver, surface info, tailer, and member-context settings. It validates model attribution, constructs the capability wrappers, and returns one `ExtensionContext`.

**Call relations**: This is the factory that gives extensions their safe toolbox. It creates `ScopedStore`, `CredentialAccess`, optional corpus/files/model access, surface installation access, and other injected seams in one consistent shape.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### Core transport and model protocols
Defines stable contracts for browser connections and provider-neutral model requests, responses, tool calls, images, and reasoning blocks.

### `core/src/ufo/browser.py`

`io_transport` · `per-turn browser setup, reconnect, file transfer, and turn cleanup`

This file is a boundary, not a browser implementation. It describes how the rest of the system may borrow access to Chrome through CDP, the Chrome DevTools Protocol, which is the remote-control interface used to drive a browser. The important idea is separation: core code should not know how Chrome is launched, where it lives, or how files move in and out of it. Instead, a provider gives the system a short-lived lease, much like checking out a rental car for one trip.

A `CdpProvider` is the thing that can create or reconnect to a browser session. Each turn asks it for a `CdpLease`. The lease gives the browser-driving extension enough information to connect: a URL plus any needed headers. It also answers practical file questions. If Chrome runs inside the same sandbox as the task, a file path can be used directly. If Chrome is remote, the provider may need to upload the file and return a different location. Downloads work the same way in reverse.

The file also defines `SessionGone`, which tells callers that an old browser session token no longer points to a live session. In that case, the caller should start fresh instead of pretending it can continue on a page that disappeared.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the actual Chrome connection information for this lease. A browser-driving extension uses this to connect to Chrome through CDP.

**Data flow**: It takes no direct input beyond the lease itself. It reads whatever connection details the concrete lease represents, then returns a `CdpEndpoint`, which contains the CDP URL and any connection headers needed to use it.

**Call relations**: This is part of the lease contract. After a `CdpProvider` creates or reattaches a lease, the browser extension calls this method so it knows where to connect.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: Returns a durable text handle for this browser session. The system can save this token so a later recovered turn can try to reconnect to the same session.

**Data flow**: It takes no direct input beyond the lease. It turns the lease’s underlying session identity, such as a hosted session id or a stable URL, into a string that can be stored and passed around.

**Call relations**: This works together with `CdpProvider.reattach`. A running turn can save the token from the lease, and a later turn can hand that token back to the provider to recover the session if it still exists.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Makes a workspace file available to the Chrome connected by this lease and returns the path or location Chrome should use. This hides the difference between a local sandbox browser and a remote hosted browser.

**Data flow**: It receives the original workspace path and a `read` callback that can fetch the file bytes. If Chrome can already see the file, the concrete lease may simply return the same path. If Chrome is remote, it can call `read`, upload the bytes somewhere Chrome can reach, and return that remote location.

**Call relations**: The browser-driving extension uses this before asking Chrome to open or upload a file. The concrete provider decides whether anything must be copied based on where Chrome is running.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: Tells the browser where it should put downloaded files for this lease. This gives the rest of the system one way to request downloads even when Chrome is local in a sandbox or remote in hosted storage.

**Data flow**: It takes no direct input beyond the lease. It returns a directory or provider-specific location that Chrome can write downloads into.

**Call relations**: The browser-driving extension calls this when configuring Chrome downloads. Later, `CdpLease.fetch_download` uses the download identifier to retrieve the resulting bytes from wherever that provider stored them.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves the bytes of a completed browser download. The caller gives the download’s CDP guid, which is Chrome’s generated identifier for that download.

**Data flow**: It receives a download guid. The concrete lease looks in the place where its Chrome stored downloads, whether that is the sandbox filesystem or a remote provider’s storage, then returns the downloaded file as bytes.

**Call relations**: This follows `CdpLease.download_dir`. Chrome is first told where to save downloads, then this method is used to bring a finished download back into the system.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: Releases the browser lease when the turn is done. For a local or static browser this may do nothing, while a remote provider may use it to release a hosted browser session.

**Data flow**: It takes no direct input beyond the lease. It performs whatever cleanup the concrete lease requires and returns no value.

**Call relations**: Turn cleanup calls this after the browser is no longer needed. It is the counterpart to `CdpProvider.lease` and prevents provider-owned sessions or resources from being left open.


##### `CdpProvider.lease`  (lines 83–83)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a fresh browser lease for one turn. It is the standard way the system gets a Chrome connection without knowing where Chrome actually comes from.

**Data flow**: It may receive a sandbox if the provider needs to find Chrome inside that sandbox. It uses the provider’s own setup rules to create or locate a live browser session, then returns a `CdpLease` for that session.

**Call relations**: Turn setup calls this when no saved session is being resumed, or when reattachment is not possible. The returned lease then supplies endpoint, file, download, token, and cleanup operations.


##### `CdpProvider.reattach`  (lines 85–85)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Tries to reconnect to a browser session that was saved earlier. If the session is gone, it raises `SessionGone` so the caller can start with a fresh lease instead.

**Data flow**: It receives a saved token and may also receive the recovered turn’s sandbox. It uses those details to find the old browser session. If the session is still alive, it returns a new `CdpLease` pointing to it; if not, it signals that the session is gone.

**Call relations**: Recovery code calls this before creating a new browser. It pairs with `CdpLease.token`: one method creates the saved handle, and this method attempts to turn that handle back into a usable lease.


### `core/src/ufo/harness/models/interface.py`

`data_model` · `request construction and model streaming`

This file is the project’s common contract for talking to AI models. Different providers have different APIs, but the rest of the system should not have to care about those differences. This file gives them one shared set of message shapes and one shared client protocol.

The main pieces are small data models for conversation content: text blocks, image blocks, tool-use requests from the model, tool results sent back to the model, and reasoning blocks that some providers require to be echoed back exactly in later turns. A `ModelRequest` gathers the whole prompt: the system instruction, conversation messages, available tools, token budget, reasoning setting, cache hints, and optional forced tool choice. A `ModelClient` is the promise that any real provider client must fulfill: given a `ModelRequest`, it streams back model events such as text, tool-call fragments, reasoning blocks, and usage counts.

The file also protects the system from image-related provider limits. Images can be expensive and providers cap how many can be sent. `trim_images` keeps the newest images within per-message, per-request, and size limits, replacing older or oversized ones with a clear text note. `omit_images` does the same kind of replacement when a model cannot accept images at all. Without this file, provider clients would each invent their own message shapes and safety rules, making conversations easier to break and harder to reason about.

#### Function details

##### `ModelRequest._forced_choice_names_an_offered_tool`  (lines 157–162)

```
def _forced_choice_names_an_offered_tool(self) -> 'ModelRequest'
```

**Purpose**: This validation step makes sure that if a request forces the model to use a specific tool, that tool was actually offered in the same request. It prevents sending an impossible instruction, like telling someone to pick an item that is not on the menu.

**Data flow**: A newly built `ModelRequest` comes in with its tool list and optional `tool_choice`. If there is no forced tool choice, it is left unchanged. If there is one, the function checks the offered tool names; it returns the request when the name matches, or raises an error when it does not.

**Call relations**: This runs as part of Pydantic’s model validation when a `ModelRequest` is created. It acts before any provider client receives the request, so downstream code can trust that a forced tool choice names a real offered tool.


##### `ModelClient.complete`  (lines 216–216)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the common promise every model client must keep: accept one complete model request and stream back response events. It lets the rest of the harness call Anthropic, OpenAI, or another provider through the same shape.

**Data flow**: A `ModelRequest` goes in. A concrete client implementation sends it to its provider and yields a stream of `ModelEvent` items, such as text chunks, tool-call pieces, reasoning blocks, usage information, or start markers.

**Call relations**: This function is declared as a protocol method, meaning it is a required interface rather than working code here. Provider-specific clients implement it so orchestration code can ask for a completion without knowing which provider is underneath.


##### `trim_images`  (lines 224–255)

```
def trim_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function reduces a conversation’s inline images so the request stays within the tightest provider image limits. It keeps the newest useful images and replaces dropped ones with a short note, so the model knows something was omitted instead of silently losing context.

**Data flow**: A tuple of messages goes in. The function finds every image, chooses which ones fit the per-request and per-message count limits, then spends a request-wide image data budget from newest to oldest. Any image outside those limits is replaced with `[image omitted: over the provider image limit]`, and a new tuple of messages comes out; if nothing needs trimming, the original messages are returned.

**Call relations**: When a request may contain images, this function prepares the shared message format before provider-specific translation. It asks `_image_positions` to locate images, uses `_image_data_len` to measure kept candidates against the size budget, and calls `_trim_message` to build the final messages with text placeholders where images were removed.

*Call graph*: calls 3 internal fn (_image_data_len, _image_positions, _trim_message).


##### `omit_images`  (lines 258–266)

```
def omit_images(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function removes all images from messages for a model that only accepts text. It does not simply delete them; it inserts an explicit marker so the conversation still records that an image had been present.

**Data flow**: A tuple of messages goes in. The function finds every image position and, if any exist, returns a new tuple where each image is replaced by `[image omitted: model accepts text input only]`. If there are no images, it returns the original messages unchanged.

**Call relations**: This is used before sending a conversation to a text-only model. It relies on `_image_positions` to find both top-level and tool-result images, then delegates the actual replacement work to `_trim_message`.

*Call graph*: calls 2 internal fn (_image_positions, _trim_message).


##### `_image_data_len`  (lines 269–280)

```
def _image_data_len(messages: tuple[Message, ...], position: tuple[int, int, int | None]) -> int
```

**Purpose**: This helper measures the stored data length of one image already identified inside the message list. It is used so image trimming can respect an overall request-size budget.

**Data flow**: The full message tuple and one image position go in. The function follows that position to either a top-level image block or an image nested inside a tool result, reads the image’s base64 data string, and returns its length. If the position does not actually point to an image, it raises an error.

**Call relations**: `trim_images` calls this while deciding how many of the candidate images can fit within the request-wide image data budget. It is intentionally narrow: it trusts positions produced by `_image_positions` and only answers the size question.

*Call graph*: called by 1 (trim_images).


##### `_image_positions`  (lines 283–303)

```
def _image_positions(messages: tuple[Message, ...]) -> list[tuple[int, int, int | None]]
```

**Purpose**: This helper finds every inline image in the conversation, in oldest-to-newest order. It gives the trimming functions a map of where images live, including images nested inside tool results.

**Data flow**: A tuple of messages goes in. The function skips plain string messages, scans structured content blocks, records top-level image blocks, and also records image parts inside tuple-shaped tool results. It returns a list of positions, each describing the message, block, and optional nested part index.

**Call relations**: Both `trim_images` and `omit_images` call this first, because they need to know what images exist before deciding what to replace. Its output is later passed to `_image_data_len` for sizing and `_trim_message` for replacement.

*Call graph*: called by 2 (omit_images, trim_images).


##### `_trim_message`  (lines 306–333)

```
def _trim_message(message_index: int, message: Message, drop: set[tuple[int, int, int | None]], replacement: str) -> Message
```

**Purpose**: This helper rebuilds one message, replacing selected images with a text explanation. It preserves the rest of the message so removing images does not disturb unrelated text, tool calls, or tool results.

**Data flow**: A message index, one message, a set of image positions to drop, and a replacement string go in. If the message is plain text, it comes back unchanged. If it has structured blocks, the function walks through them, swaps matching top-level or nested images for new `TextBlock` placeholders, and returns a copied `Message` with updated content.

**Call relations**: `trim_images` and `omit_images` call this after deciding which image positions should disappear. It creates replacement text blocks and uses the message-copying behavior from the data model so the original message structure is mostly preserved while only the image content changes.

*Call graph*: called by 2 (omit_images, trim_images); 2 external calls (__init__, model_copy).


### Extension API surfaces
Marks the extension API package and defines the conversation-side portal slot shapes and provider registration contracts.

### `core/src/ufo/runtime/ext/__init__.py`

`other` · `import time`

This is a package entry file. In Python, an `__init__.py` file tells the language that a folder is an importable package, a named area of code that other files can refer to. Here, the package is for the runtime extension API. That means it is the doorway to code that describes how extensions plug into the platform.

The short module comment explains the package’s purpose: it covers both sides of the extension boundary. One side is what the platform offers to extension code, like services or helper interfaces. The other side is the schema, meaning the agreed shape or format, of what extensions can provide back to the platform.

There is no executable logic in this file. Nothing is computed, loaded, or validated here. Its value is organizational: it gives this part of the codebase a clear name and a clear meaning, like a labeled section in a handbook. Without it, imports from this package may not work the same way, and newcomers would lose a small but useful signpost explaining what this package is meant to contain.


### `core/src/ufo/runtime/ext/conversation_slots.py`

`data_model` · `conversation rendering and extension slot reads`

A conversation can have extra side information: files the agent produced, web sources it used, task progress, created sites, or scheduled automations. This file is the contract for that information. It says what each item must look like, how many may be shown, which fields are allowed, and which URLs are safe enough to display.

Most of the classes are Pydantic models, meaning they are data containers that check their contents when they are created. They are frozen, so once made they cannot be changed, and they forbid extra unknown fields. That is like accepting a completed form only if every box is expected and filled within the allowed limits.

The file also protects the browser-facing portal from risky links. Artifact, source, site, and image preview URLs must be normal HTTP or HTTPS links and must not include embedded usernames or passwords. Image preview URLs are checked even more strictly because they are drawn directly in a page.

At the end, the file defines the provider interface. A conversation slot provider has an ID, label, icon, content type, and two async callbacks: one to summarize how much content exists and one to read the full payload. This lets extensions plug new conversation-scoped panels into the runtime in a predictable, safe format.

#### Function details

##### `ImagePreview.drawable_url`  (lines 52–69)

```
def drawable_url(cls, value: str) -> str
```

**Purpose**: This validator checks that an image preview URL is safe and drawable by the portal. It rejects links that are not HTTP or HTTPS, links with embedded login details, links with fragments, backslashes, or hidden control characters.

**Data flow**: It receives the proposed image URL as text. It parses the URL, decodes escaped characters, and looks for unsafe features such as a missing host, credentials, a fragment after `#`, backslashes, or invisible control characters. If the URL passes, the same text is returned; if not, model creation fails with a clear error.

**Call relations**: Pydantic calls this automatically when an `ImagePreview` is created. Inside the check, it relies on standard URL and text helpers to split the URL, decode it, and inspect character categories before the preview is accepted for display.

*Call graph*: 3 external calls (category, unquote, urlsplit).


##### `ConversationArtifact.http_url`  (lines 85–96)

```
def http_url(cls, value: str | None) -> str | None
```

**Purpose**: This validator makes sure an artifact download or view URL is either absent or is a normal HTTP or HTTPS link without embedded credentials. It helps prevent unsafe or surprising links from being placed in the conversation portal.

**Data flow**: It receives the artifact URL, which may be `None`. If there is no URL, it leaves it as `None`. If there is a URL, it parses it and checks for an HTTP or HTTPS scheme, a real host name, and no username or password inside the link. A valid URL is returned unchanged; an invalid one stops the artifact from being accepted.

**Call relations**: Pydantic calls this during `ConversationArtifact` creation. The artifact can then be included in an `ArtifactsSlotPayload`, where the portal can trust that any attached artifact URL has passed this basic safety check.

*Call graph*: 1 external calls (urlsplit).


##### `ConversationSource.http_url`  (lines 117–126)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validator checks that a cited source link is a proper HTTP or HTTPS URL without embedded login information. It keeps the list of conversation sources limited to ordinary web links.

**Data flow**: It takes the source URL text, parses it, and verifies that it has an allowed web scheme, a host, and no username or password. If the checks pass, the original URL comes out unchanged. If any check fails, creation of the source item fails.

**Call relations**: Pydantic runs this when a `ConversationSource` is built. Accepted source items may then be grouped into a `SourcesSlotPayload` for display as supporting references in the conversation.

*Call graph*: 1 external calls (urlsplit).


##### `TasksSlotPayload.consistent_progress`  (lines 155–169)

```
def consistent_progress(self) -> 'TasksSlotPayload'
```

**Purpose**: This validator checks that the visible task list and the summary counts agree with each other. It prevents impossible task panels, such as showing more completed tasks than the reported completed total.

**Data flow**: It receives a fully built `TasksSlotPayload`. It compares `completed_count`, `total_count`, the number of visible tasks, and each visible task’s status. If the payload says it is not truncated, it also requires the visible task list to contain every task. A consistent payload is returned unchanged; inconsistent counts raise an error.

**Call relations**: Pydantic calls this after the task payload fields have been loaded. It acts as the final consistency check before a tasks slot provider can hand task progress to the portal.


##### `ConversationSite.http_url`  (lines 184–193)

```
def http_url(cls, value: str) -> str
```

**Purpose**: This validator checks that a conversation site URL is a normal HTTP or HTTPS link without embedded credentials. It protects the portal from displaying site links in unexpected or unsafe formats.

**Data flow**: It takes the site URL text, parses it, and confirms that the link uses HTTP or HTTPS, includes a host, and does not carry a username or password. A valid URL is returned as-is. An invalid URL causes the site item to be rejected.

**Call relations**: Pydantic invokes this whenever a `ConversationSite` is created. Valid site items can then be included in a `SitesSlotPayload`, while sensitive authorization fields remain excluded from serialized output.

*Call graph*: 1 external calls (urlsplit).


### iMessage source contract
Defines the provider, message, and attachment shapes used by the iMessage extension to communicate with message-like sources.

### `extensions/imessage/ufo_ext_imessage/provider.py`

`data_model` · `cross-cutting`

This file is like the plug shape for the iMessage extension. The rest of the system does not need to know whether messages come from Apple Messages, a test double, or some other backend. It only needs a provider that follows this contract.

The small data classes describe the information that moves through the extension. A MessageAttachment records an attachment’s identity, name, and size. An InboundMessage records who sent a message, which conversation it belongs to, its text, attachments, and whether it was direct. A ProviderEvent is the wrapper used when reading message history or live updates: it can carry a message, a sequence number, or the current head position. The sequence is a cursor, meaning a bookmark that lets the system resume from the right place later.

MessageProvider is a Protocol, which means “any object with these methods counts.” It sets out everything a real provider must be able to do: identify its installation, assign a phone line, read old messages, stream new ones, send text and attachments, download attachment bytes, and classify errors. Without this file, the surface layer would have no stable, predictable way to talk to different message backends.

#### Function details

##### `MessageProvider.installation_id`  (lines 37–37)

```
def installation_id(self) -> str
```

**Purpose**: This property returns the provider’s stable installation identity. The rest of the system can use it to tell one configured message backend apart from another.

**Data flow**: The caller asks the provider for its installation ID. The provider reads whatever identity it uses internally and returns it as a string. Nothing is changed.

**Call relations**: This is part of the provider contract. Other code can rely on every MessageProvider offering this identity, even though the actual source of the ID depends on the concrete provider implementation.


##### `MessageProvider.assign_line`  (lines 39–39)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This method asks the provider to connect or reserve a phone number for sending and receiving messages. The idempotency key is a safety token that helps avoid doing the same assignment twice if a request is retried.

**Data flow**: The caller provides a phone number and an idempotency key. The provider attempts the assignment using its backend rules. It returns a string result, typically an identifier or confirmation from the provider.

**Call relations**: This is a required provider capability. Higher-level setup code can call it without caring how a specific backend performs the line assignment.


##### `MessageProvider.catch_up`  (lines 41–41)

```
def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This method reads older or missed provider events after a saved sequence bookmark. It lets the extension recover messages that arrived while it was offline or not listening.

**Data flow**: The caller passes the last known sequence number, or nothing if there is no saved bookmark. The provider looks for later events and yields them one at a time as ProviderEvent objects. The output is an asynchronous stream, so events can arrive gradually instead of all at once.

**Call relations**: ImessageSurface._catch_up calls this when the surface needs to synchronize past messages. The provider supplies events, and the surface consumes them to bring its local view up to date.

*Call graph*: called by 1 (_catch_up).


##### `MessageProvider.subscribe`  (lines 43–43)

```
def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This method starts listening for new live provider events. The ready event lets the provider signal when the live stream is actually connected and safe to rely on.

**Data flow**: The caller gives an asyncio.Event, which is a small asynchronous signal flag. The provider connects to its live message source, sets the ready flag when listening has started, and then yields ProviderEvent objects as new activity appears.

**Call relations**: ImessageSurface._pump_live calls this during live message pumping. The provider becomes the source of fresh events, and the surface reacts to each event as it arrives.

*Call graph*: called by 1 (_pump_live).


##### `MessageProvider.send_text`  (lines 45–45)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This method sends a plain text message into an existing conversation. The idempotency key helps make retries safe, so the same logical send request should not accidentally create duplicate messages.

**Data flow**: The caller passes a conversation ID, the text to send, and an idempotency key. The provider sends the text through its backend. It returns a string, usually the provider’s ID for the sent message.

**Call relations**: ImessageSurface._prove calls this when it needs to send a proof or confirmation message. The surface decides what should be sent, and the provider performs the actual backend send.

*Call graph*: called by 1 (_prove).


##### `MessageProvider.send_attachment`  (lines 47–53)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This method sends a file-like attachment into a conversation. It is used when the extension needs to deliver something more than text, such as a contact card.

**Data flow**: The caller provides the conversation ID, a filename, the raw file bytes, and an idempotency key. The provider uploads or transmits the attachment through its backend. It returns a string identifier for the sent attachment or message.

**Call relations**: ImessageSurface._send_contact_card calls this when it has prepared attachment data to send. The provider takes over the transport-specific work and reports the resulting ID back.

*Call graph*: called by 1 (_send_contact_card).


##### `MessageProvider.download_attachment`  (lines 55–55)

```
def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This method downloads the contents of an attachment by its provider attachment ID. It returns the data in pieces, which is useful for large files because the whole file does not have to sit in memory at once.

**Data flow**: The caller passes an attachment ID. The provider finds the attachment and yields chunks of bytes asynchronously. The caller receives those chunks and can write or process them as they arrive.

**Call relations**: ImessageSurface._downloaded_files calls this when it needs to turn attachment references from messages into actual file data. The provider supplies the bytes, while the surface decides what to do with the downloaded content.

*Call graph*: called by 1 (_downloaded_files).


##### `MessageProvider.invalidate`  (lines 57–57)

```
async def invalidate(self) -> None
```

**Purpose**: This method tells the provider to shut down or discard its current usable state. It is a cleanup or reset hook for cases where the provider should no longer be trusted as-is.

**Data flow**: The caller invokes the method with no extra data. The provider performs whatever invalidation its implementation requires, such as closing sessions or marking credentials stale. It returns nothing.

**Call relations**: This is part of the provider contract for lifecycle cleanup. Concrete providers decide what invalidation means for their own backend.


##### `MessageProvider.invalid_cursor`  (lines 59–59)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This method answers whether an error means the saved sequence cursor is no longer valid. A cursor is a bookmark into the event stream; if it becomes invalid, the system may need to resynchronize differently.

**Data flow**: The caller passes an exception. The provider inspects it using backend-specific knowledge and returns true or false. Nothing else is changed.

**Call relations**: This gives higher-level code a provider-neutral way to interpret cursor failures. Each concrete provider can recognize its own error shapes while exposing a simple yes-or-no answer.


##### `MessageProvider.external_error`  (lines 61–61)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This method answers whether an exception came from the outside message service rather than from the extension’s own logic. That distinction helps the surface decide how to report or recover from failures.

**Data flow**: The caller passes an exception. The provider checks whether it matches the backend’s known external failure types and returns true or false. The exception is not modified.

**Call relations**: ImessageSurface._consume_connected, ImessageSurface._downloaded_files, and ImessageSurface._send_contact_card call this when something goes wrong during event consumption, attachment download, or attachment sending. The provider helps the surface classify the failure in a backend-aware way.

*Call graph*: called by 3 (_consume_connected, _downloaded_files, _send_contact_card).


##### `MessageProvider.error_code`  (lines 63–63)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This method converts a provider-specific exception into a short error code. That gives the rest of the system a compact label for logging, reporting, or choosing a response.

**Data flow**: The caller passes an exception. The provider examines it and returns a string code that summarizes the kind of failure. Nothing else is changed.

**Call relations**: ImessageSurface._send_contact_card calls this when sending an attachment fails and it needs a provider-specific error label. The provider translates the raw exception into a simpler code the surface can use.

*Call graph*: called by 1 (_send_contact_card).

## 📊 State Registers Touched

- `reg-pack-composition` — The selected bundle of built-in extensions, prompts, skills, jobs, and setup steps for this deployment.
- `reg-extension-registry` — The live catalog of installed extensions and the capabilities each one has registered.
- `reg-model-catalog` — The shared list of available AI models, their abilities, providers, prices, and credential needs.
- `reg-host-environment` — The assembled per-turn world given to the agent: prompts, skills, files, model choice, tools, and extension context.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-browser-sessions` — The active or reusable Chrome browser sessions, tabs, downloads, and remote-control connections used by agents.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-surface-routing` — The saved routing state that maps web, Slack, iMessage, terminal, and other surfaces to workspaces and agents.
- `reg-artifact-publication` — The shared state for files, previews, signed downloads, hosted sites, app pages, and published outputs.
- `reg-extension-store` — The per-workspace storage area where extensions keep their own durable settings and small JSON records.
- `reg-conversation-slots-ui` — The shared side-panel and workspace UI state for artifacts, sources, tasks, sites, automations, and app home screens.
