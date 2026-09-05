# Schema, persistence contracts, and durable storage  `stage-17` (cross-cutting infrastructure)

This stage is the system’s filing cabinet and rulebook for saved data. It is shared behind-the-scenes support used by the app screens, background workers, workflows, extensions, and storage engines whenever they need to save or read long-lived information.

The records file defines the common shapes of important items: agents, conversation turns, settings, final answers, questions, credential requests, and queue choices. In plain terms, it says what fields each item must have so every part of the system describes the same thing in the same way.

The transcript file focuses on saved conversations. It gives the system one agreed format for naming, compressing, decoding, and reading conversation history and compacted summaries.

The tables file maps these records into real database tables. It defines columns, links between tables, default values, and safety rules for both SQLite, used locally, and Postgres, used in deployed setups. Together, these files make saved state reliable and understandable across the whole project.

## Files in this stage

### Shared persistence contracts
Defines the durable record formats and database schema shared by conversations, workers, runtime state, and storage backends.

### `core/src/ufo/runtime/turns/transcript.py`

`io_transport` · `cross-cutting`

A conversation in this system is not just temporary chat text. It must survive between turns, be inspected later by debugging tools, and be read by evaluation code. This file is the shared contract for that saved data, like a labeled filing system where everyone agrees on the drawer names and document shape.

It defines typed records for a saved conversation, parked turns, and compaction summaries. A “compaction” is when an old, long message history is replaced by a shorter summary plus a kept recent tail, so the model can continue without carrying too much text. The file also records checks about whether important facts survived that shortening.

The actual stored bytes are JSON, compressed with LZ4, a fast compression format. The helper functions turn typed Python objects into compressed bytes, turn compressed bytes back into validated objects, and build the exact blob-store keys where records live. A blob store is a simple storage service for named chunks of bytes.

The important behavior is that decode errors are wrapped as TranscriptDecodeError. That gives callers a clear signal: the saved blob is corrupt, unreadable, or no longer matches the expected shape. Without this file, writers, readers, debuggers, and evaluators could quietly drift into incompatible formats.

#### Function details

##### `transcript_key`  (lines 61–62)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage name for the main saved transcript of one conversation. Callers use it so every part of the system looks in the same place for that conversation’s message history.

**Data flow**: It receives a conversation ID. It inserts that ID into a fixed path shaped like a folder location, ending in messages.json.lz4 to show that the content is JSON compressed with LZ4. It returns that path as a string and does not change anything else.

**Call relations**: This is the shared naming rule for transcript blobs. Writers and readers can use this key to meet at the same stored file without needing to know each other directly.


##### `encode`  (lines 65–67)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a Conversation object into compact bytes ready to save. This is used when the system wants to persist a transcript in the agreed storage format.

**Data flow**: It receives a validated Conversation object. It first converts it into plain JSON-friendly data, then serializes that data into a small JSON string, encodes it as bytes, and compresses those bytes with LZ4. The result is a compressed byte string suitable for the blob store.

**Call relations**: This function sits on the write side of the transcript flow. It relies on the Conversation model’s own dump behavior and standard JSON encoding, then produces the exact byte format that decode expects to read later.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 70–74)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns stored transcript bytes back into a validated Conversation object. Callers use it when they need to read a saved conversation safely.

**Data flow**: It receives compressed bytes from storage. It decompresses them, asks the Conversation model to parse and validate the JSON, and returns the resulting Conversation. If the bytes cannot be decompressed or the JSON does not match the expected shape, it raises TranscriptDecodeError instead of leaking lower-level errors.

**Call relations**: This is the read-side partner to encode. It protects higher-level readers from storage-format details and gives them one clear failure type when a transcript cannot be trusted.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 160–161)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage name for one piece of a compaction record. A compaction has separate stored parts: the window before compaction, the window after compaction, and the summary.

**Data flow**: It receives a conversation ID, a compaction index, and which part is wanted: before, after, or summary. It combines them into a fixed blob-store path and returns that path as a string.

**Call relations**: Both read_compaction_after and read_compaction_record call this function before fetching bytes. It keeps those readers from hard-coding their own path rules, so all compaction lookups stay consistent.

*Call graph*: called by 2 (read_compaction_after, read_compaction_record).


##### `decode_compaction`  (lines 164–173)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds a full CompactionRecord from the three stored byte blobs that make it up. It is used when a reader wants the before window, after window, and structured summary together.

**Data flow**: It receives the compaction index plus compressed bytes for the before window, after window, and summary. It decompresses each piece, validates the message windows and summary against their expected shapes, and returns one CompactionRecord containing all of them. If any piece is unreadable or malformed, it raises TranscriptDecodeError.

**Call relations**: read_compaction_record fetches the three stored blobs and then hands them to this function. decode_compaction is the assembly step that turns separate storage files into one meaningful record.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_after`  (lines 176–189)

```
async def read_compaction_after(blob: BlobStore, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the small “after” window for a particular compaction. This is useful when a caller only needs to check what message window replaced the old history, without loading the larger full record.

**Data flow**: It receives a blob store, a conversation ID, and a compaction index. It builds the key for the after part, fetches those bytes, decompresses and validates them, and returns the messages. If the blob is not found, it returns None. If the blob exists but is unreadable or invalid, it raises TranscriptDecodeError.

**Call relations**: This function calls compaction_key to find the correct blob and BlobStore.get to fetch it. It is a lightweight shortcut for readers that do not need read_compaction_record’s full before/after/summary package.

*Call graph*: calls 2 internal fn (get, compaction_key); 1 external calls (__init__).


##### `read_compaction_record`  (lines 192–203)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one complete compaction record for a conversation. It gives callers the full picture of what was shortened, what replaced it, and what summary was produced.

**Data flow**: It receives a blob store, a conversation ID, and a compaction index. It builds and fetches the before, after, and summary blobs. If any of those blobs is missing, it returns None, treating that index as absent. If all are present, it passes the bytes to decode_compaction and returns the decoded CompactionRecord.

**Call relations**: This is the per-index reader used by read_compaction_records. It delegates path creation to compaction_key, byte fetching to BlobStore.get, and validation/assembly to decode_compaction.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 206–216)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for a conversation in order. It is used by tools that need the full compaction history, such as debugging or evaluation readers.

**Data flow**: It receives a blob store and a conversation ID. Starting at index 1, it repeatedly asks read_compaction_record for the next record. Each found record is added to a list. The first missing index stops the loop, and the function returns all collected records as an immutable tuple.

**Call relations**: This function is the simple walker over sequential compaction records. It relies on read_compaction_record for each individual fetch and stops naturally when that helper reports that the next numbered record does not exist.

*Call graph*: calls 1 internal fn (read_compaction_record).


### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

This file is like the set of official forms used by a busy dispatch center. A “turn” is one unit of work for an agent: someone says something, the system queues it, a worker runs it, and the result is recorded. The file defines the allowed states for that work, such as queued, running, done, failed, or cancelled, and the structured records carried along the way.

Most records are Pydantic models, meaning they are data shapes that check their own contents when created. That matters because these records cross boundaries: web surfaces, workers, databases, billing, sandbox runtimes, and account-connection flows all read them. If the shape is wrong, the mistake is caught early instead of halfway through a run.

The file also gives stable identity rules. Turn IDs, billing ledger IDs, and mid-turn reply IDs are generated from predictable inputs, so retrying the same work produces the same identifier instead of duplicate rows. It chooses whether a turn belongs on the normal queue or the express queue, selects default agent icons, validates time zones and runtime digests, and ensures terminal results match terminal statuses.

Without this file, different parts of the system could disagree about the meaning of a turn, duplicate billing or replies during retries, accept unsafe display text, or run with unclear runtime settings.

#### Function details

##### `auto_agent_icon`  (lines 178–197)

```
def auto_agent_icon(name: str, taken: Collection[str]) -> TablerIcon
```

**Purpose**: Chooses an icon for a newly created agent. It tries to make icons meaningful from the agent name when possible, and otherwise spreads choices across unused icons so agents in the same workspace are easier to tell apart.

**Data flow**: It receives an agent name and the set of icon names already taken. It looks for known keywords in the name, such as words related to billing, code, or support. If a matching icon is free, it returns that. Otherwise it hashes the name, uses that number to choose from unused icons, and falls back to reusing an icon only after all choices are taken.

**Call relations**: This helper is used when a new agent needs a visual mark. It relies on SHA-256 hashing from the standard library to make the fallback choice stable: the same name tends to land on the same icon instead of changing randomly.

*Call graph*: 1 external calls (sha256).


##### `admits_spent_balance`  (lines 222–236)

```
def admits_spent_balance(intent: ToolIntent) -> bool
```

**Purpose**: Decides whether a prepared tool action should still be allowed when a workspace has run out of balance. The special allowed action is managing billing, because blocking that would also block the user from fixing the problem.

**Data flow**: It receives a ToolIntent, which is a pre-made tool call. It checks whether the tool is an object action and whether its kind and action fields exactly match the workspace billing-management action. It returns true only for that case and false for all others.

**Call relations**: This function fits into billing or admission gates that normally refuse work for an overdrawn workspace. It gives those gates a narrow exception for the one action that can restore payment, without handing off to any other helper.


##### `turn_queue_for`  (lines 250–258)

```
def turn_queue_for(parent_turn_id: UUID | None, admission_source: 'TurnAdmissionSource') -> str
```

**Purpose**: Chooses which queue a turn should enter. Normal root turns go to the regular turn queue, while spawned child turns and prepared intents go to an express queue so they do not get stuck behind capacity limits.

**Data flow**: It receives an optional parent turn ID and an admission source. If there is a parent turn, or if the source says this is a prepared intent, it returns the express queue name. Otherwise it returns the regular turn queue name.

**Call relations**: This is used during turn admission, when the system first decides how work should be dispatched. It does not call other project functions; it simply applies the queueing rule that keeps parent-child work and short intent actions from deadlocking or waiting unnecessarily.


##### `turn_id_for`  (lines 267–269)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates the stable ID for a turn. The same workspace, conversation, and sequence number always produce the same UUID, which helps retries avoid creating duplicate turns.

**Data flow**: It receives a workspace ID, conversation ID, and sequence number. It combines them into a URL-like name and passes that name to UUID version 5 generation, which creates a deterministic UUID. The returned UUID becomes the turn identity.

**Call relations**: This is used wherever a new turn identity must be derived before storing or running work. It hands the actual deterministic UUID creation to the standard library's uuid5 function.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 272–277)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing ledger ID for one turn, one billing dimension, and one run attempt. This prevents replayed work from writing duplicate billing rows while still allowing a resumed attempt to be billed separately.

**Data flow**: It receives the workspace ID, turn ID, billing dimension, and an optional attempt ID. It formats those values into a stable name and uses UUID version 5 to turn that name into a UUID. The output is the ID for that billing record.

**Call relations**: This belongs to the billing path around a turn run. It delegates deterministic UUID creation to the standard library, so callers can safely retry the same billing write and land on the same row.

*Call graph*: 1 external calls (uuid5).


##### `mid_turn_reply_id_for`  (lines 280–291)

```
def mid_turn_reply_id_for(turn_id: UUID, round_index: int, span_index: int, attempt: str='') -> UUID
```

**Purpose**: Creates a stable ID for a reply that is sent before a turn has fully finished. This lets the system retry delivery without showing the same mid-turn message twice.

**Data flow**: It receives the turn ID, round number, span position, and optional run attempt ID. It combines these into a stable name and generates a deterministic UUID from it. The result identifies exactly one mid-turn reply for that attempt and position.

**Call relations**: This is part of the message-delivery flow for turns that speak while still running. It uses uuid5 so a replay of the same attempt repeats the same ID, while a resumed attempt can produce fresh IDs for fresh words.

*Call graph*: 1 external calls (uuid5).


##### `RuntimeIdentity._artifact_pair`  (lines 447–450)

```
def _artifact_pair(self) -> 'RuntimeIdentity'
```

**Purpose**: Checks that runtime revision and image digest are provided together. This prevents a record from naming only half of the deployed runtime artifact.

**Data flow**: It reads the RuntimeIdentity being built. If exactly one of revision or image_digest is missing, it raises an error. If both are present or both are absent, it returns the record unchanged.

**Call relations**: Pydantic calls this validator automatically after creating a RuntimeIdentity. It is a guardrail for any code that records which service and sandbox runtime actually ran a turn.


##### `TurnRuntimeConfig._pinned_values`  (lines 469–477)

```
def _pinned_values(self) -> 'TurnRuntimeConfig'
```

**Purpose**: Checks that per-turn runtime choices are concrete and valid. A turn may pin a real model or environment document, but it may not pin the vague model value “auto” or a malformed environment digest.

**Data flow**: It reads the TurnRuntimeConfig being built. If the model is exactly “auto”, it raises an error. If an environment value is present, it must look like a SHA-256 digest. Valid records are returned unchanged.

**Call relations**: Pydantic calls this validator when a TurnRuntimeConfig is created. It protects later runtime setup code from receiving ambiguous model choices or invalid environment references.


##### `TurnContext._tag_safe_line`  (lines 534–538)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans user-surface text so it can be safely inserted into a context block. It removes angle brackets and flattens whitespace so sender names, questions, or source strings cannot fake markup.

**Data flow**: It receives one optional text value. If the value is missing, it stays missing. Otherwise the function removes “<” and “>”, splits and rejoins whitespace into one line, and returns the cleaned text, or null if nothing remains.

**Call relations**: Pydantic applies this validator to TurnContext sender, question, and source fields. It acts before the engine renders those facts into the prompt context for a turn.


##### `TurnContext._known_zone`  (lines 542–549)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a provided time zone name is real. This catches bad surface-provided time zones before a turn is running.

**Data flow**: It receives an optional time zone string. If it is missing, it returns it unchanged. If present, it asks the system time-zone database to load it; success returns the original string, while failure raises a clear validation error.

**Call relations**: Pydantic calls this validator for the TurnContext timezone field. It uses the standard library's ZoneInfo lookup so later prompt-building code can trust that the stored zone name is valid.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn.spawned`  (lines 586–589)

```
def spawned(self) -> bool
```

**Purpose**: Tells whether this turn was created as a child of another turn. In plain terms, it answers: did another agent run spawn this work?

**Data flow**: It reads the Turn's parent_turn_id field. If that field is present, it returns true. If there is no parent turn ID, it returns false.

**Call relations**: This property is read by code that needs to distinguish root turns from spawned child turns. It does not call other helpers; it summarizes the parent-link rule in one place.


##### `Turn.authority`  (lines 592–594)

```
def authority(self) -> ExecutionAuthority
```

**Purpose**: Computes whose authority the turn runs under: a member, someone acting on behalf of a member, or workspace-level authority. This matters because tools and actions need to know what permissions apply.

**Data flow**: It reads the turn's speaker_member_id and on_behalf_of_member_id. It passes those IDs to turn_authority, which returns an ExecutionAuthority object. The property returns that authority to the caller.

**Call relations**: Other turn-running or validation code can ask the Turn for its authority instead of rebuilding the rule. This property hands off the permission decision to ufo.runtime.authority.turn_authority.

*Call graph*: 1 external calls (turn_authority).


##### `Turn._nothing_created`  (lines 598–601)

```
def _nothing_created(cls, value: object) -> object
```

**Purpose**: Turns a missing created-objects value into an empty tuple. This lets database null mean “nothing was created” instead of forcing every caller to check for null.

**Data flow**: It receives the raw created_refs value before normal validation. If the value is null, it returns an empty tuple. Any other value is returned as-is for Pydantic to continue validating.

**Call relations**: Pydantic applies this validator when loading or creating a Turn. It protects later code that reads created_refs by ensuring it can treat the field as a collection.


##### `Turn._aware_utc`  (lines 605–610)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Ensures turn timestamps are marked as UTC time. This avoids a subtle bug where a database driver returns a timestamp without a timezone marker and Python might treat it as local time.

**Data flow**: It receives an optional datetime value. Missing values stay missing. If the timestamp already has timezone information, it is returned unchanged; if not, UTC timezone information is attached and the adjusted datetime is returned.

**Call relations**: Pydantic applies this validator to created_at, updated_at, and retry_at. It uses datetime.replace from the standard library to add the UTC marker when needed.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 613–619)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that a turn's final result agrees with its status. A running or queued turn must not already have a terminal frame, and a done, failed, or cancelled turn must have one.

**Data flow**: It reads the completed Turn object. First it forces authority computation, so invalid authority combinations are caught. Then it checks whether the terminal field is present exactly when the status is terminal, and whether terminal.status matches the turn status. It raises an error on mismatch or returns the Turn unchanged.

**Call relations**: Pydantic calls this validator after building a Turn. It ties together the status field and the terminal result record so downstream workers, surfaces, and storage readers see one consistent story.


### `core/src/ufo/schema/tables.py`

`data_model` · `database setup and runtime data writes`

Think of this file as the blueprint for the system’s filing cabinet. It does not store the data itself. Instead, it tells the database what drawers exist, what labels each drawer has, and which papers are allowed to go where.

The file uses SQLAlchemy, a Python library for describing databases in code. Its central object is `metadata`, which collects every table definition. Other parts of the system can use this metadata to create tables, run migrations, or build database queries without rewriting the schema by hand.

The tables cover the main things the product remembers: workspaces, members, agents, conversations, turns in a conversation, incoming messages, billing ledger entries, spending caps, credentials, external connections, shared artifacts, synced source pages, runtime workers, and access records. The schema also encodes important rules. For example, an agent can only have certain reasoning levels, a turn can only be in known statuses such as queued or done, token counts cannot be negative, and some identities must be unique inside a workspace. These rules protect the data even if a bug elsewhere tries to write something invalid.

A small helper chooses the default audience for a conversation based on the member creating it. Without this file, the rest of the application would not have a single trusted map of what can be stored, how records relate, or what the database must reject.

#### Function details

##### `_conversation_audience`  (lines 12–13)

```
def _conversation_audience(context: DefaultExecutionContext) -> str
```

**Purpose**: This function decides the default audience label for a new conversation. In plain terms, it helps answer: should this conversation be shared, or tied to a specific member?

**Data flow**: It receives a database execution context from SQLAlchemy, which contains the values currently being inserted. It reads the `member_id` from those values, asks `conversation_audience` to turn that member information into the correct audience value, converts the result to text, and returns that text as the value stored in the `audience` column.

**Call relations**: This function is not normally called by application code directly. SQLAlchemy calls it when inserting a conversation row that needs a default `audience`. It relies on `DefaultExecutionContext.get_current_parameters` to see the pending row values, then hands the member id to `ufo.runtime.turns.audience.conversation_audience` so the same audience rule is used consistently across the system.

*Call graph*: 2 external calls (get_current_parameters, conversation_audience).

## 📊 State Registers Touched

- `reg-database-schema-version` — The current shape and migration level of the database, so old stored data can be upgraded and all code agrees on table layouts.
- `reg-extension-store` — The per-workspace saved data that extensions use to remember their own settings and state.
- `reg-prompt-skill-environment` — The saved instructions, skills, environment documents, and fingerprints that shape what an agent sees for a turn.
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
- `reg-sandbox-handles` — The durable handles and leases that let conversations reconnect to their sandbox, files, ports, hosted previews, and work directories.
- `reg-source-sync-state` — The saved state for connected information sources, including cursors, pages, deletions, warnings, backoff, and source access grants.
- `reg-object-artifact-site-store` — The shared store of workspace objects, files, artifacts, previews, reports, websites, todos, and objective records.
- `reg-object-change-journal` — The durable history of object changes, recording who changed what and what the object looked like before and after.
- `reg-schedules-automations` — The durable alarm clock for future work, pauses, monitors, source-change triggers, notification inbox items, and extension jobs.
- `reg-delegation-state` — The parent-child work state that tracks subagent turns, their contracts, trace links, pending results, and delivery back to the parent.
- `reg-billing-ledger-balance` — The shared money and usage record, including spend caps, model costs, sandbox and egress usage, prepaid balances, and export progress.
- `reg-observability-trace` — The tracing, health, logging, and traceparent state used to connect work across turns, subagents, workers, and cleanup.
- `reg-database-connection-pools` — Process-global database engines, sessions, transaction handles, and connection pools shared by serving, workers, migrations, and cleanup code.
- `reg-blob-storage-state` — The raw byte/blob storage namespaces and content-addressed stored files that back artifacts, previews, environment files, workspace files, and deploy-wide assets.
- `reg-inbound-message-buffer` — Durable inbound messages from external surfaces waiting to be rendered, admitted, deduplicated, or converted into conversation work.
- `reg-human-request-state` — Pending and resolved human-interaction requests, including agent questions, secret requests, credential requests, and connection-authorization handoffs.
- `reg-improvement-proposals` — Durable proposed changes and offline-improvement candidates, including their pending, approved, or rejected review state.
- `reg-transcript-access-audit` — Audit records of privileged transcript reads, especially admin access to another member’s private conversation history.
