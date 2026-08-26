# Sandbox backends and file safety  `stage-10.1`

This stage is the behind-the-scenes safety and workspace layer for conversations. Its job is to give each conversation a private place to work, run commands there, expose previews, and keep files from leaking outside that place. conversation.py is the front door to a conversation’s /workspace: it opens it, reuses it, and reads or writes files there. session.py gives the rest of UFO one common “sandbox” interface for commands, files, ports, and proof that a request belongs to the right conversation. containment.py is the lock on the door: it checks untrusted paths so tricks like “../” or symbolic links cannot escape the workspace. local.py runs the workspace as a normal folder for development. terminal.py uses a member’s connected terminal as the worker. The Docker and E2B extensions provide stronger isolated workers, either in local containers or a cloud container. cache.py points sandboxes at a controlled dependency cache. ingress_host.py creates safe, unique names for exposed sandbox websites. preview.py names the document-to-image preview service. file_changes.py sets a shared maximum path length.

## Files in this stage

### Workspace entry and user sandboxes
Conversation workspaces are opened and bound to lightweight sandbox carriers, including local folders and member-connected terminals.

### `core/src/ufo/sandbox/conversation.py`

`domain_logic` · `turn setup, off-turn workspace access, and workspace browsing`

A conversation can have files that tools and users both need to see. Those files live in a sandbox workspace, and this file makes sure everyone reaches the same one instead of accidentally creating several different copies. Think of it like the key desk for a shared workshop: before anyone can work, browse, or drop off a file, they must get the correct key.

The main class, `ConversationSandbox`, decides where the workspace lives. It may be served by the deployment’s normal sandbox carrier, or by a connected user terminal. Once a conversation is tied to one place, that binding is stored in the database as a durable handle, so later requests return to the same workspace.

A key safety rule is that reading must not create anything. If a conversation has no sandbox yet, browsing or reading simply returns nothing. Writing is different: it opens or creates the sandbox, then copies the file in. The file also protects against races. If two tasks try to create the first sandbox at the same time, only one stored handle wins; the loser adopts the winner’s sandbox.

The file also carefully checks workspace directories to avoid unsafe paths such as links that escape the configured root directory. This matters because the sandbox may run code that can write files, so the project must be strict about exactly where those writes can go.

#### Function details

##### `ConversationSandbox.open`  (lines 93–137)

```
async def open(self, conversation_id: UUID, turn_id: UUID | None, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Opens the sandbox for a conversation, creating it if needed, and makes sure the conversation row records the durable sandbox handle. Someone uses this when a turn needs tools, or when an off-turn operation must write into the workspace.

**Data flow**: It receives a conversation id, optional turn id, run token, and environment variables. It reads the current stored sandbox binding and desired sandbox size, asks `_opened` to create or attach to the right sandbox, then tries to save the resulting handle in the database. If another opener won the race first, it retries using the winner’s handle. It returns a `SandboxSession`, which is the usable connection to the sandbox.

**Call relations**: The turn queue calls this through `_open_sandbox` when a turn is about to run, and `write` calls it when an attachment or other off-turn content must be placed into the workspace. Inside, it relies on `_binding` for the database state, `_opened` for the actual carrier choice, and `_claim` for the race-safe database update.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 2 (_open_sandbox, write); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 139–182)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Looks for an already-existing sandbox without creating a new one. This is the safe read path: browsing or reading a workspace must not cause a brand-new workspace to appear.

**Data flow**: It receives a conversation id and reads the stored sandbox handle. If there is no handle, or the handle belongs to a backend this instance cannot reach, it returns `None`. If the handle points to a terminal, it tries to attach through the terminal carrier. Otherwise it checks the local workspace directory, asks the configured carrier to attach, and returns a `SandboxSession` if that succeeds.

**Call relations**: `entries`, `prune`, and `read` call this before touching files. It delegates handle parsing to `sandbox_handle_id`, terminal access to `TerminalCarrier`, and local directory checking to `_existing_dir` when needed.

*Call graph*: calls 1 internal fn (_stored); called by 3 (entries, prune, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 184–194)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a conversation that has no sandbox yet to a connected user terminal’s working directory. This lets the conversation use the user’s terminal workspace as its sandbox location.

**Data flow**: It receives a conversation id and a terminal current working directory. It builds a stored handle that says “client terminal at this directory,” checks whether the conversation already has a handle, and if not tries to write that handle into the database. It returns `true` only if this call successfully made the claim.

**Call relations**: This is used when terminal admission wants to reserve a conversation for the live terminal. It reads through `_stored` and writes through `_claim`, so the same race-safe compare-and-swap rule used by sandbox opening protects terminal binding too.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 196–207)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Copies bytes into a file inside the conversation workspace and returns the `/workspace/...` path the agent can use. It exists for off-turn file landing, such as an inbound attachment arriving before a turn runs.

**Data flow**: It receives a conversation id, a relative file path, and file content as bytes. It first rejects content over the configured maximum size so the server is not forced to hold huge files in memory. Then it opens or creates the sandbox without a real turn token, writes the file into the session, and returns the workspace path string visible inside the sandbox.

**Call relations**: This function calls `open`, so a write is allowed to create the workspace if none exists. It then hands the actual file copy to the returned sandbox session and uses `workspace_path` to translate the relative path into the agent-facing `/workspace` form.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.prune`  (lines 209–220)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files under a workspace subdirectory, keeping only the newest requested number. This prevents unattended writers, such as log appenders, from growing the workspace forever.

**Data flow**: It receives a conversation id, a relative directory prefix, and a number of files to keep. It attaches only to an existing sandbox; if none exists, it does nothing. If a sandbox is reachable, it runs a small Python cleanup program inside the sandbox so file age and visibility match what the agent sees. If that program fails, it raises an error.

**Call relations**: `prune` depends on `existing` because cleanup should not create a workspace. It then runs the embedded `PRUNE_PROG` through the sandbox session, passing the target workspace path and the keep count.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.entries`  (lines 222–261)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists the files a member can see in a conversation’s workspace. It returns clean relative paths, sizes, and modification times, sorted by path.

**Data flow**: It receives a conversation id and attaches to an existing sandbox. If there is none, it returns an empty tuple. Otherwise it asks the sandbox file tool to walk the workspace, excluding names such as `.git`, checks that the tool returned a file list, warns if the result was truncated, converts each absolute path into a workspace-relative path, and wraps each item as a `WorkspaceFile`.

**Call relations**: Workspace browsing calls this read-style function. It uses `existing` to avoid creating a sandbox, `_workspace_rel` to strip either container or host workspace prefixes safely, `WorkspaceFile` to shape the result, and `warn` to report when the list may be incomplete.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 263–267)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns a path reported by the workspace file walker into a relative path suitable for display. It also catches paths that do not actually belong to the workspace.

**Data flow**: It receives the sandbox handle and a path string. It checks whether the path starts with either the in-container workspace root or the host workspace path. If it matches, it removes that root prefix and returns the remaining relative path. If it matches neither, it raises an error instead of trusting a suspicious path.

**Call relations**: `entries` calls this for every file returned by the sandbox file walker. Its job is small but important: it keeps the file browser from showing absolute internal paths or accepting paths outside the workspace.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 269–277)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Reads one file from a conversation workspace in chunks, or returns nothing if the workspace or file is absent. It is designed for safe file download or preview without creating new state.

**Data flow**: It receives a conversation id and a relative file path. It attaches only to an existing sandbox; if none exists, it returns `None`. It then asks the session whether the file exists. If the file exists, it returns an async byte stream for the file; otherwise it returns `None`.

**Call relations**: This is a read path, so it calls `existing` rather than `open`. The actual existence check and streaming are handed off to the sandbox session, which knows how to reach files in the selected carrier.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 279–335)

```
async def _opened(self, conversation_id: UUID, turn_id: UUID | None, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Chooses where a sandbox open should happen and performs the create or resume operation. This is the central decision point for terminal-backed versus deployment-backed workspaces.

**Data flow**: It receives the conversation id, optional turn id, previously stored handle, run token, environment variables, and requested sandbox size. It first checks whether the stored handle, or a currently connected terminal, points to a terminal workspace. If so, it creates a terminal-backed sandbox session. Otherwise it chooses the deployment carrier, prepares or resolves the host workspace directory, may change ownership for the sandbox user, and creates or resumes the sandbox there. It returns the backend name, carrier, and sandbox handle.

**Call relations**: `open` calls this during each open attempt. `_opened` constructs `SandboxSpec` objects for either `TerminalCarrier` or the normal carrier, uses `sandbox_handle_id` to understand stored handles, and uses `_provisioned_dir` when a local directory must be created.

*Call graph*: called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 337–352)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates and verifies the host directory for a conversation workspace. It is used when a write or turn needs a local workspace directory that may not exist yet.

**Data flow**: It receives a conversation id. It ensures the configured workspace root directory exists, resolves the trusted root, then creates the conversation-specific child directory while checking that the path stays inside the root and does not follow unsafe links below it. It returns the safe directory path.

**Call relations**: `_opened` uses this when the deployment’s own carrier needs a host workspace directory. It relies on `configured_root` and `contained_dir` from the containment helpers to prevent path escape problems.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 354–365)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds the already-existing host directory for a conversation workspace without creating it. This supports read-only operations that must not have side effects.

**Data flow**: It receives a conversation id. It resolves the configured workspace root and checks for the conversation directory using containment rules. If the directory is missing, it returns `None`; if the path exists but violates containment rules, the underlying check raises an error. If all is well, it returns the safe directory path.

**Call relations**: `existing` calls this for local deployment-backed workspaces when browsing or reading. It mirrors `_provisioned_dir` but deliberately skips creation, preserving the rule that reads do not provision workspaces.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 367–369)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Fetches only the stored sandbox handle for a conversation. It is a convenience wrapper around the fuller binding lookup.

**Data flow**: It receives a conversation id, calls `_binding`, discards the sandbox size, and returns the stored handle string or `None`.

**Call relations**: `existing`, `claim_terminal`, and `_claim` use this when they only need to know the current handle. It centralizes the database read through `_binding` so the workspace and conversation checks stay consistent.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 371–392)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the conversation’s sandbox binding and the owning agent’s requested sandbox size from the database. This tells an opener both whether there is already a sandbox and what size to use if it must create one.

**Data flow**: It receives a conversation id. Inside a workspace-scoped database transaction, it selects the conversation’s stored sandbox handle and joins to the agent row for the sandbox size, limited to the current workspace. If no row is found, it raises an error because the conversation does not belong to this workspace. Otherwise it returns the handle and size.

**Call relations**: `open` calls this before attempting to create or resume a sandbox, and `_stored` calls it for handle-only reads. It uses the current workspace from `ws_current` and database access from `workspace_tx`.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 394–415)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Safely writes a sandbox handle into the conversation row only if the row still contains the value this caller previously saw. This prevents two simultaneous openers from both believing they own the conversation’s workspace.

**Data flow**: It receives a conversation id, the handle value previously read, and the new handle it wants to store. It runs a conditional database update: if the row still matches the old value, it stores the new handle and returns it. If the update loses the race, it rereads the stored handle and returns the winner’s value. If the handle somehow disappeared, it raises an error.

**Call relations**: `open` uses this to settle races between concurrent sandbox creation attempts, and `claim_terminal` uses it to bind an unbound conversation to a terminal. When it loses a race, it calls `_stored` to learn which handle actually won.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### `core/src/ufo/sandbox/local.py`

`io_transport` · `request handling`

This file lets the system run sandbox work without Docker, cloud sandboxes, or extra services. The workspace is just a real directory on the host machine, and commands run with that directory as their current folder. To tools, paths still look like `/workspace`; this file rewrites those paths to the matching host directory before running anything.

The important safety idea is that this is convenient, not truly isolated. A process is still a host process. So the file is careful about two things: it builds a clean environment for commands instead of giving them the server’s secret-filled environment, and it uses containment checks when reading or writing files so a path cannot escape the workspace through tricks like symbolic links.

It also prepares a small scratch area. Think of it as a temporary toolbox beside the workspace: it contains the compiled `ufo` client, a scratch home directory, and proxy certificate files. Commands inherit proxy settings and sentinel API keys so outbound network traffic still goes through the sandbox proxy, where it can be metered and controlled.

The main class, `LocalCarrier`, can create or attach to a workspace, run commands, copy files in and out safely, delegate file operations to the sandbox file helper, and reject port dialing because local subprocesses do not have a separate externally reachable sandbox network address.

#### Function details

##### `_provision_scratch`  (lines 60–78)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates a temporary support directory used by the local carrier for its whole lifetime. This directory holds helper executables, small support modules, and a fake home directory for commands so they do not use the server’s real home folder.

**Data flow**: It takes no input. It creates a new temporary directory, adds `home` and `bin` folders, copies the sandbox helper scripts and support module into `bin`, makes the scripts executable, and returns the path to this scratch directory.

**Call relations**: This is used as the default factory for `LocalCarrier`’s `_scratch` field, so it runs when a local carrier is constructed. The paths it prepares are later used by `LocalCarrier._base_env` when building the environment for created, attached, and executed sandbox commands.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `LocalCarrier.create`  (lines 85–116)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a local sandbox handle for a conversation. It makes sure the host workspace directory exists, writes the proxy certificate into scratch space, and returns the information needed to run commands in that workspace.

**Data flow**: It receives a `SandboxSpec`, which contains the conversation ID, workspace path, run token, proxy information, and extra environment values. It creates the workspace directory, stores the proxy certificate, builds proxy and API-key environment variables, merges in the base environment and requested environment, and returns a `SandboxHandle` describing the local sandbox.

**Call relations**: This is called when a conversation needs a local workspace to be ready for use. It calls `LocalCarrier._base_env` to get the safe command environment, then packages everything into a `SandboxHandle` that later methods such as `exec`, `write`, `read`, and `file_op` use.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier._base_env`  (lines 118–140)

```
def _base_env(self) -> dict[str, str]
```

**Purpose**: Builds the minimal environment that every local sandbox command should receive. This protects server secrets from leaking into subprocesses and avoids host Git settings that could cause commands to hang or use the wrong credentials.

**Data flow**: It reads only a small allowlist from the host environment, such as locale and temporary-directory settings. It then adds a scratch `HOME`, a `PATH` containing the sandbox helper tools and Python location, and Git settings that disable system/global credential prompts. It returns this environment as a dictionary of strings.

**Call relations**: Both `LocalCarrier.create` and `LocalCarrier.attach` call this when preparing a handle. The resulting environment is later copied into subprocesses by `LocalCarrier.exec`, so it is the foundation for safe local command execution.

*Call graph*: called by 2 (attach, create); 1 external calls (Path).


##### `LocalCarrier.attach`  (lines 142–155)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an existing local workspace without creating it. This is useful for read-only browsing or resuming access only when the workspace directory already exists.

**Data flow**: It receives a `SandboxSpec` and checks whether the workspace path is already a directory. If not, it returns `None`. If it exists, it builds the base command environment and returns a `SandboxHandle` pointing at that existing directory.

**Call relations**: This is the read-only counterpart to `LocalCarrier.create`. It calls `LocalCarrier._base_env` just like creation does, then returns a handle that can be used by the same later operations, including reads and subprocess-backed file operations.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 157–199)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command inside the local workspace as a host subprocess. It gives the command the sandbox environment and enforces a timeout so runaway commands do not block the turn forever.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It finds the host workspace path, rewrites any `/workspace` text in the arguments to that host path, starts the subprocess in its own process group with standard input closed, captures standard output and error, and returns an `ExecResult`. If the command times out, it kills the whole process group and returns a timeout result.

**Call relations**: This is the main command-running path for the local carrier. It calls `_root` to find the workspace and `_kill_process_group` when a timeout or cancellation means the process tree must be stopped. It hands the caller an `ExecResult` containing the command’s output and exit code.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 3 external calls (__init__, create_subprocess_exec, wait_for).


##### `LocalCarrier.write`  (lines 201–224)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file inside the local workspace. It runs the real filesystem work in a background thread so the async event loop is not blocked.

**Data flow**: It receives a sandbox handle, a logical workspace path, and the bytes to write. It schedules `_write_contained` in a worker thread, which performs the safe path check and writes the bytes. It returns nothing when the write is complete.

**Call relations**: This is the async public wrapper used when something needs to deliver a file into the local sandbox. It delegates the sensitive filesystem details to `LocalCarrier._write_contained`, keeping the event loop responsive while disk I/O happens.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 226–228)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the actual safe write into the workspace. It uses the containment guard to ensure the target path really stays under the workspace before replacing the file contents.

**Data flow**: It receives the sandbox handle, logical path, and bytes. It converts the logical `/workspace` path to a name relative to the host workspace, opens that target through `contained_file`, and replaces the target’s contents with the provided bytes while preserving the appropriate permission mode. It changes the filesystem and returns nothing.

**Call relations**: This is called by `LocalCarrier.write` inside a worker thread. It relies on `_workspace_name` to translate the logical path and `_root` to find the host workspace directory, then hands the risky path traversal problem to `contained_file`.

*Call graph*: calls 2 internal fn (_root, _workspace_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 230–241)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the local workspace in chunks. It avoids blocking the async event loop while reading from the host filesystem.

**Data flow**: It receives a sandbox handle and logical path. It opens a safe, contained source file using `_contained_source`, then repeatedly reads chunks from it in a worker thread and yields those byte chunks to the caller. When done or interrupted, it closes the file.

**Call relations**: This is the public async read path for local sandbox files. It delegates the safety check and file opening to `LocalCarrier._contained_source`, then provides the data as an async stream so callers can consume large files without loading everything at once.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 243–252)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Safely opens a file inside the workspace for reading. It refuses missing paths and paths that cannot be proven to stay inside the workspace.

**Data flow**: It receives a sandbox handle and logical path. It translates the logical path, uses the containment guard to inspect the real filesystem, checks that the target exists, opens it as a byte stream, and returns that open reader. If the path is outside the workspace or missing, it raises `FileNotFoundError`.

**Call relations**: This is called by `LocalCarrier.read` before streaming begins. It uses `_workspace_name` and `_root` to locate the intended file and `contained_file` to prevent path escape through symbolic links or swapped directories.

*Call graph*: calls 2 internal fn (_root, _workspace_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 254–259)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs higher-level file operations through the same `ufo fs` helper used inside other sandbox carriers. This keeps local behavior aligned with container-style sandboxes.

**Data flow**: It receives a sandbox handle, an operation name, and operation parameters. It passes those to `ufo_fs_file_op`, which runs the helper command against the workspace and returns a dictionary result describing the operation outcome.

**Call relations**: This method is the local carrier’s bridge to the shared sandbox file-operation system. Instead of reimplementing each file command here, it hands the request to `ufo_fs_file_op`, using this carrier’s `exec` behavior underneath.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `LocalCarrier.dial`  (lines 261–269)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Refuses attempts to expose or reach a service through a per-sandbox network address. The local carrier runs processes directly on the host, so it cannot provide the same remote port access model as cloud carriers.

**Data flow**: It receives a sandbox handle and port number, but does not use them to create a connection target. It raises `SandboxUnreachable` with a message explaining that a remote carrier is needed for this feature.

**Call relations**: This is called when the broader sandbox system wants a network target for a service running inside the sandbox. For the local carrier, the story stops here: it reports that this carrier cannot support that flow.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 272–277)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Forcefully stops a subprocess and any child processes in its process group. This prevents a timed-out command from leaving behind runaway background work.

**Data flow**: It receives an asyncio subprocess object. It sends a kill signal to the process group whose ID is the subprocess ID, ignores the case where the process is already gone, and waits until the process has finished. It returns nothing.

**Call relations**: This helper is called by `LocalCarrier.exec` when a command times out or the surrounding task is cancelled. It is deliberately group-wide so a shell script that started child processes cannot escape cleanup.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 280–283)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the host directory that backs `/workspace` for a local sandbox handle. It also catches the invalid case where a local handle has no host workspace path.

**Data flow**: It receives a `SandboxHandle`. If the handle has a workspace host path, it converts that string into a `Path` and returns it. If the path is missing, it raises a runtime error because the local carrier cannot operate without a host directory.

**Call relations**: This small helper is used by command execution and contained file access: `LocalCarrier.exec`, `LocalCarrier._write_contained`, and `LocalCarrier._contained_source` all call it before touching the workspace.

*Call graph*: called by 3 (_contained_source, _write_contained, exec); 1 external calls (Path).


##### `_workspace_name`  (lines 286–290)

```
def _workspace_name(path: str) -> PurePosixPath
```

**Purpose**: Converts a logical sandbox path like `/workspace/file.txt` into the relative name used under the host workspace directory. It only does the string mapping; real safety checks happen later through the containment guard.

**Data flow**: It receives a path string. It treats it as a POSIX-style path, removes the `/workspace` prefix, and returns the remaining relative path as a `PurePosixPath`.

**Call relations**: This helper is called by `LocalCarrier._write_contained` and `LocalCarrier._contained_source` before they ask `contained_file` to safely open a real file. It connects the sandbox-facing path language to the host directory layout.

*Call graph*: called by 2 (_contained_source, _write_contained); 1 external calls (PurePosixPath).


### `core/src/ufo/sandbox/terminal.py`

`io_transport` · `request handling and sandbox tool execution`

Most sandboxes are reached like remote machines: the server connects to them and runs commands there. A member’s own terminal is different. The server cannot dial into it directly, so it must ask the already-connected client to do work and then wait for the next client request to bring back the answer. This file is that meeting place, or rendezvous desk.

It keeps one shared slot per conversation. A slot remembers the terminal’s current working directory, who is allowed to answer for it, the operation currently waiting, any staged bytes for a file write, and any tasks waiting on either side. Because the workflow and the web connection can run on different event loops, it uses a lock plus careful wakeups so each waiting task is resumed on its own loop.

The `Terminals` class is the in-process rendezvous. The `TerminalCarrier` class makes this look like the normal sandbox interface: create a sandbox handle, run commands, read and write files, and run higher-level file operations. It also rewrites logical `/workspace/...` paths to the real directory where the member launched the tool. Without this file, terminal-bound work would either be impossible, would lose replies during reconnects, or would risk sending file operations to the wrong local path.

#### Function details

##### `TerminalTransport.connect`  (lines 228–228)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Defines the contract for recording that a terminal connection is now attached to a conversation. Implementations use it when the member’s client is online and ready to receive operations.

**Data flow**: It takes a conversation id, the terminal’s current directory, and the member id. An implementation stores that binding so later sandbox work knows where to send requests and who may answer them.

**Call relations**: This is part of the transport interface used by the surface connection side. `Terminals.connect` is the in-process implementation that actually wakes any work waiting for the terminal to arrive.


##### `TerminalTransport.disconnect`  (lines 230–230)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Defines the contract for noting that a terminal connection has gone away. This matters because operations should not wait forever on a terminal that is no longer connected.

**Data flow**: It takes a conversation id. An implementation reduces or removes the stored connection state for that conversation.

**Call relations**: The web surface calls this when the held client stream ends. `Terminals.disconnect` supplies the local behavior for cleaning up idle state.


##### `TerminalTransport.workspace`  (lines 232–232)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Defines how callers can ask what workspace directory is currently bound to a conversation. This is a quick look, not a wait.

**Data flow**: It takes a conversation id and reads the transport’s stored binding. It returns the directory and member id, or nothing if no terminal is currently known.

**Call relations**: This is a shared interface method for transports. The local version is `Terminals.workspace`, while other backends can answer from shared storage.


##### `TerminalTransport.arrived`  (lines 234–234)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Defines how callers wait for a terminal to be connected. This is needed because the client connection naturally drops and reconnects around long holds.

**Data flow**: It takes a conversation id and a grace period in seconds. It returns the bound workspace if a terminal is present or arrives in time, otherwise it returns nothing.

**Call relations**: Sandbox opening and operation sending depend on this method before they assume the terminal is gone. `Terminals.arrived` implements the wait locally.


##### `TerminalTransport.send`  (lines 236–245)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Defines how the server asks the terminal to do one operation and waits for its answer. Operations include command execution, file reads, file writes, and file-tool actions.

**Data flow**: It receives the conversation, operation kind, timeout, optional name, argument, JSON parameters, and optional file bytes. It sends that request through the transport and returns the terminal’s reply bytes or raises an error if the terminal disappears or reports failure.

**Call relations**: The sandbox carrier calls this whenever a tool needs the member’s machine to do work. `Terminals.send` is the in-process implementation that pairs the outgoing request with the later client reply.


##### `TerminalTransport.next_op`  (lines 247–249)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Defines how the connected client asks, “What should I do next?” It lets the terminal side pick up the operation that a workflow is waiting on.

**Data flow**: It takes a conversation id and optionally an operation id to avoid repeating. It returns the next terminal operation when one is available.

**Call relations**: The surface route serving the connected terminal uses this shape. `Terminals.next_op` supplies the local waiting and delivery behavior.


##### `TerminalTransport.staged`  (lines 251–253)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Defines how the client fetches bytes that were staged for an in-flight write operation. The bytes are kept out of the small directive message.

**Data flow**: It receives a conversation id, operation id, and optional member id. It returns the staged bytes only if that exact operation is still active and the member is allowed to read them.

**Call relations**: This supports write operations sent by `TerminalCarrier.write`. `Terminals.staged` is the in-process implementation; other transports may fetch these bytes from shared storage.


##### `TerminalTransport.resolve`  (lines 255–262)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Defines how the terminal answers an operation. It reports whether the answer matched the operation that was actually waiting.

**Data flow**: It takes the conversation id, operation id, reply bytes, optional failure text, and optional member id. It wakes the waiting sender with either bytes or a terminal-operation failure and returns true only when the answer was accepted.

**Call relations**: The client reply route uses this after running an operation. `Terminals.resolve` performs the local match-and-wake behavior.


##### `TerminalTransport.in_flight`  (lines 264–264)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Defines how callers can inspect the operation currently waiting for a reply. This is mainly useful for tests or operator visibility.

**Data flow**: It receives a conversation id and reads the current operation state. It returns the active operation or nothing.

**Call relations**: This belongs to the transport interface so different backends can expose the same observation point. `Terminals.in_flight` is the local implementation.


##### `_wake`  (lines 267–276)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: Wakes a waiting asynchronous task safely, even when the caller is running on a different thread. This avoids touching an asyncio future from the wrong event loop.

**Data flow**: It receives a stored future plus the event loop that owns it, and an answer to deliver. It schedules a tiny setter on that loop, so the future is completed in the right place.

**Call relations**: `Terminals.connect` uses it to wake code waiting for a terminal to arrive, `Terminals.send` uses it to deliver an operation to a watcher, and `Terminals.resolve` uses it to return a terminal reply to the sender.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 272–274)

```
def _set() -> None
```

**Purpose**: Completes the future if it has not already been completed. It is the small callback that actually runs on the future’s own event loop.

**Data flow**: It reads the future captured by `_wake` and the answer also captured there. If the future is still pending, it stores the answer as the result.

**Call relations**: `_wake` schedules this callback with the event loop’s thread-safe scheduling method. It is not called directly by the rest of the file.


##### `Terminals.connect`  (lines 292–305)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Records that a member’s terminal connection is now present for a conversation. It also wakes any workflow that was waiting during a normal reconnect gap.

**Data flow**: It takes the conversation id, current directory, and member id. It creates or updates the conversation slot, increments the connection count, removes arrival waiters, and wakes them after releasing the lock.

**Call relations**: The surface side calls this when a terminal connects. It uses `_wake` so waiting code in `Terminals.arrived` can continue on its own event loop.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 307–314)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Marks one terminal connection as closed and removes the conversation slot when it is safe to do so. It keeps active operations alive so a reconnect can still answer them.

**Data flow**: It takes a conversation id, finds the slot, and decreases its connection count. If there are no connections and no operation is waiting for a reply, it deletes the slot.

**Call relations**: The surface side calls this when a client stream ends. Its cleanup rules work with `Terminals.send`, which may keep a slot alive while an operation is in flight.


##### `Terminals.workspace`  (lines 316–321)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the currently known terminal workspace for a conversation. It is a quick snapshot used when the caller does not want to wait.

**Data flow**: It reads the slot for the conversation under the lock. If present, it returns a `TerminalWorkspace` containing the directory and member id; otherwise it returns nothing.

**Call relations**: This is the local implementation of the `TerminalTransport.workspace` contract. It does not wake or wait for anything.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 323–345)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits for a terminal to be connected, but only up to a caller-chosen grace period. This prevents false failures during the client’s normal reconnect cycle.

**Data flow**: It checks whether the conversation already has a slot. If not, it registers a future in the arrivals list, waits until connect wakes it or the deadline passes, then returns the workspace or nothing.

**Call relations**: `Terminals.send` calls this before sending an operation. `TerminalCarrier.create` and `TerminalCarrier.attach` call the transport’s `arrived` method to decide whether a terminal-bound sandbox can be opened.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 347–352)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: Removes a timed-out arrival waiter from the waiting list. This keeps old futures from building up after callers stop waiting.

**Data flow**: It takes the conversation id and the specific future to remove. It filters that future out of the arrival list and deletes the list if it becomes empty.

**Call relations**: `Terminals.arrived` calls this when its wait runs out. It is a cleanup helper for the arrival-wait path only.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 354–429)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one operation to the connected terminal and waits for the answer. It also serializes operations so the terminal is only asked to do one thing at a time.

**Data flow**: It waits for a terminal to be present, waits for its turn, creates a unique operation id, stores the operation and any staged body in the slot, wakes a watching client if one is waiting, then waits for `resolve` to provide bytes or failure. On timeout or completion it clears the slot state and wakes queued senders.

**Call relations**: Sandbox operations in `TerminalCarrier` call the transport’s `send` method. This implementation uses `Terminals.arrived`, `Terminals._take_turn`, `_wake`, and `uuid4`; terminal replies come back through `Terminals.resolve`.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 5 external calls (__init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 431–462)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: Waits until this sender owns the conversation’s single operation slot. It prevents two overlapping terminal operations from racing each other.

**Data flow**: It checks the slot under the lock. If the terminal is free it marks it busy and returns; otherwise it queues a future and waits until the current operation releases the slot or the deadline expires.

**Call relations**: `Terminals.send` calls this before installing a new operation. When `send` finishes, it wakes queued waiters so they can compete again for the turn.

*Call graph*: called by 1 (send); 5 external calls (__init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 464–489)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Lets the connected terminal wait for the next operation it should run. It can return an already pending operation or wait for a future one.

**Data flow**: It checks the slot for an undelivered operation that is not excluded. If one exists, it marks it delivered and returns it; otherwise it stores a watcher future and waits until `send` wakes it with an operation.

**Call relations**: The terminal-facing route calls this while holding the client connection. `Terminals.send` wakes the watcher when it has a new operation ready.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 491–503)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Returns file bytes staged for a write operation. This lets the directive stay small while the client fetches the actual content separately.

**Data flow**: It receives the conversation id, operation id, and optional member id. It checks that the same operation is still active and that the member matches, then returns the stored bytes or nothing.

**Call relations**: The client uses this after receiving a write operation from `next_op`. `TerminalCarrier.write` is the sender that placed those bytes in the slot through `send`.


##### `Terminals.in_flight`  (lines 505–510)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports the operation currently waiting for a terminal reply. This is an inspection hook rather than part of normal execution.

**Data flow**: It reads the slot for the conversation under the lock. It returns the current operation if one is stored, otherwise nothing.

**Call relations**: Tests or operator views can call this to see what the workflow is waiting on. It does not affect `send`, `next_op`, or `resolve` state.


##### `Terminals.resolve`  (lines 512–541)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts the terminal’s answer to an in-flight operation and wakes the workflow that sent it. It rejects stale, duplicate, wrong-member, or wrong-operation answers.

**Data flow**: It compares the conversation id, operation id, reply waiter, resolved flag, and optional member id against the slot. If everything matches, it marks the operation resolved and wakes the sender with reply bytes or a `TerminalOpFailed` object; otherwise it returns false.

**Call relations**: The client reply route calls this after running the operation it got from `next_op`. It uses `_wake` to resume the waiting `Terminals.send` call safely.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 556–598)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a sandbox handle for a workspace that is actually the member’s connected terminal directory. It refuses to proceed if the terminal is missing or standing in a different directory.

**Data flow**: It waits for the terminal binding, compares the bound directory with the requested workspace path, builds proxy environment variables that include the run token, and returns a `SandboxHandle`. If the binding is absent or mismatched, it raises `TerminalGone`.

**Call relations**: Sandbox setup calls this when the selected backend is the client terminal. It relies on the transport’s `arrived` method and produces the handle later used by `exec`, `write`, `read`, and `file_op`.

*Call graph*: 3 external calls (__init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 600–613)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reattaches to an already-bound terminal sandbox outside the main turn. This lets file browsers and background writes reach the same terminal workspace.

**Data flow**: It asks the transport whether the terminal is already present, with no grace wait. If the bound directory matches the resume id, it returns a lightweight `SandboxHandle`; otherwise it returns nothing.

**Call relations**: Off-turn callers use this instead of creating a new sandbox. It depends on the transport’s shared binding so it can work even when another process is holding the actual terminal connection.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 615–628)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command in the member’s terminal sandbox while translating logical `/workspace` paths into the member’s real directory. This keeps tools using the same path language across container and terminal sandboxes.

**Data flow**: It reads the real workspace root from the handle, replaces `/workspace` occurrences in the command arguments with that root, and passes the resolved arguments to `_exec`. It returns the command result from `_exec`.

**Call relations**: Higher-level sandbox users call this for command execution. It delegates the terminal-specific sending and reply parsing to `TerminalCarrier._exec`.

*Call graph*: calls 2 internal fn (_exec, _root).


##### `TerminalCarrier._exec`  (lines 630–665)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command whose paths have already been resolved to real member-machine paths. It is used when the caller has built exact host paths and must avoid rewriting them again.

**Data flow**: It sends an `exec` operation through the terminal transport with argv and environment as JSON. It parses the JSON reply, decodes stdout and stderr from base64, normalizes timeout reporting, and returns an `ExecResult`.

**Call relations**: `TerminalCarrier.exec` calls this after path rewriting, and `_enumerate` calls it for generated shell commands that list files. It uses `_reply_object` and `_reply_stream` to validate the client’s reply shape.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (_enumerate, exec); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 667–680)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file in the member’s terminal workspace. The bytes are staged separately so large file content is not crammed into the directive message.

**Data flow**: It maps the logical path to the client’s real path, sends a write operation with that path and the content body, and returns when the terminal confirms success. If the terminal reports failure, it raises an `OSError`.

**Call relations**: Sandbox file-copy code calls this for copy-in. The transport stores the body for the client to retrieve through `staged` while the write operation is active.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 682–698)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a file from the member’s terminal workspace and streams it back in chunks. It turns the terminal’s missing-file signal into the normal `FileNotFoundError` used by other carriers.

**Data flow**: It maps the logical path to the real client path, sends a read operation, receives the entire file as reply bytes, and yields those bytes in fixed-size chunks. If the terminal says the file does not exist, it raises `FileNotFoundError`; other terminal failures become `OSError`.

**Call relations**: Sandbox file-copy code calls this for copy-out. It uses `_client_path` for path mapping and the transport’s `send` method for the actual terminal request.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 700–745)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level file-system operation, such as grep, glob, or change scanning, on the member’s terminal workspace. It makes sure path parameters point at the real bound directory before asking the client to run its native operation.

**Data flow**: It rewrites selected path-like parameters under the real workspace root. For operations that need a precomputed file listing, it first calls `_enumerate`; then it sends a file operation with JSON parameters, parses the JSON reply, raises any reported error, and returns the result object.

**Call relations**: The sandbox file-system layer calls this for structured file tools. It uses `_root`, `_under_root`, `_enumerate`, and `_reply_object`, then sends the final request through the terminal transport.

*Call graph*: calls 4 internal fn (_enumerate, _reply_object, _root, _under_root); 1 external calls (dumps).


##### `TerminalCarrier._enumerate`  (lines 747–768)

```
async def _enumerate(self, handle: SandboxHandle, op: str, walk_root: str, program: str, arguments: tuple[str, ...]=()) -> None
```

**Purpose**: Runs a shell listing step before file operations that need a stable view of the tree. This keeps the server, not the client, in charge of exactly what gets visited.

**Data flow**: It builds a small shell command with `UFO_WALK_ROOT` set to the chosen root, passes optional arguments, and executes it through `_exec`. If the listing command exits with an unexpected code, it raises a clear error.

**Call relations**: `TerminalCarrier.file_op` calls this before grep, glob, or changes scans. It delegates actual command execution to `_exec` so enumeration travels through the same terminal operation path as other commands.

*Call graph*: calls 1 internal fn (_exec); called by 1 (file_op); 1 external calls (quote).


##### `TerminalCarrier.dial`  (lines 770–774)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Rejects attempts to expose a network port from a terminal-bound sandbox. A member’s local terminal does not provide the same per-port remote access that container carriers can provide.

**Data flow**: It receives a sandbox handle and port number but does not use them to create a target. It always raises `SandboxUnreachable` with guidance to use a remote carrier for this kind of access.

**Call relations**: Callers that expect sandbox port dialing reach this method through the carrier interface. This implementation makes the limitation explicit instead of pretending a reachable host exists.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 777–785)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: Extracts one captured command stream, such as stdout or stderr, from an exec reply. The stream is base64-encoded text-safe data, so this function decodes it back to bytes.

**Data flow**: It reads the named `<stream>_b64` field from the parsed reply object. If the field is missing or not valid base64, it raises; otherwise it returns decoded bytes.

**Call relations**: `TerminalCarrier._exec` calls this for stdout and stderr after `_reply_object` has parsed the JSON reply. It protects callers from silently treating malformed replies as empty output.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 788–795)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: Parses a terminal reply that should be a JSON object. It gives a clear error when the client sends something in the wrong shape.

**Data flow**: It decodes reply bytes as UTF-8, parses JSON, and checks that the result is a dictionary-like object. It returns that object or raises a runtime error describing the bad reply.

**Call relations**: `TerminalCarrier._exec` uses this for command replies, and `TerminalCarrier.file_op` uses it for file-operation replies. It is the common validation gate before reading fields from terminal responses.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 798–801)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: Returns the real host directory that backs `/workspace` for a terminal sandbox. It raises if the handle does not carry that directory, because terminal path mapping depends on it.

**Data flow**: It reads `workspace_host_path` from the sandbox handle. If present, it returns that string; if absent, it raises a runtime error.

**Call relations**: `TerminalCarrier.exec`, `TerminalCarrier.file_op`, and `_client_path` call this before translating paths. It centralizes the assumption that terminal sandboxes are rooted in a bound local directory.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 804–810)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: Maps a logical `/workspace/...` path to the real path under the member’s bound directory. It does this carefully so only the leading `/workspace` is replaced.

**Data flow**: It reads the real root with `_root`, then passes the root and requested path to `_under_root`. The result is either a real path under the bound directory or the original path if it was not under `/workspace`.

**Call relations**: `TerminalCarrier.write` and `TerminalCarrier.read` call this before sending paths to the client. It uses `_under_root` for the actual prefix-aware mapping.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 813–818)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: Rewrites a path under logical `/workspace` so it points under a given real root directory. Paths outside `/workspace` are left unchanged.

**Data flow**: It turns the input path into a POSIX-style path, checks whether it is relative to `/workspace`, and if so appends the remaining part to the supplied root. It returns the rewritten path string, or the original path if no rewrite applies.

**Call relations**: `_client_path` uses this for reads and writes, and `TerminalCarrier.file_op` uses it for selected file-operation parameters. It is the low-level path mapping helper for terminal-bound sandboxes.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### Container and cloud backends
Docker and E2B provide stronger isolated execution backends, with shared cache configuration supporting controlled dependency access.

### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox lifecycle and command/file request handling`

A sandbox is a safe work area where an agent can run commands and edit files. This file provides a Docker-based sandbox: each conversation gets a predictable container name, a mounted workspace folder, and its own Docker network. Think of it like giving each conversation its own workshop room, with the project controlling the doorway to the internet.

The important safety rule is that network credentials are not stored inside the container. Every command run inside Docker gets fresh proxy environment variables for that turn. The proxy sees the turn token, enforces allowed network destinations, and replaces a harmless placeholder API key with the real one only outside the sandbox.

The file also tries to be kind to the host machine. Docker containers and networks consume memory and limited bridge network space, so old idle containers are stopped and their networks removed. They are not deleted, so their workspace survives. If the conversation is used again, the container can be started and reconnected.

Most work goes through the Docker command-line tool. The carrier can create or attach to a container, execute commands, stream file reads and writes, install the proxy certificate so HTTPS works, and recover if a container was stopped between operations.

#### Function details

##### `_docker`  (lines 73–87)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and returns its exit code, standard output, and error output. It also enforces a deadline so a stuck Docker command does not hang the whole service.

**Data flow**: It receives Docker arguments, optional input bytes, and a timeout. It starts a `docker ...` subprocess, sends the input to it, waits for output, and returns the result. If the deadline passes, it kills the subprocess and returns a special timeout code plus a timeout message.

**Call relations**: This is the low-level doorway to Docker for the rest of the file. Container creation, lookup, restart, stopping, network setup, certificate installation, and error inspection all call this helper instead of starting Docker themselves.

*Call graph*: called by 12 (_death_report, _ensure_network, _held_id, _install_ca, _reclaim_idle, _release, _revive, _running_id, _stopped_id, _write_started (+2 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 100–203)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a sandbox container for a conversation, or reconnects to the existing one if it already exists. It also prepares the per-command proxy settings that make network use safe and accountable.

**Data flow**: It receives a sandbox specification with the conversation ID, image, workspace path, proxy details, run token, and extra environment variables. It first reclaims old idle containers, builds the container name and proxy environment, checks whether the container is already running or stopped, and either reuses, restarts, or creates it. It installs the current proxy certificate and returns a sandbox handle containing the container ID and per-turn execution environment.

**Call relations**: This is the main opening step for Docker sandboxes. It calls helpers to reclaim idle resources, find existing containers, revive stopped containers, create networks, run Docker, and install certificates. If a concurrent create already won the race, it attaches to that winner instead of making a second container.

*Call graph*: calls 8 internal fn (_ensure_network, _install_ca, _network_name, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 1 external calls (__init__).


##### `DockerCarrier.attach`  (lines 205–229)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an existing conversation container without creating a new one. It is used when the system wants to inspect or read from a sandbox only if it already exists.

**Data flow**: It receives the same sandbox specification used for creation. It looks for a running container by name; if none is running, it looks for a stopped one and tries to restart it. It returns a sandbox handle if a usable container exists, or `None` if there is no container or it cannot be revived.

**Call relations**: This is the gentler counterpart to `DockerCarrier.create`. It calls the running/stopped lookup helpers and may call `_revive`, but it never calls the fresh Docker run path.

*Call graph*: calls 3 internal fn (_revive, _running_id, _stopped_id); 1 external calls (__init__).


##### `DockerCarrier._reclaim_idle`  (lines 231–299)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops old idle containers and removes their per-conversation Docker networks so the host does not run out of memory or Docker network space. It preserves the container and workspace so the conversation can be restarted later.

**Data flow**: It receives the conversation ID currently being opened and records it as recently touched. It asks Docker for existing UFO containers and networks, adopts any it did not already know about, finds conversations that have been idle long enough and have no command in progress, then stops their containers and removes their networks. If release fails, it puts the touch record back so a later create can retry.

**Call relations**: `DockerCarrier.create` calls this before opening a new sandbox. It uses `_held_id` to identify containers and `_release` to stop and detach resources, while taking lifecycle locks so it does not fight with a simultaneous restart.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 301–333)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside a sandbox container and returns its output, error text, exit code, and timeout information. It also pins the container as active so idle cleanup will not stop it mid-command.

**Data flow**: It receives a sandbox handle, a command argument tuple, and a timeout. It marks the conversation as in flight, adds the handle’s proxy environment to `docker exec`, runs the command, and retries once if Docker says the container was stopped. It returns an `ExecResult` and updates the last-touched time when done.

**Call relations**: Higher-level sandbox command execution flows through this method. It calls `_docker` to run `docker exec` and `_revive` if the container was reclaimed just before the command.

*Call graph*: calls 2 internal fn (_revive, _docker); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 335–353)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes from the host into a file inside the sandbox workspace. It uses a guarded in-container copy program rather than a simple shell redirect, so tricky paths such as symlinks are handled safely.

**Data flow**: It receives a sandbox handle, a path, and file content bytes. It marks the conversation active, streams the bytes into Docker through standard input, retries if the container had been stopped, and raises an `OSError` if the write fails. Finally it records that the conversation was touched.

**Call relations**: File-upload and workspace-write operations use this method. It delegates the actual Docker execution to `_write_started` and uses `_revive` for recovery after idle reclaim.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 355–371)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Performs one actual attempt to write bytes into the container. It is separated from `write` so the caller can retry cleanly after restarting a stopped container.

**Data flow**: It receives a handle, target path, and bytes. It runs `python3` inside the container with the sandbox copy-in program, sends the bytes through standard input, and returns the Docker exit code plus error output.

**Call relations**: `DockerCarrier.write` calls this helper for the first attempt and, if needed, for the retry after `_revive`.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write).


##### `DockerCarrier.read`  (lines 373–407)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks, without loading the whole file into memory. It reports file errors in a way that resembles normal local file reading.

**Data flow**: It receives a sandbox handle and path. It marks the conversation active, starts a `cat` command in the container, yields bytes as they arrive, and then checks whether the command failed. If the container was stopped before any useful read, it revives and retries; if the failure looks like a normal filesystem error, it raises `OSError`; otherwise it raises a runtime error with diagnostic details.

**Call relations**: Workspace read operations use this method. It relies on `_read_started` to create a streaming attempt, `_revive` to recover stopped containers, and `_death_report` when `cat` dies without explaining why.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 409–451)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Creates one streaming file-read attempt and a small place to record whether that attempt failed. This structure lets `read` retry without reusing a half-finished asynchronous generator.

**Data flow**: It receives a handle and path. It prepares an inner stream that will run `docker exec cat <path>`, yield output chunks, and append failure details if `cat` exits with an error. It returns both the stream and the shared failure list.

**Call relations**: `DockerCarrier.read` calls this before the first read attempt and again after a revive retry. The inner `DockerCarrier._read_started.stream` does the actual subprocess work.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 424–449)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `cat` inside the container and yields the file contents chunk by chunk. It also makes sure the Docker exec process is killed if the caller stops reading early.

**Data flow**: It starts a Docker subprocess with standard output and error pipes. It repeatedly reads up to a fixed chunk size from standard output and yields each chunk. After output ends, it reads error text, waits for the process exit code, records a failure if needed, and cleans up any still-running process on exit.

**Call relations**: This inner generator is returned by `_read_started` and consumed by `DockerCarrier.read`. It talks directly to `asyncio.create_subprocess_exec` because streaming needs tighter control than the general `_docker` helper provides.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 453–471)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Adds useful Docker container state to a mysterious read failure. This helps distinguish a killed `cat` process from a stopped, removed, or out-of-memory container.

**Data flow**: It receives a sandbox handle. It runs `docker inspect` on the container and asks for status, exit code, and whether Docker says it was killed for using too much memory. It returns a human-readable diagnostic string, or a message saying inspection itself failed.

**Call relations**: `DockerCarrier.read` calls this only when the read process exits badly without any error text. It uses `_docker` to ask Docker for the container’s current state.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 473–478)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured sandbox file operation, such as one provided by the shared sandbox filesystem tool. It gives the Docker carrier the same file-operation interface as other carriers.

**Data flow**: It receives a sandbox handle, an operation name, and operation parameters. It passes those to the shared `ufo_fs_file_op` helper, which runs the baked-in `ufo fs` command through this carrier. It returns the operation’s result dictionary.

**Call relations**: Higher-level code can call this instead of hand-writing reads, writes, or command invocations. The shared helper calls back through the carrier’s execution path as needed.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `DockerCarrier.dial`  (lines 480–487)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that this Docker carrier cannot expose an in-sandbox network port to the outside caller. For port access, the project expects a different remote carrier.

**Data flow**: It receives a sandbox handle and port number, but does not use them to create a connection. It immediately raises `SandboxUnreachable` with an explanation.

**Call relations**: Code that wants to reach a browser debugging port or preview server may call this carrier method. In this implementation the flow stops here with a clear error instead of pretending a route exists.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 489–503)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation’s container and removes its Docker network. This frees the scarce host resources that idle reclaim is trying to protect.

**Data flow**: It receives a conversation ID and maybe a container ID. If there is a container, it asks Docker to stop it. Then it removes the conversation’s network. It returns `true` if the resources are released or the network was already gone, and `false` if stopping or removal failed.

**Call relations**: `DockerCarrier._reclaim_idle` calls this while holding the conversation’s lifecycle lock. It uses `_network_name` to compute the network name and `_docker` to perform Docker stop and network removal.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 505–525)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Restarts a stopped container and reconnects the Docker network it needs. This is how a quiet conversation can continue after idle cleanup stopped its sandbox.

**Data flow**: It receives a conversation ID and container ID. Under a lifecycle lock, it marks the conversation touched, ensures the per-conversation network exists, connects the container to it, and starts the container. It returns whether the start succeeded; serious network creation failures raise an error.

**Call relations**: Creation, attach, command execution, reads, and writes all call this when they find a stopped container. It calls `_network_name`, `_ensure_network`, and `_docker`, and its lock keeps it from interleaving with `_release`.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (attach, create, exec, read, write).


##### `DockerCarrier._held_id`  (lines 527–535)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a given container name, no matter whether it is running, paused, or exited. Idle cleanup needs this because any of those states can still matter for release.

**Data flow**: It receives a container name. It runs `docker ps -aq` with an exact name filter, checks for Docker command failure, and returns the found ID or `None` if there is no match.

**Call relations**: `DockerCarrier._reclaim_idle` calls this before releasing a stale conversation so `_release` knows which container, if any, should be stopped.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 537–546)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a named container only if it is stopped. This tells the carrier whether a previously reclaimed sandbox can be restarted.

**Data flow**: It receives a container name. It asks Docker for exited containers with that exact name, raises if Docker itself failed, and returns the container ID or `None`.

**Call relations**: `DockerCarrier.create` and `DockerCarrier.attach` call this after no running container is found. If it returns an ID, those methods may hand the container to `_revive`.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 548–559)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a named container only if it is currently running. It treats Docker command failures as real errors, not as absence.

**Data flow**: It receives a container name. It runs `docker ps` with exact name and running-status filters, raises if Docker failed, and returns the container ID or `None` if there is no running match.

**Call relations**: `DockerCarrier.create` uses this to reuse an already-running sandbox or resolve a create race. `DockerCarrier.attach` uses it as the first check before considering a stopped container.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 561–562)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Docker network name for one conversation. The name combines the configured network prefix with the conversation UUID in a compact form.

**Data flow**: It receives a conversation ID. It formats that ID into a deterministic Docker network name and returns the string.

**Call relations**: `DockerCarrier.create`, `_revive`, and `_release` use this so they all refer to the same per-conversation network.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 564–575)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if needed. It is safe when two callers race to create the same network.

**Data flow**: It receives a network name. It asks Docker whether the network already exists; if not, it runs `docker network create`. If Docker says the network already exists, that is accepted as success because another caller got there first.

**Call relations**: `DockerCarrier.create` calls this before running a fresh container. `_revive` calls it before reconnecting and starting a stopped container.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 577–590)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current proxy certificate inside a container so HTTPS tools trust the project’s egress proxy. This is repeated for reused containers because the proxy certificate can change after a server restart.

**Data flow**: It receives a container ID and certificate text. It runs a root shell command inside the container, writes the certificate file through standard input, and updates the container’s certificate store. If that fails, it raises an error with Docker’s message.

**Call relations**: `DockerCarrier.create` calls this on every successful path: existing running container, revived container, race winner, or freshly created container. It uses `_docker` to perform the in-container write and update.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `manifest`  (lines 593–598)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It says that the extension provides a carrier named `docker` and that `DockerCarrier` is the factory for it.

**Data flow**: It takes no input. It constructs a `Manifest` containing the carrier name, version, and carrier specification, then returns it.

**Call relations**: The extension loader calls this when discovering available sandbox backends. The returned manifest lets the core system choose this Docker carrier when configuration asks for the Docker sandbox backend.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox startup, command execution, file transfer, port dialing, and lease renewal during request handling`

UFO needs a safe place where each conversation can run code and keep files. This file provides that place using E2B, a cloud service that starts and pauses remote sandboxes. Think of it like renting a small workshop in the cloud for each conversation: tools run there, files stay there, and the workshop can go idle without being thrown away.

The file registers a carrier named "e2b" so the rest of the system can ask for a sandbox without knowing the provider details. It creates or reconnects to an E2B sandbox, prepares it by installing the proxy certificate and making `/workspace`, and then returns a `SandboxHandle` that other UFO code can use.

A major job here is keeping the sandbox alive just long enough. E2B pauses sandboxes after a timeout, but can resume them later. This file tracks leases, reconnects when needed, and avoids trusting stale connections after failures. It also routes every command’s internet traffic through UFO’s egress proxy, using a run token so requests are measured and secrets stay outside the sandbox.

The command path is careful about timeouts. E2B may stop streaming output while the process keeps running, so this file starts commands in their own process group and can kill the whole group when a deadline expires. It also remembers briefly when a sandbox stops answering, so the next command can fail quickly instead of waiting a full timeout.

#### Function details

##### `_egress_env`  (lines 150–185)

```
def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside the remote sandbox send outbound internet traffic through UFO’s proxy. This matters because the sandbox is outside the cluster, so it needs a public HTTPS proxy address and must not receive real model API keys directly.

**Data flow**: It takes a proxy description and the current run token. It checks that the proxy has a public HTTPS URL, folds the run token into the proxy URL as the username, and returns a dictionary of proxy, certificate, and sentinel API-key environment variables. If the proxy URL is missing or unsafe, it raises an error instead of creating an unmetered sandbox environment.

**Call relations**: When `E2BCarrier.create` prepares a sandbox handle, it calls this first so every later command launched through that handle inherits the correct proxy settings.

*Call graph*: called by 1 (create); 1 external calls (urlsplit).


##### `E2BCommandHandle.wait`  (lines 201–201)

```
async def wait(self) -> E2BCommandResult
```

**Purpose**: Describes the SDK operation that waits for a background command to finish. It exists so this file can type-check the E2B command object without depending on every concrete SDK detail.

**Data flow**: The command has already been started and has a process id. Waiting consumes no new command input; it waits for completion and returns stdout, stderr, and the exit code.

**Call relations**: The `exec` flow starts commands in the background so it can learn their process id, then uses this wait method to collect the final result.


##### `E2BCommands.run`  (lines 222–231)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None, background: Literal[False]=False) -> E2BCommandResult
```

**Purpose**: Describes how this file expects the E2B SDK to run commands in a sandbox. It covers both normal foreground commands and background commands whose process id is needed for later cleanup.

**Data flow**: It receives a shell command string plus optional working directory, environment, user, timeout, and background flag. In foreground mode it returns the completed command result; in background mode it returns a handle that can later be waited on.

**Call relations**: Most sandbox actions in this file go through this shape: preparation commands, health probes, command execution, and process-group stopping all rely on the SDK command runner.


##### `E2BFileStream.__aiter__`  (lines 238–238)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: Describes the streamed file reader as something that can be read chunk by chunk asynchronously. This lets large files leave the sandbox without loading the whole file into UFO memory at once.

**Data flow**: It takes an already-open file stream and produces an asynchronous sequence of byte chunks. The caller receives each chunk as it arrives.

**Call relations**: `E2BCarrier.read` uses this behavior when it yields file contents back to the rest of the system.


##### `E2BFileStream.aclose`  (lines 240–240)

```
async def aclose(self) -> None
```

**Purpose**: Describes how to close an open streamed file connection. This matters because a remote stream holds network resources until it is explicitly released.

**Data flow**: It receives no file data. It closes the open stream and releases the provider connection.

**Call relations**: `E2BCarrier.read` calls this in a cleanup path so the stream is closed even if the caller stops reading partway through a file.


##### `E2BFiles.write`  (lines 244–244)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: Describes the SDK file upload operation used to put bytes or text into the sandbox filesystem. This is the safe path for file contents because command execution only accepts shell strings, not raw stdin data.

**Data flow**: It receives a path, file data, and optionally a user. The SDK writes that data into the sandbox and returns a provider-specific acknowledgement.

**Call relations**: `E2BCarrier.write` uses this for user-visible file uploads, and `_install_ca` uses it to place the proxy certificate before installing it.


##### `E2BFiles.read`  (lines 246–246)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: Describes the SDK operation that opens a file in the sandbox for reading. In this file it is used in streaming mode so large outputs can be transferred piece by piece.

**Data flow**: It receives a path and a requested format. It returns a stream object that can yield bytes until the file is fully read.

**Call relations**: `E2BCarrier.read` calls this after renewing the sandbox lease, then forwards chunks to the caller.


##### `E2BSandbox.get_host`  (lines 255–255)

```
def get_host(self, port: int) -> str
```

**Purpose**: Describes how to turn an in-sandbox port number into an externally reachable host name. This is needed when a command starts a service inside the sandbox and UFO needs to connect to it from outside.

**Data flow**: It receives a port number and returns the public host name E2B assigns for that sandbox port.

**Call relations**: `E2BCarrier.dial` uses this address and adds E2B’s traffic token so outside clients can reach the sandbox service.


##### `E2BSdk.create`  (lines 259–268)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the SDK call that creates a brand-new E2B sandbox from a template. A template is the prebuilt sandbox image and size configuration to start from.

**Data flow**: It receives the template reference, lease timeout, metadata, lifecycle and network settings, and API key. It asks E2B to start a sandbox and returns the live sandbox object.

**Call relations**: `E2BCarrier._resume_or_open` uses this only when there is no usable existing sandbox id to reconnect to.


##### `E2BSdk.connect`  (lines 270–276)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the SDK call that reconnects to an existing E2B sandbox. In E2B, this is also the important operation that resumes a paused sandbox and sets a new lease.

**Data flow**: It receives a sandbox id, desired timeout span, and API key. It returns the live sandbox if E2B still has it, or raises a provider error if it is gone or unreachable.

**Call relations**: All reconnect and lease-renewal paths go through `E2BCarrier._connected`, which wraps this SDK call with retries and an overall timeout.


##### `E2BCarrier.create`  (lines 326–402)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Opens the sandbox for a conversation and returns the handle that the rest of UFO will use. It either resumes a durable sandbox, reuses this process’s live one, or creates a fresh E2B sandbox.

**Data flow**: It receives a sandbox specification containing the conversation id, optional resume id, size, proxy information, run token, environment, and turn id. It builds the proxy environment, finds or opens the sandbox, prepares it, records a lease in memory, and returns a `SandboxHandle` with the sandbox id and command environment. If preparation fails on a fresh or unproven sandbox, it raises instead of handing out a broken workspace.

**Call relations**: This is the main setup path for the E2B carrier. It calls `_egress_env`, checks `_leased`, delegates opening to `_resume_or_open`, prepares through `_prepare` or `_prepare_strictly`, and publishes the resulting lease only after the sandbox is safe to use.

*Call graph*: calls 5 internal fn (_leased, _prepare, _prepare_strictly, _resume_or_open, _egress_env); 5 external calls (__init__, __init__, timeout, emit_metric, log).


##### `E2BCarrier.attach`  (lines 404–428)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an already-known sandbox for read-style access, without creating a new one. If the saved sandbox id is missing or E2B no longer has it, it returns `None`.

**Data flow**: It receives a sandbox specification with a possible resume id. If there is no id, it returns nothing. Otherwise it connects to that exact sandbox, updates the in-memory lease, and returns a handle without egress environment because this path is not meant for outbound command traffic.

**Call relations**: This is the cautious counterpart to `create`: it calls `_connected` but never `_resume_or_open`, because callers asking to attach want absence reported rather than an empty replacement sandbox created.

*Call graph*: calls 1 internal fn (_connected); 2 external calls (__init__, __init__).


##### `E2BCarrier._resume_or_open`  (lines 430–470)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: Chooses between reconnecting to an existing sandbox and creating a new one. It protects the conversation from being stuck forever on a sandbox id that the provider no longer recognizes.

**Data flow**: It receives the sandbox specification and an optional sandbox id. If an id is present, it tries to connect and returns that sandbox when successful. If E2B says the sandbox is gone, it logs that fact, looks up the template for the requested size, creates a new sandbox, verifies it has the traffic token needed for inbound access, and returns it.

**Call relations**: `E2BCarrier.create` uses this as its opening decision point. It relies on `_connected` for safe reconnects and uses the SDK create call only when reconnecting is impossible.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 472–488)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: Runs sandbox preparation in a must-succeed mode. This is used for fresh or otherwise unproven sandboxes, where missing setup would mean no trusted proxy access or usable `/workspace`.

**Data flow**: It receives a sandbox and the original sandbox specification. It tries to prepare the sandbox, retrying only short-lived transport failures. If attempts run out or the failure is a real command failure, it forgets the lease for that conversation and raises the error.

**Call relations**: `E2BCarrier.create` calls this when it cannot safely assume the sandbox was prepared earlier. It delegates the actual setup to `_prepare`, uses `_drop` on failure, and records retry logs and metrics when the provider connection flakes.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 490–552)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: Reconnects to an E2B sandbox with bounded retries. This shields callers from indefinite waits when the provider’s control plane does not answer.

**Data flow**: It receives a conversation id, sandbox id, and desired lease span. It repeatedly calls the SDK connect operation on transport failures, backing off between tries, and enforces a total wall-clock limit. It returns the connected sandbox, passes through provider not-found responses, or raises the last transport-style failure when the reconnect cannot be completed.

**Call relations**: `_resume_or_open`, `_sandbox`, and `attach` all use this single reconnect wrapper, so turn setup, lease renewal, and read attachment share the same retry and timeout behavior.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 4 external calls (sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 554–561)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Makes a sandbox ready for UFO work. It installs the proxy certificate into the sandbox trust store and ensures `/workspace` exists with the right owner.

**Data flow**: It receives a sandbox and certificate text. It uploads and installs the certificate, then creates or fixes the workspace directory. It returns nothing when both steps succeed, and lets errors bubble up when setup cannot be completed.

**Call relations**: `E2BCarrier.create` may call this with a short timeout for already-prepared resumed sandboxes, while `_prepare_strictly` calls it for must-succeed setup. It hands the two concrete steps to `_install_ca` and `_ensure_workspace`.

*Call graph*: calls 2 internal fn (_ensure_workspace, _install_ca); called by 2 (_prepare_strictly, create).


##### `E2BCarrier._leased`  (lines 563–578)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: Looks up this process’s current cached lease for a conversation and sweeps expired entries. This keeps the in-memory cache from growing forever as the process touches more conversations.

**Data flow**: It receives a conversation id. It reads the current lease for that conversation, checks the clock, removes every lease whose stored expiry time has passed, and returns the original conversation’s lease if one was present.

**Call relations**: `E2BCarrier.create` uses this to decide whether an in-process sandbox can be reused, and `_sandbox` uses it to decide whether it must reconnect before work starts.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 580–588)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Installs UFO’s egress proxy certificate inside the sandbox. Without this, HTTPS traffic through the proxy would not be trusted by tools running in the sandbox.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path as root, runs the installation command, and returns on success. If the command exits with an error, it turns the command output into a clear runtime error.

**Call relations**: `_prepare` calls this before workspace setup, because every prepared sandbox must trust the proxy that command traffic will use.

*Call graph*: called by 1 (_prepare).


##### `E2BCarrier._ensure_workspace`  (lines 590–599)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: Makes sure the sandbox has a `/workspace` directory owned by the normal sandbox user. This is the conversation’s working disk inside the remote container.

**Data flow**: It receives a sandbox. It runs a root command that creates the directory and changes ownership. If the command fails, it raises an error with the command’s output.

**Call relations**: `_prepare` calls this after installing the certificate so the sandbox is both network-ready and file-ready before UFO uses it.

*Call graph*: called by 1 (_prepare).


##### `E2BCarrier.exec`  (lines 601–688)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command inside the E2B sandbox and turns the outcome into UFO’s standard execution result. It also enforces deadlines by killing the command’s process group when possible.

**Data flow**: It receives a sandbox handle, an argument tuple, and a timeout in seconds. It renews or reuses the sandbox lease, checks whether a previously silent sandbox is answering, quotes the arguments into a shell command, starts it in the workspace with proxy environment variables, tracks its process id, waits for completion, and returns stdout, stderr, and exit code. On command failure it returns the command’s exit code; on timeout it tries to stop the process group and returns timeout code 124; on provider or stream failures it drops the cached lease and raises.

**Call relations**: This is the central command path. It calls `_sandbox` for a valid lease, `_still_there` for marked silent containers, `_stop_group` and `_mark_silent` for timeout cleanup, `_forget_group` when a command is no longer running, and `_drop` when the cached lease should not be trusted.

*Call graph*: calls 6 internal fn (_drop, _forget_group, _mark_silent, _sandbox, _still_there, _stop_group); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.stop_commands`  (lines 690–712)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Stops commands that were left running for a specific turn after cancellation. This prevents a user-cancelled turn from continuing to consume sandbox CPU while avoiding killing unrelated turns in the same conversation sandbox.

**Data flow**: It receives a sandbox handle. It removes the set of tracked process-group ids for that exact container and turn. If there are none, it does nothing; otherwise it renews the sandbox briefly and sends a kill signal to each tracked group.

**Call relations**: This is the cleanup path that complements `exec`. `exec` deliberately leaves commands running on generic task cancellation because it cannot tell cancellation from replay; the higher-level stop path calls `stop_commands` when it knows the member was truly stopped.

*Call graph*: calls 2 internal fn (_sandbox, _stop_group).


##### `E2BCarrier._forget_group`  (lines 714–724)

```
def _forget_group(self, handle: SandboxHandle, pid: int) -> None
```

**Purpose**: Removes a finished command’s process group from the in-memory tracking table. This keeps later stop requests from signaling a command that already ended.

**Data flow**: It receives a sandbox handle and a process id. It finds the tracked set for that container and turn, removes the id, and deletes the whole entry if no process groups remain.

**Call relations**: `exec` calls this in its cleanup path after a command finishes or after its timeout cleanup has been attempted, unless cancellation intentionally left the command running.

*Call graph*: called by 1 (exec).


##### `E2BCarrier._stop_group`  (lines 726–748)

```
async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int) -> None
```

**Purpose**: Best-effort kills a command’s whole process group inside the sandbox. Killing the group, rather than only the shell process, is what reaches child processes like compilers, test workers, or servers the command started.

**Data flow**: It receives a sandbox, container id, and process-group id. It runs `kill -9` against the negative process id, which means the whole group. If the stop command times out, it marks the container as silent and emits a failure metric; other cleanup failures are counted but not raised.

**Call relations**: `exec` uses this when a command deadline expires, and `stop_commands` uses it when a cancelled turn needs its tracked commands stopped. It may call `_mark_silent` when even the stop request gets no answer.

*Call graph*: calls 1 internal fn (_mark_silent); called by 2 (exec, stop_commands); 1 external calls (emit_metric).


##### `E2BCarrier._mark_silent`  (lines 750–754)

```
def _mark_silent(self, container_id: str) -> None
```

**Purpose**: Temporarily remembers that a sandbox stopped answering commands. This helps avoid making the next command wait through a long timeout against a likely wedged command channel.

**Data flow**: It receives a container id. It stores that id with an expiry time based on the current clock and the configured silent-mark duration.

**Call relations**: `exec` calls this when a command launch times out before a process id is known, and `_stop_group` calls it when a stop request also times out. `_still_there` later reads and clears or expires this mark.

*Call graph*: called by 2 (_stop_group, exec).


##### `E2BCarrier._still_there`  (lines 756–786)

```
async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None
```

**Purpose**: Quickly probes a sandbox that was previously marked silent before giving it another full command deadline. This separates a recovered or merely overloaded sandbox from one that is still not answering.

**Data flow**: It receives a sandbox object and container id. If there is no silent mark, it returns immediately. If the mark has expired, it removes it and allows the real command to proceed. If the mark is still active, it runs a short `true` command; success clears the mark, while failure raises `SandboxUnreachable`.

**Call relations**: `exec` calls this before launching a real command. It uses the silent marks written by `_mark_silent` and reports unreachable sandboxes in the standard carrier error form.

*Call graph*: called by 1 (exec); 3 external calls (__init__, emit_metric, log).


##### `E2BCarrier.write`  (lines 788–799)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Uploads bytes into the sandbox filesystem. This is how UFO places files into the remote workspace without squeezing file contents through a shell command.

**Data flow**: It receives a sandbox handle, destination path, and bytes. It gets a sandbox with a lease long enough for the upload, writes the content through E2B’s file API, and returns nothing on success. If the provider call fails, it drops the cached lease and re-raises the error.

**Call relations**: This file-transfer path calls `_sandbox` for lease renewal and `_drop` when the lease should no longer be trusted after an upload failure.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 801–821)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. This lets UFO return large generated files without holding the entire file in memory.

**Data flow**: It receives a sandbox handle and path. It leases the sandbox for the full autosuspend span, opens a streamed read, yields byte chunks to the caller, and always closes the stream afterward. If E2B says the file is missing, it raises the normal Python `FileNotFoundError`; on other provider failures it drops the lease and raises.

**Call relations**: Callers consume this as an asynchronous byte stream. Internally it uses `_sandbox` for a safe lease and relies on the stream’s `aclose` behavior for cleanup.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 823–828)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level sandbox filesystem operation using UFO’s shared `ufo fs` helper. These are operations such as listing, statting, or manipulating files through a common carrier-independent interface.

**Data flow**: It receives a sandbox handle, operation name, and operation parameters. It passes them to the shared helper, which runs the corresponding tool inside the sandbox and returns a dictionary result.

**Call relations**: This method is the E2B carrier’s bridge to the generic sandbox file-operation layer. It hands off to `ufo_fs_file_op`, which in turn uses the carrier command path.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `E2BCarrier.dial`  (lines 830–851)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Builds an external connection target for a service listening on a port inside the sandbox. This is used for things like browser debugging ports or preview servers started by a turn.

**Data flow**: It receives a sandbox handle and port number. It renews the sandbox lease for longer than usual, asks E2B for the public host name for that port, includes the traffic access token as a header when present, and returns a `DialTarget` using TLS. If the sandbox is gone, it raises `SandboxUnreachable`.

**Call relations**: This method calls `_sandbox` with a longer lease because the actual network exchange happens outside carrier calls and cannot renew itself. It then uses the sandbox’s `get_host` address formatting to produce the target.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 853–895)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: Returns a sandbox object that is leased long enough for the work about to happen. It avoids a provider reconnect before every operation while still renewing before the sandbox might pause mid-transfer or mid-command.

**Data flow**: It receives a sandbox handle, the number of seconds the caller needs, and an optional minimum lease span. It checks the cached lease for the conversation, confirms it names the same container and lasts long enough, and returns it if safe. Otherwise it removes the stale entry, reconnects through `_connected`, records a new lease, logs the renewal, and returns the sandbox.

**Call relations**: `exec`, `write`, `read`, `stop_commands`, and `dial` all call this before touching E2B. It uses `_leased` to inspect the cache and `_connected` to renew or resume the sandbox.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 5 (dial, exec, read, stop_commands, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 897–902)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: Forgets a cached sandbox lease after a provider call fails. This forces the next operation to reconnect instead of trusting a local deadline that may no longer match reality.

**Data flow**: It receives a conversation id and a short label describing what was happening. It removes that conversation from the in-memory lease map and writes a log entry.

**Call relations**: `_prepare_strictly`, `exec`, `write`, and `read` call this when a failed operation makes the cached connection or lease unsafe to reuse.

*Call graph*: called by 4 (_prepare_strictly, exec, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 905–922)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: Parses the `E2B_TEMPLATES` environment variable into a map from sandbox size to E2B template reference. This ensures every size UFO offers has a matching E2B template.

**Data flow**: It receives a comma-separated string like `small=...,medium=...,large=...`. It splits each entry, validates that each part has both a size and template reference, then checks that the set of sizes exactly matches UFO’s known sandbox sizes. It returns the parsed dictionary or raises a clear configuration error.

**Call relations**: `build_e2b_carrier` calls this during carrier construction so bad template configuration fails at startup rather than later when a user requests a sandbox.

*Call graph*: called by 1 (build_e2b_carrier).


##### `build_e2b_carrier`  (lines 925–932)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: Creates the configured E2B carrier from environment variables. It is the factory function used when the manifest registers the `e2b` backend.

**Data flow**: It reads `E2B_API_KEY` and `E2B_TEMPLATES` from the process environment. If either is missing, it raises a configuration error. Otherwise it parses the templates and returns an `E2BCarrier` with the API key and size-to-template map.

**Call relations**: `manifest` exposes this function as the carrier factory, so UFO calls it when a deployment selects the E2B sandbox backend.

*Call graph*: calls 1 internal fn (sandbox_templates); 1 external calls (__init__).


##### `manifest`  (lines 935–947)

```
def manifest() -> Manifest
```

**Purpose**: Advertises this extension to UFO’s plugin system. It declares that this file provides a carrier named `e2b`, how to build it, and which sandbox sizes it supports.

**Data flow**: It takes no input. It constructs a manifest containing one carrier specification with the E2B name, factory, off-cluster flag, and supported sizes, then returns it.

**Call relations**: The wider system discovers this function when loading extensions. Once loaded, the manifest lets configuration choose `e2b` without core sandbox code knowing E2B-specific details.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/sandbox/cache.py`

`config` · `startup and sandbox setup`

A sandbox often needs to fetch public code or packages, such as GitHub repositories or Python and npm packages. Letting every sandbox talk freely to the internet would be harder to secure and less efficient. This file describes the approved cache path: a fixed internal host for Git traffic, a list of public package hosts that may be cached, and a local callback address used by the cache control path.

The key idea is simple: Git fetches can be quietly rewritten so that asking for `https://github.com/...` actually goes through `cache.ufo.internal`. This is like sending library requests through a front desk instead of letting every visitor wander into the stacks. Pushes are kept direct, so the cache is only used for reading public source, not for publishing changes.

The file also records which package registry hosts are expected to be cached by the proxy, such as npm, PyPI, Cargo, and Go module services. Those do not need per-sandbox settings because the proxy can intercept requests to the real public host.

Finally, it includes a parser for the cache daemon address. If the deployment says there is a cache daemon, the address must be written as `host:port`; otherwise the code fails clearly, because a malformed cache address is a deployment mistake rather than something to silently ignore.

#### Function details

##### `cache_git_config`  (lines 33–41)

```
def cache_git_config() -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the Git settings that make reads from cached Git hosts go through the internal cache service. It also adds a matching push rule so that writes still go to the original host instead of the cache.

**Data flow**: It reads the fixed list of Git hosts that are allowed to use the cache. For each host, it creates two Git configuration entries: one that rewrites fetch URLs to the cache host, and one that keeps pushes pointed at the real public host. It returns all of those entries as an immutable tuple.

**Call relations**: This function is called when a sandbox needs Git configured for cached fetching. It hands back plain Git configuration key-value pairs, which the caller can export or install into the sandbox environment before Git commands run.


##### `parse_cache_daemon`  (lines 44–53)

```
def parse_cache_daemon(value: str | None) -> tuple[str, int] | None
```

**Purpose**: Turns a deployment-provided cache daemon address into a usable host and port. It also deliberately rejects malformed addresses so cache setup errors are noticed immediately.

**Data flow**: It receives either no value or a string that should look like `host:port`. If there is no value, it returns `None`, meaning this deployment has no cache daemon configured. If there is a value, it splits it at the final colon, checks that a host and separator exist, converts the port text to a number, and returns the host and port together.

**Call relations**: This function is used when reading deployment or runtime configuration for the local cache daemon. It sits at the boundary between raw text configuration and the rest of the system, giving later code either a clean address to use or a clear error to stop a bad deployment.


### Sandbox safety and routing contracts
Path containment, preview and ingress routing, shared sandbox session operations, and file-change limits define the safety boundary around sandbox use.

### `core/src/ufo/sandbox/containment.py`

`domain_logic` · `cross-cutting file access validation`

A sandbox is only useful if code inside it cannot trick the host into touching files outside it. This file solves that problem for paths. It does more than check that a filename “looks safe,” because a harmless-looking path can still be redirected by a symlink, which is like a shortcut that points somewhere else.

The guard works in layers. First it rejects obviously bad relative names, such as empty names or paths that climb upward with “..”. Then it resolves the real parent directory and checks that it is still under the chosen root. Next it walks down each directory component using operating-system file descriptors, which are stable references to directories. That means if another process renames or swaps a directory after the check, the later operation still uses the same pinned directory. Finally, it checks the target file itself without following a final symlink, so a planted shortcut cannot be read as if it were a normal file.

The main result is `ContainedFile`, a small object that represents a file target whose parent directory has already been proven safe and pinned. Reads, writes, deletes, permission changes, and replacements then happen relative to that pinned parent. Without this file, a malicious or buggy sandbox user could potentially escape its workspace and read or overwrite host files.

#### Function details

##### `contained_root`  (lines 87–100)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Checks that a sandbox root exists, is a real directory, and is not itself a symlink. This is used when the root may be reachable by untrusted code, because a symlinked root could silently redirect every supposedly contained path.

**Data flow**: It takes a root path string or path-like object, turns it into a `Path`, inspects the path itself without following symlinks, and rejects it if it is missing or not a directory. If it is safe, it returns the canonical resolved directory path.

**Call relations**: This is the first safety step used by `contained_file` and `contained_dir`. They ask it to prove the root before they validate a file or directory underneath that root.

*Call graph*: called by 2 (contained_dir, contained_file); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 103–120)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Checks a root directory that came from operator configuration, where following a symlink is allowed. This supports normal deployment layouts, such as a configured storage path that points to a mounted disk.

**Data flow**: It takes the configured root path and the name of the setting it came from, follows the path normally, verifies that the result exists and is a directory, and returns the resolved real path. If it fails, the error message names the setting so the operator knows what to fix.

**Call relations**: Unlike `contained_root`, this function is not called by the path-entry functions in this file. It exists for configuration-loading code that needs to normalize trusted deployment paths before later sandbox operations use them.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 138–149)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Looks at the target file itself without following a final symlink. It answers whether the target exists and is a normal file, while refusing directories and other special filesystem objects.

**Data flow**: It reads the file entry named by `self.name` relative to the already pinned parent directory. If there is no entry, it returns `None`; if the entry is a regular file, it returns its stat information; otherwise it raises `NotRegularFile`.

**Call relations**: `contained_regular` uses this after `contained_file` has pinned the parent directory. The method is the final target check before a caller treats the path as an existing regular file.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 151–166)

```
def mode(self, default: int) -> int
```

**Purpose**: Finds the permission bits to use when overwriting this target. It preserves the mode of an existing regular file, uses a default when there is no file, and refuses to treat a directory as a file.

**Data flow**: It checks the target name relative to the pinned parent without following symlinks. A missing file or non-regular non-directory object produces the supplied default mode; a regular file contributes its current permission bits; a directory raises `NotRegularFile`.

**Call relations**: Callers use this before replacing file contents so the new file can keep sensible permissions. It fits with `replace_bytes`, which writes a safe staged file and then renames it into place.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 168–178)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained target for streaming binary reads. This is for large files or copy operations where loading the whole file into memory would be wasteful.

**Data flow**: It asks `_open_regular` to open the file safely and prove it is a regular file. It wraps the resulting low-level file descriptor in a Python binary file object and returns that object; if wrapping fails, it closes the descriptor before re-raising the error.

**Call relations**: `read_bytes` calls this for simple bounded reads. The method hands the real opening work to `_open_regular`, which performs the no-symlink and regular-file checks.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 180–183)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a chosen number of bytes from a contained regular file. It is a convenience method for callers that want a bounded in-memory byte result.

**Data flow**: It takes a byte limit, opens the file through `open_bytes`, reads at most that many bytes, closes the file automatically, and returns the bytes read.

**Call relations**: `read_text` builds on this when it wants decoded text. This method relies on `open_bytes` so it inherits the same safe opening behavior.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 185–186)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads a contained file as UTF-8 text, replacing invalid characters rather than failing. It is useful when callers need human-readable file contents from a sandbox file.

**Data flow**: It takes a byte limit, gets bytes from `read_bytes`, decodes them as UTF-8, replaces malformed byte sequences, and returns a string.

**Call relations**: This is a small text-focused layer over `read_bytes`. It does not open the file itself; it depends on the lower-level safe read path.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 188–189)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the permission bits of the contained target. It applies only normal file permission bits and does so relative to the pinned parent directory.

**Data flow**: It takes a mode number, masks it down to standard permission bits, and calls the operating system to change the named file without following symlinks.

**Call relations**: This is used after a `ContainedFile` has already been produced by `contained_file`. The safety work is done before this method runs; this method performs the actual permission change.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 191–195)

```
def unlink(self) -> None
```

**Purpose**: Deletes the contained target name if it exists. Missing files are treated as already deleted, so callers can clean up without needing a separate existence check.

**Data flow**: It asks the operating system to remove `self.name` relative to the pinned parent directory. If the file is not found, it ignores that case and returns normally.

**Call relations**: This is a cleanup operation on a `ContainedFile`. It relies on the parent directory having been pinned by `contained_file` before deletion is attempted.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 197–199)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Atomically replaces this contained target with another contained file. “Atomically” means readers see either the old file or the new file, not a half-written state.

**Data flow**: It takes another `ContainedFile` as the source and asks the operating system to rename the source name onto this target name, using each file’s pinned parent directory.

**Call relations**: Both files must already have passed containment checks. This method is the handoff point where one safe file target is installed over another.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 201–202)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Writes text into the contained target by replacing the whole file safely. It is the text version of the binary replacement method.

**Data flow**: It takes a string and a permission mode, encodes the string to bytes, and passes those bytes to `replace_bytes`. The final output is a file containing the new text.

**Call relations**: This is a convenience wrapper around `replace_bytes`. All the safe staging and rename behavior happens in `replace_bytes`.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 204–228)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely replaces the contained target with new binary data. It writes to a temporary sibling file first, then renames it into place so a symlink cannot redirect the write and readers never see a partial file.

**Data flow**: It takes bytes and a permission mode, creates a uniquely named staged file in the pinned parent directory without following symlinks, writes the data, sets permissions, and renames the staged file onto the target name. It also closes or removes the staged file during cleanup if something goes wrong.

**Call relations**: `replace_text` calls this after encoding text. This is the main safe-write primitive used once `contained_file` has produced a pinned `ContainedFile`.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 230–246)

```
def _open_regular(self) -> int
```

**Purpose**: Opens the target file for reading only if it is a regular file and not a symlink. This is the low-level safety check behind streaming reads.

**Data flow**: It tries to open the target name relative to the pinned parent with flags that refuse symlinks. If the file is missing, it raises `PathNotFound`; if the opened object is not a regular file, it closes it and raises `NotRegularFile`; otherwise it returns the open file descriptor.

**Call relations**: `open_bytes` calls this before wrapping the descriptor in a Python file object. It is private because callers should use the safer higher-level read methods.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 250–284)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main entry point for safely reading or writing one file under a sandbox root. It performs the full containment process and yields a `ContainedFile` whose parent directory is pinned.

**Data flow**: It takes a requested path, a root, and an option to create missing parent directories. It validates the root, roots relative paths under it, rejects unusable target names, resolves and checks the parent, opens the root directory, walks each parent component without following symlinks, optionally creates missing directories, and yields a `ContainedFile`. When the caller is done, it closes the pinned directory descriptor.

**Call relations**: `contained_regular` uses this to prove an existing regular file. Internally it calls `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend` in order to build the safe `ContainedFile` object.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_regular); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 287–313)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Safely proves that a directory path is inside a sandbox root and returns its canonical path. It is used when a caller wants to list or walk a directory rather than open one specific file.

**Data flow**: It takes a directory path, a root, and an option to create missing directories. It validates the root, roots and resolves the target, checks that the resolved directory stays under the root, then walks each directory component without following symlinks, optionally creating components as it goes. It closes the final descriptor and returns the resolved path.

**Call relations**: `contained_glob` calls this to choose the safe starting directory for pattern-based listing. Like `contained_file`, it relies on `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_glob); 3 external calls (__init__, close, mkdir).


##### `contained_regular`  (lines 316–325)

```
def contained_regular(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> Path
```

**Purpose**: Returns the canonical path of an existing regular file that has passed the containment checks. This is for callers that must hand a filename to another tool or library instead of reading from an open file descriptor.

**Data flow**: It takes a path and root, enters `contained_file` to pin and validate the parent, then calls `lstat` on the target. If no file exists, it raises `PathNotFound`; otherwise it returns the validated target path.

**Call relations**: This is a stricter path-returning wrapper around `contained_file`. It uses `ContainedFile.lstat` to add the requirement that the target already exists and is a regular file.

*Call graph*: calls 1 internal fn (contained_file); 1 external calls (__init__).


##### `contained_pattern`  (lines 328–345)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Checks and rewrites a glob pattern so it cannot make a listing escape the sandbox root. A glob pattern is a filename pattern, such as `*.txt`, used to find matching files.

**Data flow**: It takes a pattern and root path, rejects patterns containing `..`, leaves safe relative patterns alone, and turns safe absolute patterns into root-relative patterns. If an absolute pattern points outside the root or names the root itself, it raises an error.

**Call relations**: `contained_glob` calls this after deciding where a listing should start. This function focuses only on the pattern text, while `contained_dir` validates the directory to walk.

*Call graph*: called by 1 (contained_glob); 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_glob`  (lines 348–360)

```
def contained_glob(pattern: str, path: str | os.PathLike[str] | None, root: Path) -> tuple[Path, str]
```

**Purpose**: Combines safe directory selection with safe glob-pattern rewriting. It gives callers both the directory to walk and the pattern to apply there, confined to the sandbox root.

**Data flow**: It takes a pattern, an optional starting path, and the root. If the pattern is absolute, it starts from the root; otherwise it starts from the supplied path or the root. It then validates the start directory with `contained_dir` and validates or rewrites the pattern with `contained_pattern`, returning both.

**Call relations**: This function is the orchestration point for safe enumeration. It delegates directory proof to `contained_dir` and pattern proof to `contained_pattern` so callers do not each invent their own glob safety rules.

*Call graph*: calls 2 internal fn (contained_dir, contained_pattern); 1 external calls (PurePosixPath).


##### `contained_relative`  (lines 363–388)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs a purely text-based containment check for a path under a root. This is used when the current process cannot inspect the real filesystem yet, such as when a path will be written later or inside a container.

**Data flow**: It takes a path string and root string, combines relative paths with the root, processes `.` and `..` components in text form, rejects paths that climb out or name the root itself, and returns the cleaned absolute-looking path string.

**Call relations**: This function stands alone as the first lexical safety tier. Later code that actually writes to a reachable filesystem should still use the stronger file or directory containment functions.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 391–399)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Extracts one safe filename component from a name supplied by another system. It drops any directory pieces, including Windows-style backslash separators, and falls back to a trusted name if nothing usable remains.

**Data flow**: It takes a raw name and fallback name, normalizes backslashes to slashes, keeps only the final path component, and returns that component unless it is empty, `.` or `..`; in those cases it returns the fallback.

**Call relations**: This is a helper for incoming filenames, such as attachments or provider-supplied names. It only creates a safe leaf name; the caller must still place it under a root and write through the containment guard.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 402–413)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Quickly checks whether an already enumerated path appears to be a regular file inside the root with no symlink involved. It is meant as a listing filter, not as permission to read the file directly.

**Data flow**: It takes a candidate path and root, checks that the candidate itself is a regular file without following symlinks, resolves it strictly, and returns `true` only if the resolved path is the same path and still inside the root. Any filesystem error produces `false`.

**Call relations**: This function uses `_inside` for the final root check. A later read of a listed file should still go through `contained_file`, because this function is optimized for filtering enumeration results.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 416–422)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Interprets a possibly relative path against a given root instead of against the process’s current working directory. This prevents callers from accidentally checking one path and later using another.

**Data flow**: It takes a path and a root. If the path is absolute, it returns it as a `Path`; if it is relative, it joins it under the root and returns that combined path.

**Call relations**: `contained_file` and `contained_dir` call this near the start of their validation. It standardizes how requested paths are anchored before deeper checks begin.

*Call graph*: called by 2 (contained_dir, contained_file); 1 external calls (Path).


##### `_inside`  (lines 425–426)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Answers the simple question: is one path equal to the root or below it? It is a small shared helper for final containment checks.

**Data flow**: It takes a path and a root, compares them directly, then checks whether the root appears among the path’s parents. It returns a boolean.

**Call relations**: `contained_file`, `contained_dir`, and `is_contained_regular` use this after resolving paths. It is private because it is only one piece of the full safety story.

*Call graph*: called by 3 (contained_dir, contained_file, is_contained_regular).


##### `_open_root`  (lines 429–433)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the sandbox root as a directory without following symlinks. This creates the first pinned directory reference used for safe component-by-component descent.

**Data flow**: It takes a root path and asks the operating system to open it with directory-only and no-symlink flags. If opening fails, it raises `NonDirectoryAncestor`; otherwise it returns the directory file descriptor.

**Call relations**: `contained_file` and `contained_dir` call this before walking down into child directories. The returned descriptor is then passed into `_descend`.

*Call graph*: called by 2 (contained_dir, contained_file); 2 external calls (__init__, open).


##### `_descend`  (lines 436–449)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory level deeper from an already pinned directory, refusing symlinks and non-directories. It also closes the previous directory descriptor so descriptors do not leak.

**Data flow**: It takes a current directory descriptor, the next path component, and the full target path for error messages. It opens the child component as a directory without following symlinks, raises a containment-specific error if the component is missing or unsafe, closes the old descriptor, and returns the child descriptor.

**Call relations**: `contained_file` and `contained_dir` call this repeatedly while walking from the root to the target’s parent or directory. It is the step that turns a text path into a chain of pinned real directories.

*Call graph*: called by 2 (contained_dir, contained_file); 5 external calls (__init__, __init__, __init__, close, open).


### `core/src/ufo/sandbox/ingress_host.py`

`domain_logic` · `link generation and request handling`

A browser keeps cookies, storage, and security rules separate by origin, which usually means by host name. This file gives each sandboxed site its own host-name label, like a small signed address: it contains the conversation ID and the sandbox port, plus a short proof that this server deployment created it. Without this, different sandboxed sites could collide under the same browser origin, or the server might waste work looking up conversations for random guessed host names.

The label is not meant to be the main permission check. It is more like a signed room number on an envelope: it says which conversation and port the request claims to be for, and proves the label was made with this deployment's secret. Real access is still checked later with a token or session cookie. But the label stops malformed or invented host names early.

The file also enforces one exact spelling for each label. Base32 text encoding can leave unused bits at the end, which means several different-looking labels could decode to the same bytes. Browsers would treat those spellings as different sites with different cookies. To avoid that split-brain behavior, parsing decodes the label, re-encodes it in the canonical lowercase form, and rejects anything that does not match exactly.

#### Function details

##### `site_label`  (lines 52–57)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: Creates the DNS label for a specific conversation ID and sandbox port. It is used when the system needs to publish or redirect to the browser-facing address for one sandboxed site.

**Data flow**: It receives a conversation UUID and a port number. It first rejects ports outside the valid network port range, then turns the UUID and port into bytes, adds a short cryptographic signature, and encodes the result as lowercase base32 text. The result is the label that can be placed in a host name.

**Call relations**: When the system needs a stable host label, this function builds the address bytes, asks `_signature` to make the tamper-evident proof, and asks `_encode` to turn the bytes into DNS-safe text. The matching reader on incoming requests is `parse_site_label`, which reverses and verifies this work.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 60–72)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: Reads a DNS label back into the conversation ID and port it claims to name, but only if the label is well formed, signed by this deployment, and written in the one accepted spelling. This protects the rest of the system from acting on random or altered host names.

**Data flow**: It receives a label string from a host name. It decodes the base32 text into bytes, re-encodes those bytes to make sure the spelling is canonical, splits the bytes into address and signature parts, and compares the supplied signature with the one this server would create. If everything matches, it returns the UUID and port; otherwise it raises `SiteLabelError` and nothing is trusted.

**Call relations**: This is the entry point for checking labels during request handling. It uses `_encode` to catch alternate spellings and `_signature` plus a constant-time comparison to catch tampering. If validation succeeds, it hands the conversation and port onward to later access checks; if not, it stops the flow before any sandbox is contacted or conversation data is read.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 75–76)

```
def _encode(raw: bytes) -> str
```

**Purpose**: Turns raw bytes into the lowercase base32 text form used for site labels. Base32 is an encoding that uses letters and digits that are safe for DNS-style names.

**Data flow**: It receives bytes, base32-encodes them, removes padding characters that are not needed in the DNS label, and lowercases the result. The output is the canonical text spelling used both when creating and checking labels.

**Call relations**: `site_label` uses this helper to produce the label shown to browsers. `parse_site_label` uses the same helper after decoding to make sure the incoming text is exactly the canonical spelling, not one of several lookalike encodings.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 79–81)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: Creates the short proof attached to a site label. The proof is an HMAC, which is a keyed hash: a small fingerprint that can only be reproduced by someone who knows the deployment secret.

**Data flow**: It receives the address bytes made from the conversation ID and port. It reads the deployment's ingress secret, combines that secret with a fixed label kind and the address bytes, hashes them with SHA-256, and returns only the first four bytes as the compact signature.

**Call relations**: `site_label` calls this when minting a new label, and `parse_site_label` calls it again when checking an incoming label. Because both sides compute the signature from the same address bytes and secret, a mismatch means the label was malformed, altered, or made for a different deployment.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/sandbox/preview.py`

`config` · `config load`

The preview service is an internal helper, not a public website. Its job is to render a document the sandbox already has into an image. This file captures the two pieces of information other parts of the system need to agree on: the special internal hostname, `preview.ufo.internal`, and the optional deployment address where that service is actually running.

Think of `PREVIEW_HOST` like the name on an internal office door. Other code can ask for that name, and the proxy knows it should route the request to the preview service rather than out to the internet. That matters because agents may be blocked from public internet access, but they still need to preview files they already hold. Allowing this internal host does not open a new public route.

The function `parse_preview_service` reads a deployment value such as `some-host:443`. If no preview service is configured, it returns `None`. If a value is present, it must be in `host:port` form. The parser deliberately fails with an error for malformed values, because this is deployment configuration: a bad address means the system was set up incorrectly, not that preview should quietly turn off.

#### Function details

##### `parse_preview_service`  (lines 15–24)

```
def parse_preview_service(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function turns an optional preview service setting into a usable host and port. It is used so deployment configuration like `preview.example:8443` becomes a clear pair of values the proxy or deployment code can use.

**Data flow**: It receives either no value, meaning no preview service is deployed, or a text value expected to look like `host:port`. If the input is `None`, it returns `None`. Otherwise it splits the text at the last colon, checks that a host was present, converts the port text into a number, and returns the result as `(host, port)`. If the text is missing the colon or host, or if the port is not a valid number, it raises an error instead of guessing.

**Call relations**: This function is a small configuration helper. Code that reads deployment settings can call it when it needs to understand the preview service address. It does not call other project functions; it only validates and reshapes the supplied value before handing the cleaned address back to its caller.


### `core/src/ufo/sandbox/session.py`

`domain_logic` · `tool execution and sandbox access`

A sandbox is the protected work area for one conversation. Tools may run shell commands and read or write files there, but they must not reach private transcript data or escape the workspace. This file is the shared contract that makes that possible across different sandbox backends, such as Docker, local execution, or a remote provider.

The file has three main jobs. First, it defines small value objects, such as SandboxSpec, SandboxHandle, ExecResult, and DialTarget, that describe what to open, what was opened, what a command returned, and how to contact a service running inside the sandbox. Second, it defines the Carrier interface: the set of actions every sandbox backend must provide, like create, exec, read, write, and dial. Think of Carrier as a wall socket shape: many power sources can sit behind it, but tools plug in the same way. Third, SandboxSession wraps a carrier and a handle into the per-turn object that tools actually use.

Security is a central theme. Paths are forced under /workspace. Proxy credentials are signed so the network proxy can tell which turn or probe is making a request. Python commands run in isolated mode so code planted in the workspace cannot hijack imports before the containment guard loads.

#### Function details

##### `_basic_username`  (lines 74–80)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username part from a Basic proxy authorization header. This matters because sandbox proxy tokens are passed as the username inside the proxy URL rather than through a separate secret channel.

**Data flow**: It receives a header string like a Proxy-Authorization value. It checks that the scheme is Basic, decodes the base64 text, splits the decoded username:password pair, and returns only the username. If the header is not valid Basic auth, it raises an error instead of guessing.

**Call relations**: RunTokenCodec.from_proxy_auth and ProbeTokenCodec.from_proxy_auth call this first. After it pulls out the signed username token, those codec methods verify and interpret that token for their own token type.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 99–103)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Builds a run-token signer from the deployment secret stored in the environment. The server needs this at startup so it can create and later verify tokens that identify sandbox network traffic for a turn.

**Data flow**: It reads the UFO token secret environment variable. If the value is missing, it stops with a clear runtime error. If present, it encodes the value as bytes and returns a RunTokenCodec ready to sign or verify run tokens.

**Call relations**: The serve startup path calls this when the application begins running. The resulting codec is later used by sandbox-opening code to mint run tokens for individual turns.

*Call graph*: called by 1 (run).


##### `RunTokenCodec.encode`  (lines 105–108)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a RunToken into a signed string that can be used as the proxy username for one sandbox process tree. This lets the proxy trust that the request really came from a token minted by this deployment.

**Data flow**: It receives a RunToken containing a workspace id, turn id, and optionally a member id. It formats those fields into a domain-specific payload, signs the bytes with the codec secret, and returns the signed token string.

**Call relations**: The sandbox-opening flow calls this when it prepares a sandbox for a turn. The produced token is later embedded in proxy environment variables so outbound network traffic can be attributed to the right turn.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 110–122)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads and verifies a run token from a proxy authorization header. The proxy uses this to decide which workspace, turn, and optional member a sandbox network request belongs to.

**Data flow**: It receives a proxy authorization header. It extracts the Basic username, verifies the signature with this deployment's secret, splits the payload into expected fields, checks that it is a run token, converts ids back into UUID values, and returns a RunToken. Bad encoding, bad signatures, wrong token type, or malformed ids become a clear invalid-token error.

**Call relations**: It builds on _basic_username for header parsing and on token verification for trust. It is the counterpart to RunTokenCodec.encode: one side mints the token, this side recovers it only if it is authentic.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `ProbeTokenCodec.encode`  (lines 157–163)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Turns a ProbeToken into a signed proxy username for an off-turn sandbox probe. A probe is work that runs outside a normal turn, so the token includes its own expiry time.

**Data flow**: It receives a ProbeToken with workspace, conversation, probe, member, and expiry information. It writes those values into a probe-specific payload, signs the payload with the shared secret, and returns the signed token string.

**Call relations**: This method is the minting side for probe credentials. ProbeTokenCodec.from_proxy_auth is the matching reading side, and both use a probe-specific token kind so probe tokens and run tokens cannot be confused.

*Call graph*: 1 external calls (sign_token).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 165–181)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Reads and verifies a probe token from a proxy authorization header. This lets the proxy identify off-turn probe traffic and check the probe's built-in deadline.

**Data flow**: It receives a proxy authorization header. It extracts the Basic username, verifies the signed token, splits the payload, checks that it is a probe token, converts ids and the expiry value into their proper types, and returns a ProbeToken. If anything is wrong, it raises a single invalid-token error.

**Call relations**: It calls _basic_username to get the signed token from the header. It mirrors ProbeTokenCodec.encode and protects the proxy from accepting forged tokens or tokens meant for a different purpose.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `sandbox_handle_id`  (lines 268–273)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the backend-specific sandbox id out of a stored handle only if that handle belongs to the current backend. This prevents one sandbox provider from trying to resume another provider's container id.

**Data flow**: It receives a backend name and a stored handle string. It checks for the expected prefix, such as backend plus a separator, and returns the remaining id when it matches. If the prefix does not match, it returns None.

**Call relations**: Carrier-specific resume logic can use this helper before attaching to an existing sandbox. It keeps backend switching safe by treating mismatched handles as not resumable.


##### `Carrier.create`  (lines 311–311)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Describes the operation every sandbox backend must provide to create or attach to a usable sandbox for a conversation. It is part of the common contract tools rely on, regardless of whether the backend is Docker, local, or remote.

**Data flow**: An implementation receives a SandboxSpec describing the desired image, workspace, proxy, token, and optional resume information. It opens or finds the sandbox and returns a SandboxHandle that future calls can use.

**Call relations**: This is a protocol method, so this file declares the shape and concrete carriers supply the behavior. Higher-level sandbox-opening code calls it through the Carrier interface so tools do not need to know which backend is underneath.


##### `Carrier.attach`  (lines 313–319)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Describes how a backend should reconnect to an already-existing sandbox without creating a new one. This is important for read-only or inspection paths where simply looking at files must not accidentally provision a fresh container.

**Data flow**: An implementation receives a SandboxSpec whose resume information names an existing sandbox. It returns a SandboxHandle if that sandbox is reachable, or None if it is gone or not owned by this backend.

**Call relations**: This protocol method is implemented by carriers. It sits beside create as the non-creating path, used when the caller needs to observe an existing sandbox rather than start a new one.


##### `Carrier.exec`  (lines 321–323)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Describes how a backend runs one command inside a sandbox. Commands are the main way tools use the sandbox for shell work, Python programs, and helper utilities.

**Data flow**: An implementation receives a SandboxHandle, an argument list, and a timeout. It runs that exact command in the sandbox and returns an ExecResult containing stdout, stderr, exit code, and whether the backend timeout fired.

**Call relations**: ufo_fs_file_op calls this protocol method to run the `ufo fs` command inside the sandbox. SandboxSession methods such as bash, sh, python, and file checks also rely on carrier implementations of this contract.

*Call graph*: called by 1 (ufo_fs_file_op).


##### `Carrier.write`  (lines 325–337)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Describes how a backend writes bytes into a file under the sandbox workspace. This gives every backend a safe copy-in path instead of smuggling large or unsafe content through shell command arguments.

**Data flow**: An implementation receives a handle, an already-scoped workspace path, and bytes to write. It creates needed parent directories, safely replaces the target file, and returns nothing when the write succeeds; filesystem refusals surface as errors.

**Call relations**: SandboxSession.write_file is the user-facing wrapper that scopes the path first, then calls this carrier method. Each backend implements the actual transfer in the way its environment supports.


##### `Carrier.read`  (lines 339–350)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Describes how a backend streams a file out of the sandbox workspace in chunks. Streaming matters because produced files may be large, and the host process should not have to load the whole file into memory.

**Data flow**: An implementation receives a handle and a scoped workspace path. It yields chunks of bytes from the file, or raises an appropriate error if the file is missing or unsafe to read.

**Call relations**: SandboxSession.read_file calls this after applying workspace path checks. Concrete carriers decide whether the bytes come from a container command, a filesystem API, or a local directory.


##### `Carrier.dial`  (lines 352–361)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Describes how a backend exposes a port from inside the sandbox to a caller outside it. This is used when a command starts a service, such as a browser debugging endpoint or a development server.

**Data flow**: An implementation receives a sandbox handle and an in-sandbox port number. It returns a DialTarget with the host to contact, whether TLS is used, and any required headers; if the sandbox cannot be reached, it raises SandboxUnreachable.

**Call relations**: SandboxSession.dial calls this as the public session wrapper. The sandbox Chrome extension uses that session method when it needs to reach Chrome's in-sandbox debugging port.


##### `Carrier.file_op`  (lines 363–373)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Describes how a backend runs one structured file operation inside the workspace, such as reading a window, editing, globbing, or grepping. The goal is to keep file processing near the files and return only bounded JSON results.

**Data flow**: An implementation receives a handle, an operation name, and a dictionary of parameters. It performs the operation inside the sandbox and returns a parsed JSON-like dictionary; recoverable tool mistakes are reported as ValueError and unexpected failures as RuntimeError.

**Call relations**: SandboxSession.run_ufo_fs prepares safe parameters and calls this method. Backends that bake the `ufo` client can use ufo_fs_file_op as their shared implementation.


##### `CommandStopping.stop_commands`  (lines 393–393)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Describes the optional ability to stop commands that may keep running after their launch call is cancelled. Only some backends need this, so it is separated from the main Carrier contract.

**Data flow**: An implementation receives a SandboxHandle that identifies the sandbox and turn. It stops commands associated with that turn and returns nothing when the stop request has been sent or completed.

**Call relations**: SandboxSession.stop_commands checks whether the carrier supports this protocol before calling it. This keeps normal carriers simple while allowing long-running remote command backends to clean up deliberately cancelled work.


##### `ufo_fs_file_op`  (lines 396–419)

```
async def ufo_fs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Provides a shared implementation of Carrier.file_op for sandboxes that bake the `ufo` client. It runs one file operation inside the sandbox and turns the command's JSON output into a Python dictionary.

**Data flow**: It receives a carrier, handle, operation name, and parameters. It serializes the parameters as compact JSON, runs `ufo fs` through Carrier.exec, trims and parses stdout, checks that the result is a JSON object, turns reported errors into ValueError, and returns the parsed dictionary on success.

**Call relations**: Carrier implementations can delegate their file_op method to this helper instead of repeating the same command-building and JSON-parsing code. It depends on Carrier.exec to actually run the `ufo fs` command in the sandbox.

*Call graph*: calls 1 internal fn (exec); 2 external calls (dumps, loads).


##### `workspace_path`  (lines 422–430)

```
def workspace_path(path: str) -> str
```

**Purpose**: Converts a tool-supplied path into an absolute path under /workspace and rejects attempts to escape that area. This is one of the core safety checks between user-controlled filenames and the sandbox filesystem.

**Data flow**: It receives a path string, treats relative paths as being under /workspace, normalizes dot and dot-dot path parts, and checks that the final path is still inside /workspace. It returns the safe absolute path or raises an error if the path tries to climb out.

**Call relations**: SandboxSession.write_file, file_exists, run_ufo_fs, and read_file call this before handing paths to the carrier. It uses _resolve_parts to do the path cleanup in a simple, controlled way.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 4 (file_exists, read_file, run_ufo_fs, write_file); 1 external calls (PurePosixPath).


##### `_resolve_parts`  (lines 433–442)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Normalizes path pieces by applying . and .. rules without allowing the path to climb above the workspace root. It is the small helper behind workspace_path's escape check.

**Data flow**: It receives a tuple of path parts. It walks them like a stack: normal names are pushed, . and empty parts are ignored, and .. pops one level unless that would escape the protected root. It returns the cleaned list of path parts.

**Call relations**: workspace_path calls this while turning a caller's path into a safe /workspace path. Keeping this logic separate makes the escape prevention easy to reason about.

*Call graph*: called by 1 (workspace_path).


##### `SandboxSession.authorize`  (lines 454–480)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new SandboxSession with updated proxy authority for a specific turn or member. This is used when the same sandbox container is shared, but each command needs the right network identity.

**Data flow**: It receives a new run token, environment variable names to remove, and environment values to add. It checks that the current handle has a token and that proxy environment values contain it, replaces the old token with the new one in proxy variables, removes cleared variables, merges in new values, and returns a new SandboxSession with a new SandboxHandle.

**Call relations**: This method does not mutate the existing session; it hands back a re-authorized copy. Later session calls such as bash, sh, python, file operations, or dial use the new handle and environment through the same carrier.

*Call graph*: 2 external calls (__init__, __init__).


##### `SandboxSession.bash`  (lines 482–487)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a command string inside the sandbox using bash. This is the convenient path for tools or extensions that need normal shell behavior.

**Data flow**: It receives a command string and an optional timeout. It builds an argument list for bash -lc, chooses the supplied timeout or the default, sends the command to the carrier, and returns the ExecResult from the backend.

**Call relations**: The sandbox Chrome extension calls this when starting or diagnosing Chrome inside the sandbox. Internally, it is a thin, safe wrapper over Carrier.exec.

*Call graph*: called by 2 (lease, _bring_up_failure).


##### `SandboxSession.sh`  (lines 489–497)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX sh script inside the sandbox with arguments passed separately. Passing arguments separately avoids risky string interpolation and lets backends rewrite workspace paths when needed.

**Data flow**: It receives a script, any number of string arguments, and an optional timeout. It builds a sh -c command where each argument is its own command-line element, sends it through the carrier, and returns the ExecResult.

**Call relations**: This is another session-level wrapper over Carrier.exec. It is useful when callers need simple shell scripting but still want arguments kept distinct from the script text.


##### `SandboxSession.python`  (lines 499–513)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with the containment guard made importable. This is used for small trusted helper programs that need safe path handling inside the sandbox.

**Data flow**: It receives Python source text, optional arguments, and an optional timeout. It prepends a bootstrap that carries the guard’s own source into the program, runs python3 in isolated mode, passes arguments separately, and returns the carrier's ExecResult.

**Call relations**: This method wraps Carrier.exec while adding the security bootstrap described by the constants in this file. It is the standard way for session code to run guarded Python snippets in the sandbox.


##### `SandboxSession.stop_commands`  (lines 515–520)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this session's turn if the carrier supports explicit stopping. It is used after the system has determined that a cancellation is intentional and should clean up sandbox work.

**Data flow**: It reads the session's carrier and handle. If the carrier implements CommandStopping, it calls stop_commands with the handle; otherwise it does nothing because that backend has no surviving commands to stop.

**Call relations**: This bridges the optional CommandStopping protocol into ordinary session use. It only hands off to the carrier when the carrier declares that stopping is meaningful.


##### `SandboxSession.write_file`  (lines 522–523)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file in the sandbox workspace. It gives callers a simple file-writing method while still enforcing the /workspace boundary.

**Data flow**: It receives a caller path and bytes. It converts the path through workspace_path, then passes the safe absolute path and content to Carrier.write. It returns nothing when the write succeeds.

**Call relations**: The skill runtime calls this when mounting skill files into the sandbox. It delegates safety checking to workspace_path and the actual byte transfer to the carrier.

*Call graph*: calls 1 internal fn (workspace_path); called by 1 (install_skill).


##### `SandboxSession.ensure_tool_output_dir`  (lines 525–547)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine's private .tool-output directory exists inside /workspace. It also removes a file or symlink squatting on that name so later tool-output offloading cannot be blocked.

**Data flow**: It runs a fixed shell script through the carrier. The script checks whether the target is already a directory, removes a non-directory or symlink at that exact fixed path, creates the directory, and prints a marker if it reclaimed a squatter. The method raises OSError on command failure and returns true only when something was reclaimed.

**Call relations**: This is a session helper built on Carrier.exec. Because the target path is a constant owned by the engine, the destructive cleanup is limited to the engine's private output area.


##### `SandboxSession.file_exists`  (lines 549–554)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the sandbox workspace. It is a small convenience for callers that need to branch based on the presence of a file.

**Data flow**: It receives a path, scopes it with workspace_path, runs test -f for that exact path through the carrier, and returns true when the command exits successfully. Other exit codes become false.

**Call relations**: It uses workspace_path for the same boundary protection as reads and writes. The actual check is performed by Carrier.exec inside the sandbox.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.run_ufo_fs`  (lines 556–567)

```
async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured `ufo fs` file operation through the carrier. It prepares the operation so every file tool is confined to /workspace in the same way.

**Data flow**: It receives an operation name and argument dictionary. It copies the arguments, scopes a string path argument if present, adds the fixed workspace root, calls Carrier.file_op, and returns the resulting dictionary.

**Call relations**: This is the session-level entry into the carrier's file_op contract. It calls workspace_path before handing parameters off, while the carrier decides whether to use ufo_fs_file_op or another backend-specific mechanism.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.read_file`  (lines 569–572)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox workspace in bounded chunks. This lets callers retrieve produced files without loading the whole file into memory at once.

**Data flow**: It receives a path, scopes it with workspace_path, and returns the async byte stream produced by Carrier.read. The caller consumes the chunks over time.

**Call relations**: It is the read counterpart to SandboxSession.write_file. It enforces the workspace path rule before delegating the actual streaming to the carrier.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.dial`  (lines 574–577)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Gets the outside address for a service listening on a port inside the sandbox. Callers use this when a sandbox process starts something that another component needs to contact.

**Data flow**: It receives a port number. It passes the session handle and port to Carrier.dial and returns the DialTarget containing host, TLS choice, and any required headers.

**Call relations**: The sandbox Chrome extension calls this after starting Chrome so it can reach Chrome's debugging endpoint. The method is a direct session wrapper over the carrier's backend-specific reachability logic.

*Call graph*: called by 1 (lease).


### `core/src/ufo/tools/file_changes.py`

`config` · `cross-cutting`

This file is intentionally tiny: it contains a single named value, `FILE_CHANGE_PATH_MAX_CHARS`, set to 4,096 characters. That value acts like a ruler for any part of the project that deals with paths in file-change records. A file path is the text address of a file, such as `src/app/main.py`; this constant says how long that address is allowed to be before it is considered too large.

Keeping this number in one place matters because path length rules should be consistent. Without a shared constant, different parts of the system might quietly choose different limits. One place might accept a very long path while another rejects it or fails later. By naming the limit here, the codebase can point to the same rule everywhere, like everyone using the same measuring tape.

There are no functions in this file. It does not perform checks by itself; it simply provides the value that other code can import and use when validating or shaping file-change data.
