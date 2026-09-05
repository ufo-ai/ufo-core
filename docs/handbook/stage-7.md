# Conversation admission, scheduling, and cancellation  `stage-7`

This stage is the traffic controller for conversation work. It sits behind the scenes during the main work loop, whenever a person sends a message, a timer wakes up, a monitor notices something, a paused task resumes, or a running reply is cancelled. Its job is to decide what becomes queued work and when it may run.

The admission code is the front door. It checks whether a new turn, meaning one unit of conversation work, can start now, must wait, should join an already-running turn, or should be refused for policy or billing reasons. The dispatch code is the handoff point. When a turn finishes, pauses, fails, or is cancelled, it starts the next waiting turn only if the conversation is free.

The audience and visibility pieces act like privacy guards. They decide who may see or join each turn, and when the agent should stay quiet in a busy shared room. The durable queue and stop pieces save waits and cancellations safely, notify listeners, and can start a follow-up. The site report file turns a broken hosted page into a controlled message so the agent can repair it.

## Sub-stages

- [Audience, visibility, and participation decisions](stage-7.1.md) `stage-7.1` — 4 files
- [Durable queues, waits, and stop requests](stage-7.2.md) `stage-7.2` — 1 files

## Files in this stage

### Admission and Dispatch
Converts external failures and incoming wake-ups into admitted, deferred, merged, refused, or dispatched conversation turns.

### `core/src/ufo/harness/sandbox/site_report.py`

`orchestration` · `request handling`

A hosted site can fail in a place that cannot fix it directly. The ingress process is like a front desk: it notices that nobody is answering on a site port, but it does not run the conversation engine. This file builds the small, secure handoff from that front desk to the service that can talk to the agent.

The ingress side, `SiteReporter`, creates a short-lived signed token. The token is both proof and message: it says which workspace, conversation, and port were involved, and it is signed with the deploy secret so the receiver can trust it. It then posts that token to an internal endpoint. If the post fails, it logs a warning and stops, because the user’s waiting page will reload and try again soon.

The service side, `SiteReports`, exposes that internal endpoint. It checks the token, finds the agent for the conversation, and invokes the conversation with a clear instruction: the hosted site on this port did not answer, find out why, restart it, and verify it works.

One important detail is the time bucket used in the idempotency key. The waiting page may reload many times, but reports in the same bucket count as the same turn. That prevents a broken site from flooding the agent with duplicate repair requests.

#### Function details

##### `SiteReporter.report`  (lines 74–105)

```
async def report(self, claims: IngressClaims) -> None
```

**Purpose**: This is the ingress side of the site-down report. When the ingress sees that a hosted site is not answering, this function sends a short, signed report to the main service so the conversation’s agent can be told.

**Data flow**: It receives ingress claims, which include facts such as the workspace, conversation, and port. If there is no service address configured, or the request is for a shipped site rather than a live conversation sandbox, it does nothing. Otherwise it stamps the claims with a short expiry time, removes fields that should not travel in this report, signs them into a token, and posts that token to the internal report endpoint. Nothing is returned. If the network call fails, or the service refuses the report, it writes a warning for operators.

**Call relations**: This function is called from the ingress flow after a site fails to answer. It uses the current time to make the report expire soon, uses `replace` to make a safe copy of the claims, and hands the claims to `mint_ingress_token` so the receiver can trust them. If posting the report raises an HTTP error or gets an unexpected response, it calls `warn` instead of retrying, because the browser’s waiting page will reload and create another chance to report.

*Call graph*: 4 external calls (replace, now, warn, mint_ingress_token).


##### `SiteReports.router`  (lines 125–128)

```
def router(self) -> APIRouter
```

**Purpose**: This builds the small FastAPI router, meaning the web-route object, that receives site-down reports. The main service mounts this router so ingress has a fixed internal URL to post to.

**Data flow**: It takes no request data. It creates a new router, attaches the internal site-report path to the `_report` function for POST requests, and returns the router so the application can include it.

**Call relations**: This is used during service setup, when the web application is being assembled. It creates a FastAPI `APIRouter` and connects the public-facing route machinery to this file’s private `_report` method, so later HTTP POSTs are delivered to the report verifier and turn invoker.

*Call graph*: 1 external calls (APIRouter).


##### `SiteReports._report`  (lines 130–154)

```
async def _report(self, authorization: Annotated[str, Header()]='') -> Response
```

**Purpose**: This is the service-side endpoint that receives a site-down report. It verifies the signed token, finds the right conversation agent, and asks that agent to repair the stopped site.

**Data flow**: It starts with the HTTP `Authorization` header. It strips the `Bearer` prefix, verifies the token against the expected report kind and current time, and rejects bad tokens with a 401 unauthorized error. For a valid token, it enters the matching workspace, looks up the agent for the conversation, and returns 404 if there is no such conversation. If an agent exists, it builds a time-bucketed idempotency key, sends the repair instruction to the workspace’s turn invoker, and returns an empty 204 response. If the agent has been archived, it still returns 204 because there is nothing useful to repair.

**Call relations**: FastAPI calls this function when ingress posts to the internal report route made by `SiteReports.router`. It uses `verify_ingress_token` as the gatekeeper, `ws` to run inside the right workspace, and `conversation_agent_id` to find who should receive the message. It then uses `authority_from_member_id(None)` to mark the report as a system/workspace action rather than a member’s personal action, and hands the final repair request to the `TurnInvoker` returned by `invoker_for`.

*Call graph*: 7 external calls (now, HTTPException, Response, verify_ingress_token, authority_from_member_id, conversation_agent_id, ws).


### `core/src/ufo/runtime/surfaces/admission.py`

`domain_logic` · `request handling and background admission`

A "turn" is one unit of agent work in a conversation, like one ticket in a help-desk queue. This file makes sure every ticket is created the same safe way. Without it, different entry points could accidentally skip spending limits, seat checks, duplicate-message protection, or the rule that a conversation stays bound to its original agent.

The main class, Admission, works under a database lock on the conversation. That lock is important because it lets the code assign the next turn number safely, one at a time. It first checks whether the message is a repeat using an idempotency key, which is a caller-provided label meaning "this is the same delivery as before." If it is a repeat, the caller is pointed back to the already-created turn or queued arrival instead of creating duplicate work.

If another turn is already live in the conversation, many incoming messages are not given their own turn. They are written to an inbound-message queue for the live turn to absorb at its next boundary. If folding is not safe, for example because the authority differs, the new turn waits until the live one ends.

The file also enforces business gates: archived agents, missing member seats, unresolved speakers, and spending or balance limits. Durable surfaces get writeback rows so replies can be delivered later. Finally, queued turns are handed to DBOS, the background workflow queue, for execution.

#### Function details

##### `_refused`  (lines 154–165)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: Decides what a refusal means for a turn. If the turn already represents paid or completed work, it parks the turn instead of cancelling it; otherwise it creates a cancelled final answer explaining why the work was refused.

**Data flow**: It receives a yes-or-no flag saying whether the turn contains work already done, plus a human-readable refusal message. It turns that into either a parked status with no final message, or a cancelled status with a terminal frame that carries the explanation.

**Call relations**: Admission._create_turn uses this when billing or spending says a new turn cannot run. Admission._authority_refusal also uses it when an archived app or missing seat should stop the turn without losing already-paid work.

*Call graph*: called by 2 (_authority_refusal, _create_turn); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 176–221)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: Admits a message spoken by a real member through a surface, such as a chat or app UI. It validates the member-facing envelope, sends the message through the shared admission path, and notifies the live hub when the message folded into an existing turn or created a visible comment.

**Data flow**: It receives workspace and conversation IDs, the message body, the speaker member ID, optional duplicate-protection key, context, intent, comment, and runtime settings. It checks that prepared intents match their body and that comments are valid, builds member authority from the speaker, calls Admission._admit, then publishes arrival or comment events to the hub when needed. It returns an Admitted object describing the turn and any arrival or comment IDs.

**Call relations**: Surfaces call this as the safe way to admit member messages. Admission.redispatch also calls it when an old pending member message needs another chance. Internally it relies on Admission._admit for the real admission decision, then sends side-channel updates through the hub for clients already watching the turn.

*Call graph*: calls 1 internal fn (_admit); called by 1 (redispatch); 5 external calls (__init__, __init__, model_dump_json, span, authority_from_member_id).


##### `Admission.redispatch`  (lines 223–272)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Gives the oldest unconsumed member message in a conversation another chance to start or join work. This is used when a message was left waiting because its target turn was cancelled or did not consume it.

**Data flow**: It reads the database for the oldest pending inbound message from a member. If the row has no idempotency key, it stamps one so the retry cannot create duplicate work. It then calls admit_member with the stored body, speaker, key, and context. It returns the new turn ID and arrival ID only when this retry opened a run; otherwise it returns None.

**Call relations**: This function is a recovery path around Admission.admit_member. It first selects and prepares a pending inbound_message row itself, then hands the actual admission back to the normal member-admission flow so all the same checks still apply.

*Call graph*: calls 1 internal fn (admit_member); 4 external calls (model_validate, select, update, workspace_tx).


##### `Admission.invoke`  (lines 274–358)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, authority: ExecutionAuthority, holds
```

**Purpose**: Admits an internal turn, such as work triggered by an extension, scheduled job, or subagent result. It carries explicit execution authority and can optionally refuse to run if a member has spoken since the caller began waiting.

**Data flow**: It receives the workspace, conversation, asserted agent, message, optional idempotency key and context, authority, scheduling and standalone flags, member-watermark values, and runtime settings. It passes these to Admission._admit. If the member-watermark check says the member already replied first, it returns None; otherwise it returns the admitted turn ID.

**Call relations**: Internal jobs and extension workflows use this instead of admit_member so they cannot pretend to be member speech. It delegates all important decisions to Admission._admit and translates the private _SupersededByMember signal into the public result None.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 360–565)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, a
```

**Purpose**: Runs the full admission decision from start to finish. It is the central funnel that locks the conversation, checks duplicates, folds messages into live turns when safe, creates new turns when needed, records comments, and decides whether to enqueue work now.

**Data flow**: It receives all details about the proposed inbound message or internal invocation. Inside one database transaction, it locks the conversation, confirms the asserted agent still matches, checks archived-agent rules, validates the speaker, deduplicates idempotency keys, checks member wait watermarks, tries to fold into a live turn, or creates a new turn. It records optional comments and then calls Admission._finish_admission after the transaction to emit metrics and enqueue any runnable turn. It returns an Admitted result.

**Call relations**: Admission.admit_member and Admission.invoke both enter here. This function is the traffic controller: it calls _validate_member_watermarks, _deduplicate, _guard_member_watermark, _fold_live, _create_turn, _record_comment, and finally _finish_admission, depending on what it finds.

*Call graph*: calls 7 internal fn (_create_turn, _deduplicate, _finish_admission, _fold_live, _guard_member_watermark, _record_comment, _validate_member_watermarks); called by 2 (admit_member, invoke); 8 external calls (__init__, __init__, __init__, exists, select, update, workspace_tx, uuid4).


##### `Admission._guard_member_watermark`  (lines 567–602)

```
async def _guard_member_watermark(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, deduped: _ExistingTurn | None, turn_watermark: int | None, arrival_watermark: int | None
```

**Purpose**: Stops an internal wake-up from running if a member has already spoken after the caller started waiting. This prevents two competing resumes, such as a timer and a member reply, from both winning.

**Data flow**: It receives the database connection, workspace and conversation IDs, any already-deduplicated turn, and two sequence watermarks. If there is already a deduped turn or no watermarks, it does nothing. Otherwise it checks for newer member-spoken turns or newer member arrivals. If it finds one, it raises _SupersededByMember.

**Call relations**: Admission._admit calls this after deduplication and before folding or creating new work. Admission.invoke catches the resulting _SupersededByMember exception and returns None, telling the caller that the member response already ended the wait.

*Call graph*: called by 1 (_admit); 3 external calls (exists, execute, select).


##### `Admission._validate_member_watermarks`  (lines 605–609)

```
def _validate_member_watermarks(turn_watermark: int | None, arrival_watermark: int | None) -> None
```

**Purpose**: Checks that member-wait protection is specified completely. A caller must provide both the turn sequence watermark and the inbound-message sequence watermark, because member speech can appear in either place.

**Data flow**: It receives two optional numbers. If exactly one is present, it raises a ValueError; if both are present or both are absent, it returns without changing anything.

**Call relations**: Admission._admit calls this at the start so later logic never has to guess whether only half of the member-wait question was asked.

*Call graph*: called by 1 (_admit).


##### `Admission._finish_admission`  (lines 611–643)

```
async def _finish_admission(self, workspace_id: UUID, conversation_id: UUID, surface: str, turn_id: UUID, status: TurnStatus | None, admitted: Admitted, counted_source: TurnAdmissionSource | None, fol
```

**Purpose**: Performs the after-transaction side effects of admission. It emits the admission metric and places the turn on the DBOS workflow queue when the turn is ready to run.

**Data flow**: It receives the workspace, conversation, surface, turn ID, turn status, Admitted result, source to count, folded parked turn ID, dispatch flag, and optional workflow ID. It emits a metric if a new turn should be counted. If a parked turn was reawakened, it enqueues that. Otherwise it enqueues the admitted queued turn only when this turn is next in line. It returns the same Admitted result.

**Call relations**: Admission._admit calls this after committing the database changes. This separation matters because the database record is made durable first, and then _finish_admission asks _enqueue to start background execution.

*Call graph*: calls 1 internal fn (_enqueue); called by 1 (_admit); 2 external calls (emit_metric, uuid4).


##### `Admission._deduplicate`  (lines 645–762)

```
async def _deduplicate(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, idempotency_key: str | None, runtime_config: TurnRuntimeConfig | None, inbound: _In
```

**Purpose**: Resolves a repeated idempotency key so the same delivered message does not create duplicate work. It can reconnect the caller to an existing turn, an existing folded arrival, or an orphaned arrival that should be retried.

**Data flow**: It receives the database connection, workspace, conversation, agent, idempotency key, runtime settings, inbound message, and optional comment. With no key, it simply says there is no existing work. With a key, it first looks for a turn that already used it, then for an inbound_message row that used it. Depending on what it finds, it returns an existing turn, an already-admitted result, or a modified inbound message copied from the queued row after deleting that row for re-admission.

**Call relations**: Admission._admit calls this before making any new turn or arrival. It may call _record_comment when a repeated delivery includes a comment that should be attached to the already-known target.

*Call graph*: calls 1 internal fn (_record_comment); called by 1 (_admit); 10 external calls (__init__, __init__, __init__, model_validate, model_validate, replace, delete, execute, select, authority_from_member_id).


##### `Admission._fold_live`  (lines 764–941)

```
async def _fold_live(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, conversation_member_id: UUID | None, agent_id: UUID, archived: bool, member_admission: bool, authorit
```

**Purpose**: Tries to place a new inbound message onto the queue of an already-live turn instead of creating a separate turn. This keeps a conversation from running multiple turns at once while still preserving each incoming message as its own arrival.

**Data flow**: It receives the locked database connection, conversation and agent details, archived state, admission kind, authority, runtime settings, idempotency key, inbound message, and optional comment. It looks for the earliest non-terminal turn in the conversation, checks runtime compatibility, authority compatibility, seat access, and spending or balance permission. If folding is allowed, it inserts an inbound_message row pointing at the live turn. If the live turn was parked and is due to retry, it changes it back to queued. It returns a FoldResult saying whether the message folded, woke a parked turn, must wait for the live turn, or did not fold.

**Call relations**: Admission._admit calls this only when there is no deduped turn and the admission is allowed to fold. It may call _record_comment to attach a surface comment to the folded arrival, or hand back a parked turn ID so _finish_admission can enqueue that turn again.

*Call graph*: calls 1 internal fn (_record_comment); called by 1 (_admit); 16 external calls (__init__, __init__, __init__, __init__, __init__, model_validate, execute, insert, or_, select (+6 more)).


##### `Admission._create_turn`  (lines 943–1091)

```
async def _create_turn(self, connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, conversation_member_id: UUID | None, surface: str, agent_id: UUID, archived: bool, member_admission
```

**Purpose**: Creates a new turn row in the database when the message cannot or should not fold into a live turn. It also decides the new turn's initial status: queued, parked, or cancelled.

**Data flow**: It receives the database connection, conversation metadata, surface, agent, archived state, admission kind, intent and scheduling flags, authority, duplicate key, runtime settings, and inbound message. It assigns the next sequence number, derives the stable turn ID, preserves subagent identity when needed, checks authority and seat refusal, evaluates spending and balance limits, inserts the turn row, fills a blank conversation title, and creates a writeback row for durable surfaces. It returns the created turn ID, sequence, status, and admission source.

**Call relations**: Admission._admit calls this when no existing turn or fold handles the message. It calls _authority_refusal first, may use _refused for billing or cap refusals, and supplies the status later used by _finish_admission to decide whether to enqueue.

*Call graph*: calls 2 internal fn (_authority_refusal, _refused); called by 1 (_admit); 14 external calls (__init__, __init__, __init__, __init__, model_dump, execute, insert, select, update, current_traceparent (+4 more)).


##### `Admission._authority_refusal`  (lines 1093–1108)

```
async def _authority_refusal(self, connection: AsyncConnection, workspace_id: UUID, authority: ExecutionAuthority, archived: bool, member_admission: bool, holds_work_already_done: bool) -> tuple[TurnS
```

**Purpose**: Checks whether the caller has the basic right to admit this turn. It refuses archived apps, unresolved member-surface speakers, and members or scheduled work that lack a seat.

**Data flow**: It receives a database connection, workspace ID, execution authority, archived flag, whether this is member admission, and whether the turn already contains completed work. It returns None if admission is allowed. Otherwise it returns a status and optional terminal message explaining the refusal, parking instead of cancelling when completed work must not be discarded.

**Call relations**: Admission._create_turn calls this before spending checks. It uses _refused for cases where existing work may need protection, and direct cancellation for the special unresolved-speaker case.

*Call graph*: calls 1 internal fn (_refused); called by 1 (_create_turn); 3 external calls (__init__, __init__, authority_member_id).


##### `Admission._record_comment`  (lines 1110–1153)

```
async def _record_comment(self, connection: AsyncConnection, workspace_id: UUID, admitted: Admitted, comment: str | None, message_ref: UUID | None=None) -> Admitted
```

**Purpose**: Stores an optional surface comment as a mid-turn reply so clients can see a comment attached to the turn or folded arrival. It writes the comment only once even if the same admission is retried.

**Data flow**: It receives the database connection, workspace ID, current Admitted result, optional comment text, and optional message reference. If there is no comment, it returns the original Admitted result. Otherwise it creates a deterministic comment ID, inserts a mid_turn_reply row if it does not already exist, and returns an Admitted result that includes the comment ID when a new row was written.

**Call relations**: Admission._admit calls this for newly admitted or existing turns. _deduplicate and _fold_live also call it when repeated or folded deliveries carry a comment that should be visible to surfaces.

*Call graph*: called by 3 (_admit, _deduplicate, _fold_live); 3 external calls (__init__, execute, mid_turn_reply_id_for).


##### `Admission._enqueue`  (lines 1155–1202)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Offers a queued turn to DBOS, the background workflow system that will actually run the agent work. It also undoes the dispatch marker if the enqueue attempt is cancelled or fails, so the turn can be offered again later.

**Data flow**: It receives workspace, conversation, turn ID, and an optional workflow ID. It reads the turn kind to choose the correct queue, builds DBOS enqueue options, and calls the DBOS client. If the task is cancelled or an error occurs, it clears dispatch_enqueued_at on the queued turn and logs failures that should be retried later.

**Call relations**: Admission._finish_admission calls this after the database commit says a turn is ready to run. It is the bridge from durable admission records to asynchronous worker execution.

*Call graph*: called by 1 (_finish_admission); 5 external calls (select, update, workspace_tx, log, turn_queue_for).


##### `AdmissionInvoker.invoke`  (lines 1213–1243)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, authority: ExecutionAuthority, holds_work_already_don
```

**Purpose**: Provides a workspace-bound shortcut for internal invocation. Jobs and extensions can use it without being given the power to choose an arbitrary workspace or claim a member spoke.

**Data flow**: It receives a conversation, agent, message, optional duplicate key, context, authority, scheduling flags, wait watermarks, and runtime settings. It adds the stored workspace ID and forwards everything to Admission.invoke. It returns the turn ID or None using the same meaning as Admission.invoke.

**Call relations**: This is a narrow wrapper around Admission.invoke. The larger system can pass AdmissionInvoker to internal code as a limited capability rather than exposing the full Admission object.


##### `AdmissionInvoker.member_reach`  (lines 1245–1293)

```
async def member_reach(self, member_id: UUID, limit: int) -> tuple[MemberReach, ...]
```

**Purpose**: Finds recent durable conversations where a specific member personally spoke and can be reached. This lets internal work discover which member-owned conversations are valid targets for follow-up.

**Data flow**: It receives a member ID and a maximum number of results. It queries conversations on durable surfaces, joined to live agents and turns spoken by that member, restricted to that member's private audience. It orders by the most recent time the member spoke, converts timestamps to timezone-aware values, and returns MemberReach records.

**Call relations**: This method belongs to the internal AdmissionInvoker capability. It uses _aware for timestamp cleanup and conversation_audience to enforce that only the member's own private conversation audience is returned.

*Call graph*: calls 1 internal fn (_aware); 4 external calls (__init__, select, workspace_tx, conversation_audience).


##### `_aware`  (lines 1296–1297)

```
def _aware(value: datetime) -> datetime
```

**Purpose**: Ensures a datetime value has timezone information. If the database returned a timestamp without a timezone, it treats it as UTC.

**Data flow**: It receives a datetime. If the datetime already has timezone information, it returns it unchanged. Otherwise it returns a copy marked with the UTC timezone.

**Call relations**: AdmissionInvoker.member_reach calls this before building MemberReach objects, so callers receive consistent time values.

*Call graph*: called by 1 (member_reach); 1 external calls (replace).


##### `MemberAdmission.admit`  (lines 1308–1330)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Provides a workspace-bound way for surfaces to admit member messages. It keeps surfaces on the member-admission path, where speaker and seat rules are enforced.

**Data flow**: It receives a conversation, message, optional duplicate key, context, required speaker member ID, optional intent, comment, and runtime settings. It adds the stored workspace ID and forwards the request to Admission.admit_member. It returns the resulting Admitted record.

**Call relations**: Surfaces are intended to receive this wrapper instead of the full Admission object. It delegates to Admission.admit_member, which then enters the shared Admission._admit flow.


##### `ConnectResume.resume`  (lines 1358–1393)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Writes the result of an external account-connection callback back into the conversation that requested it. It admits the result as the granting member's own message, unless the target lane is a prepared-intent lane where free-text resume messages are intentionally declined.

**Data flow**: It receives a conversation ID, message, speaker member ID, and idempotency key. It first checks the latest turn's admission source. If the lane is intent-only, it logs a decline and returns False. Otherwise it reads the current workspace, tries to admit the message through Admission.admit_member, logs and returns False on failure, and returns True on success.

**Call relations**: This is used after a browser-based connect flow finishes. It calls back into Admission.admit_member so the resume message follows the same folding, deduplication, seat, and spending rules as any other member message.

*Call graph*: 4 external calls (select, workspace_tx, log, ws_current).


### `core/src/ufo/runtime/turns/dispatch.py`

`orchestration` · `after a turn ends or during recovery dispatch`

A conversation can have several user or system turns waiting to run, but they must not all run at once. This file is the careful gatekeeper for that rule. Think of it like a receptionist calling the next person from a waiting room only after checking that the consultation room is empty.

The queue itself is deliberately simple and does not enforce conversation order. Instead, this code uses the database as the source of truth. It locks the conversation row, checks whether any turn is already marked as running, then looks for the earliest queued turn. If there is no eligible turn, or if that turn has already been marked as offered for dispatch, it stops.

When it does find a turn to run, it first stamps the turn in the database with a dispatch time. That stamp matters because more than one recovery path may try to dispatch the same turn after crashes or races. The stamp lets only one attempt win. After the database transaction is complete, it asks DBOS, the workflow runner, to enqueue the turn’s workflow. If enqueueing fails, it clears the stamp so another attempt can try later, and logs that the enqueue was deferred.

One important detail is that a turn that was previously claimed gets a fresh workflow id. This avoids DBOS treating the new attempt as a duplicate of an already-consumed workflow.

#### Function details

##### `dispatch_next_turn`  (lines 28–98)

```
async def dispatch_next_turn(client: DBOSClient, conversation_id: UUID) -> None
```

**Purpose**: This function offers the next queued turn in a conversation to the workflow runner, but only if no turn from that conversation is currently running. It protects turn order and prevents two turns in the same conversation from being started at the same time.

**Data flow**: It receives a DBOS client, used to enqueue work, and a conversation id, used to find the relevant turns. It opens a database transaction, locks the conversation, checks for a running turn, and then finds the earliest queued turn. If that turn is eligible, it marks it as dispatch-enqueued in the database. After leaving the transaction, it builds enqueue options such as the queue name, workflow name, workflow id, and app version, then sends the turn id and workspace id to DBOS. If that send fails, it reopens the database, removes the dispatch stamp from the still-queued turn, and writes a log entry so the system can try again later.

**Call relations**: This function is called by the parts of the runtime that finish or interrupt a turn, and also by recovery paths that sweep for work after failures. Inside its flow, it relies on `workspace_tx` for safe database changes, SQLAlchemy queries to read and update turn records, `turn_queue_for` to choose the right DBOS queue, `uuid4` when a retried turn needs a fresh workflow id, `DBOSClient.enqueue_async` to actually schedule the workflow, and `log` to record enqueue failures.

*Call graph*: 7 external calls (enqueue_async, select, update, workspace_tx, log, turn_queue_for, uuid4).

## 📊 State Registers Touched

- `reg-agent-records` — The saved assistant profiles, including their model choice, tools policy, setup needs, visibility, reasoning level, and spawn contracts.
- `reg-workspace-member-seat-state` — The shared record of workspaces, members, admins, invitations, seats, and workspace-level limits.
- `reg-surface-routing-state` — The saved routing information that maps web, Slack, iMessage, terminal, hosted app, and public-link traffic to the right workspace and conversation.
- `reg-conversation-records` — The durable conversation state, including conversation identity, title, surface label, sandbox handle, audience, and related metadata.
- `reg-turn-queue-state` — The durable queue of conversation turns, including admission source, run claim, parked state, resume state, and final status.
- `reg-transcript-history` — The saved conversation timeline, including messages, compacted summaries, final answers, costs, and readable history.
- `reg-audience-visibility-state` — The shared privacy labels that decide who may read or join conversation content and workspace objects.
- `reg-live-updates-delivery` — The live reply and notification delivery state used to stream running turns and safely deliver mid-turn or delayed messages once.
- `reg-runtime-fleet-claims` — The attendance and claim sheet for running service processes, including heartbeats, work ownership, and surface listener claims.
- `reg-cancellation-cleanup-state` — The shared stop-and-cleanup state that records when active turns, workflows, child work, sandboxes, and streams are being wound down.
- `reg-schedules-automations` — The durable alarm clock for future work, pauses, monitors, source-change triggers, notification inbox items, and extension jobs.
- `reg-delegation-state` — The parent-child work state that tracks subagent turns, their contracts, trace links, pending results, and delivery back to the parent.
- `reg-billing-ledger-balance` — The shared money and usage record, including spend caps, model costs, sandbox and egress usage, prepaid balances, and export progress.
- `reg-inbound-message-buffer` — Durable inbound messages from external surfaces waiting to be rendered, admitted, deduplicated, or converted into conversation work.
- `reg-human-request-state` — Pending and resolved human-interaction requests, including agent questions, secret requests, credential requests, and connection-authorization handoffs.
