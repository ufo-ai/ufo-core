# Provider and backend registration  `stage-3.1`

This stage is part of the system’s plug-in setup. When UFO starts, these extension files tell the main program which outside services it can use, and how to connect to them. A “backend” here means a replaceable service that does one job, like running a model, making search embeddings, or passing live messages between servers.

The Bedrock extension registers Amazon Bedrock Mantle as a model source. It lists available Anthropic and OpenAI-compatible models, their costs, required credentials, and the client code needed to call them. The OpenAI embedding extension registers a service that turns text into numeric vectors, which are number lists used for search, and it splits large requests so they stay within safe limits.

The Redis hub package provides shared live communication. Its package marker only makes the code importable. Its manifest announces two optional Redis backends to the main system. The stream hub acts like a live notice board for turn updates, text chunks, costs, and completion messages. The stream terminal connects terminal users to remote work, using Redis for routing messages and blob storage for larger data.

## Files in this stage

### Redis hub registration
Package and manifest files that make the Redis live hub extension importable and register its optional backends.

### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a nameplate on a folder: it tells Python that the folder should be treated as an importable package. This particular file is empty, so it does not run setup code, expose shortcuts, or define shared values. Its main value is structural. Without it, some tools or older Python import behavior might not recognize `extensions/redis_hub/ufo_ext_redis_hub` as a package, which could make imports fail or behave inconsistently. The actual Redis hub extension logic lives in other files inside this package; this file simply makes the package visible and usable to the rest of the project.


### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup / extension discovery`

This file is the extension’s sign-up sheet. When the larger application looks for available extensions, this manifest says: “I provide a hub backend named redis, and a terminal transport named redis.” A hub is the part of the system that distributes live frames or messages. Redis is an external data store and message system often used so several server processes can share information. Without this file, the Redis hub code could exist on disk, but the main system would not know how to select or build it.

The important shared setting is config.hub.url, a Redis connection address such as redis://host:6379/0. Both Redis features use that same URL. The file deliberately checks for a missing URL as soon as the Redis backend is selected. That means configuration mistakes fail early with a clear message, instead of causing a confusing crash later during live traffic.

There are two small builder functions. One creates a RedisStreamHub, which lets multiple server instances fan out frames through Redis Streams. The other creates RedisTerminals, which lets terminal connections be reached even when a request lands on a different pod or server. The manifest() function packages those builders into SDK spec objects so the core application can discover and use them.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed live-frame hub. It is used when the application configuration selects hub.backend = "redis".

**Data flow**: It receives a Redis URL, or possibly no URL. If the URL is missing, it stops immediately with a clear error explaining that hub.url is required. If the URL is present, it passes that URL into RedisStreamHub and returns the newly created hub object.

**Call relations**: This builder is handed to the core system through the HubSpec created in manifest(). When the Redis hub backend is chosen, the system calls this builder, and the builder hands off the real work to RedisStreamHub.__init__, which sets up the Redis Streams hub.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: This function creates the Redis-backed terminal transport. It is used when the application configuration selects terminal.backend = "redis".

**Data flow**: It receives a Redis URL and a BlobStore, which is the storage service used for larger shared data. If the URL is missing, it raises a clear error before anything starts running. If the URL is present, it gives the URL and blob store to RedisTerminals and returns the created terminal transport.

**Call relations**: This builder is registered inside the TerminalTransportSpec created by manifest(). When the Redis terminal transport is selected, the system calls this builder, which then calls RedisTerminals.__init__ to create the transport that can route terminal activity through Redis and shared blob storage.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension manifest, which is the object the main system reads to learn what this extension offers. It names the extension, gives its version, and registers the Redis hub and Redis terminal transport builders.

**Data flow**: It uses the file’s constants for the extension name, version, and backend names. It wraps _build_hub in a HubSpec and _build_terminal in a TerminalTransportSpec, then places both specs into a Manifest object. The returned Manifest is the finished description of this extension’s capabilities.

**Call relations**: During extension discovery, the core system calls manifest() to collect available backends. This function creates HubSpec, TerminalTransportSpec, and Manifest objects, which connect the plain backend names like "redis" to the builder functions that know how to create the actual Redis components.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Model and embedding providers
Extensions that register external AI backends for Bedrock-hosted models and OpenAI-powered embeddings.

### `extensions/bedrock/ufo_ext_bedrock.py`

`config` · `startup / provider discovery`

This file is like a catalog page plus a set of plug adapters for Amazon Bedrock Mantle. The catalog lists the available model IDs, their prices, context window sizes, knowledge cutoff dates, and whether they support reasoning features. The plug adapters know how to connect those model entries to the right underlying API client.

Bedrock Mantle exposes two kinds of models here. Anthropic model IDs use Anthropic’s Bedrock Mantle client. OpenAI-compatible model IDs use an OpenAI-style client, but pointed at Amazon’s Bedrock Mantle endpoint instead of OpenAI’s servers. The file does not translate requests itself. It relies on the core UFO SDK clients to format chat or responses requests correctly.

A Bedrock region is required because Amazon endpoints are regional, like choosing which branch office to call. The file reads that region from AWS_REGION or AWS_DEFAULT_REGION and fails early if neither is set. All models use the same credential slot, backed by the AWS_BEARER_TOKEN_BEDROCK environment variable.

At the end, manifest() packages this information into a Manifest object. That manifest is what the wider system reads to discover that this provider exists and which models it can offer.

#### Function details

##### `bedrock_region`  (lines 46–52)

```
def bedrock_region() -> str
```

**Purpose**: This function finds the AWS region that Bedrock Mantle should use. It prevents the system from making a vague or invalid API call by requiring AWS_REGION or AWS_DEFAULT_REGION to be set.

**Data flow**: It reads the process environment and first looks for AWS_REGION, then AWS_DEFAULT_REGION. If it finds a value, it returns that region name. If neither value exists, it raises an error explaining which environment variables must be set.

**Call relations**: When either kind of Bedrock client is being built, _anthropic_client and _openai_client call this function first. The returned region is then used to create the correct regional Bedrock Mantle endpoint.

*Call graph*: called by 2 (_anthropic_client, _openai_client).


##### `_anthropic_client`  (lines 55–67)

```
def _anthropic_client(spec: ModelSpec, key: str) -> AnthropicClient
```

**Purpose**: This function builds the runtime client used for Anthropic models served through Bedrock Mantle. Someone uses it indirectly when a ModelSpec for an Anthropic model needs a real connection object to make requests.

**Data flow**: It receives a model description and an API key. It asks bedrock_region for the AWS region, creates an Anthropic Bedrock Mantle async client with that key, region, timeout, and no automatic retries, then wraps it in the UFO SDK’s AnthropicClient along with the model description. The result is a ready-to-use AnthropicClient.

**Call relations**: The _anthropic helper stores this function inside each Anthropic ModelSpec as that model’s client builder. Later, when the broader system wants to call one of those models, it invokes this builder; the builder gets the region from bedrock_region and hands the finished low-level client to AnthropicClient.

*Call graph*: calls 1 internal fn (bedrock_region); 3 external calls (__init__, AsyncAnthropicBedrockMantle, cast).


##### `_openai_client`  (lines 70–77)

```
def _openai_client(spec: ModelSpec, key: str) -> OpenAIClient
```

**Purpose**: This function builds the runtime client used for OpenAI-compatible models served through Bedrock Mantle. It chooses the right Bedrock Mantle URL depending on whether the model uses the chat API or the newer responses API.

**Data flow**: It receives a model description and an API key. It reads the AWS region through bedrock_region, builds a base URL for either the OpenAI-compatible chat endpoint or responses endpoint, creates an OpenAI SDK client pointed at that URL, and wraps it in the UFO SDK’s OpenAIClient. The result is a ready-to-use OpenAIClient.

**Call relations**: The _openai helper stores this function inside each OpenAI-style ModelSpec as that model’s client builder. When the broader system later needs to call one of those models, this function creates the correctly targeted OpenAI-compatible client.

*Call graph*: calls 1 internal fn (bedrock_region); 2 external calls (__init__, openai_sdk_client).


##### `_anthropic`  (lines 80–99)

```
def _anthropic(id: str, price: ModelPrice, cutoff: str, *, context_window: int=ANTHROPIC_CONTEXT_WINDOW, reasoning: ReasoningSupport=REASONS) -> ModelSpec
```

**Purpose**: This helper creates a ModelSpec for an Anthropic model on Bedrock Mantle. A ModelSpec is the system’s standard description of one model: its ID, provider, price, context size, reasoning behavior, credential source, and client builder.

**Data flow**: It receives the model ID, pricing information, knowledge cutoff date, and optional context window and reasoning settings. It fills in the Bedrock provider name, Anthropic client builder, API surface, credential slot, and API key environment variable. It returns a complete ModelSpec that can be placed in the provider’s model list.

**Call relations**: This function is used while the module is loaded to build the Anthropic entries in BEDROCK_MODEL_SPECS. Those specs later become part of the Manifest returned by manifest().

*Call graph*: 1 external calls (__init__).


##### `_openai`  (lines 102–116)

```
def _openai(id: str, price: ModelPrice, cutoff: str, window: int, api_surface: ApiSurface) -> ModelSpec
```

**Purpose**: This helper creates a ModelSpec for an OpenAI-compatible model on Bedrock Mantle. It gives the larger system all the facts needed to price, display, and call that model.

**Data flow**: It receives the model ID, pricing information, knowledge cutoff date, context window size, and API surface name. It combines those with the Bedrock provider name, OpenAI client builder, reasoning support, credential slot, and API key environment variable. It returns a complete ModelSpec ready for the provider’s model list.

**Call relations**: This function is used during module loading to build the OpenAI-compatible entries in BEDROCK_MODEL_SPECS. Those entries are later included in the Manifest returned by manifest(), so the rest of the system can discover and use them.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 204–215)

```
def manifest() -> Manifest
```

**Purpose**: This function publishes the Bedrock extension to the UFO system. It returns the provider’s name, version, needed credential, and full list of supported models in one standard package.

**Data flow**: It creates a CredentialSlot describing the Bedrock API key requirement, then creates and returns a Manifest containing the extension name, version, credential slot, and BEDROCK_MODEL_SPECS. It does not make network calls; it only reports configuration and model metadata.

**Call relations**: The wider plugin or extension loader calls manifest() when it wants to discover what this file contributes. manifest() hands back the complete Manifest, built from the model specs created earlier by _anthropic and _openai.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/embed_openai/ufo_ext_embed_openai.py`

`io_transport` · `startup registration and background embedding work`

This file exists because the system needs a standard way to convert pieces of text into embeddings: long lists of numbers that capture meaning well enough for search and comparison. It uses OpenAI’s `text-embedding-3-large` model for that job, and registers it under the backend name `default`, so other parts of the system can use embeddings without caring which provider is behind them.

A key detail is that the OpenAI API key is not read when the program starts. Instead, it is read each time an embedding request is made. That means a local development server can start even if no key is configured, but the actual embedding call will fail clearly if the key is missing.

The file also protects the OpenAI request from becoming too large. Before sending text to OpenAI, it clips each individual text item to a maximum length, then groups items into batches that stay under both an item count limit and a character count limit. Think of it like packing boxes for shipping: each object is trimmed to fit, and each box has both a maximum number of objects and a maximum total weight.

Finally, `manifest` tells the extension system what this plugin is called, which deploy-time secret it needs, and how to build the embedding client.

#### Function details

##### `plan_embed_batches`  (lines 33–49)

```
def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]
```

**Purpose**: This function prepares text for OpenAI by making sure no single request is too large. It trims overly long text items and groups the remaining text into batches that fit the configured limits.

**Data flow**: It receives a tuple of text strings. For each string, it keeps only the allowed number of characters, then adds it to the current batch unless doing so would exceed the maximum number of items or total characters. It returns a tuple of batches, where each batch is a tuple of clipped strings ready to send to the embedding provider.

**Call relations**: When `OpenAIEmbedClient.embed` needs to send text to OpenAI, it first asks this function to split the input into safe chunks. The embed method then sends each planned batch one at a time.

*Call graph*: called by 1 (embed).


##### `OpenAIEmbedClient.embed`  (lines 63–75)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This asynchronous method turns text into embeddings using OpenAI. Someone would use it when they need machine-readable vectors for search, indexing, or meaning-based comparison.

**Data flow**: It receives a tuple of text strings. It reads the deploy API key from the environment, fails with a clear error if no key is available, creates an asynchronous OpenAI client, splits the text into safe batches, sends each batch to OpenAI, sorts the returned rows back into the original order, and returns a tuple of embedding vectors as floats.

**Call relations**: This is the main working method of the embedding client built by `build`. During an embedding job, it calls `deploy_env` to find the API key, uses `plan_embed_batches` to keep requests within limits, and hands each batch to `openai.AsyncOpenAI` for the actual network call.

*Call graph*: calls 1 internal fn (plan_embed_batches); 2 external calls (AsyncOpenAI, deploy_env).


##### `build`  (lines 78–83)

```
def build(ctx: ExtensionContext) -> EmbedClient
```

**Purpose**: This function creates the embedding client that the core system will use. It deliberately does not require an OpenAI key at construction time, so the program can start even before embedding is actually needed.

**Data flow**: It receives an `ExtensionContext`, which represents the surrounding extension/workspace setup, but this backend does not read anything from it. It returns a new `OpenAIEmbedClient` instance.

**Call relations**: The extension system uses this function as the factory named in `manifest`. When the default embedding backend is selected, the core calls `build`, receives an `OpenAIEmbedClient`, and later calls that client’s `embed` method when text must be embedded.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 86–92)

```
def manifest() -> Manifest
```

**Purpose**: This function describes the extension to the host system. It tells the system the extension’s name and version, which environment key it needs, and which embedding backend it provides.

**Data flow**: It takes no input. It creates an `EmbedBackendSpec` for the backend named `default`, pointing to `build` as the way to construct it, then wraps that in a `Manifest` along with the extension name, version, and required deploy key. It returns that manifest.

**Call relations**: At extension discovery or startup, the host calls `manifest` to learn what this file contributes. The returned manifest lets the host register `build` as the factory for the default embedding backend.

*Call graph*: 2 external calls (__init__, __init__).


### Redis stream transports
Runtime Redis transport implementations for sharing live frame updates and routing terminal traffic across server processes.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `request handling and live streaming`

When an agent is producing an answer, the user interface needs small live updates, not just the final saved result. This file sends those updates through Redis Streams, which are append-only lists kept in Redis, a fast shared memory service. Each turn gets its own stream, like a dedicated message lane. A publisher adds frames to that lane, and subscribers read from a saved cursor so they can reconnect and continue without repeating or missing retained messages.

The file deliberately treats these frames as temporary. Redis keeps only a limited number of entries and expires quiet streams after a day. That is safe because the final answer is stored elsewhere; losing a live text fragment only means the display may redraw from durable state, not that the turn becomes wrong.

A key detail is that asynchronous Redis clients are tied to the event loop that created them. An event loop is the scheduler that runs async tasks. Because this project may publish from one loop and serve subscribers from another, RedisStreamHub keeps a separate Redis client per loop.

The file also translates between internal frame objects and a small JSON “wire” format. That way Redis stores simple text, while subscribers reconstruct the right kind of frame when reading it back.

#### Function details

##### `frame_payload`  (lines 78–84)

```
def frame_payload(frame: HubFrame) -> dict[str, object]
```

**Purpose**: Turns one live frame object into a simple dictionary that can be written to Redis as JSON. It adds a kind label so a later reader knows what type of frame to rebuild.

**Data flow**: It receives a HubFrame, such as a text delta or terminal marker. It extracts the frame’s fields into JSON-friendly data and pairs them with a kind name. It returns that dictionary, ready to be encoded and stored.

**Call relations**: RedisStreamHub.publish calls this just before writing a frame into a Redis stream. For normal frames it uses the frame’s own data dump; for Activity frames it writes a compatible tool-activity shaped record so older or external readers can understand the activity-style message.

*Call graph*: called by 1 (publish); 2 external calls (__init__, model_dump).


##### `frame_from_payload`  (lines 87–97)

```
def frame_from_payload(payload: dict[str, object]) -> HubFrame
```

**Purpose**: Rebuilds a live frame object from the dictionary form read out of Redis. This is the reverse of frame_payload.

**Data flow**: It receives a payload containing a kind label and data. It checks the kind, validates the data against the matching frame shape, and returns the reconstructed HubFrame. Special activity formats, such as tool activity or skill loading, are turned back into Activity messages.

**Call relations**: RedisStreamHub.subscribe uses this when streaming messages to a caller, and RedisStreamHub.latest_activity uses it when it finds an activity entry while scanning recent stream entries. It is the bridge from Redis’s plain JSON back into the project’s typed live-frame objects.

*Call graph*: called by 2 (latest_activity, subscribe); 2 external calls (__init__, cast).


##### `_stream_id`  (lines 100–102)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis stream entry ID into numbers that can be compared reliably. Redis IDs look like a timestamp plus a sequence number, such as “1710000000000-2”.

**Data flow**: It receives an entry ID string. It splits it into the millisecond timestamp part and the sequence part, converts both to integers, and returns them as a pair. The result can be compared with another pair to decide which stream entry came first.

**Call relations**: RedisStreamHub.covers uses this helper when deciding whether a saved cursor still points into the part of the stream Redis has retained. It keeps that comparison small and explicit.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 105–114)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from Redis’s xread response and checks that the response has the expected shape. This prevents the code from silently misreading a Redis reply.

**Data flow**: It receives the raw batch returned by Redis xread. If the batch is empty, it returns an empty list. If the response is not the expected list form, it raises an error. Otherwise it returns the entries for the stream.

**Call relations**: RedisStreamHub.subscribe calls this after each xread. It acts like an unpacking step between Redis’s transport format and the loop that yields individual live frames to subscribers.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 130–136)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client that is safe to use on the current async event loop. If this loop has not used Redis yet, it creates and remembers a new client for it.

**Data flow**: It reads the currently running event loop and looks for a cached Redis client tied to that loop. If one exists, it returns it. If not, it creates a client from the configured Redis URL, stores it under that loop, and returns it.

**Call relations**: All RedisStreamHub operations call this before talking to Redis. This matters because publish, subscribe, covers, and latest_activity may run on different event loops, and sharing one async Redis client across loops can break async scheduling.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 138–139)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name for a turn. It gives every turn its own message lane.

**Data flow**: It receives a turn UUID. It combines the fixed stream prefix with that UUID and returns the Redis key string.

**Call relations**: Publishers, subscribers, cursor checks, and activity lookups all call this before reading or writing Redis. It keeps the naming rule in one place so every operation talks about the same stream for the same turn.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 141–148)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: Adds one live frame to the Redis stream for a turn and returns the Redis cursor for that new entry. Callers use this to broadcast real-time updates to any process that is listening.

**Data flow**: It receives a turn ID and a HubFrame. It turns the frame into JSON-ready payload data, encodes that as compact JSON, writes it to the turn’s Redis stream, trims the stream to a bounded size, refreshes the stream’s expiry time, and returns the new entry ID.

**Call relations**: This is the sending side of the hub. It relies on _stream to choose the Redis key, _client to get a loop-safe Redis connection, and frame_payload to serialize the frame. Redis then makes the entry available to RedisStreamHub.subscribe and to latest_activity scans.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 150–174)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: Continuously reads live frames for a turn from Redis, starting after a given cursor. It lets a surface replay retained updates and then wait for new ones.

**Data flow**: It receives a turn ID and an optional cursor. It chooses the turn’s stream and starts reading after the cursor, or from the beginning if no cursor was given. It first tries a normal read, then a blocking read that waits briefly for new data. Each Redis entry is decoded from JSON, rebuilt into a HubFrame, and yielded with its new cursor.

**Call relations**: This is the receiving side of the hub. It uses _client and _stream to read the right Redis lane, _stream_entries to unpack Redis responses, and frame_from_payload to rebuild frame objects. Timeouts are treated as normal quiet periods, so the loop simply tries again without losing its place.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 176–182)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether Redis still has enough stream history to resume from a saved cursor. This tells a reconnecting reader whether it can continue smoothly or should redraw from durable state.

**Data flow**: It receives a turn ID and a cursor. If the cursor is empty, it returns false. Otherwise it reads the first retained entry in the turn’s stream. If the stream is gone or empty, it returns false. If the first retained ID is less than or equal to the cursor, it returns true, meaning the cursor is still covered by the stream.

**Call relations**: Reconnect logic can call this before subscribing from an old cursor. It uses _stream to find the Redis key, _client to query Redis, and _stream_id to compare Redis entry IDs correctly.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


##### `RedisStreamHub.latest_activity`  (lines 184–216)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Finds the newest recent activity message for a turn, such as a tool call or skill load. This gives callers a quick “what is it doing right now?” answer without scanning the whole stream.

**Data flow**: It receives a turn ID. It reads recent Redis stream entries newest-first, in bounded pages, up to a fixed maximum. For each entry, it decodes only enough JSON to check the kind. If it finds an activity kind, it rebuilds and returns that Activity. If the stream is empty or no recent activity is found, it returns None.

**Call relations**: Status polling code can use this alongside the live stream to show current activity. It uses _stream and _client to read Redis, json.loads to inspect stored frames, and frame_from_payload only when it has found an activity entry worth rebuilding.

*Call graph*: calls 3 internal fn (_client, _stream, frame_from_payload); 2 external calls (loads, cast).


### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `cross-pod terminal request handling`

In a single process, sending a command to a user’s terminal can be as simple as keeping a local queue. In a fleet of pods, that breaks: the web request holding the terminal connection may be on one pod, while the workflow that wants to run a terminal operation may be on another. This file is the meeting place, like a numbered pickup counter shared by all pods. Redis stores short-lived bindings that say “conversation X currently has a terminal at this workspace,” operation streams that carry requests, reply streams that carry answers, and locks that make sure only one operation runs per conversation at a time. The blob store carries larger byte payloads, such as input bodies or large replies, so Redis is not used as bulk storage. The main class, RedisTerminals, publishes terminal presence when a client connects, waits briefly for that presence when a workflow wants to send an operation, writes the operation into Redis, and waits for a reply until a strict deadline. The terminal-serving side calls next_op to claim exactly one pending operation, so reconnects do not accidentally run the same command twice. Most keys have time-to-live expiry, so if a pod dies mid-operation, the system eventually cleans itself up instead of leaving future turns stuck forever.

#### Function details

##### `_text`  (lines 55–58)

```
def _text(value: bytes | str) -> str
```

**Purpose**: Converts a Redis field value into normal Python text. Redis may give back bytes or text depending on client settings, and the rest of this file wants one consistent form.

**Data flow**: It receives one Redis value, either bytes or a string. If it is bytes, it decodes it into a string; if it is already a string, it returns it unchanged.

**Call relations**: Small parsing helpers and RedisTerminals methods call this whenever they read Redis fields. It keeps decoding details out of higher-level code such as operation decoding, reply decoding, binding reads, and safety checks.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 61–66)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: Turns Redis’s flat field list into an easy-to-use field dictionary. This is needed because the Lua script returns stream fields as alternating names and values.

**Data flow**: It receives a flat list like field, value, field, value. It converts each item to text and groups neighboring items into a dictionary; if the input is not a list, it raises an error.

**Call relations**: RedisTerminals.next_op uses this after the Redis Lua script claims an operation. _pairs relies on _text so the claimed operation can then be decoded cleanly.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 69–74)

```
def _bind_payload(cwd: str, member_id: UUID | None, runtime_id: str) -> str
```

**Purpose**: Builds the small JSON record that says where a terminal is and who it belongs to. Both the live binding and the in-flight binding pin use this same shape.

**Data flow**: It receives a working directory, an optional member ID, and a runtime ID. It serializes them into a JSON string suitable for storing in Redis.

**Call relations**: RedisTerminals._heartbeat uses it to publish live terminal presence. RedisTerminals._run_op uses it again to pin the binding while an operation is running.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 148–157)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts actual stream entries from the Redis XREAD response format this code expects. It also fails loudly if Redis returns an unexpected shape.

**Data flow**: It receives the raw XREAD response. Empty responses become an empty list; valid responses return the entries for the first stream; invalid response shapes raise a type error.

**Call relations**: RedisTerminals._await_reply calls this while waiting for a terminal answer. This helper protects the reply wait from accidentally misreading Redis protocol structure.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 185–202)

```
def _client(self) -> Redis
```

**Purpose**: Returns a Redis client tied to the currently running async event loop. This matters because asyncio clients should not be freely shared across different loops.

**Data flow**: It reads the current event loop and checks whether this RedisTerminals object already has a client for it. If not, it creates one with bounded socket timeouts, stores it, and returns it.

**Call relations**: Nearly every Redis-using method calls this before reading or writing keys and streams. It is the common doorway to Redis for sending operations, receiving replies, heartbeats, cleanup, and staged body access.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 204–205)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores the live terminal binding for a conversation. This key is refreshed while a terminal connection is held.

**Data flow**: It receives a conversation ID and returns the Redis key string for that conversation’s binding.

**Call relations**: RedisTerminals._heartbeat writes this key, and RedisTerminals._read_binding reads it first when looking for a connected terminal.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 207–208)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores a temporary binding while an operation is already running. This lets other requests still know the terminal exists even after the held stream has handed off work.

**Data flow**: It receives a conversation ID and returns the Redis key string for that conversation’s in-flight binding.

**Call relations**: RedisTerminals._run_op writes this key, RedisTerminals._read_binding falls back to it, and RedisTerminals._clear_op deletes it during cleanup.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 210–211)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name where terminal operations for one conversation are posted. A Redis Stream is an append-only message log that readers can wait on.

**Data flow**: It receives a conversation ID and returns the stream key for operations in that conversation.

**Call relations**: RedisTerminals._run_op appends operations to this stream, RedisTerminals.next_op reads and claims from it, and RedisTerminals._clear_op removes completed entries.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 213–214)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: Builds the Redis Stream name where the reply for one operation is posted. Each operation gets its own reply stream.

**Data flow**: It receives an operation ID and returns the reply stream key for that operation.

**Call relations**: RedisTerminals._await_reply waits on this stream, RedisTerminals._deliver_reply writes to it, and RedisTerminals._clear_op removes it afterward.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 216–217)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key for the per-conversation lock. The lock makes operations run one at a time for the same conversation.

**Data flow**: It receives a conversation ID and returns the Redis key string used for locking that conversation.

**Call relations**: RedisTerminals.send uses this key before posting an operation, so two callers do not ask the same terminal to do overlapping work.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 219–220)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key used as a delivery marker for an operation. This marker prevents reconnecting terminal streams from delivering the same operation twice.

**Data flow**: It receives an operation ID and returns the marker key string.

**Call relations**: The Lua script in Redis creates these marker keys while RedisTerminals.next_op is claiming work. RedisTerminals._clear_op removes the marker during cleanup.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 222–223)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key that stores operation metadata, such as which conversation and member the operation belongs to. This is used as a safety gate for replies and staged body reads.

**Data flow**: It receives an operation ID and returns the metadata key string.

**Call relations**: RedisTerminals._run_op writes this metadata, RedisTerminals.staged and RedisTerminals._deliver_reply check it, and RedisTerminals._clear_op deletes it.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 225–226)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for an operation’s input body. The blob store is used for larger raw bytes instead of putting them directly into Redis.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s staged input body.

**Call relations**: RedisTerminals._run_op writes this blob when there is a body, RedisTerminals.staged reads it for the terminal side, and RedisTerminals._clear_op deletes it.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 228–229)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for a large operation reply. Small replies go directly through Redis, but large replies are stored as blobs.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s large reply body.

**Call relations**: RedisTerminals._deliver_reply writes this blob for large replies, RedisTerminals._decode_reply reads it, and RedisTerminals._clear_op deletes it.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 231–253)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that this pod currently holds a terminal connection for a conversation. It starts or shares a heartbeat that keeps the Redis binding alive.

**Data flow**: It receives the conversation, working directory, optional member ID, and optional runtime ID. It updates local hold state under a lock, starts a background heartbeat if this is the first local connection, and increments the connection count.

**Call relations**: Terminal-serving code calls this when a held terminal stream begins. It creates the RedisTerminals._heartbeat task that other pods later discover through arrived and send.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 255–264)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Notes that one local terminal connection has ended. When the last local connection leaves, it stops the heartbeat instead of deleting the Redis binding immediately.

**Data flow**: It receives a conversation ID, finds the local hold, decrements its connection count, and cancels the heartbeat when no connections remain.

**Call relations**: Terminal-serving code calls this when a held stream ends. The choice not to delete the Redis key lets a reconnecting client avoid a race where an old pod erases a newer pod’s binding.


##### `RedisTerminals._heartbeat`  (lines 266–280)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Keeps the Redis binding fresh while this pod holds a terminal connection. It repeatedly rewrites the binding with a time-to-live, like renewing a parking meter.

**Data flow**: It receives the conversation and binding details. It builds the JSON payload, writes it to Redis with an expiry time, sleeps, and repeats until cancelled.

**Call relations**: RedisTerminals.connect starts this as a background task. RedisTerminals._read_binding later sees the key it refreshes, allowing RedisTerminals.arrived and send to find the terminal from another pod.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 282–295)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the terminal workspace known locally on this pod, without contacting Redis. It is a quick local lookup for the pod that actually holds the connection.

**Data flow**: It receives a conversation ID and checks local hold state under a lock. If present, it returns a TerminalWorkspace with directory, member, and runtime; otherwise it returns None.

**Call relations**: This is the local counterpart to RedisTerminals.arrived. It does not take part in the cross-pod Redis wait; it answers only from this pod’s memory.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 297–310)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal binding to appear in Redis. This covers normal reconnect gaps where the terminal exists but has not republished its binding yet.

**Data flow**: It receives a conversation ID and a grace period in seconds. It repeatedly calls _read_binding until it finds a workspace or the deadline passes, then returns the workspace or None.

**Call relations**: RedisTerminals.send calls this before trying to post an operation. It hands send either a usable TerminalWorkspace or proof that no terminal showed up in time.

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 312–330)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Reads the terminal binding from Redis and turns it into a TerminalWorkspace. It checks both the live binding and the in-flight pin.

**Data flow**: It receives a conversation ID, reads the live binding key, then the in-flight key if needed. If JSON is found, it parses the directory, member, and runtime and returns a workspace; if not, it returns None.

**Call relations**: RedisTerminals.arrived calls this while waiting for terminal presence. The keys it reads are written by RedisTerminals._heartbeat and RedisTerminals._run_op.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 332–388)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one operation to a conversation’s terminal and waits for the reply. It enforces deadlines and turns Redis problems into terminal-level errors.

**Data flow**: It receives operation details and optional body bytes. It waits for a terminal binding, creates an operation ID, acquires a per-conversation Redis lock, runs the operation flow, returns reply bytes, and releases the lock.

**Call relations**: This is the main workflow-side entry for using a terminal. It calls arrived, then delegates the post-lock work to RedisTerminals._run_op, which eventually waits through _await_reply.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 6 external calls (__init__, __init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 390–436)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: Performs the actual send-after-lock sequence for one operation. It pins the binding, stages any input body, posts the operation, waits for the reply, and cleans up.

**Data flow**: It receives the conversation, operation object, optional body, bound workspace, and deadline. It writes metadata and in-flight keys to Redis, optionally uploads the body to the blob store, appends the operation to the Redis stream, waits for a reply, and finally clears operation state.

**Call relations**: RedisTerminals.send calls this only after acquiring the conversation lock. It uses _op_fields to format the stream message, _await_reply to receive the answer, and _clear_op for teardown.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 438–446)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: Formats a TerminalOp into Redis stream fields. This creates the compact message that the terminal side will later read.

**Data flow**: It receives a TerminalOp and returns a dictionary containing its ID, kind, timeout, name, argument, and parameters as Redis-friendly values.

**Call relations**: RedisTerminals._run_op calls this just before adding an operation to the operation stream. RedisTerminals._decode_op later reverses this shape on the receiving side.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 448–456)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: Turns Redis stream fields back into a TerminalOp object. This lets the terminal-serving side work with a normal typed operation instead of raw Redis fields.

**Data flow**: It receives a field dictionary from Redis. It converts text fields, parses the timeout as an integer, fills missing optional fields with empty strings, and returns a TerminalOp.

**Call relations**: RedisTerminals.next_op calls this after claiming an operation. It is the receiving-side counterpart to RedisTerminals._op_fields.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 458–481)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: Waits for the reply to a posted operation until a deadline. It uses Redis blocking reads, but only in bounded chunks so it can notice timeouts.

**Data flow**: It receives an operation ID, total deadline, and user-facing timeout. It repeatedly reads the operation’s reply stream, decodes the first reply found, and returns bytes; if time runs out or Redis fails, it raises TerminalGone.

**Call relations**: RedisTerminals._run_op calls this after posting an operation. It uses _stream_entries to parse Redis responses and _decode_reply to turn the reply record into bytes or an error.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 483–496)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: Interprets a terminal reply record. It distinguishes failed operations, large blob-backed replies, and small base64-encoded replies.

**Data flow**: It receives an operation ID and reply fields. A failure field becomes TerminalOpFailed; a blob marker causes a blob-store read; otherwise it base64-decodes the inline reply and returns bytes.

**Call relations**: RedisTerminals._await_reply calls this when a reply stream entry arrives. RedisTerminals._deliver_reply writes the reply shapes that this method understands.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 498–529)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for and claims the next operation that should be delivered to the terminal client. Claiming is atomic, meaning no second pod can grab the same operation at the same time.

**Data flow**: It receives a conversation ID and optionally an operation ID to skip. It runs a Redis Lua script that scans old operations, removes expired ones, claims an unclaimed operation, and returns it; if none is ready, it waits for stream activity and tries again.

**Call relations**: Terminal-serving code calls this while holding a client stream open. It uses _pairs and _decode_op to turn the Lua result into a TerminalOp, and it raises TerminalGone if Redis cannot be reached.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 531–546)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Fetches the input body staged for an in-flight operation, if the request is allowed to see it. It prevents one conversation or member from reading another’s staged bytes.

**Data flow**: It receives a conversation ID, operation ID, and optional member ID. It reads operation metadata, checks it with _gate_ok, then reads the body from the blob store; missing metadata, mismatches, missing blobs, or timeouts return None.

**Call relations**: A pod serving the terminal-side read projection calls this when the client needs the operation body. It relies on metadata written by RedisTerminals._run_op and blob keys produced by _body_blob.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 548–563)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts a terminal reply and schedules it to be delivered through Redis. It returns immediately so the HTTP reply route or similar caller is not blocked on Redis I/O.

**Data flow**: It receives the conversation, operation ID, reply bytes, optional failure message, and optional member ID. It creates a background task to deliver the reply and returns True.

**Call relations**: Terminal-side code calls this after an operation finishes. It hands the real work to RedisTerminals._deliver_reply through RedisTerminals._spawn.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 565–594)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: Writes a terminal reply into the shared rendezvous. It also checks that the reply belongs to the expected conversation and member before publishing it.

**Data flow**: It receives operation identity, reply bytes, failure text, and member identity. It reads metadata, rejects missing or mismatched operations with a warning, stores large replies in the blob store or small replies inline, then appends a reply record to Redis and sets an expiry.

**Call relations**: RedisTerminals.resolve schedules this in the background. RedisTerminals._await_reply is waiting on the reply stream this method writes to, and RedisTerminals._decode_reply later reads the format it produced.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 596–607)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a staged-body read or reply is allowed for the named conversation and member. It fails closed, meaning unclear or mismatched information is treated as not allowed.

**Data flow**: It receives raw metadata, a conversation ID, and an optional member ID. It parses the metadata, verifies the conversation matches, and if a member was supplied, verifies it matches the stored member.

**Call relations**: RedisTerminals.staged and RedisTerminals._deliver_reply call this before exposing body bytes or accepting replies. It is the safety gate around cross-pod operation data.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 609–612)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports that this Redis-backed transport has no reliable local view of the operation currently awaited elsewhere. It deliberately returns no partial answer.

**Data flow**: It receives a conversation ID but does not read Redis or local state. It always returns None.

**Call relations**: Operator or inspection code may call this through the terminal transport interface. In this cross-pod version, the real in-flight operation may be on another pod, so the method avoids pretending it knows.


##### `RedisTerminals._clear_op`  (lines 614–635)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: Cleans up Redis keys and blob-store objects after an operation finishes or times out. The cleanup is best-effort because expiry times also protect the system.

**Data flow**: It receives the conversation, operation ID, and optional stream entry ID. It deletes the operation stream entry, metadata, delivery marker, in-flight binding, reply stream, input blob, and reply blob, suppressing cleanup failures.

**Call relations**: RedisTerminals._run_op calls this in a finally block after waiting for the reply. It removes the state created by _run_op, next_op, and _deliver_reply, while Redis key expiries cover missed deletes.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 637–644)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: Starts a background task and keeps track of it so it is not lost immediately. It also routes failures through a logging wrapper.

**Data flow**: It receives a coroutine and an event loop. It creates a task for _logged to run that coroutine, stores the task in a set, and removes it from the set when it finishes.

**Call relations**: RedisTerminals.resolve uses this to fire off reply delivery without waiting. It passes the work to RedisTerminals._logged so errors are reported.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 646–650)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: Runs a background coroutine and logs any exception it raises. This prevents background delivery failures from disappearing silently.

**Data flow**: It receives a coroutine, awaits it, and if an exception happens, sends a warning with the error text.

**Call relations**: RedisTerminals._spawn wraps background reply delivery with this method. It is the final safety net for RedisTerminals._deliver_reply failures.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).
