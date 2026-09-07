# Tool dispatch, sandbox workspaces, and command execution  `stage-11`

This stage is used during the main work of a conversation, whenever the model asks to do something outside plain text. It is like the control desk for tools. The registry lists which tools exist, how they are shown to the model, and how a requested name is safely matched to real code. The tool context then gives that code only the powers it should have, such as sandbox access, artifact sharing, billing records, cleanup hooks, or connector account choices.

Built-in and extension tools are the actual instruments: shell commands, file edits, REPL snippets, Slack search, notifications, MCP server calls, todos, monitoring, and more. The sandbox lifecycle layer provides the safe workspace where risky work happens, whether local, terminal-based, Docker, or cloud-hosted, and controls network access and secrets.

The bridge files let code inside a sandbox ask the main runtime to list, inspect, or run approved tools through the normal permission-checked turn system. The package files are simple signposts that make these tool folders importable and explain what belongs there.

## Sub-stages

- [Sandbox lifecycle and controlled network egress](stage-11.1.md) `stage-11.1` — 16 files
- [Built-in tools and extension tools](stage-11.2.md) `stage-11.2` — 13 files

## Files in this stage

### Sandbox tool bridge
Runtime bridge files let sandboxed code list, inspect, and invoke approved tools through the live parent turn using a constrained wire protocol.

### `core/src/ufo/runtime/tool_bridge.py`

`orchestration` · `during a live sandbox turn, when the sandbox requests a bridge tool`

A sandbox may need to call a project tool, but it should not be allowed to bypass the system’s normal rules. This file is the bridge. It first checks that the original, parent turn is still running. Then it answers simple discovery requests, such as “what tools can I use?” or “what input shape does this tool expect?” For real tool execution, it creates a new child turn in the database, queues that turn for the workflow runner, and waits for the result.

The important idea is that the bridge is not the final source of authority. It filters what the sandbox can see, but the new turn still re-checks grants and speaker rules when it actually runs. Think of it like a receptionist who can tell you which rooms you may request and file your request, but the room guard still checks your badge at the door.

The file also pays close attention to reliability. It uses stable IDs so the same request can be retried without making duplicate turns. It records the request before queueing work. If queueing is cancelled or fails, it clears the “enqueued” marker so another dispatcher can pick it up later. Finally, it watches the child turn until it finishes, turns successful output into JSON when possible, and reports failures in a structured way.

#### Function details

##### `ToolBridge.request`  (lines 66–100)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is the main entry point for a sandbox tool-bridge request. It checks whether the parent turn is still alive, answers tool listing and schema requests, or starts a durable child turn to run the requested tool.

**Data flow**: It receives a live run token and a bridge request. It looks up the parent turn, checks whether the requested tool is allowed, and then either returns a list, returns a schema, reports a clear failure, or creates and queues a child turn. For an execution request, it waits until the child turn finishes and returns the child turn’s final success or failure as the bridge response.

**Call relations**: This function drives the whole flow. It asks _parent for the live parent turn, uses _allowed to decide what the sandbox may see or request, calls _admit to record a new child turn, calls _enqueue to put that turn on the worker queue, and then calls _terminal to wait for the final answer.

*Call graph*: calls 5 internal fn (_admit, _allowed, _enqueue, _parent, _terminal); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `ToolBridge._parent`  (lines 102–131)

```
async def _parent(self, run: RunToken) -> sa.Row[tuple[object, ...]] | None
```

**Purpose**: This function finds the currently running parent turn that gives the sandbox its authority. Without this check, an old or stopped sandbox could keep asking for work after its turn was no longer valid.

**Data flow**: It takes the run token, opens a workspace database transaction, and searches for a turn with the matching workspace and turn ID whose status is still running. It also reads related agent and conversation details needed later, such as allowed tools, audience, and conversation identity. It returns that database row, or nothing if the parent turn is not currently running.

**Call relations**: ToolBridge.request calls this first. The returned parent row becomes the shared evidence used by later permission checks and by _admit when creating the child conversation and turn.

*Call graph*: called by 1 (request); 2 external calls (select, workspace_tx).


##### `ToolBridge._allowed`  (lines 133–145)

```
def _allowed(self, parent: sa.Row[tuple[object, ...]], tool: ToolDef) -> bool
```

**Purpose**: This function decides whether one specific tool should be visible or callable for the parent turn. It applies the rules for normal agents, subagents, action-based tools, and default subagent tools.

**Data flow**: It receives the parent turn’s database row and a tool definition. It compares the tool name against the parent agent’s tool list or the subagent profile’s tool list, adding any implied grants, which are permissions that come along with another permission. It returns true if the tool is allowed and false if it should be hidden or rejected.

**Call relations**: ToolBridge.request uses this while listing tools and before serving schema or execution requests. For special action-related tools, it asks _any_action_granted whether the parent has at least one action permission that makes those tools meaningful.

*Call graph*: calls 1 internal fn (_any_action_granted); called by 1 (request); 1 external calls (with_implied_grants).


##### `ToolBridge._any_action_granted`  (lines 147–163)

```
def _any_action_granted(self, parent: sa.Row[tuple[object, ...]]) -> bool
```

**Purpose**: This function answers a narrower question: does this parent turn have access to at least one registered bound action? Bound actions are named operations tied to project objects, and some bridge tools only make sense if at least one such action is available.

**Data flow**: It reads the bridge’s action registry and the parent’s agent or subagent permissions. For normal agents, it checks either broad access or the agent’s tool grants. For subagents, it checks the subagent profile, extra grants, implied grants, and default action rules. It returns true if any action is available under those rules.

**Call relations**: _allowed calls this when deciding whether to expose the object-action tool and related action-reading tools. It does not run any action itself; it only helps decide whether those bridge tools should be offered.

*Call graph*: called by 1 (_allowed); 1 external calls (with_implied_grants).


##### `ToolBridge._admit`  (lines 165–261)

```
async def _admit(self, run: RunToken, parent: sa.Row[tuple[object, ...]], request: ToolBridgeRequest) -> tuple[UUID, UUID] | None
```

**Purpose**: This function records a real tool execution request as a new child turn in the database. “Admit” here means the request has been accepted into the normal turn system, not that the tool has already run.

**Data flow**: It receives the run token, parent turn row, and bridge request. It builds stable IDs from the workspace, parent turn, and request ID, packages the requested tool and arguments as an intent, and opens a database transaction. Inside that transaction it re-checks that the parent turn is still running, inserts the child conversation and child turn if they do not already exist, verifies that a reused request ID still refers to the exact same call, marks the queued turn as ready for dispatch, and returns the child turn and conversation IDs. If the parent is no longer running, it returns nothing.

**Call relations**: ToolBridge.request calls this when a sandbox wants to execute a tool. After _admit has safely written the child turn, request hands the returned IDs to _enqueue so the workflow runner can process the turn.

*Call graph*: called by 1 (request); 9 external calls (__init__, TypeAdapter, select, update, workspace_tx, current_traceparent, authority_member_id, turn_id_for, uuid5).


##### `ToolBridge._enqueue`  (lines 263–291)

```
async def _enqueue(self, workspace_id: UUID, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This function asks the DBOS workflow system to run the newly admitted child turn. DBOS is the background workflow engine used here to run queued work reliably.

**Data flow**: It receives the workspace ID, turn ID, and conversation ID. It builds queue options that identify the express queue, turn workflow, workflow ID, and application version, then calls the workflow client. If the enqueue operation is cancelled or fails, it clears the database marker that said dispatch had been enqueued, so the queued turn is not left pretending it is already scheduled. On ordinary enqueue failure, it also logs the problem for later diagnosis.

**Call relations**: ToolBridge.request calls this right after _admit. It is the handoff point between database admission and actual background execution. If enqueueing fails, the database cleanup allows another recovery path to retry later.

*Call graph*: called by 1 (request); 3 external calls (update, workspace_tx, log).


##### `ToolBridge._terminal`  (lines 293–302)

```
async def _terminal(self, turn_id: UUID) -> ToolBridgeResponse
```

**Purpose**: This function waits for the child turn to reach a final result and converts that final state into a bridge response. It also stops the child turn if it becomes parked, meaning it is waiting in a way the bridge cannot complete normally.

**Data flow**: It receives the child turn ID and opens a live tail, which is a stream of turn updates. As frames arrive, it watches for either a terminal frame, meaning the turn has ended, or a parked frame, meaning the turn is stuck waiting. For a terminal frame it delegates to _response. For a parked frame it cancels the child turn and returns a failure message.

**Call relations**: ToolBridge.request calls this after enqueueing the child turn. It relies on the turn tailer to hear about progress, calls _response to interpret a normal ending, and calls the cancellation helper if the turn parks instead of finishing.

*Call graph*: calls 1 internal fn (_response); called by 1 (request); 2 external calls (__init__, cancel_one_turn).


##### `ToolBridge._response`  (lines 304–316)

```
def _response(self, terminal: TerminalFrame) -> ToolBridgeResponse
```

**Purpose**: This function turns a finished child turn into the success-or-failure shape expected by the sandbox bridge. It preserves useful error detail and parses successful output as JSON when possible.

**Data flow**: It receives a terminal frame from a finished turn. If the status is not done, it builds a readable error from the error class, error message, or final text and returns a failure response. If the status is done, it tries to read the terminal text as JSON; if that fails, it keeps the text as-is. It validates the result as a JSON-compatible value and returns a success response.

**Call relations**: _terminal calls this when it sees that the child turn has ended. This is the final translation step between the turn system’s terminal record and the sandbox-facing ToolBridgeResponse.

*Call graph*: called by 1 (_terminal); 4 external calls (__init__, __init__, loads, TypeAdapter).


### `core/src/ufo/runtime/tools/bridge.py`

`io_transport` · `live-turn sandbox tool bridge setup and request handling`

A sandbox is deliberately isolated, so it cannot freely reach into the main system. This file is the agreed “menu and order form” for that boundary. It says what a tool-bridge request must look like, what responses look like, and which tool names are allowed through the bridge.

The central request model, ToolBridgeRequest, carries a unique request ID, an action such as “list,” “get_schema,” or “execute,” an optional tool name, and optional arguments. Its validation rules prevent confusing requests, such as asking to list tools while also naming one specific tool. This matters because the bridge is a trust boundary: unclear or malformed requests should be rejected before they reach real tool code.

The file also defines small response shapes for success and failure, plus list-result shapes for showing available tools. ToolBridgeRequester is a protocol, meaning it describes the shape of an object that can serve bridge requests without saying which concrete class must do it.

Finally, bridge_tools builds the actual set of tools exposed over this bridge. It combines built-in object tools with selected extension or connector tools from manifests, then checks them with ToolRegistry. Bound object actions are intentionally not exposed as separate tools; they are reached through object_action, like using one service desk instead of many private side doors.

#### Function details

##### `ToolBridgeRequest._matches_action`  (lines 52–58)

```
def _matches_action(self) -> 'ToolBridgeRequest'
```

**Purpose**: This validation step makes sure a bridge request’s fields match the kind of action being asked for. It prevents requests that are ambiguous or missing required information before they travel deeper into the system.

**Data flow**: It starts with a parsed ToolBridgeRequest containing an action, maybe a tool name, and maybe arguments. If the action is “list,” it checks that no tool name or arguments were supplied, because listing all tools needs neither. If the action is “get_schema” or “execute,” it checks that a tool name is present. If the request is valid, it returns the same request object; if not, it raises a validation error.

**Call relations**: This is called automatically by Pydantic, the data-checking library, after a ToolBridgeRequest is created from incoming data. It acts as the gatekeeper before any ToolBridgeRequester implementation receives the request and decides what to do with it.


##### `ToolBridgeRequester.request`  (lines 92–92)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This declares the one operation that any tool-bridge requester must provide: take a signed live-run identity and a validated bridge request, then return either a success result or an error. It is a contract for other code to implement.

**Data flow**: It receives a RunToken, which represents the authorized live run making the request, and a ToolBridgeRequest, which says what tool action is wanted. An implementing class uses that information to list tools, return a schema, or execute a tool. The result comes back as either ToolBridgeSuccess with a JSON-compatible result or ToolBridgeFailure with an error message.

**Call relations**: This method is not implemented here; the file only states that bridge requester objects must have it. Other runtime code can depend on this shape without caring which concrete class performs the request, much like asking for “anything with a send button” rather than a specific phone model.


##### `bridge_tools`  (lines 95–111)

```
def bridge_tools(manifests: tuple[Manifest, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: This builds the approved set of tools that the sandbox bridge is allowed to expose. It keeps the bridge’s surface small and predictable by only including built-in object tools and selected unbound extension or connector tools with approved names.

**Data flow**: It receives a tuple of manifests, which describe extension and connector tools available to the runtime. First it creates the built-in object-related tools from ObjectVerbs. Then it walks through each manifest’s tools and each connector’s tools, keeping only tools that are not bound to a particular object and whose names are in the bridge’s allow-list. It combines those tools, constructs a ToolRegistry to validate the final set, and returns them as a tuple.

**Call relations**: This function is used when the bridge’s callable menu is being assembled. It calls ObjectVerbs.__init__ to obtain the built-in object verbs, and it calls ToolRegistry.__init__ as a consistency check for the combined tool list before handing that list back to the bridge setup code.

*Call graph*: 2 external calls (__init__, __init__).


### Tool runtime foundations
Package signposts, execution context, and registry definitions establish how tools are described, looked up, authorized, and safely run.

### `core/src/ufo/host/tools/__init__.py`

`other` · `cross-cutting`

This file is a small doorway into the `ufo.host.tools` area of the project. It does not define any functions or classes itself. Its job is to make the folder importable as a Python package and to document, in one sentence, the kind of code a reader should expect to find nearby. In plain terms, this package is where the project keeps the pieces that let the host expose and run “tools” — actions the system can call on demand. The docstring points to three main ideas: a registry, which is like a directory of available tools; a handler context, which is the information a tool needs while it is running; and a built-in tool set, which are tools supplied by the project itself. Without this file, older Python packaging styles and some tooling might not clearly recognize this directory as a package, and newcomers would lose a useful signpost about the purpose of the surrounding files.


### `core/src/ufo/runtime/tools/__init__.py`

`other` · `import time`

This file contains only a module docstring: a short piece of text that describes the purpose of the package. In plain terms, it tells readers that this part of the project is about the shared rules for tools: what a tool is expected to look like, what information is available when a tool is run, and how tools are registered so other parts of the system can find or call them.

There is no executable code here. Nothing is created, transformed, sent, or stored by this file itself. Its value is organizational. Like a label on a drawer, it helps people understand what should be inside this package before they open the other files.

Without this file, the surrounding package might still work depending on the Python version and packaging setup, but the clear human-facing explanation would be missing. It also helps tools that inspect Python packages recognize this directory as an intentional module area.


### `core/src/ufo/runtime/tools/context.py`

`orchestration` · `tool execution`

A tool in this system should not be able to reach anything it was not deliberately given. This file is the boundary object that makes that possible. Think of ToolContext like a checked-out toolbox: it contains only the keys, IDs, storage access, sandbox access, account grants, and helper services that this one tool call is allowed to use.

The file also standardizes how tools talk back. ToolResult can contain text or images. ToolFailure gives failures a consistent shape, including what was attempted, what already changed, and short diagnostic details. That matters because an AI agent may retry a failed step; it needs to know whether retrying would duplicate work.

Several helpers protect sensitive or shared resources. Artifact helpers measure and upload files without silently changing bytes. Preview helpers store screenshots or rendered images. Connector helpers choose exactly which connected external account a tool may use, respecting private member accounts versus accounts shared with an agent. Speaker and admin helpers make sure actions that need a real requesting member do not run under vague workspace authority.

The file also defines interfaces for spawning subagents and controlling background child turns, plus a cleanup registry so temporary resources such as browser connections are closed when the turn ends.

#### Function details

##### `UnknownSubagentProfile.__init__`  (lines 157–162)

```
def __init__(self, requested: str, registered: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when code asks for a subagent profile that is not registered. The message names the bad profile and shows the valid choices so the caller can correct the request.

**Data flow**: It receives the requested profile name and the list of registered profile names. It turns those into a human-readable exception message and stores both pieces of information on the exception for later inspection.

**Call relations**: The subagent registry calls this when a profile lookup fails. Instead of letting a plain missing-key error escape, the registry hands back a useful explanation that can be shown to the model or recorded in telemetry.

*Call graph*: called by 1 (get).


##### `SpawnPayloadRejected.__init__`  (lines 173–177)

```
def __init__(self, target: str, keys: str, detail: str) -> None
```

**Purpose**: Builds a clear error when a spawn target exists, but the input payload does not match what that target accepts. This tells the model what keys are expected and what was wrong.

**Data flow**: It receives the target name, a description of accepted keys, and a validation detail. It combines them into an exception message and keeps the same facts as fields.

**Call relations**: The subagent spawning code calls this after validating a child task’s payload. It separates “you named the wrong target” from “you named the right target but gave it the wrong data,” which makes retries more repairable.

*Call graph*: called by 1 (_validated).


##### `SpawnModelRejected.__init__`  (lines 188–190)

```
def __init__(self, message: str, requested: str) -> None
```

**Purpose**: Creates an error for a rejected model choice when spawning a child turn. It preserves both the message and the model ID that was requested.

**Data flow**: It takes a ready-made explanation and the requested model name. It initializes the exception with the explanation and stores the requested model for structured handling.

**Call relations**: The class methods in the same class use this constructor to create specific rejection cases. The subagent runtime raises these errors when a requested model cannot safely or honestly be applied.


##### `SpawnModelRejected.unknown`  (lines 193–198)

```
def unknown(cls, requested: str, models: tuple[str, ...]) -> 'SpawnModelRejected'
```

**Purpose**: Creates a model rejection for a model ID that this deployment does not serve. It includes the known model IDs so the caller can pick a valid one.

**Data flow**: It receives the requested model and the tuple of available models. It formats a message listing the valid models and returns a SpawnModelRejected exception object.

**Call relations**: The subagent runtime configuration code calls this before creating a child turn. This prevents a child from being recorded with a model that will fail later during setup.

*Call graph*: called by 1 (_child_runtime_config).


##### `SpawnModelRejected.pinned_tree`  (lines 201–206)

```
def pinned_tree(cls, requested: str, pinned: str) -> 'SpawnModelRejected'
```

**Purpose**: Creates a model rejection when the whole turn tree is already pinned to one model. This avoids pretending that a child ran on a different requested model.

**Data flow**: It receives the requested model and the already-pinned model. It produces an exception explaining that every child must keep the existing pinned model.

**Call relations**: The subagent runtime configuration code calls this when a parent turn tree has a fixed model choice. The rejection happens at spawn time, while the caller can still retry without a model override.

*Call graph*: called by 1 (_child_runtime_config).


##### `SpawnModelRejected.own_account`  (lines 209–214)

```
def own_account(cls, requested: str, target: str) -> 'SpawnModelRejected'
```

**Purpose**: Creates a model rejection when the spawn target runs on the member’s own provider account. In that case, this system cannot force a specific hosted model ID.

**Data flow**: It receives the requested model and target name. It returns an exception explaining that the member-owned account chooses from its own available models.

**Call relations**: The subagent spawning flow calls this when a caller tries to pin a model for a target backed by the member’s connected ChatGPT or Claude account.

*Call graph*: called by 1 (spawn).


##### `UnknownSpawnTarget.__init__`  (lines 221–228)

```
def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when a spawn request names no known profile or workspace agent. The message lists both valid profile names and valid agent names.

**Data flow**: It receives the requested target, available profiles, and available agents. It formats those into one exception message and stores the values on the exception.

**Call relations**: The subagent resolver and agent-spawn checker call this when a target cannot be found. It gives the model enough information to retry with a real target instead of guessing.

*Call graph*: called by 2 (_require_agent_spawn, _resolve).


##### `SpawnNeedsOwnModelKey.__init__`  (lines 242–250)

```
def __init__(self, requested: str, connect_url: str | None=None) -> None
```

**Purpose**: Builds an error explaining that a coding-style spawn target needs the member to connect their own model provider account first. It can include a direct URL to the credentials page.

**Data flow**: It receives the requested target and an optional base URL. It builds a message telling the user where to connect an account and stores the requested target.

**Call relations**: The subagent spawning flow calls this when the selected profile depends on the member’s own ChatGPT or Claude account but neither is connected.

*Call graph*: called by 1 (spawn).


##### `AmbiguousSpawnTarget.__init__`  (lines 257–262)

```
def __init__(self, requested: str) -> None
```

**Purpose**: Builds an error when a bare spawn target name matches both a profile and an agent. It tells the caller to use an explicit prefix.

**Data flow**: It receives the ambiguous name. It creates a message suggesting the exact forms `profile:name` or `agent:name` and stores the name.

**Call relations**: The subagent target resolver calls this before spawning. It prevents accidentally running the wrong kind of child task when two namespaces contain the same name.

*Call graph*: called by 1 (_resolve).


##### `clipped`  (lines 280–286)

```
def clipped(value: str, limit: int) -> str
```

**Purpose**: Shortens long diagnostic text to a safe size and appends a standard notice saying how much was removed. This keeps tool errors readable and bounded.

**Data flow**: It receives a string and a character limit. If the string fits, it returns it unchanged; if not, it returns the beginning plus a truncation notice.

**Call relations**: Validation methods for command output, failure summaries, and provider errors call this. It gives all cut-off messages the same format, so the model can recognize that text was shortened.

*Call graph*: called by 3 (_bound_stream, _bound_provider, _nonempty_summary).


##### `CommandDiagnostics._bound_stream`  (lines 304–305)

```
def _bound_stream(cls, value: str) -> str
```

**Purpose**: Limits captured command output so failed shell commands cannot flood the tool result. It applies the same limit to both standard output and standard error.

**Data flow**: It receives one output stream as text. It passes that text to `clipped` with the command-stream limit and returns the bounded version.

**Call relations**: Pydantic, the data validation library, calls this automatically when a CommandDiagnostics object is created. It relies on `clipped` to enforce the shared truncation style.

*Call graph*: calls 1 internal fn (clipped).


##### `ToolFailure._nonempty_summary`  (lines 340–341)

```
def _nonempty_summary(cls, value: str) -> str
```

**Purpose**: Ensures every tool failure has a useful summary. Empty or whitespace-only summaries become a fixed notice saying no reason was recorded.

**Data flow**: It receives the summary text, trims it, substitutes a default message if needed, clips it to the summary limit, and returns the cleaned text.

**Call relations**: Pydantic calls this when creating a ToolFailure. It uses `clipped` so long failure summaries do not exceed the model-facing budget.

*Call graph*: calls 1 internal fn (clipped).


##### `ToolFailure._bound_applied`  (lines 345–346)

```
def _bound_applied(cls, value: tuple[AppliedEffect, ...]) -> tuple[AppliedEffect, ...]
```

**Purpose**: Limits how many already-applied effects a failure reports. This keeps a failure response from becoming too large after a large batch operation.

**Data flow**: It receives the tuple of applied effects. It returns only the first allowed number of entries.

**Call relations**: Pydantic calls this while building a ToolFailure. It protects the standard failure format used by tool handlers.


##### `ToolFailure._bound_provider`  (lines 350–351)

```
def _bound_provider(cls, value: str | None) -> str | None
```

**Purpose**: Limits structured error text from an outside provider, such as an API service. This keeps third-party diagnostics useful but bounded.

**Data flow**: It receives optional provider text. If there is no text, it returns None; otherwise it clips the text to the provider-error limit.

**Call relations**: Pydantic calls this during ToolFailure validation. It uses `clipped`, matching the same truncation behavior used for summaries and command streams.

*Call graph*: calls 1 internal fn (clipped).


##### `ToolFailure.result`  (lines 353–358)

```
def result(self, *, untrusted: bool=False) -> ToolResult
```

**Purpose**: Converts a structured ToolFailure into a ToolResult that a tool handler can return. This gives all failed tools the same model-facing response shape.

**Data flow**: It reads the failure object, serializes it as JSON text, wraps that text in a TextContent block, and returns a ToolResult marked as an error. It can also mark the result as untrusted data.

**Call relations**: Tool handlers use this when they need to return a failure rather than raise an unexpected exception. It creates TextContent and ToolResult objects as the final handoff back to the tool engine.

*Call graph*: 2 external calls (__init__, __init__).


##### `Spawn.__call__`  (lines 441–451)

```
async def __call__(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False, model: str
```

**Purpose**: Defines the interface for starting a child task, called a subagent. It describes the inputs needed to choose a target, validate the payload, optionally run in the background, and optionally pin a model.

**Data flow**: A caller supplies the target name, payload, background flags, idempotency key, display name, interruption behavior, and optional model. An implementation validates and runs the child turn, then returns a SpawnResult.

**Call relations**: This is a protocol method, meaning this file defines the contract but not the implementation. ToolContext carries an object matching this interface so tools can delegate work without knowing how subagents are implemented.


##### `SubagentControl.result`  (lines 462–462)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Defines the interface for reading the final result of an already-spawned background subagent. It is used when the child has finished and the caller wants its validated output.

**Data flow**: It takes a child turn ID. An implementation looks up that child and returns a SpawnResult containing its terminal state and output if available.

**Call relations**: This protocol method is supplied through ToolContext by the subagent system. Tools that manage background work call it after spawning or reconnecting to a child.


##### `SubagentControl.wait`  (lines 464–464)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Defines the interface for waiting on one or more background subagents for a bounded period. It reports which children have reached a terminal state.

**Data flow**: It receives a tuple of child turn IDs. An implementation waits as appropriate and returns a tuple of SubagentStatus records.

**Call relations**: ToolContext exposes this through its `subagents` field. Lifecycle tools use it when a parent turn wants to check on several background children.


##### `SubagentControl.cancel`  (lines 466–466)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Defines the interface for stopping a running background subagent. It returns the child’s resulting status.

**Data flow**: It receives one child turn ID. An implementation requests cancellation and returns a SubagentStatus describing the result.

**Call relations**: Tools that supervise background subagents call this through ToolContext. The actual cancellation behavior lives in the subagent subsystem, not in this file.


##### `SubagentControl.message`  (lines 468–468)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus
```

**Purpose**: Defines the interface for sending a follow-up message to a live or idle child subagent. The deduplication key helps avoid sending the same message twice after a retry.

**Data flow**: It receives the child turn ID, message text, and deduplication key. An implementation delivers or schedules the message and returns the child’s status.

**Call relations**: ToolContext exposes this protocol to tools that need an ongoing conversation with a background child. The idempotency story matches the spawn system’s retry-safe behavior.


##### `TurnCleanup.register`  (lines 482–483)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous cleanup action to be run when the turn ends. Tools use this after opening a per-turn resource such as a browser connection.

**Data flow**: It receives a no-argument async close function. It appends that function to the cleanup list and returns nothing.

**Call relations**: Tool code registers closers here during tool execution. Later, the turn loop drains the registry so resources do not leak beyond the turn.


##### `TurnCleanup.drain`  (lines 485–491)

```
async def drain(self) -> None
```

**Purpose**: Runs all registered cleanup actions, newest first, and logs failures without stopping the rest. This makes teardown reliable even when one resource fails to close.

**Data flow**: It repeatedly removes the last registered closer, awaits it, and catches any exception. If a closer fails, it writes a log entry and continues.

**Call relations**: The turn loop calls this at the end of a turn. It uses the project logging helper to record cleanup failures while still closing remaining resources.

*Call graph*: 1 external calls (log).


##### `_speaker_required`  (lines 503–523)

```
def _speaker_required(subject: str, withheld: Sequence[Grant], audience: Audience) -> SpeakerRequired
```

**Purpose**: Creates a SpeakerRequired error when a connector account exists but cannot be used because the call did not identify the right member. It may name account owners when it is safe to do so.

**Data flow**: It receives a subject description, withheld private grants, and the conversation audience. It decides whether owner emails may be shown, builds a helpful message, and returns a SpeakerRequired exception.

**Call relations**: Connector account selection calls this when the missing piece is a member reference rather than an absent account. It helps the engine ask the model to retry with `requested_by` where that can fix the call.

*Call graph*: called by 1 (connector_connection); 2 external calls (__init__, startswith).


##### `measure_file`  (lines 536–548)

```
async def measure_file(sandbox: Sandbox, scoped: str) -> MeasuredFile
```

**Purpose**: Measures a file inside the sandbox before sharing or uploading it. It records size, SHA-256 digest, and whether the file appears to be text.

**Data flow**: It receives a sandbox and a scoped path. It runs a portable shell preflight command inside the sandbox, parses the JSON result, and returns a MeasuredFile. If the path is not a normal readable file, it raises an error.

**Call relations**: Artifact-sharing code uses this before upload. It calls the sandbox shell and `shell_path` helper so the measurement happens where the file actually lives.

*Call graph*: calls 1 internal fn (bash); 3 external calls (__init__, loads, shell_path).


##### `store_artifact`  (lines 551–586)

```
async def store_artifact(sandbox: Sandbox, blob: WorkspaceBlobStore, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Uploads a measured sandbox file into the workspace artifact store. It supports both cloud object storage and a local filesystem store.

**Data flow**: It receives the sandbox, blob store, file path, destination key, measured size, and digest. For S3-style storage, it gets a presigned upload URL and asks the sandbox to `curl` the file directly; for filesystem storage, it streams the file through the blob store. It returns nothing or raises if the upload fails.

**Call relations**: Higher-level artifact sharing uses this after measuring a file. It calls blob-store upload helpers, sandbox shell execution, sandbox file reading, and quoting/path helpers to move bytes safely.

*Call graph*: calls 4 internal fn (presigned_put, put_stream, bash, read_file); 3 external calls (b64encode, quote, shell_path).


##### `ToolContext.authority`  (lines 626–632)

```
def authority(self) -> ExecutionAuthority
```

**Purpose**: Answers whose authority this tool call is running under. If a live speaker is bound to the call, that member is the authority; otherwise the turn’s stored delegated authority is used.

**Data flow**: It reads `speaker_member_id` and the turn’s `on_behalf_of_member_id`. It returns a MemberAuthority for a live speaker or converts the stored member ID into an ExecutionAuthority.

**Call relations**: Many permission decisions depend on this property. Other ToolContext methods use it to decide what the call may read or which connector accounts it may access.

*Call graph*: 2 external calls (__init__, authority_from_member_id).


##### `ToolContext.effective_audience`  (lines 635–645)

```
def effective_audience(self) -> Audience
```

**Purpose**: Determines the audience label that new writes should belong to. This prevents private or cross-organization conversation facts from being stamped into the wrong memory space.

**Data flow**: It reads the call authority and current audience. If a member is acting in the shared workspace audience, it returns that member’s private conversation audience; otherwise it keeps the existing audience.

**Call relations**: Write paths use this property when deciding where a tool’s output or memory belongs. It calls audience helpers to translate the acting member into the right conversation audience.

*Call graph*: 2 external calls (authority_member_id, conversation_audience).


##### `ToolContext.read_subjects`  (lines 648–657)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes the set of subjects this call may read: the conversation’s subjects plus the active member’s private subject when there is one. A subject is a label used to decide which stored facts are visible.

**Data flow**: It reads the current audience and authority. It expands the audience into readable subjects, adds the acting member’s subject if applicable, and returns the combined frozen set.

**Call relations**: Source and memory readers use this property to avoid leaking information across conversations or members. It calls helpers for audience subjects and member subject labels.

*Call graph*: 3 external calls (authority_member_id, audience_subjects, member_subject).


##### `ToolContext.store_preview`  (lines 659–705)

```
async def store_preview(self, sandbox_path: str, name: str, *, extension: str='png') -> StoredPreview | None
```

**Purpose**: Stores an image-like preview that a tool rendered inside the sandbox. If the preview cannot be measured or uploaded, it logs the problem and returns None because the preview is optional decoration.

**Data flow**: It receives a sandbox path, display name, and file extension. It measures the file size in the sandbox, creates a new artifact key, uploads the bytes either by presigned URL or stream, and returns a StoredPreview with the blob key and size.

**Call relations**: The sites extension calls this while composing share cards. The method uses sandbox commands, blob storage, UUIDs, and logging to put preview bytes in the artifact namespace owned by core.

*Call graph*: called by 1 (_compose); 5 external calls (__init__, quote, log, shell_path, uuid4).


##### `ToolContext.render_site_preview`  (lines 707–714)

```
async def render_site_preview(self, name: str, port: int, width: int, height: int) -> StoredPreview | None
```

**Purpose**: Asks the configured preview service to capture an image of a hosted sandbox port. It returns None when no preview service is configured.

**Data flow**: It receives a name, port, width, and height. It chooses the right conversation ID for the sandbox session, calls the site previewer, and returns the StoredPreview result or None.

**Call relations**: The sites extension calls this when it wants an illustration of a running site. ToolContext supplies the previewer so the tool does not need to know how preview capture is wired.

*Call graph*: called by 1 (_illustrate).


##### `ToolContext.source_reader`  (lines 716–726)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Builds a SourceReader describing who is asking to read synced source pages. It combines the agent ID, the live requesting member if any, and the subjects the call may read.

**Data flow**: It reads the turn’s agent ID, `speaker_member_id`, and `read_subjects`. It returns a SourceReader object containing those access facts.

**Call relations**: Memory and source extensions call this before listing or reading stored pages. It centralizes the access view so those extensions do not each rebuild the same permission logic.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 728–737)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images on this turn’s billing ledger. This lets image-provider extensions report provider-specific charges back to core accounting.

**Data flow**: It receives the model name, image count, and cost in micro-dollars. It opens a workspace database transaction and records image usage for the turn’s workspace and turn ID.

**Call relations**: The OpenRouter image extension calls this after generating images. The method delegates the actual ledger write to the billing accounting module.

*Call graph*: called by 1 (generate); 2 external calls (workspace_tx, record_image_usage).


##### `ToolContext.meter_videos`  (lines 739–747)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos on this turn’s billing ledger. This is parallel to image metering but for video outputs.

**Data flow**: It receives the model name, video count, and cost in micro-dollars. It opens a workspace database transaction and records video usage for the current workspace and turn.

**Call relations**: The OpenRouter video extension calls this after generating videos. Core owns the ledger write, while the extension supplies the price it learned from the provider.

*Call graph*: called by 1 (generate); 2 external calls (workspace_tx, record_video_usage).


##### `ToolContext.share_artifact`  (lines 749–833)

```
async def share_artifact(self, filename: str, data: bytes, subject: str | None=None, *, preview: StoredPreview | None=None) -> None
```

**Purpose**: Publishes a small in-memory file created by a tool as a shared artifact for the turn. It can also attach a previously stored raster preview.

**Data flow**: It receives a filename, bytes, optional subject, and optional preview. It checks size and preview safety, chooses a deterministic artifact key when an idempotency key exists, writes the blob, inserts a database row, cleans up the blob if the database insert fails, and optionally publishes artifact updates.

**Call relations**: The iMessage extension calls this to share generated connection artifacts. It uses database transactions, blob storage, media-type helpers, UUIDs, and logging so surfaces can later deliver the file through the normal artifact route.

*Call graph*: called by 1 (run); 8 external calls (now, select, workspace_tx, log, artifact_media_type, raster_image_media_type, uuid4, uuid5).


##### `ToolContext.speaker_is_admin`  (lines 835–845)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live requesting member is a workspace admin. If there is no live speaker, it returns False.

**Data flow**: It reads `speaker_member_id`. If absent, it returns False; otherwise it opens a workspace transaction and asks the seats subsystem whether that member is an admin in this workspace.

**Call relations**: Many object and extension operations call this before showing or applying admin-only behavior. It is a permission check used throughout request handling.

*Call graph*: called by 18 (_widens_for_admin, _visible_rows, restore, apply, delete, get, list, status, _credential_authorization, require_speaking_admin (+8 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.require_speaker`  (lines 847–860)

```
def require_speaker(self, gate: str='') -> UUID
```

**Purpose**: Requires the tool call to be tied to a real member and returns that member’s ID. If no member is named, it raises a special error that tells the engine the model can retry with `requested_by`.

**Data flow**: It reads `speaker_member_id` and an optional gate description. If a speaker exists, it returns the UUID; otherwise it raises SpeakerRequired with a repair-oriented message.

**Call relations**: Built-in tools, member operations, agent creation, and member-owned object actions call this when workspace authority is not specific enough. Admin checks and credential authorization build on it.

*Call graph*: called by 11 (add, apply, connect_account_handler, request_credentials_handler, _create, restore, apply, delete, _credential_authorization, require_speaking_admin (+1 more)); 1 external calls (__init__).


##### `ToolContext.require_speaking_admin`  (lines 862–872)

```
async def require_speaking_admin(self, gate: str) -> bool
```

**Purpose**: Requires both a live speaker and admin status. It asks the “who is speaking?” question first so missing `requested_by` is reported as a fixable missing speaker, not as a failed admin check.

**Data flow**: It receives a gate description, calls `require_speaker`, then calls `speaker_is_admin`. It returns True or False for admin status, while missing speaker raises SpeakerRequired.

**Call relations**: Admin-only tools and extensions call this before destructive or workspace-wide actions. It composes the simpler speaker and admin helpers in the safe order.

*Call graph*: calls 2 internal fn (require_speaker, speaker_is_admin); called by 11 (delete, request_credentials_handler, _resync, rebuild_page_facts_handler, _admin_billing, rebuild_report_digest_handler, delete, _apply_owned, _derive_manifest_identity, delete (+1 more)).


##### `ToolContext.agent_is_main`  (lines 874–885)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether the current turn’s agent is the workspace’s main agent. Some actions are only visible or allowed for the main agent.

**Data flow**: It opens a workspace database transaction, selects the `is_main` flag for the current agent in the current workspace, and returns it as a boolean.

**Call relations**: Member, workspace, and web-audience code call this when deciding visibility or grant behavior. It reads the database directly for the current turn’s agent.

*Call graph*: called by 6 (add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.agent_visibility`  (lines 887–899)

```
async def agent_visibility(self) -> AgentVisibility
```

**Purpose**: Reads whether the current agent is private or workspace-visible. It rejects any stored value outside those supported levels.

**Data flow**: It opens a workspace transaction, selects the agent visibility field, validates that it is `private` or `workspace`, and returns it.

**Call relations**: The sites extension calls this when redeploying a homepage. The method keeps database storage values aligned with the AgentVisibility type expected by callers.

*Call graph*: called by 1 (_redeploy_homepage); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 901–903)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for an extension credential slot. It returns a sealed authorization string or link data produced by the credential request service.

**Data flow**: It receives a credential slot name and payload. It first runs `_credential_authorization` to verify speaker, extension declaration, configured secret storage, and admin status, then calls `authorize` on the credential request service.

**Call relations**: The Slack extension calls this to create an OAuth authorization link. It delegates all gate checks to `_credential_authorization` so both begin and open flows share the same rules.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 1 (_oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 905–907)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens or verifies a previously sealed credential authorization for an extension slot. This is the counterpart to beginning an authorization request.

**Data flow**: It receives the slot name and sealed authorization value. It runs `_credential_authorization` for the same safety checks, then calls `open_authorization` on the credential request service.

**Call relations**: This method is available to tools that need to complete a stored credential authorization flow. It shares its permission checks with `begin_credential_authorization`.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext._credential_authorization`  (lines 909–917)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the shared safety checks for credential authorization. It makes sure a real admin speaker is acting, the extension declared the slot, and the deployment can store secrets.

**Data flow**: It receives a slot name. It requires a speaker, checks the current extension and declared credential slots, checks that credential requests are configured, verifies admin status, and returns the request service plus speaker member ID.

**Call relations**: `begin_credential_authorization` and `open_credential_authorization` both call this. It centralizes the credential gate so the two public methods cannot drift apart.

*Call graph*: calls 2 internal fn (require_speaker, speaker_is_admin); called by 2 (begin_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 919–931)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the external broker account ID that a connector tool should use. It is a convenience wrapper when the caller only needs the account ID, not the full connection record.

**Data flow**: It receives a provider name and optional account ID. It calls `connector_connection`, then returns the selected connection’s `account_id`.

**Call relations**: The sample connector execution code calls this. More advanced connector flows call `connector_connection` directly when they need grant and connection IDs too.

*Call graph*: calls 1 internal fn (connector_connection); called by 1 (_connector_execute).


##### `ToolContext.connector_connection`  (lines 933–981)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Selects the exact connector connection this tool call may use for an external provider. It respects member-private accounts, agent-shared accounts, requested account IDs, and ambiguity.

**Data flow**: It receives a provider and optional account ID. It asks `_connector_account_tiers` for usable private grants, usable shared grants, and withheld private grants. It either returns a ConnectorConnection, raises SpeakerRequired when naming a member could unlock a private account, or raises a clear ValueError for missing or ambiguous accounts.

**Call relations**: Connector tools and source tools call this before making external API calls. It uses `_speaker_required` for fixable private-account misses and constructs ConnectorConnection records for successful selections.

*Call graph*: calls 2 internal fn (_connector_account_tiers, _speaker_required); called by 3 (connector_account, call_external_tool, _resolved_account); 1 external calls (__init__).


##### `ToolContext.require_connector_connection`  (lines 983–1003)

```
async def require_connector_connection(self, selected: ConnectorConnection) -> None
```

**Purpose**: Rechecks that a previously selected connector connection is still valid. This protects against a revoke, disconnect, or sharing change that happens after selection but before the external side effect.

**Data flow**: It receives a ConnectorConnection chosen earlier. It reloads current private and shared grants for that provider and looks for an exact match on grant ID, connection ID, account ID, and owner. If none exists, it raises an error.

**Call relations**: Connector execution flows can call this right before contacting the broker. It uses `_connector_account_tiers` to make the final permission check against current state.

*Call graph*: calls 1 internal fn (_connector_account_tiers).


##### `ToolContext.connector_accounts`  (lines 1005–1011)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists the connected account IDs this call may use for one provider. This is useful when a tool needs to show choices or resolve an account by name.

**Data flow**: It receives a provider name. It gets private and shared usable grants from `_connector_account_tiers`, collects their account IDs, sorts them, and returns them as a tuple.

**Call relations**: Source tools call this while resolving which external account to use. It shares the same access rules as `connector_connection` by relying on the same tier helper.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 1013–1052)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant], list[Grant]]
```

**Purpose**: Splits active connector grants for a provider into three groups: private grants usable by the acting member, shared grants usable by the agent, and private grants currently withheld. This is the core account-permission calculation for connector tools.

**Data flow**: It receives a provider name. It checks that grant storage is available, finds the acting member from the call authority, loads active grants, filters to the provider, sorts usable private and shared grants, and decides whether to include other members’ private grants as withheld.

**Call relations**: `connector_connection`, `connector_accounts`, and `require_connector_connection` all call this. It is the shared rulebook that keeps connector account selection consistent across listing, choosing, and final rechecking.

*Call graph*: called by 3 (connector_accounts, connector_connection, require_connector_connection); 2 external calls (__init__, authority_member_id).


### `core/src/ufo/runtime/tools/registry.py`

`domain_logic` · `startup validation and runtime tool lookup`

A “tool” here is a callable ability the model can ask the system to run, such as reading data, writing to an outside service, or taking a final action. This file is the catalog format for those abilities. Each ToolDef ties together a public name, a plain description, an input model that describes what arguments the tool accepts, and the handler function that actually runs it.

The file also marks tools with safety information. For example, untrusted means the result may contain attacker-written text, so the engine must not treat it like instructions. side_effecting means the tool writes to the outside world, so the engine can attach an idempotency key, which is like a receipt number that helps avoid doing the same external action twice after a retry.

Some tools are not ordinary global tools. A bound tool is an object action, attached to a specific kind of object or one visible instance. Those actions use a special canonical identity like action:<kind>:<name> and are deliberately kept out of the normal wire registry.

ToolRegistry is the frozen catalog used at runtime. When it is created, it rejects duplicate names, reserved prefixes, invalid bound actions, and invalid presentation or final-action declarations. This makes startup fail loudly instead of letting an unsafe or ambiguous tool catalog reach the model.

#### Function details

##### `ToolDef.canonical_id`  (lines 100–105)

```
def canonical_id(self) -> str
```

**Purpose**: Returns the stable identity for a tool. A normal tool uses its own name, while an object-bound action gets a longer identity that includes the object kind, so allowlists, logging, and retry keys can refer to it unambiguously.

**Data flow**: It reads the ToolDef’s name and optional object binding. If there is no binding, it returns the name unchanged. If there is a binding, it builds and returns a string in the action:<kind>:<name> form.

**Call relations**: Other parts of the runtime can use this property when they need a single dependable identifier for a tool, whether it is a normal global tool or an object action. It does not call out to anything else; it simply derives the identifier from the ToolDef’s stored fields.


##### `ToolDef.schema`  (lines 107–120)

```
def schema(self, *, include_requested_by: bool=True) -> ToolSchema
```

**Purpose**: Builds the wire-facing description of a tool, meaning the shape of the tool call that can be shown to or sent by the model. It includes the tool’s name, description, and the JSON-style input schema created from its input model.

**Data flow**: It starts with the input_model attached to the ToolDef and asks it for a JSON schema. Unless told not to, it adds a requested_by field, which records the message reference that gave authority for member-specific actions. It then packages the name, description, and input schema into a ToolSchema object and returns it.

**Call relations**: ToolRegistry.schemas calls this for every registered tool when the engine needs the catalog to expose on the wire. This function hands the final packaging to ToolSchema.__init__, which creates the schema object used outside this registry.

*Call graph*: 1 external calls (__init__).


##### `validate_tool_declaration`  (lines 123–147)

```
def validate_tool_declaration(tool: ToolDef[Any], label: str) -> None
```

**Purpose**: Checks that one declared tool follows the project’s rules before the runtime accepts it. It prevents confusing or unreachable declarations, such as a button with no label, a profile-only tool exposed as a prepared user action, or a final action model that no terminal frame can carry.

**Data flow**: It receives a ToolDef and a human-readable label for error messages. It inspects the tool’s presentation settings, object binding, profile-only flag, and final-act model. If everything is consistent, it returns nothing; if something is invalid, it raises a ValueError explaining the problem.

**Call relations**: ToolRegistry.__post_init__ calls this during registry creation after checking broader name and prefix rules. In the bigger startup story, this is the per-tool inspection step that catches subtle declaration mistakes before any tool can be advertised or run.

*Call graph*: called by 1 (__post_init__).


##### `ToolRegistry.__post_init__`  (lines 154–174)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the whole tool registry immediately after it is created. This is the guardrail that makes sure the runtime starts with a clean, non-ambiguous tool catalog.

**Data flow**: It reads the tuple of ToolDef objects stored in the registry. It checks for duplicate names, bound object actions placed in the normal registry, names using the reserved action: prefix, and input models that already define the reserved requested_by field. Then it runs validate_tool_declaration on each tool. If all checks pass, the registry remains usable; otherwise creation fails with a ValueError.

**Call relations**: This runs automatically after a ToolRegistry dataclass instance is constructed. It calls validate_tool_declaration for the detailed per-tool rules, so invalid tools are rejected at startup rather than later during model interaction or tool dispatch.

*Call graph*: calls 1 internal fn (validate_tool_declaration).


##### `ToolRegistry.schemas`  (lines 176–177)

```
def schemas(self, *, include_requested_by: bool=True) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the public schemas for all registered tools. This is how the runtime turns its internal tool definitions into the list of callable tools the model can see.

**Data flow**: It receives an option saying whether to include the requested_by field. It walks through every ToolDef in the registry, asks each one to build its schema with that option, and returns the resulting ToolSchema objects as an immutable tuple.

**Call relations**: When the engine needs to advertise available tools, it calls this registry-level method instead of building schemas one by one. The work is delegated to each ToolDef.schema method, keeping the registry as the catalog and each tool definition responsible for describing itself.


##### `ToolRegistry.get`  (lines 179–183)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Looks up a tool definition by its name. This is the simple runtime doorway from a requested tool name to the ToolDef that says how to validate and run it.

**Data flow**: It receives a name string and scans the registry’s stored tools. If it finds a ToolDef with that name, it returns that ToolDef. If no tool matches, it raises a KeyError so the caller gets a clear “unknown tool” failure instead of silently doing the wrong thing.

**Call relations**: Tool dispatch code can call this when a model asks to run a named tool. Because ToolRegistry.__post_init__ already rejected duplicate names, this lookup can safely return the first match as the one intended definition.

## 📊 State Registers Touched

- `reg-effective-config` — The merged service settings that tell the process how this deployment should run.
- `reg-extension-registry` — The discovered set of installed extensions and the capabilities each extension contributes.
- `reg-tool-catalog` — The shared list of tools and actions agents may ask to run, including extension tools.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-credential-connections` — The encrypted outside-account credentials, reusable connections, and grants that let agents use them.
- `reg-environment-documents` — The saved per-agent run environment describing prompts, tools, skills, files, and model overrides.
- `reg-sandbox-handles` — The remembered sandbox workspaces and conversation sandbox handles used to resume or clean up execution.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-browser-sessions` — The active browser automation workbench for a turn, including Chrome sessions, tabs, and downloads.
- `reg-source-index` — The stored external sources, synced pages, permissions, indexing status, and retry/backoff state.
- `reg-artifact-blob-store` — The shared files, media blobs, previews, metadata, and signed-download records created by agent work.
- `reg-hosted-site-registry` — The saved hosted-site names, owners, visibility, ports, files, previews, and ingress routing state.
- `reg-notification-inbox` — The stored pending notifications and delivery state used to batch notices and wake conversations.
- `reg-usage-ledger-balance` — The money and usage ledger that tracks costs, prepaid balances, limits, exports, and billing status.
- `reg-workspace-change-log` — Durable per-conversation sandbox file-change snapshots and summaries used after tool execution and shown in workspace-change slots.
- `reg-egress-policy-cache-state` — Per-workspace egress-rule generation and cache-freshness state used by proxies to detect stale sandbox network-access rules.
- `reg-turn-resource-budget` — Per-turn context-window, token, image, reasoning, and cost/resource budgets derived before execution and consumed by prompt assembly, model calls, tools, and accounting.
- `reg-active-workflow-handles` — In-process handles for currently executing turns/workflows, including cancellation tokens and cleanup callbacks used to stop, tear down, or recover live work.
- `reg-workflow-checkpoints` — Durable per-turn workflow checkpoints, serialized runner state, and step/tool-output idempotency records used to resume, cancel, or recover work without rerunning completed actions.
- `reg-rate-limit-buckets` — Shared throttling counters, leases, and cooldown state for ingress, provider/model calls, connector actions, and background workers, separate from spend-cap accounting.
- `reg-proposal-review-state` — Durable reviewable-change proposals with source/target digests, creator, approval state, and publication lifecycle outside the self-improvement prompt-promotion loop.
