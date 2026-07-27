# Extension runtime context, package entry, HTTP, and observability SDK  `stage-20.1`

This stage is shared runtime support for extensions: small pieces of public “tooling” that extension authors use while their code is running. It sits behind the scenes during request handling or background jobs, giving outside code safe access without exposing the whole system.

The main piece is `ext/context.py`. It creates the limited workbench an extension receives inside a workspace. Through it, code can touch only approved things, such as allowed data, credentials, transcripts, model calls, sources, and proposals. This keeps extensions useful but contained.

The SDK files make that workbench easier and safer to use. `sdk/__init__.py` simply marks the SDK folder as importable Python code. `sdk/context.py` is the stable front door for importing context types, so extension authors do not depend on internal file paths. `sdk/http.py` provides request and response helpers, including safe session cookie setting, without exposing web-framework internals. `sdk/o11y.py` gives a simple logging doorway, so extensions can record messages and warnings in the project’s structured log format.

## Files in this stage

### Runtime Execution Context
Defines the limited workspace-safe toolbox that extension handlers and background jobs receive at runtime.

### `core/src/ufo/ext/context.py`

`domain_logic` · `cross-cutting: active whenever an extension handler, background job, or internal turn runs in a workspace`

Extensions need useful powers, but they should not get a master key to the whole system. This file solves that by building a capability-scoped context: a package of carefully limited access objects. Think of it like giving a contractor a badge that opens only the rooms needed for today’s job, not the whole building.

The main object is `ExtensionContext`. It contains a private key-value store for one extension, credential access limited to declared credential slots, optional model access, source/page access, scheduling, turn invocation, and read-only transcript access. Most operations find the current workspace through `ws_current()`, meaning the workspace is set by the surrounding job or turn rather than passed around manually. That helps prevent an extension from accidentally reaching into another workspace.

The file also provides wrappers for sensitive actions. `ScopedStore` stores extension data under that extension’s name. `CredentialAccess` refuses undeclared secrets. `TrajectoryCorpus` reads recent conversation transcripts but skips missing or corrupt ones instead of failing the whole job. `ModelAccess` routes language-model calls through billing and usage tracking. Source methods register, list, remove, or relabel synced content in a workspace.

Without this file, extensions would either be too weak to do real work or too powerful and risky. This is the boundary that makes extension code useful while keeping workspace data, credentials, and billing under control.

#### Function details

##### `ScopedStore.workspace_id`  (lines 74–75)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID that is currently bound to the running job or turn. This keeps the store tied to the active workspace instead of letting callers choose any workspace by hand.

**Data flow**: It reads the current workspace context → takes its `workspace_id` → returns that UUID to the caller.

**Call relations**: The other `ScopedStore` methods call on this property whenever they query or change stored extension data, so every store operation is automatically scoped to the workspace already chosen by the surrounding runtime.

*Call graph*: 1 external calls (ws_current).


##### `ScopedStore.get`  (lines 77–88)

```
async def get(self, key: str) -> JsonValue | None
```

**Purpose**: Reads one saved JSON value from this extension’s private store inside the current workspace. It is used when an extension wants to remember small durable settings or state.

**Data flow**: It receives a key → opens a workspace-scoped database transaction → looks for a row matching the current workspace, this extension name, and the key → returns the saved value, or `None` if no row exists.

**Call relations**: Extension code uses this through `ExtensionContext.store`. It relies on `workspace_tx` for the database access and on `ScopedStore.workspace_id` for the current workspace boundary.

*Call graph*: 2 external calls (select, workspace_tx).


##### `ScopedStore.put`  (lines 90–111)

```
async def put(self, key: str, value: JsonValue) -> None
```

**Purpose**: Saves or replaces one JSON value in this extension’s private store. It gives extensions a simple durable memory without exposing the whole database.

**Data flow**: It receives a key and JSON-like value → opens a workspace transaction → tries to update an existing row for this workspace and extension → if none exists, inserts a new row → returns nothing after the database is changed.

**Call relations**: Extension handlers use this through `ExtensionContext.store`. It uses normal SQL update/insert operations inside `workspace_tx`, so it follows the same workspace scoping as the rest of the context.

*Call graph*: 3 external calls (insert, update, workspace_tx).


##### `ScopedStore.delete`  (lines 113–121)

```
async def delete(self, key: str) -> None
```

**Purpose**: Removes one key from this extension’s private store in the current workspace. It is useful when saved extension state is no longer needed.

**Data flow**: It receives a key → opens a workspace transaction → deletes the row matching the current workspace, extension name, and key → returns nothing whether or not a row was present.

**Call relations**: This is part of the storage tool exposed as `ExtensionContext.store`. It stays inside the same scoped database path as `get`, `put`, and `list`.

*Call graph*: 2 external calls (delete, workspace_tx).


##### `ScopedStore.list`  (lines 123–136)

```
async def list(self, prefix: str='') -> tuple[tuple[str, JsonValue], ...]
```

**Purpose**: Lists saved key-value pairs for this extension in the current workspace, optionally only those whose keys start with a prefix. This lets extensions inspect a group of their own stored settings.

**Data flow**: It receives an optional prefix → opens a workspace transaction → finds matching rows for this workspace and extension → sorts them by key → returns a tuple of key/value pairs.

**Call relations**: Extension code reaches it through `ExtensionContext.store`. It uses a scoped database query, so it never lists another extension’s or workspace’s stored values.

*Call graph*: 2 external calls (select, workspace_tx).


##### `CredentialAccess.workspace_id`  (lines 150–151)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID currently bound to the running task. Credential operations use this so secrets always come from the active workspace.

**Data flow**: It reads the current workspace context → extracts the workspace UUID → returns it.

**Call relations**: Credential methods use this value when they seal or resolve credentials. It is part of the safety design: callers do not pass arbitrary workspace IDs into credential access.

*Call graph*: 1 external calls (ws_current).


##### `CredentialAccess.get`  (lines 153–159)

```
async def get(self, slot: str) -> str
```

**Purpose**: Fetches a credential value only if the extension declared that credential slot in its manifest. This stops an extension from asking for secrets it never requested permission to use.

**Data flow**: It receives a slot name → checks whether that slot is in the declared set → if not, raises `UndeclaredCredentialSlot` → otherwise asks the current workspace for the live credential value → returns the secret string.

**Call relations**: Extension handlers call this through `ExtensionContext.credentials`. It delegates the actual secret lookup to the current workspace object, after enforcing this file’s declared-slot gate.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.rotate`  (lines 161–166)

```
async def rotate(self, slot: str, expected: str, plaintext: str) -> bool
```

**Purpose**: Updates an existing declared credential after an outside provider rotates it, using a compare-and-swap check. Compare-and-swap means it changes the secret only if the old expected value still matches, which avoids overwriting a newer update.

**Data flow**: It receives a slot, the expected current value, and the new plaintext value → rejects undeclared slots → asks the current workspace to rotate the credential → returns `True` or `False` depending on whether the rotation succeeded.

**Call relations**: Extension code uses this through `ExtensionContext.credentials` when it already has permission for the slot. The current workspace object performs the real credential update.

*Call graph*: 2 external calls (__init__, ws_current).


##### `CredentialAccess.bind_installation`  (lines 168–181)

```
async def bind_installation(self, slot: str, installation_id: str) -> None
```

**Purpose**: Stores proof that a workspace is connected to an external provider installation, but only for a declared credential slot. Instead of storing a plain installation ID, it stores a sealed value that can only be opened for the right workspace and slot.

**Data flow**: It receives a slot and installation ID → rejects undeclared slots → creates a sealed token using the credential installation key, workspace ID, slot, and installation ID → writes that sealed value into the workspace credential slot → returns nothing.

**Call relations**: This is used when an extension connects a workspace to an external service. It calls the credential sealing helpers, then writes through the current workspace’s credential storage.

*Call graph*: 4 external calls (__init__, installed_credential_requests, seal_installation, ws_current).


##### `TrajectoryCorpus.workspace_id`  (lines 214–215)

```
def workspace_id(self) -> UUID
```

**Purpose**: Returns the workspace ID whose transcripts this corpus may read. This keeps transcript access tied to the active workspace.

**Data flow**: It reads the current workspace context → returns its workspace UUID.

**Call relations**: The `trajectories` method uses this property to choose conversations from the current workspace only.

*Call graph*: 1 external calls (ws_current).


##### `TrajectoryCorpus.trajectories`  (lines 217–268)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Reads a bounded set of recent conversation transcripts for the current workspace and turns them into evaluation examples. It skips missing or corrupt transcripts so one bad record does not ruin the whole corpus.

**Data flow**: It finds recent conversations with turns in the database → for each conversation, reads the transcript blob → decodes it into messages → calculates a digest of the agent prompt → builds `Trajectory` records → returns them as a tuple.

**Call relations**: Extensions can reach this through `ExtensionContext.trajectories` when a blob store has been wired into the context. It uses the database for conversation metadata, blob storage for transcript bodies, and logging when corrupt data is skipped.

*Call graph*: 7 external calls (__init__, select, workspace_tx, prompt_digest, log, decode, transcript_key).


##### `trajectory_workspaces`  (lines 271–293)

```
def trajectory_workspaces() -> WorkspaceCandidates
```

**Purpose**: Builds a workspace candidate selector for jobs that need to read conversation trajectories. In plain terms, it says: run this job only for workspaces that have at least one conversation with at least one turn.

**Data flow**: It defines a database query builder → wraps that builder with `owner_candidates` → returns a `WorkspaceCandidates` object that the scheduler or dispatcher can use.

**Call relations**: Trajectory-reading extensions declare this as their candidate source. The inner query `trajectory_workspaces.with_a_turn` supplies the actual database condition.

*Call graph*: 1 external calls (owner_candidates).


##### `trajectory_workspaces.with_a_turn`  (lines 279–291)

```
def with_a_turn() -> sa.Select[tuple[UUID]]
```

**Purpose**: Creates the database query used to find workspaces that have real conversation history. It looks for workspaces with at least one conversation that has at least one turn.

**Data flow**: It takes no runtime input → builds a SQL query using nested `exists` checks → returns that query to the surrounding `trajectory_workspaces` function.

**Call relations**: This helper is enclosed inside `trajectory_workspaces`. It is not the public seam itself; it supplies the query that `owner_candidates` wraps for the dispatcher.

*Call graph*: 2 external calls (exists, select).


##### `TurnInvoker.invoke`  (lines 300–302)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Describes the method an internal turn invoker must provide. It represents the ability for a background handler to start a new agent turn in a conversation.

**Data flow**: An implementation receives a conversation ID, agent ID, message, and idempotency key → it should admit or reuse the requested turn → it returns the UUID of the turn.

**Call relations**: This is a protocol, meaning a promise about shape rather than an implementation here. `ExtensionContext.invoke` calls whatever concrete invoker the main system wires in.


##### `ModelResolver.auto_model`  (lines 312–312)

```
def auto_model(self) -> str
```

**Purpose**: Describes the property that returns the deployment’s default model name. Background model calls use this instead of letting each extension pick an arbitrary model.

**Data flow**: A concrete resolver provides the model identifier → callers read it as a string.

**Call relations**: This protocol property is used by `ModelAccess.model` and `ModelAccess.turn` when they decide which model to call and bill.


##### `ModelResolver.pricing`  (lines 315–315)

```
def pricing(self) -> Pricing
```

**Purpose**: Describes the property that returns the price table for model usage. The price table is needed to turn token counts into billable usage.

**Data flow**: A concrete resolver provides pricing information → callers read it when recording usage.

**Call relations**: `ModelAccess.turn` uses this after a model stream completes, so the same model call can be billed through the workspace accounting path.


##### `ModelResolver.client_for`  (lines 317–317)

```
async def client_for(self, model: str) -> ModelClient
```

**Purpose**: Describes how to get the actual model client for a named model. A model client is the object that talks to the language-model provider.

**Data flow**: A caller provides a model name → the resolver chooses or creates the right client, using workspace credentials if needed → it returns a `ModelClient`.

**Call relations**: `ModelAccess.turn` calls this before streaming a completion. The concrete implementation lives outside this file to avoid tying extension context code directly to the model registry internals.


##### `ModelResolver.key_slot_for`  (lines 319–319)

```
def key_slot_for(self, model: str) -> str | None
```

**Purpose**: Describes how to find which credential slot, if any, supplies the key for a model. This is used to label usage as using a workspace-provided key or a platform key.

**Data flow**: A caller provides a model name → the resolver returns a credential slot name or `None` → that answer is used for usage export labeling.

**Call relations**: The `context_for` function passes this method into `ExtensionContext`, and `pending_usage_exports` uses it when minting export records.


##### `ModelAccess.model`  (lines 335–337)

```
def model(self) -> str
```

**Purpose**: Returns the one model name this access object will call and bill. It makes the default model visible to extension code without allowing model selection here.

**Data flow**: It reads `auto_model` from the resolver → returns that model name.

**Call relations**: This is a small convenience property over the injected `ModelResolver`. `complete` and `turn` use the same resolver value when they run model requests.


##### `ModelAccess.complete`  (lines 339–346)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: Runs one language-model request and returns only the assistant’s text. It is a simpler wrapper for callers that do not care about tool calls or rich message blocks.

**Data flow**: It receives a `ModelRequest` → calls `ModelAccess.turn` to do the real streamed, metered model call → if the response is plain text, returns it → otherwise joins together the text blocks and returns that string.

**Call relations**: Memory extension code calls this when deriving facts or summaries. It hands the hard work to `ModelAccess.turn`, which performs the model call and billing.

*Call graph*: calls 1 internal fn (turn); called by 2 (_extract, _summarize).


##### `ModelAccess.turn`  (lines 348–396)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one full language-model turn, including streamed text, possible tool calls, and usage billing. It returns an assistant message in the same shape normal conversation turns use.

**Data flow**: It receives a model request → replaces its model with the deployment default → gets the provider client → streams events from the client → collects text chunks, tool-call JSON, and usage counts → records billable usage for the current workspace → returns an assistant `Message` containing text and any tool-use blocks.

**Call relations**: `ModelAccess.complete` calls this for text-only use, and the knowledge graph extension can call it directly when it needs tool-aware output. It relies on `ws_current().billable_event()` so model usage is charged to the same workspace whose credentials were used.

*Call graph*: called by 2 (complete, _tier_b); 7 external calls (__init__, __init__, __init__, __init__, model_copy, loads, ws_current).


##### `ExtensionContext.pending_usage_exports`  (lines 453–475)

```
async def pending_usage_exports(self, floor: datetime, limit: int) -> tuple[UsageExport, ...]
```

**Purpose**: Finds usage records that this extension should export to an outside billing or reporting system. Before reading, it mints any newly eligible export records so repeated reads are stable and deduplicated.

**Data flow**: It receives a time floor and a limit → checks that model key-slot lookup was wired → opens a workspace transaction → creates export intents for settled usage after the floor → reads up to the requested number of pending exports → returns them.

**Call relations**: Exporting extensions call this through their context. It delegates ledger-specific work to the accounting module and uses the extension name so different exporters do not acknowledge each other’s records.

*Call graph*: 3 external calls (mint_usage_exports, read_pending_usage_exports, workspace_tx).


##### `ExtensionContext.ack_usage_exports`  (lines 477–486)

```
async def ack_usage_exports(self, exports: tuple[UsageExport, ...]) -> None
```

**Purpose**: Marks usage exports as delivered after an outside receiver has accepted them. This prevents successfully delivered records from being returned again.

**Data flow**: It receives a tuple of usage export records → if the tuple is empty, does nothing → otherwise opens a workspace transaction → asks the accounting module to acknowledge those exports → returns nothing.

**Call relations**: A usage exporter calls this after delivery succeeds. If delivery fails and this is not called, `pending_usage_exports` can return the same frozen records again for safe retry.

*Call graph*: 2 external calls (ack_usage_exports, workspace_tx).


##### `ExtensionContext.transaction`  (lines 489–501)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Gives an extension a database transaction for its own tables and approved SDK operations. It commits if the block succeeds and rolls back if an error happens.

**Data flow**: A caller enters the async context manager → it opens a workspace transaction and yields the raw database connection → caller runs its SQL → on exit, the surrounding transaction machinery commits or rolls back.

**Call relations**: Extension handlers use this when they need more than the simple key-value store. The docstring warns that the connection is powerful: runtime SQL is not automatically limited to the extension’s schema, so the extension must still scope its own queries correctly.

*Call graph*: 1 external calls (workspace_tx).


##### `ExtensionContext.invoke`  (lines 503–512)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str) -> UUID
```

**Purpose**: Starts an internal agent turn from a background handler. It refuses to silently do nothing if no turn invoker has been wired.

**Data flow**: It receives a conversation ID, agent ID, message, and idempotency key → checks that an invoker exists → passes those values to the invoker → returns the resulting turn UUID.

**Call relations**: This method is the extension-facing wrapper around the `TurnInvoker` protocol. The actual admission and idempotency behavior is implemented by the concrete invoker supplied by the main system.


##### `ExtensionContext.register_source`  (lines 514–576)

```
async def register_source(self, backend: str, config: BaseModel, *, subject: str, owner_member_id: UUID | None) -> UUID
```

**Purpose**: Registers a content-sync source, such as an external account or folder, for the current workspace. It is idempotent: registering the same backend and configuration again returns the same source instead of making duplicates.

**Data flow**: It receives a backend name, typed config object, visibility subject, and optional owner member ID → converts the config to JSON → derives a stable source ID from workspace/backend/config → checks whether that source already exists → returns it if live and subject matches, revives it if removed, or inserts a new row → returns the source UUID.

**Call relations**: Sample and YC extensions call this during setup. Later, the core sync driver can poll the registered source and create pages from it. The method protects existing synced pages by refusing to change a live source’s disclosure through re-registration.

*Call graph*: called by 2 (_setup, setup_sources); 7 external calls (now, model_dump, insert, select, update, workspace_tx, source_row_id).


##### `ExtensionContext.sources`  (lines 578–616)

```
async def sources(self, backend: str | None=None) -> tuple[SourceRecord, ...]
```

**Purpose**: Lists live registered content sources in the current workspace, optionally filtered to one backend. Removed sources are intentionally hidden.

**Data flow**: It receives an optional backend name → builds a workspace-scoped database query for non-removed sources → reads matching rows → converts each row into a `SourceRecord` → returns the records as a tuple.

**Call relations**: The sources extension uses this to build bindings from existing source rows. It is the read-side companion to `register_source` and `remove_source`.

*Call graph*: called by 1 (_bindings_from_ext); 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.source_pages`  (lines 618–663)

```
async def source_pages(self, subjects: frozenset[str] | None=None) -> tuple[PageRecord, ...]
```

**Purpose**: Lists live synced pages in the current workspace, optionally limited to specific visibility subjects. It returns page metadata and a body reference, not the full page body.

**Data flow**: It receives an optional set of subjects → builds a workspace-scoped query for non-tombstoned pages → adds a subject filter if provided → reads rows → converts them into `PageRecord` objects → returns a tuple.

**Call relations**: Extensions use this sanctioned read path instead of querying the core page table directly. Subject filtering lets member-facing code avoid seeing another member’s private synced pages.

*Call graph*: 3 external calls (__init__, select, workspace_tx).


##### `ExtensionContext.forget_page`  (lines 665–681)

```
async def forget_page(self, page_id: UUID) -> None
```

**Purpose**: Marks one live page as forgotten by tombstoning it. A tombstone is a database marker saying the page should be treated as removed while preserving history needed by cleanup pipelines.

**Data flow**: It receives a page ID → records the current time → updates the matching live page in this workspace to `tombstone=True` → if no row was changed, raises an error → returns nothing on success.

**Call relations**: This is the write-side counterpart to `source_pages`. Downstream page-change processing can notice the tombstone and remove derived index data.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.remove_source`  (lines 683–714)

```
async def remove_source(self, source_id: UUID) -> None
```

**Purpose**: Removes a registered source and tombstones all of its live pages in the same transaction. This stops future syncing and triggers cleanup of derived page data.

**Data flow**: It receives a source ID → records the current time → marks the live source in this workspace as removed and clears any sync claim → if no source matched, raises an error → tombstones all live pages belonging to that source → returns nothing.

**Call relations**: This completes the lifecycle started by `register_source`. Re-registering the same configuration later can revive the source row, while page cleanup follows the normal page-change path.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.set_source_subject`  (lines 716–742)

```
async def set_source_subject(self, source_ids: tuple[UUID, ...], subject: str) -> None
```

**Purpose**: Changes the visibility subject for one or more live sources and restamps their live pages with the same subject. This is how already-synced content is reclassified safely and atomically.

**Data flow**: It receives source IDs and a new subject → records the current time → updates matching live source rows in this workspace → if none matched, raises an error → updates all live pages for those sources with the new subject and timestamp → returns nothing.

**Call relations**: This method is used when a source binding’s audience changes. By changing sources and pages in one transaction, it avoids a half-updated state where some streams have the old disclosure and others have the new one.

*Call graph*: 3 external calls (now, update, workspace_tx).


##### `ExtensionContext.propose_change`  (lines 744–750)

```
async def propose_change(self, change: AgentChange) -> ProposalRef
```

**Purpose**: Creates a governed proposal to change an agent prompt instead of changing it directly. Governance means the change must pass through the project’s approval and safety process.

**Data flow**: It receives an `AgentChange` → creates a `Governance` object stamped with this workspace and extension → submits the proposal → returns a `ProposalRef` that identifies it.

**Call relations**: The sample extension calls this during its tick flow. The actual proposal storage and later approval behavior live in the governance module.

*Call graph*: called by 1 (_tick); 1 external calls (__init__).


##### `ExtensionContext.trajectories`  (lines 752–757)

```
async def trajectories(self) -> tuple[Trajectory, ...]
```

**Purpose**: Returns this workspace’s recent conversation transcripts as a read-only evaluation corpus. It fails clearly if transcript access was not wired into this context.

**Data flow**: It checks whether a `TrajectoryCorpus` exists → if not, raises a runtime error → otherwise asks the corpus to load trajectories → returns the resulting tuple.

**Call relations**: The sample extension calls this when it wants conversation history. This method is a small guard and forwarding layer over `TrajectoryCorpus.trajectories`.

*Call graph*: called by 1 (_tick).


##### `context_for`  (lines 760–789)

```
def context_for(extension: str, declared: frozenset[str], index: IndexBackend | None=None, embed: EmbedClient | None=None, pages: PageFeed | None=None, blob: BlobStore | None=None, invoker: TurnInvoke
```

**Purpose**: Builds the `ExtensionContext` object that a handler receives. It assembles only the capabilities the runtime has chosen to wire in for that extension or core job.

**Data flow**: It receives the extension name, declared credential slots, and optional services such as indexing, embedding, pages, blob storage, model resolver, invoker, scheduler, and surfaces → creates scoped access wrappers for each available capability → returns a ready-to-use `ExtensionContext`.

**Call relations**: This is the factory that makes core jobs and extensions travel through the same access path. It constructs `ScopedStore`, `CredentialAccess`, `SurfaceInstallationAccess`, optional `TrajectoryCorpus`, optional `ModelAccess`, and schedule storage so handlers receive one consistent context object.

*Call graph*: 7 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__).


### SDK Context Entry Points
Establishes the SDK package and exposes approved context names through a stable public import path.

### `core/src/ufo/sdk/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can act like an importable package when it contains an `__init__.py` file. That means other parts of the project can write imports that start with `ufo.sdk`, and Python knows to treat this directory as part of the project’s module tree.

Because the file has no code inside it, it does not define functions, classes, settings, or startup behavior. Its value is structural rather than active. Think of it like a label on a drawer: the label does not contain the tools, but it tells the rest of the system where that drawer begins and lets people refer to what is inside it by name.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.sdk` might fail or behave differently. Keeping it here makes the package boundary explicit and helps the project organize SDK-related code under a clear namespace.


### `core/src/ufo/sdk/context.py`

`data_model` · `cross-cutting; used when extensions import SDK context types`

When an extension runs, it needs a safe way to see only the information and tools it is allowed to use: credentials, model access, saved records, page data, and similar context. This file does not create those objects itself. Instead, it re-exports them from their real homes under a cleaner public path, `ufo.sdk.context`.

Think of it like a reception desk in a building. The people and services are elsewhere, but visitors only need to know one desk to ask for them. That matters because extension code can depend on this stable SDK surface, while the internal layout of the project can change later without breaking those extensions.

The imported names include the main `ExtensionContext`, access helpers such as `CredentialAccess`, `ModelAccess`, and `ScopedStore`, record-shaped values like `PageRecord`, `SourceRecord`, `Trajectory`, `AgentChange`, and `ProposalRef`, plus error-like types for credential problems. It also exposes `trajectory_workspaces`, a helper from the context layer. The important behavior is the boundary it creates: extension authors are guided to use this public API instead of internal modules such as `ufo.ext.context` directly.


### SDK HTTP and Observability
Provides public helpers for extension HTTP handling and structured logging without exposing internal framework details.

### `core/src/ufo/sdk/http.py`

`io_transport` · `request handling`

This file is a small public doorway for HTTP route code. A route is a function that receives a Request, meaning the incoming web request, and returns a Response, meaning what the server sends back. Instead of making extensions import these pieces directly from Starlette, the web toolkit underneath, this file re-exports the approved response classes through `ufo.sdk`. That gives the project one stable place to say, “these are the HTTP building blocks extension authors may use.”

Its most important behavior is around session cookies. A session cookie is a small browser-stored value used to remember who a visitor is. If cookies are set carelessly, they can leak across subdomains or be easier to steal. The `set_session_cookie` helper is the approved way to create one. It deliberately does not allow a `domain` option, so the browser keeps the cookie tied to the exact host that set it. Think of it like giving a key that only fits one building, not every building on the street. It also always marks the cookie as `HttpOnly`, so browser scripts cannot read it, and `Secure`, so it is only sent over HTTPS. The only choice callers get is the `SameSite` setting, which controls when the browser sends the cookie during cross-site navigation.

#### Function details

##### `set_session_cookie`  (lines 17–25)

```
def set_session_cookie(response: Response, name: str, token: str, *, samesite: Literal['lax', 'strict', 'none']) -> None
```

**Purpose**: Sets a session cookie on an outgoing HTTP response using the project’s required safety rules. Code uses this instead of calling the lower-level cookie setter directly, so every session cookie is host-only, secure, and hidden from browser scripts.

**Data flow**: It receives an outgoing response, a cookie name, a token value, and a `SameSite` choice. It passes those values to the response’s cookie-setting method while forcing `HttpOnly` and `Secure` to true and never providing a domain. The response is changed in place so that, when sent to the browser, it includes the session cookie.

**Call relations**: When route or session code needs to attach a login/session token to a response, it calls this helper. The helper then hands the actual low-level work to Starlette’s `Response.set_cookie`, but only after locking in the project’s safety choices.

*Call graph*: 1 external calls (set_cookie).


### `core/src/ufo/sdk/o11y.py`

`io_transport` · `cross-cutting`

This file is intentionally tiny, but it matters because it defines part of the public SDK, meaning the stable interface that outside extension code is expected to use. Structured logging means recording events in a consistent shape so they are easier to search, filter, and understand later. Instead of asking extension authors to import logging helpers from the internal `ufo.o11y` module directly, this file re-exports the two approved helpers: `log` and `warn`.

Think of it like a front desk in a building. The real offices are elsewhere, but visitors are told to come through the front desk because that entrance can stay familiar even if the rooms behind it move around. If the internal observability code changes location or shape later, this SDK file can preserve the public import path for extensions.

Without this file, extensions would either have to depend on internal project paths, which makes them fragile, or they would lack a clear official way to send structured log messages and warnings.
