# Conversation admission, queueing, cancellation, and live streaming  `stage-6`

This stage is the traffic controller for conversations after an incoming member event has been accepted. It sits in the main work path, just before and during a “turn,” meaning one user request and the system’s process of answering it. Its job is to decide what should happen next: start a fresh turn, attach the message to one already running, wait because limits are reached, cancel work, or save the message for later.

The admission code is the front door that makes this decision in one safe, locked place, so two messages do not confuse the same conversation. Ambient reply logic is the “should we speak?” filter for group chats, avoiding unnecessary work when the agent was not really addressed. Stop and cancellation code provide the brake pedal: they check permission, tell running work to halt, update records, and notify listeners. The external surface bridge connects web or chat clients to these core actions.

Live update support then acts like a broadcast system, streaming progress, reconnecting clients, and sharing updates across server processes.

## Sub-stages

- [Live turn updates and terminal coordination](stage-6.1.md) `stage-6.1` — 4 files

## Files in this stage

### Turn admission policy
Rules for deciding whether incoming conversation activity should start work, merge into existing work, wait, or stay silent.

### `core/src/ufo/surfaces/admission.py`

`domain_logic` · `request handling and background turn admission`

A “turn” is one unit of conversation work: a member message, a scheduled wake-up, or an internal result that needs the agent to respond. This file makes sure every such turn enters the system through the same gate. That matters because this is where costs, seat access, conversation order, duplicate delivery, and queueing are all checked together. Without this file, two messages could get the same sequence number, a spending cap could be bypassed, a duplicate retry could start duplicate work, or a reply could close while an unread message was still waiting.

The main class, `Admission`, works like a traffic controller at a single-lane bridge. It locks the conversation row in the database, checks who is allowed to speak, checks budget and balance rules, assigns the next sequence number, and decides whether to create a new turn or add the message to the current live turn’s inbound queue. If the turn should run now, it is placed on the durable DBOS workflow queue. DBOS is the background workflow system that later executes the turn.

The wrapper classes narrow what different callers are allowed to do. `MemberAdmission` lets surfaces admit only member-spoken messages. `AdmissionInvoker` lets jobs and extensions admit internal turns without pretending to be a member. `ConnectResume` feeds an account-connection result back into the conversation that requested it, but avoids doing that in special prepared-intent lanes where nobody would read the resulting free-text turn.

#### Function details

##### `_refused`  (lines 90–101)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: Decides what should happen when a turn is not allowed to proceed because of a seat or spending refusal. If the turn already represents paid-for finished work, it parks the turn instead of throwing the result away; otherwise it cancels the turn with a message the user can see.

**Data flow**: It receives a flag saying whether the turn contains work already completed, plus the refusal message. If there is completed work, it returns a parked status and no final message. If not, it builds a terminal cancellation frame containing the refusal text and returns that with a cancelled status.

**Call relations**: `Admission._admit` uses this helper whenever a seat gate, balance gate, or spend decision refuses a turn. The helper keeps that policy in one place so admission treats ordinary new messages and already-completed internal results differently for the right reason.

*Call graph*: called by 1 (_admit); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 110–135)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: Admits a message that came from a real workspace member through a surface such as chat or another user-facing channel. It also validates prepared tool intents, so the stored envelope and the message body cannot disagree.

**Data flow**: It takes the workspace, conversation, message body, optional speaker, optional duplicate-protection key, context, and optional tool intent. It checks that tool intents have a speaker and that the body exactly matches the serialized intent. Then it opens an observation span and passes the request to the shared admission path, marking it as member admission. The result says which turn accepted the message and whether a new run was opened.

**Call relations**: `Admission.redispatch` calls this when it re-admits a pending member message. User-facing surfaces normally enter through this path, which then hands all real decisions to `Admission._admit` so member messages cannot bypass seat, spending, ordering, or deduplication rules.

*Call graph*: calls 1 internal fn (_admit); called by 1 (redispatch); 2 external calls (model_dump_json, span).


##### `Admission.redispatch`  (lines 137–186)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Gives an old, still-unconsumed member message another chance to run. This is used when a message was waiting behind a turn that later ended or was cancelled, so the pending message should either start its own turn or fold into whatever is now live.

**Data flow**: It opens a workspace database transaction, finds the oldest unconsumed inbound message from a member in the conversation, and locks that row. If the row has no idempotency key, it stamps one onto it so retries stay safe. Then it calls `admit_member` with the saved body, speaker, key, and context. It returns the new turn and arrival row only if this redispatch actually opened a run; otherwise it returns nothing.

**Call relations**: This function reuses the normal member admission route instead of inventing a separate retry path. That means an old queued arrival faces the same live-turn folding, seat checks, spending checks, and duplicate handling as a freshly delivered message.

*Call graph*: calls 1 internal fn (admit_member); 4 external calls (model_validate, select, update, workspace_tx).


##### `Admission.invoke`  (lines 188–259)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=Non
```

**Purpose**: Admits an internal turn, such as a scheduled wake-up, an extension-triggered message, or a subagent result. It can also refuse to run if a member has spoken since the caller started waiting, which prevents both a timer and a member reply from resuming the same wait.

**Data flow**: It receives the workspace, conversation, asserted agent, message body, optional idempotency key, context, member authority, scheduling flag, and optional member-message watermarks. It passes these to the shared `_admit` method. If `_admit` reports that a newer member message already superseded the request, this function returns `None`; otherwise it returns the admitted turn id.

**Call relations**: Jobs and extension workflows use this path for non-member work. It delegates to `Admission._admit` so internal work still obeys the conversation’s bound agent, the durable queue rules, spending limits, seat rules when acting for a member, and idempotency rules.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 261–706)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, o
```

**Purpose**: This is the central admission decision-maker. It is the single place that turns an incoming message into one of several outcomes: duplicate of an existing turn, folded arrival on a live turn, newly queued turn, parked turn, or cancelled turn.

**Data flow**: It receives all details about the attempted admission: workspace, conversation, asserted agent, body, speaker, idempotency key, context, source type, spending and seat flags, and optional member-wait watermarks. Inside one database transaction, it locks the conversation, confirms the agent binding, checks the speaker and updates their timezone when provided, looks for duplicate turn or queued-message keys, checks whether a member has superseded an internal wait, and looks for a live turn that can absorb this message. If folding is allowed, it writes an inbound-message row. If a new turn is needed, it assigns the next sequence number, checks seats and spending, inserts the turn, optionally creates a durable writeback row, and decides whether the turn should be enqueued now. After the transaction, it enqueues runnable work and returns an `Admitted` result describing what happened.

**Call relations**: `Admission.admit_member` and `Admission.invoke` both funnel into this method so all admission sources follow the same rules. It uses `_refused` to choose between cancellation and parking on refusal, and `_enqueue` to hand runnable turns to the background workflow queue after the database commit has recorded the turn safely.

*Call graph*: calls 2 internal fn (_enqueue, _refused); called by 2 (admit_member, invoke); 20 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_dump, model_validate, delete, exists (+10 more)).


##### `Admission._enqueue`  (lines 708–748)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Places a queued turn onto the DBOS background workflow queue so a worker can execute it. It also carefully undoes the “enqueue was attempted” marker if the enqueue call is cancelled or fails, so another attempt can happen later.

**Data flow**: It takes the workspace id, conversation id, turn id, and optionally a workflow id. It builds queue options including the queue name, workflow name, workflow id, conversation partition key, and app version, then asks DBOS to enqueue the workflow. If the caller is cancelled or DBOS raises an error, it reopens a transaction and clears the turn’s dispatch timestamp while the turn is still queued. On ordinary errors it logs that enqueueing was deferred instead of crashing the admission result.

**Call relations**: `Admission._admit` calls this only after it has committed the database changes that make the turn real. This separation keeps the database as the source of truth: if the queue offer fails, the turn remains queued in storage and can be retried.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 759–784)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=None, holds_work_alr
```

**Purpose**: Provides a workspace-bound way for internal jobs and extensions to admit turns. It deliberately does not expose the member-admission path, so internal callers cannot claim that their message was spoken directly by a member.

**Data flow**: It receives a conversation, agent, message, optional idempotency key, context, member authority, scheduling flag, and optional wait watermarks. It adds the workspace id stored on the wrapper and forwards everything to `Admission.invoke`. The output is the admitted turn id, or `None` if a member message superseded the internal wait.

**Call relations**: This is a small capability wrapper around `Admission.invoke`. The bigger system can hand this object to trusted internal code when that code should be able to wake or continue a conversation, but only within one workspace and only through the internal admission rules.


##### `MemberAdmission.admit`  (lines 795–813)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Adm
```

**Purpose**: Provides a workspace-bound way for user-facing surfaces to admit member messages. It forces the caller to supply the speaking member, which lets admission apply seat checks and avoid treating an unknown speaker as a real participant.

**Data flow**: It receives the conversation, message, optional duplicate key, context, required speaker member id, and optional tool intent. It adds the wrapper’s workspace id and forwards the call to `Admission.admit_member`. The result tells the surface which turn or arrival accepted the message and whether a new run started.

**Call relations**: Surfaces receive this narrower wrapper instead of the full `Admission` object. That design keeps surface code on the safe member-message path, where `Admission.admit_member` and then `_admit` enforce speaker, seat, spending, folding, and queueing rules.


##### `ConnectResume.resume`  (lines 841–876)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Feeds the result of an account-connection callback back into the conversation that asked for it. It returns a simple success flag so the web page can tell the member whether the system actually accepted the resume message.

**Data flow**: It takes the conversation id, resume message, speaking member id, and idempotency key. First it reads the latest turn in that conversation to see whether the conversation is a prepared-intent lane; if so, it logs a decline and returns `False` because a free-text resume there would not be read properly. Otherwise it reads the current workspace, admits the message as that member through `Admission.admit_member`, and returns `True`. If admission fails, it logs the failure and returns `False`.

**Call relations**: This function is used after an external connect or consent flow has already committed the grant. It hands the outcome back through normal member admission, so a still-running turn can absorb it or an ended conversation can start again, while preserving the same duplicate and queueing protections as any other member message.

*Call graph*: 4 external calls (select, workspace_tx, log, ws_current).


### `core/src/ufo/ambient_reply.py`

`domain_logic` · `request handling, before starting a new agent turn`

In a busy chat thread, people may talk to the agent, talk about the agent, or talk only to each other. This file is the small gatekeeper that decides which kind of message has arrived before the system starts a full agent turn. Without it, every unmentioned reply in a thread could become a costly agent response, even when two humans are simply continuing their own conversation.

The key idea is to ask a cheap model one narrow question: should the agent reply, yes or no? The file defines the exact instructions for that model. Those instructions tell it to look at the recent thread, apply ordered rules, and answer with only `REPLY` or `NO_REPLY`. The model is given a short history window, not the whole thread, so the check stays bounded and cheap.

Messages are packaged carefully. Each message records who spoke, whether it was written by the agent, and the text. The text is put into JSON, which is a structured data format, and wrapped between fence lines so that a user’s message cannot pretend to be part of the system instructions. If the model gives an unreadable answer, this code raises an error instead of silently choosing. The caller can then choose the safer path: admit the turn rather than accidentally ignore someone who needed the agent.

#### Function details

##### `MeteredModel.model`  (lines 94–94)

```
def model(self) -> str
```

**Purpose**: This property tells the classifier which model name it should use for the cheap reply-or-stay-quiet decision. It is part of a small interface, meaning this file only requires that any supplied model object can reveal its model name.

**Data flow**: The classifier has a model-like object. It reads this property to get a model identifier, then places that identifier into the request it sends for the decision. Nothing is changed by reading it.

**Call relations**: When `AmbientReplyClassifier.decide` builds the model request, it uses this property so the request goes to the intended ambient-reply classifier model rather than hard-coding that choice here.


##### `MeteredModel.complete`  (lines 96–96)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This method represents the one-shot call to the language model that returns the decision text. The classifier depends on it to ask, in a controlled and metered way, whether the agent should reply.

**Data flow**: A prepared `ModelRequest` goes in. The model provider reads the prompt and thread payload, then returns text that should contain `REPLY` or `NO_REPLY`. This protocol does not say how the provider works internally; it only states what the classifier needs from it.

**Call relations**: `AmbientReplyClassifier.decide` calls this method after building the request. The returned text is then checked by `decide` to extract the final decision.


##### `_entry`  (lines 99–104)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This helper turns one chat message into a small, safe dictionary for the model payload. It keeps only the speaker, whether the agent wrote it, and a shortened copy of the text.

**Data flow**: An `AmbientMessage` goes in. The function copies its speaker, own-message flag, and text, trimming the text to the configured character limit. A plain dictionary comes out, ready to be turned into JSON.

**Call relations**: `AmbientReplyClassifier._payload` calls this for each recent history message and for the new message. It is the small adapter that makes the thread data uniform before JSON serialization.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 117–133)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision function. Given a new ambient message and recent thread history, it asks the cheap classifier model whether the agent should start a new turn.

**Data flow**: The new message and recent history go in. The function builds a fenced JSON payload with `_payload`, places it inside a model request together with the system rules, and sends it to the configured model. It then scans the model’s answer for `REPLY` or `NO_REPLY`; if it cannot find either, it raises an error instead of guessing. The output is the final decision word.

**Call relations**: This function is the entry point for callers that need the gatekeeping decision. It hands the message packaging work to `AmbientReplyClassifier._payload`, uses `Message` and `ModelRequest` to form the model call, then relies on `MeteredModel.complete` to get the answer.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 135–151)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This function prepares the thread data that the model will read. It turns recent messages and the new message into a compact JSON object and wraps it in clear boundary lines.

**Data flow**: The current message and full available history go in. The function keeps only the last configured number of history messages, converts each message with `_entry`, serializes the result to JSON, and chooses a fence string that does not already appear in the payload. It returns one string containing the fence, the JSON, and the same fence again.

**Call relations**: `AmbientReplyClassifier.decide` calls this just before making the model request. Inside, it calls `_entry` to standardize each message and `json.dumps` to produce the JSON text the model will receive.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).


### Turn cancellation
Member stop requests and shared cancellation mechanics for safely ending running work.

### `core/src/ufo/surfaces/stop.py`

`domain_logic` · `request handling`

A “turn” is one running unit of conversation work. This file exists for the moment when a member presses stop. Stopping is not just flipping a switch: the system must first make sure the turn really belongs to the conversation the member is viewing, then cancel it in durable storage, then wake up anyone watching that turn so their screen does not sit waiting.

The main piece is `MemberStop`, a small workflow object with three collaborators. It uses the database to check ownership, a shared cancellation primitive to end the turn, an admission component to start or redispatch the next turn when a pending member message already exists, and a hub to publish live updates. The hub is like a noticeboard for running clients: when a terminal message is posted, listeners know the turn is over.

One important detail is ordering. If a follow-up turn is created, this file publishes that new turn’s absorbed arrival before publishing the cancelled terminal for the old turn. That way, anyone woken by the cancellation can immediately see that the conversation has already moved on. If the turn already ended by itself, stopping does nothing harmful and reports that nothing was newly ended.

#### Function details

##### `MemberStop.stop`  (lines 38–61)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops one running turn, but only if it belongs to the requested conversation in the requested workspace. It safely cancels the turn, optionally starts a follow-up turn, publishes live updates, and returns a `Stopped` result telling the caller what happened.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. First it opens a workspace database transaction and reads which conversation owns that turn. If the turn is not part of the given conversation, it raises an error instead of cancelling the wrong work. If ownership is correct, it asks the shared cancellation routine to cancel the turn. If that routine says the turn was already finished, it returns `ended=False`. Otherwise it asks admission to redispatch any pending member message into a new turn. If a new turn is founded, it publishes an `Absorbed` notice for that new turn. Finally it publishes a `Terminal` notice for the cancelled old turn and returns `ended=True`, including the new turn ID when there is one.

**Call relations**: This is the end-to-end stop path used when the surface layer wants to stop a member’s running turn. It relies on `workspace_tx` and `sqlalchemy.select` to prove the turn belongs to the conversation before acting. It then hands cancellation to `cancel_one_turn`, wraps live-update messages as `Absorbed` and `Terminal`, and returns a `Stopped` answer for the surface to use when updating the member’s view.

*Call graph*: 6 external calls (__init__, __init__, __init__, select, cancel_one_turn, workspace_tx).


### `core/src/ufo/cancellation.py`

`domain_logic` · `cancel handling`

A “turn” is a unit of work, and some turns can start child turns that run as their own durable workflows. Cancelling a parent does not automatically stop those children, so this file focuses on one clear job: cancel exactly one turn, in a way every caller can trust. Think of it like stamping a work order “cancelled” only after you have first sent the stop order to the crew doing the work.

The important rule here is order. The code first checks the turn row in the database. If the turn does not exist, or if it has already finished, failed, or otherwise reached an end state, it leaves it alone. If the turn is still active, it asks DBOS, the durable workflow system, to cancel the workflow for that turn. Only after that cancellation request is durably recorded does it update the turn row to the terminal status “cancelled.”

This protects the system during crashes or races. If something goes wrong between asking DBOS to cancel and updating the database, the row is still non-terminal, so recovery or reconciliation can find it later. It avoids the dangerous opposite case: a database row claiming “cancelled” when the workflow was never actually told to stop. The function also records what objects the turn had already created, so observers still learn what came out of a partially completed turn.

#### Function details

##### `cancel_one_turn`  (lines 23–83)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Cancels one turn safely and records that cancellation in the database if the turn was still unfinished. It returns the final cancellation frame only when this call actually changed the turn to cancelled; otherwise it returns nothing.

**Data flow**: It receives a DBOS client and a turn id. It reads the turn’s current database row to see whether the turn exists and is still non-terminal, meaning not already finished. If it is eligible, it asks DBOS to cancel the workflow named by that turn id, then reads the objects the turn already created, builds a cancelled terminal record from them, and tries to update the row to cancelled. If that update succeeds, it emits a metric counting the cancelled turn and returns the terminal record; if another process finished or cancelled the turn first, it returns null instead.

**Call relations**: This is the shared cancellation primitive used by higher-level cancel paths, such as a user-facing cancel action, an evaluation driver, or a reconciler that cleans up descendant turns. Inside its flow it opens database transactions with `workspace_tx`, uses SQLAlchemy queries to read and update the turn row, asks `DBOSClient.cancel_workflow_async` to stop the durable workflow, builds a `TerminalFrame` containing validated `ObjectRef` entries for already-created objects, and finally reports the outcome through `emit_metric` with profile details from `turn_profile`.

*Call graph*: 8 external calls (__init__, model_validate, cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile).


### Surface integration bridge
External chat and web surface orchestration for member identity, conversations, admitted messages, live streams, replies, and portal data.

### `core/src/ufo/ext/surface.py`

`orchestration` · `cross-cutting: request handling, live streaming, credential handoff, portal reads, and background delivery`

A “surface” is any outside-facing place where a member interacts with UFO, such as Slack, iMessage, the web app, or a persistent listener. This file defines the privileged doorway those surfaces use. That matters because surfaces are allowed to say who a user is and submit messages as that user, which ordinary extensions are not allowed to do.

The file has three large jobs. First, it prepares inbound member text safely: it wraps the member’s words and attachments in unique markers, cleans attachment filenames, and can later recover just what the member said. Second, it defines SurfaceContext, a large toolbox handed to surface code. Through it, a surface can link an external identity to a workspace member, create or find conversations, admit turns, tail live updates, read transcripts, list agents and files, mint download links, handle credential handoffs, and query admin portal views. Third, it runs background delivery for durable surfaces. A durable surface cannot keep a browser-like connection open, so the WritebackPoller and MidTurnReplyPoller repeatedly claim undelivered replies from the database, call the surface’s send functions, and mark success or schedule retry.

Think of this file as the guarded service desk between UFO’s private machinery and the public counters where members talk to it.

#### Function details

##### `mint_marker`  (lines 201–211)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to label one member message’s wrapper tags. The marker makes it extremely unlikely that text typed by a member can accidentally imitate the system’s own message boundary.

**Data flow**: It takes no input, asks the secrets library for random bytes written as hex text, and returns that marker string.

**Call relations**: It is used when preparing inbound text before a surface admits a member message.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 214–227)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the exact text that enters the transcript for one member message. It separates ambient context, the member’s own words, and attachment output into clearly named sections.

**Data flow**: It receives a marker, ambient text, message body, and attachment text. It wraps the body and optional attachments in marker-specific tags and returns one combined string.

**Call relations**: Surface ingests use this before admission so later transcript readers can distinguish what the member actually said from context and attachment material.


##### `inbox_name`  (lines 230–259)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an untrusted incoming filename into a safe file leaf name for the workspace. It prevents path tricks, unsafe characters, overlong names, and duplicate names in one batch.

**Data flow**: It receives the raw filename and a set of names already used. It strips path parts, replaces unsafe characters, preserves useful suffixes where possible, adds a number if needed, updates the used set, and returns the safe name.

**Call relations**: All surfaces should use this shared helper when storing attached files so each surface follows the same safety rule.

*Call graph*: 1 external calls (contained_leaf).


##### `member_message_text`  (lines 262–276)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts just the member’s own words from an admitted inbound message. This is useful when a display should show what the person said, not the hidden context the engine also saw.

**Data flow**: It receives the stored inbound text, removes known engine context wrappers, looks for the marker-based member-message wrapper, and returns either the inner text or the original text if no wrapper is present.

**Call relations**: conversation_name calls it to title new conversations from the member’s words rather than surrounding context.

*Call graph*: called by 1 (conversation_name).


##### `MemberAdmitter.admit`  (lines 313–322)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Adm
```

**Purpose**: Defines the interface for admitting a member message into UFO’s durable turn queue. Concrete implementations decide whether the message starts a new turn or joins an existing live one.

**Data flow**: It receives conversation identity, message text, optional idempotency key, context, speaker member id, and optional prepared tool intent. It returns an Admitted result naming the turn and whether a run was opened.

**Call relations**: SurfaceContext.admit delegates to this protocol so surfaces do not directly touch the queue implementation.


##### `TurnTailer.tail`  (lines 334–336)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines the interface for reading live frames from a running turn. Live surfaces use it to stream updates to a member while the turn is still running.

**Data flow**: It receives a turn id and optional cursor, opens an async scope, and yields frame cursor plus frame pairs until the turn ends or the scope closes.

**Call relations**: SurfaceContext.tail exposes this to web, debugger, sample, and UFO surfaces without exposing the hub directly.


##### `TurnStopper.stop`  (lines 346–346)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines the interface for stopping a running turn at a member’s request. It also reports whether a pending follow-up message founded a next turn.

**Data flow**: It receives workspace, conversation, and turn ids. It cancels or observes the turn and returns a Stopped result.

**Call relations**: SurfaceContext.stop_turn delegates here after the surface has already authorized the acting member.


##### `_media_predicate`  (lines 372–388)

```
def _media_predicate(column: sa.ColumnElement[str], media: str) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database filter for artifact media categories such as image, document, or other. It lets artifact listings narrow results without duplicating media-type rules.

**Data flow**: It receives a database media-type column and a requested category. It returns a SQL condition matching that category, or raises an error for an unknown category.

**Call relations**: SurfaceContext.list_artifacts calls it when the portal filters shared files by media type.

*Call graph*: called by 1 (list_artifacts); 3 external calls (and_, not_, or_).


##### `conversation_name`  (lines 492–497)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Creates a bounded title for a conversation from the opening inbound message. It focuses on the member’s words, not added ambient context.

**Data flow**: It receives inbound text, extracts member text, trims whitespace, cuts it to the title length limit, and returns it.

**Call relations**: Conversation-creation paths use this naming rule so member-started and agent-spawned conversations are titled consistently.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 500–517)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Changes a conversation’s stored title when a surface has a better name for it. Empty titles are ignored.

**Data flow**: It receives workspace id, conversation id, and proposed title. It trims and bounds the title, then updates the matching database row if one exists.

**Call relations**: SurfaceContext.retitle_conversation wraps this helper for the current workspace.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `summarize_conversation_title`  (lines 520–540)

```
async def summarize_conversation_title(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores the title generated by a background summarizing job and records that the job has run. This avoids paying to summarize the same opening exchange repeatedly.

**Data flow**: It receives workspace id, conversation id, and summary title. It writes the nonblank title or keeps the existing one, and marks title_summarized true.

**Call relations**: Titling jobs call this after producing a summary.

*Call graph*: 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 596–597)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures the agent update time has timezone information. This prevents displays and APIs from mixing timezone-aware and timezone-less dates.

**Data flow**: It receives a datetime. If it already has a timezone it returns it; otherwise it marks it as UTC.

**Call relations**: Pydantic calls this automatically when creating AgentDetail values.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 641–642)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a connection timestamp is treated as UTC when the database returned a timezone-less value.

**Data flow**: It receives connected_at and returns the same instant with UTC attached if needed.

**Call relations**: Pydantic runs it while building ConnectionView rows for the portal.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 662–663)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes connection-pool timestamps to timezone-aware UTC values.

**Data flow**: It receives connected_at, keeps it if timezone-aware, or attaches UTC if not.

**Call relations**: Pydantic runs it when SurfaceContext.list_connections builds pool entries.

*Call graph*: 1 external calls (replace).


##### `_binding_fields`  (lines 722–750)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts the identity fields for a connector-backed source binding. These fields let the portal submit actions against exactly the same source object it listed.

**Data flow**: It receives a backend name and stored JSON config. If the config validates as a connector source, it returns the binding name and important spec fields; otherwise it returns None values.

**Call relations**: SurfaceContext.list_sources uses it to enrich SourceView rows.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 776–777)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a source’s next sync time to UTC-aware form.

**Data flow**: It receives next_sync_at and returns it unchanged if timezone-aware, or with UTC added if not.

**Call relations**: Pydantic runs it when creating SourceView records.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 795–798)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation timestamps while allowing the last-turn time to be absent.

**Data flow**: It receives a datetime or None. None stays None; a timezone-less datetime is marked UTC.

**Call relations**: Pydantic applies it to ConversationSummary date fields.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 811–872)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged they are opening another member’s private transcript. The record both audits the act and temporarily permits the read.

**Data flow**: It receives workspace, conversation, agent, and reader member ids. It verifies the conversation belongs to the agent and is another member’s private conversation, writes an access row, logs the disclosure, and returns the reader and subject emails; otherwise it returns None.

**Call relations**: The portal reaches this through a prepared intent before serving private transcript content.

*Call graph*: 9 external calls (__init__, now, insert, select, audience_member, parse_audience, workspace_tx, log, uuid4).


##### `LedgerEntry._aware_utc`  (lines 933–934)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Makes an accounting timestamp timezone-aware as UTC.

**Data flow**: It receives created_at and returns it with UTC attached if needed.

**Call relations**: Pydantic runs it for ledger rows inside turn details.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 990–995)

```
def _fulfilled_marker_key(sealed: str, slot: str) -> str
```

**Purpose**: Builds the blob-store key that records one fulfilled credential prompt. It is keyed by the sealed request and slot so sibling prompts remain independent.

**Data flow**: It receives the sealed request string and slot name, hashes the seal, and returns a marker path.

**Call relations**: SurfaceContext.credential_prompt_pending checks this marker, and fulfill_credential_request writes it.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_main_agent`  (lines 998–1011)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. This is the fallback agent when a surface installation has no explicit binding.

**Data flow**: It receives a workspace id, queries the agent table for the main agent, and returns its id or raises an error if none exists.

**Call relations**: _bind_surface_installation and SurfaceContext._surface_agent call it.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 1014–1046)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str) -> None
```

**Purpose**: Creates or updates the record that ties an external surface installation, such as a Slack team, to a workspace. It keeps the existing agent binding when rebinding the installation identity.

**Data flow**: It receives workspace id, surface name, and installation id. It validates the id, chooses the main agent for a new row, upserts the installation row, and converts uniqueness conflicts into SurfaceInstallationConflict.

**Call relations**: SurfaceContext.bind_installation and SurfaceInstallationAccess.bind both use this shared writer.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.fleet_blob`  (lines 1098–1101)

```
def fleet_blob(self) -> FleetBlobStore
```

**Purpose**: Returns a deploy-wide blob-store view for shared fleet assets. It is separate from the workspace-scoped blob view.

**Data flow**: It reads the backend from the context’s workspace blob store and wraps it as a FleetBlobStore.

**Call relations**: Surface code can call this when it needs shared static data rather than workspace data.

*Call graph*: 1 external calls (__init__).


##### `SurfaceContext.conversation_slots`  (lines 1104–1106)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Exposes the fixed set of extension-provided conversation slots available to this surface.

**Data flow**: It reads the tuple stored on the context and returns it unchanged.

**Call relations**: Web surface code uses this list when rendering conversation slot features.


##### `SurfaceContext.read_conversation_slot`  (lines 1108–1113)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Reads one conversation slot while binding execution to the conversation’s agent. This keeps agent-scoped providers looking at the right agent namespace.

**Data flow**: It receives a bound slot and slot context, temporarily sets the active agent id, calls the provider’s read method, and returns the payload.

**Call relations**: The web surface calls it when serving a conversation slot.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1115–1120)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Runs the summary operation for one conversation slot in the correct agent scope.

**Data flow**: It receives a bound slot and context, binds the context’s agent id, calls the provider’s summarize method, and returns the optional count or marker it produces.

**Call relations**: The web surface calls it while building slot summaries.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.deploy_extensions`  (lines 1123–1126)

```
def deploy_extensions(self) -> tuple[DeployExtensionView, ...]
```

**Purpose**: Returns the installed deploy extensions as shown in administration views.

**Data flow**: It reads and returns the boot-time tuple stored on the context.

**Call relations**: Portal administration pages use this as static deploy-status information.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1129–1132)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Reports whether the deploy’s installed extensions allow sandbox internet access at all.

**Data flow**: It returns the boolean stored on the context.

**Call relations**: Portal settings compare this deploy-wide ceiling with each agent’s own setting.


##### `SurfaceContext.deploy_skills`  (lines 1135–1140)

```
def deploy_skills(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Lists deploy-provided loadable skills. These are the shared skills every agent may use before member-authored skills are added.

**Data flow**: It asks the skill registry for its index and returns that tuple.

**Call relations**: Surfaces use it when showing deploy skill availability.


##### `SurfaceContext.models`  (lines 1143–1147)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Returns the model identifiers this deploy offers.

**Data flow**: It reads the model tuple stored on the context and returns it.

**Call relations**: Portal settings use it to populate model choices.


##### `SurfaceContext.sandbox_sizes`  (lines 1150–1153)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Returns the sandbox sizes this deploy can provision.

**Data flow**: It reads the stored tuple of size names and returns it.

**Call relations**: Portal settings use it to decide whether to show a sandbox-size choice.


##### `SurfaceContext.credential`  (lines 1155–1158)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential value for trusted surface code. It refuses if no credential store was configured.

**Data flow**: It receives a slot name, checks that a store exists, reads the slot for this workspace, and returns the secret value.

**Call relations**: Slack and other surface code call it for signing secrets, provider credentials, and identity proof.

*Call graph*: called by 11 (_channel_origin, _ctx_signing_secret, _identity, _post_ephemeral, _run_identity_proof, _to_inbound, attach, ingest, interactive, post (+1 more)).


##### `SurfaceContext.credential_prompt_pending`  (lines 1160–1174)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs a value for one slot. It prevents already fulfilled or invalid prompts from being shown again.

**Data flow**: It receives a sealed request and slot. It opens and validates the seal, checks workspace and slot membership, looks for the fulfilled marker blob, and returns true only if the prompt is still pending.

**Call relations**: The web surface uses it when rendering pending credential prompts.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 1 (_pending_prompts); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 1176–1186)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential handoff and returns its claims. This lets a surface callback recover which workspace, member, and slots the handoff was for.

**Data flow**: It receives the sealed string, verifies it with the credential store’s Fernet key, and returns the decoded CredentialRequestState or raises if invalid.

**Call relations**: Slack OAuth callbacks use it after the browser returns with sealed state.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 1188–1213)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Stores the value for one credential slot after proving the sealed request belongs to this workspace, member, and slot. It then marks that prompt as fulfilled.

**Data flow**: It receives a seal, slot, value, and member id. It validates all claims, writes the credential, writes a marker blob, and returns nothing; invalid claims raise CredentialRequestInvalid.

**Call relations**: Slack, web, and UFO extension surfaces call it when a member completes a credential handoff.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 1215–1221)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation id to the current workspace.

**Data flow**: It receives an installation id and passes this workspace and surface name to the shared binding helper.

**Call relations**: Slack calls it during OAuth installation.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.public_base_url`  (lines 1224–1227)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deploy’s configured public base URL, if any.

**Data flow**: It reads the stored public URL and returns it or None.

**Call relations**: Surfaces use it to build callback or public links.


##### `SurfaceContext.home_url`  (lines 1229–1239)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link into the deploy’s browser home surface. It returns None when the deploy has no public base URL or no home surface.

**Data flow**: It receives an optional URL fragment, combines the public base, home surface mount path, and fragment, and returns the URL.

**Call relations**: Durable surfaces use it when they need to send a member to the browser portal.

*Call graph*: called by 2 (_terminal_text, _reply_with_oversize_links).


##### `SurfaceContext.shared_artifacts`  (lines 1241–1273)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Lists the files a turn shared, in delivery order. Live surfaces use this to render download links directly.

**Data flow**: It receives a turn id, queries shared_artifact rows for this workspace and turn, converts each row to SharedArtifact, and returns the tuple.

**Call relations**: Web and UFO surfaces call it when showing shared files.

*Call graph*: called by 2 (shared_files, _events); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 1275–1290)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed download link for a shared artifact. If public artifact delivery is not configured, it returns None.

**Data flow**: It receives a SharedArtifact, checks token secret and public base URL, mints an expiring artifact path, and returns the full URL.

**Call relations**: Slack, iMessage, web, and UFO surfaces use it when they need a link instead of inline upload.

*Call graph*: called by 7 (_terminal_text, _oversize_link_line, shared_files, _file_payload, _project_slot_context, _radar_run, workspace_artifacts); 2 external calls (now, mint_artifact_url).


##### `SurfaceContext.artifact_preview_link`  (lines 1292–1325)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed image-preview link when the artifact or its preview blob is a safe raster image. It avoids previewing unknown, mismatched, or too-large bytes.

**Data flow**: It receives an artifact, chooses preview bytes or original bytes, verifies media type and size, mints a preview-granted artifact URL, and returns it or None.

**Call relations**: The web surface uses it when rendering file cards and artifact listings.

*Call graph*: called by 4 (_file_payload, _project_slot_context, _radar_run, workspace_artifacts); 4 external calls (__init__, now, mint_artifact_url, raster_image_media_type).


##### `SurfaceContext.ingress_url`  (lines 1327–1356)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str) -> str | None
```

**Purpose**: Builds a signed browser URL for opening one sandbox port. This lets a surface expose a conversation’s running site without holding ingress secrets itself.

**Data flow**: It receives conversation id, port, and entry path. It mints a short-lived ingress token, builds the stable subdomain for that conversation and port, quotes the path, and returns the URL or None if ingress is unconfigured.

**Call relations**: The sites extension calls it when serving embedded site frames.

*Call graph*: called by 1 (frame); 6 external calls (__init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `SurfaceContext._identity_member`  (lines 1358–1371)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up which workspace member is linked to a given external id on a given surface.

**Data flow**: It receives a surface name and external id, queries surface_identity, and returns the member id or None.

**Call relations**: linked_member and adopt_identity use it as their shared lookup.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 1373–1374)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the member linked to this surface’s external user id.

**Data flow**: It receives an external id, calls the shared identity lookup for this surface, and returns the member id or None.

**Call relations**: Many surfaces call it before admitting or serving user-specific requests.

*Call graph*: calls 1 internal fn (_identity_member); called by 9 (_linked_member, _surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, channel, op_body, _authenticate).


##### `SurfaceContext.is_operator_workspace`  (lines 1376–1383)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace is the fleet operator’s own workspace. This gates operator-only display details.

**Data flow**: It reads the workspace email domain and compares it with the operator domain constant.

**Call relations**: Surface rendering can use it to decide whether to show internal debugging or accounting details.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 1385–1408)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to the same member already known by another surface. This lets one human keep one member identity across surfaces.

**Data flow**: It receives a peer surface and external id, looks up the peer link, inserts a link for this surface if found, logs races, and returns the member id or None.

**Call relations**: The sample live surface uses it when adopting identities from a peer surface.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 1410–1432)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing workspace member by email. It returns None if no member with that email exists.

**Data flow**: It receives external id and email, finds the oldest case-insensitive matching member, then calls link_member_id to create the link.

**Call relations**: join_member uses it first; several surfaces use it during authentication or member resolution.

*Call graph*: calls 1 internal fn (link_member_id); called by 5 (join_member, _surface_ingest, _viewer, channel, _authenticate); 2 external calls (select, workspace_tx).


##### `SurfaceContext.link_member_id`  (lines 1434–1463)

```
async def link_member_id(self, external_id: str, member_id: UUID) -> UUID | None
```

**Purpose**: Links this surface’s external id to a specific existing member id. This is used when the surface has already proved the member’s identity.

**Data flow**: It receives external id and member id, verifies the member belongs to this workspace, inserts the surface_identity row, logs races, and returns the member id or None.

**Call relations**: link_member delegates to it, and iMessage uses it after its own member proof.

*Call graph*: called by 2 (link_member, _linked_member); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 1465–1482)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links an external id by email, and if needed creates a new member when the email domain matches the workspace’s own domain. This supports first-contact joining from trusted channels.

**Data flow**: It receives external id and email, tries linking an existing member, compares the email domain with the workspace domain, creates a member if allowed, then links again.

**Call relations**: Slack uses it when resolving channel-verified users.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 1484–1494)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the database query for finding this surface’s conversation by queue key.

**Data flow**: It receives a queue key and returns a SQL select scoped to workspace, surface, and key.

**Call relations**: conversation_for, find_conversation, and terminal_op_body reuse this query shape.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 1496–1502)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for this surface and queue key without creating one.

**Data flow**: It receives a queue key, runs the shared lookup, and returns the conversation id or None.

**Call relations**: Slack uses it to decide whether an ambient reply belongs to an existing conversation.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 2 (_participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 1504–1517)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation is permanently bound to.

**Data flow**: It receives a conversation id, queries this workspace’s conversation row, and returns the agent id or None.

**Call relations**: The web surface uses it when resolving a chat permalink.

*Call graph*: called by 1 (_resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 1519–1522)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation in this workspace.

**Data flow**: It receives a conversation id and title, then calls the module-level retitle helper with this workspace id.

**Call relations**: Slack and web surfaces call it when they learn a better conversation title.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 2 (_admit_inbound, _open_conversation).


##### `SurfaceContext.conversation_for`  (lines 1524–1618)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation for this surface’s queue key. It also narrows audience information and chooses the agent for new conversations.

**Data flow**: It receives queue key, audience, optional agent id, optional conversation id, and optional label. It reuses an existing row if present, narrows audience and updates labels as needed, or inserts a new conversation bound to an agent.

**Call relations**: Almost every surface calls it before admitting a message or prepared intent.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 8 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, interactive, channel, submit_intent, _open_conversation); 9 external calls (insert, select, update, audience_member, narrow_audience, parse_audience, workspace_tx, log, uuid4).


##### `SurfaceContext._surface_agent`  (lines 1620–1632)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Finds the agent this surface installation is bound to, falling back to the main agent.

**Data flow**: It queries surface_installation for this workspace and surface. If a binding exists it returns that agent id; otherwise it returns the main agent.

**Call relations**: conversation_for calls it when creating a conversation without an explicit agent.

*Call graph*: calls 1 internal fn (_main_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 1634–1665)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Asks the ambient-reply classifier whether the agent should answer an unaddressed message. It fails open so a possible member request is not silently dropped.

**Data flow**: It receives the new ambient message and recent history, calls the classifier with a timeout, logs the result, and returns false only for a definite no-reply decision.

**Call relations**: Slack and iMessage use it before admitting ambient channel traffic.

*Call graph*: called by 2 (_admit_message, _ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext.admit`  (lines 1667–1698)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Admitt
```

**Purpose**: Submits a member message or prepared intent to the core turn queue. The conversation’s bound agent, not the surface, decides which agent runs.

**Data flow**: It receives conversation id, body, optional idempotency key, context, speaker member id, and optional intent. It passes them to the injected MemberAdmitter and returns the Admitted result.

**Call relations**: All message-ingesting surfaces call it after resolving identity and conversation.

*Call graph*: called by 9 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, interactive, _send, channel, submit_intent, chat).


##### `SurfaceContext.connect_url`  (lines 1700–1706)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates an authorization URL for a terminal connect request as the speaking member.

**Data flow**: It receives turn id and member id, obtains the installed connect flow, and asks ConnectHandoff to authorize the member for that turn.

**Call relations**: Slack, iMessage, and web surfaces call it when rendering connect affordances.

*Call graph*: called by 3 (_terminal_text, interactive, _events); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.admitted_body`  (lines 1708–1733)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Looks up what body was admitted under an idempotency key. This helps a surface decide whether a raced click or submit was the one that actually landed.

**Data flow**: It receives an idempotency key, checks matching turn rows first and inbound-message rows second, and returns the stored body or None.

**Call relations**: Slack and web surfaces call it when reconciling idempotent actions.

*Call graph*: called by 3 (_unseen_tail, interactive, chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 1735–1749)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member who owns the conversation containing a turn. Live surfaces use this to prevent one member from tailing another member’s turn.

**Data flow**: It receives a turn id, joins turn to conversation in this workspace, and returns the conversation member id or None.

**Call relations**: Web and sample surfaces call it before live turn access.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 1751–1757)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops a running turn in an already-authorized conversation.

**Data flow**: It receives conversation id and turn id, passes workspace, conversation, and turn to the injected stopper, and returns the Stopped result.

**Call relations**: Web and UFO surfaces call it when a member presses stop.

*Call graph*: called by 2 (channel, chat).


##### `SurfaceContext.retract_arrival`  (lines 1759–1777)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Deletes a pending message that the member sent but no turn has consumed yet.

**Data flow**: It receives conversation id, arrival id, and member id. It deletes the inbound_message row only if it belongs to that member and is unconsumed, then returns whether a row was removed.

**Call relations**: The UFO surface calls it for unsend behavior.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 1779–1796)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a terminal database state. Missing turns count as terminal because there is nothing to keep reporting.

**Data flow**: It receives a turn id, reads its status, and returns true if missing or done, failed, or cancelled.

**Call relations**: Side-channel reporters can use it to avoid posting progress after a final answer.

*Call graph*: 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 1798–1815)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the newest turn in a conversation.

**Data flow**: It receives a conversation id, queries turns in descending sequence order, and returns the latest turn id or None.

**Call relations**: Slack, web, and UFO surfaces use it when resuming or rendering a conversation.

*Call graph*: called by 4 (_participating_conversation, channel, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.absorbing_turn`  (lines 1817–1861)

```
async def absorbing_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Checks whether a new message would fold into an already-running turn. It also verifies spend and balance gates so it does not promise a fold that admission would refuse.

**Data flow**: It receives a conversation id, finds the oldest nonterminal turn, rejects parked turns, evaluates spend and balance, and returns the live turn id only if both gates allow.

**Call relations**: Slack calls it before deciding whether ambient classification applies.

*Call graph*: called by 1 (_folds_into_live_turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.tail`  (lines 1863–1869)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn.

**Data flow**: It receives a turn id and optional cursor, delegates to the injected TurnTailer, and returns an async context manager over frames.

**Call relations**: Web, debugger, sample, panels, and UFO surfaces call it to stream live updates.

*Call graph*: called by 5 (_events, _surface_frames, channel, submit_intent, _events).


##### `SurfaceContext.spend_rollup`  (lines 1871–1874)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace-wide usage and spending for a time window or all time.

**Data flow**: It receives an optional window length, opens a workspace transaction, and returns SpendRollup’s report.

**Call relations**: Web usage pages and sample code call it for usage display.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 1876–1891)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded file into a conversation’s sandbox workspace before a turn runs.

**Data flow**: It receives conversation id, relative path, and byte chunks. It accumulates chunks up to a maximum size, rejects oversized uploads, and writes the bytes through the sandbox carrier.

**Call relations**: Slack, iMessage, web, and sample surfaces use it to deliver member attachments.

*Call graph*: called by 4 (_downloaded_files, _surface_ingest, _download_files, _deliver_uploads).


##### `SurfaceContext.render_preview`  (lines 1893–1921)

```
async def render_preview(self, kind: str, data: bytes) -> bytes | None
```

**Purpose**: Asks an external preview service to turn uploaded document bytes into a small PNG preview. If previewing is unavailable or fails, composing can continue without a preview.

**Data flow**: It receives a kind and bytes, sends them with render options to the preview service, and returns PNG bytes only on a successful image/png response.

**Call relations**: The web surface calls it for upload previews.

*Call graph*: called by 1 (preview); 2 external calls (AsyncClient, dumps).


##### `SurfaceContext.list_agents`  (lines 1923–1955)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists all agents in the workspace, main agent first. Surfaces can then apply their own audience rules before showing choices.

**Data flow**: It queries agent rows for this workspace, converts them to AgentSummary objects, and returns them.

**Call relations**: Web and sites surfaces call it for agent selectors and related views.

*Call graph*: called by 4 (frame, web_audience, _created_apps, _subagent_nodes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_extension_agent_ids`  (lines 1957–1972)

```
async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]
```

**Purpose**: Finds agents that have private extension conversations for a member.

**Data flow**: It receives a member id, queries distinct agent ids from extension-surface private conversations, and returns them as a frozenset.

**Call relations**: The web audience code uses it when deciding which agents a member can see.

*Call graph*: called by 1 (web_audience); 3 external calls (select, conversation_audience, workspace_tx).


##### `SurfaceContext.agent_detail`  (lines 1974–2030)

```
async def agent_detail(self, agent_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads the full editable/detail view of one agent, including prompt digest, bound surfaces, and pending setup needs.

**Data flow**: It receives an agent id, queries the agent row and its installations, computes the prompt digest, looks up pending setup, and returns AgentDetail or None.

**Call relations**: Web panels call it for agent settings and intent submission.

*Call graph*: called by 2 (agent_settings, submit_intent); 5 external calls (__init__, select, pending_setup, workspace_tx, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 2032–2044)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for a registered object kind, such as listable fields and form schema.

**Data flow**: It receives a kind name, looks up the bound object kind, and returns PortalKind or None.

**Call relations**: The web surface calls it before serving object pages.

*Call graph*: called by 1 (_object_gate); 1 external calls (__init__).


##### `SurfaceContext.agent_skills`  (lines 2046–2064)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists the skills a selected agent would load, combining deploy skills and member-authored skills.

**Data flow**: It receives an agent id, binds that agent scope, merges skill registries, labels each top-level skill as deploy or member origin, and returns PortalSkill records.

**Call relations**: The web surface calls it for the skills page.

*Call graph*: called by 1 (skills); 2 external calls (__init__, agent).


##### `SurfaceContext.memory_available`  (lines 2067–2071)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory-search provider is installed.

**Data flow**: It checks whether the context has a memory provider and returns a boolean.

**Call relations**: Surfaces use it as a gate before offering memory search or browse.


##### `SurfaceContext.search_memory`  (lines 2073–2083)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches memory items visible to a reader. It deliberately errors if no memory provider is installed so callers must gate first.

**Data flow**: It receives a source reader and query strings, checks provider availability, delegates to the memory provider, and returns matches.

**Call relations**: The web surface calls it for workspace memory search.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 2085–2098)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items for readable subjects, optionally filtered by kind and paged by cursor.

**Data flow**: It receives subjects, limit, optional kinds, and cursor, checks provider availability, delegates to list_recent, and returns a listing page.

**Call relations**: The web surface calls it for memory browsing.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 2101–2106)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the filterable memory item classes supported by the installed provider.

**Data flow**: It checks that memory exists, asks the provider for listable kinds, and returns them.

**Call relations**: Portal memory views use it to build filters.


##### `SurfaceContext.agent_spend`  (lines 2108–2113)

```
async def agent_spend(self, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Reads usage and spending for one agent.

**Data flow**: It receives an agent id and optional time window, opens a transaction, and returns the agent spend report.

**Call relations**: Administration or usage views can call it when showing agent-level costs.

*Call graph*: 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.member_spend`  (lines 2115–2120)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads usage and spending for one member.

**Data flow**: It receives a member id and optional time window, opens a transaction, and returns the member spend report.

**Call relations**: The web usage view calls it for member-specific usage.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 2122–2174)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to one agent that the viewer may see.

**Data flow**: It receives agent id, member id, and admin flag. It queries grant and connection rows, applies visibility rules, and returns ConnectionView records.

**Call relations**: The web connections page calls it.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.list_connections`  (lines 2176–2245)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists visible connector accounts across the workspace, grouped with the agents attached to each.

**Data flow**: It receives member id and admin flag, queries connections and optional grants, filters non-admin visibility, groups agent rows per account, and returns ConnectionPoolView records.

**Call relations**: The web connection pool view calls it.

*Call graph*: called by 1 (connection_pool); 6 external calls (__init__, __init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.github_coverage`  (lines 2247–2295)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports which GitHub integration pieces are present and visible: API connection, git-push credential, and sources.

**Data flow**: It receives member id and admin flag, builds visibility conditions, checks existence of GitHub connections and sources, checks credential slots, and returns GithubCoverageView.

**Call relations**: The web surface calls it for GitHub integration status.

*Call graph*: called by 1 (github_coverage); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.list_artifacts`  (lines 2297–2402)

```
async def list_artifacts(self, member_id: UUID, *, admin: bool, limit: int, cursor: 'ListingCursor | None'=None, q: str | None=None, media: str | None=None, scope: str | None=None) -> 'ListingPage[Lis
```

**Purpose**: Returns a paged list of shared files visible to a member or admin, with optional search, media, and scope filters.

**Data flow**: It receives viewer information, pagination, and filters. It builds a query over shared artifacts and conversations, applies visibility, gets sources, wraps rows as ListedArtifact, and returns a listing page.

**Call relations**: The web workspace artifacts page calls it; it uses _media_predicate and _conversation_sources.

*Call graph*: calls 2 internal fn (_conversation_sources, _media_predicate); called by 1 (workspace_artifacts); 6 external calls (or_, select, readable_audiences, workspace_tx, page_of, page_query).


##### `SurfaceContext.list_conversation_artifacts`  (lines 2404–2470)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists recent shared files for one conversation.

**Data flow**: It receives a conversation id and limit, queries newest matching artifacts in this workspace, gets the conversation source, and returns ListedArtifact records.

**Call relations**: The web surface uses it for transcript aids and slot context.

*Call graph*: calls 1 internal fn (_conversation_sources); called by 2 (_project_slot_context, _transcript_aids); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.list_scheduled_runs`  (lines 2472–2556)

```
async def list_scheduled_runs(self, member_id: UUID, *, limit: int, cursor: 'ListingCursor | None'=None, agent_id: UUID | None=None) -> 'ListingPage[ScheduledRun]'
```

**Purpose**: Returns a paged feed of completed scheduled runs that the member may read. Successful runs only appear when they shared a file; failures appear because the failure is itself useful output.

**Data flow**: It receives member id, limit, cursor, and optional agent id. It builds the scheduled-run query, pages it, loads files for those turns, reads sources, and returns ScheduledRun entries.

**Call relations**: The web radar page calls it; it shares query construction with count_scheduled_runs_since.

*Call graph*: calls 2 internal fn (_conversation_sources, _scheduled_runs); called by 1 (workspace_radar); 5 external calls (__init__, select, workspace_tx, page_of, page_query).


##### `SurfaceContext.count_scheduled_runs_since`  (lines 2558–2575)

```
async def count_scheduled_runs_since(self, member_id: UUID, since: datetime, *, agent_id: UUID | None=None) -> int
```

**Purpose**: Counts scheduled-run feed rows at or after a given time under the same visibility rules as the feed.

**Data flow**: It receives member id, since datetime, and optional agent id. It builds the scheduled-run query, changes it to a count, applies the time bound, and returns the integer count.

**Call relations**: The web radar page uses it to size recent windows.

*Call graph*: calls 1 internal fn (_scheduled_runs); called by 1 (workspace_radar); 1 external calls (workspace_tx).


##### `SurfaceContext._scheduled_runs`  (lines 2577–2611)

```
def _scheduled_runs(self, member_id: UUID, agent_id: UUID | None) -> sa.Select[Any]
```

**Purpose**: Builds the shared SQL query for scheduled-run feed rows.

**Data flow**: It receives member id and optional agent id, creates conditions for scheduled terminal turns, readable audiences, failures or reported successes, and returns the select.

**Call relations**: list_scheduled_runs and count_scheduled_runs_since both call it.

*Call graph*: called by 2 (count_scheduled_runs_since, list_scheduled_runs); 3 external calls (or_, select, readable_audiences).


##### `SurfaceContext.list_member_objects`  (lines 2613–2639)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Lists one registered object kind for a signed-in member, using the object kind’s own visibility rules.

**Data flow**: It receives kind, agent id, member id, admin flag, and query. It finds the bound kind, verifies it is member-listable, binds the agent, stamps supported fields into the query, and delegates to the store.

**Call relations**: The web surface calls it for object indexes, homepage data, and radar task names.

*Call graph*: called by 3 (_radar_task_names, homepage, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 2641–2656)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one object detail for a signed-in member, hiding missing and unauthorized rows the same way.

**Data flow**: It receives kind, object name, agent id, member id, and admin flag. It checks the bound kind is member-readable, binds the agent, delegates to the store, and returns the object or None.

**Call relations**: The web object detail route calls it.

*Call graph*: called by 1 (object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 2658–2680)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants or rows tied to a conversation for a member-visible object kind.

**Data flow**: It receives kind, agent id, conversation id, member id, admin flag, and limit. It verifies the bound kind supports conversation-member listing, binds the agent, delegates to the store, and returns rows or None.

**Call relations**: The web surface uses it while projecting conversation slot context.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 2682–2713)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists member-fillable credential slots and whether each has a stored value, never the secret value itself.

**Data flow**: It reads filled slots from the database, maps declared slots to object names, filters to member-fillable slots, and returns CredentialSlotView records.

**Call relations**: The web credentials page and credential-related panel submits call it.

*Call graph*: called by 2 (submit_intent, workspace_credentials); 4 external calls (__init__, select, named_slots, workspace_tx).


##### `SurfaceContext.workspace_domain`  (lines 2715–2721)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Returns the workspace’s own email domain, if one can be derived.

**Data flow**: It opens a workspace transaction, asks the seats helper for the domain, and returns it or None.

**Call relations**: join_member and is_operator_workspace call it.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.list_members`  (lines 2723–2731)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Lists the workspace roster in stable email order.

**Data flow**: It reads a Seats snapshot for the workspace, sorts members by email, and returns the tuple.

**Call relations**: The web team page calls it.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 2733–2778)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists live source bindings visible to a member or admin.

**Data flow**: It receives member id and admin flag, queries nonremoved sources, applies visibility, adds connector binding fields, and returns SourceView records.

**Call relations**: The web workspace sources page calls it; it uses _binding_fields.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.spend_caps`  (lines 2780–2828)

```
async def spend_caps(self) -> tuple[SpendCapView, ...]
```

**Purpose**: Lists workspace spend caps with human-readable subject names.

**Data flow**: It queries spend caps joined to agent or member names, converts rows into SpendCapView records, and returns them.

**Call relations**: The web administration index calls it.

*Call graph*: called by 1 (admin_index); 4 external calls (__init__, and_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 2830–2846)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations for this workspace and the agent each is bound to.

**Data flow**: It queries surface_installation rows ordered by surface and returns InstallationSummary records.

**Call relations**: Web admin and workspace surfaces pages call it.

*Call graph*: called by 2 (admin_index, workspace_surfaces); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 2848–2897)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists recent workspace conversations across all surfaces for debugging-style views.

**Data flow**: It aggregates turn count and last activity, joins conversations and members, orders by activity, limits results, and returns ConversationSummary records.

**Call relations**: The debugger surface calls it.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 2899–3018)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Lists conversations for one agent as the portal sees them, with visibility, disclosure, title, source, and speaker information.

**Data flow**: It receives agent, viewer, filters, and limit. It builds an activity-ordered query, applies surface, id, participation, audience, and search filters, fetches readable content extras for allowed rows, and returns ListedConversation records.

**Call relations**: Many web chat and conversation routes call it; it uses _participated, _others, _matches, _conversation_sources, and _conversation_speakers.

*Call graph*: calls 5 internal fn (_conversation_sources, _conversation_speakers, _matches, _others, _participated); called by 5 (_member_chat, _named, _resolve_chat, chats_index, conversations); 8 external calls (__init__, __init__, select, audience_member, conversation_audience, parse_audience, readable_audiences, workspace_tx).


##### `SurfaceContext._spoken`  (lines 3020–3041)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition meaning a conversation contains a member-spoken turn. It can check a specific member or any member.

**Data flow**: It receives an optional member id and returns a correlated EXISTS condition over turns.

**Call relations**: _participated and _others use it inside conversation listing filters.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `SurfaceContext._participated`  (lines 3043–3051)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for conversations a member participated in.

**Data flow**: It receives a member id and returns true for conversations bound to that member or containing a turn spoken by them.

**Call relations**: list_agent_conversations uses it for the participation='mine' filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list_agent_conversations); 1 external calls (or_).


##### `SurfaceContext._others`  (lines 3053–3065)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for conversations where other members spoke and this member did not participate.

**Data flow**: It receives a member id and returns a SQL condition excluding the member’s bound or spoken conversations while requiring some member speech.

**Call relations**: list_agent_conversations uses it for the participation='others' filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list_agent_conversations); 2 external calls (and_, not_).


##### `SurfaceContext._matches`  (lines 3067–3096)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a search condition for conversation listing. It protects private content by only matching titles and speaker names where the viewer may read the content.

**Data flow**: It receives search text and member id, creates conditions over surface label, owner email, readable title, and readable speaker email, and returns their OR combination.

**Call relations**: list_agent_conversations calls it when a search term is provided.

*Call graph*: called by 1 (list_agent_conversations); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `SurfaceContext._conversation_sources`  (lines 3098–3133)

```
async def _conversation_sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the source link or identifier recorded on each listed conversation’s opening turn.

**Data flow**: It receives conversation ids, finds the first turn in each, decodes its TurnContext, and returns a map from conversation id to source or None.

**Call relations**: Conversation, artifact, and scheduled-run listings use it to provide links back to where work started.

*Call graph*: called by 4 (list_agent_conversations, list_artifacts, list_conversation_artifacts, list_scheduled_runs); 4 external calls (model_validate, and_, select, workspace_tx).


##### `SurfaceContext._conversation_speakers`  (lines 3135–3191)

```
async def _conversation_speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Finds the first few distinct speakers in each listed conversation.

**Data flow**: It receives conversation ids, queries first turns per speaker, decodes sender display names from context, and returns a map of conversation id to ConversationSpeaker tuples.

**Call relations**: list_agent_conversations uses it to enrich readable conversation rows.

*Call graph*: called by 1 (list_agent_conversations); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `SurfaceContext.readable_conversation`  (lines 3193–3234)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Decides whether a member may read a conversation’s content. It allows own and shared conversations, and allows admin reads of another member’s private conversation only after a recent disclosure record.

**Data flow**: It receives conversation, agent, member, and admin flag. It reads the conversation audience, checks normal readable audiences, checks admin disclosure rules and window, and returns a boolean.

**Call relations**: The web surface calls it before transcript and file content routes.

*Call graph*: called by 1 (_readable_conversation); 6 external calls (now, select, audience_member, parse_audience, readable_audiences, workspace_tx).


##### `SurfaceContext.conversation_audience`  (lines 3236–3246)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience attached to a conversation for this workspace and agent.

**Data flow**: It receives conversation id and agent id, queries the audience string, parses it, and returns an Audience or None.

**Call relations**: The web surface uses it while building slot context.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, parse_audience, workspace_tx).


##### `SurfaceContext.conversation_subagent_turns`  (lines 3248–3285)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists all subagent turns spawned under a conversation’s turns, including nested descendants.

**Data flow**: It receives conversation id and limit, builds a recursive query over parent_turn_id, reads matching turn rows, converts them to Turn records, and returns them breadth-first.

**Call relations**: The web surface uses it for events, transcript aids, and slot targeting.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_events, _slot_target, _transcript_aids); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 3287–3303)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists the durable turns in a conversation in admission order.

**Data flow**: It receives conversation id and limit, queries the latest limited set in reverse order, converts rows to Turn records, and returns them oldest first.

**Call relations**: Debugger and web transcript views call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _transcript_aids); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 3305–3340)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Identifies transcript message references that came from scheduled runs or subagent result envelopes rather than member prose.

**Data flow**: It receives a conversation id, unions matching turn ids and inbound-message ids, converts them to strings, and returns a frozenset.

**Call relations**: Web conversation rendering uses it to avoid displaying machine envelopes as member messages.

*Call graph*: called by 2 (_conversation_messages, _history_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 3342–3392)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads a detailed view of one turn, including accounting rows and immediate child subagent turns.

**Data flow**: It receives a turn id, queries the turn, child turns, and ledger rows, converts them to TurnDetail, and returns None if the turn is absent.

**Call relations**: Debugger and web views call it for turn pages, live event context, and message projection.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 3394–3435)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Lists admitted messages in a conversation that are not yet written into the durable transcript.

**Data flow**: It receives conversation id and optional draining turn id, verifies ownership, queries unconsumed or currently-draining inbound messages, and returns QueuedArrival records.

**Call relations**: The web surface uses it to show messages during a live turn reload.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 3437–3469)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Reads attribution for member-admitted inbound-message rows, including drained ones.

**Data flow**: It receives a conversation id, verifies ownership, queries member-admission rows, decodes context, and returns SpokenArrival records.

**Call relations**: Web message and history rendering use it to label folded messages.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (_conversation_messages, _history_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.keyed_admissions`  (lines 3471–3507)

```
async def keyed_admissions(self, conversation_id: UUID) -> tuple[KeyedAdmission, ...]
```

**Purpose**: Lists all messages in a conversation that were admitted with idempotency keys.

**Data flow**: It receives a conversation id, verifies ownership, unions keyed turn and inbound-message rows, and returns KeyedAdmission records.

**Call relations**: The web transcript aids view uses it to recognize its own submitted messages.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_transcript_aids); 4 external calls (__init__, select, union_all, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 3509–3520)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads the durable transcript blob for a conversation if it belongs to this workspace.

**Data flow**: It receives a conversation id, verifies ownership, reads the transcript blob, decodes it, and returns the Conversation or None if absent.

**Call relations**: Debugger, web, and UFO surfaces call it for conversation history.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 5 (conversation_transcript, channel, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 3522–3533)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists saved compaction record indices for a conversation.

**Data flow**: It receives a conversation id, verifies ownership, lists matching blob keys, extracts numeric indices, sorts them, and returns them.

**Call relations**: Debugger and web conversation views call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_compactions, _conversation_messages).


##### `SurfaceContext.read_compaction`  (lines 3535–3541)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one stored compaction record for a conversation.

**Data flow**: It receives conversation id and index, verifies ownership, delegates to the transcript compaction reader, and returns the record or None.

**Call relations**: Debugger and web history views call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (compaction_record, _history_messages); 1 external calls (read_compaction_record).


##### `SurfaceContext.read_compaction_after`  (lines 3543–3551)

```
async def read_compaction_after(self, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the post-compaction message window for one compaction record.

**Data flow**: It receives conversation id and index, verifies ownership, delegates to read_compaction_after, and returns messages or None.

**Call relations**: The web surface uses it to verify earlier history windows.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_verified_earlier); 1 external calls (read_compaction_after).


##### `SurfaceContext.list_workspace_files`  (lines 3553–3559)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files in a conversation’s sandbox workspace.

**Data flow**: It receives a conversation id, verifies ownership, asks the sandbox manager for entries, and returns them or an empty tuple.

**Call relations**: Debugger and web attachment routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_files, conversation_attachment).


##### `SurfaceContext.conversation_changes`  (lines 3561–3567)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last recorded workspace file changes for a conversation.

**Data flow**: It receives a conversation id, verifies ownership, returns recorded changes, or NOTHING_CHANGED for foreign conversations.

**Call relations**: The web surface uses it in projected slot context.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 3569–3578)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one file from a conversation’s sandbox workspace.

**Data flow**: It receives conversation id and relative path, verifies ownership, asks the sandbox manager for a byte stream, and returns the stream or None.

**Call relations**: Debugger and web attachment routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_file, conversation_attachment).


##### `SurfaceContext.terminal_connect`  (lines 3580–3584)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Registers that a member’s live terminal connection is available for a conversation.

**Data flow**: It receives conversation id, current directory, and optional member id, and records the connection in the sandbox terminal rendezvous.

**Call relations**: Live terminal transports call it around the lifetime of a held connection.


##### `SurfaceContext.terminal_disconnect`  (lines 3586–3587)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the terminal connection for a conversation.

**Data flow**: It receives a conversation id and tells the sandbox terminal rendezvous to disconnect it.

**Call relations**: Live terminal transports call it when the held connection ends.


##### `SurfaceContext.claim_terminal`  (lines 3589–3595)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Claims a connected terminal for a fresh conversation if the conversation has no sandbox binding yet.

**Data flow**: It receives conversation id and current directory, asks the sandbox manager to claim the terminal, and returns whether this call made the binding.

**Call relations**: The UFO surface calls it when sending work tied to a terminal.

*Call graph*: called by 2 (_send, channel).


##### `SurfaceContext.next_terminal_op`  (lines 3597–3604)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for or reads the next terminal operation requested by a turn.

**Data flow**: It receives conversation id and optional operation id to exclude, delegates to the terminal manager, and returns the next TerminalOp.

**Call relations**: Terminal streaming routes use it to send operations to the client.


##### `SurfaceContext.terminal_resolve`  (lines 3606–3620)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Completes an in-flight terminal operation with the client’s reply or failure.

**Data flow**: It receives conversation id, operation id, reply bytes, failure text, and optional member id. It delegates to the terminal manager and returns whether the operation was resolved.

**Call relations**: The UFO surface calls it when a terminal client posts back.

*Call graph*: called by 1 (channel).


##### `SurfaceContext.terminal_op_body`  (lines 3622–3634)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads staged bytes for a terminal operation without creating a conversation.

**Data flow**: It receives queue key, operation id, and member id, finds the existing conversation by queue key, then asks the terminal manager for the staged body.

**Call relations**: The UFO surface op_body route calls it.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 3636–3649)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation identity for another surface.

**Data flow**: It receives a peer surface name, queries surface_installation, and returns the installation id or None.

**Call relations**: The debugger surface uses it for workspace metadata and deep links.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 3652–3661)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Yields a raw workspace database transaction for a trusted surface extension’s own tables.

**Data flow**: It opens workspace_tx, yields the connection, commits on normal exit, and rolls back on error through the transaction context.

**Call relations**: Sites and Slack surface code call it for extension-specific reads.

*Call graph*: called by 2 (_viewer_is_admin, _folds_into_live_turn); 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 3663–3673)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace.

**Data flow**: It receives a conversation id, queries the conversation table scoped by workspace, and returns true if found.

**Call relations**: Transcript, compaction, queued-arrival, and workspace-file reads call it before touching unscoped storage.

*Call graph*: called by 10 (arrival_speakers, conversation_changes, keyed_admissions, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_compaction_after, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 3675–3694)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the standard select list for reading turn rows.

**Data flow**: It takes no input and returns a SQL select containing all fields needed to reconstruct a Turn record.

**Call relations**: list_turns, turn_detail, and conversation_subagent_turns reuse it.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 3696–3715)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database row into a typed Turn record.

**Data flow**: It receives a row, validates optional context and terminal JSON, copies scalar fields, and returns a Turn.

**Call relations**: Turn-reading methods call it after querying rows with _turn_query.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.installation`  (lines 3736–3749)

```
async def installation(self, surface: str) -> str | None
```

**Purpose**: Lets a tool read the current workspace’s installation id for a manifest-declared surface.

**Data flow**: It receives a surface name, rejects undeclared names, queries the installation row for the ambient workspace, and returns the id or None.

**Call relations**: Tool code uses this scoped access rather than the broader SurfaceContext.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `SurfaceInstallationAccess.bind`  (lines 3751–3756)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Lets a tool bind a declared surface installation to the ambient workspace.

**Data flow**: It receives a surface name and installation id, rejects undeclared names, and calls the shared installation binder for the current workspace.

**Call relations**: Manifest-scoped tools use it to register installations safely.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 3768–3778)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Resolves which workspace owns a surface installation id before a request is bound to any workspace.

**Data flow**: It receives an installation id, queries owner-scoped installation rows for this surface, and returns the workspace id or None.

**Call relations**: Slack workspace resolution calls it during shared ingress authentication.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 3780–3792)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential handoff before workspace binding, returning None instead of raising for invalid seals.

**Data flow**: It receives a sealed string, verifies it if a credential store exists, and returns CredentialRequestState or None.

**Call relations**: Slack’s resolver uses it to recover workspace information from OAuth state.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 3794–3810)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential slot for a specific workspace during pre-binding authentication.

**Data flow**: It receives workspace id and slot, checks the slot was declared, verifies the workspace exists, reads the credential, and returns it.

**Call relations**: Slack uses it to read signing secrets while resolving incoming requests.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceListenerContext.workspace`  (lines 3846–3854)

```
async def workspace(self, installation_id: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds one listener event to the workspace that owns an installation id, while confirming this process still owns the listener lease.

**Data flow**: It receives an installation id, checks fleet ownership, resolves workspace id, yields None if unknown, or enters the workspace scope and yields a SurfaceContext.

**Call relations**: Persistent listener implementations use it around each provider event.

*Call graph*: called by 4 (_clear_cursor, _process_event, _read_cursor, _store_cursor); 1 external calls (ws).


##### `SurfaceListenerRunner.run`  (lines 3880–3918)

```
async def run(self) -> None
```

**Purpose**: Runs a persistent surface listener only while this process owns the fleet-wide lease. It restarts on ownership changes and parks non-database listener failures.

**Data flow**: It loops forever, waits for ownership, starts the listener and an ownership-loss watcher, reacts to whichever finishes first, logs failures, and cancels leftover tasks.

**Call relations**: Core starts this runner for surfaces that declare a listener.

*Call graph*: calls 2 internal fn (_wait_until_not_owned, _wait_until_owned); 9 external calls (__init__, CancelledError, create_task, ensure_future, gather, sleep, wait, emit_metric, log).


##### `SurfaceListenerRunner._wait_until_not_owned`  (lines 3920–3925)

```
async def _wait_until_not_owned(self) -> None
```

**Purpose**: Waits until this process no longer owns the listener lease.

**Data flow**: It repeatedly checks ownership, returns when ownership is false, and sleeps between checks.

**Call relations**: run starts it beside the listener to know when to stop that listener.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._wait_until_owned`  (lines 3927–3931)

```
async def _wait_until_owned(self) -> None
```

**Purpose**: Waits until this process obtains or renews the listener lease.

**Data flow**: It repeatedly checks ownership and sleeps until the check returns true.

**Call relations**: run calls it before starting a listener.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._owned_on_tick`  (lines 3933–3942)

```
async def _owned_on_tick(self) -> bool | None
```

**Purpose**: Performs one safe ownership check for the listener lease.

**Data flow**: It calls _owns, returns its result, and converts database errors into a logged None result.

**Call relations**: Both ownership wait loops call it.

*Call graph*: calls 1 internal fn (_owns); called by 2 (_wait_until_not_owned, _wait_until_owned); 1 external calls (log).


##### `SurfaceListenerRunner._owns`  (lines 3944–3962)

```
async def _owns(self) -> bool
```

**Purpose**: Claims or renews listener ownership while protecting the database transaction from cancellation damage.

**Data flow**: It starts _claim as a task, shields it until complete, remembers cancellation, re-raises cancellation after the claim finishes, and returns the claim result.

**Call relations**: _owned_on_tick calls it for each lease check.

*Call graph*: calls 1 internal fn (_claim); called by 1 (_owned_on_tick); 2 external calls (ensure_future, shield).


##### `SurfaceListenerRunner._claim`  (lines 3964–4004)

```
async def _claim(self) -> bool
```

**Purpose**: Writes or renews the database lease for one surface listener.

**Data flow**: It computes expiry time, upserts the listener-claim row if expired or already owned by this instance token, and returns whether the stored owner token matches this runner.

**Call relations**: _owns calls it; the lease controls whether run may keep the listener active.

*Call graph*: called by 1 (_owns); 7 external calls (now, timedelta, and_, insert, insert, or_, owner_tx).


##### `SurfaceDeliveryError.__init__`  (lines 4011–4015)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that may include a provider-requested retry delay.

**Data flow**: It receives an error message and optional retry-after seconds, rejects negative delays, stores the delay, and initializes RuntimeError.

**Call relations**: Slack delivery code can raise it so pollers schedule retries according to provider advice.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 4070–4094)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for terminal writebacks that are ready to deliver. It also waits for pending mid-turn replies so the final answer arrives last.

**Data flow**: It receives the current time and returns a SQL condition over turn and writeback status, claim expiry, and pending mid-turn replies.

**Call relations**: writeback_workspaces.due and WritebackPoller._claim use it.

*Call graph*: called by 2 (_claim, due); 3 external calls (and_, exists, or_).


##### `writeback_workspaces`  (lines 4097–4132)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating candidate reader for workspaces with due terminal writebacks.

**Data flow**: It initializes a cursor, wraps the due query with owner_candidates, and returns an async candidates function that pages workspace ids and wraps around.

**Call relations**: WritebackPoller receives this as its workspace source.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 4104–4118)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query for workspace ids that currently have deliverable terminal writebacks.

**Data flow**: It reads the current time, selects grouped workspace ids matching _writeback_due, applies the cursor if set, and limits the page.

**Call relations**: owner_candidates calls it inside writeback_workspaces.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 4122–4130)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with due writebacks.

**Data flow**: It calls the owner-scoped due reader, resets the cursor if it reached the end, updates the cursor to the last returned id, and returns the ids.

**Call relations**: WritebackPoller.run and drain call the candidates function.


##### `_WritebackDeliveryFailed.__init__`  (lines 4140–4143)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a delivery exception with the phase that failed: posting the reply or attaching files.

**Data flow**: It receives a phase and original exception, stores them, and initializes the runtime error text from the original error.

**Call relations**: WritebackPoller._deliver_claimed raises it so _deliver can retry or fail the row with phase-specific logging.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 4166–4199)

```
async def run(self) -> None
```

**Purpose**: Continuously drains terminal writebacks across workspaces in the background.

**Data flow**: It keeps a bounded set of workspace drain tasks, removes completed ones, asks for new candidate workspaces, starts drains under a concurrency semaphore, sleeps between polls, and cancels tasks on shutdown.

**Call relations**: Core runs it for durable surfaces; it calls _drain_workspace for each active workspace.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 4201–4210)

```
async def drain(self) -> None
```

**Purpose**: Runs one bounded drain pass for currently due workspaces. This is useful for tests or one-shot maintenance.

**Data flow**: It reads candidate workspace ids, drains them concurrently with a semaphore, gathers results, and raises an ExceptionGroup if any drains failed.

**Call relations**: It uses the same _drain_workspace path as the continuous run loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 4212–4230)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers due terminal writebacks for one workspace.

**Data flow**: It enters the workspace scope, claims rows, starts claim-renewal tasks for each, delivers each row, then cancels and awaits renewals.

**Call relations**: run and drain call it; it coordinates _claim, _renew_claim, and _deliver.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 4232–4265)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due writeback rows for this worker.

**Data flow**: It builds a claimable subquery, updates matching writeback rows to claimed with worker id and expiry, and returns turn id, reply ref, and last error for each claimed row.

**Call relations**: _drain_workspace calls it before attempting delivery.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 4267–4299)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed terminal writeback and records the outcome.

**Data flow**: It receives workspace id, turn id, optional reply ref, and renewal task. It times the work, calls _deliver_with_lease, handles lost claims or delivery failures, updates retry/failure state, and logs success or failure.

**Call relations**: _drain_workspace calls it for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 4301–4330)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while the claim-renewal task is alive, then marks the row delivered only after renewal is stopped.

**Data flow**: It starts _deliver_claimed, waits for delivery or renewal failure, cancels leftovers, then calls _mark_delivered.

**Call relations**: _deliver calls it to separate provider calls from the final database commit.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 4332–4354)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Builds a Writeback and calls the owning surface’s post and attach functions.

**Data flow**: It receives workspace id, turn id, and optional reply ref. It builds delivery data, finds the surface spec, skips missing delivery functions, posts if no reply ref exists, records the ref, then attaches files.

**Call relations**: _deliver_with_lease calls it; it uses _build and _record_ref.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 4356–4359)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps a writeback claim alive while external delivery is in progress.

**Data flow**: It repeatedly sleeps for the refresh interval and calls _refresh_claim for the turn.

**Call relations**: _drain_workspace starts one renewal task per claimed row.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 4361–4377)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim expiry for a writeback row.

**Data flow**: It receives a turn id, updates the claimed row if still owned by this worker, and raises _WritebackClaimLost if no row was updated.

**Call relations**: _renew_claim calls it on each refresh tick.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 4379–4431)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the Writeback object needed by a surface delivery function.

**Data flow**: It receives a turn id, reads the terminal frame, conversation, agent, queue key, surface name, and shared artifacts, validates terminal JSON, and returns the Writeback plus surface name.

**Call relations**: _deliver_claimed calls it before invoking a surface.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 4433–4445)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Stores the durable provider reply reference after a successful post. This prevents a retry from reposting if the process crashes later.

**Data flow**: It receives turn id and reply ref, updates the claimed writeback row owned by this worker, and raises _WritebackClaimLost if ownership was lost.

**Call relations**: _deliver_claimed calls it between post and attach.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 4447–4464)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a claimed writeback as fully delivered.

**Data flow**: It receives a turn id, updates the row to delivered, clears claim fields, and raises _WritebackClaimLost if this worker no longer owns it.

**Call relations**: _deliver_with_lease calls it after external delivery completes.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 4466–4515)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Schedules a failed terminal writeback for retry or marks it permanently failed after the delivery window expires.

**Data flow**: It receives turn id and wrapped delivery error, chooses retry delay from SurfaceDeliveryError or fixed backoff, truncates error text, updates row status and next claim time, and returns outcome details.

**Call relations**: _deliver calls it after _deliver_with_lease reports a delivery failure.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `mid_turn_reply_workspaces`  (lines 4518–4549)

```
def mid_turn_reply_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating candidate reader for workspaces with due mid-turn replies.

**Data flow**: It initializes a cursor, wraps its due query with owner_candidates, and returns an async candidates function that pages workspace ids.

**Call relations**: MidTurnReplyPoller receives this as its workspace source.

*Call graph*: 1 external calls (owner_candidates).


##### `mid_turn_reply_workspaces.due`  (lines 4524–4535)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query for workspace ids that have deliverable mid-turn replies.

**Data flow**: It reads the current time, selects grouped workspace ids matching _mid_turn_reply_due, applies the cursor if set, and limits the page.

**Call relations**: owner_candidates calls it inside mid_turn_reply_workspaces.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); 2 external calls (now, select).


##### `mid_turn_reply_workspaces.candidates`  (lines 4539–4547)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with due mid-turn replies.

**Data flow**: It calls the owner-scoped due reader, resets the cursor at the end, updates the cursor to the last id, and returns workspace ids.

**Call relations**: MidTurnReplyPoller.drain calls it.


##### `_mid_turn_reply_due`  (lines 4552–4565)

```
def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for mid-turn reply rows that are claimable now.

**Data flow**: It receives the current time and returns a SQL condition matching pending or expired claimed rows.

**Call relations**: mid_turn_reply_workspaces.due and MidTurnReplyPoller._claim use it.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `MidTurnReplyPoller.run`  (lines 4591–4597)

```
async def run(self) -> None
```

**Purpose**: Continuously delivers mid-turn replies in the background.

**Data flow**: It loops forever, calls drain, logs drain failures, then sleeps before the next poll.

**Call relations**: Core runs it for durable surfaces that may speak before a turn ends.

*Call graph*: calls 1 internal fn (drain); 2 external calls (sleep, log).


##### `MidTurnReplyPoller.drain`  (lines 4599–4609)

```
async def drain(self) -> None
```

**Purpose**: Claims and delivers due mid-turn replies for candidate workspaces.

**Data flow**: It reads candidate workspace ids, enters each workspace scope, claims rows, logs retries, and delivers each row.

**Call relations**: run calls it every poll interval; it coordinates _claim and _deliver.

*Call graph*: calls 2 internal fn (_claim, _deliver); called by 1 (run); 2 external calls (log, ws).


##### `MidTurnReplyPoller._claim`  (lines 4611–4652)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due mid-turn reply rows for this worker in stable delivery order.

**Data flow**: It builds a claimable query ordered by creation and span order, updates rows to claimed with expiry, returns delivery fields, and sorts the claimed rows.

**Call relations**: drain calls it before delivering replies.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); called by 1 (drain); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `MidTurnReplyPoller._deliver`  (lines 4654–4677)

```
async def _deliver(self, workspace_id: UUID, row: sa.Row) -> None
```

**Purpose**: Delivers one claimed mid-turn reply and records success or retry/failure.

**Data flow**: It receives workspace id and claimed row, calls _speak, sends failures to _fail_or_retry, marks success with _mark_delivered, and logs the result.

**Call relations**: drain calls it for each claimed row.

*Call graph*: calls 3 internal fn (_fail_or_retry, _mark_delivered, _speak); called by 1 (drain); 2 external calls (now, log).


##### `MidTurnReplyPoller._speak`  (lines 4679–4720)

```
async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None
```

**Purpose**: Calls the owning surface’s mid-turn speak function, or completes silently if there is no function to call.

**Data flow**: It receives workspace id and reply row. If a reply ref already exists it returns it; otherwise it reads turn and conversation surface data, finds the spec, builds MidTurnReply, and calls spec.speak if present.

**Call relations**: _deliver calls it before marking the row delivered.

*Call graph*: called by 1 (_deliver); 4 external calls (__init__, select, workspace_tx, log).


##### `MidTurnReplyPoller._mark_delivered`  (lines 4722–4740)

```
async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Marks a mid-turn reply row delivered and stores its provider reference if any.

**Data flow**: It receives reply id and optional reply ref, updates the row only if still claimed by this worker, clears claim fields, and logs if the claim was lost.

**Call relations**: _deliver calls it after _speak succeeds or intentionally does nothing.

*Call graph*: called by 1 (_deliver); 3 external calls (update, workspace_tx, log).


##### `MidTurnReplyPoller._fail_or_retry`  (lines 4742–4789)

```
async def _fail_or_retry(self, reply_id: UUID, error: Exception) -> tuple[str, str, datetime | None]
```

**Purpose**: Schedules a failed mid-turn reply for retry or marks it failed after it ages out. A failed span no longer blocks the terminal writeback.

**Data flow**: It receives reply id and error, chooses a retry delay, truncates error text, updates status and claim expiry based on age, and returns outcome details.

**Call relations**: _deliver calls it when _speak raises an error.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).

## 📊 State Registers Touched

- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-surface-installations` — The saved bindings for web, Slack, iMessage, CLI, hosted sites, and other surfaces that connect outside channels to workspaces and agents.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-inbound-message-queue` — The saved holding area for incoming chat messages before they are admitted into a running or queued turn.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-runtime-fleet` — The records of running server or worker instances, their heartbeats, listener claims, and cleanup ownership.
- `reg-transcript-history` — The saved conversation transcript and compaction snapshots that all surfaces, workers, prompts, and recovery logic read and update.
- `reg-live-stream-state` — The live update stream that broadcasts turn progress, tool activity, subagent activity, cancellations, and final replies to connected clients.
- `reg-midturn-replies` — The durable outbox for replies sent before a turn is fully complete, so they can be delivered once even after retries.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-pending-human-interactions` — The durable pending questions, credential-collection prompts, setup requests, and checklist-style waits that tools create and surfaces later resolve.
- `reg-surface-delivery-state` — The outbound reply/writeback bookkeeping for external chat surfaces, including delivery targets, external message identifiers, and exactly-once final reply status.
- `reg-active-cancellation-handles` — Process-local abort tokens and cancellation handles that bridge durable cancel requests to currently running turns, tools, sandboxes, and child turns.
- `reg-turn-surface-context` — Durable per-turn inbound context such as speaker, on-behalf-of member, timezone, original surface metadata, and connection-authorization status carried from admission into prompting, execution, and delivery.
- `reg-admission-ordering-locks` — Conversation-level admission and serialization locks/cursors that prevent concurrent messages, starts, stops, or queued turns from racing before durable turn execution begins.
