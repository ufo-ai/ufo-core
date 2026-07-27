# SDK contribution contracts and sample extension conformance  `stage-20.4`

This stage is shared support for extension authors. An extension is add-on code that plugs new abilities into UFO without changing the core system. The main contract lives in manifest.py. It defines the “menu form” an extension fills out to say what it offers, such as tools, web routes, jobs, credentials, connectors, hooks, skills, models, search, and sandbox support.

The SDK files are the public doorways into that contract. sdk/manifest.py exposes the manifest types from one stable place. sdk/tools.py, sdk/jobs.py, and sdk/skills.py do the same for tools, background jobs, and skills. They mostly forward names from deeper code, so extension authors do not need to know the core’s internal folder layout.

The sample extension, ufo_ext_sample.py, is the proving ground. It declares many example contributions using these public SDK paths. Tests can load it like a real extension and check that the whole path works, from declaration to runtime behavior.

## Files in this stage

### Sample Extension
A complete sample extension demonstrates the public contribution contracts through real tools, routes, hooks, sources, models, search, sandbox, browser, and surface integrations.

### `extensions/sample/ufo_ext_sample.py`

`domain_logic` · `cross-cutting`

Think of this file as a demo booth for the extension system. It does not talk to real outside services. Instead, it provides small, predictable versions of many things an extension might add: a tool that echoes text, a note writer, an HTTP route, onboarding, object stores, a connector, a model backend, a search backend, a browser endpoint provider, and a sandbox carrier. Each piece records what happened in the extension's durable store, which is a real database-backed store, not a fake test log. That matters because the conformance tests can then check whether UFO called the extension through the public software development kit, or SDK, exactly as promised. The file also includes deliberately simple backends, like an index that ranks text by word counts and a model client that always returns the same reply. These are not meant to be useful products. They are probes. If a future change breaks a public extension seam, one of these probes should fail. The manifest at the bottom is the map that tells UFO what this extension contributes and how to call each contribution.

#### Function details

##### `_echo`  (lines 241–245)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: This is the sample echo tool. It records the message it received in the extension store and then returns the same message as tool output.

**Data flow**: It receives a tool context and an input object containing a message. It checks that the tool was given an extension context, writes the message data under a known store key, and returns a text result containing the original message.

**Call relations**: UFO calls this when the sample echo tool is dispatched from the manifest. A pre-tool hook in this same file can deny this tool before it runs, which lets tests prove that denial stops the handler.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 248–270)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: This tool writes a note into the sample extension's own database table and reads it back. It proves that an extension migration-created table can be used through a workspace-scoped transaction.

**Data flow**: It receives note text and the tool context. It finds the current workspace, opens an extension transaction, updates or inserts the note for that workspace, selects the stored value back, and returns it as text.

**Call relations**: UFO calls this as the sample note tool. The manifest also grants it to the sample subagent, so tests can prove both normal tools and subagent-granted tools can reach extension-owned database data.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 273–289)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the sample background job. It records that it ran, then, when trajectory data exists, proposes a small prompt change for an agent.

**Data flow**: It receives an extension context. It writes a ran marker, optionally reads available trajectories, stores their count, and if there is at least one trajectory, creates an agent prompt-change proposal and stores the proposal id.

**Call relations**: UFO calls this through the job declared in the manifest. It exercises the job path, trajectory lookup, and agent-change proposal path from an extension.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 292–295)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the sample HTTP route handler. It records the request body and echoes it back as plain text.

**Data flow**: It receives an extension context and an HTTP request. It reads the raw request body, decodes it, stores it under a route key, and returns the same body in the response.

**Call relations**: The manifest registers this as a POST route. UFO reaches it only after the route identification function accepts the request's workspace bearer token.

*Call graph*: 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 330–341)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists sample widget objects stored by the extension. It turns stored widget rows into object-list rows that UFO's object surface can page through.

**Data flow**: It receives a tool context and a list query. It reads all extension-store keys with the widget prefix, validates each stored value, builds rows with names, summaries, and visible fields, and returns a paged result.

**Call relations**: UFO calls this when someone lists the sample widget object kind. It relies on WidgetStore._ext to get the extension context before reading the durable store.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 343–350)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: This fetches one sample widget by name. It returns the widget's saved specification and timestamps if it exists.

**Data flow**: It receives a tool context and widget name. It reads the matching prefixed store key, returns nothing if missing, otherwise validates the saved record and wraps it as an object detail.

**Call relations**: UFO calls this for object get operations on sample widgets. It uses WidgetStore._ext for access to the extension store.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 352–353)

```
async def status(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This supplies status for a sample widget, but the sample widgets have no separate status to report.

**Data flow**: It receives the tool context and object name, does not read or change anything, and returns nothing.

**Call relations**: UFO may call this as part of the object status verb. It intentionally stays empty so tests can see that a store may support objects without extra status data.


##### `WidgetStore.apply`  (lines 355–365)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None) -> None
```

**Purpose**: This creates or updates a sample widget. It preserves the original creation time on updates and refreshes the update time.

**Data flow**: It receives a tool context, widget name, new spec, and optional old spec. It reads the current stored value, chooses a creation timestamp, writes the new stored widget record, and returns no content.

**Call relations**: UFO calls this for object apply operations. It uses WidgetStore._ext to reach the extension store and is paired with list and get so tests can verify the written object later.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, now).


##### `WidgetStore.delete`  (lines 367–370)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This deletes a sample widget, but only if the speaker is the workspace owner. It proves that object actions can enforce authorization rules.

**Data flow**: It receives a tool context and widget name. It asks the context whether the speaker is the owner; if not, it raises an owner-required error, otherwise it deletes the widget's store key.

**Call relations**: UFO calls this for object delete operations. It uses the tool context's ownership check before using WidgetStore._ext to remove the row.

*Call graph*: calls 2 internal fn (speaker_is_owner, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 372–375)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small helper extracts the extension context from a tool context. It gives the widget store safe access to the extension's durable store.

**Data flow**: It receives a tool context. If the context has no extension attached, it raises an error; otherwise it returns the extension context.

**Call relations**: The widget list, get, apply, and delete methods call this before touching extension data. It centralizes the guard that the store is being run in the right environment.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 383–387)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists the sample relic objects. The relic store is read-only and always exposes one canned relic.

**Data flow**: It receives a tool context and list query, creates one object row for the canned relic, and returns it through the common object paging helper.

**Call relations**: UFO calls this when listing the sample relic object kind. It contrasts with WidgetStore by showing a system-produced object kind that users cannot modify.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 389–394)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: This fetches the one sample relic by name. It returns details only for the known relic name.

**Data flow**: It receives a tool context and requested name. If the name is not the canned relic, it returns nothing; otherwise it returns a detail object with the relic inscription.

**Call relations**: UFO calls this for object get operations on relics. Together with RelicStore.list, it proves read-only object kinds can still be browsed.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 396–397)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reports a small status for a relic. The status says the relic was excavated, meaning it came from the system rather than user authoring.

**Data flow**: It receives a tool context and relic name, does not consult storage, and returns a small status dictionary.

**Call relations**: UFO may call this through the object status verb. It supports the sample's read-only relic story without allowing writes.


##### `RelicStore.apply`  (lines 399–402)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None) -> None
```

**Purpose**: This refuses attempts to create or update relics. It proves that an object kind can be visible but not writable.

**Data flow**: It receives the attempted name and spec but does not store them. It raises a verb-not-supported error with the sample refusal message.

**Call relations**: UFO calls this if someone tries the apply verb on relics. The refusal is intentional and complements RelicStore.delete.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 404–405)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This refuses attempts to delete relics. It keeps the read-only sample relic immutable.

**Data flow**: It receives a context and name, ignores them for storage purposes, and raises a verb-not-supported error.

**Call relations**: UFO calls this if someone tries the delete verb on relics. It proves mutation refusal is surfaced through the public object API.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 424–435)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This returns one deterministic page from a sample source configuration. It simulates syncing content from an external source without needing a real external system.

**Data flow**: It receives source config, an optional cursor, and auth information. It hashes the configured topic into a digest, builds one page using that topic as body and title source, and returns it with no next cursor.

**Call relations**: UFO calls this after onboarding registers the sample source. The returned page can then flow into memory and indexing like a real synced page.

*Call graph*: 3 external calls (__init__, __init__, sha256).


##### `SampleIndex.upsert`  (lines 448–450)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This inserts or replaces chunks in the sample in-memory index. A chunk is a small piece of searchable text plus metadata.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it in a dictionary keyed by the chunk digest, replacing any older chunk with the same digest.

**Call relations**: UFO calls this through the manifest-contributed index backend. Later lexical and vector searches read from the same dictionary.


##### `SampleIndex.delete`  (lines 452–454)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This deletes all indexed chunks belonging to a requested scope. A scope identifies one owner kind and owner id.

**Data flow**: It receives an index scope. It scans the stored chunks, uses _in_scope to find matching chunks, and removes their digests from the dictionary.

**Call relations**: UFO calls this when a whole indexed scope should be cleared. The helper _in_scope keeps the matching rule shared with pruning.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 456–462)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes old chunks from a scope while keeping a named set of current chunk digests. It simulates the cleanup part of re-indexing.

**Data flow**: It receives a scope and a keep set. It scans chunks, finds those in the scope whose digests are not in the keep set, and deletes them.

**Call relations**: UFO calls this after deciding which chunks should remain. It uses _in_scope for scope matching and preserves anything explicitly listed in keep.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 464–473)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches indexed text by counting query words. It is a simple word-match search, not a sophisticated search engine.

**Data flow**: It receives query text, allowed subjects, owner kind, and a result limit. It filters chunks with _scoped, counts how often query terms appear, turns positive scores into hits, sorts by score, and returns the top results.

**Call relations**: UFO calls this through the sample index backend for text search. It uses _hit to convert internal chunks into public search-hit records.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 475–483)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches indexed chunks by comparing numeric embedding vectors. An embedding is a list of numbers representing text in a form useful for similarity search.

**Data flow**: It receives an embedding, allowed subjects, owner kind, and limit. It filters chunks with _scoped, scores each by dot product with _dot, converts positive scores to hits, sorts them, and returns the top ones.

**Call relations**: UFO calls this for vector retrieval through the index backend. It shares filtering with lexical search and shares hit construction through _hit.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 485–490)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: This filters indexed chunks to the subject and owner kind being searched. It prevents searches from seeing unrelated chunks.

**Data flow**: It receives allowed subjects and an owner kind. It scans the in-memory chunk dictionary and returns only chunks whose owner kind and subject match.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this before scoring. It is the sample index's access boundary.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 499–500)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This returns a fixed embedding vector for every input text. It proves UFO can select and call an extension-provided embedding backend.

**Data flow**: It receives a tuple of texts. It ignores the actual text content and returns the same sample vector once for each input.

**Call relations**: UFO calls this through the embed backend registered in the manifest. The sample index can then use those vectors in vector search tests.


##### `_in_scope`  (lines 503–504)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: This checks whether a chunk belongs to a particular index scope. It keeps deletion and pruning rules consistent.

**Data flow**: It receives a chunk and a scope. It compares the chunk's owner kind and owner id to the scope's owner kind and owner id, and returns true or false.

**Call relations**: SampleIndex.delete and SampleIndex.prune call this while deciding which chunks to remove.

*Call graph*: called by 2 (delete, prune).


##### `_dot`  (lines 507–510)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This computes a dot product, a simple similarity score between two number lists. If either list is empty, it returns zero.

**Data flow**: It receives two numeric tuples. It multiplies matching positions, adds the products, and returns the sum.

**Call relations**: SampleIndex.vector calls this to score each chunk against the requested embedding.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 513–522)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: This converts an internal indexed chunk into a public search hit with a score. It is the adapter between stored data and search results.

**Data flow**: It receives a chunk and numeric score. It copies the chunk's identifying fields, text, and ordinal into a Hit object and attaches the score.

**Call relations**: Both lexical and vector searches call this after calculating a positive score.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 525–532)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the sample onboarding step. It records that onboarding ran and registers the sample source for syncing.

**Data flow**: It receives an extension context. It writes an onboarded marker to the store, builds a source config with the sample topic, and registers the source under a shared subject.

**Call relations**: UFO calls this from the onboarding step declared in the manifest. It sets up SampleSource.fetch to be used later by the sync flow.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 535–538)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook refuses the sample echo tool before it runs. It proves a pre-tool hook can stop a tool call.

**Data flow**: It receives a hook context and returns a Deny outcome with a fixed reason. It does not change stored data.

**Call relations**: The manifest attaches this to the pre_tool_use event for the echo tool. When it runs, _echo should not be called.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 541–549)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records successful tool calls after they finish. It proves successful calls reach the post-tool event.

**Data flow**: It receives a hook context. If the payload is a successful post-tool-use event, it stores the tool name, then returns no special outcome.

**Call relations**: UFO calls this after successful tool dispatches because the manifest registers it for post_tool_use. Failed calls are meant to go to _record_post_failure instead.


##### `_record_post_failure`  (lines 552–558)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records tool calls that ended in error. It proves failures use a different hook event than successful calls.

**Data flow**: It receives a hook context. If the payload is a post-tool-use-failure event, it stores the failing tool name and returns no special outcome.

**Call relations**: UFO calls this on post_tool_use_failure. It complements _record_post so tests can check that success and failure are separated.


##### `_record_stop`  (lines 561–567)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records the final answer when a turn is stopping. It proves extensions can observe turn-end information.

**Data flow**: It receives a hook context. If the payload contains a stop event, it stores the answer text and returns no special outcome.

**Call relations**: UFO calls this through the stop hook declared in the manifest, near the end of a turn.


##### `_record_pre_compact`  (lines 570–577)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records information just before conversation compaction. Compaction means shortening old context so the model can keep working within its token limit.

**Data flow**: It receives a hook context. If the payload is a pre-compaction event, it stores the reason and the estimated token count before compaction.

**Call relations**: UFO calls this through the pre_compact hook. _record_post_compact later records the after side of the same kind of lifecycle.


##### `_record_post_compact`  (lines 580–592)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records information after conversation compaction. It captures the summary and before-and-after token counts.

**Data flow**: It receives a hook context. If the payload is a post-compaction event, it stores the summary, previous token estimate, and new token estimate.

**Call relations**: UFO calls this through the post_compact hook. Together with _record_pre_compact, it lets tests prove both compaction events are delivered.


##### `_record_page_change`  (lines 595–608)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records batches of page changes delivered to the extension. It also notes whether a model client was wired into the off-turn context.

**Data flow**: It receives a hook context. If the payload contains page changes, it stores their page ids and a true-or-false marker saying whether ctx.ext.model is present.

**Call relations**: UFO calls this through the page_change hook. It exercises a data-plane event, meaning an event caused by changed stored content rather than by an active user turn.


##### `_SampleConnectorOAuth.authorize_url`  (lines 621–622)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the sample connector's OAuth authorization URL. OAuth is a standard web flow where a user grants an app access to an external account.

**Data flow**: It receives a state value and redirect URI. It places both into a fixed sample authorization URL and returns the string.

**Call relations**: UFO calls this when starting the connector authorization flow. The paired exchange method completes the fake connection later.


##### `_SampleConnectorOAuth.exchange`  (lines 624–627)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This completes the sample OAuth flow by returning a connected account id. It does not return a secret token.

**Data flow**: It receives the authorization code, redirect URI, workspace id, and state. It ignores the sample code details and returns a fixed OAuth account id.

**Call relations**: UFO calls this after the authorization redirect. The connector broker later uses the account id when executing connector tools.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 640–641)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: This returns the sample connector's tool catalog. The catalog contains one canned broker tool.

**Data flow**: It receives workspace id, provider name, and search query. It returns a tuple with one tool slug and description.

**Call relations**: UFO calls this when discovering connector tools. _SampleBroker.search also calls it to include tools in a broker search result.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 643–650)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: This returns the input schema for the sample broker tool. It refuses unknown tool slugs.

**Data flow**: It receives workspace id, provider, and tool slug. If the slug is not the known sample slug, it raises an unknown-tool error; otherwise it returns the tool description and a small JSON-style input schema.

**Call relations**: UFO calls this when it needs details for a dynamic connector tool before execution.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 652–669)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: This simulates executing the sample connector tool. Instead of contacting a provider, it echoes the request details back.

**Data flow**: It receives workspace id, provider, tool slug, arguments, account id, and an optional idempotency key. It rejects unknown slugs, otherwise returns a dictionary containing all those important values.

**Call relations**: UFO calls this through the connector broker execution seam. Its echoed response lets tests check exactly what UFO sent.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 671–682)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: This extracts produced file references from a broker response. It turns echoed URLs into file records that UFO can fetch or expose.

**Data flow**: It receives a broker response dictionary. It looks inside the response arguments for a list named file_output_urls, converts string URLs into BrokerFile objects using each URL's final path name, and returns them.

**Call relations**: UFO calls this after broker execution when it needs to discover files produced by a connector tool.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 684–710)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: This stages a file upload for the sample connector. It simulates minting a workspace file location where sandbox code can put bytes.

**Data flow**: It receives workspace, provider, tool slug, filename, mimetype, and md5 hash. It builds a content-addressed key; if already staged, it returns an upload argument without a put URL, otherwise it remembers the key and returns a file URL plus the argument to pass to the tool.

**Call relations**: UFO calls this before executing connector tools that need uploaded files. The remembered set lets tests prove duplicate staging is treated as already existing.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 712–715)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: This returns a simple broker search result with the sample tool and a suggested plan. It simulates provider-side tool search.

**Data flow**: It receives workspace id, provider, and query. It calls _SampleBroker.tools to get the catalog and wraps it with a fixed plan sentence.

**Call relations**: UFO calls this when searching connector capabilities. It reuses the same tool catalog as direct tool discovery.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 717–718)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This returns a bearer credential for a connected account. A bearer credential is a token sent with requests to prove authorization.

**Data flow**: It receives workspace id, provider, and account id. It prefixes the account id with a sample token prefix and returns it as a credential.

**Call relations**: UFO calls this when it needs connector credentials for egress or provider calls.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 725–743)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: This is a server-side connector execution tool. It resolves the account bound to the current agent and records that account plus call details.

**Data flow**: It receives a tool context and connector input. It checks for an extension context, asks the tool context for the connected account for the sample provider, stores the account, requested tool name, and idempotency key, and returns the account as text.

**Call relations**: UFO calls this as the connector tool declared in the manifest. It depends on the connector account grant being present; without it, account resolution fails before useful work happens.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 753–754)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: This tiny helper turns a byte string into a one-piece async stream. It is used when writing an inbound file into the workspace.

**Data flow**: It receives bytes. When iterated, it yields those same bytes once and then ends.

**Call relations**: _surface_ingest calls this when a surface request includes inbound text that should be saved as a workspace file.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_ingest`  (lines 757–776)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This handles incoming messages on the durable sample surface. A surface is an integration point for an outside channel, such as chat or email.

**Data flow**: It reads JSON from the request, resolves or links a member identity, gets or creates a conversation, optionally writes inbound text as a workspace file, admits a new turn with an idempotency key, and returns the turn and conversation ids.

**Call relations**: UFO calls this through the sample surface route. Later _surface_post and _surface_attach cover the writeback side for responses and artifacts.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 2 external calls (JSONResponse, body).


##### `_surface_post`  (lines 779–780)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: This supplies a fixed reply reference after a surface writeback. It stands in for posting a reply to an outside channel.

**Data flow**: It receives a surface context and writeback description. It does not inspect or change them, and returns a fixed reference string.

**Call relations**: UFO calls this through the durable surface's post callback after a turn has a response to deliver.


##### `_surface_attach`  (lines 783–788)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: This copies shared artifacts from their blob-store keys into delivered blob-store keys. It proves attachment streaming works.

**Data flow**: It receives a surface context, writeback, and reply reference. For each artifact, it reads the artifact as a stream from the blob store and writes the same stream under a delivered key.

**Call relations**: UFO calls this after _surface_post when there are files to attach to a surface reply.


##### `_surface_live_admit`  (lines 791–813)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This handles incoming messages on the live sample surface. Live mode admits a turn for real-time streaming rather than durable postback polling.

**Data flow**: It reads request JSON, finds or adopts a member identity, gets or creates a conversation, admits a turn, reads the turn owner, reads recent spend totals, and returns all those values as JSON.

**Call relations**: UFO calls this through the live surface POST route. The paired stream route lets clients tail the turn's live frames.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 2 external calls (JSONResponse, body).


##### `_surface_live_stream`  (lines 816–820)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This opens a live stream of frames for one turn. It returns newline-delimited JSON, which is JSON objects separated by newlines.

**Data flow**: It reads the turn id from the route path, converts it to a UUID, creates a streaming response from _surface_frames, and sets the response media type for newline-delimited JSON.

**Call relations**: UFO calls this through the live surface GET route. It delegates the actual frame iteration to _surface_frames.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 823–825)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: This yields live turn frames from the surface hub. A hub is the in-process message channel used for real-time updates.

**Data flow**: It receives a surface context and turn id. It tails the turn, converts each frame to JSON bytes, appends a newline, and yields the bytes.

**Call relations**: _surface_live_stream uses this as the body producer for the streaming HTTP response.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 836–838)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the sample model backend's completion stream. It always emits one text reply and one usage record.

**Data flow**: It receives a model request. It yields a fixed text delta, then yields fixed token usage showing one input token and one output token.

**Call relations**: UFO calls this when the sample model id is selected from the manifest. The model spec's price can then be applied to the returned usage.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 847–848)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the sample browser debugging endpoint. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It receives no extra data. It returns a CdpEndpoint object containing a fixed websocket URL.

**Call relations**: Browser code calls this on a lease produced by SampleCdpProvider.lease or reattach.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 850–851)

```
async def token(self) -> str
```

**Purpose**: This returns a durable token for reattaching to the sample browser lease. In this sample, the token is simply the fixed URL.

**Data flow**: It receives no inputs beyond the lease. It returns the sample CDP URL string.

**Call relations**: UFO can call this when it wants to save a reattach handle for the browser session.


##### `SampleCdpLease.aclose`  (lines 853–854)

```
async def aclose(self) -> None
```

**Purpose**: This closes the sample browser lease. Because the sample owns no real browser, closing does nothing.

**Data flow**: It receives no extra data, changes nothing, and returns nothing.

**Call relations**: Browser lifecycle code may call this during cleanup after using a lease.


##### `SampleCdpProvider.lease`  (lines 864–865)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This creates a new sample browser lease. It returns a canned lease rather than launching a browser.

**Data flow**: It receives an optional sandbox session. It ignores it and returns a new SampleCdpLease.

**Call relations**: UFO calls this through the CDP provider registered in the manifest when it needs browser access.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 867–868)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This reconnects to a sample browser lease from a saved token. The sample always returns the same canned lease.

**Data flow**: It receives a token string, ignores its contents, and returns a new SampleCdpLease.

**Call relations**: UFO calls this when resuming browser access from a stored lease token.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 879–880)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This returns a fixed credential for the sample auth proxy backend. An auth proxy supplies credentials to another process without exposing them in ordinary code paths.

**Data flow**: It receives workspace id, provider, and account. It ignores their values and returns a credential with a fixed bearer token.

**Call relations**: UFO calls this through the auth proxy backend registered in the manifest.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 892–900)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This returns a canned web search result and direct answer. It proves UFO can call an extension-provided search backend.

**Data flow**: It receives a search query. It ignores the query text and returns one fixed hit plus a fixed answer string.

**Call relations**: Research or search tools call this after selecting the sample search provider from the manifest.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 902–903)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This fetches a canned page for a requested URL. It simulates the page-fetch part of a search backend.

**Data flow**: It receives a fetch request with a URL. It returns a fetched page using that same URL and fixed sample text.

**Call relations**: UFO calls this when a search provider result needs page contents and the provider says it supports fetching.

*Call graph*: 1 external calls (__init__).


##### `SampleMemorySearch.search`  (lines 912–928)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This records a scoped memory search and returns one canned memory match. Memory search looks through stored facts or past context.

**Data flow**: It receives queries, an optional member id, and optional start and end times. It stores those search parameters in the extension store, converting ids and dates to strings, then returns one fixed memory match.

**Call relations**: UFO calls this through the memory search provider declared in the manifest. Storing the parameters lets tests confirm the requested scope reached the provider.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleCarrier.__init__`  (lines 940–941)

```
def __init__(self) -> None
```

**Purpose**: This initializes the sample sandbox carrier. A carrier is the backend that creates and controls a sandbox-like environment.

**Data flow**: It receives no external inputs and creates an empty dictionary for paths and bytes written into the fake sandbox.

**Call relations**: UFO constructs this through the carrier factory declared in the manifest before using the carrier methods.


##### `SampleCarrier.create`  (lines 943–944)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This creates a fake sandbox handle. It does not start a real container.

**Data flow**: It receives a sandbox spec, copies the conversation id from it, attaches a fixed container id, and returns a sandbox handle.

**Call relations**: UFO calls this when selecting the sample carrier to create a sandbox session.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 946–949)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This simulates running a command in the fake sandbox. It echoes the command arguments as stdout.

**Data flow**: It receives a sandbox handle, an argument tuple, and a timeout. It joins the arguments with spaces and returns a successful execution result with empty stderr.

**Call relations**: UFO calls this through the carrier protocol when it wants to execute code in the selected sandbox.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 951–952)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This records bytes written to a path in the fake sandbox. It acts like a tiny in-memory filesystem.

**Data flow**: It receives a sandbox handle, path, and content bytes. It stores the bytes in the carrier's written dictionary under that path.

**Call relations**: UFO calls this when copying files into a sandbox. Tests can inspect behavior by checking what was recorded.


##### `SampleCarrier.export`  (lines 954–955)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: This simulates exporting a sandbox path into the blob store. A blob store is storage for file-like binary objects.

**Data flow**: It receives a sandbox handle, path, blob store, and destination key. It writes the path text itself as bytes into the blob store under the given key.

**Call relations**: UFO calls this when it wants to copy a sandbox file out to durable blob storage.

*Call graph*: calls 1 internal fn (put).


##### `SampleCarrier.destroy`  (lines 957–958)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: This destroys the fake sandbox. Since nothing real was created, it does nothing.

**Data flow**: It receives a sandbox handle, changes nothing, and returns nothing.

**Call relations**: UFO may call this during sandbox teardown after using the carrier.


##### `SampleCarrier.host`  (lines 960–961)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: This returns a host string for reaching a port on the fake sandbox. It uses the fixed sample container name.

**Data flow**: It receives a sandbox handle and port number. It returns a string in the form sample-container:port.

**Call relations**: UFO calls this when it needs a network address for something running in the selected carrier.


##### `resolve_workspace`  (lines 964–972)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: This identifies the workspace for an HTTP request from its bearer token. If the request is not properly authorized, it returns nothing.

**Data flow**: It reads the Authorization header, checks for the Bearer scheme and a non-empty token, then asks workspace_claim to extract the workspace id from the token.

**Call relations**: The sample route uses this as its identify function. resolve_surface_workspace also delegates to it for surface requests.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 975–977)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This is the asynchronous workspace resolver for sample surfaces. It uses the same bearer-token rule as the plain route resolver.

**Data flow**: It receives a request and surface auth object, ignores the auth object, calls resolve_workspace, and returns that workspace id or nothing.

**Call relations**: The sample surface specs use this as their identify function before dispatching surface routes.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `manifest`  (lines 980–1140)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension manifest, the single declaration that tells UFO everything the sample extension contributes. Without it, UFO would not know about the sample tools, hooks, routes, surfaces, backends, credentials, or skills.

**Data flow**: It creates a sample broker, then returns a Manifest filled with tool definitions, object kinds, jobs, routes, onboarding, prompt sections, subagents, credential slots, connector provider, hooks, surfaces, source and index backends, model spec, hub, skill, browser provider, carrier, auth proxy, search provider, and memory search provider.

**Call relations**: UFO calls this as the extension entry point during extension loading. Every other handler and backend in the file is connected to the system through this returned manifest.

*Call graph*: 32 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


### Contribution Manifest
The manifest contract defines the full menu of extension-provided capabilities that the core system can discover and consume.

### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and extension loading`

This file is mostly a set of frozen data shapes. “Frozen” means they are meant to be declared once and then read, not changed at runtime. An extension returns a Manifest, which is like a detailed shipping label for what that extension brings into the system. A pack returns a Pack, which groups extensions and pack-level setup into one product configuration.

The important idea is separation: extensions declare what they offer, and the core loader decides how to wire those declarations into the running service. For example, a manifest can declare HTTP routes to mount, background jobs to schedule, credential slots to request, connector providers to expose, hooks to run during an agent turn, or sandbox carriers to make available. Without this file, the rest of the system would not have a shared language for understanding extension capabilities.

Many of the classes here describe security boundaries. Credential injection describes how secrets are swapped into outgoing requests without giving the sandbox the real secret. Hook payloads and outcomes describe exactly when an extension may block, change, or observe agent activity. Subagent profiles describe what smaller helper agents may do and which tools they may use.

The one small function in the file checks a special connector rule: only one extension may provide a catch-all connector namespace. That prevents the system from being unable to tell which extension owns an unknown connector name.

#### Function details

##### `open_connector_namespace`  (lines 577–589)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This function finds the single open connector namespace declared by the installed manifests, if one exists. An open connector namespace is a catch-all place that can answer for connector names that were not explicitly registered, so the system must not have two of them competing.

**Data flow**: It takes a tuple of Manifest objects as input. It looks through each manifest and checks whether that manifest has a connector_resolver. If none are found, it returns None. If exactly one is found, it returns that resolver. If it finds a second one, it raises a RuntimeError so startup fails clearly instead of leaving connector routing ambiguous.

**Call relations**: During extension loading or service startup, code that builds connector support can call this after it has collected all manifests. The function does not build connectors itself; it simply enforces the rule that the catch-all connector path has one owner at most, then hands that owner back to the caller for later connector resolution.


### SDK Import Doorways
Stable SDK modules re-export job, manifest, skill, and tool building blocks so extension authors do not depend on internal package layout.

### `core/src/ufo/sdk/jobs.py`

`other` · `extension definition and job scheduling setup`

This file does not define new behavior. Its job is to make the project easier and safer to extend. Extensions need a way to describe background jobs: what the job is, and which workspaces may have work waiting. Rather than asking extension authors to import from deeper internal files, this module re-exports the approved public names in one clear place: `JobSpec`, `WorkspaceCandidates`, and `owner_candidates`.

The important idea is separation between the public “front desk” and the private machinery behind it. Like a reception desk in a building, this file tells outsiders where to go without exposing the hallways and maintenance rooms. If the internals move later, this public import path can stay the same.

The comments also explain an important pattern: an extension can declare workspace candidates through `owner_candidates`. That means it provides a database query builder that, each time the job dispatcher checks for work, selects the workspace IDs that might need the job to run. Building this query each tick matters because time-based jobs can compute “now” at that moment, not once at startup. Without this public re-export, extensions would have to depend on internal module paths, making them more fragile when the core code changes.


### `core/src/ufo/sdk/manifest.py`

`data_model` · `cross-cutting import time`

Extensions need to describe what they provide: prompts, hooks, tools, credential needs, search providers, onboarding steps, and similar pieces. Those descriptions are called a manifest, meaning a structured declaration of what an extension can do. This file exists so extension code can import all of those manifest building blocks from `ufo.sdk.manifest` instead of reaching into internal project paths like `ufo.ext.manifest` or `ufo.credentials`.

It does not create new behavior. It simply re-exports selected classes and types under the public SDK namespace. An everyday analogy is a hotel reception desk: guests do not need to know which back-office shelf stores each form; they ask at the desk, and the desk provides the right form.

This matters because it protects extension authors from internal code movement. The core project can reorganize its private modules later, while keeping `ufo.sdk.manifest` stable. Without this file, extensions would be more tightly tied to internal layout, making upgrades more fragile. The opening comment also explains an important project rule: package `__init__.py` files are kept empty, so public SDK names live in explicit modules like this one.


### `core/src/ufo/sdk/skills.py`

`other` · `cross-cutting import-time SDK access`

This module is like a clearly labeled front desk for skill-related SDK features. The real code lives deeper in the project, under `ufo.skills.runtime`, but outside users should not have to know that internal layout. Instead, they can import from `ufo.sdk.skills`, which is intended to be part of the public interface.

It exposes two things: `RuntimeSkill`, a value object that represents a skill available at runtime, and `parse_skill_content`, a helper that turns declared skill content into that runtime form. A “value object” here means a small object whose main job is to carry meaningful data, not to run a big process.

The comment at the top explains an important project rule: package `__init__.py` files are kept empty, so public SDK names are provided through specific modules like this one. Without this file, extension authors would either need to import from internal locations, which could change, or the SDK would lack a clean public doorway for skill declarations.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting; active when extension code imports the SDK tool API`

This is a small public-facing doorway into the tool system. Extensions need names like `ToolContext`, `ToolResult`, `TextContent`, `ImageContent`, `ToolDef`, and `ConnectUnavailable` in order to describe tools and write code that responds when those tools are used. Instead of asking extension authors to import those names from deeper internal files, this module re-exports them from one SDK location: `ufo.sdk.tools`.

The idea is similar to a shop putting the commonly needed items on a front counter. The items are stored elsewhere in the building, but customers should not need to know the storage-room layout. If the internal code is later reorganized, this file can keep presenting the same public names, so extensions are less likely to break.

There is no runtime logic here. It does not create tools, run tools, validate input, or talk to external services. Its job is simply to define the supported import surface for tool-related SDK code. The comment at the top also explains a project rule: `__init__.py` files are kept empty, so public SDK exports live in named modules like this one rather than in package initializer files.
