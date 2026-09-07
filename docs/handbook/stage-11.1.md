# Sandbox lifecycle and controlled network egress  `stage-11.1`

This stage is shared support for the system’s main work: giving each conversation a safe workspace and controlling how it reaches the internet. The conversation layer creates or reattaches the right workspace, records its location, and offers safe file read, write, list, and cleanup operations. The selector chooses which sandbox backends to keep ready, while the package marker simply makes the sandbox code importable.

Several backends can do the actual work. Local runs commands in a host folder for development. Terminal uses a user’s connected machine as the runner. Docker creates or reuses per-conversation containers. E2B does the same on a cloud sandbox service. Session and protocol provide the common “language” for commands and files, so the rest of UFO does not care which backend is underneath.

Exec environment and client-binary helpers prepare commands with approved settings and the right UFO program. Cache settings route downloads through shared caches. Background tasks keep long jobs running and expose logs. Finally, egress control, resolver, and rules decide which outside sites are allowed, what usage is metered, and where approved secrets can be safely injected.

## Files in this stage

### Workspace orchestration
These files establish the conversation workspace and choose which sandbox backend should own or reopen it.

### `core/src/ufo/harness/sandbox/conversation.py`

`orchestration` · `request handling and turn setup`

A conversation can have files, but those files are not just loose files on the server. They live inside a sandbox: an isolated working area, like a locked workshop for that conversation. This file makes sure everyone uses the same workshop, even if a turn, an attachment upload, and a background job all arrive around the same time.

The central class, ConversationSandbox, decides where the workspace lives. It may be on the normal carrier, which is the service that runs sandboxes, or it may be tied to a user’s connected terminal. It also knows how to resume an older sandbox if the database already stores a handle for it. A handle is a durable label like “backend:id” that says which provider owns the workspace.

A key rule is that reads do not create workspaces. If someone browses files for a conversation that has never opened a sandbox, this file returns “nothing there” instead of silently making a new empty workspace. Writes, by contrast, do open the sandbox if needed.

The file also protects the host file system. When it creates or finds directories, it uses containment checks so a bad path or symbolic link cannot escape the configured workspace root. It limits write sizes, hides Git metadata from listings, and prunes old files from inside the sandbox so cleanup matches what the agent itself would see.

#### Function details

##### `ConversationSandbox._route`  (lines 105–116)

```
def _route(self, stored: str | None) -> tuple[Carrier, str, bool]
```

**Purpose**: Chooses which sandbox provider should be used for a stored sandbox handle. This matters because a workspace must be reopened on the same kind of backend that originally created it, when that backend is still configured.

**Data flow**: It receives an optional stored handle from the database. If the handle names a configured resume backend, it returns that backend’s carrier, name, and off-cluster setting. Otherwise it falls back to this deployment’s normal carrier and backend settings.

**Call relations**: When existing or _opened needs to reach a previously created sandbox, they ask _route where that handle belongs. _route uses sandbox_handle_backend to read the backend part of the stored handle, then hands back the carrier choice that the caller should use.

*Call graph*: called by 2 (_opened, existing); 1 external calls (sandbox_handle_backend).


##### `ConversationSandbox.open`  (lines 118–170)

```
async def open(self, conversation_id: UUID, turn_id: UUID | None, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Opens the conversation’s sandbox for active use, creating it if necessary, and makes sure the database records the winning sandbox handle. This is the main path used when a turn needs a workspace or when something must write into the workspace outside a turn.

**Data flow**: It starts by reading the conversation’s current sandbox handle and sandbox size from the database. It asks _opened to create or attach to a sandbox, builds the durable backend:id handle, and tries to save that handle with _claim. If another caller won the race first, it reopens using the winner’s handle. It returns a SandboxSession, which is the usable connection to the workspace.

**Call relations**: write, write_runtime, and the runtime queue’s sandbox-opening code call open when they need a real workspace. open coordinates _binding, _opened, and _claim so concurrent callers all settle on the same sandbox instead of creating separate workspaces.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 3 (write, write_runtime, _open_sandbox); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 172–234)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Looks for an already-existing sandbox without creating one. It is the safe read path: browsing or reading files should not have the side effect of making a new workspace.

**Data flow**: It reads the stored handle. If there is no handle, it returns None. If the handle points to a client terminal, it tries to attach through a TerminalCarrier. Otherwise it routes the handle to the proper carrier, checks that any needed local directory already exists, and asks the carrier to attach. It returns a SandboxSession if attachment works, or None if the sandbox is not reachable.

**Call relations**: entries, prune, prune_runtime, and read call existing before touching files. existing uses _stored and _route to find the right place, then builds a SandboxSpec and attaches through either a terminal carrier or a normal carrier.

*Call graph*: calls 2 internal fn (_route, _stored); called by 4 (entries, prune, prune_runtime, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 236–246)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Tries to bind an unbound conversation to the user’s connected terminal directory. This lets a conversation use the same local workspace the user can see through their terminal.

**Data flow**: It receives a conversation id and a current working directory. It formats those into a client-backed handle, checks whether the conversation already has any stored handle, and if not tries to save the terminal handle. It returns true only if this call successfully made the binding.

**Call relations**: This function is used when a live terminal connection is being admitted. It relies on _stored to check the current state and _claim to perform the safe database update, so it does not overwrite an existing workspace binding.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 248–259)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Writes bytes into a conversation’s visible /workspace area and returns the path the agent should use to read the file. It is used for things like landing an inbound attachment before a turn runs.

**Data flow**: It receives a conversation id, a relative path, and file content. It first rejects content over the configured size limit. Then it opens the sandbox off-turn, writes the file through the session, and returns the corresponding /workspace path.

**Call relations**: write calls open because writing is allowed to create the workspace if it does not exist yet. It uses workspace_path to turn the relative file name into the in-sandbox path that other code can show to the agent.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.write_runtime`  (lines 261–273)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output into a named runtime area, separate from the ordinary user-visible workspace path. This is useful for files the system needs to store for a conversation but organize under a runtime category.

**Data flow**: It receives a conversation id, category, relative path, and content bytes. It checks the size limit, opens the sandbox off-turn, combines the category and path, writes the runtime file, and returns a display path for that runtime file.

**Call relations**: Like write, it calls open because a write may need to create or resume the sandbox. After the session is open, the session methods perform the actual runtime-file write and path formatting.

*Call graph*: calls 1 internal fn (open).


##### `ConversationSandbox.prune`  (lines 275–286)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files under a workspace prefix, keeping only a chosen number of newest files. This prevents unattended writers, such as logs, from growing forever.

**Data flow**: It receives a conversation id, a relative directory prefix, and a keep count. It attaches only if the sandbox already exists. Then it runs a small Python cleanup program inside the sandbox; if that program reports failure, it raises an error.

**Call relations**: prune calls existing because cleanup should not create a new sandbox just to delete from it. It uses workspace_path to point the in-sandbox cleanup program at the correct /workspace directory.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.prune_runtime`  (lines 288–300)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files from an internal runtime directory, keeping only the newest requested number. It applies the same bounded-cleanup idea as prune, but under a runtime category.

**Data flow**: It receives a conversation id, runtime category, relative prefix, and keep count. It attaches to an existing sandbox if possible, asks the session for the target runtime paths, runs the cleanup program, and raises an error if cleanup fails.

**Call relations**: prune_runtime calls existing so it never creates a workspace on a cleanup-only request. It then delegates path resolution and program execution to the SandboxSession.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox.entries`  (lines 302–341)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists the member-visible files in a conversation’s workspace. It returns clean, relative paths with file size and modification time, suitable for a file browser.

**Data flow**: It starts by attaching to an existing sandbox. If none exists, it returns an empty tuple. Otherwise it asks the sandbox to run a file-glob command that walks /workspace while excluding names such as .git. It checks the returned data, warns if the result was truncated, converts each file record into a WorkspaceFile, sorts by path, and returns the tuple.

**Call relations**: File browsing calls entries. entries relies on existing for the no-side-effect read rule, and on _workspace_rel to convert absolute paths from the sandbox or local carrier into paths relative to the workspace root.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 343–347)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Converts a path reported by a workspace walk into a relative workspace path. This keeps the file browser from exposing container or host absolute paths.

**Data flow**: It receives a sandbox handle and an absolute path. It checks whether the path starts with either /workspace or the handle’s host workspace path, removes that root prefix, and returns the remaining relative path. If the path is outside both roots, it raises an error.

**Call relations**: entries calls _workspace_rel for every file returned by the sandbox’s file listing. This is the final safety check that listed files really came from the workspace area.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 349–357)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Reads one file from an existing conversation workspace, returning its bytes as chunks. It returns None when there is no workspace or no such file.

**Data flow**: It receives a conversation id and relative file path. It attaches only to an existing sandbox, checks whether the file exists, and if so returns the session’s asynchronous byte stream for that file. It does not create a sandbox and does not return data for missing files.

**Call relations**: read is the file-download path. It uses existing to preserve the rule that reads are side-effect-free, then relies on the SandboxSession to test for the file and stream its contents.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 359–418)

```
async def _opened(self, conversation_id: UUID, turn_id: UUID | None, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Performs the actual choice and opening of a sandbox for open. It decides between a terminal-backed workspace and the normal carrier, prepares the right workspace path, and asks the chosen carrier to create or resume the sandbox.

**Data flow**: It receives the conversation id, optional turn id, stored handle, run token, environment variables, and requested sandbox size. It first checks whether the conversation is or should be bound to a client terminal. If so, it creates through TerminalCarrier. Otherwise it routes any stored handle, prepares either an off-cluster path or a protected local directory, adjusts ownership when running as root, and asks the carrier to create the sandbox. It returns the backend name, carrier, and sandbox handle.

**Call relations**: open calls _opened during each attempt to create or resume the workspace. _opened uses _route for stored non-terminal handles and builds SandboxSpec objects that carriers understand.

*Call graph*: calls 1 internal fn (_route); called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 420–435)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates and verifies the host directory used for a conversation workspace on an in-cluster carrier. It exists to stop path tricks, such as symbolic links, from escaping the configured workspace root.

**Data flow**: It receives a conversation id. It ensures the workspace root exists, canonicalizes the configured root, then creates and validates the conversation’s directory under that root using containment checks. It returns the safe directory path.

**Call relations**: _opened uses _provisioned_dir when it needs a local host directory for a sandbox that may write files. The helper delegates the delicate path-safety work to configured_root and contained_dir.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 437–448)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds an already-created conversation workspace directory without making one. This supports read-only flows where absence should simply mean “there is no workspace.”

**Data flow**: It receives a conversation id. It canonicalizes the configured workspace root and checks for the conversation directory inside it. If the directory is missing, it returns None. If the path exists but violates containment rules, the underlying check raises an error.

**Call relations**: existing uses _existing_dir when attaching to a local in-cluster workspace. It runs it in a worker thread because filesystem checks can block the async event loop.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 450–452)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Fetches just the stored sandbox handle for a conversation. It is a small convenience wrapper around the fuller database read.

**Data flow**: It receives a conversation id, calls _binding, takes the handle part of the returned pair, and returns that handle or None.

**Call relations**: existing, claim_terminal, and _claim call _stored when they only need to know what handle the conversation row currently contains. _stored delegates the actual database query to _binding.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 454–475)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the conversation’s current sandbox handle and the owning agent’s requested sandbox size from the database. This gives open the information needed to resume the right workspace or create a new one with the right size.

**Data flow**: It receives a conversation id. Inside a workspace-scoped database transaction, it joins the conversation and agent rows, restricted to the current workspace. If no row is found, it raises an error because the conversation does not belong here. Otherwise it returns the stored handle and sandbox size.

**Call relations**: open calls _binding at the start of the create-or-resume flow, and _stored calls it for simpler handle lookups. It uses workspace_tx for the database connection and ws_current to ensure the lookup stays inside the active workspace.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 477–498)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Safely writes a sandbox handle into the conversation row only if the row still contains the value this caller previously saw. This is the race-prevention step that keeps two simultaneous openers from both claiming different sandboxes.

**Data flow**: It receives a conversation id, the handle value that was previously read, and the new handle to store. It runs a conditional database update: update only if the row still matches the old value. If the update succeeds, it returns the new handle. If it loses the race, it reads and returns the winner’s stored handle; if the handle vanished unexpectedly, it raises an error.

**Call relations**: open uses _claim after creating or attaching to a sandbox, so all concurrent open attempts converge on the one handle in the database. claim_terminal also uses _claim to bind a terminal only when the conversation is still unbound.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### `core/src/ufo/harness/sandbox/select.py`

`orchestration` · `startup`

A “carrier” is the part of the system that actually provides sandboxes, such as the built-in local sandbox runner or an extension-provided remote runner like Docker or E2B. This file is the gatekeeper that turns configuration into real carrier objects the rest of the process can use.

It starts with one guaranteed option: the built-in local carrier. Then it adds any carriers advertised by extension manifests. It refuses unclear situations, such as two carriers claiming the same backend name, because that would make configuration mean two different things.

The file also supports migration between sandbox providers. New sandboxes open on the configured default backend, but old sandbox handles may still point to older backends. The `resume_backends` setting names those older providers so the process keeps them available long enough to reopen existing workspaces. It checks that these names are not duplicates and do not repeat the default backend.

A particularly important safety rule applies to remote carriers, called “off-cluster” here. If a sandbox runs somewhere outside the local process environment, it must have a public HTTPS proxy URL. Without that, the sandbox could bypass the system’s controlled egress path, meaning outbound network access would not be properly credential-injected, blocked by default, or metered. So this file fails loudly instead of falling back silently.

#### Function details

##### `select_carriers`  (lines 25–49)

```
def select_carriers(config: Config, manifests: tuple[Manifest, ...]) -> DeployCarriers
```

**Purpose**: Builds the complete set of sandbox carriers this deployment will use. It picks the default carrier for new sandboxes and prepares extra carriers used only to resume older sandbox handles.

**Data flow**: It receives the application configuration and the extension manifests. It begins with the built-in `local` carrier, adds carrier definitions from the manifests, checks for duplicate or invalid resume backend names, then asks `_built` to turn each selected name into a live carrier object. It returns a `DeployCarriers` bundle containing the default carrier, its description, and the resume-only carriers.

**Call relations**: This is the main entry point of the file. During startup, higher-level setup code calls it after configuration and extension manifests are available. It delegates the detailed validation and construction of each named backend to `_built`, then packages the results into `DeployCarriers` for the rest of the sandbox system to use.

*Call graph*: calls 1 internal fn (_built); 2 external calls (__init__, __init__).


##### `_built`  (lines 52–72)

```
def _built(specs: dict[str, CarrierSpec], config: Config, name: str) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Looks up one configured sandbox backend name and turns it into a real carrier instance. It also enforces safety requirements for remote sandbox providers.

**Data flow**: It receives the known carrier specifications, the configuration, and the backend name to build. It first checks that the name exists. If the carrier is remote, it reads `proxy_public_url` from the sandbox configuration and verifies that it is a usable HTTPS URL. If everything is valid, it calls the carrier factory and returns both the new carrier object and its specification; if not, it raises a clear error.

**Call relations**: `select_carriers` calls this once for the default backend and once for each resume backend. `_built` is the file’s safety checkpoint: it prevents unknown backend names from slipping through and prevents remote sandboxes from starting without the secure public proxy they need.

*Call graph*: called by 1 (select_carriers); 2 external calls (__init__, urlparse).


### `core/src/ufo/harness/sandbox/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to modules inside `ufo.harness.sandbox` using normal Python import paths. Think of it like putting a label on a drawer: the label does not store the tools, but it tells Python that the drawer is part of the organized toolbox. Without this file, depending on the Python version and packaging setup, imports for sandbox code might fail or behave less predictably. Because the file is empty, it does not set up defaults, expose helper names, or run any startup logic.


### Sandbox carriers
These implementations run the same workspace and command model on local folders, connected terminals, Docker containers, or E2B cloud sandboxes.

### `core/src/ufo/harness/sandbox/local.py`

`io_transport` · `request handling`

This file lets the system run sandbox work without Docker, E2B, or any cloud runtime. Think of it as setting up a temporary workshop on the local computer: the conversation gets a real directory for its files, commands run as normal local subprocesses inside that directory, and paths that tools call `/workspace` are translated to that host directory.

The file also builds a safe-ish command environment. It does not pass through the server’s own environment, because that might contain deployment secrets. Instead it creates a scratch home directory, puts the `ufo` client binary on `PATH`, disables host Git credential helpers and prompts, and adds proxy settings so network traffic still goes through the sandbox egress proxy. That proxy arrangement keeps model-key swapping and metering behavior consistent with container-based sandboxes.

A large part of the file is about safe file access. Reads and writes are checked so paths stay inside the workspace or runtime area, and symlinks cannot redirect operations outside those roots. It also installs and verifies “skills,” which are packaged tool resources, using manifests and SHA-256 digests to make sure files match what was declared.

The important tradeoff is clear: this is convenient and zero-dependency, but not isolation. A local subprocess shares the host kernel and network namespace. Real containment comes from container carriers.

#### Function details

##### `_provision_scratch`  (lines 79–103)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates one temporary support area for the whole process. This area holds a scratch home directory and, if available, a copy of the `ufo` client binary so locally run commands can use the same helper tools as container sandboxes.

**Data flow**: It starts with no caller-provided input. It creates a temporary directory, adds `home` and `bin` folders, tries to find the built `ufo` client, and copies it into `bin` with executable permissions. If the client binary is missing, it records a warning and still returns the scratch directory, so only commands that actually need the binary fail later.

**Call relations**: This is used as the default factory for `LocalCarrier`’s scratch directory. Later methods such as `create`, `attach`, and `_base_env` rely on this scratch area to build command environments.

*Call graph*: 4 external calls (Path, mkdtemp, warn, client_binary).


##### `LocalCarrier.ufo_home`  (lines 111–113)

```
def ufo_home(self) -> Path
```

**Purpose**: Returns the local runtime’s private `UFO_HOME` directory. This is where local sandbox support data, such as installed skills, lives.

**Data flow**: It reads the carrier’s scratch directory and appends `home/.ufo` to it. The result is a filesystem path; it does not create files by itself.

**Call relations**: Other methods use this property when seeding skills, loading skills, and creating per-conversation runtime directories.


##### `LocalCarrier.seed_system_skills`  (lines 115–143)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Installs a bundled set of system skills into the local runtime. A skill is a packaged set of files that tools can use; this method replaces old versions safely and records a manifest describing what was installed.

**Data flow**: It receives a zip archive as bytes. It reads `manifest.json`, validates its shape, compares the old and new skill names, removes affected top-level skill directories, extracts each file through containment checks, and writes a new `.system-manifest.json`. The result is an updated skills directory, or an error if the archive is malformed.

**Call relations**: It calls `_system_manifest` to learn what was previously installed, then uses containment helpers to remove and write files safely. Later, `_load_skills` and `_load_system_skill` depend on the manifest and files written here.

*Call graph*: calls 1 internal fn (_system_manifest); 7 external calls (BytesIO, loads, Path, contained_file, contained_relative, contained_remove, ZipFile).


##### `LocalCarrier.load_skills`  (lines 145–155)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asynchronously loads the skills requested for a sandbox turn and returns a command-like result. It converts success or failure into an `ExecResult`, so the rest of the sandbox system can treat skill loading like an operation that produced stdout, stderr, and an exit code.

**Data flow**: It receives a sandbox handle and a payload describing requested system and user skills. It runs the blocking work in a background thread, then returns JSON containing the resolved skill roots on success. If validation or file access fails, it returns exit code 1 with the error text in stderr.

**Call relations**: This is the async public wrapper around `_load_skills`. Callers use it when preparing a sandbox environment that needs skill directories available.

*Call graph*: 3 external calls (__init__, to_thread, dumps).


##### `LocalCarrier._load_skills`  (lines 157–180)

```
def _load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Resolves requested system and user skills into local filesystem directories. It verifies the stored system manifest, checks requested system skills by digest, and installs user-provided skills after validating them.

**Data flow**: It reads the skills root under `UFO_HOME`, loads the system manifest, and reads `system` and `user` entries from the payload. For each system skill, it asks `_load_system_skill` to verify and locate it. For each user skill, it asks `_load_user_skill` to validate, install, and locate it. It returns a dictionary mapping skill names to local root paths.

**Call relations**: It is called by `LocalCarrier.load_skills`. It coordinates the more specific helpers `_load_system_skill`, `_load_user_skill`, and `_system_manifest`.

*Call graph*: calls 3 internal fn (_load_system_skill, _load_user_skill, _system_manifest).


##### `LocalCarrier._load_system_skill`  (lines 182–201)

```
def _load_system_skill(self, root: Path, manifest_skills: Mapping[object, object], name: object, digest: object) -> tuple[str, str] | None
```

**Purpose**: Checks whether a requested system skill is already installed and exactly matches the requested digest. If it matches, it returns the skill’s name and directory; if not, it quietly reports that the skill is unavailable.

**Data flow**: It receives the skills root, the manifest’s skills section, a skill name, and a digest. It validates the types, checks that the path stays inside the skills root, compares the requested digest with the manifest, reads the listed files, recomputes their digest, and returns a `(name, path)` pair only if everything matches.

**Call relations**: It is called by `_load_skills` for each requested system skill. It hands file reading to `_read_skill_files` and digest calculation to `_skill_digest`.

*Call graph*: calls 2 internal fn (_read_skill_files, _skill_digest); called by 1 (_load_skills); 1 external calls (contained_relative).


##### `LocalCarrier._load_user_skill`  (lines 203–236)

```
def _load_user_skill(self, root: Path, system_names: tuple[object, ...], name: object, encoded: object) -> tuple[str, str]
```

**Purpose**: Validates and installs a user-supplied skill. It makes sure the skill name is safe, does not overlap a system skill, and that the provided file contents match their declared digest.

**Data flow**: It receives the skills root, known system skill names, a user skill name, and encoded skill data. It checks the name, rejects conflicts, base64-decodes each file, checks every path is inside that skill, sorts the files, verifies the digest, writes the files into the skills directory, and returns the installed skill name and path.

**Call relations**: It is called by `_load_skills` for each user skill. It relies on `_validate_user_skill_name`, `_skill_digest`, and `_install_user_skill` to split validation, integrity checking, and writing into smaller steps.

*Call graph*: calls 3 internal fn (_install_user_skill, _skill_digest, _validate_user_skill_name); called by 1 (_load_skills); 2 external calls (urlsafe_b64decode, contained_relative).


##### `LocalCarrier._system_manifest`  (lines 239–247)

```
def _system_manifest(root: Path) -> Mapping[str, object]
```

**Purpose**: Reads the local system-skill manifest if it exists. If no manifest has been installed yet, it returns an empty manifest.

**Data flow**: It receives the skills root. It safely opens `.system-manifest.json` under that root; if the file is absent, it returns `{"skills": {}}`. If present, it parses JSON and confirms that the top-level value is a mapping before returning it.

**Call relations**: It is used by `seed_system_skills` before replacing installed system skills, and by `_load_skills` when deciding whether requested system skills are available.

*Call graph*: called by 2 (_load_skills, seed_system_skills); 2 external calls (load, contained_file).


##### `LocalCarrier._read_skill_files`  (lines 250–257)

```
def _read_skill_files(root: Path, name: str, files: list[str]) -> list[tuple[str, bytes]]
```

**Purpose**: Reads the files that make up an installed system skill. It does this through containment checks so the manifest cannot trick the code into reading outside the skills directory.

**Data flow**: It receives the skills root, a skill name, and a list of relative file paths. For each path, it builds `skill_name/path`, confirms it stays under the root, opens the file safely, reads its bytes, and returns a list of `(path, bytes)` pairs.

**Call relations**: It is called by `_load_system_skill`, which then passes the returned contents to `_skill_digest` for integrity verification.

*Call graph*: called by 1 (_load_system_skill); 2 external calls (contained_file, contained_relative).


##### `LocalCarrier._install_user_skill`  (lines 260–266)

```
def _install_user_skill(root: Path, name: str, files: list[tuple[str, bytes]]) -> None
```

**Purpose**: Writes a validated user skill into the local skills directory. It first removes any previous copy of that skill so the installed directory matches the new payload.

**Data flow**: It receives the skills root, a skill name, and already-decoded file contents. It removes the destination skill directory, then writes each file through containment-checked paths, creating parent directories as needed. The output is changed files on disk.

**Call relations**: It is called only after `_load_user_skill` has validated names, paths, and digests. It uses containment helpers to keep the write inside the skills root.

*Call graph*: called by 1 (_load_user_skill); 3 external calls (contained_file, contained_relative, contained_remove).


##### `LocalCarrier._validate_user_skill_name`  (lines 269–272)

```
def _validate_user_skill_name(name: str, root: Path) -> None
```

**Purpose**: Rejects unsafe user skill names. User skills must be a single visible top-level name, not a nested path and not a hidden dot-name.

**Data flow**: It receives a proposed skill name and the skills root. It turns the name into a contained relative path, checks that it has exactly one path part, and rejects names beginning with `.`. It returns nothing on success and raises an error on invalid names.

**Call relations**: It is called by `_load_user_skill` before any user skill files are decoded or written.

*Call graph*: called by 1 (_load_user_skill); 2 external calls (Path, contained_relative).


##### `LocalCarrier._skill_digest`  (lines 275–280)

```
def _skill_digest(files: list[tuple[str, bytes]]) -> str
```

**Purpose**: Computes the integrity fingerprint for a skill. The digest covers both file names and file contents, so changing either changes the result.

**Data flow**: It receives a list of `(path, bytes)` pairs. For each file, it hashes the path and content separately and feeds those hashes into a larger SHA-256 hash. It returns a string like `sha256:<hex digest>`.

**Call relations**: Both `_load_system_skill` and `_load_user_skill` use this to confirm that skill files match the digest declared by a manifest or payload.

*Call graph*: called by 2 (_load_system_skill, _load_user_skill); 1 external calls (sha256).


##### `LocalCarrier.create`  (lines 282–317)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a local sandbox handle for a conversation turn. It prepares the workspace directory, per-conversation runtime directory, proxy certificate, and environment variables commands will inherit.

**Data flow**: It receives a `SandboxSpec` containing paths, IDs, proxy details, a run token, and environment additions. It creates the workspace and runtime directories, writes the proxy certificate into scratch space, builds proxy URLs and base environment variables, adds sentinel model API keys and certificate settings, and returns a `SandboxHandle` describing the ready local sandbox.

**Call relations**: This is the main setup method for a new local sandbox. It calls `_base_env` for the safe command environment and returns the handle later used by `exec`, `write`, `read`, `file_op`, and `dial`.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier._base_env`  (lines 319–342)

```
def _base_env(self) -> dict[str, str]
```

**Purpose**: Builds the minimal environment given to local sandbox commands. It deliberately avoids inheriting the server’s full environment so deployment secrets do not leak into subprocesses.

**Data flow**: It reads only a small allowlist of host variables such as locale and temporary-directory settings. It then adds a scratch `HOME`, `UFO_HOME`, a `PATH` containing the copied `ufo` client, and Git settings that disable host config, credential helpers, and interactive prompts. It returns a dictionary of environment variables.

**Call relations**: It is called by both `create` and `attach`. `create` adds proxy and model-key settings on top of this base; `attach` uses the base environment for read-only access without creating a new workspace.

*Call graph*: called by 2 (attach, create); 1 external calls (Path).


##### `LocalCarrier.attach`  (lines 344–359)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an existing local workspace without creating it. This is useful for browsing or reading a conversation that may or may not already have workspace files.

**Data flow**: It receives a `SandboxSpec`, checks whether the workspace host path is an existing directory, and returns `None` if it is not. If it exists, it builds and returns a `SandboxHandle` pointing at the same workspace and runtime location, with the base local command environment.

**Call relations**: It is the read-only counterpart to `create`. It calls `_base_env` and produces a handle that can be used by later read or file operations.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 361–419)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs one command as a local subprocess inside the workspace. It rewrites logical `/workspace` arguments to real host paths and enforces a timeout.

**Data flow**: It receives a sandbox handle, command arguments, a timeout, and an optional model command label. It finds the workspace root, rewrites command arguments to host paths, adjusts shell commands so the expected `PATH` is visible, starts the subprocess with the sandbox environment, and waits for stdout, stderr, and exit code. If the command times out, it kills the whole process group and returns exit code 124 with `timed out`.

**Call relations**: This is the main command-running path for the local carrier. It uses `_root` to locate the workspace, `host_argv` to translate paths, and `_kill_process_group` when a timeout or cancellation must stop child processes too.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 5 external calls (__init__, create_subprocess_exec, wait_for, quote, host_argv).


##### `LocalCarrier.write`  (lines 421–444)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into the local workspace at a sandbox path. It runs the blocking filesystem work in a background thread so it does not stall the async event loop.

**Data flow**: It receives a handle, a sandbox path, and bytes to write. It hands those to `_write_contained` in a worker thread. On success, the target file in the workspace or runtime area contains the provided bytes.

**Call relations**: This is the public async write method. `_write_contained` performs the actual safe path resolution and atomic file replacement.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 446–449)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the actual safe local file write. It ensures the destination is inside an allowed sandbox root before replacing the file contents.

**Data flow**: It receives a handle, path, and bytes. It converts the sandbox path into a contained name and root using `_contained_name`, opens the target through containment checks, and replaces the file with the new bytes while preserving or applying the intended write mode.

**Call relations**: It is called by `LocalCarrier.write`. It depends on `_contained_name` to decide whether the path belongs under `/workspace` or the runtime root.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 451–462)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes out of a local workspace file. It reads in chunks so large files do not need to be loaded into memory all at once.

**Data flow**: It receives a handle and sandbox path. It opens a safe contained source file in a worker thread, repeatedly reads fixed-size chunks, yields each chunk to the caller, and closes the file in a worker thread when finished or interrupted.

**Call relations**: This is the public async read method. It relies on `_contained_source` to open the file safely before streaming begins.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 464–474)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Opens a file for safe reading inside an allowed sandbox root. It refuses missing paths and paths that containment checks cannot prove safe.

**Data flow**: It receives a handle and path. It maps the path to a contained root with `_contained_name`, opens the target through containment checks, verifies it exists, and returns a binary file reader. If a contained path component is missing, it raises `FileNotFoundError`.

**Call relations**: It is called by `LocalCarrier.read` before chunked streaming. The returned open file is no longer dependent on a path lookup that could be swapped later.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 476–481)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level file operation through the `ufo fs` helper. This gives local sandboxes the same file-tool behavior as sandboxes that run inside containers.

**Data flow**: It receives a handle, an operation name, and operation parameters. It delegates to `ufo_fs_file_op`, which runs the client-side file operation against the workspace and returns a dictionary result.

**Call relations**: This is the local carrier’s bridge to the shared file-tool implementation. It depends on the scratch `PATH` prepared earlier so the `ufo` client can be found.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `LocalCarrier.dial`  (lines 483–489)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Builds a connection target for a service started by a local sandbox command. Because local commands share the host network, a sandbox port is simply the same port on `127.0.0.1`.

**Data flow**: It receives a handle and port number. It returns a `DialTarget` with host `127.0.0.1:<port>` and TLS disabled. It does not open the connection itself.

**Call relations**: Callers use this when they need to reach an HTTP service that a command started. Unlike container carriers, there is no per-conversation port isolation here.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 492–497)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Force-stops a command and any child processes in its process group. This prevents timed-out or cancelled shell commands from leaving runaway background work behind.

**Data flow**: It receives an async subprocess object. It sends `SIGKILL` to the process group whose ID is the child process ID, ignores the case where the process is already gone, and waits for the process to finish cleanup.

**Call relations**: It is called by `LocalCarrier.exec` when a command times out or when execution is interrupted. `exec` starts commands in a new session specifically so this helper can kill the whole group.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 500–503)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the host directory that backs the sandbox workspace. It raises an error if the handle does not have a workspace host path, because the local carrier cannot work without one.

**Data flow**: It receives a sandbox handle. If `workspace_host_path` is missing, it raises a runtime error; otherwise it converts that path string into a `Path` object and returns it.

**Call relations**: It is used by `LocalCarrier.exec` to choose the subprocess working directory, and by `_contained_name` when mapping `/workspace` paths to the host filesystem.

*Call graph*: called by 2 (exec, _contained_name); 1 external calls (Path).


##### `_contained_name`  (lines 506–512)

```
def _contained_name(handle: SandboxHandle, path: str) -> tuple[PurePosixPath, Path]
```

**Purpose**: Maps an incoming sandbox path to a safe relative path plus the local root it belongs under. It only accepts paths inside `/workspace` or inside the handle’s runtime root.

**Data flow**: It receives a handle and a path string. It treats the path as a POSIX-style sandbox path, checks whether it starts under `/workspace`, and if so returns the path relative to the workspace plus the workspace root. If it instead starts under the runtime root, it returns the runtime-relative path plus that root. Anything else is rejected with a clear error.

**Call relations**: It is called by `_write_contained` and `_contained_source` before touching the filesystem. It uses `_root` when the path belongs to the workspace.

*Call graph*: calls 1 internal fn (_root); called by 2 (_contained_source, _write_contained); 2 external calls (Path, PurePosixPath).


### `core/src/ufo/harness/sandbox/terminal.py`

`io_transport` · `request handling and cross-cutting sandbox operations`

Most sandbox backends are reached by dialing a container or remote machine. A member’s own terminal is different: the server cannot dial it directly, so it must send a request down the terminal’s already-open connection and wait for the client to answer on its next request. This file is that meeting place, like a staffed pickup counter where one side drops off a job ticket and the other side later brings back the result.

The central piece is the terminal transport. It remembers, per conversation, which terminal is connected, what working directory it represents, and whether an operation is already in progress. Operations are deliberately serial: one command or file operation runs at a time, so replies cannot get mixed up. If the browser or terminal connection drops during a pause, the pending operation is kept by conversation ID rather than by connection, so the next reconnect can continue the same exchange.

The file also defines TerminalCarrier, the sandbox adapter that turns normal sandbox actions, such as exec, read, write, and file browsing, into terminal operations. It rewrites logical /workspace paths to the member’s real directory, stages large write bodies outside the directive line, decodes command replies, and special-cases document rendering and file-tree scans. Without this file, a terminal-bound workspace would either be unreachable or would lose in-flight tool calls whenever the client briefly reconnects.

#### Function details

##### `TerminalTransport.connect`  (lines 250–256)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Defines the transport contract for announcing that a member’s terminal connection is now present for a conversation. Implementations use it to record where that terminal is standing and who is allowed to answer its operations.

**Data flow**: Input is a conversation ID, current working directory, optional member ID, and optional runtime ID. An implementation records that binding so later sends can find the terminal. It returns nothing, but it changes the transport’s shared state.

**Call relations**: Surface routes call this kind of method when a client connection is held open. Later sandbox actions use the recorded binding through arrived, send, staged, and resolve.


##### `TerminalTransport.disconnect`  (lines 258–258)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Defines how a transport is told that a terminal connection has gone away. This lets the implementation remove idle bindings without losing an operation that is still waiting for a reply.

**Data flow**: Input is the conversation ID whose connection ended. The implementation updates its connection count or removes the slot if nothing is pending. It returns nothing.

**Call relations**: It is the counterpart to connect. Surface routes use it when a held terminal stream closes, while send may still keep pending work alive until it finishes or times out.


##### `TerminalTransport.workspace`  (lines 260–260)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Defines a quick lookup for the workspace currently bound to a conversation. It is useful when code needs to know whether a terminal is connected and where it is rooted.

**Data flow**: Input is a conversation ID. The implementation reads its binding table and returns terminal workspace details, or nothing if no terminal is known.

**Call relations**: This is a read-only view of the state created by connect. It complements arrived, which can wait for a terminal instead of only checking immediately.


##### `TerminalTransport.arrived`  (lines 262–262)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Defines how callers wait for a terminal to reconnect or confirm that none is present. This matters because reconnects are normal between held client requests.

**Data flow**: Input is a conversation ID and a grace period in seconds. The implementation either returns the terminal workspace once it appears, or returns nothing when the grace period expires.

**Call relations**: TerminalCarrier.create and TerminalCarrier.attach rely on this behavior through the transport. Terminals.send also uses it before trying to deliver an operation.


##### `TerminalTransport.send`  (lines 264–273)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Defines the main request-and-reply operation: ask the connected terminal to do something and wait for its bytes back. This is the heart of running commands and file actions on the member’s machine.

**Data flow**: Inputs describe the operation type, timeout, optional operation name, target path or argument, JSON parameters, and optional staged body bytes. The transport delivers that request to the terminal and returns the reply bytes, or raises an error if no terminal answers.

**Call relations**: TerminalCarrier uses this contract for exec, read, write, skills, and file operations. The client-facing routes pair it with next_op, staged, and resolve.


##### `TerminalTransport.next_op`  (lines 275–277)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Defines how the connected client asks, “What operation should I run next?” It lets the server hand one pending command or file request to the terminal.

**Data flow**: Input is a conversation ID and optionally an operation ID that should not be repeated. The implementation waits until an operation is available and returns its directive.

**Call relations**: This is the receiving half of send. The surface’s held terminal stream calls it, then the client runs the operation and later answers through resolve.


##### `TerminalTransport.staged`  (lines 279–281)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Defines how the terminal retrieves bytes that were staged for an operation, such as file content for a write. Large data is fetched separately so it does not have to ride inside the small operation directive.

**Data flow**: Inputs are conversation ID, operation ID, and optional member ID. The implementation returns the matching staged bytes only if the operation and member are valid; otherwise it returns nothing.

**Call relations**: A client that receives a write-style operation can call this path to fetch the body. It is tied to send, which stores the body, and guarded by the same conversation and member checks used by resolve.


##### `TerminalTransport.resolve`  (lines 283–290)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Defines how the client answers an operation after running it. It reports either reply bytes or a failure message from the terminal-side operation.

**Data flow**: Inputs identify the conversation and operation, carry reply bytes, optionally carry a failure string, and optionally identify the member. The implementation wakes the waiting sender if the answer matches the current operation and returns whether it accepted the answer.

**Call relations**: This is the answer path for next_op. TerminalCarrier methods are waiting inside send, and resolve is what lets them continue with the terminal’s result.


##### `TerminalTransport.in_flight`  (lines 292–292)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Defines a way to inspect the operation currently waiting for a reply. This is mostly useful for tests or operator tooling.

**Data flow**: Input is a conversation ID. The implementation reads current state and returns the pending operation, or nothing if there is none.

**Call relations**: It observes the same state that send creates and resolve clears. It does not drive the normal client flow, but helps external code see what is stuck or active.


##### `_wake`  (lines 295–304)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: Wakes an asynchronous waiter from another thread safely. This is needed because the server workflow loop and the client connection loop may be different event loops on different threads.

**Data flow**: Input is a stored future plus its owning event loop, and an answer object. The function schedules a small callback on the correct loop so the future receives the answer there. It returns nothing, but it causes the waiting coroutine to resume.

**Call relations**: Terminals.connect uses it to wake code waiting for a terminal arrival. Terminals.send uses it to hand an operation to a watching client or release queued senders. Terminals.resolve uses it to deliver the client’s reply to the original sender.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 300–302)

```
def _set() -> None
```

**Purpose**: This inner callback actually sets the result on the future, but only after it is running on the future’s own event loop. It avoids touching asyncio state from the wrong thread.

**Data flow**: It reads the future and answer captured by _wake. If the future is not already complete, it stores the answer as the future’s result. It returns nothing.

**Call relations**: _wake schedules this callback with the event loop’s thread-safe scheduling hook. No outside code calls it directly.


##### `Terminals.connect`  (lines 320–342)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that a terminal connection is available for a conversation. It also wakes any server work that was waiting for the member’s terminal to reconnect.

**Data flow**: Inputs are conversation ID, current directory, optional member ID, and optional runtime ID. The method creates or updates the conversation slot, increments the connection count, and wakes arrival waiters. It returns nothing.

**Call relations**: Client-facing surface code calls this when a terminal stream arrives. It prepares state used by arrived and send, and it calls _wake so waiting tasks can continue.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 344–351)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Notes that one terminal connection for a conversation has closed. It removes the slot only when no connection remains and no operation is waiting for a reply.

**Data flow**: Input is the conversation ID. The method lowers the connection count and may delete the slot if it is idle. It returns nothing.

**Call relations**: This pairs with Terminals.connect. It lets brief reconnects happen without automatically destroying in-flight operation state.


##### `Terminals.workspace`  (lines 353–360)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the currently bound terminal workspace for a conversation, if one is known. It gives callers a snapshot of the terminal’s directory, member, and runtime identity.

**Data flow**: Input is a conversation ID. The method reads the slot under a lock and returns a TerminalWorkspace object or nothing. It does not change state.

**Call relations**: This is a simple lookup over the state created by connect. Code that needs to wait for reconnects uses arrived instead.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 362–386)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal to be connected to a conversation. This prevents normal client reconnect gaps from being mistaken for a missing terminal.

**Data flow**: Inputs are a conversation ID and a grace period. The method checks for an existing slot; if absent, it registers a future and waits until connect wakes it or the deadline passes. It returns workspace details or nothing.

**Call relations**: Terminals.send calls this before sending an operation. TerminalCarrier uses the transport’s arrived behavior when creating or attaching to a terminal-backed sandbox. If waiting times out, it asks _drop_arrival to remove the unused waiter.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 388–393)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: Removes an arrival waiter that no longer needs to be woken. This keeps the waiting list clean after a timeout.

**Data flow**: Inputs are a conversation ID and the future to remove. The method filters that future out of the stored arrival waiters and deletes the list if it becomes empty. It returns nothing.

**Call relations**: Only Terminals.arrived uses this helper, after the wait has expired or no time remains.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 395–470)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one operation to the connected terminal and waits for the reply. It also enforces the rule that each conversation runs only one terminal operation at a time.

**Data flow**: Inputs describe the operation and optional body bytes. The method waits for a terminal, waits for its turn, stores the operation in the slot, wakes any client watcher, waits for resolve to answer, and returns reply bytes. On timeout or disappearance it raises a terminal-gone style error, and in all cases it clears the slot for the next operation.

**Call relations**: TerminalCarrier methods depend on this for commands, reads, writes, file operations, and skill loading. It calls arrived and _take_turn before publishing the operation, uses _wake to notify the client side, and is completed when Terminals.resolve wakes its reply future.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 6 external calls (__init__, __init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 472–503)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: Waits until this sender owns the conversation’s single operation slot. It prevents two requests from being delivered to the same terminal at once.

**Data flow**: Inputs are the conversation ID, the caller’s event loop, and the operation timeout. If the slot is free, it marks it busy and returns. If another operation is running, it queues a future and waits until the slot is released or the deadline expires.

**Call relations**: Terminals.send calls this before creating a TerminalOp. When send finishes, it wakes queued waiters so they can compete for the next turn.

*Call graph*: called by 1 (send); 6 external calls (__init__, __init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 505–530)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Lets the connected terminal stream wait for the next server operation to perform. It also avoids redelivering an operation that the same request just answered.

**Data flow**: Inputs are a conversation ID and optionally an operation ID to exclude. The method returns an undelivered pending operation immediately, or stores the caller as the current watcher and waits. When a sender publishes an operation, the watcher receives it.

**Call relations**: This is the client-facing half of Terminals.send. The stream calls next_op, runs the returned operation locally, and later answers it through resolve.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 532–544)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Returns the staged bytes attached to the current operation, usually the content for a write. It only serves bytes for the exact operation and allowed member.

**Data flow**: Inputs are conversation ID, operation ID, and optional member ID. The method checks the current slot, operation ID, and member gate; if all match, it returns the stored body bytes. Otherwise it returns nothing.

**Call relations**: Terminals.send stores the body, and the client retrieves it through this method while processing the operation from next_op. The data is cleared when send finishes.


##### `Terminals.in_flight`  (lines 546–551)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Shows the operation currently waiting for a terminal reply. It is a diagnostic or test hook rather than part of the usual request path.

**Data flow**: Input is a conversation ID. The method reads the slot and returns its current operation, or nothing if no operation is active. It makes no changes.

**Call relations**: It observes state created by Terminals.send and cleared after resolve or timeout.


##### `Terminals.resolve`  (lines 553–582)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts the terminal client’s answer for the current operation and wakes the server code waiting in send. It rejects stale, duplicate, wrong-member, or wrong-operation replies.

**Data flow**: Inputs identify the conversation and operation, provide reply bytes, optionally provide a failure message, and optionally identify the member. If the answer matches the live operation, the method marks it resolved and wakes the waiting sender with either bytes or a TerminalOpFailed value. It returns true if accepted and false otherwise.

**Call relations**: This completes the operation delivered by next_op. It calls _wake so the send coroutine resumes on its own event loop.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 598–645)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a sandbox handle for a workspace that is actually the member’s connected terminal. It verifies that the connected terminal is in the expected directory before letting the run proceed.

**Data flow**: Input is a sandbox specification containing conversation, workspace path, proxy settings, token, and environment. The method waits for the terminal, checks its directory, builds proxy environment variables, and returns a SandboxHandle. If no suitable terminal is connected, it raises an error.

**Call relations**: Higher-level sandbox setup calls this when choosing the terminal-backed carrier. The returned handle is then used by exec, read, write, file_op, and other carrier methods.

*Call graph*: 4 external calls (__init__, __init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 647–662)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reattaches to an already-bound terminal workspace outside the main turn, for example for file browsing or background writes. It does not wait long; it only succeeds if the current binding matches the requested resume path.

**Data flow**: Input is a sandbox specification with conversation and resume information. The method checks for an arrived terminal immediately, compares its directory, and returns a SandboxHandle or nothing.

**Call relations**: Off-turn features use this to reach the same terminal-backed workspace. If it succeeds, the returned handle feeds the same read, write, and file operation methods as create.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 664–690)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs a command on the member’s machine through the terminal-backed sandbox. It rewrites logical workspace paths to the member’s real directory before sending the command.

**Data flow**: Inputs are a sandbox handle, command arguments, timeout, and optional model-written command text for safety classification. The method finds the real root, converts arguments for the host, optionally prepares safety arguments, and delegates to _exec. It returns an ExecResult.

**Call relations**: Sandbox callers use this as the normal command execution entry for a terminal carrier. It relies on _root, host argument rewriting, and then TerminalCarrier._exec for the actual terminal round trip.

*Call graph*: calls 2 internal fn (_exec, _root); 1 external calls (host_argv).


##### `TerminalCarrier.load_skills`  (lines 692–703)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asks the connected client to load skill files under the client’s UFO home directory. It wraps the terminal response as a successful execution result.

**Data flow**: Input is a sandbox handle and a JSON-like payload describing skills. The method serializes the payload, sends a skills operation to the terminal, and turns the reply bytes into stdout in an ExecResult. If the terminal reports failure, it raises a runtime error.

**Call relations**: Higher-level skill setup calls this through the carrier. It uses the transport’s send operation directly rather than going through shell execution.

*Call graph*: 2 external calls (__init__, dumps).


##### `TerminalCarrier._exec`  (lines 705–745)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, safety_argv: tuple[str, ...] | None=None) -> ExecResult
```

**Purpose**: Runs command arguments that have already been converted to real member-machine paths. It is the lower-level execution helper used when callers must avoid a second workspace rewrite.

**Data flow**: Inputs are a sandbox handle, concrete command arguments, timeout, and optional safety arguments. The method sends an exec operation with argv and environment, parses the JSON reply, decodes base64 stdout and stderr, normalizes timeout exit codes, and returns an ExecResult.

**Call relations**: TerminalCarrier.exec calls this after path conversion. TerminalCarrier._enumerate also calls it for file-tree listing commands that build their own exact host paths.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (_enumerate, exec); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 747–760)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file in the member’s workspace through the terminal. The bytes are staged separately instead of being embedded in the operation directive.

**Data flow**: Inputs are a sandbox handle, logical path, and content bytes. The method maps the logical path to the client’s real path, sends a write operation with the content body, and returns nothing on success. Terminal-reported failures become operating-system style errors.

**Call relations**: Sandbox file-write callers use this. It depends on _client_path for path mapping and on the transport’s send method to deliver both the directive and staged body.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 762–778)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a file from the member’s workspace through the terminal. It yields the reply in chunks so callers can consume it like other sandbox read streams.

**Data flow**: Inputs are a sandbox handle and logical path. The method maps the path, sends a read operation, receives the full file body as bytes, and yields it in one-megabyte chunks. Missing files become FileNotFoundError; other terminal failures become OSError.

**Call relations**: Sandbox file-read callers use this. It uses _client_path before sending, and it relies on the terminal client to return the file body through resolve.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 780–868)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level UFO file operation, such as read, grep, glob, or changes, where the files actually live: on the member’s machine. It sends operation parameters to the client rather than shipping code.

**Data flow**: Inputs are a sandbox handle, operation name, and parameter dictionary. The method rewrites workspace paths, optionally reads and renders office documents, optionally precomputes tree or git-change listings, sends the file operation, parses the JSON reply, and returns the result dictionary. Reported operation errors become Python exceptions.

**Call relations**: The file-tool layer calls this for terminal-backed workspaces. It uses _root and _under_root for path mapping, _enumerate for walk-style operations, the transport’s send for the actual client operation, and _reply_object to validate the reply.

*Call graph*: calls 4 internal fn (_enumerate, _reply_object, _root, _under_root); 2 external calls (dumps, PurePosixPath).


##### `TerminalCarrier._enumerate`  (lines 870–891)

```
async def _enumerate(self, handle: SandboxHandle, op: str, walk_root: str, program: str, arguments: tuple[str, ...]=()) -> None
```

**Purpose**: Runs a shell snippet on the member’s machine to create a file listing that a later file operation will read. This keeps the server, not the client, in charge of exactly what gets walked.

**Data flow**: Inputs are a handle, operation name, walk root, shell program text, and optional arguments. The method quotes the root, runs the snippet through _exec, and raises an error if the listing command fails unexpectedly. It returns nothing when the listing file has been prepared.

**Call relations**: TerminalCarrier.file_op calls this before grep, glob, or changes operations. It delegates actual command execution to _exec.

*Call graph*: calls 1 internal fn (_exec); called by 1 (file_op); 1 external calls (quote).


##### `TerminalCarrier.dial`  (lines 893–897)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Refuses attempts to expose a network port from a terminal-backed sandbox. A member’s own terminal is not a remote container with dialable service ports.

**Data flow**: Inputs are a sandbox handle and port number. The method does not inspect them further; it always raises SandboxUnreachable with an explanatory message. Nothing is returned.

**Call relations**: Sandbox networking callers may try this through the carrier interface. This implementation tells them to use a remote carrier instead.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 900–908)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: Extracts one captured command stream, such as stdout or stderr, from an exec reply. The client sends these streams as base64 text, which is a safe text form for arbitrary bytes.

**Data flow**: Inputs are the parsed reply dictionary and stream name. The function looks for the matching base64 field, decodes it into bytes, and returns those bytes. If the field is missing or malformed, it raises an error instead of pretending the stream was empty.

**Call relations**: TerminalCarrier._exec calls this after _reply_object has parsed the terminal’s JSON reply.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 911–918)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: Parses a terminal reply that is expected to be a JSON object. It makes sure the client sent the shape the server understands.

**Data flow**: Inputs are raw reply bytes and the operation name for error messages. The function decodes the bytes as UTF-8, parses JSON, checks that the result is a dictionary, and returns it. Invalid JSON or non-object JSON raises a runtime error.

**Call relations**: TerminalCarrier._exec uses this for command replies. TerminalCarrier.file_op uses it for file-operation replies.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 921–924)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: Returns the real host directory that backs /workspace for a terminal sandbox. It fails loudly if a handle does not have such a directory.

**Data flow**: Input is a SandboxHandle. The function reads its workspace_host_path and returns it, or raises an error if it is missing. It does not change anything.

**Call relations**: TerminalCarrier.exec and file_op use this before rewriting paths. _client_path also uses it as the root for mapping a single path.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 927–933)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: Maps a logical /workspace path to the member’s real bound directory. It only strips the leading /workspace prefix, avoiding accidental replacements in the middle of a path.

**Data flow**: Inputs are a sandbox handle and a path string. The function gets the handle’s root directory and passes root plus path to _under_root. It returns the mapped client-side path.

**Call relations**: TerminalCarrier.read and write use this before sending file paths to the terminal client.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 936–941)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: Performs the actual path mapping from /workspace/... to root/.... Paths outside /workspace are left unchanged.

**Data flow**: Inputs are the real root directory and a path string. The function treats the path as a POSIX-style path, checks whether it is under /workspace, and returns either the original path or the corresponding path under the real root.

**Call relations**: _client_path uses this for reads and writes. TerminalCarrier.file_op uses it directly to rewrite path parameters inside file-operation payloads.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox creation, command execution, file access, and idle reclaim`

A sandbox is the safe workspace where an agent can run commands and read or write files. This file provides the Docker version of that sandbox. Think of each conversation as getting its own rented workshop: Docker supplies the workshop, the host provides the shared `/workspace` folder, and this carrier opens the door only when work needs to happen.

The main class, `DockerCarrier`, creates a container with a predictable name based on the conversation ID. If the container already exists, it attaches to it instead of making a new one. If it was stopped to save memory and network resources, it starts it again. Every command is run with fresh proxy environment variables, so one turn’s network token is not accidentally reused by a later turn.

The file also cleans up idle containers. It stops them, rather than deleting them, so the workspace can survive and be resumed later. It creates a private Docker network per conversation, installs the proxy certificate inside the container, prepares the mounted folders and permissions, and streams file reads and writes through Docker commands. Without this file, deployments configured for Docker sandboxes could not safely isolate conversations, reclaim idle resources, or enforce proxied network access.

#### Function details

##### `_docker`  (lines 79–93)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and returns its exit code, standard output, and error output. It is the small shared doorway through which the rest of this file talks to Docker.

**Data flow**: It receives Docker arguments, optional input bytes, and a timeout. It starts `docker ...`, feeds the input to it, waits for completion, and returns the command’s result. If Docker takes too long, it kills the process and returns a special timeout code with a short error message.

**Call relations**: Almost every Docker-facing method calls this helper when it needs to inspect containers, create networks, execute commands, install certificates, or stop resources. It hides the repetitive subprocess work so the carrier methods can focus on sandbox behavior.

*Call graph*: called by 13 (_death_report, _ensure_network, _exec_with, _held_id, _install_ca, _prepare_mounts, _reclaim_idle, _release, _revive, _running_id (+3 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 106–219)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or reconnects to the Docker container for a conversation. This is used when a sandbox must be ready to run commands for a new turn.

**Data flow**: It receives a sandbox specification with the conversation ID, image, workspace path, proxy details, run token, and environment variables. It first reclaims old idle containers, builds the per-command proxy environment, looks for an already running or stopped container, revives one if possible, or starts a new Docker container and network. It returns a `SandboxHandle`, which is the project’s reference to the usable sandbox.

**Call relations**: This is the main setup path for Docker sandboxes. It calls helpers to find existing containers, revive stopped ones, make networks, install the proxy certificate, prepare mounted folders, and clean up if creation fails.

*Call graph*: calls 9 internal fn (_ensure_network, _install_ca, _network_name, _prepare_mounts, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier.attach`  (lines 221–250)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an existing conversation container without creating a new one. It is useful for read-style access where absence should simply mean “there is no sandbox to attach to.”

**Data flow**: It receives a sandbox specification, checks whether the named container is running, and if not, checks whether a stopped version exists. If possible it revives the stopped container and prepares its mounts. It returns a `SandboxHandle` when successful, or `None` if there is no usable container.

**Call relations**: This is the gentler companion to `create`. It uses the same running/stopped lookup and revive helpers, but unlike `create` it never runs a fresh container and converts several failures into `None`.

*Call graph*: calls 4 internal fn (_prepare_mounts, _revive, _running_id, _stopped_id); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier._reclaim_idle`  (lines 252–320)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops containers and frees Docker networks for conversations that have been idle too long. This keeps the host from running out of memory or Docker bridge networks.

**Data flow**: It receives the conversation currently being opened and marks it as recently used. It asks Docker which UFO containers and networks exist, records any it did not already know about, finds conversations that have not been touched recently and have no command in progress, and then tries to release each stale one. If a release fails, it records the conversation again so a later create can retry.

**Call relations**: `create` calls this before opening a sandbox. It relies on `_held_id` to find containers and `_release` to stop containers and remove networks, while using per-conversation locks so reclaim does not collide with revive.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 322–336)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs a normal command inside the sandbox container using that turn’s network proxy settings. This is the standard way tools execute shell commands in the Docker sandbox.

**Data flow**: It receives a sandbox handle, command arguments, timeout, and optional model command label. It converts the handle’s egress environment into Docker `--env` options and passes everything to `_exec_with`. The result is an `ExecResult` containing output text, error text, exit code, and timeout information.

**Call relations**: Higher-level sandbox users call this for ordinary command execution. It delegates the real Docker execution, retry, timeout translation, and idle bookkeeping to `_exec_with`.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier.exec_skill`  (lines 338–342)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a skill-related command inside the container as the root user. This is for server-controlled setup or synchronization work that needs elevated permissions.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It passes those to `_exec_with` with Docker options that choose the root user. It returns the same kind of execution result as normal commands.

**Call relations**: This is a specialized wrapper around `_exec_with`. It shares the same lifecycle protection and retry behavior as `exec`, but changes the Docker options for privileged skill operations.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier._exec_with`  (lines 344–383)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, options: tuple[str, ...]) -> ExecResult
```

**Purpose**: Performs the actual `docker exec` call and records that the container is busy while the command runs. It also retries once if the container had been stopped and can be revived.

**Data flow**: It receives a sandbox handle, command arguments, timeout, and Docker execution options. It increments the in-flight counter, marks the conversation as recently touched, runs the command in `/workspace`, revives and retries if Docker says the container is not running, then turns Docker’s raw result into an `ExecResult`. Finally, it decrements the in-flight counter and refreshes the touch time.

**Call relations**: Both `exec` and `exec_skill` feed into this function. It calls `_docker` to run the command and `_revive` when reclaim has stopped the container underneath an operation.

*Call graph*: calls 2 internal fn (_revive, _docker); called by 2 (exec, exec_skill); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 385–403)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file as seen from inside the sandbox container. It uses a protected copy-in program instead of putting file contents or unsafe shell paths on the command line.

**Data flow**: It receives a sandbox handle, a target path, and byte content. It marks the container as busy, starts the write through `_write_started`, revives and retries if the container was stopped, and raises an `OSError` if the write still fails. It changes the file inside the sandbox view of the workspace or runtime area.

**Call relations**: Sandbox file-upload paths call this when they need to place content in the container. It delegates the actual Docker stdin transfer to `_write_started` and uses `_revive` for recovery.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 405–423)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Starts one attempt to copy bytes into the container. It chooses the safe root for the target path and streams the content through standard input.

**Data flow**: It receives the sandbox handle, destination path, and content bytes. It decides whether the path belongs under the sandbox runtime root or the normal workspace, then runs a Python copy-in helper inside the container with the bytes as stdin. It returns the Docker exit code and error output.

**Call relations**: `write` calls this for the first write attempt and again after a successful revive. This function uses `_docker` for the actual `docker exec -i` operation.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `DockerCarrier.read`  (lines 425–459)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox container in chunks. It reads through the container so the caller sees the same filesystem view the sandbox sees.

**Data flow**: It receives a sandbox handle and path, marks the container as busy, and starts a `cat` stream through `_read_started`. It yields file bytes chunk by chunk. After the stream ends, it checks for errors, revives and retries if the container was stopped before reading, translates familiar filesystem error messages into `OSError`, or raises a runtime error with diagnostic details.

**Call relations**: Higher-level file download or browse paths use this method. It depends on `_read_started` for streaming, `_revive` for stopped-container recovery, and `_death_report` when a failed read gives no useful stderr.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 461–503)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Builds one streaming read attempt and a place to record its final failure, if any. This separation lets `read` retry cleanly without reusing a half-finished async generator.

**Data flow**: It receives a sandbox handle and file path. It returns an async byte stream plus a small failure list that will be filled only after the Docker `cat` process finishes with a non-zero exit code. The caller consumes the stream first, then checks the failure list.

**Call relations**: `read` calls this for the first read attempt and, if needed, for a retry after revive. The inner `stream` function does the actual subprocess work.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 476–501)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `docker exec cat <path>` and yields the file bytes as they arrive. It also makes sure an abandoned read does not leave a Docker exec process running in the background.

**Data flow**: It starts a Docker subprocess with stdout and stderr pipes. It repeatedly reads bounded chunks from stdout and yields them. When stdout ends, it reads stderr and waits for the exit code; if the exit code failed, it records the failure. If the caller stops early, it kills and reaps the subprocess.

**Call relations**: This inner generator is returned by `_read_started` and consumed by `read`. It is the low-level streaming mechanism behind Docker sandbox file reads.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 505–523)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Gathers Docker’s own state report for a container after a read dies without an error message. This gives investigators a concrete clue, such as whether the container exited or was killed for using too much memory.

**Data flow**: It receives a sandbox handle, runs `docker inspect` for the container, and formats either the reported state or the inspection failure. It returns a human-readable string to append to an error.

**Call relations**: `read` calls this only when `cat` failed silently. It uses `_docker` to ask Docker for facts instead of guessing why the read failed.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 525–531)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured UFO filesystem operation inside the Docker sandbox. This supports file actions beyond simple raw read and write.

**Data flow**: It receives a sandbox handle, an operation name, and operation parameters. It passes the carrier and request to the shared `ufo_fs_file_op` helper, which runs the project’s `ufo fs` client inside the sandbox. It returns the operation’s result as a dictionary.

**Call relations**: Higher-level file APIs can call this when they need the common UFO filesystem behavior. This method plugs the Docker carrier into that shared helper.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `DockerCarrier.dial`  (lines 533–540)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that this Docker carrier cannot expose a service port from inside the sandbox to the outside world. It prevents callers from assuming browser or dev-server ports are reachable through Docker here.

**Data flow**: It receives a sandbox handle and a port number, but does not connect to anything. It raises `SandboxUnreachable` with an explanation that this carrier has no external per-port route.

**Call relations**: Code that wants to reach an in-sandbox service may call this through the carrier interface. For Docker it stops the flow immediately and tells the caller to use a remote carrier that supports port access.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 542–556)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation’s container and removes its Docker network. This frees the host resources that idle sandboxes consume while keeping the stopped container itself available for later restart.

**Data flow**: It receives a conversation ID and possibly a container ID. If there is a container, it asks Docker to stop it. Then it removes the conversation’s network, treating an already-missing network as acceptable. It returns `true` only when the release reached the desired state.

**Call relations**: `_reclaim_idle` calls this under a lifecycle lock. It uses `_network_name` to identify the network and `_docker` to perform the stop and network removal.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 558–578)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Restarts a stopped container so work can continue after idle reclaim. It rebuilds the network connection first, then starts the container.

**Data flow**: It receives a conversation ID and container ID. Under that conversation’s lifecycle lock, it marks the conversation as touched, ensures the Docker network exists, connects the container to it, and starts the container. It returns whether the container was successfully started.

**Call relations**: `create`, `attach`, `_exec_with`, `write`, and `read` all call this when they find a stopped container. It coordinates with `_release` so a restart and an idle stop cannot interleave halfway.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (_exec_with, attach, create, read, write).


##### `DockerCarrier._held_id`  (lines 580–588)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a given container name in any state. Reclaim uses this when it needs to stop whatever still holds resources.

**Data flow**: It receives a container name, asks Docker for matching containers whether running or exited, and returns the found ID or `None`. If the Docker query itself fails, it raises an error.

**Call relations**: `_reclaim_idle` calls this before releasing a stale conversation. It uses `_docker` to query Docker accurately rather than assuming a missing result when Docker itself failed.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 590–599)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a named container that exists but is stopped. This is how the carrier knows whether it can resume an old sandbox.

**Data flow**: It receives a container name, asks Docker for matching exited containers, and returns the container ID or `None`. Docker command failures become runtime errors.

**Call relations**: `create` and `attach` call this after they do not find a running container. If it returns an ID, those callers can try `_revive` instead of creating from scratch.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 601–612)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a named container that is currently running. It distinguishes “no container” from “Docker query failed.”

**Data flow**: It receives a container name, asks Docker for a running container with that exact name, and returns the ID or `None`. If Docker cannot complete the query, it raises an error instead of pretending the container is absent.

**Call relations**: `create` and `attach` use this as their first lookup. Its accurate failure behavior prevents misleading later errors such as trying to create a container whose name is already held.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 614–615)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Docker network name for one conversation. It gives every conversation a predictable, separate network name.

**Data flow**: It receives a conversation UUID and combines the carrier’s base network name with the UUID in compact hexadecimal form. It returns that string.

**Call relations**: `create`, `_revive`, and `_release` use this whenever they need to create, connect, or remove the per-conversation Docker network.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 617–628)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if needed. It is safe when two callers race to create the same network.

**Data flow**: It receives a network name, asks Docker whether it already exists, and returns if found. If missing, it runs `docker network create`; an “already exists” response is treated as success because another caller created it first. Other Docker failures raise errors.

**Call relations**: `create` calls this before starting a new container, and `_revive` calls it before reconnecting a stopped container. It uses `_docker` for both the lookup and creation.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 630–643)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the egress proxy’s certificate authority inside the container. This lets HTTPS tools inside the sandbox trust the proxy that inspects and controls outbound traffic.

**Data flow**: It receives a container ID and certificate text. It runs a root shell command in the container that writes the certificate file and refreshes the system certificate store. It returns nothing on success and raises an error if installation fails.

**Call relations**: `create` calls this for both new and reused containers. That matters because the proxy certificate can change when the server process restarts.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._prepare_mounts`  (lines 645–672)

```
async def _prepare_mounts(self, container_id: str, conversation_id: UUID) -> None
```

**Purpose**: Fixes ownership, permissions, and runtime directories inside the container before it is used. This makes the mounted workspace and session files safe and writable by the sandbox user.

**Data flow**: It receives a container ID and conversation ID. It computes the runtime paths, then runs a root shell script in the container to adjust workspace ownership, create runtime directories, and create or repair a private session file. It raises an error if the preparation command fails.

**Call relations**: `create` and `attach` call this after they have a running container. It uses `_docker` to perform the setup inside Docker and `sandbox_runtime_root` to know where the project expects runtime files.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `manifest`  (lines 675–680)

```
def manifest() -> Manifest
```

**Purpose**: Advertises this file as a UFO extension named `docker`. It tells the host system that `DockerCarrier` is the factory to use for Docker-backed sandboxes.

**Data flow**: It takes no input. It creates and returns a `Manifest` containing the carrier name, version, and carrier specification.

**Call relations**: The extension-loading system calls this when discovering available sandbox backends. The returned manifest connects the configuration name `docker` to the `DockerCarrier` class.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox setup, command execution, file transfer, and service dialing`

A sandbox is the isolated computer where a conversation’s tools and commands run. This file is the E2B version of that sandbox provider. Without it, a deployment that asks for the `e2b` sandbox backend could not start workspaces, run commands, stream files, or expose in-sandbox services through E2B.

The main idea is: each conversation gets a remote E2B sandbox with its own `/workspace` disk. The disk is treated as the only copy of that conversation’s files, so this code never deliberately destroys a sandbox. Instead, it lets E2B pause idle sandboxes and later resumes them.

The file also works around important E2B timing behavior. E2B’s timeout is a wall-clock lease, not an idle timer. A paused sandbox can usually be woken by file or command calls, but only `connect` both resumes it and sets the desired lease length. So this carrier keeps a small in-process lease cache and reconnects when needed, like renewing a library book before using it again.

Before work runs, the carrier installs the current UFO client binary, trusts the proxy certificate, creates `/workspace`, and limits workload memory and process count so user commands cannot starve the sandbox daemon. Commands are launched as their own process groups so timeouts and cancels can stop whole command trees, not just the shell.

#### Function details

##### `E2BCommandHandle.wait`  (lines 188–188)

```
async def wait(self) -> E2BCommandResult
```

**Purpose**: This protocol method describes how a caller waits for a background E2B command to finish. It exists so the rest of this file can talk about E2B command handles without depending tightly on the SDK’s concrete class.

**Data flow**: It starts with a running command handle that already has a process id. Waiting on it eventually produces stdout, stderr, and an exit code, or raises an SDK error if the command fails in a special way.

**Call relations**: The carrier gets this kind of handle from `E2BCommands.run` when it launches commands in the background, then `E2BCarrier._exec_with` waits on it so it can still stop the process group if a timeout happens.


##### `E2BCommands.run`  (lines 209–218)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None, background: Literal[False]=False) -> E2BCommandResult
```

**Purpose**: This protocol method describes E2B’s command-running API. The carrier uses it to run shell commands inside the remote sandbox, either waiting for the result or receiving a live command handle.

**Data flow**: It receives a shell command string plus optional working directory, environment variables, user, timeout, and background flag. It sends that command to the sandbox and returns either the finished result or a handle for a still-running command.

**Call relations**: Almost all sandbox preparation and execution in this file ultimately goes through this method: installing certificates, creating `/workspace`, capping resources, probing a silent sandbox, stopping process groups, and running user commands.


##### `E2BFileStream.__aiter__`  (lines 225–225)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: This protocol method describes how streamed file reads produce chunks of bytes over time. It allows large files to be read piece by piece instead of loaded all at once.

**Data flow**: It starts with an open E2B file stream. Iterating over it yields byte chunks until the remote file has been fully read or the connection ends.

**Call relations**: `E2BCarrier.read` uses this iterator after `E2BFiles.read` opens a streamed file response, then passes each chunk onward to its own caller.


##### `E2BFileStream.aclose`  (lines 227–227)

```
async def aclose(self) -> None
```

**Purpose**: This protocol method closes an open streamed file read. It matters because streamed network responses can hold a connection until they are explicitly released.

**Data flow**: It receives the open stream object and asks the SDK to close it. Nothing is returned, but the network/file resource is released.

**Call relations**: `E2BCarrier.read` calls this in a final cleanup step, even if the consumer stops reading early, so the E2B connection is not left hanging.


##### `E2BFiles.write`  (lines 231–231)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: This protocol method describes writing data into the sandbox filesystem. The carrier uses it when command-line execution is not suitable, especially for raw bytes such as binaries and uploaded files.

**Data flow**: It receives a path, text or bytes, and optionally a sandbox user. The data is sent through E2B’s filesystem API and written at that path inside the sandbox.

**Call relations**: `E2BCarrier.write`, `_ensure_client`, and `_install_ca` rely on this method to put files into the sandbox before commands use them.


##### `E2BFiles.read`  (lines 233–233)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: This protocol method describes opening a file from the sandbox for reading. In this file it is used in streaming mode so large files can be returned safely.

**Data flow**: It receives a path and a requested format. It asks E2B to read that file and returns a stream object that yields bytes.

**Call relations**: `E2BCarrier.read` calls this after ensuring the sandbox lease is long enough for the transfer.


##### `E2BSandbox.get_host`  (lines 242–242)

```
def get_host(self, port: int) -> str
```

**Purpose**: This protocol method formats the public host name for a port exposed from the E2B sandbox. It lets outside clients reach services started inside the sandbox.

**Data flow**: It receives a port number and returns the host name that E2B assigns for that sandbox and port.

**Call relations**: `E2BCarrier.dial` calls this after renewing the sandbox lease, then wraps the host in a `DialTarget` with TLS and the needed access header.


##### `E2BSdk.create`  (lines 246–255)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes creating a brand-new E2B sandbox from a template. The carrier uses it when there is no existing sandbox to resume.

**Data flow**: It receives the template name, lease timeout, metadata, lifecycle settings, network settings, and API key. E2B creates a sandbox and returns an object representing it.

**Call relations**: `E2BCarrier._resume_or_open` calls this only after resume is impossible or no resume id exists.


##### `E2BSdk.connect`  (lines 257–263)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes reconnecting to an existing E2B sandbox. In E2B, this is also the key operation that wakes a paused sandbox and sets a fresh lease.

**Data flow**: It receives a sandbox id, desired lease span, and API key. It returns a live sandbox object if E2B still has that sandbox, or raises an error if not.

**Call relations**: `E2BCarrier._connected` wraps this method with retries, time limits, and error translation, and all reconnect paths go through that wrapper.


##### `E2BCarrier.create`  (lines 315–399)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This opens the sandbox for a conversation, either by resuming a known sandbox, reusing this process’s current one, or creating a fresh one. It also prepares the sandbox so UFO commands can actually run there.

**Data flow**: It receives a `SandboxSpec` containing conversation id, possible resume id, size, proxy settings, run token, environment, and turn id. It chooses or opens an E2B sandbox, installs or rechecks the client and runtime setup, records a lease, and returns a `SandboxHandle` that the rest of the system can use.

**Call relations**: This is the main setup call for the carrier. It consults `_leased`, opens through `_resume_or_open`, prepares through `_ensure_client`, `_prepare_runtime`, or `_prepare_strictly`, drops bad leases through `_drop`, and finally hands back the handle used by later execution, file, and dial calls.

*Call graph*: calls 6 internal fn (_drop, _ensure_client, _leased, _prepare_runtime, _prepare_strictly, _resume_or_open); 8 external calls (__init__, __init__, __init__, timeout, emit_metric, log, egress_proxy_env, sandbox_runtime_root).


##### `E2BCarrier.attach`  (lines 401–426)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reconnects to an already-known sandbox for read-style access without creating a replacement if it is gone. It is useful when the system wants to inspect an existing conversation workspace but must not accidentally create an empty new one.

**Data flow**: It receives a `SandboxSpec` with a resume id. If there is no id, it returns `None`; if E2B no longer has the sandbox, it clears the local lease and returns `None`; otherwise it reconnects, records a lease, and returns a `SandboxHandle`.

**Call relations**: Unlike `create`, this path calls `_connected` but never `_resume_or_open` or creation. It is deliberately conservative so a missing sandbox is reported as absent rather than replaced.

*Call graph*: calls 1 internal fn (_connected); 3 external calls (__init__, __init__, sandbox_runtime_root).


##### `E2BCarrier._resume_or_open`  (lines 428–468)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: This chooses between reconnecting to an existing sandbox and creating a new one. It keeps conversation workspaces alive when possible, but falls back to a fresh sandbox if E2B says the old id no longer exists.

**Data flow**: It receives the desired sandbox specification and an optional sandbox id to resume. If an id is present, it tries `_connected`; if that fails because the sandbox is gone, it logs the miss. Then it looks up the template for the requested size, creates a sandbox through the SDK, checks that traffic access is available, and returns the sandbox.

**Call relations**: `E2BCarrier.create` calls this before preparation. This helper calls `_connected` for safe resume and the SDK’s `create` for the one-time fresh-sandbox path.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 470–486)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: This prepares a sandbox when preparation must succeed before the sandbox is trusted. It retries only transport-level failures, because those may be temporary dropped connections rather than real setup errors.

**Data flow**: It receives a sandbox and its spec. It repeatedly calls `_prepare`; if a retryable network error happens, it logs, emits a metric, sleeps briefly, and tries again. If all attempts fail or the failure is not retryable, it drops the lease and raises the error.

**Call relations**: `E2BCarrier.create` uses this for fresh sandboxes or cached sandboxes that are not proven prepared. It hands the actual setup to `_prepare` and uses `_drop` when the sandbox should no longer be trusted locally.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 488–553)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: This is the safe reconnect wrapper around E2B’s `connect`. It resumes paused sandboxes, renews their lease, retries temporary provider/control-plane trouble, and turns prolonged unavailability into a clear UFO error.

**Data flow**: It receives a conversation id, sandbox id, and requested lease span. It calls the SDK connect method, retrying network errors and retryable provider statuses with backoff until success, retry exhaustion, or an overall wall-clock timeout. It returns the sandbox or raises a provider-unavailable or SDK error.

**Call relations**: `_resume_or_open`, `_sandbox`, and `attach` all use this, so every reconnect in the carrier gets the same retry and timeout behavior.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 5 external calls (__init__, sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 555–557)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This performs the complete standard preparation sequence for a sandbox. It makes sure the UFO client is present, then prepares the runtime environment.

**Data flow**: It receives a sandbox and proxy certificate. It first calls `_ensure_client`, then `_prepare_runtime`; it returns nothing if both succeed.

**Call relations**: `_prepare_strictly` calls this inside its retry loop. It delegates the detailed work to `_ensure_client` and `_prepare_runtime`.

*Call graph*: calls 2 internal fn (_ensure_client, _prepare_runtime); called by 1 (_prepare_strictly).


##### `E2BCarrier._prepare_runtime`  (lines 559–564)

```
async def _prepare_runtime(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This sets up the sandbox environment that user work depends on: trusted proxy certificate, writable workspace, and resource limits. It makes the remote machine safe and usable for UFO workloads.

**Data flow**: It receives a sandbox and CA certificate text. It installs the certificate, ensures `/workspace` exists and belongs to the sandbox user, and applies memory/process caps to the workload control groups.

**Call relations**: `_prepare` and the resume path inside `create` call this. It sequences `_install_ca`, `_ensure_workspace`, and `_cap_workload`.

*Call graph*: calls 3 internal fn (_cap_workload, _ensure_workspace, _install_ca); called by 2 (_prepare, create).


##### `E2BCarrier._ensure_client`  (lines 566–591)

```
async def _ensure_client(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure the sandbox has the exact UFO client binary expected by this process. That matters because all workload entry goes through the `ufo` command inside the sandbox.

**Data flow**: It receives a sandbox, checks whether this sandbox id is already marked ready, then compares the installed binary’s SHA-256 hash with the local client bytes. If the binary is missing or different, it uploads a staged replacement, verifies it, installs it atomically, and marks the sandbox ready.

**Call relations**: `create` calls this directly for resumed sandboxes, and `_prepare` calls it during strict preparation. It uses E2B file writes and command runs through the sandbox protocols.

*Call graph*: called by 2 (_prepare, create); 2 external calls (sha256, uuid4).


##### `E2BCarrier._leased`  (lines 593–608)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: This reads the local lease cache for a conversation while also removing expired lease entries. It prevents the process from remembering every sandbox it has ever touched.

**Data flow**: It receives a conversation id. It checks the current time, deletes all expired leases from the in-memory map, and returns the conversation’s lease if one was present before cleanup.

**Call relations**: `create` uses this to decide whether this process already knows a sandbox, and `_sandbox` uses it to decide whether a provider reconnect is needed.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 610–618)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This installs the proxy certificate authority into the sandbox’s trusted system certificates. That lets HTTPS traffic from sandbox tools trust the UFO proxy that sits between the sandbox and outside services.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path, runs a root command to install and refresh trust, and raises a clear runtime error if the command reports failure.

**Call relations**: `_prepare_runtime` calls this before workspace and resource setup, because networked workload commands rely on trusted proxy TLS.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._ensure_workspace`  (lines 620–629)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This creates `/workspace` and gives ownership to the normal sandbox user. That directory is where the conversation’s files live.

**Data flow**: It receives a sandbox. It runs a root command to create the directory if needed and adjust ownership; if the command fails, it raises an error with command output details.

**Call relations**: `_prepare_runtime` calls this after certificate installation and before applying workload caps.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._cap_workload`  (lines 631–641)

```
async def _cap_workload(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This limits how much memory and how many processes user workloads can consume. The goal is to stop user commands from exhausting the whole sandbox and freezing the daemon that UFO needs to talk to it.

**Data flow**: It receives a sandbox. It runs a root command that calculates a memory ceiling below total guest memory and writes memory and process limits into workload control groups; failures become runtime errors with details.

**Call relations**: `_prepare_runtime` calls this as the final runtime safety step.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier.exec`  (lines 643–695)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: This runs a normal user command inside the sandbox. It applies the turn’s network proxy environment so outbound requests are routed and metered correctly.

**Data flow**: It receives a sandbox handle, argument tuple, timeout, and optional model command value. It passes the command to `_exec_with` as the normal sandbox user context and returns an `ExecResult` with stdout, stderr, exit code, and possible timeout information.

**Call relations**: This is the public command execution method used by the rest of UFO for ordinary sandbox work. It delegates all launch, timeout, stop, and error mapping details to `_exec_with`.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier.exec_skill`  (lines 697–701)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a command as root for server-carried skill setup or synchronization. It is a privileged variant of normal execution.

**Data flow**: It receives a sandbox handle, command arguments, and timeout. It calls `_exec_with` with the user set to root and returns the resulting `ExecResult`.

**Call relations**: Higher-level skill-loading code uses this when it needs administrator permissions in the sandbox. `_exec_with` still provides the shared leasing and timeout behavior.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier._exec_with`  (lines 703–750)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, user: str | None) -> ExecResult
```

**Purpose**: This is the core command runner. It leases the sandbox, launches the command as a process-group leader, waits for it, maps failures into normal execution results, and tries to stop the whole command tree on timeout.

**Data flow**: It receives a handle, argument tuple, timeout, and optional user. It reconnects or reuses the sandbox through `_sandbox`, checks any previous silence with `_still_there`, quotes the arguments into a shell command, launches it in the background, records its process group, waits for completion, and returns an `ExecResult`. On timeout it stops the group if possible; on cancellation or transport failure it drops the lease.

**Call relations**: `exec` and `exec_skill` both call this. It coordinates `_sandbox`, `_still_there`, `_stop_group`, `_mark_silent`, `_forget_group`, and `_drop` so all command paths behave consistently.

*Call graph*: calls 6 internal fn (_drop, _forget_group, _mark_silent, _sandbox, _still_there, _stop_group); called by 2 (exec, exec_skill); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.stop_commands`  (lines 752–774)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: This stops commands still running for a particular turn after the system has decided that turn was truly cancelled. It avoids killing unrelated commands from other turns that share the same conversation sandbox.

**Data flow**: It receives a sandbox handle. It removes the recorded process groups for that handle’s container and turn; if there are any, it leases the sandbox and sends a stop signal to each group.

**Call relations**: This complements `_exec_with`, which deliberately does not stop work on every task cancellation because some cancellations are retried preemptions. It calls `_sandbox` and `_stop_group` only when there is recorded work to stop.

*Call graph*: calls 2 internal fn (_sandbox, _stop_group).


##### `E2BCarrier._forget_group`  (lines 776–786)

```
def _forget_group(self, handle: SandboxHandle, pid: int) -> None
```

**Purpose**: This removes a process group from the in-memory record once the command has ended or has been stopped. It keeps the stop list from growing stale.

**Data flow**: It receives a sandbox handle and process id. It looks up the groups recorded for that container and turn, removes the pid, and deletes the turn entry if no groups remain.

**Call relations**: `_exec_with` calls this in its cleanup path for commands that are no longer intentionally left running.

*Call graph*: called by 1 (_exec_with).


##### `E2BCarrier._stop_group`  (lines 788–814)

```
async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int, user: str | None) -> None
```

**Purpose**: This sends a kill signal to an entire process group inside the sandbox. It is used so a timed-out command’s child processes do not keep consuming CPU after the caller has been told the command stopped.

**Data flow**: It receives the sandbox, container id, process group id, and user. It runs `kill -9` against the negative process id, which means the whole group; if the sandbox does not answer, it marks the container silent and emits a metric instead of replacing the original caller error.

**Call relations**: `_exec_with` calls this after command timeouts, and `stop_commands` calls it for cancelled turns. It may call `_mark_silent` when the sandbox command channel appears wedged.

*Call graph*: calls 1 internal fn (_mark_silent); called by 2 (_exec_with, stop_commands); 1 external calls (emit_metric).


##### `E2BCarrier._mark_silent`  (lines 816–820)

```
def _mark_silent(self, container_id: str) -> None
```

**Purpose**: This temporarily remembers that a sandbox stopped answering commands. The mark prevents the next command from spending its full timeout on a container that is probably unreachable.

**Data flow**: It receives a container id and records an expiry time in the `_silent` map based on the current clock. It returns nothing but changes future command behavior for that container until the mark expires or a probe succeeds.

**Call relations**: `_exec_with` marks silence when a launch times out before a process id is known, and `_stop_group` marks silence when the cleanup command itself times out. `_still_there` later reads and clears or honors the mark.

*Call graph*: called by 2 (_exec_with, _stop_group).


##### `E2BCarrier._still_there`  (lines 822–852)

```
async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None
```

**Purpose**: This probes a sandbox that was recently marked silent before trusting it with another real command. It is a quick health check to avoid wasting a long command timeout on a dead command channel.

**Data flow**: It receives a sandbox object and container id. If there is no active silent mark, it does nothing; if the mark expired, it removes it; otherwise it runs a tiny `true` command with a short timeout. A successful probe clears the mark, and a failed probe raises `SandboxUnreachable`.

**Call relations**: `_exec_with` calls this just before launching a command. It uses the state written by `_mark_silent` and reports metrics or logs when a sandbox still cannot answer.

*Call graph*: called by 1 (_exec_with); 3 external calls (__init__, emit_metric, log).


##### `E2BCarrier.write`  (lines 854–865)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This uploads bytes into the sandbox filesystem. It is the safe path for file contents because E2B command execution takes shell strings, not raw standard input.

**Data flow**: It receives a handle, destination path, and bytes. It leases or reconnects to the sandbox through `_sandbox`, writes the bytes through E2B’s file API, and drops the local lease if the write fails.

**Call relations**: Higher-level code uses this to place files into the conversation workspace. It relies on `_sandbox` for lease safety and `_drop` when the provider call undermines trust in the cached lease.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 867–887)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This streams a file out of the sandbox in chunks. It supports large outputs without loading the whole file into this process’s memory.

**Data flow**: It receives a handle and path. It leases the sandbox for a full autosuspend span, opens a streamed file read, translates E2B’s missing-file error into Python’s `FileNotFoundError`, yields each byte chunk, and always closes the stream afterward.

**Call relations**: The rest of the system consumes this as an async byte iterator. It uses `_sandbox` before opening the stream and `_drop` if the provider call fails before streaming starts.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 889–894)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs structured file operations through the UFO client inside the sandbox. It gives the carrier the same file-operation interface as other sandbox backends.

**Data flow**: It receives a handle, operation name, and parameter dictionary. It passes those to the shared `ufo_fs_file_op` helper, which runs the corresponding `ufo fs` command and returns a dictionary result.

**Call relations**: This public carrier method delegates to the common sandbox helper rather than reimplementing file operation protocol details in the E2B carrier.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `E2BCarrier.dial`  (lines 896–917)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This returns the outside address for a service running on a port inside the sandbox. It is how callers reach things like browser debugging endpoints or preview web servers started by sandbox commands.

**Data flow**: It receives a handle and port. It renews the sandbox lease long enough for an off-carrier connection, asks E2B for the host name, attaches TLS and the traffic access token header if present, and returns a `DialTarget`.

**Call relations**: Callers use this after starting an in-sandbox service. It calls `_sandbox` with a longer lease floor and maps a missing sandbox into the carrier-level `SandboxUnreachable` error.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 919–961)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: This returns a live sandbox object with enough lease time left for the work about to happen. It avoids reconnecting on every operation while still preventing work from running past the provider’s lease.

**Data flow**: It receives a handle, the seconds needed by the next operation, and an optional minimum lease span. It checks the cached lease with `_leased`; if the lease names the right container and lasts long enough, it returns it. Otherwise it removes the cache entry, reconnects through `_connected`, records a new lease, logs the renewal, and returns the sandbox.

**Call relations**: `_exec_with`, `write`, `read`, `dial`, and `stop_commands` all use this as their gate to E2B. It is the central lease-renewal point for active sandbox operations.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 5 (_exec_with, dial, read, stop_commands, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 963–968)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: This forgets a cached lease after a provider call failed. The idea is simple: if the sandbox stopped answering, the local belief about its lease should no longer be trusted.

**Data flow**: It receives a conversation id and a short label describing what was happening. It removes that conversation from the in-memory lease map and logs the drop.

**Call relations**: `create`, `_prepare_strictly`, `_exec_with`, `write`, and `read` call this when failures make the cached sandbox reference suspect.

*Call graph*: called by 5 (_exec_with, _prepare_strictly, create, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 971–988)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: This parses the `E2B_TEMPLATES` environment variable into a size-to-template map. It ensures every supported sandbox size has exactly one E2B template configured.

**Data flow**: It receives a comma-separated string such as `small=...,medium=...,large=...`. It splits each entry, validates the format, checks that the declared sizes match UFO’s known sandbox sizes, and returns a dictionary.

**Call relations**: `build_e2b_carrier` uses this to configure the carrier, and `e2b_runtime_digest` uses it to compute a stable digest of the selected runtime templates.

*Call graph*: called by 2 (build_e2b_carrier, e2b_runtime_digest).


##### `e2b_runtime_digest`  (lines 991–999)

```
def e2b_runtime_digest() -> str
```

**Purpose**: This computes a stable fingerprint of the E2B template configuration. The fingerprint lets the system identify which sandbox runtime setup this process is using.

**Data flow**: It reads `E2B_TEMPLATES` from the environment, parses it with `sandbox_templates`, serializes the map in sorted JSON form, hashes it with SHA-256, and returns the digest string.

**Call relations**: `manifest` exposes this function in the carrier specification so the larger system can ask for the runtime digest when needed.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (sha256, dumps).


##### `build_e2b_carrier`  (lines 1002–1011)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: This constructs a ready-to-use `E2BCarrier` from environment configuration and the local UFO client binary. It is the factory used when the E2B carrier is selected.

**Data flow**: It reads the E2B API key and template map from environment variables, validates and parses the templates, reads the compiled UFO client binary for the E2B target, and returns an `E2BCarrier` instance.

**Call relations**: `manifest` registers this as the carrier factory. When the core sandbox system needs an E2B backend, it calls this factory to get the provider object.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (__init__, client_binary).


##### `manifest`  (lines 1014–1027)

```
def manifest() -> Manifest
```

**Purpose**: This publishes the E2B extension to UFO’s plugin system. It tells the core system that a carrier named `e2b` exists and how to build it.

**Data flow**: It creates a `Manifest` containing one `CarrierSpec` with the carrier name, factory function, supported sizes, off-cluster flag, and runtime digest function. The manifest object is returned to the extension loader.

**Call relations**: This is the registration point for the whole file. The core manifest system reads it so deployments can choose `[sandbox] backend = "e2b"` without core code knowing E2B-specific details.

*Call graph*: 2 external calls (__init__, __init__).


### Sandbox command interface
These files define the common protocol, session abstraction, runtime environment, client binary lookup, cache settings, and durable task handling used across sandbox backends.

### `core/src/ufo/harness/sandbox/session.py`

`domain_logic` · `cross-cutting request handling`

A sandbox is like a locked workshop for one conversation: tools may touch the workbench, `/workspace`, and the runtime skill area, but not the private records stored elsewhere. This file defines that workshop contract and the wrapper objects callers use. The `Carrier` protocol says what any backend must provide: create or attach to a sandbox, run a command, move files, expose a port, and perform file operations. `Sandbox` then gives higher-level, safer actions on top of that contract, such as `bash`, `python`, `write_file`, `read_file`, and `load_skills`.

A major job here is keeping paths honest. User- or model-supplied paths are normalized and checked so `../` tricks or symbolic-link-style escapes cannot reach outside allowed areas. Runtime files get a separate root under `$UFO_HOME/runs/...`, and only selected read-style operations may inspect that runtime or skill tree.

The file also prepares network proxy settings and signed tokens. Those tokens let the egress proxy know which workspace, turn, probe, and authority a sandbox network request belongs to. Finally, it supports both already-open sandboxes (`SandboxSession`) and lazy sandboxes (`_LateSandbox`) that are created only when first used, so tools do not need to care how the sandbox came into being.

#### Function details

##### `egress_proxy_env`  (lines 372–411)

```
def egress_proxy_env(proxy: 'ProxyEndpoint', run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make programs inside a remote sandbox send outside network traffic through UFO's egress proxy. This matters because the proxy can meter, authorize, and safely replace placeholder model API keys.

**Data flow**: It takes a proxy endpoint and a run token → checks that the proxy has a secure public HTTPS address → returns HTTP proxy, HTTPS proxy, no-proxy, certificate, and sentinel API-key environment variables.

**Call relations**: When a carrier prepares a sandbox command, it can use this helper to give the command a safe network route. Internally it parses the configured public URL with `urlsplit` before building the proxy address.

*Call graph*: 1 external calls (urlsplit).


##### `_basic_username`  (lines 417–423)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username part from a `Proxy-Authorization: Basic ...` header. In this system, that username is where signed sandbox tokens are carried.

**Data flow**: It receives an authorization header string → checks that it is Basic authentication → base64-decodes the credentials → returns the text before the first colon as the username.

**Call relations**: The run-token and probe-token decoders both call this first, then verify the signed token found in the username.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 442–446)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Creates a run-token signer/verifier from the deployment secret stored in the environment. It fails early if the secret is missing, because unsigned or unverifiable sandbox traffic would be unsafe.

**Data flow**: It reads the configured token-secret environment variable → encodes it as bytes → returns a `RunTokenCodec` using that secret, or raises an error if it is absent.

**Call relations**: The server startup path calls this so later sandbox openings can mint tokens that the egress proxy will trust.

*Call graph*: called by 1 (run).


##### `RunTokenCodec.encode`  (lines 448–452)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns one sandbox run identity into a signed token suitable for use as the proxy username. The token ties network traffic to a workspace, a turn, and an execution authority.

**Data flow**: It receives a `RunToken` → converts its authority into an optional member id → builds a compact text payload → signs that payload and returns the signed token string.

**Call relations**: The sandbox-opening flow calls this when preparing a run. It relies on the shared token-signing helper so the proxy can later verify the same token.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (sign_token, authority_member_id).


##### `RunTokenCodec.from_proxy_auth`  (lines 454–466)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads and verifies a signed run token from a proxy authorization header. It rejects tokens from the wrong domain, malformed tokens, and forged tokens.

**Data flow**: It receives a Basic authorization header → extracts the username token → verifies the signature → parses workspace id, turn id, and member authority → returns a `RunToken`.

**Call relations**: This is the proxy-side counterpart to `RunTokenCodec.encode`. It calls `_basic_username`, signature verification, UUID parsing, and authority reconstruction.

*Call graph*: calls 1 internal fn (_basic_username); 4 external calls (__init__, verify_token, authority_from_member_id, UUID).


##### `ProbeTokenCodec.encode`  (lines 499–506)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Signs a short-lived token for an off-turn probe command. A probe is a background check or watch that is not tied to a normal running turn, so the token carries its own expiry time.

**Data flow**: It receives a `ProbeToken` → converts its authority into an optional member id → includes workspace, conversation, probe id, authority, and expiry in a payload → returns a signed token string.

**Call relations**: Probe execution code can give this token to the proxy just like a run token, but with a different token kind so the two cannot be confused.

*Call graph*: 2 external calls (sign_token, authority_member_id).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 508–524)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Reads and verifies a signed probe token from proxy authentication. It reconstructs the probe identity only if the token was minted by this deployment and belongs to the probe-token domain.

**Data flow**: It receives a Basic authorization header → extracts the username token → verifies the signature → parses workspace, conversation, probe id, authority, and expiry → returns a `ProbeToken`.

**Call relations**: This mirrors `ProbeTokenCodec.encode` on the receiving side. It uses `_basic_username`, token verification, UUID parsing, and authority reconstruction.

*Call graph*: calls 1 internal fn (_basic_username); 4 external calls (__init__, verify_token, authority_from_member_id, UUID).


##### `sandbox_handle_id`  (lines 612–617)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the raw sandbox id out of a stored handle if that handle belongs to a particular backend. This prevents one sandbox backend from trying to resume another backend's container.

**Data flow**: It receives a backend name and a stored handle string → checks for the `<backend>:` prefix → returns the id after the prefix, or `None` if it does not match.

**Call relations**: Carrier setup code can use this when deciding whether a saved conversation handle is resumable by the current backend.


##### `sandbox_handle_backend`  (lines 620–623)

```
def sandbox_handle_backend(value: str) -> str
```

**Purpose**: Finds which backend wrote a stored sandbox handle. This is useful during migrations or deployments where more than one carrier may still have live sandboxes.

**Data flow**: It receives a stored handle string → splits it at the first separator → returns the backend prefix.

**Call relations**: Routing code can use this before handing the handle to the correct carrier.


##### `Carrier.create`  (lines 665–665)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the contract for creating or attaching to a sandbox for a conversation. Each real carrier implements this for its own platform.

**Data flow**: It receives a `SandboxSpec` describing the conversation, image, workspace, proxy, and authority → the carrier provisions or attaches to the sandbox → returns a `SandboxHandle`.

**Call relations**: Lazy sandbox opening eventually depends on this carrier method, while higher-level `Sandbox` methods stay independent of Docker, E2B, local, or any other backend.


##### `Carrier.attach`  (lines 667–673)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines the contract for attaching only to an already-existing sandbox. It must not create a new sandbox, which is important for read paths that should not resurrect missing workspaces.

**Data flow**: It receives a `SandboxSpec` with any resume information → checks whether that sandbox is reachable → returns a handle if found, otherwise `None`.

**Call relations**: Code that wants to inspect an existing sandbox can use this path without triggering fresh provisioning.


##### `Carrier.exec`  (lines 675–688)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Defines how a backend runs a command inside the sandbox. The optional model-authored command text lets stricter carriers treat model-written commands differently from system-written ones.

**Data flow**: It receives a sandbox handle, command arguments, timeout, and optional model text → runs the command in the backend's sandbox → returns stdout, stderr, exit code, and timeout information.

**Call relations**: Most `Sandbox` actions eventually use this through `SandboxCommands`, including shell commands, Python programs, file-existence checks, and runtime directory setup.


##### `Carrier.write`  (lines 690–702)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how bytes are written into the sandbox filesystem. The path must be guarded so writes stay inside the allowed workspace or runtime area.

**Data flow**: It receives a sandbox handle, destination path, and bytes → writes the bytes into the sandbox, creating needed parents safely → returns nothing or raises an error.

**Call relations**: Higher-level methods such as `Sandbox.write_file`, runtime writes, and staged skill loading call this instead of passing file contents through shell arguments.


##### `Carrier.read`  (lines 704–715)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how bytes are streamed out of a sandbox file. It is an iterator so large files can be copied in chunks instead of being loaded into memory all at once.

**Data flow**: It receives a sandbox handle and path → opens or streams the file from the backend → yields byte chunks until the file is complete.

**Call relations**: Sandbox read helpers call this after checking or translating the path they are allowed to expose.


##### `Carrier.dial`  (lines 717–726)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how outside code can reach a service listening on a port inside the sandbox. This is used for things like browser debugging ports or preview servers.

**Data flow**: It receives a sandbox handle and port number → asks the backend how that port is exposed → returns host, TLS choice, and any required headers.

**Call relations**: The high-level `Sandbox.dial` method delegates to this, while each carrier hides the details of its own networking setup.


##### `Carrier.file_op`  (lines 728–738)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Defines how structured file operations such as read windows, edits, globbing, and grep run inside the sandbox. Running them inside the sandbox avoids pulling whole files into the host process.

**Data flow**: It receives a handle, operation name, and JSON-like parameters → performs the operation in the sandbox workspace → returns a JSON-like result or raises a recoverable error.

**Call relations**: The high-level `Sandbox.run_ufo_fs` method prepares safe parameters and then calls this carrier operation.


##### `CommandStopping.stop_commands`  (lines 758–758)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines the optional ability to stop commands that may keep running after their launch call is cancelled. Not every carrier needs this.

**Data flow**: It receives a sandbox handle, including the turn id → stops only commands launched for that turn → returns nothing.

**Call relations**: `Sandbox.stop_commands` and `_LateSandbox.stop_commands` call this only when the carrier advertises the `CommandStopping` protocol.


##### `SkillLoading.load_skills`  (lines 765–767)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Defines an optional native way for a carrier to load skills into its runtime. A native carrier can do this without using the generic staged Python loader.

**Data flow**: It receives a sandbox handle and a skill payload → installs or links the requested skills → returns an execution-style result describing success and loaded roots.

**Call relations**: `Sandbox.load_skills` uses this path when available; otherwise it falls back to staging files and running the bundled skill-load program.


##### `SkillExecuting.exec_skill`  (lines 774–776)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines an optional privileged command path used for server-carried skill installation programs. Some carriers need to run these as a more powerful sandbox user.

**Data flow**: It receives a handle, command arguments, and timeout → runs the skill helper command with the needed privileges → returns an `ExecResult`.

**Call relations**: `Sandbox._exec_skill` calls this after confirming that the carrier supports `SkillExecuting`.


##### `SystemSkillSeeding.seed_system_skills`  (lines 783–783)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Defines an optional way for a carrier whose runtime filesystem is created locally to seed bundled system skills. This lets the carrier prepare the skill cache before normal sandbox use.

**Data flow**: It receives a zip archive of system skills → writes or installs those skills into the carrier-controlled runtime filesystem → returns nothing.

**Call relations**: Carriers that support local filesystem seeding implement this protocol; the shared `Sandbox` methods do not call it directly.


##### `ufo_fs_file_op`  (lines 786–799)

```
async def ufo_fs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Provides a shared implementation of carrier file operations for sandbox images that include the `ufo fs` or `sbxfs` command. It avoids every carrier duplicating the same command setup and JSON parsing rules.

**Data flow**: It receives a carrier, handle, operation name, and parameters → builds a `SandboxFileOperations` runner around `carrier.exec` → runs the operation and returns the parsed result.

**Call relations**: Carrier implementations can delegate their `file_op` method here when they use the standard in-sandbox file command.

*Call graph*: 1 external calls (__init__).


##### `host_argv`  (lines 807–819)

```
def host_argv(argv: tuple[str, ...], root: str) -> tuple[str, ...]
```

**Purpose**: Rewrites command arguments for carriers where `/workspace` is actually a host directory. It changes only real `/workspace` path segments, not accidental matching text.

**Data flow**: It receives command arguments and the host workspace root → replaces safe occurrences of `/workspace` with that root → returns the rewritten argument tuple.

**Call relations**: Host-path carriers can use this before launching commands so tools can keep speaking in sandbox paths.


##### `workspace_path`  (lines 822–832)

```
def workspace_path(path: str) -> str
```

**Purpose**: Turns a user- or model-supplied path into a safe absolute path under `/workspace`. It rejects attempts to escape the workspace.

**Data flow**: It receives a path string → treats relative paths as being under `/workspace` → resolves `.` and `..` parts → returns a normalized workspace path or raises a helpful error.

**Call relations**: File-writing, file-existence, file-reading, and file-operation code call this before handing paths to a carrier.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 5 (_read_scoped_file, file_exists, run_ufo_fs, write_file, rooted_path); 1 external calls (PurePosixPath).


##### `runtime_relative`  (lines 835–845)

```
def runtime_relative(path: str) -> PurePosixPath
```

**Purpose**: Validates a path meant to live under the private runtime area for one conversation. It makes sure callers pass a plain relative name, not an escape path.

**Data flow**: It receives a runtime path fragment → checks it with the containment guard → returns a `PurePosixPath` relative path or raises `ValueError`.

**Call relations**: Runtime path builders call this before joining a relative name to the sandbox's runtime root.

*Call graph*: called by 2 (_runtime_display_path, _runtime_path); 2 external calls (PurePosixPath, contained_relative).


##### `sandbox_runtime_root`  (lines 848–850)

```
def sandbox_runtime_root(conversation_id: UUID) -> str
```

**Purpose**: Builds the default private runtime directory for one conversation inside the sandbox. This separates engine-owned files from the member workspace.

**Data flow**: It receives a conversation id → formats it under `$UFO_HOME/runs/` using the id's hex form → returns the runtime root path string.

**Call relations**: `_runtime_root` calls this when a handle does not already carry a custom runtime root.

*Call graph*: called by 1 (_runtime_root).


##### `shell_path`  (lines 853–858)

```
def shell_path(path: str) -> str
```

**Purpose**: Quotes a sandbox path so it can be safely placed in a shell command. It preserves `$UFO_HOME` expansion when the path is meant to be reusable inside the sandbox shell.

**Data flow**: It receives a path string → if it begins with `$UFO_HOME/`, quotes only the rest while keeping the variable live; otherwise quotes the whole path → returns shell-safe text.

**Call relations**: Code that builds human-facing or shell-facing commands can use this to avoid accidental shell interpretation.

*Call graph*: 1 external calls (quote).


##### `_runtime_root`  (lines 861–862)

```
def _runtime_root(handle: SandboxHandle) -> str
```

**Purpose**: Chooses the runtime root for a sandbox handle. It uses the handle's explicit root when present, otherwise falls back to the standard per-conversation root.

**Data flow**: It receives a `SandboxHandle` → reads `runtime_root` and `conversation_id` → returns the effective runtime root path.

**Call relations**: Runtime path helpers and sandbox methods use this as the single source for where private runtime files belong.

*Call graph*: calls 1 internal fn (sandbox_runtime_root); called by 7 (_read_scoped_file, _run_staged_skill_load, _sync_system_skills, run_ufo_fs, write_runtime_path, _runtime_display_path, _runtime_path).


##### `_runtime_path`  (lines 865–866)

```
def _runtime_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds a full sandbox path for a private runtime file. It validates the relative part before joining it to the runtime root.

**Data flow**: It receives a handle and relative runtime name → finds the runtime root → validates the relative name → returns the full path string.

**Call relations**: Runtime file writes, staged skill loading, system skill syncing, and output-directory setup all call this.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 7 (_run_staged_skill_load, _sync_system_skills, ensure_tool_output_dir, runtime_file_exists, runtime_path, write_runtime_file, write_runtime_path); 1 external calls (PurePosixPath).


##### `_runtime_display_path`  (lines 869–873)

```
def _runtime_display_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds the `$UFO_HOME/...` form of a runtime path for display or reuse by an agent. This hides the absolute root while still naming the same runtime file.

**Data flow**: It receives a handle and relative runtime name → finds the run id from the runtime root → validates the relative name → returns a `$UFO_HOME/runs/...` path.

**Call relations**: `Sandbox.runtime_display_path` calls this after binding to a real session.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 1 (runtime_display_path); 1 external calls (PurePosixPath).


##### `rooted_path`  (lines 876–879)

```
def rooted_path(path: str, root: str) -> str
```

**Purpose**: Normalizes a path under a chosen root while applying the same workspace escape checks. It is used for special readable roots such as runtime and skills.

**Data flow**: It receives a path and root → maps the path through `/workspace` validation → restores the original root spelling → returns the safe normalized path.

**Call relations**: `Sandbox.run_ufo_fs` and `_read_scoped_file` use this when allowing read-like access outside the normal workspace.

*Call graph*: calls 1 internal fn (workspace_path); called by 2 (_read_scoped_file, run_ufo_fs).


##### `_resolve_parts`  (lines 882–891)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies path pieces by applying `.` and `..` rules without letting the path climb above its root. It is the small path-cleaning engine behind workspace checks.

**Data flow**: It receives path parts → walks them with a stack → removes normal current-directory parts, pops on safe `..`, and rejects root escapes → returns cleaned parts.

**Call relations**: `workspace_path` calls this before deciding whether the resulting path is still inside `/workspace`.

*Call graph*: called by 1 (workspace_path).


##### `Sandbox.conversation_id`  (lines 903–907)

```
def conversation_id(self) -> UUID
```

**Purpose**: Declares that every sandbox reference can report which conversation's workspace it points at. This is available even before a lazy sandbox is created.

**Data flow**: A concrete sandbox object provides the value → callers read it as a UUID → no filesystem or backend work is required.

**Call relations**: `SandboxSession`, `_LateSandbox`, and `_AuthorizedSandbox` implement this property for their different ways of holding a sandbox.


##### `Sandbox.turn_id`  (lines 910–914)

```
def turn_id(self) -> UUID | None
```

**Purpose**: Declares that a sandbox reference can report the turn whose authority it carries, if it is turn-bound. This lets command stopping and resource ownership stay scoped to one turn.

**Data flow**: A concrete sandbox object provides a UUID or `None` → callers use it to identify the owning turn → no sandbox creation is needed.

**Call relations**: Concrete sandbox wrappers implement this property from either the handle or the lazy sandbox metadata.


##### `Sandbox.created`  (lines 917–919)

```
def created(self) -> bool
```

**Purpose**: Declares that callers can ask whether a sandbox has already been created. This is especially useful for lazy sandboxes.

**Data flow**: A concrete sandbox object checks its own state → returns `True` or `False` → no carrier operation is required.

**Call relations**: `SandboxSession` always returns true, while `_LateSandbox` and `_AuthorizedSandbox` reflect lazy-session state.


##### `Sandbox.authorize`  (lines 921–929)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'Sandbox'
```

**Purpose**: Declares how to view the same sandbox under a specific run token and environment authority. This is how the same container can be reused safely across turns or members.

**Data flow**: It receives a new run token, environment names to clear, and environment values to add → returns a sandbox wrapper with that adjusted authority.

**Call relations**: `SandboxSession` rewrites the handle immediately; `_LateSandbox` returns an `_AuthorizedSandbox` that applies the rewrite once opened.


##### `Sandbox._bound`  (lines 931–932)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Declares the internal step that turns any sandbox reference into a real `SandboxSession`. Lazy sandboxes create themselves here, while existing sessions simply return themselves.

**Data flow**: It reads the concrete object's state → creates, retrieves, or returns a session → yields a `SandboxSession` ready for carrier calls.

**Call relations**: Nearly every high-level sandbox operation calls `_bound` before touching the carrier.

*Call graph*: called by 18 (_read_file, _read_scoped_file, bash, bash_task, dial, ensure_tool_output_dir, file_exists, load_skills, python, run_ufo_fs (+8 more)).


##### `Sandbox.runtime_path`  (lines 934–936)

```
async def runtime_path(self, relative: str) -> str
```

**Purpose**: Returns the absolute sandbox path for a private runtime file. Callers use it when they need the real in-sandbox location.

**Data flow**: It receives a relative runtime name → binds to a session → resolves the full runtime path → returns the path string.

**Call relations**: It relies on `_bound` and `_runtime_path`, so lazy sandboxes are opened only when the path is actually needed.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.runtime_display_path`  (lines 938–940)

```
async def runtime_display_path(self, relative: str) -> str
```

**Purpose**: Returns a `$UFO_HOME`-style path for a private runtime file. This is friendlier for commands or messages that should make sense inside the sandbox.

**Data flow**: It receives a relative runtime name → binds to a session → builds the display path → returns that string.

**Call relations**: It calls `_runtime_display_path` after obtaining the session through `_bound`.

*Call graph*: calls 2 internal fn (_bound, _runtime_display_path).


##### `Sandbox.write_runtime_file`  (lines 942–945)

```
async def write_runtime_file(self, relative: str, content: bytes) -> None
```

**Purpose**: Writes an engine-owned file into the conversation's private runtime area, outside the member workspace. This keeps internal files separate from normal workspace files.

**Data flow**: It receives a relative runtime path and bytes → binds to a session → resolves the safe runtime path → asks the carrier to write the bytes.

**Call relations**: It uses `_runtime_path` for containment and then delegates the actual write to the carrier.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.write_runtime_path`  (lines 947–955)

```
async def write_runtime_path(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to an already-resolved runtime path, but only if it is still inside this conversation's runtime root. This prevents accidental writes elsewhere in the sandbox.

**Data flow**: It receives a full path and bytes → binds to a session → checks that the path is inside the runtime root and not the root itself → writes through the carrier.

**Call relations**: It combines `_runtime_root`, `_runtime_path`, and carrier writing to enforce the private-runtime boundary.

*Call graph*: calls 3 internal fn (_bound, _runtime_path, _runtime_root); 1 external calls (PurePosixPath).


##### `Sandbox.runtime_file_exists`  (lines 957–964)

```
async def runtime_file_exists(self, relative: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the private runtime area. It uses a simple in-sandbox test command.

**Data flow**: It receives a relative runtime name → binds to a session → resolves the runtime path → runs `test -f` inside the sandbox → returns whether the exit code was zero.

**Call relations**: It depends on carrier command execution after `_runtime_path` has produced the guarded target.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox._commands`  (lines 966–977)

```
def _commands(self, bound: 'SandboxSession', model_command: str | None=None) -> SandboxCommands[ExecResult]
```

**Purpose**: Builds the shared command runner used for bash, shell scripts, Python, and journaled tasks. It packages the carrier's `exec` method with default timeouts and the sandbox Python bootstrap.

**Data flow**: It receives a bound session and optional model-authored command text → creates a `SandboxCommands` object wired to that carrier and handle → returns the command helper.

**Call relations**: `Sandbox.bash`, `bash_task`, `sh`, and `python` all call this so command behavior stays consistent.

*Call graph*: called by 4 (bash, bash_task, python, sh); 1 external calls (__init__).


##### `Sandbox.bash`  (lines 979–981)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a bash command inside the sandbox. This is the simple high-level entry for command-style tool work.

**Data flow**: It receives command text and optional timeout → binds to a session → builds the command helper → runs bash → returns an `ExecResult`.

**Call relations**: Runtime tools and sandbox Chrome helpers call this for measurement, artifact storage, browser setup, and cleanup work.

*Call graph*: calls 2 internal fn (_bound, _commands); called by 8 (measure_file, store_artifact, _lease, reattach, _abandon_allocation, _bring_up_failure, _stop_bridge, _stop_stack).


##### `Sandbox.bash_task`  (lines 983–1000)

```
async def bash_task(self, command: str, base: str, *, detach: bool, model_authored: bool, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs or reattaches to a supervised, journaled bash task. It records whether the command text came from the model so carriers with stricter policies can decide what is allowed.

**Data flow**: It receives command text, task base, detach flag, model-authored flag, and optional timeout → binds to a session → builds a command helper with model text when appropriate → runs or reattaches the task.

**Call relations**: The sandbox Chrome bridge startup uses this for long-running command work that may need supervision.

*Call graph*: calls 2 internal fn (_bound, _commands); called by 1 (_start_bridge).


##### `Sandbox.sh`  (lines 1002–1007)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX shell script inside the sandbox with arguments passed as separate command arguments. This avoids unsafe string interpolation.

**Data flow**: It receives script text, positional arguments, and optional timeout → binds to a session → builds the command helper → runs the script and returns its result.

**Call relations**: It shares the same command machinery as bash and Python through `_commands`.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.python`  (lines 1009–1020)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with UFO's containment guard injected. This ensures path checks use the server's trusted guard code, not whatever happens to be in the sandbox workspace.

**Data flow**: It receives Python source, arguments, and optional timeout → binds to a session → builds the command helper with isolated Python settings → runs the program and returns its result.

**Call relations**: It uses `_commands`, which includes the bootstrap code and isolated Python flag defined in this file.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.stop_commands`  (lines 1022–1028)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this sandbox turn when a deliberate user stop has occurred. It does nothing for carriers whose commands cannot outlive the original execution call.

**Data flow**: It binds to a session → checks whether the carrier supports command stopping → if so, asks the carrier to stop commands for the handle's turn.

**Call relations**: This high-level method is the normal stop path for already-bound sandbox references.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.write_file`  (lines 1030–1032)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the conversation workspace at a safe path. It is the main high-level file-write method exposed to setup and tools.

**Data flow**: It receives a path and content bytes → binds to a session → normalizes and checks the path under `/workspace` → writes through the carrier.

**Call relations**: Sandbox setup code calls this to place files in the workspace while reusing the common path guard.

*Call graph*: calls 2 internal fn (_bound, workspace_path); called by 1 (run).


##### `Sandbox.load_skills`  (lines 1034–1076)

```
async def load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Loads system and user skills into the sandbox runtime skill directory. It validates the loader's response and refreshes system skills when needed.

**Data flow**: It receives a skill payload → binds to a session → uses native carrier loading if available, otherwise runs a staged loader → parses the JSON response → returns a name-to-root-path map or raises on invalid results.

**Call relations**: Skill runtime code calls this when installing or loading skills. It may call `_run_staged_skill_load` and, if baked system skills are stale or missing, `_sync_system_skills`.

*Call graph*: calls 3 internal fn (_bound, _run_staged_skill_load, _sync_system_skills); called by 2 (install_skill, load_skills); 1 external calls (loads).


##### `Sandbox._run_staged_skill_load`  (lines 1078–1100)

```
async def _run_staged_skill_load(self, bound: 'SandboxSession', payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Implements the generic skill-loading fallback by staging a JSON payload inside the sandbox and running the bundled loader program. This is used when the carrier has no native skill loader.

**Data flow**: It receives a bound session and skill payload → writes the JSON payload to a private runtime staging path → computes a checksum → executes the skill-load Python program → returns its `ExecResult`.

**Call relations**: `Sandbox.load_skills` calls this when native loading is unavailable or after system skills have been refreshed.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 3 external calls (sha256, dumps, uuid4).


##### `Sandbox._sync_system_skills`  (lines 1102–1119)

```
async def _sync_system_skills(self, bound: 'SandboxSession') -> None
```

**Purpose**: Copies the server's system-skill archive into the sandbox and installs it safely. This repairs or updates the sandbox's system skill cache.

**Data flow**: It receives a bound session → writes the zip archive to a runtime staging path → computes a checksum → runs the system-skill sync Python program → raises an error if that command fails.

**Call relations**: `Sandbox.load_skills` calls this when expected system skills are missing and an archive is available.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 2 external calls (sha256, uuid4).


##### `Sandbox._exec_skill`  (lines 1121–1126)

```
async def _exec_skill(self, bound: 'SandboxSession', argv: tuple[str, ...]) -> ExecResult
```

**Purpose**: Runs a privileged skill helper command through carriers that support that special capability. It refuses to proceed if the carrier cannot run skill programs this way.

**Data flow**: It receives a bound session and command arguments → checks for the `SkillExecuting` protocol → calls `exec_skill` with the default timeout → returns an `ExecResult`.

**Call relations**: Both staged skill loading and system-skill syncing use this to run their in-sandbox installer programs.

*Call graph*: called by 2 (_run_staged_skill_load, _sync_system_skills).


##### `Sandbox.ensure_tool_output_dir`  (lines 1128–1152)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine's private tool-output directory exists in the runtime area. If a file or symlink is squatting on that fixed internal name, it removes it and reports that cleanup happened.

**Data flow**: It binds to a session → resolves the runtime tool-output path → runs a shell script that checks, removes a non-directory target if needed, and creates the directory → returns whether a squatter was reclaimed.

**Call relations**: Tool-output offload code can call this before writing large private results.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.file_exists`  (lines 1154–1160)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists in the workspace. The path is guarded before the sandbox command runs.

**Data flow**: It receives a path → normalizes it under `/workspace` → binds to a session → runs `test -f` inside the sandbox → returns true when the command succeeds.

**Call relations**: It uses `workspace_path` and carrier execution for a lightweight existence check.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.run_ufo_fs`  (lines 1162–1203)

```
async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured file operation inside the sandbox while enforcing which roots may be touched. Normal operations are limited to `/workspace`; read-like operations may also inspect runtime and skill roots.

**Data flow**: It receives an operation name and arguments → binds to a session → rewrites or rejects any path argument according to the allowed root → adds the workspace root parameter → calls the carrier's file operation.

**Call relations**: File tools use this high-level path before the carrier's `file_op`; it relies on `workspace_path`, `rooted_path`, and `_runtime_root`.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); 1 external calls (PurePosixPath).


##### `Sandbox.read_file`  (lines 1205–1207)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts streaming a workspace or current-runtime file from the sandbox. It returns chunks so callers do not need to load the whole file at once.

**Data flow**: It receives a path → delegates to `_read_scoped_file` → returns an async byte iterator.

**Call relations**: Artifact-storage code calls this when it needs to copy sandbox files out safely.

*Call graph*: calls 1 internal fn (_read_scoped_file); called by 1 (store_artifact).


##### `Sandbox._read_scoped_file`  (lines 1209–1222)

```
async def _read_scoped_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Performs the path checks for `read_file` and streams the chosen file. It allows normal workspace reads and selected reads from the current runtime display path.

**Data flow**: It receives a path → binds to a session → decides whether it names runtime or workspace → normalizes the target safely → yields chunks from carrier `read`.

**Call relations**: `Sandbox.read_file` calls this, and it uses `_runtime_root`, `rooted_path`, and `workspace_path` before delegating to the carrier.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); called by 1 (read_file); 1 external calls (PurePosixPath).


##### `Sandbox._read_file`  (lines 1224–1227)

```
async def _read_file(self, target: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file from an already-resolved target path. It is a lower-level helper that assumes the caller has already chosen a safe target.

**Data flow**: It receives a target path → binds to a session → yields byte chunks from carrier `read`.

**Call relations**: This shares the same carrier streaming mechanism as scoped reads, but without doing the path-selection work itself.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.dial`  (lines 1229–1233)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Gets an outside-reachable address for a service running on a port inside the sandbox. Callers use it instead of guessing how the backend publishes ports.

**Data flow**: It receives a port number → binds to a session → asks the carrier to dial that port → returns host, TLS flag, and any required headers.

**Call relations**: The sandbox Chrome endpoint code calls this to reach browser-related services inside the sandbox.

*Call graph*: calls 1 internal fn (_bound); called by 1 (_endpoint).


##### `SandboxSession.conversation_id`  (lines 1247–1248)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id from an already-open sandbox handle. This identifies the workspace the session reaches.

**Data flow**: It reads `handle.conversation_id` → returns that UUID → no backend call happens.

**Call relations**: This implements the abstract `Sandbox.conversation_id` property for concrete sessions.


##### `SandboxSession.turn_id`  (lines 1251–1252)

```
def turn_id(self) -> UUID | None
```

**Purpose**: Returns the turn id carried by an already-open sandbox handle, if any. This keeps turn-scoped operations tied to the correct owner.

**Data flow**: It reads `handle.turn_id` → returns the UUID or `None`.

**Call relations**: This implements the abstract `Sandbox.turn_id` property for concrete sessions.


##### `SandboxSession.created`  (lines 1255–1256)

```
def created(self) -> bool
```

**Purpose**: Reports that a `SandboxSession` is already backed by a real sandbox. Unlike a lazy sandbox, there is nothing left to create.

**Data flow**: It receives no input beyond the session → returns `True`.

**Call relations**: This implements the abstract `Sandbox.created` property for sessions.


##### `SandboxSession._bound`  (lines 1258–1259)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Returns this session as the bound sandbox. Since it is already open, no locking or creation is needed.

**Data flow**: It receives the session object → returns itself.

**Call relations**: All inherited high-level `Sandbox` methods use this when called on a `SandboxSession`.


##### `SandboxSession.authorize`  (lines 1261–1289)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new session view of the same sandbox with a different run token and adjusted environment. This lets one shared container be used under the exact authority of the current operation.

**Data flow**: It receives a new run token, environment keys to remove, and new environment values → verifies the old proxy environment contains the current token → rewrites proxy URLs to the new token → returns a new `SandboxSession` with an updated handle.

**Call relations**: `_AuthorizedSandbox._bound` uses this after a lazy sandbox opens, and callers may use it directly on existing sessions.

*Call graph*: 2 external calls (__init__, __init__).


##### `_LateSandbox.__init__`  (lines 1293–1305)

```
def __init__(self, conversation_id: UUID, turn_id: UUID, open: Callable[[], Awaitable[SandboxSession]], existing: Callable[[], Awaitable[SandboxSession | None]]) -> None
```

**Purpose**: Creates a lazy sandbox reference that knows how to open a session later and how to look for an existing one. It delays expensive sandbox creation until the first real operation.

**Data flow**: It receives conversation id, turn id, an open callback, and an existing-session callback → stores them → creates a lock to prevent duplicate opens → starts with no session.

**Call relations**: Higher-level orchestration can hand this object to tools before a backend container actually exists.

*Call graph*: 1 external calls (Lock).


##### `_LateSandbox.conversation_id`  (lines 1308–1309)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id known by the lazy sandbox without opening it. This lets callers name the workspace without creating a container.

**Data flow**: It reads the stored conversation id → returns it.

**Call relations**: This implements the `Sandbox.conversation_id` property for lazy sandboxes.


##### `_LateSandbox.turn_id`  (lines 1312–1313)

```
def turn_id(self) -> UUID
```

**Purpose**: Returns the turn id associated with the lazy sandbox. This value is known up front and does not require opening the sandbox.

**Data flow**: It reads the stored turn id → returns it.

**Call relations**: This implements the `Sandbox.turn_id` property for lazy sandboxes.


##### `_LateSandbox.created`  (lines 1316–1317)

```
def created(self) -> bool
```

**Purpose**: Reports whether the lazy sandbox has already opened its session. It does not ask the backend.

**Data flow**: It checks whether the cached session is present → returns true or false.

**Call relations**: This implements `Sandbox.created` for lazy sandboxes and is reflected by `_AuthorizedSandbox.created`.


##### `_LateSandbox.authorize`  (lines 1319–1325)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Creates an authorized view of the lazy sandbox without forcing it to open. The authorization will be applied when the sandbox is eventually bound.

**Data flow**: It receives a run token, cleared environment names, and environment additions → packages them with the lazy sandbox → returns an `_AuthorizedSandbox` wrapper.

**Call relations**: This implements `Sandbox.authorize` for the lazy case by deferring the actual session rewrite.

*Call graph*: 1 external calls (__init__).


##### `_LateSandbox._bound`  (lines 1327–1332)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens the lazy sandbox on first use and then reuses the same session. A lock prevents two simultaneous first operations from creating two sandboxes.

**Data flow**: It checks for a cached session → if missing, enters a lock and calls the open callback once → stores and returns the resulting `SandboxSession`.

**Call relations**: Every inherited high-level sandbox method reaches this before calling the carrier when the sandbox was handed out lazily.


##### `_LateSandbox.stop_commands`  (lines 1334–1337)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this turn even if the lazy sandbox was not opened by this object yet. It can attach to an existing session just for the stop operation.

**Data flow**: It uses the cached session if present, otherwise asks the existing-session callback → if a stop-capable carrier is found, replaces the handle's turn id with this lazy turn → asks the carrier to stop commands.

**Call relations**: This overrides `Sandbox.stop_commands` so a user stop can reach already-running work without accidentally creating a new sandbox.

*Call graph*: 1 external calls (replace).


##### `_AuthorizedSandbox.conversation_id`  (lines 1348–1349)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id of the underlying lazy sandbox. Authorization wrapping does not change which workspace is used.

**Data flow**: It reads `late.conversation_id` → returns that UUID.

**Call relations**: This implements the `Sandbox.conversation_id` property for authorized lazy views.


##### `_AuthorizedSandbox.turn_id`  (lines 1352–1353)

```
def turn_id(self) -> UUID
```

**Purpose**: Returns the turn id of the underlying lazy sandbox. Authorization wrapping does not change turn ownership.

**Data flow**: It reads `late.turn_id` → returns that UUID.

**Call relations**: This implements the `Sandbox.turn_id` property for authorized lazy views.


##### `_AuthorizedSandbox.created`  (lines 1356–1357)

```
def created(self) -> bool
```

**Purpose**: Reports whether the underlying lazy sandbox has already been created. The authorized wrapper itself has no separate session state.

**Data flow**: It reads `late.created` → returns that boolean.

**Call relations**: This mirrors `_LateSandbox.created` for code holding the authorized view.


##### `_AuthorizedSandbox.authorize`  (lines 1359–1365)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Re-authorizes the same lazy sandbox with a new token and environment. The newest authorization replaces the wrapper's previous one.

**Data flow**: It receives new authorization inputs → delegates to the underlying lazy sandbox's `authorize` → returns a fresh authorized wrapper.

**Call relations**: This keeps stacked authorization simple by going back to `_LateSandbox.authorize` rather than wrapping wrappers.


##### `_AuthorizedSandbox._bound`  (lines 1367–1368)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens or retrieves the underlying lazy session and then applies the stored authorization to it. This is where deferred authorization becomes a real updated session handle.

**Data flow**: It binds the underlying lazy sandbox → calls `SandboxSession.authorize` with the stored token and environment changes → returns the authorized session.

**Call relations**: All inherited high-level sandbox operations on an authorized lazy sandbox pass through this before reaching the carrier.


### `core/src/ufo/harness/sandbox/protocol.py`

`io_transport` · `request handling`

A sandbox is an isolated place where code can run without freely touching the host machine. This file is the adapter layer for asking that sandbox to do work. It does not run processes itself. Instead, callers provide an `execute` function, and this file builds the exact command arguments to pass into it.

There are two main helpers. `SandboxCommands` builds common command requests: run a bash command, run a tracked long-running bash task, run a POSIX `sh` script, or run a Python snippet with a bootstrap prefix that sets up containment. Think of it like a menu at a restaurant: callers choose “bash” or “python,” and this class formats the order correctly before handing it to the kitchen.

`SandboxFileOperations` speaks a simple JSON-based file protocol. It sends an operation name plus compact JSON parameters to a configured command, waits with the right timeout, then expects exactly one JSON object back. If the sandbox returns no output, invalid JSON, a non-object response, or an explicit error field, this code turns that into a Python exception so callers do not accidentally treat a failed file operation as success.

One important detail is document reads. Some file types can take longer to read or convert, so reads for configured document suffixes get a longer timeout than ordinary operations.

#### Function details

##### `CommandResult.stdout`  (lines 14–14)

```
def stdout(self) -> str
```

**Purpose**: This names the standard place where a sandbox command’s normal text output can be read. Any concrete command result used with this protocol must provide it.

**Data flow**: A sandbox command has already finished and produced a result object. Reading `stdout` gives the text the command wrote to its normal output stream, with no parsing done here.

**Call relations**: This is part of the shared shape that `SandboxFileOperations.run` relies on after it calls the supplied executor. The file operation runner reads this output because the sandbox file protocol is expected to return its JSON response there.


##### `CommandResult.stderr`  (lines 17–17)

```
def stderr(self) -> str
```

**Purpose**: This names the standard place where a sandbox command’s error text can be read. It lets error messages from the sandbox be preserved and shown to callers.

**Data flow**: A sandbox command has already finished and produced a result object. Reading `stderr` gives the text the command wrote to its error stream, usually useful when something went wrong.

**Call relations**: This supports `SandboxFileOperations.run` when output is missing or malformed. In those cases, the runner prefers the sandbox’s own error text so the caller sees the most helpful failure message.


##### `CommandResult.exit_code`  (lines 20–20)

```
def exit_code(self) -> int
```

**Purpose**: This names the standard place where a sandbox command’s numeric finish status can be read. A value of zero usually means success, while other values usually mean failure.

**Data flow**: A sandbox command has already finished and produced a result object. Reading `exit_code` gives the integer status reported by the process or sandbox carrier.

**Call relations**: This protocol field makes command results carrier-independent, meaning different sandbox backends can expose the same basic information. This particular file does not inspect the code directly, but it requires the field so callers can depend on a common result shape.


##### `SandboxCommands.bash`  (lines 33–38)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This builds and runs one bash command inside the sandbox through the configured supervisor. Use it when the caller wants normal shell behavior such as pipes, redirects, and environment expansion.

**Data flow**: It receives a command string and an optional timeout. It wraps that string as `bash -lc <command>`, prefixes the configured supervisor command, chooses either the provided timeout or the default timeout, then sends the complete argument tuple to the supplied asynchronous `execute` function. The result from `execute` is returned unchanged.

**Call relations**: This method is a convenience doorway into the sandbox command runner. Higher-level code can ask for “run this bash command” without rebuilding the supervisor invocation each time; this method hands the finished request to the injected executor.


##### `SandboxCommands.bash_task`  (lines 40–62)

```
async def bash_task(self, command: str, base: str, *, detach: bool, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This runs or reconnects to a journaled bash task through the sandbox supervisor. It is useful for work that may be detached, tracked, or resumed by a task name rather than treated as a one-off command.

**Data flow**: It receives a bash command, a task base name, a `detach` choice, and an optional timeout. It adds task-related supervisor arguments, includes `--detach` only when requested, wraps the command as `bash -lc <command>`, selects the timeout, and passes the final argument tuple to `execute`. Whatever result the executor returns is passed back to the caller.

**Call relations**: This sits between higher-level task control and the low-level sandbox executor. The caller decides whether the task should detach; this method translates that decision into the supervisor’s command-line format and then hands it off.


##### `SandboxCommands.sh`  (lines 64–69)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This runs a POSIX `sh` script in the sandbox while keeping each script argument separate. It is meant for portable shell snippets where arguments must not be merged into one unsafe string.

**Data flow**: It receives a script, any number of string arguments, and an optional timeout. It builds an argument list shaped like `sh -c <script> sh <args...>`, chooses the timeout, sends it to the supplied `execute` function, and returns that result.

**Call relations**: This gives callers a safer and simpler way to run small shell scripts. It hands off directly to the executor, but first it preserves argument boundaries so the script can read its inputs predictably.


##### `SandboxCommands.python`  (lines 71–82)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ResultT
```

**Purpose**: This runs a Python program inside the sandbox with the configured containment bootstrap placed before the caller’s code. Use it when Python code needs to run in the sandbox with the project’s safety setup applied.

**Data flow**: It receives Python source text, optional command-line arguments, and an optional timeout. It builds a `python3` command using the configured Python flag, combines the bootstrap code with the caller’s program, appends the arguments, chooses the timeout, and sends the request to `execute`. The executor’s result is returned unchanged.

**Call relations**: This hides the details of how sandboxed Python must be started. Higher-level code supplies only the Python program; this method adds the required bootstrap and delegates the finished command to the executor.


##### `SandboxFileOperations.run`  (lines 96–126)

```
async def run(self, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This performs one sandbox file operation and turns the sandbox’s JSON reply into a normal Python dictionary. It also converts bad replies and reported file-operation errors into clear exceptions.

**Data flow**: It receives an operation name, such as a read-like action, and a dictionary of parameters. It checks whether this is a read of a configured document suffix using `PurePosixPath`; document reads get a longer timeout, while other operations use the default. It sends the operation plus compact JSON parameters made with `json.dumps` to the configured command. Then it trims the command’s standard output, parses it with `json.loads`, confirms the parsed value is a dictionary, raises an error if the dictionary contains an `error` string, and otherwise returns the dictionary.

**Call relations**: This is the file-protocol bridge between ordinary Python callers and the sandbox command that actually touches files. It calls the injected executor to run the operation, uses JSON conversion to speak the sandbox protocol, and uses path suffix checking to choose the right timeout before handing the parsed response back to the caller.

*Call graph*: 3 external calls (dumps, loads, PurePosixPath).


### `core/src/ufo/harness/sandbox/exec_env.py`

`domain_logic` · `sandbox open before command execution`

A sandbox is like a locked workshop where commands run. The commands often need to talk to Git hosts, connector command-line tools, or external services such as keyed providers. This file prepares the “badge labels” the workshop gets: environment variables that identify what it is allowed to use. Importantly, these labels are not real secrets. They are sentinels, meaning placeholder values that an outside proxy later swaps for the real credential only when a network request is allowed.

The main entry point is `ProbeEnv.exports`, which combines several kinds of settings into one environment dictionary. It always includes the conversation id, adds Git configuration so Git knows how to ask the project’s credential helper for authentication, adds connector CLI credentials based on the current execution authority, and adds provider-specific variables when the workspace has stored credentials for them.

The file is careful about safety. If a credential slot is missing or unreadable, it skips only that provider instead of failing the whole sandbox open. If more than one account could match a single static CLI variable, it refuses to guess and logs the ambiguity. If two Git providers both try to set commit identity, it removes the identity rather than letting commits accidentally appear under whichever account happened to win.

#### Function details

##### `ProbeEnv.exports`  (lines 65–77)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, authority: ExecutionAuthority) -> dict[str, str]
```

**Purpose**: Builds the full set of environment variables for an off-turn probe sandbox. It gives the sandbox the safe placeholder values it needs for Git, connector CLIs, keyed providers, and conversation tracking.

**Data flow**: It receives a conversation id, a probe id, and an execution authority that says whose permissions apply. It reads the current workspace id, combines fixed Git proxy settings with connector Git settings, asks for grant-based CLI variables, and asks for keyed-provider variables. It returns one dictionary of environment variable names and string values; it does not expose real secrets.

**Call relations**: This is the coordinating function for the file. When a probe sandbox is opened, it calls `ws_current` to learn the workspace, then calls `cli_git_config` and `_git_config_env` to prepare Git settings, `_grant_cli_env` to prepare connector account access, and `_keyed_provider_env` to prepare provider-specific placeholders.

*Call graph*: calls 4 internal fn (_git_config_env, _grant_cli_env, _keyed_provider_env, cli_git_config); 1 external calls (ws_current).


##### `_git_config_env`  (lines 80–87)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into environment variables in the special format Git understands. This lets the sandbox configure Git without writing a Git config file.

**Data flow**: It receives a list of Git setting name/value pairs. It creates `GIT_CONFIG_COUNT` plus numbered `GIT_CONFIG_KEY_n` and `GIT_CONFIG_VALUE_n` variables. It returns those variables as a dictionary ready to merge into the sandbox environment.

**Call relations**: `ProbeEnv.exports` calls this after gathering the fixed proxy-auth setting and connector-specific Git helper settings. Its output is one piece of the final sandbox environment.

*Call graph*: called by 1 (exports).


##### `cli_git_config`  (lines 90–105)

```
def cli_git_config(clis: Mapping[str, CliCredential]) -> tuple[tuple[str, str], ...]
```

**Purpose**: Creates Git credential-helper settings for connector CLIs that support Git. This lets ordinary `git clone` or `git push` inside the sandbox authenticate through the same safe sentinel-based route as the connector CLI.

**Data flow**: It receives the configured connector CLI credentials. For each CLI that declares Git support, it creates Git settings for that host’s credential helper. It returns a tuple of Git configuration pairs; CLIs without Git support add nothing.

**Call relations**: `ProbeEnv.exports` calls this before `_git_config_env`. Its settings are folded into Git’s environment-based configuration, so later Git commands can ask the helper for credentials.

*Call graph*: called by 1 (exports).


##### `_keyed_provider_env`  (lines 108–155)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Adds environment variables for external providers whose credentials are stored by the workspace, such as an API key and sometimes a host or region. It exports only sentinels and host choices, never the actual secret value.

**Data flow**: It receives an optional credential store, declared credential slots, and the workspace id. For each slot that has an injection target, it checks whether the workspace has a stored value and resolves the correct provider host. If the slot is unset, unreadable, or has no valid host, it skips that slot, warning for unexpected failures. It returns a dictionary containing provider environment variables and host variables that are safe for the sandbox to see.

**Call relations**: `ProbeEnv.exports` calls this while building the sandbox environment. Inside, it uses `CredentialStore.get` to confirm a credential exists, `credential_host` to resolve the provider host, and `warn` to record problems without stopping unrelated sandbox work.

*Call graph*: calls 1 internal fn (get); called by 1 (exports); 2 external calls (warn, credential_host).


##### `_grant_cli_env`  (lines 158–205)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], authority: ExecutionAuthority, run_id: UUID) -> dict[str, str]
```

**Purpose**: Adds connector CLI environment variables based on the grants the current authority is allowed to use. A grant is permission to use a connected account, and this function turns the chosen account into a safe sentinel for the sandbox CLI.

**Data flow**: It receives an optional grant store, configured CLIs, the execution authority, and the current run id for logging. It finds the member id from the authority, loads active grants, and for each provider chooses usable accounts. If exactly one account is usable, it exports that CLI’s environment variable with the grant sentinel; if the CLI also supports Git, it may add Git author and committer identity. If multiple accounts would fit one variable, or multiple providers fight over Git identity, it logs and avoids silently choosing the wrong account. It returns the environment variables it safely derived.

**Call relations**: `ProbeEnv.exports` calls this to add grant-based connector access. This function calls `active_grants` to see current permissions, `usable_cli_accounts` to narrow them for a provider and member, `grant_sentinel` to create the placeholder value, `_git_identity_env` to derive Git commit identity, and `log` when ambiguity would make automation unsafe.

*Call graph*: calls 2 internal fn (_git_identity_env, active_grants); called by 1 (exports); 4 external calls (log, grant_sentinel, usable_cli_accounts, authority_member_id).


##### `_git_identity_env`  (lines 208–237)

```
def _git_identity_env(granted: tuple[Grant, ...], provider: str, account_id: str) -> dict[str, str]
```

**Purpose**: Builds the Git author and committer variables for a specific connected account. This helps commits made in the sandbox be attributed to the same account used to authenticate the Git push.

**Data flow**: It receives all active grants, a provider name, and an account id. It finds the matching grant and checks whether that grant includes commit identity information. If it does, it returns Git author and committer name/email variables; otherwise it returns an empty dictionary so Git will not be given a misleading identity.

**Call relations**: `_grant_cli_env` calls this after it has selected exactly one usable account for a Git-capable connector. Its output is merged into the CLI environment unless another provider has already claimed the Git identity, in which case `_grant_cli_env` removes the identity to avoid accidental attribution.

*Call graph*: called by 1 (_grant_cli_env).


### `core/src/ufo/harness/sandbox/client_binary.py`

`util` · `sandbox setup and tests that need a real client binary`

A sandbox needs a real `ufo` executable inside it, because commands like `ufo fs` and `ufo llm` live in that compiled client program. Unlike a simple script, a compiled program must be built ahead of time. This file is the project’s single “lost and found desk” for that executable.

It checks for the binary in a careful order. First, it looks for an environment variable named `UFO_CLIENT_BINARY`. That lets a continuous integration job or release pipeline say, “Use this exact file I already built.” If that path is wrong, the file raises an error instead of guessing.

If no override is given, it looks in the normal Rust build output folders under the repository’s `client` crate, trying release builds before debug builds. If the caller asks for a specific Rust target triple, meaning a platform name such as Linux on a certain CPU type, it searches that target’s build folder. If no target is requested, it also falls back to a `ufo` already installed on the local command path.

The important rule is that this file never starts a build. Building can take minutes and belongs to the client build pipeline, not to sandbox startup. If nothing suitable is found, the error message tells the user exactly which Cargo command to run or how to point `UFO_CLIENT_BINARY` at an existing artifact.

#### Function details

##### `client_binary`  (lines 32–59)

```
def client_binary(target: str | None=None) -> Path
```

**Purpose**: Finds the already-built `ufo` executable that should be used for a sandbox or local subprocess. A caller can ask for the host machine’s binary, or for a binary built for another platform by passing a Rust target triple.

**Data flow**: It takes an optional target platform name. It first reads the `UFO_CLIENT_BINARY` environment variable; if set, that path must point to a real file and is returned. If there is no override, it builds possible paths under the repository’s Rust client build directory, checking release and debug outputs. For host binaries only, it also asks the operating system whether `ufo` is installed on the command path. If every check fails, it raises a `RuntimeError` that explains how to build or provide the missing file.

**Call relations**: Sandbox image creation, local carrier code, and tests can all call this function so they agree on where the client binary comes from. Inside the function, `pathlib.Path` is used to turn text paths into path objects and test whether files exist, while `shutil.which` is used only for the host-machine case to find an installed `ufo` command on the normal command path.

*Call graph*: 2 external calls (Path, which).


### `core/src/ufo/harness/sandbox/cache.py`

`config` · `startup and sandbox network setup`

A sandbox often needs to download source code or packages, such as GitHub repositories, npm packages, Python packages, or Ubuntu packages. This file names the approved cache service and the public internet hosts that may be routed through it. Think of it like a mailroom: sandboxes ask for outside items, but the request is sent through a known internal desk that checks, stores, and forwards only allowed deliveries.

The main cache host is fixed as `cache.ufo.internal`. Git downloads for selected hosts, currently GitHub, can be rewritten so normal fetches go to this internal cache instead of directly to GitHub. Pushes are kept direct, so the cache is only used for reading code, not publishing changes.

The file also lists package registry and download hosts that the proxy can transparently route through the cache. This matters because many package managers follow extra download links behind the scenes; those links still need to be cached and controlled.

Finally, the file defines the environment variable name used for a cache control token, and includes a small parser for a cache daemon address. If that address is malformed, it raises an error rather than silently disabling the cache, because a bad deployment setting should be noticed immediately.

#### Function details

##### `cache_git_config`  (lines 46–54)

```
def cache_git_config() -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the Git configuration entries that make fetches for cached hosts go through the internal cache. It also adds a matching push rule so pushes still go to the real origin instead of the cache.

**Data flow**: It reads the fixed cache host and the list of Git hosts that are allowed to use the cache. For each host, it creates two Git setting pairs: one that rewrites normal download URLs toward the cache, and one that preserves direct push behavior. It returns these settings as an immutable tuple, ready for another part of the system to apply to Git.

**Call relations**: This function is used when the sandbox environment needs Git to behave safely by default. A caller asks it for the needed Git settings, then applies those settings before Git fetch operations happen, so repository reads can be cached while writes are not redirected.


##### `parse_cache_daemon`  (lines 57–66)

```
def parse_cache_daemon(value: str | None) -> tuple[str, int] | None
```

**Purpose**: Turns a deployment setting like `host:port` into a host name and numeric port for the local cache daemon. If there is no value, it clearly reports that no cache daemon is configured.

**Data flow**: It receives either a text value or `None`. If the value is `None`, it returns `None`. Otherwise, it splits the text at the final colon, checks that a host and separator exist, converts the port part to a number, and returns the result as `(host, port)`. If the text is missing the required `host:port` shape, it raises an error so the bad configuration is not ignored.

**Call relations**: This function belongs to deployment or startup configuration flow. A caller provides the raw cache daemon setting, and this function either hands back a usable address or stops the setup with a clear error when the setting is broken.


### `core/src/ufo/runtime/tools/tasks.py`

`domain_logic` · `tool command execution`

This file solves a common shell-tool problem: a command may take longer than the tool wants to wait, but killing it would waste useful work. Instead, every command is launched through a small task journal under the run directory. Think of it like giving each command a mailbox: its log, process id, and exit file are written in predictable places, so later code can check the same mailbox instead of starting the work again.

The main path is `run_task`. It chooses a stable task id when the current tool call has an idempotency key, starts the command through the sandbox, and waits only for the allowed foreground time. If the command finishes in time, its normal result is returned. If the wait expires, the file probes whether the detached supervisor process is still alive or has already written its exit file. A still-running command is reported as detached, with handles for reading its log, watching for completion, or stopping it.

The file also protects the system from commands that simply pad a turn with a long flat `sleep`, while allowing normal polling loops. If the sandbox itself appears to stop answering, it records a short diagnostic snapshot, such as load and disk information, but it does not let that diagnostic failure hide the original timeout.

#### Function details

##### `flat_sleeps`  (lines 33–51)

```
def flat_sleeps(command: str) -> tuple[int, ...]
```

**Purpose**: Finds long, plain `sleep` commands that would only waste the foreground wait time. It ignores sleeps inside shell loops, quotes, and heredoc text because those are usually data or polling delays rather than padding.

**Data flow**: It takes a shell command as text. It first blanks out quoted strings and heredoc bodies, then scans the remaining command for `sleep`, `do`, and `done`. It tracks whether the scan is inside a loop, keeps only sleeps outside loops, filters out sleeps of 10 seconds or less, and returns the remaining sleep lengths as a tuple of numbers.

**Call relations**: No in-file caller is shown in the provided graph. It is a screening helper for the surrounding tool layer, used before running a command so obviously padded foreground waits can be refused instead of launched.


##### `run_task`  (lines 94–139)

```
async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None, *, model_authored: bool) -> TaskRun
```

**Purpose**: Launches one shell command through the durable task journal and waits only as long as the caller is willing to wait. If the command outlives that wait, it keeps running and the function returns enough information for later code to point the user at the task files.

**Data flow**: It receives a tool context, command text, an optional timeout in milliseconds, and a flag saying whether the model wrote the command. It converts the timeout to seconds, caps it at the maximum allowed time, asks `task_id` for the journal name, builds paths in the sandbox, and starts the command through the sandbox task runner. If the command finishes, it returns a `TaskRun` with the result and no process id. If the wait times out, it probes the task files to see whether the supervisor process is still alive or already has an exit file; if nothing is found, it records diagnostic information. It then returns a `TaskRun` containing the original result, the requested wait, any discovered process id, and the display path for the task files.

**Call relations**: This is the central workflow in the file. It calls `task_id` first so repeated dispatch attempts can attach to the same journal entry. If a timeout looks suspicious because no detached process can be found, it calls `_record_exec_timeout` to log what the sandbox looked like at that moment. It constructs and returns a `TaskRun`, which later code can use to decide whether to show a normal result or detached-task handles.

*Call graph*: calls 2 internal fn (_record_exec_timeout, task_id); 1 external calls (__init__).


##### `task_id`  (lines 142–149)

```
def task_id(ctx: ToolContext) -> str
```

**Purpose**: Chooses the short name used for a task’s journal files. When possible, it makes the name repeatable so a retried tool step finds the first launch instead of starting the same command twice.

**Data flow**: It reads the idempotency key from the tool context. If there is no key, it creates a fresh random id and returns its first eight hex characters. If there is a key, it hashes that key with SHA-256 and returns the first eight hex characters, giving the same answer every time for the same recorded step.

**Call relations**: `run_task` calls this before launching the command. That makes the task journal stable across crash recovery or retry when the context has an idempotency key, while still allowing ordinary unrecorded contexts to create new independent tasks.

*Call graph*: called by 1 (run_task); 2 external calls (sha256, uuid4).


##### `task_handles`  (lines 152–173)

```
def task_handles(task: str, pid: str, display_base: str, applied_s: int | None=None, note: str='') -> str
```

**Purpose**: Builds the user-facing message for a detached command. The message explains whether the command was detached from the start or kept running after a timeout, and gives concrete commands and file paths for following it.

**Data flow**: It receives the task id, supervisor process id, display path base, an optional applied timeout, and an optional note. It chooses the right lead sentence, builds a small JSON object containing the task id, pid, log file, exit file, watch command, and stop command, then returns one combined text block with the explanation plus the JSON payload.

**Call relations**: No in-file caller is shown in the provided graph. It is the formatting partner to `run_task`: after a task is known to be detached, surrounding tool code can call this to hand the user stable handles instead of stale partial output.

*Call graph*: 1 external calls (dumps).


##### `timeout_notice`  (lines 176–192)

```
def timeout_notice(applied_s: int, requested_s: int | None) -> str
```

**Purpose**: Creates a clear timeout explanation for a command that was stopped by the sandbox. It distinguishes between the default timeout, the maximum cap, and an explicitly requested timeout.

**Data flow**: It takes the timeout that actually applied and the timeout the caller originally requested, if any. If the caller requested nothing, it returns a message explaining the default. If the caller requested more than the allowed maximum, it says the request was capped. Otherwise, it says the command was stopped after the applied number of seconds.

**Call relations**: No in-file caller is shown in the provided graph. It supplies consistent wording for callers that need to explain why a foreground command ended, especially because an exit code alone may not reveal which timeout caused the stop.


##### `_record_exec_timeout`  (lines 195–228)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: Records a short diagnostic snapshot when a command times out and the detached task cannot be found alive. This helps tell the difference between slow user work and a sandbox command channel that stopped responding.

**Data flow**: It receives the tool context, command text, applied timeout, and requested timeout. It tries, within a small extra deadline, to run a simple health check inside the sandbox: load average, memory, and workspace disk usage. Whether that probe succeeds or fails, it writes a structured log entry with the profile, timeout values, a shortened command, whether the vitals were reached, and the vitals text if available. It returns nothing and deliberately swallows diagnostic failures.

**Call relations**: `run_task` calls this only after a timed-out task cannot be confirmed as still alive or already completed. Inside, it uses `asyncio.timeout` to keep the diagnostic bounded, `turn_profile` to label the log with the current turn’s profile, and `log` to emit the record. Its result never changes the command outcome; it only adds evidence for later troubleshooting.

*Call graph*: called by 1 (run_task); 3 external calls (timeout, log, turn_profile).


### Controlled egress policy
These files expose the proxy-facing access API and resolve concrete outbound network, metering, and secret-injection rules for each sandboxed run.

### `core/src/ufo/runtime/access/egress_control.py`

`io_transport` · `request handling`

The egress proxy is the part that sits on the network path and enforces outbound connections from a sandbox. But it deliberately does not know the real policy rules, customer credentials, or billing logic. This file is the control desk it calls back to. Think of the proxy as a security guard at a door, and this file as the office that checks the guest list, hands over temporary instructions, and records what happened.

The routes are private FastAPI endpoints, protected by bearer tokens. One token protects the main egress routes, such as authorizing a run, resolving the set of allowed network rules, recording usage, and forwarding tool-bridge requests. A separate token protects the git-credential endpoint, so the cache daemon can ask only for Git credentials and cannot reach the broader secrets or metering API.

Incoming requests carry a run token or probe token. A run token identifies a real sandbox run; a probe token identifies a limited check. The file verifies those tokens, asks PerAgentRules whether the run or probe is still live, asks it for the rules or Git credential that apply, and records network and model-token usage through the billing accounting functions. It also converts Python rule objects into the exact JSON shape expected by the Rust proxy, so both sides agree on what each rule means.

#### Function details

##### `rule_json`  (lines 47–67)

```
def rule_json(rule: Rule) -> dict[str, object]
```

**Purpose**: Turns one internal egress rule into the small JSON object that the Rust proxy understands. This matters because the proxy and core must agree exactly on names like the rule kind, host list, injected header, or service prefix.

**Data flow**: It receives a rule object, checks which kind of rule it is, and builds a plain dictionary with the fields needed for that kind. The output is JSON-ready data that can be sent over the private API to the proxy.

**Call relations**: When the resolve endpoint has collected the rules for a run or probe, it calls this helper for each rule before returning them to the proxy. This is the translation step between core's Python policy objects and the proxy's wire format.

*Call graph*: called by 1 (_resolve).


##### `EgressControl.router`  (lines 144–150)

```
def router(self) -> APIRouter
```

**Purpose**: Builds the private FastAPI router for the main egress-control API. It attaches the endpoints the proxy uses for authorization, rule lookup, metering, and tool bridging.

**Data flow**: It starts with the control token guard as a required check for all routes under the internal egress prefix. It then registers the route functions and returns the completed router for the server to mount.

**Call relations**: During server setup, core calls this to expose the egress-control endpoints. Each request that arrives through this router first passes through the guard before reaching the matching method.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl.git_credential_router`  (lines 152–157)

```
def git_credential_router(self) -> APIRouter
```

**Purpose**: Builds a separate private router just for Git credential lookup by the cache daemon. It uses a different token so that cache-related code cannot call the more powerful egress secrets and metering endpoints.

**Data flow**: It creates an internal router, applies the cache-token guard to it, registers the git-credential route, and returns the router to be mounted by the server.

**Call relations**: This runs during server setup alongside the main router, but it creates a narrower entry point. Requests through this router go only to the Git credential callback after passing the cache-specific guard.

*Call graph*: 2 external calls (APIRouter, Depends).


##### `EgressControl._guard`  (lines 159–161)

```
async def _guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: Checks that a caller is allowed to use the main internal egress API. It rejects any request whose Authorization header does not exactly match the expected control token.

**Data flow**: It reads the Authorization header from the incoming request and compares it with the configured bearer token. If the value matches, the request continues; if not, it raises an HTTP 401 unauthorized error.

**Call relations**: FastAPI runs this before any route created by the main egress router. It is the first gate the proxy must pass before authorization, rule resolution, metering, or tool-bridge work happens.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._cache_guard`  (lines 163–165)

```
async def _cache_guard(self, authorization: Annotated[str, Header()]='') -> None
```

**Purpose**: Checks that a caller is allowed to use the Git credential callback. It protects that endpoint with its own separate token.

**Data flow**: It reads the Authorization header and compares it with the cache control token. A match lets the request proceed; a mismatch stops it with an HTTP 401 unauthorized error.

**Call relations**: FastAPI runs this before the git-credential endpoint. It keeps the cache daemon on a narrow path: even with its token, it can reach only the credential callback router, not the wider egress-control API.

*Call graph*: 1 external calls (HTTPException).


##### `EgressControl._authorize`  (lines 167–170)

```
async def _authorize(self, body: AuthorizeRequest) -> AuthorizeResponse
```

**Purpose**: Answers the proxy's basic question: is this run or probe token valid and still live? It also returns the current policy generation number when the caller is authorized, so the proxy can know whether its cached rules are fresh.

**Data flow**: It receives a body containing the raw proxy authorization value. It tries to turn that value into a run or probe principal, asks for the live generation if the token is valid, and returns an authorization response saying yes or no plus the generation when available.

**Call relations**: The proxy calls this before allowing traffic. This method relies on _principal to verify the token and on _live_generation to ask the rule resolver whether the identified run or probe is still active.

*Call graph*: calls 2 internal fn (_live_generation, _principal); 1 external calls (__init__).


##### `EgressControl._live_generation`  (lines 172–177)

```
async def _live_generation(self, principal: EgressPrincipal) -> int | None
```

**Purpose**: Checks whether a specific run or probe is still considered live and returns the rule generation that applies to it. The generation is like a version number for the policy the proxy may cache.

**Data flow**: It receives a verified run token or probe token. For a run token it asks the resolver about the live turn; for a probe token it asks about the live probe. The result is either an integer generation or nothing if it is not live.

**Call relations**: _authorize calls this after token verification. It is the bridge from identity checking to the policy resolver's liveness check.

*Call graph*: called by 1 (_authorize).


##### `EgressControl._resolve`  (lines 179–181)

```
async def _resolve(self, body: ResolveRequest) -> dict[str, object]
```

**Purpose**: Returns the full set of egress rules that apply to the supplied run or probe token. These rules tell the proxy what hosts are allowed, what credentials may be injected, what service routes exist, and what should be metered.

**Data flow**: It receives a proxy authorization value, turns it into a principal if possible, asks the resolver for the applicable rules, converts each rule into proxy-readable JSON, and returns them in a rules list.

**Call relations**: The proxy calls this when it needs policy details, often after authorization or when its cached generation is stale. This method uses _principal for token verification and rule_json to produce the exact wire format expected by the proxy.

*Call graph*: calls 2 internal fn (_principal, rule_json).


##### `EgressControl._meter`  (lines 183–238)

```
async def _meter(self, body: MeterRequest) -> dict[str, object]
```

**Purpose**: Accepts usage records from the proxy and writes them into metrics and billing. It groups repeated records together first, so billing writers receive compact totals rather than one tiny write per network event or token report.

**Data flow**: It receives a list of metering records. Metric records are counted by host and dimension and emitted as observability metrics. Egress records are counted by workspace and turn, with probe traffic handled separately when there is no turn. Token records are grouped by workspace, turn, and model, with token counts added together. It then enters each workspace context, opens a workspace database transaction, writes the egress and token accounting records, and returns an empty response.

**Call relations**: The proxy calls this after it observes outbound requests or model token usage. This method hands observability counts to the metric emitter, hands network usage to the egress accounting writers, and hands token usage to the sandbox token accounting writer after applying _priced_cache_write.

*Call graph*: calls 1 internal fn (_priced_cache_write); 7 external calls (__init__, workspace_tx, emit_metric, record_egress_request, record_probe_egress_request, record_sandbox_tokens, ws).


##### `EgressControl._priced_cache_write`  (lines 240–253)

```
def _priced_cache_write(self, model: str, usage: Usage) -> Usage
```

**Purpose**: Adjusts token usage when a model does not have a special price for 30-minute cache writes. In that case, those cache-write tokens are billed as normal input tokens instead.

**Data flow**: It receives a model name and a Usage object. It looks up the model's pricing information. If the model supports 30-minute cache-write pricing, or there are no such tokens, it returns the usage unchanged. Otherwise it creates a copy where those cache-write tokens are moved into the regular input-token count.

**Call relations**: _meter calls this just before recording sandbox token usage. It keeps the billing behavior in core, where pricing data exists, instead of making the proxy understand pricing rules.

*Call graph*: called by 1 (_meter); 1 external calls (model_copy).


##### `EgressControl._tool_bridge`  (lines 255–260)

```
async def _tool_bridge(self, body: ToolBridgeControlRequest) -> ToolBridgeResponse
```

**Purpose**: Allows the proxy to forward a bounded tool request into the host-side tool bridge, but only for a valid run token. Probe tokens and missing bridge support are rejected.

**Data flow**: It receives a proxy authorization value and a tool request. It verifies the principal, confirms it is a run token and that a bridge is configured, enters the run's workspace context, sends the request to the bridge, and returns the bridge response. If the checks fail, it raises an HTTP 403 forbidden error.

**Call relations**: The proxy calls this when sandbox-side traffic needs to use the tool bridge. This method uses _principal for identity, the workspace context to scope the work correctly, and then delegates the actual tool dispatch to the configured bridge requester.

*Call graph*: calls 1 internal fn (_principal); 2 external calls (HTTPException, ws).


##### `EgressControl._git_credential`  (lines 262–279)

```
async def _git_credential(self, body: GitCredentialRequest) -> dict[str, object]
```

**Purpose**: Lets the cache daemon ask for the Git credential that the proxy would be allowed to inject for a particular run or probe and host. If no valid credential applies, it tells the daemon to fetch anonymously rather than borrowing someone else's identity.

**Data flow**: It receives an optional proxy authorization value and optional host. If either is missing or the token is invalid, it returns a public principal. Otherwise it asks the resolver for a Git credential for that principal and host. If none exists, it returns public/no credential; if one exists, it returns the username, token, and a principal label tied to the workspace and account.

**Call relations**: Requests reach this method only through the separate cache-protected router. It uses _principal to verify the run or probe token and then relies on the resolver to decide whether that exact principal may receive a credential for the requested host.

*Call graph*: calls 1 internal fn (_principal).


##### `EgressControl._principal`  (lines 281–291)

```
def _principal(self, proxy_auth: str) -> EgressPrincipal | None
```

**Purpose**: Turns the raw proxy authorization value into a verified identity, either a run token or a probe token. If the value is missing or invalid, it returns no principal instead of throwing an error.

**Data flow**: It receives the raw authorization string from a request body. It first tries to decode it as a run token using the configured run token codec. If that fails, it tries to decode it as a probe token using the same secret. The result is a verified token object or None.

**Call relations**: Authorization, rule resolution, tool bridging, and Git credential lookup all call this before trusting the request body. It is the common doorway that turns proxy-supplied token text into the scoped identity used by the rest of the file.

*Call graph*: called by 4 (_authorize, _git_credential, _resolve, _tool_bridge); 1 external calls (__init__).


### `core/src/ufo/runtime/access/egress_resolver.py`

`domain_logic` · `request handling`

This file is the policy brain behind egress control, meaning control over what a running agent may connect to on the internet or internal services. The low-level proxy asks this code, “For this token, what is allowed?” and this file answers without exposing secrets to the proxy itself.

The central idea is that access is resolved fresh each time. A run token or probe token names a workspace, an agent, and an authority. The resolver checks the database to make sure the turn or probe is still valid, then builds rules from several sources: the always-present base rules, the agent’s internet setting, internal service access such as the tool bridge, workspace credential slots, OAuth-style grants, command-line credentials, preview access, and cache hosts.

It also enforces isolation. An agent only gets that agent’s grants. A workspace only reads that workspace’s secrets. A member-only credential is usable only if that member still has a valid seat. If the token is missing, forged, expired, or points to a no-longer-running turn, the resolver gives either only the safe base rules or no rules at all.

A useful analogy is a hotel key desk. The proxy is the door guard, but this file is the desk that checks whether the guest is still checked in, which rooms they paid for, and which private keys they may receive.

#### Function details

##### `_seat_scope`  (lines 50–67)

```
def _seat_scope(workspace_id: UUID, authority: ExecutionAuthority) -> tuple[sa.ColumnElement[bool], ...]
```

**Purpose**: This helper adds the database condition that proves a member is still allowed to act in a workspace. Workspace-wide authority needs no extra check, but member-specific authority must show that the member is still seated, meaning still valid for that workspace.

**Data flow**: It receives a workspace ID and an execution authority. If the authority applies to the whole workspace, it returns no extra database filters. If it belongs to one member, it builds a database existence check for that member in that workspace with an active seat. If the authority is not one of the expected kinds, it raises an error instead of silently making a risky choice.

**Call relations**: The database-reading methods use this whenever they check whether a run, probe, or conversation is still legitimate. Those methods fold this condition into their queries so revoked members lose access immediately.

*Call graph*: called by 4 (_conversation_of, _turn_of, probe_live, turn_live); 2 external calls (exists, select).


##### `PerAgentRules.resolve`  (lines 110–166)

```
async def resolve(self, principal: EgressPrincipal | None) -> tuple[Rule, ...]
```

**Purpose**: This is the main rule builder. Given a run token, probe token, or no token, it returns the exact network rules the proxy should enforce for that principal right now.

**Data flow**: It starts with the principal presented by the proxy. With no principal, it returns only the base rules. With a run or probe token, it enters that token’s workspace, looks up the live agent and authority, and returns no rules if the token is no longer valid. If the authority is valid, it builds a rule list from base rules, optional internet rules, service access, cache hosts, preview authentication, stored credential injections, active grants, and command-line credential rules. For probe tokens, it removes the deployment’s model-key injection before returning the result.

**Call relations**: The proxy relies on this when deciding what a connection may do. It asks _turn_of or _conversation_of to confirm who the token represents, calls the rule-derivation helpers for credentials and grants, and finally uses _without_the_model_key for probes so probe executions do not inherit the model secret.

*Call graph*: calls 3 internal fn (_conversation_of, _turn_of, _without_the_model_key); 7 external calls (__init__, __init__, derive_cli_rules, derive_credential_rules, derive_grant_rules, agent, ws).


##### `PerAgentRules.git_credential`  (lines 168–215)

```
async def git_credential(self, principal: EgressPrincipal, host: str) -> tuple[GitWire, str, str] | None
```

**Purpose**: This finds the Git credential that a cache daemon should use when fetching from a specific Git host on behalf of a run or probe. If no exact, safe account is available, it returns nothing so the fetch can proceed anonymously.

**Data flow**: It receives a principal and a Git host name. It checks the token’s workspace and live authority, then reads the agent’s active grants. It searches configured command-line integrations for one whose Git settings match the requested host. For that provider, it asks which account is usable for this authority. Only when there is exactly one usable account does it request that account’s secret token. If reading the token fails, it logs a warning and keeps looking. On success it returns the Git wire settings, the token, and the account ID; otherwise it returns None.

**Call relations**: The cache daemon calls this when it wants to fetch repositories using the same identity the sandbox would expose through tools like a GitHub token. This method reuses _turn_of or _conversation_of for liveness, uses usable_cli_accounts to avoid picking the wrong account, and records secret-fetch failures with warn rather than breaking anonymous public clones.

*Call graph*: calls 2 internal fn (_conversation_of, _turn_of); 5 external calls (warn, usable_cli_accounts, agent, authority_member_id, ws).


##### `PerAgentRules._turn_of`  (lines 217–252)

```
async def _turn_of(self, run: RunToken) -> _Authority | None
```

**Purpose**: This looks up the agent and effective internet policy for a run token that names a running turn. It is the resolver’s way of proving that a run token still points to a live turn before granting access.

**Data flow**: It receives a run token. Inside a workspace database transaction, it looks for a turn with the token’s turn ID and workspace ID, requires the turn to be running, joins to the agent, and adds any needed member-seat check. If no row matches, it returns None. If a row matches, it reads optional runtime configuration and calculates whether internet access is still allowed, then returns an _Authority object containing the agent ID, internet permission, and execution authority.

**Call relations**: resolve calls this before building rules for a normal run, and git_credential calls it before selecting a Git account. It depends on _seat_scope so member revocation is reflected in the same database check.

*Call graph*: calls 1 internal fn (_seat_scope); called by 2 (git_credential, resolve); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `PerAgentRules._conversation_of`  (lines 254–286)

```
async def _conversation_of(self, probe: ProbeToken) -> _Authority | None
```

**Purpose**: This looks up the agent and internet policy for a probe token. A probe is tied to a conversation rather than a specific turn, so this method validates the conversation path instead.

**Data flow**: It receives a probe token. First it checks the token’s expiry time against the current time; expired probes immediately return None. It then queries the workspace database for the conversation and its agent, again including any member-seat requirement. If the conversation or authority is not valid, it returns None. Otherwise it returns an _Authority with the conversation’s agent, the agent’s internet setting, and the authority carried by the probe token.

**Call relations**: resolve calls this when building rules for probe executions, and git_credential calls it when a probe needs Git access. Like _turn_of, it relies on _seat_scope so member-specific access disappears when the member no longer has a seat.

*Call graph*: calls 1 internal fn (_seat_scope); called by 2 (git_credential, resolve); 4 external calls (__init__, now, select, workspace_tx).


##### `PerAgentRules._without_the_model_key`  (lines 288–299)

```
def _without_the_model_key(self, rules: tuple[Rule, ...]) -> tuple[Rule, ...]
```

**Purpose**: This removes the deployment’s own model-key injection from a rule set. It lets probes keep other allowed access, such as Git credentials or grants, without giving them the model API secret.

**Data flow**: It receives a tuple of rules. It filters out only those injection rules whose sentinel marker is the model-key sentinel. All other rules pass through unchanged. The result is a new tuple of rules safe for a probe context.

**Call relations**: resolve uses this at the end of probe rule resolution. The rest of the rule-building flow stays the same as a normal turn, but this final filter prevents model authentication from being enforced for probes.

*Call graph*: called by 1 (resolve).


##### `PerAgentRules.turn_live`  (lines 301–333)

```
async def turn_live(self, run: RunToken) -> int | None
```

**Purpose**: This is the live-authorization check for a run token. It answers whether the token still names a running turn, and if so returns the current egress-rules generation number.

**Data flow**: It receives a run token. In that token’s workspace, it queries the turn and workspace together, applying any member-seat condition. If the turn is missing or not running, it returns None. If the turn is still running, it returns the workspace’s current egress rules generation, a number that lets callers know which version of the rules is current.

**Call relations**: This is used as a gate before allowing connection behavior tied to a run. It shares the same member-seat logic through _seat_scope, but it deliberately reads fresh from the database instead of trusting a cached rule set.

*Call graph*: calls 1 internal fn (_seat_scope); 3 external calls (select, workspace_tx, ws).


##### `PerAgentRules.probe_live`  (lines 335–357)

```
async def probe_live(self, probe: ProbeToken) -> int | None
```

**Purpose**: This is the live-authorization check for a probe token. It confirms the probe is unexpired, still tied to an existing conversation, and still allowed by the current member or workspace authority.

**Data flow**: It receives a probe token. If the token has expired, it returns None immediately. Otherwise it enters the token’s workspace and queries for the conversation joined to its workspace, including any member-seat condition. If the conversation is valid, it returns the workspace’s current egress rules generation; if not, it returns None.

**Call relations**: This plays the same gatekeeping role for probes that turn_live plays for runs. It uses _seat_scope to respect member revocation and reads the current workspace generation so callers can compare against the latest egress policy.

*Call graph*: calls 1 internal fn (_seat_scope); 4 external calls (now, select, workspace_tx, ws).


### `core/src/ufo/runtime/access/egress_rules.py`

`domain_logic` · `turn setup and request handling`

A sandboxed agent should not be able to freely send data anywhere, and it should not be handed raw secrets unless they are truly needed. This file builds the rulebook used by the egress proxy, which is like a security guard at the sandbox door. The rules say which exact hosts are allowed, whether public internet is allowed, which requests count toward usage, which local service hosts may be reached, and when a harmless placeholder value should be replaced with a real secret only as the request leaves the sandbox.

The file does not register an API or talk to the network itself. Instead, it derives plain rule objects from the run’s situation. If the model is OpenAI or Anthropic, it allows that provider, injects the model key into the correct header, and meters token usage. If an extension asks for internet access, it adds a public internet rule. If artifacts are stored in S3, it allows only the S3 upload host needed to share files. If a workspace has stored credentials, it allows their target host and injects their secret; broken or missing slots are skipped rather than failing the whole run. Grants work similarly: granted connector hosts are allowed and metered, while CLI credentials can inject account tokens when the run has authority to use them.

Without this file, the proxy would not know the difference between a safe, intended outbound request and an accidental or dangerous one.

#### Function details

##### `provider_host`  (lines 95–99)

```
def provider_host(model: str) -> str
```

**Purpose**: This function figures out which model provider host belongs to a model name. For example, model names starting with OpenAI-style prefixes map to OpenAI’s API host, while Claude names map to Anthropic’s host.

**Data flow**: It receives a model name as text. It compares the beginning of that name against known prefixes, then returns the matching provider host. If no known prefix matches, it raises an error because the system cannot safely build network rules for an unknown model provider.

**Call relations**: This is the first lookup used by derive_model_rules. That later function needs the host before it can allow traffic, choose the right authorization header, and build secret-injection and metering rules.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 102–117)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: This function builds the basic network rules needed for the run’s language model provider. It allows the provider host, arranges for the real model API key to replace the sandbox’s placeholder, and marks model traffic for token metering.

**Data flow**: It receives a model name and the real API key for that model provider. It finds the provider host, picks the provider’s expected authorization header format, then produces three rules: allow this host, swap the sentinel key for the real key on outgoing requests, and meter the host under token usage.

**Call relations**: It calls provider_host to identify where the model traffic goes. It then creates ScopeRule, InjectionRule, and MeterRule objects that the egress proxy later reads when deciding whether to connect, whether to rewrite a header, and how to count usage.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 120–122)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: This function checks whether any extension manifest asks for sandbox internet access. If so, it adds the rule that lets live turns reach the public internet.

**Data flow**: It receives the deploy’s manifests. It looks for any manifest with sandbox internet enabled. If it finds one, it returns an InternetRule; otherwise it returns no rules.

**Call relations**: This function contributes one possible piece to the larger rule set built before a run. Unlike exact host rules, its InternetRule is broad, so it is only added when a manifest explicitly asks for that wider access.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 125–141)

```
async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]
```

**Purpose**: This function gives the sandbox just enough network access to share produced files when the artifact store is S3. It avoids granting general internet access just because file upload needs a presigned S3 URL.

**Data flow**: It receives the configured blob store. If the store is S3, it asks the store for the upload host and returns rules allowing that exact host and metering requests to it. If the store is local filesystem storage, it returns no network rules because no outbound network upload is needed.

**Call relations**: During rule derivation, this function adds storage-specific permissions alongside model, credential, and grant rules. It creates ScopeRule and MeterRule entries so the egress proxy can permit S3 file sharing without opening unrelated internet destinations.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 144–199)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: This function turns workspace credential slots into safe network permissions and secret-injection rules. A credential slot is a declared place where a secret may be used; if the workspace has a real value for it, the proxy can swap it in only for the intended host and header.

**Data flow**: It receives credential slot declarations, a workspace ID, and the credential store. For each slot that declares an injection target, it tries to read the real secret and resolve the host. Missing, unset, or failing slots are skipped, with warnings for unexpected failures. It groups successful injections by host, then returns rules that allow each host, inject the needed headers, and meter requests when the slot declared a metering dimension.

**Call relations**: This function is called as part of building the run’s overall egress policy. It reads from CredentialStore.get and credential_host, logs warnings through warn when a single slot cannot be used, and emits ScopeRule, InjectionRule, and MeterRule objects. Its important behavior is that one bad credential slot does not break all other egress rules.

*Call graph*: calls 1 internal fn (get); 5 external calls (__init__, __init__, __init__, warn, credential_host).


##### `derive_grant_rules`  (lines 202–220)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: This function converts active connector grants into allowed hosts and request-metering rules. A grant means the run has permission to talk to a connector provider or its related file-transfer hosts.

**Data flow**: It receives the active grants and, optionally, a lookup object for connector transfer hosts. For each grant, it gathers the grant’s main host plus any extra transfer hosts, removes duplicates and empty values, then returns a rule allowing those hosts and a request-metering rule for each one.

**Call relations**: This function is part of the grant side of egress setup. It may call ConnectorTransferHosts.of to add broker file-store hosts, then creates ScopeRule and MeterRule objects for the proxy. It deliberately does not inject secrets; CLI token injection is handled separately by derive_cli_rules.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 223–270)

```
async def derive_cli_rules(grants: tuple[Grant, ...], authority: ExecutionAuthority, clis: Mapping[str, CliCredential], workspace_id: UUID) -> tuple[Rule, ...]
```

**Purpose**: This function builds secret-injection rules for connector command-line credentials when a granted account is allowed for the current execution authority. It lets the sandbox send placeholder tokens while the proxy replaces them with real account tokens at the last moment.

**Data flow**: It receives grants, the execution authority, known CLI credential declarations, and a workspace ID. It checks whether each grant has a matching CLI credential and whether the authority may use that grant. For allowed grants, it asks the credential broker for the real token. If that succeeds, it creates injection rules for the provider host, and possibly for a related Git host; if it fails, it logs a warning and skips only that grant. Git hosts are grouped so each host is allowed and metered once, even if several injections apply.

**Call relations**: This function complements derive_grant_rules. Grant rules allow and meter provider traffic, while this function adds the credential swapping needed by CLI-based connectors and separately scopes Git hosts when needed. It uses authority_member_id to decide ownership, grant_sentinel to identify the placeholder token, and warn to report per-grant credential failures.

*Call graph*: 6 external calls (__init__, __init__, __init__, warn, grant_sentinel, authority_member_id).


##### `ConnectorTransferHosts.of`  (lines 284–285)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This method answers the question: for a given connector provider, which file-transfer hosts should its grant also allow? It gives provider-specific hosts when known, otherwise it falls back to the open connector namespace default.

**Data flow**: It receives a provider name. It looks in the explicit provider-to-hosts mapping. If that provider is present, it returns those hosts; if not, it returns the default host tuple.

**Call relations**: derive_grant_rules uses this lookup while building grant-based network permissions. This keeps the grant logic simple: it asks this object for extra hosts, then adds them to the allowed and metered host set.


##### `connector_transfer_hosts`  (lines 288–299)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: This function builds the transfer-host lookup used by grant rule derivation. It reads connector declarations from manifests so file-transfer permissions match what the deployed connectors actually declare.

**Data flow**: It receives the deploy’s manifests. It collects every registered connector’s provider name and declared transfer hosts into an explicit mapping. It also checks for an open connector namespace and uses that namespace’s transfer hosts as the default for providers that are not explicitly registered. It returns a ConnectorTransferHosts object containing both pieces.

**Call relations**: This prepares data for derive_grant_rules. It calls open_connector_namespace to find the default namespace, then creates ConnectorTransferHosts so later grant processing can ask for transfer hosts provider by provider.

*Call graph*: 2 external calls (__init__, open_connector_namespace).
