# Tool dispatch, sandboxed execution, and workspace side effects  `stage-10`

This stage is where the agent’s requested actions become real work. During the main conversation loop, the model may ask to use a tool, such as reading a file, running a command, searching the web, or opening a browser. The tool registry is the catalog: it defines each tool, its allowed inputs, and how to find it by name. The tool context is the safety wrapper: it tells the tool what workspace, credentials, accounts, cleanup steps, and helper agents it may use.

The built-in tools connect model requests to the project’s safe workspace, file store, database, user prompts, secrets, account connections, and subagents. Research tools add web search and page fetching through configured search services.

Around these tools are larger workshops. The sandbox stage runs commands, limits file access, tracks changes, and controls network access. The document stage repairs, reads, annotates, and rebuilds office files and PDFs. The browser stage drives real or hosted browsers, reads pages, and performs clicks, typing, downloads, and screenshots safely. Together, these parts turn tool calls into controlled side effects and return results to the conversation.

## Sub-stages

- [Sandbox workspace, command execution, file access, and egress proxying](stage-10.1.md) `stage-10.1` — 14 files
- [Document, office, PDF, and skill helper execution](stage-10.2.md) `stage-10.2` — 19 files
- [Browser automation and remote browser sessions](stage-10.3.md) `stage-10.3` — 26 files

## Files in this stage

### Tool framework
Package-level orientation, tool definition lookup, and the safe execution context establish how tool calls are exposed and constrained.

### `core/src/ufo/tools/__init__.py`

`other` · `cross-cutting`

This is the package entry file for the `ufo.tools` area of the project. A package entry file is like a label on a folder: it tells Python that the folder is a usable module, and it gives readers a quick clue about what they will find inside. Here, the docstring says this package contains the pieces related to “tools”: a registry, which is likely the catalog of available tools; a handler context, which is likely the information passed around while a tool is being used; and the built-in tool set, which are the tools that come with the project by default. There is no executable code in this file, so it does not perform work at runtime beyond allowing imports through this package. Its main value is organization. Without it, nearby modules could still exist, but this folder would not have this clear package identity and simple documentation point.


### `core/src/ufo/tools/registry.py`

`data_model` · `startup and request handling`

A “tool” here is an action the model can ask the system to run, such as reading a page, searching, writing somewhere, or calling an outside service. This file gives each tool a clear definition: its name, a human-readable description, the expected input data, and the async function that actually runs it. It also records important safety labels. For example, an “untrusted” tool may return text controlled by someone else, so the engine must treat that result as data, not as instructions. A “side_effecting” tool changes something outside the model, such as sending a message or making a write, so it may need an idempotency key, which is a repeat-safe tracking key that helps avoid doing the same external action twice.

The file has two main parts. `ToolDef` is one tool’s entry in the catalog. Its `schema()` method turns the tool’s input model into the wire format sent to the model client, and it automatically adds a shared `requested_by` field used to tie a call to the message that authorized it. `ToolRegistry` is the frozen catalog of all tools. When it is created, it checks for duplicate tool names and prevents tools from defining their own `requested_by` field, because that name is reserved. Later, the engine can ask the registry for all schemas or look up one tool by name during dispatch.

#### Function details

##### `ToolDef.schema`  (lines 53–65)

```
def schema(self) -> ToolSchema
```

**Purpose**: Builds the public description of one tool that can be sent to the model client. It includes the tool name, description, and the expected input fields, plus a standard `requested_by` field used for authority tracking.

**Data flow**: It starts with the Pydantic input model attached to the tool, asks that model for its JSON schema, then adds a `requested_by` property to the schema. It returns a `ToolSchema` object containing the tool’s name, description, and completed input schema.

**Call relations**: When the system needs to advertise available tools, this method is the per-tool translator from internal Python definition to the wire schema. It creates a `ToolSchema`, which is the object other parts of the model interface can pass along.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 72–81)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the tool catalog is valid as soon as it is created. It prevents two tools from having the same name and prevents any tool input model from using the reserved `requested_by` field.

**Data flow**: It reads the tuple of tools stored in the registry, collects their names, and looks for repeats. It also checks each tool’s input fields for the reserved name. If everything is clean, nothing changes; if there is a problem, it raises a clear error before the registry can be used.

**Call relations**: This runs automatically after a `ToolRegistry` is constructed. It acts like an entry inspection at the door: bad tool catalogs fail early, before the engine later tries to dispatch calls by name.


##### `ToolRegistry.schemas`  (lines 83–84)

```
def schemas(self) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the model-facing schemas for every registered tool. This is how the catalog becomes a list of tool descriptions the model client can understand.

**Data flow**: It reads the registry’s tuple of `ToolDef` objects, asks each one to produce its schema, and returns the results as a tuple. It does not change the registry.

**Call relations**: This is the registry-wide version of `ToolDef.schema`. Instead of translating one tool, it walks the whole catalog so the rest of the system can present all available tools together.


##### `ToolRegistry.get`  (lines 86–90)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the registered tool with a given name. The engine uses this when the model asks to call a tool and the system needs the matching definition and handler.

**Data flow**: It receives a tool name, scans the registry’s tools one by one, and returns the first `ToolDef` whose name matches. If no tool has that name, it raises a `KeyError` so the unknown call fails loudly instead of silently doing the wrong thing.

**Call relations**: During tool dispatch, `core/src/ufo/loop/engine._dispatch_segments` calls this lookup to turn a model-requested tool name into the actual tool definition. Once found, that definition tells the engine which handler to run and what safety flags apply.

*Call graph*: called by 1 (_dispatch_segments).


### `core/src/ufo/tools/context.py`

`domain_logic` · `tool execution during a turn, plus turn-end cleanup`

A tool in this system should not be able to reach everything directly. This file is the controlled doorway. `ToolContext` is the bundle of permissions and services a tool receives: the sandbox for files and commands, the blob store for artifacts, the current turn and agent, the audience that controls what may be shared, and helpers for credentials, connectors, skills, browser access, search, and spawned subagents. Think of it like a visitor badge: it does not just identify the visitor, it also says which rooms they may enter.

The file also defines the shape of tool output. A tool can return text or an image, and can mark output as an error or as untrusted data. Untrusted data matters because web pages or third-party systems might contain text that looks like instructions; the engine must treat that as data, not as orders.

Several small exception classes turn confusing setup failures into clear messages, especially when a tool asks for a subagent profile or agent name that does not exist. `TurnCleanup` is a per-turn cleanup list for things like browser connections, so resources are closed even if the turn fails. The rest of `ToolContext` enforces important rules: which member is acting, what audience can read or write information, whether the speaker is an admin, which connector account may be used, and how extension credentials are authorized.

#### Function details

##### `UnknownSubagentProfile.__init__`  (lines 89–94)

```
def __init__(self, requested: str, registered: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when code asks for a subagent profile that is not registered. It includes both the bad name and the valid names so the caller can recover or show a useful message.

**Data flow**: It receives the requested profile name and the list of registered profile names. It turns those into a human-readable exception message and stores both pieces of information on the exception object for later logging or reporting.

**Call relations**: The subagent registry calls this when a profile lookup fails. Instead of letting a plain missing-key error escape, this function gives the spawning flow enough detail to tell the model or operator what names are actually valid.

*Call graph*: called by 1 (get).


##### `UnknownSpawnTarget.__init__`  (lines 101–108)

```
def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when a spawn request names neither a known subagent profile nor a known workspace agent. This helps a caller retry with a real target instead of failing mysteriously.

**Data flow**: It receives the requested target name, the known profile names, and the known agent names. It formats those into an exception message and keeps the three values on the exception object.

**Call relations**: The subagent spawning resolver calls this when it cannot match a requested target. The error travels back through the spawn path with enough context to explain what can be spawned.

*Call graph*: called by 1 (_resolve).


##### `AmbiguousSpawnTarget.__init__`  (lines 115–120)

```
def __init__(self, requested: str) -> None
```

**Purpose**: Builds an error when a spawn target name matches both a profile and an agent. It tells the caller to use a qualified name, such as `profile:name` or `agent:name`, to remove the confusion.

**Data flow**: It receives the ambiguous name. It creates an exception message that explains the conflict and stores the name on the exception object.

**Call relations**: The spawn target resolver calls this when a bare name is not specific enough. This protects the system from accidentally spawning the wrong kind of child task.

*Call graph*: called by 1 (_resolve).


##### `Spawn.__call__`  (lines 176–184)

```
async def __call__(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='') -> SpawnResult
```

**Purpose**: Describes the callable interface used to delegate work to a child turn. A tool uses it to ask a named subagent profile or workspace agent to do a typed subtask.

**Data flow**: The caller provides a target name, an input payload, and options such as whether the child should run in the background and whether a deduplication key should prevent duplicate work. An implementation validates the input, creates or reconnects to the child turn, and returns a `SpawnResult` describing the child and, when available, its result.

**Call relations**: This is a protocol, meaning this file defines the shape of the operation but another part of the system supplies the actual implementation. `ToolContext` carries a `spawn` object with this shape so tool handlers can delegate work without knowing the internals of the subagent runner.


##### `SubagentControl.result`  (lines 194–194)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Describes how a tool asks for the final result of an already-spawned background subagent. It is used when the parent wants to inspect a child turn after it has been started.

**Data flow**: The caller supplies a child turn id. An implementation looks up that child and returns a `SpawnResult` containing its terminal state and validated output if it is finished.

**Call relations**: This is part of the `SubagentControl` protocol carried on `ToolContext`. The actual subagent workflow implements it so tools can talk to background children through a narrow, controlled interface.


##### `SubagentControl.wait`  (lines 196–196)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes how a tool waits for one or more background subagents for a bounded period inside a tool call. It lets a parent check which children have finished without taking over the whole run loop.

**Data flow**: The caller provides child turn ids. An implementation waits according to its own rules and returns a tuple of `SubagentStatus` records showing each child’s current terminal status and text.

**Call relations**: This protocol method belongs to the subagent control surface attached to `ToolContext`. It gives tools a standard way to pause for child work while the real waiting logic lives in the subagent subsystem.


##### `SubagentControl.cancel`  (lines 198–198)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes how a tool asks the system to stop a running background subagent. It is the controlled way to end delegated work that is no longer needed.

**Data flow**: The caller supplies a child turn id. An implementation cancels or marks the child as stopped and returns a `SubagentStatus` describing what happened.

**Call relations**: This is another method on the subagent control protocol. Tool handlers call through the context, while the subagent workflow decides how cancellation is performed safely.


##### `SubagentControl.message`  (lines 200–202)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Describes how a tool sends a follow-up message to an already-spawned child turn. The deduplication key helps avoid sending the same follow-up twice after a retry or crash recovery.

**Data flow**: The caller gives a child turn id, message text, a deduplication key, and whether the child will deliver its own result. An implementation admits the message to the child and returns a `SubagentStatus` showing the child’s response state.

**Call relations**: This protocol method is supplied by the subagent workflow and exposed through `ToolContext`. It lets parent tools continue a child conversation without bypassing the turn and idempotency rules.


##### `TurnCleanup.register`  (lines 216–217)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous cleanup action to be run when the turn ends. Tools use it for resources such as browser sessions or leases that must not outlive the turn.

**Data flow**: It receives an async close function and appends it to the cleanup list. Nothing is returned, but the cleanup registry now remembers that closer for later.

**Call relations**: Tool code calls this after opening a per-turn resource. Later, the turn loop drains the registry so those resources are closed even if the turn completed, failed, or was cancelled.


##### `TurnCleanup.drain`  (lines 219–225)

```
async def drain(self) -> None
```

**Purpose**: Runs all registered cleanup actions and logs failures instead of letting one bad cleanup stop the rest. It closes resources in reverse order, like packing away the last thing opened first.

**Data flow**: It reads the internal list of close functions. One by one, it removes the last closer, awaits it, and if that closer raises an error, it writes a log entry and continues with the remaining closers.

**Call relations**: The turn-end flow calls this after tools have had a chance to register resources. It hands cleanup failures to the observability logger so operators can see leaks or close errors without breaking the rest of teardown.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 266–274)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Figures out which member’s authority this tool call may use. It prefers the live speaker, and falls back to the member the turn is acting on behalf of, such as for scheduled work.

**Data flow**: It reads `speaker_member_id` and `on_behalf_of_member_id` from the context. It returns the speaker id if present; otherwise it returns the on-behalf-of id, or `None` if there is no member.

**Call relations**: Other permission checks in this file use this property as their starting point. It keeps tools from guessing whose grants or private access should apply.


##### `ToolContext.effective_audience`  (lines 277–287)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides the exact audience to stamp on a write. This prevents information from a private or shared conversation from being stored in a way that leaks into the wrong place later.

**Data flow**: It reads the current audience and the acting member. If there is no acting member, or the conversation is not the workspace-shared audience, it returns the conversation audience as-is. If the shared audience is being used by a specific member, it creates and returns that member’s conversation audience.

**Call relations**: When write operations need to know where information belongs, they use this property. It calls the audience helper only in the special shared-conversation case where the requester’s own audience must be derived.

*Call graph*: 1 external calls (conversation_audience).


##### `ToolContext.read_subjects`  (lines 290–299)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes the set of subjects this tool call may read. A subject is a label for a memory or information area, such as the conversation’s area or a member’s private area.

**Data flow**: It starts with the subjects allowed by the current audience. If there is an acting member, it adds that member’s private subject. It returns the combined set as an immutable collection.

**Call relations**: Source and memory access use this property to avoid reading beyond the conversation and requester’s allowed scope. It relies on audience and member-subject helper functions to convert identities into readable subject labels.

*Call graph*: 2 external calls (audience_subjects, member_subject).


##### `ToolContext.source_reader`  (lines 301–311)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Builds the small identity object used when reading synced source pages. It says which agent is asking, which live member is requesting, and which subjects may be read.

**Data flow**: It reads the current turn’s agent id, the live speaker member id, and the computed readable subjects. It packages those into a `SourceReader` object and returns it.

**Call relations**: Memory and source extensions call this before fetching pages or objects. It gives those extensions a consistent permission envelope instead of making each one recalculate agent, member, and audience rules.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 313–322)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images against this turn’s accounting ledger. This matters because some image providers charge per image rather than through the normal language-model token accounting.

**Data flow**: It receives the model name, number of images, and cost in micro-dollars. It opens a workspace database transaction and writes an image-usage record tied to the workspace, turn, and model.

**Call relations**: The OpenRouter image extension calls this after it knows the provider’s charge. This function hands the database write to the core accounting helper so extension spending is still billed in the same central ledger.

*Call graph*: called by 1 (generate); 2 external calls (record_image_usage, workspace_tx).


##### `ToolContext.meter_videos`  (lines 324–332)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos against this turn’s accounting ledger. This covers providers that charge by generated video output instead of normal text tokens.

**Data flow**: It receives the model name, number of videos, and cost in micro-dollars. It opens a workspace database transaction and writes a video-usage record tied to the workspace, turn, and model.

**Call relations**: The OpenRouter video extension calls this after it prices a video generation call. This keeps provider-specific video costs connected to the core accounting system.

*Call graph*: called by 1 (generate); 2 external calls (record_video_usage, workspace_tx).


##### `ToolContext.speaker_is_admin`  (lines 334–344)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live requesting speaker is a workspace administrator. If there is no live speaker, it returns false so background work cannot silently use admin power.

**Data flow**: It reads `speaker_member_id`. If no speaker exists, it returns `False`; otherwise it opens a workspace database transaction and asks the seats subsystem whether that member is an admin in the current workspace.

**Call relations**: Many workspace-wide operations call this before allowing changes or sensitive reads. Credential authorization also uses it so only an actual live admin can approve storing secret material.

*Call graph*: called by 19 (apply, delete, apply, delete, get, list, status, request_credentials_handler, _credential_authorization, connect_github (+9 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 346–357)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether the current turn’s agent is the workspace’s main agent. Some member and workspace operations are only visible or allowed for that main agent.

**Data flow**: It opens a workspace database transaction, queries the agent table for the current agent in the current workspace, reads the `is_main` flag, and returns it as a boolean.

**Call relations**: Member, workspace, and web-audience code call this when deciding what the current agent may see or change. It uses a direct database query because the answer must reflect the stored workspace state.

*Call graph*: called by 6 (add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 359–361)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for storing or using an extension credential slot. It returns a sealed authorization token that can later be opened or fulfilled.

**Data flow**: It receives a credential slot name and payload. It first runs the shared credential-authorization checks, then asks the credential request service to create an authorization for this workspace, member, slot, and payload, returning the sealed string.

**Call relations**: Coding and Slack extension flows call this when they need an admin-approved credential action, such as connecting GitHub or building an OAuth link. It delegates all safety checks to `_credential_authorization` before creating the request.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 363–365)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens and verifies a sealed credential authorization token. This lets a later step confirm that the token really belongs to this workspace, member, and credential slot.

**Data flow**: It receives a slot name and sealed token. It runs the shared authorization checks, then asks the credential request service to open the token and returns the stored payload.

**Call relations**: This is the read-back side of the credential authorization flow. It uses the same `_credential_authorization` guard as the begin and fulfill steps so a token cannot be opened in the wrong context.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext.fulfill_credential_authorization`  (lines 367–372)

```
async def fulfill_credential_authorization(self, slot: str, sealed: str, plaintext: str) -> None
```

**Purpose**: Completes a credential authorization by verifying the sealed token and storing the plaintext secret. This is the point where an approved credential becomes available to the workspace.

**Data flow**: It receives the slot name, sealed token, and plaintext secret. It checks the authorization, opens the token to prove it is valid, then writes the plaintext credential into the current workspace’s secret storage.

**Call relations**: This function uses `_credential_authorization` for policy checks and then uses the current workspace object to store the credential. It is deliberately strict because it handles real secrets.

*Call graph*: calls 1 internal fn (_credential_authorization); 1 external calls (ws_current).


##### `ToolContext._credential_authorization`  (lines 374–383)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the shared safety checks for credential authorization. It makes sure there is a live speaker, the extension declared the credential slot, secret storage is configured, and the speaker is an admin.

**Data flow**: It reads the speaker id, extension metadata, configured credential request service, and admin status. If any requirement is missing, it raises a clear error; otherwise it returns the credential request service and the speaker member id.

**Call relations**: The begin, open, and fulfill credential methods all call this before doing their specific work. It centralizes the rules so every credential path enforces the same admin and extension-slot checks.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 3 (begin_credential_authorization, fulfill_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 385–394)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the external broker account id that a connector tool may use. It is a convenience wrapper for callers that only need the account id, not the full connection record.

**Data flow**: It receives a connector provider name and optionally a specific account id. It resolves the permitted connection through `connector_connection` and returns that connection’s account id.

**Call relations**: Connector execution tools call this before asking the broker to run an external action. It relies on `connector_connection` so all account-selection and permission rules stay in one place.

*Call graph*: calls 1 internal fn (connector_connection); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_connection`  (lines 396–433)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Chooses the exact connector connection this turn is allowed to use. It enforces private-by-default behavior and prevents a tool from accidentally using another member’s account.

**Data flow**: It receives a provider name and optionally an account id. It fetches private and shared grant tiers, then either finds the requested account or chooses a single allowed account, preferring the acting member’s private grants over shared ones. It returns a `ConnectorConnection` with the connection id, external account id, and owner member id, or raises a clear error if no safe choice exists.

**Call relations**: The simpler `connector_account` method calls this, and source tools call it when they need to persist the precise connection generation. It depends on `_connector_account_tiers` to get the allowed grants before applying selection rules.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


##### `ToolContext.connector_accounts`  (lines 435–443)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists the connector account ids this turn may use for a provider. This is useful when a caller needs to show or choose among available accounts.

**Data flow**: It receives a provider name, gets the private and shared grant tiers, combines their account ids, removes duplicates, sorts them, and returns them as a tuple.

**Call relations**: Source tools call this when resolving which account should be used. It shares the same `_connector_account_tiers` permission logic as `connector_connection`, so listing accounts and selecting one follow the same access rules.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 445–464)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: Splits active connector grants into two allowed groups: the acting member’s private accounts and accounts shared with the agent. This is the core permission filter for connector access.

**Data flow**: It checks that the grant subsystem is available, reads the acting member id, then loads active grants. It filters them by provider, ownership, and sharing status, sorts each group by account id, and returns the private and shared lists.

**Call relations**: `connector_accounts` and `connector_connection` both call this before listing or choosing accounts. If grants are not configured, it raises `ConnectUnavailable`, making connector tools fail clearly instead of pretending no accounts exist.

*Call graph*: called by 2 (connector_accounts, connector_connection); 1 external calls (__init__).


### Tool implementations
Built-in and research extension tools translate model requests into sandboxed workspace, search, account, sharing, and subagent side effects.

### `core/src/ufo/tools/builtins.py`

`domain_logic` · `tool execution during an agent turn`

This file is the agent's basic toolbox. Without it, the agent could talk, but it could not safely inspect files, run commands, create outputs, hand files back to a user, ask for missing information, or delegate work to child agents.

A key idea in this file is containment. File and shell work goes through the sandbox, which is an isolated workspace container. Think of it like a workshop behind a glass wall: tools can operate inside it, but only controlled results come back out. Reading, editing, globbing, and grepping use an in-sandbox helper called sbxfs, so large files are not pulled into the host process just to inspect them. The file also remembers which paths were read during the turn, so edits and overwrites can be refused if the agent has not looked at the file first.

Sharing files is treated as a special, safer exit door. The file is measured inside the sandbox, uploaded or streamed to the artifact store, recorded in the database, and returned as a time-limited download link. Spawning and messaging subagents use the context's subagent workflow. User-facing pauses, such as asking a question, connecting an account, or requesting secrets, return structured instructions that the chat surface can render privately or in the next user message.

#### Function details

##### `_bounded_file_path`  (lines 183–186)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the limited JSON result space used by file tools. This prevents an unusually huge path string from breaking the tool result format.

**Data flow**: It receives a path string, measures how large that path becomes when encoded as JSON, and either returns the original path unchanged or raises a validation error if it is too large.

**Call relations**: This is used as a validator for file path inputs on write and edit requests. It runs before the main file tool handlers, so bad paths are rejected early.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 379–405)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandbox and turns the result into a tool response the agent can read. It supports both normal foreground commands and detached background jobs.

**Data flow**: It receives the tool context and a command request. If background mode is requested, it hands off to _bash_background. Otherwise it starts the command through the task runner, waits up to the requested budget, and returns output, timeout information, task handles, or an error message depending on what happened.

**Call relations**: This is the registered handler for the built-in bash tool. It calls the task-running layer for foreground work and calls _bash_background when the caller wants the command detached immediately.

*Call graph*: calls 1 internal fn (_bash_background); 5 external calls (__init__, __init__, run_task, task_handles, timeout_notice).


##### `_bash_background`  (lines 408–420)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command as a detached task and immediately returns the identifiers needed to watch it later. It is used when the agent wants work to continue without waiting for it.

**Data flow**: It receives the sandbox context and command text. It creates a task id, asks the sandbox to launch the task wrapper in detached mode, and returns the task id, process id, and log information, or an error if the task did not start.

**Call relations**: bash_handler calls this when the bash tool request has background set. It relies on the task helper functions to create names and human-readable handles.

*Call graph*: called by 1 (bash_handler); 5 external calls (__init__, __init__, task_base, task_handles, task_id).


##### `_require_str`  (lines 423–426)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Makes sure a value returned by the sandbox is a real, non-empty string. It gives clearer failures when a file read result is malformed.

**Data flow**: It receives an unknown value and the name of the field it should represent. If the value is a non-empty string, it returns it; otherwise it raises an error naming the missing field.

**Call relations**: read_handler and _pdf_result use this while turning image and document read results into content blocks. It is a small safety check before constructing output for the model.

*Call graph*: called by 2 (_pdf_result, read_handler).


##### `_pdf_result`  (lines 429–474)

```
def _pdf_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a sandbox PDF or PowerPoint read result into mixed text and image content for the agent. This lets the agent see extracted text plus rendered page or slide images.

**Data flow**: It receives a dictionary returned by sbxfs. It collects extracted text, page range notes, warnings, and each rendered page image, then returns a ToolResult containing text and image blocks. If the structure is empty or malformed, it raises an error.

**Call relations**: read_handler calls this when sbxfs reports that the file is a PDF or PPTX. It uses _require_str to validate image fields before creating ImageContent blocks.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 477–514)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a file from the sandboxed workspace and returns a bounded, model-friendly result. It supports text files, images, PDFs, and PowerPoint decks.

**Data flow**: It receives a file path and optional offset or limit. It asks sbxfs inside the sandbox to read the file, records that this path has been read this turn, and returns text, image content, document pages, or an empty-file message. For long text files, it includes line range information and how to continue reading.

**Call relations**: This is the registered handler for the read tool. It calls _pdf_result for PDF and PPTX results, and _require_str when building image output.

*Call graph*: calls 2 internal fn (_pdf_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 517–540)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Creates or overwrites a workspace file safely. It refuses overwriting an existing file unless that path has already been read in the current turn.

**Data flow**: It receives the target path and text content. It writes the content to a temporary staged file in the sandbox, asks sbxfs to move it into place with overwrite rules, adds size and line count details, formats the result, and then marks the path as read for future edits in the same turn.

**Call relations**: This is the registered handler for the write tool. It uses _file_tool_result to keep the response small and consistent.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (uuid4).


##### `edit_handler`  (lines 543–556)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact string replacements to a file that the agent has already read. This protects against blind edits where the agent might change content it has not seen.

**Data flow**: It receives a path and one or more edit instructions. It first checks the path was read this turn, encodes the old and new strings safely, sends the edit request to sbxfs, adds the path to the result, and returns a compact file-tool response.

**Call relations**: This is the registered handler for the edit tool. It hands the final sandbox result to _file_tool_result, and it uses base64 encoding so replacement strings can safely contain newlines or special characters.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 559–573)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats the result of a write or edit into a bounded JSON text response. It avoids returning large snippets or diffs that could exceed the tool's output budget.

**Data flow**: It receives a result dictionary, converts it to compact JSON, and returns it if it fits. If it is too large, it removes the snippet and simplifies the message, then returns the smaller JSON or raises an error if it still cannot fit.

**Call relations**: write_handler and edit_handler call this after sbxfs has changed a file. It is the final shaping step before the agent sees the file operation result.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 576–582)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files in the workspace whose paths match a glob pattern, such as '**/*.py'. It does the search inside the sandbox rather than walking files from the host process.

**Data flow**: It receives a pattern and optional starting directory. It asks sbxfs to match files under that directory, then returns the matching paths as JSON text.

**Call relations**: This is the registered handler for the glob tool. It delegates the actual file traversal to the sandbox helper and only formats the returned result.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 585–603)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches workspace file contents for a regular expression, which is a text pattern language. It uses the sandbox's ripgrep-based search so only bounded results leave the container.

**Data flow**: It receives the search pattern and optional filters such as file glob, context lines, case sensitivity, output mode, and result limit. It builds sbxfs search parameters, runs the search in the sandbox, and returns the result as JSON text.

**Call relations**: This is the registered handler for the grep tool. It is the safe replacement for asking the shell to run grep or rg directly.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 606–640)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies a measured workspace file into the artifact store, either by uploading directly from the sandbox to S3 or by streaming through the filesystem blob store. This is the low-level step that makes a shared file durable outside the sandbox.

**Data flow**: It receives the sandbox path, destination key, file size, and sha256 digest. For S3, it creates a presigned upload URL tied to that exact size and checksum, then has the sandbox upload the file with curl. For filesystem storage, it streams the file from the sandbox into the blob store.

**Call relations**: share_file_handler calls this for the actual file, and _shared_preview calls it for a generated preview image. It is deliberately separated so both file and preview use the same storage path.

*Call graph*: called by 2 (_shared_preview, share_file_handler); 2 external calls (b64encode, quote).


##### `_shared_preview`  (lines 652–699)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str) -> ArtifactPreview | None
```

**Purpose**: Attempts to create a small preview image for certain shared document types, such as PDFs and Office files. The preview helps users recognize a shared file visually, but failure to make one does not block sharing.

**Data flow**: It receives the sandbox path and safe display name. If the file type is previewable, it renders the first page or slide to a PNG inside the sandbox, measures that PNG, rejects empty or oversized previews, stores it as an artifact, and returns preview metadata. If rendering or measuring fails, it logs the reason and returns nothing.

**Call relations**: share_file_handler calls this after storing the main file. It calls _store_artifact to save the preview image beside the original artifact.

*Call graph*: calls 1 internal fn (_store_artifact); called by 1 (share_file_handler); 6 external calls (__init__, loads, PurePosixPath, quote, log, uuid4).


##### `share_file_handler`  (lines 702–788)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares a workspace file with the user by storing it as an artifact, recording it in the database, and returning a time-limited download URL. This is the only intended way for a produced file to leave the sandbox.

**Data flow**: It receives the requested workspace file path, optional download name, caption, and context. It checks artifact sharing is configured, measures the file inside the sandbox, chooses a safe filename, stores the file, optionally creates a preview, records the shared artifact in the database, mints an expiring URL, and returns JSON with the URL, name, size, digest, and artifact identity.

**Call relations**: This is the registered handler for the share_file tool. It calls _store_artifact for the main file, _shared_preview for optional visual preview, and database/artifact helpers so chat surfaces can later present the file.

*Call graph*: calls 2 internal fn (_shared_preview, _store_artifact); 16 external calls (__init__, __init__, now, dumps, loads, guess_type, PurePosixPath, quote, insert, select (+6 more)).


##### `spawn_handler`  (lines 791–824)

```
async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult
```

**Purpose**: Delegates work to another agent or subagent profile. It lets the current agent hand off a typed subtask and either wait for the result or continue while it runs in the background.

**Data flow**: It receives the target name, payload, background flag, and display name. It asks the ToolContext to spawn the target, catches unknown or ambiguous target errors, and returns either a child question, a background turn id, or the validated output from the child.

**Call relations**: This is the registered handler for the spawn tool. It relies on ToolContext.spawn for the actual subagent workflow, then shapes the result into text the parent agent can use.

*Call graph*: 4 external calls (__init__, __init__, spawn, dumps).


##### `ask_user_handler`  (lines 832–842)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Packages a question the agent needs to ask the user in the normal chat flow. It tells the agent to ask in its reply and then stop, so the answer arrives as the user's next message.

**Data flow**: It receives a structured question request. It builds a JSON payload containing the title and questions, prefixes it with an instruction to end the turn, and returns it as text content.

**Call relations**: This is the registered handler for the ask_user tool. It does not contact the user directly; instead, it gives the surrounding chat surface and model a structured question to present.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 845–856)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill and its dependencies into the workspace, then returns their instructions. A skill is a bundle of workflow guidance and files the agent can use for a specialized task.

**Data flow**: It receives the skill name. It resolves the full dependency chain, mounts each skill's files into the sandbox workspace, builds the combined instruction context, and returns that text to the agent.

**Call relations**: This is the registered handler for the load_skill tool. It calls the skills runtime to mount files and format the loaded context.

*Call graph*: 4 external calls (__init__, __init__, loaded_context, mount_skill).


##### `_grantee_agent_id`  (lines 865–888)

```
async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None
```

**Purpose**: Figures out which agent should receive access when connecting an external account. It enforces the rule that only the workspace's main agent may connect an account on behalf of another agent.

**Data flow**: It receives the current context and an optional agent name. If no name is given, it returns nothing, meaning the connection is for the asking agent. If a name is given, it reads workspace agents from the database, verifies the asking agent is the main one, finds the named target, and returns that target's id unless it is the same as the asker.

**Call relations**: connect_account_handler calls this before creating a connection request. It resolves authority up front so the later private OAuth handoff is bound to the correct agent.

*Call graph*: called by 1 (connect_account_handler); 2 external calls (select, workspace_tx).


##### `connect_account_handler`  (lines 891–905)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private account-connection flow, such as OAuth for Gmail or GitHub, for the speaking member. It returns instructions for the chat surface without exposing an authorization URL in the transcript.

**Data flow**: It receives the provider name, sharing choice, optional target agent, and context. It verifies there is a speaking member, resolves the grantee agent, validates that the provider is installed, builds a ConnectRequest, and returns it with a directive to tell the member to use the private connection control.

**Call relations**: This is the registered handler for the connect_account tool. It calls _grantee_agent_id for agent targeting and the installed connect flow to validate the provider.

*Call graph*: calls 1 internal fn (_grantee_agent_id); 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 915–938)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks an admin member to provide secrets, such as API keys, through a private prompt instead of chat. This keeps secret values out of the conversation transcript.

**Data flow**: It receives a reason and credential prompts. It checks there is a speaking member, credential storage is configured, and the speaker is an admin. It seals the allowed credential slots for that member, builds a CredentialRequest, and returns it with instructions to end the turn and wait.

**Call relations**: This is the registered handler for the request_credentials tool. It calls the context's admin check and uses the credential sealing service attached to the context.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `cancel_spawn_handler`  (lines 941–953)

```
async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult
```

**Purpose**: Cancels a running child spawn and reports its status. If the child already finished, cancellation does not undo its completed result.

**Data flow**: It receives a spawn id. It checks spawn control is available, converts the id into a UUID, asks the subagent system to cancel it, and returns JSON with the spawn id and current status.

**Call relations**: This is the registered handler for the cancel_spawn tool. It talks to ctx.subagents, which is the same subagent workflow used by spawn_handler.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_spawn_handler`  (lines 956–974)

```
async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a spawned child agent. This is how the parent can answer a child question or give new instructions to a background spawn.

**Data flow**: It receives a spawn id and message. It checks spawn control and an idempotency key are available, converts the id into a UUID, queues the message as the spawn's next turn, and returns JSON with the spawn id and updated status.

**Call relations**: This is the registered handler for the message_spawn tool. It uses ctx.subagents to queue the follow-up, and the result is later delivered back to the current conversation.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### `extensions/research/ufo_ext_research/tools.py`

`domain_logic` · `tool call handling during a turn`

This file is the bridge between an agent asking for outside information and the search service that can provide it. Without it, the agent might know that a search tool exists, but it would not have a safe, consistent way to ask questions, fetch pages, or receive results back.

The file defines three public tools: web search, URL fetch, and vertical search. It uses Pydantic models, which are structured input checkers, to make sure each tool call has the right shape before anything is sent to the search backend. For example, web search limits how many queries and results can be requested, and URL fetching requires a public web address.

A key safety idea is that the actual search provider runs on the host side, not inside the agent workspace. That means secret search API keys stay out of the sandbox. When fetching a page, the file also clearly labels the content as coming from the provider’s crawler session, not from the user’s workspace or account. Like sending a courier to read a notice on your behalf, the courier’s identity may affect what they see.

The tool handlers call the selected provider, optionally record observations for the activity timeline, and return JSON text wrapped in a tool result.

#### Function details

##### `_provider`  (lines 145–148)

```
def _provider(ctx: ToolContext) -> SearchProvider
```

**Purpose**: This small helper retrieves the search provider chosen for the current turn. It fails loudly if no provider is configured, because the research tools cannot work without a backend to answer searches or fetch pages.

**Data flow**: It receives the current tool context, looks inside it for a search provider, and returns that provider if present. If the context has none, it stops the request by raising an error instead of letting later code fail in a confusing way.

**Call relations**: The web search, URL fetch, and vertical search handlers all call this first. It acts like checking that the power is plugged in before starting the machine; only after it succeeds do those handlers ask the provider to search or fetch.

*Call graph*: called by 3 (_fetch_url, _search_vertical, _search_web).


##### `_results_json`  (lines 151–165)

```
def _results_json(hits: list[SearchHit], answer: str | None) -> str
```

**Purpose**: This function turns search hits into a JSON string that the agent can read in a predictable format. It includes each result’s URL, title, snippet text, publication date, highlights, and optionally a direct answer from the provider.

**Data flow**: It receives a list of search hits and an optional answer. It copies the useful fields from each hit into plain dictionaries, adds the answer if one exists, and serializes the whole package into JSON text.

**Call relations**: Both regular web search and vertical search use this after they receive results from the provider. It is the final packing step before the handlers wrap the text in a tool result and return it to the agent.

*Call graph*: called by 2 (_search_vertical, _search_web); 1 external calls (dumps).


##### `_search_web`  (lines 168–186)

```
async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_web` tool. It runs one web search for each requested query, combines the results, records what was found when observation logging is available, and returns the combined result list to the agent.

**Data flow**: It receives the tool context and validated search arguments, including queries, result limits, date filters, and allowed domains. It gets the configured provider, sends each query as a search request, gathers all hits into one list, keeps the first provider answer if any, optionally records the hits for the turn, and returns JSON text inside a tool result.

**Call relations**: When the agent calls `search_web`, this function is the main work path. It first relies on `_provider` to get the backend, builds search requests for the provider, then hands the collected hits to `_results_json` so the final response has the standard search-results shape.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).


##### `_fetch_url`  (lines 189–210)

```
async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult
```

**Purpose**: This is the handler behind the `fetch_url` tool. It asks the provider to retrieve a public web page, optionally using a prompt to extract specific information, and returns the page content with a clear warning about crawler provenance.

**Data flow**: It receives the tool context and validated fetch arguments: URL, optional extraction prompt, optional length limit, and whether to bypass cache. It gets the provider, checks whether that provider supports fetching, and either returns a clear error message or sends a fetch request. When a page comes back, it optionally records the fetched page, builds a JSON reply with the URL, text, provenance warning, and optional summary, then returns it as a tool result.

**Call relations**: When the agent calls `fetch_url`, this function coordinates the whole fetch. It uses `_provider` to find the backend, calls the provider’s fetch operation, records the page through the observations system when available, and directly serializes the final page reply as JSON.

*Call graph*: calls 1 internal fn (_provider); 5 external calls (__init__, __init__, __init__, dumps, record_fetched_page).


##### `_search_vertical`  (lines 213–222)

```
async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult
```

**Purpose**: This is the handler behind the `search_vertical` tool. It searches a specialized category, such as images, people, academic papers, videos, or shopping results, instead of doing a general web search.

**Data flow**: It receives the tool context and validated vertical-search arguments: the category and the query. It gets the configured provider, sends a search request that includes the chosen vertical, optionally records the returned hits, converts the results into the standard JSON format, and returns them in a tool result.

**Call relations**: When the agent needs a specific type of content, this function follows a shorter version of the web-search path. It calls `_provider`, asks the provider for a vertical search, records the hits if observation logging is active, and uses `_results_json` so its output matches the regular search format.

*Call graph*: calls 2 internal fn (_provider, _results_json); 4 external calls (__init__, __init__, __init__, record_search_hits).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-credential-vault` — The encrypted store of workspace secrets and API keys that tools and connectors can request through guarded paths.
- `reg-connection-grants` — The saved outside-service account connections and the grants saying which agents may use them.
- `reg-tool-registry` — The shared catalog of tools the model is allowed to call and the input rules for each tool.
- `reg-connector-tool-catalog` — The discovered connector actions from systems like Gmail, Slack, GitHub, Composio, Pipedream, and MCP servers.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-sandbox-state` — The per-conversation sandbox handle, size, filesystem environment, command execution state, and cleanup state.
- `reg-network-egress-policy` — The shared network access rules and freshness counter that tell sandboxes and proxies where code may connect.
- `reg-browser-sessions` — The browser or Chrome DevTools session state used when tools and hosted sandbox websites need a controlled browser.
- `reg-object-store` — The durable named workspace objects owned by extensions, with their names, data, permissions, and owner routing.
- `reg-blob-artifacts` — The shared file and artifact storage for large bytes, generated files, previews, and signed downloads.
- `reg-portal-slots` — The safe display state for conversation panels such as sources, artifacts, tasks, sites, automations, and workspace changes.
- `reg-source-feeds` — The registered external content sources, sync cursors, backoff state, ownership, grants, and wake-up triggers.
- `reg-workflow-plans` — The longer-running goals, objective steps, blockers, todos, delegated work, and progress evidence that survive across turns.
- `reg-subagent-delivery` — The parent-child turn links and pending result records used when helper agents run work and report back.
- `reg-usage-ledger` — The shared cost and usage records for model calls, tools, sandboxes, connectors, network use, and generated media.
- `reg-spend-controls` — The spend caps, prepaid balances, price table fingerprints, and checks that decide whether work may continue.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-extension-kv-store` — Private per-workspace JSON/key-value state owned by extensions for setup, feature bookkeeping, and small durable extension data that is not a user-visible object.
- `reg-workspace-change-log` — Durable records of file/Git workspace changes attached to conversations so side effects can be recovered, summarized, and rendered safely in portal panels.
- `reg-hosted-site-state` — Durable hosted website records, publication metadata, permissions, and homepage-agent bindings used to build, serve, list, and remove sites.
- `reg-research-observations` — Durable per-conversation web/search source observations and retrieval metadata saved by research tools for later citation and Sources-panel rendering.
- `reg-untrusted-content-taint` — Trust/taint markers attached to external content as it moves through retrieval, prompts, tools, and rendering so prompt-injection safety checks can be enforced.
- `reg-tool-execution-context` — The per-turn tool runtime context carrying permitted workspace handles, account/credential accessors, cleanup callbacks, sandbox/browser handles, and helper-agent hooks across tool calls.
- `reg-sandbox-runtime-image` — The prepared sandbox runtime image, build/cache metadata, and proxy validation state used before sandboxes can be launched safely.
- `reg-skill-workflow-catalog` — The registered agent skills, helper subagent profiles, workflow profiles, and related prompt/activity metadata injected into turns and surfaced to users.
