# Cross-cutting persistence and domain schemas  `stage-16` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for keeping important system facts in storage. It is not one single step in startup or shutdown. Instead, many parts of the system use these definitions while they do their work, such as admission, conversation turns, tools, billing, memory, syncing, onboarding, and the user interface.

The two files here focus on saved conversations. core/src/ufo/transcript.py defines the common “shape” of a transcript: the agreed format for stored conversations and for compacted summaries of older conversation content. This is like a standard form everyone must fill out the same way, so readers and writers do not misunderstand each other.

core/src/ufo/loop/transcript.py uses that format to actually read and write transcript data durably. It also protects newer saved data from being replaced by stale or repeated updates. Together, one file defines the contract, and the other safely applies it while the system runs.

## Files in this stage

### Transcript persistence
Durable transcript access is paired with the shared saved-conversation storage format used by readers and writers.

### `core/src/ufo/loop/transcript.py`

`domain_logic` · `turn completion and repair republishing`

A conversation transcript is the saved record of what has happened so far in a conversation. This file wraps a shared blob store, which is a simple storage place for named chunks of data, and gives the rest of the system one safe way to read and write that record.

The important rule here is that transcript versions only move forward. Each saved conversation has a `seq`, a sequence number that acts like a page number in a notebook. Before writing, this file reads the current saved transcript. If the saved transcript already has the same or a higher sequence number, the new write is ignored. That means the first successful write for a turn is treated as the trusted one, and late or repeated attempts cannot roll the conversation backward.

When reading, the file asks the blob store for the transcript using a key made from the conversation ID. If nothing has been saved yet, it returns `None` instead of treating that as an error. If data is found, it decodes the stored bytes back into a `Conversation` object. When writing a newer conversation, it encodes the conversation and stores it under the same key. Without this guard, overlapping tasks or repair flows could accidentally replace the official transcript with stale data.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: Reads the saved transcript for this conversation, if one exists. It gives callers either the decoded conversation record or `None` when the conversation has not been stored yet.

**Data flow**: It starts with the `conversation_id` stored in the `Transcript` object. It turns that ID into the blob-store key, asks the blob store for the saved bytes, and if the blob is missing it returns `None`. If bytes are found, it decodes them into a `Conversation` object and returns that.

**Call relations**: This is the lookup step used before a write decides whether it is safe to save. `Transcript.write` calls it to compare the currently stored sequence number with the sequence number of the conversation it wants to publish.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: Saves a conversation transcript only if it is newer than what is already stored. This protects the durable transcript from being overwritten by stale work.

**Data flow**: It receives a `Conversation` object to save. First it reads the current stored transcript. If there is already a transcript with an equal or higher `seq`, it stops and changes nothing. Otherwise, it encodes the new conversation into bytes, builds the blob-store key from the conversation ID, and writes those bytes to the blob store.

**Call relations**: This is the publishing step used when a turn finishes, or when a repair path republishes a committed ending state. It relies on `Transcript.read` for the safety check, then hands the conversation to the shared transcript encoder and blob store only when the new version is allowed to become the durable record.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### `core/src/ufo/transcript.py`

`io_transport` · `conversation save/load and compaction readback`

This file is the project’s common contract for durable conversation history. A conversation transcript is not just chat text; it also records the message sequence number, the system prompt used for the model, and any extra context injected into that turn. That matters because later tools, such as debugging screens or evaluation runs, need to reconstruct what the model actually saw.

The file also defines how “compaction” records are stored. Compaction means taking an older, bulky part of a conversation and replacing it with a structured summary so the model can keep working without carrying every old message. Like boxing up old paperwork and leaving a clear label on the box, these records save the original window, the replacement window, and the summary that explains what was kept.

Everything is stored as JSON compressed with LZ4, a fast compression format. The JSON gives a readable, structured shape; the compression keeps blob storage smaller. The key-building functions decide exactly where each item lands in the blob store. The decode functions validate stored bytes against strict models and raise a clear transcript-specific error if the saved data is corrupt or no longer matches the expected shape.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for the main transcript of one conversation. Code uses this so every part of the system looks for the transcript in the same place.

**Data flow**: It receives a conversation ID → inserts that ID into a fixed path pattern → returns a string such as a blob-store filename for the compressed transcript.

**Call relations**: This is a small shared naming rule. Writers and readers can call it whenever they need the blob key for a conversation’s saved messages, avoiding hard-coded paths spread through the codebase.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a validated Conversation object into compact bytes ready to save. It makes the transcript stable and small by converting it to JSON and then compressing it.

**Data flow**: It receives a Conversation → asks the model for its plain data form → writes that data as sorted, compact JSON text → encodes the text as bytes → compresses those bytes with LZ4 → returns the compressed byte string.

**Call relations**: This function sits on the write side of transcript storage. When a conversation needs to be persisted, this is the shared codec that prepares the data so later readers can use decode to reverse the process.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns saved transcript bytes back into a Conversation object. It also converts low-level compression or validation failures into a clear TranscriptDecodeError.

**Data flow**: It receives compressed bytes from storage → decompresses them → validates the resulting JSON against the Conversation shape → returns a Conversation. If the bytes are damaged or the JSON no longer fits the expected schema, it raises TranscriptDecodeError instead.

**Call relations**: This is the read-side partner to encode. Any reader that retrieves a transcript blob can use it to safely rebuild the conversation record and get a project-specific error when the stored record cannot be trusted.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 101–102)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one part of one compaction record. Each compaction has three saved parts: the messages before, the messages after, and the summary.

**Data flow**: It receives a conversation ID, a compaction index, and which part is wanted → places them into the standard compaction path → returns the blob key string.

**Call relations**: read_compaction_record calls this three times when it wants to fetch a complete compaction record. By centralizing the path format here, all readers and writers agree on where compaction artifacts live.

*Call graph*: called by 1 (read_compaction_record).


##### `decode_compaction`  (lines 105–114)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds a complete CompactionRecord from its three stored byte blobs. It validates both message windows and the structured summary before returning them together.

**Data flow**: It receives the compaction index plus compressed bytes for before, after, and summary → decompresses each blob → validates the before and after blobs as message windows → validates the summary as a structured CompactionSummary → returns a CompactionRecord containing all three pieces. If any piece is unreadable or malformed, it raises TranscriptDecodeError.

**Call relations**: read_compaction_record uses this after it has fetched the three blobs from storage. This function is the checkpoint that turns raw stored bytes into a reliable, typed record for debug tools, evaluation code, or other readers.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_record`  (lines 117–128)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Fetches one numbered compaction record for a conversation from the blob store. If that numbered record does not exist, it returns None instead of treating absence as corruption.

**Data flow**: It receives a blob store, a conversation ID, and an index → builds the three expected keys for before, after, and summary → asks the blob store for each blob → if any blob is missing, returns None → otherwise decodes the three blobs into a CompactionRecord and returns it.

**Call relations**: read_compaction_records calls this repeatedly while walking through a conversation’s compaction history. Inside, it relies on compaction_key to find the right blobs and decode_compaction to turn those blobs into a usable record.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 131–141)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all saved compaction records for a conversation in order. It stops at the first missing index because compactions are written sequentially starting from one.

**Data flow**: It receives a blob store and conversation ID → starts at index 1 → repeatedly asks read_compaction_record for the next record → adds each found record to a list → stops when None is returned → returns all found records as an ordered tuple.

**Call relations**: This is the convenient bulk reader for tools that need the whole compaction history, such as a debug view or evaluation harness. It delegates the per-record storage details to read_compaction_record and only controls the simple oldest-to-newest walk.

*Call graph*: calls 1 internal fn (read_compaction_record).

## 📊 State Registers Touched

- `reg-database-schema` — The shared database layout and migration version that define which long-term records the system can store.
- `reg-workspace-objects` — The shared records for workspaces, agents, members, conversations, artifacts, memories, sources, and other workspace objects.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-inbound-message-state` — The saved incoming messages and surface-provided context waiting to be admitted into a conversation turn.
- `reg-transcript-state` — The stored conversation transcript, including exact recent messages and compact summaries of older content.
- `reg-source-sync-state` — The saved state of external sources, synced pages, deletion markers, cursors, and retry backoff.
- `reg-memory-store` — The long-term memory store of remembered facts and recall results used to inform later responses.
- `reg-artifact-storage` — The shared file and blob storage for generated artifacts, plus the signed download state used to protect them.
- `reg-surface-installations` — The saved Slack, web, terminal, and other surface bindings used to receive messages and send replies back.
- `reg-proposal-governance` — The saved proposals and safety checks used to govern prompt or system improvements before applying them.
- `reg-database-connection-pool` — The shared database engine, session factory, connection pool, and transaction context reused by migrations, request handlers, workers, and background jobs.
