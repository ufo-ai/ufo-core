# Built-In Runtime Tools and Workspace Files  `stage-10.1`

This stage is shared behind-the-scenes support for the agent’s built-in tools. It is the layer that lets an agent safely act on the real workspace instead of only talking. The main bridge is builtins.py, which offers tools for shell commands, file edits, file sharing, questions to the user, helper agents, skills, and account setup. Each tool runs through a controlled tool context from context.py, so it only gets approved access to sandboxes, files, credentials, billing, and previews.

Several parts keep this safe and understandable. containment.py checks that file paths stay inside allowed folders, even with tricky shortcuts called symlinks. sandbox/protocol.py defines the simple command messages used to run shell or Python work in an isolated sandbox. tasks.py keeps a journal for long-running commands, so users can check, follow, or stop them later. workspace_changes.py records file changes after a turn, while file_changes.py defines a shared path length limit. activity.py turns raw tool calls into friendly user-facing labels. The REPL extension adds persistent JavaScript and Python workbenches, and __init__.py simply identifies the tools package.

## Files in this stage

### Tool Surfaces
Defines the primary built-in and extension tools exposed to agents, with the package marker tying the host tool area together.

### `core/src/ufo/host/tools/builtins.py`

`domain_logic` · `tool execution during an agent turn`

This file is like the agent’s built-in toolbox, with rules printed on every tool handle. Without it, the agent would not have a standard, safe way to inspect workspace files, run commands, delegate work, or hand finished files back to a user.

A key theme is containment. File reads, writes, searches, shell commands, and sharing all go through the sandbox, which is the controlled workspace environment. The file tools also guard against careless edits: a file must be read in the current turn before it can be edited or overwritten, so the agent cannot blindly replace text it has not seen.

The sharing path is especially careful. A file is measured inside the sandbox, uploaded to blob storage without loading the whole file into this Python process, recorded in the database, and returned as a temporary download link. Directories are packed into archives first. If anything fails before the database record is made, uploaded leftovers are cleaned up.

The file also supports longer-running work. Shell commands and spawned subagents can move into the background instead of being killed. Other tools create chat-native handoffs: ask_user asks the member in the conversation, connect_account asks them to use a private OAuth control, and request_credentials collects secrets privately so they never appear in chat.

#### Function details

##### `_bounded_file_path`  (lines 183–186)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the small JSON result envelope used by file-change tools. This prevents a strangely huge path from making tool output too large or malformed.

**Data flow**: It receives a path string, serializes it as JSON to see its real escaped size, and either returns the same path unchanged or raises an error if it is too large.

**Call relations**: It is used as validation for file paths in write and edit input models before those handlers run, so oversized paths are stopped early.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 356–387)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command in the sandbox and reports whether it finished, failed, timed out, or was moved to the background. It lets the agent use command-line tools without giving direct access to the host machine.

**Data flow**: It receives the tool context and a command request. If background mode is requested, it delegates immediately to _bash_background. Otherwise it rejects simple sleep-only waits, starts the command through the task runner, and turns the task result into text output, an error, or background task handles.

**Call relations**: This is the handler behind the built-in bash tool. It calls the task-running helpers for foreground commands and calls _bash_background when the caller wants a detached task from the start.

*Call graph*: calls 1 internal fn (_bash_background); 7 external calls (__init__, __init__, format, flat_sleeps, run_task, task_handles, timeout_notice).


##### `_bash_background`  (lines 390–404)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command as a detached background task and returns the information needed to watch it later. It is used when the agent wants work to continue while the turn moves on.

**Data flow**: It creates a task id and task directory, asks the sandbox to launch the command detached, then returns the task id, process id, and log locations. If the command cannot detach, it returns an error result.

**Call relations**: bash_handler calls this when the bash input asks for background execution. It shares the same task-handle format used for foreground commands that outlive their waiting time.

*Call graph*: called by 1 (bash_handler); 4 external calls (__init__, __init__, task_handles, task_id).


##### `_require_str`  (lines 407–410)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Confirms that a value from a sandbox file-read result is a real non-empty string. It keeps malformed image or document results from being passed on silently.

**Data flow**: It receives an unknown value and the name of the field being checked. If the value is a non-empty string, it returns it; otherwise it raises an error explaining which field was missing.

**Call relations**: read_handler and _document_result use it when building image content blocks, where media type and encoded image data must be present.

*Call graph*: called by 2 (_document_result, read_handler).


##### `_document_result`  (lines 413–456)

```
def _document_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns the sandbox’s structured document-read result into the tool response an agent can see. It combines extracted text, page or slide images, and helpful paging notes.

**Data flow**: It receives a dictionary describing a PDF, slide deck, word-processing file, or spreadsheet. It validates the document metadata, builds text notes and image blocks, and returns a ToolResult containing those blocks.

**Call relations**: read_handler calls this when the sandbox says the file is a document type. It relies on _require_str to make sure rendered page images are complete before returning them.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 459–496)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox and returns an appropriate result for text, images, and common document formats. It also records that the path was read, which later permits safe edits or overwrites.

**Data flow**: It receives a file path plus optional offset and limit. It asks the sandbox file tool to read the file, adds the path to the turn’s read set, and converts the response into text content, image content, document content, or an empty-file message.

**Call relations**: This is the handler behind the built-in read tool. It calls _document_result for document files and _require_str for image fields.

*Call graph*: calls 2 internal fn (_document_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 499–521)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Writes text to a workspace file while enforcing the rule that existing files must have been read first. This protects against overwriting content the agent has not inspected during the turn.

**Data flow**: It receives a target path and text content. It writes the bytes to a temporary staged file in the sandbox, asks the sandbox file tool to install it at the requested path, adds size and line-count details, records the path as read, and returns a bounded summary.

**Call relations**: This is the handler behind the built-in write tool. It uses _file_tool_result to keep the returned summary within the tool output limit.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (uuid4).


##### `edit_handler`  (lines 524–537)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to a file, but only after the file has been read in the current turn. This makes edits deliberate instead of blind.

**Data flow**: It receives a path and one or more replacement instructions. It refuses the edit if the path is not in the turn’s read set, encodes the old and new strings safely, sends the edit request to the sandbox file tool, and returns a bounded summary.

**Call relations**: This is the handler behind the built-in edit tool. It calls _file_tool_result after the sandbox has applied, or refused, the edit.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 540–554)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats the result of a write or edit so it stays small enough to return safely. It reports what happened without sending back a potentially huge diff or full file content.

**Data flow**: It receives a result dictionary, serializes it to compact JSON, and returns it if it fits. If it is too large, it removes the snippet and shortens the message before trying again; if it still cannot fit, it raises an error.

**Call relations**: write_handler and edit_handler both use this helper after the sandbox file command finishes.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 557–563)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files whose paths match a glob pattern, such as **/*.py. The search happens inside the sandbox so only the bounded list of matches comes back.

**Data flow**: It receives a pattern and optional directory. It asks the sandbox file tool to search from that directory, or from the workspace root by default, then returns the result as JSON text.

**Call relations**: This is the handler behind the built-in glob tool. It delegates the actual filesystem walk to the sandbox’s ufo fs command.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 566–584)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents for a regular expression, which is a pattern language for matching text. It keeps large searches inside the sandbox and returns only a capped result.

**Data flow**: It receives the search pattern plus optional path, file filter, context lines, case handling, output mode, and result limit. It builds sandbox search parameters, applies a default head limit when none is supplied, and returns the sandbox result as JSON text.

**Call relations**: This is the handler behind the built-in grep tool. It delegates the heavy content search to the sandbox file command rather than using host-side file access.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 587–621)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies a staged shared file into the artifact store, either by direct streaming or by a tightly limited S3 upload. It is the low-level step that makes a file available for later download.

**Data flow**: It receives the sandbox path, storage key, file size, and SHA-256 digest. For S3, it creates a presigned upload URL bound to that exact size and checksum and tells the sandbox to upload with curl. For filesystem storage, it streams the file from the sandbox into the blob store.

**Call relations**: _staged_share calls this after measuring a file. If this step fails, higher-level sharing code cleans up and does not record the artifact.

*Call graph*: called by 1 (_staged_share); 3 external calls (b64encode, quote, shell_path).


##### `_discard_artifact`  (lines 634–638)

```
async def _discard_artifact(ctx: ToolContext, key: str) -> None
```

**Purpose**: Best-effort cleanup for an artifact blob that should not remain stored. It prevents failed share attempts from leaving orphaned files behind when possible.

**Data flow**: It receives a blob key and asks the blob store to delete it. If deletion itself fails, it logs the cleanup failure instead of hiding the original problem.

**Call relations**: _shared_preview, _staged_share, and share_file_handler call this when preview creation, staging, or database recording fails after a blob may already have been created.

*Call graph*: called by 3 (_shared_preview, _staged_share, share_file_handler); 1 external calls (log).


##### `_shared_preview`  (lines 641–706)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str, artifact_id: UUID, recorded: bool) -> ArtifactPreview | None
```

**Purpose**: Creates a small preview image for shareable document formats, when the storage backend supports it. This lets users see a visual preview of a shared document instead of only a filename.

**Data flow**: It receives the staged file path, safe filename, artifact id, and whether a database record already exists. If the file type is previewable and S3 is available, it sends the file to the preview service, lets that service upload a PNG to blob storage, parses the returned size, and returns preview metadata. On failure it logs the reason and returns no preview.

**Call relations**: _staged_share calls this after the main artifact is stored. It calls _discard_artifact if a newly created preview blob must be removed after a failed render.

*Call graph*: calls 1 internal fn (_discard_artifact); called by 1 (_staged_share); 7 external calls (__init__, dumps, loads, PurePosixPath, quote, log, shell_path).


##### `_packed_directory`  (lines 726–755)

```
async def _packed_directory(ctx: ToolContext, scoped: str) -> str | None
```

**Purpose**: Turns a directory selected for sharing into a .tar.gz archive inside the sandbox. This lets the rest of the sharing code treat both files and directories as a single file to measure and upload.

**Data flow**: It receives a sandbox-scoped path. It first checks whether that path is a real directory and not a symbolic link; if not, it returns None. If it is a directory, it creates a temporary archive in the tool output area and returns the archive path.

**Call relations**: _staged_share calls this before measuring a share request. A regular file continues directly to preflight measurement, while a directory’s archive becomes the file that is shared.

*Call graph*: called by 1 (_staged_share); 3 external calls (quote, shell_path, uuid4).


##### `_share_request_fingerprint`  (lines 758–760)

```
def _share_request_fingerprint(spec: SharedFileSpec) -> str
```

**Purpose**: Creates a stable fingerprint for a share request. This helps repeated calls with the same idempotency key reuse the same artifact safely, while detecting if the request changed.

**Data flow**: It receives a SharedFileSpec, converts it to sorted compact JSON, hashes that JSON with SHA-256, and returns the digest string.

**Call relations**: _recorded_share uses it to verify that an existing idempotent record matches the current request, and _staged_share stores it with new staged shares.

*Call graph*: called by 2 (_recorded_share, _staged_share); 3 external calls (model_dump, sha256, dumps).


##### `_recorded_share`  (lines 763–815)

```
async def _recorded_share(ctx: ToolContext, spec: SharedFileSpec, artifact_id: UUID) -> _StagedShare | None
```

**Purpose**: Looks for an already-recorded artifact for the same turn and idempotency-derived artifact id. This prevents duplicate uploads and duplicate database rows when a share call is retried.

**Data flow**: It receives the tool context, share request, and artifact id. It queries the workspace database for a matching shared artifact row, checks that the saved fingerprint matches the current request, reconstructs preview metadata if present, and returns a staged-share object. If no row exists, it returns None.

**Call relations**: _staged_share calls this first. If it returns a record, staging can skip packing, measuring, uploading, and preview rendering.

*Call graph*: calls 1 internal fn (_share_request_fingerprint); called by 1 (_staged_share); 4 external calls (__init__, __init__, select, workspace_tx).


##### `_staged_share`  (lines 818–872)

```
async def _staged_share(ctx: ToolContext, spec: SharedFileSpec, artifact_id: UUID) -> _StagedShare
```

**Purpose**: Prepares one requested file for sharing by safely naming it, measuring it, uploading it, and optionally creating a preview. It does all of this before any database row is written.

**Data flow**: It receives one file specification and an artifact id. It first checks for an existing recorded share, then scopes the path to the workspace, packs directories if needed, runs a sandbox preflight to get size, digest, and text-ness, chooses a safe download name, stores the file, creates a preview if possible, and returns all metadata needed for database insertion.

**Call relations**: share_file_handler calls this once per requested file. It coordinates _recorded_share, _packed_directory, _store_artifact, _shared_preview, _share_request_fingerprint, and _discard_artifact.

*Call graph*: calls 6 internal fn (_discard_artifact, _packed_directory, _recorded_share, _share_request_fingerprint, _shared_preview, _store_artifact); called by 1 (share_file_handler); 6 external calls (__init__, loads, guess_type, PurePosixPath, shell_path, workspace_path).


##### `share_file_handler`  (lines 875–979)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares one or more workspace files with the user by storing them as artifacts, recording them in the database, and returning temporary download links. This is the controlled exit door for files produced inside the sandbox.

**Data flow**: It receives a list of file specs. It stages every file first, then opens one database transaction to insert shared-artifact rows in the same order the caller provided. If recording succeeds, it optionally publishes artifacts, creates expiring URLs, and returns a JSON list with link, name, object name, size, digest, and text flag. If staging or recording fails, it deletes newly created blobs where possible.

**Call relations**: This is the handler behind the built-in share_file tool. It calls _staged_share for each file and _discard_artifact during failure cleanup.

*Call graph*: calls 2 internal fn (_discard_artifact, _staged_share); 15 external calls (__init__, __init__, gather, publish_artifacts, now, timedelta, dumps, select, workspace_tx, artifact_object_names (+5 more)).


##### `_spawn_handles`  (lines 994–1000)

```
def _spawn_handles(target: str, turn_id: UUID, moved: bool) -> str
```

**Purpose**: Builds the message that tells the agent how to refer to a spawn that is now running in the background. It gives one consistent format whether the spawn was backgrounded deliberately or moved there because a new message arrived.

**Data flow**: It receives the target name, child turn id, and a flag saying why it moved to background. It chooses the right explanation, builds a small JSON payload with spawn id and status, and returns the combined text.

**Call relations**: spawn_handler calls this when a child spawn has not produced immediate output and must be controlled later with message_spawn or cancel_spawn.

*Call graph*: called by 1 (spawn_handler); 1 external calls (dumps).


##### `spawn_handler`  (lines 1003–1046)

```
async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult
```

**Purpose**: Delegates a typed subtask to a child agent or subagent profile. It lets the main agent hand off work while still receiving validated output, a question, or a background handle.

**Data flow**: It receives a target name, payload, background flag, and display name. It asks the tool context to create or run the spawn, handles unknown or ambiguous targets as recoverable errors, and returns either the child’s output, a structured question, or background spawn handles.

**Call relations**: This is the handler behind the built-in spawn tool. It calls _spawn_handles when the child continues in the background.

*Call graph*: calls 1 internal fn (_spawn_handles); 4 external calls (__init__, __init__, spawn, dumps).


##### `ask_user_handler`  (lines 1054–1065)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Creates a structured question for the agent to ask the member in chat. It supports the pattern where the agent asks in its reply, ends the turn, and receives the answer as the next member message.

**Data flow**: It receives a title, optional icon, and one or more question records. It builds a JSON payload describing the pending question and returns it with an instruction telling the agent to ask and then stop.

**Call relations**: This is the handler behind the built-in ask_user tool. Rich chat surfaces can read the structured payload, while the agent also receives plain guidance for what to do next.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 1068–1080)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill and the skills it depends on into the sandbox, then returns their instructions. Skills are reusable bundles of workflow guidance and files.

**Data flow**: It receives a skill name. It asks the skill system for the dependency closure, materializes the needed files, installs them in the sandbox, builds the text context describing the loaded workflows and file tree, and returns that text.

**Call relations**: This is the handler behind the built-in load_skill tool. It calls the runtime skill-loading helpers that actually place files and build the returned context.

*Call graph*: 4 external calls (__init__, __init__, load_skills, loaded_context).


##### `_grantee_agent_id`  (lines 1089–1113)

```
async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None
```

**Purpose**: Figures out whether an external account connection should be granted to another agent, and checks whether the asking agent is allowed to do that. Only the workspace’s main agent may grant a connection for a different agent.

**Data flow**: It receives the tool context and an agent name. If no name was provided, it returns None, meaning the connection is for the asking agent. Otherwise it loads active agents from the database, verifies the current agent is the main one, resolves the named target, and returns that target’s id when it differs from the current agent.

**Call relations**: connect_account_handler calls this before creating the connection request, so the durable request already names the correct grantee.

*Call graph*: called by 1 (connect_account_handler); 2 external calls (select, workspace_tx).


##### `connect_account_handler`  (lines 1116–1130)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private OAuth account-connection handoff for the speaking member. OAuth is the common web flow where a user authorizes access to an external service without sharing their password in chat.

**Data flow**: It receives the provider name, sharing choice, and optional target agent name. It requires a speaking member, resolves any target agent, validates that the provider is installed, builds a ConnectRequest, and returns it with instructions telling the agent to direct the member to the private connection control.

**Call relations**: This is the handler behind the built-in connect_account tool. It calls _grantee_agent_id for agent-targeting rules and the installed connect flow to validate the provider.

*Call graph*: calls 1 internal fn (_grantee_agent_id); 5 external calls (__init__, __init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 1140–1163)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks an admin member to enter secret values, such as API keys, through a private prompt instead of chat. This keeps secrets out of the conversation transcript.

**Data flow**: It receives a reason and a small list of credential prompts. It requires a speaking member, checks that credential storage is configured, verifies the speaker is an admin, seals the requested slot list into a signed token, builds a CredentialRequest, and returns it with instructions to end the turn.

**Call relations**: This is the handler for the request_credentials bound action. It uses the tool context’s admin check and credential sealer before handing the structured request to the chat surface.

*Call graph*: calls 1 internal fn (speaker_is_admin); 4 external calls (__init__, __init__, __init__, __init__).


##### `cancel_spawn_handler`  (lines 1166–1178)

```
async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult
```

**Purpose**: Cancels a child spawn that this turn started, or reports its existing status if it already finished. It gives the agent a way to stop delegated background work.

**Data flow**: It receives a spawn id string. It checks that spawn control is available, converts the id to a UUID, asks the subagent workflow to cancel it, and returns JSON with the spawn id and current status.

**Call relations**: This is the handler behind the built-in cancel_spawn tool. It talks to ctx.subagents, the same subagent control system used by spawn_handler.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_spawn_handler`  (lines 1181–1199)

```
async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a running or waiting child spawn. This is how the parent agent can answer a child’s question or give it new instructions.

**Data flow**: It receives a spawn id and message. It checks that spawn control and an idempotency key are available, converts the id to a UUID, queues the message as the spawn’s next turn, and returns JSON with the spawn id and status.

**Call relations**: This is the handler behind the built-in message_spawn tool. It uses ctx.subagents to continue a spawn that was created by spawn_handler and makes the follow-up deliver its result back to the conversation.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### `extensions/repl/ufo_ext_repl/manifest.py`

`orchestration` · `extension load and tool request handling`

This file is the entry point for the REPL extension. A REPL is an interactive coding workspace: like a notebook, each successful cell becomes part of the ongoing session. The important rule here is safety through replay: new code is appended to the saved session only if it exits successfully. If the code fails or times out, the saved state is left untouched, so the next call starts from the last known-good point.

The JavaScript tool writes a temporary Node.js ES module file, adds a small prelude that defines emitImage, links globally installed Node packages so imports can work, and runs the file inside the project sandbox. Images sent through emitImage are written to a per-call file, then read back into the tool result. The per-call file matters because a timed-out browser script may keep running in the background and should not overwrite images from a later call.

The Excel tool does the same persistent-state pattern for Python. It appends a footer that prints a JSON version of result if the user defines it, which gives a simple way to return structured data. Both tools use detached task running, so long-running code can continue after the caller stops waiting, and the result can include task handles for checking on it later.

#### Function details

##### `_meter_run`  (lines 71–87)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one monitoring count for a REPL run, including which tool ran and how the process ended. This helps operators tell the difference between user code failing and the interpreter itself being missing or broken.

**Data flow**: It receives the current tool context, the tool name, and the process exit code. It groups unusual exit codes into a shared “other” bucket, adds the current profile from the turn context, and sends a metric named repl_run_total. It does not return a value; its effect is the emitted monitoring data.

**Call relations**: After js_repl or xlsx_repl runs code, they call _meter_run before deciding how to report the result. _meter_run hands the final counting work to emit_metric and uses turn_profile to label the metric with the current agent profile.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 90–109)

```
def global_modules_link(directory: str, roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds a shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This is needed because ES modules do not automatically look at NODE_PATH, so bare imports like installed package names would otherwise fail.

**Data flow**: It receives a workspace directory and a list of possible global module roots. It returns a shell command string that creates a node_modules folder, removes an old whole-folder symlink if present, and symlinks each available package into that folder. It only creates the command; js_repl later runs it in the sandbox.

**Call relations**: js_repl calls this before starting Node. The function uses shell_path to quote paths safely for the shell, then js_repl executes the returned command so package imports can resolve during the run.

*Call graph*: called by 1 (js_repl); 1 external calls (shell_path).


##### `js_emit_relative`  (lines 116–124)

```
def js_emit_relative(call: str) -> str
```

**Purpose**: Chooses the workspace-relative file name where one JavaScript REPL call will write emitted images. Each call gets its own file so an old, still-running task cannot mix its images into a newer call’s output.

**Data flow**: It receives a short call identifier. It combines that identifier with the REPL state directory and returns a relative JSON-lines file path for image output. It does not touch the filesystem.

**Call relations**: js_repl creates a fresh identifier and calls js_emit_relative before building the JavaScript run file. The returned path is then passed into js_emit_prelude and later used by _emitted_images to read the images back.

*Call graph*: called by 1 (js_repl).


##### `js_emit_prelude`  (lines 127–164)

```
def js_emit_prelude(emit_relative: str) -> str
```

**Purpose**: Generates the JavaScript setup code that defines globalThis.emitImage for the REPL run. emitImage lets user code return images inline as part of the tool result instead of only as text.

**Data flow**: It receives the image-output path for this call. It returns JavaScript source code that imports file-writing helpers, defines size and count limits, accepts image bytes or base64 text, stores a rolling window of recent images, and writes them as JSON lines. The returned code is prepended to the user’s JavaScript before Node runs it.

**Call relations**: js_repl calls js_emit_prelude while preparing the temporary run file. The generated prelude writes image records, and _emitted_images later reads those records and converts valid ones into ImageContent objects.

*Call graph*: called by 1 (js_repl); 1 external calls (dumps).


##### `_candidate_source`  (lines 232–240)

```
async def _candidate_source(ctx: ToolContext, relative: str, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be run for the next REPL call. It combines the last saved successful state with the new code, unless the caller asked for a reset.

**Data flow**: It receives the sandbox context, a relative state-file name, the full sandbox path, the new code, and a reset flag. If reset is true, it deletes the saved state file. If there is no saved state, it returns just the new code plus a newline. If saved state exists, it reads that file and appends the new code. The returned text is a candidate state: it is only saved permanently after a successful run.

**Call relations**: Both js_repl and xlsx_repl call _candidate_source before writing their temporary run files. It uses shell_path when deleting or reading the saved state through the sandbox shell.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (shell_path).


##### `_repl_result`  (lines 243–255)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Turns a completed interpreter run into the standard tool result returned to the caller. If the run failed, it clearly says that the REPL state did not advance.

**Data flow**: It receives stdout, stderr, an exit code, and optionally images. It packages the text output, error output, and exit code as JSON text. For nonzero exit codes, it adds a notice explaining that the failed code was not committed. It returns a ToolResult marked as an error when the exit code is nonzero, with any images attached after the text.

**Call relations**: js_repl and xlsx_repl call _repl_result after a run finishes without a foreground timeout. _repl_result uses TextContent to hold the JSON message and ToolResult to wrap the final response.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_expired_result`  (lines 258–274)

```
def _expired_result(run: TaskRun, applied_s: int) -> ToolResult
```

**Purpose**: Creates the tool result for a run that outlasted the caller’s timeout. It explains that the saved REPL state is unchanged and, when possible, gives handles for checking the still-running task later.

**Data flow**: It receives the task run record and the timeout that was actually applied. If there is no process id, it returns an error message saying the wait expired and the state did not advance. If the process is still known, it returns task handles such as task id, log path, and process id, plus the same state-unchanged note. The result itself is a ToolResult.

**Call relations**: js_repl and xlsx_repl call _expired_result when run_task reports that the foreground wait timed out. It delegates wording to timeout_notice when there is no surviving process and to task_handles when there is one.

*Call graph*: called by 2 (js_repl, xlsx_repl); 4 external calls (__init__, __init__, task_handles, timeout_notice).


##### `_emitted_images`  (lines 282–296)

```
async def _emitted_images(ctx: ToolContext, emit_relative: str, emit_path: str) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code sent through emitImage and converts them into image objects for the tool response. It also removes the temporary image file after reading it.

**Data flow**: It receives the sandbox context, the relative image file name, and its full sandbox path. If the file does not exist, it returns an empty tuple. If it exists, it reads the JSON-lines content, deletes the file, validates up to the latest allowed number of image records, skips malformed lines, and returns ImageContent objects containing media type and base64 data.

**Call relations**: Only js_repl calls _emitted_images, after Node finishes and before building the final result. The image file it reads was created by the JavaScript code generated by js_emit_prelude.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, shell_path).


##### `js_repl`  (lines 299–323)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs one JavaScript REPL call in the sandbox while preserving successful state across calls. It supports top-level await, linked Node packages, foreground timeouts, background continuation, and inline image output.

**Data flow**: It receives the tool context and validated JavaScript input containing code, optional timeout, and optional reset. It finds the sandbox paths, builds the candidate source from prior state plus new code, creates a unique image-output path, writes a temporary .mjs run file with the image prelude and candidate source, links global Node modules, and runs Node through run_task. If the run times out, it returns an expired result and does not save state. If it exits successfully, it saves the candidate as the new persistent state. It returns stdout, stderr, exit code, and any emitted images.

**Call relations**: This is the handler registered for the js_repl tool by manifest. It coordinates helper functions: _candidate_source prepares replayable code, js_emit_relative and js_emit_prelude set up image capture, global_modules_link prepares imports, _meter_run records the outcome, _expired_result reports timeouts, _emitted_images gathers images, and _repl_result formats the final response.

*Call graph*: calls 8 internal fn (_candidate_source, _emitted_images, _expired_result, _meter_run, _repl_result, global_modules_link, js_emit_prelude, js_emit_relative); 3 external calls (shell_path, run_task, uuid4).


##### `xlsx_repl`  (lines 326–341)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs one Python REPL call for Excel and data work while preserving successful state across calls. It gives users a simple convention: assign to result to print a JSON-friendly value back to the caller.

**Data flow**: It receives the tool context and validated Python input containing code, optional timeout, and optional reset. It builds the candidate source from saved state plus new code, writes a temporary Python run file with a footer that prints result if present, and runs it with python3 through run_task. If it times out, it returns timeout information without saving state. If it exits successfully, it saves the candidate source as the new state. It returns stdout, stderr, and the exit code in the standard result format.

**Call relations**: This is the handler registered for the xlsx_repl tool by manifest. It shares the same state and timeout helpers as js_repl: _candidate_source, _meter_run, _expired_result, and _repl_result, while run_task performs the actual sandboxed execution.

*Call graph*: calls 4 internal fn (_candidate_source, _expired_result, _meter_run, _repl_result); 2 external calls (shell_path, run_task).


##### `manifest`  (lines 344–364)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system: its name, version, available tools, loadable skills, and need for sandbox internet access. Without it, the REPL tools and their related data skills would not be advertised to the agent.

**Data flow**: It takes no input. It constructs two ToolDef entries, one for js_repl and one for xlsx_repl, each with a description, input model, and handler function. It also creates SkillSpec entries for the bundled data skills and returns a Manifest object containing all of this metadata.

**Call relations**: The host loads manifest when discovering the extension. The returned Manifest points future JavaScript tool calls to js_repl and future Excel/Python tool calls to xlsx_repl, and it exposes the skills stored under the extension’s skills directory.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `core/src/ufo/host/tools/__init__.py`

`other` · `import time`

This is a package marker file. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package. Here, it does not define any functions, classes, or runtime behavior. Its only content is a short docstring, which is a human-readable note saying that this package contains the project’s tool-related pieces: the registry, the handler context, and the built-in tool set.

In plain terms, this file is like the title page of a folder. It does not do the work itself, but it helps organize the code and tells both Python and human readers that the surrounding directory has a specific purpose. Without it, depending on the Python packaging setup, imports from this folder could be less clear or could fail in environments that expect explicit package markers.


### Safe Execution Context
Provides path containment, sandbox command protocols, and the controlled runtime context that tools use to access system capabilities safely.

### `core/src/ufo/harness/containment.py`

`domain_logic` · `cross-cutting filesystem access`

This file solves a dangerous filesystem problem: a name that looks safe can still point somewhere unsafe after the operating system follows links. A symlink is like a signpost file that redirects one path to another. If an agent can create such a signpost inside its workspace, it might trick the system into reading or overwriting a host file outside that workspace.

The module therefore uses several layers of checking. First it rejects path names that are empty, point at the current directory, or try to climb upward with `..`. Then it resolves the path’s real parent directory and checks that it is inside the allowed root. Next it walks down the directory tree one component at a time using file descriptors, which are operating-system handles to already-open directories. This “pins” the parent directory, like holding the actual folder in your hand instead of trusting a street address that someone could swap. Finally, it checks the target file itself without following a final symlink.

The main public entry points are for safe files, directories, deletion, glob patterns, and simple lexical path cleanup. `ContainedFile` represents a file target whose parent directory has already been pinned. Its methods read, write, rename, delete, and change permissions without letting a last-minute symlink swap redirect the operation.

#### Function details

##### `contained_root`  (lines 88–101)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Checks that a root directory used for containment really exists and is an actual directory, not a symlink. This matters because if the root itself is a symlink, every later “inside the root” check could be fooled into protecting the wrong place.

**Data flow**: It receives a root path → turns it into a `Path` object → looks at the path itself without following symlinks → refuses it if it is missing or not a directory → returns the root’s resolved, real path.

**Call relations**: This is the first checkpoint for `contained_file`, `contained_dir`, and `contained_remove`. Those higher-level operations ask it to prove the root is trustworthy before they inspect or change anything underneath it.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 104–121)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Checks a root directory that came from operator configuration, where a symlinked root is allowed. This supports normal deployment layouts, such as a configured data directory that points to mounted storage.

**Data flow**: It receives a path and the name of the configuration setting → follows symlinks while checking that the final target exists and is a directory → raises an error that names the bad setting if not → returns the real resolved directory.

**Call relations**: Unlike `contained_root`, this helper is for trusted configuration rather than agent-controlled locations. It prepares a safe canonical root for later filesystem operations, while allowing deployment-chosen symlinks at the root itself.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 139–150)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Checks what currently exists at the target file name without following a final symlink. It is used to confirm that the target is either absent or a regular file, not a directory or special filesystem object.

**Data flow**: It uses the pinned parent directory and the target file name → asks the operating system for information about that exact name without following links → returns `None` if nothing is there, returns file metadata if it is a regular file, or raises an error if it is not a regular file.

**Call relations**: Callers use this after `contained_file` has produced a `ContainedFile`. `contained_regular` relies on it to prove that an existing file can safely be passed onward as a path.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 152–167)

```
def mode(self, default: int) -> int
```

**Purpose**: Finds the permission bits to use when overwriting this target. It preserves permissions from an existing regular file, uses a default when the file is missing, and refuses directories.

**Data flow**: It receives a default permission mode → checks the target name inside the pinned parent without following symlinks → returns the existing regular file’s permissions, the default for a missing or symlink-like target, or raises an error for a directory.

**Call relations**: This is meant to be used by code preparing to replace a contained file. It supports the write path by deciding the mode that `replace_bytes` or similar write operations should apply.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 169–179)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained target for safe binary reading. It returns a stream so large files can be read gradually instead of loaded all at once.

**Data flow**: It starts with a validated `ContainedFile` → asks `_open_regular` to open the target safely and prove it is a regular file → wraps the raw file descriptor in a buffered binary reader → returns that reader, closing the descriptor if wrapping fails.

**Call relations**: `read_bytes` calls this when it wants a simple byte result. Other callers can use it directly when they need streaming reads, such as copying out a large artifact.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 181–184)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a requested number of bytes from the contained file. It is the simple read helper for callers that do not need streaming.

**Data flow**: It receives a byte limit → opens the file safely through `open_bytes` → reads at most that many bytes → closes the file automatically and returns the bytes.

**Call relations**: `read_text` builds on this for text reads. It sits above `open_bytes`, so it gets the same symlink-safe opening behavior without duplicating it.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 186–187)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads the contained file as UTF-8 text, replacing invalid characters instead of failing. It is useful when the caller wants human-readable content with a size limit.

**Data flow**: It receives a byte limit → gets bytes from `read_bytes` → decodes them as UTF-8, substituting replacement characters for bad byte sequences → returns a string.

**Call relations**: This is the text-friendly wrapper around `read_bytes`. It depends on the lower-level safe read path rather than opening the file itself.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 189–190)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the permissions on the contained target without following symlinks. It limits the mode to ordinary permission bits.

**Data flow**: It receives a permission mode → masks it down to standard file permission bits → applies it to the target name relative to the pinned parent directory.

**Call relations**: Callers use this after obtaining a `ContainedFile` from `contained_file`. It performs the permission change at the already-validated location.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 192–196)

```
def unlink(self) -> None
```

**Purpose**: Deletes the contained target if it exists. Missing files are treated as already gone, so cleanup code can call it safely.

**Data flow**: It uses the pinned parent directory and target name → asks the operating system to unlink, meaning remove that name → ignores `FileNotFoundError` → leaves the filesystem with the target absent if possible.

**Call relations**: This is a small operation available once `contained_file` has pinned the parent. It is for single-file removal, while `contained_remove` handles the larger public remove flow.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 198–200)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Atomically renames another contained file onto this target. “Atomically” means readers see either the old file or the new file, not a half-written state.

**Data flow**: It receives another `ContainedFile` as the source → uses both files’ pinned parent directories and names → asks the operating system to replace the destination name with the source name.

**Call relations**: This method connects two already-validated contained targets. It hands the final move to the operating system using directory-relative names so path swaps cannot redirect either side.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 202–203)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Writes text into the contained target by using the safer byte-replacement path. It is a convenience wrapper for callers that already have a string.

**Data flow**: It receives text and a permission mode → encodes the text into bytes → passes those bytes and the mode to `replace_bytes` → the target is replaced with the encoded content.

**Call relations**: `replace_text` delegates all actual writing to `replace_bytes`. That keeps the careful staged-write behavior in one place.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 205–229)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely replaces the target with new bytes. It writes to a temporary sibling file first, then renames it into place, which avoids partial writes and avoids writing through a symlink.

**Data flow**: It receives bytes and a permission mode → creates a random staged filename in the pinned parent using exclusive creation and no symlink following → writes the bytes and sets permissions → renames the staged file onto the target → cleans up the staged file if anything goes wrong.

**Call relations**: `replace_text` calls this for string content. Other callers use it directly for binary data, relying on it to do the staged create-and-rename sequence safely.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 231–247)

```
def _open_regular(self) -> int
```

**Purpose**: Opens the contained target as a regular file and refuses symlinks or non-file objects. It is the low-level safety check behind reading.

**Data flow**: It uses the pinned parent directory and target name → opens the name without following symlinks → reports a missing path clearly → checks the opened file descriptor to confirm it is a regular file → returns the raw descriptor or closes it and raises an error.

**Call relations**: `open_bytes` calls this before turning the descriptor into a Python file object. Keeping this as a separate helper centralizes the exact “open safely and prove regular file” rule.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 251–285)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main safe entry point for reading or writing a file under a root. It performs the full containment check and yields a `ContainedFile` whose parent directory is pinned.

**Data flow**: It receives a requested path, a root, and an option to create missing parent directories → verifies the root with `contained_root` → joins relative paths under the root with `rooted` → rejects unusable target names → resolves and checks the parent is inside the root → opens the root and descends through each parent directory without following symlinks, optionally creating missing directories → yields a `ContainedFile` → closes the pinned directory descriptor afterward.

**Call relations**: `contained_regular` uses this to prove a file before returning its path. Other read and write code is expected to enter through this context manager, then use the yielded `ContainedFile` methods for the actual operation.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_regular); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 288–314)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Safely resolves a directory under a root and optionally creates it. It is used when the caller needs a directory to walk or enumerate, not a file to open.

**Data flow**: It receives a path, a root, and a create flag → validates the root → roots and resolves the requested directory → checks it stays inside the root → opens the root and descends one directory component at a time without following symlinks, creating components if requested → closes the descriptor and returns the resolved directory path.

**Call relations**: `contained_glob` calls this to decide where a pattern search may start. Internally it uses the same root-opening, containment, and component-descent helpers as the file path.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_glob); 3 external calls (__init__, close, mkdir).


##### `contained_remove`  (lines 317–344)

```
def contained_remove(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> None
```

**Purpose**: Safely removes one file or directory tree under a root without following symlinks. It is the delete-side counterpart to the guarded read and write paths.

**Data flow**: It receives a path and root → validates and roots the path → rejects names like empty, `.` or `..` → checks the parent resolves inside the root → opens the root and descends safely to the parent → if the target is missing, it quietly returns → if the target is a directory, it removes the tree only if the platform’s removal routine is symlink-safe → otherwise it unlinks the single target.

**Call relations**: This public remove helper uses `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend` in the same safety sequence as `contained_file`. It does not call `ContainedFile` because deleting a directory tree needs different logic from opening a file.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 8 external calls (__init__, __init__, __init__, close, stat, unlink, rmtree, S_ISDIR).


##### `contained_regular`  (lines 347–356)

```
def contained_regular(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> Path
```

**Purpose**: Returns the safe canonical path of an existing regular file under a root. It is for cases where another tool or library needs a filename rather than an open file handle.

**Data flow**: It receives a path and root → enters `contained_file` to run the full file containment checks → calls `lstat` on the resulting `ContainedFile` → raises a not-found error if no file exists → returns the validated path.

**Call relations**: This is a bridge for less-flexible consumers, such as subprocesses or libraries that only accept paths. It relies on `contained_file` for the hard safety work, then adds the requirement that the target already exists.

*Call graph*: calls 1 internal fn (contained_file); 1 external calls (__init__).


##### `contained_pattern`  (lines 359–376)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Checks and rewrites a glob pattern so it cannot make a file search leave the root. A glob pattern is a filename pattern like `*.txt` used to list matching files.

**Data flow**: It receives a pattern and root → parses the pattern using POSIX-style path rules → rejects any pattern containing `..` → leaves relative patterns unchanged → for absolute patterns, verifies they point inside the root and rewrites them to be relative to the root → rejects a pattern that names the root directory itself.

**Call relations**: `contained_glob` calls this after deciding where the search should start. This helper focuses only on the pattern text, not on opening directories.

*Call graph*: called by 1 (contained_glob); 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_glob`  (lines 379–391)

```
def contained_glob(pattern: str, path: str | os.PathLike[str] | None, root: Path) -> tuple[Path, str]
```

**Purpose**: Prepares a safe directory and safe pattern for a glob search under a root. It keeps both the starting point and the pattern from escaping the allowed area.

**Data flow**: It receives a pattern, an optional starting path, and a root → checks whether the pattern is absolute → chooses the root as the start for absolute patterns or missing starts, otherwise uses the caller’s start path → validates the start directory with `contained_dir` → validates and possibly rewrites the pattern with `contained_pattern` → returns the safe start directory and safe pattern.

**Call relations**: This function ties together `contained_dir` and `contained_pattern`. It is the coordination point for enumeration code that wants to run a pattern search without each caller re-learning the path rules.

*Call graph*: calls 2 internal fn (contained_dir, contained_pattern); 1 external calls (PurePosixPath).


##### `contained_relative`  (lines 394–419)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs the lexical-only containment check for a path that this process cannot inspect on disk. Lexical means it looks only at the text of the path, not at real directories or symlinks.

**Data flow**: It receives a path and a root string → treats relative paths as being under the root → walks the path parts, applying `..` by moving up one level but refusing moves above the root → refuses paths that resolve to the root itself → returns the cleaned absolute-looking path under the root.

**Call relations**: This helper is for callers that need to validate intent before some later system performs the real write. It does not replace `contained_file`; the eventual filesystem operation still needs the stronger checks where the files actually live.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 422–430)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Extracts one usable filename from a name supplied by another system. It drops any directory parts and falls back to a safe name if the result is empty, `.` or `..`.

**Data flow**: It receives a raw name and a fallback → treats backslashes like slashes so Windows-style paths are handled → keeps only the last path component → returns that component if usable, otherwise returns the fallback.

**Call relations**: This is used before joining an external filename under a controlled root. It deliberately does not prove full containment; later writes should still go through the guarded file path.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 433–444)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Quickly checks whether an already-enumerated path is a regular file inside a root without having crossed a symlink. It is a filter for listing results, not the final authority for reading.

**Data flow**: It receives a path and root → uses `lstat` to make sure the path itself is a regular file → resolves the path strictly → returns false on any filesystem error → returns true only if the resolved path is exactly the same path and lies inside the root.

**Call relations**: Enumeration code can use this to decide what to show. A later read of a listed file should still go through `contained_file`, because listing and opening happen at different times.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 447–453)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Interprets a caller’s path relative to the containment root instead of relative to the process’s current working directory. This avoids checking one path but later using another.

**Data flow**: It receives a path and root → turns the path into a `Path` object → returns it unchanged if it is already absolute → otherwise joins it under the root.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` all call this near the start of their flow. It gives those entry points a consistent idea of what path the caller meant.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 1 external calls (Path).


##### `_inside`  (lines 456–457)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Answers the simple question: is one path the root itself or somewhere below it? It is the shared containment test after paths have been resolved.

**Data flow**: It receives a path and root → compares the path to the root and to the path’s parents → returns true if the root is the same path or an ancestor, otherwise false.

**Call relations**: `contained_file`, `contained_dir`, `contained_remove`, and `is_contained_regular` all use this as their final inside-the-root check. It is small, but it keeps the containment rule consistent.

*Call graph*: called by 4 (contained_dir, contained_file, contained_remove, is_contained_regular).


##### `_open_root`  (lines 460–464)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the containment root as a directory file descriptor without following symlinks. This begins the pinned-directory walk used by the safer operations.

**Data flow**: It receives a root path → asks the operating system to open it as a directory with no symlink following → returns the directory descriptor → raises a containment error if the root cannot be opened that way.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this before descending into child directories. Its returned descriptor is handed to `_descend` for each path component.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 2 external calls (__init__, open).


##### `_descend`  (lines 467–480)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory level deeper using an already-open parent directory, while refusing symlinks and non-directories. It also closes the parent descriptor it leaves behind.

**Data flow**: It receives the current directory descriptor, the next path component, and the full target path for error messages → opens the child component as a directory without following symlinks → turns missing components or non-directory components into clear containment errors → closes the old descriptor → returns the child descriptor.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this repeatedly while walking from the root to a target’s parent or directory. It is the mechanical heart of the pinned, symlink-safe descent.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 5 external calls (__init__, __init__, __init__, close, open).


### `core/src/ufo/harness/sandbox/protocol.py`

`io_transport` · `request handling`

A sandbox is an isolated place where the system can run commands or inspect files without giving that code full access to the host machine. This file acts like a menu and translator for that sandbox. Instead of making every caller build raw command-line arguments by hand, it offers clear helper methods such as “run this bash command” or “run this Python snippet.” The caller still supplies the actual execution function, so this file does not decide whether commands go through a container, a remote process, or some other carrier.

There are two main pieces. `SandboxCommands` builds safe, bounded command requests. “Bounded” here means each command is given a timeout, so it cannot run forever. It knows how to wrap bash commands through a supervisor, run journaled tasks that can detach or reattach, run simple POSIX shell scripts, and start Python with a bootstrap prefix supplied by the sandbox setup.

`SandboxFileOperations` is for structured file requests. It sends an operation name plus JSON parameters, waits for one JSON answer, checks that the answer is usable, and turns sandbox-reported errors into normal Python exceptions. It also gives document reads a longer timeout when their file suffix matches configured document types, because those reads may take longer than ordinary file operations.

#### Function details

##### `CommandResult.stdout`  (lines 14–14)

```
def stdout(self) -> str
```

**Purpose**: This names the standard output text that any sandbox command result must provide. Standard output is the normal text a command prints when it succeeds or reports regular information.

**Data flow**: A concrete command result object comes in with its stored command output. Reading this property gives back the command’s standard output as a string and does not change anything.

**Call relations**: This is part of the `CommandResult` protocol, which is a promise about what shape command results must have. `SandboxFileOperations.run` relies on this property after it executes a file operation, because it expects the sandbox’s structured JSON reply to appear in standard output.


##### `CommandResult.stderr`  (lines 17–17)

```
def stderr(self) -> str
```

**Purpose**: This names the error-output text that any sandbox command result must provide. Error output is where command-line programs usually print warnings, failures, or diagnostic messages.

**Data flow**: A concrete command result object comes in with its stored error text. Reading this property gives back the command’s standard error as a string and does not change anything.

**Call relations**: This property lets code explain failures in a useful way without knowing the exact result class being used. `SandboxFileOperations.run` uses it when the sandbox gives no valid JSON answer, so the caller sees the sandbox’s own error message if one exists.


##### `CommandResult.exit_code`  (lines 20–20)

```
def exit_code(self) -> int
```

**Purpose**: This names the numeric exit code that any sandbox command result must provide. An exit code is the number a process returns when it finishes, commonly zero for success and nonzero for failure.

**Data flow**: A concrete command result object comes in with its stored finish status. Reading this property gives back that integer status and does not change anything.

**Call relations**: This file defines the property as part of the common result shape, even though the helpers here mostly read output text instead. Other sandbox code can depend on the same protocol and check whether a command succeeded by looking at this value.


##### `SandboxCommands.bash`  (lines 33–38)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This builds and runs one bash command through the sandbox supervisor. It is used when a caller wants normal shell behavior but still wants the sandbox’s timeout and supervision rules.

**Data flow**: The caller provides a shell command string and may provide a timeout. The method builds an argument list that starts with the configured supervisor, adds a separator, then runs `bash -lc` with the command. It sends that argument list and the chosen timeout to the supplied `execute` function, then returns whatever result that function produces.

**Call relations**: This method is a convenience wrapper around the injected `execute` function. Callers ask `SandboxCommands` for a bash run; this method formats the request in the supervisor’s expected shape and hands it off to the real sandbox carrier.


##### `SandboxCommands.bash_task`  (lines 40–62)

```
async def bash_task(self, command: str, base: str, *, detach: bool, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This builds and runs a named, journaled bash task through the sandbox supervisor. It supports both starting a task in detached mode and reattaching to a previously tracked task.

**Data flow**: The caller provides a command string, a task base name, whether the task should detach, and optionally a timeout. The method turns the detach choice into a `--detach` flag when needed, builds the supervisor command with `--task`, the base name, and `bash -lc`, then passes the complete argument list and timeout to `execute`. The returned command result is passed back unchanged.

**Call relations**: This is the task-oriented version of `SandboxCommands.bash`. It sits between higher-level code that thinks in terms of long-running or resumable work and the lower-level sandbox execution function that only receives command arguments and a timeout.


##### `SandboxCommands.sh`  (lines 64–69)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This runs a small POSIX shell script while preserving each script argument as a separate command-line argument. POSIX shell means the basic `sh` command language available on Unix-like systems.

**Data flow**: The caller gives a script string, any number of argument strings, and optionally a timeout. The method builds `sh -c` arguments so the script is executed by `sh` and the extra values remain separate rather than being merged into one unsafe string. It sends those arguments and the timeout to `execute`, then returns the result.

**Call relations**: This method provides a simpler shell path than the supervisor-backed bash methods. Higher-level code can call it when it needs portable shell execution, and it delegates the actual run to the same injected `execute` function used by the rest of `SandboxCommands`.


##### `SandboxCommands.python`  (lines 71–82)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This runs a Python program inside the sandbox with a configured bootstrap prefix. The bootstrap is setup code added before the caller’s program, usually to enforce isolation or prepare the environment.

**Data flow**: The caller provides Python source text, optional argument strings, and optionally a timeout. The method combines the configured bootstrap with the caller’s program, builds a `python3 ... -c` command using the configured Python flag, and appends the arguments. It sends the final argument tuple and timeout to `execute`, then returns the command result.

**Call relations**: This gives higher-level code a safe standard way to start Python inside the sandbox. It hides the exact interpreter flags and bootstrap details, then hands the finished command request to the sandbox carrier through `execute`.


##### `SandboxFileOperations.run`  (lines 96–126)

```
async def run(self, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This performs one structured file operation in the sandbox and turns the sandbox’s JSON reply into a Python dictionary. It also turns missing, malformed, or explicitly failed replies into clear exceptions.

**Data flow**: The caller gives an operation name, such as `read`, and a dictionary of parameters. The method chooses a timeout: ordinary operations use the default timeout, but reads of configured document file types get the longer document-read timeout. It serializes the parameters to compact JSON, runs the configured sandbox file command, strips the command’s standard output, parses it as JSON, checks that the parsed value is an object, and returns it. If there is no output, invalid JSON, a non-object reply, or an `error` string in the reply, it raises an exception instead.

**Call relations**: This method is the bridge between ordinary Python callers and the sandbox’s file protocol. It uses `PurePosixPath` to inspect the requested path suffix, `json.dumps` to send parameters in the protocol format, and `json.loads` to read the sandbox’s single structured response. The actual command execution is still delegated to the injected `execute` function.

*Call graph*: 3 external calls (dumps, loads, PurePosixPath).


### `core/src/ufo/runtime/tools/context.py`

`domain_logic` · `tool execution and per-turn cleanup`

A tool is a small action the agent can call, such as editing a file, searching memory, using a connector, or generating an image. This file is the toolbox and rulebook handed to that action. It says what the tool is allowed to touch, who it is acting for, what audience can see its output, and how it can safely ask the rest of the system for help.

The central piece is ToolContext. It carries the current turn, agent, sandbox, blob storage, permission grants, skill registry, browser/search providers, and other per-turn services. It also includes helper methods for common sensitive tasks: saving generated artifacts, charging image or video usage to the billing ledger, checking whether the speaker is an admin, resolving which connected external account a tool may use, and starting credential authorization.

The file also defines the shape of tool results, child-agent spawning, background subagent control, and cleanup. Cleanup works like a coat-check ticket system: when a tool opens a temporary resource, it registers how to close it, and the turn drains those closers at the end so browser sessions or leases do not leak.

Without this file, tools would either need unsafe direct access to the runtime or would each reimplement permission checks, artifact storage, account selection, and cleanup differently.

#### Function details

##### `UnknownSubagentProfile.__init__`  (lines 120–125)

```
def __init__(self, requested: str, registered: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when code asks for a subagent profile that is not registered. The message includes both the bad name and the valid names, so the caller or model has something useful to correct.

**Data flow**: It receives the requested profile name and the tuple of registered profile names. It turns those into a human-readable exception message and stores both pieces of information on the exception object. The result is an exception ready to be raised and inspected.

**Call relations**: The subagent registry calls this when a profile lookup fails. Instead of letting a plain missing-key error escape, this gives the spawning flow a specific, helpful failure that can be surfaced back to the agent.

*Call graph*: called by 1 (get).


##### `UnknownSpawnTarget.__init__`  (lines 132–139)

```
def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when a spawn request names something that is neither a known subagent profile nor a workspace agent. It tells the caller what names are available in both places.

**Data flow**: It receives the requested target name, the valid profile names, and the valid agent names. It formats them into an exception message and stores them as fields on the exception. The output is an exception that explains exactly why spawning cannot continue.

**Call relations**: The subagent runtime uses this while resolving spawn targets and when requiring an agent spawn. It is part of the path that turns a bad child-agent request into a retryable, understandable tool error.

*Call graph*: called by 2 (_require_agent_spawn, _resolve).


##### `SpawnNeedsOwnModelKey.__init__`  (lines 153–161)

```
def __init__(self, requested: str, connect_url: str | None=None) -> None
```

**Purpose**: Builds an error for a spawn target that requires the member to connect their own model provider account, such as ChatGPT or Claude, when they have not done so. It includes a link or fallback instruction telling them where to connect one.

**Data flow**: It receives the requested spawn target and optionally a public base URL. It combines that URL with the credentials page path when possible, or says to use the portal otherwise. The result is an exception whose message tells the model what the user must do next.

**Call relations**: The subagent spawning flow raises this when it discovers that the requested child profile depends on a member-owned model key that is missing. This stops futile retries and lets the assistant guide the member to the setup screen.

*Call graph*: called by 1 (spawn).


##### `AmbiguousSpawnTarget.__init__`  (lines 168–173)

```
def __init__(self, requested: str) -> None
```

**Purpose**: Builds an error when a spawn target name could mean both a profile and a workspace agent. It tells the caller to use a qualified name such as profile:name or agent:name.

**Data flow**: It receives the ambiguous target name. It writes an exception message explaining the two possible meanings and saves the requested name. The output is a specific exception that asks the caller to disambiguate.

**Call relations**: The subagent target resolver calls this when a bare name matches both namespaces. It protects the system from guessing wrong about which kind of child work should be started.

*Call graph*: called by 1 (_resolve).


##### `Spawn.__call__`  (lines 245–254)

```
async def __call__(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnRes
```

**Purpose**: Describes the callable interface for starting a child turn, either as a named subagent profile or as another workspace agent. A tool uses this when it wants to delegate a typed subtask instead of doing all the work itself.

**Data flow**: The caller supplies a target name, a payload of input data, and options such as whether the child should run in the background, whether the child should be deduplicated, and whether its result should be delivered directly to the conversation. An implementation validates the payload, starts or reconnects to the child turn, and returns a SpawnResult describing the child and, when available, its output or terminal state.

**Call relations**: This file only defines the protocol, meaning the expected shape of the operation. The subagent runtime provides the real implementation, and tool handlers call it through ToolContext.spawn when they need delegated work.


##### `SubagentControl.result`  (lines 264–264)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Describes the operation for reading the final result of a background child turn. A tool uses it when it already knows the child turn id and wants the completed output.

**Data flow**: It receives a child turn id. The implementation looks up that child, reads its terminal state and validated output if it is finished, and returns a SpawnResult. It does not create a new child; it reports on an existing one.

**Call relations**: This is part of the SubagentControl protocol attached to ToolContext. The subagent workflow implements it, and lifecycle tools use it to reconnect a parent turn to work that was spawned earlier.


##### `SubagentControl.wait`  (lines 266–266)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes the operation for waiting on one or more background child turns for a bounded period inside a tool call. It lets a tool pause for children without losing track of their status.

**Data flow**: It receives a tuple of child turn ids. The implementation waits according to its own rules, gathers the terminal status and final text for children that are ready, and returns a tuple of SubagentStatus records.

**Call relations**: This protocol method is supplied by the subagent runtime. Tools that coordinate background subagents call it through ToolContext.subagents when they want to collect progress or results.


##### `SubagentControl.cancel`  (lines 268–268)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes the operation for stopping a running background child turn. A tool uses it when delegated work is no longer wanted.

**Data flow**: It receives a child turn id. The implementation requests cancellation for that child and returns a SubagentStatus showing the resulting terminal state and message.

**Call relations**: This sits on the SubagentControl interface. The actual cancellation behavior lives in the subagent system, while tools access it through the context provided here.


##### `SubagentControl.message`  (lines 270–272)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Describes the operation for sending a follow-up message to an already-running child turn. The deduplication key helps avoid sending the same follow-up twice after a retry or crash recovery.

**Data flow**: It receives the child turn id, message text, a deduplication key, and a flag saying whether the child should deliver its own final result. The implementation admits that message to the child turn and returns the child’s updated status.

**Call relations**: This protocol method connects parent tools to background subagents after the initial spawn. The subagent runtime implements it so tools can continue or redirect child work safely.


##### `TurnCleanup.register`  (lines 286–287)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous close function to the list of things that must be cleaned up when the turn ends. Tools use this when they open a temporary resource, such as a browser connection.

**Data flow**: It receives a no-argument async function that knows how to close one resource. It appends that function to the cleanup list. Nothing is returned, but the context now remembers that cleanup task.

**Call relations**: Tool code calls this when it first creates a per-turn resource. Later, the runtime calls TurnCleanup.drain to run the registered closers.


##### `TurnCleanup.drain`  (lines 289–295)

```
async def drain(self) -> None
```

**Purpose**: Closes all resources registered for the turn, in reverse order. If one close operation fails, it logs the failure and keeps going so other resources still get cleaned up.

**Data flow**: It reads the internal list of registered async close functions. It pops each one, awaits it, and logs any exception instead of stopping the whole cleanup. When it finishes, the list is empty.

**Call relations**: The turn loop drains this registry at turn end. It calls the project logging helper when cleanup fails, which prevents hidden leaks while still making the failure visible to operators.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 342–350)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Finds the member whose authority this tool call should use. It prefers the live speaker, and falls back to the member the turn is running on behalf of.

**Data flow**: It reads speaker_member_id from the context. If that is present, it returns it; otherwise it returns turn.on_behalf_of_member_id. It changes nothing.

**Call relations**: Many permission decisions in this file build on this property, including audience selection and connector-account selection. It gives those later checks one consistent answer to “who is this action for?”


##### `ToolContext.effective_audience`  (lines 353–363)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides which audience a write should belong to. In a shared workspace conversation with a known acting member, it narrows the write to that member’s conversation audience; otherwise it keeps the current conversation audience.

**Data flow**: It reads the acting member and the current audience. If there is no acting member or the audience is not the shared workspace audience, it returns the existing audience. If both conditions match, it calls conversation_audience for that member and returns that more specific audience.

**Call relations**: Tools that write memory or records can use this to avoid leaking private-room facts into broader spaces. It relies on the audience helper to build the member-specific audience only when that narrowing is needed.

*Call graph*: 1 external calls (conversation_audience).


##### `ToolContext.read_subjects`  (lines 366–375)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes what subjects this tool call may read. It combines the conversation’s readable subjects with the acting member’s private subject when there is an acting member.

**Data flow**: It converts the current audience into its readable subjects. Then, if an acting member exists, it adds that member’s private subject. It returns the final frozen set and does not modify the context.

**Call relations**: Source and memory tools use this indirectly through source_reader. It calls the audience and subject helpers so every tool follows the same privacy boundary.

*Call graph*: 2 external calls (audience_subjects, member_subject).


##### `ToolContext.store_preview`  (lines 377–423)

```
async def store_preview(self, sandbox_path: str, name: str, *, extension: str='png') -> StoredPreview | None
```

**Purpose**: Stores an image preview that a tool rendered inside the sandbox and returns the blob key and byte size. If the preview file cannot be read or uploaded, it logs the problem and returns None instead of failing the main tool work.

**Data flow**: It receives a sandbox file path, display name, and image extension. It asks the sandbox to measure the file size, creates a fresh artifact key, and either uploads through a presigned S3 URL or streams the file into local blob storage. On success it returns a StoredPreview with the key and measured size; on sizing or upload failure it returns None.

**Call relations**: Site-related extension code calls this after rendering visual previews. It uses sandbox shell commands, blob storage, UUIDs, path quoting, and logging to move bytes from the isolated sandbox into the artifact namespace without pulling the whole file through normal tool output.

*Call graph*: called by 2 (design_ufo_application, _compose); 5 external calls (__init__, quote, log, shell_path, uuid4).


##### `ToolContext.render_site_preview`  (lines 425–432)

```
async def render_site_preview(self, name: str, port: int, width: int, height: int) -> StoredPreview | None
```

**Purpose**: Asks the configured preview service to take a screenshot-like preview of a hosted sandbox port. If no preview service is configured, it quietly returns None.

**Data flow**: It receives a preview name, port, width, and height. It chooses the right conversation id for the sandbox host, then calls the site previewer with that information. The result is either a StoredPreview from the preview service or None.

**Call relations**: The sites extension calls this when it wants a visual preview of a running web app. This method keeps the tool from needing to know how the preview service reaches a sandbox port.

*Call graph*: called by 1 (_illustrate).


##### `ToolContext.source_reader`  (lines 434–444)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Builds a small permission description for reading synced source pages. It says which agent is asking, which live member is requesting, and which subjects are readable.

**Data flow**: It reads the turn’s agent id, the current speaker member id, and the computed read_subjects. It packages those into a SourceReader object. It does not fetch source content itself.

**Call relations**: Memory and source extensions call this before searching or listing source-backed content. It hands those extensions a consistent reader identity so source access follows the same audience rules as the rest of the turn.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 446–455)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images on this turn’s billing ledger. This is used for image providers whose pricing is not part of the normal language-model token accounting.

**Data flow**: It receives the provider model name, number of images, and cost in micro-dollars. It opens a workspace database transaction and writes an image-usage record tied to the workspace and turn. It returns nothing, but the billing ledger is updated.

**Call relations**: The OpenRouter image extension calls this after it knows what the provider charged. This keeps provider-specific pricing in the extension while core still owns the official billing record.

*Call graph*: called by 1 (generate); 2 external calls (workspace_tx, record_image_usage).


##### `ToolContext.meter_videos`  (lines 457–465)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos on this turn’s billing ledger. This covers video providers that charge differently from text models.

**Data flow**: It receives the provider model name, number of videos, and cost in micro-dollars. It opens a workspace database transaction and writes a video-usage record tied to the workspace and turn. It returns nothing, but the ledger now includes that spend.

**Call relations**: The OpenRouter video extension calls this after generation. Like image metering, it lets extensions price their own provider calls while core records the spend consistently.

*Call graph*: called by 1 (generate); 2 external calls (workspace_tx, record_video_usage).


##### `ToolContext.share_artifact`  (lines 467–550)

```
async def share_artifact(self, filename: str, data: bytes, subject: str | None=None, *, preview: StoredPreview | None=None) -> None
```

**Purpose**: Publishes a small in-memory file produced by a tool as a shared artifact of the current turn. It stores the bytes, records metadata in the database, optionally links a validated preview image, and notifies the surface if publishing is configured.

**Data flow**: It receives a filename, bytes, optional subject, and optional preview. It checks size limits and preview validity, creates a deterministic artifact id when an idempotency key exists, uploads the bytes to blob storage, and inserts a shared-artifact row. If the database insert fails after a new upload, it tries to delete the blob so orphaned files are not left behind. It may call publish_artifacts at the end.

**Call relations**: The iMessage and sites extensions call this when they create a file for the member. It uses database transactions, blob storage, media-type detection, UUIDs, and logging to make artifact sharing safe, repeatable, and visible to the user interface.

*Call graph*: called by 2 (run, design_ufo_application); 8 external calls (now, select, workspace_tx, log, artifact_media_type, raster_image_media_type, uuid4, uuid5).


##### `ToolContext.speaker_is_admin`  (lines 552–562)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live requesting member is a workspace admin. If there is no live speaker, it returns false because background work should not exercise admin-only powers.

**Data flow**: It reads speaker_member_id. If none is present, it immediately returns false. Otherwise it opens a workspace transaction, asks the seat system whether that member is an admin in this workspace, and returns the boolean result.

**Call relations**: Many object and credential operations call this before allowing workspace-wide actions. The credential authorization helper also uses it to ensure only admins can approve extension credential slots.

*Call graph*: called by 27 (_widens_for_admin, delete, _visible_rows, request_credentials_handler, restore, apply, delete, get, list, status (+15 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 564–575)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether the current turn’s agent is the workspace’s main agent. Some actions are allowed only from, or shown differently to, the main agent.

**Data flow**: It opens a workspace transaction and selects the is_main flag for the current agent in the current workspace. It returns that flag as a boolean, or false if no matching row is found.

**Call relations**: Member, workspace, and web-audience code call this when deciding what the current agent may see or change. It centralizes the database lookup so those callers do not each query the agent table differently.

*Call graph*: called by 6 (add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.agent_visibility`  (lines 577–589)

```
async def agent_visibility(self) -> AgentVisibility
```

**Purpose**: Reads whether the current agent is private or workspace-visible. It raises an error if the database contains a value the code does not understand.

**Data flow**: It opens a workspace transaction and selects the visibility field for the current agent. If the value is private or workspace, it returns it. Otherwise it raises a runtime error to flag inconsistent stored data.

**Call relations**: The sites extension calls this when redeploying a homepage. This method provides a checked, typed answer instead of letting callers work with arbitrary database strings.

*Call graph*: called by 1 (_redeploy_homepage); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 591–593)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for an extension credential slot. It returns a sealed authorization string that can later be opened after the user completes the flow.

**Data flow**: It receives a credential slot name and payload. It first calls the shared _credential_authorization checks to confirm the speaker, extension declaration, credential storage, and admin permission. Then it asks the CredentialRequests service to authorize this workspace, member, slot, and payload, and returns the sealed string.

**Call relations**: Coding and Slack extension tools call this when they need the user to connect or authorize credentials. It delegates all safety checks to _credential_authorization so the begin and open paths enforce the same rules.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 595–597)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens a sealed credential authorization for an extension credential slot. This is the counterpart to beginning an authorization flow.

**Data flow**: It receives a credential slot name and sealed authorization string. It calls _credential_authorization to repeat the same permission and configuration checks. Then it asks CredentialRequests to open the sealed authorization for this workspace, member, and slot, returning the resulting payload string.

**Call relations**: This method is available to extension tools that need to complete a credential flow. It shares its gatekeeping with begin_credential_authorization to avoid a weaker second step.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext._credential_authorization`  (lines 599–608)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the common safety checks required before an extension can authorize or open a credential slot. It makes sure there is a live speaker, the extension declared the slot, credential storage is configured, and the speaker is an admin.

**Data flow**: It receives a slot name. It reads speaker_member_id, extension metadata, configured credential request service, and admin status. If any requirement fails, it raises a clear error; otherwise it returns the CredentialRequests service and the speaker member id.

**Call relations**: Both credential authorization entry points call this before doing their actual work. It calls speaker_is_admin for the admin check and raises SpeakerRequired when a tool tries to do this without a live member.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (begin_credential_authorization, open_credential_authorization); 1 external calls (__init__).


##### `ToolContext.connector_account`  (lines 610–619)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the external broker account id that a connector tool is allowed to use for a provider. It is a convenience wrapper when the caller only needs the account id, not the full connection record.

**Data flow**: It receives a provider name and optionally a specific account id. It calls connector_connection to resolve and validate the allowed connection. It returns only the account_id from that connection.

**Call relations**: Connector tools and sample connector execution call this before asking the external broker to run work. The real selection and permission logic lives in connector_connection, so this method stays small and consistent.

*Call graph*: calls 1 internal fn (connector_connection); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_connection`  (lines 621–658)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Chooses the exact connected external account this turn may use for a provider. It prefers the acting member’s private connection, falls back to shared agent connections, and refuses ambiguous or unavailable choices.

**Data flow**: It receives a provider name and optionally a requested account id. It asks _connector_account_tiers for private and shared grants. If an account id was supplied, it returns the matching connection or raises an error. If no account id was supplied, it chooses the only account in the preferred tier, or raises when none or multiple are available. On success it returns a ConnectorConnection with the connection id, account id, and owning member.

**Call relations**: connector_account calls this when it only needs the account id, and source tools call it when they need the exact connection generation. It is the main guardrail that prevents a tool from using another agent’s or member’s connected account.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


##### `ToolContext.connector_accounts`  (lines 660–669)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists the connected account ids this turn may use for one provider. It includes the acting member’s private grants and any shared grants available to the agent.

**Data flow**: It receives a provider name. It calls _connector_account_tiers, combines the private and shared grants, removes duplicates by account id, sorts them, and returns them as a tuple of strings.

**Call relations**: Source tools call this when resolving which account to use or showing available choices. It relies on the same tier calculation as connector_connection, so listing and selecting accounts follow the same permission rules.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 671–690)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: Separates active connector grants into two groups: private grants owned by the acting member, and shared grants for the requested provider. This is the permission foundation for connector account listing and selection.

**Data flow**: It receives a provider name. If no grant store is configured, it raises ConnectUnavailable. Otherwise it reads all active grants, filters them by provider, acting member, and whether the connection is shared, sorts each group by account id, and returns the private and shared lists.

**Call relations**: connector_connection and connector_accounts both call this before making their user-facing decisions. By keeping the filtering here, the file ensures connector tools consistently respect private-by-default connected accounts.

*Call graph*: called by 2 (connector_accounts, connector_connection); 1 external calls (__init__).


### Command and Workspace Journals
Tracks long-running command state and records bounded workspace file-change snapshots for later reporting.

### `core/src/ufo/runtime/tools/file_changes.py`

`config` · `cross-cutting`

This is a tiny but useful settings file. It defines `FILE_CHANGE_PATH_MAX_CHARS`, a single number: 4,096 characters. That value is meant to be used anywhere the system needs to store, compare, or display the path of a file that changed.

The problem it solves is consistency. Without a shared limit, different parts of the code might make different guesses about how long a path can be. One part might accept a very long path, while another part might later fail, truncate it differently, or use too much memory. By putting the limit in one named constant, the project gives everyone the same rule.

Think of it like a standard envelope size for mailing forms. The file does not mail anything itself, but it tells the rest of the office what size envelope to design around.

There are no functions here. The file simply exposes this named value so other code can import it and apply the same boundary when working with file-change paths.


### `core/src/ufo/runtime/tools/tasks.py`

`orchestration` · `tool execution and crash recovery`

This file solves a practical problem: tool commands may take longer than the current turn can wait, but killing them would waste work. Instead, commands are launched through a small journal under the run’s task directory. Think of it like giving each command a numbered locker: the command writes its log, process id, and final exit code there, and later code can come back to the same locker instead of starting over.

The central flow is `run_task`. It chooses a stable task id when the tool call is being replayed after a crash, starts the command in the sandbox, and waits only for the allowed foreground budget. If the command finishes in time, the normal result is returned. If the wait expires, the file probes whether the detached command is still alive or has already written its exit file. If it is alive, callers can be shown handles created by `task_handles`: where to read the log, what file signals completion, and how to stop it. If nothing is alive after a timeout, the code records a short diagnostic snapshot of the sandbox, because that suggests the command channel failed rather than the work simply continuing.

The file also refuses a specific bad pattern: long plain `sleep` calls outside loops. That protects turns from being padded by waiting instead of doing useful work.

#### Function details

##### `flat_sleeps`  (lines 33–51)

```
def flat_sleeps(command: str) -> tuple[int, ...]
```

**Purpose**: Finds long, plain `sleep` commands that would just burn the caller’s foreground waiting time. It ignores sleeps inside shell loops, quoted text, and heredoc blocks, because those are usually data or polling behavior rather than pointless padding.

**Data flow**: It takes a shell command as text. It first blanks out quoted strings and heredoc bodies, then scans what remains for `sleep`, `do`, and `done`. It tracks whether the scan is currently inside a loop, keeps only sleeps outside loops, filters out short sleeps of 10 seconds or less, and returns the remaining sleep lengths as numbers.

**Call relations**: This helper is independent in this file’s call graph. Other tool code can use it before running a command to decide whether to refuse a foreground command that is mostly waiting.


##### `run_task`  (lines 94–131)

```
async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None) -> TaskRun
```

**Purpose**: Starts one shell command through the task journal and waits only as long as the caller’s timeout allows. If the command is still running after that wait, it leaves the command alive and returns enough information for the caller to reconnect to it.

**Data flow**: It receives a tool context, the command text, and an optional timeout in milliseconds. It turns the timeout into seconds, applies the maximum allowed cap, derives the task id, asks the sandbox where the task files should live, and launches the command through `bash_task`. If the command finishes, it returns a `TaskRun` with the result and no running process id. If the wait expires, it probes the task files to see whether the detached supervisor process or exit file exists. If the probe finds nothing, it records timeout diagnostics. It then returns a `TaskRun` containing the original result, timeout request, possible process id, and display path.

**Call relations**: This is the main entry point in the file’s flow. It calls `task_id` first so repeated dispatch attempts can attach to the same journal entry, and it calls `_record_exec_timeout` only when a timeout leaves no live detached command to report. It packages the outcome in `TaskRun` for surrounding tool code to interpret.

*Call graph*: calls 2 internal fn (_record_exec_timeout, task_id); 1 external calls (__init__).


##### `task_id`  (lines 134–141)

```
def task_id(ctx: ToolContext) -> str
```

**Purpose**: Chooses the short name used for a task’s journal files. When a tool call has an idempotency key, meaning a stable replay-safe identifier, this function turns it into the same task id every time; otherwise it creates a fresh random id.

**Data flow**: It reads the idempotency key from the tool context. If there is no key, it generates a random UUID and returns the first eight hex characters. If there is a key, it hashes that key with SHA-256 and returns the first eight hex characters, so the same key always maps to the same task name.

**Call relations**: It is called by `run_task` before launching a command. That lets a crashed or retried dispatch step find the first command’s files instead of accidentally starting the same command again.

*Call graph*: called by 1 (run_task); 2 external calls (sha256, uuid4).


##### `task_handles`  (lines 144–165)

```
def task_handles(task: str, pid: str, display_base: str, applied_s: int | None=None, note: str='') -> str
```

**Purpose**: Builds the user-facing message for a detached command. It explains whether the command was detached from the start or moved to the background after its wait expired, then gives practical handles for reading logs, watching completion, and stopping the work.

**Data flow**: It receives the task id, supervisor process id, display path base, an optional applied timeout, and an optional note. It chooses the right lead sentence, builds paths and commands for the log, exit file, watch command, and stop command, serializes those details as JSON, and returns one text block for the caller to show.

**Call relations**: This function is not called inside this file, but it is the companion to `run_task` when a command continues detached. After `run_task` reports a live process id, surrounding tool code can call `task_handles` to tell the user exactly how to follow or stop the command.

*Call graph*: 1 external calls (dumps).


##### `timeout_notice`  (lines 168–184)

```
def timeout_notice(applied_s: int, requested_s: int | None) -> str
```

**Purpose**: Creates a clear explanation for a command timeout. It distinguishes the default timeout, a caller-requested timeout, and a requested timeout that was reduced by the system maximum.

**Data flow**: It takes the timeout that actually applied, plus the timeout the caller originally requested if any. If there was no request, it says the sandbox used its default. If the request was larger than the applied value, it says the request was capped. Otherwise it reports the applied timeout plainly. The output is a human-readable sentence.

**Call relations**: This helper is independent in this file’s call graph. Tool result formatting code can use it when a sandbox-stopped command needs an explanation that is more informative than just an exit code.


##### `_record_exec_timeout`  (lines 187–220)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: Writes diagnostic information when a command times out and the system cannot confirm that detached work is still alive. This is for operators and logs, not for changing the user-visible result.

**Data flow**: It receives the tool context, command text, applied timeout, and requested timeout. It tries, within a short extra deadline, to ask the sandbox for basic health facts such as load, memory, and workspace disk usage. Whether that succeeds or fails, it logs a `sandbox.exec_timeout` event with the profile, timeout values, a shortened command, whether the vitals probe answered, and the vitals text if available. It deliberately returns nothing and swallows probe errors so the original timeout can still be reported.

**Call relations**: It is called by `run_task` only after a wait expires and the follow-up probe finds no live detached command or exit file. It calls the observability helpers `turn_profile` and `log` so timeout incidents can be investigated later without blocking the main command flow.

*Call graph*: called by 1 (run_task); 3 external calls (timeout, log, turn_profile).


### `core/src/ufo/runtime/turns/workspace_changes.py`

`domain_logic` · `turn end`

A conversation can edit files, run shell commands, delete things, or rename things inside its workspace. The message history alone cannot reliably describe all of that. This file solves that by asking the sandbox, “What does Git think changed here?” and storing the answer in the database.

The key idea is to avoid scanning an entire possibly huge workspace every time. Instead, the code builds a watch list. File-writing tools add the files they named. A shell command adds the workspace root, because a command can change anything without saying which file. The recorder also keeps watching directories that had changes last time, so a changed checkout remains visible until Git says it is clean.

The saved data is shaped by small validation models: one change has a path, a patch, and a flag saying whether it was shortened; one scan has many changes and an overall shortened flag. There are size limits so an enormous diff cannot flood storage or the portal.

At the end of a turn, WorkspaceChangeRecorder fetches the last stored scan, decides which directories to ask about, asks the sandbox filesystem helper for current changes, and writes the result. If two turns update the same workspace at nearly the same time, it carefully merges results so one scan does not erase another scan’s still-relevant entries. If scanning fails, it logs the problem but does not fail the already-finished turn; an old answer is considered better than breaking the user flow.

#### Function details

##### `change_targets`  (lines 37–55)

```
def change_targets(calls: Iterable[ToolUseBlock]) -> tuple[str, ...]
```

**Purpose**: This function looks at tool calls from a turn and extracts the workspace paths that might have been changed. It is used to decide where later Git-style scanning should look, instead of blindly scanning everything.

**Data flow**: It receives a sequence of tool-use records. For write and edit calls, it reads the file_path, checks that the path really belongs under the workspace, and stores the workspace-relative path. For bash calls, it stores the workspace root, because a shell command might change files anywhere. It returns a tuple of unique paths in the order they were first seen.

**Call relations**: This is an early filtering step for workspace change recording. It uses workspace_path to reject paths outside the workspace and PurePosixPath to turn accepted paths into clean relative paths. The resulting targets are later carried by WorkspaceChangeRecorder so the final scan knows where to look.

*Call graph*: 2 external calls (PurePosixPath, workspace_path).


##### `WorkspaceChangeRecorder.record`  (lines 101–114)

```
async def record(self) -> None
```

**Purpose**: This is the main turn-end action that refreshes the stored list of workspace changes. It asks what was previously known, scans the relevant directories again, and stores the new combined answer.

**Data flow**: It starts with the recorder’s sandbox, workspace id, conversation id, and target paths. If there is no created sandbox and no targets, it does nothing. Otherwise it reads the last saved scan, turns that plus the new targets into directories to watch, asks the sandbox for current changes, and stores the result. If anything goes wrong, it logs the failure and leaves the old stored scan in place.

**Call relations**: This method is the coordinator for the file. It calls recorded_workspace_changes to get the previous snapshot, _directories to choose what to scan, _scan to ask the sandbox filesystem helper, and _store to write the database update. When an error happens, it hands details to the logging system rather than raising the failure back into the completed turn.

*Call graph*: calls 4 internal fn (_directories, _scan, _store, recorded_workspace_changes); 1 external calls (log).


##### `WorkspaceChangeRecorder._directories`  (lines 116–129)

```
def _directories(self, recorded: WorkspaceChanges) -> list[str]
```

**Purpose**: This helper chooses the directory list that should be scanned. It combines newly touched paths with paths that were changed in the last saved scan, so old visible changes stay watched until they disappear.

**Data flow**: It receives the last recorded WorkspaceChanges object and reads the recorder’s current targets. For every target and every previously recorded changed file, it takes the parent directory. It sorts these directories, caps the list at a configured maximum, logs if some had to be dropped, and returns the final list.

**Call relations**: record calls this after loading the previous scan. The directory list it returns is passed directly to _scan. It uses PurePosixPath to find each path’s parent directory and the logger to report when the safety cap removes extra scan targets.

*Call graph*: called by 1 (record); 2 external calls (PurePosixPath, log).


##### `WorkspaceChangeRecorder._scan`  (lines 131–136)

```
async def _scan(self, directories: list[str]) -> WorkspaceChanges
```

**Purpose**: This helper asks the sandbox to report current file changes for selected directories. It also checks that the sandbox’s answer has the expected shape before the rest of the system trusts it.

**Data flow**: It receives a list of workspace-relative directories. It sends those paths to the sandbox’s filesystem command named changes. The sandbox returns raw data, which this function validates as a WorkspaceChanges object. If the data is malformed, it raises a clear runtime error.

**Call relations**: record calls this after _directories has chosen where to look. The validated WorkspaceChanges result is then passed to _store. This method is the boundary between the recorder’s Python logic and the sandbox filesystem helper.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 138–161)

```
async def _store(self, scanned: WorkspaceChanges, asked: frozenset[str]) -> None
```

**Purpose**: This helper saves a fresh scan in the database without accidentally deleting another recorder’s work. It is careful because two turns that share one workspace may finish at nearly the same time.

**Data flow**: It receives the newly scanned changes and the set of directories that were actually asked about. Inside a database transaction, it ensures a row exists for this workspace and conversation, locks that row, reads the current saved scan, merges the current saved scan with the new scan, and writes the merged result back.

**Call relations**: record calls this after _scan succeeds. It opens a workspace database transaction, uses the right insert style for PostgreSQL or SQLite, reads the existing row under a lock, then calls _merged to decide exactly what should survive. Its output is not a return value; the important effect is the updated conversation_change row.

*Call graph*: calls 1 internal fn (_merged); called by 1 (record); 5 external calls (model_dump, and_, select, update, workspace_tx).


##### `WorkspaceChangeRecorder._merged`  (lines 163–178)

```
def _merged(self, scanned: WorkspaceChanges, stored: WorkspaceChanges, asked: frozenset[str]) -> WorkspaceChanges
```

**Purpose**: This helper combines a fresh scan with the scan already stored in the database. It keeps old entries only when the fresh scan did not look in that entry’s directory, so unscanned areas are not falsely marked clean.

**Data flow**: It receives the new scan, the stored scan, and the set of directories that were scanned. It takes all new changes, then adds stored changes whose parent directory was not scanned and whose exact path was not already reported fresh. It trims the combined list to the maximum allowed size and sets the truncated flag if either scan was already truncated in a still-relevant way or the combined list was too long.

**Call relations**: _store calls this while holding the database row lock. This is the small piece of logic that makes concurrent recorders safe: scanned directories are replaced with fresh truth, while unscanned directories keep their previous saved truth.

*Call graph*: called by 1 (_store); 2 external calls (__init__, PurePosixPath).


##### `recorded_workspace_changes`  (lines 181–205)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: This function reads the last saved workspace-change scan for a conversation. If the conversation is a subagent sharing a parent workspace, it resolves to the parent conversation’s scan so there is one answer for the shared workspace.

**Data flow**: It receives a conversation id. In a database transaction, it looks up the owning sandbox conversation id, falling back to the conversation’s own id. It then reads the saved scan for that owner. If there is no conversation or no saved scan, it returns the shared NOTHING_CHANGED value; otherwise it validates and returns the stored WorkspaceChanges object.

**Call relations**: WorkspaceChangeRecorder.record calls this before deciding what to scan. It uses database queries through workspace_tx and SQLAlchemy. Its result feeds _directories, which means past saved changes influence what the next scan continues watching.

*Call graph*: called by 1 (record); 2 external calls (select, workspace_tx).


### User Activity Labels
Turns raw tool calls into concise plain-language activity descriptions for the user interface.

### `core/src/ufo/runtime/turns/activity.py`

`domain_logic` · `during a turn, when preparing user-visible progress for a tool call`

When the system uses a tool, the raw record of that tool call is often too technical or sensitive to show directly. It may contain tool names, arguments, paths, IDs, or other details that are useful to the program but confusing or unsafe for a person watching progress. This file creates a small “what is happening now” label, like a status update on a delivery app.

The main piece is ActivitySummarizer. It receives one tool call and an optional user goal. It trims the goal and the tool arguments so the summary request stays small. It then wraps that information in JSON and sends it to a language model with strict instructions: write only a short 3-to-8-word plain-language label, and do not reveal tool names, commands, paths, URLs, IDs, secrets, or JSON.

The model call is protected by a timeout, so a slow summary cannot stall the larger turn. If anything goes wrong, the file records a metric and a log message, then returns no label instead of breaking the user flow. Finally, it cleans the model’s answer by removing extra whitespace, bullets, quotes, and ending punctuation.

#### Function details

##### `ActivityModel.model`  (lines 31–31)

```
def model(self) -> str
```

**Purpose**: This is the promised way to ask an activity-labeling model which model name it uses. ActivitySummarizer needs this name when it builds the request sent to the model service.

**Data flow**: Nothing is passed in beyond the model object itself. The property reads the model identifier from that object and gives back a string name that can be placed into a ModelRequest.

**Call relations**: ActivitySummarizer.summarize relies on any ActivityModel-compatible object to provide this property before it asks for a completion. This file defines the expectation, while the actual model implementation lives elsewhere.


##### `ActivityModel.complete`  (lines 33–33)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This is the promised way to ask an activity-labeling model for text. It takes a prepared model request and returns the model’s written completion.

**Data flow**: A ModelRequest goes in, containing the prompt, user message, token limit, and model settings. The model object sends or computes that request elsewhere and returns a text string with the proposed activity label.

**Call relations**: ActivitySummarizer.summarize calls this method after building the request. The protocol lets this file stay independent from the concrete model provider, as long as that provider can complete the request in this shape.


##### `ActivitySummarizer.summarize`  (lines 42–69)

```
async def summarize(self, call: ToolUseBlock, goal: str='') -> str | None
```

**Purpose**: This function creates one short, user-friendly label for a single tool call. It is used when the system wants to show progress without exposing technical tool details.

**Data flow**: It receives a ToolUseBlock, plus an optional goal string. It trims the goal, turns the tool name and safely shortened arguments into compact JSON, builds a ModelRequest with strict summary instructions, and asks the model for a completion within a fixed timeout. If the model fails or takes too long, it records the failure and returns null. If the model responds, it passes the text through activity_line and returns the cleaned label, or null if nothing usable remains.

**Call relations**: This is the central flow of the file. It calls _bounded_arguments to keep tool arguments small, constructs Message and ModelRequest objects for the model interface, uses asyncio.timeout so the larger turn is not blocked too long, records failures through emit_metric and log, and then hands the model text to activity_line for final cleanup.

*Call graph*: calls 2 internal fn (_bounded_arguments, activity_line); 6 external calls (__init__, __init__, timeout, dumps, emit_metric, log).


##### `_bounded_arguments`  (lines 72–76)

```
def _bounded_arguments(arguments: dict[str, object]) -> str
```

**Purpose**: This helper turns a tool call’s arguments into JSON while making sure they do not get too long. It keeps the activity-summary prompt compact and avoids sending oversized argument text to the model.

**Data flow**: A dictionary of tool arguments goes in. The function converts it to compact JSON. If the result is short enough, it returns it as-is; if it is too long, it cuts it at the configured character limit and adds an ellipsis to show that it was shortened.

**Call relations**: ActivitySummarizer.summarize calls this before building the model request. Its job is to prepare the argument part of the payload so the summarizer can focus on asking for the label.

*Call graph*: called by 1 (summarize); 1 external calls (dumps).


##### `activity_line`  (lines 79–82)

```
def activity_line(text: str) -> str | None
```

**Purpose**: This helper cleans the model’s raw answer into one display-ready activity label. It removes common formatting that a model might add even though the prompt asked for plain text.

**Data flow**: A text completion goes in. The function collapses repeated whitespace, trims surrounding spaces, removes leading bullet characters, strips wrapping quotes or backticks, and removes ending punctuation such as periods or exclamation marks. It returns the cleaned label, or null if the cleaned text is empty.

**Call relations**: ActivitySummarizer.summarize calls this after the model returns text. It is the final gate between the model’s answer and the user-visible activity label.

*Call graph*: called by 1 (summarize); 1 external calls (sub).
