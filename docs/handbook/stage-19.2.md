# Durable transcript and shared record formats  `stage-19.2`

This stage is shared behind-the-scenes support. It defines the long-lasting records that let different parts of the system agree on what happened in a conversation, even after a request has moved through queues, workers, billing, memory, or storage.

The transcript files are the conversation’s notebook. core/src/ufo/transcript.py defines the common saved format: how conversation records and shorter “compaction” summaries are named, compressed, written, read, and decoded. core/src/ufo/loop/transcript.py uses that format to safely store and load the active transcript in a shared blob store, which is a place for large saved data. It also prevents an older copy from overwriting newer conversation state.

core/src/ufo/schema/records.py defines the standard shape of a turn, meaning one user request or internal task. It records IDs, status, times, prompts, and results so all system parts speak the same language.

core/src/ufo/subjects.py defines who memory belongs to, either everyone or one member, keeping ownership labels consistent.

## Files in this stage

### Durable transcript access
Conversation transcript readers and writers manage durable blob-store persistence while preventing stale overwrites.

### `core/src/ufo/loop/transcript.py`

`io_transport` · `conversation turn persistence`

A conversation transcript is the saved record of what has happened so far in a conversation. This file wraps the lower-level blob store, which is a place to save and fetch raw bytes, and turns it into a safer transcript store for one conversation. Its main job is to make sure the saved transcript only moves forward.

The key idea is the conversation sequence number, called `seq`. You can think of it like a page number in a notebook. If page 10 has already been saved, someone should not later replace it with page 8 or another version of page 10. The `Transcript` class enforces that rule. When reading, it looks up the transcript by the conversation's unique ID. If nothing has been saved yet, it returns no transcript. If bytes are found, it decodes them into a `Conversation` object.

When writing, it first reads the current saved transcript. If the saved version already has the same or a newer sequence number, the write is ignored. Otherwise, it encodes the new conversation and saves it. This matters because several flows may try to publish conversation state, and the first valid write for a turn should remain authoritative.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: This reads the saved transcript for this conversation, if one exists. It gives callers a usable `Conversation` object instead of raw stored bytes.

**Data flow**: It starts with the `conversation_id` stored on the `Transcript` object and turns it into the storage key for this conversation. It asks the blob store for the bytes at that key. If the blob is missing, it returns `None`; otherwise it decodes the bytes into a `Conversation` and returns that.

**Call relations**: This is the lookup step used by `Transcript.write` before saving anything. It relies on `transcript_key` to find the right blob and `decode` to turn stored bytes back into the in-memory conversation record.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: This saves a conversation transcript, but only if it is newer than what is already stored. It prevents an older or duplicate conversation state from overwriting the current durable record.

**Data flow**: It receives a `Conversation` to save. First it calls `Transcript.read` to see what is already stored. If a current transcript exists and its sequence number is greater than or equal to the incoming one, nothing changes. If the incoming conversation is newer, it encodes that conversation into bytes, builds the transcript storage key from the conversation ID, and writes the bytes to the blob store.

**Call relations**: This is the protective write path for the transcript. It calls `Transcript.read` as a guard check, then uses `encode` and `transcript_key` to store the new transcript in the shared blob store only when the sequence number shows it should win.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### Shared record vocabularies
Common turn and ownership record shapes define the IDs, statuses, timestamps, prompts, outcomes, and memory subjects shared across subsystems.

### `core/src/ufo/schema/records.py`

`data_model` · `cross-cutting`

This file is the contract for work items in the system. A “turn” is like a ticket on a shared work queue: it has an identity, who it belongs to, what was said, what state it is in, and what happened when it finished. Without these shared records, the part that receives a message and the part that processes it could disagree about basic facts, such as whether a turn is still running, finished with an error, or waiting for a user answer.

Most classes here are Pydantic models, which are data containers that also check their own values when they are created. The file defines safe status names, default constants, token usage records, user-question structures, credential and account-connection requests, terminal result frames, agent records, and the main Turn record.

Two helper functions create repeatable UUIDs, meaning the same workspace, conversation, and sequence number always produce the same turn ID. That matters because workflow retries can happen; the system needs to recognize “this is the same work again,” not create a duplicate.

The important safety checks are small but meaningful. Sender names are flattened so they cannot fake markup in the engine’s context. Time zones must be real IANA time zone names, like “America/New_York”. Timestamps that come back without a time zone are treated as UTC. A turn may only carry a terminal result when its status is terminal, keeping the queue state honest.

#### Function details

##### `turn_id_for`  (lines 42–44)

```
def turn_id_for(workspace_id: UUID, conversation_id: UUID, seq: int) -> UUID
```

**Purpose**: Creates a stable ID for a turn from its workspace, conversation, and sequence number. Someone would use this when admitting work to make sure retries or replays point to the same turn instead of creating a duplicate.

**Data flow**: It takes a workspace ID, a conversation ID, and a sequence number. It joins those values into one text key and feeds that key into UUID version 5, which makes a repeatable UUID from fixed input. The result is a UUID that can also be used as the workflow ID for that turn.

**Call relations**: When the system needs a turn identity, this helper delegates the actual UUID creation to uuid.uuid5. That gives higher-level queue and workflow code one simple place to ask for the canonical turn ID.

*Call graph*: 1 external calls (uuid5).


##### `ledger_id_for`  (lines 47–52)

```
def ledger_id_for(workspace_id: UUID, turn_id: UUID, dimension: str, attempt: str='') -> UUID
```

**Purpose**: Creates a stable billing-ledger ID for one spending record tied to a turn. It prevents the same workflow attempt from writing duplicate billing rows while still allowing a resumed attempt to be counted separately.

**Data flow**: It takes the workspace ID, turn ID, billing dimension, and an optional attempt string. It combines them into one text key and passes that key to UUID version 5. The output is a repeatable UUID for exactly that workspace, turn, billing category, and run attempt.

**Call relations**: Billing or usage-recording code can call this before writing a ledger row. The helper hands the composed key to uuid.uuid5 so repeated execution of the same attempt collapses to the same ledger identity.

*Call graph*: 1 external calls (uuid5).


##### `TurnContext._tag_safe_line`  (lines 163–167)

```
def _tag_safe_line(cls, value: str | None) -> str | None
```

**Purpose**: Cleans the reported sender name before it is stored in turn context. This matters because sender names are free text from an outside surface, and they must not be able to fake special tag-like structure later.

**Data flow**: It receives either a sender string or nothing. If there is no sender, it leaves it as nothing. If there is text, it removes angle brackets, splits and rejoins whitespace into a single clean line, and returns that cleaned value; if the result is empty, it returns nothing.

**Call relations**: Pydantic calls this validator when a TurnContext is built or checked. It does not hand off to other project code; it simply turns unsafe or messy sender text into a safe one-line value before the rest of the system sees it.


##### `TurnContext._known_zone`  (lines 171–178)

```
def _known_zone(cls, value: str | None) -> str | None
```

**Purpose**: Checks that a supplied time zone is a real, known time zone name. This catches bad surface-provided values early, before a worker tries to use the time zone during a turn.

**Data flow**: It receives either a time zone string or nothing. If nothing was supplied, it returns nothing. If a string was supplied, it tries to load it with ZoneInfo, Python’s standard time zone database access; if that fails, it raises a clear validation error. If it succeeds, the original string is returned.

**Call relations**: Pydantic invokes this while validating TurnContext. The function calls zoneinfo.ZoneInfo as the source of truth for known time zones, so later engine code can trust that the saved time zone name is usable.

*Call graph*: 1 external calls (ZoneInfo).


##### `Turn._aware_utc`  (lines 202–207)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Makes sure turn timestamps always carry time zone information. This prevents timestamps returned by some database drivers without a time zone marker from being accidentally interpreted as local machine time.

**Data flow**: It receives a created_at or updated_at datetime value, or nothing. If the value is missing, it returns nothing. If the datetime already has time zone information, it returns it unchanged. If it is missing that marker, it adds UTC as the time zone and returns the corrected datetime.

**Call relations**: Pydantic runs this validator when a Turn is constructed or read back into the model. When it needs to repair a timestamp, it uses datetime.replace to attach the UTC marker without changing the clock time.

*Call graph*: 1 external calls (replace).


##### `Turn._terminal_matches_status`  (lines 210–215)

```
def _terminal_matches_status(self) -> 'Turn'
```

**Purpose**: Checks that a turn’s final-result data agrees with its status. This keeps the queue contract consistent: unfinished turns must not have terminal frames, and finished turns must have a matching terminal frame.

**Data flow**: It looks at the Turn after its fields have been filled. If the status is queued, running, or parked, it requires terminal to be absent. If the status is done, failed, or cancelled, it requires terminal to be present and to have the same status. It returns the Turn unchanged when the rules are satisfied, or raises a validation error when they are not.

**Call relations**: Pydantic calls this model-level validator after building a Turn. It acts as a final gatekeeper before queue workers, storage code, or surfaces rely on the turn’s status and terminal result.


### `core/src/ufo/subjects.py`

`data_model` · `cross-cutting`

This file solves a simple but important naming problem. The system stores or recalls memory in different scopes: a shared space that everyone in a conversation can use, and private spaces tied to individual members. If different parts of the project invented their own names for those spaces, memory could be saved under one label and later searched under another, which would make recall unreliable.

The file acts like a label maker. It defines one fixed label, `shared`, for memories visible to the whole conversation. It also defines a fixed prefix, `member:`, for memories that belong to one member. The helper function `member_subject` takes a member's UUID, which is a unique identifier, and turns it into a subject string such as `member:<that-id>`.

This vocabulary is kept in core because more than one subsystem needs it. The sync pipeline and source integration code need to name shared subjects, while the memory extension uses the same rules to decide which subject or subjects should be searched during a turn. The important behavior is that member-specific subjects are not guessed or handwritten elsewhere; they are built in one consistent way here.

#### Function details

##### `member_subject`  (lines 15–16)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: Builds the standard subject name for one member's private memory space. Use this whenever code needs to save or search memory that belongs to a specific member, so every part of the system uses the same label format.

**Data flow**: It receives a member ID as a UUID, which is a unique identifier for that member. It places the fixed `member:` prefix in front of that ID. It returns the finished subject string, without changing anything else.

**Call relations**: Other parts of the system call on this helper when they need the private-memory label for a member. It sits beside the shared subject constant, so code can choose between the conversation-wide memory space and a member-specific memory space using the same vocabulary.


### Saved conversation formats
Shared transcript and compaction storage formats provide consistent naming, compression, encoding, writing, reading, and decoding for saved conversations.

### `core/src/ufo/transcript.py`

`io_transport` · `cross-cutting transcript persistence and readback`

A conversation transcript is not useful if each part of the system guesses a different file name or data shape. This file is the shared contract for those saved records. It says where transcript blobs live in the blob store, what fields they contain, and how they are turned into bytes and back again.

The main saved conversation shape is `Conversation`. It contains the current message window, a sequence number, and, when available, the exact system prompt and injected context used for a completed model turn. That matters for debugging, because it lets a human see the full input the model actually received.

The file also defines the saved form of “compaction.” Compaction is when a long conversation head is summarized so the model can keep working without carrying every old message. The code stores three pieces for each compaction: the messages before, the messages after, and a structured summary explaining what was compressed. Think of it like replacing a pile of papers with a labeled folder and an index card.

All records are JSON, then compressed with LZ4, a fast compression format. If old or damaged bytes cannot be read as the expected shape, the code raises `TranscriptDecodeError`, giving callers one clear failure type.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the blob-store path for the main transcript of one conversation. Callers use it so everyone saves and reads the transcript from the same place.

**Data flow**: It receives a conversation identifier, which is a UUID, meaning a globally unique ID. It inserts that ID into a fixed path pattern. The result is a string path ending in `messages.json.lz4`, showing that the stored data is JSON compressed with LZ4.

**Call relations**: This is the small naming rule that transcript writers and readers rely on. It does not call other project code; it simply gives the shared address for the durable conversation blob.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a validated `Conversation` object into compressed bytes ready to store. This keeps the saved transcript compact and in a predictable JSON format.

**Data flow**: It takes a `Conversation`, asks it for its plain data form, converts that data to JSON with stable formatting, encodes the JSON as bytes, and compresses those bytes with LZ4. The output is the byte string that can be written to storage.

**Call relations**: Transcript-writing code calls this when it is time to persist a conversation. Inside, it relies on `Conversation.model_dump` to get validated fields and on `json.dumps` to create the JSON text before compression.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns stored transcript bytes back into a validated `Conversation`. It gives readers a safe way to reject corrupt or outdated transcript data.

**Data flow**: It receives compressed bytes from storage. It decompresses them, asks the `Conversation` model to validate the JSON, and returns a `Conversation` object. If decompression or validation fails, it raises `TranscriptDecodeError` instead of leaking low-level errors.

**Call relations**: Transcript-reading code uses this after fetching the stored transcript blob. Its main handoff is to the `Conversation` validator; if that cannot understand the bytes, this function wraps the problem in the file’s shared decode-error type.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 100–101)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the blob-store path for one piece of a compaction record. A compaction has three pieces: `before`, `after`, and `summary`, and this function gives each one its exact storage address.

**Data flow**: It receives a conversation ID, a compaction index number, and the requested half name. It combines them into a fixed path such as a folder per conversation, then a folder per compaction number, then a compressed JSON file name for that piece. The output is a string path.

**Call relations**: When `read_compaction_record` needs to fetch the three stored pieces of a compaction, it calls this function three times. This keeps the naming rule in one place so writers and readers do not drift apart.

*Call graph*: called by 1 (read_compaction_record).


##### `decode_compaction`  (lines 104–113)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds one complete compaction record from its three stored byte blobs. It validates both message windows and the structured summary before returning them.

**Data flow**: It receives the compaction index plus three compressed byte strings: the messages before compaction, the messages after compaction, and the summary. It decompresses each one, validates the two message windows and the summary against their expected shapes, then returns a `CompactionRecord`. If any piece is unreadable or has the wrong shape, it raises `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` calls this after it has fetched all three blobs from storage. This function then creates the durable in-memory record, using `CompactionRecord` as the final bundle handed back to callers.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_record`  (lines 116–127)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one numbered compaction record for a conversation from the blob store. It returns `None` when that numbered record does not exist.

**Data flow**: It receives a blob store, a conversation ID, and an index number. It builds the three storage keys for `before`, `after`, and `summary`, fetches each blob, and then decodes them into a `CompactionRecord`. If any fetch reports that the blob is missing, it treats that index as absent and returns `None`.

**Call relations**: `read_compaction_records` calls this repeatedly while walking through a conversation’s compaction history. This function does the per-index work: it asks `compaction_key` for paths, asks `BlobStore.get` for bytes, and hands those bytes to `decode_compaction`.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 130–140)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for one conversation, in order from oldest to newest. It stops when it reaches the first missing index.

**Data flow**: It receives a blob store and a conversation ID. Starting at index 1, it asks for one compaction record at a time. Each found record is added to a list; the first `None` means there are no more sequential records. It returns the collected records as an immutable tuple.

**Call relations**: This is the convenience reader used by parts of the system that need the full compaction history, such as debugging or evaluation tools. It delegates the actual fetch-and-decode step to `read_compaction_record` and only controls the ordered walk through indexes.

*Call graph*: calls 1 internal fn (read_compaction_record).
