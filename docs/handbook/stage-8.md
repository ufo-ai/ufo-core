# Turn admission, durable queuing, and live update streams  `stage-8`

This stage is the traffic control room for conversations. A “turn” means one unit of agent work, such as answering a user message or reacting to an internal event. When something new arrives, admission.py is the trusted front door. It checks that the account can use the system, the delivery is not a duplicate, the right agent is attached, and only one turn for the conversation runs at a time. ambient_reply.py adds a social filter for group chats: if someone replies in a thread without clearly inviting the agent, it can skip starting a costly unwanted turn.

Once a turn is waiting, dispatch.py decides when it is safe to start the next one and sends it to the background worker queue. While the turn runs, hub.py broadcasts live progress, like a radio channel for text, status, costs, and results. hub_tail.py helps clients follow that channel reliably, checking storage too so late or reconnecting viewers still see the ending. stream_hub.py extends the same live updates across many server processes using Redis Streams.

## Files in this stage

### Turn admission gates
These files decide whether an incoming message or ambient thread activity should become a queued turn.

### `core/src/ufo/runtime/surfaces/admission.py`

`orchestration` · `request handling and background turn admission`

A “turn” is one unit of work for an agent in a conversation: a member message, a scheduled event, or an internal follow-up. This file decides whether that turn may start now, must wait, should be merged into a turn already running, or must be refused. Without this single admission point, different callers could accidentally bypass spend limits, start two turns in the same conversation at once, switch a conversation to the wrong agent, or create duplicate work when a message is retried.

The main class, Admission, works like a guarded reception desk. It locks the conversation row in the database so only one admission decision assigns the next sequence number at a time. It first checks whether the request is a duplicate, then checks member “watermarks” used to avoid races between timers and human replies. If a turn is already live, it usually stores the new message as an inbound arrival for that live turn instead of starting a second run. If no live turn can take it, it creates a new turn row, applies seat and spending rules, registers durable writeback when needed, and enqueues runnable work into DBOS, the background workflow queue.

Small wrapper classes expose safer versions of this power: surfaces can only admit member messages, while internal jobs can invoke turns without pretending to be members.

#### Function details

##### `_refused`  (lines 166–177)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: Decides what a refusal should mean for a turn. If the system already accepted and paid for work, it parks the turn so it can resume later; otherwise it cancels the turn with a readable explanation.

**Data flow**: It receives a flag saying whether work has already been done and a refusal message. It turns that into either a parked status with no final reply, or a cancelled status with a terminal frame containing the message.

**Call relations**: Admission._create_turn and Admission._authority_refusal call this when seats, billing, or caps say a turn cannot proceed. It supplies the final status those callers write into the turn row.

*Call graph*: called by 2 (_authority_refusal, _create_turn); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 188–232)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: Admits a message that came from a member-facing surface, such as a chat or panel. It validates that prepared intents and comments are well formed, then sends the message through the shared admission path.

**Data flow**: It receives workspace and conversation identifiers, the message body, the speaker member, optional duplicate key, context, prepared intent, comment, and runtime settings. It converts the speaker into execution authority, calls the core admission routine, wakes any live turn that received the message, optionally publishes the comment to the hub, and returns an Admitted result telling the surface what happened.

**Call relations**: This is the public member-message entrance into Admission._admit. After _admit commits the database changes, it calls Admission._wake_live_turn so listeners learn about folded arrivals, and it may publish a Reply frame for surface comments.

*Call graph*: calls 2 internal fn (_admit, _wake_live_turn); 4 external calls (__init__, model_dump_json, span, authority_from_member_id).


##### `Admission.redispatch`  (lines 234–340)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID, ended_turn_id: UUID) -> UUID | None
```

**Purpose**: Re-admits an inbound message that was left waiting after a turn ended. This keeps messages from being lost when the turn they arrived on failed, stopped, or ended before consuming them.

**Data flow**: It reads the ended turn and the oldest still-unconsumed inbound message from the database. If needed, it stamps that message with a redispatch idempotency key, rebuilds its context and runtime settings, calls the shared admission routine as already-accepted work, wakes any live turn, and returns the id of a newly opened turn or None if the message only folded into an existing turn or nothing was pending.

**Call relations**: Workflow exits and member stop paths use this to continue conversation work after a turn ends. It hands the actual decision back to Admission._admit, then uses Admission._wake_live_turn and, for member-founded redispatches, publishes an Absorbed frame to the hub.

*Call graph*: calls 2 internal fn (_admit, _wake_live_turn); 8 external calls (__init__, model_validate, model_validate, select, update, workspace_tx, authority_from_member_id, turn_authority).


##### `Admission.invoke`  (lines 342–428)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, authority: ExecutionAuthority, holds
```

**Purpose**: Admits an internal turn, such as a scheduled fire, extension callback, or subagent result. It preserves the authority of the work that caused the invocation, so internal work cannot silently gain or lose permissions.

**Data flow**: It receives the target conversation and asserted agent, message, idempotency key, context, authority, admission options, optional member-wait watermarks, and runtime settings. It calls the shared admission routine; if a member has spoken after the supplied watermarks, it returns None instead of starting work. Otherwise it wakes any live turn and returns the admitted turn id.

**Call relations**: Internal jobs and extension workflows call this rather than admit_member. It delegates all real admission decisions to Admission._admit and uses Admission._wake_live_turn afterward for arrivals folded into a live run.

*Call graph*: calls 2 internal fn (_admit, _wake_live_turn).


##### `Admission._wake_live_turn`  (lines 430–436)

```
async def _wake_live_turn(self, admitted: Admitted) -> None
```

**Purpose**: Notifies a running turn that a new arrival was queued for it. This lets a live stream or worker notice the message after the database transaction has safely committed.

**Data flow**: It receives an Admitted result. If that result names an arrival that did not open a new run, and a hub is available, it publishes an ArrivalQueued event for the turn; otherwise it does nothing.

**Call relations**: Admission.admit_member, Admission.invoke, and Admission.redispatch call this after the admission commit. It is the small bridge from durable database state to live in-memory listeners.

*Call graph*: called by 3 (admit_member, invoke, redispatch); 1 external calls (__init__).


##### `Admission._admit`  (lines 438–644)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, a
```

**Purpose**: Performs the central admission decision for every kind of inbound work. It is where duplicate keys, agent binding, member checks, folding into live turns, turn creation, queue ordering, comments, and final enqueue decisions come together.

**Data flow**: It receives all details about the proposed turn or message. Inside one workspace database transaction, it locks the conversation, checks the bound agent and archived state, validates the speaker, deduplicates retries, checks member-wait watermarks, tries to fold into a live turn, creates or reuses a turn if needed, marks whether it may dispatch now, records comments, and then finishes by emitting metrics and enqueueing runnable work outside the transaction.

**Call relations**: Admission.admit_member, Admission.invoke, and Admission.redispatch all feed into this method. It coordinates Admission._validate_member_watermarks, _deduplicate, _guard_member_watermark, _fold_live, _create_turn, _record_comment, and _finish_admission in that order as needed.

*Call graph*: calls 7 internal fn (_create_turn, _deduplicate, _finish_admission, _fold_live, _guard_member_watermark, _record_comment, _validate_member_watermarks); called by 3 (admit_member, invoke, redispatch); 8 external calls (__init__, __init__, __init__, exists, select, update, workspace_tx, uuid4).


##### `Admission._guard_member_watermark`  (lines 646–681)

```
async def _guard_member_watermark(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, deduped: _ExistingTurn | None, turn_watermark: int | None, arrival_watermark: int | None
```

**Purpose**: Stops an internal invocation from resuming a wait if a member has already replied. This prevents races where both a timer and a human answer try to continue the same waiting work.

**Data flow**: It receives a database connection, conversation identity, any already-deduplicated turn, and two sequence watermarks. If the request is new and both watermarks are present, it checks whether any non-cancelled member turn or member arrival has appeared after them. If so, it raises a private exception that makes the caller return None.

**Call relations**: Admission._admit calls this after deduplication and before creating or folding work. Admission.invoke catches the resulting _SupersededByMember signal and treats it as a clean “the member won the race” outcome.

*Call graph*: called by 1 (_admit); 3 external calls (exists, execute, select).


##### `Admission._validate_member_watermarks`  (lines 684–688)

```
def _validate_member_watermarks(turn_watermark: int | None, arrival_watermark: int | None) -> None
```

**Purpose**: Checks that member-wait watermarks are supplied as a pair. A member message can become either a turn or an arrival, so one number alone would leave half the race unchecked.

**Data flow**: It receives the turn sequence watermark and the inbound-arrival sequence watermark. If exactly one is missing, it raises a ValueError; if both are present or both absent, it returns normally.

**Call relations**: Admission._admit calls this at the start of the shared path. This protects the later Admission._guard_member_watermark query from being asked an incomplete question.

*Call graph*: called by 1 (_admit).


##### `Admission._finish_admission`  (lines 690–722)

```
async def _finish_admission(self, workspace_id: UUID, conversation_id: UUID, surface: str, turn_id: UUID, status: TurnStatus | None, admitted: Admitted, counted_source: TurnAdmissionSource | None, fol
```

**Purpose**: Performs the after-commit side of admission: counting newly admitted turns and offering runnable turns to the background queue. It keeps database decision-making separate from external queue calls.

**Data flow**: It receives the chosen turn, its status, the Admitted result, metric source, folded parked turn information, dispatch flag, and optional workflow id. It emits an admission metric when a new turn was counted, enqueues resumed parked turns or queued turns that are ready to run, and returns the same Admitted result.

**Call relations**: Admission._admit calls this after the transaction closes. It calls Admission._enqueue when work should be offered to DBOS and uses a fresh workflow id when resuming a previously claimed parked turn.

*Call graph*: calls 1 internal fn (_enqueue); called by 1 (_admit); 2 external calls (emit_metric, uuid4).


##### `Admission._deduplicate`  (lines 724–841)

```
async def _deduplicate(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, idempotency_key: str | None, runtime_config: TurnRuntimeConfig | None, inbound: _In
```

**Purpose**: Interprets an idempotency key, which is a caller-provided retry key meaning “this is the same message as before.” It makes repeated deliveries join the work already admitted instead of creating duplicate turns or duplicate arrivals.

**Data flow**: It receives the database connection, target conversation and agent, idempotency key, runtime config, inbound message, and optional comment. If no key exists, it passes the inbound message through. If the key already belongs to a turn, it returns that existing turn. If it belongs to an inbound arrival, it either points the caller at the live or consumed turn, or deletes an orphaned arrival and returns its saved body, context, speaker, and timestamp for re-admission.

**Call relations**: Admission._admit calls this before any new admission decision. When a duplicate already has a settled destination, this method may call Admission._record_comment and return an already-complete Admitted result so _admit can stop early.

*Call graph*: calls 1 internal fn (_record_comment); called by 1 (_admit); 10 external calls (__init__, __init__, __init__, model_validate, model_validate, replace, delete, execute, select, authority_from_member_id).


##### `Admission._fold_live`  (lines 843–1025)

```
async def _fold_live(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, conversation_member_id: UUID | None, surface: str, agent_id: UUID, archived: bool, member_admission:
```

**Purpose**: Tries to attach a new message to the conversation’s currently live turn instead of starting another turn. This enforces the rule that a conversation should have one active turn reading new arrivals at a time.

**Data flow**: It reads the oldest non-terminal turn in the conversation, compares runtime config and authority, checks seats, spend cap, and balance, and decides whether the live turn can absorb the new inbound message. If it can, it inserts an inbound_message row with its own sequence and idempotency key. It may simply return an admitted arrival, record a balance-hold notice, or move a parked turn back to queued so it can run again.

**Call relations**: Admission._admit calls this before creating a new turn when folding is allowed. It calls Admission._record_comment for accepted folded messages and Admission._record_park_notice when a balance problem should be visible to a durable surface.

*Call graph*: calls 2 internal fn (_record_comment, _record_park_notice); called by 1 (_admit); 17 external calls (__init__, __init__, __init__, __init__, __init__, model_validate, execute, insert, or_, select (+7 more)).


##### `Admission._create_turn`  (lines 1027–1174)

```
async def _create_turn(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, conversation_member_id: UUID | None, surface: str, agent_id: UUID, archived: bool, member_admission
```

**Purpose**: Creates a new turn row when the message cannot or should not fold into a live turn. It applies the first hard gates for authority, seats, spending caps, and balance before deciding whether the turn is queued, parked, or cancelled.

**Data flow**: It receives the locked database connection, conversation and agent details, admission kind, authority, idempotency key, runtime settings, and inbound message. It assigns the next conversation sequence, derives a stable turn id, inherits subagent identity when needed, checks authority and billing, inserts the turn row, sets the conversation title if missing, registers durable writeback rows, records a park notice if balance held the member message, and returns the new turn id, sequence, status, and source.

**Call relations**: Admission._admit calls this when there is no deduplicated existing turn and no folded parked turn. It uses Admission._authority_refusal, _refused, _reply_context, and Admission._record_park_notice to prepare the row it writes.

*Call graph*: calls 4 internal fn (_authority_refusal, _record_park_notice, _refused, _reply_context); called by 1 (_admit); 15 external calls (__init__, __init__, __init__, __init__, model_dump, execute, insert, select, update, current_traceparent (+5 more)).


##### `Admission._authority_refusal`  (lines 1176–1195)

```
async def _authority_refusal(self, connection: AsyncConnection, workspace_id: UUID, authority: ExecutionAuthority, archived: bool, member_admission: bool, holds_work_already_done: bool) -> tuple[TurnS
```

**Purpose**: Checks non-billing reasons a turn is not allowed: archived apps, unresolved member speakers, and missing seats. These checks run before spend decisions so strangers or unseated members do not get folded into live work.

**Data flow**: It receives the database connection, workspace, execution authority, archived flag, whether this is member admission, and whether work has already been accepted. It returns None when authority is acceptable, or a status and optional terminal frame explaining cancellation or parking.

**Call relations**: Admission._create_turn calls this before spending checks. It uses _refused for seat refusals that may need to park already-accepted work, and creates direct terminal messages for archived apps or unresolved speakers.

*Call graph*: calls 1 internal fn (_refused); called by 1 (_create_turn); 3 external calls (__init__, __init__, authority_member_id).


##### `Admission._record_park_notice`  (lines 1197–1229)

```
async def _record_park_notice(self, connection: AsyncConnection, workspace_id: UUID, turn_id: UUID, notice: str) -> None
```

**Purpose**: Writes a one-time visible notice explaining that a turn is parked because of balance or credit. This avoids a durable surface going silent when the turn is not terminal and therefore has no final reply yet.

**Data flow**: It receives a connection, workspace id, turn id, and notice text. It inserts a mid-turn reply row with a deterministic id, and does nothing if that same notice already exists.

**Call relations**: Admission._create_turn calls this when a newly created member turn is parked for balance. Admission._fold_live calls it when another member message folds into a turn already held by balance.

*Call graph*: called by 2 (_create_turn, _fold_live); 2 external calls (execute, mid_turn_reply_id_for).


##### `Admission._record_comment`  (lines 1231–1274)

```
async def _record_comment(self, connection: AsyncConnection, workspace_id: UUID, admitted: Admitted, comment: str | None, message_ref: UUID | None=None) -> Admitted
```

**Purpose**: Stores a surface comment as a mid-turn reply tied to the turn or arrival it comments on. It makes comments durable and avoids duplicating the same comment on retries.

**Data flow**: It receives a connection, workspace id, current Admitted result, optional comment text, and optional message reference. If there is no comment, it returns the original result. Otherwise it builds a stable comment id, inserts the comment if absent, and returns an updated Admitted result containing the comment id only when a new row was written.

**Call relations**: Admission._admit uses this near the end of normal admission, while Admission._deduplicate and Admission._fold_live use it when a retry or folded arrival still needs to attach a comment.

*Call graph*: called by 3 (_admit, _deduplicate, _fold_live); 3 external calls (__init__, execute, mid_turn_reply_id_for).


##### `Admission._enqueue`  (lines 1276–1323)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Offers a queued turn to DBOS, the workflow queue that runs turn workers. It also repairs the database marker if the queue offer is cancelled or fails, so another attempt can happen later.

**Data flow**: It reads the turn’s parent and admission source to choose the correct queue name, builds enqueue options with the workflow name, workflow id, and app version, and calls DBOS. If the coroutine is cancelled or another exception occurs, it clears dispatch_enqueued_at on the still-queued turn and logs deferred enqueue errors.

**Call relations**: Admission._finish_admission calls this whenever a turn should start running now or a parked turn has been resumed. This is the boundary between admission’s database state and the external background execution system.

*Call graph*: called by 1 (_finish_admission); 5 external calls (select, update, workspace_tx, log, turn_queue_for).


##### `_reply_context`  (lines 1329–1340)

```
def _reply_context(inbound: _Inbound, surface: str, durable: frozenset[str]) -> dict[str, object]
```

**Purpose**: Builds the context stored on a new turn, including where its reply is expected to go. This gives later background work a simple fact it cannot reliably infer by itself.

**Data flow**: It receives the inbound message, conversation surface name, and set of durable surfaces. It decides that replies reach the durable surface, the member’s conversation, or nobody, then writes that value into a TurnContext and returns it as a JSON-ready dictionary.

**Call relations**: Admission._create_turn calls this while inserting a new turn row. The stored context later tells execution and delivery code whether a reply has an audience.

*Call graph*: called by 1 (_create_turn); 1 external calls (__init__).


##### `AdmissionInvoker.invoke`  (lines 1351–1381)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, authority: ExecutionAuthority, holds_work_already_don
```

**Purpose**: Provides a workspace-bound wrapper for internal invocation. Callers using this object do not need to pass the workspace id and cannot claim a message was spoken by a member.

**Data flow**: It receives a conversation, agent, message, optional idempotency key and context, authority, admission flags, member-wait watermarks, and runtime config. It forwards those values with the stored workspace id to Admission.invoke and returns that result.

**Call relations**: Jobs and extension workflows receive AdmissionInvoker as their limited admission capability. It is a thin handoff to Admission.invoke.


##### `AdmissionInvoker.redispatch`  (lines 1383–1384)

```
async def redispatch(self, conversation_id: UUID, ended_turn_id: UUID) -> UUID | None
```

**Purpose**: Provides a workspace-bound way for internal code to re-admit pending arrivals after a turn ends. It hides the workspace id from the caller.

**Data flow**: It receives a conversation id and the ended turn id. It forwards them, along with the stored workspace id, to Admission.redispatch and returns the new turn id or None.

**Call relations**: Internal workflow code can call this wrapper instead of the full Admission object. The real redispatch behavior lives in Admission.redispatch.


##### `AdmissionInvoker.member_reach`  (lines 1386–1434)

```
async def member_reach(self, member_id: UUID, limit: int) -> tuple[MemberReach, ...]
```

**Purpose**: Finds recent durable conversations through which an internal invoke can reach a particular member. It only returns conversations where that member personally spoke, on that member’s private audience, and whose agent is not archived.

**Data flow**: It receives a member id and a maximum number of results. It queries conversations, turns, and agents in the current workspace, filters to durable surfaces and private member audience, groups by conversation, orders by the member’s latest spoken turn, converts timestamps to timezone-aware values, and returns MemberReach records.

**Call relations**: This belongs to the internal invocation capability because extension or job code may need to choose where to contact a member. It calls _aware to normalize database timestamps before building MemberReach objects.

*Call graph*: calls 1 internal fn (_aware); 4 external calls (__init__, select, workspace_tx, conversation_audience).


##### `_aware`  (lines 1437–1438)

```
def _aware(value: datetime) -> datetime
```

**Purpose**: Ensures a datetime value carries timezone information. It treats timezone-less database timestamps as UTC.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it returns it unchanged; otherwise it returns a copy marked as UTC.

**Call relations**: AdmissionInvoker.member_reach uses this when turning database rows into MemberReach records, so callers receive consistent timestamp values.

*Call graph*: called by 1 (member_reach); 1 external calls (replace).


##### `MemberAdmission.admit`  (lines 1449–1471)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Provides a workspace-bound wrapper for surfaces admitting member messages. It limits surfaces to the member-admission path, where speaker and seat checks are enforced.

**Data flow**: It receives the conversation, message, optional duplicate key and context, required speaker member id, optional intent, comment, and runtime config. It forwards everything with the stored workspace id to Admission.admit_member and returns the Admitted result.

**Call relations**: Member-facing surfaces call this instead of the full Admission object. The real admission work is delegated to Admission.admit_member.


##### `ConnectResume.resume`  (lines 1499–1534)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Writes the result of an external account-connect callback back into the conversation that started it. It returns whether that resume message was actually admitted.

**Data flow**: It receives the target conversation, message, speaker member, and idempotency key. It first checks the conversation’s latest turn lane; if it was a prepared-intent lane, it declines because free text there would create unread work. Otherwise it uses the current workspace and admits the message as the member; failures are logged and return False, success returns True.

**Call relations**: Connect callback code uses this after a grant has already been committed. It calls Admission.admit_member for the actual resume path and logs instead of raising when admission fails, so the callback page can give a cautious answer.

*Call graph*: 4 external calls (select, workspace_tx, log, ws_current).


### `core/src/ufo/runtime/turns/ambient_reply.py`

`domain_logic` · `request handling, just before admitting a new ambient thread reply as an agent turn`

In a busy thread, people may talk to each other after the agent has already joined. Without this file, every later reply could become a full agent turn, even if the message was really one person asking another person a question. That is expensive and can make the agent feel intrusive.

This file adds a small “front door” check before a new turn is created. It sends a bounded snapshot of the recent thread to a cheaper model and asks for only one word: REPLY or NO_REPLY. Think of it like a receptionist deciding whether to put a call through, instead of waking the whole team every time the phone rings.

The main data shape is AmbientMessage, which records who spoke, whether it was the agent itself, and the text. AmbientReplyClassifier builds a compact JSON package containing the recent history and the new message, wraps it between clear fence lines, and sends it to the model with detailed rules. The rules cover cases like “someone told the agent to stop,” “someone is correcting the agent,” or “two humans are just talking to each other.”

A key safety choice is that long new messages are not shortened for the decision. If the new message is too long, classification fails instead. The caller can then choose the safer expensive path: admit the turn rather than accidentally ignore someone.

#### Function details

##### `MeteredModel.model`  (lines 111–111)

```
def model(self) -> str
```

**Purpose**: This property names the model that should be used for the ambient reply decision. It lets the classifier build a model request without knowing the concrete model-access implementation behind it.

**Data flow**: The classifier reads this property from the supplied model object. The value goes into the outgoing ModelRequest so the completion call uses the intended, billed model.

**Call relations**: AmbientReplyClassifier.decide relies on this property when it prepares the one-shot classification request. The actual implementation is supplied elsewhere, because MeteredModel is only a protocol, meaning a small contract that other objects promise to follow.


##### `MeteredModel.complete`  (lines 113–113)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This asynchronous method sends one prepared request to the model and returns the model's text answer. Here, that answer is expected to contain the decision word REPLY or NO_REPLY.

**Data flow**: A ModelRequest goes in, containing the prompt, the thread payload, token limits, and reasoning settings. The model provider processes it and returns plain text, which the classifier then reads as the decision.

**Call relations**: AmbientReplyClassifier.decide calls this after building the request. MeteredModel itself does not implement the call; it describes what a real metered model object must provide.


##### `_entry`  (lines 116–121)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This helper turns one AmbientMessage into the small dictionary form sent to the model. It also trims message text to the per-message history limit so old thread context stays small and predictable.

**Data flow**: An AmbientMessage goes in with speaker, own flag, and text. A dictionary comes out with those same fields, except the text is capped at AMBIENT_MESSAGE_CHARS characters.

**Call relations**: AmbientReplyClassifier._payload calls this for each history message and for the new message. It is the small formatting step that makes every message fit the JSON payload shape the classifier prompt expects.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 134–152)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision function. It asks the model whether a new ambient message should create an agent turn, and returns either REPLY or NO_REPLY.

**Data flow**: It receives the new message and recent thread history. First it rejects a new message that is too long, because deciding from a cut-off version could be misleading. Then it builds a fenced JSON payload, sends it to the configured model inside a ModelRequest, scans the answer for REPLY or NO_REPLY, and returns the last decision word it finds. If the answer cannot be read, it raises an error rather than guessing silently.

**Call relations**: This function is called by the chat surface before admitting an ambient reply as a new turn. It calls AmbientReplyClassifier._payload to package the thread, creates the user Message and ModelRequest objects, then hands the request to MeteredModel.complete. Its errors are intentional: the caller can fall back to admitting the turn, which is costly but safer than ignoring a real request.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 154–170)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This helper packages the thread context into a clear, safe text block for the model to read. It keeps recent history, includes the new message, and wraps the JSON between fence lines so the model can tell prompt instructions apart from user-written chat text.

**Data flow**: It receives the new message and the history tuple. It keeps only the latest AMBIENT_HISTORY_MESSAGES history items, converts each message through _entry, serializes the result as compact JSON, then chooses a fence string that does not already appear inside the payload. The returned string is the final user content sent to the model.

**Call relations**: AmbientReplyClassifier.decide calls this immediately before making the model request. This function calls _entry to format individual messages and json.dumps to produce the JSON block. Its fenced format supports the system prompt's warning that chat messages are untrusted text, not instructions for the classifier to obey.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).


### Live turn streams
These files let clients and server processes publish, replay, and watch live progress frames for a running turn.

### `core/src/ufo/runtime/surfaces/hub_tail.py`

`domain_logic` · `request handling`

A “turn” is a unit of work whose progress is streamed to a caller, such as a web client. The hard part is that a caller may start listening after the turn has already begun, or even after another event loop has finished and saved it. This file solves that by listening in two ways at once, like watching both a live scoreboard and the official match record.

The live source is the Hub, which publishes frames as they happen. The durable source is the database, which records whether the turn has ended or is parked. Parked means paused but not finished, usually because something like billing, spending caps, or seat access blocks it.

The central stream, tail_frames, starts a hub subscription and also checks the stored turn state. If the database already says the turn is terminal or parked, it immediately yields that final frame. Otherwise it keeps yielding hub frames while a background poll checks the database once per second. Whichever source first reports a terminal or parked frame ends the stream.

This design favors correctness over perfect live delivery. A missed live token can be redrawn later, but missing the final state would leave a caller waiting forever. The poll is deliberately persistent: a temporary database read failure is logged and retried instead of closing the stream.

#### Function details

##### `tail_frames`  (lines 35–67)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='', billing_url: str | None=None) -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: This is the main async stream for watching one turn. It yields live frames to the caller until the turn reaches a true stopping point: finished terminal output or a parked pause.

**Data flow**: It receives a hub, a turn id, an optional last-seen cursor, and an optional billing URL. It first decides where the live hub stream can safely resume, starts a background hub reader, and checks the database for an already-saved stopping state. If no stored stop exists, it also starts a background database poll. Frames from both sources flow into one queue, then out to the caller; when a Terminal or Parked frame appears, the stream returns and cancels its background tasks.

**Call relations**: HubTailer.tail exposes this generator to the rest of the surface layer. Inside, tail_frames asks the Hub whether a reconnect cursor is still covered, starts _pump to read live hub frames, calls _read_status_frame for the durable state, and starts _poll_status so the database can still end the stream if the hub misses or cannot see the final update.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, _read_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 70–79)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background worker copies live frames from the Hub into the shared queue used by tail_frames. It filters out internal queue-notification frames that are not meant to be shown to the caller.

**Data flow**: It receives the hub, turn id, starting cursor, and the queue where visible frames should go. It subscribes to the hub from that cursor, skips ArrivalQueued markers, and places all other live frames into the queue with their cursor. If the hub subscription fails, it logs the problem instead of crashing the whole tail.

**Call relations**: tail_frames starts _pump as one of its background tasks. _pump depends on Hub.subscribe for the live feed and hands usable frames back through the queue, where tail_frames later yields them to the caller.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 82–94)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]], billing_url: str | None) -> None
```

**Purpose**: This background worker repeatedly checks the database for the turn’s saved end or parked state. It exists because the live hub may not deliver the final frame to a late subscriber or across event loops.

**Data flow**: It receives the turn id, the shared frame queue, and the optional billing URL. Once per interval, it asks _read_status_frame for the durable status frame. If the read fails, it logs the error and tries again later. When a Terminal or Parked frame is found, it puts that frame into the queue with an empty cursor and stops.

**Call relations**: tail_frames starts _poll_status only after an initial database read shows the turn is not already stopped. _poll_status repeatedly calls _read_status_frame and eventually hands a durable stopping frame back to tail_frames through the queue.

*Call graph*: calls 1 internal fn (_read_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `_read_status_frame`  (lines 97–115)

```
async def _read_status_frame(turn_id: UUID, billing_url: str | None) -> LiveFrame | None
```

**Purpose**: This safely asks for the turn’s durable status while respecting cancellation. Its job is to avoid losing an in-progress database read in a way that could leave the caller without the final frame.

**Data flow**: It starts turn_status_frame as its own async task, waits for it behind a shield, and notes if the outer stream is cancelled while the read is still running. When the read finishes, it returns the frame or None. If cancellation happened, it re-raises cancellation at a safe point; if the read itself failed, it passes that failure upward.

**Call relations**: tail_frames uses _read_status_frame for the first immediate durable check, and _poll_status uses it for repeated checks. _read_status_frame delegates the actual database and policy work to turn_status_frame, while adding careful task and cancellation behavior around it.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 2 (_poll_status, tail_frames); 2 external calls (ensure_future, shield).


##### `turn_status_frame`  (lines 118–175)

```
async def turn_status_frame(turn_id: UUID, billing_url: str | None=None) -> LiveFrame | None
```

**Purpose**: This reads the database and decides whether the turn’s stream should end now. It returns a Terminal frame for completed turns, a Parked frame with a human message for paused turns, or None when the turn is still active.

**Data flow**: It opens a workspace database transaction and looks up the turn’s status, stored terminal frame, workspace, agent, conversation, speaker, and admission information. If a terminal frame is stored, it validates that saved data and wraps it as a Terminal live frame. If the turn is not parked, it returns None. If it is parked, it checks likely reasons in order: seat access, low balance, and spending caps. It returns a Parked frame with the most current message it can determine, falling back to a generic pause message if no specific blocker is found.

**Call relations**: _read_status_frame is the only local function that calls turn_status_frame. turn_status_frame reaches outward to the database layer, seat admission checks, billing balance reading, spending-cap evaluation, and terminal-frame validation so that the stream-ending frame reflects the current durable truth.

*Call graph*: called by 1 (_read_status_frame); 11 external calls (__init__, __init__, __init__, __init__, model_validate, select, workspace_tx, turn_authority, applicable_caps_absent, balance_park_message (+1 more)).


##### `HubTailer.tail`  (lines 188–191)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: This is the object-oriented entry point surfaces use to follow a turn. It wraps tail_frames in an async closing context so background work is cleaned up when the caller stops reading.

**Data flow**: It receives a turn id and an optional reconnect cursor. It passes the stored hub and billing URL from the HubTailer instance into tail_frames, then wraps the resulting async generator with a closing helper. The caller receives an async iterator of cursor-and-frame pairs inside a context manager.

**Call relations**: Surface code calls HubTailer.tail instead of importing the hub-tail functions directly. HubTailer.tail hands the real streaming work to tail_frames and uses aclosing so that leaving the caller’s block closes the generator, which triggers tail_frames to cancel its pump and poll tasks.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


##### `HubTailer.latest_activity`  (lines 193–194)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This asks the hub for the most recent activity known for a turn. A caller can use it to show or inspect the latest live state without starting a full tail stream.

**Data flow**: It receives a turn id and forwards that id to the hub stored in the HubTailer. The hub returns an Activity object if it knows one, or None if it does not.

**Call relations**: This method is a thin seam between surface code and the Hub. Unlike HubTailer.tail, it does not start polling or streaming; it simply delegates to the hub’s latest_activity lookup.


### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `live request handling and cross-process streaming`

When an agent is producing an answer, the user interface needs small live updates: text chunks, tool activity, cost changes, completion notices, and similar events. This file stores those short-lived updates in Redis Streams, which are like append-only message logs. Each turn gets its own stream, so subscribers can replay from a cursor and then keep watching for new frames.

The important idea is that these frames are convenient but not the source of truth. If Redis drops old frames because the stream was trimmed or expired, the system can redraw or recover from the durable turn record elsewhere. This keeps live streaming fast and scalable without making Redis responsible for correctness.

The file also deals with a subtle async issue. An asyncio Redis client is tied to the event loop that created it. Since publishing and subscribing can happen on different loops in the same process, the hub keeps a separate Redis client per running loop, like giving each checkout lane its own card reader instead of sharing one reader across lanes.

Frames are converted to a small JSON form before publishing and rebuilt when read back. Subscribers read in batches, wait briefly when there is nothing new, and continue from the last stream entry they saw. The hub can also check whether a saved cursor is still covered by Redis and peek backward for the latest activity frame.

#### Function details

##### `frame_payload`  (lines 78–84)

```
def frame_payload(frame: HubFrame) -> dict[str, object]
```

**Purpose**: Turns one live frame into a plain dictionary that can be safely written to Redis as JSON. It records both what kind of frame it is and the frame's data, so another process can rebuild the right frame type later.

**Data flow**: A HubFrame goes in. The function checks whether it is an Activity frame, which gets a special wire shape for compatibility, or otherwise looks up the frame's kind and asks the model for JSON-ready fields. A dictionary with a kind label and data comes out.

**Call relations**: RedisStreamHub.publish calls this just before writing a frame to Redis. It is the packing step that makes live frame objects portable across processes.

*Call graph*: called by 1 (publish); 2 external calls (__init__, model_dump).


##### `frame_from_payload`  (lines 87–97)

```
def frame_from_payload(payload: dict[str, object]) -> HubFrame
```

**Purpose**: Rebuilds a live frame object from the dictionary form stored in Redis. This is the unpacking partner to frame_payload.

**Data flow**: A payload dictionary comes in with a kind label and data. The function uses the label to decide which frame class to rebuild, including older or special activity forms for tool calls and skill loading. A HubFrame comes out, or an error is raised if the kind is unknown.

**Call relations**: RedisStreamHub.subscribe uses this when sending stream entries to a caller, and RedisStreamHub.latest_activity uses it after finding an activity entry. It turns raw Redis JSON back into meaningful live update objects.

*Call graph*: called by 2 (latest_activity, subscribe); 2 external calls (__init__, cast).


##### `_stream_id`  (lines 100–102)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis Stream entry id into two numbers so ids can be compared correctly. Redis ids look like a timestamp plus a sequence number, such as '12345-0'.

**Data flow**: A stream entry id string goes in. The function splits it at the dash, turns the timestamp and sequence parts into integers, and returns them as a pair. That pair can be compared using normal numeric ordering.

**Call relations**: RedisStreamHub.covers uses this helper when deciding whether a stored cursor points to an entry that is still within the retained stream.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 105–114)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from Redis's XREAD response and refuses unexpected response shapes. This prevents the code from silently misreading Redis data.

**Data flow**: A Redis XREAD response goes in. If it is empty, the function returns an empty list. If it has the expected list form, it pulls out the entries for the stream. If Redis returns some other shape, it raises an error instead of guessing.

**Call relations**: RedisStreamHub.subscribe calls this after each Redis read. It acts as a small safety gate between Redis's protocol response and the subscriber loop.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 130–136)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client that belongs to the current asyncio event loop, creating it if needed. This avoids sharing one async client across loops, which can break because its internal promises are tied to the loop that made it.

**Data flow**: The hub reads the currently running event loop and checks its internal client map. If a client already exists for that loop, it returns it. If not, it creates a Redis client from the configured URL, stores it under that loop, and returns it.

**Call relations**: Publishing, subscribing, cursor checks, and activity peeking all call this before talking to Redis. It is the common doorway from hub logic to the Redis connection.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 138–139)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name for a specific turn. This keeps every turn's live frames in its own named stream.

**Data flow**: A turn UUID goes in. The function combines it with the shared stream prefix and returns a Redis key string. It does not touch Redis itself.

**Call relations**: RedisStreamHub.publish, RedisStreamHub.subscribe, RedisStreamHub.covers, and RedisStreamHub.latest_activity all call this so they agree on exactly where a turn's frames live.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 141–148)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: Adds one live frame to the Redis stream for a turn and returns the new stream cursor. A caller uses this when it wants surfaces or other processes to see a fresh live update.

**Data flow**: A turn id and HubFrame go in. The function builds the stream name, converts the frame to JSON, appends it to Redis with a maximum retained length, and refreshes the stream's expiration time. The new Redis entry id comes out as a string cursor.

**Call relations**: This is the writing side of the hub. It relies on _stream for the Redis key, _client for the correct loop-local Redis connection, and frame_payload plus JSON encoding to prepare the frame for transport. RedisStreamHub.subscribe later reads what this writes.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 150–174)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: Continuously reads live frames for a turn, starting from a cursor if one is provided. It first catches up on retained entries, then waits for new ones.

**Data flow**: A turn id and optional cursor go in. The function turns the cursor into a Redis read position, repeatedly reads batches from the turn's stream, waits briefly when there is no immediate data, and ignores timeout-as-idle cases. For each valid entry, it updates the cursor, decodes the JSON frame, rebuilds the HubFrame, and yields the pair of new cursor and frame.

**Call relations**: This is the reading side of the hub. It uses _stream and _client to reach Redis, _stream_entries to normalize Redis responses, and frame_from_payload to turn stored JSON back into live frames. It consumes the entries written by RedisStreamHub.publish.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 176–182)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether Redis still has enough retained history for a subscriber to resume from a saved cursor without a gap. If not, the caller knows it should redraw or restart from a safer point.

**Data flow**: A turn id and cursor go in. An empty cursor immediately means false. Otherwise the function reads the oldest retained stream entry from Redis and compares that id with the cursor. It returns true if the cursor is at or after the oldest retained entry, and false if the stream is gone or the cursor is too old.

**Call relations**: This supports reconnect logic around RedisStreamHub.subscribe. It uses _stream to find the turn stream, _client to read Redis, and _stream_id to compare Redis ids in the same way Redis orders them.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


##### `RedisStreamHub.latest_activity`  (lines 184–216)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Looks backward through recent stream entries to find the newest activity frame, such as a tool call or skill load. This gives a quick answer to 'what is the agent currently doing?' without scanning the whole stream.

**Data flow**: A turn id goes in. The function reads the turn stream newest-first in bounded batches, up to a fixed limit. For each entry, it decodes only enough JSON to check the kind. If it finds an activity kind, it rebuilds and returns that Activity frame. If the stream is empty or no recent activity appears within the limit, it returns None.

**Call relations**: This is a helper for status polling or display code that wants the latest meaningful activity. It uses _stream and _client to read Redis, JSON decoding to inspect entries, and frame_from_payload to rebuild only the activity frame it returns.

*Call graph*: calls 3 internal fn (_client, _stream, frame_from_payload); 2 external calls (loads, cast).


### `core/src/ufo/runtime/hub.py`

`io_transport` · `active during live turn execution, reconnect replay, and surface tailing`

A running agent turn produces many small updates: text chunks, tool activity, cost ticks, replies, final frames, and notices that new messages were absorbed. This file defines the shapes of those updates and an in-memory hub that fans them out to any live viewers. Think of it like a small radio tower per turn: publishers broadcast frames, subscribers tune in, and late listeners can hear the recent recording before receiving live audio.

The important promise is that publishing never waits for a slow viewer. Each subscriber has a bounded queue. If that queue fills, the oldest waiting frame is dropped for that subscriber, instead of blocking the running turn. At the same time, the hub stores a bounded ring buffer, which is a fixed-size recent-history list, so a client that reconnects with a cursor can replay frames it missed.

The file also defines when memory is kept or released. A still-running turn keeps its replay buffer even if nobody is watching, because a client may disconnect briefly while handing control to the user’s machine. Once a turn reaches a terminal or parked state, the stream can be dropped when there are no subscribers left. Subagent activity is treated carefully: it only mirrors activity onto an already-live root turn and does not recreate an ended stream.

#### Function details

##### `Hub.publish`  (lines 153–153)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This is the interface promise for adding one live frame to a turn’s stream. A caller uses it when something new has happened during a turn and surfaces should be told about it.

**Data flow**: It receives a turn id and a frame, such as a text update or final result. An implementation stores or broadcasts that frame and returns a cursor, which is a small marker the client can save as its place in the stream.

**Call relations**: This protocol method is the shape that hub implementations must follow. Runtime queue code can call it when committing a failed terminal result, without caring whether the backing hub is in-process or provided by another backend.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 155–155)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This is the interface promise for watching a turn’s live stream. A surface uses it to receive missed frames after a saved cursor and then continue receiving new frames.

**Data flow**: It receives a turn id and optionally a cursor from a previous connection. It produces an asynchronous stream of cursor-and-frame pairs, first replaying newer saved frames and then yielding live updates as they arrive.

**Call relations**: The hub tail surface code calls this while pumping frames to a client. The concrete in-process hub supplies the actual replay and live queue behavior behind this common interface.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 157–157)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for asking whether the hub still has enough history to resume from a cursor without a gap. A reconnecting client uses it to decide whether it can continue smoothly or must redraw from durable state.

**Data flow**: It receives a turn id and a cursor. It checks the retained history for that turn and returns true if the cursor is still within the saved range, otherwise false.

**Call relations**: The tailing logic calls this before deciding how to resume a stream. Implementations answer based on whatever replay store they use.

*Call graph*: called by 1 (tail_frames).


##### `Hub.latest_activity`  (lines 159–159)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This is the interface promise for quickly asking what a running turn is currently doing. It is meant for status views that want one recent activity line without opening a full live subscription.

**Data flow**: It receives a turn id. An implementation looks at recent retained frames and returns the newest activity frame, or nothing if no useful recent activity is available.

**Call relations**: This completes the hub contract alongside publishing, subscribing, and cursor coverage. It lets status-style readers peek at the live stream without joining it.


##### `_offer`  (lines 162–165)

```
def _offer(queue: asyncio.Queue[tuple[str, HubFrame]], item: tuple[str, HubFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber’s queue without ever blocking the publisher. If the subscriber is too far behind, it discards that subscriber’s oldest waiting frame to make room.

**Data flow**: It receives a queue and one cursor-and-frame item. If the queue is full, it removes one old item, then immediately adds the new item; it returns nothing and only changes that queue.

**Call relations**: InProcessHub.publish schedules this helper on each subscriber’s event loop. This keeps delivery safe across different asynchronous loops while preserving the rule that publishing should not wait for slow readers.


##### `InProcessHub._stream`  (lines 212–222)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This internal helper finds or creates the live state for one turn. It is where a turn gets its replay buffer, subscriber list, and cursor counter.

**Data flow**: It receives a turn id and reads the hub’s dictionaries while the hub lock is already held. If the turn already has a stream, it returns it; otherwise it creates a new stream with a fixed-size replay buffer and starts its sequence number from the last remembered mark.

**Call relations**: InProcessHub.publish and InProcessHub.subscribe call this whenever they need the per-turn stream to exist. It is kept private because callers must hold the lock, which is the guard that stops two threads from changing the same stream state at once.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 224–243)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This adds one frame to a turn’s stream, saves it for possible replay, and sends it to current subscribers. It is designed so the running turn never gets stuck behind a slow or disconnected viewer.

**Data flow**: It receives a turn id and a frame. Under a lock, it finds the stream, gives the frame the next cursor, saves it in the replay ring, remembers current subscribers, and updates end-of-stream state for terminal or parked frames. After leaving the lock, it schedules delivery of the frame to each subscriber’s queue and returns the cursor.

**Call relations**: This is the main publishing path for the in-memory hub. It uses InProcessHub._stream to get per-turn state and _offer to safely push frames onto subscriber queues. It also enforces the special rule that SubagentActivity cannot create or revive a stream after the root turn is gone.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 245–272)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This lets a surface follow one turn’s stream. It first replays saved frames after the client’s cursor, then waits for new live frames.

**Data flow**: It receives a turn id and optional cursor. It creates a bounded queue for live frames, registers that queue as a subscriber, snapshots all buffered frames newer than the cursor, yields that replay, and then yields new items from the queue until the subscriber stops. When the subscription ends, it removes the queue and may delete the stream if it is finished and no one is watching.

**Call relations**: Surface tailing code calls this to feed live updates to a client. It uses InProcessHub._stream to attach to the right turn and coordinates with InProcessHub.publish through the same lock, so replayed frames and live frames do not overlap or leave gaps.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 274–282)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether a saved cursor is still covered by the in-memory replay buffer. It helps decide whether reconnecting can be seamless.

**Data flow**: It receives a turn id and cursor. If there is no cursor, no stream, or no buffered history, it returns false; otherwise it compares the cursor with the earliest retained cursor and returns whether the buffer still reaches back far enough.

**Call relations**: The surface tailing flow asks this before relying on cursor replay. It reads only the hub’s retained buffer and does not create a stream or change any state.


##### `InProcessHub.latest_activity`  (lines 284–301)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This returns the newest recent activity message for a turn, if one is still meaningful. It gives status readers a cheap one-line view without subscribing to the whole stream.

**Data flow**: It receives a turn id. Under the lock, it looks backward through only a limited number of recent buffered frames and returns the first Activity frame it finds; if the turn has no stream or no recent activity frame, it returns nothing.

**Call relations**: This is a read-only peek into the same replay ring used by publishing and subscribing. It deliberately scans only a bounded slice of recent frames, so frequent status polling does not become expensive on turns that are mostly streaming text.

*Call graph*: 1 external calls (islice).


### Queued turn dispatch
This file hands the next eligible queued turn to the background workflow once ordering allows it to run.

### `core/src/ufo/runtime/turns/dispatch.py`

`orchestration` · `between turns, when a turn finishes, parks, is cancelled, or a recovery sweep tries to continue a conversation`

A conversation can receive multiple turns, but only one normal running turn should move forward at a time. This file is the gatekeeper for that rule. Think of it like a single-lane bridge: before letting the next car on, it checks whether another car is already crossing.

The main function opens a database transaction, locks the conversation row so two dispatchers cannot make the same decision at once, and checks whether any turn in that conversation is already marked as running. If one is running, it stops. If not, it finds the earliest queued turn, marks it as offered for dispatch, and then asks DBOS, the workflow runner, to enqueue the work.

The file also protects against duplicate or failed enqueue attempts. If a turn was claimed before, it gets a fresh workflow id, because reusing an old completed id could make the workflow system ignore it. If enqueueing fails, the function clears the dispatch marker in the database so another attempt can happen later, then logs the delay. This matters because turn ordering is not trusted to the queue itself; the database stamp is the source of truth.

#### Function details

##### `dispatch_next_turn`  (lines 28–98)

```
async def dispatch_next_turn(client: DBOSClient, conversation_id: UUID) -> None
```

**Purpose**: This function tries to start the next queued turn for one conversation, but only if no turn in that conversation is currently running. It is used when the system needs to continue a conversation after the previous turn has stopped or been set aside.

**Data flow**: It receives a DBOS client, which can place work on the workflow queue, and a conversation id, which identifies the conversation to continue. It locks that conversation in the database, checks for a running turn, then finds the first queued turn if the lane is clear. It marks that turn as enqueued, builds queue options such as the queue name, workflow name, workflow id, and app version, then asks DBOS to enqueue the turn. If enqueueing fails, it reopens the database, removes the enqueue marker from the still-queued turn, updates its timestamp, and writes a log message so the delay is visible.

**Call relations**: This function is the shared handoff used after a turn-ending event wants to offer the next turn. Inside, it relies on workspace_tx to make database reads and writes safely, uses SQLAlchemy select and update statements to inspect and stamp rows, asks turn_queue_for which queue should receive the work, may call uuid4 to create a fresh workflow id for a retried turn, and finally calls DBOSClient.enqueue_async to hand the turn to the workflow runner. If that handoff fails, it calls the logging helper to record that enqueueing was deferred.

*Call graph*: 7 external calls (enqueue_async, select, update, workspace_tx, log, turn_queue_for, uuid4).

## 📊 State Registers Touched

- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-workspace-directory` — The shared record of workspaces, members, seats, admins, invitations, and onboarding status.
- `reg-member-session-auth` — The signed tokens and browser/session identity state that prove who is making a request.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-conversation-transcripts` — The durable conversation history, compacted records, audiences, and readable timeline data.
- `reg-turn-queue-state` — The durable state of conversation turns, including pending, running, paused, cancelled, and finished work.
- `reg-live-update-streams` — The shared live progress channels that stream text, status, costs, and completion events to clients.
- `reg-surface-routing-state` — The saved routing state for web, Slack, iMessage, terminal, and other public conversation surfaces.
- `reg-inbound-delivery-ledger` — The durable deduplication and delivery records for inbound messages, writebacks, and mid-turn replies.
- `reg-subagent-delivery-state` — The parent-child task links and owed-result records used when agents spawn helper agents.
- `reg-scheduled-work-store` — The durable records for recurring tasks, delayed resumes, scheduled fires, and background job claims.
- `reg-runtime-fleet-liveness` — The shared record of running service and worker instances, heartbeats, listener claims, and stuck work.
- `reg-usage-ledger-balance` — The money and usage ledger that tracks costs, prepaid balances, limits, exports, and billing status.
- `reg-telemetry-context` — The shared trace, metric, log, health, and redaction context used to observe work across the system.
- `reg-shared-infra-clients` — Long-lived non-database infrastructure clients and connection pools such as Redis, HTTP, provider, and service clients shared by workers and request handlers.
- `reg-turn-billing-snapshot` — Per-turn frozen billing identity and BYOK attempt state captured before execution and consumed later for stable accounting.
- `reg-rate-limit-buckets` — Shared throttling counters, leases, and cooldown state for ingress, provider/model calls, connector actions, and background workers, separate from spend-cap accounting.
