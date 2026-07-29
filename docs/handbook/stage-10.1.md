# Built-in shell, file, artifact, and coordination tools  `stage-10.1`

This stage is the agent’s practical toolbox during the main work loop. It lets the model do real tasks without freely touching the whole computer. Instead, work happens inside a sandbox, a controlled workspace like a fenced-off workbench.

The builtins file defines the tools the agent can call: run shell commands, read and edit files, share finished artifacts, ask the user for input, load extra skills, request secrets, connect accounts, and coordinate subagents. The tool context is the permission slip each tool receives. It exposes only the outside resources that tool is allowed to use, such as files, browser access, credentials, or cleanup callbacks.

The sandbox session file provides one standard doorway into sandbox work. It creates sandboxes, runs commands, moves files, and ties each action to the right conversation turn. The conversation sandbox file manages the private workspace for one conversation: opening it, resuming it, listing files, reading, writing, and cleaning up. The local sandbox file is a development-friendly version that runs commands in a real local folder, useful but not secure like a true isolated sandbox.

## Files in this stage

### Conversation sandbox frontends
Conversation-facing sandbox components connect each conversation to private workspace storage and optional local command execution.

### `core/src/ufo/sandbox/conversation.py`

`domain_logic` · `request handling and off-turn workspace access`

A conversation’s workspace is the place where files live for that conversation. This file makes sure all access to that workspace goes through the sandbox “carrier”, which is the service that actually creates or reconnects to the sandbox. Without this file, two parts of the system could accidentally create different sandboxes for the same conversation, writes could go to a sandbox that is never used again, or a simple read could unexpectedly create new storage.

The central class is `ConversationSandbox`. It knows which sandbox backend is being used, where local workspace directories should live, what image to run, and how to talk through the proxy. When a turn needs the workspace, `open` either resumes the sandbox recorded in the database or creates one and stores its durable handle. It uses a compare-and-swap style database update, meaning “only write this new value if the old value is still what I saw.” This prevents races, like two people trying to put different labels on the same box at the same time.

For off-turn work, such as uploading an attachment or browsing files, it uses an unsigned token that cannot make outbound network connections. Reads are deliberately careful: `existing` never creates a sandbox just to answer a read. The file also provides bounded writes, file listing, streaming reads, and pruning old files from a directory.

#### Function details

##### `ConversationSandbox.open`  (lines 79–104)

```
async def open(self, conversation_id: UUID, run_token: str, env: Mapping[str, str]) -> SandboxHandle
```

**Purpose**: Opens the sandbox for a conversation, creating it if needed, and makes sure the database records the sandbox that everyone should use later. It is designed to be safe when two callers try to open a brand-new conversation workspace at the same time.

**Data flow**: It receives a conversation ID, a run token, and environment variables. It reads the currently stored sandbox handle, asks `_opened` to create or resume a sandbox, then tries `_claim` to save that sandbox handle in the conversation row. If another caller won the race first, it adopts the winner’s stored sandbox instead of leaving files in an unreferenced one; if the handle keeps changing too many times, it raises an error.

**Call relations**: The main turn-opening path calls this when a run needs a sandbox, and `write` also uses it when an off-turn writer needs to place a file. Inside, it relies on `_stored` to see what the database says, `_opened` to contact the carrier, and `_claim` to safely record the result.

*Call graph*: calls 3 internal fn (_claim, _opened, _stored); called by 2 (_open_sandbox, write).


##### `ConversationSandbox.existing`  (lines 106–126)

```
async def existing(self, conversation_id: UUID) -> SandboxHandle | None
```

**Purpose**: Reconnects to a conversation’s sandbox only if one is already recorded and belongs to this backend. It is the safe read-path entry: it never creates a new sandbox just because someone wants to browse or read files.

**Data flow**: It receives a conversation ID and reads the stored handle from the database. If there is no handle, or the handle was written by a different backend, it returns `None`. Otherwise it builds a sandbox request with an unsigned off-turn token and asks the carrier to attach to the existing sandbox, returning the handle if reachable.

**Call relations**: `entries`, `prune`, and `read` call this before touching files. That keeps those operations from accidentally provisioning new storage when the conversation has no workspace yet.

*Call graph*: calls 1 internal fn (_stored); called by 3 (entries, prune, read); 2 external calls (__init__, sandbox_handle_id).


##### `ConversationSandbox.write`  (lines 128–140)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a file inside the conversation’s workspace and returns the `/workspace/...` path where the agent can find it. It protects the process from very large uploads by enforcing a size limit first.

**Data flow**: It receives a conversation ID, a relative path, and file content as bytes. It rejects the write if the content is over the configured limit, converts the relative path into a safe workspace path, opens or resumes the sandbox, then asks the carrier to copy the bytes into that sandbox. The result is the path inside `/workspace`.

**Call relations**: This is used for off-turn producers such as inbound attachments. It hands off to `open` because writing is allowed to create the workspace if it does not exist yet, then delegates the actual copy to the carrier.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.prune`  (lines 142–154)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files under a workspace directory, keeping only the newest allowed count. This is useful for unattended appenders, such as logs, so they do not grow forever.

**Data flow**: It receives a conversation ID, a relative directory prefix, and a number of files to keep. It attaches only if a sandbox already exists; if not, it does nothing. When a sandbox is present, it runs a small Python cleanup program inside the sandbox, and raises an error if that program fails.

**Call relations**: It starts with `existing` so pruning never creates a sandbox by itself. It then uses a `SandboxSession` to run the cleanup command inside the same file view the agent sees.

*Call graph*: calls 1 internal fn (existing); 3 external calls (__init__, quote, workspace_path).


##### `ConversationSandbox.entries`  (lines 156–189)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists the files currently present in a conversation’s workspace for a file browser view. It returns plain file records with relative paths, sizes, and modification times.

**Data flow**: It receives a conversation ID and first tries to attach to an existing sandbox. If there is none, it returns an empty tuple. Otherwise it asks the sandbox file tool to glob, or walk, all files under `/workspace`, checks that the answer is shaped like a file list, warns if the result was truncated, converts each reported path into a workspace-relative path, and returns the files sorted by path.

**Call relations**: This is a read-only browser operation, so it calls `existing` instead of `open`. For each file returned by the sandbox tool, it calls `_workspace_rel` to strip away the container or host workspace prefix before building `WorkspaceFile` records.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 4 external calls (__init__, __init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 191–195)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns a full workspace path into the relative path shown to users. It prevents paths outside the workspace from being accepted as normal workspace entries.

**Data flow**: It receives a sandbox handle and a path reported by the workspace walk. It checks whether the path starts with either `/workspace/` or the sandbox’s host workspace path. If so, it removes that root and returns the remaining relative path; otherwise it raises an error because the reported path is outside the expected workspace.

**Call relations**: `entries` uses this while converting raw sandbox file-list output into clean browser entries. It is a small safety gate between low-level file-tool output and the user-facing list.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 197–206)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Reads one file from a conversation’s workspace as a stream of byte chunks. It returns `None` when there is no sandbox or the requested path is not a file.

**Data flow**: It receives a conversation ID and a relative path. It attaches only to an existing sandbox, checks whether the file exists, and if so returns an asynchronous byte stream for the caller to consume. It does not create storage, and it does not load the whole file into memory at once.

**Call relations**: Like `entries` and `prune`, it starts with `existing` so read requests stay side-effect-free. Once attached, it uses `SandboxSession` for the file-existence check and the streaming read.

*Call graph*: calls 1 internal fn (existing); 1 external calls (__init__).


##### `ConversationSandbox._opened`  (lines 208–230)

```
async def _opened(self, conversation_id: UUID, stored: str | None, run_token: str, env: Mapping[str, str]) -> SandboxHandle
```

**Purpose**: Does the low-level work of preparing and creating or resuming a sandbox through the carrier. It sets up the host-side workspace directory when this deployment serves workspace files from the cluster.

**Data flow**: It receives the conversation ID, the previously stored handle if any, the run token, and environment variables. For in-cluster operation, it creates the host workspace directory and, when running as root, changes ownership so the sandbox user can write there. Then it builds a sandbox specification, including any resume ID derived from the stored handle, and asks the carrier to create or reconnect to the sandbox.

**Call relations**: `open` calls this during each attempt to establish the sandbox. `_opened` is responsible for the carrier-facing setup, while `open` remains responsible for deciding whether the resulting handle wins the database race.

*Call graph*: called by 1 (open); 4 external calls (__init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._stored`  (lines 232–245)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Reads the sandbox handle currently recorded for a conversation in the active workspace. It also verifies that the conversation belongs to the current workspace.

**Data flow**: It receives a conversation ID. It opens a workspace database transaction, selects the conversation’s stored sandbox handle for the current workspace, and returns that handle or `None` if no sandbox is recorded. If the conversation row is missing from the current workspace, it raises a value error.

**Call relations**: `open`, `existing`, and `_claim` use this as the source of truth for what the database currently says. It keeps sandbox access tied to the active workspace rather than just any conversation ID.

*Call graph*: called by 3 (_claim, existing, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 247–268)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Safely records a sandbox handle in the database only if nobody else changed the handle since it was read. This is the race-control step that prevents two simultaneous opens from both becoming official.

**Data flow**: It receives a conversation ID, the handle value that was seen earlier, and the new handle it wants to store. It tries a conditional database update: write the new handle only if the row still contains the earlier value. If that succeeds, it returns the new handle. If it fails, it rereads the stored handle and returns the other caller’s winning value; if the handle vanished, it raises an error.

**Call relations**: `open` calls this after `_opened` returns a sandbox. Together they form a careful create-or-adopt flow: `_opened` may produce a sandbox, but `_claim` decides whether that sandbox becomes the one recorded for the conversation.

*Call graph*: calls 1 internal fn (_stored); called by 1 (open); 3 external calls (update, workspace_tx, ws_current).


### `core/src/ufo/sandbox/local.py`

`io_transport` · `sandbox setup and command execution`

This file is the simplest way the system can give a conversation a workspace and run commands in it. Instead of starting Docker or a remote sandbox, it treats the conversation’s workspace as an ordinary folder on the host computer. When a tool asks for `/workspace/file.txt`, this file translates that logical path into the real folder path on disk.

It also prepares the environment around each command. Commands inherit proxy settings so any outgoing web traffic still passes through the sandbox proxy, where credentials can be swapped and usage can be measured. It also installs small helper programs, `sbx` and `sbxfs`, into a temporary command path so file and network helper tools work even without a container image.

The important catch is safety. This local carrier is convenient, but it does not lock the process inside the workspace at the operating-system level. It is more like asking someone to stay in one room than actually locking the doors. Path checks elsewhere help keep normal tool file access under `/workspace`, but a host subprocess is still a host subprocess. For stronger isolation, the project uses carriers such as Docker or E2B.

#### Function details

##### `_provision_scratch`  (lines 41–54)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates a temporary support area for the local carrier. This area holds a fake home directory and copies of the helper programs that local subprocesses need on their command path.

**Data flow**: It starts with no caller-provided input. It creates a temporary directory, adds `home` and `bin` subfolders, copies the bundled `sbx` and `sbxfs` helper files into `bin`, marks them executable, and returns the temporary directory path.

**Call relations**: This is used when a `LocalCarrier` is created, as the default way to prepare its private scratch area. Later, `create` and `attach` put this scratch `bin` directory on the command path so subprocesses can find the helper tools.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `LocalCarrier.create`  (lines 61–92)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a local sandbox handle for a conversation. It makes sure the workspace folder exists and prepares the environment that future commands will run with.

**Data flow**: It receives a `SandboxSpec`, which includes the workspace path, conversation identity, run token, proxy information, and extra environment variables. It creates the workspace directory if needed, writes the proxy certificate into the scratch area, builds proxy-related environment variables, adds sentinel model API keys, adds helper tools to `PATH`, and returns a `SandboxHandle` describing the ready local sandbox.

**Call relations**: This is the setup path for a new local workspace. It hands back a `SandboxHandle`, which later methods such as `exec`, `write`, and `read` use to know where the workspace lives and what environment commands should inherit.

*Call graph*: 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.attach`  (lines 94–113)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Connects to an existing local workspace without creating a new one. This is used when the system wants to browse or reuse a conversation workspace only if it already exists.

**Data flow**: It receives a `SandboxSpec` and checks whether the workspace directory is already present. If the folder is missing, it returns `None`. If it exists, it returns a `SandboxHandle` with the conversation details, workspace path, run token, and a minimal environment containing the scratch home and helper command path.

**Call relations**: This is the read-only-style counterpart to `create`. It supplies the same kind of handle used by later read or command operations, but deliberately avoids making a new workspace just because someone looked for one.

*Call graph*: 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 115–141)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command as a normal host subprocess, with its current directory set to the workspace. It makes local execution look like sandbox execution by translating `/workspace` paths and applying the sandbox proxy environment.

**Data flow**: It receives a sandbox handle, a command as a tuple of arguments, and a timeout in seconds. It finds the real workspace folder, rewrites any `/workspace` text in the command arguments to the host folder path, starts the subprocess there, waits for it to finish, and captures its output. It returns an `ExecResult` containing standard output, standard error, and the exit code; if the command runs too long, it kills it and returns a timeout result.

**Call relations**: This is the main command-running path for the local carrier. It relies on `_root` to find the workspace folder, uses the environment prepared by `create` or `attach`, starts the subprocess, and packages the result for the higher sandbox layer.

*Call graph*: calls 1 internal fn (_root); 3 external calls (__init__, create_subprocess_exec, wait_for).


##### `LocalCarrier.write`  (lines 143–148)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file inside the local workspace. It is how the system places files into the conversation’s working directory.

**Data flow**: It receives a sandbox handle, a logical `/workspace/...` path, and the bytes to write. It converts the logical path to a real host path, creates parent folders if needed, writes the bytes to disk, and returns nothing.

**Call relations**: This is used when something needs to add or replace a workspace file. It delegates path translation to `_host_path`, then performs the actual disk work in a background thread so the async event loop is not blocked.

*Call graph*: calls 1 internal fn (_host_path); 1 external calls (to_thread).


##### `LocalCarrier.read`  (lines 150–158)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the local workspace in chunks. This lets callers read workspace files without loading a large file all at once.

**Data flow**: It receives a sandbox handle and a logical `/workspace/...` path. It converts that to the real host path, opens the file for binary reading, repeatedly reads chunks up to the configured chunk size, yields each chunk, and closes the file afterward.

**Call relations**: This is the local carrier’s copy-out path. Like `write`, it uses `_host_path` for safe workspace path translation, and it performs file operations through background threads because normal filesystem calls are blocking.

*Call graph*: calls 1 internal fn (_host_path); 1 external calls (to_thread).


##### `LocalCarrier.host`  (lines 160–168)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Explains that the local carrier cannot provide an external host address for a service running inside the sandbox. Since local commands are just host subprocesses, there is no separate network-addressable sandbox port to expose.

**Data flow**: It receives a sandbox handle and a port number, but it does not use them to build an address. Instead, it raises a runtime error telling the caller to use a remote carrier such as E2B when per-port external access is needed.

**Call relations**: This method exists to satisfy the same carrier interface as remote sandboxes. When a caller asks the local carrier for an externally reachable service endpoint, it stops the flow immediately with a clear error rather than pretending such an endpoint exists.


##### `_root`  (lines 171–174)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Finds the real host folder that backs `/workspace` for a local sandbox. It also catches the invalid case where a local sandbox has no host workspace path.

**Data flow**: It receives a `SandboxHandle`. If the handle has no `workspace_host_path`, it raises an error. Otherwise, it converts that stored path into a `Path` object and returns it.

**Call relations**: This helper sits underneath command and path operations. `LocalCarrier.exec` uses it to choose the subprocess working directory, and `_host_path` uses it as the base folder for translating logical workspace paths.

*Call graph*: called by 2 (exec, _host_path); 1 external calls (Path).


##### `_host_path`  (lines 177–180)

```
def _host_path(handle: SandboxHandle, path: str) -> Path
```

**Purpose**: Translates a logical sandbox path like `/workspace/a.txt` into the matching real path on the host machine. This is the bridge between the sandbox-facing path language and the local filesystem.

**Data flow**: It receives a sandbox handle and a logical path string. It gets the workspace root from `_root`, removes the `/workspace` prefix from the logical path, appends the remaining relative path to the host workspace folder, and returns the resulting host `Path`.

**Call relations**: This helper is used by `LocalCarrier.write` before writing files and by `LocalCarrier.read` before reading files. It keeps both directions of file transfer using the same path translation rule.

*Call graph*: calls 1 internal fn (_root); called by 2 (read, write); 1 external calls (PurePosixPath).


### Built-in tool surface
The built-in toolbox exposes shell, file, artifact, user-coordination, skill, secret, connector, and subagent actions to the agent.

### `core/src/ufo/tools/builtins.py`

`domain_logic` · `tool execution during a turn`

Think of this file as the agent’s supervised workshop. The agent can inspect files, edit them, search them, run commands, create downloadable artifacts, ask for missing human input, and delegate work, but every action goes through controlled doors. File and shell operations happen inside a sandbox, which is an isolated workspace container. That matters because it keeps the host system protected and lets the platform enforce limits on what can leave the workspace.

The file also adds important safety habits. A file must be read before it can be edited or overwritten, so the agent cannot blindly change content it has never seen. Large reads, searches, PDF rendering, and image handling are done by an in-sandbox helper called `sbxfs`, so only bounded results come back to the main process. Sharing a file is a separate, explicit tool: it measures the file, stores it in the artifact store, records it in the database, and returns a time-limited download link.

Beyond files, this file supports human and team workflows. It can ask the user questions in chat, request credentials through private prompts instead of the transcript, start OAuth account connection, load reusable skill instructions into the workspace, and control background subagents.

#### Function details

##### `bash_handler`  (lines 307–317)

```
async def bash_handler(ctx: ToolContext, args: BashInput) -> ToolResult
```

**Purpose**: Runs a shell command inside the sandboxed workspace and returns what the command printed. It marks the tool result as an error if the command exits unsuccessfully.

**Data flow**: It receives the tool context and a command with an optional timeout. It caps the timeout at the allowed maximum, asks the sandbox to run the command, joins standard output and standard error, and returns that text. If the command failed, it also includes the exit code and marks the result as an error.

**Call relations**: This is the handler behind the built-in `bash` tool. It does not call other local helpers; it packages the sandbox command result into `TextContent` and `ToolResult` so the rest of the tool system can show it to the model.

*Call graph*: 2 external calls (__init__, __init__).


##### `_require_str`  (lines 320–323)

```
def _require_str(value: object, field: str) -> str
```

**Purpose**: Checks that a value coming back from the sandbox is a real, non-empty string. It exists to catch malformed image or document results before they are passed onward.

**Data flow**: It receives an unknown value and the name of the field being checked. If the value is a non-empty string, it returns it unchanged. If not, it raises an error saying the sandbox read result was missing that field.

**Call relations**: This is a small guard used by `_pdf_result` and `read_handler` when they build image content. It prevents bad sandbox output from becoming a confusing or broken tool result.

*Call graph*: called by 2 (_pdf_result, read_handler).


##### `_pdf_result`  (lines 326–371)

```
def _pdf_result(result: dict[str, object]) -> ToolResult
```

**Purpose**: Turns a sandbox PDF or slideshow read result into content the model can understand: text plus rendered page or slide images when available. It also explains pagination, such as which pages were returned and where to continue.

**Data flow**: It receives a dictionary produced by the sandbox read helper. It collects extracted text, page counts, notes, and quality reminders into a text block, then adds one image block for each rendered page or slide. It returns a `ToolResult` containing those blocks, or raises an error if the result is empty or malformed.

**Call relations**: This helper is called by `read_handler` when the file type is PDF or PPTX. It uses `_require_str` to validate image fields before handing the finished text and image content back to the tool system.

*Call graph*: calls 1 internal fn (_require_str); called by 1 (read_handler); 3 external calls (__init__, __init__, __init__).


##### `read_handler`  (lines 374–411)

```
async def read_handler(ctx: ToolContext, args: ReadInput) -> ToolResult
```

**Purpose**: Reads a workspace file through the sandbox and returns the right kind of content for the file type. It supports text, images, PDFs, and PowerPoint files, with pagination for large documents.

**Data flow**: It receives a file path and optional offset and limit. It asks the sandbox `sbxfs` reader for a bounded result, records that the path has been read, then converts the result into text or image content. For text files, it adds a footer showing which lines were returned and how to continue if there is more.

**Call relations**: This is the handler behind the `read` tool. It calls `_pdf_result` for PDFs and slideshows, and `_require_str` for image data. Its record of read paths is later used by `write_handler` and `edit_handler` to prevent blind modification.

*Call graph*: calls 2 internal fn (_pdf_result, _require_str); 3 external calls (__init__, __init__, __init__).


##### `write_handler`  (lines 414–435)

```
async def write_handler(ctx: ToolContext, args: WriteInput) -> ToolResult
```

**Purpose**: Creates or overwrites a text file in the workspace, while enforcing the rule that an existing file must be read before it is changed. It returns a small summary of what was written.

**Data flow**: It receives a target path and text content. It asks the sandbox whether the file already exists; if it does and the path was not read earlier this turn, it refuses. Otherwise it writes the encoded bytes, marks the path as read, counts bytes and lines, and returns those facts as JSON text.

**Call relations**: This is the handler behind the `write` tool. It relies on `read_handler` having added existing paths to `ctx.read_paths`, and it packages its summary with `json.dumps`, `TextContent`, and `ToolResult`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `edit_handler`  (lines 438–446)

```
async def edit_handler(ctx: ToolContext, args: EditInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to a file that the agent has already read. This helps make edits deliberate instead of guessing at unseen content.

**Data flow**: It receives a file path and one or more replacement instructions. It first checks that the file path is in the turn’s read set. Then it converts the edits into plain dictionaries, sends them to the sandbox `sbxfs edit` command, and returns the sandbox’s JSON-style result as text.

**Call relations**: This is the handler behind the `edit` tool. It depends on `read_handler` having recorded the file as seen, then hands the actual editing work to the sandbox and wraps the response for the tool system.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `glob_handler`  (lines 449–455)

```
async def glob_handler(ctx: ToolContext, args: GlobInput) -> ToolResult
```

**Purpose**: Finds workspace files whose names match a glob pattern, which is a wildcard pattern such as `**/*.py`. It keeps the directory walk inside the sandbox.

**Data flow**: It receives a pattern and an optional starting directory. If no directory is provided, it searches from the workspace root. It asks the sandbox `sbxfs glob` command for matching paths and returns the bounded result as JSON text.

**Call relations**: This is the handler behind the `glob` tool. It avoids using shell commands like `find` by delegating matching to the sandbox helper, then wraps the result with `TextContent` and `ToolResult`.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `grep_handler`  (lines 458–476)

```
async def grep_handler(ctx: ToolContext, args: GrepInput) -> ToolResult
```

**Purpose**: Searches file contents in the workspace for a regular expression, meaning a pattern language for matching text. It is the safe, bounded version of running `grep` or `ripgrep` directly.

**Data flow**: It receives the search pattern plus optional filters such as file glob, context lines, case-insensitive mode, output style, and result limit. It builds a parameter dictionary, defaults the search to the workspace root and a fixed head limit, asks the sandbox to run `sbxfs grep`, and returns the result as JSON text.

**Call relations**: This is the handler behind the `grep` tool. Like `glob_handler`, it keeps scanning inside the sandbox and only returns the limited search result to the main tool system.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `_store_artifact`  (lines 479–515)

```
async def _store_artifact(ctx: ToolContext, scoped: str, key: str, size_bytes: int, digest: str) -> None
```

**Purpose**: Copies a pre-measured workspace file into the configured artifact store, which is where downloadable shared files live. It supports both S3-style cloud storage and local filesystem storage.

**Data flow**: It receives the tool context, the sandbox path, the destination key, and the file’s measured size and SHA-256 digest, which is a fingerprint of the bytes. For S3, it creates a presigned upload URL tied to that size and checksum, then tells the sandbox to upload directly with `curl`. For filesystem storage, it streams the file from the sandbox into the blob store. It returns nothing, but the file is stored or an error is raised.

**Call relations**: This helper is called only by `share_file_handler`. It does the actual transfer after `share_file_handler` has checked the file and chosen its public artifact name.

*Call graph*: called by 1 (share_file_handler); 2 external calls (b64encode, quote).


##### `share_file_handler`  (lines 518–595)

```
async def share_file_handler(ctx: ToolContext, args: ShareFileInput) -> ToolResult
```

**Purpose**: Makes a workspace file available outside the sandbox as a downloadable artifact. This is the explicit, controlled path for giving a produced file back to the user.

**Data flow**: It receives a file path, optional download name, optional caption, and context. It verifies artifact sharing is configured, measures the file inside the sandbox for size, checksum, and likely text/binary status, chooses a safe filename, stores the file through `_store_artifact`, records the shared artifact in the database, creates a time-limited download token, and returns JSON containing the URL and file details.

**Call relations**: This is the handler behind the `share_file` tool. It calls `_store_artifact` for the byte transfer, uses the database to record what was shared, and uses artifact-token helpers so core’s artifact route can later serve the file securely.

*Call graph*: calls 1 internal fn (_store_artifact); 15 external calls (__init__, __init__, now, dumps, loads, guess_type, PurePosixPath, quote, insert, select (+5 more)).


##### `spawn_subagent_handler`  (lines 598–609)

```
async def spawn_subagent_handler(ctx: ToolContext, args: SpawnSubagentInput) -> ToolResult
```

**Purpose**: Delegates a typed subtask to a child agent profile. It can either wait for the child’s answer or start it in the background and return its turn ID.

**Data flow**: It receives the profile name, payload, background flag, and context. It asks `ctx.spawn` to create and run the subagent. If the profile name is unknown, it returns a recoverable error result. If the child is running in the background, it returns the new turn ID; otherwise it returns the child’s validated JSON output and preserves whether that output is untrusted.

**Call relations**: This is the handler behind the `spawn_subagent` tool. It hands work off to `ToolContext.spawn`; later tools such as `wait_for_subagents_handler`, `cancel_subagent_handler`, and `message_subagent_handler` can control background children.

*Call graph*: 3 external calls (__init__, __init__, spawn).


##### `ask_user_handler`  (lines 617–627)

```
async def ask_user_handler(ctx: ToolContext, args: AskUserCall) -> ToolResult
```

**Purpose**: Prepares a structured question for the user and tells the agent to ask it in its reply, then stop. It keeps the interaction inside the normal chat flow instead of opening a separate prompt.

**Data flow**: It receives a title, one or more questions, and activity narration. It builds a JSON payload describing the pending question and prepends a directive telling the model to ask the question and end the turn. It returns that text as the tool result.

**Call relations**: This is the handler behind the `ask_user` tool. It packages the question for both the model and any rich chat surface that can render structured questions.

*Call graph*: 3 external calls (__init__, __init__, dumps).


##### `load_skill_handler`  (lines 630–641)

```
async def load_skill_handler(ctx: ToolContext, args: LoadSkillInput) -> ToolResult
```

**Purpose**: Loads a reusable skill package into the workspace and returns its instructions. A skill is a bundle of workflow guidance and supporting files for a task area.

**Data flow**: It receives a skill name. It asks the skill registry for that skill plus all of its dependencies, mounts each skill’s files into the sandbox workspace, builds the combined instruction context, and returns it as text.

**Call relations**: This is the handler behind the `load_skill` tool. It calls `mount_skill` to place files where the agent can read them, then `loaded_context` to produce the instruction text the model should follow.

*Call graph*: 4 external calls (__init__, __init__, loaded_context, mount_skill).


##### `connect_account_handler`  (lines 650–662)

```
async def connect_account_handler(ctx: ToolContext, args: ConnectAccountInput) -> ToolResult
```

**Purpose**: Starts a private account-connection handoff, such as OAuth, for the member currently speaking. OAuth is the common web flow where a user grants access without pasting a password into chat.

**Data flow**: It receives a provider name, whether the connection should be shared with the workspace, and context. It requires a speaking member, validates that the provider is installed, creates a `ConnectRequest`, and returns a directive telling the model to point the member to a private connection control rather than exposing an authorization URL in the conversation.

**Call relations**: This is the handler behind the `connect_account` tool. It uses the installed connect flow to validate the provider, then hands a structured request back to the chat surface through `ToolResult`.

*Call graph*: 4 external calls (__init__, __init__, __init__, installed_connect_flow).


##### `request_credentials_handler`  (lines 671–694)

```
async def request_credentials_handler(ctx: ToolContext, args: RequestCredentialsInput) -> ToolResult
```

**Purpose**: Asks an admin user to provide secret values, such as API keys, through private prompts instead of chat. This prevents secrets from appearing in the transcript.

**Data flow**: It receives a reason and a limited list of credential prompts. It checks that there is a speaking member, that credential storage is configured, and that the speaker is a workspace admin. It seals the requested credential slots for that member and workspace, builds a `CredentialRequest`, and returns a directive plus the structured request as text.

**Call relations**: This is the handler behind the `request_credentials` tool. It calls `ctx.speaker_is_admin` before creating the request, and relies on the surrounding surface to show private prompts and fulfill the sealed credential request.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `wait_for_subagents_handler`  (lines 697–710)

```
async def wait_for_subagents_handler(ctx: ToolContext, args: WaitForSubagentsInput) -> ToolResult
```

**Purpose**: Waits for one or more background subagents to finish and reports their final status and output. It is used when the main agent has delegated work and now needs the results.

**Data flow**: It receives subagent IDs as strings. It checks that subagent control exists, converts each ID into a UUID, asks the subagent controller to wait for them, and returns JSON listing each subagent’s ID, status, and final text. If any result is marked untrusted, the returned tool result is also marked untrusted.

**Call relations**: This is the handler behind the `wait_for_subagents` tool. It fits after `spawn_subagent_handler` has started background children, using `ctx.subagents.wait` to collect their terminal results.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `cancel_subagent_handler`  (lines 713–725)

```
async def cancel_subagent_handler(ctx: ToolContext, args: CancelSubagentInput) -> ToolResult
```

**Purpose**: Cancels a running background subagent and reports what state it is now in. If the subagent already finished, cancellation is harmless and simply reports its existing status.

**Data flow**: It receives one subagent ID as text. It checks that subagent control is available, converts the ID to a UUID, asks the controller to cancel it, and returns JSON with the subagent ID and status.

**Call relations**: This is the handler behind the `cancel_subagent` tool. It is used after `spawn_subagent_handler` has created a background child and the main agent or user no longer wants that child to continue.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


##### `message_subagent_handler`  (lines 728–741)

```
async def message_subagent_handler(ctx: ToolContext, args: MessageSubagentInput) -> ToolResult
```

**Purpose**: Queues a follow-up message for a background subagent. The message becomes the subagent’s next turn after its current work finishes.

**Data flow**: It receives a subagent ID and message text. It checks that subagent control is available, converts the ID to a UUID, sends the message through the controller, and returns JSON with the resulting subagent turn ID and status.

**Call relations**: This is the handler behind the `message_subagent` tool. It works with subagents created by `spawn_subagent_handler` and can be followed later by `wait_for_subagents_handler` to see the result of the follow-up.

*Call graph*: 4 external calls (__init__, __init__, dumps, UUID).


### Shared execution contracts
The shared sandbox session and tool context define the safe APIs and allowed outside-world powers used by sandbox and tool implementations.

### `core/src/ufo/sandbox/session.py`

`domain_logic` · `cross-cutting during sandbox startup and per-turn tool execution`

A sandbox is a contained work area, like a rented workshop, where tools can run commands and touch files without seeing the system's private records. This file is the contract for that workshop. It says that tools may reach only the conversation's `/workspace`, while transcripts and other private data stay outside in storage the sandbox cannot access.

The file has three main jobs. First, it defines small value objects, such as `SandboxSpec`, `SandboxHandle`, and `ExecResult`, that describe what sandbox to open, how to refer to it later, and what came back from a command. Second, it defines `Carrier`, a common interface for different sandbox backends. A carrier might be Docker, E2B, or something remote, but tools do not need to know which one is underneath. Third, it defines `SandboxSession`, the per-turn object that tools actually use.

A key safety feature is path checking. `workspace_path` turns a user-supplied path into a path under `/workspace` and rejects tricks like `..` that would climb outside it. Another key feature is signed run tokens. These identify the workspace, turn, and optional acting member when sandbox traffic goes through the egress proxy. In short, this file is the seam that keeps tool execution useful, portable, and tightly scoped.

#### Function details

##### `RunTokenCodec.from_env`  (lines 53–57)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Builds a token signer from the deployment secret stored in the environment. The system needs this secret so it can create and verify sandbox run tokens that cannot be forged by outsiders.

**Data flow**: It reads the environment variable named by `UFO_TOKEN_SECRET_ENV`. If the value is missing, it stops with a clear error. If it is present, it turns the text into bytes and returns a `RunTokenCodec` ready to sign or check tokens.

**Call relations**: The serve process and proxy serve setup call this when they need the shared token codec. After that, other parts of the system can use the codec to mint run tokens for sandboxes or decode proxy credentials.

*Call graph*: called by 2 (serve, run).


##### `RunTokenCodec.encode`  (lines 59–62)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a `RunToken` into a signed string that can be used as the proxy username for one sandbox turn. This lets the proxy know which workspace and turn a network request should be attributed to.

**Data flow**: It receives a `RunToken` containing a workspace id, turn id, and maybe an acting member id. It formats those values into a simple payload, uses `sign_token` with the codec secret, and returns the signed token string.

**Call relations**: The sandbox-opening flow calls this when preparing a sandbox for a turn. It hands the formatted payload to `ufo.token_signing.sign_token`, so later the proxy can reject tokens that were not minted by this deployment.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 64–79)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads a proxy `Authorization` header and recovers the trusted `RunToken` inside it. This is how the proxy turns an incoming Basic Auth username into the workspace, turn, and member authority it should enforce.

**Data flow**: It receives a header string. It checks that the scheme is Basic Auth, base64-decodes the credentials, takes the username, verifies the signed token with the codec secret, splits out the token fields, converts ids into UUID values, and returns a `RunToken`. If anything is malformed or the signature is wrong, it raises `ValueError`.

**Call relations**: This is the inverse of `RunTokenCodec.encode`. It calls base64 decoding, signed-token verification, UUID parsing, and `RunToken` construction so the proxy can trust only credentials created by the same deployment.

*Call graph*: 4 external calls (__init__, b64decode, verify_token, UUID).


##### `sandbox_handle_id`  (lines 142–147)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Extracts the sandbox id from a stored handle only if that handle belongs to the expected backend. This prevents one sandbox backend from trying to resume another backend's container or remote sandbox.

**Data flow**: It receives a backend name and a stored handle string. If the handle begins with the matching `backend:` prefix, it returns the part after the prefix. Otherwise it returns `None`.

**Call relations**: This helper is used around resume decisions. It keeps carrier-specific stored ids from being mixed up when deployments switch from one sandbox provider to another.


##### `Carrier.create`  (lines 164–164)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the carrier operation for creating or attaching to a conversation sandbox. A concrete carrier, such as Docker or a remote provider, supplies the real behavior.

**Data flow**: It receives a `SandboxSpec` describing the conversation, image, workspace, proxy, token, and optional resume id. The implementing carrier returns a `SandboxHandle`, which is the system's reference to the live sandbox.

**Call relations**: This is part of the carrier contract. Higher-level sandbox-opening code can call `create` without knowing whether the underlying carrier starts a local container, resumes one, or asks a remote service for a sandbox.


##### `Carrier.attach`  (lines 166–172)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines the carrier operation for reconnecting to an existing sandbox without creating a new one. This matters for read-only browsing, where simply looking for files must not accidentally start a fresh sandbox.

**Data flow**: It receives a `SandboxSpec`, usually with a resume id. The implementing carrier returns a `SandboxHandle` if that sandbox is reachable, or `None` if it is gone or unavailable.

**Call relations**: This is the safe read-side counterpart to `create`. Carrier implementations use it when the caller wants to inspect an existing workspace but must not provision a new container as a side effect.


##### `Carrier.exec`  (lines 174–176)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines how to run a command inside the sandbox. Concrete carriers implement this so sessions can execute shell commands or helper programs in a backend-neutral way.

**Data flow**: It receives a sandbox handle, an argument tuple for the command, and a timeout in seconds. The carrier runs the command inside the sandbox and returns an `ExecResult` with stdout, stderr, and exit code.

**Call relations**: Session methods such as `SandboxSession.bash`, `SandboxSession.ensure_tool_output_dir`, `SandboxSession.file_exists`, and `SandboxSession.run_sbxfs` rely on this seam. They decide what command is needed; the carrier decides how that command is run.


##### `Carrier.write`  (lines 178–184)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how to copy bytes from the host into a file under the sandbox workspace. This avoids pushing large file contents through command-line arguments, which can fail or be unsafe for some providers.

**Data flow**: It receives a sandbox handle, an absolute workspace path, and the bytes to write. The carrier writes those bytes inside the sandbox, creating parent directories as needed, and returns nothing when complete.

**Call relations**: Sandbox file-writing flows call through this interface after path safety checks. Each carrier can choose the right transport, such as an upload API, a Docker stream, or direct local filesystem access.


##### `Carrier.read`  (lines 186–191)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how to stream a workspace file out of the sandbox in chunks. Streaming avoids loading an entire large file into the host process at once.

**Data flow**: It receives a sandbox handle and an absolute workspace path. The carrier returns an asynchronous stream of byte chunks, or raises `FileNotFoundError` if there is no file there.

**Call relations**: Sandbox read flows call through this interface after path safety checks. The session asks for the file; the carrier supplies the backend-specific way to stream it out.


##### `Carrier.host`  (lines 193–199)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Defines how to get an externally reachable address for a service running on a port inside the sandbox. This is needed when a tool starts something like a browser debugging endpoint or a preview web server.

**Data flow**: It receives a sandbox handle and an in-sandbox port number. The carrier returns a host address, sometimes including a port, that outside callers can dial; carriers without such routing can raise an error.

**Call relations**: Higher-level code calls this through `SandboxSession.host`. The session knows which sandbox is active, while the carrier knows how that sandbox's ports are exposed.


##### `workspace_path`  (lines 202–210)

```
def workspace_path(path: str) -> str
```

**Purpose**: Turns a tool-supplied path into a safe absolute path under `/workspace`. It blocks path tricks that would escape the sandbox workspace, such as using `..` to climb upward.

**Data flow**: It receives a path string, treats relative paths as being inside `/workspace`, normalizes `.` and `..` pieces through `_resolve_parts`, and checks that the final path is still inside `/workspace`. It returns the safe path string or raises `ValueError` if the path escapes.

**Call relations**: File-facing session methods call this before writing, reading, checking existence, or running `sbxfs` file operations. It delegates the piece-by-piece cleanup to `_resolve_parts` and uses `PurePosixPath` so the logic follows Unix-style sandbox paths.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 4 (file_exists, read_file, run_sbxfs, write_file); 1 external calls (PurePosixPath).


##### `_resolve_parts`  (lines 213–222)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Normalizes the pieces of a path while enforcing that `..` cannot move above the workspace root. It is the small path-cleaning helper behind `workspace_path`.

**Data flow**: It receives a tuple of path pieces. It builds a stack, ignores empty pieces and `.`, pops one level for `..`, and raises `ValueError` if popping would escape the root. It returns the cleaned list of pieces.

**Call relations**: `workspace_path` calls this as its safety core. By keeping this rule in one helper, all workspace path checks behave the same way.

*Call graph*: called by 1 (workspace_path).


##### `SandboxSession.authorize`  (lines 234–260)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new session view for a specific run token and environment. This lets a shared sandbox container be reused across turns without keeping the previous turn's network authority.

**Data flow**: It receives a new run token, a set of environment variable names to remove, and extra environment values to add. It checks that the existing handle has a run token, replaces the old token with the new one inside proxy-related environment variables, removes cleared variables, merges in the new environment, and returns a new `SandboxSession` with an updated `SandboxHandle`.

**Call relations**: This method is used when a sandbox handle is being scoped for a particular turn. It builds fresh `SandboxHandle` and `SandboxSession` objects rather than mutating the old session, so later command execution uses the correct proxy credentials.

*Call graph*: 2 external calls (__init__, __init__).


##### `SandboxSession.bash`  (lines 262–267)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a shell command inside the sandbox. It is the simple way for tools or extensions to ask the sandbox to do command-line work in `/workspace`.

**Data flow**: It receives a command string and optionally a timeout. It wraps the command as `bash -lc ...`, uses the default timeout when none is supplied, sends it to the carrier's `exec`, and returns the resulting stdout, stderr, and exit code.

**Call relations**: The sandbox Chrome extension calls this when leasing browser-related sandbox resources. This method hands the actual execution off to `Carrier.exec`, keeping the caller independent of the sandbox backend.

*Call graph*: called by 1 (lease).


##### `SandboxSession.write_file`  (lines 269–270)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file in the sandbox workspace after proving the requested path stays inside `/workspace`. This gives tools a safe copy-in operation.

**Data flow**: It receives a path and byte content. It converts the path with `workspace_path`, then passes the safe absolute path and bytes to the carrier's `write`. It returns nothing after the carrier finishes writing.

**Call relations**: The skill mounting flow calls this to place files into the sandbox. It relies on `workspace_path` for safety and on the carrier for the actual backend-specific file transfer.

*Call graph*: calls 1 internal fn (workspace_path); called by 1 (mount_skill).


##### `SandboxSession.ensure_tool_output_dir`  (lines 272–294)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine's private `.tool-output` directory exists inside `/workspace`. If a regular file or broken link is squatting on that reserved name, it removes it so later tool-output offloading will not fail.

**Data flow**: It runs a small shell script in the sandbox against the fixed tool-output path. The script exits if the directory already exists, removes a non-directory squatter and prints `r` if it reclaimed one, then creates the directory. The method raises `OSError` if the command fails and returns `true` only when something was reclaimed.

**Call relations**: This method uses `Carrier.exec` directly because the check and repair must happen inside the sandbox filesystem. It is deliberately limited to the fixed engine-owned directory, so the destructive cleanup cannot target arbitrary member files.


##### `SandboxSession.file_exists`  (lines 296–301)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the sandbox workspace. It is a small safe test used before code decides whether to read or rely on a file.

**Data flow**: It receives a path, converts it to a safe workspace path, then runs `test -f` inside the sandbox. It returns `true` if the command exits successfully and `false` otherwise.

**Call relations**: This method calls `workspace_path` before using `Carrier.exec`. The session owns the safety check; the carrier only runs the resulting command.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.run_sbxfs`  (lines 303–330)

```
async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured file operation through the in-sandbox `sbxfs` command and returns its JSON result. This lets large or complex file work happen inside the sandbox instead of copying whole files to the host first.

**Data flow**: It receives an operation name and a dictionary of arguments. If the arguments include a string `path`, it rewrites that path safely under `/workspace`, serializes the arguments as compact JSON, runs `sbxfs`, reads stdout, parses it as JSON, and returns the parsed object. Empty output, invalid JSON, or a non-object result becomes a runtime error; a JSON `error` string becomes a `ValueError` suitable for reporting as a recoverable tool problem.

**Call relations**: This method calls `workspace_path`, `json.dumps`, and `json.loads`, then uses `Carrier.exec` to run the real work in the sandbox. It is the bridge between host-side tool code and in-sandbox file helpers such as searching, windowed reading, or rendering.

*Call graph*: calls 1 internal fn (workspace_path); 2 external calls (dumps, loads).


##### `SandboxSession.read_file`  (lines 332–335)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox workspace after checking that the path is safe. It is used when a produced file needs to leave the sandbox without being loaded all at once.

**Data flow**: It receives a path, converts it with `workspace_path`, and returns the carrier's asynchronous byte stream for that safe path. The caller consumes the stream chunk by chunk.

**Call relations**: This method calls `workspace_path` and then hands off to `Carrier.read`. The session enforces the workspace boundary, while the carrier supplies the backend-specific streaming mechanism.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.host`  (lines 337–341)

```
async def host(self, port: int) -> str
```

**Purpose**: Asks the carrier for an outside address that can reach a service running on a port inside the sandbox. This supports workflows like connecting to an in-sandbox browser or preview server.

**Data flow**: It receives a port number. It passes the current sandbox handle and port to the carrier, waits for the carrier's answer, and returns the reachable host string.

**Call relations**: The sandbox Chrome extension calls this when it needs to connect to a browser endpoint inside the sandbox. This method delegates the routing details to `Carrier.host`, because each sandbox backend exposes ports differently.

*Call graph*: called by 1 (lease).


##### `SandboxSession.traffic_token`  (lines 344–347)

```
def traffic_token(self) -> str | None
```

**Purpose**: Exposes the carrier's optional traffic token for callers that need it when connecting to a public sandbox port. Some providers require this token as an extra connection credential.

**Data flow**: It reads the `traffic_token` stored on the session's sandbox handle and returns it. If the carrier does not use such a token, it returns `None`.

**Call relations**: This property is used alongside `SandboxSession.host`: one value tells the caller where to connect, and this one may tell the caller what token header or credential to include.


### `core/src/ufo/tools/context.py`

`domain_logic` · `tool execution and turn cleanup`

A tool in this system is not allowed to freely reach into the whole application. Instead, it gets a ToolContext: a carefully scoped bundle of capabilities for the current turn. You can think of it like a visitor badge. The badge says which rooms the tool may enter, whose authority it is acting under, what audience its output belongs to, and which external accounts it may touch.

The file also defines the shapes of tool outputs, including text and image blocks, and a ToolResult that can mark output as an error or as untrusted. “Untrusted” means the content may have come from a web page or third party, so the system must keep it from being treated as instructions.

For delegation, the Spawn and SubagentControl protocols describe how a tool can start a child agent, wait for it, cancel it, or send it a follow-up message. For resources that must not leak, TurnCleanup lets tools register async close functions, which are drained at the end of the turn.

The ToolContext methods enforce important boundaries. They decide the acting member, the correct audience for writes, readable memory subjects, admin checks, credential authorization, and connector account selection. Without this file, tools would lack a single, consistent place to enforce who may do what.

#### Function details

##### `Spawn.__call__`  (lines 126–132)

```
async def __call__(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: This describes how a tool asks the system to delegate work to a named subagent profile. A subagent is a child agent turn that can do a typed task for the parent.

**Data flow**: The caller provides a profile name, an input payload, and options such as whether the child should run in the background and whether repeated attempts should reuse the same child. The implementation validates the payload, starts or reconnects to the child turn, and returns a SpawnResult containing the child turn id and, for foreground work, the validated output.

**Call relations**: This is a protocol method, so this file defines the promise rather than the concrete behavior. Tool handlers call it through ToolContext.spawn when they need another agent to do a subtask, and the subagent system supplies the real implementation.


##### `SubagentControl.wait`  (lines 141–141)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: This describes how a tool waits for one or more background subagents to finish. It is used when work was started earlier and the parent now wants the final result.

**Data flow**: The caller gives child turn ids. The implementation waits until those turns reach terminal states, then returns one status record per child with its final state, text, and trust marker.

**Call relations**: This is part of the SubagentControl protocol attached to ToolContext. Tools use it after background spawning, while the subagent workflow provides the actual waiting behavior.


##### `SubagentControl.cancel`  (lines 143–143)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: This describes how a tool asks the system to stop a running background subagent. It is useful when delegated work is no longer needed or should not continue.

**Data flow**: The caller gives one child turn id. The implementation cancels that child if possible and returns a SubagentStatus describing the terminal result.

**Call relations**: This protocol method is called through ToolContext.subagents by lifecycle-style tools. The real cancellation is supplied by the subagent workflow outside this file.


##### `SubagentControl.message`  (lines 145–145)

```
async def message(self, turn_id: UUID, text: str) -> SubagentStatus
```

**Purpose**: This describes how a tool sends a follow-up message to an already spawned background subagent. It lets the parent continue or redirect a child task.

**Data flow**: The caller provides a child turn id and message text. The implementation delivers that message as the child’s next turn and returns the child’s resulting status.

**Call relations**: This is a protocol contract. Tools call it through ToolContext.subagents, and the subagent workflow implements the actual messaging.


##### `TurnCleanup.register`  (lines 159–160)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: This records a cleanup action that must run when the turn ends. A tool uses it after opening something like a browser connection or hosted session lease.

**Data flow**: The function receives an async close function. It appends that close function to the cleanup list and returns nothing; the visible change is that the resource is now scheduled to be closed later.

**Call relations**: Tool code calls this when it creates a per-turn resource. Later, the turn loop calls TurnCleanup.drain to run the registered close functions.


##### `TurnCleanup.drain`  (lines 162–168)

```
async def drain(self) -> None
```

**Purpose**: This closes all resources registered for the turn, even if one close operation fails. It prevents browser connections, session leases, or similar resources from leaking after a turn ends.

**Data flow**: It reads the stored list of async close functions. It pops them in reverse order, awaits each one, and logs any exception instead of stopping the rest of cleanup.

**Call relations**: The turn runner drains this registry at the end of a turn. If a closer raises an error, this function hands the failure to ufo.o11y.log so cleanup problems are visible without blocking the remaining cleanup work.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 205–213)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: This identifies whose authority the current tool call may use. It chooses the live speaker when there is one, otherwise the member carried by a scheduled or delegated turn.

**Data flow**: It reads speaker_member_id and on_behalf_of_member_id from the context. If a speaker is present, that id comes out; otherwise the on-behalf-of id comes out, or None if neither exists.

**Call relations**: Other ToolContext methods use this property when deciding audiences, readable subjects, and connector grants. It is the small rule that keeps later permission checks tied to the correct person.


##### `ToolContext.effective_audience`  (lines 216–226)

```
def effective_audience(self) -> Audience
```

**Purpose**: This decides which audience a write should belong to. It prevents private-room or shared-channel information from being stamped in a way that would leak into the wrong memory space.

**Data flow**: It reads the current audience and acting member. If there is no acting member, or the current audience is not the workspace-shared audience, it returns the existing audience. If the conversation is shared and there is an acting member, it returns that member’s conversation-specific audience.

**Call relations**: This property relies on acting_member_id for the person and calls conversation_audience to build the correct member-scoped audience. Tools that write memories or records can use this to stamp data safely.

*Call graph*: 1 external calls (conversation_audience).


##### `ToolContext.read_subjects`  (lines 229–238)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: This computes the set of memory subjects the current tool call may read. It combines the conversation’s own subjects with the acting member’s private subject, but does not accidentally include broader shared workspace content.

**Data flow**: It converts the context audience into subjects, then checks the acting member. If there is an acting member, it adds that member’s subject; otherwise it returns only the audience-derived subjects.

**Call relations**: It calls audience_subjects to interpret the conversation audience and member_subject to name the member’s private subject. Read paths in object and memory tools can use this result to stay within the proper privacy boundary.

*Call graph*: 2 external calls (audience_subjects, member_subject).


##### `ToolContext.speaker_is_admin`  (lines 240–250)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: This checks whether the actual speaking member is a workspace admin. It deliberately returns false for background calls without a speaker, so scheduled or delegated work cannot borrow admin power silently.

**Data flow**: It first looks for speaker_member_id. If there is no speaker, it returns false. Otherwise it opens a workspace database transaction, asks whether that member is an admin in the turn’s workspace, and returns the answer.

**Call relations**: Many object and member operations call this before allowing workspace-wide actions. Internally it uses workspace_tx to access the database and member_is_admin to perform the admin check.

*Call graph*: called by 19 (apply, delete, _visible_rows, apply, delete, get, list, status, request_credentials_handler, _credential_authorization (+9 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 252–263)

```
async def agent_is_main(self) -> bool
```

**Purpose**: This checks whether the current turn belongs to the workspace’s main agent. Some operations are allowed only for the main agent rather than every agent.

**Data flow**: It opens a workspace database transaction, queries the agent table for the current turn’s agent id and workspace id, and reads the is_main flag. It returns true or false.

**Call relations**: Agent and member object code calls this when deciding what the current agent may see or change. The function builds the database query with SQLAlchemy and runs it inside workspace_tx.

*Call graph*: called by 3 (apply, _visible_rows, apply); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 265–267)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: This starts an authorization flow for storing or using a credential slot declared by an extension. It creates a sealed authorization token that can later be opened or fulfilled.

**Data flow**: The caller provides a credential slot name and a payload. The function first verifies that credential authorization is allowed, then asks the CredentialRequests service to authorize that workspace, member, slot, and payload, returning the sealed authorization string.

**Call relations**: Extension tools such as GitHub and Slack connection flows call this when they need an admin-approved credential setup. It delegates the permission checks to _credential_authorization before creating the request.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 269–271)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: This opens and verifies an existing sealed credential authorization. It is used to confirm that a credential action was previously approved for the same workspace, member, and slot.

**Data flow**: The caller gives a slot name and sealed authorization string. The function verifies the context can authorize that slot, then asks CredentialRequests to open the sealed value and returns the stored payload.

**Call relations**: It shares the same permission gate as the other credential methods by calling _credential_authorization. That keeps opening an authorization under the same admin and extension-slot rules as creating one.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext.fulfill_credential_authorization`  (lines 273–278)

```
async def fulfill_credential_authorization(self, slot: str, sealed: str, plaintext: str) -> None
```

**Purpose**: This completes an approved credential authorization by storing the actual secret value. It is the step that turns an authorization into a saved credential.

**Data flow**: The caller supplies the slot, sealed authorization, and plaintext secret. The function verifies authorization, opens the sealed value to confirm it is valid for this workspace and member, then stores the plaintext credential in the current workspace.

**Call relations**: It calls _credential_authorization for the safety checks and ws_current to reach the current workspace storage. It follows the same authorization path as begin and open so credentials cannot be stored outside the approved slot.

*Call graph*: calls 1 internal fn (_credential_authorization); 1 external calls (ws_current).


##### `ToolContext._credential_authorization`  (lines 280–289)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: This is the shared safety gate for all credential authorization actions. It makes sure there is a real speaking member, the extension declared the requested credential slot, credential storage is configured, and the speaker is an admin.

**Data flow**: It reads the speaker id, extension context, configured credential request service, and admin status. If any requirement is missing, it raises a clear error. If everything is valid, it returns the CredentialRequests object and the speaking member id.

**Call relations**: begin_credential_authorization, open_credential_authorization, and fulfill_credential_authorization all call this before doing credential work. It calls speaker_is_admin so credential changes are tied to a live workspace admin.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 3 (begin_credential_authorization, fulfill_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 291–300)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: This returns the external connector account id that a connector tool should use. It is a convenience wrapper for tools that only need the broker’s account id, not the full connection details.

**Data flow**: The caller gives a provider name and optionally a specific account id. The function resolves an allowed connector connection for this turn, then returns only that connection’s account_id.

**Call relations**: Connector extension tools call this before asking the broker to execute against an external account. It delegates the real selection and permission checks to connector_connection.

*Call graph*: calls 1 internal fn (connector_connection); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_connection`  (lines 302–339)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: This chooses the exact external account connection a turn is allowed to use. It protects private accounts by checking the grants available to the acting member and the agent’s shared grants.

**Data flow**: It reads private and shared grant tiers for the provider. If the caller named an account id, it searches both tiers for that account and returns its connection details or raises an error. If no account id was given, it prefers private grants over shared grants, requires exactly one match in the chosen tier, and returns the connection id, account id, and owner member id.

**Call relations**: connector_account calls this when it needs just the account id, and source tools call it when they need the full connection identity. It relies on _connector_account_tiers to separate private and shared grants before making the final choice.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


##### `ToolContext.connector_accounts`  (lines 341–349)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This lists the connector account ids available to the current turn for one provider. It lets tools show or choose from the accounts the acting member and agent may use.

**Data flow**: The caller gives a provider name. The function reads private and shared grant tiers, combines their account ids, removes duplicates, sorts them, and returns them as a tuple.

**Call relations**: Source tools call this when resolving which account to use. It delegates grant filtering to _connector_account_tiers so it follows the same privacy rules as connector_connection.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 351–370)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: This separates available connector grants into private and shared groups for a provider. It is the core permission filter that keeps a member’s private external accounts private by default.

**Data flow**: It checks that the grant system is configured, then reads all active grants. It filters private grants to those matching the provider and owned by the acting member, and filters shared grants to those matching the provider and marked shared. Each group is sorted by account id and returned.

**Call relations**: connector_connection and connector_accounts both call this before selecting or listing accounts. If grants are unavailable, it raises ConnectUnavailable so connector tools fail clearly instead of guessing or bypassing permissions.

*Call graph*: called by 2 (connector_accounts, connector_connection); 1 external calls (__init__).
