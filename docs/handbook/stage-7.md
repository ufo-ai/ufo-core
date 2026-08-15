# Conversation admission, turn creation, and live control  `stage-7`

This stage is the conversation “front desk” during the main work loop. It decides when new work may enter a conversation, creates a durable turn record for that work, and gives people live control over work that is already running.

The admission file is the main doorway. Member messages, scheduled events, and results from internal tools all pass through it. It applies the same safety and permission checks, then either starts a new turn or joins the caller to an existing live stream. This keeps conversation work orderly and recorded instead of happening as loose background activity.

The ambient reply file handles a quieter case: a message appears in a thread without directly calling on the agent. Before spending money and time on a full response, it asks a small bounded model whether the agent should speak or stay silent.

The stop file is the emergency brake. It verifies that a person is allowed to stop a running turn, cancels it safely, starts any waiting follow-up work, and notifies live listeners that the old turn ended.

## Files in this stage

### Turn Admission and Control
Decides when to create conversational work, admits eligible turn inputs through shared checks, and handles member-initiated stopping of running turns.

### `core/src/ufo/ambient_reply.py`

`domain_logic` · `request handling, before a new ambient thread reply becomes an agent turn`

In a group chat thread, not every new message is meant for the agent. After the agent has joined a thread once, later replies may be people talking to each other, thanking someone else, or asking another person for an opinion. If the system treated every such message as a full agent turn, it would spend time and money just to say something like “nothing from me.” This file puts a cheap gate in front of that.

The main idea is simple: before admitting a new message as an agent turn, the code asks a classifier model for exactly one decision: REPLY or NO_REPLY. The classifier gets the new message plus a small recent slice of thread history, because short messages only make sense in context. Each message records who said it, whether it was written by the agent, and the text.

The file is careful about safety and cost. It trims the number of history messages and the length of each message. It wraps the thread data as JSON between fence lines, so a user’s words are treated as quoted data, not as instructions to the classifier. If the model’s answer cannot be read, this code raises an error instead of silently guessing. The caller can then choose the safer fallback: admit the turn rather than accidentally ignore someone who wanted the agent.

#### Function details

##### `MeteredModel.model`  (lines 91–91)

```
def model(self) -> str
```

**Purpose**: This protocol property describes the model name that will be billed and used for the ambient reply decision. It lets this file depend on a small promise about the model object without importing the larger model-access machinery.

**Data flow**: A concrete model object supplies its configured model name. Code using the protocol reads that name and places it into the request sent to the model provider. Nothing is changed by reading it.

**Call relations**: AmbientReplyClassifier.decide relies on this property when it builds the model request. The protocol keeps the classifier loosely connected to whatever real model-access class the rest of the system provides.


##### `MeteredModel.complete`  (lines 93–93)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This protocol method describes the one-shot model call used to classify a message as REPLY or NO_REPLY. It is the narrow doorway through which this file asks the language model for a decision.

**Data flow**: A ModelRequest goes in, containing the prompt, the fenced thread data, token limits, and reasoning setting. The concrete model object sends that request to the model service and returns the model’s text answer. The classifier later reads that text to find the decision word.

**Call relations**: AmbientReplyClassifier.decide calls this method after preparing the payload. The actual implementation lives elsewhere, but this file only needs to know that it can submit a request and receive a string response.


##### `_entry`  (lines 96–101)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This helper turns one AmbientMessage into the compact dictionary form sent to the classifier model. It also shortens the message text so the prompt stays small and predictable.

**Data flow**: An AmbientMessage goes in, with a speaker, text, and a flag saying whether the agent wrote it. The function copies those fields into a plain dictionary and cuts the text down to the allowed character limit. The resulting dictionary comes out ready to be placed into the JSON payload.

**Call relations**: AmbientReplyClassifier._payload calls this helper for each recent history item and for the new message. It is the small formatting step that keeps every message represented in the same safe, simple shape before json.dumps serializes it.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 114–129)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision point: it asks the classifier model whether the agent should answer a new unmentioned or third-party-directed message. Someone would use it before starting a full agent turn, to avoid spending a full response on a message that was not meant for the agent.

**Data flow**: The new message and recent history go in. The function builds a fenced JSON payload, wraps it in a user Message, creates a ModelRequest with the fixed system instructions and cost controls, and sends it through the metered model. It then searches the model’s answer for REPLY or NO_REPLY. A readable decision comes out; if no decision word is found, it raises an error instead of pretending to know.

**Call relations**: This method is the public action of AmbientReplyClassifier. It calls AmbientReplyClassifier._payload to prepare the thread context, then constructs the external Message and ModelRequest objects needed by the model interface. It hands the request to MeteredModel.complete and converts the model’s text response back into the small decision the surrounding chat surface needs.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 131–147)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This method builds the exact text shown to the classifier model as the thread evidence. It packages the recent history and new message as JSON, then places that JSON between matching fence lines so the model can clearly tell data from instructions.

**Data flow**: The new AmbientMessage and a tuple of history messages go in. The method keeps only the latest allowed history messages, converts each message with _entry, serializes the result as compact JSON, and chooses a fence string that does not appear inside the payload. The output is one string containing the fence, the JSON, and the same fence again.

**Call relations**: AmbientReplyClassifier.decide calls this right before creating the model request. Inside, it calls _entry to normalize each message and json.dumps to turn the Python data into JSON. Its output becomes the user-facing content of the classifier request, while the system prompt supplies the decision rules.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).


### `core/src/ufo/surfaces/admission.py`

`domain_logic` · `request handling and job admission`

A “turn” is one unit of conversation work: a message comes in, the agent thinks, and a reply or final status is produced. This file makes turn creation safe and consistent. Without it, different parts of the system could accidentally skip limits, create duplicate turns, switch a conversation to the wrong agent, or race each other when several messages arrive at once.

The main idea is simple: before any work is placed on the durable DBOS queue, admission takes a lock on the conversation row in the database. That lock is like a single checkout counter: only one caller at a time can assign the next sequence number, decide whether a live turn already exists, and record what should happen.

If a conversation already has a live turn, most new messages are not given their own run. They are written into an inbound-message queue so the live turn can absorb them at a safe point. If no live turn exists, admission creates a new turn row. It then checks seats, spend limits, idempotency keys, and agent binding. Seat checks stop unseated members from speaking. Spend checks either allow the turn, park it for later, or cancel it with a message. Idempotency keys stop retries from creating duplicate turns.

For durable surfaces, admission also creates a writeback row so the eventual reply will be delivered even if the original caller is gone. Finally, only queued turns are offered to DBOS for execution.

#### Function details

##### `_refused`  (lines 83–94)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: Decides what should happen when a turn is refused by a limit or gate. If the turn already represents paid-for work, it parks the turn instead of throwing away the result; otherwise it cancels the turn with a clear message.

**Data flow**: It receives a yes-or-no flag saying whether completed work is already attached, plus the refusal text. If work must be preserved, it returns a parked status and no final reply. If not, it creates a terminal frame, which is the stored final result for a turn, and returns a cancelled status with that frame.

**Call relations**: Admission._admit calls this when a seat check or spending decision refuses a new turn. The returned status and optional terminal frame are then stored with the turn row so later readers can see whether the turn is waiting or cancelled.

*Call graph*: called by 1 (_admit); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 102–127)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: Admits a message that came from a real member-facing surface, such as a chat interface. It enforces that prepared tool intents are tied to an actual speaking member and then sends the message through the shared admission path.

**Data flow**: It receives the workspace, conversation, message body, optional speaker member, idempotency key, context, and optional intent. It first checks that an intent has a speaker and that the message body exactly matches the intent envelope. It then opens an observability span, a timing/logging wrapper for tracing work, and passes everything to Admission._admit as a member admission. It returns an Admitted result describing the turn and whether a new run opened.

**Call relations**: Admission.redispatch uses this when it needs to re-admit an old pending member message. Member-facing callers usually reach it through MemberAdmission.admit, while the heavy decision-making is delegated to Admission._admit.

*Call graph*: calls 1 internal fn (_admit); called by 1 (redispatch); 2 external calls (model_dump_json, span).


##### `Admission.redispatch`  (lines 129–178)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Gives an old pending member message another chance to become work. This is used when a member message was left waiting because its original target turn was cancelled or no longer alive.

**Data flow**: It receives a workspace and conversation. It opens a database transaction, finds the oldest unconsumed inbound member message, and locks that row so another task cannot pick the same one at the same time. If the row has no idempotency key, it stamps one onto it. Then it calls admit_member with the saved body, speaker, key, and context. If that call starts a new run, it returns the new turn id together with the arrival row id; if there was nothing to do, or the message folded into an existing turn, it returns None.

**Call relations**: This function calls Admission.admit_member so the retry follows exactly the same rules as a fresh member delivery. It reads and updates inbound_message rows through workspace_tx, and it rebuilds saved context using TurnContext.model_validate before handing the message back to admission.

*Call graph*: calls 1 internal fn (admit_member); 4 external calls (model_validate, select, update, workspace_tx).


##### `Admission.invoke`  (lines 180–245)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=Non
```

**Purpose**: Admits an internal turn, such as a scheduled fire, extension invocation, or subagent result. It can also refuse to start if a member has spoken since the caller began waiting, which prevents both a timer and a member reply from resuming the same wait.

**Data flow**: It receives the workspace, conversation, asserted agent, message body, optional idempotency key, context, member authority, scheduling flags, preservation flag, and optional member watermarks. It passes these to Admission._admit. If _admit reports that a member already superseded the wait, this function returns None; otherwise it returns the admitted turn id.

**Call relations**: AdmissionInvoker.invoke is the workspace-bound wrapper that normally calls this for jobs and extension code. This function itself does not perform the database work; it hands the whole request to Admission._admit and only translates the special superseded-member exception into a None result.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 247–631)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, o
```

**Purpose**: This is the central admission engine. It decides whether an inbound item joins a live turn, creates a new turn, gets parked, gets cancelled, or is simply recognized as a retry of something already admitted.

**Data flow**: It receives all details about the proposed turn: workspace, conversation, agent assertion, body, speaker, idempotency key, context, source type, intent, seat/spend flags, and optional wait watermarks. Inside one database transaction, it locks the conversation, verifies the agent binding, checks the speaker, looks for duplicate turn or inbound-message records, checks whether a member has superseded a wait, and looks for an existing live turn. If folding into a live turn is allowed, it writes an inbound_message row. If a new turn is needed, it assigns the next sequence number, computes a stable turn id, checks seats and spending, inserts the turn, possibly creates a durable writeback row, and decides whether this turn should be enqueued now. After the transaction, it enqueues any turn that should run immediately and returns an Admitted result with the turn id, whether a run opened, and sometimes an arrival id.

**Call relations**: Admission.admit_member and Admission.invoke both funnel into this function, which is why all callers share the same rules. It calls _refused to turn refusals into stored statuses, uses Seats and SpendEvaluator to enforce access and budget rules, creates Admitted results for callers, and calls Admission._enqueue when a queued turn should be offered to the worker queue.

*Call graph*: calls 2 internal fn (_enqueue, _refused); called by 2 (admit_member, invoke); 17 external calls (__init__, __init__, __init__, __init__, model_dump, model_validate, delete, exists, insert, select (+7 more)).


##### `Admission._enqueue`  (lines 633–673)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Places a queued turn onto the DBOS workflow queue so a worker can execute it. It also cleans up the database marker if enqueueing is interrupted or fails, so the turn can be retried later.

**Data flow**: It receives the workspace id, conversation id, turn id, and optionally a workflow id. It builds DBOS enqueue options, including the queue name, workflow name, partition key, and app version, then asks DBOS to enqueue the turn. If the task is cancelled or enqueueing raises an error, it opens a transaction and clears dispatch_enqueued_at on the queued turn. On ordinary errors it also logs that enqueueing was deferred; on cancellation it re-raises the cancellation.

**Call relations**: Admission._admit calls this after it has safely committed the turn or reawakened a parked turn. This separation matters because the database record is written first, then the external queue is notified; if notification fails, the database is marked so another dispatcher can try again.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 684–709)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=None, holds_work_alr
```

**Purpose**: Provides a workspace-bound way for internal jobs and extensions to admit turns. Because the workspace is already fixed and there is no speaker argument, callers using this capability cannot pretend their turn was directly spoken by a member.

**Data flow**: It receives a conversation, agent, message, and optional admission details such as idempotency key, context, member authority, scheduling flag, and wait watermarks. It adds the stored workspace id and forwards the request to Admission.invoke. The result is the new or existing turn id, or None if a member message already won the race.

**Call relations**: This is a thin capability wrapper around Admission.invoke. Jobs and extension workflows can be given this object instead of the full Admission object, which narrows what they are allowed to admit.


##### `MemberAdmission.admit`  (lines 720–738)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Adm
```

**Purpose**: Provides a workspace-bound way for member-facing surfaces to admit member messages. It forces every surface delivery to name the speaking member separately from the message text, so seat checks and member identity rules can be applied.

**Data flow**: It receives a conversation, message, optional idempotency key, optional context, required speaker member id, and optional prepared intent. It adds the stored workspace id and forwards the request to Admission.admit_member. It returns the Admitted result, including the turn id and whether this delivery opened a new run or joined an existing one.

**Call relations**: Surfaces use this wrapper instead of calling the full Admission object directly. It hands off to Admission.admit_member, which then validates member-specific rules and delegates the main decision to Admission._admit.


### `core/src/ufo/surfaces/stop.py`

`domain_logic` · `request handling`

A “turn” is one running unit of conversation work. This file is the stop button’s back-end path for a member-facing surface, such as a chat screen. Its job is to make stopping a turn safe and predictable, even when other things are happening at the same time.

The main class, MemberStop, first checks the database to make sure the requested turn really belongs to the conversation the surface says it belongs to. This matters because stopping the wrong turn would be like pulling the emergency brake on someone else’s train. If the turn is not part of that conversation, it refuses the request.

If the turn is valid, it asks the shared cancellation system to cancel that one turn. If the turn had already ended, the cancellation does nothing and the method reports that nothing new was stopped. This makes repeated stop clicks harmless.

When cancellation succeeds, it gives the admission system a chance to start a follow-up turn that the member may already have sent. If such a new turn is created, the file publishes a message to the hub saying which incoming message was absorbed into that new turn. Finally, it publishes a cancelled terminal message for the stopped turn, so any live stream or “tail” waiting on that turn wakes up immediately instead of waiting until it checks again.

#### Function details

##### `MemberStop.stop`  (lines 38–60)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops one running turn for a member, but only after proving that the turn belongs to the requested conversation. It cancels the turn, optionally starts the next waiting turn, and notifies live listeners that the stopped turn has ended as cancelled.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. It reads the database inside a workspace transaction to find which conversation owns that turn in that workspace. If the owner does not match the requested conversation, it raises an error. If ownership matches, it asks the shared cancellation helper to cancel the turn. If the turn was already finished, it returns a Stopped result saying nothing ended. If cancellation succeeds, it asks admission to redispatch any waiting follow-up message into a new turn. When a new turn is founded, it publishes an Absorbed hub event for that new turn. It then publishes a Terminal hub event with the standard cancelled frame for the stopped turn, and returns a Stopped result saying the stop succeeded and, if applicable, naming the new turn.

**Call relations**: This is the end-to-end stop workflow used when a surface asks to stop a member’s turn. It relies on the database transaction helper to safely check ownership, the shared cancel_one_turn primitive to perform durable cancellation, and the hub to notify live readers. It creates Stopped responses for the caller, Absorbed events when a follow-up turn is founded, and a Terminal event so listeners of the cancelled turn immediately learn that it is over.

*Call graph*: 6 external calls (__init__, __init__, __init__, select, cancel_one_turn, workspace_tx).

## 📊 State Registers Touched

- `reg-workspace-membership` — The roster of workspaces, members, admins, seats, and which people belong where.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-model-catalog` — The lookup table of available AI models, providers, routing details, capabilities, and pricing metadata.
- `reg-surface-installations` — Mappings from outside entry points like Slack, web, terminal, and hosted surfaces into workspaces, members, and agents.
- `reg-inbound-message-log` — Incoming external messages saved until they are safely rendered, deduplicated, and admitted into a conversation.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-turn-queue` — The durable waiting line and status record for each unit of agent work, from queued to running to finished or failed.
- `reg-live-hub` — The live stream state that lets browsers, terminals, and operators watch progress and reconnect without losing updates.
- `reg-scheduled-automation` — Future and repeating tasks, pauses, wakeups, and their last-run state for long-running automation.
- `reg-human-interaction-requests` — Pending user questions, approval prompts, and credential-request prompts created by tools and resumed through surfaces.
- `reg-execution-scope-context` — Per-request, per-job, and per-turn scoped context carrying the active workspace, member, agent, turn, permissions, credentials, billing, and service handles through core code.
- `reg-inflight-cancellation-handles` — Live cancellation signals, workflow handles, and parent-child cancellation propagation state for active turns and delegated work before final durable status is written.
