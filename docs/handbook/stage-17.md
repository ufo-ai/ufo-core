# Turn completion, delivery, cleanup, and teardown  `stage-17`

This stage is the system’s “wrap up and put things away” step. It happens after the assistant has done its work for a turn. The final answer is committed, the turn is marked as finished or paused, live viewers are updated, and any reply is sent back to the place that asked for it. At the same time, temporary resources such as browsers, sandboxes, terminals, locks, and child tasks are closed or released so they do not leak into later work. It also helps during service shutdown by cleaning up old leftover resources.

The replies file acts like a mail sorter. If the model includes a hidden “reply-to” tag, meaning “send this part privately to that message,” it detects the tag, routes the reply correctly, and removes the tag so users do not see internal control text. The workspace changes file acts like a change log. It stores a snapshot of files changed during the conversation, using git-style scanning, so the system can later report what work was done.

## Files in this stage

### Reply Delivery and Change Records
Final turn handling strips private reply markup, routes targeted replies, and preserves a snapshot of workspace changes for later inspection.

### `core/src/ufo/loop/replies.py`

`domain_logic` · `main loop and live response streaming`

This file solves a careful presentation problem. The model may write a hidden instruction-like span such as `<reply-to message="..."></reply-to>` to say, “send these words as a reply to that message.” Those words should be delivered as a reply, but the surrounding tag is not meant for people to read. Without this file, users could see raw markup, reply text could appear twice, or half-written tags could leak during streaming.

There are two paths. After a full round is complete, `marked_replies` scans the whole text, finds finished reply spans, records each one as a `MarkedReply`, and returns a cleaned version of the text with the tags removed. Invalid message IDs are allowed, but they become `None` rather than crashing the system.

During live streaming, text arrives in chunks, and a tag can be split across chunk boundaries. `ReplyRedaction.feed` works like a cautious curtain: it lets ordinary text through, but holds back anything that might be part of a reply tag until it knows whether it is safe. Text inside a reply span is withheld from the live stream because it will be delivered separately later. Unfinished or malformed reply markup is stripped rather than shown.

#### Function details

##### `marked_replies`  (lines 42–51)

```
def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]
```

**Purpose**: This reads a completed model response and extracts any closed reply-to spans from it. It also returns the same response with the reply markup removed, so the stored conversation keeps the spoken words but not the hidden control tags.

**Data flow**: It takes the full text of a completed round. It searches for each complete `<reply-to ...>...</reply-to>` span, removes any reply markup inside that span, trims the spoken text, and converts the named message value into a UUID when possible. It returns two things: a tuple of `MarkedReply` records for the closed spans, and the original text with all reply tags stripped out.

**Call relations**: This is used after the model has finished a round, when the system can safely inspect the whole response at once. For each reply span it asks `_named_message` to interpret the message name, then packages the result into `MarkedReply` objects for the later delivery path.

*Call graph*: calls 1 internal fn (_named_message); 1 external calls (__init__).


##### `_named_message`  (lines 54–58)

```
def _named_message(named: str) -> UUID | None
```

**Purpose**: This small helper tries to turn the message name written in a reply tag into a real UUID, which is a standard unique identifier. If the name is not a valid UUID, it returns `None` instead of stopping the whole reply extraction.

**Data flow**: It receives the raw string from the `message="..."` part of a reply tag. It trims extra space and tries to parse it as a UUID. The output is either the parsed UUID or `None` if the text was not a valid identifier.

**Call relations**: It is called by `marked_replies` while building each `MarkedReply`. This keeps the main scanning function simple: it can record a reply even when the target message reference is malformed.

*Call graph*: called by 1 (marked_replies); 1 external calls (UUID).


##### `ReplyRedaction.feed`  (lines 77–99)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This filters one incoming live text chunk and returns only the part that is safe to show immediately. It hides reply spans and prevents partial tags from leaking when a tag is split across multiple streamed chunks.

**Data flow**: It receives a new chunk of streamed text and appends it to any text it was already holding back. If it is inside a reply span, it looks for the closing tag and withholds everything until that tag appears. If it is outside a reply span, it publishes normal text, enters hidden mode when it sees a valid opener, and keeps possible unfinished tag fragments in reserve. It returns the cleaned text that can be shown now, while updating its own `held` and `inside` state for the next chunk.

**Call relations**: This is used during live output, before the full round is complete. It calls `_growing_suffix` when it is waiting for a possible closing tag, and `_settled_chars` when it needs to decide how much ordinary-looking text can be safely released. After it has chosen what to publish, it strips any remaining reply markup as a final safety pass.

*Call graph*: calls 2 internal fn (_growing_suffix, _settled_chars); 1 external calls (find).


##### `_growing_suffix`  (lines 102–107)

```
def _growing_suffix(text: str, token: str) -> str
```

**Purpose**: This finds the tail end of some text that might become a specific token if more characters arrive later. In this file, it is used to keep just enough text to recognize a closing reply tag that may be split across chunks.

**Data flow**: It receives the current held text and a target token, such as `</reply-to>`. It checks the end of the text for the longest suffix that matches the beginning of that token. It returns that suffix, or an empty string if no ending could grow into the token.

**Call relations**: It is called by `ReplyRedaction.feed` while the stream is inside a hidden reply span and no full closing tag has arrived yet. Its result tells the redactor what small fragment to keep for the next chunk and what can be discarded.

*Call graph*: called by 1 (feed).


##### `_settled_chars`  (lines 110–119)

```
def _settled_chars(text: str) -> int
```

**Purpose**: This decides how much currently held text is definitely ordinary prose and can be published now. It is cautious around a trailing `<`, because that character might be the start of a reply tag once the next chunk arrives.

**Data flow**: It receives held text from the live stream. It looks for the last `<` character and checks whether the remaining tail could still grow into an opening or closing reply tag. It returns a character count: everything before that point is safe to publish, while the uncertain tail should be kept for later.

**Call relations**: It is called by `ReplyRedaction.feed` when the stream is not currently inside a reply span. It helps the live redactor avoid showing half of a tag, while still allowing normal text to appear promptly.

*Call graph*: called by 1 (feed).


### `core/src/ufo/workspace_changes.py`

`domain_logic` · `turn-end background refresh`

A conversation can change files in ways that are not fully captured by the message history. For example, a shell command can rename or delete files, and replaying the text of a tool result may not tell you the real state of the checkout. This file solves that by asking the sandbox filesystem to scan likely affected directories and then storing the answer in the database.

The flow starts by finding scan targets from tool calls. File-writing tools name specific paths, while a shell command is treated as able to change the workspace root. At the end of a turn, `WorkspaceChangeRecorder` looks up the last saved scan, adds the old changed directories to the new targets, and scans them again. This is like keeping a watchlist: once a directory is known to have changes, it stays watched until a later scan says it is clean.

The scan result is stored as structured data: each changed path has a patch, plus flags that say whether the list or patch text was cut short. The database update is careful about concurrent turns sharing the same sandbox. If another recorder saved changes for a directory this recorder did not scan, those changes are kept instead of overwritten. If scanning fails, the file logs the problem but does not fail the already-finished turn; an old answer is considered better than no answer.

#### Function details

##### `change_targets`  (lines 37–55)

```
def change_targets(calls: Iterable[ToolUseBlock]) -> tuple[str, ...]
```

**Purpose**: This function reads a batch of tool calls and picks the workspace paths that may need to be checked for file changes. It focuses on file edits and writes, and treats any shell command as possibly changing the workspace root.

**Data flow**: It takes tool-call records as input. For `write` and `edit` calls, it reads the `file_path`, checks that the path is inside the workspace, and converts it to a workspace-relative path. For `bash` calls, it adds `.` to mean the workspace root. Invalid or unrelated calls are ignored. It returns the discovered paths as a tuple, with duplicates removed while keeping first-seen order.

**Call relations**: This is the first filtering step before a later recorder decides where to scan. It relies on `workspace_path` to reject paths outside the workspace and on `PurePosixPath` to express the final path relative to the workspace root.

*Call graph*: 2 external calls (PurePosixPath, workspace_path).


##### `WorkspaceChangeRecorder.record`  (lines 101–112)

```
async def record(self) -> None
```

**Purpose**: This is the main turn-end routine that refreshes the saved file-change view for a conversation. It gathers the previous scan, decides what directories still need watching, scans the sandbox, and stores the merged result.

**Data flow**: It starts with the recorder's sandbox, conversation id, workspace id, and target paths. It reads the last recorded changes for the conversation, turns those plus the new targets into directories, asks the sandbox to scan those directories, and saves the result. If anything goes wrong, it logs the failure and leaves the previous database record untouched.

**Call relations**: This function drives the whole file-change refresh. It calls `recorded_workspace_changes` to get the old state, `_directories` to build the watchlist, `_scan` to ask the sandbox filesystem what changed, and `_store` to persist the result. On failure it reports the problem through the logging system instead of interrupting the completed turn.

*Call graph*: calls 4 internal fn (_directories, _scan, _store, recorded_workspace_changes); 1 external calls (log).


##### `WorkspaceChangeRecorder._directories`  (lines 114–127)

```
def _directories(self, recorded: WorkspaceChanges) -> list[str]
```

**Purpose**: This helper turns individual changed file paths into the directories that should be scanned. It also keeps the scan from becoming too broad by capping the number of directories.

**Data flow**: It takes the last recorded workspace changes. It combines the recorder's new target paths with paths from the old recorded changes, then keeps only each path's parent directory. It sorts the directory list, trims it if it exceeds the configured maximum, logs how many were dropped, and returns the final list.

**Call relations**: `record` calls this after loading the previous scan and before scanning the sandbox. It uses `PurePosixPath` to find parent directories and the logger to note when the watchlist was shortened.

*Call graph*: called by 1 (record); 2 external calls (PurePosixPath, log).


##### `WorkspaceChangeRecorder._scan`  (lines 129–134)

```
async def _scan(self, directories: list[str]) -> WorkspaceChanges
```

**Purpose**: This helper asks the sandbox filesystem for the actual list of changed files under selected directories. It also checks that the sandbox returned data in the shape this application expects.

**Data flow**: It receives a list of workspace-relative directories. It sends those paths to the sandbox using the `changes` operation. The raw result is then validated into a `WorkspaceChanges` object. If the result is malformed, it raises a clear runtime error instead of letting bad data spread.

**Call relations**: `record` calls this after deciding which directories to inspect. Its output is passed straight to `_store`, which writes the scan into the database.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 136–159)

```
async def _store(self, scanned: WorkspaceChanges, asked: frozenset[str]) -> None
```

**Purpose**: This helper saves a new scan in the database without accidentally deleting changes written by another recorder at the same time. It creates the row if needed, locks the current row, merges old and new data, and writes the merged scan back.

**Data flow**: It takes the freshly scanned changes and the set of directories that were actually scanned. Inside a database transaction, it inserts an initial row if one does not exist, reads the current stored scan while locking that row, validates it, merges it with the new scan, and updates the row with the merged JSON data.

**Call relations**: `record` calls this after `_scan` succeeds. This function calls `_merged` to decide which old entries should survive. It uses `workspace_tx` for the database transaction and SQLAlchemy helpers to insert, select, lock, and update the conversation-change row.

*Call graph*: calls 1 internal fn (_merged); called by 1 (record); 5 external calls (model_dump, and_, select, update, workspace_tx).


##### `WorkspaceChangeRecorder._merged`  (lines 161–176)

```
def _merged(self, scanned: WorkspaceChanges, stored: WorkspaceChanges, asked: frozenset[str]) -> WorkspaceChanges
```

**Purpose**: This function combines a fresh scan with the previously stored scan in a concurrency-safe way. It replaces entries from directories that were just scanned, but keeps old entries from directories this scan did not look at.

**Data flow**: It receives the new scan, the stored scan, and the set of directories that were scanned. It records all fresh changed paths, then keeps stored changes only when their parent directory was not scanned and they are not duplicated by the fresh scan. It appends kept old changes after fresh ones, cuts the list to the maximum allowed size, and sets the `truncated` flag if anything may have been omitted.

**Call relations**: `_store` calls this while holding the database row lock. Its result is the final `WorkspaceChanges` object that `_store` writes back to the database.

*Call graph*: called by 1 (_store); 2 external calls (__init__, PurePosixPath).


##### `recorded_workspace_changes`  (lines 179–203)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: This function reads the last saved workspace-change scan for a conversation. If the conversation is a subagent sharing another conversation's sandbox, it resolves to the owning conversation so the shared workspace has one answer.

**Data flow**: It takes a conversation id. It opens a database transaction, finds the sandbox-owning conversation id if one exists, then looks up the saved scan for that owner. If there is no conversation or no saved scan, it returns a shared “nothing changed” object. Otherwise, it validates the stored JSON into a `WorkspaceChanges` object and returns it.

**Call relations**: `WorkspaceChangeRecorder.record` calls this before deciding what to scan next. It uses `workspace_tx` for database access and SQLAlchemy queries to find the owner conversation and its saved change record.

*Call graph*: called by 1 (record); 2 external calls (select, workspace_tx).

## 📊 State Registers Touched

- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-runtime-fleet` — The records of running server or worker instances, their heartbeats, listener claims, and cleanup ownership.
- `reg-transcript-history` — The saved conversation transcript and compaction snapshots that all surfaces, workers, prompts, and recovery logic read and update.
- `reg-live-stream-state` — The live update stream that broadcasts turn progress, tool activity, subagent activity, cancellations, and final replies to connected clients.
- `reg-midturn-replies` — The durable outbox for replies sent before a turn is fully complete, so they can be delivered once even after retries.
- `reg-tool-execution-context` — The per-turn but shared rulebook passed through tools, saying who the tool acts for, what files, accounts, sandboxes, and subagents it may use.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-browser-site-runtime` — The shared browser sessions, hosted preview servers, public site links, and ownership records used to browse, test, and publish websites.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-workspace-change-log` — The saved record of file changes made during a conversation, used to explain later what the agent changed in the workspace.
- `reg-pending-human-interactions` — The durable pending questions, credential-collection prompts, setup requests, and checklist-style waits that tools create and surfaces later resolve.
- `reg-surface-delivery-state` — The outbound reply/writeback bookkeeping for external chat surfaces, including delivery targets, external message identifiers, and exactly-once final reply status.
- `reg-sandbox-image-cache` — The local or remote sandbox image/build cache and validation state used to choose, compare, and launch safe execution environments.
- `reg-active-cancellation-handles` — Process-local abort tokens and cancellation handles that bridge durable cancel requests to currently running turns, tools, sandboxes, and child turns.
- `reg-turn-created-references` — Saved references or citations created by a turn so final replies, source panels, transcripts, and later turns can resolve cited material consistently.
- `reg-turn-surface-context` — Durable per-turn inbound context such as speaker, on-behalf-of member, timezone, original surface metadata, and connection-authorization status carried from admission into prompting, execution, and delivery.
- `reg-conversation-workspace-files` — The mutable per-conversation working file tree that tools, skills, document automation, site building, artifacts, and cleanup read or modify before changes are snapshotted or shared.
