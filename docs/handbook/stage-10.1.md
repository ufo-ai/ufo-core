# Sandbox carriers, workspaces, terminals, and command tasks  `stage-10.1`

This stage is shared behind-the-scenes support for any conversation that needs a place to work. It gives each conversation a private workspace, like a temporary project folder, and a way to run commands there. The conversation workspace code creates, reopens, lists, reads, writes, trims, and deletes files without losing the right sandbox. The session and protocol code provide one common “socket” for command running and file operations, so tools do not care whether the workspace is local, Docker, E2B, or a user terminal.

The local carrier runs directly on the host for development. The Docker carrier starts and manages per-conversation containers, with network traffic routed through a proxy. The E2B carrier does the same on a cloud sandbox service, including ports and leases. The terminal carrier sends work to a user’s already-open terminal, while the Redis terminal bridge keeps terminal sessions alive across different server pods.

Selection code chooses the active carrier while preserving old ones for saved sandboxes. Environment code supplies safe placeholder credentials. Task code keeps long-running command logs durable. The REPL extension preserves Python and JavaScript sessions across calls.

## Files in this stage

### Conversation workspaces
Entry points for creating, reopening, inspecting, modifying, and trimming a conversation's private workspace.

### `core/src/ufo/harness/sandbox/conversation.py`

`orchestration` · `request handling and background workspace operations`

A conversation can have files that tools, attachments, jobs, and humans all need to reach. This file makes sure all of those paths lead to the same place. Think of it like the key desk for a storage locker: if the locker already exists, it gives you the same key; if it does not, it creates one and records the key so the next worker does not open a different locker by mistake.

The main class, `ConversationSandbox`, decides where the workspace lives. It may be in the normal sandbox carrier, in a resumed backend from an older stored handle, or in a member’s connected terminal directory. It also records the durable sandbox handle in the conversation row in the database. That record is important because later processes may run on different machines and still need to find the same workspace.

The file is careful about side effects. Read-only operations use `existing`, which never creates a sandbox just because someone browsed files. Write operations use `open`, which may create one. When two callers race to create a sandbox at the same time, `_claim` uses a database compare-and-swap, meaning “only write this handle if the old value is still what I saw.” The loser adopts the winner’s sandbox, so data is not stranded in an unreferenced workspace.

It also protects filesystem paths so a conversation directory cannot escape the configured workspace root through unsafe links.

#### Function details

##### `ConversationSandbox._route`  (lines 105–116)

```
def _route(self, stored: str | None) -> tuple[Carrier, str, bool]
```

**Purpose**: Chooses which sandbox provider should be used for a stored sandbox handle. This matters when old conversations were created by a different backend and must keep opening where their files already live.

**Data flow**: It receives a stored handle, if there is one. It reads the backend name encoded inside that handle and checks whether a resume backend is configured for it. It returns the carrier to use, that backend’s name, and whether it runs off-cluster; otherwise it falls back to this deployment’s normal carrier settings.

**Call relations**: When `open` needs to create or resume a sandbox, `_opened` asks `_route` where to go. When a read-only path uses `existing`, it also asks `_route` so it can attach to the right already-existing sandbox without creating a new one.

*Call graph*: called by 2 (_opened, existing); 1 external calls (sandbox_handle_backend).


##### `ConversationSandbox.open`  (lines 118–170)

```
async def open(self, conversation_id: UUID, turn_id: UUID | None, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Opens the conversation’s sandbox for active use, creating it if needed, and makes sure the database records the durable handle for later. This is the safe entry point for anything that may write to the workspace.

**Data flow**: It starts by reading the conversation’s current sandbox handle and sandbox size from the database. It asks `_opened` to create or attach to a sandbox, builds the handle string that should be stored, and then uses `_claim` to persist it only if nobody else won the race first. It returns a `SandboxSession`, which is the usable connection to the sandbox.

**Call relations**: Workspace writes call this before copying data in, and runtime queue code calls it before running a turn. It coordinates `_binding`, `_opened`, and `_claim`; if another opener wins at the same time, it loops and adopts the winner’s sandbox instead of leaving two live workspaces.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 3 (write, write_runtime, _open_sandbox); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 172–234)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Tries to reach a conversation’s already-known sandbox without creating a new one. This is used for read-style actions, where merely looking should not create a workspace.

**Data flow**: It reads the stored handle. If there is none, it returns `None`. If the handle points to a client terminal, it tries to attach through the terminal transport. Otherwise it routes to the correct backend, checks that any needed local directory already exists, and tries to attach. It returns a `SandboxSession` when reachable, or `None` when not.

**Call relations**: File listing, reading, and pruning all call `existing` because they should operate only on a workspace that already exists. It uses `_stored`, `_route`, and safe directory lookup, then hands back the session those higher-level operations use.

*Call graph*: calls 2 internal fn (_route, _stored); called by 4 (entries, prune, prune_runtime, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 236–246)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a conversation that has no sandbox yet to a member’s connected terminal directory. This lets the first later sandbox open use the user’s live terminal workspace instead of the normal carrier.

**Data flow**: It receives a conversation id and terminal current directory. It builds a stored `client:` handle for that directory, checks whether the conversation already has a handle, and if not tries to write the terminal handle into the database. It returns `true` only if this call made the binding.

**Call relations**: Admission code can call this while a terminal connection is live. It relies on `_stored` to avoid replacing an existing binding and `_claim` to safely win or lose against another opener.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 248–259)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Copies a user-visible file into the conversation’s `/workspace`. It is used when something outside the sandbox, such as an attachment landing process, needs to place bytes where the agent can read them.

**Data flow**: It receives a relative path and file bytes. It first rejects content over the configured size limit, then opens the sandbox off-turn, writes the file through the session, and returns the `/workspace/...` path the agent should use.

**Call relations**: This is a write path, so it calls `open`, not `existing`, because it is allowed to create the workspace. After `open` returns a session, the actual file write is delegated to that session.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.write_runtime`  (lines 261–273)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output into a named runtime area of the sandbox. This keeps system-generated files separate from normal member-visible workspace files.

**Data flow**: It receives a conversation id, category, relative path, and bytes. It enforces the same maximum size limit, opens the sandbox off-turn, writes under `category/rel` in the runtime area, and returns a display path for that runtime file.

**Call relations**: Like `write`, this uses `open` because it may need to create the sandbox before writing. The session then performs the runtime-specific write and path formatting.

*Call graph*: calls 1 internal fn (open).


##### `ConversationSandbox.prune`  (lines 275–286)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files under a workspace subdirectory, keeping only the newest requested number. This protects unattended append-style writers from growing a directory forever.

**Data flow**: It receives a conversation id, a relative directory prefix, and a keep count. It attaches only to an existing sandbox; if there is none, it does nothing. It runs a small Python pruning program inside the sandbox and raises an error if that program reports failure.

**Call relations**: This is called for cleanup work on visible workspace files. It uses `existing` so cleanup does not create a new workspace, then hands the deletion rules to the sandbox’s Python runner.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.prune_runtime`  (lines 288–300)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files in an internal runtime directory, keeping only the newest requested number. It is the runtime-area version of `prune`.

**Data flow**: It receives a conversation id, runtime category, relative prefix, and keep count. It attaches to an existing sandbox if one exists, translates the category and prefix into runtime paths, runs the pruning program there, and raises an error if pruning fails.

**Call relations**: Runtime cleanup calls this when system-owned output needs trimming. It follows the same pattern as `prune`: use `existing`, compute safe target paths through the session, then run the shared pruning script.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox.entries`  (lines 302–341)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists the member-visible files in a conversation’s workspace. It powers a file browser-style view without creating a workspace for conversations that do not have one.

**Data flow**: It attaches to an existing sandbox. If none exists, it returns an empty tuple. Otherwise it asks the sandbox to run a filesystem glob, checks that the result is a file list, warns if the list was truncated, converts each returned path into a workspace-relative path, and returns sorted `WorkspaceFile` records with size and modified time.

**Call relations**: File browsing calls this. It depends on `existing` to avoid side effects and `_workspace_rel` to normalize paths returned by different carriers into the same relative form.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 343–347)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns a path reported by a sandbox file walk into a path relative to the workspace root. This hides whether the carrier reported `/workspace/...` or a host-side directory path.

**Data flow**: It receives the sandbox handle and a reported path. It checks whether the path starts with the container workspace root or the host workspace root, strips that root, and returns the relative path. If neither root matches, it raises an error because the walker reported something outside the workspace.

**Call relations**: `entries` calls this for every listed file. Its job is to make file listing output consistent and to catch surprising paths before they are shown to callers.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 349–357)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Reads one file from the conversation’s workspace in chunks, or reports that it is not available. It is a safe read path that does not create a sandbox.

**Data flow**: It receives a conversation id and relative file path. It attaches to an existing sandbox, returns `None` if there is none, checks whether the file exists, returns `None` if it does not, and otherwise returns an async byte stream from the session.

**Call relations**: Download or file-view code calls this. It uses `existing` so a read request cannot create a new workspace, then delegates existence checking and chunked reading to the session.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 359–418)

```
async def _opened(self, conversation_id: UUID, turn_id: UUID | None, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Performs the actual choice and opening of a sandbox for `open`. It decides whether the conversation should use a connected terminal, a resumed backend, or the normal configured carrier.

**Data flow**: It receives the conversation id, optional turn id, stored handle, run token, environment variables, and requested sandbox size. It first checks whether the conversation is or should be bound to a terminal. If so, it creates a terminal-backed sandbox. Otherwise it routes to a backend, prepares or chooses the workspace host path, possibly changes ownership for the sandbox user, and asks the carrier to create or resume the sandbox. It returns the backend name, carrier, and opened handle.

**Call relations**: `open` calls `_opened` during each claim attempt. `_opened` uses `_route` for non-terminal backends and constructs the sandbox specification that carrier implementations need in order to start or resume the workspace.

*Call graph*: calls 1 internal fn (_route); called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 420–435)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates and verifies the host directory used as a conversation’s workspace for local carrier storage. Its main job is safety: the directory must stay inside the configured workspace root.

**Data flow**: It receives a conversation id. It creates the workspace root if needed, resolves the configured root safely, creates the conversation directory if missing, and returns the verified path. It refuses unsafe path situations such as links that would lead outside the allowed root.

**Call relations**: `_opened` uses this when a write-capable open needs a local workspace directory. This function is deliberately not used by read-only access, because it may create directories.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 437–448)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds the host directory for a conversation only if it already exists. It is the read-safe partner to `_provisioned_dir`.

**Data flow**: It receives a conversation id. It resolves the configured workspace root and checks for the conversation directory inside it. If the directory is absent, it returns `None`; if the path is unsafe, it raises; otherwise it returns the verified path.

**Call relations**: `existing` uses this for local, non-off-cluster sandboxes. That keeps read operations from accidentally creating directories while still enforcing the same containment safety rules.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 450–452)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Fetches just the stored sandbox handle for a conversation. It is a small convenience wrapper around the fuller database binding read.

**Data flow**: It receives a conversation id, calls `_binding`, discards the sandbox size, and returns the handle string or `None`.

**Call relations**: `existing`, `claim_terminal`, and `_claim` use this when they only need to know what handle the conversation row currently contains.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 454–475)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the database row information needed to open a sandbox: the current stored handle and the owning agent’s sandbox size. It also confirms the conversation belongs to the current workspace.

**Data flow**: It receives a conversation id. Inside a workspace database transaction, it joins the conversation and agent tables, filters by conversation id and current workspace id, and reads the sandbox handle plus sandbox size. It returns both values, or raises an error if the conversation is not in this workspace.

**Call relations**: `open` calls this at the start of sandbox creation or resume. `_stored` also calls it when other flows need only the handle.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 477–498)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Safely writes a sandbox handle into the conversation row only if the row still has the value this caller previously saw. This prevents two simultaneous creators from both believing they own the conversation’s workspace.

**Data flow**: It receives the conversation id, the previously observed handle, and the new handle to store. It updates the database only when the current stored value still matches the observed value. If the update succeeds, it returns the new handle. If it loses the race, it reads and returns the winner’s stored handle.

**Call relations**: `open` uses `_claim` to settle races between concurrent sandbox opens. `claim_terminal` uses it to bind an unbound conversation to a terminal without overwriting another binding.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### Sandbox carriers
Backend implementations that run workspace commands locally, in Docker, on E2B, or through user and Redis-backed terminal streams.

### `core/src/ufo/harness/sandbox/local.py`

`io_transport` · `cross-cutting: sandbox setup, command execution, file access, and local port access`

This file lets the system treat an ordinary directory on the host computer as if it were the sandbox’s `/workspace`. When an agent asks to run a command, read a file, write a file, or contact a local port, `LocalCarrier` translates that request into host-machine work. Think of it like putting a workbench in the garage instead of renting a locked workshop: it is easy and fast, but it is not a security boundary.

The file carefully builds a clean environment for commands instead of passing through the server’s own environment, which may contain deployment secrets. It creates a scratch home directory, adds the `ufo` helper command to `PATH` when available, disables troublesome host Git credential helpers, and routes web traffic through the sandbox proxy so metering and key substitution still work.

It also protects file paths. Tool calls may say `/workspace/file.txt`, but local subprocesses need the real host path. The code rewrites command arguments and uses containment helpers when reading or writing, so normal file operations cannot escape the intended workspace or runtime directory. It can also install and verify “skills” — packaged reusable tool files — by checking manifests and cryptographic digests before making them available.

#### Function details

##### `_provision_scratch`  (lines 79–103)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates one temporary support area for the whole running process. This area holds a fake home directory for sandboxed commands and, if available, a copy of the `ufo` client program used by file and model helper commands.

**Data flow**: It starts with no inputs beyond the current process environment. It makes a temporary directory with `home` and `bin` subfolders, tries to find the built `ufo` client, copies it into `bin`, and marks it executable. If the client is missing, it logs a warning and still returns the scratch directory so basic local execution can continue.

**Call relations**: A `LocalCarrier` receives this directory through its default field when it is constructed. Later methods use it to build `HOME`, `UFO_HOME`, and `PATH` for subprocesses.

*Call graph*: 4 external calls (Path, mkdtemp, warn, client_binary).


##### `LocalCarrier.ufo_home`  (lines 111–113)

```
def ufo_home(self) -> Path
```

**Purpose**: Gives the local sandbox its private `UFO_HOME` directory. This is where local runtime data such as installed skills is stored.

**Data flow**: It reads the carrier’s scratch directory and returns the path to `home/.ufo` inside it. It does not create files or change anything by itself.

**Call relations**: Skill loading, sandbox creation, and attachment use this property as the common place for runtime files belonging to the local carrier.


##### `LocalCarrier.seed_system_skills`  (lines 115–143)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Installs a bundle of built-in system skills into the local sandbox’s skill directory. It replaces any old top-level skill folders covered by the new or previous manifest so the installed set stays consistent.

**Data flow**: It receives a zip archive as bytes. It opens the archive, reads and validates `manifest.json`, removes old skill folders named by the old or new manifest, writes each skill file safely under `UFO_HOME/skills`, and saves the manifest as `.system-manifest.json`.

**Call relations**: This prepares the system skill store that `LocalCarrier.load_skills` later consults. It relies on `_system_manifest` to compare against the previous install and containment helpers to avoid writing outside the skills directory.

*Call graph*: calls 1 internal fn (_system_manifest); 7 external calls (BytesIO, loads, Path, contained_file, contained_relative, contained_remove, ZipFile).


##### `LocalCarrier.load_skills`  (lines 145–155)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asynchronously resolves the skills requested for a run and reports where they live on disk. It wraps the blocking file work so it does not stall the event loop.

**Data flow**: It receives a sandbox handle and a payload describing requested system and user skills. It runs `_load_skills` in a worker thread; on success it returns an `ExecResult` whose standard output is JSON containing skill roots, and on validation or file errors it returns an `ExecResult` with an error message and exit code 1.

**Call relations**: Higher-level sandbox code can treat skill loading like a command-style operation because this returns `ExecResult`. The real checking and installation are delegated to `_load_skills`.

*Call graph*: 3 external calls (__init__, to_thread, dumps).


##### `LocalCarrier._load_skills`  (lines 157–180)

```
def _load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Does the actual work of matching requested skills to installed system skills and unpacking user-provided skills. It returns the safe local paths for every skill that should be available.

**Data flow**: It reads the system skill manifest from `UFO_HOME/skills`, then reads `system` and `user` entries from the payload. For each system skill it verifies the manifest and digest through `_load_system_skill`; for each user skill it validates, verifies, and installs through `_load_user_skill`. It returns a dictionary from skill name to local root path.

**Call relations**: This is called by `LocalCarrier.load_skills` in a background thread. It coordinates the smaller skill helpers rather than doing all validation inline.

*Call graph*: calls 3 internal fn (_load_system_skill, _load_user_skill, _system_manifest).


##### `LocalCarrier._load_system_skill`  (lines 182–201)

```
def _load_system_skill(self, root: Path, manifest_skills: Mapping[object, object], name: object, digest: object) -> tuple[str, str] | None
```

**Purpose**: Checks whether a requested built-in system skill is already installed and matches the expected digest. If it does, it returns the skill’s name and directory; if not, it quietly reports that the skill is unavailable.

**Data flow**: It receives the skill root, manifest data, a requested name, and a requested digest. It verifies the name and digest are strings, confirms the manifest entry has the same digest, reads the listed files, recomputes their digest, and returns the safe directory path only if everything matches.

**Call relations**: It is called from `_load_skills` for each requested system skill. It uses `_read_skill_files` to collect file contents and `_skill_digest` to prove the files match the manifest.

*Call graph*: calls 2 internal fn (_read_skill_files, _skill_digest); called by 1 (_load_skills); 1 external calls (contained_relative).


##### `LocalCarrier._load_user_skill`  (lines 203–236)

```
def _load_user_skill(self, root: Path, system_names: tuple[object, ...], name: object, encoded: object) -> tuple[str, str]
```

**Purpose**: Validates and installs a user-supplied skill. It makes sure the skill name is safe, does not collide with a system skill, and that the supplied files match their declared digest.

**Data flow**: It receives the skill root, known system skill names, a user skill name, and encoded file data. It checks the name, decodes each base64 file body, verifies the digest over all files, removes any old copy of that user skill, writes the new files, and returns the skill name with its local directory.

**Call relations**: It is called from `_load_skills` for each user skill in the payload. It hands validation to `_validate_user_skill_name`, digest calculation to `_skill_digest`, and writing to `_install_user_skill`.

*Call graph*: calls 3 internal fn (_install_user_skill, _skill_digest, _validate_user_skill_name); called by 1 (_load_skills); 2 external calls (urlsafe_b64decode, contained_relative).


##### `LocalCarrier._system_manifest`  (lines 239–247)

```
def _system_manifest(root: Path) -> Mapping[str, object]
```

**Purpose**: Reads the saved manifest for installed system skills. If no manifest exists yet, it returns an empty skill list.

**Data flow**: It receives the skills root directory. It safely opens `.system-manifest.json` if present, parses it as JSON, confirms it is an object-like mapping, and returns it; if the file is absent, it returns `{"skills": {}}`.

**Call relations**: Both `seed_system_skills` and `_load_skills` call this so they share one view of what system skills are installed.

*Call graph*: called by 2 (_load_skills, seed_system_skills); 2 external calls (load, contained_file).


##### `LocalCarrier._read_skill_files`  (lines 250–257)

```
def _read_skill_files(root: Path, name: str, files: list[str]) -> list[tuple[str, bytes]]
```

**Purpose**: Reads the files that belong to an installed system skill so their contents can be checked. It keeps all reads inside the skills directory.

**Data flow**: It receives the skills root, the skill name, and a list of relative file paths. For each path it safely resolves `skill_name/path`, opens the file, reads its bytes, and returns a list of `(path, bytes)` pairs.

**Call relations**: It is used by `_load_system_skill` before digest verification. The returned contents are passed to `_skill_digest`.

*Call graph*: called by 1 (_load_system_skill); 2 external calls (contained_file, contained_relative).


##### `LocalCarrier._install_user_skill`  (lines 260–266)

```
def _install_user_skill(root: Path, name: str, files: list[tuple[str, bytes]]) -> None
```

**Purpose**: Writes a verified user skill into the local skill store. It replaces any previous skill folder with the same name.

**Data flow**: It receives the skills root, a skill name, and a sorted list of file paths with bytes. It removes the old destination folder, then writes each file under the skill’s directory using safe path checks and parent-directory creation.

**Call relations**: It is called only after `_load_user_skill` has validated the name, decoded files, and confirmed the digest. This keeps writing separate from trust checking.

*Call graph*: called by 1 (_load_user_skill); 3 external calls (contained_file, contained_relative, contained_remove).


##### `LocalCarrier._validate_user_skill_name`  (lines 269–272)

```
def _validate_user_skill_name(name: str, root: Path) -> None
```

**Purpose**: Rejects user skill names that could escape the skill directory or hide as dot-prefixed folders. It ensures user skills are simple top-level names.

**Data flow**: It receives a proposed name and the skills root. It resolves the name through the containment guard, converts it to a relative path, and raises an error unless it is exactly one path component and does not start with `.`.

**Call relations**: It is called early by `_load_user_skill` before any user-provided files are decoded or written.

*Call graph*: called by 1 (_load_user_skill); 2 external calls (Path, contained_relative).


##### `LocalCarrier._skill_digest`  (lines 275–280)

```
def _skill_digest(files: list[tuple[str, bytes]]) -> str
```

**Purpose**: Computes the fingerprint used to prove a skill’s files are exactly the expected set and contents. A digest is like a tamper-evident seal: changing a path or file body changes the result.

**Data flow**: It receives an ordered list of file paths and bytes. For each file, it feeds a hash of the path and a hash of the content into a larger SHA-256 hash, then returns the final value as a `sha256:...` string.

**Call relations**: _load_system_skill and `_load_user_skill` both call this to compare actual files with declared digests before a skill is accepted.

*Call graph*: called by 2 (_load_system_skill, _load_user_skill); 1 external calls (sha256).


##### `LocalCarrier.create`  (lines 282–317)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a local sandbox handle for a conversation turn. It prepares the workspace and runtime folders and builds the environment that commands will inherit.

**Data flow**: It receives a `SandboxSpec` containing the workspace path, conversation identifiers, proxy details, run token, and extra environment variables. It creates needed directories, writes the proxy certificate, builds proxy and model-key environment variables on top of `_base_env`, and returns a `SandboxHandle` describing the local sandbox.

**Call relations**: This is the main setup path before commands or file operations run. It calls `_base_env` so every created handle gets the same safe baseline environment, then adds turn-specific proxy settings.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier._base_env`  (lines 319–342)

```
def _base_env(self) -> dict[str, str]
```

**Purpose**: Builds the minimal safe environment for local sandbox commands. It avoids leaking the server’s own secrets and disables host Git behaviors that can hang or prompt for credentials.

**Data flow**: It reads only a small allowlist of host environment variables such as locale and temporary-directory settings. It then adds scratch `HOME`, `UFO_HOME`, a controlled `PATH`, and Git settings that prevent system config, credential prompts, and inherited Git config from interfering. It returns this environment dictionary.

**Call relations**: `create` uses this as the base for proxy-enabled command environments, while `attach` uses it for read-only access to an existing workspace.

*Call graph*: called by 2 (attach, create); 1 external calls (Path).


##### `LocalCarrier.attach`  (lines 344–359)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reopens an existing local workspace without creating it. This is useful when browsing or reading a past conversation where a workspace may or may not exist.

**Data flow**: It receives a `SandboxSpec` and checks whether the workspace host path is already a directory. If not, it returns `None`; if yes, it returns a `SandboxHandle` pointing at that directory with the local runtime path and base environment.

**Call relations**: This is the read-only counterpart to `create`. It still calls `_base_env` because file-tool commands may need the local `ufo` client on `PATH`.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 361–419)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs one command as a host subprocess in the workspace. It makes local execution look like sandbox execution by rewriting `/workspace` arguments to the real host directory and applying the prepared command environment.

**Data flow**: It receives a sandbox handle, command arguments, a timeout, and an optional model command label. It finds the real workspace root, rewrites arguments, starts the subprocess with that directory as its working directory, collects standard output and error, and returns an `ExecResult`. If the command times out, it kills the whole process group and returns exit code 124 with a timeout message.

**Call relations**: This is the central command-running method used by the local carrier. It depends on `_root` for the host workspace, `host_argv` for path rewriting, and `_kill_process_group` when timeout or cancellation requires cleanup.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 5 external calls (__init__, create_subprocess_exec, wait_for, quote, host_argv).


##### `LocalCarrier.write`  (lines 421–444)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the local workspace. It runs the blocking file operation in a worker thread so the async server can keep doing other work.

**Data flow**: It receives a sandbox handle, a sandbox path, and bytes to write. It sends those inputs to `_write_contained` in another thread and returns when the write is complete.

**Call relations**: Higher-level upload or file-copy code calls this when content needs to enter the sandbox. `_write_contained` performs the actual safe path resolution and atomic replacement.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 446–449)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the actual safe write into either the workspace or runtime directory. It refuses paths outside allowed sandbox roots.

**Data flow**: It receives a handle, a requested path, and bytes. It converts the requested path into a contained local name and root, opens the target safely with parent creation, and replaces the file contents while preserving or applying the intended write permissions.

**Call relations**: It is called by `write` after the async method moves the blocking work to a thread. It relies on `_contained_name` to decide which local root the path belongs to.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 451–462)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the local workspace or runtime directory in chunks. Chunking avoids loading large files into memory all at once.

**Data flow**: It receives a sandbox handle and path. It opens a safe source file through `_contained_source` in a worker thread, repeatedly reads fixed-size chunks, yields each chunk to the caller, and closes the file at the end even if the read stops early.

**Call relations**: Higher-level download or file-inspection code uses this to copy data out of the local sandbox. `_contained_source` handles the security-sensitive opening step.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 464–474)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Safely opens a readable file inside an allowed sandbox root. It converts missing contained paths into normal `FileNotFoundError` errors.

**Data flow**: It receives a handle and requested path. It resolves the path with `_contained_name`, checks the target exists without following unsafe path tricks, opens it as bytes, and returns the open file object. If the guarded path lookup fails because a path component is missing, it raises `FileNotFoundError`.

**Call relations**: It is called by `read` before streaming begins. Once it returns an open file, later path changes cannot redirect that already-open stream.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 476–481)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured file operation through the local `ufo fs` helper. This lets the local carrier reuse the same file-tool behavior used by other sandbox types.

**Data flow**: It receives a handle, an operation name, and operation parameters. It passes them to `ufo_fs_file_op`, which executes the helper against the local workspace, and returns the resulting dictionary.

**Call relations**: When callers need higher-level file actions instead of raw read or write streams, they call this method. It delegates the protocol details to the shared sandbox session helper.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `LocalCarrier.dial`  (lines 483–489)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns the address for reaching a service started by a local sandbox command. Because local commands share the host network, a sandbox port is simply the same port on `127.0.0.1`.

**Data flow**: It receives a handle and port number. It creates and returns a `DialTarget` with host `127.0.0.1:<port>` and TLS disabled.

**Call relations**: Callers use this when they want to connect to a server launched by a command. Unlike container carriers, this does not create per-conversation network isolation, so port conflicts can happen.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 492–497)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Force-stops a command and any child processes in its process group. This matters when a timed-out shell has started background work that would otherwise keep running.

**Data flow**: It receives an asyncio subprocess object. It sends `SIGKILL` to the process group identified by the child process ID, ignores the case where the process is already gone, and waits for the process to finish cleanup.

**Call relations**: `LocalCarrier.exec` calls this when a command times out or when execution is interrupted. It is the cleanup tool that prevents orphaned local subprocess trees.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 500–503)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Finds the real host directory that backs `/workspace` for a local sandbox handle. It raises an error if the handle does not have such a directory.

**Data flow**: It receives a sandbox handle. If `workspace_host_path` is missing, it raises a runtime error; otherwise it returns that path as a `Path` object.

**Call relations**: `LocalCarrier.exec` uses this before running commands, and `_contained_name` uses it when translating `/workspace/...` paths into host filesystem paths.

*Call graph*: called by 2 (exec, _contained_name); 1 external calls (Path).


##### `_contained_name`  (lines 506–512)

```
def _contained_name(handle: SandboxHandle, path: str) -> tuple[PurePosixPath, Path]
```

**Purpose**: Translates an incoming sandbox path into a safe relative name plus the local root it belongs to. It only accepts paths under the workspace root or the runtime root.

**Data flow**: It receives a handle and a path string. If the path starts under `/workspace`, it returns the path relative to the host workspace root; if it starts under the handle’s runtime root, it returns the path relative to that runtime directory. Anything else raises a `ValueError`.

**Call relations**: `_write_contained` and `_contained_source` call this before touching files. It is the shared gatekeeper that keeps local reads and writes inside the allowed sandbox areas.

*Call graph*: calls 1 internal fn (_root); called by 2 (_contained_source, _write_contained); 2 external calls (Path, PurePosixPath).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox lifecycle and command execution`

This file is the Docker version of a sandbox carrier: the part of the system that gives a conversation a safe place to run commands and read or write files. Think of each conversation as getting its own rented workshop. Docker provides the walls, `/workspace` is the shared workbench, and the egress proxy is the only doorway to the outside internet.

The important job here is to keep containers useful but not wasteful. A conversation's container is named from its conversation ID, so later turns can find the same container again. If the container is running, this code attaches to it. If it was stopped to save memory and network resources, this code starts it again. If it does not exist, this code creates it with a private Docker network and a mounted workspace.

Every command is run with fresh proxy environment variables. That matters because each turn has its own run token for tracking and billing network requests. The token is never baked into the container, so an old container cannot accidentally reuse an old turn's identity.

The file also cleans up idle containers by stopping them and removing their per-conversation network. It does not delete the container or workspace, so quiet conversations can resume later. It also installs the current proxy certificate into reused containers, prepares runtime directories, streams file reads and writes safely, and reports clear errors when Docker or the container fails.

#### Function details

##### `_docker`  (lines 79–93)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and captures its result. It is the small gateway through which the rest of this file asks Docker to create containers, inspect them, execute commands, and change networks.

**Data flow**: It receives Docker arguments, optional input bytes, and a timeout. It starts a `docker ...` process, feeds it the input, waits for stdout and stderr, and returns an exit code plus the two output streams. If Docker takes too long, it kills the process and returns a special timeout code instead of pretending Docker gave a normal answer.

**Call relations**: Almost every DockerCarrier helper relies on this function when it needs Docker to do real work. Higher-level methods such as create, revive, release, and exec build the right Docker command, then hand it to `_docker` to actually run.

*Call graph*: called by 13 (_death_report, _ensure_network, _exec_with, _held_id, _install_ca, _prepare_mounts, _reclaim_idle, _release, _revive, _running_id (+3 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 106–219)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or reconnects to the Docker container for one conversation. It returns a sandbox handle, which is the system's ticket for running commands and accessing files in that container.

**Data flow**: It receives a sandbox specification with the conversation ID, image, workspace path, proxy details, run token, and environment. It first reclaims old idle containers, builds per-turn proxy environment variables, then looks for an existing running or stopped container. It reuses one when possible, otherwise creates a Docker network and starts a new long-lived container, installs the proxy certificate, prepares directories, and returns a handle describing the ready sandbox.

**Call relations**: This is the main setup path used when a conversation needs a Docker sandbox. It coordinates helper methods for finding containers, reviving stopped ones, ensuring networks, installing certificates, and preparing mounts; if Docker reports a name conflict, it treats that as another create call winning the race and attaches to the winner.

*Call graph*: calls 9 internal fn (_ensure_network, _install_ca, _network_name, _prepare_mounts, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier.attach`  (lines 221–250)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an already-existing conversation container without creating a new one. It is useful for read-style access where absence should simply mean there is no sandbox to attach to.

**Data flow**: It receives the same kind of sandbox specification used for creation. It checks whether the named container is running; if not, it checks whether it exists but is stopped and tries to start it again. If it can prepare the container's runtime directories, it returns a sandbox handle; if not, it returns `None`.

**Call relations**: This is the quieter companion to `create`. It uses the same container lookup and revive helpers, but it deliberately avoids fresh Docker creation and converts many failures into `None` because callers using attach are asking, in effect, 'is there something already there?'

*Call graph*: calls 4 internal fn (_prepare_mounts, _revive, _running_id, _stopped_id); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier._reclaim_idle`  (lines 252–320)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops containers and removes Docker networks for conversations that have been idle too long. This protects the host from running out of memory or Docker bridge network space.

**Data flow**: It receives the conversation ID that is being opened and records it as recently touched. It asks Docker which UFO containers and per-conversation networks currently exist, adds any unknown ones to its tracking map, finds conversations that have been idle and have no command in progress, and tries to release each one. If a release fails, it keeps the tracking entry so a later create can try again.

**Call relations**: `create` calls this before opening a sandbox, so cleanup happens naturally when new work arrives. It uses `_held_id` to find the container in any state and `_release` to perform the stop-and-network-removal sequence while respecting lifecycle locks used by revive.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 322–336)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs a normal command inside the conversation's container with the current turn's proxy settings. This is how agent tool commands get network access that is tracked and filtered by UFO.

**Data flow**: It receives a sandbox handle, command arguments, a timeout, and optionally a model command label. It turns the handle's egress environment into Docker `--env` options and delegates the actual execution. The result is an `ExecResult` containing text output, text errors, an exit code, and timeout information.

**Call relations**: This is the public command-running path. It hands the real work to `_exec_with`, adding the per-turn environment that `create` stored in the handle.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier.exec_skill`  (lines 338–342)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the container as the root user for server-side skill loading or syncing. This is reserved for setup-style operations that need more permission than normal sandbox commands.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. Instead of adding proxy environment variables, it adds Docker's `--user root` option and delegates to the shared execution helper. It returns the same `ExecResult` shape as a normal exec.

**Call relations**: This shares the safe execution and revive behavior of `_exec_with`, but is called for privileged skill operations rather than ordinary agent commands.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier._exec_with`  (lines 344–383)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, options: tuple[str, ...]) -> ExecResult
```

**Purpose**: Runs one Docker exec command with supplied Docker options, while protecting the container from idle cleanup during the command. It also retries once if the container was stopped underneath the caller.

**Data flow**: It receives a handle, command arguments, a timeout, and Docker exec options such as environment variables or user selection. It marks the conversation as in flight, runs `docker exec` in `/workspace`, checks whether Docker says the container is not running, revives and retries if possible, then returns a normalized execution result. Finally it clears the in-flight count and updates the last-touched time.

**Call relations**: Both `exec` and `exec_skill` funnel into this method. It calls `_docker` for the actual Docker process and calls `_revive` only when Docker reports that the target container has stopped.

*Call graph*: calls 2 internal fn (_revive, _docker); called by 2 (exec, exec_skill); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 385–403)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file inside the sandbox. It streams the bytes through Docker standard input instead of putting the contents on a command line, which is safer and works for binary data.

**Data flow**: It receives a handle, a sandbox path, and bytes to write. It marks the conversation active, asks `_write_started` to perform the copy, revives and retries if Docker says the container is not running, and raises an `OSError` if the write still fails. It then marks the conversation idle again.

**Call relations**: This is the public write path. It relies on `_write_started` for the actual guarded copy and shares the same revive pattern used by command execution.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 405–423)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Performs one attempt to copy bytes into the container using UFO's sandbox copy-in program. That copy-in program enforces path rules more carefully than a simple shell redirect would.

**Data flow**: It receives a handle, target path, and content bytes. It decides whether the path belongs under the runtime root or normal workspace, then runs Python inside the container with the copy-in program and sends the bytes through stdin. It returns Docker's exit code and stderr bytes.

**Call relations**: `write` calls this for the first attempt and again after a revive if the container had been stopped. This function uses `_docker` only for the concrete `docker exec -i` operation.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `DockerCarrier.read`  (lines 425–459)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the container in chunks. It reads through the container's own view of the filesystem, so the caller sees what sandboxed programs would see.

**Data flow**: It receives a handle and path. It marks the container active, starts a `cat` stream, yields chunks to the caller, and then checks whether the read failed. If the failure was because the container was stopped before producing data, it revives and retries from the beginning. If `cat` reports a familiar filesystem error, it raises an `OSError`; otherwise it raises a runtime error with extra container state.

**Call relations**: This is the public read path. It uses `_read_started` to create each streaming attempt, `_revive` for stopped containers, and `_death_report` when a failed read has no useful stderr.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 461–503)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Sets up one file-read attempt and returns both the byte stream and a place where the final failure details will appear. This split lets the caller retry cleanly without reusing a half-finished async generator.

**Data flow**: It receives a handle and path. It creates an empty failure list and an inner streaming generator. It returns the generator plus that list; while the generator is consumed, it fills the list only if the `cat` command exits with a non-zero code.

**Call relations**: `read` calls this each time it wants to try streaming a file. The actual Docker subprocess is started inside the nested `stream` function when the caller begins iterating.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 476–501)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `cat` inside the container and yields the file bytes as they arrive. It also makes sure an abandoned read does not leave a Docker exec process running forever.

**Data flow**: It starts `docker exec ... cat path` with stdout and stderr pipes. It repeatedly reads bounded chunks from stdout and yields them. After stdout ends, it reads stderr, waits for the process, and records a failure if the exit code is non-zero. If the caller stops reading early, it kills and reaps the process.

**Call relations**: This nested generator is produced by `_read_started` and consumed by `read`. It is the only part of the read path that directly launches the streaming Docker subprocess.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 505–523)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Adds useful Docker container state to a mysterious failed read. It helps distinguish a killed exec command from a stopped, missing, or out-of-memory container.

**Data flow**: It receives a sandbox handle and asks Docker to inspect the container status, exit code, and whether it was killed for using too much memory. It returns a short explanatory string. If inspection itself fails, it returns a string saying Docker inspect failed and includes Docker's stderr.

**Call relations**: `read` calls this only when `cat` died without giving stderr that explains the reason. `_death_report` delegates the Docker inspection command to `_docker`.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 525–531)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured UFO filesystem operation inside the Docker sandbox. This supports higher-level file actions through the same sandbox path as shell commands.

**Data flow**: It receives a handle, an operation name, and operation parameters. It passes them to the shared `ufo fs` helper, which runs the actual operation through this carrier. It returns the operation's result as a dictionary.

**Call relations**: This method plugs the Docker carrier into the SDK's generic filesystem operation helper. Instead of duplicating file-operation logic here, it delegates to `ufo_fs_file_op`.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `DockerCarrier.dial`  (lines 533–540)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that this Docker carrier cannot expose an in-container service port to the outside. For example, a browser debugging endpoint or preview server cannot be reached through this carrier.

**Data flow**: It receives a handle and a port number, but does not use them to build a route. It immediately raises `SandboxUnreachable` with a message explaining that a remote carrier is needed for this kind of access.

**Call relations**: This is part of the carrier interface, but Docker does not implement external per-port access here. Callers that need dialing get a clear refusal instead of a misleading partial connection.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 542–556)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation's container and removes its per-conversation Docker network. This frees the main host resources that idle containers consume.

**Data flow**: It receives a conversation ID and maybe a container ID. If there is a container, it asks Docker to stop it. Then it asks Docker to remove the conversation's network. It returns `true` if both steps reached the desired state, including the case where the network was already gone.

**Call relations**: `_reclaim_idle` calls this while holding the conversation lifecycle lock. It uses `_network_name` to find the right network and `_docker` to perform the stop and network removal.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 558–578)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Starts a previously stopped container again and reconnects its Docker network first. This lets a quiet conversation resume after idle reclaim stopped its container.

**Data flow**: It receives the conversation ID and container ID. Under a lifecycle lock, it marks the conversation touched, ensures the network exists, connects the container to that network, and starts the container. It returns `true` if the container is running again, `false` if Docker refused the connect or start in an expected way, and raises for network setup failures.

**Call relations**: Create, attach, exec, write, and read all call this when they find a stopped container or Docker reports one. The lifecycle lock keeps revive from interleaving with `_release` during idle cleanup.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (_exec_with, attach, create, read, write).


##### `DockerCarrier._held_id`  (lines 580–588)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a given container name in any state: running, paused, or exited. This is used before releasing resources because even a non-running container can still be the named conversation container.

**Data flow**: It receives a Docker container name. It runs a Docker search over all containers, raises if Docker itself fails, and returns the found ID or `None` if no matching container exists.

**Call relations**: `_reclaim_idle` calls this before `_release` so release knows which container, if any, should be stopped before the network is removed.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 590–599)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a named container only if it is stopped/exited. This is how the carrier detects containers that idle reclaim stopped but kept for later reuse.

**Data flow**: It receives a Docker container name. It asks Docker for exited containers with that exact name, raises on Docker command failure, and returns the container ID or `None`.

**Call relations**: Both `create` and `attach` use this after checking for a running container. If it finds a stopped container, those methods can try `_revive` instead of creating from scratch.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 601–612)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a named container only if it is currently running. It treats Docker command failure as a real error, not as 'nothing found.'

**Data flow**: It receives a Docker container name. It asks Docker for running containers with that exact name, raises if Docker fails, and returns the ID or `None` when the command succeeds but no container matches.

**Call relations**: `create` and `attach` use this as their first lookup. This prevents them from starting unnecessary containers when the right one is already alive.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 614–615)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the deterministic Docker network name for one conversation. Deterministic names let different calls find the same network without storing extra state.

**Data flow**: It receives a conversation UUID. It combines the carrier's network prefix with the UUID's hex form and returns that string.

**Call relations**: `create`, `_revive`, and `_release` use this whenever they need the per-conversation Docker network name.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 617–628)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if needed. It is safe if two callers try to create the same network at the same time.

**Data flow**: It receives a network name. It first asks Docker whether a matching network exists. If it does, it returns. Otherwise it asks Docker to create it and treats Docker's 'already exists' response as success, because that means another caller created it first.

**Call relations**: `create` calls this before a fresh container run, and `_revive` calls it before reconnecting a stopped container. The actual Docker commands go through `_docker`.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 630–643)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current egress proxy certificate inside a container. Without this, HTTPS tools in a reused container might reject the proxy after the UFO server restarts with a fresh certificate.

**Data flow**: It receives a container ID and certificate text. It runs a root shell command inside the container, writes the certificate file through stdin, and updates the system certificate store. If Docker reports failure, it raises an error with Docker's message.

**Call relations**: `create` calls this for both new and reused containers before returning a usable handle. It relies on `_docker` for the privileged Docker exec.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._prepare_mounts`  (lines 645–672)

```
async def _prepare_mounts(self, container_id: str, conversation_id: UUID) -> None
```

**Purpose**: Prepares ownership and runtime directories inside the container so sandbox code can use the mounted workspace safely. This includes creating a private runtime root and session file with the expected permissions.

**Data flow**: It receives a container ID and conversation ID. It calculates the sandbox runtime paths, then runs a root shell script inside the container to fix workspace ownership, create needed directories, and set secure permissions. If the script fails, it raises a runtime error.

**Call relations**: `create` calls this before handing out a new or reused sandbox, and `attach` calls it before returning an existing one. It uses `_docker` to run the setup command and shared SDK path helpers to compute the runtime root.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `manifest`  (lines 675–680)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the UFO plugin system. It says that this file provides a carrier named `docker` and tells UFO which factory class to instantiate.

**Data flow**: It takes no input. It constructs a manifest object containing the extension name, version, and carrier specification, then returns it.

**Call relations**: The extension loader calls this when discovering available carriers. The returned manifest points the system at `DockerCarrier` whenever configuration asks for the Docker sandbox backend.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `cross-cutting: sandbox startup, per-turn command/file/network operations, and cancellation cleanup`

A sandbox is the isolated computer where a conversation’s tools and code run. This file is the bridge between UFO’s generic sandbox interface and E2B’s remote sandbox service. Without it, a deployment that chooses the E2B backend could not start a workspace, keep it alive between turns, run commands, move files, or connect to services running inside it.

The main class, E2BCarrier, acts like a concierge for each conversation’s remote machine. When a conversation starts or resumes, it either reconnects to the existing E2B sandbox or creates a new one from the right template size. It then prepares the machine: installs the UFO client binary, trusts the proxy certificate, creates `/workspace`, and sets resource limits so user work cannot starve the sandbox’s own control process.

A major concern here is E2B’s “lease” behavior. The provider pauses idle sandboxes instead of deleting them, but calls need to renew enough time so a command or file transfer is not interrupted. This file keeps a local lease cache, reconnects when needed, and forgets leases after failed calls so later operations do not trust stale state.

It also carefully handles command timeouts. E2B may stop the client stream while leaving the command running, so commands are launched in their own process group and can be killed as a whole. File streaming, port dialing, retries, and provider failures are all translated into UFO’s common sandbox behavior.

#### Function details

##### `E2BCommandHandle.wait`  (lines 188–188)

```
async def wait(self) -> E2BCommandResult
```

**Purpose**: This protocol method describes how a caller waits for a background E2B command to finish. It exists so this file can talk about the SDK object without depending tightly on its concrete class.

**Data flow**: It starts with a running command handle that already has a process id. Waiting on it produces the command’s final standard output, standard error, and exit code.

**Call relations**: E2BCarrier._exec_with launches commands in the background so it can learn their process id, then uses this wait method to collect the final result.


##### `E2BCommands.run`  (lines 209–218)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None, background: Literal[False]=False) -> E2BCommandResult
```

**Purpose**: This protocol method describes E2B’s command runner. It can either run a command to completion or start it in the background and return a handle immediately.

**Data flow**: It receives a shell command string plus optional working directory, environment, user, timeout, and background flag. It sends that work into the sandbox and returns either the completed result or a handle for a still-running command.

**Call relations**: Many carrier methods rely on this shape: preparation uses it for setup commands, _exec_with uses it for user commands, _stop_group uses it to kill process groups, and _still_there uses it as a small health probe.


##### `E2BFileStream.__aiter__`  (lines 225–225)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: This protocol method describes how a streamed file read yields chunks of bytes over time. It lets callers read large files without loading the whole file into memory.

**Data flow**: It starts from an open file stream returned by E2B. Iterating over it produces byte chunks until the file is fully read or the stream ends.

**Call relations**: E2BCarrier.read uses this iterator when it streams a sandbox file back to the caller.


##### `E2BFileStream.aclose`  (lines 227–227)

```
async def aclose(self) -> None
```

**Purpose**: This protocol method describes how to close an open streamed file connection. It matters because an unfinished stream still holds network resources.

**Data flow**: It receives the open stream object and releases the underlying connection. It returns no file data; its effect is cleanup.

**Call relations**: E2BCarrier.read calls this in a finally block so the stream is closed even if the caller stops reading early.


##### `E2BFiles.write`  (lines 231–231)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: This protocol method describes E2B’s file upload operation. It is the safe way to send raw bytes into the sandbox, since command execution only accepts shell strings.

**Data flow**: It receives a sandbox path, text or bytes, and optionally a user. It writes that content into the sandbox filesystem and returns the SDK’s write result.

**Call relations**: E2BCarrier.write uses it for normal uploads, while _ensure_client and _install_ca use it to place the UFO client binary and certificate inside the sandbox.


##### `E2BFiles.read`  (lines 233–233)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: This protocol method describes E2B’s file download operation. In this file it is used in streaming mode so large files can be sent out piece by piece.

**Data flow**: It receives a sandbox path and a format choice. It opens the file in the sandbox and returns a stream object that yields bytes.

**Call relations**: E2BCarrier.read calls this after renewing the sandbox lease, then passes the stream’s chunks back to the caller.


##### `E2BSandbox.get_host`  (lines 242–242)

```
def get_host(self, port: int) -> str
```

**Purpose**: This protocol method describes how to turn an internal sandbox port into the external host name E2B exposes. It is used when something outside the sandbox needs to reach a service running inside it.

**Data flow**: It receives a port number. It returns the public hostname for that port on this sandbox.

**Call relations**: E2BCarrier.dial calls this after renewing the lease, then packages the host with TLS and access-token information.


##### `E2BSdk.create`  (lines 246–255)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes creating a new E2B sandbox from a template. It is used when there is no existing sandbox to resume.

**Data flow**: It receives template name, lease timeout, metadata, lifecycle settings, network settings, and API key. It asks E2B to start a sandbox and returns the sandbox object.

**Call relations**: E2BCarrier._resume_or_open calls this only when reconnecting to an existing sandbox is not possible or no resume id was supplied.


##### `E2BSdk.connect`  (lines 257–263)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes reconnecting to an existing E2B sandbox. In E2B, this also wakes a paused sandbox and sets a fresh lease.

**Data flow**: It receives a sandbox id, desired timeout span, and API key. It returns a live sandbox object if E2B still has that sandbox.

**Call relations**: E2BCarrier._connected wraps this call with retries and timeout protection, and all resume or lease-renewal paths go through that wrapper.


##### `E2BCarrier.create`  (lines 315–399)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This opens the sandbox for a conversation and returns a UFO SandboxHandle that the rest of the system can use. It may resume a persisted sandbox, reuse a live in-process one, or create a fresh E2B sandbox.

**Data flow**: It receives a SandboxSpec containing conversation id, optional resume id, size, proxy data, token, environment, and turn id. It chooses the sandbox, prepares it enough to run UFO workloads, records a local lease, and returns a handle naming the container and runtime settings.

**Call relations**: This is the main startup path for an E2B sandbox. It checks _leased, delegates opening to _resume_or_open, prepares with _ensure_client, _prepare_runtime, or _prepare_strictly, drops bad leases with _drop, and finally hands a SandboxHandle back to core sandbox orchestration.

*Call graph*: calls 6 internal fn (_drop, _ensure_client, _leased, _prepare_runtime, _prepare_strictly, _resume_or_open); 8 external calls (__init__, __init__, __init__, timeout, emit_metric, log, egress_proxy_env, sandbox_runtime_root).


##### `E2BCarrier.attach`  (lines 401–426)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reconnects to an already-known sandbox for read-style access without creating a replacement if it is gone. It is useful when the caller wants the existing workspace or nothing.

**Data flow**: It receives a SandboxSpec with a resume id. If there is no id, it returns None. If E2B still has the sandbox, it reconnects, records a lease, and returns a SandboxHandle; if E2B says it is missing, it clears the local cache and returns None.

**Call relations**: It uses _connected directly rather than the in-process cache because it must answer based on the provider’s current truth. Unlike create, it never calls _resume_or_open and never creates a fresh empty sandbox.

*Call graph*: calls 1 internal fn (_connected); 3 external calls (__init__, __init__, sandbox_runtime_root).


##### `E2BCarrier._resume_or_open`  (lines 428–468)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: This chooses between reconnecting to an existing sandbox and creating a new one. It protects conversations from getting stuck forever on a sandbox id that E2B no longer knows.

**Data flow**: It receives the sandbox spec and an optional sandbox id to resume. It tries _connected when an id is present; if E2B says the sandbox is gone, it logs that fact and creates a new sandbox from the template for the requested size.

**Call relations**: E2BCarrier.create calls this during sandbox opening. It hands provider reconnect work to _connected, and only falls through to the SDK create call when resume is impossible or not requested.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 470–486)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: This prepares a sandbox and treats failure as fatal for that open. It is used for fresh or not-yet-proven sandboxes, where UFO cannot assume earlier setup succeeded.

**Data flow**: It receives a sandbox and its spec. It tries preparation several times only for transport-level failures, waits between retries, logs and counts retries, and either returns after success or drops the lease and raises the failure.

**Call relations**: E2BCarrier.create calls this for new sandboxes or cached sandboxes without durable proof of preparation. It calls _prepare for the actual setup and _drop when the sandbox should no longer be trusted.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 488–553)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: This is the safe wrapper around E2B reconnect. It retries temporary provider or network failures and puts a wall-clock limit around the whole resume attempt.

**Data flow**: It receives a conversation id, sandbox id, and lease span. It calls the SDK connect method, retries retryable transport or 429/5xx errors with backoff, and returns the sandbox. If the provider does not answer in time, it raises SandboxProviderUnavailable.

**Call relations**: _resume_or_open uses it during setup, _sandbox uses it for lease renewal, and attach uses it for read-only reattachment. It centralizes the rules for reconnecting to E2B.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 5 external calls (__init__, sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 555–557)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This performs the full sandbox preparation sequence. It combines installing the UFO client with preparing the runtime environment.

**Data flow**: It receives a sandbox and proxy certificate. It ensures the right UFO client binary is present, then installs trust, workspace, and resource limits through _prepare_runtime.

**Call relations**: _prepare_strictly calls this when a sandbox must be fully proven ready before create can return.

*Call graph*: calls 2 internal fn (_ensure_client, _prepare_runtime); called by 1 (_prepare_strictly).


##### `E2BCarrier._prepare_runtime`  (lines 559–564)

```
async def _prepare_runtime(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This sets up the operating environment inside the sandbox so UFO workloads can run safely and reach the proxy. It installs trust, creates the workspace, and limits workload resource use.

**Data flow**: It receives a sandbox and certificate text. It writes and installs the certificate, ensures `/workspace` exists with the right owner, and applies memory and process-count caps.

**Call relations**: It is called by _prepare for strict setup and directly by create for resumed sandboxes where runtime preparation can be bounded and deferred if the sandbox is temporarily silent.

*Call graph*: calls 3 internal fn (_cap_workload, _ensure_workspace, _install_ca); called by 2 (_prepare, create).


##### `E2BCarrier._ensure_client`  (lines 566–591)

```
async def _ensure_client(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure the sandbox has the exact UFO client binary this process expects. That client is needed because workloads enter the remote sandbox through UFO’s own command wrapper.

**Data flow**: It receives a sandbox and checks the hash of `/usr/local/bin/ufo`. If it already matches, it records the sandbox as ready. If not, it uploads the bundled client to a staging path, verifies its hash, installs it atomically, and records readiness.

**Call relations**: create calls it directly for resumed sandboxes before bounded runtime prep, and _prepare calls it during strict preparation. It uses E2B command and file APIs rather than other carrier helpers.

*Call graph*: called by 2 (_prepare, create); 2 external calls (sha256, uuid4).


##### `E2BCarrier._leased`  (lines 593–608)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: This looks up the local cached lease for a conversation and sweeps expired entries. It keeps the process from remembering every sandbox it has ever touched.

**Data flow**: It receives a conversation id. It reads the current clock, removes expired leases from the cache, and returns the lease that was associated with the requested conversation if one was present.

**Call relations**: create uses it to decide whether a process-local sandbox can be reused. _sandbox uses it to decide whether it must reconnect and renew the lease before doing work.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 610–618)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This installs the proxy certificate authority inside the sandbox. That lets programs in the sandbox trust TLS connections routed through UFO’s proxy.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path, runs the install/update command as root, and raises a clear runtime error if the command fails.

**Call relations**: _prepare_runtime calls this as the first part of making the sandbox’s network environment trustworthy.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._ensure_workspace`  (lines 620–629)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure `/workspace` exists and belongs to the normal sandbox user. That directory is the conversation’s working disk.

**Data flow**: It receives a sandbox. It runs a root command to create the directory and set ownership, returning normally on success or raising a runtime error with command output on failure.

**Call relations**: _prepare_runtime calls this after certificate setup so later commands can safely run in the shared workspace.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._cap_workload`  (lines 631–641)

```
async def _cap_workload(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This applies memory and process limits to the sandbox workload cgroups, which are Linux control groups used to limit resource use. The goal is to stop user commands from starving E2B’s own control process.

**Data flow**: It receives a sandbox. It runs a root command that calculates a safe memory ceiling and writes memory and process limits into the workload cgroup files.

**Call relations**: _prepare_runtime calls this after workspace setup. The limits then affect later commands launched by exec and exec_skill.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier.exec`  (lines 643–695)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: This runs a normal user command inside the sandbox workspace. It uses the turn’s egress proxy environment so network traffic is routed and metered correctly.

**Data flow**: It receives a sandbox handle, argument tuple, timeout, and optional model command label. It delegates to _exec_with as the normal sandbox user and returns an ExecResult containing output, errors, exit code, and timeout information if applicable.

**Call relations**: Core sandbox users call this for ordinary tool execution. It is a thin public wrapper around _exec_with.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier.exec_skill`  (lines 697–701)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a skill setup or synchronization command as root inside the sandbox. It is for trusted server-carried work that needs elevated permissions.

**Data flow**: It receives a sandbox handle, command arguments, and timeout. It delegates to _exec_with with the root user and returns the resulting ExecResult.

**Call relations**: It shares the command-running machinery with exec, but passes a user value that causes _exec_with to run as root and use only the base sandbox environment.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier._exec_with`  (lines 703–750)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, user: str | None) -> ExecResult
```

**Purpose**: This is the real command runner. It renews the sandbox lease, checks whether a previously silent sandbox is alive, launches the command in its own process group, waits for completion, and converts provider exceptions into UFO-style results.

**Data flow**: It receives a handle, argument tuple, timeout, and optional user. It gets a leased sandbox, quotes the arguments into a shell command, starts it in the background to learn its process id, tracks that process group, waits for the result, and returns ExecResult. On timeout it kills the group if possible; on cancellation or unexpected failure it drops the lease.

**Call relations**: exec and exec_skill both call this. It relies on _sandbox for lease renewal, _still_there for silent-box checks, _stop_group for timeout cleanup, _mark_silent when the command channel fails early, _forget_group after completion, and _drop when the lease should no longer be trusted.

*Call graph*: calls 6 internal fn (_drop, _forget_group, _mark_silent, _sandbox, _still_there, _stop_group); called by 2 (exec, exec_skill); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.stop_commands`  (lines 752–774)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: This stops commands that are still running for a specific turn after that turn has been cancelled. It avoids killing commands from sibling turns that share the same conversation sandbox.

**Data flow**: It receives a sandbox handle. It removes the tracked process groups for that handle’s container and turn; if there are any, it gets a leased sandbox and sends kill signals to each group.

**Call relations**: This complements _exec_with’s cancellation behavior. _exec_with deliberately leaves cancelled commands running because it cannot tell a permanent stop from replayable preemption; stop_commands is called from the layer that knows the stop is final.

*Call graph*: calls 2 internal fn (_sandbox, _stop_group).


##### `E2BCarrier._forget_group`  (lines 776–786)

```
def _forget_group(self, handle: SandboxHandle, pid: int) -> None
```

**Purpose**: This removes a process group from the in-memory tracking map after the command has ended or has been stopped. It prevents later cleanup from signaling an old process id.

**Data flow**: It receives a sandbox handle and process id. It finds the matching turn-and-container entry, removes that process id, and deletes the whole entry if no groups remain.

**Call relations**: _exec_with calls this in its cleanup path whenever a launched command is no longer intentionally left running.

*Call graph*: called by 1 (_exec_with).


##### `E2BCarrier._stop_group`  (lines 788–814)

```
async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int, user: str | None) -> None
```

**Purpose**: This sends a force-kill signal to an entire command process group. Killing the group, not just the shell, stops child processes such as compilers, test workers, or background helpers that were started by the command.

**Data flow**: It receives the sandbox, container id, process group id, and user. It runs `kill -9 -pid` in the sandbox. If the sandbox does not answer, it marks the container as silent and records a metric; other cleanup errors are counted but not raised.

**Call relations**: _exec_with calls this when a command times out after its process id is known. stop_commands calls it when a cancelled turn needs its still-running command groups stopped.

*Call graph*: calls 1 internal fn (_mark_silent); called by 2 (_exec_with, stop_commands); 1 external calls (emit_metric).


##### `E2BCarrier._mark_silent`  (lines 816–820)

```
def _mark_silent(self, container_id: str) -> None
```

**Purpose**: This remembers that a sandbox stopped answering commands for a short time. It prevents the next command from spending its full timeout on a container that is probably wedged.

**Data flow**: It receives a container id. It stores that id with an expiry timestamp based on the current clock and the configured silent-mark duration.

**Call relations**: _exec_with marks a sandbox silent if command launch times out before a process id is known. _stop_group marks it silent when even the cleanup kill command times out. _still_there later reads and clears or honors this mark.

*Call graph*: called by 2 (_exec_with, _stop_group).


##### `E2BCarrier._still_there`  (lines 822–852)

```
async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None
```

**Purpose**: This quickly probes a sandbox that was recently marked silent before trusting it with another long command. It separates “temporarily overloaded” from “not answering at all” as cheaply as possible.

**Data flow**: It receives a sandbox object and container id. If there is no active silent mark, it returns. If the mark expired, it clears it. Otherwise it runs a short `true` command; success clears the mark, failure raises SandboxUnreachable.

**Call relations**: _exec_with calls this before launching a command. It uses the mark set by _mark_silent, logs expiry, and emits an unreachable metric when the probe fails.

*Call graph*: called by 1 (_exec_with); 3 external calls (__init__, emit_metric, log).


##### `E2BCarrier.write`  (lines 854–865)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This uploads bytes into the sandbox filesystem. It is used for file content that should not be squeezed through a shell command string.

**Data flow**: It receives a sandbox handle, target path, and bytes. It gets a leased sandbox, writes the content through E2B’s file API, and returns nothing. If the write fails, it drops the cached lease and re-raises the error.

**Call relations**: Callers use this for normal file upload. It depends on _sandbox for a valid lease and _drop when the provider call proves the cached lease cannot be trusted.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 867–887)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This streams a file out of the sandbox in chunks. It supports large files without holding the whole file in this process’s memory.

**Data flow**: It receives a sandbox handle and path. It gets a long enough lease, opens an E2B file stream, yields each byte chunk to the caller, maps E2B missing-file errors to FileNotFoundError, drops the lease on other open failures, and always closes the stream afterward.

**Call relations**: Callers use this to download sandbox output. It relies on _sandbox for lease renewal and _drop for failed provider calls, and it uses the stream protocol’s iterator and close behavior.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 889–894)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs a higher-level UFO file operation inside the sandbox. It delegates to the shared UFO filesystem helper rather than duplicating file-operation logic here.

**Data flow**: It receives a sandbox handle, operation name, and operation parameters. It passes them to ufo_fs_file_op, which runs the appropriate `ufo fs` command through this carrier and returns a dictionary result.

**Call relations**: This is the E2B carrier’s hook for generic sandbox file operations. It hands off the details to ufo.sdk.sandbox.ufo_fs_file_op.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `E2BCarrier.dial`  (lines 896–917)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This returns the public network target for a service running on a port inside the sandbox. It is how outside code reaches things like browser debugging ports or preview web servers.

**Data flow**: It receives a sandbox handle and port. It renews the sandbox lease for a longer dial-friendly span, asks E2B for the host name for that port, and returns a DialTarget with TLS enabled and the traffic access token header when available.

**Call relations**: Callers use this when they need a live network connection into the sandbox. It uses _sandbox for reconnect/lease behavior and maps a missing E2B sandbox to UFO’s SandboxUnreachable error.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 919–961)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: This returns a sandbox object whose lease covers the work about to happen. It avoids reconnecting on every operation while still renewing before the provider might pause the sandbox.

**Data flow**: It receives a handle, the number of seconds the next operation needs, and an optional minimum lease span. It checks the cached lease; if it names the right container and lasts long enough, it returns it. Otherwise it drops the cache, reconnects with _connected, records a new lease, logs renewal, and returns the sandbox.

**Call relations**: _exec_with, write, read, stop_commands, and dial all call this before provider work. It uses _leased to inspect and sweep the cache and _connected to renew through E2B.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 5 (_exec_with, dial, read, stop_commands, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 963–968)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: This forgets the cached lease for a conversation after a provider call fails. It is a safety measure: if the sandbox stopped answering, the local lease timestamp is no longer reliable evidence.

**Data flow**: It receives a conversation id and a short label describing what was happening. It removes that conversation from the live lease cache and logs the drop.

**Call relations**: create, _prepare_strictly, _exec_with, write, and read call this when failures mean later calls should reconnect instead of trusting cached state.

*Call graph*: called by 5 (_exec_with, _prepare_strictly, create, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 971–988)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: This parses the E2B template configuration from an environment-variable string. It makes sure every supported sandbox size has exactly one template reference.

**Data flow**: It receives a comma-separated string such as `small=ref,medium=ref,large=ref`. It splits it into a dictionary from size to template reference and raises an error if an entry is malformed or the set of sizes does not match UFO’s supported sizes.

**Call relations**: build_e2b_carrier uses this to build the carrier configuration. e2b_runtime_digest uses it to compute a stable digest of the selected runtime templates.

*Call graph*: called by 2 (build_e2b_carrier, e2b_runtime_digest).


##### `e2b_runtime_digest`  (lines 991–999)

```
def e2b_runtime_digest() -> str
```

**Purpose**: This computes a stable fingerprint of the E2B template map selected by the current process. The manifest uses it to identify the sandbox runtime version.

**Data flow**: It reads E2B_TEMPLATES from the environment, parses it with sandbox_templates, serializes the resulting map in sorted JSON form, hashes it with SHA-256, and returns a `sha256:...` string.

**Call relations**: manifest refers to this function in the CarrierSpec so the wider system can ask what runtime definition this carrier is using.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (sha256, dumps).


##### `build_e2b_carrier`  (lines 1002–1011)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: This constructs an E2BCarrier from environment configuration and the bundled UFO client binary. It is the factory used when the E2B carrier is selected.

**Data flow**: It reads E2B_API_KEY and E2B_TEMPLATES from the environment, validates and parses the templates, reads the client binary for the E2B Linux target, and returns a configured E2BCarrier.

**Call relations**: manifest installs this function as the carrier factory. It calls sandbox_templates for configuration parsing and client_binary to locate the executable that _ensure_client later installs in sandboxes.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (__init__, client_binary).


##### `manifest`  (lines 1014–1027)

```
def manifest() -> Manifest
```

**Purpose**: This declares the E2B extension to UFO’s plugin system. It tells the core system that a carrier named `e2b` exists and how to build it.

**Data flow**: It creates a Manifest containing one CarrierSpec with the carrier name, factory, off-cluster flag, supported sizes, and runtime digest provider. The result is returned to the extension loader.

**Call relations**: The UFO manifest system calls this when loading extensions. The returned CarrierSpec points back to build_e2b_carrier and e2b_runtime_digest so the core can instantiate and describe the E2B backend without hard-coding it.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `cross-cutting terminal request handling`

In a single-process setup, a terminal request can be handed directly to the code holding the user's live terminal connection. In a fleet of pods, that direct handoff breaks: the user's browser may be connected to one pod while the workflow that needs the terminal runs on another. This file provides the shared rendezvous point between them.

Think of Redis as a numbered noticeboard. A pod with a live terminal posts a short-lived "I am here" note. A workflow that wants to run a terminal operation checks for that note, takes a per-conversation lock so only one operation runs at a time, posts an operation into a Redis stream, and waits for a reply stream. The connection pod reads the operation, delivers it to the user side, and later posts the result back. Large input or output bytes are not squeezed into Redis messages; they are stored in the fleet's blob store and referenced by key.

The file is careful about failure. Keys have expiration times, blocking Redis reads have deadlines, and Redis errors are turned into terminal-specific "gone" errors. It also uses delivery markers so a reconnecting client does not accidentally run the same command twice. Without this file, terminal operations would often disappear, hang forever, or run more than once when requests cross pod boundaries.

#### Function details

##### `_text`  (lines 55–58)

```
def _text(value: bytes | str) -> str
```

**Purpose**: Converts a Redis field value into normal text. Redis values may arrive as bytes or strings, and the rest of the file wants one predictable shape.

**Data flow**: It receives one value from Redis. If it is already text, it returns it unchanged; if it is bytes, it decodes those bytes into text. Nothing else is changed.

**Call relations**: This small helper is used wherever Redis data is read back, including operation decoding, reply decoding, binding reads, gate checks, and Lua-script results. It keeps those callers from repeating the same bytes-versus-text check.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 61–66)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: Turns the flat field list returned by the Redis Lua script into a normal field dictionary. This makes a stream entry easier for the rest of the code to read.

**Data flow**: It receives a list like field, value, field, value. It checks that the input is really a list, converts each item to text with _text, and returns a dictionary mapping each field name to its value.

**Call relations**: RedisTerminals.next_op calls this after the Lua script claims an operation. The resulting dictionary is then handed to RedisTerminals._decode_op so it can become a TerminalOp object.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 69–74)

```
def _bind_payload(cwd: str, member_id: UUID | None, runtime_id: str) -> str
```

**Purpose**: Builds the small JSON note that says where a terminal is and who it belongs to. This note is what other pods read to find the right terminal workspace.

**Data flow**: It receives a current working directory, an optional member id, and a runtime id. It writes them into a JSON string, using the member id's hex text when present. The JSON string is returned for storage in Redis.

**Call relations**: RedisTerminals._heartbeat uses it to publish the live terminal binding. RedisTerminals._run_op uses it to pin the same binding while an operation is in flight.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 148–157)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts stream entries from Redis's XREAD response and checks that the response has the expected shape. This prevents the code from silently misreading a malformed or unexpected Redis reply.

**Data flow**: It receives the raw XREAD response. If there is no response, it returns an empty list. If the response is not the expected list shape, it raises an error; otherwise it returns the entries inside the first stream response.

**Call relations**: RedisTerminals._await_reply uses this after each Redis blocking read of a reply stream. It gives that caller a clean list of reply entries to inspect.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 185–202)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the current asyncio event loop. This matters because async Redis clients are tied to the loop that created them, like a tool that only works at the workbench where it was assembled.

**Data flow**: It reads the currently running event loop and looks for an existing Redis client for that loop. If none exists, it creates one from the configured Redis URL with explicit socket timeouts, stores it, and returns it.

**Call relations**: Nearly every Redis operation in this class goes through this method: heartbeats, binding reads, sends, replies, cleanup, and operation polling. It is the common doorway from this transport into Redis.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 204–205)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores the live terminal binding for a conversation. The binding is the short-lived note saying a terminal connection is currently present.

**Data flow**: It receives a conversation id and returns a string key containing that id. It does not touch Redis itself.

**Call relations**: RedisTerminals._heartbeat uses this key when refreshing the live binding. RedisTerminals._read_binding uses the same key when another pod is trying to find the terminal.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 207–208)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores a pinned binding while an operation is running. This keeps the terminal visible even after the held client stream has already handed off the operation.

**Data flow**: It receives a conversation id and returns the corresponding inflight Redis key. It only formats the name.

**Call relations**: RedisTerminals._run_op writes this key before posting an operation, RedisTerminals._read_binding falls back to it if the live binding is absent, and RedisTerminals._clear_op removes it during cleanup.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 210–211)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name where terminal operations for a conversation are posted. This stream is the shared queue between the workflow pod and the connection pod.

**Data flow**: It receives a conversation id and returns the stream key for that conversation's operations. It has no side effects.

**Call relations**: RedisTerminals._run_op writes operations to this stream, RedisTerminals.next_op reads and claims from it, and RedisTerminals._clear_op deletes completed entries from it.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 213–214)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: Builds the Redis stream name where one operation's reply will be posted. Each operation has its own reply stream so the sender can wait for exactly the answer it requested.

**Data flow**: It receives an operation id and returns the reply stream key. It only creates the key name.

**Call relations**: RedisTerminals._await_reply waits on this stream, RedisTerminals._deliver_reply writes to it, and RedisTerminals._clear_op deletes it after the operation ends.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 216–217)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key used as the per-conversation lock. The lock stops two terminal operations from running at the same time in one conversation.

**Data flow**: It receives a conversation id and returns the lock key string. It does not acquire the lock by itself.

**Call relations**: RedisTerminals.send uses this key before posting an operation. That is where the one-at-a-time rule is enforced.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 219–220)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key used as a delivery marker for an operation. The marker means "this operation has already been handed to a client stream."

**Data flow**: It receives an operation id and returns the marker key. It does not create or check the marker itself.

**Call relations**: RedisTerminals._clear_op deletes this marker during cleanup. The Lua script used by RedisTerminals.next_op creates and checks matching marker keys directly through the shared prefix.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 222–223)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key that stores metadata about an in-flight operation. That metadata says which conversation and member the operation belongs to.

**Data flow**: It receives an operation id and returns the metadata key. It only formats the key name.

**Call relations**: RedisTerminals._run_op writes this metadata, RedisTerminals.staged and RedisTerminals._deliver_reply read it to check permission, and RedisTerminals._clear_op removes it after the operation ends.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 225–226)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for an operation's input body. This is used when the request includes bytes too large or unsuitable to keep directly in the Redis stream.

**Data flow**: It receives an operation id and returns the blob key for the staged request body. It does not read or write the blob store itself.

**Call relations**: RedisTerminals._run_op stores the input body under this key, RedisTerminals.staged reads it for the serving side, and RedisTerminals._clear_op deletes it later.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 228–229)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for a large operation reply. Small replies ride in Redis; large replies are stored as blobs and referenced from the reply stream.

**Data flow**: It receives an operation id and returns the blob key for that operation's reply bytes. It only makes the name.

**Call relations**: RedisTerminals._deliver_reply writes large replies under this key, RedisTerminals._decode_reply reads them, and RedisTerminals._clear_op deletes them during cleanup.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 231–253)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that this pod is currently holding a live terminal connection for a conversation. It starts or shares a heartbeat that keeps the Redis binding fresh.

**Data flow**: It receives the conversation id, working directory, optional member id, and optional runtime id. Under a local lock, it creates a _Hold record if needed, starts RedisTerminals._heartbeat for the first connection, and increments the local connection count.

**Call relations**: This is called by the serving side when a terminal stream is held open. It hands ongoing Redis publishing to RedisTerminals._heartbeat, and RedisTerminals.disconnect later reduces the count and stops the heartbeat when the last connection leaves.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 255–264)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Marks that one held terminal connection for a conversation has gone away on this pod. When the last local connection leaves, it stops the heartbeat.

**Data flow**: It receives a conversation id, finds the local hold record, and subtracts one connection. If no connections remain, it cancels the heartbeat task and removes the local hold record. It does not delete the Redis binding directly.

**Call relations**: This is the counterpart to RedisTerminals.connect. By canceling the heartbeat instead of deleting the Redis key, it lets Redis expiration handle reconnect races safely.


##### `RedisTerminals._heartbeat`  (lines 266–280)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Keeps the live terminal binding visible in Redis while this pod still holds the connection. It is a background pulse that says "this terminal is still here."

**Data flow**: It receives the conversation and workspace details, turns them into a JSON payload with _bind_payload, then repeatedly writes that payload to the binding key with a time-to-live and sleeps before refreshing again. Redis errors are ignored so the loop can try again.

**Call relations**: RedisTerminals.connect starts this task. It uses RedisTerminals._bind_key and RedisTerminals._client to refresh Redis until RedisTerminals.disconnect cancels the task.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 282–295)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the workspace information for a terminal connection held by this same pod. It is a fast local lookup with no Redis call.

**Data flow**: It receives a conversation id and checks the local hold table under a lock. If this pod has no hold, it returns None; otherwise it returns a TerminalWorkspace containing the directory, member id, and runtime id.

**Call relations**: This supports callers that need to know whether the current pod itself holds the connection. Cross-pod discovery uses RedisTerminals.arrived and RedisTerminals._read_binding instead.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 297–310)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal binding to appear in Redis. This covers the normal reconnect gap where the user's client is between held streams.

**Data flow**: It receives a conversation id and a grace period. It repeatedly calls RedisTerminals._read_binding until it finds a workspace or the deadline passes, sleeping briefly between tries. It returns the workspace or None.

**Call relations**: RedisTerminals.send calls this before posting an operation. It gives the terminal a fair chance to arrive before send raises TerminalAbsent.

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 312–330)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Reads the terminal workspace binding from Redis. It checks both the live connection binding and the pinned in-flight binding.

**Data flow**: It receives a conversation id, reads the live binding key, and if that is missing reads the inflight key. If neither exists it returns None; otherwise it parses the JSON and returns a TerminalWorkspace with the saved directory, member, and runtime.

**Call relations**: RedisTerminals.arrived uses this while waiting for a terminal to appear. It relies on RedisTerminals._bind_key, RedisTerminals._inflight_key, RedisTerminals._client, and _text to fetch and decode the shared note.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 332–388)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one terminal operation and waits for its answer. It is the main workflow-side entry point for asking the user's terminal to do something.

**Data flow**: It receives the conversation id, operation details, timeout, and optional body bytes. It waits for a binding, creates a TerminalOp, acquires the Redis lock for the conversation, runs RedisTerminals._run_op under a deadline, then returns the reply bytes. Missing terminals, timeouts, and Redis failures become terminal-specific errors.

**Call relations**: This method ties together discovery, locking, posting, waiting, and cleanup. It calls RedisTerminals.arrived first, uses RedisTerminals._lock_key and RedisTerminals._client for the one-at-a-time lock, and hands the actual operation work to RedisTerminals._run_op.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 6 external calls (__init__, __init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 390–436)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: Performs the actual post-lock operation flow: prepare metadata, stage input bytes, publish the operation, wait for the reply, and clean up. It assumes RedisTerminals.send already holds the conversation lock.

**Data flow**: It receives the conversation id, TerminalOp, optional body, known workspace binding, and deadline. It writes operation metadata and an inflight binding to Redis, stores the body in the blob store if present, adds the operation to the Redis stream, waits for the reply, and finally calls RedisTerminals._clear_op whether the wait succeeds or fails.

**Call relations**: RedisTerminals.send calls this after acquiring the lock. It uses RedisTerminals._op_fields to encode the operation, RedisTerminals._await_reply to receive the answer, and RedisTerminals._clear_op to remove Redis keys and blobs.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 438–446)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: Turns a TerminalOp into the field dictionary stored in the Redis operation stream. Redis stream entries are simple field-value records, so the operation object must be flattened.

**Data flow**: It receives a TerminalOp and returns a dictionary containing its id, kind, timeout, name, argument, and parameters as stream-friendly values. It does not write to Redis itself.

**Call relations**: RedisTerminals._run_op calls this right before adding an operation to the Redis stream. RedisTerminals._decode_op later performs the reverse step on the serving side.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 448–456)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: Turns Redis stream fields back into a TerminalOp object. This gives the serving side a normal operation object to render or execute.

**Data flow**: It receives a dictionary of stream fields, converts values to text with _text, turns the timeout into an integer, fills missing optional fields with empty strings, and returns a TerminalOp.

**Call relations**: RedisTerminals.next_op calls this after it has claimed an operation from Redis. It is the counterpart to RedisTerminals._op_fields.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 458–481)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: Waits for the reply to one operation, but only until that operation's deadline. This prevents a workflow from getting stuck forever if the terminal disappears.

**Data flow**: It receives an operation id, a deadline budget, and the user-facing timeout. It repeatedly blocks on the operation's reply stream for short intervals, checks the remaining time, and when an entry arrives passes its fields to RedisTerminals._decode_reply. It returns reply bytes or raises TerminalGone.

**Call relations**: RedisTerminals._run_op calls this after posting the operation. It uses RedisTerminals._reply_stream and RedisTerminals._client to read Redis, _stream_entries to interpret Redis's response, and RedisTerminals._decode_reply to turn the entry into bytes or an error.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 483–496)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: Interprets a reply stream entry. It distinguishes failed operations, large blob-backed replies, and small inline replies.

**Data flow**: It receives an operation id and reply fields. If the fields contain a failure message, it raises TerminalOpFailed. If they point to a blob, it reads that blob and returns its bytes; otherwise it base64-decodes the inline reply and returns the bytes.

**Call relations**: RedisTerminals._await_reply calls this once a reply entry appears. It uses RedisTerminals._reply_blob for large replies and _text for safe field decoding.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 498–529)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next operation that should be delivered to the held terminal stream. It claims an operation atomically so reconnects or duplicate streams do not run the same operation twice.

**Data flow**: It receives a conversation id and optionally an operation id to skip. It runs a Redis Lua script that scans the operation stream, removes expired entries, skips already delivered operations, marks one as delivered, and returns it. If none is ready, it blocks for new stream data and tries again.

**Call relations**: This is the serving-side partner to RedisTerminals.send. It reads from the stream written by RedisTerminals._run_op, uses _pairs and RedisTerminals._decode_op to build a TerminalOp, and raises TerminalGone if Redis cannot be reached.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 531–546)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Fetches the staged input body for an in-flight operation, if the requester is allowed to see it. This lets any pod serve the bytes because the body lives in the shared blob store.

**Data flow**: It receives the conversation id, operation id, and optional member id. It reads operation metadata from Redis, checks it with RedisTerminals._gate_ok, then reads the body blob. If metadata is missing, the gate fails, the blob is missing, or the read times out, it returns None.

**Call relations**: This is used by the serving side when it needs the request body for an operation. It relies on RedisTerminals._opmeta_key for the gate data and RedisTerminals._body_blob for the shared bytes.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 548–563)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts a terminal operation's reply and schedules delivery to Redis without making the HTTP route wait. It reports success immediately because the sender's own wait will decide whether the reply arrived in time.

**Data flow**: It receives the conversation id, operation id, reply bytes, optional failure text, and optional member id. It creates a background task for RedisTerminals._deliver_reply on the current event loop and returns True right away.

**Call relations**: This is the serving-side reply entry point. It uses RedisTerminals._spawn to run RedisTerminals._deliver_reply safely in the background.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 565–594)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: Writes an operation reply into Redis, storing large replies in the blob store when needed. It also checks that the reply belongs to the right conversation and member before accepting it.

**Data flow**: It receives conversation and operation ids, reply bytes, optional failure text, and optional member id. It reads operation metadata, rejects missing or mismatched metadata with a warning, then writes either a failure field, a blob marker after storing the bytes, or a base64 inline reply to the reply stream and sets that stream's expiration.

**Call relations**: RedisTerminals.resolve schedules this through RedisTerminals._spawn. The waiting sender is in RedisTerminals._await_reply, which will read the reply stream written here.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 596–607)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a staged-body or reply request matches the operation's conversation and member. It fails closed, meaning unclear or mismatched requests are refused rather than trusted.

**Data flow**: It receives raw metadata, a conversation id, and an optional member id. It parses the metadata JSON, compares the stored conversation to the requested one, and if a member was supplied checks it against the stored member. It returns True only when the request is allowed.

**Call relations**: RedisTerminals.staged uses this before serving input bytes. RedisTerminals._deliver_reply uses it before accepting a reply.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 609–612)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports that this cross-pod transport has no local view of the operation currently awaited by another pod. It returns None rather than pretending to know partial state.

**Data flow**: It receives a conversation id but does not inspect Redis or local state. It always returns None.

**Call relations**: This likely satisfies the same transport interface as an in-process terminal implementation. In this Redis-backed version, the real in-flight state is distributed through Redis keys and streams, not a local object.


##### `RedisTerminals._clear_op`  (lines 614–635)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: Cleans up the Redis keys, stream entry, and blob-store objects belonging to a finished or timed-out operation. The safety of "do not run twice" comes from delivery markers and time windows; this method is tidy cleanup.

**Data flow**: It receives the conversation id, operation id, and optional Redis stream entry id. It tries to delete the operation stream entry, metadata, delivery marker, inflight binding, reply stream, request body blob, and reply blob. Redis and blob-store delete failures are suppressed or bounded by timeouts.

**Call relations**: RedisTerminals._run_op calls this in a finally block after waiting for the reply. It uses the various key-building helpers so all operation-owned state is removed consistently.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 637–644)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: Starts a background task and keeps a reference to it until it finishes. This prevents a fire-and-forget reply delivery from being lost silently.

**Data flow**: It receives a coroutine and an event loop. It wraps the coroutine with RedisTerminals._logged, schedules it as a task, stores the task in a set, and arranges for the task to remove itself from the set when done.

**Call relations**: RedisTerminals.resolve uses this to schedule RedisTerminals._deliver_reply. It hands errors to RedisTerminals._logged instead of letting them disappear.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 646–650)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: Runs a background coroutine and logs a warning if it fails. This makes asynchronous delivery failures visible to operators.

**Data flow**: It receives a coroutine, awaits it, and if any exception is raised, records a warning with the error text. It returns no value.

**Call relations**: RedisTerminals._spawn wraps background work with this helper. In this file, that background work is the reply delivery started by RedisTerminals.resolve.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).


### `core/src/ufo/harness/sandbox/terminal.py`

`io_transport` · `request handling and tool execution`

Normally a sandbox is something the server can contact directly, like a container. A terminal-bound sandbox is different: the files and commands live on the member's own machine, so the server must ask the connected UFO client to do each action. This file is the meeting point for those two sides.

The important problem is timing. The workflow that wants to run a command and the web connection held by the user's client run on different event loops, often in different threads. Also, the client connection naturally drops and reconnects between holds. If the server forgot an operation during that gap, a tool call could wait forever. This file keeps one shared “mailbox” per conversation so the operation survives reconnects.

`Terminals` is the in-process rendezvous. It records which terminal is connected, queues one operation at a time, wakes the waiting side safely, stores staged bytes for file writes, and accepts replies. `TerminalCarrier` presents that rendezvous as a normal sandbox carrier: create a handle, run commands, read and write files, perform file-browser operations, and reject network dialing because a local terminal has no exposed container ports.

A key safety detail is path rewriting. Tools speak in `/workspace/...` paths, but the user's machine has a real directory. This file rewrites only that leading workspace prefix, so ordinary folder names are not accidentally changed.

#### Function details

##### `TerminalTransport.connect`  (lines 250–256)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Defines the transport operation for announcing that a user's terminal connection is now available for a conversation. Implementations use it to remember where that terminal is standing and who owns it.

**Data flow**: It receives a conversation id, current working directory, optional member id, and optional runtime id. An implementation records that information so later sandbox work can be routed to the right connected terminal. It returns nothing, but changes the transport's view of connected terminals.

**Call relations**: This is part of the `TerminalTransport` protocol, which means `TerminalCarrier` can use either the local `Terminals` implementation or another backend with the same behavior. Surface routes call this when a client stream connects.


##### `TerminalTransport.disconnect`  (lines 258–258)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Defines the transport operation for saying that a terminal connection has gone away. Implementations use it to stop treating that connection as currently available.

**Data flow**: It receives a conversation id. An implementation reduces or clears the recorded connection state for that conversation. It produces no value.

**Call relations**: This protocol method is called from the serving side when the held client stream ends. It balances earlier calls to `connect`.


##### `TerminalTransport.workspace`  (lines 260–260)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Defines a quick lookup for the currently bound terminal workspace, if one is known. It lets callers ask, “Where is this conversation's terminal?”

**Data flow**: It receives a conversation id and reads the transport's stored binding. It returns a `TerminalWorkspace` with the directory, member, and runtime id, or `None` if no terminal is known.

**Call relations**: This is a protocol hook for implementations. The local `Terminals.workspace` provides the in-memory version.


##### `TerminalTransport.arrived`  (lines 262–262)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Defines how to wait for a terminal to appear. This matters because the user's client may reconnect shortly after a tool call starts.

**Data flow**: It receives a conversation id and a grace period in seconds. An implementation checks for an existing terminal, or waits up to that grace period for one to connect. It returns workspace information or `None` if nothing arrives.

**Call relations**: The carrier uses this before creating or sending work to a terminal. The local implementation is `Terminals.arrived`.


##### `TerminalTransport.send`  (lines 264–273)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Defines how the server asks the connected terminal to perform one operation and waits for the answer. This is the main request-and-reply channel for terminal sandboxes.

**Data flow**: It receives a conversation id, operation kind, timeout, optional operation name, path or argument, JSON parameters, and optional staged bytes. The implementation delivers that request to the client and waits for the reply bytes. It returns the reply or raises an error if the terminal is absent, times out, or reports failure.

**Call relations**: All higher-level terminal sandbox actions, such as command execution and file reads, are built on this protocol method. `TerminalCarrier` calls it through its `terminals` field.


##### `TerminalTransport.next_op`  (lines 275–277)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Defines how the connected client asks, “What operation should I run next?” It is the terminal side of the rendezvous.

**Data flow**: It receives a conversation id and optionally an operation id to avoid repeating. The implementation waits until there is work for that conversation and returns a `TerminalOp` describing it.

**Call relations**: Surface routes serving the held client stream use this while `send` waits on the workflow side. Together they form the two halves of a terminal operation.


##### `TerminalTransport.staged`  (lines 279–281)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Defines how the client fetches bytes that were staged for an operation, especially file writes. Large content does not travel inside the small operation directive.

**Data flow**: It receives a conversation id, operation id, and optional member id. The implementation checks that this exact operation is still current and that the member is allowed, then returns the staged bytes or `None`.

**Call relations**: This supports write-like operations created by `TerminalCarrier.write` through `send`. The client calls it after receiving the operation directive.


##### `TerminalTransport.resolve`  (lines 283–290)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Defines how the client answers an operation after running it. It can carry either normal reply bytes or a failure message.

**Data flow**: It receives a conversation id, operation id, reply bytes, optional failure text, and optional member id. The implementation matches this answer to the currently waiting operation and wakes the sender. It returns `True` if the answer was accepted, otherwise `False`.

**Call relations**: This is the counterpart to `send`. Surface routes call it when the client posts an operation result.


##### `TerminalTransport.in_flight`  (lines 292–292)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Defines a way to inspect the operation currently waiting for a reply. It is mainly useful for tests or operator visibility.

**Data flow**: It receives a conversation id and reads the transport state. It returns the current `TerminalOp`, or `None` if no operation is in progress.

**Call relations**: This is a protocol convenience. The local implementation is `Terminals.in_flight`.


##### `_wake`  (lines 295–304)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: Safely wakes an `asyncio` future from another thread. A future is a promise for a later result, and this helper makes sure the promise is completed on the event loop that owns it.

**Data flow**: It receives a waiter, which is a future plus its event loop, and an answer object. It schedules a small setter function on that loop. Nothing is returned, but the waiting task will later receive the answer.

**Call relations**: `Terminals.connect`, `Terminals.send`, and `Terminals.resolve` call this whenever one side of the rendezvous needs to wake the other side. It is the thread-safe doorbell for this file.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 300–302)

```
def _set() -> None
```

**Purpose**: Completes the waiting future if it has not already been completed. This avoids trying to answer the same promise twice.

**Data flow**: It closes over the future and answer from `_wake`. When the owning event loop runs it, it checks whether the future is still pending and then stores the answer in it. It returns nothing.

**Call relations**: This tiny inner function is scheduled by `_wake` through the event loop's thread-safe callback mechanism.


##### `Terminals.connect`  (lines 320–342)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that a client terminal is connected for a conversation. It also wakes any work that was waiting for the terminal to arrive.

**Data flow**: It receives a conversation id, the terminal's current directory, the member id, and optional runtime id. Under a lock, it creates or updates the conversation slot, increments the connection count, and gathers any arrival waiters. After releasing the lock, it wakes those waiters.

**Call relations**: Surface connection code calls this when a held client stream starts. It uses `_wake` to notify `Terminals.arrived`, which may be waiting inside `Terminals.send` or carrier setup.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 344–351)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Marks one terminal connection as closed. If no operation is waiting and no connections remain, it removes the conversation slot.

**Data flow**: It receives a conversation id. Under the lock, it finds the slot, decreases its connection count, and deletes the slot if it is idle and empty. It returns nothing.

**Call relations**: Surface code calls this when a client stream ends. The state may stay alive if an operation is still waiting, so a reconnect can finish the work.


##### `Terminals.workspace`  (lines 353–360)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the currently known workspace for a connected terminal. It is a simple snapshot of where the terminal is bound.

**Data flow**: It receives a conversation id and reads the matching slot under the lock. If found, it creates a `TerminalWorkspace` containing directory, member id, and runtime id. If not found, it returns `None`.

**Call relations**: This implements the `TerminalTransport.workspace` protocol for the in-memory transport.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 362–386)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits for a terminal to be connected, but only up to a chosen grace period. This prevents normal reconnect gaps from looking like permanent absence.

**Data flow**: It receives a conversation id and a number of seconds to wait. It first checks for an existing slot; if none exists, it registers a future as an arrival waiter and waits until either `connect` wakes it or time runs out. It returns a `TerminalWorkspace` or `None`.

**Call relations**: `Terminals.send` calls this before sending an operation, and `TerminalCarrier` uses the transport version during create and attach. If the wait expires, it calls `Terminals._drop_arrival` to remove its unused waiter.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 388–393)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: Removes an arrival waiter that timed out. This keeps old waiting promises from piling up after callers have stopped caring.

**Data flow**: It receives a conversation id and the future to remove. Under the lock, it filters that future out of the waiting list and deletes the list if it becomes empty. It returns nothing.

**Call relations**: `Terminals.arrived` calls this whenever its grace period ends before a terminal connects.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 395–470)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one operation to a conversation's terminal and waits for the client's reply. It is the core server-side half of terminal execution.

**Data flow**: It receives the operation details and optional body bytes. It waits for a terminal to be present, waits for its turn because the terminal runs one operation at a time, creates a `TerminalOp`, stores it in the slot, wakes any connected watcher, and then waits for a reply. It returns reply bytes, or raises an error if the terminal disappears, times out, or reports failure. Before finishing, it clears the operation and wakes queued senders.

**Call relations**: `TerminalCarrier` methods call this through the `TerminalTransport` interface for exec, read, write, file operations, and skill loading. It calls `Terminals.arrived`, `Terminals._take_turn`, and `_wake` to coordinate with reconnecting client streams.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 6 external calls (__init__, __init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 472–503)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: Waits until this sender owns the terminal's single operation slot. It serializes operations so two callers do not ask the same client to run two things at once.

**Data flow**: It receives the conversation id, the caller's event loop, and this operation's timeout. If the slot is free, it marks it busy and returns. If another operation is running, it queues a future and waits until released or until the deadline passes. On timeout, it removes its ticket and raises an error.

**Call relations**: `Terminals.send` calls this before installing a new `TerminalOp`. When `send` finishes, it wakes queued waiters so they can compete for the next turn.

*Call graph*: called by 1 (send); 6 external calls (__init__, __init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 505–530)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Lets the connected client wait for the next operation it should run. This is the client-facing half of the rendezvous.

**Data flow**: It receives a conversation id and optionally an operation id to exclude. If an undelivered operation is already waiting and is not excluded, it marks it delivered and returns it. Otherwise it stores a watcher future and waits until `send` wakes it with a new `TerminalOp`.

**Call relations**: Surface routes call this while holding the client's connection. It pairs with `Terminals.send`, which creates the operation and wakes the watcher.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 532–544)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Returns bytes staged for the currently in-flight operation, such as file content being copied into the terminal workspace. It only exposes bytes to the right operation and member.

**Data flow**: It receives a conversation id, operation id, and optional member id. Under the lock, it checks that the slot and operation still match, and that the member is allowed if a member id was provided. It returns the staged body bytes or `None`.

**Call relations**: The client uses this after receiving an operation that needs extra bytes. `TerminalCarrier.write` causes those bytes to be stored by calling `send` with a body.


##### `Terminals.in_flight`  (lines 546–551)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports the operation currently waiting for a client reply. This is useful for tests or debugging.

**Data flow**: It receives a conversation id and reads the slot under the lock. It returns the current `TerminalOp`, or `None` if there is no slot or no operation.

**Call relations**: This implements the inspection method from `TerminalTransport` for the local in-memory transport.


##### `Terminals.resolve`  (lines 553–582)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts the client's answer for an in-flight operation. It wakes the original sender if the answer matches the current operation.

**Data flow**: It receives the conversation id, operation id, reply bytes, optional failure text, and optional member id. Under the lock, it verifies that the operation is still current, unresolved, and authorized. If valid, it marks it resolved and wakes the waiting sender with either bytes or a `TerminalOpFailed` object. It returns `True` when accepted and `False` for stale, wrong, or unauthorized replies.

**Call relations**: Surface routes call this when the client posts a result. It uses `_wake` to deliver the answer back to the `Terminals.send` call that is waiting in the workflow.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 598–645)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a sandbox handle for a terminal-bound workspace. It verifies that the user's connected terminal is present and standing in the expected directory.

**Data flow**: It receives a `SandboxSpec` describing the conversation, workspace, proxy, token, and environment. It waits for the terminal binding, checks that its directory matches the requested workspace, builds proxy environment variables, and returns a `SandboxHandle`. It raises a terminal absence or mismatch error if the binding is not usable.

**Call relations**: The sandbox system calls this when it wants to open a terminal-backed sandbox for a turn. Later `TerminalCarrier` methods use the returned handle to run operations through the same terminal transport.

*Call graph*: 4 external calls (__init__, __init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 647–662)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reattaches to an already bound terminal outside the main turn, such as for file browsing or background writes. It only succeeds if the current binding matches the requested workspace.

**Data flow**: It receives a `SandboxSpec`, checks for an arrived terminal without waiting, compares the bound directory with the resume id, and returns a lightweight `SandboxHandle` if they match. If no matching terminal is present, it returns `None`.

**Call relations**: Off-turn features call this to reuse a member's terminal. It uses the transport's arrival lookup so the binding can be discovered through whatever transport implementation is active.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 664–690)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs a command on the member's own machine through the connected client. It rewrites logical `/workspace` paths to the real directory before sending the command.

**Data flow**: It receives a sandbox handle, command arguments, timeout, and optional model-generated command text for safety classification. It finds the workspace root, rewrites arguments for the host machine, builds optional safety arguments, and delegates to `_exec`. It returns an `ExecResult` with output, error text, exit code, and timeout information.

**Call relations**: Higher-level sandbox users call this as they would on any carrier. It calls `_root`, the shared `host_argv` path helper, and then `TerminalCarrier._exec` to send the actual operation.

*Call graph*: calls 2 internal fn (_exec, _root); 1 external calls (host_argv).


##### `TerminalCarrier.load_skills`  (lines 692–703)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asks the connected client to load skills into its UFO home directory. Skills are extra capabilities the client can make available during a run.

**Data flow**: It receives a sandbox handle and a JSON-like payload. It serializes the payload, sends a skills operation through the terminal transport, and wraps the reply text as a successful `ExecResult`. If the terminal reports failure, it raises a runtime error.

**Call relations**: This is a carrier operation built directly on `terminals.send`. It does not go through shell execution because the client has a native skills operation.

*Call graph*: 2 external calls (__init__, dumps).


##### `TerminalCarrier._exec`  (lines 705–745)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, safety_argv: tuple[str, ...] | None=None) -> ExecResult
```

**Purpose**: Runs command arguments that are already expressed as real host paths. It is the lower-level execution helper used when paths must not be rewritten again.

**Data flow**: It receives a handle, final command arguments, timeout, and optional safety arguments. It serializes the command and environment to JSON, sends an exec operation, parses the JSON reply, decodes base64-encoded stdout and stderr, normalizes timeout exit codes, and returns an `ExecResult`.

**Call relations**: `TerminalCarrier.exec` calls this for normal commands after path rewriting. `TerminalCarrier._enumerate` also calls it for generated shell snippets that enumerate files before file operations.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (_enumerate, exec); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 747–760)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file in the terminal workspace. The file content is staged separately instead of being embedded in the small operation directive.

**Data flow**: It receives a handle, logical path, and content bytes. It maps the logical path to the user's real workspace path and sends a write operation with the bytes as the staged body. It returns nothing if successful, or raises an `OSError` if the client reports failure.

**Call relations**: Sandbox file-writing callers use this just like they would on a container carrier. It calls `_client_path` for path mapping and relies on `Terminals.staged` on the client side to serve the bytes.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 762–778)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a file from the terminal workspace and yields it in chunks. It presents terminal file reads in the same streaming shape as other sandbox carriers.

**Data flow**: It receives a handle and logical path. It maps the path, sends a read operation, and receives the whole reply body as bytes. It then yields slices of that body up to one megabyte each. Missing files become `FileNotFoundError`; other terminal failures become `OSError`.

**Call relations**: File download or tool code calls this through the sandbox interface. It calls `_client_path` before asking the terminal transport to read.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 780–868)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level `ufo fs` file operation, such as read, grep, glob, or changes, against the user's terminal workspace. It sends operation names and parameters, not executable server code.

**Data flow**: It receives a handle, operation name, and parameter dictionary. It rewrites only parameters that are known to be workspace paths. For supported office document reads, it can fetch the document bytes and ask a document renderer to extract pages. For walk-like operations, it first runs a controlled enumeration command and then sends the file operation with the generated listing name. It parses the JSON reply and returns the result dictionary, or raises a clear error if the client reports one.

**Call relations**: This is the main bridge between UFO's file tools and the terminal client. It calls `_root`, `_under_root`, `_enumerate`, and `_reply_object`, and uses `terminals.send` for the final native client operation.

*Call graph*: calls 4 internal fn (_enumerate, _reply_object, _root, _under_root); 2 external calls (dumps, PurePosixPath).


##### `TerminalCarrier._enumerate`  (lines 870–891)

```
async def _enumerate(self, handle: SandboxHandle, op: str, walk_root: str, program: str, arguments: tuple[str, ...]=()) -> None
```

**Purpose**: Runs a small shell program that lists the files or repositories a later file operation should inspect. This makes the server, not the client, decide exactly what gets walked.

**Data flow**: It receives a handle, operation name, walk root, shell program text, and optional arguments. It builds a shell command with `UFO_WALK_ROOT` set safely, runs it through `_exec`, and checks the exit code. It returns nothing on acceptable results, or raises a `ValueError` if enumeration failed.

**Call relations**: `TerminalCarrier.file_op` calls this before grep, glob, and changes operations. It delegates execution to `TerminalCarrier._exec` because these commands already use concrete host paths.

*Call graph*: calls 1 internal fn (_exec); called by 1 (file_op); 1 external calls (quote).


##### `TerminalCarrier.dial`  (lines 893–897)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Rejects attempts to dial a network port on a terminal-bound sandbox. A user's local terminal does not expose container-style per-port network addresses.

**Data flow**: It receives a sandbox handle and port number. It does not inspect or connect to the port. It raises `SandboxUnreachable` with guidance to use a remote carrier for services that need dialing.

**Call relations**: Code that expects all sandbox carriers to offer a dial method may call this. This carrier deliberately fails because terminal sandboxes are local-process based, not network-container based.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 900–908)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: Extracts and decodes one captured command stream from an exec reply. The terminal client sends stdout and stderr as base64 text, which is a safe text form for arbitrary bytes.

**Data flow**: It receives a parsed reply dictionary and a stream name such as `stdout` or `stderr`. It looks for the matching `<name>_b64` field, decodes it from base64, and returns raw bytes. Missing or malformed data raises an error instead of pretending the stream was empty.

**Call relations**: `TerminalCarrier._exec` calls this after `_reply_object` parses the client's exec reply.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 911–918)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: Parses a terminal reply as a JSON object. It protects callers from treating malformed or wrongly shaped client replies as valid results.

**Data flow**: It receives raw reply bytes and the operation name for error messages. It decodes the bytes as UTF-8, parses JSON, checks that the parsed value is a dictionary, and returns it. Invalid JSON or a non-object result raises a runtime error.

**Call relations**: `TerminalCarrier._exec` uses this for command replies, and `TerminalCarrier.file_op` uses it for file operation replies.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 921–924)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: Returns the real host directory that backs `/workspace` for a terminal sandbox. It also checks that such a directory exists on the handle.

**Data flow**: It receives a `SandboxHandle`. If the handle has a `workspace_host_path`, it returns that string. If not, it raises an error because terminal sandboxes must be backed by a bound directory.

**Call relations**: `TerminalCarrier.exec`, `TerminalCarrier.file_op`, and `_client_path` call this before rewriting workspace paths.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 927–933)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: Maps a logical sandbox path to the user's real terminal path. It rewrites `/workspace/...` to the bound directory and leaves other paths alone.

**Data flow**: It receives a sandbox handle and a path string. It gets the root directory with `_root`, then calls `_under_root` to apply the anchored workspace-prefix rewrite. It returns the path the client should use on the user's machine.

**Call relations**: `TerminalCarrier.write` and `TerminalCarrier.read` call this before sending file operations to the terminal.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 936–941)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: Performs the actual anchored `/workspace` path rewrite. It changes only a leading workspace prefix, not every word named “workspace” inside the path.

**Data flow**: It receives the real root directory and a path string. If the path is not under `/workspace`, it returns the original path. If it is exactly `/workspace`, it returns the root; otherwise it appends the relative remainder to the root and returns that new path.

**Call relations**: `_client_path` uses this for read and write paths, and `TerminalCarrier.file_op` uses it for selected file-operation parameters.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### Sandbox session contract
The shared session abstraction, execution environment setup, and protocol wrapper that let tools use any carrier consistently.

### `core/src/ufo/harness/sandbox/session.py`

`domain_logic` · `cross-cutting during sandbox creation, command execution, file access, skill loading, and teardown`

A sandbox is like a rented workshop for one conversation. The agent can run commands there and read or write files under `/workspace`, but it should not be able to reach private records such as transcripts or stored compaction data. This file defines that boundary.

It does three big jobs. First, it describes the carrier interface: the small set of actions any sandbox backend must provide, such as create a sandbox, run a command, write a file, read a file, or expose a port. Second, it provides the common `Sandbox` object that tools use. Tools do not need to know whether the sandbox already exists or will be created on first use; they call the same methods either way. Third, it protects paths, tokens, and network access. It signs short-lived run and probe tokens, builds proxy environment variables so outbound traffic is metered, and normalizes paths so tool-supplied names cannot escape the allowed workspace or runtime folders.

The file also contains careful skill-loading support. Skills are staged, checked by cryptographic digest, and installed into the sandbox runtime only if their contents match what was declared. Without this file, each tool and backend would need to reinvent sandbox access, and small mistakes could let commands read the wrong files, bypass the proxy, or run with the wrong authority.

#### Function details

##### `egress_proxy_env`  (lines 368–407)

```
def egress_proxy_env(proxy: 'ProxyEndpoint', run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside an off-cluster sandbox send web traffic through UFO's egress proxy. This matters because the proxy meters traffic, applies authorization, and can replace sentinel API keys with real keys only on approved requests.

**Data flow**: It receives a proxy endpoint and a signed run token. It checks that the proxy has a public HTTPS URL, turns that URL into standard proxy variables such as `HTTP_PROXY`, adds loopback exceptions and certificate settings, and returns a dictionary ready to pass into a sandbox command. If the proxy URL is missing or unsafe, it raises an error instead of silently creating an unmetered sandbox.

**Call relations**: It is used when a sandbox command needs outbound network access through the public proxy. It relies on URL parsing to validate and format the proxy address, then hands the resulting environment to carrier-specific execution code.

*Call graph*: 1 external calls (urlsplit).


##### `_basic_username`  (lines 413–419)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username part from a `Basic` proxy authorization header. UFO stores signed sandbox tokens in that username field because normal proxy URLs already know how to carry it.

**Data flow**: It receives an authorization header string, verifies that it uses Basic authentication, decodes the base64 value, and returns the text before the first colon. Bad or non-Basic headers become a `ValueError`.

**Call relations**: Both run-token and probe-token decoders call this first. After it pulls out the username, those decoders verify the signature and interpret the token contents.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 438–442)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Creates a run-token signer/verifier from the deployment secret stored in the environment. The server needs this so only this deployment can mint valid sandbox proxy tokens.

**Data flow**: It reads the token-secret environment variable, encodes it as bytes, and returns a `RunTokenCodec`. If the secret is absent, it stops startup with a clear error.

**Call relations**: Server startup calls this while preparing the sandbox and proxy system. Later, the resulting codec is used to create and read per-turn run tokens.

*Call graph*: called by 1 (run).


##### `RunTokenCodec.encode`  (lines 444–448)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a turn's identity and authority into a signed token suitable for proxy authentication. The token lets the proxy attribute outbound sandbox traffic to the correct workspace, turn, and member authority.

**Data flow**: It receives a `RunToken`, converts the authority into an optional member id, builds a compact text payload, signs it with the codec secret, and returns the signed string.

**Call relations**: The sandbox-opening flow calls this when preparing a command's proxy credentials. The proxy later feeds the presented token back through `RunTokenCodec.from_proxy_auth`.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (sign_token, authority_member_id).


##### `RunTokenCodec.from_proxy_auth`  (lines 450–462)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Verifies a proxy authorization header and turns it back into a trusted `RunToken`. This is how the proxy decides which workspace and turn a sandbox request belongs to.

**Data flow**: It extracts the Basic-auth username, verifies the signature with the deployment secret, checks that the token is in the run-token domain, parses UUIDs and authority, and returns a `RunToken`. Invalid signatures, malformed text, or wrong token kinds become a `ValueError`.

**Call relations**: The proxy side uses this after `_basic_username` extracts the token. It mirrors `RunTokenCodec.encode`, refusing probe tokens or forged strings.

*Call graph*: calls 1 internal fn (_basic_username); 4 external calls (__init__, verify_token, authority_from_member_id, UUID).


##### `ProbeTokenCodec.encode`  (lines 495–502)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Creates a signed token for a probe command, which is an off-turn sandbox command with its own expiry time. This allows limited network access for background checks that do not belong to an active turn row.

**Data flow**: It receives a `ProbeToken`, writes its workspace, conversation, probe id, authority, and expiry into a text payload, signs that payload, and returns the token string.

**Call relations**: Probe execution code can place this token in proxy credentials. The proxy later validates it through `ProbeTokenCodec.from_proxy_auth`.

*Call graph*: 2 external calls (sign_token, authority_member_id).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 504–520)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Verifies a probe proxy authorization header and recovers the probe identity. This lets the proxy decide whether a probe request is still valid and what authority it carries.

**Data flow**: It extracts the Basic-auth username, verifies the signed payload, checks that it is a probe token, parses the IDs and expiry, and returns a `ProbeToken`. Anything malformed or signed for the wrong purpose is rejected.

**Call relations**: This is the decoding counterpart to `ProbeTokenCodec.encode`. It shares `_basic_username` with run-token decoding but checks a separate token domain so token types cannot be swapped.

*Call graph*: calls 1 internal fn (_basic_username); 4 external calls (__init__, verify_token, authority_from_member_id, UUID).


##### `sandbox_handle_id`  (lines 608–613)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the backend-specific sandbox id out of a stored handle only if it belongs to the expected backend. This prevents one carrier from accidentally trying to resume another carrier's sandbox.

**Data flow**: It receives a backend name and a stored handle string. If the handle starts with `<backend>:`, it returns the part after the colon; otherwise it returns `None`.

**Call relations**: Carrier resume logic uses this when reading a saved conversation handle. It helps deployment changes stay safe when different sandbox backends may have written different handle formats.


##### `sandbox_handle_backend`  (lines 616–619)

```
def sandbox_handle_backend(value: str) -> str
```

**Purpose**: Reads the backend name from a stored sandbox handle. The system uses this to know which carrier owns a saved sandbox reference.

**Data flow**: It receives a string shaped like `<backend>:<id>` and returns the text before the first separator.

**Call relations**: Routing or resume code can use this before asking a carrier to attach. It pairs with `sandbox_handle_id`, which checks whether a specific backend should accept the handle.


##### `Carrier.create`  (lines 661–661)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the required operation for creating or attaching to a writable sandbox for a conversation. A real carrier implements this for Docker, remote sandboxes, local execution, or another backend.

**Data flow**: The caller provides a `SandboxSpec` describing conversation id, image, workspace path, proxy, environment, size, and turn. The implementation returns a `SandboxHandle` that future calls use to run commands or access files.

**Call relations**: This is part of the carrier contract. Higher-level sandbox-opening code calls it, while `SandboxSession` later uses the returned handle for all concrete operations.


##### `Carrier.attach`  (lines 663–669)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines a non-creating attach operation for reading an existing sandbox if it is still reachable. It is important because browsing files should not accidentally create a new sandbox.

**Data flow**: The caller provides a `SandboxSpec`, usually with a resume id. The implementation returns a `SandboxHandle` if that sandbox exists and can be reached, or `None` if not.

**Call relations**: Read-only flows and late stop logic can use this to look for an existing sandbox. It deliberately differs from `create`, which may provision a fresh container.


##### `Carrier.exec`  (lines 671–684)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Defines how a backend runs a command inside the sandbox. It also carries an optional marker showing which command text came from the model, for carriers that must enforce that distinction.

**Data flow**: It receives a sandbox handle, an argument vector, a timeout, and optionally the model-authored command text. The implementation runs the command and returns stdout, stderr, exit code, and timeout information in an `ExecResult`.

**Call relations**: Nearly every sandbox command path eventually passes through this method, often via `SandboxCommands`. Concrete carriers provide the actual transport to Docker, local shell, or a remote provider.


##### `Carrier.write`  (lines 686–698)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how bytes are written into the sandbox filesystem. The write must stay inside allowed areas and must not rely on unsafe shell redirection.

**Data flow**: It receives a handle, an absolute sandbox path, and bytes. The implementation writes the content, creating parents when allowed, and either completes or raises an error for refused or failed writes.

**Call relations**: High-level methods such as `Sandbox.write_file`, runtime-file writing, and skill staging call this. Each carrier chooses the safest way to move bytes into its own environment.


##### `Carrier.read`  (lines 700–711)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how a file is streamed out of the sandbox in chunks. Streaming avoids loading large files entirely into the server process.

**Data flow**: It receives a handle and an absolute sandbox path. The implementation yields byte chunks asynchronously or raises an error if the file is missing, not readable, or outside allowed scope.

**Call relations**: Sandbox read helpers call this after path scoping. Concrete carriers implement the transport, such as filesystem APIs, stdout streams, or local file reads.


##### `Carrier.dial`  (lines 713–722)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how code outside the sandbox can reach a service listening on a sandbox port. This is needed for things like browser debugging ports or preview web servers started inside the sandbox.

**Data flow**: It receives a handle and port number. The implementation returns a `DialTarget` with host, TLS choice, and any required headers, or raises `SandboxUnreachable` if no route exists.

**Call relations**: Higher-level browser or preview features call `Sandbox.dial`, which delegates here. Each carrier knows how its own ports are exposed.


##### `Carrier.file_op`  (lines 724–734)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Defines the backend operation used by file tools for bounded reads, writes, edits, globs, greps, and change listings. Running the operation inside the sandbox avoids pulling whole files across the boundary.

**Data flow**: It receives a handle, an operation name, and JSON-like parameters. The implementation returns a parsed dictionary result, raises `ValueError` for recoverable tool errors, or raises another error for unexpected failures.

**Call relations**: The common `Sandbox.run_ufo_fs` method scopes paths and then calls this. Carriers may implement it directly or reuse `ufo_fs_file_op`.


##### `CommandStopping.stop_commands`  (lines 754–754)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines an optional operation for carriers that can stop commands still running after their launch call was cancelled. This matters for remote systems where canceling the wait does not necessarily kill the process.

**Data flow**: It receives a sandbox handle, including the turn id to stop. The implementation stops only commands belonging to that turn and returns nothing.

**Call relations**: `Sandbox.stop_commands` and `_LateSandbox.stop_commands` call this only when the carrier supports the protocol. Carriers without long-lived commands do not implement it.


##### `SkillLoading.load_skills`  (lines 761–763)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Defines an optional native skill-loading operation for carriers that can install skills through their own runtime support. It avoids using the generic staged Python installer when the backend has a better path.

**Data flow**: It receives a handle and a skill payload describing system and user skills. The implementation loads them and returns an `ExecResult` whose stdout should describe installed roots.

**Call relations**: `Sandbox.load_skills` checks for this protocol first. If the carrier does not support it, the sandbox falls back to staged skill loading.


##### `SkillExecuting.exec_skill`  (lines 770–772)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines an optional privileged execution path for skill installation programs. Some skill setup must run outside the normal unprivileged command path.

**Data flow**: It receives a handle, command arguments, and a timeout. The implementation runs the skill-related command with the required privileges and returns an `ExecResult`.

**Call relations**: The staged skill loader and system-skill sync path call `_exec_skill`, which in turn requires this protocol. A carrier that cannot run these privileged programs cannot use the generic skill installer.


##### `SystemSkillSeeding.seed_system_skills`  (lines 779–779)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Defines an optional way for a carrier to seed system skills directly into its runtime filesystem. This is for backends whose runtime storage is prepared by the current process.

**Data flow**: It receives a system-skill archive as bytes. The implementation writes or installs those bytes into the backend's runtime area and returns nothing.

**Call relations**: Carrier setup code can use this protocol when preparing a sandbox backend. It sits below the higher-level skill loading done by `Sandbox.load_skills`.


##### `ufo_fs_file_op`  (lines 782–795)

```
async def ufo_fs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Provides a shared implementation of `Carrier.file_op` for sandbox images that can run UFO's file command inside the sandbox. It keeps command construction and JSON parsing consistent across carriers.

**Data flow**: It receives a carrier, handle, operation name, and parameters. It builds a `SandboxFileOperations` helper that executes through the carrier, runs the requested operation, and returns the parsed dictionary result.

**Call relations**: Concrete carriers can call this instead of duplicating the file-tool protocol. It bridges the carrier's `exec` method to the higher-level file tools.

*Call graph*: 1 external calls (__init__).


##### `host_argv`  (lines 803–815)

```
def host_argv(argv: tuple[str, ...], root: str) -> tuple[str, ...]
```

**Purpose**: Rewrites `/workspace` paths in command arguments for carriers whose workspace is actually a host directory. This lets the same logical command work when the sandbox is local or bind-mounted.

**Data flow**: It receives command arguments and the real host root. It replaces only standalone `/workspace` path segments with that root and returns a new argument tuple, leaving lookalike text such as URLs or `/workspace-old` untouched.

**Call relations**: Host-path carriers use this before executing commands. It protects unrelated strings from accidental rewriting while still mapping real workspace paths.


##### `workspace_path`  (lines 818–826)

```
def workspace_path(path: str) -> str
```

**Purpose**: Normalizes a user- or tool-supplied path so it stays under `/workspace`. This is a core safety check that stops `..` path tricks from escaping the conversation's files.

**Data flow**: It receives a path, treats relative paths as being under `/workspace`, resolves `.` and `..` parts, checks that the result remains inside `/workspace`, and returns the safe absolute path. If the path escapes, it raises `ValueError`.

**Call relations**: Most workspace file operations call this before touching files or passing paths to carriers. It uses `_resolve_parts` for the actual path-part cleanup.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 5 (_read_scoped_file, file_exists, run_ufo_fs, write_file, rooted_path); 1 external calls (PurePosixPath).


##### `runtime_relative`  (lines 829–839)

```
def runtime_relative(path: str) -> PurePosixPath
```

**Purpose**: Validates a name inside the sandbox's private runtime directory. This protects UFO-owned files, such as staged skill payloads or tool-output files, from path escapes.

**Data flow**: It receives a relative runtime path, checks it through the containment guard, confirms the normalized spelling matches the input, and returns a `PurePosixPath`. Invalid or escaping paths become `ValueError`.

**Call relations**: Runtime path builders call this before joining names onto the runtime root. It is the runtime counterpart to `workspace_path`.

*Call graph*: called by 2 (_runtime_display_path, _runtime_path); 2 external calls (PurePosixPath, contained_relative).


##### `sandbox_runtime_root`  (lines 842–844)

```
def sandbox_runtime_root(conversation_id: UUID) -> str
```

**Purpose**: Builds the private runtime directory path for one conversation inside the sandbox. This keeps UFO's internal files separate from member-visible workspace files.

**Data flow**: It receives a conversation UUID and returns a path under `$UFO_HOME/runs/` using the UUID's hex form.

**Call relations**: `_runtime_root` uses this as the default when a handle does not already carry a runtime root. Many runtime-file methods then build on that result.

*Call graph*: called by 1 (_runtime_root).


##### `shell_path`  (lines 847–852)

```
def shell_path(path: str) -> str
```

**Purpose**: Quotes a sandbox path for safe use in a shell command, while preserving `$UFO_HOME` expansion when intended. This avoids accidental shell interpretation of path characters.

**Data flow**: It receives a path string. If it starts with `$UFO_HOME/`, it keeps that environment-variable prefix expandable and quotes the rest; otherwise it shell-quotes the whole path.

**Call relations**: Callers that need to display or embed sandbox paths in shell snippets can use this helper. It relies on standard shell quoting rather than custom escaping.

*Call graph*: 1 external calls (quote).


##### `_runtime_root`  (lines 855–856)

```
def _runtime_root(handle: SandboxHandle) -> str
```

**Purpose**: Returns the runtime root recorded on a sandbox handle, or computes the default for the conversation. This gives all runtime helpers one consistent source of truth.

**Data flow**: It receives a `SandboxHandle`. If `handle.runtime_root` is set, it returns that; otherwise it builds the normal conversation runtime root.

**Call relations**: Runtime path, display path, skill staging, tool-output setup, and scoped file reading all call this before resolving runtime locations.

*Call graph*: calls 1 internal fn (sandbox_runtime_root); called by 7 (_read_scoped_file, _run_staged_skill_load, _sync_system_skills, run_ufo_fs, write_runtime_path, _runtime_display_path, _runtime_path).


##### `_runtime_path`  (lines 859–860)

```
def _runtime_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds a real sandbox path for a file under the conversation's runtime root. It validates the relative part before joining it to the root.

**Data flow**: It receives a handle and a relative runtime name. It gets the runtime root, validates the relative path with `runtime_relative`, joins them, and returns the resulting string.

**Call relations**: Runtime-file reads, writes, existence checks, skill staging, and tool-output setup use this whenever they need an actual in-sandbox runtime path.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 7 (_run_staged_skill_load, _sync_system_skills, ensure_tool_output_dir, runtime_file_exists, runtime_path, write_runtime_file, write_runtime_path); 1 external calls (PurePosixPath).


##### `_runtime_display_path`  (lines 863–867)

```
def _runtime_display_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds the user-facing `$UFO_HOME/runs/...` spelling for a runtime file. This is useful when the agent should see or reuse a path without hard-coding the sandbox's internal home directory.

**Data flow**: It receives a handle and a relative runtime name. It extracts the run directory name, validates the relative path, and returns a path beginning with `$UFO_HOME/runs/`.

**Call relations**: `Sandbox.runtime_display_path` calls this after binding to a session. It complements `_runtime_path`, which returns the actual internal path.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 1 (runtime_display_path); 1 external calls (PurePosixPath).


##### `rooted_path`  (lines 870–873)

```
def rooted_path(path: str, root: str) -> str
```

**Purpose**: Normalizes a path beneath a chosen root while still preserving that root's spelling. It is used for allowed read-only areas outside `/workspace`, such as runtime files or skills.

**Data flow**: It receives a path and the root it is supposed to be under. It temporarily maps the path into `/workspace` form for safety checking, then maps the normalized result back under the original root.

**Call relations**: `Sandbox.run_ufo_fs` and `_read_scoped_file` use this when allowing reads, globs, or greps under runtime or skill roots. It delegates the escape check to `workspace_path`.

*Call graph*: calls 1 internal fn (workspace_path); called by 2 (_read_scoped_file, run_ufo_fs).


##### `_resolve_parts`  (lines 876–885)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies path pieces by applying `.` and `..` rules while refusing to climb above the root. This is the small path-cleaning engine behind workspace scoping.

**Data flow**: It receives path parts. It skips empty and `.` parts, pops one part for `..`, and raises an error if `..` would escape the root; otherwise it returns the cleaned stack of parts.

**Call relations**: `workspace_path` calls this before checking that the normalized result remains under `/workspace`.

*Call graph*: called by 1 (workspace_path).


##### `Sandbox.conversation_id`  (lines 897–901)

```
def conversation_id(self) -> UUID
```

**Purpose**: Defines the property that tells which conversation a sandbox reference belongs to. Knowing this should not require creating the sandbox.

**Data flow**: Concrete subclasses return a UUID. The base version raises `NotImplementedError` because it is only the shared interface.

**Call relations**: `SandboxSession`, `_LateSandbox`, and `_AuthorizedSandbox` each provide the real value. Higher-level code can ask any sandbox-like object for the conversation id.


##### `Sandbox.turn_id`  (lines 904–908)

```
def turn_id(self) -> UUID | None
```

**Purpose**: Defines the property that tells which turn, if any, this sandbox reference is bound to. This is used to scope command stopping and authority.

**Data flow**: Concrete subclasses return a UUID or `None`. The base version raises `NotImplementedError`.

**Call relations**: Session and late sandbox implementations provide this property. Command-stopping logic relies on it to avoid stopping sibling turns in the same container.


##### `Sandbox.created`  (lines 911–913)

```
def created(self) -> bool
```

**Purpose**: Defines the property that says whether a real sandbox session has already been created or attached. This lets callers check without forcing creation.

**Data flow**: Concrete subclasses return a boolean. The base version raises `NotImplementedError`.

**Call relations**: `SandboxSession` always reports true, while `_LateSandbox` reports whether its lazy open has happened. Authorized wrappers forward the value.


##### `Sandbox.authorize`  (lines 915–923)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'Sandbox'
```

**Purpose**: Defines how to view the same sandbox under a specific run token and environment authority. This is needed when different turns or members share a container but must not share credentials.

**Data flow**: It accepts a new run token, a set of environment variable names to remove, and environment variables to add. Concrete implementations return another `Sandbox` object with that authority applied.

**Call relations**: `SandboxSession` rewrites its handle immediately, while `_LateSandbox` returns an `_AuthorizedSandbox` wrapper that applies the authority after the sandbox is opened.


##### `Sandbox._bound`  (lines 925–926)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Defines the internal step that turns any sandbox reference into a concrete `SandboxSession`. It hides whether the session already exists or must be opened lazily.

**Data flow**: Concrete implementations return a `SandboxSession`, possibly after creating or attaching the sandbox. The base method raises `NotImplementedError`.

**Call relations**: Nearly all high-level sandbox operations call `_bound` first. This is the seam that lets eager and lazy sandbox references share the same public methods.

*Call graph*: called by 18 (_read_file, _read_scoped_file, bash, bash_task, dial, ensure_tool_output_dir, file_exists, load_skills, python, run_ufo_fs (+8 more)).


##### `Sandbox.runtime_path`  (lines 928–930)

```
async def runtime_path(self, relative: str) -> str
```

**Purpose**: Returns the actual sandbox path for one UFO-owned runtime file. Callers use it when they need a real path inside the sandbox, not a display path.

**Data flow**: It binds to a session, resolves the relative name under that handle's runtime root, and returns the path string.

**Call relations**: It uses `_bound` to get the active handle and `_runtime_path` for safe path construction.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.runtime_display_path`  (lines 932–934)

```
async def runtime_display_path(self, relative: str) -> str
```

**Purpose**: Returns a reusable `$UFO_HOME`-based path for one runtime file. This is friendlier for commands or messages that should not expose a backend-specific absolute home path.

**Data flow**: It binds to a session, converts the relative runtime name into a display path, and returns that string.

**Call relations**: It delegates path creation to `_runtime_display_path` after `_bound` supplies the handle.

*Call graph*: calls 2 internal fn (_bound, _runtime_display_path).


##### `Sandbox.write_runtime_file`  (lines 936–939)

```
async def write_runtime_file(self, relative: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a UFO-owned runtime file outside the member workspace. This is used for internal data such as staged skill payloads or tool-output files.

**Data flow**: It receives a relative runtime path and content bytes. It binds the sandbox, converts the relative path to a safe runtime path, and asks the carrier to write the bytes.

**Call relations**: It combines `_bound`, `_runtime_path`, and the carrier's `write` operation. Unlike `write_file`, it writes under the runtime root rather than `/workspace`.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.write_runtime_path`  (lines 941–949)

```
async def write_runtime_path(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to an already-resolved path, but only if that path is inside this conversation's runtime root. This prevents callers from using an absolute path to overwrite something outside UFO's runtime area.

**Data flow**: It receives an absolute path and content. It binds the sandbox, checks that the path is below the runtime root and not the root itself, converts it back to a relative runtime name, and writes through the carrier.

**Call relations**: It uses `_runtime_root` for the boundary and `_runtime_path` for the final safe path. It is a stricter variant for callers that already hold a full runtime path.

*Call graph*: calls 3 internal fn (_bound, _runtime_path, _runtime_root); 1 external calls (PurePosixPath).


##### `Sandbox.runtime_file_exists`  (lines 951–958)

```
async def runtime_file_exists(self, relative: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the runtime area. It gives callers a safe existence test without exposing arbitrary filesystem access.

**Data flow**: It receives a relative runtime path, binds the sandbox, builds the safe target path, runs `test -f` inside the sandbox, and returns true when the command exits successfully.

**Call relations**: It uses the carrier's `exec` method directly after `_runtime_path`. It does not read file contents.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox._commands`  (lines 960–971)

```
def _commands(self, bound: 'SandboxSession', model_command: str | None=None) -> SandboxCommands[ExecResult]
```

**Purpose**: Builds the command helper used to run bash, shell scripts, Python, and supervised tasks inside the sandbox. It centralizes timeouts, Python isolation, and the command supervisor.

**Data flow**: It receives a bound session and optional model-authored command text. It returns a `SandboxCommands` helper whose execute callback calls the carrier with the session handle.

**Call relations**: `bash`, `bash_task`, `sh`, and `python` all call this before running command-specific logic. It is the bridge from high-level command methods to carrier execution.

*Call graph*: called by 4 (bash, bash_task, python, sh); 1 external calls (__init__).


##### `Sandbox.bash`  (lines 973–975)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a bash command inside the sandbox. This is the simple command path used by tools and extensions that need shell behavior.

**Data flow**: It receives command text and an optional timeout. It binds the sandbox, builds the command helper, runs bash, and returns an `ExecResult`.

**Call relations**: Sandbox Chrome extension code calls this for setup, teardown, and recovery commands. Internally it delegates execution through `_commands` and then the carrier.

*Call graph*: calls 2 internal fn (_bound, _commands); called by 6 (_lease, reattach, _abandon_allocation, _bring_up_failure, _stop_bridge, _stop_stack).


##### `Sandbox.bash_task`  (lines 977–994)

```
async def bash_task(self, command: str, base: str, *, detach: bool, model_authored: bool, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs or reattaches to a supervised, journaled bash task. It also records whether the command text was written by the model, which matters for carriers running on a member's own machine.

**Data flow**: It receives command text, a task base name, detach and model-authored flags, and an optional timeout. It binds the sandbox, creates a command helper carrying the model text only when appropriate, and returns the task result.

**Call relations**: The sandbox Chrome bridge startup uses this for long-running supervised work. It shares the same command machinery as `bash` but goes through the supervisor task path.

*Call graph*: calls 2 internal fn (_bound, _commands); called by 1 (_start_bridge).


##### `Sandbox.sh`  (lines 996–1001)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX shell script inside the sandbox with each argument passed separately. Passing arguments separately avoids unsafe string interpolation.

**Data flow**: It receives a script, positional arguments, and an optional timeout. It binds the sandbox, builds command support, runs the script with the arguments, and returns an `ExecResult`.

**Call relations**: Callers use this when they want a small portable shell script rather than bash-specific behavior. It delegates to `_commands`.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.python`  (lines 1003–1014)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with UFO's containment guard injected and Python isolated from workspace-planted imports. This keeps path-checking code trustworthy even when the workspace is writable by the agent.

**Data flow**: It receives Python source text, arguments, and an optional timeout. It binds the sandbox, builds command support configured with the guard bootstrap and isolated Python flag, runs the program, and returns an `ExecResult`.

**Call relations**: This is the safe common route for in-sandbox Python snippets. It uses `_commands`, which ultimately calls the carrier's `exec` method.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.stop_commands`  (lines 1016–1022)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this turn when the carrier supports explicit stopping. It is used after a deliberate member stop, not for every cancellation.

**Data flow**: It binds the sandbox. If the carrier implements `CommandStopping`, it asks the carrier to stop commands for the handle; otherwise it does nothing.

**Call relations**: Higher-level cancellation logic can call this on any sandbox. The protocol check keeps carriers without long-lived commands from needing a fake stop implementation.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.write_file`  (lines 1024–1026)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a file in the conversation workspace. It first scopes the path to `/workspace` so callers cannot write outside the allowed file area.

**Data flow**: It receives a path and bytes. It binds the sandbox, normalizes the path with `workspace_path`, and asks the carrier to write the content.

**Call relations**: Sandbox setup code uses this to place files into the workspace. It is the public workspace-writing counterpart to runtime-file writing.

*Call graph*: calls 2 internal fn (_bound, workspace_path); called by 1 (run).


##### `Sandbox.load_skills`  (lines 1028–1070)

```
async def load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Loads system and user skills into the sandbox runtime and returns the installed skill roots. It verifies the loader's response and can refresh system skills if the sandbox image is stale.

**Data flow**: It receives a skill payload. It binds the sandbox, uses a native carrier loader if available or stages the payload and runs the generic loader, parses JSON output, checks that returned names match the requested skills, and returns a name-to-path map. If expected system skills are missing and an archive is available, it syncs system skills once and retries.

**Call relations**: The runtime skill installation flow calls this. It coordinates `SkillLoading`, `_run_staged_skill_load`, `_sync_system_skills`, and JSON validation.

*Call graph*: calls 3 internal fn (_bound, _run_staged_skill_load, _sync_system_skills); called by 2 (install_skill, load_skills); 1 external calls (loads).


##### `Sandbox._run_staged_skill_load`  (lines 1072–1094)

```
async def _run_staged_skill_load(self, bound: 'SandboxSession', payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Implements the generic skill-loading fallback by writing the payload into the sandbox and running a guarded installer program there. It is used when the carrier has no native skill loader.

**Data flow**: It serializes the payload as JSON, writes it to a unique staging path under the runtime root, computes its digest, and runs the skill loader Python program through `_exec_skill`. The command result is returned for the caller to validate.

**Call relations**: `Sandbox.load_skills` calls this initially or after syncing system skills. It uses carrier writing for staging and privileged skill execution for installation.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 3 external calls (sha256, dumps, uuid4).


##### `Sandbox._sync_system_skills`  (lines 1096–1113)

```
async def _sync_system_skills(self, bound: 'SandboxSession') -> None
```

**Purpose**: Copies the server's system-skill archive into the sandbox and installs it after verifying the archive digest. This repairs a sandbox whose baked system skills are missing or outdated.

**Data flow**: It writes the archive to a unique runtime staging path, runs the system-skill sync Python program with the archive digest, and raises an `OSError` if the command fails.

**Call relations**: `Sandbox.load_skills` calls this only when the first skill-load attempt is missing system skills and an archive is available. It uses `_exec_skill` for the privileged install step.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 2 external calls (sha256, uuid4).


##### `Sandbox._exec_skill`  (lines 1115–1120)

```
async def _exec_skill(self, bound: 'SandboxSession', argv: tuple[str, ...]) -> ExecResult
```

**Purpose**: Runs a privileged skill-related command through a carrier that supports skill execution. It refuses to proceed on carriers that cannot do this safely.

**Data flow**: It receives a bound session and command arguments. It checks that the carrier implements `SkillExecuting`, runs the command with the default timeout, and returns the `ExecResult`; otherwise it raises `RuntimeError`.

**Call relations**: Both staged skill loading and system-skill syncing call this. It is the enforcement point for the `SkillExecuting` protocol.

*Call graph*: called by 2 (_run_staged_skill_load, _sync_system_skills).


##### `Sandbox.ensure_tool_output_dir`  (lines 1122–1146)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure UFO's private tool-output runtime directory exists, even if a file or broken link is squatting on that name. This keeps later output offloading from being poisoned by a bad preexisting path.

**Data flow**: It binds the sandbox, builds the runtime tool-output path, runs a shell snippet that creates the directory or removes a non-directory squatter first, and returns true if it reclaimed a squatter. Command failure becomes an `OSError`.

**Call relations**: Tool-output offload code can call this before writing internal output files. It uses the carrier's `exec` directly because the target path is fixed and internal.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.file_exists`  (lines 1148–1154)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the workspace. It is a lightweight test that still respects workspace path scoping.

**Data flow**: It receives a path, normalizes it under `/workspace`, binds the sandbox, runs `test -f` inside the sandbox, and returns true for success.

**Call relations**: Callers use this before file operations that depend on a workspace file being present. It combines `workspace_path` with carrier execution.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.run_ufo_fs`  (lines 1156–1197)

```
async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one UFO file-tool operation inside the sandbox after carefully scoping its path argument. It allows normal workspace operations and read-only access to selected runtime or skill paths.

**Data flow**: It copies the input arguments, inspects any `path`, rewrites allowed workspace, runtime, or skill paths, rejects writes outside `/workspace`, adds the effective workspace root, and calls the carrier's `file_op`. The result is the parsed operation output.

**Call relations**: File tools use this common method rather than talking to carriers directly. It relies on `_runtime_root`, `rooted_path`, and `workspace_path` before handing off to `Carrier.file_op`.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); 1 external calls (PurePosixPath).


##### `Sandbox.read_file`  (lines 1199–1201)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts a streamed read of a workspace file or an allowed current-runtime file. It gives callers bytes in chunks rather than one large buffer.

**Data flow**: It receives a path and returns the async iterator produced by `_read_scoped_file`. The actual binding, scoping, and carrier reading happen when the iterator is consumed.

**Call relations**: Public file-read callers use this entry point. It delegates all safety decisions to `_read_scoped_file`.

*Call graph*: calls 1 internal fn (_read_scoped_file).


##### `Sandbox._read_scoped_file`  (lines 1203–1216)

```
async def _read_scoped_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes from a scoped file path after deciding whether the path belongs to the workspace or current runtime area. This is the safe implementation behind `read_file`.

**Data flow**: It binds the sandbox, computes the runtime display path, maps runtime paths to the real runtime root when allowed, otherwise normalizes the path under `/workspace`, and yields chunks from the carrier's `read` method.

**Call relations**: `Sandbox.read_file` calls this. It uses `_runtime_root`, `rooted_path`, and `workspace_path` to choose a safe target before delegating to the carrier.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); called by 1 (read_file); 1 external calls (PurePosixPath).


##### `Sandbox._read_file`  (lines 1218–1221)

```
async def _read_file(self, target: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes from an already-resolved target path. It is a lower-level helper for code that has already done path scoping.

**Data flow**: It receives a target path, binds the sandbox, asks the carrier to read that path, and yields each byte chunk onward.

**Call relations**: This bypasses the workspace/runtime decision made by `_read_scoped_file`, so it is intended for internal callers that already know the target is safe.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.dial`  (lines 1223–1227)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Gets an externally reachable address for a service running inside the sandbox on a given port. This lets outside code connect to sandbox-started services without knowing backend-specific networking.

**Data flow**: It receives a port number, binds the sandbox, calls the carrier's `dial`, and returns a `DialTarget` containing host, TLS setting, and any needed headers.

**Call relations**: The sandbox Chrome extension uses this to reach browser-related endpoints. The real networking details are supplied by the carrier.

*Call graph*: calls 1 internal fn (_bound); called by 1 (_endpoint).


##### `SandboxSession.conversation_id`  (lines 1241–1242)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id from an already-bound sandbox handle. For a session, this value is immediately available.

**Data flow**: It reads `handle.conversation_id` and returns it.

**Call relations**: This fulfills the base `Sandbox.conversation_id` contract for concrete sessions.


##### `SandboxSession.turn_id`  (lines 1245–1246)

```
def turn_id(self) -> UUID | None
```

**Purpose**: Returns the turn id carried by an already-bound sandbox handle, if any. This identifies the turn authority for the session.

**Data flow**: It reads `handle.turn_id` and returns that UUID or `None`.

**Call relations**: This fulfills the base `Sandbox.turn_id` contract and supports turn-scoped stopping and authorization.


##### `SandboxSession.created`  (lines 1249–1250)

```
def created(self) -> bool
```

**Purpose**: Reports that a concrete sandbox session already exists. Unlike a late sandbox, no creation is pending.

**Data flow**: It returns `True` every time.

**Call relations**: This fulfills the base `Sandbox.created` contract for an eager, bound session.


##### `SandboxSession._bound`  (lines 1252–1253)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Returns the session itself because it is already bound to a carrier and handle. No lazy open is needed.

**Data flow**: It receives no extra input and returns `self`.

**Call relations**: All inherited `Sandbox` operations call `_bound`; for `SandboxSession`, that call is effectively a no-op.


##### `SandboxSession.authorize`  (lines 1255–1283)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new session view with a different run token and adjusted environment variables. This lets one shared container be used under the correct authority for a specific operation.

**Data flow**: It receives a new run token, environment names to clear, and variables to add. It checks that the current handle has a run token and that proxy environment variables contain it, rewrites proxy URLs to use the new token, drops cleared variables, merges new variables, and returns a new `SandboxSession` with an updated handle.

**Call relations**: The authorized lazy wrapper calls this after opening the real session. It is the concrete implementation of `Sandbox.authorize` for already-created sandboxes.

*Call graph*: 2 external calls (__init__, __init__).


##### `_LateSandbox.__init__`  (lines 1287–1299)

```
def __init__(self, conversation_id: UUID, turn_id: UUID, open: Callable[[], Awaitable[SandboxSession]], existing: Callable[[], Awaitable[SandboxSession | None]]) -> None
```

**Purpose**: Creates a lazy sandbox reference that knows how to open a session only when needed. This avoids provisioning a sandbox for operations that may never touch it.

**Data flow**: It stores the conversation id, turn id, an async open function, an async existing-session lookup, creates a lock, and starts with no session cached.

**Call relations**: Sandbox-opening orchestration constructs this kind of object for turn flows. Later calls to `_bound` use the stored open function, protected by the lock.

*Call graph*: 1 external calls (Lock).


##### `_LateSandbox.conversation_id`  (lines 1302–1303)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id for a lazy sandbox without creating it. This lets callers name the workspace safely before any backend work happens.

**Data flow**: It reads the stored conversation UUID and returns it.

**Call relations**: This implements the base sandbox property for lazy references and is forwarded by `_AuthorizedSandbox`.


##### `_LateSandbox.turn_id`  (lines 1306–1307)

```
def turn_id(self) -> UUID
```

**Purpose**: Returns the turn id associated with the lazy sandbox. The turn id is known before the sandbox is opened.

**Data flow**: It reads the stored turn UUID and returns it.

**Call relations**: This implements the base sandbox property and supports scoped command stopping even before creation.


##### `_LateSandbox.created`  (lines 1310–1311)

```
def created(self) -> bool
```

**Purpose**: Reports whether the lazy sandbox has already opened and cached a real session. It does not create the sandbox just to answer.

**Data flow**: It checks whether `_session` is set and returns a boolean.

**Call relations**: This implements the base `created` property. `_AuthorizedSandbox` forwards to it.


##### `_LateSandbox.authorize`  (lines 1313–1319)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Returns an authorized wrapper around the lazy sandbox. The actual token and environment rewrite is delayed until the sandbox is opened.

**Data flow**: It receives the desired run token, cleared environment names, and added environment variables, then returns an `_AuthorizedSandbox` holding those settings.

**Call relations**: This implements lazy authorization. When `_AuthorizedSandbox._bound` later runs, it opens the underlying session and applies `SandboxSession.authorize`.

*Call graph*: 1 external calls (__init__).


##### `_LateSandbox._bound`  (lines 1321–1326)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens the sandbox on first use and then reuses the same session. The lock prevents two simultaneous first operations from creating two sandboxes.

**Data flow**: If a session is already cached, it returns it. Otherwise it takes the async lock, checks again, awaits the stored open function, caches the resulting `SandboxSession`, and returns it.

**Call relations**: All inherited sandbox operations on a late sandbox eventually call this. It is the lazy creation point for command, file, skill, and dial operations.


##### `_LateSandbox.stop_commands`  (lines 1328–1331)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops this turn's commands even if the lazy sandbox has not been opened by this object yet. It can look for an existing sandbox without forcing creation.

**Data flow**: It uses the cached session if present; otherwise it awaits the existing-session lookup. If a session exists and its carrier supports command stopping, it calls the carrier with a handle whose turn id is set to this lazy sandbox's turn.

**Call relations**: Cancellation code can call this safely on lazy sandboxes. It uses the non-creating existing lookup so a stop request does not create a fresh sandbox just to stop nothing.

*Call graph*: 1 external calls (replace).


##### `_AuthorizedSandbox.conversation_id`  (lines 1342–1343)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id of the underlying lazy sandbox. Authorization does not change which conversation the sandbox belongs to.

**Data flow**: It reads `late.conversation_id` and returns it.

**Call relations**: This forwards the base sandbox property while preserving the authorized wrapper's transparent behavior.


##### `_AuthorizedSandbox.turn_id`  (lines 1346–1347)

```
def turn_id(self) -> UUID
```

**Purpose**: Returns the turn id of the underlying lazy sandbox. Authorization does not change the turn binding.

**Data flow**: It reads `late.turn_id` and returns it.

**Call relations**: This forwards the base sandbox property for callers holding an authorized lazy view.


##### `_AuthorizedSandbox.created`  (lines 1350–1351)

```
def created(self) -> bool
```

**Purpose**: Reports whether the underlying lazy sandbox has already been opened. The authorized wrapper does not force creation.

**Data flow**: It reads `late.created` and returns that boolean.

**Call relations**: This keeps authorized and unauthorised lazy references consistent when callers check creation state.


##### `_AuthorizedSandbox.authorize`  (lines 1353–1359)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Creates a new authorized view of the same lazy sandbox, replacing the prior authorization settings. This avoids stacking multiple environment rewrites.

**Data flow**: It receives a run token, names to clear, and environment variables, then asks the underlying late sandbox to create a fresh authorized wrapper with those values.

**Call relations**: Callers can re-authorize an already authorized sandbox. The method delegates to `_LateSandbox.authorize` so there is still only one lazy-open object underneath.


##### `_AuthorizedSandbox._bound`  (lines 1361–1362)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens or retrieves the underlying lazy session, then applies this wrapper's run token and environment authority. This is where delayed authorization becomes a concrete session.

**Data flow**: It awaits `late._bound()` to get a `SandboxSession`, calls that session's `authorize` with the stored token and environment changes, and returns the authorized session.

**Call relations**: All inherited sandbox operations on an authorized lazy sandbox pass through this method. It connects `_LateSandbox` lazy creation with `SandboxSession.authorize` token rewriting.


### `core/src/ufo/harness/sandbox/exec_env.py`

`domain_logic` · `sandbox open / command execution setup`

A sandbox is a controlled place where the system can run commands. Those commands often need to use outside services, such as Git hosting or connector command-line tools. This file decides which environment variables the sandbox should receive so those tools work, while keeping real credentials out of the sandbox.

The key idea is a sentinel: a harmless placeholder value. The sandbox sees the sentinel, not the secret. Later, an egress proxy, which watches outbound network traffic, swaps the sentinel for the real credential only when making an allowed request. This is like giving a courier a claim ticket instead of the valuable item itself.

The file combines several sources of information. It adds the conversation ID, prepares Git configuration so Git can ask the right credential helper, exports connector CLI variables for accounts the current authority may use, adds Git author information when a grant provides it, and exports keyed provider variables when a workspace has the needed stored credential and host choice.

It is careful around ambiguity and failure. If two possible accounts could fill one static environment variable, it logs the problem and exports nothing for that connector rather than silently choosing the wrong account. If a credential slot cannot be read, only that provider is skipped; the whole sandbox open is not failed.

#### Function details

##### `ProbeEnv.exports`  (lines 65–77)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, authority: ExecutionAuthority) -> dict[str, str]
```

**Purpose**: Builds the full set of environment variables for an off-turn probe running inside a sandbox. It gathers conversation identity, Git settings, connector credentials, and keyed provider placeholders into one dictionary.

**Data flow**: It receives a conversation ID, a probe ID, and an execution authority that says whose permissions apply. It reads the current workspace ID, then asks helper functions to create Git config variables, connector CLI variables, and keyed provider variables. It returns one combined mapping of environment variable names to string values, all safe to place inside the sandbox.

**Call relations**: This is the main entry point in this file. When a probe sandbox is opened, this method coordinates the smaller helpers: it uses `cli_git_config` to find Git credential-helper settings, `_git_config_env` to encode those settings for Git, `_grant_cli_env` to add grant-based connector variables, and `_keyed_provider_env` to add stored provider placeholders.

*Call graph*: calls 4 internal fn (_git_config_env, _grant_cli_env, _keyed_provider_env, cli_git_config); 1 external calls (ws_current).


##### `_git_config_env`  (lines 80–87)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into environment variables in the format Git understands. This avoids needing to write a Git config file inside the sandbox.

**Data flow**: It takes a sequence of Git setting pairs, each with a key and value. It creates `GIT_CONFIG_COUNT` plus numbered `GIT_CONFIG_KEY_*` and `GIT_CONFIG_VALUE_*` variables. It returns those variables as a dictionary ready to merge into the sandbox environment.

**Call relations**: `ProbeEnv.exports` calls this after collecting the Git settings. Its output gives Git inside the sandbox instructions such as which proxy authentication method or credential helper to use.

*Call graph*: called by 1 (exports).


##### `cli_git_config`  (lines 90–105)

```
def cli_git_config(clis: Mapping[str, CliCredential]) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds Git credential-helper settings for connector CLIs that know how to authenticate to Git hosts. This lets ordinary Git commands like clone or push ask the connector helper for a safe sentinel credential.

**Data flow**: It receives the configured connector CLI credentials. For each connector that declares Git support, it creates Git settings for that host's credential helper. Connectors without Git support are skipped. It returns the settings as a tuple.

**Call relations**: `ProbeEnv.exports` uses this before creating the final Git environment variables. Its settings are later converted by `_git_config_env`, so Git inside the sandbox can authenticate through the same connector path as the CLI.

*Call graph*: called by 1 (exports).


##### `_keyed_provider_env`  (lines 108–155)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Adds environment variables for provider credentials that are stored by the workspace, such as an API key and possibly a provider host or region. It exports only sentinels and host names, never the real secret.

**Data flow**: It receives an optional credential store, declared credential slots, and the workspace ID. For each slot with an injection target, it checks whether a credential exists, resolves the correct host if needed, and then adds the slot's sentinel and host value to the environment. If a slot is unset, unreadable, or has no valid host, it skips that slot and logs warnings for unexpected problems. It returns the provider environment variables that are safe to expose.

**Call relations**: `ProbeEnv.exports` calls this while assembling the sandbox environment. This helper talks to the credential store and host resolver, but it contains failures to the affected provider so an unrelated sandbox run can still start.

*Call graph*: calls 1 internal fn (get); called by 1 (exports); 2 external calls (warn, credential_host).


##### `_grant_cli_env`  (lines 158–205)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], authority: ExecutionAuthority, run_id: UUID) -> dict[str, str]
```

**Purpose**: Adds connector CLI environment variables for accounts that the current authority is allowed to use. It also adds Git commit identity when the chosen grant includes one.

**Data flow**: It receives the grant store, connector CLI definitions, the execution authority, and the run ID used for logging. It finds the member tied to the authority, loads active grants, and checks which accounts are usable for each connector. If exactly one account is usable, it exports that connector's sentinel variable. If the connector also supports Git, it may add Git author and committer variables. If there are multiple possible accounts or conflicting Git identities, it logs the ambiguity and avoids exporting misleading values. It returns the selected environment variables.

**Call relations**: `ProbeEnv.exports` calls this to translate permissions into sandbox-visible CLI variables. Inside, it relies on grant lookup helpers to choose usable accounts and on `_git_identity_env` to derive commit identity for Git-capable connectors.

*Call graph*: calls 2 internal fn (_git_identity_env, active_grants); called by 1 (exports); 4 external calls (log, grant_sentinel, usable_cli_accounts, authority_member_id).


##### `_git_identity_env`  (lines 208–237)

```
def _git_identity_env(granted: tuple[Grant, ...], provider: str, account_id: str) -> dict[str, str]
```

**Purpose**: Finds the Git author and committer identity for a specific connected account. This helps commits made in the sandbox be attributed to the same account used to authenticate Git access.

**Data flow**: It receives the active grants, a provider name, and an account ID. It searches for the matching grant. If the grant includes commit identity details, it returns the four standard Git identity variables for author and committer name and email. If there is no matching grant or no commit identity, it returns an empty dictionary.

**Call relations**: `_grant_cli_env` calls this after selecting a connector account. Its result is folded into the sandbox environment only when a single, unambiguous Git identity can be used.

*Call graph*: called by 1 (_grant_cli_env).


### `core/src/ufo/harness/sandbox/protocol.py`

`io_transport` · `request handling`

A sandbox is an isolated place where code can run without freely touching the host machine. This file is the thin protocol layer that turns ordinary requests, like “run this Bash command” or “read this file,” into carefully shaped command calls for that sandbox.

The main idea is separation. The file does not know whether commands are sent over a container, a remote service, or some other transport. Instead, callers give it an `execute` function. That function is the delivery truck. This file only packs the boxes correctly.

`SandboxCommands` builds safe command argument lists for common cases: running Bash, running a longer tracked Bash task, running a POSIX shell script, or running a Python snippet with a bootstrap prefix. It also applies a default timeout, so commands cannot run forever unless a caller chooses a different limit.

`SandboxFileOperations` is more structured. It sends a named file operation plus JSON parameters to a sandbox-side file tool, waits for one JSON response, and turns that response back into a Python dictionary. If the sandbox returns no output, malformed JSON, or an error field, this code raises clear exceptions. One important detail is that reads of certain document-like file types get a longer timeout, because opening or extracting those files may take more time than simple text operations.

#### Function details

##### `CommandResult.stdout`  (lines 14–14)

```
def stdout(self) -> str
```

**Purpose**: This property describes the normal text output that any sandbox command result must provide. It lets the rest of the code read successful command output without caring what concrete result class was used.

**Data flow**: Before: some sandbox command has finished and produced a result object. This property reads the result’s standard output text. After: the caller receives that text as a string.

**Call relations**: This is part of the `CommandResult` protocol, which is a promise about what a result object looks like. `SandboxFileOperations.run` relies on this property when it reads the sandbox file tool’s JSON response from normal output.


##### `CommandResult.stderr`  (lines 17–17)

```
def stderr(self) -> str
```

**Purpose**: This property describes the error text that any sandbox command result must provide. It is used when normal output is missing or unusable, so the caller can get a helpful failure message.

**Data flow**: Before: a sandbox command result may contain text written to the error stream. This property reads that error text. After: the caller receives it as a string, often to include in an exception.

**Call relations**: This is part of the shared result shape expected by the sandbox protocol. `SandboxFileOperations.run` uses it as a fallback explanation when the sandbox file operation gives no usable JSON response.


##### `CommandResult.exit_code`  (lines 20–20)

```
def exit_code(self) -> int
```

**Purpose**: This property describes the numeric status code returned by a sandbox command. A status code is the conventional number a process returns to say whether it succeeded or failed.

**Data flow**: Before: a command has completed in the sandbox. This property reads the command’s exit code. After: the caller receives that integer status value.

**Call relations**: This property belongs to the `CommandResult` protocol so different command carriers can expose the same basic result fields. This particular file defines the expectation, while concrete executors elsewhere decide how to use or check the code.


##### `SandboxCommands.bash`  (lines 33–38)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This function runs one Bash command through the sandbox’s command supervisor. Bash is a common command-line shell, and the supervisor is the wrapper that keeps execution bounded and controlled.

**Data flow**: Before: the caller supplies a command string and optionally a timeout. The function builds an argument list that asks the supervisor to run `bash -lc` with that command, then chooses either the caller’s timeout or the default timeout. After: it awaits the supplied `execute` function and returns whatever result that executor produces.

**Call relations**: Callers use this when they want a simple shell command run inside the sandbox. It hands the fully built command tuple to the injected `execute` function, so the actual transport remains outside this file.


##### `SandboxCommands.bash_task`  (lines 40–62)

```
async def bash_task(self, command: str, base: str, *, detach: bool, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This function runs a Bash command as a named, journaled task, or reattaches to one. In plain terms, it is for longer-running or trackable shell work where the sandbox supervisor keeps a record under a task name.

**Data flow**: Before: the caller provides a command, a base task name, whether to detach, and optionally a timeout. The function adds task-related supervisor flags, includes `--detach` when requested, appends the Bash command, and picks the timeout. After: it sends that prepared command to `execute` and returns the resulting sandbox command result.

**Call relations**: This is used when a caller needs more than a one-off shell command, such as starting work that may continue or be checked later. Like the other command helpers, it does not run anything directly; it delegates the finished argument list to the caller-provided `execute` function.


##### `SandboxCommands.sh`  (lines 64–69)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This function runs a POSIX shell script with each extra argument kept separate. POSIX shell is the widely available basic shell interface, and keeping arguments separate avoids accidentally merging values into one unsafe string.

**Data flow**: Before: the caller supplies a shell script, any number of string arguments, and optionally a timeout. The function builds a command like `sh -c <script> sh <args>`, preserving each argument as its own item, then chooses the timeout. After: it awaits `execute` and returns its result.

**Call relations**: Callers use this for portable shell snippets, especially when they need to pass arguments cleanly. It hands control to the injected `execute` function after shaping the command in the expected form.


##### `SandboxCommands.python`  (lines 71–82)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This function runs a small Python program inside the sandbox, with a bootstrap prefix added first. The bootstrap is setup code supplied by the caller, often used to make the Python run more contained or predictable.

**Data flow**: Before: the caller provides Python source text, optional command-line arguments, and optionally a timeout. The function prefixes the program with `python_bootstrap`, runs it through `python3` with the configured Python flag, and keeps each argument separate. After: it sends the command to `execute` and returns the sandbox result.

**Call relations**: This helper is the Python counterpart to the shell helpers. It prepares a controlled Python invocation, then relies on the external `execute` function to actually run it in the sandbox.


##### `SandboxFileOperations.run`  (lines 96–126)

```
async def run(self, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This function performs one structured file operation in the sandbox and returns the parsed JSON response. It is used when file actions need a clear request-and-response format instead of raw shell output.

**Data flow**: Before: the caller gives an operation name, such as a file action, and a dictionary of parameters. The function checks whether this is a read of a document-like file suffix and, if so, uses the longer document-read timeout; otherwise it uses the normal timeout. It serializes the parameters to compact JSON, calls the sandbox file command through `execute`, strips the command’s normal output, parses that output as JSON, checks that it is a dictionary, and treats a string `error` field as a reported failure. After: it returns the parsed dictionary, or raises an exception if the response is empty, not valid JSON, not an object, or explicitly reports an error.

**Call relations**: This is the main bridge between high-level file requests and the sandbox-side file protocol. During its work it uses `PurePosixPath` to inspect the requested path’s suffix, `json.dumps` to package the request parameters, and `json.loads` to unpack the sandbox’s reply.

*Call graph*: 3 external calls (dumps, loads, PurePosixPath).


### Carrier selection
Backend selection logic that chooses the active sandbox carrier while preserving access to previously configured carriers.

### `core/src/ufo/harness/sandbox/select.py`

`orchestration` · `startup / config load`

A sandbox carrier is the thing that actually opens and resumes isolated workspaces, such as a local sandbox or one supplied by an extension. This file is the selection desk for those carriers. It starts with the built-in local carrier, then adds any carriers advertised by installed extensions through their manifests. It refuses confusing setups early: two carriers cannot use the same backend name, a requested backend must actually exist, and a resume-only backend cannot duplicate the default backend.

The important idea is migration safety. New sandboxes open on the configured default backend. But old sandbox handles may still point to older backend names, so this file can also build a separate set of “resume backends” that stay alive only to reopen those existing workspaces. That is like moving a hotel’s new bookings to a new building while keeping the old front desk staffed for guests who already have rooms there.

There is also a safety check for remote carriers, meaning carriers that run outside the current machine or cluster. They must be given a public HTTPS proxy URL. Without it, code inside the sandbox could not reliably use the project’s controlled egress path, where outbound network access can be credential-injected, denied by default, and measured. The result is a frozen DeployCarriers object containing the chosen default carrier, its description, and any resume carriers.

#### Function details

##### `select_carriers`  (lines 25–49)

```
def select_carriers(config: Config, manifests: tuple[Manifest, ...]) -> DeployCarriers
```

**Purpose**: Builds the complete carrier set for this running deployment. It decides which backend new sandboxes should use, and which older backends should remain available only for resuming existing sandbox handles.

**Data flow**: It receives the main configuration and the extension manifests. It starts with the built-in local carrier, adds each carrier advertised by extensions, checks for duplicate names and invalid resume settings, then asks _built to create the default carrier and each resume carrier. It returns a DeployCarriers object containing the live default carrier, its carrier specification, and a name-to-carrier map for resume-only backends.

**Call relations**: This is the public entry point of the file. During setup, higher-level startup code calls it after configuration and extension manifests are known. It delegates the per-backend validation and construction work to _built, then packages the result into DeployCarriers for the rest of the sandbox system to use.

*Call graph*: calls 1 internal fn (_built); 2 external calls (__init__, __init__).


##### `_built`  (lines 52–72)

```
def _built(specs: dict[str, CarrierSpec], config: Config, name: str) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Creates one carrier from a registered backend name, while enforcing the safety rules for that backend. Someone uses it when they have already collected all possible carriers and need one concrete live carrier instance.

**Data flow**: It receives the registry of known carrier specifications, the configuration, and the requested backend name. It looks up that name; if it is missing, it raises an error explaining what names are available. If the backend is remote, it reads the configured public proxy URL and checks that it exists and is an HTTPS URL. If all checks pass, it calls the carrier factory and returns the new carrier together with its specification.

**Call relations**: select_carriers calls this once for the default sandbox backend and once for each resume backend. _built does the stricter, backend-specific checks before handing a ready carrier back to select_carriers, so unsafe or unknown carriers fail during startup instead of later when a sandbox is already being opened.

*Call graph*: called by 1 (select_carriers); 2 external calls (__init__, urlparse).


### Persistent command tools
Durable command and REPL task support for long-running shells, logs, resumable sessions, and crash-safe repeated calls.

### `core/src/ufo/runtime/tools/tasks.py`

`domain_logic` · `tool execution`

This file is the task journal for shell work started by UFO tools. Its main idea is simple: a command can be launched once, watched for a limited time, and then either return normally or keep running in the background with clear instructions for how to follow it. Without this, a long command might be killed just because the foreground wait expired, or a crash-and-retry might run the same command twice.

The file treats the timeout as “how long the caller waits,” not always “how long the command may live.” That distinction matters. If a command is still alive when the wait expires, the code probes for its supervisor process and returns handles: where to read the log, where the exit-code file will appear, how to watch for completion, and how to stop it. Think of it like dropping off laundry: you wait at the counter for a bit, but if it is not ready, you get a ticket instead of forcing the washing machine to stop.

It also contains guardrails and explanations. It can spot long, flat `sleep` commands that merely waste a turn, create stable task IDs from retry keys, explain timeout messages clearly, and record diagnostic information when the sandbox command channel appears to stop responding.

#### Function details

##### `flat_sleeps`  (lines 33–51)

```
def flat_sleeps(command: str) -> tuple[int, ...]
```

**Purpose**: Finds long top-level `sleep` commands that look like they are just padding a foreground wait. It ignores sleeps inside shell loops, quotes, and heredoc blocks because those are often data or polling behavior rather than wasted waiting.

**Data flow**: It takes a shell command as text. It first blanks out quoted strings and heredoc bodies, then scans the remaining text for `sleep`, `do`, and `done`; while inside a loop it ignores sleeps, and outside a loop it collects only sleeps longer than the allowed small amount. It returns a tuple of the long sleep durations it found.

**Call relations**: This helper is meant to be used before running a command, when a tool wants to refuse commands that would consume a turn by simply sleeping. It does not launch anything itself; it only gives callers a clean yes-or-no clue by returning the suspicious sleep lengths.


##### `run_task`  (lines 94–139)

```
async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None, *, model_authored: bool) -> TaskRun
```

**Purpose**: Launches a shell command through the durable task journal and waits only for the caller’s allowed time. If the command finishes, it returns the result; if it is still running, it returns enough information for the caller to reconnect to it later.

**Data flow**: It receives a tool context, command text, an optional timeout in milliseconds, and a flag saying whether the model wrote the command. It converts the timeout to seconds, caps it at the system maximum, creates or reuses a task ID, asks the sandbox to run the command using task files, and waits for the result. If the sandbox reports a timeout, it probes the task’s process ID; if no still-running task can be found, it records diagnostic details. It returns a `TaskRun` containing the task ID, sandbox result, requested timeout, possible process ID, and display path for the task files.

**Call relations**: This is the central path used when a tool needs to run shell work. It asks `task_id` for the stable journal name, delegates the actual command execution and probing to the sandbox object in the context, and calls `_record_exec_timeout` only when the wait expired but no live detached command can be confirmed.

*Call graph*: calls 2 internal fn (_record_exec_timeout, task_id); 1 external calls (__init__).


##### `task_id`  (lines 142–149)

```
def task_id(ctx: ToolContext) -> str
```

**Purpose**: Builds the short name used for a task’s files in the journal. It makes retries safe by giving the same recorded dispatch step the same task name, so recovery can find the first launch instead of starting over.

**Data flow**: It reads the idempotency key from the tool context. If there is no key, it creates a fresh random short ID; if there is a key, it hashes that key and uses the first part of the hash as the ID. The output is a short string suitable for naming task files.

**Call relations**: It is called by `run_task` before launching shell work. Its result decides whether a command gets a new journal entry or reconnects to an existing one from a previous attempt.

*Call graph*: called by 1 (run_task); 2 external calls (sha256, uuid4).


##### `task_handles`  (lines 152–173)

```
def task_handles(task: str, pid: str, display_base: str, applied_s: int | None=None, note: str='') -> str
```

**Purpose**: Creates the human-readable message and machine-readable JSON that tell someone how to interact with a detached command. It gives the log path, exit-file path, watch command, and stop command in one consistent format.

**Data flow**: It receives the task ID, supervisor process ID, display path for the task files, and optionally the timeout that caused the command to move into the background plus an extra note. It chooses the right lead sentence, builds a payload of file paths and shell snippets, joins the explanatory text, serializes the payload as JSON, and returns the combined string.

**Call relations**: This function is used by callers that need to report a still-running command back to the user or model. It does not decide whether a task is detached; it formats the handles after another part of the flow has already confirmed that the task is reachable.

*Call graph*: 1 external calls (dumps).


##### `timeout_notice`  (lines 176–192)

```
def timeout_notice(applied_s: int, requested_s: int | None) -> str
```

**Purpose**: Writes a clear timeout explanation for a command that was stopped by the sandbox. It distinguishes between the default timeout, a requested timeout, and a request that was reduced by the maximum cap.

**Data flow**: It receives the timeout that actually applied and the timeout the caller originally requested, if any. It compares the two and returns a sentence explaining why the command stopped and, when relevant, how long a caller may request in the future.

**Call relations**: This is a message-building helper for tool results that need to explain a timeout. It keeps timeout wording consistent, especially because an exit code alone may not reveal whether the sandbox deadline or something inside the command caused the stop.


##### `_record_exec_timeout`  (lines 195–228)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: Records diagnostic information when a command wait expired but the detached task could not be found alive. This helps separate “the command was slow” from “the sandbox command channel stopped answering.”

**Data flow**: It receives the tool context, the command text, the applied timeout, and the originally requested timeout. It briefly asks the sandbox for basic container health information such as load, memory, and workspace disk usage, but gives that probe its own short deadline. Whether the probe succeeds or fails, it writes a structured log entry with the profile, timeout values, a shortened command, and any vitals it could collect; it does not return a value and it swallows its own failures.

**Call relations**: It is called by `run_task` only in the suspicious case where the wait timed out and the follow-up probe did not find a live task or exit file. It hands the observation to the logging system, using the turn profile so operators can later connect the timeout to the kind of work that was running.

*Call graph*: called by 1 (run_task); 3 external calls (timeout, log, turn_profile).


### `extensions/repl/ufo_ext_repl/manifest.py`

`domain_logic` · `request handling`

This file is the doorway and control room for the “repl” extension. A REPL is an interactive coding session: you run a bit of code, then later code can reuse what succeeded before. Here, that memory is stored in workspace files. Before each run, the tool combines the saved code with the new code. If the run exits successfully, the combined code becomes the new saved state. If it fails or times out, the saved state is left untouched. This matters because otherwise one bad experiment could silently corrupt the whole session.

There are two main tools. `js_repl` runs JavaScript as a Node.js ES module, which allows top-level `await` and supports browser-testing libraries such as Playwright. It also installs a small helper called `emitImage`, so JavaScript code can return screenshots or other images inline. `xlsx_repl` runs Python code for spreadsheet work and prints a JSON version of a variable named `result` when one is set.

All code runs inside the project sandbox, meaning the file access and network rules of the container still apply. Long-running code uses the same background task system as shell commands: if the caller stops waiting, the process may continue, and the response gives task handles instead of pretending the run finished.

#### Function details

##### `_meter_run`  (lines 75–91)

```
def _meter_run(ctx: ToolContext, tool: str, exit_code: int) -> None
```

**Purpose**: Records one monitoring count for a REPL run, including which tool ran and what exit code it produced. This helps operators tell the difference between user code failing and the interpreter itself being missing or broken.

**Data flow**: It receives the tool context, the tool name, and the process exit code. It folds unusual exit codes into a shared “other” bucket, adds the current turn profile, and sends a metric event. It does not change the REPL state or return a value.

**Call relations**: After `js_repl` or `xlsx_repl` runs an interpreter, they call this function before building the user-facing result. It hands the observation off to the telemetry system through `emit_metric` and uses `turn_profile` to label where the run happened.

*Call graph*: called by 2 (js_repl, xlsx_repl); 2 external calls (emit_metric, turn_profile).


##### `global_modules_link`  (lines 94–113)

```
def global_modules_link(directory: str, roots: tuple[str, ...]=GLOBAL_MODULE_ROOTS) -> str
```

**Purpose**: Builds a shell command that makes globally installed Node.js packages visible to the JavaScript REPL. This is needed because ES modules do not automatically search `NODE_PATH` for imports.

**Data flow**: It receives a workspace directory and a list of possible global package roots. It returns a shell command string that creates a local `node_modules` folder and adds symbolic links to packages found in those roots. The command itself is run later by the JavaScript REPL.

**Call relations**: `js_repl` calls this before starting Node. The returned command uses `shell_path` to quote paths safely, then the sandbox shell executes it so bare imports such as installed browser libraries can resolve.

*Call graph*: called by 1 (js_repl); 1 external calls (shell_path).


##### `js_emit_relative`  (lines 120–128)

```
def js_emit_relative(call: str) -> str
```

**Purpose**: Creates the per-call workspace filename where JavaScript-emitted images will be written. A separate file per call prevents a still-running old task from mixing its images with a later call.

**Data flow**: It receives a short call identifier. It turns that into a relative path such as a JSON-lines image file under the REPL state directory. It returns only the path string.

**Call relations**: `js_repl` calls this when preparing a JavaScript run. The result is passed into `js_emit_prelude`, and later `_emitted_images` reads from the same location.

*Call graph*: called by 1 (js_repl).


##### `js_emit_prelude`  (lines 131–168)

```
def js_emit_prelude(emit_relative: str) -> str
```

**Purpose**: Creates the JavaScript setup code that defines `emitImage` for a single run. That helper lets user code return images by writing base64 image records to a file.

**Data flow**: It receives the image output path for the current call. It returns JavaScript source text that imports file-writing helpers, defines size and count limits, accepts several image input shapes, and writes a rolling list of recent images as JSON lines.

**Call relations**: `js_repl` places this prelude before the user’s JavaScript code in the run file. Later, `_emitted_images` reads the file that this prelude writes.

*Call graph*: called by 1 (js_repl); 1 external calls (dumps).


##### `ReplStateUnreadable.__init__`  (lines 258–260)

```
def __init__(self, failure: ToolFailure) -> None
```

**Purpose**: Stores a structured failure when the saved REPL state cannot be read or cleared. This turns a dangerous storage problem into a controlled error instead of letting the session be overwritten.

**Data flow**: It receives a `ToolFailure` object that explains what went wrong. It sets the exception message from that failure and keeps the failure object on the exception. The result is an exception that can be caught by the REPL handlers.

**Call relations**: `_state_failed` creates this exception when a state-file operation fails. `js_repl` and `xlsx_repl` catch it indirectly from `_candidate_source` and return its safe failure result to the caller.

*Call graph*: called by 1 (_state_failed).


##### `_state_failed`  (lines 263–279)

```
def _state_failed(verb: str, path: str, result: ExecResult) -> ReplStateUnreadable
```

**Purpose**: Builds the special error used when the REPL cannot read or clear its saved state. It exists to stop execution before new code accidentally replaces the old session with incomplete state.

**Data flow**: It receives the attempted action, the state path, and the sandbox command result. It packages the exit code, output, and error text into diagnostics, wraps that in a user-facing failure, and returns a `ReplStateUnreadable` exception.

**Call relations**: `_candidate_source` calls this when removing or reading the saved state fails. It constructs the failure with `CommandDiagnostics` and `ToolFailure`, then hands it back as an exception for the REPL handlers to catch.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_candidate_source); 2 external calls (__init__, __init__).


##### `_candidate_source`  (lines 294–306)

```
async def _candidate_source(ctx: ToolContext, relative: str, path: str, code: str, reset: bool) -> ReplSource
```

**Purpose**: Builds the full source code that should be run for the next REPL call. It combines the previously committed code with the new code, unless the caller asked for a reset.

**Data flow**: It receives the tool context, state-file paths, the new code, and whether to reset. If reset is requested, it tries to delete the saved state. If no saved state exists, it returns just the new code. Otherwise it reads the saved code, appends the new code, and returns both the full candidate and the committed portion it was based on.

**Call relations**: Both `js_repl` and `xlsx_repl` call this near the start of a request. If a state operation fails, it calls `_state_failed`; otherwise it returns a `ReplSource` used later to run code and, on success, update the saved state.

*Call graph*: calls 1 internal fn (_state_failed); called by 2 (js_repl, xlsx_repl); 2 external calls (__init__, shell_path).


##### `_redeclared_from_state`  (lines 323–331)

```
def _redeclared_from_state(committed: str, output: str) -> bool
```

**Purpose**: Checks whether a JavaScript “already declared” error was caused by a name saved in earlier REPL state. This lets the tool give the right advice: reset the session only when the conflict is truly with old state.

**Data flow**: It receives the committed JavaScript source and the combined process output. It looks for Node’s redeclaration error message, extracts the conflicting name, and searches the committed source for a matching declaration. It returns true or false.

**Call relations**: `js_repl` calls this after a JavaScript run finishes. If it returns true, `js_repl` passes a reset-focused hint into `_repl_result`; otherwise the normal failure message is used.

*Call graph*: called by 1 (js_repl); 2 external calls (escape, search).


##### `_repl_result`  (lines 334–350)

```
def _repl_result(stdout: str, stderr: str, exit_code: int, images: tuple[ImageContent, ...]=(), hint: str='') -> ToolResult
```

**Purpose**: Turns a completed interpreter run into the standard tool response. It reports stdout, stderr, and exit code, and warns when a failed run did not change the saved REPL state.

**Data flow**: It receives process output, an exit code, optional images, and an optional hint. It creates a JSON text payload. If the exit code is nonzero, it adds a notice explaining that the code was not committed and marks the tool result as an error. It returns a `ToolResult` containing text and any images.

**Call relations**: `js_repl` and `xlsx_repl` use this after runs that actually reached an exit code. It creates `TextContent` for the JSON report and wraps everything in `ToolResult`.

*Call graph*: called by 2 (js_repl, xlsx_repl); 3 external calls (__init__, __init__, dumps).


##### `_expired_result`  (lines 353–369)

```
def _expired_result(run: TaskRun, applied_s: int) -> ToolResult
```

**Purpose**: Builds the response for a run where the foreground wait time expired. It explains that the saved REPL state did not advance and, when possible, gives handles for the still-running background task.

**Data flow**: It receives the task run record and the number of seconds actually waited. If there is no process id, it returns an error message about the timeout. If the process is still reachable, it returns task id, log path, and process id details so the caller can inspect it later.

**Call relations**: `js_repl` and `xlsx_repl` call this when `run_task` reports a timeout. It uses `timeout_notice` for the human message and `task_handles` when there is a background task to hand back.

*Call graph*: called by 2 (js_repl, xlsx_repl); 4 external calls (__init__, __init__, task_handles, timeout_notice).


##### `_emitted_images`  (lines 377–391)

```
async def _emitted_images(ctx: ToolContext, emit_relative: str, emit_path: str) -> tuple[ImageContent, ...]
```

**Purpose**: Reads images that JavaScript code sent through `emitImage` and converts them into tool response content. It also removes the per-call image file afterward.

**Data flow**: It receives the tool context plus the relative and full paths of the emit file. If the file does not exist, it returns no images. If it exists, it reads the JSON-lines records, deletes the file, validates each recent line as an image, and returns image content objects for valid entries.

**Call relations**: `js_repl` calls this after Node finishes. It reads the file created by the JavaScript prelude from `js_emit_prelude` and passes the resulting images into `_repl_result`.

*Call graph*: called by 1 (js_repl); 2 external calls (__init__, shell_path).


##### `js_repl`  (lines 394–427)

```
async def js_repl(ctx: ToolContext, args: JsReplInput) -> ToolResult
```

**Purpose**: Runs one persistent JavaScript REPL call inside the sandbox. It preserves successful code for later calls, supports image output, links global Node packages, and protects state when a run fails or times out.

**Data flow**: It receives the tool context and validated JavaScript input. It finds the state and run file paths, builds candidate source with `_candidate_source`, writes a temporary run file with the image prelude plus user code, links Node modules, and runs Node through the task system. On success it saves the candidate as committed state. It returns stdout, stderr, exit code, notices, task handles, and any emitted images as appropriate.

**Call relations**: This is the handler registered for the `js_repl` tool in `manifest`. During a request it coordinates helpers: `_candidate_source` prepares code, `global_modules_link` prepares imports, `run_task` starts Node, `_meter_run` records the outcome, `_expired_result` covers timeouts, `_redeclared_from_state` improves redeclaration advice, `_emitted_images` gathers images, and `_repl_result` formats the final answer.

*Call graph*: calls 9 internal fn (_candidate_source, _emitted_images, _expired_result, _meter_run, _redeclared_from_state, _repl_result, global_modules_link, js_emit_prelude, js_emit_relative); 3 external calls (shell_path, run_task, uuid4).


##### `xlsx_repl`  (lines 430–448)

```
async def xlsx_repl(ctx: ToolContext, args: XlsxReplInput) -> ToolResult
```

**Purpose**: Runs one persistent Python REPL call for Excel-style spreadsheet work. It keeps successful Python code for later calls and prints the value of `result` as JSON when the user sets it.

**Data flow**: It receives the tool context and validated Python input. It builds the candidate source from saved state and new code, writes a run file with a footer that tries to JSON-print `result`, and runs Python through the task system. If the run succeeds, it commits the candidate source as the new saved state. It returns normal output, errors, exit code, or timeout information.

**Call relations**: This is the handler registered for the `xlsx_repl` tool in `manifest`. It relies on `_candidate_source` for state composition, `run_task` for execution, `_meter_run` for monitoring, `_expired_result` for foreground timeouts, and `_repl_result` for completed runs.

*Call graph*: calls 4 internal fn (_candidate_source, _expired_result, _meter_run, _repl_result); 2 external calls (shell_path, run_task).


##### `manifest`  (lines 451–471)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to the host system. It names the extension, registers the two REPL tools, lists related data skills, and declares that the sandbox may use the internet.

**Data flow**: It takes no input. It creates tool definitions for JavaScript and Excel/Python REPLs, creates skill specifications for the bundled data skills, and returns a `Manifest` object containing all of that metadata.

**Call relations**: The host calls this when loading the extension. The returned manifest points tool requests to `js_repl` and `xlsx_repl`, and it exposes the skill folders so the agent can load them when needed.

*Call graph*: 3 external calls (__init__, __init__, __init__).
