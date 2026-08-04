# Sample Extension API Coverage Module  `stage-24.5`

This stage is a built-in “test plug” for the extension system. It is not part of the main user workflow. Instead, it supports development and testing by proving that UFO’s public extension points still work. An extension point is a planned place where outside code can connect to the system, like a socket where an add-on can be plugged in.

The single file, `extensions/sample/ufo_ext_sample.py`, defines a working sample extension. It registers with many of the public hooks, commands, or capabilities that UFO exposes to extensions. When the system loads this sample, it checks whether those connection points are still available and whether an extension can use them as expected.

In practice, this file acts like a smoke test for the extension API. If it loads cleanly and its features behave correctly, developers gain confidence that recent changes have not broken extension support. It is less a reusable tool and more a broad coverage example for keeping the extension doorway healthy.

## Files in this stage

### Sample Extension API Coverage Module
### `extensions/sample/ufo_ext_sample.py`

`test` · `cross-cutting`

This file is less about providing a useful product feature and more about proving that extensions can safely plug into UFO. It declares a manifest, which is the extension’s menu of contributions: tools, background jobs, web routes, onboarding, hooks, connectors, object stores, model backends, search, memory, browser access, sandbox carriers, and more. Each piece is deliberately simple and predictable. For example, the echo tool records what it received, the search provider returns one canned result, and the model client streams one fixed reply. That predictability lets tests check whether UFO called the right extension code and passed the right data.

The file also uses real public surfaces rather than private shortcuts. When it records something, it writes through the extension store or the extension’s own database table. That matters because it tests the same paths a real third-party extension would use. Think of it like a full set of labeled sockets on a demo appliance: every socket has a tiny bulb attached, so the test can see which connections actually got power.

Without this file, the project would lose an end-to-end conformance probe for the extension SDK. Changes could accidentally break extension loading, routing, hooks, connector behavior, object verbs, or backend selection without being noticed.

#### Function details

##### `_echo`  (lines 255–259)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It saves the tool input in the extension’s durable store, then replies with the same message.

**Data flow**: It receives a tool context and validated echo arguments. It checks that the extension context is present, writes the argument data under the sample tool key, and returns a tool result containing the original message as text.

**Call relations**: The manifest registers this as the main sample tool. When core dispatches that tool, this function records the call using the same store that tests later read; a pre-tool hook can deny it before it runs.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 262–284)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Runs the sample note tool. It proves that an extension can use its own database table inside a workspace-scoped transaction.

**Data flow**: It receives note text and the tool context. It finds the current workspace, updates or inserts that workspace’s note in the sample table, reads the note back, and returns the stored text.

**Call relations**: The manifest exposes this as a second tool. It is also granted to the sample subagent, so tests can check both normal tool dispatch and extension-owned database access.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 287–309)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample background job. It records that the job ran, then optionally inspects agent trajectories, proposes a prompt change, and writes a workspace file.

**Data flow**: It receives an extension context. It writes a job marker, reads available trajectories if corpus support exists, stores their count, proposes a prompt edit for the first trajectory if present, and writes a file if workspace file access is available.

**Call relations**: The manifest registers this as the sample job. Core calls it off-turn, and it calls the extension context’s trajectory and proposal APIs to prove jobs can use those public capabilities.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 312–315)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Handles the sample HTTP route. It echoes the request body and records that the route was hit.

**Data flow**: It receives an extension context and request. It reads the raw body, stores that body under a route key, and returns the same body as plain text.

**Call relations**: The manifest registers this route with workspace identification. Core calls it for matching POST requests after resolving the workspace.

*Call graph*: 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 350–361)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists sample widgets stored by this extension. It turns stored widget records into object rows suitable for UFO’s object listing surface.

**Data flow**: It receives a tool context and list query. It reads all extension-store keys with the widget prefix, validates each stored value, builds display rows, and returns a paged object result.

**Call relations**: Core calls this through the object API when someone lists the sample widget kind. It relies on WidgetStore._ext to get the extension context.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 363–370)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one sample widget by name. It returns the stored specification and timestamps if the widget exists.

**Data flow**: It receives a context and widget name. It reads the prefixed store key, returns nothing if missing, or validates the stored row and returns an object detail.

**Call relations**: Core calls this for object get operations on sample widgets. It uses WidgetStore._ext to reach the extension store.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 372–379)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Provides status for a sample widget. In this sample, widgets have no extra live status, so it always reports nothing.

**Data flow**: It receives the context, object name, and optional generation check. It does not read or change anything and returns None.

**Call relations**: Core may call this as part of the object status verb. It intentionally leaves the full-create-update-delete path focused on stored specs.


##### `WidgetStore.apply`  (lines 381–397)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It preserves the original creation time when updating and refreshes the update time.

**Data flow**: It receives a widget name and desired spec. It reads any existing stored widget, chooses a creation timestamp, builds a new stored record with the current time, and writes it back to the extension store.

**Call relations**: Core calls this for object apply operations. It uses WidgetStore._ext to access durable extension storage.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, now).


##### `WidgetStore.delete`  (lines 399–408)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only if the speaker is a workspace admin. This tests permission failure as well as successful deletion.

**Data flow**: It receives a context and widget name. It asks whether the speaker is an admin; if not, it raises an admin-required error. If yes, it removes the widget key from the extension store.

**Call relations**: Core calls this for object delete operations. It uses ToolContext.speaker_is_admin for the permission check and WidgetStore._ext for storage.

*Call graph*: calls 2 internal fn (speaker_is_admin, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 410–413)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Extracts the extension context from a tool context. It fails loudly if the store is used without the extension context it needs.

**Data flow**: It receives a tool context. If ctx.ext exists, it returns it; otherwise it raises a runtime error.

**Call relations**: WidgetStore.list, WidgetStore.get, WidgetStore.apply, and WidgetStore.delete all call this helper before touching the extension store.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 421–425)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample read-only relic object. It always returns one canned relic row.

**Data flow**: It receives a context and list query. It creates one object row with the fixed relic name and summary, then wraps it in an object page.

**Call relations**: Core calls this through the object API for the sample relic kind. It demonstrates list behavior for a system-produced, read-only object.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 427–432)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Fetches the one sample relic if the requested name matches. It returns no result for any other name.

**Data flow**: It receives a context and name. If the name is not the fixed relic name it returns None; otherwise it builds an object detail with a fixed inscription.

**Call relations**: Core calls this for object get operations on relics. It pairs with RelicStore.list to expose the canned read-only object.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 434–441)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports live status for the sample relic. It returns a small marker saying the relic was “excavated.”

**Data flow**: It receives a context, name, and optional generation check. It ignores the stored state and returns a simple status dictionary.

**Call relations**: Core may call this through the object status verb. It shows that read-only objects can still have status information.


##### `RelicStore.apply`  (lines 443–452)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a relic. This demonstrates a read-only object kind.

**Data flow**: It receives the desired relic spec but does not store it. It raises a verb-not-supported error with the sample refusal message.

**Call relations**: Core calls this when someone tries the apply verb on relics. The function intentionally stops that flow.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 454–461)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a relic. This keeps the sample relic read-only.

**Data flow**: It receives the name and generation check, ignores them, and raises a verb-not-supported error.

**Call relations**: Core calls this when someone tries the delete verb on relics. It proves delete refusal is surfaced through the object API.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 480–489)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one deterministic page from the sample source configuration. It simulates syncing content from an external source without needing any external service.

**Data flow**: It receives typed source configuration, a cursor, and auth information. It builds one page whose body comes from the configured topic and returns it with no next cursor.

**Call relations**: The onboarding step registers this source, and core later calls fetch through the source provider seam.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 502–504)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the sample in-memory index. A chunk is a piece of text plus metadata used for later search.

**Data flow**: It receives a tuple of chunks. For each one, it stores the chunk by its digest, replacing any previous chunk with the same digest.

**Call relations**: Core calls this through the registered index backend when indexing content. Later search and pruning methods read the same in-memory dictionary.


##### `SampleIndex.delete`  (lines 506–508)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Removes all chunks belonging to a given index scope. A scope identifies the owner whose indexed content should be deleted.

**Data flow**: It receives an index scope. It finds stored chunks whose owner kind and owner id match that scope, then deletes their digests from the dictionary.

**Call relations**: Core calls this through the index backend. It uses _in_scope to make the ownership check consistent with other index operations.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 510–511)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether the sample index contains any chunks for a scope.

**Data flow**: It receives an index scope. It scans stored chunks and returns true if at least one belongs to that scope, otherwise false.

**Call relations**: Core can call this before deciding whether work is needed. It uses _in_scope for the scope test.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 513–519)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Deletes old chunks in a scope while keeping a named set of current chunks.

**Data flow**: It receives a scope and a set of digests to keep. It removes stored chunks that are in the scope but not in the keep set.

**Call relations**: Core calls this during index maintenance. It uses _in_scope to identify which chunks are eligible for pruning.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 521–530)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches indexed text by counting query words. It is a simple stand-in for full-text search.

**Data flow**: It receives a query, allowed subjects, owner kind, and limit. It narrows chunks to that owner and subjects, counts matching terms in each chunk’s text, builds hits, sorts by score, and returns the best hits.

**Call relations**: Core calls this through the index backend for keyword-style retrieval. It uses SampleIndex._scoped to filter chunks and _hit to shape results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 532–540)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches indexed chunks by vector similarity. A vector is a list of numbers used to compare meaning-like closeness.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and limit. It filters chunks, computes a dot product score against each chunk’s embedding, builds hits for positive scores, sorts them, and returns the best matches.

**Call relations**: Core calls this through the index backend for embedding search. It uses SampleIndex._scoped, _dot, and _hit.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 542–547)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters stored chunks to the owner kind and subjects a search is allowed to see.

**Data flow**: It receives a set of subjects and an owner kind. It scans the in-memory chunk dictionary and returns only matching chunks.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this before scoring, so both search modes obey the same visibility filter.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 556–557)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for each input text. It lets tests exercise embedding backend selection without calling a real model service.

**Data flow**: It receives one or more texts. It ignores the content and returns the same sample vector once per text.

**Call relations**: The manifest registers this as an embedding backend. Core calls it when it needs embeddings from the sample provider.


##### `_in_scope`  (lines 560–561)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether an indexed chunk belongs to a given owner scope.

**Data flow**: It receives a chunk and scope. It compares owner kind and owner id and returns true only when both match.

**Call relations**: SampleIndex.delete, SampleIndex.has_chunks, and SampleIndex.prune call this helper so they all use the same scope rule.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 564–567)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes a dot product between two vectors. This is the sample’s simple similarity score for vector search.

**Data flow**: It receives two number tuples. If either is empty, it returns 0; otherwise it multiplies matching positions and sums the products.

**Call relations**: SampleIndex.vector calls this for each scoped chunk to decide how strongly it matches the query embedding.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 570–579)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Turns a stored chunk and score into a search hit object.

**Data flow**: It receives a chunk and numeric score. It copies the chunk’s identifying fields and text into a Hit with that score.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this after scoring chunks, so both return the same hit shape.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 582–589)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It records onboarding and registers the sample content source.

**Data flow**: It receives an extension context. It writes an onboarding marker, builds a source config with the sample topic, and asks core to register that source for the shared subject.

**Call relations**: The manifest registers this as an onboarding step. Core calls it during extension onboarding, and later source sync can call SampleSource.fetch.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 592–595)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Refuses the sample echo tool before it runs. This proves a pre-tool hook can stop tool dispatch.

**Data flow**: It receives a hook context. It ignores the payload details and returns a Deny outcome with a fixed reason.

**Call relations**: The manifest attaches this hook to pre_tool_use for the echo tool. If core honors it, _echo is not called.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 598–606)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool use after a tool finishes. It only records post-tool payloads that represent success.

**Data flow**: It receives a hook context. If the payload is a successful PostToolUse event, it writes the tool name into the extension store, then returns no decision.

**Call relations**: Core calls this for post_tool_use events registered in the manifest. It complements _record_post_failure by covering the success path.


##### `_record_post_failure`  (lines 609–615)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed tool use after a tool errors. It proves failures go to a different hook event than successes.

**Data flow**: It receives a hook context. If the payload is PostToolUseFailure, it writes the failing tool name into the extension store and returns no decision.

**Call relations**: Core calls this for post_tool_use_failure events. Tests compare it with _record_post to confirm the split.


##### `_record_stop`  (lines 618–624)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer at the end of a turn.

**Data flow**: It receives a hook context. If the payload is a Stop event, it stores the final answer in the extension store and returns no decision.

**Call relations**: Core calls this stop hook before committing the turn’s final answer. The stored value proves the hook fired.


##### `_record_pre_compact`  (lines 627–634)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information just before conversation compaction. Compaction means shortening older context so the model can keep working within its token limit.

**Data flow**: It receives a hook context. If the payload is PreCompact, it stores the compaction reason and estimated token count before compaction.

**Call relations**: Core calls this pre_compact hook before summarizing or trimming context. It pairs with _record_post_compact.


##### `_record_post_compact`  (lines 637–649)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction finishes.

**Data flow**: It receives a hook context. If the payload is PostCompact, it stores the summary plus token counts before and after compaction.

**Call relations**: Core calls this post_compact hook after compaction. Together with _record_pre_compact it proves both sides of the event are delivered.


##### `_record_page_change`  (lines 652–665)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records page-change deliveries. It also checks whether the off-turn context includes a model client.

**Data flow**: It receives a hook context. If the payload is a PageChangeBatch, it stores the changed page ids and whether ctx.ext.model is present.

**Call relations**: Core calls this for page_change hooks. It lets tests confirm the data-plane runner delivered page ids and wired the off-turn context correctly.


##### `_SampleConnectorOAuth.authorize_url`  (lines 678–679)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the sample connector’s OAuth authorization URL. OAuth is the standard browser handoff where a user grants access to another service.

**Data flow**: It receives a state value and redirect URI. It formats them into the fixed sample authorization URL and returns that string.

**Call relations**: The connector provider exposes this object through the manifest. Core calls this when starting the sample connector authorization flow.


##### `_SampleConnectorOAuth.exchange`  (lines 681–684)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the sample OAuth flow by returning a fixed connected account id. It does not return a real secret.

**Data flow**: It receives the code, redirect URI, workspace id, and state. It ignores the values and returns an OAuthAccount with the sample account id.

**Call relations**: Core calls this after the OAuth redirect. The returned account id is later used by connector tools and broker credentials.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 697–698)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample broker’s tool catalog. A broker is the connector-side service that knows which remote actions exist.

**Data flow**: It receives workspace, provider, and query values. It returns one BrokerTool describing the sample widget-listing action.

**Call relations**: Core can call this to discover connector tools, and _SampleBroker.search calls it when returning a search plan.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 700–707)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool. It refuses unknown tool slugs.

**Data flow**: It receives workspace, provider, and slug. If the slug is not the sample slug it raises UnknownBrokerTool; otherwise it returns a BrokerTool with a small JSON input schema.

**Call relations**: Core calls this when it needs details for a dynamic connector tool. The unknown-tool branch proves error handling.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 709–726)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Simulates running a connector broker tool. It echoes the call details back as the provider response.

**Data flow**: It receives workspace, provider, slug, arguments, account id, and idempotency key. It rejects unknown slugs; otherwise it returns a dictionary containing those call details.

**Call relations**: Core calls this when executing a dynamic connector action through the broker. The echoed response lets tests verify exactly what core sent.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 728–739)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Turns file output URLs from a broker response into produced file records.

**Data flow**: It receives the response dictionary. It looks inside the echoed arguments for file_output_urls, filters string URLs, derives filenames from the URL paths, and returns BrokerFile objects.

**Call relations**: Core calls this after broker execution to discover files the remote tool produced. It uses the response shape returned by _SampleBroker.execute.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 741–767)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Prepares a workspace-backed upload slot for connector tools. It also simulates deduplication when the same content has already been staged.

**Data flow**: It receives workspace, provider, slug, filename, MIME type, and MD5 hash. It builds a content-addressed key; if already minted, it returns metadata without a put URL, otherwise it records the key and returns a file URL plus the argument to pass to the tool.

**Call relations**: Core calls this before executing connector tools that need file uploads. The returned staged upload tells the sandbox where to put bytes and what argument to send.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 769–772)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a broker search result with the sample tool and a canned plan.

**Data flow**: It receives workspace, provider, and query. It calls _SampleBroker.tools to get the available tool and wraps it with a fixed plan string.

**Call relations**: Core calls this when searching connector capabilities. It reuses _SampleBroker.tools so discovery and search agree.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 774–775)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a bearer credential for the connected account. A bearer credential is a token sent with requests to prove access.

**Data flow**: It receives workspace, provider, and account id. It returns a Credential whose bearer string includes the account id.

**Call relations**: Core calls this when it needs an account credential from the broker, such as for egress proxy or connector execution flows.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 786–804)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector’s server-side tool. It resolves which connected account is bound to the current agent and records the call.

**Data flow**: It receives a tool context and connector execute arguments. It gets the account id for the sample connector, stores the account, requested tool name, and idempotency key, then returns the account id as text.

**Call relations**: The manifest registers this as a connector-provided tool. It calls ToolContext.connector_account, so an agent without the needed connector grant fails before execution.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 814–815)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps bytes as a one-piece async stream. This lets code that expects streaming input receive a small in-memory value.

**Data flow**: It receives bytes. It yields those same bytes once and then finishes.

**Call relations**: _surface_ingest calls this when writing optional inbound text into the workspace file store.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_ingest`  (lines 818–845)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles inbound messages for the sample durable surface. A surface is an external channel, like chat or email, that can admit turns into UFO.

**Data flow**: It reads JSON from the request, links or finds a member identity, gets or creates a conversation, optionally writes an inbound file, admits a turn with an idempotency key, and returns turn and conversation ids plus whether a run was opened.

**Call relations**: The manifest registers this route on the durable sample surface. It uses SurfaceContext methods for identity, conversation, workspace file, and turn admission, and calls _one_chunk for file streaming.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 848–849)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed external reply reference for a surface writeback.

**Data flow**: It receives a surface context and writeback description. It ignores the contents and returns the sample posted-reference string.

**Call relations**: The durable sample surface registers this as its post callback. Core calls it when sending an answer back through that surface.


##### `_surface_attach`  (lines 852–857)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies shared artifacts into delivered blob keys. This proves attachments can be streamed out and stored again.

**Data flow**: It receives a writeback containing artifacts. For each artifact, it builds a delivered key and streams bytes from the artifact blob key into that delivered location.

**Call relations**: Core calls this after surface posting when there are files to attach. It exercises blob get-stream and put-stream behavior through the surface context.


##### `_surface_live_admit`  (lines 860–882)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles inbound messages for the sample live surface. Live surfaces stream turn events instead of relying on durable writeback rows.

**Data flow**: It reads JSON from the request, finds or adopts a member identity, gets a conversation, admits a turn, reads the turn owner and spend rollup, and returns those details as JSON.

**Call relations**: The manifest registers this route on the live sample surface. It uses SurfaceContext identity, conversation, admission, owner, and spend APIs to prove live-mode wiring.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 885–889)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts a live event stream for a turn. It returns newline-delimited JSON frames, which are JSON objects separated by newlines.

**Data flow**: It reads the turn id from the request path, converts it to a UUID, and returns a streaming response produced by _surface_frames.

**Call relations**: The manifest registers this GET route on the live surface. It hands off to _surface_frames to actually tail the hub.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 892–894)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Streams live frames for a turn from the surface hub.

**Data flow**: It receives a surface context and turn id. It tails the turn, converts each frame to JSON bytes, adds a newline, and yields it.

**Call relations**: _surface_live_stream calls this to provide the body of the streaming response. It relies on SurfaceContext.tail for the event feed.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 905–907)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Simulates a model completion stream. It emits one text delta and one usage report.

**Data flow**: It receives a model request. It yields a fixed text reply, then yields fixed token usage showing one input and one output token.

**Call relations**: The manifest registers a model spec that builds this client. Core calls complete when selecting the sample model backend.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 917–918)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the sample browser debugging endpoint. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It receives no extra data. It returns a CdpEndpoint with the fixed sample WebSocket URL.

**Call relations**: Core browser code calls this on a lease from SampleCdpProvider. It proves endpoint retrieval through the CDP lease protocol.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 920–921)

```
async def token(self) -> str
```

**Purpose**: Returns a reattach token for the browser lease. In this sample, the token is just the fixed endpoint URL.

**Data flow**: It receives no extra data and returns the sample CDP URL string.

**Call relations**: Core can store this token and later pass it to SampleCdpProvider.reattach.


##### `SampleCdpLease.place_file`  (lines 923–924)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Pretends to place a file for browser upload and returns the same path. No actual copy is needed in this sample.

**Data flow**: It receives a path and a byte-reader callback. It ignores the reader and returns the original path unchanged.

**Call relations**: Browser-driving code calls this through the CDP lease protocol when it needs a file available to the browser.


##### `SampleCdpLease.download_dir`  (lines 926–927)

```
async def download_dir(self) -> str
```

**Purpose**: Reports the sample download directory path.

**Data flow**: It receives no extra data and returns the fixed download directory string.

**Call relations**: Core browser code can call this to know where downloads should appear for the sample lease.


##### `SampleCdpLease.fetch_download`  (lines 929–930)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads downloaded file bytes from the sample download directory.

**Data flow**: It receives a download id, treats it as a filename inside the sample download directory, reads that file’s bytes in a worker thread, and returns the bytes.

**Call relations**: Core calls this through the CDP lease when retrieving a browser download. It uses asyncio.to_thread so file reading does not block the async loop.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 932–933)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample CDP lease. There is nothing real to clean up, so it does nothing.

**Data flow**: It receives no extra data, changes nothing, and returns None.

**Call relations**: Core calls this when it is done with a browser lease. The no-op still proves the close method exists on the protocol.


##### `SampleCdpProvider.lease`  (lines 943–944)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a new sample CDP lease.

**Data flow**: It receives an optional sandbox session. It ignores it and returns a new SampleCdpLease.

**Call relations**: Core calls this provider method when selecting the manifest-contributed CDP backend for a new browser session.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 946–947)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reconnects to a sample CDP lease from a token.

**Data flow**: It receives a token. It ignores the token value and returns a new SampleCdpLease pointing at the same fixed endpoint.

**Call relations**: Core calls this when reattaching to a browser session using the token returned by SampleCdpLease.token.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 958–959)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed bearer credential for the sample auth proxy backend.

**Data flow**: It receives workspace, provider, and account id. It ignores them and returns a Credential with the fixed sample bearer value.

**Call relations**: The manifest registers this auth proxy. Core calls it when it needs credentials from the selected auth-proxy backend.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 971–979)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns a canned web search response. It includes both a hit and a direct answer.

**Data flow**: It receives a search query. It ignores the query text and returns one SearchHit with fixed URL, title, and text, plus a fixed answer string.

**Call relations**: Core research tools call this through the registered search provider seam. It proves provider selection and result shaping.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 981–982)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Returns a canned fetched page for a URL.

**Data flow**: It receives a fetch request. It copies the requested URL into a FetchedPage and fills in fixed sample text.

**Call relations**: Core calls this when a search provider supports fetching. The supports_fetch flag on the class advertises that capability.

*Call graph*: 1 external calls (__init__).


##### `SampleMemorySearch.search`  (lines 991–1009)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match.

**Data flow**: It receives queries, a source reader with subjects, and optional start and end times. It stores the queries, subjects, and time bounds in the extension store, then returns one fixed memory match.

**Call relations**: The manifest registers this memory search provider. Core calls it for memory retrieval, and the stored record lets tests inspect what scope was passed.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1011–1012)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list.

**Data flow**: It receives no extra data and returns a tuple containing the sample memory kind.

**Call relations**: Core can call this before listing recent memory items to know what kinds are available.


##### `SampleMemorySearch.list_recent`  (lines 1014–1040)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a request for recent memory and returns one canned match in a listing page.

**Data flow**: It receives subjects, limit, optional kinds, and optional cursor. It serializes those request details into the extension store, then returns a ListingPage containing one fixed memory match.

**Call relations**: Core calls this through the memory search provider when listing recent items. It complements SampleMemorySearch.search by testing listing and cursor data.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1052–1053)

```
def __init__(self) -> None
```

**Purpose**: Initializes the sample sandbox carrier’s in-memory file storage.

**Data flow**: It receives no inputs besides the new instance. It creates an empty dictionary where written file bytes will be kept by path.

**Call relations**: The manifest registers SampleCarrier as a carrier factory. Core creates it when selecting the sample sandbox backend.


##### `SampleCarrier.create`  (lines 1055–1060)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a fake sandbox handle. It does not start a real container.

**Data flow**: It receives a sandbox spec. It returns a SandboxHandle using the spec’s conversation id and run token plus the fixed sample container id.

**Call relations**: Core calls this through the carrier protocol when creating a sandbox. The returned handle is used by exec, write, read, and dial.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1062–1069)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Attaches to a fake existing sandbox when a resume id is available.

**Data flow**: It receives a sandbox spec. If there is no resume id, it returns None; otherwise it returns a SandboxHandle using that resume id as the container id.

**Call relations**: Core calls this when trying to resume a sandbox. It tests both “cannot attach” and “attached” paths.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1071–1074)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Pretends to run a command in the sandbox by echoing the command arguments.

**Data flow**: It receives a sandbox handle, argv tuple, and timeout. It joins the arguments into stdout and returns an ExecResult with empty stderr and exit code 0.

**Call relations**: Core calls this through the carrier protocol for command execution. The echoed stdout proves this carrier was selected.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1076–1077)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Stores file bytes in the fake sandbox carrier.

**Data flow**: It receives a handle, path, and content bytes. It saves the bytes in the carrier’s in-memory dictionary under that path.

**Call relations**: Core calls this when copying files into the sandbox. SampleCarrier.read can later return the same bytes.


##### `SampleCarrier.read`  (lines 1079–1082)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams file bytes back from the fake sandbox carrier.

**Data flow**: It receives a handle and path. If the path was never written, it raises FileNotFoundError; otherwise it yields the stored bytes once.

**Call relations**: Core calls this when reading files from the sandbox. It pairs with SampleCarrier.write.


##### `SampleCarrier.dial`  (lines 1084–1085)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a fake network target for a sandbox port.

**Data flow**: It receives a handle and port. It returns a DialTarget whose host is the sample container name plus that port, with TLS disabled.

**Call relations**: Core calls this through the carrier protocol when it needs to connect to a service inside the sandbox.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1088–1096)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace for a normal extension route from a bearer token. A bearer token is an authorization string sent in the request header.

**Data flow**: It receives a request. It reads the Authorization header, requires the Bearer scheme and a non-empty token, then asks workspace_claim to extract the workspace id; otherwise it returns None.

**Call relations**: The route spec uses this as its identify function. resolve_surface_workspace also calls it so surface routes share the same workspace resolution rule.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1099–1101)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies the workspace for a surface route. It uses the same bearer-token logic as normal extension routes.

**Data flow**: It receives a request and surface auth object. It passes the request to resolve_workspace and returns that result.

**Call relations**: Surface specs register this as their identify function. It delegates to resolve_workspace to keep route and surface behavior aligned.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `manifest`  (lines 1104–1264)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the sample extension manifest. The manifest is the contract that tells UFO everything this extension contributes.

**Data flow**: It creates the sample broker, then constructs a Manifest containing tools, objects, jobs, routes, onboarding, hooks, surfaces, sources, indexes, embeddings, model backend, hub, skill, browser provider, carrier, auth proxy, search provider, and memory provider.

**Call relations**: UFO calls this entry point when loading the extension. The returned manifest wires most other functions and classes in this file into the core system.

*Call graph*: 32 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).
