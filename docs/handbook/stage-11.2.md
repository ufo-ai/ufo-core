# Built-in workspace, file, shell, question, share, and skill tools  `stage-11.2`

This stage is the agent’s everyday toolbox during the main work loop. It gives the agent controlled ways to act in the workspace, talk to the user, and coordinate longer jobs. The builtins file is the front counter of the toolbox. It defines actions such as running a shell command, reading or editing files, searching project contents, sharing a finished artifact, asking the user a question, loading extra skills, requesting secrets, connecting external accounts, and starting or managing helper agents called subagents.

The tasks file is the safety clerk for shell commands, especially slow ones. Some commands may keep running after the agent stops waiting for them. Instead of launching the same command again by mistake, this code writes each command into a task journal, like a logbook. Later the agent can look up the existing command, check its output, stop it, or continue watching it. Together, these files let the agent do useful work while keeping actions trackable and less likely to be duplicated.

## Files in this stage

### Built-in tool execution
Defines the agent-facing built-in tools and the task-journal support that keeps long-running shell commands safe across retries.

### `core/src/ufo/host/tools/builtins.py`

`domain_logic` · `request handling`

This file is the toolbox that every agent gets before any extension adds more tools. It turns high-level tool calls into safe, bounded actions inside the workspace sandbox. The sandbox is the protected place where files and commands live, so this file is careful to route file access through it instead of letting the main server poke around directly.

A big theme here is safety. File reads are limited so huge files do not flood the model. Writes and edits are guarded: an existing file must be read in the current turn before it can be changed, like requiring someone to look at a document before crossing out a sentence. Shell commands can keep running in the background, but the agent gets task IDs and log paths so the work is still traceable.

The file also controls the one approved way for workspace files to leave the sandbox: share_file. It measures files, uploads them to the artifact store, records them in the database, and returns time-limited download links. Directories are packed into tar.gz archives first.

Beyond files, it supports delegation through spawn, chat-native questions through ask_user, private OAuth account connection, private credential collection, and loading reusable skill instructions. At the bottom, all these handlers are registered as ToolDef objects so the rest of the system can expose them to agents.

#### Function details

##### `_bounded_file_path`  (lines 169–172)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the tool-result envelope after it is encoded as JSON. This prevents a path from being so large that it breaks later result reporting.

**Data flow**: It receives a path string, measures the length of its JSON form, and either returns the same path unchanged or raises an error if it is too large.

**Call relations**: This is used as validation for file paths in write and edit inputs before the real file operation begins. It relies on JSON encoding because that is how the path will later travel back in tool output.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 342–373)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command in the sandbox and reports what happened. It lets long-running work continue in the background instead of killing it just because the foreground wait time ended.

**Data flow**: It receives the tool context and a command request. If background mode is requested, it hands off to _bash_background. Otherwise it rejects plain sleep-style waiting, starts the command through the task runner, and returns either command output, an error with exit code, or task handles for a still-running command.

**Call relations**: The built-in bash tool calls this when an agent asks to run a command. It uses the shared task machinery for starting commands, detecting timeout, and formatting follow-up handles; for detached starts it delegates to _bash_background.

*Call graph*: calls 1 internal fn (_bash_background); 7 external calls (__init__, __init__, format, flat_sleeps, run_task, task_handles, timeout_notice).


##### `_bash_background`  (lines 376–390)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command and immediately returns the information needed to find its logs and status later. It is for work the agent does not want to wait on right now.

**Data flow**: It receives the context and command text, creates a new task ID and runtime directory, asks the sandbox to launch the command detached, then returns task handles if launch succeeded or an error message if it did not.

**Call relations**: bash_handler calls this when the bash tool is used with background=true. It shares the same handle format as foreground commands that outlive their wait budget, so the rest of the system can treat both cases alike.

*Call graph*: called by 1 (bash_handler); 4 external calls (__init__, __init__, task_handles, task_id).


##### `_require_str`  (lines 393–396)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Pulls a required string field out of a result and fails clearly if it is missing or empty. It is a small guard against malformed sandbox responses.

**Data flow**: It receives an unknown value and a field name. If the value is a non-empty string, it returns it; otherwise it raises an error naming the missing field.

**Call relations**: read_handler and _document_result use this when turning image or document data from the sandbox into tool content. It keeps bad file-read results from silently becoming broken output.

*Call graph*: called by 2 (_document_result, read_handler).


##### `_document_result`  (lines 399–442)

```
def _document_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a document read result, such as a PDF or slide deck, into text and image blocks the agent can inspect. It also adds helpful page or slide range notes.

**Data flow**: It receives a dictionary from the sandbox’s file reader. It collects extracted text, page-count information, notes, and rendered page images, validates required fields, and returns a ToolResult containing text and/or images.

**Call relations**: read_handler calls this when the sandbox says the file is a PDF, PowerPoint, Word document, or spreadsheet. It uses _require_str to make sure image media type and data are actually present before building image content.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 445–482)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a file from the sandbox and formats it for the agent, whether it is text, an image, or a supported document. It also records that this path has been read so later edits or overwrites can be allowed safely.

**Data flow**: It receives a file path plus optional offset and limit. It asks the sandbox’s ufo fs reader for bounded content, records the path in ctx.read_paths, then returns image content, document content, an empty-file note, or text with line-range information.

**Call relations**: The built-in read tool calls this when an agent wants to inspect a file. For document results it hands off to _document_result, and for image fields it uses _require_str to validate the sandbox response.

*Call graph*: calls 2 internal fn (_document_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 485–507)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Writes text to a workspace file while preventing blind overwrites. If the target already exists, the path must have been read earlier in the same turn.

**Data flow**: It receives the desired path and text. It stages the bytes into a temporary workspace file, asks the sandbox file tool to write them to the final path with the read-before-overwrite rule, adds size and line-count details, formats the result, and marks the path as read afterward.

**Call relations**: The built-in write tool calls this. It uses _file_tool_result to keep the returned status small and machine-readable, and it uses a fresh UUID so each staged write file has a unique name.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (uuid4).


##### `edit_handler`  (lines 510–523)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to a file, but only after that file has been read in the current turn. This protects against changing content the agent has not seen.

**Data flow**: It receives a file path and one or more replacement instructions. It first checks ctx.read_paths, encodes old and new strings safely as base64, asks the sandbox file tool to apply the edits in order, then returns a compact result.

**Call relations**: The built-in edit tool calls this. It hands final formatting to _file_tool_result, while the actual file mutation is done by the sandbox’s ufo fs edit command.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 526–540)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats write and edit results so they stay within the tool-output size limit. It reports what happened without returning a huge diff or full file content.

**Data flow**: It receives a result dictionary, serializes it as compact JSON, and returns it if it fits. If it is too large, it removes the snippet and shortens the message; if it still cannot fit, it raises an error.

**Call relations**: write_handler and edit_handler call this after the sandbox finishes changing a file. It centralizes the size limit so both tools behave the same way.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 543–549)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files whose paths match a glob pattern, such as '**/*.py'. A glob is a simple file-name pattern with wildcards.

**Data flow**: It receives a pattern and optional starting directory. It asks the sandbox’s ufo fs glob command to search from that directory or the workspace root, then returns the matching paths as JSON text.

**Call relations**: The built-in glob tool calls this. The actual directory walk happens inside the sandbox, so only the bounded list of matches comes back to core.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 552–570)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents for a regular expression, which is a text pattern that can match many possible strings. It is the safer built-in alternative to running grep or ripgrep manually through bash.

**Data flow**: It receives the search pattern and optional filters such as directory, file glob, context lines, case sensitivity, output mode, and result limit. It builds parameters for the sandbox search command and returns the bounded JSON result.

**Call relations**: The built-in grep tool calls this. It delegates the heavy scanning to the sandbox’s ufo fs grep command and only brings back the summarized matches.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 573–607)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies one already-measured file from the sandbox into the artifact store. The artifact store is where shared files live so users can download them.

**Data flow**: It receives the sandbox path, destination blob key, file size, and SHA-256 digest. For S3 storage, it creates a tightly limited upload URL and makes the sandbox upload directly to it; for filesystem storage, it streams bytes from the sandbox into the local blob store.

**Call relations**: _staged_share calls this after preflight has measured the file. It is the upload step in the share_file pipeline and is deliberately separated from naming, measuring, and database recording.

*Call graph*: called by 1 (_staged_share); 3 external calls (b64encode, quote, shell_path).


##### `_shared_preview`  (lines 619–675)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str) -> ArtifactPreview | None
```

**Purpose**: Tries to create a small PNG preview image for shareable document types, such as PDFs, spreadsheets, slides, and Markdown. If preview creation fails, the file still shares successfully.

**Data flow**: It receives the sandbox file path and safe download name. If the extension is previewable and S3 storage is available, it asks the preview service to render the first page into a presigned upload location, parses the returned size, and returns preview metadata; otherwise it returns None.

**Call relations**: _staged_share calls this after the main file has been stored. It logs preview failures instead of stopping the share, because a missing thumbnail should not block the user from getting the file.

*Call graph*: called by 1 (_staged_share); 8 external calls (__init__, dumps, loads, PurePosixPath, quote, log, shell_path, uuid4).


##### `_packed_directory`  (lines 693–722)

```
async def _packed_directory(ctx: ToolContext, scoped: str) -> str | None
```

**Purpose**: Turns a directory into a tar.gz archive before sharing it. This lets users share a folder as one downloadable file without the agent needing to create the archive itself.

**Data flow**: It receives a sandbox path. It checks whether the path is a real directory, creates an output archive path if so, runs tar inside the sandbox, and returns the archive path; if the original path is not a directory, it returns None.

**Call relations**: _staged_share calls this before measuring a share target. If it returns an archive path, the rest of the sharing pipeline treats that archive as the file to upload.

*Call graph*: called by 1 (_staged_share); 3 external calls (quote, shell_path, uuid4).


##### `_staged_share`  (lines 725–767)

```
async def _staged_share(ctx: ToolContext, spec: SharedFileSpec) -> _StagedShare
```

**Purpose**: Prepares one requested file for sharing: it normalizes the name, packs directories, measures the file, uploads it, and optionally creates a preview. It does all this before any database row is written.

**Data flow**: It receives one SharedFileSpec from the tool input. It confines the path to the workspace, packs it if it is a directory, runs a preflight command to get size, digest, and text-likeness, chooses a safe filename, stores the artifact, attempts a preview, and returns a _StagedShare record with everything later steps need.

**Call relations**: share_file_handler calls this once for each file in the request. It is the middle of the share_file machine, coordinating _packed_directory, _store_artifact, and _shared_preview.

*Call graph*: calls 3 internal fn (_packed_directory, _shared_preview, _store_artifact); called by 1 (share_file_handler); 7 external calls (__init__, loads, guess_type, PurePosixPath, shell_path, workspace_path, uuid4).


##### `share_file_handler`  (lines 770–842)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Shares one or more workspace files with the user by storing them as artifacts, recording them in the database, and returning temporary download links. This is the approved doorway from the sandbox back to the outside world.

**Data flow**: It receives a list of files to share. It first stages every file, then inserts shared-artifact rows in one database transaction with timestamps that preserve the requested order, builds stable artifact object names, mints expiring URLs, and returns a JSON list describing each shared file.

**Call relations**: The built-in share_file tool calls this. It relies on _staged_share for all per-file upload work, then uses the database and artifact URL helpers so chat surfaces and artifact routes can serve the files later.

*Call graph*: calls 1 internal fn (_staged_share); 13 external calls (__init__, __init__, now, timedelta, dumps, insert, select, workspace_tx, artifact_object_names, artifact_media_type (+3 more)).


##### `_spawn_handles`  (lines 857–863)

```
def _spawn_handles(target: str, turn_id: UUID, moved: bool) -> str
```

**Purpose**: Creates the message returned when a child agent run is continuing in the background. It tells the caller which spawn ID to use for follow-up or cancellation.

**Data flow**: It receives the target name, child turn ID, and whether the spawn was moved to the background because a new message arrived. It chooses the right explanation text, builds a small JSON status payload, and returns one combined string.

**Call relations**: spawn_handler calls this whenever a spawn has no immediate output. Keeping this wording in one helper makes requested background spawns and automatically detached spawns report themselves consistently.

*Call graph*: called by 1 (spawn_handler); 1 external calls (dumps).


##### `spawn_handler`  (lines 866–909)

```
async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult
```

**Purpose**: Delegates a typed subtask to another agent or subagent profile. It can wait for the child result, start it in the background, or let it keep running if the conversation needs attention first.

**Data flow**: It receives the target name, payload, background flag, and display name. It asks ctx.spawn to create and run the child turn, converts unknown or ambiguous target errors into tool errors, and returns either a question from the child, background handles, or the child’s validated output.

**Call relations**: The built-in spawn tool calls this. It delegates the real child-turn work to ToolContext.spawn and uses _spawn_handles when the child is still running instead of returning a final answer now.

*Call graph*: calls 1 internal fn (_spawn_handles); 4 external calls (__init__, __init__, spawn, dumps).


##### `ask_user_handler`  (lines 917–928)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Packages one or more questions the agent should ask the user in chat. It is not an out-of-band pop-up; the agent includes the question in its reply and waits for the next user message.

**Data flow**: It receives a structured question request. It builds a JSON payload with title, optional icon, and question definitions, prefixes it with an instruction to ask and end the turn, and returns that text as the tool result.

**Call relations**: The built-in ask_user tool calls this when an agent needs missing information or confirmation. Rich chat surfaces can read the structured payload, while the model also gets a plain directive to present it faithfully.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 931–943)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill’s instructions and files, including any skills it depends on. A skill is a reusable bundle of workflow guidance and supporting assets.

**Data flow**: It receives a skill name. It resolves the full dependency closure through the context, materializes those skill files, installs them into the sandbox, builds the text context for the agent, and returns it.

**Call relations**: The built-in load_skill tool calls this. It uses the skills runtime helpers to put files where the sandbox can read them and to format the instructions that should enter the agent’s working context.

*Call graph*: 4 external calls (__init__, __init__, load_skills, loaded_context).


##### `_grantee_agent_id`  (lines 952–976)

```
async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None
```

**Purpose**: Figures out whether an external account connection should be granted to another agent, and enforces who is allowed to make that choice. Only the workspace’s main agent may connect an account on behalf of a different agent.

**Data flow**: It receives the tool context and a requested agent name. If the name is empty, it returns None, meaning the current agent. Otherwise it reads active agents from the workspace database, checks that the asking agent is the main one, looks up the named target, and returns that target’s ID unless it is the current agent.

**Call relations**: connect_account_handler calls this before creating the connection request. By resolving the target here, the later private authorization handoff carries a fixed grantee rather than re-deciding it later.

*Call graph*: called by 1 (connect_account_handler); 2 external calls (select, workspace_tx).


##### `connect_account_handler`  (lines 979–993)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private OAuth account-connection flow for the speaking member. OAuth is the common web sign-in process where a user authorizes access without giving the app their password.

**Data flow**: It receives a provider name, sharing choice, and optional target agent. It verifies there is a speaking member, resolves any target agent, validates that the provider is installed, builds a ConnectRequest, and returns instructions plus the structured request.

**Call relations**: The built-in connect_account tool calls this. It relies on _grantee_agent_id for cross-agent grants and on the installed connect flow to reject unknown providers before the user is asked to authorize anything.

*Call graph*: calls 1 internal fn (_grantee_agent_id); 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 1003–1026)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks a workspace admin to enter secrets, such as API keys, through a private prompt instead of chat. This keeps sensitive values out of the conversation transcript.

**Data flow**: It receives a reason and a small list of credential prompts. It checks that there is a speaking member, that secret storage is configured, and that the speaker is an admin; then it seals the allowed slots into a signed request and returns instructions plus a CredentialRequest.

**Call relations**: The request_credentials action calls this when an agent needs user-supplied secrets. It uses ToolContext.speaker_is_admin to enforce permissions and the credential sealing object on the context to make sure only the named slots can be fulfilled.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `cancel_spawn_handler`  (lines 1029–1041)

```
async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult
```

**Purpose**: Cancels a child agent run that this turn started, or reports its status if it has already finished. It gives the parent a way to stop delegated background work.

**Data flow**: It receives a spawn ID string. It verifies spawn control is available, converts the ID into a UUID, asks the subagents controller to cancel it, and returns JSON with the spawn ID and current status.

**Call relations**: The built-in cancel_spawn tool calls this. It hands the actual cancellation to ctx.subagents, which enforces that the parent can only control its own spawned children.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_spawn_handler`  (lines 1044–1062)

```
async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a running or waiting child agent. This is how a parent can answer a child’s question or give new instructions without starting over.

**Data flow**: It receives a spawn ID and message text. It checks that spawn control and an idempotency key are present, converts the spawn ID to a UUID, queues the message through the subagents controller, and returns JSON with the spawn ID and new status.

**Call relations**: The built-in message_spawn tool calls this. It delegates to ctx.subagents.message, passing the idempotency key so repeated delivery of the same tool call does not accidentally send duplicate follow-ups.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### `core/src/ufo/runtime/tools/tasks.py`

`domain_logic` · `tool execution and timeout handling`

This file solves a common problem for tool-driven shell work: a command may still be useful even if it does not finish within the caller's waiting time. Instead of killing that command when the wait expires, the system launches it in a detached task journal. Think of it like leaving a job ticket at a workshop: if the customer walks away, the work can continue, and the ticket tells them where to check later.

The journal lives under the run's task directory in the sandbox. Each command gets a short task id, log file, process id file, and exit file. The log is where output accumulates. The exit file appears once, when the command finishes or is stopped. That gives every tool the same simple contract: read the log for progress, watch the exit file for completion, and use the stop command if needed.

The file also prevents one bad pattern: long plain `sleep` commands in foreground work. Those waste the caller's turn by doing nothing, so `flat_sleeps` detects them unless they are inside loops, quotes, or heredoc data.

A key detail is durability. If a dispatch step is retried after a crash, the same idempotency key produces the same task id, so the system reconnects to the earlier command instead of launching a duplicate. When a timeout looks like a sandbox failure rather than a command still running, the file records basic container health information for diagnosis without changing the result returned to the caller.

#### Function details

##### `flat_sleeps`  (lines 33–51)

```
def flat_sleeps(command: str) -> tuple[int, ...]
```

**Purpose**: This function looks for long, plain `sleep` commands that would waste a foreground wait. It ignores sleeps inside shell loops, quoted text, and heredoc blocks, because those are often part of polling logic or data rather than idle padding.

**Data flow**: It takes a shell command string, removes quoted and heredoc sections from what it scans, then walks through matches for `sleep`, `do`, and `done`. It tracks whether it is currently inside a loop; only sleeps outside loops are counted. It returns the sleep lengths that are greater than the allowed small limit.

**Call relations**: This is a guard helper for tools that decide whether a command should be allowed to run in the foreground. It does not launch anything itself; it provides a simple warning signal that callers can use before handing work to the task runner.


##### `run_task`  (lines 94–131)

```
async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None) -> TaskRun
```

**Purpose**: This is the main launcher for shell work in this file. It starts a command through the sandbox task journal, waits only for the caller's allowed time, and reports whether the command finished or is still running detached.

**Data flow**: It receives a tool context, a command, and an optional timeout in milliseconds. It turns the timeout into seconds, caps it at the system maximum, derives a task id, and asks the sandbox to run the command with journal files under the task directory. If the command finishes within the wait, it returns a `TaskRun` with the result and no running process id. If the wait expires, it probes the task files to see whether the detached supervisor is still alive or already produced an exit file. If the probe finds no live or completed task, it records diagnostic timeout information, then returns a `TaskRun` describing the outcome.

**Call relations**: This function is the center of the file's flow. It calls `task_id` so retries can reconnect to the same journal entry, constructs the returned `TaskRun`, and calls `_record_exec_timeout` only when the sandbox stopped answering in a way that does not look like normal detached work.

*Call graph*: calls 2 internal fn (_record_exec_timeout, task_id); 1 external calls (__init__).


##### `task_id`  (lines 134–141)

```
def task_id(ctx: ToolContext) -> str
```

**Purpose**: This function chooses the short name used for a task's journal files. Its main job is to make retried dispatch steps find the same task again, while unrelated calls get fresh names.

**Data flow**: It reads the `idempotency_key` from the tool context. If there is no key, it creates a random short id. If there is a key, it hashes that key and uses the first part of the hash, so the same key always leads to the same task id.

**Call relations**: It is called by `run_task` before launching shell work. That lets `run_task` either reconnect to an existing task from an earlier attempt or create a new task when there is no retry identity available.

*Call graph*: called by 1 (run_task); 2 external calls (sha256, uuid4).


##### `task_handles`  (lines 144–165)

```
def task_handles(task: str, pid: str, display_base: str, applied_s: int | None=None, note: str='') -> str
```

**Purpose**: This function builds the message a caller sees when a command is running detached. It tells the caller where to read output, how to detect completion, and how to stop the command.

**Data flow**: It takes the task id, supervisor process id, display path for the task files, an optional expired timeout, and an optional note. It chooses a lead sentence depending on whether the command was detached from the start or moved to the background after timing out. Then it builds a small JSON payload containing the task name, pid, log path, exit file path, watch command, and stop command, and returns that as readable text.

**Call relations**: This is a formatting helper used by tool-facing code when it needs to explain a detached task. It shares the same wording for background tasks and timed-out-but-still-running tasks so different tools give users the same instructions.

*Call graph*: 1 external calls (dumps).


##### `timeout_notice`  (lines 168–184)

```
def timeout_notice(applied_s: int, requested_s: int | None) -> str
```

**Purpose**: This function writes a clear explanation for a command timeout. It distinguishes the default timeout, a caller-requested timeout, and a requested timeout that was reduced by the system maximum.

**Data flow**: It receives the timeout that actually applied and the timeout the caller requested, if any. It compares them and returns a sentence explaining why the command stopped and, when relevant, that the requested wait was capped.

**Call relations**: This is a user-message helper for timeout results. It does not inspect the running task; it gives surrounding tool code a reliable way to explain what deadline was responsible.


##### `_record_exec_timeout`  (lines 187–220)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: This function records diagnostic information when a command timeout looks like a sandbox execution problem rather than a normal long-running detached command. It is for operators and debugging, not for changing what the user is told.

**Data flow**: It receives the tool context, the command, the timeout that applied, and the originally requested timeout. It briefly asks the sandbox for basic health readings such as load, memory, and workspace disk space. Whether that probe succeeds or fails, it writes a structured log entry with the profile, timeout values, a shortened copy of the command, whether vitals were reached, and any vitals text collected. It swallows its own errors so the original timeout result is still returned.

**Call relations**: It is called by `run_task` only after a timed-out launch cannot be confirmed as still alive or completed through the task probe. Inside, it uses the observability logging helpers so later investigation can tell whether the sandbox itself stopped responding.

*Call graph*: called by 1 (run_task); 3 external calls (timeout, log, turn_profile).
