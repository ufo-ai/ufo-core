# Sample extension hook coverage  `stage-19.5`

This stage is a test support stage. It is not part of normal startup, the main work loop, or shutdown for users. Instead, it acts like a full practice extension that the test suite can plug into the system. Its job is to prove that every public extension hook, meaning every official place where outside code is allowed to connect, really works from end to end.

The file `extensions/sample/ufo_ext_sample.py` is the whole sample extension. It provides small fake versions of many things a real extension might add: tools a user can call, web routes, background jobs, storage, search, browser access, model access, connectors, surfaces, and object backends. “Fake” here does not mean useless; it means simplified, predictable parts built for testing. Together they act like a training rig for the extension system. Tests can load this sample, call its pieces, and check that the system treats them the same way it would treat a real third-party extension.

## Files in this stage

### Sample extension hook coverage
### `extensions/sample/ufo_ext_sample.py`

`test` · `cross-cutting`

Think of this file as a model train set for the extension system. Nothing here talks to a real outside service, but every track, switch, and station is real. The extension declares a manifest, which is the menu of things it contributes to UFO: tools an agent can call, background jobs, HTTP routes, onboarding steps, object types, hooks, model backends, search backends, browser leases, sandbox carriers, feature flags, and more.

Most handlers do two things. First, they perform a tiny predictable action, such as echoing text, returning one canned search result, or creating a pretend widget. Second, they write evidence into the extension's durable store, so tests can later read that evidence through public APIs. This matters because it proves the platform is not merely calling mock functions; it is moving through the same extension boundary real third-party code would use.

The file also includes safety and behavior probes. Some hooks deny a tool before it runs. Some rewrite tool input or output. Widget edits use a generation value, like a ticket number, to reject stale updates. Read-only relics refuse changes. Surface routes create conversations and admit turns. Overall, the file is a conformance sample: if this extension works, the public extension seam is likely intact.

#### Function details

##### `_echo`  (lines 344–348)

```
async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult
```

**Purpose**: Runs the sample echo tool. It records the message it received and then returns that same message to the caller.

**Data flow**: It receives a tool context and an input object containing a message. It checks that the extension context is present, stores the input in the extension's durable store, and returns a tool result containing the message as text.

**Call relations**: The manifest registers this as the sample echo tool. A pre-tool hook can deny this tool before it runs; otherwise the tool path uses this function to prove a handler can write to extension storage and return normal tool content.

*Call graph*: 3 external calls (__init__, __init__, model_dump).


##### `_note`  (lines 351–373)

```
async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult
```

**Purpose**: Runs the sample note tool. It writes a note into the extension-owned database table and reads it back, proving extension database migrations and transactions work.

**Data flow**: It receives a tool context and note text. It opens a workspace-scoped database transaction, updates or inserts the note for this workspace, selects the stored note, and returns the stored text.

**Call relations**: The manifest registers this as a tool and also grants it to a subagent profile. It relies on the extension context transaction boundary, so tests can verify that extension-owned tables are usable through public SDK surfaces.

*Call graph*: 5 external calls (__init__, __init__, insert, select, update).


##### `_tick`  (lines 376–404)

```
async def _tick(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample background job. It records that the job ran, then optionally inspects active agent trajectories, proposes a prompt change, writes a workspace file, and probes that file through the sandbox.

**Data flow**: It receives an extension context. It writes job status into the store, reads available trajectories if a corpus is present, creates a proposed agent change from the first trajectory, writes a file into the workspace if file access exists, and runs a probe command if probes are available.

**Call relations**: The manifest registers this as the sample scheduled job. It hands off to extension context services such as trajectory lookup and change proposal to prove off-turn jobs get the same real workspace services as interactive code.

*Call graph*: calls 2 internal fn (propose_change, trajectories); 1 external calls (__init__).


##### `_hook`  (lines 407–410)

```
async def _hook(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: Serves the sample HTTP route. It echoes the request body and records that the route was reached.

**Data flow**: It receives an extension context and an HTTP request. It reads the raw request body, stores the body plus the extension home URL, and returns the body as plain text.

**Call relations**: The manifest registers this route with workspace identification. Route tests call it to prove extension HTTP handlers receive requests, can find their home URL, and can persist evidence.

*Call graph*: calls 1 internal fn (home_url); 2 external calls (PlainTextResponse, body).


##### `WidgetStore.list`  (lines 449–460)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists sample widget objects from extension storage. It turns stored widget rows into object-list rows the platform can show or page through.

**Data flow**: It receives a tool context and list query. It reads all store keys with the widget prefix, validates each stored value, builds rows with names and visible fields, and returns a paged object result.

**Call relations**: The widget object kind in the manifest uses this store. This method calls the store helper to get the extension context, then packages rows through the object paging helper.

*Call graph*: calls 1 internal fn (_ext); 2 external calls (__init__, object_page).


##### `WidgetStore.get`  (lines 462–472)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[WidgetSpec] | None
```

**Purpose**: Fetches one sample widget by name. It returns the widget's specification and bookkeeping data if the widget exists.

**Data flow**: It receives a tool context and widget name. It reads the corresponding prefixed store key, validates the saved widget data, and returns an object detail with spec, timestamps, and generation; missing data becomes null.

**Call relations**: The object system calls this when a widget instance is read. It shares the same extension-store access helper as the other widget methods.

*Call graph*: calls 1 internal fn (_ext); 1 external calls (__init__).


##### `WidgetStore.status`  (lines 474–485)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Checks whether a widget is still current for an operation. It uses the widget generation as a stale-edit guard.

**Data flow**: It receives a context, name, and expected generation. It reads the stored widget, and if present compares the stored generation with the expected one; it returns no extra status data.

**Call relations**: The object system can call this before actions or edits. It delegates the freshness check to WidgetStore._require_current, the same guard used by apply and delete.

*Call graph*: calls 2 internal fn (_ext, _require_current).


##### `WidgetStore.apply`  (lines 487–512)

```
async def apply(self, ctx: ToolContext, name: str, spec: WidgetSpec, old: WidgetSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Creates or updates a sample widget. It preserves the original creation time on update and gives every write a new generation value.

**Data flow**: It receives the desired widget name and spec, plus an optional expected generation. It reads any existing widget, rejects stale edits, chooses timestamps, writes the new stored widget record, and returns nothing.

**Call relations**: The object apply verb uses this method. It calls the extension-context helper and the generation checker, then writes back a StoredWidget so later reads and actions see the new version.

*Call graph*: calls 2 internal fn (_ext, _require_current); 3 external calls (__init__, now, uuid4).


##### `WidgetStore.delete`  (lines 514–527)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes a sample widget, but only for a workspace administrator. It also refuses stale deletes if the widget changed since it was read.

**Data flow**: It receives a context, widget name, and expected generation. It checks the current stored row if one exists, asks whether the speaker is an admin, raises an admin-required error if not, and deletes the store key if allowed.

**Call relations**: The object delete verb uses this method. It combines the same generation fence as updates with the platform's speaker permission check.

*Call graph*: calls 3 internal fn (speaker_is_admin, _ext, _require_current); 1 external calls (__init__).


##### `WidgetStore._require_current`  (lines 529–533)

```
def _require_current(self, name: str, stored: StoredWidget, expected_generation: UUID | None) -> None
```

**Purpose**: Protects widget writes from stale information. It raises an error when the stored generation does not match the generation the caller expected.

**Data flow**: It receives a widget name, stored widget record, and expected generation. It compares the stored generation to the expected value and either returns quietly or raises a clear changed-while-editing error.

**Call relations**: WidgetStore.status, WidgetStore.apply, and WidgetStore.delete all use this helper so they enforce the same freshness rule.

*Call graph*: called by 3 (apply, delete, status).


##### `WidgetStore._ext`  (lines 535–538)

```
def _ext(self, ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Retrieves the extension context from a tool context. It gives widget-store methods access to durable extension storage.

**Data flow**: It receives a tool context. If the context contains an extension context, it returns it; otherwise it raises an error explaining the widget store was called incorrectly.

**Call relations**: Every WidgetStore method that touches storage calls this first, making missing extension context failures consistent.

*Call graph*: called by 5 (apply, delete, get, list, status).


##### `RelicStore.list`  (lines 546–550)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists the sample read-only relic object. It always returns one canned relic row.

**Data flow**: It receives a tool context and list query. It creates a single object row for the fixed relic and wraps it in the standard object page result.

**Call relations**: The relic object kind in the manifest uses this store. It proves object listing also works for system-produced, read-only objects.

*Call graph*: 2 external calls (__init__, object_page).


##### `RelicStore.get`  (lines 552–557)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[RelicSpec] | None
```

**Purpose**: Fetches the sample relic if the requested name matches. It returns a detail object with the relic inscription.

**Data flow**: It receives a context and object name. If the name is not the fixed relic name it returns null; otherwise it returns a relic spec with no created or updated timestamps.

**Call relations**: The object system calls this for relic reads. It pairs with RelicStore.list to show that read-only object kinds can still be browsed.

*Call graph*: 2 external calls (__init__, __init__).


##### `RelicStore.status`  (lines 559–566)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Returns a small live status for a relic. It marks the relic as excavated.

**Data flow**: It receives a context, name, and expected generation but does not need them. It returns a dictionary saying the origin is excavated.

**Call relations**: The object status path can call this to prove read-only objects may still report status without allowing edits.


##### `RelicStore.apply`  (lines 568–577)

```
async def apply(self, ctx: ToolContext, name: str, spec: RelicSpec, old: RelicSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update a relic. Relics are deliberately read-only in this sample.

**Data flow**: It receives the attempted relic name and spec. Instead of writing anything, it raises a verb-not-supported error with the sample refusal message.

**Call relations**: The object apply verb reaches this method for relics. It proves the platform can surface a store-level refusal cleanly.

*Call graph*: 1 external calls (__init__).


##### `RelicStore.delete`  (lines 579–586)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to delete a relic. This keeps the relic object kind read-only from end to end.

**Data flow**: It receives the requested relic name and expected generation. It does not read or change storage; it raises a verb-not-supported error.

**Call relations**: The object delete verb reaches this method for relics. It mirrors RelicStore.apply so all mutation paths are blocked.

*Call graph*: 1 external calls (__init__).


##### `_target_record`  (lines 626–637)

```
def _target_record(target: ObjectActionTarget | None) -> dict[str, JsonValue] | None
```

**Purpose**: Turns an optional object-action target into plain JSON-friendly data. This lets action handlers record exactly what object or collection they were called on.

**Data flow**: It receives an action target or null. It returns null for no target, or a dictionary containing kind, name, agent name, generation, and expected generation as simple values.

**Call relations**: Most sample object-action tools and bless hooks call this before writing audit evidence to the store.

*Call graph*: called by 9 (_audit, _beseech, _bless, _bless_fold, _bless_replace, _calibrate, _divine, _engrave, _polish).


##### `_action_ext`  (lines 640–643)

```
def _action_ext(ctx: ToolContext) -> ExtensionContext
```

**Purpose**: Gets the extension context for object-action tools. It fails loudly if an action is dispatched without the extension context it needs.

**Data flow**: It receives a tool context. It returns the contained extension context or raises a runtime error if it is missing.

**Call relations**: The audit, polish, engrave, divine, calibrate, bless, and beseech action handlers call this before using extension storage.

*Call graph*: called by 7 (_audit, _beseech, _bless, _calibrate, _divine, _engrave, _polish).


##### `_audit`  (lines 646–656)

```
async def _audit(ctx: ToolContext, args: AuditInput) -> ToolResult
```

**Purpose**: Runs the workspace audit action. It records the requested subject and action target, then returns a short audit message.

**Data flow**: It receives a tool context and audit input. It extracts the extension context, formats the target record, stores the audit details, and returns text saying what was audited.

**Call relations**: The manifest binds this tool to the workspace collection. It uses the shared action helpers so tests can inspect what target information reached the action.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_polish`  (lines 659–662)

```
async def _polish(ctx: ToolContext, args: PolishInput) -> ToolResult
```

**Purpose**: Runs the widget polish action. It records how many coats were requested for the target widget.

**Data flow**: It receives a tool context and polish input. It stores the coat count plus target information and returns text confirming the polish.

**Call relations**: The manifest binds this to widget instances and marks it agent-targetable. It uses the same target recording path as other object actions.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_engrave`  (lines 665–701)

```
async def _engrave(ctx: ToolContext, args: EngraveInput) -> ToolResult
```

**Purpose**: Runs a side-effecting widget engraving action. It updates the widget generation, records the operation, and avoids repeating the same external write when the idempotency key matches.

**Data flow**: It receives engraving input and a targeted widget. It checks for a prior matching idempotency key, verifies the target generation is still current, reads and rewrites the widget with a new generation, records the engraving, optionally simulates interruption, and returns confirmation text.

**Call relations**: The manifest marks this action as side-effecting and gives it presentation metadata. It uses _action_ext and _target_record and directly updates StoredWidget records to test stale-write and retry behavior.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 5 external calls (__init__, __init__, __init__, now, uuid4).


##### `_divine`  (lines 704–707)

```
async def _divine(ctx: ToolContext, args: DivineInput) -> ToolResult
```

**Purpose**: Runs a collection-level divination action that returns untrusted text. It records the query and target before returning the canned divination.

**Data flow**: It receives a context and query input. It stores the query and target data, then returns a tool result containing the sample untrusted text.

**Call relations**: The manifest marks this tool as untrusted and parallel-safe. It proves that third-party text can pass through the tool surface as data.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_calibrate`  (lines 710–715)

```
async def _calibrate(ctx: ToolContext, args: CalibrateInput) -> ToolResult
```

**Purpose**: Runs the sample calibration action. It records a numeric offset for the target widget collection.

**Data flow**: It receives calibration input. It stores the offset and target information, then returns text confirming the offset.

**Call relations**: The manifest marks this as profile-only, so it is used to test tool visibility and profile restrictions.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_bless`  (lines 718–723)

```
async def _bless(ctx: ToolContext, args: BlessInput) -> ToolResult
```

**Purpose**: Runs the sample blessing action. It can either fail on purpose or record and return the blessing phrase.

**Data flow**: It receives a phrase and fail flag. If failure was requested, it raises an error; otherwise it stores the phrase and target, then returns confirmation text.

**Call relations**: The manifest registers hooks around this canonical object action. Pre and post hooks can modify its input, replace its output, or record failures.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 2 external calls (__init__, __init__).


##### `_beseech`  (lines 726–734)

```
async def _beseech(ctx: ToolContext, args: BeseechInput) -> ToolResult
```

**Purpose**: Runs an action that asks the user a question. It records the question and returns a final-act payload describing the user prompt.

**Data flow**: It receives a question. It stores the question and target, builds an AskUserInput object containing the question, serializes it into the tool text, and returns it.

**Call relations**: The manifest marks this with a final-act model. It proves an object action can hand back structured user-input instructions.

*Call graph*: calls 2 internal fn (_action_ext, _target_record); 4 external calls (__init__, __init__, __init__, __init__).


##### `_bless_fold`  (lines 737–751)

```
async def _bless_fold(ctx: HookContext) -> HookOutcome
```

**Purpose**: Prepares a bless action before it runs by changing its input phrase. It appends a visible suffix so tests can see the pre-tool hook took effect.

**Data flow**: It receives a hook context. If the payload is a pre-tool-use event for BlessInput, it records the call and target, then returns modified tool input with the phrase folded; otherwise it does nothing.

**Call relations**: The manifest attaches this pre-tool hook to the canonical bless action. It uses _target_record and returns ModifyInput so the main _bless handler receives altered arguments.

*Call graph*: calls 1 internal fn (_target_record); 2 external calls (__init__, __init__).


##### `_bless_replace`  (lines 754–762)

```
async def _bless_replace(ctx: HookContext) -> HookOutcome
```

**Purpose**: Replaces the output of a successful bless action. It records what the original output was before returning the replacement.

**Data flow**: It receives a hook context. If the payload is a successful post-tool-use event, it stores the call, output, and target, then returns a modified output string.

**Call relations**: The manifest attaches this post-tool hook to bless. It runs after _bless succeeds and demonstrates that hooks can rewrite tool results.

*Call graph*: calls 1 internal fn (_target_record); 1 external calls (__init__).


##### `_bless_failure`  (lines 765–769)

```
async def _bless_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed bless actions. It observes errors without changing the failure.

**Data flow**: It receives a hook context. If the payload is a post-tool-use-failure event, it stores the call and failure output, then returns nothing.

**Call relations**: The manifest attaches this failure hook to bless. It complements _bless_replace by proving successful and failed tool calls go to different hook events.


##### `SampleSource.fetch`  (lines 788–797)

```
async def fetch(self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult
```

**Purpose**: Fetches one deterministic sample source page. It turns the source configuration topic into a page for indexing and memory.

**Data flow**: It receives typed source config, a cursor, and auth information. It builds a page with a fixed source reference and body from the topic, then returns a sync result with no next cursor.

**Call relations**: The onboarding setup registers this source backend. Core calls fetch when polling the source, proving typed source configs pass through the extension seam.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleIndex.upsert`  (lines 810–812)

```
async def upsert(self, chunks: tuple[Chunk, ...]) -> None
```

**Purpose**: Adds or replaces chunks in the sample in-memory index. A chunk is a piece of text plus metadata used for search.

**Data flow**: It receives a tuple of chunks. For each chunk, it stores it by chunk digest in the index dictionary; it returns nothing.

**Call relations**: The manifest registers this index backend. Search and indexing tests use it to prove core can select and write to an extension-provided index.


##### `SampleIndex.delete`  (lines 814–816)

```
async def delete(self, scope: IndexScope) -> None
```

**Purpose**: Deletes all chunks that belong to a given index scope. A scope identifies the owner kind and owner id to remove.

**Data flow**: It receives an index scope. It scans stored chunks, finds those in scope, deletes their digests from the dictionary, and returns nothing.

**Call relations**: It uses _in_scope for the ownership test, the same helper used by has_chunks and prune.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.has_chunks`  (lines 818–819)

```
async def has_chunks(self, scope: IndexScope) -> bool
```

**Purpose**: Reports whether any chunks exist for a given scope. This lets core ask whether an owner has indexed content.

**Data flow**: It receives an index scope. It scans the stored chunks and returns true as soon as one chunk matches the scope, otherwise false.

**Call relations**: It relies on _in_scope so its definition of ownership matches delete and prune.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.prune`  (lines 821–827)

```
async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None
```

**Purpose**: Removes outdated chunks from a scope while keeping named digests. This mimics cleaning an index after source content changes.

**Data flow**: It receives a scope and a keep-set of chunk digests. It deletes any stored chunk that is in the scope but not in the keep-set.

**Call relations**: It uses _in_scope for filtering. Core can call this after re-indexing to prove extension indexes can remove stale entries.

*Call graph*: calls 1 internal fn (_in_scope).


##### `SampleIndex.lexical`  (lines 829–838)

```
async def lexical(self, query: str, subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by ordinary word matching. It scores chunks by how often query terms appear in their text.

**Data flow**: It receives a query, allowed subjects, owner kind, and limit. It filters chunks by subject and owner kind, counts matching terms, turns positive scores into hits, sorts by score, and returns the top hits.

**Call relations**: It uses SampleIndex._scoped to narrow the candidate chunks and _hit to convert chunks into search results.

*Call graph*: calls 2 internal fn (_scoped, _hit).


##### `SampleIndex.vector`  (lines 840–848)

```
async def vector(self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int) -> tuple[Hit, ...]
```

**Purpose**: Searches chunks by vector similarity. A vector is a list of numbers representing meaning; this sample compares vectors with a dot product.

**Data flow**: It receives an embedding vector, subjects, owner kind, and limit. It filters candidate chunks, calculates a dot-product score against each chunk embedding, keeps positive scores, sorts them, and returns hits.

**Call relations**: It uses SampleIndex._scoped for filtering, _dot for scoring, and _hit for result conversion.

*Call graph*: calls 3 internal fn (_scoped, _dot, _hit).


##### `SampleIndex._scoped`  (lines 850–855)

```
def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]
```

**Purpose**: Filters stored chunks to those visible for a requested subject set and owner kind. It keeps search results inside the intended boundary.

**Data flow**: It receives allowed subjects and an owner kind. It scans all stored chunks and returns only chunks with matching owner kind and subject.

**Call relations**: The lexical and vector search methods both call this before scoring so they search the same slice of stored data.

*Call graph*: called by 2 (lexical, vector).


##### `SampleEmbed.embed`  (lines 864–865)

```
async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]
```

**Purpose**: Returns a fixed embedding vector for every input text. It is a simple stand-in for a real embedding service.

**Data flow**: It receives a tuple of texts. It returns a tuple of identical sample vectors, one for each text.

**Call relations**: The manifest registers this embed backend. Core can select it and feed its vectors into SampleIndex.vector.


##### `_in_scope`  (lines 868–869)

```
def _in_scope(chunk: Chunk, scope: IndexScope) -> bool
```

**Purpose**: Checks whether a chunk belongs to an index scope. It compares the chunk's owner kind and owner id to the scope.

**Data flow**: It receives a chunk and scope. It returns true when both owner fields match, otherwise false.

**Call relations**: SampleIndex.delete, SampleIndex.has_chunks, and SampleIndex.prune use this shared ownership test.

*Call graph*: called by 3 (delete, has_chunks, prune).


##### `_dot`  (lines 872–875)

```
def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float
```

**Purpose**: Calculates a simple dot product between two numeric vectors. This is the sample's vector similarity score.

**Data flow**: It receives two tuples of numbers. If either is empty it returns 0; otherwise it multiplies matching positions together, sums them, and returns the score.

**Call relations**: SampleIndex.vector calls this for each candidate chunk to decide ranking.

*Call graph*: called by 1 (vector).


##### `_hit`  (lines 878–887)

```
def _hit(chunk: Chunk, score: float) -> Hit
```

**Purpose**: Turns an indexed chunk and score into a search hit. A hit is the result object returned from index searches.

**Data flow**: It receives a chunk and score. It copies identifying fields, text, subject, ordinal, and score into a Hit object.

**Call relations**: SampleIndex.lexical and SampleIndex.vector call this after they have scored matching chunks.

*Call graph*: called by 2 (lexical, vector); 1 external calls (__init__).


##### `_setup`  (lines 890–897)

```
async def _setup(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the sample onboarding step. It records onboarding completion and registers the sample source for the workspace.

**Data flow**: It receives an extension context. It writes an onboarding marker to the store, builds source config with the sample topic, and asks the platform to register the source for the shared subject.

**Call relations**: The manifest registers this as an onboarding step. It hands off to ExtensionContext.register_source so later source polling can call SampleSource.fetch.

*Call graph*: calls 1 internal fn (register_source); 1 external calls (__init__).


##### `_deny_echo`  (lines 900–903)

```
async def _deny_echo(ctx: HookContext) -> HookOutcome
```

**Purpose**: Blocks the sample echo tool before it runs. It proves a pre-tool hook can stop dispatch completely.

**Data flow**: It receives a hook context but does not need to inspect it. It returns a Deny outcome with the sample reason.

**Call relations**: The manifest attaches this pre-tool hook to the echo tool. When it fires, _echo should not run or write its normal store record.

*Call graph*: 1 external calls (__init__).


##### `_record_post`  (lines 906–914)

```
async def _record_post(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records successful tool calls after they finish. It observes the post-tool-use event and writes which tool succeeded.

**Data flow**: It receives a hook context. If the payload describes a successful tool call, it stores the tool name; it returns no change.

**Call relations**: The manifest registers this as a general post-tool hook. It complements the failure recorder so tests can see which event path was used.


##### `_record_post_failure`  (lines 917–923)

```
async def _record_post_failure(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records failed tool calls after an error. It writes the failed tool name into extension storage.

**Data flow**: It receives a hook context. If the payload is a tool failure event, it stores the tool name and returns no change.

**Call relations**: The manifest registers this as a general post-tool-failure hook. It should run for errored calls instead of _record_post.


##### `_record_stop`  (lines 926–932)

```
async def _record_stop(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records the final answer at the end of a turn. This proves stop hooks receive the answer about to be committed.

**Data flow**: It receives a hook context. If the payload is a stop event, it stores the answer text and returns no change.

**Call relations**: The manifest registers this for stop events. Turn-finalization tests read the stored answer to confirm the hook fired.


##### `_record_pre_compact`  (lines 935–942)

```
async def _record_pre_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information before conversation compaction. Compaction means shrinking old context into a shorter summary.

**Data flow**: It receives a hook context. If the payload is a pre-compaction event, it stores the reason and token estimate from before compaction.

**Call relations**: The manifest registers this for pre-compact events. It pairs with _record_post_compact to prove both sides of compaction are visible.


##### `_record_post_compact`  (lines 945–957)

```
async def _record_post_compact(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records information after conversation compaction. It captures the produced summary and token counts before and after.

**Data flow**: It receives a hook context. If the payload is a post-compaction event, it stores the summary, before-token count, and after-token count.

**Call relations**: The manifest registers this for post-compact events. Tests compare it with _record_pre_compact evidence.


##### `_record_page_change`  (lines 960–973)

```
async def _record_page_change(ctx: HookContext) -> HookOutcome
```

**Purpose**: Records delivered page-change events. It also notes whether a model was wired into the off-turn extension context.

**Data flow**: It receives a hook context. If the payload contains page changes, it stores their page ids and a boolean saying whether ctx.ext.model is present.

**Call relations**: The manifest registers this for page-change events. It proves data-plane updates reach extension hooks with useful off-turn services.


##### `_SampleConnectorOAuth.authorize_url`  (lines 986–987)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: Builds the sample connector authorization URL. It includes the state and redirect URI needed for an OAuth-style handoff.

**Data flow**: It receives a state string and redirect URI. It returns the canned authorize URL with those values as query parameters.

**Call relations**: The connector provider uses this OAuth descriptor when core starts a connection flow.


##### `_SampleConnectorOAuth.exchange`  (lines 989–992)

```
async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: Completes the fake connector authorization flow. It returns a fixed connected account id without exposing a secret token.

**Data flow**: It receives an authorization code, redirect URI, workspace id, and state. It ignores the live values and returns the sample OAuthAccount.

**Call relations**: Core calls this after the authorization redirect. The broker later uses the returned account id when executing connector tools.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.tools`  (lines 1005–1006)

```
async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]
```

**Purpose**: Returns the sample broker's tool catalog. The catalog contains one canned broker tool.

**Data flow**: It receives workspace, provider, and query information. It returns a tuple with one BrokerTool description.

**Call relations**: The broker search method calls this, and core can also call it directly when discovering dynamic connector tools.

*Call graph*: called by 1 (search); 1 external calls (__init__).


##### `_SampleBroker.schema`  (lines 1008–1015)

```
async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool
```

**Purpose**: Returns the input schema for the sample broker tool. It rejects unknown tool slugs.

**Data flow**: It receives a workspace id, provider, and slug. If the slug is not the sample slug it raises UnknownBrokerTool; otherwise it returns the tool description with a small JSON schema.

**Call relations**: Core calls this when it needs the dynamic connector tool's input shape before execution.

*Call graph*: 2 external calls (__init__, __init__).


##### `_SampleBroker.execute`  (lines 1017–1034)

```
async def execute(self, workspace_id: UUID, provider: str, slug: str, arguments: Mapping[str, object], account_id: str, idempotency_key: str | None) -> dict[str, object]
```

**Purpose**: Executes the fake broker tool by echoing the request details. It proves arguments, account id, and idempotency key cross the connector boundary.

**Data flow**: It receives workspace id, provider, slug, arguments, account id, and idempotency key. It rejects unknown slugs, then returns a dictionary containing the same call information.

**Call relations**: Dynamic connector tool execution reaches this broker method. File output processing can then inspect its echoed response.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.file_outputs`  (lines 1036–1047)

```
def file_outputs(self, response: dict[str, object]) -> tuple[BrokerFile, ...]
```

**Purpose**: Converts echoed file-output URLs into produced file records. It lets tests drive the connector file bridge without a real broker service.

**Data flow**: It receives a broker response dictionary. It looks inside the arguments for a list of file_output_urls, turns each string URL into a BrokerFile named from the URL path, and returns them.

**Call relations**: Core can call this after broker execution to discover files produced by the connector.

*Call graph*: 2 external calls (__init__, PurePosixPath).


##### `_SampleBroker.stage_upload`  (lines 1049–1075)

```
async def stage_upload(self, workspace_id: UUID, provider: str, slug: str, filename: str, mimetype: str, md5: str) -> StagedUpload
```

**Purpose**: Creates a fake upload slot for connector file inputs. It also simulates deduplication by returning no upload URL for a file key already staged.

**Data flow**: It receives workspace, provider, slug, filename, mimetype, and md5 hash. It builds a content-addressed key, records first-time keys, and returns a StagedUpload with either a file URL to write to or a deduped no-upload result.

**Call relations**: Core calls this before executing connector tools that need uploaded files. The returned argument is later passed into _SampleBroker.execute.

*Call graph*: 1 external calls (__init__).


##### `_SampleBroker.search`  (lines 1077–1080)

```
async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch
```

**Purpose**: Returns a broker search result with tools and a plan. It is a fake planning response for connector discovery.

**Data flow**: It receives workspace, provider, and query. It calls the broker's tool catalog and wraps the tools with the sample search plan.

**Call relations**: It reuses _SampleBroker.tools so the search result and direct catalog agree.

*Call graph*: calls 1 internal fn (tools); 1 external calls (__init__).


##### `_SampleBroker.credential`  (lines 1082–1083)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a bearer credential for a connected broker account. The token value names the account in a predictable way.

**Data flow**: It receives workspace id, provider, and account id. It returns a Credential containing the sample bearer prefix plus the account.

**Call relations**: Core calls this when it needs connector credentials for egress or broker-backed operations.

*Call graph*: 1 external calls (__init__).


##### `_connector_execute`  (lines 1090–1108)

```
async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult
```

**Purpose**: Runs the sample connector's server-side execution tool. It resolves the connected account granted to the current agent and records the call.

**Data flow**: It receives a tool context and connector-execute input. It asks the context for the account bound to the sample connector, stores the account, requested tool name, and idempotency key, then returns the account as text.

**Call relations**: The connector provider registers this tool in the manifest. It proves server-side connector tools can find their account binding before doing side effects.

*Call graph*: calls 1 internal fn (connector_account); 2 external calls (__init__, __init__).


##### `_one_chunk`  (lines 1118–1119)

```
async def _one_chunk(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps bytes as a one-piece async stream. It is used when a surface writes an inbound file into the workspace.

**Data flow**: It receives bytes. When iterated, it yields those bytes once and then ends.

**Call relations**: _surface_ingest calls this to feed inbound text into SurfaceContext.write_workspace_file, which expects a stream rather than one plain byte string.

*Call graph*: called by 1 (_surface_ingest).


##### `_surface_model`  (lines 1125–1131)

```
async def _surface_model(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports which model was wired into a surface route. It returns null if the deployment did not provide one.

**Data flow**: It receives a surface context and request. It reads ctx.model, extracts the model id when present, and returns it as JSON.

**Call relations**: The durable sample surface registers this GET route. It proves surface handlers can see the configured model service.

*Call graph*: 1 external calls (JSONResponse).


##### `_surface_ingest`  (lines 1134–1161)

```
async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message through the durable sample surface. It links identity, finds or creates a conversation, optionally stores an inbound file, and admits a turn.

**Data flow**: It reads JSON request input with external id, optional email, message, and optional inbound text. It resolves or links a member, gets a conversation for that identity, writes a workspace file if text was provided, admits the turn with an idempotency key, and returns turn and conversation ids plus whether a run was opened.

**Call relations**: The sample surface registers this POST route. It calls _one_chunk for file streaming and uses surface context methods for identity, conversation, file, and turn admission work.

*Call graph*: calls 6 internal fn (admit, conversation_for, link_member, linked_member, write_workspace_file, _one_chunk); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_post`  (lines 1164–1165)

```
async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Returns a fixed external reference for surface writeback. It stands in for posting a reply to an outside service.

**Data flow**: It receives a surface context and writeback description. It ignores the details and returns the sample posted reference string.

**Call relations**: The durable sample surface uses this as its post callback after a turn has output to deliver.


##### `_surface_attach`  (lines 1168–1173)

```
async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Copies shared artifact files into delivered blob keys. This proves surface attachment delivery can stream bytes out and back into blob storage.

**Data flow**: It receives a writeback with artifacts and a reply reference. For each artifact, it builds a delivered key and streams the artifact blob into that new key.

**Call relations**: The durable sample surface uses this attach callback after _surface_post. Tests can read the delivered blob keys to confirm attachment streaming worked.


##### `_surface_live_admit`  (lines 1176–1198)

```
async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts an inbound message through the live sample surface. It admits a turn without durable writeback and returns ownership and spend information.

**Data flow**: It reads the request JSON, resolves or adopts a member identity, gets the conversation, admits a turn, reads the turn owner, reads recent workspace spend, and returns all of that as JSON.

**Call relations**: The live sample surface registers this POST route. It contrasts with _surface_ingest by using live delivery rather than the durable post callback path.

*Call graph*: calls 6 internal fn (admit, adopt_identity, conversation_for, linked_member, spend_rollup, turn_owner); 3 external calls (conversation_audience, JSONResponse, body).


##### `_surface_live_stream`  (lines 1201–1205)

```
async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams live frames for a turn as newline-delimited JSON. This lets a caller watch a live turn progress.

**Data flow**: It reads the turn id from the route path, converts it to a UUID, and returns a streaming HTTP response backed by _surface_frames.

**Call relations**: The live sample surface registers this GET route. It delegates the actual frame iteration to _surface_frames.

*Call graph*: calls 1 internal fn (_surface_frames); 2 external calls (StreamingResponse, UUID).


##### `_surface_frames`  (lines 1208–1211)

```
async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]
```

**Purpose**: Reads live turn frames from the hub and yields them as bytes. Each frame becomes one JSON line.

**Data flow**: It receives a surface context and turn id. It opens a tail stream, loops over frames as they arrive, serializes each frame to JSON, appends a newline, and yields bytes.

**Call relations**: _surface_live_stream calls this to provide the body of its streaming response.

*Call graph*: calls 1 internal fn (tail); called by 1 (_surface_live_stream).


##### `SampleModelClient.complete`  (lines 1222–1225)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Streams a canned model completion. It behaves like a tiny language model backend that always replies with the same text and usage count.

**Data flow**: It receives a model request. It yields a stream-start event, one text-delta event containing the sample reply, and a usage event with one input and one output token.

**Call relations**: The manifest registers a model spec that constructs this client. Core can select it, consume its event stream, and price it with the sample rate.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `SampleCdpLease.endpoint`  (lines 1235–1236)

```
async def endpoint(self) -> CdpEndpoint
```

**Purpose**: Returns the fixed browser debugging endpoint for the sample lease. CDP means Chrome DevTools Protocol, a browser-control connection.

**Data flow**: It receives no input beyond the lease. It returns a CdpEndpoint containing the sample WebSocket URL.

**Call relations**: Browser code calls this on a lease returned by SampleCdpProvider.lease or reattach.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpLease.token`  (lines 1238–1239)

```
async def token(self) -> str
```

**Purpose**: Returns the token used to reattach to the sample browser lease. In this fake lease, the token is just the fixed endpoint URL.

**Data flow**: It receives no meaningful input. It returns the sample CDP URL string.

**Call relations**: Core can save this token and later pass it to SampleCdpProvider.reattach.


##### `SampleCdpLease.place_file`  (lines 1241–1242)

```
async def place_file(self, path: str, read: FileBytes) -> str
```

**Purpose**: Pretends to place a file for browser upload. It returns the same path unchanged.

**Data flow**: It receives a path and a byte-reading callback. It does not copy data and simply returns the path.

**Call relations**: Browser automation can call this through the CdpLease protocol when preparing file uploads.


##### `SampleCdpLease.download_dir`  (lines 1244–1245)

```
async def download_dir(self) -> str
```

**Purpose**: Reports the directory where sample browser downloads live. It returns a fixed path.

**Data flow**: It receives no input. It returns the sample download directory string.

**Call relations**: Browser download code can call this on the lease to locate downloaded files.


##### `SampleCdpLease.fetch_download`  (lines 1247–1248)

```
async def fetch_download(self, guid: str) -> bytes
```

**Purpose**: Reads a downloaded file from the sample download directory. It loads the named file's bytes without blocking the async event loop.

**Data flow**: It receives a download guid, builds a path under the sample download directory, reads the file bytes in a worker thread, and returns those bytes.

**Call relations**: Browser download code calls this through the lease when it needs the contents of a completed download.

*Call graph*: 2 external calls (to_thread, Path).


##### `SampleCdpLease.aclose`  (lines 1250–1251)

```
async def aclose(self) -> None
```

**Purpose**: Closes the sample browser lease. There is nothing real to close, so it is a no-op.

**Data flow**: It receives no input beyond the lease and returns nothing. It changes no state.

**Call relations**: Core can call this during browser cleanup through the normal CdpLease protocol.


##### `SampleCdpProvider.lease`  (lines 1261–1262)

```
async def lease(self, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Creates a new sample browser lease. It ignores the sandbox and returns the canned lease object.

**Data flow**: It receives an optional sandbox. It constructs and returns a SampleCdpLease.

**Call relations**: The manifest registers this CDP provider. Browser setup calls lease when it needs a new controllable browser endpoint.

*Call graph*: 1 external calls (__init__).


##### `SampleCdpProvider.reattach`  (lines 1264–1265)

```
async def reattach(self, token: str, sandbox: Sandbox | None=None) -> CdpLease
```

**Purpose**: Reconnects to an existing sample browser lease. In this fake provider, every token maps to the same canned lease.

**Data flow**: It receives a token and optional sandbox. It constructs and returns a new SampleCdpLease with the fixed endpoint behavior.

**Call relations**: Core calls this when restoring a browser session from a saved lease token.

*Call graph*: 1 external calls (__init__).


##### `SampleAuthProxy.credential`  (lines 1276–1277)

```
async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential
```

**Purpose**: Returns a fixed bearer credential for the sample auth proxy. A bearer credential is a token sent with requests to prove authorization.

**Data flow**: It receives workspace id, provider, and account name. It returns a Credential containing the fixed sample bearer value.

**Call relations**: The manifest registers this auth proxy backend. Sync or egress code can call it through the public auth-proxy seam.

*Call graph*: 1 external calls (__init__).


##### `SampleSearchProvider.search`  (lines 1289–1297)

```
async def search(self, query: SearchQuery) -> SearchResults
```

**Purpose**: Returns one canned web-search result and a direct answer. It is a predictable search backend for tests.

**Data flow**: It receives a search query. It ignores the query content and returns SearchResults containing one hit plus the fixed answer text.

**Call relations**: The manifest registers this search provider. Research tools can call it as if it were a real external search service.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleSearchProvider.fetch`  (lines 1299–1300)

```
async def fetch(self, request: FetchRequest) -> FetchedPage
```

**Purpose**: Fetches a canned page for a URL. It proves the search provider's fetch path can return page text.

**Data flow**: It receives a fetch request containing a URL. It returns a FetchedPage with that URL and fixed sample text.

**Call relations**: Core or research tools call this after search when they need page contents. The provider advertises that fetch is supported.

*Call graph*: 1 external calls (__init__).


##### `build_flag_provider`  (lines 1303–1316)

```
def build_flag_provider(_cache_ttl_seconds: float) -> InMemoryProvider
```

**Purpose**: Builds an in-memory feature-flag provider. It defines one sample flag that resolves on and one that resolves off.

**Data flow**: It receives a cache time setting but does not need it. It creates flag variants for served true and served false, then returns an OpenFeature in-memory provider with the sample flags.

**Call relations**: The manifest registers this as a flag provider backend. Flagged code paths can evaluate both true and false cases without an outside flag service.

*Call graph*: 2 external calls (InMemoryFlag, InMemoryProvider).


##### `SampleMemorySearch.search`  (lines 1325–1343)

```
async def search(self, queries: tuple[str, ...], reader: SourceReader, start: datetime | None=None, end: datetime | None=None) -> tuple[MemoryMatch, ...]
```

**Purpose**: Records a scoped memory search and returns one canned memory match. A memory match is a piece of remembered workspace information.

**Data flow**: It receives query strings, a source reader with subjects, and optional start and end times. It stores the queries, subjects, and time bounds, then returns one MemoryMatch with fixed kind and text.

**Call relations**: The manifest registers this memory-search provider. Core calls it through the provider seam when doing memory recall.

*Call graph*: 2 external calls (__init__, isoformat).


##### `SampleMemorySearch.listable_kinds`  (lines 1345–1346)

```
def listable_kinds(self) -> tuple[str, ...]
```

**Purpose**: Reports which memory kinds this provider can list. It returns the single sample memory kind.

**Data flow**: It receives no outside data. It returns a tuple containing the sample memory kind string.

**Call relations**: Core can call this before listing recent memories to know which kinds are supported.


##### `SampleMemorySearch.list_recent`  (lines 1348–1374)

```
async def list_recent(self, subjects: frozenset[str], limit: int, kinds: frozenset[str] | None=None, cursor: ListingCursor | None=None) -> ListingPage[MemoryMatch]
```

**Purpose**: Records a recent-memory listing request and returns one canned memory row. It supports subject, kind, limit, and cursor inputs.

**Data flow**: It receives subjects, a limit, optional kinds, and an optional cursor. It stores those request details in plain JSON-friendly form and returns a ListingPage containing one MemoryMatch.

**Call relations**: Core calls this through the memory-search provider when listing recent memories rather than searching by query.

*Call graph*: 2 external calls (__init__, __init__).


##### `SampleCarrier.__init__`  (lines 1386–1387)

```
def __init__(self) -> None
```

**Purpose**: Initializes the fake sandbox carrier. It creates an in-memory dictionary for files written into the pretend sandbox.

**Data flow**: It receives no arguments besides the new object. It sets up an empty path-to-bytes map.

**Call relations**: The manifest registers SampleCarrier as a carrier factory. Core constructs it when selecting the sample sandbox carrier.


##### `SampleCarrier.create`  (lines 1389–1395)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a pretend sandbox handle. It does not start a real container but returns the metadata a sandbox handle normally carries.

**Data flow**: It receives a sandbox spec. It builds a SandboxHandle with the conversation id, fixed container id, run token, and runtime root path, then returns it.

**Call relations**: Sandbox orchestration calls this through the Carrier protocol when starting a new sample sandbox.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.attach`  (lines 1397–1405)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Attaches to a pretend existing sandbox when a resume id is present. Without a resume id, it reports that no attach is possible.

**Data flow**: It receives a sandbox spec. If resume_id is missing it returns null; otherwise it builds a SandboxHandle using the resume id as the container id.

**Call relations**: Core calls this when trying to resume a sandbox. It complements create by testing the attach path.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.exec`  (lines 1407–1414)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, model_command: str | None=None) -> ExecResult
```

**Purpose**: Pretends to run a command in the sandbox. It returns the command arguments joined together as stdout.

**Data flow**: It receives a sandbox handle, argv tuple, timeout, and optional model command. It produces an ExecResult with joined argv, empty stderr, and exit code 0.

**Call relations**: Sandbox command execution reaches this carrier method. Tests can tell the sample carrier was used by inspecting the echoed stdout.

*Call graph*: 1 external calls (__init__).


##### `SampleCarrier.write`  (lines 1416–1417)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into the fake sandbox file store. It keeps the content in memory under the requested path.

**Data flow**: It receives a handle, path, and bytes. It stores the bytes in the carrier's written dictionary and returns nothing.

**Call relations**: Core calls this when copying files into the sample sandbox. SampleCarrier.read can later return the same bytes.


##### `SampleCarrier.read`  (lines 1419–1422)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads bytes from the fake sandbox file store. It raises a file-not-found error if the path was never written.

**Data flow**: It receives a handle and path. It looks up the path in memory and yields the stored bytes as an async stream.

**Call relations**: Core calls this when copying files out of the sample sandbox. It pairs with SampleCarrier.write.


##### `SampleCarrier.file_op`  (lines 1424–1427)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level filesystem operation through the shared UFO filesystem helper. This gives the fake carrier standard file-operation behavior.

**Data flow**: It receives a handle, operation name, and parameters. It delegates to ufo_fs_file_op and returns that helper's dictionary result.

**Call relations**: Core can call this for generic sandbox file operations. The helper may call back into the carrier's read and write methods.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `SampleCarrier.dial`  (lines 1429–1430)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Returns a fake network dial target for a sandbox port. A dial target tells callers what host and port-like endpoint to connect to.

**Data flow**: It receives a handle and port. It returns a DialTarget using the fixed container name and requested port, with TLS disabled.

**Call relations**: Core calls this when it wants to reach a service exposed from the sample sandbox.

*Call graph*: 1 external calls (__init__).


##### `resolve_workspace`  (lines 1433–1441)

```
def resolve_workspace(request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace for an HTTP request from its bearer token. If the Authorization header is missing or malformed, it rejects the request by returning null.

**Data flow**: It reads the authorization header, splits out the scheme and token, checks for a Bearer token, and passes the token to workspace_claim to get a workspace id.

**Call relations**: The sample route uses this as its identify function. resolve_surface_workspace also delegates to it for surface routes.

*Call graph*: called by 1 (resolve_surface_workspace); 1 external calls (workspace_claim).


##### `resolve_surface_workspace`  (lines 1444–1446)

```
async def resolve_surface_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies the workspace for a surface request. It uses the same bearer-token logic as ordinary sample routes.

**Data flow**: It receives a request and surface auth object. It ignores the auth object and returns the result of resolve_workspace.

**Call relations**: Both sample surface specs use this async identify function before running their routes.

*Call graph*: calls 1 internal fn (resolve_workspace).


##### `_conversation_slot_summary`  (lines 1449–1450)

```
async def _conversation_slot_summary(_ctx: ConversationSlotContext) -> None
```

**Purpose**: Placeholder summarizer for the sample conversation slot. It intentionally produces no summary.

**Data flow**: It receives a conversation slot context and returns nothing. It does not read or write data.

**Call relations**: The manifest registers it as the summarize callback for the sample conversation slot.


##### `_conversation_slot_read`  (lines 1453–1454)

```
async def _conversation_slot_read(_ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Returns empty workspace changes for the sample conversation slot. It proves the slot read callback can return the expected typed shape.

**Data flow**: It receives a conversation slot context. It returns a WorkspaceChanges object with no changes and not truncated.

**Call relations**: The manifest registers it as the read callback for the sample conversation slot.

*Call graph*: 1 external calls (__init__).


##### `_workspace_fact_held`  (lines 1457–1461)

```
async def _workspace_fact_held(ext: ExtensionContext) -> bool
```

**Purpose**: Checks whether the sample workspace fact should appear in the agent prompt. The fact is held only when a specific store key is true.

**Data flow**: It receives an extension context. It reads the workspace-fact key from extension storage and returns true only if the stored value is exactly true.

**Call relations**: The manifest registers this as the hold test for the sample workspace fact. Prompt assembly calls it to decide whether to include the fact line.


##### `manifest`  (lines 1464–1738)

```
def manifest() -> Manifest
```

**Purpose**: Builds and returns the full sample extension manifest. The manifest is the contract telling UFO everything this extension contributes.

**Data flow**: It creates a sample broker, then constructs a Manifest containing tools, objects, jobs, routes, onboarding, prompt sections, facts, agents, subagents, credentials, connectors, hooks, surfaces, providers, skills, flags, memory search, and conversation slots. The returned manifest is what core reads to wire the extension into the system.

**Call relations**: This is the file's central assembly point. It references nearly every handler and provider class in the file so core can call them later through the public SDK extension seam.

*Call graph*: 43 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).
