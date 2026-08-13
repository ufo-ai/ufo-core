# Sandbox Execution, File I/O, and Network Mediation  `stage-12`

This stage is the system’s safe workshop for agent work during the main work loop. It gives each conversation a private workspace where commands can run, files can be read or written, websites can be opened, and network calls can be controlled.

The shared session layer is the front door. It gives all tools one common way to run commands, transfer files, and reach services inside the sandbox without exposing private host data. The conversation workspace code makes sure a conversation keeps returning to the same /workspace folder, so files do not get scattered across different sandboxes.

Different backends provide different kinds of workshop. The local sandbox uses an ordinary folder and subprocesses for development. The Docker extension runs work inside an isolated container. The E2B extension does the same in a remote cloud sandbox. All can route internet traffic through the same controls.

The egress proxy is the guarded exit, checking and metering outside requests and only adding credentials when allowed. Browser Session Control is the web bench, managing real browser sessions, clicks, downloads, page reading, and recovery.

## Sub-stages

- [Egress Proxy and Credential Injection](stage-12.1.md) `stage-12.1` — 2 files
- [Browser Session Control](stage-12.2.md) `stage-12.2` — 21 files

## Files in this stage

### Conversation workspace access
Conversation-level sandbox access ensures each conversation consistently uses the same private workspace.

### `core/src/ufo/sandbox/conversation.py`

`domain_logic` · `request handling and off-turn workspace access`

A conversation can have files: attachments that arrive before a turn, files written by tools during a turn, logs written by background jobs, and files shown in a browser. This file centralizes access to that storage so all of those paths point at one real sandbox. Think of it like a coat-check ticket: the database row stores the durable sandbox handle, and later code uses that ticket to find the same workspace again.

The main class, `ConversationSandbox`, can open a sandbox for active work, attach to an existing one for read-only browsing, write a file into it, list its contents, stream a file back, and prune old files. It also protects against a common race: two processes may both try to create the first sandbox for a conversation at the same time. The code uses a database compare-and-swap, meaning “only save my handle if the value is still what I saw earlier.” If another process wins first, the loser adopts the saved sandbox instead of continuing with an unreferenced one.

A key rule is that reads do not create sandboxes. If a conversation has no workspace yet, listing or reading returns empty or missing. That prevents a harmless-looking read request from secretly changing system state.

#### Function details

##### `ConversationSandbox.open`  (lines 79–104)

```
async def open(self, conversation_id: UUID, run_token: str, env: Mapping[str, str]) -> SandboxHandle
```

**Purpose**: Opens the sandbox for a conversation, creating it if needed, and makes sure the database records the sandbox handle that future processes should reuse. It is used when real work needs a writable workspace, such as a turn starting or an off-turn file write.

**Data flow**: It takes a conversation ID, a run token, and environment variables. It reads the currently stored sandbox handle from the database, asks `_opened` to create or resume a sandbox, then tries to save the resulting handle with `_claim`. If another opener saved a different handle first, it switches to that winner and tries again. It returns a `SandboxHandle`, which is the usable reference to the sandbox.

**Call relations**: The turn-opening path calls this through `_open_sandbox`, and `ConversationSandbox.write` also calls it before copying in a file. Inside this function, `_stored` fetches the current database value, `_opened` talks to the carrier to get a sandbox, and `_claim` settles any race over which handle becomes the official one.

*Call graph*: calls 3 internal fn (_claim, _opened, _stored); called by 2 (_open_sandbox, write).


##### `ConversationSandbox.existing`  (lines 106–126)

```
async def existing(self, conversation_id: UUID) -> SandboxHandle | None
```

**Purpose**: Attaches to a conversation’s already-recorded sandbox without creating a new one. This is the safe read path: if there is no usable existing sandbox, it simply returns nothing.

**Data flow**: It takes a conversation ID and reads the stored sandbox handle from the database. If there is no handle, or if the handle belongs to another backend, it returns `None`. Otherwise it builds a sandbox specification using an unsigned off-turn token and asks the carrier to attach to that existing sandbox, returning the resulting handle.

**Call relations**: The listing, pruning, and reading functions call this before touching files. It relies on `_stored` for the database lookup and on `sandbox_handle_id` to check whether this backend can understand the saved handle.

*Call graph*: calls 1 internal fn (_stored); called by 3 (entries, prune, read); 2 external calls (__init__, sandbox_handle_id).


##### `ConversationSandbox.write`  (lines 128–140)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s workspace at a relative path and returns the `/workspace/...` path where the agent will see the file. It is used for landing things like inbound attachments before a turn runs.

**Data flow**: It receives a conversation ID, a relative filename or path, and the file contents as bytes. It first rejects content larger than the configured size limit, then converts the relative path into a workspace path. It opens the conversation sandbox using the unsigned off-turn token, asks the carrier to write the bytes into the sandbox, and returns the final workspace path.

**Call relations**: This function sits above `open`: it uses `open` to ensure there is a real sandbox before writing. It also uses `workspace_path` so callers can give normal relative paths while the sandbox receives a proper `/workspace` path.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.prune`  (lines 142–154)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files directly under a workspace folder, keeping only the newest requested number. This protects unattended off-turn writers, such as log appenders, from growing a directory forever.

**Data flow**: It receives a conversation ID, a relative folder prefix, and a number of files to keep. It attaches to the existing sandbox; if none exists, it does nothing. If a sandbox exists, it runs a small Python program inside the sandbox that lists regular files in that directory and removes the oldest excess files. If the program fails, it raises an error with the sandbox’s message.

**Call relations**: It calls `existing` because pruning should not create a workspace by itself. Once attached, it creates a `SandboxSession` and runs the embedded prune script through a shell command, quoting paths safely before handing them to the sandbox.

*Call graph*: calls 1 internal fn (existing); 3 external calls (__init__, quote, workspace_path).


##### `ConversationSandbox.entries`  (lines 156–189)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists the files currently present in a conversation’s workspace for a file browser. It returns clean relative paths, sizes, and modification times instead of exposing internal container or host paths.

**Data flow**: It receives a conversation ID and attaches to the existing sandbox. If there is no sandbox, it returns an empty tuple. Otherwise it asks the sandbox file tool to find files under `/workspace`, checks that the result is a list, warns if the list was cut short, converts each returned file path into a workspace-relative path, builds `WorkspaceFile` records, sorts them by path, and returns them.

**Call relations**: This is one of the read-side callers of `existing`. It uses `SandboxSession` to run the sandbox file listing command, calls `_workspace_rel` to remove the workspace root from each returned path, and emits a warning through observability if the sandbox reports that the listing was truncated.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 4 external calls (__init__, __init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 191–195)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns an absolute path reported by a sandbox or local carrier into a path relative to the workspace root. This keeps the file browser from showing internal implementation paths.

**Data flow**: It receives a sandbox handle and a path string. It checks whether the path starts under the container workspace root or under the handle’s host workspace path. If so, it strips that root and returns the relative remainder. If the path is outside both known roots, it raises an error because that would mean the listing escaped the workspace.

**Call relations**: `entries` calls this for every file returned by the sandbox listing. It is a safety and cleanup step between raw sandbox output and user-facing `WorkspaceFile` records.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 197–206)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Returns a stream of bytes for one file in a conversation’s workspace, or `None` if the sandbox or file is missing. It lets callers download or inspect workspace files without loading the whole file at once.

**Data flow**: It receives a conversation ID and a relative path. It attaches to the existing sandbox; if there is none, it returns `None`. It then checks whether the requested file exists. If the file is absent, it returns `None`; if present, it returns an async byte stream from the sandbox session.

**Call relations**: Like `entries` and `prune`, it starts with `existing` so a read cannot create a sandbox. After that it uses `SandboxSession` to check for the file and hand back the streaming reader.

*Call graph*: calls 1 internal fn (existing); 1 external calls (__init__).


##### `ConversationSandbox._opened`  (lines 208–230)

```
async def _opened(self, conversation_id: UUID, stored: str | None, run_token: str, env: Mapping[str, str]) -> SandboxHandle
```

**Purpose**: Does the low-level work of preparing the workspace directory and asking the carrier to create or resume a sandbox. It is the helper that actually talks to the sandbox carrier during an open.

**Data flow**: It receives a conversation ID, the previously stored handle if any, a run token, and environment variables. For in-cluster carriers, it creates the host workspace directory and, when running as root, changes ownership so the sandbox user can write there. It then builds a sandbox specification, including the image, proxy, run token, optional resume ID, and environment, and asks the carrier to create or resume the sandbox. It returns the carrier’s sandbox handle.

**Call relations**: `open` calls this while trying to establish the conversation’s sandbox. It uses `sandbox_handle_id` to turn the stored database string into a backend-specific resume ID when possible, and it delegates the final create-or-resume action to the carrier.

*Call graph*: called by 1 (open); 4 external calls (__init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._stored`  (lines 232–245)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Reads the sandbox handle currently saved on the conversation’s database row. It is the single helper this file uses to learn what sandbox, if any, the conversation already owns.

**Data flow**: It receives a conversation ID. It opens a workspace database transaction, selects the conversation row for the current workspace, and reads its `sandbox_handle` field. If the conversation is not in the current workspace, it raises an error. Otherwise it returns the stored handle string or `None`.

**Call relations**: `open`, `existing`, and `_claim` all call this when they need the authoritative database value. It uses `workspace_tx` for the database transaction and `ws_current` so the lookup is limited to the active workspace.

*Call graph*: called by 3 (_claim, existing, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 247–268)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Tries to make a newly opened sandbox handle the official handle for the conversation, but only if the database still contains the value that was read earlier. This is the race-control step that prevents two simultaneous opens from both winning.

**Data flow**: It receives a conversation ID, the handle value that was originally seen, and the new handle it wants to save. It runs a conditional database update: save the new handle only if the current row still matches the earlier value. If the update succeeds, it returns the new handle. If it loses the race, it rereads the stored value and returns the winner’s handle; if the handle somehow disappeared, it raises an error.

**Call relations**: `open` calls this after `_opened` returns a sandbox. `_claim` uses the database as the referee, and if it loses, it calls `_stored` to discover which concurrent opener won.

*Call graph*: calls 1 internal fn (_stored); called by 1 (open); 3 external calls (update, workspace_tx, ws_current).


### Sandbox runtime providers
Local, Docker, and E2B providers implement the sandbox execution environments used to run commands and mediate network access.

### `core/src/ufo/sandbox/local.py`

`io_transport` · `sandbox setup and request handling`

This file is the no-frills way to run sandbox work on a developer’s own machine. Instead of starting a container, it treats the conversation’s workspace as a normal host directory and runs requested commands there. That makes local development easier, because only Python and the checked-in helper binaries are needed.

The important tradeoff is safety. This is not a true security boundary. A subprocess is not locked inside the workspace by the operating system the way a container would be. The code rewrites logical `/workspace` paths into the real host folder and relies on path checks around file operations, but a local process is still a host process.

The `LocalCarrier` class follows the same shape as other sandbox carriers. It can create or attach to a workspace, run a command, write a file, read a file, and answer whether a port can be reached. When creating a sandbox, it also prepares environment variables for web access through the project’s proxy, including fake model API keys and a certificate file. This keeps network metering and key-swapping behavior consistent with container-based sandboxes.

A small scratch area is created for helper programs like `sbx` and `sbxfs`, plus a fake home directory for tools that expect `$HOME`. File writes are careful: data is first written to a temporary sibling file, then renamed into place, so readers do not see half-written files.

#### Function details

##### `_provision_scratch`  (lines 47–60)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates a private temporary support area for the local carrier. This area holds helper command-line tools and a home directory for subprocesses, while keeping the actual workspace clean.

**Data flow**: It starts with no caller-supplied input. It makes a new temporary folder, adds `home` and `bin` subfolders, copies the bundled `sbx` and `sbxfs` helper programs into `bin`, marks them executable, and returns the path to this scratch folder.

**Call relations**: This is used automatically when a `LocalCarrier` is created. Later, `create` and `attach` use the scratch folder to build the command environment, especially the `PATH` and `HOME` seen by local subprocesses.

*Call graph*: 2 external calls (Path, mkdtemp).


##### `LocalCarrier.create`  (lines 67–98)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a usable local sandbox handle for a conversation. In practice, that means ensuring the workspace folder exists and preparing the environment that commands will inherit.

**Data flow**: It receives a sandbox specification containing the workspace path, conversation identity, proxy details, run token, and extra environment variables. It creates the workspace directory if needed, writes the proxy certificate into the scratch area, builds proxy and tool-related environment variables, and returns a `SandboxHandle` that other sandbox operations can use.

**Call relations**: This is the setup path for a new local sandbox. Code that wants to run commands or move files first gets this handle, then passes it to methods such as `exec`, `write`, and `read`.

*Call graph*: 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.attach`  (lines 100–119)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Connects to an already existing local workspace without creating it. This is useful when browsing or inspecting a previous conversation: if no workspace exists, it simply reports that there is nothing to attach to.

**Data flow**: It receives a sandbox specification and checks whether the workspace directory already exists. If it does not, it returns `None`; if it does, it returns a `SandboxHandle` with the same workspace location and a basic environment that includes the local helper tools.

**Call relations**: This is the read-only counterpart to `create`. Once it returns a handle, later operations such as `read` or helper-backed file browsing can use the same local workspace path.

*Call graph*: 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 121–147)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command as a normal host subprocess inside the workspace folder. It makes local execution look like sandbox execution by translating `/workspace` paths and applying the sandbox’s environment.

**Data flow**: It receives a sandbox handle, a command with arguments, and a timeout. It finds the real workspace folder, rewrites any `/workspace` references in the arguments to that real folder, starts the subprocess with that folder as its working directory, waits for output, and returns an execution result containing standard output, standard error, and an exit code. If the command runs too long, it kills it and returns a timeout result.

**Call relations**: Callers use this after `create` or `attach` has produced a handle. It relies on `_root` to find the workspace directory, then hands the command to the operating system’s subprocess machinery and packages the outcome as an `ExecResult`.

*Call graph*: calls 1 internal fn (_root); 3 external calls (__init__, create_subprocess_exec, wait_for).


##### `LocalCarrier.write`  (lines 149–180)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file inside the local workspace. It writes safely so another reader should see either the old complete file or the new complete file, not a half-written version.

**Data flow**: It receives a sandbox handle, a logical workspace path, and file content as bytes. It turns the logical path into a safe host target, creates parent directories if needed, checks whether an existing regular file has permission bits to preserve, writes the new data to a temporary sibling file, optionally applies the old permissions, and renames the temporary file into the final location. If anything fails, it removes the temporary file before re-raising the error.

**Call relations**: This is used when the system needs to place a file into the local sandbox. It depends on `_write_target` to reject invalid targets before writing, and it uses background thread calls so blocking disk work does not freeze the asynchronous event loop.

*Call graph*: calls 1 internal fn (_write_target); 3 external calls (to_thread, S_ISREG, uuid4).


##### `LocalCarrier.read`  (lines 182–190)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a file out of the local workspace in chunks. Chunked reading avoids loading a large file into memory all at once.

**Data flow**: It receives a sandbox handle and a logical workspace path. It converts that path to the real host file, opens it for binary reading, yields chunks of bytes until the file is exhausted, and then closes the file even if reading stops early.

**Call relations**: Callers use this to stream file contents from the local sandbox. It asks `_host_path` where the logical `/workspace` file lives on the host, then performs the blocking file reads in worker threads so the async loop stays responsive.

*Call graph*: calls 1 internal fn (_host_path); 1 external calls (to_thread).


##### `LocalCarrier.dial`  (lines 192–200)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Explains that local sandboxes do not expose per-port network addresses. If a caller needs to reach a service running inside a sandbox, this carrier cannot provide that feature.

**Data flow**: It receives a sandbox handle and a port number, but does not use them to create a connection target. Instead, it raises a clear `SandboxUnreachable` error telling the caller to use a remote carrier such as E2B for this kind of access.

**Call relations**: This fits the same carrier interface as remote sandboxes, but it deliberately stops this path. Any higher-level feature that wants to preview or connect to an in-sandbox service must handle this error or choose a carrier that supports external port access.

*Call graph*: 1 external calls (__init__).


##### `_root`  (lines 203–206)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the real host directory that backs `/workspace` for a local sandbox. It also catches the invalid case where a local sandbox handle has no workspace path.

**Data flow**: It receives a sandbox handle. If the handle has no host workspace path, it raises an error; otherwise it converts the stored path into a `Path` object and returns it.

**Call relations**: This is the common helper used by command execution and path conversion. `exec`, `_host_path`, and `_write_target` all ask it for the workspace root before touching files or starting commands.

*Call graph*: called by 3 (exec, _host_path, _write_target); 1 external calls (Path).


##### `_write_target`  (lines 209–217)

```
def _write_target(handle: SandboxHandle, path: str) -> Path
```

**Purpose**: Figures out the real host file that a write should land on, while refusing dangerous or nonsensical targets. In particular, it will not allow writing directly to the workspace root or outside it.

**Data flow**: It receives a sandbox handle and a logical path. It gets the workspace root, converts the logical path to a host path, normalizes it, and checks that the target is truly inside the workspace and not the root directory itself. If the target is invalid, it raises an `IsADirectoryError`; otherwise it returns the target path.

**Call relations**: This sits in front of `LocalCarrier.write` as a guardrail. It uses `_root` and `_host_path` to do the path calculation, then hands back only a safe target for the actual write step.

*Call graph*: calls 2 internal fn (_host_path, _root); called by 1 (write); 1 external calls (Path).


##### `_host_path`  (lines 220–223)

```
def _host_path(handle: SandboxHandle, path: str) -> Path
```

**Purpose**: Translates a logical sandbox path beginning with `/workspace` into the matching real path on the host machine. This is the bridge between the project’s sandbox view of files and the local carrier’s real folder.

**Data flow**: It receives a sandbox handle and a logical path string. It gets the workspace root, strips the `/workspace` prefix in POSIX-style path form, joins the remaining relative path onto the host workspace root, and returns the resulting host path.

**Call relations**: This helper is used by `read` when locating files to stream out and by `_write_target` when deciding where writes would land. It relies on `_root` for the base directory.

*Call graph*: calls 1 internal fn (_root); called by 2 (read, _write_target); 1 external calls (PurePosixPath).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox startup, command execution, file access, idle cleanup`

This file is the Docker-backed sandbox carrier. A “carrier” is the part of UFO that provides a workspace where commands can run. Here, that workspace is a Docker container: a lightweight isolated machine with its files mounted at `/workspace` from the host. The main problem this solves is safe, repeatable command execution without giving the sandbox direct access to the host or to raw API keys.

Each conversation gets a predictable container name and its own Docker network. When a turn starts, `DockerCarrier.create` finds the existing container, restarts a stopped one, or creates a new one. Commands are run with `docker exec`, and the proxy settings are added only for that command. This matters because a container can outlive one turn; it must not keep using an old turn’s run token.

The file also cleans up scarce Docker resources. Idle containers are stopped, not deleted, so their workspace survives. This is like turning off a parked car to save fuel while leaving everything inside it. Later, the carrier can “revive” the container and continue.

Reads and writes stream through Docker rather than loading whole files into memory. Network access is deliberately limited: Docker containers here do not expose inner ports to the outside, and all outbound web traffic is expected to pass through the egress proxy.

#### Function details

##### `_docker`  (lines 61–75)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs a Docker command as a child process and returns its exit code, output, and error text. It is the file’s common doorway to the Docker command-line tool.

**Data flow**: It receives Docker arguments, optional bytes for standard input, and a timeout. It starts `docker ...`, feeds the input, waits for the command to finish, and returns three things: the numeric result code, bytes written to standard output, and bytes written to standard error. If the command takes too long, it kills the process and returns a timeout-style result.

**Call relations**: Most of the carrier’s helper methods call this when they need Docker to inspect, create, start, stop, remove, or execute inside containers. It hides the repetitive subprocess work so higher-level methods can talk in terms of sandbox actions.

*Call graph*: called by 12 (_death_report, _ensure_network, _held_id, _install_ca, _reclaim_idle, _release, _revive, _running_id, _stopped_id, _write_started (+2 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 88–184)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Opens the sandbox for a conversation. It either connects to an already running container, restarts a stopped one, or creates a new Docker container and network.

**Data flow**: It takes a sandbox specification containing the conversation id, image, workspace path, proxy details, run token, and environment variables. It first marks the conversation as active and reclaims old idle containers. Then it builds per-command proxy environment variables, looks for an existing container, installs the current proxy certificate, and returns a `SandboxHandle` describing the usable sandbox. If it creates a new container and something fails, it removes the partial container and network.

**Call relations**: This is the main entry used when a turn needs a Docker sandbox. It calls the lookup helpers to find running or stopped containers, calls the revival path when needed, creates the network when needed, installs the proxy certificate, and uses `_docker` for the actual Docker operations.

*Call graph*: calls 8 internal fn (_ensure_network, _install_ca, _network_name, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 1 external calls (__init__).


##### `DockerCarrier.attach`  (lines 186–210)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an existing conversation container for read-only-style access without creating a new one. If the container was stopped by idle cleanup, it tries to restart it.

**Data flow**: It receives a sandbox specification and looks up the expected Docker container name. If a running container exists, it returns a handle. If only a stopped container exists, it attempts to revive it and then returns a handle. If no usable container exists, or revival fails in the expected ways, it returns `None`.

**Call relations**: This is used when the system wants to reconnect to an existing sandbox rather than open a fresh one. It relies on `_running_id`, `_stopped_id`, and `_revive`, but unlike `create` it does not install proxy environment settings because its intended reads do not need outbound network access.

*Call graph*: calls 3 internal fn (_revive, _running_id, _stopped_id); 1 external calls (__init__).


##### `DockerCarrier._reclaim_idle`  (lines 212–280)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops old inactive containers and removes their Docker networks so the host does not run out of memory or network subnets. It preserves the container and workspace so a later turn can restart it.

**Data flow**: It receives the conversation currently being opened, marks it as recently touched, asks Docker which UFO containers and networks currently exist, and records any it did not already know about. It then finds conversations that have been idle long enough and are not currently running a command. For each stale conversation, it reserves the cleanup by removing its touch record, stops the container if present, removes the network, and restores the record if cleanup failed so it can retry later.

**Call relations**: `create` calls this before opening a sandbox, making new activity the trigger for cleanup. It calls `_held_id` to find containers in any state and `_release` to do the stop-and-network-removal work, using a per-conversation lock so cleanup cannot collide with revival.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 282–312)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the container and returns its output. It also protects the container from idle cleanup while the command is running.

**Data flow**: It receives a sandbox handle, a command argument tuple, and a timeout. It increases an in-flight counter, marks the conversation as recently used, builds Docker `--env` arguments from the handle’s per-turn proxy environment, and runs `docker exec`. If Docker says the container is not running, it tries to revive the container and run the command once more. It returns an `ExecResult` with stdout, stderr, and the exit code, then updates the activity time and in-flight counter.

**Call relations**: Higher-level sandbox command execution reaches Docker through this method. It uses `_docker` to run `docker exec` and `_revive` when idle cleanup has stopped the container underneath the caller.

*Call graph*: calls 2 internal fn (_revive, _docker); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 314–328)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file inside the container’s workspace. It streams the content through standard input, so file bytes do not have to be placed on the command line.

**Data flow**: It receives a sandbox handle, a path, and raw bytes. It marks the conversation as busy, asks `_write_started` to create parent directories and write the bytes inside the container, and retries once after revival if Docker reports the container is stopped. If the write still fails, it raises an `OSError`; either way, it updates the activity tracking when done.

**Call relations**: This is the public file-write path for the Docker carrier. It delegates the actual Docker command to `_write_started` and shares the same revive-on-stopped behavior used by command execution and reading.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 330–345)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Performs one actual attempt to write a file into the container. It is separated from `write` so the caller can retry the whole attempt after reviving a stopped container.

**Data flow**: It receives the sandbox handle, destination path, and bytes to write. It runs `docker exec -i` with a shell command that creates the destination directory and pipes standard input into the target file. It returns the Docker exit code and error output.

**Call relations**: `write` calls this for the first write attempt and possibly again after `_revive`. This helper uses `_docker` for the subprocess work and does not decide whether failure should raise; that decision stays in `write`.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write).


##### `DockerCarrier.read`  (lines 347–381)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the container in chunks. It avoids loading the whole file into memory and reports file errors in a way that matches normal operating-system errors where possible.

**Data flow**: It receives a sandbox handle and a path, marks the conversation as active, and starts a `cat` process inside the container through `_read_started`. As chunks arrive, it yields them to the caller. After the stream ends, it checks whether the command failed. If the failure was because the container was stopped, it revives and restarts the read from the beginning. If `cat` reported a normal file-system reason such as “No such file,” it turns that into an `OSError`; otherwise it raises a runtime error with extra container state from `_death_report` when needed.

**Call relations**: This is the public file-read path for the Docker carrier. It coordinates `_read_started`, `_revive`, and `_death_report`, while also updating the same in-flight and last-touched counters used by cleanup.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 383–425)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Starts one read attempt and returns both the byte stream and a place where the final failure, if any, will be recorded. This shape lets `read` retry cleanly without reusing the same async generator.

**Data flow**: It receives a sandbox handle and path. It prepares an empty failure list and creates an inner async generator that will run `docker exec ... cat path`. It returns that generator plus the list. When the generator is later consumed, bytes flow out first; after the command exits, a nonzero exit code and error text are stored in the list.

**Call relations**: `read` calls this to begin a read attempt, and may call it again after `_revive`. The inner `stream` function does the actual subprocess work, while this wrapper gives `read` a simple way to inspect the result after the stream has ended.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 398–423)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `cat` inside the container and yields the file contents piece by piece. It also cleans up the Docker exec process if the caller stops reading early.

**Data flow**: It starts `docker exec` with standard output and standard error pipes. It repeatedly reads up to a fixed chunk size from standard output and yields each chunk. After output ends, it reads the error text and waits for the process exit code. If the command failed, it records the code and error text in the shared failure list. If the generator is abandoned before the process finishes, it kills the process and drains its pipes.

**Call relations**: This inner generator is created by `_read_started` and consumed by `read`. It is the low-level streaming bridge between Docker’s process output and the caller receiving file bytes.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 427–445)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Adds useful Docker container state to a mysterious read failure. It helps distinguish whether the container died, was killed for memory, disappeared, or whether only the `cat` command failed.

**Data flow**: It receives a sandbox handle and asks Docker to inspect the container’s status, exit code, and out-of-memory flag. If inspection succeeds, it returns a short text report with those facts. If inspection itself fails, it returns text saying that inspection failed and includes Docker’s error output.

**Call relations**: `read` calls this only when `cat` failed without useful error text. It uses `_docker` to query Docker and gives the final runtime error more context for debugging.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.dial`  (lines 447–454)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that this Docker carrier cannot expose a service port from inside the sandbox to the outside world. It tells callers to use a remote carrier if they need that feature.

**Data flow**: It receives a sandbox handle and a port number, but does not try to connect. It immediately raises `SandboxUnreachable`, explaining that Docker sandboxes in this setup do not provide external per-port addresses.

**Call relations**: This is the Docker carrier’s answer to the general sandbox “dial a port” feature. Unlike carriers that can publish browser or preview ports, this one deliberately refuses the request.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 456–470)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation’s container and removes its Docker network. This frees host resources while leaving the stopped container’s filesystem available for later revival.

**Data flow**: It receives a conversation id and, if known, a container id. It stops the container when one is present, then removes the conversation-specific Docker network. It returns `true` if both steps succeeded or if the network was already gone, and `false` if stopping or removal failed in a way that should be retried later.

**Call relations**: `_reclaim_idle` calls this while holding the conversation’s lifecycle lock. It uses `_network_name` to compute the network name and `_docker` to perform the stop and network removal.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 472–492)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Restarts a container that idle cleanup previously stopped. It recreates or reconnects the container’s Docker network before starting the container.

**Data flow**: It receives a conversation id and container id. Under the conversation’s lifecycle lock, it marks the conversation as active, computes the network name, ensures the network exists, connects the container to it, and starts the container. It returns `true` when the container is running again, `false` when Docker refuses a normal connect or start, and raises if network creation itself fails unexpectedly.

**Call relations**: This is the shared recovery path used by `create`, `attach`, `exec`, `write`, and `read` whenever they find a stopped container. It calls `_ensure_network`, `_network_name`, and `_docker`, and its lock keeps it from interleaving with `_release`.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (attach, create, exec, read, write).


##### `DockerCarrier._held_id`  (lines 494–502)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container id for a given container name in any state: running, paused, or stopped. Cleanup needs this because even non-running containers may still be relevant to resource release.

**Data flow**: It receives a container name, asks Docker for all containers matching exactly that name, and returns the matching id as text. If none exists, it returns `None`. If Docker itself fails, it raises an error instead of pretending the container is absent.

**Call relations**: `_reclaim_idle` calls this before releasing an idle conversation. It uses `_docker` to query Docker and gives `_release` the id to stop if a container is still present.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 504–513)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds the id of a stopped container with a given name. This tells the carrier whether a previous sandbox can be restarted instead of creating a new one.

**Data flow**: It receives a container name, asks Docker for exited containers matching exactly that name, and returns the id if found. If no stopped container matches, it returns `None`. If the Docker query fails, it raises a runtime error.

**Call relations**: `create` and `attach` use this after checking for a running container. When it finds a stopped container, those callers can hand it to `_revive` and preserve the existing workspace.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 515–526)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds the id of a currently running container with a given name. It treats Docker command failures as real failures, not as “no container found.”

**Data flow**: It receives a container name, asks Docker for running containers matching exactly that name, and returns the id if present. Empty output means no running match. A nonzero Docker exit code becomes a runtime error so callers do not accidentally try to create a duplicate container after a Docker hiccup.

**Call relations**: `create` and `attach` use this as their first lookup step. It uses `_docker` for the query and helps those callers decide whether to reuse, revive, or create a container.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 528–529)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Docker network name for one conversation. The name is deterministic, so the carrier can find the same network later.

**Data flow**: It receives a conversation id and combines the carrier’s network prefix with the id written as compact hexadecimal text. The result is a string such as a project prefix followed by the conversation identifier.

**Call relations**: `create`, `_revive`, and `_release` call this whenever they need the exact Docker network name. It keeps the naming rule in one place.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 531–542)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if necessary. It is safe if two callers race and one creates the network first.

**Data flow**: It receives a network name, asks Docker whether a matching network already exists, and returns immediately if it does. Otherwise it runs `docker network create`. If Docker says the network already exists, that is accepted as success; other failures raise an error.

**Call relations**: `create` calls this before starting a fresh container, and `_revive` calls it before reconnecting a stopped container. It uses `_docker` for both the lookup and creation commands.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 544–557)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current egress proxy certificate inside a container. This lets HTTPS requests from the sandbox trust the local proxy that is inspecting and forwarding traffic.

**Data flow**: It receives a container id and certificate text. It runs a root `docker exec` command that writes the certificate into the container’s trusted certificate directory and refreshes the certificate store. If the command fails, it raises a runtime error with Docker’s message.

**Call relations**: `create` calls this whenever it returns a usable container, whether the container is new, already running, or revived. That is important because the proxy certificate can change when the host process restarts.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `manifest`  (lines 560–565)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It says that the carrier named `docker` is provided by `DockerCarrier`.

**Data flow**: It takes no input. It constructs and returns a `Manifest` containing the extension name, version, and a carrier specification that points to the `DockerCarrier` factory.

**Call relations**: The extension loader calls this to discover what this file provides. The returned manifest is how the rest of UFO learns that setting the sandbox backend to Docker should use this carrier.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox creation, request handling, command/file I/O`

A sandbox is the safe work area where a conversation can run commands and read or write files. This file is the E2B version of that work area. It plugs a new carrier named “e2b” into the system, so configuration can choose E2B without the core system knowing E2B-specific details.

The main job is to keep one remote sandbox tied to a conversation. If the conversation already has a sandbox id, the carrier reconnects to it. If this process recently opened one, it can reuse that. Otherwise it creates a fresh E2B sandbox from a configured template. E2B can pause idle sandboxes without deleting their disk, so this file treats a pause like a sleep, not a loss. It carefully renews leases so a sandbox does not pause in the middle of a command.

Before use, the sandbox is prepared like a rented room being stocked: the proxy’s certificate is installed so HTTPS traffic is trusted, and `/workspace` is created as the conversation’s file area. Commands run with environment variables that force outgoing internet traffic through UFO’s public egress proxy. That proxy uses the turn’s run token to meter requests and swaps placeholder model API keys for real ones outside the sandbox.

The file also supports uploading files, streaming files back in chunks, and building a public dial target for services running inside the sandbox.

#### Function details

##### `_egress_env`  (lines 107–142)

```
def _egress_env(proxy: ProxyEndpoint, run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make sandbox commands send all outside network traffic through UFO’s egress proxy. It also puts safe placeholder model API keys in the sandbox instead of real secrets.

**Data flow**: It receives a proxy description and a run token. It checks that the proxy has a public HTTPS URL, turns that URL into proxy settings that include the run token as the username, adds no-proxy exceptions and certificate settings, and returns a dictionary of environment variables. If the proxy URL is missing or unsafe, it raises an error before any sandbox command can run with unmetered access.

**Call relations**: When `E2BCarrier.create` prepares a sandbox handle for a turn, it calls this first so every later command launched through that handle has the right network rules.

*Call graph*: called by 1 (create); 1 external calls (urlsplit).


##### `E2BCommands.run`  (lines 155–163)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None) -> E2BCommandResult
```

**Purpose**: Describes the E2B SDK method used to run a shell command inside a sandbox. This is a protocol method, meaning it documents the shape of the SDK object this file expects rather than implementing the command itself.

**Data flow**: A command string, optional working directory, environment variables, user name, and timeout go into the E2B SDK. The SDK runs the command remotely and returns stdout, stderr, and an exit code, or raises an SDK exception if the command fails in a special way.

**Call relations**: Carrier methods such as command execution and sandbox preparation rely on SDK objects that satisfy this method shape, so the rest of the file can call `sandbox.commands.run` without depending directly on concrete SDK classes.


##### `E2BFileStream.__aiter__`  (lines 170–170)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: Describes how a streamed file read can be used in an async loop. In plain terms, it says the file arrives piece by piece instead of all at once.

**Data flow**: The open stream is used as the input. Iterating over it produces chunks of bytes until the file is fully read or the stream ends.

**Call relations**: `E2BCarrier.read` depends on this behavior when it yields file chunks back to the caller without loading the whole file into memory.


##### `E2BFileStream.aclose`  (lines 172–172)

```
async def aclose(self) -> None
```

**Purpose**: Describes the SDK method for closing an open streamed file connection. This matters because a stream holds a network connection until it is explicitly released.

**Data flow**: The open stream goes in. The method tells the SDK to close it, and no file bytes come out; the side effect is freeing the underlying connection.

**Call relations**: `E2BCarrier.read` calls this in a cleanup step so the connection is closed even if the caller stops reading early.


##### `E2BFiles.write`  (lines 176–176)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: Describes the E2B SDK method used to upload data into a sandbox file. It is the safe path for sending raw bytes, because command execution only accepts shell text.

**Data flow**: A target path, text or bytes, and optionally a user name go into the SDK. The SDK writes that content inside the sandbox and returns an SDK-specific acknowledgement.

**Call relations**: `E2BCarrier.write` uses this for user file uploads, and `_install_ca` uses it to place the proxy certificate into the sandbox before installing it.


##### `E2BFiles.read`  (lines 178–178)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: Describes the E2B SDK method used to open a sandbox file for reading. In this file it is used in streaming mode so large files can be read safely.

**Data flow**: A path and requested format go into the SDK. The SDK opens the remote file and returns a stream object that can produce bytes over time.

**Call relations**: `E2BCarrier.read` calls this when a caller wants to download a file produced inside the sandbox.


##### `E2BSandbox.get_host`  (lines 187–187)

```
def get_host(self, port: int) -> str
```

**Purpose**: Describes the SDK method that turns an in-sandbox port into an externally reachable host name. This is how callers reach a web server or browser endpoint started inside the remote sandbox.

**Data flow**: A port number goes in. The SDK formats or retrieves the host name that routes outside traffic to that port in the sandbox.

**Call relations**: `E2BCarrier.dial` uses this after ensuring the sandbox lease is valid, then wraps the host together with TLS and access-token information.


##### `E2BSdk.create`  (lines 191–200)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK call for creating a new remote sandbox. It is used when no usable previous sandbox exists for a conversation.

**Data flow**: A template name, lease timeout, metadata, lifecycle rules, network rules, and API key go into E2B. E2B starts a sandbox and returns an object representing it.

**Call relations**: `E2BCarrier._resume_or_open` calls this after deciding that it cannot reconnect to an existing sandbox.


##### `E2BSdk.connect`  (lines 202–208)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: Describes the E2B SDK call for reconnecting to an existing sandbox by id. In E2B this also wakes a paused sandbox and renews its lease.

**Data flow**: A sandbox id, requested timeout, and API key go into E2B. If the sandbox still exists, E2B returns a live sandbox object; if it is gone, the SDK raises a not-found error.

**Call relations**: `E2BCarrier.attach`, `_resume_or_open`, and `_sandbox` rely on this as the safe way to resume work on a paused or previously opened sandbox.


##### `E2BCarrier.create`  (lines 238–298)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Opens the sandbox for a conversation and returns a handle the rest of the system can use. It either reconnects to the conversation’s existing sandbox, reuses this process’s current one, or creates a new one.

**Data flow**: It receives a sandbox specification containing the conversation id, optional resume id, run token, proxy details, and extra environment variables. It builds proxy environment settings, chooses the sandbox id to resume if one is available, opens or reconnects to E2B, records a local lease, prepares the sandbox certificate and workspace, and returns a `SandboxHandle` with the sandbox id and command environment.

**Call relations**: This is the main entry used by the sandbox layer when a turn needs a work area. It calls `_egress_env` for network safety, `_leased` to check the local cache, `_resume_or_open` to get the E2B sandbox, and `_prepare` to make that sandbox usable before handing back the handle.

*Call graph*: calls 4 internal fn (_leased, _prepare, _resume_or_open, _egress_env); 5 external calls (__init__, __init__, timeout, emit_metric, log).


##### `E2BCarrier.attach`  (lines 300–323)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an already-known sandbox for read-style access without creating a new one. If the saved sandbox id is missing or E2B no longer has it, it returns `None` instead of silently creating an empty replacement.

**Data flow**: It receives a sandbox specification with an optional resume id. If there is no id, it returns `None`; if there is an id, it asks E2B to connect and renew the lease. On success it stores the lease and returns a handle; on not-found it clears any local cache entry and returns `None`.

**Call relations**: This supports flows that need to look at an existing conversation sandbox. Unlike `create`, it deliberately does not fall back to creating a fresh sandbox, because that would hide the fact that the old workspace is gone.

*Call graph*: 2 external calls (__init__, __init__).


##### `E2BCarrier._resume_or_open`  (lines 325–357)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: Contains the decision for “resume this sandbox if possible, otherwise open a new one.” It keeps the public `create` method simpler and centralizes E2B’s reconnect-or-create behavior.

**Data flow**: It receives the sandbox specification and an optional sandbox id. If an id is present, it tries to connect to it; if E2B says it is missing, it logs that loss. Then it creates a new sandbox from the configured template, checks that E2B supplied a traffic access token, and returns the sandbox object.

**Call relations**: `E2BCarrier.create` calls this after deciding which resume id, if any, should be trusted. It hands back the sandbox that `create` will cache, prepare, and expose through a handle.

*Call graph*: called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare`  (lines 359–366)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Makes a sandbox ready for normal UFO work. It installs the proxy certificate and ensures `/workspace` exists with the right ownership.

**Data flow**: It receives a sandbox object and the proxy certificate text. It first asks `_install_ca` to place and install the certificate, then asks `_ensure_workspace` to create and fix ownership of the workspace directory. It returns nothing, but the sandbox is changed so commands can trust the proxy and use the workspace.

**Call relations**: `E2BCarrier.create` calls this whenever it opens or resumes a sandbox. It delegates the two concrete setup jobs to `_install_ca` and `_ensure_workspace`.

*Call graph*: calls 2 internal fn (_ensure_workspace, _install_ca); called by 1 (create).


##### `E2BCarrier._leased`  (lines 368–383)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: Looks up the local cached lease for a conversation and cleans out leases whose remembered deadlines have passed. This keeps the process from remembering every sandbox it has ever touched.

**Data flow**: It receives a conversation id and reads the carrier’s `_live` map. It checks the current clock, deletes expired entries from the map, and returns the lease that was associated with the requested conversation if one was present.

**Call relations**: `E2BCarrier.create` uses this to see whether this process already has a recent sandbox for the conversation. `_sandbox` uses it before deciding whether it must reconnect to E2B and renew the lease.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 385–393)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: Installs UFO’s proxy certificate into the sandbox so HTTPS connections through the proxy are trusted. Without this, many secure network requests from inside the sandbox would fail certificate checks.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path as root, then runs a root command that installs it into the system certificate store. If the install command exits with an error, it raises a clear runtime error containing the command’s output.

**Call relations**: `_prepare` calls this before setting up the workspace, because network trust is part of making the sandbox safe and usable.

*Call graph*: called by 1 (_prepare).


##### `E2BCarrier._ensure_workspace`  (lines 395–404)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: Creates `/workspace` inside the sandbox and makes sure the normal sandbox user owns it. This directory is the conversation’s working disk area.

**Data flow**: It receives a sandbox. It runs a root command that creates the workspace directory if needed and changes its owner to the sandbox user. If that command fails, it raises a runtime error with the command’s output.

**Call relations**: `_prepare` calls this after certificate installation so later command execution can use `/workspace` as its working directory.

*Call graph*: called by 1 (_prepare).


##### `E2BCarrier.exec`  (lines 406–442)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the E2B sandbox and returns a normal `ExecResult` with stdout, stderr, and an exit code. It also makes sure the sandbox lease lasts long enough for the command.

**Data flow**: It receives a sandbox handle, an argument list, and a command timeout. It gets or renews the sandbox through `_sandbox`, quotes the argument list into a shell command, runs it in `/workspace` with the sandbox and proxy environment variables, and converts the SDK result into `ExecResult`. Command failures and timeouts are translated into exit-style results; unexpected provider failures drop the cached lease and are re-raised.

**Call relations**: This is the command-running method the rest of the sandbox system calls during a turn. It depends on `_sandbox` for a valid E2B connection and uses `_drop` if the provider connection becomes untrustworthy.

*Call graph*: calls 2 internal fn (_drop, _sandbox); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.write`  (lines 444–455)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Uploads bytes into a file inside the sandbox. It is used for file content because E2B command execution does not provide a standard input stream for arbitrary bytes.

**Data flow**: It receives a sandbox handle, a target path, and byte content. It gets a leased sandbox through `_sandbox` and writes the bytes through E2B’s file API. If the provider call fails, it drops the cached lease and raises the original error.

**Call relations**: Callers use this when they need to place files into the conversation workspace. It shares the lease-renewal path with command execution and uses `_drop` on failed file transport.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 457–475)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. This lets large files be downloaded without storing the whole file in the UFO process at once.

**Data flow**: It receives a sandbox handle and path. It gets a leased sandbox through `_sandbox`, opens the file through E2B’s streaming file API, yields each byte chunk to the caller, and always closes the stream afterward. If E2B says the file is missing, it turns that into Python’s normal `FileNotFoundError`; if another provider error happens, it drops the lease and re-raises.

**Call relations**: This is the file-download path for sandbox users. It relies on `_sandbox` before opening the stream and on the stream’s `aclose` behavior to release the network connection.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.dial`  (lines 477–495)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Builds the outside address needed to reach a service running on a port inside the sandbox. For example, this can expose a dev server or browser debugging endpoint started by a command.

**Data flow**: It receives a sandbox handle and port. It ensures the sandbox is reachable through `_sandbox`, asks E2B for the host name for that port, adds TLS and the sandbox traffic access token as a header when available, and returns a `DialTarget`. If the sandbox is gone, it raises the carrier-neutral `SandboxUnreachable` error.

**Call relations**: The rest of the system calls this when it needs inbound access to something running inside the sandbox. It uses `_sandbox` because an address is only useful if the remote container is awake and leased.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 497–534)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int) -> E2BSandbox
```

**Purpose**: Returns a sandbox object whose E2B lease should last long enough for the next piece of work. It is the central renewal gate for commands, file operations, and dialing.

**Data flow**: It receives a sandbox handle and the number of seconds the caller needs. It checks the local lease cache through `_leased`; if the cached sandbox id matches and the lease has enough time left, it returns it. Otherwise it removes the old cache entry, reconnects to E2B with a long enough timeout, stores the new lease, logs the renewal, and returns the sandbox.

**Call relations**: `exec`, `write`, `read`, and `dial` all call this before touching E2B. This keeps lease logic in one place instead of scattering reconnect decisions through every operation.

*Call graph*: calls 1 internal fn (_leased); called by 4 (dial, exec, read, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 536–541)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: Forgets a cached sandbox lease after a provider call fails. The idea is simple: if E2B did not answer reliably, this process should not trust its remembered deadline anymore.

**Data flow**: It receives a conversation id and a short label for what was happening. It removes that conversation from the local lease map and logs that the lease was dropped. It returns nothing.

**Call relations**: `exec`, `write`, and `read` call this when an unexpected E2B failure happens, so the next operation will reconnect instead of reusing stale local state.

*Call graph*: called by 3 (exec, read, write); 1 external calls (log).


##### `build_e2b_carrier`  (lines 544–551)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: Creates an `E2BCarrier` from environment variables. It fails early with a clear message if the E2B API key or template name is missing.

**Data flow**: It reads `E2B_API_KEY` and `E2B_TEMPLATE` from the process environment. If either value is absent, it raises an error; otherwise it constructs and returns an `E2BCarrier` with those settings.

**Call relations**: `manifest` registers this function as the factory for the `e2b` carrier, so the wider system calls it when configuration selects the E2B backend.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 554–559)

```
def manifest() -> Manifest
```

**Purpose**: Publishes this extension to UFO’s plugin system. It says there is a carrier named `e2b`, how to build it, and that it runs off-cluster.

**Data flow**: It takes no input. It creates a carrier specification pointing to `build_e2b_carrier`, wraps it in a manifest object with the extension name and version, and returns that manifest.

**Call relations**: The host application discovers this function when loading extensions. The returned manifest is what connects the name `e2b` in configuration to the carrier implementation in this file.

*Call graph*: 2 external calls (__init__, __init__).


### Shared sandbox session interface
The session layer defines the common safe interface for command execution, file movement, workspace access, and service routing across sandbox providers.

### `core/src/ufo/sandbox/session.py`

`domain_logic` · `per-turn sandbox access and tool execution`

This file is the contract for working with a sandbox: a contained place where each conversation can run commands and keep files under `/workspace`. The important rule is that tools may touch only that workspace. They must not reach the transcript, stored conversation records, or other private system data.

The file does three main jobs. First, it defines small value objects, such as `SandboxSpec`, `SandboxHandle`, `ExecResult`, and `DialTarget`, that describe a sandbox, a running container, a command result, or a reachable in-sandbox port. Second, it defines `Carrier`, a protocol, meaning a promise that different sandbox backends must keep. Docker, local execution, or a remote sandbox can all implement the same actions: create or attach to a sandbox, execute commands, read and write files, and expose a port.

Third, it provides `SandboxSession`, the object tools actually use during a turn. It wraps the carrier and adds safety checks, such as forcing file paths to stay under `/workspace`, replacing proxy credentials with the current turn’s signed token, and using bounded file streaming so large files do not get loaded all at once.

A useful analogy is a hotel keycard. The carrier owns the building; the session is the keycard for one guest and one stay. It opens only the rooms that guest is allowed to enter.

#### Function details

##### `RunTokenCodec.from_env`  (lines 53–57)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Builds the token signer from the deployment secret stored in an environment variable. This secret is needed so sandbox network requests can prove which workspace and turn they belong to.

**Data flow**: It reads the configured secret value from the process environment. If the value is missing, it stops with a clear runtime error. If present, it turns the text into bytes and returns a `RunTokenCodec` ready to sign and verify run tokens.

**Call relations**: Startup paths call this when serving the main app or the proxy. That means token signing is prepared before sandboxes begin making proxied network requests.

*Call graph*: called by 2 (serve, run).


##### `RunTokenCodec.encode`  (lines 59–62)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a run identity into a signed token that can be used as the proxy username for one sandbox turn. The signature prevents a sandbox from inventing another workspace or turn identity.

**Data flow**: It receives a `RunToken` containing a workspace ID, turn ID, and optional acting member ID. It formats those pieces into a compact text payload, signs that payload with the codec secret, and returns the signed token string.

**Call relations**: The sandbox-opening flow calls this when preparing a sandbox for a turn. It hands the finished token to the proxy environment so later outbound network traffic can be attributed to the right run.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 64–79)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads a proxy `Authorization` header and recovers the run identity only if the token was genuinely signed by this deployment. It is the safety check on the receiving side of proxy authentication.

**Data flow**: It takes an HTTP proxy authorization header, expects Basic authentication, decodes the username, verifies the username as a signed token, and splits the verified payload into workspace, turn, and member IDs. If anything is malformed, unsigned, or from the wrong token domain, it raises a plain `ValueError`; otherwise it returns a `RunToken`.

**Call relations**: No direct caller is shown in the provided graph, but it is the natural counterpart to `RunTokenCodec.encode`: the proxy receives the signed username and uses this method to turn it back into trusted run information.

*Call graph*: 4 external calls (__init__, b64decode, verify_token, UUID).


##### `sandbox_handle_id`  (lines 142–147)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Extracts the backend-specific sandbox ID from a stored handle only if it belongs to the requested backend. This prevents one sandbox provider from trying to resume another provider’s saved container ID.

**Data flow**: It receives a backend name and a stored handle string. If the string begins with that backend name plus the separator, it returns the rest of the string; otherwise it returns `None`.

**Call relations**: This helper is used around sandbox resume decisions. It protects deployments that switch carrier backends by making old handles from a different backend invisible to the new one.


##### `Carrier.create`  (lines 179–179)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the required operation for creating or attaching to the main sandbox for a conversation. Each backend implements this in its own way, but callers can rely on receiving a usable sandbox handle.

**Data flow**: It receives a `SandboxSpec`, which describes the conversation, image, workspace location, proxy settings, and run token. The implementation creates or reconnects to the sandbox and returns a `SandboxHandle` that later operations can use.

**Call relations**: This is part of the carrier contract. Higher-level sandbox setup code calls it through the `Carrier` interface, while concrete backends such as Docker or remote sandboxes provide the actual behavior.


##### `Carrier.attach`  (lines 181–187)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines a non-creating lookup for an existing sandbox. It is used when the system wants to read or reconnect only if the sandbox is already there, without accidentally starting a fresh one.

**Data flow**: It receives a `SandboxSpec`, usually with a resume ID. The implementation checks whether that sandbox is reachable and returns a `SandboxHandle` if it is; if not, it returns `None`.

**Call relations**: This sits beside `Carrier.create` in the backend contract. Read-style flows can use it to avoid changing system state just because someone browsed a conversation’s files.


##### `Carrier.exec`  (lines 189–191)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines how to run a command inside the sandbox. This is the core action behind shell tools and in-sandbox helper programs.

**Data flow**: It receives a sandbox handle, a command as a tuple of arguments, and a timeout in seconds. The implementation runs the command inside the sandbox and returns standard output, standard error, and an exit code in an `ExecResult`.

**Call relations**: `SandboxSession` methods call this for shell commands, file checks, tool-output directory setup, and `sbxfs` helper operations. Each carrier backend supplies the real command-running mechanism.


##### `Carrier.write`  (lines 193–199)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how to copy bytes from the host into a file under the sandbox workspace. It exists so large content can be written safely without squeezing it through a command line.

**Data flow**: It receives a sandbox handle, an absolute workspace path, and the bytes to write. The implementation creates any needed parent directories and writes the bytes into the sandbox filesystem.

**Call relations**: `SandboxSession.write_file` calls this after checking that the requested path stays inside `/workspace`. Concrete carriers decide whether writing means a filesystem API call, a Docker stream, or local disk access.


##### `Carrier.read`  (lines 201–210)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how to stream a file out of the sandbox workspace in chunks. This avoids loading an entire large file into the host process at once.

**Data flow**: It receives a sandbox handle and an absolute workspace path. The implementation returns an asynchronous stream of byte chunks, or raises an appropriate error if the file cannot be read.

**Call relations**: `SandboxSession.read_file` calls this after path safety checks. Each backend provides its own streaming method while preserving the shared promise that missing files are reported consistently where possible.


##### `Carrier.dial`  (lines 212–221)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how outside code can reach a service listening on a port inside the sandbox. This is needed for things like browser debugging endpoints or preview servers started by a tool.

**Data flow**: It receives a sandbox handle and a port number. The implementation returns a `DialTarget` containing the host to contact, whether to use TLS-secured protocols, and any required headers, or raises `SandboxUnreachable` if no route exists.

**Call relations**: `SandboxSession.dial` passes requests through to this contract. Extensions such as the sandbox Chrome provider use it when they need to connect to a service running inside the sandbox.


##### `workspace_path`  (lines 224–232)

```
def workspace_path(path: str) -> str
```

**Purpose**: Turns a tool-supplied file path into a safe absolute path under `/workspace`. It blocks path tricks such as `..` that would try to escape the allowed workspace area.

**Data flow**: It receives a path string, makes it absolute relative to `/workspace` if needed, normalizes `.` and `..` parts, and checks that the result still sits under `/workspace`. It returns the safe path string or raises `ValueError` if the path escapes.

**Call relations**: File-facing session methods call this before reading, writing, checking, or passing a path to `sbxfs`. It delegates the step-by-step path cleanup to `_resolve_parts`.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 4 (file_exists, read_file, run_sbxfs, write_file); 1 external calls (PurePosixPath).


##### `_resolve_parts`  (lines 235–244)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Normalizes the pieces of a path while detecting attempts to walk above the workspace root. It is the small path-cleaning helper behind `workspace_path`.

**Data flow**: It receives the path parts produced by `PurePosixPath`. It builds a stack of real path parts, skips empty and current-directory markers, pops one part for `..`, and raises an error if `..` would climb too high. It returns the cleaned list of parts.

**Call relations**: `workspace_path` calls this whenever it checks a tool-supplied path. It is kept private because callers should use the full workspace safety check rather than this lower-level helper directly.

*Call graph*: called by 1 (workspace_path).


##### `SandboxSession.authorize`  (lines 256–281)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new session for the same sandbox but with the current turn’s network authority and environment. This keeps a long-lived container from accidentally using an older turn’s proxy token.

**Data flow**: It receives a new run token, a set of environment variable names to remove, and extra environment values to add. It checks that the existing handle has a token, rewrites proxy environment values from the old token to the new one, removes cleared variables, merges in the new environment, and returns a fresh `SandboxSession` with an updated handle.

**Call relations**: This method is used when the system scopes a shared sandbox to a particular turn. It does not create a new container; it rebuilds the session wrapper so later `exec`, file, and dial operations carry the right authority.

*Call graph*: 2 external calls (__init__, __init__).


##### `SandboxSession.bash`  (lines 283–288)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a shell command inside the sandbox using Bash. It is the simple entry point for tools or extensions that need normal command-line behavior in the workspace environment.

**Data flow**: It receives a command string and an optional timeout. It wraps the command as `bash -lc ...`, chooses the default timeout if none was provided, sends it to the carrier’s `exec`, and returns the resulting output, error text, and exit code.

**Call relations**: The sandbox Chrome extension calls this while leasing a browser debugging session. Internally it hands the real work to `Carrier.exec`.

*Call graph*: called by 1 (lease).


##### `SandboxSession.write_file`  (lines 290–291)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file in the sandbox workspace after making sure the path is safe. It is the normal copy-in path for adding files to a conversation’s sandbox.

**Data flow**: It receives a path and byte content. It converts the path to a checked `/workspace` path with `workspace_path`, then passes the safe path and bytes to the carrier’s `write` method. It returns nothing after the write completes.

**Call relations**: Skill-mounting code calls this to place skill files into the sandbox. The method combines local path safety with backend-specific writing supplied by the carrier.

*Call graph*: calls 1 internal fn (workspace_path); called by 1 (mount_skill).


##### `SandboxSession.ensure_tool_output_dir`  (lines 293–315)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine’s private `.tool-output` directory exists inside the workspace. If a file or broken link is squatting on that exact name, it removes that blocker and creates the directory.

**Data flow**: It runs a small shell script inside the sandbox against the fixed tool-output path. If the path is already a directory, nothing changes. If a non-directory object is there, it removes it, records that reclamation happened, and creates the directory. It returns `true` if something was reclaimed, or raises `OSError` if the command fails.

**Call relations**: This method prepares the sandbox for large tool outputs that are offloaded into a private directory. It uses `Carrier.exec` directly and is deliberately limited to the fixed engine-owned path, not a user-supplied path.


##### `SandboxSession.file_exists`  (lines 317–322)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists at a safe workspace path. It gives callers a simple yes-or-no answer without exposing wider filesystem access.

**Data flow**: It receives a path, converts it with `workspace_path`, and runs `test -f` inside the sandbox. If the command exits successfully, it returns `true`; otherwise it returns `false`.

**Call relations**: It uses the same path guard as other file methods and then delegates the actual check to `Carrier.exec`.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.run_sbxfs`  (lines 324–351)

```
async def run_sbxfs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs an in-sandbox file helper program named `sbxfs` and returns its JSON result. This lets heavy file work, such as searching or rendering, happen inside the sandbox instead of copying whole files to the host.

**Data flow**: It receives an operation name and a dictionary of arguments. If the arguments include a string path, it rewrites that path safely under `/workspace`. It serializes the arguments to JSON, runs `sbxfs` inside the sandbox, parses the command’s JSON output, and returns the parsed object. Missing output, invalid JSON, or a non-object response becomes a runtime error; a reported tool-level error becomes a `ValueError`.

**Call relations**: Tool code can use this as a compact bridge to sandbox-side file operations. It depends on `workspace_path` for path safety, JSON conversion for the command boundary, and `Carrier.exec` for actually running the helper.

*Call graph*: calls 1 internal fn (workspace_path); 2 external calls (dumps, loads).


##### `SandboxSession.read_file`  (lines 353–356)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox workspace after checking the path. It is the safe copy-out path for files produced by tools.

**Data flow**: It receives a path, converts it into a safe `/workspace` path, and returns the carrier’s asynchronous byte stream for that file. The caller consumes the chunks over time rather than receiving one giant byte string.

**Call relations**: This method is the session-level wrapper around `Carrier.read`. It adds the shared workspace boundary check before handing off to the backend-specific streaming implementation.

*Call graph*: calls 1 internal fn (workspace_path).


##### `SandboxSession.dial`  (lines 358–361)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Asks the carrier how to reach a port opened inside the sandbox. Callers use it when they need to connect from outside to something a tool started inside, such as a browser or preview server.

**Data flow**: It receives a port number and passes the session’s sandbox handle and that port to the carrier. It returns the resulting `DialTarget`, including address, TLS choice, and required headers.

**Call relations**: The sandbox Chrome extension calls this while leasing access to Chrome’s debugging port. The session does not invent routing details itself; it relies on the carrier’s knowledge of how that sandbox is exposed.

*Call graph*: called by 1 (lease).

## 📊 State Registers Touched

- `reg-effective-configuration` — The final startup settings that decide how the service, security, sandbox, proxy, and enabled packs should behave.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-usage-accounting-ledger` — The spending ledger that records model usage, egress usage, prices, caps, billing exports, and payment-related state.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-egress-network-state` — The controlled network exit state, including proxy configuration, certificates, allowed destinations, metering, and last-moment credential injection.
- `reg-browser-session-state` — The live browser-control session state used to click, read pages, download files, recover sessions, and route sandbox browser links.
- `reg-artifact-blob-store` — The shared file and blob store for generated artifacts, copied outputs, download records, and signed access links.
- `reg-observability-trace-state` — The shared logging, metrics, trace IDs, trace parents, and redaction context used to follow work across processes without leaking secrets.
- `reg-sandbox-image-cache` — The built or validated sandbox runtime image/backend artifact that later sandbox launches reuse.
- `reg-repl-scratchpad-state` — The Python and JavaScript REPL scratchpad sessions, files, and execution state kept for agent experimentation across tool calls or turns.
- `reg-turn-cleanup-callback-state` — The per-turn registry of cleanup callbacks and borrowed-resource finalizers that tools add during execution and completion/teardown later drains.
- `reg-web-metadata-store` — Persistent web metadata/cache records captured by web/search/browser-related extensions for later lookup, indexing, or display.
