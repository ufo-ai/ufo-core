# Tool registry, tool dispatch, and core built-ins  `stage-11`

This stage is the tool desk for the agent during the main work loop. When the model wants to do something outside plain text, such as read a file or run a command, it must ask for a named tool. The tools package marks this area of the codebase and groups the tool directory, the running context, and the built-in tools.

The registry is like a catalog. It lists each tool, its name, its description, and the shape of the input it accepts, so the model can be shown valid options and unclear names can be rejected. The context is the safety envelope passed to a tool when it runs. It controls what the tool may access, such as files, browser sessions, accounts, credentials, subagents, memory, and billing.

The built-ins are the main workbench: shell commands, file edits, search, sharing, user questions, skills, account connections, and subagent control. The todos extension adds a checklist tool pack, letting agents plan multi-step work and show progress in the UI.

## Files in this stage

### Tool package foundations
The package marker introduces the tools area before the safe execution context and registry define what tools can access and how they are advertised.

### `core/src/ufo/tools/__init__.py`

`other` · `import time`

This is a small package introduction file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it does not define any code or re-export any names. Its only content is a short note explaining the purpose of the surrounding `tools` package. That package is where the project keeps the pieces that let the system know what tools exist, how a tool call gets the context it needs, and which tools are available by default. Think of this file like a label on a drawer: it does not contain the tools itself, but it tells a reader what kind of things to expect inside the drawer.


### `core/src/ufo/tools/context.py`

`domain_logic` · `tool execution and turn cleanup`

A tool is powerful: it might read files, call outside services, open a browser, ask for credentials, or start another agent. This file is the guardrail and toolbox for that work. It gives each tool a ToolContext, which is like a badge plus a backpack: the badge says whose authority the tool is using and what audience it may write to, while the backpack contains approved services such as the sandbox, blob store, search provider, browser provider, grants, and cleanup hooks.

The file also defines the shapes of tool output. A tool can return text or an image, and it can mark output as an error or as untrusted data so the model does not accidentally treat outside content as instructions.

Several helpers enforce boundaries. Audience helpers decide what information can be read or written without leaking private room data into another conversation. Credential helpers require a real speaking workspace admin before a tool can authorize or store secrets. Connector helpers choose which connected external account a turn is allowed to use, preferring the acting member’s private account over shared accounts and failing clearly when the choice is missing or ambiguous.

Finally, TurnCleanup lets tools register resources, such as browser connections, to close at the end of the turn. Without this file, tools would lack a consistent permission boundary and could easily leak data, reuse the wrong account, or leave resources open.

#### Function details

##### `Spawn.__call__`  (lines 136–143)

```
async def __call__(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False) -> SpawnResult
```

**Purpose**: Describes how a tool can start a named subagent to do a smaller job. The caller provides the subagent profile and input, and may choose whether to wait for the result or let it run in the background.

**Data flow**: A profile name, input payload, background flag, optional deduplication key, and delivery choice go in. An implementation elsewhere validates the input, starts or reconnects to the child turn, and returns a SpawnResult containing the child turn identity and, when available, its final output.

**Call relations**: This file only defines the promise that spawn implementations must keep. Tool handlers receive this callable through ToolContext and use it when they need to delegate work to a child agent instead of doing everything in one tool call.


##### `SubagentControl.result`  (lines 153–153)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Describes how a tool can fetch the finished result of a background subagent. It is used when the parent wants the child’s exact terminal state and validated output.

**Data flow**: A child turn id goes in. An implementation elsewhere looks up that child, confirms its state, and returns a SpawnResult with its terminal information and output when available.

**Call relations**: This is part of the SubagentControl interface attached to ToolContext. Lifecycle tools for background subagents call on this operation when a parent turn asks, “what did that child produce?”


##### `SubagentControl.wait`  (lines 155–155)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes how a tool can wait for one or more background subagents, but only within the bounds of that tool call. It lets a parent pause for children without permanently owning their results.

**Data flow**: A tuple of child turn ids goes in. An implementation elsewhere waits for their terminal states or a bounded stopping point, then returns a tuple of SubagentStatus records summarizing what happened.

**Call relations**: This belongs to the same background-subagent control surface as result, cancel, and message. A tool uses it when it has already spawned children and wants a short status-gathering step before continuing.


##### `SubagentControl.cancel`  (lines 157–157)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes how a tool can stop a running background subagent. It gives the parent a controlled way to end delegated work that is no longer needed.

**Data flow**: A child turn id goes in. An implementation elsewhere requests cancellation for that child and returns a SubagentStatus showing the resulting terminal status and message.

**Call relations**: ToolContext exposes this through SubagentControl so subagent lifecycle tools can cleanly stop children instead of leaving them to run after the parent has changed direction.


##### `SubagentControl.message`  (lines 159–159)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus
```

**Purpose**: Describes how a tool can send a follow-up message to an already-spawned background subagent. The deduplication key prevents the same follow-up from being admitted twice after a retry.

**Data flow**: A child turn id, message text, and deduplication key go in. An implementation elsewhere delivers or reuses the same message admission and returns the child’s current status.

**Call relations**: This rounds out the background-subagent control API. Parent tools use it after spawning a child when they need to steer or update the child rather than simply wait for it.


##### `TurnCleanup.register`  (lines 173–174)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous close function to the list of things that must be cleaned up when the turn ends. This is how tools make sure opened resources are not forgotten.

**Data flow**: A no-argument async closer goes in. The function stores it in the cleanup list and returns nothing; the visible change is that the closer will be run later.

**Call relations**: Tools call this when they first open a per-turn resource, such as a browser connection. TurnCleanup.drain later walks this list and calls the registered closers.


##### `TurnCleanup.drain`  (lines 176–182)

```
async def drain(self) -> None
```

**Purpose**: Closes every resource registered for the turn, working backward from the most recently registered one. If one cleanup fails, it logs the failure and continues so the rest still get closed.

**Data flow**: It reads the stored closer list. It repeatedly removes one closer, awaits it, logs any exception through the observability logger, and ends with the list empty.

**Call relations**: The main turn loop drains this registry at turn end. It calls the close functions registered earlier through TurnCleanup.register and uses ufo.o11y.log when a closer raises an error.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 219–227)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Identifies the member whose authority this tool call may use. It prefers the live speaker, and otherwise falls back to the member the turn is acting for, such as in a scheduled run or delegated subagent.

**Data flow**: It reads speaker_member_id and on_behalf_of_member_id from the context. It returns the speaker if present, otherwise the on-behalf-of member, or None if neither exists.

**Call relations**: Many other ToolContext helpers depend on this answer. Audience and connector logic use it to decide what private information or accounts are available to the current call.


##### `ToolContext.effective_audience`  (lines 230–240)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides the audience label to put on new writes made by this tool. In a workspace-shared conversation, it narrows writes to the acting member’s conversation audience so private requester-scoped notes do not leak into unrelated rooms.

**Data flow**: It reads the current audience and acting member. If there is no acting member, or the current audience is not the shared workspace audience, it returns the existing audience; otherwise it builds and returns that member’s conversation audience.

**Call relations**: This property uses conversation_audience when shared-space writes need to be scoped to a person. Other tool and object code can rely on it to stamp writes with the correct memory boundary.

*Call graph*: 1 external calls (conversation_audience).


##### `ToolContext.read_subjects`  (lines 243–252)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes the set of subjects this tool may read from. A subject is a plain label for an information space, such as a conversation or a member’s private space.

**Data flow**: It converts the conversation audience into readable subjects, then adds the acting member’s private subject if there is an acting member. The result is a frozen set that callers cannot accidentally modify.

**Call relations**: It calls audience_subjects for the conversation side and member_subject for the requester’s private side. ToolContext.source_reader uses this set when asking source and memory extensions what synced content may be shown.

*Call graph*: 2 external calls (audience_subjects, member_subject).


##### `ToolContext.source_reader`  (lines 254–264)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Builds a small permission object that says who is asking to read synced source pages. It includes the current agent, the live speaking member, and the subjects this context may read.

**Data flow**: It reads the turn’s agent id, speaker_member_id, and read_subjects. It packages those into a SourceReader object and returns it.

**Call relations**: Memory and source extensions call this before listing or fetching pages and memory objects. It hands them a compact, consistent view of the caller’s read permissions.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 266–275)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images against this turn’s billing ledger. Image providers price their own work, but core records the spend so it is tied to the right workspace and turn.

**Data flow**: A model name, image count, and cost in micro-dollars go in. The function opens a workspace database transaction and writes an image-usage record for this turn, producing no returned value.

**Call relations**: The OpenRouter image extension calls this after generating images. This function hands the actual ledger write to record_image_usage inside workspace_tx.

*Call graph*: called by 1 (generate); 2 external calls (record_image_usage, workspace_tx).


##### `ToolContext.meter_videos`  (lines 277–285)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos against this turn’s billing ledger. This keeps video spending accountable even though video pricing is not handled like normal token-based model use.

**Data flow**: A model name, video count, and cost in micro-dollars go in. The function opens a workspace database transaction and writes a video-usage record for this turn, returning nothing.

**Call relations**: The OpenRouter video extension calls this after generating videos. This function delegates the ledger write to record_video_usage inside workspace_tx.

*Call graph*: called by 1 (generate); 2 external calls (record_video_usage, workspace_tx).


##### `ToolContext.speaker_is_admin`  (lines 287–297)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live speaking member is a workspace admin. If there is no live speaker, it returns false so background work cannot borrow admin power.

**Data flow**: It reads speaker_member_id and the turn’s workspace id. If no speaker is present it returns False; otherwise it opens a workspace transaction, asks member_is_admin, and returns that boolean result.

**Call relations**: Many object and credential operations call this before workspace-wide changes. ToolContext._credential_authorization also uses it to restrict credential authorization to live admins.

*Call graph*: called by 20 (_create, apply, delete, apply, delete, get, list, status, request_credentials_handler, _credential_authorization (+10 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 299–310)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether this turn belongs to the workspace’s main agent. Some actions are allowed only for, or behave differently around, the main agent.

**Data flow**: It reads the turn’s agent id and workspace id. It queries the database for that agent’s is_main flag and returns True or False.

**Call relations**: Agent, member, workspace, and web-audience code call this when deciding what the current agent is allowed to see or change. It uses a SQL select inside a workspace transaction.

*Call graph*: called by 8 (_create, apply, add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 312–314)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for storing or using an extension credential. It first verifies that the current speaker is allowed to authorize that credential slot.

**Data flow**: A credential slot name and payload go in. The function asks _credential_authorization for the credential request service and member id, then returns a sealed authorization string produced by that service.

**Call relations**: Coding and Slack extensions call this when they need to begin a credential or OAuth-style approval flow. It relies on _credential_authorization for all safety checks before handing off to CredentialRequests.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 316–318)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens and verifies a sealed credential authorization token. This is used to check that an authorization belongs to the right workspace, member, and credential slot.

**Data flow**: A credential slot and sealed authorization string go in. The function first runs _credential_authorization, then asks the credential request service to open the sealed value and returns the contained payload.

**Call relations**: This is the read-back half of the credential authorization flow. It uses the same safety gate as begin_credential_authorization so callers cannot open tokens outside the allowed context.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext.fulfill_credential_authorization`  (lines 320–325)

```
async def fulfill_credential_authorization(self, slot: str, sealed: str, plaintext: str) -> None
```

**Purpose**: Completes a credential authorization by storing the supplied secret value in the current workspace. It verifies the sealed authorization before writing the credential.

**Data flow**: A slot, sealed authorization, and plaintext secret go in. The function checks permission through _credential_authorization, verifies the sealed token, then writes the plaintext credential into the workspace credential store.

**Call relations**: This is the final step after an admin-approved credential flow. It uses ws_current to reach the current workspace store after _credential_authorization confirms the caller may proceed.

*Call graph*: calls 1 internal fn (_credential_authorization); 1 external calls (ws_current).


##### `ToolContext._credential_authorization`  (lines 327–336)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the shared safety checks for credential authorization. It makes sure there is a live speaker, the extension declared the credential slot, credential storage is configured, and the speaker is an admin.

**Data flow**: A credential slot goes in. The function reads the context’s speaker, extension, configured credential request service, and admin status; it either raises a clear error or returns the request service plus the speaker’s member id.

**Call relations**: begin_credential_authorization, open_credential_authorization, and fulfill_credential_authorization all call this before doing anything with credential tokens. It calls speaker_is_admin as its final authority check.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 3 (begin_credential_authorization, fulfill_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 338–347)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the external broker account id that a connector tool should use. It is a convenience wrapper for callers that only need the account id, not the full connection record.

**Data flow**: A provider name and optional account id go in. The function asks connector_connection to choose an allowed connection, then returns that connection’s account_id string.

**Call relations**: Connector execution tools call this before asking the broker to run an external action. It delegates the real selection and permission logic to connector_connection.

*Call graph*: calls 1 internal fn (connector_connection); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_connection`  (lines 349–386)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Chooses the exact connected external account this turn may use for a provider. It protects privacy by considering only grants available to this turn and by failing when the requested or implied choice is not valid.

**Data flow**: A provider name and optional account id go in. The function gets private and shared grant tiers, matches a requested account if one was supplied, or otherwise prefers private grants over shared grants; it returns a ConnectorConnection or raises a clear error for none or many.

**Call relations**: connector_account calls this when it only needs the broker account id, and source tools call it when they need the full connection identity. It relies on _connector_account_tiers to separate private and shared grants, then builds a ConnectorConnection.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


##### `ToolContext.connector_accounts`  (lines 388–396)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists all connected account ids this turn may use for one provider. It is useful when a tool needs to show or resolve the available choices.

**Data flow**: A provider name goes in. The function gets private and shared grant tiers, combines their account ids, removes duplicates, sorts them, and returns them as a tuple.

**Call relations**: Source tools call this while resolving which external account should back a source. It shares the same permission filtering as connector_connection by calling _connector_account_tiers.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 398–417)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: Splits available connector grants into private grants for the acting member and shared grants for the provider. This is the core filter that keeps private connected accounts private by default.

**Data flow**: A provider name goes in. The function reads the configured grant store and acting member, fetches active grants, filters them into private and shared lists for that provider, sorts each list by account id, and returns both lists.

**Call relations**: connector_connection and connector_accounts call this before choosing or listing accounts. If no grant system is configured, it raises ConnectUnavailable so connector tools fail clearly instead of silently using the wrong account.

*Call graph*: called by 2 (connector_accounts, connector_connection); 1 external calls (__init__).


### `core/src/ufo/tools/registry.py`

`domain_logic` · `cross-cutting`

A “tool” here is an action the AI system can ask the software to perform, such as reading a page, searching, calling another service, or writing somewhere outside the system. This file gives each tool a small official record, called a ToolDef, that says its name, what it does, what input data it expects, and what code should run when it is called.

The file also records safety hints. A tool can be marked “untrusted” when its result may contain outside text that should be treated as data, not as instructions. It can be marked “side_effecting” when it writes to the outside world or performs an action that should not accidentally happen twice. These flags help the larger engine make safer choices around retries and model input.

ToolRegistry is the frozen collection of all available tools. It acts like a front desk index: if two tools try to use the same name, startup fails early instead of letting the system guess later. It also reserves a special requested_by input field, which lets calls say which message explicitly authorized them. When the engine later receives a tool name from the model, the registry either returns the matching ToolDef or raises a clear error if no such tool exists.

#### Function details

##### `ToolDef.schema`  (lines 47–59)

```
def schema(self) -> ToolSchema
```

**Purpose**: Builds the tool description that can be sent to the model client. It combines the tool’s name and description with the JSON-style input shape taken from its Pydantic model, and adds the reserved requested_by field used for authority tracking.

**Data flow**: It starts with the tool’s input model class and asks it for a JSON schema, which is a machine-readable description of allowed input fields. It then adds requested_by as an extra optional-looking schema property with a UUID format and explanatory text. Finally, it creates and returns a ToolSchema object containing the tool name, description, and completed input schema.

**Call relations**: This is the per-tool packaging step. When a registry needs model-facing schemas, each ToolDef can turn itself into a ToolSchema, handing the final object off through ToolSchema.__init__ so the rest of the model interface sees a consistent shape.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 66–75)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a registry is valid right after it is created. It refuses duplicate tool names and refuses tools whose own input model already uses the reserved requested_by field.

**Data flow**: It reads the tuple of ToolDef objects stored in the registry. First it collects their names and looks for any name that appears more than once; if it finds any, it raises a ValueError. Then it checks each tool’s input model fields to make sure none already define requested_by; if any do, it raises a ValueError. If both checks pass, the registry remains usable unchanged.

**Call relations**: This is the registry’s safety gate. It runs automatically after the frozen dataclass is constructed, before later code can advertise tools or dispatch a call, so mistakes are caught early instead of during a live tool request.


##### `ToolRegistry.schemas`  (lines 77–78)

```
def schemas(self) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the full set of model-facing tool schemas for every tool in the registry. This is how the system can tell the model what tools exist and what inputs each one accepts.

**Data flow**: It reads the registry’s stored tools in order. For each one, it asks the ToolDef to build its schema. It returns a tuple of those ToolSchema objects, leaving the registry itself unchanged.

**Call relations**: This is the batch version of ToolDef.schema. Code that needs to present all available tools to the model can call this once, and it delegates the per-tool details to each ToolDef.


##### `ToolRegistry.get`  (lines 80–84)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the ToolDef with a given name so the engine can run the right handler. If the name is not registered, it fails loudly instead of silently doing nothing or choosing the wrong tool.

**Data flow**: It takes a tool name string as input and scans the registry’s stored tools. If it finds a ToolDef whose name matches, it returns that ToolDef. If it reaches the end without a match, it raises a KeyError explaining that the tool is unknown.

**Call relations**: During tool dispatch, core/src/ufo/loop/engine._dispatch_segments calls this lookup when it needs to turn a model-requested tool name into the actual registered tool definition. The returned ToolDef gives the engine the handler and metadata it needs for the next step.

*Call graph*: called by 1 (_dispatch_segments).


### Core built-in tools
The built-in tool implementations provide the standard shell, file, search, sharing, account, skill, and subagent capabilities exposed through the tool system.

### `core/src/ufo/tools/builtins.py`

`domain_logic` · `tool handling during an agent turn`

This file is the main toolbox the agent uses during a turn. Without it, the agent could talk but could not safely inspect the workspace, create files, search code, delegate work, or hand finished files back to a user.

The central idea is containment. File and shell operations go through the sandbox, which is an isolated workspace container. That means the agent does not freely read the host machine or move unlimited data around. Dedicated file tools such as read, write, edit, glob, and grep call an in-sandbox helper named sbxfs, so large files, PDFs, images, and searches are processed inside the container and only bounded results come back.

The file also adds guardrails. A file must be read during the turn before it can be edited or overwritten, which helps stop blind changes. Long shell commands can continue in the background, with log and exit files acting like a receipt and progress board. Sharing a file is the one approved exit path from the sandbox: the file is measured, stored as an artifact, recorded in the database, and returned as a temporary download URL.

Other tools coordinate conversation flow. The agent can ask the user structured questions, request secrets through a private prompt, start an OAuth account connection, load reusable “skills,” and spawn or control subagents.

#### Function details

##### `_bounded_file_path`  (lines 252–255)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the tool result limits after it is encoded as JSON. This prevents an extremely long or unusual path from breaking the small response envelope used for file changes.

**Data flow**: It receives a path string, measures how large that path becomes when written as JSON, and either returns the same path unchanged or raises an error if it is too large.

**Call relations**: This is used as validation for file path inputs before write and edit tools run. It relies on JSON encoding to measure the path the same way the result system will later carry it.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 436–478)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandbox. It can wait for the command, detach it immediately, or move it to the background if the wait time runs out.

**Data flow**: It receives the tool context and a command request. If background mode is requested, it hands off to the background path. Otherwise it chooses a timeout, creates a stable task name, launches the command in the sandbox, and returns either the command output, an error with the exit code, or background-task instructions if the command is still running.

**Call relations**: The tool registry calls this when the agent uses the bash tool. It depends on _task_id and _task_base to name the background task, calls _bash_background for explicitly detached commands, and calls _moved_to_background when a foreground command exceeds its waiting budget.

*Call graph*: calls 4 internal fn (_bash_background, _moved_to_background, _task_base, _task_id); 2 external calls (__init__, __init__).


##### `_moved_to_background`  (lines 481–510)

```
async def _moved_to_background(ctx: ToolContext, command: str, task_id: str, applied_s: int, requested_s: int | None) -> ToolResult
```

**Purpose**: Explains what happened when a shell command did not finish before its timeout. It checks whether the command is still alive and, if so, returns the same kind of tracking information as a normal background command.

**Data flow**: It receives the command, task id, actual timeout, and requested timeout. It probes the sandbox for the task’s process or exit file. If the work is still present, it returns task handles; if not, it records diagnostic details and returns a timeout error message.

**Call relations**: bash_handler calls this after a sandbox command times out. It uses _task_base to find the task files, _task_result to format a live background task response, and _record_exec_timeout to log what the sandbox looked like when the timeout happened.

*Call graph*: calls 3 internal fn (_record_exec_timeout, _task_base, _task_result); called by 1 (bash_handler); 2 external calls (__init__, __init__).


##### `_task_id`  (lines 513–520)

```
def _task_id(ctx: ToolContext) -> str
```

**Purpose**: Creates the short identifier used for a background shell task. When possible, it makes the id stable across retries so the same command is not accidentally launched twice after a crash.

**Data flow**: It reads the context’s idempotency key, which is a repeatable key for the same dispatched action. If the key exists, it hashes it into a short id; if not, it creates a fresh random id.

**Call relations**: bash_handler and _bash_background call this before launching work. Its stable ids let later helper functions point at the same log, process id, and exit-code files.

*Call graph*: called by 2 (_bash_background, bash_handler); 2 external calls (sha256, uuid4).


##### `_task_base`  (lines 523–527)

```
def _task_base(task_id: str) -> str
```

**Purpose**: Builds the base workspace path used for a background task’s files. This gives every task a predictable place for its log, pid file, and exit file.

**Data flow**: It receives a task id and returns an absolute path under the workspace’s .tasks directory. The returned path is later extended with endings like .log, .pid, and .exit.

**Call relations**: bash_handler, _bash_background, _moved_to_background, and _task_result all use this so they agree on where a task’s control files live.

*Call graph*: called by 4 (_bash_background, _moved_to_background, _task_result, bash_handler).


##### `_task_result`  (lines 530–550)

```
def _task_result(task_id: str, pid: str, applied_s: int | None=None) -> ToolResult
```

**Purpose**: Builds the user-facing response for a detached shell command. It tells the agent where to read progress, how to watch for completion, and how to stop the task.

**Data flow**: It receives a task id, the wrapper process id, and optionally the timeout that caused the task to detach. It creates a JSON payload with the log path, exit file path, watch command, and stop command, then returns it as tool text.

**Call relations**: _bash_background uses this for commands started in the background from the beginning. _moved_to_background uses it for commands that started in the foreground but continued after the wait expired. It uses _task_base so the paths match the launcher.

*Call graph*: calls 1 internal fn (_task_base); called by 2 (_bash_background, _moved_to_background); 3 external calls (__init__, __init__, dumps).


##### `_bash_background`  (lines 553–565)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command as a background task and returns immediately. This is for long-running work where the agent should not wait for completion before continuing.

**Data flow**: It receives the context and command, creates a task id and base path, launches the sandbox wrapper, reads back the wrapper process id, and returns either an error or the standard task-tracking response.

**Call relations**: bash_handler calls this when the bash tool input asks for background mode. It uses _task_id and _task_base to name the task and _task_result to describe how to follow it.

*Call graph*: calls 3 internal fn (_task_base, _task_id, _task_result); called by 1 (bash_handler); 2 external calls (__init__, __init__).


##### `_record_exec_timeout`  (lines 568–601)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: Logs diagnostic information when a sandbox command times out. This is for operators and debugging, not for changing what the user sees.

**Data flow**: It receives the context, command, applied timeout, and requested timeout. It tries briefly to read basic sandbox health information such as load, memory, and disk usage, then writes a structured log entry. Any failure during this diagnosis is swallowed.

**Call relations**: _moved_to_background calls this only when a timed-out command cannot be confirmed as a still-running background task. It uses the observability logger and turn profile information so timeout patterns can be understood later.

*Call graph*: called by 1 (_moved_to_background); 3 external calls (timeout, log, turn_profile).


##### `_require_str`  (lines 604–607)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Verifies that a value returned by the sandbox is a non-empty string. It turns malformed sandbox output into a clear runtime error.

**Data flow**: It receives any value and the field name it is expected to represent. If the value is a non-empty string, it returns it; otherwise it raises an error naming the missing field.

**Call relations**: read_handler uses this when returning images, and _pdf_result uses it for rendered PDF or slide images. It is a small safety check between raw sandbox JSON and tool content objects.

*Call graph*: called by 2 (_pdf_result, read_handler).


##### `_pdf_result`  (lines 610–655)

```
def _pdf_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a sandbox PDF or PowerPoint read result into content the model can use: extracted text plus rendered page or slide images when available.

**Data flow**: It receives a dictionary from sbxfs. It collects readable text, page-range information, notes, and image blocks, validates image fields, and returns a tool result containing text and image content. If the sandbox result is empty or malformed, it raises an error.

**Call relations**: read_handler calls this when sbxfs reports that the file is a PDF or PPTX. It uses _require_str to safely pull image media types and image data before building ImageContent blocks.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 658–695)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox and returns a bounded, useful view of it. It supports plain text, images, PDFs, and PowerPoint files.

**Data flow**: It receives a file path and optional offset and limit. It asks sbxfs inside the sandbox to read the file, records that this path has been seen this turn, and returns the right kind of content: image data, PDF or slide content, an empty-file note, a no-lines note, or text with line-range information.

**Call relations**: The tool registry calls this when the agent uses the read tool. Its record in ctx.read_paths is later used by write_handler and edit_handler as a safety gate before changing existing files.

*Call graph*: calls 2 internal fn (_pdf_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 698–721)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Writes text to a workspace file safely. It refuses to overwrite an existing file unless that path has already been read in the current turn.

**Data flow**: It receives the desired path and content. It stages the bytes in the sandbox’s tool-output area, asks sbxfs to write them to the final path with the read-before-overwrite rule, adds size and line counts to the result, records the path as read, and returns a compact summary.

**Call relations**: The tool registry calls this for the write tool. It uses _file_tool_result to keep the response within size limits, and its read-path update means later edits in the same turn are allowed.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (uuid4).


##### `edit_handler`  (lines 724–737)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to a file that the agent has already read this turn. This prevents editing a file based only on guesses.

**Data flow**: It receives a file path and a list of replacement instructions. It first checks the path against the turn’s read history. Then it base64-encodes the old and new strings so special characters survive transport, asks sbxfs to apply the edits in order, and returns a compact result.

**Call relations**: The tool registry calls this for the edit tool. It relies on read_handler having added the path to ctx.read_paths, and it uses _file_tool_result to format the sandbox’s edit summary.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 740–754)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats write and edit results so they stay small enough for the tool response. It summarizes what happened instead of returning a large diff or full file contents.

**Data flow**: It receives a result dictionary, serializes it as compact JSON, and returns it if it fits. If it is too large, it removes the snippet and shortens the message, then tries again. If the result still cannot fit, it raises an error.

**Call relations**: write_handler and edit_handler both call this after sbxfs finishes changing a file. It is the shared final step that keeps file-changing tools from flooding the model with too much text.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 757–763)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds workspace files whose paths match a glob pattern, such as **/*.py. A glob is a simple filename pattern, often using * as a wildcard.

**Data flow**: It receives a pattern and optional starting directory. It asks sbxfs inside the sandbox to do the search, defaulting to the workspace root, then returns the matching paths as JSON text.

**Call relations**: The tool registry calls this for the glob tool. It deliberately uses the sandbox file-search helper instead of shell commands so traversal happens inside the controlled workspace.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 766–784)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches inside workspace files for text matching a regular expression, which is a pattern language for text. It is the safer built-in alternative to running grep or ripgrep directly in bash.

**Data flow**: It receives the search pattern plus optional filters such as file glob, context lines, case-insensitive mode, output style, and result limit. It builds a parameter object, runs the search through sbxfs in the sandbox, and returns the bounded JSON result.

**Call relations**: The tool registry calls this for the grep tool. It keeps content scanning inside the sandbox and applies a default head limit so only a manageable number of matches comes back.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 787–823)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies a shared file from the sandbox into the configured artifact storage. It supports both S3-style object storage and a local filesystem blob store.

**Data flow**: It receives the sandbox file path, destination key, measured file size, and measured checksum. For S3, it asks for a presigned upload URL tied to that size and checksum, then makes the sandbox upload the file directly. For filesystem storage, it streams the bytes from the sandbox into the blob store.

**Call relations**: share_file_handler calls this to store the main shared file, and _shared_preview calls it to store preview images. It is the low-level storage step behind the share_file tool.

*Call graph*: called by 2 (_shared_preview, share_file_handler); 2 external calls (b64encode, quote).


##### `_shared_preview`  (lines 835–882)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str) -> ArtifactPreview | None
```

**Purpose**: Creates a small preview image for certain shared document types, such as PDFs and Office files. The preview lets a chat surface show a visual thumbnail instead of only a filename.

**Data flow**: It receives the context, sandbox file path, and safe display name. If the file extension is previewable, it runs sandbox renderers to turn the first page into a PNG, measures the PNG, rejects missing or oversized previews, stores the preview as an artifact, and returns its blob information. If previewing fails, it logs the reason and returns nothing.

**Call relations**: share_file_handler calls this after storing the main file. It uses _store_artifact for the preview blob, and failures here do not stop the main file from being shared.

*Call graph*: calls 1 internal fn (_store_artifact); called by 1 (share_file_handler); 6 external calls (__init__, loads, PurePosixPath, quote, log, uuid4).


##### `share_file_handler`  (lines 885–969)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares a produced workspace file with the user by turning it into a stored artifact and a temporary download URL. This is the approved path for a file to leave the sandbox.

**Data flow**: It receives a workspace path, optional download name, optional caption, and context. It checks artifact sharing is configured, measures the file inside the sandbox, chooses a safe filename, stores the file, optionally creates a preview, records the shared artifact in the database, mints a time-limited URL, and returns metadata such as name, size, digest, and text-ness.

**Call relations**: The tool registry calls this for the share_file tool. It uses _store_artifact for the actual upload, _shared_preview for thumbnails, database writes for the shared-artifact record, and artifact URL helpers to produce the final link.

*Call graph*: calls 2 internal fn (_shared_preview, _store_artifact); 16 external calls (__init__, __init__, now, dumps, loads, guess_type, PurePosixPath, quote, insert, select (+6 more)).


##### `spawn_subagent_handler`  (lines 972–989)

```
async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult
```

**Purpose**: Delegates a typed subtask to a subagent profile. A subagent is a child agent turn with its own instructions, tools, and input/output shape.

**Data flow**: It receives a profile name, payload, background flag, and context. It asks the context to spawn the subagent. If the profile is unknown, it returns a tool error; if the child runs in the background, it returns the child turn id; otherwise it returns the child’s validated JSON output.

**Call relations**: The tool registry calls this for spawn_subagent. It hands the real work to ToolContext.spawn and packages the child result back into a normal tool result.

*Call graph*: 3 external calls (__init__, __init__, spawn).


##### `ask_user_handler`  (lines 997–1007)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Prepares a structured question for the user and tells the agent to ask it in the chat reply. This keeps the question inside the normal conversation instead of using a hidden prompt.

**Data flow**: It receives a title and one or more question records. It builds a JSON payload describing the pending question and returns it with an instruction to ask the question and end the turn.

**Call relations**: The tool registry calls this for ask_user. The returned structure can be rendered by a richer chat surface, while the text directive guides the model’s next reply.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 1010–1021)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill into the workspace. A skill is a reusable bundle of instructions and supporting files for a particular kind of task.

**Data flow**: It receives the skill name, resolves that skill plus any skills it depends on, mounts each skill’s files into the sandbox workspace, builds the combined instruction context, and returns it as text.

**Call relations**: The tool registry calls this for load_skill. It delegates mounting to mount_skill and instruction formatting to loaded_context, using the skill closure from the context.

*Call graph*: 4 external calls (__init__, __init__, loaded_context, mount_skill).


##### `connect_account_handler`  (lines 1030–1042)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private account-connection handoff for an external provider such as GitHub or Google. It does not put the OAuth authorization URL into the chat transcript.

**Data flow**: It receives the requested provider, whether the connection should be shared, and context. It verifies there is a speaking member, validates the provider, creates a connection request tied to that member, and returns a directive plus structured request JSON.

**Call relations**: The tool registry calls this for connect_account. It uses the installed connect-flow validator before creating the ConnectRequest that the user interface can turn into a private connection control.

*Call graph*: 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 1052–1075)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks an admin user to provide secrets, such as API keys, through a private prompt rather than chat. This protects secrets from appearing in the transcript.

**Data flow**: It receives a reason and credential prompts. It checks that there is a speaking member, credential storage is configured, and the speaker is an admin. Then it seals the requested slots to that workspace and member, builds a credential request, and returns instructions plus structured JSON.

**Call relations**: The tool registry calls this for request_credentials. It calls ToolContext.speaker_is_admin for the permission check and uses the credential sealing service in the context before returning a CredentialRequest.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `cancel_subagent_handler`  (lines 1078–1090)

```
async def cancel_subagent_handler(ctx: ToolContext, args: CancelSubagentInput) -> ToolResult
```

**Purpose**: Cancels a running subagent that this turn is allowed to control. If the subagent already finished, it simply reports the current status.

**Data flow**: It receives a subagent id and context. It checks that subagent control exists, converts the id into a UUID, asks the subagent controller to cancel it, and returns the subagent id and resulting status as JSON.

**Call relations**: The tool registry calls this for cancel_subagent. It hands cancellation to ctx.subagents and only formats the status response.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_subagent_handler`  (lines 1093–1110)

```
async def message_subagent_handler(ctx: ToolContext, args: MessageSubagentInput) -> ToolResult
```

**Purpose**: Queues a follow-up message for a background subagent. The message becomes the subagent’s next turn after its current work reaches a stopping point.

**Data flow**: It receives a subagent id, message text, and context. It checks that subagent control and an idempotency key are available, converts the id to a UUID, sends the message through the subagent controller with the deduplication key, and returns the resulting turn id and status.

**Call relations**: The tool registry calls this for message_subagent. It relies on ctx.subagents for the actual delivery and uses the idempotency key so a retry does not enqueue the same follow-up twice.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### Todo extension tools
The todos extension contributes an additional tool pack and UI-facing task panel on top of the shared tool context and registry.

### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling and conversation UI refresh`

This file solves a simple but important problem: an agent working through several steps needs a shared checklist that survives beyond one tool call or one model message. Without this file, the agent could still talk about a plan, but there would be no durable, structured board that the system can save, update, and show to the user.

The file defines the shape of a todo board: a title plus tasks, where each task has text and a status such as pending, in_progress, or completed. It provides two tools. The first, update_todo_list, writes the whole checklist from scratch, replacing any old one for the same conversation. The second, update_todo_status, reads the saved checklist, changes selected task statuses by their 1-based task number, and saves it again. Think of it like a clipboard kept at the front desk for one customer conversation: each visit to the desk sees the same clipboard and can update it.

The checklist is stored in the extension’s scoped store, keyed by conversation ID, so different conversations do not overwrite each other. The file also defines a conversation slot called “Tasks,” which lets the host read a compact version of the board for display. That display version enforces length and count limits, and marks the result as truncated if anything had to be shortened.

#### Function details

##### `_require_ext`  (lines 93–96)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has access to the todos extension context. That context is needed because it contains the extension’s private store, where the checklist is saved.

**Data flow**: It receives the current tool context. If the context contains an extension object, it returns that object. If it does not, it stops the call with an error, because the todo tools cannot safely read or write their saved board without it.

**Call relations**: Both update_todo_list and update_todo_status call this at the start of their work. It acts like a gatekeeper before either tool tries to touch the saved checklist.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 99–100)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This helper turns a conversation ID into the storage key used for that conversation’s todo board. It keeps each conversation’s checklist separate.

**Data flow**: It receives a conversation UUID, adds the todo key prefix to it, and returns the resulting text key. Nothing else is changed.

**Call relations**: The write path in update_todo_list uses this key to save a board. update_todo_status uses it to find and rewrite the same board. The conversation-slot readers, _summarize_tasks and _read_tasks, use the same key so the UI sees the board for the current conversation.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 103–104)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper packages the current todo board as the standard result returned by a tool. It gives the model a fresh copy of the checklist after a create or update operation.

**Data flow**: It receives a TodoBoard, turns it into JSON text, wraps that text as TextContent, and returns it inside a ToolResult. The board itself is not changed.

**Call relations**: update_todo_list and update_todo_status call this after saving the board. It is the final handoff back from the todo tool to the tool-calling system.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 107–109)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store and converts it back into the validated TodoBoard shape. It centralizes the “load this conversation’s checklist” step.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved value. If nothing is there, it returns None. If a value exists, it validates that value as a TodoBoard and returns the board object.

**Call relations**: update_todo_status calls this before applying status changes. _summarize_tasks and _read_tasks call it when the host wants to show or summarize the conversation’s task state.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 112–116)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or fully replaces the todo checklist for the current conversation. An agent uses it when starting or revising a multi-step plan.

**Data flow**: It receives the tool context and the requested title, tasks, and user-facing description. It first gets the extension context, then builds a TodoBoard from the title and tasks. It saves a JSON-compatible version of that board under the current conversation’s key, then returns the saved board as JSON text. The user_description is part of the tool input, but this function does not store it in the board.

**Call relations**: This is one of the two public tool handlers registered by manifest. It calls _require_ext to get storage access, _board_key to choose where to save, and _board_result to return the updated checklist to the caller.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 119–130)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of one or more existing tasks. An agent uses it to mark work as started or finished without rewriting the whole checklist.

**Data flow**: It receives the tool context plus one or more task-number and status updates. It gets the extension context, builds the conversation’s storage key, and reads the existing board. If no board exists, or the board is empty, it raises an error telling the caller to create a list first. For each update, it checks that the 1-based task number is inside the list, changes that task’s status, saves the whole board back to storage, and returns the updated board.

**Call relations**: This is the second public tool handler registered by manifest. It depends on _require_ext for store access, _board_key for the conversation-specific key, _read_board for loading the current board, and _board_result for sending the final board back.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 133–135)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick summary number for the conversation’s task slot. It tells the host how many tasks exist without loading the full display payload.

**Data flow**: It receives a conversation-slot context, uses the conversation ID to build the board key, and reads the saved board. If there is no board, it returns None. If there is a board, it returns the number of tasks in it.

**Call relations**: The TASKS_SLOT provider uses this function as its summarize callback. It shares the same _board_key and _read_board helpers as the tool handlers, so the summary is based on the same saved checklist.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 138–168)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This function prepares the saved todo board for display in the conversation’s “Tasks” slot. It returns a safe, compact version that respects title, task count, and task description limits.

**Data flow**: It receives a conversation-slot context, builds the storage key, and reads the board. If no board exists, it returns an empty TasksSlotPayload. If a board exists, it shortens the title if needed, includes only the allowed number of tasks, shortens long task descriptions, counts completed tasks, and records whether anything was truncated.

**Call relations**: The TASKS_SLOT provider uses this as its read callback when the host wants the actual task display data. It reads the same stored board written by update_todo_list and update_todo_status, then turns it into ConversationTask and TasksSlotPayload objects for the UI-facing layer.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 181–201)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the todo extension to the host system. It tells the host which tools exist, what input shapes they expect, what prompt guidance to include, and what conversation slot the extension provides.

**Data flow**: It takes no input. It builds and returns a Manifest containing the extension name and version, two ToolDef entries for the create/replace and status-update tools, one prompt section loaded from a markdown file, and the Tasks conversation slot provider.

**Call relations**: The extension host calls this when loading the pack. The returned manifest connects outside tool calls to update_todo_list and update_todo_status, and connects conversation task display requests to the TASKS_SLOT provider.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-extension-pack-registry` — The selected packs and loaded extensions that decide which features, tools, routes, jobs, and backends exist.
- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-live-hub` — The live stream state that lets browsers, terminals, and operators watch progress and reconnect without losing updates.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-sandbox-network-policy` — The per-run rules and proxy state that decide what sandboxed code may reach on the internet and when secrets may be injected.
- `reg-browser-session` — The leased browser instance for a turn, including tabs, page state, downloads, dialogs, and provider connection details.
- `reg-subagent-state` — The helper-agent catalog and child-conversation handoff state used when one agent delegates work to another.
- `reg-search-index` — The searchable text chunks, embeddings, and backend index state used to find relevant documents and pages.
- `reg-memory-store` — Saved facts, memory pages, confidence, provenance, and audience labels that let the assistant remember useful context.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-artifact-store` — Generated files, previews, blobs, and signed shared-artifact links that outlive a single message.
- `reg-observability-context` — Trace IDs, metrics, logs, and sanitized operational events used to understand work across services and turns.
- `reg-proposal-state` — Saved proposed changes and their pending, approved, or rejected decision state.
- `reg-human-interaction-requests` — Pending user questions, approval prompts, and credential-request prompts created by tools and resumed through surfaces.
- `reg-user-skill-library` — Persisted user- or agent-authored skills and reusable skill metadata loaded into the agent’s available capabilities.
- `reg-execution-scope-context` — Per-request, per-job, and per-turn scoped context carrying the active workspace, member, agent, turn, permissions, credentials, billing, and service handles through core code.
- `reg-connector-auth-flow-state` — Short-lived OAuth, consent-link, CSRF/state, and callback progress for connecting external accounts before durable connections and grants exist.
- `reg-scratchpad-notebooks` — Persisted scratchpad or notebook content that agents reuse across turns separately from saved skills and ordinary conversation files.
