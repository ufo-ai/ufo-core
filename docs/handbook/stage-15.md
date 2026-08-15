# Subagents and delegated multi-agent workflows  `stage-15`

This stage is shared support for delegation during the main work loop. It lets one agent act like a manager: choose a specialist helper, give it a clearly shaped task, wait for it, cancel it if needed, and safely read its result. The core profile file defines the default “general purpose” helper. The catalog file keeps an up-to-date menu of available helpers, so the parent agent knows what kinds of tasks each one accepts. The main subagents file creates child conversations, runs them, checks their inputs and outputs, and blocks unsafe or badly formed replies. The delivery file is a backup courier that returns child results even if the normal handoff was interrupted.

Extensions add specialist workers. Browser files define and launch browser helpers, including parallel web visits. Research files define normal and deep research helpers, plus a tool that splits broad research across many children and saves results. Documents adds a writing helper. Sites adds a website-building helper and a tool that connects “build a site” requests to that child worker.

## Files in this stage

### Core subagent runtime
Defines the default helper profile, exposes available profiles to agents, runs child-agent lifecycles, and safely returns missed child results.

### `core/src/ufo/loop/profiles.py`

`config` · `subagent setup and dispatch`

This file is the system’s fallback recipe for making a child agent. A child agent is like a helper coworker: the parent gives it a contained task, and it works in the same shared workspace to produce an answer or file. Without this file, the core system would have no default subagent to spawn when an extension does not provide a more specific one.

The file names this default profile `general_purpose`. It gives the subagent a carefully limited tool set. The helper can read and write files, search, edit, run shell commands, load skills, and use certain optional extension tools if those extensions are installed. But it cannot ask the user questions, create more subagents, message or cancel sibling agents, or approve account connections. In plain terms, it can do work, but it cannot delegate, interrupt others, or make user-facing permission decisions.

It also defines simple input and output shapes using Pydantic, a library that describes and checks structured data. The input is a freeform `task`; the output is a freeform `result`.

The prompt text tells the helper how to behave: work independently, avoid repeated failing loops, load relevant skills first, use proper Office formats for formal documents, and save useful artifacts in `/workspace` with clear names. At the end, all of this is bundled into `GENERAL_PURPOSE_PROFILE`, and then exposed as the core profile list.


### `core/src/ufo/loop/subagent_catalog.py`

`domain_logic` · `startup`

This file exists so an agent can safely delegate work to subagents without guessing the available options. A subagent profile is like a job description: it has a name and a shape of input data it accepts. Instead of writing that list by hand, this file reads the live subagent registry, which is the same source used when `spawn_subagent` actually starts a subagent.

The main output is a `RuntimeSkill`, which is a skill made available to the agent while it runs. The skill contains a small Markdown table. Each row names a subagent profile and lists the payload keys that profile expects. Required keys and optional keys are shown differently, so the agent knows what must be included.

The important idea is “one source of truth.” If a new subagent profile is registered, or its input changes, this catalog is generated from that same live information at boot. Without this file, the agent might rely on stale instructions and try to spawn a subagent with the wrong profile name or missing payload fields.

#### Function details

##### `_payload`  (lines 18–25)

```
def _payload(profile: SubagentProfile) -> str
```

**Purpose**: This helper turns one subagent profile’s input model into a short human-readable list of payload fields. It marks which fields are optional so the catalog can tell the agent what data must be supplied.

**Data flow**: It receives a `SubagentProfile`, reads the fields from that profile’s input model, and formats their names. If there are no fields, it returns the text “(no fields)”; otherwise it returns a comma-separated list where required fields are plain names and optional fields are labeled as optional.

**Call relations**: This is called while `subagent_catalog_skill` is building the catalog table. It supplies the payload-column text for each profile row, so the larger skill can show not just what subagents exist, but what each one needs.

*Call graph*: called by 1 (subagent_catalog_skill).


##### `subagent_catalog_skill`  (lines 28–50)

```
def subagent_catalog_skill(registry: SubagentRegistry) -> RuntimeSkill
```

**Purpose**: This function creates the actual runtime skill that agents can load to learn how to spawn subagents. It builds the catalog directly from the current subagent registry, so the displayed instructions match what the system can really dispatch.

**Data flow**: It receives a `SubagentRegistry`, reads all registered profiles, sorts them by name, and asks `_payload` to describe each profile’s expected input. It then writes those rows into Markdown instructions and uses them to create and return a `RuntimeSkill` object with a name, description, instructions, and raw skill text.

**Call relations**: This is the main builder in the file. During startup, code that prepares runtime skills can call it with the live registry; it calls `_payload` for each profile, then hands the completed text to `RuntimeSkill.__init__` to produce the skill object the agent will later read before delegating work.

*Call graph*: calls 1 internal fn (_payload); 1 external calls (__init__).


### `core/src/ufo/loop/subagents.py`

`domain_logic` · `turn execution and subagent result delivery`

A subagent is like sending a specialist assistant to do a bounded job while the main assistant keeps its own place. This file defines the rules for that: which subagent profiles exist, how a child turn is created, how it is placed on the work queue, how its final answer is checked, and how that answer is delivered back to the parent conversation.

The flow starts with a profile. A profile names the child’s instructions, allowed tools, and the expected input and output data shapes. `SubagentRegistry` is the address book for these profiles and refuses duplicate names. `subagent_system_prompt` builds the child’s instructions, including skill information and a strict rule that the child must finish by calling the `finish` tool with a structured answer.

`Subagents` is used by a running parent turn. It can spawn a child, optionally using a deduplication key so crash recovery does not create the same child twice. It writes the child conversation and turn to the database, queues it for execution, and either returns immediately for background work or waits until the child finishes. It also supports later result lookup, waiting, cancellation, and follow-up messages.

`SubagentResult` is the delivery path for background children. When a child finishes, it posts a carefully wrapped result back to the parent conversation. If the child failed, used an unknown profile, or returned invalid data, that failure is delivered instead of unsafe prose.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 100–104)

```
def __post_init__(self) -> None
```

**Purpose**: This checks the registry right after it is created and makes sure no two subagent profiles use the same name. Without this, asking for a profile by name could silently pick the wrong specialist.

**Data flow**: It reads the names from the registry’s profile list, finds any names that appear more than once, and either leaves the registry usable or raises an error naming the duplicates.

**Call relations**: This runs automatically when a `SubagentRegistry` object is built. Later lookups through `SubagentRegistry.get` depend on this guarantee that each profile name points to only one profile.


##### `SubagentRegistry.get`  (lines 106–113)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: This finds a subagent profile by name. It is used whenever the system needs to spawn a subagent or validate a finished child’s answer.

**Data flow**: It receives a profile name, scans the stored profiles, and returns the matching profile. If none match, it builds a helpful error that includes the valid profile names and raises `UnknownSubagentProfile`.

**Call relations**: The spawning and result-checking paths call on this registry lookup before they trust a profile name. When the name is missing, it stops the flow clearly instead of letting an unknown child run or an unchecked result pass through.

*Call graph*: 1 external calls (__init__).


##### `subagent_system_prompt`  (lines 116–147)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[LoadedSkill, ...]=()) -> str
```

**Purpose**: This builds the full instruction text that a child subagent sees. It combines the profile’s own prompt, available skill information, optional preloaded skill text, shared safety and formatting rules, and the final answer contract.

**Data flow**: It takes a subagent profile, a list of skill names and descriptions, and optional already-loaded skills. It fills the skill index slot, checks that no unresolved prompt placeholders remain, rejects oversized preloaded skill text, and returns one complete prompt string ending with the required `finish` tool instruction.

**Call relations**: This function prepares the child’s operating instructions before model execution. It relies on prompt rendering helpers for the skill index and loaded skill text, and it deliberately keeps the final output contract at the end so preloaded skills cannot override how the child must answer.

*Call graph*: 3 external calls (findall, render_skill_index, loaded_context).


##### `Subagents.authorize`  (lines 161–162)

```
def authorize(self, requester_member_id: UUID | None) -> 'Subagents'
```

**Purpose**: This records which audience member is asking to use subagent powers. It lets later checks make sure one member cannot operate on another member’s child subagent.

**Data flow**: It receives an optional member id, copies the current `Subagents` object, sets that requester id on the copy, and returns the copy. The original object is not changed.

**Call relations**: Callers use this before spawning or controlling children on behalf of a specific member. The copied value feeds into `acting_member_id`, which is then used by spawn, message, cancel, and child-ownership checks.

*Call graph*: 1 external calls (replace).


##### `Subagents.acting_member_id`  (lines 165–166)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: This chooses the member identity that the subagent action should count as. It prefers the explicit requester, and otherwise falls back to the member the parent turn is already acting for.

**Data flow**: It reads `requester_member_id` and the parent turn’s `on_behalf_of_member_id`, then returns the first available value. It does not change anything.

**Call relations**: Many methods use this as the authority stamp for child turns. `_admit` stores it on new children, and `_require_child` later checks it before allowing result lookup, waiting, messaging, or cancellation.


##### `Subagents.spawn`  (lines 168–221)

```
async def spawn(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False) -> SpawnResult
```

**Purpose**: This starts a child subagent turn. It validates the input, creates or reuses the child turn, queues it to run, and either returns the child id immediately or waits for its checked final answer.

**Data flow**: It receives a profile name, input payload, and options such as background mode and a deduplication key. It looks up the profile, validates the payload against the profile’s input model, chooses a child conversation id, admits the child turn into the database, queues it if needed, and then either returns a `SpawnResult` with no output for background work or waits for the terminal result and validates the final JSON against the output model.

**Call relations**: This is the main entry into subagent spawning. It uses `_admit` to create durable database rows, `_enqueue` to hand the work to the workflow queue, and `_await_terminal` when foreground callers need the child’s finished answer before continuing.

*Call graph*: calls 3 internal fn (_admit, _await_terminal, _enqueue); 5 external calls (__init__, __init__, turn_id_for, uuid4, uuid5).


##### `Subagents.result`  (lines 223–258)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: This reads the final state and checked output of a child that this conversation spawned. It is useful when the caller already has a child turn id and wants the trustworthy structured result, not just whatever text the model wrote.

**Data flow**: It receives a child turn id, first confirms that the turn belongs to this parent conversation, then reads the child’s conversation id and terminal frame from the database. If the child finished successfully and the profile still exists, it validates the terminal text against the profile’s output model; otherwise it returns no output but still includes the terminal details.

**Call relations**: This method is a safe result-inspection path. It calls `_require_child` for the ownership check and `_untrusted_output` to mark whether the returned material should be treated as untrusted.

*Call graph*: calls 2 internal fn (_require_child, _untrusted_output); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `Subagents.wait`  (lines 260–280)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: This pauses until one or more child turns have finished and reports their final statuses and text. It is for short, tool-bounded waits, not for keeping a parent turn open forever.

**Data flow**: It receives a tuple of child turn ids. For each id it confirms the child belongs to this conversation, waits until the terminal frame appears in the database, and returns a tuple of `SubagentStatus` values containing the final status, final text, and whether the output is untrusted.

**Call relations**: This method combines `_require_child`, `_await_terminal`, and `_untrusted_output`. It is used when a caller needs to block on child completion inside the current operation rather than relying on background result delivery.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 282–299)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: This cancels a running child subagent that belongs to the current conversation. It refuses to cancel unrelated turns, which protects one user or parent turn from interfering with another’s work.

**Data flow**: It receives a child turn id, checks ownership, asks the shared cancellation routine to cancel that turn’s durable workflow, then reads the turn’s current status and terminal text from the database. It returns a `SubagentStatus` showing the result of the cancellation attempt.

**Call relations**: This method uses `_require_child` as the gatekeeper, then hands cancellation to the wider turn-cancellation system. After cancellation, it reports the database state back to the caller.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents.message`  (lines 301–407)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str) -> SubagentStatus
```

**Purpose**: This sends a follow-up message to a background child subagent’s own conversation. It lets a parent continue an existing child’s thread instead of starting a brand-new specialist.

**Data flow**: It receives the original child turn id, message text, and a deduplication key. It confirms the child belongs here, locks the child conversation, either finds an already-created follow-up turn for that key or inserts the next turn in the child conversation, checks whether earlier queued child turns must run first, and queues the follow-up if it is ready to dispatch. It returns the follow-up turn id and status.

**Call relations**: This method depends on `_require_child` for permission and `_enqueue` for dispatch. It is designed for recovery: if the same messaging step is replayed after a crash, the deduplication key reconnects to the existing follow-up instead of adding a duplicate.

*Call graph*: calls 2 internal fn (_enqueue, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._untrusted_output`  (lines 409–415)

```
def _untrusted_output(self, profile: str) -> bool
```

**Purpose**: This answers whether a profile’s output should be treated as unsafe instructions rather than trusted data. It fails closed: if the profile cannot be found, the output is treated as untrusted.

**Data flow**: It receives a profile name, tries to look it up in the registry, and returns that profile’s `untrusted_output` flag. If the profile is unknown, it returns `true`.

**Call relations**: This helper is used by `result` and `wait` when they report child output. It keeps missing or untrusted profiles from being accidentally treated as reliable parent instructions.

*Call graph*: called by 2 (result, wait).


##### `Subagents._require_child`  (lines 417–447)

```
async def _require_child(self, turn_id: UUID) -> str
```

**Purpose**: This proves that a given turn id is a subagent child that this conversation is allowed to operate on. It is the safety gate before reading, waiting for, messaging, or cancelling a child.

**Data flow**: It receives a turn id and reads the turn’s parent id, profile, and member identity from the database. It accepts the turn if it was spawned by this parent turn, or by another turn in the same parent conversation, and if the member identity matches the current actor. Otherwise it raises an error.

**Call relations**: The public control methods `result`, `wait`, `cancel`, and `message` all call this first. That makes ownership and member-authority checking a shared rule instead of something each method has to recreate.

*Call graph*: called by 4 (cancel, message, result, wait); 2 external calls (select, workspace_tx).


##### `Subagents._admit`  (lines 449–528)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, profile: str, inbound: str, delivers_result: bool=False) -> bool
```

**Purpose**: This creates the database records for a child subagent conversation and its first turn. It is careful to be idempotent, meaning the same request can be replayed without creating duplicate children.

**Data flow**: It receives the child conversation id, turn id, profile name, serialized input, and whether the child should deliver a background result. It inserts the conversation and turn if they do not already exist, checks that an existing deduplicated turn belongs to the same acting member, marks a queued turn as ready for dispatch, and returns whether it should be enqueued.

**Call relations**: `spawn` calls this before queueing work. `_admit` prepares durable state first, so if the process crashes, recovery can find the existing child turn and continue without charging or running the same branch twice.

*Call graph*: called by 1 (spawn); 6 external calls (select, update, audience_member, workspace_tx, conversation_name, current_traceparent).


##### `Subagents._enqueue`  (lines 530–559)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This submits a queued subagent turn to the workflow queue that actually runs turns. It also cleans up the database marker if enqueueing is cancelled or fails.

**Data flow**: It receives a turn id and conversation id, builds queue options including the workflow id and queue partition, and asks the DBOS client to enqueue the work. If the coroutine is cancelled or the enqueue fails, it clears the dispatch timestamp for still-queued turns so another dispatcher can try later; unexpected failures are logged.

**Call relations**: `spawn` uses this for a new child’s first turn, and `message` uses it for follow-up turns. It is the bridge between durable database admission and the external workflow runner.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 561–571)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: This waits until a child turn has a final terminal frame in the database. A terminal frame is the saved final outcome of a turn, such as done, failed, or cancelled.

**Data flow**: It receives a turn id, repeatedly reads that turn’s terminal field from the database, and sleeps briefly between checks. Once the terminal field is present, it validates it as a `TerminalFrame` and returns it.

**Call relations**: `spawn` uses this for foreground subagents that must return an answer directly, and `wait` uses it for explicit waiting on child ids. It is a simple polling loop over durable state.

*Call graph*: called by 2 (spawn, wait); 4 external calls (sleep, model_validate, select, workspace_tx).


##### `SubagentResult.deliver`  (lines 593–625)

```
async def deliver(self, child: Turn) -> None
```

**Purpose**: This posts a finished background child’s result back into the parent conversation. It lets the parent receive child work as a normal new arrival instead of holding a turn open while waiting.

**Data flow**: It receives a child turn. If the child is not marked for pending delivery, it does nothing. Otherwise it verifies the child has a parent, profile, and terminal result, reads the parent conversation from the database, builds the delivery body, invokes the parent conversation with a deduplicated result key, and finally marks the child result as delivered.

**Call relations**: This is the background result delivery path. It calls `_body` to create the safe result message, uses the turn invoker to post it to the parent, and only then updates the child as delivered so a crash can safely retry without losing the result.

*Call graph*: calls 1 internal fn (_body); 3 external calls (select, update, workspace_tx).


##### `SubagentResult._body`  (lines 627–642)

```
def _body(self, profile: str, child_id: UUID, terminal: TerminalFrame) -> str
```

**Purpose**: This wraps a child’s result in a clear envelope that names the profile, child id, and status. The envelope helps the parent understand which child answered and prevents child text from escaping into ordinary instructions.

**Data flow**: It receives the profile name, child id, and terminal frame. It finds the matching profile if one still exists, asks `_payload` for the deliverable text and status, wraps untrusted or unknown-profile content in a protective wall, escapes any fake closing result tag inside the payload, and returns one formatted result block.

**Call relations**: `deliver` calls this right before posting the result to the parent conversation. `_body` in turn calls `_payload` to decide whether the child produced valid output, an error diagnostic, or an invalid-result message.

*Call graph*: calls 1 internal fn (_payload); called by 1 (deliver); 1 external calls (wall).


##### `SubagentResult._payload`  (lines 644–664)

```
def _payload(self, resolved: SubagentProfile | None, terminal: TerminalFrame) -> tuple[str, str]
```

**Purpose**: This decides what text is safe and accurate to deliver for a finished child. It delivers valid structured output when possible, and otherwise turns failures or invalid answers into explicit diagnostic messages.

**Data flow**: It receives an optional resolved profile and a terminal frame. If the child did not finish with status `done`, it returns an error diagnostic and that terminal status. If the profile is missing, it returns an unknown-profile warning. If the child claims success, it validates the terminal text against the profile’s output model and returns normalized JSON on success or a schema-failure message on validation failure.

**Call relations**: `_body` uses this as the decision engine for result content. This separation keeps the validation and failure interpretation distinct from the outer XML-like envelope and untrusted-content wrapping.

*Call graph*: called by 1 (_body).


### `core/src/ufo/loop/delivery.py`

`orchestration` · `background scheduled sweep`

When a conversation delegates work to a child turn, the parent expects to be woken up when that child finishes. Usually that happens directly at the end of the child’s execution. But some endings happen from the outside, such as cancellation by another process, or a crash after the result was saved but before the parent was notified. Without this file, a parent could wait forever even though the child’s final state is already stored in the database.

`DeliverySweep` is the backstop. Think of it like a mailroom worker who periodically checks for completed letters that were never delivered. It looks in durable database state, not in a currently running task, so it can find missed results after failures. It gathers finished child turns whose `result_delivery` marker still says delivery is pending, groups them by the parent conversation, and sends each result through `SubagentResult`, the same delivery route used by the normal event path.

It also avoids waking the same conversation too often. If another result was delivered to that conversation recently, this sweep skips it until a later tick. That gives the system a simple cooldown so a busy fan-out of child tasks is folded into fewer parent wake-ups instead of one wake-up per child.

#### Function details

##### `DeliverySweep.run`  (lines 50–65)

```
async def run(self) -> None
```

**Purpose**: This is the main sweep pass. It finds child turns that are finished but not yet handed back, skips parent conversations that were woken very recently, and delivers the remaining child results.

**Data flow**: It starts with no outside argument and reads the current workspace plus database-backed child-turn state. First it asks `_outstanding` for pending finished children grouped under their parent conversations. If there are none, it stops. Otherwise it computes a recent-time cutoff, asks `_woken_since` which of those conversations already got a delivery during that cooldown window, builds a `SubagentResult` delivery helper for the current workspace, and calls that helper for every child that is still safe to deliver. The result is that database-stored child completions are turned into parent wake-ups; the function itself returns nothing.

**Call relations**: This is the top-level body of the delivery job. It depends on `_outstanding` to find missed work, then on `_woken_since` to avoid repeating wake-ups too quickly. After that, it hands each chosen child turn to `SubagentResult`, which performs the actual arrival path used elsewhere in the system.

*Call graph*: calls 2 internal fn (_outstanding, _woken_since); 4 external calls (__init__, now, timedelta, ws_current).


##### `DeliverySweep.candidate_workspaces`  (lines 67–79)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This finds which workspaces have at least one finished child turn whose result is still waiting to be delivered. A scheduler can use it to decide where running this sweep would be useful.

**Data flow**: It opens an owner-level database transaction, which can see workspace-wide metadata, and selects distinct workspace IDs from turns marked as pending delivery and already terminal, meaning finished. It turns those database rows into a tuple of workspace UUIDs. It does not deliver anything or change any state; it only reports where pending work exists.

**Call relations**: This supports the job scheduling side rather than the delivery pass itself. Before a sweep is run inside a workspace, this function can be called to discover the workspaces that need attention.

*Call graph*: 2 external calls (select, owner_tx).


##### `DeliverySweep._outstanding`  (lines 81–114)

```
async def _outstanding(self) -> dict[UUID, list[Turn]]
```

**Purpose**: This collects the actual child turns that need rescue delivery in the current workspace. It groups them by the parent conversation so one sweep can wake a parent once for a whole batch of finished children.

**Data flow**: It reads the workspace database and joins each child turn to its parent turn so it can learn the parent conversation ID. It keeps only child turns that are finished and still marked as pending result delivery, orders them by parent conversation and finish/update time, and limits the batch size. Each database row is converted into a `Turn` record, then placed into a dictionary keyed by the parent conversation ID. The output is that grouped dictionary; the database is not changed here.

**Call relations**: `DeliverySweep.run` calls this first to know what needs delivery. Its grouped output shapes the rest of the sweep: `run` checks recent wake-ups per parent conversation and then delivers each child in those groups.

*Call graph*: called by 1 (run); 3 external calls (model_validate, select, workspace_tx).


##### `DeliverySweep._woken_since`  (lines 116–143)

```
async def _woken_since(self, cutoff: datetime, conversations: tuple[UUID, ...]) -> frozenset[UUID]
```

**Purpose**: This checks which parent conversations were already woken by a child-result delivery after a given time. It is the cooldown check that prevents repeated immediate wake-ups for the same conversation.

**Data flow**: It receives a cutoff time and a set of conversation IDs to check. It reads the workspace database, connects parent turns to delivered child turns, and looks for child turns marked as already delivered with an update time newer than the cutoff. It returns those matching conversation IDs as a frozen set. It only reads state; it does not change delivery markers.

**Call relations**: `DeliverySweep.run` calls this after finding outstanding children. The returned set tells `run` which conversations to skip for now, leaving their pending children to be retried on a later sweep tick.

*Call graph*: called by 1 (run); 2 external calls (select, workspace_tx).


### Browser delegation
Configures browser-capable child agents and provides tools for single or parallel delegated browsing sessions.

### `extensions/browser/ufo_ext_browser/subagent.py`

`config` · `subagent setup and browser task spawning`

This file is like an ID card and instruction packet for a browser-focused helper agent. The main agent can send work to this helper when a task needs web browsing, such as opening pages, filling forms, collecting information, or saving screenshots and notes.

The file first loads a long browser-specific prompt from a nearby Markdown file. That prompt teaches the child agent how to work in the browser. It then builds the allowed tool list: browser tools, plus a few basic file and search tools so the child can read and write shared workspace files and search the web when needed.

Two small data models describe the messages going into and coming out of the subagent. `BrowserTask` says a browser job includes a freeform task, and may also include a starting URL, a task name, and an `extended_context` flag. That flag defaults to true because browser sessions often need many steps, and cutting them off halfway would leave the parent agent with work that cannot easily be resumed. `BrowserResult` wraps the final freeform answer.

Finally, `BROWSER_PROFILE` combines all of this into one `SubagentProfile`. This is the object the wider system can register or call when it wants to launch the browser subagent safely, with a known prompt, known tools, known input and output formats, and a chosen model.


### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling during browser tool calls`

This file exists so the main agent does not have to drive a browser directly. Instead, it can delegate browser work to a separate “browser” subagent, like asking a specialist coworker to take over a web task and report back. That keeps browser sessions isolated, gives them clear time limits, and prevents a stuck website from trapping the main agent forever.

The first tool, `browser_task`, starts one fresh browser session with a URL, a task description, and a friendly task name. It waits for the browser subagent to finish, but only up to a bounded timeout. If the browser run takes too long, it is cancelled and the tool returns an error-style message instead of hanging.

The second tool, `wide_browse`, is for batch work. It reads a workspace file containing URLs or site names, removes blank lines and duplicates, and then sends each remaining item to a browser subagent. It limits how many browser jobs run at once, so a large list does not flood the system. Each result is collected into rows, written to `wide_browse.json`, and also returned to the caller.

A key detail is deduplication. Each delegated browser run gets a stable key based on the tool call and, for batch browsing, the entity name. If work is retried after a crash, the system can reconnect to existing child runs instead of accidentally starting duplicates.

#### Function details

##### `_browser_task`  (lines 98–127)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one full browser automation task by spawning the dedicated browser subagent and waiting for its answer. It protects the parent task from getting stuck by enforcing a timeout and cancelling the browser run if it runs too long.

**Data flow**: It receives the tool context and a `BrowserTaskInput` containing the starting URL, task instructions, task name, timeout, and user-facing description. It checks that subagent control is available, starts a browser child run with the URL and task details, then waits for that child run. If the wait times out or the child is cancelled, it returns a `ToolResult` explaining that the browser task was cancelled. If the child finishes normally, it reads the child’s JSON text as a `BrowserResult` and returns that result as text to the caller.

**Call relations**: This is the handler behind the public `browser_task` tool. When the tool is called, it uses `ToolContext.spawn` to hand work to the browser profile, waits under `asyncio.timeout`, and then packages the browser subagent’s final `BrowserResult` into a normal tool response.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 130–143)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. `wide_browse` uses this to get the list of websites or entities it should visit.

**Data flow**: It receives the tool context and a file path. It safely quotes the path for the shell, runs `cat` through the sandbox, and raises an error if the file cannot be read. It then splits the file into lines, trims whitespace, skips blank entries, removes duplicates while keeping the first occurrence, and returns the cleaned list.

**Call relations**: `_wide_browse` calls this before starting any browser jobs. It provides the batch input list that `_wide_browse` later fans out across browser subagents.

*Call graph*: called by 1 (_wide_browse); 1 external calls (quote).


##### `_wide_browse`  (lines 146–173)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs many browser automation tasks from a list of URLs or site names, with a limit on how many run at the same time. It collects all child results and writes them into a JSON file in the workspace.

**Data flow**: It receives the tool context and a `WideBrowseInput` containing an entities file, a prompt template, an output schema file, and a user-facing description. It reads and deduplicates the entities, rejects the request if there are more than 128, reads the optional output schema, and creates a semaphore, which is a small gate that allows only a fixed number of jobs through at once. It then starts one `visit` task per entity, gathers their results, writes the list of rows to `wide_browse.json`, and returns both the rows and the output file name in a `ToolResult`.

**Call relations**: This is the handler behind the public `wide_browse` tool. It first relies on `_read_lines` to prepare the work list, then uses its inner `visit` function for each entity, runs those visits concurrently with `asyncio.gather`, and finally returns the combined browser outputs to the caller.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_browse.visit`  (lines 154–167)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs one browser subagent visit for one entity in a `wide_browse` batch. It turns the shared prompt template into a specific task for that entity and returns a small result row.

**Data flow**: It receives one entity, such as a URL or company name. It waits for permission from the semaphore so the batch does not run too many browser sessions at once. It replaces `{entity}` in the prompt template, appends the output schema text if one was provided, and spawns a browser subagent with that task. When the child run finishes, it returns a dictionary containing the original entity and the child output as JSON text, or an empty string if there was no output.

**Call relations**: `_wide_browse` creates and calls this helper once for every cleaned entity. Each `visit` hands one specific browsing job to `ctx.spawn`, and `_wide_browse` later gathers all of those per-entity rows into the final JSON output.


### Writing subagent profile
Declares the specialist child assistant used for drafting and editing prose.

### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `subagent setup`

This file is like an employee badge and job description for a writing-focused helper. When the larger system wants a child agent to work on prose, it can use the profile defined here instead of building that setup from scratch each time.

The file names the profile `writing`, pins it to the `gpt-5.6-terra` model, and gives it a prompt loaded from `prompts/subagent_writing.md`. It also limits the tools the child can use. The writing child may read, write, edit, search files, and load a skill. It cannot run shell commands, execute code, browse the web, or share files directly. That matters because this child is meant to write and revise text, not act like a general coding assistant or gather outside facts on its own.

The file also defines two small data shapes using Pydantic, a library that checks structured data. `WritingTask` is the input the parent gives the child, including the writing objective and which skills to preload. By default, it preloads the `writing-drafts` skill, so the child begins with the right workflow already available. `WritingResult` is the simple structured answer the child returns. Finally, all of these pieces are bundled into `WRITING_PROFILE`, which the rest of the system can use when spawning this prose-writing subagent.


### Research delegation
Defines research-focused child profiles and the orchestration tool that fans out entity research in parallel.

### `extensions/research/ufo_ext_research/subagent.py`

`config` · `startup / agent profile registration`

This file is like a job description packet for research-focused helper agents. Instead of putting all research behavior directly into the main agent, the system can spin up a specialized child agent with a clear mission, a fixed tool belt, and a known way to receive and return work.

It defines two profile names: `research` for ordinary scoped research tasks, and `deep_research` for longer multi-source investigations. Both profiles use the same allowed tools. These include web search and page fetching tools, a browser task tool, external tool access, file editing and reading tools, memory search, and spreadsheet support. The important safety boundary is that these research agents get only the tools listed here, not every possible capability in the system.

The file also loads two prompt files from disk. A prompt is the written instruction set that tells the agent how to behave. The normal and deep research agents each get their own prompt. The deep version also gets a much higher round limit, meaning it can take more back-and-forth steps before it must stop.

Finally, the file defines simple input and output models. A research task comes in as an `objective`, and the result comes back as `result`. These models make the handoff between the main system and the subagent predictable.


### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling`

This file solves the problem of doing the same research task for many targets, such as a list of companies or topics. Instead of asking one agent to work through the whole list one by one, it fans the work out to several research subagents at once, like giving each person in a research team one name from a checklist.

The tool starts with an input file that contains one entity per line. It reads that file safely through the sandbox, removes blank lines, and keeps only the first copy of any duplicate entity. It also checks that the list is not too large, so one request cannot accidentally launch an unbounded amount of work.

For each entity, the tool fills in a prompt template by replacing `{entity}` with the actual name. If the caller provided an output schema file, the schema text is appended so the research subagent knows what shape the answer should have. The work is then sent to the existing research profile through `ctx.spawn`, which means each child run uses the normal research tool setup instead of inventing a separate path.

Only a limited number of subagents run at the same time. This keeps the system from overloading itself. Each child also gets a stable deduplication key based on the parent call and entity name, so if the parent run is retried after a crash, completed child work can be reused instead of repeated. Finally, all rows are written to `wide_research.json`, and the tool returns both the rows and the output filename.

#### Function details

##### `_read_lines`  (lines 42–55)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: This helper reads the entity list file and turns it into a clean list of unique names. It exists so the main research flow can start from trustworthy, de-duplicated input instead of raw file text.

**Data flow**: It receives the tool context and a file path. It asks the sandbox to run `cat` on that path, quoting the path so special shell characters in the filename are treated as text, not commands. If reading fails, it raises an error. If reading succeeds, it walks through the file line by line, trims extra spaces, skips empty lines, removes duplicates while preserving the original order, and returns the final list of entities.

**Call relations**: The main `_wide_research` function calls this first, before it creates any child research jobs. This helper uses shell quoting through `shlex.quote` because the file is read through a real shell command, and safe quoting prevents a filename from being mistaken for shell instructions.

*Call graph*: called by 1 (_wide_research); 1 external calls (quote).


##### `_wide_research`  (lines 58–85)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: This is the main body of the `wide_research` tool. It reads the list of targets, launches bounded parallel research jobs for them, writes the combined results to a JSON file, and returns a summary to the caller.

**Data flow**: It receives the tool context and a structured input object containing the entity file, prompt template, output schema file, and user description. It reads and de-duplicates the entities, rejects the request if there are too many, reads the optional schema file, then creates a semaphore, which is a simple gate that limits how many child tasks may run at once. It starts one `visit` task per entity, waits for all of them to finish, writes the collected rows to `wide_research.json` in the workspace, and returns a `ToolResult` containing JSON text with the rows and output filename.

**Call relations**: This function is registered as the handler for `WIDE_RESEARCH_TOOL`, so it runs when the tool is invoked. It calls `_read_lines` to prepare the entity list, uses `asyncio.gather` to wait for all per-entity visits, uses `json.dumps` to format the final data, and wraps the response in `TextContent` and `ToolResult` so the tool system can send it back in the expected format.

*Call graph*: calls 1 internal fn (_read_lines); 6 external calls (__init__, __init__, Semaphore, gather, dumps, quote).


##### `_wide_research.visit`  (lines 66–79)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: This inner helper performs the research step for one entity. It builds the exact objective for that entity, sends it to a research subagent, and turns the answer into one row for the final output file.

**Data flow**: It receives a single entity name from the outer `_wide_research` function. It waits for a slot in the semaphore so only a fixed number of entities are researched at once. It replaces `{entity}` in the prompt template, appends the output schema if one was successfully read, and calls `ctx.spawn` to run the research profile with that objective. It then returns a dictionary containing the entity name and the child result as JSON text, or an empty string if the child produced no output.

**Call relations**: This helper is created inside `_wide_research` because it needs access to the current request, schema text, semaphore, and idempotency key. `_wide_research` starts one `visit` task for each entity and gathers their returned rows. Each `visit` hands the actual research work off to the research subagent profile, using a deterministic deduplication key so retries can reconnect to prior child runs instead of repeating finished work.


### Website delegation
Configures the website-building child agent and the tool that delegates site creation work to it.

### `extensions/sites/ufo_ext_sites/subagent.py`

`config` · `subagent setup when a website-building task is delegated`

This file describes a specialized helper agent whose job is to build websites inside a controlled workspace. Think of it like giving a contractor a job brief, a toolbox, and a rule that they must report back instead of handing the finished product directly to the customer.

The file loads the website-building prompt from a nearby Markdown file. That prompt contains the detailed working instructions for the subagent. It then sets a round limit, which caps how long the subagent may keep working before control must return.

A key part of the file is the tool list. The subagent can read and edit files, run shell commands, build and locally serve a site, use JavaScript and spreadsheet-style REPL tools, and optionally use web research tools if they are installed. It deliberately excludes publishing and file-sharing tools. That matters because this child agent works inside the parent turn’s sandbox. It should build and verify the site, then leave the result in the workspace for the parent agent to inspect and decide what to do next.

The two small Pydantic models define the shape of messages going in and out: a task with an objective, and a freeform result. Finally, all of this is packaged into a SubagentProfile so the wider system can launch this website-building worker consistently.


### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file exists so the main assistant does not have to do every website build itself. Instead, it can delegate the work to a specialized “website_building” subagent, much like a project manager handing a complete brief to a web developer. The important rule is that the brief must be self-contained: the child agent does not inherit the parent conversation’s chat history, so the objective has to include all needed context.

The file defines the shape of the tool’s input with `BuildWebsiteInput`. This is a Pydantic model, meaning it describes and checks the expected fields before the tool runs. The input includes the build objective, an optional friendly task name, optional skills to preload for the child, an optional setting for a larger work budget, and a plain-language user description for the activity timeline.

The actual tool function, `_build_website`, calls `ctx.spawn` to start or reconnect to the website-building child agent. The child works in the same sandbox, so files and hosted site registration remain attached to this conversation. The tool is marked as side-effecting because it can create files, run services, and deploy something. It also uses an idempotency key, so if the system retries after a crash, it can reconnect to the same child task instead of accidentally starting a duplicate build.

#### Function details

##### `_build_website`  (lines 57–64)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This function performs the actual delegation when the `build_website` tool is called. It sends the website build request to the specialized website-building subagent and returns that child agent’s summary as the tool result.

**Data flow**: It receives the current tool context and a checked `BuildWebsiteInput` object. It turns the input into a plain data package, leaving out empty fields and removing `user_description` because that field is for the timeline rather than the child’s build instructions. It then asks the context to spawn the website-building subagent, using the current idempotency key so retries reconnect to the same work. When the child finishes or reports back, the function turns the child’s output into JSON text if there is output, wraps that text in `TextContent`, and returns it inside a `ToolResult`.

**Call relations**: This function is registered as the handler for the `build_website` tool in `DELEGATION_TOOLS`. When the main agent chooses that tool, the tool system calls `_build_website`; `_build_website` hands the job to `ToolContext.spawn`, then packages the child agent’s answer for the caller.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).

## 📊 State Registers Touched

- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-turn-queue` — The durable waiting line and status record for each unit of agent work, from queued to running to finished or failed.
- `reg-runtime-fleet` — The sign-in sheet of running server and worker instances used to detect active work, crashes, and abandoned turns.
- `reg-subagent-state` — The helper-agent catalog and child-conversation handoff state used when one agent delegates work to another.
- `reg-observability-context` — Trace IDs, metrics, logs, and sanitized operational events used to understand work across services and turns.
- `reg-inflight-cancellation-handles` — Live cancellation signals, workflow handles, and parent-child cancellation propagation state for active turns and delegated work before final durable status is written.
