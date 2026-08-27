# Shared Search, Model Catalogs, and Knowledge Infrastructure  `stage-24` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support. It gives the rest of the system common “reference shelves” for AI models, search, and stored knowledge, so different parts can ask the same kinds of questions in the same way.

The model files are the catalog desk. The model specification defines what every AI model record must say, such as its name, cost, required API key, and special abilities. The built-in catalog fills that record with known OpenAI and Anthropic models, including prices, token limits, and reasoning support.

The indexing files are the filing system. The core indexing contract defines how text is split into searchable pieces and what a stored piece looks like. The default index stores and searches those pieces locally, using either word matching or embeddings, which are number patterns that roughly represent meaning. The Turbopuffer extension offers another storage backend for the same job.

The search files are the outside-library window. The core search contract defines search results and page fetching, while the Perplexity extension connects that contract to Perplexity’s web API.

## Files in this stage

### Model catalog metadata
Defines the shared model specification and the built-in Anthropic and OpenAI catalog entries that use it.

### `core/src/ufo/models/spec.py`

`data_model` · `cross-cutting; used when models are registered and when each model request is prepared`

This file is the project’s “model fact sheet” format. Instead of scattering separate lists around the codebase for prices, API styles, reasoning support, image support, and credential names, the system keeps those facts together in one frozen record called ModelSpec. Frozen means the record is not meant to change after it is created, which helps keep model behavior predictable.

A ModelSpec describes one model: its provider, how to build a client for talking to it, its price, its knowledge cutoff date, how much context it can accept, whether it supports reasoning, which API surface it uses, and where to find its API key. If a model is missing or incorrectly described, the system can fail early with a clear error instead of breaking later during a user turn.

The file also defines ReasoningSupport, a smaller record that explains whether the model can do extended reasoning, whether that reasoning can be used while tools are active, and whether reasoning is on by default. The helper methods turn these facts into safe request settings. For example, if a provider refuses reasoning while tools are being used, the request simply leaves reasoning out rather than sending a setting the provider will reject.

There is also a clear credential error builder for rejected API keys, so authentication failures can be reported as something the user or workspace can fix.

#### Function details

##### `ReasoningSupport.internal_effort`  (lines 36–39)

```
def internal_effort(self) -> ReasoningEffort
```

**Purpose**: This returns the safest internal reasoning setting for a model before a user request is considered. It answers: should the system treat reasoning as off, or is there a minimum effort that cannot be disabled?

**Data flow**: It reads the ReasoningSupport fields on the current record: whether reasoning is supported, whether it can be disabled, and the minimum effort. If reasoning is unsupported, or if it can be turned off, it returns "off". If reasoning is always on and cannot be disabled, it returns the model’s minimum allowed effort.

**Call relations**: This is a small policy helper attached to the reasoning fact record. Other parts of the model setup can ask it for the model’s baseline internal reasoning effort instead of reinterpreting the same flags themselves.


##### `ModelSpec.__post_init__`  (lines 66–76)

```
def __post_init__(self) -> None
```

**Purpose**: This checks that a newly created ModelSpec is internally consistent. It catches bad model definitions immediately, before the system tries to use them in a live request.

**Data flow**: After a ModelSpec is built, it reads the knowledge cutoff and reasoning flags from that same record. It verifies that the cutoff looks like a year and month, such as "2024-06". It also rejects impossible combinations, such as saying tools can use reasoning when reasoning itself is not supported. If everything is valid, nothing is returned; if something is wrong, it raises a ValueError with a clear message.

**Call relations**: This runs automatically as part of creating a ModelSpec. It protects the model registry and later request-building code by ensuring they only receive model records whose basic facts make sense.


##### `ModelSpec.key_rejected`  (lines 78–89)

```
def key_rejected(self) -> CredentialValueInvalid
```

**Purpose**: This creates a clear, typed error for the case where a provider rejects the API key chosen for this model. It turns a raw authentication failure into a message that tells someone which key sources to check and replace.

**Data flow**: It reads the model id, provider name, environment-variable key name, and workspace BYOK slot name from the ModelSpec. It builds a human-readable message explaining that the provider did not accept the key. It returns a CredentialValueInvalid error object containing that message.

**Call relations**: When provider authentication comes back as rejected, model-calling code can use this method to report the problem as a bad credential rather than a generic stream or network failure. Inside the method, it hands the finished message to CredentialValueInvalid.__init__ to create the typed error object.

*Call graph*: 1 external calls (__init__).


##### `ModelSpec.wire_reasoning`  (lines 91–105)

```
def wire_reasoning(self, requested: ReasoningEffort, tools: tuple[ToolSchema, ...]) -> ReasoningEffort | None
```

**Purpose**: This decides what reasoning setting should actually be sent to the model provider for one request. It prevents the system from sending reasoning options that the model or API surface does not support.

**Data flow**: It receives the reasoning effort requested for this call and the tools being included in the request. It reads the model’s reasoning support rules. If the model does not support reasoning, it returns None, meaning no reasoning setting should be sent. If tools are present but the model cannot combine tools with reasoning, it also returns None. If the caller asked for "off" but the model has unavoidable default reasoning, it returns the model’s minimum effort instead. Otherwise, it returns the requested effort unchanged.

**Call relations**: This method is used when a request is being prepared for the provider. It sits between the user or system’s desired reasoning level and the final wire request, making sure the outgoing request matches the model’s declared limits.


### `core/src/ufo/models/catalog.py`

`config` · `startup and model/pricing lookup`

This file is like a price list and instruction card for every model that ships with the core system. Without it, the system would not know which built-in model IDs are valid, how much to charge or record for their usage, how large a conversation can grow before it must be shortened, or which provider client to create when a user picks a model.

The file starts by naming the environment variables that hold provider API keys, such as ANTHROPIC_API_KEY and OPENAI_API_KEY. It also defines common context windows, meaning the rough maximum number of tokens a model can accept in one request. A token is a small chunk of text used for counting input and output size.

Small helper functions build provider-specific model entries. For Anthropic models, `_anthropic` creates a `ModelSpec`, which is a complete description of one model. For OpenAI models, `_openai` does the same, including whether the model should use the chat API or the newer responses API.

The main function, `core_model_specs`, returns the full tuple of built-in model specifications. Each entry includes pricing through `ModelPrice`, knowledge cutoff dates, cache pricing, reasoning support, and context-window choices. At import time, the file also builds quick lookup objects for prices and a pricing digest, so the rest of the system can consistently bill or audit model usage.

#### Function details

##### `_anthropic_client`  (lines 27–28)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function creates a ready-to-use Anthropic model client for one model specification and one API key. It is the bridge between the catalog’s plain model description and the actual Anthropic software client that can make requests.

**Data flow**: It receives a `ModelSpec`, which describes the model, and a secret API key string. It first creates the underlying Anthropic SDK client with that key, then wraps it in the project’s `AnthropicClient` along with the model specification. The result is an Anthropic client object that knows both how to talk to Anthropic and which model rules to follow.

**Call relations**: This function is stored inside Anthropic `ModelSpec` entries as the client factory. Later, when the system needs to run an Anthropic model, the model specification can call this factory to build the real client. It hands off the low-level provider setup to `anthropic_sdk_client` and then packages it through `AnthropicClient`.

*Call graph*: 2 external calls (__init__, anthropic_sdk_client).


##### `_openai_client`  (lines 31–32)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function creates a ready-to-use OpenAI model client for one model specification and one API key. It connects the catalog entry for an OpenAI model to the actual OpenAI client used for requests.

**Data flow**: It receives a `ModelSpec` and an API key string. It uses the key to create the underlying OpenAI SDK client, then combines that SDK client with the model specification inside an `OpenAIClient`. The output is a project-level OpenAI client that knows the model’s settings and can send requests to OpenAI.

**Call relations**: This function is attached to OpenAI `ModelSpec` entries as their client factory. When another part of the system chooses an OpenAI model and has an API key, this factory is the piece that turns those facts into a usable client. It delegates raw SDK creation to `openai_sdk_client` and wraps the result in `OpenAIClient`.

*Call graph*: 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 35–55)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, key_env: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS_WITH_TOOLS) -> ModelSpec
```

**Purpose**: This helper builds one complete catalog entry for an Anthropic model. It keeps repeated Anthropic-specific settings in one place so each model row only needs to state what is unique, such as its ID, price, and knowledge cutoff.

**Data flow**: It receives the model ID, pricing object, knowledge cutoff date, API-key environment variable name, and optional settings such as context window and reasoning support. It fills in Anthropic defaults: provider name, client factory, API surface, key slot, and key environment variable. It returns a `ModelSpec`, which is the finished description the rest of the system can register and use.

**Call relations**: `core_model_specs` calls this function repeatedly while building the built-in Anthropic portion of the catalog. `_anthropic` does not contact Anthropic itself; it prepares the instruction card that later code can use to create a client through `_anthropic_client`.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `_openai`  (lines 58–72)

```
def _openai(id: str, price: ModelPrice, cutoff: str, key_env: str, *, api_surface: ApiSurface='chat') -> ModelSpec
```

**Purpose**: This helper builds one complete catalog entry for an OpenAI model. It avoids repeating the same OpenAI-specific settings for every model listed in the catalog.

**Data flow**: It receives the model ID, pricing object, knowledge cutoff date, API-key environment variable name, and optionally which OpenAI API surface to use. It adds shared OpenAI details such as provider name, client factory, context window, reasoning support, key slot, and key environment variable. It returns a finished `ModelSpec` for that model.

**Call relations**: `core_model_specs` calls this function for each built-in OpenAI model. Some newer GPT models are marked to use the responses API instead of the chat API, and this helper records that choice in the returned specification. Later, the specification can create a real OpenAI client through `_openai_client`.

*Call graph*: called by 1 (core_model_specs); 1 external calls (__init__).


##### `core_model_specs`  (lines 75–180)

```
def core_model_specs(anthropic_key_env: str, openai_key_env: str) -> tuple[ModelSpec, ...]
```

**Purpose**: This function builds the full list of model specifications that core ships with. It is the central source of truth for built-in model IDs, prices, token limits, knowledge cutoffs, reasoning support, and provider routing.

**Data flow**: It receives the names of the environment variables that should supply Anthropic and OpenAI API keys. It then creates many `ModelPrice` objects and passes them into `_anthropic` or `_openai` to make one `ModelSpec` per model. The output is a tuple of all built-in model specifications, ready to be registered, priced, and used by the rest of the system.

**Call relations**: This is the main builder in the file. It calls `_anthropic` for Claude models and `_openai` for GPT models, while `ModelPrice` records the billing rates used later by the ledger. At module load time, the file calls `core_model_specs` once to create `CORE_MODEL_SPECS`, then derives price lookup tables and a pricing digest from that catalog.

*Call graph*: calls 2 internal fn (_anthropic, _openai); 1 external calls (__init__).


### Searchable indexing backends
Defines the chunk indexing contract and the default and Turbopuffer implementations for storing and searching embedded text.

### `core/src/ufo/indexing.py`

`domain_logic` · `indexing and retrieval`

This file is the project’s indexing doorway. Indexing means taking a larger body of text, cutting it into smaller searchable pieces, turning those pieces into numeric meaning-vectors called embeddings, and asking a backend to store them. Without this file, different parts of the system could disagree about what a searchable chunk is, how chunks are named, or how old chunks are removed after text changes.

The main idea is separation. `TextChunker` does the in-memory text work. It splits text into chunks of roughly a target size, tries to split at natural places like paragraphs or sentences, adds a little overlap so a search does not lose context at chunk boundaries, and caps very long chunks by character count. This is like cutting a long article into index cards, while copying the last few lines of one card onto the next so the story still makes sense.

`Chunk`, `Hit`, and `IndexScope` are small value objects passed across the indexing boundary. `IndexBackend` describes what a storage/search extension must provide, such as saving chunks, deleting them, or doing text and vector searches. `EmbedClient` describes the embedding service. The shared helper `chunk_embed_upsert` ties these together: it chunks one body, embeds each chunk, saves the new chunks, then prunes old chunks for the same owner so stale search results do not remain after edits.

#### Function details

##### `IndexBackend.upsert`  (lines 68–68)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This is the storage-side promise for saving chunks into an index. “Upsert” means insert if new, or update if already present, so repeated indexing of the same text is safe.

**Data flow**: It receives a group of `Chunk` objects, each containing text, ownership details, and usually an embedding. The backend implementation stores those chunks or refreshes existing records with the same identity. It returns no value, but the index is changed so later searches can find the chunks.

**Call relations**: `chunk_embed_upsert` calls this after text has been split and embedded. The protocol does not say how storage works; an extension supplies the real database or search-engine behavior behind this promise.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.delete`  (lines 70–70)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This is the storage-side promise for removing all indexed chunks that belong to one owner. It is used when an indexed item should disappear completely from search.

**Data flow**: It receives an `IndexScope`, which names an owner kind and owner id. The backend implementation removes every stored chunk in that scope. It returns no value, but the index loses those chunks.

**Call relations**: The skill creation extension’s `_index_card` flow calls this when it needs to clear indexed content for a card. This file only defines the contract; the actual deletion is done by whichever backend implements it.

*Call graph*: called by 1 (_index_card).


##### `IndexBackend.prune`  (lines 72–72)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This is the storage-side promise for deleting outdated chunks while keeping the current ones. It prevents edited text from leaving old, now-wrong search results behind.

**Data flow**: It receives an `IndexScope` naming the owner and a set of chunk digests that should remain. The backend compares stored chunks for that owner against the keep-set, deletes anything not in it, and returns nothing. Afterward, the owner’s indexed chunks match the latest chunking result.

**Call relations**: `chunk_embed_upsert` calls this every time after processing a body, even if the body produced no chunks. That means an empty or heavily edited body correctly removes stale chunks.

*Call graph*: called by 1 (chunk_embed_upsert).


##### `IndexBackend.has_chunks`  (lines 74–74)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This is the storage-side promise for checking whether an owner already has indexed chunks. It can be used to avoid unnecessary work or decide whether indexing is missing.

**Data flow**: It receives an `IndexScope` naming the owner to check. The backend looks in its index for chunks in that scope. It returns `true` if at least one chunk exists, otherwise `false`.

**Call relations**: No caller is shown in the provided graph, but it belongs to the same backend contract as saving, pruning, deleting, and searching. Extensions implementing an index are expected to provide this check.


##### `IndexBackend.lexical`  (lines 76–78)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the storage-side promise for ordinary text search, where the query words are matched against indexed text. It is useful when exact wording matters.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a maximum number of results. The backend searches stored chunks that fit those filters and returns `Hit` objects with text and scores. The index is only read, not changed.

**Call relations**: No caller is shown in the provided graph, but this is one of the two search paths offered by the indexing seam. A backend implements it so higher-level code can ask for word-based matches without knowing the database details.


##### `IndexBackend.vector`  (lines 80–82)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This is the storage-side promise for meaning-based search using an embedding, which is a list of numbers representing the meaning of text. It helps find relevant chunks even when they do not use the exact same words as the query.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. The backend compares that embedding with stored chunk embeddings, filters the candidates, and returns scored `Hit` objects. It reads from the index and does not change it.

**Call relations**: The queue flow `_shadow_skill_selection` calls this when it needs to select likely relevant skills by meaning. That caller gets an embedding elsewhere, then relies on this backend contract to find matching indexed chunks.

*Call graph*: called by 1 (_shadow_skill_selection).


##### `EmbedClient.embed`  (lines 86–86)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This is the promise made by whatever service turns text into embeddings, which are numeric summaries of meaning. Indexing and semantic search both need this conversion before vectors can be stored or compared.

**Data flow**: It receives a tuple of text strings. The implementation sends or computes those texts through an embedding model and returns one numeric vector for each input text, in the same order. It does not directly change the index.

**Call relations**: `chunk_embed_upsert` calls this after chunking a body so chunks can be saved with embeddings. `_shadow_skill_selection` also calls it to turn a search or selection query into a vector before asking the backend for similar chunks.

*Call graph*: called by 2 (chunk_embed_upsert, _shadow_skill_selection).


##### `chunk_embed_upsert`  (lines 89–114)

```
async def chunk_embed_upsert(index: IndexBackend, embed: EmbedClient, chunker: 'TextChunker', owner_kind: str, owner_id: str, subject: str, body: str) -> None
```

**Purpose**: This is the shared “index this body of text” workflow. It cuts one body into chunks, embeds the chunks, stores them, and removes any old chunks for the same owner that are no longer current.

**Data flow**: It receives an index backend, an embedding client, a chunker, owner details, a subject, and the body text. First it asks the chunker for chunks. If there are chunks, it asks the embedder for matching vectors, copies those vectors into the chunks, and upserts them into the backend. Finally it builds an owner scope and asks the backend to prune everything for that owner except the newly produced chunk digests. It returns nothing, but the index now reflects the latest body.

**Call relations**: This function is the bridge between text processing and storage. It calls `TextChunker.chunk` indirectly through the supplied chunker, then calls `EmbedClient.embed`, `IndexBackend.upsert`, and `IndexBackend.prune` in that order so indexing is both searchable and clean after edits.

*Call graph*: calls 3 internal fn (embed, prune, upsert); 2 external calls (__init__, replace).


##### `TextChunker.chunk`  (lines 123–134)

```
def chunk(self, text: str, owner_kind: str, owner_id: str, subject: str) -> tuple[Chunk, ...]
```

**Purpose**: This turns a body of text into `Chunk` records that the rest of the indexing system understands. It attaches ownership information and gives each chunk a stable digest, which is a unique fingerprint.

**Data flow**: It receives raw text plus the owner kind, owner id, and subject. It asks `_slices` to split the text into pieces, numbers those pieces in order, computes a digest for each one, and wraps each piece in a `Chunk`. It returns all chunks as a tuple, with no embeddings yet.

**Call relations**: `chunk_embed_upsert` depends on this as the first step of indexing. Inside the chunker, this method is the public entry point that coordinates `_slices` for splitting and `_digest` for stable chunk identity.

*Call graph*: calls 2 internal fn (_digest, _slices); 1 external calls (__init__).


##### `TextChunker._slices`  (lines 136–144)

```
def _slices(self, text: str) -> list[str]
```

**Purpose**: This decides how to break raw text into plain text slices before they become `Chunk` objects. It keeps small text whole and applies the full splitting, merging, overlap, and character-capping process to larger text.

**Data flow**: It receives a text string. If the text is blank, it returns an empty list. If the text is already short enough by the chunker’s word count, it trims it and caps it by characters if needed. For longer text, it recursively splits at natural delimiters, merges small pieces back toward the target size, adds overlap between neighboring chunks, and finally applies the character cap. It returns a list of chunk text strings.

**Call relations**: `TextChunker.chunk` calls this before creating `Chunk` records. `_slices` is the central workshop that calls `_count_words`, `_recursive_split`, `_greedy_merge`, `_apply_overlap`, and `_cap_by_chars` to produce practical search-sized text pieces.

*Call graph*: calls 5 internal fn (_apply_overlap, _cap_by_chars, _count_words, _greedy_merge, _recursive_split); called by 1 (chunk).


##### `TextChunker._count_words`  (lines 147–153)

```
def _count_words(text: str) -> int
```

**Purpose**: This estimates the size of a text in words, with special care for Chinese, Japanese, and Korean text where words are often not separated by spaces. The chunker uses this estimate to decide when text is too large.

**Data flow**: It receives a text string. It removes whitespace to count real characters, checks how much of the text is made of CJK characters, and either counts non-whitespace characters for dense CJK text or counts runs of non-space text for space-separated writing. It returns an integer size estimate.

**Call relations**: `_slices`, `_recursive_split`, and `_greedy_merge` call this whenever they need to decide whether a piece is small enough or can be joined with another piece. It keeps the chunking rules usable across different writing systems.

*Call graph*: called by 3 (_greedy_merge, _recursive_split, _slices); 1 external calls (sub).


##### `TextChunker._cap_by_chars`  (lines 155–167)

```
def _cap_by_chars(self, text: str) -> list[str]
```

**Purpose**: This is the safety limit that prevents any one chunk from becoming too large in characters. It matters because very long chunks can be expensive or impossible for embedding and storage systems to process.

**Data flow**: It receives one text piece. If it is within the character limit, it returns that piece, or nothing if it is empty. If it is too long, it cuts it into character windows with a small overlap between windows, trims each window, and returns the non-empty pieces.

**Call relations**: `_slices` calls this for both short text and already-processed chunks as the final size guard. It does not decide semantic boundaries; it simply enforces the maximum size after other splitting has tried to be natural.

*Call graph*: called by 1 (_slices).


##### `TextChunker._recursive_split`  (lines 169–181)

```
def _recursive_split(self, text: str, level: int) -> list[str]
```

**Purpose**: This breaks large text apart by trying increasingly smaller natural boundaries. It starts with broad breaks like paragraphs, then moves down toward sentences, punctuation, and finally whitespace.

**Data flow**: It receives text and a delimiter level. At each level, it tries to split the text using that level’s delimiters. If no useful split is found, it moves to the next level. If a resulting piece is still too large, it recursively splits that piece at a finer level. It returns a list of smaller text pieces.

**Call relations**: `_slices` calls this when the full text is too large. It calls `_split_at_delimiters` for natural separators, `_count_words` to test piece size, and `_split_on_whitespace` as the fallback when delimiter splitting is exhausted.

*Call graph*: calls 3 internal fn (_count_words, _split_at_delimiters, _split_on_whitespace); called by 1 (_slices).


##### `TextChunker._split_at_delimiters`  (lines 184–197)

```
def _split_at_delimiters(text: str, delimiters: tuple[str, ...]) -> list[str]
```

**Purpose**: This cuts text at the earliest matching delimiter from a given set, while keeping the delimiter with the preceding piece. It helps preserve punctuation and paragraph breaks in the chunk text.

**Data flow**: It receives text and a tuple of delimiters such as paragraph breaks or sentence punctuation. It repeatedly finds the next earliest delimiter, moves the text up to and including that delimiter into the output, and continues with the remaining text. It returns only pieces that contain non-whitespace content.

**Call relations**: `_recursive_split` calls this while trying each delimiter level. It is the low-level cutter used before falling back to whitespace-based splitting.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._split_on_whitespace`  (lines 199–215)

```
def _split_on_whitespace(self, text: str) -> list[str]
```

**Purpose**: This is the fallback splitter when natural punctuation-based splitting is no longer available. It divides text into groups of roughly the target number of word-like runs.

**Data flow**: It receives a text string. If the text has normal whitespace-separated words, it groups those words into target-sized batches and joins each batch back into text. If there are no words, or one very long run, it slices the raw text by a simple character-size fallback. It returns non-empty text pieces.

**Call relations**: `_recursive_split` calls this at the final delimiter level. It is the last resort that guarantees very large text can still be broken into indexable pieces.

*Call graph*: called by 1 (_recursive_split).


##### `TextChunker._greedy_merge`  (lines 217–231)

```
def _greedy_merge(self, pieces: list[str]) -> list[str]
```

**Purpose**: This joins nearby small pieces back together so the final chunks are not too tiny. It tries to make useful, fuller chunks without going far beyond the target size.

**Data flow**: It receives a list of already split pieces. Starting from the first piece, it tries to append each next piece to the current one. If the combined text stays within about one and a half times the target word count, it keeps merging; otherwise it saves the current chunk and starts a new one. It returns the merged list.

**Call relations**: `_slices` calls this after recursive splitting. It uses `_count_words` and a rounded size limit to balance two goals: respecting natural boundaries and producing chunks large enough to be useful for search.

*Call graph*: calls 1 internal fn (_count_words); called by 1 (_slices); 1 external calls (ceil).


##### `TextChunker._apply_overlap`  (lines 233–239)

```
def _apply_overlap(self, chunks: list[str]) -> list[str]
```

**Purpose**: This adds context from the end of each chunk to the start of the next chunk. The goal is to avoid losing important meaning that crosses a chunk boundary.

**Data flow**: It receives a list of chunk strings. If there is only one chunk or overlap is disabled, it returns the list unchanged. Otherwise, it keeps the first chunk as-is and prefixes each later chunk with trailing context taken from the previous chunk. It returns the overlapped chunk list.

**Call relations**: `_slices` calls this after pieces have been merged. It uses `_trailing_context` to choose the copied ending text and `itertools.pairwise` to walk through each previous/current chunk pair.

*Call graph*: calls 1 internal fn (_trailing_context); called by 1 (_slices); 1 external calls (pairwise).


##### `TextChunker._trailing_context`  (lines 241–251)

```
def _trailing_context(self, text: str) -> str
```

**Purpose**: This chooses the bit of text to copy from the end of one chunk into the next. It tries to provide helpful context while avoiding awkwardly starting in the middle of a sentence when possible.

**Data flow**: It receives one chunk of text. It collects the last configured number of word-like runs. If there are not enough words, it returns an empty string because the whole previous chunk is already too small to copy from safely. If it finds a sentence boundary early enough in the trailing text, it starts after that boundary; otherwise it returns the raw trailing words.

**Call relations**: `_apply_overlap` calls this for each previous chunk when building overlapped chunks. This function supplies the actual context snippet that gets prefixed to the following chunk.

*Call graph*: called by 1 (_apply_overlap).


##### `TextChunker._digest`  (lines 254–256)

```
def _digest(owner_kind: str, owner_id: str, subject: str, ordinal: int, text: str) -> str
```

**Purpose**: This creates a stable fingerprint for a chunk. The fingerprint identifies not just the text, but also who owns it, what subject it belongs to, and where it appears in the sequence.

**Data flow**: It receives owner kind, owner id, subject, ordinal number, and chunk text. It joins those fields with a separator unlikely to appear by accident, hashes the result with SHA-256, and prefixes the hash with `sha256:`. It returns that digest string.

**Call relations**: `TextChunker.chunk` calls this for every slice it turns into a `Chunk`. `chunk_embed_upsert` later relies on these digests when telling the backend which chunks to keep during pruning.

*Call graph*: called by 1 (chunk); 1 external calls (sha256).


### `extensions/index_default/ufo_ext_index_default.py`

`domain_logic` · `cross-cutting search/index operations`

This file is the project’s default memory search engine. Without it, a fresh deployment would have no standard way to save chunks of text and later retrieve the chunks most relevant to a question or member sentence.

It supports two database worlds. In PostgreSQL, it uses native full-text search for word matching and pgvector for vector search, where an embedding is a list of numbers representing text meaning. In SQLite, it uses FTS5, SQLite’s built-in full-text search table, and does vector comparison in Python by scanning rows and computing cosine similarity. That makes SQLite simple for local or small-scale use, while PostgreSQL is the stronger deployment path.

The main class, DefaultIndex, opens a workspace-scoped database transaction for each operation. It can insert or update chunks, delete all chunks for an owner, check whether an owner already has chunks, prune old chunks after re-chunking, and run lexical or vector searches. A chunk is like a card in a filing cabinet: it has an owner, a subject, an order, text, and optionally an embedding. Searches return Hit objects, which are the same cards plus a score saying how well they matched.

A key detail is that database-specific formats stay hidden here. The rest of the system sees neutral Chunk, Hit, and IndexScope objects, not PostgreSQL halfvec values or SQLite byte blobs.

#### Function details

##### `pgvector_literal`  (lines 35–36)

```
def pgvector_literal(vector: tuple[float, ...]) -> str
```

**Purpose**: Turns a Python tuple of numbers into the text form PostgreSQL’s pgvector extension expects. This lets the index send an embedding to PostgreSQL without exposing that database-specific format to the rest of the project.

**Data flow**: It receives a tuple of floating-point numbers. It converts each value to a float-looking string and joins them inside square brackets. It returns one string, such as a vector literal that PostgreSQL can cast into its vector type.

**Call relations**: DefaultIndex.upsert uses this when saving embeddings into PostgreSQL, and DefaultIndex.vector uses it when sending a query embedding for vector search. It is the small adapter between ordinary Python data and PostgreSQL’s vector syntax.

*Call graph*: called by 2 (upsert, vector).


##### `cosine`  (lines 39–47)

```
def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Compares two embeddings and returns how similar their directions are. This is used for SQLite vector search, where the database does not provide the same vector search feature as PostgreSQL.

**Data flow**: It receives two tuples of numbers. It computes the length of each tuple as a vector, multiplies matching positions together, and divides by the two lengths. It returns a similarity score, with 0.0 used when either vector has no length.

**Call relations**: DefaultIndex.vector calls this after reading candidate embeddings from SQLite. PostgreSQL does this kind of comparison inside the database, but SQLite hands the rows back and this function scores them in Python.

*Call graph*: called by 1 (vector); 1 external calls (sqrt).


##### `pack_embedding`  (lines 50–51)

```
def pack_embedding(vector: tuple[float, ...]) -> bytes
```

**Purpose**: Stores an embedding compactly for SQLite by turning its numbers into bytes. SQLite keeps this as a binary blob rather than as a special vector type.

**Data flow**: It receives a tuple of floating-point numbers. It packs each number into a 32-bit float binary format. It returns a bytes object that can be written into SQLite.

**Call relations**: DefaultIndex.upsert calls this only on the SQLite path when saving chunks. It is paired with unpack_embedding, which reverses the process during vector search.

*Call graph*: called by 1 (upsert); 1 external calls (pack).


##### `unpack_embedding`  (lines 54–55)

```
def unpack_embedding(blob: bytes) -> tuple[float, ...]
```

**Purpose**: Reads an embedding back out of SQLite’s binary storage. It reverses the compact byte format created by pack_embedding.

**Data flow**: It receives a bytes blob from the database. It treats every 4 bytes as one floating-point number. It returns a tuple of floats ready for similarity comparison.

**Call relations**: DefaultIndex.vector calls this on the SQLite path before passing the restored embedding to cosine. Together, unpack_embedding and cosine let SQLite support vector-style retrieval without a database vector extension.

*Call graph*: called by 1 (vector); 1 external calls (unpack).


##### `_hit`  (lines 58–67)

```
def _hit(row: sa.RowMapping, score: float) -> Hit
```

**Purpose**: Builds a Hit object from a database row and a score. A Hit is the search result shape that the rest of the indexing system understands.

**Data flow**: It receives a database row containing chunk fields and a numeric score. It copies the chunk identity, owner, subject, order, and text into a Hit, adds the score, and returns that Hit.

**Call relations**: DefaultIndex.lexical and DefaultIndex.vector both call this after their database or Python scoring work is done. It is the final packaging step that turns raw query results into normal search results.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `DefaultIndex.upsert`  (lines 177–214)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds new chunks to the index or updates existing ones with the same identity. This is how text becomes searchable after the system has split it into chunks.

**Data flow**: It receives a tuple of Chunk objects. If the tuple is empty, it does nothing. Otherwise it opens a database transaction, checks whether the connection is PostgreSQL or SQLite, and writes each chunk using the right SQL. Embeddings are converted to PostgreSQL vector text or SQLite bytes as needed. On SQLite, it also refreshes the full-text search table so word search sees the latest text.

**Call relations**: Other indexing flows call this when they have chunks ready to store. It relies on pgvector_literal for PostgreSQL embeddings and pack_embedding for SQLite embeddings, then hands the actual write work to the database connection.

*Call graph*: calls 2 internal fn (pack_embedding, pgvector_literal).


##### `DefaultIndex.delete`  (lines 216–223)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all indexed chunks for one owner. This is used when a document, memory owner, or similar indexed source should no longer appear in search results.

**Data flow**: It receives an IndexScope, which names an owner kind and owner id. It opens a transaction and deletes matching rows. In SQLite it first removes matching full-text search entries, then removes the main chunk rows. It returns nothing, but the database no longer contains those chunks.

**Call relations**: DefaultIndex.prune calls this when the keep-set is empty, meaning no chunks should remain for that owner. Other cleanup paths can also use it directly to remove an owner’s indexed content.

*Call graph*: called by 1 (prune).


##### `DefaultIndex.has_chunks`  (lines 225–228)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether the index already contains any chunks for one owner. This can help the system decide whether indexing work is needed.

**Data flow**: It receives an IndexScope with the owner kind and owner id. It opens a transaction and asks the database for a single matching chunk. It returns true if one exists and false otherwise.

**Call relations**: This is a small lookup method on the backend. It does not call helper functions; it simply asks the chunk table whether that owner has at least one row.


##### `DefaultIndex.prune`  (lines 230–244)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes stale chunks for an owner while keeping a named set of current chunks. This matters after re-chunking, because old chunks should not linger and appear in future search results.

**Data flow**: It receives an IndexScope and a frozen set of chunk digests to keep. If the keep-set is empty, it deletes the whole scope. Otherwise it opens a transaction and deletes only rows whose digest is not in the keep-set. In SQLite it also removes matching full-text search rows before deleting the main chunk rows.

**Call relations**: When no chunks should be kept, it delegates to DefaultIndex.delete. Otherwise it performs database-specific prune SQL itself. It is part of the indexing refresh story: save the new chunks, then remove orphaned old ones.

*Call graph*: calls 1 internal fn (delete).


##### `DefaultIndex.lexical`  (lines 246–282)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Finds chunks by matching the words in a text query. This is the ordinary keyword-style search path, useful when exact or related terms in the text are strong evidence of relevance.

**Data flow**: It receives a query string, a set of allowed subjects, an owner kind, and a result limit. If there are no subjects, or the cleaned query is empty, it returns no results. Otherwise it opens a transaction and runs PostgreSQL full-text search or SQLite FTS5 search. It converts matching rows into Hit objects with scores and returns them as a tuple.

**Call relations**: Search orchestration calls this when it wants word-based evidence. After the database ranks the matches, this method calls _hit for each row so callers receive standard Hit objects rather than raw database rows.

*Call graph*: calls 1 internal fn (_hit).


##### `DefaultIndex.vector`  (lines 284–317)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Finds chunks by comparing embedding similarity rather than matching literal words. This helps retrieve text that is close in meaning even when it uses different wording.

**Data flow**: It receives a query embedding, allowed subjects, an owner kind, and a result limit. If there is no embedding or no subject set, it returns no results. In PostgreSQL it sends the embedding to the database and lets pgvector score rows. In SQLite it reads all candidate embeddings, unpacks them, scores each one with cosine similarity, sorts them from best to worst, and returns the top hits.

**Call relations**: Search orchestration calls this for semantic retrieval. It uses pgvector_literal on the PostgreSQL path, and uses unpack_embedding plus cosine on the SQLite path. In both cases it calls _hit at the end to return normal Hit objects.

*Call graph*: calls 4 internal fn (_hit, cosine, pgvector_literal, unpack_embedding).


##### `manifest`  (lines 320–330)

```
def manifest() -> Manifest
```

**Purpose**: Announces this extension to the host system and registers the default index backend. This is how the rest of the project discovers that the backend named "default" can be created from this file.

**Data flow**: It creates a Manifest containing the extension name, version, and one IndexBackendSpec. That spec says the backend is named "default" and gives a factory function that builds a DefaultIndex using the transaction opener supplied by the host context. It returns the completed Manifest.

**Call relations**: The extension loading system calls this during setup. The returned manifest hands the host a factory, and later the host uses that factory to create DefaultIndex instances for real indexing and search work.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/turbopuffer/ufo_ext_turbopuffer.py`

`io_transport` · `startup and indexing/search request handling`

This extension is the bridge between UFO's internal memory index interface and Turbopuffer, an external search service. Without it, a workspace configured to use `memory.index_backend = "turbopuffer"` could not save chunks into Turbopuffer or search them back.

The file treats each workspace like its own labeled drawer in Turbopuffer, called a namespace. Each chunk is stored as a document with a stable id, its vector embedding, and useful labels such as owner kind, owner id, subject, position, and text. The vector supports meaning-based search, while the text field is configured for BM25, a keyword ranking method that scores text by matching important terms.

The main class, `TurbopufferIndex`, exposes the actions UFO expects from an index: add chunks, delete a whole scope, prune old chunks after re-chunking, check whether anything exists, and search by text or vector. It sends direct HTTP requests with a Bearer API key read from the workspace credentials. A few helper functions translate IDs, build request bodies, clean up keyword queries, and turn Turbopuffer response rows back into UFO `Hit` objects.

A useful analogy is a library catalog: this file decides how each page excerpt gets a catalog card, which shelf it belongs on, and how to ask the library for the best matching cards later.

#### Function details

##### `turbopuffer_id`  (lines 48–54)

```
def turbopuffer_id(chunk_digest: str) -> str
```

**Purpose**: Converts UFO's normal chunk digest into a shorter document id that Turbopuffer can store comfortably. It only rewrites standard SHA-256 digests; any other id is left as it is.

**Data flow**: It receives a chunk digest string. If the string looks like `sha256:` followed by a full hexadecimal hash, it decodes that hash into bytes and re-encodes it as shorter URL-safe Base64 without padding. It returns the shortened id, or the original string if it was not the expected digest form.

**Call relations**: When chunks are written, `upsert_body` uses this to choose the Turbopuffer document ids. When chunks are removed, `TurbopufferIndex.delete` and `TurbopufferIndex.prune` use the same conversion so the ids they ask Turbopuffer to delete match the ids that were originally stored.

*Call graph*: called by 3 (delete, prune, upsert_body); 1 external calls (urlsafe_b64encode).


##### `chunk_digest_from_id`  (lines 57–66)

```
def chunk_digest_from_id(chunk_id: str) -> str
```

**Purpose**: Turns a Turbopuffer document id back into the original UFO chunk digest when possible. This keeps search results using the same digest format the rest of UFO understands.

**Data flow**: It receives an id string from Turbopuffer. If it has the exact length of the shortened Base64 form, it tries to decode it and rebuild a `sha256:` digest. If decoding fails, or if the length does not match, it returns the id unchanged.

**Call relations**: Search and export responses come back from Turbopuffer with document ids. `hit_from_row` uses this helper when building search hits, and `TurbopufferIndex._scope_chunks` uses it when listing chunks for deletion or pruning.

*Call graph*: called by 2 (_scope_chunks, hit_from_row); 1 external calls (urlsafe_b64decode).


##### `upsert_body`  (lines 69–85)

```
def upsert_body(chunks: tuple[Chunk, ...]) -> dict[str, Any]
```

**Purpose**: Builds the JSON body used to add or replace a batch of chunks in Turbopuffer. It also tells Turbopuffer that vectors should use cosine distance and that the text field should support keyword search.

**Data flow**: It receives a tuple of `Chunk` objects. It turns them into parallel columns: ids, vectors, owner labels, subjects, order numbers, and text. It returns a dictionary ready to send as an HTTP JSON request.

**Call relations**: `TurbopufferIndex.upsert` calls this for each write batch. Inside the body-building step, it calls `turbopuffer_id` so stored document ids follow Turbopuffer's length expectations.

*Call graph*: calls 1 internal fn (turbopuffer_id); called by 1 (upsert).


##### `bm25_query`  (lines 88–104)

```
def bm25_query(text: str) -> str
```

**Purpose**: Cleans and shortens a text search query so Turbopuffer's keyword search can rank it safely. It drops text that has no useful searchable terms, such as only punctuation or single-letter fragments.

**Data flow**: It receives raw query text. It keeps only whitespace-separated tokens that contain at least one run of two or more letters or digits, then checks the encoded byte length. If the query is too long, it clips it to Turbopuffer's limit and avoids leaving a half term at the end when possible. It returns the cleaned query string, which may be empty.

**Call relations**: `TurbopufferIndex.lexical` calls this before making a BM25 keyword search. If the cleaned query is empty, lexical search stops there instead of asking Turbopuffer for a meaningless broad match.

*Call graph*: called by 1 (lexical).


##### `query_filters`  (lines 107–111)

```
def query_filters(owner_kind: str, subjects: frozenset[str]) -> list[Any]
```

**Purpose**: Builds the filter that keeps a search inside the intended part of memory. It limits results to one owner kind and to the allowed subject set.

**Data flow**: It receives an owner kind and a frozen set of subjects. It sorts the subjects and returns a Turbopuffer filter expression saying: owner kind must match, and subject must be one of these values.

**Call relations**: `TurbopufferIndex._query` uses this for both keyword and vector searches, so both search styles obey the same scope rules.

*Call graph*: called by 1 (_query).


##### `scope_filters`  (lines 114–121)

```
def scope_filters(scope: IndexScope, after_id: str | None) -> list[Any]
```

**Purpose**: Builds a filter for operations that target one specific indexed owner, such as deleting or checking its chunks. It can also continue after a previous id for page-by-page listing.

**Data flow**: It receives an `IndexScope`, which names an owner kind and owner id, plus an optional `after_id`. It returns a Turbopuffer filter expression for that owner. If `after_id` is present, it adds a condition that only ids greater than that value should be returned.

**Call relations**: `TurbopufferIndex.has_chunks` uses this for a quick existence check. `TurbopufferIndex._scope_chunks` uses it repeatedly while paging through all chunks in a scope.

*Call graph*: called by 2 (_scope_chunks, has_chunks).


##### `hit_from_row`  (lines 124–133)

```
def hit_from_row(row: dict[str, Any], score: float) -> Hit
```

**Purpose**: Converts one Turbopuffer result row into UFO's standard `Hit` object. A `Hit` is the common shape the rest of the system expects from any index backend.

**Data flow**: It receives a row dictionary from Turbopuffer and a score chosen by the caller. It converts the row's id back into a chunk digest, copies the owner labels, subject, ordinal, and text, and returns a new `Hit` with that score.

**Call relations**: `TurbopufferIndex.lexical` uses this after keyword search, and `TurbopufferIndex.vector` uses it after vector search. It calls `chunk_digest_from_id` so the hit points back to UFO's original chunk identity.

*Call graph*: calls 1 internal fn (chunk_digest_from_id); called by 2 (lexical, vector); 1 external calls (__init__).


##### `vector_score`  (lines 136–142)

```
def vector_score(row: dict[str, Any], position: int, total: int) -> float
```

**Purpose**: Turns Turbopuffer's vector result information into a score where higher means better. This gives the rest of the recall system a consistent way to compare vector hits.

**Data flow**: It receives a result row, that row's position in the returned list, and the total number of rows. If Turbopuffer included a cosine distance, it returns `1 - distance`. If not, it falls back to a simple descending rank score based on position.

**Call relations**: `TurbopufferIndex.vector` calls this for each vector search row before turning the row into a `Hit`. Rows with non-positive scores are not returned.

*Call graph*: called by 1 (vector).


##### `TurbopufferIndex.upsert`  (lines 156–167)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in Turbopuffer. This is how newly prepared memory chunks become searchable.

**Data flow**: It receives a tuple of chunks. It first discards chunks without embeddings, because Turbopuffer's vector index needs vectors. It reads the API key, splits the remaining chunks into batches, builds a JSON body for each batch, sends each body to the workspace namespace, and raises an error if Turbopuffer rejects a request. It returns nothing, but Turbopuffer is updated.

**Call relations**: The wider indexing flow calls this when it needs to store chunks. The method gets authorization through `_auth`, gets the namespace path through `_path`, and delegates request-body construction to `upsert_body`.

*Call graph*: calls 3 internal fn (_auth, _path, upsert_body).


##### `TurbopufferIndex.delete`  (lines 169–177)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes every indexed chunk belonging to a given scope. This is used when an owner should disappear from the index entirely.

**Data flow**: It receives an `IndexScope`. It reads the API key, lists all chunks currently in that scope, converts their digests to Turbopuffer ids, sends delete requests in batches, and raises an error if any delete request fails. The result is that those documents are removed from Turbopuffer.

**Call relations**: Delete operations in the index layer call this for full cleanup. It uses `_auth` for credentials, `_scope_chunks` to discover what needs deleting, `turbopuffer_id` to match stored ids, and `_path` to address the workspace namespace.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.prune`  (lines 179–189)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes only the chunks in a scope that are no longer in a given keep-set. This is useful after re-chunking, when new chunks replace old ones and leftovers must be cleaned up.

**Data flow**: It receives an `IndexScope` and a set of chunk digests to keep. It reads the API key, lists all chunks in the scope, filters out the chunks that should remain, converts the rest to Turbopuffer ids, and sends batched delete requests. It returns nothing, but outdated documents are removed.

**Call relations**: The indexing flow calls this when it wants to avoid orphaned chunks after updating an owner. It relies on `_scope_chunks` to see the current state, `turbopuffer_id` to form delete ids, `_auth` for the API key, and `_path` for the target namespace.

*Call graph*: calls 4 internal fn (_auth, _path, _scope_chunks, turbopuffer_id).


##### `TurbopufferIndex.has_chunks`  (lines 191–197)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a particular scope already has any chunks in Turbopuffer. This lets callers avoid unnecessary work or decide whether an owner is indexed.

**Data flow**: It receives an `IndexScope`. It builds a tiny query that asks for at most one id in that scope, sends it with authorization, and treats a missing namespace as empty. It returns `true` if at least one row comes back, otherwise `false`.

**Call relations**: Callers use this as a quick existence test. The method builds the scope filter with `scope_filters`, gets the API key through `_auth`, and sends the request to the path from `_path`.

*Call graph*: calls 3 internal fn (_auth, _path, scope_filters).


##### `TurbopufferIndex.lexical`  (lines 199–208)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a keyword-based search over stored chunk text. This is the BM25 side of recall, where matching words and terms matter.

**Data flow**: It receives raw query text, allowed subjects, an owner kind, and a result limit. It cleans the query with `bm25_query`; if there is no useful query or no subjects, it returns no hits. Otherwise it asks Turbopuffer to rank by text BM25, then converts each returned row into a `Hit` with a rank-based score.

**Call relations**: The recall/search layer calls this when it wants text matching. This method prepares the lexical query, delegates the HTTP search to `_query`, and uses `hit_from_row` to return results in UFO's common format.

*Call graph*: calls 3 internal fn (_query, bm25_query, hit_from_row).


##### `TurbopufferIndex.vector`  (lines 210–219)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a meaning-based search using an embedding vector. This finds chunks that are close in vector space, even if they do not share the exact same words.

**Data flow**: It receives an embedding, allowed subjects, an owner kind, and a result limit. If the embedding or subject set is empty, it returns no hits. Otherwise it asks Turbopuffer for approximate nearest neighbors, scores each row with `vector_score`, drops non-positive scores, and returns the remaining rows as `Hit` objects.

**Call relations**: The recall/search layer calls this for semantic search. It hands the actual HTTP query to `_query`, uses `vector_score` to translate distance into a useful score, and uses `hit_from_row` to produce standard hits.

*Call graph*: calls 3 internal fn (_query, hit_from_row, vector_score).


##### `TurbopufferIndex._query`  (lines 221–234)

```
async def _query(self, rank_by: list[Any], owner_kind: str, subjects: frozenset[str], limit: int) -> list[dict[str, Any]]
```

**Purpose**: Sends the shared Turbopuffer query request used by both keyword and vector search. It centralizes the repeated work of filtering, including attributes, and handling an empty namespace.

**Data flow**: It receives a Turbopuffer `rank_by` instruction, an owner kind, a subject set, and a limit. It builds a JSON query with those ranking rules, asks for the standard chunk attributes, adds the owner-and-subject filter, sends the request, and returns the response rows as a list. If Turbopuffer says the namespace does not exist, it returns an empty list.

**Call relations**: `TurbopufferIndex.lexical` and `TurbopufferIndex.vector` both call this after deciding how results should be ranked. It uses `query_filters` for scoping, `_auth` for authorization, and `_path` for the namespace URL.

*Call graph*: calls 3 internal fn (_auth, _path, query_filters); called by 2 (lexical, vector).


##### `TurbopufferIndex._scope_chunks`  (lines 236–264)

```
async def _scope_chunks(self, scope: IndexScope, headers: dict[str, str]) -> list[Chunk]
```

**Purpose**: Lists all chunks that belong to one scope, page by page. This is needed before delete and prune operations because those operations must know the document ids to remove.

**Data flow**: It receives an `IndexScope` and already-built HTTP headers. It repeatedly queries Turbopuffer for chunks in that scope, ordered by id, asking for one page at a time. Each row is converted into a lightweight `Chunk` object without an embedding. It stops when the namespace is missing or when the final page has fewer rows than the page size, then returns the collected chunks.

**Call relations**: `TurbopufferIndex.delete` and `TurbopufferIndex.prune` call this before deciding which ids to delete. It builds each page filter with `scope_filters`, uses `_path` for the query endpoint, and converts returned ids through `chunk_digest_from_id`.

*Call graph*: calls 3 internal fn (_path, chunk_digest_from_id, scope_filters); called by 2 (delete, prune); 1 external calls (__init__).


##### `TurbopufferIndex._auth`  (lines 266–268)

```
async def _auth(self) -> dict[str, str]
```

**Purpose**: Builds the HTTP authorization header for Turbopuffer requests. It reads the workspace's Turbopuffer API key at the moment the request is made.

**Data flow**: It asks the credential reader for the `turbopuffer_api_key` value. It then returns a dictionary containing an `Authorization` header in Bearer-token form.

**Call relations**: Most outward-facing methods call this before contacting Turbopuffer: `upsert`, `delete`, `prune`, `has_chunks`, and `_query`. `_scope_chunks` receives headers from its caller rather than fetching them itself.

*Call graph*: called by 5 (_query, delete, has_chunks, prune, upsert).


##### `TurbopufferIndex._path`  (lines 270–271)

```
def _path(self, suffix: str='') -> str
```

**Purpose**: Builds the Turbopuffer namespace path for the current workspace. This keeps each workspace's indexed chunks separated from the others.

**Data flow**: It receives an optional URL suffix, such as `/query`. It combines the fixed namespace prefix, the credential context's workspace id, and the suffix into one path string.

**Call relations**: Every method that sends a Turbopuffer request calls this directly or indirectly. It is used by writes, deletes, existence checks, searches, and scope listing so they all address the same workspace namespace.

*Call graph*: called by 6 (_query, _scope_chunks, delete, has_chunks, prune, upsert).


##### `manifest`  (lines 274–294)

```
def manifest() -> Manifest
```

**Purpose**: Registers this extension with UFO. It tells the host system that a Turbopuffer index backend exists and that it needs a Turbopuffer API key credential.

**Data flow**: It creates a manifest with the extension name and version, declares the credential slot called `turbopuffer_api_key`, and declares an index backend named `turbopuffer`. The backend factory builds a `TurbopufferIndex` with the runtime credential access object and an HTTP client pointed at Turbopuffer's API.

**Call relations**: The extension loading system calls this during startup. The manifest it returns is what lets core configuration select this backend and later construct `TurbopufferIndex` for indexing and search work.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Web search providers
Defines provider-neutral web search shapes and the Perplexity extension that implements them.

### `core/src/ufo/search.py`

`data_model` · `startup and request handling`

This file is a boundary, or “seam,” between the core application and any real web search provider. The core code does not contain a built-in search engine and does not keep provider API keys. Instead, an extension supplies a SearchProvider at startup, and the core talks to it through the simple types defined here.

The file describes the pieces of information that move across that boundary. SearchQuery is the request: the user’s search intent, how many results are wanted, optional date limits, allowed domains, and an optional category such as academic or image search. SearchResults is the reply: a ranked list of SearchHit items, plus possibly a direct answer if the provider can generate one. FetchRequest asks for the contents of one web page, and FetchedPage returns the extracted text and maybe a summary.

The SearchProvider protocol is the contract every search backend must follow. A protocol is like a checklist: any provider that has these methods can be used, no matter how it is implemented. This keeps sensitive work, like reading bring-your-own API keys and calling external services, on the host side. The sandboxed tool code only sees the safe interface.

#### Function details

##### `SearchProvider.supports_fetch`  (lines 82–82)

```
def supports_fetch(self) -> bool
```

**Purpose**: This property tells callers whether the selected search provider can fetch and extract the contents of a specific web page. It matters because some providers may only search and return links, while others can also read pages.

**Data flow**: A caller asks the provider for this true-or-false capability flag. The provider reports whether page fetching is available. Nothing else is changed; the result is used to decide whether a fetch request is safe to attempt.

**Call relations**: Research tools check this property before asking the provider to fetch a URL. If it says fetching is not supported, the tool should stop or choose another path instead of calling fetch and failing later.


##### `SearchProvider.search`  (lines 84–84)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This method is the standard way to run a web search through whatever provider was chosen at startup. Callers use it when they need ranked web results, and possibly a direct answer, without caring which outside service produced them.

**Data flow**: A SearchQuery goes in, carrying the search sentence and limits such as result count, dates, domains, or category. The provider sends that request to its own backend and turns the backend’s reply into SearchResults. The output is a consistent set of SearchHit records and maybe an answer.

**Call relations**: During a research turn, a tool gets the selected provider from the turn’s ToolContext and calls this method. The concrete extension backend does the external API work, then hands the normalized SearchResults back to the tool.


##### `SearchProvider.fetch`  (lines 86–86)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This method is the standard way to retrieve readable text from a single URL, when the provider supports page fetching. It is used after a caller has a page address and wants the page content, optionally guided by an extraction prompt.

**Data flow**: A FetchRequest goes in with a URL, optional prompt, optional character limit, and a flag saying whether to bypass cache. The provider fetches or re-fetches the page through its backend, extracts text, and returns a FetchedPage with the URL, text, and possibly a summary. The request itself is not changed.

**Call relations**: The research fetch_url tool calls this only after checking supports_fetch. The actual provider implementation performs the network/API work outside the sandbox and returns the cleaned page data to the core-facing tool flow.


### `extensions/perplexity/ufo_ext_perplexity.py`

`io_transport` · `request handling`

This file is an adapter: it translates UFO’s own search and page-fetch requests into the shape Perplexity expects, sends them over the network, and translates the replies back into UFO’s standard result objects. Without it, UFO could not use Perplexity as one of its search backends.

The main piece is `PerplexitySearchProvider`. Its `search` method takes a normal UFO search query, adds limits and optional filters such as domains or publication dates, calls Perplexity’s `/search` endpoint, checks that the answer has the expected shape, and returns a list of standard `SearchHit` items. Its `fetch` method uses the same Perplexity search endpoint in a more targeted way: it asks Perplexity for content from one exact URL, then checks that the returned result really matches that URL before producing a `FetchedPage`.

The file is careful about boundaries. It rejects overly long queries, prompts, and URLs before sending them. It caps result counts and token counts so requests stay within Perplexity’s limits. It also wraps bad HTTP responses, invalid JSON, and malformed Perplexity replies in a clear `PerplexityError`.

At the bottom, `manifest` tells UFO how to discover this extension: it declares the needed Perplexity API key credential and registers this provider under the Perplexity backend name.

#### Function details

##### `PerplexitySearchProvider.search`  (lines 72–85)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Runs a normal web search through Perplexity and returns results in UFO’s standard format. Someone uses this when they want search hits without caring which search engine is behind them.

**Data flow**: It receives a `SearchQuery` containing the user’s search text, desired result count, and optional filters. It turns that query into a Perplexity request body, sends it to Perplexity, validates the reply, and converts each returned item into a `SearchHit`. The output is a `SearchResults` object containing those hits.

**Call relations**: This is one of the public methods the UFO search system calls when the Perplexity backend is selected. It relies on `_search_body` to prepare the request, `_post` to send it over the network, and `_response` to make sure Perplexity’s reply can safely be read.

*Call graph*: calls 3 internal fn (_post, _response, _search_body); 2 external calls (__init__, __init__).


##### `PerplexitySearchProvider.fetch`  (lines 87–131)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Tries to extract text for one specific web page using Perplexity. It is used when UFO already has a URL and wants page content or a prompt-guided summary from that exact page.

**Data flow**: It receives a `FetchRequest` with a URL, optional extraction prompt, and optional character limit. It first checks that the URL, prompt, and limits are acceptable. Then it builds a Perplexity request restricted to the URL’s domain, sends it, validates the reply, and looks for a result whose canonicalized URL matches the requested page. If found, it trims the snippet to the requested size and returns a `FetchedPage`; if not, it raises a `PerplexityError`.

**Call relations**: This is the provider’s public page-fetch path. It uses `_post` for the API call, `_response` for reply validation, and `_canonical_page` to compare URLs fairly, for example treating a trailing slash or `.html` suffix as less important.

*Call graph*: calls 3 internal fn (_post, _response, _canonical_page); 3 external calls (__init__, __init__, urlsplit).


##### `PerplexitySearchProvider._search_body`  (lines 134–159)

```
def _search_body(query: SearchQuery) -> dict[str, Json]
```

**Purpose**: Builds the JSON request body that Perplexity expects for a search. It also enforces safe limits before any network call happens.

**Data flow**: It receives a UFO `SearchQuery`. It checks the requested number of results, combines the search text with an optional vertical hint such as academic, image, video, or shopping, adds filters for domains and dates when present, and calculates token limits. It returns a plain dictionary ready to send as JSON to Perplexity.

**Call relations**: `search` calls this before contacting Perplexity. When date filters are included, it hands each date to `_api_date` so Perplexity receives the date format it expects.

*Call graph*: calls 1 internal fn (_api_date); called by 1 (search); 1 external calls (__init__).


##### `PerplexitySearchProvider._response`  (lines 162–166)

```
def _response(payload: object) -> _PerplexitySearchResponse
```

**Purpose**: Checks that Perplexity’s reply has the result structure this adapter needs. This prevents later code from guessing at missing or wrongly shaped data.

**Data flow**: It receives a raw decoded response object, usually from JSON. It asks Pydantic, a data validation library, to confirm that the object contains a `results` list with items that have a URL, title, and snippet. It returns a typed response object if valid, or raises `PerplexityError` if the shape is wrong.

**Call relations**: Both `search` and `fetch` call this right after `_post` returns. It is the safety gate between untrusted external API data and the rest of the provider’s logic.

*Call graph*: called by 2 (fetch, search); 1 external calls (__init__).


##### `PerplexitySearchProvider._post`  (lines 168–188)

```
async def _post(self, body: dict[str, Json]) -> object
```

**Purpose**: Sends one HTTP POST request to Perplexity’s `/search` endpoint. It centralizes credential use, network setup, error reporting, and JSON parsing.

**Data flow**: It receives a JSON-ready request body. It asks the credential store for the Perplexity API key, creates an HTTP client pointed at `api.perplexity.ai`, sends the body with a bearer authorization header, and waits for the response. If Perplexity reports an error or returns invalid JSON, it raises `PerplexityError`; otherwise it returns the decoded JSON object.

**Call relations**: `search` and `fetch` both use this as their only network doorway. That means the details of headers, timeouts, API host, and HTTP error handling stay in one place instead of being repeated.

*Call graph*: called by 2 (fetch, search); 2 external calls (__init__, AsyncClient).


##### `_api_date`  (lines 191–192)

```
def _api_date(value: date) -> str
```

**Purpose**: Formats a Python date into the date string format Perplexity expects. It is a small translation helper for publication-date filters.

**Data flow**: It receives a `date` value. It formats it as month/day/year, such as `03/09/2025`, and returns that string.

**Call relations**: `_search_body` calls this when a search query includes start or end publication dates, so those filters are sent in Perplexity’s required format.

*Call graph*: called by 1 (_search_body); 1 external calls (strftime).


##### `_canonical_page`  (lines 195–202)

```
def _canonical_page(value: str) -> tuple[str | None, str, str]
```

**Purpose**: Turns a URL into a simpler comparison key so the fetch logic can tell whether two URLs point to the same page. This helps avoid false mismatches caused by small URL formatting differences.

**Data flow**: It receives a URL string. It splits out the host name, path, and query string, removes a trailing slash from the path, and strips common page suffixes like `.html`, `.htm`, or `.txt`. It returns those pieces as a tuple for comparison.

**Call relations**: `fetch` uses this on both the requested URL and Perplexity’s returned URLs. The goal is to confirm that Perplexity returned the page the caller asked for, not just another page from the same site.

*Call graph*: called by 1 (fetch); 1 external calls (urlsplit).


##### `manifest`  (lines 205–225)

```
def manifest() -> Manifest
```

**Purpose**: Describes this extension to UFO so it can be discovered and used. It declares the Perplexity API key requirement and registers the Perplexity search provider.

**Data flow**: It takes no input. It creates a manifest containing the extension name, version, one credential slot for the Perplexity API key, and a search-provider registration that knows how to build a `PerplexitySearchProvider`. It returns that `Manifest` to the host system.

**Call relations**: UFO’s extension-loading code calls this during discovery or startup. The returned manifest is how the host learns both what secret it must provide and how to create the provider when someone selects the Perplexity backend.

*Call graph*: 3 external calls (__init__, __init__, __init__).

## 📊 State Registers Touched

- `reg-effective-config` — The current trusted settings for how the service should run, including database, provider, deployment, and safety options.
- `reg-model-catalog` — The shared list of available AI models, their abilities, prices, limits, and required credentials.
- `reg-search-indexes` — The shared searchable indexes and embeddings that let turns, research tools, and memory lookup find relevant text.
- `reg-memory-store` — The long-term saved facts, profiles, notes, and summaries that can be recalled in later conversations.
