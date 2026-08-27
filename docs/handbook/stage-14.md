# Delegation, Subagents, Objectives, and Multi-Agent Workflows  `stage-14`

This stage is part of the main work loop, with some shared support behind the scenes. It lets the main agent act like a project lead: break work into tasks, send tasks to helper agents, track progress, and collect results.

The subagent core starts and manages these helpers. Profiles define the default helper and specialized helpers, while contracts check that each task and result has the expected shape, like using a form before work begins and after it ends. The subagent manager queues child turns, waits for them or lets them run separately, cancels them when needed, and returns their final answers.

Several extensions provide ready-made helpers. Browser delegation sends web tasks to one or many browser agents. Research profiles define normal and deep research agents. Site tools hand website-building to a focused builder. The brief pipeline runs outline, draft, and critique as three linked writing jobs.

Objectives tools and storage support longer plans. They record steps, evidence, attempts, and completion, and they re-check real success conditions instead of trusting a simple “done.” The self-improvement files build test sets from past failures, compare prompt changes, and only accept changes that prove reliable.

## Files in this stage

### Objective planning state
Tools and durable storage let agents plan multi-step objectives, track evidence, delegate ready work, and verify completion conditions.

### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `request handling`

This file is the working interface for the objectives extension. It gives agents a small set of verbs: plan an objective, record a step, run independent steps in subagents, and read the objective later. The problem it solves is memory and honesty across turns. A future agent turn may not remember the full conversation, so the objective plan records the goal, the ordered steps, and the real-world checks that prove each step is complete.

The central idea is like a checklist where some boxes can only be ticked after looking at the actual room, not after someone says they cleaned it. For example, a step may require that a file exists, that a file contains certain text, or that a command succeeds. When a step is recorded as attempted, this file runs those checks in the sandbox and stores whether they truly hold.

It also protects against weak plans. If a planned step says it will produce a file, but that file already exists before the step starts, the plan is refused because that condition would prove nothing. Command checks are treated differently because a test suite may already pass before work begins and still be a useful guard later.

Finally, the file turns objective state into readable text and defines the actual tool objects that the agent platform exposes.

#### Function details

##### `_require_ext`  (lines 95–98)

```
def _require_ext(ctx: ToolContext) -> ExtensionContext
```

*Call graph*: called by 4 (plan_objective, read_objective, record_step, run_independent_steps).


##### `render`  (lines 101–116)

```
def render(view: ObjectiveView) -> str
```

*Call graph*: called by 3 (plan_objective, read_objective, record_step); 1 external calls (condition_summary).


##### `evaluate`  (lines 119–123)

```
async def evaluate(ctx: ToolContext, step: StepView) -> tuple[ConditionVerdict, ...]
```

*Call graph*: calls 1 internal fn (_verdict); called by 2 (read_objective, record_step).


##### `_verdict`  (lines 126–150)

```
async def _verdict(ctx: ToolContext, condition: Condition, phase: str) -> ConditionVerdict
```

*Call graph*: called by 2 (evaluate, plan_objective); 4 external calls (__init__, quote, emit_metric, condition_summary).


##### `plan_objective`  (lines 153–208)

```
async def plan_objective(ctx: ToolContext, args: PlanObjectiveInput) -> ToolResult
```

*Call graph*: calls 3 internal fn (_require_ext, _verdict, render); 5 external calls (__init__, __init__, __init__, agent_current, condition_summary).


##### `record_step`  (lines 211–259)

```
async def record_step(ctx: ToolContext, args: RecordStepInput) -> ToolResult
```

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 5 external calls (__init__, __init__, __init__, agent_current, emit_metric).


##### `run_independent_steps`  (lines 262–308)

```
async def run_independent_steps(ctx: ToolContext, args: RunIndependentStepsInput) -> ToolResult
```

*Call graph*: calls 1 internal fn (_require_ext); 6 external calls (__init__, __init__, __init__, spawn, agent_current, emit_metric).


##### `read_objective`  (lines 311–330)

```
async def read_objective(ctx: ToolContext, args: ReadObjectiveInput) -> ToolResult
```

*Call graph*: calls 4 internal fn (_require_ext, _with_verdicts, evaluate, render); 4 external calls (__init__, __init__, __init__, agent_current).


##### `_with_verdicts`  (lines 333–343)

```
def _with_verdicts(view: ObjectiveView, title: str, verdicts: tuple[ConditionVerdict, ...]) -> ObjectiveView
```

*Call graph*: called by 2 (read_objective, record_step); 1 external calls (replace).


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `during objective planning, turn startup, progress recording, and frontier selection`

An objective is a small plan made of steps. This file makes sure that plan survives across turns, heartbeats, and subagent hand-backs. That matters because the next turn must not rely on the last worker’s temporary memory or self-report. It must read the lasting record and decide from facts.

The file defines database tables for objectives, steps, events, and condition checks. Events are append-only: once something was tried or blocked, that history is not edited away. Checks are also appended, so the system can remember what the extension observed at the time.

It also defines the small data shapes used in this record. A step may say what will count as acceptance, such as “this file exists,” “this file contains this text,” or “this command succeeds.” These checks are deliberately concrete. Like a checklist taped to a work order, they stop a worker from later changing the definition of success after the task turns out to be hard.

The main store class, `Objectives`, reads and writes one workspace’s objectives. It can find an objective, create or revise a plan, record that a step was done or blocked, save condition verdicts, and rebuild a readable `ObjectiveView`. The view then derives useful states such as pending, attempted, done, blocked, or unmet from the stored events and checks.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: This property answers the simple question: has anyone recorded that work was done on this step? It treats only a `did` event as an actual attempt.

**Data flow**: It reads the step’s stored event list → looks for any event whose kind is `did` → returns `True` if it finds one, otherwise `False`. It does not change anything.

**Call relations**: Other state-reading code uses this as a building block. `StepView.state` uses it to distinguish a step that has never been tried from one that was tried but not yet confirmed, and `ObjectiveView.runnable` uses it to avoid dispatching already-attempted independent steps.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: This property finds the current unanswered block on a step, if there is one. A block means the worker got stuck and asked for help or clarification.

**Data flow**: It reads the step’s events → looks only at the latest event → if that latest event is `blocked`, it returns that event; otherwise it returns `None`. It does not edit the event history.

**Call relations**: This helps prevent noisy repeats. `Objectives.record` checks it before writing another block, and `ObjectiveView.runnable` uses it so a step with an open block is not treated as ready to run.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: This property turns the raw history of a step into a plain status such as pending, attempted, done, blocked, or unmet. It is the central rule for deciding whether a step is complete.

**Data flow**: It reads the latest event, whether the step was attempted, the step’s acceptance conditions, and any saved verdicts → applies the file’s rules in order → returns a state string. It does not write anything back.

**Call relations**: Higher-level views depend on this answer. `ObjectiveView.confirmed` counts steps whose state is done, and `ObjectiveView.frontier` keeps every step whose state is not done.


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: This property counts how many recorded work attempts exist across the whole objective. It gives a quick sign of how much effort has already been spent.

**Data flow**: It reads every step and every event inside those steps → counts events whose kind is `did` → returns that count. Nothing is changed.

**Call relations**: It summarizes the history stored in the view. Other code can use it to notice objectives that keep getting attempts without much confirmed progress.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: This property counts how many steps are truly done according to the extension’s own state rules. It is not just a count of worker claims.

**Data flow**: It reads every step → asks each step for its derived `state` → counts the ones marked `done` → returns that number. It does not alter the objective.

**Call relations**: It relies on `StepView.state`, so it inherits the rule that a step with acceptance conditions is not done until the extension has checked them.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: This property identifies the steps that may be sent out to run in parallel. It selects only unfinished steps that were explicitly marked independent, have not already been attempted, and are not waiting on an open block.

**Data flow**: It starts with the objective’s `frontier` of unfinished steps → filters for independent steps with no attempt and no open block → returns those steps as a tuple. It does not start the work itself.

**Call relations**: It builds on `ObjectiveView.frontier`, `StepView.attempted`, and `StepView.open_block`. A dispatcher can use this result when deciding which steps can safely fan out at the same time.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: This property returns the unfinished part of the objective. It is the current work queue in its broadest form.

**Data flow**: It reads all steps → asks each step for its state → keeps every step whose state is not `done` → returns those steps. It does not change the plan or the records.

**Call relations**: This is the base list used by `ObjectiveView.runnable`. Anything deciding what remains to do can start here.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: This helper turns a machine-readable acceptance condition into a short human-readable sentence. It is useful when showing or explaining what must be true for a step to pass.

**Data flow**: It receives one condition object → checks which kind it is: file exists, file contains text, or command succeeds → returns a short text summary. It does not read or write the database.

**Call relations**: It sits beside the storage logic as a display helper. Code that needs to present conditions to a person can call it instead of formatting each condition type itself.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: This method finds one objective by conversation and name within the current workspace. Scoping by conversation prevents a subagent or another conversation from accidentally taking over an objective with the same name.

**Data flow**: It receives a conversation ID and objective name → queries the objective table for a matching row in this workspace → if found, asks `_view` to build the full readable objective; if not found, returns `None`.

**Call relations**: `Objectives.plan` calls this first to decide whether it is creating a new objective or revising an existing one. When a row is found, this method hands off to `Objectives._view` to load the steps, events, and checks.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: This method finds the most recently created objective for a conversation in the current workspace. It is a convenient way to reopen the current objective when only the conversation is known.

**Data flow**: It receives a conversation ID → queries the objective table for objectives in that conversation and workspace → orders them newest first and takes one → returns a full `ObjectiveView` through `_view`, or `None` if none exists.

**Call relations**: Like `Objectives.named`, it uses `Objectives._view` to turn a database row into the full in-memory view. It is called when code wants the latest objective for a conversation rather than a particular named one.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: This method creates a new objective or revises an existing objective’s plan. It is careful not to let already-attempted steps change their acceptance conditions after the fact.

**Data flow**: It receives a conversation ID, name, directive, and planned steps → looks for an existing objective with `named` → inserts a new objective if needed, or updates the directive if it already exists → keeps acceptance conditions frozen for steps that have already been attempted → removes unstarted old steps that are no longer in the plan → updates or inserts the current steps → reloads and returns the finished `ObjectiveView`.

**Call relations**: This is the main write path for planning. It calls `Objectives.named` at the start and again at the end, and uses database insert, update, and delete operations to make the durable record match the revised plan while preserving important history.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: This method appends a new event to a step, such as work being done or the step becoming blocked. It refuses to write the same still-open block again, which prevents repeated heartbeat turns from flooding the record with the same question.

**Data flow**: It receives a step view, event kind, actor turn ID, and evidence text → trims the evidence to the maximum allowed length → checks whether the same block is already open → if it is a duplicate block, returns `False`; otherwise inserts a new event row and returns `True`.

**Call relations**: It uses `StepView.open_block` to recognize a standing block before writing. Other objective tools call this when a worker reports progress or blockage, and the saved event later feeds into `Objectives._view` and `StepView.state`.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: This method records what the extension observed when it evaluated a step’s acceptance conditions. These verdicts are stored as observations, not as worker claims.

**Data flow**: It receives a step, a tuple of condition verdicts, and an actor turn ID → converts each verdict into JSON-friendly data containing the condition, whether it held, and detail text → inserts a new check row. It returns no value.

**Call relations**: This is called after the extension has actually checked the conditions. Later, `Objectives._view` reads the latest check back so `StepView.state` can decide whether the step is done, still attempted, or unmet.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: This private method rebuilds a complete readable objective from database rows. It gathers the objective’s steps, their events, and the latest saved condition checks into one `ObjectiveView`.

**Data flow**: It receives an objective table row → queries for that objective’s steps, events, and checks → groups events by step → keeps the latest check per step → parses saved conditions and verdicts back into typed objects → returns an `ObjectiveView` containing `StepView` objects.

**Call relations**: `Objectives.named` and `Objectives.on_conversation` call this after finding an objective row. It hands off JSON parsing to `_conditions` and `_verdicts`, then builds the view objects that the rest of the extension reads.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: This helper turns raw saved JSON condition data back into condition objects the code can trust and use. It rejects unknown condition kinds instead of silently guessing.

**Data flow**: It receives a payload from the database → if the payload is not a list, returns an empty tuple → otherwise reads each item’s `kind` → validates it as `FileExists`, `FileContains`, or `CommandSucceeds` → returns the parsed conditions as a tuple.

**Call relations**: `Objectives._view` uses it when rebuilding steps from the database. `_verdicts` also uses it to parse the condition stored inside each saved verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This helper turns raw saved JSON verdict data back into `ConditionVerdict` objects. A verdict says whether one acceptance condition held and includes a short detail message.

**Data flow**: It receives a database payload → if it is not a list, returns an empty tuple → for each dictionary item, parses the nested condition with `_conditions`, converts the hold value to a boolean, converts the detail to text → returns a tuple of verdict objects.

**Call relations**: `Objectives._view` calls this when loading the latest condition check for each step. It depends on `_conditions` so verdicts and planned conditions are interpreted using the same rules.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).


### Subagent lifecycle contracts
Core profiles, orchestration, and validation define how child agents are configured, spawned, monitored, messaged, cancelled, and returned.

### `core/src/ufo/loop/profiles.py`

`config` · `startup and subagent creation`

This file is the system’s fallback recipe for creating a child agent. A child agent is like a focused coworker: the main agent gives it a self-contained job, and it works in the same shared workspace to produce a result.

The profile here is called `general_purpose`. It is deliberately useful but limited. It can read, write, edit, search files, run shell commands, load skills, share files, and use certain optional extension tools if those extensions are installed. But it cannot ask the user questions, create more subagents, message or cancel sibling subagents, or approve account access. That matters because a delegated helper should make progress independently without taking over coordination or user-facing decisions.

The file also builds the actual instruction text the subagent will see. Those instructions tell it to make reasonable assumptions, avoid retry loops, load relevant skills first, use proper Office formats for formal document deliverables, and save useful work in `/workspace` with clear names. A skill index placeholder is included so the available skills can be inserted later.

Finally, the file wraps all of this into a `SubagentProfile` object and exposes it as the only core subagent profile. Extensions can add more specialized profiles, but this one is the safe default.


### `core/src/ufo/loop/subagents.py`

`orchestration` · `request handling`

This file solves a common problem in agent systems: a main agent often needs to delegate work without blocking the whole conversation. Think of it like asking a coworker to research something. Sometimes you wait at their desk for the answer. Other times you send them off and keep talking to the customer, then read their report later.

The file supports two kinds of helpers. A subagent profile is a predefined helper recipe: prompt, allowed tools, and input/output shapes. A workspace agent is a real saved agent in the workspace, with its own identity, tools, model, and ownership rules. The code resolves the requested name, checks that the caller is allowed to use it, validates the input, creates a new child conversation and turn in the database, and puts that child turn on the DBOS work queue.

If the caller asks for foreground mode, the parent waits for the child to finish and returns only a schema-checked final answer. If a user message arrives while waiting, the child can be detached so the parent can answer the user while the child keeps running. If the child runs in background, this file later delivers its result back into the parent conversation in a guarded envelope, protecting the parent from untrusted or invalid content.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 133–137)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the configured subagent profiles do not reuse the same name. This matters because spawning by name would be unsafe or confusing if two different helper recipes had the same label.

**Data flow**: It reads the profile names stored in the registry, looks for repeated names, and either leaves the registry usable or raises an error listing the duplicates.

**Call relations**: This runs automatically when a SubagentRegistry is created, before any lookup happens. It protects later calls such as SubagentRegistry.get and SubagentRegistry.find from ambiguous profile names.


##### `SubagentRegistry.get`  (lines 139–145)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: Returns the subagent profile with a given name, and treats a missing name as an error. Use this when the caller requires the profile to exist.

**Data flow**: It receives a profile name, asks SubagentRegistry.find for a matching profile, and returns it if found. If not found, it raises an UnknownSubagentProfile error that includes the valid profile names.

**Call relations**: It builds on SubagentRegistry.find for the actual search. The queue setup code uses it when it must resolve a profile and cannot continue safely without one.

*Call graph*: calls 2 internal fn (find, __init__); called by 1 (_resolve_profile).


##### `SubagentRegistry.find`  (lines 147–148)

```
def find(self, name: str) -> SubagentProfile | None
```

**Purpose**: Looks up a subagent profile by name, returning nothing if it is absent. This is the gentle lookup used when absence is allowed and the caller will decide what to do next.

**Data flow**: It receives a name, scans the registry's stored profiles, and returns the first profile whose name matches. If none match, it returns null.

**Call relations**: SubagentRegistry.get calls this and turns a null result into a clear error. Other code in this file also uses the same idea when it needs to know whether a profile is still registered.

*Call graph*: called by 1 (get).


##### `_target_model`  (lines 163–171)

```
def _target_model(resolved: SubagentProfile | AgentTarget) -> str | None
```

**Purpose**: Figures out which model should be billed or selected for a spawn target when that target is a profile. Workspace agents do not return a model here because their model is read from their own agent record.

**Data flow**: It receives a resolved spawn target. If the target is a subagent profile, it returns the model pinned by that profile; otherwise it returns null.

**Call relations**: Subagents.spawn uses this just before admitting a child turn, so the balance check can weigh the new work against the model that will actually answer.

*Call graph*: called by 1 (spawn).


##### `subagent_system_prompt`  (lines 174–205)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: Builds the system prompt for a profile-based subagent. The system prompt is the instruction text the child agent sees before doing its work.

**Data flow**: It receives a profile plus optional skill information. It fills the skill index slot, checks for leftover template slots, optionally appends preloaded skill text within a size limit, then appends shared rules about citations, result delivery, and finishing through the required output schema.

**Call relations**: It relies on prompt rendering helpers and skill-loading helpers to turn profile instructions into final text. It is used by the broader turn execution path when preparing a profile child to run.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 222–223)

```
def authorize(self, requester_member_id: UUID | None) -> 'Subagents'
```

**Purpose**: Returns a copy of the Subagents helper that is bound to a specific requesting member. This lets later permission checks know whose authority the spawn is using.

**Data flow**: It receives a member id, copies the current Subagents object, and stores that id as the requester. The original object is unchanged.

**Call relations**: Later methods such as Subagents.acting_member_id and Subagents._may_spawn read this requester when deciding whether a workspace agent may be spawned.

*Call graph*: 1 external calls (replace).


##### `Subagents.acting_member_id`  (lines 226–235)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Chooses the member identity that a spawn should act under. This is important because ownership, billing visibility, and follow-up authority must all agree on the same person.

**Data flow**: It reads, in order, the explicitly authorized requester, the parent turn's speaker, and the member the parent turn acts on behalf of. It returns the first one that exists, or null if none is known.

**Call relations**: Permission checks, child turn admission, follow-up messages, and child ownership checks all depend on this value so they apply a consistent authority model.


##### `Subagents.spawn`  (lines 237–350)

```
async def spawn(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnResult
```

**Purpose**: Starts a child helper turn. It is the main entry for asking a profile or workspace agent to do delegated work.

**Data flow**: It receives a target name, input payload, and options such as background mode or deduplication. It resolves the target, checks permissions, validates the input, creates or reuses a child conversation and turn, enqueues it, and either returns the child id immediately or waits for a checked final answer.

**Call relations**: This is the central flow that calls the resolver, admission, queueing, waiting, detaching, billing, and output-validation pieces. Tool code calls it when a model uses the spawn feature.

*Call graph*: calls 7 internal fn (_admit, _await_terminal, _await_terminal_or_detach, _enqueue, _may_spawn, _resolve, _target_model); 8 external calls (__init__, __init__, turn_id_for, cancel_one_turn, input_contract, output_contract, uuid4, uuid5).


##### `Subagents.result`  (lines 352–389)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Reads the final result of a child turn that has already finished. It gives host-side code a safe way to inspect what happened without asking a model to repeat it.

**Data flow**: It receives a child turn id, confirms the turn belongs to this spawning conversation, loads its terminal record from the database, finds the right output contract, and returns a SpawnResult with validated output when possible.

**Call relations**: It depends on Subagents._require_child for authorization and on schema helpers to validate the output. It is used after a child has completed, especially for background-result workflows.

*Call graph*: calls 3 internal fn (_agent_output_schema, _require_child, _untrusted_output); 5 external calls (__init__, model_validate, select, workspace_tx, output_contract).


##### `Subagents.wait`  (lines 391–411)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Waits for one or more child turns to finish and returns their status and final text. This is for tools that deliberately hold open their own call while a child works.

**Data flow**: It receives child turn ids, verifies each one belongs to this conversation, waits for each terminal result, and returns a tuple of simple status objects including text and whether the output should be treated as untrusted.

**Call relations**: It uses the same child check and terminal-waiting path as foreground spawn. Unlike normal background delivery, this function is for a bounded, explicit wait inside a tool call.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 413–430)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Cancels a running child turn that this conversation spawned. It prevents abandoned helper work from continuing when the parent no longer wants it.

**Data flow**: It receives a child turn id, verifies ownership, asks the shared cancellation routine to stop it, reloads its status and terminal text from the database, and returns a SubagentStatus.

**Call relations**: It is the cancellation companion to spawn and wait. It uses Subagents._require_child to make sure one conversation cannot cancel another conversation's helper.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents.message`  (lines 432–554)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Sends a follow-up message to a background child. This lets the parent answer a child's question or continue a child conversation without starting from scratch.

**Data flow**: It receives an existing child turn id, message text, a deduplication key, and a delivery option. It verifies the child, checks the profile still exists when needed, creates or reuses the next turn in the child's conversation, checks balance for new work, and enqueues it if it is ready.

**Call relations**: It fits after a spawned child has gone background or asked a question. It uses the same queueing and balance machinery as new spawns, but appends work to the child's own conversation.

*Call graph*: calls 4 internal fn (_enqueue, _profile_model, _require_balance, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._resolve`  (lines 556–579)

```
async def _resolve(self, target: str) -> SubagentProfile | AgentTarget
```

**Purpose**: Turns a user-supplied spawn target name into either a profile or a workspace agent. It also catches ambiguous names so the caller must be explicit.

**Data flow**: It receives a target string. If the string has a profile: or agent: prefix, it searches only that namespace; otherwise it searches both profiles and workspace agents. It returns the matching target or raises a clear unknown or ambiguous target error.

**Call relations**: Subagents.spawn calls this before it can validate input or create a child. It uses profile-name and agent-name helpers to produce helpful error messages.

*Call graph*: calls 5 internal fn (_agent_names, _agent_target, _profile_names, __init__, __init__); called by 1 (spawn).


##### `Subagents._profile_names`  (lines 581–582)

```
def _profile_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the list of registered profile names. This is mainly used to explain what valid profile targets exist.

**Data flow**: It reads the registry's profiles, sorts their names, and returns them as an immutable tuple.

**Call relations**: Subagents._resolve uses this when reporting unknown or ambiguous target errors.

*Call graph*: called by 1 (_resolve).


##### `Subagents._agent_names`  (lines 584–596)

```
async def _agent_names(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of active workspace agents that can be considered as spawn targets. Archived agents are left out.

**Data flow**: It opens a workspace database transaction, selects non-archived agent names for the parent workspace, orders them, and returns the names.

**Call relations**: Subagents._resolve uses this to build clear error messages when a requested target cannot be found.

*Call graph*: called by 1 (_resolve); 2 external calls (select, workspace_tx).


##### `Subagents._agent_target`  (lines 598–623)

```
async def _agent_target(self, name: str) -> AgentTarget | None
```

**Purpose**: Looks up a workspace agent by name and packages the facts needed to spawn it. These facts include its id, owner, and input/output schemas.

**Data flow**: It receives an agent name, queries the workspace database for a non-archived agent with that name, and returns an AgentTarget if found. If no row matches, it returns null.

**Call relations**: Subagents._resolve calls this while deciding whether a target name refers to a workspace agent.

*Call graph*: called by 1 (_resolve); 3 external calls (__init__, select, workspace_tx).


##### `Subagents._agent_output_schema`  (lines 625–631)

```
async def _agent_output_schema(self, agent_id: UUID) -> dict[str, object] | None
```

**Purpose**: Fetches the output schema for a workspace agent. The output schema describes the shape of a valid final answer.

**Data flow**: It receives an agent id, queries the agent table, and returns that agent's output schema, which may be null if no custom schema is declared.

**Call relations**: Subagents.result uses this when reading the result of an agent child, because agent children do not use profile output contracts.

*Call graph*: called by 1 (result); 2 external calls (select, workspace_tx).


##### `Subagents._may_spawn`  (lines 633–645)

```
async def _may_spawn(self, owner_member_id: UUID | None) -> bool
```

**Purpose**: Decides whether the current acting member is allowed to spawn a workspace agent. Owners may run their own agents, and workspace admins may run any agent.

**Data flow**: It receives the target agent's owner member id. It compares that owner to the acting member, and if that is not enough, checks the database to see whether the acting member is a workspace admin.

**Call relations**: Subagents.spawn calls this only for workspace-agent targets. Profile children do not go through this ownership gate because they run under the parent's agent.

*Call graph*: called by 1 (spawn); 2 external calls (workspace_tx, member_is_admin).


##### `Subagents._untrusted_output`  (lines 647–656)

```
def _untrusted_output(self, profile: str | None) -> bool
```

**Purpose**: Decides whether a child's output should be treated as untrusted content. Untrusted content is wrapped so the parent agent reads it as data, not as new instructions.

**Data flow**: It receives a profile name or null for an agent child. Agent children are always untrusted; missing profiles are treated as untrusted; registered profiles use their own untrusted-output setting.

**Call relations**: Subagents.result and Subagents.wait use this when reporting child output. SubagentResult._body applies the same idea when delivering background results.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 658–694)

```
async def _require_child(self, turn_id: UUID) -> str | None
```

**Purpose**: Confirms that a turn id names a child spawned by this conversation and by the same acting member. This prevents one user or conversation from controlling another user's helper.

**Data flow**: It receives a turn id, loads its parent, profile, and acting member from the database, and checks whether it was spawned by this turn or by another turn in the same parent conversation. It returns the child's profile name, or null for an agent child, if allowed.

**Call relations**: Cancel, message, result, and wait all call this before touching a child. It is the main safety gate for operations on existing subagents.

*Call graph*: called by 4 (cancel, message, result, wait); 2 external calls (select, workspace_tx).


##### `Subagents._profile_model`  (lines 696–702)

```
def _profile_model(self, profile: str | None) -> str | None
```

**Purpose**: Finds the model pinned by a profile name, if that profile still exists. It is used for billing follow-up work on profile children.

**Data flow**: It receives a profile name or null, searches the current registry, and returns the profile's model if found. If the profile is absent or unnamed, it returns null.

**Call relations**: Subagents.message uses this before checking balance for a follow-up turn. It deliberately does not fail if a profile disappeared, because the child already exists.

*Call graph*: called by 1 (message).


##### `Subagents._require_balance`  (lines 704–722)

```
async def _require_balance(self, connection: AsyncConnection, model: str | None, agent_id: UUID | None=None) -> None
```

**Purpose**: Checks whether the workspace has enough prepaid balance to start new child work. This prevents a fan-out of helper turns from bypassing spending limits.

**Data flow**: It receives a database connection, an optional model, and optionally the child agent id. It asks the balance gate whether the work is allowed and raises BalanceExhausted if the answer is no.

**Call relations**: Subagents._admit calls this for a new child turn, and Subagents.message calls it for a new follow-up turn. Reused or already-running work does not pass through this check again.

*Call graph*: called by 2 (_admit, message); 2 external calls (__init__, __init__).


##### `Subagents._admit`  (lines 724–813)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, *, agent_id: UUID, profile: str | None, inherits_sandbox: bool, inbound: str, delivers_result: bool=False, name: str='', model: str | None=
```

**Purpose**: Creates the database records for a child conversation and its first turn, or reconnects to existing records when a deduplication key is reused. Admission is the durable step before queueing work.

**Data flow**: It receives child ids, agent/profile details, input text, sandbox behavior, delivery options, and billing model information. It inserts the conversation and turn if missing, verifies the acting member, checks balance for a queued new turn, stamps it as ready for dispatch, and returns whether it should be enqueued.

**Call relations**: Subagents.spawn calls this after resolving and validating the target. If it returns true, Subagents.spawn hands the turn to Subagents._enqueue.

*Call graph*: calls 1 internal fn (_require_balance); called by 1 (spawn); 6 external calls (select, update, workspace_tx, conversation_name, current_traceparent, audience_member).


##### `Subagents._enqueue`  (lines 815–844)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: Puts a child or follow-up turn onto the DBOS workflow queue so a worker can run it. DBOS is the durable workflow system used here to run queued turns.

**Data flow**: It receives a turn id and conversation id, builds queue options, and asks the DBOS client to enqueue the workflow. If enqueueing is cancelled or fails, it clears the dispatch marker in the database so another dispatcher can try later.

**Call relations**: Subagents.spawn uses this for first child turns, and Subagents.message uses it for follow-up turns. It is the bridge from database admission to actual execution.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 846–889)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: Waits until a child turn has a terminal frame, meaning a recorded final state such as done, failed, or cancelled. It also handles workflow restarts and parked children.

**Data flow**: It receives a child turn id, watches the DBOS workflow result when possible, repeatedly checks the database for a terminal or parked state, follows a newer running attempt if one appears, and returns the terminal frame once committed.

**Call relations**: Foreground spawn, explicit wait, and interruptible wait all depend on this function. It delegates database checks to Subagents._terminal_or_park and running-attempt lookup to Subagents._running_attempt.

*Call graph*: calls 2 internal fn (_running_attempt, _terminal_or_park); called by 3 (_await_terminal_or_detach, spawn, wait); 1 external calls (sleep).


##### `Subagents._running_attempt`  (lines 891–897)

```
async def _running_attempt(self, turn_id: UUID) -> str | None
```

**Purpose**: Reads which workflow attempt is currently running for a turn. This helps waiting code follow a turn if execution was retried under a different workflow id.

**Data flow**: It receives a turn id, queries the turn row, and returns the running attempt id or null.

**Call relations**: Subagents._await_terminal uses this when the workflow it was watching is missing or ended before a terminal was stored.

*Call graph*: called by 1 (_await_terminal); 2 external calls (select, workspace_tx).


##### `Subagents._await_terminal_or_detach`  (lines 899–954)

```
async def _await_terminal_or_detach(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Waits for a child to finish, but stops waiting if a member message arrives for the parent conversation first. In that case, the child is moved to background delivery instead of being cancelled.

**Data flow**: It receives a child turn id, starts one task waiting for the child terminal and another waiting for parent-conversation arrivals. If the child finishes first, it returns the terminal; if a valid member arrival wins, it marks the child for result delivery and returns null.

**Call relations**: Subagents.spawn uses this when detach_on_arrival is requested. It calls Subagents._await_terminal for the normal wait path and Subagents._detach to safely hand result delivery to the background path.

*Call graph*: calls 2 internal fn (_await_terminal, _detach); called by 1 (spawn); 5 external calls (create_task, ensure_future, gather, wait, log).


##### `Subagents._terminal_or_park`  (lines 956–972)

```
async def _terminal_or_park(self, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Checks whether a turn has finished, and treats a parked child as something the parent should stop waiting on. A parked turn is one paused on a spending limit or similar condition without a final result.

**Data flow**: It receives a turn id, reads the turn's terminal and status from the database, and returns a TerminalFrame if present. If the status is parked, it cancels the child and raises SubagentParked; otherwise it returns null.

**Call relations**: Subagents._await_terminal calls this repeatedly while waiting. It is the safety valve that prevents a parent turn from hanging forever on a child that cannot make progress.

*Call graph*: called by 1 (_await_terminal); 5 external calls (__init__, model_validate, select, workspace_tx, cancel_one_turn).


##### `Subagents._detach`  (lines 974–1005)

```
async def _detach(self, turn_id: UUID, arrival_id: UUID) -> bool
```

**Purpose**: Marks a still-running foreground child so its result will be delivered later instead of returned inline. It does this only if the parent really has a fresh member message waiting.

**Data flow**: It receives a child turn id and an arrival id, then performs one guarded database update. The update succeeds only if the child has no terminal, is not already marked for delivery, and the arrival belongs to the parent conversation and has not been consumed.

**Call relations**: Subagents._await_terminal_or_detach calls this when an arrival notification appears. Its true or false return resolves the race between the child finishing and the parent being interrupted.

*Call graph*: called by 1 (_await_terminal_or_detach); 4 external calls (exists, select, update, workspace_tx).


##### `SubagentResult.deliver`  (lines 1029–1070)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: Posts a finished background child's result back into the conversation that spawned it. This lets the parent receive the child result as a normal incoming event instead of staying blocked.

**Data flow**: It receives a child Turn, ignores it unless result delivery is pending, confirms it has a terminal, loads the parent conversation and any needed child-agent data, builds the delivery body, invokes a new parent-side turn with a stable idempotency key, and marks the child result as delivered.

**Call relations**: This is the background-result path paired with Subagents.spawn and Subagents._detach. It calls SubagentResult._body to create safe message content before handing it to the turn invoker.

*Call graph*: calls 1 internal fn (_body); 3 external calls (select, update, workspace_tx).


##### `SubagentResult._body`  (lines 1072–1101)

```
def _body(self, child: Turn, child_agent: sa.Row | None) -> str
```

**Purpose**: Builds the actual text delivered to the parent conversation for a child result. It wraps the payload in a clear spawn-result envelope that names the child and target.

**Data flow**: It receives the child turn and, for agent children, the child agent row. It chooses the target label, finds the right output contract, asks SubagentResult._payload for checked content and status, wraps untrusted content when needed, escapes closing tags inside the payload, and returns the final message body.

**Call relations**: SubagentResult.deliver calls this before invoking the parent conversation. It uses output-contract and untrusted-content helpers so delivered results follow the same safety rules as foreground results.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 2 external calls (output_contract, wall).


##### `SubagentResult._payload`  (lines 1103–1122)

```
def _payload(self, contract: Contract | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: Turns a child's terminal frame into the payload and status used in a delivered spawn result. It distinguishes success, questions, failures, missing schemas, and invalid output.

**Data flow**: It receives an output contract or null plus a terminal frame. If the child failed, it returns diagnostic text and the failure status; if the child asked a question, it returns the structured question; if there is no contract, it returns a withheld-result message; otherwise it validates the final text and returns either clean JSON or an invalid-output explanation.

**Call relations**: SubagentResult._body calls this while building the result envelope. This is the last validation step before a background child result is shown to the parent conversation.

*Call graph*: called by 1 (_body); 1 external calls (model_validate_json).


### `core/src/ufo/turns/contracts.py`

`domain_logic` · `spawn, dispatch, delivery validation, and schema write-time checks`

When one agent gives work to another, both sides need a clear agreement about the shape of the data being passed. This file is that agreement layer. By default, a task is a simple object with a `task` string, and a result is a simple object with a `result` string. But workspace agents can also declare their own JSON Schema, which is a plain-data rulebook for JSON objects.

The important idea is that both kinds of contract behave the same way. A Pydantic model and this file’s `JsonContract` can both show their schema, validate Python data, and validate JSON text. That means the rest of the system does not need two separate paths.

The file is also defensive. User-declared schemas are checked before they are stored. They must be small, must describe a top-level object, must be valid JSON Schema, and cannot contain references or regular-expression features. This matters because references could cause the server to look outside the stored data, and regular expressions can sometimes be made expensive to run. In everyday terms, it only accepts self-contained rulebooks that are small enough and safe enough to read during normal service.

#### Function details

##### `ValidatedJson.model_dump`  (lines 53–54)

```
def model_dump(self) -> object
```

**Purpose**: Returns the already-validated payload as ordinary Python data. It lets a raw JSON-Schema-validated value act like a Pydantic model when other code asks for its contents.

**Data flow**: It starts with a `ValidatedJson` object holding some data that has already passed validation. It simply gives that stored data back unchanged.

**Call relations**: This is part of the small adapter that makes raw JSON Schema validation look like normal model validation. After `JsonContract.model_validate` accepts a payload and wraps it in `ValidatedJson`, later code can call this method the same way it would call it on a Pydantic model.


##### `ValidatedJson.model_dump_json`  (lines 56–57)

```
def model_dump_json(self) -> str
```

**Purpose**: Turns the already-validated payload into a JSON string. This is useful when the rest of the system expects a model-like object that can serialize itself.

**Data flow**: It reads the stored validated data, passes it to JSON encoding, and returns the resulting text. It does not re-check the data or change it.

**Call relations**: This completes the model-like wrapper created by `JsonContract.model_validate`. Together with `model_dump`, it lets JSON-Schema-backed payloads be used in places that expect Pydantic-style validated objects.

*Call graph*: 1 external calls (dumps).


##### `JsonContract.model_json_schema`  (lines 66–67)

```
def model_json_schema(self) -> dict[str, object]
```

**Purpose**: Returns the JSON Schema that defines this contract. Other code can use this to inspect or publish the rules for a payload.

**Data flow**: It reads the schema stored inside the `JsonContract`, copies it into a normal dictionary, and returns that copy. The original stored schema is not changed.

**Call relations**: This is one of the methods that makes `JsonContract` stand in for a Pydantic model class. Wherever the system asks a contract what shape it expects, this method supplies the raw schema.


##### `JsonContract.model_validate`  (lines 69–104)

```
def model_validate(self, data: object) -> ValidatedJson
```

**Purpose**: Checks ordinary Python data against the stored JSON Schema. If the data is valid, it wraps it in `ValidatedJson`; if not, it raises a Pydantic `ValidationError` so callers see the same kind of error they get from normal Pydantic models.

**Data flow**: It takes incoming Python data and the contract’s stored schema. It runs a JSON Schema validator over the data, using an empty reference registry so schema references cannot fetch or resolve outside material. If the schema contains an unresolvable reference, or if the data breaks one or more schema rules, it converts those problems into Pydantic-style validation errors. If there are no problems, it returns a `ValidatedJson` wrapper around the original data.

**Call relations**: This is the main validation step for raw JSON-Schema-backed contracts. `JsonContract.model_validate_json` calls it after turning JSON text into Python data, so both text input and already-parsed input end up going through the same rule checker.

*Call graph*: called by 1 (model_validate_json); 6 external calls (__init__, from_exception_data, Draft202012Validator, InitErrorDetails, PydanticCustomError, Registry).


##### `JsonContract.model_validate_json`  (lines 106–122)

```
def model_validate_json(self, text: str) -> ValidatedJson
```

**Purpose**: Checks JSON text against the stored JSON Schema. It first makes sure the text is valid JSON, then reuses the normal data validation path.

**Data flow**: It receives a string. It tries to parse that string as JSON; if parsing fails, it raises a Pydantic-style validation error explaining that the JSON is invalid. If parsing succeeds, it sends the parsed data to `JsonContract.model_validate` and returns that result.

**Call relations**: This is the text-entry version of contract validation. It hands parsed data to `JsonContract.model_validate`, which means JSON strings and Python objects get the same schema checking and the same error shape.

*Call graph*: calls 1 internal fn (model_validate); 4 external calls (from_exception_data, loads, InitErrorDetails, PydanticCustomError).


##### `input_contract`  (lines 128–129)

```
def input_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract used for an incoming task payload. If no custom schema is supplied, it uses the default task model; otherwise it wraps the supplied schema in `JsonContract`.

**Data flow**: It receives either `None` or a mapping that represents a JSON Schema. With `None`, it returns the built-in `TaskInput` model. With a schema, it creates and returns a `JsonContract` around that schema.

**Call relations**: This is a small factory for the input side of spawning or dispatching work. It hides the difference between the default Pydantic model and a declared JSON Schema, so later validation code can treat both as a contract.

*Call graph*: 1 external calls (__init__).


##### `output_contract`  (lines 132–133)

```
def output_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the contract used for a result payload. If no custom schema is supplied, it uses the default result model; otherwise it wraps the supplied schema in `JsonContract`.

**Data flow**: It receives either `None` or a mapping that represents a JSON Schema. With `None`, it returns the built-in `ResultOutput` model. With a schema, it creates and returns a `JsonContract` around that schema.

**Call relations**: This is the output-side partner to `input_contract`. It gives the rest of the delivery flow one contract object to use, whether the result shape is the standard one or a workspace-declared schema.

*Call graph*: 1 external calls (__init__).


##### `check_declared_schema`  (lines 136–151)

```
def check_declared_schema(candidate: Mapping[str, object], field: str) -> None
```

**Purpose**: Checks a user-declared schema before it is stored, so unsafe or unsuitable schemas are rejected early. It protects the running service from oversized schemas, outside references, and regular-expression features that could be expensive to evaluate.

**Data flow**: It receives a candidate schema and the name of the field being checked. It serializes the schema to measure its size, confirms that the top-level payload is an object, asks `_refused_keyword` whether forbidden keywords appear anywhere inside, and then asks the JSON Schema library whether the schema itself is valid. If any check fails, it raises `ValueError`; otherwise it returns nothing, meaning the schema is acceptable.

**Call relations**: This function is meant to run when a declared contract is written or saved, not later when an agent is spawned. It calls `_refused_keyword` to search the whole schema tree before letting the JSON Schema library compile-check the remaining shape.

*Call graph*: calls 1 internal fn (_refused_keyword); 2 external calls (dumps, check_schema).


##### `_refused_keyword`  (lines 154–166)

```
def _refused_keyword(node: object) -> str | None
```

**Purpose**: Searches through a schema for keywords this project does not allow in declared contracts. It finds references and regular-expression-related rules no matter how deeply they are nested.

**Data flow**: It receives any value from the schema tree. If the value is a mapping, it checks each key and then recursively checks each nested value. If the value is a list, it recursively checks each item. It returns the first forbidden keyword it finds, or `None` if the whole subtree is clean.

**Call relations**: This is the helper used by `check_declared_schema` during schema write-time validation. It acts like a careful inspector walking every room of a house, reporting the first banned item it sees so the caller can reject the schema with a clear message.

*Call graph*: called by 1 (check_declared_schema).


### Specialized delegation workflows
Extension profiles and tools describe reusable child-agent workflows for brief writing, browser work, research, and website building.

### `extensions/brief_pipeline/ufo_ext_brief_pipeline/pipeline.py`

`config` · `extension load`

This file is like a recipe card for a small writing assembly line. The goal is to produce a better brief by splitting the work into three focused stages instead of asking one agent to do everything at once. First, an outline agent receives a topic and audience and returns an outline. Next, a draft agent receives the topic plus that outline and writes the draft. Finally, a critic agent reads the draft and returns a verdict with suggested improvements.

The file uses Pydantic models, which are simple typed data shapes that check fields are present and in the expected form. These models act like labeled boxes passed between stages, so the parent agent knows exactly what information to send and what to expect back.

The three SubagentProfile objects describe the actual worker roles. Each profile names the subagent, loads its instruction prompt from a nearby Markdown file, says it has no tools, sets its input and output data shapes, and limits how many conversation rounds it may take. Because these subagents cannot spawn other subagents and have no tools, the pipeline stays predictable: the parent agent drives the sequence from outline to draft to critique.


### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `tool request handling`

This file is the bridge between a general agent and a specialized browser agent. Instead of giving the main agent direct control of a browser, it asks a child agent with the browser profile to do the web task and report back. This keeps browser work isolated, like hiring a specialist courier instead of giving everyone the keys to the delivery truck.

The single-task tool, `browser_task`, starts one fresh browser session with a URL, a task description, and a friendly task name. It waits for the browser subagent to finish, but only up to a bounded timeout. If the website hangs or the browser agent gets stuck, this file cancels the child task so the parent does not wait forever.

The batch tool, `wide_browse`, reads a workspace file containing one entity per line, such as websites or company names. It removes blank lines and duplicates, limits the total size, then sends each entity to a browser subagent. It uses a small parallel pool so several browser visits can happen at once without launching too many at the same time. Each result is collected into `wide_browse.json` and also returned to the caller.

The file also defines the input shapes for these tools, including required fields and limits. Its most important safety ideas are bounded time, bounded fan-out, and deterministic deduplication keys so recovered runs reconnect to already-started child work instead of accidentally duplicating it.

#### Function details

##### `_browser_task`  (lines 92–121)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one complete browser automation task by spawning a browser subagent and waiting for its summary. It protects the parent agent from a stuck website or runaway browser loop by enforcing a timeout and cancelling the child if needed.

**Data flow**: It receives the current tool context and a `BrowserTaskInput` containing the starting URL, task instructions, task name, and time budget. It checks that subagent control is available, starts a browser-profile child task with the requested work, then waits for that child to finish. If the wait times out or the child is cancelled, it returns an error-style tool result explaining that the task was cancelled. If the child finishes normally, it parses the browser subagent's JSON summary into a `BrowserResult` and returns that summary as text.

**Call relations**: This is the handler behind the `browser_task` tool definition. When the agent calls that tool, this function uses `ToolContext.spawn` to create the browser child turn, waits under `asyncio.timeout`, and packages the final message with `TextContent` and `ToolResult` for the caller.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 124–137)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique non-empty lines. `wide_browse` uses this to turn a user-provided file of URLs or names into the set of browser jobs to run.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for the shell, runs `cat` in the sandbox to read the file, and raises an error if the file cannot be read. It then trims whitespace from each line, skips blank lines, removes duplicates while preserving first-seen order, and returns the resulting list of entities.

**Call relations**: `_wide_browse` calls this first, before starting any browser work. By doing the cleanup here, the batch browser tool can focus on spawning visits and writing results, while this helper handles the file-reading and duplicate-removal step.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 140–167)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs browser automation over many entities from a file and collects the results into a JSON output file. It is useful when the agent needs to perform the same extraction task across many sites or names.

**Data flow**: It receives the tool context and a `WideBrowseInput` containing an entities file, a prompt template, and an optional schema file path. It reads and deduplicates the entities, rejects the request if there are too many, reads the output schema if available, and creates a semaphore, which is a small gate that limits how many browser jobs can run at once. It then runs one `visit` task per entity, gathers all rows, writes them to `wide_browse.json` in the workspace, and returns a tool result containing both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It calls `_read_lines` to prepare the input list, uses its nested `visit` function for each entity, runs those visits together with `asyncio.gather`, and returns the final combined report through `ToolResult`.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 148–161)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser subagent for one entity inside a larger `wide_browse` batch. It builds the per-entity prompt, optionally appends the desired output schema, and records the child agent's answer.

**Data flow**: It receives one entity string from the outer `_wide_browse` loop. Before doing work, it passes through the semaphore so only a limited number of visits run at the same time. It replaces `{entity}` in the prompt template with the current entity, appends schema instructions if a schema was read, spawns a browser-profile subagent for that one task, and returns a dictionary with the entity and the child's JSON output text, or an empty string if there is no output.

**Call relations**: `_wide_browse` creates this inner function and schedules one copy for each entity. Each `visit` is one worker in the batch: it hands one browser job to `ctx.spawn`, then returns a row that `_wide_browse` later gathers, writes to the workspace file, and reports to the caller.


### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup or extension load`

This file is like a job description and equipment list for research helpers. Instead of letting every agent use every possible tool, it creates two named subagent profiles: `research` for ordinary scoped research work, and `deep_research` for larger tasks that may need many steps and sources.

Both profiles use the same research tool kit. That includes web search and page fetching tools, a browser task tool, external tool access, file tools, memory search, and spreadsheet support. The important boundary is that these agents get research-oriented tools, not unrestricted control of every browsing surface. This keeps delegated research focused and safer.

The file also loads two prompt files from disk. These prompts are the written instructions that tell each subagent how to behave. The deep research version gets a much higher round limit, meaning it can spend more back-and-forth reasoning/tool-use cycles before stopping.

Finally, the file defines simple input and output models. A research subagent receives an `objective`, which is the task to accomplish, and returns a `result`, which is its completed answer. Without this file, the rest of the system would not have a ready-made, consistent way to spin up these research workers with the right instructions, tools, limits, and data contract.


### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file is a small bridge between the main conversation and a dedicated website-building subagent. Think of it like a work order form plus a dispatch button: the main agent writes down exactly what kind of site is needed, then sends that request to a specialist who does the build work in the same workspace.

The key input type is `BuildWebsiteInput`. It asks for a self-contained `objective`, because the child agent does not inherit the main conversation history. It can also include a friendly task name, a list of skills to preload, and an option to allow a longer work session for bigger builds.

The tool definition, `DELEGATION_TOOLS`, exposes this as `build_website`. It is marked as side-effecting, meaning it can change the workspace by creating files, starting or registering a site, or otherwise producing lasting results. To make retries safer, the actual spawn call uses the current tool call's idempotency key. In plain terms, if the system crashes and repeats the same dispatch step, it tries to reconnect to the same child build instead of accidentally starting a duplicate job.

The important boundary is that the child agent gets its own conversation, but not its own separate filesystem. Anything it builds remains available in this conversation's sandbox afterward.

#### Function details

##### `_build_website`  (lines 54–61)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the actual worker behind the `build_website` tool. It starts the specialized website-building child agent, passes along the user's build request, then returns the child agent's summary as the tool result.

**Data flow**: It receives a tool context, which includes the ability to spawn child agents and an idempotency key, plus a `BuildWebsiteInput` object containing the website objective and options. It converts that input into a plain data payload, leaving out empty fields, then asks the context to spawn the website-building profile with that payload. When the child finishes, it takes the child's output, turns it into JSON text if there is any output, wraps that text in `TextContent`, and returns it inside a `ToolResult`.

**Call relations**: This function is registered as the handler for the `build_website` tool, so it runs when the main agent chooses to delegate a web build. Its main handoff is to `ToolContext.spawn`, which creates or reconnects to the website-building child agent. After that child returns, `_build_website` packages the result into the standard tool-response shape using `TextContent` and `ToolResult`.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).


### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `when a website-building subagent is configured or spawned`

This file is like a job description and toolbox list for a temporary website-building helper. When the main assistant needs a site built, it can start this subagent with a clear task. The subagent then works inside the same project files, using only the tools it is allowed to use.

The file loads a website-building prompt from a nearby Markdown file. That prompt contains the detailed working instructions. It also names the tools the subagent may use: basic file tools for reading and editing, build and local website tools, JavaScript and spreadsheet REPL tools for testing or inspecting behavior, and optional web research tools if they are available.

A key detail is what the subagent is not allowed to do. It does not get `publish_website`, because publishing a live app with a backend is reserved for the parent assistant that is directly talking to the user. It also does not get `share_file`, because the child agent has no direct user to deliver files to. Instead, it leaves its work in the shared workspace, and the parent reads the files afterward.

The two small data models, `WebsiteBuildingTask` and `WebsiteBuildingResult`, define the shape of the message sent into the subagent and the summary returned from it. Finally, `WEBSITE_BUILDING_PROFILE` bundles all of this into one profile the system can register and run.


### Self-improvement evaluation
Corpus construction, replay evaluation, and statistical gatekeeping decide whether proposed prompt changes are safe improvements.

### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

This file answers a practical question: “Which past failures should the system learn from, and how do we test whether a proposed improvement really helps?” A trajectory is a recorded conversation, including user messages, assistant tool calls, and tool results. Since the system does not have a separate channel where it says “I struggled here,” this code treats tool errors as the visible sign of friction.

The file defines two small data shapes. A TaskExample is one useful failed conversation, reduced to the user’s original request, the full message history needed to replay it, and a plain description of the tool error. A TaskClass is a group of those examples for one kind of failure, named after the tool that errored, such as “tool:search”.

The flow is like sorting broken workshop jobs by which machine broke. First it finds the first user request in a conversation. Then it finds the first tool result marked as an error and connects it back to the tool call it answered. If both exist, the trajectory becomes a training/evaluation example. Finally, examples are grouped by failing tool and split into two sets: a “mine” set, used to inspire a proposed fix, and a “held_out” set, used later to test the fix on different conversations. That split matters because grading on the same examples used to invent a fix would make success look too easy.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. This gives the later evaluation a clear question or task to judge against.

**Data flow**: It receives the full tuple of conversation messages. It scans from the beginning until it finds a message from the user whose content is non-empty plain text. It returns that text, or returns nothing if no suitable user request exists.

**Call relations**: bad_trajectory calls this while deciding whether a recorded conversation is usable. If there is no clear user request, bad_trajectory rejects the conversation because there would be no stable task to evaluate.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first tool failure in a conversation and identifies which tool failed. This is the main signal the self-improvement loop uses to decide what kind of problem the conversation represents.

**Data flow**: It receives the full tuple of messages. First it scans tool-use blocks and remembers which tool name belongs to each tool-call id. Then it scans tool-result blocks looking for the first one marked as an error. When it finds one, it uses the stored id-to-name map to return the tool name and the error text. If no matching error is found, it returns nothing.

**Call relations**: bad_trajectory calls this before creating a TaskExample. The tool name it returns becomes the class label, so failures from the same tool are grouped together later by task_classes.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Turns one recorded conversation into a self-improvement example if it contains both a user request and a tool error. It filters out conversations that do not provide enough evidence to learn from or test against.

**Data flow**: It receives a Trajectory, which includes a conversation id and all messages. It asks first_tool_error for the earliest tool failure and first_request for the first user request. If either is missing, it returns nothing. If both are present, it builds a TaskExample containing the conversation id, request, full messages, and a readable problem statement, then returns it together with a class name like “tool:<name>”.

**Call relations**: task_classes calls this for every trajectory it is given. bad_trajectory is the bridge between raw conversation logs and the cleaner examples that the rest of the corpus-building code can group and split.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many past conversations. Each class represents failures from one specific tool and contains separate examples for proposing improvements and for later evaluation.

**Data flow**: It receives a tuple of trajectories. For each one, it asks bad_trajectory whether the conversation is a usable failure example. Usable examples are collected under their failure class name. Each group is then passed to _split, which either turns it into a TaskClass or rejects it if the group is too small. The function returns the surviving classes sorted so larger classes come first, with names used as a tie-breaker.

**Call relations**: This is the top-level function in the file. Other parts of the self-improvement system would call it when they need a prepared corpus. It delegates individual conversation filtering to bad_trajectory and delegates the mine-versus-held-out division to _split.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Divides one group of examples into a proposer set and a held-out evaluation set. This keeps the system from judging a candidate improvement only on the same examples that inspired it.

**Data flow**: It receives a class name and all examples for that class. If there are not enough examples to make both required sets, it returns nothing. Otherwise, it sorts examples by conversation id for a stable, repeatable order, chooses an evaluation count, and returns a TaskClass whose held_out examples come first in that order and whose mine examples are the remaining ones.

**Call relations**: task_classes calls this after grouping failures by tool. _split is the final gate: it decides whether a tool-failure class has enough material to be useful and packages accepted groups into TaskClass objects for the rest of the self-improvement loop.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation and gating`

This file is the evidence-gathering part of the self-improvement system. A new prompt should not be trusted just because it sounds better. The system needs a fair comparison, like giving two students the same exam and grading them with the same answer key. Here, the two “students” are the current prompt and the candidate prompt.

`CandidateEvaluation` takes a replay model, which can rerun saved task conversations, and a judge model, which decides whether an answer satisfies the original user request. For each held-out task, it replays the task twice: once with the current prompt and once with the candidate prompt. The replay uses the same saved task setup so the prompt is intended to be the only meaningful difference. Each regenerated answer is then sent to the judge, which must return a small JSON result saying whether the answer was accepted.

The results are turned into simple success-or-failure labels. Local held-out tasks measure whether the candidate improved on the kind of task it was designed for. Global held-out tasks check that it did not make other task types worse. Finally, the file passes both sets of labels to the two-stage gate, which makes the accept-or-reject decision using those comparisons.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the main entry point for judging a candidate prompt. It compares the candidate prompt against the current prompt on local test tasks and optional broader test tasks, then returns the gate’s final verdict.

**Data flow**: It receives the candidate prompt, the current prompt, and two groups of saved task examples. It asks `_labels` to turn each group into pass-or-fail results for both prompts. It then gives those two result sets to the gate, which returns whether the candidate should pass.

**Call relations**: When the self-improvement system needs to decide if a prompt change is good enough, it calls this method. This method delegates the repeated replay-and-grade work to `_labels`, then hands the collected evidence to `two_stage_gate` so the final decision is made in one consistent place.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This helper produces the raw comparison evidence for a group of held-out tasks. For every task, it tests both the current prompt and the candidate prompt, then records whether each answer was accepted.

**Data flow**: It receives both prompts and a tuple of saved task examples. For each example, it creates a replay evaluator, reruns the example with the current prompt and then the candidate prompt, sends each final answer to `_accepts`, and stores an `OutcomeLabel` showing which prompt was used and whether it succeeded. It returns all labels as an immutable tuple.

**Call relations**: `evaluate` calls this once for local tasks and once for global tasks. Inside the loop, `_labels` relies on `ReplayEvaluation` to regenerate answers from saved examples and on `_accepts` to judge each answer, then packages the outcomes for the gate logic.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This helper asks the judge model whether one answer correctly satisfies one user request. It is deliberately strict: if the judge does not return valid JSON with `accepted` set to true, the answer is treated as not accepted.

**Data flow**: It receives the original request text and the answer produced by replay. It builds a user message containing both, sends it with grading instructions to the judge model, searches the judge’s reply for a JSON object, parses it, and returns true only when that object says `accepted` is exactly true. Bad formatting, missing JSON, or invalid JSON all become false.

**Call relations**: `_labels` calls this after each replayed answer is produced. This function is the bridge between raw generated text and the simple success-or-failure labels that the evaluation gate can count and compare.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation`

This file is the “promotion gate” for self-improvement. When the system tests a candidate prompt, it compares two groups of replayed examples: cases where the candidate prompt was present and cases where it was absent. The goal is not just to ask, “Did the candidate win more often?” but “Are we confident enough that the win is real?”

It does this by counting accepted answers in each group, then estimating a safe lower bound for the improvement in acceptance rate. A lower bound means the pessimistic end of the estimate: if even that is high enough, the candidate probably helped. The file uses Wilson confidence bounds, a statistical method for estimating a success rate when there may be only a few examples. Think of it like checking whether a small product sample is convincing enough before changing the whole assembly line.

There are two checks. First, the local gate asks whether the candidate improves the target task class enough, with enough examples on both sides. Second, the global check makes sure the change does not clearly hurt other task classes. Importantly, the global check only blocks when there is strong evidence of harm; weak or incomplete evidence is allowed through. The final result is a GateVerdict saying whether the candidate passed, why, and how much evidence was used.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This estimates a cautious lower success rate for a set of accepted-versus-total results. Someone would use it when they want to avoid over-trusting a small sample, such as treating 1 success out of 1 as definitely perfect.

**Data flow**: It takes the number accepted, the total number tested, and an optional confidence setting. If there are no examples, it returns 0. Otherwise it computes a Wilson lower confidence bound and returns a number between 0 and 1, representing the pessimistic plausible success rate.

**Call relations**: This is a building block for the lift calculations. When the file needs the cautious side of the candidate’s success rate, or the cautious side of the baseline’s success rate, the lift functions call this helper before combining the uncertainty from both groups.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This estimates a generous upper success rate for accepted-versus-total results. It is used when the code needs to know the optimistic plausible end of a success rate, not just the observed rate.

**Data flow**: It takes accepted and total counts plus an optional confidence setting. If there are no examples, it returns 1, meaning the rate is completely unconstrained. Otherwise it computes a Wilson upper confidence bound and returns a number between 0 and 1.

**Call relations**: This pairs with wilson_lower_bound. The lift functions use it to build a safe interval around the difference between candidate and baseline acceptance rates.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This calculates the cautious lower estimate of how much better the candidate prompt is than the current one. It answers: even after accounting for uncertainty, how much improvement can we safely claim?

**Data flow**: It receives a Contingency object containing accepted and total counts for the candidate-present group and candidate-absent group. If either group has no examples, it returns 0. Otherwise it compares their observed acceptance rates, subtracts an uncertainty penalty built from Wilson bounds, and returns the lower bound of the improvement.

**Call relations**: score_gate calls this after the replay labels have been counted. It relies on the Wilson bound helpers to avoid giving a candidate too much credit for a lucky small sample.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This calculates the optimistic upper estimate of the candidate’s lift over the baseline. It is mainly used to decide whether there is clear evidence that the candidate made other tasks worse.

**Data flow**: It receives counted results for the candidate-present and candidate-absent groups. If either side has no examples, it returns 0. Otherwise it compares the observed rates, adds an uncertainty allowance, and returns the upper end of the possible improvement interval.

**Call relations**: global_non_inferior calls this during the safety check for other task classes. If even this optimistic estimate is still too negative, the candidate is treated as a real regression.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This turns individual replay results into the four counts needed for the statistical checks. It separates examples where the candidate prompt was present from those where it was absent, then counts successes in each group.

**Data flow**: It takes a tuple of OutcomeLabel records. Each label says whether the candidate was present and whether the answer was accepted. It produces a Contingency object with accepted and total counts for both the present and absent groups.

**Call relations**: Both score_gate and global_non_inferior use this first, because the later math works on counts rather than individual replay records. It is the small counting step before the confidence calculations begin.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This makes the local promotion decision for the task class being improved. It checks that there are enough replay examples on both sides and that the candidate’s cautious improvement clears the required floor.

**Data flow**: It takes replay labels, plus optional thresholds for the required improvement and minimum examples per group. It counts the labels, computes the lower bound on acceptance lift, and returns a GateVerdict. The verdict says pass or fail, gives a human-readable reason, and records the evidence size.

**Call relations**: two_stage_gate calls this first. If this local check fails, the full promotion process stops immediately, because there is no reason to test broader safety unless the candidate first proves a local win.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This checks whether the candidate avoids clearly harming other task classes. It is deliberately forgiving when evidence is thin, and only rejects when the data strongly suggests a meaningful regression.

**Data flow**: It takes replay labels for the broader held-out tasks, plus optional margin and sample-size settings. It counts the labels. If either group has too few examples, it returns true. Otherwise it computes the optimistic upper bound of lift and returns whether that is not worse than the allowed negative margin.

**Call relations**: two_stage_gate calls this only after the local gate has passed. It uses lift_upper_bound because the question is not “did the candidate definitely improve globally?” but “can we rule out serious harm?”

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This gives the final promote-or-reject decision for a candidate prompt. It requires both a strong local improvement and no clear global regression.

**Data flow**: It takes two sets of replay labels: one for the task class being improved and one for other task classes. It first asks score_gate for the local verdict. If that fails, it returns that failure. If the local check passes, it runs the global safety check. A global failure becomes a new failing GateVerdict; otherwise it returns the successful local verdict.

**Call relations**: This is the top-level decision point in this file. It ties together the local improvement test and the wider safety test, so a candidate prompt is promoted only when both parts of the story look acceptable.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).

## 📊 State Registers Touched

- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-live-update-stream` — The temporary live feed of progress messages that open clients and other server processes can follow.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-tool-catalog` — The current list of tools the agent may call, with their names, inputs, permissions, and implementations.
- `reg-skill-store` — The shared library of built-in and user-created skills that can be selected, checked, and loaded into a turn.
- `reg-model-catalog` — The shared list of available AI models, their abilities, prices, limits, and required credentials.
- `reg-sandbox-handles` — The remembered execution workspaces, browser workbenches, terminal sessions, and sandbox IDs used across a conversation or turn.
- `reg-subagent-objectives` — The shared plan and delegation state for child agents, objectives, steps, evidence, attempts, and result delivery.
- `reg-proposal-state` — Durable proposed-change records, including pending, approved, or rejected prompt/config/self-improvement proposals and their before/after payloads.
- `reg-evaluation-run-store` — Durable evaluation test cases, replay runs, comparison results, and self-improvement validation state used to accept or reject changes.
