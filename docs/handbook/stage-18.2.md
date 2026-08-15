# Durable objectives and checked progress  `stage-18.2`

This stage is the project’s memory and gatekeeper for long-running work. It is used during the main work loop, when an agent needs to plan a goal, split it into steps, hand work to others, or check whether progress is real. Instead of trusting a worker’s short-term memory or a simple “done” message, it keeps a durable record in the database and rechecks the stated conditions before marking a step complete.

The package marker, __init__.py, simply labels this extension so the larger system can recognize it. The store.py file is the filing cabinet. It saves and retrieves objectives, their steps, attempts to complete them, blockers, and condition checks. The tools.py file is the front desk. It exposes the actions agents can use to create plans, inspect status, delegate tasks, report blockers, and request completion checks. Together, the tools decide what should happen, while the store preserves the facts so later agents can continue from reliable records.

## Files in this stage

### Objective planning tools and storage
The objectives extension exposes planning and progress-checking tools backed by durable records for objectives, steps, attempts, blocks, and condition checks.

### `extensions/objectives/ufo_ext_objectives/__init__.py`

`other` · `import time`

This is the package entry file for the objectives extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, meaning other parts of the project can refer to code inside this directory using normal import paths. Here, the file only contains a short documentation string: “The objectives extension.”

Its main value is structural rather than behavioral. Think of it like a label on a folder in a filing cabinet: it does not do the work stored in the folder, but it makes the folder recognizable and usable by the rest of the system. Without this file, depending on the Python version and packaging setup, importing this extension could be less clear or fail in environments that expect traditional package markers.

There are no functions, classes, settings, or side effects here. When the package is imported, Python may read this file, record its documentation string, and then move on to the actual modules in the extension.


### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `request handling during objective tool calls`

This file is the control panel for the objectives extension. An objective is a piece of work that may take more than one turn, so it needs a durable plan: a name, a directive, and ordered steps. Each step can include acceptance conditions, such as “this file exists,” “this file contains this text,” or “this command succeeds.” Those conditions are the receipt that proves the step really finished.

The important idea is that planning and reporting are kept separate from proof. When a step is recorded as “did,” the code re-checks the current sandbox state instead of trusting the report. This is like a checklist where ticking a box also makes someone inspect the workbench. If the file is missing or the command fails, the step remains unmet and the result says what failed.

The file also prevents weak plans. If a step says it will produce a file, but that file already exists while planning, the plan is refused because that condition would prove nothing. Command checks are treated differently: a test suite may already pass before work starts, but it can fail later, so it is still useful.

Finally, the file can dispatch independent steps to subagents, read the latest objective state, and format objective progress into plain text for the caller.

#### Function details

##### `_require_ext`  (lines 107–110)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small safety check makes sure the tool call has access to the objectives extension context. Without that context, the tool cannot open the database transaction it needs to read or write objective records.

**Data flow**: It receives the current tool context. If the extension context is present, it returns it. If it is missing, it stops immediately by raising an error, because continuing would mean later code fails in a less clear way.

**Call relations**: The main tool handlers call this at the start of their work: planning an objective, recording a step, reading an objective, and dispatching independent steps. It acts like checking that you have the right key before trying to open the filing cabinet.

*Call graph*: called by 4 (plan_objective, read_objective, record_step, run_independent_steps).


##### `render`  (lines 113–128)

```
def render(view: ObjectiveView) -> str
```

**Purpose**: This turns an objective record into readable text for the agent or user. It summarizes the objective, each step, what each step still needs, recent events, and any failed checks.

**Data flow**: It receives an ObjectiveView, which is a snapshot of one objective. It reads the objective name, directive, attempt counts, step states, acceptance conditions, verdicts, and recent events. It produces one multi-line string that can be placed in a tool result.

**Call relations**: After an objective is planned, checked, recorded, or read, the tool handlers call this to present the current state. It relies on condition_summary to describe each acceptance condition in human-friendly words.

*Call graph*: called by 3 (plan_objective, read_objective, record_step); 1 external calls (condition_summary).


##### `evaluate`  (lines 131–135)

```
async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This checks every acceptance condition attached to a step and collects the results. It is used when a step has been attempted, or when the system refreshes the truth of an already attempted step.

**Data flow**: It receives the tool context and a step view. For each condition listed under the step’s accepts field, it asks _verdict to test that condition against the sandbox. It returns a tuple of verdicts, one per condition, saying whether each condition currently holds.

**Call relations**: record_step calls this after someone says a step was done, so the claim can be verified. read_objective also calls it for attempted steps, so the displayed objective state reflects the latest real state. It delegates the actual condition-by-condition testing to _verdict.

*Call graph*: calls 1 internal fn (_verdict); called by 2 (read_objective, record_step).


##### `_verdict`  (lines 138–162)

```
async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict
```

**Purpose**: This is the actual condition checker. It translates a condition like “file exists,” “file contains text,” or “command succeeds” into a sandbox command, runs it, records a metric, and returns whether the condition is true.

**Data flow**: It receives a tool context, one condition, and a phase name that says why the check is happening, such as planning or recording. It builds the right shell command, runs it in the sandbox with a timeout, reads the exit code, emits a metric counting the result, and returns a ConditionVerdict with the condition, a true-or-false result, and a short detail message.

**Call relations**: evaluate calls this for normal step checks. plan_objective also calls it during planning to reject produced-state conditions that are already true. It uses shlex.quote to safely put file paths and text into shell commands, condition_summary to describe conditions, and emit_metric so production behavior can be observed later.

*Call graph*: called by 2 (evaluate, plan_objective); 4 external calls (__init__, quote, emit_metric, condition_summary).


##### `plan_objective`  (lines 165–220)

```
async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult
```

**Purpose**: This records or revises an objective plan, but first rejects acceptance conditions that would give false confidence. In particular, it refuses file-based conditions that are already true before the step has started.

**Data flow**: It receives the tool context and the requested plan: objective name, directive, steps, and a user-facing description. It loads any existing objective with the same name, compares planned steps with stored ones, and checks new file-based acceptance conditions against the sandbox. If any such condition is already true, it returns an error explaining which conditions are vacuous. Otherwise it stores the plan in the objectives store and returns a rendered view of the objective.

**Call relations**: This is the handler behind the plan_objective tool definition. It starts by using _require_ext to get database access. It uses _verdict for plan-time condition checks, Objectives to read and save the plan, condition_summary to explain refused conditions, and render to show the saved result.

*Call graph*: calls 3 internal fn (_require_ext, _verdict, render); 5 external calls (__init__, __init__, __init__, agent_current, condition_summary).


##### `record_step`  (lines 223–271)

```
async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult
```

**Purpose**: This records that a step was attempted or is blocked. If the caller says the step was done, it does not simply close the step; it re-checks the step’s acceptance conditions and reports whether they actually hold.

**Data flow**: It receives the tool context and a record request containing the objective name, exact step title, kind of record, and evidence. It loads the objective, finds the named step, and writes the event. If the step is marked blocked, it records or reports the block. If the step is marked did, it evaluates the step’s conditions, saves the check results, refreshes the objective, overlays the new verdicts for display, emits a metric about the final state, and returns the rendered objective.

**Call relations**: This is the handler behind the record_step tool definition. It uses _require_ext for extension access, Objectives for storage, evaluate for proof checks, _with_verdicts to place fresh verdicts into the displayed view, render for the response, and metrics so the system can count blocked, repeated, closed, or unmet recordings.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 5 external calls (__init__, __init__, __init__, agent_current, emit_metric).


##### `run_independent_steps`  (lines 274–320)

```
async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult
```

**Purpose**: This starts all currently runnable independent steps as background subagent tasks. It lets work fan out in parallel instead of forcing one agent to take each independent step one by one.

**Data flow**: It receives the tool context and a request naming the objective and the subagent profile to use. It loads the objective, reads which steps are runnable, and if none are ready it returns a plain explanation. For each runnable step, it spawns a background subagent with the objective directive and that step’s title, uses a deduplication key so the same step is not started twice in the same way, records a dispatch metric, and returns a list of dispatched step titles and subagent turn IDs.

**Call relations**: This is the handler behind the run_independent_steps tool definition. It uses _require_ext and Objectives to read the plan, then hands each runnable step to ToolContext.spawn. The spawned children deliver their results back later, and the caller is told to record each step when those results arrive.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, spawn, agent_current, emit_metric).


##### `read_objective`  (lines 323–342)

```
async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult
```

**Purpose**: This reads the current state of an objective and refreshes the truth of attempted steps before showing it. It is meant to help an agent see what remains to be done before re-attempting work.

**Data flow**: It receives the tool context and the objective name. It loads the objective from storage. If it does not exist, it returns an error. For each attempted step that has acceptance conditions, it re-evaluates those conditions, saves the latest check results, updates the displayed view, and finally returns a rendered text summary.

**Call relations**: This is the handler behind the read_objective tool definition. It uses _require_ext for extension access, Objectives for reading and saving checks, evaluate to re-test conditions, _with_verdicts to update the in-memory view, and render to produce the readable output.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 4 external calls (__init__, __init__, __init__, agent_current).


##### `_with_verdicts`  (lines 345–355)

```
def _with_verdicts(view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]) -> ObjectiveView
```

**Purpose**: This creates an updated copy of an objective view with fresh verdicts attached to one named step. It is used for display so the response can show the latest check results immediately.

**Data flow**: It receives an ObjectiveView, a step title, and a tuple of verdicts. It walks through the objective’s steps, replaces only the matching step with a copy containing the new verdicts, and returns a new ObjectiveView with that updated step list. It does not change the original object in place.

**Call relations**: record_step calls this after checking a just-attempted step, and read_objective calls it while refreshing attempted steps. Internally it uses dataclasses.replace, which is a helper for making modified copies of data objects.

*Call graph*: called by 2 (read_objective, record_step); 1 external calls (replace).


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `active whenever objectives are planned, resumed, checked, or updated during a conversation turn`

This file is the memory system for the objectives extension. An objective is a named goal inside a conversation. It has ordered steps, and each step can declare acceptance conditions, such as “this file exists,” “this file contains this text,” or “this command succeeds.” The important rule is that the extension, not the worker, decides whether a step is done by checking those conditions.

The file stores four kinds of database records: objectives, planned steps, worker events such as “did” or “blocked,” and condition-check results. Events and checks are appended rather than rewritten. That means the history is like a lab notebook: later plan changes cannot erase what already happened.

The view classes turn raw stored rows into useful snapshots. A StepView can say whether a step is pending, attempted, blocked, done, or unmet. An ObjectiveView can count attempts, count confirmed completed steps, and identify the current frontier: the steps not yet done. Some frontier steps may be runnable in parallel if the plan marked them as independent.

The Objectives class is the main doorway to the database. It can find an objective, create or revise a plan, record a worker’s event, store the extension’s own check results, and rebuild a complete ObjectiveView from stored rows.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: This property answers the simple question: has anyone recorded doing work on this step yet? It matters because a step with conditions should not be treated as failed or complete before any attempt has been made.

**Data flow**: It reads the step’s stored events → looks for any event whose kind is the recorded “did” marker → returns true if at least one such event exists, otherwise false. It does not change anything.

**Call relations**: Other step-state logic uses this as a basic signal. For example, StepView.state uses it to distinguish a step that has not started from one that has been tried and now needs condition-check evidence.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: This property finds whether the step is currently blocked by an unanswered question or obstacle. It only treats the most recent event as active, so an older block can be cleared by later activity.

**Data flow**: It reads the step’s event history → takes the latest event if one exists → returns that event only when its kind is “blocked”; otherwise it returns nothing. It does not write to storage.

**Call relations**: StepView.state uses this to report a blocked state. ObjectiveView.runnable uses it to avoid dispatching work that is already waiting on a block, and Objectives.record uses it to avoid recording the same block message again.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: This property translates a step’s history and condition checks into one clear status: pending, attempted, done, blocked, or unmet. It is the core rule that prevents a worker’s claim from being mistaken for verified completion.

**Data flow**: It reads the step’s latest event, whether it has been attempted, its declared acceptance conditions, and the latest condition verdicts → applies the file’s rules in order → returns a status string. It does not change the step.

**Call relations**: ObjectiveView.confirmed and ObjectiveView.frontier rely on this property to decide which steps are complete and which still need attention. The rest of the extension can then act on a clear status instead of reinterpreting raw events.


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: This property counts how many recorded “did” attempts exist across all steps in the objective. It helps show whether the objective is consuming repeated work without making progress.

**Data flow**: It reads every step and every event inside those steps → counts events marked as “did” → returns that count as a number. It does not change anything.

**Call relations**: This is a reporting signal on the objective view. It complements ObjectiveView.confirmed: together they show the difference between work attempted and work actually verified as complete.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: This property counts how many steps are currently verified as done. It measures confirmed progress, not just claimed progress.

**Data flow**: It reads each step → asks each StepView for its current state → counts the steps whose state is “done” → returns that count. It does not write anything.

**Call relations**: This depends on StepView.state, which includes the condition-check rules. It is meant to be read by higher-level objective logic or user-facing summaries that need to know how much of the plan has really closed.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: This property picks out the frontier steps that may be started at the same time. A step qualifies only if the plan said it is independent, it has not already been attempted, and it is not currently blocked.

**Data flow**: It reads the objective’s frontier steps → filters for steps marked independent, not attempted, and without an open block → returns those steps as a tuple. It does not change the objective.

**Call relations**: It builds on ObjectiveView.frontier, StepView.attempted, and StepView.open_block. A dispatcher can use it when deciding which pieces of work can be fanned out safely instead of running strictly one after another.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: This property returns the steps that are not yet done. It is the objective’s current working edge: the remaining places where attention may be needed.

**Data flow**: It reads all steps in order → asks each step for its state → keeps every step whose state is not “done” → returns them as a tuple. It does not change anything.

**Call relations**: ObjectiveView.runnable narrows this frontier further to find parallel-ready work. Other code can use the frontier as the plain list of unfinished steps.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: This function turns a condition object into a short human-readable phrase. It is useful when the system needs to explain what a step is waiting to prove.

**Data flow**: It receives one condition, such as a file-exists check, file-contains check, or command-succeeds check → formats the important fields into a sentence-like string → returns that string. It does not read or write storage.

**Call relations**: It stands apart from the database flow as a display helper. Code that presents objective conditions to a person can call it instead of exposing the raw stored shape.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: This method finds one objective by conversation and name within the current workspace. The conversation filter is important because subagents can have their own conversations and should not accidentally take over a parent conversation’s objective with the same name.

**Data flow**: It receives a conversation ID and objective name, and uses the Objectives instance’s workspace ID and database connection → queries the objective table for an exact match → returns nothing if no row exists, or rebuilds and returns a full ObjectiveView if it does.

**Call relations**: Objectives.plan calls this first to decide whether it is creating a new objective or revising an existing one. When a row is found, this method hands it to Objectives._view so the caller gets the full objective with steps, events, and latest checks.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: This method finds the most recently created objective for a conversation in the current workspace. It gives callers a convenient way to resume or inspect the active objective without already knowing its name.

**Data flow**: It receives a conversation ID and reads the workspace ID from the Objectives instance → queries objective rows for that conversation, newest first, limited to one → returns nothing if there is no objective, or returns a rebuilt ObjectiveView for the newest one.

**Call relations**: Like Objectives.named, it delegates the row-to-view work to Objectives._view. It is a lookup path for code that is oriented around the conversation rather than a specific objective name.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: This method creates a new objective plan or revises an existing one. Its most important safeguard is that once a step has been attempted, its acceptance conditions are frozen, so the worker cannot make the goal easier after seeing the work.

**Data flow**: It receives the conversation ID, objective name, directive text, and planned steps → looks for an existing objective → inserts a new objective or updates the old directive → removes unstarted old steps that are no longer kept → updates or inserts each planned step, preserving frozen conditions for attempted steps → returns the freshly rebuilt ObjectiveView.

**Call relations**: It starts by calling Objectives.named. It then writes through SQL insert, update, and delete operations as needed. At the end it calls Objectives.named again to return the durable version of what was saved, and raises an error if the objective somehow cannot be read back.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: This method appends a worker event to a step, such as “did work” or “blocked.” It also prevents the same still-open block from being recorded repeatedly, which avoids filling a thread with the same unanswered question.

**Data flow**: It receives a StepView, an event kind, the actor turn ID, and evidence text → trims the evidence to the maximum stored length → checks whether this is a duplicate of the current open block → if duplicate, returns false without writing; otherwise inserts a new event row and returns true.

**Call relations**: It uses StepView.open_block to recognize repeated block reports. Higher-level objective tools can call it after a worker attempts a step or reports a blocker, and the saved event later becomes part of the StepView rebuilt by Objectives._view.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: This method records the extension’s own verdicts after evaluating a step’s acceptance conditions. It appends a new check instead of overwriting old ones, so the history can show that something passed at one time and failed later.

**Data flow**: It receives a StepView, a tuple of condition verdicts, and the actor turn ID → converts each verdict into stored JSON containing the condition, whether it held, and detail text → inserts a new objective_check row. It returns no value.

**Call relations**: This is called after condition evaluation elsewhere in the extension. Objectives._view later reads the latest check for each step and turns it back into StepView.verdicts, which StepView.state uses to decide whether a step is done or unmet.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: This private method rebuilds a complete ObjectiveView from database rows. It gathers the objective’s steps, their event history, and their latest condition-check results into one clean in-memory snapshot.

**Data flow**: It receives an objective database row → queries step rows for that objective, event rows for those steps, and check rows for those steps → groups events and keeps the latest check per step → parses stored JSON conditions and verdicts → returns an ObjectiveView containing StepView and StepEvent objects.

**Call relations**: Objectives.named and Objectives.on_conversation call this after finding an objective row. It calls _conditions and _verdicts to turn stored JSON back into typed condition and verdict objects before building the final view.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: This helper turns stored condition data back into condition objects the rest of the code can use safely. It recognizes the supported condition kinds and rejects unknown ones.

**Data flow**: It receives a stored payload, usually a JSON-like list from the database → if the payload is not a list, returns an empty tuple → validates each item as a file-exists, file-contains, or command-succeeds condition → returns the parsed conditions as a tuple, or raises an error for an unknown kind.

**Call relations**: Objectives._view calls this when rebuilding each StepView’s accepted conditions. _verdicts also calls it to parse the condition embedded inside each stored verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This helper turns stored condition-check results back into ConditionVerdict objects. Those verdicts are what StepView.state uses to decide whether attempted work actually satisfies the plan.

**Data flow**: It receives a stored payload, usually a JSON-like list from the database → if the payload is not a list, returns an empty tuple → for each dictionary item, parses the embedded condition, converts the hold flag to true or false, and converts the detail to text → returns a tuple of ConditionVerdict objects.

**Call relations**: Objectives._view calls this when a step has saved check rows. This helper calls _conditions for the nested condition data, then hands the reconstructed verdicts to StepView.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).
