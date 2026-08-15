# Durable conversation transcript storage  `stage-21.2`

This stage is shared behind-the-scenes support for keeping conversation history safe after it leaves memory. It defines what a saved transcript looks like and provides the main doorway used to read and write it in durable storage, meaning storage that should survive beyond one running process.

The shared format lives in core/src/ufo/transcript.py. It is like the agreed form everyone must fill out. It defines the keys, record shapes, and conversion rules used for saved conversations and for compaction records, which are shortened summaries or reorganized versions of older transcript data. Because all parts of the system use the same definitions, a writer and a reader do not accidentally disagree about what a record means.

The safe storage doorway lives in core/src/ufo/loop/transcript.py. It loads and saves transcript records through the storage layer, but with protection against stale writes. If one part has an older copy, it cannot overwrite a newer version. Together, these files make transcript storage consistent and safe.

## Files in this stage

### Transcript storage contracts
Defines the durable transcript storage doorway and the shared record formats that keep conversation reads and writes consistent and version-safe.

### `core/src/ufo/loop/transcript.py`

`io_transport` · `turn completion and repair publishing`

A conversation transcript is the long-lived record of what happened in a conversation. This file wraps a shared blob store, which is a simple place to save and fetch chunks of bytes, and gives the rest of the system a safer way to use it for transcripts.

The main idea is a sequence number, called `seq`, which acts like a page number in a notebook. A later page should not be replaced by an earlier page. When the system wants to write a transcript, this file first reads the currently saved version. If the saved version already has the same or a higher sequence number, the write is ignored. That means the first valid write for a turn stays authoritative, and stale work cannot accidentally roll the conversation backward.

The `Transcript` class knows two things: which blob store to use, and which conversation ID it belongs to. It turns the conversation ID into the storage key, converts saved bytes back into a `Conversation` when reading, and converts a `Conversation` into bytes when writing. If no transcript has been saved yet, reading returns nothing rather than treating that as an error.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: This function fetches the saved transcript for this conversation, if one exists. It gives callers either a decoded `Conversation` object or a clear `None` when there is no saved transcript yet.

**Data flow**: It starts with the `Transcript` object's conversation ID and blob store. It turns the conversation ID into the transcript's storage key, asks the blob store for the saved bytes, and if the blob is missing it returns `None`. If bytes are found, it decodes them into a `Conversation` and returns that usable conversation object.

**Call relations**: This is the read side of the transcript wrapper. `Transcript.write` calls it before saving so it can compare the new conversation against the one already stored. It relies on the shared transcript helpers to build the storage key and to decode the stored bytes.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: This function saves a conversation transcript only if it is newer than the one already stored. It protects the durable record from being overwritten by stale or duplicate work.

**Data flow**: It receives a `Conversation` to save. First it reads the currently saved transcript. If there is already a saved conversation whose sequence number is greater than or equal to the new one, it stops without changing storage. Otherwise, it turns the new conversation into bytes, builds the transcript storage key from the conversation ID, and writes those bytes to the blob store.

**Call relations**: This is the write side of the transcript wrapper. It calls `Transcript.read` as a safety check before it writes. When it decides the new transcript is valid, it hands the conversation to the shared encoder and then stores the encoded result under the standard transcript key.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### `core/src/ufo/transcript.py`

`io_transport` · `conversation persistence, compaction, debug/eval reads`

This file is the contract for durable conversation history. A running agent writes transcript data, debugging tools read it back, and evaluation code studies it later. Those parts live in different packages, so they need a neutral place that says: “this is what a conversation record looks like, this is where it is stored, and this is how to decode it.” Without this file, a small format change in one area could silently break another.

The main transcript record is `Conversation`: a numbered message window, plus optional details about the system prompt and injected context used for a completed turn. The file also defines compaction records. Compaction is when older conversation history is summarized to save space in the model’s limited context window. The compaction data keeps the before window, the after window, the summary, file references, important facts called anchors, and a verification report showing what survived the swap.

For storage, records are converted to JSON, then compressed with LZ4, a fast compression format. Reading reverses that process and validates the shape of the data. If the bytes are corrupt or the saved shape no longer matches the expected schema, the code raises `TranscriptDecodeError` instead of returning questionable data. Think of it like a shared filing cabinet label system plus a strict form checker: everyone stores papers in the same folders, and every retrieved form is checked before use.

#### Function details

##### `transcript_key`  (lines 37–38)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for the main saved transcript of one conversation. Code uses this so every writer and reader looks in the same place for that conversation’s messages.

**Data flow**: It takes a conversation ID, which is a unique identifier, and inserts it into a standard path string. The result is a blob-store key like a folder path pointing to the compressed transcript file.

**Call relations**: This is a shared naming helper. Other parts of the system can call it when they need to save or fetch the durable conversation transcript, so the path format is not duplicated in many places.


##### `encode`  (lines 41–43)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a validated `Conversation` object into compressed bytes that can be stored durably. This gives the system a compact, predictable on-disk representation of a transcript.

**Data flow**: It receives a `Conversation`, asks it for a plain data version, writes that data as JSON with stable ordering, changes the JSON text into bytes, and compresses those bytes with LZ4. The output is the byte string ready to place in blob storage.

**Call relations**: This is the write-side partner to `decode`. Transcript-writing code uses it before saving, while readers later use `decode` to restore the same kind of `Conversation` object.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 46–50)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Turns compressed stored transcript bytes back into a validated `Conversation`. It protects callers from corrupt or outdated stored data by raising a clear transcript-specific error when decoding fails.

**Data flow**: It receives compressed bytes, decompresses them, and asks the `Conversation` schema to validate the JSON inside. If that succeeds, it returns a `Conversation`; if decompression or validation fails, it wraps the failure in `TranscriptDecodeError`.

**Call relations**: This is the read-side partner to `encode`. Any code that fetches a transcript blob can call it to get a safe, typed conversation record instead of working with raw bytes.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 136–137)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one piece of a compaction record: the before window, after window, or summary. This keeps all compaction artifacts filed under a consistent conversation and index.

**Data flow**: It takes a conversation ID, a compaction index number, and which part is being requested. It returns the exact blob-store key where that compressed JSON piece belongs.

**Call relations**: `read_compaction_record` calls this three times when it looks for the before, after, and summary blobs for one compaction. By centralizing the path, readers and writers can share the same storage layout.

*Call graph*: called by 1 (read_compaction_record).


##### `decode_compaction`  (lines 140–149)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Rebuilds one full compaction record from its three stored compressed pieces. It validates the before window, after window, and summary so callers receive one trustworthy `CompactionRecord`.

**Data flow**: It receives the compaction index plus three byte strings: before, after, and summary. It decompresses each, validates the message windows and structured summary, then returns a `CompactionRecord` containing all three. If anything is unreadable or does not match the expected shape, it raises `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` calls this after fetching the three blobs from storage. This function is the point where raw stored bytes become a typed record that debug tools, evaluation code, or other readers can inspect.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_record`  (lines 152–163)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Fetches one compaction record from blob storage, or reports that it is absent. It is the shared read path for tools that need to inspect a specific compaction boundary.

**Data flow**: It receives a blob store, a conversation ID, and an index. It builds the three expected keys, asks the blob store for the before, after, and summary bytes, and returns `None` if any required blob is missing. If all are present, it passes them to `decode_compaction` and returns the decoded record.

**Call relations**: `read_compaction_records` calls this repeatedly while walking through a conversation’s compactions. Inside, it relies on `compaction_key` for the storage paths and `decode_compaction` to turn fetched bytes into a useful record.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 166–176)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all saved compaction records for a conversation in order. It stops at the first missing index because compactions are written sequentially starting at one.

**Data flow**: It receives a blob store and conversation ID, starts at index 1, and repeatedly asks `read_compaction_record` for the next record. Each found record is added to a list; when a record is missing, the loop ends and the list is returned as an immutable tuple.

**Call relations**: This is the convenience reader for callers that want the whole compaction history, such as a debug view or evaluation harness. It delegates the per-index fetch and decode work to `read_compaction_record`.

*Call graph*: calls 1 internal fn (read_compaction_record).
