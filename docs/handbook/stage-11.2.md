# Objective and Workflow State Machines  `stage-11.2`

This stage is the project’s “job tracker” for work that takes more than one agent turn. It supports the main work loop and also helps after a pause, when the agent wakes up and must remember unfinished goals. The objectives manifest tells UFO to load this extension and to bring long-running objectives back into view at the start of each turn. The objectives tools let an agent plan a goal, record step evidence, run checks, delegate parts to other workers, and mark progress. They do not accept “I’m done” by itself; they re-run the promised checks first. The objectives store is the durable notebook that keeps goals, steps, attempts, evidence, and verification results.

Several specialized workflows use the same idea. The application builder guides a worker from a member’s request to a deployable UFO app page, with tools for design, writing, checking, repair, and publishing. The application audit is its quality gate, deciding pass or repair from browser, layout, accessibility, and product checks. The brief pipeline defines a simpler writing machine: outline, draft, then critique.

## Files in this stage

### Objective lifecycle state
Objective extension files register turn-time rehydration, expose planning and verification tools, and persist step evidence in durable state.

### `extensions/objectives/ufo_ext_objectives/manifest.py`

`orchestration` · `startup and user prompt handling`

This extension exists because an agent can lose its short-term working memory between turns, especially after a scheduled wake-up or after another worker hands control back. Without this file’s hook, an objective might still exist in durable storage, but the agent would not be reminded what it was trying to finish, which steps remain open, or what evidence is still missing.

The file does two things. First, it defines a prompt section that teaches the agent when to create an objective, what counts as a real step, and how to record progress honestly. In plain terms, it tells the agent: save important multi-turn work, split it into meaningful pieces, and do not mark something done just because you hope it is done.

Second, it registers a hook. A hook is code the system automatically runs at a particular moment. Here, the hook runs when a user prompt is submitted. It looks up whether the current conversation has an objective. If it does, it builds a compact “frontier” summary: the objective name, directive, attempt count, closed-step count, the currently relevant steps, their acceptance conditions, and any already-raised blocker. This is like putting the project checklist back on the desk before work resumes.

#### Function details

##### `_inject_frontier`  (lines 51–88)

```
async def _inject_frontier(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function adds the current objective’s live checklist into the agent’s prompt at the start of a turn. It helps the agent continue long-running work without relying on memory from earlier turns.

**Data flow**: It receives a hook context from the system. If there is no current turn, it does nothing. Otherwise, it opens the extension’s stored data, uses the current agent’s workspace to find any objective tied to this conversation, and stops if none exists. When it finds one, it records a metric, turns the objective’s open work into readable lines of text, summarizes each acceptance condition, notes blockers that were already raised with the member, and warns when attempted steps still need their conditions checked. It returns an injected prompt context containing that text, which becomes visible to the agent.

**Call relations**: The UFO hook system calls this function when a user prompt is submitted, because `manifest` registers it for that event. Inside, it asks `agent_current` which workspace is active, creates an `Objectives` view over the stored objective data, uses `condition_summary` to make acceptance conditions readable, reports the injection with `emit_metric`, and finally hands the finished text to `InjectContext` so the platform can add it to the turn.

*Call graph*: 5 external calls (__init__, __init__, agent_current, emit_metric, condition_summary).


##### `manifest`  (lines 91–103)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the objectives extension to the UFO runtime. It names the extension, lists the tools it provides, registers the automatic prompt-injection hook, and adds the instruction text the agent should see.

**Data flow**: It takes no input. It gathers constants and imported tool definitions into a single manifest object: the extension name and version, the four objective-related tools, the hook that should run on user prompt submission, and the prompt section explaining how objectives should be used. It returns that manifest for the runtime to load.

**Call relations**: The extension loader calls this function when the objectives extension is being set up. The function builds a `HookSpec` that points to `_inject_frontier`, creates a `PromptSection` for the agent-facing instructions, and wraps everything in a `Manifest` so the rest of UFO knows which tools and prompt behavior this extension contributes.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `tool invocation during objective planning, progress recording, reading, and delegation`

This file is the practical front door for the objectives extension. An objective is longer-running work, broken into named steps. Each step may include acceptance conditions: concrete things that must be true, such as “this file exists,” “this file contains this text,” or “this command succeeds.” Without this file, agents could store plans but would not have the tools to create them, inspect them, mark progress, or send independent steps to subagents.

The key idea is that saying “I did it” is not the same as proving “it is done.” When a step is recorded as attempted, the file re-checks the step’s conditions in the sandbox, which is the controlled environment where commands can be run. If the checks fail, the step stays unmet and the result explains what failed.

It also prevents a misleading kind of plan: a step cannot use a produced artifact, like an already-existing file, as proof of future work. That would be like checking off “bake a cake” because there was already a cake on the table before cooking began. The file also formats objectives for humans, updates stored objective state through the Objectives store, emits metrics so operators can see how often checks pass or fail, and can fan out independent steps to subagents.

#### Function details

##### `_require_ext`  (lines 95–98)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small helper makes sure the tool call has the extension context it needs. The extension context is the extra project-specific state and services attached to a tool run, such as access to stored objectives.

**Data flow**: It receives the current tool context. If the context contains an extension object, it returns that object. If it does not, it stops the operation by raising an error, because the objectives tools cannot safely work without their storage and transaction support.

**Call relations**: The main tool handlers call this at the start of their work. `plan_objective`, `record_step`, `run_independent_steps`, and `read_objective` all rely on it before they open database transactions or read objective state.

*Call graph*: called by 4 (plan_objective, read_objective, record_step, run_independent_steps).


##### `render`  (lines 101–116)

```
def render(view: ObjectiveView) -> str
```

**Purpose**: This turns an objective view into readable text for the agent or user. It is the report card for an objective: what it is, how many steps are closed, what each step still needs, and recent evidence.

**Data flow**: It receives an `ObjectiveView`, which is a snapshot of one objective and its steps. It builds a list of plain text lines from the objective name, directive, step states, acceptance conditions, failed verdicts, and the last couple of events for each step. It returns one joined string ready to put in a tool result.

**Call relations**: After objective state is created, updated, or refreshed, the tool handlers call `render` to explain the current state. While building the text, it asks `condition_summary` to turn each technical condition into a short human-readable phrase.

*Call graph*: called by 3 (plan_objective, read_objective, record_step); 1 external calls (condition_summary).


##### `evaluate`  (lines 119–123)

```
async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This checks all acceptance conditions for one step. It is used when the system needs to know whether an attempted step really satisfies its promised proof.

**Data flow**: It receives the tool context and a step view. For every condition attached to the step, it asks `_verdict` to test that condition in the sandbox. It collects the resulting pass-or-fail verdicts and returns them as a tuple.

**Call relations**: `record_step` calls this after an agent says a step was attempted, and `read_objective` calls it to refresh the truth of already-attempted steps. It delegates the actual checking of each single condition to `_verdict`.

*Call graph*: calls 1 internal fn (_verdict); called by 2 (read_objective, record_step).


##### `_verdict`  (lines 126–150)

```
async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict
```

**Purpose**: This tests one acceptance condition against real state and records a metric about the result. It answers the simple question: does this promised condition hold right now?

**Data flow**: It receives the tool context, one condition, and the phase that asked for the check, such as planning or recording. It converts the condition into a shell command: for example, test whether a path exists, search for fixed text in a file, or run a supplied command. It runs that command in the sandbox with a timeout, treats exit code zero as success, emits a metric describing the kind of condition and whether it held, and returns a `ConditionVerdict` with the condition, the true-or-false result, and a readable detail string.

**Call relations**: `evaluate` uses `_verdict` for normal step closure checks. `plan_objective` also uses it earlier as a gate, to reject produced-state conditions that are already true before the work starts. It uses `shlex.quote` to safely place paths and text into shell commands, `condition_summary` for readable details, and `emit_metric` so production behavior can be observed.

*Call graph*: called by 2 (evaluate, plan_objective); 4 external calls (__init__, quote, emit_metric, condition_summary).


##### `plan_objective`  (lines 153–208)

```
async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult
```

**Purpose**: This creates or revises an objective plan, but first rejects acceptance checks that would prove nothing. It is the tool an agent uses when it wants future turns to remember the goal, steps, and what counts as done.

**Data flow**: It receives the tool context and a plan containing an objective name, directive, and ordered steps. It loads any existing objective with the same name, then checks new file-based acceptance conditions that claim the step will produce state. If one of those conditions is already true, it returns an error explaining that the condition is empty proof. Otherwise, it stores the plan through the objectives store and returns a rendered view of the saved objective.

**Call relations**: This function starts by getting the extension context through `_require_ext`. It reads existing state through `Objectives`, uses `_verdict` during planning to catch vacuous file conditions, uses `condition_summary` in error messages, saves the accepted plan, and finally hands the saved view to `render` so the caller sees what was recorded.

*Call graph*: calls 3 internal fn (_require_ext, _verdict, render); 5 external calls (__init__, __init__, __init__, agent_current, condition_summary).


##### `record_step`  (lines 211–259)

```
async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult
```

**Purpose**: This records that a step was attempted or that it is blocked. For attempted work, it does not simply trust the claim; it re-checks the step’s acceptance conditions before showing the updated objective state.

**Data flow**: It receives the tool context and a record containing the objective name, exact step title, kind of record, and evidence. It looks up the objective and step, returning clear errors if either is missing. If the step is marked blocked, it stores that block and reports whether it was new or a repeated block. If the step is marked did, it records the attempt, evaluates the step’s conditions, stores the check results, refreshes the objective, overlays the newest verdicts, emits a metric, and returns a rendered status report.

**Call relations**: This is one of the central tool handlers. It uses `_require_ext` to access extension services, `Objectives` to read and write objective state, `evaluate` to test whether an attempted step actually holds, `_with_verdicts` to show the freshest verdicts in the returned view, `render` to format the answer, and `emit_metric` to count what happened.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 5 external calls (__init__, __init__, __init__, agent_current, emit_metric).


##### `run_independent_steps`  (lines 262–308)

```
async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult
```

**Purpose**: This sends every currently runnable independent step to a subagent at the same time. It saves the current agent from manually figuring out which steps can run in parallel.

**Data flow**: It receives the tool context, an objective name, and a subagent profile such as a coding profile. It loads the objective, finds the steps marked runnable, and if none are ready it returns an explanatory message. For each runnable step, it spawns a background subagent with the overall directive and that step’s title, using a deduplication key so the same step is not accidentally launched twice in the same way. It returns a list of dispatched steps and their subagent turn IDs.

**Call relations**: The function first uses `_require_ext` and `Objectives` to read the current objective. It then calls `ToolContext.spawn` for each runnable step and emits a dispatch metric. Unlike `record_step`, it does not close anything itself; it starts child work and tells the caller to wait for results and record each step later.

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, spawn, agent_current, emit_metric).


##### `read_objective`  (lines 311–330)

```
async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult
```

**Purpose**: This retrieves an objective and presents its current status. If attempted steps have acceptance conditions, it re-checks them so the reader sees whether they still hold now.

**Data flow**: It receives the tool context and an objective name. It loads the matching objective, or returns an error if none exists. For every step that has been attempted and has acceptance conditions, it evaluates those conditions again, stores the new check results, and updates the view used for display. It returns the rendered objective report.

**Call relations**: This is the read-only-looking status tool, though it may update stored check results to keep them fresh. It uses `_require_ext` and `Objectives` to load and update state, `evaluate` to re-check real-world conditions, `_with_verdicts` to keep the displayed view current, and `render` to produce the final human-readable output.

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 4 external calls (__init__, __init__, __init__, agent_current).


##### `_with_verdicts`  (lines 333–343)

```
def _with_verdicts(view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]) -> ObjectiveView
```

**Purpose**: This returns a copy of an objective view with fresh verdicts attached to one named step. It lets the tool response show the newest check results immediately, without mutating the original snapshot in place.

**Data flow**: It receives an objective view, a step title, and a tuple of verdicts. It creates a replacement objective view whose steps are the same except that the matching step is copied with the supplied verdicts. The output is a new `ObjectiveView` value ready for rendering.

**Call relations**: `record_step` uses this after checking a just-attempted step, and `read_objective` uses it while refreshing attempted steps. Internally it relies on `dataclasses.replace`, which is a standard helper for making changed copies of data objects.

*Call graph*: called by 2 (read_objective, record_step); 1 external calls (replace).


### `extensions/objectives/ufo_ext_objectives/__init__.py`

`other` · `startup/import time`

This is the package marker file for the objectives extension. In Python, a folder with an `__init__.py` file can be imported as a package, which means other parts of the system can refer to this extension by its package name. Think of it like a label on a drawer: the drawer may contain many useful tools in other files, but this label tells Python and readers what the drawer is for. Here, the only content is a docstring, which says that this package is “The objectives extension.” There are no functions, classes, settings, or side effects in this file. If it were missing, imports that expect `extensions.objectives.ufo_ext_objectives` to be a regular Python package could fail or behave differently depending on the Python environment.


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `objective planning, turn wake-up, and progress recording`

This file defines how the objectives extension stores and reads its work. An objective is a named goal inside a conversation. It has ordered steps, and each step can declare acceptance conditions such as “this file exists,” “this file contains this text,” or “this command succeeds.” Those conditions are stored as data so the extension can check them later, instead of trusting a worker’s own claim.

The database tables keep four kinds of records: objectives, steps, step events, and condition checks. Events are append-only, meaning new facts are added rather than old facts being edited. This matters because a later plan revision should not erase what already happened. It is like keeping a lab notebook: you can add a new observation, but you do not rewrite yesterday’s page.

The view classes turn database rows into readable snapshots. A step’s state is derived from its events and latest check: pending, attempted, done, blocked, or unmet. The Objectives class is the main doorway for code that wants to create or revise a plan, find an objective, record that work was done or blocked, and save condition-check results. A key safety rule is that once a step has been attempted, its acceptance conditions are frozen, so nobody can loosen the test after the work proves difficult.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: Tells whether anyone has recorded a “did” event for this step. This separates a step that has merely been planned from one that someone actually tried to complete.

**Data flow**: It reads the step’s stored events → looks for at least one event whose kind is “did” → returns true if it finds one, otherwise false. It does not change anything.

**Call relations**: Other step-reading logic uses this as a basic signal. The step state calculation uses it to avoid calling an untried step done or unmet, and the objective’s runnable-step logic uses it to avoid dispatching work that has already been attempted.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: Finds the current unanswered block on a step, if there is one. A block means the worker asked for help or reported something stopping progress.

**Data flow**: It looks at the most recent event on the step → if that latest event is a “blocked” event, it returns that event → otherwise it returns nothing. It does not search older events because only the latest event can represent the currently standing block.

**Call relations**: The step state uses this same idea to report a step as blocked. The objective runnable logic uses it to avoid starting blocked work again, and Objectives.record uses it to avoid writing the exact same block repeatedly.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: Turns a step’s history and latest checks into a simple status word, such as pending, attempted, done, blocked, or unmet. This is the central rule that decides whether a step still belongs on the objective’s frontier.

**Data flow**: It reads the step’s events, acceptance conditions, and saved verdicts → first treats a latest block as blocked, then checks whether the step was attempted, then decides whether missing or failed condition checks prevent completion → returns one status string. It changes no stored data.

**Call relations**: ObjectiveView.confirmed and ObjectiveView.frontier depend on this property to count finished work and find unfinished work. The rest of the extension can ask for the state without reimplementing the rules for events, conditions, and verdicts.


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: Counts how many times work was recorded across all steps of an objective. This gives a simple measure of effort spent.

**Data flow**: It reads every step and every event inside those steps → counts only events marked “did” → returns that count as a number. Nothing is written or updated.

**Call relations**: This is a reporting view over the objective. It pairs with ObjectiveView.confirmed so callers can compare effort against proven progress.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: Counts how many steps are actually done according to the extension’s state rules. It measures confirmed progress, not just claimed progress.

**Data flow**: It reads each step → asks each step for its state → counts the steps whose state is “done” → returns that number. It does not change the objective.

**Call relations**: This builds directly on StepView.state. It is meant to be read alongside attempts: many attempts with few confirmed steps can show that an objective is stuck or too broad.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: Identifies the unfinished steps that may be started in parallel right now. A step qualifies only if the plan marked it independent, it has not already been attempted, and it is not currently blocked.

**Data flow**: It starts from the objective’s frontier, meaning unfinished steps → filters to independent steps with no prior attempt and no open block → returns those steps as a tuple. It does not write anything.

**Call relations**: This relies on ObjectiveView.frontier plus StepView.attempted and StepView.open_block. A dispatcher can use it when deciding which steps to fan out to workers at the same time.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: Returns the steps that are not done yet. This is the objective’s active edge: the remaining work still needing attention or proof.

**Data flow**: It reads all steps in order → asks each step for its state → keeps only steps whose state is not “done” → returns those steps. It does not modify the plan.

**Call relations**: ObjectiveView.runnable narrows this list further for parallel dispatch. Callers that wake up a conversation can use the frontier to know what remains open.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: Turns an acceptance condition into a short human-readable sentence. This is useful when showing or explaining what proof a step requires.

**Data flow**: It receives one condition object → checks whether it is a file-exists, file-contains, or command-succeeds condition → returns a plain text summary. It does not read or write the database.

**Call relations**: This is a small helper for presentation. It does not call into the store, but it gives other parts of the extension a consistent way to describe the checks defined here.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: Looks up one objective by conversation and name. The conversation filter is important because subagents can have their own conversations and should not accidentally take over a parent conversation’s objective with the same name.

**Data flow**: It receives a conversation id and objective name → queries the objective table for a matching row in the current workspace → if found, passes that row to Objectives._view to build a full readable ObjectiveView; if not found, returns nothing.

**Call relations**: Objectives.plan calls this first to decide whether it is creating a new objective or revising an existing one. When a row is found, this function hands off to Objectives._view so the caller gets steps, events, and checks, not just the objective header.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: Finds the most recently created objective for a conversation. This lets code reopen the current objective for a conversation without already knowing its name.

**Data flow**: It receives a conversation id → queries the objective table for objectives in the current workspace and conversation, newest first → turns the newest row into an ObjectiveView through Objectives._view, or returns nothing if none exists.

**Call relations**: This is another read doorway into the store. Like Objectives.named, it delegates the full assembly work to Objectives._view after the database query finds the objective row.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: Creates a new objective or revises the plan for an existing one. It preserves the acceptance conditions of any step that has already been attempted, so a later revision cannot make the finish line easier after the race has started.

**Data flow**: It receives a conversation id, name, directive, and planned steps → looks for an existing objective → inserts a new objective or updates the old directive → removes unstarted old steps that are no longer kept → updates or inserts the planned steps in order, freezing attempted steps’ conditions when needed → reads back and returns the completed ObjectiveView.

**Call relations**: This is the main write path for planning. It begins by calling Objectives.named, uses database insert, update, and delete operations to reshape the stored plan, and finally calls Objectives.named again to return the same kind of full view that readers use.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: Adds a new event to a step, such as work being done or the step being blocked. It avoids recording the same still-open block over and over, which would otherwise flood the history with repeated copies of the same question.

**Data flow**: It receives a step, event kind, actor turn id, and evidence text → trims the evidence to the maximum stored length → if this is the same block already standing, returns false without writing → otherwise inserts a new event row and returns true.

**Call relations**: Callers use this after a worker reports progress or blockage. It reads StepView.open_block before writing so it can tell the difference between a new event and a repeated unresolved block.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: Saves the extension’s own verdicts after evaluating a step’s acceptance conditions. These records are observations made by the extension, not claims made by the worker.

**Data flow**: It receives a step, a tuple of condition verdicts, and the actor turn id → converts each verdict into JSON-friendly data containing the condition, whether it held, and the detail text → inserts a new check row. It appends the result rather than replacing old checks.

**Call relations**: This is called after condition evaluation happens elsewhere. Objectives._view later reads the saved checks and uses the latest one for each step, allowing future turns to see the last known proof without reusing a worker’s temporary memory.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: Builds a complete ObjectiveView from database rows. It gathers the objective’s steps, their event history, and their latest condition-check results into one snapshot that the rest of the extension can read easily.

**Data flow**: It receives an objective database row → queries the step, event, and check tables → groups events by step and keeps the latest check per step → parses stored JSON conditions and verdicts into typed objects → returns an ObjectiveView containing StepView objects with their events, verdicts, and metadata.

**Call relations**: Objectives.named and Objectives.on_conversation call this after finding an objective row. It hands parsing work to _conditions and _verdicts, and it constructs the view objects that expose state, frontier, runnable steps, attempts, and confirmed progress.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: Converts raw stored condition data back into condition objects the code can trust. It rejects unknown condition kinds instead of silently guessing.

**Data flow**: It receives a payload, usually JSON read from the database → if the payload is not a list, returns an empty tuple → for each item, checks its kind and validates it as FileExists, FileContains, or CommandSucceeds → returns the parsed conditions as a tuple, or raises an error for an unknown kind.

**Call relations**: Objectives._view uses this when rebuilding StepView.accepts from stored rows. _verdicts also uses it to rebuild the condition embedded inside each saved verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: Converts raw saved check results into ConditionVerdict objects. These verdicts say which condition was checked, whether it held, and what detail was recorded.

**Data flow**: It receives a payload from the database → if it is not a list, returns an empty tuple → for each dictionary item, parses its condition through _conditions, converts the hold flag to true or false, converts the detail to text, and builds a ConditionVerdict → returns all parsed verdicts as a tuple.

**Call relations**: Objectives._view calls this while building each StepView. It depends on _conditions so verdicts and step acceptance conditions are interpreted with the same validation rules.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).


### Application builder gates
The application builder workflow guides app creation through design, repair, and deployment while relying on audit rules to block unsafe or unverified output.

### `extensions/sites/ufo_ext_sites/application_builder.py`

`orchestration` · `request handling`

This file is the control room for building a UFO application page. A member may ask for an app, but the system does not let a language model freely write and deploy arbitrary files. Instead, it gives the worker a fixed workspace, a required SVG design step, strict source-code rules, browser checks, and deployment gates.

The workflow works like a building permit process. First, the worker may create a wireframe: an SVG drawing that fixes the layout and names the important regions. That design is checked for size, visible regions, allowed components, and safety. Then the worker writes one source file, app.tsx, and the file is checked for allowed imports, required UFO kit components, styling rules, and successful compilation. If something fails, the worker can read small excerpts and apply exact edits rather than rewriting everything blindly.

Before deployment is accepted, product-owned QA proof must exist. The file then verifies that the deployed site belongs to the right member, contains retained source, matches the QA-tested source, and can be bound as the member’s homepage. Several hook functions also keep the workflow on rails: they force the right skill to load for app creation, stop deployment before QA, limit repeated repair reads, and prevent wireframe-only turns from doing build work.

#### Function details

##### `_local_source_bindings`  (lines 507–524)

```
def _local_source_bindings(code: str) -> set[str]
```

**Purpose**: Finds names that are locally defined inside a source file, such as functions, variables, destructured values, and function parameters. This helps the validator tell the difference between a real UFO kit component and a locally defined component with the same-looking name.

**Data flow**: It takes the app source text in → scans it with simple patterns for declarations and parameter names → returns a set of names that belong to the file itself.

**Call relations**: When _rendered_application_components is checking JSX tags, it calls this helper first so local names can be excluded from the list of kit components that appear on screen.

*Call graph*: called by 1 (_rendered_application_components); 1 external calls (findall).


##### `ApplicationBuilderTask.source_is_the_scaffolds_app_tsx`  (lines 683–695)

```
def source_is_the_scaffolds_app_tsx(self) -> 'ApplicationBuilderTask'
```

**Purpose**: Checks that a build task is only allowed to work on app.tsx directly inside the fixed scaffold folder. This prevents a worker from pointing the build tools at some other file.

**Data flow**: It reads the task’s scaffold_path and source_path → converts them into safe paths under /workspace → accepts the task only if the source path is exactly scaffold/app.tsx, otherwise it raises a validation error.

**Call relations**: This runs automatically when an ApplicationBuilderTask is validated, before build and wireframe worker turns use the task.

*Call graph*: 2 external calls (PurePosixPath, contained_relative).


##### `ApplicationWireframeResult.result_matches_status`  (lines 729–738)

```
def result_matches_status(self) -> 'ApplicationWireframeResult'
```

**Purpose**: Makes sure a wireframe result says only things that match its status. A ready wireframe must include the shared file and digest; a blocked one must include a reason.

**Data flow**: It reads the result fields after construction → compares them with the status value → either returns the valid result or raises an error describing the mismatch.

**Call relations**: This protects the output returned by design_ufo_application so callers do not receive a half-ready or contradictory wireframe response.


##### `ApplicationBuilderResult.result_matches_status`  (lines 756–775)

```
def result_matches_status(self) -> 'ApplicationBuilderResult'
```

**Purpose**: Makes sure a builder result is internally honest. A wireframe, deployed app, and blocked app each require different evidence, and this validator enforces those combinations.

**Data flow**: It reads the result’s status and evidence fields → checks that required fields are present and forbidden fields are absent → returns the valid model or raises an error.

**Call relations**: This validation is used whenever worker output is parsed, especially by build_ufo_application and ApplicationBuildAcceptance, so bad worker claims are rejected early.


##### `ApplicationBuildAcceptance.accept`  (lines 785–871)

```
async def accept(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult
```

**Purpose**: Performs the final acceptance check after the worker says an app was deployed. It proves the deployed site is the right one, belongs to the right member, matches QA-tested source, and can be bound as the homepage.

**Data flow**: It takes a worker result in → rejects obvious wrong statuses or paths → reads stored QA proof and hosted-site records → compares source hashes from deployment, QA, and accepted source → updates the result with the final bound site URL or returns a blocked result.

**Call relations**: build_ufo_application calls this after a child worker finishes. It delegates smaller checks to _initial_result, _proof, _blocked, _source_acceptance_path, and _runtime_root, then talks to HostedSites to verify and bind the site.

*Call graph*: calls 5 internal fn (_blocked, _initial_result, _proof, _runtime_root, _source_acceptance_path); 5 external calls (__init__, __init__, model_copy, model_validate_json, site_url).


##### `ApplicationBuildAcceptance._initial_result`  (lines 873–880)

```
def _initial_result(self, result: ApplicationBuilderResult) -> ApplicationBuilderResult | None
```

**Purpose**: Quickly rejects worker results that are the wrong kind before doing heavier checks. It catches cases like returning a wireframe when a full build was requested.

**Data flow**: It receives the worker result → checks status and source path → returns either a blocked replacement result or None to mean the result can continue to deeper review.

**Call relations**: ApplicationBuildAcceptance.accept calls this first. If it finds a problem, accept stops there instead of checking store records and deployment details.

*Call graph*: calls 1 internal fn (_blocked); called by 1 (accept).


##### `ApplicationBuildAcceptance._proof`  (lines 882–904)

```
async def _proof(self, result: ApplicationBuilderResult, extension: ExtensionContext) -> ApplicationQaProof | ApplicationBuilderResult
```

**Purpose**: Loads and checks the product QA proof for a builder turn. This proof is the system’s record that browser QA passed for a particular app source.

**Data flow**: It reads the QA proof key from the extension store → validates the stored data as an ApplicationQaProof → blocks if proof is missing or if the worker reported browser errors → otherwise returns the proof.

**Call relations**: ApplicationBuildAcceptance.accept calls this before trusting any deployment. It uses _blocked when proof is absent or contradicted by reported errors.

*Call graph*: calls 1 internal fn (_blocked); called by 1 (accept); 1 external calls (model_validate).


##### `ApplicationBuildAcceptance._blocked`  (lines 906–919)

```
def _blocked(self, result: ApplicationBuilderResult, reason: str, browser_batches: int) -> ApplicationBuilderResult
```

**Purpose**: Creates a standard blocked build result with a clear reason. This gives callers one consistent shape for failed acceptance.

**Data flow**: It receives the original result, a reason, and browser batch count → copies over useful evidence like checked controls and observed errors → returns a new ApplicationBuilderResult with status blocked.

**Call relations**: _initial_result, _proof, and accept all use this helper whenever a worker result fails one of the acceptance gates.

*Call graph*: called by 3 (_initial_result, _proof, accept); 1 external calls (__init__).


##### `EditApplicationSourceInput.json_text_edits_are_objects`  (lines 973–995)

```
def json_text_edits_are_objects(cls, value: object) -> object
```

**Purpose**: Accepts a few convenient edit formats and turns them into the structured edit objects the tool expects. This makes repair calls more forgiving without changing what edits mean.

**Data flow**: It receives the raw edits field → if items are JSON strings, patch-style search/replace blocks, or pairs of strings, it converts them into old_text/new_text objects → returns the normalized edit list for normal validation.

**Call relations**: This runs during EditApplicationSourceInput validation before edit_application_source applies exact replacements.

*Call graph*: 1 external calls (loads).


##### `_validate_application_imports`  (lines 998–1009)

```
def _validate_application_imports(source: str) -> None
```

**Purpose**: Checks that app.tsx imports only from the approved ufo/kit package and does so using named imports. This keeps generated apps inside the supported runtime and component library.

**Data flow**: It takes source text in → scans import and export statements → raises clear repair errors for missing kit imports, outside imports, side-effect imports, non-named imports, exports, or old runtime names.

**Call relations**: _validate_application_source calls this as the first source-code gate before checking rendered components and styling rules.

*Call graph*: called by 1 (_validate_application_source).


##### `_rendered_application_components`  (lines 1012–1039)

```
def _rendered_application_components(source: str) -> set[str]
```

**Purpose**: Finds which approved UFO kit components are actually rendered in the app. It prevents a page from importing the kit but drawing everything with unsupported custom markup.

**Data flow**: It takes source text in → confirms mountApp is used on the root element → reads named imports from ufo/kit → removes strings, comments, and locally declared names → returns the set of imported kit components that appear as JSX elements.

**Call relations**: _validate_application_source calls this after import validation. It uses _local_source_bindings to avoid counting local components as kit components.

*Call graph*: calls 1 internal fn (_local_source_bindings); called by 1 (_validate_application_source).


##### `_validate_designed_components`  (lines 1042–1053)

```
def _validate_designed_components(rendered_kit_components: set[str], designed_kit_components: tuple[str, ...]) -> None
```

**Purpose**: Checks that components promised by the SVG design also appear directly in the app source. This links the visual contract to the built page.

**Data flow**: It receives the set of rendered kit components and the list of designed kit components → finds any designed components missing from the rendered source → raises a repair error if any are absent.

**Call relations**: _validate_application_source calls this after discovering rendered components, especially when a prior accepted design named required components.

*Call graph*: called by 1 (_validate_application_source).


##### `_validate_application_styling`  (lines 1056–1086)

```
def _validate_application_styling(source: str) -> None
```

**Purpose**: Enforces house styling rules for generated app pages. These rules keep pages on the shared theme instead of using raw CSS values, reserved attributes, unsupported spacing, or ad-hoc style tags.

**Data flow**: It takes source text in → searches for disallowed styling patterns → raises a specific error explaining the fix when it finds one → otherwise returns without output.

**Call relations**: _validate_application_source calls this after import and component checks, before source is allowed to compile and be written.

*Call graph*: called by 1 (_validate_application_source).


##### `_validate_application_source`  (lines 1089–1095)

```
def _validate_application_source(source: str, designed_kit_components: tuple[str, ...]=()) -> None
```

**Purpose**: Runs all source-code checks for app.tsx in one place. It is the main quality gate for generated application code.

**Data flow**: It receives source text and optional designed component names → checks imports → identifies rendered kit components → checks that designed components are present → checks styling rules → either completes silently or raises a repairable error.

**Call relations**: write_application_source and edit_application_source call this before compiling and publishing app.tsx.

*Call graph*: calls 4 internal fn (_rendered_application_components, _validate_application_imports, _validate_application_styling, _validate_designed_components); called by 2 (edit_application_source, write_application_source).


##### `_parse_application_design`  (lines 1098–1126)

```
def _parse_application_design(source: str) -> tuple[ElementTree.Element, tuple[float, ...]]
```

**Purpose**: Parses the SVG design and checks its basic page shape. The design must be a safe, 305-pixel-wide mobile-style page with a valid height.

**Data flow**: It receives SVG text → rejects XML entity declarations → parses the SVG root → reads and validates viewBox, width, and height → returns the root element and numeric viewBox values.

**Call relations**: _validate_application_design calls this before walking individual SVG elements.

*Call graph*: called by 1 (_validate_application_design); 3 external calls (isfinite, split, fromstring).


##### `_visible_design_element`  (lines 1129–1155)

```
def _visible_design_element(element: ElementTree.Element, tag: str, attributes: dict[str, str]) -> bool
```

**Purpose**: Decides whether a drawing element would actually show something. Empty rectangles, zero-length lines, and blank text should not count as real design content.

**Data flow**: It receives an SVG element, its tag name, and attributes → applies tag-specific visibility checks → returns true if the element is visibly meaningful.

**Call relations**: _validate_design_element calls this while counting visible drawing elements in the full design.

*Call graph*: called by 1 (_validate_design_element); 1 external calls (itertext).


##### `_validate_design_attributes`  (lines 1158–1165)

```
def _validate_design_attributes(element: ElementTree.Element) -> None
```

**Purpose**: Rejects SVG attributes that could run code or load outside content. This keeps a design drawing from becoming an active web document.

**Data flow**: It receives an SVG element → inspects every attribute name and value → raises an error if it sees event handlers or javascript, data, http, or https links.

**Call relations**: _validate_design_element calls this for every SVG element it walks.

*Call graph*: called by 1 (_validate_design_element).


##### `_validate_design_element`  (lines 1168–1221)

```
def _validate_design_element(element: ElementTree.Element, ids: set[str], regions: list[ElementTree.Element], kit_components: list[str]) -> bool
```

**Purpose**: Checks one SVG element against the application design rules. It catches unsafe effects, duplicate IDs, scripts, invalid region markers, and invalid kit component markers.

**Data flow**: It receives an element plus shared collections for IDs, regions, and kit components → validates the element and records region/component markers → returns whether this element counts as a visible drawing element.

**Call relations**: _validate_application_design calls this for every element in the SVG tree. It uses _validate_design_attributes and _visible_design_element for focused checks.

*Call graph*: calls 2 internal fn (_validate_design_attributes, _visible_design_element); called by 1 (_validate_application_design); 1 external calls (itertext).


##### `_validate_application_design`  (lines 1224–1251)

```
def _validate_application_design(source: str) -> tuple[tuple[str, ...], tuple[str, ...], int]
```

**Purpose**: Runs the full static validation for an application SVG design. It confirms the drawing is safe, visible, regioned, and tied to at least one approved UFO kit component.

**Data flow**: It receives SVG text → parses page bounds → walks all elements → gathers region names and kit components → checks region count, uniqueness, and nesting → returns region names, kit component names, and page height.

**Call relations**: write_application_design, design_ufo_application, build_ufo_application, and _require_application_design call this before trusting or reusing any design.

*Call graph*: calls 2 internal fn (_parse_application_design, _validate_design_element); called by 4 (_require_application_design, build_ufo_application, design_ufo_application, write_application_design).


##### `_build_application_project`  (lines 1254–1268)

```
async def _build_application_project(ctx: ToolContext, project: str, runtime_root: str | None=None) -> None
```

**Purpose**: Builds the application project with the standard page kit installed. This proves the project can be compiled by the same toolchain used for real pages.

**Data flow**: It receives a tool context, project path, and optional runtime root → writes project config if needed → unpacks the page kit → runs the Vite build command → raises a concise compile error if the build fails.

**Call relations**: _compile_application_source uses this for temporary compile checks, while write_application_source and edit_application_source use it again on the real scaffold.

*Call graph*: called by 3 (_compile_application_source, edit_application_source, write_application_source); 1 external calls (unpack_page_kit).


##### `_compile_application_source`  (lines 1271–1284)

```
async def _compile_application_source(ctx: ToolContext, task: ApplicationBuilderTask, source: str) -> None
```

**Purpose**: Compiles a proposed app.tsx in a temporary project before touching the real scaffold. This gives a safe test run for generated source.

**Data flow**: It receives the task and source text → copies the existing index.html and proposed app.tsx into a runtime check folder → asks _build_application_project to compile that folder → returns only if compilation succeeds.

**Call relations**: write_application_source and edit_application_source call this after validation and before writing the source into the live scaffold.

*Call graph*: calls 2 internal fn (_build_application_project, _runtime_root); called by 2 (edit_application_source, write_application_source).


##### `_source_claim_path`  (lines 1287–1291)

```
async def _source_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Computes the runtime file path used to claim ownership of app.tsx for one builder turn. The claim prevents repeated full writes from racing or overwriting each other.

**Data flow**: It receives the context, task, and turn ID → hashes the source path and combines it with the turn ID → returns a runtime path for the claim file.

**Call relations**: write_application_source creates this claim, and _require_application_source checks it before repair reads or edits are allowed.

*Call graph*: called by 2 (_require_application_source, write_application_source); 1 external calls (sha256).


##### `_runtime_root`  (lines 1294–1295)

```
async def _runtime_root(ctx: ToolContext) -> str
```

**Purpose**: Finds the root directory that sandbox helper scripts should treat as their safe runtime area. This keeps helper-file operations inside the intended sandbox.

**Data flow**: It receives the tool context → asks the sandbox for the runtime path of tool-output → returns that path’s parent as a string.

**Call relations**: Many functions pass this path into contained helper scripts, including build_ufo_application, write_application_design, source reading and editing, and final acceptance.

*Call graph*: called by 9 (accept, _compile_application_source, _require_application_design, _require_application_source, build_ufo_application, edit_application_source, read_application_source, write_application_design, write_application_source); 1 external calls (PurePosixPath).


##### `_design_path`  (lines 1298–1299)

```
def _design_path(task: ApplicationBuilderTask) -> str
```

**Purpose**: Builds the fixed path where the application design SVG must live inside the scaffold. It centralizes the naming rule so all design tools use the same file.

**Data flow**: It receives a build task → appends application-design.svg to the task’s scaffold path → returns that full workspace path.

**Call relations**: Design claiming, design validation, wireframe acceptance, and source requirements all call this when they need the canonical design file path.

*Call graph*: called by 4 (_design_claim_path, _require_application_design, accept_application_wireframe, write_application_design).


##### `_design_claim_path`  (lines 1302–1306)

```
async def _design_claim_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Computes the runtime path used to claim that the design has been fixed for one builder turn. This stops the worker from changing the accepted design after source work begins.

**Data flow**: It receives the context, task, and turn ID → gets the canonical design path → hashes it and combines it with the turn ID → returns a runtime claim path.

**Call relations**: write_application_design writes this claim, and _require_application_design checks it before app.tsx can be written or edited.

*Call graph*: calls 1 internal fn (_design_path); called by 2 (_require_application_design, write_application_design); 1 external calls (sha256).


##### `application_design_acceptance_relative`  (lines 1309–1315)

```
def application_design_acceptance_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: Returns the relative runtime path where the accepted design SVG is stored. This is product-owned evidence, separate from the editable workspace file.

**Data flow**: It receives a design path and turn ID → hashes the design path and adds an accepted SVG suffix → returns the relative runtime filename.

**Call relations**: write_application_design uses this when sealing the validated SVG as accepted evidence.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `application_design_evidence_relative`  (lines 1318–1324)

```
def application_design_evidence_relative(design_path: str, turn_id: UUID) -> str
```

**Purpose**: Returns the relative runtime path where metadata about the accepted design is stored. That metadata records what design was accepted and what regions/components were found.

**Data flow**: It receives a design path and turn ID → hashes the design path and adds an accepted-design JSON suffix → returns the relative runtime filename.

**Call relations**: write_application_design uses this beside application_design_acceptance_relative when publishing design evidence.

*Call graph*: called by 1 (write_application_design); 1 external calls (sha256).


##### `_source_candidate_path`  (lines 1327–1333)

```
async def _source_candidate_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Computes the runtime path for the current candidate app.tsx. Repairs operate on this candidate before the source is accepted and written to the scaffold.

**Data flow**: It receives the context, task, and turn ID → hashes the source path and adds a candidate TypeScript suffix → returns a runtime path.

**Call relations**: write_application_source creates the candidate, read_application_source reads it, and edit_application_source updates it.

*Call graph*: called by 3 (edit_application_source, read_application_source, write_application_source); 1 external calls (sha256).


##### `_render_application_design`  (lines 1336–1387)

```
async def _render_application_design(ctx: ToolContext, candidate_path: str, preview_path: str, names: tuple[str, ...], page_height: int) -> tuple[ApplicationAuditRegion, ...]
```

**Purpose**: Runs a browser-based audit of the SVG design and returns the visible named regions it found. This catches layout problems that plain XML parsing cannot see, such as overlaps or drawing outside the page.

**Data flow**: It receives paths for the candidate SVG and preview image, expected region names, and page height → writes the audit script → runs it with Node → validates the JSON region output → checks that regions match and do not overlap → returns the audited regions.

**Call relations**: write_application_design calls this after static SVG validation and before sealing the design as accepted.

*Call graph*: called by 1 (write_application_design); 2 external calls (search, application_region_relation).


##### `_source_acceptance_path`  (lines 1390–1396)

```
async def _source_acceptance_path(ctx: ToolContext, task: ApplicationBuilderTask, turn_id: UUID) -> str
```

**Purpose**: Computes the runtime path where the accepted source hash is recorded. This hash later proves that deployed source matches the source that passed checks.

**Data flow**: It receives context, task, and turn ID → hashes the source path and combines it with the turn ID → returns a runtime path ending in accepted.

**Call relations**: write_application_source and edit_application_source write this hash after a successful build. ApplicationBuildAcceptance.accept reads it during final deployment acceptance.

*Call graph*: called by 3 (accept, edit_application_source, write_application_source); 1 external calls (sha256).


##### `_require_application_source`  (lines 1399–1408)

```
async def _require_application_source(ctx: ToolContext, task: ApplicationBuilderTask) -> None
```

**Purpose**: Checks that an initial app.tsx candidate has already been claimed before repair tools run. This prevents reading or editing source that was never admitted into the workflow.

**Data flow**: It receives context and task → checks the source claim file through a sandbox helper → raises a user-facing error if no claim exists, or a runtime error if the claim cannot be read.

**Call relations**: read_application_source and edit_application_source call this before touching the candidate source.

*Call graph*: calls 2 internal fn (_runtime_root, _source_claim_path); called by 2 (edit_application_source, read_application_source).


##### `_require_application_design`  (lines 1411–1432)

```
async def _require_application_design(ctx: ToolContext, task: ApplicationBuilderTask) -> tuple[str, ...]
```

**Purpose**: Checks that a valid design has been written before app.tsx is written or repaired. It also returns the UFO kit components the source must render.

**Data flow**: It receives context and task → checks the design claim → reads application-design.svg → verifies any accepted wireframe digest → validates the SVG → returns the kit component names recorded in the design.

**Call relations**: write_application_source and edit_application_source call this so code cannot proceed without a fixed visual contract.

*Call graph*: calls 4 internal fn (_design_claim_path, _design_path, _runtime_root, _validate_application_design); called by 2 (edit_application_source, write_application_source); 1 external calls (sha256).


##### `write_application_design`  (lines 1435–1589)

```
async def write_application_design(ctx: ToolContext, args: WriteApplicationDesignInput) -> ToolResult
```

**Purpose**: Writes and seals the SVG visual contract for the app. This is the step that fixes the layout before source-code work begins.

**Data flow**: It receives the tool context and SVG content → validates the design → writes a candidate SVG → runs the browser design audit → creates evidence JSON → atomically publishes accepted design and evidence → writes the design into the scaffold → returns path, digest, size, height, and region details.

**Call relations**: This is exposed as the profile-only write_application_design tool. accept_application_wireframe also calls it to seal a previously staged member-approved wireframe.

*Call graph*: calls 8 internal fn (_complete_application_design_cleanup, _design_claim_path, _design_path, _render_application_design, _runtime_root, _validate_application_design, application_design_acceptance_relative, application_design_evidence_relative); called by 1 (accept_application_wireframe); 7 external calls (__init__, __init__, __init__, sha256, dumps, application_design_region_fold_failure, application_design_region_size_failure).


##### `accept_application_wireframe`  (lines 1592–1603)

```
async def accept_application_wireframe(ctx: ToolContext, _args: AcceptApplicationWireframeInput) -> ToolResult
```

**Purpose**: Accepts an already staged wireframe as the fixed design for a build turn. It does not redesign anything; it validates and seals the existing SVG.

**Data flow**: It reads the task from the turn → confirms the task names an accepted wireframe digest → reads application-design.svg from the scaffold → passes that content into write_application_design → returns the same kind of design result.

**Call relations**: This is a profile-only tool used when a member has approved a wireframe and the build should start from that exact design.

*Call graph*: calls 2 internal fn (_design_path, write_application_design); 1 external calls (__init__).


##### `_complete_application_design_cleanup`  (lines 1606–1622)

```
async def _complete_application_design_cleanup(ctx: ToolContext, program: str, *args: str) -> tuple[ExecResult | None, tuple[str, ...]]
```

**Purpose**: Tries hard to clean up design claims or accepted files if write_application_design fails partway through. It shields cleanup from cancellation so half-published evidence is less likely to remain.

**Data flow**: It receives a sandbox cleanup program and its arguments → starts it as an async task → waits even through interruptions while recording cleanup problems → returns the cleanup result and any failure notes.

**Call relations**: write_application_design calls this in its error path, either to release a claim or remove accepted design evidence.

*Call graph*: called by 1 (write_application_design); 2 external calls (create_task, shield).


##### `read_application_source`  (lines 1625–1679)

```
async def read_application_source(ctx: ToolContext, args: ReadApplicationSourceInput) -> ToolResult
```

**Purpose**: Returns small, targeted excerpts of the candidate app.tsx for repairs. It avoids dumping the whole file while still giving enough context around requested search terms.

**Data flow**: It receives search terms → verifies source was claimed → reads the candidate source → counts matching lines for each term → builds line-numbered excerpts from the start, end, and match areas within a size limit → returns the excerpt text.

**Call relations**: The builder uses this after an exact edit fails. limit_application_builder_repair_reads can restrict repeated calls to this tool during repair loops.

*Call graph*: calls 3 internal fn (_require_application_source, _runtime_root, _source_candidate_path); 2 external calls (__init__, __init__).


##### `edit_application_source`  (lines 1682–1731)

```
async def edit_application_source(ctx: ToolContext, args: EditApplicationSourceInput) -> ToolResult
```

**Purpose**: Applies exact text replacements to the current candidate app.tsx, then revalidates and rebuilds it. This is the safe repair path after the initial full write.

**Data flow**: It receives one or more old_text/new_text edits → verifies source and design claims → reads candidate source → ensures each old_text appears exactly once and edits do not overlap → applies replacements → validates, compiles, writes the scaffold, builds the project, records accepted source hash → returns path, replacement count, and size.

**Call relations**: This profile-only tool follows write_application_source when repairs are needed. It calls the same validation and build helpers, then writes acceptance evidence for ApplicationBuildAcceptance.

*Call graph*: calls 8 internal fn (_build_application_project, _compile_application_source, _require_application_design, _require_application_source, _runtime_root, _source_acceptance_path, _source_candidate_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `write_application_source`  (lines 1734–1778)

```
async def write_application_source(ctx: ToolContext, args: WriteApplicationSourceInput) -> ToolResult
```

**Purpose**: Writes the first complete app.tsx candidate for a build turn. After this, repairs must use exact edits rather than another full overwrite.

**Data flow**: It receives complete source text → verifies a design exists → claims source ownership → stores the candidate source → validates imports, components, and styling → compiles it in a temporary project → writes it to the scaffold → builds the real project → records the accepted source hash → returns path and size.

**Call relations**: This is the main source-writing tool available to the application builder profile. edit_application_source is the follow-up path if validation or QA requires changes.

*Call graph*: calls 8 internal fn (_build_application_project, _compile_application_source, _require_application_design, _runtime_root, _source_acceptance_path, _source_candidate_path, _source_claim_path, _validate_application_source); 4 external calls (__init__, __init__, sha256, dumps).


##### `design_ufo_application`  (lines 1781–1839)

```
async def design_ufo_application(ctx: ToolContext, args: DesignUfoApplicationInput) -> ToolResult
```

**Purpose**: Runs the builder in wireframe mode and shares the accepted SVG with the member. It stores the exact design so a later build can use the same member-approved wireframe.

**Data flow**: It receives an application name, prompt, and optional revision → ensures scaffold files exist → spawns the application builder profile in wireframe phase → validates the returned SVG and digest → stores a preview and shares the SVG artifact → saves the wireframe in extension storage → returns ready or blocked wireframe status.

**Call relations**: This is the public design_ufo_application delegation tool. It calls _ensure_application_scaffold and _validate_application_design, then stores the wireframe for build_ufo_application to consume later.

*Call graph*: calls 4 internal fn (share_artifact, store_preview, _ensure_application_scaffold, _validate_application_design); 8 external calls (__init__, __init__, __init__, __init__, __init__, spawn, sha256, PurePosixPath).


##### `_ensure_application_scaffold`  (lines 1842–1850)

```
async def _ensure_application_scaffold(ctx: ToolContext) -> None
```

**Purpose**: Creates the basic app scaffold files if they are missing. These files give the generated app a stable index page, placeholder source, and preview shell.

**Data flow**: It checks each required scaffold path → if a file cannot be read, writes the default content → returns once the scaffold is present.

**Call relations**: design_ufo_application and build_ufo_application call this before spawning the worker so the child turn starts with a known project shape.

*Call graph*: called by 2 (build_ufo_application, design_ufo_application).


##### `build_ufo_application`  (lines 1853–1940)

```
async def build_ufo_application(ctx: ToolContext, _args: BuildUfoApplicationInput) -> ToolResult
```

**Purpose**: Delegates one full application build to the fixed builder worker and returns the accepted result. It is the main tool a member-facing agent uses to turn a request into a deployed UFO app.

**Data flow**: It verifies extension context and idempotency → claims that this parent turn only delegates once → records redeploy and audit contract data → prepares scaffold and any stored accepted wireframe → spawns the builder profile → validates or blocks the worker output through ApplicationBuildAcceptance → deletes a spent wireframe after successful deployment → returns structured JSON.

**Call relations**: This is the public build_ufo_application tool. It coordinates scaffold setup, stored wireframes, child worker spawning, and final deployment acceptance.

*Call graph*: calls 3 internal fn (_ensure_application_scaffold, _runtime_root, _validate_application_design); 9 external calls (__init__, __init__, __init__, __init__, __init__, spawn, sha256, format, format).


##### `limit_application_builder_repair_reads`  (lines 1943–1978)

```
async def limit_application_builder_repair_reads(ctx: HookContext) -> Deny | None
```

**Purpose**: Limits how many times the builder can read source excerpts after product QA has requested repairs. This nudges the worker to edit, test, and deploy instead of endlessly inspecting code.

**Data flow**: It receives a hook context before a tool call → ignores unrelated turns and tools → checks whether a QA repair attempt exists → resets the read count on edits → increments the count on reads → returns a Deny if the limit is reached.

**Call relations**: This hook runs around application builder tool use. It affects read_application_source and edit_application_source during repair loops.

*Call graph*: 2 external calls (__init__, format).


##### `enforce_application_builder_phase`  (lines 1981–1996)

```
async def enforce_application_builder_phase(ctx: HookContext) -> Deny | None
```

**Purpose**: Keeps wireframe-only builder turns from doing source, QA, connector, or deployment work. It also stops redesign when a member-approved wireframe is already fixed.

**Data flow**: It receives a pre-tool-use hook → checks whether the turn belongs to the application builder profile → reads the task phase → returns a Deny for tools that are not allowed in that phase.

**Call relations**: This hook protects the profile defined at the bottom of the file, especially when design_ufo_application spawns a wireframe-phase worker.

*Call graph*: 1 external calls (__init__).


##### `is_application_creation_request`  (lines 1999–2003)

```
def is_application_creation_request(text: str) -> bool
```

**Purpose**: Detects whether a member’s message appears to ask for creating an application rather than a general website or other app type. This helps route requests to the right skill.

**Data flow**: It receives plain text → tests it against creation wording and excludes website/mobile/desktop/full-stack wording → returns true or false.

**Call relations**: enforce_application_creation_route calls this before deciding whether the main agent must load the create-application skill.

*Call graph*: called by 1 (enforce_application_creation_route).


##### `enforce_application_creation_route`  (lines 2006–2029)

```
async def enforce_application_creation_route(ctx: HookContext) -> Deny | None
```

**Purpose**: Forces the main agent to load the create-application skill before using other tools when a member asks to create a UFO application. This prevents the wrong workflow from starting.

**Data flow**: It receives a hook context → filters to member messages on the main agent → checks the message with is_application_creation_request → allows the first correct load_skill call and records it → denies other tool use until that happens.

**Call relations**: This hook runs before tool use in the main conversation, outside the builder subagent. It uses the extension store to remember that routing was completed for the turn.

*Call graph*: calls 1 internal fn (is_application_creation_request); 1 external calls (__init__).


##### `require_application_builder_qa`  (lines 2032–2045)

```
async def require_application_builder_qa(ctx: HookContext) -> Deny | None
```

**Purpose**: Blocks application deployment until product QA proof exists and is valid. It is the final guardrail before the builder can use the deployment tool.

**Data flow**: It receives a hook context → checks the current builder turn’s QA proof key in the store → validates the proof if present → returns a Deny when proof is missing, otherwise allows deployment to continue.

**Call relations**: This hook is tied to the application builder profile’s deployment step. ApplicationBuildAcceptance later relies on the same proof when accepting the worker’s deployed result.

*Call graph*: 2 external calls (__init__, model_validate).


### `extensions/sites/ufo_ext_sites/application_audit.py`

`domain_logic` · `quality gate after browser measurement`

This file is the quality gate for an application builder. A browser has already visited the staged application and collected facts such as text contrast, page width, visible regions, console errors, and whether controls actually change the page. This file gives that raw evidence a strict shape, then applies deterministic checks to it. “Deterministic” means the same report always produces the same verdict, like a checklist rather than a human opinion.

Most of the file is made of Pydantic models, which are data containers that also validate their contents. They describe things like one measured browser view, one text contrast problem, one clickable control, or the final audit verdict. The constants near the top are the audit’s fixed standards: desktop and narrow widths, light and dark color schemes, minimum contrast ratios, minimum controls, maximum issue count, and so on.

The main work happens in audit_application. It checks that all required views exist, that the page has readable text, no horizontal overflow, no clipping, no accidental overlaps, no browser errors, enough accessible controls, enough successful interactions, and any required facts. It also compares the live application’s visible regions with the accepted design, rather like checking that a built room still has the same named areas in the same order as the blueprint. The result is an ApplicationAuditVerdict: either no issues, or a short bounded list of repair instructions.

#### Function details

##### `AcceptedApplicationDesignEvidence.regions_are_unique`  (lines 131–137)

```
def regions_are_unique(self) -> 'AcceptedApplicationDesignEvidence'
```

**Purpose**: This validation step makes sure an accepted design does not use the same region name or Kit component name more than once. That matters because later checks compare regions by name, so duplicate names would make the blueprint ambiguous.

**Data flow**: It reads the design evidence object after its fields have been filled. It collects region names and component names, compares each list with its set of unique values, and either returns the unchanged object or raises an error explaining the duplicate problem.

**Call relations**: This runs automatically when AcceptedApplicationDesignEvidence is created. It protects later design-comparison code from receiving a design where two different areas claim the same identity.


##### `ApplicationAuditReport.views_are_unique`  (lines 202–206)

```
def views_are_unique(self) -> 'ApplicationAuditReport'
```

**Purpose**: This validation step makes sure the browser report contains at most one view for each combination of color scheme and screen width. Without this, the audit could see two conflicting reports for the same view and make an unclear decision.

**Data flow**: It reads the report’s views, turns each view into a simple key made from its scheme and width, and checks for duplicates. If all keys are unique, the report is returned unchanged; if not, report creation fails with a clear error.

**Call relations**: This runs automatically when an ApplicationAuditReport is created. The main audit later builds a lookup table from scheme and width, so this validator ensures that lookup has only one answer for each required view.


##### `ApplicationAuditVerdict.passed`  (lines 227–230)

```
def passed(self) -> bool
```

**Purpose**: This property answers the simple question: did the audit pass? It returns true only when there are no repair issues.

**Data flow**: It reads the verdict’s issue list. If the list is empty, it returns true; if there is even one issue, it returns false. It does not change anything.

**Call relations**: Code that receives an ApplicationAuditVerdict can use this property as the final yes-or-no result after audit_application has built the issue list.


##### `_needed_ratio`  (lines 277–282)

```
def _needed_ratio(item: ApplicationAuditText) -> float
```

**Purpose**: This helper decides the minimum contrast ratio required for one piece of text. Contrast ratio is a number that describes how readable foreground text is against its background.

**Data flow**: It receives one measured text item. If the text is in a special quiet Kit slot, it uses the lower Kit-specific floor; if it is large text, it uses the large-text accessibility floor; otherwise it uses the normal body-text floor. It returns the required number.

**Call relations**: _contrast_failures calls this for each measured text style so it can compare what the browser saw against the correct readability standard.

*Call graph*: called by 1 (_contrast_failures).


##### `_issue`  (lines 285–292)

```
def _issue(code: AuditIssueCode, message: str, terms: tuple[str, ...]=()) -> ApplicationAuditIssue
```

**Purpose**: This helper creates one audit issue while enforcing the file’s safety limits on message length and number of search terms. It keeps repair feedback short enough to send back to the builder safely.

**Data flow**: It receives an issue code, a human-readable message, and optional terms connected to the problem. It cuts the message down to the maximum allowed length, keeps only the first ten terms, and returns a new ApplicationAuditIssue.

**Call relations**: audit_application calls this whenever it finds a problem. This keeps all issue creation consistent instead of repeating trimming rules throughout the main audit.

*Call graph*: called by 1 (audit_application); 1 external calls (__init__).


##### `application_first_screen_scale`  (lines 295–305)

```
def application_first_screen_scale(page_height: int) -> float
```

**Purpose**: This helper converts first-screen pixel rules into fractions of the whole measured page. It is needed because region positions are stored as fractions, while the design rule is based on the first 844 pixels of the page.

**Data flow**: It receives the measured page height. It divides the fixed first-screen height by that page height and returns the scale factor. Taller pages produce a smaller fraction.

**Call relations**: application_region_relation and application_design_region_size_failure use this when they need vertical tolerances or minimum heights to mean the same real pixel size on pages of different heights.

*Call graph*: called by 2 (application_design_region_size_failure, application_region_relation).


##### `application_region_relation`  (lines 308–328)

```
def application_region_relation(first: ApplicationAuditRegion, second: ApplicationAuditRegion, page_height: int=APPLICATION_DESIGN_FOLD) -> tuple[Literal['horizontal', 'vertical'], int] | None
```

**Purpose**: This function decides whether two visible regions are separated vertically or horizontally, and which one comes first. If they overlap or touch too much, it says there is no clean relationship.

**Data flow**: It receives two regions and the page height their coordinates were measured against. It scales the allowed vertical near-touch tolerance, compares top, bottom, left, and right edges, and returns a pair such as horizontal-before or vertical-after. If the regions are not clearly separated, it returns nothing.

**Call relations**: application_design_fidelity calls this first on accepted design regions to learn the intended layout, then again on measured application regions to see whether the live app kept the same order.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_size_failure`  (lines 331–349)

```
def application_design_region_size_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function finds the first design region that is too small to count as a meaningful visible area. It prevents tiny slivers or accidental marks from being treated as real application regions.

**Data flow**: It receives a tuple of regions and the page height. It scales vertical and area thresholds to the first-screen size, then checks each region’s width, height, and area. It returns a short failure message for the first too-small region, or nothing if all regions are large enough.

**Call relations**: application_design_fidelity calls this before doing deeper region matching. If the accepted design itself has an unusably small region, fidelity checking stops early with that failure.

*Call graph*: calls 1 internal fn (application_first_screen_scale); called by 1 (application_design_fidelity).


##### `application_design_region_fold_failure`  (lines 352–374)

```
def application_design_region_fold_failure(regions: tuple[ApplicationAuditRegion, ...], page_height: int=APPLICATION_DESIGN_FOLD) -> str | None
```

**Purpose**: This function checks whether a design region crosses the first-screen boundary in a way the design rules reject. The “fold” is the bottom of the initially visible screen before scrolling.

**Data flow**: It receives regions and the page height. For each region, it converts fractional top and bottom positions back into pixel rows, allows a small tolerance around the fold, and returns a message if a region clearly paints on both sides of that boundary. If none do, it returns nothing.

**Call relations**: This function is available as a design-rule check, although it is not called by the listed functions in this file’s call graph. It complements the other region-quality helpers by focusing specifically on the first-screen boundary.


##### `application_design_fidelity`  (lines 377–460)

```
def application_design_fidelity(report: ApplicationAuditReport) -> ApplicationDesignFidelity
```

**Purpose**: This function scores how well the live desktop application matches the accepted design’s named visible regions. It checks identity, visibility above the fold, non-overlap, and relative ordering.

**Data flow**: It receives the full audit report. It first validates that the accepted design has the right number of unique regions, then checks region size and overlap. For each desktop light and dark view, it compares measured application region names with design names, checks that expected visible regions are above the fold, and verifies that region order did not change. It returns an ApplicationDesignFidelity object with passed count, total count, and failure messages.

**Call relations**: audit_application calls this as the design-matching part of the audit. Inside, it relies on application_design_region_size_failure and application_region_relation to turn region measurements into clear pass-or-fail evidence.

*Call graph*: calls 2 internal fn (application_design_region_size_failure, application_region_relation); called by 1 (audit_application); 1 external calls (__init__).


##### `_contrast_failures`  (lines 463–478)

```
def _contrast_failures(views: tuple[ApplicationAuditView, ...]) -> list[str]
```

**Purpose**: This helper gathers readable explanations for every text style whose contrast is too low. It turns raw contrast measurements into messages a builder can act on.

**Data flow**: It receives the measured views. For each text item in each view, it asks _needed_ratio what contrast that item requires, compares the measured ratio with that requirement, and appends a message when the text is not readable enough. It returns the list of failure messages.

**Call relations**: audit_application calls this after confirming which required views were measured. The messages it returns are folded into a single contrast repair issue.

*Call graph*: calls 1 internal fn (_needed_ratio); called by 1 (audit_application).


##### `audit_application`  (lines 481–604)

```
def audit_application(report: ApplicationAuditReport, contract: ApplicationAuditContract | None=None) -> ApplicationAuditVerdict
```

**Purpose**: This is the main audit function. It applies the fixed product, accessibility, layout, interaction, design, and required-fact checks to one browser report and returns the verdict.

**Data flow**: It receives an ApplicationAuditReport and, optionally, an ApplicationAuditContract containing facts the app must show. It builds a lookup of measured views, checks for missing or empty views, low contrast, overflow, clipping, overlaps, design mismatch, browser console errors, too few controls, too few successful interactions, absent required facts, and facts not visible above the desktop fold. Each problem becomes a bounded ApplicationAuditIssue, and the function returns an ApplicationAuditVerdict containing at most the maximum allowed number of issues.

**Call relations**: This is the file’s central flow. It calls _contrast_failures for readability checks, application_design_fidelity for design matching, and _issue whenever it needs to create repair feedback. The returned verdict is what downstream builder or QA code can use to decide whether the application passed or needs repair.

*Call graph*: calls 3 internal fn (_contrast_failures, _issue, application_design_fidelity); 1 external calls (__init__).


### Brief pipeline stages
The brief pipeline configuration defines the outline, draft, and critique stages with their instructions, shapes, and limits.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load`

This file is the recipe card for a small writing assembly line. The extension wants to turn a topic into a useful brief, but it does that in three clear steps instead of asking one agent to do everything at once. First, an outline agent plans the brief. Next, a draft agent writes from that outline. Finally, a critic agent reviews the draft and suggests improvements.

The file defines simple Pydantic models, which are data shapes that check that information has the expected fields. For example, a brief request must include a topic, and may include an audience. The outline stage returns an outline, the draft stage returns a draft, and the critic stage returns a verdict plus optional improvements.

It also builds three SubagentProfile objects. A subagent profile is like a job description for a worker: it names the worker, gives it a prompt file with instructions, says what tools it may use, and states what kind of input and output it must accept. These agents are deliberately given no tools and cannot spawn further agents, so the parent pipeline stays in control of the order and depth of the process. One important detail is that the prompt files are read when this module is loaded, so missing prompt files would break setup before the pipeline can run.
