# Sample extension and pack conformance scaffolding  `stage-19.3`

This stage is a proving ground for the public SDK, which is the supported toolkit outsiders use to add features. It is not part of the main product work loop. It is behind-the-scenes scaffolding used during development, testing, and startup checks to make sure extensions, packs, skills, and loading rules still work together.

The sample extension is the main “demo machine.” It uses only the public ufo.sdk package and tries many extension points: tools, background jobs, web routes, hooks, object types, connectors, search, model, sandbox, browser, and user-facing surface features. This shows that an extension can plug into all these places without private shortcuts.

The sample pack wraps that extension into a pack, which is a bundle that can install related pieces together. It also adds a skill and an onboarding action that writes a real stored record, proving packs can do useful setup work.

The skill probe is the smallest check: run it, and it prints success. Together, these files act like test plugs that confirm the whole extension-and-pack path is wired correctly.

## Files in this stage

### Sample SDK Conformance
Sample probe, extension, and pack files demonstrate that public SDK extension, skill, and pack loading paths work end to end.

### `extensions/sample/skills/sample_skill/probe.py`

`entrypoint` · `startup or probe check`

This file acts like a quick “is this thing alive?” test for the sample skill. It does not define any reusable code or perform the real work of a skill. Instead, as soon as Python runs the file, it prints the message `sample-skill-probe-ok`.

That message is useful because it gives a clear signal that the file was reached, Python could execute it, and the sample skill’s basic wiring is present. Without a small probe like this, a broken path, missing package, or failed launch might be harder to distinguish from a skill that simply did nothing visible.

An everyday analogy is a doorbell test: pressing the button does not prove the whole house works, but if you hear the chime, you know the button, wiring, and bell are connected well enough for a basic check.


### `extensions/sample/ufo_ext_sample.py`

`domain_logic` · `cross-cutting conformance extension: registration at startup, then active during tools, jobs, routes, hooks, surfaces, search, indexing, and sandbox/browser/model tests`

Think of this file as a test showroom for the extension system. Instead of talking to real outside services, it returns small fixed answers and records what happened in the extension’s durable store. That lets the project’s tests check the same paths a real extension would use, without depending on a live API, browser, search engine, or container service. The file defines a manifest, which is the extension’s menu of offerings: tools agents can call, scheduled jobs, HTTP routes, onboarding steps, prompt text, connector support, hooks that observe or block events, UI-like surfaces, source sync, indexing, embeddings, model backends, and more. Most handlers do one simple thing, such as echoing input, saving a note, returning a canned search result, or minting a fake browser endpoint. The important part is not the sample data itself; it is that every feature travels through the real public seams. If this file were missing, the project would lose a broad conformance probe: a compact, installed extension that catches breakage when the SDK contract changes.

#### Function details

##### `_echo`  (lines 232–236)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: This is the sample echo tool. It records the message it was given in the extension’s durable store, then returns the same message to the caller.

**Data flow**: It receives a tool context and an input object containing a message. It checks that the tool was given an extension context, saves the message as JSON-like data under the sample tool key, and returns a tool result containing that text.

**Call relations**: The manifest exposes this as the main sample tool. A pre-tool hook in this same file can deliberately block it, so tests can prove that denied tools do not reach this handler.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 239–261)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: This tool proves that an extension can own and use its own database table. It writes a note for the current workspace and reads it back.

**Data flow**: It receives note text and the current tool context. It opens a workspace-scoped transaction, updates the note row if it exists or inserts it if it does not, selects the stored note, and returns that text as the tool output.

**Call relations**: The manifest exposes this as a second tool, and scheduled-job candidate selection also reads from the same note table. It demonstrates the migration and database access path for extensions.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 264–280)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the sample scheduled job. It records that the job ran and, when a corpus is available, proposes a small change to an existing agent prompt.

**Data flow**: It receives an extension context. It writes a job-ran marker, asks for available trajectories, stores their count, and if one exists, builds an agent-change proposal that appends a short suffix to that agent’s prompt.

**Call relations**: The manifest registers this as the sample job. It calls the extension context’s trajectory and proposal features so tests can verify scheduled jobs can see agent history and request prompt changes.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 283–286)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the sample HTTP route handler. It records the request body and echoes it back as plain text.

**Data flow**: It receives an extension context and a request. It reads the raw request body, decodes it to text, saves that text in the extension store, and returns the same text in a plain text response.

**Call relations**: The manifest exposes this route behind workspace identification. It proves that extension routes can receive authenticated requests and write to the extension store.

*Call graph*: 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 311–321)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: This lists sample widgets saved in the extension store. It supports simple name filtering and page-by-page browsing.

**Data flow**: It receives a tool context, a search query, and a cursor. It reads widget keys from the extension store, strips the storage prefix to get widget names, filters by the query, slices one page after the cursor, and returns object rows plus a next cursor if more remain.

**Call relations**: The object system calls this when someone lists objects of the sample widget kind. It uses WidgetStore._ext to get the extension context safely.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, __init__).


##### `WidgetStore.get`  (lines 323–325)

```
async def get(self, ctx: ToolContext, name: str) -> WidgetSpec | None
```

**Purpose**: This retrieves one saved sample widget by name. It turns stored JSON-like data back into the typed widget shape.

**Data flow**: It receives a tool context and widget name. It reads the matching store key, returns nothing if there is no row, or validates the stored value as a WidgetSpec and returns it.

**Call relations**: The object system calls this for the get/read verb. It relies on WidgetStore._ext to reach the extension’s durable store.

*Call graph*: calls 1 internal fn (_ext).


##### `WidgetStore.status`  (lines 327–328)

```
async def status(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This reports extra status for a widget, but the sample widget has no extra status. It always answers with nothing.

**Data flow**: It receives the tool context and widget name. It does not read or change anything and returns None.

**Call relations**: The object system may call this when it wants status information. Here it exists to satisfy the object-store protocol while keeping widgets simple.


##### `WidgetStore.apply`  (lines 330–333)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None) -> None
```

**Purpose**: This creates or updates a sample widget. It saves the typed widget settings under the widget’s name.

**Data flow**: It receives a tool context, widget name, new spec, and possibly the old spec. It converts the new spec to plain data and writes it into the extension store under the widget key.

**Call relations**: The object system calls this for create and update operations. It uses WidgetStore._ext to get the extension context before writing.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (model_dump).


##### `WidgetStore.delete`  (lines 335–338)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This deletes a sample widget, but only if the speaker is the workspace owner. It demonstrates permission checks on object deletion.

**Data flow**: It receives a tool context and widget name. It asks whether the current speaker is the owner; if not, it raises an owner-required error. If allowed, it deletes the widget key from the extension store.

**Call relations**: The object system calls this for the delete verb. It combines ToolContext.speaker_is_owner with WidgetStore._ext so tests can prove owner-only deletion is enforced.

*Call graph*: calls 2 internal fn (speaker_is_owner, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 340–343)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This small helper gets the extension context from a tool context. It fails loudly if the object store was called without the context it needs.

**Data flow**: It receives a tool context. If the context has no extension attached, it raises an error; otherwise it returns that extension context.

**Call relations**: WidgetStore.list, get, apply, and delete call this before using the extension store. It keeps the safety check in one place.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 351–354)

```
async def list(self, ctx: ToolContext, query: str, cursor: str) -> ObjectPage
```

**Purpose**: This lists the sample relic objects. The sample has exactly one canned relic, and it appears only when the query matches or is empty.

**Data flow**: It receives a tool context, query, and cursor. It ignores paging because there is only one relic, filters by the relic name when a query is present, and returns an object page containing either the relic row or no rows.

**Call relations**: The object system calls this for listing the read-only relic kind. It contrasts with WidgetStore, which supports full create, update, and delete.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.get`  (lines 356–357)

```
async def get(self, ctx: ToolContext, name: str) -> RelicSpec | None
```

**Purpose**: This retrieves the one sample relic. It returns a fixed inscription when the requested name is the relic’s name.

**Data flow**: It receives a tool context and object name. If the name matches the canned relic, it returns a RelicSpec; otherwise it returns nothing.

**Call relations**: The object system calls this for read operations on relics. It helps prove system-produced, read-only object kinds can still be listed and fetched.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.status`  (lines 359–360)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This reports fixed status for a relic. It marks the relic as coming from an “excavated” origin.

**Data flow**: It receives a tool context and relic name. It returns a small status dictionary and does not read or write storage.

**Call relations**: The object system may call this after fetching a relic. It supplies a tiny example of extra object status data.


##### `RelicStore.apply`  (lines 362–365)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None) -> None
```

**Purpose**: This refuses every attempt to create or update a relic. Relics in this sample are read-only.

**Data flow**: It receives a tool context, name, new spec, and old spec. Instead of saving anything, it raises a verb-not-supported error with the sample refusal message.

**Call relations**: The object system calls this when someone tries to apply a relic spec. The refusal proves that object kinds can be readable but not writable.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 367–368)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This refuses every attempt to delete a relic. It keeps the sample relic read-only from every mutation path.

**Data flow**: It receives a tool context and name. It does not delete anything and raises a verb-not-supported error.

**Call relations**: The object system calls this for relic deletion attempts. Together with RelicStore.apply, it proves mutation refusal is surfaced cleanly.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 387–392)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This produces one deterministic source page from the sample source configuration. It proves an extension can contribute a content source for memory and indexing.

**Data flow**: It receives typed source config, an optional cursor, and source authentication. It hashes the configured topic to make a digest, builds one page containing that topic, and returns it with no next cursor.

**Call relations**: The onboarding step registers this source, and the core source-sync runner later calls fetch. The returned page can then flow into memory and index systems.

*Call graph*: 3 external calls (__init__, __init__, sha256).


##### `SampleIndex.upsert`  (lines 405–407)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This adds or replaces chunks in the sample in-memory index. A chunk is a searchable piece of text plus metadata.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it in a dictionary keyed by its digest, replacing any older chunk with the same digest.

**Call relations**: The index backend interface calls this when content is indexed. Later lexical or vector searches read from the same dictionary.


##### `SampleIndex.delete`  (lines 409–411)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes all indexed chunks that belong to one owner scope. A scope is the owner kind and owner id that define what content is being cleared.

**Data flow**: It receives an index scope. It finds stored chunks whose owner kind and owner id match that scope, then deletes those chunks from the in-memory dictionary.

**Call relations**: The index backend interface calls this when a whole scope should be removed. It uses _in_scope to keep the scope test consistent with pruning.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 413–419)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This removes old chunks in a scope while keeping a specified set. It is like cleaning a shelf while leaving the books on a keep list.

**Data flow**: It receives a scope and a set of digests to keep. It scans stored chunks, and for chunks in the scope whose digest is not in the keep set, it deletes them.

**Call relations**: The index backend interface calls this after re-syncing content. It uses _in_scope to identify which chunks belong to the affected owner.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 421–430)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches indexed text using simple word counting. It ranks chunks higher when the query terms appear more often.

**Data flow**: It receives a query, allowed subjects, owner kind, and result limit. It splits the query into lowercase terms, filters chunks to the requested owner kind and subjects, counts term appearances in each chunk, converts matching chunks to hits, sorts by score, and returns the top results.

**Call relations**: Search code calls this through the index backend interface for text-based retrieval. It uses SampleIndex._scoped to filter and _hit to package results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 432–440)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This searches indexed chunks by comparing embedding vectors. An embedding is a list of numbers representing text meaning in a way a model can compare.

**Data flow**: It receives a query embedding, allowed subjects, owner kind, and result limit. It filters chunks to the requested area, computes a dot product score between each chunk embedding and the query embedding, turns positive matches into hits, sorts by score, and returns the top results.

**Call relations**: Search code calls this through the index backend interface for vector retrieval. It uses SampleIndex._scoped for filtering, _dot for scoring, and _hit for result packaging.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 442–447)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: This filters stored chunks to the owner kind and subjects a search is allowed to see. It keeps searches from mixing unrelated content.

**Data flow**: It receives a set of subjects and an owner kind. It scans the in-memory chunk dictionary and returns only chunks whose owner kind matches and whose subject is in the allowed set.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this before scoring. It is the shared visibility filter for both search styles.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 456–457)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This returns a fixed embedding vector for every input text. It is a fake embedding backend used to test selection and wiring, not semantic quality.

**Data flow**: It receives a tuple of text strings. For each text, it returns the same sample vector, producing one vector per input.

**Call relations**: The manifest registers this as an embedding backend. Core can select it and pass its vectors into the sample index’s vector search.


##### `_in_scope`  (lines 460–461)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: This checks whether a chunk belongs to a given index scope. It compares the chunk’s owner kind and owner id with the scope.

**Data flow**: It receives a chunk and an index scope. It returns true when both owner fields match, otherwise false.

**Call relations**: SampleIndex.delete and SampleIndex.prune call this while deciding which chunks to remove.

*Call graph*: called by 2 (delete, prune).


##### `_dot`  (lines 464–467)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This computes the dot product of two number vectors, a simple similarity score. If either vector is empty, it returns zero.

**Data flow**: It receives two tuples of numbers. It multiplies matching positions together, sums those products, and returns the score.

**Call relations**: SampleIndex.vector calls this to score each stored chunk against the requested embedding.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 470–479)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: This turns an indexed chunk and score into a search hit. A hit is the result object returned to callers.

**Data flow**: It receives a chunk and a numeric score. It copies the chunk’s identifying fields, subject, order, and text into a Hit object and attaches the score.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this after they decide a chunk matched.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 482–489)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the sample onboarding step. It records that onboarding happened and registers the sample source for later syncing.

**Data flow**: It receives an extension context. It writes an onboarding marker into the store, builds typed source config with the sample topic, and asks the context to register that source for the shared subject.

**Call relations**: The manifest lists this as an onboarding step. It connects onboarding to source sync by creating the source that SampleSource.fetch later serves.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 492–495)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook deliberately blocks the sample echo tool. It proves that a pre-tool hook can stop a tool before its handler runs.

**Data flow**: It receives a hook context. It ignores the details and returns a Deny outcome with the sample denial reason.

**Call relations**: The manifest attaches this to the pre_tool_use event for the echo tool. Because it returns Deny, _echo should not be called for that blocked path.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 498–506)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records successful tool calls. It shows that after a tool succeeds, observers can see which tool ran.

**Data flow**: It receives a hook context. If the payload is a successful post-tool event, it extracts the tool name and stores it under the post-hook key, then returns no special outcome.

**Call relations**: The manifest registers this for post_tool_use. It is the success-side counterpart to _record_post_failure.


##### `_record_post_failure`  (lines 509–515)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records failed tool calls. It proves that errors are delivered to a separate failure event instead of the success event.

**Data flow**: It receives a hook context. If the payload is a tool-failure event, it stores the failed tool name under the failure-hook key and returns no special outcome.

**Call relations**: The manifest registers this for post_tool_use_failure. Tests compare it with _record_post to verify the success/failure split.


##### `_record_stop`  (lines 518–524)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records the final answer at the end of a turn. It proves extensions can observe turn completion.

**Data flow**: It receives a hook context. If the payload is a stop event, it stores the final answer in the extension store.

**Call relations**: The manifest registers this for the stop event. It runs near the end of an agent turn, after an answer has been prepared.


##### `_record_pre_compact`  (lines 527–534)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records information before conversation compaction. Compaction means shortening stored context so it fits within model limits.

**Data flow**: It receives a hook context. If the payload is a pre-compaction event, it stores the reason and the token estimate before compaction.

**Call relations**: The manifest registers this for pre_compact. It pairs with _record_post_compact so tests can see both sides of compaction.


##### `_record_post_compact`  (lines 537–549)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records information after conversation compaction. It captures the summary and token counts before and after.

**Data flow**: It receives a hook context. If the payload is a post-compaction event, it stores the summary plus before-and-after token counts.

**Call relations**: The manifest registers this for post_compact. It complements _record_pre_compact in the compaction lifecycle.


##### `_record_page_change`  (lines 552–565)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook records page-change batches and whether a model was available in the off-turn context. It proves data-plane events reach extensions with the expected wiring.

**Data flow**: It receives a hook context. If the payload contains page changes, it stores the changed page ids and a boolean saying whether the extension context has a model attached.

**Call relations**: The manifest registers this for page_change. It is called by the page-change runner rather than by a normal agent tool turn.


##### `_SampleConnectorOAuth.authorize_url`  (lines 578–579)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the sample connector’s OAuth authorization URL. OAuth is the common web flow where a user grants access to an outside service.

**Data flow**: It receives a state value and redirect URI. It inserts both into the canned sample authorization URL and returns the full URL string.

**Call relations**: The connector setup flow calls this when it needs to send a user to the provider. The paired exchange method completes the fake connection.


##### `_SampleConnectorOAuth.exchange`  (lines 581–584)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This completes the sample OAuth flow by returning a fixed connected account id. It does not return a secret token.

**Data flow**: It receives a code, redirect URI, workspace id, and state. It ignores the code details and returns an OAuthAccount with the sample account id.

**Call relations**: The connector setup flow calls this after the provider redirects back. Later connector tools and broker credentials use the account id.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 597–598)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: This returns the sample broker’s tool catalog. The catalog contains one fake tool for listing widgets.

**Data flow**: It receives workspace id, provider name, and query text. It returns one BrokerTool with a fixed slug and description.

**Call relations**: Connector search and dynamic tool discovery call this. _SampleBroker.search also calls it while building a search response.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 600–607)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: This returns the input schema for the sample broker tool. If the requested tool slug is unknown, it reports that clearly.

**Data flow**: It receives workspace id, provider name, and tool slug. If the slug matches the sample tool, it returns a schema with an optional integer limit; otherwise it raises UnknownBrokerTool.

**Call relations**: Dynamic connector tooling calls this when it needs to know what arguments a broker tool accepts.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 609–626)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: This runs the sample broker tool by echoing the call details back. It proves connector execution wiring without doing a real provider action.

**Data flow**: It receives workspace id, provider, slug, arguments, account id, and optional idempotency key. It rejects unknown slugs, otherwise returns a dictionary containing the provider, slug, arguments, account, and idempotency key.

**Call relations**: Dynamic connector tool execution calls this after account selection. Its response can be inspected directly, and file_outputs can also project files from it.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 628–639)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: This extracts produced files from a broker response. It looks for URLs in the echoed arguments and turns them into file records.

**Data flow**: It receives the response dictionary from execution. If it contains an arguments dictionary with a list named file_output_urls, each string URL becomes a BrokerFile using the URL’s filename; otherwise it returns no files.

**Call relations**: The connector file bridge calls this after broker execution to discover files the provider produced.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 641–667)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: This prepares a workspace file upload for a connector tool. It also simulates deduplication: staging the same content twice returns no upload URL the second time.

**Data flow**: It receives workspace id, provider, slug, filename, mimetype, and md5 hash. It builds a content-addressed key; if already minted, it returns a staged upload with no put URL, otherwise it records the key and returns a file URL plus the argument data the tool should receive.

**Call relations**: Connector upload handling calls this before tool execution when a tool needs a file. The returned argument can later appear in _SampleBroker.execute.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 669–672)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: This returns broker search results: the sample tool catalog plus a one-line plan. It demonstrates searching connector capabilities.

**Data flow**: It receives workspace id, provider, and query. It calls _SampleBroker.tools to get matching tools, adds the fixed plan text, and returns a BrokerSearch object.

**Call relations**: Connector search code calls this when an agent or UI searches available provider tools.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 674–675)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This returns a fake bearer credential for a connected account. A bearer credential is a token sent with requests to prove access.

**Data flow**: It receives workspace id, provider, and account id. It prefixes the account id with the sample bearer prefix and returns it as a Credential.

**Call relations**: Connector and egress code call this when they need a provider credential for the account.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 682–700)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: This is the sample server-side connector tool. It proves a tool can resolve the connected account bound to the current agent and record the idempotency key for safe side-effecting calls.

**Data flow**: It receives a tool context and input containing a tool name. It checks for an extension context, asks the tool context for the connected account for the sample provider, stores the account, requested tool name, and idempotency key, and returns the account id as text.

**Call relations**: The manifest registers this inside the connector provider. It calls ToolContext.connector_account so missing grants fail before the sample records execution.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 710–711)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: This tiny async generator yields one byte chunk. It is used to stream a small inbound file into the workspace.

**Data flow**: It receives bytes. When iterated, it yields those same bytes once and then ends.

**Call relations**: _surface_ingest calls this when the request includes inbound text to save as a workspace file.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_ingest`  (lines 714–735)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This is the durable sample surface’s inbound route. A surface is an external entry point, such as chat, email, or another app, that can admit work into UFO.

**Data flow**: It receives a surface context and HTTP request. It parses the JSON body, links or finds a member identity, gets or creates a conversation, optionally writes inbound text as a workspace file, finds the default agent, admits a turn with an idempotency key, and returns the turn and conversation ids as JSON.

**Call relations**: The manifest registers this on the durable sample surface. It uses many SurfaceContext methods so tests can verify identity linking, conversation creation, file writing, and turn admission.

*Call graph*: calls 7 internal fn (admit, conversation_for, default_agent, link_member, linked_member, write_workspace_file, _one_chunk); 2 external calls (JSONResponse, body).


##### `_surface_post`  (lines 738–739)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: This returns a fixed external reply reference after a durable surface writeback. It stands in for posting a reply to an outside service.

**Data flow**: It receives a surface context and writeback object. It ignores their details and returns the sample post reference string.

**Call relations**: The durable surface in the manifest uses this as its post callback. _surface_attach can then attach shared artifacts to that reply reference.


##### `_surface_attach`  (lines 742–747)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: This copies shared artifacts from their blob keys to delivered blob keys. It proves attachments can be streamed out and written back.

**Data flow**: It receives a surface context, writeback, and reply reference. For each artifact on the writeback, it opens a stream from the artifact’s blob key and writes that stream into a new delivered key based on the turn id and filename.

**Call relations**: The durable surface uses this after posting. It exercises the blob store’s streaming read and write path.


##### `_surface_live_admit`  (lines 750–773)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This is the live sample surface’s admission route. It admits a turn without durable postback, then returns ownership and spending information.

**Data flow**: It receives a surface context and request. It parses the request, finds or adopts a member identity from a peer surface, gets a conversation, finds the default agent, admits a turn, reads the turn owner, reads the recent spend rollup, and returns those values as JSON.

**Call relations**: The manifest registers this on the live surface. It contrasts with _surface_ingest: live turns are meant to be tailed from the hub rather than posted back through a durable writeback row.

*Call graph*: calls 7 internal fn (admit, adopt_identity, conversation_for, default_agent, linked_member, spend_rollup, turn_owner); 2 external calls (JSONResponse, body).


##### `_surface_live_stream`  (lines 776–780)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This streams live turn frames as newline-delimited JSON. It lets a client follow a turn as it runs.

**Data flow**: It receives a surface context and request. It reads the turn id from the route path, builds a streaming response around _surface_frames, and sets the media type to newline-delimited JSON.

**Call relations**: The manifest registers this as the live surface’s stream route. It delegates frame generation to _surface_frames.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 783–785)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: This yields live frames for a turn one line at a time. Each frame is encoded as JSON plus a newline.

**Data flow**: It receives a surface context and turn id. It tails the turn through the surface context, converts each frame to JSON bytes, adds a newline, and yields it to the streaming response.

**Call relations**: _surface_live_stream calls this to provide the body of the HTTP stream. The SurfaceContext.tail method supplies the live frames.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 796–798)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This fake model client streams one text reply and one usage report. It proves the model provider path can select an extension-supplied client.

**Data flow**: It receives a model request. It yields a text delta containing the canned reply, then yields usage with one input token and one output token.

**Call relations**: The manifest’s model provider creates this client for the sample model id. Core consumes it like any other streaming model backend.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 807–808)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the browser debugging endpoint for the sample lease. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It receives no input beyond the lease object. It returns a CdpEndpoint containing the fixed sample WebSocket URL.

**Call relations**: Browser code calls this after SampleCdpProvider.lease or reattach returns a lease.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 810–811)

```
async def token(self) -> str
```

**Purpose**: This returns a durable token for reattaching to the sample browser lease. In this sample, the token is just the fixed endpoint URL.

**Data flow**: It receives no extra input. It returns the sample CDP URL string.

**Call relations**: Browser lifecycle code can store this token and later pass it to SampleCdpProvider.reattach.


##### `SampleCdpLease.aclose`  (lines 813–814)

```
async def aclose(self) -> None
```

**Purpose**: This closes the sample browser lease, but there is nothing real to close. It is a no-op.

**Data flow**: It receives no extra input. It performs no cleanup and returns None.

**Call relations**: Browser lifecycle code calls this when it is done with a lease. The method exists to satisfy the lease protocol.


##### `SampleCdpProvider.lease`  (lines 824–825)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This creates a sample browser-control lease. It returns a lease pointing to the fixed sample endpoint.

**Data flow**: It receives an optional sandbox session. It ignores it and returns a new SampleCdpLease.

**Call relations**: The manifest registers this CDP provider. Browser code calls lease when it needs a fresh browser-control connection.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 827–828)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This reconnects to a sample browser lease from a token. The sample always returns the same fixed lease.

**Data flow**: It receives a token string. It ignores the token contents and returns a new SampleCdpLease.

**Call relations**: Browser code calls this when resuming a previous browser-control session. The token usually comes from SampleCdpLease.token.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 839–840)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This returns a fixed credential for the sample auth proxy. It proves an extension can contribute an auth-proxy backend.

**Data flow**: It receives workspace id, provider, and account id. It ignores the specific values and returns a Credential with the sample bearer token.

**Call relations**: The manifest registers this auth proxy. Sync or egress code can select it by backend name and ask for a credential.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 852–860)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This returns a canned web-search result and a canned direct answer. It proves extension search providers can plug into research tools.

**Data flow**: It receives a search query. It ignores the query text and returns one SearchHit with fixed URL, title, and text, plus the fixed answer string.

**Call relations**: The manifest registers this search provider. Research/search code calls it through the provider interface.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 862–863)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This fetches a canned page for a requested URL. It proves the search provider’s fetch path is wired.

**Data flow**: It receives a fetch request containing a URL. It returns a FetchedPage with that same URL and fixed sample page text.

**Call relations**: Search tools call this after a search hit or explicit fetch request. The provider advertises that fetch is supported.

*Call graph*: 1 external calls (__init__).


##### `SampleMemorySearch.search`  (lines 872–888)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This records a scoped memory search and returns one canned memory match. Memory search means looking up stored facts or context relevant to a turn.

**Data flow**: It receives queries, an optional member id, and optional start and end datetimes. It stores those search parameters in the extension store, converting ids and times to strings when present, then returns one fact-like MemoryMatch.

**Call relations**: The manifest registers this as a memory search provider. Core calls it through the public provider seam, and tests read the recorded search back from the store.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleCarrier.create`  (lines 899–900)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This creates a fake sandbox handle. A carrier is the backend that would normally create and control a sandbox or container.

**Data flow**: It receives a sandbox spec. It returns a SandboxHandle using the spec’s conversation id and a fixed container id.

**Call relations**: The manifest registers this carrier so sandbox selection can choose an extension-provided backend.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 902–905)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int) -> ExecResult
```

**Purpose**: This fake sandbox execution echoes the command arguments. It proves the selected carrier received the command.

**Data flow**: It receives a sandbox handle, argument tuple, standard input bytes, and timeout. It joins the arguments with spaces and returns them as stdout, with empty stderr and exit code zero.

**Call relations**: Sandbox code calls this when executing a command inside the selected carrier. Tests can identify the sample carrier by the echoed output.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.export`  (lines 907–908)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: This fake export writes the requested path text into the blob store. It stands in for copying a file out of a sandbox.

**Data flow**: It receives a sandbox handle, path, blob store, and destination key. It encodes the path string as bytes and writes those bytes to the blob store at the destination key.

**Call relations**: Sandbox export code calls this when it wants to save a sandbox file as a blob. It exercises BlobStore.put through the carrier interface.

*Call graph*: calls 1 internal fn (put).


##### `SampleCarrier.destroy`  (lines 910–911)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: This destroys a fake sandbox, but there is no real container to remove. It is a no-op cleanup hook.

**Data flow**: It receives a sandbox handle. It does nothing and returns None.

**Call relations**: Sandbox lifecycle code calls this during teardown. The method exists so the sample carrier implements the full carrier protocol.


##### `SampleCarrier.host`  (lines 913–914)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: This returns a fake host address for a port exposed by the sample sandbox. It combines the fixed container id and requested port.

**Data flow**: It receives a sandbox handle and port number. It returns a string like the sample container name followed by the port.

**Call relations**: Sandbox networking code calls this when it needs a host address for a service running in the sandbox.


##### `resolve_workspace`  (lines 917–925)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: This identifies the workspace for an incoming extension route request. It reads a bearer token from the Authorization header and extracts the workspace claim.

**Data flow**: It receives an HTTP request. It parses the Authorization header, rejects missing or non-bearer tokens by returning None, and otherwise passes the token to workspace_claim to get the workspace id.

**Call relations**: The manifest uses this as the identify function for the sample route. resolve_surface_workspace also calls it for surface routes.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 928–930)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This is the async workspace identifier for sample surfaces. It reuses the same bearer-token logic as normal routes.

**Data flow**: It receives a request and surface auth object. It ignores the auth object and returns whatever resolve_workspace finds in the request.

**Call relations**: Both sample surface specs use this identify function. It delegates all parsing to resolve_workspace.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `manifest`  (lines 933–1088)

```
def manifest() -> Manifest
```

**Purpose**: This builds the extension manifest, the formal list of everything the sample extension contributes. It is the main entry point the host uses to discover this extension.

**Data flow**: It creates a sample broker, then returns a Manifest filled with tool definitions, object kinds, jobs, routes, onboarding, prompt sections, subagents, credentials, connector provider, hooks, surfaces, source/index/embed/model backends, hub, skill, browser provider, sandbox carrier, auth proxy, search provider, and memory search provider.

**Call relations**: The extension loader calls this during registration. Every other handler and class in the file is connected to the host through objects created here.

*Call graph*: 31 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


### `packs/sample_pack/ufo_pack_sample.py`

`config` · `pack discovery and onboarding`

This is a conformance sample: a deliberately simple pack that exercises the same path a real external pack would use. A pack is a bundle of extensions, skills, and setup steps that UFO can discover and activate. The important point is that this file imports only from `ufo.sdk`, the public surface that pack authors are expected to use, so it acts like a safety test for that public contract.

The file names the pack, its version, the extension it bundles, the skill folder it contributes, and the onboarding step it wants UFO to run. When the system asks this module for its pack definition, `pack()` returns a `Pack` object containing all of that information.

The onboarding step is `_setup`. It receives an `ExtensionContext`, which is the pack’s doorway into approved UFO services. Through that context it writes a small marker into the pack’s scoped store. This matters because it uses the real durable storage path, not a fake log or shortcut. In everyday terms, this file is like a sample appliance plugged into every supported socket: if any socket changes shape, the sample stops working and the project learns that the pack seam has broken.

#### Function details

##### `_setup`  (lines 25–26)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the pack’s onboarding action. It records a durable marker saying that the sample pack has been onboarded, so tests can later confirm the setup step really ran through the public store interface.

**Data flow**: It receives an `ExtensionContext`, which gives access to the pack’s scoped storage area. It writes the key `pack:onboarded` with the value `{"pack_onboarded": true}` into that store. It returns nothing, but it changes persistent stored data by adding or replacing that marker.

**Call relations**: This function is handed to an `OnboardingStep` by `pack()`. Later, when the pack onboarding flow runs that step, UFO calls `_setup` with the active extension context so the setup record is written through the same path real packs would use.


##### `pack`  (lines 29–36)

```
def pack() -> Pack
```

**Purpose**: Builds and returns the pack description that UFO reads when it discovers this sample pack. It tells UFO the pack’s name, version, bundled extension, contributed skill, and onboarding step.

**Data flow**: It reads the constants defined in this file, such as the pack name, version, skill path, and onboarding step name. It wraps the skill path in a `SkillSpec`, wraps `_setup` in an `OnboardingStep`, and places those together with the bundled extension name into a `Pack`. The result is a complete pack object that the rest of the system can load.

**Call relations**: This is the entry point UFO uses for this pack module. Inside it, the function creates a `SkillSpec` for the contributed skill, an `OnboardingStep` for the setup action, and then a `Pack` that gathers everything into one definition for the pack loader.

*Call graph*: 3 external calls (__init__, __init__, __init__).
