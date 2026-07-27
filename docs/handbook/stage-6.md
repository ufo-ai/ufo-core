# Conversation admission, policy checks, and durable turn enqueueing  `stage-6`

This stage is the gatehouse for a new conversation turn. After a message has been cleaned into a standard shape, it comes here before the system spends effort answering it. The goal is to accept only work that is allowed, affordable, not a repeat, and ready to process safely in order.

The main front door is `admission.py`. It runs the incoming message through a fixed checklist: is the workspace allowed to use the agent, is the spending limit okay, has this delivery already been seen, is the conversation paused, and can the turn be queued? If the answer is yes, it writes a durable turn record, meaning a saved record that survives restarts, and places the turn onto the background work queue.

`seats.py` supplies one important part of that checklist. It decides which workspace members the agent may answer, based on seat limits, automatic seat assignment, owner rules, and refusals when no seat is available. Together, these files prevent unwanted or duplicate work from entering the main processing loop.

## Files in this stage

### Conversation Admission and Seat Policy
Incoming conversation turns are admitted through shared checks, including seat eligibility, before being durably queued for processing.

### `core/src/ufo/surfaces/admission.py`

`orchestration` · `request handling`

This file protects the boundary where a message becomes work for the agent. Think of it like a ticket desk at a busy clinic: it decides whether a new request should get its own ticket, be added to the patient already being seen, wait until payment is approved, or be refused with a clear reason.

The central class, Admission, accepts three kinds of input: a member speaking through a surface, an internal extension or job invoking the agent, and a scheduled task firing. All of them end up in the same private path, _admit, so callers cannot skip important rules.

Inside _admit, the code locks the conversation row in the database. A lock means only one admission decision can assign the next sequence number at a time. It then confirms the conversation is still bound to the expected agent, checks that the speaker is valid, looks for an existing turn with the same idempotency key, and decides whether the message should join a live turn instead of creating a new one. An idempotency key is a caller-provided duplicate marker, used so retrying the same request does not create two turns.

Before any turn is sent to the worker queue, the file checks seat permissions and spending limits. If allowed, the turn is queued. If spending is paused, the turn is parked. If refused, the turn is immediately marked cancelled so the caller still gets a finished result. For durable surfaces, it also creates a writeback row so the reply can be delivered later even if the original caller is gone.

#### Function details

##### `Admission.admit_member`  (lines 87–106)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: This is the public path for admitting a message spoken by a workspace member. It marks the message as member-originated, which matters because member messages can resume a pending one-time pause in the conversation.

**Data flow**: It receives the workspace, conversation, message text, optional speaker member, optional duplicate marker, and optional context. It wraps the workspace and conversation as a pending pause target, then passes everything into the shared admission routine. The result is the UUID of the turn that accepted the message, whether that is a new turn or an existing live turn.

**Call relations**: Surface code calls this when a person sends a message. This function does not make the admission decision itself; it hands the request to Admission._admit with the extra signal that member admission is allowed to consume a pending pause.

*Call graph*: calls 1 internal fn (_admit); 1 external calls (__init__).


##### `Admission.invoke`  (lines 108–127)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: This is the public path for internal code, such as jobs or extensions, to ask an agent to run in a conversation. It requires the caller to name the agent it believes owns the conversation, so the conversation cannot silently switch agents.

**Data flow**: It receives the workspace, conversation, asserted agent, message text, optional duplicate marker, and optional context. It forwards them to the shared admission routine without a speaker member and without pending-pause authority. It returns the UUID of the turn that was created, reused, or joined.

**Call relations**: Internal callers use this when they need to trigger a conversation turn. It delegates to Admission._admit, which checks that the asserted agent matches the conversation’s stored agent before any work is queued.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission.invoke_scheduled`  (lines 129–172)

```
async def invoke_scheduled(self, workspace_id: UUID, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This admits a turn caused by a scheduled task, such as a recurring reminder or a one-time pause timer. It formats the scheduled message and makes sure only a claimed, still-current scheduled task can fire.

**Data flow**: It receives a workspace, a scheduled task record, and optionally a runtime instruction. It rejects impossible inputs, builds a stable idempotency key from the task and fire time, adds scheduled-task metadata to recurring prompts, and calls the shared admission routine. It returns the admitted turn UUID, or None if another worker already superseded this scheduled firing.

**Call relations**: The scheduler calls this after claiming a task. It hands the prepared scheduled input to Admission._admit. If _admit reports that the schedule was superseded, this wrapper turns that internal race condition into a harmless None result.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 174–692)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, p
```

**Purpose**: This is the main admission decision-maker for all inbound work. It decides whether a message creates a new turn, joins an existing live turn, resumes a pause, stays parked because spending is capped, or finishes immediately as cancelled.

**Data flow**: It receives all details about the attempted admission: workspace, conversation, optional asserted agent, message body, speaker, duplicate marker, context, pending-pause authority, scheduled-task details, and optional member behalf information. It opens a database transaction, locks the conversation, validates agent and speaker identity, checks for duplicate requests, looks for live or parked turns, applies seat and spending decisions, writes or updates turn rows, inbound-message rows, scheduled-task rows, and writeback rows, then commits. After the commit, it may enqueue the turn for background execution. It returns the turn UUID that accepted the message, or raises if the request is invalid.

**Call relations**: Admission.admit_member, Admission.invoke, and Admission.invoke_scheduled all funnel into this function so the same rules apply everywhere. When it decides a queued turn should run now, it calls Admission._enqueue to place the turn onto the DBOS workflow queue. It also relies on seat checking, spend evaluation, turn context serialization, and database operations to make one atomic admission decision.

*Call graph*: calls 1 internal fn (_enqueue); called by 3 (admit_member, invoke, invoke_scheduled); 14 external calls (__init__, __init__, __init__, model_dump, model_validate, delete, exists, insert, select, update (+4 more)).


##### `Admission._enqueue`  (lines 694–734)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: This places an admitted queued turn onto the DBOS work queue, which is the background system that will actually run the turn. It also repairs the database marker if queueing fails or is cancelled.

**Data flow**: It receives the workspace, conversation, turn ID, and optionally a workflow ID. It builds queue options that identify the queue, workflow name, workflow ID, conversation partition, and app version, then asks DBOS to enqueue the work. If enqueueing is cancelled or errors, it clears the turn’s dispatch timestamp in the database so another retry can safely try again; on ordinary errors it also writes a log entry.

**Call relations**: Admission._admit calls this only after the database transaction has recorded that a turn should be dispatched. This function is the handoff from durable admission records to the background worker queue.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 745–760)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: This is a workspace-bound helper for internal invocation. It lets jobs or extension workflows invoke an agent without being able to pretend to be member admission.

**Data flow**: It receives a conversation, agent, message, optional duplicate marker, and optional context. It adds the workspace ID stored in the AdmissionInvoker and forwards the request to Admission.invoke. It returns the UUID of the turn that accepted the invocation.

**Call relations**: Code that should only have internal-invocation power receives this wrapper instead of the full Admission object. The wrapper narrows what the caller can do, then delegates to Admission.invoke.


##### `AdmissionInvoker.invoke_scheduled`  (lines 762–765)

```
async def invoke_scheduled(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This is a workspace-bound helper for firing scheduled tasks. It allows scheduler-related code to admit scheduled work without exposing broader admission powers.

**Data flow**: It receives a scheduled task and optional runtime instruction. It supplies the stored workspace ID and forwards the task to Admission.invoke_scheduled. It returns the admitted turn UUID, or None if the scheduled firing was superseded.

**Call relations**: Scheduler or job code uses this wrapper when it is already operating within one workspace. The real scheduled-admission logic remains in Admission.invoke_scheduled.


##### `MemberAdmission.admit`  (lines 776–792)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: This is a workspace-bound helper for surfaces that accept real member messages. It ensures those messages go through the member-admission path, including pause-resume behavior and speaker checks.

**Data flow**: It receives a conversation, message, optional duplicate marker, optional context, and required speaker member ID. It adds the stored workspace ID and calls Admission.admit_member. It returns the UUID of the turn that accepted the member’s message.

**Call relations**: Surface integrations receive this limited wrapper instead of the full Admission object. That design makes member messages consistently consume pending one-time pauses, while preventing surface code from using internal invocation paths by accident.


### `core/src/ufo/seats.py`

`domain_logic` · `request handling and cross-cutting admission checks`

A “seat” is permission for a workspace member to talk to the agent. This file is the rulebook for that permission. Without it, different parts of the system could disagree about who may use the agent, when a new member should be admitted automatically, or whether the owner can accidentally be locked out.

The core idea is simple: a workspace may have no seat rules at all, in which case everyone is admitted. Or it may have a limit, and possibly an “included” allowance, meaning some members get seats automatically and extra seats need an explicit grant from the owner. A member who has no seat is still stored as a real member, but the agent refuses their turns until a seat is granted.

The file reads and writes the workspace and member database tables. The Seats class is the main tool: it can check whether a workspace is gated, decide whether one member is admitted, show a snapshot of all seats, grant or revoke a seat, set limits, and automatically seat a newly created member when space is available. It also protects the first member, treated as the owner, from losing their seat. A small short-lived cache avoids repeated database checks for workspaces known to have no seat limit, like remembering “this door is open” for a few seconds.

#### Function details

##### `gate_member`  (lines 45–59)

```
def gate_member(speaker_member_id: UUID | None, admission_source: TurnAdmissionSource, on_behalf_of_member_id: UUID | None) -> UUID | None
```

**Purpose**: Decides which member a turn should be checked against for seat permission. Usually that is the speaker, but for scheduled work it can be the member the scheduled task acts for.

**Data flow**: It receives the speaker member ID, the source of the turn, and an optional “on behalf of” member ID. It first uses the speaker if one is known; if not, and the turn came from scheduled work, it uses the creator or represented member; otherwise it returns nothing, meaning no member should be seat-gated.

**Call relations**: This is the shared answer to the question “whose seat matters here?” Admission, resumed work, sweeps, and repeated per-round checks can all use the same rule instead of each inventing a slightly different one.


##### `seat_gate_absent`  (lines 62–68)

```
def seat_gate_absent(workspace_id: UUID) -> bool
```

**Purpose**: Quickly tells callers whether this workspace was recently seen to have no seat gate at all. This lets common unlimited workspaces skip an extra database read for a few seconds.

**Data flow**: It receives a workspace ID and looks in a small in-memory dictionary for an expiry time. If the saved time is still in the future, it returns true; otherwise it returns false.

**Call relations**: It is the read side of the short-lived cache filled by _note_absent_limit. Per-round enforcement can call it before doing slower database work.

*Call graph*: 1 external calls (monotonic).


##### `_note_absent_limit`  (lines 71–76)

```
def _note_absent_limit(workspace_id: UUID) -> None
```

**Purpose**: Records that a workspace currently has no seat limit or included-seat setting. This is an internal helper for the fast path used by ungated workspaces.

**Data flow**: It receives a workspace ID, checks the current clock, removes expired cache entries if the cache is full, and stores a new expiry time a few seconds in the future. It returns nothing but changes the in-memory cache.

**Call relations**: Seats.gated and Seats.admits call this after they read the database and discover that the workspace has no seat gate. Later, seat_gate_absent can use that note to skip repeated reads.

*Call graph*: called by 2 (admits, gated); 1 external calls (monotonic).


##### `SeatSnapshot.seated`  (lines 106–107)

```
def seated(self) -> int
```

**Purpose**: Counts how many members in a seat snapshot currently have seats. It gives callers a ready-made total instead of making them count by hand.

**Data flow**: It reads the snapshot’s member entries, counts the entries marked as seated, and returns that number. It does not change the snapshot.

**Call relations**: This property belongs to SeatSnapshot, which Seats.snapshot creates when someone needs a human-readable or tool-readable view of the current seat state.


##### `Seats.gated`  (lines 118–132)

```
async def gated(self, connection: AsyncConnection) -> bool
```

**Purpose**: Checks whether this workspace enforces seat rules at all. A workspace is ungated when both the hard seat limit and the included-seat allowance are unset.

**Data flow**: It receives a database connection through which it reads the workspace’s seat_limit and included_seats fields. If both are empty, it notes that in the fast-path cache and returns false; otherwise it returns true.

**Call relations**: Callers use this when they need to know whether seat checks should apply. When it finds no gate, it hands that fact to _note_absent_limit so later checks can be cheaper.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.admits`  (lines 134–161)

```
async def admits(self, connection: AsyncConnection, member_id: UUID) -> bool
```

**Purpose**: Decides whether one specific member may be answered by the agent in this workspace. Ungated workspaces admit everyone who is a member; gated workspaces admit only members with a seat.

**Data flow**: It receives a database connection and member ID. It looks up that member together with the workspace’s seat settings. If the member is not found, it returns false. If the workspace has no gate, it records that fast-path fact and returns true. Otherwise it returns whether the member has a seated_at timestamp.

**Call relations**: This is the main yes-or-no admission check used by the seat gate. It relies on the database for the current member and workspace state and updates the no-limit cache through _note_absent_limit when possible.

*Call graph*: calls 1 internal fn (_note_absent_limit); 2 external calls (execute, select).


##### `Seats.snapshot`  (lines 163–185)

```
async def snapshot(self, connection: AsyncConnection) -> SeatSnapshot
```

**Purpose**: Builds a full picture of the workspace’s seat situation: the limits, each member’s email, who is seated, and who is considered the owner. This is useful for reports, owner tools, or billing-related views.

**Data flow**: It receives a database connection, reads the workspace’s seat settings, then reads all members in creation order. It turns those rows into SeatEntry objects and returns a SeatSnapshot containing the limit, included allowance, and member list.

**Call relations**: This is the read-only overview companion to the write methods such as grant and revoke. It packages database rows into simple data objects that other parts of the system can show or inspect.

*Call graph*: 4 external calls (__init__, __init__, execute, select).


##### `Seats.grant`  (lines 187–199)

```
async def grant(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Gives a seat to the member with the given email address. It refuses to exceed the workspace’s hard limit, so granting another seat may require revoking one first.

**Data flow**: It receives a database connection and email address. It locks and reads the workspace limits, finds the member by email, returns quietly if the member is already seated, checks the current seated count against the limit, and either raises SeatLimitReached or writes a seated timestamp for that member.

**Call relations**: Owner or billing tools can call this when someone explicitly grants a seat. It uses _locked_limits to avoid race conditions, _member_by_email to identify the person, _seated_count to enforce the limit, and _seat to make the database change.

*Call graph*: calls 4 internal fn (_locked_limits, _member_by_email, _seat, _seated_count); 1 external calls (__init__).


##### `Seats.revoke`  (lines 201–215)

```
async def revoke(self, connection: AsyncConnection, email: str) -> None
```

**Purpose**: Removes a seat from the member with the given email address. It deliberately refuses to revoke the owner’s seat so the workspace cannot lose its only built-in way to grant seats again.

**Data flow**: It receives a database connection and email address. It locks the workspace limit row, finds the member, checks whether that member is the owner, and raises OwnerSeatRevocation if so. If the member is already unseated it does nothing; otherwise it clears seated_at and updates the member row.

**Call relations**: Seat administration tools call this to free a seat. It asks owner_member_id for the protected owner identity and uses _member_by_email to find the target before writing the database update.

*Call graph*: calls 3 internal fn (_locked_limits, _member_by_email, owner_member_id); 3 external calls (__init__, execute, update).


##### `Seats.ensure_limit`  (lines 217–229)

```
async def ensure_limit(self, connection: AsyncConnection, limit: int) -> None
```

**Purpose**: Sets the workspace’s hard seat limit only if no hard limit has been set yet. This protects an existing manually adjusted value from being overwritten by a repeated setup step.

**Data flow**: It receives a database connection and a proposed limit. If the limit is less than one, it raises an error. Otherwise it updates the workspace row only where seat_limit is currently empty, and returns nothing.

**Call relations**: Billing or setup code can call this when establishing seat rules for a workspace. It does not call other local helpers because its job is a single guarded database update.

*Call graph*: 2 external calls (execute, update).


##### `Seats.ensure_included`  (lines 231–243)

```
async def ensure_included(self, connection: AsyncConnection, included: int) -> None
```

**Purpose**: Sets the number of seats that may be handed out automatically, but only if that number has not already been set. This is the plan’s included allowance.

**Data flow**: It receives a database connection and an included-seat count. If the count is less than one, it raises an error. Otherwise it writes included_seats only when the current value is empty, and returns nothing.

**Call relations**: Setup or billing extension code can call this to establish the silent auto-seat allowance. Like ensure_limit, it is intentionally conservative and will not overwrite an existing value.

*Call graph*: 2 external calls (execute, update).


##### `Seats.auto_seat`  (lines 245–254)

```
async def auto_seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Tries to seat a newly created member automatically if there is still room in the included allowance, or in the hard limit when no included allowance exists. If no automatic space is left, it leaves the member unseated instead of failing member creation.

**Data flow**: It receives a database connection and member ID. It locks and reads the workspace limits, chooses the automatic seating bound, checks how many members are already seated when a bound exists, and either returns without seating or writes a seated timestamp for the new member.

**Call relations**: create_member calls this immediately after inserting a new member. It uses _locked_limits, _seated_count, and _seat so automatic seating follows the same counting and locking rules as explicit grants.

*Call graph*: calls 3 internal fn (_locked_limits, _seat, _seated_count).


##### `Seats._locked_limits`  (lines 256–264)

```
async def _locked_limits(self, connection: AsyncConnection) -> tuple[int | None, int | None]
```

**Purpose**: Reads the workspace’s seat limit and included-seat allowance while locking that workspace row. The lock is like letting one clerk count seats at a time, preventing two grants from racing past the limit.

**Data flow**: It receives a database connection, selects the workspace’s seat_limit and included_seats with a database row lock, and returns those two values. It does not change the values.

**Call relations**: Seats.grant, Seats.revoke, and Seats.auto_seat call this before making seat decisions that must not conflict with another simultaneous decision.

*Call graph*: called by 3 (auto_seat, grant, revoke); 2 external calls (execute, select).


##### `Seats._member_by_email`  (lines 266–279)

```
async def _member_by_email(self, connection: AsyncConnection, email: str) -> tuple[UUID, datetime | None]
```

**Purpose**: Finds a workspace member by email address and reports whether they are already seated. It is an internal lookup used by seat grant and revoke commands.

**Data flow**: It receives a database connection and email address. It trims and lowercases the email for matching, reads the member ID and seated timestamp in this workspace, and returns them. If no member matches, it raises UnknownMember.

**Call relations**: Seats.grant and Seats.revoke call this after locking the workspace limits. It gives those methods the exact member row they need before they decide whether to seat, unseat, or refuse the request.

*Call graph*: called by 2 (grant, revoke); 3 external calls (__init__, execute, select).


##### `Seats._seated_count`  (lines 281–289)

```
async def _seated_count(self, connection: AsyncConnection) -> int
```

**Purpose**: Counts how many members in the workspace currently have seats. This is the number checked before automatic seating or explicit granting.

**Data flow**: It receives a database connection, asks the database to count member rows in this workspace whose seated_at field is not empty, and returns that integer.

**Call relations**: Seats.grant uses this to enforce the hard limit. Seats.auto_seat uses it to decide whether there is still automatic seating room.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, select).


##### `Seats._seat`  (lines 291–296)

```
async def _seat(self, connection: AsyncConnection, member_id: UUID) -> None
```

**Purpose**: Marks one member as seated. It is the small shared write used after higher-level code has already decided that seating is allowed.

**Data flow**: It receives a database connection and member ID. It updates that member row by setting seated_at and updated_at to the current database time, then returns nothing.

**Call relations**: Seats.grant and Seats.auto_seat call this as their final step. Those callers do the rule checking first; this helper only performs the database update.

*Call graph*: called by 2 (auto_seat, grant); 2 external calls (execute, update).


##### `owner_member_id`  (lines 299–309)

```
async def owner_member_id(connection: AsyncConnection, workspace_id: UUID) -> UUID | None
```

**Purpose**: Finds the workspace owner according to this file’s rule: the earliest-created member in the workspace. There is no separate owner column, so this provides one shared definition.

**Data flow**: It receives a database connection and workspace ID. It reads the first member ordered by creation time and ID, then returns that member’s ID, or returns nothing if the workspace has no members.

**Call relations**: Seats.revoke calls this to protect the owner from losing their seat. owner_conversation calls it to know whose conversation should receive workspace-level requests.

*Call graph*: called by 2 (revoke, owner_conversation); 2 external calls (execute, select).


##### `create_member`  (lines 312–345)

```
async def create_member(connection: AsyncConnection, workspace_id: UUID, email: str) -> UUID
```

**Purpose**: Creates a workspace member, or returns the existing member if the same email was created at the same time elsewhere. It also applies the automatic seating rule in the same central place.

**Data flow**: It receives a database connection, workspace ID, and email. It tries to insert a new member with a fresh ID. If the insert succeeds, it calls Seats.auto_seat for that new member and returns the new ID. If the member already exists, it reads and returns the existing member ID.

**Call relations**: All code paths that create members should go through this function so they do not forget seating rules. It constructs a Seats object for the workspace and lets auto_seat decide whether the new member should immediately get a seat.

*Call graph*: 4 external calls (__init__, execute, select, uuid4).


##### `owner_conversation`  (lines 348–371)

```
async def owner_conversation(connection: AsyncConnection, workspace_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Finds the best conversation to use when the system needs to ask the workspace owner something, such as requesting a seat grant. It chooses the owner’s most recently active member-bound conversation.

**Data flow**: It receives a database connection and workspace ID. It first asks owner_member_id for the owner. If there is no owner, it returns nothing. Otherwise it reads the owner’s latest conversation in that workspace and returns the conversation ID and agent ID, or nothing if no such conversation exists.

**Call relations**: Workspace-level seat or billing flows can call this when they need a place to contact the owner. It relies on owner_member_id for the shared owner rule, then performs the conversation lookup itself.

*Call graph*: calls 1 internal fn (owner_member_id); 2 external calls (execute, select).


##### `member_workspaces`  (lines 374–382)

```
def member_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a candidate list for jobs that should run for workspaces that have at least one member. For example, a seat-reporting job can use this instead of knowing the member table details itself.

**Data flow**: It creates an inner query function that selects distinct workspace IDs from the member table, wraps that query with owner_candidates, and returns the resulting WorkspaceCandidates object.

**Call relations**: Extensions or scheduled jobs can declare this as their workspace source. The helper keeps the raw database query in core, then hands it to owner_candidates to fit the broader candidate-selection system.

*Call graph*: 1 external calls (owner_candidates).


##### `member_workspaces.with_a_member`  (lines 379–380)

```
def with_a_member() -> sa.Select[tuple[UUID]]
```

**Purpose**: Defines the actual database query for finding workspaces that have any member. It is nested inside member_workspaces so it stays tied to that candidate source.

**Data flow**: It takes no outside arguments directly. When called, it builds and returns a SQL query selecting distinct workspace IDs from the member table.

**Call relations**: member_workspaces passes this query builder to owner_candidates. The surrounding candidate system can then call it when it needs the set of member-containing workspaces.

*Call graph*: 1 external calls (select).

## 📊 State Registers Touched

- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-member-session-auth` — The signed-in person’s identity and session proof used to decide who is making a request.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-inbound-message-dedup` — The durable inbox and duplicate-detection state for messages arriving from external surfaces.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-durable-work-queue` — The shared queue of conversation and job work waiting to be claimed, retried, resumed, or completed by workers.
- `reg-seat-entitlement` — The workspace membership and seat-limit state that decides which people the agent may serve.
- `reg-spend-ledger-billing` — The shared usage ledger, prices, caps, exports, and billing records used to track and limit spending.
- `reg-scheduled-task-state` — The saved clock-based tasks, waits, pauses, last-run markers, and expiration times used to wake work later.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
