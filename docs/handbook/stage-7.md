# Conversation Admission, Membership, and Turn Creation  `stage-7`

This stage is the system’s front door for conversation work. It runs before the main engine starts thinking or using tools. Its job is to turn outside “surface” events, such as a chat message, a scheduled wake-up, or an internal extension result, into a safe, durable turn record that the engine can later pick up and process.

The main gate is admission.py. It gathers the needed context: who sent the message, which room or workspace it belongs to, what visibility rules apply, and whether the audience should be limited for private or external spaces. It also accepts tool-submitted intents, so internally generated requests enter through the same controlled path as human messages.

ambient_reply.py is a smaller decision helper for group threads. If someone talks in a shared thread without directly mentioning the agent, it decides whether the agent should respond or stay quiet, avoiding unwanted interruptions and wasted work.

__init__.py simply marks the turns area as a package so the code can be organized there.

## Files in this stage

### Conversation admission
Entry-point admission logic validates incoming messages, wake-ups, and internal extension results before queueing work.

### `core/src/ufo/surfaces/admission.py`

`domain_logic` · `request handling`

A “turn” is one unit of conversation work: a message arrives, an agent thinks, and a reply may be produced. This file decides whether a new turn should be created, whether the message should be folded into an already-running turn, or whether the work must be cancelled or parked for later. It is like a reception desk for the system: nobody gets into the work queue without being checked in here.

The main checks are practical safeguards. It verifies the conversation is still tied to the same agent, checks whether the agent is archived, confirms the speaker is a real workspace member, applies seat rules, and asks billing whether more work is allowed. It also supports idempotency keys, which are repeat-delivery labels that stop the same message from creating duplicate turns.

If a conversation already has a live turn, most new messages are saved as inbound arrivals for that turn instead of starting another one. The running agent can later read those arrivals. For durable surfaces, this file also creates a writeback record so a reply will be delivered later. Finally, when a turn is ready to run, it is placed on the DBOS workflow queue, which is the background job system used here.

#### Function details

##### `_refused`  (lines 100–111)

```
def _refused(holds_work_already_done: bool, message: str) -> tuple[TurnStatus, TerminalFrame | None]
```

**Purpose**: Decides what should happen when a turn is not allowed to run. If the turn already represents completed paid work, it parks the turn instead of throwing away the result; otherwise it creates a cancelled ending with a message the user can see.

**Data flow**: It receives a yes-or-no flag saying whether work has already been done, plus a refusal message. If work exists, it returns a parked status and no final message. If not, it returns a cancelled status and a terminal frame containing the refusal text.

**Call relations**: Admission._admit calls this whenever seat, billing, balance, or archived-agent checks refuse a turn. The result is then written into the turn row so later parts of the system know whether the turn is waiting, cancelled, or still runnable.

*Call graph*: called by 1 (_admit); 1 external calls (__init__).


##### `Admission.admit_member`  (lines 122–164)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: Accepts a message that came from a workspace member through a surface, such as a chat or other user-facing channel. It adds member-specific guardrails, then asks the shared admission path to place or fold the message.

**Data flow**: It receives the workspace, conversation, message body, speaker member, optional idempotency key, context, prepared tool intent, and optional surface comment. It validates that prepared intents and comments are well-formed, calls the shared admission routine, and then may publish hub notifications when a message was folded into an existing turn or when a comment should appear immediately.

**Call relations**: Surfaces use this as their safe way to admit member speech. Admission.redispatch also uses it when an old pending member message needs to be tried again. Internally it hands the real admission decision to Admission._admit, then publishes ArrivalQueued or Reply events to the live hub when needed.

*Call graph*: calls 1 internal fn (_admit); called by 1 (redispatch); 4 external calls (__init__, __init__, model_dump_json, span).


##### `Admission.redispatch`  (lines 166–215)

```
async def redispatch(self, workspace_id: UUID, conversation_id: UUID) -> tuple[UUID, UUID] | None
```

**Purpose**: Gives an older pending member message another chance to start work. This is useful when a message was left waiting because its previous target turn ended or was cancelled before consuming it.

**Data flow**: It opens a workspace database transaction, finds the oldest unconsumed inbound member message for the conversation, and makes sure it has an idempotency key. Then it calls admit_member with the saved message contents. It returns the new turn and arrival identifiers only if this retry actually opened a run; otherwise it returns nothing.

**Call relations**: This function is a recovery path around the normal member admission flow. It reads pending inbound messages directly, stamps a stable redispatch key if needed, then re-enters Admission.admit_member so the same deduplication and folding rules apply as if the member had resent the message.

*Call graph*: calls 1 internal fn (admit_member); 4 external calls (model_validate, select, update, workspace_tx).


##### `Admission.invoke`  (lines 217–292)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=Non
```

**Purpose**: Starts an internal turn, such as work triggered by an extension, scheduled job, or subagent result. It lets internal callers ask for work without pretending that a member directly spoke the message.

**Data flow**: It receives the workspace, conversation, asserted agent, message, optional idempotency key and context, optional member authority, and several flags that explain why the internal work is being admitted. It calls the shared admission routine. If a member has spoken since the caller began waiting, it returns None instead of starting work; otherwise it returns the admitted turn id.

**Call relations**: Internal jobs and extension workflows call this rather than admit_member. It delegates the detailed locking, billing, seat, idempotency, and queue decisions to Admission._admit. If _admit raises the private “superseded by member” signal, invoke turns that into None so the waiting job can stop cleanly.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 294–786)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, o
```

**Purpose**: This is the central admission decision-maker. It decides whether an incoming message creates a new turn, joins a live turn, wakes a parked turn, is cancelled, or is parked until spending or seat conditions change.

**Data flow**: It takes all details about the attempted admission: workspace, conversation, agent assertion, body, speaker, idempotency key, context, member authority, source flags, waiting watermarks, and optional comment. Under a database lock on the conversation, it reads the conversation, agent, member, existing turns, inbound messages, seats, and billing state. It writes turn rows, inbound-message rows, writeback rows, comments, and dispatch timestamps as needed. After the transaction commits, it enqueues runnable work when appropriate and returns an Admitted object describing what happened.

**Call relations**: Admission.admit_member and Admission.invoke both funnel into this function so no caller can bypass the same rules. It uses _refused to turn refusals into parked or cancelled outcomes, _record_comment to save surface comments, and _enqueue to put queued turns onto the DBOS worker queue. It is the place where database state, billing decisions, seat checks, idempotency, and live-turn folding all meet.

*Call graph*: calls 3 internal fn (_enqueue, _record_comment, _refused); called by 2 (admit_member, invoke); 21 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, model_dump, model_validate, delete (+11 more)).


##### `Admission._record_comment`  (lines 788–831)

```
async def _record_comment(self, connection: AsyncConnection, workspace_id: UUID, admitted: Admitted, comment: str | None, message_ref: UUID | None=None) -> Admitted
```

**Purpose**: Stores a surface comment as a mid-turn reply when the admitting surface supplied one. This lets a comment be attached to the turn or arrival it belongs to without creating duplicate comment rows on retries.

**Data flow**: It receives an open database connection, workspace id, current admission result, optional comment text, and optional message reference. If there is no comment, it returns the admission unchanged. If there is a comment, it builds a stable comment id, inserts the comment if it does not already exist, and returns an updated Admitted object containing the comment id when a new row was written.

**Call relations**: Admission._admit calls this just before returning an admission result, including deduplicated and folded cases. The stable id and conflict-ignore insert mean redelivered messages do not create repeated comments.

*Call graph*: called by 1 (_admit); 3 external calls (__init__, execute, mid_turn_reply_id_for).


##### `Admission._enqueue`  (lines 833–873)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Places a queued turn onto the DBOS background workflow queue so a worker can run it. It also repairs the database marker if enqueueing is cancelled or fails.

**Data flow**: It receives workspace id, conversation id, turn id, and optionally a workflow id. It builds DBOS queue options, using the conversation as the queue partition so turns for the same conversation stay ordered. If enqueue succeeds, nothing else is returned. If enqueue is cancelled or errors, it clears the turn’s dispatch timestamp so another attempt can happen later, and logs ordinary failures.

**Call relations**: Admission._admit calls this after committing database changes for turns that should run now, or when a folded message wakes a parked turn. It hands the actual execution off to DBOS while keeping the database consistent if that handoff does not complete.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 884–909)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, on_behalf_of_member_id: UUID | None=None, holds_work_alr
```

**Purpose**: Provides a workspace-bound way for internal code to invoke a turn. Because the workspace is already fixed, callers cannot accidentally or maliciously admit work into another workspace.

**Data flow**: It receives conversation, agent, message, optional idempotency and context, member authority, scheduling flags, and member-wait watermarks. It adds the stored workspace id and forwards the request to the shared Admission.invoke path. The result is either a turn id or None when a member message superseded the wait.

**Call relations**: Jobs and extension workflows are meant to receive this smaller capability instead of the full Admission object. It narrows what they can do: they can start internal work in one workspace, but they cannot claim a member directly spoke.


##### `MemberAdmission.admit`  (lines 920–940)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Provides a workspace-bound way for surfaces to admit member messages. It makes the surface supply a speaker member, so member-facing messages are checked against member identity and seat rules.

**Data flow**: It receives conversation, message, optional idempotency key and context, required speaker member, optional prepared intent, and optional comment. It adds the stored workspace id and forwards everything to Admission.admit_member. The output is an Admitted object saying whether a run opened, which turn was involved, and possibly which arrival or comment was created.

**Call relations**: User-facing surfaces use this limited wrapper instead of the full Admission object. It routes them into the member-admission path, which then reaches Admission._admit through Admission.admit_member.


##### `ConnectResume.resume`  (lines 968–1003)

```
async def resume(self, conversation_id: UUID, message: str, *, speaker_member_id: UUID, idempotency_key: str) -> bool
```

**Purpose**: Writes the result of an account-connection callback back into the conversation that asked for it. It treats the result as the member’s own message so a waiting turn can absorb it, or a new turn can be started if the old one is already gone.

**Data flow**: It receives a conversation id, message, speaker member, and idempotency key. It first checks the latest turn’s admission source; if the conversation is a prepared-intent lane, it declines because free-text callback messages do not belong there. Otherwise it reads the current workspace, tries to admit the message through Admission.admit_member, logs and returns False on failure, and returns True when admission succeeds.

**Call relations**: This is used by connect callbacks after an external authorization flow finishes. It calls into the same member admission path as normal surface messages, but it deliberately refuses prepared-intent lanes so it does not start unread work in a hidden or single-purpose conversation.

*Call graph*: 4 external calls (select, workspace_tx, log, ws_current).


### Ambient turn gating
Turn-package scaffolding and ambient-reply logic decide when indirect group-thread messages should create agent turns.

### `core/src/ufo/turns/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` is used to say “the files in this folder belong together as an importable package.” Here, that package is `ufo.turns`, which likely contains code related to turns elsewhere in the project.

Because the file is empty, it does not change program behavior directly. It does not create objects, load settings, import other modules, or expose shortcut names. Its value is mostly structural: it gives the folder a clear place in the project’s module tree, like a labeled divider in a binder. Other code can import modules from `ufo.turns`, but this file itself adds no extra work during that import.

If this file were removed, modern Python might still allow the folder to be imported as a “namespace package” in some situations, but keeping it makes the package boundary explicit and compatible with tooling and older expectations.


### `core/src/ufo/turns/ambient_reply.py`

`domain_logic` · `request handling, before starting a new agent turn`

In a shared chat thread, people often talk near the agent without talking to the agent. Once the agent has joined a thread, later replies might be meant for it, for another person, or for nobody in particular. If every message automatically became a full agent turn, the system could waste expensive model calls just to say something like “standing by.” This file puts a small, cheap checkpoint in front of that larger work.

The main idea is simple: package the recent thread history and the new message, ask a model for exactly one word, and use that word as the gate. The answer is either `REPLY` or `NO_REPLY`. The prompt gives ordered rules: reply when the message is really aimed at the agent, stay quiet when it is clearly a conversation between people, and prefer replying when the situation is truly balanced.

The file is careful about safety and cost. It only sends a limited number of recent messages, and each message is shortened to a fixed size. It wraps the data as JSON between fence lines so people’s chat text is treated as quoted data, not as instructions to the classifier. If the model gives an unreadable answer, this code raises an error instead of silently deciding not to reply; the wider system can then choose the safer, more visible option of admitting the turn.

#### Function details

##### `MeteredModel.model`  (lines 94–94)

```
def model(self) -> str
```

**Purpose**: This is the promised way to ask a model wrapper which model name it will use. The classifier needs that name when it builds the model request.

**Data flow**: The classifier has a `MeteredModel` object → it reads this property to get the configured model identifier → that identifier is copied into the request sent to the model service.

**Call relations**: The protocol says any object used by `AmbientReplyClassifier` must provide this property. During `AmbientReplyClassifier.decide`, the classifier reads it while building the `ModelRequest` for the one-word decision.


##### `MeteredModel.complete`  (lines 96–96)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This is the promised method for making one model call and getting back the model’s text answer. Here, that answer should be just `REPLY` or `NO_REPLY`.

**Data flow**: A completed `ModelRequest` goes in → the model wrapper sends it to the configured language model and waits for the response → a text string comes back to the classifier.

**Call relations**: The protocol lets this file depend only on the small shape it needs, not on a larger model-access implementation. `AmbientReplyClassifier.decide` calls this method after it has prepared the prompt and payload.


##### `_entry`  (lines 99–104)

```
def _entry(message: AmbientMessage) -> dict[str, object]
```

**Purpose**: This helper turns one chat message into the small dictionary shape that is sent to the classifier model. It keeps only the speaker, whether the agent wrote it, and a shortened version of the text.

**Data flow**: An `AmbientMessage` goes in → its speaker, ownership flag, and text are copied into a plain data object, with the text cut down to the allowed length → that dictionary comes out ready to be placed in JSON.

**Call relations**: `AmbientReplyClassifier._payload` calls this for every recent history message and for the new message. It is the small formatting step that makes all messages look the same before they are serialized.

*Call graph*: called by 1 (_payload).


##### `AmbientReplyClassifier.decide`  (lines 117–133)

```
async def decide(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> AmbientDecision
```

**Purpose**: This is the main decision point. It asks the cheap classifier model whether the new ambient message deserves a full agent reply, and returns `REPLY` or `NO_REPLY`.

**Data flow**: The new message and recent thread history go in → the method builds a fenced JSON payload, places it inside a model request with the fixed system instructions, and sends it through the metered model → it scans the model’s text for a valid decision word and returns the last one it finds. If no valid decision appears, it raises an error instead of guessing.

**Call relations**: This is the function the surrounding chat surface would call before founding a new turn. It delegates payload construction to `AmbientReplyClassifier._payload`, wraps that payload in a `Message` and `ModelRequest`, then hands the request to `MeteredModel.complete` for the actual model answer.

*Call graph*: calls 1 internal fn (_payload); 2 external calls (__init__, __init__).


##### `AmbientReplyClassifier._payload`  (lines 135–151)

```
def _payload(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> str
```

**Purpose**: This builds the exact user message sent to the classifier model: recent thread history and the new message as one compact JSON object. It also wraps that JSON in fence lines so chat content is clearly separated from the instructions.

**Data flow**: The new message and history go in → the history is trimmed to the most recent allowed messages, each message is converted with `_entry`, and the result is serialized as compact JSON → the method chooses a fence string that does not appear inside the JSON and returns the fenced payload text.

**Call relations**: `AmbientReplyClassifier.decide` calls this just before making the model request. Inside, it uses `_entry` to normalize each message and `json.dumps` to turn the structured data into a string the model can read safely.

*Call graph*: calls 1 internal fn (_entry); called by 1 (decide); 1 external calls (dumps).

## 📊 State Registers Touched

- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-member-auth-principals` — The shared answer to who the current person or service is and what member identity they are acting as.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-surface-routing` — The mapping from outside places like web, Slack, terminal, and iMessage to the right workspace, conversation, member, and agent.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-inbound-admission-queue` — The saved queue of incoming messages or intents waiting to become safe conversation turns.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-transcript-store` — The saved conversation history and compacted summaries that later turns, portals, and auditors read back.
- `reg-schedule-monitor-store` — The saved recurring prompts, pauses, and outside-world watches that can wake conversations later.
- `reg-subagent-objectives` — The shared plan and delegation state for child agents, objectives, steps, evidence, attempts, and result delivery.
- `reg-source-trigger-subscriptions` — Rules that map source changes or sync events to the conversations, agents, or turns that should be woken or admitted.
- `reg-content-provenance-trust-labels` — Visibility, provenance, and trust labels attached to messages, source content, and external text so prompt construction and policy checks can separate trusted instructions from untrusted content.
