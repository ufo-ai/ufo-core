# Tool collection and trusted host-side dispatch  `stage-11`

This stage is the system’s tool room and dispatcher during the main work loop. When the model asks to use a tool, the system first decides which tools are allowed for this turn, checks that their names and input forms are safe, records the request, then either runs a trusted built-in tool or sends it to an approved outside broker.

The registry is the rulebook for defining and collecting tools. The context file builds the safe workbench each tool receives: what it may read, use, and report back. The tool bridge lets code inside a sandbox, a locked-down running area, ask the main system to list or run approved tools without bypassing checks. Activity labeling turns tool calls into short user-friendly status messages.

The built-in tools cover everyday workspace work like files, shell commands, questions, sharing, and subagents. Connector tools safely reach external services such as Gmail, GitHub, Slack, web search, and MCP servers without exposing secrets. Domain-specific extension tools add deeper abilities for web work, documents, spreadsheets, PDFs, schedules, monitors, reports, debugging, and test-only fake services.

## Sub-stages

- [Credentialed connector and external API tools](stage-11.1.md) `stage-11.1` — 12 files
- [Built-in workspace, file, shell, question, share, and skill tools](stage-11.2.md) `stage-11.2` — 2 files
- [Domain-specific extension tools](stage-11.3.md) `stage-11.3` — 27 files

## Files in this stage

### Trusted Tool Dispatch
Sandbox-originated tool requests are validated against the allowed tool workbench and registry, recorded for the turn loop, and summarized for user-facing activity.

### `core/src/ufo/loop/tool_bridge.py`

`orchestration` · `request handling`

A sandbox run cannot simply call any project tool directly. It needs a controlled bridge back into the normal turn loop, where permissions, speaker rules, logging, retries, and durable state already exist. This file provides that bridge through the ToolBridge class.

Think of it like a service desk window. The sandbox can ask, “What tools may I use?”, “What shape of input does this tool expect?”, or “Please run this tool with these arguments.” ToolBridge first finds the live parent turn and checks whether the requested tool is visible to that parent agent or subagent. For action-based tools, it checks whether any real bound action has been granted.

When a tool is actually called, ToolBridge does not execute it inline. Instead, it creates a child conversation and a child turn in the database, using stable identifiers so the same request can be retried safely. It then asks DBOS, the durable workflow queue, to run that turn. Finally, it tails the child turn until it finishes, and turns the terminal result into either a success value or a clear failure message.

An important detail is that listing a tool is only discovery, not final authority. The child turn will still re-resolve the named tool under its own grants when it runs.

#### Function details

##### `ToolBridge.request`  (lines 64–98)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is the main doorway for sandbox bridge requests. It answers tool-list and schema questions immediately when allowed, or turns an actual tool call into a queued child turn and waits for its result.

**Data flow**: It receives a sandbox run token and a bridge request. It looks up the live parent turn, checks which tools are allowed, optionally returns a list or input schema, or records and queues a new child turn for a tool call. The output is a bridge response: either a success with JSON-like data or a failure with a human-readable error.

**Call relations**: This function coordinates the whole file. It asks _parent for the current parent turn, uses _allowed to filter or approve tools, calls _admit to write a callable request into the database, hands that turn to _enqueue for durable execution, and then waits through _terminal for the child turn’s final answer.

*Call graph*: calls 5 internal fn (_admit, _allowed, _enqueue, _parent, _terminal); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `ToolBridge._parent`  (lines 100–129)

```
async def _parent(self, run: RunToken) -> sa.Row[tuple[object, ...]] | None
```

**Purpose**: This function finds the still-running parent turn that gives the sandbox its authority. If the parent turn is gone or no longer running, the bridge should not proceed.

**Data flow**: It receives the run token, reads the workspace and turn identifiers from it, and queries the database for the matching turn, agent, and conversation details. It returns the database row if the turn is currently running, or nothing if the bridge request is no longer valid.

**Call relations**: ToolBridge.request calls this first because every later decision depends on the parent turn. The returned row supplies the agent, subagent, conversation, audience, and tool grant information used by permission checks and by _admit when creating the child turn.

*Call graph*: called by 1 (request); 2 external calls (select, workspace_tx).


##### `ToolBridge._allowed`  (lines 131–143)

```
def _allowed(self, parent: sa.Row[tuple[object, ...]], tool: ToolDef) -> bool
```

**Purpose**: This function decides whether a particular tool should be available to the sandbox under the parent turn’s grants. It treats ordinary tools, action tools, and subagent defaults according to the same rules used by the turn system.

**Data flow**: It takes the parent turn row and one tool definition. It checks special action-tool cases, expands implied grants such as helper tools that come along with other permissions, and considers whether the parent is a normal agent or a subagent profile. It returns true if the tool may be shown or requested, otherwise false.

**Call relations**: ToolBridge.request uses this both when building the visible tool list and before returning a schema or admitting a call. For action-related tools, _allowed asks _any_action_granted whether the parent has at least one action permission that makes those tools meaningful.

*Call graph*: calls 1 internal fn (_any_action_granted); called by 1 (request); 1 external calls (with_implied_grants).


##### `ToolBridge._any_action_granted`  (lines 145–161)

```
def _any_action_granted(self, parent: sa.Row[tuple[object, ...]]) -> bool
```

**Purpose**: This helper answers a narrow question: does this parent turn have access to at least one bound action? That matters because the generic action tools should only appear when there is an actual action they could use.

**Data flow**: It reads the bridge’s registered bound actions and the parent’s tool or subagent grants. It expands implied grants, compares them with each action’s canonical identifier, and also respects action defaults for non-isolated subagents. It returns a simple yes or no.

**Call relations**: _allowed calls this when it needs to decide whether the object-action tool or action-reading tools should be visible. It does not admit or run anything itself; it only supports the permission decision made higher up.

*Call graph*: called by 1 (_allowed); 1 external calls (with_implied_grants).


##### `ToolBridge._admit`  (lines 163–258)

```
async def _admit(self, run: RunToken, parent: sa.Row[tuple[object, ...]], request: ToolBridgeRequest) -> tuple[UUID, UUID] | None
```

**Purpose**: This function records a sandbox tool call as a real child turn in the database. It makes the request durable before anything is queued, so the system can recover or retry without inventing a different call.

**Data flow**: It receives the run token, parent turn row, and bridge request. It builds stable conversation and turn identifiers from the workspace, parent turn, and request id, serializes the requested tool and arguments as an intent, then opens a database transaction. Inside that transaction it confirms the parent is still running, inserts the child conversation and child turn if needed, verifies that a reused request id means the exact same call, and marks the turn ready for dispatch. It returns the child turn id and conversation id, or nothing if the parent stopped running.

**Call relations**: ToolBridge.request calls this only for real tool execution, after permission checks pass. Its output is handed to _enqueue so DBOS can run the child turn. It also uses the tracing context so the new turn stays connected to the parent in observability tools.

*Call graph*: called by 1 (request); 8 external calls (__init__, TypeAdapter, select, update, workspace_tx, current_traceparent, turn_id_for, uuid5).


##### `ToolBridge._enqueue`  (lines 260–289)

```
async def _enqueue(self, workspace_id: UUID, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This function asks DBOS, the durable workflow queue, to run the child turn created by _admit. If enqueueing is interrupted or fails, it clears the dispatch marker so another recovery path can enqueue it later.

**Data flow**: It receives the workspace id, child turn id, and conversation id. It builds queue options that identify the turn workflow and its partition key, then calls the DBOS async enqueue API. If the task is cancelled, it rolls back the turn’s dispatch-enqueued timestamp and re-raises the cancellation. If another error happens, it also clears that timestamp and logs that enqueueing was deferred.

**Call relations**: ToolBridge.request calls this after _admit writes the child turn. It does not wait for the work to finish; it only places the turn on the durable queue. The later result is collected separately by _terminal.

*Call graph*: called by 1 (request); 3 external calls (update, workspace_tx, log).


##### `ToolBridge._terminal`  (lines 291–299)

```
async def _terminal(self, turn_id: UUID) -> ToolBridgeResponse
```

**Purpose**: This function waits for the child turn to reach an end state and converts that end state into a bridge response. It also reports a parked turn as a failure because the sandbox call cannot continue normally.

**Data flow**: It receives a child turn id and opens a tail on that turn’s event stream. As frames arrive, it watches for a terminal frame or a parked frame. A terminal frame is passed to _response, a parked frame becomes a failure message, and if the stream ends without an ending frame it raises an internal error.

**Call relations**: ToolBridge.request calls this after enqueueing the child turn. It relies on the TurnTailer to observe the turn loop’s progress, and delegates final success-or-failure formatting to _response.

*Call graph*: calls 1 internal fn (_response); called by 1 (request); 1 external calls (__init__).


##### `ToolBridge._response`  (lines 301–313)

```
def _response(self, terminal: TerminalFrame) -> ToolBridgeResponse
```

**Purpose**: This function translates the child turn’s final record into the bridge protocol’s success or failure shape. It makes sure successful results are valid JSON-like values before returning them to the sandbox.

**Data flow**: It receives a terminal frame from a finished child turn. If the turn did not finish with status done, it combines the available error class, error message, or text into a failure. If it did finish, it tries to parse the terminal text as JSON; if parsing fails, it treats the text itself as the value. It then validates that value as JSON-compatible data and returns a success response.

**Call relations**: _terminal calls this when the tailed child turn emits its terminal frame. This is the last step in the bridge flow: it turns the turn loop’s internal ending record into the response that ToolBridge.request gives back to the sandbox.

*Call graph*: called by 1 (_terminal); 4 external calls (__init__, __init__, loads, TypeAdapter).


### `core/src/ufo/tools/context.py`

`data_model` · `tool execution and turn cleanup`

A tool in this system should not be able to reach everything directly. This file builds the controlled doorway it must use instead. The central type is ToolContext: a bundle of carefully chosen powers, such as access to the sandbox, artifact storage, the current turn and agent, subagent spawning, credential approval, connected accounts, browser and search providers, billing meters, and cleanup hooks. Think of it like giving a contractor a keycard that opens only the rooms needed for today’s job.

The file also defines the shapes of tool output, including plain text and images, plus result records for child agents. It includes clear error classes for cases where a requested subagent or spawn target does not exist, or where a name is ambiguous.

Several ToolContext methods enforce safety boundaries. They decide whose authority is active, what audience can read or receive data, whether the speaker is an admin, which connector accounts are available, and whether credential authorization is allowed. Other methods turn sandbox files into previews or shared artifacts, record paid media-generation costs, and register cleanup work so browser sessions or other temporary resources do not leak after a turn ends. Without this file, tools would either be too powerless to do useful work or too powerful to run safely.

#### Function details

##### `UnknownSubagentProfile.__init__`  (lines 111–116)

```
def __init__(self, requested: str, registered: tuple[str, ...]) -> None
```

**Purpose**: Builds a helpful error when code asks for a subagent profile that is not registered. The message includes both the bad name and the valid choices so the caller can recover instead of seeing a vague failure.

**Data flow**: It receives the requested profile name and the tuple of registered names. It stores both on the exception and formats them into a readable error message. The result is an exception object ready to be raised and logged.

**Call relations**: The subagent registry calls this when a profile lookup fails. This error then travels back toward the spawning path so the tool or model can try again with a real profile name.

*Call graph*: called by 1 (get).


##### `UnknownSpawnTarget.__init__`  (lines 123–130)

```
def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when something tries to spawn a target name that is neither a known subagent profile nor a workspace agent. It explains what names are actually available.

**Data flow**: It receives the requested target, the known profile names, and the known agent names. It saves those facts on the exception and turns them into one readable message. The output is an exception that carries enough detail to guide a retry.

**Call relations**: The subagent spawning resolver uses this when it cannot match a target. It supports the larger spawn flow by turning a dead-end lookup failure into an actionable message.

*Call graph*: called by 1 (_resolve).


##### `AmbiguousSpawnTarget.__init__`  (lines 137–142)

```
def __init__(self, requested: str) -> None
```

**Purpose**: Builds an error for the case where one bare name could mean both a profile and an agent. It tells the caller to use an explicit prefix so the system does not guess wrong.

**Data flow**: It receives the ambiguous name, stores it, and creates a message suggesting the two qualified forms. The result is an exception object that explains how to disambiguate.

**Call relations**: The spawn target resolver raises this when both namespaces contain the same name. It protects the spawning flow from accidentally choosing the wrong kind of child worker.

*Call graph*: called by 1 (_resolve).


##### `Spawn.__call__`  (lines 208–217)

```
async def __call__(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnRes
```

**Purpose**: Defines the contract for starting a child turn, either as a subagent profile or as another workspace agent. This is a protocol method, meaning this file describes what an implementation must provide rather than doing the work here.

**Data flow**: A caller supplies a target name, input payload, and options such as background running, deduplication key, result delivery behavior, display name, and whether waiting can be interrupted. An implementation validates and starts or reconnects to the child turn. It returns a SpawnResult describing the child and, when available, its final output.

**Call relations**: ToolContext exposes this as ctx.spawn so tools can delegate subtasks without knowing the subagent engine internals. The actual spawning implementation lives elsewhere, but tools rely on this shape when they ask another agent to do work.


##### `SubagentControl.result`  (lines 227–227)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Defines how callers retrieve the final result of an already-spawned background subagent. It is part of a protocol, so it sets the expected interface rather than implementing storage or waiting here.

**Data flow**: It takes a child turn id. An implementation looks up that child and returns a SpawnResult containing its terminal state and validated output if it has finished. The method does not change this file’s data by itself.

**Call relations**: ToolContext may provide a SubagentControl object to tools that need to inspect background children. The subagent lifecycle implementation supplies the real behavior behind this protocol.


##### `SubagentControl.wait`  (lines 229–229)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Defines how a tool can wait for one or more background subagents and receive their current terminal summaries. This lets a tool pause briefly for child work without owning the whole subagent engine.

**Data flow**: It receives a tuple of child turn ids. An implementation waits according to its own rules and returns a tuple of SubagentStatus records, each summarizing one child’s state and text. The caller gets status information rather than raw internal turn records.

**Call relations**: Tools access this through ToolContext.subagents when they need to coordinate children they already started. The actual waiting behavior is provided by the subagent workflow outside this file.


##### `SubagentControl.cancel`  (lines 231–231)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Defines how a running background subagent can be stopped. It gives tools a controlled way to clean up child work that is no longer needed.

**Data flow**: It receives the child turn id. An implementation requests cancellation and returns a SubagentStatus describing what happened. The important change is in the child turn’s lifecycle, not in this context object.

**Call relations**: A tool calls this through ToolContext.subagents when it decides a background child should not continue. The subagent system performs the actual cancellation.


##### `SubagentControl.message`  (lines 233–235)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Defines how to send a follow-up message to an existing background subagent. The deduplication key helps avoid sending the same message twice during retries.

**Data flow**: It receives the child turn id, message text, a deduplication key, and whether the child will deliver its own result. An implementation admits the message to the child conversation and returns a SubagentStatus. The caller gets a concise report of the child’s state after the message.

**Call relations**: Tools use this through ToolContext.subagents when ongoing child work needs more information. The subagent workflow handles the actual message admission and idempotency.


##### `TurnCleanup.register`  (lines 249–250)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous cleanup action to the current turn’s cleanup list. Tools use it when they open a temporary resource, such as a browser connection, that must be closed later.

**Data flow**: It receives a callable that can be awaited to close something. It appends that callable to the internal list. Nothing is closed immediately; the function simply records the future cleanup job.

**Call relations**: Resource-building code calls this during a turn after opening something temporary. Later, TurnCleanup.drain walks the recorded closers and runs them.


##### `TurnCleanup.drain`  (lines 252–258)

```
async def drain(self) -> None
```

**Purpose**: Runs all registered cleanup actions at the end of a turn. This prevents temporary resources from surviving after the work that needed them has finished.

**Data flow**: It reads the internal list of cleanup callables, removes them one by one in reverse order, and awaits each one. If a closer fails, it logs the failure and keeps going. The result is an emptied cleanup list.

**Call relations**: The turn loop calls this during turn shutdown. It hands failures to the logging system instead of letting one broken cleanup block the rest.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 305–313)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Identifies the member whose authority this tool call may use. It chooses the live speaker when there is one, otherwise the member the turn is acting on behalf of.

**Data flow**: It reads speaker_member_id and on_behalf_of_member_id from the context. If a speaker is present, that id wins; otherwise it falls back to the on-behalf-of id. It returns a member id or None.

**Call relations**: Other context methods use this as the starting point for permission decisions, especially audience selection and connector account access.


##### `ToolContext.effective_audience`  (lines 316–326)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides which audience a write should belong to. This matters because writing information into the wrong audience could leak private-room or cross-organization facts into other conversations.

**Data flow**: It reads the current audience and the acting member. If there is no acting member, or the conversation is not workspace-shared, it returns the existing audience. If the conversation is shared and there is an acting member, it returns that member’s conversation audience.

**Call relations**: It uses the audience helper to build the member-specific conversation audience. Other write paths can rely on this property when they need the correct disclosure boundary.

*Call graph*: 1 external calls (conversation_audience).


##### `ToolContext.read_subjects`  (lines 329–338)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes the set of subjects this tool call is allowed to read. A subject is a label used to decide which memories or source pages are visible.

**Data flow**: It starts with the subjects attached to the current audience. If there is an acting member, it adds that member’s private subject. It returns the combined set as an immutable frozenset.

**Call relations**: It uses audience and member-subject helper functions. Source and memory readers use this permission set to avoid showing data from audiences the requester should not see.

*Call graph*: 2 external calls (audience_subjects, member_subject).


##### `ToolContext.store_preview`  (lines 340–386)

```
async def store_preview(self, sandbox_path: str, name: str, *, extension: str='png') -> StoredPreview | None
```

**Purpose**: Stores an image file produced inside the sandbox as a preview artifact. It is intentionally forgiving: if the preview cannot be read or uploaded, it returns None instead of failing the main tool work.

**Data flow**: It receives a sandbox path, a preview name, and a file extension. It asks the sandbox to measure the file size, creates a new artifact key, then either uploads through a presigned S3 URL or streams the file into the blob store. If everything succeeds, it returns a StoredPreview with the blob key and size; otherwise it logs the issue and returns None.

**Call relations**: The sites extension calls this after composing visual cards. The method talks to the sandbox, blob storage, UUID generation, shell path quoting, and logging so extension code does not need to know the storage details.

*Call graph*: called by 1 (_compose); 5 external calls (__init__, quote, log, shell_path, uuid4).


##### `ToolContext.render_site_preview`  (lines 388–395)

```
async def render_site_preview(self, name: str, port: int, width: int, height: int) -> StoredPreview | None
```

**Purpose**: Asks the configured site preview service to take a screenshot-like preview of a hosted sandbox port. If no preview service is configured, it simply reports that no preview is available.

**Data flow**: It receives a name, port, width, and height. It picks the conversation id tied to the sandbox, then calls the site previewer if one exists. It returns a StoredPreview from that service or None.

**Call relations**: The sites extension uses this when it needs a visual preview of a running site. ToolContext supplies the correct conversation identity so the preview service sees the right hosted port.

*Call graph*: called by 1 (_illustrate).


##### `ToolContext.source_reader`  (lines 397–407)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Creates a SourceReader that describes who is asking to read synced source pages. This keeps source access tied to the current agent, live requester, and readable subjects.

**Data flow**: It reads the turn’s agent id, the current speaking member id, and the computed read_subjects. It packages those into a SourceReader object. The result is a compact permission description for source-reading code.

**Call relations**: Memory and source extensions call this before listing or fetching stored pages. It connects those extensions to the context’s audience and requester rules without duplicating them.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 409–418)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images on the workspace billing ledger. Image providers may price work outside the normal language-model token system, so extensions report the price here.

**Data flow**: It receives the model name, image count, and cost in micro-dollars. It opens a workspace database transaction and writes an image-usage record tied to this workspace and turn. It returns nothing, but the billing ledger is updated.

**Call relations**: The OpenRouter image extension calls this after generating images. This method hands the actual write to the billing accounting function inside a workspace transaction.

*Call graph*: called by 1 (generate); 2 external calls (record_image_usage, workspace_tx).


##### `ToolContext.meter_videos`  (lines 420–428)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos on the workspace billing ledger. Video generation has its own pricing style, so extensions can book that spend through core.

**Data flow**: It receives the model name, video count, and cost in micro-dollars. It opens a workspace database transaction and records video usage for this workspace and turn. It returns nothing, but billing data is persisted.

**Call relations**: The OpenRouter video extension calls this after generating videos. This method delegates the ledger write to the video accounting function.

*Call graph*: called by 1 (generate); 2 external calls (record_video_usage, workspace_tx).


##### `ToolContext.share_artifact`  (lines 430–455)

```
async def share_artifact(self, filename: str, data: bytes, subject: str | None=None) -> None
```

**Purpose**: Shares a small in-memory file as a turn artifact so users can download or view it through the normal artifact path. It is for bytes already computed by a tool, not for files that need to be copied from the sandbox.

**Data flow**: It receives a filename, raw bytes, and an optional subject. It rejects data above the small shared-artifact limit, stores the bytes under a new artifact key, determines the media type from the filename, and inserts a shared-artifact row in the database. The result is a stored blob plus a database record linking it to the turn.

**Call relations**: The iMessage and sites extensions call this when they need to expose generated content. It coordinates blob storage, timestamping, media-type detection, UUID generation, and the database insert.

*Call graph*: called by 2 (run, render_application_preview); 5 external calls (now, insert, workspace_tx, artifact_media_type, uuid4).


##### `ToolContext.speaker_is_admin`  (lines 457–467)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live requesting member is a workspace administrator. Background or speakerless work cannot borrow admin power through this method.

**Data flow**: It first checks whether speaker_member_id exists. If not, it returns False. Otherwise it opens a workspace transaction and asks the seats subsystem whether that member is an admin in this workspace.

**Call relations**: Many object and workspace operations call this before allowing broader visibility or destructive changes. Credential authorization also depends on it.

*Call graph*: called by 27 (restore, _widens_for_admin, delete, _visible_rows, apply, delete, get, list, status, request_credentials_handler (+15 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 469–480)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether the current agent is marked as the workspace’s main agent. Some operations are only allowed, or shown differently, for the main agent.

**Data flow**: It opens a workspace transaction and queries the agent table for the current turn’s agent id and workspace id. It reads the is_main value and returns it as a boolean. No context fields are changed.

**Call relations**: Member, workspace, and web audience features call this when they need to distinguish the main agent from other agents. The method hides the database query behind a simple yes-or-no answer.

*Call graph*: called by 6 (add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.agent_visibility`  (lines 482–494)

```
async def agent_visibility(self) -> AgentVisibility
```

**Purpose**: Reads whether the current agent is private or visible to the workspace. It also guards against unexpected stored values.

**Data flow**: It opens a workspace transaction and selects the visibility field for the current agent. If the stored value is one of the supported levels, it returns it. If the database contains something outside those levels, it raises a runtime error.

**Call relations**: The sites extension calls this when redeploying a homepage. The method provides a trusted visibility value rather than making extension code query the database directly.

*Call graph*: called by 1 (_redeploy_homepage); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 496–498)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts the process of authorizing an extension credential slot, such as an OAuth-style secret. It first checks that this caller is allowed to authorize that slot.

**Data flow**: It receives a credential slot name and payload. It calls the shared credential-authorization checker, then asks the CredentialRequests service to create an authorization string for this workspace, member, slot, and payload. It returns that sealed authorization value.

**Call relations**: Coding and Slack extension connection flows call this when they need an admin to authorize credentials. It relies on _credential_authorization for all safety checks before creating the request.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 500–502)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens and verifies a previously sealed credential authorization for an extension slot. This lets the system confirm that the authorization belongs to the right workspace, member, and slot.

**Data flow**: It receives a slot name and sealed authorization string. It runs the same authorization checks as the begin method, then asks CredentialRequests to open the sealed value. It returns the opened payload or authorization content.

**Call relations**: It pairs with begin_credential_authorization and uses the same private helper. Even though no caller is listed here, it exists for flows that need to validate an authorization after it comes back.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext._credential_authorization`  (lines 504–513)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the safety checks required before an extension can create or open a credential authorization. It centralizes the rules so both begin and open use the same gate.

**Data flow**: It checks that there is a live speaking member, that the current extension declares the requested credential slot, that credential storage is configured, and that the speaker is an admin. If all checks pass, it returns the CredentialRequests service and the speaker’s member id. If any check fails, it raises a clear ValueError.

**Call relations**: begin_credential_authorization and open_credential_authorization call this first. It calls speaker_is_admin as the final authority check.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (begin_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 515–524)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the connected-account id a connector tool should pass to the external broker. It is the simple form for tools that only need the account id, not the full connection metadata.

**Data flow**: It receives a provider name and optionally a specific account id. It asks connector_connection to resolve the allowed connection, then extracts and returns the account_id field. It does not itself inspect grants.

**Call relations**: Connector execution tools call this before invoking broker-side actions. It delegates the harder permission and ambiguity logic to connector_connection.

*Call graph*: calls 1 internal fn (connector_connection); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_connection`  (lines 526–563)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Chooses the exact connector connection this turn is allowed to use. It respects private member grants, shared agent grants, explicit account selection, and ambiguity rules.

**Data flow**: It receives a provider and optional account id. It gathers private and shared grant tiers, then either finds the requested account or chooses the single best available account, preferring private grants over shared ones. It returns a ConnectorConnection with the connection id, account id, and owner member id, or raises a clear error if none or too many are available.

**Call relations**: connector_account calls this for the common account-id case, and source tools call it when they need the stable connection identity. It relies on _connector_account_tiers to get the permission-filtered grant lists.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


##### `ToolContext.connector_accounts`  (lines 565–573)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists all connected account ids for one provider that this turn may use. It gives tools a safe discovery view without exposing unavailable or unrelated accounts.

**Data flow**: It receives a provider name, asks _connector_account_tiers for allowed private and shared grants, combines their account ids, removes duplicates, sorts them, and returns them as a tuple. It does not change any grants.

**Call relations**: Source tools call this when resolving which external account to use. It shares the same grant-filtering helper as connector_connection so listing and selection follow the same rules.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 575–594)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: Builds the two groups of connector grants available to this turn: the acting member’s private accounts and the shared accounts for the agent. This is the permission core behind connector account selection.

**Data flow**: It checks that a grant store exists, reads the acting member id, and loads active grants. It filters grants for the requested provider into private grants owned by the acting member and shared grants available to the agent, sorting each group by account id. It returns the two lists, or raises ConnectUnavailable if grants are not configured.

**Call relations**: connector_connection and connector_accounts both call this so they agree on what accounts are usable. It is the point where connector privacy is enforced before any external broker call happens.

*Call graph*: called by 2 (connector_accounts, connector_connection); 1 external calls (__init__).


### `core/src/ufo/tools/registry.py`

`domain_logic` · `startup and tool lookup`

The system lets an AI model call named tools, such as reading data, sending something, or acting on a visible object. This file gives those tools a precise catalog entry. A `ToolDef` says: here is the tool name, its plain-language description, the input data it expects, and the code that will run it. It also records important safety labels, such as whether the result may contain untrusted text from outside, whether the tool changes something outside the system, and whether calls can run in parallel.

Some tools are global, like a normal command in a toolbox. Others are object actions, meaning they belong to a certain kind of object or even one specific visible object. Those bound actions get a special stable identity like `action:<kind>:<name>`, and this file makes sure they do not accidentally appear in the normal wire registry sent to the model.

`ToolRegistry` is the frozen catalog used by the engine. On creation, it checks for duplicate names, reserved prefixes, illegal input fields, and invalid presentation settings. This is like checking a public menu before opening a restaurant: every item must have a unique name, no private kitchen-only items can appear on the menu, and no item may use words reserved for the staff.

#### Function details

##### `ToolDef.canonical_id`  (lines 95–100)

```
def canonical_id(self) -> str
```

**Purpose**: This gives every tool a stable identity used across the system. For an ordinary tool, that identity is just its name; for an object-bound action, it includes the object kind so it cannot be confused with a global tool.

**Data flow**: It reads the tool definition, especially its name and optional object binding. If there is no binding, it returns the plain name. If there is a binding, it builds and returns an identifier in the form `action:<kind>:<name>`.

**Call relations**: No direct caller is listed in the provided graph, but this property is meant for any code that needs the tool's durable identity, such as allowlists, telemetry, hooks, or idempotency tracking.


##### `ToolDef.schema`  (lines 102–115)

```
def schema(self, *, include_requested_by: bool=True) -> ToolSchema
```

**Purpose**: This turns a tool definition into the schema that can be sent over the wire to the model. A schema is a machine-readable description of what inputs the tool accepts.

**Data flow**: It starts with the tool's Pydantic input model, which knows the allowed input fields. It converts that model into JSON schema, optionally adds a special `requested_by` field used to link a call to the message that requested it, and returns a `ToolSchema` containing the tool name, description, and input schema.

**Call relations**: When this function finishes preparing the input description, it hands that data to `ToolSchema.__init__` to create the final wire-facing schema object. `ToolRegistry.schemas` uses each tool's schema method when it needs the full registry's public schema list.

*Call graph*: 1 external calls (__init__).


##### `validate_tool_declaration`  (lines 118–142)

```
def validate_tool_declaration(tool: ToolDef[Any], label: str) -> None
```

**Purpose**: This checks that a tool's declaration is internally consistent before the system accepts it. It catches mistakes early, such as a button label with no text or an object action pinned to a specific object in an invalid way.

**Data flow**: It receives a tool definition and a human-readable label for error messages. It inspects presentation settings, object binding settings, and final-act settings. If everything is valid, it returns nothing; if something is wrong, it raises a `ValueError` explaining the bad declaration.

**Call relations**: This is called by `ToolRegistry.__post_init__` while the registry is being created. It is the per-tool inspection step after registry-wide checks like duplicate names have been handled.

*Call graph*: called by 1 (__post_init__).


##### `ToolRegistry.__post_init__`  (lines 149–169)

```
def __post_init__(self) -> None
```

**Purpose**: This is the registry's startup safety check. It refuses to build a tool catalog that would be ambiguous, unsafe, or incompatible with the wire protocol.

**Data flow**: It reads the tuple of tools supplied to the registry. It checks for repeated tool names, bound object actions that should not be in the normal registry, names using the reserved `action:` prefix, and input models that already use the reserved `requested_by` field. Then it validates each individual tool declaration. If all checks pass, the registry remains usable; otherwise creation fails with a clear error.

**Call relations**: This runs automatically after a `ToolRegistry` is constructed. As part of that setup, it calls `validate_tool_declaration` for each tool so both catalog-level and individual-tool rules are enforced before dispatch can happen.

*Call graph*: calls 1 internal fn (validate_tool_declaration).


##### `ToolRegistry.schemas`  (lines 171–172)

```
def schemas(self, *, include_requested_by: bool=True) -> tuple[ToolSchema, ...]
```

**Purpose**: This produces the public list of tool schemas for all tools in the registry. The engine can use this list to tell the model which tools exist and what input each one expects.

**Data flow**: It receives the registry and an option saying whether to include the `requested_by` field. It asks every registered tool to produce its schema using that same option, then returns all of those schemas as an immutable tuple.

**Call relations**: No direct caller is listed in the provided graph, but this function is the natural bridge from the internal registry to the wire-facing tool descriptions. It relies on `ToolDef.schema` for the per-tool conversion.


##### `ToolRegistry.get`  (lines 174–178)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: This looks up a tool definition by name. It is used when some part of the engine has a tool name and needs the full definition, including the handler code that should run.

**Data flow**: It receives a name string and scans the registry's tools. If it finds a matching tool, it returns that `ToolDef`. If no tool has that name, it raises a `KeyError` so the failure is loud instead of silently doing the wrong thing.

**Call relations**: No direct caller is listed in the provided graph, but this is the registry's dispatch-time lookup path: code with a requested tool name can ask the registry for the corresponding definition before running the tool.


### `core/src/ufo/turns/activity.py`

`domain_logic` · `during tool-call activity reporting`

When the system uses a tool, the raw details can be technical, noisy, or unsafe to show directly. A tool call might include a tool name, file path, command, URL, identifier, or secret. This file exists to translate that internal action into a simple member-facing phrase such as “Search recent policy updates” or “Check account details,” without exposing the machinery behind it.

The main piece is ActivitySummarizer. It receives one ToolUseBlock, which is the project’s record of a tool being used, plus the user’s goal if available. It builds a small JSON description containing the goal and a shortened version of the tool arguments. Then it sends that JSON to a language model with strict instructions: write only a short plain-language label, describe the current step, and do not reveal tool names, paths, commands, URLs, IDs, secrets, or JSON.

There are two safety rails around this. First, long inputs are trimmed so the summarizer does not send too much argument text. Second, the model call has a timeout; if it fails or takes too long, the code records a metric and log message, then returns nothing instead of blocking the larger workflow. Finally, activity_line tidies the model’s response by removing bullets, quotes, extra spaces, and ending punctuation. Like a receptionist turning a messy internal work order into a neat status line, this file makes tool activity understandable without leaking private details.

#### Function details

##### `ActivityModel.model`  (lines 31–31)

```
def model(self) -> str
```

**Purpose**: This protocol property names the language model that should be used for activity summaries. It lets ActivitySummarizer work with any model provider that offers this value.

**Data flow**: The summarizer reads this property from its model object before making a request. The value goes into the ModelRequest so the request is aimed at the right model.

**Call relations**: ActivitySummarizer.summarize relies on an object that follows the ActivityModel protocol. This property supplies the model name used when that summarizer builds its request.


##### `ActivityModel.complete`  (lines 33–33)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This protocol method represents the act of asking a language model to finish a prompt and return text. Here, that returned text is expected to be a short activity label.

**Data flow**: It receives a ModelRequest containing instructions, the user-facing prompt data, and limits such as maximum tokens. It sends that request to the underlying model service and returns the model’s text answer.

**Call relations**: ActivitySummarizer.summarize calls this method after preparing the prompt. The summarizer then passes the returned text to activity_line so it can be cleaned into a displayable label.


##### `ActivitySummarizer.summarize`  (lines 42–69)

```
async def summarize(self, call: ToolUseBlock, goal: str='') -> str | None
```

**Purpose**: This is the main entry point for turning one internal tool call into a short user-friendly step description. It protects the user experience by hiding technical details and by giving up safely if the model call fails.

**Data flow**: It takes a ToolUseBlock and an optional goal string. It trims the goal, converts the tool name and bounded arguments into compact JSON, wraps that in a ModelRequest with strict safety instructions, and asks the configured model for a short answer. If the model succeeds, the answer is cleaned and returned as a string; if anything goes wrong or times out, it records the failure and returns None.

**Call relations**: This method coordinates the whole summarizing flow. It calls _bounded_arguments before sending data to the model so the input stays small, uses Message and ModelRequest to package the request, runs the model call inside asyncio.timeout so it cannot hang forever, records failures through emit_metric and log, and finally hands the model’s text to activity_line for cleanup.

*Call graph*: calls 2 internal fn (_bounded_arguments, activity_line); 6 external calls (__init__, __init__, timeout, dumps, emit_metric, log).


##### `_bounded_arguments`  (lines 72–76)

```
def _bounded_arguments(arguments: dict[str, object]) -> str
```

**Purpose**: This helper turns a tool’s argument dictionary into a compact JSON string and cuts it down if it is too long. It keeps the summarizer from sending overly large or unwieldy tool details to the model.

**Data flow**: It receives the tool arguments as a dictionary. It serializes them into compact JSON, checks the length, and either returns the full text or the first allowed chunk followed by an ellipsis to show it was shortened.

**Call relations**: ActivitySummarizer.summarize calls this before building the model prompt. Its output becomes the arguments field inside the JSON payload that is sent to the language model.

*Call graph*: called by 1 (summarize); 1 external calls (dumps).


##### `activity_line`  (lines 79–82)

```
def activity_line(text: str) -> str | None
```

**Purpose**: This helper cleans the language model’s answer into one neat label. It removes common formatting leftovers so the rest of the system gets a plain display string.

**Data flow**: It receives raw text from the model. It collapses repeated whitespace, removes leading bullet markers and surrounding quotes or backticks, strips ending punctuation such as periods or exclamation marks, and returns the cleaned line. If nothing meaningful remains, it returns None.

**Call relations**: ActivitySummarizer.summarize calls this after the model returns a completion. It is the last step before the activity label is handed back to whatever user-facing flow requested the summary.

*Call graph*: called by 1 (summarize); 1 external calls (sub).

## 📊 State Registers Touched

- `reg-extension-catalog` — The installed extension and pack catalog that says which extra tools, routes, agents, skills, jobs, and backends are available.
- `reg-auth-tokens` — The signed login, surface, artifact, and SDK tokens used to prove that a caller or link is allowed to act.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-credentials-and-grants` — The encrypted secrets, account connections, and grants that say which member or agent may use an outside service.
- `reg-egress-network-policy` — The outbound network permission state that decides which external hosts, proxies, and secret injections are allowed for a workspace or agent.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-execution-environment` — The controlled runtime environment given to commands, files, terminals, browsers, and documents, including safe environment variables and containment rules.
- `reg-tool-catalog-policy` — The current tool catalog and allowlist rules that say which built-in, extension, connector, MCP, and sandbox tools may be called.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-source-page-sync-state` — The source and page records that remember connected feeds, cursors, backoff, deletes, ownership, grants, and the latest synced content.
- `reg-index-memory-store` — The searchable index and long-term memory store built from synced pages, embeddings, recalled facts, and deduplicated notes.
- `reg-skill-store` — The saved and selected skills that can be provisioned by packs, loaded into agent sandboxes, or created by users inside a workspace.
- `reg-request-actor-scope` — Context-local current workspace, member, acting agent, and object/action scope carried through authorization, database boundaries, object APIs, tools, and egress checks.
- `reg-turn-resource-budget` — In-flight per-turn resource budget and usage accumulator for spend, model tokens, cache reads, sandbox tokens, egress, retries, and stop conditions before final ledger reconciliation.
- `reg-human-question-state` — Pending human-question and answer state used when tools or workflows ask a member for input and later resume the affected turn.
- `reg-turn-created-reference-index` — Durable per-turn list of objects, artifacts, sites, files, or other references created during a turn for later transcript display, panels, delivery, and recovery.
