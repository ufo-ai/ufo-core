# Provider, data, model, and security SDK façades  `stage-17.3`

This stage is shared support for people adding integrations to the system. It is not the main work loop itself. Instead, it provides public “front doors” to internal features, so extension authors can import stable SDK names without depending on private file locations that may change.

The security-facing doors are authproxy, bearer, credentials, and grants. They expose approved pieces for authentication backends, bearer-token checking, credential objects, and grant or connection audit helpers. Connectors and sources cover outside systems: connectors gathers connector and OAuth tools, while sources gathers source-sync interfaces, REST connector helpers, pagination, and related errors. The lower-level sources connector file defines the common contract every external data connector must follow, including how to move through partitioned data such as many repositories or channels.

The data and model doors are index, memory, search, and models. They expose types for indexing, embeddings, memory search, search interfaces, and model clients. Operator adds public helpers for web login and sessions. Together these files act like a well-labeled tool cabinet for extension builders.

## Files in this stage

### Security and connection façades
Stable SDK import points for authentication backends, bearer verification, connector OAuth hooks, credentials, and grant auditing.

### `core/src/ufo/sdk/authproxy.py`

`other` · `extension integration and credential resolution setup`

This file is a doorway, not a workshop. It does not create new behavior itself. Instead, it re-exports a few important names from deeper inside the project so outside extensions can use them safely.

The problem it solves is boundary control. Feed-sync sources may need credentials, such as tokens or account secrets, before they can talk to outside providers. An extension can provide an auth proxy: a piece of code that knows how to turn a source’s account reference into the real `Credential` used by a connector. This file gathers the public pieces for that job: `AuthProxy`, `Credential`, `DIRECT_ACCOUNT`, and `AuthProxySpec`.

The long module comment explains the expected flow. An extension declares an `AuthProxySpec` in its manifest, gives the backend a name, and implements `AuthProxy`. If there is only one backend, the system can use it automatically. If there are several, configuration chooses one. Sources marked with `DIRECT_ACCOUNT` use the selected backend directly; sources connected through a broker get their credential from that broker instead.

An everyday analogy: this file is like a front desk sign that says, “Auth proxy extension authors, use these forms here.” The actual machinery lives elsewhere, but this keeps the public entrance simple and stable.


### `core/src/ufo/sdk/bearer.py`

`io_transport` · `request handling`

A bearer token is like a stamped wristband: whoever presents it can prove they were admitted by the gateway. This file is a small public doorway for surface extensions that need to check such a token. It does not implement the checking itself. Instead, it re-exports three verification helpers from the internal `ufo.bearer` module: one to verify a token, one to read verified claims, and one to extract the workspace claim.

The important design choice is separation of power. The control plane is responsible for minting, or creating, tokens. Extensions are only allowed to verify tokens they receive. The file’s docstring also points out that the signing secret stays inside core code: callers pass in a token, and the verification functions resolve `UFO_TOKEN_SECRET` themselves. That means an extension can check whether a token is valid without ever being handed the key that could create new trusted tokens.

Without this file, extensions would either need to import from a less stable internal location or risk being given too much authority. This file keeps the public API narrow and intentional.


### `core/src/ufo/sdk/connectors.py`

`other` · `cross-cutting import/API boundary`

This file does not define new behavior itself. Instead, it gathers and re-exports the important connector types from two internal areas: `ufo.connectors` and `ufo.grants`. A connector is how an outside service, such as a brokered data provider, plugs into the system. OAuth is the common web sign-in flow where a user grants access without sharing their password.

The main problem this file solves is stability. Extension authors need a clear, supported place to import things like `ConnectorBroker`, `ConnectorRegistry`, `OAuthProvider`, and related data shapes. If they imported directly from deeper internal modules, future reorganizing could break their extensions. This file acts like a front desk: outsiders ask here for the public connector tools, while the project can keep its back rooms arranged however it wants.

The docstring explains the larger flow. An extension supplies an OAuth provider for sign-in and a connector broker for catalog lookup, server-side tool execution, and feed-sync credentials. Core code later combines registered connectors into a `ConnectorRegistry`, attaches that registry to a tool context during a turn, and routes connector requests through it. This file is the named seam that makes those pieces visible to extension code while hiding the broker-specific details.


### `core/src/ufo/sdk/credentials.py`

`io_transport` · `cross-cutting`

This file does not create new credential behavior itself. Instead, it chooses which credential features are part of the public extension interface. In a project like this, extensions should not reach into every internal module directly, because internal code may change or include things extensions should not depend on. This file gives them a stable, intentional place to import from.

The items it re-exports are about creating, storing, and opening credentials in controlled ways. A credential is a piece of proof or access information, like a keycard. The file also exposes error types for two important failure cases: when a credential cannot be minted, meaning created, and when a credential request is not valid.

The important design idea is separation. The deeper `ufo.credentials` module can contain the real implementation, while `ufo.sdk.credentials` defines the public promise: these are the credential value objects and sealed operations an extension may touch. Without this file, extension authors would either have no clear supported import path, or they might rely on private internals that are easier to misuse and harder for the project to change safely.


### `core/src/ufo/sdk/grants.py`

`other` · `import time and extension use`

This file is like a clearly labeled front desk for a few grant-related tools. The real definitions live elsewhere, in `ufo.grants`, but outside code should not need to know that internal layout. Instead, extensions can import from `ufo.sdk.grants`, which is part of the public software development kit, or SDK, meaning the set of tools meant for other developers to use.

The file exposes four names: two summary types, `ConnectionSummary` and `GrantSummary`, and two functions, `connection_summaries` and `grant_summaries`. These are used to inspect connection and connector-grant information in an audit-friendly way. In plain terms, they help answer questions like “what connections exist?” and “what permissions or grants have been given?”

The comment at the top explains why this module exists as a separate file: the project does not allow executable code inside `__init__.py` files, so public SDK names are gathered in small named modules like this one. Without this file, users would either need to import from deeper internal modules, which makes their code more fragile, or the SDK would have no clean public doorway for these grant audit views.


### Provider data façades
Public SDK surfaces for indexing, memory search, and model-facing provider types.

### `core/src/ufo/sdk/index.py`

`data_model` · `startup and extension integration`

This file exists to give outside extensions a stable, simple place to import the pieces needed for indexing. Indexing here means storing chunks of text, turning them into vector embeddings, and later searching them by words or meaning. Without this file, extension authors would need to reach directly into `ufo.indexing`, which is more like internal machinery and could change more easily.

The file does not define new behavior. Instead, it acts like a labeled service counter at the front of a building: the actual tools live elsewhere, but this counter tells outsiders, “Use these names from here.” It exposes the main contract for an `IndexBackend`, which is the part an extension implements to provide lexical search and vector search over text chunks. It also exposes `EmbedClient`, the contract for a service that turns batches of text into embeddings, meaning number-based representations that search systems can compare.

It also re-exports supporting shapes such as `Chunk`, `Hit`, and `IndexScope`, plus owner-kind constants that say what kind of thing a chunk belongs to. The important idea is separation: core code can keep its internal indexing module, while extension code gets a clean SDK surface that says, “These are the supported parts you may rely on.”


### `core/src/ufo/sdk/memory.py`

`data_model` · `cross-cutting`

This file is a small public doorway into the project’s memory-search feature. Instead of asking outside extension code to import directly from the internal `ufo.memory` module, it re-exports the key names through `ufo.sdk.memory`. That matters because an SDK, or software development kit, is meant to be the safe and documented surface that other people build against. If the internal layout changes later, the project can try to keep this SDK path steady.

The three exported names are the default memory search provider, the shape of a memory search result, and the provider interface itself. In plain terms, they let an extension ask, “What remembered information matches this query?” and understand the answer. This file does not perform any searching by itself. It is more like a labeled shelf in a library: the books live elsewhere, but this shelf tells outsiders where they are allowed to pick them up.

Without this file, provider extensions might depend on deeper internal modules, making them more fragile and harder to support over time.


### `core/src/ufo/sdk/models.py`

`data_model` · `cross-cutting, when SDK users import model-related APIs`

This file does not create new behavior. Its job is to gather the model-facing pieces of the system into one safe, public place. Think of it like a front desk: instead of asking visitors to wander through many back offices, it points them to the approved objects they can use.

The exported names cover several things an extension may need when talking to an AI model: message and content block shapes, model request and response event types, tool-use records, image and text pieces, model client interfaces, concrete OpenAI and Anthropic clients, pricing and model specification types, and usage records. It also exposes helper functions such as `trim_images`, plus OpenAI-specific helpers for building messages or clients.

The important reason this file exists is stability. Internal modules can move around or change, but code written against the SDK should not have to chase those changes. By importing from `ufo.sdk.models`, extensions depend on a clear contract: these are the model-related building blocks the project intends to support publicly. Without this file, extension authors would likely import directly from internal modules, making their code more fragile and more tightly tied to implementation details.


### Operator session façade
SDK access to operator web login and session helpers.

### `core/src/ufo/sdk/operator.py`

`orchestration` · `request handling`

This file is a small public doorway. The actual operator-session code lives elsewhere, in `ufo.ext.operator`, but SDK users should not need to know that internal location. Instead, they can import from this file.

The two re-exported helpers support an operator-only web surface, such as debugging or administration tools. One helper, `resolve_operator_workspace`, figures out which workspace an operator is trying to use, including reading a `?ws=` value from the web request, and checks that the request belongs to the operator domain. The other helper, `bind_operator_session`, connects a successful operator action to the shared session cookie, so later requests can recognize the same operator session.

In everyday terms, this file is like a labeled front desk for a restricted office area. The front desk does not make the rules itself, but it points everyone to the right gatekeeper and badge-stamping process. Without this file, callers would either need to import from a more internal module or duplicate knowledge about where these authentication helpers live, making future refactors harder and less safe.


### Search façade
Stable public imports for search-related interfaces and types.

### `core/src/ufo/sdk/search.py`

`data_model` · `cross-cutting`

This file is like a clearly marked service counter for search features. The real search definitions live elsewhere, in `ufo.search`, but outsiders should not have to know that internal location or depend on it directly. Instead, they import from this SDK file.

The search system has two sides. One side is an extension that provides search capability, called a search provider. The other side is tool code that asks for searches or page fetches. This file exposes the shared vocabulary both sides need: a `SearchQuery` is the question being asked, `SearchResults` is the answer, `SearchHit` is one result item, `FetchRequest` asks to retrieve a page, `FetchedPage` is the retrieved page, `SearchProvider` is the interface a backend must implement, and `SearchUnsupported` is the error used when a provider cannot do a requested fetch.

Without this file, extension authors would need to import from the internal `ufo.search` module directly. That would make the internal layout part of the public contract. By re-exporting these names here, the project keeps a cleaner boundary: the SDK path stays stable even if the internal implementation is reorganized later.


### Source connector APIs
Public source-building imports followed by the shared connector contract and partition-walking helper logic they expose.

### `core/src/ufo/sdk/sources.py`

`other` · `cross-cutting; used when extensions import SDK source-sync tools`

This file does not implement syncing itself. Instead, it acts like a clearly labeled toolbox at the edge of the project. An extension that brings in content from an outside service needs to describe how to fetch records, turn them into pages, remember where it left off, and report special cases like “the saved cursor no longer works” or “this account is not allowed to read this stream.” The real classes and helpers live deeper in the `ufo.sources` package, but this file re-exports them through the SDK so extension code does not need to know the internal layout of the core codebase.

The main idea is a “source backend”: extension code provides a backend that can fetch pages of content using a typed configuration and workspace authentication. For REST-style services, the file exposes reusable connector pieces such as `RestConnector`, stream descriptions, pagination strategies, and helpers for pulling records out of JSON responses. It also exposes support for full snapshot syncs, incremental syncs, deletion reporting, skipped streams, expired cursors, and partitioned streams such as “one cursor per repository or channel.”

Without this file, extension authors would have to import many internal modules directly. That would make extensions more fragile, because a package reorganization inside core could break them. This file gives them one intended, stable import surface.


### `core/src/ufo/sources/connector.py`

`domain_logic` · `during source sync runs`

A connector is the project’s adapter for an outside service, such as a document app, email provider, or code host. This file sets the common vocabulary: a connector exposes one or more streams, where each stream is a collection of records to sync, such as issues, messages, or documents. Each stream describes how records are identified, whether they can be synced incrementally, and whether missing records should be treated as deleted.

The file also defines page-shaped objects. A page is a batch of records from the provider. Some pages can also carry deleted record IDs and a cursor, which is a saved bookmark used to resume later.

The most involved part is PartitionWalk. Some sources are naturally split into partitions, like “one Slack channel at a time” or “one GitHub repository at a time.” PartitionWalk is like a careful librarian moving shelf by shelf with a bookmark for each shelf. It remembers which partitions are done, which are mid-way through, and what time or ID boundary should be used next. This matters because sync jobs may stop early, APIs may return newest items first, and records can arrive while the sync is running. Without this logic, the system could skip records, repeat too much work, or forget deletions.

Finally, the Connector base class says what every connector must provide: which streams exist, how to fetch pages, and how to turn one raw record into readable text for recall.

#### Function details

##### `PartitionWalk.stream`  (lines 207–299)

```
async def stream(self, cursor: str | None) -> AsyncIterator[StreamPage]
```

**Purpose**: This function walks through all partitions of a stream and yields sync pages with updated progress bookmarks. It is used when one logical stream is split across many smaller areas, such as channels, repositories, or folders.

**Data flow**: It starts with an incoming cursor string, which is the saved progress from a previous run. It decodes that into a per-partition map, asks for the list of partitions, then asks the connector for pages from each partition using the right boundary: after a known watermark, before a saved backfill point, or from the beginning. As it receives pages, it updates the checkpoint map and yields StreamPage objects containing records, deletes, and the next cursor to save. When the walk finishes, it cleans up stale partition entries so progress for removed partitions does not live forever.

**Call relations**: The sync machinery calls this when a connector needs partition-aware progress tracking. Inside the walk, it relies on _decode to understand the saved cursor and _encode to publish updated cursor strings after each useful checkpoint. It calls the connector-supplied partition iterator and page factory, then hands StreamPage results back to the caller so the rest of the sync can store records and save progress.

*Call graph*: calls 2 internal fn (_decode, _encode); 3 external calls (__init__, __init__, __init__).


##### `PartitionWalk._decode`  (lines 302–331)

```
def _decode(cursor: str | None) -> dict[str, str | _Window]
```

**Purpose**: This helper turns a saved cursor string back into the partition progress map used by PartitionWalk. It protects the walk from confusing unrelated cursor formats with its own saved state.

**Data flow**: It receives a cursor string or nothing. If there is no cursor, invalid JSON, or JSON that is not an object, it returns an empty map, meaning the walk should start fresh. If it finds a JSON object, each value must be either a simple watermark string or a saved backfill window with high and until bounds. Valid entries become Python objects; malformed entries raise an error instead of silently dropping progress.

**Call relations**: PartitionWalk.stream calls this at the beginning of a walk. Its output decides where each partition resumes: already finished, part-way through a newest-first backfill, or not seen before.

*Call graph*: called by 1 (stream); 1 external calls (loads).


##### `PartitionWalk._encode`  (lines 334–339)

```
def _encode(partition_map: Mapping[str, 'str | _Window']) -> str
```

**Purpose**: This helper turns the current per-partition progress map into a stable JSON cursor string that can be stored and used later. It is the counterpart to _decode.

**Data flow**: It receives a map from partition name to either a watermark string or a backfill window object. It converts window objects into plain dictionaries, then serializes the whole map as JSON with sorted keys. The result is a string that can be placed on a StreamPage as the next resume cursor.

**Call relations**: PartitionWalk.stream calls this whenever it needs to emit an updated checkpoint. The encoded string travels out with each StreamPage so the broader sync system can save progress safely between batches.

*Call graph*: called by 1 (stream); 1 external calls (dumps).


##### `Connector.streams`  (lines 353–354)

```
def streams(self) -> list[StreamSpec]
```

**Purpose**: This abstract method tells the system which streams a connector can sync. Each concrete connector must implement it so the runner knows what source-side collections are available.

**Data flow**: It takes the connector instance as input and returns a list of StreamSpec objects. Those specs describe stream names, record IDs, cursor fields, deletion behavior, and related sync rules. Because this base method is abstract, the actual list comes from each provider-specific connector.

**Call relations**: The sync runner calls this before fetching data so it can choose and configure the stream to run. Provider connectors supply the concrete implementation, while the rest of the system consumes the returned StreamSpec descriptions.


##### `Connector.fetch_page`  (lines 357–366)

```
def fetch_page(self, stream: StreamSpec, *, cursor: str | None, credential: Credential, base_url: str, self_user_id: str | None) -> AsyncIterator[list[dict[str, Any]] | StreamPage]
```

**Purpose**: This abstract method is the connector’s main data-fetching hook. A concrete connector implements it to call the outside service and yield batches of records for one stream.

**Data flow**: It receives a stream description, an optional cursor to resume from, a resolved credential, a base URL, and optionally the current user’s ID for filtering self-authored records where needed. It then asynchronously yields either plain lists of record dictionaries or richer StreamPage objects that can include deletes and a next cursor. The base class only defines the shape; each provider decides how to talk to its API.

**Call relations**: The connector backend or sync runner calls this during a sync run after selecting a stream. Concrete connectors implement the provider-specific work, and the yielded pages are handed onward to be rendered, stored, deduplicated, and checkpointed.


##### `Connector.render`  (lines 368–387)

```
def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]
```

**Purpose**: This method turns one raw provider record into a readable title and body for recall. It gives every connector a safe default, while content-heavy connectors can override it to produce nicer prose.

**Data flow**: It receives a record dictionary and the stream it belongs to. It looks for a human-friendly title in common fields such as title, name, login, or subject. If none exists, it falls back to the record’s primary key and builds a title like stream/id; if that identity is missing too, it raises an error because the record cannot be named. It returns a pair: the title, and a body containing a heading plus the record serialized as sorted JSON.

**Call relations**: After records are fetched, the sync flow uses render to create the text that will be stored or indexed for later recall. Provider-specific connectors such as document or email sources may override this method when raw JSON would be less useful than the actual document text or message body.

*Call graph*: 1 external calls (dumps).
