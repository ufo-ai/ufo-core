# Turn Commit, Teardown, Recovery, and Cleanup  `stage-19`

This stage happens when a turn is ending, or when the system must recover from something that did not finish cleanly. Its job is to leave the workspace in a knowable state. It records final results, releases temporary resources like browsers or sandboxes, cancels work that should no longer keep running, and saves enough information for later review, replay, billing, or a follow-up turn.

The files here focus on one important part of that ending record: changed files. `workspace_changes.py` asks git, the version-tracking tool, what changed in the conversation’s workspace. This catches edits made by shell commands, file renames, and deletes, even when no tool message directly described them. It answers the practical question, “What did this turn leave behind?” `file_changes.py` supplies one shared limit for how long a recorded file path may be. That keeps file-change records consistent across the system. Together, they act like the checkout list at the end of a workshop session: note what was altered, keep the notes tidy, and make them durable for whoever comes next.

## Files in this stage

### Workspace Change Recording
Defines shared limits and records git-reported workspace file changes so completed turns retain durable evidence of modified, renamed, or deleted files.

### `core/src/ufo/tools/file_changes.py`

`config` · `cross-cutting`

This is a tiny but useful settings file. It contains one constant, `FILE_CHANGE_PATH_MAX_CHARS`, set to 4,096 characters. In plain terms, it says: when the system talks about a changed file, the file path should not be longer than this limit.

A limit like this matters because file paths can come from outside the program or from many different parts of a project. Without a shared maximum, one part of the system might accept very long paths while another part cannot store, display, or process them safely. Using one named value is like putting a height limit sign at the entrance to a tunnel: every driver sees the same rule before they enter.

The file does not perform any work by itself. It has no functions and does not read or write anything. Its job is to be imported by other code that needs to check, trim, validate, or document the maximum size of a file-change path.


### `core/src/ufo/turns/workspace_changes.py`

`domain_logic` · `turn end`

This file is like a checkout inspector for the workspace. At the end of a turn, it asks the sandbox to look at the parts of the workspace that may have been touched and then saves a compact report of changed files and patches in the database.

The important idea is that file changes belong to the workspace, not just to the chat messages. A tool result may say that a file was written, but git can also see things like deleted files, renamed files, or changes made by a shell command. Without this file, the system could lose track of those real workspace changes, especially after message history is compacted or replayed.

The file first works out which paths are worth checking. Writes and edits point to specific files. A bash command may touch anything, so it marks the workspace root. The recorder also keeps watching directories that were changed in the last scan, so a changed checkout stays visible until git says it is clean.

The scan result is represented by small data models: one changed path with its patch, and one full scan containing many such changes. The recorder stores this scan in a database row for the conversation that owns the sandbox. If two turns update the same workspace at the same time, it merges carefully: new answers replace the directories that were just scanned, while unrelated stored changes are kept.

#### Function details

##### `change_targets`  (lines 37–55)

```
def change_targets(calls: Iterable[ToolUseBlock]) -> tuple[str, ...]
```

**Purpose**: This function looks at tool calls from a turn and extracts the workspace paths that might have changed. It is used to decide where the later git-style scan should look, instead of scanning an arbitrary large workspace unnecessarily.

**Data flow**: It receives a sequence of tool-use records. For write and edit calls, it reads the file path, checks that the path belongs inside the workspace, and turns it into a workspace-relative path. For bash calls, it adds the workspace root because a shell command can change files without naming them in a structured way. It returns a tuple of unique paths in the order they were first seen.

**Call relations**: This is the path-finding step before recording changes. It relies on `workspace_path` to reject paths outside the workspace, and uses `PurePosixPath` to express the accepted path relative to the workspace root. The resulting targets are later carried by `WorkspaceChangeRecorder` when it decides what directories to scan.

*Call graph*: 2 external calls (PurePosixPath, workspace_path).


##### `WorkspaceChangeRecorder.record`  (lines 101–114)

```
async def record(self) -> None
```

**Purpose**: This is the main turn-end action that refreshes the stored workspace-change report. It checks the relevant directories, stores the result, and deliberately logs failures instead of breaking a turn that has already finished.

**Data flow**: It starts with the recorder’s sandbox, conversation IDs, workspace ID, and target paths. If there is no created sandbox and no target path, it does nothing. Otherwise it reads the previously recorded changes, computes the directories to watch, asks the sandbox for a fresh scan, and stores the merged result in the database. If anything goes wrong, it logs the error and leaves the old stored answer in place.

**Call relations**: This method ties the whole file together. It calls `recorded_workspace_changes` to get the current saved view, passes that to `_directories` to choose scan targets, calls `_scan` to ask the sandbox what changed, and finally calls `_store` to persist the result. Its fallback path uses `log` so scan problems are visible without making the completed turn fail.

*Call graph*: calls 4 internal fn (_directories, _scan, _store, recorded_workspace_changes); 1 external calls (log).


##### `WorkspaceChangeRecorder._directories`  (lines 116–129)

```
def _directories(self, recorded: WorkspaceChanges) -> list[str]
```

**Purpose**: This helper chooses which directories should be checked for changes. It combines the paths touched in the current turn with the paths that were already known to be changed, so the system keeps watching a dirty checkout until it becomes clean.

**Data flow**: It receives the last recorded scan. It takes the parent directory of every current target path and every previously recorded changed path, removes duplicates, sorts them, and caps the list at a fixed maximum. If too many directories were found, it logs how many were dropped. It returns the final list of directory paths to scan.

**Call relations**: `record` calls this after loading the existing stored scan. It uses `PurePosixPath` to find each path’s parent directory, and `log` to report when the safety cap cuts the list down. The returned directory list is handed directly to `_scan`.

*Call graph*: called by 1 (record); 2 external calls (PurePosixPath, log).


##### `WorkspaceChangeRecorder._scan`  (lines 131–136)

```
async def _scan(self, directories: list[str]) -> WorkspaceChanges
```

**Purpose**: This helper asks the sandbox to inspect the chosen directories and report file changes. It also checks that the sandbox’s answer has the expected shape before the rest of the system trusts it.

**Data flow**: It receives a list of workspace-relative directories. It sends those paths to the sandbox’s `ufo fs changes` operation. The sandbox returns raw data, which this function validates as a `WorkspaceChanges` object. If the data is malformed, it raises a clear runtime error.

**Call relations**: `record` calls this after `_directories` has chosen where to look. The result is then passed to `_store`. This function is the boundary between the Python recorder and the sandbox-side filesystem scanner.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 138–161)

```
async def _store(self, scanned: WorkspaceChanges, asked: frozenset[str]) -> None
```

**Purpose**: This helper saves a fresh scan in the workspace database while protecting against overlapping recorders. It makes sure one recorder does not accidentally erase changes found by another recorder scanning a different part of the same workspace.

**Data flow**: It receives the newly scanned changes and the set of directories that were actually asked about. It opens a workspace database transaction, creates the conversation-change row if it does not already exist, locks and reads the current saved scan, merges the fresh scan with the stored one, and writes the merged scan back. The database is changed; the function returns nothing.

**Call relations**: `record` calls this after `_scan` succeeds. Inside the transaction it uses SQL-building helpers to insert, select, lock, and update the row. It calls `_merged` to decide exactly which old entries survive and which fresh entries replace them.

*Call graph*: calls 1 internal fn (_merged); called by 1 (record); 5 external calls (model_dump, and_, select, update, workspace_tx).


##### `WorkspaceChangeRecorder._merged`  (lines 163–178)

```
def _merged(self, scanned: WorkspaceChanges, stored: WorkspaceChanges, asked: frozenset[str]) -> WorkspaceChanges
```

**Purpose**: This helper combines a fresh scan with the scan already stored in the database. Its rule is simple: trust the fresh scan for directories that were just checked, but keep old changes from directories this scan did not ask about.

**Data flow**: It receives the new scan, the previously stored scan, and the set of directories that were scanned this time. It collects the paths in the fresh scan, keeps old changes only when their parent directory was not scanned and they were not replaced by a fresh path, then joins fresh changes followed by kept old changes. It trims the result to the maximum allowed number of changes and sets the `truncated` flag if anything may have been left out.

**Call relations**: `_store` calls this while holding the database row lock, so the merge is based on a stable current value. It uses `PurePosixPath` to compare parent directories and returns a new `WorkspaceChanges` object for `_store` to write back.

*Call graph*: called by 1 (_store); 2 external calls (__init__, PurePosixPath).


##### `recorded_workspace_changes`  (lines 181–205)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: This function reads the last saved workspace-change scan for a conversation. It hides the detail that sub-conversations may share their parent’s sandbox, so callers get the scan for the workspace owner rather than only the exact conversation ID they passed.

**Data flow**: It receives a conversation ID. It opens a workspace database transaction, looks up the conversation’s sandbox-owning conversation ID if there is one, then reads the saved scan for that owner. If the conversation does not exist or no scan has been recorded yet, it returns the shared `NOTHING_CHANGED` value. Otherwise it validates the stored JSON-like data as a `WorkspaceChanges` object and returns it.

**Call relations**: `WorkspaceChangeRecorder.record` calls this before choosing directories, so the recorder can keep watching paths that were already known to be changed. It uses database select queries inside `workspace_tx` to find both the owning conversation and its stored change projection.

*Call graph*: called by 1 (record); 2 external calls (select, workspace_tx).

## 📊 State Registers Touched

- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-subagent-objectives` — The shared plan and delegation state for child agents, objectives, steps, evidence, attempts, and result delivery.
- `reg-workspace-change-store` — Saved per-conversation file-change summaries produced from workspace git state at turn teardown and later shown in portal slots or audits.
- `reg-sandbox-task-session-state` — Persistent/pollable state for long-running sandbox commands and REPL sessions that survive tool timeouts across tool calls.
- `reg-turn-created-reference-store` — Saved references created by a turn, linking its work to newly produced artifacts, objects, sources, sites, or other records for later display, replay, and cleanup.
