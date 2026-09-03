# Tool dispatch and sandboxed command/file execution  `stage-9`

This stage is part of the system’s main work loop. It is used whenever the model asks to use a tool, such as running a command, editing a file, asking the user a question, or starting a helper agent. First, the tool contract and registry act like a rulebook and catalog. They check that the requested tool exists, that the request has the right shape, and that the tool only gets the permissions it needs. The tool context is the safe doorway the tool works through, with path checks and limits to prevent accidental or unsafe file access. Long-running task journals let slow commands continue and be checked later.

The sandbox carrier lifecycle provides the protected workspace where risky work happens. It may be local, Docker-based, cloud-based, or connected to a user terminal, but the rest of the system sees one common interface. The bridge lets sandboxed code ask the main runtime to run approved tools without bypassing normal rules. Built-in tools provide the everyday abilities. The REPL extension adds persistent Python and JavaScript scratchpads, while the batching helper helps the harness run ordered work safely.

## Sub-stages

- [Tool contract, context, and long-running task journals](stage-9.1.md) `stage-9.1` — 8 files
- [Sandbox carrier lifecycle](stage-9.2.md) `stage-9.2` — 10 files

## Files in this stage

### Harness batching
Small harness utilities prepare ordered work batches before tool execution begins.

### `core/src/ufo/harness/tools.py`

`util` · `cross-cutting`

This file solves a practical scheduling problem: some work items can safely be grouped and potentially run in parallel, while others must stand alone. The important rule is that the visible order of the original list must not be scrambled. Think of it like sorting people in a queue into small elevator groups: people who are allowed to ride together can share an elevator, but anyone who must ride alone gets their own trip, and nobody jumps ahead in line.

The single helper, `dispatch_segments`, walks through the input items from first to last. When it sees items marked as safe for parallel dispatch, it collects consecutive ones into a batch, up to a caller-provided maximum size. When it reaches an unsafe item, it first sends out any waiting safe batch, then yields the unsafe item by itself. At the end, it yields any final batch that was still waiting.

This matters because it gives the rest of the harness a simple, predictable shape of work: each yielded tuple is either a group that may be dispatched together or a single item that should be treated separately. It also protects against a bad configuration by rejecting a batch limit below one.

#### Function details

##### `dispatch_segments`  (lines 4–23)

```
def dispatch_segments(items: tuple[ItemT, ...], *, parallel_safe: Callable[[ItemT], bool], limit: int) -> Iterator[tuple[ItemT, ...]]
```

**Purpose**: Splits an ordered tuple of items into smaller ordered tuples, grouping only consecutive items that the caller says are safe to run together. Someone would use it before dispatching work so they can gain parallelism without changing the original order or mixing in items that must run alone.

**Data flow**: It receives a tuple of items, a `parallel_safe` test function, and a maximum batch size called `limit`. It checks that the limit is positive, then scans the items in order: safe items are collected into a temporary batch until the batch is full, while unsafe items cause any waiting batch to be emitted first and are then emitted alone. The output is an iterator that yields tuples of items, preserving the original order and changing only how adjacent safe items are grouped.

**Call relations**: This helper does not call other project functions; it relies on the caller-provided `parallel_safe` function to decide which items may be grouped. In the bigger flow, code that is about to dispatch harness work can call this function to turn one ordered list into dispatch-ready segments, then process each yielded tuple according to whether it contains one item or a safe batch.


### Built-in tool dispatch
Core built-in tools define the agent-facing command, file, collaboration, skill, and credential operations, with a bridge for sandboxed code to invoke approved runtime tools safely.

### `core/src/ufo/host/tools/builtins.py`

`domain_logic` · `tool handling during an agent turn`

This is the agent’s standard toolbox. It turns high-level tool calls into safe actions inside the workspace and surrounding system. Without it, an agent could not reliably inspect files, change them, delegate work, hand finished files back to a user, or ask for missing information in the project’s expected way.

A key theme is safety. File access goes through the sandbox, which is the isolated work area where commands and file operations are allowed to run. Reads, writes, edits, globs, and greps use the in-sandbox `ufo fs` command so the heavy work happens beside the files, and only bounded results come back. The file editing tools also remember which paths have been read during the turn, so the agent cannot blindly overwrite or string-replace content it has not seen.

The sharing path is more careful because it moves data outside the sandbox. It measures each file, uploads it to the artifact store, records it in the database, and returns short-lived download links. Directories are automatically packed into `.tar.gz` archives. For some document types, it also asks a preview service to render a thumbnail.

The rest of the file covers conversation-native actions: spawning child agents, asking the user structured questions, loading skill bundles, starting private account connection flows, collecting secrets without putting them in chat, and controlling running spawns.

#### Function details

##### `_bounded_file_path`  (lines 185–188)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the tool result limits once it is encoded as JSON. This prevents a strangely large path from making later file-change reporting too large to return safely.

**Data flow**: It receives a path string, measures the size of that path after JSON encoding, and either returns the same path unchanged or raises an error if it is too large.

**Call relations**: This is used as validation for write and edit file paths before those tool calls reach the actual file-changing handlers.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 362–393)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command in the sandbox and returns the command output or a clear error. If the command takes too long, it is not killed automatically; it can keep running in the background and the agent receives information for checking it later.

**Data flow**: It receives the tool context and a command request. If background mode is requested, it hands off to `_bash_background`. Otherwise it blocks simple sleep-only commands, runs the task, reads the task result, and returns text containing output, timeout information, background handles, or an exit-code error.

**Call relations**: This is the main handler behind the built-in `bash` tool. It uses the shared task-running utilities for launching commands and formatting task handles, and it delegates the detached case to `_bash_background` so foreground and background launches stay consistent.

*Call graph*: calls 1 internal fn (_bash_background); 7 external calls (__init__, __init__, format, flat_sleeps, run_task, task_handles, timeout_notice).


##### `_bash_background`  (lines 396–410)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command and immediately detaches from it. It is used when the caller wants the work to continue without waiting for the result right now.

**Data flow**: It receives the tool context and command text, creates a task id and task directory, asks the sandbox to start the command detached, and returns the task id, log path, and process id. If the command cannot detach, it returns an error result.

**Call relations**: It is called only by `bash_handler` when the `bash` tool is invoked with background mode. It uses the same task-handle formatting as foreground commands that time out and move to the background.

*Call graph*: called by 1 (bash_handler); 4 external calls (__init__, __init__, task_handles, task_id).


##### `_require_str`  (lines 413–416)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Verifies that a value returned by a lower-level file reader is a non-empty string. It gives a clear internal error if expected image or document data is missing.

**Data flow**: It receives an unknown value and a field name. If the value is a non-empty string, it returns it; otherwise it raises an error naming the missing field.

**Call relations**: It is a small guard used by `read_handler` and `_document_result` when turning sandbox file-reader output into text or image content for the tool result.

*Call graph*: called by 2 (_document_result, read_handler).


##### `_document_result`  (lines 419–462)

```
def _document_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a document-read result, such as a PDF, PowerPoint, Word file, or spreadsheet, into a tool result containing extracted text, page information, and rendered page images when available.

**Data flow**: It receives a dictionary from the sandbox file reader. It collects text, page or slide counts, notes, quality reminders, and page images, then returns them as text and image blocks. If the result is malformed or empty, it raises an error.

**Call relations**: It is called by `read_handler` after the sandbox says the file is a supported document type. It uses `_require_str` to make sure image media types and encoded image data are present.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 465–502)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a file from the sandboxed workspace and returns a bounded view of it. It supports text files, images, and several document formats, and it records the path as having been read so later edits or overwrites can be allowed safely.

**Data flow**: It receives a file path plus optional offset and limit. It asks the sandbox `ufo fs read` command for the content, records the path in `ctx.read_paths`, and returns image content, document content, an empty-file message, or text lines with a footer explaining what portion was returned.

**Call relations**: This is the handler behind the built-in `read` tool. It delegates document formatting to `_document_result` and uses `_require_str` when constructing image results.

*Call graph*: calls 2 internal fn (_document_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 505–527)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Writes text to a workspace file while protecting existing files from blind overwrites. If the target file already exists, the sandbox is only allowed to overwrite it when this turn has already read that path.

**Data flow**: It receives the context and desired file path plus text content. It stages the bytes in a temporary workspace file, asks `ufo fs write` to move them into place with the read-before-overwrite rule, adds size and line-count details, formats the result, and marks the path as read for the rest of the turn.

**Call relations**: This is the handler behind the built-in `write` tool. It calls `_file_tool_result` to keep the returned status small and consistent.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (uuid4).


##### `edit_handler`  (lines 530–543)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact string replacements to a file, but only after the file has been read in the current turn. This keeps the agent from modifying unseen content by guesswork.

**Data flow**: It receives a file path and a list of replacements. It first checks the path was read, encodes the old and new strings safely, sends the edit request to `ufo fs edit`, attaches the path to the result, and returns a bounded summary.

**Call relations**: This is the handler behind the built-in `edit` tool. It relies on `read_handler` or `write_handler` having placed the path in `ctx.read_paths`, and it uses `_file_tool_result` for its final response.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 546–560)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats the result of a write or edit into a small JSON text response. If a detailed snippet would make the result too large, it removes that extra detail and keeps the important status fields.

**Data flow**: It receives a result dictionary, serializes it as compact JSON, and returns it if it fits. If not, it drops the snippet and shortens the message before returning; if it still does not fit, it raises an error.

**Call relations**: It is shared by `write_handler` and `edit_handler` so both file-changing tools return results with the same size discipline.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 563–569)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files whose paths match a pattern, such as `**/*.py`, inside the sandbox. It avoids using ad hoc shell commands for file discovery.

**Data flow**: It receives a pattern and optional starting directory, defaults the directory to the workspace root, asks `ufo fs glob` inside the sandbox to find matches, and returns the match list as JSON text.

**Call relations**: This is the handler behind the built-in `glob` tool. It keeps directory traversal inside the sandbox and only sends the bounded result back to the agent.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 572–590)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents for a regular expression, meaning a text pattern language, inside the sandbox. It is the safer built-in alternative to running `grep` or `rg` manually through the shell.

**Data flow**: It receives a search pattern plus optional directory, file filter, context lines, case setting, output mode, and result limit. It builds a parameter dictionary, runs `ufo fs grep` in the sandbox, and returns the bounded search result as JSON text.

**Call relations**: This is the handler behind the built-in `grep` tool. Like `glob_handler`, it keeps the expensive scan close to the files and only returns the prepared result.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 593–627)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies one already-measured file from the sandbox into the artifact store, which is where user-downloadable shared files live. It supports both S3-style cloud storage and a local filesystem store.

**Data flow**: It receives the sandbox path, destination key, file size, and SHA-256 digest. For S3, it creates a presigned upload URL tied to the size and checksum, then asks the sandbox to upload with `curl`. For filesystem storage, it streams the file through the sandbox into the blob store.

**Call relations**: It is called by `_staged_share` after preflight measurement. It is the point where shared file bytes actually leave the sandbox and enter durable artifact storage.

*Call graph*: called by 1 (_staged_share); 3 external calls (b64encode, quote, shell_path).


##### `_discard_artifact`  (lines 640–644)

```
async def _discard_artifact(ctx: ToolContext, key: str) -> None
```

**Purpose**: Best-effort cleanup for an artifact blob that should not remain after a failed share or preview attempt. It tries to delete the blob and logs cleanup failures instead of hiding the original problem.

**Data flow**: It receives a blob key, asks the blob store to delete it, and returns nothing. If deletion fails, it records a log entry with the error class.

**Call relations**: It is used by `_shared_preview`, `_staged_share`, and `share_file_handler` whenever an upload or database recording step fails after a blob may already have been created.

*Call graph*: called by 3 (_shared_preview, _staged_share, share_file_handler); 1 external calls (log).


##### `_shared_preview`  (lines 647–712)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str, artifact_id: UUID, recorded: bool) -> ArtifactPreview | None
```

**Purpose**: Creates a preview image for certain shared document types, so users can see a visual thumbnail instead of only a filename. If preview creation fails, the original file share still succeeds.

**Data flow**: It receives the sandbox file path, safe filename, artifact id, and whether the preview was already recorded. If the file type is previewable and the blob store can accept presigned uploads, it asks the preview service to render the first page and store a PNG, then returns preview metadata. On refusal, timeout, or bad output, it logs the issue, cleans up when needed, and returns no preview.

**Call relations**: It is called by `_staged_share` after the main artifact file has been stored. It may call `_discard_artifact` to remove an unused preview blob after a failed render.

*Call graph*: calls 1 internal fn (_discard_artifact); called by 1 (_staged_share); 7 external calls (__init__, dumps, loads, PurePosixPath, quote, log, shell_path).


##### `_packed_directory`  (lines 732–761)

```
async def _packed_directory(ctx: ToolContext, scoped: str) -> str | None
```

**Purpose**: Turns a shared directory into a `.tar.gz` archive before upload. This lets an agent share a folder directly without manually creating an archive first.

**Data flow**: It receives a sandbox path. It first checks whether the path is a real directory and not a symbolic link. If not, it returns nothing. If it is a directory, it creates an archive in the tool output area and returns the archive path.

**Call relations**: It is called by `_staged_share` before file measurement. A regular file simply skips this step; a directory becomes the file that later preflight, upload, and database recording use.

*Call graph*: called by 1 (_staged_share); 3 external calls (quote, shell_path, uuid4).


##### `_share_request_fingerprint`  (lines 764–766)

```
def _share_request_fingerprint(spec: SharedFileSpec) -> str
```

**Purpose**: Creates a stable fingerprint for one share request. This helps the system recognize a repeated request and make sure an idempotency key is not reused for a different file request.

**Data flow**: It receives a shared-file specification, converts it into sorted compact JSON, hashes that JSON with SHA-256, and returns the digest as a string.

**Call relations**: It is used by `_recorded_share` to compare a stored share with the current request, and by `_staged_share` when creating metadata for a new share.

*Call graph*: called by 2 (_recorded_share, _staged_share); 3 external calls (model_dump, sha256, dumps).


##### `_recorded_share`  (lines 769–821)

```
async def _recorded_share(ctx: ToolContext, spec: SharedFileSpec, artifact_id: UUID) -> _StagedShare | None
```

**Purpose**: Checks whether this share request has already been recorded for the current turn and artifact id. This supports safe retries without uploading and recording duplicate files.

**Data flow**: It receives the tool context, requested file spec, and artifact id. It queries the database for an existing shared artifact row, verifies the request fingerprint and content identity, reconstructs any preview metadata, and returns a staged-share record or nothing.

**Call relations**: It is called at the start of `_staged_share`. If it returns an existing record, `_staged_share` can skip packing, measuring, uploading, and previewing.

*Call graph*: calls 1 internal fn (_share_request_fingerprint); called by 1 (_staged_share); 4 external calls (__init__, __init__, select, workspace_tx).


##### `_staged_share`  (lines 824–878)

```
async def _staged_share(ctx: ToolContext, spec: SharedFileSpec, artifact_id: UUID) -> _StagedShare
```

**Purpose**: Prepares one requested file for sharing by putting its bytes in artifact storage and collecting all metadata needed for the database row and final download link.

**Data flow**: It receives the context, one file spec, and an artifact id. It first checks for an already recorded share, confines the path to the workspace, packs directories, measures the file size, digest, and text-likeness, chooses a safe download name, uploads the file, optionally creates a preview, and returns a staged-share record. If upload or preview setup fails after the main blob is created, it cleans up.

**Call relations**: It is called once per file by `share_file_handler`. It coordinates `_recorded_share`, `_packed_directory`, `_store_artifact`, `_shared_preview`, `_share_request_fingerprint`, and `_discard_artifact`.

*Call graph*: calls 6 internal fn (_discard_artifact, _packed_directory, _recorded_share, _share_request_fingerprint, _shared_preview, _store_artifact); called by 1 (share_file_handler); 6 external calls (__init__, loads, guess_type, PurePosixPath, shell_path, workspace_path).


##### `share_file_handler`  (lines 881–985)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares one or more workspace files with the user by storing them as artifacts, recording them in the database, and returning short-lived download URLs. This is the official route for moving produced files out of the sandbox.

**Data flow**: It receives a list of file specs. It stages every file first, then writes database rows in one transaction with timestamps that preserve the requested order. It gathers artifact display names, optionally notifies publishing code, creates expiring URLs, and returns JSON with each file’s link, name, size, digest, and text flag. If anything fails, it deletes blobs created during this attempt.

**Call relations**: This is the handler behind the built-in `share_file` tool. It drives `_staged_share` for each file and uses `_discard_artifact` during failure cleanup.

*Call graph*: calls 2 internal fn (_discard_artifact, _staged_share); 15 external calls (__init__, __init__, gather, publish_artifacts, now, timedelta, dumps, select, workspace_tx, artifact_object_names (+5 more)).


##### `_spawn_handles`  (lines 1000–1006)

```
def _spawn_handles(target: str, turn_id: UUID, moved: bool) -> str
```

**Purpose**: Builds the message returned when a spawned child agent continues in the background. It gives the caller the spawn id and explains how the result will arrive later.

**Data flow**: It receives the target name, child turn id, and whether the spawn moved to the background because a new message arrived. It chooses the right explanatory lead text, creates a small JSON status payload, and returns one combined text message.

**Call relations**: It is called by `spawn_handler` when a spawn has no immediate output because it is still running in the background.

*Call graph*: called by 1 (spawn_handler); 1 external calls (dumps).


##### `spawn_handler`  (lines 1009–1052)

```
async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult
```

**Purpose**: Delegates a typed subtask to a child agent or subagent profile. It can wait for a validated result, start the child in the background, or move the child to the background if the main conversation needs attention.

**Data flow**: It receives a target, payload, background flag, and display name. It asks the tool context to start the spawn. If the target is unknown, ambiguous, or rejects the payload, it returns a tool error. If the child asks a question, it returns that structured question. If the child is still running, it returns background handles. Otherwise it returns the child’s validated output.

**Call relations**: This is the handler behind the built-in `spawn` tool. It calls the context’s spawn service and uses `_spawn_handles` for background status messages.

*Call graph*: calls 1 internal fn (_spawn_handles); 4 external calls (__init__, __init__, spawn, dumps).


##### `ask_user_handler`  (lines 1060–1071)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Creates a structured question for the agent to ask the user in chat. It tells the agent to include the question in its reply and then stop, so the answer can arrive as the user’s next message.

**Data flow**: It receives title, icon, and question records. It builds a JSON payload marked as awaiting a question, combines it with a plain directive, and returns that as tool output.

**Call relations**: This is the handler behind the built-in `ask_user` tool. It does not contact the user directly; instead it gives the model and chat surface the structured material to present.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 1074–1086)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill bundle and the skills it depends on into the sandbox, then returns the instructions the agent should follow. A skill is a packaged set of workflow notes and supporting files.

**Data flow**: It receives a skill name, asks the skill registry for the full dependency closure, materializes those files, installs them in the sandbox, and returns the combined context text.

**Call relations**: This is the handler behind the built-in `load_skill` tool. It hands off file installation to `load_skills` and context formatting to `loaded_context`.

*Call graph*: 4 external calls (__init__, __init__, load_skills, loaded_context).


##### `_grantee_agent_id`  (lines 1095–1119)

```
async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None
```

**Purpose**: Decides which agent should receive access to a newly connected external account. If no other agent is named, the connection is for the asking agent; if another is named, only the workspace main agent is allowed to do that.

**Data flow**: It receives the context and an agent name. If the name is empty, it returns no override. Otherwise it reads active agents from the database, verifies the asking agent is the main agent, resolves the named target, and returns that target’s id unless it is the asking agent itself.

**Call relations**: It is called by `connect_account_handler` before creating the account-connection request, so the durable request already names the correct grantee.

*Call graph*: called by 1 (connect_account_handler); 2 external calls (select, workspace_tx).


##### `connect_account_handler`  (lines 1122–1136)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private account-connection flow, such as OAuth, for an external provider. OAuth is the common web pattern where a user grants access without pasting their password into chat.

**Data flow**: It receives provider, shared/private choice, and optional target agent name. It requires a speaking member, resolves the grantee agent, validates that the provider exists, creates a connection request, and returns instructions plus structured JSON for the chat surface to render a private connection control.

**Call relations**: This is the handler behind the built-in `connect_account` tool. It relies on `_grantee_agent_id` for agent targeting and the installed connection-flow service for provider validation.

*Call graph*: calls 1 internal fn (_grantee_agent_id); 5 external calls (__init__, __init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 1146–1169)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Requests secret values, such as API keys, through a private prompt instead of chat. This keeps sensitive values out of the conversation transcript.

**Data flow**: It receives a reason and credential prompts. It requires a speaking member, a configured credential key, and admin permission. It seals the workspace, member, and requested slots into a token, builds a credential request, and returns instructions plus structured JSON for a capable surface to collect the secrets privately.

**Call relations**: This is the handler behind the `request_credentials` bound action. It asks the context whether the speaker is an admin and uses the credential storage helper attached to the context to create the sealed request.

*Call graph*: calls 1 internal fn (speaker_is_admin); 4 external calls (__init__, __init__, __init__, __init__).


##### `cancel_spawn_handler`  (lines 1172–1184)

```
async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult
```

**Purpose**: Cancels a child agent run that this turn spawned, or reports its current status if it has already finished. This gives the parent turn a safe way to stop delegated background work.

**Data flow**: It receives a spawn id, verifies spawn control exists in this context, converts the id into a UUID, asks the subagent controller to cancel it, and returns JSON with the spawn id and status.

**Call relations**: This is the handler behind the built-in `cancel_spawn` tool. It uses the same subagent control system that `spawn_handler` creates work through.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_spawn_handler`  (lines 1187–1205)

```
async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a running or waiting child agent. This is how the parent can answer a child’s question or give new instructions without starting over.

**Data flow**: It receives a spawn id and message. It verifies spawn control and an idempotency key are available, converts the spawn id into a UUID, queues the message through the subagent controller, and returns JSON with the resulting status.

**Call relations**: This is the handler behind the built-in `message_spawn` tool. It works with child turns created by `spawn_handler` and uses the subagent workflow to run the follow-up as the child’s next turn.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### `core/src/ufo/runtime/tool_bridge.py`

`orchestration` · `request handling`

A sandbox is an isolated place where agent code can run, but it should not be allowed to call every system tool directly. This file acts like a guarded service window: the sandbox can ask what tools are available, ask for a tool’s input shape, or request that a tool be run. The bridge first checks that the parent turn is still running. Then it checks whether the parent agent or subagent is allowed to see or use the requested tool. For read-only discovery requests, it answers immediately with a list or schema. For an actual tool call, it creates a new conversation and turn in the database, using stable IDs so the same request can be retried without creating duplicates. It records who the call is on behalf of, copies the right conversation context, and stores the tool call as an admitted intent. After that, it asks DBOS, the background workflow system, to run the new turn. Finally, it watches the turn stream until the tool turn finishes, fails, or parks. A parked turn is cancelled and returned as a bridge failure. The important behavior is that this bridge only helps with discovery and dispatch; the admitted turn later re-checks authority under the normal runtime rules.

#### Function details

##### `ToolBridge.request`  (lines 66–100)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is the main entry point for a sandbox bridge request. It answers tool-list and schema requests directly, or turns a real tool call into a queued runtime turn and waits for the result.

**Data flow**: It receives a live sandbox run token and a bridge request. It first looks up the parent turn, checks tool permissions, and either builds a success or failure response right away, or creates and queues a new tool turn. For real tool calls, the final output is the terminal success or failure returned by the newly run turn.

**Call relations**: Sandbox-facing code calls this method when it wants bridge service. It asks `_parent` for the current running turn, uses `_allowed` to decide what may be exposed, calls `_admit` to write a durable tool-turn request, calls `_enqueue` to start that turn, and then waits through `_terminal` for the finished answer.

*Call graph*: calls 5 internal fn (_admit, _allowed, _enqueue, _parent, _terminal); 5 external calls (__init__, __init__, __init__, __init__, __init__).


##### `ToolBridge._parent`  (lines 102–131)

```
async def _parent(self, run: RunToken) -> sa.Row[tuple[object, ...]] | None
```

**Purpose**: This looks up the parent turn that the sandbox request claims to belong to. It makes sure the turn still exists and is still running before any bridge action is allowed.

**Data flow**: It takes the run token, opens a workspace database transaction, and searches for the matching running turn along with its agent and conversation details. It returns that database row when found, or `None` if the parent turn is no longer running.

**Call relations**: `request` calls this first as a safety gate. The rest of the bridge flow depends on the row it returns, because permission checks and child turn creation need the parent agent, conversation, audience, and subagent information.

*Call graph*: called by 1 (request); 2 external calls (select, workspace_tx).


##### `ToolBridge._allowed`  (lines 133–145)

```
def _allowed(self, parent: sa.Row[tuple[object, ...]], tool: ToolDef) -> bool
```

**Purpose**: This decides whether a particular tool should be visible or callable from this parent turn. It applies the agent’s tool grants, subagent profile rules, and special action-tool rules.

**Data flow**: It receives the parent turn row and a tool definition. It compares the tool name against the parent agent’s tools, subagent profile tools, implied grants, and special object-action permissions. It returns `True` if the tool is available and `False` if it should be hidden or rejected.

**Call relations**: `request` calls this while listing tools, fetching a schema, or running a tool. When the tool is action-related, it delegates to `_any_action_granted` to answer the narrower question of whether any bound action is available.

*Call graph*: calls 1 internal fn (_any_action_granted); called by 1 (request); 1 external calls (with_implied_grants).


##### `ToolBridge._any_action_granted`  (lines 147–163)

```
def _any_action_granted(self, parent: sa.Row[tuple[object, ...]]) -> bool
```

**Purpose**: This checks whether the parent agent or subagent has access to at least one configured object action. Object actions are special tools tied to named actions on runtime objects.

**Data flow**: It reads the bridge’s bound action registry and the parent turn’s agent or subagent grants. It expands those grants with implied permissions, compares them to each action’s canonical identifier and defaults, and returns whether any usable action exists.

**Call relations**: `_allowed` calls this when deciding whether to expose the object-action tool or related read tools. Its answer lets the bridge avoid advertising action tools when there is no action the caller could actually use.

*Call graph*: called by 1 (_allowed); 1 external calls (with_implied_grants).


##### `ToolBridge._admit`  (lines 165–261)

```
async def _admit(self, run: RunToken, parent: sa.Row[tuple[object, ...]], request: ToolBridgeRequest) -> tuple[UUID, UUID] | None
```

**Purpose**: This records a requested tool call as a real queued turn in the database. It is the durability step: once admitted, the tool call has a stable identity and can be retried safely.

**Data flow**: It receives the run token, parent turn row, and bridge request. It derives a deterministic conversation ID and turn ID from the workspace, parent turn, and request ID; builds a tool-call intent; verifies the parent turn is still running under a database lock; inserts the child conversation and turn if they do not already exist; checks that an existing request ID was not reused for different data; and marks the turn as ready for dispatch. It returns the new turn and conversation IDs, or `None` if the parent stopped running before admission.

**Call relations**: `request` calls this only for real tool execution, after permission checks pass. It relies on database transactions, authority-member lookup, trace context capture, and turn ID generation. Its output is handed to `_enqueue`, which starts the background workflow for the admitted turn.

*Call graph*: called by 1 (request); 9 external calls (__init__, TypeAdapter, select, update, workspace_tx, current_traceparent, authority_member_id, turn_id_for, uuid5).


##### `ToolBridge._enqueue`  (lines 263–291)

```
async def _enqueue(self, workspace_id: UUID, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This asks the background workflow system to run the newly admitted tool turn. If enqueueing is interrupted or fails, it clears the dispatch marker so another dispatcher can try later.

**Data flow**: It receives the workspace ID, turn ID, and conversation ID. It builds DBOS enqueue options for the express turn queue and calls the asynchronous enqueue API. On cancellation or ordinary failure, it updates the database to remove the recorded enqueue timestamp for still-queued turns; on ordinary failure it also logs that dispatch was deferred.

**Call relations**: `request` calls this after `_admit` creates the child turn. A successful call starts the turn workflow; a failed call does not lose the tool request, because the database state is reset so later dispatch can pick it up.

*Call graph*: called by 1 (request); 3 external calls (update, workspace_tx, log).


##### `ToolBridge._terminal`  (lines 293–302)

```
async def _terminal(self, turn_id: UUID) -> ToolBridgeResponse
```

**Purpose**: This waits for the tool turn to finish and converts its final event into a bridge response. It also cancels a turn if it becomes parked, meaning it stopped waiting for some outside continuation instead of finishing normally.

**Data flow**: It receives a turn ID and opens a live tail of that turn’s events. As frames arrive, it returns a parsed response when it sees a terminal frame, or cancels and returns a failure when it sees a parked frame. If the stream ends without a terminal event, it raises an error because the bridge cannot know the result.

**Call relations**: `request` calls this after enqueueing the child turn. It hands terminal frames to `_response` for success-or-failure formatting, and it calls the turn cancellation helper when a parked turn cannot be used as a synchronous bridge answer.

*Call graph*: calls 1 internal fn (_response); called by 1 (request); 2 external calls (__init__, cancel_one_turn).


##### `ToolBridge._response`  (lines 304–316)

```
def _response(self, terminal: TerminalFrame) -> ToolBridgeResponse
```

**Purpose**: This converts a completed turn’s terminal record into the response shape expected by the sandbox bridge. It separates successful JSON results from failures with useful error text.

**Data flow**: It receives the terminal frame from a finished turn. If the turn status is not `done`, it builds a failure message from the recorded error class, error message, or text. If the turn succeeded, it tries to parse the terminal text as JSON; if that is not valid JSON, it keeps the raw text, validates that the result is JSON-compatible, and returns a success response.

**Call relations**: `_terminal` calls this when the tailed turn reaches its final state. This is the last translation step before `request` returns the result to the sandbox caller.

*Call graph*: called by 1 (_terminal); 4 external calls (__init__, __init__, loads, TypeAdapter).


### Persistent REPL tools
The REPL extension package exposes persistent JavaScript and Python execution tools for exploratory coding and data work.

### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the `ufo_ext_repl` folder should be treated as an importable package. In everyday terms, it is like a label on a folder that says, “the files inside here belong together and can be referenced as one named module.”

There is no executable code here, no setup work, and no functions or classes. That is intentional. The actual REPL extension behavior lives in other files in this package. Without this file, some Python tooling or older import systems might not reliably recognize the directory as a package, which could make imports fail or behave inconsistently.


### `extensions/repl/ufo_ext_repl/manifest.py`

`domain_logic` · `tool registration and tool request handling`

This file is the front door for a reusable code workspace. A REPL, short for “read-evaluate-print loop,” is like a notebook: you run one piece of code, then the next piece can build on what worked before. This file provides two such notebooks inside the project sandbox: `js_repl` for Node.js JavaScript, including browser work such as Playwright, and `xlsx_repl` for Python spreadsheet work with openpyxl.

The important safety rule is that state is saved only when a run exits successfully. If a code block fails or times out, its variables and imports are not added to future runs. This prevents one broken experiment from poisoning the rest of the session. A reset can also wipe the saved state on purpose.

For each call, the file builds a temporary “candidate” script by joining the saved successful code with the new code. It writes that script into the sandbox, runs it as a background-capable task, records a metric about the exit code, and then returns stdout, stderr, and success or failure. If the run takes too long, the process may keep going in the background and the caller gets task handles to inspect it later.

The JavaScript side has extra support for images. It injects an `emitImage` helper into the run, reads back a small rolling list of emitted base64 images, and includes them in the tool result. The file also registers data-related skills that the agent can load when needed.

#### Function details

##### `_meter_run`  (lines 71–87)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one monitoring count for each REPL interpreter run, tagged with which tool ran and how the process ended. This matters because a tool can fail without raising a Python exception, and the exit code tells operators whether the problem was likely user code, a missing interpreter, or something else.

**Data flow**: It receives the current tool context, the tool name, and the interpreter exit code. It converts uncommon exit codes into a shared “other” bucket so metrics do not explode into too many labels, adds the current profile information, and sends the count to the observability system. It returns nothing and only changes monitoring data outside the function.

**Call relations**: After `js_repl` or `xlsx_repl` finishes running code, they call this function before shaping the final answer. It hands the final metric event to the observability helpers, using `turn_profile` to describe the current run context and `emit_metric` to publish the count.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 90–109)

```
def global_modules_link(directory: str, roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds the shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This is needed because ES modules in Node.js do not use `NODE_PATH` the way older module loading often did, so bare imports like Playwright may not resolve unless links are created.

**Data flow**: It receives a sandbox directory and a list of possible global Node module roots. It returns a shell script string that creates a local `node_modules` directory, removes a stale whole-directory symlink if needed, and symlinks each available global package into that local directory. It does not run the command itself.

**Call relations**: `js_repl` asks this function for the setup command just before launching Node. The returned command uses `shell_path` to quote paths safely, and `js_repl` then runs it in the sandbox so JavaScript imports can find installed packages.

*Call graph*: called by 1 (js_repl); 1 external calls (shell_path).


##### `js_emit_relative`  (lines 116–124)

```
def js_emit_relative(call: str) -> str
```

**Purpose**: Creates the per-call workspace path where JavaScript-emitted images will be written. Each call gets its own file so an old long-running task cannot accidentally overwrite or mix images into a later call’s result.

**Data flow**: It receives a short call identifier string. It combines that identifier with the REPL state directory and returns a relative path like a named image log file. It does not touch the filesystem.

**Call relations**: `js_repl` calls this near the start of each JavaScript execution. The returned path is then used by `js_emit_prelude` to tell JavaScript where to write images, and later by `_emitted_images` to read those images back.

*Call graph*: called by 1 (js_repl).


##### `js_emit_prelude`  (lines 127–164)

```
def js_emit_prelude(emit_relative: str) -> str
```

**Purpose**: Generates the JavaScript code that defines `emitImage`, a helper the user’s JavaScript can call to send images back in the tool result. This turns browser screenshots or generated plots into inline output instead of forcing the caller to hunt through files.

**Data flow**: It receives the image log path for this one call. It returns a JavaScript source string that imports file-writing helpers, defines size and count limits, accepts images as buffers, byte arrays, base64 strings, or small objects, and writes recent images as JSON lines. Nothing is executed here; it only creates code to be prepended to the user’s code.

**Call relations**: `js_repl` calls this before writing the temporary Node.js run file. It uses `json.dumps` to safely embed the path into JavaScript source, then `js_repl` places the generated prelude before the candidate code so the user can call `emitImage` during execution.

*Call graph*: called by 1 (js_repl); 1 external calls (dumps).


##### `_candidate_source`  (lines 232–240)

```
async def _candidate_source(ctx: ToolContext, relative: str, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be run for a REPL call. It preserves notebook-like behavior by combining previously successful code with the new code, unless the caller asks to reset.

**Data flow**: It receives the sandbox context, a relative state-file name, the concrete sandbox path for that file, the new code, and a reset flag. If reset is true, it deletes the saved state file. If there is no saved state after that, it returns just the new code with a final newline. Otherwise, it reads the saved state and appends the new code, returning the combined source. It may change the sandbox by deleting a state file.

**Call relations**: Both `js_repl` and `xlsx_repl` call this before running an interpreter. It uses the sandbox to check and read files, and uses `shell_path` when asking the sandbox shell to remove or read the saved state.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (shell_path).


##### `_repl_result`  (lines 243–255)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Turns a completed interpreter run into the standard tool response. It includes the text output, error output, exit code, and, for JavaScript, any images that were emitted.

**Data flow**: It receives stdout, stderr, an exit code, and optionally image objects. It packages stdout, stderr, and the exit code into JSON text. If the exit code is not zero, it also adds a notice explaining that the REPL state did not advance. It returns a `ToolResult` marked as an error when the run failed.

**Call relations**: `js_repl` and `xlsx_repl` call this when the interpreter actually finishes before the timeout. It creates `TextContent` for the JSON summary, attaches any JavaScript images passed in, and returns the final `ToolResult` to the tool caller.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_expired_result`  (lines 258–274)

```
def _expired_result(run: TaskRun, applied_s: int) -> ToolResult
```

**Purpose**: Creates the tool response for a run that outlasted the caller’s foreground timeout. It explains that the REPL state was not saved and, when possible, gives handles for checking the still-running background task.

**Data flow**: It receives the task run record and the timeout duration that was applied. If there is no process id, it returns an error text saying the wait expired and state stayed unchanged. If the process is still available, it builds a message containing the task id, log path, process id, and the same state-unchanged warning. It returns a `ToolResult`; it does not stop the running process.

**Call relations**: `js_repl` and `xlsx_repl` call this when `run_task` reports that the foreground wait timed out. It relies on `timeout_notice` for the human-readable timeout message and `task_handles` for the follow-up information.

*Call graph*: called by 2 (js_repl, xlsx_repl); 4 external calls (__init__, __init__, task_handles, timeout_notice).


##### `_emitted_images`  (lines 282–296)

```
async def _emitted_images(ctx: ToolContext, emit_relative: str, emit_path: str) -> tuple[ImageContent, ...]
```

**Purpose**: Reads image records written by JavaScript `emitImage` calls and converts them into image content for the tool response. It quietly ignores malformed image lines so one bad record does not break the whole result.

**Data flow**: It receives the tool context, the relative image-log path, and the sandbox path to that log. If the file does not exist, it returns an empty tuple. If it exists, it reads the file, deletes it, keeps only the most recent allowed lines, validates each line as an emitted image, and returns image content objects containing media type and base64 data.

**Call relations**: `js_repl` calls this after a JavaScript run finishes and before building the final result. It reads the file created by the `emitImage` prelude and hands the image objects to `_repl_result` so they appear alongside stdout and stderr.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, shell_path).


##### `js_repl`  (lines 299–323)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs a persistent JavaScript ES-module session inside the sandbox. It is meant for interactive Node.js work such as browser automation, website testing, and producing screenshots or other images.

**Data flow**: It receives the tool context and validated JavaScript input containing code, an optional timeout, and an optional reset flag. It finds the saved-state and temporary-run paths, builds the candidate source, creates a unique image-output file, writes a run file with the image prelude plus user code, links global Node packages into the local module path, and runs Node. If the run times out, it returns timeout information and leaves state unchanged. If the run succeeds, it saves the candidate source as the new state. It returns stdout, stderr, exit code, and any emitted images.

**Call relations**: This is the handler registered for the `js_repl` tool by `manifest`. During a call it coordinates the helper functions: `_candidate_source` builds the code, `js_emit_relative` and `js_emit_prelude` set up images, `global_modules_link` prepares package imports, `run_task` launches Node, `_meter_run` records the outcome, `_expired_result` handles timeouts, `_emitted_images` collects images, and `_repl_result` formats the finished response.

*Call graph*: calls 8 internal fn (_candidate_source, _emitted_images, _expired_result, _meter_run, _repl_result, global_modules_link, js_emit_prelude, js_emit_relative); 3 external calls (shell_path, run_task, uuid4).


##### `xlsx_repl`  (lines 326–341)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs a persistent Python session for spreadsheet work, especially Excel manipulation through openpyxl. It gives the caller a notebook-like place to load workbooks, inspect data, change cells, and return a JSON-printable `result`.

**Data flow**: It receives the tool context and validated Python input containing code, an optional timeout, and an optional reset flag. It builds the candidate source from saved state plus new code, writes a temporary Python file with a footer that prints `result` as JSON when present, and runs Python in the sandbox. If the run times out, state is left unchanged and timeout information is returned. If the run exits successfully, the candidate source becomes the saved state. The final output contains stdout, stderr, and the exit code.

**Call relations**: This is the handler registered for the `xlsx_repl` tool by `manifest`. It uses `_candidate_source` to preserve successful session history, `run_task` to launch Python, `_meter_run` to record the exit code, `_expired_result` for foreground timeouts, and `_repl_result` for normal completed runs.

*Call graph*: calls 4 internal fn (_candidate_source, _expired_result, _meter_run, _repl_result); 2 external calls (shell_path, run_task).


##### `manifest`  (lines 344–364)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system: its name, version, tools, skills, and sandbox internet setting. Without this function, the REPL tools and related data skills would not be advertised for use.

**Data flow**: It takes no input. It constructs two tool definitions, one pointing to `js_repl` with the JavaScript input model and one pointing to `xlsx_repl` with the spreadsheet input model. It also creates skill specifications for the bundled data skills and returns a `Manifest` object with internet access enabled for the sandbox.

**Call relations**: The extension loader calls this when discovering the package. The returned manifest is how the rest of the system learns that `js_repl` and `xlsx_repl` exist, what arguments they accept, what descriptions to show, and which on-demand skills are available.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-credentials-connections` — The stored secrets, connected accounts, grants, and refreshable permissions used to call outside services.
- `reg-egress-policy` — The network access rules that decide which outside hosts sandboxed or connector code may contact.
- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-live-workflow-state` — The shared run-state for active turns, including locks, progress, retry guards, cancellation, and completion markers.
- `reg-host-environment` — The assembled per-turn world given to the agent: prompts, skills, files, model choice, tools, and extension context.
- `reg-tool-catalog` — The shared catalog of tools and the policies that decide which tools may run with which permissions.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-browser-sessions` — The active or reusable Chrome browser sessions, tabs, downloads, and remote-control connections used by agents.
- `reg-connector-brokers` — The shared catalog and runtime state for service connectors, MCP servers, broker accounts, and approved actions.
- `reg-artifact-publication` — The shared state for files, previews, signed downloads, hosted sites, app pages, and published outputs.
- `reg-object-system` — The common address book and audit trail for durable workspace objects such as agents, members, artifacts, and connectors.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-subagent-state` — The parent-child turn links, delegation contracts, spawn identities, and pending result deliveries for helper agents.
- `reg-tool-task-journals` — Persistent journals and handles for long-running tool or command executions so they can be resumed, polled, deduplicated, or cleaned up later.
- `reg-objective-state` — The durable goal/objective records holding plans, steps, evidence, blockers, and progress used by objective tools and background follow-up.
- `reg-egress-policy-cache-generation` — Workspace egress-rule generation counters and proxy cache-invalidation state for refreshed network access decisions.
- `reg-repl-scratchpad-state` — Persistent Python/JavaScript REPL interpreter sessions, variables, scratch files, and execution handles kept across tool calls or turns.
- `reg-debug-problem-reports` — Durable agent/operator problem reports and warnings with workspace, turn, and diagnostic context for later debugging.
- `reg-action-proposal-state` — Durable proposal records for actions or changes that require later review, approval, rejection, or replay.
- `reg-document-review-state` — Saved document-review findings and annotation state files used by PDF, PowerPoint, Word, and spreadsheet automation utilities.
