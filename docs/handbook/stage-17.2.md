# Extension declaration, runtime SDK façades, and conformance sample  `stage-17.2`

This stage is the public front door for people who write UFO extensions. It is mostly shared support used before and during startup, when the system reads extension declarations and wires their features into the main application. The core piece is `manifest.py`, which defines the “manifest”: a set of fixed data objects, meaning values that describe an extension but are not changed later. An extension uses these objects to announce what it provides, such as tools, routes, jobs, search backends, browser providers, hooks, skills, and credentials.

The `ufo.sdk.*` files are safe import doorways. They let extension authors use names like jobs, manifests, skills, and tools without depending on the project’s internal folder layout. They are like labeled sockets on the outside of a machine: the inside can be rearranged, while outside code keeps plugging into the same place.

The sample extension ties it together. It declares small examples of many extension points, giving tests and new developers a working model of the public API end to end.

## Files in this stage

### Extension declarations
Defines the frozen manifest data objects that extensions and packs use to declare their capabilities.

### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and extension loading`

This file is like the contract form an extension fills out before UFO starts using it. Instead of letting an extension directly change global state, the extension returns a Manifest: a plain bundle of declarations. Core code can then read that bundle and decide how to wire the extension into the running system. That keeps startup predictable and makes missing or conflicting pieces fail early.

Most of the file is made of frozen dataclasses, meaning simple value objects that are not meant to be changed after creation. Each one describes one kind of contribution. For example, CredentialSlot says what secret an extension needs; InjectionTarget says how a secret can be safely swapped into outgoing network traffic without exposing it inside the sandbox; RouteSpec describes an HTTP endpoint; JobSpec describes scheduled work; HookSpec describes a reaction to a turn event; and SubagentProfile describes a reusable child-agent setup.

The same pattern is used for pluggable backends: sandbox carriers, browser CDP providers, search providers, embedding backends, index backends, hubs, and auth proxies. A Pack is similar, but it groups installed extensions plus pack-level skills and onboarding steps into one named product setup.

The two helper functions at the end derive shared views from these declarations: one finds the single allowed open connector namespace, and one flattens declared credential slots for the rest of the system.

#### Function details

##### `open_connector_namespace`  (lines 584–596)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This function finds the one catch-all connector namespace declared by the active extensions. It exists because there can only be one fallback owner for connector provider names that were not explicitly registered.

**Data flow**: It receives the active manifests. It scans them one by one, ignoring manifests with no connector resolver. If it finds one resolver, it remembers it. If it later finds a second one, it stops startup by raising an error, because the system would not know which resolver should answer unknown connector names. It returns the single resolver it found, or None if no extension declared one.

**Call relations**: During extension assembly, other startup code can call this after manifests have been loaded. The result becomes the shared fallback path for connect flows and connector lookup. The function does not hand work off to other helpers; it enforces the one-owner rule directly.


##### `declared_slots`  (lines 617–630)

```
def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]
```

**Purpose**: This function turns all credential declarations from active manifests into a single list the rest of the system can show or query. It is used for member-facing credential views and for representing credentials as declared objects.

**Data flow**: It receives the active manifests. For every credential slot in every manifest, it creates a DeclaredSlot containing the slot name, description, owning extension name, whether a member is allowed to fill it, and the target host if the slot has network injection information. The output is one tuple containing all of those DeclaredSlot records.

**Call relations**: After manifests are loaded, code that needs a project-wide view of required credentials calls this function. It hands each flattened credential declaration into DeclaredSlot.__init__ to create the standardized record that downstream credential and portal code can consume.

*Call graph*: 1 external calls (__init__).


### Public SDK façades
Provides stable public import doorways for extension authors so they can use manifest, job, skill, and tool APIs without depending on internal module paths.

### `core/src/ufo/sdk/jobs.py`

`other` · `extension development and job registration`

This module exists to keep the project’s public extension API tidy and stable. Extensions need a way to say, “I have a background job, and here is how core can find the workspaces where that job has work to do.” Rather than making extension authors import from deeper internal files, this module re-exports the few names they are meant to use.

The main idea is separation between the public front door and the private machinery behind it. Like a shop counter, this file exposes only the approved items: `JobSpec`, which describes a background job, and the workspace candidate tools, which help a job identify which workspaces should be considered on each scheduler tick.

The comments explain an important pattern: `owner_candidates` is used by an extension to build a database query that selects distinct workspace IDs from the extension’s own tables. Core then runs that query through a special read path that bypasses RLS, meaning row-level security rules that normally restrict which rows are visible. This is needed so the dispatcher can correctly find which workspaces have pending work before binding jobs to them.

There is no new logic here. Its value is in protecting callers from internal layout changes and making the intended SDK import path clear.


### `core/src/ufo/sdk/manifest.py`

`data_model` · `import time / extension development`

This file solves a compatibility problem. Extensions need many manifest types, such as `Manifest`, `HookSpec`, `CredentialSlot`, and provider specifications, but they should not depend on the project’s internal folder layout. This module acts like a front desk: it does not create new behavior itself, but it points callers to the right internal definitions and presents them under a public, supported address.

Without this file, extension authors might import directly from places like `ufo.ext.manifest` or `ufo.credentials`. That would make their extensions more fragile, because an internal refactor could break their imports even if the public meaning of the types stayed the same.

The file is intentionally simple. It imports each public type from its real home and re-exports it with the same name. The repeated `as SameName` style makes the public API explicit: these names are meant to be available from this module. The opening comment also explains an architectural rule: `ufo.sdk` keeps its package `__init__.py` empty, so named modules like this one carry the public surface instead.

In practical terms, this file is a stable signpost. It tells extension code, “Import your manifest building blocks here.”


### `core/src/ufo/sdk/skills.py`

`other` · `import time / cross-cutting SDK access`

This is a small “front desk” module for the SDK. The real skill code lives deeper inside the project, in `ufo.skills.runtime`, but extension authors should not have to know or depend on that internal layout. Instead, they can import `RuntimeSkill` and `parse_skill_content` from `ufo.sdk.skills`, which is clearer and more stable.

A runtime skill is a value object, meaning it represents a piece of skill information as data. The parser turns in-memory skill content into that structured form. This file simply points those public names at the real implementations.

The comment explains why this exists as a named module rather than being placed in `ufo.sdk.__init__`: this project bans code in `__init__.py` files, so public SDK entry points are exposed through small modules like this one. If this file disappeared, users could still reach the internal implementation, but they would lose the clean public import path, and future internal refactors could break their extensions more easily.


### `core/src/ufo/sdk/tools.py`

`orchestration` · `cross-cutting`

This file is a small public doorway into the tool system. Extensions need to define tools, receive tool input, and return results, but they should not have to import from deep internal modules whose paths may change. Instead, they can import names from `ufo.sdk.tools`, which is meant to stay stable.

It works like a reception desk: it does not create the objects itself, but it points users to the right official forms. It re-exports `ToolDef`, which describes a tool; `ToolContext`, which gives a tool information it needs while running; `ToolResult`, which represents what the tool sends back; and content types such as `TextContent` and `ImageContent`, which let a result contain text or images. It also re-exports `ConnectUnavailable`, an error used when a connection needed by a tool is not available.

The comment explains an important design rule: `ufo.sdk` keeps its package initializer empty, so public imports are placed in named modules like this one. Without this file, extension writers would have to depend on internal project paths, making their code more fragile when the project is reorganized.


### Conformance sample
Exercises the public extension APIs end to end by registering representative examples of the available extension points.

### `extensions/sample/ufo_ext_sample.py`

`test` · `extension load, then active during conformance-driven tool calls, jobs, routes, hooks, surface requests, and backend selection`

Think of this file as a showroom model for the extension system. It imports only `ufo.sdk`, which is the public toolkit extension authors are supposed to use, then builds a `Manifest` describing everything the extension contributes: tools, jobs, routes, hooks, object types, connectors, surfaces, model backends, search, browser sessions, sandbox carriers, and more. Most pieces return fixed, predictable answers, but they are not fake at the API boundary: they store records through the real extension store, write files through the real workspace file path, and use the same request and response objects the main system uses. That makes it useful as a conformance probe: if the core system changes in a way that breaks extension authors, this sample should fail. The sample also checks important guardrails, such as refusing undeclared credential slots, denying a tool before it runs, requiring an admin for deletion, and separating successful tool hooks from failed ones. Without this file, the project would lose a compact, installed extension that exercises the whole public seam in one place.

#### Function details

##### `_echo`  (lines 251–255)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: This is the sample echo tool. It records the message it received in the extension's durable store, then returns the same message as tool output.

**Data flow**: It receives a tool context and typed echo arguments. It checks that an extension context is present, saves the input data under the sample tool key, and returns a text result containing the original message.

**Call relations**: The manifest exposes this as the sample tool. When core dispatches that tool, this function is the endpoint, unless the sample pre-tool hook denies the call first.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 258–280)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: This tool proves an extension can use its own database table created by its migration. It writes a note for the current workspace and reads it back.

**Data flow**: It receives a tool context and note text. It opens the extension's workspace-scoped transaction, updates or inserts the note row for that workspace, reads the stored note, and returns it as text.

**Call relations**: The manifest exposes this as a second tool. It is also granted to the sample subagent, so tests can prove subagent tool grants and extension-owned database tables both work.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 283–305)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: This is the sample background job. It records that it ran, then, when possible, inspects agent trajectories, proposes a prompt change, and writes a workspace file.

**Data flow**: It receives an extension context. It writes a job marker, reads available trajectories, stores their count, optionally proposes a prompt edit for the first one, and optionally writes a file into that conversation's workspace.

**Call relations**: The manifest registers this as the sample job. Core calls it during job execution, and it hands work to extension context helpers for trajectories, proposal creation, and file writing.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 308–311)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is a tiny HTTP route handler for the extension. It records the request body and echoes it back as plain text.

**Data flow**: It receives an extension context and request. It reads the request body, saves the decoded body in the extension store, and returns the same body in a plain text response.

**Call relations**: The manifest registers this route behind a workspace-identifying function. Core calls it when a matching extension route request arrives.

*Call graph*: 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 346–357)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists the sample's writable widget objects. It turns stored widget rows into object-list rows that the object API can page through.

**Data flow**: It receives a tool context and list query. It reads all extension-store keys with the widget prefix, validates each stored value, builds human-readable rows, and returns a paged object response.

**Call relations**: The widget object kind in the manifest uses this store. Object listing calls this method, which relies on `WidgetStore._ext` to reach the extension store.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 359–366)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: This fetches one sample widget by name. It returns the saved spec and timestamps if the widget exists.

**Data flow**: It receives a tool context and widget name. It reads the matching store key, returns nothing if absent, or validates the saved value and wraps it as object detail.

**Call relations**: The object API calls this when someone asks for a specific widget. It uses `WidgetStore._ext` to require that the object call is running inside an extension context.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 368–375)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This reports no special live status for writable widgets. It exists to satisfy the object store protocol.

**Data flow**: It receives the context, name, and optional expected generation. It does not read or change anything and returns no status.

**Call relations**: Core can call this as part of the object status flow. For the sample writable object, there is intentionally no extra status to hand off.


##### `WidgetStore.apply`  (lines 377–393)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This creates or updates a sample widget. It preserves the original creation time on updates and refreshes the update time.

**Data flow**: It receives a tool context, widget name, new spec, old spec, and optional generation check. It reads the existing stored widget, chooses the correct creation timestamp, saves the new stored value, and returns nothing.

**Call relations**: The object API calls this for create and update operations. It uses `WidgetStore._ext` to access the extension store and writes the durable object row there.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, now).


##### `WidgetStore.delete`  (lines 395–404)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This deletes a widget, but only if the speaker is a workspace admin. It demonstrates permission checking on object mutations.

**Data flow**: It receives a tool context and widget name. It asks whether the speaker is an admin; if not, it raises an admin-required error. If allowed, it removes the widget key from the extension store.

**Call relations**: Core calls this for object deletion. It consults the tool context for the permission check, then uses `WidgetStore._ext` to perform the deletion.

*Call graph*: calls 2 internal fn (speaker_is_admin, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 406–409)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: This helper extracts the extension context from a tool context. It fails loudly if the widget store was called without the extension context it needs.

**Data flow**: It receives a tool context. It checks the `ext` field and returns it when present; otherwise it raises a runtime error.

**Call relations**: The widget store's list, get, apply, and delete methods call this before touching extension storage. It is the shared guardrail for those methods.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 417–421)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This lists the sample's read-only relic objects. There is always one canned relic.

**Data flow**: It receives a tool context and list query. It builds one object row for the fixed relic and passes it through the normal object paging helper.

**Call relations**: The manifest registers relics as a read-only object kind. Core calls this method when listing those objects.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 423–428)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: This fetches the one sample relic by name. It returns nothing for any other name.

**Data flow**: It receives a context and name. If the name matches the fixed relic, it returns object detail with the relic inscription; otherwise it returns no object.

**Call relations**: The object API calls this for relic lookups. It builds a typed relic spec so the read-only object still has normal object detail shape.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 430–437)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: This reports a small status payload for a relic. It shows that read-only, system-produced objects can still expose live status.

**Data flow**: It receives the context, relic name, and optional generation. It returns a fixed status dictionary saying the relic was excavated.

**Call relations**: Core may call this through the object status path. Unlike widget status, this read-only kind returns a visible status value.


##### `RelicStore.apply`  (lines 439–448)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to create or update relics. It demonstrates a read-only object kind.

**Data flow**: It receives the attempted relic change. Instead of writing anything, it raises a verb-not-supported error with the sample refusal message.

**Call relations**: Core calls this if someone tries to apply a relic spec. The method stops the mutation at the object-store boundary.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 450–457)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: This refuses attempts to delete relics. It keeps the sample relic kind fully read-only.

**Data flow**: It receives the delete request. It does not inspect storage or change state; it raises a verb-not-supported error.

**Call relations**: Core calls this for relic deletion attempts. It mirrors `RelicStore.apply` so every mutation path is refused.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 476–485)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: This produces one deterministic page from the sample source configuration. It proves source backends can supply typed content to the system.

**Data flow**: It receives typed source config, an optional cursor, and source auth. It creates one page whose body and title come from the configured topic, then returns a sync result with no next cursor.

**Call relations**: The onboarding step registers this source. Later, the source sync path calls `fetch` to pull a page into the system.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 498–500)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: This inserts or replaces chunks in the sample in-memory index. A chunk is a piece of searchable text plus metadata.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it in a dictionary keyed by chunk digest, replacing any older chunk with the same digest.

**Call relations**: Core's indexing path calls this when it wants the sample index backend to remember searchable chunks.


##### `SampleIndex.delete`  (lines 502–504)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: This removes all indexed chunks that belong to a given scope. A scope means a particular owner kind and owner id.

**Data flow**: It receives an index scope. It finds stored chunks that match that scope and deletes their dictionary entries.

**Call relations**: Core calls this when an indexed owner should be removed. It uses `_in_scope` to make the matching rule shared and explicit.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 506–507)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: This answers whether the sample index has any chunks for a given scope.

**Data flow**: It receives an index scope. It scans the stored chunks and returns true as soon as one chunk matches, otherwise false.

**Call relations**: Core can call this to decide whether a scope already has indexed content. It depends on `_in_scope` for the match check.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 509–515)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: This deletes outdated chunks in a scope while keeping a named set. It is like cleaning a shelf but leaving the books on a keep-list.

**Data flow**: It receives a scope and a set of chunk digests to keep. It removes stored chunks that are in the scope but not in the keep set.

**Call relations**: Core calls this after re-indexing when it knows which chunks remain valid. `_in_scope` supplies the scope test.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 517–526)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This performs a simple word-based search over stored chunks. It scores chunks by how often query terms appear in the text.

**Data flow**: It receives query text, allowed subjects, owner kind, and a limit. It filters chunks to that owner kind and subjects, counts query-term matches, turns positive scores into hits, sorts best first, and returns up to the limit.

**Call relations**: Core calls this through the index backend protocol for lexical search. It uses `_scoped` to filter candidates and `_hit` to shape search results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 528–536)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: This performs a simple vector search. Vectors are lists of numbers used to compare meaning; here the comparison is a dot product.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and a limit. It filters chunks, scores each by dot product against the query vector, keeps positive scores, sorts best first, and returns hits.

**Call relations**: Core calls this through the vector search path. It uses `_scoped` for filtering, `_dot` for scoring, and `_hit` for the returned hit objects.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 538–543)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: This filters stored chunks to the owner kind and subjects relevant to a search.

**Data flow**: It receives a set of subjects and an owner kind. It scans the in-memory chunk dictionary and returns only chunks whose owner kind and subject match.

**Call relations**: `SampleIndex.lexical` and `SampleIndex.vector` call this before scoring so both searches obey the same visibility boundary.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 552–553)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: This returns a fixed embedding vector for every input text. It proves the embed backend selection path without calling a real embedding service.

**Data flow**: It receives a tuple of texts. It returns a tuple of equal length where each item is the same sample vector.

**Call relations**: Core calls this through the manifest-registered embed backend. The resulting vectors can be fed into the sample index's vector search.


##### `_in_scope`  (lines 556–557)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: This checks whether a chunk belongs to a particular index scope.

**Data flow**: It receives a chunk and scope. It compares the chunk's owner kind and owner id with the scope and returns true only if both match.

**Call relations**: The sample index uses this helper when deleting, pruning, or checking for chunks, so all scope-sensitive operations agree.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 560–563)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: This computes the dot product between two vectors. It is the sample's simple way to score vector similarity.

**Data flow**: It receives two number tuples. If either is empty, it returns 0; otherwise it multiplies matching positions together and sums the products.

**Call relations**: `SampleIndex.vector` calls this to turn a chunk vector and query vector into a score.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 566–575)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: This turns a stored chunk and a score into a search hit. A hit is the result shape the index API returns.

**Data flow**: It receives a chunk and numeric score. It copies the chunk metadata and text into a `Hit` object and attaches the score.

**Call relations**: Both lexical and vector search call this after scoring, so both return the same hit format.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 578–585)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: This is the sample onboarding step. It records that onboarding happened and registers the sample source.

**Data flow**: It receives an extension context. It writes an onboarding marker to the extension store, creates typed source configuration, and asks core to register the source for the shared subject.

**Call relations**: The manifest registers this as an onboarding step. Core calls it during extension onboarding, and it hands source registration to the extension context.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 588–591)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: This pre-tool hook refuses the sample echo tool. It proves a hook can stop a tool before the tool handler runs.

**Data flow**: It receives hook context. It ignores the details and returns a deny outcome with the sample reason.

**Call relations**: The manifest attaches this to pre-use events for the echo tool. When core asks before running echo, this outcome short-circuits dispatch.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 594–602)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: This records successful tool-use events. It proves successful tool calls reach the post-tool hook.

**Data flow**: It receives hook context. If the payload is a successful post-tool-use event, it stores the tool name under the sample post key and returns no blocking outcome.

**Call relations**: Core calls this after successful tool dispatches. It observes the event and writes evidence into the extension store.


##### `_record_post_failure`  (lines 605–611)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: This records failed tool-use events. It proves failed calls go to a different hook than successful calls.

**Data flow**: It receives hook context. If the payload is a post-tool-use-failure event, it stores the failed tool name and returns no blocking outcome.

**Call relations**: Core calls this after a tool dispatch ends in error. It records the failure path separately from `_record_post`.


##### `_record_stop`  (lines 614–620)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: This records the final answer at the end of a turn. It proves stop hooks receive the answer that is about to be committed.

**Data flow**: It receives hook context. If the payload is a stop event, it saves the final answer in the extension store and returns no outcome.

**Call relations**: Core calls this near turn completion. The stored value lets tests confirm the stop event was delivered.


##### `_record_pre_compact`  (lines 623–630)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: This records information just before conversation compaction. Compaction means shortening accumulated context so it fits in the model window.

**Data flow**: It receives hook context. If the payload is a pre-compaction event, it stores the reason and token estimate from before compaction.

**Call relations**: Core calls this before compacting context. It records the inputs to that lifecycle event.


##### `_record_post_compact`  (lines 633–645)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: This records information just after conversation compaction. It captures the produced summary and token counts before and after.

**Data flow**: It receives hook context. If the payload is a post-compaction event, it stores the summary, previous token estimate, and new token estimate.

**Call relations**: Core calls this after compaction. It pairs with `_record_pre_compact` to prove both sides of the compaction event stream work.


##### `_record_page_change`  (lines 648–661)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: This records page-change batches delivered to the extension. It also notes whether a model client was wired into the off-turn context.

**Data flow**: It receives hook context. If the payload is a page-change batch, it stores the changed page ids and a true-or-false flag showing whether `ctx.ext.model` is available.

**Call relations**: Core calls this when page changes are delivered. It proves the data-plane hook and off-turn context setup are both working.


##### `_SampleConnectorOAuth.authorize_url`  (lines 674–675)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This builds the sample connector's OAuth authorization URL. OAuth is the common web flow where a user grants an app access to an account.

**Data flow**: It receives a state value and redirect URI. It returns a fixed sample authorization URL with those values added as query parameters.

**Call relations**: Core asks the connector OAuth object for this URL when starting a connection flow.


##### `_SampleConnectorOAuth.exchange`  (lines 677–680)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This finishes the sample OAuth flow by returning a canned connected account id. It does not expose any secret token.

**Data flow**: It receives the authorization code, redirect URI, workspace id, and state. It ignores the live provider details and returns the sample account id.

**Call relations**: Core calls this after an OAuth callback. The resulting account id is later used by connector execution paths.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 693–694)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: This returns the sample connector broker's available tool catalog. There is one canned broker tool.

**Data flow**: It receives workspace, provider, and query values. It returns a tuple containing one broker tool with a fixed slug and description.

**Call relations**: Core calls this when discovering connector tools. `_SampleBroker.search` also calls it to include tools in broker search results.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 696–703)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: This returns the input schema for the sample broker tool. It refuses unknown tool slugs.

**Data flow**: It receives workspace, provider, and tool slug. If the slug is not the sample slug, it raises an unknown-tool error; otherwise it returns the tool description and JSON-style input schema.

**Call relations**: Core calls this when it needs the detailed shape of a dynamic connector tool before execution.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 705–722)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: This executes the sample broker tool by echoing the call details back. It proves arguments, account id, and idempotency key reach the broker.

**Data flow**: It receives workspace, provider, slug, arguments, account id, and optional idempotency key. It rejects unknown slugs, otherwise returns a dictionary containing all those call details.

**Call relations**: Core calls this through the dynamic connector execution path. The echoed response gives tests a direct view of what core sent.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 724–735)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: This converts echoed output URLs into broker file records. It demonstrates how connector responses can point to produced files.

**Data flow**: It receives the broker response dictionary. It looks inside the echoed arguments for a `file_output_urls` list, turns string URLs into named broker files, and ignores anything malformed.

**Call relations**: Core can call this after broker execution to discover files that should be fetched or bridged into the workspace.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 737–763)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: This stages an upload for a connector tool. It also demonstrates deduplication: the second time the same content key is staged, no upload URL is needed.

**Data flow**: It receives workspace, provider, slug, filename, MIME type, and MD5 hash. It builds a content-addressed key, returns a file URL the first time, remembers that key, and returns a no-upload-needed response on repeats.

**Call relations**: Core calls this before connector execution when a local file must be uploaded for the broker tool. The returned argument is what core passes into execution.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 765–768)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: This returns a sample broker search result. It includes the broker's tool catalog and a fixed suggested plan.

**Data flow**: It receives workspace, provider, and query. It asks `tools` for the available sample tools, then wraps them with a canned plan string.

**Call relations**: Core calls this when searching connector capabilities. It delegates tool listing to `_SampleBroker.tools`.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 770–771)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This returns a bearer credential for a connected account. A bearer credential is a token sent with requests to prove authorization.

**Data flow**: It receives workspace, provider, and account id. It returns a credential whose bearer token is the sample prefix plus the account id.

**Call relations**: Core calls this when it needs a connector account credential to thread into controlled outbound access.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 782–800)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: This is the sample connector's server-side tool. It resolves which connected account the current agent is allowed to use and records that execution request.

**Data flow**: It receives a tool context and connector execution arguments. It checks for an extension context, asks the tool context for the bound connector account, stores the account, tool name, and idempotency key, then returns the account as text.

**Call relations**: The manifest registers this under the connector provider. Core dispatches it as a side-effecting connector tool, and it relies on the context's connector-account lookup before recording the call.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 810–811)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: This small helper turns bytes into a one-piece async stream. It lets file-writing code use a streaming interface even for a tiny sample file.

**Data flow**: It receives bytes. When iterated, it yields those same bytes once and then ends.

**Call relations**: `_surface_ingest` calls this when it wants to write optional inbound text into the workspace file store.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_ingest`  (lines 814–841)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This handles inbound messages for the sample durable surface. A surface is an external channel, like an email or chat integration, that can open conversations and admit turns.

**Data flow**: It receives a surface context and HTTP request. It parses the body, finds or links a member identity, gets or creates a conversation, optionally writes an inbound file, admits a turn with an idempotency key, and returns ids plus whether a run was opened.

**Call relations**: The durable surface route in the manifest points here. It calls surface-context helpers for identity linking, conversation lookup, file writing, and turn admission.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 844–845)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: This pretends to post a reply back to the outside surface. It returns a fixed external reply reference.

**Data flow**: It receives a surface context and writeback description. It does not inspect or change anything and returns the sample post reference string.

**Call relations**: The durable surface registers this as its post callback. Core calls it when there is an outgoing writeback to deliver.


##### `_surface_attach`  (lines 848–853)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: This delivers shared artifacts by copying their bytes to sample delivered blob keys. It proves streamed blob reads and writes work.

**Data flow**: It receives a surface context, writeback, and reply reference. For each artifact, it opens a read stream from the source blob key and writes that stream to a delivered key based on the turn id and filename.

**Call relations**: Core calls this after posting when writeback artifacts need attachment. It uses the blob store on the surface context.


##### `_surface_live_admit`  (lines 856–878)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This handles inbound messages for the sample live surface. Live mode admits a turn without durable writeback polling and lets clients tail live frames.

**Data flow**: It receives a request, parses it, finds or adopts a member identity, gets a conversation, admits a turn, reads the turn owner and spend rollup, and returns those values as JSON.

**Call relations**: The live surface route in the manifest points here. It uses surface-context calls for identity adoption, admission, ownership lookup, and spend reporting.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 881–885)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This opens a live event stream for a turn. It returns newline-delimited JSON frames from the hub.

**Data flow**: It receives a request with a turn id path parameter. It parses that id, builds a streaming response from `_surface_frames`, and sets the media type for newline-delimited JSON.

**Call relations**: The live surface's stream route calls this. It hands frame production to `_surface_frames`.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 888–890)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: This yields the live frames for a turn as bytes. Each frame becomes one JSON line.

**Data flow**: It receives a surface context and turn id. It tails the turn through the context, converts each frame to JSON bytes, appends a newline, and yields it.

**Call relations**: `_surface_live_stream` uses this as the body of its streaming response. It delegates the actual live-feed reading to the surface context.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 901–903)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: This is the sample model backend's completion stream. It emits one fixed text reply and one fixed usage report.

**Data flow**: It receives a model request. It yields a text-delta event with the sample reply, then yields token usage showing one input token and one output token.

**Call relations**: Core calls this when the manifest-contributed sample model is selected. The stream shape proves custom model clients fit the model protocol.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 913–914)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: This returns the sample browser debugging endpoint. CDP means Chrome DevTools Protocol, the control channel used to drive a browser.

**Data flow**: It receives no extra input. It returns a fixed endpoint object containing the sample WebSocket URL.

**Call relations**: Browser-driving code calls this on a lease when it needs the address to connect to.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 916–917)

```
async def token(self) -> str
```

**Purpose**: This returns the lease token used for reattachment. In the sample, the token is just the fixed endpoint URL.

**Data flow**: It receives no extra input and returns the sample CDP URL string.

**Call relations**: Core can save this token and later pass it to the provider's reattach method.


##### `SampleCdpLease.place_file`  (lines 919–920)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: This pretends to place a file where the browser can access it. The sample returns the path unchanged.

**Data flow**: It receives a path and a file-reading callback. It does not read or copy the bytes and simply returns the same path.

**Call relations**: Browser tooling can call this when it needs a local file path inside the browser environment.


##### `SampleCdpLease.download_dir`  (lines 922–923)

```
async def download_dir(self) -> str
```

**Purpose**: This reports the sample browser download directory.

**Data flow**: It receives no extra input and returns the fixed sample download directory path.

**Call relations**: Browser tooling calls this when it needs to know where downloads appear for this lease.


##### `SampleCdpLease.fetch_download`  (lines 925–926)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: This reads a downloaded file from the sample download directory.

**Data flow**: It receives a download guid, treats it as a filename under the sample download directory, reads the bytes in a worker thread, and returns them.

**Call relations**: Browser tooling calls this when it wants to retrieve a downloaded file by id.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 928–929)

```
async def aclose(self) -> None
```

**Purpose**: This closes the sample CDP lease. Because the sample owns no real browser resource, it does nothing.

**Data flow**: It receives no extra input, changes nothing, and returns nothing.

**Call relations**: Core calls this during cleanup through the lease protocol. It is a no-op stand-in for real browser teardown.


##### `SampleCdpProvider.lease`  (lines 939–940)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: This creates a new sample browser lease. It always returns the same canned lease object.

**Data flow**: It receives an optional sandbox session. It ignores it and returns a new `SampleCdpLease`.

**Call relations**: Core calls this when selecting the manifest-contributed CDP provider for a new browser session.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 942–943)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: This reconnects to an existing sample browser lease. The sample returns a fresh lease object for the same fixed endpoint.

**Data flow**: It receives a saved token. It ignores the token contents and returns a new `SampleCdpLease`.

**Call relations**: Core calls this when resuming a browser session from a saved lease token.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 954–955)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: This returns a fixed credential from the sample auth proxy. It proves auth-proxy backends can be selected and called.

**Data flow**: It receives workspace id, provider, and account. It returns a credential containing the fixed sample bearer value.

**Call relations**: Core calls this through the manifest-registered auth proxy when it needs credentials for a provider/account pair.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 967–975)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: This performs a canned web search. It returns one fixed hit and a fixed direct answer.

**Data flow**: It receives a search query. It ignores the query details and returns search results containing the sample URL, title, text, and answer.

**Call relations**: Research or search tools call this through the registered sample search provider.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 977–978)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: This fetches a canned page for a search result URL. It proves the optional fetch path works.

**Data flow**: It receives a fetch request. It returns a fetched page with the requested URL and fixed sample text.

**Call relations**: Core or research tools call this after search when they want page contents for a URL.

*Call graph*: 1 external calls (__init__).


##### `SampleMemorySearch.search`  (lines 987–1005)

```
async def search(self, queries: tuple[str, ...], subjects: frozenset[str], start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: This records a scoped memory search and returns one sample memory match. Memory search means looking up remembered facts or prior content relevant to a query.

**Data flow**: It receives queries, subjects, and optional start and end times. It stores those search parameters in the extension store, converting times to strings, then returns one fixed memory match.

**Call relations**: Core calls this through the registered memory-search provider. The stored record lets tests verify the query scope and time range were passed correctly.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleCarrier.__init__`  (lines 1017–1018)

```
def __init__(self) -> None
```

**Purpose**: This initializes the sample sandbox carrier. A carrier is the backend that creates and talks to sandbox environments.

**Data flow**: It receives no arguments beyond the new object. It creates an empty dictionary for files written into the pretend sandbox.

**Call relations**: Core constructs this through the carrier factory in the manifest when selecting the sample carrier.


##### `SampleCarrier.create`  (lines 1020–1025)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This creates a pretend sandbox handle. It does not start a real container.

**Data flow**: It receives a sandbox spec. It returns a handle using the spec's conversation id and run token plus the fixed sample container id.

**Call relations**: Core calls this when it wants a new sandbox from the manifest-contributed carrier.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1027–1034)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reattaches to a pretend sandbox if a resume id is available.

**Data flow**: It receives a sandbox spec. If there is no resume id, it returns nothing; otherwise it returns a handle whose container id is that resume id.

**Call relations**: Core calls this before creating a new sandbox when it may be able to resume an existing one.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1036–1039)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This pretends to run a command in the sandbox. It echoes the command arguments as standard output.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout. It joins the arguments with spaces and returns a successful execution result with no error output.

**Call relations**: Core calls this through the carrier protocol when executing commands. The echoed output proves the selected carrier handled the call.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1041–1042)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This writes bytes into the pretend sandbox's in-memory file map.

**Data flow**: It receives a sandbox handle, path, and bytes. It stores the bytes under that path and returns nothing.

**Call relations**: Core calls this when copying files into the sandbox. Later `SampleCarrier.read` can stream the same bytes back.


##### `SampleCarrier.read`  (lines 1044–1047)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads bytes back from the pretend sandbox. It raises a file-not-found error if the path was never written.

**Data flow**: It receives a sandbox handle and path. It looks up the path in the in-memory file map, raises if missing, or yields the stored bytes.

**Call relations**: Core calls this when copying files out of the sandbox. It pairs with `SampleCarrier.write`.


##### `SampleCarrier.host`  (lines 1049–1050)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: This returns a pretend host address for a sandbox port.

**Data flow**: It receives a sandbox handle and port number. It returns a string made from the fixed container name and the port.

**Call relations**: Core calls this when it needs a reachable host:port address for something running in the sandbox.


##### `resolve_workspace`  (lines 1053–1061)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: This identifies which workspace an extension route request belongs to by reading its bearer token. If the token is missing or malformed, it rejects the request by returning no workspace.

**Data flow**: It receives an HTTP request. It reads the `authorization` header, checks for the `Bearer` scheme, strips the token, and asks `workspace_claim` to decode the workspace id.

**Call relations**: The manifest uses this as the route identifier. `resolve_surface_workspace` also calls it so normal routes and surface routes share the same workspace resolution rule.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1064–1066)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This identifies the workspace for sample surface requests. It uses the same bearer-token logic as normal extension routes.

**Data flow**: It receives a request and surface auth object. It ignores the auth object and returns whatever `resolve_workspace` extracts from the request.

**Call relations**: Surface specs in the manifest use this as their identify callback. It delegates to `resolve_workspace`.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `manifest`  (lines 1069–1229)

```
def manifest() -> Manifest
```

**Purpose**: This is the extension entry point. It returns the complete manifest that tells core everything the sample extension contributes.

**Data flow**: It creates the sample broker and then builds a manifest containing tools, objects, jobs, routes, onboarding, hooks, surfaces, source/index/embed/model backends, hub, skill, browser provider, sandbox carrier, auth proxy, search provider, and memory search provider.

**Call relations**: Core calls this when loading the extension. The returned manifest wires nearly every function and class in this file into the public extension system.

*Call graph*: 32 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).
