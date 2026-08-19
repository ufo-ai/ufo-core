# Objectives and progress verification  `stage-12.2`

This stage is shared behind-the-scenes support for managing longer goals that may take several turns or several workers to finish. It acts like a project notebook plus a referee. The tools file gives the agent ways to create an objective, break it into steps, record what was tried, ask whether the required proof is actually true, and send independent steps to subagents, which are helper agents working on separate pieces. Its key rule is that effort is not the same as success: a step is not complete just because someone reports doing it; the required check must pass.

The store file is the durable memory for this system. It records the goal, its steps, attempts, evidence, and current check results. It keeps an append-only history, meaning new facts are added instead of old ones being overwritten. Together, the tools decide how objectives are planned and verified, while the store preserves the evidence trail so progress remains trustworthy across turns.

## Files in this stage

### Objective planning and evidence tracking
Agent-facing objective tools plan goals, record progress evidence, verify completion checks, delegate independent work, and persist append-only objective history in the backing store.

### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `tool invocation during objective planning, progress recording, status reading, and subagent dispatch`

This file is the working surface of the objectives extension. It turns objective planning into a set of explicit tools: make a plan, record a step, run independent steps elsewhere, and read the current state. The important idea is that a step can have acceptance conditions: real checks such as “this file exists,” “this file contains this text,” or “this command succeeds.” When someone records that they did a step, the tool re-checks those conditions in the sandbox instead of trusting the claim. Like a checklist that requires looking at the actual item, not just ticking the box, this prevents empty progress reports.

The file also protects against weak plans. If a planned step says it will produce a file or text that already exists, the plan is rejected, because that condition would prove nothing about the future work. Command checks are treated differently: a test suite may already pass before work starts, but it can fail later, so it is still useful as a closing condition.

The tool handlers read and write objective records through the objective store, format those records for the agent to read, emit metrics for later measurement, and use the sandbox to evaluate conditions. One tool can also fan out ready independent steps to subagents, so parallel work is launched consistently from the stored plan rather than being re-decided each turn.

#### Function details

##### `_require_ext`  (lines 107–110)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small guard makes sure the tool is running with the objectives extension context available. Without that context, the tool cannot open the extension’s database transaction or read objective records.

**Data flow**: It receives the current tool context. If the context contains an extension object, it returns that object. If not, it stops immediately by raising an error, because continuing would mean trying to use missing storage and setup.

**Call relations**: The main tool handlers call this first when they need objective storage: planning, reading, recording, and dispatching steps all depend on it. It acts like checking that the workshop key is on the ring before trying to open the workshop.

*Call graph*: called by 4 (plan_objective, read_objective, record_step, run_independent_steps).


##### `render`  (lines 113–128)

```
def render(view: ObjectiveView) -> str
```

**Purpose**: This turns an objective record into a readable text summary for the agent. It shows the objective, how many steps are closed, each step’s state, its required conditions, recent events, and any unmet checks.

**Data flow**: It takes an ObjectiveView, reads its name, directive, attempts, steps, conditions, verdicts, and recent events, and builds a multi-line string. Nothing is saved or changed; the output is just a plain text report.

**Call relations**: After an objective is planned, recorded, or read, the tool handlers use this function to present the current state back to the user or agent. It relies on condition_summary to explain each acceptance condition in human-readable form.

*Call graph*: called by 3 (plan_objective, read_objective, record_step); 1 external calls (condition_summary).


##### `evaluate`  (lines 131–135)

```
async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This checks all acceptance conditions for one step and returns the results. It is used when a step has been attempted, or when an existing objective is read and its attempted steps need fresh checking.

**Data flow**: It receives the tool context and one StepView. For each condition listed on that step, it asks _verdict to test the real state. It gathers those individual verdicts into a tuple and returns them.

**Call relations**: record_step calls this after someone says a step was done, so the step closes only if the checks pass. read_objective also calls it to refresh the status of attempted steps before showing the objective.

*Call graph*: calls 1 internal fn (_verdict); called by 2 (read_objective, record_step).


##### `_verdict`  (lines 138–162)

```
async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict
```

**Purpose**: This performs one concrete acceptance check and records whether it passed. It is the place where abstract conditions like “file exists” become actual shell commands run in the sandbox.

**Data flow**: It receives a condition and a phase label, such as planning or recording. For file checks it builds a safe shell command using quoted file paths or text; for command checks it runs the command directly. It runs the command in the sandbox with a timeout, treats exit code 0 as success, emits a metric describing the result, and returns a ConditionVerdict with the condition, whether it held, and a short detail message.

**Call relations**: evaluate calls this for normal step-closing checks. plan_objective also calls it during planning, but only to reject file-based conditions that are already true before the work starts. It uses condition_summary to describe the check and emit_metric so operators can later see how often checks pass or fail.

*Call graph*: called by 2 (evaluate, plan_objective); 4 external calls (__init__, quote, emit_metric, condition_summary).


##### `plan_objective`  (lines 165–220)

```
async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult
```

**Purpose**: This records or revises an objective plan, including its ordered steps and the real-world conditions that prove each step is done. It refuses plans whose file-based proof is already true, because that would let a step close without doing new work.

**Data flow**: It receives the current tool context and a PlanObjectiveInput containing the objective name, directive, steps, and timeline description. It loads any existing objective with the same name, skips already-attempted steps, and checks new file_exists or file_contains conditions that are supposed to represent produced state. If any such condition already holds, it returns an error explaining which checks are useless. Otherwise it stores the plan through the Objectives store and returns a rendered summary.

**Call relations**: This is the handler behind the plan_objective tool definition. It starts by requiring the extension context, uses Objectives to read and save the record, calls _verdict for plan-time gate checks, and calls render to show the accepted plan.

*Call graph*: calls 3 internal fn (_require_ext, _verdict, render); 5 external calls (__init__, __init__, __init__, agent_current, condition_summary).


##### `record_step`  (lines 223–271)

```
async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult
```

**Purpose**: This records that a step was attempted or that it is blocked. For a completed attempt, it does not simply mark the step done; it re-checks the step’s acceptance conditions and reports whether the work actually satisfies them.

**Data flow**: It receives the current tool context and a RecordStepInput naming the objective, step, record kind, and evidence. It loads the objective, finds the named step, and writes the event. If the step is marked blocked, it records that state and avoids repeating the same block. If the step is marked did, it evaluates the step’s conditions, saves the check results, refreshes the objective, overlays the fresh verdicts for display, emits a metric, and returns a rendered report.

**Call relations**: This is the handler behind the record_step tool definition. It calls _require_ext to access storage, Objectives to find and update the objective, evaluate to test the step’s proof, _with_verdicts to make the displayed view reflect the newest check results, and render to show the outcome.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 5 external calls (__init__, __init__, __init__, agent_current, emit_metric).


##### `run_independent_steps`  (lines 274–320)

```
async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult
```

**Purpose**: This launches all currently runnable independent steps as background subagent tasks. It saves the current agent from manually re-deciding which steps can run in parallel.

**Data flow**: It receives the tool context and a RunIndependentStepsInput with the objective name and subagent profile. It loads the objective, asks the stored view which steps are runnable, and if none are available returns a message explaining that. For each runnable step, it spawns a background subagent with the overall directive and that step title, uses a deduplication key so the same step is not launched twice unnecessarily, emits a dispatch metric, and returns a list of dispatched subagent turn IDs.

**Call relations**: This is the handler behind the run_independent_steps tool definition. It uses _require_ext and Objectives to read the plan, then hands each ready step to ToolContext.spawn. The spawned children deliver their results back later, after which record_step is expected to record each result.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, spawn, agent_current, emit_metric).


##### `read_objective`  (lines 323–342)

```
async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult
```

**Purpose**: This reads an objective’s current state and returns a clear summary. Before showing attempted steps with acceptance conditions, it refreshes their checks so the report reflects the real state now, not just an older verdict.

**Data flow**: It receives the tool context and a ReadObjectiveInput naming the objective. It loads the objective from storage. If it does not exist, it returns an error. For each attempted step that has conditions, it evaluates the conditions again, saves the new check results, updates the display copy of the objective with those verdicts, and finally returns the rendered summary.

**Call relations**: This is the handler behind the read_objective tool definition. It calls _require_ext to access the extension, Objectives to load and update records, evaluate to refresh condition truth, _with_verdicts to update the report view, and render to produce the final text.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 4 external calls (__init__, __init__, __init__, agent_current).


##### `_with_verdicts`  (lines 345–355)

```
def _with_verdicts(view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]) -> ObjectiveView
```

**Purpose**: This creates a display version of an objective where one step has freshly supplied verdicts. It avoids changing the original object directly while letting the caller show the newest check result immediately.

**Data flow**: It receives an ObjectiveView, a step title, and a tuple of verdicts. It makes a copy of the objective, replacing only the matching step with a copy that contains the supplied verdicts. The result is a new ObjectiveView suitable for rendering.

**Call relations**: record_step uses this after checking a just-attempted step, and read_objective uses it while refreshing attempted steps. It relies on dataclasses.replace, which is a standard way to make a modified copy of a dataclass-like object.

*Call graph*: called by 2 (read_objective, record_step); 1 external calls (replace).


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `cross-cutting`

This file defines the database-backed store for an “objective,” which is a named goal inside a workspace and conversation. An objective has ordered steps. Each step may declare concrete acceptance checks, such as “this file exists,” “this file contains this text,” or “this command succeeds.” These checks matter because the worker should not be able to simply claim a step is done; the extension itself records whether the outside evidence actually matches the plan.

The file has three layers. First, it declares the database tables for objectives, steps, step events, and check results. Second, it defines small data shapes, such as `StepView` and `ObjectiveView`, that present the stored rows as understandable objects. Third, the `Objectives` class reads and writes those rows through an asynchronous database connection.

A key idea is that history is append-only for work events and check results. If a step was attempted, its acceptance conditions are frozen, so a later plan revision cannot weaken the test after the task turned out to be difficult. A step’s current state is then derived from its evidence: no event means pending, a “blocked” event means blocked, an attempt plus passing checks means done, and failing checks means unmet. Without this file, later turns would wake up without a reliable, shared record of what was planned, tried, blocked, or confirmed.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: This property answers a simple question: has anyone recorded that work was done on this step? It is used to distinguish an untouched step from one that was tried but still needs checking or repair.

**Data flow**: It reads the step’s stored event list. If any event has the kind `did`, it returns `true`; otherwise it returns `false`. It does not change anything.

**Call relations**: Other step-reading logic, especially `StepView.state` and `ObjectiveView.runnable`, relies on this property when deciding whether a step is still new, already tried, or eligible to be started.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: This property finds the currently standing block, if the latest event says the step is blocked. It prevents the system from repeatedly asking the same unresolved question as if it were new.

**Data flow**: It looks at the last event in the step’s event history. If that event exists and its kind is `blocked`, it returns that event; otherwise it returns nothing.

**Call relations**: The state calculation uses it indirectly through the latest event, and `ObjectiveView.runnable` uses it to avoid dispatching blocked work. `Objectives.record` also uses it before writing a new blocked event, so duplicate open blocks are not stored.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: This property turns a step’s history and check results into a plain status such as pending, attempted, done, blocked, or unmet. It is the central rule that says a step is not finished just because someone tried it.

**Data flow**: It reads the step’s events, acceptance conditions, and latest verdicts. A latest blocked event becomes `blocked`; no attempt becomes `pending`; an attempted step with no required checks becomes `done`; an attempted step with missing or incomplete verdicts remains `attempted`; and a fully checked step becomes either `done` or `unmet` depending on whether all checks passed.

**Call relations**: Objective-level summaries such as `ObjectiveView.confirmed` and `ObjectiveView.frontier` depend on this property. It is the bridge between raw stored facts and the higher-level question, “What still needs work?”


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: This property counts how many times work was recorded across all steps in an objective. It gives readers a quick sense of how much effort has already been spent.

**Data flow**: It reads every step’s event list and counts events whose kind is `did`. The output is a number; nothing is changed.

**Call relations**: It is part of the objective view used by other parts of the extension to understand progress. Together with `ObjectiveView.confirmed`, it can show when attempts keep rising but confirmed completions do not.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: This property counts how many steps are actually confirmed done according to the store’s rules. It does not trust effort alone; it uses each step’s derived state.

**Data flow**: It reads each step in the objective and asks for its `state`. It counts the ones whose state is `done` and returns that count.

**Call relations**: It builds on `StepView.state`, so it benefits from the same distinction between attempted, blocked, unmet, and truly done. It gives higher-level code a compact progress signal.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: This property returns the steps that can be started in parallel right now. A runnable step must be on the current frontier, marked independent, not already attempted, and not blocked.

**Data flow**: It starts from `ObjectiveView.frontier`, then filters those steps. It keeps only steps whose plan says they are independent, whose event history shows no attempt, and whose latest event is not an open block. It returns those steps as a tuple.

**Call relations**: It depends on `ObjectiveView.frontier`, `StepView.attempted`, and `StepView.open_block`. Dispatching code can use this as the safe fan-out list when deciding which planned steps may be handed to workers at the same time.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: This property returns every step that is not yet done. It is the objective’s current work surface: the set of steps still needing attention, repair, checking, or unblocking.

**Data flow**: It reads all steps and keeps the ones whose `state` is not `done`. It returns those remaining steps without changing the stored data.

**Call relations**: It relies on `StepView.state` for the meaning of done. `ObjectiveView.runnable` narrows this frontier further to find independent steps that can be started now.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: This helper turns a stored acceptance condition into a short human-readable phrase. It is useful when the system needs to explain what evidence a step requires.

**Data flow**: It receives one condition object. Depending on whether the condition is file existence, file contents, or command success, it formats a sentence-like summary and returns that text.

**Call relations**: It is a small presentation helper for the condition types defined in this file. It does not read the database or affect objective state.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: This method looks up one objective by conversation and name within the current workspace. The conversation boundary is important so a subagent using the same name does not accidentally pick up its parent’s objective.

**Data flow**: It receives a conversation ID and objective name, then queries the objective table for a matching row in this workspace. If none is found, it returns nothing; if one is found, it asks `_view` to build the full objective view with steps, events, and checks.

**Call relations**: It is called by `Objectives.plan` before creating or revising an objective. It hands successful database rows to `Objectives._view`, which expands the row into the complete readable object.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: This method finds the most recently created objective for a conversation in the current workspace. It is useful when code knows the conversation but not the objective name.

**Data flow**: It receives a conversation ID and queries the objective table for matching rows in this workspace, newest first, limited to one. If there is a result, it passes that row to `_view`; otherwise it returns nothing.

**Call relations**: Like `Objectives.named`, it uses `Objectives._view` to turn a database row into a full `ObjectiveView`. It is a read-side entry point for retrieving the active objective context for a conversation.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: This method creates a new objective or revises an existing objective’s plan. Its most important safeguard is that acceptance checks for already attempted steps are kept frozen, so later revisions cannot move the goalposts.

**Data flow**: It receives the conversation ID, objective name, directive, and planned steps. It first looks for an existing objective. If none exists, it inserts a new objective row. If one exists, it updates the directive, remembers acceptance checks for attempted steps, and removes unstarted old steps that are no longer kept. Then it updates or inserts each planned step with its position, title, checks, and independence flag. Finally, it reads the objective back and returns the complete view.

**Call relations**: It calls `Objectives.named` at the start to decide whether this is a create or revise operation, and again at the end to return the fresh view. It uses database insert, update, and delete operations to make the durable plan match the requested plan while preserving history-sensitive checks.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: This method appends a work event to a step, such as “did work” or “blocked.” It refuses to write a duplicate standing block with the same evidence, which prevents repeated wakeups from spamming the same unresolved question.

**Data flow**: It receives a step view, event kind, actor turn ID, and evidence text. It trims the evidence to a fixed maximum length, checks whether the same block is already open, and either returns `false` without writing or inserts a new event row and returns `true`.

**Call relations**: It uses `StepView.open_block` to recognize an already-standing block. The rows it writes are later read by `Objectives._view`, which turns them into `StepEvent` objects used by `StepView.state`, `ObjectiveView.attempts`, and other progress calculations.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: This method records the extension’s own verdicts after evaluating a step’s acceptance checks. It appends a new check result instead of overwriting the old one, so the record can show how the truth changed over time.

**Data flow**: It receives a step, a set of condition verdicts, and the actor turn ID. It converts each verdict into JSON-friendly data containing the condition, whether it held, and a detail message, then inserts a new check row for that step.

**Call relations**: The check rows written here are later read by `Objectives._view`, which keeps the latest check for each step and turns it into `ConditionVerdict` objects. `StepView.state` then uses those verdicts to decide whether an attempted step is done or unmet.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: This private method builds the full in-memory picture of an objective from database rows. It gathers the objective’s steps, their event histories, and their latest check results into an `ObjectiveView`.

**Data flow**: It receives one objective database row. It queries the step table for that objective, then queries events and checks for those steps. It groups events by step, keeps the latest check per step, parses stored condition data and verdict data, and returns an `ObjectiveView` containing `StepView` and `StepEvent` objects.

**Call relations**: It is called by `Objectives.named` and `Objectives.on_conversation` whenever a stored objective must be read. It calls `_conditions` and `_verdicts` to turn raw JSON-like database payloads back into typed condition and verdict objects.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: This helper parses stored acceptance-condition data back into condition objects the rest of the file can use safely. It also rejects unknown condition kinds instead of silently treating them as valid.

**Data flow**: It receives a raw payload, usually a list loaded from the database. If the payload is not a list, it returns an empty tuple. For each listed item, it looks at the `kind` field and validates it as file-exists, file-contains, or command-succeeds; unknown kinds raise an error. The result is a tuple of condition objects.

**Call relations**: It is used by `Objectives._view` when rebuilding each step’s acceptance checks, and by `_verdicts` when parsing the condition stored inside each verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This helper parses stored check-result data into `ConditionVerdict` objects. It gives the rest of the code a clean, typed view of what each condition check found.

**Data flow**: It receives a raw payload, usually a list from the check table. If it is not a list, it returns an empty tuple. For each dictionary item, it parses the nested condition with `_conditions`, converts the stored pass/fail value to a boolean, converts the detail to text, and returns all verdicts as a tuple.

**Call relations**: It is called by `Objectives._view` when rebuilding the latest check results for a step. It calls `_conditions` so verdicts refer to the same validated condition objects used by planned steps.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).
