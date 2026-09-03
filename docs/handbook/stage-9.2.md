# Sandbox carrier lifecycle  `stage-9.2`

This stage is the backstage workshop for each conversation. It provides the place where commands run, files are stored, and temporary services can be reached. The rest of the system talks to it through a common “sandbox session,” which is a safe doorway for running commands, reading and writing files, loading skills, and dialing ports without caring whether the workspace is local, Docker, E2B, or a user’s terminal.

conversation.py makes sure a conversation keeps returning to the same private workspace. local.py runs work directly on the host for simple development. terminal.py sends work to a connected user terminal. The Docker and E2B extensions provide stronger carriers: they create or reconnect to containers or cloud sandboxes, prepare them, move files, run commands, route network traffic through the proxy, and stop or restart them when needed.

Supporting pieces keep setup predictable. cache.py points sandbox traffic at approved caches. client_binary.py finds the prebuilt UFO client without building it. exec_env.py prepares safe environment variables, using placeholders instead of real secrets. __init__.py simply makes this sandbox code importable.

## Files in this stage

### Conversation workspace entry
Conversation-level orchestration ensures each conversation consistently reuses the same sandbox-backed workspace.

### `core/src/ufo/harness/sandbox/conversation.py`

`orchestration` · `request handling and cross-cutting workspace access`

A conversation’s `/workspace` is like a shared project folder. Many things may need it: a running turn, an uploaded attachment, a file browser, or a background job trimming old files. This file makes all of those paths go through one coordinator, `ConversationSandbox`, so they agree on where the workspace is and which sandbox owns it.

The main job is to open a sandbox safely. If the conversation already has a stored sandbox handle, this code resumes that exact sandbox. If not, it creates one and saves the handle in the database. If two callers try to create the first sandbox at the same time, it uses a compare-and-swap database update, meaning “save this only if the value is still what I saw.” The loser then adopts the winner’s sandbox, so files are not stranded in an unreferenced place.

The file also separates “turn” access from “off-turn” access. Off-turn work, such as reading files or landing an attachment, uses an unsigned token with no network permission. Reads are especially careful: they never create a sandbox just because someone asked to browse files.

It also supports terminal-bound workspaces, where a user’s connected local terminal supplies the workspace, and regular carrier-backed sandboxes, where storage is under a configured workspace root. Extra care is taken to avoid unsafe filesystem tricks such as symlinks escaping the allowed directory.

#### Function details

##### `ConversationSandbox._route`  (lines 105–116)

```
def _route(self, stored: str | None) -> tuple[Carrier, str, bool]
```

**Purpose**: Chooses which sandbox provider should be used for an already stored sandbox handle. This matters because a workspace must be reopened by the same kind of backend that originally created it.

**Data flow**: It receives a stored handle, or no handle for a new conversation. If the handle names a configured resume backend, it returns that backend’s carrier, name, and off-cluster flag. Otherwise it falls back to this deployment’s normal carrier settings.

**Call relations**: When `open` needs to create or resume a sandbox, `_opened` asks `_route` where to go. When read-only access checks an existing sandbox, `existing` also asks `_route` so it does not accidentally contact the wrong provider.

*Call graph*: called by 2 (_opened, existing); 1 external calls (sandbox_handle_backend).


##### `ConversationSandbox.open`  (lines 118–170)

```
async def open(self, conversation_id: UUID, turn_id: UUID | None, run_token: str, env: Mapping[str, str]) -> SandboxSession
```

**Purpose**: Creates or resumes the sandbox for a conversation and makes sure the chosen sandbox handle is saved durably. This is the main entry used when a turn or an off-turn write needs real workspace access.

**Data flow**: It starts by reading the conversation’s current stored handle and sandbox size from the database. It then opens a sandbox through `_opened`, builds the durable handle string, and tries to claim that handle in the database. If another caller won the race, it retries using the winner’s stored handle. It returns a `SandboxSession`, which is the object callers use to read, write, or run commands in the sandbox.

**Call relations**: Workspace writes call this before copying files in, and the runtime queue uses it when a turn needs a sandbox. It delegates the actual provider choice to `_opened`, the database read to `_binding`, and the race-safe save to `_claim`.

*Call graph*: calls 3 internal fn (_binding, _claim, _opened); called by 3 (write, write_runtime, _open_sandbox); 1 external calls (__init__).


##### `ConversationSandbox.existing`  (lines 172–234)

```
async def existing(self, conversation_id: UUID) -> SandboxSession | None
```

**Purpose**: Opens a sandbox only if the conversation already has one that can be reached. It is the safe read path: browsing or reading files must not create a new workspace as a side effect.

**Data flow**: It reads the stored sandbox handle. If none exists, it returns `None`. If the handle points to a terminal workspace, it tries to attach through the terminal carrier. Otherwise it routes the handle to the right backend, checks that the needed local directory already exists when appropriate, and attaches to the sandbox. It returns a `SandboxSession` if reachable, or `None` if not.

**Call relations**: File listing, pruning, and reading all call `existing` first. It uses `_stored` to fetch the saved handle, `_route` to choose the backend, and helper functions from the sandbox session layer to interpret the handle.

*Call graph*: calls 2 internal fn (_route, _stored); called by 4 (entries, prune, prune_runtime, read); 5 external calls (__init__, __init__, __init__, to_thread, sandbox_handle_id).


##### `ConversationSandbox.claim_terminal`  (lines 236–246)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a conversation that has no sandbox yet to a user’s connected terminal directory. This lets a later turn use the terminal’s workspace even if the actual sandbox open happens after the connection moment.

**Data flow**: It receives a conversation id and a current working directory. It formats that directory as a terminal-backed sandbox handle, checks whether the conversation already has a handle, and if not tries to save the terminal handle with `_claim`. It returns `true` only if this call made the binding.

**Call relations**: This is used during terminal admission or connection setup. It reads through `_stored` and writes through `_claim`, using the same race-safe database rule as normal sandbox creation.

*Call graph*: calls 2 internal fn (_claim, _stored).


##### `ConversationSandbox.write`  (lines 248–259)

```
async def write(self, conversation_id: UUID, rel: str, content: bytes) -> str
```

**Purpose**: Copies a bounded chunk of bytes into a conversation’s visible workspace. It is used for things like landing an uploaded attachment where the agent can later read it under `/workspace`.

**Data flow**: It receives a conversation id, a relative file path, and bytes to write. It rejects content over the maximum size limit, opens the sandbox off-turn, writes the bytes into the requested relative path, and returns the `/workspace/...` path that the agent will see.

**Call relations**: This function calls `open` because writing is allowed to create the workspace if needed. After `open` returns a session, the actual file copy is handed to the session object.

*Call graph*: calls 1 internal fn (open); 1 external calls (workspace_path).


##### `ConversationSandbox.write_runtime`  (lines 261–273)

```
async def write_runtime(self, conversation_id: UUID, category: str, rel: str, content: bytes) -> str
```

**Purpose**: Writes internal runtime output into a named runtime area instead of the user-visible workspace root. This is for system-produced files that still belong to the conversation but are grouped by category.

**Data flow**: It receives a conversation id, category, relative path, and bytes. It checks the size limit, opens the sandbox off-turn, combines the category and path, writes the bytes into the runtime storage area, and returns a display path for that runtime file.

**Call relations**: Like `write`, it calls `open` because an off-turn writer may need to create the sandbox. It then relies on the returned session to place the file and translate its internal path into something displayable.

*Call graph*: calls 1 internal fn (open).


##### `ConversationSandbox.prune`  (lines 275–286)

```
async def prune(self, conversation_id: UUID, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files directly under a workspace subdirectory, keeping only the newest requested number. This prevents unattended off-turn writers, such as log appenders, from growing forever.

**Data flow**: It receives a conversation id, a relative directory prefix, and a keep count. It attaches only if a sandbox already exists. If there is one, it runs a small Python cleanup program inside the sandbox that finds regular files in the target directory and deletes the oldest extras. It raises an error if that cleanup program fails.

**Call relations**: It calls `existing` because pruning should not create a workspace. It passes the cleanup work to the sandbox session’s Python runner, using the shared `PRUNE_PROG` script for safe in-sandbox deletion.

*Call graph*: calls 1 internal fn (existing); 1 external calls (workspace_path).


##### `ConversationSandbox.prune_runtime`  (lines 288–300)

```
async def prune_runtime(self, conversation_id: UUID, category: str, rel_prefix: str, keep: int) -> None
```

**Purpose**: Deletes older files from an internal runtime directory, keeping only the newest requested number. It is the runtime-storage version of `prune`.

**Data flow**: It receives a conversation id, category, relative directory prefix, and keep count. It attaches only to an existing sandbox, asks the session for the target runtime paths, runs the safe pruning script there, and raises an error if pruning fails.

**Call relations**: It follows the same pattern as `prune`: call `existing`, do nothing if there is no sandbox, and delegate deletion to the in-sandbox Python program so the view matches what the sandbox sees.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox.entries`  (lines 302–341)

```
async def entries(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Returns the list of member-visible files in the conversation workspace. It is used by a file browser or similar surface to show what files exist without creating a workspace just for the listing.

**Data flow**: It receives a conversation id and attaches only if a sandbox already exists. It asks the sandbox to run a filesystem glob search under `/workspace`, excluding names such as `.git`. It validates the result, warns if the listing was truncated, converts each file into a `WorkspaceFile` with relative path, size, and modification time, sorts them by path, and returns the tuple.

**Call relations**: It depends on `existing` for safe read-only access. For each returned path, it calls `_workspace_rel` to turn either an in-container path or host-side path into the relative path the user should see.

*Call graph*: calls 2 internal fn (_workspace_rel, existing); 3 external calls (__init__, fromtimestamp, warn).


##### `ConversationSandbox._workspace_rel`  (lines 343–347)

```
def _workspace_rel(self, handle: SandboxHandle, path: str) -> str
```

**Purpose**: Turns a full workspace path into a clean path relative to the workspace root. This keeps file listings from exposing container or host filesystem details.

**Data flow**: It receives a sandbox handle and a path returned by the workspace walker. It checks whether the path starts with `/workspace/` or with the handle’s host workspace directory. If so, it strips that root and returns the relative part. If the path is outside both expected roots, it raises an error.

**Call relations**: `entries` calls this while shaping raw filesystem results into user-facing `WorkspaceFile` objects. It acts as a safety check between low-level path output and the file browser view.

*Call graph*: called by 1 (entries).


##### `ConversationSandbox.read`  (lines 349–357)

```
async def read(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Returns a stream of bytes for one workspace file, if it exists. It is careful not to create a sandbox just because someone tried to read a file.

**Data flow**: It receives a conversation id and a relative path. It attaches through `existing`; if no sandbox is reachable, it returns `None`. It then checks whether the file exists. If not, it returns `None`; otherwise it returns an async byte stream for the file contents.

**Call relations**: Read surfaces call this when a user wants a file. It relies on `existing` for the no-side-effect rule and hands the actual file existence check and streaming to the sandbox session.

*Call graph*: calls 1 internal fn (existing).


##### `ConversationSandbox._opened`  (lines 359–418)

```
async def _opened(self, conversation_id: UUID, turn_id: UUID | None, stored: str | None, run_token: str, env: Mapping[str, str], size: str) -> tuple[str, Carrier, SandboxHandle]
```

**Purpose**: Does the actual work of choosing and opening the right sandbox carrier for a conversation. It is where terminal workspaces, stored handles, resume backends, and normal sandbox storage come together.

**Data flow**: It receives the conversation id, optional turn id, stored handle, run token, environment variables, and desired sandbox size. If the conversation is bound to a terminal, or a live terminal is available for a brand-new conversation, it opens through a `TerminalCarrier`. Otherwise it routes the stored handle or new conversation to the configured carrier, prepares the host workspace path when needed, fixes ownership when running as root, and asks the carrier to create or resume the sandbox. It returns the backend name, carrier, and opened handle.

**Call relations**: `open` calls `_opened` inside its claim-and-retry loop. `_opened` calls `_route` for non-terminal backends and builds the sandbox specification that is passed to the selected carrier.

*Call graph*: calls 1 internal fn (_route); called by 1 (open); 5 external calls (__init__, __init__, to_thread, geteuid, sandbox_handle_id).


##### `ConversationSandbox._provisioned_dir`  (lines 420–435)

```
def _provisioned_dir(self, conversation_id: UUID) -> Path
```

**Purpose**: Creates and verifies the host directory used as a conversation’s workspace for local carrier-backed sandboxes. It exists to make directory creation safe, especially against symlink tricks.

**Data flow**: It receives a conversation id. It ensures the workspace root exists, resolves the configured root according to the deployment setting, then creates and verifies the conversation-specific directory under that root using containment checks. It returns the safe directory path.

**Call relations**: `_opened` calls this when it needs a local host directory for a sandbox. The containment helpers do the low-level safety work so the later sandbox mount cannot be pointed outside the allowed root.

*Call graph*: 3 external calls (suppress, configured_root, contained_dir).


##### `ConversationSandbox._existing_dir`  (lines 437–448)

```
def _existing_dir(self, conversation_id: UUID) -> Path | None
```

**Purpose**: Finds the already existing host workspace directory for a conversation without creating it. This supports read-only operations that must not provision new storage.

**Data flow**: It receives a conversation id. It resolves the configured workspace root and checks whether the conversation directory exists safely under it. If the directory is missing, it returns `None`; if it exists but violates containment rules, the containment helper raises an error.

**Call relations**: `existing` calls this before attaching to a non-off-cluster local sandbox on the read path. This preserves the rule that reads do not create workspaces.

*Call graph*: 2 external calls (configured_root, contained_dir).


##### `ConversationSandbox._stored`  (lines 450–452)

```
async def _stored(self, conversation_id: UUID) -> str | None
```

**Purpose**: Fetches only the saved sandbox handle for a conversation. It is a small convenience wrapper around the fuller binding lookup.

**Data flow**: It receives a conversation id, calls `_binding`, discards the sandbox size, and returns the stored handle or `None`.

**Call relations**: `existing`, `claim_terminal`, and `_claim` use this when they only need to know what handle the conversation row currently names.

*Call graph*: calls 1 internal fn (_binding); called by 3 (_claim, claim_terminal, existing).


##### `ConversationSandbox._binding`  (lines 454–475)

```
async def _binding(self, conversation_id: UUID) -> tuple[str | None, str]
```

**Purpose**: Reads the conversation’s current sandbox handle and the owning agent’s requested sandbox size from the database. This gives sandbox creation the information it needs before opening anything.

**Data flow**: It receives a conversation id. Inside a workspace-scoped database transaction, it joins the conversation row to its agent row, restricted to the current workspace. If no matching conversation exists, it raises an error. Otherwise it returns the stored handle and sandbox size.

**Call relations**: `open` calls this at the start of sandbox creation or resume. `_stored` also calls it when other paths only need the handle.

*Call graph*: called by 2 (_stored, open); 3 external calls (select, workspace_tx, ws_current).


##### `ConversationSandbox._claim`  (lines 477–498)

```
async def _claim(self, conversation_id: UUID, stored: str | None, handle: str) -> str
```

**Purpose**: Saves a sandbox handle to the conversation row only if the row still contains the value the caller previously saw. This is the race-control point that prevents two simultaneous opens from both becoming official.

**Data flow**: It receives a conversation id, the previously observed stored handle, and the new handle to save. It runs a conditional database update: if the old value still matches, it writes the new handle and returns it. If the update loses the race, it rereads the stored handle and returns the winner’s value. If the handle somehow disappeared, it raises an error.

**Call relations**: `open` uses `_claim` after creating or resuming a sandbox so all concurrent callers converge on one official handle. `claim_terminal` uses the same mechanism to bind a fresh conversation to a terminal safely.

*Call graph*: calls 1 internal fn (_stored); called by 2 (claim_terminal, open); 3 external calls (update, workspace_tx, ws_current).


### Sandbox carriers
Carrier implementations provide interchangeable local, terminal, Docker, and E2B backends for commands, files, networking, reuse, reconnect, and lifecycle control.

### `core/src/ufo/harness/sandbox/local.py`

`io_transport` · `sandbox operations during conversation setup, command execution, file access, and tool calls`

This file lets the system treat a local directory like a sandbox workspace. Tools can talk about paths such as `/workspace/file.txt`, but this carrier rewrites those paths to a real folder on the host before running commands. It also copies in a small `ufo` client program so file tools and in-sandbox helper commands can work even without a container.

The important tradeoff is safety. This is convenient, but it is not a real security boundary. A subprocess runs on the same machine as the server. The code protects file operations by checking that requested paths stay inside the allowed workspace or runtime folders, but the operating system is not confining the process the way a container would.

The file also builds a careful environment for each command. It does not pass through the server’s secret environment. Instead it creates a scratch home directory, a controlled `PATH`, proxy settings, fake model API keys, and certificate settings. That means outbound network calls still go through the sandbox proxy, where metering and key-swapping can happen.

Besides running commands, the carrier can seed and load “skills” under its local UFO home directory. Skills are checked with digests, like tamper-evident seals, so the system can tell whether the files match the manifest that describes them.

#### Function details

##### `_provision_scratch`  (lines 79–103)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates one temporary support area for the current server process. This area holds a fake home directory and, when available, a copied `ufo` client binary that sandbox commands can run.

**Data flow**: It takes no input. It makes a temporary directory with `home` and `bin` subfolders, tries to find the client binary, copies it into `bin`, makes it executable, and returns the scratch directory path. If the binary is missing, it logs a warning and still returns the scratch directory.

**Call relations**: This is used as the default factory for `LocalCarrier._scratch`, so it runs the first time a local carrier needs scratch space. Later methods use that scratch space for command homes, PATH setup, certificates, and local runtime files.

*Call graph*: 4 external calls (Path, mkdtemp, warn, client_binary).


##### `LocalCarrier.ufo_home`  (lines 111–113)

```
def ufo_home(self) -> Path
```

**Purpose**: Gives the local carrier’s private UFO home directory. This is where local runtime data such as skills are stored.

**Data flow**: It reads the carrier’s scratch directory and appends `home/.ufo` to it. The result is a path, and it does not create or change anything by itself.

**Call relations**: Other methods call this property when they need the local runtime root, especially for skills and per-conversation runtime folders.


##### `LocalCarrier.seed_system_skills`  (lines 115–143)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Installs the built-in system skills from a zip archive into the local carrier’s UFO home. It replaces old versions safely and records the new manifest.

**Data flow**: It receives zip bytes. It opens the archive, reads `manifest.json`, compares old and new skill names, removes affected top-level skill folders, writes each archived skill file under the skills directory, and finally writes `.system-manifest.json`. It raises errors if the manifest or names are malformed.

**Call relations**: This prepares the skill store before later skill loading. It relies on `_system_manifest` to read the previous manifest and uses containment helpers so archive paths cannot escape the skills directory.

*Call graph*: calls 1 internal fn (_system_manifest); 7 external calls (BytesIO, loads, Path, contained_file, contained_relative, contained_remove, ZipFile).


##### `LocalCarrier.load_skills`  (lines 145–155)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Loads the requested system and user skills and returns their installed root paths in the same shape as a command result. It wraps skill loading so callers get stdout, stderr, and an exit code.

**Data flow**: It receives a sandbox handle and a payload describing requested skills. It runs `_load_skills` in a background thread, because it touches the filesystem. On success it returns an `ExecResult` whose stdout is JSON containing skill roots; on validation or filesystem failure it returns exit code 1 and the error message in stderr.

**Call relations**: This is the async public entry for skill loading. It hands the real checking and installation work to `_load_skills`, then packages the answer for the wider sandbox interface.

*Call graph*: 3 external calls (__init__, to_thread, dumps).


##### `LocalCarrier._load_skills`  (lines 157–180)

```
def _load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Does the actual skill resolution work. It checks requested system skills against the installed manifest and installs user-provided skills after validating them.

**Data flow**: It receives a payload with `system` and `user` sections. It reads the system manifest, validates the payload shape, tries each requested system skill through `_load_system_skill`, installs each user skill through `_load_user_skill`, and returns a mapping from skill name to its local root directory.

**Call relations**: It is called by `LocalCarrier.load_skills`. It coordinates `_system_manifest`, `_load_system_skill`, and `_load_user_skill` so the public method can stay focused on async wrapping and result formatting.

*Call graph*: calls 3 internal fn (_load_system_skill, _load_user_skill, _system_manifest).


##### `LocalCarrier._load_system_skill`  (lines 182–201)

```
def _load_system_skill(self, root: Path, manifest_skills: Mapping[object, object], name: object, digest: object) -> tuple[str, str] | None
```

**Purpose**: Checks whether one requested built-in skill is present and exactly matches the expected digest. If it does, it reports where that skill lives.

**Data flow**: It receives the skills root, the manifest’s skill table, a skill name, and a digest. It validates the name and digest, confirms the manifest declares the same digest, reads the listed files, recomputes their digest, and returns the skill name plus path if everything matches. If the manifest or files do not match the requested digest, it returns `None`.

**Call relations**: It is called by `_load_skills` for each requested system skill. It uses `_read_skill_files` to gather bytes and `_skill_digest` to verify that the files have not changed.

*Call graph*: calls 2 internal fn (_read_skill_files, _skill_digest); called by 1 (_load_skills); 1 external calls (contained_relative).


##### `LocalCarrier._load_user_skill`  (lines 203–236)

```
def _load_user_skill(self, root: Path, system_names: tuple[object, ...], name: object, encoded: object) -> tuple[str, str]
```

**Purpose**: Validates and installs one user-provided skill. It makes sure the skill has a safe name, does not overlap a system skill, and matches its declared digest.

**Data flow**: It receives the skills root, the system skill names, a user skill name, and encoded file data. It checks types and path safety, decodes each base64 file, computes the digest, compares it with the supplied digest, removes any old copy of that user skill, writes the new files, and returns the skill name plus installed path.

**Call relations**: It is called by `_load_skills` for every user skill in the payload. It delegates name checking to `_validate_user_skill_name`, digest creation to `_skill_digest`, and final writing to `_install_user_skill`.

*Call graph*: calls 3 internal fn (_install_user_skill, _skill_digest, _validate_user_skill_name); called by 1 (_load_skills); 2 external calls (urlsafe_b64decode, contained_relative).


##### `LocalCarrier._system_manifest`  (lines 239–247)

```
def _system_manifest(root: Path) -> Mapping[str, object]
```

**Purpose**: Reads the stored manifest for installed system skills. If there is no manifest yet, it returns an empty skill list.

**Data flow**: It receives the skills root path. It opens `.system-manifest.json` inside that root using a contained file access check, parses it as JSON when present, verifies it is an object-like mapping, and returns it. If the file is absent, it returns `{"skills": {}}`.

**Call relations**: It is used by `seed_system_skills` to compare old and new system skills, and by `_load_skills` to know which system skills are available.

*Call graph*: called by 2 (_load_skills, seed_system_skills); 2 external calls (load, contained_file).


##### `LocalCarrier._read_skill_files`  (lines 250–257)

```
def _read_skill_files(root: Path, name: str, files: list[str]) -> list[tuple[str, bytes]]
```

**Purpose**: Reads the files that make up a system skill so their contents can be checked. This supports digest verification.

**Data flow**: It receives the skills root, a skill name, and a list of file paths from the manifest. For each path, it builds a contained path under `root/name`, opens the file safely, reads its bytes, and returns a list of `(relative path, bytes)` pairs.

**Call relations**: It is called by `_load_system_skill` after the manifest says a skill should exist. The returned contents are passed to `_skill_digest` to confirm the installed files match the expected digest.

*Call graph*: called by 1 (_load_system_skill); 2 external calls (contained_file, contained_relative).


##### `LocalCarrier._install_user_skill`  (lines 260–266)

```
def _install_user_skill(root: Path, name: str, files: list[tuple[str, bytes]]) -> None
```

**Purpose**: Writes a validated user skill into the local skills directory. It replaces any older copy first.

**Data flow**: It receives the skills root, a skill name, and already-decoded file contents. It removes the old skill directory or file under that name, then writes each new file safely beneath the skills root with normal readable file permissions.

**Call relations**: It is called by `_load_user_skill` only after the skill name, file paths, and digest have all been checked. It uses containment helpers so user-supplied paths cannot write outside the skills area.

*Call graph*: called by 1 (_load_user_skill); 3 external calls (contained_file, contained_relative, contained_remove).


##### `LocalCarrier._validate_user_skill_name`  (lines 269–272)

```
def _validate_user_skill_name(name: str, root: Path) -> None
```

**Purpose**: Checks that a user skill name is simple and safe. A user skill must be a single top-level name and cannot start with a dot.

**Data flow**: It receives a name and the skills root. It resolves the name as a contained path, looks at the relative path parts, and raises an error if the name would be nested, hidden, or otherwise invalid. It returns nothing on success.

**Call relations**: It is called early by `_load_user_skill`, before any user files are decoded or written. This prevents confusing or dangerous skill names from entering the install process.

*Call graph*: called by 1 (_load_user_skill); 2 external calls (Path, contained_relative).


##### `LocalCarrier._skill_digest`  (lines 275–280)

```
def _skill_digest(files: list[tuple[str, bytes]]) -> str
```

**Purpose**: Computes the digest used to prove a skill’s file list and contents match what was declared. A digest is a compact fingerprint of data.

**Data flow**: It receives a list of file paths and bytes. For each file, it hashes the path and the content, feeds both hashes into a combined SHA-256 hash, and returns a string like `sha256:<hex value>`.

**Call relations**: Both `_load_system_skill` and `_load_user_skill` use this to compare real files against expected digests. It is the common seal-checking step for all skills.

*Call graph*: called by 2 (_load_system_skill, _load_user_skill); 1 external calls (sha256).


##### `LocalCarrier.create`  (lines 282–317)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or opens the local workspace for a conversation and returns a sandbox handle that other operations can use. It also prepares network proxy settings for commands.

**Data flow**: It receives a `SandboxSpec` containing workspace path, conversation IDs, proxy information, run token, and environment additions. It creates the workspace directory and runtime directory, writes the proxy certificate into scratch space, builds proxy and certificate environment variables, and returns a `SandboxHandle` describing the local sandbox.

**Call relations**: This is the main setup method for a new local sandbox. It calls `_base_env` for the safe baseline command environment, then adds per-turn proxy settings and spec-provided environment values.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier._base_env`  (lines 319–342)

```
def _base_env(self) -> dict[str, str]
```

**Purpose**: Builds the safe default environment for every local command. It avoids leaking the server’s secrets and disables host Git credential helpers and prompts that could hang or expose credentials.

**Data flow**: It reads only a small allowlist of host environment variables, such as locale and temporary-directory settings. It then adds a scratch `HOME`, `UFO_HOME`, a controlled `PATH`, and Git settings that block system config, credential prompts, and inherited Git configuration. It returns the environment dictionary.

**Call relations**: Both `create` and `attach` call this before returning a sandbox handle. Commands later inherit this environment through `LocalCarrier.exec`.

*Call graph*: called by 2 (attach, create); 1 external calls (Path).


##### `LocalCarrier.attach`  (lines 344–359)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Returns a handle for an existing local workspace without creating it. This is useful when browsing or reading an older conversation that may or may not have a workspace.

**Data flow**: It receives a `SandboxSpec`. It checks whether the workspace host path is already a directory. If not, it returns `None`; if it exists, it returns a `SandboxHandle` with the workspace path, runtime path, IDs, and base environment.

**Call relations**: This is the read-only counterpart to `create`. It calls `_base_env` for command/file helper support but deliberately skips workspace creation.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 361–419)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs one command as a host subprocess inside the local workspace. It makes local execution look like sandbox execution by rewriting `/workspace` paths and passing the sandbox environment.

**Data flow**: It receives a sandbox handle, command arguments, a timeout, and an optional model command name. It finds the host workspace, rewrites logical sandbox paths to host paths, adjusts bash commands so the intended PATH is visible, starts the subprocess with the workspace as its current directory, waits for output, and returns stdout, stderr, and exit code. If the command times out or the task is cancelled, it kills the whole process group.

**Call relations**: This is the command-running heart of the local carrier. It uses `_root` to find the workspace, `host_argv` to rewrite paths, and `_kill_process_group` when a command must be stopped along with any child processes it started.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 5 external calls (__init__, create_subprocess_exec, wait_for, quote, host_argv).


##### `LocalCarrier.write`  (lines 421–444)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the workspace safely from async code. It is used for copy-in operations, such as delivering a file to the sandbox.

**Data flow**: It receives a sandbox handle, a logical path, and bytes. Because normal filesystem writes are blocking, it sends the actual work to a background thread. It returns nothing after the contained write completes.

**Call relations**: This is the async wrapper around `_write_contained`. Callers use it as part of the sandbox file interface while `_write_contained` performs the guarded disk write.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 446–449)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the actual safe file write under an allowed sandbox root. It refuses paths outside the workspace or runtime area.

**Data flow**: It receives a handle, path, and bytes. It maps the logical path to a contained relative name and root using `_contained_name`, opens the target through containment checks, and atomically replaces the file contents while preserving suitable permissions.

**Call relations**: It is called by `LocalCarrier.write` in a worker thread. It depends on `_contained_name` to decide which root the path belongs to before touching disk.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 451–462)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the local workspace in chunks. It is used for copy-out without loading the whole file into memory at once.

**Data flow**: It receives a sandbox handle and logical path. It opens a safely contained source file in a background thread, repeatedly reads fixed-size chunks in background threads, yields each chunk to the caller, and closes the file when done or interrupted.

**Call relations**: This is the async streaming wrapper around `_contained_source`. The containment check happens before streaming begins, and the open file descriptor keeps the read tied to the checked file.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 464–474)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Safely opens a file for reading from an allowed sandbox root. It turns containment path failures into ordinary file-not-found errors for callers.

**Data flow**: It receives a handle and path. It maps the path through `_contained_name`, opens the target through containment checks, verifies it exists, and returns a binary file reader. If a guarded path component is missing, it raises `FileNotFoundError`.

**Call relations**: It is called by `LocalCarrier.read` before chunked streaming starts. `_contained_name` chooses the workspace or runtime root, and containment helpers prevent symlinks or path tricks from escaping that root.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 476–481)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured file operation through the local `ufo fs` helper. This gives the local carrier the same file-tool behavior as other sandbox carriers.

**Data flow**: It receives a handle, an operation name, and operation parameters. It delegates to `ufo_fs_file_op`, which runs the helper command against the workspace and returns a dictionary result.

**Call relations**: This method plugs the local carrier into the shared sandbox file-operation interface. It relies on the scratch PATH prepared earlier so the `ufo` client can be found.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `LocalCarrier.dial`  (lines 483–489)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Builds the address used to reach a service started by a local sandbox command. Because local commands share the host network, a sandbox port is just the same port on localhost.

**Data flow**: It receives a handle and a port number. It returns a `DialTarget` pointing to `127.0.0.1:<port>` with TLS disabled. It does not inspect the workspace or start any network connection itself.

**Call relations**: Callers use this after a command starts a server and the outside system needs to connect to it. Unlike container carriers, this local version has no per-conversation port isolation.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 492–497)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Force-stops a command and any child processes in its process group. This prevents timed-out or cancelled commands from leaving runaway work behind.

**Data flow**: It receives an asyncio subprocess object. It sends `SIGKILL` to the process group whose id matches the process id, ignores the case where the group is already gone, and waits for the process to finish.

**Call relations**: It is called by `LocalCarrier.exec` when a subprocess times out or when execution is interrupted. This cleanup is important because shell commands can start child processes that would otherwise keep running.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 500–503)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the host directory that backs `/workspace` for a local sandbox handle. It fails clearly if the handle has no host workspace path.

**Data flow**: It receives a sandbox handle. If `workspace_host_path` is missing, it raises an error; otherwise it converts that path string into a `Path` and returns it.

**Call relations**: It is used by `LocalCarrier.exec` to choose the subprocess working directory, and by `_contained_name` when mapping logical workspace paths to host paths.

*Call graph*: called by 2 (exec, _contained_name); 1 external calls (Path).


##### `_contained_name`  (lines 506–512)

```
def _contained_name(handle: SandboxHandle, path: str) -> tuple[PurePosixPath, Path]
```

**Purpose**: Maps a logical sandbox path to a safe relative name plus the real root directory it belongs to. It only accepts paths inside `/workspace` or the handle’s runtime root.

**Data flow**: It receives a sandbox handle and a path string. It treats the path as a POSIX-style path, checks whether it is under the workspace directory, otherwise checks whether it is under the runtime root, and returns the relative path plus the matching host root. If neither check passes, it raises an error.

**Call relations**: It is called by `_write_contained` and `_contained_source` before any disk access. Those functions then combine its answer with lower-level containment helpers to keep reads and writes inside approved sandbox roots.

*Call graph*: calls 1 internal fn (_root); called by 2 (_contained_source, _write_contained); 2 external calls (Path, PurePosixPath).


### `core/src/ufo/harness/sandbox/terminal.py`

`io_transport` · `request handling and turn execution`

Most sandbox carriers run commands inside a container or remote machine the server can contact directly. This file solves the harder case where the “sandbox” is the user’s own terminal. The server cannot open a socket into that terminal on demand, so it uses a rendezvous: the terminal keeps reconnecting to ask, “is there work for me?”, the server gives it one operation, and the terminal later posts back the result.

The central idea is a per-conversation slot, like a numbered pickup window at a shop. A workflow can place one order there, and the connected terminal can pick it up and return the answer. Because a terminal connection may briefly disappear and reconnect between turns, the waiting operation is stored by conversation, not by the individual network connection. Because workflow code and web connection code run on different event loops, the file uses a lock and careful wakeups so one side can safely wake the other.

The file has two main parts. `Terminals` is the in-process rendezvous store: it records who is connected, queues one operation at a time, stores staged file bytes, and delivers replies. `TerminalCarrier` makes that rendezvous look like a normal sandbox: it can create or attach to a handle, run commands, read and write files, run file-browser operations, and load skills. It also rewrites logical `/workspace/...` paths to the real directory where the user launched UFO.

#### Function details

##### `TerminalTransport.connect`  (lines 250–256)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: This protocol method defines how a terminal announces that it is connected for a conversation. It records where the terminal is standing on disk, who owns it, and which runtime area it should use.

**Data flow**: Input is a conversation id, current working directory, optional member id, and optional runtime id. An implementation stores that as the active terminal binding for later work. Nothing is returned.

**Call relations**: This is part of the shared transport contract. The in-process implementation is `Terminals.connect`, while other backends can provide the same behavior across processes.


##### `TerminalTransport.disconnect`  (lines 258–258)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: This protocol method defines how a terminal reports that one of its connections has gone away. It lets the transport know when the terminal may no longer be reachable.

**Data flow**: Input is the conversation id. An implementation lowers or removes the connection record for that conversation. Nothing is returned.

**Call relations**: The surface side calls this when the held terminal connection closes. `Terminals.disconnect` is the local implementation of the contract.


##### `TerminalTransport.workspace`  (lines 260–260)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: This protocol method asks what workspace, if any, is currently bound to a conversation. It is a quick lookup of the terminal’s current directory and identity.

**Data flow**: Input is a conversation id. The implementation reads stored terminal state and returns a `TerminalWorkspace`, or returns nothing if no terminal is known.

**Call relations**: It gives callers a non-waiting way to inspect the binding. `Terminals.workspace` supplies the in-memory version.


##### `TerminalTransport.arrived`  (lines 262–262)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: This protocol method waits for a terminal to show up, for up to a given grace period. It exists because the client’s connection can naturally drop and reconnect between holds.

**Data flow**: Input is a conversation id and a wait time in seconds. The implementation either returns the terminal workspace once connected or returns nothing after the grace time expires.

**Call relations**: Sandbox setup uses this before treating the user’s terminal as available. `Terminals.send` also calls it before trying to send work.


##### `TerminalTransport.send`  (lines 264–273)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: This protocol method sends one operation to the terminal and waits for its answer. It is the main request-and-reply path for command execution, file reads, file writes, and file operations.

**Data flow**: Input describes the operation: conversation id, kind, timeout, optional name, argument, JSON parameters, and optional body bytes. The implementation delivers that request to the terminal, waits for a reply, and returns reply bytes or raises an error if the terminal fails or disappears.

**Call relations**: `TerminalCarrier` relies on this method for almost everything it does. `Terminals.send` is the local implementation that pairs the outgoing operation with a later `resolve` call.


##### `TerminalTransport.next_op`  (lines 275–277)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: This protocol method lets the connected terminal ask for the next operation it should perform. It is the terminal side of the rendezvous.

**Data flow**: Input is the conversation id and, optionally, an operation id that should not be repeated. The implementation returns the next `TerminalOp` when one is available.

**Call relations**: The surface route serving the terminal waits here. It receives operations that were placed by `send`.


##### `TerminalTransport.staged`  (lines 279–281)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: This protocol method lets the terminal fetch large bytes attached to a pending operation, such as file content being written. The bytes do not travel in the small directive message.

**Data flow**: Input is a conversation id, operation id, and optional member id for access checking. The implementation returns the staged bytes for that exact operation, or nothing if they are missing or not allowed.

**Call relations**: It supports write-style operations created by `send`. `Terminals.staged` provides the in-memory body lookup.


##### `TerminalTransport.resolve`  (lines 283–290)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: This protocol method lets the terminal answer an operation after it has run it. It reports success bytes or a failure message.

**Data flow**: Input is a conversation id, operation id, reply bytes, optional failure text, and optional member id. The implementation matches that answer to the waiting operation, wakes the sender, and returns whether the reply was accepted.

**Call relations**: This is the return path for work obtained through `next_op`. `Terminals.resolve` wakes the workflow that is blocked inside `send`.


##### `TerminalTransport.in_flight`  (lines 292–292)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: This protocol method reports the current operation waiting for a reply, if there is one. It is mainly useful for tests or operator inspection.

**Data flow**: Input is a conversation id. The implementation reads the conversation slot and returns the pending operation or nothing.

**Call relations**: `Terminals.in_flight` implements this for the local store.


##### `_wake`  (lines 295–304)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: This helper safely wakes an asynchronous waiter that may live on a different event loop thread. It prevents cross-thread future updates, which are unsafe in Python’s async system.

**Data flow**: Input is a stored waiter, which contains a future and the event loop that owns it, plus the answer to deliver. The helper schedules a tiny setter on the correct loop. It returns nothing but causes the waiter to receive the answer.

**Call relations**: `Terminals.connect` uses it to wake code waiting for a terminal to arrive. `Terminals.send` uses it to hand an operation to a watching terminal or wake queued senders. `Terminals.resolve` uses it to deliver the terminal’s reply back to the sender.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 300–302)

```
def _set() -> None
```

**Purpose**: This inner callback performs the actual future update on the future’s own event loop. It only sets the result if nobody has already completed the future.

**Data flow**: It reads the future and answer captured by `_wake`. If the future is still pending, it stores the answer in it; otherwise it leaves it alone. It returns nothing.

**Call relations**: `_wake` schedules this callback with the event loop’s thread-safe scheduling method. It is deliberately tiny so the cross-thread handoff is safe.


##### `Terminals.connect`  (lines 320–342)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: This records that a terminal connection is now present for a conversation. It also wakes anyone who was waiting for that terminal to reconnect.

**Data flow**: Input is the conversation id, terminal directory, optional member id, and optional runtime id. The method creates or refreshes the conversation slot, increases the connection count, takes any arrival waiters, and wakes them after releasing the lock. Nothing is returned.

**Call relations**: The surface calls this when the terminal’s held stream arrives. It calls `_wake` so `arrived` or `send` can continue once a terminal is available.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 344–351)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: This records that a terminal connection has closed. If no operation is waiting and no connections remain, it removes the conversation slot.

**Data flow**: Input is the conversation id. The method finds the slot, decreases its connection count, and deletes idle state when the last connection is gone. It returns nothing.

**Call relations**: The surface calls this when the terminal connection ends. It cooperates with `send`, which keeps a slot alive while an operation is still awaiting a reply.


##### `Terminals.workspace`  (lines 353–360)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: This gives a snapshot of the currently bound terminal workspace for a conversation. It is a fast read that does not wait for reconnection.

**Data flow**: Input is the conversation id. The method reads the slot under the lock and returns a `TerminalWorkspace` with directory, member id, and runtime id, or returns nothing if no slot exists.

**Call relations**: It is the local implementation of the transport contract’s workspace lookup.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 362–386)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: This waits for a terminal to be connected, allowing for the normal short gap while the client reconnects. Without this grace, a turn could fail even though the user’s terminal is about to reconnect.

**Data flow**: Input is a conversation id and a grace time. The method checks for an existing slot; if none exists, it registers a future and waits until `connect` wakes it or time runs out. It returns a `TerminalWorkspace` or nothing.

**Call relations**: `Terminals.send` calls this before sending an operation. If the wait times out, it calls `_drop_arrival` to remove its abandoned waiter.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 388–393)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: This removes a waiter that gave up waiting for a terminal to arrive. It keeps the arrival-waiting list from accumulating dead entries.

**Data flow**: Input is the conversation id and the future that timed out. The method removes matching entries from the arrival list and deletes the list if it becomes empty. Nothing is returned.

**Call relations**: `Terminals.arrived` calls this when its grace period has expired or when the wait is otherwise no longer useful.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 395–470)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: This sends one operation to the connected terminal and waits for the terminal’s reply. It is careful to serialize operations so the terminal only has one active job at a time.

**Data flow**: Input is the operation description and optional body bytes. The method waits for a terminal, waits for its turn, stores a new `TerminalOp` in the slot, wakes any terminal watcher, then waits for a reply or timeout. It returns reply bytes, raises a terminal failure, or raises that the terminal is gone; in all cases it clears the slot and wakes queued senders.

**Call relations**: This is the heart of the rendezvous. `TerminalCarrier` methods use the transport’s `send` method to run commands and file operations. Inside the local implementation, it calls `arrived`, `_take_turn`, and `_wake`.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 6 external calls (__init__, __init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 472–503)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: This waits until a sender owns the single operation slot for a conversation. It prevents two callers from asking the same terminal to do two jobs at once.

**Data flow**: Input is the conversation id, the caller’s event loop, and the operation timeout. If the slot is free, it marks it busy and returns. If another operation is running, it queues a ticket and waits; if the wait takes too long, it removes its ticket and raises an error.

**Call relations**: `Terminals.send` calls this before publishing an operation. When a send finishes, it wakes queued tickets so they can compete for the next turn.

*Call graph*: called by 1 (send); 6 external calls (__init__, __init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 505–530)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: This lets the connected terminal wait for the next operation it should run. It also avoids redelivering the operation that a reconnecting request just answered.

**Data flow**: Input is the conversation id and an optional operation id to exclude. If an undelivered operation is already waiting, it returns it immediately. Otherwise it stores a watcher future and waits until a sender wakes it with a `TerminalOp`.

**Call relations**: The terminal-facing route calls this while holding the client connection open. `Terminals.send` wakes this watcher when it has an operation ready.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 532–544)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: This returns the body bytes attached to the current operation, such as file content for a write. It only serves bytes for the exact operation and, when requested, the exact member.

**Data flow**: Input is the conversation id, operation id, and optional member id. The method checks the slot, operation id, and member ownership, then returns the stored body bytes or nothing.

**Call relations**: A terminal that receives a write directive can fetch its staged content through this path. The bytes were placed in the slot by `Terminals.send`.


##### `Terminals.in_flight`  (lines 546–551)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: This reports the currently pending operation for a conversation. It is a simple inspection hook.

**Data flow**: Input is a conversation id. The method reads the slot and returns its current operation, or nothing if there is no slot or no operation.

**Call relations**: It implements the transport contract for tests and operator-style reads that need to see what the turn is waiting on.


##### `Terminals.resolve`  (lines 553–582)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: This accepts the terminal’s answer to an operation and wakes the workflow waiting in `send`. It ignores stale, duplicate, or unauthorized replies.

**Data flow**: Input is the conversation id, operation id, reply bytes, optional failure text, and optional member id. The method checks that this reply matches the active operation and member, marks it resolved, then wakes the stored reply waiter with either bytes or a `TerminalOpFailed` object. It returns true if accepted and false otherwise.

**Call relations**: The terminal-facing surface calls this after the client posts an operation result. It uses `_wake` to return control to `Terminals.send` on the sender’s own event loop.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 598–645)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This creates a sandbox handle for a conversation whose sandbox is the user’s own terminal. It verifies that the connected terminal is in the expected workspace directory.

**Data flow**: Input is a sandbox specification with conversation id, expected workspace path, proxy settings, token, and environment. The method waits for a terminal, compares its directory to the expected workspace, builds proxy environment variables, and returns a `SandboxHandle`. It raises an error if the terminal is absent or in the wrong directory.

**Call relations**: Sandbox orchestration calls this when opening a terminal-backed sandbox. The returned handle is later used by `exec`, `read`, `write`, `file_op`, and other carrier methods.

*Call graph*: 4 external calls (__init__, __init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 647–662)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reconnects to an already bound terminal outside the normal turn flow, such as for file browsing or off-turn writes. It only succeeds if the current terminal matches the requested resume path.

**Data flow**: Input is a sandbox specification. The method does a no-grace arrival check, compares the bound directory to the resume id, and returns a lightweight `SandboxHandle` if they match; otherwise it returns nothing.

**Call relations**: Off-turn features use this to reach the member’s terminal without creating a new sandbox. It relies on the transport’s arrival lookup.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 664–690)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: This runs a command on the member’s machine in the bound workspace. It translates sandbox-style paths into real host paths before sending the command to the terminal.

**Data flow**: Input is a sandbox handle, argument vector, timeout, and optional model-written command text for safety checking. The method finds the real workspace root, rewrites the arguments with `host_argv`, prepares optional safety arguments, and delegates to `_exec`. It returns an `ExecResult`.

**Call relations**: Higher-level sandbox users call this to execute commands. It calls `_root` and then hands the actual terminal operation to `TerminalCarrier._exec`.

*Call graph*: calls 2 internal fn (_exec, _root); 1 external calls (host_argv).


##### `TerminalCarrier.load_skills`  (lines 692–703)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: This asks the terminal client to load skill files into the user-side UFO home area. It packages the skill payload and returns the client’s text response as a successful execution result.

**Data flow**: Input is a sandbox handle and a payload mapping. The method serializes the payload to compact JSON, sends a skills operation through the terminal transport, decodes the reply as stdout, and returns an `ExecResult` with exit code zero. A terminal-reported failure becomes a runtime error.

**Call relations**: Skill-loading code uses this carrier method. It sends an `OP_SKILLS` request over the same rendezvous used for commands and files.

*Call graph*: 2 external calls (__init__, dumps).


##### `TerminalCarrier._exec`  (lines 705–745)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, safety_argv: tuple[str, ...] | None=None) -> ExecResult
```

**Purpose**: This is the lower-level command runner once paths have already been resolved for the user’s machine. It sends an exec request to the terminal client and converts the JSON reply into the project’s normal execution result shape.

**Data flow**: Input is a handle, real command arguments, timeout, and optional safety arguments. The method builds JSON parameters including environment variables, sends an exec operation, parses the JSON reply, decodes base64 stdout and stderr, and returns an `ExecResult` with timeout information when reported.

**Call relations**: `TerminalCarrier.exec` uses this after rewriting paths. `_enumerate` also uses it for internally generated shell commands that produce file listings.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (_enumerate, exec); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 747–760)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This writes bytes into a file in the terminal-backed workspace. The file content is staged separately so large bytes do not have to fit in the directive message.

**Data flow**: Input is a handle, logical path, and content bytes. The method maps the path to the real workspace path, sends a write operation with the content as staged body bytes, and returns nothing on success. A terminal-reported failure becomes an `OSError`.

**Call relations**: File-copy code calls this to place data on the member’s machine. It uses `_client_path` for path translation and the transport’s `send` method for delivery.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 762–778)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads a file from the terminal-backed workspace. It makes file-not-found look the same as other sandbox carriers.

**Data flow**: Input is a handle and logical path. The method maps the path to the real workspace path, sends a read operation, receives the full file bytes, and yields them in chunks. A missing file becomes `FileNotFoundError`; other terminal failures become `OSError`.

**Call relations**: File-copy and file-browser code consume this as an async stream. It uses `_client_path` before sending the operation.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 780–868)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs a higher-level `ufo fs` file operation where the files actually live: on the member’s machine. It rewrites workspace paths, prepares file tree listings when needed, and asks the terminal client to run its native file operation.

**Data flow**: Input is a handle, operation name, and parameter dictionary. The method maps selected path parameters under the real root, optionally reads and renders office-style documents, optionally enumerates tree or change data first, sends the file operation, parses the JSON reply, and returns the result dictionary or raises a clear error.

**Call relations**: Higher-level file tooling calls this for reads, greps, globs, changes scans, and similar operations. It calls `_root`, `_under_root`, `_enumerate`, and `_reply_object` as needed.

*Call graph*: calls 4 internal fn (_enumerate, _reply_object, _root, _under_root); 2 external calls (dumps, PurePosixPath).


##### `TerminalCarrier._enumerate`  (lines 870–891)

```
async def _enumerate(self, handle: SandboxHandle, op: str, walk_root: str, program: str, arguments: tuple[str, ...]=()) -> None
```

**Purpose**: This runs a small server-composed shell program on the terminal machine to produce a stable file listing for operations like grep, glob, or changes. The later file operation reads that listing instead of deciding its own walk.

**Data flow**: Input is a handle, operation name, walk root, shell program, and optional arguments. The method quotes the walk root, runs the shell program through `_exec`, and raises an error if the listing command fails with an unexpected exit code. It returns nothing on success.

**Call relations**: `TerminalCarrier.file_op` calls this before operations that need an enumeration file. It delegates command execution to `_exec`.

*Call graph*: calls 1 internal fn (_exec); called by 1 (file_op); 1 external calls (quote).


##### `TerminalCarrier.dial`  (lines 893–897)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This refuses attempts to expose a port from a terminal-backed sandbox. A member’s terminal is not a remote container with per-port networking that the server can dial.

**Data flow**: Input is a sandbox handle and port number. The method does not use them to build a connection; it raises `SandboxUnreachable` explaining that this carrier cannot provide that kind of network access.

**Call relations**: Code that expects all sandbox carriers to support dialing can call this and receive a clear failure. Remote carriers, not this terminal carrier, provide service access.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 900–908)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: This decodes one captured command stream, such as stdout or stderr, from the terminal client’s exec reply. It treats missing or malformed stream data as a real protocol error, not as empty output.

**Data flow**: Input is a parsed reply object and the stream name. The function looks for a base64 field like `stdout_b64`, decodes it into bytes, and returns those bytes. If the field is absent or invalid, it raises an error.

**Call relations**: `TerminalCarrier._exec` calls this for stdout and stderr after `_reply_object` has parsed the terminal’s JSON reply.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 911–918)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: This parses a terminal reply that should be a JSON object. It protects callers from accidentally treating non-JSON or wrongly shaped replies as success.

**Data flow**: Input is raw reply bytes and the operation name for error messages. The function decodes the bytes, parses JSON, verifies the result is a dictionary-like object, and returns it. Bad JSON or a non-object result raises a runtime error.

**Call relations**: `TerminalCarrier._exec` uses it for exec replies. `TerminalCarrier.file_op` uses it for file operation replies.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 921–924)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: This returns the real host directory that backs logical `/workspace` for a terminal sandbox. It fails loudly if the handle does not contain such a directory.

**Data flow**: Input is a sandbox handle. The function reads `workspace_host_path` and returns it, or raises an error if it is missing.

**Call relations**: `TerminalCarrier.exec` and `TerminalCarrier.file_op` use this before rewriting paths. `_client_path` also calls it as the first step in mapping a logical path.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 927–933)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: This maps a logical sandbox path like `/workspace/file.txt` to the member’s real workspace directory. It only strips the leading workspace prefix, avoiding accidental replacements elsewhere in the path.

**Data flow**: Input is a handle and a path string. The function gets the real root with `_root`, passes root and path to `_under_root`, and returns the translated path.

**Call relations**: `TerminalCarrier.read` and `TerminalCarrier.write` call this before sending paths to the terminal client.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 936–941)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: This performs the actual path rewrite from `/workspace/...` to the real terminal workspace root. Paths outside `/workspace` are left unchanged.

**Data flow**: Input is a real root path and a candidate path. The function checks whether the candidate is under the logical workspace directory; if so, it appends the relative part to the real root, otherwise it returns the original path.

**Call relations**: `_client_path` uses this for read and write paths. `TerminalCarrier.file_op` uses it directly for selected file-operation parameters.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox creation and command/file access during request handling`

A sandbox is the safe work area where an agent can run commands and touch files. This file provides the Docker version of that work area. Think of it like giving each conversation its own workshop in a shipping container: the workshop can be locked, reopened later, and connected only through a guarded doorway.

The main class, DockerCarrier, names containers predictably from the conversation ID, mounts the conversation workspace at /workspace, and runs a long-lived sleep process so the container stays available. Commands are then run with docker exec. Network access is not given directly. Instead, each command receives proxy environment variables for that turn, so outside requests go through an egress proxy that can meter and restrict them. Real model API keys are not placed in the container; sentinel values are swapped by the proxy when allowed.

The file also protects host resources. Docker networks and running containers consume memory and address space, so old idle conversations are stopped. They are not deleted, because the workspace should survive. A later command can revive the stopped container and continue. The code is careful around races: two requests for the same conversation may arrive together, Docker’s name conflict behavior is used as the tie-breaker, and per-conversation locks keep stopping and restarting from crossing wires.

#### Function details

##### `_docker`  (lines 79–93)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line tool asynchronously and returns its exit code, standard output, and standard error. It gives all other functions one consistent way to talk to Docker, including a clear result when Docker takes too long.

**Data flow**: It receives Docker arguments, optional input bytes, and a timeout. It starts the docker process, sends the input to it, waits for output, and returns the numeric result plus the two output streams. If the deadline passes, it kills the process and returns a special timeout code with a short error message.

**Call relations**: Almost every Docker operation in this file goes through this helper. Higher-level methods such as creating containers, inspecting state, installing certificates, preparing mounts, and stopping networks call it instead of starting subprocesses themselves.

*Call graph*: called by 13 (_death_report, _ensure_network, _exec_with, _held_id, _install_ca, _prepare_mounts, _reclaim_idle, _release, _revive, _running_id (+3 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 106–219)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates or reconnects to the Docker container for one conversation. It is the main entry point used when a sandbox needs to exist for a turn.

**Data flow**: It receives a sandbox specification with the conversation ID, image, workspace path, proxy settings, run token, and environment. It builds per-command proxy environment variables, looks for an already running or stopped container, revives or creates one as needed, installs the current proxy certificate, prepares filesystem locations, and returns a SandboxHandle that later operations use.

**Call relations**: This method coordinates many smaller helpers. It first asks _reclaim_idle to free old resources, then uses _running_id, _stopped_id, _revive, _ensure_network, _install_ca, and _prepare_mounts to produce a ready sandbox. If Docker reports that another concurrent create already won the container name, it attaches to that winner instead of making a second container.

*Call graph*: calls 9 internal fn (_ensure_network, _install_ca, _network_name, _prepare_mounts, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier.attach`  (lines 221–250)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Finds an existing conversation container without creating a new one. It is used when the caller wants to resume or inspect a sandbox only if it already exists.

**Data flow**: It receives a sandbox specification, checks for a running container, and if necessary looks for a stopped one to revive. If a usable container is found, it prepares the mount points and returns a SandboxHandle. If the container is absent or cannot be revived cleanly, it returns None.

**Call relations**: Unlike create, this method does not run idle reclamation or create a fresh container. It relies on _running_id, _stopped_id, _revive, and _prepare_mounts, and it deliberately turns some lifecycle failures into absence because attaching is a read-style operation.

*Call graph*: calls 4 internal fn (_prepare_mounts, _revive, _running_id, _stopped_id); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier._reclaim_idle`  (lines 252–320)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops containers and removes per-conversation Docker networks that have been idle too long. This prevents old conversations from tying up memory and scarce Docker bridge network space.

**Data flow**: It receives the conversation currently being opened, marks it as recently touched, asks Docker which UFO containers and networks exist, records unknown ones, then finds conversations that have no active operation and have passed the idle limit. For each stale conversation, it reserves that conversation, stops its container, removes its network, and either forgets it or puts it back for retry if release failed.

**Call relations**: create calls this before opening a sandbox. It uses _held_id to identify the container that owns a name and _release to actually free resources. Its locks and in-flight counters cooperate with _revive and command execution so an active or newly revived sandbox is not stopped mid-use.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 322–336)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Runs a normal command inside the sandbox container with that turn’s proxy and environment settings. This is how agent tool commands execute in the Docker sandbox.

**Data flow**: It receives a sandbox handle, a command argument tuple, a timeout, and optionally a model command label. It converts the handle’s environment into Docker --env options and passes everything to _exec_with. The result is an ExecResult containing text output, error text, exit code, and timeout information.

**Call relations**: This is the public command-running path for regular sandbox work. It delegates the actual Docker execution, retry after stopped-container errors, and in-flight bookkeeping to _exec_with.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier.exec_skill`  (lines 338–342)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command inside the container as root for server-controlled skill loading or synchronization. It is separate from normal execution because these setup actions need higher permissions.

**Data flow**: It receives the sandbox handle, command arguments, and a timeout. It adds the Docker option to run as root, then passes the request to _exec_with. The output is the same ExecResult shape used for normal commands.

**Call relations**: This is a specialized wrapper around _exec_with. It shares the same restart and accounting behavior as exec, but changes the user identity inside the container.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier._exec_with`  (lines 344–383)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, options: tuple[str, ...]) -> ExecResult
```

**Purpose**: Does the actual docker exec call for command execution. It also protects the container from idle cleanup while the command is running and retries once if the container had been stopped.

**Data flow**: It receives a handle, command arguments, timeout, and extra Docker options. It increments the in-flight count, records a touch time, runs docker exec in /workspace, and if Docker says the container is not running it tries _revive and repeats the command. It returns an ExecResult, translating this file’s internal timeout marker into the standard shell timeout code.

**Call relations**: exec and exec_skill both funnel into this helper. It calls _docker for the Docker command and _revive when reclaim or outside action stopped the container before execution.

*Call graph*: calls 2 internal fn (_revive, _docker); called by 2 (exec, exec_skill); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 385–403)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file as seen from inside the sandbox. It streams the content through standard input rather than placing file data on the command line.

**Data flow**: It receives a handle, target path, and bytes to write. It marks the conversation active, calls _write_started to perform the guarded copy, and if the container is stopped it revives and retries. On success it returns nothing; on failure it raises an OSError with Docker’s error text.

**Call relations**: Callers use this for attachments or generated files that must enter the sandbox. It depends on _write_started for the safe copy operation and _revive for recovery when the container was reclaimed.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 405–423)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Performs one attempt to copy file content into the container using the sandbox’s own safe copy program. This avoids unsafe shell tricks around paths and symbolic links.

**Data flow**: It receives a handle, path, and content bytes. It chooses the allowed root for the path, starts python inside the container with the copy-in program, feeds the bytes through standard input, and returns Docker’s exit code and error output.

**Call relations**: write calls this helper for the first attempt and, after a revive, for the retry. It uses _docker to run the container-side Python copy program.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `DockerCarrier.read`  (lines 425–459)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. It reads through the container so the caller sees the same filesystem view the sandbox sees.

**Data flow**: It receives a handle and path, marks the conversation active, starts a cat process in the container, and yields bytes as they arrive. After the stream ends, it checks for failure details, retries from the beginning if the container had stopped before producing a usable read, converts familiar filesystem errors into OSError, or raises a RuntimeError with extra container state.

**Call relations**: This is the public read path. It uses _read_started to create each streaming attempt, _revive if a stopped container caused the failure, and _death_report when Docker gives too little detail about why the read died.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 461–503)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Sets up one file-read attempt and returns both the byte stream and a place where failure details will be recorded after the stream finishes. This split lets read retry cleanly without reusing an already-running async generator.

**Data flow**: It receives a handle and path. It creates an empty failure list and returns an async generator plus that list. While the generator runs, it fills the list only if the container-side cat command exits with an error.

**Call relations**: read calls this helper for the first attempt and possibly again after a revive. The inner stream function does the subprocess work, while read interprets the collected failure details.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 476–501)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs docker exec cat for one file and yields the file’s bytes piece by piece. It also makes sure an abandoned read does not leave a Docker exec process behind.

**Data flow**: It starts docker exec with cat and the requested path, then repeatedly reads up to a fixed chunk size from standard output and yields each chunk. When output ends, it reads standard error, waits for the process, and records any non-zero exit. If the caller stops reading early, it kills and reaps the process.

**Call relations**: This inner generator is produced by _read_started and consumed by read. It talks directly to asyncio.create_subprocess_exec because streaming needs finer control than the _docker helper, which waits for the whole command to finish.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 505–523)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Adds useful facts when a file read fails without an error message. It asks Docker what state the container is in so the error points investigators in the right direction.

**Data flow**: It receives a sandbox handle, runs docker inspect for the container status, exit code, and out-of-memory flag, and returns a sentence describing what Docker reported. If inspect itself fails, it returns that failure instead.

**Call relations**: read calls this only when cat dies with no standard error. It uses _docker to run docker inspect and turns the result into context for the RuntimeError raised by read.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 525–531)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured filesystem operation through the ufo fs tool inside the sandbox. This gives higher-level callers operations like listing or metadata checks without writing their own shell commands.

**Data flow**: It receives a handle, operation name, and parameter dictionary. It passes those to the shared ufo_fs_file_op helper, which executes the appropriate sandbox-side command and returns a dictionary result.

**Call relations**: This method connects the Docker carrier to the common sandbox filesystem API. The shared helper can call back into this carrier’s execution behavior, so file operations get the same container pinning and revive behavior as normal commands.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `DockerCarrier.dial`  (lines 533–540)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Reports that this Docker carrier cannot expose a service port running inside the sandbox to the outside world. It gives callers a clear error instead of pretending a route exists.

**Data flow**: It receives a sandbox handle and port number, but does not use them to open a connection. It raises SandboxUnreachable with an explanation that this carrier has no external per-port host.

**Call relations**: Any code that wants to connect to an in-sandbox browser, preview server, or similar service may call this. For Docker, the story ends here; deployments that need this behavior must use a different carrier that supports it.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 542–556)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation’s container and removes its Docker network. This is the concrete cleanup step used when idle reclaim frees host resources.

**Data flow**: It receives a conversation ID and maybe a container ID. If there is a container, it asks Docker to stop it. Then it removes the conversation’s network and returns true if cleanup succeeded or the network was already gone; otherwise it returns false.

**Call relations**: _reclaim_idle calls this while holding the conversation’s lifecycle lock. It uses _network_name to find the network and _docker to perform Docker stop and network removal.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 558–578)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Starts a previously stopped container again and reconnects its per-conversation Docker network first. This lets an idle-reclaimed sandbox come back when someone touches it later.

**Data flow**: It receives the conversation ID and container ID. Under the lifecycle lock, it refreshes the touch time, ensures the Docker network exists, connects the container to it, then starts the container. It returns true if the container is running again, false for recoverable refusal, and raises if Docker cannot create the network.

**Call relations**: create, attach, exec, write, and read all use this recovery path. It coordinates with _release through the same lifecycle lock so stop-and-remove and reconnect-and-start do not interleave.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (_exec_with, attach, create, read, write).


##### `DockerCarrier._held_id`  (lines 580–588)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a given container name in any state, including running, paused, or exited. Reclaim needs this because a non-running container can still own the name and be tied to resources.

**Data flow**: It receives a container name, asks Docker for any matching container ID, and returns the ID string or None if nothing matches. If Docker itself fails, it raises a RuntimeError.

**Call relations**: _reclaim_idle calls this before releasing a stale conversation. It uses _docker to query Docker’s container list.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 590–599)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a named container that is currently exited. This identifies a sandbox that was stopped for reclaim and can potentially be revived.

**Data flow**: It receives a container name, asks Docker for an exited matching container, and returns its ID or None. If the Docker query fails, it raises a RuntimeError.

**Call relations**: create and attach use this after they do not find a running container. If it returns an ID, those methods may call _revive instead of creating a fresh sandbox.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 601–612)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a named container that is currently running. It distinguishes a true no-match from a Docker failure.

**Data flow**: It receives a container name, asks Docker for a running matching container, and returns the ID or None. If Docker returns an error, it raises a RuntimeError rather than treating the container as absent.

**Call relations**: create and attach call this first when looking for a usable sandbox. Accurate failure reporting here prevents misleading follow-up errors such as trying to create a container whose name is already held.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 614–615)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Docker network name for one conversation. Each conversation gets its own predictable network name based on its ID.

**Data flow**: It receives a conversation UUID and combines the carrier’s network prefix with the UUID in compact hexadecimal form. It returns that string.

**Call relations**: create, _revive, and _release use this whenever they need to create, connect, or remove the conversation’s Docker network.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 617–628)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a Docker network exists, creating it if needed. It treats “already exists” as success so concurrent creators can safely race.

**Data flow**: It receives a network name, asks Docker whether that network already exists, and returns immediately if it does. Otherwise it runs docker network create and raises an error only if creation fails for a reason other than another process creating it first.

**Call relations**: create uses this before docker run, and _revive uses it before reconnecting a stopped container. It uses _docker for both Docker network queries and creation.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 630–643)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current egress proxy certificate into the container’s trusted certificate store. This lets HTTPS tools inside the sandbox trust the proxy that inspects and controls outbound traffic.

**Data flow**: It receives a container ID and certificate text. It runs a root shell command inside the container, writes the certificate through standard input, updates the system certificates, and raises if that command fails.

**Call relations**: create calls this both for fresh containers and reused containers. That matters because the proxy certificate can change when the server process restarts, while the Docker container may persist.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._prepare_mounts`  (lines 645–672)

```
async def _prepare_mounts(self, container_id: str, conversation_id: UUID) -> None
```

**Purpose**: Sets up ownership and runtime directories inside the container so the sandbox user can work safely. It also prepares a private session file used under the sandbox runtime area.

**Data flow**: It receives a container ID and conversation ID, computes the runtime paths, then runs a root shell script inside the container. The script fixes /workspace ownership when needed, creates runtime directories with specific permissions, and ensures the session file is a regular private file. It raises if Docker reports failure.

**Call relations**: create and attach call this before returning a SandboxHandle. It uses _docker to run the setup command and sandbox_runtime_root to calculate the expected per-conversation paths.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `manifest`  (lines 675–680)

```
def manifest() -> Manifest
```

**Purpose**: Declares this file as the Docker carrier extension so the larger system can discover and load it. It names the carrier and points to DockerCarrier as the factory.

**Data flow**: It takes no input. It constructs and returns a Manifest containing the extension name, version, and carrier specification.

**Call relations**: The extension loading system calls this when discovering available sandbox backends. The returned manifest tells core code that the carrier named docker can be created with DockerCarrier.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox startup, request handling, command execution, file transfer, port dialing`

A UFO conversation needs a safe workspace where tools can run, files can live, and network traffic can be controlled. This file is the adapter between UFO’s generic sandbox interface and E2B’s cloud sandbox service. Without it, a deployment that selects the `e2b` sandbox backend could not start or reuse remote sandboxes, run commands in them, read and write files, or expose ports back to the rest of the system.

The main piece is `E2BCarrier`. Think of it like a travel agent for work sent to E2B: it books a sandbox, makes sure it has the right tools installed, renews the booking before it expires, and gives commands directions for where to run. It also keeps track of which sandbox belongs to which conversation, because `/workspace` inside that sandbox is the only copy of that conversation’s files.

A key concern here is E2B’s pause-and-resume behavior. Sandboxes pause after a timeout, but their disk and processes survive. The code uses `connect` not just to reconnect, but also to renew the lease. It avoids deleting sandboxes because that would delete the workspace. It also prepares each sandbox by installing the UFO client, trusting the proxy certificate, creating `/workspace`, and limiting workload memory and process counts so user work cannot starve the sandbox service itself.

#### Function details

##### `E2BCommandHandle.wait`  (lines 180–180)

```
async def wait(self) -> E2BCommandResult
```

**Purpose**: This is the promised shape of an E2B background command handle. Calling it waits until a command that was already started finishes, then returns its output and exit code.

**Data flow**: It starts with a running command object that has a process id. The method waits for that remote process to end, then produces a command result containing standard output, standard error, and an exit code.

**Call relations**: This protocol is not implemented in this file; it describes what the E2B SDK provides. `E2BCarrier._exec_with` starts a command in the background so it can learn the process id, then calls `wait` to collect the final result.


##### `E2BCommands.run`  (lines 201–210)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None, background: Literal[False]=False) -> E2BCommandResult
```

**Purpose**: This describes how this file expects to ask E2B to run a shell command inside a sandbox. It can either wait for the command result or start it in the background and return a handle.

**Data flow**: The caller gives a command string plus optional working directory, environment variables, user name, timeout, and background flag. E2B runs that command in the remote sandbox and returns either the finished result or a handle for a still-running command.

**Call relations**: This is a protocol for the E2B SDK object used throughout the carrier. Preparation helpers, command execution, stop signals, and health probes all rely on this shape.


##### `E2BFileStream.__aiter__`  (lines 217–217)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: This describes a streamed file reader that can be used in an asynchronous loop. It lets large files be read piece by piece instead of all at once.

**Data flow**: It starts from an open file stream from E2B. Iterating over it yields byte chunks until the file has been fully read.

**Call relations**: The E2B SDK supplies this object. `E2BCarrier.read` uses it to pass file chunks back to callers while keeping memory use bounded.


##### `E2BFileStream.aclose`  (lines 219–219)

```
async def aclose(self) -> None
```

**Purpose**: This describes how to close an open E2B file stream. It is important because the stream holds a live network connection.

**Data flow**: It starts with an open stream. Calling it releases the underlying connection and returns no data.

**Call relations**: The E2B SDK supplies this method. `E2BCarrier.read` calls it in a cleanup block so the stream is closed even if the caller stops reading early.


##### `E2BFiles.write`  (lines 223–223)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: This describes the E2B file upload operation used by the carrier. It writes text or bytes into a path inside the remote sandbox.

**Data flow**: The caller provides a sandbox path, file contents, and optionally a user. E2B stores those contents in the sandbox filesystem and returns only an acknowledgement-like result.

**Call relations**: This protocol method is used by `E2BCarrier.write`, `_install_ca`, and `_ensure_client` to place data into the sandbox without squeezing bytes through a shell command.


##### `E2BFiles.read`  (lines 225–225)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: This describes the E2B file download operation used by the carrier. It opens a file in the sandbox and returns a stream of bytes.

**Data flow**: The caller gives a path and asks for a format such as a stream. E2B opens the remote file and returns an async byte stream for the caller to consume.

**Call relations**: The E2B SDK supplies this method. `E2BCarrier.read` calls it and then forwards each chunk to UFO’s caller.


##### `E2BSandbox.get_host`  (lines 234–234)

```
def get_host(self, port: int) -> str
```

**Purpose**: This describes how to turn an in-sandbox port into an externally reachable host name. It is used when a service running inside the sandbox needs to be contacted from outside.

**Data flow**: The caller gives a port number. The sandbox object formats and returns the public host name for that port.

**Call relations**: The E2B SDK supplies this method. `E2BCarrier.dial` calls it after renewing the sandbox lease, then wraps the host in a UFO `DialTarget`.


##### `E2BSdk.create`  (lines 238–247)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: This describes the E2B SDK call for creating a brand-new sandbox from a template. The carrier uses it when there is no usable existing sandbox for a conversation.

**Data flow**: The caller supplies a template, lease timeout, metadata, lifecycle rules, network rules, and API key. E2B starts a remote sandbox and returns an object that can run commands, access files, and expose ports.

**Call relations**: This protocol method is called from `E2BCarrier._resume_or_open` after resume is impossible or no resume id exists.


##### `E2BSdk.connect`  (lines 249–255)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: This describes the E2B SDK call for reconnecting to an existing sandbox. In this file it is also the safe way to wake a paused sandbox and renew its lease.

**Data flow**: The caller provides a sandbox id, a desired lease span, and the API key. E2B returns the live sandbox object or reports that the sandbox no longer exists.

**Call relations**: All reconnects go through `E2BCarrier._connected`, which wraps this method with retries and a total timeout.


##### `E2BCarrier.create`  (lines 307–391)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This opens the sandbox for a conversation. It resumes the durable sandbox if one is named, reuses this process’s live one if safe, or creates a fresh E2B sandbox and prepares it for UFO work.

**Data flow**: It receives a `SandboxSpec` containing the conversation id, optional resume id, size, proxy settings, run token, environment, and turn id. It chooses or opens an E2B sandbox, installs or verifies the UFO client and runtime setup, records a lease, and returns a `SandboxHandle` that the rest of UFO can use.

**Call relations**: This is the main setup path for the carrier. It consults `_leased`, opens through `_resume_or_open`, prepares through `_ensure_client`, `_prepare_runtime`, or `_prepare_strictly`, drops bad leases with `_drop`, and hands the finished `SandboxHandle` back to core sandbox orchestration.

*Call graph*: calls 6 internal fn (_drop, _ensure_client, _leased, _prepare_runtime, _prepare_strictly, _resume_or_open); 8 external calls (__init__, __init__, __init__, timeout, emit_metric, log, egress_proxy_env, sandbox_runtime_root).


##### `E2BCarrier.attach`  (lines 393–418)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reconnects to an already-known sandbox without creating a replacement. It is used when UFO wants to read from an existing conversation sandbox and should return “not found” rather than silently create an empty new workspace.

**Data flow**: It receives a `SandboxSpec` with a possible resume id. If no id exists, it returns `None`; if E2B can connect, it records a fresh lease and returns a `SandboxHandle`; if E2B says the sandbox is gone, it clears the local cache and returns `None`.

**Call relations**: This path calls `_connected` directly because it must ask the provider, not trust local memory. It returns a handle for read-style operations but does not build egress proxy environment because it is not meant to run outbound workload traffic.

*Call graph*: calls 1 internal fn (_connected); 3 external calls (__init__, __init__, sandbox_runtime_root).


##### `E2BCarrier._resume_or_open`  (lines 420–460)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: This decides whether to reconnect to an existing sandbox or create a new one. It keeps conversations moving if a stored sandbox id is gone by opening a replacement instead of failing forever.

**Data flow**: It receives the sandbox request and an optional sandbox id. If an id exists, it tries `_connected`; if E2B says not found, it logs that miss. Then it looks up the right template for the requested size, creates a sandbox through the SDK, checks that E2B returned a traffic token, and returns the sandbox object.

**Call relations**: `E2BCarrier.create` calls this during sandbox setup. It hands reconnect work to `_connected` and otherwise calls the SDK’s create operation.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 462–478)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: This prepares a sandbox and treats failure as fatal. It is used for fresh or not-yet-proven sandboxes, where UFO cannot safely assume the client, certificate, workspace, or resource limits are already in place.

**Data flow**: It receives a sandbox and its spec. It tries preparation several times, but only retries network transport failures; deterministic command failures are raised immediately. If all attempts fail, it drops the cached lease and raises the error.

**Call relations**: `E2BCarrier.create` uses this when the sandbox is not a known prepared resume target. It calls `_prepare`, records retry logs and metrics, sleeps between retry attempts, and calls `_drop` when the sandbox should no longer be trusted.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 480–542)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: This is the safe reconnect-and-renew wrapper around E2B’s `connect` call. It retries only when the provider’s network call was unanswered, and it bounds the total time the caller can wait.

**Data flow**: It receives a conversation id, sandbox id, and lease span. It calls the SDK’s connect method; on temporary transport failures it logs, waits, and retries with backoff; on a total timeout it raises the last transport error or creates a read-timeout error. On success it returns the sandbox.

**Call relations**: `_resume_or_open`, `_sandbox`, and `attach` all use this instead of calling the SDK directly. That gives turn setup, lease renewal, and read attachment the same retry and timeout behavior.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 4 external calls (sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 544–546)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This performs the full preparation sequence for a sandbox. It makes sure the UFO client is present first, then sets up the runtime environment.

**Data flow**: It receives a sandbox and proxy certificate text. It calls `_ensure_client`, then `_prepare_runtime`; if both succeed, the sandbox is ready for UFO commands.

**Call relations**: `_prepare_strictly` calls this during strict setup. It is a small bridge that groups client installation with runtime preparation.

*Call graph*: calls 2 internal fn (_ensure_client, _prepare_runtime); called by 1 (_prepare_strictly).


##### `E2BCarrier._prepare_runtime`  (lines 548–553)

```
async def _prepare_runtime(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This puts the sandbox operating environment into the shape UFO expects. It installs the proxy certificate, creates the workspace, and sets resource ceilings for user workloads.

**Data flow**: It receives a sandbox and certificate text. It writes trust material, ensures `/workspace` exists and belongs to the sandbox user, and applies memory and process limits to workload control groups. It returns nothing if all steps succeed.

**Call relations**: `_prepare` uses this during full preparation, and `create` may call it directly for resumed sandboxes after checking the client. It delegates the concrete steps to `_install_ca`, `_ensure_workspace`, and `_cap_workload`.

*Call graph*: calls 3 internal fn (_cap_workload, _ensure_workspace, _install_ca); called by 2 (_prepare, create).


##### `E2BCarrier._ensure_client`  (lines 555–580)

```
async def _ensure_client(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure the correct UFO command-line client is installed inside the sandbox. That matters because all remote workload entry goes through the baked `ufo run` behavior.

**Data flow**: It receives a sandbox, hashes the local client binary, and checks whether `/usr/local/bin/ufo` in the sandbox has the same hash. If not, it uploads the binary to a temporary staging path, verifies it, installs it atomically, and remembers that this sandbox is ready.

**Call relations**: `create` and `_prepare` call this before running workload-dependent setup. It uses E2B command and file APIs directly, and its ready cache avoids repeating the check for the same sandbox in this process.

*Call graph*: called by 2 (_prepare, create); 2 external calls (sha256, uuid4).


##### `E2BCarrier._leased`  (lines 582–597)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: This looks up the locally cached lease for a conversation and cleans out expired cached leases. It keeps the process from holding references to every sandbox it has ever touched.

**Data flow**: It receives a conversation id. It reads the current local lease, removes all entries whose believed expiry time has passed, and returns the original lease for the requested conversation if one was present.

**Call relations**: `create` uses it when deciding whether this process already has a sandbox to reuse, and `_sandbox` uses it before deciding whether to reconnect. It does not contact E2B; it only maintains the local memory cache.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 599–607)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This installs the proxy certificate authority into the sandbox’s system trust store. That lets HTTPS traffic inside the sandbox trust UFO’s forwarding proxy.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path as root, runs the install/update command, and raises a clear runtime error if the command reports failure.

**Call relations**: `_prepare_runtime` calls this as the first runtime setup step. It uses the sandbox file API to place the certificate and the command API to update system trust.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._ensure_workspace`  (lines 609–618)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure `/workspace` exists and is owned by the normal sandbox user. That directory is the durable working area for the conversation.

**Data flow**: It receives a sandbox. It runs a root command to create the directory if needed and change its owner. If the command fails, it raises a runtime error with the command’s output.

**Call relations**: `_prepare_runtime` calls this after certificate setup. Later command execution uses `/workspace` as the working directory.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._cap_workload`  (lines 620–630)

```
async def _cap_workload(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This limits how much memory and how many processes user workloads can consume inside the sandbox. The goal is to stop user commands from starving E2B’s own sandbox service process.

**Data flow**: It receives a sandbox. It runs a root command that calculates a safe memory ceiling, writes memory and process limits into workload control groups, and raises a clear error if the setup fails.

**Call relations**: `_prepare_runtime` calls this after workspace setup. The limits persist across pause and resume because they are kernel state inside the sandbox snapshot.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier.exec`  (lines 632–684)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: This runs a normal user command inside the sandbox. It routes network access through UFO’s proxy environment and maps E2B command outcomes into UFO’s standard `ExecResult`.

**Data flow**: It receives a sandbox handle, argument tuple, timeout, and optional model command value. It passes the work to `_exec_with` with no forced user, then returns the resulting stdout, stderr, exit code, and timeout information.

**Call relations**: This is the public command execution method used by the sandbox interface. It delegates the detailed launch, timeout, stop, and lease logic to `_exec_with`.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier.exec_skill`  (lines 686–690)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a skill-related command as root inside the sandbox. It is for server-carried setup or sync work that needs elevated permissions.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout. It calls `_exec_with` with the user set to root and returns the resulting `ExecResult`.

**Call relations**: This is a specialized public execution path. Like `exec`, it relies on `_exec_with` for all command lifecycle and cleanup behavior.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier._exec_with`  (lines 692–739)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, user: str | None) -> ExecResult
```

**Purpose**: This is the core command runner. It renews the sandbox lease, starts the command in its own process group, waits for the result, converts errors into UFO results, and tries to stop timed-out work.

**Data flow**: It receives a handle, command arguments, timeout, and optional user. It gets a leased sandbox through `_sandbox`, probes silent sandboxes through `_still_there`, quotes the arguments into a shell command, starts it in the background, records its process group, waits for completion, and returns an `ExecResult`. On timeout it stops the group if possible; on cancellation or unknown failure it drops the lease so the next call reconnects.

**Call relations**: `exec` and `exec_skill` both call this. It coordinates `_sandbox`, `_still_there`, `_stop_group`, `_mark_silent`, `_forget_group`, and `_drop` so command execution, cleanup, and lease safety stay consistent.

*Call graph*: calls 6 internal fn (_drop, _forget_group, _mark_silent, _sandbox, _still_there, _stop_group); called by 2 (exec, exec_skill); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.stop_commands`  (lines 741–763)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: This stops all command groups still known to be running for one turn. It is used when a member is truly stopped, not for every internal cancellation that might later be replayed.

**Data flow**: It receives a sandbox handle. It removes the recorded process groups for that container and turn; if none exist, it returns without contacting E2B. If groups exist, it leases the sandbox and sends a kill signal to each group.

**Call relations**: This complements `_exec_with`, which deliberately leaves commands running when its task is cancelled because it cannot tell whether the cancellation is final. `stop_commands` is called from the higher-level place that knows the turn should really stop, and it uses `_sandbox` and `_stop_group`.

*Call graph*: calls 2 internal fn (_sandbox, _stop_group).


##### `E2BCarrier._forget_group`  (lines 765–775)

```
def _forget_group(self, handle: SandboxHandle, pid: int) -> None
```

**Purpose**: This removes a process group from the local “still running” record after it has ended or after the carrier has tried to stop it. It prevents later cleanup from signalling the same group again.

**Data flow**: It receives a handle and process id. It finds the matching container-and-turn entry, removes that pid, and deletes the whole entry if no groups remain.

**Call relations**: `_exec_with` calls this in its cleanup path for commands that did not get intentionally left running. It only updates local bookkeeping.

*Call graph*: called by 1 (_exec_with).


##### `E2BCarrier._stop_group`  (lines 777–803)

```
async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int, user: str | None) -> None
```

**Purpose**: This sends a forceful kill signal to an entire process group inside the sandbox. It targets the whole tree of child processes, not just the shell that launched them.

**Data flow**: It receives a sandbox, container id, process id, and optional user. It runs `kill -9` against the negative process id, which means the process group. If the sandbox does not answer, it marks the container as silent and records a metric; cleanup errors are swallowed so they do not hide the original timeout or stop reason.

**Call relations**: `_exec_with` uses this when a command times out, and `stop_commands` uses it when a turn is cancelled for real. It may call `_mark_silent` if the sandbox command channel appears wedged.

*Call graph*: calls 1 internal fn (_mark_silent); called by 2 (_exec_with, stop_commands); 1 external calls (emit_metric).


##### `E2BCarrier._mark_silent`  (lines 805–809)

```
def _mark_silent(self, container_id: str) -> None
```

**Purpose**: This remembers that a sandbox stopped answering commands for a limited time. The mark makes the next command test the sandbox quickly instead of spending its full timeout on a likely-dead channel.

**Data flow**: It receives a container id. It records an expiry time based on the current clock plus the configured silent-mark duration.

**Call relations**: `_exec_with` marks a sandbox silent if a launch times out before returning a pid, and `_stop_group` marks it if even the cleanup signal does not get an answer. `_still_there` later reads and clears or expires the mark.

*Call graph*: called by 2 (_exec_with, _stop_group).


##### `E2BCarrier._still_there`  (lines 811–841)

```
async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None
```

**Purpose**: This checks whether a previously silent sandbox is answering again before running a new command. It avoids making callers wait a long time on a container that is already known to be unhealthy.

**Data flow**: It receives a sandbox and container id. If there is no active silent mark, it returns. If the mark expired, it removes it and returns. Otherwise it runs a tiny `true` command with a short timeout; success clears the mark, while failure raises `SandboxUnreachable` and records a metric.

**Call relations**: `_exec_with` calls this just before launching a real command. It uses marks created by `_mark_silent` to decide when a cheap health probe is worth doing.

*Call graph*: called by 1 (_exec_with); 3 external calls (__init__, emit_metric, log).


##### `E2BCarrier.write`  (lines 843–854)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This uploads bytes into the sandbox filesystem. It is the safe channel for binary file content because E2B command execution only accepts shell strings, not standard input bytes.

**Data flow**: It receives a sandbox handle, path, and byte content. It gets a leased sandbox through `_sandbox`, writes the bytes through E2B’s file API, and returns nothing. If the write fails, it drops the local lease and re-raises the error.

**Call relations**: This is the public file-upload method for the carrier. It relies on `_sandbox` for lease safety and `_drop` when a provider call makes the cached lease untrustworthy.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 856–876)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This streams a file out of the sandbox in chunks. It lets UFO handle large files without loading the whole file into memory at once.

**Data flow**: It receives a sandbox handle and path. It leases the sandbox for a full autosuspend span, opens an E2B file stream, yields each byte chunk to the caller, translates E2B’s not-found error into Python’s `FileNotFoundError`, and always closes the stream afterward.

**Call relations**: This is the public file-download method for the carrier. It uses `_sandbox` to ensure the sandbox stays awake during the transfer and `_drop` if the provider call fails unexpectedly.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 878–883)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs higher-level file operations through the UFO client inside the sandbox. It reuses the same command execution path as other sandbox work.

**Data flow**: It receives a handle, operation name, and parameters. It passes them to `ufo_fs_file_op`, which runs the appropriate `ufo fs` command and returns a dictionary result.

**Call relations**: This public method is a thin adapter to shared sandbox helper logic. The helper calls back through the carrier’s command-running behavior rather than duplicating file-operation command details here.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `E2BCarrier.dial`  (lines 885–906)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This returns the outside address for a service running on a port inside the E2B sandbox. It is used for things like browser debugging ports or preview web servers.

**Data flow**: It receives a sandbox handle and port. It renews the sandbox lease long enough for an off-carrier connection, asks E2B for the public host name, attaches the traffic access token as a header if present, and returns a TLS-enabled `DialTarget`. If the sandbox is gone, it raises `SandboxUnreachable`.

**Call relations**: Callers use this when they need to connect to something the sandbox started. It calls `_sandbox` with a longer lease floor because the later network exchange does not itself make carrier calls that could renew the sandbox.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 908–950)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: This returns a sandbox object whose E2B lease lasts long enough for the work about to happen. It avoids reconnecting before every small operation while still preventing work from running past the provider’s timeout.

**Data flow**: It receives a handle, the number of seconds needed, and an optional minimum lease span. It checks the local lease; if it names the right sandbox and has enough time left, it returns it. Otherwise it removes the old cache entry, reconnects through `_connected`, records a new lease, logs the renewal, and returns the sandbox.

**Call relations**: `_exec_with`, `write`, `read`, `dial`, and `stop_commands` all call this before touching E2B. It uses `_leased` for cache cleanup and `_connected` for the actual provider reconnect.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 5 (_exec_with, dial, read, stop_commands, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 952–957)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: This forgets a cached lease after a provider call fails. A failed call means the local belief about the sandbox’s live state should no longer be trusted.

**Data flow**: It receives a conversation id and a label describing what was happening. It removes that conversation from the local lease map and logs the drop.

**Call relations**: `create`, `_prepare_strictly`, `_exec_with`, `write`, and `read` call this when preparation, command execution, or file access makes the current lease suspect. The sandbox itself is not deleted.

*Call graph*: called by 5 (_exec_with, _prepare_strictly, create, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 960–977)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: This parses the `E2B_TEMPLATES` environment variable into a map from sandbox size to E2B template reference. It also checks that every supported size is configured and no unexpected size appears.

**Data flow**: It receives a comma-separated string like `small=ref,medium=ref,large=ref`. It splits each entry, validates the `size=template` form, builds a dictionary, compares its keys with the expected sandbox sizes, and returns the dictionary or raises a runtime error.

**Call relations**: `build_e2b_carrier` uses this to configure the carrier, and `e2b_runtime_digest` uses it to compute a stable digest of the selected runtime templates.

*Call graph*: called by 2 (build_e2b_carrier, e2b_runtime_digest).


##### `e2b_runtime_digest`  (lines 980–988)

```
def e2b_runtime_digest() -> str
```

**Purpose**: This computes a stable fingerprint of the E2B template configuration selected by the process. The fingerprint lets UFO identify which sandbox runtime version this carrier is using.

**Data flow**: It reads `E2B_TEMPLATES` from the environment, parses it with `sandbox_templates`, serializes the map in a stable sorted JSON form, hashes it with SHA-256, and returns a `sha256:...` string.

**Call relations**: `manifest` exposes this function in the carrier spec so the broader system can ask for the runtime digest when describing or comparing sandbox backends.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (sha256, dumps).


##### `build_e2b_carrier`  (lines 991–1000)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: This constructs a ready-to-use `E2BCarrier` from environment configuration. It is the factory used when the deployment selects E2B as the sandbox backend.

**Data flow**: It reads the E2B API key and template map from environment variables, validates both are present, parses templates with `sandbox_templates`, loads the UFO client binary for the E2B target platform, and returns an `E2BCarrier` containing those values.

**Call relations**: `manifest` registers this as the carrier factory. When core asks for an E2B carrier, this function supplies the configured instance.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (__init__, client_binary).


##### `manifest`  (lines 1003–1016)

```
def manifest() -> Manifest
```

**Purpose**: This advertises the E2B carrier extension to UFO’s plugin system. It says the carrier is named `e2b`, runs off-cluster, supports the standard sandbox sizes, and can be built by `build_e2b_carrier`.

**Data flow**: It creates a `CarrierSpec` with the carrier name, factory, off-cluster flag, supported sizes, and runtime digest function. It wraps that spec in a `Manifest` and returns it.

**Call relations**: This is the registration point for the extension. The UFO manifest loader calls it so a deployment can choose `[sandbox] backend = "e2b"` without core code knowing E2B-specific details.

*Call graph*: 2 external calls (__init__, __init__).


### Sandbox setup utilities
Package markers, cache routing, client binary discovery, and execution-environment construction prepare sandboxes for safe and consistent work.

### `core/src/ufo/harness/sandbox/__init__.py`

`other` · `startup/import time`

This file is intentionally empty, but it still has a job. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as a package, meaning its contents can be imported using normal Python import paths. Here, it makes `core/src/ufo/harness/sandbox` available as the `ufo.harness.sandbox` package.

Think of it like putting a label on a drawer. The drawer may not contain instructions on the label itself, but the label lets the rest of the system find and refer to what is inside. Without this file, depending on the Python version and packaging setup, imports from this folder could fail or behave differently.

Because the file contains no code, it does not perform any setup, define any functions, or change program behavior directly. Its value is structural: it keeps the project’s package layout clear and importable.


### `core/src/ufo/harness/sandbox/cache.py`

`config` · `startup and sandbox setup`

A sandbox often needs to fetch source code or packages, such as GitHub repositories, Python packages, or Ubuntu packages. This file is the central place that says which cache host the sandbox should use and which outside hosts are allowed to be cached. Think of it like a posted list at a company mailroom: only packages from approved senders are accepted, and everyone uses the same mailroom address.

The cache host is fixed as `cache.ufo.internal`, an internal name recognized by the proxy. For Git, the file lists hosts that may be mirrored, currently `github.com`, and builds Git configuration that quietly redirects fetches through the cache while keeping pushes pointed at the real origin. That matters because reading public code can be cached, but writing code back should not go through a cache.

The package-host list names public registries and download services that the proxy may route through the cache for agents allowed to use the internet. Keeping this list in one place helps the sandbox, proxy, and cache daemon agree on what is safe.

Finally, the file includes a parser for the cache daemon’s `host:port` setting. If the setting is missing, caching can be disabled. If it is malformed, the code fails loudly, because a broken deployment address should be fixed rather than silently ignored.

#### Function details

##### `cache_git_config`  (lines 46–54)

```
def cache_git_config() -> tuple[tuple[str, str], ...]
```

**Purpose**: This builds the Git settings that make fetches from approved hosts go through the internal cache. It also adds a matching push rule so that pushes still go directly to the original host, not to the cache.

**Data flow**: It starts with the approved Git host list from this file. For each host, it creates two Git configuration entries: one that rewrites normal fetch URLs to the cache address, and one that preserves direct push behavior. It returns all of those entries as an immutable tuple, ready for another part of the system to pass to Git.

**Call relations**: This function is a shared setup helper for code that prepares a sandbox’s Git environment. It does not call other project functions; it simply turns this file’s cache constants into concrete Git configuration that later sandbox or harness setup code can apply.


##### `parse_cache_daemon`  (lines 57–66)

```
def parse_cache_daemon(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This reads the deployment setting that points to a local cache daemon. It accepts a `host:port` string, returns the host and numeric port, and treats malformed values as deployment errors.

**Data flow**: It receives either a string such as `example.internal:8080` or no value at all. If there is no value, it returns `None`, meaning no cache daemon is configured. If there is a value, it splits it at the last colon, checks that a host was present, converts the port text to a number, and returns the pair as `(host, port)`. If the address is missing the required shape, it raises an error instead of guessing.

**Call relations**: This function is meant for configuration-loading code that needs to turn a text setting into a usable network address. It does not hand work to other project helpers; its job is to validate the raw setting early so later cache startup or connection code receives a clear host and port.


### `core/src/ufo/harness/sandbox/client_binary.py`

`util` · `sandbox setup and test setup`

A sandbox needs a real `ufo` command inside it, and local sandbox code may also need to run that same command on the host machine. This file is the shared “address book” for finding that command. Without it, different parts of the system might guess different paths, silently use the wrong binary, or try to compile during a sandbox run.

The search order is deliberate. First, it checks an environment variable, `UFO_CLIENT_BINARY`. An environment variable is a setting passed in from the outside, often by a continuous integration job. If it is set, the file it names is trusted as the chosen artifact, but it must actually exist.

If there is no override, the code looks in the repository’s Rust client build output. It checks both common build folders: `release` first, then `debug`. If the caller asked for a specific Rust target triple, meaning a platform name like “Linux on x86_64,” it looks under that target’s build folder. This matters because the machine preparing a sandbox image may not be the same kind of machine that will run inside the sandbox.

Finally, for host-only use, it checks whether a `ufo` command is already installed on the system path. If nothing is found, it raises a clear error telling the user exactly how to build the missing binary or how to point to one.

#### Function details

##### `client_binary`  (lines 32–59)

```
def client_binary(target: str | None=None) -> Path
```

**Purpose**: Finds the `ufo` executable that should be used by sandbox-related code. A caller can ask for the host machine’s binary, or for a binary built for another platform, such as the Linux binary that belongs inside a sandbox image.

**Data flow**: It starts with an optional target platform name and reads the `UFO_CLIENT_BINARY` environment variable. If that variable names an existing file, it returns that path. Otherwise it searches the client build directory for release and debug builds, optionally under the requested target platform. If no target was requested, it also asks the operating system whether `ufo` is installed on the command path. If all checks fail, it raises an error that explains how to build or provide the binary.

**Call relations**: When sandbox setup, image building, or tests need a concrete `ufo` program, they call this function instead of inventing their own path. Inside its search, it uses `pathlib.Path` to build and check filesystem paths, and `shutil.which` to ask the operating system whether a host-installed `ufo` command is available.

*Call graph*: 2 external calls (Path, which).


### `core/src/ufo/harness/sandbox/exec_env.py`

`domain_logic` · `sandbox open / command setup`

A sandbox needs enough information to run useful commands, such as cloning a private Git repository or asking a connector CLI to talk to Datadog or another service. But it must not receive real API keys or tokens. This file solves that by exporting sentinels: harmless placeholder strings that the egress proxy later swaps for the real credential only when traffic leaves the sandbox. Think of a sentinel like a claim ticket at a coat check: the sandbox holds the ticket, not the coat.

The main piece is `ProbeEnv`, which gathers the stores and declarations needed to decide what a probe may use. When asked for exports, it adds the conversation id, Git configuration passed through environment variables, connector CLI credential placeholders, and keyed-provider placeholders such as `DD_API_KEY` when the workspace has that credential stored.

The file is careful about failure and ambiguity. If a credential slot is unset or unreadable, that provider’s variables are skipped instead of failing the whole sandbox open. If more than one CLI account could match a static environment variable, it logs the ambiguity and exports nothing, so the command fails visibly instead of silently using the wrong account. The result is a sandbox environment that is useful, auditable, and deliberately limited.

#### Function details

##### `ProbeEnv.exports`  (lines 60–72)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, authority: ExecutionAuthority) -> dict[str, str]
```

**Purpose**: Builds the full set of environment variables for an off-turn probe sandbox. It combines the conversation id, Git settings, connector CLI credential placeholders, and keyed-provider placeholders into one dictionary the sandbox opener can use.

**Data flow**: It receives a conversation id, a probe id, and an execution authority that says whose access rules apply. It reads the current workspace id, then asks helper functions to prepare Git configuration, grant-backed CLI variables, and stored credential variables. It returns a plain dictionary of environment variable names to string values, all safe placeholders or non-secret metadata.

**Call relations**: This is the top-level assembly point in the file. When a probe sandbox is being opened, callers use this method to get the environment; it then calls `cli_git_config` and `_git_config_env` for Git, `_grant_cli_env` for connector accounts, and `_keyed_provider_env` for workspace credentials.

*Call graph*: calls 4 internal fn (_git_config_env, _grant_cli_env, _keyed_provider_env, cli_git_config); 1 external calls (ws_current).


##### `_git_config_env`  (lines 75–82)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into the special environment-variable format Git understands. This lets the sandbox influence Git behavior without writing a Git config file.

**Data flow**: It receives a sequence of Git setting pairs, each with a key and value. It counts them, numbers them, and creates variables such as `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0`, and `GIT_CONFIG_VALUE_0`. It returns those variables as a dictionary.

**Call relations**: `ProbeEnv.exports` calls this after gathering both the built-in proxy-auth setting and connector-specific Git helper settings. Its output becomes part of the sandbox environment, so later Git commands inside the sandbox automatically see the right configuration.

*Call graph*: called by 1 (exports).


##### `cli_git_config`  (lines 85–100)

```
def cli_git_config(clis: Mapping[str, CliCredential]) -> tuple[tuple[str, str], ...]
```

**Purpose**: Creates Git credential-helper settings for connector CLIs that know how to authenticate to Git hosts. This lets ordinary `git clone` or `git push` commands use the same safe credential path as the connector CLI.

**Data flow**: It receives the known connector CLI credentials. For each CLI that declares a Git host, it creates Git settings that point that host at the CLI’s credential helper. CLIs without Git support are ignored. It returns a tuple of Git configuration key/value pairs.

**Call relations**: `ProbeEnv.exports` uses this before calling `_git_config_env`. The settings it produces do not choose a specific account by themselves; the matching environment variable is supplied separately by `_grant_cli_env`, so re-authorization can change the credential without rewriting the Git configuration.

*Call graph*: called by 1 (exports).


##### `_keyed_provider_env`  (lines 103–150)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Exports environment variables for provider credentials that the workspace has stored, such as an API-key variable and optionally a provider host or region variable. It exports only sentinels and host names, never the real secret.

**Data flow**: It receives a credential store, declared credential slots, and a workspace id. For each slot that has an injection target, it checks whether the workspace has a stored value and resolves the correct provider host. If the slot is unset, invalid, unreadable, or has no usable host, it skips that slot and may warn. For usable slots, it returns environment variables containing the slot sentinel and any resolved host value.

**Call relations**: `ProbeEnv.exports` calls this while preparing the sandbox environment. This helper reaches into the credential store and host-resolution logic, and uses warnings to make partial failures visible without stopping an unrelated probe from opening.

*Call graph*: calls 1 internal fn (get); called by 1 (exports); 2 external calls (warn, credential_host).


##### `_grant_cli_env`  (lines 153–184)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], authority: ExecutionAuthority, run_id: UUID) -> dict[str, str]
```

**Purpose**: Exports connector CLI environment variables for the accounts the current authority is allowed to use. Each variable receives a grant sentinel, so the CLI can authenticate through the proxy without seeing the real account token.

**Data flow**: It receives a grant store, known connector CLIs, the execution authority, and the run id used for logging. It asks the grant store for active grants, determines the relevant member from the authority, and checks which accounts are usable for each provider. If exactly one account fits, it exports that CLI’s environment variable with that account’s sentinel. If multiple accounts fit, it logs the ambiguity and exports nothing for that provider.

**Call relations**: `ProbeEnv.exports` calls this when building the sandbox environment. It relies on the grant-selection helpers to decide which connector accounts are allowed, and it logs ambiguous cases so a failed CLI authentication can be traced back to the skipped export.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (exports); 4 external calls (log, grant_sentinel, usable_cli_accounts, authority_member_id).


### Session abstraction
The session layer exposes the backend-neutral API that tools use to run commands, read and write files, load skills, and dial services inside any sandbox carrier.

### `core/src/ufo/harness/sandbox/session.py`

`domain_logic` · `cross-cutting`

A sandbox is the isolated workspace where an agent can work with files and run commands without seeing private system records. This file is the contract and common wrapper for that world. Think of it like a power adapter: tools plug into the same shape, while different sandbox providers supply the electricity behind it.

The file defines small value objects for sandbox identity, command results, proxy addresses, and signed tokens. The signed tokens let the egress proxy know which workspace, turn, and authority a network request belongs to, without giving the sandbox broad credentials. It also defines the Carrier protocol, which is the promise every backend must keep: create or attach to a sandbox, execute commands, copy files, expose ports, and perform file operations.

On top of that contract, the Sandbox class gives callers friendly operations such as bash, python, write_file, read_file, load_skills, and dial. It carefully checks paths so normal file tools stay inside /workspace, while only specific read-like operations may look at runtime output or installed skills. Two concrete wrappers matter: SandboxSession is already bound to a live sandbox, while _LateSandbox creates the sandbox only when the first operation needs it. This avoids unnecessary containers but gives every caller the same interface.

#### Function details

##### `egress_proxy_env`  (lines 368–407)

```
def egress_proxy_env(proxy: 'ProxyEndpoint', run_token: str) -> dict[str, str]
```

**Purpose**: Builds the environment variables that make commands inside a remote sandbox send outgoing web traffic through UFO's egress proxy. This is how the system meters and authorizes network access instead of letting the sandbox freely call the internet.

**Data flow**: It receives a proxy endpoint and a signed run token. It checks that the proxy has a usable public HTTPS address, turns that address into standard proxy variables, adds loopback exceptions, model-key sentinels, and certificate settings, then returns the environment dictionary to pass into sandbox commands.

**Call relations**: When a carrier prepares an off-cluster sandbox command, it uses this helper to form the proxy settings. The helper relies on URL parsing to validate and split the public proxy URL before handing back values that ordinary HTTP clients understand.

*Call graph*: 1 external calls (urlsplit).


##### `_basic_username`  (lines 413–419)

```
def _basic_username(header: str) -> str
```

**Purpose**: Extracts the username part from a Basic authentication header. In this system, that username is where signed sandbox tokens are carried.

**Data flow**: It receives a Proxy-Authorization header string, verifies that it uses Basic auth, base64-decodes the credentials, splits username from password, and returns only the username. Bad formats raise an error instead of being treated as anonymous.

**Call relations**: Both RunTokenCodec.from_proxy_auth and ProbeTokenCodec.from_proxy_auth call this first, because they both need to recover a signed token from the proxy authentication header before checking what kind of token it is.

*Call graph*: called by 2 (from_proxy_auth, from_proxy_auth); 1 external calls (b64decode).


##### `RunTokenCodec.from_env`  (lines 438–442)

```
def from_env(cls) -> 'RunTokenCodec'
```

**Purpose**: Creates a run-token signer from the deployment secret stored in the environment. It makes startup fail loudly if the server cannot sign sandbox network tokens.

**Data flow**: It reads the configured token secret environment variable. If it is present, it encodes it as bytes and returns a codec; if it is missing, it raises an error.

**Call relations**: The server startup path calls this while preparing the service. Later, the resulting codec is used to mint and verify per-turn tokens for the egress proxy.

*Call graph*: called by 1 (run).


##### `RunTokenCodec.encode`  (lines 444–448)

```
def encode(self, run: RunToken) -> str
```

**Purpose**: Turns a run's workspace, turn, and authority into a signed token suitable for use as a proxy username. This lets the proxy trust the token came from this deployment.

**Data flow**: It receives a RunToken object, converts the authority into a member id when there is one, builds a compact text payload, signs it with the codec secret, and returns the signed string.

**Call relations**: The sandbox-opening flow calls this when it needs a run token for a turn. It delegates signing to the shared token-signing helper and authority conversion to the runtime authority helper.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (sign_token, authority_member_id).


##### `RunTokenCodec.from_proxy_auth`  (lines 450–462)

```
def from_proxy_auth(self, header: str) -> RunToken
```

**Purpose**: Reads and verifies a run token from a proxy authorization header. It rejects forged tokens, malformed tokens, and tokens from the wrong token family.

**Data flow**: It receives an auth header, extracts the Basic username, verifies the signed value with the secret, splits the decoded payload into workspace, turn, and member parts, converts those into UUIDs and authority, and returns a RunToken. Any invalid step becomes a clear token error.

**Call relations**: The egress proxy uses this when a sandbox command tries to reach the network. It first calls _basic_username, then the token verifier, and finally rebuilds the RunToken object that downstream policy can check.

*Call graph*: calls 1 internal fn (_basic_username); 4 external calls (__init__, verify_token, authority_from_member_id, UUID).


##### `ProbeTokenCodec.encode`  (lines 495–502)

```
def encode(self, probe: ProbeToken) -> str
```

**Purpose**: Creates a signed token for an off-turn probe command. A probe has its own expiry because it is not tied to a running turn row.

**Data flow**: It receives a ProbeToken, converts authority into a member id if needed, builds a payload containing workspace, conversation, probe id, member, and expiration time, signs it, and returns the signed token string.

**Call relations**: Probe execution code can use this codec when it needs temporary network authority. It uses the same signing machinery as run tokens, but labels the payload with the probe token kind so the two cannot be confused.

*Call graph*: 2 external calls (sign_token, authority_member_id).


##### `ProbeTokenCodec.from_proxy_auth`  (lines 504–520)

```
def from_proxy_auth(self, header: str) -> ProbeToken
```

**Purpose**: Reads and verifies a probe token from a proxy authorization header. It ensures the token is genuine, belongs to the probe token family, and carries parseable ids and expiry.

**Data flow**: It receives an auth header, extracts the Basic username, verifies the signature, splits the decoded payload, converts ids and expiry into typed values, and returns a ProbeToken. Bad signatures, wrong token kind, or malformed fields raise an error.

**Call relations**: The egress proxy uses this for probe traffic. Like run-token decoding, it starts with _basic_username, but it reconstructs a ProbeToken instead of a RunToken.

*Call graph*: calls 1 internal fn (_basic_username); 4 external calls (__init__, verify_token, authority_from_member_id, UUID).


##### `sandbox_handle_id`  (lines 608–613)

```
def sandbox_handle_id(backend: str, value: str) -> str | None
```

**Purpose**: Pulls the raw sandbox id out of a stored backend-prefixed handle, but only if it belongs to the requested backend. This prevents one sandbox provider from trying to resume another provider's sandbox.

**Data flow**: It receives a backend name and a stored handle string. If the string starts with that backend plus the separator, it returns the remaining id; otherwise it returns None.

**Call relations**: Carrier-specific resume code uses this when deciding whether a saved conversation handle is theirs. It is paired with sandbox_handle_backend, which reads the backend prefix without checking ownership.


##### `sandbox_handle_backend`  (lines 616–619)

```
def sandbox_handle_backend(value: str) -> str
```

**Purpose**: Returns the backend prefix from a stored sandbox handle. This tells the system which carrier originally created that handle.

**Data flow**: It receives a handle string in backend:id form, splits at the first separator, and returns the backend part.

**Call relations**: Routing or resume logic can use this to choose the right carrier when several sandbox backends may exist during a deployment transition.


##### `Carrier.create`  (lines 657–657)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Defines the contract for creating or attaching to a usable sandbox. Implementing carriers provide the real behavior.

**Data flow**: The caller supplies a SandboxSpec describing conversation, image, workspace, proxy, token, size, and related setup. The carrier returns a SandboxHandle that later operations use.

**Call relations**: Sandbox-opening code calls this through a concrete carrier. SandboxSession then keeps the returned handle and passes it back to the same carrier for commands and file access.


##### `Carrier.attach`  (lines 659–665)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Defines how a backend should reconnect to an already existing sandbox without creating a new one. This matters for read-only or recovery paths where simply browsing files must not start a fresh container.

**Data flow**: It receives a SandboxSpec that may name a previous sandbox id. The carrier returns a handle if that sandbox is reachable, or None if it is absent.

**Call relations**: Late or existing-session flows use this kind of method when they need to check for an existing sandbox. Concrete carriers decide what 'reachable' means for their backend.


##### `Carrier.exec`  (lines 667–680)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Defines how to run one command inside the sandbox. It also lets a backend know when the model authored the command text, which matters for carriers running on a user's own machine.

**Data flow**: The caller provides a sandbox handle, command arguments, timeout, and optionally the model-written command string. The carrier runs it and returns stdout, stderr, exit code, and timeout information as an ExecResult.

**Call relations**: Most Sandbox command helpers eventually call this. SandboxCommands builds safe command shapes, while the concrete carrier performs the actual execution.


##### `Carrier.write`  (lines 682–694)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Defines how to copy bytes into a sandbox file. The contract requires path safety and avoids pushing file content through command-line arguments.

**Data flow**: It receives a handle, an absolute sandbox path, and bytes. The carrier writes those bytes into the sandbox, creating parents if needed, or raises an error if the write is unsafe or fails.

**Call relations**: Sandbox.write_file, runtime-file helpers, and skill staging all call carrier write. Each backend implements the copy-in using the safest method available for that environment.


##### `Carrier.read`  (lines 696–707)

```
def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Defines how to stream a file out of the sandbox without loading the whole file into memory at once. This is the copy-out side of sandbox file access.

**Data flow**: It receives a handle and a sandbox path. It yields byte chunks until the file is fully read, or raises an appropriate error if the file cannot be opened safely.

**Call relations**: Sandbox.read_file and internal read helpers delegate to this. Concrete carriers provide different streaming mechanisms, such as filesystem APIs, stdout pipes, or local file reads.


##### `Carrier.dial`  (lines 709–718)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Defines how outside code can reach a service listening on a port inside the sandbox. This is used for things like browser debugging ports or preview servers.

**Data flow**: It receives a handle and an in-sandbox port number. The carrier returns a DialTarget containing host, TLS choice, and any required headers, or raises SandboxUnreachable if no route exists.

**Call relations**: Sandbox.dial calls this for consumers such as the sandbox Chrome extension. Each backend translates the internal port into whatever public or local address it can expose.


##### `Carrier.file_op`  (lines 720–730)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Defines a bounded, structured file-operation API for tools. It lets tools read, edit, glob, grep, and inspect files without pulling entire files across the sandbox boundary unnecessarily.

**Data flow**: It receives a handle, an operation name, and JSON-like parameters. The carrier runs the operation in its workspace and returns a parsed JSON-like result, or raises a model-recoverable or system error.

**Call relations**: Sandbox.run_ufo_fs calls this after scoping paths. Carriers may use ufo_fs_file_op to share the standard in-sandbox command implementation.


##### `CommandStopping.stop_commands`  (lines 750–750)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: Defines the optional ability to stop commands that may continue running after their launch call is cancelled. Not every carrier needs this.

**Data flow**: It receives a handle that includes the turn id. The carrier stops commands associated with that turn, without stopping sibling turns sharing the same sandbox.

**Call relations**: Sandbox.stop_commands and _LateSandbox.stop_commands call this only when the carrier declares that it supports command stopping.


##### `SkillLoading.load_skills`  (lines 757–759)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Defines an optional native way for a carrier to load skills into the sandbox runtime. Some connected runtimes can do this without the generic staged-file path.

**Data flow**: It receives a sandbox handle and a skill payload. The carrier installs or locates the requested skills and returns an ExecResult whose output describes the skill roots.

**Call relations**: Sandbox.load_skills prefers this method when the carrier supports it. Otherwise it falls back to staging a payload and running the shared in-sandbox loader.


##### `SkillExecuting.exec_skill`  (lines 766–768)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Defines an optional privileged command path for running server-provided skill installation programs. It is separate from normal command execution because skill setup may need more authority.

**Data flow**: It receives a handle, command arguments, and timeout. The carrier runs the skill-related program and returns an ExecResult.

**Call relations**: Sandbox._exec_skill calls this after checking the carrier supports SkillExecuting. The staged skill-load and system-skill sync paths both depend on it.


##### `SystemSkillSeeding.seed_system_skills`  (lines 775–775)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Defines an optional way for a carrier to seed system skills into a runtime filesystem created by this process. It is for backends where the server prepares the runtime storage directly.

**Data flow**: It receives a system skill archive as bytes. The carrier stores or installs those bytes according to its backend-specific runtime layout.

**Call relations**: Carriers that create runtime filesystems can implement this protocol. The rest of this module treats it as an optional capability rather than part of every carrier.


##### `ufo_fs_file_op`  (lines 778–791)

```
async def ufo_fs_file_op(carrier: Carrier, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs the standard in-sandbox UFO file command and returns its parsed file-operation result. This saves container-style carriers from reimplementing the same file command wrapper.

**Data flow**: It receives a carrier, handle, operation name, and parameters. It builds a SandboxFileOperations runner that uses carrier.exec, applies standard timeouts and document suffix rules, runs the operation, and returns the resulting dictionary.

**Call relations**: Concrete carriers can use this as their Carrier.file_op implementation. It hands actual command execution to the carrier while centralizing the shared command name, timeout choices, and result parsing.

*Call graph*: 1 external calls (__init__).


##### `host_argv`  (lines 799–811)

```
def host_argv(argv: tuple[str, ...], root: str) -> tuple[str, ...]
```

**Purpose**: Rewrites command arguments for carriers where /workspace is actually a host directory. It changes only true /workspace path segments, not similar text inside URLs or other names.

**Data flow**: It receives a tuple of command arguments and the host root path. For each argument, it replaces whole /workspace path segments with the host root and returns the rewritten tuple.

**Call relations**: Host-path carriers use this before executing commands locally. It supports the larger sandbox illusion: tools can always talk about /workspace even when the backend stores it somewhere else.


##### `workspace_path`  (lines 814–822)

```
def workspace_path(path: str) -> str
```

**Purpose**: Normalizes a caller-supplied file path and rejects attempts to leave /workspace. This is a core safety check for file tools.

**Data flow**: It receives a path that may be absolute or relative. It treats relative paths as inside /workspace, resolves . and .. segments, checks the result is still under /workspace, and returns the normalized path.

**Call relations**: Many Sandbox methods call this before writes, existence checks, file operations, and scoped reads. It uses _resolve_parts to perform the path cleanup.

*Call graph*: calls 1 internal fn (_resolve_parts); called by 5 (_read_scoped_file, file_exists, run_ufo_fs, write_file, rooted_path); 1 external calls (PurePosixPath).


##### `runtime_relative`  (lines 825–835)

```
def runtime_relative(path: str) -> PurePosixPath
```

**Purpose**: Checks that a runtime-internal relative path stays inside the conversation's runtime area. Runtime files are system-owned scratch files, not user workspace files.

**Data flow**: It receives a relative path string, runs it through the containment guard under a fake /runtime root, rejects changed or escaping paths, and returns a PurePosixPath relative path.

**Call relations**: _runtime_path and _runtime_display_path call this before building real runtime paths. That keeps internal file names safe even though they are outside /workspace.

*Call graph*: called by 2 (_runtime_display_path, _runtime_path); 2 external calls (PurePosixPath, contained_relative).


##### `sandbox_runtime_root`  (lines 838–840)

```
def sandbox_runtime_root(conversation_id: UUID) -> str
```

**Purpose**: Builds the default runtime directory path for one conversation inside the sandbox. This is where private run data lives under UFO_HOME.

**Data flow**: It receives a conversation UUID, uses its hex form as the directory name, and returns the full sandbox path under the runs root.

**Call relations**: _runtime_root calls this when a SandboxHandle does not already carry a custom runtime root.

*Call graph*: called by 1 (_runtime_root).


##### `shell_path`  (lines 843–848)

```
def shell_path(path: str) -> str
```

**Purpose**: Quotes a sandbox path so it can be safely placed in a shell command, while preserving a leading $UFO_HOME expansion when intended. This avoids accidental shell interpretation.

**Data flow**: It receives a path string. If it starts with $UFO_HOME/, it quotes only the remainder and leaves the environment variable expandable; otherwise it shell-quotes the whole path.

**Call relations**: Callers that need to display or compose shell-safe paths can use this helper. It relies on standard shell quoting to avoid injection through path text.

*Call graph*: 1 external calls (quote).


##### `_runtime_root`  (lines 851–852)

```
def _runtime_root(handle: SandboxHandle) -> str
```

**Purpose**: Returns the active runtime root for a sandbox handle. It uses an explicit handle value when present, otherwise the default per-conversation path.

**Data flow**: It receives a SandboxHandle. If handle.runtime_root is set, it returns that; otherwise it builds the default path from the conversation id.

**Call relations**: Runtime path helpers and Sandbox methods call this whenever they need to locate internal run files. It delegates to sandbox_runtime_root for the default case.

*Call graph*: calls 1 internal fn (sandbox_runtime_root); called by 7 (_read_scoped_file, _run_staged_skill_load, _sync_system_skills, run_ufo_fs, write_runtime_path, _runtime_display_path, _runtime_path).


##### `_runtime_path`  (lines 855–856)

```
def _runtime_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds an absolute sandbox path for a runtime-owned file. It ensures the relative part cannot escape the runtime root.

**Data flow**: It receives a handle and a relative runtime path. It finds the runtime root, validates the relative path, joins them, and returns the resulting string.

**Call relations**: Runtime file writes, existence checks, skill staging, output directory setup, and display helpers use this to address private runtime files safely.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 7 (_run_staged_skill_load, _sync_system_skills, ensure_tool_output_dir, runtime_file_exists, runtime_path, write_runtime_file, write_runtime_path); 1 external calls (PurePosixPath).


##### `_runtime_display_path`  (lines 859–863)

```
def _runtime_display_path(handle: SandboxHandle, relative: str) -> str
```

**Purpose**: Builds the user-facing $UFO_HOME path for a runtime file. This gives agents a reusable-looking path without exposing backend-specific details.

**Data flow**: It receives a handle and a relative runtime path. It gets the run directory name from the runtime root, validates the relative path, and returns a path starting with $UFO_HOME/runs/...

**Call relations**: Sandbox.runtime_display_path calls this when a caller needs to show or reuse an internal runtime path in sandbox terms.

*Call graph*: calls 2 internal fn (_runtime_root, runtime_relative); called by 1 (runtime_display_path); 1 external calls (PurePosixPath).


##### `rooted_path`  (lines 866–869)

```
def rooted_path(path: str, root: str) -> str
```

**Purpose**: Normalizes a path under a chosen allowed root while preserving that root's spelling. This is useful for roots such as the runtime display path or skills directory.

**Data flow**: It receives a path and a root string. It temporarily maps the path into /workspace form for safety checking, then maps the normalized suffix back under the original root.

**Call relations**: Sandbox.run_ufo_fs and Sandbox._read_scoped_file use this for safe read-only access outside normal /workspace roots, such as runtime output or skill files.

*Call graph*: calls 1 internal fn (workspace_path); called by 2 (_read_scoped_file, run_ufo_fs).


##### `_resolve_parts`  (lines 872–881)

```
def _resolve_parts(parts: tuple[str, ...]) -> list[str]
```

**Purpose**: Simplifies path parts by removing empty and current-directory pieces and applying parent-directory pieces safely. It refuses paths that would climb above the root.

**Data flow**: It receives path parts from PurePosixPath. It walks them like a stack, popping on .. when safe, adding normal parts, and raising an error if .. would escape the root.

**Call relations**: workspace_path uses this as its low-level path normalizer. It is intentionally small because many higher-level safety checks depend on it.

*Call graph*: called by 1 (workspace_path).


##### `Sandbox.conversation_id`  (lines 893–897)

```
def conversation_id(self) -> UUID
```

**Purpose**: Declares that every Sandbox can report which conversation's workspace it represents. The base class leaves the actual value to subclasses.

**Data flow**: There is no input besides the Sandbox object. A concrete subclass returns a UUID; the base implementation raises because it is only a contract.

**Call relations**: SandboxSession, _LateSandbox, and _AuthorizedSandbox each provide the value in their own way. Callers can ask for the conversation id without caring whether the sandbox is already created.


##### `Sandbox.turn_id`  (lines 900–904)

```
def turn_id(self) -> UUID | None
```

**Purpose**: Declares that a Sandbox can report the turn whose authority it carries, if it is tied to a turn. The base class defines the interface.

**Data flow**: There is no input besides the Sandbox object. A subclass returns a turn UUID or None; the base implementation raises.

**Call relations**: Concrete sandbox wrappers expose this so stopping commands and scoping resources can distinguish sibling turns sharing one container.


##### `Sandbox.created`  (lines 907–909)

```
def created(self) -> bool
```

**Purpose**: Declares whether a real sandbox session already exists behind this object. This lets callers ask without forcing lazy creation.

**Data flow**: It reads the sandbox wrapper's state and returns a boolean in concrete subclasses. The base implementation raises.

**Call relations**: SandboxSession always says yes, while _LateSandbox says yes only after its first successful bind. _AuthorizedSandbox forwards the same answer.


##### `Sandbox.authorize`  (lines 911–919)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'Sandbox'
```

**Purpose**: Declares how to view the same sandbox under a specific network authority and environment. This supports per-turn or per-member egress credentials.

**Data flow**: It receives a run token, names of environment variables to clear, and variables to add. Concrete implementations return a Sandbox wrapper with that adjusted authorization.

**Call relations**: SandboxSession rewrites an existing handle, while _LateSandbox returns an _AuthorizedSandbox that will apply the authorization after lazy creation.


##### `Sandbox._bound`  (lines 921–922)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Declares how a Sandbox becomes a concrete SandboxSession. This is the hidden hook that lets all public operations work for both already-created and lazy sandboxes.

**Data flow**: It takes the Sandbox object and returns an awaitable SandboxSession in subclasses. The base implementation raises.

**Call relations**: Nearly every Sandbox operation calls _bound first, then performs carrier work through the returned session.

*Call graph*: called by 18 (_read_file, _read_scoped_file, bash, bash_task, dial, ensure_tool_output_dir, file_exists, load_skills, python, run_ufo_fs (+8 more)).


##### `Sandbox.runtime_path`  (lines 924–926)

```
async def runtime_path(self, relative: str) -> str
```

**Purpose**: Returns the absolute internal runtime path for a relative runtime file. Callers use it when they need the real sandbox path, not the display form.

**Data flow**: It receives a relative path, waits for the sandbox session, validates and joins the path under the runtime root, and returns the absolute string.

**Call relations**: It calls _bound so it works with lazy sandboxes, then uses _runtime_path for the safe path construction.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.runtime_display_path`  (lines 928–930)

```
async def runtime_display_path(self, relative: str) -> str
```

**Purpose**: Returns a friendly $UFO_HOME-based path for an internal runtime file. This is suitable for showing to or reusing inside sandbox commands.

**Data flow**: It receives a relative path, binds to the session, validates the relative path, and returns a display path under $UFO_HOME/runs/...

**Call relations**: It calls _bound and then _runtime_display_path. It is the public wrapper around the private display-path helper.

*Call graph*: calls 2 internal fn (_bound, _runtime_display_path).


##### `Sandbox.write_runtime_file`  (lines 932–935)

```
async def write_runtime_file(self, relative: str, content: bytes) -> None
```

**Purpose**: Writes bytes to a runtime-owned file outside the user's workspace. This is for engine-owned files such as staged payloads or tool output.

**Data flow**: It receives a relative runtime path and bytes. It binds the sandbox, converts the relative name into a safe runtime path, and asks the carrier to write the bytes there.

**Call relations**: It uses the same carrier.write path as workspace writes, but targets the private runtime root through _runtime_path.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.write_runtime_path`  (lines 937–945)

```
async def write_runtime_path(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes to an already-resolved runtime path, after confirming it is inside this sandbox's runtime root. This protects against accidental writes to arbitrary sandbox locations.

**Data flow**: It receives an absolute path and content. It binds the sandbox, checks the path is below the runtime root and not the root itself, converts it back to a relative path, then writes via the carrier.

**Call relations**: It combines _runtime_root and _runtime_path so even pre-resolved paths go through the same runtime containment rules.

*Call graph*: calls 3 internal fn (_bound, _runtime_path, _runtime_root); 1 external calls (PurePosixPath).


##### `Sandbox.runtime_file_exists`  (lines 947–954)

```
async def runtime_file_exists(self, relative: str) -> bool
```

**Purpose**: Checks whether a runtime-owned regular file exists. It uses a small shell test inside the sandbox.

**Data flow**: It receives a relative runtime path. It binds the sandbox, builds the safe target path, runs test -f through the carrier, and returns true when the exit code is zero.

**Call relations**: It uses _runtime_path for safety and carrier.exec for the actual check. This mirrors file_exists but targets runtime storage instead of /workspace.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox._commands`  (lines 956–967)

```
def _commands(self, bound: 'SandboxSession', model_command: str | None=None) -> SandboxCommands[ExecResult]
```

**Purpose**: Builds a shared command runner for bash, shell, Python, and journaled tasks. It centralizes timeouts, Python isolation, bootstrap code, and the command supervisor.

**Data flow**: It receives a bound session and optionally model-authored command text. It creates a SandboxCommands object whose execute callback calls the carrier with the session handle and model-command marker.

**Call relations**: Sandbox.bash, bash_task, sh, and python all call this before running their command. SandboxCommands then formats the actual argv and hands execution back through carrier.exec.

*Call graph*: called by 4 (bash, bash_task, python, sh); 1 external calls (__init__).


##### `Sandbox.bash`  (lines 969–971)

```
async def bash(self, command: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Bash command inside the sandbox. It is the simple public method for command execution.

**Data flow**: It receives command text and an optional timeout. It binds the sandbox, builds the shared command runner, runs the Bash command, and returns the ExecResult.

**Call relations**: Sandbox Chrome support calls this for setup, teardown, and recovery commands. Internally it relies on _commands, which delegates execution to the carrier.

*Call graph*: calls 2 internal fn (_bound, _commands); called by 6 (_lease, reattach, _abandon_allocation, _bring_up_failure, _stop_bridge, _stop_stack).


##### `Sandbox.bash_task`  (lines 973–990)

```
async def bash_task(self, command: str, base: str, *, detach: bool, model_authored: bool, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs or reattaches a journaled Bash task through the sandbox supervisor. It also marks whether the model wrote the command text.

**Data flow**: It receives command text, a task base name, detach and model-authored flags, and optional timeout. It binds the sandbox, builds a command runner with the model command when appropriate, runs the task, and returns an ExecResult.

**Call relations**: The sandbox Chrome bridge startup uses this when launching a long-running bridge. It passes the work through SandboxCommands so detached or supervised execution follows the same rules as other sandbox commands.

*Call graph*: calls 2 internal fn (_bound, _commands); called by 1 (_start_bridge).


##### `Sandbox.sh`  (lines 992–997)

```
async def sh(self, script: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a POSIX shell script inside the sandbox with separate positional arguments. Keeping arguments separate avoids unsafe string interpolation.

**Data flow**: It receives script text, zero or more string arguments, and an optional timeout. It binds the sandbox, builds the shared command runner, runs the script, and returns the ExecResult.

**Call relations**: Callers can use this when they need portable sh behavior rather than Bash. Like bash and python, it routes through _commands and then carrier.exec.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.python`  (lines 999–1010)

```
async def python(self, program: str, *args: str, timeout_s: int | None=None) -> ExecResult
```

**Purpose**: Runs a Python program inside the sandbox with the containment guard already bootstrapped. This gives small helper programs the same path-safety checks as the main file tools.

**Data flow**: It receives Python source, arguments, and optional timeout. It binds the sandbox, builds the command runner with isolated Python settings and guard bootstrap, executes the program, and returns the ExecResult.

**Call relations**: Higher-level code can use this for safe in-sandbox Python helpers. It shares command construction with other command methods through _commands.

*Call graph*: calls 2 internal fn (_bound, _commands).


##### `Sandbox.stop_commands`  (lines 1012–1018)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this turn when the backend supports explicit stopping. It is used after the system knows a cancellation was deliberate.

**Data flow**: It binds the sandbox. If the carrier implements CommandStopping, it asks the carrier to stop commands for the current handle; otherwise it does nothing.

**Call relations**: This public method hides optional carrier support from callers. _LateSandbox has a special version so it can stop existing commands without necessarily creating a sandbox.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.write_file`  (lines 1020–1022)

```
async def write_file(self, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the conversation workspace. It first forces the path to stay under /workspace.

**Data flow**: It receives a path and bytes. It binds the sandbox, normalizes and checks the path with workspace_path, then asks the carrier to write the content.

**Call relations**: Tools and inbound file-delivery paths use this instead of calling the carrier directly. The carrier handles the backend copy, while this method enforces the workspace boundary.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.load_skills`  (lines 1024–1066)

```
async def load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Loads system and user skills into the sandbox's UFO_HOME skill tree and returns their installed roots. It verifies the loader output so unexpected paths or names are not trusted blindly.

**Data flow**: It receives a skill payload. It binds the sandbox, chooses native carrier loading if available or the staged loader otherwise, checks the command succeeded, parses JSON output, validates returned names and paths, and may refresh system skills once before retrying.

**Call relations**: The runtime skill installation and loading code calls this. It may call _run_staged_skill_load, _sync_system_skills, and JSON parsing as part of its fallback and validation flow.

*Call graph*: calls 3 internal fn (_bound, _run_staged_skill_load, _sync_system_skills); called by 2 (install_skill, load_skills); 1 external calls (loads).


##### `Sandbox._run_staged_skill_load`  (lines 1068–1090)

```
async def _run_staged_skill_load(self, bound: 'SandboxSession', payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Implements the generic skill-load path for carriers without native loading. It stages a JSON payload in runtime storage, then runs the in-sandbox skill loader program.

**Data flow**: It receives a bound session and payload. It serializes the payload, writes it to a unique runtime staging file, computes its hash, builds the Python loader command with the expected hash, and executes it as a privileged skill program.

**Call relations**: Sandbox.load_skills calls this when native loading is unavailable or after refreshing system skills. It delegates actual privileged execution to _exec_skill.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 3 external calls (sha256, dumps, uuid4).


##### `Sandbox._sync_system_skills`  (lines 1092–1109)

```
async def _sync_system_skills(self, bound: 'SandboxSession') -> None
```

**Purpose**: Copies the system skill archive into the sandbox and installs it into the skill tree. This refreshes missing or stale baked system skills.

**Data flow**: It receives a bound session. It writes the archive to a unique runtime staging file, computes the archive hash, runs the system-skill sync Python program, and raises an error if the command fails.

**Call relations**: Sandbox.load_skills calls this when a staged load cannot find expected system skills and a system archive is available. It uses _exec_skill for privileged execution.

*Call graph*: calls 3 internal fn (_exec_skill, _runtime_path, _runtime_root); called by 1 (load_skills); 2 external calls (sha256, uuid4).


##### `Sandbox._exec_skill`  (lines 1111–1116)

```
async def _exec_skill(self, bound: 'SandboxSession', argv: tuple[str, ...]) -> ExecResult
```

**Purpose**: Runs a privileged skill-related command through carriers that support that capability. It refuses to continue if the carrier cannot execute skill programs safely.

**Data flow**: It receives a bound session and argv. It checks the carrier implements SkillExecuting, then calls exec_skill with the default execution timeout and returns the ExecResult.

**Call relations**: _run_staged_skill_load and _sync_system_skills both call this. It is the narrow gate between generic Sandbox logic and carrier-specific privileged skill execution.

*Call graph*: called by 2 (_run_staged_skill_load, _sync_system_skills).


##### `Sandbox.ensure_tool_output_dir`  (lines 1118–1142)

```
async def ensure_tool_output_dir(self) -> bool
```

**Purpose**: Makes sure the engine's private tool-output directory exists in runtime storage. If a file or broken link is squatting on that fixed name, it removes it first.

**Data flow**: It binds the sandbox, builds the runtime tool-output path, runs a shell script that checks for a directory, removes a non-directory occupant, creates the directory, and returns whether it had to reclaim the name.

**Call relations**: Tool-output offload code can call this before writing private output files. It uses carrier.exec rather than direct filesystem assumptions so every backend follows the same behavior.

*Call graph*: calls 2 internal fn (_bound, _runtime_path).


##### `Sandbox.file_exists`  (lines 1144–1150)

```
async def file_exists(self, path: str) -> bool
```

**Purpose**: Checks whether a regular file exists in /workspace. It is a safe, boolean convenience method.

**Data flow**: It receives a path, scopes it with workspace_path, binds the sandbox, runs test -f on the target through the carrier, and returns true only for exit code zero.

**Call relations**: Higher-level tool code can use this before reads or writes. It mirrors runtime_file_exists but applies the workspace boundary.

*Call graph*: calls 2 internal fn (_bound, workspace_path).


##### `Sandbox.run_ufo_fs`  (lines 1152–1193)

```
async def run_ufo_fs(self, op: str, args: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one structured file operation in the sandbox after deciding which root the path is allowed to use. Normal operations stay in /workspace; read, glob, and grep may also inspect runtime output or skills.

**Data flow**: It receives an operation name and arguments. It copies the arguments, scopes any path to /workspace, runtime, or skills according to strict rules, sets the workspace root parameter, then calls carrier.file_op and returns its dictionary result.

**Call relations**: File tools call this to perform bounded file operations. It uses workspace_path, rooted_path, and _runtime_root before handing the final request to the carrier.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); 1 external calls (PurePosixPath).


##### `Sandbox.read_file`  (lines 1195–1197)

```
def read_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Starts a streamed read of a workspace file or current runtime file. It returns chunks instead of buffering the whole file.

**Data flow**: It receives a path and returns the async byte stream produced by _read_scoped_file. The actual binding and path checks happen as the stream is consumed.

**Call relations**: Callers use this public method for file downloads or tool reads. It delegates to _read_scoped_file so the same path rules are shared.

*Call graph*: calls 1 internal fn (_read_scoped_file).


##### `Sandbox._read_scoped_file`  (lines 1199–1212)

```
async def _read_scoped_file(self, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file after scoping it either to /workspace or to the current runtime root. This allows reading engine-produced runtime files without opening arbitrary UFO_HOME paths.

**Data flow**: It receives a path, binds the sandbox, checks whether the path names the runtime display root, real runtime root, or normal workspace, normalizes it accordingly, then yields chunks from carrier.read.

**Call relations**: Sandbox.read_file calls this. It uses _runtime_root, rooted_path, and workspace_path before delegating the actual stream to the carrier.

*Call graph*: calls 4 internal fn (_bound, _runtime_root, rooted_path, workspace_path); called by 1 (read_file); 1 external calls (PurePosixPath).


##### `Sandbox._read_file`  (lines 1214–1217)

```
async def _read_file(self, target: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file from an already chosen sandbox target path. It is a lower-level helper that skips the public path scoping step.

**Data flow**: It receives a target path, binds the sandbox, calls carrier.read, and yields each byte chunk it receives.

**Call relations**: Internal code can use this when it already has a trusted target path. It still goes through _bound so lazy sandboxes behave the same as live sessions.

*Call graph*: calls 1 internal fn (_bound).


##### `Sandbox.dial`  (lines 1219–1223)

```
async def dial(self, port: int) -> DialTarget
```

**Purpose**: Asks the carrier how to reach a port exposed by a service inside the sandbox. This gives outside code the right host, protocol choice, and headers.

**Data flow**: It receives a port number, binds the sandbox, calls carrier.dial with the handle and port, and returns the DialTarget.

**Call relations**: The sandbox Chrome extension calls this when it needs an endpoint for an in-sandbox service. The backend-specific carrier decides how the port is published.

*Call graph*: calls 1 internal fn (_bound); called by 1 (_endpoint).


##### `SandboxSession.conversation_id`  (lines 1237–1238)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id from an already bound sandbox handle. This identifies which workspace the session reaches.

**Data flow**: It reads handle.conversation_id and returns it.

**Call relations**: This fulfills the Sandbox.conversation_id contract for live sessions. Code can ask for it without involving the carrier.


##### `SandboxSession.turn_id`  (lines 1241–1242)

```
def turn_id(self) -> UUID | None
```

**Purpose**: Returns the turn id carried by this live sandbox handle, if any. This is used to scope authority and stoppable commands.

**Data flow**: It reads handle.turn_id and returns the UUID or None.

**Call relations**: This fulfills the Sandbox.turn_id contract for SandboxSession. Stop and authorization flows rely on the handle's turn value.


##### `SandboxSession.created`  (lines 1245–1246)

```
def created(self) -> bool
```

**Purpose**: Reports that a SandboxSession is already created and bound to a carrier handle. There is no lazy work left to do.

**Data flow**: It returns true.

**Call relations**: This fulfills the Sandbox.created contract. It contrasts with _LateSandbox, which may not have opened anything yet.


##### `SandboxSession._bound`  (lines 1248–1249)

```
async def _bound(self) -> 'SandboxSession'
```

**Purpose**: Returns this session as the bound sandbox. It is the simplest implementation of the common binding hook.

**Data flow**: It receives no extra input and returns self.

**Call relations**: All inherited Sandbox operations call _bound first. For SandboxSession, that call is immediate because the carrier and handle are already available.


##### `SandboxSession.authorize`  (lines 1251–1279)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> 'SandboxSession'
```

**Purpose**: Creates a new live session for the same sandbox but with a different run token and adjusted environment. This lets the same container be used under a new exact authority.

**Data flow**: It receives a new run token, environment variable names to remove, and variables to add. It checks the existing handle has a token and that proxy variables contain it, replaces that token inside proxy environment values, drops cleared variables, merges added ones, and returns a new SandboxSession with a new handle.

**Call relations**: _AuthorizedSandbox calls this after lazy binding, and direct callers can use it on live sessions. It rebuilds SandboxHandle and SandboxSession rather than mutating the original.

*Call graph*: 2 external calls (__init__, __init__).


##### `_LateSandbox.__init__`  (lines 1283–1295)

```
def __init__(self, conversation_id: UUID, turn_id: UUID, open: Callable[[], Awaitable[SandboxSession]], existing: Callable[[], Awaitable[SandboxSession | None]]) -> None
```

**Purpose**: Creates a lazy sandbox wrapper that knows how to open a session later and how to look for an existing one. This avoids starting a sandbox until the first real operation needs it.

**Data flow**: It receives conversation id, turn id, an async open function, and an async existing-session function. It stores them, creates an async lock to prevent duplicate opens, and starts with no session.

**Call relations**: Public Sandbox methods inherited by _LateSandbox will eventually call _bound, which uses the stored opener. stop_commands uses the existing-session function so stopping does not necessarily create a new sandbox.

*Call graph*: 1 external calls (Lock).


##### `_LateSandbox.conversation_id`  (lines 1298–1299)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id known before the lazy sandbox is created. This lets callers name the workspace without forcing creation.

**Data flow**: It reads the stored conversation UUID and returns it.

**Call relations**: This implements the Sandbox.conversation_id contract for lazy sandboxes. _AuthorizedSandbox forwards to it.


##### `_LateSandbox.turn_id`  (lines 1302–1303)

```
def turn_id(self) -> UUID
```

**Purpose**: Returns the turn id associated with this lazy sandbox reference. The turn is known even before the actual sandbox exists.

**Data flow**: It reads the stored turn UUID and returns it.

**Call relations**: This implements the Sandbox.turn_id contract for lazy sandboxes and lets stop logic remain turn-scoped.


##### `_LateSandbox.created`  (lines 1306–1307)

```
def created(self) -> bool
```

**Purpose**: Reports whether the lazy wrapper has already opened its real session. It does not open the sandbox just to answer.

**Data flow**: It checks whether the stored session is None and returns a boolean.

**Call relations**: This implements the Sandbox.created contract. _AuthorizedSandbox forwards to it so authorization wrapping does not hide lazy state.


##### `_LateSandbox.authorize`  (lines 1309–1315)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Returns an authorization wrapper around this lazy sandbox. The new token and environment will be applied only after the underlying session is opened.

**Data flow**: It receives a run token, cleared environment names, and extra environment values. It creates and returns an _AuthorizedSandbox holding those details.

**Call relations**: This supports the common Sandbox.authorize contract for lazy sandboxes. _AuthorizedSandbox later calls back into the late sandbox's _bound method.

*Call graph*: 1 external calls (__init__).


##### `_LateSandbox._bound`  (lines 1317–1322)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Opens the sandbox on first use and returns the resulting session. It uses a lock so two simultaneous first operations do not create two sandboxes.

**Data flow**: It checks whether a session already exists. If not, it enters the async lock, checks again, calls the stored open function once, saves the session, and returns it.

**Call relations**: Every inherited Sandbox operation calls this before doing carrier work. It is the core of the lazy-create behavior described by the file.


##### `_LateSandbox.stop_commands`  (lines 1324–1327)

```
async def stop_commands(self) -> None
```

**Purpose**: Stops commands for this turn without unnecessarily creating a sandbox. This is important when cancellation happens before any normal operation opened the sandbox.

**Data flow**: It uses the existing session if one is already open, otherwise asks for an existing session. If a session exists and the carrier supports command stopping, it replaces the handle's turn id with this lazy reference's turn id and stops those commands.

**Call relations**: This overrides Sandbox.stop_commands because the base version would call _bound and create a sandbox. It uses dataclass replacement to scope the stop to the intended turn.

*Call graph*: 1 external calls (replace).


##### `_AuthorizedSandbox.conversation_id`  (lines 1338–1339)

```
def conversation_id(self) -> UUID
```

**Purpose**: Returns the conversation id of the underlying lazy sandbox. Authorization wrapping does not change which workspace is reached.

**Data flow**: It reads late.conversation_id and returns it.

**Call relations**: This fulfills the Sandbox.conversation_id contract for authorized lazy views. It simply forwards to _LateSandbox.


##### `_AuthorizedSandbox.turn_id`  (lines 1342–1343)

```
def turn_id(self) -> UUID
```

**Purpose**: Returns the turn id of the underlying lazy sandbox. Authorization wrapping does not change turn ownership.

**Data flow**: It reads late.turn_id and returns it.

**Call relations**: This fulfills the Sandbox.turn_id contract for authorized lazy views and forwards to _LateSandbox.


##### `_AuthorizedSandbox.created`  (lines 1346–1347)

```
def created(self) -> bool
```

**Purpose**: Reports whether the underlying lazy sandbox has been opened. It does not force creation.

**Data flow**: It reads late.created and returns that boolean.

**Call relations**: This keeps authorized wrappers transparent for callers checking lazy state.


##### `_AuthorizedSandbox.authorize`  (lines 1349–1355)

```
def authorize(self, run_token: str, cleared_env: frozenset[str], env: Mapping[str, str]) -> Sandbox
```

**Purpose**: Applies a new authorization request by returning a fresh authorized view from the underlying lazy sandbox. The latest authorization replaces the wrapper's stored one.

**Data flow**: It receives a run token, cleared environment names, and environment values, then delegates to late.authorize and returns the resulting Sandbox.

**Call relations**: This prevents stacking multiple authorization wrappers. It hands the request back to _LateSandbox, which creates a clean _AuthorizedSandbox.


##### `_AuthorizedSandbox._bound`  (lines 1357–1358)

```
async def _bound(self) -> SandboxSession
```

**Purpose**: Binds the underlying lazy sandbox, then applies this wrapper's run token and environment changes to the live session. This is where delayed authorization takes effect.

**Data flow**: It awaits late._bound to get a SandboxSession, calls that session's authorize method with the stored token and environment edits, and returns the authorized session.

**Call relations**: All inherited Sandbox operations on an _AuthorizedSandbox flow through this method. It connects lazy creation from _LateSandbox with environment rewriting from SandboxSession.authorize.
