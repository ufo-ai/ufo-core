# Self-improvement and objective maintenance  `stage-16.2`

This stage is shared behind-the-scenes support for two long-running jobs: keeping objectives honest, and helping the system improve its own prompts with human approval. The objectives package marker lets the rest of the project import this extension cleanly. Its store is the notebook: it records objectives, steps, attempts, blockers, and checks so later turns know the real history. Its tools are the controls: they let users plan, inspect, delegate, and mark progress, but they verify evidence against actual state before accepting that a step is done.

The self-improvement side is like a careful test kitchen. The model wrapper gives all parts one simple way to ask the language model for text. The corpus builder finds past failed tool uses and turns them into training and test examples. The proposer asks for a better prompt based on real failures. Replay reruns old conversations with a new prompt while keeping tool results fixed. Evaluation compares old and new answers, using a judge model. The gate checks whether the change truly helps without obvious harm. Cron runs this loop on a schedule and opens only tested proposals for humans to approve.

## Files in this stage

### Objective tracking and verification
Package setup, durable objective storage, and user-facing tools for planning, delegation, progress checks, and evidence-backed completion.

### `extensions/objectives/ufo_ext_objectives/__init__.py`

`other` · `startup/import time`

This is the package entry file for the objectives extension. In Python, an `__init__.py` file tells Python that a folder should be treated as an importable package, like putting a label on a drawer so other code knows what is inside. This particular file does not define any functions, classes, or setup behavior. It only contains a brief documentation string: “The objectives extension.” Without this file, depending on the Python version and how the project loads extensions, other parts of the system might have trouble recognizing or importing this folder as the objectives extension package. Its main value is structural: it gives the extension a clear package boundary and a minimal description.


### `extensions/objectives/ufo_ext_objectives/store.py`

`domain_logic` · `cross-cutting`

This file is the memory and rulebook for objective tracking. An objective is a named goal inside a conversation. It has ordered steps, and each step may declare acceptance conditions, such as “this file exists,” “this file contains this text,” or “this command succeeds.” The important idea is that workers can say they tried something, but the extension decides whether the step is actually done by checking those conditions.

The file uses database tables for objectives, steps, events, and checks. Events are append-only records such as “did” or “blocked.” They are not edited later. That matters because a revised plan should not erase what already happened. Checks are also appended, so the system can remember what the extension observed at a particular time.

The view classes, such as StepView and ObjectiveView, turn raw database rows into useful plain objects. They derive states like pending, attempted, done, blocked, and unmet from the recorded facts. The Objectives class is the main doorway for reading and writing: it can find an objective, create or revise a plan, record an attempt or block, save check results, and rebuild a full objective view from the database. A key safeguard is that once a step has been attempted, its acceptance conditions are frozen, so a worker cannot loosen the definition of success after the work becomes hard.

#### Function details

##### `StepView.attempted`  (lines 157–158)

```
def attempted(self) -> bool
```

**Purpose**: This property answers the simple question: has anyone recorded that they did work on this step? It looks only for a “did” event, not for whether the work succeeded.

**Data flow**: It reads the step’s stored event list → checks whether any event has the kind “did” → returns true if at least one attempt exists, otherwise false. It does not change anything.

**Call relations**: Other step-state decisions lean on this answer. StepView.state uses it to avoid calling an untouched step done or unmet, and ObjectiveView.runnable uses it to avoid dispatching independent work that has already been tried.


##### `StepView.open_block`  (lines 161–166)

```
def open_block(self) -> StepEvent | None
```

**Purpose**: This property finds the current unanswered block, if the latest event says the step is blocked. It prevents the system from asking the same blocking question again and again.

**Data flow**: It reads the step’s event list → looks at only the newest event → returns that event if it is a “blocked” event, or returns nothing if the latest event is not a block. It does not edit the event history.

**Call relations**: StepView.state uses this same idea when deciding whether the step is currently blocked. ObjectiveView.runnable and Objectives.record also rely on it: runnable steps should not include a step with an open block, and record avoids writing a duplicate block when the same question is already standing.


##### `StepView.state`  (lines 169–184)

```
def state(self) -> str
```

**Purpose**: This property turns the raw history of a step into a human-meaningful state: pending, attempted, done, blocked, or unmet. It is the main rule that says when a step really counts as closed.

**Data flow**: It reads the step’s events, acceptance conditions, and latest verdicts → applies the ordering of rules: a latest block means blocked; no attempt means pending; no conditions after an attempt means done; missing or incomplete checks means attempted; complete checks decide done versus unmet → returns the state name as text. It does not save anything.

**Call relations**: This is the central interpretation used by ObjectiveView.confirmed and ObjectiveView.frontier. It also depends on StepView.attempted so a condition with no check is not accidentally treated as successful.


##### `ObjectiveView.attempts`  (lines 199–200)

```
def attempts(self) -> int
```

**Purpose**: This property counts how many recorded “did” attempts exist across all steps in the objective. It gives a quick sense of how much effort has been spent.

**Data flow**: It reads every step in the objective → scans every event on those steps → counts events whose kind is “did” → returns that number. It does not modify the objective.

**Call relations**: Code that receives an ObjectiveView can use this as a progress signal. It pairs with ObjectiveView.confirmed: many attempts with few confirmed steps suggests the objective may be stuck or poorly split.


##### `ObjectiveView.confirmed`  (lines 203–204)

```
def confirmed(self) -> int
```

**Purpose**: This property counts how many steps are truly done according to the extension’s state rules. It is not just a count of worker claims; it uses StepView.state.

**Data flow**: It reads all steps → asks each step for its state → counts only those whose state is “done” → returns the count. It does not change stored records.

**Call relations**: It depends on StepView.state for the real done/not-done decision. Together with ObjectiveView.attempts, it helps readers understand whether work is converting into confirmed progress.


##### `ObjectiveView.runnable`  (lines 207–215)

```
def runnable(self) -> tuple[StepView, ...]
```

**Purpose**: This property picks out the steps that may be run in parallel right now. A step qualifies only if it is in the current frontier, marked independent, not yet attempted, and not waiting on a block.

**Data flow**: It starts with the objective’s frontier steps → filters to steps declared independent → removes steps already attempted → removes steps with an open block → returns the remaining steps as an ordered tuple. It does not start the work itself.

**Call relations**: It builds on ObjectiveView.frontier, StepView.attempted, and StepView.open_block. A dispatcher can use this set when deciding which independent steps to fan out to workers at the same time.


##### `ObjectiveView.frontier`  (lines 218–219)

```
def frontier(self) -> tuple[StepView, ...]
```

**Purpose**: This property returns the unfinished part of the objective. It is the list of steps that still need attention because they are not in the done state.

**Data flow**: It reads all steps → asks each step for its state → keeps only steps whose state is not “done” → returns them in their stored order. It does not change anything.

**Call relations**: ObjectiveView.runnable narrows this frontier further to find independent work that can be dispatched now. Any code showing progress or choosing next work can use this as the current open edge of the plan.


##### `condition_summary`  (lines 222–229)

```
def condition_summary(condition: Condition) -> str
```

**Purpose**: This function turns a machine-readable condition into a short sentence a person can understand. It is useful for displaying or explaining what must be true for a step to close.

**Data flow**: It receives one condition object → checks which kind it is: file exists, file contains text, or command succeeds → returns a readable text summary. It does not read the database or change the condition.

**Call relations**: It sits beside the condition data models as a presentation helper. Other parts of the extension can call it when they need to show acceptance conditions without exposing the raw stored shape.


##### `Objectives.named`  (lines 239–253)

```
async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None
```

**Purpose**: This method finds one objective by conversation and name inside the current workspace. It prevents one conversation, such as a subagent conversation, from accidentally taking over another conversation’s objective just because the names match.

**Data flow**: It receives a conversation ID and objective name → queries the objective table for a row with the current workspace, that conversation, and that name → if no row is found, returns nothing; if found, asks Objectives._view to build the full objective with steps, events, and checks. It only reads from the database.

**Call relations**: Objectives.plan calls this first to decide whether it is creating a new objective or revising an existing one. When a row is found, this method hands off to Objectives._view, which does the heavier work of assembling the full view.

*Call graph*: calls 1 internal fn (_view); called by 1 (plan); 1 external calls (select).


##### `Objectives.on_conversation`  (lines 255–267)

```
async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None
```

**Purpose**: This method returns the most recently created objective for a conversation in the current workspace. It is a convenient way to ask, “what objective is this conversation currently associated with?”

**Data flow**: It receives a conversation ID → queries the objective table for matching rows in the current workspace → orders them newest first and takes one → returns nothing if there is no objective, or calls Objectives._view to return the full objective view. It only reads from the database.

**Call relations**: Like Objectives.named, it uses Objectives._view to turn a database row into the richer in-memory form. It is used when the caller knows the conversation but not necessarily the objective name.

*Call graph*: calls 1 internal fn (_view); 1 external calls (select).


##### `Objectives.plan`  (lines 269–332)

```
async def plan(self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]) -> ObjectiveView
```

**Purpose**: This method creates a new objective plan or revises an existing one. Its most important protection is that acceptance conditions for steps already attempted are kept frozen, so success cannot be redefined after the fact.

**Data flow**: It receives a conversation ID, name, directive, and planned steps → looks for an existing objective with Objectives.named → inserts a new objective if none exists, or updates the directive if one exists → preserves acceptance conditions for attempted steps → removes unstarted old steps that are no longer in the plan → updates or inserts each planned step with its position, conditions, and independence flag → reads the finished objective back and returns it. It changes the objective and step tables.

**Call relations**: This is the main write path for planning. It calls Objectives.named both to find the previous plan and to return the final view, and it uses database insert, update, and delete operations to make the stored plan match the new plan without erasing meaningful history.

*Call graph*: calls 1 internal fn (named); 5 external calls (delete, insert, true, update, uuid4).


##### `Objectives.record`  (lines 334–354)

```
async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool
```

**Purpose**: This method appends a step event, such as an attempt or a block. It refuses to write the same standing block twice, which avoids repeatedly posting the same unanswered question.

**Data flow**: It receives a StepView, an event kind, the actor turn ID, and evidence text → trims the evidence to the maximum stored length → checks whether this is a duplicate of the currently open block → if it is a duplicate, returns false without writing; otherwise inserts a new event row and returns true. It changes the event table only when a new event is actually recorded.

**Call relations**: It uses StepView.open_block to detect whether the latest event is already the same block. Other code can call this after a worker reports work or blockage, and the returned true/false tells whether the durable history grew.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives.checked`  (lines 356–383)

```
async def checked(self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID) -> None
```

**Purpose**: This method records what the extension found when it evaluated a step’s acceptance conditions. It saves observations, not worker claims, and it appends a new check instead of overwriting old ones.

**Data flow**: It receives a StepView, condition verdicts, and the actor turn ID → converts each verdict into plain database-friendly data, including the original condition, whether it held, and a detail message → inserts a new check row with a fresh ID and timestamp. It writes to the check table and returns nothing.

**Call relations**: This is called after the extension has actually checked the live conditions for a step. Later, Objectives._view reads the latest check back so StepView.state can decide whether attempted conditional work is done, unmet, or still only attempted.

*Call graph*: 2 external calls (insert, uuid4).


##### `Objectives._view`  (lines 385–436)

```
async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView
```

**Purpose**: This private helper rebuilds a complete ObjectiveView from database rows. It gathers the objective’s steps, their event history, and their latest condition check into one object that the rest of the extension can reason about.

**Data flow**: It receives one objective database row → queries all steps for that objective in position order → queries all events for those steps in time order → queries all checks for those steps in time order → groups events by step and keeps the latest check per step → parses stored condition JSON into condition objects and stored verdict JSON into verdict objects → returns an ObjectiveView containing StepView objects. It reads from the database but does not write.

**Call relations**: Objectives.named and Objectives.on_conversation call this whenever they find an objective row. It delegates JSON parsing to _conditions and _verdicts, and it constructs StepEvent, StepView, and ObjectiveView objects as the final readable form.

*Call graph*: calls 2 internal fn (_conditions, _verdicts); called by 2 (named, on_conversation); 4 external calls (__init__, __init__, __init__, select).


##### `_conditions`  (lines 439–453)

```
def _conditions(payload: object) -> tuple[Condition, ...]
```

**Purpose**: This helper turns stored condition data back into the proper condition objects. It is the bridge from plain JSON-like database data to validated Python objects.

**Data flow**: It receives an unknown payload → if the payload is not a list, returns an empty tuple → for each list item, looks at its kind field → validates it as FileExists, FileContains, or CommandSucceeds → returns the parsed conditions as a tuple, or raises an error if a condition kind is unknown.

**Call relations**: Objectives._view calls this when rebuilding each step’s acceptance conditions. _verdicts also calls it to parse the condition embedded inside each stored verdict.

*Call graph*: called by 2 (_view, _verdicts).


##### `_verdicts`  (lines 456–467)

```
def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]
```

**Purpose**: This helper turns stored check results back into ConditionVerdict objects. A verdict says which condition was checked, whether it held, and the detail text from the check.

**Data flow**: It receives an unknown payload → if the payload is not a list, returns an empty tuple → for each dictionary item, parses its embedded condition with _conditions, converts the holds value to true or false, converts detail to text, and builds a ConditionVerdict → returns all parsed verdicts as a tuple. It does not write anything.

**Call relations**: Objectives._view calls this when attaching the latest saved check to each StepView. It depends on _conditions so verdicts use the same condition parsing rules as step plans.

*Call graph*: calls 1 internal fn (_conditions); called by 1 (_view); 1 external calls (__init__).


### `extensions/objectives/ufo_ext_objectives/tools.py`

`domain_logic` · `request handling`

This file is the working surface of the objectives extension. An objective is a named piece of work with ordered steps, and each step can name conditions that prove it is done, such as “this file exists,” “this file contains this text,” or “this command succeeds.” Without this file, agents could write down plans, but they would not have a reliable way to create them, update them, verify them, or hand independent parts to subagents.

The central idea is simple: saying “I did it” is not the same as proving “it is true.” When a step is recorded as done, the file re-checks that step’s acceptance conditions in the sandbox, which is the controlled environment where commands can be run. If the checks fail, the result tells the worker which conditions are still unmet.

It also protects against empty proof. For example, if a step says it will create a file, but that file already exists when the plan is made, the condition is refused because it would prove nothing. This is like checking off “bake a cake” because a cake was already on the table before you started.

Finally, the file exposes these abilities as tool definitions: plan an objective, record a step, run independent steps in parallel through subagents, and read the current objective state.

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


### Self-improvement foundations
Shared model access and corpus construction provide the examples and language-model doorway used by the prompt-improvement loop.

### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `cross-cutting model calls`

The self-improvement extension needs to call a language model in a few different ways: sometimes it wants plain text back, and sometimes it wants a full model “turn” that may use tools. This file keeps those calls uniform and controlled. Think of it like a ticket counter: the rest of the extension says what it wants, and this file fills out the proper request form before handing it to the SDK.

It defines two small protocols, which are like promises about what an object can do. `ModelLeg` promises a `complete` method for getting a text answer. `ReplayLeg` promises a `turn` method for getting a structured model message, including optional tool use. These let other code depend on a simple shape rather than a particular implementation.

`ModelAccessLeg` is the real adapter. It wraps the SDK’s `ModelAccess`, builds a `ModelRequest`, and sets shared limits and options in one place: a maximum output size, a short conversation cache lifetime, and reasoning turned off. This matters because it makes model use predictable and metered consistently. Without this file, each caller might create requests differently, leading to uneven cost, behavior, or replay results.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This is the promised shape for anything that can ask the model for a plain text completion. Code can rely on this method without caring which concrete model adapter is behind it.

**Data flow**: It receives a system instruction and a sequence of prior messages. An implementation is expected to send those to a model and return the model’s text response.

**Call relations**: This method is an interface point, not working code by itself. Other self-improvement pieces can be written against `ModelLeg`, and `ModelAccessLeg.complete` provides the concrete version that actually sends the request.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This is the promised shape for anything that can ask the model to take one full conversational turn, possibly with tools available. It is useful when replay code needs the model’s response as a structured message rather than just text.

**Data flow**: It receives a system instruction, the conversation so far, and the tool descriptions the model may use. An implementation is expected to return the next model message.

**Call relations**: This method is an interface point. Replay-style code can depend on `ReplayLeg`, while `ModelAccessLeg.turn` supplies the real behavior by building and sending the SDK request.


##### `ModelAccessLeg.complete`  (lines 28–38)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a plain completion request to the SDK-backed model and returns only the text answer. It centralizes common request settings so callers do not repeat them or accidentally choose different limits.

**Data flow**: It takes the system instruction and message history, combines them with the configured model name, token limit, cache lifetime, and reasoning setting, and creates a `ModelRequest`. It sends that request through `self.model.complete` and returns the resulting text.

**Call relations**: When self-improvement code needs a text-only model answer through the `ModelLeg` interface, this is the concrete path. It hands the request setup to `ModelRequest.__init__`, then passes the finished request to the SDK model access object.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 40–53)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This sends a full model-turn request to the SDK-backed model, including any tools the model is allowed to call. It returns the model’s structured message so replay logic can inspect or continue the conversation.

**Data flow**: It takes the system instruction, message history, and tool schemas, then packages them with the configured model name, token limit, cache lifetime, and reasoning setting into a `ModelRequest`. It sends that request through `self.model.turn` and returns the resulting message.

**Call relations**: When replay code needs behavior promised by the `ReplayLeg` interface, this method provides the real SDK-backed implementation. It uses `ModelRequest.__init__` to build the request, then delegates the actual model call to the wrapped `ModelAccess` object.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

The self-improvement system needs real examples of where it struggled. This file treats a tool error in a conversation as a useful signal: it means the assistant hit friction that is visible in the transcript. From each such conversation, it keeps the user's original request, the full message history, and the text of the tool error.

The main idea is like sorting customer complaints by which machine part broke. If the file-search tool failed in several conversations, those examples become one task class named for that tool. The system can then study some of those examples while keeping others aside for a fair replay test. That matters because a proposed improvement should not be graded only on the same cases that inspired it.

Two small data shapes carry the result. `TaskExample` is one failed conversation reduced to the useful facts. `TaskClass` is a named bucket of examples, split into `mine` examples for proposing improvements and `held_out` examples for evaluation. The helper functions find the first user request, find the first tool error, turn one trajectory into a flagged example, and finally build sorted task classes from many trajectories. Classes that are too small are ignored, because they cannot provide both learning and test examples.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first real user request in a conversation. This gives the self-improvement loop a clear target: what the user was trying to get done before any tool failure happened.

**Data flow**: It receives the conversation messages. It scans them in order until it finds a message from the user whose content is plain, non-empty text. It returns that text, or returns nothing if no suitable user request exists.

**Call relations**: When `bad_trajectory` is deciding whether a conversation can become a training-and-test example, it calls `first_request` to get the request that future grading should compare against.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first failed tool result in a conversation and identifies which tool caused it. This is how the system decides what kind of problem the conversation represents.

**Data flow**: It receives the conversation messages. First it reads tool-use blocks and remembers which tool-use ID belongs to which tool name. Then it scans again for the first tool-result block marked as an error. If it can match that error result back to a tool use, it returns the tool name and the error text; otherwise it returns nothing.

**Call relations**: When `bad_trajectory` checks whether a trajectory is worth keeping, it asks `first_tool_error` for the concrete failure signal. The returned tool name becomes the class label, and the returned error text becomes part of the problem description.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Turns one conversation trajectory into a useful failed-task example, but only if it has both a user request and a tool error. It labels the example by the tool that failed.

**Data flow**: It receives a `Trajectory`, which includes a conversation ID and messages. It calls `first_tool_error` to find the failing tool and error text, and `first_request` to find the user's original request. If either is missing, it returns nothing. If both exist, it builds a `TaskExample` containing the conversation ID, request, full messages, and a readable problem summary, then returns it together with a class name such as `tool:search`.

**Call relations**: `task_classes` calls `bad_trajectory` for each saved trajectory. `bad_trajectory` is the filter that decides which conversations are useful for self-improvement and packages them before grouping.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many past trajectories. It groups failed conversations by the tool that errored, removes groups that are too small, and returns the usable groups in a stable order.

**Data flow**: It receives a tuple of trajectories. For each one, it calls `bad_trajectory`; conversations without a usable tool error are skipped. The remaining examples are collected by class name. Each class is passed to `_split`, which either divides it into learning and held-out examples or rejects it for being too small. The function returns the accepted `TaskClass` objects, sorted so larger classes come first and names break ties.

**Call relations**: This is the file's main assembly function. It relies on `bad_trajectory` to extract examples and `_split` to make the fair mine-versus-held-out split used later by the self-improvement loop.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Splits one group of failed examples into examples to learn from and examples to hold back for evaluation. It rejects groups that do not have enough examples for both sides.

**Data flow**: It receives a class name and the examples in that class. If there are too few examples, it returns nothing. Otherwise it sorts examples by conversation ID for a repeatable order, chooses an evaluation size of at least one while leaving at least one mining example, and returns a `TaskClass` with the earlier slice as `held_out` and the later slice as `mine`.

**Call relations**: `task_classes` calls `_split` after grouping examples by failing tool. `_split` creates the final `TaskClass` object that downstream self-improvement steps can use without accidentally testing on every example they learned from.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### Candidate prompt generation
The proposer turns real failure examples into a concrete candidate system-prompt change.

### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is part of a self-improvement loop. Its job is to take an agent's current system prompt, look at a group of past problem cases, and ask another model to rewrite the prompt so the agent can do better next time. A system prompt is the instruction text that shapes how an AI agent behaves, like a job description plus operating manual.

The main class, PromptProposer, does one focused thing: it prepares a clear request for the model, sends that request, cleans up the model's answer, and turns it into a PromptCandidate. The request includes the task class name, the current prompt, and a limited number of examples showing what users asked for and what went wrong. These limits keep the model input from becoming too large.

The file is careful not to create useless candidates. If there are no mined examples, it returns nothing. If the model returns an empty answer, or simply repeats the existing prompt, it also returns nothing. That matters because a later approval step would reject a no-op anyway. In everyday terms, this file is like asking an editor to revise an instruction manual after seeing customer complaints, but only keeping the edit if it is actually different.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main entry point for creating a possible improved prompt. It uses examples from a task class where the agent had trouble, asks the model for a revised full prompt, and returns a PromptCandidate only if the suggestion is meaningful.

**Data flow**: It receives the current prompt text and a TaskClass containing the task name and mined examples of failures or friction. If the task class has no examples, it stops immediately. Otherwise it builds a user message with PromptProposer._prompt, sends that plus the proposer system instruction to the model, cleans the returned text with _clean, compares it with the current prompt, and either returns nothing or returns a PromptCandidate containing the task name and revised prompt.

**Call relations**: This function drives the whole proposal step. It calls PromptProposer._prompt to package the evidence for the model, creates a Message to send that evidence, asks the configured ModelLeg for a completion, then calls _clean so the model's response is usable as plain prompt text. If the result passes the basic checks, it hands back a PromptCandidate for later parts of the self-improvement system to evaluate.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This builds the actual text shown to the model as the user request. It explains the task class, includes the current system prompt, and shows selected examples of what went wrong.

**Data flow**: It receives the current prompt and a TaskClass. It takes up to the configured maximum number of mined examples, trims each request and problem to the configured character limit, formats them into a readable list, and returns one combined instruction string asking for the full revised system prompt.

**Call relations**: PromptProposer.propose calls this right before contacting the model. Its output becomes the content of the user Message, so it is the bridge between raw self-improvement evidence and the language model's prompt-rewriting task.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This tidies the model's answer so it can be treated as the revised prompt itself. In particular, it removes surrounding Markdown code fences if the model added them despite being asked not to.

**Data flow**: It receives raw text from the model. It trims whitespace from the ends, checks whether the response starts with a triple-backtick code block marker, removes the opening and closing code-fence lines when present, trims again, and returns the cleaned prompt body.

**Call relations**: PromptProposer.propose calls this after the model returns text. The cleaned result is then checked for being empty or unchanged before it is allowed to become a PromptCandidate.

*Call graph*: called by 1 (propose).


### Replay evaluation and gating
Saved conversations are replayed with candidate prompts, judged for acceptability, and passed through an improvement gate.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation gate`

This file is the evidence-gathering step for self-improvement. A candidate prompt should not be accepted just because it sounds better; it must perform better on real saved tasks. The file compares two “arms” of the same experiment: absent means the current prompt is used, and present means the candidate prompt is used. Each held-out task is replayed under both prompts, so the task is the same and the only intended difference is the prompt text.

The main class, CandidateEvaluation, is given two model-like helpers: one that can replay the agent’s behavior, and one that can judge answers. For every saved example, it runs the replay, takes the final answer, and asks the judge a simple question: does this answer satisfy the original request? The judge is instructed to return only JSON, such as {"accepted": true}. If the judge response is missing valid JSON, the answer is treated as not accepted.

The results become OutcomeLabel records saying which prompt arm was used and whether it succeeded. Local held-out tasks measure whether the candidate improves the task class it was mined for. Global held-out tasks check that it does not make other task classes worse. Finally, two_stage_gate makes the pass/fail decision from those labels.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the top-level check for a candidate prompt. It compares the candidate prompt against the current prompt on local and global held-out examples, then asks the two-stage gate whether the candidate is good enough to accept.

**Data flow**: It receives the candidate prompt, the current prompt, a group of local test examples, and optionally a group of broader global test examples. It turns each group into success/failure labels by calling _labels. It then gives those labels to two_stage_gate, which returns a GateVerdict saying whether the candidate passed and why.

**Call relations**: This method starts the evaluation flow for the file. It relies on _labels to collect the raw experiment results for each prompt arm, then hands those results to two_stage_gate so the final decision is made by the shared gate logic rather than inside this method.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This function runs the side-by-side prompt experiment for a set of saved tasks. For each task, it tries the current prompt and the candidate prompt, judges both answers, and records whether each one succeeded.

**Data flow**: It receives both prompt texts and a tuple of held-out task examples. It creates a ReplayEvaluation helper, then loops through every example twice: once with the current prompt marked as not present, and once with the candidate prompt marked as present. For each replayed answer, it asks _accepts whether the answer satisfies the original request. It returns a tuple of OutcomeLabel objects, each carrying the prompt arm and the success result.

**Call relations**: CandidateEvaluation.evaluate calls this once for local examples and once for global examples. Inside the loop, _labels uses ReplayEvaluation to regenerate an answer and then calls _accepts to judge that answer. Its labels are the raw material that the later gate decision uses.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This function asks the judge model whether one answer correctly satisfies one request. It converts the judge’s JSON response into a plain true-or-false result.

**Data flow**: It receives the original user request and the answer produced by replay. It sends both to the judge with instructions to return only a JSON object containing an accepted field. It then looks for the JSON object in the judge’s text, parses it, and returns true only if the parsed object is a dictionary with accepted set to true. If the judge gives malformed or missing JSON, it returns false.

**Call relations**: _labels calls this after each replay to turn a generated answer into a success or failure. It uses Message to format the judge request and json.loads to read the judge’s response. Its boolean result becomes the success value stored in each OutcomeLabel.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `self-improvement evaluation replay`

This file exists to answer a careful question: “If we had used a different system prompt, would the model have given a better final answer on this archived task?” To make that comparison fair and safe, it replays only the model parts of the conversation. Any tool call the model asks for is answered from the archived record, not by actually calling the tool again. This is like testing a new driver on a simulator track: the scenery and obstacles are the same, so only the driver’s choices change.

The replay starts by removing the old final assistant answer, because that is what the new prompt should regenerate. It also removes saved reasoning blocks, because those belong to the original model run and may be rejected or misleading in a new run. Then it builds a small tool catalog from the tools that appeared in the archive, so the model can ask for the same kinds of tool calls.

During replay, the model gets the swapped system prompt plus the preserved conversation context. If it asks for tools, this file looks up matching archived results by tool name and input. If every requested tool result exists, the replay feeds those results back and continues. If the model asks for a tool call that was not in the archive, the replay stops early and grades whatever answer text has been produced so far. A round limit prevents endless loops.

#### Function details

##### `_canonical_input`  (lines 40–41)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This helper turns a tool input into a stable text key. It makes sure the same input object produces the same lookup string even if dictionary keys appear in a different order.

**Data flow**: It takes any input value, converts it to compact JSON text with sorted keys, and returns that text. The returned string is used as part of a lookup key for archived tool results.

**Call relations**: When archived tool calls are indexed, archived_tool_results uses this to name each call consistently. Later, _feed_archived uses the same conversion on replayed calls so it can find the matching archived result.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 44–60)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This prepares the archived conversation for replay by removing the old final answer. The replay should regenerate that answer under the new prompt, not see the answer it is supposed to replace.

**Data flow**: It receives the full archived message history. It trims trailing assistant messages that do not contain tool calls, then passes each remaining message through _without_reasoning to remove saved reasoning blocks. It returns the cleaned conversation prefix.

**Call relations**: ReplayEvaluation.replay calls this near the start, after collecting archived tool information. The cleaned messages become the starting context sent to the model during replay.

*Call graph*: calls 1 internal fn (_without_reasoning); called by 1 (replay).


##### `_without_reasoning`  (lines 63–71)

```
def _without_reasoning(message: Message) -> Message
```

**Purpose**: This removes model reasoning blocks from a message while leaving ordinary text and tool-related content intact. This matters because saved reasoning can be tied to the original model/provider and is not safe to replay as fresh input.

**Data flow**: It takes one message. If the message content is plain text, it returns it unchanged. If the content is made of blocks, it filters out thinking or reasoning blocks and returns a new message with the remaining blocks.

**Call relations**: replay_head calls this for every message it keeps. It is a cleanup step before ReplayEvaluation.replay sends the archived context to the replay model.

*Call graph*: called by 1 (replay_head); 1 external calls (__init__).


##### `archived_tool_results`  (lines 74–96)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This builds a lookup table of tool results from the archived conversation. It lets the replay answer tool calls with the exact old results instead of executing tools again.

**Data flow**: It reads all messages from the archive. First it records tool result blocks by their tool-use id. Then it finds each tool-use block, pairs it with its recorded result, and stores that result under a key made from the tool name and canonicalized input. It returns that lookup table.

**Call relations**: ReplayEvaluation.replay calls this before the replay loop begins. _feed_archived later uses the produced table to answer tool calls requested by the replayed model.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 99–116)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This creates the small list of tools the replay model is allowed to call. The list is based only on tools that the archived run actually used.

**Data flow**: It scans the archived messages for tool-use blocks, remembers each distinct tool name in first-seen order, and creates a permissive tool schema for each one. The schema says the tool accepts an object with any properties, because the archived conversation itself shows the expected shape.

**Call relations**: ReplayEvaluation.replay calls this during setup. The resulting tool schemas are passed into the model turn so the model can reproduce archived tool calls if it chooses.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 119–135)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This answers the replay model’s requested tool calls using archived results. If the replay asks for something not found in the archive, it reports that the replay has diverged by returning nothing.

**Data flow**: It takes the tool calls from the current replay turn and the archived result lookup table. For each call, it builds the same lookup key from tool name and input. If all calls are found, it creates a user message containing tool-result blocks with the replay’s current tool-use ids but the archived contents. If any call is missing, it returns None.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks for tools. A real tool is never called; this function either supplies archived answers so the replay can continue, or signals that the replay should stop early.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 148–169)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay flow for one archived task and one candidate system prompt. It produces the final answer text that the model gives under that prompt, while keeping tool results fixed from the archive.

**Data flow**: It takes archived messages and a system prompt. It builds the archived tool-result lookup, builds the replay tool list, and trims the conversation to the replay starting point. Then it repeatedly asks the replay model for the next assistant message. If the model gives final text with no tool calls, it returns that text. If the model asks for tools, it feeds back archived results when possible. If a requested tool result is missing, or the round limit is reached, it returns the best text seen so far.

**Call relations**: This method coordinates the whole file. It relies on archived_tool_results, replay_tools, and replay_head for setup, calls the replay model for each turn, and uses _feed_archived to keep tool behavior locked to the archive.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation before prompt promotion`

This file is the promotion gate for prompt self-improvement. Imagine testing a new recipe against an old one: you do not switch just because the new recipe won once or twice. You want enough taste tests, and you want the win to be large enough that it is unlikely to be luck. Here, each replayed example records whether it used the candidate prompt and whether the judge accepted the result.

The file turns those yes/no outcomes into counts, then estimates the candidate prompt's “lift,” meaning how much better its acceptance rate is than the old prompt's acceptance rate. It uses Wilson confidence bounds, a statistical way to stay cautious when there are only a few examples. Instead of trusting the raw score, it asks: “What is the lowest improvement we can reasonably believe?”

There are two checks. First, the local check requires enough examples for both prompt versions and requires the cautious lower estimate of improvement to clear a small positive floor. Second, the global check looks at other task classes and blocks the candidate only if there is confident evidence that it causes a meaningful regression. The final result is a GateVerdict: pass or fail, a human-readable reason, the measured lower bound, and the sample counts.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious lower estimate for a success rate, such as “how often did this prompt get accepted?” It is used when the code wants to avoid being fooled by a tiny number of lucky successes.

**Data flow**: It takes the number of accepted examples and the total number of examples, plus an optional confidence setting. If there are no examples, it returns 0. Otherwise it computes the lower side of a Wilson confidence interval, using a square-root calculation for the uncertainty, and returns a number between 0 and 1.

**Call relations**: The lift calculations call this when they need the pessimistic side of one prompt arm's acceptance rate. It supplies one of the cautious ingredients used by lift_lower_bound and lift_upper_bound.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious upper estimate for a success rate. It answers, “How good could this prompt reasonably be, given the limited evidence?”

**Data flow**: It takes accepted and total counts, plus an optional confidence setting. If there are no examples, it returns 1, meaning the upper estimate is completely open. Otherwise it computes the upper side of a Wilson confidence interval and returns a number between 0 and 1.

**Call relations**: The lift calculations call this when they need the optimistic side of one prompt arm's acceptance rate. lift_lower_bound uses it to be cautious about the old prompt, and lift_upper_bound uses it to be generous about the candidate prompt.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This function estimates the worst believable improvement of the candidate prompt over the current prompt. It is the core “do we have real evidence of a win?” calculation.

**Data flow**: It takes a Contingency object containing accepted and total counts for candidate-present examples and candidate-absent examples. If either side has no examples, it returns 0. Otherwise it compares the two raw acceptance rates, subtracts a combined uncertainty amount, and returns the cautious lower bound for the candidate's lift.

**Call relations**: score_gate calls this after turning replay labels into counts. To build the cautious estimate, it calls wilson_lower_bound, wilson_upper_bound, and a square-root calculation.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This function estimates the best believable improvement of the candidate prompt over the current prompt. It is used to decide whether a candidate is clearly harmful on other tasks.

**Data flow**: It takes a Contingency object with the two prompt arms' accepted and total counts. If either side has no examples, it returns 0. Otherwise it compares the two acceptance rates, adds a combined uncertainty amount, and returns the optimistic upper bound for the lift.

**Call relations**: global_non_inferior calls this during the global safety check. It calls wilson_upper_bound and wilson_lower_bound to ask whether even the optimistic view of the candidate is still worse than the allowed margin.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This function turns individual replay results into the four counts the statistical checks need. It separates examples where the candidate prompt was present from examples where it was absent, then counts successes in each group.

**Data flow**: It takes a tuple of OutcomeLabel records. Each label says whether the candidate was present and whether the replay succeeded. The function splits the labels into present and absent groups, counts accepted examples and totals for both groups, and returns a Contingency object.

**Call relations**: score_gate and global_non_inferior both call this before doing any lift calculation. It is the counting step that feeds the later statistical judgment.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This function makes the local promotion decision for the target task class. It passes only when there are enough replays for both prompt versions and the cautious improvement estimate is above the required floor.

**Data flow**: It takes replay labels, plus optional thresholds for the minimum lower-bound lift and the minimum number of examples per side. It converts labels into counts, computes the lower lift bound, then returns a GateVerdict. The verdict explains whether the candidate failed because there was too little data, failed because the improvement was too weak, or passed the local gate.

**Call relations**: two_stage_gate calls this first. Inside, it calls contingency to count the examples and lift_lower_bound to measure the cautious improvement, then packages the result in a GateVerdict.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This function checks that the candidate prompt is not clearly worse on other task classes. It is intentionally lenient when evidence is thin: lack of proof of safety does not block promotion, but confident evidence of harm does.

**Data flow**: It takes replay labels for the broader held-out task set, plus an allowed regression margin and sample-size floor. It counts candidate-present and candidate-absent outcomes. If either side has too few examples, it returns true. Otherwise it computes the optimistic upper bound of the candidate's lift and returns true unless that optimistic value is still worse than the negative margin.

**Call relations**: two_stage_gate calls this only after the local score gate has passed. It calls contingency for counts and lift_upper_bound to decide whether the global results show a meaningful regression.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This function gives the full promotion verdict. A candidate must first show a clear local win, then avoid a clear global regression.

**Data flow**: It takes local replay labels for the target task class and global replay labels for other task classes. It runs the local gate first. If that fails, it returns that failure verdict unchanged. If the local gate passes, it runs the global safety check. A global regression produces a new failing GateVerdict; otherwise the original passing local verdict is returned.

**Call relations**: This is the top-level decision function in the file. It ties together score_gate for the improvement test and global_non_inferior for the safety test, creating a GateVerdict when the second stage blocks promotion.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### Scheduled improvement proposals
The cron runner orchestrates the full self-improvement cycle and opens only sufficiently tested prompt-change proposals for human approval.

### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled background tick`

This file is the safety-minded heartbeat of the self-improvement system. On each scheduled tick, it groups recorded agent runs by agent, then decides whether each agent has a prompt-improvement candidate that should be opened, tested, rejected, or promoted to a proposal. It does not directly change an agent’s prompt. Instead, if a candidate keeps passing tests, it creates a governed change proposal that someone else must approve.

The main idea is like testing a recipe change before updating the cookbook. For each agent prompt version, the file keeps at most one candidate prompt in a scoped store, which is persistent storage owned by this extension. The stored `CandidateState` remembers which original prompt digest the candidate came from, what task it targets, which examples are being held out for testing, how many times it has passed, and whether it is still being evaluated, already promoted, or rejected.

On every tick, the code either reuses an active candidate or asks the prompt proposer for a new one. Then it evaluates the candidate against held-out examples: examples saved for testing rather than used to create the candidate. A single pass is not enough. The candidate must pass for several consecutive ticks, controlled by `stability_count`, before a proposal is opened. If it fails, it is marked rejected so the same prompt version is not repeatedly proposed again.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: Runs one full scheduled self-improvement tick. It gathers all known trajectories, groups them by agent, and advances the improvement process separately for each agent.

**Data flow**: It starts by reading trajectories from the extension context. It groups those trajectories by their agent identifier, then sends each agent’s bundle onward for candidate checking. It returns nothing; its effect is whatever candidate state or proposals are created during the tick.

**Call relations**: This is the top-level method for this file’s cron work. It uses `_by_agent` to split the workspace history into per-agent groups, then calls `ImproveCron._advance` for each group so each agent is considered independently.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Moves one agent one step forward in the improvement process. It either finds or opens a candidate prompt for the agent’s current prompt version, then tests that candidate if one exists.

**Data flow**: It receives an agent ID and that agent’s trajectories. It reads the prompt digest from the first trajectory, builds the storage key for this agent’s candidate, and asks `_active_or_open` for a candidate tied to that digest. If there is no usable candidate, it stops. If there is one, it passes it to `_gate` for evaluation and possible promotion.

**Call relations**: `ImproveCron.run` calls this once per agent during a tick. This method is the bridge between candidate discovery in `ImproveCron._active_or_open` and safety testing in `ImproveCron._gate`.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: Finds the current active candidate for an agent, or opens a new one if it is safe and useful to do so. It prevents repeated proposals for candidates that were already rejected or promoted for the same prompt version.

**Data flow**: It receives a storage key, the current prompt digest, and the agent’s trajectories. It first checks the scoped store for saved candidate data. If the stored candidate belongs to the same prompt digest and is still evaluating, it returns that candidate. If the stored candidate is finished, it returns nothing. If there is no current candidate, it looks for task classes in the trajectories, asks the prompt proposer for an improved prompt for the first class, saves the new candidate state, and returns it.

**Call relations**: `ImproveCron._advance` calls this before any evaluation happens. It uses `task_classes` to find useful task groupings from the agent’s history and asks the proposer to create a candidate prompt when there is enough information.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: Tests a candidate prompt and decides whether it should keep being tested, be rejected, or be promoted to a governed proposal. This is the safety gate that stops a single lucky result from changing an agent.

**Data flow**: It receives the agent ID, store key, prompt digest, candidate state, and trajectories. It builds two sets of test examples: the candidate’s own held-out examples and held-out examples from other task classes. It asks the evaluator to compare the candidate prompt against the current prompt. If the verdict fails, it saves the candidate as rejected. If it passes but has not passed enough consecutive ticks, it saves an increased pass count. If it has passed enough times, it opens an `AgentChange` proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: `ImproveCron._advance` calls this after a candidate is found or opened. It relies on `_held_out` to reconstruct relevant test examples, on `task_classes` to collect broader checks, on the evaluator to judge the candidate, and on `_save` to persist the new candidate status after each decision.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: Writes an updated candidate state back to the extension’s store. It keeps the stored record in sync after a candidate passes, fails, or is promoted.

**Data flow**: It receives the store key, the existing candidate, and the new status information. It creates a copied candidate record with the updated status, pass count, and optional proposal ID, then writes that JSON-friendly data to the scoped store. It returns nothing.

**Call relations**: `ImproveCron._gate` calls this whenever the evaluation outcome changes the candidate’s state. This keeps later cron ticks from forgetting whether a candidate is still under test, already rejected, or already proposed.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: Groups trajectories by the agent that produced them. This lets the cron process each agent’s prompt history separately.

**Data flow**: It receives a tuple of trajectories. It builds a dictionary where each agent ID points to that agent’s trajectories, then returns the grouped data as tuples. It does not change the trajectories themselves.

**Call relations**: `ImproveCron.run` calls this at the start of a tick. Its output shapes the rest of the flow: each group becomes one call to `ImproveCron._advance`.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: Rebuilds the candidate’s held-out test examples from stored conversation IDs. It filters those conversations down to examples that represent bad or useful-to-test trajectories.

**Data flow**: It receives all trajectories for an agent and a tuple of held-out conversation ID strings. It makes a lookup table from conversation ID to trajectory, walks the stored held-out IDs, skips any missing conversations, and runs `bad_trajectory` on each found trajectory. When `bad_trajectory` identifies a usable failing example, it adds that example to the result. It returns the collected test examples.

**Call relations**: `ImproveCron._gate` calls this while preparing evaluation input. It hands the evaluator the specific held-out examples tied to the candidate, so the candidate is judged against the cases it was meant to improve.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).
