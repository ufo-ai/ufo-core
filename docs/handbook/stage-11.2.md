# Built-in tools and extension tools  `stage-11.2`

This stage is the agent’s toolbox. It is shared support used during the main work of a conversation, whenever the agent needs to act rather than just reply. The core built-ins are the basic tools: run shell commands, read or edit files, share artifacts, start subagents, ask the user questions, and request private credentials, all inside a controlled sandbox.

The extension files add special-purpose tools around that core. The notification tools let an agent place a message in a member’s inbox, then let the Notification app deliver it safely and only in the right context. The debugger report tool records deployment problems for engineers, with a link to the relevant transcript turn when possible. The MCP tool connects to outside tool servers, discovers what they offer, and calls them with limits and clear errors. The monitor tool watches for outside changes by rerunning a command. The REPL extension runs small Python or JavaScript snippets and keeps successful state. Slack tools help connect and search Slack. The todo tool stores checklists for ongoing work. Several __init__ files simply mark extension folders as importable packages.

## Files in this stage

### Core sandbox toolbox
The built-in tools let agents act in the workspace, edit files, run commands, communicate with users, and hand work to subagents.

### `core/src/ufo/host/tools/builtins.py`

`domain_logic` · `tool handling during an agent turn`

This file is the bridge between an agent's tool call and the real work that happens behind it. Without it, the agent could talk, but it could not safely inspect files, change files, run commands, delegate work, share outputs, or ask for missing information in a structured way.

A key idea here is containment. File and shell operations go through the sandbox, which is like a locked workshop: the agent can work with the tools and files inside, but cannot freely reach outside. Reads, writes, edits, searches, and glob matches use an in-sandbox file helper so only bounded, controlled results come back.

The file also adds safety rails. For example, an existing file must be read in the current turn before it can be edited or overwritten. This prevents blind replacements, where the agent changes text it has not actually seen.

Sharing files is treated as a special escape hatch. Files are streamed from the workspace to the artifact store, recorded in the database, and returned as temporary download links. Large files are not loaded fully into memory.

The rest of the file covers conversation-native actions: spawning child agents, messaging or cancelling them, asking the user a question, loading skill instructions, starting account connection flows, and requesting secrets through a private prompt instead of chat.

#### Function details

##### `_bounded_file_path`  (lines 167–170)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the small JSON result envelope used by file-change tools. This prevents an extremely long path from making later tool output too large or unsafe to return.

**Data flow**: It receives a path string, encodes it as JSON to measure its true returned size, and either returns the same path unchanged or raises a validation error if it is too large.

**Call relations**: This is used as validation for file path input models. It runs before write or edit work reaches the sandbox, so bad input is refused early.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 349–380)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandbox and reports whether it finished, failed, timed out, or was moved to the background. It gives the agent controlled access to command-line work without letting that work escape the sandbox rules.

**Data flow**: It receives the tool context and a command request. If background mode is requested, it hands off to the background launcher. Otherwise it blocks obvious long sleeps, starts the command through the task system, and returns command output, an error message, or task handles for a still-running process.

**Call relations**: This is the handler behind the built-in `bash` tool. It calls `_bash_background` for detached work and otherwise relies on the shared task-running helpers to launch, wait, and format status for sandbox commands.

*Call graph*: calls 1 internal fn (_bash_background); 7 external calls (__init__, __init__, format, flat_sleeps, run_task, task_handles, timeout_notice).


##### `_bash_background`  (lines 383–397)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command as a detached background task and immediately returns the information needed to find it later. This is useful for long work such as builds or data processing that should continue while the agent does something else.

**Data flow**: It receives a context and command, creates a task id and task log location, asks the sandbox to start the command detached, and returns either an error or a small handle summary containing the task id, process id, and paths.

**Call relations**: It is called only by `bash_handler` when the user selected background execution. It uses the same task-handle formatting as foreground commands that time out but keep running.

*Call graph*: called by 1 (bash_handler); 4 external calls (__init__, __init__, task_handles, task_id).


##### `_require_str`  (lines 400–403)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Checks that a value returned from the sandbox is a real, non-empty string. It turns malformed file-read responses into clear internal errors instead of silently producing broken tool output.

**Data flow**: It receives an unknown value and the name of the expected field. If the value is a non-empty string, it returns it; otherwise it raises an error saying the field was missing.

**Call relations**: It is used by `read_handler` and `_document_result` when building image or document responses, especially for required media type and image data fields.

*Call graph*: called by 2 (_document_result, read_handler).


##### `_document_result`  (lines 406–449)

```
def _document_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a sandbox document-read response, such as for a PDF, presentation, Word file, or spreadsheet, into tool output the agent can understand. It combines extracted text, page or slide notes, and rendered page images.

**Data flow**: It receives a dictionary from the in-sandbox file reader. It validates page counts and document type, builds readable footer text such as page ranges, adds notes when present, attaches rendered images, and returns a `ToolResult` containing text and image blocks.

**Call relations**: It is called by `read_handler` when the sandbox says the file is a document. It uses `_require_str` to make sure each rendered page has valid image data before passing it back.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 452–489)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox and returns text, image content, or document previews in a bounded form. It also records that this path has been read, which later allows safe edits or overwrites in the same turn.

**Data flow**: It receives a file path plus optional offset and limit. It asks the sandbox file reader for that slice of the file, records the path in the context's read set, then formats the result as image data, document output, an empty-file note, or text with line-range information.

**Call relations**: This is the handler behind the `read` tool. It delegates document formatting to `_document_result` and uses `_require_str` for image fields. Its read-path side effect is later checked by `write_handler` and `edit_handler`.

*Call graph*: calls 2 internal fn (_document_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 492–514)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Writes text to a workspace file while preventing accidental overwrites of files the agent has not read this turn. It is meant for creating files or replacing known files safely.

**Data flow**: It receives a target path and text content. It stages the bytes into a temporary workspace file, asks the sandbox file tool to perform the real write, tells it whether overwriting is allowed based on the read set, formats the result, and then marks the target path as read.

**Call relations**: This is the handler behind the `write` tool. It uses `_write_refusal` to improve the error message for unread overwrites and `_file_tool_result` to return a bounded JSON summary.

*Call graph*: calls 2 internal fn (_file_tool_result, _write_refusal); 1 external calls (uuid4).


##### `_write_refusal`  (lines 517–525)

```
def _write_refusal(path: str, allow_existing: bool, error: ValueError) -> ValueError
```

**Purpose**: Turns the sandbox's unread-overwrite refusal into a more helpful message. It tells the agent exactly what to do next: read the file first, then try again.

**Data flow**: It receives the path, whether overwriting was allowed, and the original error. If the error is specifically the read-before-write guard, it returns a new error with a read-first hint; otherwise it leaves the original error alone.

**Call relations**: It is called by `write_handler` only when the sandbox write fails. It keeps the user-facing recovery advice close to the context that knows which paths were read this turn.

*Call graph*: called by 1 (write_handler).


##### `edit_handler`  (lines 528–544)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact string replacements to a file, but only after the file has been read in the current turn. This protects against blind edits where the agent might replace text in a file it has not inspected.

**Data flow**: It receives a path and one or more edits. It first checks the path is in the read set, then base64-encodes the old and new strings so they can safely pass through JSON, asks the sandbox file tool to apply the edits atomically, and returns a compact result.

**Call relations**: This is the handler behind the `edit` tool. It relies on the read tracking established by `read_handler` and formats successful sandbox responses through `_file_tool_result`.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 547–561)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats the result of a write or edit into a small JSON text response. It avoids returning huge snippets or diffs that could exceed tool output limits.

**Data flow**: It receives a result dictionary from the sandbox. It serializes it to compact JSON; if it is too large, it removes the optional snippet and shortens the message, then returns the bounded JSON as tool output or raises an error if it still cannot fit.

**Call relations**: It is shared by `write_handler` and `edit_handler`. This keeps the size policy for file-change responses consistent across both tools.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 564–570)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files whose paths match a glob pattern, such as `**/*.py`, inside the sandbox. A glob is a simple wildcard pattern for filenames.

**Data flow**: It receives a pattern and optional starting directory. It asks the sandbox file helper to search from that directory, defaulting to the workspace root, and returns the matching paths as JSON text.

**Call relations**: This is the handler behind the `glob` tool. It keeps file discovery inside the sandbox instead of using unrestricted shell commands like `find`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 573–591)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents for a regular expression inside the sandbox. A regular expression is a search pattern that can match more than plain text.

**Data flow**: It receives the search pattern, optional path, file filter, context-line count, case setting, output mode, and result limit. It builds a parameter object, supplies a default result cap, asks the sandbox search helper to run ripgrep, and returns the bounded JSON result.

**Call relations**: This is the handler behind the `grep` tool. It keeps content scanning in the sandbox and gives the agent a safer alternative to shell `grep` or `rg`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_discard_artifact`  (lines 604–608)

```
async def _discard_artifact(ctx: ToolContext, key: str) -> None
```

**Purpose**: Deletes an artifact blob that was created during a failed share operation. It is cleanup code that tries not to leave orphaned uploaded files behind.

**Data flow**: It receives the tool context and a blob key. It asks the blob store to delete that key; if deletion fails, it logs the failure instead of hiding the original problem.

**Call relations**: It is used by preview creation, staging, and the main share flow whenever an uploaded file or preview must be removed after an error.

*Call graph*: called by 3 (_shared_preview, _staged_share, share_file_handler); 1 external calls (log).


##### `_shared_preview`  (lines 611–676)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str, artifact_id: UUID, recorded: bool) -> ArtifactPreview | None
```

**Purpose**: Creates a small preview image for shareable document types, so a user can see a visual preview next to the download. If preview creation fails, the original shared file is still allowed to succeed.

**Data flow**: It receives the context, sandbox path, safe filename, artifact id, and whether the file was already recorded. It checks whether the extension supports previews, asks S3 for a temporary upload URL, sends the file to the preview service through the sandbox, parses the returned size, and returns preview metadata or nothing.

**Call relations**: It is called by `_staged_share` after the main file has been uploaded. It calls `_discard_artifact` if a newly created preview blob needs to be cleaned up after a failed render.

*Call graph*: calls 1 internal fn (_discard_artifact); called by 1 (_staged_share); 7 external calls (__init__, dumps, loads, PurePosixPath, quote, log, shell_path).


##### `_packed_directory`  (lines 696–725)

```
async def _packed_directory(ctx: ToolContext, scoped: str) -> str | None
```

**Purpose**: Turns a directory chosen for sharing into a `.tar.gz` archive inside the sandbox. This lets users share folders without the agent manually creating an archive first.

**Data flow**: It receives a sandbox-scoped path. It checks whether the path is a real directory and not a symbolic link, creates a tool output directory, runs `tar` inside the sandbox, and returns the archive path; if the path is not a directory, it returns nothing.

**Call relations**: It is called by `_staged_share` before measuring and uploading a shared item. Regular files skip this step, while directories become a single archive file for the rest of the share process.

*Call graph*: called by 1 (_staged_share); 3 external calls (quote, shell_path, uuid4).


##### `_share_request_fingerprint`  (lines 728–730)

```
def _share_request_fingerprint(spec: SharedFileSpec) -> str
```

**Purpose**: Creates a stable fingerprint for a share request. This helps detect whether a repeated share call is truly the same request or a conflicting reuse of the same idempotency key.

**Data flow**: It receives a shared-file specification, serializes it in a stable JSON order, hashes that JSON with SHA-256, and returns the hash string with a `sha256:` prefix.

**Call relations**: It is used by `_recorded_share` to compare against existing database records and by `_staged_share` when creating a new staged share.

*Call graph*: called by 2 (_recorded_share, _staged_share); 3 external calls (model_dump, sha256, dumps).


##### `_recorded_share`  (lines 733–785)

```
async def _recorded_share(ctx: ToolContext, spec: SharedFileSpec, artifact_id: UUID) -> _StagedShare | None
```

**Purpose**: Looks for an already-recorded artifact for the same turn and artifact id. This supports safe retries: the system can return the previous upload instead of uploading another copy.

**Data flow**: It receives the context, requested file spec, and artifact id. It queries the workspace database for a matching shared artifact, checks that the stored fingerprint matches the current request, rebuilds preview metadata if present, and returns a staged-share record or nothing.

**Call relations**: It is called at the start of `_staged_share`. If it finds a valid previous record, staging can stop early and reuse that stored artifact.

*Call graph*: calls 1 internal fn (_share_request_fingerprint); called by 1 (_staged_share); 4 external calls (__init__, __init__, select, workspace_tx).


##### `_staged_share`  (lines 788–836)

```
async def _staged_share(ctx: ToolContext, spec: SharedFileSpec, artifact_id: UUID) -> _StagedShare
```

**Purpose**: Prepares one requested file for sharing by verifying it, optionally packing it, measuring it, uploading it to the artifact store, and creating preview metadata. It is the careful preflight step before anything is recorded as shared.

**Data flow**: It receives the context, one file spec, and an artifact id. It first checks for an existing recorded share, scopes the path to the workspace, packs directories, measures size and digest, chooses a safe download name, streams the file to blob storage, tries to create a preview, and returns all metadata needed for the database row and final URL.

**Call relations**: It is called once per requested file by `share_file_handler`. It uses `_recorded_share`, `_packed_directory`, `_shared_preview`, `_share_request_fingerprint`, and `_discard_artifact` to make staging retry-safe and clean up after failures.

*Call graph*: calls 5 internal fn (_discard_artifact, _packed_directory, _recorded_share, _share_request_fingerprint, _shared_preview); called by 1 (share_file_handler); 6 external calls (__init__, guess_type, PurePosixPath, workspace_path, measure_file, store_artifact).


##### `share_file_handler`  (lines 839–946)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares one or more workspace files with the user by uploading them to artifact storage, recording them in the database, and returning temporary download links. This is the official path for getting produced files out of the sandbox.

**Data flow**: It receives a list of files to share. It checks that artifact sharing is configured, stages every file first, writes database rows in share order, gathers artifact display names, optionally publishes artifacts to an external surface, mints expiring URLs, and returns JSON entries with URL, name, size, digest, and text/binary status.

**Call relations**: This is the handler behind the `share_file` tool. It calls `_staged_share` for each file and `_discard_artifact` if anything fails after blobs have been created, so partial failed shares do not leave stray files.

*Call graph*: calls 2 internal fn (_discard_artifact, _staged_share); 15 external calls (__init__, __init__, gather, publish_artifacts, now, timedelta, dumps, select, workspace_tx, artifact_object_names (+5 more)).


##### `_spawn_handles`  (lines 961–967)

```
def _spawn_handles(target: str, turn_id: UUID, moved: bool) -> str
```

**Purpose**: Builds the short status message returned when a spawned child agent is running in the background. It gives the caller the spawn id and explains how the result will arrive later.

**Data flow**: It receives the target name, child turn id, and whether the spawn was moved to the background because a new message arrived. It chooses the right explanatory text, builds a small JSON payload, and returns one combined string.

**Call relations**: It is called by `spawn_handler` whenever there is no immediate child output. It keeps background-spawn wording consistent for both explicitly backgrounded spawns and foreground spawns that were detached.

*Call graph*: called by 1 (spawn_handler); 1 external calls (dumps).


##### `spawn_handler`  (lines 970–1019)

```
async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult
```

**Purpose**: Delegates a typed subtask to a child agent or subagent profile and returns its result, question, or background handle. This lets the main agent split work into specialized smaller jobs.

**Data flow**: It receives a target, payload, background setting, optional display name, and optional model id. It asks the context to spawn the child, catches common user-correctable spawn errors, and then returns either a structured question, background status, or the child's validated JSON output.

**Call relations**: This is the handler behind the `spawn` tool. It uses `_spawn_handles` when the child is still running and otherwise depends on `ToolContext.spawn` to run the child turn.

*Call graph*: calls 1 internal fn (_spawn_handles); 4 external calls (__init__, __init__, spawn, dumps).


##### `ask_user_handler`  (lines 1027–1038)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Creates a structured question for the agent to ask the user in its next reply. It keeps the interaction inside the chat flow rather than opening a separate prompt.

**Data flow**: It receives a title, optional icon, and one or more questions. It packages them into JSON with an instruction telling the agent to ask and end its turn, then returns that text as the tool result.

**Call relations**: This is the handler behind the `ask_user` tool. The returned structure can be rendered by capable chat surfaces, while the agent also receives plain instructions for what to do next.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 1041–1053)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill and its dependencies into the sandbox, then returns the instructions and file tree needed to use it. A skill is a reusable bundle of workflow guidance and supporting files.

**Data flow**: It receives a skill name. It resolves the full dependency closure, materializes the skill files, installs them into the sandbox, builds the context text describing what was loaded, and returns that text.

**Call relations**: This is the handler behind the `load_skill` tool. It relies on the runtime skill loader to copy files and build the readable skill context.

*Call graph*: 4 external calls (__init__, __init__, load_skills, loaded_context).


##### `_grantee_agent_id`  (lines 1065–1089)

```
async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None
```

**Purpose**: Figures out which agent should receive access to a connected external account. It enforces the rule that only the workspace's main agent may connect an account on behalf of another agent.

**Data flow**: It receives the current context and an optional agent name. If no name is given, it returns nothing, meaning the current agent is the grantee. If a name is given, it loads active agents from the database, checks that the asking agent is the main one, resolves the target by name, and returns that target agent id when it differs from the current agent.

**Call relations**: It is called by `connect_account_handler` before creating the connection request. Doing this lookup early means the later private OAuth handoff already knows exactly which agent the grant is for.

*Call graph*: called by 1 (connect_account_handler); 2 external calls (select, workspace_tx).


##### `connect_account_handler`  (lines 1092–1105)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private account-connection flow, such as OAuth, for an external provider. It tells the member to use a private connection control rather than exposing authorization links in chat.

**Data flow**: It receives the provider name, whether the connection should be shared with the workspace, and an optional target agent name. It verifies there is a speaking member, resolves the grantee agent, validates that the provider exists, builds a connection request, and returns instructions plus the request JSON.

**Call relations**: This is the handler behind the `connect_account` tool. It calls `_grantee_agent_id` for optional agent delegation and the installed connect-flow service to validate the provider.

*Call graph*: calls 2 internal fn (_grantee_agent_id, require_speaker); 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 1115–1137)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks a workspace admin to provide secrets, such as API keys, through a private prompt instead of chat. This prevents secrets from appearing in the conversation transcript.

**Data flow**: It receives a reason and a small list of credential prompts. It verifies there is a speaking member, checks that credential storage is configured, requires that the speaker is an admin, seals the allowed credential slots into a signed request, and returns instructions plus the credential request JSON.

**Call relations**: This is the handler behind the `request_credentials` action. It uses context permission checks and raises an admin-required error when a non-admin tries to fill workspace-global credential slots.

*Call graph*: calls 2 internal fn (require_speaker, require_speaking_admin); 4 external calls (__init__, __init__, __init__, __init__).


##### `cancel_spawn_handler`  (lines 1140–1152)

```
async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult
```

**Purpose**: Cancels a running child spawn owned by this turn and reports its status. If the child already finished, cancellation does not undo its completed result.

**Data flow**: It receives a spawn id string. It checks that spawn control exists, converts the id to a UUID, asks the subagent controller to cancel it, and returns JSON with the spawn id and resulting status.

**Call relations**: This is the handler behind the `cancel_spawn` tool. It works through the same subagent controller that backs spawning and refuses contexts where spawn control is unavailable.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_spawn_handler`  (lines 1155–1175)

```
async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a spawned child agent, often to answer a question the child asked. The child either reads it while still running or uses it as the next turn if it has paused or finished.

**Data flow**: It receives a spawn id and message. It checks that spawn control and an idempotency key are available, converts the spawn id to a UUID, queues the message through the subagent controller, and returns JSON showing whether the message is queued or attached to a running turn.

**Call relations**: This is the handler behind the `message_spawn` tool. It is the counterpart to `spawn_handler` for continuing or answering spawned work after the initial delegation.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### Notification delivery
The notification extension queues messages for members and safely delivers them through the Notification app.

### `extensions/app_notification/ufo_ext_app_notification/deliver.py`

`domain_logic` · `notification tool invocation`

Most notifications can sit quietly in a portal, but sometimes the system decides a member should hear about something now. This file is the bridge from “there are notifications waiting” to “the member sees one clear message in their usual chat.” Think of it like a mailroom clerk who chooses the best mailbox, checks that the sender is authorized, and stamps the letters so they are not sent twice.

The file defines the input shape for the tool: a list of notification references and the text the member should read. The main `deliver` function first proves it is running inside the Notification app and not from some other agent pretending to use the same action name. It then identifies the member, looks up the named notifications that are still undelivered, and searches for the member’s most recently used durable conversation. “Durable” here means a chat surface where messages are saved and can be replied to later.

If a usable conversation exists, the file asks that conversation’s agent to say the notification text in its own voice, under the member’s authority. If the target agent was archived, it tries the next conversation. If no chat channel is available, it marks the notifications as delivered only to the portal. It also records the delivery turn on the notification rows, which prevents repeat pushes and stops notification delivery from recursively creating more notifications about itself.

#### Function details

##### `_require_ext`  (lines 90–93)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the tool was called with the Notification extension’s runtime context. Without that context, the code would not know how to find conversations, invoke agents, or open the notification store.

**Data flow**: It receives a possible extension context. If the context is present, it returns it unchanged. If it is missing, it stops the flow by raising an error that says this action requires the app_notification context.

**Call relations**: `deliver` calls this at the very start, before doing any real work. It acts like checking that the delivery clerk has the right building keys before trying to enter the mailroom.

*Call graph*: called by 1 (deliver).


##### `_refusal`  (lines 96–97)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This builds a standard error result for cases where delivery should not happen, such as no valid notifications or a refused target conversation. It gives the caller a clear message instead of silently doing nothing.

**Data flow**: It receives a plain text explanation. It wraps that explanation in a `TextContent` object, then wraps that in a `ToolResult` marked as an error, and returns the result to the caller.

**Call relations**: `deliver` uses this whenever it needs to decline the requested delivery. It relies on `TextContent` and `ToolResult` to format the refusal in the same shape as other tool responses.

*Call graph*: called by 1 (deliver); 2 external calls (__init__, __init__).


##### `_require_notification_agent`  (lines 100–102)

```
async def _require_notification_agent(ext: ExtensionContext, ctx: ToolContext) -> None
```

**Purpose**: This guard checks that the agent calling `deliver` is actually the Notification app’s own provision. It prevents another extension from widening access just by naming the same action.

**Data flow**: It receives the extension context and the tool call context. It asks the notification store helper `inbox_agent_id` for the official Notification app agent id, compares it with the current turn’s agent id, and either allows execution to continue or raises an error.

**Call relations**: `deliver` calls this immediately after confirming the extension context exists. This is the file’s authorization checkpoint before it looks up notifications or sends any message.

*Call graph*: called by 1 (deliver); 1 external calls (inbox_agent_id).


##### `_names`  (lines 105–107)

```
def _names(refs: tuple[str, ...]) -> tuple[str, ...]
```

**Purpose**: This normalizes notification references into the internal names used by the store. It lets callers pass references with the notification kind prefix while the database lookup receives the bare names it expects.

**Data flow**: It receives a tuple of reference strings. For each one, it removes the leading notification-kind prefix if present, then returns a new tuple of cleaned names.

**Call relations**: `deliver` calls this just before asking `NotificationStore` for deliverable rows. It is the small translation step between user-facing references and store-facing identifiers.

*Call graph*: called by 1 (deliver).


##### `deliver`  (lines 110–140)

```
async def deliver(ctx: ToolContext, args: DeliverInput) -> ToolResult
```

**Purpose**: This is the main tool handler that sends a batch of named notifications to a member’s most recently used durable chat, or marks them as portal-only if no such chat exists. It enforces who may call the action, which notifications are eligible, and whether a delivery has already happened in the same turn.

**Data flow**: It receives the tool call context and a `DeliverInput` containing notification refs and message text. First it gets the extension context, verifies the caller is the Notification app, and extracts the member id from the authority. Then it opens `NotificationStore`, converts refs to internal names, and fetches undelivered rows for that member. If there is nothing valid to send, it returns an error result. Otherwise it walks through the member’s reachable conversations, tries to invoke the chosen conversation’s agent with the notification message, and records the successful relay turn on the notification rows. If all chat options are unavailable, it marks the rows as delivered to the portal only. The output is a `ToolResult` explaining whether the message was delivered, refused, or left portal-only.

**Call relations**: `deliver` is the function registered as the handler in the `DELIVER` tool definition. During a tool call, it calls `_require_ext`, `_require_notification_agent`, `_names`, and `_refusal` for its guardrails and formatting. It creates `NotificationStore` to read and mark notification rows, creates `MemberAuthority` so the relay turn is made under the member’s authority, and returns `TextContent` inside `ToolResult` so the caller gets a clear result message.

*Call graph*: calls 4 internal fn (_names, _refusal, _require_ext, _require_notification_agent); 6 external calls (__init__, __init__, __init__, __init__, authority_member_id, wall).


### `extensions/app_notification/ufo_ext_app_notification/notify_tool.py`

`domain_logic` · `request handling during an agent tool call`

This file is the producer side of the Notification app. In everyday terms, it is like a drop box: an agent can place one note into the right member’s notification inbox, but it cannot choose an arbitrary recipient or create loops.

The file defines what the tool accepts: a short `subject`, which is the stable thing the message is about, and a `body`, which explains what happened and why it matters. These limits matter because repeated messages with the same subject can be folded together instead of becoming a pile of duplicates.

When the tool runs, it first checks whose authority the current turn has. If the turn belongs only to the workspace and not to a specific member, there is no person to notify, so it refuses. It then checks that the Notification extension is available, that this turn is not itself a notification delivery, that the Notification app has an inbox agent, and that the current agent is not the notification agent trying to notify itself.

Only after those fences pass does it write to `NotificationStore`, the storage layer for notification rows. The result returned to the model is intentionally small: either a refusal reason, “Queued,” or a message saying this was folded into an existing notification.

#### Function details

##### `_require_ext`  (lines 83–86)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This function makes sure the Notification app’s extension context is present before the tool tries to use it. Without that context, the tool would not know how to reach the app’s storage and workspace-specific setup.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it stops the run by raising an error saying that `notify` requires the app notification extension context.

**Call relations**: The main `notify` function calls this after it has confirmed there is a member to notify. It acts as a gate before `notify` creates a `NotificationStore` or looks up the notification inbox.

*Call graph*: called by 1 (notify).


##### `_refusal`  (lines 89–90)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This function packages a refusal message into the standard tool-result shape. It is used when the tool is not allowed to queue a notification, but it still needs to tell the model exactly why.

**Data flow**: It receives plain text explaining the refusal. It wraps that text in a `TextContent` object, then wraps that in a `ToolResult` marked as an error. The output is a tool response that the calling model can read and react to.

**Call relations**: The `notify` function calls this for every blocked path: no member, missing inbox, self-notification, delivery-turn notification, or a refusal returned by the store. It uses the shared `TextContent` and `ToolResult` response types so refusals look like normal tool responses to the rest of the system.

*Call graph*: called by 1 (notify); 2 external calls (__init__, __init__).


##### `notify`  (lines 93–122)

```
async def notify(ctx: ToolContext, args: NotifyInput) -> ToolResult
```

**Purpose**: This is the actual tool handler that tries to queue a notification for the member represented by the current turn. It protects against unsafe or meaningless notifications before writing anything to the notification store.

**Data flow**: It receives the tool context, which contains the current turn, authority, and extension context, plus the user-supplied notification subject and body. It extracts the member id from the turn’s authority, checks the extension context, asks the store whether this turn is already delivering a notification, finds the Notification app inbox, and rejects self-notification. If all checks pass, it posts the message with details such as the sending agent, turn, and conversation. It returns either an error-style refusal, a success message saying the notification was queued, or a success message saying it was folded into an existing notification with the same subject.

**Call relations**: The tool system uses this function as the handler registered in `NOTIFY_TOOL`. During a tool call, it relies on `authority_member_id` to learn who the notification is for, `_require_ext` to obtain the extension context, `NotificationStore` to check and write notification records, `inbox_agent_id` to find the provisioned notification inbox, and `_refusal` to produce clear blocked responses.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 5 external calls (__init__, __init__, __init__, authority_member_id, inbox_agent_id).


### Extension package boundaries
These package markers establish importable namespaces for several optional extension areas.

### `extensions/coding/ufo_ext_coding/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a label on a folder that says, “this folder is an importable module.” This particular file is empty, so it does not define any functions, classes, settings, or startup behavior. Its value is structural: it allows other parts of the system to refer to `extensions.coding.ufo_ext_coding` using normal Python import paths. Without it, depending on the Python version and packaging setup, imports from this folder could become unreliable or fail in environments that expect traditional package markers. Think of it as a nameplate on an office door: it does not do the work inside the office, but it helps people and tools find the right place.


### `extensions/composio/ufo_ext_composio/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to this extension using package-style imports, such as importing modules from `ufo_ext_composio`. Think of it like a label on a folder: the label does not contain the documents, but it tells Python that the folder belongs in the project's import system. Because the file is empty, it does not run setup code, expose shortcut names, or change package behavior. If it were missing in environments that still rely on traditional package markers, imports from this extension folder could fail or behave differently.


### `extensions/debugger/ufo_ext_debugger/__init__.py`

`other` · `import/package discovery`

Python projects often use an `__init__.py` file as a signpost that says, “this folder is a package.” That is what this file does for the debugger extension. It does not define any functions, classes, settings, or startup work. Its value is structural: it lets Python and project tooling recognize `extensions/debugger/ufo_ext_debugger` as an importable unit. Without it, some environments or packaging tools might not treat this directory as part of the debugger extension in the expected way. Think of it like a label on a folder in a filing cabinet. The label does not contain the documents, but it helps the rest of the system find and refer to the folder correctly.


### Diagnostics and external tools
These tools report deployment problems and bridge agents to externally configured MCP tool servers.

### `extensions/debugger/ufo_ext_debugger/report.py`

`domain_logic` · `tool invocation during a conversation turn`

This file exists for problems that cannot be fixed inside the current conversation turn, such as broken authentication, missing credentials, or a service that keeps failing. Instead of trying to solve those silently or sending details to the user, the agent can call this tool to create a telemetry record that engineers can see later.

The main input shape is `ReportProblemInput`. It asks for four things: the agent's own short description of what went wrong, a fixed category so reports can be grouped, an impact level so engineers know how costly the problem is, and an origin saying whether this was a real fault or a member asked for it to be reported. The fixed category list matters because free text is hard to count or route reliably.

The tool is deliberately cautious about secrets. The problem text is length-limited, and it rejects URLs that include user information before the `@` sign, because that pattern can expose credentials such as proxy tokens. The code also tells agents not to paste command output or stack traces.

When `report_problem` runs, it builds a debugger link using the current workspace, conversation, and turn. Then it emits a warning record through telemetry. It does not store anything itself, retry, deduplicate, or reply with an engineer's answer. Like dropping a clearly labeled note into an operations inbox, one call creates one report.

#### Function details

##### `ReportProblemInput._is_the_agents_own_account`  (lines 112–115)

```
def _is_the_agents_own_account(cls, value: str) -> str
```

**Purpose**: This validation step protects against accidentally reporting secrets. It checks that the agent's problem description does not contain a credentialed URL, which is a URL with login-like information embedded in it.

**Data flow**: It receives the proposed `problem` text before the input is accepted. It searches that text for a URL pattern containing user information before an `@` sign. If it finds one, it rejects the input with an error; otherwise it returns the same text unchanged.

**Call relations**: This runs automatically when a `ReportProblemInput` is created for the tool call. It acts as a gate before `report_problem` can send anything to telemetry, so unsafe problem text is stopped before it leaves the tool input stage.


##### `report_problem`  (lines 118–139)

```
async def report_problem(ctx: ToolContext, args: ReportProblemInput) -> ToolResult
```

**Purpose**: This is the tool's action. It turns the agent's report into one warning telemetry event that engineers can later read, including enough IDs and usually a debugger link to find the exact conversation turn.

**Data flow**: It receives the tool context, which contains deployment and turn information, and the validated report input. It builds a link to the debugger surface if a public base URL is available, extracts the member ID when there is one, and sends a warning event with the problem, category, impact, origin, turn IDs, agent ID, member ID, and debug URL. It returns a short tool result telling the agent that the report was sent and that no answer will arrive in the conversation.

**Call relations**: This function is registered as the handler for the `report_problem` tool. When the tool is called, it uses `authority_member_id` to identify the member where possible, calls `warn` to publish the telemetry event, and wraps the user-facing confirmation in `TextContent` and `ToolResult`.

*Call graph*: 4 external calls (__init__, __init__, authority_member_id, warn).


### `extensions/mcp/ufo_ext_mcp.py`

`io_transport` · `extension load and tool request handling`

MCP, or Model Context Protocol, is a standard way for an outside service to publish tools that an agent can use. This file is the bridge between the agent and those outside MCP servers. Without it, a workspace could store MCP server details, but the agent would not know how to list or call the tools those servers provide.

The file defines one credential slot, called `mcp_servers`, where a workspace stores named servers. Each server has an HTTP or HTTPS URL and may have a bearer token, which is a secret sent in the `Authorization` header. The code validates this configuration so the agent does not make blind calls to malformed or unknown destinations.

The extension exposes two tools to the agent. `list_mcp_tools` first gives a small catalog: tool names, short summaries, parameter names, and required parameters. If the agent asks for specific tool names, it returns their full input schemas. This two-step design avoids dumping a huge catalog into a single tool result. `call_mcp_tool` then sends JSON arguments to a chosen remote tool and returns either structured JSON or joined text.

Because MCP servers are external, their replies are treated as untrusted content. The file also enforces one-mebibyte request and response limits, like a parcel office refusing packages that are too large rather than silently cutting them open.

#### Function details

##### `McpServer._http_url`  (lines 81–84)

```
def _http_url(cls, value: str) -> str
```

**Purpose**: Checks that an MCP server URL starts with HTTP or HTTPS. This prevents storing server addresses that this extension cannot safely or correctly contact.

**Data flow**: A proposed URL string goes in. The function compares it with the allowed URL pattern. The same URL comes out if it is valid; otherwise validation fails with a clear message.

**Call relations**: This is called automatically when a `McpServer` configuration object is built or validated. It protects later steps, such as creating the HTTP client, from receiving unusable server addresses.


##### `McpServerUpdate._name`  (lines 99–103)

```
def _name(cls, value: str) -> str
```

**Purpose**: Cleans and checks the name used when adding or updating one MCP server. A server name must not be empty, because later tool calls use that name to choose which server to contact.

**Data flow**: A submitted name goes in. The function trims spaces from both ends and checks that something remains. The cleaned name comes out, or validation fails if it was blank.

**Call relations**: This runs during validation of a single-server update. Its output becomes the key used when `merge_mcp_server` adds or replaces a server in the saved configuration.


##### `McpServerRemoval._name`  (lines 112–116)

```
def _name(cls, value: str) -> str
```

**Purpose**: Cleans and checks the name used when removing an MCP server. It makes sure a removal request names a real, non-empty target.

**Data flow**: A submitted removal name goes in. The function strips surrounding spaces and rejects it if nothing remains. The cleaned name comes out for the removal logic to use.

**Call relations**: This runs when `merge_mcp_server` receives a request like `{name, remove: true}`. It gives that function a safe server name to look up and delete.


##### `merge_mcp_server`  (lines 119–157)

```
def merge_mcp_server(current: str | None, submitted: str) -> str
```

**Purpose**: Updates the stored MCP server credential value. It accepts several user-friendly forms: replace the whole server map, add or update one named server, or remove one named server.

**Data flow**: The existing saved JSON configuration, if any, and a newly submitted JSON string go in. The function parses the submitted JSON, validates URLs and names, combines it with the current configuration when needed, preserves an old auth token when updating a server without providing a new token, and returns a normalized JSON string. If the submission is malformed or refers to a missing server during removal, it raises a credential error.

**Call relations**: This function is plugged into the `mcp_servers` credential slot by `manifest`. It runs when someone changes that credential, before any tool call uses the stored server list.

*Call graph*: 4 external calls (__init__, __init__, __init__, loads).


##### `mcp_client`  (lines 177–183)

```
def mcp_client(server: McpServer) -> Client
```

**Purpose**: Builds a network client for one configured MCP server. It includes the optional bearer token and sets a timeout so a remote server cannot hang the tool call forever.

**Data flow**: A validated `McpServer` object goes in. The function creates HTTP headers if an auth token exists, attaches those headers to a streamable HTTP transport, and returns a FastMCP client ready to list or call tools.

**Call relations**: `_list_mcp_tools` and `_call_mcp_tool` call this after `_server` has found the requested server. The returned client owns the MCP protocol details, such as the initialization handshake and streaming format.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools); 2 external calls (Client, StreamableHttpTransport).


##### `_server`  (lines 186–198)

```
async def _server(ctx: ToolContext, name: str) -> McpServer
```

**Purpose**: Looks up one named MCP server from the workspace's stored credentials. It refuses to continue if the extension context is missing or if the requested server name is not configured.

**Data flow**: A tool context and a server name go in. The function reads the `mcp_servers` credential JSON, validates it into configuration objects, searches for the named server, and returns that server. If it cannot do this safely, it raises an error.

**Call relations**: Both `_list_mcp_tools` and `_call_mcp_tool` call this before making any network request. It is the gate that turns a friendly server name, such as `docs`, into the actual URL and token.

*Call graph*: called by 2 (_call_mcp_tool, _list_mcp_tools).


##### `_list_mcp_tools`  (lines 201–222)

```
async def _list_mcp_tools(ctx: ToolContext, args: ListMcpToolsInput) -> ToolResult
```

**Purpose**: Implements the agent-facing tool that lists tools exposed by an MCP server. It gives either a compact browseable catalog or full schemas for specific tools the agent plans to call.

**Data flow**: A tool context and validated input containing a server name and optional tool names go in. The function finds the configured server, connects to it, asks for its tool list, and then formats the result. With no requested names it returns compact catalog entries; with requested names it checks they exist and returns fuller schema entries, while refusing overly broad schema requests.

**Call relations**: This is registered as the handler for `list_mcp_tools` in `manifest`. It relies on `_server` and `mcp_client` to reach the external server, then hands formatting to `_catalog_entry`, `_schema_entry`, `_bounded_schemas`, and `_json_result`.

*Call graph*: calls 6 internal fn (_bounded_schemas, _catalog_entry, _json_result, _schema_entry, _server, mcp_client).


##### `_idempotent`  (lines 225–227)

```
def _idempotent(tool: McpTool) -> bool
```

**Purpose**: Reads whether an MCP tool claims it is idempotent, meaning repeating the same call should not cause an extra side effect. This helps the agent understand which tools are safer to retry.

**Data flow**: An MCP tool description goes in. The function checks its annotations for the idempotent hint and returns `true` or `false`, defaulting to `false` if no hint is present.

**Call relations**: `_catalog_entry` and `_schema_entry` call this while preparing tool information for the agent. It adds a small but useful safety clue to both compact and full listings.

*Call graph*: called by 2 (_catalog_entry, _schema_entry).


##### `_catalog_entry`  (lines 230–246)

```
def _catalog_entry(tool: McpTool) -> JsonValue
```

**Purpose**: Turns one full MCP tool description into a short catalog item. The catalog is meant for browsing, so it keeps names, a short summary, parameter names, required fields, and the idempotent hint.

**Data flow**: A full MCP tool object goes in. The function reads its input schema, extracts property names and required names, shortens the description with `_summary`, adds the idempotent flag, and returns a JSON-friendly dictionary.

**Call relations**: `_list_mcp_tools` calls this for every remote tool when the agent asks to browse a server without requesting full schemas. It uses `_summary` and `_idempotent` to keep the result useful but small.

*Call graph*: calls 2 internal fn (_idempotent, _summary); called by 1 (_list_mcp_tools).


##### `_summary`  (lines 249–255)

```
def _summary(description: str) -> str
```

**Purpose**: Creates a short human-readable summary from a longer tool description. It is designed for docstring-like descriptions where the first line is usually the useful overview.

**Data flow**: A description string goes in. The function trims it, takes the first line, keeps text up to the first sentence break, and limits it to the maximum summary length. A compact summary string comes out.

**Call relations**: `_catalog_entry` calls this when building the browseable tool catalog. It keeps the catalog from being flooded with long argument documentation.

*Call graph*: called by 1 (_catalog_entry).


##### `_schema_entry`  (lines 258–264)

```
def _schema_entry(tool: McpTool) -> JsonValue
```

**Purpose**: Builds the detailed listing for one selected MCP tool. This is what the agent reads before calling a tool so it knows exact parameter names and input structure.

**Data flow**: A full MCP tool object goes in. The function copies out the tool name, full description, input schema, and idempotent flag into a JSON-friendly dictionary.

**Call relations**: `_list_mcp_tools` calls this only for tool names the agent specifically requested. It uses `_idempotent` to include the same retry-safety clue as the compact catalog.

*Call graph*: calls 1 internal fn (_idempotent); called by 1 (_list_mcp_tools).


##### `_call_mcp_tool`  (lines 267–278)

```
async def _call_mcp_tool(ctx: ToolContext, args: CallMcpToolInput) -> ToolResult
```

**Purpose**: Implements the agent-facing tool that invokes one remote MCP tool. It checks request size, performs the remote call, and returns either the remote structured result or text result.

**Data flow**: A tool context and validated input containing server name, tool name, and JSON arguments go in. The function finds the server, measures the serialized arguments, rejects them if they are too large, calls the remote MCP tool, and then converts the reply into a `ToolResult`. If the remote server reports an error, it returns a tool failure marked as untrusted.

**Call relations**: This is registered as the handler for `call_mcp_tool` in `manifest`. It depends on `_server` and `mcp_client` for the connection, uses `_call_failed` for remote refusals, and uses `_json_result` or `_joined_text` to shape successful replies.

*Call graph*: calls 5 internal fn (_call_failed, _joined_text, _json_result, _server, mcp_client); 2 external calls (__init__, dumps).


##### `_call_failed`  (lines 281–296)

```
def _call_failed(args: CallMcpToolInput, result: CallToolResult) -> ToolFailure
```

**Purpose**: Turns an error reported by the MCP server into a clear tool failure for the agent. It preserves the server's own diagnostic details when available, because those details may explain what argument or condition was wrong.

**Data flow**: The original call arguments and the remote call result go in. The function joins any text error blocks, uses that text as the summary or supplies a fallback message, serializes structured error content when present, and returns a `ToolFailure` tied to the server and tool name.

**Call relations**: `_call_mcp_tool` calls this when the remote MCP server says the tool call failed. The failure is then returned to the agent as untrusted content rather than being treated like a local system error.

*Call graph*: calls 1 internal fn (_joined_text); called by 1 (_call_mcp_tool); 2 external calls (__init__, dumps).


##### `_joined_text`  (lines 299–300)

```
def _joined_text(content: Sequence[object]) -> str
```

**Purpose**: Collects text blocks from an MCP response into one plain string. MCP results can contain different kinds of content, and this function keeps only the text pieces.

**Data flow**: A sequence of content blocks goes in. The function selects blocks that are MCP text content, takes their text, joins them with newlines, and returns the combined string.

**Call relations**: `_call_mcp_tool` uses this when a successful result has no structured JSON content. `_call_failed` uses it to extract a readable error message from a failed remote call.

*Call graph*: called by 2 (_call_failed, _call_mcp_tool).


##### `_bounded`  (lines 303–306)

```
def _bounded(text: str) -> str
```

**Purpose**: Enforces the maximum response size for MCP results. It fails loudly if a result is too large instead of silently cutting it down and possibly changing its meaning.

**Data flow**: A text string goes in. The function measures its byte size after encoding and returns the same string if it fits. If it is larger than the allowed limit, it raises an MCP size error.

**Call relations**: `_json_result` calls this just before wrapping JSON text into a tool result. That means normal successful outputs pass through the same response-size gate.

*Call graph*: called by 1 (_json_result); 1 external calls (__init__).


##### `_bounded_schemas`  (lines 309–326)

```
def _bounded_schemas(payload: dict[str, JsonValue], tools: int) -> ToolResult
```

**Purpose**: Prevents the agent from asking for too many full tool schemas at once when a smaller request would work better. This keeps schema lookup useful instead of producing an oversized, partial, or offloaded result.

**Data flow**: A payload containing schema entries and the number of requested tools go in. If multiple schemas would make the listing too large, the function raises a friendly error telling the agent to ask for fewer tools. Otherwise it passes the payload to `_json_result` and returns the final tool result.

**Call relations**: `_list_mcp_tools` calls this when the agent requested full schemas for named tools. It sits between schema formatting and final JSON output to enforce the extension's two-step browsing design.

*Call graph*: calls 1 internal fn (_json_result); called by 1 (_list_mcp_tools); 1 external calls (dumps).


##### `_json_result`  (lines 329–330)

```
def _json_result(payload: dict[str, JsonValue]) -> ToolResult
```

**Purpose**: Packages a JSON-friendly dictionary as a tool result. It is the common exit path for successful listing and calling operations.

**Data flow**: A dictionary payload goes in. The function serializes it to JSON text, checks the size with `_bounded`, wraps the text in `TextContent`, and returns a `ToolResult`.

**Call relations**: `_list_mcp_tools`, `_bounded_schemas`, and `_call_mcp_tool` use this whenever they need to return successful data to the agent. It centralizes JSON formatting and response-size enforcement.

*Call graph*: calls 1 internal fn (_bounded); called by 3 (_bounded_schemas, _call_mcp_tool, _list_mcp_tools); 3 external calls (__init__, __init__, dumps).


##### `manifest`  (lines 333–364)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It declares the extension name and version, the two tools the agent may call, and the credential slot used to store MCP server configuration.

**Data flow**: No runtime input is needed. The function builds and returns a manifest containing two tool definitions, their input models and handler functions, plus the `mcp_servers` credential slot and its merge function.

**Call relations**: The host calls this when loading the extension. The manifest is how the rest of the system learns that `_list_mcp_tools` and `_call_mcp_tool` exist and that `merge_mcp_server` should process credential updates.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Automation and stateful execution
These extensions watch changing state and provide persistent sandbox REPL sessions for iterative code execution.

### `extensions/monitors/ufo_ext_monitors/monitor_tool.py`

`domain_logic` · `tool call / request handling`

This file is the front door for creating a durable monitor. A monitor is like leaving a note with an alarm clock: “Keep checking this thing, and wake me once something changes, keeps failing, or the deadline arrives.” The agent supplies a short name, a shell command to run, how often to check, a deadline, and instructions for the future turn that will run when the monitor fires.

The important safety choice is that the command is run once right away, during the current live tool call. If the command is broken, the tool refuses to create the monitor, so the system does not leave behind a silent job that will fail later. If it works, the command’s output becomes the baseline. Later probe output is compared against that baseline byte for byte, so the command should avoid changing timestamps, counters, or other noisy text unless those changes are truly meaningful.

The file also enforces limits: only a capped number of monitors may be armed in one conversation, names must be unique, and deadlines cannot exceed the configured maximum. When everything is valid, it stores the monitor record through `MonitorStore` and returns a message telling the agent to show `ai_response` and end its turn.

#### Function details

##### `_require_ext`  (lines 77–80)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This small guard makes sure the monitor tool has the extension context it needs to reach monitor storage and settings. Without that context, arming a monitor would not know where to save its durable watch.

**Data flow**: It receives the extension context from the tool context. If the value is missing, it stops the operation by raising an error; if it is present, it returns that same context unchanged so the rest of the monitor setup can use it.

**Call relations**: The `monitor` function calls this at the start of creating a `MonitorStore`. It acts as the checkpoint before any monitor data is read or written.

*Call graph*: called by 1 (monitor).


##### `_refusal`  (lines 83–84)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This helper builds a standard error result when the tool refuses to arm a monitor. It keeps all refusal replies shaped the same way, whether the problem is too many monitors, a duplicate name, or a failing probe command.

**Data flow**: It takes a plain text explanation of what went wrong. It wraps that text in a `TextContent` message and returns a `ToolResult` marked as an error, so the caller sees a clear failure instead of a saved monitor.

**Call relations**: The `monitor` function calls this whenever it must stop before storing anything. It hands the final refusal message directly back to the tool caller.

*Call graph*: called by 1 (monitor); 2 external calls (__init__, __init__).


##### `monitor`  (lines 87–134)

```
async def monitor(ctx: ToolContext, args: MonitorInput) -> ToolResult
```

**Purpose**: This is the actual tool action that arms a monitor. It validates the request, runs the probe command once now, saves the monitor only if that probe succeeds, and returns the baseline output plus instructions to end the turn.

**Data flow**: It receives the current tool context and a validated `MonitorInput`. It reads the conversation and agent information from the context, checks existing monitors from `MonitorStore`, rejects requests that exceed the cap or reuse a name, then runs the supplied shell command in the sandbox with a timeout. If the command fails, it returns an error and saves nothing. If it succeeds, it records the current time, caps the captured output for storage, writes a new monitor row with the deadline, next probe time, instructions, metadata, and creator identity, then returns a JSON payload containing the armed monitor name, baseline, deadline, interval, reason, and next steps.

**Call relations**: This function is the handler attached to the exported `MONITOR_TOOL`. During a tool call, the framework invokes it with the user’s monitor request. It relies on `_require_ext` before opening the monitor store, uses `_refusal` for all early exits, runs the sandbox probe before saving, then hands the saved monitor information back in a `ToolResult` so the agent can respond and end the turn.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 10 external calls (__init__, __init__, __init__, now, timedelta, dumps, authority_member_id, capped, qualified_name, stderr_tail).


### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the import system that the surrounding folder should be treated as a package: a named bundle of related code. Here, that bundle is `ufo_ext_repl`, which appears to belong to a REPL extension. A REPL is an interactive prompt where a user can type commands, run them, and see results immediately.

Because this file is empty, it does not create objects, run setup steps, or expose helper functions. Its value is structural. It is like a label on a folder in a filing cabinet: the label does not contain the documents, but it helps the system know that the folder is meant to be used as one organized unit.

Without this file, some Python tooling or older import behavior might not recognize the directory as a normal package. Other files may still do the real work, but this file helps make imports predictable.


### `extensions/repl/ufo_ext_repl/manifest.py`

`orchestration` · `request handling and tool registration`

This file is the front door and engine room for two interactive coding tools. One runs JavaScript with Node.js, mainly for browser automation and visual work. The other runs Python for Excel spreadsheet work with openpyxl. A REPL is an interactive coding session; here, it works like a notebook where each successful cell is saved and replayed before the next one.

The main problem this file solves is safe persistence. If a snippet succeeds, its code is appended to a saved workspace file. If it fails or times out, it is not saved. That matters because otherwise a broken half-run could poison every later call. Think of it like writing in pen only after the experiment worked; failed scratch work stays on scrap paper.

For each call, the file builds a temporary run file from the saved state plus the new code, runs it in the sandbox, records metrics, and returns stdout, stderr, exit code, and sometimes images. JavaScript gets an extra helper called emitImage so code can return screenshots or generated images inline. Long-running code is not killed just because the caller stopped waiting; instead, the result gives task handles so it can be inspected later. The manifest at the end advertises these tools and related data-analysis skills to the host system.

#### Function details

##### `_meter_run`  (lines 75–91)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one measurement for a REPL execution, including which tool ran and how the interpreter exited. This helps operators tell the difference between user code failing and the environment itself being broken.

**Data flow**: It receives the current tool context, the tool name, and an exit code. It turns the current subagent profile into a metric label, folds unusual exit codes into a safe bucket, and sends a count to the observability system. It does not return data; it updates monitoring outside the tool result.

**Call relations**: After either `js_repl` or `xlsx_repl` finishes running the interpreter, they call this before shaping the final answer. It hands the measurement off to `emit_metric`, using `turn_profile` to describe the active profile.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 94–113)

```
def global_modules_link(directory: str, roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds a shell command that makes globally installed Node.js packages visible to the JavaScript run file. This is needed because ES modules do not automatically use the usual global module search path.

**Data flow**: It takes a workspace directory and a set of module roots. It returns a shell script string that creates a local `node_modules` folder and symlinks packages from the available global roots into it, while quoting paths safely.

**Call relations**: `js_repl` calls this just before running Node. The returned command is executed in the sandbox so JavaScript snippets can import packages such as Playwright by name.

*Call graph*: called by 1 (js_repl); 1 external calls (shell_path).


##### `js_emit_relative`  (lines 120–128)

```
def js_emit_relative(call: str) -> str
```

**Purpose**: Creates the workspace-relative filename where one JavaScript call should write emitted images. Each call gets its own image log so a long-running old call cannot overwrite a newer call's images.

**Data flow**: It receives a short call identifier. It combines that identifier with the REPL state directory and returns a relative JSON-lines path for image output.

**Call relations**: `js_repl` calls this while preparing a JavaScript execution. The path it returns is then used by `js_emit_prelude` and later read by `_emitted_images`.

*Call graph*: called by 1 (js_repl).


##### `js_emit_prelude`  (lines 131–168)

```
def js_emit_prelude(emit_relative: str) -> str
```

**Purpose**: Generates JavaScript setup code that defines a global `emitImage` function. User code can call that function to send images, such as screenshots, back in the tool response.

**Data flow**: It receives the image output path for this call. It returns JavaScript source text that accepts buffers, byte arrays, base64 strings, or simple image objects, limits image size and count, and writes the latest images as JSON lines.

**Call relations**: `js_repl` places this generated code at the start of the temporary JavaScript run file. Later, `_emitted_images` reads the file that this prelude wrote.

*Call graph*: called by 1 (js_repl); 1 external calls (dumps).


##### `ReplStateUnreadable.__init__`  (lines 258–260)

```
def __init__(self, failure: ToolFailure) -> None
```

**Purpose**: Wraps a state-read or state-clear failure in a special exception that carries a ready-made tool failure response. This prevents the REPL from accidentally replacing existing session state with incomplete new state.

**Data flow**: It receives a `ToolFailure` object. It stores that failure on the exception and uses the failure summary as the exception message.

**Call relations**: `_state_failed` creates this exception when sandbox file operations fail. `_candidate_source` raises it, and the top-level REPL handlers catch it and return its failure result instead of running code.

*Call graph*: called by 1 (_state_failed).


##### `_state_failed`  (lines 263–279)

```
def _state_failed(verb: str, path: str, result: ExecResult) -> ReplStateUnreadable
```

**Purpose**: Creates a clear failure object for cases where the saved REPL state cannot be read or cleared. Its message explains that no code ran and no state was changed.

**Data flow**: It receives the attempted action, the state path, and the sandbox command result. It builds command diagnostics from the exit code, output, error text, and timeout information, then wraps them in `ReplStateUnreadable`.

**Call relations**: `_candidate_source` calls this when removing or reading the state file fails. It constructs the `ToolFailure` and passes it into `ReplStateUnreadable.__init__`.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_candidate_source); 2 external calls (__init__, __init__).


##### `_candidate_source`  (lines 294–306)

```
async def _candidate_source(ctx: ToolContext, relative: str, path: str, code: str, reset: bool) -> ReplSource
```

**Purpose**: Builds the full source code that should be tried for the next REPL call. It combines previously committed code with the new snippet, unless the caller asked for a reset.

**Data flow**: It receives the tool context, relative and full state paths, the new code, and a reset flag. If reset is requested, it tries to delete the saved state. If no state exists, it returns just the new code. Otherwise, it reads the saved code and returns both the combined source and the committed old part.

**Call relations**: Both `js_repl` and `xlsx_repl` call this before writing their temporary run files. If state access fails, it uses `_state_failed` so the handlers can stop safely instead of running against missing state.

*Call graph*: calls 1 internal fn (_state_failed); called by 2 (js_repl, xlsx_repl); 2 external calls (__init__, shell_path).


##### `_redeclared_from_state`  (lines 323–331)

```
def _redeclared_from_state(committed: str, output: str) -> bool
```

**Purpose**: Checks whether a JavaScript “already declared” error is caused by a name saved in earlier REPL state. This lets the tool give the right advice: reset the state, rather than endlessly editing the new snippet.

**Data flow**: It receives the committed JavaScript source and the combined output text from the failed run. It looks for Node's redeclaration error, extracts the name, then searches the committed source for a real declaration of that exact name. It returns true or false.

**Call relations**: `js_repl` calls this after a JavaScript run finishes. Its answer decides whether `_repl_result` should include the special redeclaration notice.

*Call graph*: called by 1 (js_repl); 2 external calls (escape, search).


##### `_repl_result`  (lines 334–350)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=(), hint: str='') -> ToolResult
```

**Purpose**: Turns a completed interpreter run into the standard tool response. It includes output text, error text, exit code, optional images, and a warning when failed code was not saved.

**Data flow**: It receives stdout, stderr, an exit code, optional image contents, and an optional hint. It builds a JSON text payload and marks the result as an error if the exit code is nonzero. Images are attached after the text payload.

**Call relations**: `js_repl` and `xlsx_repl` call this after runs that actually ended. It creates `TextContent` and `ToolResult` objects for the host system to return to the caller.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_expired_result`  (lines 353–369)

```
def _expired_result(run: TaskRun, applied_s: int) -> ToolResult
```

**Purpose**: Builds the response for a REPL call that exceeded the foreground wait time. It explains that the saved REPL state did not advance and, when possible, gives handles for the still-running background task.

**Data flow**: It receives the task run record and the timeout duration that was applied. If there is no process id, it returns a timeout notice as an error. If the process is still reachable, it returns task id, log path, and process id information so the caller can inspect it later.

**Call relations**: `js_repl` and `xlsx_repl` call this when `run_task` reports a timeout. It relies on `timeout_notice` for the human message and `task_handles` for follow-up access.

*Call graph*: called by 2 (js_repl, xlsx_repl); 4 external calls (__init__, __init__, task_handles, timeout_notice).


##### `_emitted_images`  (lines 377–391)

```
async def _emitted_images(ctx: ToolContext, emit_relative: str, emit_path: str) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code sent through `emitImage` and converts them into image attachments for the tool response. It ignores malformed image records rather than failing the whole tool call.

**Data flow**: It receives the tool context plus the relative and full image-log paths. If the file exists, it reads it, removes it, parses recent JSON-lines entries, validates each as an emitted image, and returns image content objects.

**Call relations**: `js_repl` calls this after a JavaScript run before building the final result. It reads the file written by the code generated from `js_emit_prelude`.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, shell_path).


##### `js_repl`  (lines 394–427)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs a stateful JavaScript ES-module snippet in the sandbox. It supports persistent successful code, reset, Node package linking, timeout-aware background execution, and inline image return.

**Data flow**: It receives a tool context and validated JavaScript input. It finds sandbox paths, builds candidate source from saved state plus new code, writes a temporary `.mjs` file with the image prelude, links global Node modules, runs Node, records metrics, and then either saves the new state on success or leaves state unchanged on failure or timeout. It returns a structured tool result with text and possible images.

**Call relations**: This is the JavaScript tool handler registered by `manifest`. It coordinates the helper functions: `_candidate_source` prepares code, `global_modules_link` prepares imports, `js_emit_prelude` enables images, `run_task` executes Node, `_meter_run` records the run, `_expired_result` covers timeouts, `_redeclared_from_state` improves error advice, `_emitted_images` collects images, and `_repl_result` formats the final answer.

*Call graph*: calls 9 internal fn (_candidate_source, _emitted_images, _expired_result, _meter_run, _redeclared_from_state, _repl_result, global_modules_link, js_emit_prelude, js_emit_relative); 3 external calls (shell_path, run_task, uuid4).


##### `xlsx_repl`  (lines 430–448)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs a stateful Python snippet for Excel work in the sandbox. It preserves successful Python code between calls and prints the variable `result` as JSON when the user sets it.

**Data flow**: It receives a tool context and validated Python input. It builds source from saved state plus new code, appends a small footer that prints `result` if present, writes a temporary Python file, runs it with `python3`, records metrics, and commits the new state only if the run exits successfully. It returns output, errors, and exit status, or timeout information if the run outlasts the wait budget.

**Call relations**: This is the Python Excel tool handler registered by `manifest`. It uses `_candidate_source` for persistence, `run_task` for sandbox execution, `_meter_run` for observability, `_expired_result` for long-running calls, and `_repl_result` for completed runs.

*Call graph*: calls 4 internal fn (_candidate_source, _expired_result, _meter_run, _repl_result); 2 external calls (shell_path, run_task).


##### `manifest`  (lines 451–471)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, available tools, skill packs, and sandbox internet setting. Without this, the REPL tools would not be advertised or callable.

**Data flow**: It takes no input. It constructs tool definitions for the JavaScript and Excel REPLs, constructs skill specifications from the known skill directories, and returns a `Manifest` object.

**Call relations**: The extension loader calls this when discovering the package. It hands the host system the `js_repl` and `xlsx_repl` handlers plus the skill locations so they can be loaded on demand.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Collaboration and task tracking
These tools connect conversational work to Slack discovery and persistent per-conversation todo checklists.

### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and Slack-related tool calls`

This file lets someone set up Slack by talking to the agent instead of hand-editing hidden system settings. It supports two setup paths. In the one-click path, an administrator gets an “Add to Slack” link, and Slack later sends back the token and workspace identity. In the bring-your-own-app path, the agent prints a ready-made Slack app manifest, the user creates that app in Slack, and the app secrets are collected privately through credential storage rather than through chat.

The main setup action, `slack_connect_handler`, acts like a checklist. It asks: do we already have a bot token? Do we know which Slack workspace and bot this is? Is this Slack workspace already tied to another UFO workspace? Has Slack successfully reached this server with a verified request? Based on those answers it reports a clear state such as `not_configured`, `not_installed`, `pending`, or `connected`.

The file also includes `slack_manifest_handler`, which prints the exact Slack app configuration needed for this deployment, including permissions and callback URLs. Finally, `slack_channels_handler` uses the stored Slack bot token to search channels and direct messages so the agent can find a conversation by name or people, not just by Slack’s internal IDs.

#### Function details

##### `_events_url`  (lines 146–147)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should call when it sends events to this deployment. This keeps the Slack callback URL in one consistent shape.

**Data flow**: It takes the deployment’s public base URL, removes any trailing slash, then adds `/surface/slack`. The result is a single URL string that can be shown in a manifest or used while reporting setup status.

**Call relations**: The connect flow uses this when it reports where Slack should reach the server. The manifest flow uses it to fill in the Slack app’s event and interactivity request URLs.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 150–152)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a Slack setup status into the standard tool response format. It turns internal setup progress into a simple JSON message the agent can show or reason about.

**Data flow**: It receives a state name, a human-readable hint, the Slack events URL if known, and any extra fields such as a team ID or authorization link. It builds a dictionary, converts it to JSON text, and returns it inside a tool result.

**Call relations**: The Slack connection helpers call this whenever they need to explain the current install state. It is the common exit path for messages like “not configured,” “pending,” or “connected.”

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 155–196)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the Slack installation checklist from a chat action. A user can call it repeatedly before, during, and after setup, and it will say what still needs to happen.

**Data flow**: It reads the public base URL, tries to read the stored Slack bot token, and uses that token to look up the saved Slack identity if possible. If no identity exists, it either creates an OAuth install link or tries the manifest-based identity check. Once identity is known, it binds that Slack team to this UFO workspace, checks whether Slack has already reached the server with a verified request, and returns a JSON status result.

**Call relations**: This is the main handler for the `slack_connect` tool. It delegates one-click installation to `_oauth_link`, manifest installation to `_derive_manifest_identity`, URL creation to `_events_url`, status formatting to `_state`, identity reading to Slack surface helpers, and final reachability checking to `_verified`.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 199–229)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates an “Add to Slack” link for the one-click installation path. It is used when this deployment has its own Slack app configured.

**Data flow**: It first checks whether the needed Slack app client ID and secret exist in environment variables. If not, it returns a message telling the user to use the manifest path. If they do exist, it checks that the speaker is an administrator, confirms there is a public base URL, seals a short-lived credential handoff, builds Slack’s authorization URL, and returns that link in a setup status response.

**Call relations**: The main connect handler calls this when Slack is not installed yet and the requested method is OAuth. It uses the tool context to confirm admin rights and start credential authorization, then uses Slack surface helpers to build the final Slack install URL.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 232–266)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-Slack-app setup path after the user has supplied the app secrets privately. It proves that the bot token works and records which Slack workspace and bot it belongs to.

**Data flow**: It checks whether both required credential slots are filled: the bot token and signing secret. If either is missing, it returns a `not_configured` message explaining what to collect. If both exist, it reads the bot token, requires an administrator, calls Slack identity resolution using that token, and either returns the resolved identity or a helpful error message if Slack rejects the token.

**Call relations**: The main connect handler calls this when the user chose the manifest method and no Slack identity is saved yet. It uses `_state` for user-facing setup messages and `_token_diagnosis` to turn Slack token errors into clearer instructions.

*Call graph*: calls 3 internal fn (require_speaking_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 269–293)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has successfully contacted this deployment using the currently trusted signing secret. This is what turns setup from “identity proven” into “connected.”

**Data flow**: It reads the current signing-secret fingerprint, then looks for a stored marker saying Slack previously reached this server. It parses that marker, compares its fingerprint with the current one, and returns `false` if anything is missing, malformed, or outdated. If the marker matches, it mirrors that verified status into the wider workspace store and returns `true`.

**Call relations**: The main connect handler calls this after Slack identity is known. It relies on Slack surface helpers to compute the current verification fingerprint and to mirror the verified marker so other parts of the system can see the same connection proof.

*Call graph*: called by 1 (slack_connect_handler); 3 external calls (loads, mirror_url_verified, verifying_fingerprint).


##### `slack_manifest_handler`  (lines 296–311)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces a ready-to-paste Slack app manifest for the manifest setup path. This saves the user from manually choosing permissions, event subscriptions, and callback URLs in Slack.

**Data flow**: It receives a requested bot display name, checks that the name is simple and within Slack’s expected length, then reads the deployment’s public base URL. It builds the Slack event and interactivity URLs, fills them into the manifest template, and returns the manifest as plain text in a tool result.

**Call relations**: This is the handler for the `slack_app_manifest` tool. It calls `_events_url` so the manifest points Slack back to the same endpoint used by the rest of the Slack surface.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 314–337)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for channels and direct messages. It helps the agent find where to read or post when the user gives a name or person instead of a Slack conversation ID.

**Data flow**: It reads the stored Slack bot token, then reads the saved Slack identity so it knows which bot user it is acting as. It runs a Slack conversation search using the query text, converts the found conversations to JSON, includes whether the result was truncated, and returns the data as untrusted content because it comes from Slack users and workspace text.

**Call relations**: This is the handler for the `slack_channels` tool. It depends on Slack already being connected through `slack_connect_handler`, uses `read_identity` to confirm setup is complete, and hands the actual Slack paging and matching work to `SlackConversationSearch`.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 340–346)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns Slack token error codes into clearer setup advice. It helps users understand whether they should re-copy the bot token or look at a more general Slack authentication failure.

**Data flow**: It receives a Slack error string. If the error is one of the known token rejection cases, it returns a message telling the user to collect the Bot User OAuth Token again. Otherwise, it returns a generic `auth.test` failure message with the error included.

**Call relations**: The manifest identity setup helper calls this when Slack rejects or cannot verify the supplied bot token. Its output is placed into the setup state message returned to the user.

*Call graph*: called by 1 (_derive_manifest_identity).


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling and conversation display`

This file gives the system a durable task board, like a small checklist pinned to one conversation. Without it, the assistant could still say what it plans to do, but there would be no structured, saved record of the steps, no reliable way to mark progress, and no clean data for the interface to display.

The file defines two user-facing tools. One tool, `update_todo_list`, creates or replaces the whole checklist. The other, `update_todo_status`, changes the status of one or more existing tasks, such as from `pending` to `in_progress` or `completed`. The checklist is stored in the extension's scoped store, which is a durable storage area owned by this extension. Each board is keyed by conversation ID, so separate conversations do not overwrite each other.

The file also defines the shapes of the data using Pydantic models, which are validation classes that check incoming data has the expected fields and types. It returns the current board after every change, so the assistant immediately sees the updated state.

Finally, it registers the extension through `manifest()`. That manifest tells the host system which tools exist, what prompt guidance to include, and how to expose a conversation slot called `tasks` so the UI can summarize and read the current checklist.

#### Function details

##### `_require_ext`  (lines 87–90)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has access to the todos extension context. That context is needed because it contains the extension's private store where checklists are saved.

**Data flow**: It receives the current tool context. If the extension context is present, it returns it. If it is missing, it stops the operation with an error, because the todo tools cannot read or write their saved checklist without it.

**Call relations**: Both `update_todo_list` and `update_todo_status` call this at the start of their work. It acts like checking that you have the right key before trying to open the storage cabinet.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 93–94)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This helper builds the storage key used to save or find the todo board for one conversation. It keeps each conversation's checklist separate.

**Data flow**: It receives a conversation ID. It combines that ID with the `todo/` prefix and returns the resulting text key, which is then used to read from or write to the extension store.

**Call relations**: `update_todo_list`, `update_todo_status`, `_summarize_tasks`, and `_read_tasks` all use this when they need to access the board for the current conversation. It is the shared address-making step for every todo-board lookup.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 97–98)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns a todo board into the standard result format returned by a tool. It lets the assistant see the full current checklist after creating or updating it.

**Data flow**: It receives a `TodoBoard`. It converts the board into JSON text, wraps that text in a `TextContent` object, and then wraps that content in a `ToolResult`. The returned value is ready for the tool system to send back to the model.

**Call relations**: `update_todo_list` and `update_todo_status` call this after they have saved the latest board. It is the final packaging step before the updated checklist is returned.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 101–103)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store and turns it back into a validated `TodoBoard` object. It also cleanly reports when no board exists yet.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved value at that key. If nothing is found, it returns `None`; otherwise, it validates the stored data as a `TodoBoard` and returns that board.

**Call relations**: `update_todo_status` uses this before applying status changes, because it needs the existing list. `_summarize_tasks` and `_read_tasks` also use it when the host system asks what task information should be shown for the conversation.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 106–110)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates a new checklist or replaces the current one. It is meant to be used at the start of complex work so progress can be tracked visibly.

**Data flow**: It receives the tool context and a title plus a complete list of tasks. It checks that the extension context exists, builds a `TodoBoard`, saves that board under the current conversation's key, and returns the saved board as JSON text in a tool result.

**Call relations**: This is one of the extension's main public tools, registered in `manifest()`. It calls `_require_ext` to get storage access, `_board_key` to choose where to save the board, and `_board_result` to return the finished checklist.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 113–124)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of tasks already on the checklist. It lets the assistant mark work as started or finished without rewriting the whole list.

**Data flow**: It receives the tool context and one or more status updates. It checks for the extension context, reads the current board for the conversation, rejects the request if no list exists, checks that each 1-based task number points to a real task, changes the requested statuses, saves the board again, and returns the updated board.

**Call relations**: This is the second main public tool registered in `manifest()`. It depends on `_read_board` to fetch the current checklist, `_board_key` to locate it, `_require_ext` for extension storage access, and `_board_result` to send the updated state back.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 127–129)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick summary of the conversation's task list by returning how many tasks exist. If there is no checklist, it returns no summary.

**Data flow**: It receives a conversation slot context, which includes the extension context and conversation ID. It builds the board key, reads the saved board, and returns `None` if no board exists or the number of tasks if one does.

**Call relations**: The `TASKS_SLOT` provider uses this when the host wants a lightweight summary of the tasks slot. It relies on `_board_key` and `_read_board` to find the same saved board used by the todo tools.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 132–162)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This function prepares the saved checklist for display in the conversation UI. It returns a safe, size-limited task payload with counts and truncation information.

**Data flow**: It receives a conversation slot context. It reads the board for that conversation. If none exists, it returns an empty `TasksSlotPayload`. If a board exists, it copies over the title and tasks, trims them to configured length and count limits, counts completed tasks, marks whether anything was shortened, and returns the display-ready payload.

**Call relations**: The `TASKS_SLOT` provider calls this when the host needs the actual task data to show. It uses `_board_key` and `_read_board` to fetch the stored checklist, then creates `ConversationTask` and `TasksSlotPayload` objects for the UI-facing view.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 175–197)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what the todos extension provides. It registers the two todo tools, the prompt guidance, and the conversation task display slot.

**Data flow**: It takes no input. It builds and returns a `Manifest` containing the extension name and version, the tool definitions for creating and updating todo lists, the prompt section text, and the tasks conversation slot.

**Call relations**: The host system calls this when loading the extension. Through this manifest, `update_todo_list` and `update_todo_status` become available as tools, and the task slot backed by `_summarize_tasks` and `_read_tasks` becomes available for conversation display.

*Call graph*: 3 external calls (__init__, __init__, __init__).
