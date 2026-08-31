# Teardown, cancellation, retry recovery, and resource cleanup  `stage-17`

This stage is the system’s clean-up and recovery area. It runs when a turn, request, job, or whole process is finishing, failing, being cancelled, or restarting after a crash. Its job is to leave the system in a safe, understandable state: save what must be remembered, stop work that should no longer run, release outside resources like browsers, terminals, sandboxes, or containers, and make retries safe so the same cleanup can happen more than once without causing damage.

The file `workspace_changes.py` handles one important piece of that story: it records what changed in a conversation’s workspace during a turn. It does this by scanning files in a git-like way, meaning it compares the workspace before and after to find added, edited, or removed files. This creates a durable record of “what this turn left behind.” That record remains useful even if detailed tool output is later shortened, compacted, or missing from the chat history.

## Files in this stage

### Teardown, cancellation, retry recovery, and resource cleanup
### `core/src/ufo/runtime/turns/workspace_changes.py`

`domain_logic` · `turn-end background refresh`

A conversation can change files in ways that are not fully captured by chat messages. For example, a shell command may rename a file, delete a directory, or modify many files without listing each one. This file keeps a separate, durable record of those workspace changes.

It first works out which parts of the workspace are worth checking. File tools like `write` and `edit` point to a specific path, while a `bash` command might change anything, so it watches the workspace root. It also keeps watching directories that were changed in the previous scan until a later scan says they are clean. This is like leaving sticky notes on shelves that were recently disturbed, then removing them only after checking the shelf again.

The main worker is `WorkspaceChangeRecorder`. At the end of a turn, it asks the sandbox’s file system helper for changes in the selected directories. The answer is validated into strict data shapes, then stored in the database. If two recorders update the same workspace around the same time, the stored results are merged carefully so one scan does not erase changes found by another. If scanning fails, the error is logged, but the turn is not failed; an old change report is considered better than no report.

#### Function details

##### `change_targets`  (lines 37–55)

```
def change_targets(calls: Iterable[ToolUseBlock]) -> tuple[str, ...]
```

**Purpose**: This function looks at the tool calls from a turn and picks the workspace paths that may need to be checked for file changes. It is used to narrow the later git-style scan to likely affected areas instead of scanning everything blindly.

**Data flow**: It receives a sequence of tool-use records. For `write` and `edit`, it reads the `file_path`, verifies that the path belongs inside the workspace, and stores it relative to the workspace root. For `bash`, it adds `.` because a shell command may alter the workspace without naming files in a structured way. Duplicate paths are removed while keeping the first-seen order, and the function returns the final tuple of target paths.

**Call relations**: This is an early filtering step before recording workspace changes. It relies on `workspace_path` to reject paths outside the allowed workspace and on `PurePosixPath` to produce clean relative paths that later scans can use.

*Call graph*: 2 external calls (PurePosixPath, workspace_path).


##### `WorkspaceChangeRecorder.record`  (lines 101–114)

```
async def record(self) -> None
```

**Purpose**: This is the top-level action that refreshes the stored change report for a conversation’s workspace. It is meant to run after a turn has finished, so the user-facing turn can complete even if this background scan has trouble.

**Data flow**: It starts with the recorder’s sandbox, conversation IDs, and target paths. If there is no created sandbox and no targets, it does nothing. Otherwise it loads the last recorded changes, decides which directories should be scanned, asks the sandbox for a fresh scan, and stores the merged result in the database. If anything goes wrong, it logs the failure instead of raising it further.

**Call relations**: This method ties the whole file together. It calls `recorded_workspace_changes` to learn what was known before, `_directories` to decide what to ask about, `_scan` to get the sandbox’s current answer, and `_store` to save it. When an error interrupts that story, it hands details to the logging system.

*Call graph*: calls 4 internal fn (_directories, _scan, _store, recorded_workspace_changes); 1 external calls (log).


##### `WorkspaceChangeRecorder._directories`  (lines 116–129)

```
def _directories(self, recorded: WorkspaceChanges) -> list[str]
```

**Purpose**: This helper decides which directories should be scanned this time. It combines newly touched paths with paths that were already reported as changed, so changed areas stay watched until they become clean.

**Data flow**: It receives the previously recorded change report. It takes each new target path and each previously changed file path, converts each one to its parent directory, removes duplicates, sorts the list, and caps it at a fixed maximum size. If too many directories were found, it logs how many were dropped. It returns the final list of directories to scan.

**Call relations**: `record` calls this after loading the previous scan. The result becomes the direct input to `_scan`, and later the same set is used by `_store` to know which old entries are safe to replace.

*Call graph*: called by 1 (record); 2 external calls (PurePosixPath, log).


##### `WorkspaceChangeRecorder._scan`  (lines 131–136)

```
async def _scan(self, directories: list[str]) -> WorkspaceChanges
```

**Purpose**: This helper asks the sandbox to report file changes for selected directories. It also checks that the sandbox’s answer has the expected shape before the result is trusted.

**Data flow**: It receives a list of workspace-relative directories. It sends those paths to the sandbox’s `ufo fs changes` command. The sandbox returns raw structured data, which is validated as a `WorkspaceChanges` object. A valid scan is returned; malformed data is turned into a clear runtime error.

**Call relations**: `record` calls this after `_directories` chooses what to inspect. Its returned `WorkspaceChanges` object is handed to `_store`, which makes it the new durable projection of workspace changes.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 138–161)

```
async def _store(self, scanned: WorkspaceChanges, asked: frozenset[str]) -> None
```

**Purpose**: This helper saves a new scan in the database without accidentally deleting another recorder’s work. It is careful because two turns sharing the same sandbox may finish and record changes at nearly the same time.

**Data flow**: It receives the freshly scanned changes and the set of directories that were scanned. It opens a workspace database transaction, creates the conversation-change row if it does not already exist, locks and reads the current stored scan, merges the fresh and stored data, then writes the merged result back. The database is changed; the function itself returns nothing.

**Call relations**: `record` calls this after `_scan` succeeds. Inside, it calls `_merged` to decide which old entries should survive beside the new scan, then writes the final answer through SQLAlchemy database operations.

*Call graph*: calls 1 internal fn (_merged); called by 1 (record); 5 external calls (model_dump, and_, select, update, workspace_tx).


##### `WorkspaceChangeRecorder._merged`  (lines 163–178)

```
def _merged(self, scanned: WorkspaceChanges, stored: WorkspaceChanges, asked: frozenset[str]) -> WorkspaceChanges
```

**Purpose**: This helper combines a fresh scan with the scan already stored in the database. Its job is to replace entries for directories that were just checked, while keeping entries from directories this scan did not cover.

**Data flow**: It receives the new scan, the stored scan, and the set of directories that were asked about. It keeps all fresh changes. From the old scan, it keeps only changes whose parent directory was not part of this scan and whose exact path was not already found fresh. It trims the final list to the maximum allowed number of changes and sets the `truncated` flag if information may have been left out.

**Call relations**: `_store` calls this while holding the database row lock. This makes it the decision point that protects concurrent scans from overwriting each other’s unrelated findings.

*Call graph*: called by 1 (_store); 2 external calls (__init__, PurePosixPath).


##### `recorded_workspace_changes`  (lines 181–205)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: This function reads the last stored workspace-change scan for a conversation. If the conversation is a subagent that shares a parent workspace, it resolves to the owning conversation so there is one shared answer for that workspace.

**Data flow**: It receives a conversation ID. It opens a workspace database transaction, looks up the conversation’s sandbox owner, then fetches the saved scan for that owner. If there is no conversation or no saved scan, it returns a shared “nothing changed” value. If a scan is found, it validates it as a `WorkspaceChanges` object and returns it.

**Call relations**: `WorkspaceChangeRecorder.record` calls this before deciding what to scan next. The previous result feeds `_directories`, so old changed paths continue to be checked until a later scan removes them.

*Call graph*: called by 1 (record); 2 external calls (select, workspace_tx).

## 📊 State Registers Touched

- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-workflow-claims` — The workflow attempt and run-claim state that prevents two workers from running the same turn, scheduled task, listener, or cleanup job at once.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-sandbox-handles` — The remembered sandbox or workspace handle for each conversation so tools can resume the same isolated files, terminals, browsers, and services.
- `reg-execution-environment` — The controlled runtime environment given to commands, files, terminals, browsers, and documents, including safe environment variables and containment rules.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-artifact-blob-store` — The shared file, blob, attachment, artifact, signed download, and media-preview storage used to publish and recover produced work.
- `reg-conversation-workspace-changes` — Durable git-like summaries of files added, edited, or removed inside a conversation workspace during a turn.
- `reg-background-runner-handles` — Process-local async task handles, wakeup queues, and scheduler loop state for live background workers distinct from their durable job records.
- `reg-durable-workflow-store` — Serialized durable workflow/checkpoint objects used to reload or resume long-running turns, jobs, and recovery work after crashes or code changes.
- `reg-sandbox-runtime-cache` — Built sandbox client/runtime image and reusable sandbox cache artifacts used when launching isolated execution environments.
