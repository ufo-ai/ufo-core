# Public SDK extension authoring and runtime facades  `stage-22.2`

This stage is the public front door for people writing UFO extensions. It is shared support used while an extension is loaded and while it runs. Most files here are “facades”: thin, stable import points that hide the project’s private folder layout, so extension code can keep working even if internals move.

The package marker makes ufo.sdk importable. The manifest doorway exposes the types and limits used to describe an extension. The context doorway gives extensions the scoped information they receive from the host while running. The HTTP toolbox provides safe request, response, form, upload, and cookie helpers for routes. The tools, jobs, skills, and sandbox doorways expose the building blocks for user-callable tools, background work, skill definitions, and controlled execution behavior. The observability doorway lets extensions write logs and metrics, meaning structured signals about what happened, using names the core system understands.

The sample extension ties the machine together. It declares examples of these features and records results, proving the public SDK can be used as intended.

## Files in this stage

### SDK package foundations
Package and context entry points establish stable import locations for extension authors.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

In Python, a folder often needs an `__init__.py` file to be treated as a package, meaning other parts of the program can import code from it using names like `ufo.sdk.something`. This file is the front door for the `ufo.sdk` package, but right now that front door is empty: it does not expose helper functions, set up package-wide state, or run startup code. Its value is mostly structural. Without it, some Python tooling or older Python import behavior might not recognize this directory as an importable package. Think of it like a blank label on a drawer: the drawer may be empty for now, but the label tells the system where SDK-related pieces belong.


### `core/src/ufo/sdk/context.py`

`other` · `cross-cutting import-time SDK access`

This file is like a front desk for the extension context API. The real definitions live in internal modules such as `ufo.ext.context`, `ufo.credentials`, and `ufo.schema.records`, but outside code should not need to know those internal paths. Instead, extension handlers can import what they need from `ufo.sdk.context`.

The items re-exported here describe the world an extension can see and use: conversation facts, page and source records, the current agent identity, model access, credential access, a scoped store for extension data, trajectory information, and records such as agent changes or proposal references. In plain terms, these are the objects an extension uses to understand what is happening, read relevant state, ask for allowed resources, and report useful results.

This matters because it keeps the public SDK tidy and stable. If the project later moves the internal implementation around, extension authors can keep using the same import path. Without a file like this, every extension would need to reach into internal modules directly, making outside code more fragile and harder to learn.


### Extension declaration facades
Manifest and job modules expose public declarations for extension metadata and background work.

### `core/src/ufo/sdk/jobs.py`

`other` · `cross-cutting`

This file exists to keep the extension-facing API stable and easy to find. Instead of asking extension authors to know where job machinery lives inside the core codebase, it gathers the approved pieces under `ufo.sdk.jobs`. Think of it like a reception desk: visitors get what they need there, without walking through the building’s private offices.

The main idea is background work that runs per workspace. A workspace is a separate area of data or activity, and a job may only need to run for workspaces where there is actually due work. The exported `owner_candidates` helper lets an extension describe how to find those workspaces, usually by building a database query over its own tables that returns distinct workspace IDs. The comment notes that this query is built each scheduler tick, so time-based checks can use the current time.

The file also re-exports `WorkspaceCandidates`, `store_key_workspaces`, and `JobSpec`, which are the public building blocks for describing candidate workspaces, storing workspace-related context, and defining a job. There is no behavior here beyond importing and re-publishing these names; its value is in drawing a clean boundary between public SDK and private core internals.


### `core/src/ufo/sdk/manifest.py`

`other` · `cross-cutting import time`

This file is like a front desk for the extension software development kit, often called an SDK, which means the public tools outside developers are meant to use. It does not create new behavior of its own. Instead, it gathers many names from deeper parts of the project and re-publishes them from one safe, official place.

That matters because extensions need to describe what they provide: credentials, hooks, prompt sections, search providers, conversation slots, image previews, workspace changes, and other manifest pieces. If extension authors imported those pieces directly from internal modules, the project would have less freedom to reorganize its own folders later. By importing from this file, extensions depend on a stable public contract rather than on private implementation details.

The repeated `as SameName` imports are intentional. They make it explicit that each imported class, constant, or helper is part of this module’s public surface. The comment also explains a project rule: package `__init__.py` files stay empty, so named modules like this one carry the public API. In short, this file is not a machine that does work; it is a carefully labeled shelf where extension builders can find the official manifest building blocks.


### Runtime capability facades
HTTP, sandbox, skills, and tools modules provide stable public imports for extension runtime behavior.

### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file defines the HTTP surface that extension authors are meant to use. A route receives a `Request`, which represents the incoming browser or API call, and returns a `Response`, which is what gets sent back. The file re-exports common response types such as HTML pages, JSON replies, plain text, redirects, and streaming responses, so callers can stay inside `ufo.sdk` instead of depending directly on Starlette, the lower-level web framework underneath.

It also exposes `UploadFile` and `FormData`, which are used when a browser submits a form, especially one containing files. If the submitted form is malformed, `FormParserError` is the error route code can catch to return a clean “bad request” response.

The one piece of behavior in this file is `set_session_cookie`. Cookies are small values stored by the browser and sent back on later requests. Session cookies are sensitive because they can prove who a user is. This helper forces every UFO-set session cookie to be secure: browser JavaScript cannot read it, it is only sent over HTTPS, and it is tied to the exact host that set it. In everyday terms, it prevents a key meant for one front door from also working at neighboring doors.

#### Function details

##### `set_session_cookie`  (lines 23–34)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none']) -> None
```

**Purpose**: Sets a session cookie on an outgoing HTTP response in the approved, safe way. It prevents route code from accidentally creating a cookie that is readable by browser scripts, sent over insecure connections, or shared too widely across domains.

**Data flow**: It receives a response object, a cookie name, a token value, and a `SameSite` choice, which controls when the browser sends the cookie during cross-site navigation. It passes those values to the response’s cookie-setting method, while always adding `HttpOnly` and `Secure`. The response is changed in place so that, when it is sent to the browser, it includes the new cookie header; the function itself returns nothing.

**Call relations**: Route or session-related code calls this when it needs to attach a session token to a response. This function then hands the actual low-level work to Starlette’s `Response.set_cookie`, but it fixes the important safety settings first so callers do not have to remember them or choose weaker options.

*Call graph*: 1 external calls (set_cookie).


### `core/src/ufo/sdk/sandbox.py`

`other` · `cross-cutting import-time API surface`

This module does not create new behavior. Its job is to gather the sandbox API that outside extensions are meant to use and re-export it under `ufo.sdk.sandbox`. In plain terms, it is like a labeled front desk: the actual tools live in other rooms, but this file tells visitors which tools are officially available and where to pick them up.

The sandbox is the controlled environment where work can run safely. Extensions can register a `CarrierSpec`, implement the `Carrier` protocol, and use value objects such as `SandboxSpec`, `SandboxSession`, and `ExecResult` to describe, start, and talk to that environment. This file also exposes constants such as workspace paths, bootstrap flags, and proxy-related names that other code needs to agree on.

This matters because public imports are a promise. If extension code had to import directly from internal modules like `ufo.sandbox.session`, future refactors could easily break it. By re-exporting the public surface here, the project can move internal code around while keeping the outside-facing SDK stable. There are no functions defined in this file; everything listed is imported from the real implementation modules and made available under this public module name.


### `core/src/ufo/sdk/skills.py`

`io_transport` · `cross-cutting`

This file is a small public doorway into the project’s skill system. A “skill” here is a runtime capability contributed by an extension, and extension code needs a clear, supported way to refer to the skill value object and helper functions without reaching into deeper internal modules.

The file imports three names from `ufo.skills.runtime` and makes them available through `ufo.sdk.skills`: `RuntimeSkill`, `parse_skill_content`, and `skill_mount_root`. This is like putting commonly needed tools on a labeled shelf near the entrance, instead of asking users to search through the workshop.

The comment at the top explains why this exists as a separate named module. The `ufo.sdk` package keeps its `__init__.py` empty because the project forbids code there, so public SDK names are exposed through files like this one instead.

Without this file, extension authors would need to import directly from the internal runtime module. That would make their code more tightly tied to the project’s internal layout, and future refactors would be harder because outside users would depend on private paths.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting import-time public API`

This module is a public doorway into the tool system. Instead of asking extension authors to import classes and constants from deeper internal files, it re-exports the names they are expected to use: tool definitions, tool contexts, tool results, text and image content types, file-change limits, request metadata, and a connection-unavailable error.

Think of it like a front desk in a large office building. Visitors do not need to know which floor every department is on; they go to the front desk and get what they need through a stable, public route. In the same way, extension code can import from `ufo.sdk.tools` without learning where `ufo.tools.context`, `ufo.tools.registry`, or `ufo.grants` live internally.

There is no runtime algorithm here. The file simply imports selected names and exposes them again under the same names. This matters because it creates a clear boundary between the public software development kit, or SDK, and the private implementation. If the internal package structure changes later, this file can keep the public import path steady, so extensions are less likely to break.


### Observability and validation
Observability exports let extensions emit signals, while the sample extension validates the public SDK surface end to end.

### `core/src/ufo/sdk/o11y.py`

`io_transport` · `cross-cutting`

This file is a small public interface for observability, often shortened to “o11y,” which means the tools used to understand what a running system is doing. In everyday terms, it is like a service counter and incident log for extensions: extensions can write structured logs, raise warnings, emit approved metrics, and use turn profiling through names provided by the core project.

The important design choice is that this file does not create its own logging or metrics logic. Instead, it re-exports selected functions from `ufo.o11y`, the core observability module. That means extension authors import from the SDK path, while the real rules and registry stay in core.

This matters because metrics are most useful when they are predictable and centrally known. If every extension could invent metric names freely, dashboards, alerts, and fleet-wide analysis would become messy and unreliable. By routing extension observability through this file, the project gives extensions a stable public API while keeping the actual metric surface controlled in one place.

There are no functions defined here directly. Its job is to expose approved tools under the SDK namespace.


### `extensions/sample/ufo_ext_sample.py`

`test` · `cross-cutting conformance extension registration and request/job/tool handling`

Think of this file as a showroom model for extension authors and a safety check for the platform. It imports only `ufo.sdk`, which is the public doorway extensions are supposed to use. Its `manifest()` function advertises many things an extension can contribute: tools, background jobs, web routes, onboarding, hooks, connectors, surfaces, model backends, search, memory search, browser/CDP access, sandbox carriers, objects, skills, and more.

Most implementations here are deliberately simple and canned. The echo tool echoes text. The search provider returns one fixed result. The model backend streams one fixed reply. The sandbox carrier stores files in memory. That simplicity is the point: tests are not checking a real external service. They are checking that the core system can discover an extension, call its public handlers, pass the right context, enforce permissions, and store/read durable results.

A recurring pattern is that handlers write a small record into the extension store. That store is real workspace-scoped storage, not a fake log. Without this file, the project would lack a broad end-to-end probe that catches breakage in the public SDK seam before real third-party extensions are affected.

#### Function details

##### `_echo`  (lines 270–274)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It saves the received message in the extension's durable store, then returns the same message as tool output.

**Data flow**: It receives a tool context and parsed echo arguments. It checks that an extension context is present, stores the argument data under a known key, and returns a text result containing the input message.

**Call relations**: The manifest registers this as the `sample_echo` tool. A pre-tool hook can deny this tool before it runs, which lets tests prove denial stops dispatch.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 277–299)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Runs the sample note tool that writes to a database table owned by the extension. It proves an extension migration-created table can be used through the public transaction API.

**Data flow**: It receives note text and the tool context. It opens a workspace-scoped database transaction, updates or inserts the note for the current workspace, reads it back, and returns the stored text.

**Call relations**: The manifest registers this as a normal tool and also grants it to the sample subagent. It uses SQLAlchemy statements to exercise real database access rather than the extension key-value store.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 302–328)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample background job. It records that the job ran and, when richer context is available, probes trajectories, proposes an agent prompt change, writes a workspace file, and runs a sandbox probe.

**Data flow**: It receives an extension context. It writes a job marker, optionally reads trajectory information, submits a proposed prompt edit, optionally writes a file into a conversation workspace, optionally runs a command against that workspace, and stores each observed result.

**Call relations**: The manifest registers this as the `sample_tick` job. It calls core context methods for trajectories and proposed changes so conformance tests can verify off-turn job context is wired correctly.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 331–334)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Implements the sample HTTP route. It echoes the request body and records that the route was hit.

**Data flow**: It receives an extension context and HTTP request, reads the raw body, stores that body in the extension store, and returns a plain text response with the same body.

**Call relations**: The manifest exposes this through a POST route. The route uses `resolve_workspace` to decide which workspace the request belongs to before this handler runs.

*Call graph*: 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 369–380)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists sample widgets stored by this extension. It shows how an extension can expose editable objects through the object API.

**Data flow**: It receives a tool context and list query, reads all extension-store keys with the widget prefix, turns stored rows into object rows with public fields, and returns a paged object list.

**Call relations**: The object kind registered in `manifest()` calls this when users list `sample_widget` objects. It relies on `WidgetStore._ext` to get the extension context.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 382–389)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one sample widget by name. It returns the saved widget spec and timestamps if the widget exists.

**Data flow**: It receives a tool context and widget name, reads the matching extension-store key, validates the stored shape, and returns object detail or nothing if no row exists.

**Call relations**: The object API calls this for reads of `sample_widget`. Like the other widget operations, it uses `WidgetStore._ext` to require a real extension context.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 391–398)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Provides a status hook for a widget object, but this sample has no extra status to report.

**Data flow**: It receives context, name, and an optional expected generation value, then simply returns no status.

**Call relations**: The object API may call this after or alongside object reads. It exists to satisfy the full object-store protocol for the sample kind.


##### `WidgetStore.apply`  (lines 400–416)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It preserves the original creation time and refreshes the update time.

**Data flow**: It receives a widget name and desired spec. It reads any existing stored widget, chooses a creation timestamp, writes the new stored widget record, and returns no separate value.

**Call relations**: The object API calls this for create/update requests on `sample_widget`. It uses `WidgetStore._ext` and stores the result in the extension key-value store.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, now).


##### `WidgetStore.delete`  (lines 418–427)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only if the speaker is a workspace admin. This proves object mutations can enforce permissions.

**Data flow**: It receives a widget name and context. It asks the tool context whether the speaker is an admin; if not, it raises an admin-required error, otherwise it deletes the widget key.

**Call relations**: The object API calls this for delete requests. It depends on the core `speaker_is_admin` check and uses `WidgetStore._ext` to access extension storage.

*Call graph*: calls 2 internal fn (speaker_is_admin, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 429–432)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Pulls the extension context out of a tool context. It fails loudly if the object store was invoked without that required context.

**Data flow**: It receives a tool context, checks its `ext` field, and returns the extension context or raises an error.

**Call relations**: The widget list, get, apply, and delete methods all call this small guard before touching extension storage.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 440–444)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample read-only relic object. It represents system-produced objects that can be inspected but not authored.

**Data flow**: It ignores storage, creates one fixed object row for the sample relic, applies normal paging, and returns the page.

**Call relations**: The manifest registers this store for `sample_relic`. The object API calls it when listing relics.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 446–451)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Fetches the one canned relic if the requested name matches. It returns no result for any other name.

**Data flow**: It receives a name, compares it with the fixed relic name, and returns object detail containing a read-only inscription or `None`.

**Call relations**: The object API calls this for reads of `sample_relic`. It constructs the sample `RelicSpec` returned to callers.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 453–460)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small status payload for the relic. The status says the relic was 'excavated', reinforcing that it is system-produced.

**Data flow**: It receives the normal object status inputs and returns a fixed dictionary with an origin field.

**Call relations**: The object API can call this for relic status. Unlike widget status, this sample read-only kind returns visible status data.


##### `RelicStore.apply`  (lines 462–471)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a relic. This proves an object kind can be read-only while still supporting list and get.

**Data flow**: It receives the proposed relic spec and immediately raises a 'verb not supported' error with the sample refusal message.

**Call relations**: The object API calls this on apply requests for `sample_relic`; tests expect the refusal rather than a stored change.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 473–480)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a relic. This keeps the sample relic read-only.

**Data flow**: It receives the delete request inputs and raises a 'verb not supported' error without changing anything.

**Call relations**: The object API calls this on delete requests for `sample_relic`; it mirrors `RelicStore.apply` for the delete verb.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 499–508)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one deterministic content page from the source configuration. It proves source backends can be registered, polled, and turned into pages.

**Data flow**: It receives typed source config, an optional cursor, and auth information. It builds a page using the configured topic as body and title source, then returns a sync result with no next cursor.

**Call relations**: `_setup` registers this source backend during onboarding. The core source sync runner later calls `fetch` through the public source interface.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 521–523)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the sample in-memory search index. A chunk is a small piece of text plus metadata used for retrieval.

**Data flow**: It receives a tuple of chunks and stores each one in a dictionary keyed by its digest, replacing any existing chunk with the same digest.

**Call relations**: The manifest registers `SampleIndex` as an index backend. Core indexing code calls this when it wants the backend to remember chunks.


##### `SampleIndex.delete`  (lines 525–527)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all indexed chunks belonging to a given scope. A scope identifies one owner, such as one source or document.

**Data flow**: It receives an index scope, finds stored chunks whose owner kind and owner id match, and removes them from the in-memory dictionary.

**Call relations**: Core index maintenance calls this when a whole scope should be cleared. It uses `_in_scope` for the matching rule.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 529–530)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether this sample index has any chunks for a given scope.

**Data flow**: It receives a scope, scans the stored chunks, and returns true if at least one chunk belongs to that scope.

**Call relations**: Core index code can use this as a quick existence check. It shares the same `_in_scope` test as delete and prune.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 532–538)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes stale chunks in a scope while keeping a named set of current chunks.

**Data flow**: It receives a scope and a keep-set of digests. It deletes stored chunks that are in the scope but whose digest is not in the keep-set.

**Call relations**: Core index refresh code calls this after deciding which chunks should remain. It uses `_in_scope` to avoid deleting chunks from other owners.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 540–549)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches indexed text by counting query words. It is a simple text search used only to prove the index interface works.

**Data flow**: It receives a query, allowed subjects, owner kind, and limit. It filters chunks to that owner and subject set, counts query-term occurrences, converts positive scores into hits, sorts by score, and returns the top hits.

**Call relations**: Core retrieval calls this for keyword-style search. It uses `SampleIndex._scoped` to filter candidates and `_hit` to shape results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 551–559)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches indexed chunks by vector similarity. A vector is a list of numbers representing meaning; this sample uses a simple dot product.

**Data flow**: It receives an embedding vector plus filters and a limit. It filters candidate chunks, scores each by `_dot`, converts positive scores into hits, sorts them, and returns the top hits.

**Call relations**: Core retrieval calls this for embedding-based search. It depends on `SampleIndex._scoped`, `_dot`, and `_hit`.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 561–566)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters stored chunks to the owner kind and subject set requested by a search.

**Data flow**: It receives subjects and an owner kind, scans the in-memory chunk dictionary, and returns only chunks matching both conditions.

**Call relations**: `SampleIndex.lexical` and `SampleIndex.vector` call this before scoring so searches do not leak chunks from other subjects or owner types.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 575–576)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for every input text. It proves an extension-provided embedding backend can be selected and called.

**Data flow**: It receives a tuple of texts and returns a tuple of identical sample vectors, one per text.

**Call relations**: The manifest registers this as the sample embed backend. Core embedding code calls it through the public embed interface.


##### `_in_scope`  (lines 579–580)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a chunk belongs to a particular index scope.

**Data flow**: It receives a chunk and scope, compares owner kind and owner id, and returns a yes/no result.

**Call relations**: Sample index delete, existence check, and prune operations call this to use one shared matching rule.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 583–586)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes a dot product between two vectors. This gives the sample vector search a simple similarity score.

**Data flow**: It receives two number tuples. If either is empty it returns zero; otherwise it multiplies matching positions and sums the products.

**Call relations**: `SampleIndex.vector` calls this while scoring candidate chunks for embedding search.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 589–598)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Turns an indexed chunk and score into a search hit result.

**Data flow**: It receives a chunk and numeric score, copies the chunk's identifying fields and text into a `Hit`, and attaches the score.

**Call relations**: Both lexical and vector search call this after they decide a chunk matched.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 601–608)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It records onboarding completion and registers the sample source for syncing.

**Data flow**: It receives an extension context, stores an onboarding marker, builds typed source configuration, and asks core to register that source for the shared subject.

**Call relations**: The manifest registers this as an onboarding step. It hands off to the core source registration method.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 611–614)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Always denies the sample echo tool when used as a pre-tool hook. This proves a hook can stop a tool before the tool handler runs.

**Data flow**: It receives hook context and returns a denial outcome with a fixed reason.

**Call relations**: The manifest attaches this to the `pre_tool_use` event for `sample_echo`. Because it returns `Deny`, `_echo` should not be dispatched in that path.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 617–625)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool-use events. It proves post-success hooks receive the tool name after a tool completes.

**Data flow**: It receives hook context, checks whether the payload is a successful post-tool-use event, stores the tool name, and returns no special outcome.

**Call relations**: The manifest registers this for all `post_tool_use` events. It records only successful calls; failures go to `_record_post_failure` instead.


##### `_record_post_failure`  (lines 628–634)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed tool-use events. It proves failed tool calls are reported on a separate hook path.

**Data flow**: It receives hook context, checks for a post-tool-use-failure payload, stores the failed tool name, and returns no special outcome.

**Call relations**: The manifest registers this for `post_tool_use_failure`. It complements `_record_post`, which records successful tool calls.


##### `_record_stop`  (lines 637–643)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer at the end of a turn. This proves stop hooks see the answer before it is committed.

**Data flow**: It receives hook context, checks for a stop payload, stores the answer text, and returns no special outcome.

**Call relations**: The manifest registers this for the `stop` event, which fires near turn completion.


##### `_record_pre_compact`  (lines 646–653)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information before conversation compaction. Compaction means shrinking stored conversation context to fit limits.

**Data flow**: It receives hook context, checks for a pre-compaction payload, stores the reason and token estimate before compaction, and returns no special outcome.

**Call relations**: The manifest registers this for `pre_compact`. It pairs with `_record_post_compact` to prove both sides of compaction are observable.


##### `_record_post_compact`  (lines 656–668)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction. It captures the summary and before/after token counts.

**Data flow**: It receives hook context, checks for a post-compaction payload, stores summary and token counts, and returns no special outcome.

**Call relations**: The manifest registers this for `post_compact`. Tests can compare it with `_record_pre_compact` records.


##### `_record_page_change`  (lines 671–684)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records delivered page-change events. It also notes whether a model client was available in the off-turn context.

**Data flow**: It receives hook context, checks for a page-change batch, stores the page ids and a yes/no flag showing whether `ctx.ext.model` was wired, and returns no special outcome.

**Call relations**: The manifest registers this for `page_change`. It proves data-plane events reach extension hooks with useful context.


##### `_SampleConnectorOAuth.authorize_url`  (lines 697–698)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds a canned OAuth authorization URL. OAuth is the common web flow where a user grants access to another service.

**Data flow**: It receives a state value and redirect URI, inserts both into the fixed sample authorize URL, and returns the URL string.

**Call relations**: The connector provider uses this during the connect flow before token exchange.


##### `_SampleConnectorOAuth.exchange`  (lines 700–703)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the sample OAuth exchange without contacting a real provider. It returns a fixed connected account id.

**Data flow**: It receives code, redirect URI, workspace id, and state, ignores the live-provider details, and returns an OAuth account object with the sample account id.

**Call relations**: The connector flow calls this after authorization. The returned account id is later used by connector tools and broker credentials.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 716–717)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample broker's tool catalog. A broker is the service-side adapter that tells UFO which connected-account tools exist.

**Data flow**: It receives workspace, provider, and query information, ignores the query, and returns one fixed broker tool.

**Call relations**: `_SampleBroker.search` calls this while building broker search results. Core connector discovery may also call it directly.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 719–726)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the one sample broker tool. It refuses unknown tool slugs.

**Data flow**: It receives a tool slug. If the slug is not the sample slug, it raises an unknown-tool error; otherwise it returns a broker tool with a small JSON-style input schema.

**Call relations**: Core connector tooling calls this when it needs details for a dynamic broker tool before execution.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 728–745)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Executes the sample broker tool by echoing the call details. It proves the connector execution path passes provider, arguments, account, and idempotency data correctly.

**Data flow**: It receives workspace id, provider, slug, arguments, connected account id, and optional idempotency key. It rejects unknown slugs, otherwise returns those values as a dictionary.

**Call relations**: Core dynamic connector tools call this through the broker. Its response can later be inspected or used by `file_outputs`.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 747–758)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Extracts produced file references from a broker response. It treats any `file_output_urls` argument as a list of files made by the tool.

**Data flow**: It receives the broker response dictionary, looks inside its echoed arguments for URL strings, and returns broker-file records named from each URL path.

**Call relations**: Connector file-bridging code calls this after broker execution to decide what files should be fetched into the workspace.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 760–786)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Stages a file upload for a connector tool. It simulates a content-addressed store and reports whether bytes still need to be uploaded.

**Data flow**: It receives file metadata, builds a key from md5 and filename, and checks whether that key was already minted. New keys get a file URL and upload argument; repeated keys get no put URL but the same argument shape.

**Call relations**: Connector upload handling calls this before executing broker tools that need files. The in-memory minted set lets tests prove deduplication behavior.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 788–791)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns broker search results with one tool and one canned plan. The plan is a short instruction for how to use the available tool.

**Data flow**: It receives workspace, provider, and query, calls `_SampleBroker.tools` to get the tool list, and wraps that list with the fixed plan.

**Call relations**: Core connector search calls this when looking for broker tools relevant to a query.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 793–794)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a bearer credential for the sample connected account. A bearer credential is a token sent as proof of authorization.

**Data flow**: It receives workspace, provider, and account id, prefixes the account with the sample token prefix, and returns it as a credential.

**Call relations**: Connector egress or auth plumbing calls this when it needs a provider credential for the connected account.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 805–823)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector's server-side tool. It resolves which connected account is bound to the current agent and records that account plus the idempotency key.

**Data flow**: It receives tool context and input arguments, requires an extension context, asks core for the connected account for the sample provider, stores account/tool/idempotency details, and returns the account id as text.

**Call relations**: The manifest registers this under the connector provider. It calls the core `connector_account` helper, so tests can verify grants and account binding.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 833–834)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Turns one bytes value into a tiny async byte stream. It is used to simulate streaming file upload input.

**Data flow**: It receives bytes, yields that same bytes value once, and then ends.

**Call relations**: `_surface_ingest` calls this when an inbound surface message includes text that should be written as a workspace file.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_ingest`  (lines 837–864)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message on the durable sample surface. A surface is an external-facing integration point, like an email or chat bridge.

**Data flow**: It reads JSON from the request, links or finds a member identity, gets or creates a conversation, optionally writes inbound text as a workspace file, admits a turn using the external id as an idempotency key, and returns ids plus whether a run opened.

**Call relations**: The manifest registers this as the POST route for `sample_surface`. It calls several `SurfaceContext` methods to prove identity linking, conversation creation, file writing, and turn admission work together.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 867–868)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed reference for an outbound surface post. It stands in for sending a reply to an external system.

**Data flow**: It receives surface context and writeback details, ignores their contents, and returns the sample posted reference string.

**Call relations**: The durable surface in `manifest()` uses this as its post callback when UFO writes back an answer.


##### `_surface_attach`  (lines 871–876)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies shared output files into delivered-file keys. It proves surface attachment delivery can stream blobs out and back in.

**Data flow**: It receives writeback artifacts, reads each artifact's blob stream, and writes that stream to a new delivered key containing the turn id and filename.

**Call relations**: The durable surface uses this after `_surface_post` when there are files to attach. It exercises the blob store streaming APIs.


##### `_surface_live_admit`  (lines 879–901)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message on the sample live surface. Live surfaces tail hub events instead of relying on durable writeback rows.

**Data flow**: It reads JSON, finds or adopts a member identity, gets or creates a conversation, admits a turn, reads the turn owner and recent spend rollup, and returns those details as JSON.

**Call relations**: The manifest registers this under `sample_live`. It contrasts with `_surface_ingest` by not registering a post callback.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 904–908)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live turn frames for a live surface. The response is newline-delimited JSON, meaning one JSON object per line.

**Data flow**: It reads the `turn_id` path parameter, converts it to a UUID, wraps `_surface_frames` in a streaming HTTP response, and sets the media type.

**Call relations**: The live surface route calls this for GET stream requests. It delegates the actual frame iteration to `_surface_frames`.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 911–914)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Reads live frames from the surface hub and yields them as newline-ended JSON bytes.

**Data flow**: It receives a surface context and turn id, opens a tail subscription, loops over frames, serializes each frame to JSON, appends a newline, and yields bytes.

**Call relations**: `_surface_live_stream` calls this to provide the body of the streaming response. It uses the core `tail` capability.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 925–928)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a canned model response. It proves an extension-contributed model client can be selected, called, and priced.

**Data flow**: It receives a model request, then yields a stream-start event, one text delta with the fixed reply, and usage with one input and one output token.

**Call relations**: The model spec in `manifest()` builds this client. Core model-running code consumes its async event stream.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 938–939)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the fixed browser debugging endpoint for the sample lease. CDP means Chrome DevTools Protocol, a way to control a browser.

**Data flow**: It receives no extra input and returns a `CdpEndpoint` containing the sample WebSocket URL.

**Call relations**: Browser-driving code calls this on a lease produced by `SampleCdpProvider.lease` or `reattach`.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 941–942)

```
async def token(self) -> str
```

**Purpose**: Returns a durable token that can be used to reattach to the same sample browser endpoint.

**Data flow**: It receives no extra input and returns the fixed sample CDP URL as the token.

**Call relations**: Core browser code can store this token and later pass it to `SampleCdpProvider.reattach`.


##### `SampleCdpLease.place_file`  (lines 944–945)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Reports where a file is available to the browser. In this sample, the path is already usable, so it returns it unchanged.

**Data flow**: It receives a path and a byte-reader callback, ignores the reader, and returns the original path.

**Call relations**: Browser automation code calls this when it needs to make a file available inside the leased browser environment.


##### `SampleCdpLease.download_dir`  (lines 947–948)

```
async def download_dir(self) -> str
```

**Purpose**: Reports the directory where sample browser downloads appear.

**Data flow**: It receives no extra input and returns the fixed download directory path.

**Call relations**: Browser code calls this when it needs to know where to look for downloaded files.


##### `SampleCdpLease.fetch_download`  (lines 950–951)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a downloaded file from the sample download directory.

**Data flow**: It receives a download guid, builds a path under the fixed download directory, reads the file bytes in a worker thread, and returns those bytes.

**Call relations**: Browser code calls this after a download is known. It uses `asyncio.to_thread` so the blocking file read does not block the async event loop.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 953–954)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample CDP lease. There is nothing real to clean up, so it does nothing.

**Data flow**: It receives no extra input and returns without changing state.

**Call relations**: Core code calls this during browser lease cleanup, proving the close method exists on the protocol.


##### `SampleCdpProvider.lease`  (lines 964–965)

```
async def lease(self, sandbox: SandboxSession | None=None) -> CdpLease
```

**Purpose**: Creates a new sample browser lease. It returns a canned lease rather than starting a real browser.

**Data flow**: It optionally receives a sandbox session, ignores it, and returns a new `SampleCdpLease`.

**Call relations**: The manifest registers this provider. Browser setup code calls `lease` when selecting the sample CDP backend.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 967–968)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reattaches to a sample browser lease from a token. The sample always returns the same canned lease.

**Data flow**: It receives a token, ignores its value, and returns a new `SampleCdpLease`.

**Call relations**: Core browser code calls this when resuming a previously leased browser endpoint.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 979–980)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed credential for the sample auth proxy backend. An auth proxy supplies credentials to another subsystem without exposing real secrets here.

**Data flow**: It receives workspace, provider, and account id, ignores the details, and returns a credential with the fixed bearer value.

**Call relations**: The manifest registers this auth proxy. Sync or egress code calls it through the public auth proxy interface.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 992–1000)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns one canned web search result and a direct answer. It proves an extension search backend can be selected and called.

**Data flow**: It receives a search query, ignores its contents, and returns a result object containing one fixed hit plus a fixed answer string.

**Call relations**: The manifest registers this as a search provider. Research tools or core search flows call it through the public search interface.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1002–1003)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches a canned page for a requested URL. It proves the optional fetch side of a search provider works.

**Data flow**: It receives a fetch request, copies the requested URL into the response, and returns fixed page text.

**Call relations**: Search tooling calls this when it wants page text after a search hit. The provider advertises `supports_fetch` as true.

*Call graph*: 1 external calls (__init__).


##### `SampleMemorySearch.search`  (lines 1012–1030)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match. Memory search finds saved information relevant to a conversation or subject set.

**Data flow**: It receives queries, a source reader with subjects, and optional start/end times. It stores those search inputs in the extension store, then returns one fixed memory match.

**Call relations**: The manifest registers this as a memory search provider. Core memory recall calls it, and tests read the stored inputs back.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1032–1033)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list. The sample has one kind.

**Data flow**: It receives no extra input and returns a tuple containing the sample memory kind.

**Call relations**: Core listing code can call this before asking `list_recent` for recent memories of supported kinds.


##### `SampleMemorySearch.list_recent`  (lines 1035–1061)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a request for recent memories and returns one canned result page.

**Data flow**: It receives subjects, limit, optional kinds, and optional cursor. It stores those inputs in the extension store, including cursor details if present, then returns a listing page with one fixed memory match.

**Call relations**: Core memory-listing flows call this through the provider built by `manifest()`.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1073–1074)

```
def __init__(self) -> None
```

**Purpose**: Creates the sample sandbox carrier's in-memory file store. A carrier is the backend responsible for creating and interacting with a sandbox.

**Data flow**: It receives no input beyond the new object and initializes an empty dictionary mapping paths to bytes.

**Call relations**: The carrier factory registered in `manifest()` constructs this when core selects the sample carrier.


##### `SampleCarrier.create`  (lines 1076–1081)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a fake sandbox handle. It does not start a real container, but returns the identifying information core expects.

**Data flow**: It receives a sandbox spec, copies the conversation id and run token, uses a fixed container id, and returns a sandbox handle.

**Call relations**: Sandbox orchestration calls this when it needs a new sandbox from the sample carrier.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1083–1090)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Attaches to a fake existing sandbox when a resume id is present.

**Data flow**: It receives a sandbox spec. If there is no resume id, it returns `None`; otherwise it returns a handle using the resume id as the container id.

**Call relations**: Sandbox orchestration calls this before creating a new sandbox, to see whether an existing one can be resumed.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1092–1095)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Pretends to run a command in the sandbox by echoing the command arguments.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout. It joins the arguments with spaces and returns an execution result with that text, empty stderr, and exit code zero.

**Call relations**: Core sandbox command execution calls this after selecting the sample carrier.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1097–1098)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the sample carrier's in-memory file store.

**Data flow**: It receives a sandbox handle, path, and bytes, then stores the bytes under that path.

**Call relations**: Core file-copy code calls this when placing files into the selected sample sandbox.


##### `SampleCarrier.read`  (lines 1100–1103)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads bytes back from the sample carrier's in-memory file store.

**Data flow**: It receives a sandbox handle and path. If the path was never written it raises `FileNotFoundError`; otherwise it yields the stored bytes.

**Call relations**: Core file retrieval or filesystem operations call this to stream data out of the sample sandbox.


##### `SampleCarrier.file_op`  (lines 1105–1108)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level sandbox filesystem operation using the shared helper. This avoids duplicating file operation rules in the sample carrier.

**Data flow**: It receives a handle, operation name, and parameters, then passes them to `sbxfs_file_op` with this carrier as the read/write backend.

**Call relations**: Core sandbox filesystem APIs call this for operations beyond raw read and write.

*Call graph*: 1 external calls (sbxfs_file_op).


##### `SampleCarrier.dial`  (lines 1110–1111)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a fake network target inside the sample container. Dialing means asking how to reach a port exposed by the sandbox.

**Data flow**: It receives a handle and port, builds a host string from the fixed container name and port, marks TLS as false, and returns the target.

**Call relations**: Core networking code calls this when it wants to connect to a service running in the selected sandbox.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1114–1122)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace for a normal extension route from a bearer token. If the authorization header is missing or malformed, it rejects by returning nothing.

**Data flow**: It reads the request's `Authorization` header, expects `Bearer <token>`, and passes the token to `workspace_claim` to extract a workspace id.

**Call relations**: The sample route uses this as its synchronous identify function. `resolve_surface_workspace` also delegates to it.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1125–1127)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies the workspace for a surface request using the same bearer-token rule as normal routes.

**Data flow**: It receives a request and surface auth object, ignores the auth object, and returns the result of `resolve_workspace`.

**Call relations**: Both sample surface specs use this as their identify function before surface route handlers run.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1130–1131)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: Provides the summary hook for the sample conversation slot, but this sample has nothing to summarize.

**Data flow**: It receives conversation slot context and returns no content or side effect.

**Call relations**: The manifest registers it with the sample conversation slot provider.


##### `_conversation_slot_read`  (lines 1134–1135)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Provides the read hook for the sample conversation slot. It returns an empty set of workspace changes.

**Data flow**: It receives conversation slot context and returns a `WorkspaceChanges` object with no changes and `truncated` set to false.

**Call relations**: The conversation slot provider registered in `manifest()` calls this when the slot content is read.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1138–1311)

```
def manifest() -> Manifest
```

**Purpose**: Declares everything this sample extension contributes to UFO. It is the main entry point the extension loader calls.

**Data flow**: It creates the sample broker, builds a `Manifest` containing tools, objects, jobs, routes, hooks, surfaces, backends, credentials, skills, and conversation slots, and returns that manifest to core.

**Call relations**: Core extension loading calls this to discover the extension. Almost every other function or class in the file is referenced from this manifest as a handler, provider, factory, or store.

*Call graph*: 34 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).
