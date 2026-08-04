# Tool Catalog Dispatch and Built-In Agent Actions  `stage-11`

This stage is the agent’s tool desk during the main work loop. When the model wants to do something outside text, such as read a file or run a command, it must choose from a controlled catalog instead of acting freely. The registry is that catalog. It gives each tool a name and description, shows only the allowed tools to the model, and matches a requested tool name to the correct implementation.

The built-ins are the standard tools on the desk. They turn model requests into real actions: running shell commands, reading or editing workspace files, sharing completed artifacts, asking the user for missing information, connecting accounts, loading extra skills, coordinating subagents, working with workspace objects, and running cleanup callbacks.

The context is the safety envelope around each tool call. It tells a tool what it may access, such as files, browser sessions, credentials, accounts, memory, or subagents. Together, these pieces let the model act usefully while keeping each action named, routed, and bounded.

## Files in this stage

### Tool Catalog
Defines how tools are named, described, advertised to the model, and resolved for dispatch.

### `core/src/ufo/tools/registry.py`

`data_model` · `startup and request handling`

A “tool” here is an action the AI model can ask the system to perform, such as reading a page, searching, calling a connector, or writing to an outside service. This file gives each tool a clear record, called `ToolDef`, that includes its public name, a human-readable description, the shape of the input it expects, and the function that actually runs it.

It also records safety flags. For example, `untrusted` marks tool output that may contain attacker-controlled text, like a web page or search result, so the engine can treat that text as data rather than instructions. `side_effecting` marks tools that can change something outside the system, such as posting to an API, so retries can be made safer with an idempotency key, meaning a repeated attempt can be recognized as the same action rather than a new one.

`ToolRegistry` is the frozen catalog of all available tools. When it is created, it checks for duplicate tool names and rejects tool input models that try to use the reserved `requested_by` field. Later, the engine can ask the registry for the wire schemas to show the model, or look up a named tool when the model requests it. In everyday terms, this file is both the menu and the index card drawer for tools.

#### Function details

##### `ToolDef.schema`  (lines 43–55)

```
def schema(self) -> ToolSchema
```

**Purpose**: Builds the public description of one tool that can be sent to the model client. It combines the tool’s name and description with the JSON-style input shape the model must follow, and adds a standard `requested_by` field used to link a tool call to the message that authorized it.

**Data flow**: It starts with a `ToolDef`, reads the Pydantic input model attached to it, and asks that model for its JSON schema, which is a machine-readable description of allowed input. It then adds the reserved `requested_by` property to that schema. The result is a `ToolSchema` object containing the tool name, description, and final input schema ready to put on the wire.

**Call relations**: This is used when the registry prepares tool schemas for the model. It hands its finished schema into `ToolSchema.__init__`, so the rest of the system can pass around a consistent description of what the tool is and what arguments it accepts.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 62–71)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a newly created tool registry is valid before it is used. It prevents two tools from sharing the same name, and prevents tool input models from defining `requested_by`, because this file adds that field itself in a controlled way.

**Data flow**: It receives a completed `ToolRegistry` object after the dataclass has been initialized. It reads all tool names, finds any repeated names, and raises an error if there are duplicates. It also inspects each tool’s input model fields and raises an error if any model already contains the reserved `requested_by` field. If everything is clean, it changes nothing and lets the registry exist.

**Call relations**: This runs automatically when a `ToolRegistry` is constructed. It acts like a safety inspection before the engine starts relying on the catalog; later dispatch and schema generation assume these checks have already passed.


##### `ToolRegistry.schemas`  (lines 73–74)

```
def schemas(self) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the complete list of tool schemas that can be shown to the model client. This is how the registry turns its internal tool definitions into the public menu of available actions.

**Data flow**: It reads the registry’s tuple of `ToolDef` objects. For each one, it calls that tool’s `schema` method to produce a `ToolSchema`. It returns all of those schemas as a tuple, without changing the registry.

**Call relations**: This sits one level above `ToolDef.schema`: instead of describing one tool, it describes every registered tool. Code that needs to advertise the available tools can call this method and receive a ready-made set of wire schemas.


##### `ToolRegistry.get`  (lines 76–80)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the tool definition with a given name. The engine uses this when the model asks to call a tool, so it can move from the requested name to the actual handler function and safety settings.

**Data flow**: It receives a tool name as text. It scans the registry’s tools one by one until it finds a matching name, then returns that `ToolDef`. If no tool has that name, it raises a `KeyError` so the failure is loud and clear instead of silently doing the wrong thing.

**Call relations**: During dispatch, `core/src/ufo/loop/engine._dispatch_segments` calls this method after the model has requested a tool by name. `get` supplies the matching tool definition, which gives the engine the handler to run and the metadata it needs for safe execution.

*Call graph*: called by 1 (_dispatch_segments).


### Built-In Tool Execution
Implements the built-in agent actions and the safe execution context they receive.

### `core/src/ufo/tools/builtins.py`

`domain_logic` · `tool execution during an agent turn`

This file is the agent’s toolbox. Without it, the agent could talk about doing work but could not safely inspect files, create outputs, ask for missing information, or hand tasks to helper agents. The main rule is that workspace files are touched through the sandbox, which is an isolated container. That keeps file access under the same safety limits no matter whether the action is a shell command, a file read, or a search.

The file first describes the shape of each tool’s input using Pydantic models, which are checked data forms. Then it implements one handler function per tool. File tools deliberately avoid pulling whole files into the host process. Reads, edits, glob searches, and grep searches are delegated to an in-sandbox helper called `sbxfs`, so only a limited result comes back. A safety guard records which files were read during the turn; existing files cannot be edited or overwritten unless the agent has already seen them.

Sharing a file is handled as a special export path. The file is measured inside the sandbox, uploaded to blob storage, recorded in the database, and returned as a short-lived download link. The file also supports conversation-native interruptions: `ask_user`, `request_credentials`, and `connect_account` return structured instructions telling the agent to end its turn while the user or interface completes the next step. At the bottom, all handlers are registered as `ToolDef` objects so the rest of the system can expose them to the model.

#### Function details

##### `bash_handler`  (lines 308–318)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandboxed workspace and returns its output. It is used when the agent needs general command-line power, but not for file reading or searching, which have safer dedicated tools.

**Data flow**: It receives a tool context and a command with an optional timeout. It caps the timeout, sends the command to the sandbox, joins standard output and error text, and returns that text. If the command failed, it marks the tool result as an error and includes the exit code.

**Call relations**: This function is the handler registered for the `bash` tool in the built-in tool list. After the sandbox finishes the command, it wraps the visible output in `TextContent` and then in a `ToolResult` so the agent can read what happened.

*Call graph*: 2 external calls (__init__, __init__).


##### `_require_str`  (lines 321–324)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Checks that a value returned by the sandbox is a real, non-empty string. It protects later code from treating missing or malformed image/PDF data as valid content.

**Data flow**: It receives an unknown value and the name of the field being checked. If the value is a non-empty string, it returns it unchanged. If not, it raises an error that names the missing field.

**Call relations**: This small helper is used by `read_handler` and `_pdf_result` when they build image content from sandbox results. It acts like a ticket checker before data is allowed into `ImageContent`.

*Call graph*: called by 2 (_pdf_result, read_handler).


##### `_pdf_result`  (lines 327–372)

```
def _pdf_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a sandbox PDF or PowerPoint read result into model-friendly content: extracted text plus rendered page or slide images when available. It lets the agent understand visual documents without moving the whole file out of the sandbox.

**Data flow**: It receives a dictionary produced by the sandbox reader. It gathers text, page-range notes, and quality warnings into a text block, then adds one image block for each rendered page or slide. It returns a tool result containing those blocks, or raises an error if the result is malformed or empty.

**Call relations**: `read_handler` calls this when `sbxfs` says the file is a PDF or PPTX. Inside, it uses `_require_str` to validate media type and encoded image data before creating `ImageContent`, then returns everything as a `ToolResult`.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 375–412)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file safely through the sandbox. It supports text files, images, PDFs, and PowerPoint files, and it records that the file has been seen so later edits or writes can be allowed.

**Data flow**: It receives a file path and optional offset and limit. It asks the sandbox `sbxfs` tool to read only the requested window of the file. Depending on the returned type, it produces text, image content, or a PDF/PPTX result, and it adds the path to the turn’s `read_paths` set.

**Call relations**: This is the handler for the `read` tool. When the sandbox returns image data, it validates fields with `_require_str`; when it returns PDF or PowerPoint data, it hands off to `_pdf_result`. Its recorded read path is later checked by `write_handler` and `edit_handler`.

*Call graph*: calls 2 internal fn (_pdf_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 415–436)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Creates a new workspace file or overwrites an existing one, but only overwrites existing files the agent has already read this turn. This prevents blind overwrites of content the model has not inspected.

**Data flow**: It receives a path and text content. It asks the sandbox whether the file already exists; if it does and was not read, it refuses. Otherwise it writes the bytes, marks the path as read, counts size and lines, and returns a small JSON summary.

**Call relations**: This is the handler for the `write` tool. It relies on the read tracking created by `read_handler`, then packages its summary through `json.dumps`, `TextContent`, and `ToolResult`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `edit_handler`  (lines 439–447)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to a file the agent has already read. It is meant for careful, visible edits rather than guessing at file contents.

**Data flow**: It receives a file path and one or more edit instructions. It first checks that the path is in the turn’s read list. It converts the edit objects into plain dictionaries, sends them to the sandbox `sbxfs edit` command, and returns the sandbox’s JSON result.

**Call relations**: This is the handler for the `edit` tool. It depends on `read_handler` having recorded the file earlier in the turn, then wraps the sandbox edit result using `json.dumps`, `TextContent`, and `ToolResult`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 450–456)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files whose names match a pattern, such as `**/*.py`, inside the sandboxed workspace. It gives the agent a safe way to discover files without running broad shell commands.

**Data flow**: It receives a pattern and optionally a starting directory. It asks `sbxfs` inside the sandbox to perform the file traversal, defaulting to the workspace root. It returns the matching paths as JSON text.

**Call relations**: This is the handler for the `glob` tool. The sandbox performs the search, and this function only formats the bounded result through `json.dumps`, `TextContent`, and `ToolResult`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 459–477)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents in the workspace for a regular expression, which is a pattern language for matching text. It keeps large searches inside the sandbox and returns only a limited result.

**Data flow**: It receives the search pattern plus optional filters such as file glob, context lines, case-insensitive mode, output style, and result limit. It builds a parameter dictionary, fills in a default result cap when needed, calls sandbox `sbxfs grep`, and returns the JSON result.

**Call relations**: This is the handler for the `grep` tool. Like `glob_handler`, it leaves the heavy scanning work in the sandbox and only wraps the returned summary with `json.dumps`, `TextContent`, and `ToolResult`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 480–516)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Moves a prepared workspace file into the artifact store, which is where downloadable shared files live. It chooses the correct upload path depending on whether storage is S3 or local filesystem storage.

**Data flow**: It receives the sandbox path, destination storage key, measured file size, and SHA-256 digest. For S3, it creates a checksum, asks for a short-lived upload URL, and tells the sandbox to upload the exact measured file with `curl`. For filesystem storage, it streams file bytes from the sandbox into the blob store. It returns nothing, but the artifact is stored or an error is raised.

**Call relations**: `share_file_handler` calls this after measuring the file. This helper is the low-level transfer step, while `share_file_handler` handles naming, database records, and the final download link.

*Call graph*: called by 1 (share_file_handler); 2 external calls (b64encode, quote).


##### `share_file_handler`  (lines 519–597)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares a finished workspace file with the user by turning it into a downloadable artifact. This is the intended path for files to leave the sandbox.

**Data flow**: It receives a file path, optional display name, optional caption, and context. It checks that artifact sharing is configured, measures size and hash inside the sandbox, chooses a safe filename, stores the file through `_store_artifact`, records the shared artifact in the database, creates a short-lived tokenized download URL, and returns JSON describing the shared file.

**Call relations**: This is the handler for the `share_file` tool. It calls `_store_artifact` for the actual upload, uses database access to record the artifact for the current turn, and returns a `ToolResult` that the agent or chat surface can show to the user.

*Call graph*: calls 1 internal fn (_store_artifact); 15 external calls (__init__, __init__, now, dumps, loads, guess_type, PurePosixPath, quote, insert, select (+5 more)).


##### `spawn_subagent_handler`  (lines 600–613)

```
async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult
```

**Purpose**: Delegates a typed subtask to a child agent. It can either wait for the child’s validated answer or start it in the background and return its turn ID.

**Data flow**: It receives a subagent profile name, payload, background flag, and context. It calls the context’s spawn method with an idempotency key so repeated requests do not duplicate work. If the profile is unknown, it returns a recoverable error; otherwise it returns either the child turn ID or the child’s JSON output.

**Call relations**: This is the handler for the `spawn_subagent` tool. It hands the real work to `ToolContext.spawn`; later, background children can be followed up through `wait_for_subagents_handler`, `cancel_subagent_handler`, or `message_subagent_handler`.

*Call graph*: 3 external calls (__init__, __init__, spawn).


##### `ask_user_handler`  (lines 621–631)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Creates a structured question for the user and tells the agent to end its turn while waiting for the answer. It keeps the interaction inside normal chat instead of opening a separate prompt.

**Data flow**: It receives a title, questions, and activity description. It builds a payload saying the turn is awaiting a question, serializes the questions, prefixes a directive telling the agent what to do next, and returns that as text content.

**Call relations**: This is the handler for the `ask_user` tool. It formats the question with `json.dumps` and wraps it in a `ToolResult`; the surrounding conversation system uses that result to guide the next user reply.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 634–645)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill, meaning a bundle of instructions and files the agent can use for a specialized task. It also loads any skills that the chosen skill depends on.

**Data flow**: It receives the skill name. It asks the skills registry for the full dependency closure, mounts each skill’s files into the sandbox workspace, builds the combined instruction text, and returns it to the agent.

**Call relations**: This is the handler for the `load_skill` tool. It calls `mount_skill` for each skill’s files and `loaded_context` to produce the instruction text that becomes visible to the model.

*Call graph*: 4 external calls (__init__, __init__, loaded_context, mount_skill).


##### `connect_account_handler`  (lines 654–666)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private account connection flow, such as OAuth, for the speaking member. OAuth is a standard way to let an app access an external service without asking the user to paste their password into chat.

**Data flow**: It receives a provider name and whether the connection should be shared with the workspace. It verifies that there is a speaking member, validates that the provider is installed, builds a connection request, and returns a directive telling the agent to have the member use a private connection control.

**Call relations**: This is the handler for the `connect_account` tool. It calls `installed_connect_flow` to validate the provider, creates a `ConnectRequest`, and returns it as structured text in a `ToolResult`.

*Call graph*: 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 675–698)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks an admin user to enter secrets, such as API keys, through private interface prompts rather than chat. This keeps sensitive values out of the conversation transcript.

**Data flow**: It receives a reason and a limited list of credential prompts. It checks that there is a speaking member, that credential storage is configured, and that the speaker is a workspace admin. It seals the allowed credential slots for that member, builds a credential request, and returns a directive plus the structured request.

**Call relations**: This is the handler for the `request_credentials` tool. It calls `ToolContext.speaker_is_admin` before making the request, then creates a `CredentialRequest` and wraps it in a `ToolResult` for the chat surface to act on.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `wait_for_subagents_handler`  (lines 701–714)

```
async def wait_for_subagents_handler(ctx: ToolContext, args: WaitForSubagentsInput) -> ToolResult
```

**Purpose**: Waits for one or more background subagents to finish and reports their final status and answer. It is used when the main agent has delegated work and needs the results before continuing.

**Data flow**: It receives subagent IDs as strings. It checks that subagent control is available, converts each ID into a UUID, waits for those children through the context’s subagent controller, and returns JSON containing each child’s status and output. If any child output is marked untrusted, the returned tool result is also marked untrusted.

**Call relations**: This is the handler for the `wait_for_subagents` tool. It works with subagents originally created by `spawn_subagent_handler`, then packages the completed statuses with `json.dumps`, `TextContent`, and `ToolResult`.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `cancel_subagent_handler`  (lines 717–729)

```
async def cancel_subagent_handler(ctx: ToolContext, args: CancelSubagentInput) -> ToolResult
```

**Purpose**: Cancels a running background subagent and returns its current status. If the child has already finished, the function simply reports the existing terminal state.

**Data flow**: It receives a subagent ID string. It checks that subagent control exists, converts the ID into a UUID, asks the subagent controller to cancel that child, and returns JSON containing the child ID and status.

**Call relations**: This is the handler for the `cancel_subagent` tool. It operates on subagents created through `spawn_subagent_handler` and returns the controller’s status report through `json.dumps`, `TextContent`, and `ToolResult`.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_subagent_handler`  (lines 732–749)

```
async def message_subagent_handler(ctx: ToolContext, args: MessageSubagentInput) -> ToolResult
```

**Purpose**: Queues a follow-up message for a background subagent. The message becomes the subagent’s next turn after its current work finishes.

**Data flow**: It receives a subagent ID and message text. It checks that subagent control is available and that the call has an idempotency key, converts the ID into a UUID, sends the message through the subagent controller, and returns JSON with the follow-up turn ID and status.

**Call relations**: This is the handler for the `message_subagent` tool. It continues work that began with `spawn_subagent_handler`, uses the idempotency key to avoid duplicate follow-ups, and returns a status that can later be passed to `wait_for_subagents_handler`.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### `core/src/ufo/tools/context.py`

`domain_logic` · `tool execution and turn cleanup`

A tool can do powerful things: read files, call outside services, open browsers, ask for credentials, or start helper agents. This file is the rulebook and supply box for those actions. Instead of letting a tool reach into the whole system, the runtime hands it a ToolContext containing only the capabilities that are valid for this turn, this agent, this workspace, and this requester.

The file also defines the shape of tool outputs. A result can contain text or an image, and it can be marked as an error or as untrusted data. “Untrusted” means the content came from somewhere like a web page and must not be treated as instructions for the model.

ToolContext answers practical safety questions. Who is the acting member? Which audience should see a write? What private or shared information may be read? Is the speaker a workspace admin? Which connected external account is available? It also supports controlled credential authorization, so only an admin using an extension-declared credential slot can store secrets.

Finally, TurnCleanup is like a checkout sheet for temporary resources opened during a turn. Tools register close functions, and the turn drains them at the end so browser connections or hosted sessions do not leak.

#### Function details

##### `Spawn.__call__`  (lines 128–134)

```
async def __call__(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: Describes the callable interface used when a tool wants to delegate work to a named subagent. A subagent is a helper agent profile that can run a child turn for a typed subtask.

**Data flow**: The caller provides a profile name, an input payload, whether the child should run in the background, and optionally a deduplication key that prevents duplicate child turns after retries. The implementation validates and runs the child turn, then returns a SpawnResult containing the child turn id and, for foreground work, its validated output.

**Call relations**: This is a protocol method, so this file defines the promise rather than the implementation. ToolContext carries a Spawn object so tool handlers can ask the wider subagent workflow to start child turns without knowing how that workflow is built.


##### `SubagentControl.wait`  (lines 144–144)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes how a tool can wait for one or more background subagents to finish. It is used after a previous background spawn returned child turn ids.

**Data flow**: The caller supplies child turn ids. The implementation waits until those children reach terminal states, then returns one SubagentStatus for each child with its final status and text.

**Call relations**: This is part of the SubagentControl protocol placed on ToolContext. Lifecycle tools use it when they need to collect results from background subagents that were started earlier.


##### `SubagentControl.cancel`  (lines 146–146)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes how a tool can cancel a running background subagent. This gives the parent turn a controlled way to stop delegated work that is no longer wanted.

**Data flow**: The caller supplies one child turn id. The implementation requests cancellation and returns that child’s terminal status as a SubagentStatus.

**Call relations**: This protocol method belongs to the same subagent lifecycle surface as wait and message. ToolContext exposes it so tools can control already-spawned child turns without directly touching the scheduler.


##### `SubagentControl.message`  (lines 148–148)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus
```

**Purpose**: Describes how a tool can send a follow-up message to an already-spawned background subagent. The deduplication key helps avoid sending the same follow-up twice during crash recovery or retries.

**Data flow**: The caller provides the child turn id, the message text, and a deduplication key. The implementation admits that message as the child’s next turn and returns the resulting subagent status.

**Call relations**: This is exposed through ToolContext alongside spawn. It lets parent tools continue a background child’s work in a controlled, repeat-safe way.


##### `TurnCleanup.register`  (lines 162–163)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous close function to the list of things that must be cleaned up when the turn ends. Tools use this when they open a temporary resource, such as a browser connection.

**Data flow**: A tool passes in a no-argument async function that closes one resource. The cleanup registry stores it for later and returns nothing.

**Call relations**: Tool code calls this during a turn after opening a resource. Later, the runtime calls TurnCleanup.drain to run the registered close functions.


##### `TurnCleanup.drain`  (lines 165–171)

```
async def drain(self) -> None
```

**Purpose**: Closes all resources registered for this turn, even if the turn ended with an error or cancellation. It prevents temporary connections from outliving the work that needed them.

**Data flow**: It reads the stored close functions, removes them one by one in reverse order, and awaits each close operation. If a close operation fails, it logs the failure and keeps draining the rest.

**Call relations**: The turn loop calls this at turn end. It calls ufo.o11y.log when cleanup fails, so a bad closer is recorded without stopping the remaining cleanup.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 208–216)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Finds the member whose authority this tool call may use. Usually this is the live speaker, but scheduled work or subagents may instead act on behalf of an initiating member.

**Data flow**: It reads speaker_member_id first. If there is no live speaker, it falls back to on_behalf_of_member_id. It returns that member id, or None if no member authority is attached.

**Call relations**: Other ToolContext methods use this property when deciding audience visibility and connector account access. It is the basic answer to “whose permissions are in effect right now?”


##### `ToolContext.effective_audience`  (lines 219–229)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides which audience a write should belong to, so information is not accidentally shared across private spaces. An audience is the set of people or subjects allowed to see a piece of information.

**Data flow**: It reads the current acting member and the conversation audience. If there is no acting member, or the conversation is not the workspace-shared audience, it returns the existing audience. If the turn is in the shared audience with an acting member, it returns that member’s conversation audience.

**Call relations**: It calls conversation_audience when a shared conversation needs to be narrowed to the acting member. Tools that write memories or records can use this to stamp output with the correct visibility.

*Call graph*: 1 external calls (conversation_audience).


##### `ToolContext.read_subjects`  (lines 232–241)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Builds the set of subjects this tool may read from: the conversation’s subjects plus, when there is an acting member, that member’s private subject. This keeps private and shared information separated.

**Data flow**: It starts with subjects derived from the current audience. If there is an acting member, it adds that member’s subject. It returns the final set as an immutable frozenset.

**Call relations**: It calls audience_subjects to expand the conversation audience and member_subject to add the requester’s private subject. ToolContext.source_reader uses this result when asking extensions to read synced source content.

*Call graph*: 2 external calls (audience_subjects, member_subject).


##### `ToolContext.source_reader`  (lines 243–253)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Creates a SourceReader that tells source and memory extensions who is asking and what they are allowed to read. This is the access badge for reading synced source pages.

**Data flow**: It reads the turn’s agent id, the live speaker member id, and the readable subjects from read_subjects. It packages those into a SourceReader object and returns it.

**Call relations**: Memory and source extension code calls this before listing or fetching stored pages and memories. It hands those extensions a compact permission description instead of making them reconstruct access rules themselves.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.speaker_is_admin`  (lines 255–265)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live speaker is a workspace administrator. This is used for workspace-wide actions that should not be performed by background work with no live requester.

**Data flow**: If there is no speaker_member_id, it immediately returns False. Otherwise it opens a workspace database transaction, asks whether that member is an admin in this workspace, and returns the answer.

**Call relations**: Many object and agent operations call this before allowing privileged actions. Internally it uses workspace_tx for database access and member_is_admin for the actual admin check.

*Call graph*: called by 21 (_create, apply, delete, apply, delete, get, list, status, request_credentials_handler, _credential_authorization (+11 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 267–278)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether this turn’s agent is the workspace’s main agent. Some actions are only visible or allowed for the main agent.

**Data flow**: It opens a workspace database transaction, queries the agent table for this turn’s agent within this workspace, reads the is_main value, and returns it as a boolean.

**Call relations**: Agent, member, and web audience operations call this when their rules depend on whether the current agent is the main one. It uses SQLAlchemy to build the database query and workspace_tx to run it.

*Call graph*: called by 7 (_create, apply, add, _visible_rows, apply, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 280–282)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts a controlled flow for authorizing an extension credential, such as an API key or OAuth-related secret. It ensures the request is allowed before creating the authorization token.

**Data flow**: The caller provides a credential slot name and a payload. The method first asks _credential_authorization to verify the speaker, extension declaration, secret-storage setup, and admin status. It then asks the CredentialRequests service to create and return a sealed authorization string.

**Call relations**: Coding and Slack extension tools call this when they need a user to authorize credentials. It delegates all safety checks to _credential_authorization before handing off to the credential request service.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 284–286)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens and verifies a previously sealed credential authorization. This lets the system confirm that an authorization token belongs to the right workspace, member, and credential slot.

**Data flow**: The caller supplies the slot and sealed token. The method runs the same authorization checks through _credential_authorization, then asks CredentialRequests to open the sealed authorization and returns its payload.

**Call relations**: This is the read-back half of the credential authorization flow. It depends on _credential_authorization so a token cannot be opened outside the allowed extension and admin context.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext.fulfill_credential_authorization`  (lines 288–293)

```
async def fulfill_credential_authorization(self, slot: str, sealed: str, plaintext: str) -> None
```

**Purpose**: Completes a credential authorization by storing the actual secret value. It verifies the authorization first, then saves the plaintext credential into the current workspace secret store.

**Data flow**: The caller gives the slot, sealed authorization token, and plaintext secret. The method checks authorization through _credential_authorization, opens the sealed token to validate it, then writes the plaintext credential with ws_current().put_credential. It returns nothing.

**Call relations**: This is the final step after an admin has authorized a credential slot. It uses _credential_authorization for permission checks and ws_current to reach the workspace’s credential storage.

*Call graph*: calls 1 internal fn (_credential_authorization); 1 external calls (ws_current).


##### `ToolContext._credential_authorization`  (lines 295–304)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the shared safety checks for credential authorization. It makes sure only a live workspace admin can authorize a credential slot that the current extension actually declared.

**Data flow**: It reads the live speaker, current extension, configured credential request service, and admin status. If anything is missing or invalid, it raises a clear ValueError. If all checks pass, it returns the CredentialRequests service and the speaker member id.

**Call relations**: The three public credential methods call this before beginning, opening, or fulfilling an authorization. It calls speaker_is_admin as the final authority check.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 3 (begin_credential_authorization, fulfill_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 306–315)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the external connected-account id that a connector tool should use for a provider. This is the simple version for tools that only need the account id, not the full connection details.

**Data flow**: The caller names a provider and optionally an account id. The method asks connector_connection to resolve exactly which allowed connection applies, then returns that connection’s account_id.

**Call relations**: Connector extension tools call this before executing work against an external service. It delegates selection and permission rules to connector_connection.

*Call graph*: calls 1 internal fn (connector_connection); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_connection`  (lines 317–354)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Chooses the exact connected external account this turn may use, including the connection id and owning member. It enforces private-by-default account access and rejects missing or ambiguous choices.

**Data flow**: It asks _connector_account_tiers for private and shared grants for the provider. If the caller supplied an account id, it searches both tiers for that exact allowed account. If not, it prefers private grants, falls back to shared grants, requires exactly one match, and returns a ConnectorConnection. If no valid choice exists, it raises a helpful error.

**Call relations**: connector_account uses this when only an account id is needed, and source tools call it when they need the stable connection generation. It calls _connector_account_tiers to get the allowed grants and builds a ConnectorConnection for the selected grant.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


##### `ToolContext.connector_accounts`  (lines 356–364)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists the external account ids available to this turn for one provider. This is useful when a tool needs to show or resolve possible account choices.

**Data flow**: The caller provides a provider name. The method gets private and shared grant tiers, combines their account ids, removes duplicates, sorts them, and returns them as a tuple.

**Call relations**: Source tools call this when resolving which account can be used. It relies on _connector_account_tiers for the permission-filtered grant lists.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 366–385)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: Splits available connector grants into two groups: private grants owned by the acting member, and shared grants available to the agent’s audience. This is the core access rule for connected external accounts.

**Data flow**: It first checks that the grant system is configured; if not, it raises ConnectUnavailable. It reads the acting member, fetches active grants, filters them by provider, separates private owner-matching grants from shared grants, sorts each group by account id, and returns both lists.

**Call relations**: connector_connection and connector_accounts call this whenever connector account access must be resolved. It is the permission filter that keeps tools from using another member’s private connection.

*Call graph*: called by 2 (connector_accounts, connector_connection); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-extension-catalog` — The loaded list of extensions and packs that tells the system which extra tools, routes, jobs, skills, and backends exist.
- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-access-grants` — The saved approvals that say which workspace, member, agent, account, source, or conversation is allowed to use a protected resource.
- `reg-connector-account-state` — The connected-app state for OAuth, hosted connector accounts, GitHub installations, Slack setup, and provider action access.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-browser-session-state` — The live browser-control session state used to click, read pages, download files, recover sessions, and route sandbox browser links.
- `reg-skill-inventory` — The declared and user-created skill inventory, including skill ownership, dependencies, files, and the load order copied into a sandbox.
- `reg-subagent-workflow-state` — The shared parent-child workflow state used when an agent delegates work to helper agents and waits for or cancels them.
- `reg-artifact-blob-store` — The shared file and blob store for generated artifacts, copied outputs, download records, and signed access links.
- `reg-workspace-object-state` — The shared shelf of workspace objects, their types, names, owners, permissions, listings, and object-specific actions.
- `reg-todo-checklist-state` — The durable visible todo/checklist state that agents update during long-running work and reuse across turns.
- `reg-repl-scratchpad-state` — The Python and JavaScript REPL scratchpad sessions, files, and execution state kept for agent experimentation across tool calls or turns.
- `reg-user-question-state` — The pending human-question/answer state created when an agent asks the user for information and later resumed when the surface delivers a reply.
- `reg-turn-execution-budget-state` — The per-turn live execution limits and counters for context size, tokens, reasoning, tool iterations, cost checks, and stop conditions that gate the model loop before final ledger recording.
- `reg-turn-cleanup-callback-state` — The per-turn registry of cleanup callbacks and borrowed-resource finalizers that tools add during execution and completion/teardown later drains.
- `reg-connector-action-catalog` — The dynamic catalog/cache of hosted connector actions, MCP-discovered tools, schemas, and routing metadata available for connected external accounts.
