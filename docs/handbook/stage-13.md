# Workspace objects, artifacts, sites, and portal data  `stage-13`

This stage is shared behind-the-scenes support for the workspace. It defines the “things” the system works with, such as agents, members, conversations, pages, reports, files, websites, todos, and long-running objectives. These things are stored as readable records, checked before use, and shown safely in the portal.

The object system is the rulebook. It decides what kind of object something is, whether a user may see or change it, and how changes are recorded. The artifacts and sites part is the display and delivery layer. It serves shared files, builds previews, tracks hosted websites, and exposes the right items back into conversations and portal panels.

The objective tools and store add durable planning. They remember planned steps, work attempts, and verification results, so a task is not marked done just because someone claims it is. The listings helper gives portal screens stable “next page” browsing. The todo extension adds simple conversation checklists. Together, these parts make workspace data reliable, permission-aware, and visible in the right places.

## Sub-stages

- [Object system and permission-safe mutations](stage-13.1.md) `stage-13.1` — 23 files
- [Artifacts, previews, hosted sites, and app pages](stage-13.2.md) `stage-13.2` — 16 files

## Files in this stage

### Objective planning
Objective tools and storage track long-running work through planned steps, recorded attempts, and verified completion checks.

### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `tool invocation during objective planning, progress recording, reading, and delegation`

This file is the working surface of the objectives extension. An objective is a longer task with named steps, and each step can say what real state proves it is done, such as a file existing, a file containing text, or a command succeeding. Without this file, agents could keep informal to-do lists, but the system would not have a reliable way to store them, resume them later, delegate independent pieces, or verify that claimed work actually happened.

The main idea is simple: planning and finishing are separate. When a plan is written, the file rejects some weak proof, such as saying “this step is done when this file exists” if that file already exists before the work starts. Later, when a worker records “I did this step,” the tool does not automatically believe it. It runs the step’s checks in the sandbox, records which ones passed or failed, and shows the result back in plain text. This is like a checklist where ticking the box also makes someone inspect the work.

The file also supports reading the current objective state and launching independent steps in parallel subagents. Tool input models define exactly what each tool accepts, and ToolDef objects register these actions with the wider system.

#### Function details

##### `_require_ext`  (lines 104–107)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

*Call graph*: called by 4 (plan_objective, read_objective, record_step, run_independent_steps).


##### `render`  (lines 110–125)

```
def render(view: ObjectiveView) -> str
```

*Call graph*: called by 3 (plan_objective, read_objective, record_step); 1 external calls (condition_summary).


##### `evaluate`  (lines 128–132)

```
async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]
```

*Call graph*: calls 1 internal fn (_verdict); called by 2 (read_objective, record_step).


##### `_verdict`  (lines 135–159)

```
async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict
```

*Call graph*: calls 1 internal fn (_unmet); called by 2 (evaluate, plan_objective); 4 external calls (__init__, quote, emit_metric, condition_summary).


##### `_unmet`  (lines 162–177)

```
def _unmet(condition: Condition, result: ExecResult) -> str
```

*Call graph*: called by 1 (_verdict); 1 external calls (condition_summary).


##### `plan_objective`  (lines 180–235)

```
async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult
```

*Call graph*: calls 3 internal fn (_require_ext, _verdict, render); 5 external calls (__init__, __init__, __init__, agent_current, condition_summary).


##### `record_step`  (lines 238–286)

```
async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult
```

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 5 external calls (__init__, __init__, __init__, agent_current, emit_metric).


##### `run_independent_steps`  (lines 289–338)

```
async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult
```

*Call graph*: calls 2 internal fn (_dispatch_stopped, _require_ext); 6 external calls (__init__, __init__, __init__, spawn, agent_current, emit_metric).


##### `_dispatch_stopped`  (lines 341–364)

```
def _dispatch_stopped(step: str, dispatched: list[tuple[str, str]], error: Exception) -> ToolFailure
```

*Call graph*: called by 1 (run_independent_steps); 2 external calls (__init__, __init__).


##### `read_objective`  (lines 367–386)

```
async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult
```

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 4 external calls (__init__, __init__, __init__, agent_current).


##### `_with_verdicts`  (lines 389–399)

```
def _with_verdicts(view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]) -> ObjectiveView
```

*Call graph*: called by 2 (read_objective, record_step); 1 external calls (replace).


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `cross-cutting: planning, turn startup, progress recording, and condition checking`

This file solves a trust and continuity problem. Each turn starts fresh, so the system cannot rely on temporary memory from the previous turn. It needs a permanent record of objectives, steps, attempts, blocks, and check results. Without this file, later turns would not know what had already been tried, which steps were still blocked, or whether the promised completion conditions really held.

The file defines database tables for objectives, steps, events, and checks. A step can declare acceptance conditions, such as “this file exists,” “this file contains this text,” or “this command succeeds.” These conditions are stored as data and later evaluated by the extension. That matters because a worker is not allowed to quietly weaken the finish line after discovering the work is hard.

The main read shape is an ObjectiveView containing StepView objects. These views do not store a hand-written status. Instead, they calculate status from facts: events like “did” or “blocked,” plus the latest condition verdicts. This is like a checklist where nobody can erase old marks; they can only add new notes.

The Objectives class is the store. It creates or revises plans, records attempts and blocks, records check results, and rebuilds readable views from database rows.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: This tells whether anyone has actually tried the step. It matters because a step should not be treated as finished, failed, or checked until there has been a real attempt.

**Data flow**: It reads the step’s event list. If any event says the worker “did” the step, it returns true; otherwise it returns false. It does not change anything.

**Call relations**: Other step and objective calculations use this as a basic fact. For example, the step state uses it to separate untouched work from attempted work, and runnable-step selection uses it to avoid dispatching work that has already been tried.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: This finds whether the step is currently blocked by an unanswered question or obstacle. It only treats the most recent event as an active block, so an older block can be superseded by later work.

**Data flow**: It looks at the step’s last recorded event. If that event is a block, it returns that event; otherwise it returns nothing. It does not edit the event history.

**Call relations**: The step state uses this idea to report a blocked step. Objective dispatch also uses it indirectly so the system does not keep asking the same blocked question every time it wakes up.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: This turns the raw history of a step into a plain status such as pending, blocked, done, attempted, or unmet. It prevents the system from calling a step complete just because no checks were run.

**Data flow**: It reads the latest event, whether the step was attempted, the acceptance conditions, and the latest verdicts. From those facts it returns one status string. It does not write back to storage.

**Call relations**: Objective-level views depend on this property to count confirmed steps, find unfinished frontier steps, and decide what work remains. It is the central rulebook for interpreting a step’s recorded facts.


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: This counts how many times work has been attempted across all steps in an objective. It helps show whether effort is accumulating without corresponding progress.

**Data flow**: It scans every step and every event in those steps. Each “did” event adds one to the count, and the final number is returned. Nothing is changed.

**Call relations**: This is a reporting helper on the objective view. Other parts of the system can use it to understand the shape of progress, especially when attempts rise but completed steps do not.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: This counts how many steps are truly done according to the extension’s state rules. It counts confirmed completion, not just claimed completion.

**Data flow**: It asks each step for its current state. Steps whose state is done are counted, and the count is returned. It does not change the objective.

**Call relations**: This sits above StepView.state. It gives callers a simple progress number while still relying on the stricter per-step checks underneath.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: This finds steps that the engine may safely run in parallel right now. A step qualifies only if it is unfinished, declared independent, not yet attempted, and not currently blocked.

**Data flow**: It starts from the objective’s frontier, meaning unfinished steps. It filters that list down to independent steps that have no attempt and no open block, then returns them as a tuple.

**Call relations**: It builds on ObjectiveView.frontier and StepView properties. Dispatch code can use this to fan out work, while the step plan’s independent flag controls whether parallel work is allowed.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: This returns the unfinished part of an objective. It is the objective’s active edge: the steps that still need work, checking, or attention.

**Data flow**: It reads every step and keeps only those whose state is not done. The result is returned as an ordered tuple. The objective itself is not modified.

**Call relations**: ObjectiveView.runnable builds from this list. It is also the natural view for callers that need to know what remains after completed steps are removed.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: This turns a stored acceptance condition into a short human-readable sentence. It is useful when showing someone what proof a step requires.

**Data flow**: It receives one condition object. Depending on whether the condition is file existence, file contents, or command success, it formats a short text summary and returns it.

**Call relations**: This is a small presentation helper. It does not read the database or affect objective state; it simply translates condition data into wording.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: This looks up one objective by conversation and name. The conversation boundary matters because different agents may reuse the same objective name without meaning the same work.

**Data flow**: It receives a conversation ID and objective name, then queries the objective table for a matching row in the current workspace. If nothing matches, it returns nothing; if a row is found, it asks Objectives._view to build a full ObjectiveView with steps, events, and checks.

**Call relations**: Objectives.plan calls this first to decide whether it is creating a new objective or revising an existing one. When a row is found, this function hands off to Objectives._view so callers receive a complete readable object rather than a bare database row.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: This finds the most recently created objective for a conversation. It is useful when the system knows the conversation but not the objective name.

**Data flow**: It receives a conversation ID, queries the objective table within the current workspace, orders matching objectives newest first, and takes one. If found, it converts that row into a full ObjectiveView; otherwise it returns nothing.

**Call relations**: Like Objectives.named, this is a lookup path into Objectives._view. It is used when the broader flow wants the active or latest objective attached to a conversation.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: This creates a new objective or revises an existing objective’s plan. Its most important safeguard is that acceptance conditions for already-attempted steps stay frozen, so the goalposts cannot move after work begins.

**Data flow**: It receives a conversation ID, objective name, directive, and planned steps. It first checks whether the objective already exists. For a new objective, it inserts the objective row. For an existing one, it updates the directive, keeps steps that already have events, removes untouched obsolete steps, and preserves acceptance conditions for attempted steps. It then updates or inserts each planned step and finally returns the rebuilt ObjectiveView.

**Call relations**: This is the main planning entry into the store. It calls Objectives.named to inspect the current record, uses database insert, update, and delete operations to make the plan durable, and returns through Objectives.named again so the caller sees the stored result exactly as it will be read later.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: This appends a step event, such as an attempt or a block. It deliberately avoids recording the same standing block twice, so a repeating heartbeat does not flood the history with the same unanswered question.

**Data flow**: It receives a StepView, event kind, actor turn ID, and evidence text. It trims the evidence to the maximum stored length, checks whether this is a duplicate of the current open block, and either returns false without writing or inserts a new event row and returns true.

**Call relations**: This is how worker actions become permanent objective history. Later, Objectives._view reads these events back into StepView objects, and StepView.state uses them to decide whether a step is pending, blocked, attempted, or done.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: This records what the extension found when it evaluated a step’s acceptance conditions. The result is stored as an observation by the extension, not as a worker’s claim.

**Data flow**: It receives a StepView, a tuple of condition verdicts, and the actor turn ID. It serializes each verdict into JSON-friendly data and inserts a new check row with a timestamp. It does not overwrite older checks.

**Call relations**: Condition-checking code calls this after evaluating live conditions. Later, Objectives._view reads the latest check for each step so StepView.state can decide whether attempted conditional work is done, still attempted, or unmet.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: This rebuilds a complete ObjectiveView from database rows. It gathers the objective’s steps, their event histories, and their latest check results into one readable object.

**Data flow**: It receives an objective database row. It queries step rows, event rows, and check rows for that objective, groups events and latest checks by step, parses stored condition data, and creates StepView objects inside an ObjectiveView. The output is a clean in-memory view of the durable record.

**Call relations**: Objectives.named and Objectives.on_conversation call this after finding an objective row. It hands stored JSON through _conditions and _verdicts, then assembles the objects that the rest of the extension reads.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: This converts raw stored condition data back into typed condition objects. It is the bridge from database JSON to safer in-memory objects the code can reason about.

**Data flow**: It receives an arbitrary payload. If the payload is not a list, it returns an empty tuple. If it is a list, it examines each item’s kind, validates it as the matching condition type, and returns all parsed conditions; unknown kinds raise an error.

**Call relations**: Objectives._view uses this when rebuilding each step’s acceptance conditions. _verdicts also uses it to parse the condition attached to each stored verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This converts raw stored check results back into ConditionVerdict objects. A verdict says which condition was checked, whether it held, and what detail was observed.

**Data flow**: It receives an arbitrary payload. If it is not a list, it returns an empty tuple. For each dictionary item, it parses the nested condition with _conditions, converts the holds value to true or false, converts the detail to text, and returns the resulting verdict objects.

**Call relations**: Objectives._view calls this when a step has stored check rows. It depends on _conditions so the verdict points back to the same kind of condition object used by the rest of the objective logic.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).


### Portal pagination
Shared listing helpers provide stable cursor-based pagination for portal views.

### `core/src/ufo/runtime/listings.py`

`domain_logic` · `request handling`

Listings often show changing data, such as memories or artifacts. If a page used a simple offset like “skip the first 20 rows,” a newly inserted row could make the reader see the same item twice or miss one entirely. This file avoids that by using keyset paging: each page is anchored to the last seen row’s real position, made from its creation time and its unique id.

The file defines a cursor, `ListingCursor`, which is a small token saying, “start from this row, and move older or newer.” It can turn itself into a string for a URL and can rebuild itself from that string later. If the string is broken or fake, the code raises `MalformedCursor` rather than guessing and silently showing the wrong page.

`page_query` prepares a database query with the right ordering and boundary check. It asks for one extra row beyond the requested limit, like peeking around the corner to see whether another page exists. `page_of` then turns those raw rows into a `ListingPage`, trims the extra row away, reverses rows when needed, and creates the “older” and “newer” cursors for navigation controls.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: This turns a cursor into a single string that can travel in a URL or request parameter. It records the direction, the creation time, and the item id so the next request can return to the same exact spot.

**Data flow**: It starts with a `ListingCursor` object containing a timestamp, an item id, and whether the request is for newer rows. It chooses the word `newer` or `older`, joins that with the timestamp and id using a separator, and returns the finished text token. It does not change anything else.

**Call relations**: This is used when a page is being prepared for a client and the system needs to put navigation positions into links or controls. It is the counterpart to `ListingCursor.decode`, which later reads the token back.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: This reads a cursor string from a client and turns it back into a `ListingCursor`. It protects listings from bad or invented cursor values by rejecting tokens that do not describe a real position.

**Data flow**: It receives a text token, splits it into direction, timestamp, and item id, then checks that the direction is valid and both pieces of position data are present. It parses the timestamp as a date and time, checks that the id is a valid UUID (a standard unique identifier), and returns a cursor object. If any part is missing or invalid, it raises `MalformedCursor` instead of returning a guess.

**Call relations**: The web workspace memory surface calls this when a request arrives with a cursor parameter. Inside, it relies on standard parsing helpers for dates and UUIDs, and it raises `MalformedCursor` so the caller can answer the client with a clear error.

*Call graph*: called by 1 (workspace_memory); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: This prepares a database query for one stable page of a listing. It applies the shared newest-first ordering, adds the cursor boundary if there is one, and asks for one extra row to learn whether another page exists.

**Data flow**: It receives an unfinished SQL query, an optional cursor, a page size, and the two database columns that define row position: creation time and id. If there is no cursor, it orders newest first and limits the result to one more than requested. If there is a cursor, it compares each row’s `(created_at, id)` pair to the cursor’s position and keeps only rows on the requested side. It returns a new SQL query ready to run.

**Call relations**: Listing readers use this before fetching rows from the database. It builds the position comparison using SQLAlchemy’s tuple comparison helper and converts the cursor’s id text back into a UUID so the database comparison uses the right kind of value. The rows it asks for are then meant to be shaped by `page_of`.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: This turns the rows returned by `page_query` into a clean page for the caller. It removes the extra peek row, converts each source row into the desired output shape, and creates cursors for the older and newer navigation controls.

**Data flow**: It receives the fetched rows, the cursor that led here, the requested limit, plus two small caller-provided functions: one to render each row and one to read its position. It checks whether there was an extra row beyond the limit, keeps only the real page rows, reverses them if the query had to walk toward newer rows, and returns a `ListingPage` containing rendered rows plus optional older and newer cursors. If there are no rows, it returns an empty page with no cursors.

**Call relations**: This is the second half of the paging flow after `page_query`. It uses the extra row from the database query to decide whether a next control should exist, and it constructs the final `ListingPage` object that listing surfaces can return to clients. It uses its nested helper `page_of.at` to make boundary cursors from the first and last visible rows.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: This small helper creates a cursor for one boundary row of a page. It is used so the page can say exactly where the “older” or “newer” button should start from.

**Data flow**: It receives one source row and a direction flag. It calls the provided `position` function to pull out that row’s creation time and item id, then builds and returns a `ListingCursor` pointing at that row in the requested direction.

**Call relations**: This helper lives inside `page_of` because it only makes sense while building a page envelope. `page_of` calls it for the last visible row when an older page exists and for the first visible row when a newer page exists.

*Call graph*: 1 external calls (__init__).


### Conversation todos
The todo extension stores and updates visible per-conversation checklists for agents.

### `extensions/todos/ufo_ext_todos.py`

`domain_logic` · `request handling and conversation display`

This file solves a simple but important problem: when an agent is doing a multi-step job, both the user and the agent need a shared progress board. Without it, the agent might lose track of what is done, and the user interface would have no reliable checklist to display.

The file defines two tools. One tool, `update_todo_list`, creates or replaces the whole checklist. The other, `update_todo_status`, changes the status of individual tasks, such as from `pending` to `in_progress` or `completed`. Think of it like a whiteboard beside a workbench: the full task list is written first, and then checkmarks are added as work moves forward.

The checklist is stored in the extension's own store, keyed by the conversation ID. That means the list is not just temporary text in the agent's current reply; it can be read again later in the same conversation. The file also defines a conversation slot, which is a structured piece of UI-facing conversation state, so the product can show the current task list, counts, and completed items. Long titles, descriptions, or task lists are trimmed before display to stay within allowed limits.

Finally, `manifest` advertises this extension to the larger system: it names the tools, describes when the agent should use them, attaches prompt guidance, and exposes the task display slot.

#### Function details

##### `_require_ext`  (lines 87–90)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper makes sure a tool call has access to the todo extension's context. The extension context is needed because it contains the durable store where todo boards are saved.

**Data flow**: It receives a tool context. If that context includes an extension context, it returns it unchanged. If the extension context is missing, it stops the operation by raising an error, because the todo tools cannot save or read lists without it.

**Call relations**: When `update_todo_list` or `update_todo_status` starts, they call this first to confirm they have the storage access they need. It acts like checking that you have the key to the filing cabinet before trying to file or edit a checklist.

*Call graph*: called by 2 (update_todo_list, update_todo_status).


##### `_board_key`  (lines 93–94)

```
def _board_key(conversation_id: UUID) -> str
```

**Purpose**: This helper turns a conversation ID into the storage key used for that conversation's todo board. It keeps every conversation's checklist separate.

**Data flow**: It receives a conversation UUID, which is a unique identifier. It prefixes that ID with `todo/` and returns the resulting text key, such as a labeled folder name for the board.

**Call relations**: `update_todo_list`, `update_todo_status`, `_summarize_tasks`, and `_read_tasks` all use this helper whenever they need to find the right board in storage. It is the shared naming rule that keeps reads and writes pointed at the same place.

*Call graph*: called by 4 (_read_tasks, _summarize_tasks, update_todo_list, update_todo_status).


##### `_board_result`  (lines 97–98)

```
def _board_result(board: TodoBoard) -> ToolResult
```

**Purpose**: This helper turns a todo board into the standard tool response returned to the agent. It makes sure every create or update call gives the agent the current checklist state back.

**Data flow**: It receives a `TodoBoard`, converts it to JSON text, wraps that text as `TextContent`, and then wraps that content in a `ToolResult`. The output is the response object the tool system expects.

**Call relations**: After `update_todo_list` writes a new board, and after `update_todo_status` saves changed statuses, they call this helper to send the latest board back. It hands the result off to the tool framework in a consistent format.

*Call graph*: called by 2 (update_todo_list, update_todo_status); 3 external calls (__init__, __init__, model_dump_json).


##### `_read_board`  (lines 101–103)

```
async def _read_board(ext: ExtensionContext, key: str) -> TodoBoard | None
```

**Purpose**: This helper reads a saved todo board from the extension store. It also validates that the stored data still has the expected shape before the rest of the code uses it.

**Data flow**: It receives the extension context and a storage key. It asks the store for the saved value. If nothing is stored there, it returns `None`; otherwise, it turns the stored data into a `TodoBoard` object and returns it.

**Call relations**: `update_todo_status` uses this before applying task updates, because it must edit an existing board. `_summarize_tasks` and `_read_tasks` also use it when the conversation UI asks what tasks exist for the current conversation.

*Call graph*: called by 3 (_read_tasks, _summarize_tasks, update_todo_status).


##### `update_todo_list`  (lines 106–110)

```
async def update_todo_list(ctx: ToolContext, args: UpdateTodoListInput) -> ToolResult
```

**Purpose**: This is the tool that creates or fully replaces the current checklist. An agent uses it at the start of multi-step work, or when it needs to rewrite the plan.

**Data flow**: It receives the live tool context and a title plus a complete list of tasks. It confirms the extension store is available, builds a new `TodoBoard`, stores it under the current conversation's key, and returns the full board as JSON text in a tool result. Any previous board for that conversation is replaced.

**Call relations**: This is one of the two public tools advertised by `manifest`. It relies on `_require_ext` for storage access, `_board_key` to choose where to save the board, and `_board_result` to return the saved checklist to the agent.

*Call graph*: calls 3 internal fn (_board_key, _board_result, _require_ext); 2 external calls (__init__, loads).


##### `update_todo_status`  (lines 113–124)

```
async def update_todo_status(ctx: ToolContext, args: UpdateTodoStatusInput) -> ToolResult
```

**Purpose**: This is the tool that changes the status of one or more existing tasks. An agent uses it to mark work as started or finished while carrying out a multi-step request.

**Data flow**: It receives the tool context and a list of status changes, each using a 1-based task number. It finds the saved board for the current conversation, refuses to continue if no board exists, checks that every requested task number is valid, updates the matching task statuses, saves the revised board, and returns the current board. If a task number is outside the list, it raises an error instead of silently changing the wrong item.

**Call relations**: This is the second public tool advertised by `manifest`. It calls `_require_ext` to get storage access, `_board_key` and `_read_board` to load the current checklist, and `_board_result` to report the updated checklist back to the agent.

*Call graph*: calls 4 internal fn (_board_key, _board_result, _read_board, _require_ext); 1 external calls (loads).


##### `_summarize_tasks`  (lines 127–129)

```
async def _summarize_tasks(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick summary of whether a conversation has tasks and how many there are. It is used for the conversation slot system, where the UI or host may first ask for a lightweight summary before reading full details.

**Data flow**: It receives a conversation slot context, builds the storage key for that conversation, and reads the saved board. If there is no board, it returns `None`; if there is a board, it returns the number of tasks in it.

**Call relations**: The `TASKS_SLOT` provider uses this as its summary function. It depends on `_board_key` and `_read_board` so its answer matches the same stored checklist used by the todo tools.

*Call graph*: calls 2 internal fn (_board_key, _read_board).


##### `_read_tasks`  (lines 132–162)

```
async def _read_tasks(ctx: ConversationSlotContext) -> TasksSlotPayload
```

**Purpose**: This function prepares the saved checklist for display in the conversation UI. It returns a safe, bounded version of the board with counts and truncation information.

**Data flow**: It receives a conversation slot context and reads the board for that conversation. If none exists, it returns an empty `TasksSlotPayload`. If a board exists, it trims the title and task descriptions to allowed lengths, limits the number of displayed tasks, counts completed tasks, notes whether anything was shortened, and returns a `TasksSlotPayload` for the UI.

**Call relations**: The `TASKS_SLOT` provider uses this when the system needs the full task display data. It reads the same board written by `update_todo_list` and `update_todo_status`, then converts it into `ConversationTask` and `TasksSlotPayload` objects that the broader conversation-slot system understands.

*Call graph*: calls 2 internal fn (_board_key, _read_board); 2 external calls (__init__, __init__).


##### `manifest`  (lines 175–197)

```
def manifest() -> Manifest
```

**Purpose**: This function tells the host system what the todo extension offers. It registers the tools, prompt guidance, and conversation task display slot.

**Data flow**: It takes no input. It builds and returns a `Manifest` containing the extension name and version, two tool definitions, a prompt section loaded from the companion markdown file, and the task conversation slot provider.

**Call relations**: The larger extension system calls `manifest` when loading this pack. Through the returned manifest, it learns that `update_todo_list` and `update_todo_status` are callable tools and that `TASKS_SLOT` can be used to show checklist progress in the conversation interface.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-extension-store` — The per-workspace saved data that extensions use to remember their own settings and state.
- `reg-agent-records` — The saved assistant profiles, including their model choice, tools policy, setup needs, visibility, reasoning level, and spawn contracts.
- `reg-workspace-member-seat-state` — The shared record of workspaces, members, admins, invitations, seats, and workspace-level limits.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-transcript-history` — The saved conversation timeline, including messages, compacted summaries, final answers, costs, and readable history.
- `reg-audience-visibility-state` — The shared privacy labels that decide who may read or join conversation content and workspace objects.
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-object-artifact-site-store` — The shared store of workspace objects, files, artifacts, previews, reports, websites, todos, and objective records.
- `reg-object-change-journal` — The durable history of object changes, recording who changed what and what the object looked like before and after.
- `reg-schedules-automations` — The durable alarm clock for future work, pauses, monitors, source-change triggers, notification inbox items, and extension jobs.
- `reg-portal-slots-ui-state` — The structured conversation portal display state that extensions can fill with artifacts, sources, tasks, sites, and automations.
- `reg-blob-storage-state` — The raw byte/blob storage namespaces and content-addressed stored files that back artifacts, previews, environment files, workspace files, and deploy-wide assets.
- `reg-transcript-access-audit` — Audit records of privileged transcript reads, especially admin access to another member’s private conversation history.
