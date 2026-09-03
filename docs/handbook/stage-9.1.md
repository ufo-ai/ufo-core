# Tool contract, context, and long-running task journals  `stage-9.1`

This stage is shared support for the system’s tool use. It sets the rules for how the main runtime, sandbox, and individual tools talk to each other, and it keeps that work safe while the system is running. The bridge contract defines the “wire format,” meaning the exact shape of messages sent between the runtime and the sandbox, plus the tool names that are allowed. The registry is the catalog: it names tools, checks that their descriptions are valid, and finds the right tool when the model asks for one.

The tool context is the controlled doorway every tool receives. Through it, a tool can access files, connected accounts, child agents, shared artifacts, and paid media metering, but only with the permissions it has been given. Containment adds a guardrail around file paths so untrusted input cannot escape the intended folder, even with tricks like symlinks. File-change limits keep path sizes consistent. Task journals let long shell commands keep running after the caller stops waiting. The package files simply mark and describe these tool areas.

## Files in this stage

### Bridge and path contracts
These files define the outer safety contracts for bridge tool traffic and untrusted filesystem paths.

### `core/src/ufo/runtime/tools/bridge.py`

`io_transport` · `live-run tool bridge setup and request handling`

The tool bridge is a controlled doorway between a live run and tools that live outside the immediate sandbox. Without this file, different parts of the system could disagree about request shapes, tool names, or error formats, which would make tool calls fragile and unsafe.

The file first names the bridge host and URL, then defines the exact set of bridge tool names that are allowed. This is like a front desk with a fixed menu: callers can only ask for services on the approved list.

Most of the file is made of small Pydantic models. Pydantic is a validation library that checks incoming data before the program trusts it. `ToolBridgeRequest` describes a caller asking to list tools, get one tool’s schema, or execute a tool. `ToolBridgeSuccess` and `ToolBridgeFailure` describe the two possible reply shapes. Other models describe listed tools and bridge intents.

The `ToolBridgeRequester` protocol describes an object that knows how to send one of these requests under a specific live run identity. Finally, `bridge_tools` builds the actual callable set exposed by the bridge: built-in object tools plus selected unbound tools from manifests and connectors. It also validates the final tool collection by constructing a `ToolRegistry`, so bad or conflicting definitions are caught early.

#### Function details

##### `ToolBridgeRequest._matches_action`  (lines 52–58)

```
def _matches_action(self) -> 'ToolBridgeRequest'
```

**Purpose**: This checks that a bridge request makes sense for the action it claims to perform. A request to list tools must not include a specific tool name or arguments, while requests to inspect or execute a tool must name the tool.

**Data flow**: It starts with a `ToolBridgeRequest` object whose fields have already been parsed. It looks at the `action`, `tool_name`, and `arguments` fields. If the combination is invalid, it raises an error so the bad request is rejected; if the combination is valid, it returns the same request object unchanged.

**Call relations**: This validator runs as part of Pydantic’s model validation whenever a `ToolBridgeRequest` is created. It protects later bridge code, including anything implementing `ToolBridgeRequester.request`, from having to deal with impossible request shapes such as “execute” without a tool name or “list” with stray arguments.


##### `ToolBridgeRequester.request`  (lines 92–92)

```
async def request(self, run: RunToken, request: ToolBridgeRequest) -> ToolBridgeResponse
```

**Purpose**: This is an interface promise: any bridge requester must provide an asynchronous method that sends one validated bridge request for one live run and returns either success or failure. It does not implement the work here; it defines what other code can rely on.

**Data flow**: It receives a `RunToken`, which represents the signed identity of the live run, and a `ToolBridgeRequest`, which says what the caller wants. An implementation is expected to send or perform that request and produce a `ToolBridgeResponse`, either carrying a result or an error message.

**Call relations**: Other parts of the runtime can depend on this protocol without caring which concrete requester is used underneath. The request object it receives is shaped and checked by `ToolBridgeRequest`, and the response must follow the success-or-failure models defined in this same file.


##### `bridge_tools`  (lines 95–111)

```
def bridge_tools(manifests: tuple[Manifest, ...]) -> tuple[ToolDef, ...]
```

**Purpose**: This builds the exact list of tool definitions that the bridge is allowed to expose. It combines built-in object tools with approved external gateway tools from manifests, while deliberately excluding bound tools that must be reached through `object_action` instead.

**Data flow**: It receives a tuple of manifests, where each manifest can declare tools and connectors with their own tools. It first creates the built-in object tools from `ObjectVerbs({}).tools()`. Then it scans every manifest and connector tool, keeping only tools that are unbound and whose names are in the bridge’s approved name set. It combines those tools, constructs a `ToolRegistry` to validate the collection, and returns the tools as a tuple.

**Call relations**: This function is used when the bridge’s callable menu is being assembled. It calls `ObjectVerbs.__init__` to get the standard object-related actions, then calls `ToolRegistry.__init__` as a sanity check for the final set. The returned tools are what later bridge listing, schema, and execution flows can advertise or call.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/harness/containment.py`

`domain_logic` · `cross-cutting file access`

This module solves a dangerous file-access problem: a name like `report.txt` may look harmless, but the filesystem can redirect it through symbolic links, which are special files that point somewhere else. Without this guard, an agent could plant a link inside a writable workspace and make the system read or overwrite a file outside that workspace.

The file treats path safety like passing through several locked doors. First it checks the text of the path, refusing empty names and attempts to climb upward with `..`. Then it resolves the path’s real parent directory and confirms that location is still under the allowed root. Next it walks down each directory one piece at a time using directory file descriptors, which are operating-system handles to already-open directories. This “pins” the parent directory, so a later rename or symlink swap cannot silently redirect the final operation. Finally, when opening the target file, it refuses to follow a symlink at the target name itself.

The `ContainedFile` object represents a file that has passed these checks. Its read, write, rename, chmod, and delete helpers operate relative to the pinned parent directory, not by trusting a free-floating string path. The module also offers lighter lexical helpers for cases where this process cannot inspect the real filesystem yet, such as names that will be written later or inside a container.

#### Function details

##### `contained_root`  (lines 88–101)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Turns a configured root directory into its real, canonical path, but refuses the root if it is missing, not a directory, or itself a symlink. This is used when the root may be reachable by untrusted code, because a symlinked root could redirect every supposedly safe path.

**Data flow**: It receives a root path. It checks the path itself without following a symlink, verifies that it is a directory, then returns the resolved real path. If the path is absent or unsuitable, it raises a containment-specific error instead of returning a usable root.

**Call relations**: The main file, directory, and removal entry points call this first. It supplies the trusted starting point before those flows check whether a target path stays inside the root.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 104–121)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Turns an operator-provided root setting into a canonical directory path, allowing the root itself to be a symlink. This supports normal deployment layouts where, for example, a configured storage directory points to a mounted disk.

**Data flow**: It receives a root path and the name of the configuration setting that supplied it. It follows the path, verifies that the final target exists and is a directory, then returns the resolved path. If not, it raises an error message that names the setting to fix.

**Call relations**: This helper is not called by the other functions shown here. It exists for configuration-loading code that needs root validation but should allow an operator-chosen symlink.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 139–150)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Checks what currently exists at the contained file’s name without following a final symlink. It is useful when a caller needs to know whether the target is a normal file, absent, or unsafe to treat as a file.

**Data flow**: It reads the file entry named by `name` relative to the pinned parent directory. If nothing is there, it returns `None`; if a regular file is there, it returns its filesystem information; if a directory or other non-file is there, it raises an error.

**Call relations**: This method is used after `contained_file` has produced a `ContainedFile`. It relies on the pinned parent directory created by that earlier containment flow, so the check and the later operation refer to the same directory.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 152–167)

```
def mode(self, default: int) -> int
```

**Purpose**: Chooses the permission bits to use when replacing a file. It preserves permissions from an existing regular file, but falls back to a caller-provided default when the target is missing or is a final symlink.

**Data flow**: It looks at the target name relative to the pinned parent directory without following symlinks. If there is a regular file, it returns that file’s permission bits; if there is no file or a non-regular non-directory entry, it returns the default; if there is a directory, it raises an error.

**Call relations**: Callers use this on a `ContainedFile` before writing replacement bytes. It supports the safe replacement flow by deciding permissions without letting a symlink steer where bytes are written.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 169–179)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained file for streaming binary reads. This is for large files or copy-out operations where reading the whole file into memory at once would be wasteful.

**Data flow**: It asks `_open_regular` to open the target safely as a real file. It wraps the resulting low-level file descriptor in a buffered binary reader and returns that reader; if wrapping fails, it closes the descriptor before re-raising the error.

**Call relations**: It is called by `ContainedFile.read_bytes` for simple bounded reads. It hands off the risky opening step to `_open_regular`, which performs the final no-symlink and regular-file checks.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 181–184)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a caller-specified number of bytes from a contained file. It gives callers a simple safe read operation without exposing the lower-level file-opening details.

**Data flow**: It receives a byte limit. It opens the file through `open_bytes`, reads at most that many bytes from the returned stream, closes the stream automatically, and returns the bytes.

**Call relations**: It sits above `open_bytes` as the convenient read helper. `ContainedFile.read_text` calls it when the caller wants decoded text instead of raw bytes.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 186–187)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads a contained file as UTF-8 text, replacing invalid characters instead of failing. This is useful for previews, logs, or other human-facing text reads.

**Data flow**: It receives a byte limit. It calls `read_bytes` to get raw data, decodes those bytes as UTF-8, replaces undecodable sequences, and returns a string.

**Call relations**: It is the text-focused wrapper around `read_bytes`. The actual containment and safe opening work has already been done below it.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 189–190)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the permission bits on the contained file without following a final symlink. This lets callers update file permissions while staying within the already-pinned directory.

**Data flow**: It receives a mode value, keeps only the ordinary permission bits, and applies them to the target name relative to the pinned parent directory. It changes the filesystem entry and returns nothing.

**Call relations**: Callers use this on a `ContainedFile` yielded by `contained_file`. It depends on that earlier setup for safe relative addressing.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 192–196)

```
def unlink(self) -> None
```

**Purpose**: Deletes the contained file name if it exists. It quietly does nothing if the file is already gone, which makes cleanup safe to call repeatedly.

**Data flow**: It tries to remove the target name relative to the pinned parent directory. If removal succeeds, the name disappears; if the name is missing, the method ignores that and returns.

**Call relations**: This method is a small deletion helper for code already holding a `ContainedFile`. Broader path validation and parent pinning happen before it, in `contained_file`.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 198–200)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Renames one already-contained file onto another already-contained target. This is useful for moving staged or prepared content into its final safe location.

**Data flow**: It receives another `ContainedFile` as the source. It asks the operating system to replace this target’s name with the source name, using each file’s pinned parent directory, and returns nothing.

**Call relations**: It connects two containment-checked file handles. Because both sides are addressed through pinned parent directories, the rename does not depend on reinterpreting unsafe path strings.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 202–203)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Replaces the contained file with text. It is the text version of the safer byte-replacement operation.

**Data flow**: It receives a string and a permission mode. It encodes the string to bytes, passes those bytes to `replace_bytes`, and lets that method perform the staged write and rename.

**Call relations**: It is a convenience wrapper over `replace_bytes`. All important write-safety behavior is delegated to that lower-level method.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 205–229)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely replaces the contained file with new bytes in one staged operation. It avoids writing through symlinks and avoids leaving readers with a half-written file.

**Data flow**: It receives bytes and a permission mode. It creates a random hidden sibling file using exclusive creation and no symlink following, writes the bytes there, sets its permissions, then atomically renames that staged file onto the target name. On errors, it closes any open descriptor and removes the staged file if it still exists.

**Call relations**: It is called by `replace_text` and by any caller that wants binary replacement. It uses the pinned parent directory from `contained_file`, so the staged name and final name live beside each other in the already-approved directory.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 231–247)

```
def _open_regular(self) -> int
```

**Purpose**: Performs the strict low-level open used for safe reads. It refuses missing files, final symlinks, directories, and other non-regular filesystem objects.

**Data flow**: It tries to open the target name relative to the pinned parent directory with flags that refuse symlinks. It then checks the opened file descriptor itself to confirm it is a regular file. If the check passes, it returns the descriptor; otherwise it closes it and raises an appropriate error.

**Call relations**: This is the private safety step beneath `open_bytes`. `open_bytes` turns the descriptor it returns into a Python file object for callers to read.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 251–285)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main entry point for safely reading or writing one file under a root directory. It performs all containment checks and yields a `ContainedFile` whose parent directory is pinned open.

**Data flow**: It receives a path, a root, and an option to create missing parent directories. It validates the root, combines relative paths with that root, rejects unusable target names, resolves and confirms the parent stays inside the root, opens the root directory, walks each parent component safely, optionally creating directories as it goes, then yields a `ContainedFile`. When the caller leaves the context, it closes the pinned directory descriptor.

**Call relations**: This function ties together `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. Callers enter this context when they need a safe file object; methods on the yielded `ContainedFile` then do the actual reading, writing, renaming, or permission changes.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 288–314)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Safely resolves a directory under a root for enumeration or walking. It proves each directory component is really a directory and not a symlink swap before returning the canonical path.

**Data flow**: It receives a path, a root, and an option to create missing directories. It validates the root, resolves the requested directory, confirms it stays inside the root, opens the root, descends through each component without following symlinks, optionally creates missing components, closes the descriptor, and returns the resolved directory path.

**Call relations**: It uses the same building blocks as `contained_file`: `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. Unlike `contained_file`, it does not yield a long-lived file handle, because the caller wants a checked directory path to enumerate.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 3 external calls (__init__, close, mkdir).


##### `contained_remove`  (lines 317–344)

```
def contained_remove(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> None
```

**Purpose**: Safely removes one file or directory tree under a root without following symlinks along the way. It is the delete counterpart to the safe read and write entry points.

**Data flow**: It receives a path and root. It validates the root, rejects unusable target names, confirms the resolved parent is inside the root, safely descends to the parent, and then checks the target itself without following symlinks. If the target is a directory, it removes the directory tree only if the platform’s recursive removal is symlink-safe; otherwise it removes the single file-like entry. Missing paths are treated as already removed.

**Call relations**: It shares the containment setup flow used by `contained_file`: `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. After reaching the pinned parent, it performs the actual deletion itself.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 8 external calls (__init__, __init__, __init__, close, stat, unlink, rmtree, S_ISDIR).


##### `contained_pattern`  (lines 347–364)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Checks and rewrites a glob pattern so it cannot make a listing escape the root. A glob pattern is a search pattern such as `*.txt`; absolute patterns are especially dangerous because they can restart the search from the filesystem root.

**Data flow**: It receives a pattern string and a root path. It refuses any pattern containing `..`; if the pattern is relative, it returns it unchanged. If the pattern is absolute, it confirms the absolute location is inside the root, rewrites it as a root-relative pattern, and refuses a pattern that names only the root directory itself.

**Call relations**: This is a standalone lexical guard for enumeration code. It does not open files; it prepares safe pattern text that later listing code can use.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_relative`  (lines 367–392)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs a text-only containment check for a path under a root. It is for cases where this process cannot inspect the real filesystem yet, such as a path inside a container or a file key saved for later.

**Data flow**: It receives a path string and a root string. It combines relative paths with the root, processes `.` and `..` parts as text, refuses attempts to climb above the root, refuses a path that names the root itself, and returns the cleaned absolute-looking path string under the root.

**Call relations**: This helper is separate from the filesystem-opening functions. It supplies the first, lexical layer of safety for callers that must later hand the path to another component that will do the stronger filesystem checks.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 395–403)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Extracts one safe filename component from a name supplied by an outside system. It drops any directory parts, including Windows-style backslash parts, so only the final leaf name remains.

**Data flow**: It receives a raw name and a fallback name. It normalizes backslashes to slashes, takes only the final filename component, and returns the fallback if that component is empty, `.`, or `..`; otherwise it returns the leaf.

**Call relations**: This is a lightweight helper for inbound names such as attachment filenames. It does not prove full containment by itself; callers still join the returned leaf under a root and write through the main guard.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 406–417)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Answers whether an already-enumerated path is a real regular file inside a root, with no symlink crossed to reach it. It is meant as a listing filter, not as permission to open the file directly.

**Data flow**: It receives a path and root. It checks that the path itself is a regular file without following symlinks, resolves the path strictly, and returns true only if the resolved path equals the original path and lies inside the root. If any filesystem check fails, it returns false.

**Call relations**: Enumeration code can use this to decide which paths to show. A later read of a matching path should still go through `contained_file`, which performs the stronger pinned-directory open.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 420–426)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Combines a caller’s path with the intended root in a predictable way. It prevents relative paths from being interpreted against the process’s current working directory by accident.

**Data flow**: It receives a path and a root. If the path is already absolute, it returns it as a `Path`; otherwise it returns the path joined underneath the root.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this near the start of their flows. It makes sure all later checks talk about the same target path.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 1 external calls (Path).


##### `_inside`  (lines 429–430)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Checks whether one path is the root itself or lies somewhere below it. This is the simple containment test used after paths have been resolved into canonical form.

**Data flow**: It receives a path and a root. It returns true if the path equals the root or if the root appears among the path’s parent directories; otherwise it returns false.

**Call relations**: `contained_file`, `contained_dir`, `contained_remove`, and `is_contained_regular` use this as their inside-the-root decision. It is deliberately small because the more subtle symlink safety happens before and after this check.

*Call graph*: called by 4 (contained_dir, contained_file, contained_remove, is_contained_regular).


##### `_open_root`  (lines 433–437)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the root directory as a directory file descriptor. This creates the pinned starting point for safe component-by-component descent.

**Data flow**: It receives a root path. It asks the operating system to open it as a directory without following symlinks, returning the resulting descriptor. If it cannot be opened that way, it raises an error saying the root is not an openable directory.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this after validating the root and target location. `_descend` then uses the returned descriptor to walk deeper.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 2 external calls (__init__, open).


##### `_descend`  (lines 440–453)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory component deeper from an already-open parent directory, refusing symlinks and non-directories. It is the core step that keeps path checks tied to real filesystem objects instead of changeable strings.

**Data flow**: It receives an open directory descriptor, the next path component, and the full target path for error messages. It opens the child component as a directory without following symlinks, closes the old parent descriptor, and returns the child descriptor. If the child is missing, a symlink, or not a directory, it raises a containment error.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this repeatedly while walking from the root to a target’s parent or directory. Each call advances the pinned handle one safe step down the tree.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 5 external calls (__init__, __init__, __init__, close, open).


### Tool package landmarks
These package markers orient readers to the host-side and runtime-side tool modules.

### `core/src/ufo/host/tools/__init__.py`

`other` · `import time`

This file does not contain working code. Its job is to label the surrounding folder as the place where the project’s tool system lives. In Python, an `__init__.py` file is what makes a folder importable as a package, meaning other code can refer to it as one named module. Here, the file also includes a short note explaining the package’s purpose: it contains the registry that keeps track of available tools, the context passed to tool handlers, and the built-in set of tools provided by the project. Think of it like a sign on a toolbox drawer. The sign does not use the tools itself, but it tells a newcomer what kinds of things are stored inside. Without this file, depending on the Python packaging setup, imports involving `ufo.host.tools` could be less clear or fail in older packaging styles, and readers would lose a simple entry point for understanding this part of the codebase.


### `core/src/ufo/runtime/tools/__init__.py`

`other` · `import time`

This file does not define any working code itself. Its main job is to label the surrounding folder as a Python package, which means other code can import modules from `ufo.runtime.tools`. The short text inside is a package-level note: it tells a reader that this part of the project is about the “tool contract,” meaning the shared rules for what a tool is and how it should be called; the “dispatch context,” meaning the information available when the system sends work to a tool; and the “wire registry,” meaning the mapping used when tools need to be exposed or connected across a boundary such as an API or message format. An everyday analogy is a sign on a filing cabinet drawer: the sign does not contain the files, but it tells you what kind of documents should be inside. Without this file, the package would lose that small piece of documentation, and in some Python packaging setups imports from this directory could become less clear or fail depending on how the project is arranged.


### Execution context limits
These files define the controlled context tools run with and the shared limit used for file-change paths.

### `core/src/ufo/runtime/tools/context.py`

`data_model` · `tool execution`

A tool should not be able to freely reach into the whole system. This file solves that by packaging only the powers a tool is allowed to use into one object, ToolContext. Think of it like a hotel key card: it opens some doors for this guest, in this room, at this time, but not every door in the building.

The file also defines the shapes of tool results, spawned subagent results, connector account selections, and several clear error types. Those errors are written so the AI model can often recover: for example, if it asks to spawn an unknown target, the message says which targets are valid.

ToolContext carries the current turn, agent, audience, sandbox, blob storage, credential access, browser/search providers, skill registry, cleanup hooks, and billing hooks. Its methods then apply project rules around those capabilities. For example, sharing an artifact checks size and preview safety before writing a database row; connector account selection only returns accounts this turn is allowed to use; credential authorization requires a real speaking workspace admin.

Without this file, each tool would have to reinvent security, cleanup, billing, sharing, and delegation rules. That would make leaks, duplicate external actions, stale connections, and permission mistakes much more likely.

#### Function details

##### `UnknownSubagentProfile.__init__`  (lines 126–131)

```
def __init__(self, requested: str, registered: tuple[str, ...]) -> None
```

**Purpose**: Builds a helpful error when code asks for a subagent profile that is not registered. It includes both the bad name and the valid profile names so the caller can correct the request.

**Data flow**: It receives the requested profile name and the tuple of registered names. It formats those into a readable error message and stores both pieces of information on the exception for later logging or inspection.

**Call relations**: The subagent registry calls this when a profile lookup fails. The exception then travels back to the spawning flow so the tool or model sees a repairable explanation instead of a bare missing-key failure.

*Call graph*: called by 1 (get).


##### `SpawnPayloadRejected.__init__`  (lines 142–146)

```
def __init__(self, target: str, keys: str, detail: str) -> None
```

**Purpose**: Builds a clear error when a spawn target exists, but the input sent to it does not match what that target accepts. It tells the caller which target rejected the payload, what keys are expected, and what was wrong.

**Data flow**: It receives the target name, a description of accepted keys, and the validation detail. It turns them into one plain error message and also stores each part on the exception.

**Call relations**: The subagent validation path calls this after resolving a target but rejecting its payload. This lets the spawning tool report an input-shape problem that the model can fix on a retry.

*Call graph*: called by 1 (_validated).


##### `UnknownSpawnTarget.__init__`  (lines 153–160)

```
def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None
```

**Purpose**: Builds an error for a spawn request that names neither a known subagent profile nor a known workspace agent. It lists available profiles and agents so the caller has a useful next step.

**Data flow**: It receives the requested target, valid profile names, and valid agent names. It formats them into an error message and stores them as fields on the exception.

**Call relations**: The subagent resolver and agent-spawn checker call this when they cannot find the named target. The message is meant to flow back to the tool result so the model can choose a real target.

*Call graph*: called by 2 (_require_agent_spawn, _resolve).


##### `SpawnNeedsOwnModelKey.__init__`  (lines 174–182)

```
def __init__(self, requested: str, connect_url: str | None=None) -> None
```

**Purpose**: Builds an error for a spawn target that needs the member’s own connected ChatGPT or Claude account, when the member has not connected one. It includes a link destination when available.

**Data flow**: It receives the requested spawn target and an optional base URL. It turns that into a message explaining the missing account requirement, either pointing to the credential page or to the portal generally, and stores the requested target.

**Call relations**: The subagent spawning flow raises this before starting a coding-style child agent that cannot run without the member’s own provider account. The caller can then tell the member where to connect the account instead of repeatedly trying the same spawn.

*Call graph*: called by 1 (spawn).


##### `AmbiguousSpawnTarget.__init__`  (lines 189–194)

```
def __init__(self, requested: str) -> None
```

**Purpose**: Builds an error when a short spawn target name matches both a profile and an agent. It tells the caller to use an explicit prefix so the system knows which one is meant.

**Data flow**: It receives the ambiguous name. It creates a message naming the two qualified forms, profile:name and agent:name, and stores the original requested name.

**Call relations**: The subagent target resolver calls this when both namespaces contain the same bare name. The error guides the model or host code to retry with a precise target.

*Call graph*: called by 1 (_resolve).


##### `Spawn.__call__`  (lines 266–275)

```
async def __call__(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnRes
```

**Purpose**: Describes the callable interface used to delegate work to a child turn, either a named subagent profile or a workspace agent. Callers use it to run a typed subtask and optionally wait for the result.

**Data flow**: The caller provides a target name, a payload dictionary, and options such as background mode, a deduplication key, result delivery behavior, display name, and whether to detach if a member message arrives. An implementation validates the payload, creates or reconnects to a child turn, and returns a SpawnResult describing that child and any finished output.

**Call relations**: ToolContext exposes this protocol as ctx.spawn. The real implementation lives in the subagent runtime; tools call through this interface without needing to know how child turns are admitted, awaited, deduplicated, or delivered.


##### `SubagentControl.result`  (lines 285–285)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Describes the operation for reading the final result of an already-spawned background subagent. It is used when a tool has a child turn id and wants its completed output.

**Data flow**: It takes a child turn id. An implementation looks up that child, reads its terminal state and validated output if available, and returns a SpawnResult.

**Call relations**: ToolContext can expose a SubagentControl object for lifecycle tools. Those tools call result when they need to collect a background child’s answer rather than spawning a new child.


##### `SubagentControl.wait`  (lines 287–287)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes the operation for waiting on one or more background subagents for a bounded time inside a tool call. It reports their statuses rather than blindly blocking forever.

**Data flow**: It takes a tuple of child turn ids. An implementation waits according to its own rules, checks the children, and returns a tuple of SubagentStatus objects with status text and trust information.

**Call relations**: Lifecycle tools use this through ToolContext when a caller wants to pause for background children. It fits between spawning and final result collection.


##### `SubagentControl.cancel`  (lines 289–289)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes the operation for stopping a running background subagent. It gives the caller a final status for the child it tried to cancel.

**Data flow**: It takes a child turn id. An implementation asks the subagent runtime to stop that child and returns a SubagentStatus describing the resulting state.

**Call relations**: Tools that supervise background subagents call this through ToolContext when work is no longer needed or should be interrupted.


##### `SubagentControl.message`  (lines 291–293)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Describes the operation for sending a follow-up message to an existing child turn. The deduplication key helps avoid sending the same message twice during retries.

**Data flow**: It takes a child turn id, message text, a deduplication key, and a flag saying whether the child will deliver its own result. An implementation admits that message to the child turn and returns the child’s current or resulting status.

**Call relations**: Lifecycle tools use this when a background subagent needs more instructions. It relies on the same subagent workflow that backs spawning, but acts on an existing child.


##### `TurnCleanup.register`  (lines 307–308)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous cleanup action to be run when the turn ends. Tools use this for resources like browser connections or hosted-session leases that must not leak past the turn.

**Data flow**: It receives a no-argument async close function. It appends that function to the cleanup list, changing the TurnCleanup object so the closer will be called later.

**Call relations**: Tools register closers when they first open per-turn resources. The main turn loop later calls TurnCleanup.drain to close everything in a controlled order.


##### `TurnCleanup.drain`  (lines 310–316)

```
async def drain(self) -> None
```

**Purpose**: Runs all registered cleanup actions at the end of a turn, newest first. If one cleanup fails, it logs the failure and keeps going so one bad close does not leave the rest open.

**Data flow**: It reads the internal list of async close functions. It repeatedly removes the last one, awaits it, and logs any exception through the observation/logging system; when done, the list is empty.

**Call relations**: The turn loop calls this during turn shutdown. It consumes the actions that tools previously added with TurnCleanup.register.

*Call graph*: 1 external calls (log).


##### `ToolContext.authority`  (lines 365–371)

```
def authority(self) -> ExecutionAuthority
```

**Purpose**: Figures out whose authority this tool call is running under. If there is a live speaking member, it uses that member; otherwise it falls back to the authority recorded on the turn.

**Data flow**: It reads speaker_member_id and turn.on_behalf_of_member_id. It returns a MemberAuthority for the live speaker when present, or converts the turn’s stored member id into the appropriate execution authority.

**Call relations**: Many permission decisions depend on this property. Other ToolContext methods, such as effective_audience and connector account selection, use it to decide what the tool may read or use.

*Call graph*: 2 external calls (__init__, authority_from_member_id).


##### `ToolContext.effective_audience`  (lines 374–384)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides which audience a write should belong to. It prevents facts from private or special conversations from being stamped in a way that would leak them into unrelated rooms.

**Data flow**: It reads the current authority and conversation audience. If there is no acting member or the conversation is not the shared workspace audience, it returns the existing audience; otherwise it returns an audience scoped to the acting member’s conversation.

**Call relations**: Write paths use this when they need the correct privacy boundary for new data. It builds on ToolContext.authority and audience helper functions.

*Call graph*: 2 external calls (authority_member_id, conversation_audience).


##### `ToolContext.read_subjects`  (lines 387–396)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes the set of subjects this tool call may read from: the conversation’s subjects plus the acting member’s private subject when there is an acting member. A subject is a privacy label used to decide which stored information is visible.

**Data flow**: It reads the current audience and authority. It turns the audience into readable subjects, optionally adds the acting member’s subject, and returns the combined frozen set.

**Call relations**: Source and memory tools use this through source_reader and related access checks. It relies on audience and authority helpers to keep read visibility consistent.

*Call graph*: 3 external calls (authority_member_id, audience_subjects, member_subject).


##### `ToolContext.store_preview`  (lines 398–444)

```
async def store_preview(self, sandbox_path: str, name: str, *, extension: str='png') -> StoredPreview | None
```

**Purpose**: Stores an image file produced inside the sandbox as a preview artifact. It is meant for decorative previews: if the image cannot be read or uploaded, it logs the problem and returns nothing instead of failing the whole tool.

**Data flow**: It receives a sandbox path, a display name, and an image extension. It asks the sandbox to measure the file, creates a new artifact key, then either uploads through a presigned S3 URL from inside the sandbox or streams the file into local blob storage. On success it returns a StoredPreview containing the blob key and byte size; on sizing or upload failure it returns None.

**Call relations**: Site-building and share-card extension code call this after rendering an image. It uses sandbox shell access, blob storage, UUID generation, media preview conventions, and logging to safely move the preview into the artifact namespace.

*Call graph*: called by 2 (design_ufo_application, _compose); 5 external calls (__init__, quote, log, shell_path, uuid4).


##### `ToolContext.render_site_preview`  (lines 446–453)

```
async def render_site_preview(self, name: str, port: int, width: int, height: int) -> StoredPreview | None
```

**Purpose**: Asks the configured preview service to take a screenshot-like preview of a hosted sandbox port. If no preview service is configured, it quietly returns nothing.

**Data flow**: It receives a name, port, width, and height. It chooses the sandbox conversation id when present, otherwise the turn conversation id, then asks site_previewer to render and store the preview; the result is a StoredPreview or None.

**Call relations**: Site extension tools call this when they want an image of a running web page. It hands off the actual browser or rendering work to the SitePreviewer service.

*Call graph*: called by 1 (_illustrate).


##### `ToolContext.source_reader`  (lines 455–465)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Builds a compact description of who is reading synced source pages. It combines the current agent, the live requesting member, and the subjects this call may read.

**Data flow**: It reads the turn’s agent id, speaker_member_id, and read_subjects. It packages those into a SourceReader object for source and memory systems to use.

**Call relations**: Memory and source extension code call this before listing or reading stored pages. It centralizes the access identity so those extensions do not each rebuild the same permission context.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 467–476)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images on this turn’s billing ledger. This matters because image providers may charge per image rather than through the normal language-model token accounting.

**Data flow**: It receives the provider model name, image count, and cost in micro-dollars. It opens a workspace database transaction and writes an image-usage record tied to the workspace, turn, and model.

**Call relations**: The OpenRouter image extension calls this after it knows the provider charge. The method hands the write to the central billing accounting function.

*Call graph*: called by 1 (generate); 2 external calls (workspace_tx, record_image_usage).


##### `ToolContext.meter_videos`  (lines 478–486)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos on this turn’s billing ledger. Video generation has its own pricing shape, so the extension reports the charge here for core billing to store.

**Data flow**: It receives the provider model name, video count, and cost in micro-dollars. It opens a workspace database transaction and writes a video-usage record tied to the workspace, turn, and model.

**Call relations**: The OpenRouter video extension calls this after generation. It delegates the actual ledger write to the central video accounting function.

*Call graph*: called by 1 (generate); 2 external calls (workspace_tx, record_video_usage).


##### `ToolContext.share_artifact`  (lines 488–571)

```
async def share_artifact(self, filename: str, data: bytes, subject: str | None=None, *, preview: StoredPreview | None=None) -> None
```

**Purpose**: Publishes a small in-memory file as a shared artifact for the current turn. It checks size and preview safety, stores the bytes, records the artifact in the database, and optionally notifies the surrounding surface that artifacts changed.

**Data flow**: It receives a filename, bytes, an optional subject, and an optional StoredPreview. It rejects files over the shared byte limit and invalid previews, chooses a stable artifact id when an idempotency key exists, writes the bytes to blob storage, inserts a shared_artifact row, cleans up the blob if the database write fails for a new artifact, and finally calls publish_artifacts if configured.

**Call relations**: Extensions call this when they create a file directly in process, such as a generated site artifact or an iMessage-related output. It coordinates blob storage, database rows, media typing, idempotent naming, cleanup logging, and artifact publication.

*Call graph*: called by 2 (run, design_ufo_application); 8 external calls (now, select, workspace_tx, log, artifact_media_type, raster_image_media_type, uuid4, uuid5).


##### `ToolContext.speaker_is_admin`  (lines 573–583)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live requesting member is a workspace admin. If there is no live speaker, it returns false because background work should not silently exercise admin powers.

**Data flow**: It reads speaker_member_id. If absent, it returns False; otherwise it opens a workspace database transaction and asks the seat/member system whether that member is an admin in this workspace.

**Call relations**: Many object and host tools call this before workspace-wide or admin-only actions. Credential authorization also uses it through _credential_authorization.

*Call graph*: called by 26 (_widens_for_admin, delete, _visible_rows, request_credentials_handler, restore, apply, delete, get, list, status (+15 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 585–596)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether the current turn’s agent is the workspace’s main agent. Some actions are only allowed or shown differently for the main agent.

**Data flow**: It reads the turn’s agent id and workspace id. It queries the agent table for the is_main flag and returns it as a boolean, treating a missing row as false.

**Call relations**: Member, workspace, and web-audience tools call this when deciding visibility or whether an action may be applied by the current agent.

*Call graph*: called by 6 (add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.agent_visibility`  (lines 598–610)

```
async def agent_visibility(self) -> AgentVisibility
```

**Purpose**: Reads whether the current agent is private or visible to the workspace. It raises an error if the stored database value is outside the levels this code understands.

**Data flow**: It reads the turn’s agent id and workspace id, queries the agent table for the visibility value, validates that it is either private or workspace, and returns that value.

**Call relations**: The sites extension calls this when redeploying a homepage. It provides the extension with the current agent’s sharing level without duplicating the database query.

*Call graph*: called by 1 (_redeploy_homepage); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 612–614)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for an extension credential slot. It is used when an admin needs to approve storing or connecting a secret for an extension.

**Data flow**: It receives a credential slot name and payload. It first calls _credential_authorization to check that the request is allowed and to get the credential request service plus member id, then asks that service to create a sealed authorization string.

**Call relations**: The Slack extension calls this to build an OAuth-style authorization link. It depends on _credential_authorization for all permission and configuration checks.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 1 (_oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 616–618)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens and verifies a previously sealed credential authorization for an extension slot. This is part of completing a credential authorization flow safely.

**Data flow**: It receives a slot name and sealed authorization string. It calls _credential_authorization to confirm the current call is allowed, then asks the credential request service to open the sealed value for this workspace, member, and slot.

**Call relations**: It pairs with begin_credential_authorization. Both share the same internal gatekeeper, _credential_authorization, so starting and opening authorizations follow the same rules.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext._credential_authorization`  (lines 620–629)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the shared safety checks for credential authorization. It requires a live speaking member, a declared extension credential slot, a configured credential system, and workspace-admin status.

**Data flow**: It receives a slot name and reads speaker_member_id, extension metadata, requestable_credentials, and admin status. It raises clear errors when any requirement is missing; otherwise it returns the credential request service and the speaking member id.

**Call relations**: begin_credential_authorization and open_credential_authorization both call this before touching credential authorization data. It also calls speaker_is_admin to enforce the admin-only rule.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 2 (begin_credential_authorization, open_credential_authorization); 1 external calls (__init__).


##### `ToolContext.connector_account`  (lines 631–640)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns only the external account id for a connector provider that this turn may use. It is a convenience wrapper for tools that do not need the full connection metadata.

**Data flow**: It receives a provider name and optional account id. It asks connector_connection to resolve the allowed connection, then returns that connection’s account_id.

**Call relations**: Sample connector execution code calls this when it only needs the broker’s account identifier. The fuller permission and ambiguity logic lives in connector_connection.

*Call graph*: calls 1 internal fn (connector_connection); called by 1 (_connector_execute).


##### `ToolContext.connector_connection`  (lines 642–683)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Chooses the exact connector connection this tool call is allowed to use. It respects private member grants, shared agent grants, explicit account choices, and ambiguity rules.

**Data flow**: It receives a provider name and optional account id. It gets private and shared grant tiers from _connector_account_tiers; if an account id was requested, it returns the matching grant or raises. If no account id was requested, it prefers private grants over shared grants, rejects none or multiple choices, and returns a ConnectorConnection with connection id, grant id, provider, account id, and owner member id.

**Call relations**: Connector and source extensions call this before using an external account. It provides the exact grant generation so later code can recheck it with require_connector_connection before making an external side effect.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 3 (connector_account, call_external_tool, _resolved_account); 1 external calls (__init__).


##### `ToolContext.require_connector_connection`  (lines 685–705)

```
async def require_connector_connection(self, selected: ConnectorConnection) -> None
```

**Purpose**: Rechecks that a previously selected connector connection is still valid for this call. This protects against a grant being revoked, disconnected, or changed after the tool selected it but before it acts externally.

**Data flow**: It receives a ConnectorConnection selected earlier. It reloads the current private and shared grant tiers for that provider and looks for a grant matching the same grant id, connection id, account id, and owner member. If none matches, it raises an error; otherwise it returns without changing anything.

**Call relations**: Connector tools can call this as a last-mile safety check before sending work to the external broker. It uses the same _connector_account_tiers rule as initial selection.

*Call graph*: calls 1 internal fn (_connector_account_tiers).


##### `ToolContext.connector_accounts`  (lines 707–713)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists the connector account ids available to this tool call for one provider. It is useful when a caller needs to show choices or resolve an account selection.

**Data flow**: It receives a provider name. It gathers private and shared grants from _connector_account_tiers, extracts their account ids, removes duplicates, sorts them, and returns them as a tuple.

**Call relations**: Source extension code calls this while resolving which external account to use. It shares the underlying grant visibility logic with connector_connection.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 715–734)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: Splits active connector grants into private grants for the acting member and shared grants for the agent. This is the core permission filter behind connector account use.

**Data flow**: It receives a provider name and reads the configured GrantStore plus the current authority. If grants are unavailable, it raises ConnectUnavailable. Otherwise it loads active grants, filters private grants to those owned by the acting member for this provider, filters shared grants for this provider, sorts both lists by account id, and returns the two lists.

**Call relations**: connector_connection, connector_accounts, and require_connector_connection all call this. It is the common access-control step that keeps connector tools from using accounts outside the turn’s authority.

*Call graph*: called by 3 (connector_accounts, connector_connection, require_connector_connection); 2 external calls (__init__, authority_member_id).


### `core/src/ufo/runtime/tools/file_changes.py`

`config` · `cross-cutting`

This file is intentionally tiny, but it still matters. It sets a single named limit, `FILE_CHANGE_PATH_MAX_CHARS`, to 4,096 characters. That limit can be used anywhere the runtime records or checks paths for changed files.

In plain terms, it is like putting a height limit sign at the entrance to a tunnel. Other parts of the system do not need to guess how long a path is allowed to be; they can all read the same sign. This helps keep behavior consistent and makes the limit easier to find and change later.

Without this shared constant, different parts of the code might choose different path limits, or copy the same number by hand. That can lead to confusing bugs, such as one part accepting a long path while another rejects it. By naming the limit clearly, the file also explains the intent: this number is specifically about paths used in file-change tracking.


### Dispatch and task journals
These files validate and dispatch runtime tools while preserving durable journals for long-running shell work.

### `core/src/ufo/runtime/tools/registry.py`

`domain_logic` · `startup and tool-call dispatch`

A “tool” here is an action the model can ask the system to run, such as reading data, writing to an external service, or acting on a visible object. This file gives each tool a standard wrapper, `ToolDef`, which records its public name, human-readable description, input shape, and the async function that actually runs it. It also records important safety hints: whether the tool output may contain attacker-controlled text, whether it changes the outside world, whether it can run in parallel with other calls, and whether it is tied to a specific object type.

The file also separates normal global tools from object-bound actions. Object actions get a special canonical identity like `action:<kind>:<name>` and are not allowed into the normal wire registry. That prevents name clashes and keeps object actions routed through the object-action path instead of pretending to be ordinary tools.

`ToolRegistry` is the frozen catalog the engine uses at runtime. When created, it checks for duplicate names, reserved prefixes, invalid object bindings, and illegal input fields. This is like checking a toolbox before work starts: every tool must have a unique label, fit in the right drawer, and not reuse reserved parts. Without this file, tool calls could be ambiguous, unsafe to expose, or impossible to route reliably.

#### Function details

##### `ToolDef.canonical_id`  (lines 95–100)

```
def canonical_id(self) -> str
```

**Purpose**: Gives a tool its stable system-wide identity. Ordinary tools use their name, while object-bound actions get a special `action:<kind>:<name>` identity so they can be tracked and authorized separately.

**Data flow**: It reads the tool’s own name and, if present, its object binding. If there is no binding, it returns the plain name. If the tool is bound to an object kind, it builds and returns the special action-style identifier.

**Call relations**: Other parts of the runtime can use this identity for things like allowlists, logs, idempotency keys, and dispatch decisions. It keeps global tools and object actions from being confused even if their short names look similar.


##### `ToolDef.schema`  (lines 102–115)

```
def schema(self, *, include_requested_by: bool=True) -> ToolSchema
```

**Purpose**: Builds the public schema for a tool: the name, description, and expected input format that can be sent over the wire to the model or client. A schema is a machine-readable description of what arguments the tool accepts.

**Data flow**: It starts with the tool’s Pydantic input model, asks it for a JSON-style input schema, and optionally adds a reserved `requested_by` field that records which message explicitly requested the call. It then packages the name, description, and input schema into a `ToolSchema` object.

**Call relations**: When the registry needs to publish available tools, each `ToolDef` turns itself into a `ToolSchema`. This function hands off to `ToolSchema` construction so the rest of the system receives a consistent wire-ready shape.

*Call graph*: 1 external calls (__init__).


##### `validate_tool_declaration`  (lines 118–142)

```
def validate_tool_declaration(tool: ToolDef[Any], label: str) -> None
```

**Purpose**: Checks that a declared tool is internally consistent before the system starts using it. It catches configuration mistakes early, such as an empty button label, an object action pinned to the wrong kind of binding, or a final-action model the terminal frame cannot carry.

**Data flow**: It receives a `ToolDef` and a human-readable label for error messages. It inspects presentation settings, object binding settings, profile-only status, and final-action model settings. If everything is valid it returns nothing; if something is wrong it raises a `ValueError` that explains the problem.

**Call relations**: `ToolRegistry.__post_init__` calls this for each tool during registry construction. That means invalid tool declarations fail at startup instead of causing confusing behavior later during a model turn.

*Call graph*: called by 1 (__post_init__).


##### `ToolRegistry.__post_init__`  (lines 149–169)

```
def __post_init__(self) -> None
```

**Purpose**: Acts as the registry’s startup inspection. After the frozen registry is created, it rejects tool lists that would be unsafe or ambiguous to expose.

**Data flow**: It reads the registry’s tuple of tools. It checks for duplicate names, object-bound actions accidentally placed in the normal registry, names using the reserved `action:` prefix, and input models that already define the reserved `requested_by` field. Then it sends each tool through `validate_tool_declaration`. It changes no stored data; it either finishes silently or raises a clear error.

**Call relations**: This is automatically run after `ToolRegistry` is constructed. It calls `validate_tool_declaration` as the per-tool detailed check, while it performs the whole-registry checks itself.

*Call graph*: calls 1 internal fn (validate_tool_declaration).


##### `ToolRegistry.schemas`  (lines 171–172)

```
def schemas(self, *, include_requested_by: bool=True) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the wire-ready descriptions for every global tool in the registry. This is what another part of the engine can use when it needs to tell the model which tools are available and how to call them.

**Data flow**: It reads the registry’s stored tools and the `include_requested_by` option. For each tool, it asks the tool to produce its schema with that option, then returns all schemas as an immutable tuple.

**Call relations**: This sits between the frozen internal registry and the outside-facing tool list. Instead of callers building schemas themselves, they ask the registry, which delegates the per-tool formatting to each `ToolDef`.


##### `ToolRegistry.get`  (lines 174–178)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the tool definition for a given tool name. The engine uses this when a tool call arrives and it needs to know which handler and input model belong to that name.

**Data flow**: It receives a name string, scans the registry’s tools, and returns the matching `ToolDef` if it finds one. If no tool has that name, it raises a `KeyError` so the unknown call fails loudly instead of being ignored or misrouted.

**Call relations**: During dispatch, code can ask the registry for the tool named in an incoming call. The registry hands back the full definition, including the handler to run and the input model to validate against.


### `core/src/ufo/runtime/tools/tasks.py`

`orchestration` · `tool execution / request handling`

This file solves a practical problem: tools often need to run shell commands that may take longer than the current turn can wait. Instead of killing that work when the wait time expires, this code starts commands in a detached way and records them under the run’s task directory. Think of it like dropping off a package at a service desk: you may leave before the job is done, but you get a claim ticket so you can check the result later.

The central flow is `run_task`. It chooses a stable task name, starts the command through the sandbox, and waits only for the caller’s time budget. If the command finishes in time, it returns the normal result. If the wait expires, it checks whether the detached command is still alive. If it is alive, the caller can later read its log, watch for its exit file, or stop it. If nothing is alive, that suggests the sandbox execution channel failed rather than the command simply taking too long, so the file records diagnostic information.

The file also includes small helpers for user-facing messages. `task_handles` prints the paths and commands a caller needs to follow a running task. `timeout_notice` explains which timeout actually happened. `flat_sleeps` protects foreground turns from commands that simply waste time with long top-level `sleep` calls, while still allowing polling loops.

#### Function details

##### `flat_sleeps`  (lines 33–51)

```
def flat_sleeps(command: str) -> tuple[int, ...]
```

**Purpose**: This function looks for long, plain `sleep` commands that would just pad out a foreground turn. It ignores sleeps inside quoted text, heredoc bodies, and shell loop bodies, because those are usually data or part of polling logic rather than a wasteful wait.

**Data flow**: It takes a shell command as text. It first masks out quoted strings and heredoc blocks, then scans what remains for `sleep`, `do`, and `done`. It tracks whether the scan is currently inside a loop, keeps only top-level sleeps longer than the allowed small limit, and returns those sleep lengths as a tuple of numbers.

**Call relations**: No direct caller is shown in the provided call facts, but this helper is meant to be used before running a foreground command. It acts as an early safety check so the task-running path does not spend a turn waiting on an avoidable long sleep.


##### `run_task`  (lines 94–139)

```
async def run_task(ctx: ToolContext, command: str, timeout_ms: int | None, *, model_authored: bool) -> TaskRun
```

**Purpose**: This is the main entry for launching a command through the task journal. It starts the command, waits for the allowed time, and returns enough information to know whether the command finished, timed out, or is still running in the background.

**Data flow**: It receives a tool context, the shell command text, an optional timeout in milliseconds, and a flag saying whether the model wrote the command. It converts the timeout to seconds, caps it at the system maximum, builds a stable task path, and asks the sandbox to run the command as a journaled task. If the command finishes, it returns a `TaskRun` with the result and no running process id. If the wait expires, it probes the task files to see whether the detached supervisor process still exists. If the process cannot be found, it records timeout diagnostics. It returns a `TaskRun` containing the task id, sandbox result, requested timeout, possible process id, and display path.

**Call relations**: When a tool needs shell work done, this function drives the whole launch-and-wait sequence. It calls `task_id` first so repeated attempts can refer to the same journal entry. If a timeout looks like a sandbox execution failure rather than a still-running task, it hands off to `_record_exec_timeout` for diagnostics before returning the outcome.

*Call graph*: calls 2 internal fn (_record_exec_timeout, task_id); 1 external calls (__init__).


##### `task_id`  (lines 142–149)

```
def task_id(ctx: ToolContext) -> str
```

**Purpose**: This function chooses the short name used for a task’s files. Its main job is to make retrying the same recorded step find the same task instead of launching the command again.

**Data flow**: It reads the idempotency key from the tool context. If there is no key, it creates a fresh random id, because there is no earlier recorded attempt to reconnect to. If there is a key, it hashes that key and uses the first part of the hash as the task id, producing the same id every time for the same recorded step.

**Call relations**: `run_task` calls this before creating the journal paths. This is what lets a recovered or retried dispatch step reattach to the first command’s task files rather than duplicating the shell command.

*Call graph*: called by 1 (run_task); 2 external calls (sha256, uuid4).


##### `task_handles`  (lines 152–173)

```
def task_handles(task: str, pid: str, display_base: str, applied_s: int | None=None, note: str='') -> str
```

**Purpose**: This function writes the human-readable instructions for following a detached task. It tells the caller where to read output, how to detect completion, and how to stop the command.

**Data flow**: It receives the task id, supervisor process id, display path for the task files, an optional timeout that expired, and an optional note. It chooses the right opening sentence depending on whether the command was backgrounded from the start or moved to the background after a timeout. Then it builds a small JSON payload containing the log path, exit-file path, watch command, and stop command, and returns all of that as text.

**Call relations**: No direct caller is shown in the provided call facts, but this helper is the companion to `run_task` when a command remains detached. After `run_task` identifies a live background task, callers can use this function to present consistent follow-up instructions.

*Call graph*: 1 external calls (dumps).


##### `timeout_notice`  (lines 176–192)

```
def timeout_notice(applied_s: int, requested_s: int | None) -> str
```

**Purpose**: This function turns timeout details into a clear message for the caller. It explains whether the default timeout was used, whether a requested timeout was capped, or whether the requested timeout simply expired.

**Data flow**: It receives the number of seconds that actually applied and the number of seconds requested, if any. If no timeout was requested, it says the sandbox used its default. If the request was larger than the maximum allowed, it says the request was capped. Otherwise it reports the timeout plainly. It returns only the message text.

**Call relations**: No direct caller is shown in the provided call facts, but this is meant for result formatting after a command stops because of a timeout. It complements the task-running code by making the reason understandable instead of leaving the caller to infer it from an exit code.


##### `_record_exec_timeout`  (lines 195–228)

```
async def _record_exec_timeout(ctx: ToolContext, command: str, applied_s: int, requested_s: int | None) -> None
```

**Purpose**: This function records diagnostic clues when a command times out and the task probe does not show a still-running process. It helps distinguish “the command was slow” from “the sandbox stopped answering.”

**Data flow**: It receives the tool context, the command text, the applied timeout, and the requested timeout. It tries, for only a few seconds, to run a small system-status command inside the sandbox that reads load, memory, and disk information. Whether that probe succeeds or fails, it writes a structured log entry with the turn profile, timeout values, a shortened copy of the command, and any vitals it could collect. It does not change the command result and it deliberately swallows its own failures.

**Call relations**: `run_task` calls this only after a wait expires and the follow-up probe cannot find a live detached task. This keeps normal timeout handling fast, while still leaving an operations log for the unusual case where the sandbox execution channel may have failed.

*Call graph*: called by 1 (run_task); 3 external calls (timeout, log, turn_profile).
