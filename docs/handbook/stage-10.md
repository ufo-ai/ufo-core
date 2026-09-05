# Tool workbench and sandboxed execution  `stage-10`

This stage is the system’s safe workbench. It is shared support used whenever an agent needs to do real work, such as running a command, editing a file, using a browser, processing a document, or asking for human help. The workbench is like a supervised workshop: tools are available, but each action must go through approved doors.

The sandbox and workspace part provides the private project folder and command runner. It can use local machines, Docker containers, cloud sandboxes, or a user’s terminal, while keeping files, logs, and long-running tasks tied to the right conversation. Browser automation adds a controlled browser for page reading, clicking, typing, downloads, and screenshots. Document and office scripts handle PDFs, Word, PowerPoint, and spreadsheets by unpacking, annotating, repairing, rendering, or recalculating them.

builtins.py defines the main tool set agents can call, such as shell, file, sharing, questions, skills, delegation, and secrets requests. context.py gives each tool its allowed workspace and result format. bridge.py and tool_bridge.py let live sandbox code request approved tools safely and durably. notify_tool.py adds guarded user notifications.

## Sub-stages

- [Sandbox carriers, workspaces, terminals, and command tasks](stage-10.1.md) `stage-10.1` — 12 files
- [Browser automation backends](stage-10.2.md) `stage-10.2` — 23 files
- [Document and office automation scripts](stage-10.3.md) `stage-10.3` — 19 files

## Files in this stage

### Built-in tool surface
Defines the primary safe actions agents can request inside a workspace.

### `core/src/ufo/host/tools/builtins.py`

`domain_logic` · `tool execution during an agent turn`

This file is the toolbox that every agent starts with. It turns common actions into carefully bounded tool calls, so the agent can work without bypassing the project’s safety rules. For example, file reads, searches, writes, and edits all go through the sandbox, which is the protected workspace where the agent is allowed to operate. That is like letting someone use tools only inside a workshop, rather than handing them keys to the whole building.

A major theme is controlled movement of data. Reading a file returns limited, structured content. Writing or editing is only allowed after the file has been read in the current turn, which helps prevent blind overwrites. Sharing a file is even stricter: the file is measured, uploaded or copied to the artifact store, recorded in the database, and returned as a temporary download link. This is the intended doorway from the sandbox back to the outside world.

The file also defines conversational tools. An agent can ask the user a question, request private credentials, start an account-connection flow, or spawn a child agent for a subtask. These tools keep sensitive or long-running work out of ordinary chat when needed, while still giving the agent a clear result to act on.

#### Function details

##### `_bounded_file_path`  (lines 186–189)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the small result envelope used by file-change tools. This prevents a path string from being so large that it breaks later tool output.

**Data flow**: It receives a path string, measures how large that path becomes when written as JSON text, and either returns the same path unchanged or raises an error if it is too large.

**Call relations**: This is used as a validation step for file paths in write and edit inputs. It runs before the actual tool handler so bad paths are refused early.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 370–401)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandbox and reports whether it finished, failed, timed out, or moved to the background. It gives agents a controlled way to run build commands, scripts, and other terminal work.

**Data flow**: It receives the tool context and a command request. If the request asks for background mode, it hands off to `_bash_background`. Otherwise it refuses suspicious plain sleep commands, starts the command through the task system, waits for the requested foreground time, and returns command output, an error message, or background task handles.

**Call relations**: The tool registry calls this when the agent uses the `bash` tool. It relies on the shared task helpers to start the command, format timeout notices, and produce handles that can be used to watch a still-running task.

*Call graph*: calls 1 internal fn (_bash_background); 7 external calls (__init__, __init__, format, flat_sleeps, run_task, task_handles, timeout_notice).


##### `_bash_background`  (lines 404–418)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command and immediately detaches it, returning the identifiers needed to find its logs and completion files later. It is used when the agent does not want to wait for the command.

**Data flow**: It creates a task id and task directory paths, asks the sandbox to launch the command detached, and checks that a process id came back. The result is either an error explaining that detaching failed or a text block containing task handles.

**Call relations**: Only `bash_handler` calls this, when the `bash` tool input sets background mode. It uses the same task-handle formatting as foreground commands that outlive their wait time.

*Call graph*: called by 1 (bash_handler); 4 external calls (__init__, __init__, task_handles, task_id).


##### `_require_str`  (lines 421–424)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Makes sure a value from a sandbox file-read result is a non-empty string. It protects later code from treating missing or malformed data as valid media content.

**Data flow**: It receives an unknown value and the name of the field being checked. If the value is a real non-empty string, it returns it; otherwise it raises an error naming the missing field.

**Call relations**: `read_handler` and `_document_result` call this when turning image or document data from the sandbox into tool output. It acts as a small guardrail around external structured results.

*Call graph*: called by 2 (_document_result, read_handler).


##### `_document_result`  (lines 427–470)

```
def _document_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a document-read response, such as a PDF, slide deck, Word file, or spreadsheet, into text and image blocks the agent can understand. It adds helpful page or slide notes so the agent knows whether more content remains.

**Data flow**: It receives a dictionary returned by the sandbox file reader. It extracts text, page counts, notes, and rendered page images, validates required media fields, and returns a `ToolResult` containing text blocks and image blocks.

**Call relations**: `read_handler` calls this when the sandbox says the file is a supported document type. It uses `_require_str` to validate embedded image data before packaging it for the agent.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 473–510)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox and returns the right kind of content for that file: text, an image, or document pages. It also records that this path has been seen, which later allows safe edits or overwrites.

**Data flow**: It receives a file path plus optional offset and limit values. It asks the sandbox `ufo fs` tool to read the file, records the path in `ctx.read_paths`, then converts the response into text, image content, or a document result with page information.

**Call relations**: The tool registry calls this for the `read` tool. It hands document responses to `_document_result` and uses `_require_str` for image fields, while its recorded path becomes important to `write_handler` and `edit_handler`.

*Call graph*: calls 2 internal fn (_document_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 513–535)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Writes text to a workspace file while enforcing the rule that existing files must be read before being overwritten. This helps stop accidental destruction of unseen content.

**Data flow**: It receives a target file path and text content. It writes the content to a temporary staged file inside the sandbox, asks `ufo fs` to move that staged content into place, adds size and line-count details, formats the result, and records the path as read afterward.

**Call relations**: The tool registry calls this for the `write` tool. It uses `_file_tool_result` to keep the returned confirmation small and structured.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (uuid4).


##### `edit_handler`  (lines 538–551)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact string replacements to a file that the current turn has already read. It is designed to make edits deliberate rather than blind.

**Data flow**: It receives a file path and one or more edit instructions. It first checks that the file path is in `ctx.read_paths`, encodes the old and new strings safely, sends the edit request to `ufo fs`, and returns a bounded summary of what changed.

**Call relations**: The tool registry calls this for the `edit` tool. It relies on the read-before-edit record created by `read_handler`, and sends its final response through `_file_tool_result`.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 554–568)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats the result of a file write or edit so it stays within the tool-output size limit. It reports what happened without dumping large file contents or oversized snippets back into the conversation.

**Data flow**: It receives a result dictionary, turns it into compact JSON, and returns it if it fits. If it is too large, it removes bulky snippet data and simplifies the message before trying again; if it still cannot fit, it raises an error.

**Call relations**: `write_handler` and `edit_handler` both call this after the sandbox file operation finishes. It is the shared final packaging step for file-changing tools.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 571–577)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files in the sandbox whose paths match a glob pattern, which is a wildcard pattern like `**/*.py`. It gives agents a safer alternative to shelling out with `find` or `ls`.

**Data flow**: It receives a pattern and an optional search directory. It defaults the directory to the workspace root, asks the sandbox `ufo fs` matcher to find paths, and returns the matches as JSON text.

**Call relations**: The tool registry calls this for the `glob` tool. It does the search inside the sandbox and only returns the bounded list of matching paths.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 580–598)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents inside the sandbox for a regular expression, which is a search pattern that can match flexible text. It lets agents inspect code or documents without running raw shell search commands.

**Data flow**: It receives a search pattern plus optional directory, file filter, context size, case setting, output mode, and result limit. It builds a parameter object, sends it to the sandbox `ufo fs grep` command, and returns the bounded search result as JSON text.

**Call relations**: The tool registry calls this for the `grep` tool. The actual scanning happens in the sandbox, keeping traversal and file access under the same rules as other file tools.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 601–635)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies a prepared file from the sandbox into the artifact store, which is where files live when they are shared outside the workspace. It supports both S3 storage and local filesystem storage.

**Data flow**: It receives the sandbox path, destination blob key, file size, and SHA-256 digest. For S3, it creates a tightly limited upload URL and has the sandbox upload directly to it; for filesystem storage, it streams the file through the blob store. It returns nothing unless something fails.

**Call relations**: `_staged_share` calls this after measuring a file. It is the actual transfer step in the `share_file` pipeline.

*Call graph*: called by 1 (_staged_share); 3 external calls (b64encode, quote, shell_path).


##### `_discard_artifact`  (lines 648–652)

```
async def _discard_artifact(ctx: ToolContext, key: str) -> None
```

**Purpose**: Best-effort cleanup for an artifact blob that was created during sharing but should not remain. It prevents failed share attempts from leaving stray files behind when possible.

**Data flow**: It receives a blob key, asks the blob store to delete it, and logs a cleanup failure if deletion itself fails. It does not raise cleanup errors back to the main flow.

**Call relations**: `_shared_preview`, `_staged_share`, and `share_file_handler` call this when upload, preview, or database recording fails. It is the cleanup broom for the sharing workflow.

*Call graph*: called by 3 (_shared_preview, _staged_share, share_file_handler); 1 external calls (log).


##### `_shared_preview`  (lines 655–720)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str, artifact_id: UUID, recorded: bool) -> ArtifactPreview | None
```

**Purpose**: Creates a small preview image for shareable document types, so the recipient can see a visual preview rather than only a filename. If previewing fails, the file share still succeeds.

**Data flow**: It receives the stored file path, safe display name, artifact id, and a flag saying whether the artifact was already recorded. If the file type supports previews and the blob store can accept one, it asks the preview service to render the first page and upload a PNG, then returns preview metadata. On failure, it logs the reason and returns no preview.

**Call relations**: `_staged_share` calls this after the main file has been stored. It may call `_discard_artifact` if a newly created preview blob needs to be removed after a failed render.

*Call graph*: calls 1 internal fn (_discard_artifact); called by 1 (_staged_share); 7 external calls (__init__, dumps, loads, PurePosixPath, quote, log, shell_path).


##### `_packed_directory`  (lines 740–769)

```
async def _packed_directory(ctx: ToolContext, scoped: str) -> str | None
```

**Purpose**: Turns a directory selected for sharing into a `.tar.gz` archive inside the sandbox. This lets users share folders without manually creating an archive first.

**Data flow**: It receives a sandbox path. It checks whether that path is a real directory, returns `None` if it is not, otherwise creates a tool-output location, runs `tar` in the sandbox to pack the directory, and returns the archive path.

**Call relations**: `_staged_share` calls this before measuring and uploading a share target. If it returns an archive path, the rest of the sharing pipeline treats that archive as the file to share.

*Call graph*: called by 1 (_staged_share); 3 external calls (quote, shell_path, uuid4).


##### `_share_request_fingerprint`  (lines 772–774)

```
def _share_request_fingerprint(spec: SharedFileSpec) -> str
```

**Purpose**: Creates a stable fingerprint for one share request. This is used to recognize repeated calls and make sure an idempotency key is not reused for a different file request.

**Data flow**: It receives a shared-file specification, serializes its fields in a stable JSON order, hashes that text with SHA-256, and returns the digest string.

**Call relations**: `_recorded_share` uses this to verify an existing share record matches the current request. `_staged_share` uses it when creating a new staged-share record.

*Call graph*: called by 2 (_recorded_share, _staged_share); 3 external calls (model_dump, sha256, dumps).


##### `_recorded_share`  (lines 777–829)

```
async def _recorded_share(ctx: ToolContext, spec: SharedFileSpec, artifact_id: UUID) -> _StagedShare | None
```

**Purpose**: Looks for an artifact that was already shared for this turn and idempotency-derived artifact id. This avoids uploading the same file again when a tool call is retried.

**Data flow**: It receives the context, requested file spec, and artifact id. It queries the workspace database for a matching shared artifact row, checks that its stored fingerprint matches the current request, reconstructs preview metadata if present, and returns a `_StagedShare` object or `None`.

**Call relations**: `_staged_share` calls this first. If it finds a valid existing record, the upload and preview steps are skipped.

*Call graph*: calls 1 internal fn (_share_request_fingerprint); called by 1 (_staged_share); 4 external calls (__init__, __init__, select, workspace_tx).


##### `_staged_share`  (lines 832–886)

```
async def _staged_share(ctx: ToolContext, spec: SharedFileSpec, artifact_id: UUID) -> _StagedShare
```

**Purpose**: Prepares one file for sharing by normalizing its name, packing directories if needed, measuring it, storing it, and optionally creating a preview. It is the main per-file workhorse behind `share_file`.

**Data flow**: It receives a share specification and artifact id. It first checks for an existing recorded share, confines the requested path to the workspace, packs directories, runs a preflight command to measure size, digest, and text-likeness, chooses a safe filename, stores the file, creates a preview if possible, and returns a `_StagedShare` summary.

**Call relations**: `share_file_handler` calls this once for each requested file. It coordinates `_recorded_share`, `_packed_directory`, `_store_artifact`, `_shared_preview`, `_share_request_fingerprint`, and `_discard_artifact`.

*Call graph*: calls 6 internal fn (_discard_artifact, _packed_directory, _recorded_share, _share_request_fingerprint, _shared_preview, _store_artifact); called by 1 (share_file_handler); 6 external calls (__init__, loads, guess_type, PurePosixPath, shell_path, workspace_path).


##### `share_file_handler`  (lines 889–993)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares one or more workspace files with the user by storing them as artifacts, recording them in the database, and returning temporary download links. This is the official path for moving produced files out of the sandbox.

**Data flow**: It receives a list of file specs. It creates or reuses artifact ids, stages every file, writes shared-artifact rows in one database transaction, publishes artifacts if a publisher is available, mints expiring URLs, and returns a JSON list containing each file’s link and metadata. If something fails after blobs were created, it attempts to delete them.

**Call relations**: The tool registry calls this for the `share_file` tool. It calls `_staged_share` for each file and `_discard_artifact` during error cleanup.

*Call graph*: calls 2 internal fn (_discard_artifact, _staged_share); 15 external calls (__init__, __init__, gather, publish_artifacts, now, timedelta, dumps, select, workspace_tx, artifact_object_names (+5 more)).


##### `_spawn_handles`  (lines 1008–1014)

```
def _spawn_handles(target: str, turn_id: UUID, moved: bool) -> str
```

**Purpose**: Builds the message that tells the agent how to refer to a child agent that is still running in the background. It keeps background-spawn wording consistent.

**Data flow**: It receives the target name, child turn id, and whether the spawn was moved to the background because a new message arrived. It chooses the right lead text, builds a small JSON payload, and returns one combined text message.

**Call relations**: `spawn_handler` calls this when a child run does not yet have a final output. It plays the same role for spawned agents that task handles play for background shell commands.

*Call graph*: called by 1 (spawn_handler); 1 external calls (dumps).


##### `spawn_handler`  (lines 1017–1066)

```
async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult
```

**Purpose**: Starts a child agent or subagent profile to do a delegated subtask. It can wait for validated output, return a question from the child, or leave the child running in the background.

**Data flow**: It receives the target name, payload, background choice, optional display name, and optional model override. It asks `ctx.spawn` to run the child, converts known rejection errors into tool errors, and returns either the child’s question, background handles, or validated JSON output.

**Call relations**: The tool registry calls this for the `spawn` tool. It calls `_spawn_handles` when the child continues after the current tool call returns.

*Call graph*: calls 1 internal fn (_spawn_handles); 4 external calls (__init__, __init__, spawn, dumps).


##### `ask_user_handler`  (lines 1074–1085)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Creates a structured question for the agent to ask the user in its next reply. It keeps user questions inside the normal chat flow instead of opening a separate prompt.

**Data flow**: It receives a title, optional icon, and question records. It builds a JSON payload saying the agent is awaiting a question answer, prefixes it with instructions to ask and end the turn, and returns that text as the tool result.

**Call relations**: The tool registry calls this for the `ask_user` tool. A chat surface can render the structured payload, while the agent’s reply carries the human-readable question.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 1088–1100)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill and its dependencies into the sandbox, then returns the instructions and file tree the agent needs to use that skill. A skill is a bundle of workflow guidance and supporting files.

**Data flow**: It receives a skill name. It resolves the dependency closure through the skill context, materializes the files, installs them into the sandbox, builds the text context for the agent, and returns it.

**Call relations**: The tool registry calls this for the `load_skill` tool. It hands off file installation to `load_skills` and text assembly to `loaded_context`.

*Call graph*: 4 external calls (__init__, __init__, load_skills, loaded_context).


##### `_grantee_agent_id`  (lines 1109–1133)

```
async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None
```

**Purpose**: Figures out which agent should receive access to a newly connected account when the caller names another agent. It enforces that only the workspace main agent can grant a connection on someone else’s behalf.

**Data flow**: It receives the tool context and an agent name. If the name is empty, it returns `None`, meaning the calling agent is the grantee. Otherwise it loads active agents for the workspace, checks the caller is the main agent, finds the named target, and returns that target’s id unless it is the caller itself.

**Call relations**: `connect_account_handler` calls this before creating the connection request. It resolves authority up front so the later private handoff knows exactly which agent the grant is for.

*Call graph*: called by 1 (connect_account_handler); 2 external calls (select, workspace_tx).


##### `connect_account_handler`  (lines 1136–1150)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Creates a private account-connection request for an external provider, such as GitHub or Google, without exposing the authorization URL in chat. It is used when a member asks to connect an account.

**Data flow**: It receives provider, sharing, and optional target-agent information. It verifies there is a speaking member, resolves any grantee agent, validates that the provider exists, builds a `ConnectRequest`, and returns instructions plus the request JSON.

**Call relations**: The tool registry calls this for the `connect_account` tool. It depends on `_grantee_agent_id` for cross-agent grants and on the installed connection flow to validate the provider.

*Call graph*: calls 1 internal fn (_grantee_agent_id); 5 external calls (__init__, __init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 1160–1183)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks an admin member to fill secret credential slots through a private prompt instead of typing secrets into chat. This protects API keys and similar values from appearing in the transcript.

**Data flow**: It receives a reason and credential prompts. It checks that there is a speaking member, that credential storage is configured, and that the speaker is an admin. It then seals the requested slots, builds a `CredentialRequest`, and returns instructions plus the structured request.

**Call relations**: The tool registry calls this through the `request_credentials` bound action. It calls the context’s admin check before producing a private credential request.

*Call graph*: calls 1 internal fn (speaker_is_admin); 4 external calls (__init__, __init__, __init__, __init__).


##### `cancel_spawn_handler`  (lines 1186–1198)

```
async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult
```

**Purpose**: Cancels a child agent run that this turn spawned and reports its resulting status. If the child already finished, cancellation does not undo the finished result.

**Data flow**: It receives a spawn id as text. It checks that spawn control is available, converts the id to a UUID, asks the subagent controller to cancel it, and returns a JSON status record.

**Call relations**: The tool registry calls this for the `cancel_spawn` tool. It works through `ctx.subagents`, the same subsystem used by `spawn_handler` for child runs.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_spawn_handler`  (lines 1201–1219)

```
async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a running or waiting child agent. This is how the parent can answer a child’s question or give extra instructions without starting over.

**Data flow**: It receives a spawn id and message text. It checks that spawn control and an idempotency key are available, converts the spawn id to a UUID, queues the message through the subagent controller, and returns a JSON status record.

**Call relations**: The tool registry calls this for the `message_spawn` tool. It uses `ctx.subagents`, matching the child-agent workflow behind `spawn_handler`, and marks the follow-up so its result will return to the conversation.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### Sandbox bridge orchestration
Turns sandbox-originated tool requests into durable, tracked calls through the main UFO runtime.

### `core/src/ufo/runtime/tool_bridge.py`

`orchestration` · `request handling`

A sandbox is an isolated place where agent code can run, but it should not directly grab every tool or permission in the system. This file is the bridge between that sandbox and the normal turn loop. Think of it like a service desk: the sandbox submits a request, the desk checks whether the parent turn is still active and whether the tool is allowed, then either answers directly or opens a tracked work ticket.

The main class, ToolBridge, supports three kinds of requests. It can list the tools visible to the current agent, return the input schema for one tool, or actually call a tool. For a real tool call, it creates a new conversation and turn in the database using stable IDs, so repeating the same request does not accidentally create duplicate work. It then asks DBOS, the durable workflow system, to run that turn, and waits for the turn's final result through a tailer that streams turn updates.

The important safety rule is that this bridge only helps with discovery and dispatch. It does not permanently grant authority. The newly admitted turn still re-checks the named action under its own rules before doing the work.

#### Function details

##### `ToolBridge.request`  (lines 66–100)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is the main doorway into the bridge. It receives a sandbox tool-bridge request, checks that the parent turn is alive, checks which tools are allowed, and either returns information or dispatches a real tool call.

**Data flow**: It takes a RunToken, which identifies the live sandbox run, and a ToolBridgeRequest, which says whether to list tools, get a schema, or call a tool. It reads the parent turn from the database, filters tools through the permission checks, and for a call creates a child turn, enqueues it, waits for its result, and returns either ToolBridgeSuccess or ToolBridgeFailure.

**Call relations**: This function coordinates the whole file. It first asks _parent for the current turn, uses _allowed to decide what the sandbox may see or call, uses _admit to record a real tool-call turn, uses _enqueue to start that turn in the workflow system, and finally uses _terminal to wait for the completed answer.

*Call graph*: calls 5 internal fn (_admit, _allowed, _enqueue, _parent, _terminal); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `ToolBridge._parent`  (lines 102–131)

```
async def _parent(self, run: RunToken) -> sa.Row[tuple[object, ...]] | None
```

**Purpose**: This looks up the sandbox run's parent turn and confirms that it is still running. Without this check, a sandbox could try to launch work after its original turn had already ended.

**Data flow**: It takes a RunToken containing workspace and turn IDs. It opens a workspace database transaction, joins the turn, agent, and conversation records, and returns the matching running parent row with the details needed for permissions and child-turn creation. If no running parent exists, it returns nothing.

**Call relations**: ToolBridge.request calls this before doing anything else. The returned parent row becomes the source of truth for later permission checks in _allowed and for copying conversation and agent context in _admit.

*Call graph*: called by 1 (request); 2 external calls (select, workspace_tx).


##### `ToolBridge._allowed`  (lines 133–145)

```
def _allowed(self, parent: sa.Row[tuple[object, ...]], tool: ToolDef) -> bool
```

**Purpose**: This decides whether one specific tool should be visible or callable from the current parent turn. It protects tools from being exposed to agents or subagents that were not granted them.

**Data flow**: It takes the parent turn's database row and a ToolDef describing a tool. It checks special action tools first, then ordinary agent tools, then subagent profile rules, including implied grants, which are extra tool permissions that come along with another permission. It returns true if the tool is allowed and false otherwise.

**Call relations**: ToolBridge.request uses this when listing tools, when checking a named tool before returning its schema, and before dispatching a tool call. For action-related tools, it delegates to _any_action_granted because those tools depend on whether any bound object action is available.

*Call graph*: calls 1 internal fn (_any_action_granted); called by 1 (request); 1 external calls (with_implied_grants).


##### `ToolBridge._any_action_granted`  (lines 147–163)

```
def _any_action_granted(self, parent: sa.Row[tuple[object, ...]]) -> bool
```

**Purpose**: This answers a narrower question: does the parent agent or subagent have access to at least one object action? That matters because some bridge tools are only useful if there is at least one action they can read or invoke.

**Data flow**: It reads the bridge's registered bound actions and the parent turn's tool or subagent permissions. It expands permissions with implied grants, compares them against each action's canonical ID and default subagent availability, and returns true if any action is available.

**Call relations**: _allowed calls this for the object-action tool and related read-only action tools. It acts as the action-specific permission helper underneath the broader tool-visibility decision.

*Call graph*: called by 1 (_allowed); 1 external calls (with_implied_grants).


##### `ToolBridge._admit`  (lines 165–261)

```
async def _admit(self, run: RunToken, parent: sa.Row[tuple[object, ...]], request: ToolBridgeRequest) -> tuple[UUID, UUID] | None
```

**Purpose**: This records a requested tool call as a real queued turn in the database. It is the durability step: the tool call becomes something the normal turn system can run, retry, observe, and audit.

**Data flow**: It takes the sandbox run, the parent turn row, and the bridge request. It builds stable conversation and turn IDs from the workspace, parent turn, and request ID; packages the tool name and input as a ToolBridgeIntent; locks and rechecks that the parent turn is still running; inserts the child conversation and turn if they do not already exist; verifies that a repeated request ID means the same call; and marks the turn as ready to be enqueued. It returns the new turn and conversation IDs, or nothing if the parent stopped running.

**Call relations**: ToolBridge.request calls this only after the requested tool has passed permission checks. After _admit succeeds, request hands the returned IDs to _enqueue so the workflow system can actually run the recorded turn.

*Call graph*: called by 1 (request); 9 external calls (__init__, TypeAdapter, select, update, workspace_tx, current_traceparent, authority_member_id, turn_id_for, uuid5).


##### `ToolBridge._enqueue`  (lines 263–291)

```
async def _enqueue(self, workspace_id: UUID, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This asks DBOS, the durable workflow runner, to execute the queued tool-call turn. It also cleans up the database marker if enqueueing is interrupted or fails, so the system does not falsely believe the turn was successfully submitted.

**Data flow**: It receives the workspace ID, turn ID, and conversation ID. It builds workflow enqueue options, calls DBOS with the workspace and turn identifiers, and normally returns nothing. If the task is cancelled, it clears the turn's dispatch timestamp and re-raises the cancellation. If another error happens, it also clears that timestamp and logs that enqueueing was deferred.

**Call relations**: ToolBridge.request calls this after _admit has created the queued turn. Once enqueueing is attempted, request moves on to _terminal to watch for the turn's eventual completion.

*Call graph*: called by 1 (request); 3 external calls (update, workspace_tx, log).


##### `ToolBridge._terminal`  (lines 293–302)

```
async def _terminal(self, turn_id: UUID) -> ToolBridgeResponse
```

**Purpose**: This waits for the dispatched tool-call turn to finish and converts the final turn state into a bridge response. It is how the sandbox gets a simple success or failure instead of raw turn-stream events.

**Data flow**: It takes a turn ID and opens a streamed tail of that turn's updates. As frames arrive, it waits for either a terminal frame, meaning the turn ended, or a parked frame, meaning the turn cannot continue without being resumed. A terminal frame is passed to _response; a parked turn is cancelled and returned as a failure. If the stream ends without a final state, it raises an error.

**Call relations**: ToolBridge.request calls this after enqueueing the tool-call turn. It relies on _response to interpret a normal final result, and it calls cancel_one_turn when the child turn becomes parked instead of completing.

*Call graph*: calls 1 internal fn (_response); called by 1 (request); 2 external calls (__init__, cancel_one_turn).


##### `ToolBridge._response`  (lines 304–316)

```
def _response(self, terminal: TerminalFrame) -> ToolBridgeResponse
```

**Purpose**: This turns a finished turn's terminal record into the public bridge response format. It separates successful tool output from failed turn endings and tries to preserve JSON results when possible.

**Data flow**: It receives a TerminalFrame, which contains the final status and text from the tool-call turn. If the status is not done, it builds a readable failure message from the error fields or terminal text. If the status is done, it tries to parse the text as JSON; if parsing fails, it keeps the text as plain text. The result is returned as ToolBridgeSuccess or ToolBridgeFailure.

**Call relations**: _terminal calls this when the turn stream reports a final terminal frame. It is the last translation step before ToolBridge.request returns the answer to the sandbox caller.

*Call graph*: called by 1 (_terminal); 4 external calls (__init__, __init__, loads, TypeAdapter).


### Extension tools
Adds specialized approved tools that follow the same workbench safety model.

### `extensions/app_notification/ufo_ext_app_notification/notify_tool.py`

`domain_logic` · `tool call during an agent turn`

This file is the producer side of the Notification app. In plain terms, it gives agents a way to say, “This member should probably know about this later,” without putting that message directly into the current chat reply.

The important safety rule is that the model does not choose the recipient. The recipient is the member whose authority the current turn is already running under. If the turn is not tied to a member, the tool refuses, because a notification with no person behind it would be meaningless.

The file also prevents notification loops. The Notification agent itself is not allowed to notify itself, like a mailbox sending mail to its own mailbox forever. A turn that is already delivering a notification is also blocked from creating another notification about that delivery.

The input is small and structured: a `subject`, which is the stable thing being reported, and a `body`, which explains what happened and why it matters. If another notification with the same subject already exists, the store can fold the new event into the existing one instead of creating a duplicate. The tool returns only a short status line, or a clear refusal reason when a rule blocks it.

#### Function details

##### `_require_ext`  (lines 82–85)

```
def _require_ext(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: This function makes sure the notification tool has the extension context it needs. The extension context is the object that gives this code access to the app’s workspace-specific services and storage.

**Data flow**: It receives an optional extension context. If the context is present, it returns it unchanged. If it is missing, it raises an error immediately, because the notification tool cannot safely continue without knowing which app installation and storage to use.

**Call relations**: The main `notify` function calls this after confirming there is a member to notify. It acts as an early guardrail before the tool opens the notification store or looks up the inbox agent.

*Call graph*: called by 1 (notify).


##### `_refusal`  (lines 88–89)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: This function creates a standard error-style tool result when the notification request is not allowed. It keeps all refusal replies shaped the same way.

**Data flow**: It receives a plain text reason, wraps that text as tool output, marks the result as an error, and returns it to the caller. It does not write any notification or change stored data.

**Call relations**: The `notify` function uses this whenever a safety rule blocks the request, such as no member being present, no inbox existing, the notification agent trying to notify itself, or a delivery turn trying to create a new notification.

*Call graph*: called by 1 (notify); 2 external calls (__init__, __init__).


##### `notify`  (lines 92–121)

```
async def notify(ctx: ToolContext, args: NotifyInput) -> ToolResult
```

**Purpose**: This is the actual tool handler that queues a notification for the current member. It checks the rules, finds the Notification app’s inbox, writes the message, and tells the calling agent whether it was queued or folded into an existing notification.

**Data flow**: It receives the tool context, which includes the current authority, turn, agent, conversation, and extension context, plus the requested subject and body. First it finds the member attached to the current authority. Then it checks that the call has the needed extension context, that this turn is not already delivering a notification, that the Notification inbox exists, and that the calling agent is not the inbox itself. If all checks pass, it writes the notification to the store with the member, subject, body, source agent, turn, and conversation details. It returns a short success message, a folded-duplicate message, or a refusal reason.

**Call relations**: This function is called by the tool system when an agent invokes `notify`. It relies on helper functions in this file for refusals and context checking, asks the authority system which member the turn belongs to, asks the notification store whether this is a delivery turn, looks up the provisioned inbox agent, and finally hands the message to `NotificationStore.post` to record it.

*Call graph*: calls 2 internal fn (_refusal, _require_ext); 5 external calls (__init__, __init__, __init__, authority_member_id, inbox_agent_id).


### Bridge request schema
Defines the validated message formats and available tool listings used by live sandbox bridge requests.

### `core/src/ufo/runtime/tools/bridge.py`

`data_model` · `live-run tool request handling and bridge setup`

This file is the contract for a safe tool doorway inside a live sandbox run. Think of it like the menu and order form at a service counter: it says which actions are available, what a valid request must look like, and what kind of reply comes back. Without this file, different parts of the system could disagree about how to ask for a tool call, which tools are allowed through the bridge, or how success and failure should be reported.

The request models describe three basic actions: list available tools, get the schema for one tool, or execute one tool. A small validation step prevents mixed-up requests, such as asking to “list” tools while also sending arguments meant for execution. The response models keep replies simple: either success with a result, or failure with an error message.

The file also names the fixed bridge tools, including object tools such as listing or applying object changes, plus gateway tools for external connectors. The `bridge_tools` function builds the actual callable set by combining built-in object verbs with approved unbound tools declared by extension manifests. It then runs them through `ToolRegistry`, which acts like a sanity check that the resulting set is valid and not internally inconsistent.

#### Function details

##### `ToolBridgeRequest._matches_action`  (lines 52–58)

```
def _matches_action(self) -> 'ToolBridgeRequest'
```

**Purpose**: This validation step checks that a bridge request makes sense for the action it claims to perform. It prevents confusing or unsafe combinations, such as a plain tool-list request carrying a tool name or arguments.

**Data flow**: It reads the already-filled request fields: `action`, `tool_name`, and `arguments`. If the action is `list`, it requires the request to have no tool name and no arguments; if the action is `get_schema` or `execute`, it requires a tool name. If the request is valid, the same request object continues forward; if not, validation stops with an error.

**Call relations**: This is called automatically by Pydantic, the data-checking library, after a `ToolBridgeRequest` is built from incoming data. It does not call other project functions; its job is to guard the doorway before any requester or executor sees the request.


##### `ToolBridgeRequester.request`  (lines 92–92)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is a protocol method: it describes the shape of any object that can send a bridge request on behalf of one signed live run. It lets the rest of the code rely on a common interface without caring which concrete requester implementation is used.

**Data flow**: It accepts a `RunToken`, which identifies and authorizes the live sandbox run, and a validated `ToolBridgeRequest`, which says what tool action is wanted. A real implementation will use those inputs to contact or invoke the bridge, then return either a success response with a result or a failure response with an error string.

**Call relations**: Other code can depend on `ToolBridgeRequester` when it needs to ask the bridge for tool work. This file only defines the promise; concrete implementations elsewhere provide the actual behavior behind `request`.


##### `bridge_tools`  (lines 95–111)

```
def bridge_tools(manifests: tuple[Manifest, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: This builds the exact set of tools that the bridge is allowed to expose. It combines built-in object tools with approved connector or extension tools, while deliberately excluding bound tools that must be reached through `object_action` instead.

**Data flow**: It receives a tuple of extension `Manifest` objects, each of which may declare tools and connectors. It first creates the built-in object-related tools from `ObjectVerbs`, then scans the manifests for unbound tools whose names are on the bridge’s approved-name list. It joins those tools into one tuple, checks the combined set by constructing a `ToolRegistry`, and returns the tuple of allowed tool definitions.

**Call relations**: This is used when the bridge needs to know its callable menu. It calls `ObjectVerbs.__init__` as part of creating the object-tool collection, and it calls `ToolRegistry.__init__` to validate/register the final tool set before handing it back to the caller.

*Call graph*: 2 external calls (__init__, __init__).


### Tool workbench context
Provides the shared safe execution context, result shapes, cleanup handling, artifact support, and account selection used by tools.

### `core/src/ufo/runtime/tools/context.py`

`orchestration` · `tool execution and turn cleanup`

A tool in this system is not allowed to reach into the whole application freely. Instead, it gets a ToolContext, which is like a carefully prepared tool belt: it contains only the sandbox, files, permissions, audience, accounts, billing hooks, subagent controls, and cleanup hooks that this one tool call is allowed to use. Without this file, each tool would have to invent its own way to report errors, upload artifacts, choose connected accounts, spawn child agents, and decide what information is private. That would make leaks, duplicate side effects, and confusing failures much more likely.

The file also standardizes how tools answer. ToolResult carries normal output. ToolFailure carries a clear failure summary, the operation being attempted, any work already done, and bounded command or provider diagnostics. Long text is clipped so a model is not flooded with huge logs.

Several helpers protect boundaries. Audience and authority properties decide whose memory or conversation space a write belongs to. Connector helpers choose only the external accounts this turn is allowed to use. Artifact helpers store generated files and previews under core-controlled names. Metering helpers record paid image and video usage. Cleanup hooks close per-turn resources, such as browser connections, even if the turn fails or is cancelled.

In short, this file is the contract between tool code and the rest of the runtime.

#### Function details

##### `UnknownSubagentProfile.__init__`  (lines 132–137)

```
def __init__(self, requested: str, registered: tuple[str, ...]) -> None
```

**Purpose**: Builds a helpful error when code asks for a subagent profile name that does not exist. The message includes both the bad name and the valid choices, so the caller can recover instead of seeing a vague lookup failure.

**Data flow**: It receives the requested profile name and the registered profile names. It formats them into a clear error message and stores both pieces of information on the exception for later logging or reporting.

**Call relations**: The subagent registry calls this when a profile lookup fails. The exception then travels back toward the spawning tool so the model or caller can retry with one of the listed names.

*Call graph*: called by 1 (get).


##### `SpawnPayloadRejected.__init__`  (lines 148–152)

```
def __init__(self, target: str, keys: str, detail: str) -> None
```

**Purpose**: Creates an error for the case where a spawn target exists, but the supplied input does not match what that target accepts. It explains what target was called, what keys it accepts, and what was wrong.

**Data flow**: It takes the target name, a description of accepted keys, and a validation detail. It turns those into a user-facing message and stores the same details as fields on the exception.

**Call relations**: The subagent runtime uses this after it has resolved a valid target but rejected the payload. This separates a fixable input-shape problem from an unknown-target problem.

*Call graph*: called by 1 (_validated).


##### `SpawnModelRejected.__init__`  (lines 163–165)

```
def __init__(self, message: str, requested: str) -> None
```

**Purpose**: Stores an error saying a requested model cannot be used for a spawned child turn. It keeps the rejected model id so logs and callers can identify exactly what was refused.

**Data flow**: It receives a ready-made message and the requested model id. It initializes the exception with the message and saves the model id on the exception.

**Call relations**: The class methods on SpawnModelRejected build specific versions of this error. Subagent setup raises those versions when model pinning would be invalid.


##### `SpawnModelRejected.unknown`  (lines 168–173)

```
def unknown(cls, requested: str, models: tuple[str, ...]) -> 'SpawnModelRejected'
```

**Purpose**: Builds a rejection for a spawn that asked for a model this deployment does not serve. It tells the caller which model was unknown and lists the valid served models.

**Data flow**: It receives the requested model id and the tuple of supported model ids. It sorts and formats the supported ids, then returns a SpawnModelRejected exception instance.

**Call relations**: Subagent runtime configuration calls this while preparing a child turn. If the model is not in the registry, this error stops the spawn before a broken child turn is created.

*Call graph*: called by 1 (_child_runtime_config).


##### `SpawnModelRejected.pinned_tree`  (lines 176–181)

```
def pinned_tree(cls, requested: str, pinned: str) -> 'SpawnModelRejected'
```

**Purpose**: Builds a rejection for a spawn that tries to override a model choice already fixed for the whole turn tree. This prevents a child from silently running on a different model than the caller requested.

**Data flow**: It receives the requested model and the already pinned model. It creates an exception explaining that every child in this tree must keep the pinned model.

**Call relations**: Subagent runtime configuration calls this when a parent turn tree already has a model pin. The error tells the spawning tool to omit the model rather than trying to override it.

*Call graph*: called by 1 (_child_runtime_config).


##### `SpawnModelRejected.own_account`  (lines 184–189)

```
def own_account(cls, requested: str, target: str) -> 'SpawnModelRejected'
```

**Purpose**: Builds a rejection for a spawn target that runs on the member’s own external model account and therefore cannot be pinned by this system. It prevents the caller from believing a model override took effect when it cannot.

**Data flow**: It receives the requested model and the target name. It returns a SpawnModelRejected exception explaining that the target’s own account controls available models.

**Call relations**: The subagent spawning flow calls this when a profile uses the member’s own provider account. The caller is told to spawn without a model pin.

*Call graph*: called by 1 (spawn).


##### `UnknownSpawnTarget.__init__`  (lines 196–203)

```
def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None
```

**Purpose**: Creates an error when a spawn request names something that is neither a known profile nor a known workspace agent. It includes the available profile and agent names so the caller can choose a valid target.

**Data flow**: It receives the requested target plus available profile and agent names. It formats them into a clear message and stores all three values on the exception.

**Call relations**: The subagent resolver and agent-spawn checker call this when target resolution fails. It gives the model a concrete correction path instead of a dead-end failure.

*Call graph*: called by 2 (_require_agent_spawn, _resolve).


##### `SpawnNeedsOwnModelKey.__init__`  (lines 217–225)

```
def __init__(self, requested: str, connect_url: str | None=None) -> None
```

**Purpose**: Creates an error for coding-style spawn targets that require the member to connect their own ChatGPT or Claude account first. It points the user toward the credentials page when a base URL is available.

**Data flow**: It receives the requested target and optionally the public site URL. It builds a message explaining the missing account connection and stores the requested target on the exception.

**Call relations**: The subagent spawn flow raises this when a target depends on a member-owned model account and no usable account is connected. The model can then tell the member where to connect one.

*Call graph*: called by 1 (spawn).


##### `AmbiguousSpawnTarget.__init__`  (lines 232–237)

```
def __init__(self, requested: str) -> None
```

**Purpose**: Creates an error when a bare spawn name matches both a profile and an agent. It tells the caller to use an explicit prefix so the system knows which one is meant.

**Data flow**: It receives the ambiguous name. It builds a message suggesting the two qualified forms, profile:name and agent:name, and stores the original name.

**Call relations**: The subagent resolver calls this during target lookup. The error prevents the system from guessing and possibly running the wrong kind of child turn.

*Call graph*: called by 1 (_resolve).


##### `clipped`  (lines 255–261)

```
def clipped(value: str, limit: int) -> str
```

**Purpose**: Shortens long text to a fixed limit and appends a clear note saying how much was removed. This keeps tool diagnostics readable and bounded.

**Data flow**: It receives a string and a character limit. If the string is short enough, it returns it unchanged; otherwise it returns the beginning plus a standard truncation notice.

**Call relations**: Validation methods for command output and tool failures use this helper. That means summaries, stdout, stderr, and provider errors all get cut in the same recognizable way.

*Call graph*: called by 3 (_bound_stream, _bound_provider, _nonempty_summary).


##### `CommandDiagnostics._bound_stream`  (lines 279–280)

```
def _bound_stream(cls, value: str) -> str
```

**Purpose**: Limits stdout and stderr text stored in failed command diagnostics. This protects the model and logs from enormous command output.

**Data flow**: It receives one command-output string. It passes the string through clipped with the configured stream limit and returns the shortened version.

**Call relations**: Pydantic, the data validation library, calls this automatically when CommandDiagnostics is created. It delegates the actual cutting rule to clipped.

*Call graph*: calls 1 internal fn (clipped).


##### `ToolFailure._nonempty_summary`  (lines 315–316)

```
def _nonempty_summary(cls, value: str) -> str
```

**Purpose**: Makes sure every tool failure has a real, readable summary. If a tool provides an empty summary, it replaces it with a standard notice rather than letting silence look meaningful.

**Data flow**: It receives the summary string, trims whitespace, substitutes a default notice if empty, clips it to the summary limit, and returns the cleaned text.

**Call relations**: Pydantic calls this when ToolFailure is built. It uses clipped so failure summaries follow the same length policy as other diagnostics.

*Call graph*: calls 1 internal fn (clipped).


##### `ToolFailure._bound_applied`  (lines 320–321)

```
def _bound_applied(cls, value: tuple[AppliedEffect, ...]) -> tuple[AppliedEffect, ...]
```

**Purpose**: Limits how many already-applied effects a failing tool can report. This keeps a failure response from becoming an unbounded list.

**Data flow**: It receives a tuple of applied effects. It returns only the first configured maximum number of entries.

**Call relations**: Pydantic calls this during ToolFailure validation. The failure can still explain what already happened, but within a safe size.


##### `ToolFailure._bound_provider`  (lines 325–326)

```
def _bound_provider(cls, value: str | None) -> str | None
```

**Purpose**: Limits third-party provider error text attached to a tool failure. Provider messages can be large or noisy, so this keeps them useful but bounded.

**Data flow**: It receives either provider text or None. None stays None; text is passed through clipped and returned.

**Call relations**: Pydantic calls this when ToolFailure is created. It shares the same clipping helper used for summaries and command streams.

*Call graph*: calls 1 internal fn (clipped).


##### `ToolFailure.result`  (lines 328–333)

```
def result(self, *, untrusted: bool=False) -> ToolResult
```

**Purpose**: Turns a structured failure into the standard ToolResult shape that the rest of the tool engine expects. It marks the result as an error and can also mark it as untrusted data.

**Data flow**: It reads the ToolFailure fields, serializes them to JSON text, wraps that text in a TextContent block, and returns a ToolResult with is_error set to true.

**Call relations**: Tool handlers can call this when they need to return a failure. It hands the engine a normal result object while preserving structured failure details inside the text payload.

*Call graph*: 2 external calls (__init__, __init__).


##### `Spawn.__call__`  (lines 404–414)

```
async def __call__(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False, model: str
```

**Purpose**: Describes the callable interface for starting a child agent or subagent profile. It lets a tool delegate a typed subtask and optionally wait for the child’s validated answer.

**Data flow**: A caller supplies the target name, input payload, background/waiting behavior, deduplication key, optional display name, and optional model pin. An implementation validates the request, starts or reconnects to a child turn, and returns a SpawnResult describing that child and possibly its output.

**Call relations**: This is a protocol, meaning it defines what shape an implementation must have. ToolContext exposes a Spawn implementation so tools can delegate work without knowing the lower-level subagent machinery.


##### `SubagentControl.result`  (lines 424–424)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Describes how to fetch the result of an already-spawned background subagent. A tool would use it when it has a child turn id and wants the finished output or terminal state.

**Data flow**: It takes a child turn id. An implementation looks up that child and returns a SpawnResult containing its identity, terminal state, and output if available.

**Call relations**: This is part of the SubagentControl protocol attached to ToolContext. Lifecycle-style tools use implementations of this interface to inspect background work.


##### `SubagentControl.wait`  (lines 426–426)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes how to wait on one or more background subagents and receive their current terminal statuses. It gives tools a bounded way to pause for child work.

**Data flow**: It takes a tuple of child turn ids. An implementation waits according to its own rules and returns one SubagentStatus per child that has reached a reportable state.

**Call relations**: This protocol method is supplied by the subagent workflow behind ToolContext. Tools can call it without knowing how child turns are stored or scheduled.


##### `SubagentControl.cancel`  (lines 428–428)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes how to stop a running background subagent. It is used when a tool or user no longer wants that child work to continue.

**Data flow**: It takes a child turn id. An implementation cancels that child if possible and returns a SubagentStatus describing the result.

**Call relations**: This is one operation in the SubagentControl protocol. The real subagent controller implements it and ToolContext exposes it to tools that manage child lifecycles.


##### `SubagentControl.message`  (lines 430–432)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Describes how to send a follow-up message to an existing subagent. The deduplication key helps avoid sending the same follow-up twice after a retry.

**Data flow**: It receives a child turn id, message text, deduplication key, and a flag saying whether the child should deliver its own result. An implementation admits the message to the child turn and returns its status.

**Call relations**: This protocol method belongs to the same background subagent control surface as result, wait, and cancel. It lets tools continue a child conversation through ToolContext.


##### `TurnCleanup.register`  (lines 446–447)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous cleanup action to be run when the turn ends. Tools use this for resources like browser connections that must not outlive the turn.

**Data flow**: It receives a no-argument async closer function. It appends that function to the cleanup list and returns nothing.

**Call relations**: Tools that open per-turn resources call this once when they create the resource. Later, the turn loop drains the registry so those resources are closed.


##### `TurnCleanup.drain`  (lines 449–455)

```
async def drain(self) -> None
```

**Purpose**: Runs all registered cleanup actions at the end of a turn. It closes resources in reverse order and logs failures instead of letting one bad closer stop the rest.

**Data flow**: It reads the internal list of closers. While the list is not empty, it pops a closer, awaits it, and logs any exception that occurs.

**Call relations**: The turn loop calls this during teardown. Resource-opening tools feed it through register, and drain makes sure their connections or leases are not leaked.

*Call graph*: 1 external calls (log).


##### `_owner_note`  (lines 467–478)

```
def _owner_note(withheld: Sequence[Grant], audience: Audience) -> str
```

**Purpose**: Builds a small explanatory note naming the owners of private connector accounts that were not available to this call. It avoids naming owners in externally shared spaces where that information should not be exposed.

**Data flow**: It receives withheld grants and the current audience. If the audience is foreign-shared or no owner emails are known, it returns an empty string; otherwise it returns a parenthesized owner or owners note.

**Call relations**: connector_connection uses this when explaining why a connector account could not be selected. The note helps a retry name the right member without leaking owner details to outside audiences.

*Call graph*: called by 1 (connector_connection); 1 external calls (startswith).


##### `ToolContext.authority`  (lines 518–524)

```
def authority(self) -> ExecutionAuthority
```

**Purpose**: Returns the authority, or acting identity, for this tool call. If a live speaking member is attached, that member is the authority; otherwise the turn’s delegated or workspace authority is used.

**Data flow**: It reads speaker_member_id and turn.on_behalf_of_member_id. It returns a MemberAuthority for a live speaker or converts the stored member id into the appropriate execution authority.

**Call relations**: Other ToolContext properties and connector helpers use this to decide what the call may do. It is the base identity check for later permission decisions.

*Call graph*: 2 external calls (__init__, authority_from_member_id).


##### `ToolContext.effective_audience`  (lines 527–537)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides which audience a write should belong to. In a shared workspace conversation with a known member, it narrows the write to that member’s private conversation audience; otherwise it keeps the current conversation audience.

**Data flow**: It reads the call authority and current audience. If there is no acting member or the audience is not the shared workspace audience, it returns the current audience; otherwise it returns the member-specific conversation audience.

**Call relations**: Tools that write memories or records use this to avoid putting private-room facts into the wrong shared space. It builds on authority and the audience helpers.

*Call graph*: 2 external calls (authority_member_id, conversation_audience).


##### `ToolContext.read_subjects`  (lines 540–549)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes the set of subjects this tool call may read from. It combines the conversation’s readable subjects with the active member’s private subject when there is one.

**Data flow**: It reads the current audience and authority. It turns the audience into subjects, adds the acting member’s subject if present, and returns the combined frozen set.

**Call relations**: source_reader and extension code use this when reading synced sources or memory. It keeps read access tied to both the room and the exact requester.

*Call graph*: 3 external calls (authority_member_id, audience_subjects, member_subject).


##### `ToolContext.store_preview`  (lines 551–597)

```
async def store_preview(self, sandbox_path: str, name: str, *, extension: str='png') -> StoredPreview | None
```

**Purpose**: Stores an image file produced inside the sandbox as a preview artifact. It returns the stored blob key and byte size, or None if the preview could not be measured or uploaded.

**Data flow**: It receives a sandbox path, display name, and file extension. It asks the sandbox for the file size, creates a core-owned artifact key, uploads either through an S3 presigned PUT or a local blob stream, and returns a StoredPreview with the key and measured size.

**Call relations**: Site-building and share-card extension code call this after rendering a visual preview. It uses the sandbox, blob store, logging, path quoting, and generated ids to move the preview without trusting tool code to name public artifact locations.

*Call graph*: called by 2 (design_ufo_application, _compose); 5 external calls (__init__, quote, log, shell_path, uuid4).


##### `ToolContext.render_site_preview`  (lines 599–606)

```
async def render_site_preview(self, name: str, port: int, width: int, height: int) -> StoredPreview | None
```

**Purpose**: Asks the configured preview service to capture an image of a hosted sandbox port. If no preview service is configured, it simply returns None.

**Data flow**: It receives a preview name, port, width, and height. It chooses the sandbox conversation id when available, otherwise the normal conversation id, and passes those details to the site previewer.

**Call relations**: The sites extension calls this when it needs a screenshot-like preview. ToolContext supplies the preview service so the extension does not need to know deployment-specific details.

*Call graph*: called by 1 (_illustrate).


##### `ToolContext.source_reader`  (lines 608–618)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Creates a SourceReader describing who is asking to read synced source pages. It includes the current agent, the live speaking member, and the subjects the call may read.

**Data flow**: It reads the turn’s agent id, speaker member id, and computed read_subjects. It packages them into a SourceReader object.

**Call relations**: Memory and sources extensions call this before listing, fetching, or searching pages. It gives those extensions one consistent access description for source reads.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 620–629)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images against this turn’s billing ledger. Image generation providers price their own calls, but core records the charge in the workspace ledger.

**Data flow**: It receives the model name, image count, and cost in micro-dollars. It opens a workspace database transaction and writes an image usage record tied to the workspace and turn.

**Call relations**: The OpenRouter image extension calls this after generating images. The function hands off to the accounting layer so provider-specific tools do not write billing rows themselves.

*Call graph*: called by 1 (generate); 2 external calls (workspace_tx, record_image_usage).


##### `ToolContext.meter_videos`  (lines 631–639)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos against this turn’s billing ledger. This mirrors image metering for video providers whose pricing is not token-based.

**Data flow**: It receives the model name, video count, and cost in micro-dollars. It opens a workspace database transaction and writes a video usage record tied to the workspace and turn.

**Call relations**: The OpenRouter video extension calls this after generating videos. The function connects extension-level pricing to core billing records.

*Call graph*: called by 1 (generate); 2 external calls (workspace_tx, record_video_usage).


##### `ToolContext.share_artifact`  (lines 641–724)

```
async def share_artifact(self, filename: str, data: bytes, subject: str | None=None, *, preview: StoredPreview | None=None) -> None
```

**Purpose**: Publishes a small in-process file as a shared artifact of this turn. It writes the bytes to blob storage and records a database row so user-facing surfaces can show and download the file.

**Data flow**: It receives a filename, bytes, optional subject, and optional preview. It checks size limits and preview validity, chooses a deterministic artifact id when an idempotency key exists, uploads the blob, inserts the shared_artifact row, cleans up the blob if a new database insert fails, and optionally triggers artifact publishing.

**Call relations**: iMessage and sites extension code call this when they create files directly in process. It coordinates blob storage, media typing, database records, idempotent naming, cleanup logging, and publication.

*Call graph*: called by 2 (run, design_ufo_application); 8 external calls (now, select, workspace_tx, log, artifact_media_type, raster_image_media_type, uuid4, uuid5).


##### `ToolContext.speaker_is_admin`  (lines 726–736)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live requesting member is a workspace admin. If there is no live speaker, it returns false, so background authority cannot perform admin-only actions.

**Data flow**: It reads speaker_member_id. If absent, it returns false; otherwise it opens a database transaction and asks the seats layer whether that member is an admin in this workspace.

**Call relations**: Many object and credential operations call this before allowing admin-scoped behavior. _credential_authorization also uses it to restrict credential slot authorization.

*Call graph*: called by 26 (_widens_for_admin, delete, _visible_rows, request_credentials_handler, restore, apply, delete, get, list, status (+15 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 738–749)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether the current turn’s agent is marked as the workspace’s main agent. Some actions are only visible or allowed through the main agent.

**Data flow**: It opens a database transaction, selects the is_main field for this turn’s agent in this workspace, converts the result to a boolean, and returns it.

**Call relations**: Member, workspace, and web-audience code call this when deciding what the agent can see or change. It gives those flows a simple yes-or-no answer from the database.

*Call graph*: called by 6 (add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.agent_visibility`  (lines 751–763)

```
async def agent_visibility(self) -> AgentVisibility
```

**Purpose**: Reads whether the current agent is private or workspace-visible. It raises an error if the stored value is outside the expected set.

**Data flow**: It opens a database transaction, selects the agent visibility value for this turn’s agent and workspace, validates that it is either private or workspace, and returns it.

**Call relations**: The sites extension calls this during homepage redeployment. The validation protects callers from silently accepting an unsupported visibility value.

*Call graph*: called by 1 (_redeploy_homepage); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 765–767)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for an extension credential slot. It produces a sealed authorization string that can later be opened only under the right workspace, member, and slot.

**Data flow**: It receives a credential slot and payload. It first runs the shared credential-authorization checks, then asks the credential request service to authorize the payload for this workspace and member.

**Call relations**: The Slack extension calls this when building an OAuth link. It delegates all permission checks to _credential_authorization before touching the credential request service.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 1 (_oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 769–771)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens a previously sealed credential authorization for an extension credential slot. It verifies that the current call is still allowed to use that slot before opening it.

**Data flow**: It receives a credential slot and sealed authorization string. It runs the shared authorization checks, then asks the credential request service to open the sealed value for this workspace, member, and slot.

**Call relations**: This is the counterpart to begin_credential_authorization. Both methods rely on _credential_authorization so the same admin, speaker, extension, and deployment checks apply.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext._credential_authorization`  (lines 773–782)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the common safety checks for credential slot authorization. It ensures there is a live member, the extension actually declared the slot, credential storage is configured, and the member is an admin.

**Data flow**: It reads speaker_member_id, extension credential declarations, requestable_credentials, and admin status. It raises a clear error on any failed check; otherwise it returns the credential request service and member id.

**Call relations**: begin_credential_authorization and open_credential_authorization both call this before creating or opening sealed credential requests. It centralizes the rules so the two public methods cannot drift apart.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (begin_credential_authorization, open_credential_authorization); 1 external calls (__init__).


##### `ToolContext.connector_account`  (lines 784–796)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the broker account id that a connector tool should pass to an external connector execution API. It is the simple version of connector selection when the caller only needs the account id.

**Data flow**: It receives a provider name and optional account id. It calls connector_connection to select and validate the exact connection, then returns that connection’s account_id.

**Call relations**: The sample connector execution code calls this. It delegates to connector_connection so account selection, ambiguity checks, and private-account rules remain in one place.

*Call graph*: calls 1 internal fn (connector_connection); called by 1 (_connector_execute).


##### `ToolContext.connector_connection`  (lines 798–852)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Selects the exact connected external account this tool call is allowed to use. It respects private member grants, agent-shared grants, optional account targeting, and ambiguity rules.

**Data flow**: It receives a provider and optional account id. It gathers private, shared, and withheld grants; if an account id is supplied, it matches only allowed grants or explains why the target is unavailable; without an account id, it prefers private grants then shared grants, rejects zero or multiple matches, and returns a ConnectorConnection.

**Call relations**: connector_account, connector tools, and source tools call this before using an external account. It uses _connector_account_tiers for grant classification and _owner_note to produce safe, helpful private-account messages.

*Call graph*: calls 2 internal fn (_connector_account_tiers, _owner_note); called by 3 (connector_account, call_external_tool, _resolved_account); 2 external calls (__init__, __init__).


##### `ToolContext.require_connector_connection`  (lines 854–874)

```
async def require_connector_connection(self, selected: ConnectorConnection) -> None
```

**Purpose**: Rechecks that a previously selected connector connection is still active and usable. This guards against a revoke, disconnect, reconnect, or sharing change that happens after selection but before an external side effect.

**Data flow**: It receives a ConnectorConnection chosen earlier. It recomputes the current private and shared grant tiers for that provider and looks for a grant with the same grant id, connection id, account id, and owner; if none matches, it raises an error.

**Call relations**: Connector flows can call this just before sending work to the broker. It uses the same grant-tier logic as initial selection, but verifies the exact generation has not changed.

*Call graph*: calls 1 internal fn (_connector_account_tiers).


##### `ToolContext.connector_accounts`  (lines 876–882)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists the external account ids this call may use for a provider. It includes the acting member’s private grants and agent-shared grants, depending on authority.

**Data flow**: It receives a provider name. It gathers private and shared grant tiers, combines their account ids, removes duplicates, sorts them, and returns the result as a tuple.

**Call relations**: Source tools call this when resolving or presenting available accounts. It relies on _connector_account_tiers so the list follows the same permission rules as actual connector execution.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 884–923)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant], list[Grant]]
```

**Purpose**: Classifies active connector grants for one provider into usable private grants, usable shared grants, and private grants withheld from this call. The withheld group is used to produce better errors when a different speaking member could unlock access.

**Data flow**: It checks that the grant store exists, reads the acting member from authority, loads active grants, filters them by provider, sorts usable private grants for the acting member, sorts shared grants, and conditionally returns other members’ private grants as withheld.

**Call relations**: connector_connection, connector_accounts, and require_connector_connection all call this. It is the common permission engine behind connector account selection and revalidation.

*Call graph*: called by 3 (connector_accounts, connector_connection, require_connector_connection); 2 external calls (__init__, authority_member_id).

## 📊 State Registers Touched

- `reg-pack-extension-registry` — The approved set of installed packs and extensions, including what tools, jobs, agents, hooks, providers, and surfaces they add.
- `reg-tool-catalog` — The shared menu of tools the agent may call, including built-in tools, extension tools, and guarded bridge tools.
- `reg-auth-tokens-sessions` — The login, surface, sandbox, and signing tokens that prove who a request belongs to and what it may access.
- `reg-authority-context` — The current acting identity for runtime work, saying which workspace, member, and agent are allowed to act.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-execution-environment-policy` — The shared rules for where commands and tools may run, such as local execution, Docker, cloud sandboxes, terminals, and browser sessions.
- `reg-egress-proxy-policy` — The network access rules and proxy state that decide which outside hosts can be reached and when secrets may be attached.
- `reg-credential-vault-connections` — The lockbox of account connections, OAuth grants, API keys, BYOK attempts, and agent permissions to use outside services.
- `reg-object-artifact-site-store` — The shared store of workspace objects, files, artifacts, previews, reports, websites, todos, and objective records.
- `reg-billing-ledger-balance` — The shared money and usage record, including spend caps, model costs, sandbox and egress usage, prepaid balances, and export progress.
- `reg-observability-trace` — The tracing, health, logging, and traceparent state used to connect work across turns, subagents, workers, and cleanup.
- `reg-blob-storage-state` — The raw byte/blob storage namespaces and content-addressed stored files that back artifacts, previews, environment files, workspace files, and deploy-wide assets.
- `reg-human-request-state` — Pending and resolved human-interaction requests, including agent questions, secret requests, credential requests, and connection-authorization handoffs.
- `reg-deployment-artifact-state` — The built runtime bundle and sandbox image/client artifact state, including image recipe/version alignment and deployment preflight results used by startup and sandbox execution.
- `reg-browser-automation-sessions` — Live browser automation contexts, pages, cookies, downloads, screenshots, and backend session handles used by tool execution and released during teardown.
- `reg-running-command-process-state` — Active shell commands, subprocesses, terminal tasks, process identifiers, execution logs, and interrupt state tied to conversations or sandboxes.
- `reg-tool-bridge-invocation-state` — Durable request/result state for sandbox-to-host tool bridge calls, including pending bridge invocations, approvals, idempotency, and returned outputs.
- `reg-turn-assembly-snapshot` — The resolved per-turn host package handed into execution, including selected agent/model, effective prompts, allowed tools/spawn menu, seeded file digests, skills, and routing choices.
