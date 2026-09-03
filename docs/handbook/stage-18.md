# Turn completion, cancellation, cleanup, and teardown  `stage-18`

This stage is the system’s end-of-turn safety net. It runs when a model reply is finishing, when a user presses stop, or when leftover work must be cleaned up after an interruption. Its job is to make sure results are saved, the right people see the right replies, and no hidden work keeps running.

The replies module looks through a model’s text for special hidden “reply-to” sections. These mark which member should receive part of the answer. It delivers those parts to the correct place and removes the hidden markers, even while text is still streaming in small pieces. The delivery sweep is a backup messenger. If a delegated child turn finished but its result was not reported to the parent conversation, it finds and delivers it later. The stop surface handles a member pressing “stop”: it checks the request, cancels the running turn, and sends a final live update. Shared cancellation code tells the active workflow to stop before marking it cancelled. Workspace change tracking scans the conversation’s files and saves a final snapshot of what changed.

## Files in this stage

### Reply Delivery
Handles final reply extraction and safety delivery of completed delegated child turns back to their parent conversations.

### `core/src/ufo/harness/replies.py`

`domain_logic` · `live streaming and round completion`

A model turn can include hidden-looking instructions such as `<reply-to message="..."> ... </reply-to>` to say, “send these words as a reply to this message.” This file is the gatekeeper for that feature. Without it, raw markup could leak into user-visible text, reply text could appear twice, or half-written tags could be shown while the model is still streaming.

There are two main jobs. After a full round is complete, `marked_replies` scans the finished text, finds every closed reply span, extracts the spoken words, and removes the reply tags from the stored text. It keeps the words themselves in the round record, so the history still shows what was said, just without the control markup.

During live streaming, `ReplyRedaction` works more cautiously. Text arrives in pieces, and a tag might be split like `<rep` in one chunk and `ly-to...` in the next. The redactor holds back any text that might still turn into reply markup. Once it knows characters are ordinary prose, it releases them. Once it sees a full opener, it withholds everything until the matching closer. Malformed or unfinished reply markup is not shown to members. This is like a stage curtain: ordinary speech goes out to the audience, but backstage routing labels stay hidden.

#### Function details

##### `marked_replies`  (lines 42–51)

```
def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]
```

**Purpose**: This function reads a completed block of model text and pulls out all properly closed `reply-to` sections. It returns both the replies that should be delivered separately and the same text with the reply markup removed.

**Data flow**: It takes the full text of a round. It searches for opening and closing reply tags, strips any reply markup from inside each matched span, trims the spoken text, and turns each non-empty span into a `MarkedReply`. It also asks `_named_message` to turn the tag’s `message` value into a real message identifier when possible. It returns a tuple of extracted replies plus a cleaned version of the original text with reply tags removed.

**Call relations**: This is the finished-round counterpart to live redaction. When the system has the whole model output, it calls `marked_replies` to decide which reply spans become member-visible replies. Inside that process, it relies on `_named_message` to interpret the message reference and then creates `MarkedReply` records for the rest of the system to deliver or store.

*Call graph*: calls 1 internal fn (_named_message); 1 external calls (__init__).


##### `_named_message`  (lines 54–58)

```
def _named_message(named: str) -> UUID | None
```

**Purpose**: This helper tries to interpret the `message` value from a reply tag as a UUID, which is a standard unique identifier. If the value is not a valid identifier, it safely treats it as missing instead of crashing.

**Data flow**: It receives a string from the tag, trims surrounding whitespace, and passes it to the UUID parser. If parsing succeeds, it returns the UUID object. If parsing fails, it returns `None`, meaning the tag named something that is not a valid message id.

**Call relations**: `marked_replies` calls this whenever it finds a reply span. This keeps the main extraction flow simple: it can always create a `MarkedReply`, with either a usable message reference or `None` when the markup named an invalid target.

*Call graph*: called by 1 (marked_replies); 1 external calls (UUID).


##### `ReplyRedaction.feed`  (lines 77–99)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This method accepts the next piece of live model output and returns only the text that is safe to show immediately. It hides complete reply spans, unfinished tags, and any reply markup.

**Data flow**: It receives one new text chunk and appends it to text that was held back from earlier chunks. If it is currently inside a reply span, it looks for the closing tag; until that closer appears, it keeps only the small suffix that might grow into the closer and publishes nothing. If it is outside a reply span, it looks for a valid opener; text before the opener can be published, and text after the opener is withheld until a closer appears. If no opener is found, it uses `_settled_chars` to decide how much text is definitely ordinary prose and safe to release. The method returns the publishable text with any reply markup stripped, while updating its own saved state for the next chunk.

**Call relations**: The streaming layer calls this repeatedly as model output arrives. `feed` uses `_growing_suffix` when waiting for a possible closing tag split across chunks, and `_settled_chars` when deciding whether a trailing `<` might still become a reply tag. Its output is what the live display can show without leaking routing markup or previewing text that belongs in a separate reply.

*Call graph*: calls 2 internal fn (_growing_suffix, _settled_chars); 1 external calls (find).


##### `_growing_suffix`  (lines 102–107)

```
def _growing_suffix(text: str, token: str) -> str
```

**Purpose**: This helper keeps only the end of some text that could still become a specific token when more streamed text arrives. Here, it is used to avoid missing a closing reply tag that is split across chunks.

**Data flow**: It receives some current text and a target token, such as `</reply-to>`. It checks each possible tail of the text and finds the longest ending that matches the beginning of the token. It returns that tail, or an empty string if no ending could become the token.

**Call relations**: `ReplyRedaction.feed` calls this while it is inside a hidden reply span and has not yet seen the closing tag. By keeping just the possible start of a closer, the redactor can discard hidden reply content while still recognizing the closer if the next chunk completes it.

*Call graph*: called by 1 (feed).


##### `_settled_chars`  (lines 110–119)

```
def _settled_chars(text: str) -> int
```

**Purpose**: This helper decides how many characters are safe to publish now when the redactor is not inside a reply span. It protects against showing the beginning of a tag before the stream has finished spelling it.

**Data flow**: It receives currently held text. If there is no `<`, all of it is safe. If the last `<` and following characters could still grow into a reply opener or closer, it marks only the text before that point as safe. If the tail cannot be reply markup, it marks the whole text as safe, except for certain partial opener shapes that should still be held back.

**Call relations**: `ReplyRedaction.feed` calls this when no complete opener has been found. This lets the live stream release normal prose promptly while holding back ambiguous endings like `<reply` until the next chunk proves whether they are markup or just ordinary text.

*Call graph*: called by 1 (feed).


### `core/src/ufo/runtime/delivery.py`

`domain_logic` · `background scheduled result-delivery sweep`

When one agent delegates work to a child agent, the parent is supposed to hear back when the child finishes. Usually that happens as part of the child’s own execution. But some endings happen from the outside, such as cancellation, or a process can crash after recording the child’s final state but before waking the parent. Without this file, a parent conversation could wait forever for a result that is already stored in the database.

DeliverySweep is the backstop. On each pass, it looks in durable database state for child turns that are finished but still marked as needing result delivery. It groups those children by the parent conversation, so if several child results are waiting for the same parent, they can be delivered together rather than waking the parent again and again.

It also avoids waking the same conversation too frequently. If a conversation was already woken by a result delivery in the last cooldown window, this sweep leaves its remaining children for the next pass. That prevents a loop where a newly woken parent immediately spawns more children that wake it again.

If the parent agent has been archived, the sweep skips it instead of failing the whole job. The child stays pending, so if the agent is restored later, the owed result can still be delivered.

#### Function details

##### `DeliverySweep.run`  (lines 54–72)

```
async def run(self) -> None
```

**Purpose**: Runs one delivery sweep for the current workspace. It finds finished child turns that still owe their result to a parent, skips parent conversations that were recently woken, and asks the normal subagent result-delivery path to deliver each remaining child.

**Data flow**: It starts with no direct input besides the sweep object’s invoker factory and subagent registry. It reads outstanding child turns from the database, checks which parent conversations were recently woken, builds a SubagentResult helper for the current workspace, and then delivers each eligible child. The result is no returned value; the important change is that child results may be posted back to their parent conversations, while archived parents are skipped without stopping the rest of the pass.

**Call relations**: This is the main body of the sweep. It first calls DeliverySweep._outstanding to learn what work is waiting, then calls DeliverySweep._woken_since to avoid waking conversations too often. It creates the same SubagentResult delivery helper used by the event path, using ws_current to know which workspace it is working in. If delivery reports that the parent agent is archived, it simply moves on so other conversations can still receive their results.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 74–93)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: Finds which workspaces have at least one finished child turn whose result still needs to be delivered to a non-archived parent agent. A scheduler can use this to decide where the delivery sweep should run.

**Data flow**: It opens an owner-level database transaction, which can see workspace-wide ownership information. It queries turn and agent records for children marked as pending delivery, already finished, and connected to a parent agent that is not archived. It returns a tuple of workspace IDs, with each workspace listed once.

**Call relations**: This function is used before running the workspace-specific sweep. It does not deliver anything itself; it only points the job system toward workspaces that appear to have delivery work waiting. It relies on the owner_tx database context and builds its query with SQLAlchemy.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 95–131)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: Collects the child turns in the current workspace that are finished but whose results have not yet been delivered. It groups them under the parent conversation that should be woken.

**Data flow**: It reads from the workspace database. It joins each pending child turn to its parent turn and parent agent, keeps only children that are terminal, still pending delivery, and whose parent agent is not archived, and orders them by parent conversation and finish time. It converts each database row into a Turn object and returns a dictionary: parent conversation ID to a list of child turns waiting for delivery.

**Call relations**: DeliverySweep.run calls this first to learn what needs attention. The grouping it returns shapes the rest of the sweep: run can treat a parent conversation’s waiting children as one batch, which helps fan-out work come back to the parent in a compact, predictable way.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 133–160)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: Checks which of a given set of parent conversations have already been woken by a delivered child result after a cutoff time. This prevents the sweep from waking the same conversation too often.

**Data flow**: It receives a cutoff timestamp and a tuple of conversation IDs. It reads the workspace database for delivered child turns connected to parent turns in those conversations, where the child was updated after the cutoff. It returns a frozen set of conversation IDs that should be skipped for now.

**Call relations**: DeliverySweep.run calls this after finding outstanding children and before attempting delivery. Its answer acts like a temporary pause list: if a conversation was recently woken by either the normal event path or this sweep, run leaves that conversation’s pending children for a later sweep.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


### Turn Cancellation
Coordinates user stop requests and safely cancels running turn workflows while notifying live listeners.

### `core/src/ufo/runtime/surfaces/stop.py`

`orchestration` · `request handling`

This file is the “stop button” path for an active conversation turn. A turn is one running unit of work in a conversation, and stopping it must be done carefully: the system must not cancel the wrong turn, must record the cancellation durably, and must wake up anyone currently watching that turn.

The main piece is the `MemberStop` class. It is given three collaborators: a DBOS client for durable workflow work, a hub for publishing live updates, and an admission object that can start or move the conversation forward if the member has already sent another message.

The workflow first checks the database to prove that the requested turn belongs to the requested conversation inside the requested workspace. This is like checking the label on a package before removing it from the delivery line. If the label does not match, it refuses the request.

Then it asks the shared cancellation helper to cancel that one turn. If the turn had already ended, the helper returns nothing, and this file reports that nothing changed. That makes repeated stop clicks harmless.

If the turn was actually cancelled, the workflow may redispatch a waiting message into a new turn. It publishes a special “absorbed” update for that new turn so the surface does not wait forever for confirmation. Finally, it publishes the cancelled terminal update for the old turn, which wakes all live tails immediately.

#### Function details

##### `MemberStop.stop`  (lines 38–61)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: This function carries out one member stop request from start to finish. It verifies that the turn really belongs to the conversation, cancels it if it is still running, optionally starts the next waiting turn, and returns a small result telling the surface what happened.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. It reads the database inside a workspace transaction to find which conversation owns the turn. If the turn is not owned by the given conversation, it raises an error instead of touching it. If ownership is valid, it asks the shared cancellation code to cancel the turn. If there was nothing to cancel because the turn had already ended, it returns `Stopped` with `ended` set to false. If cancellation succeeds, it asks admission to redispatch any waiting work for the conversation, publishes live hub messages for the new turn if one was founded, publishes the old turn’s final cancelled state, and returns `Stopped` with `ended` set to true and the new turn ID if there is one.

**Call relations**: This is the surface-level workflow that ties together database checking, durable cancellation, and live notification. It opens a workspace transaction with `workspace_tx`, builds a SQL query with `sqlalchemy.select`, and delegates the actual durable cancellation to `cancel_one_turn`. When it needs to notify listeners, it creates `Absorbed` and `Terminal` hub messages, and it returns a `Stopped` result so the calling surface can update the member’s screen.

*Call graph*: 6 external calls (__init__, __init__, __init__, select, workspace_tx, cancel_one_turn).


### `core/src/ufo/runtime/turns/cancellation.py`

`domain_logic` · `cancel handling and recovery`

A “turn” is one unit of work in a conversation, and some turns can start child turns, like a main worker asking helpers to do separate jobs. This file does not cancel an entire family tree of turns. Instead, it is the reliable building block that cancels exactly one turn; other code is responsible for walking through descendants and calling this as needed.

The main problem it solves is ordering. If the database said “cancelled” before the actual background workflow was cancelled, the system could believe the work had stopped while it was still running. So this file follows a strict rule: first ask DBOS, the durable workflow system, to cancel the live workflow; only then write the cancelled result into the turn row.

It also protects against races. A turn may finish normally at the same time someone tries to cancel it, or it may be claimed by a new running attempt while cancellation is underway. The code checks the database, cancels the attempt it saw, then locks and rechecks the row before writing. If ownership changed, it loops and cancels the new owner instead. After a successful cancellation, it records a metric and asks the dispatcher to start the next waiting turn in the same conversation.

#### Function details

##### `cancel_one_turn`  (lines 24–101)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Cancels one turn if it is still active, and records a durable cancelled result for it. It returns the cancelled terminal frame only when this call actually changed the turn to cancelled; if the turn was already finished or missing, it returns nothing.

**Data flow**: It receives a DBOS client, which can talk to the workflow system, and a turn ID, which points to a row in the database. It first reads the turn status and the currently running workflow attempt. If the row is gone or already terminal, it stops and returns null. Otherwise it asks DBOS to cancel the matching workflow. Then it opens a locked database transaction, rereads the row, and only writes the cancelled status if the same running attempt still owns the turn. While writing, it also builds a TerminalFrame containing any objects the turn had already created, so callers can still see what existed before cancellation. After the row is successfully changed, it emits a cancellation metric, nudges the dispatcher to run the next turn for that conversation, and returns the terminal frame.

**Call relations**: This is the shared cancellation step used by higher-level cancel paths such as user-initiated cancellation, tool-triggered cancellation, and reconciliation. Inside the flow it relies on workspace_tx to read and write the database safely, DBOSClient.cancel_workflow_async to stop the durable workflow, TerminalFrame and ObjectRef validation to shape the cancelled result, emit_metric and turn_profile to report what happened, and dispatch_next_turn to let the conversation continue after this turn is no longer blocking it.

*Call graph*: 9 external calls (__init__, model_validate, cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile, dispatch_next_turn).


### Workspace Snapshots
Records and preserves workspace file-change snapshots so completed turns can still report what changed.

### `core/src/ufo/runtime/turns/workspace_changes.py`

`domain_logic` · `turn-end background refresh`

A conversation can edit files, run shell commands, delete things, or rename things inside a workspace. The message history alone cannot reliably describe all of that. This file solves that gap by asking the sandbox’s file system tool what changed and saving the answer in the database.

The main idea is simple: watch the places that tools may have touched. A `write` or `edit` tool names a file, so that file’s directory becomes a scan target. A `bash` command could change almost anything in the checkout, so the workspace root becomes a target. The recorder also keeps watching directories that had changes in the last saved scan, so a changed checkout stays visible until a later scan says it is clean.

The file defines small validated records, `WorkspaceChange` and `WorkspaceChanges`, to keep saved scans predictable: each change has a path, a patch, and a flag saying whether the patch was cut short. There are limits on patch size, number of changes, and number of target directories so a huge workspace cannot produce unbounded data.

`WorkspaceChangeRecorder` runs after a turn has already ended. If scanning fails, it logs the problem but does not fail the completed turn. When saving, it carefully merges with any existing scan so two turns sharing the same sandbox do not accidentally erase each other’s discoveries.

#### Function details

##### `change_targets`  (lines 37–55)

```
def change_targets(calls: Iterable[ToolUseBlock]) -> tuple[str, ...]
```

**Purpose**: This function looks at tool calls from a turn and extracts the workspace paths that might need to be checked for file changes. It turns concrete tool activity, such as editing a file or running a shell command, into a short ordered list of scan targets.

**Data flow**: It receives a sequence of tool-use records. For `write` and `edit`, it reads the `file_path`, checks that it belongs inside the workspace, and stores the path relative to the workspace root. For `bash`, it stores `.` to mean the workspace root, because a shell command can change files without naming them in a structured way. Duplicate targets are removed while keeping first-seen order, and the function returns the final tuple of path strings.

**Call relations**: This is the target-finding step that feeds the recorder’s later work. It relies on `workspace_path` to reject paths outside the workspace and on `PurePosixPath` to turn accepted paths into clean workspace-relative names.

*Call graph*: 2 external calls (PurePosixPath, workspace_path).


##### `WorkspaceChangeRecorder.record`  (lines 101–114)

```
async def record(self) -> None
```

**Purpose**: This is the top-level action that refreshes the saved workspace-change snapshot after a turn. It decides whether there is anything worth scanning, finds what was previously recorded, scans the relevant directories, and stores the updated result.

**Data flow**: It starts with the recorder’s sandbox, conversation ID, workspace ID, and target paths. If there is no created sandbox and no targets, it does nothing. Otherwise it reads the last saved changes, expands those plus the new targets into directories to watch, asks the sandbox to scan them, and saves the merged result. If any part fails, it logs the error and leaves the previous saved scan untouched.

**Call relations**: This function orchestrates the file’s main flow. It calls `recorded_workspace_changes` to fetch the current saved answer, `_directories` to choose what to ask about, `_scan` to get a fresh answer from the sandbox, and `_store` to write the result. If something goes wrong, it hands the failure details to the logging system instead of interrupting the already-finished turn.

*Call graph*: calls 4 internal fn (_directories, _scan, _store, recorded_workspace_changes); 1 external calls (log).


##### `WorkspaceChangeRecorder._directories`  (lines 116–129)

```
def _directories(self, recorded: WorkspaceChanges) -> list[str]
```

**Purpose**: This helper chooses which directories should be scanned. It combines the current turn’s touched paths with directories from the previous saved changes, so old visible changes keep being checked until they disappear.

**Data flow**: It receives the last recorded `WorkspaceChanges`. It takes every new target path and every previously changed path, converts each one to its parent directory, removes duplicates, sorts the result, and caps the list at the configured maximum. If too many directories were found, it logs how many were dropped. It returns the final list of directory strings.

**Call relations**: `record` calls this after loading the previous saved scan. The directory list it returns is passed directly into `_scan`, which asks the sandbox’s file-system tool about those locations.

*Call graph*: called by 1 (record); 2 external calls (PurePosixPath, log).


##### `WorkspaceChangeRecorder._scan`  (lines 131–136)

```
async def _scan(self, directories: list[str]) -> WorkspaceChanges
```

**Purpose**: This function asks the sandbox to report file changes for a set of directories. It also checks that the sandbox’s answer has the expected shape before the answer is trusted.

**Data flow**: It receives a list of workspace-relative directories. It sends those paths to the sandbox command `ufo fs changes`, then tries to validate the returned data as a `WorkspaceChanges` record. If the data is valid, it returns that typed result. If the sandbox returned malformed data, it raises a runtime error explaining that the scan was bad.

**Call relations**: `record` calls this after `_directories` chooses the scan targets. Its output is handed to `_store`, which persists the scan in the database.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 138–161)

```
async def _store(self, scanned: WorkspaceChanges, asked: frozenset[str]) -> None
```

**Purpose**: This function saves a fresh scan in the database without trampling over work saved by another recorder at the same time. It is the persistence step for the workspace-change snapshot.

**Data flow**: It receives the newly scanned changes and the set of directories that were actually asked about. Inside a database transaction, it first ensures there is a row for this workspace and conversation. It then locks and reads the current saved scan, merges the current scan with that saved scan, and writes the merged result back. The database ends with one updated scan record for the conversation.

**Call relations**: `record` calls this after `_scan` succeeds. `_store` uses `_merged` to decide which old changes should survive, and it uses the workspace database transaction helper plus SQLAlchemy query builders to read and update the saved row safely.

*Call graph*: calls 1 internal fn (_merged); called by 1 (record); 5 external calls (model_dump, and_, select, update, workspace_tx).


##### `WorkspaceChangeRecorder._merged`  (lines 163–178)

```
def _merged(self, scanned: WorkspaceChanges, stored: WorkspaceChanges, asked: frozenset[str]) -> WorkspaceChanges
```

**Purpose**: This function combines a fresh scan with the scan already stored in the database. It keeps old changes only when the fresh scan did not look at their directories, which helps avoid losing changes found by another simultaneous recorder.

**Data flow**: It receives the fresh scan, the stored scan, and the set of directories that were scanned this time. It treats every fresh change as authoritative. From the stored scan, it keeps only changes whose parent directory was not asked about and whose path is not already present in the fresh scan. It joins fresh changes plus kept old changes, cuts the list to the maximum allowed size, and returns a new `WorkspaceChanges` object with the correct truncation flag.

**Call relations**: `_store` calls this while holding the database row lock. It is the conflict-resolution step: `_store` brings in database state, `_merged` decides the combined answer, and `_store` writes that answer back.

*Call graph*: called by 1 (_store); 2 external calls (__init__, PurePosixPath).


##### `recorded_workspace_changes`  (lines 181–205)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: This function reads the last saved workspace-change scan for a conversation. If the conversation is a subagent that shares a parent workspace, it looks up the parent conversation’s saved scan instead.

**Data flow**: It receives a conversation ID. In a database transaction, it first finds the conversation that owns the sandbox, using the parent sandbox conversation when one exists. If there is no such conversation, or if no scan has been saved yet, it returns the shared `NOTHING_CHANGED` value. If a scan is found, it validates the stored data as `WorkspaceChanges` and returns it.

**Call relations**: `WorkspaceChangeRecorder.record` calls this before choosing directories to scan. Its result tells the recorder both what is currently shown as changed and which old changed directories should stay under watch.

*Call graph*: called by 1 (record); 2 external calls (select, workspace_tx).

## 📊 State Registers Touched

- `reg-auth-sessions` — The sign-in state and signed tokens that prove who a web, surface, or API request belongs to.
- `reg-access-subjects` — The shared visibility rules that say which members or audiences may read conversations, sources, and objects.
- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-turn-queue` — The durable queue of conversation turns waiting, running, parked, resumed, or blocked as duplicates.
- `reg-live-workflow-state` — The shared run-state for active turns, including locks, progress, retry guards, cancellation, and completion markers.
- `reg-sandbox-state` — The remembered sandbox handles and execution environments where commands, files, and risky work run safely.
- `reg-browser-sessions` — The active or reusable Chrome browser sessions, tabs, downloads, and remote-control connections used by agents.
- `reg-scheduled-jobs` — The durable background work list for timers, recurring conversations, monitors, reports, and long-running tasks.
- `reg-surface-routing` — The saved routing state that maps web, Slack, iMessage, terminal, and other surfaces to workspaces and agents.
- `reg-inbound-message-queue` — The durable inbox of external messages waiting to be admitted into conversations exactly once.
- `reg-artifact-publication` — The shared state for files, previews, signed downloads, hosted sites, app pages, and published outputs.
- `reg-runtime-instances` — The shared record of which server processes are alive and which background or surface duties they have claimed.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-subagent-state` — The parent-child turn links, delegation contracts, spawn identities, and pending result deliveries for helper agents.
- `reg-surface-outbox` — The durable outbound delivery buffer and writeback markers for replies or mid-turn messages that must be sent to external surfaces exactly once.
- `reg-tool-task-journals` — Persistent journals and handles for long-running tool or command executions so they can be resumed, polled, deduplicated, or cleaned up later.
- `reg-conversation-change-snapshots` — Durable per-conversation workspace/file change snapshots saved after turns so later views and audits can show what changed.
- `reg-runtime-message-bus` — Shared Redis/pub-sub or message-hub connection state used to coordinate live updates, workers, and cross-process runtime events.
- `reg-repl-scratchpad-state` — Persistent Python/JavaScript REPL interpreter sessions, variables, scratch files, and execution handles kept across tool calls or turns.
