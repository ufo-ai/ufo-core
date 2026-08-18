# Sandbox containment, injected environment, and egress policy  `stage-18.5`

This stage is the sandbox’s safety layer. It sits behind the scenes whenever untrusted work is run, such as an agent action, connector call, or off-turn probe. Its job is to give that work only the space, secrets, and network reach it is allowed to have.

The containment file checks file paths before the sandbox uses them. If a model or connector asks for a path, it verifies that the path stays inside the approved directory. It also guards against symbolic links, which are shortcut files that might secretly point outside the sandbox.

The exec environment file prepares the environment variables for sandbox probes. Environment variables are small name-value settings passed into a process. Here they may include approved credential names, but only as safe placeholders, never real secret values.

The proxy rules file controls network exits. It gathers the model being used, extension manifests, credentials, grants, and artifact storage settings, then turns them into concrete proxy rules. Together, these parts work like locked doors, fake keys, and a guarded gate.

## Files in this stage

### Filesystem containment
Validates untrusted sandbox file paths so they remain inside approved directories and cannot escape through symlinks.

### `core/src/ufo/sandbox/containment.py`

`domain_logic` · `cross-cutting file access`

This file solves a serious security problem: a path that looks harmless, such as `output.txt`, can still be made dangerous if a sandboxed process replaces part of the path with a symbolic link to a host file. Without this guard, code that reads, copies, or writes files for an agent could accidentally touch files outside the sandbox.

The file uses several layers of checking, like checking both an address on an envelope and the actual locked mailbox before putting anything inside. First it rejects unusable relative names, such as empty names or `..`, which means “go up one directory.” Then it resolves the path’s real parent directory and confirms it is still inside the root. After that, it walks down each directory one piece at a time using operating-system file descriptors, which are stable references to already-open directories. This prevents a path from being swapped after it was checked. Finally, it checks the target file itself without following a final symbolic link.

The main public tool is `contained_file`, which returns a `ContainedFile`: a safe object for reading, deleting, changing permissions, or replacing a file through its pinned parent directory. The file also offers helpers for safe directories, glob patterns, leaf filenames, and purely lexical checks for paths that cannot be inspected yet.

#### Function details

##### `contained_root`  (lines 87–100)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Checks that a sandbox root really exists and is a directory, and refuses it if it is a symbolic link or not usable. This is used when the root itself might be under untrusted control.

**Data flow**: It receives a root path, turns it into a `Path`, inspects the path itself without following symbolic links, and rejects missing or non-directory roots. If the root is safe, it returns the root's resolved real location.

**Call relations**: Both `contained_file` and `contained_dir` call this before doing any deeper path checks. It gives them a trusted starting point so their later containment checks are based on a real directory, not a redirect.

*Call graph*: called by 2 (contained_dir, contained_file); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 103–120)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Checks a root directory that came from operator configuration, while allowing the configured root itself to be a symbolic link. This supports normal deployments where a configured storage path points to another mounted location.

**Data flow**: It receives a root path and the name of the setting it came from. It follows the path, confirms the final target exists and is a directory, and returns the resolved real directory; otherwise it raises an error that names the bad setting.

**Call relations**: This is a public entry point for configuration-time validation. Unlike `contained_root`, it is not called by the file-access helpers shown here because it serves the separate case where the deployer, not the sandboxed agent, chose the root.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 138–149)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Looks at the target file itself without following a final symbolic link. It answers whether a normal file exists at the safe name, and refuses directories or other special file types.

**Data flow**: It uses the already-pinned parent directory and the stored file name. If nothing exists, it returns `None`; if a regular file exists, it returns its file information; if a directory, symlink, or special file is there, it raises `NotRegularFile`.

**Call relations**: `contained_regular` uses this after `contained_file` has pinned the parent directory. This is the final target check that prevents a planted link from being treated as a readable file.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 151–166)

```
def mode(self, default: int) -> int
```

**Purpose**: Finds the permission bits to use when replacing a file. It preserves the existing file's permissions when there is a regular file, but falls back to a default when the name is missing or held by something like a symbolic link.

**Data flow**: It reads the target entry through the pinned parent directory. A missing target produces the supplied default mode, a regular file produces its current permission bits, and a directory is rejected because it cannot be safely replaced as a file.

**Call relations**: This method is meant for callers preparing a safe overwrite through a `ContainedFile`. It complements `replace_bytes`, which writes to a staged file and then renames it into place.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 168–178)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained target for streaming binary reads. This is useful for large files because the caller can read in chunks instead of loading the whole file into memory.

**Data flow**: It asks `_open_regular` to open the target safely as a regular file. It wraps the resulting low-level file descriptor in a Python binary file object, and closes the descriptor if wrapping fails.

**Call relations**: `ContainedFile.read_bytes` calls this for simple bounded reads. It delegates the safety-sensitive open operation to `_open_regular`, then hands the caller a normal readable file object.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 180–183)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a given number of bytes from the contained file. It is the simple “give me the contents, but not more than this limit” helper.

**Data flow**: It receives a byte limit, opens the file through `open_bytes`, reads at most that many bytes, closes the file automatically, and returns the bytes read.

**Call relations**: `ContainedFile.read_text` builds on this when text is wanted instead of raw bytes. This keeps both byte and text reads using the same safe open path.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 185–186)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads a contained file as UTF-8 text, replacing invalid characters instead of failing. It is for callers that want a text preview or text content with a maximum size.

**Data flow**: It receives a byte limit, gets bytes from `read_bytes`, decodes them as UTF-8, replaces any malformed byte sequences, and returns a string.

**Call relations**: This is a small convenience layer over `ContainedFile.read_bytes`. All path and file safety work has already happened lower down.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 188–189)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the permission bits on the contained target without following symbolic links. It lets callers adjust who can read or write a safe file.

**Data flow**: It receives a mode, keeps only the normal permission bits, and applies them to the target name relative to the pinned parent directory.

**Call relations**: Callers use this after obtaining a `ContainedFile` from `contained_file`. It relies on the pinned parent created by that flow so the permission change applies to the checked location.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 191–195)

```
def unlink(self) -> None
```

**Purpose**: Deletes the contained target if it exists. If the file is already gone, it quietly treats that as success.

**Data flow**: It asks the operating system to remove the stored file name from the pinned parent directory. A missing file is ignored; other filesystem errors still surface.

**Call relations**: This is an action available once `contained_file` has produced a safe target. It does not call other helpers because the safety context is already stored in the `ContainedFile`.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 197–199)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Moves another already-contained file onto this contained file's name. This is a safe rename between two targets whose parent directories have both been pinned.

**Data flow**: It receives another `ContainedFile` as the source. It tells the operating system to replace this target name with the source name, using each object's pinned parent directory.

**Call relations**: Callers use this when both source and destination have already gone through containment checks. It hands the actual move to `os.replace`, but supplies safe directory file descriptors for both sides.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 201–202)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Replaces the target file with text content. It is the text-friendly wrapper around the safer byte replacement routine.

**Data flow**: It receives a string and a permission mode, encodes the string to bytes, and passes those bytes to `replace_bytes`. The result is a new file installed at the contained target name.

**Call relations**: This function simply feeds `ContainedFile.replace_bytes`. That keeps text and binary writes using the same staged-write safety behavior.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 204–228)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely replaces the target file with new binary data. It writes to a temporary sibling file first, then atomically renames it into place so readers see either the old file or the complete new file, not a half-written one.

**Data flow**: It receives bytes and a permission mode. It creates a uniquely named staged file in the pinned parent directory without following symbolic links, writes the data, sets permissions, renames the staged file onto the target, and cleans up any leftover staged file if something fails.

**Call relations**: `ContainedFile.replace_text` calls this after encoding text. This is the core safe-write operation for a `ContainedFile`, using operating-system create, write, rename, and cleanup calls.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 230–246)

```
def _open_regular(self) -> int
```

**Purpose**: Opens the target for reading only if it is truly a regular file and not a symbolic link or special file. It is the low-level safety check behind streaming reads.

**Data flow**: It tries to open the target name through the pinned parent directory with symbolic-link following disabled. If the file is missing, it raises `PathNotFound`; if it is not a regular file, it closes anything it opened and raises `NotRegularFile`; otherwise it returns the open file descriptor.

**Call relations**: `ContainedFile.open_bytes` calls this before creating a higher-level Python file object. This keeps the dangerous part of opening the file in one carefully checked place.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 250–284)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main read/write doorway for a single file path under a sandbox root. It performs the full containment check and yields a `ContainedFile` whose parent directory is pinned open.

**Data flow**: It receives a path, a root, and an option to create missing parent directories. It validates the root, anchors the path under that root, rejects unusable target names, confirms the resolved parent stays inside the root, opens the root directory, descends through each parent component without following links, optionally creates missing directories, yields a `ContainedFile`, and finally closes the pinned directory descriptor.

**Call relations**: `contained_regular` calls this when it needs an existing safe file path. Inside, it calls `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend` to build the safety chain before constructing the `ContainedFile`.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_regular); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 287–313)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Checks that a directory path stays inside the sandbox root and can be reached without following unsafe directory links. It is used when code needs to enumerate or create directories safely.

**Data flow**: It receives a path, a root, and an option to create missing directories. It validates the root, resolves the target directory, confirms it is inside the root, opens the root, walks each directory component safely, optionally creates missing components, closes the final descriptor, and returns the resolved directory path.

**Call relations**: `contained_glob` calls this to decide where a file search should start. It shares the same root-opening and per-component descent helpers as `contained_file`, but returns a path rather than a pinned file object.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); called by 1 (contained_glob); 3 external calls (__init__, close, mkdir).


##### `contained_regular`  (lines 316–325)

```
def contained_regular(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> Path
```

**Purpose**: Returns the safe canonical path of an existing regular file under a root. It is for cases where another tool, such as a subprocess or library, only accepts a filename and cannot read from an already-open file descriptor.

**Data flow**: It receives a path and root, enters `contained_file` to perform the full containment check, calls `lstat` on the resulting target, raises `PathNotFound` if no file exists, and returns the checked path if a regular file is present.

**Call relations**: This function is built directly on `contained_file`. It adds the requirement that the target must already exist and be regular before handing a path to code outside this safety wrapper.

*Call graph*: calls 1 internal fn (contained_file); 1 external calls (__init__).


##### `contained_pattern`  (lines 328–345)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Checks and rewrites a glob pattern so it cannot search outside the root. A glob pattern is a filename pattern such as `*.txt` used to find matching files.

**Data flow**: It receives a pattern and root. It rejects patterns containing `..`, returns relative patterns unchanged, converts absolute patterns inside the root into root-relative patterns, and rejects absolute patterns outside the root or patterns that only name the root directory itself.

**Call relations**: `contained_glob` calls this after deciding the safe starting directory. Together they prevent a search pattern from silently re-rooting the search at the whole filesystem.

*Call graph*: called by 1 (contained_glob); 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_glob`  (lines 348–360)

```
def contained_glob(pattern: str, path: str | os.PathLike[str] | None, root: Path) -> tuple[Path, str]
```

**Purpose**: Prepares a safe directory-and-pattern pair for file enumeration. It decides where a glob search should begin and how the pattern should be interpreted under the root.

**Data flow**: It receives a pattern, an optional starting path, and a root. If the pattern is absolute or no start path is given, it starts at the root; otherwise it starts at the supplied path. It then checks the start directory with `contained_dir` and checks or rewrites the pattern with `contained_pattern`, returning both.

**Call relations**: This function coordinates `contained_dir` and `contained_pattern`. Callers that want to list matching files can use its result without each re-implementing the rules for absolute and relative patterns.

*Call graph*: calls 2 internal fn (contained_dir, contained_pattern); 1 external calls (PurePosixPath).


##### `contained_relative`  (lines 363–388)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs a path-only containment check when the current process cannot inspect the real filesystem yet. It proves that the written path text intends to stay under a root, but it does not prove anything about symbolic links on disk.

**Data flow**: It receives a path and a root string. It combines relative paths with the root, processes `.` and `..` parts in a purely textual way, rejects paths that escape or name the root itself, and returns the cleaned absolute path string.

**Call relations**: This helper stands alone for callers that need an early lexical check, such as persisted file keys or paths inside another container. The later actual write is still expected to go through stronger filesystem checks like those in `contained_file`.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 391–399)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Extracts one safe filename component from a name supplied by someone else. It drops any directory parts and uses a fallback name if nothing usable remains.

**Data flow**: It receives a raw name and a fallback. It treats backslashes like slashes, takes only the final filename piece, and returns that piece unless it is empty, `.`, or `..`; in those bad cases it returns the fallback.

**Call relations**: This is a public helper for inbound filenames from providers, browsers, or attachments. It only chooses a leaf name; callers still need to place it under a root and write through the containment guard.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 402–413)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Checks whether an already-enumerated path is a regular file inside a root without having crossed symbolic links. It is a fast filter for listing results, not the final safety step for reading.

**Data flow**: It receives a path and root. It first confirms the path itself is a regular file without following links, resolves the path strictly, and returns true only if the resolved path is the same path and lies inside the root; any filesystem error becomes false.

**Call relations**: This function uses `_inside` for the final root check. The file's own documentation expects actual reads of accepted results to still go through `contained_file`.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 416–422)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Anchors a possibly relative path under a specific root instead of the process's current working directory. This avoids asking safety questions about one path and then using another.

**Data flow**: It receives a path and a root. If the path is absolute, it returns it as a `Path`; if it is relative, it joins it to the root and returns that combined path.

**Call relations**: `contained_file` and `contained_dir` call this near the start of their checks. It gives both flows a consistent target path before containment rules are applied.

*Call graph*: called by 2 (contained_dir, contained_file); 1 external calls (Path).


##### `_inside`  (lines 425–426)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Answers the simple question: is this path the root itself or somewhere below it? It is the small shared containment predicate used after paths have been resolved.

**Data flow**: It receives a path and a root. It returns true when the path equals the root or the root appears among the path's parent directories, and false otherwise.

**Call relations**: `contained_file`, `contained_dir`, and `is_contained_regular` call this after resolving or checking a path. It supplies the common yes-or-no test for staying within the allowed tree.

*Call graph*: called by 3 (contained_dir, contained_file, is_contained_regular).


##### `_open_root`  (lines 429–433)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the root directory in a way that refuses symbolic links and non-directories. This creates the first stable directory reference for safe descent.

**Data flow**: It receives a root path and asks the operating system to open it with directory-only and no-symbolic-link flags. On success it returns a file descriptor; on failure it raises `NonDirectoryAncestor`.

**Call relations**: `contained_file` and `contained_dir` call this before walking into child directories. Its returned descriptor is then passed into `_descend` for component-by-component traversal.

*Call graph*: called by 2 (contained_dir, contained_file); 2 external calls (__init__, open).


##### `_descend`  (lines 436–449)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory level deeper from an already-open parent directory, without following symbolic links. It also closes the old parent descriptor so the walk does not leak open handles.

**Data flow**: It receives a parent directory file descriptor, the next path component, and the full target path for error messages. It opens the child as a directory with symbolic-link following disabled, turns missing or non-directory cases into containment-specific errors, closes the old descriptor, and returns the child descriptor.

**Call relations**: `contained_file` and `contained_dir` call this repeatedly while walking from the root to the target's parent or directory. It is the step that pins each directory in turn and prevents path swaps from redirecting the later operation.

*Call graph*: called by 2 (contained_dir, contained_file); 5 external calls (__init__, __init__, __init__, close, open).


### Injected probe environment
Constructs the safe environment variables exposed to off-turn sandbox probes, including credential placeholders without real secrets.

### `core/src/ufo/sandbox/exec_env.py`

`domain_logic` · `sandbox open for off-turn probes`

A sandbox is a controlled place where commands can run. Those commands may still need access to things like Git repositories, provider APIs, or connector command-line tools. This file prepares the “labelled envelopes” the sandbox needs: environment variables that contain sentinels, which are harmless placeholder strings. Later, an egress proxy, meaning the network gate that watches outgoing requests, swaps those sentinels for real credentials only when a permitted request leaves the sandbox.

The central piece is `ProbeEnv`. It gathers four kinds of information. First, it adds the conversation id so code inside the sandbox knows which conversation it belongs to. Second, it prepares Git configuration in Git’s special environment-variable format, including proxy authentication and per-host credential headers when the workspace has a matching credential. Third, it exports connector CLI credentials, choosing the acting member’s own connection first and a shared workspace connection second. Fourth, it exports keyed provider variables, such as an API key variable and provider host variable, but only when the workspace actually has that credential set.

The important safety rule is that this file does not copy secrets into the sandbox. It only exports sentinels and resolved host names. If a credential slot is missing, broken, or ambiguous, the file skips that export and logs or warns, so the sandbox fails visibly instead of accidentally using the wrong account.

#### Function details

##### `ProbeEnv.exports`  (lines 60–74)

```
async def exports(self, conversation_id: UUID, probe_id: UUID, acting_member_id: UUID | None=None) -> dict[str, str]
```

**Purpose**: Builds the complete environment-variable dictionary for a probe running inside a sandbox. It combines conversation identity, Git settings, connector CLI placeholders, and keyed provider placeholders into one exportable package.

**Data flow**: It receives a conversation id, a probe id, and optionally the member the probe is acting as. It reads the current workspace id, then asks helper functions to prepare Git config, Git credential headers, connector CLI sentinels, and keyed provider variables. It returns one dictionary whose keys and values can be added to the sandbox process environment.

**Call relations**: This is the main entry point in the file for code that opens a probe sandbox. It calls `_git_config_env` to translate Git settings into environment variables, `_git_credential_config` to discover safe Git credential headers, `_grant_cli_env` to choose connector account sentinels, and `_keyed_provider_env` to expose provider placeholders when a credential exists.

*Call graph*: calls 4 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env); 1 external calls (ws_current).


##### `_git_config_env`  (lines 77–84)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Turns Git configuration settings into the special environment-variable format that Git understands. This matters because the sandbox can configure Git without writing a Git config file.

**Data flow**: It receives a tuple of Git setting pairs, where each pair is a setting name and value. It creates `GIT_CONFIG_COUNT` and numbered `GIT_CONFIG_KEY_n` / `GIT_CONFIG_VALUE_n` entries. It returns those entries as a dictionary ready to merge into the sandbox environment.

**Call relations**: `ProbeEnv.exports` calls this after collecting fixed proxy settings and any credential-related Git settings. This helper does not decide what Git should be configured with; it only converts already-chosen settings into Git’s required environment format.

*Call graph*: called by 1 (exports).


##### `_git_credential_config`  (lines 87–122)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Finds which Git hosts should receive credential placeholders and prepares Git header settings for them. It only includes a host when the workspace really has the matching credential set.

**Data flow**: It receives the credential store, declared credential slots, and workspace id. For each slot, it checks whether the slot is meant for Git basic authentication, whether a credential is stored, and which host should be used. Successful checks become Git `extraheader` settings containing a sentinel instead of a real secret. If credentials are missing, unavailable, or erroring, it skips that slot and may write a warning. It returns a tuple of Git setting pairs.

**Call relations**: `ProbeEnv.exports` calls this before `_git_config_env`, because these settings must be folded into Git’s environment-variable format. Internally it relies on `slot_is_set` to avoid exporting unusable placeholders, `credential_host` to resolve the correct host, and `warn` to report credential-slot problems without stopping the whole sandbox open.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 125–167)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Exports environment variables for provider credentials, such as API-key variables and provider host variables, using sentinels instead of real keys. It avoids creating half-working variables when the workspace has not actually configured that provider.

**Data flow**: It receives the credential store, declared credential slots, and workspace id. It walks through slots that declare an environment variable or host variable, checks that the credential exists, resolves the provider host, and then adds the sentinel and host name to an environment dictionary. If a slot cannot be checked or its host cannot be resolved, it warns and skips it. It returns the environment variables that are safe to give to the sandbox.

**Call relations**: `ProbeEnv.exports` calls this as one part of building the final sandbox environment. Like `_git_credential_config`, it uses `slot_is_set` and `credential_host` to export only usable credential placeholders, and uses `warn` so failures are visible but do not prevent other valid exports.

*Call graph*: called by 1 (exports); 3 external calls (credential_host, slot_is_set, warn).


##### `_grant_cli_env`  (lines 170–212)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], acting_member_id: UUID | None, run_id: UUID) -> dict[str, str]
```

**Purpose**: Chooses connector command-line credential placeholders for tools that run inside the sandbox. It prefers the acting member’s private connection, and falls back to a shared workspace connection when there is no private one.

**Data flow**: It receives the grant store, known connector CLI definitions, the acting member id, and the run id. It asks the grant store for active grants, then for each provider looks for matching private grants owned by the acting member, or shared grants if no private one exists. If exactly one account is suitable, it sets the connector’s environment variable to that account’s sentinel. If more than one account is equally suitable, it logs the ambiguity and exports nothing for that provider. It returns the selected CLI environment variables.

**Call relations**: `ProbeEnv.exports` calls this while assembling the probe environment. This helper gets the current grants from `GrantStore.active_grants`, turns a chosen account id into a safe placeholder with `grant_sentinel`, and uses `log` when it refuses to guess between multiple possible accounts.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (exports); 2 external calls (grant_sentinel, log).


### Network egress policy
Translates model, manifest, credential, grant, and artifact-storage state into concrete proxy rules for sandboxed network access.

### `core/src/ufo/sandbox/proxy/rules.py`

`domain_logic` · `turn setup and request handling`

A sandbox should not be able to call any website or see any raw secret by default. This file builds the proxy’s rulebook for each run: which hosts may be reached, which requests should be counted for billing or spending, which fake placeholder secrets should be replaced with real ones, and which requests should be forwarded through a broker instead of sent directly.

Think of it like preparing a temporary visitor badge. The badge says exactly which doors the visitor may open, whether each door visit is logged, and whether a receptionist must swap a fake passcode for a real one at the last moment. The sandbox sees only safe placeholder values, called sentinels. When a request leaves through the proxy, an injection rule can replace that sentinel with the real credential outside the sandbox, so the secret never lives inside the container.

The file also treats different sources of access differently. Model providers get access based on the model name. Extensions can request public internet for live turns. S3 artifact sharing gets narrowly scoped access to only the storage host. Credential slots open access only when a secret and a valid host can be resolved. Grants allow connector hosts and file-transfer hosts, but usually do not inject secrets because the broker owns and uses those server-side.

#### Function details

##### `provider_host`  (lines 108–112)

```
def provider_host(model: str) -> str
```

**Purpose**: This function finds which model provider host should be used for a model name. For example, model names starting with OpenAI-style prefixes map to OpenAI’s API host, while Claude-style names map to Anthropic’s host.

**Data flow**: It receives a model name as text. It checks the known model-name prefixes in order, and when one matches, it returns the matching API host. If no prefix matches, it raises an error because the proxy cannot safely guess where that model should connect.

**Call relations**: It is used by derive_model_rules when building the rules for model traffic. That caller needs a precise host before it can allow network access, inject the model API key, and meter usage.

*Call graph*: called by 1 (derive_model_rules).


##### `derive_model_rules`  (lines 115–130)

```
def derive_model_rules(model: str, real_key: str) -> tuple[Rule, ...]
```

**Purpose**: This function builds the proxy rules needed for the sandbox to call the selected language model provider. It allows only the provider host, swaps the sandbox’s fake model key for the real key on outgoing requests, and marks model traffic for token metering.

**Data flow**: It receives a model name and the real provider API key. It first asks provider_host which API host belongs to the model, then chooses the right authorization header format for that provider. It returns a small set of rules: one allowing the host, one replacing the sentinel key with the real key, and one saying this host should be metered as token usage.

**Call relations**: This is part of assembling the sandbox’s network rulebook before model calls happen. It calls provider_host to identify the destination, then creates ScopeRule, InjectionRule, and MeterRule values that the egress proxy later reads when traffic leaves the sandbox.

*Call graph*: calls 1 internal fn (provider_host); 3 external calls (__init__, __init__, __init__).


##### `derive_manifest_rules`  (lines 133–135)

```
def derive_manifest_rules(manifests: tuple[Manifest, ...]) -> tuple[InternetRule, ...]
```

**Purpose**: This function checks whether any installed extension says the sandbox needs internet access. If so, it adds the rule that permits public internet during live turns.

**Data flow**: It receives the extension manifests for the deploy. It looks for any manifest with sandbox internet enabled. If it finds one, it returns an InternetRule; otherwise it returns no rules.

**Call relations**: This contributes one piece to the larger proxy policy built for a run. The InternetRule it creates is later interpreted by the proxy as permission for broader public network access, instead of only exact allowlisted hosts.

*Call graph*: 1 external calls (__init__).


##### `derive_artifact_store_rules`  (lines 138–154)

```
async def derive_artifact_store_rules(blob: FilesystemBlobStore | S3BlobStore) -> tuple[Rule, ...]
```

**Purpose**: This function allows the sandbox to upload shared files when the artifact store is backed by S3. Without it, a produced file using a presigned S3 URL could be blocked before it leaves the sandbox.

**Data flow**: It receives the blob store used for artifacts. If the store is S3, it asks the store for the host used for uploads, then returns rules allowing that exact host and metering each request to it. If the store is local filesystem storage, it returns no network rules because no network host is needed.

**Call relations**: This runs while composing the egress rules for a deployment or turn. It creates ScopeRule and MeterRule values for S3-backed sharing, and it depends on the blob store’s put_host method to identify the exact destination.

*Call graph*: 3 external calls (__init__, __init__, put_host).


##### `derive_credential_rules`  (lines 157–220)

```
async def derive_credential_rules(slots: tuple[CredentialSlot, ...], workspace_id: UUID, store: CredentialStore) -> tuple[Rule, ...]
```

**Purpose**: This function turns declared credential slots into safe proxy rules. For each available credential, it allows the selected host, replaces the sandbox’s sentinel value with the real secret on the way out, and optionally meters requests to that host.

**Data flow**: It receives credential slot declarations, a workspace ID, and the credential store. For each slot that declares network injection, it tries to fetch the real secret and resolve the host for that workspace. If either is missing or fails, it logs a warning and skips only that slot. For Git basic authentication, it builds the required Basic header value from a username and secret. It groups rules by host, then returns host allow rules, injection rules, and any metering rules.

**Call relations**: This is called while building a workspace-specific proxy policy. It calls slot_secret and credential_host to resolve secrets and destinations, uses b64encode when a Git Basic header must be assembled, creates InjectionRule, ScopeRule, and MeterRule values, and calls warn when an individual credential slot cannot be used. Its important behavior is that one bad credential does not break the entire turn.

*Call graph*: 7 external calls (__init__, __init__, __init__, b64encode, credential_host, slot_secret, warn).


##### `derive_grant_rules`  (lines 223–240)

```
def derive_grant_rules(grants: tuple[Grant, ...], transfer_hosts: 'ConnectorTransferHosts | None'=None) -> tuple[Rule, ...]
```

**Purpose**: This function turns connector grants into network access for the connector’s provider host and any related file-transfer hosts. It meters those requests, but does not inject credentials, because granted connector credentials are held and used by the broker server-side.

**Data flow**: It receives the active grants and, optionally, a lookup object for connector transfer hosts. For each grant, it gathers the grant’s own provider host plus any extra transfer hosts, removes empty and duplicate entries, and returns rules that allow those hosts and meter requests to each one.

**Call relations**: This contributes grant-based access to the proxy rulebook. When transfer host information is available, it asks ConnectorTransferHosts.of for the extra hosts tied to a grant’s provider, then creates ScopeRule and MeterRule values for the proxy to enforce.

*Call graph*: 2 external calls (__init__, __init__).


##### `derive_cli_rules`  (lines 243–263)

```
def derive_cli_rules(grants: tuple[Grant, ...], acting_member_id: UUID | None, clis: Mapping[str, CliCredential]) -> tuple[Rule, ...]
```

**Purpose**: This function creates forwarding rules for connector command-line credentials. These rules let eligible requests carrying a grant sentinel be executed through the broker, so the real account token never appears in this deploy or inside the sandbox.

**Data flow**: It receives active grants, the acting member ID if there is one, and a map of connector CLI credential definitions. It keeps only grants whose provider has a CLI credential and whose account the acting member may use: either a shared connection or their own grant. For each eligible grant, it returns a ForwardRule containing the host, header, sentinel, account ID, and broker forwarding behavior.

**Call relations**: This fits beside the grant rules. derive_grant_rules allows and meters the host, while derive_cli_rules describes the special path for authenticated CLI-style requests. It calls grant_sentinel to know what placeholder value should trigger forwarding, then creates ForwardRule values for the proxy.

*Call graph*: 2 external calls (__init__, grant_sentinel).


##### `ConnectorTransferHosts.of`  (lines 277–278)

```
def of(self, provider: str) -> tuple[str, ...]
```

**Purpose**: This method looks up which broker file-transfer hosts belong to a connector provider. If the provider was explicitly registered, it uses that provider’s declared hosts; otherwise it falls back to the default open-namespace hosts.

**Data flow**: It receives a provider name. It checks the explicit provider-to-hosts mapping first. If there is no entry for that provider, it returns the default tuple of hosts.

**Call relations**: derive_grant_rules uses this method when adding file-transfer access for grants. It keeps the grant-rule code simple by hiding the distinction between registered connectors and providers served through the open connector namespace.


##### `connector_transfer_hosts`  (lines 281–292)

```
def connector_transfer_hosts(manifests: tuple[Manifest, ...]) -> ConnectorTransferHosts
```

**Purpose**: This function builds the lookup table used to decide which file-transfer hosts connector grants should allow. It reads the deploy’s manifests so the proxy stays aligned with the connectors that are actually registered.

**Data flow**: It receives extension manifests. It walks through every connector declared by those manifests and records each connector’s provider name with its declared transfer hosts. It also asks for the open connector namespace, if any, and uses that namespace’s transfer hosts as the default for providers not explicitly registered. It returns a ConnectorTransferHosts object containing both pieces.

**Call relations**: This is usually run before grant rules are derived. It calls open_connector_namespace to find the fallback namespace, constructs ConnectorTransferHosts, and hands that object to derive_grant_rules so grants can include the correct broker file-store hosts.

*Call graph*: 2 external calls (__init__, open_connector_namespace).
