# Host surface, backend, and extension-API example manifests  `stage-3.2.4`

This stage is part of startup and shared setup. It is where small extension “registration cards” tell the host what extra surfaces and backends exist, so the main system can mount them in the right places. A manifest is just a short configuration file that says, “this extension is here, this is its version, and this is what it provides.”

The debugger manifest registers the debugger extension and tells the host to expose its web-based debugging surface. The UFO manifest does the same for the UFO surface itself, making it visible as a user-facing area. The web manifest is broader: it declares the web portal’s routes, access tools, background jobs, conversation slots, and browser home-page behavior. The Redis hub manifest plugs in Redis-based backends, meaning shared services built on Redis for hub and terminal work. The sample extension is a test model: it uses the public extension API in many ways with simple fake behavior, proving the core system can call extensions correctly.

## Files in this stage

### Debugger host surface
Debugger extension metadata declares the debug web surface the host should mount.

### `extensions/debugger/ufo_ext_debugger/manifest.py`

`config` · `startup`

This file is the debugger extension’s calling card. In this project, an extension needs a manifest: a small description that says “who I am” and “what I add to the system.” Without this file, the core application would not know that the debugger extension exists, what version it is, or which routes should be exposed for it.

The file defines two simple constants, `NAME` and `VERSION`, then provides a `manifest()` function. That function builds a `Manifest` object for the extension. Inside it, the file declares one surface, meaning one externally reachable area of the extension. A surface is like a service counter in a building: it has a name, a set of routes people can visit, and a rule for deciding who is allowed to use it.

For this debugger, the routes come from `ROUTES`, the surface name comes from `SURFACE_DEBUG`, and access is tied to `resolve_operator_workspace`. That resolver is used to identify the operator workspace, so the debugger is only mounted where that identification succeeds. In plain terms, this file does not implement debugging itself; it registers the debugger’s doorway with the rest of the system and describes how that doorway should be found and protected.

#### Function details

##### `manifest`  (lines 14–21)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the debugger extension’s manifest, which is the structured description the host system uses to register the extension. Someone would use this when loading extensions so the debugger’s surface and routes become known to the core application.

**Data flow**: It starts with the file’s fixed name and version values, plus the imported debugger route list, surface name, and workspace-identification function. It packages those into a `SurfaceSpec`, then places that surface specification inside a `Manifest`. The result is a ready-to-use manifest object; it does not modify outside state by itself.

**Call relations**: When the extension system asks this module what it provides, `manifest` creates the answer. It calls `SurfaceSpec.__init__` to describe the debugger surface, then calls `Manifest.__init__` to wrap that surface together with the extension name and version for the host system to consume.

*Call graph*: 2 external calls (__init__, __init__).


### Runtime backend registration
Redis hub metadata registers Redis-backed hub and terminal backend providers with configuration builders.

### `extensions/redis_hub/ufo_ext_redis_hub/manifest.py`

`config` · `startup / config load`

This manifest exists so the core system can swap its local, single-process coordination pieces for Redis-backed ones. Redis is an external data service often used as a shared message and state store. Here it lets several running server instances share live frames and terminal connections instead of being limited to one process.

The file defines two backend names, both called "redis" in their own areas. If the user selects `hub.backend = "redis"`, the system should build a `RedisStreamHub`, which uses Redis Streams to fan out live frames across server instances. If the user selects `terminal.backend = "redis"`, the system should build `RedisTerminals`, which helps route a connected user terminal even when the current request lands on a different pod or machine.

Both builders use the same `hub.url` setting, such as `redis://host:6379/0`. The file deliberately checks for that URL immediately. If it is missing, it raises a clear error at startup instead of failing later during the first real operation. Finally, `manifest()` packages these build instructions into a `Manifest` object that the extension loader can read.

#### Function details

##### `_build_hub`  (lines 24–29)

```
def _build_hub(url: str | None) -> Hub
```

**Purpose**: This function creates the Redis-backed hub when the user has chosen the Redis hub backend. It also protects the system from a vague later failure by refusing to start if the Redis URL is missing.

**Data flow**: It receives a Redis connection URL, or `None` if no URL was configured. If the URL is missing, it raises a clear runtime error explaining that `hub.url` is required. If the URL is present, it passes that URL into `RedisStreamHub` and returns the newly created hub object.

**Call relations**: This builder is stored inside the `HubSpec` created by `manifest()`. Later, when the core system sees that the selected hub backend is `redis`, it uses this function to construct the actual Redis stream hub; the function then hands off to `RedisStreamHub.__init__` to make the concrete backend.

*Call graph*: 1 external calls (__init__).


##### `_build_terminal`  (lines 32–37)

```
def _build_terminal(url: str | None, blob: BlobStore) -> TerminalTransport
```

**Purpose**: This function creates the Redis-backed terminal transport when the user has chosen the Redis terminal backend. It connects the terminal routing layer to Redis and to the system’s blob store, which is storage for larger shared data.

**Data flow**: It receives a Redis connection URL and a `BlobStore`. If the URL is missing, it raises a clear runtime error saying `hub.url` is required. If the URL is present, it passes the URL and blob store into `RedisTerminals` and returns the new terminal transport object.

**Call relations**: This builder is placed inside the `TerminalTransportSpec` created by `manifest()`. When the core system needs the `redis` terminal transport, it calls this builder, which then hands construction over to `RedisTerminals.__init__`.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 40–48)

```
def manifest() -> Manifest
```

**Purpose**: This function returns the extension’s complete manifest: its name, version, and the Redis hub and terminal backends it offers. It is the main thing the extension loader reads to discover what this extension can add to the system.

**Data flow**: It reads the module constants for the extension name, version, and backend names. It wraps `_build_hub` in a `HubSpec`, wraps `_build_terminal` in a `TerminalTransportSpec`, and returns a `Manifest` containing both declarations.

**Call relations**: When the extension system loads this package, it calls `manifest()` to learn what is available. Inside, this function creates the `HubSpec`, `TerminalTransportSpec`, and final `Manifest`; those specs carry the builder functions that will be used later if the user selects the Redis backends.

*Call graph*: 3 external calls (__init__, __init__, __init__).


### Public extension API sample
The sample extension exercises the public extension API end to end with canned behavior for integration tests.

### `extensions/sample/ufo_ext_sample.py`

`orchestration` · `extension load, conformance tests, and feature dispatch`

Think of this file as a working demo booth for the extension system. It does not try to provide a real product feature. Instead, it installs a real extension named "sample" that touches tools, jobs, routes, onboarding, hooks, connectors, surfaces, sources, indexing, embeddings, models, browser leases, sandbox carriers, search, memory search, object storage, and conversation slots. Each part returns predictable data or writes a small record into the extension store, which is durable workspace-scoped storage. That lets conformance tests ask, "Did the system really call the extension the same way it would call a third-party extension?" without using fake logs or hidden test hooks.

The file’s main output is `manifest()`. A manifest is the extension’s menu: it tells the host application what capabilities exist and which functions should run for each one. Around that manifest are tiny implementations. For example, the echo tool records its input, the sample job writes a marker and proposes a prompt change, the route echoes a request body, the connector has a fake OAuth flow and broker, and the search provider returns one canned result. The object stores show both writable and read-only object types. The surface functions simulate incoming messages from external channels.

Without this file, the project would lose a broad, installed, real-world-style test of the extension contract. It acts like a smoke detector for API drift: if core changes break an extension seam, this sample should fail quickly.

#### Function details

##### `_echo`  (lines 277–281)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It records the message it was given in the extension’s durable store, then returns the same message as tool output.

**Data flow**: It receives a tool context and typed echo arguments. It checks that an extension context is present, saves the arguments under the sample tool key, wraps the message as text content, and returns a tool result.

**Call relations**: The manifest registers this as the main sample tool. It is also the tool targeted by the pre-tool hook that can deny execution, so tests can prove both normal dispatch and blocked dispatch.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 284–306)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Runs the sample note tool, which writes a note into the extension’s own database table and reads it back. It proves that an extension migration-created table can be used safely inside a workspace-scoped transaction.

**Data flow**: It receives note text and a tool context. It finds the workspace id, updates or inserts one note row for that workspace, reads the stored note back, and returns that text as the tool result.

**Call relations**: The manifest registers this as a second tool. The scheduled job uses this table to find candidate workspaces, so this tool also seeds data that the job path can later observe.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 309–335)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample scheduled job. It records that the job ran, then exercises optional off-turn abilities such as reading trajectories, proposing an agent prompt change, writing a workspace file, and running a probe command.

**Data flow**: It receives an extension context. It writes a job marker, optionally reads available agent trajectories, proposes a prompt edit for the first one, writes a file into that conversation’s workspace, runs a command to read that file, and stores each result.

**Call relations**: The manifest registers this as the sample job. It calls the public extension context methods that core provides to jobs, proving that background work can use the same extension-facing surfaces as live interactions.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 338–341)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Runs the sample HTTP route handler. It records the incoming request body and returns that body as plain text.

**Data flow**: It receives an extension context and request. It reads the request body bytes, decodes them to text, stores them under the route key, and returns a plain text response with the same text.

**Call relations**: The manifest registers this route at the sample route path with workspace identification handled by `resolve_workspace`.

*Call graph*: 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 376–387)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists writable sample widgets stored in the extension store. It turns internal stored rows into object-list rows that the public object API can show.

**Data flow**: It receives a tool context and list query. It reads all extension-store keys with the widget prefix, validates each stored value, builds rows with names, summaries, and visible fields, and returns a paged object result.

**Call relations**: The widget object kind in the manifest uses this store. This method relies on `WidgetStore._ext` to get the extension context and hands the rows to the shared object paging helper.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 389–396)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one writable sample widget by name. It returns the widget’s spec and timestamps if the widget exists.

**Data flow**: It receives a tool context and widget name. It looks up the matching store key, validates the stored value, and returns object detail; if no stored value exists, it returns nothing.

**Call relations**: The object API calls this when a user or test reads a specific widget. It uses `WidgetStore._ext` to reach the extension store.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 398–405)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Reports status for a sample widget. In this sample, writable widgets have no extra live status, so the function intentionally returns nothing.

**Data flow**: It receives the context, name, and optional expected generation value. It does not read or change anything and returns `None`.

**Call relations**: The object system can ask stores for status separately from stored spec. This implementation proves that a store may validly have no extra status.


##### `WidgetStore.apply`  (lines 407–423)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It preserves the original creation time when updating and refreshes the update time.

**Data flow**: It receives a widget name and new spec. It reads the existing stored widget if present, chooses a creation timestamp, writes the new stored widget under the widget key, and returns no separate value.

**Call relations**: The object API calls this for create and update operations. It uses `WidgetStore._ext` to access the extension store and the `StoredWidget` model to keep stored data in a known shape.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, now).


##### `WidgetStore.delete`  (lines 425–434)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only if the speaker is a workspace admin. It demonstrates permission-gated object deletion.

**Data flow**: It receives a tool context and widget name. It asks the tool context whether the speaker is an admin; if not, it raises an admin-required error. If allowed, it deletes the widget key from the extension store.

**Call relations**: The object API calls this for delete requests. It combines core’s speaker permission check with the extension store reached through `WidgetStore._ext`.

*Call graph*: calls 2 internal fn (speaker_is_admin, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 436–439)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Pulls the extension context out of a tool context. It gives the widget store a safe way to reach workspace-scoped storage.

**Data flow**: It receives a tool context. If the extension context is missing, it raises an error; otherwise it returns the extension context.

**Call relations**: The widget store’s list, get, apply, and delete methods all call this helper before touching the extension store.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 447–451)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample read-only relic objects. It always exposes one canned relic.

**Data flow**: It receives a tool context and list query. It builds one object row for the fixed relic and returns it through the object paging helper.

**Call relations**: The manifest registers `RelicStore` as the store for the read-only relic object kind. This method shows what a system-produced, non-editable object list can look like.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 453–458)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Fetches the one sample relic by name. It returns details only for the known relic name.

**Data flow**: It receives a relic name. If the name does not match the canned relic, it returns nothing; otherwise it returns object detail with the fixed inscription and no timestamps.

**Call relations**: The object API calls this when reading a relic. It pairs with `RelicStore.list` to provide read access while mutation methods refuse writes.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 460–467)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small live status for a relic. It labels the relic as having been "excavated".

**Data flow**: It receives context, name, and an optional expected generation. It does not read stored state and returns a small dictionary describing origin.

**Call relations**: The object system can ask this store for status. This sample uses that hook to prove read-only kinds can still report live metadata.


##### `RelicStore.apply`  (lines 469–478)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a relic. It demonstrates a read-only object kind.

**Data flow**: It receives the requested relic spec and related metadata. It does not store anything and raises a verb-not-supported error with the sample refusal message.

**Call relations**: The object API calls this for mutation attempts. Tests can confirm that read-only kinds reject writes through the public object surface.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 480–487)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a relic. It keeps the relic object kind read-only.

**Data flow**: It receives the requested relic name and metadata. It does not delete anything and raises a verb-not-supported error.

**Call relations**: The object API calls this for delete attempts. It completes the read-only behavior alongside `RelicStore.apply`.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 506–515)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one deterministic page from the sample source configuration. It proves that a source backend can turn typed config into pages for sync and indexing.

**Data flow**: It receives source config, a cursor, and auth information. It ignores the cursor and auth, builds one page whose body and title come from the configured topic, and returns it with no next cursor.

**Call relations**: The onboarding setup registers this source. Core’s source sync path can later call this fetch method and land the page in memory or search indexes.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 528–530)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the sample in-memory index. A chunk is a stored piece of text plus metadata used for search.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it in a dictionary keyed by its digest, replacing any older chunk with the same digest.

**Call relations**: Core’s indexing flow calls this through the manifest-registered index backend. Later search, delete, and prune methods operate on the same dictionary.


##### `SampleIndex.delete`  (lines 532–534)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all indexed chunks that belong to a requested scope. A scope identifies one owner, such as one page or document.

**Data flow**: It receives an index scope. It finds all stored chunks whose owner kind and owner id match that scope, then removes them from the dictionary.

**Call relations**: Core calls this when content for a scope should disappear from the index. It uses `_in_scope` to keep the matching rule shared with other methods.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 536–537)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether the sample index contains any chunks for a requested scope.

**Data flow**: It receives an index scope. It scans stored chunks and returns true if at least one chunk matches the scope, otherwise false.

**Call relations**: Core can call this to decide whether a scope is already indexed. It uses `_in_scope` for the same owner matching rule used by delete and prune.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 539–545)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes old chunks for a scope while keeping a named set of current chunk digests. This mirrors how an index drops stale pieces after content changes.

**Data flow**: It receives a scope and a keep-set of digests. It scans stored chunks, and for chunks inside the scope whose digest is not in the keep-set, it deletes them.

**Call relations**: Core’s reindexing flow can call this after upserting current chunks. It uses `_in_scope` to decide which chunks belong to the affected scope.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 547–556)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a simple word-count search over indexed text. It ranks chunks higher when query terms appear more often.

**Data flow**: It receives query text, allowed subjects, owner kind, and a limit. It filters chunks with `_scoped`, counts lowercase query-term occurrences in each chunk, turns positive scores into hits, sorts by score, and returns the top hits.

**Call relations**: Core can call this through the index backend for text search. It depends on `_scoped` for access control-style filtering and `_hit` to shape results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 558–566)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Runs a simple vector search over indexed chunks. A vector is a list of numbers used to compare meaning; here it uses dot product as the score.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and a limit. It filters chunks with `_scoped`, scores each chunk embedding with `_dot`, converts positive scores to hits, sorts, and returns the top hits.

**Call relations**: Core can call this when doing embedding-based retrieval. It uses `_scoped`, `_dot`, and `_hit` to keep filtering, scoring, and result shaping separate.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 568–573)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters indexed chunks to only those visible for a requested owner kind and subject set.

**Data flow**: It receives allowed subjects and an owner kind. It scans stored chunks and returns only chunks whose owner kind matches and whose subject is allowed.

**Call relations**: Both lexical and vector search call this before scoring, so the sample search methods only search the appropriate slice of the index.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 582–583)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for every input text. It gives tests a predictable embedding backend without calling an outside model service.

**Data flow**: It receives a tuple of texts. It ignores the actual text contents and returns the same sample vector once for each input.

**Call relations**: The manifest registers this as an embedding backend. Core can select it and feed its output into index vector search.


##### `_in_scope`  (lines 586–587)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a chunk belongs to a particular index scope. It compares the chunk’s owner kind and owner id with the scope.

**Data flow**: It receives a chunk and scope. It returns true when both owner fields match, otherwise false.

**Call relations**: Sample index delete, has-chunks, and prune call this helper so all scope filtering follows one rule.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 590–593)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes the dot product between two numeric vectors. This is the simple similarity score used by the sample vector search.

**Data flow**: It receives two tuples of numbers. If either is empty, it returns zero; otherwise it multiplies matching positions and sums the products.

**Call relations**: SampleIndex.vector calls this to score each candidate chunk against the query embedding.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 596–605)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Turns an indexed chunk and score into a search hit. A hit is the public result shape returned by the index backend.

**Data flow**: It receives a chunk and numeric score. It copies the chunk’s identifying fields, text, and ordinal into a hit object and attaches the score.

**Call relations**: Both lexical and vector search call this after scoring chunks, so their outputs share the same result format.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 608–615)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It records that onboarding happened and registers the sample content source for the workspace.

**Data flow**: It receives an extension context. It writes an onboarding marker into the store, builds a source config with the canned topic, and asks core to register the source for the shared subject.

**Call relations**: The manifest registers this as an onboarding step. It connects the source backend to the workspace so later sync can call `SampleSource.fetch`.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 618–621)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Blocks the sample echo tool before it runs. It demonstrates that a pre-tool hook can stop a tool call.

**Data flow**: It receives a hook context and returns a deny outcome with a fixed reason. It does not read or write extension state.

**Call relations**: The manifest attaches this hook to the echo tool’s pre-tool event. If it fires, `_echo` should not run.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 624–632)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool calls after they finish. It proves that post-tool hooks receive the success event.

**Data flow**: It receives a hook context. If the payload is a successful post-tool-use event, it stores the tool name under the post-hook key and returns no special outcome.

**Call relations**: The manifest registers this for all post-tool-use events. It complements `_record_post_failure`, which records failed tool calls instead.


##### `_record_post_failure`  (lines 635–641)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records tool calls that ended in an error. It proves that failed tool dispatch goes to a different hook event from successful dispatch.

**Data flow**: It receives a hook context. If the payload is a post-tool-use-failure event, it stores the failed tool name and returns no special outcome.

**Call relations**: The manifest registers this for post-tool-use-failure events. Tests can compare its store marker with `_record_post` markers.


##### `_record_stop`  (lines 644–650)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer at the end of a turn. It demonstrates the stop hook, which fires just before the answer is committed.

**Data flow**: It receives a hook context. If the payload contains a stop answer, it stores that answer in the extension store.

**Call relations**: The manifest registers this for stop events. It lets tests verify that turn-end hook delivery happened.


##### `_record_pre_compact`  (lines 653–660)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information before conversation compaction. Compaction means shortening prior context, usually to fit model limits.

**Data flow**: It receives a hook context. If the payload is a pre-compact event, it stores the reason and the token estimate before compaction.

**Call relations**: The manifest registers this for pre-compact events. It pairs with `_record_post_compact` to prove both sides of compaction are observable.


##### `_record_post_compact`  (lines 663–675)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction. It captures the summary and token counts before and after.

**Data flow**: It receives a hook context. If the payload is a post-compact event, it stores the summary, before-token count, and after-token count.

**Call relations**: The manifest registers this for post-compact events. Together with `_record_pre_compact`, it checks the compaction hook lifecycle.


##### `_record_page_change`  (lines 678–691)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records delivered page-change events. It also notes whether the off-turn context had a model wired in.

**Data flow**: It receives a hook context. If the payload is a batch of page changes, it stores the page ids and whether `ctx.ext.model` was available.

**Call relations**: The manifest registers this for page-change hooks. It proves that data-plane change delivery reaches extensions with the expected off-turn context.


##### `_SampleConnectorOAuth.authorize_url`  (lines 704–705)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the fake OAuth authorization URL for the sample connector. OAuth is the common web flow where a user grants an app access to another service.

**Data flow**: It receives a state value and redirect URI. It formats them into the canned sample authorization URL and returns it as a string.

**Call relations**: The connector provider uses this during account connection. It is the first half of the sample connector’s fake OAuth flow.


##### `_SampleConnectorOAuth.exchange`  (lines 707–710)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake OAuth exchange and returns a connected account id. It does not return a secret token.

**Data flow**: It receives the code, redirect URI, workspace id, and state. It ignores the actual values and returns the fixed sample account id.

**Call relations**: The connector provider calls this after authorization redirects back. The broker later uses the account id for tool execution and credentials.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 723–724)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample connector broker’s available tools. It always advertises one canned broker tool.

**Data flow**: It receives workspace, provider, and query values. It ignores the query and returns one tool with the fixed slug and description.

**Call relations**: Core can call this while discovering dynamic connector tools. `_SampleBroker.search` also calls it to include tools in broker search results.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 726–733)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool. It rejects unknown tool slugs.

**Data flow**: It receives a workspace, provider, and slug. If the slug is not the known sample slug, it raises an unknown-tool error; otherwise it returns the tool description and a small JSON-style input schema.

**Call relations**: Core calls this when it needs details for a dynamic broker tool before execution.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 735–752)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Pretends to execute the sample broker tool. It echoes the call details back as the provider response.

**Data flow**: It receives workspace, provider, slug, arguments, account id, and optional idempotency key. It rejects unknown slugs; otherwise it returns a dictionary containing those values.

**Call relations**: Core calls this through the connector broker when a dynamic tool runs. The response can then be inspected or converted into file outputs.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 754–765)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts produced file references from a broker response. It looks for URLs that were echoed through the tool arguments.

**Data flow**: It receives the response dictionary. If it contains an argument named `file_output_urls` that is a list of strings, it converts each URL into a broker file with a name from the URL path; otherwise it returns no files.

**Call relations**: Core can call this after broker execution to know which remote files should be fetched into the workspace.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 767–793)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Stages a file upload for a connector tool. It simulates creating a workspace-backed upload slot and also simulates deduplication when the same content was already staged.

**Data flow**: It receives workspace, provider, slug, filename, MIME type, and MD5 hash. It builds a content-addressed key; if already seen, it returns upload metadata with no upload URL. If new, it records the key and returns a file URL plus the argument that should be passed to the broker tool.

**Call relations**: Core calls this when connector tool arguments require uploading local files. The sample broker later receives the returned argument during execution.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 795–798)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a canned broker search result. It includes the sample broker tool and a simple plan telling the caller what to do first.

**Data flow**: It receives workspace, provider, and query. It calls `tools` to get available tools and wraps them with the fixed search plan.

**Call relations**: Core can call this when searching connector capabilities. It reuses `_SampleBroker.tools` so catalog and search agree.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 800–801)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fake bearer credential for a connected account. A bearer credential is a token usually sent in an authorization header.

**Data flow**: It receives workspace, provider, and account id. It returns a credential whose bearer token is the sample prefix plus the account id.

**Call relations**: Core can ask the connector broker for credentials when routing provider calls or syncing connector data.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 812–830)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector’s server-side execution tool. It proves that a tool can resolve the agent’s connected account and receive an idempotency key for side effects.

**Data flow**: It receives a tool context and arguments. It checks for an extension context, asks core for the connected account for the sample provider, stores the account, tool name, and idempotency key, then returns the account id as text.

**Call relations**: The manifest registers this tool inside the connector provider. It relies on `ToolContext.connector_account` to enforce that the agent has a connector grant.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 840–841)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Turns one bytes object into a one-piece async stream. It is a tiny helper for writing inbound surface files.

**Data flow**: It receives bytes and yields those same bytes once.

**Call relations**: `_surface_ingest` calls this when it needs to stream inbound text into a workspace file.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_ingest`  (lines 844–871)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles incoming messages on the durable sample surface. A surface is an external channel, such as email or chat, that can admit turns into UFO.

**Data flow**: It reads and validates the JSON request, finds or links a member identity, gets or creates a conversation, optionally writes inbound text as a workspace file, admits a turn with an idempotency key, and returns ids plus whether a run was opened.

**Call relations**: The durable surface route in the manifest points here. It calls several `SurfaceContext` methods so tests can verify identity linking, conversation selection, file writing, and turn admission.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 874–875)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed external reply reference for durable surface writeback. It simulates posting a reply to the outside channel.

**Data flow**: It receives the surface context and writeback payload. It ignores the contents and returns the fixed sample post reference.

**Call relations**: The durable surface registers this as its post function. `_surface_attach` can then attach files to that reply reference.


##### `_surface_attach`  (lines 878–883)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies shared artifacts from the blob store into delivered keys. It proves that surface attachment delivery can stream stored files back out.

**Data flow**: It receives a writeback with artifacts. For each artifact, it builds a delivered key using the turn id and filename, reads the artifact blob as a stream, and writes that stream to the delivered key.

**Call relations**: The durable surface registers this as its attach function. It follows `_surface_post` in the writeback flow when a reply has files.


##### `_surface_live_admit`  (lines 886–908)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles incoming messages on the live sample surface. Live mode admits a turn for real-time tailing instead of durable writeback polling.

**Data flow**: It reads the request, finds or adopts a member identity, gets or creates a conversation, admits a turn, reads the turn owner, gets a spend rollup for a time window, and returns those details as JSON.

**Call relations**: The live surface route in the manifest points here. It contrasts with `_surface_ingest` by not registering a durable post function.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 911–915)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live frames for a turn as newline-delimited JSON. This lets a client follow a turn’s progress.

**Data flow**: It reads the turn id from the route path, starts `_surface_frames`, and returns a streaming HTTP response with the proper media type.

**Call relations**: The live surface GET route points here. It delegates frame production to `_surface_frames`.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 918–921)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Yields live hub frames for a turn one JSON line at a time. A hub is the internal stream of turn events.

**Data flow**: It receives a surface context and turn id. It opens a tail on that turn, loops through frames, serializes each frame to JSON, adds a newline, and yields bytes.

**Call relations**: `_surface_live_stream` calls this to power its streaming response. It uses `SurfaceContext.tail` as the public live-stream seam.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 932–935)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a fixed model response. It acts like a tiny language model backend without calling a real model service.

**Data flow**: It receives a model request. It yields a stream start event, one text delta containing the canned reply, and a usage event with fixed token counts.

**Call relations**: The manifest registers this through a model spec. Core can select it, stream its events, and price usage with the contributed model price.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 945–946)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the browser debugging endpoint for the sample CDP lease. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It takes no external input beyond the lease object and returns a fixed endpoint URL.

**Call relations**: The browser engine calls this on a lease obtained from `SampleCdpProvider.lease` or `reattach`.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 948–949)

```
async def token(self) -> str
```

**Purpose**: Returns the durable token used to reattach to the sample browser lease. Here the token is just the fixed URL.

**Data flow**: It reads no changing state and returns the sample CDP URL string.

**Call relations**: Core can store this token and later pass it to `SampleCdpProvider.reattach`.


##### `SampleCdpLease.place_file`  (lines 951–952)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Pretends to place a file where the browser can use it. In this sample, the file path is already suitable, so it returns the path unchanged.

**Data flow**: It receives a path and a file-reading callback. It does not read the file and returns the original path.

**Call relations**: Browser automation code can call this through the CDP lease protocol when it needs to make a file available to the browser.


##### `SampleCdpLease.download_dir`  (lines 954–955)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the sample browser download directory.

**Data flow**: It reads no changing state and returns the fixed sample download path.

**Call relations**: Browser-related code can call this on the lease to know where downloads are expected.


##### `SampleCdpLease.fetch_download`  (lines 957–958)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a downloaded file by its browser-provided id. It returns the bytes from the sample download directory.

**Data flow**: It receives a download guid. It builds a path under the sample download directory and reads the file bytes in a worker thread so the async loop is not blocked.

**Call relations**: Browser code can call this through the lease after a download completes.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 960–961)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample CDP lease. It is a no-op because the sample lease owns no real browser resources.

**Data flow**: It receives no meaningful input and returns nothing without changing state.

**Call relations**: Core may call this during cleanup after using a browser lease.


##### `SampleCdpProvider.lease`  (lines 971–972)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a new sample browser lease. It returns the canned lease object.

**Data flow**: It optionally receives a sandbox session. It ignores it and returns a new `SampleCdpLease`.

**Call relations**: The manifest registers this provider. Core calls this when it needs a browser endpoint from the sample CDP backend.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 974–975)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reattaches to a sample browser lease from a token. Since the sample endpoint is fixed, it simply returns a new canned lease.

**Data flow**: It receives a token string. It ignores the token contents and returns a new `SampleCdpLease`.

**Call relations**: Core calls this when resuming a previous browser session represented by `SampleCdpLease.token`.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 986–987)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed credential for the sample auth proxy. An auth proxy supplies credentials that another backend can use.

**Data flow**: It receives workspace, provider, and account id. It ignores the values and returns a credential with the fixed sample bearer token.

**Call relations**: The manifest registers this auth proxy backend. Core can select it and ask for credentials during sync or outbound access.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 999–1007)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns a canned web search result and direct answer. It avoids real network search while proving the search-provider seam.

**Data flow**: It receives a search query. It ignores the query text and returns one fixed hit plus a fixed answer string.

**Call relations**: The manifest registers this search provider. Research tools or core search flows can call it as if it were a real provider.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1009–1010)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches a canned page for a URL. It proves that a search provider can also retrieve page text.

**Data flow**: It receives a fetch request. It copies the request URL into the result and supplies fixed page text.

**Call relations**: Core can call this after search when it needs page content. The provider declares that it supports fetch.

*Call graph*: 1 external calls (__init__).


##### `SampleMemorySearch.search`  (lines 1019–1037)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match. Memory here means previously stored information that can be recalled.

**Data flow**: It receives query strings, a source reader with allowed subjects, and optional start/end times. It stores the queries, subjects, and time bounds, then returns one fixed memory match.

**Call relations**: The manifest registers this as a memory search provider. Core calls it to prove provider-selected memory search receives the intended scope.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1039–1040)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list. It returns the one sample memory kind.

**Data flow**: It takes no input beyond the provider object and returns a tuple containing the fixed kind string.

**Call relations**: Core can call this before asking for recent memory items, so callers know which categories are available.


##### `SampleMemorySearch.list_recent`  (lines 1042–1068)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a recent-memory listing request and returns one canned memory row.

**Data flow**: It receives subjects, a limit, optional kinds, and an optional listing cursor. It stores those request details in the extension store and returns a listing page with one fixed memory match.

**Call relations**: Core calls this through the memory search provider when it needs recent items rather than query-based search.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1080–1081)

```
def __init__(self) -> None
```

**Purpose**: Initializes the sample sandbox carrier’s in-memory file storage. A carrier is the backend that creates and talks to sandboxes.

**Data flow**: It creates an empty dictionary mapping paths to written bytes.

**Call relations**: The manifest registers `SampleCarrier` as a carrier factory. Core constructs it when selecting the sample carrier backend.


##### `SampleCarrier.create`  (lines 1083–1088)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a fake sandbox handle. It does not start a real container.

**Data flow**: It receives a sandbox spec and returns a handle containing the conversation id, fixed container id, and run token from the spec.

**Call relations**: Core calls this when it wants a new sandbox from the sample carrier.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1090–1097)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Attaches to a fake existing sandbox when a resume id is available.

**Data flow**: It receives a sandbox spec. If there is no resume id, it returns nothing; otherwise it returns a handle using the resume id as the container id.

**Call relations**: Core calls this before creating a new sandbox when it might be able to resume an old one.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1099–1102)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Pretends to run a command in the sandbox. It echoes the command arguments instead of executing them.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout. It joins the arguments with spaces and returns that as stdout with exit code zero.

**Call relations**: Core calls this through the carrier protocol when sandbox command execution is requested.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1104–1105)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the sample carrier’s in-memory file map.

**Data flow**: It receives a handle, path, and bytes. It stores the bytes under the path and returns nothing.

**Call relations**: Core calls this when copying files into the sandbox. `SampleCarrier.read` can later stream the same bytes back.


##### `SampleCarrier.read`  (lines 1107–1110)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes previously written to the sample carrier. It raises an error if the path was never written.

**Data flow**: It receives a handle and path. It looks up the path in the in-memory file map; if present, it yields the bytes once, otherwise it raises file-not-found.

**Call relations**: Core calls this when reading files back from the sandbox. It pairs with `SampleCarrier.write`.


##### `SampleCarrier.file_op`  (lines 1112–1115)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a sandbox file operation through the shared sandbox file helper. This lets the sample carrier support the standard file-operation vocabulary.

**Data flow**: It receives a handle, operation name, and parameters. It passes itself and those values to the shared helper and returns the helper’s result.

**Call relations**: Core calls this for higher-level sandbox filesystem operations. The helper can use the carrier’s read and write methods.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `SampleCarrier.dial`  (lines 1117–1118)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a fake network target for a port inside the sample sandbox. Dialing means connecting to a service exposed by the sandbox.

**Data flow**: It receives a handle and port. It returns a host string based on the fixed container name and port, with TLS disabled.

**Call relations**: Core calls this when it needs to reach a service running inside a sandbox selected through the carrier.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1121–1129)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Finds the workspace id claimed by a bearer token in an HTTP request. If the request has no valid bearer token, it rejects by returning nothing.

**Data flow**: It reads the authorization header, splits out the scheme and token, checks that the scheme is bearer and the token is non-empty, and then asks the bearer helper for the workspace claim.

**Call relations**: The sample route uses this as its synchronous identify function. `resolve_surface_workspace` also calls it for surface routes.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1132–1134)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Asynchronously resolves the workspace for a surface request. It reuses the same bearer-token rule as regular sample routes.

**Data flow**: It receives a request and surface auth object. It ignores the auth object and returns the result of `resolve_workspace`.

**Call relations**: Both sample surface specs use this as their identify function, so surface requests are scoped to a workspace before handlers run.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1137–1138)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: Provides the summarizer hook for the sample conversation slot. This sample slot has no summary work to do.

**Data flow**: It receives a conversation slot context, reads nothing, changes nothing, and returns nothing.

**Call relations**: The manifest registers this with the sample conversation slot provider. It proves a slot may exist even when summarization is empty.


##### `_conversation_slot_read`  (lines 1141–1142)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Reads the sample conversation slot contents. It returns an empty set of workspace changes.

**Data flow**: It receives a conversation slot context and returns a `WorkspaceChanges` object with no changes and `truncated` set to false.

**Call relations**: The manifest registers this as the read function for the sample conversation slot.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1145–1334)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the sample extension manifest. This is the central declaration that tells UFO which sample capabilities exist and which functions implement them.

**Data flow**: It creates a sample broker, then constructs a manifest containing tools, objects, jobs, routes, onboarding, prompt sections, agents, subagents, credentials, connectors, hooks, surfaces, sources, indexes, embeds, models, hubs, terminals, skills, browser providers, carriers, auth proxies, search providers, memory search, and conversation slots.

**Call relations**: The host application calls this when loading the extension. Every other handler and class in the file is wired into the system through this manifest.

*Call graph*: 37 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


### User-facing surface manifests
UFO and web extension manifests expose user-facing host surfaces, routes, tools, jobs, slots, and browser behavior.

### `extensions/ufo/ufo_ext_ufo/manifest.py`

`config` · `startup`

This file is the manifest for the `ufo` extension. A manifest is like a label on a plug-in: it says “my name is this, my version is this, and these are the places where the host can connect to me.” Without this file, the extension might have working code elsewhere, but the main system would not know how to expose it.

The file declares two simple constants, `NAME` and `VERSION`, then defines one function, `manifest`, that builds a `Manifest` object. That object contains one `SurfaceSpec`, which describes the extension’s live surface. A surface is the part of the extension the outside world can reach. In this case, it points to routes imported from `ufo_ext_ufo.surface`, names the surface with `SURFACE_UFO`, and uses `resolve_workspace` to identify the workspace or context for a request.

An important detail from the file comment is that this extension does not define extra credential slots or configuration switches. Installing it is enough to mount it, and authentication is expected to happen through the environment token secret used by the wider UFO system.

#### Function details

##### `manifest`  (lines 15–20)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension manifest that the host system reads when loading the `ufo` extension. It tells the host the extension’s name, version, and the single surface with its routes and workspace identification function.

**Data flow**: It starts with the file’s fixed `NAME` and `VERSION` values, plus imported surface details such as the route list, surface name, and workspace resolver. It wraps those into a `SurfaceSpec`, then wraps that surface specification into a `Manifest`. The result is a complete description object that the rest of the system can use to mount the extension.

**Call relations**: When the extension loader asks this file for its manifest, this function creates the needed objects by calling `SurfaceSpec.__init__` for the reachable surface and `Manifest.__init__` for the whole extension description. It hands the finished manifest back to the loader so the extension can be registered and exposed.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/web/ufo_ext_web/manifest.py`

`config` · `startup`

This file is the web extension’s “front desk registration form.” When the larger UFO system loads extensions, it needs one clear description of what each extension adds. This manifest says that the web extension provides a browser-based surface, meaning a place where people can interact with the system through web routes. It also says the web surface should be treated as the home page, so opening the bare deployed host lands in the portal.

The manifest lists the access tools tied to the web audience, and the conversation slots the web UI uses for things like changes and artifacts. A slot is a named place where conversation-related data can be attached and later shown or used.

It also registers two scheduled background jobs. One job gives untitled conversations a readable title by summarizing their opening exchange. This applies broadly to core conversations, not only web chats, because the same conversation list may show items from Slack, CLI, and the portal together. The second job seeds agent workspaces with homepage content when they have not yet been initialized for the web extension.

There are no credential or configuration settings here. Installing the extension is enough to mount it. The member’s own session supplies access, rather than a shared bot secret.

#### Function details

##### `manifest`  (lines 34–58)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the web extension’s manifest, which is the core system’s description of what this extension adds. The system uses it to mount the web portal, expose the right tools, and schedule the web-related background jobs.

**Data flow**: It starts with constants and imported helpers that name the extension, define its web routes, describe its conversation slots, and point to job functions. It packages those pieces into a Manifest object. The result is a single structured description that the core system can read during extension loading.

**Call relations**: When the extension is discovered, the core calls this function to learn what to install. Inside, it creates a surface description for the web portal, creates job descriptions for chat-title summarizing and homepage seeding, and asks the job helpers for the right candidate workspaces: conversations needing titles and agent workspaces needing web homepage seeds.

*Call graph*: 5 external calls (__init__, __init__, __init__, unseeded_agent_workspaces, untitled_conversation_workspaces).
