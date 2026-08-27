# Sandbox Carriers, Terminal Bridges, and File Boundaries  `stage-11.1`

This stage is shared behind-the-scenes support for running work safely outside the main server. A sandbox is an isolated workspace where commands run and files live, like a fenced workbench for one conversation. session.py defines the common workbench contract, while conversation.py opens, resumes, reads, writes, lists, and cleans up the right conversation’s files. containment.py is the safety gate that keeps file paths inside their allowed folder.

Different “carriers” provide the actual workbench. local.py uses a normal local directory and subprocesses. ufo_ext_docker.py runs work in Docker containers. ufo_ext_e2b.py connects to remote E2B Linux machines. terminal.py lets a user’s own connected terminal act as the sandbox, and stream_terminal.py carries terminal messages across server pods using Redis.

The rest are support pieces. cache.py configures safe reused downloads. client_binary.py finds the built UFO client program. exec_env.py prepares limited environment variables without exposing real secrets. preview.py names the private preview service safely. ingress_host.py and ingress_url.py create checked web addresses for sandbox-hosted sites. __init__.py only makes the folder importable.

## Files in this stage

### Workspace and sandbox carriers
Conversation workspaces are opened and served through local, Docker, or E2B sandbox implementations.

### `core/src/ufo/sandbox/conversation.py`

`orchestration` · `cross-cutting: used when turns start, attachments are written, files are browsed, and runtime outputs are cleaned up`

A conversation can have files, and those files live in a sandboxed workspace. Think of the sandbox as a rented room with a locked cabinet: tools, attachments, terminals, and file browsers all need the right key to reach the same cabinet. This file is responsible for finding or creating that room, remembering its durable handle in the database, and making sure later work reuses the same room instead of creating a forgotten duplicate.

The main class, ConversationSandbox, chooses where the workspace should run. It may be on the normal server-side carrier, on a resume backend that owns an old stored handle, or inside a user-connected terminal. When a new workspace is opened, the file saves a handle like “backend:id” on the conversation row. If two tasks race to create the first workspace, it uses a compare-and-swap database update, meaning “only write my value if the old value is still what I saw.” The loser reopens the winner’s workspace so both tasks end up using one shared place.

Reads are deliberately cautious. Looking at files never creates a sandbox. If no handle exists, the answer is simply “nothing to read.” Writes do create or open the workspace, but are size-limited so one upload cannot consume too much memory. Directory paths are checked carefully to avoid following unsafe links outside the configured workspace root.

#### Function details

##### `ConversationSandbox._route`  (lines 105–116)

```
def _route(self, stored: str | None) -> tuple[Carrier, str, bool]
```

**Purpose**: Chooses which sandbox provider should serve a conversation when there is already a stored handle. This matters because an old workspace may live on a different backend than the one this deployment normally uses.

**Data flow**: It receives a stored sandbox handle, or no handle at all. If the handle names a configured resume backend, it returns that backend’s carrier, name, and off-cluster setting. Otherwise it returns this deployment’s normal carrier, backend name, and off-cluster setting.

**Call relations**: ConversationSandbox._opened uses this when opening a sandbox for work, and ConversationSandbox.existing uses it when attaching for a read-only path. Both rely on it so backend selection is decided in one place instead of scattered through the code.

*Call graph*: called by 2 (_opened, existing); 1 external calls (sandbox_handle_backend).


##### `ConversationSandbox.open`  (lines 118–170)

```
async def open(self, conversation_id: UUID, turn_id: UUID | None, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Opens the sandbox for a conversation, creating it if needed, and makes sure the database records the handle that future callers should reuse. It is the main “get me the workspace” operation for code that may write or run tools.

**Data flow**: It takes a conversation id, optional turn id, run token, and environment variables. It first reads the currently stored handle and sandbox size from the database. Then it opens or creates a sandbox, builds a durable handle string, and tries to save that handle only if the database still contains the value it originally read. It returns a SandboxSession, which is the usable connection to the workspace; if another caller won a race, it retries using the winner’s handle.

**Call relations**: The turn queue calls this through its sandbox-opening path when a turn needs tools. The write and write_runtime methods also call it when off-turn content must be placed into the workspace. Internally it depends on _binding for the database read, _opened for the actual carrier choice and sandbox open, and _claim for the race-safe database write.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 3 (_open_sandbox, write, write_runtime); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 172–234)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Attaches to a conversation’s sandbox only if one already exists and is reachable. It is used for read-like operations where merely looking should not create new storage.

**Data flow**: It takes a conversation id and reads the stored sandbox handle. If there is no handle, it returns None. If the handle points to a terminal workspace, it tries to attach through the terminal carrier. Otherwise it routes the handle to the right backend, checks or computes the host workspace path, asks the carrier to attach, and returns a SandboxSession if successful or None if the sandbox cannot be reached.

**Call relations**: entries, prune, prune_runtime, and read all call this before touching files. It calls _stored to get the saved handle, _route to choose the backend, and builds SandboxSpec objects that tell carriers exactly what to attach to.

*Call graph*: calls 2 internal fn (_route, _stored); called by 4 (entries, prune, prune_runtime, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 236–246)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a conversation that has no workspace yet to a user’s connected terminal directory. This lets the first later open use the terminal’s files instead of creating a separate server-side workspace.

**Data flow**: It receives a conversation id and a current working directory from the terminal. It formats that directory as a client-backed sandbox handle, checks whether the conversation already has a stored handle, and if not tries to store the terminal handle. It returns true only if this call made the binding.

**Call relations**: This method uses _stored to see whether the conversation is still unbound and _claim to make the database update safely. It is not called by another function in this file, but it prepares the state that _opened later trusts when deciding where a conversation should run.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 248–259)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s visible workspace and returns the path the agent can use to read them. This is how inbound content, such as an attachment, lands in /workspace.

**Data flow**: It takes a conversation id, a relative file path, and the file bytes. It rejects content larger than the configured limit, opens the sandbox off-turn using an unsigned token, writes the file through the session, and returns the normalized /workspace path.

**Call relations**: This method calls open because writing is allowed to create the workspace if it does not exist yet. It then uses the SandboxSession returned by open to perform the actual file write, and workspace_path to describe the path from the agent’s point of view.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.write_runtime`  (lines 261–273)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output into a named runtime area rather than the member-visible workspace root. This is useful for generated logs or artifacts that belong to system machinery.

**Data flow**: It takes a conversation id, a runtime category, a relative path, and bytes. It checks the byte limit, opens the sandbox off-turn, prefixes the path with the category, writes the file through the session’s runtime-file method, and returns a display path for that runtime file.

**Call relations**: Like write, it calls open because runtime output may be the first thing to create the sandbox. After open returns a SandboxSession, this method hands the bytes to that session’s runtime storage methods.

*Call graph*: calls 1 internal fn (open).


##### `ConversationSandbox.prune`  (lines 275–286)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Keeps only the newest files under a workspace subdirectory and deletes older ones. This prevents unattended append-only writers from growing the workspace forever.

**Data flow**: It takes a conversation id, a relative directory prefix, and a number of files to keep. It attaches only if the sandbox already exists. If it exists, it runs a small Python pruning program inside the sandbox, aimed at the requested workspace path. If that program fails, it raises an OSError with the error text.

**Call relations**: This method calls existing so cleanup does not create a workspace just to delete from it. It uses workspace_path to turn the relative prefix into the in-sandbox /workspace path, then relies on the session’s Python runner to do the deletion where the agent sees the files.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.prune_runtime`  (lines 288–300)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files from an internal runtime directory while keeping the newest ones. It is the runtime-storage version of prune.

**Data flow**: It takes a conversation id, runtime category, relative prefix, and keep count. It attaches to an existing sandbox, builds the runtime target path and runtime root path through the session, runs the pruning program there, and raises an error if pruning reports failure.

**Call relations**: This method calls existing for the same no-side-effect reason as prune. It then asks the session to translate runtime-relative names into real paths before handing those paths to the pruning program.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox.entries`  (lines 302–341)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists the member-visible files in a conversation’s workspace. It returns simple file records with relative path, size, and modified time.

**Data flow**: It takes a conversation id and attaches only if a sandbox already exists. It asks the sandbox to run the project’s file-globbing command over /workspace while excluding names like .git. It checks that the result contains a file list, warns if the listing was truncated, converts each returned path into a workspace-relative path, converts size and modification time into ordinary fields, sorts the results by path, and returns them.

**Call relations**: This method calls existing so browsing files never creates a new workspace. For each listed file it calls _workspace_rel to remove the container or host workspace root from the returned path. If the file-walking command reports truncation, it calls the warning logger so operators can see that the browser may be incomplete.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 343–347)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns an absolute path reported by a sandbox file walk into the relative path shown in the workspace browser. It also protects against a listing that claims a file outside the workspace.

**Data flow**: It receives a sandbox handle and a path string. It checks whether the path starts with either the standard in-container /workspace root or the handle’s host workspace path. If so, it strips that root and returns the remaining relative path. If neither root matches, it raises an error.

**Call relations**: entries calls this for every file returned by the file-walking command. It is the last safety check before paths are shown to users as workspace contents.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 349–357)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Reads one file from a conversation’s workspace in chunks, or reports that there is nothing to read. It avoids creating a workspace just because someone requested a file.

**Data flow**: It takes a conversation id and a relative file path. It attaches to an existing sandbox; if none exists, it returns None. It then checks whether the requested file exists; if not, it returns None. If the file exists, it returns an asynchronous byte stream for reading the file piece by piece.

**Call relations**: This method calls existing as its gateway to the sandbox. After that, it delegates the existence check and streaming read to the SandboxSession.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 359–418)

```
async def _opened(self, conversation_id: UUID, turn_id: UUID | None, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Performs the actual choice and open operation for a sandbox. It decides whether to use a connected terminal, a resume backend, or the deployment’s normal carrier.

**Data flow**: It receives the conversation id, optional turn id, any stored handle, run token, environment variables, and sandbox size. If the stored handle or live terminal binding points to a client terminal, it opens through a TerminalCarrier. Otherwise it routes the stored handle to the correct backend, prepares a host workspace path, creates the directory if needed for local storage, changes ownership when running as root, and asks the carrier to create or resume the sandbox. It returns the backend name, carrier, and sandbox handle.

**Call relations**: open calls this during each attempt to open or create a sandbox. _opened calls _route for backend selection, uses sandbox_handle_id to extract resume ids from stored handles, and builds SandboxSpec objects that carriers use to create or attach to the real sandbox.

*Call graph*: calls 1 internal fn (_route); called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 420–435)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates the conversation’s host-side workspace directory safely when a server-side sandbox needs one. Its main job is to make sure the directory is really under the configured workspace root, not secretly redirected through an unsafe link.

**Data flow**: It takes a conversation id. It makes sure the workspace root exists, resolves the configured root according to the sandbox.workspace_root setting, and then creates or opens the conversation-specific directory using containment checks. It returns the safe Path for that directory.

**Call relations**: This helper is used indirectly by _opened through a background thread when a non-off-cluster carrier needs a local host directory. It relies on configured_root and contained_dir from the containment layer to do the careful path checking.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 437–448)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds a conversation’s host-side workspace directory only if it already exists. Unlike _provisioned_dir, it does not create anything.

**Data flow**: It takes a conversation id. It resolves the configured workspace root and checks for the conversation directory with containment safeguards. If the directory is missing, it returns None. If the path exists but violates containment rules, the containment layer raises an error.

**Call relations**: existing calls this through a background thread for read-only attachment to local server-side workspaces. This preserves the rule that reads and listings must not create a workspace.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 450–452)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Fetches just the stored sandbox handle for a conversation. It is a small convenience wrapper around the fuller database binding read.

**Data flow**: It takes a conversation id, calls _binding to read both the handle and sandbox size, discards the size, and returns the handle or None.

**Call relations**: existing uses this to decide whether there is anything to attach to. claim_terminal uses it before trying to bind a terminal. _claim uses it after a failed database swap to learn which handle won the race.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 454–475)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the conversation’s sandbox handle and the owning agent’s requested sandbox size from the database. This gives opening code both the resume information and the size to use if a sandbox must be created.

**Data flow**: It takes a conversation id and opens a workspace-scoped database transaction. It selects the conversation’s sandbox_handle together with the related agent’s sandbox_size, restricted to the current workspace. If no row is found, it raises a ValueError because the conversation does not belong to this workspace. Otherwise it returns the stored handle and size.

**Call relations**: open calls this at the start of the main open flow. _stored calls it when only the handle is needed. It uses workspace_tx for the database transaction and ws_current to make sure the lookup is limited to the active workspace.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 477–498)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Safely stores a sandbox handle in the conversation row without overwriting another caller’s newer result. This is the race-control point for simultaneous first opens.

**Data flow**: It takes a conversation id, the handle value that the caller previously saw, and the new handle the caller wants to store. It runs an update that succeeds only if the database still contains the previously seen value. If the update succeeds, it returns the new handle. If not, it reads the current stored handle and returns that winner; if the row somehow has no handle afterward, it raises an error.

**Call relations**: open calls this after creating or resuming a sandbox so the durable handle is recorded. claim_terminal also calls it to bind an unbound conversation to a terminal directory. When _claim loses a race, it calls _stored to discover the handle chosen by the other caller.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### `core/src/ufo/sandbox/local.py`

`domain_logic` · `sandbox setup and command/file request handling`

This file lets a conversation use a real host folder as its `/workspace` and run requested commands directly on the same machine. Think of it as a workshop bench: instead of placing the work inside a locked container, UFO gives each conversation a clearly marked bench area and carefully controls the tools it hands to commands.

The file creates a scratch area for local runs. That scratch area contains a private HOME directory and, when available, a copied `ufo` client binary. Commands get this scratch HOME and PATH, not the server’s real environment, so secrets from the running service are not accidentally exposed. Git is also deliberately given a clean configuration so it does not pop up host credential prompts or hang waiting for user interaction.

When a sandbox is created, the file makes sure the workspace directory exists, prepares runtime state, writes the proxy certificate, and builds environment variables that force HTTP and HTTPS traffic through the sandbox proxy. That keeps model-key swapping and network metering working even though there is no container.

It also implements safe file reads and writes. Paths are checked so tools cannot ask for `../../some-host-file`, and files are opened in ways that avoid being tricked by symbolic links. The important warning is that this is not true security isolation. A local subprocess still runs on the host; only the file-tool paths are guarded. For real isolation, another carrier such as Docker or E2B is needed.

#### Function details

##### `_provision_scratch`  (lines 71–95)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates one temporary scratch area for the whole process. This gives local sandbox commands a private HOME directory and, if available, a copied `ufo` command-line tool on their PATH.

**Data flow**: It starts with no input. It creates a temporary directory, adds `home` and `bin` folders, tries to find the UFO client binary, and copies it into `bin` with executable permissions. It returns the scratch directory path; if the binary is missing, it logs a warning and still returns the directory.

**Call relations**: This is used as the default factory for `LocalCarrier`’s `_scratch` field. That means the first local carrier setup prepares the small local runtime area that later methods use for HOME, UFO_HOME, PATH, and command execution.

*Call graph*: 4 external calls (Path, mkdtemp, warn, client_binary).


##### `LocalCarrier.ufo_home`  (lines 103–105)

```
def ufo_home(self) -> Path
```

**Purpose**: Returns the local sandbox’s UFO home directory. This is where local runtime data such as installed skills is stored.

**Data flow**: It reads the carrier’s scratch directory and appends `home/.ufo`. The result is a filesystem path; it does not create or modify anything by itself.

**Call relations**: Other methods use this property when they need a stable local place for UFO-specific files, especially skill installation and per-conversation runtime directories.


##### `LocalCarrier.seed_system_skills`  (lines 107–135)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Installs the built-in system skills from a zip archive into the local UFO home. It keeps the installed skill set in sync with the archive’s manifest.

**Data flow**: It receives archive bytes. It opens them as a zip file, reads `manifest.json`, compares the new manifest with the previous installed manifest, removes old top-level skill folders that may conflict, writes each skill file safely under the skills directory, and finally writes the new manifest. If the manifest shape is wrong, it raises an error.

**Call relations**: This method calls `_system_manifest` to learn what was previously installed, then uses containment helpers to remove and write files without escaping the skills directory. It prepares the system skill files that `_load_skills` later verifies and exposes.

*Call graph*: calls 1 internal fn (_system_manifest); 7 external calls (BytesIO, loads, Path, contained_file, contained_relative, contained_remove, ZipFile).


##### `LocalCarrier.load_skills`  (lines 137–147)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asynchronously loads the skills requested for a run and returns a command-style result. It wraps skill loading errors into an `ExecResult` instead of letting them crash the caller.

**Data flow**: It receives a sandbox handle and a payload describing requested system and user skills. It runs `_load_skills` in a worker thread because it touches the filesystem. On success it returns JSON listing the installed skill roots; on expected validation or filesystem errors it returns exit code 1 with the error text.

**Call relations**: This is the async public wrapper around `_load_skills`. Callers can treat it like a sandbox operation that succeeds or fails with stdout, stderr, and an exit code.

*Call graph*: 3 external calls (__init__, to_thread, dumps).


##### `LocalCarrier._load_skills`  (lines 149–203)

```
def _load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Does the real work of checking, verifying, and installing skills. It makes sure system skills match their trusted manifest and user skills match their declared digest before making them available.

**Data flow**: It receives a payload with `system` and `user` sections. It reads the system manifest, validates names and data shapes, verifies requested system skill digests against files already on disk, decodes user skill files from URL-safe base64 text, checks their digest, installs valid user skills, and returns a mapping from skill name to local root path.

**Call relations**: This is called by `LocalCarrier.load_skills`. It depends on `_system_manifest`, `_read_skill_files`, `_skill_digest`, `_validate_user_skill_name`, and `_install_user_skill` to break the process into reading, checking, and writing steps.

*Call graph*: calls 5 internal fn (_install_user_skill, _read_skill_files, _skill_digest, _system_manifest, _validate_user_skill_name); 2 external calls (urlsafe_b64decode, contained_relative).


##### `LocalCarrier._system_manifest`  (lines 206–214)

```
def _system_manifest(root: Path) -> Mapping[str, object]
```

**Purpose**: Reads the installed system-skill manifest from disk. If none exists yet, it reports an empty skill set.

**Data flow**: It receives the skills root directory. It safely opens `.system-manifest.json` under that root, returns `{"skills": {}}` if the file is absent, otherwise parses JSON and checks that the result is a mapping. The output is the manifest data.

**Call relations**: Both `seed_system_skills` and `_load_skills` call this. Seeding uses it to know what old skill folders to replace; loading uses it to decide which system skills are trusted and available.

*Call graph*: called by 2 (_load_skills, seed_system_skills); 2 external calls (load, contained_file).


##### `LocalCarrier._read_skill_files`  (lines 217–224)

```
def _read_skill_files(root: Path, name: str, files: list[str]) -> list[tuple[str, bytes]]
```

**Purpose**: Reads the files that make up one system skill so their digest can be checked. This confirms the files on disk still match the manifest.

**Data flow**: It receives the skills root, a skill name, and a list of file paths. For each listed path, it safely resolves the path under the root, opens the file, reads its bytes, and adds `(path, bytes)` to a list. It returns that list of file contents.

**Call relations**: `_load_skills` calls this when validating a requested system skill. The returned file contents are then passed to `_skill_digest` so `_load_skills` can compare the actual files with the expected digest.

*Call graph*: called by 1 (_load_skills); 2 external calls (contained_file, contained_relative).


##### `LocalCarrier._install_user_skill`  (lines 227–233)

```
def _install_user_skill(root: Path, name: str, files: list[tuple[str, bytes]]) -> None
```

**Purpose**: Installs a user-provided skill into the local skills directory. It replaces any previous copy of that skill with the verified files.

**Data flow**: It receives the skills root, the skill name, and a list of file paths with bytes. It removes the destination folder safely, then writes each file under the skill’s directory, creating parent folders as needed. It does not return a value; it changes the files on disk.

**Call relations**: `_load_skills` calls this only after it has validated the user skill name and checked the digest. This keeps the writing step separate from the trust checks.

*Call graph*: called by 1 (_load_skills); 3 external calls (contained_file, contained_relative, contained_remove).


##### `LocalCarrier._validate_user_skill_name`  (lines 236–239)

```
def _validate_user_skill_name(name: str, root: Path) -> None
```

**Purpose**: Checks that a user skill name is a simple top-level folder name. This prevents a skill from pretending to be hidden or writing outside its allowed place.

**Data flow**: It receives a skill name and the skills root. It resolves the name through the containment guard, converts it to a path relative to the root, and rejects names that are nested or start with a dot. It returns nothing if the name is acceptable, and raises an error if not.

**Call relations**: `_load_skills` calls this before decoding and installing user skill files. It is one of the early safety checks before any user-provided files are written.

*Call graph*: called by 1 (_load_skills); 2 external calls (Path, contained_relative).


##### `LocalCarrier._skill_digest`  (lines 242–247)

```
def _skill_digest(files: list[tuple[str, bytes]]) -> str
```

**Purpose**: Computes the trusted fingerprint for a set of skill files. A fingerprint, or digest, is a short hash string that changes if any path or file content changes.

**Data flow**: It receives a list of `(path, bytes)` pairs. For each file, it hashes the path and the content and feeds both into a larger SHA-256 hash. It returns a string like `sha256:<hex value>`.

**Call relations**: `_load_skills` uses this for both system and user skills. For system skills it confirms installed files match the manifest; for user skills it confirms uploaded files match the digest claimed by the caller.

*Call graph*: called by 1 (_load_skills); 1 external calls (sha256).


##### `LocalCarrier.create`  (lines 249–283)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a local sandbox handle for a conversation. It prepares the workspace and builds the environment that future commands will run with.

**Data flow**: It receives a `SandboxSpec` containing the workspace path, conversation ID, proxy settings, run token, and extra environment variables. It creates the workspace directory, creates a runtime directory under UFO_HOME, writes the proxy CA certificate, builds proxy-related environment variables, and returns a `SandboxHandle` describing the ready local sandbox.

**Call relations**: This method calls `_base_env` to start from a safe local command environment, then adds proxy, model-key sentinel, certificate, and spec-provided environment values. Later command execution and file operations use the returned handle.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier._base_env`  (lines 285–308)

```
def _base_env(self) -> dict[str, str]
```

**Purpose**: Builds the minimal environment that every local sandbox command receives. It avoids inheriting the server’s real environment, which may contain secrets.

**Data flow**: It reads only a small allowlist from the host environment, such as locale and temporary-directory variables. It then adds a scratch HOME, UFO_HOME, PATH, and Git settings that disable system/global surprises, credential prompts, and terminal prompts. It returns a dictionary of environment variables.

**Call relations**: `create` and `attach` both call this. `create` adds proxy and certificate variables on top; `attach` uses it for read-only access to an existing local workspace.

*Call graph*: called by 2 (attach, create); 1 external calls (Path).


##### `LocalCarrier.attach`  (lines 310–324)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Connects to an already-existing local workspace without creating it. This is useful for browsing or resuming a conversation only if its workspace is already present.

**Data flow**: It receives a `SandboxSpec`, checks whether the workspace directory exists, and returns `None` if it does not. If it exists, it builds and returns a `SandboxHandle` pointing at the workspace and runtime directory with the safe base environment.

**Call relations**: This is the read-only counterpart to `create`. It calls `_base_env` just like `create`, but it does not make the workspace directory or add the full proxy environment.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 326–375)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command as a local subprocess inside the workspace. It rewrites logical `/workspace` arguments to the real host directory before launching the command.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It finds the workspace root, rewrites arguments for the host filesystem, starts the subprocess in that directory with the handle’s environment, collects stdout and stderr, and returns an `ExecResult`. If the command times out, it kills the whole process group and returns exit code 124 with a timeout message.

**Call relations**: This is the main command-running method for the local carrier. It calls `_root` to find the host workspace, `host_argv` to translate paths, and `_kill_process_group` if the command times out or the surrounding task is cancelled.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 4 external calls (__init__, create_subprocess_exec, wait_for, host_argv).


##### `LocalCarrier.write`  (lines 377–400)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the sandbox workspace. It is the public async wrapper that keeps blocking filesystem work off the event loop.

**Data flow**: It receives a sandbox handle, a sandbox path, and bytes to write. It sends the actual work to `_write_contained` in a worker thread. It returns nothing after the file has been safely written or raises an error if the path is invalid.

**Call relations**: This method delegates to `_write_contained` because safe file replacement uses normal blocking filesystem calls. Callers use it when they need to copy data into the local sandbox.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 402–405)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the safe on-disk write for `LocalCarrier.write`. It makes sure the target path stays inside an allowed sandbox root.

**Data flow**: It receives a handle, path, and bytes. It asks `_contained_name` which root the path belongs to, opens the target through the containment guard with parent creation enabled, and atomically replaces the target bytes while preserving or applying the expected write mode. It changes the filesystem and returns nothing.

**Call relations**: `LocalCarrier.write` calls this in a worker thread. It uses `_contained_name` to decide whether the path belongs to the workspace or runtime root before using the containment file helper to write safely.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 407–418)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes out of a file in the local sandbox. It reads in chunks so large files do not have to be loaded into memory all at once.

**Data flow**: It receives a sandbox handle and path. It opens a safely contained source file in a worker thread, then repeatedly reads chunks of up to one megabyte and yields them. When finished or interrupted, it closes the file.

**Call relations**: This is the public async read path. It calls `_contained_source` to pin and open the safe file first, then uses worker-thread reads because normal filesystem reads are blocking.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 420–430)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Safely opens a sandbox file for reading. It refuses paths that do not exist or cannot be proven to stay inside the sandbox roots.

**Data flow**: It receives a handle and path. It uses `_contained_name` to choose the allowed root, opens the target through the containment guard, checks that the file exists, and returns a binary reader. If a guarded path component is missing, it converts that into `FileNotFoundError`.

**Call relations**: `LocalCarrier.read` calls this before streaming bytes. By returning an already-open file object, it avoids later path swaps changing what the read points to.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 432–437)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level file operation through the `ufo fs` command-line interface. This lets the local carrier reuse the same file-tool behavior used in other sandbox styles.

**Data flow**: It receives a sandbox handle, an operation name, and operation parameters. It passes those to `ufo_fs_file_op`, which runs the UFO file operation against the workspace. It returns the operation’s result as a dictionary.

**Call relations**: This method is the local carrier’s bridge to shared file-tool logic. Because `_provision_scratch` puts the `ufo` client on PATH when available, this can run the file tool as a local subprocess.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `LocalCarrier.dial`  (lines 439–445)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Builds the address for reaching a service started by a local sandbox command. Because local commands share the host network, a sandbox port is just localhost on the same port.

**Data flow**: It receives a handle and port number. It returns a `DialTarget` with host `127.0.0.1:<port>` and TLS disabled. It does not inspect or change the sandbox.

**Call relations**: Callers use this after a command starts a local server and something outside the sandbox needs to connect to it. Unlike container carriers, there is no per-conversation network namespace here, so port conflicts are possible.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 448–453)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Forcefully stops a subprocess and any child processes in its process group. This prevents timed-out or cancelled commands from leaving runaway background children behind.

**Data flow**: It receives an asyncio subprocess object. It sends `SIGKILL` to the process group identified by the subprocess PID, ignores the case where the process is already gone, and waits for the process to finish. It returns nothing.

**Call relations**: `LocalCarrier.exec` calls this when a command times out or when command execution is interrupted by cancellation or another exception. It is the cleanup step that stops the whole launched command tree, not just the direct child.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 456–459)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the host directory that backs the local sandbox workspace. It fails clearly if the handle does not have such a directory.

**Data flow**: It receives a sandbox handle. If `workspace_host_path` is missing, it raises a runtime error; otherwise it converts that path string into a `Path` object and returns it.

**Call relations**: `LocalCarrier.exec` calls this to choose the subprocess working directory. `_contained_name` also calls it when translating `/workspace` paths into host paths.

*Call graph*: called by 2 (exec, _contained_name); 1 external calls (Path).


##### `_contained_name`  (lines 462–468)

```
def _contained_name(handle: SandboxHandle, path: str) -> tuple[PurePosixPath, Path]
```

**Purpose**: Translates a sandbox path into a relative path plus the host root it belongs under. It only accepts paths under the workspace or the sandbox runtime root.

**Data flow**: It receives a handle and a path string. It treats the path as a POSIX-style sandbox path, checks whether it is inside `/workspace`, or inside the handle’s runtime root, and returns the relative name together with the matching host root. If the path is outside both allowed roots, it raises a value error.

**Call relations**: `LocalCarrier._write_contained` and `LocalCarrier._contained_source` call this before writing or reading files. It is the small routing step that decides which safe filesystem root the containment helpers should enforce.

*Call graph*: calls 1 internal fn (_root); called by 2 (_contained_source, _write_contained); 2 external calls (Path, PurePosixPath).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox startup, command execution, file access, idle reclaim`

This file is the Docker version of a sandbox carrier: the part of the system that gives an agent a safe place to run commands and read or write files. Think of each conversation getting its own workshop. The workshop is a Docker container, and the shared project folder is mounted inside it as `/workspace` so work survives even if the container is stopped.

The important safety rule is that internet access is not given directly. Every command run inside the container gets proxy settings for that specific turn. The proxy can block disallowed hosts and replace fake API keys with real ones outside the sandbox, so raw credentials are not stored in the container. Those proxy settings are passed only when a command runs, not baked into the container, because a reused container must not keep an old turn’s token.

The file also watches host resources. Docker networks and running containers can pile up, so when a new sandbox is opened it looks for old inactive ones and stops them. It does not delete the workspace. Later, if that conversation is touched again, the carrier reconnects its network and starts the container back up. Most actions are built around Docker commands: create containers, execute commands, stream file reads, copy file writes, install the proxy certificate, and create the private runtime directory.

#### Function details

##### `_docker`  (lines 79–93)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and returns its exit code, standard output, and error output. It gives all other code in this file one consistent way to talk to Docker and enforce a timeout.

**Data flow**: It receives Docker command arguments, optional input bytes, and a timeout. It starts `docker ...`, sends the input to it, waits for completion, and returns the result; if Docker takes too long, it kills the process and returns a special timeout code.

**Call relations**: Nearly every DockerCarrier helper calls this when it needs Docker to do real work, such as listing containers, starting one, creating a network, installing a certificate, or running a command inside the sandbox.

*Call graph*: called by 13 (_death_report, _ensure_network, _ensure_runtime_root, _exec_with, _held_id, _install_ca, _reclaim_idle, _release, _revive, _running_id (+3 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 106–219)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or reconnects the Docker container for a conversation and returns a handle the rest of the system can use. It also prepares per-turn proxy settings so commands inside the sandbox use the correct network permissions and token.

**Data flow**: It receives a sandbox specification with the conversation id, image, workspace path, proxy details, run token, and environment. It reclaims old idle sandboxes, checks whether this conversation already has a running or stopped container, revives it if possible, or creates a new Docker network and container. It installs the current proxy certificate, creates the private runtime directory, and returns a SandboxHandle.

**Call relations**: This is the main opening path for Docker sandboxes. It calls the smaller helpers that find existing containers, revive stopped ones, create networks, install certificates, and clean up if container creation fails.

*Call graph*: calls 9 internal fn (_ensure_network, _ensure_runtime_root, _install_ca, _network_name, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier.attach`  (lines 221–250)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Looks for an existing container for a conversation without creating a new one. It is useful for read-like operations that should resume a sandbox if it exists, but report absence if it does not.

**Data flow**: It receives a sandbox specification, checks for a running container, then checks for a stopped one and tries to revive it. If a usable container is found, it ensures the runtime directory exists and returns a SandboxHandle; otherwise it returns `None`.

**Call relations**: This is a gentler companion to `DockerCarrier.create`. It uses the same running, stopped, revive, and runtime-root helpers, but it avoids fresh Docker creation and turns some revive failures into a simple “not found” result.

*Call graph*: calls 4 internal fn (_ensure_runtime_root, _revive, _running_id, _stopped_id); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier._reclaim_idle`  (lines 252–320)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops containers for conversations that have been inactive long enough, freeing memory and Docker network space while keeping their workspace data. This prevents quiet old sandboxes from exhausting host resources.

**Data flow**: It receives the conversation currently being opened, marks it as recently used, asks Docker which UFO containers and networks exist, records any it did not already know about, and finds entries old enough to reclaim. For each stale conversation, it checks that no command is in progress, removes its touch record as a reservation, and asks `_release` to stop the container and remove the network; if release fails, it puts the record back for a later retry.

**Call relations**: `DockerCarrier.create` triggers this before opening a sandbox. It relies on `_held_id` to find the container occupying a name and `_release` to actually free resources, while lifecycle locks keep reclaim from racing with revive.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 322–332)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a normal command inside the sandbox as part of a turn. It adds the per-turn proxy and API-key placeholder environment so any network access is attributed and filtered correctly.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It converts the handle’s environment settings into Docker `--env` options, then delegates to `_exec_with`; the result is an ExecResult with output, error text, exit code, and timeout information.

**Call relations**: This is the public command-execution path. It does only the normal-command setup, then hands the actual Docker execution, revive behavior, and bookkeeping to `_exec_with`.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier.exec_skill`  (lines 334–338)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a skill-related command inside the container as root. This is for server-driven setup or synchronization work that needs elevated container permissions.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It supplies Docker options that select the root user, then delegates to `_exec_with` and returns its ExecResult.

**Call relations**: This shares the same execution machinery as `DockerCarrier.exec`, but changes the Docker options so the command runs as root instead of as the normal sandbox user.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier._exec_with`  (lines 340–379)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, options: tuple[str, ...]) -> ExecResult
```

**Purpose**: Performs the actual `docker exec` call and protects the container from idle reclaim while the command is running. If the container was stopped, it can revive it and retry once.

**Data flow**: It receives a handle, command arguments, timeout, and extra Docker options. It increments an in-flight counter, updates the last-used time, runs the command in `/workspace`, retries after revive if Docker says the container is not running, then returns decoded stdout, stderr, and an exit code. It always decrements the in-flight counter and refreshes the last-used time afterward.

**Call relations**: `DockerCarrier.exec` and `DockerCarrier.exec_skill` both funnel into this helper. It calls `_docker` to run Docker and `_revive` when a stopped container must be restarted before retrying.

*Call graph*: calls 2 internal fn (_revive, _docker); called by 2 (exec, exec_skill); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 381–399)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the sandbox. It streams the content through standard input rather than putting it on a command line, which is safer and works for arbitrary file contents.

**Data flow**: It receives a handle, destination path, and bytes to write. It marks the container as in use, calls `_write_started`, retries after revive if Docker reports the container is not running, and raises an OSError if the copy program fails. It updates in-flight and last-used tracking before and after the operation.

**Call relations**: This is the public write path. It depends on `_write_started` for the actual guarded copy and `_revive` for recovery if reclaim stopped the container just before the write.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 401–419)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Starts one guarded file-copy attempt inside the container. It uses the sandbox’s own copy-in program so path checks happen from the container’s point of view.

**Data flow**: It receives a handle, path, and content bytes. It chooses the allowed root for the write, either the private runtime root or `/workspace`, then runs Python inside the container with the copy-in program and sends the file bytes through stdin. It returns Docker’s exit code and stderr bytes.

**Call relations**: `DockerCarrier.write` calls this for the first attempt and, if needed, after reviving a stopped container. It uses `_docker` to launch the container-side Python helper.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `DockerCarrier.read`  (lines 421–455)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. It avoids loading the whole file into host memory and reports filesystem errors in a way that matches normal local file reads.

**Data flow**: It receives a handle and path, marks the container as active, and starts a `cat` process through `_read_started`. It yields chunks as they arrive, retries from the beginning if the container was stopped before data was read, and converts known error messages into OSError values. If the read dies strangely, it asks `_death_report` for container state and raises a RuntimeError.

**Call relations**: This is the public read path. It uses `_read_started` to create the stream, `_revive` to recover from a stopped container, and `_death_report` to explain failures with no useful stderr.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 457–499)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Prepares one file-read attempt and separates streaming from final error reporting. This structure lets `read` retry cleanly without reusing a half-finished async generator.

**Data flow**: It receives a handle and path. It creates an empty failure list and returns two things: an async byte stream and that list, which will be filled after the stream ends if `cat` exits with an error.

**Call relations**: `DockerCarrier.read` calls this whenever it needs to start or restart a file stream. The nested `DockerCarrier._read_started.stream` function does the actual Docker process work.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 472–497)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `cat` inside the container and yields the file’s bytes as they are produced. It also cleans up the Docker exec process if the caller stops reading early.

**Data flow**: It starts `docker exec ... cat <path>` with stdout and stderr pipes. It reads stdout in fixed-size chunks and yields each chunk; after stdout ends, it reads stderr, waits for the exit code, and records any failure. If the generator is abandoned before the command exits, it kills the process and drains it.

**Call relations**: This nested generator is returned by `_read_started` and consumed by `DockerCarrier.read`. It directly uses asyncio’s subprocess creation instead of `_docker` because it must stream output gradually rather than wait for the whole command to finish.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 501–519)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Adds useful context when a read process dies without an error message. It asks Docker what state the container itself is in, which helps distinguish a killed command from a stopped or failed container.

**Data flow**: It receives a handle, runs `docker inspect` for the container’s status, exit code, and out-of-memory flag, and returns a short text explanation. If inspect itself fails, it returns text describing that failure instead.

**Call relations**: `DockerCarrier.read` calls this only for unusual read failures where `cat` gave no stderr. It uses `_docker` for the inspect command.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 521–527)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured file operation, such as one provided by the UFO filesystem helper, inside the Docker sandbox. It gives higher-level file actions the same container pinning and revive behavior as normal commands.

**Data flow**: It receives a handle, an operation name, and operation parameters. It passes the carrier, handle, operation, and parameters to the shared `ufo_fs_file_op` helper and returns that helper’s result dictionary.

**Call relations**: This bridges DockerCarrier into the common sandbox filesystem API. The shared helper calls back through the carrier’s execution behavior as needed.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `DockerCarrier.dial`  (lines 529–536)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that this Docker carrier cannot expose an in-container service port to the outside. A caller that needs browser debugging or a preview server must use a carrier that supports external routing.

**Data flow**: It receives a handle and port number but does not use them to create a connection. It raises SandboxUnreachable with an explanation instead of returning a DialTarget.

**Call relations**: This is the Docker implementation of the carrier dialing interface. Unlike remote carriers, it stops the flow immediately because this backend has no per-port public address.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 538–552)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation’s container and removes its private Docker network. This frees the scarce host resources while preserving the stopped container and mounted workspace.

**Data flow**: It receives a conversation id and possibly a container id. If there is a container id, it asks Docker to stop it; then it removes the conversation’s network. It returns `true` when the resources are released or the network was already gone, and `false` when stopping fails.

**Call relations**: `_reclaim_idle` calls this under a lifecycle lock when it decides a conversation is stale. It uses `_network_name` to identify the network and `_docker` to perform the stop and network removal.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 554–574)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Starts a stopped sandbox container again and reconnects its private network. This is the recovery path used when a later action touches a container that idle reclaim had stopped.

**Data flow**: It receives a conversation id and container id. Under the conversation’s lifecycle lock, it updates the last-used time, ensures the Docker network exists, connects the container to it, and starts the container. It returns whether the container successfully started, while serious network creation failures are raised.

**Call relations**: `create`, `attach`, `exec`, `write`, and `read` all call this when they find or encounter a stopped container. It calls `_ensure_network`, `_network_name`, and `_docker` to rebuild the Docker-side wiring.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (_exec_with, attach, create, read, write).


##### `DockerCarrier._held_id`  (lines 576–584)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container id using a given name, no matter whether it is running, paused, or exited. Reclaim needs this because any of those states can still be tied to the conversation’s resources.

**Data flow**: It receives a container name, asks Docker for all containers matching that exact name, and returns the id text if found or `None` if not. If Docker itself fails, it raises an error instead of pretending there is no container.

**Call relations**: `_reclaim_idle` calls this before releasing a stale conversation, so `_release` knows which container should be stopped.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 586–595)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds the id of a stopped container with a given name. This tells the carrier whether a reclaimed conversation can be restarted instead of creating a fresh container.

**Data flow**: It receives a container name, asks Docker for exited containers matching that exact name, and returns the id if one exists or `None` otherwise. Docker command failures become RuntimeError.

**Call relations**: `DockerCarrier.create` and `DockerCarrier.attach` use this after checking for a running container. If it finds one, they can call `_revive` and continue with the existing sandbox.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 597–608)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds the id of a running container with a given name. It treats Docker command failure as a real error, not as “not running,” so callers do not take the wrong recovery path.

**Data flow**: It receives a container name, asks Docker for running containers matching that exact name, and returns the id if found or `None` if the command succeeds with no match. If Docker cannot answer, it raises RuntimeError.

**Call relations**: `DockerCarrier.create` and `DockerCarrier.attach` call this first when looking for an existing sandbox. A positive result lets them reuse the live container immediately.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 610–611)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Docker network name for one conversation. Using a predictable name lets the carrier find, recreate, and remove the right network later.

**Data flow**: It receives a conversation id and combines the carrier’s network prefix with the id in compact hexadecimal form. It returns that network name string.

**Call relations**: `create`, `_revive`, and `_release` call this whenever they need to create, reconnect, or remove the Docker network tied to a conversation.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 613–624)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if needed. It is safe if another task creates the same network at nearly the same time.

**Data flow**: It receives a network name, asks Docker whether that network already exists, and returns immediately if it does. If not, it runs `docker network create`; an “already exists” response is accepted because it means another concurrent caller achieved the same goal.

**Call relations**: `DockerCarrier.create` calls this before starting a new container, and `_revive` calls it before reconnecting a stopped container. It uses `_docker` for Docker network commands.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 626–639)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current proxy certificate authority inside the container. This lets tools in the sandbox trust the project’s proxy when it inspects and forwards HTTPS traffic.

**Data flow**: It receives a container id and certificate text. It runs a root shell command in the container that writes the certificate file and updates the system certificate store. If that command fails, it raises RuntimeError.

**Call relations**: `DockerCarrier.create` calls this both for new containers and reused ones. That matters because the proxy certificate can change when the host process restarts.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._ensure_runtime_root`  (lines 641–659)

```
async def _ensure_runtime_root(self, container_id: str, conversation_id: UUID) -> None
```

**Purpose**: Creates the private runtime directory inside the container with the correct owner and permissions. This gives the sandbox a safe place for internal files that should not be world-readable.

**Data flow**: It receives a container id and conversation id, computes that conversation’s runtime path, and runs `install -d` as root inside the container with the sandbox user and group ownership and mode `0700`. It raises RuntimeError if Docker reports failure.

**Call relations**: `DockerCarrier.create` and `DockerCarrier.attach` call this before returning a handle, so later commands can rely on the runtime root existing.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create); 1 external calls (sandbox_runtime_root).


##### `manifest`  (lines 662–667)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It names the carrier and tells the system that `DockerCarrier` is the factory for the Docker sandbox backend.

**Data flow**: It takes no input. It constructs a CarrierSpec for the Docker carrier and wraps it in a Manifest with a version, then returns that Manifest.

**Call relations**: The extension loader calls this to discover what this file provides. The returned manifest is how the rest of the system learns that the `docker` carrier name maps to `DockerCarrier`.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox setup and request handling`

A sandbox is like a disposable workshop for one conversation: commands run there, files live there, and network access is routed through UFO’s proxy. This file teaches UFO how to create, resume, prepare, use, and reconnect to that workshop when the workshop is hosted by E2B.

The important complication is that E2B sandboxes can pause when their lease expires. Pausing keeps the disk and processes, but cuts live connections. So this file keeps a local record of each conversation’s current sandbox lease, renews it before long work, and reconnects when needed. It deliberately does not delete sandboxes, because deleting one would also delete the conversation’s only workspace.

When a sandbox is opened, the carrier installs or checks the UFO client binary, installs the proxy certificate authority (a certificate the sandbox trusts for outbound TLS traffic), ensures `/workspace` exists, and limits workload memory and process count so user commands cannot starve the sandbox daemon itself.

For commands, it wraps each command in its own process group so timeouts or explicit stops can kill the whole tree of child processes. It also tracks containers that stop answering and probes them briefly before trusting them with another long command. File reads and writes use E2B’s file API, while port dialing returns an external address plus the E2B traffic token needed to reach it.

#### Function details

##### `E2BCommandHandle.wait`  (lines 179–179)

```
async def wait(self) -> E2BCommandResult
```

**Purpose**: This protocol method represents waiting for a command that has already been started in the sandbox. It is used when the carrier launches a command in the background so it can learn the process id before waiting for completion.

**Data flow**: It starts with a running command handle. Waiting on it produces the same kind of result a normal command run would: standard output, standard error, and an exit code.

**Call relations**: The carrier’s command runner expects E2B background commands to provide this method. After launching a process, the carrier waits here unless it needs to stop or abandon the command.


##### `E2BCommands.run`  (lines 200–209)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None, background: Literal[False]=False) -> E2BCommandResult
```

**Purpose**: This protocol method describes E2B’s command execution call. It can either run a command to completion or start it in the background and return a handle for later waiting.

**Data flow**: It receives a shell command plus optional working directory, environment variables, user name, timeout, and background flag. It sends that command into the sandbox and returns either the final command result or a handle to the still-running command.

**Call relations**: Most sandbox work in this file eventually depends on this E2B SDK call: preparation commands, user commands, health probes, and stop signals all go through it.


##### `E2BFileStream.__aiter__`  (lines 216–216)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: This protocol method makes an E2B file stream usable as an asynchronous byte-by-byte or chunk-by-chunk reader. It allows large files to be read without loading them all at once.

**Data flow**: It starts with an open file stream from E2B. Iterating over it yields chunks of bytes until the remote file has been fully read.

**Call relations**: The carrier’s read path relies on this shape when it streams sandbox files back to callers.


##### `E2BFileStream.aclose`  (lines 218–218)

```
async def aclose(self) -> None
```

**Purpose**: This protocol method closes an open streamed file read. It matters because an unfinished stream holds a network connection until it is explicitly released.

**Data flow**: It receives the open stream object and closes its underlying connection. It returns no data, but it frees remote and local resources.

**Call relations**: The carrier calls this after file streaming ends, including when the reader stops early, so E2B connections are not leaked.


##### `E2BFiles.write`  (lines 222–222)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: This protocol method describes writing data into the sandbox filesystem. It is the safe path for sending raw bytes, since command execution only accepts shell text.

**Data flow**: It receives a path, text or bytes, and optionally a user. E2B writes that content inside the sandbox and returns an SDK-specific acknowledgement.

**Call relations**: The carrier uses this for user file uploads and for setup tasks such as staging the UFO client binary and certificate authority file.


##### `E2BFiles.read`  (lines 224–224)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: This protocol method describes opening a file from the sandbox for reading. In this file it is used in streaming mode so large output files can be passed back safely.

**Data flow**: It receives a path and a requested format. It opens the remote file and returns a stream that yields bytes from that file.

**Call relations**: The carrier’s `read` method calls this after renewing the sandbox lease, then hands the stream chunks back to the rest of UFO.


##### `E2BSandbox.get_host`  (lines 233–233)

```
def get_host(self, port: int) -> str
```

**Purpose**: This protocol method asks E2B for the outside address of a port exposed from inside the sandbox. It is how a service started in the sandbox becomes reachable from the caller.

**Data flow**: It receives an internal port number and turns it into the host name E2B uses for that sandbox and port. It does not itself open the network connection.

**Call relations**: The carrier’s `dial` method uses this address and adds the needed traffic token before returning a `DialTarget`.


##### `E2BSdk.create`  (lines 237–246)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes creating a new E2B sandbox from a template. The template decides the sandbox image and size.

**Data flow**: It receives a template reference, lease timeout, metadata, lifecycle and network settings, and the API key. E2B creates a sandbox and returns an object representing it.

**Call relations**: When no existing sandbox can be resumed, `E2BCarrier._resume_or_open` uses this call to start a fresh remote workspace.


##### `E2BSdk.connect`  (lines 248–254)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes reconnecting to an existing E2B sandbox. It also resumes a paused sandbox and sets a new lease.

**Data flow**: It receives a sandbox id, desired lease span, and API key. E2B returns a live sandbox object or reports that the sandbox no longer exists.

**Call relations**: All reconnect and lease-renewal paths are funneled through `E2BCarrier._connected`, which wraps this SDK call with retries and a total timeout.


##### `E2BCarrier.create`  (lines 306–390)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This opens the sandbox for a conversation and returns the handle the rest of UFO will use. It may resume a persisted sandbox, reuse this process’s live one, or create a new E2B sandbox.

**Data flow**: It receives a `SandboxSpec` containing the conversation id, desired size, proxy settings, run token, environment, and possible resume id. It chooses or opens the sandbox, prepares it if needed, stores a lease in memory, and returns a `SandboxHandle` with the sandbox id and runtime details.

**Call relations**: This is the main setup entry for the carrier. It checks cached leases with `_leased`, opens through `_resume_or_open`, prepares through `_ensure_client`, `_prepare_runtime`, or `_prepare_strictly`, drops bad leases through `_drop`, and finally hands a usable handle back to core UFO.

*Call graph*: calls 6 internal fn (_drop, _ensure_client, _leased, _prepare_runtime, _prepare_strictly, _resume_or_open); 8 external calls (__init__, __init__, __init__, timeout, emit_metric, log, egress_proxy_env, sandbox_runtime_root).


##### `E2BCarrier.attach`  (lines 392–417)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reconnects to a known sandbox only if it already exists. It is used for read-style access where opening a brand-new empty sandbox would be misleading.

**Data flow**: It receives a `SandboxSpec` and looks at its resume id. If there is no id or E2B says the sandbox is gone, it returns `None`; otherwise it reconnects, records a fresh lease, and returns a `SandboxHandle`.

**Call relations**: Unlike `create`, this never creates a new sandbox. It calls `_connected` to ask E2B directly, then returns a handle suitable for operations that inspect an existing conversation workspace.

*Call graph*: calls 1 internal fn (_connected); 3 external calls (__init__, __init__, sandbox_runtime_root).


##### `E2BCarrier._resume_or_open`  (lines 419–459)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: This chooses between resuming an existing sandbox and creating a new one. It keeps old conversations alive when possible, but recovers by opening a new sandbox if E2B no longer has the old one.

**Data flow**: It receives the desired sandbox spec and an optional sandbox id to resume. It tries `_connected` when an id exists; if that fails as not found, it picks the configured template for the requested size and calls the E2B SDK to create a sandbox.

**Call relations**: `create` delegates the open-or-resume decision here. This helper uses `_connected` for safe retries on resume, logs missed resumes, and hands back the sandbox that later preparation code will make ready.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 461–477)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: This prepares a sandbox when preparation must succeed before the caller can use it. It retries only transient transport failures, because setup commands are safe to repeat.

**Data flow**: It receives a sandbox and its spec. It runs `_prepare`; on certain network transport errors it logs, counts a retry metric, sleeps briefly, and tries again. If preparation still fails, it drops the cached lease and raises the error.

**Call relations**: `create` uses this for fresh sandboxes or cached sandboxes that are not proven prepared. It calls `_prepare`, `_drop`, logging, metrics, and sleep to turn unreliable startup communication into a bounded retry.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 479–541)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: This reconnects to an E2B sandbox with bounded retries. It exists because a remote control-plane call may simply stop answering, and callers need a clear upper limit on how long they wait.

**Data flow**: It receives a conversation id, sandbox id, and lease span. It calls the E2B SDK’s `connect`; on transport errors it retries with backoff until attempts or total time run out. It returns the connected sandbox or raises the provider error.

**Call relations**: `_resume_or_open`, `_sandbox`, and `attach` all reconnect through this one helper. That keeps resume behavior consistent for startup, mid-turn lease renewal, and read attachment.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 4 external calls (sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 543–545)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This performs the full preparation sequence for a sandbox. It makes sure the UFO client exists first, then prepares the runtime environment around it.

**Data flow**: It receives a sandbox and proxy certificate. It calls `_ensure_client`, then `_prepare_runtime`. It returns nothing, but leaves the sandbox ready for UFO commands.

**Call relations**: `_prepare_strictly` calls this when preparation is mandatory. It is the small connector between client installation and runtime setup.

*Call graph*: calls 2 internal fn (_ensure_client, _prepare_runtime); called by 1 (_prepare_strictly).


##### `E2BCarrier._prepare_runtime`  (lines 547–552)

```
async def _prepare_runtime(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This prepares the operating environment inside the sandbox. It installs proxy trust, ensures the workspace directory exists, and limits workload resource use.

**Data flow**: It receives a sandbox and certificate text. It writes and installs the certificate, creates or fixes `/workspace`, and applies memory and process ceilings to workload control groups.

**Call relations**: `create` may call this directly for resumed sandboxes with a short timeout, and `_prepare` calls it during strict setup. It delegates the three concrete setup steps to `_install_ca`, `_ensure_workspace`, and `_cap_workload.

*Call graph*: calls 3 internal fn (_cap_workload, _ensure_workspace, _install_ca); called by 2 (_prepare, create).


##### `E2BCarrier._ensure_client`  (lines 554–579)

```
async def _ensure_client(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure the sandbox has the exact UFO client binary this server expects. That matters because all real workload commands enter through that client.

**Data flow**: It receives a sandbox. It compares the installed client’s SHA-256 digest with the bundled client bytes; if they differ or the check fails, it uploads a staged copy, verifies it, installs it atomically, and marks the sandbox as client-ready in memory.

**Call relations**: `create` and `_prepare` call this before running workloads. It uses E2B command and file APIs directly, so later `exec`, `file_op`, and sandbox tasks can rely on the correct `ufo` command being present.

*Call graph*: called by 2 (_prepare, create); 2 external calls (sha256, uuid4).


##### `E2BCarrier._leased`  (lines 581–596)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: This looks up this process’s cached lease for a conversation and clears expired lease records. It prevents the carrier from keeping references forever for conversations it once touched.

**Data flow**: It receives a conversation id. It checks the in-memory lease map, removes entries whose local expiry time has passed, and returns the requested lease if one was present.

**Call relations**: `create` uses this when deciding whether a process-local sandbox can be reused. `_sandbox` uses it before deciding whether it must reconnect and renew the E2B lease.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 598–606)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This installs the proxy certificate authority into the sandbox’s trusted system certificates. Without it, sandbox programs may reject TLS connections routed through UFO’s proxy.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path, runs the install command as root, and turns command failure into a clear runtime error with the sandbox’s output.

**Call relations**: `_prepare_runtime` calls this as its first setup step. It uses E2B file writing and command execution to make outbound HTTPS traffic trust the UFO proxy.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._ensure_workspace`  (lines 608–617)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure `/workspace` exists and belongs to the normal sandbox user. That directory is the conversation’s working disk inside the sandbox.

**Data flow**: It receives a sandbox. It runs a root command that creates the directory if needed and changes ownership to the sandbox user. On failure, it raises a setup error with command details.

**Call relations**: `_prepare_runtime` calls this after certificate setup. Later command execution uses `/workspace` as its working directory.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._cap_workload`  (lines 619–629)

```
async def _cap_workload(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This applies resource ceilings to the parts of the sandbox where user work runs. It leaves memory for the sandbox daemon and kernel so heavy commands do not freeze the whole container.

**Data flow**: It receives a sandbox. It runs a root command that calculates an allowed memory maximum and sets memory and process limits for workload control groups. It raises a clear setup error if the command fails.

**Call relations**: `_prepare_runtime` calls this as part of making a sandbox safe for ongoing use. The limits then affect later commands run through `exec` and `exec_skill`.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier.exec`  (lines 631–679)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a normal user command inside the sandbox and returns its shell-like result. It includes the turn’s proxy environment so network traffic is routed and metered correctly.

**Data flow**: It receives a sandbox handle, command arguments, and timeout. It passes them to `_exec_with` with no forced root user, then returns the resulting output, error text, exit code, and possible timeout marker.

**Call relations**: This is the public command path for ordinary sandbox work. It delegates the detailed launch, lease, timeout, and cleanup behavior to `_exec_with`.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier.exec_skill`  (lines 681–685)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a command as root for server-controlled skill setup tasks. It is separate from normal execution because these maintenance operations need elevated permissions.

**Data flow**: It receives a handle, command arguments, and timeout. It sends them to `_exec_with` with the user set to root and returns the resulting execution record.

**Call relations**: Like `exec`, it relies on `_exec_with`; the difference is the user choice and the reduced environment appropriate for root setup work.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier._exec_with`  (lines 687–734)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, user: str | None) -> ExecResult
```

**Purpose**: This is the core command runner. It renews the lease, checks whether a previously silent sandbox is responsive, launches the command in its own process group, maps exits and timeouts into `ExecResult`, and records running groups for later cancellation.

**Data flow**: It receives a handle, command arguments, timeout, and optional user. It reconnects or reuses the sandbox, quotes the arguments into a shell command, starts it in the background to learn its process id, waits for completion, and returns output and exit status. On timeout it stops the process group if possible; on cancellation or unexpected failure it drops the lease.

**Call relations**: `exec` and `exec_skill` both call this. It uses `_sandbox` for leasing, `_still_there` for silent-container checks, `_stop_group` and `_mark_silent` for timeout cleanup, `_forget_group` when work ends, and `_drop` when the current lease can no longer be trusted.

*Call graph*: calls 6 internal fn (_drop, _forget_group, _mark_silent, _sandbox, _still_there, _stop_group); called by 2 (exec, exec_skill); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.stop_commands`  (lines 736–758)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: This stops commands that belong to a cancelled turn. It is careful to stop only that turn’s recorded process groups, not unrelated work sharing the same conversation sandbox.

**Data flow**: It receives a sandbox handle. It removes the tracked process groups for that container and turn; if there are any, it reconnects with a short lease and sends a stop signal to each group.

**Call relations**: This is the cleanup counterpart to `_exec_with`, which deliberately leaves commands running when its task is merely cancelled. It calls `_sandbox` only when there is something to stop, then delegates each signal to `_stop_group`.

*Call graph*: calls 2 internal fn (_sandbox, _stop_group).


##### `E2BCarrier._forget_group`  (lines 760–770)

```
def _forget_group(self, handle: SandboxHandle, pid: int) -> None
```

**Purpose**: This removes a process group from the in-memory list of commands still believed to be running. It keeps the tracking table from growing and prevents later stops from signaling a finished command.

**Data flow**: It receives a sandbox handle and process id. It finds the matching container-and-turn entry, removes that process id, and deletes the whole entry if no groups remain.

**Call relations**: `_exec_with` calls this after a command finishes or has been stopped by its timeout. `stop_commands` reads from the same tracking table later if a turn is explicitly stopped.

*Call graph*: called by 1 (_exec_with).


##### `E2BCarrier._stop_group`  (lines 772–798)

```
async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int, user: str | None) -> None
```

**Purpose**: This sends a kill signal to an entire process group in the sandbox. It targets the group rather than just the first shell so child processes do not keep running after a timeout or stop.

**Data flow**: It receives a sandbox, container id, process id, and user. It runs `kill -9` against the negative process id, which means the whole group. If the sandbox does not answer or the stop fails, it records a metric and, on timeout, marks the container as silent.

**Call relations**: `_exec_with` calls this when a command times out after launch. `stop_commands` calls it for every group left behind by a cancelled turn. It may call `_mark_silent` when even the stop command cannot get an answer.

*Call graph*: calls 1 internal fn (_mark_silent); called by 2 (_exec_with, stop_commands); 1 external calls (emit_metric).


##### `E2BCarrier._mark_silent`  (lines 800–804)

```
def _mark_silent(self, container_id: str) -> None
```

**Purpose**: This remembers that a sandbox recently stopped answering command requests. The mark is temporary so a heavily loaded but recoverable sandbox is not condemned forever.

**Data flow**: It receives a container id. It stores an expiry time in the `_silent` map based on the current clock and configured silent-mark duration.

**Call relations**: `_exec_with` marks a sandbox silent if a command launch times out before returning a process id. `_stop_group` marks it silent if even the cleanup signal times out. `_still_there` later consults and clears or expires the mark.

*Call graph*: called by 2 (_exec_with, _stop_group).


##### `E2BCarrier._still_there`  (lines 806–836)

```
async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None
```

**Purpose**: This gives a recently silent sandbox a short health check before trusting it with another long command. It fails fast when the command channel still appears wedged.

**Data flow**: It receives a sandbox object and container id. If there is no active silent mark, it returns immediately. If the mark expired, it removes it. Otherwise it runs a tiny `true` command with a short timeout; success clears the mark, while failure raises `SandboxUnreachable`.

**Call relations**: `_exec_with` calls this before launching a command. It turns earlier timeout evidence from `_mark_silent` into either a quick recovery or a clear unreachable error.

*Call graph*: called by 1 (_exec_with); 3 external calls (__init__, emit_metric, log).


##### `E2BCarrier.write`  (lines 838–849)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This uploads bytes into the sandbox filesystem. It uses E2B’s file API because shell commands are not a good channel for arbitrary binary content.

**Data flow**: It receives a sandbox handle, path, and bytes. It obtains a sandbox lease through `_sandbox`, writes the content to the path, and returns nothing. If the write fails, it drops the cached lease and re-raises the error.

**Call relations**: This is the carrier’s public file-upload path. It depends on `_sandbox` for a valid connection and `_drop` to avoid trusting a lease after a failed provider call.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 851–871)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This streams a file out of the sandbox without loading the whole file into memory. It is used for produced files that may be large.

**Data flow**: It receives a handle and path. It renews the sandbox lease for a full autosuspend span, opens an E2B stream, yields each byte chunk to the caller, and always closes the stream afterward. If E2B reports missing file, it raises Python’s normal `FileNotFoundError`.

**Call relations**: This is the carrier’s public file-download path. It calls `_sandbox` to keep the sandbox alive during the transfer and `_drop` if opening the stream fails for other reasons.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 873–878)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This performs structured filesystem operations through the UFO client inside the sandbox. It gives higher-level callers one path for operations like listing or manipulating files.

**Data flow**: It receives a handle, operation name, and parameter dictionary. It delegates to `ufo_fs_file_op`, which runs the appropriate `ufo fs` command through this carrier, and returns a dictionary result.

**Call relations**: This is a thin bridge from the carrier interface to the shared UFO filesystem helper. It relies on the client installed by `_ensure_client` and the command behavior provided by `exec`.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `E2BCarrier.dial`  (lines 880–901)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This returns the external address for a service listening on a port inside the sandbox. It is how browser debugging ports, preview servers, or similar in-sandbox services become reachable from outside.

**Data flow**: It receives a handle and port. It renews the sandbox lease long enough for an outside exchange, asks the sandbox for the host name for that port, adds the E2B traffic access token when present, and returns a TLS-enabled `DialTarget`.

**Call relations**: Callers use this after starting a service inside the sandbox. It calls `_sandbox` with a longer lease floor and maps a missing E2B sandbox into the carrier-level `SandboxUnreachable` error.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 903–945)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: This returns a usable sandbox object and makes sure its E2B lease covers the work about to happen. It avoids a network reconnect when the current cached lease is still long enough.

**Data flow**: It receives a handle, the seconds of lease needed, and an optional minimum lease span. It checks `_leased`; if the cached sandbox id matches and has enough time left, it returns it. Otherwise it removes the old lease, reconnects through `_connected`, stores a new lease, logs the renewal, and returns the sandbox.

**Call relations**: This is the central lease gate for command execution, file reads and writes, port dialing, and stop signals. Those public operations state how long they need, and `_sandbox` decides whether to reuse or reconnect.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 5 (_exec_with, dial, read, stop_commands, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 947–952)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: This forgets a cached lease after a provider call fails. It does not delete the remote sandbox; it only stops this process from trusting a local deadline that may no longer reflect reality.

**Data flow**: It receives a conversation id and a short label saying what was happening. It removes that conversation from the live lease map and writes a log entry.

**Call relations**: `create`, `_prepare_strictly`, `_exec_with`, `write`, and `read` call this when setup or sandbox I/O leaves the connection state uncertain. The next operation will reconnect instead of reusing a possibly bad object.

*Call graph*: called by 5 (_exec_with, _prepare_strictly, create, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 955–972)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: This parses the environment variable that maps UFO sandbox sizes to E2B template references. It prevents the service from starting with missing or extra size mappings.

**Data flow**: It receives a comma-separated string such as `small=...,medium=...,large=...`. It splits it into a dictionary, validates every entry shape, checks that the sizes exactly match UFO’s supported sizes, and returns the map.

**Call relations**: `build_e2b_carrier` calls this during extension setup. The resulting map is later used by `_resume_or_open` when creating a fresh sandbox of a requested size.

*Call graph*: called by 1 (build_e2b_carrier).


##### `build_e2b_carrier`  (lines 975–984)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: This constructs the E2B carrier from process configuration. It is the factory registered in the manifest.

**Data flow**: It reads the E2B API key and template mapping from environment variables, validates the template mapping with `sandbox_templates`, reads the correct UFO client binary for E2B’s Linux target, and returns an `E2BCarrier` instance.

**Call relations**: The manifest points core UFO at this factory when the `e2b` carrier is selected. After construction, the returned carrier serves create, exec, file, and dial requests.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (__init__, client_binary).


##### `manifest`  (lines 987–999)

```
def manifest() -> Manifest
```

**Purpose**: This advertises the E2B carrier extension to UFO’s plugin system. It tells core that a carrier named `e2b` exists and how to build it.

**Data flow**: It creates a `Manifest` containing the extension name, version, and one `CarrierSpec` with the carrier factory, off-cluster flag, and supported sizes. The manifest object is returned to the loader.

**Call relations**: This is the file’s registration point. When UFO discovers extensions, this manifest lets the sandbox backend setting `e2b` resolve to `build_e2b_carrier`.

*Call graph*: 2 external calls (__init__, __init__).


### Terminal transport bridges
User-connected terminals are exposed as sandbox carriers, including Redis-backed transport across server pods.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `cross-cutting request handling`

In a single-process setup, a user’s terminal connection and the code asking that terminal to run something can meet in memory. In a shared fleet, those two pieces may be on different pods, so memory is no longer enough. This file builds a rendezvous point, like a staffed message desk: one pod says “this conversation has a live terminal here,” another pod leaves an operation request, and the terminal side later posts back the result.

Redis is used for the reliable message path. Redis Streams are append-only message lists that readers can wait on and replay from. The blob store is used when the actual input or output bytes are too large to comfortably place in Redis. The code also uses short-lived Redis keys with expiry times, called TTLs, so abandoned operations clean themselves up instead of leaving future turns stuck.

The main class, RedisTerminals, publishes live terminal bindings, waits for terminals to appear, sends one operation at a time per conversation, delivers replies, and cleans up temporary keys. A Redis lock prevents two operations from being sent to the same conversation at once. A Lua script inside Redis claims each operation atomically, meaning two reconnecting terminal streams cannot accidentally run the same command twice.

#### Function details

##### `_text`  (lines 55–58)

```
def _text(value: bytes | str) -> str
```

**Purpose**: Turns a Redis field into normal Python text. Redis may return either bytes or strings depending on client settings, and this helper makes later code read both safely.

**Data flow**: It receives one value from Redis. If the value is already text, it returns it unchanged; if it is bytes, it decodes those bytes into text. The output is always a string.

**Call relations**: This is a small translation step used wherever Redis data is read back: operation decoding, reply decoding, binding reads, gate checks, Lua-script results, and flat field conversion all lean on it before interpreting stored values.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 61–66)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: Converts the flat field list returned by the Redis Lua script into a dictionary-like field map. This makes an operation record easy to read by field name.

**Data flow**: It receives an object that should be a list shaped like field, value, field, value. It checks that the shape is a list, converts each item to text, then pairs neighboring items into a map. The result is the operation’s fields keyed by name.

**Call relations**: RedisTerminals.next_op uses this after the Lua script successfully claims an operation. It prepares the raw Redis result for RedisTerminals._decode_op, which turns it into a TerminalOp.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 69–74)

```
def _bind_payload(cwd: str, member_id: UUID | None, runtime_id: str) -> str
```

**Purpose**: Builds the small JSON record that says where a terminal is and which member it belongs to. Both live terminal heartbeats and in-flight operations use this same shape.

**Data flow**: It receives the working directory, optional member ID, and runtime ID. It converts those values into a JSON string, using a hex form for the member ID when present. The output is stored in Redis as the terminal binding.

**Call relations**: RedisTerminals._heartbeat uses it to publish the live binding, and RedisTerminals._run_op uses it to pin the same binding while an operation is running.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 148–157)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the entries from a Redis XREAD response and rejects unexpected response shapes. This protects the code from silently misreading a Redis stream reply.

**Data flow**: It receives the raw response from a Redis stream read. If there is no response, it returns an empty list. If the response has the expected list shape, it returns the entries inside it; otherwise it raises an error.

**Call relations**: RedisTerminals._await_reply uses this whenever it waits for a reply message. It keeps reply-waiting code focused on actual entries rather than Redis response formatting.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 185–202)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the current asyncio event loop. This matters because asynchronous Redis clients attach their waiting work to the loop that created them.

**Data flow**: It looks up the currently running event loop. If this RedisTerminals instance already has a Redis client for that loop, it returns it; otherwise it creates one with bounded socket timeouts and saves it. The result is a Redis connection object ready for this loop.

**Call relations**: Almost every Redis operation in the class passes through this method. It supports heartbeat publishing, binding reads, sending operations, waiting for replies, serving terminal reads, delivering replies, and cleanup.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 204–205)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key name for a conversation’s live terminal binding. This key is refreshed while a terminal connection is held.

**Data flow**: It receives a conversation ID and formats it into the standard Redis key string for live bindings. Nothing is read or changed.

**Call relations**: RedisTerminals._heartbeat writes to this key, and RedisTerminals._read_binding checks it first when looking for a connected terminal.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 207–208)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key name for the binding pinned by an operation that is already running. This lets other accessors still find the terminal while the live held stream is temporarily gone.

**Data flow**: It receives a conversation ID and returns the standard Redis key string for that conversation’s in-flight binding. It has no side effects.

**Call relations**: RedisTerminals._run_op writes this key before sending an operation, RedisTerminals._read_binding uses it as a fallback, and RedisTerminals._clear_op deletes it during cleanup.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 210–211)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name where operation requests for one conversation are posted. This is the queue the terminal side watches.

**Data flow**: It receives a conversation ID and returns the stream key string for operation messages. It does not touch Redis itself.

**Call relations**: RedisTerminals._run_op appends operation requests to this stream, RedisTerminals.next_op reads and claims from it, and RedisTerminals._clear_op removes completed entries from it.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 213–214)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: Builds the Redis Stream name where the answer for one operation is posted. Each operation gets its own small reply stream.

**Data flow**: It receives an operation ID and returns the reply stream key string. It only creates the name; other methods do the reading and writing.

**Call relations**: RedisTerminals._deliver_reply appends replies to this stream, RedisTerminals._await_reply waits on it, and RedisTerminals._clear_op deletes it after the operation is over.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 216–217)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key used as the per-conversation lock. The lock makes sure only one terminal operation is in progress for a conversation at a time.

**Data flow**: It receives a conversation ID and returns the lock key string. No Redis operation happens here.

**Call relations**: RedisTerminals.send uses this key before posting an operation, so queued sends wait their turn instead of racing each other.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 219–220)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key used as the delivery marker for an operation. This marker records that a terminal stream has already claimed the operation for display.

**Data flow**: It receives an operation ID and returns the delivery-marker key string. It does not create or delete the marker by itself.

**Call relations**: The Lua script used by RedisTerminals.next_op creates delivery markers, and RedisTerminals._clear_op later removes the marker as part of cleanup.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 222–223)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key that stores an operation’s metadata, such as its conversation and member. This metadata is used to decide whether copy-in data or replies are allowed.

**Data flow**: It receives an operation ID and returns the metadata key string. It does not read or write the metadata itself.

**Call relations**: RedisTerminals._run_op writes this key, RedisTerminals.staged and RedisTerminals._deliver_reply read it for safety checks, and RedisTerminals._clear_op deletes it.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 225–226)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for an operation’s input body. This is where larger bytes sent into the terminal are staged.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s input body. It does not access the blob store itself.

**Call relations**: RedisTerminals._run_op writes the body under this key, RedisTerminals.staged reads it when serving the terminal side, and RedisTerminals._clear_op deletes it afterward.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 228–229)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for a large operation reply. Small replies stay in Redis; large replies are stored under this key.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s reply body. The method only names the storage location.

**Call relations**: RedisTerminals._deliver_reply writes large replies there, RedisTerminals._decode_reply reads them back for the sender, and RedisTerminals._clear_op removes them when finished.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 231–253)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that this pod currently holds a terminal connection for a conversation. It starts or shares a background heartbeat that keeps the Redis binding alive.

**Data flow**: It receives the conversation ID, working directory, optional member ID, and optional runtime ID. Under a thread lock, it creates a local hold if needed, starts the heartbeat task, and increments the connection count. It does not return a value.

**Call relations**: Code outside this file calls this when a held terminal stream opens. It starts RedisTerminals._heartbeat, which makes the terminal visible to sends arriving on other pods.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 255–264)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Notes that one held terminal connection for a conversation has ended. When the last local connection leaves, it stops the heartbeat instead of deleting the Redis binding immediately.

**Data flow**: It receives a conversation ID. Under a lock, it finds the local hold, lowers its connection count, and if the count reaches zero cancels the heartbeat task and removes the local hold. It returns nothing.

**Call relations**: Code outside this file calls this when a held stream closes. The Redis key is left to expire naturally, which avoids deleting a fresh binding created by a reconnecting pod.


##### `RedisTerminals._heartbeat`  (lines 266–280)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Keeps a live terminal binding fresh in Redis while this pod holds the connection. It is the repeating pulse that tells other pods the terminal is still available.

**Data flow**: It receives the conversation and binding details, turns them into a JSON payload, and repeatedly writes that payload to the binding key with an expiry time. Between writes it sleeps. If Redis has a temporary problem, it ignores that one failed pulse and tries again later.

**Call relations**: RedisTerminals.connect starts this as a background task. RedisTerminals._read_binding later reads the key it refreshes when another pod needs to find the terminal.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 282–295)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns this pod’s local view of a terminal workspace, if this pod is the one holding it. It avoids Redis because local callers can answer from memory.

**Data flow**: It receives a conversation ID and checks the local holds under a lock. If there is no hold, it returns None. If there is one, it creates and returns a TerminalWorkspace containing the directory, member, and runtime ID.

**Call relations**: This is a local lookup counterpart to RedisTerminals.arrived. It is useful when the same pod that holds the terminal needs to describe the workspace without going through Redis.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 297–310)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal binding to appear for a conversation. This covers normal reconnect gaps where the user’s terminal stream drops and reconnects on another pod.

**Data flow**: It receives a conversation ID and a grace period in seconds. It repeatedly asks RedisTerminals._read_binding for the workspace until one appears or the deadline passes, sleeping between attempts. It returns the workspace or None.

**Call relations**: RedisTerminals.send calls this before trying to send an operation. If it cannot find a terminal within the allowed grace period, send reports that no terminal is connected.

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 312–330)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Reads the terminal binding from Redis and turns it into a workspace object. It checks both the live connection key and the in-flight operation pin.

**Data flow**: It receives a conversation ID, reads the live binding key, and if missing reads the in-flight key. If neither exists, it returns None. If one exists, it parses the JSON and returns a TerminalWorkspace with directory, member, and runtime ID.

**Call relations**: RedisTerminals.arrived uses this while waiting for a terminal. The heartbeat writes the live binding, and RedisTerminals._run_op writes the in-flight binding that this method can fall back to.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 332–388)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one operation to a conversation’s terminal and waits for the reply. It is the main “please run this in the user’s terminal” entry point for this transport.

**Data flow**: It receives the conversation, operation details, timeout, and optional body bytes. It first waits for a terminal binding, builds a TerminalOp, takes a Redis lock for that conversation, and then runs the operation with its own reply deadline. It returns reply bytes, or raises a terminal-specific error if the terminal is absent, unreachable, busy too long, or silent.

**Call relations**: This method ties together RedisTerminals.arrived, RedisTerminals._lock_key, RedisTerminals._run_op, and Redis lock release. It is used by higher-level terminal code when a workflow needs work done by the user’s terminal.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 6 external calls (__init__, __init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 390–436)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: Performs the actual post-lock operation send. It pins the binding, stores any input body, posts the operation to Redis, waits for the reply, and then cleans up.

**Data flow**: It receives the conversation, TerminalOp, optional body, known workspace binding, and deadline. It writes metadata and an in-flight binding to Redis, stores the body in the blob store if present, appends the operation to the conversation stream, waits for the reply, and finally clears operation state. The output is the reply bytes.

**Call relations**: RedisTerminals.send calls this only after acquiring the per-conversation lock. It hands the posted stream entry to RedisTerminals.next_op on the terminal side and waits through RedisTerminals._await_reply for RedisTerminals.resolve to deliver the answer.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 438–446)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: Turns a TerminalOp object into Redis Stream fields. This gives Redis a simple string-based record for the operation request.

**Data flow**: It receives a TerminalOp and creates a dictionary containing operation ID, kind, timeout, name, argument, and parameters. The result is ready to pass to Redis XADD.

**Call relations**: RedisTerminals._run_op uses this immediately before appending an operation to the operation stream. RedisTerminals._decode_op later performs the reverse conversion on the terminal-reading side.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 448–456)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: Turns operation fields read from Redis back into a TerminalOp object. This gives the terminal side a normal structured operation to execute or render.

**Data flow**: It receives a map of Redis fields, converts text fields as needed, parses the timeout as an integer, and creates a TerminalOp. Missing optional fields become empty strings.

**Call relations**: RedisTerminals.next_op calls this after it has claimed an operation. It is the mirror image of RedisTerminals._op_fields.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 458–481)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: Waits for the reply to one operation, but only until the operation’s deadline. This prevents a workflow from hanging forever if the terminal disappears.

**Data flow**: It receives an operation ID, total reply deadline, and the user-facing timeout. It repeatedly reads the operation’s reply stream with bounded blocking waits. When a reply entry arrives, it decodes it; if the deadline expires, it raises TerminalGone.

**Call relations**: RedisTerminals._run_op calls this after posting an operation. RedisTerminals._deliver_reply writes the stream entry this method is waiting for, and RedisTerminals._decode_reply turns that entry into bytes or an error.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 483–496)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: Interprets a reply stream entry. It distinguishes failed operations, large blob-backed replies, and small inline replies.

**Data flow**: It receives an operation ID and reply fields. If the fields contain a failure message, it raises TerminalOpFailed. If the fields point to a blob, it reads the reply bytes from the blob store with a timeout. Otherwise it base64-decodes the inline bytes and returns them.

**Call relations**: RedisTerminals._await_reply calls this once a reply arrives. RedisTerminals._deliver_reply writes replies in the same formats this method understands.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 498–529)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for and claims the next operation for a conversation’s terminal. The claim step prevents reconnecting or duplicate held streams from running the same operation twice.

**Data flow**: It receives a conversation ID and optionally an operation ID to exclude. It runs a Redis Lua script that scans old operation entries, removes expired ones, skips excluded or already-claimed entries, and marks one unclaimed entry as delivered. If none is ready, it waits briefly for stream activity and tries again. It returns a TerminalOp or raises TerminalGone on Redis failure.

**Call relations**: The terminal-held stream calls this to learn what to render next. It consumes entries written by RedisTerminals._run_op and prepares them through _pairs and RedisTerminals._decode_op.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 531–546)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Fetches the input body staged for an in-flight operation, if the requester is allowed to see it. This lets any pod serve the bytes to the terminal side through the shared blob store.

**Data flow**: It receives conversation ID, operation ID, and optional member ID. It reads operation metadata from Redis, checks that the conversation and member match, then reads the body blob with a timeout. It returns the bytes, or None if the metadata is missing, the gate fails, the blob is missing, or the read times out.

**Call relations**: Terminal-serving code outside this file uses this after RedisTerminals.next_op reveals an operation that has a body. RedisTerminals._run_op creates the metadata and blob key that this method reads.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 548–563)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts the terminal’s reply for an operation and schedules delivery to Redis. It returns immediately so the HTTP reply path does not wait on Redis or blob storage.

**Data flow**: It receives the conversation, operation ID, reply bytes, optional failure text, and optional member ID. It creates a background task to deliver the reply and immediately returns True. The actual Redis write happens later in that task.

**Call relations**: Reply-handling code outside this file calls this when the terminal has finished an operation. It hands work to RedisTerminals._deliver_reply through RedisTerminals._spawn.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 565–594)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: Writes an operation reply to the shared rendezvous, after checking that it belongs to the right conversation and member. It stores large replies in the blob store and small replies directly in Redis.

**Data flow**: It receives conversation ID, operation ID, reply bytes, optional failure text, and optional member ID. It reads operation metadata, rejects missing or mismatched replies with a warning, then builds reply fields: failure text, blob pointer, or base64 inline data. It appends those fields to the reply stream and sets an expiry.

**Call relations**: RedisTerminals.resolve schedules this in the background. RedisTerminals._await_reply is the waiting sender that later reads the reply stream this method writes.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 596–607)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a request is allowed to access an operation’s staged body or post its reply. It fails closed when the conversation or member does not match.

**Data flow**: It receives raw operation metadata, a conversation ID, and optional member ID. It parses the metadata JSON, compares the stored conversation to the requested one, and if a member was provided checks it against the stored member. It returns true only when the request matches.

**Call relations**: RedisTerminals.staged uses this before giving out staged input bytes, and RedisTerminals._deliver_reply uses it before accepting a terminal reply.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 609–612)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports no local in-flight operation for this cross-pod transport. The operation may be waiting on another pod, so this instance cannot safely show a partial local view.

**Data flow**: It receives a conversation ID but does not read Redis or local state. It always returns None.

**Call relations**: This satisfies the same terminal transport interface as in-process implementations. For this Redis-backed version, reliable in-flight truth lives in Redis keys and streams, not in one pod’s memory.


##### `RedisTerminals._clear_op`  (lines 614–635)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: Cleans up the temporary Redis keys, stream entries, and blob objects created for one operation. The cleanup is best-effort because expiry times also protect the system if deletion fails.

**Data flow**: It receives the conversation ID, operation ID, and optional stream entry ID. It deletes the operation stream entry if known, removes metadata, delivery marker, in-flight binding, and reply stream keys, then tries to delete both input and reply blobs with time limits. It returns nothing.

**Call relations**: RedisTerminals._run_op calls this in a finally block after reply waiting ends or is cancelled. Safety comes from the claim markers and TTLs; this method keeps Redis and the blob store tidy.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 637–644)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: Starts a background task and keeps a reference to it until it finishes. This prevents fire-and-return reply delivery from being lost silently.

**Data flow**: It receives a coroutine and an event loop. It wraps the coroutine with RedisTerminals._logged, schedules it as a task, stores the task in a set, and arranges for the task to remove itself when complete. It returns nothing.

**Call relations**: RedisTerminals.resolve uses this to schedule RedisTerminals._deliver_reply without blocking the caller. RedisTerminals._logged provides the warning behavior if the task fails.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 646–650)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: Runs a background coroutine and logs a warning if it fails. This makes asynchronous delivery errors visible instead of disappearing.

**Data flow**: It receives a coroutine, awaits it, and catches any exception. On failure, it emits a warning containing the error text. It does not return useful data.

**Call relations**: RedisTerminals._spawn wraps background delivery work with this method. In practice, it watches RedisTerminals._deliver_reply tasks started by RedisTerminals.resolve.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).


### `core/src/ufo/sandbox/terminal.py`

`io_transport` · `terminal connection and sandbox operation handling`

A normal sandbox can be reached by dialing into it. A user's terminal is different: the server cannot open a new connection to the user's laptop whenever it wants. Instead, the terminal keeps checking in through an existing surface connection, receives one instruction, runs it locally, and sends the result back as the next request. This file is the meeting place for those two halves.

The central idea is a per-conversation slot. A conversation can have at most one terminal operation in flight, like a single service counter where requests must queue. This matters because the user's connection may briefly disappear and reconnect between steps, so the waiting operation cannot be tied to one specific network connection. The slot remembers the current working directory, who owns the terminal, the current operation, any staged bytes for writes, and the server task waiting for the reply.

`Terminals` is the in-process version of this rendezvous. It uses a lock, meaning a small guard that stops two threads from changing the same shared state at once. `TerminalCarrier` is the sandbox adapter: it turns high-level actions such as exec, read, write, and file browsing into terminal operations. It also rewrites logical `/workspace` paths into the real directory where the user launched UFO. Without this file, terminal-bound work would either fail during reconnects, run against the wrong folder, or lose operation replies.

#### Function details

##### `TerminalTransport.connect`  (lines 250–256)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: This is the interface method for announcing that a terminal connection is now available for a conversation. Implementations use it to remember where the terminal is and which member is allowed to answer for it.

**Data flow**: A conversation id, current directory, member id, and optional runtime id go in. The transport records that this conversation has a live terminal at that location. Nothing is returned.

**Call relations**: This belongs to the `TerminalTransport` protocol, which is the shared contract used by `TerminalCarrier` and surface routes. The concrete in-process behavior is provided by `Terminals.connect`.


##### `TerminalTransport.disconnect`  (lines 258–258)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: This is the interface method for announcing that a terminal connection has gone away. Implementations use it to reduce or remove the stored terminal binding.

**Data flow**: A conversation id goes in. The transport updates its connection count or clears the conversation's terminal state. Nothing is returned.

**Call relations**: It is part of the transport contract. The concrete local implementation is `Terminals.disconnect`.


##### `TerminalTransport.workspace`  (lines 260–260)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: This is the interface method for asking where a conversation's terminal is currently rooted. It gives callers a quick snapshot without waiting for a reconnect.

**Data flow**: A conversation id goes in. If a terminal is bound, a small workspace record comes out; otherwise the result is empty.

**Call relations**: It is defined by the protocol so different backends can expose the same workspace lookup. The local implementation is `Terminals.workspace`.


##### `TerminalTransport.arrived`  (lines 262–262)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: This is the interface method for waiting until a terminal is connected, up to a given grace period. It prevents normal short reconnect gaps from being treated as failure.

**Data flow**: A conversation id and a number of seconds go in. The transport waits if needed, then returns the terminal workspace if one appears, or nothing if time runs out.

**Call relations**: The protocol requires this because `TerminalCarrier.create`, `TerminalCarrier.attach`, and `Terminals.send` need a common way to wait for a terminal across local or distributed transports.


##### `TerminalTransport.send`  (lines 264–273)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: This is the interface method for asking the terminal to perform one operation and return bytes as the answer. It is the main bridge from server-side sandbox calls to client-side terminal work.

**Data flow**: The caller provides the conversation, operation kind, timeout, optional name, path-like argument, JSON parameters, and optional body bytes. The transport delivers that request to the terminal and waits. The returned value is the terminal's byte reply, or an error is raised if the terminal cannot answer.

**Call relations**: This is the key operation that `TerminalCarrier` relies on for exec, read, write, file operations, and skill loading. The in-process version is `Terminals.send`.


##### `TerminalTransport.next_op`  (lines 275–277)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: This is the interface method used by the connected terminal side to ask, “What should I do next?” It hands the terminal the next pending operation for the conversation.

**Data flow**: A conversation id and optionally an operation id to skip go in. The transport waits until there is an operation that should be delivered, then returns a `TerminalOp` instruction.

**Call relations**: Surface routes that hold the terminal connection use this protocol method. The local implementation is `Terminals.next_op`, which pairs with `Terminals.send`.


##### `TerminalTransport.staged`  (lines 279–281)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: This is the interface method for fetching bytes that were staged for an in-flight terminal operation, mainly file writes. It keeps large copy data out of the small directive message.

**Data flow**: A conversation id, operation id, and optional member id go in. If the matching operation is still active and the member is allowed, the staged bytes come out; otherwise the result is empty.

**Call relations**: The terminal-side route uses this after receiving an operation that needs a body. The local implementation is `Terminals.staged`.


##### `TerminalTransport.resolve`  (lines 283–290)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: This is the interface method for submitting the terminal's answer to an operation. It lets the waiting server task continue with either a successful reply or a reported operation failure.

**Data flow**: A conversation id, operation id, reply bytes, optional failure text, and optional member id go in. The transport checks that this is the current operation and wakes the waiting sender. It returns true if the answer matched something in flight, false if it was stale or unauthorized.

**Call relations**: This is called by the terminal-facing surface route after the client finishes an operation. The local implementation is `Terminals.resolve`.


##### `TerminalTransport.in_flight`  (lines 292–292)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: This is the interface method for inspecting the operation currently waiting for a reply. It is mainly useful for tests or operator-style visibility.

**Data flow**: A conversation id goes in. The current `TerminalOp` comes out if one exists; otherwise the result is empty.

**Call relations**: It is part of the transport contract. The local implementation is `Terminals.in_flight`.


##### `_wake`  (lines 295–304)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: This helper safely wakes a waiting async task from another thread. It matters because the terminal connection and the workflow waiting for the result may run on different event loops.

**Data flow**: A waiter, made of a future plus its event loop, and an answer object go in. The helper schedules a small setter on the waiter's own loop. Nothing is returned, but the future will receive the answer.

**Call relations**: `Terminals.connect` uses it to wake tasks waiting for a terminal to arrive. `Terminals.send` uses it to deliver a new operation to a waiting terminal stream. `Terminals.resolve` uses it to deliver the terminal's reply back to the server task.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 300–302)

```
def _set() -> None
```

**Purpose**: This tiny inner function performs the actual future update on the correct event loop. It avoids touching the future directly from the wrong thread.

**Data flow**: It closes over the future and answer from `_wake`. When the event loop runs it, it checks whether the future is still waiting and, if so, stores the answer in it.

**Call relations**: It is created only inside `_wake` and scheduled with the event loop's thread-safe callback mechanism. Its whole job is to complete the wake-up safely.


##### `Terminals.connect`  (lines 320–342)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: This records that a terminal stream has connected for a conversation. It also wakes anyone who was waiting for that terminal to appear.

**Data flow**: The conversation id, current working directory, member id, and optional runtime id go in. The method creates or updates the conversation slot, increases the connection count, and wakes arrival waiters. It returns nothing.

**Call relations**: Surface connection code calls this when the user's terminal checks in. It uses `_wake` so callers waiting in `Terminals.arrived` can continue on their own event loops.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 344–351)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: This records that one terminal connection for a conversation has ended. If no operation is waiting and no connections remain, it removes the slot.

**Data flow**: A conversation id goes in. The method lowers the connection count and may delete the stored terminal state. It returns nothing.

**Call relations**: Surface connection code calls this when a held terminal stream closes. It works with `Terminals.connect` to keep the per-conversation slot alive only as long as it is still useful.


##### `Terminals.workspace`  (lines 353–360)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: This returns the current terminal workspace for a conversation without waiting. It is a quick way to see where the terminal is standing.

**Data flow**: A conversation id goes in. If a slot exists, the method copies out the current directory, member id, and runtime id into a `TerminalWorkspace`; otherwise it returns nothing.

**Call relations**: This is the concrete implementation of `TerminalTransport.workspace`. It reads the same slot that `connect`, `send`, and `resolve` use.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 362–386)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: This waits for a terminal to be connected for a conversation, but only for a limited grace period. It exists because a normal client reconnect gap should not make a turn fail immediately.

**Data flow**: A conversation id and grace time go in. The method checks for an existing slot; if absent, it registers a future and waits until `connect` wakes it or the timeout expires. It returns a `TerminalWorkspace` if the terminal appears, otherwise nothing.

**Call relations**: `Terminals.send` calls this before sending work, and carrier code also waits through the transport interface. If the wait times out, it calls `Terminals._drop_arrival` to remove its unused waiter.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 388–393)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: This removes a no-longer-needed arrival waiter from the waiting list. It prevents timed-out waits from leaving stale entries behind.

**Data flow**: A conversation id and a future go in. The method filters that future out of the stored arrival waiters and deletes the list if it becomes empty. It returns nothing.

**Call relations**: `Terminals.arrived` calls this when its wait is over without a terminal arriving. It is a cleanup helper for the arrival-wait path.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 395–470)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: This asks the connected terminal to run one operation and waits for its reply. It serializes operations so the terminal receives only one job at a time.

**Data flow**: The caller supplies operation details and optional staged body bytes. The method waits for a terminal, waits for its turn if another operation is busy, creates a `TerminalOp`, stores it in the slot, wakes any terminal stream waiting in `next_op`, then waits for `resolve` to provide the reply. It returns reply bytes or raises an error if the terminal disappears, times out, or reports failure.

**Call relations**: `TerminalCarrier` indirectly depends on this for commands, file reads, writes, skill loading, and file-tool operations. It calls `Terminals.arrived` first, then `Terminals._take_turn`, and uses `_wake` to hand the operation to a waiting terminal watcher.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 6 external calls (__init__, __init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 472–503)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: This waits until the conversation's single operation slot is free. It is the queue at the service counter, making later operations wait instead of colliding with the current one.

**Data flow**: A conversation id, event loop, and timeout go in. If the slot is free, the method marks it busy and returns. If it is busy, it queues a future and waits until the current operation releases the slot or the deadline passes.

**Call relations**: `Terminals.send` calls this before installing a new operation. When `send` finishes, it wakes queued waiters so they can compete for the next turn.

*Call graph*: called by 1 (send); 6 external calls (__init__, __init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 505–530)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: This is what the terminal stream calls to receive the next operation it should run. It can return an already-waiting operation or wait for the next one to be sent.

**Data flow**: A conversation id and optional operation id to exclude go in. The method checks the slot: if there is an undelivered matching operation, it marks it delivered and returns it; otherwise it stores a watcher future and waits. The result is a `TerminalOp`.

**Call relations**: This pairs with `Terminals.send`. `send` creates the operation and wakes the watcher; `next_op` delivers it to the terminal side. The exclude id prevents a just-answered operation from being run again.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 532–544)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: This returns the body bytes attached to the currently active operation, usually the content for a file write. It also checks that the requesting member is allowed to see those bytes.

**Data flow**: A conversation id, operation id, and optional member id go in. The method verifies that the slot and operation match and that the member matches when required. It returns the staged bytes or nothing.

**Call relations**: The terminal-facing read projection uses this after receiving an operation from `next_op`. It reads the body that `Terminals.send` stored.


##### `Terminals.in_flight`  (lines 546–551)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: This reports the operation currently waiting for a terminal reply. It is a simple inspection hook.

**Data flow**: A conversation id goes in. The method returns the stored `TerminalOp` if the conversation has one in progress; otherwise it returns nothing.

**Call relations**: This implements the protocol's inspection method and reads the same slot populated by `Terminals.send`.


##### `Terminals.resolve`  (lines 553–582)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: This accepts the terminal's answer for the current operation and wakes the server task waiting in `send`. It ignores stale or unauthorized replies.

**Data flow**: A conversation id, operation id, reply bytes, optional failure text, and optional member id go in. The method checks that the reply belongs to the active operation, marks it resolved, and wakes the waiting sender with either reply bytes or a `TerminalOpFailed` marker. It returns true if the reply was accepted, false otherwise.

**Call relations**: Surface routes call this after the client posts an operation result. It uses `_wake` to return control to `Terminals.send`, which then either returns the bytes or raises the reported failure.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 598–644)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This opens a terminal-bound sandbox handle for a turn. It confirms that the connected terminal is in the expected workspace and prepares environment values for network egress through the proxy.

**Data flow**: A sandbox specification goes in. The method waits for the terminal, verifies its directory, builds proxy environment variables using the run token, and returns a `SandboxHandle`. It raises an error if no terminal arrives or if it is connected to the wrong folder.

**Call relations**: Sandbox orchestration calls this when the selected sandbox is the member's terminal. Later carrier methods use the returned handle for exec, reads, writes, and file operations.

*Call graph*: 4 external calls (__init__, __init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 646–660)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reconnects to an already bound terminal outside the main turn, for example for a file browser or off-turn write. It does not wait through a long grace period.

**Data flow**: A sandbox specification goes in. The method checks whether a terminal is currently bound at the requested resume path. If so, it returns a lightweight `SandboxHandle`; otherwise it returns nothing.

**Call relations**: Off-turn code uses this to reuse the same terminal binding. It relies on the transport's `arrived` method so a distributed transport can still find a terminal held by another process.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 662–676)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a command on the user's machine through the terminal sandbox. It first rewrites `/workspace` paths into the user's real workspace directory.

**Data flow**: A sandbox handle, argument tuple, and timeout go in. The method finds the real root directory, converts the command arguments for the host, then delegates to `_exec`. It returns an `ExecResult` with stdout, stderr, exit code, and timeout information.

**Call relations**: Higher-level sandbox users call this for command execution. It calls `_root`, uses `host_argv` from the session layer to rewrite paths, and then hands the actual run to `TerminalCarrier._exec`.

*Call graph*: calls 2 internal fn (_exec, _root); 1 external calls (host_argv).


##### `TerminalCarrier.load_skills`  (lines 678–689)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: This asks the connected client to load skill files under UFO's home directory. It packages the request as a terminal operation rather than running a shell command itself.

**Data flow**: A sandbox handle and JSON-like payload go in. The payload is serialized to compact JSON and sent through the terminal transport as a skills operation. The reply bytes become stdout in a successful `ExecResult`.

**Call relations**: Skill-loading code calls this through the carrier interface. It uses the transport's `send` method and converts a terminal-reported failure into a normal runtime error.

*Call graph*: 2 external calls (__init__, dumps).


##### `TerminalCarrier._exec`  (lines 691–726)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a command whose paths are already resolved for the user's machine. It is the lower-level command runner used when no further `/workspace` rewriting should happen.

**Data flow**: A handle, host-ready argument tuple, and timeout go in. The method sends an exec operation with arguments and environment, parses the JSON reply, decodes stdout and stderr from base64, normalizes timeout exit codes, and returns an `ExecResult`.

**Call relations**: `TerminalCarrier.exec` calls this after path rewriting. `TerminalCarrier._enumerate` also calls it for internally composed tree-walk commands, where rewriting again would corrupt paths.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (_enumerate, exec); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 728–741)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This writes bytes into a file in the terminal workspace. It stages the content through the rendezvous instead of putting large bytes in the directive message.

**Data flow**: A handle, logical path, and content bytes go in. The path is mapped to the user's real workspace, and the bytes are sent as the body of a write operation. The method returns nothing on success or raises an `OSError` on terminal-reported failure.

**Call relations**: File-copy code calls this through the sandbox carrier interface. It uses `_client_path` to rewrite the path and the terminal transport's `send` method to ask the client to perform the write.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 743–759)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads a file from the terminal workspace and yields it in chunks. It makes missing files look like `FileNotFoundError`, matching the behavior of other sandbox carriers.

**Data flow**: A handle and logical path go in. The path is mapped to the user's real workspace, a read operation is sent, and the returned bytes are split into fixed-size chunks. The output is an async stream of byte chunks.

**Call relations**: File-copy and download paths call this through the carrier interface. It uses `_client_path` for path mapping and the terminal transport for the actual read.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 761–849)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs one high-level `ufo fs` file operation, such as read, search, glob, or changes, where the user's files actually live. It also does extra setup for operations that need a consistent file listing.

**Data flow**: A handle, operation name, and parameter dictionary go in. The method rewrites workspace path parameters, may render office documents or PDFs locally through the document renderer, may run an enumeration command first for tree walks or changes scans, then sends the file operation to the terminal and parses the JSON result. A result dictionary comes out, or a clear error is raised.

**Call relations**: Higher-level file tooling calls this through the sandbox carrier interface. It calls `_root`, `_under_root`, `_enumerate`, and `_reply_object`, and it uses the terminal transport to ask the client to run the native file operation.

*Call graph*: calls 4 internal fn (_enumerate, _reply_object, _root, _under_root); 2 external calls (dumps, PurePosixPath).


##### `TerminalCarrier._enumerate`  (lines 851–872)

```
async def _enumerate(self, handle: SandboxHandle, op: str, walk_root: str, program: str, arguments: tuple[str, ...]=()) -> None
```

**Purpose**: This prepares a stable file listing for operations like grep, glob, and changes. It runs a small shell program on the user's machine before the actual file operation reads that listing.

**Data flow**: A handle, operation name, root path, shell program text, and optional arguments go in. The method builds a shell command with the chosen root, runs it through `_exec`, and checks the exit code. It returns nothing unless the listing failed, in which case it raises a value error.

**Call relations**: `TerminalCarrier.file_op` calls this before file operations that need precomputed enumeration. It delegates execution to `TerminalCarrier._exec` so the command runs through the same terminal operation path.

*Call graph*: calls 1 internal fn (_exec); called by 1 (file_op); 1 external calls (quote).


##### `TerminalCarrier.dial`  (lines 874–878)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This rejects attempts to expose a network port from a terminal-bound sandbox. A user's terminal sandbox has no remote per-port host that the server can dial.

**Data flow**: A handle and port go in. The method immediately raises `SandboxUnreachable` with an explanation. No dial target is returned.

**Call relations**: Code that expects sandbox port access may call this through the carrier interface. This carrier deliberately refuses and directs callers toward remote carriers for that feature.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 881–889)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: This decodes one captured command stream, such as stdout or stderr, from the terminal's exec reply. It treats a missing stream field as a bad reply shape rather than silent empty output.

**Data flow**: A parsed reply object and stream name go in. The function finds the matching base64 field, decodes it to bytes, and returns those bytes. If the field is missing or malformed, it raises an error.

**Call relations**: `TerminalCarrier._exec` calls this after `_reply_object` parses the exec reply. It supplies the raw stdout and stderr bytes used to build the final `ExecResult`.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 892–899)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: This parses a terminal operation reply as a JSON object. It gives callers a clear error if the client sends something that is not the expected structured reply.

**Data flow**: Raw reply bytes and the operation name go in. The function decodes the bytes as UTF-8, parses JSON, checks that the result is an object, and returns the dictionary.

**Call relations**: `TerminalCarrier._exec` uses this for exec replies, and `TerminalCarrier.file_op` uses it for file operation replies. The parsed object is then inspected by those callers.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 902–905)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: This returns the real workspace directory for a terminal sandbox handle. It enforces that terminal sandboxes must have a concrete bound directory behind `/workspace`.

**Data flow**: A sandbox handle goes in. If the handle has a workspace host path, that string comes out. If not, the function raises an error.

**Call relations**: `TerminalCarrier.exec` and `TerminalCarrier.file_op` call this before rewriting paths. `_client_path` also calls it as the first step in mapping a logical path to the user's machine.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 908–914)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: This maps a logical sandbox path like `/workspace/file.txt` to the real directory on the user's machine. It only strips the leading `/workspace`, so ordinary folder names containing “workspace” are not accidentally rewritten.

**Data flow**: A sandbox handle and path string go in. The function gets the handle's root directory and passes root plus path to `_under_root`. The returned string is the path the client should use.

**Call relations**: `TerminalCarrier.write` and `TerminalCarrier.read` use this before sending file operations to the terminal. It is a small path-mapping layer built on `_root` and `_under_root`.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 917–922)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: This performs the actual `/workspace`-to-real-root path rewrite. Paths outside `/workspace` are left unchanged.

**Data flow**: A real root directory and a path string go in. If the path starts under `/workspace`, the function replaces that prefix with the root and returns the new path. If not, it returns the original path.

**Call relations**: `_client_path` uses this for reads and writes. `TerminalCarrier.file_op` uses it directly to rewrite selected file-operation parameters before sending them to the terminal.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### Sandbox setup settings
Package markers, cache rules, client binary discovery, execution environments, and preview settings prepare sandbox runtime context.

### `core/src/ufo/sandbox/__init__.py`

`other` · `import/package discovery`

In Python projects, a folder often needs an `__init__.py` file to be treated as an importable package. This file is that marker for the `ufo.sandbox` area of the codebase. Think of it like a label on a drawer: the label does not do the work, but it tells Python and readers that the drawer contains a related set of tools. Because the file is empty, it does not define any functions, classes, settings, or startup behavior. Its value is structural: without it, some Python environments or packaging tools might not recognize this directory as a package, and imports such as `ufo.sandbox...` could fail or behave inconsistently.


### `core/src/ufo/sandbox/cache.py`

`config` · `startup and config load`

Sandboxes often need to download source code or packages from the public internet. Doing that directly every time is slower, harder to control, and riskier. This file is the small shared rulebook for a cache service: a trusted middle stop that can fetch and store approved public content, then serve it back to sandboxed jobs.

The file names the internal cache host, `cache.ufo.internal`, which is the address the proxy recognizes. It also lists the Git hosts and package registry hosts that are allowed to go through the cache. This allowlist matters because it prevents the cache from being misused as a tunnel to private or unexpected addresses.

For Git, the file builds configuration entries that quietly rewrite normal fetch URLs, such as GitHub clone URLs, so they go through the cache instead. Pushes are deliberately kept direct. In everyday terms, reads go through the library desk, but writes still go straight to the original owner.

The file also parses an optional cache daemon address from deployment configuration. If the address is missing, caching can be considered off. If it is malformed, the code raises an error instead of guessing, because a bad cache address is a deployment mistake that should be fixed loudly.

#### Function details

##### `cache_git_config`  (lines 46–54)

```
def cache_git_config() -> tuple[tuple[str, str], ...]
```

**Purpose**: This function returns the Git settings needed to fetch cached repositories through the internal cache while still allowing pushes to go to the original host. Someone would use it when preparing a sandbox or environment so ordinary Git commands benefit from the cache without changing user-facing URLs.

**Data flow**: It reads the fixed cache host and the list of Git hosts that are allowed to be cached. For each allowed host, it creates two Git configuration pairs: one that rewrites fetch-style URLs to the cache address, and one that preserves direct push behavior. It returns those pairs as an immutable tuple, ready to be applied to Git configuration.

**Call relations**: This is a helper for setup code that needs to prepare Git inside a sandbox or similar environment. When that setup wants cached fetches, it calls this function to get the exact configuration entries to install; after that, Git itself uses those entries when deciding where fetches and pushes should go.


##### `parse_cache_daemon`  (lines 57–66)

```
def parse_cache_daemon(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function reads the configured cache daemon address and turns it into a usable host and port. It also protects deployment setup by rejecting malformed addresses instead of silently disabling or misrouting the cache.

**Data flow**: It receives either no value or a text value expected to look like `host:port`. If the value is missing, it returns `None`, meaning there is no cache daemon configured. If the value is present, it splits it at the final colon, checks that a host and separator exist, converts the port text into a number, and returns the host and port together. If the format is wrong, it raises an error so the bad deployment setting is noticed.

**Call relations**: This fits into configuration loading or startup code that decides whether a local cache daemon is available. That code hands the raw setting to this function; the function hands back either a clear `None` for no cache or a parsed address that later connection code can use.


### `core/src/ufo/sandbox/client_binary.py`

`util` · `sandbox setup and test setup`

A sandbox needs a real `ufo` executable available in the right place. This file is the project’s single source of truth for finding that executable, so different parts of the system do not invent different paths. Think of it like a shipping label desk: it does not manufacture the package, but it tells everyone exactly which package to use.

The main rule is: use an existing binary, never start a build here. That matters because building the client can take minutes and may require tools that are not available while the sandbox is running. If no usable binary is found, the code raises an error that names the Cargo command needed to build one. Cargo is Rust’s build tool.

The search order is deliberate. First, it checks the `UFO_CLIENT_BINARY` environment variable, which lets a continuous integration job or deploy pipeline hand over a prebuilt artifact. If that is set, it must point to a real file. Next, it looks inside the repository’s Rust client build output, checking release builds before debug builds. If the caller is looking for a binary for the current machine, it also checks the normal command path, the same way a shell finds commands. If the caller asks for a specific Rust target triple, meaning a named platform such as Linux on x86-64, it only looks in that target’s build directory.

#### Function details

##### `client_binary`  (lines 32–59)

```
def client_binary(target: str | None=None) -> Path
```

**Purpose**: Finds the `ufo` executable that should be used for a sandbox or local subprocess. Callers can ask for the host machine’s binary, or for a binary built for a specific Rust target platform.

**Data flow**: It receives an optional target name. It first reads the `UFO_CLIENT_BINARY` environment variable; if present, that path must be a real file and is returned. If no override is set, it looks in the client crate’s build folders, first `release` and then `debug`, using either the normal target directory or the directory for the requested platform. If no target was requested, it also asks the operating system whether `ufo` is installed on the command path. If every check fails, it raises a `RuntimeError` with the exact build command the user should run.

**Call relations**: Other sandbox setup code, image-building code, and tests call this function when they need a real `ufo` binary path. Inside the function, it uses `pathlib.Path` to build and check file paths, and `shutil.which` to ask the system where an installed `ufo` command is, but only when looking for a binary for the current host.

*Call graph*: 2 external calls (Path, which).


### `core/src/ufo/sandbox/exec_env.py`

`domain_logic` · `sandbox open for off-turn probe execution`

A sandbox is a sealed place where the system can run commands. Those commands sometimes need to talk to outside services, such as Git hosts, Datadog, or connector command-line tools. This file prepares the small set of environment variables that make those calls work safely.

The important idea is that the sandbox does not get real passwords or API keys. Instead, it gets sentinels: harmless placeholder strings. When the sandbox makes an outgoing network request, an egress proxy sees the sentinel and swaps in the real credential only on the wire. An everyday analogy is a coat-check ticket: the person holding the ticket never has the coat, but the desk can match the ticket to the right coat when needed.

The main class, `ProbeEnv`, combines several sources: the current workspace, the conversation id, declared credential slots, active grants, and connector CLI settings. It exports Git configuration in the format Git expects, connector CLI variables for the right account, and provider variables such as an API-key environment variable plus the selected host. It is careful to export nothing when a credential is missing, ambiguous, or cannot be resolved. It also logs or warns in those cases so the failure is visible instead of silently using the wrong account.

#### Function details

##### `ProbeEnv.exports`  (lines 60–74)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, acting_member_id: UUID | None=None) -> dict[str, str]
```

**Purpose**: Builds the full environment dictionary for a probe running inside a sandbox. It gathers the conversation id, Git proxy settings, connector CLI credential sentinels, and keyed provider sentinels into one set of environment variables.

**Data flow**: It receives a conversation id, a probe id, and optionally the member the probe is acting as. It reads the current workspace id, then asks helper functions to create Git settings, grant-based CLI variables, and provider credential variables. It returns one combined dictionary of environment variable names and values for the sandbox.

**Call relations**: This is the public doorway for the file. When a probe needs to open a sandbox, it calls this method; the method then calls `_git_config_env`, `_git_credential_config`, `_grant_cli_env`, and `_keyed_provider_env` to build each part of the environment before handing the finished result back to the sandbox opener.

*Call graph*: calls 4 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env); 1 external calls (ws_current).


##### `_git_config_env`  (lines 77–84)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into the special environment-variable format that Git understands. This lets the sandbox configure Git without writing a Git config file inside the sandbox.

**Data flow**: It receives a tuple of Git key-value settings. It creates `GIT_CONFIG_COUNT` to tell Git how many settings there are, then creates numbered `GIT_CONFIG_KEY_*` and `GIT_CONFIG_VALUE_*` entries for each setting. It returns those entries as a dictionary.

**Call relations**: `ProbeEnv.exports` calls this after collecting the basic Git proxy setting and any credential-related Git settings. The result becomes part of the final sandbox environment that Git will read when it runs.

*Call graph*: called by 1 (exports).


##### `_git_credential_config`  (lines 87–122)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Creates Git authentication header settings for credential slots that are actually available in the workspace. These settings contain sentinels, not real secrets, so Git can authenticate through the proxy without the sandbox seeing a password.

**Data flow**: It receives a credential store, the declared credential slots, and the workspace id. For each slot that is meant for Git basic authentication, it checks whether a credential is set, resolves the correct host, and builds a Git `extraheader` setting using the slot's sentinel. If there is no store, no stored credential, or no usable host, it returns or skips that entry; unexpected resolution errors are warned about. It returns a tuple of Git settings.

**Call relations**: `ProbeEnv.exports` calls this while building the Git portion of the sandbox environment. This helper relies on `slot_is_set` to avoid exporting unusable placeholders, `credential_host` to find the right host, and `warn` to make broken credential declarations visible.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 125–167)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Exports environment variables for provider credentials such as API keys, using sentinels instead of real secret values. It can also export the resolved provider host so client code inside the sandbox talks to the right region or endpoint.

**Data flow**: It receives a credential store, declared credential slots, and the workspace id. For each slot with an environment-variable target or host-variable target, it checks that the credential is set, resolves the host, and then adds the declared environment variable with the sentinel and, when needed, a host environment variable with the resolved host. Missing credentials produce no variables; failed lookups or unavailable hosts are warned about. It returns a dictionary of environment variables.

**Call relations**: `ProbeEnv.exports` calls this to add provider-specific variables to the sandbox environment. The helper uses `slot_is_set` and `credential_host` to export only credentials that can really be used, and uses `warn` so configuration problems are not hidden.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_grant_cli_env`  (lines 170–212)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], acting_member_id: UUID | None, run_id: UUID) -> dict[str, str]
```

**Purpose**: Builds environment variables for connector command-line tools, choosing the right granted account and placing that account's sentinel into the CLI's expected variable. This lets a CLI inside the sandbox authenticate as an approved connected account.

**Data flow**: It receives a grant store, known connector CLI definitions, an optional acting member id, and the current run id. It reads active grants, then for each provider prefers a private grant owned by the acting member and falls back to a shared workspace grant. If exactly one account is suitable, it exports the CLI environment variable with that account's sentinel. If more than one account would match, it logs the ambiguity and exports nothing for that provider, preventing accidental use of the wrong account. It returns a dictionary of CLI environment variables.

**Call relations**: `ProbeEnv.exports` calls this when preparing connector access for a probe. This helper asks `GrantStore.active_grants` for the current permissions, uses `grant_sentinel` to create the placeholder value, and calls `log` when it refuses to choose between multiple possible accounts.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (exports); 2 external calls (grant_sentinel, log).


### `core/src/ufo/sandbox/preview.py`

`config` · `config load`

This file is a small shared reference point for the preview feature. The preview service is an internal service that can render shared files or document reads for a sandbox. Instead of letting the sandbox reach arbitrary public internet addresses, the system gives this service a fixed internal hostname: `preview.ufo.internal`. The proxy recognizes that name and forwards the request to the real preview service address configured for the deploy.

The file also defines the HTTP header name used for preview authorization and a sentinel token, which is a harmless placeholder. A sentinel is like a coat-check ticket: the sandbox carries the ticket, and the proxy swaps it for the real secret token only at the correct internal destination. That means the real token does not enter the sandbox, reducing the damage if sandboxed code behaves badly.

Finally, `parse_preview_service` reads the configured preview service location. The setting must look like `host:port`. If the setting is missing, there is no preview service. If it is present but malformed, the function raises an error rather than silently turning the feature off, because a bad internal deploy address is considered an operator mistake that should be noticed immediately.

#### Function details

##### `parse_preview_service`  (lines 16–25)

```
def parse_preview_service(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function turns the preview service setting into a usable host and port pair. It is used when the deploy may or may not have a preview service configured, and it deliberately fails clearly if the configured address is not shaped like `host:port`.

**Data flow**: It receives either a text value such as `preview-service:443` or `None`. If the input is `None`, it returns `None`, meaning no preview service is configured. Otherwise, it splits the text at the last colon, checks that there is a host part before the colon, converts the part after the colon into a number, and returns the result as `(host, port)`. If the colon or host is missing, or if the port cannot be read as a number, the caller gets an error instead of a guessed value.

**Call relations**: This function sits at the boundary between deploy configuration and the rest of the preview routing system. When some startup or configuration code reads the preview service setting, it can call this function to turn that raw setting into the exact address the proxy should relay preview traffic to. The constants in the same file provide the matching internal hostname and authorization placeholder used by the sandbox and proxy side of that flow.


### File and ingress boundaries
Path containment and ingress URL helpers keep sandbox file access and hosted-service links scoped to authorized conversations.

### `core/src/ufo/sandbox/containment.py`

`domain_logic` · `cross-cutting file access`

This file exists to stop a common and dangerous trick: using paths like `../secret` or symbolic links (shortcuts that point somewhere else) to escape a sandbox and read or overwrite host files. Checking that a filename “looks safe” is not enough, because the filesystem may follow links or change between the check and the actual read or write.

The module therefore turns a requested path into a carefully checked `ContainedFile`. It first rejects unusable relative names, then resolves the parent directory and confirms it is inside the root. After that it walks down each directory one step at a time using file descriptors, which are operating-system handles to already-open directories. This is like holding the actual folder in your hand instead of trusting a signpost that could be swapped. Finally, it checks the target file itself without following a final symbolic link.

It also provides safer helpers for directories, deletes, glob patterns, leaf filenames, and roots configured by an operator. Some helpers only do the first, “lexical” check for cases where this process cannot inspect the real filesystem yet. The file deliberately uses only Python’s standard library because the same code may be copied into sandboxed programs.

#### Function details

##### `contained_root`  (lines 88–101)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Checks that a sandbox root exists, is a real directory, and is not itself a symbolic link. This is used when the root might be reachable or replaceable by untrusted code.

**Data flow**: It receives a root path → looks at that exact path without following a final link → rejects it if it is missing or not a directory → returns the resolved, canonical directory path that later checks can trust.

**Call relations**: The main file, directory, and remove flows call this first. It gives `contained_file`, `contained_dir`, and `contained_remove` a safe starting point before they examine any user-provided path below it.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 104–121)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Checks a root directory that came from trusted operator configuration, allowing the root itself to be a symbolic link. This supports normal deployment layouts, such as a configured storage path pointing to a mounted disk.

**Data flow**: It receives a root path and the setting name it came from → follows the path normally → rejects it if it does not exist or is not a directory → returns the canonical resolved directory and includes the setting name in error messages.

**Call relations**: Unlike `contained_root`, this is not shown as called inside this file’s guarded path flows. It is a public setup helper for code that loads trusted configuration before later using the resulting root.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 139–150)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Looks at the target file itself without following a symbolic link. It is used to confirm that an existing target is a regular file, not a directory, device, or link.

**Data flow**: It reads the pinned parent directory and target name stored in the `ContainedFile` → asks the operating system for information about that name without following links → returns file metadata, returns `None` if the name is absent, or raises an error if the target is not a normal file.

**Call relations**: `contained_regular` uses this after `contained_file` has already pinned the parent directory. Together, they prove both the route to the file and the final file name are safe.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 152–167)

```
def mode(self, default: int) -> int
```

**Purpose**: Chooses permission bits for a replacement write. It keeps the old permissions for an existing regular file, uses a default when there is no file, and refuses directories.

**Data flow**: It receives a default permission mode → checks the current target name through the pinned parent directory → returns existing file permissions if the target is a regular file, returns the default if missing or held by a non-regular non-directory item, and raises an error for directories.

**Call relations**: This method is available to callers preparing to overwrite a contained file. It complements `replace_bytes`, which performs the actual safe replacement.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 169–179)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained target for streaming binary reads. This is useful for large files because callers can read chunks instead of loading the whole file into memory.

**Data flow**: It uses the `ContainedFile`’s pinned parent directory and target name → asks `_open_regular` for a safe file descriptor to a real regular file → wraps that descriptor as a Python binary file object → returns the readable stream, closing the descriptor if wrapping fails.

**Call relations**: `read_bytes` calls this for simple limited reads. Internally it relies on `_open_regular` to do the strict no-link, regular-file check before handing a stream to the caller.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 181–184)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a caller-specified number of bytes from a contained file. It is the simple helper for callers that do not need to stream manually.

**Data flow**: It receives a byte limit → opens the file safely through `open_bytes` → reads at most that many bytes → closes the stream and returns the bytes.

**Call relations**: `read_text` builds on this when the caller wants text instead of raw bytes. It delegates the safety-sensitive opening step to `open_bytes`.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 186–187)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads a contained file as UTF-8 text, replacing invalid characters instead of failing. This gives callers a safe, bounded text view of a file.

**Data flow**: It receives a byte limit → asks `read_bytes` for that much data → decodes the bytes as UTF-8 with replacement for bad sequences → returns a string.

**Call relations**: This is a convenience layer over `read_bytes`. The containment and file-opening safety has already been handled by the lower-level read path.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 189–190)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the contained file’s permission bits without following a symbolic link. It limits the mode to ordinary file permission bits.

**Data flow**: It receives a permission mode → masks it down to standard read, write, and execute bits → applies it to the target name relative to the pinned parent directory → changes the file permissions in place.

**Call relations**: This method is used by callers that already obtained a `ContainedFile` from the guarded entry point and now need to adjust permissions safely.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 192–196)

```
def unlink(self) -> None
```

**Purpose**: Deletes the contained file name if it exists. Missing files are treated as already gone.

**Data flow**: It uses the pinned parent directory and target name → asks the operating system to remove that name → leaves the filesystem changed if the file existed, or unchanged if it was already absent.

**Call relations**: This is a small operation on an already validated `ContainedFile`. Broader path removal, including directories, is handled separately by `contained_remove`.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 198–200)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Moves another contained file into this file’s name. Because both sides are addressed through pinned parent directories, the rename is not redirected by path swaps.

**Data flow**: It receives a source `ContainedFile` → renames the source name from its pinned parent directory onto this target name in this target’s pinned parent directory → after the call, this target name refers to the former source file.

**Call relations**: This method is for callers that have already validated both files through the containment system. It hands the final move to the operating system’s atomic replace operation.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 202–203)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Writes text into the contained target by using the same safe replacement path as binary writes. It is a convenience method for string content.

**Data flow**: It receives text and a permission mode → encodes the text as bytes → passes the bytes and mode to `replace_bytes` → the target is replaced with the encoded content.

**Call relations**: This method is a thin wrapper over `replace_bytes`. All staging, permission setting, and safe rename behavior happens there.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 205–229)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely replaces the target file with new bytes. It writes to a temporary sibling file first, then renames it into place so readers see either the old file or the complete new file, not a half-written one.

**Data flow**: It receives bytes and a permission mode → creates a unique staged file in the same pinned parent directory without following links and without reusing an existing name → writes the data and sets permissions → atomically renames the staged file onto the target → cleans up any leftover staged file if something goes wrong.

**Call relations**: `replace_text` calls this after encoding text. Callers use it after `contained_file` has pinned the parent directory, so the write lands in the checked location.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 231–247)

```
def _open_regular(self) -> int
```

**Purpose**: Opens the target as a real regular file and refuses symbolic links or special files. This is the safety-critical opening step behind reads.

**Data flow**: It uses the contained target name and pinned parent directory → opens the file without following links → checks the opened object itself to confirm it is a regular file → returns the raw file descriptor, or raises a clear containment error if the file is missing or unsafe.

**Call relations**: `open_bytes` calls this before turning the descriptor into a Python file object. It keeps the low-level regular-file proof in one place.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 251–285)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main read/write entry point for one file under a sandbox root. It performs the full containment check and yields a `ContainedFile` whose parent directory is pinned open.

**Data flow**: It receives a requested path, a root, and an optional flag to create missing parent directories → verifies the root, roots the path, rejects unusable target names, resolves and checks the parent is inside the root, opens the root, walks each parent component safely, optionally creating directories as it goes → yields a `ContainedFile` for safe reading, writing, chmod, unlink, or replacement → closes the pinned parent descriptor when the context ends.

**Call relations**: `contained_regular` uses this when it needs to prove an existing file is safe. Internally this function brings together `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend` to build the protected file handle.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_regular); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 288–314)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Checks that a directory path stays under a root and is reached without following unsafe links. It returns the canonical directory path for listing or walking.

**Data flow**: It receives a directory path, a root, and an optional create flag → verifies the root, roots and resolves the target directory, confirms it remains inside the root, opens the root, walks each directory component safely, optionally creating missing components → closes the descriptors and returns the resolved directory path.

**Call relations**: `contained_glob` calls this to choose the safe starting directory for a glob search. It uses the same root-opening and component-descent helpers as `contained_file`, but returns a path instead of a pinned file object.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_glob); 3 external calls (__init__, close, mkdir).


##### `contained_remove`  (lines 317–344)

```
def contained_remove(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> None
```

**Purpose**: Removes a file or directory tree under a root without following symbolic links on the way there. It is the safe delete operation for untrusted path names.

**Data flow**: It receives a path and root → verifies and roots the path → rejects empty, current-directory, or parent-directory targets → checks the parent is inside the root → walks to the parent through pinned directory descriptors → if the target is missing, it does nothing; if it is a directory, it uses a symlink-safe recursive remove when the platform supports it; otherwise it unlinks the file name.

**Call relations**: This function uses `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`, just like the read/write entry point. It then chooses between safe tree deletion and single-name unlinking.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 8 external calls (__init__, __init__, __init__, close, stat, unlink, rmtree, S_ISDIR).


##### `contained_regular`  (lines 347–356)

```
def contained_regular(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> Path
```

**Purpose**: Returns the canonical path of an existing regular file inside a root. It is meant for APIs that require a filename path instead of an already-open file handle.

**Data flow**: It receives a path and root → enters `contained_file` to run the full containment checks and pin the parent directory → calls `lstat` on the target without following links → raises an error if the file is missing → returns the checked canonical file path.

**Call relations**: This is a convenience bridge for subprocesses or libraries that cannot read from a file descriptor. It relies on `contained_file` for the hard containment work.

*Call graph*: calls 1 internal fn (contained_file); 1 external calls (__init__).


##### `contained_pattern`  (lines 359–376)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Checks and rewrites a glob pattern so it cannot make a file search leave the root. A glob pattern is a search expression such as `*.txt` or `logs/**/*.json`.

**Data flow**: It receives a pattern and root → rejects any pattern containing `..` → if the pattern is relative, returns it unchanged → if absolute, confirms it points inside the root and rewrites it as root-relative → rejects a pattern that would match the root directory itself.

**Call relations**: `contained_glob` calls this after deciding where the search should start. This keeps pattern safety rules in one shared place instead of repeating them in each enumerator.

*Call graph*: called by 1 (contained_glob); 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_glob`  (lines 379–391)

```
def contained_glob(pattern: str, path: str | os.PathLike[str] | None, root: Path) -> tuple[Path, str]
```

**Purpose**: Prepares a safe directory and safe pattern for file enumeration. It prevents absolute patterns from silently starting at the whole filesystem.

**Data flow**: It receives a pattern, an optional starting path, and a root → decides whether the search should start at the root or at the caller’s chosen path → passes the start directory through `contained_dir` → passes the pattern through `contained_pattern` → returns the safe start directory and rewritten pattern.

**Call relations**: This function coordinates the directory and pattern guards. Enumeration code can call it once, then run the actual glob using the returned safe pair.

*Call graph*: calls 2 internal fn (contained_dir, contained_pattern); 1 external calls (PurePosixPath).


##### `contained_relative`  (lines 394–419)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs a lexical, text-only containment check for a path under a root. This is for cases where this process cannot inspect the real filesystem yet, such as a future write inside a container.

**Data flow**: It receives a path string and root string → treats relative paths as being under the root → simplifies `.` and `..` parts as text → rejects paths that climb above the root or name the root itself → returns the resolved absolute-looking path string.

**Call relations**: This helper does not call the full filesystem descent and is not called by the main file operations here. It supplies the first containment tier to callers that will later hand the path to a real guarded write.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 422–430)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Extracts one usable filename from a raw name that may include directories or Windows-style backslashes. It is useful for names supplied by outside systems, such as uploaded attachments.

**Data flow**: It receives a raw name and a fallback name → turns backslashes into slashes, takes only the final filename component, and checks whether it is empty, `.`, or `..` → returns the safe leaf name or the fallback.

**Call relations**: This helper is independent of the filesystem guards. Callers still need to join the returned leaf under a root and write through `contained_file` or another full guard.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 433–444)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Filters an already-enumerated path to see whether it is a regular file inside the root and not reached through a symbolic link. It helps decide what to list, not what to open.

**Data flow**: It receives a path and root → checks the path itself without following a final link → resolves it strictly → returns `False` if anything is missing, non-regular, linked, or outside the root; otherwise returns `True`.

**Call relations**: This function uses `_inside` for the final root check. A later read of any accepted path should still go through `contained_file`, because listing and opening have different safety needs.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 447–453)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Interprets a possibly relative path against the sandbox root, not against the process’s current working directory. This prevents different code from asking safety questions about one path and then using another.

**Data flow**: It receives a path and canonical root → converts the path to a `Path` object → returns it unchanged if already absolute, or joins it under the root if relative.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this near the start of their flows. It gives all three entry points the same meaning for relative input.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 1 external calls (Path).


##### `_inside`  (lines 456–457)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Answers the simple question: is this path the root itself or somewhere below it? It is the shared containment test after paths have been resolved.

**Data flow**: It receives a path and root → compares whether the path equals the root or has the root among its parents → returns `True` or `False`.

**Call relations**: `contained_file`, `contained_dir`, `contained_remove`, and `is_contained_regular` use this as their final inside-root check. It is small, but central to keeping the policy consistent.

*Call graph*: called by 4 (contained_dir, contained_file, contained_remove, is_contained_regular).


##### `_open_root`  (lines 460–464)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the root directory as a directory handle without following a symbolic link. This starts the pinned-directory walk used by the stronger guards.

**Data flow**: It receives a root path → asks the operating system to open it with directory-only and no-follow flags → returns the directory file descriptor, or raises a containment error if it cannot be opened safely.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this before walking path components. `_descend` then continues the walk from that opened root.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 2 external calls (__init__, open).


##### `_descend`  (lines 467–480)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory component deeper during a safe path walk. It refuses links and non-directories, then closes the directory handle it just left.

**Data flow**: It receives the current directory descriptor, the next path part, and the full target path for error messages → opens the child component as a directory without following links → closes the old descriptor → returns the child descriptor; if the component is missing, linked, or not a directory, it raises a containment-specific error.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this repeatedly after `_open_root`. Together they turn a string path into a chain of real opened directories that cannot be redirected by later path swaps.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 5 external calls (__init__, __init__, __init__, close, open).


### `core/src/ufo/sandbox/ingress_host.py`

`domain_logic` · `request handling and sandbox URL creation`

Browsers separate cookies, local storage, and security permissions by “origin,” which is basically the scheme, host, and port of a site. This file gives every sandboxed site its own host label, so one hosted site cannot accidentally share browser state with another. Think of it like giving each temporary workshop its own locked mailbox address.

The label encodes two pieces of information: the conversation identifier and the sandbox port. It also includes a short HMAC signature, which is a tamper-evident stamp made with the deploy’s shared secret. The signature is not the main permission check; users still need a valid token or session cookie elsewhere. Its job is to stop meaningless or guessed hostnames from causing the system to look up conversations or dial sandboxes.

The file also keeps labels canonical. Base32 encoding can leave unused bits, which means several spellings could decode to the same bytes. Browsers would treat those spellings as different sites, splitting cookies and storage. To avoid that, parsing decodes the label, re-encodes it, and only accepts the one official lowercase spelling.

It also includes helpers for shipped app pages: extracting a stable app slug and creating a synthetic UUID anchor when no normal conversation row exists.

#### Function details

##### `serve_port`  (lines 57–63)

```
def serve_port(conversation_id: UUID) -> int
```

**Purpose**: This chooses the stable sandbox port for a conversation’s hosted site. It lets different conversations consistently land on different ports without storing a separate port value in the database.

**Data flow**: It takes a conversation UUID as input. It turns that UUID into a large number, folds it into the allowed application port range, and adds the configured starting port. The result is one integer port that will be the same every time for the same conversation.

**Call relations**: Other parts of the sandbox hosting flow use this when they need to know where a conversation’s site should be served. It does not call out to other project code; it is the shared rule everyone uses so producers and readers agree on the same port.


##### `shipped_app_slug`  (lines 66–74)

```
def shipped_app_slug(provisioned_by: str | None) -> str | None
```

**Purpose**: This extracts the stable page slug from the name of a provisioned shipped app. The slug is used because the visible member-facing name may change, while the extension identity should stay stable.

**Data flow**: It receives either a provision name string or nothing. If there is no provision name, or if the name does not exactly match the expected form like app_example123, it returns nothing. If it matches, it returns just the slug part after app_.

**Call relations**: This is used when shipped app pages need stable origins or bundle paths. It stands before later origin-building logic by turning a provision identity into the small slug those later steps can use.


##### `shipped_anchor`  (lines 77–82)

```
def shipped_anchor(workspace_id: UUID, slug: str) -> UUID
```

**Purpose**: This creates a stable synthetic UUID for a shipped app page inside a workspace. It gives the page a durable identity even though there is no normal conversation row behind it.

**Data flow**: It takes a workspace UUID and an app slug. It combines them with a fixed label and feeds that text into UUID version 5, which means the same inputs always produce the same UUID. The output is a UUID that can act like an origin anchor for that workspace’s copy of the app.

**Call relations**: When a shipped app page needs its own cookies and browser storage, this function supplies the identity to build that origin around. It hands the final UUID creation to uuid.uuid5, which is the standard library tool for deterministic UUIDs.

*Call graph*: 1 external calls (uuid5).


##### `site_label`  (lines 85–90)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: This builds the DNS label for one conversation and one sandbox port. That label is what can appear in a hostname to route a browser request to the correct sandboxed site.

**Data flow**: It receives a conversation UUID and a port number. First it rejects ports outside the valid network port range. Then it packs the UUID bytes and port bytes together, adds a short signature for those bytes, and encodes everything as lowercase base32 text. The output is the DNS-safe label string.

**Call relations**: This is used when the system needs to mint or display the hostname for a hosted site. It relies on _signature to make the tamper-evident stamp and _encode to turn the binary address into DNS-friendly text.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 93–105)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: This reads a DNS label back into the conversation UUID and port it claims to address. It refuses labels that are malformed, not in the one official spelling, or not signed by this deployment’s secret.

**Data flow**: It takes a label string from a hostname. It base32-decodes it, re-encodes it to make sure the spelling is canonical, splits the bytes into address and signature, checks the signature using a timing-safe comparison, and then returns the UUID and port. If any step fails, it raises SiteLabelError instead of returning a possibly unsafe result.

**Call relations**: This runs on the incoming side, before the system trusts a hostname as pointing at a real sandbox. It calls _encode to enforce the single allowed spelling, _signature to recompute the expected stamp, base64.b32decode to unpack the text, hmac.compare_digest to compare signatures safely, and uuid.UUID to rebuild the conversation identifier.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 108–109)

```
def _encode(raw: bytes) -> str
```

**Purpose**: This converts raw bytes into the lowercase base32 text used in site labels. It is a small shared helper so label creation and label checking use exactly the same spelling rule.

**Data flow**: It receives bytes. It base32-encodes them, removes padding characters that are not wanted in DNS labels, lowercases the result, and returns that text.

**Call relations**: site_label uses this to produce new labels. parse_site_label uses it after decoding to confirm the browser-provided label is the one canonical spelling, rather than an alternate spelling that would create a separate browser origin.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 112–114)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: This makes the short tamper-evident signature attached to each site label. The signature proves that the conversation-and-port bytes were minted with this deployment’s ingress secret.

**Data flow**: It receives the packed address bytes. It reads the ingress secret, combines that secret with a fixed label kind and the address bytes using HMAC with SHA-256, and returns only the first few bytes of the digest as the label signature.

**Call relations**: site_label calls this when creating a label, and parse_site_label calls it again when checking one. It depends on ingress_secret for the shared secret and hmac.new for the standard signing calculation.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/sandbox/ingress_url.py`

`domain_logic` · `request handling`

A sandbox may run a web app on a private port, but a browser needs a public URL to reach it. This file turns the project’s public ingress address into a short-lived, signed viewing URL for one workspace, one conversation, and one port. Think of it like printing a temporary visitor badge: the badge names where the visitor may go, when it expires, and sometimes who invited them.

The main function first checks whether public ingress is configured at all. If not, it returns nothing, because there is no safe public address to hand out. If public ingress exists, it parses that base address, creates optional “shipped” information when a shipped app slug and digest are present, and mints an ingress token. That token carries claims, meaning trusted statements, such as the workspace id, conversation id, port, expiry time, and optional framing information.

The hostname is also shaped carefully. The conversation and port become a site label, which is placed in front of the public ingress host. The final URL includes a fixed ingress viewing path, the token, and the requested entry path with unsafe characters escaped.

The helper function `_framer_claim` is a safety check for embedded pages. It only creates a framing claim when the page doing the framing appears to come from the same ingress base and has a valid sandbox site label.

#### Function details

##### `mint_ingress_view_url`  (lines 17–50)

```
def mint_ingress_view_url(public_url: str | None, workspace_id: UUID, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest:
```

**Purpose**: This function creates the expiring browser URL used to view a sandbox port. A caller uses it when it needs to show or return a safe public link for a sandbox web service.

**Data flow**: It receives the public ingress URL, workspace and conversation ids, the sandbox port, the desired path inside the app, and optional details about framing or shipped content. If there is no public URL, it returns `None`. Otherwise it parses the base URL, builds the token contents, asks the token system to sign them, creates the sandbox-specific subdomain label, safely escapes the entry path, and returns the complete URL string.

**Call relations**: This is the file’s main outward-facing function. During link creation it asks `_framer_claim` whether the optional `framed_from` URL should be recorded as a trusted parent sandbox. It also relies on the ingress token code to mint the signed token and on the ingress host code to build the hostname label used in the final browser link.

*Call graph*: calls 1 internal fn (_framer_claim); 7 external calls (__init__, __init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `_framer_claim`  (lines 53–73)

```
def _framer_claim(base: SplitResult, framed_from: str | None) -> FramerClaim | None
```

**Purpose**: This helper decides whether a supplied framing page should be trusted enough to include in the ingress token. It exists to avoid blindly accepting any outside web page as the parent of an embedded sandbox view.

**Data flow**: It receives the parsed public ingress base URL and an optional `framed_from` URL. It parses the framing URL, compares its scheme, port, and host against the public ingress base, and rejects it if it is missing, malformed, or from the wrong place. If the host is a valid sandbox subdomain, it extracts the conversation id and port from that label and returns a `FramerClaim`; otherwise it returns `None`.

**Call relations**: It is called by `mint_ingress_view_url` while building the token claims. When it succeeds, its result is folded into the token so later ingress checks know which sandbox page framed this view. When it fails, URL creation continues without a framer claim.

*Call graph*: called by 1 (mint_ingress_view_url); 3 external calls (__init__, parse_site_label, urlsplit).


### Unified sandbox contract
The shared session API defines the safe command and file boundary used by every sandbox carrier.

### `core/src/ufo/sandbox/session.py`

`domain_logic` · `cross-cutting during sandbox opening, command execution, file operations, skill loading, and cancellation`

This file is the “front desk” for sandbox use. Tools and engine code do not talk directly to Docker, E2B, or any other sandbox provider. They talk to the types and methods here, and each provider implements the same carrier interface behind the scenes.

The main job is safety and consistency. User-visible files are kept under `/workspace`. UFO’s own runtime files and skills live under `$UFO_HOME`, and helper functions carefully check paths so a tool cannot wander into unrelated files. Think of it like a hotel key card: it should open the guest’s room and approved service areas, not the staff office.

The file also builds proxy environment variables so network traffic from a sandbox goes through UFO’s egress proxy, where requests can be attributed to a turn or probe. Signed run and probe tokens make sure a sandbox cannot simply invent authority.

There are two ways to hold a sandbox. `SandboxSession` wraps one that already exists. `_LateSandbox` delays creation until the first real operation, so reads of metadata or cancellation checks do not accidentally start a container. The shared `Sandbox` methods then provide command execution, file reads and writes, skill loading, runtime-file access, port dialing, and cleanup helpers through the carrier underneath.

#### Function details

##### `egress_proxy_env`  (lines 353–392)

```
def egress_proxy_env(proxy: 'ProxyEndpoint', run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside a remote sandbox send outbound network traffic through UFO’s proxy. This matters because the proxy can meter traffic, attach the right user or turn authority, and replace placeholder model API keys safely.

**Data flow**: It receives a proxy endpoint and a signed run token. It checks that the proxy has a public HTTPS URL, turns that URL into authenticated proxy settings, adds loopback exceptions and certificate settings, and returns a dictionary of environment variables for the sandbox command.

**Call relations**: Sandbox-opening code uses this kind of environment when preparing commands for off-cluster sandboxes. It relies on URL parsing to validate the proxy address before any command is allowed to use it.

*Call graph*: 1 external calls (urlsplit).


##### `_basic_username`  (lines 398–404)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username from a Basic authentication header. UFO stores signed sandbox tokens in that username field because standard proxy URLs naturally carry user information there.

**Data flow**: It receives a `Proxy-Authorization` header string, checks that it uses Basic auth, decodes the base64 text, splits off the password, and returns only the username. Bad or missing Basic auth becomes a `ValueError`.

**Call relations**: Both token decoders call this first when the egress proxy receives a request. After this function pulls out the token-looking username, the specific codec verifies what kind of token it is.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 423–427)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Creates a run-token signer and verifier from the deployment secret stored in the environment. This makes all run tokens tied to the current UFO deployment.

**Data flow**: It reads the secret environment variable, refuses to continue if it is missing, encodes the secret as bytes, and returns a `RunTokenCodec` using that secret.

**Call relations**: The server startup path calls this so later sandbox openings can mint run tokens and the proxy can verify them.

*Call graph*: called by 1 (run).


##### `RunTokenCodec.encode`  (lines 429–432)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Creates a signed token for one sandbox run, tying network access to a workspace, turn, and optional acting member. The signature prevents a sandbox from forging a different identity.

**Data flow**: It receives a `RunToken`, formats its IDs into a small text payload, signs that payload with the codec secret, and returns the signed string.

**Call relations**: Sandbox-opening code calls this before creating a sandbox session. The resulting token is later embedded in proxy environment variables and checked by the proxy.

*Call graph*: called by 1 (_open_sandbox); 1 external calls (sign_token).


##### `RunTokenCodec.from_proxy_auth`  (lines 434–446)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Verifies a run token presented through proxy authentication and turns it back into trusted IDs. It rejects tokens from the wrong domain, malformed tokens, and forged tokens.

**Data flow**: It receives an auth header, extracts the Basic username, verifies the signed token, splits the payload into workspace, turn, and member fields, converts them to UUIDs, and returns a `RunToken`. Any verification or parsing failure becomes a clear invalid-token error.

**Call relations**: The egress proxy uses this when a sandbox command tries to reach the network during a turn. It depends on `_basic_username` to get the signed username before checking the signature.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `ProbeTokenCodec.encode`  (lines 481–487)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Creates a signed token for an off-turn probe command. A probe has its own expiry time because it is not naturally tied to a currently running turn row.

**Data flow**: It receives a `ProbeToken`, formats workspace, conversation, probe, member, and expiry information into a payload, signs it, and returns the token string.

**Call relations**: Probe-running code can use this to give a sandbox just enough network authority for that probe. The matching decoder later proves to the proxy that the probe token is genuine.

*Call graph*: 1 external calls (sign_token).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 489–505)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Verifies a probe token from proxy authentication and recovers the probe’s trusted identity and expiry. It keeps probe tokens separate from run tokens even though both travel as proxy usernames.

**Data flow**: It receives an auth header, extracts the Basic username, verifies the signature, checks the payload kind, converts IDs and expiry into typed values, and returns a `ProbeToken`. Bad signatures, wrong token kind, or malformed values are rejected.

**Call relations**: The egress proxy uses this for off-turn sandbox executions. It shares `_basic_username` with the run-token decoder but validates a different token domain.

*Call graph*: calls 1 internal fn (_basic_username); 3 external calls (__init__, verify_token, UUID).


##### `sandbox_handle_id`  (lines 593–598)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the provider-specific sandbox ID out of a stored handle, but only if it belongs to the expected backend. This prevents one backend from trying to resume another backend’s container ID.

**Data flow**: It receives a backend name and a stored handle string. If the string starts with that backend prefix, it returns the remaining ID; otherwise it returns `None`.

**Call relations**: Carrier implementations use this when deciding whether a saved conversation handle is theirs to resume.


##### `sandbox_handle_backend`  (lines 601–604)

```
def sandbox_handle_backend(value: str) -> str
```

**Purpose**: Reads the backend name from a stored sandbox handle. This helps route an old conversation back to the kind of carrier that created its sandbox.

**Data flow**: It receives a stored handle string, splits it at the first separator, and returns the backend part before the separator.

**Call relations**: Higher-level sandbox routing can use this when more than one carrier type may be live during a deployment change.


##### `Carrier.create`  (lines 642–642)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the carrier operation for creating or attaching to a writable sandbox for a conversation. Each backend implements this in its own way.

**Data flow**: It receives a `SandboxSpec` describing the conversation, image, workspace, proxy, environment, and optional resume information. The implementation returns a `SandboxHandle` that future operations use.

**Call relations**: Sandbox-opening orchestration calls this through the `Carrier` interface. `SandboxSession` then stores the returned handle and uses it for commands, files, and dialing.


##### `Carrier.attach`  (lines 644–650)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines a read-only style attachment check for an already-existing sandbox. It should not create a fresh sandbox just because someone wants to inspect files.

**Data flow**: It receives a `SandboxSpec`, usually with a resume ID. The implementation returns a handle if that sandbox is reachable, or `None` if it is gone or unavailable.

**Call relations**: Late or read paths can call this to avoid accidentally provisioning a new sandbox. Backends decide what “reachable” means for their provider.


##### `Carrier.exec`  (lines 652–654)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines how to run a command inside the sandbox. This is the core operation behind shell, Python, file-tool, and skill helper execution.

**Data flow**: It receives a sandbox handle, an argument list, and a timeout. The implementation runs the command and returns stdout, stderr, exit code, and timeout information in an `ExecResult`.

**Call relations**: Many `Sandbox` methods eventually call this through the carrier. `ufo_fs_file_op` also calls it directly to run the sandbox’s file command.

*Call graph*: called by 1 (ufo_fs_file_op).


##### `Carrier.write`  (lines 656–668)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how to safely write bytes into the sandbox filesystem. The contract requires the path to be confined and parent directories to be created as needed.

**Data flow**: It receives a handle, a target path, and raw bytes. The implementation writes those bytes into the sandbox, replacing unsafe or non-regular targets without following malicious links.

**Call relations**: Workspace writes, runtime-file writes, staged skill payloads, and system skill archives all go through this carrier operation.


##### `Carrier.read`  (lines 670–681)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how to stream a file out of the sandbox without loading the whole file into memory at once. This is important for large files.

**Data flow**: It receives a handle and a path. The implementation yields chunks of bytes from that sandbox file, or raises an appropriate error if the file cannot be read.

**Call relations**: Sandbox file readers and runtime-file readers call this after they have resolved and checked the requested path.


##### `Carrier.dial`  (lines 683–692)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how code outside the sandbox can reach a service listening on a sandbox port. For example, a browser or development server may start inside the sandbox and need an external URL.

**Data flow**: It receives a handle and a port number. The implementation returns a `DialTarget` with host, TLS choice, and required headers, or raises `SandboxUnreachable` if no route exists.

**Call relations**: The `Sandbox.dial` method delegates to this, and browser-related extensions use that higher-level method.


##### `Carrier.file_op`  (lines 694–704)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Defines a bounded in-sandbox file operation, such as read, edit, glob, grep, or change listing. It lets the sandbox do the heavy file work instead of copying whole files to the host.

**Data flow**: It receives a handle, an operation name, and JSON-like parameters. The implementation runs the operation inside the sandbox and returns a parsed JSON-like result dictionary.

**Call relations**: The `Sandbox.run_ufo_fs` method prepares safe paths and then calls this. Carriers with UFO’s file command can implement it using `ufo_fs_file_op`.


##### `CommandStopping.stop_commands`  (lines 724–724)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines how a carrier can stop commands that may continue running after their launch call is cancelled. Not every backend needs this.

**Data flow**: It receives a sandbox handle, including the turn identity. The implementation stops only commands associated with that turn.

**Call relations**: Sandbox cancellation paths call this only when the carrier implements `CommandStopping`, so simple carriers are not forced to support it.


##### `SkillLoading.load_skills`  (lines 731–733)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Defines a carrier-native way to load skills into the sandbox runtime. Some remote runtimes can do this more directly than running UFO’s staged Python loader.

**Data flow**: It receives a handle and a skill payload describing system and user skills. The implementation installs or resolves those skills and returns an execution-style result.

**Call relations**: `Sandbox.load_skills` uses this path when available. Otherwise it falls back to staging a payload file and running helper code inside the sandbox.


##### `SkillExecuting.exec_skill`  (lines 740–742)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines how a carrier runs privileged skill-maintenance programs inside a container. These programs update UFO’s runtime skill tree.

**Data flow**: It receives a handle, command arguments, and a timeout. The implementation runs the command with the needed privileges and returns an `ExecResult`.

**Call relations**: The fallback skill loader and system skill synchronizer call `_exec_skill`, which requires the carrier to implement this protocol.


##### `SystemSkillSeeding.seed_system_skills`  (lines 749–749)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Defines how a carrier whose filesystem is created locally can seed system skills before runtime use. This is for carriers that build or own the runtime filesystem in the current process.

**Data flow**: It receives the system skill archive as bytes. The implementation stores or unpacks it into the carrier’s runtime area.

**Call relations**: Carrier setup code can use this protocol when it has direct filesystem control, instead of copying skills through ordinary sandbox operations.


##### `ufo_fs_file_op`  (lines 752–790)

```
async def ufo_fs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs UFO’s in-sandbox file command and turns its JSON output into a Python dictionary. It is a shared implementation carriers can reuse for file tools.

**Data flow**: It receives a carrier, handle, operation name, and parameters. It chooses a timeout, runs `ufo fs` or `sbxfs` inside the sandbox, parses stdout as JSON, turns reported tool errors into `ValueError`, and returns the result dictionary.

**Call relations**: Carrier implementations can delegate their `file_op` method here. It uses `Carrier.exec` to do the actual sandbox command run.

*Call graph*: calls 1 internal fn (exec); 3 external calls (dumps, loads, PurePosixPath).


##### `host_argv`  (lines 798–810)

```
def host_argv(argv: tuple[str, ...], root: str) -> tuple[str, ...]
```

**Purpose**: Rewrites `/workspace` paths in command arguments for carriers where the workspace is actually a host directory. This lets the same command text work in both container and host-path setups.

**Data flow**: It receives command arguments and the real host root. It replaces standalone `/workspace` path segments with that root while leaving unrelated text alone, and returns the rewritten argument tuple.

**Call relations**: Host-based carriers use this before executing commands locally. The careful segment matching avoids corrupting URLs, names like `/workspace-old`, or other unrelated strings.


##### `workspace_path`  (lines 813–821)

```
def workspace_path(path: str) -> str
```

**Purpose**: Normalizes a user-supplied path and proves it stays inside `/workspace`. This is one of the main guards that stops file tools from escaping into private or system files.

**Data flow**: It receives a path that may be absolute or relative. It anchors relative paths under `/workspace`, resolves `.` and `..` pieces, rejects escapes, and returns a clean absolute workspace path.

**Call relations**: Workspace reads, writes, existence checks, rooted path handling, and file operations call this before handing paths to a carrier.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 5 (_read_scoped_file, file_exists, run_ufo_fs, write_file, rooted_path); 1 external calls (PurePosixPath).


##### `runtime_relative`  (lines 824–834)

```
def runtime_relative(path: str) -> PurePosixPath
```

**Purpose**: Validates a path meant to be relative to UFO’s private runtime directory. It keeps internal runtime paths from using tricks like `..` to escape.

**Data flow**: It receives a relative path string, checks it with the containment guard under a fake `/runtime` root, ensures the normalized result is exactly the same relative spelling, and returns a `PurePosixPath`.

**Call relations**: Runtime path builders call this before creating actual runtime file paths or display paths.

*Call graph*: called by 2 (_runtime_display_path, _runtime_path); 2 external calls (PurePosixPath, contained_relative).


##### `sandbox_runtime_root`  (lines 837–839)

```
def sandbox_runtime_root(conversation_id: UUID) -> str
```

**Purpose**: Builds the private runtime root path for one conversation inside the sandbox. This is where UFO keeps run-owned files separate from the member workspace.

**Data flow**: It receives a conversation UUID and returns a string under `$UFO_HOME/runs/` using that UUID’s hex form.

**Call relations**: `_runtime_root` calls this when a sandbox handle does not already specify a runtime root.

*Call graph*: called by 1 (_runtime_root).


##### `shell_path`  (lines 842–847)

```
def shell_path(path: str) -> str
```

**Purpose**: Quotes a sandbox path so it can safely appear in a shell command, while preserving `$UFO_HOME` expansion when intended. This avoids accidental shell interpretation of special characters.

**Data flow**: It receives a path string. If it starts with `$UFO_HOME/`, it quotes only the remainder while leaving the environment variable expandable; otherwise it shell-quotes the whole path.

**Call relations**: Code that needs to display or embed sandbox paths in shell snippets can use this helper.

*Call graph*: 1 external calls (quote).


##### `_runtime_root`  (lines 850–851)

```
def _runtime_root(handle: SandboxHandle) -> str
```

**Purpose**: Finds the runtime root for a sandbox handle. It uses an explicit root when present, otherwise it computes the standard per-conversation root.

**Data flow**: It receives a `SandboxHandle`, checks its `runtime_root` field, and returns either that value or the default path from `sandbox_runtime_root`.

**Call relations**: Runtime path, runtime display, file operation, skill loading, and scoped file reading helpers all use this as their starting point.

*Call graph*: calls 1 internal fn (sandbox_runtime_root); called by 7 (_read_scoped_file, _run_staged_skill_load, _sync_system_skills, run_ufo_fs, write_runtime_path, _runtime_display_path, _runtime_path).


##### `_runtime_path`  (lines 854–855)

```
def _runtime_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds a real internal runtime file path from a sandbox handle and a safe relative name. It keeps UFO’s private runtime files under the correct root.

**Data flow**: It receives a handle and a relative path. It gets the runtime root, validates the relative path, joins them, and returns the full path string.

**Call relations**: Runtime read/write methods, skill staging, system skill sync, tool output setup, and existence checks call this before touching runtime files.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 8 (_read_runtime_file, _run_staged_skill_load, _sync_system_skills, ensure_tool_output_dir, runtime_file_exists, runtime_path, write_runtime_file, write_runtime_path); 1 external calls (PurePosixPath).


##### `_runtime_display_path`  (lines 858–862)

```
def _runtime_display_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds the user-facing `$UFO_HOME/runs/...` spelling for a runtime path. This gives agents a portable path they can refer to without hard-coding the sandbox’s absolute home directory.

**Data flow**: It receives a handle and relative path, derives the run directory name, validates the relative path, joins it under `$UFO_HOME/runs/<id>`, and returns the display string.

**Call relations**: `Sandbox.runtime_display_path` calls this when code needs to show or pass a reusable runtime path to the agent.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 1 (runtime_display_path); 1 external calls (PurePosixPath).


##### `rooted_path`  (lines 865–868)

```
def rooted_path(path: str, root: str) -> str
```

**Purpose**: Normalizes a path under a chosen root while reusing the workspace escape guard. It is used for approved non-workspace roots like the current runtime or skill tree.

**Data flow**: It receives a path and the root it is supposed to be under. It maps that root-shaped path through `/workspace` validation, then restores the original root spelling and returns the safe path.

**Call relations**: Scoped file reading and `run_ufo_fs` use this when reads, globs, or greps are allowed outside `/workspace` but still must stay under a specific approved root.

*Call graph*: calls 1 internal fn (workspace_path); called by 2 (_read_scoped_file, run_ufo_fs).


##### `_resolve_parts`  (lines 871–880)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies path pieces by applying `.` and `..` rules while refusing to climb above the root. It is the small path-normalizing engine behind workspace guarding.

**Data flow**: It receives a tuple of path parts. It builds a stack, skips empty and current-directory parts, pops for safe `..`, raises on root escape, and returns the cleaned parts.

**Call relations**: `workspace_path` uses this to decide whether a supplied path remains inside `/workspace`.

*Call graph*: called by 1 (workspace_path).


##### `Sandbox.conversation_id`  (lines 892–896)

```
def conversation_id(self) -> UUID
```

**Purpose**: Defines that every sandbox object can report which conversation’s workspace it represents. The base class leaves the exact source to subclasses.

**Data flow**: No input beyond the sandbox object. A subclass returns the conversation UUID; the base method raises because it is only a contract.

**Call relations**: `SandboxSession`, `_LateSandbox`, and `_AuthorizedSandbox` each provide the concrete value.


##### `Sandbox.created`  (lines 899–901)

```
def created(self) -> bool
```

**Purpose**: Defines that a sandbox object can say whether an actual sandbox session already exists. This lets callers check without forcing creation.

**Data flow**: No input beyond the sandbox object. A subclass returns a boolean; the base method raises because it is only a contract.

**Call relations**: Concrete sandbox wrappers implement this differently: an existing session is always created, while a late sandbox may still be unopened.


##### `Sandbox.authorize`  (lines 903–911)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'Sandbox'
```

**Purpose**: Defines how to produce a view of the same sandbox with a different run token and environment authority. This lets the same container serve different turns or members safely.

**Data flow**: It receives a new run token, environment variable names to remove, and variables to add. A subclass returns a sandbox wrapper with those authority changes applied.

**Call relations**: `SandboxSession` rewrites an existing handle directly, while `_LateSandbox` returns `_AuthorizedSandbox` so authorization is applied after the late session opens.


##### `Sandbox._bound`  (lines 913–914)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Defines the internal step that turns any sandbox wrapper into a concrete `SandboxSession`. It is the common doorway used by nearly every sandbox operation.

**Data flow**: No external input beyond the sandbox object. A subclass either returns itself, opens a session, or opens and then authorizes one.

**Call relations**: Command, file, runtime, skill, and dial methods all call `_bound` before reaching the carrier.

*Call graph*: called by 19 (_read_file, _read_runtime_file, _read_scoped_file, bash, bash_task, dial, ensure_tool_output_dir, file_exists, load_skills, python (+9 more)).


##### `Sandbox.runtime_path`  (lines 916–918)

```
async def runtime_path(self, relative: str) -> str
```

**Purpose**: Returns the real internal path for a file under this conversation’s runtime root. Callers use it when UFO, not the member workspace, owns the file.

**Data flow**: It receives a relative runtime name, binds to a session, validates and joins the name under the runtime root, and returns the full path.

**Call relations**: It calls `_bound` and `_runtime_path`, so it works the same whether the sandbox already exists or must be opened first.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.runtime_display_path`  (lines 920–922)

```
async def runtime_display_path(self, relative: str) -> str
```

**Purpose**: Returns a `$UFO_HOME`-style path for a runtime file, suitable for showing to or reusing inside the sandbox. It hides backend-specific absolute details.

**Data flow**: It receives a relative runtime name, binds to a session, builds the display path under `$UFO_HOME/runs/<id>`, and returns it.

**Call relations**: It calls `_bound` and `_runtime_display_path` to share the same validation as real runtime paths.

*Call graph*: calls 2 internal fn (_bound, _runtime_display_path).


##### `Sandbox.write_runtime_file`  (lines 924–927)

```
async def write_runtime_file(self, relative: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a UFO-owned runtime file. This is for internal files outside the member’s `/workspace`.

**Data flow**: It receives a relative runtime path and content bytes. It binds to a session, converts the relative path into a safe full runtime path, and asks the carrier to write the bytes.

**Call relations**: Runtime offloads, skill staging, or internal bookkeeping can use this instead of workspace writing.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.write_runtime_path`  (lines 929–937)

```
async def write_runtime_path(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to an already-resolved path, but only if that path is inside this conversation’s runtime root. This protects against accidentally writing to arbitrary sandbox locations.

**Data flow**: It receives a full path and bytes. It binds to a session, checks the path is below the runtime root and not the root itself, converts it back to a safe relative name, and writes through the carrier.

**Call relations**: This is a stricter variant for callers that already hold a runtime path. It uses `_runtime_root` and `_runtime_path` for the final safe write.

*Call graph*: calls 3 internal fn (_bound, _runtime_path, _runtime_root); 1 external calls (PurePosixPath).


##### `Sandbox.runtime_file_exists`  (lines 939–946)

```
async def runtime_file_exists(self, relative: str) -> bool
```

**Purpose**: Checks whether a regular file exists in UFO’s runtime area. It uses the sandbox itself to test the file.

**Data flow**: It receives a relative runtime path, binds to a session, builds the full runtime path, runs `test -f` inside the sandbox, and returns true if the command exits successfully.

**Call relations**: It delegates execution to the carrier through `_bound`, keeping backend details hidden.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.read_runtime_file`  (lines 948–950)

```
def read_runtime_file(self, relative: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts streaming a UFO-owned runtime file. It returns an async stream rather than reading everything at once.

**Data flow**: It receives a relative runtime path and returns an async iterator produced by `_read_runtime_file`. Bytes come later as the caller iterates.

**Call relations**: This public wrapper keeps the streaming implementation in `_read_runtime_file`.

*Call graph*: calls 1 internal fn (_read_runtime_file).


##### `Sandbox._read_runtime_file`  (lines 952–955)

```
async def _read_runtime_file(self, relative: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams chunks from one runtime file after resolving it safely under the runtime root.

**Data flow**: It receives a relative runtime path, binds to a session, converts the name to a full runtime path, asks the carrier to read it, and yields each byte chunk onward.

**Call relations**: `Sandbox.read_runtime_file` calls this. The carrier supplies the actual backend-specific streaming.

*Call graph*: calls 2 internal fn (_bound, _runtime_path); called by 1 (read_runtime_file).


##### `Sandbox.bash`  (lines 957–963)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Bash command inside the sandbox through UFO’s command supervisor. This is the simple high-level way to execute shell work in the conversation environment.

**Data flow**: It receives a command string and optional timeout. It binds to a session, builds a `ufo run -- bash -lc ...` command, runs it through the carrier, and returns the execution result.

**Call relations**: Browser sandbox extension code calls this when starting or diagnosing services. It relies on `_bound` so late sandboxes are opened only when the command actually runs.

*Call graph*: calls 1 internal fn (_bound); called by 2 (lease, _bring_up_failure).


##### `Sandbox.bash_task`  (lines 965–975)

```
async def bash_task(self, command: str, base: str, *, detach: bool, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs or reattaches a journaled Bash task through UFO’s sandbox supervisor. The task name lets long-running or detached work be tracked.

**Data flow**: It receives a command, task base name, detach flag, and optional timeout. It binds to a session, builds the proper `ufo run --task` command, and returns the carrier’s execution result.

**Call relations**: Higher-level task execution can use this when command output or lifecycle needs to be journaled by the sandbox supervisor.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.sh`  (lines 977–986)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX shell script inside the sandbox with each argument passed separately. Passing arguments separately avoids unsafe string interpolation.

**Data flow**: It receives a script, positional arguments, and optional timeout. It binds to a session, runs `sh -c` with the arguments as distinct command arguments, and returns an `ExecResult`.

**Call relations**: This is the general shell helper for code that wants portable `sh` rather than Bash.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.python`  (lines 988–1003)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with UFO’s containment guard injected. It uses isolated Python mode so workspace files cannot hijack standard imports.

**Data flow**: It receives Python source text, arguments, and an optional timeout. It binds to a session, prepends the containment bootstrap, runs `python3 -I -c ...`, and returns the execution result.

**Call relations**: Helpers that need safe path handling inside the sandbox can use this rather than trusting whatever containment code might already exist in the sandbox image.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.stop_commands`  (lines 1005–1011)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands launched for this sandbox turn when a real user cancel happens. It does nothing for carriers whose commands die automatically with their execution call.

**Data flow**: It binds to a session. If the carrier supports `CommandStopping`, it asks the carrier to stop commands for the handle’s turn; otherwise it returns without action.

**Call relations**: Cancellation handling calls this after it knows the cancel is deliberate. The protocol check keeps backend-specific stop behavior optional.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.write_file`  (lines 1013–1015)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the member workspace after proving the target path stays under `/workspace`. This is the safe public workspace write.

**Data flow**: It receives a path and content bytes. It binds to a session, normalizes the path with `workspace_path`, and passes the safe path and bytes to the carrier.

**Call relations**: Inbound file delivery and tool-driven writes use this instead of calling the carrier directly.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.load_skills`  (lines 1017–1059)

```
async def load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Loads system and user skills into the sandbox’s runtime skill directory. It supports both carrier-native loading and a fallback staged loader.

**Data flow**: It receives a skill payload, binds to a session, either calls carrier-native loading or writes a staged payload and runs the skill loader, validates the JSON result, and returns a map of skill names to installed roots. If expected system skills are missing and an archive is available, it syncs system skills once and retries.

**Call relations**: The skill runtime calls this when installing or loading skills. It coordinates `_run_staged_skill_load`, `_sync_system_skills`, and carrier protocols.

*Call graph*: calls 3 internal fn (_bound, _run_staged_skill_load, _sync_system_skills); called by 2 (install_skill, load_skills); 1 external calls (loads).


##### `Sandbox._run_staged_skill_load`  (lines 1061–1083)

```
async def _run_staged_skill_load(self, bound: 'SandboxSession', payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Implements the fallback skill-load path by writing the skill payload into the sandbox and running UFO’s loader program there.

**Data flow**: It receives a bound session and payload. It serializes the payload to JSON, writes it to a unique runtime staging file, computes its hash, runs the skill loader through `_exec_skill`, and returns the execution result.

**Call relations**: `Sandbox.load_skills` calls this when the carrier has no native skill loader, and again after system skills are refreshed if needed.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 3 external calls (sha256, dumps, uuid4).


##### `Sandbox._sync_system_skills`  (lines 1085–1102)

```
async def _sync_system_skills(self, bound: 'SandboxSession') -> None
```

**Purpose**: Copies the server’s system skill archive into the sandbox and installs or refreshes the system skill tree. This repairs missing or outdated baked skills.

**Data flow**: It receives a bound session, writes the archive to a unique runtime staging path, hashes it, runs the system skill sync program through `_exec_skill`, and raises an error if the command fails.

**Call relations**: `Sandbox.load_skills` calls this only when fallback loading reports that expected system skills are missing and the session has an archive available.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 2 external calls (sha256, uuid4).


##### `Sandbox._exec_skill`  (lines 1104–1109)

```
async def _exec_skill(self, bound: 'SandboxSession', argv: tuple[str, ...]) -> ExecResult
```

**Purpose**: Runs a privileged skill-maintenance command through a carrier that supports skill execution. It centralizes the protocol check and timeout.

**Data flow**: It receives a bound session and command arguments. It verifies the carrier implements `SkillExecuting`, runs `exec_skill` with the default timeout, and returns the result.

**Call relations**: Both staged skill loading and system skill syncing call this. If the carrier cannot run privileged skill programs, those fallback paths fail clearly.

*Call graph*: called by 2 (_run_staged_skill_load, _sync_system_skills).


##### `Sandbox.ensure_tool_output_dir`  (lines 1111–1135)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure UFO’s private tool-output directory exists in the runtime area. If a file or broken link is squatting on that fixed name, it removes it and recreates the directory.

**Data flow**: It binds to a session, builds the runtime tool-output path, runs a small shell script to check or repair it, raises on failure, and returns whether something had to be reclaimed.

**Call relations**: Tool-output offload code can call this before writing private results. It uses a fixed internal path, so the cleanup cannot target arbitrary member data.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.file_exists`  (lines 1137–1143)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the member workspace. It first confines the path to `/workspace`.

**Data flow**: It receives a path, normalizes it with `workspace_path`, binds to a session, runs `test -f` inside the sandbox, and returns true on success.

**Call relations**: Callers use this lightweight check before deciding whether to read or overwrite workspace files.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.run_ufo_fs`  (lines 1145–1186)

```
async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured file operation inside the sandbox after applying the correct path rules. Workspace paths are broadly allowed, while runtime and skill paths are read-only through selected operations.

**Data flow**: It receives an operation name and arguments. It copies the arguments, checks and rewrites any path, sets the operation’s root, calls the carrier’s `file_op`, and returns the parsed result.

**Call relations**: File tools use this high-level method. It delegates the actual in-sandbox work to `Carrier.file_op`, often implemented by `ufo_fs_file_op`.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); 1 external calls (PurePosixPath).


##### `Sandbox.read_file`  (lines 1188–1190)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts streaming a file from either the workspace or the current runtime area. It exposes a simple public read method while keeping path checks inside the implementation.

**Data flow**: It receives a path and returns an async iterator from `_read_scoped_file`. The bytes are produced as the caller consumes the iterator.

**Call relations**: External callers use this for file download or inspection. `_read_scoped_file` performs the actual binding, path selection, and carrier read.

*Call graph*: calls 1 internal fn (_read_scoped_file).


##### `Sandbox._read_scoped_file`  (lines 1192–1205)

```
async def _read_scoped_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file after deciding whether the requested path is an allowed runtime path or a workspace path. It prevents reads from arbitrary sandbox locations.

**Data flow**: It receives a path, binds to a session, computes the runtime root and display spelling, rewrites approved runtime paths, otherwise confines the path to `/workspace`, and yields chunks from the carrier.

**Call relations**: `Sandbox.read_file` calls this. It uses `rooted_path` and `workspace_path` to enforce the allowed roots before calling `Carrier.read`.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); called by 1 (read_file); 1 external calls (PurePosixPath).


##### `Sandbox._read_file`  (lines 1207–1210)

```
async def _read_file(self, target: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file from an already-chosen target path. This is a lower-level helper that assumes the caller has already done the right scoping.

**Data flow**: It receives a target path, binds to a session, asks the carrier to read that path, and yields each chunk.

**Call relations**: It shares the same carrier streaming mechanism as the scoped readers, but without performing path normalization itself.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.dial`  (lines 1212–1216)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Gets an externally reachable address for a service running on a port inside the sandbox. This lets outside code connect to things the sandbox started.

**Data flow**: It receives a port number, binds to a session, asks the carrier for a `DialTarget`, and returns that target.

**Call relations**: Browser-related extensions call this after starting services with `bash`. The carrier decides the provider-specific routing details.

*Call graph*: calls 1 internal fn (_bound); called by 1 (lease).


##### `SandboxSession.conversation_id`  (lines 1230–1231)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID from an already-bound sandbox handle. For a concrete session, this information is always immediately available.

**Data flow**: It reads `handle.conversation_id` and returns it.

**Call relations**: This implements the base `Sandbox.conversation_id` contract for sessions that already exist.


##### `SandboxSession.created`  (lines 1234–1235)

```
def created(self) -> bool
```

**Purpose**: Reports that a `SandboxSession` is already created. A session always wraps a real handle.

**Data flow**: It takes no outside data and returns `True`.

**Call relations**: This implements the base `Sandbox.created` contract for the eager, already-bound sandbox form.


##### `SandboxSession._bound`  (lines 1237–1238)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Returns the current session as the concrete bound sandbox. No opening or lookup is needed.

**Data flow**: It receives only `self` and returns `self`.

**Call relations**: All inherited `Sandbox` operations can call `_bound` without caring whether they are running on a session, late sandbox, or authorized wrapper.


##### `SandboxSession.authorize`  (lines 1240–1268)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new view of the same sandbox session with a different run token and adjusted environment variables. This lets one shared container run with the right authority for a specific turn or member.

**Data flow**: It receives a new run token, names of environment variables to clear, and variables to add. It checks the old handle has a run token, rewrites proxy variables from the old token to the new one, removes cleared variables, overlays new variables, and returns a new `SandboxSession` with an updated handle.

**Call relations**: _AuthorizedSandbox uses this after a late sandbox opens. Direct callers can also re-authorize an existing session without recreating the underlying container.

*Call graph*: 2 external calls (__init__, __init__).


##### `_LateSandbox.__init__`  (lines 1272–1284)

```
def __init__(self, conversation_id: UUID, turn_id: UUID, open: Callable[[], Awaitable[SandboxSession]], existing: Callable[[], Awaitable[SandboxSession | None]]) -> None
```

**Purpose**: Builds a sandbox wrapper that delays opening the real sandbox until it is first needed. This avoids starting containers for operations that may never run commands or touch files.

**Data flow**: It receives conversation and turn IDs plus two async callbacks: one to open a session and one to check for an existing session. It stores them, creates an async lock, and starts with no session cached.

**Call relations**: Higher-level sandbox setup creates `_LateSandbox` for turns. Later calls to `_bound` use the stored open callback exactly once.

*Call graph*: 1 external calls (Lock).


##### `_LateSandbox.conversation_id`  (lines 1287–1288)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID known before the sandbox is opened. This lets callers identify the workspace without creating it.

**Data flow**: It reads the stored conversation UUID and returns it.

**Call relations**: This implements the base `Sandbox.conversation_id` contract for lazy sandboxes.


##### `_LateSandbox.created`  (lines 1291–1292)

```
def created(self) -> bool
```

**Purpose**: Reports whether the lazy sandbox has actually been opened yet. It does not trigger opening.

**Data flow**: It checks whether the cached session is present and returns that boolean.

**Call relations**: Callers can use this to avoid side effects. Once `_bound` opens the session, this becomes true.


##### `_LateSandbox.authorize`  (lines 1294–1300)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Creates an authorized wrapper around a lazy sandbox. The actual token and environment changes are applied only after the real session exists.

**Data flow**: It receives a run token, cleared environment names, and new environment values. It returns an `_AuthorizedSandbox` containing those settings and a reference to the late sandbox.

**Call relations**: This is used when authority is known before the lazy sandbox has opened. `_AuthorizedSandbox._bound` later applies the settings to the opened session.

*Call graph*: 1 external calls (__init__).


##### `_LateSandbox._bound`  (lines 1302–1307)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens the real sandbox session on first use and caches it. A lock prevents two concurrent first operations from creating two sandboxes.

**Data flow**: It checks whether a session is already cached. If not, it enters an async lock, checks again, awaits the open callback, stores the session, and returns it.

**Call relations**: Every inherited sandbox operation eventually reaches this when used on a late sandbox. After the first open, later operations reuse the same session.


##### `_LateSandbox.stop_commands`  (lines 1309–1312)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this turn without necessarily creating a new sandbox. This is important during cancellation, where starting a sandbox just to stop it would be wrong.

**Data flow**: It uses the cached session if one exists; otherwise it asks the existing-session callback. If a session is found and the carrier supports stopping, it replaces the handle’s turn ID with this late sandbox’s turn and asks the carrier to stop commands.

**Call relations**: Cancellation code can call this safely on a lazy sandbox. It differs from the base stop path by avoiding `_bound` when no sandbox has been opened.

*Call graph*: 1 external calls (replace).


##### `_AuthorizedSandbox.conversation_id`  (lines 1323–1324)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation ID of the underlying late sandbox. Authorization does not change which conversation the sandbox belongs to.

**Data flow**: It reads `late.conversation_id` and returns it.

**Call relations**: This implements the base `Sandbox.conversation_id` contract for authorized lazy views.


##### `_AuthorizedSandbox.created`  (lines 1327–1328)

```
def created(self) -> bool
```

**Purpose**: Reports whether the underlying late sandbox has been opened. The authorized wrapper itself does not create anything.

**Data flow**: It reads `late.created` and returns that boolean.

**Call relations**: This keeps authorization as a view over the late sandbox, not a separate lifecycle owner.


##### `_AuthorizedSandbox.authorize`  (lines 1330–1336)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Re-authorizes the same underlying late sandbox with a new token and environment. The latest authorization request replaces this wrapper’s settings.

**Data flow**: It receives a run token, cleared environment names, and environment values, then delegates to the underlying late sandbox’s `authorize` method and returns the new wrapper.

**Call relations**: This prevents stacking authorization wrappers unnecessarily. The late sandbox remains the single object responsible for opening the session.


##### `_AuthorizedSandbox._bound`  (lines 1338–1339)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens or retrieves the underlying lazy session, then applies this wrapper’s authorization to it. This is where delayed authorization becomes a concrete session handle.

**Data flow**: It awaits `late._bound()` to get a `SandboxSession`, calls that session’s `authorize` with the stored token and environment changes, and returns the authorized session.

**Call relations**: Inherited `Sandbox` operations call this when used through an authorized lazy sandbox. It connects `_LateSandbox` creation timing with `SandboxSession.authorize` authority rewriting.
