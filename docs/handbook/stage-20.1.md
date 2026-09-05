# Harness safety, serialization, observability, and sandbox network settings  `stage-20.1`

This stage is shared behind-the-scenes support for the harness, the part of the system that runs work on behalf of agents and tools. Its job is to keep that work safe, repeatable, and visible.

The containment code is the safety gate for file access. It checks any path that comes from an untrusted source and makes sure it cannot escape the intended folder, even with tricks like symbolic links. The untrusted-content helper solves a similar problem for text: it wraps outside material so the model treats it as evidence to read, not orders to follow.

The durability code protects saved workflow data. It stores and reloads Python objects in a way that can survive crashes and later code changes. The observability code is the monitoring window. It sends logs, metrics, traces, and health checks outward, while reducing the chance that private text leaks.

The sandbox cache and preview settings define safe network destinations for isolated work. Finally, the tools helper groups ordered tasks into chunks that can run in parallel when safe.

## Files in this stage

### Filesystem containment
Enforces safe path handling so untrusted inputs cannot escape the intended workspace.

### `core/src/ufo/harness/containment.py`

`domain_logic` · `cross-cutting file access`

A path like `../../secret` is an obvious danger, but this file protects against subtler attacks too. The main risk is a symlink, which is like a shortcut: an agent could create a harmless-looking file name inside its workspace that secretly points to a host file outside it. If the program followed that shortcut while reading or writing, it could leak or overwrite data it should never touch.

This module solves that by checking paths in layers. First it rejects names that are not usable relative file names. Then it resolves the real parent directory and confirms it is inside the allowed root. Then it walks down each directory component using file descriptors, which are operating-system handles to specific directories. That “pins” the directory, like holding the actual folder in your hand instead of trusting its street address. Finally, it checks the target file itself without following a final symlink.

The central object is `ContainedFile`, which represents a target file whose parent directory has already been pinned. Its methods read, write, rename, chmod, or remove the target through that safe parent. Writes are staged under a temporary sibling name and then renamed into place, so readers see either the old file or the complete new file, not a half-written one.

The file also has lighter helpers for cases where this process cannot inspect the real filesystem yet, such as validating a stored file key or cleaning up a filename supplied by a browser.

#### Function details

##### `contained_root`  (lines 88–101)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Checks that a root directory used for containment is real, is a directory, and is not itself a symlink. This matters because if the root were a shortcut, every later “inside the root” check could be fooled into protecting the wrong place.

**Data flow**: It receives a root path from the caller. It looks at the path itself without following symlinks, refuses it if it is missing or not a directory, and returns the root’s canonical real path. If the root is unsafe, it raises a containment-related error instead of returning.

**Call relations**: This is the first safety step for `contained_file`, `contained_dir`, and `contained_remove`. Those higher-level operations call it before they inspect or change anything under the root.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 104–121)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Checks a root directory that came from operator configuration, while allowing the configured root itself to be a symlink. This supports normal deployment layouts where, for example, a configured data folder points to mounted storage.

**Data flow**: It receives a root path and the name of the setting that supplied it. It follows the configured path, confirms the final target exists and is a directory, and returns the canonical path. If it is missing or not a directory, the error message names the setting to help the operator fix the configuration.

**Call relations**: No in-file caller is shown for this helper. It exists for setup or configuration code that needs a trusted base directory chosen by an operator, not by an untrusted agent.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 139–150)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Looks at the target file itself without following a final symlink. It tells callers whether the target is absent, is a regular file, or must be refused because it is a directory or another unsafe kind of filesystem object.

**Data flow**: It reads the target name relative to the already pinned parent directory. If nothing exists there, it returns `None`; if a regular file exists, it returns its file information; if a directory or non-regular item is there, it raises `NotRegularFile`.

**Call relations**: This method is used after `contained_file` has created a `ContainedFile`. It relies on the pinned parent directory stored in the object, so the check and later operations refer to the same location.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 152–167)

```
def mode(self, default: int) -> int
```

**Purpose**: Finds the permission bits to preserve when overwriting a file, or supplies a default when there is no regular file to copy permissions from. It refuses directories because replacing a directory as if it were a file would be wrong.

**Data flow**: It receives a default permission mode. It inspects the target name through the pinned parent directory; if the target is a regular file, it returns that file’s permission bits, if it is missing or a symlink it returns the default, and if it is a directory it raises an error.

**Call relations**: Callers use this on a `ContainedFile` before writing replacement contents. It supports `replace_bytes` and similar write flows by deciding what permissions the new staged file should carry.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 169–179)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained target for safe streaming reads. It is meant for large files or copy operations where loading the whole file into memory would be wasteful.

**Data flow**: It asks `_open_regular` to open the target safely as a real file. If that succeeds, it wraps the low-level file descriptor in a buffered binary reader and returns it; if wrapping fails, it closes the descriptor so no operating-system handle leaks.

**Call relations**: `ContainedFile.read_bytes` calls this when it wants to read a limited amount of data. The safety work is delegated to `_open_regular`, while this method turns the safe descriptor into a normal Python file-like object.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 181–184)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a caller-specified number of bytes from a contained file. The limit is important because it prevents accidentally pulling an arbitrarily large file into memory.

**Data flow**: It receives a byte limit, opens the file safely with `open_bytes`, reads at most that many bytes, closes the stream automatically, and returns the bytes read.

**Call relations**: `ContainedFile.read_text` builds on this method for text reads. This method sits between the safe streaming open and simple callers that just want a bounded byte string.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 186–187)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads a contained file as text, with a size limit. It decodes bytes as UTF-8 and replaces invalid byte sequences instead of crashing on imperfect text.

**Data flow**: It receives a byte limit, asks `read_bytes` for up to that many bytes, decodes them into a string, and returns the text.

**Call relations**: This is the text-friendly wrapper around `read_bytes`. Callers that need raw data use `read_bytes`; callers that need displayable text use this.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 189–190)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the permissions of the contained target file without following a symlink. It keeps permission changes scoped to the exact pinned parent directory.

**Data flow**: It receives a permission mode, masks it down to normal permission bits, and applies it to the target name relative to the pinned parent directory. It changes the filesystem and returns nothing.

**Call relations**: This method is available after `contained_file` yields a `ContainedFile`. It uses the object’s pinned parent so a caller does not have to pass around an unsafe path string.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 192–196)

```
def unlink(self) -> None
```

**Purpose**: Removes the contained target file if it exists. Missing files are treated as already gone, which makes cleanup code simpler and safer.

**Data flow**: It tries to delete the target name relative to the pinned parent directory. If the file is present, it removes it; if it is already missing, it silently does nothing.

**Call relations**: This is a direct cleanup operation on a `ContainedFile` produced by `contained_file`. It does not call the broader removal helper because the parent has already been pinned.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 198–200)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Moves another already-contained file into this target’s place. This is useful when both the source and destination have been separately checked and pinned.

**Data flow**: It receives another `ContainedFile` as the source. It renames the source name from its pinned parent directory onto this object’s target name in this object’s pinned parent directory, replacing any existing target file.

**Call relations**: This method connects two `ContainedFile` objects. It relies on both sides having come through the containment flow, so the rename is performed relative to pinned parent directories instead of fragile full path strings.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 202–203)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Writes text into the contained target by using the same safe replacement path as binary writes. It is a convenience wrapper for callers that have a string rather than bytes.

**Data flow**: It receives text and a permission mode, encodes the text into bytes, and passes those bytes to `replace_bytes`. The filesystem change is done by `replace_bytes`, not directly here.

**Call relations**: This method is called by text-writing code and immediately hands off to `replace_bytes`, which performs the staged, symlink-safe write.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 205–229)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely replaces the target file with new bytes. It writes to a temporary sibling first, then renames that complete file into place, so the target is not left half-written.

**Data flow**: It receives byte data and a permission mode. It creates a unique staged file beside the target without following symlinks, applies permissions, writes the data, closes the file, and atomically renames the staged file over the target; if something fails, it closes any open descriptor and tries to remove the staged file.

**Call relations**: `ContainedFile.replace_text` calls this after encoding text. Other write flows can call it directly when they already have bytes and a `ContainedFile` from `contained_file`.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 231–247)

```
def _open_regular(self) -> int
```

**Purpose**: Performs the low-level safe open used for reading. It refuses missing files, symlinks, directories, and anything that is not a normal file.

**Data flow**: It opens the target name relative to the pinned parent directory with flags that do not follow symlinks. It then checks the opened file descriptor itself; if it is not a regular file, it closes it and raises an error. On success, it returns the raw file descriptor.

**Call relations**: `ContainedFile.open_bytes` calls this first, before turning the descriptor into a buffered reader. This method is the strict safety gate for reads.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 251–285)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main entry point for safely reading or writing one file under a root directory. It runs the full containment check and yields a `ContainedFile` whose parent directory is pinned by a file descriptor.

**Data flow**: It receives a path, a root, and a flag saying whether missing parent directories may be created. It validates the root, roots relative paths under it, rejects unusable target names, confirms the resolved parent stays inside the root, opens the root, descends safely through each parent directory, optionally creates missing directories along the way, yields a `ContainedFile`, and finally closes the pinned directory descriptor when the context ends.

**Call relations**: This function ties together `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. Callers use it as a context manager, and the `ContainedFile` methods are intended to be used while that context is still open.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 288–314)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Checks and returns a safe directory path under a root, optionally creating missing directories as it descends. It is used when the caller needs a directory to enumerate or walk, not a single file to open.

**Data flow**: It receives a path, a root, and a create flag. It validates the root, makes the path absolute under that root if needed, resolves it, confirms the result stays inside the root, opens the root directory, descends component by component without following symlinks, optionally creates missing pieces, closes the descriptor, and returns the resolved directory path.

**Call relations**: Like `contained_file`, it uses `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. Unlike `contained_file`, it releases the file descriptors before returning because the result is a directory path for enumeration rather than a pinned file operation.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 3 external calls (__init__, close, mkdir).


##### `contained_remove`  (lines 317–344)

```
def contained_remove(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> None
```

**Purpose**: Safely removes one file or directory tree under a root without following symlinks along the way. It is the deletion counterpart to the safe read and write helpers.

**Data flow**: It receives a path and root, validates and roots the target, rejects unusable removable names, confirms the parent resolves inside the root, opens and descends to the parent safely, and then inspects the target without following symlinks. If the target is missing, it does nothing; if it is a directory, it removes the tree only on platforms where recursive removal is symlink-safe; otherwise it unlinks the file.

**Call relations**: This function reuses the same path-checking machinery as `contained_file` and `contained_dir`: `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. It adds deletion-specific logic after the parent directory has been pinned.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 8 external calls (__init__, __init__, __init__, close, stat, unlink, rmtree, S_ISDIR).


##### `contained_pattern`  (lines 347–364)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Validates a glob pattern, which is a wildcard pattern used for listing matching files, so that it cannot make a search start outside the allowed root. It converts acceptable absolute patterns into root-relative ones.

**Data flow**: It receives a pattern string and a root path. It rejects any pattern containing `..`; if the pattern is already relative, it returns it unchanged; if it is absolute, it confirms it lies under the root, converts it to a relative pattern, and refuses a pattern that names the root directory itself.

**Call relations**: No in-file caller is shown for this helper. It is meant for listing or enumeration code that needs lexical protection before passing a pattern to a filesystem walk.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_relative`  (lines 367–392)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs the lexical, text-only version of containment for a path that this process may not be able to inspect on disk. It proves the path intends to stay under a root, but it does not prove what symlinks on the real filesystem would do.

**Data flow**: It receives a path string and a root string. It combines relative paths with the root, simplifies `.` and `..` components in text form, rejects attempts to climb above the root, rejects the root itself as a file target, and returns the resolved path string.

**Call relations**: No in-file caller is shown for this helper. It complements the stronger filesystem checks by serving cases like persisted file keys or paths inside a container that will be physically written later by another component.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 395–403)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Extracts one safe filename component from an outside-supplied name. It drops any directory path pieces so a name like `../../x` or `folder\x` becomes just a leaf filename.

**Data flow**: It receives the raw name and a fallback name. It treats backslashes as path separators too, takes only the final filename part, and returns the fallback if the result is empty, `.`, or `..`; otherwise it returns the leaf name.

**Call relations**: No in-file caller is shown for this helper. It is for import points such as browser uploads or provider-supplied filenames, before the chosen leaf is later joined under a root and written through the stronger guard.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 406–417)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Checks whether a path found during enumeration is a regular file inside the root and not reached through a symlink. It is a fast filter for deciding what to list.

**Data flow**: It receives a path and root. It first checks the path itself without following symlinks, rejects anything that is not a regular file, resolves the real path strictly, and returns true only if the resolved path equals the original path and lies inside the root; filesystem errors simply produce false.

**Call relations**: This helper uses `_inside` for the final containment test. It is not a substitute for `contained_file`; after a file is listed, an actual read should still go through the full pinned-parent flow.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 420–426)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Turns a caller-supplied path into the path that should be considered under a specific root. It prevents relative paths from accidentally being interpreted relative to the process’s current working directory.

**Data flow**: It receives a path and a root. If the path is already absolute, it returns it as a `Path`; otherwise it joins the path under the given root and returns that new `Path`.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this near the start of their flows. It ensures all later checks talk about the same intended location.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 1 external calls (Path).


##### `_inside`  (lines 429–430)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Answers the simple question: is one path the root itself or somewhere below it? It is the shared containment test after paths have been resolved or otherwise prepared.

**Data flow**: It receives a path and a root. It compares them and checks whether the root appears among the path’s parents, returning true for inside and false for outside.

**Call relations**: `contained_file`, `contained_dir`, `contained_remove`, and `is_contained_regular` call this after they have a candidate path. It provides the common yes-or-no decision used by those larger safety checks.

*Call graph*: called by 4 (contained_dir, contained_file, contained_remove, is_contained_regular).


##### `_open_root`  (lines 433–437)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the root directory as a directory file descriptor, refusing roots that cannot be opened safely. A file descriptor is a stable operating-system handle to that directory.

**Data flow**: It receives a root path. It tries to open it with flags that require a directory and do not follow symlinks; on success it returns the descriptor, and on failure it raises `NonDirectoryAncestor`.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this before walking down into child directories. The descriptor it returns is the starting point for `_descend`.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 2 external calls (__init__, open).


##### `_descend`  (lines 440–453)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory component deeper using an already-open parent directory, while refusing symlinks and non-directories. It also closes the parent descriptor it leaves behind.

**Data flow**: It receives the current directory descriptor, the next path component, and the overall target path for error messages. It opens the child component as a directory without following symlinks, converts missing or unsafe components into containment errors, closes the old descriptor, and returns the child descriptor.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this repeatedly as they walk from the root to the target’s parent or directory. Together with `_open_root`, it is what pins the path component by component.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 5 external calls (__init__, __init__, __init__, close, open).


### Durability and observability
Preserves workflow state across upgrades and crashes while centralizing safe telemetry and logging.

### `core/src/ufo/harness/durability.py`

`io_transport` · `cross-cutting persistence and crash recovery`

DBOS stores workflow inputs, step results, and final errors in a database so work can resume after a crash. The hard part is that the program replaying that saved work may be a newer version than the one that wrote it. A normal Python pickle, which is Python’s built-in object-saving format, can restore a Pydantic model without running the model’s normal validation. Pydantic is a data-model library that knows about defaults and required fields. If a field was added later, a plain pickle may produce an object that is missing that field entirely, causing confusing failures during replay.

This file fixes that by defining `ReplaySafeSerializer`. When it sees a Pydantic `BaseModel`, it saves the model’s class plus its field values. When loading, it rebuilds the model through Pydantic validation, so new default fields are filled in and removed fields are ignored. It is like unpacking a stored form into today’s version of the form, instead of photocopying an old object exactly.

The file also contains `MOVED_MODULES`, a map from old Python module names to their current names. Pickled data records where classes lived when saved. If code moved, this map lets old recordings still find the right class today. Without this file, recovered workflows could fail after ordinary refactors or model changes.

#### Function details

##### `replay_safe_client`  (lines 176–180)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: This creates a DBOS database client that knows how to read and write this project’s replay-safe serialized data. It is meant to be the standard way the repository connects to DBOS, because data written with this serializer’s name needs the same serializer to be decoded later.

**Data flow**: It takes a system database URL as input. It builds a `ReplaySafeSerializer`, gives that serializer and the URL to `DBOSClient`, and returns the ready-to-use client. The database is not changed just by this function; it prepares the client so future reads and writes use the correct format.

**Call relations**: When code needs a DBOS client, this function is the entry point for constructing it safely. It hands off to `ReplaySafeSerializer` so DBOS can encode and decode saved workflow data instead of treating it as an unknown raw string.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 183–184)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: This rebuilds a saved Pydantic model using the model rules from the current code. It exists so replayed data can adapt to defaults and validation in today’s model class.

**Data flow**: It receives a model class and a dictionary of saved field values. It asks the model class to validate those values, which creates a fresh model object. The result is a current, validated model instance rather than a raw copy of the old object’s internal state.

**Call relations**: The custom pickler records Pydantic models in a way that points back to this function. Later, during deserialization, Python’s pickle machinery calls this rebuild path so old saved model data is turned into a proper current model.


##### `_ModelPickler.reducer_override`  (lines 188–191)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: This teaches Python’s pickling process a special rule for Pydantic models. Instead of saving them as frozen internal objects, it saves enough information to rebuild them cleanly later.

**Data flow**: It receives each object that the pickler is about to serialize. If the object is a Pydantic `BaseModel`, it returns instructions saying: save the object’s class and its current field dictionary, then rebuild it with `_rebuild` when loading. If the object is not a Pydantic model, it tells pickle to use its normal behavior.

**Call relations**: `ReplaySafeSerializer.serialize` uses `_ModelPickler`, so this method is consulted while workflow data is being written. It provides the important handoff to `_rebuild`, which is what makes later replay use current model validation.


##### `_CompatUnpickler.find_class`  (lines 195–196)

```
def find_class(self, module: str, name: str) -> object
```

**Purpose**: This helps old saved data find classes after code has been moved to new module paths. It makes deserialization tolerant of refactors that rename or relocate modules.

**Data flow**: It receives the module name and class name stored in the serialized data. Before looking up the class, it checks whether the old module name appears in `MOVED_MODULES`; if so, it replaces it with the current module name. It then lets the normal unpickling lookup continue and returns the class or object it found.

**Call relations**: `ReplaySafeSerializer.deserialize` uses `_CompatUnpickler` when loading stored data. During that loading process, this method sits at the point where pickle resolves class names, redirecting old names to current locations when needed.


##### `ReplaySafeSerializer.name`  (lines 202–203)

```
def name(self) -> str
```

**Purpose**: This reports the stable name DBOS uses to label data written by this serializer. That name is how DBOS knows which serializer should read the data later.

**Data flow**: It takes no meaningful input beyond the serializer object itself. It returns the constant serialization name used by this project. Nothing else is changed.

**Call relations**: DBOS calls this as part of its serializer interface. The name returned here ties database rows to `ReplaySafeSerializer`, so future clients can choose the right decoding behavior.


##### `ReplaySafeSerializer.serialize`  (lines 205–208)

```
def serialize(self, data: object) -> str
```

**Purpose**: This turns a Python object into a text string that can be stored in the database. It uses the custom Pydantic-safe pickling rules before converting the bytes into plain text.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, uses `_ModelPickler` to write the object into that buffer, then base64-encodes the bytes into a UTF-8 string. The returned string is suitable for storage in DBOS.

**Call relations**: DBOS uses this when it needs to persist workflow inputs, outputs, or errors. The function delegates the object-writing step to `_ModelPickler`, which applies the special rebuild rule for Pydantic models.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 210–211)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: This turns a stored text string back into a Python object. It uses the compatibility-aware unpickler so old module paths can still be resolved.

**Data flow**: It receives a base64 text string from storage. It decodes that string back into bytes, wraps the bytes in an in-memory stream, and asks `_CompatUnpickler` to load the original object. The output is the reconstructed Python object, with Pydantic models rebuilt through the recorded rebuild path when applicable.

**Call relations**: DBOS uses this when reading persisted workflow data for replay or recovery. The function hands the actual loading to `_CompatUnpickler`, which redirects old module names through `MOVED_MODULES` as classes are looked up.

*Call graph*: 3 external calls (__init__, b64decode, BytesIO).


### `core/src/ufo/harness/o11y.py`

`io_transport` · `startup and cross-cutting runtime observability`

Observability is how operators answer questions like “what is slow?”, “what failed?”, and “which workspace was affected?” This file sets up that visibility using OpenTelemetry, a common toolkit for collecting traces, metrics, and logs, and Datadog service checks for current health states.

At startup, it can connect the process to an OTLP collector, which is a receiver for OpenTelemetry data. After that, other code can open spans, emit logs, count events, record timings, and submit service checks. A span is a timed block of work, like a labeled segment on a trip map. Metrics count or measure important events, such as model calls or sandbox failures. Logs are structured records with named fields so they can be searched.

A major theme in this file is safety. Before fields are attached to logs or traces, likely sensitive keys such as prompts, content, credentials, secrets, and tokens are removed. Long third-party log messages are replaced with a short “dropped” notice, because some library messages can accidentally include huge prompts or private data. Error class labels are also kept to a fixed allowlist so one unusual exception cannot create an explosion of monitoring series.

Without this file, the system would be much harder to operate in production, and failures could either be invisible or reported with unsafe detail.

#### Function details

##### `init_service_checks`  (lines 289–305)

```
def init_service_checks(url: str | None, env: str | None, api_key: str | None) -> None
```

**Purpose**: Sets the destination for Datadog service checks, which report whether a named ongoing service is currently OK or critical. It also refuses unsafe partial setup, such as having an intake URL but no environment tag or API key.

**Data flow**: It receives a URL, environment name, and API key. If there is no URL, it clears the stored service-check destination so later submissions do nothing. If a URL exists, it validates the other required pieces and stores them in a small immutable intake object for later use.

**Call relations**: This is called during setup before service checks are emitted. It prepares the private `_service_check_intake` value that `emit_service_check` later reads when it decides whether and where to send a Datadog report.

*Call graph*: 1 external calls (__init__).


##### `init_o11y`  (lines 308–334)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Initializes the OpenTelemetry logging, tracing, and metrics pipeline. If no collector endpoint is configured, it still installs the log-size guard but leaves the rest as no-op defaults.

**Data flow**: It receives an optional collector base URL. It always first installs protection against oversized log messages. If a URL is present, it builds separate trace, metric, and log export URLs, creates OpenTelemetry providers for each signal, and registers them globally so later spans, metrics, and logs are exported.

**Call relations**: This is the main startup hook for this file. It calls `_guard_log_messages` for safety, `_otlp_signal_urls` to build valid exporter URLs, and `_bridge_warning_logs` so ordinary Python warnings from other libraries can also reach the log pipeline.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 337–349)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects ordinary Python warning-and-error logs to the OpenTelemetry log exporter. This catches important warnings from libraries or modules that do not use this file’s structured logging helpers.

**Data flow**: It receives an OpenTelemetry logger provider. It creates a logging handler that forwards warning-level and higher records, filters out this project’s own structured logger and OpenTelemetry’s internal logs, and attaches the handler to the root Python logger.

**Call relations**: It is called by `init_o11y` after the OpenTelemetry logger provider exists. It fills the gap between normal Python logging and the OpenTelemetry log pipeline, while avoiding loops from OpenTelemetry exporter failures.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 379–391)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Creates Python log records while blocking extremely long rendered messages. It is a safety net for third-party libraries that may accidentally log huge prompts or other private data.

**Data flow**: It receives the same arguments as the original Python log-record factory. It asks the wrapped factory to build the record, renders the message if possible, and either returns it unchanged or replaces the message with a short notice saying which logger and level were dropped and how large the message was.

**Call relations**: It is installed by `_guard_log_messages` as the global log-record factory. Every Python log record passes through it before any handler or exporter sees the record, and it relies on `_rendered_message` to inspect the final text safely.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 394–398)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the global protection that prevents oversized log messages from reaching stderr or monitoring systems. It avoids installing the guard twice.

**Data flow**: It reads the current Python log-record factory. If that factory is already this file’s guarded wrapper, it leaves it alone. Otherwise, it wraps the existing factory in `_GuardedRecordFactory` and registers the wrapper globally.

**Call relations**: It is called by `init_o11y` even when OpenTelemetry export is disabled. That makes the safety behavior independent from whether this process is sending telemetry anywhere.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 401–411)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely figures out what text a log record would produce. It avoids breaking logging if the record’s format string and arguments do not match.

**Data flow**: It receives one Python log record. If the message is already a plain string with no arguments, it returns it directly. Otherwise it asks the record to render itself, and if rendering raises an exception, it returns `None` instead of letting logging fail.

**Call relations**: It is used by `_GuardedRecordFactory.__call__` when deciding whether a library log message is too large. By catching rendering errors, it preserves Python logging’s normal forgiving behavior.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 414–420)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact OpenTelemetry HTTP endpoints for traces, metrics, and logs. The exporter needs full signal-specific URLs, not just the collector base address.

**Data flow**: It receives a base OTLP endpoint string. It removes any trailing slash, appends the trace, metric, and log paths, and returns the three complete URLs.

**Call relations**: It is called by `init_o11y` during OpenTelemetry setup. Its result is handed to the trace, metric, and log exporters so each signal posts to the collector path that understands it.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 423–428)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Finds the current workspace and turns it into metadata for logs and spans. This lets code inside a workspace scope be automatically tagged without every call site passing the workspace ID by hand.

**Data flow**: It reads the current workspace value from shared context. If there is no active workspace, it returns an empty dictionary. If there is one, it returns a small dictionary containing the workspace ID as text.

**Call relations**: It is used by `turn_span`, `span`, and `_emit_log`. Those functions merge this ambient workspace data into trace and log attributes so operators can filter events by workspace.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 431–437)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the currently active trace as a W3C `traceparent` header value. A `traceparent` is a standard text token that lets later work reconnect to the same trace.

**Data flow**: It creates an empty carrier dictionary, asks the OpenTelemetry trace-context propagator to inject the current trace into it, and returns the resulting `traceparent` value if one was created.

**Call relations**: Application code can call this before work crosses a queue or storage boundary. The saved value can later be passed into `turn_span` so the durable turn continues the same trace instead of starting an unrelated one.


##### `turn_profile`  (lines 440–448)

```
def turn_profile(subagent_profile: str | None, spawned: bool=False) -> str
```

**Purpose**: Chooses the small, bounded profile label used for a turn. This separates main user-facing turns, spawned agent turns, and configured subagent profiles in metrics and traces.

**Data flow**: It receives an optional subagent profile and a flag saying whether the turn was spawned. If a subagent profile is present, it returns that. Otherwise it returns `agent` for spawned turns or `main` for normal member-facing turns.

**Call relations**: It is called by `turn_span` when tagging a turn span. Other code can also use the same helper to keep metric labels consistent with trace labels.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 452–488)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Opens the main trace span for one durable turn. It marks the turn as a server-style entry point and ties it to the trace that admitted or spawned it when possible.

**Data flow**: It receives turn and conversation IDs, an optional saved traceparent, optional profile data, and an optional parent turn ID. It builds redacted attributes, adds ambient workspace data, extracts a parent trace context if supplied, opens a span named `turn`, yields it to the caller’s `with` block, and closes it when the block exits.

**Call relations**: This is used around turn execution. It calls `_ambient_scope` for workspace tagging, `turn_profile` for the profile label, and `redact_payload` before handing attributes to OpenTelemetry’s tracer.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 492–504)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Opens a smaller trace span for a named stage of work, such as a model call, tool call, or sandbox operation. It helps explain where time went inside a larger trace.

**Data flow**: It receives a span name, a span kind, and arbitrary attributes. It merges those attributes with the ambient workspace, redacts sensitive data, converts unusual values to strings, opens the span, yields it to the caller, and closes it after the caller’s block ends.

**Call relations**: Application code uses this as a context manager inside broader flows such as `turn_span`. It depends on `_ambient_scope` and `redact_payload`, then hands the cleaned attributes to OpenTelemetry.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `redact_payload`  (lines 507–513)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes sensitive fields from a dictionary before it is sent to logs or traces. It catches keys even if they use underscores, hyphens, or different letter case.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key and drops it if it looks sensitive, such as prompt, content, secret, token, or credentials. Kept values are passed through `redact_value`, and the cleaned dictionary is returned.

**Call relations**: It is a shared safety step used by `turn_span`, `span`, `_emit_log`, and recursively by `redact_value`. Anything that exposes structured attributes to observability should pass through this boundary.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 516–526)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Turns arbitrary Python values into JSON-like safe values for logs and traces. It keeps simple values, cleans nested containers, and stringifies unknown objects.

**Data flow**: It receives any value. Plain JSON-style values like strings, numbers, booleans, and `None` pass through. Dictionaries are cleaned through `redact_payload`, lists and similar sequences are cleaned item by item, and all other objects become strings.

**Call relations**: It is called by `redact_payload` for each field value. When it sees a nested mapping, it calls `redact_payload` again so sensitive keys are removed at every level.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 529–535)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured informational log event. It is for normal notable events that operators may want to search later.

**Data flow**: It receives an event name and named fields. It passes them to `_emit_log` with info-level severity, where workspace data is added, sensitive fields are removed, and the record is sent to both Python logging and OpenTelemetry logs.

**Call relations**: This is one of the public logging helpers used by the rest of the system. It delegates the common work to `_emit_log` so info, warning, and error logs behave consistently.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 538–540)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error log event. It is for failures that should stand out in monitoring and logs.

**Data flow**: It receives an event name and named fields. It passes them to `_emit_log` with error-level severity, which adds scope, redacts data, drops `None` fields, and emits the record.

**Call relations**: This public helper shares the same path as `log` and `warn` through `_emit_log`. Code that catches or reports failures can use it without repeating the redaction and export logic.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 543–545)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning log event. It is for expected but important conditions that are not full errors.

**Data flow**: It receives an event name and named fields. It sends them to `_emit_log` with warning-level severity, producing a redacted structured log record tied to the current workspace and trace if present.

**Call relations**: It is the warning-level sibling of `log` and `log_error`. All three helpers feed into `_emit_log` so operators see a consistent record shape across severities.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 548–577)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Creates a safe stack trace string for an exception without including exception messages. This gives operators the code path of a failure while avoiding leaked command output, tokens, or user text from exception messages.

**Data flow**: It receives an exception. It walks through the exception and its cause or context chain, records each exception class name and traceback frames, avoids loops, honors deliberately suppressed context, and returns the result. If the stack text is too long, it keeps the beginning and end and replaces the middle with an elision marker.

**Call relations**: Code that logs exceptions can use this helper to add a safe stack field. It calls Python’s traceback formatter for frames but intentionally does not format full exception messages.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 580–598)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the shared work for structured logs at all severities. It adds scope, redacts data, removes absent fields, and emits the record through both Python logging and OpenTelemetry.

**Data flow**: It receives an event name, OpenTelemetry severity details, a Python logging level, and raw fields. It merges in ambient workspace data, redacts sensitive content, drops fields whose value is `None`, writes to the `ufo` Python logger with the cleaned fields, and emits an OpenTelemetry log record with the same attributes.

**Call relations**: It is called by `log`, `log_error`, and `warn`. It relies on `_ambient_scope` and `redact_payload`, then hands the finished record to Python logging and the OpenTelemetry log API.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 601–614)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the `error_class` metric label within a known fixed set. This prevents one-off exception class names from creating too many monitoring series.

**Data flow**: It receives a dictionary of metric dimensions. If the `error_class` value is missing or is on the allowlist, it returns the dimensions unchanged. If the value is unknown, it returns a copy with `error_class` changed to `other`.

**Call relations**: It is called by `emit_metric` and `emit_histogram` before data reaches OpenTelemetry. That puts the guard at the common metric boundary instead of relying on every caller to remember it.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 617–627)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments one registered counter metric. A counter is a number that only goes up, such as “how many tool calls failed.”

**Data flow**: It receives a metric name, an amount, and string dimensions. It rejects unknown metric names, creates and caches the OpenTelemetry counter the first time that name is used, bounds the error class dimension, and adds the amount to the counter.

**Call relations**: This public helper is called by feature code when an event happens. It uses `_bounded_error_class` before handing the measurement to OpenTelemetry’s meter.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 630–652)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one timing or size observation for a registered histogram. A histogram groups many observed values so dashboards can show percentiles, such as typical and worst model latency.

**Data flow**: It receives a histogram name, a numeric value, and string dimensions. It rejects unknown histogram names and dimensions that were not declared for that histogram, creates and caches the OpenTelemetry histogram if needed, bounds the error class label, and records the value in milliseconds.

**Call relations**: Feature code uses this when it measures durations such as database waits, model calls, tool calls, or turn latency. It calls `_bounded_error_class` and then sends the observation through OpenTelemetry.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 655–667)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Adds or subtracts from a registered current-state metric. This is for values that can rise and fall, such as the number of active model rounds.

**Data flow**: It receives a metric name, a signed amount, and string dimensions. It checks that the metric and dimensions are allowed, creates and caches the OpenTelemetry up-down counter if needed, and applies the signed change.

**Call relations**: Runtime code calls this when a tracked state starts or ends. Unlike `emit_metric` and `emit_histogram`, it does not use `_bounded_error_class` because the registered up-down metrics here do not declare that dimension.

*Call graph*: 1 external calls (get_meter).


##### `emit_service_check`  (lines 670–702)

```
async def emit_service_check(name: str, status: int, message: str='', /, **tags: str) -> None
```

**Purpose**: Sends one Datadog service check status for a registered check. Service checks report current health, so a later OK status can clear an earlier critical alert.

**Data flow**: It receives a check name, numeric status, optional message, and string tags. It rejects unknown check names. If service checks were not configured, it returns without sending anything. Otherwise it builds a Datadog report with a stable host name, environment tag, and caller tags, posts it to the configured intake URL with the API key, and raises if Datadog rejects the request.

**Call relations**: This async helper is called by code that knows the current health of a service-like activity, such as source sync. It reads the setup made by `init_service_checks` and uses `httpx.AsyncClient` to send the report directly to Datadog because OpenTelemetry does not carry service checks.

*Call graph*: 1 external calls (AsyncClient).


### Sandbox network configuration
Defines the sandbox-facing cache and preview service settings used to constrain allowed network access.

### `core/src/ufo/harness/sandbox/cache.py`

`config` · `startup and sandbox setup`

Sandboxes often need to download code or packages from the public internet. Doing that directly can be slow, repeated, and harder to control. This file names the internal cache service that sits between a sandbox and selected public hosts, like a library desk that fetches approved books for readers instead of letting everyone wander into every archive themselves.

The cache host is fixed as cache.ufo.internal. For Git, only selected hosts are cacheable, currently github.com. The file can produce Git configuration that rewrites normal fetches from GitHub so they go through the cache service instead. Pushes are deliberately kept direct, because a cache should help read public code, not become part of writing code back to a remote repository.

The file also lists public package and download hosts that the proxy may route through the cache, such as npm, PyPI, crates.io, Go modules, RubyGems, Ubuntu, Debian, and GitHub asset hosts. These are allowlists: the cache is not a general tunnel to anywhere on the network.

Finally, it defines the environment variable name used for cache control and includes a parser for the cache daemon address. That parser is strict on purpose. If deployment configuration says the cache daemon is at a bad address, the system should fail clearly rather than silently running without the cache.

#### Function details

##### `cache_git_config`  (lines 46–54)

```
def cache_git_config() -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the Git settings that make fetches from cached Git hosts go through the internal cache. It also adds a matching push rule so pushes still go to the real origin rather than the cache.

**Data flow**: It reads the fixed cache host and the list of Git hosts that are allowed to be cached. For each host, it creates two Git configuration key-value pairs: one that rewrites read URLs toward the cache, and one that preserves direct push behavior. It returns all of those pairs as an immutable tuple, ready for another part of the system to apply to a sandbox's Git configuration.

**Call relations**: No direct caller is shown in the provided graph, but this function is the handoff point from this configuration file to sandbox setup code. When a sandbox needs Git caching, setup code can ask this function for the exact Git settings instead of rebuilding the rewrite rules itself.


##### `parse_cache_daemon`  (lines 57–66)

```
def parse_cache_daemon(value: str | None) -> tuple[str, int] | None
```

**Purpose**: Turns a deployment-provided cache daemon address into a usable host and port. It treats a missing value as 'no local cache daemon', but treats a malformed value as a real configuration error.

**Data flow**: It receives either a string such as 'host:port' or no value at all. If there is no value, it returns None. If there is a value, it splits it at the last colon, checks that a host and separator are present, converts the port text to a number, and returns the host and port together. If the format is wrong, or the port is not a number, it raises an error instead of guessing.

**Call relations**: No direct caller is shown in the provided graph, but this function fits into deployment or startup configuration loading. Code that reads the cache daemon setting can pass the raw text here and then either get a clean address to connect to, get None when caching is intentionally absent, or fail loudly when the deployment setting is broken.


### `core/src/ufo/harness/sandbox/preview.py`

`config` · `config load`

The preview service is a private helper used to render shared files and document reads. This file is the small shared “address card” for that service. It names the internal host that sandboxed code is allowed to request, and it defines the special placeholder authorization value used inside the sandbox.

The important safety idea is that the sandbox never receives the real preview token. Instead, it uses a harmless sentinel value, like a coat-check ticket. When the request goes through the egress proxy, the proxy recognizes the special preview host and swaps the sentinel for the real deploy token only for that destination. That keeps the secret out of the sandbox while still allowing legitimate preview work.

The file also includes a parser for the deploy setting that tells the system where the actual preview service is listening. If no preview service is configured, the parser returns nothing. If a value is present, it must be in `host:port` form. A bad value is treated as a configuration bug and raises an error, rather than silently disabling preview support or guessing what was meant.

#### Function details

##### `parse_preview_service`  (lines 16–25)

```
def parse_preview_service(value: str | None) -> tuple[str, int] | None
```

**Purpose**: This function turns the configured preview service address into a host and port pair the rest of the system can use. It also clearly rejects malformed deploy configuration so mistakes are caught early.

**Data flow**: It receives either a text value such as `example.internal:443` or no value at all. If there is no value, it returns `None`, meaning no preview service is configured. If there is a value, it splits it at the last colon, checks that a host was present, converts the port text into a number, and returns the result as `(host, port)`. If the address is missing the colon or host, it raises a `ValueError`; if the port is not a valid number, the integer conversion also fails.

**Call relations**: During configuration loading or deploy setup, other code can call this function when it needs to know whether a preview service exists and where to send traffic for it. The function does not contact the service itself; it only validates and translates the configured address before handing that usable host-and-port pair back to the caller.


### Work partitioning
Provides a small ordered chunking helper for grouping adjacent work that can run in parallel.

### `core/src/ufo/harness/tools.py`

`util` · `cross-cutting`

This file solves a common scheduling problem: some tasks can safely happen at the same time, but others must stand alone because they may depend on state, timing, or side effects. The helper here acts like someone sorting a line of errands into trays. If several errands in a row are safe to do together, they go into the same tray, up to a chosen size limit. If an errand is not safe to do in parallel, it gets its own tray, and the grouping starts fresh afterward.

The important promise is that the original order is preserved. The function does not rearrange items to make bigger batches. It only groups consecutive items when the caller says they are parallel-safe. This matters because many systems need both speed and predictability: they want to run safe work together, but not change the visible order of operations.

There is one guardrail: the batch size limit must be at least one. A limit of zero or less would make batching impossible, so the function raises an error right away. The result is an iterator, meaning it produces each group one at a time instead of building all groups eagerly.

#### Function details

##### `dispatch_segments`  (lines 4–23)

```
def dispatch_segments(items: tuple[ItemT, ...], *, parallel_safe: Callable[[ItemT], bool], limit: int) -> Iterator[tuple[ItemT, ...]]
```

**Purpose**: Splits a tuple of items into ordered groups for dispatch. Consecutive items that pass the caller's "parallel-safe" test are batched together up to the given limit, while unsafe items are yielded alone.

**Data flow**: It receives a fixed ordered list of items, a test function that answers whether each item is safe to group with other parallel work, and a maximum group size. It walks through the items from first to last, collecting safe items into a temporary group, yielding that group when it fills up or when an unsafe item appears. It outputs one tuple at a time: either a batch of safe items or a single unsafe item, and it does not change the original items.

**Call relations**: This is a standalone helper with no internal calls to other project functions. Code that needs to dispatch ordered work can call it first to turn one long sequence into smaller segments, then decide how to execute each segment: batched segments can be run in parallel, while single-item unsafe segments can be run carefully on their own.


### Untrusted content handling
Wraps outside text so models treat it as data rather than instructions.

### `core/src/ufo/harness/untrusted.py`

`util` · `cross-cutting`

Some text that reaches an agent comes from places the workspace does not trust, such as a web page or another outside source. The danger is that this text might say something like “ignore your previous instructions” and trick the model into treating it as a command. This file creates a clear “container” around that text, like putting a warning label and sealed wrapper around a suspicious package.

The wrapper has three parts: a notice explaining that the content is untrusted, an opening marker that names the source, and a closing marker. The content is placed between those markers. If the untrusted text itself contains the closing marker, the code changes that marker into harmless text. That matters because otherwise the outside content could pretend to end the protected section early and then place fake instructions after it.

The important idea is consistency. Tool results and background child-agent results both use this same function, so the system has one definition of what “walling off untrusted content” means. Without this file, different parts of the system might wrap outside content differently, leaving gaps or confusing the model.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: Wraps text from an untrusted source in a warning and special boundary markers. Someone uses it when outside content must be shown to an agent, but only as information to inspect, not as instructions to follow.

**Data flow**: It receives a source name and the raw content from that source. It builds a warning message, adds an opening untrusted-content marker, copies in the content after replacing any fake closing marker with safe escaped text, and then adds the real closing marker. The result is one string that can be passed to an agent while keeping the untrusted text visibly fenced off.

**Call relations**: This function is the shared doorway for untrusted text before it reaches an agent. The tool-result path uses it when a tool returns outside data, and the background child-agent hand-back flow uses it when returning a child’s untrusted output to a parent. By sending both flows through this function, the system avoids having two slightly different ideas of how to protect against untrusted instructions.
