# Built-in interactive and stateful tools  `stage-10.2`

This stage supplies the agent’s everyday work tools during the main conversation loop. It is like a supervised workshop: the agent can run commands, inspect and change files, ask for help, and keep notes, but only through controlled paths that protect the host system.

The builtins file is the main tool counter. It turns an agent’s request into safe actions: running shell commands in a sandbox, reading or editing workspace files, sharing files as artifacts, asking the user questions, delegating to subagents, loading skills, and collecting credentials when setup is needed. The tasks file adds memory for long-running shell work. Instead of losing track when a command takes time, it writes a task journal with the command’s log, process id, and final result, so later checks continue the same task rather than starting over.

Two extensions add durable working state. The REPL extension lets the agent run ongoing Python or JavaScript code sessions, keeping successful code available for later steps. The todos extension gives the agent a checklist it can update and show, helping multi-step work stay organized.

## Files in this stage

### Core tool bridge and task journal
Built-in tools route agent actions through safe workspace services, with shell work backed by durable task records.

### `core/src/ufo/tools/builtins.py`

`orchestration` · `tool invocation during a turn`

This file is like the agent’s toolbox and the rules printed on the inside of the toolbox lid. It defines what arguments each built-in tool accepts, what each tool is allowed to do, and how results are returned to the agent.

A major theme is safety. File operations go through the sandbox, which is an isolated workspace container. The host process does not casually pull whole files into memory. Reading records which paths the agent has actually seen, and writing or editing existing files is refused unless the file was read first. This helps stop blind overwrites.

The file-sharing path is also carefully controlled. A workspace file is measured, uploaded to the artifact store, recorded in the database, and then returned as a temporary download link. Direct workspace files are not visible to the outside world unless this tool shares them.

The file also supports longer-running work. Shell commands and spawned subagents can continue in the background, returning IDs that can later be watched, messaged, or cancelled. User interaction is chat-native: questions, OAuth account connections, and secret collection all end the current turn and let the chat surface collect the answer or private data safely.

At the end, `BUILTIN_TOOLS` registers all of these handlers so the rest of the system can expose them as named tools.

#### Function details

##### `_bounded_file_path`  (lines 175–178)

```
def _bounded_file_path(path: str) -> str
```

**Purpose**: Checks that a file path will still fit inside the small JSON result envelope used by file-change tools. This prevents an extremely long path from breaking later reporting.

**Data flow**: It receives a path string, converts it to JSON text to measure its real encoded size, and either returns the same path unchanged or raises an error if it is too large.

**Call relations**: This is used as a validator for file path input models. It relies on JSON encoding because the path will later travel inside JSON, so the check matches the way the data is actually sent.

*Call graph*: 1 external calls (dumps).


##### `bash_handler`  (lines 382–408)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandboxed workspace. It lets the caller either wait for the command for a limited time or start it in the background right away.

**Data flow**: It receives the tool context and command settings. If background mode is requested, it hands off to `_bash_background`. Otherwise it starts a recorded task, waits up to the requested time, and returns command output, an error with the exit code, or task handles if the command is still running.

**Call relations**: This is the public handler for the `bash` tool. It uses the task system to run commands and format timeout or background information, and it delegates the pure detach case to `_bash_background`.

*Call graph*: calls 1 internal fn (_bash_background); 5 external calls (__init__, __init__, run_task, task_handles, timeout_notice).


##### `_bash_background`  (lines 411–423)

```
async def _bash_background(ctx: ToolContext, command: str) -> ToolResult
```

**Purpose**: Starts a shell command and immediately detaches from it, so the agent can continue without waiting. It returns the task ID, log path, and process ID needed to track the command later.

**Data flow**: It receives a command, creates a task identity from the current context, asks the sandbox to launch the command detached, and returns either an error if launch failed or a text block containing the task handles.

**Call relations**: It is called by `bash_handler` when the user requested background execution. It uses the same task helpers as foreground bash runs, so detached and timed-out commands report handles in the same style.

*Call graph*: called by 1 (bash_handler); 5 external calls (__init__, __init__, task_base, task_handles, task_id).


##### `_require_str`  (lines 426–429)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Validates that a value returned by the sandbox is a non-empty string. It is a small guard that turns malformed sandbox output into a clear error.

**Data flow**: It receives an unknown value and the name of the field being checked. If the value is a real non-empty string, it returns it; otherwise it raises an error naming the missing field.

**Call relations**: It is used by `read_handler` and `_pdf_result` when building image or PDF responses. Those functions need dependable strings for media types and encoded image data before sending content back to the agent.

*Call graph*: called by 2 (_pdf_result, read_handler).


##### `_pdf_result`  (lines 432–477)

```
def _pdf_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a sandbox result for a PDF or presentation into agent-readable content: text plus page or slide images. It also adds helpful page-window notes when only part of the document was returned.

**Data flow**: It receives a dictionary produced by the sandbox file reader. It extracts text, page counts, notes, and rendered page images, checks required image fields with `_require_str`, and returns a `ToolResult` containing text and image blocks.

**Call relations**: It is called by `read_handler` when the sandbox says the file is a PDF or PowerPoint-like document. It hands back a mixed text-and-image result so the agent can understand documents visually as well as through extracted text.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 480–517)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox file reader. It supports text files, images, PDFs, and presentations, and records that the path has been read so later edits or overwrites are allowed.

**Data flow**: It receives a file path plus optional offset and limit. It asks the sandbox `ufo fs` tool to read that slice, records the path in `ctx.read_paths`, and converts the sandbox result into text, image content, PDF content, or a clear empty/no-lines message.

**Call relations**: This is the public handler for the `read` tool. It calls `_pdf_result` for paginated document rendering and `_require_str` for image fields, then returns content in the format the tool system expects.

*Call graph*: calls 2 internal fn (_pdf_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 520–543)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Writes text to a workspace file safely. If the target file already exists, the sandbox is told to allow the write only if this turn has already read that path.

**Data flow**: It receives the desired path and content. It writes the content to a temporary staged file inside the sandbox, asks `ufo fs` to move it into place with the read-before-overwrite rule, adds size and line-count details, formats the result, and marks the path as read afterward.

**Call relations**: This is the public handler for the `write` tool. It uses `_file_tool_result` to keep the response small and consistent with edit results.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (uuid4).


##### `edit_handler`  (lines 546–559)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to a file that the agent has already read in this turn. This protects against changing a file the agent has not seen.

**Data flow**: It receives a file path and one or more replacements. It first checks that the path is in `ctx.read_paths`, encodes old and new strings safely as base64 text, sends the edits to the sandbox `ufo fs` editor, and returns a compact result.

**Call relations**: This is the public handler for the `edit` tool. It calls `_file_tool_result` after the sandbox applies the edits, and it uses base64 encoding so arbitrary replacement text can travel safely through JSON.

*Call graph*: calls 1 internal fn (_file_tool_result); 1 external calls (b64encode).


##### `_file_tool_result`  (lines 562–576)

```
def _file_tool_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Formats write and edit results as bounded JSON text. It avoids returning huge snippets or diffs that could overflow the tool result limit.

**Data flow**: It receives a result dictionary, serializes it as compact JSON, and returns it if it fits. If it is too large, it removes the snippet and shortens the message, then either returns the smaller JSON or raises an error if it still cannot fit.

**Call relations**: It is called by both `write_handler` and `edit_handler`. This gives both file-changing tools the same response shape and the same size protection.

*Call graph*: called by 2 (edit_handler, write_handler); 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 579–585)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds files in the workspace whose paths match a glob pattern, such as `**/*.py`. The search runs inside the sandbox instead of on the host.

**Data flow**: It receives a pattern and optional starting directory, defaults the search to the workspace root, asks sandbox `ufo fs` to perform the match, and returns the matching paths as JSON text.

**Call relations**: This is the public handler for the `glob` tool. It keeps file discovery inside the container and only brings back the bounded list of results.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 588–606)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches workspace file contents for a regular expression. A regular expression is a search pattern that can match flexible text, not just one exact word.

**Data flow**: It receives the search pattern plus optional file filters, context lines, case sensitivity, output mode, and result limit. It builds the sandbox search request, runs `ufo fs grep`, and returns the bounded search results as JSON text.

**Call relations**: This is the public handler for the `grep` tool. It sends all scanning work into the sandbox and returns only the summarized matches.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 609–643)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies one already-measured workspace file into the artifact store. The artifact store is where files live when they are made downloadable outside the sandbox.

**Data flow**: It receives the sandbox path, destination blob key, file size, and SHA-256 digest. For S3 storage, it creates a signed upload URL tied to that exact size and digest and has the sandbox upload directly. For local filesystem storage, it streams the file through the blob store.

**Call relations**: It is called by `_staged_share` after the file has passed preflight checks. It is the point where bytes leave the sandbox-controlled workspace and enter durable artifact storage.

*Call graph*: called by 1 (_staged_share); 2 external calls (b64encode, quote).


##### `_shared_preview`  (lines 655–711)

```
async def _shared_preview(ctx: ToolContext, scoped: str, safe_name: str) -> ArtifactPreview | None
```

**Purpose**: Tries to create a preview image for certain shared document types, such as PDFs or spreadsheets. The preview helps a user see what a shared file is without opening it first.

**Data flow**: It receives the sandbox file path and safe display name. If the file type supports previews and the blob backend is S3, it creates a preview upload URL, asks the preview service to render the first page, parses the returned size, and returns preview metadata; if anything fails, it logs the reason and returns no preview.

**Call relations**: It is called by `_staged_share` after the main file has been stored. Preview failure does not stop sharing, because the actual file is more important than its thumbnail.

*Call graph*: called by 1 (_staged_share); 7 external calls (__init__, dumps, loads, PurePosixPath, quote, log, uuid4).


##### `_packed_directory`  (lines 729–762)

```
async def _packed_directory(ctx: ToolContext, scoped: str) -> str | None
```

**Purpose**: Turns a shared directory into a `.tar.gz` archive inside the sandbox. This lets the share system treat directories like single downloadable files.

**Data flow**: It receives a scoped sandbox path. It first checks whether the path is a real directory; if not, it returns `None`. If it is a directory, it creates an archive in the tool output area, avoids packing that scratch area into itself when needed, and returns the archive path.

**Call relations**: It is called by `_staged_share` before file measurement and upload. This means the rest of the sharing pipeline can handle both regular files and directories through the same artifact path.

*Call graph*: called by 1 (_staged_share); 4 external calls (quote, removeprefix, startswith, uuid4).


##### `_staged_share`  (lines 765–807)

```
async def _staged_share(ctx: ToolContext, spec: SharedFileSpec) -> _StagedShare
```

**Purpose**: Prepares one requested file or directory for sharing. It packages directories, measures the final file, chooses a safe download name, stores the bytes, and optionally creates a preview.

**Data flow**: It receives one share specification from the tool call. It scopes the path to the workspace, packs directories if needed, runs a sandbox preflight to get size, digest, and text/binary status, sanitizes the output name, uploads the file through `_store_artifact`, asks `_shared_preview` for a thumbnail when possible, and returns a `_StagedShare` record.

**Call relations**: It is called by `share_file_handler` once per requested file. It combines `_packed_directory`, `_store_artifact`, and `_shared_preview` so the outer handler can later commit database rows only after all files are safely staged.

*Call graph*: calls 3 internal fn (_packed_directory, _shared_preview, _store_artifact); called by 1 (share_file_handler); 7 external calls (__init__, loads, guess_type, PurePosixPath, quote, workspace_path, uuid4).


##### `share_file_handler`  (lines 810–882)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Makes workspace files available outside the sandbox as temporary download links. This is the official path for sending produced files to the user.

**Data flow**: It receives a list of files to share. It stages every file first, opens a workspace database transaction, records each shared artifact in order, computes stable artifact object names, mints expiring download URLs, and returns a JSON list with names, URLs, sizes, digests, and text/binary hints.

**Call relations**: This is the public handler for the `share_file` tool. It calls `_staged_share` for each file before writing database records, so one bad file prevents a partial share.

*Call graph*: calls 1 internal fn (_staged_share); 12 external calls (__init__, __init__, now, timedelta, dumps, insert, select, artifact_media_type, mint_artifact_url, artifact_object_names (+2 more)).


##### `_spawn_handles`  (lines 897–903)

```
def _spawn_handles(target: str, turn_id: UUID, moved: bool) -> str
```

**Purpose**: Builds the message returned when a spawned subagent is running in the background. It gives the agent the spawn ID and explains how the result will arrive later.

**Data flow**: It receives the target name, child turn ID, and whether the spawn was moved to the background because a new message arrived. It chooses the right explanatory lead text, creates a small JSON status payload, and returns the combined text.

**Call relations**: It is called by `spawn_handler` when a child turn has no immediate output. It keeps background-spawn wording consistent for both explicitly backgrounded work and work detached because the conversation moved on.

*Call graph*: called by 1 (spawn_handler); 1 external calls (dumps).


##### `spawn_handler`  (lines 906–949)

```
async def spawn_handler(ctx: ToolContext, args: SpawnInput) -> ToolResult
```

**Purpose**: Delegates a subtask to a named subagent profile or workspace agent. It can wait for a validated result, start the child in the background, or report that the child asked a question.

**Data flow**: It receives the target, payload, background flag, and display name. It asks `ctx.spawn` to create and run the child turn, catches unknown or ambiguous target errors, then returns either the child’s question, background handles from `_spawn_handles`, or the child’s validated JSON output.

**Call relations**: This is the public handler for the `spawn` tool. It relies on the context’s spawn system for the actual child turn and uses `_spawn_handles` when the parent should stop waiting.

*Call graph*: calls 1 internal fn (_spawn_handles); 4 external calls (__init__, __init__, spawn, dumps).


##### `ask_user_handler`  (lines 957–968)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Packages a question the agent should ask the user in its next chat reply. It tells the agent to end the turn afterward so the answer can arrive as the next message.

**Data flow**: It receives a structured question request. It builds a JSON payload containing the title, optional icon, and question records, prefixes it with a plain instruction, and returns it as tool text.

**Call relations**: This is the public handler for the `ask_user` tool. It does not contact the user directly; instead, it gives the agent and chat surface a structured question to present.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 971–982)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a named skill into the workspace. A skill is a bundle of instructions and files that teaches the agent how to do a specialized kind of work.

**Data flow**: It receives a skill name, resolves that skill plus its dependencies, mounts each skill’s files into the sandbox workspace, builds the combined instruction context, and returns it as text.

**Call relations**: This is the public handler for the `load_skill` tool. It calls the skill runtime to mount files and prepare the readable workflow text the agent will follow.

*Call graph*: 4 external calls (__init__, __init__, loaded_context, install_skill).


##### `_grantee_agent_id`  (lines 991–1014)

```
async def _grantee_agent_id(ctx: ToolContext, name: str) -> UUID | None
```

**Purpose**: Figures out which agent should receive access to a newly connected external account. It enforces the rule that only the main workspace agent may connect an account on behalf of another agent.

**Data flow**: It receives an optional agent name. If no name is given, it returns `None`, meaning the current agent is the grantee. If a name is given, it reads workspace agents from the database, checks that the asking agent is the main one, resolves the named target, and returns that target’s ID unless it is the current agent.

**Call relations**: It is called by `connect_account_handler` before creating the connection request. By resolving the grantee early, the later private authorization handoff does not have to reinterpret who should receive the grant.

*Call graph*: called by 1 (connect_account_handler); 2 external calls (select, workspace_tx).


##### `connect_account_handler`  (lines 1017–1031)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private OAuth account-connection flow for the speaking member. OAuth is the common web sign-in handoff used to let an app access an account without seeing the password.

**Data flow**: It receives the provider name, sharing choice, optional grantee agent, and context. It verifies there is a speaking member, resolves any grantee agent with `_grantee_agent_id`, validates the provider, builds a connection request, and returns instructions telling the agent to direct the member to the private connection control.

**Call relations**: This is the public handler for the `connect_account` tool. It uses the installed connection-flow validator and `_grantee_agent_id`, then returns a structured request rather than exposing an authorization URL in chat.

*Call graph*: calls 1 internal fn (_grantee_agent_id); 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 1041–1064)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Requests secrets such as API keys through a private prompt instead of chat. It ensures only a workspace admin can fill these credential slots.

**Data flow**: It receives a reason and credential prompts. It checks that there is a speaking member, that credential storage is configured, and that the speaker is an admin. It seals the workspace, member, and requested slots into a protected token, builds a credential request, and returns instructions plus JSON for the chat surface.

**Call relations**: This is the public handler for the `request_credentials` tool. It calls the context’s admin check and uses the credential sealing system so secret values never enter the conversation transcript.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `cancel_spawn_handler`  (lines 1067–1079)

```
async def cancel_spawn_handler(ctx: ToolContext, args: CancelSpawnInput) -> ToolResult
```

**Purpose**: Cancels a running child spawn, or reports its current status if it has already finished. It refuses to work when spawn control is unavailable.

**Data flow**: It receives a spawn ID string. It converts it to a UUID, asks `ctx.subagents` to cancel that child, and returns JSON containing the spawn ID and resulting status.

**Call relations**: This is the public handler for the `cancel_spawn` tool. It works through the same subagent control system that backs `spawn`, so cancellation is limited to children this turn is allowed to control.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_spawn_handler`  (lines 1082–1100)

```
async def message_spawn_handler(ctx: ToolContext, args: MessageSpawnInput) -> ToolResult
```

**Purpose**: Sends a follow-up message to a spawned child turn. This is how the parent can answer a child’s question or give more instructions to a background spawn.

**Data flow**: It receives a spawn ID and message. It checks that subagent control and an idempotency key are available, converts the ID to a UUID, queues the message through `ctx.subagents`, and returns JSON with the spawn ID and status.

**Call relations**: This is the public handler for the `message_spawn` tool. It uses the subagent system to schedule the message as the child’s next turn, and the child’s later result is delivered back to the conversation.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### `core/src/ufo/tools/tasks.py`

`domain_logic` · `request handling`

Many tools need to run shell commands, but not every command finishes quickly. This file makes those commands behave like checked-out library books: each one gets a named record, and anyone with that record can see where it is, whether it finished, and how to stop it. The record lives in `.tasks/` inside the workspace, using three files: a log file for output, a pid file for the running wrapper process, and an exit file written when the command ends.

The important idea is that commands are launched detached from the caller. “Detached” means the command can keep running even if the code that started it stops waiting or is restarted. If the command finishes within the caller’s time budget, the caller gets the normal output and exit code. If it is still running when the wait time runs out, the command is not killed. Instead, the caller gets handles: paths and small commands for reading the log, watching for completion, or stopping the task.

The file also prevents duplicate work after a crash. When a tool call has an idempotency key, this file turns that key into the same task name every time. If the dispatch step is replayed, it reconnects to the first command rather than launching a second copy. If the sandbox stops responding, the file records a short diagnostic snapshot so operators can tell the difference between a slow command and a broken execution channel.

#### Function details

##### `run_task`  (lines 117–147)

```
async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None) -> TaskRun
```

**Purpose**: This is the main entry point for running a shell command through the task journal. It starts the command in a detached wrapper, waits only as long as the caller allowed, and then returns either the normal command result or enough information to reconnect to the still-running task.

**Data flow**: It receives a tool context, a command string, and an optional timeout in milliseconds. It converts the timeout into seconds, caps it at the system maximum, derives a task name, and asks the sandbox to run the launch-and-wait script. If the command finishes in time, it returns a `TaskRun` with the command result and no pid. If the wait expires, it probes the task journal to see whether the detached command is still alive; if so, it returns the wrapper pid, and if not, it records timeout diagnostics before returning.

**Call relations**: This function pulls the flow together. It asks `task_id` for the durable task name, uses `task_base` to point the shell scripts at the right workspace files, and calls into the sandbox to launch and wait. Only when the wait times out and the probe cannot find a living or completed task does it call `_record_exec_timeout`, because that case suggests the sandbox execution channel itself may have failed.

*Call graph*: calls 3 internal fn (_record_exec_timeout, task_base, task_id); 1 external calls (__init__).


##### `task_id`  (lines 150–157)

```
def task_id(ctx: ToolContext) -> str
```

**Purpose**: This function chooses the short name used for a task’s journal files. Its job is to make replayed tool calls find the same task, while ordinary untracked calls get a fresh task name each time.

**Data flow**: It reads the context’s idempotency key, which is a stable marker for a repeatable tool call. If there is no key, it creates a random short id. If there is a key, it hashes that key and uses the first part of the hash, producing the same task id for the same replayed call without exposing the original key.

**Call relations**: `run_task` calls this before launching anything, because the task id decides which `.log`, `.pid`, and `.exit` files belong to the command. This is what lets a recovery pass reattach to earlier work instead of starting the same command twice.

*Call graph*: called by 1 (run_task); 2 external calls (sha256, uuid4).


##### `task_base`  (lines 160–164)

```
def task_base(task: str) -> str
```

**Purpose**: This function builds the shared file path prefix for a task’s journal files. It makes sure every task path is anchored under the workspace, not whatever directory the sandbox shell happens to start in.

**Data flow**: It receives a task id such as `abcd1234` and combines it with the workspace directory and `.tasks` folder. The result is a base path; callers add `.log`, `.pid`, or `.exit` to refer to the command’s output, running process id, or final exit code.

**Call relations**: `run_task` uses this path when it asks the sandbox-side shell scripts where to write and read the task journal. `task_handles` uses the same helper when it tells a user or tool where to read the log, watch for completion, or stop the task. Using one helper keeps all parts of the system pointing at the same files.

*Call graph*: called by 2 (run_task, task_handles).


##### `task_handles`  (lines 167–187)

```
def task_handles(task: str, pid: str, applied_s: int | None=None, note: str='') -> str
```

**Purpose**: This function writes the human- and machine-readable message for a detached task. It tells the caller that the command is still running and gives the exact paths and commands needed to inspect or stop it.

**Data flow**: It receives a task id, the wrapper process id, an optional applied timeout, and an optional note from the calling tool. It chooses the right opening sentence: either the command was detached from the start, or it ran past the wait time and continues detached. It then builds a small JSON object containing the task id, pid, log path, exit-file path, a watch command, and a stop command, and returns all of that as text.

**Call relations**: This function depends on `task_base` so the handles point at the same journal files that `run_task` created. It is the presentation layer for the detached-task contract: other tools can show its output when they need to hand a still-running command back to the user.

*Call graph*: calls 1 internal fn (task_base); 1 external calls (dumps).


##### `timeout_notice`  (lines 190–206)

```
def timeout_notice(applied_s: int, requested_s: int | None) -> str
```

**Purpose**: This function explains, in plain text, why a command timed out. It also clarifies whether the timeout was the caller’s requested limit, the system maximum, or the default sandbox limit.

**Data flow**: It receives the timeout that actually applied and, if known, the timeout the caller requested. If no timeout was requested, it says the sandbox used its default and suggests setting one. If the caller asked for more than the allowed maximum, it says the request was capped. Otherwise, it simply reports the timeout that stopped the command.

**Call relations**: This helper is for code that needs to turn timeout facts into a clear user-facing message. It does not launch or inspect tasks itself; it explains the meaning of timeout values produced elsewhere, especially because an exit code alone may not reveal which deadline was hit.


##### `_record_exec_timeout`  (lines 209–242)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: This function records diagnostic information when a sandbox command appears to have timed out without leaving a usable detached task behind. It is not meant to change the user-facing result; it exists to help operators understand what the sandbox looked like at the failure moment.

**Data flow**: It receives the context, the command, the timeout that applied, and the timeout the caller requested. It briefly asks the sandbox for basic system health information, such as load, memory, and workspace disk usage. Whether that probe succeeds or fails, it writes a structured log event containing the profile, timeout values, a shortened copy of the command, whether the sandbox answered, and any vitals it returned.

**Call relations**: `run_task` calls this only after a wait expires and the follow-up probe cannot find a running or completed task. Inside, it uses the observability helpers `turn_profile` and `log` to attach the diagnostic event to the current kind of run. Any failure while gathering diagnostics is swallowed, because the original timeout result still needs to be reported.

*Call graph*: called by 1 (run_task); 3 external calls (timeout, log, turn_profile).


### Stateful extension tools
Extension tools add persistent interactive coding state and durable progress tracking for multi-step agent work.

### `extensions/repl/ufo_ext_repl/manifest.py`

`orchestration` · `tool registration and request handling`

This file turns two interpreters into agent tools. One runs Node.js for browser and web testing work. The other runs Python for Excel files through openpyxl, a library for reading and editing spreadsheets. The key idea is a persistent REPL, meaning each successful code block is saved and replayed before the next one, like keeping notes on a whiteboard between experiments.

The file is careful about failure. Before each run, it builds a “candidate” state file by appending the new code to the previous saved state. If the interpreter exits successfully, that candidate becomes the new saved state. If the code fails or times out, the saved state is left untouched. This prevents a half-broken variable or import from poisoning future runs.

For JavaScript, the file also prepares an emitImage helper so code can return images inline, and it links globally installed Node packages into the local workspace so imports work in ES modules. For long-running code, it uses the same detached task system as shell commands: if the caller stops waiting, the process may keep running in the background and the result includes handles to find it later. Finally, the manifest function advertises these tools and related data skills to the larger system.

#### Function details

##### `_meter_run`  (lines 74–90)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one monitoring count for each REPL run, including which tool ran and what exit code it produced. This helps operators tell the difference between user code failing and the interpreter itself being missing or broken.

**Data flow**: It receives the current tool context, the tool name, and the interpreter exit code. It turns the current subagent profile into a metric label, groups unusual exit codes under a shared “other” bucket, and sends a metric event. It does not return data; it updates observability records outside the tool result.

**Call relations**: After either REPL finishes running code, js_repl or xlsx_repl calls this before building the final response. It hands the metric system enough information to count the run without changing what the user sees.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 93–111)

```
def global_modules_link(roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds the shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This matters because ES modules do not automatically search the usual global package path.

**Data flow**: It receives a tuple of possible global Node package roots, or uses the default roots. It returns a shell command string that creates a local node_modules folder under the REPL directory and adds per-package symbolic links, which are shortcut files pointing to the real package locations. It does not run the command itself.

**Call relations**: js_repl asks this function for the setup command before launching Node. The returned command is then run in the sandbox so JavaScript code can use bare imports such as installed browser automation packages.

*Call graph*: called by 1 (js_repl).


##### `js_emit_relative`  (lines 118–126)

```
def js_emit_relative(call: str) -> str
```

**Purpose**: Creates a unique workspace-relative file path where one JavaScript REPL call can write emitted images. Using a separate file per call prevents an old background run from mixing its images into a later call.

**Data flow**: It receives a short call identifier string. It combines that identifier with the REPL emit directory and returns a relative path like a named drop box for that call’s image data. It does not touch the filesystem.

**Call relations**: js_repl calls this when preparing a JavaScript run. The returned path is embedded into the JavaScript prelude by js_emit_prelude and later read back by _emitted_images.

*Call graph*: called by 1 (js_repl).


##### `js_emit_prelude`  (lines 129–166)

```
def js_emit_prelude(emit_relative: str) -> str
```

**Purpose**: Generates the JavaScript setup code that defines globalThis.emitImage for a single run. That helper lets JavaScript code attach images to the tool response instead of only printing text.

**Data flow**: It receives the relative image output path for the current call. It returns JavaScript source text that imports file-writing helpers, defines limits, converts image inputs to base64 text, keeps only the newest few images, and rewrites a JSON-lines file each time emitImage is called.

**Call relations**: js_repl places this generated source before the user’s JavaScript code in the temporary run file. It uses JSON string escaping while building the source so the path is safely inserted into JavaScript.

*Call graph*: called by 1 (js_repl); 1 external calls (dumps).


##### `_candidate_source`  (lines 236–242)

```
async def _candidate_source(ctx: ToolContext, path: str, code: str, reset: bool) -> str
```

**Purpose**: Builds the full source code that should be tried for the next REPL run. It either starts fresh on reset or appends the new code to the last saved successful state.

**Data flow**: It receives the sandbox context, the saved state file path, the new code, and whether reset was requested. If reset is true, it removes the old state file. If no saved state exists, it returns just the new code plus a newline. Otherwise, it reads the existing saved source and returns existing source followed by the new code.

**Call relations**: Both js_repl and xlsx_repl call this before running an interpreter. It uses safe shell quoting when reading or removing state files, so paths are treated as paths rather than accidental shell commands.

*Call graph*: called by 2 (js_repl, xlsx_repl); 1 external calls (quote).


##### `_repl_result`  (lines 245–257)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=()) -> ToolResult
```

**Purpose**: Turns a completed interpreter run into the standard tool response. It includes printed output, error output, the exit code, and optionally images.

**Data flow**: It receives stdout, stderr, an exit code, and any image content. It packs the text fields into a JSON string. If the exit code is nonzero, it adds a notice explaining that REPL state did not advance, and marks the tool result as an error. It returns a ToolResult containing the text and any images.

**Call relations**: js_repl and xlsx_repl call this after a run has actually ended rather than timed out. It is the final packaging step for normal completed runs.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_expired_result`  (lines 260–270)

```
def _expired_result(run: TaskRun, applied_s: int) -> ToolResult
```

**Purpose**: Builds the response for a REPL call whose waiting time ran out. It explains that the saved REPL state was not changed and, when possible, gives task handles so the still-running process can be inspected later.

**Data flow**: It receives the task run record and the applied timeout in seconds. If there is no surviving process id, it returns an error message with a timeout notice. If the process is still alive, it returns text containing the task id, log path, process id, and the note that state stayed unchanged.

**Call relations**: Both js_repl and xlsx_repl use this when run_task reports that the foreground wait expired. It calls shared tool helpers to format either the timeout notice or the background task handles.

*Call graph*: called by 2 (js_repl, xlsx_repl); 4 external calls (__init__, __init__, task_handles, timeout_notice).


##### `_emitted_images`  (lines 278–290)

```
async def _emitted_images(ctx: ToolContext, emit_path: str) -> tuple[ImageContent, ...]
```

**Purpose**: Reads back images written by JavaScript emitImage calls and converts them into tool response image objects. It also deletes the temporary image file afterward.

**Data flow**: It receives the sandbox context and the full emit file path. If the file does not exist, it returns an empty tuple. If it exists, it reads the JSON-lines content, removes the file, validates each recent image record, ignores malformed lines, and returns image content objects with media type and base64 data.

**Call relations**: js_repl calls this after a JavaScript run completes and before building the final _repl_result. It is the bridge between the JavaScript prelude’s file output and the platform’s inline image response format.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, quote).


##### `js_repl`  (lines 293–313)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs one JavaScript REPL call in the sandbox while preserving successful state across calls. It supports browser-style JavaScript work, package imports, image emission, resets, timeouts, and safe rollback on failure.

**Data flow**: It receives a tool context and validated JavaScript input. It builds the candidate source, chooses a unique image emit path, writes a temporary .mjs run file with the image prelude plus the candidate source, links global Node packages, and starts Node through the detached task runner. After the run, it records a metric. If the wait expired, it returns an expired-result response. If the code succeeded, it saves the candidate as the new persistent state. It then returns stdout, stderr, exit code, and any emitted images.

**Call relations**: This is the handler registered for the js_repl tool by manifest. During a request it coordinates helper functions in order: state building, image setup, module linking, task execution, metrics, timeout handling, state commit, image collection, and result packaging.

*Call graph*: calls 8 internal fn (_candidate_source, _emitted_images, _expired_result, _meter_run, _repl_result, global_modules_link, js_emit_prelude, js_emit_relative); 3 external calls (quote, run_task, uuid4).


##### `xlsx_repl`  (lines 316–325)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs one Python spreadsheet REPL call in the sandbox while preserving successful state across calls. It is meant for Excel work where later calls may reuse loaded workbooks, variables, and imports.

**Data flow**: It receives a tool context and validated Python input. It builds the candidate source, writes a temporary Python run file, adds a footer that prints the variable result as JSON when it exists, and starts python3 through the detached task runner. It records a metric, returns a timeout response if the wait expired, saves the candidate state only on success, and finally returns stdout, stderr, and exit code.

**Call relations**: This is the handler registered for the xlsx_repl tool by manifest. It uses the shared state, timeout, metric, and result helpers, but unlike js_repl it does not collect images or link Node packages.

*Call graph*: calls 4 internal fn (_candidate_source, _expired_result, _meter_run, _repl_result); 2 external calls (quote, run_task).


##### `manifest`  (lines 328–348)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system: its name, version, tools, related skills, and sandbox internet setting. Without this, the REPL tools would not be discoverable or callable.

**Data flow**: It takes no input. It constructs two tool definitions, one for JavaScript and one for spreadsheet Python, connects each to its input model and handler, adds the available data skills from disk paths, enables internet access in the sandbox, and returns a Manifest object.

**Call relations**: The larger extension loader calls this during registration. The returned manifest is how the host learns that js_repl and xlsx_repl exist and which handler function to invoke for each tool call.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling and conversation display`

This extension gives the agent a simple shared checklist, like a small whiteboard for the current conversation. Without it, the agent could still talk about its plan, but there would be no reliable stored task list for later tool calls or later turns in the same conversation to read back.

The file defines the shape of a todo task, the input expected by the two tools, and the stored board itself. The board is saved in the extension store under a key based on the conversation ID, so each conversation gets its own list. `update_todo_list` creates or replaces the whole board. `update_todo_status` reads the existing board, checks that it exists, checks that each requested task number is valid, then changes those task statuses and saves the board again.

The file also defines a conversation slot called `tasks`. A conversation slot is a small piece of structured information the surrounding system can ask for and display, such as a compact “Tasks” panel. When read, the slot returns the board title, task list, total count, completed count, and whether anything had to be shortened because it was too long.

Finally, `manifest` packages all of this for the host system: it announces the extension name and version, registers both tools, includes a prompt section that teaches the agent when to use them, and registers the task display slot.

#### Function details

##### `_require_ext`  (lines 93–96)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has access to the extension context. The extension context is the part of the runtime that gives this file access to its stored data.

**Data flow**: It receives a tool context. If that context contains an extension context, it returns it. If not, it stops immediately with an error, because the todo tools cannot read or write their checklist without that storage access.

**Call relations**: Both todo-changing tools call this at the start. It acts like checking that the key to the filing cabinet is present before trying to store or retrieve the checklist.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 99–100)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This helper builds the storage name used for one conversation’s todo board. It keeps todo data separated by conversation, so one chat does not accidentally see another chat’s checklist.

**Data flow**: It receives a conversation ID and turns it into a string by adding the todo prefix in front of it. The result is the exact key used when reading from or writing to the extension store.

**Call relations**: The create, update, summarize, and read paths all use this helper before touching stored checklist data. That keeps every part of the file using the same storage naming rule.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 103–104)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns the current todo board into the standard tool response format. It makes sure tool callers always get the latest checklist back after a change.

**Data flow**: It receives a todo board, converts it to JSON text, wraps that text as a text content item, and then wraps that in a tool result. Nothing is stored here; it only prepares the response that leaves the tool.

**Call relations**: After `update_todo_list` or `update_todo_status` saves the board, they call this helper to hand the updated state back to the agent in a consistent format.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 107–109)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper retrieves a saved todo board from the extension store. It hides the details of loading raw stored data and validating it as a proper todo board.

**Data flow**: It receives the extension context and a storage key. It asks the store for data at that key. If there is no saved data, it returns nothing. If data exists, it validates it into a `TodoBoard` object and returns that object.

**Call relations**: `update_todo_status` uses this before changing task statuses, while the conversation slot functions use it to summarize or display the current checklist. This makes reading the board work the same way everywhere.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 112–116)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates a new todo checklist or replaces the existing one for the conversation. The agent uses it when it needs to lay out or revise the full plan for a multi-step request.

**Data flow**: It receives the tool context and the requested title and tasks. It confirms extension storage is available, builds a new board from the input, saves that board under the current conversation’s storage key, and returns the saved board as JSON text in a tool result. The previous board, if any, is replaced completely.

**Call relations**: The manifest registers this as the `update_todo_list` tool, so the host system can call it when the agent asks to create or revise a checklist. Inside, it relies on `_require_ext` for storage access, `_board_key` for the conversation-specific storage name, and `_board_result` for the response.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 119–130)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of one or more existing tasks. The agent uses it to mark work as pending, in progress, or completed as the work moves forward.

**Data flow**: It receives the tool context and one or more status updates, each using a 1-based task number as a human-friendly index. It loads the existing board for the conversation. If there is no board, it raises an error telling the caller to create one first. For each update, it checks that the task number is within the list, changes that task’s status, saves the updated board, and returns the full current board.

**Call relations**: The manifest registers this as the `update_todo_status` tool. It depends on `_require_ext` to get storage access, `_board_key` to find the right conversation’s board, `_read_board` to load it, and `_board_result` to return the updated checklist.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 133–135)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives the host system a quick summary of whether a conversation has tasks and how many there are. It is used for the task conversation slot, where a lightweight count may be enough before reading the full list.

**Data flow**: It receives a conversation slot context, builds the storage key for that conversation, and reads the saved board. If there is no board, it returns nothing. If a board exists, it returns the number of tasks on it.

**Call relations**: The `TASKS_SLOT` provider uses this as its summary function. It shares the same `_board_key` and `_read_board` helpers as the tool paths, so the summary reflects the same stored checklist that the tools edit.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 138–168)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This function prepares the todo board for display in the conversation’s task slot. It turns the stored board into a compact, UI-friendly payload with counts and safe length limits.

**Data flow**: It receives a conversation slot context, reads the board for that conversation, and returns an empty task payload if none exists. If a board exists, it copies up to the maximum allowed number of tasks, shortens long titles and descriptions, counts completed tasks, and marks the result as truncated if anything had to be cut down.

**Call relations**: The `TASKS_SLOT` provider uses this when the system wants the full task panel contents. It reads the same stored board that `update_todo_list` and `update_todo_status` write, then hands back a `TasksSlotPayload` suitable for display.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 181–203)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what this extension provides. It is the extension’s registration card: name, version, tools, prompt guidance, and the task display slot.

**Data flow**: It takes no input. It builds and returns a `Manifest` object containing two tool definitions, one prompt section loaded from the nearby markdown file, and the conversation slot provider for tasks.

**Call relations**: The extension host calls this to discover and load the todo extension. The returned manifest connects external tool names to `update_todo_list` and `update_todo_status`, and connects the conversation UI slot to the task-reading functions.

*Call graph*: 3 external calls (__init__, __init__, __init__).
