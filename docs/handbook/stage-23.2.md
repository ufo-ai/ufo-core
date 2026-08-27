# Public SDK Core Facades and Sample Coverage  `stage-23.2`

This stage is shared behind-the-scenes support for people writing UFO extensions. It creates the public “front door” of the SDK, so extension code can import safe, stable names without depending on the project’s private internal folders. The package marker `__init__.py` makes `ufo.sdk` importable. The facade files then gather useful pieces into clear entry points: `context.py` exposes conversation state, agent identity, credentials, models, facts, and page records; `manifest.py` exposes the types and constants used to describe an extension; `models.py` exposes approved message, model, tool-call, pricing, and helper objects; and `tools.py` exposes the public tool-building pieces. Two small utility facades keep shared wording consistent: `delivery_register.py` publishes the common delivery-register prompt block, and `untrusted.py` gives extensions the same marker the core uses for text that should not be blindly trusted. Finally, the sample extension acts like a full test drive. It uses nearly every public feature and records the results, proving the SDK surface works in the real system.

## Files in this stage

### SDK Package Anchor
The package initializer establishes the public SDK namespace that the remaining facades populate.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a directory can contain an `__init__.py` file to tell Python, “treat this folder as an importable package.” That means other parts of the project can refer to code under this directory using imports such as `ufo.sdk.something`.

Because the file is empty, it does not set up shared objects, expose convenience imports, or run startup code when the package is imported. Its value is structural: it helps organize the SDK code into a clear namespace. A namespace is like a labeled drawer in a filing cabinet; it keeps related code together and prevents names from clashing with unrelated parts of the project.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.sdk` might behave differently or fail in environments that expect traditional package markers. Keeping it present makes the package boundary explicit and predictable.


### Core SDK Facades
These stable import surfaces expose context, manifest, model, and tool APIs without requiring extensions to depend on internal modules.

### `core/src/ufo/sdk/context.py`

`other` · `import time and extension handling`

This file does not define new behavior. Instead, it acts like a well-labeled front desk for the SDK. Extension code can import context-related tools from `ufo.sdk.context` without needing to know where each tool lives inside the larger project.

That matters because extensions should depend on a simple, stable public interface, not on the project’s internal folder layout. If every extension imported directly from internal modules such as `ufo.ext.context`, `ufo.access.credentials`, or `ufo.schema.records`, then moving code around inside the project could break outside users. This file reduces that risk by collecting the approved public names in one place.

The names exported here cover several related ideas: the current agent, the context passed to extension handlers, access to credentials and models, stored conversation facts, page and source records, trajectory information, and records describing agent changes or proposals. In plain terms, these are the pieces an extension may need to understand “who am I acting as, what conversation or page am I in, what data can I read, and what am I allowed to use?”

The repeated `as Name` imports are intentional. They make clear that these objects are being exposed under the same public names, which helps tools and readers recognize this file as an SDK re-export layer.


### `core/src/ufo/sdk/manifest.py`

`other` · `import time / extension development`

This file exists to keep the project’s public extension interface clean and stable. A manifest is the description an extension gives the system: what it provides, what permissions or credentials it needs, what hooks it wants to run, and related limits or helper types. Many of those building blocks live in deeper internal modules, but extension authors should not have to know that internal layout. This file gathers the approved names into one place and re-exports them unchanged.

Think of it like a hotel front desk. Guests do not need to know which storage room holds towels or keys; they ask at the desk. In the same way, extensions import `Manifest`, hook types, credential choices, conversation slot payloads, image preview helpers, setup types, and workspace change limits from here.

There is no runtime logic in this file beyond Python importing names. Its importance is mainly architectural: it protects extension code from internal refactors. If the project later moves `ufo.ext.manifest` or `ufo.media.image_previews`, code that imports through `ufo.sdk.manifest` can keep working as long as this public re-export is updated. Without this file, extension authors would depend directly on private module paths, making upgrades more fragile.


### `core/src/ufo/sdk/models.py`

`other` · `import time / cross-cutting SDK use`

This file is like a front desk for anything an extension needs in order to talk to language models through UFO. It does not create new behavior of its own. Instead, it gathers selected classes, data shapes, constants, and helper functions from deeper inside the project and re-publishes them under `ufo.sdk.models`.

That matters because extensions need a safe, stable contract. Without this file, extension authors would have to import directly from internal modules such as `ufo.models.interface`, `ufo.models.openai`, or `ufo.models.anthropic`. Those internal paths may change as the project evolves. This file gives outside code a cleaner promise: “use these names here; they are the supported model API surface.”

The re-exported items cover the main building blocks for model communication: message and content block types, tool-call records, streaming events, model request and response shapes, OpenAI and Anthropic client classes, model specification types, pricing records, and image-trimming helpers. In plain terms, it collects the vocabulary and adapters needed to send prompts, receive model output, use tools, track usage, and describe model capabilities.

Because all work happens in the imported modules, this file is active mainly when Python imports it. Its importance is not in computation, but in drawing a clear boundary between public SDK usage and private implementation details.


### `core/src/ufo/sdk/tools.py`

`other` · `cross-cutting; used whenever extensions or tool handlers import the public SDK`

This file does not define new behavior. Instead, it gathers selected tool-related classes, constants, and helper functions from deeper parts of the system and re-exports them under one public module. That matters because extension authors need a safe, predictable place to import from. Without this file, outside code would have to know internal paths like `ufo.tools.tasks` or `ufo.tools.context`, which makes extensions more likely to break when the project is reorganized.

Think of it like a front desk in a large building. Visitors do not need to know which back office holds each form; they ask at the front desk and get the official version. Here, the “forms” include things like `ToolContext`, which describes the situation a tool is running in; `ToolResult`, which represents what a tool returns; `ToolDef`, which describes a tool; and task helpers such as `run_task`, `task_handles`, and `timeout_notice` for commands that may keep running after the caller stops waiting.

The comments also explain an important design rule: `ufo.sdk` keeps its package initializer empty, so public SDK names live in explicit modules like this one. This keeps the public interface clear and intentional.


### Shared Safety and Prompt Text
These utility facades publish common delivery-register prompt text and untrusted-text markers through approved SDK paths.

### `core/src/ufo/sdk/delivery_register.py`

`util` · `cross-cutting`

This is a very small bridge file. Its job is to make a particular piece of shared text available to outside code: the delivery-register block. That block is used when an extension makes a direct model call and needs the model to write its result into the same “register” used by the shell and by subagent prompts. In plain terms, it keeps everyone writing their delivery notes in the same notebook, rather than each caller inventing a different place to put them.

The file does not define new behavior. It imports `DELIVERY_REGISTER_BLOCK` from the internal module `ufo.turns.delivery_register` and re-exports it under the same name. This matters because `ufo.sdk` is meant to be the public doorway for extension authors. The project also avoids putting code in package `__init__.py` files, so public items are exposed through named modules like this one.

Without this file, extension code would either need to import from an internal path, which is more fragile, or duplicate the register text, which could drift out of sync. This file keeps the public interface simple and the shared prompt wording consistent.


### `core/src/ufo/sdk/untrusted.py`

`util` · `cross-cutting`

Some text that enters the system should not be treated as trusted instructions. For example, a probe's standard output, a model provider's response, or data returned by an outside extension may contain text that looks like a command but should be fenced off as ordinary content. This file exists to make that boundary easy and consistent for SDK users.

It imports `wall` from `ufo.turns.untrusted` and exposes it again from the SDK path. In plain terms, it is like putting the same warning label in two places: the core system already knows what the label means, and this file lets outside extension code use the exact same label instead of inventing its own.

There is no extra behavior here and no separate copy of the logic. The line `wall as wall` deliberately points SDK users to the core definition. That matters because if extensions used a different marker, core rendering paths, tool-result paths, and subagent hand-back paths might disagree about what counts as fenced untrusted content. This small file keeps that agreement intact.


### Sample Extension Coverage
The sample extension exercises the public SDK surface end to end by registering representative extension features for integration-style verification.

### `extensions/sample/ufo_ext_sample.py`

`test` · `startup registration, then exercised during tools, jobs, hooks, routes, surfaces, and provider calls`

Think of this file as a showroom model for the extension system. It does not connect to real outside services. Instead, it supplies small, predictable versions of many things an extension can contribute: tools, jobs, web routes, onboarding, credentials, connectors, hooks, custom objects, surfaces, sources, search, memory, models, browser leases, sandbox carriers, flags, and more. The important point is that each piece is installed through the same public `ufo.sdk` surface that a real third-party extension would use. When core calls one of these pieces, the handler usually writes a small record into the extension store, which is durable workspace-scoped storage. Tests then read those records back through public APIs to confirm that the real wiring worked. Without this file, the project would lack a broad conformance probe: changes to the SDK could silently break extension entry points, credential injection, hook delivery, connector account resolution, surface admission, custom storage, indexing, or other public seams. Most implementations here are intentionally simple and canned. That simplicity is the point: the file is not testing external providers, it is testing whether UFO can discover, call, and coordinate extension code correctly.

#### Function details

##### `_echo`  (lines 296–300)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Implements the sample echo tool. It records the message it was asked to echo, then returns that same message to the caller.

**Data flow**: It receives a tool context and an EchoInput containing a message. It checks that the extension context is present, saves the input as JSON-like data under the tool key, and returns a ToolResult containing the message text.

**Call relations**: The manifest registers this as the handler for the sample echo tool. When core dispatches that tool, this function uses the SDK result and text-content objects to hand the answer back.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 303–325)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Implements a sample note-writing tool that proves an extension can use its own database table. It writes a note for the current workspace and reads it back.

**Data flow**: It receives tool input text and reads the workspace id from the extension store. Inside an extension-scoped database transaction, it updates or inserts the note row, selects the stored value, and returns it as tool text.

**Call relations**: The manifest registers this as the sample note tool. It is also granted to the sample subagent, so core can test both ordinary tool dispatch and subagent tool access.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 328–354)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample scheduled job. It records that the job ran, then, when extra context is available, probes trajectories, proposes an agent prompt change, writes a workspace file, and runs a sandbox probe command.

**Data flow**: It receives an ExtensionContext. It writes a job marker, optionally reads known trajectories, creates an AgentChange proposal for the first trajectory, writes a file into that conversation workspace, runs a command to read it, and stores the results.

**Call relations**: The manifest registers this as the sample job handler. Core calls it off-turn through the jobs system, and it calls ExtensionContext helpers for trajectories and proposed changes.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 357–360)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Implements the sample HTTP route. It echoes the request body and records that the route was reached.

**Data flow**: It receives an extension context and HTTP request. It reads the raw body, stores the body plus the extension home URL, and returns the body as plain text.

**Call relations**: The manifest registers this as a POST route. Core identifies the workspace first, then calls this handler and receives a PlainTextResponse.

*Call graph*: calls 1 internal fn (home_url); 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 395–406)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists all stored sample widgets. It turns extension-store rows into object-list rows that the object API can show.

**Data flow**: It receives a tool context and list query. It reads all extension-store keys with the widget prefix, validates each stored widget, extracts selected fields, and returns a paged object response.

**Call relations**: Core calls this through the object-kind store registered in the manifest. It uses WidgetStore._ext to get the extension context and object_page to apply the requested paging.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 408–415)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one sample widget by name. It returns the widget's saved specification and timestamps if it exists.

**Data flow**: It receives a tool context and widget name. It reads the matching extension-store key, validates the saved value, and returns an ObjectDetail, or returns nothing if no row exists.

**Call relations**: Core calls this when an object read asks for a widget. It relies on WidgetStore._ext to ensure the tool call really has extension storage available.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 417–424)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Provides a status hook for widgets, but this sample has no extra live status to report.

**Data flow**: It receives the context, name, and optional expected generation. It ignores them and returns None, meaning there is no separate status payload.

**Call relations**: Core may call this through the object API after locating a widget. It intentionally does not hand off to anything else.


##### `WidgetStore.apply`  (lines 426–442)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It preserves the original creation time on updates and refreshes the update time.

**Data flow**: It receives a widget name and desired spec. It reads any existing stored widget, chooses a creation timestamp, writes the new StoredWidget under the widget key, and changes extension-store state.

**Call relations**: Core calls this when an object apply request targets the widget kind. It gets storage through WidgetStore._ext and builds a StoredWidget record before saving.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, now).


##### `WidgetStore.delete`  (lines 444–453)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only if the speaker is a workspace admin. This tests permission refusal on object deletion.

**Data flow**: It receives a widget name and context. It asks the ToolContext whether the speaker is an admin; if not, it raises AdminRequired. If allowed, it deletes the widget key from extension storage.

**Call relations**: Core calls this through the object delete verb. It uses ToolContext.speaker_is_admin for the permission check and WidgetStore._ext for the actual storage delete.

*Call graph*: calls 2 internal fn (speaker_is_admin, _ext); 1 external calls (__init__).


##### `WidgetStore._ext`  (lines 455–458)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Extracts the ExtensionContext from a ToolContext. It fails loudly if core dispatched the object store without extension context.

**Data flow**: It receives a ToolContext. If ctx.ext exists, it returns it; otherwise it raises a runtime error.

**Call relations**: WidgetStore.list, get, apply, and delete call this helper before touching extension storage.

*Call graph*: called by 4 (apply, delete, get, list).


##### `RelicStore.list`  (lines 466–470)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample read-only relic objects. It always returns one canned relic.

**Data flow**: It receives a context and list query, creates one ObjectRow for the meteor-shard relic, and returns it through the standard object paging helper.

**Call relations**: Core calls this through the relic object kind registered in the manifest. It uses object_page so the response has the same shape as real object lists.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 472–477)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Gets the one sample relic if the requested name matches. Relics demonstrate system-produced objects that can be read but not edited.

**Data flow**: It receives a relic name. If the name is not the canned relic name, it returns None; otherwise it returns an ObjectDetail containing a RelicSpec inscription.

**Call relations**: Core calls this when reading a relic object. It builds the same ObjectDetail type that real object stores return.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 479–486)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small live status payload for a relic. It marks the relic as something 'excavated' rather than authored.

**Data flow**: It receives context, name, and optional generation information. It returns a dictionary with an origin field and does not change storage.

**Call relations**: Core may call this through the object status path for the relic kind. It does not call other helpers.


##### `RelicStore.apply`  (lines 488–497)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update relics. This proves the object API can report unsupported write verbs.

**Data flow**: It receives the attempted relic spec and immediately raises VerbNotSupported with the sample refusal message. No data is saved.

**Call relations**: Core calls this only when someone tries to apply a relic object. The exception tells core to return a clear unsupported-operation result.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 499–506)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete relics. This keeps the read-only object kind truly read-only.

**Data flow**: It receives the relic name and immediately raises VerbNotSupported. Nothing is removed.

**Call relations**: Core calls this through the object delete path for relics. The raised SDK exception is the handoff back to core.

*Call graph*: 1 external calls (__init__).


##### `SampleSource.fetch`  (lines 525–534)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Produces one deterministic page from the sample source configuration. It proves a source backend can provide typed content to UFO.

**Data flow**: It receives a SampleSourceConfig, cursor, and auth object. It builds one Page whose body and title come from the configured topic, then returns a SyncResult with no next cursor.

**Call relations**: The onboarding setup registers this source, and core later calls fetch through the source provider seam. It constructs the SDK Page and SyncResult objects core expects.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 547–549)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the sample in-memory index. A chunk is a searchable piece of text plus metadata.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it in the instance dictionary keyed by chunk digest, replacing any earlier chunk with the same digest.

**Call relations**: Core calls this through the registered index backend when it wants to index content. Later lexical and vector searches read from the same dictionary.


##### `SampleIndex.delete`  (lines 551–553)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all indexed chunks belonging to a given scope. A scope identifies an owner kind and owner id.

**Data flow**: It receives an IndexScope, finds every stored chunk whose owner matches that scope, and removes those entries from the chunk dictionary.

**Call relations**: Core calls this through the index backend when content for a scope should be removed. It uses _in_scope to decide which chunks belong.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 555–556)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Checks whether the sample index currently has any chunks for a scope.

**Data flow**: It receives an IndexScope, scans stored chunks, and returns true if at least one chunk matches the scope.

**Call relations**: Core calls this through the index backend to know whether indexed content exists. It delegates the match test to _in_scope.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 558–564)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes stale chunks from a scope while keeping a named set of digests. This mimics cleanup after re-indexing.

**Data flow**: It receives a scope and a keep set. It deletes any stored chunk that is inside the scope but whose digest is not in the keep set.

**Call relations**: Core calls this through the index backend during index maintenance. It uses _in_scope for the scope test.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 566–575)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches indexed text by counting query terms. This is a tiny stand-in for keyword search.

**Data flow**: It receives a query, allowed subjects, owner kind, and limit. It filters chunks to that owner and subjects, counts matching lowercase terms in each text, converts positive scores to Hits, sorts by score, and returns the top results.

**Call relations**: Core calls this through the index search protocol. It uses SampleIndex._scoped to filter visible chunks and _hit to package results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 577–585)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches indexed chunks by vector similarity. A vector is a list of numbers used to compare meaning-like signals.

**Data flow**: It receives an embedding vector, allowed subjects, owner kind, and limit. It filters chunks, computes a dot product score against each chunk embedding, wraps positive scores as Hits, sorts them, and returns the top results.

**Call relations**: Core calls this through the index backend's vector search path. It uses SampleIndex._scoped, _dot, and _hit.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 587–592)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters stored chunks to the owner kind and subjects a query is allowed to see.

**Data flow**: It receives a set of subjects and an owner kind. It scans the chunk dictionary and returns only chunks with that owner kind and a subject in the allowed set.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this before scoring, so both search modes obey the same visibility filter.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 601–602)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for each input text. This proves core can call an extension-provided embedding backend.

**Data flow**: It receives a tuple of texts. It ignores the content and returns one canned numeric vector for each text.

**Call relations**: Core calls this through the embed backend registered in the manifest. The predictable vector lets tests avoid depending on an external embedding service.


##### `_in_scope`  (lines 605–606)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a chunk belongs to an index scope.

**Data flow**: It receives a Chunk and an IndexScope. It compares the chunk owner kind and owner id to the scope and returns true or false.

**Call relations**: SampleIndex.delete, has_chunks, and prune call this helper whenever they need the same scope-matching rule.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 609–612)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Computes a dot product between two numeric vectors. This gives the sample vector search a simple score.

**Data flow**: It receives two tuples of floats. If either is empty it returns 0; otherwise it multiplies matching positions together, sums them, and returns the score.

**Call relations**: SampleIndex.vector calls this for each candidate chunk to decide how strongly it matches the query embedding.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 615–624)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Turns a stored chunk and score into a search Hit object.

**Data flow**: It receives a Chunk and numeric score. It copies the chunk identity, owner, subject, ordinal, text, and the score into a Hit.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this after scoring so core receives standard index results.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 627–634)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It records onboarding completion and registers the sample source for the workspace.

**Data flow**: It receives an ExtensionContext, writes an onboarding marker to the store, creates a SampleSourceConfig with the canned topic, and asks core to register that source under the shared subject.

**Call relations**: The manifest registers this as an onboarding step. It hands source registration to ExtensionContext.register_source.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 637–640)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Refuses the sample echo tool before it runs. This proves a pre-tool hook can stop dispatch.

**Data flow**: It receives a HookContext and returns a Deny outcome with a fixed reason. It does not inspect or change stored data.

**Call relations**: The manifest attaches this to the pre_tool_use event for the echo tool. Core sees the Deny and should not call the tool handler.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 643–651)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool calls after they finish. It proves successful calls reach the post-tool hook.

**Data flow**: It receives a HookContext. If the payload is a PostToolUse event, it stores the tool name under the post-hook key and returns no special outcome.

**Call relations**: The manifest registers this for all post_tool_use events. Core calls it only after a successful tool dispatch.


##### `_record_post_failure`  (lines 654–660)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed tool calls after they error. It proves failures are delivered to a separate hook from successes.

**Data flow**: It receives a HookContext. If the payload is a PostToolUseFailure event, it stores the failing tool name and returns no special outcome.

**Call relations**: The manifest registers this for post_tool_use_failure. Core calls it on errored tool dispatches instead of the success hook.


##### `_record_stop`  (lines 663–669)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer when a turn is about to stop.

**Data flow**: It receives a HookContext. If the payload is a Stop event, it stores the answer text and returns no special outcome.

**Call relations**: The manifest registers this for the stop event. Core calls it near the end of a turn before committing the final answer.


##### `_record_pre_compact`  (lines 672–679)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records that conversation compaction is about to happen. Compaction means shortening older context to save tokens.

**Data flow**: It receives a HookContext. If the payload is PreCompact, it stores the reason and the estimated token count before compaction.

**Call relations**: The manifest registers this for pre_compact. Core calls it before compacting conversation context.


##### `_record_post_compact`  (lines 682–694)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the result of conversation compaction after it happens.

**Data flow**: It receives a HookContext. If the payload is PostCompact, it stores the summary plus token counts before and after compaction.

**Call relations**: The manifest registers this for post_compact. Core calls it after compacting and hands over the summary data.


##### `_record_page_change`  (lines 697–710)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records page-change batches delivered to the extension. It also notes whether a model was wired into the off-turn context.

**Data flow**: It receives a HookContext. If the payload is PageChangeBatch, it stores the changed page ids and a boolean saying whether ctx.ext.model is present.

**Call relations**: The manifest registers this for page_change. Core's page-change runner calls it when source pages change.


##### `_SampleConnectorOAuth.authorize_url`  (lines 723–724)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the sample connector's OAuth authorization URL. OAuth is the common web flow where a user grants account access.

**Data flow**: It receives a state value and redirect URI. It inserts both into a canned authorization URL and returns the string.

**Call relations**: Core calls this through the connector provider when starting the account connection flow.


##### `_SampleConnectorOAuth.exchange`  (lines 726–729)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the sample OAuth exchange without contacting a real provider. It returns a fixed connected-account id.

**Data flow**: It receives the OAuth code, redirect URI, workspace id, and state. It ignores the secret details and returns an OAuthAccount named sample-account-1.

**Call relations**: Core calls this after the OAuth redirect. It constructs the SDK OAuthAccount object core stores as the connected account.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 742–743)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample broker's available tool catalog. The catalog has one canned tool.

**Data flow**: It receives workspace, provider, and query information. It returns a tuple containing one BrokerTool with the sample slug and description.

**Call relations**: Core and _SampleBroker.search use this when discovering connector tools. _SampleBroker.search calls it before building a BrokerSearch result.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 745–752)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool. It rejects unknown tool slugs.

**Data flow**: It receives a tool slug. If the slug is not the sample slug it raises UnknownBrokerTool; otherwise it returns a BrokerTool with a small JSON-style input schema.

**Call relations**: Core calls this through the connector broker when it needs tool details. The exception path proves unknown dynamic tools are rejected.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 754–771)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Executes the sample broker tool by echoing the call details. It does not perform any real external action.

**Data flow**: It receives workspace, provider, slug, arguments, account id, and idempotency key. It rejects unknown slugs, then returns a dictionary containing those call details.

**Call relations**: Core calls this through the connector broker when a dynamic connector tool runs. The echoed result lets tests verify exactly what core sent.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 773–784)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Projects URLs in a broker response into produced files. This tests the file-output bridge for connector tools.

**Data flow**: It receives the broker response dictionary. If the arguments contain a list of file_output_urls, it turns each string URL into a BrokerFile using the URL's final path name.

**Call relations**: Core calls this after broker execution when it looks for files the tool produced. It uses BrokerFile objects to hand those files back into the workspace flow.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 786–812)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Stages a file upload for a connector tool. It returns a file URL the sandbox can write to, or a deduplication result if the same content was staged before.

**Data flow**: It receives file metadata including filename, MIME type, and md5 hash. It builds a stable key; if already minted, it returns a StagedUpload without a put URL, otherwise it records the key and returns a file:// put URL plus the argument core should send to the tool.

**Call relations**: Core calls this before executing connector tools that need uploaded files. It constructs StagedUpload responses in the broker protocol's expected shape.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 814–817)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a canned connector search result with tools and a plan. The plan tells an agent which broker tool to call first.

**Data flow**: It receives workspace, provider, and query. It calls _SampleBroker.tools to get the catalog and wraps it with the sample search plan.

**Call relations**: _SampleBroker.search builds on _SampleBroker.tools, then returns a BrokerSearch for core's connector search path.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 819–820)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a bearer credential for a connected broker account. A bearer credential is a token sent as proof of access.

**Data flow**: It receives workspace id, provider, and account name. It returns a Credential whose bearer token is the sample prefix plus the account id.

**Call relations**: Core calls this when it needs a connector account credential, for example for proxying or sync work.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 827–845)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Implements a server-side connector execution tool. It proves a tool can resolve the connected account bound to the current agent.

**Data flow**: It receives tool context and input. It asks the ToolContext for the account linked to the sample connector provider, stores that account, requested tool name, and idempotency key, then returns the account as text.

**Call relations**: The manifest registers this inside the connector provider. It hands account lookup to ToolContext.connector_account and returns a standard ToolResult.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 855–856)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps bytes as a one-piece asynchronous stream. This lets file-writing code consume data as a stream even when the sample has all bytes at once.

**Data flow**: It receives bytes and yields those same bytes once. It produces no final value beyond the yielded chunk.

**Call relations**: _surface_ingest calls this when optional inbound text should be written as a workspace file.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_model`  (lines 862–868)

```
async def _surface_model(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports which model was wired into a surface route. It helps prove route handlers can see the deploy's model context.

**Data flow**: It receives a SurfaceContext and request. It reads ctx.model, converts it to either null or a model id string, and returns that in JSON.

**Call relations**: The manifest registers this as a GET route on the durable sample surface. Core calls it through the surface route mechanism.

*Call graph*: 1 external calls (JSONResponse).


##### `_surface_ingest`  (lines 871–898)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Exercises durable surface ingestion. It links an outside identity, finds or creates a conversation, optionally stores an inbound file, and admits a user message as a turn.

**Data flow**: It receives a surface request with JSON body. It validates the body, looks up or links a member, resolves the conversation, may stream inbound text into a workspace file, admits the message with an idempotency key, and returns turn and conversation ids plus whether a run was opened.

**Call relations**: The manifest registers this as the POST route for the durable sample surface. It calls SurfaceContext helpers for identity, conversation, file write, and admission, and uses _one_chunk for file streaming.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 901–902)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed external reply reference for surface writeback. It stands in for posting a reply to an outside service.

**Data flow**: It receives the surface context and writeback payload. It ignores their details and returns the canned sample post reference.

**Call relations**: The durable surface registers this as its post callback. Core calls it when it wants the surface to send a completed answer outward.


##### `_surface_attach`  (lines 905–910)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies writeback artifacts from the blob store into delivered keys. This proves shared files can be streamed out and stored again.

**Data flow**: It receives a writeback with artifacts. For each artifact, it builds a delivered key and streams bytes from the artifact blob key into that new blob-store location.

**Call relations**: The durable surface registers this as its attach callback. Core calls it after posting when there are files to attach to the external reply.


##### `_surface_live_admit`  (lines 913–935)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Exercises live surface admission. Unlike the durable surface, this route admits a turn for live tailing rather than durable writeback polling.

**Data flow**: It receives JSON input, finds or adopts an identity, resolves a conversation, admits the message, reads the turn owner, gets a recent spend rollup, and returns those values as JSON.

**Call relations**: The manifest registers this as the live surface POST route. It calls SurfaceContext helpers for identity adoption, admission, ownership, and spend reporting.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 938–942)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live turn frames as newline-delimited JSON. This lets a client watch a running turn.

**Data flow**: It reads the turn id from request path parameters, converts it to a UUID, and returns a StreamingResponse whose body comes from _surface_frames.

**Call relations**: The manifest registers this as the live surface stream route. It delegates frame production to _surface_frames.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 945–948)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Reads live frames for one turn from the hub and encodes them as newline-separated JSON bytes.

**Data flow**: It receives a SurfaceContext and turn id. It opens ctx.tail for that turn, loops over incoming frames, serializes each frame to JSON, appends a newline, and yields bytes.

**Call relations**: _surface_live_stream calls this to supply the StreamingResponse body. It hands live-frame reading to SurfaceContext.tail.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 959–962)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a canned model completion. It proves the model registry can select and call an extension-provided model client.

**Data flow**: It receives a ModelRequest. It yields a stream-start event, one text delta with the fixed reply, and a usage event with one input and one output token.

**Call relations**: Core calls this through the model client declared in the manifest. The yielded SDK events mimic a real streaming model response.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 972–973)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the browser debugging endpoint for the sample lease. CDP means Chrome DevTools Protocol, a way to drive a browser remotely.

**Data flow**: It receives no extra input beyond the lease. It returns a CdpEndpoint with the fixed sample WebSocket URL.

**Call relations**: Core's browser engine calls this through the CdpLease protocol. It constructs the endpoint object core expects.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 975–976)

```
async def token(self) -> str
```

**Purpose**: Returns a durable token for reattaching to the sample browser lease. Here the token is simply the fixed URL.

**Data flow**: It receives no extra input and returns the sample CDP URL string.

**Call relations**: Core calls this when it needs a reattach handle for a browser lease.


##### `SampleCdpLease.place_file`  (lines 978–979)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Pretends to place a file for browser use. In the sample, the path is already suitable, so it returns it unchanged.

**Data flow**: It receives a path and a file-reading callback. It ignores the bytes and returns the original path.

**Call relations**: Core calls this through the browser lease when preparing files for browser upload or access.


##### `SampleCdpLease.download_dir`  (lines 981–982)

```
async def download_dir(self) -> str
```

**Purpose**: Returns the directory where sample browser downloads are expected to appear.

**Data flow**: It receives no extra input and returns the fixed sample download directory path.

**Call relations**: Core calls this through the CdpLease protocol when it needs to locate downloads.


##### `SampleCdpLease.fetch_download`  (lines 984–985)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a downloaded file from the sample download directory.

**Data flow**: It receives a download guid, combines it with the sample download directory, reads the file bytes in a worker thread, and returns those bytes.

**Call relations**: Core calls this when it needs to collect a browser download. It uses asyncio.to_thread so the blocking file read does not block the async event loop.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 987–988)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample browser lease. There is no real browser to close, so it does nothing.

**Data flow**: It receives no extra input and returns None without changing state.

**Call relations**: Core calls this through the async close path for CdpLease objects during cleanup.


##### `SampleCdpProvider.lease`  (lines 998–999)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new sample CDP lease. It always returns the same canned lease object.

**Data flow**: It receives an optional sandbox and ignores it. It constructs and returns a SampleCdpLease.

**Call relations**: Core calls this through the CDP provider registered in the manifest when a browser lease is needed.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 1001–1002)

```
async def reattach(self, token: str) -> CdpLease
```

**Purpose**: Reattaches to a sample CDP lease from a token. In this sample, every token leads to the same canned lease.

**Data flow**: It receives a token string, ignores its contents, and returns a new SampleCdpLease.

**Call relations**: Core calls this through the CDP provider when restoring a previous browser session.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 1013–1014)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed bearer credential for auth proxy testing.

**Data flow**: It receives workspace id, provider, and account. It returns a Credential containing the sample bearer string.

**Call relations**: Core calls this through the auth proxy backend registered in the manifest.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 1026–1034)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns a canned web search result and direct answer. It proves the search provider seam can be called.

**Data flow**: It receives a SearchQuery. It ignores the query content and returns SearchResults containing one SearchHit plus the fixed sample answer.

**Call relations**: Core search or research tools call this through the registered search provider. It builds the SDK SearchHit and SearchResults objects.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1036–1037)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Returns a canned fetched page for a URL. This tests the fetch side of a search provider.

**Data flow**: It receives a FetchRequest. It copies the request URL into a FetchedPage and fills in fixed sample text.

**Call relations**: Core calls this through the search provider when a previously found page should be fetched.

*Call graph*: 1 external calls (__init__).


##### `build_flag_provider`  (lines 1040–1051)

```
def build_flag_provider(_cache_ttl_seconds: float) -> InMemoryProvider
```

**Purpose**: Builds an in-memory feature flag provider. Feature flags are named switches that let code choose one behavior or another.

**Data flow**: It receives a cache time value, which this in-memory provider does not need. It creates two flags, one resolving true and one resolving false, and returns an InMemoryProvider.

**Call relations**: The manifest registers this as the flag provider builder. Core calls it when setting up flag evaluation for the extension.

*Call graph*: 2 external calls (InMemoryFlag, InMemoryProvider).


##### `SampleMemorySearch.search`  (lines 1060–1078)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match. It proves memory search providers receive query and subject information.

**Data flow**: It receives query strings, a SourceReader with subjects, and optional start and end times. It stores the queries, sorted subjects, and time bounds, then returns one MemoryMatch with fixed kind and text.

**Call relations**: Core calls this through the memory search provider registered in the manifest. It uses the extension context captured in the SampleMemorySearch instance.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1080–1081)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list recently. The sample exposes one kind.

**Data flow**: It receives no extra input and returns a tuple containing the sample memory kind.

**Call relations**: Core calls this when it needs to know what kinds are available for recent-memory listing.


##### `SampleMemorySearch.body_max_chars`  (lines 1083–1084)

```
def body_max_chars(self) -> int
```

**Purpose**: Reports the maximum body size this memory provider wants to expose. This gives core a simple display or truncation limit.

**Data flow**: It receives no extra input and returns the fixed maximum character count.

**Call relations**: Core calls this through the memory provider protocol when shaping memory output.


##### `SampleMemorySearch.list_recent`  (lines 1086–1112)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a recent-memory listing request and returns one canned memory row.

**Data flow**: It receives subjects, a limit, optional kinds, and an optional cursor. It stores those request details, including cursor fields if present, then returns a ListingPage containing one MemoryMatch.

**Call relations**: Core calls this through the memory search provider when listing recent memories. It constructs the SDK ListingPage response.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1124–1125)

```
def __init__(self) -> None
```

**Purpose**: Initializes the sample sandbox carrier's in-memory file map. A carrier is the backend that creates and talks to sandboxes.

**Data flow**: It receives no arguments beyond self and creates an empty dictionary mapping paths to bytes.

**Call relations**: The manifest registers SampleCarrier as a carrier factory. Core constructs it when selecting the sample sandbox backend.


##### `SampleCarrier.create`  (lines 1127–1133)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a fake sandbox handle. It does not start a real container, but returns the information core expects.

**Data flow**: It receives a SandboxSpec. It copies the conversation id and run token into a SandboxHandle, uses the fixed container id, and derives a runtime root path.

**Call relations**: Core calls this through the carrier protocol when starting a sandbox with the sample backend.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1135–1143)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Attaches to a fake existing sandbox if a resume id is present.

**Data flow**: It receives a SandboxSpec. If there is no resume id it returns None; otherwise it returns a SandboxHandle using the resume id as the container id.

**Call relations**: Core calls this through the carrier protocol when trying to resume a sandbox.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1145–1148)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Pretends to execute a command in the sample sandbox. It echoes the command arguments as stdout.

**Data flow**: It receives a sandbox handle, argument tuple, and timeout. It joins the arguments with spaces and returns an ExecResult with exit code 0 and empty stderr.

**Call relations**: Core calls this through the carrier protocol when running commands. The result proves the selected carrier handled the request.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1150–1151)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the sample carrier's in-memory file map.

**Data flow**: It receives a sandbox handle, path, and bytes. It stores the bytes under that path and returns nothing.

**Call relations**: Core calls this through the carrier protocol when copying files into a sandbox. SampleCarrier.read can later stream the same bytes back.


##### `SampleCarrier.read`  (lines 1153–1156)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes previously written to the sample carrier.

**Data flow**: It receives a sandbox handle and path. If the path was not written, it raises FileNotFoundError; otherwise it yields the stored bytes once.

**Call relations**: Core calls this through the carrier protocol when reading a file from a sandbox. It pairs with SampleCarrier.write.


##### `SampleCarrier.file_op`  (lines 1158–1161)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a generic filesystem operation against the sample carrier. It delegates the operation to the shared UFO filesystem helper.

**Data flow**: It receives a sandbox handle, operation name, and parameters. It passes itself, the handle, operation, and parameters to ufo_fs_file_op and returns that helper's result.

**Call relations**: Core calls this through the carrier protocol for file operations beyond simple read and write.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `SampleCarrier.dial`  (lines 1163–1164)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a fake network address for reaching a port in the sample container.

**Data flow**: It receives a sandbox handle and port. It returns a DialTarget whose host is the fixed container name plus the port and whose TLS flag is false.

**Call relations**: Core calls this through the carrier protocol when it needs to connect to a service inside the sandbox.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1167–1175)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace named by a bearer token in an HTTP request. If the request is missing a usable bearer token, it rejects identification.

**Data flow**: It reads the Authorization header, splits it into scheme and token, checks that the scheme is bearer and the token is not blank, then asks workspace_claim to decode the workspace id.

**Call relations**: The sample route uses this as its synchronous identify function, and resolve_surface_workspace calls it for surface routes too.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1178–1180)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Async wrapper for surface workspace identification. It uses the same bearer-token logic as ordinary routes.

**Data flow**: It receives a request and surface auth object. It passes the request to resolve_workspace and returns the resulting workspace id or None.

**Call relations**: Surface specs in the manifest use this as their identify function. It delegates the actual token parsing to resolve_workspace.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1183–1184)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: Placeholder summarizer for the sample conversation slot. It intentionally produces no summary.

**Data flow**: It receives a ConversationSlotContext and returns None without reading or writing data.

**Call relations**: The manifest registers this on the sample conversation slot. Core may call it when reconciling or summarizing that slot.


##### `_conversation_slot_read`  (lines 1187–1188)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Returns an empty set of workspace changes for the sample conversation slot.

**Data flow**: It receives a ConversationSlotContext and returns a WorkspaceChanges object with no changes and truncated set to false.

**Call relations**: The manifest registers this as the slot's read function. Core calls it when it wants the slot content.

*Call graph*: 1 external calls (__init__).


##### `manifest`  (lines 1191–1398)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the extension Manifest, which is the extension's menu of contributions to UFO. This is the file's main registration point.

**Data flow**: It creates a sample broker, then constructs a Manifest containing tools, objects, jobs, routes, onboarding, prompt sections, agents, subagents, credentials, connector provider, hooks, surfaces, sources, indexes, embeds, model backend, hub, terminal transport, skill, CDP provider, carrier, auth proxy, search provider, flag provider, memory search provider, and conversation slot.

**Call relations**: UFO loads this entry point at extension startup. Every handler and provider in the file becomes reachable because manifest places it into the appropriate SDK spec object.

*Call graph*: 40 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).
