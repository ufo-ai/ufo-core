# Conversation admission, turn creation, cancellation, and control  `stage-8`

This stage is the gatekeeper for conversation work. It sits just before the main agent work loop: messages, button presses, timer wake-ups, or internal events arrive here first, and only become durable “turns” if the rules allow it. A turn is one unit of conversation work, like one ticket in a support queue.

admission.py is the main front door. It checks that new work is in the right order, not a duplicate, allowed for billing and seat limits, visible to the right people, and safe to place on the queue. ambient_reply.py handles a quieter case: a thread message that does not directly call the agent. It decides whether the agent should answer or stay out of a human side conversation.

stop.py covers the stop button. It confirms the running turn belongs to the conversation, starts cancellation, tells live viewers what happened, and can move the member into a new follow-up turn. cancellation.py is the shared brake pedal: it stops the running workflow first, then records the turn as cancelled so it cannot continue later.

## Files in this stage

### Conversation admission
Rules for deciding when messages or wake-ups may become durable, queued conversation turns.

### `core/src/ufo/runtime/surfaces/admission.py`

`domain_logic` · `request handling and background job admission`

A “turn” is one unit of work for an agent in a conversation, like one incoming message that the agent must answer. This file makes sure every turn enters through the same doorway, whether it came from a user-facing surface, a scheduled job, or an extension. That matters because this is where the system enforces the expensive and safety-critical rules: only the conversation’s bound agent may run, the speaker must be a valid seated member when required, spending limits are checked once, archived apps are refused, and repeated deliveries with the same idempotency key do not create duplicate work.

The core idea is similar to a reception desk with one locked appointment book. Under a database lock on the conversation row, the code decides whether a message should start a new turn or be folded into an already-live turn’s arrival queue. Folding is important: if a member speaks while the agent is still working, the message is saved for that same running turn instead of racing ahead as a separate one.

When a new turn is allowed, the file writes the turn row, possibly writes a durable delivery/writeback row, and enqueues the work in DBOS, the background workflow queue. If the spending or seat gate refuses the turn, the turn is either cancelled with a readable reason or parked for later retry. Small wrapper classes give callers limited powers: surfaces can admit member messages, while internal jobs can invoke only internal turns.

#### Function details

##### `_refused`  (lines 102–113)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: Decides what should happen when a turn is not allowed to run. If the turn already represents paid-for completed work, it parks the turn so it can be retried later; otherwise it cancels the turn with a message the user can see.

**Data flow**: It receives a yes-or-no flag saying whether the turn carries work already done, plus a refusal message. If work is already done, it returns a parked status and no final reply. If not, it builds a terminal cancellation frame containing the message and returns that with a cancelled status.

**Call relations**: Admission._admit calls this whenever a seat, archive, balance, or spending rule refuses a new turn. The result is then stored on the turn so the rest of the system knows whether to wait, retry, or finish the turn as cancelled.

*Call graph*: called by 1 (_admit); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 124–166)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: Admits a message that came from a real conversation member. It validates member-message-only details, sends the request into the shared admission path, and notifies live listeners when the message was folded into an existing turn.

**Data flow**: It takes the workspace, conversation, message body, optional speaker, idempotency key, context, prepared intent, and optional surface comment. It checks that prepared intents are consistent and comments are not empty, then calls the shared _admit method. After admission, it may publish an arrival notice or comment reply to the live hub. It returns an Admitted result that says which turn received the message and whether a new run opened.

**Call relations**: Surfaces use this as their safe doorway for member speech, and Admission.redispatch also uses it when re-admitting a pending member message. It hands the hard decision-making to Admission._admit, then hands live updates to the hub through ArrivalQueued or Reply events when needed.

*Call graph*: calls 1 internal fn (_admit); called by 1 (redispatch); 4 external calls (__init__, __init__, model_dump_json, span).


##### `Admission.redispatch`  (lines 168–217)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Gives an old pending member message another chance to start or join a turn. This is used when a message was left waiting because its earlier target turn could not consume it.

**Data flow**: It opens a workspace database transaction, finds the oldest unconsumed inbound member message for the conversation, and makes sure it has an idempotency key. Then it calls admit_member using the saved message content and speaker. If that re-admission opens a new run, it returns the new turn id together with the arrival id; if nothing was pending or the message merely joined an existing run, it returns None.

**Call relations**: This function is a recovery path built on top of Admission.admit_member rather than a separate admission system. By reusing the same doorway and idempotency key, it avoids saying the same message twice and lets the normal fold-or-start decision happen again.

*Call graph*: calls 1 internal fn (admit_member); 4 external calls (model_validate, select, update, workspace_tx).


##### `Admission.invoke`  (lines 219–294)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=Non
```

**Purpose**: Admits an internal turn, such as work triggered by an extension, a scheduled fire, or a subagent result. It can also refuse to run if a member has spoken since the caller began waiting.

**Data flow**: It receives the workspace, conversation, expected agent, message, optional idempotency key, context, authority member, scheduling flags, and optional member-message watermarks. It passes these into _admit. If _admit reports that a member already superseded the wait, this function turns that private signal into None; otherwise it returns the admitted turn id.

**Call relations**: Internal callers use this instead of admit_member because these turns are not spoken directly by a member. It delegates the real rules to Admission._admit and translates the special “member got there first” race outcome into a simple no-turn result for wait/resume workflows.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 296–800)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, o
```

**Purpose**: This is the central admission decision engine. It decides whether an inbound item is a duplicate, should fold into a live turn, should start a new turn, should be parked, should be cancelled, or should be queued to run now.

**Data flow**: It takes all admission details: workspace, conversation, expected agent, body, speaker, idempotency key, context, admission type, seat/spend flags, wait watermarks, and optional comment. Inside one database transaction, it locks the conversation, verifies the agent binding and speaker, checks for duplicate turn or queued-message keys, checks whether a member message has superseded an internal wait, and looks for a live turn that can accept a folded arrival. If a new turn is needed, it assigns the next sequence number, applies archive, seat, spending, and balance gates, writes the turn row, possibly writes durable delivery state, and records a comment. After the transaction commits, it emits a metric and enqueues runnable work when appropriate. The result is an Admitted object naming the turn and whether a new run opened.

**Call relations**: Admission.admit_member and Admission.invoke both funnel into this method, making it the one shared boundary for all turn creation. It calls _refused to convert refusals into parked or cancelled outcomes, _record_comment to attach surface comments safely, and _enqueue to place runnable turns on the DBOS queue after the database state is committed.

*Call graph*: calls 3 internal fn (_enqueue, _record_comment, _refused); called by 2 (admit_member, invoke); 23 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, model_dump, model_validate, delete (+13 more)).


##### `Admission._record_comment`  (lines 802–845)

```
async def _record_comment(self, connection: AsyncConnection, workspace_id: UUID, admitted: Admitted, comment: str | None, message_ref: UUID | None=None) -> Admitted
```

**Purpose**: Stores an optional surface comment as a mid-turn reply tied to the turn or arrival it comments on. This lets the visible conversation show a comment once without creating duplicates on retries.

**Data flow**: It receives an open database connection, workspace id, the current Admitted result, optional comment text, and optionally the message being referenced. If there is no comment, it returns the Admitted result unchanged. If there is a comment, it computes a stable reply id, inserts the comment only if it is not already present, and returns an updated Admitted result containing the comment id when a new row was written.

**Call relations**: Admission._admit calls this at every point where admission may return, including duplicate and folded-message paths. That keeps comments tied to the same idempotent admission decision instead of being written separately by each caller.

*Call graph*: called by 1 (_admit); 3 external calls (__init__, execute, mid_turn_reply_id_for).


##### `Admission._enqueue`  (lines 847–887)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Places a queued turn onto the DBOS workflow queue so a worker can run it. It also repairs the database marker if enqueueing fails or is cancelled.

**Data flow**: It receives the workspace, conversation, turn id, and optionally a workflow id. It builds queue options that keep work partitioned by conversation, then asks DBOS to enqueue the turn. If the coroutine is cancelled or enqueueing raises an error, it clears the turn’s dispatch_enqueued_at marker in the database so another attempt can try later; cancellation is re-raised, while ordinary enqueue errors are logged and deferred.

**Call relations**: Admission._admit calls this only after the admission transaction has committed and only for turns that should actually run. This separation prevents a worker from seeing work that was never safely written to the database.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 898–923)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=None, holds_work_alr
```

**Purpose**: Provides a workspace-bound helper for internal callers that need to start internal turns. It deliberately does not offer a way to claim that the message was spoken by a member.

**Data flow**: It receives a conversation id, agent id, message, and the same optional internal-invocation controls as Admission.invoke, but not a workspace id because the helper already holds one. It forwards everything to the underlying Admission.invoke and returns the resulting turn id or None.

**Call relations**: Jobs and extension workflows can be handed this limited object instead of the full Admission object. In the bigger flow, it is a capability wrapper: it narrows what the caller is allowed to do, then delegates to Admission.invoke for the real admission decision.


##### `MemberAdmission.admit`  (lines 934–954)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Provides a workspace-bound helper for surfaces that admit member messages. It ensures those callers use the member admission path, where speaker identity and seat rules apply.

**Data flow**: It receives a conversation id, message, optional idempotency key, context, required speaker member id, optional prepared intent, and optional comment. It adds the stored workspace id and forwards the request to Admission.admit_member. The returned Admitted result tells the surface what turn or arrival was created or joined.

**Call relations**: User-facing surfaces can receive this smaller object instead of the full Admission object. In the larger flow, it protects the boundary by making member-surface messages go through Admission.admit_member rather than the internal invocation path.


##### `ConnectResume.resume`  (lines 982–1017)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Writes the result of an account-connection callback back into the conversation that requested it. It returns whether the resume message was successfully admitted.

**Data flow**: It takes the conversation, message, granting member, and idempotency key. First it checks the latest turn in that conversation; if the lane is a prepared-intent lane, it declines because free-text resume messages do not belong there. Otherwise it reads the current workspace, calls Admission.admit_member as the granting member, and returns True if that succeeds. If admission fails, it logs the error and returns False.

**Call relations**: Connect callbacks use this after an external browser consent flow completes. It relies on the same member admission path as normal speech, so the callback result either folds into a waiting live turn or starts a new turn under the usual rules, while logging and swallowing failures so the callback page can report that no work was started.

*Call graph*: 4 external calls (select, workspace_tx, log, ws_current).


### `core/src/ufo/runtime/turns/ambient_reply.py`

`domain_logic` · `request handling, before admitting a new chat turn`

In a group chat thread, once the agent has joined, later messages may not clearly be meant for it. A person might answer the agent, correct it, ask another human for an opinion, or just keep chatting. If every such message became a full agent turn, the system could waste money and add unwanted replies. This file puts a small gate in front of that expensive turn.

The main idea is simple: package the recent thread history and the new message, ask a cheap model for exactly one word, and use that word to decide whether a new turn should be founded. The answer is either REPLY or NO_REPLY. The prompt gives ordered rules: answer when the message is really for the agent, stay quiet when it is clearly for another participant, and prefer replying when the case is genuinely unclear.

The file is careful about safety and cost. It only sends a bounded slice of history, trims long messages, and wraps the JSON input between fence lines so people’s chat text is treated as data, not as instructions to the model. If the model’s answer cannot be read, this code raises an error instead of silently guessing. The caller can then choose the safer fallback: admit the turn rather than accidentally drop a real request.

#### Function details

##### `MeteredModel.model`  (lines 94–94)

```
def model(self) -> str
```

**Purpose**: This is the promised way to ask a model wrapper which model name it will use. The classifier needs that name when it builds the request for the one-word decision.

**Data flow**: The classifier reads this property from whatever object is acting as the model. It gets back a text model identifier, which is placed into the model request.

**Call relations**: AmbientReplyClassifier.decide relies on this property when it prepares the model call. MeteredModel is a protocol, meaning it describes the shape required of the real model object without tying this file to a specific implementation.


##### `MeteredModel.complete`  (lines 96–96)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This is the promised method for making the actual one-shot model call. It sends a prepared request to a language model and returns the model’s text answer.

**Data flow**: A ModelRequest goes in, containing the prompt, payload, token limit, and reasoning settings. The model provider processes it and returns a string, which should contain REPLY or NO_REPLY.

**Call relations**: AmbientReplyClassifier.decide calls this method after building the request. The protocol keeps this file focused on the decision rules while some outside model-access object performs the real billed call.


##### `_entry`  (lines 99–104)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This turns one chat message into the small JSON-friendly shape sent to the model. It includes who spoke, whether it was the agent, and a shortened version of the text.

**Data flow**: An AmbientMessage goes in. The function copies its speaker, own flag, and text, trimming the text to the configured character limit. A plain dictionary comes out, ready to be included in the JSON payload.

**Call relations**: AmbientReplyClassifier._payload calls this for every recent history message and for the new message. It is the small formatting step that keeps the model input consistent and bounded.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 117–133)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision point: it asks whether a new, not-directly-addressed message deserves an agent reply. Callers use it before creating a full agent turn, so unwanted or irrelevant messages do not trigger expensive work.

**Data flow**: The new message and recent history go in. The function builds a fenced JSON payload, sends it to the configured model with the ambient-reply instructions, then searches the returned text for REPLY or NO_REPLY. It returns that decision, or raises an error if no readable decision is found.

**Call relations**: This method drives the whole classifier flow. It calls AmbientReplyClassifier._payload to prepare the thread data, constructs the Message and ModelRequest objects used by the model interface, then hands the request to MeteredModel.complete. The surrounding chat surface is expected to call this before admitting a new ambient turn.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 135–151)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This builds the exact text sent as the user-side input to the model. It presents the thread as JSON, so chat messages are clearly data rather than instructions the model should obey.

**Data flow**: The new message and history go in. The function keeps only the most recent configured number of history messages, converts each message with _entry, serializes everything as compact JSON, then wraps it between matching fence lines. If the fence text appears inside the payload, it lengthens the fence until it is unique. The final fenced string comes out.

**Call relations**: AmbientReplyClassifier.decide calls this immediately before the model request is created. This method calls _entry to format each message and json.dumps to turn the structured data into JSON. Its output is the protected package of context the model reads to make the one-word choice.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).


### Turn stopping
Workflows and shared primitives for safely stopping or cancelling running conversation turns.

### `core/src/ufo/runtime/surfaces/stop.py`

`orchestration` · `request handling`

This file is the “stop button” path for a conversation turn. A turn is one running unit of work in a conversation. When a member asks to stop it, the system must be careful: it should not cancel the wrong turn, it should not undo a turn that already finished by itself, and it should wake up anyone currently watching the stopped turn.

The main piece is the MemberStop class. It receives three collaborators: a DBOS client used by the durable workflow system, a Hub used to publish live updates, and an Admission service that can start or redispatch follow-up work for the conversation.

The flow is deliberately ordered. First it checks the database to confirm that the requested turn really belongs to the requested conversation in the requested workspace. This is like checking the label on a package before returning it. If the label does not match, it refuses the stop request.

Next it calls the shared cancellation primitive, cancel_one_turn. If the turn was already finished, nothing is changed and the caller is told that no ending happened. If cancellation succeeds, it asks Admission whether there is already a pending follow-up message that should become a new turn. If so, it publishes an “absorbed” event for that new turn so the user interface does not wait forever for a message that was consumed outside the normal stream. Finally, it publishes the cancelled terminal event for the stopped turn, waking any live readers immediately.

#### Function details

##### `MemberStop.stop`  (lines 38–61)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: This method performs one complete stop request for a member. It verifies that the turn is part of the named conversation, cancels it if it is still running, publishes the right live updates, and returns a small result telling the surface what happened.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. It first reads the database inside a workspace transaction to find which conversation owns that turn. If the owner does not match the requested conversation, it raises an error instead of touching the turn. If the owner matches, it asks the shared cancellation code to cancel the turn. A missing cancellation result means the turn was already done, so it returns Stopped with ended set to false. A real cancellation result means the turn ended now; it may then ask Admission to redispatch any pending follow-up for the conversation, publishes an absorbed message for that new turn if one was founded, publishes the terminal cancellation event for the stopped turn, and returns Stopped with ended set to true plus the new turn ID when there is one.

**Call relations**: This is the method the member-facing stop surface uses when a user stops a running turn. It relies on the database query to prove the stop request is aimed at the right conversation, then hands the actual durable cancellation to cancel_one_turn. After that, it asks Admission whether the conversation should move on to a waiting follow-up, and uses the Hub to publish Absorbed and Terminal events so live conversation tails and the user interface learn what happened in the right order.

*Call graph*: 6 external calls (__init__, __init__, __init__, select, workspace_tx, cancel_one_turn).


### `core/src/ufo/runtime/turns/cancellation.py`

`domain_logic` · `during cancellation requests and cancel reconciliation`

A “turn” is a unit of work that may be running as a durable DBOS workflow, meaning the system can remember and recover its progress after failures. This file exists because many parts of the system can request cancellation, and they all need to follow the same safety rule: first cancel the workflow, then record the turn as cancelled in the database. If that order were reversed, the database could claim a turn was cancelled even though the worker was still running.

The file cancels exactly one turn. It does not cancel child turns or descendants; another part of the system, the cancel reconciler, is responsible for that wider cleanup. Think of this as the reliable “stop this one machine” button, not the whole factory shutdown procedure.

The main function first looks up the turn row. If the turn is missing or already finished, it leaves it alone. If the turn is still active, it asks DBOS to cancel the workflow identified by the turn id. Only after that request is durable does it reopen the database row, build a final cancelled result, and try to mark the row as cancelled. The update is conditional, so if the turn finished normally in the meantime, this cancel attempt backs off. When cancellation wins, it also emits a metric so observability tools can count the cancelled turn.

#### Function details

##### `cancel_one_turn`  (lines 23–83)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Cancels one turn in the safe shared way used by all cancellation paths. It stops the durable workflow first, then records a cancelled terminal result in the turn table only if the turn has not already finished.

**Data flow**: It receives a DBOS client and a turn id. It reads the turn row from the database to check whether the turn exists and is still non-terminal, meaning not yet finished. If the turn can still be cancelled, it asks DBOS to cancel the workflow named by that turn id. Then it reads the objects the turn already created, builds a cancelled TerminalFrame that preserves those created object references, and conditionally updates the row to cancelled. If the update succeeds, it emits a cancellation metric and returns the TerminalFrame; if the turn was missing, already finished, or finished during the race, it returns None and leaves the row untouched.

**Call relations**: This function is the shared primitive that higher-level cancel initiators rely on, such as evaluation code, a cancel tool, or the reconciler that walks through descendant turns. Inside its flow it opens database transactions with workspace_tx, uses SQLAlchemy queries and updates to read and write the turn row, calls DBOSClient.cancel_workflow_async to stop the durable workflow, validates created object references with ObjectRef.model_validate, packages the final cancelled state with TerminalFrame, and reports the outcome through emit_metric using turn_profile for consistent labeling.

*Call graph*: 8 external calls (__init__, model_validate, cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile).

## 📊 State Registers Touched

- `reg-workspace-records` — The saved workspace records that identify each customer space and hold its limits, setup state, balance settings, and routing boundaries.
- `reg-member-identity` — The shared record of who each user is, how they logged in, what workspace they belong to, and what timezone or invitation state is known.
- `reg-auth-tokens` — The signed login, surface, artifact, and SDK tokens used to prove that a caller or link is allowed to act.
- `reg-agent-registry` — The durable list of agents, including their names, visibility, owners, purposes, model behavior, provisioning source, and tool policy.
- `reg-conversation-turn-state` — The conversation and turn queue state that tracks each unit of agent work from admission through running, completion, cancellation, or recovery.
- `reg-inbound-message-log` — The saved incoming-message log that keeps outside chat, terminal, web, and scheduled events ordered, unique, and ready to become turns.
- `reg-surface-routing` — The shared mapping from external surfaces such as Slack, iMessage, web, terminal, and hosted sites to the right workspace, agent, and conversation.
- `reg-transcript-history` — The saved conversation transcript, including compacted summaries and durable final results that later stages read instead of relying on memory.
- `reg-live-update-hub` — The live activity stream that carries turn progress, tool status, cancellations, mid-turn replies, and final updates to connected viewers.
- `reg-runtime-fleet-liveness` — The heartbeat and listener-claim records that show which long-running service instances are alive and what work they currently own.
- `reg-workflow-claims` — The workflow attempt and run-claim state that prevents two workers from running the same turn, scheduled task, listener, or cleanup job at once.
- `reg-cancellation-state` — The shared brake state that marks work as stopping or cancelled so model calls, tools, workflows, and retries do not continue stale work.
- `reg-billing-ledger` — The usage ledger, spend caps, price versions, exports, and prepaid balance records used to meter and charge workspace activity.
- `reg-feature-flags` — The workspace feature switches that let the system turn capabilities on or off without changing the code.
- `reg-observability-traces` — The shared logs, metrics, traces, traceparent links, and safety-filtered operator views used to understand what the system is doing.
- `reg-audience-visibility` — The saved visibility and audience rules that decide who may see a conversation, transcript, agent, source, artifact, or object.
- `reg-scheduled-jobs` — The durable background-job state for scheduled tasks, pauses, monitor checks, report writing, thumbnail repair, product metrics, and self-improvement runs.
- `reg-subagent-tasks` — The parent-child delegation state that tracks spawned helper agents, their inputs, outputs, names, costs, and undelivered results.
- `reg-turn-resource-budget` — In-flight per-turn resource budget and usage accumulator for spend, model tokens, cache reads, sandbox tokens, egress, retries, and stop conditions before final ledger reconciliation.
- `reg-surface-delivery-outbox` — Durable outgoing surface delivery state for final replies, mid-turn replies, writebacks, claims, retries, and de-duplication across Slack, iMessage, web, terminal, and other surfaces.
- `reg-human-question-state` — Pending human-question and answer state used when tools or workflows ask a member for input and later resume the affected turn.
- `reg-source-trigger-wakeup-queue` — Durable wakeup records created from source/page changes so background runners can later admit agent work without losing or duplicating change-triggered starts.
- `reg-durable-workflow-store` — Serialized durable workflow/checkpoint objects used to reload or resume long-running turns, jobs, and recovery work after crashes or code changes.
