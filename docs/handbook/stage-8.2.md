# Sandbox and Browser Session Preparation  `stage-8.2`

This stage prepares the safe workspace and browser access a tool needs before it starts doing work. The conversation workspace code is the front door to the private /workspace folder, so files can be opened, saved, listed, resumed, and cleaned without mixing conversations. The session code defines the common sandbox “doorway” for commands, files, skills, and network routing, while the selector chooses the carrier: local machine, Docker, E2B cloud sandbox, or a user’s connected terminal. Each carrier knows how to create or resume a workspace and run commands there.

The browser side uses a common browser contract so the rest of the system can ask for Chrome without caring where it lives. Chrome may run inside the sandbox, through Browserbase as a hosted browser, or through another backend. Proxy, cache, and preview settings keep network access on approved paths and avoid exposing real secrets. The execution environment adds only safe placeholder credentials. The client-binary helper finds the built UFO client to copy or run. File path and package setup pieces make these modules importable and keep all tool activity inside the intended sandbox boundary.

## Files in this stage

### Conversation Workspace Doorway
The conversation workspace layer opens, resumes, inspects, mutates, and cleans the private per-conversation `/workspace` area before tools use it.

### `core/src/ufo/harness/sandbox/conversation.py`

`orchestration` · `sandbox open, workspace reads/writes, and off-turn file operations`

A conversation’s workspace is the only durable copy of its files, so this file is careful about when it creates one and where it records it. Think of it like a checked-out storage locker: the database row holds the locker number, and every later process must use that same number instead of quietly renting a new locker.

The main class, `ConversationSandbox`, decides where the workspace lives. It may be on the normal sandbox carrier, on a resume backend that still owns an older sandbox, or inside a user-connected terminal. When opening a sandbox, it reads the stored handle from the database, creates or attaches to the right place, then saves the handle using a compare-and-swap update. That means “only save this if nobody changed it since I looked,” which prevents two callers from racing and ending up with two different official workspaces.

The file also protects read paths from side effects. Browsing or reading files calls `existing`, which only attaches to a workspace that already exists. It does not create a new one just because someone asked to list files. Writes are bounded by a size limit, pruning deletes old files safely inside the sandbox, and path checks stop symlinks or strange paths from escaping the intended workspace root.

#### Function details

##### `ConversationSandbox._route`  (lines 105–116)

```
def _route(self, stored: str | None) -> tuple[Carrier, str, bool]
```

**Purpose**: Chooses which sandbox carrier should serve a stored workspace handle. This matters because an old conversation may still belong to a different backend, and opening it on the wrong backend would strand or overwrite its files.

**Data flow**: It receives a stored handle, if there is one. It reads the backend name embedded in that handle and checks whether a configured resume backend owns that name. It returns the carrier to use, the backend name to record, and whether that backend is off-cluster; if nothing special matches, it returns the deployment’s default carrier settings.

**Call relations**: When `open` needs to create or resume a sandbox, `_opened` asks `_route` where to do it. When `existing` tries to attach for a read-only operation, it also asks `_route` so reads follow the same stored backend choice without creating anything new.

*Call graph*: called by 2 (_opened, existing); 1 external calls (sandbox_handle_backend).


##### `ConversationSandbox.open`  (lines 118–170)

```
async def open(self, conversation_id: UUID, turn_id: UUID | None, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Opens the conversation’s sandbox for active use and makes sure the database records the exact sandbox handle that won. This is the main create-or-resume path used by turns and by off-turn writers.

**Data flow**: It starts with a conversation id, optional turn id, run token, and environment variables. It reads the current stored sandbox handle and the requested sandbox size, asks `_opened` to create or resume a sandbox, then tries to save the resulting handle only if the database still contains what it first read. It returns a `SandboxSession`, which is the usable connection to that workspace, or raises an error if the handle keeps changing during repeated attempts.

**Call relations**: Workspace writes call `open` because writes are allowed to create the workspace if needed. The runtime queue also calls it when a turn needs a sandbox. Inside, it relies on `_binding` to read the database, `_opened` to reach the carrier, and `_claim` to settle races between simultaneous open attempts.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 3 (write, write_runtime, _open_sandbox); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 172–234)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Attaches to a conversation’s sandbox only if one already exists and is reachable. It is the safe read path: asking to browse or read files must not create a new workspace as a side effect.

**Data flow**: It takes a conversation id and reads the stored sandbox handle. If there is no handle, an unknown backend, a missing local directory, or an unreachable carrier, it returns `None`. If it can attach, it returns a `SandboxSession` using an unsigned off-turn token so file-only operations do not get network access through a real run token.

**Call relations**: `entries`, `read`, `prune`, and `prune_runtime` all call `existing` before touching files. It uses `_stored` to read the saved handle, `_route` for non-terminal backends, and carrier attach methods to reconnect without provisioning a new sandbox.

*Call graph*: calls 2 internal fn (_route, _stored); called by 4 (entries, prune, prune_runtime, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 236–246)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a not-yet-bound conversation to the currently connected user terminal at a given working directory. This lets a future first sandbox open use the user’s terminal workspace even if the actual turn starts a little later.

**Data flow**: It receives a conversation id and a terminal current working directory. It checks the database for an existing sandbox handle; if one already exists, it leaves it alone and returns `False`. If the conversation is unbound, it tries to store a `client:` handle for that directory and returns whether this call won the claim.

**Call relations**: Admission code calls this while a member’s terminal connection is live. It uses `_stored` to avoid overriding an existing binding and `_claim` to make the database update race-safe.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 248–259)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a user-visible file inside the conversation’s `/workspace`. It is used when something outside the turn, such as an inbound attachment, needs to place a file where the agent can later read it.

**Data flow**: It receives a conversation id, a relative path, and file contents. It first rejects content over the configured 100 MB limit, opens the sandbox off-turn if needed, writes the file through the session, and returns the path as it will appear inside `/workspace`.

**Call relations**: `write` calls `open` because writing is allowed to create the workspace. After the session writes the file, it uses `workspace_path` to report the container-facing path back to the caller.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.write_runtime`  (lines 261–273)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes bounded internal output into a runtime-specific area rather than the member-visible workspace root. It is for system-produced files grouped by category.

**Data flow**: It receives a conversation id, a runtime category, a relative path, and bytes. It checks the same size limit, opens the sandbox off-turn, writes the content under `category/rel`, and returns a display path for that runtime file.

**Call relations**: Like `write`, it calls `open` because internal writes may need to create the sandbox. It then hands the actual file placement and display-path calculation to the returned `SandboxSession`.

*Call graph*: calls 1 internal fn (open).


##### `ConversationSandbox.prune`  (lines 275–286)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older user-visible files under a workspace prefix, keeping only a chosen number of newest files. This prevents unattended append-style jobs from growing the workspace forever.

**Data flow**: It receives a conversation id, a relative prefix, and a keep count. It attaches only if the sandbox already exists, then runs a small Python pruning program inside the sandbox so file age and paths match what the agent sees. If the pruning program fails, it raises an `OSError` with the error text.

**Call relations**: `prune` calls `existing`, so it never creates a workspace just to delete files. It uses `workspace_path` to point the in-sandbox pruning script at the right `/workspace` location.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.prune_runtime`  (lines 288–300)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older internal runtime files under one category, keeping only the newest requested number. It is the runtime-area version of `prune`.

**Data flow**: It receives a conversation id, category, relative prefix, and keep count. It attaches to an existing sandbox, converts the runtime-relative paths into actual sandbox paths, runs the pruning program, and raises an `OSError` if that program reports failure.

**Call relations**: It depends on `existing` for the no-side-effect attach behavior. After that, it asks the `SandboxSession` for runtime paths and runs the same pruning script used by `prune`.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox.entries`  (lines 302–341)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists the member-visible files in a conversation’s workspace. It returns clean, relative file information suitable for a file browser.

**Data flow**: It receives a conversation id and attaches only if a sandbox already exists. It asks the sandbox to run `ufo fs glob`, excluding names like `.git`, checks that the result is a file list, warns if the list was truncated, converts each returned absolute path into a workspace-relative path, and returns sorted `WorkspaceFile` records with size and modification time.

**Call relations**: File browsing calls `entries`, and `entries` calls `existing` so browsing does not create a workspace. For each returned file it calls `_workspace_rel` to remove the container or host workspace root from the path before exposing it.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 343–347)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns a path returned by the sandbox file walker into a path relative to the workspace root. This keeps the file browser from exposing container or host absolute paths.

**Data flow**: It receives a sandbox handle and a path string. It checks whether the path starts under `/workspace` or under the handle’s host workspace path, strips that root, and returns the remaining relative path. If the path is not under either allowed root, it raises an error.

**Call relations**: `entries` calls this for every listed file. It acts as a final safety check that the file walker did not report something outside the workspace.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 349–357)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Reads one file from the conversation workspace in chunks, or reports that it is unavailable. It is designed for safe downloads and previews without creating a workspace.

**Data flow**: It receives a conversation id and a relative file path. It attaches to an existing sandbox, checks whether the file exists, and if so returns an async byte iterator that streams the file contents. If there is no sandbox or no such file, it returns `None`.

**Call relations**: `read` relies on `existing`, so a read request is side-effect-free. Once a session exists, the session performs the actual file existence check and streaming.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 359–418)

```
async def _opened(self, conversation_id: UUID, turn_id: UUID | None, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Performs the actual choice and creation or resumption of a sandbox during `open`. It is the central decision point for terminal-bound conversations, resumed backends, and the default carrier.

**Data flow**: It receives the conversation id, turn id, stored handle, run token, environment, and requested size. It first checks whether the conversation is already bound to a terminal, or whether a fresh conversation currently has a terminal workspace available. If so, it creates through a terminal carrier. Otherwise it routes to the proper backend, prepares or resolves the host workspace directory as needed, creates or resumes through that carrier, and returns the backend name, carrier, and handle.

**Call relations**: `open` calls `_opened` after reading the database. `_opened` may call `_route` for backend selection, may build a `TerminalCarrier` for `client:` workspaces, and creates a `SandboxSpec` to hand all launch details to the carrier.

*Call graph*: calls 1 internal fn (_route); called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 420–435)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates and verifies the host directory used as the conversation’s workspace for local in-cluster sandboxes. It is careful to avoid unsafe symbolic-link tricks that could point the sandbox at the wrong place.

**Data flow**: It receives a conversation id. It ensures the configured workspace root exists, resolves and validates that root, then creates or opens the conversation-specific directory under it using containment checks. It returns the safe directory path.

**Call relations**: `_opened` calls this when a non-off-cluster carrier needs a host workspace directory. It relies on containment helpers to prove that the created directory stays inside the configured workspace root before the carrier mounts it.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 437–448)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds the existing host workspace directory for read-only operations without creating it. Missing means the conversation has no local workspace to browse.

**Data flow**: It receives a conversation id. It validates the configured workspace root and tries to locate the conversation directory inside it. If the directory is absent, it returns `None`; if the path is unsafe, the containment helper raises an error.

**Call relations**: `existing` calls this for local non-off-cluster sandboxes. This preserves the rule that reads may attach to a real workspace but must not provision a new directory.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 450–452)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Reads just the stored sandbox handle for a conversation. It is a small convenience wrapper around the fuller database binding read.

**Data flow**: It receives a conversation id, calls `_binding`, discards the sandbox size, and returns the stored handle or `None`.

**Call relations**: `existing`, `claim_terminal`, and `_claim` call `_stored` when they only need to know what handle the conversation row currently contains.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 454–475)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the conversation’s current sandbox handle and its agent’s sandbox size from the database. This gives an open operation both the old handle to resume and the size to use if it must create a sandbox.

**Data flow**: It receives a conversation id. Inside a workspace-scoped database transaction, it joins the conversation row to its agent row, limited to the current workspace. It returns the sandbox handle and sandbox size, or raises a `ValueError` if the conversation is not part of the current workspace.

**Call relations**: `open` calls `_binding` at the start of the create-or-resume flow. `_stored` also calls it when other paths only need the handle.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 477–498)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Safely writes a sandbox handle to the database only if the row still contains the value the caller originally saw. This is the race-control step that prevents two simultaneous opens from both becoming official.

**Data flow**: It receives a conversation id, the previously observed handle, and the new handle to store. It runs a conditional database update: set the handle only if the current row still matches the old value, including `None` for an unbound conversation. If the update succeeds, it returns the new handle; if another caller won first, it reads and returns that winning handle.

**Call relations**: `open` uses `_claim` after creating or resuming a sandbox so all concurrent callers converge on one stored handle. `claim_terminal` also uses it to bind an unclaimed conversation to a terminal without overwriting an existing binding.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### Sandbox Chrome Lease
The sandbox Chrome extension starts or reuses a Chrome instance inside the active sandbox and exposes its debugging connection to the rest of the system.

### `extensions/sandbox_chrome/ufo_ext_sandbox_chrome.py`

`io_transport` · `per-turn browser lease and sandbox browser startup`

This file is the bridge between UFO’s browser engine and a Chrome process living inside a sandbox. A sandbox is an isolated workspace for one conversation, like a sealed room where files and programs can run without touching the host machine. Running Chrome there matters because the browser can directly see the same files and downloads as the task, instead of copying everything back and forth.

Chrome exposes a control channel called CDP, short for Chrome DevTools Protocol. It is the same kind of connection developer tools use to inspect and drive a browser. The tricky part is that Chrome rejects some remote requests unless they look like they came from localhost. To solve that, this file also writes and starts a tiny proxy inside the sandbox. The proxy listens on one port, rewrites the HTTP Host header so Chrome accepts it, and passes the WebSocket traffic through unchanged.

The main flow is: when a lease is requested, the provider runs a setup command in the sandbox. That command checks whether Chrome and the proxy are already answering. If not, it starts them, waits for them to become ready, and returns Chrome’s WebSocket debugger path. The provider then asks the sandbox how the outside process should dial the proxy and builds the final endpoint. Chrome and the proxy stay alive across turns, so closing a lease does not stop them.

#### Function details

##### `SandboxChromeCdpLease.endpoint`  (lines 307–308)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the ready-to-use browser control endpoint for the current lease. Other code uses it to connect to Chrome through the sandbox proxy.

**Data flow**: It reads the endpoint already stored in the lease and returns it unchanged. Nothing is started, stopped, or modified.

**Call relations**: After SandboxChromeCdpProvider.lease has started or found the sandbox browser and built the endpoint, callers ask this lease for that endpoint so the browser engine can connect.


##### `SandboxChromeCdpLease.token`  (lines 310–311)

```
async def token(self) -> str
```

**Purpose**: This returns a simple string that represents the lease: the endpoint URL. It is a durable-looking handle, but in this provider it is not enough to reconnect later by itself.

**Data flow**: It reads the URL from the stored endpoint and returns that URL as text. It does not contact the sandbox or check whether the browser is still alive.

**Call relations**: Code that wants a lease token can call this after receiving a SandboxChromeCdpLease. If recovery later tries to use the token, SandboxChromeCdpProvider.reattach refuses and asks the system to make a fresh lease instead.


##### `SandboxChromeCdpLease.place_file`  (lines 313–317)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: This tells the caller that no file upload or copying is needed. Because Chrome is already inside the sandbox, it can open the sandbox path directly.

**Data flow**: It receives a path and a file-reading callback. It ignores the reader because there is no need to move bytes, then returns the same path it was given.

**Call relations**: When browser-driving code wants to make a file available to Chrome, it can call this method. For this provider, the answer is simply: use the path you already have inside the sandbox.


##### `SandboxChromeCdpLease.download_dir`  (lines 319–322)

```
async def download_dir(self) -> str
```

**Purpose**: This returns the folder inside the sandbox where Chrome saves downloads. Browser automation uses this to know where completed download files will appear.

**Data flow**: It returns the fixed sandbox download directory path. It does not create the directory here; the browser bring-up command prepares it earlier.

**Call relations**: After a lease is active, download-related code asks this method where Chrome writes files. SandboxChromeCdpLease.fetch_download later reads completed downloads back from that same area.


##### `SandboxChromeCdpLease.fetch_download`  (lines 324–352)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: This reads a downloaded file out of the sandbox and returns its bytes to the caller. It also protects the host from unexpectedly huge downloads by checking the file size first.

**Data flow**: It receives a download identifier, treats that as the filename in Chrome’s sandbox download folder, and quotes it so the shell reads the intended path safely. It asks the sandbox for the file size, rejects the file if it is too large, then runs a sandbox command that base64-encodes the file. Back in this process, it decodes that text into raw bytes and returns them. If the file cannot be read, takes too long, or is too big, it raises an error instead.

**Call relations**: Download-handling code calls this after Chrome has saved a file. The method uses the sandbox’s command runner to inspect and read the file, uses shlex.quote to make the filename safe in a shell command, and uses asyncio.to_thread so base64 decoding of a whole file does not block the main async event loop.

*Call graph*: 2 external calls (to_thread, quote).


##### `SandboxChromeCdpLease.aclose`  (lines 354–355)

```
async def aclose(self) -> None
```

**Purpose**: This closes the lease from the caller’s point of view, but deliberately does not stop Chrome. Chrome and its proxy are meant to live as long as the conversation sandbox lives.

**Data flow**: It receives no useful input and returns nothing. It makes no changes to the browser, proxy, sandbox, or endpoint.

**Call relations**: The wider lease system can call this during cleanup at the end of a turn. For this provider, cleanup is a no-op because the next turn should be able to reuse the already-running sandbox browser.


##### `SandboxChromeCdpProvider.lease`  (lines 367–384)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: This is the main entry point for getting a usable Chrome DevTools connection inside a sandbox. It starts or reuses Chrome and the proxy, proves they are reachable, and returns a lease containing the final endpoint.

**Data flow**: It expects the current turn’s sandbox. If no sandbox is supplied, it raises an error because this provider cannot work without one. It runs the browser bring-up script inside the sandbox. If that fails, it asks _bring_up_failure to produce a useful error message. If it succeeds, it asks the sandbox how to dial the proxy port from outside, chooses secure WebSocket or plain WebSocket based on that answer, extracts the path from Chrome’s local WebSocket URL with _ws_path, combines the pieces into a CdpEndpoint, and returns a SandboxChromeCdpLease containing that endpoint and the sandbox.

**Call relations**: The core browser-provider system calls this when a turn needs browser access. This method hands work to the sandbox for command execution and port dialing, calls _bring_up_failure only on setup failure, calls _ws_path to adapt Chrome’s local URL to the externally reachable proxy address, and finally constructs the lease used by the browser engine.

*Call graph*: calls 4 internal fn (bash, dial, _bring_up_failure, _ws_path); 2 external calls (__init__, __init__).


##### `SandboxChromeCdpProvider.reattach`  (lines 386–387)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This rejects attempts to reconnect from an old token alone. The provider requires a live sandbox at lease time, so a stored URL is not enough to safely recover the session.

**Data flow**: It receives a token string and immediately raises SessionGone with that token. It does not inspect the sandbox or try to open a browser connection.

**Call relations**: Recovery code may call this when it has a previous lease token. This provider tells that recovery path the old session should be considered gone, so the caller must request a fresh lease instead.

*Call graph*: 1 external calls (__init__).


##### `_bring_up_failure`  (lines 390–404)

```
async def _bring_up_failure(sandbox: Sandbox, result: ExecResult) -> str
```

**Purpose**: This turns a failed browser setup into a clearer error message. It is especially useful when the sandbox command was killed by a timeout and the normal program output may have been lost.

**Data flow**: It receives the sandbox and the failed command result. If the command failed normally, it returns the command’s stderr or stdout text. If the command timed out, it runs a short sandbox command to read the tail ends of the Chrome and proxy logs, then returns a message that includes the timeout and any log text it found.

**Call relations**: SandboxChromeCdpProvider.lease calls this only when the bring-up command fails. The helper goes back into the sandbox to collect logs so the final exception can explain what happened to Chrome or the proxy instead of only saying that the outer command timed out.

*Call graph*: calls 1 internal fn (bash); called by 1 (lease).


##### `_ws_path`  (lines 407–412)

```
def _ws_path(url: str) -> str
```

**Purpose**: This extracts the path part of Chrome’s WebSocket debugger URL and verifies that Chrome reported a local address. That lets the provider replace Chrome’s internal host with the sandbox’s externally reachable proxy host.

**Data flow**: It receives a URL string from Chrome, trims whitespace, and checks whether it starts with one of the expected local Chrome prefixes. If it does, it removes that prefix and returns the remaining path. If it does not, it raises an error because the provider only trusts local Chrome debugger URLs here.

**Call relations**: SandboxChromeCdpProvider.lease calls this after the bring-up script prints Chrome’s debugger URL. The returned path is joined with the host and headers provided by the sandbox dial result to form the endpoint that outside browser-driving code will use.

*Call graph*: called by 1 (lease).


##### `manifest`  (lines 415–424)

```
def manifest() -> Manifest
```

**Purpose**: This describes the extension to the UFO plugin system. It says the extension provides a CDP browser backend named sandbox_chrome and tells the system how to build it.

**Data flow**: It creates a provider specification whose build function returns a SandboxChromeCdpProvider, then wraps that in a Manifest with the extension name and version. The manifest object is returned to whoever is loading extensions.

**Call relations**: Extension-loading code calls this to discover what this file offers. The returned manifest registers the sandbox_chrome backend so configuration can select it and later ask it for browser leases.

*Call graph*: 2 external calls (__init__, __init__).


### Local and Terminal Sandboxes
These backends provide host-machine and user-terminal implementations of sandbox command execution, file access, path rewriting, and proxy-aware networking.

### `core/src/ufo/harness/sandbox/local.py`

`io_transport` · `cross-cutting: sandbox creation, command execution, file access, and skill loading`

The local carrier is the simplest way to run UFO’s sandbox features during development. Instead of starting a container, it treats the conversation’s workspace as an ordinary folder on the host computer. When a tool asks to run a command in `/workspace`, this file turns that into the real folder path and starts a normal host subprocess there.

This is convenient, but it is not a security wall. The code carefully checks file paths before reads and writes, so tool file operations stay inside allowed roots. But the operating system is not confining the process the way a container would. A command can still behave like a host command.

The file also builds a clean environment for commands. It avoids inheriting the server’s secrets, sets a scratch home directory, puts the `ufo` helper binary on `PATH`, disables risky Git credential prompts, and sends web traffic through a local proxy with special placeholder model API keys. That keeps network metering and key swapping working the same way as in a container.

It also stores and loads “skills,” which are bundles of reusable files. System skills are unpacked from a trusted archive, while user skills are checked by name and digest before installation. Overall, this file is the bridge between UFO’s sandbox interface and plain local files, commands, and ports.

#### Function details

##### `_provision_scratch`  (lines 78–102)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates one temporary support area for the whole running server process. This area holds a scratch home directory and, when available, a copy of the `ufo` client binary used by local sandbox commands.

**Data flow**: It starts with no input. It creates a temporary folder with `home` and `bin` inside it, tries to find the built `ufo` client binary, copies it into `bin`, marks it executable, and returns the scratch folder path. If the binary is missing, it logs a warning and still returns the scratch folder so other local sandbox work can continue.

**Call relations**: This is used as the default factory for `LocalCarrier._scratch`, so it runs when a local carrier is first constructed. Later methods such as `LocalCarrier._base_env` depend on the folder it creates to provide `HOME`, `UFO_HOME`, and a command `PATH`.

*Call graph*: 4 external calls (Path, mkdtemp, warn, client_binary).


##### `LocalCarrier.ufo_home`  (lines 110–112)

```
def ufo_home(self) -> Path
```

**Purpose**: Gives the local carrier’s private UFO home directory. This is where local runtime data, such as installed skills, is stored away from the real user’s home folder.

**Data flow**: It reads the carrier’s scratch folder path and appends `home/.ufo`. The result is a filesystem path; it does not create the directory by itself.

**Call relations**: Many methods use this property as their shared base location. Skill seeding, skill loading, sandbox creation, and attachment all use it so local sandbox state stays in the scratch area rather than leaking into the host user’s normal configuration.


##### `LocalCarrier.seed_system_skills`  (lines 114–142)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Installs trusted built-in skills from a zip archive into the local carrier’s UFO home. It replaces older versions cleanly so the local runtime has the expected system skill files.

**Data flow**: It receives archive bytes. It opens the zip, reads and validates `manifest.json`, compares old and new skill names, removes affected top-level skill folders, writes each archived file under the skills directory using path-safety checks, and finally writes the new saved manifest.

**Call relations**: This prepares the skill store that `LocalCarrier._load_skills` later reads. It calls `LocalCarrier._system_manifest` to see what was previously installed and uses containment helpers whenever it removes or writes files so archive paths cannot escape the skills directory.

*Call graph*: calls 1 internal fn (_system_manifest); 7 external calls (BytesIO, loads, Path, contained_file, contained_relative, contained_remove, ZipFile).


##### `LocalCarrier.load_skills`  (lines 144–154)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asynchronously loads the skills requested for a sandbox turn and returns a command-style result. It turns success or failure into an `ExecResult`, the same shape used for command execution.

**Data flow**: It receives a sandbox handle and a payload describing system and user skills. It runs the blocking work in a background thread, catches validation or filesystem errors, and returns either JSON listing the resolved skill roots or an error message with exit code 1.

**Call relations**: This is the async public entry for skill loading. It delegates the real checking and installation to `LocalCarrier._load_skills`, then packages that result for the rest of the sandbox machinery.

*Call graph*: 3 external calls (__init__, to_thread, dumps).


##### `LocalCarrier._load_skills`  (lines 156–179)

```
def _load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Resolves requested system skills and installs requested user skills. It produces a map from skill name to the local folder where that skill can be used.

**Data flow**: It reads the skills root and saved system manifest, then reads `system` and `user` entries from the payload. For each system skill, it verifies the manifest and digest; for each user skill, it validates, decodes, checks, and installs files. It returns a dictionary of usable skill roots.

**Call relations**: It is called by `LocalCarrier.load_skills` in a worker thread. It hands system entries to `LocalCarrier._load_system_skill` and user entries to `LocalCarrier._load_user_skill`, using `LocalCarrier._system_manifest` as the source of truth for trusted built-in skills.

*Call graph*: calls 3 internal fn (_load_system_skill, _load_user_skill, _system_manifest).


##### `LocalCarrier._load_system_skill`  (lines 181–200)

```
def _load_system_skill(self, root: Path, manifest_skills: Mapping[object, object], name: object, digest: object) -> tuple[str, str] | None
```

**Purpose**: Checks whether a requested built-in skill is present and exactly matches its expected digest. If it is valid, it returns where that skill lives on disk.

**Data flow**: It receives the skills root, the manifest’s skill section, a skill name, and a digest. It rejects malformed inputs, ensures the name stays inside the skills root, compares the manifest digest, reads the listed files, recomputes their digest, and returns the skill name plus root path if everything matches. If the skill is missing or stale, it returns `None`.

**Call relations**: This is called while `LocalCarrier._load_skills` is processing requested system skills. It uses `LocalCarrier._read_skill_files` to collect file contents and `LocalCarrier._skill_digest` to prove the files match the requested version.

*Call graph*: calls 2 internal fn (_read_skill_files, _skill_digest); called by 1 (_load_skills); 1 external calls (contained_relative).


##### `LocalCarrier._load_user_skill`  (lines 202–235)

```
def _load_user_skill(self, root: Path, system_names: tuple[object, ...], name: object, encoded: object) -> tuple[str, str]
```

**Purpose**: Validates and installs a user-provided skill bundle. It protects system skill names from being overwritten and checks that the provided file contents match the declared digest.

**Data flow**: It receives the skills root, existing system skill names, a user skill name, and encoded skill data. It validates the name, rejects conflicts with system skills, decodes each base64 file body, checks paths stay inside the skill, sorts the file list, verifies the digest, writes the files into place, and returns the installed skill name and path.

**Call relations**: This is called by `LocalCarrier._load_skills` for each user skill. It relies on `LocalCarrier._validate_user_skill_name`, `LocalCarrier._skill_digest`, and `LocalCarrier._install_user_skill` to break the job into name checking, integrity checking, and disk installation.

*Call graph*: calls 3 internal fn (_install_user_skill, _skill_digest, _validate_user_skill_name); called by 1 (_load_skills); 2 external calls (urlsafe_b64decode, contained_relative).


##### `LocalCarrier._system_manifest`  (lines 238–246)

```
def _system_manifest(root: Path) -> Mapping[str, object]
```

**Purpose**: Reads the saved manifest for installed system skills. If no manifest exists yet, it treats the system as having no installed skills.

**Data flow**: It receives the skills root path. It safely opens `.system-manifest.json` inside that root, returns `{"skills": {}}` if the file is absent, otherwise parses JSON and verifies that the top-level value is a mapping.

**Call relations**: Both `LocalCarrier.seed_system_skills` and `LocalCarrier._load_skills` call this. It is the shared way to know which built-in skills are supposed to exist and what their expected metadata is.

*Call graph*: called by 2 (_load_skills, seed_system_skills); 2 external calls (load, contained_file).


##### `LocalCarrier._read_skill_files`  (lines 249–256)

```
def _read_skill_files(root: Path, name: str, files: list[str]) -> list[tuple[str, bytes]]
```

**Purpose**: Reads the files that belong to one system skill so their contents can be checked. It keeps every read inside the skills directory.

**Data flow**: It receives the skills root, a skill name, and a list of relative file paths. For each file, it builds a contained path, opens the file safely, reads its bytes, and returns a list of `(path, bytes)` pairs.

**Call relations**: This is called by `LocalCarrier._load_system_skill` before digest verification. It supplies the exact file contents that `LocalCarrier._skill_digest` checks.

*Call graph*: called by 1 (_load_system_skill); 2 external calls (contained_file, contained_relative).


##### `LocalCarrier._install_user_skill`  (lines 259–265)

```
def _install_user_skill(root: Path, name: str, files: list[tuple[str, bytes]]) -> None
```

**Purpose**: Writes a verified user skill into the local skills directory. It removes any previous version of that user skill first so old files do not linger.

**Data flow**: It receives the skills root, the user skill name, and verified file contents. It removes the existing destination folder, then writes each file safely under the skill folder with normal readable file permissions.

**Call relations**: This is called only after `LocalCarrier._load_user_skill` has validated the name and digest. It uses containment helpers to ensure installation cannot write outside the skills root.

*Call graph*: called by 1 (_load_user_skill); 3 external calls (contained_file, contained_relative, contained_remove).


##### `LocalCarrier._validate_user_skill_name`  (lines 268–271)

```
def _validate_user_skill_name(name: str, root: Path) -> None
```

**Purpose**: Checks that a user skill name is a simple, safe top-level folder name. This prevents hidden names and nested paths from being used as skill names.

**Data flow**: It receives a skill name and the skills root. It converts the name into a contained relative path and then verifies it has exactly one path part and does not start with a dot. If not, it raises an error.

**Call relations**: This is called early by `LocalCarrier._load_user_skill`. It blocks unsafe names before any digest checking or file installation happens.

*Call graph*: called by 1 (_load_user_skill); 2 external calls (Path, contained_relative).


##### `LocalCarrier._skill_digest`  (lines 274–279)

```
def _skill_digest(files: list[tuple[str, bytes]]) -> str
```

**Purpose**: Computes the stable fingerprint for a set of skill files. This fingerprint proves that both file names and file contents are exactly what was expected.

**Data flow**: It receives a list of `(path, bytes)` pairs. For each pair, it hashes the path and the content, feeds those hashes into a combined SHA-256 hash, and returns a string like `sha256:<hex value>`.

**Call relations**: Both `LocalCarrier._load_system_skill` and `LocalCarrier._load_user_skill` call this before accepting a skill. It is the shared integrity check for built-in and user-provided skills.

*Call graph*: called by 2 (_load_system_skill, _load_user_skill); 1 external calls (sha256).


##### `LocalCarrier.create`  (lines 281–315)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or prepares a local sandbox handle for a conversation. It makes the workspace and runtime directories and builds the environment commands will later inherit.

**Data flow**: It receives a `SandboxSpec`, which includes the workspace path, conversation id, proxy details, run token, and extra environment variables. It creates the host workspace folder, creates a per-conversation runtime folder under `UFO_HOME`, writes the proxy certificate, builds proxy-related environment variables, and returns a `SandboxHandle` describing the local sandbox.

**Call relations**: This is the main setup path for a new local sandbox. It calls `LocalCarrier._base_env` for the safe base command environment, then adds proxy settings, sentinel API keys, certificate paths, and spec-provided environment values.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier._base_env`  (lines 317–340)

```
def _base_env(self) -> dict[str, str]
```

**Purpose**: Builds the safe default environment for every local sandbox command. It deliberately does not copy the server’s full environment, because that could expose deployment secrets to a subprocess.

**Data flow**: It reads only a small allowlist of host environment variables, such as locale and temporary directory settings. It then adds a scratch `HOME`, `UFO_HOME`, a `PATH` containing the local `ufo` binary, and Git settings that prevent host credential helpers or interactive prompts from hanging or leaking data. It returns a dictionary of environment variables.

**Call relations**: Both `LocalCarrier.create` and `LocalCarrier.attach` call this when preparing a `SandboxHandle`. `LocalCarrier.exec` later passes the handle’s environment to the subprocess it starts.

*Call graph*: called by 2 (attach, create); 1 external calls (Path).


##### `LocalCarrier.attach`  (lines 342–356)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reopens an existing local workspace without creating it. This is useful for read-only browsing of a past conversation, where a missing workspace should simply mean there is nothing to attach to.

**Data flow**: It receives a `SandboxSpec`, checks whether the workspace host path is an existing directory, and returns `None` if it is not. If it exists, it builds a `SandboxHandle` with the workspace path, runtime root, run token, and base environment.

**Call relations**: This mirrors `LocalCarrier.create` but skips directory creation and proxy setup. It calls `LocalCarrier._base_env` so file tools still run with the scratch `ufo` command available.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 358–407)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one sandbox command as a local host subprocess in the workspace directory. It enforces timeouts and cleans up the whole process group if the command must be stopped.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It finds the host workspace root, rewrites logical `/workspace` arguments to host paths, starts the subprocess with the handle’s environment, waits for output, and returns stdout, stderr, and exit code. If the command times out, it kills the process group and returns a timeout result.

**Call relations**: This is the local carrier’s command execution path. It uses `_root` to locate the workspace, `host_argv` to rewrite paths, and `_kill_process_group` when a timeout or cancellation means child processes must not be left running.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 4 external calls (__init__, create_subprocess_exec, wait_for, host_argv).


##### `LocalCarrier.write`  (lines 409–432)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the local sandbox workspace. It uses a background thread because normal filesystem writes would otherwise block the async event loop.

**Data flow**: It receives a sandbox handle, a sandbox path, and bytes to write. It sends the actual write work to `LocalCarrier._write_contained` in a thread and returns when the write has finished.

**Call relations**: This is the async public copy-in method. It delegates the safety-sensitive path and file replacement work to `LocalCarrier._write_contained`.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 434–437)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the actual safe write into an allowed sandbox root. It makes sure the target path belongs to the workspace or runtime root before replacing the file contents.

**Data flow**: It receives a handle, path, and content bytes. It converts the sandbox path into a contained relative name and root, opens the target through containment checks, and atomically replaces its bytes while preserving appropriate write mode details.

**Call relations**: This is called by `LocalCarrier.write` in a worker thread. It relies on `_contained_name` to choose the correct allowed root and on containment file helpers to avoid symlink and path-escape problems.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 439–450)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the local sandbox workspace or runtime root. It reads in chunks so large files do not have to be loaded into memory all at once.

**Data flow**: It receives a sandbox handle and sandbox path. It opens the source file safely in a worker thread, repeatedly reads chunks of up to one megabyte, yields each chunk, and closes the file when finished or interrupted.

**Call relations**: This is the async public copy-out method. It calls `LocalCarrier._contained_source` to safely open the file first, then performs blocking reads through `asyncio.to_thread`.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 452–462)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Safely opens a file for reading from an allowed sandbox root. It turns containment-path failures into ordinary file-not-found errors for callers.

**Data flow**: It receives a handle and path. It resolves the path to an allowed root, checks that the target exists through the containment wrapper, opens it as bytes, and returns the open buffered reader. If the path cannot be found safely, it raises `FileNotFoundError`.

**Call relations**: This is called by `LocalCarrier.read` before streaming begins. It uses `_contained_name` to decide whether the path belongs under `/workspace` or the runtime root, and containment helpers to avoid following unsafe links.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 464–469)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level file operation using the local `ufo fs` helper. This lets local mode reuse the same file-tool behavior as other sandbox carriers.

**Data flow**: It receives a handle, an operation name, and operation parameters. It passes those to `ufo_fs_file_op`, which runs the appropriate `ufo fs` command against the workspace, and returns the resulting dictionary.

**Call relations**: This is the local carrier’s bridge from abstract file operations to the `ufo` command-line helper. It depends on earlier setup from `_provision_scratch` and `_base_env`, which put the helper binary on `PATH`.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `LocalCarrier.dial`  (lines 471–477)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns the address to reach a server started by a local sandbox command. Because local commands share the host network, the sandbox port is just the same port on `127.0.0.1`.

**Data flow**: It receives a handle and port number. It builds and returns a `DialTarget` with host `127.0.0.1:<port>` and `tls` set to false.

**Call relations**: This is used when the system wants to connect to something running inside the sandbox. Unlike container carriers, it does not need port mapping; it simply points callers at the host loopback address.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 480–485)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Force-stops a command and all child processes in its process group. This prevents timed-out or cancelled local commands from leaving runaway background work behind.

**Data flow**: It receives an asyncio subprocess object. It sends `SIGKILL` to the process group identified by the process id, ignores the case where the process is already gone, and waits for the process to finish cleaning up.

**Call relations**: This is called by `LocalCarrier.exec` when a subprocess times out or the exec operation is interrupted. It is the cleanup tool that makes local command execution safer for the host machine.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 488–491)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the host directory that backs the sandbox workspace. It raises an error if a local sandbox handle does not have such a directory.

**Data flow**: It receives a sandbox handle. It checks `workspace_host_path`; if present, it converts it to a `Path` and returns it, otherwise it raises a runtime error explaining that local mode needs a host directory.

**Call relations**: This helper is called by `LocalCarrier.exec` to choose the subprocess working directory and by `_contained_name` when resolving `/workspace` paths.

*Call graph*: called by 2 (exec, _contained_name); 1 external calls (Path).


##### `_contained_name`  (lines 494–500)

```
def _contained_name(handle: SandboxHandle, path: str) -> tuple[PurePosixPath, Path]
```

**Purpose**: Translates a sandbox path into a safe relative path plus the host root it belongs under. It accepts paths under `/workspace` and, when present, the runtime root, and rejects everything else.

**Data flow**: It receives a sandbox handle and a path string. It treats the path as a POSIX-style sandbox path, checks whether it is inside `/workspace` or inside the handle’s runtime root, and returns the relative path together with the matching host root. If the path is outside both allowed roots, it raises a value error.

**Call relations**: This is used by `LocalCarrier._write_contained` and `LocalCarrier._contained_source` before any local disk access. It calls `_root` for workspace paths, making it the central path gate for reads and writes.

*Call graph*: calls 1 internal fn (_root); called by 2 (_contained_source, _write_contained); 2 external calls (Path, PurePosixPath).


### `core/src/ufo/harness/sandbox/terminal.py`

`io_transport` · `request handling and tool execution`

Most sandboxes are reached by dialing a remote machine or container. A member’s own terminal is different: the server cannot dial into it directly. Instead, the server sends a small instruction down the already-open client connection, and the client answers on its next request back. This file is the meeting place for those two halves.

The central idea is a per-conversation slot. Think of it like a numbered pickup window at a shop: the server leaves one job for a particular conversation, the connected terminal picks it up, and later returns the result to the same window. The slot remembers the current working directory, who is allowed to answer, the operation in progress, any bytes being copied, and any tasks waiting their turn.

`Terminals` is the in-process rendezvous. It uses a lock because the workflow that asks for an operation and the web server connection that delivers it may run on different event loops and threads. It carefully wakes each waiting task on its own event loop.

`TerminalCarrier` makes this rendezvous look like a normal sandbox carrier. It can create or attach a sandbox handle, run commands, copy files in and out, run file-browser operations, render supported documents, and refuse unsupported networking. It also rewrites logical `/workspace/...` paths to the member’s real local directory, so higher-level tools can behave the same across terminal and container sandboxes.

#### Function details

##### `TerminalTransport.connect`  (lines 250–256)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Defines the contract for announcing that a client terminal is connected for a conversation. Implementations use this to make the terminal available for future operations.

**Data flow**: A conversation id, current directory, optional member id, and optional runtime id go in. The transport records that this conversation now has a reachable terminal. Nothing is returned.

**Call relations**: This is part of the transport interface. The in-process `Terminals` class provides the concrete behavior, and surface routes call this when a member’s held connection arrives.


##### `TerminalTransport.disconnect`  (lines 258–258)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Defines the contract for announcing that a terminal connection has gone away. This lets the transport stop offering a terminal that is no longer connected.

**Data flow**: A conversation id goes in. The transport updates its connection state for that conversation. Nothing is returned.

**Call relations**: This is implemented by transports such as `Terminals`. It is used by the surface side when the held client stream ends.


##### `TerminalTransport.workspace`  (lines 260–260)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Defines a quick lookup for the workspace currently bound to a conversation’s terminal. Callers use it to learn where the terminal is standing without waiting.

**Data flow**: A conversation id goes in. The transport either returns workspace details or reports that no terminal is known. It does not change state.

**Call relations**: This interface method is implemented by `Terminals` and any other terminal transport. It gives higher-level code a common way to inspect the binding.


##### `TerminalTransport.arrived`  (lines 262–262)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Defines a waitable lookup for a terminal connection. It is useful because the client connection may briefly disappear during normal long-polling or held-stream behavior.

**Data flow**: A conversation id and a grace period go in. The transport waits up to that time for a terminal, then returns its workspace details or `None`. It may record and later remove a waiter while it waits.

**Call relations**: The concrete `Terminals.arrived` method is used before sending work, and `TerminalCarrier` uses it when creating or attaching to a terminal-backed sandbox.


##### `TerminalTransport.send`  (lines 264–273)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Defines how server-side code asks the terminal to perform one operation and waits for its bytes reply. This is the main bridge from sandbox tools to the member’s machine.

**Data flow**: A conversation id, operation kind, timeout, optional name, argument, JSON parameters, and optional body bytes go in. The transport delivers that request to the terminal and returns the reply bytes, or raises if the terminal disappears or the operation fails.

**Call relations**: Implemented by `Terminals` and used heavily by `TerminalCarrier` for command execution, file reads and writes, file operations, and skill loading.


##### `TerminalTransport.next_op`  (lines 275–277)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Defines how the connected terminal asks, “What should I do next?” It returns the next pending operation for that conversation.

**Data flow**: A conversation id and optionally an operation id to skip go in. The transport waits until there is an operation to deliver, then returns its instruction record.

**Call relations**: Surface routes call this while holding the client connection open. `Terminals.next_op` supplies the in-process behavior.


##### `TerminalTransport.staged`  (lines 279–281)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Defines how the client retrieves large bytes that belong to an in-flight operation, such as the contents of a file being written. This keeps bulky data out of the small directive message.

**Data flow**: A conversation id, operation id, and optional member id go in. If they match the current operation and authorized member, the staged bytes come out; otherwise `None` comes out.

**Call relations**: Implemented by `Terminals`. It is used by the surface projection that serves the body bytes to the connected terminal.


##### `TerminalTransport.resolve`  (lines 283–290)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Defines how the client answers an operation after it has run it. It marks the waiting server-side operation as completed.

**Data flow**: A conversation id, operation id, reply bytes, optional failure message, and optional member id go in. The transport wakes the waiting sender if the answer matches the current operation, and returns whether the answer was accepted.

**Call relations**: Surface routes call this when the client posts an operation result. `Terminals.resolve` provides the local implementation and wakes the sender.


##### `TerminalTransport.in_flight`  (lines 292–292)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Defines a way to inspect the operation currently waiting for a reply. This is mainly useful for tests or operator-facing inspection.

**Data flow**: A conversation id goes in. The current operation comes out, or `None` if nothing is pending.

**Call relations**: The concrete `Terminals.in_flight` method exposes this state from the in-process rendezvous.


##### `_wake`  (lines 295–304)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: Safely wakes an `asyncio` future from another thread. A future is a promise for a later result, and it must be completed on the event loop that owns it.

**Data flow**: A saved waiter, made of a future and its event loop, plus an answer go in. The function schedules a tiny callback on that loop, which fills in the answer if the future is still waiting. Nothing is returned.

**Call relations**: `Terminals.connect` uses this to notify code waiting for a terminal to arrive. `Terminals.send` uses it to deliver an operation to a watching client stream, and `Terminals.resolve` uses it to return the terminal’s reply to the waiting sender.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 300–302)

```
def _set() -> None
```

**Purpose**: Completes the waiting future if nobody has already completed or cancelled it. This small inner step runs on the future’s own event loop.

**Data flow**: It closes over the future and answer from `_wake`. When the event loop runs it, it checks whether the future is still unfinished and stores the answer there. It returns nothing.

**Call relations**: `_wake` schedules this callback with the event loop. It is the actual moment where a waiting task is released.


##### `Terminals.connect`  (lines 320–342)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that a member’s terminal connection is now present for a conversation. It also wakes anyone who was waiting for that terminal to reconnect.

**Data flow**: The conversation id, current directory, member id, and optional runtime id go in. The method creates or updates the conversation slot, increments the connection count, removes arrival waiters, and wakes them after releasing the lock. Nothing is returned.

**Call relations**: This is called by the surface when a terminal stream connects. It calls `_wake` so `Terminals.arrived` callers can continue, and it creates a new slot when needed.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 344–351)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Records that one terminal connection for a conversation has ended. If no operation is waiting and no connections remain, it removes the conversation slot.

**Data flow**: A conversation id goes in. The method finds the slot, lowers its connection count, and may delete the slot. Nothing is returned.

**Call relations**: The surface calls this when the client stream closes. Its cleanup affects later `arrived`, `send`, and `next_op` calls.


##### `Terminals.workspace`  (lines 353–360)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the current terminal workspace for a conversation if one is connected. This is a non-waiting snapshot.

**Data flow**: A conversation id goes in. The method reads the slot under the lock and returns a `TerminalWorkspace` with directory, member, and runtime information, or `None`. It does not modify the slot.

**Call relations**: This is the concrete implementation of the transport interface’s quick workspace lookup. It builds the small workspace record used by callers.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 362–386)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits for a terminal to be connected, but only up to a given grace period. This prevents normal reconnect gaps from being mistaken for a missing terminal.

**Data flow**: A conversation id and maximum wait time go in. The method immediately returns workspace details if a slot exists; otherwise it registers a future as an arrival waiter, waits until connection or timeout, and cleans up timed-out waiters. The result is workspace details or `None`.

**Call relations**: `Terminals.send` calls this before trying to send work. `TerminalCarrier.create` and `TerminalCarrier.attach` also rely on the transport’s arrival behavior before opening a terminal-backed sandbox.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 388–393)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: Removes a timed-out or abandoned arrival waiter. This keeps the arrivals list from collecting dead futures.

**Data flow**: A conversation id and a specific future go in. The method filters that future out of the waiting list and deletes the list if it becomes empty. Nothing is returned.

**Call relations**: `Terminals.arrived` calls this when its wait expires or no time remains.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 395–470)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one operation to the connected terminal and waits for the answer. It also makes sure only one operation runs at a time for a conversation.

**Data flow**: Operation details and optional body bytes go in. The method waits for a terminal, waits for its turn, creates a unique operation id, stores the operation in the slot, wakes any connected client watcher, then waits for the reply. It returns reply bytes, or raises if the terminal is absent, times out, or reports failure. On every exit it clears the in-flight operation and wakes queued senders.

**Call relations**: This is the workhorse used by `TerminalCarrier` methods. It calls `arrived` to confirm a terminal exists, `_take_turn` to serialize operations, and `_wake` to notify the client side or queued senders.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 6 external calls (__init__, __init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 472–503)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: Waits until this sender owns the conversation’s single operation slot. It prevents two server tasks from asking the same terminal to run two operations at the same time.

**Data flow**: A conversation id, the caller’s event loop, and the intended operation timeout go in. If the slot is free, it marks it busy and returns. If not, it queues a waiter and waits until released or timed out; on timeout it removes its own waiter and raises.

**Call relations**: `Terminals.send` calls this before installing a new operation. When `send` finishes, it wakes queued waiters so they can try to claim the turn.

*Call graph*: called by 1 (send); 6 external calls (__init__, __init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 505–530)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Lets the connected terminal wait for the next server-requested operation. It is the terminal side of the rendezvous.

**Data flow**: A conversation id and optional operation id to exclude go in. If there is an undelivered matching operation, it marks it delivered and returns it. Otherwise it stores a watcher future and waits until `send` provides an operation, then returns that operation.

**Call relations**: Surface routes use this while holding the client stream. `Terminals.send` wakes the watcher when it places a new operation in the slot.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 532–544)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Returns the staged bytes for the current operation, such as file content being copied into the terminal workspace. It only serves bytes for the exact in-flight operation and allowed member.

**Data flow**: A conversation id, operation id, and optional member id go in. The method checks the current slot, operation id, and member gate. Matching staged bytes come out; otherwise `None` comes out.

**Call relations**: The surface uses this when the client follows the read projection for a write body. `TerminalCarrier.write` is one producer of those staged bytes through `send`.


##### `Terminals.in_flight`  (lines 546–551)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Shows the operation currently waiting for a terminal reply. It is a simple inspection hook.

**Data flow**: A conversation id goes in. The method returns the slot’s current operation, or `None` if there is no slot or no operation. It does not change state.

**Call relations**: This supports tests or diagnostic code that need to see what the terminal is being asked to do.


##### `Terminals.resolve`  (lines 553–582)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts the terminal’s answer to the current operation and wakes the server-side sender. It ignores stale, duplicate, wrong-member, or wrong-operation answers.

**Data flow**: The conversation id, operation id, reply bytes, optional failure string, and optional member id go in. If they match the current waiting operation, the method marks it resolved and wakes the sender with either reply bytes or a failure object. It returns `true` when accepted and `false` otherwise.

**Call relations**: Surface routes call this when the client posts an operation result. It uses `_wake` to release the `Terminals.send` call that is waiting for the answer.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 598–644)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a sandbox handle for a conversation whose sandbox is the member’s own terminal. It verifies that the connected terminal is in the expected workspace directory.

**Data flow**: A sandbox specification goes in. The method waits for the terminal, compares its current directory with the requested workspace, builds proxy environment variables with the run token, and returns a `SandboxHandle`. It raises if no terminal arrives or the terminal is standing in the wrong directory.

**Call relations**: Higher-level sandbox setup calls this when choosing the terminal carrier. The returned handle is later used by `exec`, `read`, `write`, and `file_op`.

*Call graph*: 4 external calls (__init__, __init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 646–660)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reattaches to an already-bound terminal for off-turn work such as file browsing or background writes. It does not wait long; it only succeeds if the terminal is already visible through the transport.

**Data flow**: A sandbox specification goes in. The method asks the transport for an arrived terminal with no grace time, checks that its directory matches the resume id, and returns a lightweight `SandboxHandle` or `None`.

**Call relations**: This is used outside the main turn when tools still need access to the bound terminal. It depends on the transport’s `arrived` behavior rather than process-local state.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 662–676)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command in the member’s terminal workspace using the same sandbox interface as other carriers. It translates logical workspace paths before handing the command to the terminal.

**Data flow**: A sandbox handle, command arguments, and timeout go in. The method finds the real workspace root, rewrites arguments from sandbox-style paths to host paths, then delegates to `_exec`. An `ExecResult` comes out.

**Call relations**: Sandbox command execution calls this public method. It calls `_root`, uses `host_argv` for path translation, and hands the resolved command to `TerminalCarrier._exec`.

*Call graph*: calls 2 internal fn (_exec, _root); 1 external calls (host_argv).


##### `TerminalCarrier.load_skills`  (lines 678–689)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asks the connected client to load skill files into the user’s UFO home area. Skills are extra capabilities or instructions made available to the running client.

**Data flow**: A sandbox handle and JSON-like payload go in. The payload is serialized to compact JSON and sent as a skills operation. The terminal’s text reply becomes the stdout of a successful `ExecResult`; a terminal-reported failure becomes a runtime error.

**Call relations**: Higher-level skill-loading code calls this through the carrier. It uses the transport’s `send` operation with the skills kind.

*Call graph*: 2 external calls (__init__, dumps).


##### `TerminalCarrier._exec`  (lines 691–726)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs command arguments that have already been converted to real host paths. It is the lower-level command runner for terminal-backed execution.

**Data flow**: A sandbox handle, already-resolved arguments, and timeout go in. The method sends an exec operation with arguments and environment, parses the JSON reply, decodes base64 stdout and stderr, normalizes client-reported timeouts to a standard timeout exit code, and returns an `ExecResult`.

**Call relations**: `TerminalCarrier.exec` calls this after path rewriting. `_enumerate` also calls it because enumeration commands build their own exact host paths.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (_enumerate, exec); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 728–741)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes from the server into a file in the member’s terminal workspace. The bytes are staged separately from the small operation directive.

**Data flow**: A sandbox handle, logical path, and file bytes go in. The method maps the path under the real workspace and sends a write operation with the content as staged body bytes. It returns nothing on success and raises an `OSError` on terminal-reported failure.

**Call relations**: File-copy callers use this carrier method. It calls `_client_path` for path mapping and relies on `Terminals.staged` on the client-fetch side of the flow.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 743–759)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a file from the member’s terminal workspace and yields it in chunks. Chunking keeps the caller interface consistent even though the rendezvous receives the reply as one byte string.

**Data flow**: A sandbox handle and logical path go in. The method maps the path, sends a read operation, converts terminal “not found” failures into `FileNotFoundError`, and yields the reply bytes in one-megabyte pieces.

**Call relations**: File-reading code calls this through the sandbox carrier interface. It uses `_client_path` before sending the read request through the transport.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 761–849)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one higher-level `ufo fs` file operation in the terminal workspace, such as reading metadata, searching, globbing, or reporting git changes. It keeps the operation close to where the files actually live.

**Data flow**: A sandbox handle, operation name, and parameter dictionary go in. The method rewrites path parameters to real host paths, optionally renders supported documents, optionally runs a preliminary enumeration command for walks or change scans, sends the file operation to the client, parses the JSON reply, and returns the result dictionary or raises on reported errors.

**Call relations**: File-browser and filesystem tools call this method. It calls `_root` and `_under_root` for path mapping, `_enumerate` for operations that need a stable listing, and `_reply_object` to parse the terminal’s response.

*Call graph*: calls 4 internal fn (_enumerate, _reply_object, _root, _under_root); 2 external calls (dumps, PurePosixPath).


##### `TerminalCarrier._enumerate`  (lines 851–872)

```
async def _enumerate(self, handle: SandboxHandle, op: str, walk_root: str, program: str, arguments: tuple[str, ...]=()) -> None
```

**Purpose**: Runs a preparatory shell command that lists files or repositories before a filesystem operation. This makes the server, not the client, decide exactly what the later walk should visit.

**Data flow**: A sandbox handle, operation name, walk root, shell program text, and optional arguments go in. The method builds a shell command with `UFO_WALK_ROOT`, runs it through `_exec`, and raises if the listing command exits with an unexpected failure code.

**Call relations**: `TerminalCarrier.file_op` calls this before grep, glob, or changes operations. It delegates actual command execution to `TerminalCarrier._exec`.

*Call graph*: calls 1 internal fn (_exec); called by 1 (file_op); 1 external calls (quote).


##### `TerminalCarrier.dial`  (lines 874–878)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Refuses attempts to expose a network port from a terminal-backed sandbox. A member’s local terminal does not provide the same reachable per-port host as remote sandbox carriers.

**Data flow**: A sandbox handle and port go in. The method always raises `SandboxUnreachable`; no connection target is returned.

**Call relations**: Code that tries to open a service port through the sandbox carrier reaches this method. It tells callers to use a remote carrier for that kind of access.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 881–889)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: Decodes one captured command stream, such as stdout or stderr, from the terminal’s exec reply. The terminal sends these streams as base64 text, which is a safe text form for arbitrary bytes.

**Data flow**: A parsed reply dictionary and stream name go in. The function looks for `<name>_b64`, requires it to be present, base64-decodes it, and returns raw bytes. Missing or malformed data raises an error.

**Call relations**: `TerminalCarrier._exec` calls this twice, once for stdout and once for stderr, after `_reply_object` has parsed the reply.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 892–899)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: Parses a terminal reply that should be a JSON object. It protects callers from silently accepting malformed or unexpected reply shapes.

**Data flow**: Raw reply bytes and an operation name go in. The function decodes the bytes as UTF-8, parses JSON, checks that the result is a dictionary, and returns it. Bad JSON or a non-object result raises a runtime error.

**Call relations**: `TerminalCarrier._exec` uses this for command replies, and `TerminalCarrier.file_op` uses it for filesystem operation replies.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 902–905)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: Returns the real host directory that backs `/workspace` for a terminal sandbox. It fails if the handle does not carry such a directory.

**Data flow**: A sandbox handle goes in. The function reads `workspace_host_path` and returns it, or raises if it is missing.

**Call relations**: `TerminalCarrier.exec`, `TerminalCarrier.file_op`, and `_client_path` call this before rewriting logical workspace paths.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 908–914)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: Maps a logical sandbox path like `/workspace/file.txt` to the member’s real workspace path. It strips only the leading `/workspace`, not every occurrence of the word.

**Data flow**: A sandbox handle and path go in. The function gets the workspace root with `_root`, maps the path with `_under_root`, and returns the path the client should use.

**Call relations**: `TerminalCarrier.read` and `TerminalCarrier.write` call this before sending file operations to the terminal.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 917–922)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: Performs the actual path mapping from `/workspace/...` to a real root directory. Paths outside `/workspace` are left unchanged.

**Data flow**: A real root directory and a path string go in. The function treats the path as a POSIX-style path, checks whether it is under the logical workspace directory, and either joins its relative part onto the root or returns the original path.

**Call relations**: `_client_path` uses this for reads and writes, and `TerminalCarrier.file_op` uses it to rewrite path parameters before asking the client to run filesystem operations.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### Remote Browser and Carrier Backends
These adapters provision hosted browser sessions or isolated Docker and E2B runtimes, while the browser contract hides where Chrome actually runs.

### `extensions/browserbase/ufo_ext_browserbase.py`

`io_transport` · `active during browser session setup, browser use, file upload/download, recovery reattach, and teardown`

A normal local browser can read files from the machine it runs on and write downloads back to local disk. Browserbase is different: Chrome runs on Browserbase’s servers, so this file acts like a shipping clerk between the local workspace and the remote browser. It asks Browserbase to create a browser session, gives the rest of the system the connection address for that session, uploads local files when a web page needs them, and fetches finished downloads back by calling Browserbase’s web API.

The file also protects browser state. Each browser run gets a Browserbase “Context”, which is Browserbase’s stored browser profile: cookies, local storage, and logins. That context is saved in this extension’s scoped store so a recovered run can reopen a browser with the same state. When the run ends, the session is released and the context is deleted, so authenticated browser state does not outlive the subagent turn.

The Browserbase API key is read from host-side credentials each time an API call is made. That means the key is not passed into the sandbox, and rotating the key takes effect on the next call. The file also sets limits on uploads and downloads so an unexpected web page cannot push very large files through the shared process.

#### Function details

##### `BrowserbaseApi.create_session`  (lines 84–93)

```
async def create_session(self, context_id: str) -> tuple[str, str]
```

**Purpose**: Creates a new hosted Browserbase browser session tied to an existing browser context. The rest of the system uses the returned connection URL to talk to the remote Chrome.

**Data flow**: It receives a context ID. It sends Browserbase a request to start a session using that context and a fixed timeout, then reads the session ID and connection URL from the reply. It returns those two strings to the caller.

**Call relations**: This is used when a new lease is being created. It relies on BrowserbaseApi._json to send the request and parse the response, then uses _field to make sure Browserbase actually returned the fields needed to connect.

*Call graph*: calls 2 internal fn (_json, _field).


##### `BrowserbaseApi.live_session`  (lines 95–99)

```
async def live_session(self, session_id: str) -> str
```

**Purpose**: Checks whether an existing Browserbase session is still usable and, if it is, gets its current connection URL. This is what makes recovery or reattachment possible.

**Data flow**: It receives a session ID. It asks Browserbase for that session’s status, rejects the session if the status is no longer live, and otherwise returns the connect URL. If the session is gone, it raises SessionGone so the caller knows it cannot reuse it.

**Call relations**: This is part of the reattach path. BrowserbaseCdpProvider.reattach uses it after decoding a saved token, and live_session depends on BrowserbaseApi._json and _field to safely read Browserbase’s answer.

*Call graph*: calls 2 internal fn (_json, _field); 1 external calls (__init__).


##### `BrowserbaseApi.release_session`  (lines 101–102)

```
async def release_session(self, session_id: str) -> None
```

**Purpose**: Tells Browserbase that this browser session should be released. This prevents the remote browser from continuing to run or bill after the turn is finished.

**Data flow**: It receives a session ID. It sends Browserbase a status update requesting release and does not return any data. The external effect is that Browserbase is asked to stop the session.

**Call relations**: BrowserbaseLease.aclose calls this during cleanup. It uses BrowserbaseApi._json for the network request because Browserbase’s session API is JSON-based.

*Call graph*: calls 1 internal fn (_json).


##### `BrowserbaseApi.create_context`  (lines 104–105)

```
async def create_context(self) -> str
```

**Purpose**: Creates a Browserbase context, which is the remote browser profile that stores cookies, logins, and local storage. A context lets a restarted session continue with the state already earned during the same browser run.

**Data flow**: It sends Browserbase an empty create-context request. It reads the new context ID from the JSON response and returns that ID. If Browserbase does not return a usable ID, the call fails instead of silently continuing without persistent state.

**Call relations**: BrowserbaseCdpProvider._context calls this when there is no context already stored for the current conversation. It uses BrowserbaseApi._json for the request and _field to validate the returned ID.

*Call graph*: calls 2 internal fn (_json, _field); called by 1 (_context).


##### `BrowserbaseApi.delete_context`  (lines 107–108)

```
async def delete_context(self, context_id: str) -> None
```

**Purpose**: Deletes a Browserbase context after the browser run is over. This removes the stored cookies and login state so the subagent’s authenticated browser state does not linger.

**Data flow**: It receives a context ID. It sends a DELETE request to Browserbase and returns nothing. The important output is the remote side effect: Browserbase is asked to remove that stored browser profile.

**Call relations**: BrowserbaseLease.aclose calls this after releasing the session. It uses BrowserbaseApi._send directly because no JSON body needs to be interpreted.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi.download`  (lines 110–140)

```
async def download(self, session_id: str, guid: str) -> bytes
```

**Purpose**: Fetches the bytes of a completed browser download from Browserbase storage. It waits briefly for Browserbase’s download list to catch up, and refuses files that are too large or whose size is unknown.

**Data flow**: It receives a session ID and a download GUID, which is the name used to identify the stored download. It repeatedly asks Browserbase for the session’s download list, finds the matching entry, checks its reported size, then downloads and returns the file bytes. If the file never appears, is missing a size, or is too large, it raises an error.

**Call relations**: BrowserbaseLease.fetch_download delegates to this when the engine wants the downloaded file. This function uses BrowserbaseApi._json to list downloads, _size to enforce the safety limit, _field to get the download entry ID, BrowserbaseApi._send to fetch the actual bytes, and asyncio.sleep between retries.

*Call graph*: calls 4 internal fn (_json, _send, _field, _size); 2 external calls (__init__, sleep).


##### `BrowserbaseApi.upload`  (lines 142–148)

```
async def upload(self, session_id: str, name: str, data: bytes) -> None
```

**Purpose**: Uploads a local file’s bytes into the remote Browserbase session so the hosted Chrome can use it. This is needed because a remote browser cannot access local workspace paths directly.

**Data flow**: It receives a session ID, a remote filename, and bytes. It sends those bytes to Browserbase’s upload endpoint as a file upload. It returns nothing, but after success the file is available inside the remote session.

**Call relations**: BrowserbaseLease.place_file calls this after reading and naming the workspace file. The upload itself is sent through BrowserbaseApi._send.

*Call graph*: calls 1 internal fn (_send).


##### `BrowserbaseApi._json`  (lines 150–155)

```
async def _json(self, method: str, path: str, **kwargs: object) -> dict[str, object]
```

**Purpose**: Sends a Browserbase API request that is expected to return a JSON object. It is a small safety wrapper that refuses unexpected response shapes.

**Data flow**: It receives an HTTP method, a path, and request options. It sends the request through BrowserbaseApi._send, parses the response as JSON, checks that the result is a dictionary-like object, and returns it. If the response is not a JSON object, it raises BrowserbaseError.

**Call relations**: The higher-level API methods use this whenever they expect structured Browserbase data, including creating sessions and contexts, checking sessions, listing downloads, and releasing sessions. It hands the actual network work to BrowserbaseApi._send.

*Call graph*: calls 1 internal fn (_send); called by 5 (create_context, create_session, download, live_session, release_session); 1 external calls (__init__).


##### `BrowserbaseApi._send`  (lines 157–172)

```
async def _send(self, method: str, path: str, timeout_s: float, **kwargs: object) -> httpx.Response
```

**Purpose**: Performs the actual HTTP request to Browserbase. It adds the Browserbase API key, applies a timeout, and turns failed HTTP responses into clear transport errors.

**Data flow**: It receives an HTTP method, path, timeout, and optional request details such as headers or files. It reads the API key from credentials, combines it with any extra headers, sends the request with httpx, and returns the HTTP response. If Browserbase answers with an error status, it raises BrowserbaseError with the status and response text.

**Call relations**: This is the network foundation for the file. BrowserbaseApi._json, delete_context, download, and upload all call it so API-key handling and error reporting stay consistent in one place.

*Call graph*: called by 4 (_json, delete_context, download, upload); 2 external calls (__init__, AsyncClient).


##### `_field`  (lines 175–179)

```
def _field(body: dict[str, object], name: str) -> str
```

**Purpose**: Reads a required string field from a Browserbase JSON response. It prevents the code from continuing with missing or empty IDs and URLs.

**Data flow**: It receives a dictionary and a field name. It looks up the value, checks that it is a non-empty string, and returns it. If not, it raises BrowserbaseError.

**Call relations**: Browserbase API methods call this after getting JSON responses, especially when they need IDs, connection URLs, or download entry IDs. It is the shared guardrail for required response fields.

*Call graph*: called by 4 (create_context, create_session, download, live_session); 1 external calls (__init__).


##### `_size`  (lines 182–189)

```
def _size(entry: dict[str, object]) -> int
```

**Purpose**: Reads and validates the size of a listed download. This matters because download size is the main protection against loading an unexpectedly huge file into memory.

**Data flow**: It receives one download-list entry. It checks that the entry has a numeric size and that the value is not a boolean pretending to be a number, then returns the size as an integer. If the size is missing or unusable, it raises BrowserbaseError.

**Call relations**: BrowserbaseApi.download calls this before fetching the actual file bytes. It helps download enforce its memory safety limit before data is pulled into the process.

*Call graph*: called by 1 (download); 1 external calls (__init__).


##### `BrowserbaseLease.endpoint`  (lines 207–208)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the connection information needed to talk to the remote Chrome session. The engine uses this endpoint as the doorway into the hosted browser.

**Data flow**: It reads the lease’s saved connect URL. It wraps that URL in a CdpEndpoint object and returns it. It does not contact Browserbase or change state.

**Call relations**: The browser engine calls this on a lease when it is ready to connect. It creates the CdpEndpoint object that represents the Chrome DevTools Protocol connection target, where Chrome DevTools Protocol means the control channel used to drive Chrome.

*Call graph*: 1 external calls (__init__).


##### `BrowserbaseLease.token`  (lines 210–219)

```
async def token(self) -> str
```

**Purpose**: Builds a compact reattachment token for this browser lease. The token carries enough information to find the same conversation, session, and context later.

**Data flow**: It reads the lease’s conversation ID, session ID, and context ID. It joins them into one slash-separated string and returns it. Nothing external is changed.

**Call relations**: The runtime can save this token and later pass it to BrowserbaseCdpProvider.reattach. That later path depends on the token containing all three IDs, so it does not have to guess which context belongs to which browser run.


##### `BrowserbaseLease.place_file`  (lines 221–241)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Uploads a workspace file into the remote browser session and returns the path Browserbase will expose to Chrome. It also avoids filename collisions when two different local files have the same base name.

**Data flow**: It receives a workspace path and a reader function that returns the file bytes. It extracts the file’s base name, reads the bytes, rejects files over the upload limit, chooses a remote name, uploads the bytes, records the staged name, and returns a remote path under /tmp/.uploads. If another file already used the same base name, it prefixes the later upload with a short hash of the path.

**Call relations**: The browser engine calls this when a page needs a local file, such as for a file input. This method performs the naming and safety checks, then hands the actual upload to BrowserbaseApi.upload.

*Call graph*: 2 external calls (sha256, PurePosixPath).


##### `BrowserbaseLease.download_dir`  (lines 243–246)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the only download directory name that Browserbase’s hosted Chrome accepts. It hides a Browserbase-specific rule from the rest of the browser engine.

**Data flow**: It takes no meaningful input beyond the lease itself. It returns the literal string downloads. It does not touch the network or change any state.

**Call relations**: The browser engine asks the lease where downloads should be directed. For Browserbase, this method supplies the special remote storage target rather than a local absolute path.


##### `BrowserbaseLease.fetch_download`  (lines 248–253)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Retrieves a completed download from the remote session and returns its bytes to the caller. It is the lease-level doorway for getting downloaded files out of Browserbase storage.

**Data flow**: It receives a download GUID. It passes the lease’s session ID and that GUID to BrowserbaseApi.download, waits for the bytes, and returns them. Any size, missing-file, or API errors come from the lower-level download call.

**Call relations**: The browser engine calls this after a download finishes. This method delegates the real Browserbase download lookup and fetch to BrowserbaseApi.download.


##### `BrowserbaseLease.aclose`  (lines 255–266)

```
async def aclose(self) -> None
```

**Purpose**: Cleans up the remote browser resources for this lease. It releases the Browserbase session, deletes the Browserbase context, and removes the stored context ID for the conversation.

**Data flow**: It reads the lease’s session ID, context ID, conversation ID, API object, and store. It asks Browserbase to release the session, then asks Browserbase to delete the context, then deletes the store entry that points to that context. The cleanup is arranged so the store row is removed even if deleting the remote context fails.

**Call relations**: The runtime calls this when the browser turn ends. It calls BrowserbaseApi.release_session first, then BrowserbaseApi.delete_context, then the scoped store’s delete operation, so later browser runs do not reuse a stale context entry.


##### `BrowserbaseCdpProvider.lease`  (lines 280–297)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new Browserbase-backed browser lease for a browser turn. This is the main entry used when the system needs a fresh hosted Chrome session.

**Data flow**: It receives the current sandbox, which contains the conversation ID for this browser run. It creates a BrowserbaseApi and a scoped store, gets or creates the context for that conversation, creates a Browserbase session using that context, and returns a BrowserbaseLease containing all the information needed to connect and later clean up.

**Call relations**: The core CDP-provider seam calls this when Browserbase is selected as the browser backend. It calls BrowserbaseCdpProvider._context to get persistent run state, then BrowserbaseApi.create_session, and finally packages everything into a BrowserbaseLease.

*Call graph*: calls 1 internal fn (_context); 3 external calls (__init__, __init__, __init__).


##### `BrowserbaseCdpProvider._context`  (lines 299–306)

```
async def _context(self, api: BrowserbaseApi, store: ScopedStore, conversation_id: UUID) -> str
```

**Purpose**: Finds the Browserbase context for a conversation, or creates and records one if none exists. This is what lets a recovered browser session keep cookies and local storage from earlier in the same run.

**Data flow**: It receives an API object, a scoped store, and a conversation ID. It builds a store key, checks whether a valid context ID is already stored, and returns it if present. Otherwise it creates a new Browserbase context, stores the new ID under that key, and returns it.

**Call relations**: BrowserbaseCdpProvider.lease calls this before creating a session. It reads and writes through ScopedStore and calls BrowserbaseApi.create_context only when the conversation has no stored context yet.

*Call graph*: calls 3 internal fn (get, put, create_context); called by 1 (lease).


##### `BrowserbaseCdpProvider.reattach`  (lines 308–319)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Rebuilds a lease around an already-existing Browserbase session using a saved token. This supports recovery when the system still has a live remote browser session to reconnect to.

**Data flow**: It receives a token string. It parses the token into conversation ID, session ID, and context ID, creates a BrowserbaseApi, asks Browserbase whether the session is still live and what its connection URL is, and returns a BrowserbaseLease for that existing session. If the token is invalid or the session is gone, the reattach path fails with SessionGone.

**Call relations**: The runtime calls this when it has a previous lease token and wants to reconnect instead of starting over. It uses _parse_token first, then BrowserbaseApi.live_session, and finally creates a BrowserbaseLease.

*Call graph*: calls 1 internal fn (_parse_token); 3 external calls (__init__, __init__, __init__).


##### `_parse_token`  (lines 322–330)

```
def _parse_token(token: str) -> tuple[UUID, str, str]
```

**Purpose**: Decodes a Browserbase reattachment token into the three IDs needed to reconnect and clean up. It treats malformed tokens as unusable sessions.

**Data flow**: It receives a slash-separated token string. It splits out the conversation ID, session ID, and context ID, validates that the session and context IDs are present, converts the conversation ID into a UUID, and returns all three values. If anything is missing or malformed, it raises SessionGone.

**Call relations**: BrowserbaseCdpProvider.reattach calls this before contacting Browserbase. By raising SessionGone for bad tokens, it lets the recovery flow use the same fallback behavior as it would for a remotely reaped session.

*Call graph*: called by 1 (reattach); 2 external calls (__init__, UUID).


##### `manifest`  (lines 333–349)

```
def manifest() -> Manifest
```

**Purpose**: Declares this extension to the host system. It tells the system the extension’s name and version, what credential it needs, and how to build the Browserbase CDP provider.

**Data flow**: It takes no input. It creates a Manifest containing one credential slot for the Browserbase API key and one CDP provider specification for the Browserbase backend, then returns that manifest.

**Call relations**: The extension loader calls this to discover what the file provides. The provider spec’s build function constructs BrowserbaseCdpProvider when the deployment selects Browserbase as the browser backend.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox creation, command execution, file access, and idle cleanup`

This file is the Docker version of a sandbox carrier: the part of the system that creates a safe work area where an agent can run commands and read or write files. Think of it like giving each conversation its own rented workshop. The workshop can be locked, restarted, or cleaned up without mixing tools or files with another conversation.

The carrier names containers from the conversation ID, so the same conversation can reconnect to the same container later. If the container is running, it reuses it. If it was stopped to save memory and network space, it starts it again. If no container exists, it creates one, mounts the conversation workspace at `/workspace`, joins it to a per-conversation Docker network, and runs a long-lived `sleep infinity` process so later commands can enter with `docker exec`.

Network access is deliberately routed through an egress proxy. A proxy is a gatekeeper for outgoing web requests. Each command receives proxy settings for the current turn only; those settings are not stored inside the container, because the container may outlive the turn. The real API key is also not placed in the sandbox. A harmless sentinel value is used instead, and the proxy swaps it for the real key only when allowed.

The file also reclaims idle containers by stopping them, not deleting them. That frees host resources while preserving the workspace for a later restart.

#### Function details

##### `_docker`  (lines 79–93)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and returns its exit code, standard output, and error output. It is the single low-level doorway this file uses to talk to Docker.

**Data flow**: It receives Docker arguments, optional input bytes, and a timeout. It starts `docker ...`, feeds the input to it, waits for it to finish, and returns the result. If Docker takes too long, it kills the process and returns a special timeout code with a short error message.

**Call relations**: Almost every DockerCarrier operation calls this helper when it needs Docker to inspect, start, stop, create, remove, or execute inside containers. Higher-level methods build safe, meaningful behavior on top of this raw Docker call.

*Call graph*: called by 13 (_death_report, _ensure_network, _exec_with, _held_id, _install_ca, _prepare_mounts, _reclaim_idle, _release, _revive, _running_id (+3 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 106–219)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or reconnects to the Docker container for one conversation. It also builds the per-turn proxy environment so commands in the sandbox use the current run token and never inherit an older one.

**Data flow**: It receives a sandbox specification containing the conversation ID, image, workspace path, proxy details, run token, and extra environment variables. It first reclaims old idle containers, then looks for an existing running or stopped container. It installs the current proxy certificate, prepares the mounted workspace, and returns a SandboxHandle describing how later operations should reach this container. If no usable container exists, it creates the Docker network and container from scratch.

**Call relations**: This is the main setup path used when a conversation needs a sandbox. It asks helper methods to find running or stopped containers, revive stopped ones, create networks, install certificates, and prepare mounts. If Docker reports that another concurrent create already won the container name, it attaches to that winner instead of making a duplicate.

*Call graph*: calls 9 internal fn (_ensure_network, _install_ca, _network_name, _prepare_mounts, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier.attach`  (lines 221–250)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an existing container for a conversation without creating a new one. It is used when the system wants to read or inspect an existing sandbox and should treat absence as normal.

**Data flow**: It receives a sandbox specification and checks for a running container with the conversation’s name. If only a stopped container exists, it tries to revive it. It prepares the mounts and returns a SandboxHandle, or returns None if the container is absent or cannot be safely resumed.

**Call relations**: This is a gentler cousin of `DockerCarrier.create`. It uses the same container lookup, revive, and mount preparation helpers, but it never runs a fresh Docker container. When revive or preparation fails, it reports no attachment rather than forcing the caller through a Docker error.

*Call graph*: calls 4 internal fn (_prepare_mounts, _revive, _running_id, _stopped_id); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier._reclaim_idle`  (lines 252–320)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops old, inactive containers so the host does not run out of memory or Docker network space. It stops containers rather than deleting them, so a quiet conversation can resume later.

**Data flow**: It receives the conversation that is currently being opened and marks it as recently touched. It asks Docker for running sandbox containers and per-conversation networks, records any it did not already know about, then finds conversations that have been idle long enough and have no active command. For each stale conversation, it tries to stop the container and remove its network, keeping a retry record if cleanup fails.

**Call relations**: `DockerCarrier.create` calls this before opening a sandbox. It relies on `_held_id` to identify containers in any state and `_release` to perform the stop-and-network-removal step. Locks and in-flight counters keep cleanup from racing with a command or restart for the same conversation.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 322–332)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a normal command inside the sandbox container with the current turn’s network proxy settings. This is how agent tool commands execute in the Docker sandbox.

**Data flow**: It receives a sandbox handle, the command arguments, and a timeout. It turns the handle’s egress environment into Docker `--env` options and passes everything to `_exec_with`. The result is an ExecResult containing text output, error text, an exit code, and timeout information if applicable.

**Call relations**: This is the public command-running path for regular sandbox work. It delegates the actual Docker execution, restart-on-stopped-container behavior, and in-flight bookkeeping to `_exec_with`.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier.exec_skill`  (lines 334–338)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the container as the root user for server-controlled skill setup or synchronization. It is separated from normal execution because these setup tasks need elevated permissions.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It passes them to `_exec_with` with Docker options that choose the root user. It returns the same ExecResult shape as a normal command.

**Call relations**: This shares the same execution engine as `DockerCarrier.exec`, but supplies different Docker options. `_exec_with` still does the container pinning, Docker call, retry after revive, and result formatting.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier._exec_with`  (lines 340–379)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, options: tuple[str, ...]) -> ExecResult
```

**Purpose**: Performs the actual `docker exec` call and wraps the result in the project’s standard execution result object. It also protects active commands from idle cleanup.

**Data flow**: It receives a handle, command arguments, a timeout, and extra Docker options such as environment variables or user selection. It marks the conversation as active, runs `docker exec` in `/workspace`, and if Docker says the container is not running, tries to revive it once and retry. It converts Docker output into an ExecResult, including a special timeout marker, then marks the conversation idle again.

**Call relations**: `DockerCarrier.exec` and `DockerCarrier.exec_skill` both call this. It uses `_docker` for the subprocess call and `_revive` for recovery if cleanup stopped the container between uses.

*Call graph*: calls 2 internal fn (_revive, _docker); called by 2 (exec, exec_skill); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 381–399)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the sandbox, without putting those bytes on the command line. This is safer for arbitrary file contents and for paths supplied by tools or attachments.

**Data flow**: It receives a handle, a target path, and file contents as bytes. It marks the conversation active, starts the guarded copy-in program inside the container, and streams the bytes through standard input. If the container was stopped, it revives and retries once. On failure, it raises an OSError with Docker’s error text.

**Call relations**: This is the public file-write path for the Docker carrier. It delegates the single write attempt to `_write_started` and uses `_revive` if Docker reports that the container is not running.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 401–419)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Starts one guarded write attempt inside the container. The guard program enforces the sandbox’s path rules instead of relying on a shell redirect.

**Data flow**: It receives a handle, path, and bytes. It decides whether the path belongs under the runtime root or the workspace, then runs Python inside the container with the sandbox copy-in program and sends the bytes through standard input. It returns Docker’s exit code and error output.

**Call relations**: `DockerCarrier.write` calls this for the first attempt and possible retry. The helper uses `_docker` for the actual `docker exec -i` call and `sandbox_runtime_root` to choose the correct safe root.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `DockerCarrier.read`  (lines 421–455)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. It avoids loading a whole large file into host memory and reports filesystem errors in a way that matches the local sandbox carrier.

**Data flow**: It receives a handle and path, marks the conversation active, and starts a `cat` process inside the container. It yields chunks of bytes to the caller as they arrive. After the stream ends, it checks whether `cat` failed, turns recognizable system error messages into OSError values, and gives a detailed runtime error for unusual deaths. If the container was stopped before reading, it revives and starts again from the beginning.

**Call relations**: This is the public file-read path. It uses `_read_started` to create each streaming attempt, `_revive` to recover from a stopped container, and `_death_report` when `cat` dies without useful error text.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 457–499)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Prepares one file-read attempt and returns both the byte stream and a place where any final failure will be recorded. This shape lets the caller retry cleanly if the container was stopped.

**Data flow**: It receives a handle and path. It creates an empty failure list and an async generator that will run `docker exec ... cat path`. It returns the generator plus the failure list, which will stay empty on success or receive the exit code and error text after the stream drains.

**Call relations**: `DockerCarrier.read` calls this each time it needs a fresh read attempt. The nested `DockerCarrier._read_started.stream` function does the actual Docker subprocess work.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 472–497)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `cat` inside the container and yields the file bytes as they arrive. It also makes sure an abandoned read does not leave a Docker exec process running.

**Data flow**: It starts `docker exec container cat path` with output and error pipes. It reads standard output in fixed-size chunks and yields each chunk. When output ends, it reads the error pipe, waits for the process, and records failure details if the exit code is nonzero. If the caller stops reading early, it kills and drains the process.

**Call relations**: This nested generator is created by `_read_started` and consumed by `DockerCarrier.read`. It calls asyncio’s subprocess creation directly rather than `_docker` because it must stream output gradually instead of waiting for the whole command to finish.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 501–519)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Adds useful Docker container state when a read command dies without explaining why. This helps distinguish a killed `cat` process from a stopped, missing, or out-of-memory container.

**Data flow**: It receives a sandbox handle, asks Docker to inspect the container’s status, exit code, and out-of-memory flag, and returns a human-readable sentence. If inspection itself fails, it returns Docker’s inspection failure instead of guessing.

**Call relations**: `DockerCarrier.read` calls this only for unusual read failures with no error text. It uses `_docker` to run `docker inspect`.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 521–527)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one of the project’s structured file operations inside the Docker sandbox. These operations use the `ufo fs` helper rather than ad hoc shell commands.

**Data flow**: It receives a handle, an operation name, and operation parameters. It passes the Docker carrier itself, the handle, and the request to the shared `ufo_fs_file_op` helper. The result is a dictionary describing the file operation’s output.

**Call relations**: This method plugs the Docker carrier into the SDK’s shared file-operation machinery. The helper can call back through the carrier’s execution behavior, so file operations get the same container pinning and revive behavior as normal commands.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `DockerCarrier.dial`  (lines 529–536)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that this Docker carrier cannot expose an in-sandbox network port to the outside world. It tells callers to use a carrier that supports external per-port access instead.

**Data flow**: It receives a handle and port number, but does not try to connect. It raises SandboxUnreachable with an explanation that Docker sandboxes here do not publish per-port host routes.

**Call relations**: Callers use `dial` when they want to reach a service running inside a sandbox, such as a preview server or browser debugging endpoint. In this carrier, the story ends immediately with a clear unsupported-feature error.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 538–552)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation’s container and removes its per-conversation Docker network. This frees scarce host resources while leaving the stopped container’s filesystem state available for later revival.

**Data flow**: It receives a conversation ID and, if known, a container ID. It stops the container, then removes the Docker network named for that conversation. It returns true if everything was released, including the case where the network was already gone, and false if stopping the container failed.

**Call relations**: `DockerCarrier._reclaim_idle` calls this while holding the conversation’s lifecycle lock. It uses `_network_name` to compute the network name and `_docker` to issue the stop and network removal commands.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 554–574)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Restarts a stopped sandbox container and reconnects it to its conversation network. This is the recovery path after idle reclaim has stopped a container.

**Data flow**: It receives a conversation ID and container ID. Under a lifecycle lock, it marks the conversation touched, ensures the Docker network exists, connects the container to that network, and starts the container. It returns true if the container is running again, false if Docker refuses the reconnect or start in an expected way, and raises for network setup failures.

**Call relations**: This helper is used by create, attach, command execution, reads, and writes whenever they find a stopped container. It calls `_ensure_network`, `_network_name`, and `_docker`, and its lock keeps it from interleaving with `_release`.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (_exec_with, attach, create, read, write).


##### `DockerCarrier._held_id`  (lines 576–584)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a sandbox name in any state, not just running. Reclaim needs this because stopped, paused, or exited containers can still be tied to resources or names.

**Data flow**: It receives a container name, asks Docker for any container matching that exact name, and returns the ID text if found. If Docker itself fails, it raises a runtime error instead of pretending no container exists.

**Call relations**: `DockerCarrier._reclaim_idle` calls this before releasing an idle conversation. It uses `_docker` to run the container lookup.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 586–595)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds a stopped container for a conversation name. This tells the carrier whether it can revive an existing sandbox instead of creating a new one.

**Data flow**: It receives a container name, asks Docker for an exited container with that exact name, and returns its ID or None. A Docker command failure becomes a runtime error.

**Call relations**: `DockerCarrier.create` and `DockerCarrier.attach` call this after checking for a running container. It uses `_docker` for the Docker lookup.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 597–608)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds a currently running container for a conversation name. It treats Docker command failures as real errors, not as absence.

**Data flow**: It receives a container name, asks Docker for a running container with that exact name, and returns its ID or None. If Docker cannot answer, it raises a runtime error so callers do not accidentally try to create a duplicate container.

**Call relations**: `DockerCarrier.create` and `DockerCarrier.attach` use this as their first lookup step. It calls `_docker` to query Docker.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 610–611)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Docker network name for one conversation. This gives every conversation its own predictable bridge network.

**Data flow**: It receives a conversation UUID and combines the carrier’s network prefix with the UUID’s compact hexadecimal form. It returns the resulting Docker network name as a string.

**Call relations**: `DockerCarrier.create`, `_revive`, and `_release` call this whenever they need to create, reconnect, or remove the conversation’s network.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 613–624)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if necessary. It is safe when two creates race, because Docker may report that the network already exists and that is acceptable.

**Data flow**: It receives a network name, asks Docker whether that network already exists, and returns if it does. If not, it runs `docker network create`. Any failure other than an already-exists race is raised as a runtime error.

**Call relations**: `DockerCarrier.create` calls this before launching a new container, and `_revive` calls it before reconnecting a stopped one. It uses `_docker` for both listing and creating networks.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 626–639)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current proxy certificate authority inside the container. This lets HTTPS tools in the sandbox trust the local egress proxy, which may inspect and rewrite allowed requests.

**Data flow**: It receives a container ID and certificate text. It runs a root command inside the container that writes the certificate file and updates the system certificate store. If the command fails, it raises an error with Docker’s message.

**Call relations**: `DockerCarrier.create` calls this for both new and reused containers. That matters because the proxy certificate can change when the host process restarts, while the container may persist.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._prepare_mounts`  (lines 641–668)

```
async def _prepare_mounts(self, container_id: str, conversation_id: UUID) -> None
```

**Purpose**: Sets up ownership and runtime directories inside the container so the sandbox user can work safely. It also ensures a private session file exists with strict permissions.

**Data flow**: It receives a container ID and conversation ID. It computes the runtime directory, then runs a root shell command inside the container to fix workspace ownership if needed, create parent runtime directories, create or repair the session file, and make the runtime root private to the sandbox user. If setup fails, it raises a runtime error.

**Call relations**: `DockerCarrier.create` and `DockerCarrier.attach` call this after a container is available. It uses `_docker` to perform the setup and `sandbox_runtime_root` to know where the conversation runtime area belongs.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `manifest`  (lines 671–676)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It announces that this file provides a carrier named `docker` and tells the system how to construct it.

**Data flow**: It takes no input. It creates a Manifest containing the extension name, version, and a CarrierSpec whose factory is DockerCarrier. The Manifest is returned to the loader.

**Call relations**: The extension loader calls this when discovering available sandbox backends. The returned Manifest connects the external carrier name `docker` to the DockerCarrier class defined in this file.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox startup, command execution, file transfer, lease renewal, and service dialing`

A sandbox is the isolated computer where a conversation’s tools and files live. This file is the E2B version of that sandbox layer. Without it, a deployment configured to use E2B could not start a workspace, reconnect to an old one, run commands, stream files, or expose a service running inside the sandbox.

The main class, E2BCarrier, acts like a careful travel agent between UFO and E2B’s cloud service. It chooses the right E2B template for the requested sandbox size, opens or reconnects to the sandbox, installs the UFO client binary, installs the proxy certificate, makes sure `/workspace` exists, and adds safety limits so user work cannot starve the sandbox service itself.

A major concern here is the E2B lease clock. E2B pauses idle sandboxes after a timeout, but paused sandboxes can later resume with their disk intact. This file keeps a local record of when each sandbox lease should still be valid and renews it when needed. That avoids unnecessary remote calls while still preventing a sandbox from pausing during important work.

Command execution is also defensive. Commands are started in their own process group so a timeout can kill the whole tree, not just the shell. If a sandbox stops answering, the carrier marks it as temporarily suspicious and probes it before trusting it again. File reads stream bytes in chunks, so large files do not need to fit in memory.

#### Function details

##### `E2BCommandHandle.wait`  (lines 180–180)

```
async def wait(self) -> E2BCommandResult
```

**Purpose**: This protocol method represents waiting for a command that has already been started in the sandbox. It is used when the caller needs the command’s final output and exit code after launching it in the background.

**Data flow**: It starts with a running command handle that already knows the command’s process id. Waiting on it produces a command result containing standard output, standard error, and the exit code.

**Call relations**: E2BCarrier._exec_with launches commands in the background so it can learn their process id, then uses this wait step to collect the normal result if the command finishes.


##### `E2BCommands.run`  (lines 201–210)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None, background: Literal[False]=False) -> E2BCommandResult
```

**Purpose**: This protocol method describes E2B’s command runner. It sends a shell command into the sandbox and either returns the finished result or, when run in the background, a handle to the running command.

**Data flow**: It receives a command string plus optional working directory, environment variables, user name, timeout, and background flag. E2B runs that command inside the sandbox and returns either the completed output or a running-command handle.

**Call relations**: Many E2BCarrier methods rely on this SDK operation: preparation commands use it for setup, execution uses it for user commands, stop logic uses it to send kill signals, and health probing uses it to check whether the sandbox still answers.


##### `E2BFileStream.__aiter__`  (lines 217–217)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: This protocol method describes a streamed file reader that can be looped over asynchronously. It lets the caller receive a file as small byte chunks instead of loading the whole file at once.

**Data flow**: It starts with an open file stream from E2B. Iterating over it yields chunks of bytes until the remote file has been fully read.

**Call relations**: E2BCarrier.read receives this stream from E2BFiles.read and passes its chunks onward to the rest of the system.


##### `E2BFileStream.aclose`  (lines 219–219)

```
async def aclose(self) -> None
```

**Purpose**: This protocol method closes an open streamed file connection. It matters because a stopped or partial read still needs to release the remote connection cleanly.

**Data flow**: It receives the existing stream object and closes the underlying connection. It returns no file content, but it frees the transport resources.

**Call relations**: E2BCarrier.read calls this in a final cleanup step, so the stream is closed whether the caller reads the whole file or stops early.


##### `E2BFiles.write`  (lines 223–223)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: This protocol method writes text or bytes into a file inside the E2B sandbox. It is the safe path for sending raw bytes, since command execution only accepts shell strings.

**Data flow**: It takes a sandbox path, data, and optionally a user name. E2B writes that data into the sandbox filesystem and returns an SDK-specific acknowledgement.

**Call relations**: E2BCarrier.write uses this for user file uploads, and setup code uses it to place the UFO client binary and certificate material inside the sandbox.


##### `E2BFiles.read`  (lines 225–225)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: This protocol method opens a file inside the sandbox for reading. In this file it is used in streaming mode so large outputs can be copied out piece by piece.

**Data flow**: It takes a path and a requested format. E2B opens the file and returns a stream object that yields bytes.

**Call relations**: E2BCarrier.read calls this after making sure the sandbox lease is long enough for the transfer.


##### `E2BSandbox.get_host`  (lines 234–234)

```
def get_host(self, port: int) -> str
```

**Purpose**: This protocol method asks E2B for the public host name that reaches a port inside the sandbox. It is used when something running in the sandbox, such as a browser or dev server, must be contacted from outside.

**Data flow**: It receives a port number and formats or returns the external host for that port. The output is a host name, not a network connection.

**Call relations**: E2BCarrier.dial calls it after renewing the sandbox lease, then wraps the host with TLS and access-token information.


##### `E2BSdk.create`  (lines 238–247)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes creating a brand-new E2B sandbox from a named template. It is used when no usable previous sandbox exists for the conversation.

**Data flow**: It receives the template reference, lease timeout, metadata, lifecycle settings, network settings, and API key. E2B creates a sandbox and returns an object for talking to it.

**Call relations**: E2BCarrier._resume_or_open calls this when reconnecting is not possible or no resume id exists.


##### `E2BSdk.connect`  (lines 249–255)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method reconnects to an existing E2B sandbox. In E2B, this also resumes a paused sandbox and sets or extends its lease.

**Data flow**: It receives a sandbox id, desired timeout span, and API key. E2B returns a live sandbox object or reports that the sandbox no longer exists.

**Call relations**: E2BCarrier._connected wraps this method with retries and an overall timeout, and all reconnect or lease-renewal paths go through that wrapper.


##### `E2BCarrier.create`  (lines 307–391)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This opens the sandbox for a conversation and returns the handle the rest of the system will use. It may reconnect to a stored sandbox, reuse a still-live in-process one, or create a fresh E2B sandbox.

**Data flow**: It receives a SandboxSpec containing the conversation id, possible resume id, size, proxy settings, token, environment, and turn id. It chooses or opens the sandbox, prepares it if needed, records its lease, and returns a SandboxHandle with the sandbox id and runtime information.

**Call relations**: This is the main entry into the carrier for starting a turn’s sandbox. It calls _leased to check the local cache, _resume_or_open to obtain a sandbox, _ensure_client and preparation helpers to make it usable, and _drop if setup proves the cached lease cannot be trusted.

*Call graph*: calls 6 internal fn (_drop, _ensure_client, _leased, _prepare_runtime, _prepare_strictly, _resume_or_open); 8 external calls (__init__, __init__, __init__, timeout, emit_metric, log, egress_proxy_env, sandbox_runtime_root).


##### `E2BCarrier.attach`  (lines 393–418)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reconnects to an already-known sandbox only if the caller provides a resume id. It is for read-only or attachment-style flows that should not create an empty replacement sandbox when the old one is gone.

**Data flow**: It receives a SandboxSpec. If there is no resume id, it returns None. If E2B still has the sandbox, it reconnects, records a fresh lease, and returns a SandboxHandle; if E2B says the sandbox is gone, it clears the local cache and returns None.

**Call relations**: This uses _connected directly so it gets the provider’s current answer instead of trusting the local cache.

*Call graph*: calls 1 internal fn (_connected); 3 external calls (__init__, __init__, sandbox_runtime_root).


##### `E2BCarrier._resume_or_open`  (lines 420–460)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: This chooses between reconnecting to an existing sandbox and creating a new one. It protects the conversation from being permanently stuck on a sandbox id that E2B no longer has.

**Data flow**: It receives the sandbox spec and an optional resume id. If an id exists, it tries to connect to it; if that fails because the sandbox is missing, it logs the miss. Then it looks up the right template for the requested size, creates a new sandbox, checks that E2B supplied a traffic token, and returns the sandbox.

**Call relations**: E2BCarrier.create calls this before preparation. It hands reconnects to _connected, but calls the SDK create operation directly for new sandboxes.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 462–478)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: This prepares a sandbox and treats failure as fatal. It is used for fresh sandboxes or cached sandboxes whose successful setup has not already been proven.

**Data flow**: It receives a sandbox and its spec. It runs preparation, retries only certain transport failures, logs and emits a metric on retries, and either returns after success or drops the local lease and raises the error.

**Call relations**: E2BCarrier.create calls this when it cannot safely defer preparation. It delegates the actual setup to _prepare and uses _drop when preparation failure means the lease should not be trusted.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 480–542)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: This reconnects to an existing E2B sandbox with bounded retries. It exists because a remote provider call can fail by silence, and callers need a clear limit on how long they wait.

**Data flow**: It receives the conversation id, sandbox id, and lease span to request. It repeatedly calls the SDK connect operation on transport errors, waits between attempts, stops after the retry or total-time limit, and returns the connected sandbox or raises the provider error.

**Call relations**: Reconnect and renewal paths all pass through this function: _resume_or_open uses it at startup, attach uses it for attachment, and _sandbox uses it when a lease must be refreshed.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 4 external calls (sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 544–546)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This is the full preparation recipe for a sandbox. It makes sure both the UFO client and the runtime environment are ready.

**Data flow**: It receives a sandbox and certificate text. It first ensures the expected client binary is installed, then prepares the runtime pieces such as trust, workspace, and resource limits.

**Call relations**: _prepare_strictly calls this during strict setup. It splits the work between _ensure_client and _prepare_runtime.

*Call graph*: calls 2 internal fn (_ensure_client, _prepare_runtime); called by 1 (_prepare_strictly).


##### `E2BCarrier._prepare_runtime`  (lines 548–553)

```
async def _prepare_runtime(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This sets up the parts of the sandbox operating environment that commands rely on. It installs proxy trust, ensures `/workspace` exists, and applies workload limits.

**Data flow**: It receives a sandbox and the proxy certificate. It writes and installs the certificate, creates or fixes the workspace directory, and caps workload memory and process counts.

**Call relations**: It is called by _prepare for strict preparation and directly by create when a resumed sandbox gets a bounded, best-effort recheck.

*Call graph*: calls 3 internal fn (_cap_workload, _ensure_workspace, _install_ca); called by 2 (_prepare, create).


##### `E2BCarrier._ensure_client`  (lines 555–580)

```
async def _ensure_client(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure the sandbox contains the exact UFO client binary expected by this server. That matters because all remote workloads enter through the `ufo` command.

**Data flow**: It receives a sandbox. It computes the SHA-256 fingerprint of the local client binary, checks whether the sandbox binary matches, and if not, uploads and atomically installs the replacement. It remembers successful sandboxes so it does not repeat the check in the same process.

**Call relations**: create may call it directly for resumed sandboxes, and _prepare calls it during strict setup. It uses E2B file writing for upload and command running for verification and installation.

*Call graph*: called by 2 (_prepare, create); 2 external calls (sha256, uuid4).


##### `E2BCarrier._leased`  (lines 582–597)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: This reads the local lease cache and removes expired entries. It keeps the process from holding references forever to sandboxes from old conversations.

**Data flow**: It receives a conversation id. It looks up that conversation’s cached lease, sweeps all expired leases from the map, and returns the original lease if one was present.

**Call relations**: create uses it when deciding whether there is an in-process sandbox to reuse, and _sandbox uses it before deciding whether a remote reconnect is necessary.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 599–607)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This installs the proxy certificate authority into the sandbox’s trusted certificate store. That lets programs inside the sandbox trust the proxy’s TLS certificates.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path, runs the install command as root, and raises a clear RuntimeError if the command fails.

**Call relations**: _prepare_runtime calls this before workspace and resource setup because networked commands depend on trusted proxy TLS.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._ensure_workspace`  (lines 609–618)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure `/workspace` exists and belongs to the normal sandbox user. That directory is the conversation’s working disk inside the sandbox.

**Data flow**: It receives a sandbox. It runs a root command to create the directory and set ownership, returning nothing on success or raising a clear error if setup fails.

**Call relations**: _prepare_runtime calls this so later command execution can use `/workspace` as its working directory.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._cap_workload`  (lines 620–630)

```
async def _cap_workload(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This puts memory and process-count limits on user workload groups inside the sandbox. The goal is to stop user commands from exhausting the sandbox so badly that E2B’s own command service cannot respond.

**Data flow**: It receives a sandbox. It runs a root command that calculates safe memory limits and writes memory and process ceilings into Linux control-group files. On command failure it raises a clear setup error.

**Call relations**: _prepare_runtime calls this as the last runtime safety step.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier.exec`  (lines 632–680)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a normal user command inside the sandbox. It routes the command through the shared execution helper with the user’s network proxy environment applied.

**Data flow**: It receives a sandbox handle, argument list, and timeout. It passes them to _exec_with without a root user override and returns an ExecResult with output, exit code, and possible timeout information.

**Call relations**: This is the public command-execution method for ordinary sandbox work. _exec_with does the detailed leasing, launching, timeout mapping, and cleanup.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier.exec_skill`  (lines 682–686)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a command as root for server-carried skill setup or synchronization. It exists for trusted setup work that needs administrator permissions inside the sandbox.

**Data flow**: It receives a sandbox handle, argument list, and timeout. It passes them to _exec_with with the user set to root and returns the resulting ExecResult.

**Call relations**: Like exec, it delegates the hard parts to _exec_with, but chooses root execution instead of normal user execution.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier._exec_with`  (lines 688–735)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, user: str | None) -> ExecResult
```

**Purpose**: This is the real command runner. It renews the lease, checks whether a previously silent sandbox is responsive, starts the command in its own process group, waits for completion, and turns E2B exceptions into the system’s standard ExecResult.

**Data flow**: It receives a handle, command arguments, timeout, and optional user. It reconnects or reuses the sandbox, builds a quoted shell command, launches it in the background, records its process group, waits for it, and returns stdout, stderr, and exit code. On timeout it tries to kill the process group and returns a timeout exit code; on cancellation or unknown transport failure it drops the lease.

**Call relations**: exec and exec_skill both call this. It relies on _sandbox for leasing, _still_there for silent-box checks, _stop_group for deadline cleanup, _forget_group for bookkeeping, _mark_silent when the command channel does not answer, and _drop when the cached lease should no longer be trusted.

*Call graph*: calls 6 internal fn (_drop, _forget_group, _mark_silent, _sandbox, _still_there, _stop_group); called by 2 (exec, exec_skill); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.stop_commands`  (lines 737–759)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: This stops commands still known to be running for a specific turn. It is used after a real member cancellation, not for every internal task cancellation, because some cancellations may be retried and should leave work intact.

**Data flow**: It receives a sandbox handle. It removes the recorded process groups for that container and turn; if none exist, it returns without a provider call. Otherwise it gets the sandbox and sends a stop signal to each recorded group.

**Call relations**: It uses _sandbox only when there is something to stop, then calls _stop_group for each process group. It complements _exec_with, which deliberately does not kill work on a generic asyncio cancellation.

*Call graph*: calls 2 internal fn (_sandbox, _stop_group).


##### `E2BCarrier._forget_group`  (lines 761–771)

```
def _forget_group(self, handle: SandboxHandle, pid: int) -> None
```

**Purpose**: This removes a process group from the local “still running” record after the command has ended or has already been stopped. It prevents later cancellation cleanup from signaling a process id that may have been reused.

**Data flow**: It receives a handle and process id. It finds the entry for that container and turn, removes the process id, and deletes the turn entry if no groups remain.

**Call relations**: _exec_with calls this in its cleanup path for commands that are no longer intentionally left running.

*Call graph*: called by 1 (_exec_with).


##### `E2BCarrier._stop_group`  (lines 773–799)

```
async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int, user: str | None) -> None
```

**Purpose**: This sends a hard kill signal to an entire command process group inside the sandbox. A process group is like a family label for the command and its child processes, so this reaches more than just the shell.

**Data flow**: It receives the sandbox, container id, process id, and optional user. It runs `kill -9` against the negative process id, which targets the whole group. If the sandbox does not answer or the stop fails, it records a metric and may mark the container as silent, but it does not raise over the caller’s original timeout or cancellation.

**Call relations**: _exec_with calls this when a command deadline fires after launch. stop_commands calls it when a turn cancellation should stop all known work for that turn.

*Call graph*: calls 1 internal fn (_mark_silent); called by 2 (_exec_with, stop_commands); 1 external calls (emit_metric).


##### `E2BCarrier._mark_silent`  (lines 801–805)

```
def _mark_silent(self, container_id: str) -> None
```

**Purpose**: This remembers that a sandbox stopped answering command-channel requests. The mark lasts only for a short time so a heavily loaded but recovering sandbox is not condemned forever.

**Data flow**: It receives a container id. It stores an expiration time in the silent map based on the current clock and configured silent-mark duration.

**Call relations**: _exec_with marks a sandbox silent if a command launch never answers, and _stop_group marks it silent if even the cleanup kill command times out. _still_there later checks this mark.

*Call graph*: called by 2 (_exec_with, _stop_group).


##### `E2BCarrier._still_there`  (lines 807–837)

```
async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None
```

**Purpose**: This probes a sandbox that was recently marked silent before committing another full command timeout to it. It asks one tiny question: can the command channel answer at all?

**Data flow**: It receives the sandbox object and container id. If there is no active silent mark, it returns. If the mark has expired, it clears it. If the mark is active, it runs a short `true` command; success clears the mark, while failure raises SandboxUnreachable.

**Call relations**: _exec_with calls this before launching a command. It emits a metric if the sandbox still cannot answer and logs when an old silent mark expires.

*Call graph*: called by 1 (_exec_with); 3 external calls (__init__, emit_metric, log).


##### `E2BCarrier.write`  (lines 839–850)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This uploads bytes into a file inside the sandbox. It uses E2B’s filesystem API rather than shell commands so binary content does not have to be squeezed through command-line quoting.

**Data flow**: It receives a sandbox handle, destination path, and bytes. It obtains a sandbox lease long enough for the upload margin, writes the content, and returns nothing. If the write fails, it drops the cached lease and raises the error.

**Call relations**: It calls _sandbox for lease and reconnect logic, then uses the E2B file API. _drop is used when a failed provider call means the local lease should not be trusted.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 852–872)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This streams a file out of the sandbox in chunks. It is designed for large files, because the caller can consume the file piece by piece instead of loading it all into memory.

**Data flow**: It receives a sandbox handle and path. It obtains a long enough lease, opens the E2B file stream, converts E2B’s missing-file error into Python’s FileNotFoundError, yields each byte chunk, and always closes the stream when finished or interrupted.

**Call relations**: It calls _sandbox before opening the stream and _drop if the provider call fails unexpectedly. The stream object’s async iterator and close method carry the actual bytes and cleanup.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 874–879)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs higher-level file operations through the UFO client inside the sandbox. It provides the carrier’s implementation of generic file actions such as operations exposed by `ufo fs`.

**Data flow**: It receives a handle, operation name, and parameters. It hands them to the shared ufo_fs_file_op helper, which runs the correct in-sandbox command and returns a dictionary result.

**Call relations**: This method is a thin bridge from the carrier interface to the common sandbox file-operation helper.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `E2BCarrier.dial`  (lines 881–902)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This returns the outside address for reaching a service running on a port inside the sandbox. It is used for things like browser debugging ports or preview servers.

**Data flow**: It receives a sandbox handle and port. It renews the sandbox lease for a longer dial span, asks E2B for the host name, attaches TLS and the traffic-access token header if present, and returns a DialTarget. If the sandbox is gone, it raises SandboxUnreachable.

**Call relations**: It calls _sandbox to make sure the sandbox will stay awake while an external client uses the address, then uses E2BSandbox.get_host to form the target.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 904–946)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: This returns a usable sandbox object with a lease that covers the work about to happen. It avoids reconnecting before every operation, but renews when the current lease is missing, stale, for the wrong sandbox, or too short.

**Data flow**: It receives a sandbox handle, the number of seconds the next operation needs, and an optional minimum lease span. It checks the local lease cache; if the cached sandbox is right and long enough, it returns it. Otherwise it removes the cache entry, reconnects through _connected, records the new lease, logs the renewal, and returns the sandbox.

**Call relations**: Command execution, file reads, file writes, service dialing, and stop_commands all call this before touching E2B. It uses _leased for cache cleanup and _connected for the remote reconnect.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 5 (_exec_with, dial, read, stop_commands, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 948–953)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: This forgets a cached lease after a provider call failed. It does not delete the actual sandbox; it only stops this process from trusting a local lease that may no longer reflect reality.

**Data flow**: It receives a conversation id and a short label for what was happening. It removes that conversation’s lease from the local map and logs the drop.

**Call relations**: create, _prepare_strictly, _exec_with, read, and write call this when failures mean future operations should reconnect instead of reusing the cached sandbox object.

*Call graph*: called by 5 (_exec_with, _prepare_strictly, create, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 956–973)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: This parses the E2B template configuration from an environment-variable string. It makes sure every supported sandbox size has exactly a declared E2B template.

**Data flow**: It receives a comma-separated string like `small=ref,medium=ref,large=ref`. It splits each entry into a size and template reference, validates the format, checks that the set of sizes matches the system’s known sandbox sizes, and returns a dictionary.

**Call relations**: build_e2b_carrier uses this to configure the carrier, and e2b_runtime_digest uses it to produce a stable fingerprint of the runtime template map.

*Call graph*: called by 2 (build_e2b_carrier, e2b_runtime_digest).


##### `e2b_runtime_digest`  (lines 976–984)

```
def e2b_runtime_digest() -> str
```

**Purpose**: This computes a stable digest, or fingerprint, of the selected E2B template map. The system can use that fingerprint to identify which sandbox runtime configuration this process is using.

**Data flow**: It reads E2B_TEMPLATES from the environment, parses it, serializes the resulting map in a stable JSON order, hashes it with SHA-256, and returns the digest string.

**Call relations**: manifest includes this function in the carrier registration so the broader system can ask for the runtime digest when needed. It depends on sandbox_templates for validation.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (sha256, dumps).


##### `build_e2b_carrier`  (lines 987–996)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: This constructs the E2BCarrier from environment configuration. It is the factory used when the manifest-selected carrier backend is `e2b`.

**Data flow**: It reads the E2B API key and template map from environment variables, validates both, loads the compiled UFO client binary for the E2B Linux target, and returns an E2BCarrier containing those pieces.

**Call relations**: manifest registers this as the carrier factory. It calls sandbox_templates for configuration parsing and client_binary to find the client executable that will be installed into sandboxes.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (__init__, client_binary).


##### `manifest`  (lines 999–1012)

```
def manifest() -> Manifest
```

**Purpose**: This declares the E2B extension to the host system. It tells the system that a carrier named `e2b` exists, how to build it, what sizes it supports, and that it runs off-cluster.

**Data flow**: It creates a Manifest containing one CarrierSpec. That spec points to the factory, size list, carrier name, off-cluster flag, and runtime digest function.

**Call relations**: The extension loader calls this to discover and register the E2B carrier. From there, deployments that choose the E2B backend can use build_e2b_carrier to create the actual runtime carrier.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/browser.py`

`io_transport` · `startup and per-turn browser session use`

This file is a boundary, or “seam,” between the core system and whatever browser service is actually used. The core does not start Chrome itself and does not choose a default browser provider. Instead, it speaks through two small interfaces: a CdpProvider, which can create or reconnect to browser sessions, and a CdpLease, which represents one held browser session for a single turn.

CDP means Chrome DevTools Protocol: the control channel tools use to drive Chrome, inspect pages, click elements, and watch downloads. A CdpEndpoint is the connection address for that channel, plus any headers needed to connect, such as an authorization token.

The important idea is that different providers can behave very differently while looking the same to the rest of the system. A sandbox provider may point at Chrome already running inside the turn’s sandbox. A remote provider may create a hosted browser session and upload files to it. The lease hides those differences. It can say where Chrome should open a file, where downloads should go, how to fetch downloaded bytes, and how to release the session when the turn is over.

Without this file, the core would have to know too much about every browser setup. That would make local sandbox Chrome and remote hosted Chrome harder to swap, test, or extend.

#### Function details

##### `CdpLease.endpoint`  (lines 59–59)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This asks the lease for the actual Chrome DevTools Protocol endpoint the browser-driving code should connect to. It gives the URL and any required connection headers.

**Data flow**: The caller already has a CdpLease for the current turn. It asks for the endpoint; the concrete provider works out the right URL and headers; the caller receives a CdpEndpoint that can be used to open the browser control connection.

**Call relations**: The browser-driving extension calls this when it is ready to connect to Chrome. The implementation comes from whichever CdpProvider created the lease, so the same call can return a sandbox-local endpoint or a remote hosted-browser endpoint.


##### `CdpLease.token`  (lines 61–61)

```
async def token(self) -> str
```

**Purpose**: This asks for a durable text token that can be saved and later used to reconnect to the same browser session. The token might be a hosted session ID or a static endpoint name, depending on the provider.

**Data flow**: The caller has an active lease. It requests a token; the concrete lease turns its current browser session identity into a string; the caller receives that string and can persist it for recovery or reattachment.

**Call relations**: The browser extension or turn orchestration uses this after a lease is created so a later run can call CdpProvider.reattach. It is the bridge between a live browser session now and a possible reconnect later.


##### `CdpLease.place_file`  (lines 63–63)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: This tells the lease, “Here is a workspace file; where can this Chrome open it?” It hides whether Chrome can see the same filesystem or whether the file must be uploaded somewhere first.

**Data flow**: The caller provides a path and a read function that can produce the file’s bytes if needed. A sandbox-local implementation may simply return the same path. A remote implementation may call the read function, upload the bytes, and return the remote location Chrome can access.

**Call relations**: The browser-driving layer uses this before asking Chrome to open or upload a workspace file. The lease decides whether to do nothing, because Chrome shares the sandbox, or hand the bytes off through a remote provider’s storage system.


##### `CdpLease.download_dir`  (lines 65–65)

```
async def download_dir(self) -> str
```

**Purpose**: This asks where Chrome should write downloaded files during the turn. The answer depends on where Chrome is running.

**Data flow**: The caller asks the lease for a download directory. The concrete lease chooses a usable location for its Chrome instance, such as a sandbox path or a provider-owned remote storage area. The caller receives a path or location string to give to Chrome.

**Call relations**: The browser engine calls this while setting up Chrome download behavior. Later, when Chrome reports a completed download by its download identifier, the system uses CdpLease.fetch_download to get the bytes back.


##### `CdpLease.fetch_download`  (lines 67–67)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: This retrieves the bytes of a file Chrome downloaded. It uses Chrome’s download identifier, not a normal filename, because Chrome is configured to store completed downloads under that identifier.

**Data flow**: The caller provides the download guid, which is Chrome’s unique ID for the completed download. The concrete lease looks in the right place for that provider, either reading from the sandbox or calling a remote provider API. The caller receives the downloaded file as bytes.

**Call relations**: This is used after Chrome finishes a download. It pairs with CdpLease.download_dir: first the browser is told where to save downloads, then this method brings the saved file back into the turn’s control.


##### `CdpLease.aclose`  (lines 69–69)

```
async def aclose(self) -> None
```

**Purpose**: This releases the browser lease at the end of a turn. For some providers this may do nothing; for remote providers it may release or close a hosted session.

**Data flow**: The caller has finished using the browser for the turn and calls aclose. The concrete lease performs whatever cleanup its provider requires. Nothing is returned, but external resources such as hosted sessions may be released.

**Call relations**: Turn cleanup calls this after browser work is done. It hands control back to the provider implementation so local static endpoints can stay alive while remote sessions can be properly released.


##### `CdpProvider.lease`  (lines 82–82)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: This creates a fresh browser lease for a turn. It may use the turn’s sandbox if the browser lives inside that sandbox, or ignore it if the browser is remote or static.

**Data flow**: The caller may pass the current Sandbox, which represents the isolated workspace for the turn. The provider uses that context if it needs it, creates or locates a Chrome session, and returns a CdpLease that the rest of the turn can use.

**Call relations**: The turn setup code calls this when it needs a browser session. The returned CdpLease is then used by the browser extension to get the CDP endpoint, place files, configure downloads, and clean up afterward.


##### `CdpProvider.reattach`  (lines 84–84)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This tries to reconnect to a browser session named by a previously saved token. If the session no longer exists, it signals that the caller should create a new lease instead.

**Data flow**: The caller provides a token saved from CdpLease.token. The provider checks whether that named browser session is still alive. If it is, the provider returns a new CdpLease over that session; if not, it raises SessionGone so the caller knows recovery is impossible and should start fresh.

**Call relations**: Recovery or resume logic calls this before minting a new browser session. It connects back to the token produced by CdpLease.token, and it uses SessionGone as the clear handoff point when the old browser has disappeared.


### Shared Sandbox Setup
Shared package markers, cache addresses, client binary lookup, safe execution environments, preview routing, backend selection, and the common sandbox session interface support all sandbox carriers.

### `core/src/ufo/harness/sandbox/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package. That means other parts of the project can refer to modules inside `ufo.harness.sandbox` using normal Python import paths.

There is no code here, so it does not create objects, run setup steps, or change behavior directly. Its value is structural: it gives the project a stable place for sandbox-related code to live. Without it, depending on the Python version and import setup, code that tries to import from this package might fail or behave less predictably.

A simple analogy is a label on a drawer. The label does not contain the tools, but it tells the rest of the workshop that this drawer is a known place where sandbox tools belong.


### `core/src/ufo/harness/sandbox/cache.py`

`config` · `startup and sandbox configuration`

A sandbox often needs to fetch code from GitHub or download packages from public registries such as npm, PyPI, Go, RubyGems, Ubuntu, or Debian. This file is the small, central map that says which public services are allowed to go through UFO's cache and what internal hostname represents that cache.

The main idea is safety and repeatability. Instead of giving every sandbox open internet access, the system routes approved fetches through a cache daemon at `cache.ufo.internal`. You can think of it like a library desk: sandboxes ask the desk for public materials, and the desk checks what is allowed before retrieving or reusing them. That prevents the cache from being used as a tunnel to private addresses.

The file lists the Git hosts that may be mirrored, the package registry hosts that the proxy/cache should recognize, and the environment variable name used for a cache control token. It also provides one helper that builds Git rewrite settings so fetches from allowed Git hosts go through the cache while pushes still go directly to the real origin, and another helper that parses a configured cache daemon address. If this file were wrong or missing, sandboxes might bypass the cache, fail to fetch dependencies, or accidentally allow traffic to places the deployment did not intend.

#### Function details

##### `cache_git_config`  (lines 46–54)

```
def cache_git_config() -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the Git configuration entries that make reads from approved Git hosts go through the internal cache. It also adds a push rule so write operations, such as pushing commits, still target the real Git host instead of the cache.

**Data flow**: It reads the fixed cache hostname and the list of cache-approved Git hosts from this file. For each host, it creates two Git setting pairs: one that rewrites normal fetch URLs to the cache, and one that keeps push URLs aimed at the original host. It returns all of those setting pairs as an immutable tuple, so callers can apply them to a sandbox's Git configuration.

**Call relations**: No project-level caller is shown in the supplied graph, and this function does not hand work to other project functions. It is meant to be called by setup code when preparing a sandbox or Git environment, giving that code the exact settings needed to route fetches through the cache safely.


##### `parse_cache_daemon`  (lines 57–66)

```
def parse_cache_daemon(value: str | None) -> tuple[str, int] | None
```

**Purpose**: Turns a configured cache daemon address into a usable host and port. It deliberately raises an error for malformed values because a bad cache address is considered a deployment mistake, not something to silently ignore.

**Data flow**: It receives either a string like `hostname:port` or `None`. If the input is `None`, it returns `None`, meaning this deployment is not using a local cache daemon. Otherwise it splits the string at the last colon, checks that both the host and separator exist, converts the port text to a number, and returns `(host, port)`.

**Call relations**: No project-level caller is shown in the supplied graph, and this function does not call other project functions. It fits into deployment or startup configuration: code that reads the cache daemon setting can pass it here to get a clean address tuple or an immediate error if the setting is invalid.


### `core/src/ufo/harness/sandbox/client_binary.py`

`util` · `sandbox setup and local carrier startup`

A sandbox needs the `ufo` command-line program available inside it, and local sandbox tooling may also need to run that same program as a subprocess. Unlike a script, this program is a compiled binary, so it cannot just be copied from source code. It must already exist somewhere.

This file is the single shared place that decides where that binary comes from. It first checks an environment variable, `UFO_CLIENT_BINARY`, which is a way for a continuous integration job or deployment pipeline to say, “use this exact file I already built.” If that is set, the file must really exist, or the code raises an error.

If there is no override, it looks in the expected Rust build output folders under the repository’s `client` crate, checking release builds before debug builds. If the caller asked for a specific Rust target triple, meaning a named platform such as Linux on x86-64, it looks under that target’s build folder. If no target is requested, it can also fall back to an installed `ufo` found on the machine’s command path.

The important behavior is what it does not do: it does not start a build. Building could take minutes and may require tools that are not present, so a missing binary becomes a clear `RuntimeError` telling the user which `cargo build` command to run or which environment variable to set.

#### Function details

##### `client_binary`  (lines 32–59)

```
def client_binary(target: str | None=None) -> Path
```

**Purpose**: Finds the already-built `ufo` executable that should be used for a sandbox or for the local host. Someone uses this when they need a trustworthy path to the client binary without accidentally triggering a slow build.

**Data flow**: It takes an optional `target`, which names the platform the binary must run on. First it reads the `UFO_CLIENT_BINARY` environment variable; if that points to a real file, it returns that path. If not, it builds expected filesystem paths under the client crate’s `target` directory and checks release and debug output folders. For host-only use, it finally asks the operating system whether `ufo` is installed on the command path. If none of those checks find a file, it raises a `RuntimeError` with instructions for building or pointing to one.

**Call relations**: In the bigger flow, this function is the shared answer for code that needs to place or run the `ufo` binary. Internally, it uses `pathlib.Path` to construct and test file paths, and it uses `shutil.which` only for the host case, where an installed command on the machine may be acceptable.

*Call graph*: 2 external calls (Path, which).


### `core/src/ufo/harness/sandbox/exec_env.py`

`domain_logic` · `sandbox open for probe execution`

A sandboxed command often needs to talk to outside services: Git hosts, connector command-line tools, or keyed providers such as monitoring APIs. The tricky part is that the command needs enough information to authenticate, but the sandbox must not receive the real secret keys. This file solves that by exporting sentinels: harmless placeholder strings that an egress proxy later swaps for real credentials only when traffic leaves the sandbox. Think of them like coat-check tickets: the sandbox holds the ticket, while the real coat stays behind the counter.

The main piece is ProbeEnv, which gathers the stores that know about grants, command-line connector credentials, declared credential slots, and workspace credentials. Its exports method produces one combined dictionary of environment variables for a probe run. That dictionary includes the conversation id, Git configuration written through Git’s environment-based config mechanism, connector CLI variables for allowed accounts, and provider-specific variables for credential slots that are actually set.

The file is careful about absence and ambiguity. If no credential is stored, it exports nothing for that slot. If a host cannot be resolved, it warns and skips it. If a connector CLI could match more than one account, it refuses to silently choose one. This matters because a wrong export could either fail confusingly or, worse, act as the wrong account.

#### Function details

##### `ProbeEnv.exports`  (lines 60–74)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, acting_member_id: UUID | None=None) -> dict[str, str]
```

**Purpose**: Builds the full set of environment variables that an off-turn probe should see inside its sandbox. It combines conversation identity, Git proxy settings, connector CLI sentinels, and keyed provider sentinels into one safe export map.

**Data flow**: It receives a conversation id, a probe id, and optionally the member the probe is acting as. It reads the current workspace id, asks helper functions to produce Git settings, grant-based CLI variables, and credential-slot provider variables, then merges all of those into a single dictionary of string environment variables.

**Call relations**: This is the public entry point of the file. When a probe sandbox is opened, callers use this method to assemble the environment. It delegates the specialized work to _git_config_env, _git_credential_config, _grant_cli_env, and _keyed_provider_env, because each kind of export has different rules.

*Call graph*: calls 4 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env); 1 external calls (ws_current).


##### `_git_config_env`  (lines 77–84)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into the special environment-variable format that Git understands. This lets the sandbox influence Git behavior without writing a Git config file.

**Data flow**: It receives a list of Git key/value settings. It creates GIT_CONFIG_COUNT and then numbered GIT_CONFIG_KEY_n and GIT_CONFIG_VALUE_n entries, returning them as an environment dictionary.

**Call relations**: ProbeEnv.exports calls this after collecting the Git settings that should apply. It is the final formatting step that makes those settings readable by Git inside the sandbox.

*Call graph*: called by 1 (exports).


##### `_git_credential_config`  (lines 87–122)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Creates Git authentication header settings for credential slots that are present and usable. The headers contain sentinels, not real secrets, so the proxy can later replace them safely when Git contacts the host.

**Data flow**: It receives an optional credential store, the declared credential slots, and the workspace id. For each slot that declares Git basic-auth injection, it checks whether the slot is set, resolves the correct host, and returns Git config pairs for the slots that pass those checks. If something fails, it logs a warning and skips that slot.

**Call relations**: ProbeEnv.exports calls this before passing the returned settings into _git_config_env. It relies on slot_is_set and credential_host to avoid exporting unusable or misleading Git authentication data, and uses warn when a slot cannot be prepared.

*Call graph*: called by 1 (exports); 3 external calls (warn, credential_host, slot_is_set).


##### `_keyed_provider_env`  (lines 125–167)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Exports environment variables for external providers that need API keys or similar credentials, again using sentinels instead of real secrets. It may also export the resolved provider host, so client code inside the sandbox talks to the right service region or endpoint.

**Data flow**: It receives an optional credential store, credential slot declarations, and the workspace id. It walks the slots, keeps only those that declare environment-variable or host exports, verifies that a credential is actually stored, resolves the host, and returns the environment variables for usable slots. Missing, broken, or unresolved slots are skipped, with warnings for failures.

**Call relations**: ProbeEnv.exports calls this to add provider-specific variables to the sandbox environment. It uses slot_is_set and credential_host to decide what can be safely exported, and warn to make skipped-but-declared credentials visible to operators.

*Call graph*: called by 1 (exports); 3 external calls (warn, credential_host, slot_is_set).


##### `_grant_cli_env`  (lines 170–212)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], acting_member_id: UUID | None, run_id: UUID) -> dict[str, str]
```

**Purpose**: Builds environment variables for connector command-line tools, choosing the account a sandboxed CLI is allowed to use. It prefers the acting member’s private grant and falls back to shared workspace grants.

**Data flow**: It receives an optional grant store, the known connector CLI definitions, an optional acting member id, and the run id. It reads active grants, matches them by provider, chooses either one private account or one shared account, and returns the CLI environment variable set to that account’s sentinel. If more than one account could match, it logs the ambiguity and exports nothing for that provider.

**Call relations**: ProbeEnv.exports calls this when building the probe environment. It asks GrantStore.active_grants for current permissions, turns the chosen account into a sentinel with grant_sentinel, and uses log when it refuses to choose between multiple possible accounts.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (exports); 2 external calls (log, grant_sentinel).


### `core/src/ufo/harness/sandbox/preview.py`

`config` · `startup/config load`

The preview service is the part of the system that renders files or document reads that the sandbox already has permission to see. This file is like a small shared label maker: it gives the rest of the system the exact internal hostname, header name, and placeholder token they must use so the proxy can recognize preview requests safely.

The important idea is that the sandbox does not receive the real preview service secret. Instead, requests use a harmless fixed value, called a sentinel, meaning “replace me later.” The egress proxy, which is the controlled path for outgoing requests, recognizes the special preview host and swaps that sentinel for the real deploy token only for this internal service. That keeps the secret outside the sandbox while still allowing previews to work.

The file also includes `parse_preview_service`, which reads a deploy configuration value written as `host:port`. If no preview service is configured, it returns `None`. If a value is present but malformed, it raises an error instead of silently turning the feature off. That matters because a bad preview address is a deployment mistake, and hiding it would make previews fail later in a more confusing way.

#### Function details

##### `parse_preview_service`  (lines 16–25)

```
def parse_preview_service(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function turns the configured preview service address into a host and port pair the rest of the system can use. It also catches bad deploy configuration early, so a mistyped address fails clearly instead of causing mysterious preview failures later.

**Data flow**: It receives either a text value such as `example.internal:8443` or `None`. If the value is `None`, it returns `None`, meaning no preview service is configured. If text is present, it splits it at the final colon, checks that there is a host before the colon, converts the part after the colon into a number, and returns `(host, port)`. If the text is missing the colon or host, or if the port is not a valid number, it raises an error.

**Call relations**: This is a small configuration helper for setup code that needs to interpret the deploy’s preview service setting. It does not call other project functions; it simply validates and reshapes the raw setting before other parts of the system use it to connect the proxy to the preview service.


### `core/src/ufo/harness/sandbox/select.py`

`orchestration` · `startup / config load`

A sandbox backend is the place where isolated workspaces run, such as the built-in local machine backend or an extension-provided remote backend. This file is the chooser and safety checker for those backends. Without it, the system could start sandboxes on an unknown backend, accidentally register two backends under the same name, or switch providers in a way that strands old workspaces.

The main result is a `DeployCarriers` object. It contains the default carrier used for new sandboxes, its description, and a map of extra “resume” carriers used only to reopen existing sandboxes whose saved handles belong to older backends. This is like moving a workshop to a new building while keeping keys to the old storage units until everything there is gone.

The file starts with the built-in `local` carrier, then adds carriers declared by extension manifests. It rejects duplicate names so one name always means exactly one backend. It also rejects resume backends that repeat the default backend, and repeated resume names.

A special safety rule applies to remote carriers. If a backend runs away from this process, it must be given a public HTTPS proxy URL. That URL lets sandbox network traffic go through the project’s controlled proxy, where credentials and limits can be enforced. The code refuses to silently run remote sandboxes without that secure path.

#### Function details

##### `select_carriers`  (lines 25–49)

```
def select_carriers(config: Config, manifests: tuple[Manifest, ...]) -> DeployCarriers
```

**Purpose**: Builds the complete set of carriers this deployment will keep alive: one default carrier for new sandboxes and optional resume carriers for old saved sandboxes. It is used when the process starts so backend names from configuration and extensions become real carrier objects.

**Data flow**: It receives the project configuration and a group of extension manifests. It begins with the built-in `local` backend, adds every extension-provided carrier by name, checks for duplicate or invalid resume settings, then asks `_built` to create the default carrier and each resume carrier. It returns a `DeployCarriers` value containing the created carriers and their carrier descriptions.

**Call relations**: This is the main selector in the file. `select_carrier` calls it when only the default backend is needed. During its work, it delegates the final lookup, safety checks, and object creation for each named backend to `_built`.

*Call graph*: calls 1 internal fn (_built); called by 1 (select_carrier); 2 external calls (__init__, __init__).


##### `select_carrier`  (lines 52–55)

```
def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Returns only the default carrier and its description, for code that does not need to know about resume-only backends. It is a small convenience wrapper around the fuller selection process.

**Data flow**: It receives the same configuration and manifests as `select_carriers`. It calls `select_carriers`, takes the default carrier and its spec from the returned `DeployCarriers` object, and returns just those two items.

**Call relations**: This function sits on top of `select_carriers`. It lets callers use the same validation and construction path while ignoring the extra resume carrier information.

*Call graph*: calls 1 internal fn (select_carriers).


##### `_built`  (lines 58–78)

```
def _built(specs: dict[str, CarrierSpec], config: Config, name: str) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Looks up one named sandbox backend, verifies it is safe to use, and creates its carrier object. It is the guardrail that prevents unknown backends and unsafe remote sandbox setups from slipping through.

**Data flow**: It receives the known carrier specs, the configuration, and the backend name to build. It finds the matching spec or raises an error if none exists. If the selected carrier runs remotely, it checks that `[sandbox] proxy_public_url` exists and is an HTTPS URL with a host name. After those checks pass, it calls the carrier factory and returns the new carrier together with its spec.

**Call relations**: `select_carriers` calls this once for the default backend and once for each resume backend. `_built` uses URL parsing to inspect the configured public proxy URL, and it raises clear errors before any unsafe or unregistered carrier can be used.

*Call graph*: called by 1 (select_carriers); 2 external calls (__init__, urlparse).


### `core/src/ufo/harness/sandbox/session.py`

`domain_logic` · `cross-cutting: sandbox open, command execution, file access, skill loading, network proxy authorization`

A sandbox is the project’s controlled workbench. The agent can create files and run programs there, but it should not be able to read transcripts, compaction records, or other private host data. This file draws that boundary. It says that ordinary tools may work inside `/workspace`, and some trusted runtime code may use a separate `$UFO_HOME` runtime area for skills and tool output.

The file has three main jobs. First, it defines small value objects such as `SandboxSpec`, `SandboxHandle`, `ExecResult`, and token types. These are the labels and receipts that describe a sandbox, a command result, and who is allowed to use the network proxy. Second, it defines the `Carrier` protocol: a promise that any sandbox backend can create or attach to a sandbox, execute commands, stream files, and expose ports. Third, it provides the `Sandbox` class, the friendly object used by the rest of the system. `Sandbox` checks paths, prepares command runners, writes runtime files, loads skills, and delegates the actual work to the carrier.

A key design point is “late” creation. Some callers hold a sandbox object before the sandbox exists. The first real operation opens it, protected by a lock so two simultaneous operations do not create two sandboxes. This is like holding a keycard that only asks the building to unlock when you first reach the door.

#### Function details

##### `egress_proxy_env`  (lines 363–402)

```
def egress_proxy_env(proxy: 'ProxyEndpoint', run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside a remote sandbox send internet traffic through UFO’s egress proxy. This matters because the proxy meters traffic, applies authorization, and swaps placeholder model API keys for real ones outside the sandbox.

**Data flow**: It receives a proxy endpoint and a signed run token. It checks that the proxy has a public HTTPS address, turns that address into standard proxy environment variables, adds loopback exceptions, model-key sentinels, and certificate settings, then returns the completed environment dictionary.

**Call relations**: Sandbox-opening code uses this kind of environment when preparing a carrier-backed sandbox. It relies on URL parsing to validate and shape the proxy address before the carrier receives the command environment.

*Call graph*: 1 external calls (urlsplit).


##### `_basic_username`  (lines 408–414)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username from a `Proxy-Authorization: Basic ...` header. In this system, that username is where signed sandbox tokens are carried.

**Data flow**: It receives an authorization header string. It verifies that the header uses Basic authentication, decodes the base64 payload, takes the part before the password separator, and returns that username. Bad headers become `ValueError` exceptions.

**Call relations**: Both run-token and probe-token decoders call this first. After it pulls out the username, those decoders verify the signature and interpret the token contents.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 433–437)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Creates a run-token signer/verifier from the deployment secret stored in the process environment. It is used at server startup so all sandbox network tokens are tied to this deployment’s secret.

**Data flow**: It reads the token-secret environment variable. If the value is missing, it raises an error; otherwise it encodes the secret as bytes and returns a `RunTokenCodec`.

**Call relations**: The server startup path calls this while setting up the service. Later code uses the resulting codec to mint and read per-turn run tokens.

*Call graph*: called by 1 (run).


##### `RunTokenCodec.encode`  (lines 439–442)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a turn’s sandbox authority into a signed string suitable for proxy authentication. The signature prevents a sandbox or caller from inventing a different workspace, turn, or member identity.

**Data flow**: It receives a `RunToken` containing workspace, turn, and optional acting member IDs. It formats those fields into a domain-tagged payload, signs the bytes with the codec secret, and returns the signed token string.

**Call relations**: Sandbox-opening code calls this when preparing a sandbox for a turn. The proxy later receives the token and asks `from_proxy_auth` to verify and decode it.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 444–456)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads and verifies a run token presented to the egress proxy. It converts a Basic-auth header back into trusted workspace, turn, and member information.

**Data flow**: It receives a proxy authorization header. It extracts the Basic-auth username, verifies the signed token with the codec secret, splits the decoded payload, validates its token kind, converts IDs into UUID objects, and returns a `RunToken`. Any invalid token becomes a `ValueError`.

**Call relations**: The proxy uses this after `_basic_username` has recovered the signed username. It hands the trusted result to the proxy’s authorization and accounting logic.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `ProbeTokenCodec.encode`  (lines 491–497)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Creates a signed token for an off-turn sandbox probe, such as a recurring check that is not tied to a live turn row. The token carries its own expiry time.

**Data flow**: It receives a `ProbeToken` with workspace, conversation, probe, optional member, and expiration data. It formats those fields with a probe-specific token kind, signs the payload, and returns the signed token string.

**Call relations**: Probe-running code can put this token into proxy credentials. The proxy later uses `ProbeTokenCodec.from_proxy_auth` to recover and validate it.

*Call graph*: 1 external calls (sign_token).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 499–515)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Verifies a probe token from proxy authentication and reconstructs the probe authority it represents. This lets the proxy distinguish probe traffic from turn traffic.

**Data flow**: It receives a Basic-auth proxy header. It extracts the username, verifies the signature, checks the probe token kind, parses UUIDs and the expiration time, and returns a `ProbeToken`. Invalid or mismatched tokens become `ValueError` exceptions.

**Call relations**: It follows the same pattern as run-token decoding but uses the probe token domain. `_basic_username` supplies the signed username, and token verification supplies trusted contents.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `sandbox_handle_id`  (lines 603–608)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the backend-specific sandbox ID out of a stored handle only if that handle belongs to the expected backend. This prevents one carrier from trying to resume another carrier’s sandbox.

**Data flow**: It receives a backend name and a stored handle string. If the string begins with `<backend>:`, it returns everything after the separator; otherwise it returns `None`.

**Call relations**: Carrier setup and resume logic can use this before attempting an attach. It is paired with `sandbox_handle_backend`, which reads the prefix for routing.


##### `sandbox_handle_backend`  (lines 611–614)

```
def sandbox_handle_backend(value: str) -> str
```

**Purpose**: Reads the backend name from a stored sandbox handle. This is useful when multiple sandbox backends may be alive during a deployment change.

**Data flow**: It receives a stored handle string and returns the text before the first backend separator.

**Call relations**: Higher-level sandbox routing code can use this to choose the carrier that wrote the handle. `sandbox_handle_id` then extracts the backend’s own ID if the selected carrier matches.


##### `Carrier.create`  (lines 652–652)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the contract for creating or resuming a sandbox for a conversation. Concrete carriers implement it for their own backend.

**Data flow**: An implementation receives a `SandboxSpec` describing the conversation, image, workspace, proxy, environment, and resume details. It creates or attaches to the backend resource and returns a `SandboxHandle` that future operations can use.

**Call relations**: The abstract `Sandbox` wrapper eventually depends on a `SandboxSession` containing this returned handle. Backend implementations provide the real behavior.


##### `Carrier.attach`  (lines 654–660)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines the contract for attaching to an already-existing sandbox without creating a new one. This supports read-only or inspection flows that must not accidentally provision a fresh sandbox.

**Data flow**: An implementation receives a `SandboxSpec` with a resume identity. It either returns a usable `SandboxHandle` for the existing sandbox or `None` if it cannot be reached.

**Call relations**: Late or read-side sandbox logic can call this through a carrier when it wants to check for an existing sandbox. If it returns nothing, callers can treat the sandbox as absent.


##### `Carrier.exec`  (lines 662–664)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines how a backend runs one command inside the sandbox. Every command-facing helper eventually depends on this operation.

**Data flow**: An implementation receives a sandbox handle, an argument list, and a timeout. It runs the command in the backend and returns an `ExecResult` with output, exit code, and timeout information.

**Call relations**: `Sandbox` command helpers, runtime checks, file operations, and skill support all delegate actual execution to this carrier method.


##### `Carrier.write`  (lines 666–678)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how a backend writes bytes into the sandbox filesystem safely. It is used for workspace files and runtime files.

**Data flow**: An implementation receives a handle, an absolute sandbox path, and bytes. It writes those bytes into the backend, creating parent directories as needed, and raises an error if the path cannot be safely written.

**Call relations**: `Sandbox.write_file`, runtime-file helpers, and staged skill loading all call through this contract. Each carrier chooses the safest transfer method for its backend.


##### `Carrier.read`  (lines 680–691)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how a backend streams a file out of the sandbox without loading the whole file into host memory. This is important for large files.

**Data flow**: An implementation receives a handle and an absolute sandbox path. It yields chunks of bytes from that file or raises an appropriate error if the file cannot be read.

**Call relations**: `Sandbox.read_file`, runtime reads, and lower-level read helpers delegate to this method. The carrier owns the backend-specific stream mechanism.


##### `Carrier.dial`  (lines 693–702)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how callers outside the sandbox can reach a service running on a port inside it. This is needed for things like browser debugging endpoints or preview servers.

**Data flow**: An implementation receives a handle and an internal port number. It returns a `DialTarget` with the external host, whether TLS is used, and any required headers, or raises `SandboxUnreachable` if no route exists.

**Call relations**: `Sandbox.dial` forwards requests here. Extensions such as sandbox Chrome use the returned target to connect to in-sandbox services.


##### `Carrier.file_op`  (lines 704–714)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Defines how a backend runs one bounded file-tool operation inside the sandbox. These operations include reading windows of files, writing, editing, searching, and listing changes.

**Data flow**: An implementation receives a handle, an operation name, and parameters. It runs the operation against sandbox files and returns a parsed JSON-like dictionary, or raises a tool-facing error for recoverable failures.

**Call relations**: `Sandbox.run_ufo_fs` scopes paths first, then hands the operation to this carrier method. Many carriers can implement it by reusing `ufo_fs_file_op`.


##### `CommandStopping.stop_commands`  (lines 734–734)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines an optional carrier ability to stop commands that may keep running after the original execute call is cancelled. Not every backend needs this.

**Data flow**: An implementation receives a sandbox handle, usually carrying the turn ID to stop. It terminates only the commands associated with that turn and returns nothing.

**Call relations**: `Sandbox.stop_commands` and `_LateSandbox.stop_commands` call this only when the carrier declares that it supports command stopping.


##### `SkillLoading.load_skills`  (lines 741–743)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Defines an optional native carrier operation for loading skills into the sandbox runtime. A skill is a package of code the agent can use while working.

**Data flow**: An implementation receives a handle and a skill payload. It installs or makes available the requested skills and returns an `ExecResult`, normally containing JSON that names loaded skill roots.

**Call relations**: `Sandbox.load_skills` uses this path when the carrier supports it. Otherwise it falls back to staging files and running the bundled skill-loader program.


##### `SkillExecuting.exec_skill`  (lines 750–752)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines an optional carrier operation for running trusted skill-management programs with the needed privileges. It is separate from ordinary command execution because skill installation may require more authority.

**Data flow**: An implementation receives a handle, command arguments, and a timeout. It runs the privileged skill command and returns an `ExecResult`.

**Call relations**: `Sandbox._exec_skill` calls this after checking the carrier supports the protocol. Staged skill loading and system-skill syncing depend on it.


##### `SystemSkillSeeding.seed_system_skills`  (lines 759–759)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Defines an optional way for a carrier to seed system skills directly into a runtime filesystem created by the current process. This avoids later loading work for carriers that can prepare the filesystem themselves.

**Data flow**: An implementation receives a system-skill archive as bytes. It stores or extracts those skills into the backend’s runtime area and returns nothing.

**Call relations**: Carrier setup code can use this capability when available. The rest of `Sandbox.load_skills` still verifies and loads skills for backends that do not seed them.


##### `ufo_fs_file_op`  (lines 762–775)

```
async def ufo_fs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Provides a shared implementation of `Carrier.file_op` for sandbox images that can run the `ufo fs` command, with a fallback command name for older images. It avoids duplicating file-operation command setup in every carrier.

**Data flow**: It receives a carrier, handle, operation name, and parameters. It builds a `SandboxFileOperations` runner using the carrier’s `exec`, applies standard timeouts and document suffix rules, runs the requested operation, and returns the parsed result.

**Call relations**: Carrier implementations can call this from their `file_op` method. It hands actual process execution back to the carrier while centralizing command shape and result parsing.

*Call graph*: 1 external calls (__init__).


##### `host_argv`  (lines 783–795)

```
def host_argv(argv: tuple[str, ...], root: str) -> tuple[str, ...]
```

**Purpose**: Rewrites command arguments for carriers where `/workspace` is actually a host directory. It carefully changes only real `/workspace` path segments, not unrelated text that merely contains the same letters.

**Data flow**: It receives an argument tuple and a host root path. It scans each argument and replaces valid `/workspace` path occurrences with the host root, returning a new tuple.

**Call relations**: Host-path carriers use this when converting sandbox-style commands into commands that run directly on the host filesystem. It protects URLs and similarly sensitive strings from accidental rewriting.


##### `workspace_path`  (lines 798–806)

```
def workspace_path(path: str) -> str
```

**Purpose**: Normalizes a user- or tool-supplied path so it stays inside `/workspace`. This is one of the main safety gates that prevents file tools from escaping the conversation workspace.

**Data flow**: It receives a path that may be relative or absolute. It treats relative paths as inside `/workspace`, resolves `.` and `..` pieces with `_resolve_parts`, checks the result remains under `/workspace`, and returns the safe absolute path.

**Call relations**: Workspace writes, existence checks, scoped reads, and file operations call this before reaching the carrier. `rooted_path` also uses it when checking alternate allowed roots.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 5 (_read_scoped_file, file_exists, run_ufo_fs, write_file, rooted_path); 1 external calls (PurePosixPath).


##### `runtime_relative`  (lines 809–819)

```
def runtime_relative(path: str) -> PurePosixPath
```

**Purpose**: Validates a path meant to be relative to the conversation’s private runtime directory. It rejects absolute paths, parent-directory escapes, and normalized variants that do not exactly match the input.

**Data flow**: It receives a runtime-relative string. It asks the containment guard to resolve it under a fake root, converts the result back to a relative path, checks the spelling is unchanged, and returns a `PurePosixPath`.

**Call relations**: `_runtime_path` and `_runtime_display_path` call this before building real runtime paths. This keeps internal runtime file access inside the intended subtree.

*Call graph*: called by 2 (_runtime_display_path, _runtime_path); 2 external calls (PurePosixPath, contained_relative).


##### `sandbox_runtime_root`  (lines 822–824)

```
def sandbox_runtime_root(conversation_id: UUID) -> str
```

**Purpose**: Builds the default runtime root path for one conversation inside an isolated sandbox. This is where private run files live, separate from `/workspace`.

**Data flow**: It receives a conversation UUID and returns a string under `$UFO_HOME/runs/` using the UUID’s hex form.

**Call relations**: `_runtime_root` uses this when a sandbox handle does not already carry a custom runtime root.

*Call graph*: called by 1 (_runtime_root).


##### `shell_path`  (lines 827–832)

```
def shell_path(path: str) -> str
```

**Purpose**: Quotes a sandbox path so it can be safely placed in a shell command, while preserving `$UFO_HOME` expansion when that prefix is intentionally used.

**Data flow**: It receives a path string. If it begins with `$UFO_HOME/`, it quotes only the remainder and leaves the environment-variable prefix expandable; otherwise it shell-quotes the whole path.

**Call relations**: Command-building code can use this helper when it needs a safe shell spelling of sandbox paths.

*Call graph*: 1 external calls (quote).


##### `_runtime_root`  (lines 835–836)

```
def _runtime_root(handle: SandboxHandle) -> str
```

**Purpose**: Finds the runtime root for a sandbox handle. It respects a handle-specific runtime root when present and otherwise falls back to the standard per-conversation path.

**Data flow**: It receives a `SandboxHandle`. It returns `handle.runtime_root` if set; otherwise it calls `sandbox_runtime_root` using the handle’s conversation ID.

**Call relations**: Most runtime helpers call this before reading, writing, displaying, or scoping runtime paths.

*Call graph*: calls 1 internal fn (sandbox_runtime_root); called by 7 (_read_scoped_file, _run_staged_skill_load, _sync_system_skills, run_ufo_fs, write_runtime_path, _runtime_display_path, _runtime_path).


##### `_runtime_path`  (lines 839–840)

```
def _runtime_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds an absolute sandbox path for a runtime-owned file. It combines the runtime root with a validated relative path.

**Data flow**: It receives a sandbox handle and a relative path string. It finds the runtime root, validates the relative path with `runtime_relative`, joins them, and returns the absolute path string.

**Call relations**: Runtime file reads and writes, skill staging, system-skill syncing, output-directory setup, and existence checks all use this helper before calling a carrier.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 8 (_read_runtime_file, _run_staged_skill_load, _sync_system_skills, ensure_tool_output_dir, runtime_file_exists, runtime_path, write_runtime_file, write_runtime_path); 1 external calls (PurePosixPath).


##### `_runtime_display_path`  (lines 843–847)

```
def _runtime_display_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds the `$UFO_HOME`-style path shown to the agent for a runtime file. This gives the agent a reusable path without exposing backend-specific details.

**Data flow**: It receives a sandbox handle and a relative runtime path. It finds the runtime run ID, validates the relative path, and returns a display path like `$UFO_HOME/runs/<id>/...`.

**Call relations**: `Sandbox.runtime_display_path` calls this when code needs a human- or agent-facing name for an internal runtime file.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 1 (runtime_display_path); 1 external calls (PurePosixPath).


##### `rooted_path`  (lines 850–853)

```
def rooted_path(path: str, root: str) -> str
```

**Purpose**: Normalizes a path under an allowed root while preserving that root’s spelling. It is used when paths may refer to special readable areas beyond `/workspace`, such as runtime output or skills.

**Data flow**: It receives a path and a root prefix. It temporarily maps the path into `/workspace`, validates it with `workspace_path`, then maps it back to the original root spelling.

**Call relations**: `Sandbox.run_ufo_fs` and `_read_scoped_file` use this for allowed non-workspace read roots. It reuses the same escape-prevention logic as normal workspace paths.

*Call graph*: calls 1 internal fn (workspace_path); called by 2 (_read_scoped_file, run_ufo_fs).


##### `_resolve_parts`  (lines 856–865)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies path pieces while preventing `..` from climbing above the root. It is the small stack-based engine behind workspace path normalization.

**Data flow**: It receives path parts. It skips empty and `.` parts, pops one level for `..` when safe, raises an error if `..` would escape the root, and returns the cleaned list of parts.

**Call relations**: `workspace_path` calls this while deciding whether a supplied path is safely inside `/workspace`.

*Call graph*: called by 1 (workspace_path).


##### `Sandbox.conversation_id`  (lines 877–881)

```
def conversation_id(self) -> UUID
```

**Purpose**: Defines the property every sandbox object must expose: which conversation’s workspace it represents. This can be known even before a real sandbox has been created.

**Data flow**: A concrete sandbox subclass returns the UUID of its conversation. The base version raises because it is only an interface point.

**Call relations**: `SandboxSession`, `_LateSandbox`, and `_AuthorizedSandbox` each provide the real answer. Callers use the property without caring which form they hold.


##### `Sandbox.created`  (lines 884–886)

```
def created(self) -> bool
```

**Purpose**: Defines the property that says whether a real sandbox session has already been opened. This lets callers distinguish a planned sandbox from an active one.

**Data flow**: A concrete subclass returns a boolean. The base version raises because it has no stored session itself.

**Call relations**: `SandboxSession` always reports true, while late and authorized wrappers report whether the underlying late sandbox has opened.


##### `Sandbox.authorize`  (lines 888–896)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'Sandbox'
```

**Purpose**: Defines how to create a view of the same sandbox with different network authority and environment variables. This lets one sandbox be reused while commands act as the correct member or turn.

**Data flow**: A concrete subclass receives a new run token, environment variable names to clear, and variables to add. It returns a sandbox object that will use those values for future operations.

**Call relations**: `SandboxSession` rewrites an existing handle immediately. `_LateSandbox` creates an authorization wrapper that applies the change once the sandbox is opened.


##### `Sandbox._bound`  (lines 898–899)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Defines the internal step that turns any sandbox wrapper into a concrete `SandboxSession`. All real operations go through this so they work the same for already-open and late-created sandboxes.

**Data flow**: A concrete subclass either returns itself as a session or opens/reuses a session first. The base version raises because it has no binding behavior.

**Call relations**: Nearly every `Sandbox` operation calls `_bound` before delegating to the carrier. `_LateSandbox` is the important lazy implementation.

*Call graph*: called by 19 (_read_file, _read_runtime_file, _read_scoped_file, bash, bash_task, dial, ensure_tool_output_dir, file_exists, load_skills, python (+9 more)).


##### `Sandbox.runtime_path`  (lines 901–903)

```
async def runtime_path(self, relative: str) -> str
```

**Purpose**: Returns the real sandbox path for an internal runtime file. This is for trusted code that needs to address private run storage directly.

**Data flow**: It receives a relative runtime path. It binds the sandbox, validates and joins the path through `_runtime_path`, and returns the absolute runtime path.

**Call relations**: It depends on `_bound` so it creates a late sandbox if needed. It shares path safety with other runtime helpers.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.runtime_display_path`  (lines 905–907)

```
async def runtime_display_path(self, relative: str) -> str
```

**Purpose**: Returns the `$UFO_HOME`-style display path for an internal runtime file. This is useful when the agent should see or reuse a stable-looking path.

**Data flow**: It receives a relative runtime path. It binds the sandbox, builds a display path with `_runtime_display_path`, and returns it.

**Call relations**: It is the public wrapper around `_runtime_display_path`, using `_bound` to get the handle that names the current runtime root.

*Call graph*: calls 2 internal fn (_bound, _runtime_display_path).


##### `Sandbox.write_runtime_file`  (lines 909–912)

```
async def write_runtime_file(self, relative: str, content: bytes) -> None
```

**Purpose**: Writes trusted runtime-owned bytes outside the member workspace. This is used for engine data such as staged payloads or tool output.

**Data flow**: It receives a relative runtime path and bytes. It binds the sandbox, resolves the safe runtime path, and asks the carrier to write the content there.

**Call relations**: It uses the same carrier write path as workspace writes, but targets the private runtime root through `_runtime_path`.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.write_runtime_path`  (lines 914–922)

```
async def write_runtime_path(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to an already-resolved runtime path, after proving it still sits inside this conversation’s runtime root. This protects callers that already have an absolute path.

**Data flow**: It receives an absolute path and bytes. It binds the sandbox, checks that the path is inside the runtime root and not the root itself, converts it back to a relative path, rebuilds the safe runtime path, and writes through the carrier.

**Call relations**: It combines `_runtime_root`, `_runtime_path`, and the carrier write operation. It is stricter than `write_runtime_file` because its input is already absolute.

*Call graph*: calls 3 internal fn (_bound, _runtime_path, _runtime_root); 1 external calls (PurePosixPath).


##### `Sandbox.runtime_file_exists`  (lines 924–931)

```
async def runtime_file_exists(self, relative: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the runtime area. It uses the sandbox itself to answer, so it works across all carrier backends.

**Data flow**: It receives a relative runtime path. It binds the sandbox, resolves the target path, runs `test -f` inside the sandbox with a short timeout, and returns true if the command exits successfully.

**Call relations**: It depends on carrier command execution after `_runtime_path` has safely built the target.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.read_runtime_file`  (lines 933–935)

```
def read_runtime_file(self, relative: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts streaming a runtime-owned file. It returns an asynchronous stream so large files do not need to be loaded all at once.

**Data flow**: It receives a relative runtime path and returns the asynchronous iterator produced by `_read_runtime_file`.

**Call relations**: This is the public wrapper. `_read_runtime_file` performs the binding, path resolution, and carrier read.

*Call graph*: calls 1 internal fn (_read_runtime_file).


##### `Sandbox._read_runtime_file`  (lines 937–940)

```
async def _read_runtime_file(self, relative: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes from a private runtime file after resolving its safe path. It is the implementation behind `read_runtime_file`.

**Data flow**: It receives a relative runtime path. It binds the sandbox, resolves the runtime path, asks the carrier to read it, and yields each chunk it receives.

**Call relations**: `read_runtime_file` calls this. The actual file transport is supplied by the carrier’s `read` method.

*Call graph*: calls 2 internal fn (_bound, _runtime_path); called by 1 (read_runtime_file).


##### `Sandbox._commands`  (lines 942–949)

```
def _commands(self, bound: 'SandboxSession') -> SandboxCommands[ExecResult]
```

**Purpose**: Builds the common command runner used for bash, shell, and Python execution. It centralizes timeouts, Python isolation, containment bootstrap, and the supervisor command.

**Data flow**: It receives a bound `SandboxSession`. It creates a `SandboxCommands` object whose execute function calls the carrier’s `exec`, then returns that command helper.

**Call relations**: `bash`, `bash_task`, `sh`, and `python` call this before running their specific command shape. It keeps all those methods consistent.

*Call graph*: called by 4 (bash, bash_task, python, sh); 1 external calls (__init__).


##### `Sandbox.bash`  (lines 951–953)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a bash command inside the sandbox. This is the general command execution path for callers that need a shell.

**Data flow**: It receives a command string and optional timeout. It binds the sandbox, builds the shared command runner, runs the command, and returns an `ExecResult`.

**Call relations**: Sandbox Chrome support calls this to start or diagnose browser-related services. Internally it delegates command execution to `SandboxCommands`, which delegates to the carrier.

*Call graph*: calls 2 internal fn (_bound, _commands); called by 2 (lease, _bring_up_failure).


##### `Sandbox.bash_task`  (lines 955–962)

```
async def bash_task(self, command: str, base: str, *, detach: bool, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs or reattaches to a journaled bash task through the sandbox supervisor. This supports long-running or detachable command work.

**Data flow**: It receives a command, a task base name, a detach flag, and optional timeout. It binds the sandbox, builds the shared command runner, and asks it to run the supervised task.

**Call relations**: It follows the same `_bound` and `_commands` path as `bash`, but uses the supervisor-aware task mode.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.sh`  (lines 964–969)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX shell script inside the sandbox with each argument passed separately. Passing arguments separately avoids unsafe string interpolation.

**Data flow**: It receives a script, positional arguments, and optional timeout. It binds the sandbox, builds the command runner, executes the script with those arguments, and returns an `ExecResult`.

**Call relations**: It uses `_commands` like the other command helpers. Host-path carriers can still rewrite `/workspace` arguments because they remain separate argv elements.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.python`  (lines 971–982)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with the project’s containment guard injected. This ensures small in-sandbox helper programs use the same path safety rules as the host.

**Data flow**: It receives Python source, positional arguments, and optional timeout. It binds the sandbox, builds the command runner configured for isolated Python mode and bootstrap code, runs the program, and returns an `ExecResult`.

**Call relations**: It shares `_commands` with shell helpers. The injected bootstrap is important for helpers that touch files safely inside the sandbox.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.stop_commands`  (lines 984–990)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands associated with this sandbox view when the carrier supports explicit stopping. It is used for deliberate user cancellation, not every internal cancellation.

**Data flow**: It binds the sandbox. If the carrier implements `CommandStopping`, it asks the carrier to stop commands for the handle; otherwise it does nothing.

**Call relations**: The method bridges high-level cancellation handling to optional carrier behavior. `_LateSandbox` overrides it to stop existing commands without unnecessarily creating a sandbox.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.write_file`  (lines 992–994)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the conversation workspace after checking that the target path stays inside `/workspace`. This is the safe public write path for member-visible files.

**Data flow**: It receives a path and content bytes. It binds the sandbox, normalizes the path with `workspace_path`, and asks the carrier to write the bytes.

**Call relations**: It combines this file’s path guard with the backend-specific carrier write operation.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.load_skills`  (lines 996–1038)

```
async def load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Loads system and user skills into the sandbox’s runtime skill directory and returns the installed root paths. It verifies that the loader returns only expected skill names.

**Data flow**: It receives a skill payload. It binds the sandbox, uses a carrier-native loader if available or stages and runs the bundled loader otherwise, parses the JSON result, checks expected names and paths, optionally refreshes missing system skills from an archive, and returns a mapping of skill names to roots.

**Call relations**: Skill runtime code calls this when installing or loading skills. It may call `_run_staged_skill_load`, `_sync_system_skills`, and then `_run_staged_skill_load` again if the sandbox’s baked system skills are stale.

*Call graph*: calls 3 internal fn (_bound, _run_staged_skill_load, _sync_system_skills); called by 2 (install_skill, load_skills); 1 external calls (loads).


##### `Sandbox._run_staged_skill_load`  (lines 1040–1062)

```
async def _run_staged_skill_load(self, bound: 'SandboxSession', payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Implements the fallback skill-loading path by writing the skill payload into the sandbox and running the bundled loader program there. It avoids passing large or sensitive payloads through command-line arguments.

**Data flow**: It receives a bound session and a skill payload. It serializes the payload to compact JSON, writes it to a random runtime staging path, computes its hash, then runs the skill loader through `_exec_skill` with the staged path and expected hash.

**Call relations**: `Sandbox.load_skills` calls this when the carrier lacks native skill loading, and again after syncing system skills if needed. `_exec_skill` provides the privileged execution step.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 3 external calls (sha256, dumps, uuid4).


##### `Sandbox._sync_system_skills`  (lines 1064–1081)

```
async def _sync_system_skills(self, bound: 'SandboxSession') -> None
```

**Purpose**: Refreshes the sandbox’s system skills from a trusted archive. This repairs cases where the sandbox image has older or missing system skill files.

**Data flow**: It receives a bound session. It writes the archive to a random runtime staging path, computes its hash, runs the system-skill sync program through `_exec_skill`, and raises an error if the sync command fails.

**Call relations**: `Sandbox.load_skills` calls this only when system skills are missing from a non-native load and a system-skill archive is available.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 2 external calls (sha256, uuid4).


##### `Sandbox._exec_skill`  (lines 1083–1088)

```
async def _exec_skill(self, bound: 'SandboxSession', argv: tuple[str, ...]) -> ExecResult
```

**Purpose**: Runs a trusted skill-management command using the carrier’s privileged skill execution support. It refuses to proceed if the carrier cannot provide that special execution mode.

**Data flow**: It receives a bound session and command arguments. It checks whether the carrier implements `SkillExecuting`; if not, it raises an error. Otherwise it calls `exec_skill` with the standard timeout and returns the result.

**Call relations**: Both staged skill loading and system-skill syncing call this. The carrier supplies the backend-specific privileged execution.

*Call graph*: called by 2 (_run_staged_skill_load, _sync_system_skills).


##### `Sandbox.ensure_tool_output_dir`  (lines 1090–1114)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Ensures the engine’s private tool-output directory exists in the runtime area. If a file or symlink is squatting on that fixed name, it removes it and recreates the directory.

**Data flow**: It binds the sandbox, builds the fixed runtime target path, runs a shell script that checks or repairs the directory, raises an error on failure, and returns true if it had to reclaim a squatting file or link.

**Call relations**: Tool-output offloading code can call this before writing large outputs. It relies on `_runtime_path` for the fixed safe target and carrier `exec` for the repair.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.file_exists`  (lines 1116–1122)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the member workspace. It applies workspace scoping before asking the sandbox.

**Data flow**: It receives a path. It normalizes it with `workspace_path`, binds the sandbox, runs `test -f` inside the sandbox, and returns true when the command succeeds.

**Call relations**: This is the workspace counterpart to `runtime_file_exists`. It delegates the actual check to the carrier’s command execution.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.run_ufo_fs`  (lines 1124–1165)

```
async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured file operation through the carrier after deciding which filesystem root the operation may use. Most writes must stay in `/workspace`; reads, globs, and greps may also inspect current runtime files or skills.

**Data flow**: It receives an operation name and argument dictionary. It copies the arguments, validates and rewrites any path according to allowed roots, records the chosen workspace root in the parameters, then calls the carrier’s `file_op` and returns its result.

**Call relations**: File tools use this high-level gate before reaching backend file operations. It relies on `_runtime_root`, `rooted_path`, and `workspace_path` to enforce the boundary.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); 1 external calls (PurePosixPath).


##### `Sandbox.read_file`  (lines 1167–1169)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts streaming a readable file from the workspace or the current runtime area. It returns chunks so large files can be handled safely.

**Data flow**: It receives a path and returns the asynchronous iterator from `_read_scoped_file`.

**Call relations**: This is the public read wrapper. `_read_scoped_file` performs path scoping and carrier streaming.

*Call graph*: calls 1 internal fn (_read_scoped_file).


##### `Sandbox._read_scoped_file`  (lines 1171–1184)

```
async def _read_scoped_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file after deciding whether the requested path is in `/workspace` or an allowed current-runtime location. It prevents arbitrary `$UFO_HOME` access.

**Data flow**: It receives a path. It binds the sandbox, computes the current runtime display path, maps allowed runtime-display or runtime-root paths to real paths, otherwise normalizes the path as workspace-only, then yields chunks from the carrier’s read stream.

**Call relations**: `read_file` calls this. It uses `_runtime_root`, `rooted_path`, and `workspace_path` before handing the safe target to the carrier.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); called by 1 (read_file); 1 external calls (PurePosixPath).


##### `Sandbox._read_file`  (lines 1186–1189)

```
async def _read_file(self, target: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes from an already-resolved target path. It is a lower-level helper for trusted callers that have already performed path checks.

**Data flow**: It receives a target path. It binds the sandbox, asks the carrier to read the target, and yields each chunk.

**Call relations**: It follows the same carrier read stream pattern as scoped runtime and workspace reads, but skips additional path normalization.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.dial`  (lines 1191–1195)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Gets the outside address for a service listening on a port inside the sandbox. Callers use this when they need to connect to a browser, preview server, or similar in-sandbox process.

**Data flow**: It receives a port number. It binds the sandbox, asks the carrier to dial that port, and returns the carrier’s `DialTarget`.

**Call relations**: Sandbox Chrome support calls this after starting browser services. The carrier owns the backend-specific routing details.

*Call graph*: calls 1 internal fn (_bound); called by 1 (lease).


##### `SandboxSession.conversation_id`  (lines 1209–1210)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID stored in this already-bound sandbox handle. For a session, the sandbox is real and the handle is authoritative.

**Data flow**: It reads `handle.conversation_id` and returns that UUID.

**Call relations**: It fulfills the base `Sandbox.conversation_id` property for concrete sessions.


##### `SandboxSession.created`  (lines 1213–1214)

```
def created(self) -> bool
```

**Purpose**: Reports that a `SandboxSession` already has a live handle. Unlike a late sandbox, it never needs to be opened first.

**Data flow**: It returns `True`.

**Call relations**: It fulfills the base `Sandbox.created` property for already-bound sessions.


##### `SandboxSession._bound`  (lines 1216–1217)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Returns this session as the bound sandbox. No creation or lookup is needed.

**Data flow**: It receives no extra input and returns `self`.

**Call relations**: All base `Sandbox` operations call `_bound`; for `SandboxSession`, that call is effectively a no-op.


##### `SandboxSession.authorize`  (lines 1219–1247)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new session view with a different run token and environment, while pointing at the same underlying container. This lets shared sandboxes run commands with the correct member-specific proxy authority.

**Data flow**: It receives a new run token, environment variable names to remove, and variables to add. It checks that the current handle has a token and that proxy variables contain it, replaces the old token in proxy URLs, drops cleared variables, merges additions, builds a new `SandboxHandle`, and returns a new `SandboxSession`.

**Call relations**: _AuthorizedSandbox calls this after a late sandbox has opened. Direct callers can also reauthorize an already-bound session.

*Call graph*: 2 external calls (__init__, __init__).


##### `_LateSandbox.__init__`  (lines 1251–1263)

```
def __init__(self, conversation_id: UUID, turn_id: UUID, open: Callable[[], Awaitable[SandboxSession]], existing: Callable[[], Awaitable[SandboxSession | None]]) -> None
```

**Purpose**: Creates a sandbox wrapper that does not open the real sandbox until the first operation needs it. This saves work and avoids creating sandboxes for operations that never actually run.

**Data flow**: It receives the conversation ID, turn ID, an async opener, and an async existing-session lookup. It stores them, creates an async lock, and starts with no session cached.

**Call relations**: Higher-level runtime code constructs this wrapper when sandbox creation should be lazy. `_bound` later uses the stored opener under the lock.

*Call graph*: 1 external calls (Lock).


##### `_LateSandbox.conversation_id`  (lines 1266–1267)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID known before sandbox creation. This allows callers to identify the workspace without forcing the sandbox open.

**Data flow**: It reads the stored conversation UUID and returns it.

**Call relations**: It fulfills the `Sandbox.conversation_id` property for late-created sandboxes.


##### `_LateSandbox.created`  (lines 1270–1271)

```
def created(self) -> bool
```

**Purpose**: Reports whether the lazy sandbox has already opened a real session. This is useful for avoiding accidental creation.

**Data flow**: It checks whether the cached session is not `None` and returns that boolean.

**Call relations**: Authorized wrappers delegate their created state back to this late sandbox.


##### `_LateSandbox.authorize`  (lines 1273–1279)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Creates an authorized wrapper around the late sandbox. The real authorization is applied only after the sandbox is opened.

**Data flow**: It receives a run token, cleared environment names, and added environment variables. It packages them with this late sandbox into an `_AuthorizedSandbox` and returns it.

**Call relations**: This lets callers request member-specific authority before a sandbox exists. `_AuthorizedSandbox._bound` later applies the authorization to the opened session.

*Call graph*: 1 external calls (__init__).


##### `_LateSandbox._bound`  (lines 1281–1286)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens the sandbox on first use and then reuses the same session afterward. A lock ensures that concurrent first users do not open duplicate sandboxes.

**Data flow**: It checks whether a session is already cached. If not, it enters the async lock, checks again, awaits the opener, stores the returned session, and then returns it.

**Call relations**: All inherited `Sandbox` operations reach this method when used on a late sandbox. It is the lazy-creation hinge for the class.


##### `_LateSandbox.stop_commands`  (lines 1288–1291)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this turn without necessarily creating the sandbox. This matters because stopping should clean up existing work, not create new work.

**Data flow**: It uses the cached session if present; otherwise it asks the existing-session lookup for one. If a session exists and its carrier can stop commands, it replaces the handle’s turn ID with this late sandbox’s turn ID and asks the carrier to stop those commands.

**Call relations**: It overrides the base stop behavior to avoid calling `_bound`, which would open a sandbox. It uses the optional `CommandStopping` carrier protocol.

*Call graph*: 1 external calls (replace).


##### `_AuthorizedSandbox.conversation_id`  (lines 1302–1303)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID of the underlying late sandbox. Authorization changes who the sandbox acts as, not which conversation it belongs to.

**Data flow**: It reads `late.conversation_id` and returns it.

**Call relations**: It fulfills the base sandbox property by delegating to `_LateSandbox`.


##### `_AuthorizedSandbox.created`  (lines 1306–1307)

```
def created(self) -> bool
```

**Purpose**: Reports whether the underlying late sandbox has already been opened. The authorization wrapper itself does not create a session.

**Data flow**: It reads `late.created` and returns that boolean.

**Call relations**: It delegates lifecycle state to `_LateSandbox`, because that object owns the cached session.


##### `_AuthorizedSandbox.authorize`  (lines 1309–1315)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Replaces the pending authorization with a new one on the same late sandbox. This keeps authorization layering simple instead of nesting wrappers.

**Data flow**: It receives a new run token, cleared environment names, and variables. It forwards them to the underlying late sandbox’s `authorize` method and returns the result.

**Call relations**: Callers can reauthorize an already-authorized late sandbox. The final authorization is applied when `_bound` is called.


##### `_AuthorizedSandbox._bound`  (lines 1317–1318)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Binds the underlying late sandbox and then applies this wrapper’s authorization to the resulting session. This is where a lazy sandbox becomes a member- or turn-authorized session.

**Data flow**: It awaits `late._bound()` to get a real `SandboxSession`, calls that session’s `authorize` with the stored token and environment changes, and returns the authorized session.

**Call relations**: All inherited sandbox operations on an authorized wrapper go through this method. It connects `_LateSandbox` lazy creation with `SandboxSession.authorize` token rewriting.
