# Observability, safety gates, and generic infrastructure  `stage-20` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It is not one main workflow; it is the guardrails and dashboard used by many workflows while the system starts, serves requests, runs tools, and handles background work. The containment module is the main file safety gate. It checks untrusted paths before reading, writing, deleting, or walking folders, so tricks like symlinks cannot escape the allowed workspace. Workspace and agent-scope modules make sure code knows which workspace and which agent it is acting for, protecting secrets, billing, and database access from crossing boundaries. Observability records traces, metrics, structured logs, stack summaries, and service checks, with redaction to hide sensitive data. Image preview validation rejects corrupt, mislabeled, oversized, or costly images before they can cause trouble. Listings provides safe cursor-based paging, so long lists can be read in pieces without losing place. Tools groups ordered work into safe parallel chunks. Untrusted wraps outside text so agents treat it as information, not commands. File-change limits give one shared maximum path length. The Redis package marker simply makes that extension importable.

## Files in this stage

### File path containment
Central path-safety checks keep untrusted file operations confined to the intended root, including symlink-aware access.

### `core/src/ufo/harness/containment.py`

`domain_logic` · `cross-cutting file access`

A file name can look harmless and still point somewhere dangerous. For example, an agent could create a symlink, which is like a shortcut, inside its writable folder that secretly points to a host file outside the sandbox. If the system later reads or writes through that shortcut, it may leak or overwrite data it should never touch.

This module prevents that. It treats every untrusted path as suspicious and checks it in layers. First it rejects unusable names such as empty paths, `.` and `..`. Then it resolves the path’s real parent location and confirms it is still under the allowed root. After that it walks down the directory tree using operating-system file descriptors, which are stable handles to actual directories, and opens each component with “do not follow symlinks” protection. Finally, it checks the target file itself without following a final symlink.

The main result is a `ContainedFile`: a validated file name plus a pinned parent directory. Reads, writes, permission changes, replacement, and deletion then happen relative to that pinned parent, so a later path swap cannot redirect the operation. The file also offers lighter “lexical only” helpers for cases where this process cannot inspect the filesystem yet, such as paths inside a container. Without this file, many parts of the harness would have to invent their own incomplete path checks, and one missed symlink case could become a sandbox escape.

#### Function details

##### `contained_root`  (lines 88–101)

```
def contained_root(root: str | os.PathLike[str]) -> Path
```

**Purpose**: Checks that a root directory used for containment is real, exists, and is not itself a symlink. This matters because if the root is a symlink, all later “inside the root” checks could be fooled into protecting the wrong place.

**Data flow**: It receives a root path from the caller. It turns it into a `Path`, inspects the path itself without following symlinks, rejects missing or non-directory roots, and returns the root’s resolved real location.

**Call relations**: This is the first safety step for `contained_file`, `contained_dir`, and `contained_remove`. Those higher-level operations call it before they trust any path underneath the root.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `configured_root`  (lines 104–121)

```
def configured_root(root: str | os.PathLike[str], setting: str) -> Path
```

**Purpose**: Validates a root directory that came from operator configuration, while allowing that configured root itself to be a symlink. This supports normal deployment layouts where, for example, a configured storage folder points to a mounted disk.

**Data flow**: It receives a path and the name of the setting it came from. It follows the configured path, confirms it exists and is a directory, and returns the resolved real directory; errors mention the setting so the operator knows what to fix.

**Call relations**: This is a public root-checking helper for configuration-time use. Unlike `contained_root`, it is meant for trusted deployment choices rather than roots an agent might be able to replace.

*Call graph*: 4 external calls (__init__, __init__, Path, S_ISDIR).


##### `ContainedFile.lstat`  (lines 139–150)

```
def lstat(self) -> os.stat_result | None
```

**Purpose**: Looks at the validated target file without following a final symlink. It tells callers whether the target is absent or is a regular file, and refuses directories or special files.

**Data flow**: It reads the file named by `self.name` relative to the already pinned parent directory. If nothing is there it returns `None`; if a normal file is there it returns its file information; if a directory or non-regular object is there it raises an error.

**Call relations**: Callers use this after `contained_file` has produced a `ContainedFile`. It relies on the pinned parent descriptor created by the containment flow, so it checks the same directory that was validated earlier.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.mode`  (lines 152–167)

```
def mode(self, default: int) -> int
```

**Purpose**: Finds the permission bits that should be carried over when replacing a file. If the target does not exist, or is something like a symlink, it falls back to a caller-provided default instead of writing through the link.

**Data flow**: It receives a default permission mode. It checks the target name relative to the pinned parent without following symlinks; for a regular file it returns that file’s permission bits, for a missing or non-regular non-directory target it returns the default, and for a directory it raises an error.

**Call relations**: This is used on a `ContainedFile` after the path has already passed containment. It supports safe replacement flows such as `replace_bytes`, where a symlink at the target name must not steer the write.

*Call graph*: 4 external calls (__init__, stat, S_ISDIR, S_ISREG).


##### `ContainedFile.open_bytes`  (lines 169–179)

```
def open_bytes(self) -> BufferedReader
```

**Purpose**: Opens the contained target for streaming binary reads. This is useful for large files because callers can read gradually instead of loading the whole file into memory.

**Data flow**: It asks `_open_regular` to open the target safely as a real file. It wraps the returned low-level file descriptor in a Python binary file object and returns that object; if wrapping fails, it closes the descriptor so no file handle leaks.

**Call relations**: `ContainedFile.read_bytes` calls this when it wants a simple limited read. It delegates the strict “must be a real file, not a symlink or directory” check to `_open_regular`.

*Call graph*: calls 1 internal fn (_open_regular); called by 1 (read_bytes); 2 external calls (close, fdopen).


##### `ContainedFile.read_bytes`  (lines 181–184)

```
def read_bytes(self, limit: int) -> bytes
```

**Purpose**: Reads up to a caller-specified number of bytes from the contained file. It gives callers a simple safe read method when they do not need to stream manually.

**Data flow**: It receives a byte limit. It opens the file through `open_bytes`, reads at most that many bytes, closes the file automatically, and returns the bytes read.

**Call relations**: `ContainedFile.read_text` builds on this to read text. This method uses the same safe opening path as streaming readers by going through `open_bytes`.

*Call graph*: calls 1 internal fn (open_bytes); called by 1 (read_text).


##### `ContainedFile.read_text`  (lines 186–187)

```
def read_text(self, limit: int) -> str
```

**Purpose**: Reads a contained file as UTF-8 text, up to a limit. Invalid text bytes are replaced rather than causing the read to fail.

**Data flow**: It receives a byte limit. It calls `read_bytes`, decodes the returned bytes as UTF-8 with replacement for bad characters, and returns a string.

**Call relations**: This is the text-friendly wrapper over `ContainedFile.read_bytes`. It is used after containment has already pinned the target’s parent directory.

*Call graph*: calls 1 internal fn (read_bytes).


##### `ContainedFile.chmod`  (lines 189–190)

```
def chmod(self, mode: int) -> None
```

**Purpose**: Changes the permissions of the contained target file. It applies only the normal permission bits and does not follow a symlink at the target name.

**Data flow**: It receives a permission mode. It masks that mode down to standard permission bits and asks the operating system to apply it to the target name relative to the pinned parent directory.

**Call relations**: This operates on a `ContainedFile` produced by `contained_file`. It depends on the earlier containment work so the permission change is aimed at the validated directory entry.

*Call graph*: 1 external calls (chmod).


##### `ContainedFile.unlink`  (lines 192–196)

```
def unlink(self) -> None
```

**Purpose**: Removes the contained target if it exists. If the file is already gone, it treats that as success.

**Data flow**: It tries to delete `self.name` relative to the pinned parent directory. If deletion succeeds, the directory entry is gone; if the name is missing, nothing changes and no error is raised.

**Call relations**: This is a convenience operation on a `ContainedFile`. It uses the pinned parent supplied by `contained_file` so removal cannot be redirected by changing a path string later.

*Call graph*: 1 external calls (unlink).


##### `ContainedFile.replace_with`  (lines 198–200)

```
def replace_with(self, source: ContainedFile) -> None
```

**Purpose**: Atomically renames one contained file onto another contained target. “Atomically” means other readers should see either the old file or the new file, not a half-written mix.

**Data flow**: It receives another `ContainedFile` as the source. It asks the operating system to replace this target name with the source name, using each file’s pinned parent directory descriptor.

**Call relations**: This joins two already-contained file handles. It hands the final move to the operating system through `os.replace`, while keeping both sides relative to validated parent directories.

*Call graph*: 1 external calls (replace).


##### `ContainedFile.replace_text`  (lines 202–203)

```
def replace_text(self, text: str, mode: int) -> None
```

**Purpose**: Writes text to the contained target by replacing the file safely. It is the text version of the byte replacement operation.

**Data flow**: It receives text and a permission mode. It encodes the text into bytes, passes those bytes and the mode to `replace_bytes`, and leaves the target name pointing at the new contents.

**Call relations**: This is a small wrapper around `ContainedFile.replace_bytes`. It exists so callers that have text do not need to perform the encoding themselves.

*Call graph*: calls 1 internal fn (replace_bytes).


##### `ContainedFile.replace_bytes`  (lines 205–229)

```
def replace_bytes(self, data: bytes, mode: int) -> None
```

**Purpose**: Safely writes bytes by creating a temporary sibling file and then renaming it over the target. This avoids following a malicious symlink and avoids exposing a half-written file.

**Data flow**: It receives bytes and a permission mode. It creates a uniquely named staged file in the same pinned parent directory using exclusive creation and no-symlink protection, writes the data, sets permissions, renames the staged file onto the target, and cleans up any leftover staged name if something fails.

**Call relations**: `ContainedFile.replace_text` calls this after encoding text. This method performs the core safe-write operation for a `ContainedFile` that was produced by the containment guard.

*Call graph*: called by 1 (replace_text); 7 external calls (close, fchmod, fdopen, open, replace, unlink, uuid4).


##### `ContainedFile._open_regular`  (lines 231–247)

```
def _open_regular(self) -> int
```

**Purpose**: Opens the contained target as a real regular file and refuses missing files, directories, symlinks, and special files. It is the low-level safety check behind safe reads.

**Data flow**: It opens `self.name` relative to the pinned parent with no-symlink protection. It then checks the opened file descriptor itself; if the descriptor is not a regular file, it closes it and raises an error; otherwise it returns the descriptor.

**Call relations**: `ContainedFile.open_bytes` calls this before turning the descriptor into a Python file object. It is kept private because callers should normally use the higher-level read methods.

*Call graph*: called by 1 (open_bytes); 6 external calls (__init__, __init__, close, fstat, open, S_ISREG).


##### `contained_file`  (lines 251–285)

```
def contained_file(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create_parent: bool=False) -> Iterator[ContainedFile]
```

**Purpose**: This is the main entry point for safely reading or writing one file under a root directory. It returns a `ContainedFile` whose parent directory has been pinned so later operations cannot be redirected by path tricks.

**Data flow**: It receives an untrusted path, a root, and an option to create missing parent directories. It validates the root, roots relative paths under it, rejects unusable target names, resolves and checks the parent location, opens the root, descends through each parent component safely, optionally creates missing parents as it goes, yields a `ContainedFile`, and closes the directory descriptor when the caller is done.

**Call relations**: This function wires together `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. The `ContainedFile` methods then perform actual reads, writes, permission changes, or replacements through the pinned parent it provides.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 5 external calls (__init__, __init__, __init__, close, mkdir).


##### `contained_dir`  (lines 288–314)

```
def contained_dir(path: str | os.PathLike[str], root: str | os.PathLike[str], *, create: bool=False) -> Path
```

**Purpose**: Validates a directory path under a root and returns its canonical location. It is used when the caller needs a directory to enumerate or walk rather than a single file to open.

**Data flow**: It receives a path, a root, and an option to create missing directories. It validates the root, combines relative paths with that root, resolves the directory’s real location, confirms it stays inside the root, opens the root, safely descends through each resolved directory component, optionally creates missing components, closes the final descriptor, and returns the resolved path.

**Call relations**: Like `contained_file`, it uses `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. Unlike `contained_file`, it does not yield a long-lived `ContainedFile`; it proves the directory path and then releases the descriptors.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 3 external calls (__init__, close, mkdir).


##### `contained_remove`  (lines 317–344)

```
def contained_remove(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> None
```

**Purpose**: Safely removes one file or directory tree under a root. It avoids following symlinks while finding the parent, so a planted link cannot turn a cleanup into deletion outside the allowed area.

**Data flow**: It receives a path and root. It validates and roots the path, rejects unusable removal targets, checks the resolved parent is inside the root, safely descends to that parent, and then inspects the target without following symlinks. If the target is missing it does nothing; if it is a directory it removes the tree only on platforms where that is symlink-safe; otherwise it unlinks the target.

**Call relations**: This removal flow reuses the same containment building blocks as file and directory access: `contained_root`, `rooted`, `_inside`, `_open_root`, and `_descend`. It hands recursive directory deletion to `shutil.rmtree` only after checking that the platform implementation avoids symlink attacks.

*Call graph*: calls 5 internal fn (_descend, _inside, _open_root, contained_root, rooted); 8 external calls (__init__, __init__, __init__, close, stat, unlink, rmtree, S_ISDIR).


##### `contained_pattern`  (lines 347–364)

```
def contained_pattern(pattern: str, root: Path) -> str
```

**Purpose**: Checks and rewrites a glob pattern, which is a wildcard file-matching pattern, so it cannot start a search outside the root. This is needed because an absolute pattern can ignore the directory the caller thought it had scoped.

**Data flow**: It receives a pattern string and a root path. It rejects any pattern containing `..`, returns relative patterns unchanged, and for absolute patterns confirms they point inside the root before converting them to root-relative form.

**Call relations**: This helper covers cases where the process is checking a pattern before enumeration rather than opening one concrete file. It raises the same containment-style errors used by the rest of the module.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_relative`  (lines 367–392)

```
def contained_relative(path: str, root: str) -> str
```

**Purpose**: Performs a path-intent check using only the text of the path, without inspecting the real filesystem. It is for situations where this process cannot stat the destination yet, such as a path that will be written inside a container later.

**Data flow**: It receives a path and a root string. It treats relative paths as being under the root, simplifies `.` and `..` textually, refuses attempts to climb above the root, refuses a path that names the root itself, and returns the cleaned absolute path string.

**Call relations**: This is a lighter companion to `contained_file`. It proves only that the written path text intends to stay under the root; later real filesystem writes still need the stronger containment descent where possible.

*Call graph*: 3 external calls (__init__, __init__, PurePosixPath).


##### `contained_leaf`  (lines 395–403)

```
def contained_leaf(raw: str, fallback: str) -> str
```

**Purpose**: Extracts one safe filename component from a name supplied by an outside system. It drops any directory parts so a supplied name like `../../secret` becomes just a leaf name rather than a path.

**Data flow**: It receives a raw name and a fallback name. It treats backslashes as path separators too, takes only the final component, and returns the fallback if the result is empty, `.`, or `..`; otherwise it returns the leaf.

**Call relations**: This helper is used before joining an outside filename under a controlled root. It does not replace the full containment guard; callers still write the resulting leaf through the safe file path machinery.

*Call graph*: 1 external calls (PurePosixPath).


##### `is_contained_regular`  (lines 406–417)

```
def is_contained_regular(path: Path, root: Path) -> bool
```

**Purpose**: Answers whether an already-enumerated path is a regular file inside a root and was not reached through a symlink. It is a quick filter for listing results, not permission to open the file unsafely.

**Data flow**: It receives a path and root. It checks the path itself is a regular file, resolves the path strictly, rejects errors, confirms the resolved path is exactly the same as the original path, and then checks it lies inside the root.

**Call relations**: This helper uses `_inside` for the final containment check. A later read of any accepted result should still go through `contained_file`, because this function is for deciding what to list, not for performing the final safe open.

*Call graph*: calls 1 internal fn (_inside); 3 external calls (lstat, resolve, S_ISREG).


##### `rooted`  (lines 420–426)

```
def rooted(path: str | os.PathLike[str], root: Path) -> Path
```

**Purpose**: Combines a possibly relative path with the intended root. This prevents relative paths from being interpreted against the process’s current working directory by accident.

**Data flow**: It receives a path and a root. If the path is already absolute it returns it as a `Path`; otherwise it returns the path joined underneath the root.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this near the start of their flows. It makes sure all later checks are asking questions about the same intended location.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 1 external calls (Path).


##### `_inside`  (lines 429–430)

```
def _inside(path: Path, root: Path) -> bool
```

**Purpose**: Checks whether one path is the root itself or sits somewhere below it. It is the small shared containment test used after paths have been resolved or cleaned.

**Data flow**: It receives a path and a root. It returns `true` if the path equals the root or has the root among its parents; otherwise it returns `false`.

**Call relations**: `contained_file`, `contained_dir`, `contained_remove`, and `is_contained_regular` use this to make the final inside-versus-outside decision after their own preparation steps.

*Call graph*: called by 4 (contained_dir, contained_file, contained_remove, is_contained_regular).


##### `_open_root`  (lines 433–437)

```
def _open_root(root: Path) -> int
```

**Purpose**: Opens the containment root as a directory file descriptor, which is a stable operating-system handle to that directory. This pinned handle is the starting point for safe component-by-component descent.

**Data flow**: It receives a root path. It tries to open it as a directory with no-symlink-following flags, returns the descriptor on success, and raises a containment error if the root cannot be opened as a directory.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this before walking down into child directories. The descriptor it returns is then passed through `_descend`.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 2 external calls (__init__, open).


##### `_descend`  (lines 440–453)

```
def _descend(descriptor: int, part: str, target: Path) -> int
```

**Purpose**: Moves one directory level deeper from an already-open parent directory, without following a symlink. It also closes the parent descriptor it leaves behind, so handles do not pile up.

**Data flow**: It receives the current directory descriptor, the next path component, and the overall target path for error messages. It opens the child component as a directory relative to the current descriptor, rejects missing components and symlink or non-directory components, closes the old descriptor, and returns the child descriptor.

**Call relations**: `contained_file`, `contained_dir`, and `contained_remove` call this repeatedly while walking from the root to the target’s parent or directory. It is the step-by-step “stay on the proven path” mechanism at the heart of the containment guard.

*Call graph*: called by 3 (contained_dir, contained_file, contained_remove); 5 external calls (__init__, __init__, __init__, close, open).


### Observability backbone
Tracing, metrics, structured logging, stack summaries, redaction, and service checks provide shared operational visibility.

### `core/src/ufo/harness/o11y.py`

`io_transport` · `startup and cross-cutting during turn, job, model, tool, and service-check activity`

Observability is the project's “black box recorder.” This file sets up OpenTelemetry, a standard way to send traces, metrics, and logs to an outside collector. A trace shows the path of one unit of work over time, a metric counts or measures things, and a structured log is a search-friendly event with named fields.

The file does three important safety jobs as well. First, it redacts sensitive information before it leaves the process, such as prompt-like fields, tokens, credentials, authorization headers, and usernames inside URLs. Second, it protects the process from third-party libraries that accidentally log huge messages, by replacing oversized log text with a small note saying which logger emitted it. Third, it limits metric dimensions, especially error class names, so one unexpected exception type does not create an uncontrolled number of monitoring time series.

The main setup function, init_o11y, installs OpenTelemetry exporters when an OTLP endpoint is configured. Other functions are used throughout the program to open spans, emit logs, count events, record durations, and submit Datadog service checks. The file also reads the current workspace from ambient context, so callers do not have to pass workspace IDs by hand everywhere.

#### Function details

##### `init_service_checks`  (lines 296–312)

```
def init_service_checks(url: str | None, env: str | None, api_key: str | None) -> None
```

**Purpose**: Configures where Datadog service-check reports should be sent. A service check is a current health status, like “source sync is OK” or “source sync is critical,” rather than a simple count of events.

**Data flow**: It receives an intake URL, environment name, and API key. If there is no URL, it clears the service-check destination so later submissions do nothing. If a URL is present, it verifies that the environment and API key are also present, then stores those settings for emit_service_check to use later.

**Call relations**: This is normally called during startup configuration. It creates the small _ServiceCheckIntake record that emit_service_check later reads when it needs to send a status directly to Datadog.

*Call graph*: 1 external calls (__init__).


##### `init_o11y`  (lines 315–341)

```
def init_o11y(otlp_endpoint: str | None) -> None
```

**Purpose**: Turns on the tracing, metrics, and log export pipeline for the process. If no collector endpoint is configured, it still installs the oversized-log guard but leaves OpenTelemetry in its no-op default state.

**Data flow**: It receives an optional OTLP collector endpoint. It always installs the log-message guard, then, when an endpoint exists, builds separate URLs for traces, metrics, and logs, creates OpenTelemetry providers for each signal, and registers them globally.

**Call relations**: This is the main setup doorway for this file. It calls _guard_log_messages first, uses _otlp_signal_urls to build exporter destinations, installs OpenTelemetry providers, and then calls _bridge_warning_logs so ordinary Python warnings and errors can also reach the log pipeline.

*Call graph*: calls 3 internal fn (_bridge_warning_logs, _guard_log_messages, _otlp_signal_urls); 13 external calls (set_logger_provider, OTLPLogExporter, OTLPMetricExporter, OTLPSpanExporter, set_meter_provider, LoggerProvider, BatchLogRecordProcessor, MeterProvider, PeriodicExportingMetricReader, create (+3 more)).


##### `_bridge_warning_logs`  (lines 344–356)

```
def _bridge_warning_logs(logger_provider: LoggerProvider) -> None
```

**Purpose**: Connects normal Python warning-and-error logs to the OpenTelemetry log exporter. This catches important warnings from libraries or other modules that did not use this file's structured log helpers.

**Data flow**: It receives the OpenTelemetry logger provider. It creates a logging handler that only accepts warning-level and above records, filters out this project's own structured logger and OpenTelemetry's own exporter logs, and attaches the handler to the root logger.

**Call relations**: init_o11y calls this after the log exporter is installed. From then on, ordinary Python logging records at warning level or higher can be forwarded through the same observability pipeline.

*Call graph*: called by 1 (init_o11y); 2 external calls (getLogger, LoggingHandler).


##### `_GuardedRecordFactory.__call__`  (lines 386–398)

```
def __call__(self, *args: object, **kwargs: object) -> logging.LogRecord
```

**Purpose**: Creates Python log records while preventing very large library log messages from being written out. This protects logs and stderr from accidental megabyte-sized messages that may contain prompts or other sensitive content.

**Data flow**: It receives the same arguments the original logging record factory would receive. It asks the original factory to make a record, tries to render the message, and if the rendered text is too long, replaces the message with a short dropped-message notice and removes the original arguments.

**Call relations**: _guard_log_messages installs this object as the process-wide log record factory. Every later Python log record passes through it before any handler or exporter sees the record, and it uses _rendered_message to inspect the final message safely.

*Call graph*: calls 1 internal fn (_rendered_message).


##### `_guard_log_messages`  (lines 401–405)

```
def _guard_log_messages() -> None
```

**Purpose**: Installs the oversized-log protection if it is not already installed. It is designed to be safe to call more than once.

**Data flow**: It reads the current Python log record factory. If the factory is already a _GuardedRecordFactory, it leaves it alone. Otherwise, it wraps the current factory in _GuardedRecordFactory and makes that the new global factory.

**Call relations**: init_o11y calls this before setting up exports. Once installed, _GuardedRecordFactory.__call__ becomes part of every later logging path, including logs from third-party libraries.

*Call graph*: called by 1 (init_o11y); 3 external calls (__init__, getLogRecordFactory, setLogRecordFactory).


##### `_rendered_message`  (lines 408–418)

```
def _rendered_message(record: logging.LogRecord) -> str | None
```

**Purpose**: Safely gets the text of a logging record without letting bad formatting crash the caller. Python logging can delay string formatting until later, and this helper respects that behavior.

**Data flow**: It receives a LogRecord. If the record already has a plain string message with no arguments, it returns that string. Otherwise, it asks the record to format itself; if that raises an exception, it returns None instead of propagating the error.

**Call relations**: _GuardedRecordFactory.__call__ uses this helper before deciding whether a message is too large. This keeps the guard from changing Python logging's normal “report formatting errors later” behavior.

*Call graph*: called by 1 (__call__); 1 external calls (getMessage).


##### `_otlp_signal_urls`  (lines 421–427)

```
def _otlp_signal_urls(otlp_endpoint: str) -> tuple[str, str, str]
```

**Purpose**: Builds the exact HTTP URLs used for OpenTelemetry traces, metrics, and logs. The collector expects each kind of signal at its own path.

**Data flow**: It receives the base OTLP endpoint, removes any trailing slash, and returns three URLs: one ending in v1/traces, one in v1/metrics, and one in v1/logs.

**Call relations**: init_o11y calls this before creating the OpenTelemetry exporters. Those exporters then post to the returned URLs instead of accidentally posting to the collector base URL.

*Call graph*: called by 1 (init_o11y).


##### `_ambient_scope`  (lines 430–435)

```
def _ambient_scope() -> dict[str, str]
```

**Purpose**: Adds the current workspace ID to logs and spans when the code is running inside a workspace scope. This avoids making every caller pass the workspace ID manually.

**Data flow**: It reads current_workspace, which is ambient context set elsewhere in the program. If there is a workspace ID, it returns a small dictionary containing it as text; otherwise it returns an empty dictionary.

**Call relations**: turn_span, span, and _emit_log call this whenever they build trace attributes or log fields. It is the shared path that makes workspace tagging consistent across observability output.

*Call graph*: called by 3 (_emit_log, span, turn_span); 1 external calls (get).


##### `current_traceparent`  (lines 438–444)

```
def current_traceparent() -> str | None
```

**Purpose**: Captures the currently active trace identity in the standard W3C traceparent header format. This lets work that is queued now continue the same trace later.

**Data flow**: It creates an empty carrier dictionary, asks the OpenTelemetry trace-context propagator to inject the current trace information into it, and returns the traceparent value if one was produced.

**Call relations**: Other parts of the system can call this when admitting or queuing a turn. Later, turn_span can use that saved traceparent to connect the durable turn span back to the trace that admitted it.


##### `turn_profile`  (lines 447–455)

```
def turn_profile(subagent_profile: str | None, spawned: bool=False) -> str
```

**Purpose**: Chooses the profile label used for a turn in traces and metrics. The label keeps main member-facing work, spawned agent work, and named subagent profiles separate without using high-cardinality IDs.

**Data flow**: It receives an optional subagent profile and a spawned flag. If a subagent profile is provided, it returns that; otherwise it returns agent for spawned child turns or main for normal turns.

**Call relations**: turn_span calls this while building span attributes. Metric-emitting call sites can also use the same idea so trace labels and metric labels line up.

*Call graph*: called by 1 (turn_span).


##### `turn_span`  (lines 459–495)

```
def turn_span(turn_id: UUID, conversation_id: UUID, traceparent: str | None, subagent_profile: str | None, parent_turn_id: UUID | None) -> Iterator[Span]
```

**Purpose**: Opens the top-level trace span for one durable turn. A span is a timed block in a trace, like a stopwatch entry with labels attached.

**Data flow**: It receives turn and conversation IDs, an optional saved traceparent, optional subagent profile, and optional parent turn ID. It builds redacted attributes, includes ambient workspace information, extracts the parent trace context if present, opens a SERVER span named turn, yields it to the caller's code, and closes it when the caller leaves the context.

**Call relations**: This function calls turn_profile, _ambient_scope, and redact_payload before asking OpenTelemetry for a tracer. It is the bridge between queued turn records and the trace waterfall that operators inspect.

*Call graph*: calls 3 internal fn (_ambient_scope, redact_payload, turn_profile); 2 external calls (get_tracer, cast).


##### `span`  (lines 499–511)

```
def span(name: str, kind: SpanKind=SpanKind.INTERNAL, **attributes: object) -> Iterator[Span]
```

**Purpose**: Opens a smaller trace span for a named stage inside existing work, such as a model round, tool call, or sandbox step. It gives operators timing and context for a piece of a larger trace.

**Data flow**: It receives a span name, a span kind, and any number of attributes. It adds ambient workspace data, redacts sensitive fields, converts non-simple values to strings, opens the span as current, yields it, and closes it when the caller's block finishes.

**Call relations**: Call sites use this inside larger flows such as turns. It calls _ambient_scope and redact_payload, then hands the cleaned attributes to OpenTelemetry's tracer.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); 1 external calls (get_tracer).


##### `mark_span_outcome`  (lines 514–524)

```
def mark_span_outcome(opened: Span, error_class: str | None, message: str | None=None) -> None
```

**Purpose**: Marks a span as failed when the code reports an error as a return value instead of raising an exception. This keeps traces honest even for failure paths that do not throw.

**Data flow**: It receives an opened span, an optional error class, and an optional message. If there is no error class, it changes nothing. If there is one, it adds the class as a span attribute and sets the span status to error.

**Call relations**: Code that used span or turn_span can call this before the span closes. It uses OpenTelemetry span methods directly so the trace shows the same failure that metrics may count.

*Call graph*: 3 external calls (set_attribute, set_status, Status).


##### `redact_payload`  (lines 527–533)

```
def redact_payload(fields: Mapping[str, object]) -> dict[str, JsonValue]
```

**Purpose**: Removes sensitive fields from a dictionary and redacts sensitive-looking values inside the remaining fields. This is the main privacy gate before data is attached to logs or traces.

**Data flow**: It receives a mapping of field names to values. For each field, it normalizes the key by removing underscores and dashes and lowercasing it; if the key is sensitive, it drops the field. Otherwise it sends the value through redact_value and returns a new safe dictionary.

**Call relations**: _emit_log, span, turn_span, and redact_value call this. It is the shared sanitizer for structured observability data.

*Call graph*: calls 1 internal fn (redact_value); called by 4 (_emit_log, redact_value, span, turn_span).


##### `redact_value`  (lines 536–553)

```
def redact_value(value: object) -> JsonValue
```

**Purpose**: Cleans a single value so it is safe to send to logs or traces. It handles both obvious strings and nested structures like lists and dictionaries.

**Data flow**: It receives any Python object. Simple JSON values pass through, strings have credential-shaped text replaced, mappings are cleaned through redact_payload, sequences are cleaned item by item, and other objects are converted to strings.

**Call relations**: redact_payload calls this for every kept field. When redact_value sees a nested dictionary, it calls redact_payload again, so redaction applies at every depth.

*Call graph*: calls 1 internal fn (redact_payload); called by 1 (redact_payload).


##### `log`  (lines 556–562)

```
def log(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured informational event. It is for normal events that are useful to search and correlate with traces.

**Data flow**: It receives an event name and named fields. It passes them to _emit_log with info-level severity, where workspace tagging, redaction, and OpenTelemetry emission happen.

**Call relations**: Application code calls this for ordinary observability events. It is a thin, friendly wrapper around _emit_log.

*Call graph*: calls 1 internal fn (_emit_log).


##### `log_error`  (lines 565–567)

```
def log_error(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured error event. It is for failures that should be visible as errors in logs and observability tools.

**Data flow**: It receives an event name and named fields. It passes them to _emit_log with error-level severity so the event appears as an error in both Python logging and OpenTelemetry logs.

**Call relations**: Application code calls this on error paths. Like log and warn, it delegates the real work to _emit_log so redaction and workspace tagging stay consistent.

*Call graph*: calls 1 internal fn (_emit_log).


##### `warn`  (lines 570–572)

```
def warn(event: str, **fields: object) -> None
```

**Purpose**: Writes a structured warning event. It is for expected but notable situations that may deserve operator attention.

**Data flow**: It receives an event name and named fields. It sends them to _emit_log with warning-level severity.

**Call relations**: Application code calls this when something is not fatal but should stand out. It shares the same _emit_log path as info and error logs.

*Call graph*: calls 1 internal fn (_emit_log).


##### `formatted_stack`  (lines 575–605)

```
def formatted_stack(error: BaseException) -> str
```

**Purpose**: Builds a safe stack-trace summary for an exception without including the exception message. This avoids leaking secrets that may appear in messages while still showing where the error came from.

**Data flow**: It receives an exception. It walks through the exception and its cause or context chain, recording each exception class name and traceback frames. If the result is too long, it keeps the beginning and end and replaces the middle with an elision note.

**Call relations**: Error-logging call sites can use this to add a stack field to structured logs. It relies on traceback.format_tb for frame formatting but deliberately avoids formatting exception messages.

*Call graph*: 1 external calls (format_tb).


##### `_emit_log`  (lines 608–626)

```
def _emit_log(event: str, severity_number: SeverityNumber, severity_text: str, level: int, fields: Mapping[str, object]) -> None
```

**Purpose**: Performs the actual structured log emission for info, warning, and error helpers. It sends the same cleaned event to both Python logging and the OpenTelemetry logs pipeline.

**Data flow**: It receives an event name, severity values, a Python logging level, and fields. It adds ambient workspace data, redacts the combined fields, removes fields whose value is None, logs through the ufo Python logger, and emits an OpenTelemetry log record with the cleaned attributes.

**Call relations**: log, log_error, and warn all call this. It calls _ambient_scope and redact_payload first, then hands the result to Python logging and OpenTelemetry.

*Call graph*: calls 2 internal fn (_ambient_scope, redact_payload); called by 3 (log, log_error, warn); 2 external calls (getLogger, get_logger).


##### `_bounded_error_class`  (lines 629–642)

```
def _bounded_error_class(dimensions: dict[str, str]) -> dict[str, str]
```

**Purpose**: Keeps the error_class metric dimension under control. Monitoring systems become expensive and hard to query if every unexpected exception class creates a new permanent series.

**Data flow**: It receives a dictionary of metric dimensions. If the error_class value is missing or is in the approved set, it returns the dimensions unchanged. If the class is not approved, it returns a copy with error_class changed to other.

**Call relations**: emit_metric and emit_histogram call this before sending measurements. That makes the safety rule apply at the shared emission boundary instead of relying on every caller to remember it.

*Call graph*: called by 2 (emit_histogram, emit_metric).


##### `emit_metric`  (lines 645–655)

```
def emit_metric(name: str, amount: int=1, /, **dimensions: str) -> None
```

**Purpose**: Increments a named counter metric, such as “turns started” or “tool calls failed.” A counter records how many times something happened.

**Data flow**: It receives a registered metric name, an amount, and string dimensions. It rejects unknown metric names, creates and caches the OpenTelemetry counter if needed, bounds the error_class dimension, and adds the amount with those attributes.

**Call relations**: Call sites use this whenever a counted event occurs. It asks OpenTelemetry for a meter only the first time each metric name is used, then reuses the cached counter.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_histogram`  (lines 658–680)

```
def emit_histogram(name: str, value: int, /, **dimensions: str) -> None
```

**Purpose**: Records one duration or size-like observation for a registered histogram metric. A histogram lets operators ask questions like “what was the 95th percentile latency?”

**Data flow**: It receives a registered histogram name, a numeric value in milliseconds, and string dimensions. It rejects unknown histogram names and dimensions not declared for that histogram, creates and caches the OpenTelemetry histogram if needed, bounds the error_class dimension, and records the value.

**Call relations**: Call sites use this around timed work such as model rounds, tool calls, turns, or database waits. It uses _bounded_error_class before handing the observation to OpenTelemetry.

*Call graph*: calls 1 internal fn (_bounded_error_class); 1 external calls (get_meter).


##### `emit_up_down_metric`  (lines 683–695)

```
def emit_up_down_metric(name: str, amount: int, /, **dimensions: str) -> None
```

**Purpose**: Changes a current-state metric up or down, such as the number of active model rounds. Unlike a counter, this kind of metric can decrease.

**Data flow**: It receives a registered up-down metric name, a signed amount, and string dimensions. It rejects unknown names and undeclared dimensions, creates and caches the OpenTelemetry up-down counter if needed, and adds the signed amount.

**Call relations**: Call sites use this when entering and leaving ongoing activity. It talks directly to OpenTelemetry's meter and keeps the instrument cached for later emissions.

*Call graph*: 1 external calls (get_meter).


##### `emit_service_check`  (lines 698–730)

```
async def emit_service_check(name: str, status: int, message: str='', /, **tags: str) -> None
```

**Purpose**: Sends one Datadog service-check status for a registered check. This reports the current health of a named thing and can clear an alert by later sending OK for the same tags.

**Data flow**: It receives a check name, numeric status, optional message, and string tags. It rejects unknown check names, does nothing if service checks were not configured, builds a Datadog report with a stable host name and environment tag, posts it with the stored API key, and raises if Datadog rejects the request.

**Call relations**: After init_service_checks stores the intake settings, runtime code can call this to report health. It uses httpx.AsyncClient because service checks go directly to Datadog rather than through the OpenTelemetry collector.

*Call graph*: 1 external calls (AsyncClient).


### Harness utility wrappers
Small harness helpers organize ordered work safely and mark external content as untrusted data for agents.

### `core/src/ufo/harness/tools.py`

`util` · `cross-cutting`

This file solves a practical scheduling problem: sometimes a harness has a list of actions to perform, and some neighboring actions can be run in parallel while others must run alone. The helper here acts like a careful traffic controller. It lets safe cars travel in small groups, but sends risky or special cars through one at a time.

The main function, `dispatch_segments`, takes a fixed set of items, a test that says whether each item is safe to group with others, and a maximum group size. It walks through the items from first to last. Consecutive items marked as parallel-safe are collected into batches, but never larger than the given limit. When it reaches an item that is not parallel-safe, it first sends out any safe batch it has collected, then sends the unsafe item as its own one-item batch. At the end, it sends out any leftover safe batch.

The important behavior is that order is preserved. The function never moves an item earlier or later than its original place in the sequence. It only decides where to place the boundaries between batches. It also refuses a zero or negative batch limit, because that would make batching impossible and would hide a caller mistake.

#### Function details

##### `dispatch_segments`  (lines 4–23)

```
def dispatch_segments(items: tuple[ItemT, ...], *, parallel_safe: Callable[[ItemT], bool], limit: int) -> Iterator[tuple[ItemT, ...]]
```

**Purpose**: Splits an ordered tuple of items into smaller ordered groups for dispatch. Items that are safe to run in parallel can be grouped together up to a maximum size, while items that are not safe are yielded alone.

**Data flow**: It receives a tuple of items, a `parallel_safe` check that answers yes or no for each item, and a positive `limit` for the largest safe group. It walks through the items in order, builds batches of consecutive safe items, breaks those batches when they reach the limit or when an unsafe item appears, and yields each batch as a tuple. If the limit is less than one, it stops immediately by raising an error instead of producing misleading output.

**Call relations**: This is a helper meant to be called by higher-level harness code when it needs to dispatch work without changing the original call order. The caller supplies the safety rule, and `dispatch_segments` hands back ready-to-use groups that the caller can then run together or one at a time.


### `core/src/ufo/harness/untrusted.py`

`util` · `cross-cutting`

AI agents often receive text from places the project does not control, such as websites, tool results, or another process. That text might contain phrases like “ignore your previous instructions,” either by accident or on purpose. This file helps protect against that by putting such text behind a clear “wall.” Think of it like placing a suspicious letter inside an evidence bag: the agent can inspect what is inside, but the label says not to treat it as orders.

The file defines the exact warning message, the opening marker, the closing marker, and the safe replacement used if the outside text itself contains the closing marker. That last part matters: without it, hostile or unlucky content could pretend to end the protected section early, then place new text outside the wall where it might look like normal instructions.

The main function, `wall`, combines these pieces. It names the source, adds a warning, surrounds the content with `<untrusted-content>` tags, and escapes any fake closing tag inside the content. Keeping this in one small file prevents different parts of the system from inventing slightly different versions of “untrusted content,” which would make the safety boundary weaker and harder to reason about.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: `wall` turns outside text into a clearly labeled block of untrusted data. Someone uses it when content comes from beyond the workspace’s trust boundary, so the agent sees it as something to examine rather than something to obey.

**Data flow**: It takes a `source` name and the raw `content`. It writes a warning that names the source, adds an opening untrusted-content marker, replaces any real closing marker inside the content with a harmless escaped version, then adds the real closing marker at the end. The result is one safe string ready to be passed to an agent.

**Call relations**: This function is the shared doorway for external content before it reaches an agent. Tool-result paths and parent-wakeup flows for background child results can call on it so they all use the same warning, same boundary markers, and same escaping rule instead of each inventing their own wrapper.


### Runtime guardrails
Runtime infrastructure enforces agent and workspace scope, stable pagination, media preview validation, and shared file-path limits.

### `core/src/ufo/runtime/agent_scope.py`

`domain_logic` · `cross-cutting during agent-scoped runtime work`

This file provides a small but important piece of runtime safety: the “ambient” agent identity. Ambient means the identity is available to code running in the current flow without being passed as an argument to every function. It is like putting on a visitor badge when entering a secured room: while you are inside, other parts of the system can check who you are and which room you belong to.

The file defines an AgentScope, which records two IDs: the workspace ID and the agent ID. A workspace is the larger boundary, and an agent is bound inside it. The agent context is stored in a ContextVar, which is a Python tool for keeping per-task state safely, especially when many asynchronous tasks may run at once.

The agent function is used as a context manager: code runs inside `with agent(agent_id):`. It captures the current workspace, binds the given agent to it, and restores the previous state afterward. It also prevents silently switching to a different agent while one is already bound.

The agent_current function is the checkpoint. Code that needs an agent identity calls it. If no agent has been bound, or if the workspace no longer matches, it raises an error instead of guessing. Without this file, agent-owned capabilities could accidentally run as nobody, as the wrong agent, or across the wrong workspace boundary.

#### Function details

##### `agent`  (lines 28–38)

```
def agent(agent_id: UUID) -> Iterator[AgentScope]
```

**Purpose**: This function opens a temporary agent scope for a block of code. Someone uses it when they want all work inside that block to clearly belong to one agent in the current workspace.

**Data flow**: It takes an agent ID as input and reads the current workspace ID from the workspace runtime state. It combines those into an AgentScope, checks whether a different agent is already bound, and if not stores the scope in the current execution context. While the caller’s block runs, agent-aware code can read that scope. When the block finishes, even if it fails with an error, the previous agent context is restored.

**Call relations**: This is the entry point for creating an agent boundary. It calls `ufo.runtime.workspace.ws_current` to find the workspace that the agent belongs to, then creates an `AgentScope`. Later, code inside the block can call `agent_current` to retrieve the identity that this function placed in the context.

*Call graph*: 2 external calls (__init__, ws_current).


##### `agent_current`  (lines 41–48)

```
def agent_current() -> AgentScope
```

**Purpose**: This function returns the agent identity currently bound to the running code. It is used by agent-owned features that need to know which agent is acting, and it deliberately raises clear errors when that identity is missing or inconsistent.

**Data flow**: It reads the current AgentScope from the context variable. If nothing is there, it raises AgentUnbound with a message telling the caller to wrap the work in `with agent(agent_id):`. If an agent is present, it also reads the current workspace and compares it with the workspace stored in the agent scope. If they match, it returns the scope; if they do not match, it raises an error because the agent identity no longer belongs to the active workspace.

**Call relations**: This function is the companion to `agent`. Code that needs the active agent calls it during agent-scoped work. It relies on `ufo.runtime.workspace.ws_current` to verify the workspace boundary, and it uses `AgentUnbound` to report the specific mistake of asking for an agent when none has been bound.

*Call graph*: 2 external calls (__init__, ws_current).


### `core/src/ufo/runtime/listings.py`

`domain_logic` · `request handling`

Listings in this system are shown newest first. That sounds simple, but paging can go wrong if the list changes while someone is reading it. If the system used page numbers or offsets, a newly added row could push everything down and make the reader see the same row twice or miss one. This file avoids that by using keyset paging: each page is based on the actual position of a row, like placing a bookmark between items rather than saying “go to item number 20.”

The position is stored in a ListingCursor. It contains the row creation time and the row id, because two rows can share the same time and the id breaks the tie. The cursor also says which direction the reader wants to move: toward newer rows or older rows.

page_query prepares a database query so it asks for the right slice of rows, in the right order, and asks for one extra row to learn whether there is another page. page_of then turns those raw rows into a ListingPage: the visible rows plus optional cursors for the “newer” and “older” controls. If no cursor exists in one direction, the user is already at that end of the listing.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: This turns a cursor into a single text token that can be put into a web link or query string. Someone would use it when building the “older” or “newer” link for a listing page.

**Data flow**: It starts with a ListingCursor containing a creation time, an item id, and a direction. It writes the direction as either “newer” or “older,” turns the time into standard text, joins those pieces with a separator, and returns the finished token string.

**Call relations**: This is the outward half of cursor handling. page_of creates ListingCursor objects for page boundaries, and encode can then turn those boundary positions into link-friendly text for a client to send back later.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: This reads a cursor token that came back from a client and turns it into a ListingCursor the system can trust. If the token is missing pieces or contains an invalid date or id, it raises MalformedCursor instead of guessing.

**Data flow**: It receives a text token. It splits the token into direction, timestamp, and item id, checks that the direction is allowed, parses the timestamp into a datetime, and checks that the id is a valid UUID. If all checks pass, it returns a ListingCursor; if not, it reports the token as malformed.

**Call relations**: This is used when a listing endpoint receives a cursor from outside the system, such as the workspace memory surface. After decode turns the text back into a safe cursor object, that cursor can be passed into page_query and page_of to fetch and shape the requested page.

*Call graph*: called by 1 (workspace_memory); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: This prepares a database query for one page of a newest-first listing. It adds the correct ordering, applies the cursor boundary if there is one, and asks for one extra row so the caller can tell whether another page exists.

**Data flow**: It receives a database select query, an optional cursor, a page size limit, and the two database columns that define listing order: creation time and id. If there is no cursor, it asks for the newest rows. If the cursor asks for older rows, it selects rows before that position. If the cursor asks for newer rows, it temporarily reverses the ordering so the database can walk that direction. It returns the modified query; it does not run the query itself.

**Call relations**: This is the database-facing half of the paging flow. A listing feature builds its base query, passes it here, runs the returned query, and then gives the resulting rows to page_of so they can be turned into a page envelope with navigation cursors.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: This turns the rows returned from page_query into a ListingPage that is ready for the rest of the application to use. It keeps only the visible rows, detects whether more rows exist on either side, and creates the boundary cursors for navigation.

**Data flow**: It receives the fetched rows, the cursor that led to this page, the requested limit, a render function that converts each source row into the public row shape, and a position function that extracts the row’s creation time and id. It uses the extra fetched row, if present, to decide whether there is another page in the direction being walked. If the query was walking toward newer rows, it reverses the visible rows back into normal newest-first order. It returns a ListingPage containing rendered rows plus optional older and newer cursors.

**Call relations**: This is normally used after page_query. page_query shapes what the database returns; page_of interprets that result and packages it for callers. Inside it, page_of.at creates the ListingCursor objects that mark the page boundaries.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: This small helper creates a cursor for a specific source row at the edge of a page. It is used so the page can say, “start from this row next time if the user clicks older or newer.”

**Data flow**: It receives one source row and a direction flag. It calls the supplied position function to pull out that row’s creation time and item id, then builds and returns a ListingCursor with those values and the requested direction.

**Call relations**: This helper lives inside page_of because it depends on page_of’s position function. page_of calls it for the first visible row when making a newer cursor and for the last visible row when making an older cursor.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/media/image_previews.py`

`domain_logic` · `request handling`

Image previews are convenient, but they are also risky: a tiny-looking upload can be damaged, fake its file type, or expand into a huge amount of image data when opened. This file acts like a gatekeeper before the rest of the system trusts an image preview.

It supports common raster image types: GIF, JPEG, PNG, and WebP. First, it can guess the expected media type from a filename suffix, such as “.png” meaning “image/png”. Then, when preview bytes arrive as an asynchronous stream, it checks that the number of bytes exactly matches a signed claim called an ImagePreviewGrant. That grant says what type the image should be and how large it should be.

After the bytes are collected, the file uses Pillow, a Python image library, to inspect the image in a worker thread so the main async flow is not blocked. It verifies the container ending, confirms Pillow can parse the file, checks that the detected image format matches the signed media type, and walks through animation frames if present. It enforces limits on file size, width, height, number of frames, and total decoded pixels. Without these checks, the system could accept broken previews, mislabeled files, or images designed to waste memory and CPU.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function looks at a path or filename and decides whether it appears to be a supported raster image. It is a quick first guess based only on the file extension, not on the actual bytes inside the file.

**Data flow**: It receives a path string, extracts its suffix such as “.jpg” or “.png”, lowercases it, and looks it up in the table of supported image extensions. It returns the matching media type, such as “image/jpeg”, or returns nothing if the suffix is not recognized.

**Call relations**: This is the lightweight front-door check. Code that has a filename can call it before deeper validation to decide whether the file looks like a supported preview type. The stronger byte-level checks happen later in validated_image_preview and _ImagePreviewValidator.validate.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This function receives an image preview as a stream of byte chunks and returns the bytes only if the preview is safe and truthful. It checks that the size matches the signed grant and then asks the image validator to inspect the actual image contents.

**Data flow**: It takes an asynchronous stream of bytes plus an ImagePreviewGrant containing the claimed media type and claimed byte count. As chunks arrive, it counts them and rejects the preview if it grows past the claim or past the hard maximum. Once the stream ends, it rejects the data if the final size does not exactly match the claim. If the size is right, it joins the chunks into one byte string, validates the image in a background thread, and returns the original bytes if everything passes.

**Call relations**: This is the main public validation path for incoming preview data. It performs the streaming size checks itself, then hands the completed byte string to _ImagePreviewValidator.validate for the more expensive image parsing and safety checks. It uses a worker thread for that parsing so the async caller is not held up by CPU-heavy image work.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs the deep safety check on the image bytes. It confirms that the file is complete, readable, truly the claimed media type, and small enough to decode safely.

**Data flow**: It receives the full image bytes and the media type the image claims to be. First it checks simple container rules, such as whether a PNG has the expected ending marker. Then it opens the bytes with Pillow, treats decompression-bomb warnings as errors, verifies that the image structure is valid, and records the actual format Pillow sees. It opens the image again to walk through each frame, checking frame count, width, height, and total decoded pixels, and forcing each frame to load. If anything looks corrupt, unsafe, too large, or mislabeled, it raises InvalidImagePreview. If all checks pass, it returns nothing, meaning the image is accepted.

**Call relations**: validated_image_preview calls this after it has gathered the incoming stream and confirmed the byte count. This function then coordinates the lower-level container check in _ImagePreviewValidator._validate_container and the Pillow-based decoding checks. It is the final judge before the preview bytes are allowed to continue through the system.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function does quick format-specific completeness checks before the heavier image decoding step. It catches obvious truncated or malformed JPEG, GIF, PNG, and WebP files by looking for required markers in the raw bytes.

**Data flow**: It receives the image bytes and the claimed media type. For each supported type, it checks the small pieces of structure that should be present, such as a JPEG end marker, a GIF semicolon terminator, a PNG IEND chunk, or a WebP RIFF/WEBP header and length. If the bytes do not match the expected container shape, it raises InvalidImagePreview. Otherwise it returns nothing and lets deeper validation continue.

**Call relations**: _ImagePreviewValidator.validate calls this near the start, before asking Pillow to parse the image. This makes the validation stricter about incomplete files and gives a clear rejection point for previews whose outer container does not match their claimed type.

*Call graph*: 1 external calls (__init__).


### `core/src/ufo/runtime/tools/file_changes.py`

`config` · `cross-cutting`

This is a tiny file, but it captures an important guardrail: file paths reported as part of file changes should not be longer than 4,096 characters. A path is the text address of a file, like `src/app/main.py`. Without a shared limit, different parts of the system might make different assumptions about how long a path can be, which could lead to inconsistent validation, oversized messages, or hard-to-debug failures. Think of it like setting a maximum label size for packages in a warehouse: every station can trust that labels will fit where they need to go. The constant `FILE_CHANGE_PATH_MAX_CHARS` is likely used elsewhere when reading, validating, storing, or sending information about changed files. This file does not perform any work by itself; it simply names the limit in one place so other code can refer to it clearly.


### `core/src/ufo/runtime/workspace.py`

`orchestration` · `cross-cutting during request handling, jobs, model calls, credential lookup, and billing`

A workspace is the project or tenant whose data, credentials, and bill are being used. This file makes that workspace an ambient context, meaning code inside a block can ask “what workspace am I in?” without every function needing a workspace argument. The `ws(workspace_id)` context manager sets that boundary, and `ws_current()` retrieves it or fails loudly if nothing was set.

The file’s main job is safety. A model key is never fetched in the abstract; it is fetched through `ws_current().credential(...)` or `ws_current().model_credential(...)`, so the lookup is tied to one workspace. If the workspace has its own stored key, that is used. Otherwise the code falls back to platform environment variables. Some model calls can be routed to a specific member’s connected provider account, but only when `model_authority(...)` has explicitly said that member should pay for those specific models.

The file also records model usage. Code wraps provider calls in `billable_event()`, adds reported token usage to the event, and the charges are written to the workspace when the block exits. This matters because a provider may charge for an attempt even if the model output later cannot be used.

A notable piece is grant refresh. Some connected accounts store refreshable grants instead of raw API keys. If a grant is spent, this file lets only one caller refresh it while others wait, like giving one person the checkout ticket so the whole line does not buy the same item twice.

#### Function details

##### `model_authority`  (lines 71–84)

```
def model_authority(authority: ExecutionAuthority, models: frozenset[str]=frozenset()) -> Iterator[None]
```

**Purpose**: Temporarily marks which member’s connected model account should serve specific model calls. This prevents a member’s personal account from being used for unrelated background or workspace-wide work.

**Data flow**: It receives an execution authority, meaning the actor allowed to run something, and a set of model names. It stores those facts in a task-local context for the duration of the `with` block, then restores the previous value afterward.

**Call relations**: Code around a turn or subtask uses this when model calls should be paid for by a member’s own provider account. Later, `WorkspaceScope._slot_order` reads this context to decide whether to look first in the member’s credential slot or in the workspace’s normal slot.


##### `init_workspace_credentials`  (lines 90–94)

```
def init_workspace_credentials(store: CredentialStore | None) -> None
```

**Purpose**: Installs the credential store that workspace code will use to read and write saved secrets. It is normally called once during startup.

**Data flow**: It receives either a credential store object or `None`. It saves that value in a module-wide variable so later workspace methods know whether stored credentials are available; if it is `None`, lookups can only use platform environment defaults.

**Call relations**: Startup code calls this before requests or jobs need credentials. The methods on `WorkspaceScope` then rely on the stored value when looking up, rotating, or saving credentials.


##### `ResolvedModelClient.complete`  (lines 124–125)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Runs a model request through an already resolved model client. The surrounding object keeps the client together with the payer facts that billing must use.

**Data flow**: It receives a model request and passes it directly to the underlying client. The result is an asynchronous stream of model events, such as chunks of output or usage information.

**Call relations**: After some other code has chosen the correct credential and built a model client, this method is the simple pass-through used to perform the completion while preserving the resolved funding and payer metadata on the wrapper.


##### `BillableEvent.usage`  (lines 135–145)

```
def usage(self, model: str, usage: Usage, pricing: Pricing=CORE_PRICING, byok: bool=False) -> None
```

**Purpose**: Adds one provider-reported model usage record to a billable event. Code uses it whenever a model attempt consumed tokens that may need to be charged to the workspace.

**Data flow**: It receives the model name, the usage numbers, a pricing table, and whether the call used a customer-owned key. It appends those facts to the event’s internal list; nothing is written to the database yet.

**Call relations**: This is used inside `WorkspaceScope.billable_event`. Callers add usage as model providers report it, and `billable_event` later writes all collected usage to the workspace ledger when the surrounding block ends.


##### `WorkspaceScope.credential`  (lines 161–183)

```
async def credential(self, slot: str, env: str | None=None, model: str | None=None) -> str
```

**Purpose**: Gets a secret value for this workspace, such as an API key. It first tries the workspace’s stored credential, including member-routed credentials when appropriate, and falls back to platform environment variables if no stored value exists.

**Data flow**: It receives a credential slot name, an optional environment variable name, and an optional model name. It asks `_slot_order` which stored slots to try, reads the first one that exists, or reads a platform environment value through `deploy_env`; if all are missing, it raises `CredentialSlotUnset`.

**Call relations**: Credentialed code calls this through `ws_current()` so the lookup is tied to a bound workspace. It depends on `_slot_order` to avoid using a member’s personal account unless `model_authority` has explicitly allowed that model call.

*Call graph*: calls 1 internal fn (_slot_order); 2 external calls (__init__, deploy_env).


##### `WorkspaceScope.model_credential`  (lines 185–205)

```
async def model_credential(self, slot: str, env: str | None, model: str) -> ModelCredential
```

**Purpose**: Gets both the credential for a model call and the payer category attached to it. This is important because retries must not silently switch from one payer to another while pretending the billing decision stayed the same.

**Data flow**: It receives a slot, optional environment variable name, and model name. It tries stored candidate slots from `_slot_order`; a raw stored key is returned as key-funded, a usable grant is returned as plan-funded, and a spent grant is refreshed before being returned. If no stored credential exists, it falls back to an environment key and marks it platform-funded.

**Call relations**: Model-client setup calls this before making provider requests. It calls `_refreshed_credential` when a connected-account grant has already been spent, and uses `read_grant` to tell whether a stored value is a grant or a plain key.

*Call graph*: calls 2 internal fn (_refreshed_credential, _slot_order); 4 external calls (__init__, __init__, read_grant, deploy_env).


##### `WorkspaceScope._refreshed_credential`  (lines 207–236)

```
async def _refreshed_credential(self, store: 'CredentialStore', candidate: str, slot: str, stored: str, grant: Grant) -> ModelCredential
```

**Purpose**: Refreshes a spent connected-account grant safely, even when several model calls notice the spent grant at the same time. It protects against reusing a one-time refresh token, which could cause a provider to revoke the account.

**Data flow**: It receives the credential store, the stored slot being refreshed, the public slot name, the stored grant text, and the parsed grant. It tries to claim a short refresh lease by rotating the stored value; the winner refreshes with the provider and saves the new grant, while others wait and re-read until a usable value appears. It returns a model credential or raises `GrantRefusedRefresh` if no refresh succeeds in time.

**Call relations**: `WorkspaceScope.model_credential` calls this only when it finds a grant that is already spent. This helper calls the grant refresh routine outside of database transactions, then stores the new value through the credential store so later callers can use it.

*Call graph*: calls 1 internal fn (__init__); called by 1 (model_credential); 6 external calls (__init__, sleep, model_copy, time, read_grant, refreshed).


##### `WorkspaceScope.member_routed_call`  (lines 238–241)

```
def member_routed_call(self, slot: str, model: str) -> bool
```

**Purpose**: Answers whether a specific model call should first try the speaking member’s own credential slot. This is a quick way to know if the call is member-routed rather than purely workspace-routed.

**Data flow**: It receives a credential slot and model name. It asks `_slot_order` what slots would be tried; if there is more than one, the first is the member’s slot and the function returns true.

**Call relations**: Other model or billing code can use this to understand how a credential will be resolved. It relies entirely on `_slot_order`, which reads the current `model_authority` binding.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope.member_payer`  (lines 243–249)

```
def member_payer(self, slot: str, model: str) -> str | None
```

**Purpose**: Returns the stored credential slot that represents the member payer for a model call, if the call is actually routed through a member. If the call is not member-routed, it returns nothing.

**Data flow**: It receives a credential slot and model name. It asks `_slot_order` for the lookup order; when that order starts with a member-specific slot, it returns that slot name, otherwise it returns `None`.

**Call relations**: Billing or model-selection code can call this before or after credential lookup to identify the member-owned payer. It depends on the same routing decision used by `credential` and `model_credential`, so the reported payer matches the lookup path.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope._slot_order`  (lines 251–262)

```
def _slot_order(self, slot: str, model: str | None) -> list[str]
```

**Purpose**: Decides which credential slots should be tried, and in what order. It is the central rule that prevents a member’s connected account from being used unless the current model call is explicitly allowed to use it.

**Data flow**: It receives a base slot and maybe a model name. It reads the current model authority context; if the slot is member-routable, the model is included, and the authority is a member, it returns the member-specific slot first and the workspace slot second. Otherwise it returns only the workspace slot.

**Call relations**: `credential`, `model_credential`, `credential_is_stored`, `member_routed_call`, and `member_payer` all call this so they make the same routing choice. It uses `member_slot` to turn a normal provider slot into a member-specific slot name.

*Call graph*: called by 5 (credential, credential_is_stored, member_payer, member_routed_call, model_credential); 1 external calls (member_slot).


##### `WorkspaceScope.member_holds_own_model_key`  (lines 264–267)

```
async def member_holds_own_model_key(self, authority: ExecutionAuthority) -> bool
```

**Purpose**: Checks whether the given authority belongs to a member who has connected at least one model provider account. It is a yes-or-no convenience wrapper.

**Data flow**: It receives an execution authority. It asks `member_model_provider` for the first connected provider and returns true if one exists, false otherwise.

**Call relations**: Code that only needs to know whether a member has any usable personal model account can call this. It delegates the actual provider lookup to `member_model_provider`.

*Call graph*: calls 1 internal fn (member_model_provider).


##### `WorkspaceScope.member_model_provider`  (lines 269–275)

```
async def member_model_provider(self, authority: ExecutionAuthority) -> str | None
```

**Purpose**: Returns the first model provider that a member has connected, or nothing if they have connected none. This picks the primary provider for member-funded coding work.

**Data flow**: It receives an execution authority. It asks `member_model_providers` for all connected providers and returns the first item from that ordered list, or `None` if the list is empty.

**Call relations**: `member_holds_own_model_key` calls this for a simple true-or-false answer. The function itself relies on `member_model_providers` to inspect the member’s stored credential slots.

*Call graph*: calls 1 internal fn (member_model_providers); called by 1 (member_holds_own_model_key).


##### `WorkspaceScope.member_model_providers`  (lines 277–291)

```
async def member_model_providers(self, authority: ExecutionAuthority) -> tuple[str, ...]
```

**Purpose**: Finds every model provider account that the authority’s member has connected in this workspace. The order matters because the first provider is treated as the primary one.

**Data flow**: It receives an execution authority. It extracts the member id, then checks each member-routable provider slot in the credential store; every slot that exists adds that provider name to the result tuple. If there is no credential store or no member id, it returns an empty tuple.

**Call relations**: `member_model_provider` calls this when it needs the primary provider. This method uses `authority_member_id` to identify the member and `member_slot` to look for that member’s provider credentials.

*Call graph*: called by 1 (member_model_provider); 2 external calls (member_slot, authority_member_id).


##### `WorkspaceScope.credential_is_stored`  (lines 293–307)

```
async def credential_is_stored(self, slot: str, model: str | None=None) -> bool
```

**Purpose**: Checks whether a credential would come from a stored workspace or member secret rather than a platform environment default. This helps decide whether usage is already paid by the key holder instead of by the platform.

**Data flow**: It receives a slot and optional model name. It asks `_slot_order` which stored slots to try, then returns true as soon as one exists in the credential store; if none exist or no store is configured, it returns false.

**Call relations**: Billing and model code can use this as a companion to credential lookup. It follows the same routing rules as `credential`, so its answer reflects the same member-versus-workspace decision.

*Call graph*: calls 1 internal fn (_slot_order).


##### `WorkspaceScope.rotate_credential`  (lines 309–314)

```
async def rotate_credential(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Replaces a stored credential only if it still has the expected old value. This compare-and-swap style update prevents two writers from accidentally overwriting each other.

**Data flow**: It receives a slot, the expected current secret text, and the new secret text. If no credential store exists it returns false; otherwise it asks the store to rotate the workspace’s credential and returns whether the rotation succeeded.

**Call relations**: Credential refresh or administration flows use this when updating an existing stored secret. It works only on stored workspace credentials; platform environment defaults are outside the store and cannot be rotated here.


##### `WorkspaceScope.put_credential`  (lines 316–320)

```
async def put_credential(self, slot: str, plaintext: str) -> None
```

**Purpose**: Stores a new credential value for this workspace. It is for owner-authorized setup, such as saving an API key or connected account secret.

**Data flow**: It receives a slot name and plaintext secret. If no credential store is configured, it raises an error; otherwise it writes the secret into the store under this workspace and slot.

**Call relations**: Credential setup flows call this after authorization checks have already happened elsewhere. Later, `credential` and `model_credential` can read the saved value through the same workspace scope.


##### `WorkspaceScope.billable_event`  (lines 323–335)

```
async def billable_event(self) -> AsyncIterator[BillableEvent]
```

**Purpose**: Creates a block where model usage can be collected and then written to this workspace’s billing ledger when the block exits. It makes sure reported provider usage is recorded even if later processing fails.

**Data flow**: It creates a fresh `BillableEvent` and yields it to the caller. The caller adds usage records; when control leaves the block, the function opens a workspace database transaction and writes each collected usage record with `record_workspace_usage`.

**Call relations**: Model-call code wraps provider attempts in this context manager and calls `BillableEvent.usage` as usage arrives. On exit, this function hands the collected records to the billing accounting layer inside `workspace_tx`.

*Call graph*: 3 external calls (__init__, workspace_tx, record_workspace_usage).


##### `ws`  (lines 339–347)

```
def ws(workspace_id: UUID) -> Iterator[WorkspaceScope]
```

**Purpose**: Binds a workspace id as the current workspace for a block of code. This is the boundary that makes all later workspace-scoped calls inside the block use the same workspace.

**Data flow**: It receives a workspace id, stores it in the current workspace context, and yields a `WorkspaceScope` for that id. When the block ends, it restores the previous context value.

**Call relations**: Request handlers, turn runners, or job runners call this at their outer boundary. Inside the block, `ws_current`, workspace database transactions, credential lookups, and billing all see the same workspace id.

*Call graph*: 3 external calls (__init__, reset, set).


##### `ws_current`  (lines 350–356)

```
def ws_current() -> WorkspaceScope
```

**Purpose**: Returns the currently bound workspace scope. If no workspace has been bound, it raises an error instead of guessing or silently using the wrong workspace.

**Data flow**: It reads the current workspace id from context. If an id is present, it returns a `WorkspaceScope` for it; if not, it raises `WorkspaceUnbound` with a message telling the caller to wrap the work in `ws(workspace_id)`.

**Call relations**: Any code that needs credentials, billing, or workspace-scoped behavior calls this rather than passing workspace ids around everywhere. It depends on `ws` having been used earlier by the request, turn, or job boundary.

*Call graph*: 3 external calls (__init__, __init__, get).


### Extension package marker
The Redis hub extension package initializer exposes the package for import without adding runtime behavior.

### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a nameplate on a folder: it tells Python that the folder is meant to be treated as an importable package. This particular file is empty, so it does not define any functions, classes, settings, or startup behavior. Its value is structural. Without it, some Python tooling or older import setups might not recognize `ufo_ext_redis_hub` as a package, which could make imports from the Redis Hub extension fail or behave inconsistently. In other words, this file does not do the Redis work itself; it makes sure the surrounding folder can be found and used by the rest of the project.

## 📊 State Registers Touched

- `reg-effective-config` — The merged service settings that tell the process how this deployment should run.
- `reg-feature-flags` — The shared on/off switches that let the service enable or disable behavior at runtime.
- `reg-member-session-auth` — The signed tokens and browser/session identity state that prove who is making a request.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-egress-sandbox-policy` — The shared rules for sandbox network access, proxy behavior, internet permission, and exposed secrets.
- `reg-object-store-and-journal` — The shared workspace object records and change history for agents, tasks, memories, sites, and related items.
- `reg-telemetry-context` — The shared trace, metric, log, health, and redaction context used to observe work across the system.
- `reg-build-metadata` — The product, package, version, and build identity exposed to CLI/admin surfaces, health checks, and telemetry.
- `reg-shared-infra-clients` — Long-lived non-database infrastructure clients and connection pools such as Redis, HTTP, provider, and service clients shared by workers and request handlers.
- `reg-transcript-access-audit` — Durable audit records of privileged/admin reads of private member transcripts for compliance and safety review.
- `reg-egress-policy-cache-state` — Per-workspace egress-rule generation and cache-freshness state used by proxies to detect stale sandbox network-access rules.
- `reg-rate-limit-buckets` — Shared throttling counters, leases, and cooldown state for ingress, provider/model calls, connector actions, and background workers, separate from spend-cap accounting.
