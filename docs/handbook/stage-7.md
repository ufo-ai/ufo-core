# Conversation admission and durable turn queueing  `stage-7`

This stage is the front door to the system’s work loop. Whenever a person sends a message, a scheduled task fires, or another part of the system asks the agent to do something, it is first turned into a “turn”: one saved step in a conversation. The word “durable” means the turn is written to storage, so it is not lost if the process restarts.

The file core/src/ufo/surfaces/admission.py makes all entry paths use the same gate. It checks whether the conversation is still allowed to continue, including whether spending limits have been reached and whether the conversation has been cancelled. It also records any context supplied by the “surface,” meaning the outside place the request came from, such as a chat interface or scheduler.

If the request is accepted, this stage creates the turn record and queues exactly one piece of agent work. Later, a worker can claim that queued item and run the agent safely.

## Files in this stage

### Conversation admission and durable turn queueing
### `core/src/ufo/surfaces/admission.py`

`domain_logic` · `request handling and scheduled task firing`

A “turn” is one unit of conversation work: a message comes in, the agent thinks, and a reply is eventually produced. This file decides whether a new turn should be created, whether a message should be folded into a turn that is already alive, or whether the request must be refused or parked for later. It is like the receptionist for a busy clinic: it gives each visitor a place in line, checks whether they are allowed in, and avoids creating duplicate appointments for the same request.

The central rule is that admission happens once, at the boundary. The code locks the conversation row in the database while it assigns the next sequence number, so two arrivals cannot accidentally get the same place. It also checks idempotency keys, which are repeat-safe labels used when the same delivery may arrive twice; a retry joins the already admitted turn instead of creating a second one.

The file also protects important promises. A conversation stays bound to its original agent. Seat limits are enforced before a message can enter. Spending limits can allow the turn, park it until the cap changes, or cancel it with a clear reason. If a conversation already has a live turn, new messages go into an inbound-message queue so one final reply can answer everything that arrived. For durable surfaces, it also creates a writeback record so the eventual reply will be delivered even if the admitting process was not the surface itself.

#### Function details

##### `Admission.admit_member`  (lines 90–109)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None) -> Admitted
```

**Purpose**: Admits a message spoken by a workspace member into a conversation. Surfaces use this path for real user/member input, so it also participates in resuming any pending one-time pause for that conversation.

**Data flow**: It receives the workspace, conversation, message text, optional speaker member, optional idempotency key, and optional context. It wraps the conversation as a pending pause candidate, then passes everything to the shared admission routine. It returns an Admitted result that says which turn the message belongs to and whether this delivery opened a new run.

**Call relations**: This is one of the public doors into Admission._admit. It supplies the member-specific information and the pending-pause marker, then relies on the shared routine to do locking, deduplication, seat checks, spend checks, folding into live turns, and queueing.

*Call graph*: calls 1 internal fn (_admit); 1 external calls (__init__).


##### `Admission.invoke`  (lines 111–131)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: Starts a turn from inside the system, such as from an extension or job, while asserting which agent the conversation is supposed to use. This prevents internal callers from silently switching a conversation to a different agent.

**Data flow**: It receives the workspace, conversation, asserted agent, message, optional idempotency key, and optional context. It sends those to the shared admission routine without a speaker member and without a pending member pause. It returns only the admitted turn id, because internal callers usually just need to know which turn was started or joined.

**Call relations**: This is the internal-invocation door into Admission._admit. Unlike member admission, it does not consume a member’s pending pause; it asks the shared routine to verify the agent binding and decide whether to create, deduplicate, fold, park, cancel, or enqueue the turn.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission.invoke_scheduled`  (lines 133–177)

```
async def invoke_scheduled(self, workspace_id: UUID, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: Admits a turn caused by a scheduled task, such as a timer or repeated schedule. It turns schedule metadata into the inbound message the agent will see and safely handles the case where another worker already superseded the scheduled fire.

**Data flow**: It receives the workspace, a claimed scheduled task, and an optional runtime instruction. It verifies the task was claimed, formats schedule information into the message for recurring tasks, builds an idempotency key from the task and fire time, and calls the shared admission routine. It returns the turn id, or None if the scheduled invocation was superseded before it could be admitted.

**Call relations**: This is the scheduled-task door into Admission._admit. It prepares the scheduled message and catches the private superseded signal raised by the shared routine when the database no longer matches the claimed task.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 179–723)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, p
```

**Purpose**: This is the main admission engine. It is the single place that decides whether an inbound message becomes a new queued turn, joins an existing live turn, resumes a pause, parks because of spend limits, or is cancelled because it is not allowed.

**Data flow**: It receives all admission details: workspace, conversation, optional asserted agent, message body, speaker, idempotency key, context, pause information, scheduled-task information, and optional member-on-behalf-of information. Inside one database transaction, it locks the conversation, confirms the agent and speaker, checks scheduled-task claims, looks for duplicate turn or queued-message records, handles pending pauses, checks for a live turn to fold into, applies seat rules, asks the spend evaluator whether the turn is allowed, writes turn/inbound/writeback/scheduled-task rows, and marks whether dispatch should happen. After the transaction, it may enqueue the turn for workers. It returns an Admitted object naming the turn and saying whether a new run was opened.

**Call relations**: Admission.admit_member, Admission.invoke, and Admission.invoke_scheduled all funnel into this function so no caller can bypass the boundary checks. When a turn actually needs worker execution, it hands off to Admission._enqueue. It also creates Admitted results for callers and uses supporting pieces such as Seats for seat limits, SpendEvaluator for spend-cap decisions, TerminalFrame for cancellation messages, and TurnContext conversion when storing or restoring context.

*Call graph*: calls 1 internal fn (_enqueue); called by 3 (admit_member, invoke, invoke_scheduled); 15 external calls (__init__, __init__, __init__, __init__, model_dump, model_validate, delete, exists, insert, select (+5 more)).


##### `Admission._enqueue`  (lines 725–765)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Places an admitted queued turn onto the DBOS work queue, which is the background workflow system that will run the turn. It also cleans up the database marker if enqueueing fails or is cancelled, so the turn can be retried later.

**Data flow**: It receives the workspace, conversation, turn id, and optionally a specific workflow id. It builds queue options including the queue name, workflow name, workflow id, conversation partition key, and app version, then asks DBOS to enqueue the work. If the enqueue is cancelled or errors, it opens a database transaction and clears the turn’s dispatch timestamp while the turn is still queued; on ordinary errors it also logs that enqueue was deferred.

**Call relations**: Admission._admit calls this only after it has committed the database decision that a turn should run. This separation is important: the durable turn row exists first, then the queue offer is attempted. If the queue offer is ambiguous or fails, the database state makes later redispatch safe.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 776–791)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: Provides a workspace-bound helper for internal code that wants to invoke a conversation turn. It saves callers from repeatedly passing the workspace id and limits them to the internal invocation path.

**Data flow**: It receives a conversation id, agent id, message, optional idempotency key, and optional context. It combines those with the workspace id stored on the AdmissionInvoker and calls Admission.invoke. It returns the resulting turn id.

**Call relations**: Jobs and extension workflows can be given this smaller capability instead of the full Admission object. In the larger flow, it forwards internal requests to Admission.invoke, which then enters the shared Admission._admit path without consuming member pause state.


##### `AdmissionInvoker.invoke_scheduled`  (lines 793–796)

```
async def invoke_scheduled(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: Provides a workspace-bound helper for admitting a scheduled task. It lets internal scheduling code fire a claimed task without being given broader admission powers.

**Data flow**: It receives a ScheduledTask and optional runtime instruction. It adds the stored workspace id and calls Admission.invoke_scheduled. It returns the admitted turn id, or None if the scheduled fire was superseded.

**Call relations**: This is a narrow wrapper around Admission.invoke_scheduled. It fits into the scheduling flow by passing claimed scheduled tasks into the same shared admission machinery used by all other turn sources.


##### `MemberAdmission.admit`  (lines 807–823)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> Admitted
```

**Purpose**: Provides a workspace-bound helper for surfaces that receive member messages. It ensures surface code can admit member input but does so through the member-safe path that consumes pending pauses when appropriate.

**Data flow**: It receives a conversation id, message, optional idempotency key, optional context, and required speaker member id. It combines these with the stored workspace id and calls Admission.admit_member. It returns an Admitted result showing which turn accepted the message and whether this delivery opened a run.

**Call relations**: Surfaces are given this limited capability instead of the full Admission object. In the larger flow, it forwards user/member messages to Admission.admit_member, which then enters Admission._admit with the pending-pause behavior enabled.

## 📊 State Registers Touched

- `reg-database-schema` — The shared database layout and migration version that define which long-term records the system can store.
- `reg-workspace-boundary` — The current workspace or tenant boundary used to keep each customer’s data and actions separate.
- `reg-pricing-table` — The shared price list used to turn model and service usage into cost records.
- `reg-auth-session` — The signed login and identity state that proves which member or operator is using the system.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-membership-and-seats` — The shared membership, admin role, paid seat, and seat-limit state for a workspace.
- `reg-agent-identity` — The saved identity and settings of each agent, including its main workspace role and whether it may use the internet.
- `reg-audience-policy` — The saved visibility rules that decide which people may see or use a conversation or agent.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-inbound-message-state` — The saved incoming messages and surface-provided context waiting to be admitted into a conversation turn.
- `reg-transcript-state` — The stored conversation transcript, including exact recent messages and compact summaries of older content.
- `reg-surface-installations` — The saved Slack, web, terminal, and other surface bindings used to receive messages and send replies back.
- `reg-background-jobs` — The shared registry and saved queue of scheduled, recurring, delayed, and administrative background work.
- `reg-fleet-presence` — The shared record of live runtime processes used for supervision, cancellation, and recovery after crashes.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-acting-principal-scope` — The current acting principal context—member, agent, on-behalf-of member, and object/agent scope—used to authorize actions, attribute turns, choose grants, and keep tool work tied to the right actor.
