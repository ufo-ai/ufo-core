# Cross-cutting public SDK, extension interfaces, manifests, types, and conformance samples  `stage-18` (cross-cutting infrastructure)

This stage is shared behind-the-scenes support for people who build extensions. It is not the main engine loop. It provides the public “front door” that extensions should use, so their code does not depend on private internal files that may move or change.

The SDK facades expose safe import paths for security, credentials, accounting, operator tools, logging, browser links, connectors, model access, search, memory, jobs, schedules, objects, skills, surfaces, and tools. These pieces act like labeled counters in a service center: each one points extension code to an approved part of the system.

The package marker files for ufo.ext and ufo.sdk simply make those folders importable. The manifest files define and expose the menu of things an extension or pack can declare, such as tools, routes, jobs, hooks, credentials, skills, and backends. The context file exposes the request-time information extension handlers receive. The HTTP file exposes request and response types plus a safe cookie helper. The sample extension then uses these public paths end to end, proving that real extensions can register features and be tested through the same supported interface.

## Sub-stages

- [Public SDK auth, credentials, accounting, and operator utilities](stage-18.1.md) `stage-18.1` — 7 files
- [Public SDK backend, connector, model, and search integration facades](stage-18.2.md) `stage-18.2` — 10 files
- [Public SDK extension capability facades](stage-18.3.md) `stage-18.3` — 6 files

## Files in this stage

### Extension Manifest Contract
Defines the public extension package boundary and the manifest schema used by extensions and packs to declare capabilities.

### `core/src/ufo/ext/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that start with `ufo.ext`, and Python will know that this directory is part of the project’s module tree.

There is no code here, so it does not run setup steps, expose helper functions, or store configuration. Its value is structural: it gives the extension area of the codebase a clear place in the package layout. You can think of it like a labeled drawer in a filing cabinet. The drawer may not contain instructions itself, but the label lets everyone find and refer to the files inside it consistently.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.ext` could fail or behave less predictably. Keeping it present makes the package layout explicit.


### `core/src/ufo/ext/manifest.py`

`data_model` · `startup and extension loading, with declarations consulted during runtime`

This file is the shared vocabulary between the core application and its extensions. An extension does not directly wire itself into the running system. Instead, it returns a frozen Manifest object that lists what it offers and what it needs. The core loader reads these declarations at startup and then mounts routes, registers tools, schedules jobs, prepares credential injection, adds prompt sections, enables hooks, and selects optional backends.

Most of the file is made of small frozen data classes. “Frozen” means they are value objects: once created, they are not meant to be changed. That matters because these declarations are treated as facts about an installed extension, not as live registration code with side effects.

The same pattern is used for packs. A Pack declares a coherent bundle of extensions plus pack-level skills and onboarding steps. Activating a pack is therefore like choosing a product configuration: the system loads exactly the extension set and extra content the pack names.

A few declarations describe runtime behavior, such as hook payloads and hook outcomes. Hooks let extensions observe or narrow behavior during a turn, for example denying a tool call or adding context, but they cannot secretly grant powers the user or system did not already allow. The file’s only function checks one important global rule: there can be at most one catch-all connector namespace, because two catch-alls would make ownership of unknown connector names ambiguous.

#### Function details

##### `open_connector_namespace`  (lines 577–589)

```
def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None
```

**Purpose**: This function finds the single open connector namespace declared by the installed manifests, if one exists. It prevents two extensions from both claiming to be the fallback for unregistered connector names, because the system would not know which one should answer.

**Data flow**: It receives a tuple of Manifest objects. It walks through them, looking at each manifest's connector_resolver field. If none are present, it returns None. If exactly one is present, it returns that resolver. If it finds a second one, it raises a RuntimeError so the process fails early instead of making unclear routing decisions later.

**Call relations**: During extension setup, code that has collected the installed manifests can call this helper before building connector-related behavior. The returned namespace is then the single place other connector flows may use for provider names that were not explicitly registered. If the helper raises, startup stops with a clear error instead of letting connect flow, connector routing, or egress rules disagree later.


### SDK Import Facades
Provides stable SDK import paths for context values, HTTP helpers, and manifest-related types used by extension authors.

### `core/src/ufo/sdk/__init__.py`

`other` · `import/package discovery`

In Python, an `__init__.py` file is the doorway to a package: a folder of related Python code that can be imported together. This particular doorway is empty, which means it does not set up shared objects, re-export helper functions, or run startup code. Its main value is structural. It tells readers and tools that `core/src/ufo/sdk` is meant to be a named part of the project, likely holding code for an SDK, or software development kit, which is a set of tools other code can use to interact with this system. Without this file, some Python environments or packaging tools might not treat the folder as an importable package in the same way. Think of it like a labeled section divider in a binder: it may not contain instructions itself, but it makes the surrounding material easier to find and use.


### `core/src/ufo/sdk/context.py`

`data_model` · `cross-cutting`

An extension handler needs a safe, limited view of the system: things like credentials it is allowed to use, model access, stored records, page/source information, and trajectory data. This file gathers those public names into one place under `ufo.sdk.context`.

It does not create new behavior. Instead, it re-exports types and helpers that are defined elsewhere, mostly in `ufo.ext.context`, plus a few related records and credential errors. Think of it like a front desk: visitors do not need to know which office a form came from; they just pick it up at the official counter.

This matters because extensions should write their handler signatures against the SDK surface, not the private internal modules. If the internal files move or are reorganized later, this re-export layer can keep the public import path stable. Without this file, extension code would be more tightly coupled to internal implementation details, making upgrades more fragile.

The names exposed here include the main `ExtensionContext`, access helpers such as `CredentialAccess` and `ModelAccess`, storage and record types such as `ScopedStore`, `PageRecord`, and `SourceRecord`, and error/value types used when declared credentials or trajectory workspaces are involved.


### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file is the HTTP part of the project's SDK, meaning it is the approved set of web request and response building blocks exposed to extension authors. A route handler receives a Request and returns some kind of Response, such as an HTML page, JSON data, plain text, a redirect, or a streaming response. Rather than asking every extension to import those pieces directly from Starlette, this file re-exports them from one stable project-owned place.

The most important behavior here is cookie safety. Session cookies are sensitive because they often prove who a user is. If they are set carelessly, they can leak across subdomains or be read by browser scripts. The helper set_session_cookie is the single approved way to attach a session cookie to a response. It deliberately does not accept a domain value, which means the browser treats the cookie as host-only: it belongs only to the exact host that set it. It also always turns on HttpOnly, so JavaScript cannot read it, and Secure, so browsers only send it over HTTPS. The only choice callers get is SameSite, a browser rule that controls when cookies are sent during cross-site navigation. In short, this file is a guardrail: it makes the common HTTP tasks easy while preventing one especially risky cookie mistake.

#### Function details

##### `set_session_cookie`  (lines 17–25)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none']) -> None
```

**Purpose**: This function safely attaches a session cookie to an HTTP response. It exists so the rest of the project has one approved cookie-setting path with fixed security rules, instead of many callers each remembering the right options.

**Data flow**: It receives a response object, a cookie name, a session token value, and a SameSite setting. It passes those values to the underlying response's cookie-setting method, while always adding HttpOnly and Secure and never adding a domain. Nothing is returned; the response is changed so that, when sent to the browser, it tells the browser to store the session cookie.

**Call relations**: When route code needs to bind a browser session to a response, it should call this helper instead of calling the lower-level cookie API directly. This helper then hands the actual cookie-writing work to Starlette's Response.set_cookie, but only after locking in the project's safety rules.

*Call graph*: 1 external calls (set_cookie).


### `core/src/ufo/sdk/manifest.py`

`other` · `cross-cutting; used when extensions are imported or written against the SDK`

Extensions need to describe what they provide: connectors, hooks, skills, search providers, credential needs, onboarding steps, and similar pieces. Those descriptions are grouped into a “manifest,” which is like a plugin’s application form: it tells the main system what the extension can do and what it needs.

This file does not create new behavior. Instead, it re-exports selected classes and types from deeper modules such as `ufo.ext.manifest` and `ufo.credentials`. Re-exporting means it imports a name and then makes that same name available from this public module. For example, extension code can import `Manifest` from `ufo.sdk.manifest` rather than reaching into `ufo.ext.manifest` directly.

That matters because internal module paths can change as the project grows. If extensions depended on those internal paths, small reorganizations could break them. This file is the stable doorway the project promises to outsiders. It also fits the project rule that package `__init__.py` files stay empty, so public SDK names live in explicit modules like this one instead.


### Sample Extension Conformance
Exercises the public extension interfaces end to end by registering many extension features and recording behavior for tests.

### `extensions/sample/ufo_ext_sample.py`

`test` · `cross-cutting conformance runs`

Think of this file as a working showroom for the extension system. It does not connect to real outside services. Instead, it provides small, predictable versions of many things an extension can contribute: tools, jobs, web routes, onboarding, hooks, object stores, connectors, surfaces, search, memory, models, browser sessions, sandboxes, and more. The point is not to be useful to an end user. The point is to exercise the public SDK boundary end to end.

Most handlers write a small record into the extension store, which is durable workspace-scoped storage. That lets conformance tests ask, “Did the core system really call this extension point?” without relying on fake logs or private internals. Other pieces return canned data, such as a fixed search result, a fixed model reply, or a fixed browser endpoint.

The `manifest()` function is the center of the file. It declares everything this extension offers. Core reads that manifest, then later calls the registered handlers when a tool runs, a hook fires, a source syncs, a surface receives a request, or a backend is selected. Without this file, the project would lose a broad, installed example that checks whether the extension API remains compatible and complete.

#### Function details

##### `_echo`  (lines 241–245)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It records the incoming message in the extension's durable store, then returns the same message as tool output.

**Data flow**: It receives a tool context and an `EchoInput` containing a message. It checks that the tool was given an extension context, saves the message under the sample tool key, and returns a text result containing that message.

**Call relations**: Core calls this when the `sample_echo` tool is dispatched from the manifest. It builds the outgoing `TextContent` and `ToolResult`, while the pre-tool hook in this same file can deny this tool before it ever reaches this handler.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 248–270)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Runs the sample note tool, proving that an extension can use its own database table. It writes a note for the current workspace and reads it back.

**Data flow**: It receives a tool context and note text. It opens the extension's workspace-scoped transaction, updates or inserts one row in `sample_ext_note`, selects the stored note, and returns that stored text as the tool result.

**Call relations**: Core calls this through the `sample_note` tool declared in `manifest()`. It uses SQLAlchemy insert, update, and select calls to exercise the migration-created table path, and its result is wrapped as tool text.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 273–289)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample scheduled job. It marks that the job ran and, when conversation history is available, proposes a small prompt change.

**Data flow**: It receives an extension context. It writes a job-ran marker, reads available trajectories, stores their count, and if one exists creates an `AgentChange` with a prompt suffix and stores the resulting proposal id.

**Call relations**: Core calls this through the `sample_tick` job declared in the manifest. It calls the extension context's trajectory and proposal APIs to prove jobs can inspect past agent runs and submit changes.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 292–295)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Implements the sample HTTP route. It echoes the request body and records that the route was reached.

**Data flow**: It receives an extension context and an HTTP request. It reads the raw request body, decodes it as text, stores that text, and returns the same text in a plain response.

**Call relations**: Core calls this for the manifest's `POST /hook` route after `resolve_workspace` identifies the workspace. It uses the SDK request and response types to prove extension routes work.

*Call graph*: 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 330–341)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists saved sample widgets through the object API. It turns internal store rows into user-facing object rows.

**Data flow**: It receives a tool context and list query. It reads all extension-store keys with the widget prefix, validates each stored value, builds rows with names, summaries, and fields, and returns a paged object result.

**Call relations**: Core calls this when someone lists the `sample_widget` object kind declared in `manifest()`. It uses `WidgetStore._ext` to reach extension storage and hands the rows to `object_page` for paging.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 343–350)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one saved sample widget by name. It returns the widget spec and timestamps if the widget exists.

**Data flow**: It receives a tool context and widget name. It reads the matching extension-store key, returns nothing if missing, or validates the stored row and returns an object detail.

**Call relations**: Core calls this for object read requests on `sample_widget`. It shares the same extension-store prefix as `list`, `apply`, and `delete`.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 352–353)

```
async def status(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Reports extra runtime status for a widget. In this sample, widgets have no extra status, so it always returns nothing.

**Data flow**: It receives the context and object name, ignores them, and returns `None`. No storage is read or changed.

**Call relations**: Core may call this as part of object status display for the registered widget kind. It intentionally stays empty to show that status is optional.


##### `WidgetStore.apply`  (lines 355–365)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None) -> None
```

**Purpose**: Creates or updates a sample widget. It preserves the original creation time and refreshes the update time.

**Data flow**: It receives a context, object name, new widget spec, and optional old spec. It reads any existing stored widget, chooses a creation timestamp, writes the new stored widget row, and changes extension storage.

**Call relations**: Core calls this for create or update object verbs on `sample_widget`. It uses `WidgetStore._ext` to reach storage and `datetime.now` to stamp the row.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, now).


##### `WidgetStore.delete`  (lines 367–370)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Deletes a sample widget, but only if the speaker is the workspace owner. This checks that object operations can enforce permissions.

**Data flow**: It receives a context and widget name. It asks whether the speaker is the owner; if not, it raises an owner-required error. If allowed, it deletes the widget key from extension storage.

**Call relations**: Core calls this for delete requests on `sample_widget`. It depends on `ToolContext.speaker_is_owner` for the permission check and uses `WidgetStore._ext` for the actual storage delete.

*Call graph*: calls 2 internal fn (speaker_is_owner, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 372–375)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Extracts the extension context from a tool context. It fails loudly if the object store was called without the needed extension context.

**Data flow**: It receives a tool context. If `ctx.ext` exists, it returns it; otherwise it raises a runtime error and changes nothing.

**Call relations**: The widget store's list, get, apply, and delete methods call this before touching extension storage. It is the small safety gate that keeps those methods from silently running without workspace-scoped storage.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 383–387)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample read-only relic object. It always returns one canned relic.

**Data flow**: It receives a context and list query. It creates one object row for the fixed relic name and returns it as a paged result.

**Call relations**: Core calls this when listing the `sample_relic` object kind. It uses the same object paging helper as the writable widget store, but with fixed data.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 389–394)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Fetches the sample relic by name. It returns details only for the one known relic.

**Data flow**: It receives a context and object name. If the name is not the fixed relic name, it returns nothing; otherwise it returns a detail with the relic inscription and no timestamps.

**Call relations**: Core calls this for read requests on `sample_relic`. It creates a `RelicSpec` and `ObjectDetail` to show how system-produced read-only objects can be exposed.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 396–397)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status marker for a relic. The marker says the relic was “excavated,” matching the sample's read-only story.

**Data flow**: It receives a context and object name, does not read storage, and returns a dictionary with the origin field.

**Call relations**: Core may call this when showing object status for `sample_relic`. Unlike widgets, relics provide a small live status value.


##### `RelicStore.apply`  (lines 399–402)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None) -> None
```

**Purpose**: Refuses attempts to create or update a relic. This proves the object API can expose read-only kinds.

**Data flow**: It receives the attempted name, spec, and old spec, ignores their contents, and raises a not-supported error. Nothing is stored.

**Call relations**: Core calls this if someone tries to apply changes to `sample_relic`. The raised `VerbNotSupported` error is the intended handoff back to core.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 404–405)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Refuses attempts to delete a relic. The sample relic can be read, but never changed or removed.

**Data flow**: It receives the context and name, ignores them, and raises a not-supported error. No storage is changed.

**Call relations**: Core calls this for delete attempts on `sample_relic`. It mirrors `RelicStore.apply` to prove both mutation paths are blocked.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 424–435)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one deterministic page from the sample source configuration. It proves that a contributed source backend can return content for indexing and memory.

**Data flow**: It receives typed source config, an optional cursor, and auth information. It hashes the configured topic into a digest, builds one page from the topic text, and returns a sync result with no next cursor.

**Call relations**: Core calls this after `_setup` registers the source. It creates SDK `Page` and `SyncResult` objects, while ignoring auth because this sample source needs no real token.

*Call graph*: 3 external calls (__init__, __init__, sha256).


##### `SampleIndex.upsert`  (lines 448–450)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the in-memory sample index. A chunk is a searchable piece of text with metadata.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it in the `chunks` dictionary under its digest, replacing any older chunk with the same digest.

**Call relations**: Core calls this through the index backend registered in `manifest()`. Later lexical and vector searches read the chunks stored here.


##### `SampleIndex.delete`  (lines 452–454)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all indexed chunks that belong to a given owner scope. A scope is the owner kind and owner id that identify a group of indexed content.

**Data flow**: It receives an index scope. It scans stored chunks, uses `_in_scope` to find matching ones, and removes those digests from the dictionary.

**Call relations**: Core calls this when it wants the sample index to remove a whole scope. The helper `_in_scope` holds the shared matching rule also used by pruning.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 456–462)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes old chunks inside a scope while keeping a named set. This mimics cleanup after a source resync.

**Data flow**: It receives a scope and a set of chunk digests to keep. It deletes stored chunks that are in the scope but not in the keep set.

**Call relations**: Core calls this through the sample index backend when it needs to discard stale indexed data. It uses `_in_scope` to avoid deleting chunks outside the requested owner.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 464–473)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches stored chunks by plain word matching. It scores chunks by how often query terms appear in their text.

**Data flow**: It receives query text, allowed subjects, an owner kind, and a limit. It filters chunks with `_scoped`, counts matching terms, converts positive matches to hits, sorts by score, and returns the top hits.

**Call relations**: Core calls this when using the sample index for text search. It relies on `_scoped` for visibility filtering and `_hit` to turn stored chunks into search results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 475–483)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches stored chunks by vector similarity. A vector is a list of numbers representing text, and this sample compares vectors with a dot product.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and limit. It filters chunks, computes `_dot` between each chunk embedding and the query embedding, converts positive scores to hits, sorts them, and returns the top hits.

**Call relations**: Core calls this when using the sample index for embedding search. It uses `_scoped` for filtering, `_dot` for scoring, and `_hit` for the returned hit objects.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 485–490)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters indexed chunks to the owner kind and subjects allowed by a search. This keeps searches from seeing unrelated stored chunks.

**Data flow**: It receives allowed subjects and an owner kind. It scans the in-memory chunk dictionary and returns only chunks whose owner kind and subject match.

**Call relations**: Both `SampleIndex.lexical` and `SampleIndex.vector` call this before scoring. It is the common visibility filter for the sample index.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 499–500)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for each input text. It proves the embed-backend selection path without calling a real embedding service.

**Data flow**: It receives a tuple of texts. For every text, it outputs the same sample vector and changes no state.

**Call relations**: Core calls this through the embed backend registered in `manifest()`. The sample index can then use those vectors for vector search.


##### `_in_scope`  (lines 503–504)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether one indexed chunk belongs to a requested index scope. It compares owner kind and owner id.

**Data flow**: It receives a chunk and a scope. It returns `true` only when both owner fields match, and otherwise returns `false`.

**Call relations**: `SampleIndex.delete` and `SampleIndex.prune` call this before removing chunks. It keeps cleanup operations from touching the wrong owner's data.

*Call graph*: called by 2 (delete, prune).


##### `_dot`  (lines 507–510)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes a dot product, a simple similarity score between two number lists. Empty vectors score zero.

**Data flow**: It receives two tuples of floats. If either is empty it returns `0.0`; otherwise it multiplies matching positions and sums the products.

**Call relations**: `SampleIndex.vector` calls this to score each candidate chunk against the query embedding.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 513–522)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Turns an indexed chunk and a score into a search hit. It copies the chunk's identifying information into the result object.

**Data flow**: It receives a chunk and numeric score. It builds and returns a `Hit` with the chunk digest, owner, subject, ordinal, text, and score.

**Call relations**: `SampleIndex.lexical` and `SampleIndex.vector` call this after scoring matching chunks. It standardizes the result shape returned to core.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 525–532)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It records that onboarding happened and registers the sample source for syncing.

**Data flow**: It receives an extension context. It writes an onboarding marker, creates typed source config with the sample topic, and asks core to register the source under the shared subject.

**Call relations**: Core calls this through the onboarding step declared in `manifest()`. It hands off to `ExtensionContext.register_source`, which later causes `SampleSource.fetch` to be used.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 535–538)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Blocks the sample echo tool during the pre-tool hook. It proves that a hook can stop a tool before the handler runs.

**Data flow**: It receives hook context and returns a `Deny` outcome with a fixed reason. It does not read or write storage.

**Call relations**: Core calls this for the `pre_tool_use` hook attached to `sample_echo`. Because it returns `Deny`, `_echo` should not run for that denied call.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 541–549)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool-use events. It observes calls that completed successfully and stores the tool name.

**Data flow**: It receives hook context. If the payload is a successful post-tool event, it writes the tool name under the post-hook key and returns no special outcome.

**Call relations**: Core calls this for the global `post_tool_use` hook from the manifest. Failed tool calls go to `_record_post_failure` instead, so tests can see the split.


##### `_record_post_failure`  (lines 552–558)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed tool-use events. It proves that tool errors are reported through a separate hook path.

**Data flow**: It receives hook context. If the payload is a failure event, it writes the failed tool name under the post-failure key and returns no special outcome.

**Call relations**: Core calls this for the `post_tool_use_failure` hook. It complements `_record_post`, which only records successful calls.


##### `_record_stop`  (lines 561–567)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer at the end of a turn. This proves the stop hook fires before the answer is committed.

**Data flow**: It receives hook context. If the payload is a stop event, it stores the answer text and returns no special outcome.

**Call relations**: Core calls this through the manifest's `stop` hook. Tests can read the extension store afterward to confirm the turn-end event arrived.


##### `_record_pre_compact`  (lines 570–577)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information before conversation compaction. Compaction means shortening old context so a model can keep working within its token limit.

**Data flow**: It receives hook context. If the payload is a pre-compaction event, it stores the reason and estimated token count before compaction.

**Call relations**: Core calls this through the `pre_compact` hook. `_record_post_compact` records the matching after-compaction information.


##### `_record_post_compact`  (lines 580–592)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction. It stores the summary and token counts from before and after.

**Data flow**: It receives hook context. If the payload is a post-compaction event, it writes the summary, before-token count, and after-token count into extension storage.

**Call relations**: Core calls this through the `post_compact` hook. Together with `_record_pre_compact`, it proves both sides of compaction notification work.


##### `_record_page_change`  (lines 595–608)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records delivered page-change notifications. It also notes whether a model was available in the off-turn extension context.

**Data flow**: It receives hook context. If the payload contains page changes, it stores the changed page ids and a boolean showing whether `ctx.ext.model` was wired.

**Call relations**: Core calls this through the `page_change` hook. It gives tests evidence that data-plane page changes reached the extension.


##### `_SampleConnectorOAuth.authorize_url`  (lines 621–622)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the sample connector's OAuth authorization URL. OAuth is the common “send the user to a provider to approve access” flow.

**Data flow**: It receives a state value and redirect URI. It returns a fixed sample authorization URL with those values placed in query parameters.

**Call relations**: Core calls this during connector setup for the provider declared in `manifest()`. The returned URL stands in for a real provider's consent page.


##### `_SampleConnectorOAuth.exchange`  (lines 624–627)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the sample OAuth exchange. Instead of contacting a real provider, it returns one fixed connected-account id.

**Data flow**: It receives the authorization code, redirect URI, workspace id, and state. It ignores the code contents and returns an `OAuthAccount` with the canned account id.

**Call relations**: Core calls this after the OAuth redirect returns. The account id it returns can later be resolved by connector tools such as `_connector_execute`.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 640–641)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample broker's available tool catalog. A broker is the middle service that knows provider-side tools.

**Data flow**: It receives workspace id, provider name, and search query. It returns one `BrokerTool` describing the sample list-widgets tool.

**Call relations**: Core and `_SampleBroker.search` call this when discovering dynamic connector tools. The result feeds connector search and schema flows.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 643–650)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool. It refuses unknown tool slugs.

**Data flow**: It receives workspace id, provider, and tool slug. If the slug is not the sample slug, it raises `UnknownBrokerTool`; otherwise it returns a tool description with a small JSON-style input schema.

**Call relations**: Core calls this when it needs detailed argument information for a dynamic connector tool. It protects the sample broker from pretending unknown tools exist.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 652–669)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Executes the sample broker tool by echoing the call details. This proves that connector execution passes provider, arguments, account, and idempotency information correctly.

**Data flow**: It receives workspace id, provider, slug, arguments, account id, and optional idempotency key. It rejects unknown slugs, otherwise returns a dictionary echoing the received values.

**Call relations**: Core calls this when running the dynamic broker tool. The returned dictionary may later be inspected by `file_outputs` to discover produced files.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 671–682)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts produced files from a broker response. It looks for URLs that were echoed inside the tool arguments.

**Data flow**: It receives a response dictionary. If `arguments.file_output_urls` is a list of strings, it turns each URL into a `BrokerFile` named from the URL path; otherwise it returns no files.

**Call relations**: Core calls this after broker execution when it needs to bridge provider-produced files into the workspace. It uses `PurePosixPath` to derive simple file names.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 684–710)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Stages a file upload for a connector tool. It returns where the sandbox should put bytes and what argument should be passed to the provider.

**Data flow**: It receives workspace id, provider, slug, filename, mimetype, and md5 hash. It builds a content-addressed key; if already staged, it returns a dedup result with no upload URL, otherwise it remembers the key and returns a file URL plus argument metadata.

**Call relations**: Core calls this before executing connector tools that need uploaded files. The later execute call can receive the returned argument object.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 712–715)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Searches the sample broker catalog. It returns the sample tool plus a canned plan saying what to call first.

**Data flow**: It receives workspace id, provider, and query. It calls `_SampleBroker.tools` to get tools, combines them with the fixed plan text, and returns a `BrokerSearch`.

**Call relations**: Core calls this when an agent or UI searches connector capabilities. It reuses `tools` so discovery and search stay consistent.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 717–718)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a bearer credential for a connected connector account. A bearer credential is a token sent with requests to prove access.

**Data flow**: It receives workspace id, provider, and account id. It returns a `Credential` whose bearer token is the sample prefix plus the account id.

**Call relations**: Core calls this when connector egress needs a token for the sample provider. It is separate from OAuth exchange, which only returned the account id.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 725–743)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector's server-side execute tool. It resolves the connected account bound to the current agent and records the call.

**Data flow**: It receives a tool context and connector input. It checks for an extension context, asks the tool context for the account id for the sample connector, stores the account, requested tool name, and idempotency key, then returns the account as text.

**Call relations**: Core calls this through the connector tool declared in `manifest()`. It depends on `ToolContext.connector_account`, so a missing connector grant fails before a fake execution can be recorded.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 753–754)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Turns one bytes value into a tiny async byte stream. It is used when writing an inbound surface file.

**Data flow**: It receives bytes and yields those same bytes once. It does not store anything or transform the content.

**Call relations**: `_surface_ingest` calls this when inbound text should be saved as a workspace file. It gives `write_workspace_file` the streaming shape it expects.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_ingest`  (lines 757–776)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives a request from the sample durable surface. A surface is an external channel, such as chat or email, that can admit messages into UFO conversations.

**Data flow**: It reads JSON from the request, resolves or links a member identity, gets or creates a conversation, optionally writes inbound text as a workspace file, admits a turn with an idempotency key, and returns the turn and conversation ids as JSON.

**Call relations**: Core calls this for the main sample surface route declared in `manifest()`. It calls several `SurfaceContext` methods to prove identity linking, conversation creation, file writing, and turn admission work together.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 2 external calls (JSONResponse, body).


##### `_surface_post`  (lines 779–780)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed reference for a posted surface reply. It represents the external system's id for the reply.

**Data flow**: It receives surface context and writeback details, ignores their contents, and returns the canned post reference string.

**Call relations**: Core calls this as the `post` callback for the durable sample surface. `_surface_attach` can then attach artifacts to that reply reference.


##### `_surface_attach`  (lines 783–788)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies shared artifacts from the blob store to delivered blob keys. This proves file attachments can be streamed out and back in.

**Data flow**: It receives surface context, writeback details, and a reply reference. For each artifact, it reads the artifact blob as a stream and writes it to a delivered key based on turn id and filename.

**Call relations**: Core calls this as the durable surface's `attach` callback after posting a reply. It uses the blob store's streaming read and write paths.


##### `_surface_live_admit`  (lines 791–813)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives a request from the sample live surface. Live mode admits a turn for hub streaming rather than durable writeback posting.

**Data flow**: It reads JSON, resolves an existing member or adopts identity from a peer surface, gets or creates a conversation, admits a turn, reads the turn owner, reads recent spend totals, and returns those details as JSON.

**Call relations**: Core calls this for the live surface's POST route. It contrasts with `_surface_ingest`: this live surface has no `post` callback, so tests can confirm no writeback row is created.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 2 external calls (JSONResponse, body).


##### `_surface_live_stream`  (lines 816–820)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live turn frames as newline-delimited JSON. This lets a client follow a running turn through the surface API.

**Data flow**: It reads the `turn_id` path parameter, converts it to a UUID, creates a streaming response from `_surface_frames`, and sets the response media type for newline-delimited JSON.

**Call relations**: Core calls this for the live surface's GET stream route. It delegates the actual tailing work to `_surface_frames`.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 823–825)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Reads live frames for a turn from the hub and yields them as bytes. Each frame becomes one JSON line.

**Data flow**: It receives surface context and turn id. It asynchronously follows `ctx.tail`, serializes each frame to JSON, adds a newline, and yields the bytes.

**Call relations**: `_surface_live_stream` calls this to supply the body of its streaming response. It relies on `SurfaceContext.tail` to receive live turn updates.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 836–838)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a fixed sample model response. It proves a manifest-contributed model client can be selected and used.

**Data flow**: It receives a model request. It yields one text delta with the canned reply, then yields usage information showing one input and one output token.

**Call relations**: Core calls this through the model spec registered in `manifest()`. The yielded `TextDelta` and `Usage` events mimic the shape of a real streaming model.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 847–848)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the browser debugging endpoint for the sample lease. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It receives no input beyond the lease object. It returns a `CdpEndpoint` with the fixed sample WebSocket URL.

**Call relations**: Browser-related core code calls this after `SampleCdpProvider.lease` or `reattach` returns a lease. It supplies the endpoint the browser engine would connect to.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 850–851)

```
async def token(self) -> str
```

**Purpose**: Returns a reattach token for the sample browser lease. In this sample, the token is just the fixed endpoint URL.

**Data flow**: It receives no extra input and returns the sample CDP URL string. No state changes.

**Call relations**: Core can call this when it wants a durable handle for reconnecting to a browser session. `SampleCdpProvider.reattach` accepts a token but always returns the same canned lease.


##### `SampleCdpLease.aclose`  (lines 853–854)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample browser lease. Because this is a fake lease, closing does nothing.

**Data flow**: It receives no extra input and returns `None`. No network connection or local state is changed.

**Call relations**: Core calls this during browser lease cleanup. It satisfies the lease protocol without needing a real browser.


##### `SampleCdpProvider.lease`  (lines 864–865)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a sample browser debugging lease. It returns a canned lease instead of starting a real browser.

**Data flow**: It receives an optional sandbox session, ignores it, and returns a new `SampleCdpLease`.

**Call relations**: Core calls this through the CDP provider registered in `manifest()`. The returned lease supplies endpoint, token, and close behavior.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 867–868)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reattaches to a sample browser debugging lease. It always returns the same canned lease.

**Data flow**: It receives a token, ignores the token's value, and returns a new `SampleCdpLease`.

**Call relations**: Core calls this when restoring a browser session from a saved token. The sample keeps the path simple so tests focus on provider selection and protocol use.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 879–880)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed credential from the sample auth-proxy backend. An auth proxy supplies credentials to another process without exposing real secrets here.

**Data flow**: It receives workspace id, provider, and account id. It returns a `Credential` with the canned bearer value.

**Call relations**: Core calls this through the auth proxy spec registered in `manifest()`. It proves contributed auth-proxy backends can be selected.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 892–900)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns a canned web search result and direct answer. It proves a contributed search provider can be called.

**Data flow**: It receives a search query. It ignores the query contents and returns one search hit with fixed URL, title, and text, plus a fixed answer string.

**Call relations**: Research tools in core call this through the search provider registered in `manifest()`. `SampleSearchProvider.fetch` supplies the matching fetch path.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 902–903)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches a canned page for a requested URL. It proves the search provider's fetch capability works.

**Data flow**: It receives a fetch request. It copies the requested URL into a `FetchedPage` and fills the page text with fixed sample content.

**Call relations**: Core calls this after search when it wants page contents. The provider advertises `supports_fetch`, so this method is part of the same search backend seam.

*Call graph*: 1 external calls (__init__).


##### `SampleMemorySearch.search`  (lines 912–928)

```
async def search(self, queries: tuple[str, ...], member_id: UUID | None, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match. Memory search means looking up stored facts or past context relevant to a query.

**Data flow**: It receives queries, optional member id, and optional start and end times. It stores those inputs in the extension store, converting ids and dates to strings, then returns one fixed fact-like memory match.

**Call relations**: Core calls this through the memory search provider registered in `manifest()`. The stored record lets tests confirm the scope and time filters were passed through.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleCarrier.__init__`  (lines 940–941)

```
def __init__(self) -> None
```

**Purpose**: Creates the sample sandbox carrier's in-memory file record. A carrier is the backend that creates and controls sandbox environments.

**Data flow**: It receives no external data. It initializes an empty dictionary that will remember bytes written by path.

**Call relations**: Core creates this through the carrier spec registered in `manifest()`. Later `write` stores data in the dictionary.


##### `SampleCarrier.create`  (lines 943–944)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a fake sandbox handle. It does not start a container, but returns the identifiers core expects.

**Data flow**: It receives a sandbox spec. It copies the conversation id into a `SandboxHandle` and uses the fixed sample container id.

**Call relations**: Core calls this after selecting the sample carrier. The returned handle is passed to `exec`, `write`, `export`, `host`, and `destroy`.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 946–949)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a fake command in the sample sandbox. It echoes the command arguments as standard output.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout. It joins the arguments with spaces and returns an `ExecResult` with exit code zero and no stderr.

**Call relations**: Core calls this when executing inside the selected carrier. The echoed output proves the sample carrier, not another backend, was used.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 951–952)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the sample carrier's in-memory file map. It proves core copied content into the selected sandbox backend.

**Data flow**: It receives a handle, path, and bytes. It stores the bytes under that path in the carrier's `written` dictionary.

**Call relations**: Core calls this when placing files into the sandbox. The data may later be inspected by tests through the carrier instance.


##### `SampleCarrier.export`  (lines 954–955)

```
async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None
```

**Purpose**: Exports a fake sandbox path into the blob store. Instead of reading a real file, it stores the path text as bytes.

**Data flow**: It receives a handle, sandbox path, blob store, and destination key. It encodes the path string and writes it into the blob store at the requested key.

**Call relations**: Core calls this when copying sandbox output back to durable blobs. It uses `BlobStore.put` to exercise the same output path as real carriers.

*Call graph*: calls 1 internal fn (put).


##### `SampleCarrier.destroy`  (lines 957–958)

```
async def destroy(self, handle: SandboxHandle) -> None
```

**Purpose**: Destroys the fake sandbox. There is no real container to stop, so it does nothing.

**Data flow**: It receives a sandbox handle and returns `None`. No stored state is changed.

**Call relations**: Core calls this during sandbox cleanup. It completes the carrier protocol alongside create, exec, write, export, and host.


##### `SampleCarrier.host`  (lines 960–961)

```
async def host(self, handle: SandboxHandle, port: int) -> str
```

**Purpose**: Returns a fake host address for a sandbox port. It combines the fixed container name with the requested port.

**Data flow**: It receives a sandbox handle and port number. It returns a string like `sample-container:1234`.

**Call relations**: Core calls this when it needs a reachable host for a sandbox service. The sample response proves the carrier host path is wired.


##### `resolve_workspace`  (lines 964–972)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace for a normal extension route from a bearer token. If the authorization header is missing or malformed, it rejects the request by returning nothing.

**Data flow**: It receives an HTTP request. It reads the `authorization` header, expects `Bearer <token>`, and passes the token to `workspace_claim`; otherwise it returns `None`.

**Call relations**: The manifest uses this as the route identifier for `_hook`. `resolve_surface_workspace` also calls it so normal routes and surface routes share the same bearer-token logic.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 975–977)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies the workspace for a surface request. It reuses the same bearer-token rule as normal routes.

**Data flow**: It receives a request and surface auth object. It ignores the surface auth object and returns whatever `resolve_workspace` finds from the request header.

**Call relations**: The surface specs in `manifest()` use this as their identify function. It delegates to `resolve_workspace` to keep route and surface authentication consistent.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `manifest`  (lines 980–1140)

```
def manifest() -> Manifest
```

**Purpose**: Declares everything the sample extension contributes to UFO. This is the entry point core reads to discover the extension's tools, hooks, routes, backends, surfaces, and other capabilities.

**Data flow**: It creates a sample broker instance and builds a `Manifest` containing tool definitions, object kinds, jobs, routes, onboarding steps, prompt sections, subagents, credential slots, connector provider, hooks, surfaces, source/index/embed/model/hub backends, skills, CDP provider, carrier, auth proxy, search provider, and memory search provider. The returned manifest is the complete contract between this extension and core.

**Call relations**: Core calls this when loading the installed extension. Almost every other function and class in the file is referenced here directly or through a factory, so this function is the map that tells core when to call each sample piece.

*Call graph*: 32 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).

## 📊 State Registers Touched

- `reg-extension-pack-manifest` — The installed pack and extension menu that says what tools, routes, jobs, skills, credentials, and backends exist.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-authorization-grants` — The saved permissions showing which user-approved outside accounts an agent may use.
- `reg-tool-catalog` — The live list of tools the model can call, including their names, descriptions, schemas, and dispatch targets.
- `reg-model-catalog-pricing` — The shared list of available AI models, provider details, limits, credentials, and prices.
- `reg-prompt-skill-library` — The enabled instructions, skill folders, helper profiles, and prompt versions that shape how the agent behaves.
- `reg-browser-session-provider` — The shared way to obtain a browser automation endpoint for a turn, regardless of where the browser runs.
- `reg-connector-broker-catalog` — The known external service brokers and provider actions that let agents use connected services safely.
- `reg-mcp-server-connections` — The configured MCP tool-server connections used to discover and call extra provider tools.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-extension-object-store` — The durable per-workspace storage and named objects that extensions expose or update over time.
- `reg-extension-request-context-envelope` — The request-time workspace, member, conversation, turn, capability, and cleanup context passed from core into extension handlers and tools.
- `reg-web-search-fetch-backend` — The configured web-search and page-fetch provider backend, client settings, and availability used by research, browsing, source, and SDK search calls.
