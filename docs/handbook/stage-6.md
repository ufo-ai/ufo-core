# Conversation ingress, turn admission, and live turn control  `stage-6`

This stage is the front door and traffic controller for a conversation. It is used when something wants to start or continue work: a user message, a scheduled wake-up, an outside event, or an internal resume request. Before the main worker can run, the system must decide whether the new “turn” belongs in the queue and whether it is safe to run now.

The admission code checks the basics once at entry: who is allowed to act, whether there is an available seat, whether spending limits allow more work, whether this delivery is a duplicate, and whether the turn is in the right order. The ambient reply code is a cost-saving filter. If a thread message does not directly call on the agent, it decides whether the agent should stay quiet instead of creating a full turn. The dispatch code is the queue’s gatekeeper. It advances the conversation one turn at a time and only hands a queued turn to the workflow runner when no other turn for that conversation is already running.

## Files in this stage

### Turn Admission and Dispatch
Validates incoming conversation work, gates ambient replies, and advances queued turns into the workflow runner.

### `core/src/ufo/runtime/surfaces/admission.py`

`domain_logic` · `request handling and turn admission`

A “turn” is one unit of conversation work: a member message, a scheduled event, or an internal result that needs the agent to respond. This file makes admission act like a guarded ticket counter. Everyone must come through the same counter, so no surface or background job can skip billing, seat checks, or conversation ordering.

The main class, Admission, locks the conversation row while it decides what to do. That lock matters because turns need sequence numbers, and only one message should get the next number at a time. If the same delivery arrives twice with an idempotency key, meaning a “same request” marker, the code joins the already-created turn or already-queued arrival instead of making a duplicate.

The file also decides whether a new message should start a fresh turn or be folded into an existing live turn. Folding means the message is stored in an inbound-message queue for the running turn to read at its next safe boundary, like adding a note to a job already on someone’s desk instead of opening a second job. If a conversation is archived, a speaker has no seat, or the spending cap blocks the work, the turn is either cancelled with a readable reason or parked for later, depending on whether cancelling would lose already-paid-for work.

Finally, when a turn is ready, this file records any durable writeback needed for surfaces that poll for replies, emits metrics, and enqueues the turn in DBOS, the workflow queue system.

#### Function details

##### `_refused`  (lines 153–164)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: This helper decides what a refusal should look like when a turn cannot run. If the turn already carries work someone paid for, it parks the turn instead of throwing the result away; otherwise it cancels the turn with a message the caller can read.

**Data flow**: It receives a flag saying whether completed work is already attached, plus the refusal text. It turns that into either a parked status with no final message, or a cancelled status with a terminal frame containing the explanation.

**Call relations**: Admission._create_turn uses this after billing or spend checks fail, and Admission._authority_refusal uses it when archive or seat rules block the turn. It is the small shared rule that keeps refusals consistent.

*Call graph*: called by 2 (_authority_refusal, _create_turn); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 175–220)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: This is the public path for a real member’s message entering a conversation. It validates member-specific inputs, admits the message through the shared admission logic, and notifies live listeners when the message was folded into an existing turn or when a surface comment was recorded.

**Data flow**: It receives workspace and conversation IDs, message text, the speaker member ID, optional duplicate key, context, prepared tool intent, comment, and runtime settings. It checks that prepared intents and comments are well formed, converts the speaker into execution authority, calls Admission._admit, then may publish hub events for queued arrivals or comments. It returns an Admitted object describing the turn and whether a new run opened.

**Call relations**: Surfaces use this as the member-message front door, and Admission.redispatch calls it when an old pending member message needs another chance. It hands the real decision to Admission._admit, then hands live notifications to the hub when needed.

*Call graph*: calls 1 internal fn (_admit); called by 1 (redispatch); 5 external calls (__init__, __init__, model_dump_json, span, authority_from_member_id).


##### `Admission.redispatch`  (lines 222–271)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: This gives an old pending member message another chance to start work after the turn it was waiting on was cancelled or left it unread. It prevents a message from being stranded forever in the inbound queue.

**Data flow**: It opens a database transaction, finds the oldest unconsumed inbound message from a member, gives it an idempotency key if it lacks one, then calls Admission.admit_member with the saved body, speaker, and context. It returns the new turn ID and arrival ID only if that message actually founded a new run; otherwise it returns None.

**Call relations**: This is a recovery path built on the normal member admission path. Rather than inventing a special queue rule, it reuses Admission.admit_member so duplicate handling and fold-or-start decisions stay identical to a redelivery.

*Call graph*: calls 1 internal fn (admit_member); 4 external calls (model_validate, select, update, workspace_tx).


##### `Admission.invoke`  (lines 273–357)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, authority: ExecutionAuthority, holds
```

**Purpose**: This is the public path for internal work to wake or start a conversation turn, such as an extension result, scheduled fire, or background job. It can also ask, safely, “has a member spoken since I started waiting?” and refuse to run if the member already answered.

**Data flow**: It receives the conversation, asserted agent, message, authority, duplicate key, context, runtime settings, and optional member-watermark numbers. It passes those to Admission._admit. If the watermark check shows the member already spoke, it returns None; otherwise it returns the admitted turn ID. Archived agents are not swallowed, because their owed work may need to resume after restore.

**Call relations**: Internal callers use this instead of pretending to be a member. It delegates all admission decisions to Admission._admit and converts the private _SupersededByMember signal into the public result None.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 359–564)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, a
```

**Purpose**: This is the central admission engine. It decides, under one conversation lock, whether an inbound item is a duplicate, should fold into a live turn, should create a new turn, should wait, should be parked, or should be cancelled.

**Data flow**: It receives all details about the inbound message or internal wake-up. It locks and reads the conversation, checks the asserted agent, verifies the speaker and timezone, handles idempotency, checks member watermarks, tries to fold into a live turn when allowed, creates a turn when needed, marks whether it can be dispatched now, records optional comments, commits the database work, and finally asks Admission._finish_admission to emit metrics and enqueue work. It returns an Admitted result.

**Call relations**: Admission.admit_member and Admission.invoke both funnel into this function. It coordinates the specialized helpers: validation, duplicate resolution, live-turn folding, turn creation, comment recording, and final queue dispatch.

*Call graph*: calls 7 internal fn (_create_turn, _deduplicate, _finish_admission, _fold_live, _guard_member_watermark, _record_comment, _validate_member_watermarks); called by 2 (admit_member, invoke); 8 external calls (__init__, __init__, __init__, exists, select, update, workspace_tx, uuid4).


##### `Admission._guard_member_watermark`  (lines 566–601)

```
async def _guard_member_watermark(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, deduped: _ExistingTurn | None, turn_watermark: int | None, arrival_watermark: int | None
```

**Purpose**: This protects waits from a race. If an internal invocation says it should only run if no member has spoken since certain sequence numbers, this function checks that promise under the same admission lock.

**Data flow**: It receives the database connection, conversation IDs, any already-deduplicated turn, and the two watermark numbers. If the call is a fresh admission with both watermarks, it searches for newer member-founded turns or newer member arrivals. If it finds one, it raises _SupersededByMember; otherwise it changes nothing.

**Call relations**: Admission._admit calls this after duplicate handling and before creating or folding new work. Admission.invoke catches the resulting _SupersededByMember and returns None, meaning the member’s reply won the race.

*Call graph*: called by 1 (_admit); 3 external calls (exists, execute, select).


##### `Admission._validate_member_watermarks`  (lines 604–608)

```
def _validate_member_watermarks(turn_watermark: int | None, arrival_watermark: int | None) -> None
```

**Purpose**: This checks that callers waiting on member activity provide both required sequence markers. A member message can appear as either a turn or an arrival folded into a live turn, so one marker alone would leave half the question unanswered.

**Data flow**: It receives the turn watermark and arrival watermark. If exactly one is present, it raises a ValueError; if both are present or both absent, it allows admission to continue.

**Call relations**: Admission._admit calls this before doing any database work. It prevents later logic in Admission._guard_member_watermark from making an incomplete safety check.

*Call graph*: called by 1 (_admit).


##### `Admission._finish_admission`  (lines 610–642)

```
async def _finish_admission(self, workspace_id: UUID, conversation_id: UUID, surface: str, turn_id: UUID, status: TurnStatus | None, admitted: Admitted, counted_source: TurnAdmissionSource | None, fol
```

**Purpose**: This performs the after-commit side effects of admission: counting a new turn and placing ready queued work onto the workflow queue. It keeps database decisions separate from queue delivery.

**Data flow**: It receives the admitted turn, its status, whether it opened a counted source, whether a parked live turn was revived, and whether this turn is first in line for dispatch. It emits an admission metric when appropriate. If a folded message revived a parked turn, it enqueues that parked turn with a fresh workflow ID. If the turn is queued and ready now, it enqueues it. It returns the same Admitted result.

**Call relations**: Admission._admit calls this after the transaction has closed. It calls Admission._enqueue only when work should actually be offered to DBOS.

*Call graph*: calls 1 internal fn (_enqueue); called by 1 (_admit); 2 external calls (emit_metric, uuid4).


##### `Admission._deduplicate`  (lines 644–761)

```
async def _deduplicate(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, idempotency_key: str | None, runtime_config: TurnRuntimeConfig | None, inbound: _In
```

**Purpose**: This resolves an idempotency key, which is a caller-provided marker saying “this is the same delivery as before.” It stops retries from creating duplicate turns or duplicate inbound messages.

**Data flow**: It receives the current database connection, conversation and agent IDs, duplicate key, runtime config, inbound data, and optional comment. Without a key, it returns the inbound unchanged. With a key, it looks for an existing turn or inbound-message row. It either returns the existing turn, returns an already-admitted result, deletes an orphaned old arrival and reuses its saved message data, or says there is no duplicate.

**Call relations**: Admission._admit calls this early, before folding or creating anything. When a duplicate already landed on a turn, this function may call Admission._record_comment so a repeated delivery can still attach the same surface comment safely.

*Call graph*: calls 1 internal fn (_record_comment); called by 1 (_admit); 10 external calls (__init__, __init__, __init__, model_validate, model_validate, replace, delete, execute, select, authority_from_member_id).


##### `Admission._fold_live`  (lines 763–927)

```
async def _fold_live(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, conversation_member_id: UUID | None, agent_id: UUID, archived: bool, member_admission: bool, authorit
```

**Purpose**: This decides whether a new inbound item should be added to the queue of the conversation’s current live turn instead of starting its own turn. Folding keeps one conversation from running two turns at the same time while still preserving every incoming message.

**Data flow**: It receives the locked database connection, conversation details, authority, billing context, duplicate key, inbound message, and comment. It finds the oldest non-terminal turn, compares runtime settings and authority, checks archive, seat, spend, and balance rules, then either returns “do not fold,” records a new inbound_message arrival, records a comment, or revives a parked turn by changing it back to queued. Its result says whether an arrival was created, whether a parked turn should be enqueued, or whether the caller must wait for the live turn.

**Call relations**: Admission._admit calls this when there is no duplicate and the admission type is allowed to fold. It may call Admission._record_comment for folded arrivals, and its result tells Admission._admit whether to create a new turn or finish with the live one.

*Call graph*: calls 1 internal fn (_record_comment); called by 1 (_admit); 15 external calls (__init__, __init__, __init__, __init__, __init__, model_validate, execute, insert, select, update (+5 more)).


##### `Admission._create_turn`  (lines 929–1077)

```
async def _create_turn(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, conversation_member_id: UUID | None, surface: str, agent_id: UUID, archived: bool, member_admission
```

**Purpose**: This creates the actual turn row when an inbound item needs its own run. It assigns the next conversation sequence number, applies authority and spending decisions, records delivery needs, and stores the initial message.

**Data flow**: It receives database connection, conversation and agent details, surface type, admission source flags, authority, duplicate key, runtime config, and inbound message. It calculates the next sequence, chooses a deterministic turn ID, inherits subagent identity when needed, checks archive/seat authority, checks spend and balance gates, chooses queued/parked/cancelled status, inserts the turn, sets a conversation title if missing, and creates a writeback row for durable surfaces. It returns the created turn ID, sequence, status, and admission source.

**Call relations**: Admission._admit calls this after duplicate and fold checks show a new turn is needed. It relies on Admission._authority_refusal and _refused for refusal policy, and its result drives later dispatch in Admission._finish_admission.

*Call graph*: calls 2 internal fn (_authority_refusal, _refused); called by 1 (_admit); 14 external calls (__init__, __init__, __init__, __init__, model_dump, execute, insert, select, update, current_traceparent (+4 more)).


##### `Admission._authority_refusal`  (lines 1079–1094)

```
async def _authority_refusal(self, connection: AsyncConnection, workspace_id: UUID, authority: ExecutionAuthority, archived: bool, member_admission: bool, holds_work_already_done: bool) -> tuple[TurnS
```

**Purpose**: This applies non-billing permission rules before a turn can run. It blocks archived apps, unknown member speakers on member surfaces, and authorities without the required seat.

**Data flow**: It receives the database connection, workspace, authority, archive flag, whether this is member admission, and whether completed work is already attached. It returns None if authority is acceptable. Otherwise it returns a status and optional terminal message, using parking when cancellation would lose already-done work.

**Call relations**: Admission._create_turn calls this before spend and balance checks. It uses _refused for archive and seat refusals, and directly creates the special terminal message for an unresolved member speaker.

*Call graph*: calls 1 internal fn (_refused); called by 1 (_create_turn); 3 external calls (__init__, __init__, authority_member_id).


##### `Admission._record_comment`  (lines 1096–1139)

```
async def _record_comment(self, connection: AsyncConnection, workspace_id: UUID, admitted: Admitted, comment: str | None, message_ref: UUID | None=None) -> Admitted
```

**Purpose**: This stores a surface comment as a mid-turn reply tied to the turn or arrival it refers to. It lets surfaces show a user-visible comment once, even when admission is retried.

**Data flow**: It receives the database connection, workspace, an Admitted result, optional comment text, and optional message reference. If there is no comment, it returns the admitted result unchanged. If there is a comment, it builds a stable comment ID, inserts the reply row only if it does not already exist, and returns an Admitted result that includes the comment ID when a new row was written.

**Call relations**: Admission._admit uses this for new or existing turns, Admission._deduplicate uses it when a duplicate maps to an existing arrival or consumed turn, and Admission._fold_live uses it for folded messages. It is the shared idempotent comment writer.

*Call graph*: called by 3 (_admit, _deduplicate, _fold_live); 3 external calls (__init__, execute, mid_turn_reply_id_for).


##### `Admission._enqueue`  (lines 1141–1188)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: This offers a queued turn to DBOS, the workflow runner. It also carefully rolls back the “dispatch was enqueued” marker if the enqueue attempt is cancelled or fails, so another attempt can try later.

**Data flow**: It receives workspace, conversation, turn ID, and optional workflow ID. It reads what queue the turn belongs on, builds DBOS enqueue options, and calls the DBOS async enqueue API. If cancellation or an error happens, it clears dispatch_enqueued_at for the still-queued turn; ordinary errors are logged instead of raised.

**Call relations**: Admission._finish_admission calls this only after admission has decided a turn is ready to run. It is the bridge from the database admission decision to the external workflow queue.

*Call graph*: called by 1 (_finish_admission); 5 external calls (select, update, workspace_tx, log, turn_queue_for).


##### `AdmissionInvoker.invoke`  (lines 1199–1229)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, authority: ExecutionAuthority, holds_work_already_don
```

**Purpose**: This is a workspace-bound wrapper for internal callers. It lets jobs and extension workflows invoke conversation work without giving them the power to claim a message was spoken by a member.

**Data flow**: It receives a conversation, agent, message, duplicate key, context, authority, wait watermarks, and runtime settings. It fills in the stored workspace ID and forwards everything to Admission.invoke. The output is the admitted turn ID, or None if a member superseded the wait.

**Call relations**: This object is handed to internal systems as a limited capability. Its only job is to call Admission.invoke with the workspace already fixed.


##### `MemberAdmission.admit`  (lines 1240–1262)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: This is a workspace-bound wrapper for surfaces that accept member messages. It ensures those surfaces can only admit messages through the member path, where member identity and seat rules apply.

**Data flow**: It receives a conversation, message, speaker member ID, duplicate key, context, optional prepared intent, comment, and runtime settings. It fills in the stored workspace ID and forwards the request to Admission.admit_member. It returns the Admitted result from the shared admission system.

**Call relations**: Surface code receives this narrower capability instead of the full Admission object. It delegates to Admission.admit_member so all member messages go through the same checks.


##### `ConnectResume.resume`  (lines 1290–1325)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: This admits the result of an account-connection callback back into the conversation that requested it. It reports whether that resume message was accepted, while avoiding a special prepared-intent lane where free-text resume messages would create unread work.

**Data flow**: It receives the conversation, resume message, speaker member ID, and duplicate key. It looks up the most recent turn’s admission source. If the lane is for prepared intents, it logs a decline and returns False. Otherwise it reads the current workspace, tries to admit the message as the member, returns True on success, and logs and returns False on failure.

**Call relations**: Connect callbacks use this after an external browser consent flow completes. It calls Admission.admit_member for normal conversations, and uses logging plus a boolean result so the callback page can avoid promising work that was not actually admitted.

*Call graph*: 4 external calls (select, workspace_tx, log, ws_current).


### `core/src/ufo/runtime/turns/ambient_reply.py`

`domain_logic` · `request handling, before admitting a new ambient thread message as an agent turn`

In a group chat thread, once the agent has participated, later messages can be ambiguous. Someone might be replying to the agent, or two humans might simply be talking to each other. If every such message became a full agent turn, the system could spend real money just to decide it had nothing useful to say. This file prevents that by making a smaller, cheaper decision first: should this new message earn a reply at all?

The main idea is like a receptionist screening calls before forwarding them. The file packages the recent thread history and the new message into a small JSON bundle. Each message records who spoke, whether it was the agent itself, and the text. The classifier then asks a model to answer with exactly one word: `REPLY` or `NO_REPLY`.

There are important safety and cost limits. Only the last few messages are included, and each message is shortened to a fixed character limit. But the new message itself is not silently truncated; if it is too long, the classifier raises an error so the caller can choose the safer path, which is to admit the turn rather than risk ignoring someone. The prompt also treats chat text as untrusted data, so a user cannot smuggle instructions into the history and control the classifier.

#### Function details

##### `MeteredModel.model`  (lines 95–95)

```
def model(self) -> str
```

**Purpose**: This protocol property represents the name of the model that will be billed and used for the ambient-reply decision. Code using the classifier relies on this so it can build a complete model request without knowing the concrete model-access object.

**Data flow**: The concrete model wrapper provides a model name → the classifier reads that name → the name is placed into the outgoing model request.

**Call relations**: This is part of the small interface that `AmbientReplyClassifier` expects. When `AmbientReplyClassifier.decide` prepares its one-shot classification request, it reads this property to say which model should answer.


##### `MeteredModel.complete`  (lines 97–97)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This protocol method represents the actual one-shot call to the language model. The classifier uses it to ask for the single decision word, rather than creating a full agent turn.

**Data flow**: A prepared `ModelRequest` goes in → the concrete model service sends it to the chosen model and waits for text → the returned text comes back to the classifier for interpretation.

**Call relations**: This is the handoff point from local decision-building code to the external model layer. `AmbientReplyClassifier.decide` calls it after building the prompt and payload, then reads the answer to find `REPLY` or `NO_REPLY`.


##### `_entry`  (lines 100–105)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This helper turns one chat message into the compact dictionary shape sent to the classifier model. It keeps only the speaker, whether the agent wrote it, and a bounded slice of the text.

**Data flow**: An `AmbientMessage` goes in → its speaker, ownership flag, and text are copied into a plain dictionary → the text is cut down to the configured maximum length before being included.

**Call relations**: `AmbientReplyClassifier._payload` calls this for each history message and for the new message. It is the small formatting step that keeps the model input consistent and size-limited.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 118–136)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision function. It asks the cheap classifier model whether an unmentioned thread message should become a real agent turn, and returns either `REPLY` or `NO_REPLY`.

**Data flow**: The new message and recent thread history go in → the function first rejects a too-long new message rather than making a decision from a shortened version → it builds a fenced JSON payload, sends a model request with the fixed system instructions, and receives free-form model text → it searches that text for the allowed decision words and returns the last one found. If the model fails to give a readable decision, it raises an error instead of guessing.

**Call relations**: This is called by the surrounding turn-admission code when a message arrives that does not clearly name the agent. It relies on `AmbientReplyClassifier._payload` to prepare safe input, constructs the chat `Message` and `ModelRequest`, and then hands the request to `MeteredModel.complete`. Its errors are intentional: the caller can then choose the safer expensive path, admitting the turn rather than staying silent by mistake.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 138–154)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This builds the exact text sent as the user message to the classifier model. It wraps the thread data as JSON between fence lines, so the model can tell the difference between the system instructions and the users’ chat text.

**Data flow**: The new message and history go in → the function keeps only the most recent configured number of history messages → each message is converted with `_entry` → the result is serialized as compact JSON → a fence label is chosen and lengthened if needed so it does not already appear inside the payload → the final fenced text comes out.

**Call relations**: `AmbientReplyClassifier.decide` calls this right before making the model request. This function calls `_entry` to normalize each message and `json.dumps` to turn the structured data into text the model can read safely.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).


### `core/src/ufo/runtime/turns/dispatch.py`

`orchestration` · `between turns, when a workflow exits or a dispatcher sweep offers the next queued turn`

A conversation can have several turns waiting, but they must not all run at once. This file is the small handoff point that says, in effect, “the current turn is done, so start the next one if it is safe.” Without it, two turns from the same conversation could run out of order, or a waiting turn might never be started.

The main function, `dispatch_next_turn`, first opens a database transaction. A transaction is a protected database operation where related reads and writes happen together. It locks the conversation row, like taking a ticket at a service desk, so two dispatchers cannot make the same decision at the same time. It then checks whether any turn in that conversation is already marked as running. If so, it stops.

If nothing is running, it looks for the earliest queued turn. If there is no queued turn, or that turn has already been marked as offered to the workflow system, it stops. Otherwise it stamps the turn with a dispatch time in the database. After leaving the transaction, it asks DBOS, the workflow execution system, to enqueue the turn on the right queue.

One important detail is failure recovery. If enqueueing fails, the function clears the dispatch stamp so the turn can be tried again later, then logs that the enqueue was deferred. This keeps the database and workflow queue from drifting apart permanently.

#### Function details

##### `dispatch_next_turn`  (lines 28–98)

```
async def dispatch_next_turn(client: DBOSClient, conversation_id: UUID) -> None
```

**Purpose**: This function offers the next queued turn of a conversation to the workflow runner, but only if no turn in that same conversation is currently running. It is used at the handoff point after a turn finishes, parks, fails, or is otherwise no longer the active workflow.

**Data flow**: It receives a DBOS client, which can enqueue workflow work, and a conversation ID. It reads the database under a conversation lock, checks for a running turn, then finds the earliest queued turn. If it finds one that has not already been offered, it writes a dispatch timestamp to that turn, builds enqueue options such as the queue name and workflow ID, and sends the turn ID and workspace ID to DBOS. If the enqueue request fails, it reopens the database, removes the dispatch timestamp for that queued turn, updates its timestamp, and writes a log message so the system can try again later.

**Call relations**: This is the single handoff helper used when a conversation is ready to advance to its next turn. Inside the handoff, it uses `workspace_tx` to make safe database changes, SQLAlchemy `select` and `update` calls to inspect and stamp turn rows, `turn_queue_for` to choose the correct workflow queue, and `DBOSClient.enqueue_async` to actually place the work on that queue. If a previously claimed turn needs a fresh workflow identity, it uses `uuid4`; if enqueueing fails, it reports the deferred work through the logging helper.

*Call graph*: 7 external calls (enqueue_async, select, update, workspace_tx, log, turn_queue_for, uuid4).

## 📊 State Registers Touched

- `reg-database-schema` — The durable database layout and connection layer used to store and retrieve system records safely.
- `reg-workspace-directory` — The saved list of workspaces, members, agents, admins, and workspace-level settings.
- `reg-auth-sessions` — The sign-in state and signed tokens that prove who a web, surface, or API request belongs to.
- `reg-acting-authority` — The shared record of whether work is acting as a member, an agent, or only the workspace.
- `reg-access-subjects` — The shared visibility rules that say which members or audiences may read conversations, sources, and objects.
- `reg-billing-ledger` — The shared meter and wallet state for usage costs, spend caps, prepaid balances, and billing identity.
- `reg-conversation-transcript` — The saved conversation history, turns, compactions, titles, audiences, and generated references.
- `reg-turn-queue` — The durable queue of conversation turns waiting, running, parked, resumed, or blocked as duplicates.
- `reg-live-workflow-state` — The shared run-state for active turns, including locks, progress, retry guards, cancellation, and completion markers.
- `reg-turn-runtime-config` — The per-turn saved runtime settings that must survive retries and keep a turn using the same execution choices.
- `reg-scheduled-jobs` — The durable background work list for timers, recurring conversations, monitors, reports, and long-running tasks.
- `reg-surface-routing` — The saved routing state that maps web, Slack, iMessage, terminal, and other surfaces to workspaces and agents.
- `reg-inbound-message-queue` — The durable inbox of external messages waiting to be admitted into conversations exactly once.
- `reg-observability-trace` — The tracing, metrics, health, logs, and saved step history used to understand what the system did.
- `reg-runtime-message-bus` — Shared Redis/pub-sub or message-hub connection state used to coordinate live updates, workers, and cross-process runtime events.
