# Turn admission, cancellation, and live subscription setup  `stage-7`

This stage is the front door for work before the main agent loop begins, and it also handles “stop” requests while work is running. Every new member message, scheduled wake-up, and internal call enters through the admission path. admission.py checks permissions, finds or creates the right conversation, decides whether to join an existing turn or queue a new one, and prepares the work for execution.

ambient_reply.py is a small gatekeeper for group threads. If someone speaks without directly calling the agent, it decides whether the agent should answer or stay quiet, avoiding wasted turns when people are just talking to each other.

While a turn runs, the live frame and terminal transport stage acts like a viewing window. It sends partial answers, tool updates, terminal data, and final status to listeners, and lets them reconnect without missing the ending.

If a member presses stop, stop.py checks that the request is allowed, uses cancellation.py to safely tell the outside workflow system to halt, records the cancellation, notifies listeners, and can start a follow-up turn if needed.

## Sub-stages

- [Live frame and terminal transport](stage-7.1.md) `stage-7.1` — 4 files

## Files in this stage

### Admission filtering and turn creation
Shared admission logic decides whether a message merits a turn and routes incoming messages, wakeups, and extension calls through consistent checks before queueing work.

### `core/src/ufo/ambient_reply.py`

`domain_logic` · `request handling, before admitting a new ambient thread reply as an agent turn`

In a group chat thread, the agent may have already spoken once. After that, new messages can arrive that mention nobody, or mention someone else. Without a gate like this, every such message could become a full agent turn, even if the right answer is silence. This file makes a cheaper first decision: should the agent reply at all?

The file defines a small message shape, `AmbientMessage`, containing who spoke, whether it was the agent, and the text. It also defines a `MeteredModel` interface: any model plugged in here must say what model name it uses and must be able to complete one request. The main worker is `AmbientReplyClassifier`. It builds a compact view of the recent thread, sends it to a language model with strict instructions, and expects exactly one decision word: `REPLY` or `NO_REPLY`.

The recent thread is deliberately limited: only the last few messages are included, and each message is shortened. This keeps cost and risk under control. The thread is sent as JSON, like putting the chat transcript in a sealed envelope, so a user’s text is treated as data rather than as instructions to the classifier. If the model answer cannot be read, this code raises an error instead of silently choosing. The caller can then choose the safer fallback: admit the turn rather than miss a real request.

#### Function details

##### `MeteredModel.model`  (lines 94–94)

```
def model(self) -> str
```

**Purpose**: This property promises that any model object used here can report the name of the actual language model it will call. The classifier needs that name when it builds the request.

**Data flow**: The classifier reads this property from the supplied model object. It gets back a model name string, which is copied into the request sent to the model provider.

**Call relations**: This is part of the `MeteredModel` protocol, which is like a contract for compatible model objects. `AmbientReplyClassifier.decide` relies on this contract when it prepares the one-shot classification call.


##### `MeteredModel.complete`  (lines 96–96)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This method promises that the supplied model object can take a model request and return the model’s text answer. Here, that answer should contain the decision word for whether to reply.

**Data flow**: A `ModelRequest` goes in, containing the system instructions, the thread payload, token limits, and reasoning setting. The model provider processes it and returns text, which the classifier later searches for `REPLY` or `NO_REPLY`.

**Call relations**: This is the other half of the `MeteredModel` contract. `AmbientReplyClassifier.decide` calls it after building the request, then interprets the returned text as the gate’s decision.


##### `_entry`  (lines 99–104)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This helper turns one `AmbientMessage` into the simple dictionary form that will be placed in the JSON payload. It also trims the message text so one long chat message cannot make the classifier request too large or too expensive.

**Data flow**: An `AmbientMessage` goes in, with a speaker, text, and an `own` flag saying whether the agent wrote it. A plain dictionary comes out with the same speaker and ownership flag, plus the text shortened to the configured character limit.

**Call relations**: `AmbientReplyClassifier._payload` calls this for each recent history message and for the new message being judged. It is the small formatting step before the whole thread is serialized as JSON.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 117–133)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision function. It asks the cheap classifier model whether the agent should answer the new ambient message, then returns either `REPLY` or `NO_REPLY`.

**Data flow**: It receives the new message and recent thread history. It builds a safe payload from them, wraps that payload in a model request with the classifier instructions, and sends it through the configured metered model. It then scans the model’s answer for a valid decision word and returns the last one it finds. If no valid word appears, it raises an error instead of guessing.

**Call relations**: A caller uses this before creating a full agent turn for an unmentioned reply. Inside, it calls `AmbientReplyClassifier._payload` to prepare the thread, constructs the chat `Message` and `ModelRequest`, then hands the request to `MeteredModel.complete`. Its result is the admission gate for the new turn.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 135–151)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This function packages the recent thread and the new message into a compact, safe JSON block for the classifier model. Its goal is to give the model enough context to decide without letting chat text masquerade as instructions.

**Data flow**: It receives the new message and the full available history. It keeps only the most recent configured number of history messages, converts each message with `_entry`, serializes everything as JSON, and surrounds it with matching fence lines. If the fence text already appears inside the JSON, it lengthens the fence until it is unique. The result is one string ready to place in the model request.

**Call relations**: `AmbientReplyClassifier.decide` calls this right before asking the model for a decision. `_payload` calls `_entry` for message formatting and `json.dumps` for serialization, then hands the finished string back to `decide` as the user-facing content of the classifier request.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).


### `core/src/ufo/surfaces/admission.py`

`domain_logic` · `request handling and turn admission`

A "turn" is one unit of conversation work: a message comes in, the agent thinks, and a reply or result is produced. This file decides whether a new turn should be created, whether the message should be folded into an already-running turn, or whether the work must be parked or cancelled. It is like a reception desk with one ledger: every visitor is recorded in order, checked against the rules, and either sent to a room, asked to wait, or turned away with a reason.

The main rules live in `Admission._admit`. It locks the conversation row in the database so two messages cannot claim the same sequence number at the same time. It checks that the conversation is still bound to the expected agent, checks idempotency keys so repeated deliveries do not create duplicate turns, checks member seats, and asks the spend evaluator whether the workspace is allowed to spend more. If another turn is already live, a normal incoming message is stored as an inbound arrival for that live turn instead of starting separate work.

When a turn is accepted and ready to run, this file writes the turn row, optionally writes a durable delivery row, and enqueues the turn in DBOS, the background workflow system. The wrapper classes give safer, narrower entry points: surfaces can admit member messages, while internal jobs can invoke turns without pretending to be a member.

#### Function details

##### `_refused`  (lines 87–98)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: This helper decides what should happen when a turn is not allowed to proceed. If the turn has not already produced valuable work, it creates a cancelled result with a clear message; if it carries already-paid-for work, it parks the turn instead of throwing that work away.

**Data flow**: It receives a yes-or-no flag saying whether work has already been done, plus the refusal message. If work must be preserved, it returns a parked status and no final reply. Otherwise it returns a cancelled status and a terminal frame containing the explanation.

**Call relations**: The main admission flow calls this when a seat check or spend check refuses work. Its answer tells `Admission._admit` whether to write the new turn as parked for later retry or as cancelled so the caller can see the refusal.

*Call graph*: called by 1 (_admit); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 106–131)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: This is the public entry point for admitting a message spoken by a member. It validates that any prepared tool intent matches the actual message body, then sends the request into the shared admission path.

**Data flow**: It takes the workspace, conversation, message text, speaker member id, optional idempotency key, context, and optional intent. It checks that intents really came from a known speaker and that the serialized intent matches the message body. Then it opens an observability span and returns the `Admitted` result produced by `_admit`.

**Call relations**: Surfaces use this path when a real member speaks, and `Admission.redispatch` also uses it when it re-admits an older pending member message. It hands the real decision-making to `Admission._admit`, marking the admission as member-originated so member-only rules apply.

*Call graph*: calls 1 internal fn (_admit); called by 1 (redispatch); 2 external calls (model_dump_json, span).


##### `Admission.redispatch`  (lines 133–182)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: This function gives an older pending member message another chance to start work. It is used when a message was left waiting and now needs to be re-admitted safely without risking a duplicate.

**Data flow**: It opens a database transaction, finds the oldest unconsumed inbound message in the conversation that came from a member, and locks that row. If the message has no idempotency key, it stamps one onto it. Then it calls `admit_member` with the stored body, speaker, key, and context. It returns the turn id and arrival id only if this re-admission opened a new run; otherwise it returns nothing.

**Call relations**: This function sits between the stored inbound-message queue and normal member admission. Instead of inventing a special path, it routes the old message through `Admission.admit_member`, so the same seat, spend, idempotency, and folding rules apply as if the member had resent it.

*Call graph*: calls 1 internal fn (admit_member); 4 external calls (model_validate, select, update, workspace_tx).


##### `Admission.invoke`  (lines 184–255)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=Non
```

**Purpose**: This is the public entry point for internal work that wants to wake or start a turn. It is used by jobs, scheduled fires, extension workflows, or subagent results rather than by a member directly.

**Data flow**: It receives the workspace, conversation, expected agent, message body, optional idempotency key, context, and several flags that describe why the internal work is being admitted. It passes these values to `_admit`. If `_admit` reports that a member already spoke after the caller began waiting, this function returns `None`; otherwise it returns the admitted turn id.

**Call relations**: Internal callers use this instead of `admit_member` so they cannot claim to be a speaking member. It delegates the shared admission rules to `Admission._admit`, and it translates the private `_SupersededByMember` signal into the simpler result `None`.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 257–686)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, o
```

**Purpose**: This is the central admission engine. It decides whether an incoming message creates a new turn, joins a live turn, reuses an existing idempotent turn, gets parked, gets cancelled, or gets queued to run.

**Data flow**: It receives all information about the attempted admission: workspace, conversation, agent assertion, message body, speaker identity, idempotency key, context, and special internal flags. Inside one database transaction, it locks the conversation, verifies the agent binding, validates the speaker, updates the speaker timezone when provided, checks for prior work with the same idempotency key, optionally refuses if a member has spoken since a waiting watermark, looks for a live turn to fold into, checks seats, checks spend limits, assigns sequence numbers, writes turn or inbound-message rows, and records durable writeback rows when needed. After the transaction, it enqueues queued work when appropriate and returns an `Admitted` object describing what happened.

**Call relations**: Both member admission and internal invocation funnel into this function. It calls `_refused` to turn seat or spend denials into parked or cancelled outcomes, and it calls `_enqueue` after the database commit when a turn should actually run. It is the file’s traffic controller: all narrower entry points exist mainly to prepare safe inputs for this shared decision point.

*Call graph*: calls 2 internal fn (_enqueue, _refused); called by 2 (admit_member, invoke); 18 external calls (__init__, __init__, __init__, __init__, model_dump, model_validate, delete, exists, insert, select (+8 more)).


##### `Admission._enqueue`  (lines 688–728)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: This function places an admitted queued turn onto the DBOS workflow queue so a worker can run it. It also repairs the database marker if enqueueing is interrupted or fails.

**Data flow**: It receives the workspace id, conversation id, turn id, and optionally a workflow id. It builds queue options, using the conversation as the partition key so work for the same conversation is ordered. It asks DBOS to enqueue the turn. If the task is cancelled or enqueueing throws an error, it clears the turn’s `dispatch_enqueued_at` marker in the database so another attempt can try later; non-cancellation errors are also logged.

**Call relations**: `Admission._admit` calls this only after the admission transaction has decided a queued turn should be dispatched. `_enqueue` is deliberately separate from the database writes so the system can commit the durable admission first, then make a best-effort queue offer and recover if that offer fails.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 739–764)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=None, holds_work_alr
```

**Purpose**: This is a workspace-bound wrapper for internal turn invocation. It lets jobs and extension workflows start or wake turns in one workspace without being able to choose a different workspace or impersonate a member speaker.

**Data flow**: It receives the conversation, agent, message, optional idempotency key, context, and internal admission options. It adds the wrapper’s stored workspace id and forwards everything to `Admission.invoke`. It returns the turn id from that call, or `None` if the invocation was superseded by a member message.

**Call relations**: Code that should have internal-invocation power gets this wrapper rather than the full `Admission` object. The wrapper narrows what the caller can do, then hands the actual admission decision to `Admission.invoke`.


##### `MemberAdmission.admit`  (lines 775–793)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Adm
```

**Purpose**: This is a workspace-bound wrapper for admitting member messages from a surface. It ensures surface code can submit only member-originated messages and must provide the speaker identity.

**Data flow**: It receives the conversation, message, optional idempotency key, context, required speaker member id, and optional intent. It adds the wrapper’s stored workspace id and forwards the request to `Admission.admit_member`. It returns the resulting `Admitted` object, which says whether a new run opened or the message joined an existing turn.

**Call relations**: Surfaces receive this limited capability instead of the full admission service. It keeps surface admissions on the member path, where `Admission.admit_member` and then `Admission._admit` apply member-specific checks such as speaker resolution and seat gating.


### Cancellation and stop handling
Stop requests use the shared cancellation path, notify live listeners, and may create a follow-up turn from already-sent member input.

### `core/src/ufo/cancellation.py`

`domain_logic` · `cancellation handling`

A “turn” is a unit of work that may be running as a durable DBOS workflow, meaning DBOS remembers and can recover that work even after failures. Cancelling is tricky because a turn can have child turns, and stopping one workflow does not automatically stop its descendants. This file deliberately solves only one small, important problem: cancel exactly one turn in a safe order.

The key rule is “cancel first, write cancelled second.” In everyday terms, it is like first calling the delivery driver to stop the trip, and only then marking the order as cancelled in the shop’s records. If the system crashes between those two steps, the database still shows the turn as unfinished, so later repair code can try again. What it avoids is the dangerous opposite: a database row that says “cancelled” even though the workflow was never told to stop.

The function first checks the turn row in the database. If the turn is missing or already finished, it leaves it alone. Otherwise it asks DBOS to cancel the workflow for that turn id. Then it updates the database row to a cancelled terminal state, but only if the row is still unfinished. This protects against races where the turn finishes normally at the same time someone tries to cancel it. Finally, if the cancellation really changed the row, it emits a metric so monitoring can count cancelled turns.

#### Function details

##### `cancel_one_turn`  (lines 24–73)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> bool
```

**Purpose**: Cancels one turn safely and records that cancellation only after DBOS has been asked to stop the durable workflow. It returns true only when this call actually changed the turn from unfinished to cancelled.

**Data flow**: It receives a DBOS client and a turn id. It reads the turn’s current status, profile, and parent id from the database; if the turn does not exist or is already finished, it returns false. If the turn is still active, it asks DBOS to cancel the workflow with that turn id, then writes a cancelled terminal record back to the turn row if it is still active. If that database write succeeds, it emits a cancellation metric using the turn’s profile information and returns true; if another process already finished the turn first, it returns false.

**Call relations**: All cancellation paths are meant to use this function when they need to stop a single turn. Inside, it opens database transactions with `ufo.db.workspace_tx`, builds its read and write statements with SQLAlchemy, asks `DBOSClient.cancel_workflow_async` to stop the durable workflow, and reports the result through `ufo.o11y.emit_metric` after formatting the profile with `ufo.o11y.turn_profile`.

*Call graph*: 6 external calls (cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile).


### `core/src/ufo/surfaces/stop.py`

`orchestration` · `request handling`

This file is the “stop button” path for a conversation turn. A turn is one running stretch of work in a conversation. When a member asks to stop it, the system must be careful: it should only stop a turn that really belongs to that member’s conversation, it must record the cancellation durably, and it must wake up anyone watching the turn so they do not keep waiting.

The main piece is `MemberStop`, which is given three collaborators: a DBOS client for durable background-work control, a hub for publishing live updates, and an admission object that can start or “redispatch” a follow-up turn if there is already a pending member message.

The flow is deliberately ordered. First it looks in the database to confirm that the given turn belongs to the given conversation in the given workspace. This prevents one conversation from stopping another conversation’s work. Then it asks the shared cancellation primitive to cancel that one turn. If the turn had already finished, nothing more is done; this makes repeated stop presses harmless.

If cancellation succeeds, it may start a replacement turn for a pending message. If that happens, it publishes an “absorbed” notice for the message that founded the new turn. Finally, it publishes a cancelled terminal frame for the stopped turn. That final notice is like turning on the lights in a waiting room: any live stream or “tail” watching the old turn wakes up immediately and sees that it ended by cancellation.

#### Function details

##### `MemberStop.stop`  (lines 38–60)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops one running turn on behalf of a member, but only after proving the turn belongs to the requested conversation. It records the cancellation, starts a follow-up turn if a pending member message should continue the conversation, and tells live listeners that the old turn is cancelled.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. It first reads the database to find which conversation owns that turn in that workspace. If the owner does not match the requested conversation, it raises an error instead of cancelling anything. If the turn is valid, it asks the shared cancellation routine to cancel it. If cancellation says the turn was already finished, it returns a `Stopped` result saying no new ending happened. If cancellation succeeds, it asks admission to redispatch any pending follow-up message. When a new turn is founded, it publishes a notice tying that new turn to the message it consumed. It then publishes a cancelled terminal notice for the stopped turn and returns a `Stopped` result that says the turn ended and includes the new turn ID if one was created.

**Call relations**: This is the end-to-end stop workflow used by the member-facing surface. It opens a workspace database transaction to check ownership, hands the actual durable cancellation to the shared `cancel_one_turn` primitive, asks `Admission` to create the next turn when needed, and uses the hub to broadcast both the new turn’s founding message and the old turn’s cancelled ending. The cancelled terminal is published last so listeners of the stopped turn wake up only after any replacement turn is already visible.

*Call graph*: 6 external calls (__init__, __init__, __init__, select, cancel_one_turn, workspace_tx).

## 📊 State Registers Touched

- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-transcript-state` — The saved message history and transcript snapshots that are read, compacted, updated, audited, and shown later.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-cancellation-state` — The shared stop-and-recovery state used to cancel running turns and prevent abandoned work from continuing.
- `reg-live-delivery` — The live stream and delivery state for partial replies, tool updates, terminal output, final status, and missed messages.
- `reg-runtime-fleet` — The records of which runtime processes and workers are alive, what they own, and when they last checked in.
- `reg-surface-ingress` — The shared records that connect external surfaces like web, Slack, shell, OAuth, and inbound messages to conversations and replies.
- `reg-scheduled-work` — The saved jobs, scheduled tasks, pauses, monitors, due times, retry state, and duplicate-run guards.
- `reg-spend-controls` — The spend caps, prepaid balances, price table fingerprints, and checks that decide whether work may continue.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-ephemeral-cache-bus` — The selected Redis/cache/pub-sub backend and its ephemeral keys, locks, and connection state used to coordinate live delivery, workers, and shared runtime services.
